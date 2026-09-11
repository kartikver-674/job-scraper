"""A model service Sweep can talk to instead of a model on its own machine.

    Sweep  ->  this  ->  Ollama  ->  qwen3:8b

The whole reason it exists: an 8B model in the user's own process is a
5GB resident that competes with the sweep it is supposed to be serving,
and it cannot move anywhere else while the pipeline calls Ollama's API
directly. One HTTP hop makes the runtime somebody else's problem.

WHAT IT IS NOT
--------------
Not a résumé parser. It is handed a prompt and a JSON Schema and returns
whatever the model says, parsed. The prompts, the schema, the grounding
checks and the arithmetic all stay in local_extract.py, which is the file
the benchmark scores. Copying them here would give them two homes and one
would drift; see inference.py for the longer version of that argument.

Which also means Ollama is an implementation detail of THIS FILE and
nowhere else. `RUNTIME` below is the only line that knows what a runtime
is; pointing it at vLLM, llama.cpp or a hosted endpoint changes nothing
on Sweep's side.

STATELESS, AND DELIBERATELY FORGETFUL
-------------------------------------
Nothing is written down. No résumé text, no prompt, no model output, no
database — it holds a request only for as long as it takes to answer it.
The access log carries request id, model, duration, outcome and an error
category, and the PROMPT SIZE IN BYTES rather than the prompt: enough to
debug a slow call, not enough to reconstruct anyone's employment history.

ONE MODEL, ONE AT A TIME
------------------------
The service accepts requests concurrently and runs generations ONE AT A
TIME, behind a semaphore sized by SWEEP_INFERENCE_WORKERS (default 1).

That is not timidity, it is the shape of the resource. A second
concurrent generation against one Ollama does not halve the latency; it
makes the runtime load a second copy of a 5GB model, or evict the first
and reload it, and both requests then run slower than either would have
alone. Threads that arrive while the slot is taken WAIT, for at most
SWEEP_INFERENCE_QUEUE_WAIT seconds, and are then refused with a
`model_busy` category and a Retry-After rather than piling up invisibly.

Accepting concurrently but serialising the model is what keeps /healthz
answering in milliseconds while a 90-second generation is in flight.

WHAT IS NOT HERE YET, ON PURPOSE
--------------------------------
No per-user accounts, no rate limiting, no registration. The shape that
those will need is here — a bearer token resolved to a principal before
any work happens, one endpoint, one request id — so a limiter keyed on
principal() drops in front of generate() without the contract moving. See
principal().

    SWEEP_INFERENCE_TOKEN=dev-token python -m inference_service
    SWEEP_INFERENCE_TOKEN=dev-token python -m inference_service --demo

`python -m inference_service` IS the production command: it validates the
configuration and then execs gunicorn with gunicorn.conf.py. There is no
second, different way to start this for real — the development server is
gone, because the one that gets tested should be the one that ships.
"""

import json
import logging
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

import inference

# The one line that knows what a runtime is. Everything else in this file
# is transport, validation and logging.
RUNTIME = inference.LocalOllama

# Longest a caller may ask us to wait. A client asking for an hour is a
# client that can hold a worker for an hour, and the extraction calls this
# serves measure under 30s on the benchmark.
MAX_TIMEOUT = 300
DEFAULT_TIMEOUT = 120

# Body limit, enforced by Werkzeug before a byte reaches a view. The prompt
# limit is the real one; the slack covers the model name and the schema.
MAX_BODY_BYTES = inference.MAX_PROMPT_BYTES + 64 * 1024

# Deployment settings. Four, and no more than four: everything about WHICH
# model and WHERE the runtime is already comes from OLLAMA_HOST /
# OLLAMA_MODEL / SWEEP_MODEL_KEEP_ALIVE, which this does not duplicate.
HOST_BIND_ENV = "SWEEP_INFERENCE_HOST"
PORT_ENV = "SWEEP_INFERENCE_PORT"
WORKERS_ENV = "SWEEP_INFERENCE_WORKERS"
QUEUE_WAIT_ENV = "SWEEP_INFERENCE_QUEUE_WAIT"

# Loopback by default. Binding 0.0.0.0 is a decision with consequences and
# has to be made out loud, in the environment, by someone who has read the
# deployment notes.
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8811

# One model, one generation at a time. See the docstring.
DEFAULT_WORKERS = 1

# How long a request waits for the model slot before being refused. Long
# enough that the second of Sweep's two calls queues rather than fails,
# short enough that a caller learns the service is saturated before its
# own deadline runs out.
DEFAULT_QUEUE_WAIT = 30

# Where gunicorn's settings live. Read by main() and by the deployment
# notes; a single file so the timeouts cannot drift from MAX_TIMEOUT.
GUNICORN_CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "gunicorn.conf.py")

# Exactly these keys, and no others. A caller sending something we do not
# understand has a different idea of the contract than we do, and silently
# ignoring the extra key is how the two ideas stay different.
REQUEST_KEYS = {"model", "prompt", "schema", "timeout"}
REQUIRED_KEYS = {"prompt", "schema"}

log = logging.getLogger("inference")


def adopt_gunicorn_logging():
    """Send this module's log where gunicorn's own log goes.

    Without this the access line — request id, model, duration, outcome —
    is SILENTLY DROPPED in production. gunicorn configures its own
    handlers and leaves everybody else's logger bare, so "inference"
    propagates to a root with no handler and falls back to lastResort,
    which is WARNING-only. The service would look healthy and log
    nothing, which is the failure mode you discover during an incident.
    """
    parent = logging.getLogger("gunicorn.error")
    if parent.handlers:
        log.handlers = parent.handlers
        log.setLevel(parent.level)
    return bool(parent.handlers)


class Refused(Exception):
    """A request we will not run, with the category the client gets back."""

    def __init__(self, status, category, message):
        self.status, self.category, self.message = status, category, message
        super().__init__(message)


class Misconfigured(RuntimeError):
    """The service cannot start as configured. Checked before it binds."""


def _positive(name, default, cast=int):
    """One positive number from the environment, or a startup failure.

    A typo'd SWEEP_INFERENCE_WORKERS must not silently become the default:
    somebody set it because they meant something by it.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise Misconfigured(f"{name}={raw!r} is not a number") from None
    if value <= 0:
        raise Misconfigured(f"{name}={raw!r} must be positive")
    return value


def bind():
    """(host, port) to listen on."""
    return (os.environ.get(HOST_BIND_ENV) or "").strip() or DEFAULT_BIND, \
        _positive(PORT_ENV, DEFAULT_PORT)


def workers():
    """How many generations may run at once. One, unless told otherwise."""
    return _positive(WORKERS_ENV, DEFAULT_WORKERS)


def queue_wait():
    """How long a request waits for the model slot before a 503."""
    return _positive(QUEUE_WAIT_ENV, DEFAULT_QUEUE_WAIT, float)


def tokens():
    """The bearer tokens this service accepts, from SWEEP_INFERENCE_TOKEN.

    Comma-separated so the value can be rotated without a restart window.
    Empty means the service will not start — an unauthenticated model
    endpoint on a laptop is still an unauthenticated model endpoint, and
    "it's only localhost" stops being true the first time it is not.
    """
    raw = os.environ.get(inference.TOKEN_ENV) or ""
    return {t.strip() for t in raw.split(",") if t.strip()}


def require_tokens():
    """The configured tokens, or a startup failure naming what is missing.

    Called before the socket is bound, by both start paths. An open model
    endpoint is not a dev convenience, it is a dev incident, and the
    moment this is hosted anywhere it stops being localhost.
    """
    configured = tokens()
    if not configured:
        raise Misconfigured(
            f"set {inference.TOKEN_ENV} to a shared secret before starting "
            f"— an open model endpoint is not a dev convenience, it is a "
            f"dev incident")
    return configured


def principal(header, accepted):
    """Who is calling, or None.

    Returns the token itself as the identity. That is the right shape for
    a POC with one caller and the right shape for what comes next: when
    tokens become per-user, this returns a user id and a rate limiter
    keyed on the return value needs no other change. Nothing downstream
    of here reads the Authorization header.
    """
    header = (header or "").strip()
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    return token if token and token in accepted else None


def validate(body):
    """(model, prompt, schema, timeout) or Refused.


    Strict: exact keys, exact types, bounded sizes. This is a trust
    boundary — a prompt is a string we are about to spend GPU seconds on
    and a schema is something a runtime will compile.
    """
    if not isinstance(body, dict):
        raise Refused(400, "bad_request", "the body must be a JSON object")
    unknown = set(body) - REQUEST_KEYS
    if unknown:
        raise Refused(400, "bad_request",
                      f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = REQUIRED_KEYS - set(body)
    if missing:
        raise Refused(400, "bad_request",
                      f"missing field(s): {', '.join(sorted(missing))}")

    prompt = body["prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise Refused(400, "bad_request", "prompt must be a non-empty string")
    size = len(prompt.encode())
    if size > inference.MAX_PROMPT_BYTES:
        raise Refused(413, "payload_too_large",
                      f"prompt is {size} bytes, over the "
                      f"{inference.MAX_PROMPT_BYTES}-byte limit")

    schema = body["schema"]
    if not isinstance(schema, dict) or not schema:
        raise Refused(400, "bad_request",
                      "schema must be a non-empty JSON Schema object")

    model = body.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise Refused(400, "bad_request",
                      "model must be a non-empty string when given")

    timeout = body.get("timeout", DEFAULT_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise Refused(400, "bad_request", "timeout must be a number")
    if timeout <= 0:
        raise Refused(400, "bad_request", "timeout must be positive")
    # Clamped rather than refused: a client asking for longer than we will
    # give is not an error, it just does not get it.
    return (inference.model_name(model), prompt, schema,
            min(float(timeout), MAX_TIMEOUT))


def runtime_reachable(timeout=3):
    """Is there a model runtime behind us? For /healthz only."""
    try:
        url = inference.host() + "/api/tags"
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def create_app(runtime=None, accepted=None, slots=None):
    """The service, and gunicorn's application factory.

    `runtime`, `accepted` and `slots` are injected by the tests; in
    production all three come from the environment, and the token check
    happens HERE so a misconfigured worker fails to boot rather than
    binding a socket that will refuse everything.
    """
    adopt_gunicorn_logging()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    app.config["RUNTIME"] = runtime or RUNTIME()
    app.config["TOKENS"] = accepted
    if accepted is None:
        require_tokens()
    # One permit per concurrent generation. Bounded so a release() bug
    # cannot quietly widen it — the failure is loud, at the bug, rather
    # than as an unexplained second model load a week later.
    app.config["SLOTS"] = slots if slots is not None else workers()
    app.config["MODEL_SLOT"] = threading.BoundedSemaphore(
        app.config["SLOTS"])
    app.config["QUEUE_WAIT"] = queue_wait()

    def accepted_tokens():
        configured = app.config["TOKENS"]
        return tokens() if configured is None else configured

    def fail(status, category, message):
        """The one error shape. inference._from_error reads `category`."""
        g.category = category
        return jsonify({"request_id": getattr(g, "request_id", "-"),
                        "error": {"category": category,
                                  "message": message}}), status

    @app.before_request
    def _identify():
        g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        g.started = time.time()
        g.category = "ok"

    @app.after_request
    def _log(response):
        """Five facts, and no sixth.

        request id, model, duration, outcome and the category — plus the
        path and status those two describe. Nothing else, and in
        particular nothing DERIVED from the request body.

        prompt_bytes used to be here. It is a size, not content, and it
        was genuinely useful for spotting a slow call — and it is still
        gone, because "it is only a size" is the argument every field
        makes on the way in. A service that handles other people's
        résumés earns trust by having nothing to explain, not by having
        a good explanation. Duration and the outcome category are enough
        to debug with; if they ever stop being enough, the thing to add
        is a metric, not a log field.
        """
        log.info("request_id=%s path=%s status=%s model=%s duration_ms=%d "
                 "outcome=%s",
                 getattr(g, "request_id", "-"), request.path,
                 response.status_code, getattr(g, "model", "-"),
                 int((time.time() - getattr(g, "started", time.time())) * 1000),
                 getattr(g, "category", "ok"))
        response.headers["X-Request-Id"] = getattr(g, "request_id", "-")
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_exc):
        # Werkzeug rejects on Content-Length, so this fires before the body
        # is read — the point of a size limit rather than a length check.
        return fail(413, "payload_too_large",
                    f"the request body exceeds the {MAX_BODY_BYTES}-byte limit")

    @app.errorhandler(404)
    def _no_route(_exc):
        return fail(404, "not_found", "no such endpoint")

    @app.errorhandler(405)
    def _no_method(_exc):
        return fail(405, "not_found", "wrong method for this endpoint")

    @app.errorhandler(Exception)
    def _unhandled(exc):
        """An error from code this service does not control.

        The traceback is logged WITHOUT the exception's message, and that
        is not squeamishness. The catch-all exists for failures inside the
        runtime adapter, urllib and json — libraries that routinely put
        the thing they choked on INTO the message. json.JSONDecodeError
        quotes the document, a urllib error carries the URL, and a runtime
        adapter can quote the request body. For a service whose every
        request is somebody's résumé, the exception message is precisely
        where the résumé leaks.
        
        The frames survive, so the WHERE is intact — file, line and the
        source of each frame — and the request id joins it to the access
        line.

        ponytail: loses the exception message, which is sometimes the
        useful half (a bare KeyError names its key there). Upgrade path is
        a scrubber, or an operator-only flag that logs messages in full on
        a machine with no real résumés on it.
        """
        where = "".join(traceback.format_tb(exc.__traceback__)).strip()
        log.error("request_id=%s unhandled %s\n%s",
                  getattr(g, "request_id", "-"), type(exc).__name__, where)
        return fail(500, "internal_error",
                    "the inference service failed to handle the request")

    @app.get("/healthz")
    def healthz():
        """Unauthenticated on purpose: it is a liveness probe and it says
        nothing a caller could not learn by connecting."""
        # Answers in milliseconds while a 90-second generation is in
        # flight: it takes no model slot and asks the runtime a question
        # that does not touch the weights.
        up = runtime_reachable()
        return jsonify({"status": "ok" if up else "degraded",
                        "runtime_reachable": up,
                        "model": inference.model_name(),
                        "model_slots": app.config["SLOTS"]}), (200 if up
                                                               else 503)

    @app.post("/v1/generate")
    def generate():
        """One schema-constrained generation.

        A future per-user rate limiter goes exactly here: after the
        principal is known and before any runtime work is done.
        """
        who = principal(request.headers.get("Authorization"), accepted_tokens())
        if who is None:
            return fail(401, "unauthorized",
                        "a valid bearer token is required")
        # ponytail: no rate limiting yet, one shared dev token. A limiter
        # keyed on `who` slots in on this line when tokens become per-user.

        try:
            body = request.get_json(force=False, silent=True)
            model, prompt, schema, timeout = validate(body)
        except Refused as exc:
            return fail(exc.status, exc.category, exc.message)

        g.model = model

        # Wait for the model, do not race for it. A second concurrent
        # generation makes the runtime load a second copy of a 5GB model
        # or evict the first; both callers then finish later than either
        # would have alone.
        if not app.config["MODEL_SLOT"].acquire(
                timeout=app.config["QUEUE_WAIT"]):
            # 429, not 503. The difference is not pedantry: 503 says the
            # service is unavailable, and a caller that believes that has
            # a reason to go somewhere else — in Sweep's case, to a paid
            # API, under load, which is the worst possible moment to
            # start spending money. 429 says "you asked for more than
            # there is right now", which is exactly true and is the one
            # failure here a caller may sensibly retry.
            reply = fail(429, "model_busy",
                         f"all {app.config['SLOTS']} model slot(s) were "
                         f"busy for {app.config['QUEUE_WAIT']:g}s")
            # Advisory, and honest: the queue is however long it is, and
            # one generation is the shortest it can be.
            reply[0].headers["Retry-After"] = str(
                max(1, int(app.config["QUEUE_WAIT"])))
            return reply

        started = time.time()
        try:
            result = app.config["RUNTIME"].generate(model, prompt, schema,
                                                    timeout)
        except inference.ModelTimeout as exc:
            return fail(504, "model_timeout", str(exc))
        except inference.BadModelOutput as exc:
            return fail(502, "bad_model_output", str(exc))
        except inference.ModelUnavailable as exc:
            return fail(503, "model_unavailable", str(exc))
        finally:
            # Released on every path, including the unhandled-exception
            # one: a slot leaked on an error is a service that answers
            # `model_busy` forever and needs a restart to recover.
            app.config["MODEL_SLOT"].release()

        g.category = "ok"
        return jsonify({"request_id": g.request_id, "model": model,
                        "duration_ms": int((time.time() - started) * 1000),
                        "result": result})

    return app


def startup_report():
    """What this process is about to be, as one log line per fact.

    Checked and printed BEFORE the socket is bound, so a wrong model name
    or an unreachable runtime is visible at start rather than at the
    first request an hour later.
    """
    host, port = bind()
    lines = [f"bind            {host}:{port}",
             f"model slots     {workers()} "
             f"(queue wait {queue_wait():g}s)",
             f"runtime         {RUNTIME().describe()}",
             f"model           {inference.model_name()} "
             f"(keep_alive {inference.keep_alive()})",
             f"max prompt      {inference.MAX_PROMPT_BYTES} bytes",
             f"max timeout     {MAX_TIMEOUT}s"]
    if host not in ("127.0.0.1", "localhost", "::1"):
        lines.append(f"NOTE            bound beyond loopback via "
                     f"{HOST_BIND_ENV} — the bearer token is the only "
                     f"thing in front of the model")
    if not runtime_reachable():
        # A warning, not a failure. The runtime is a separate process
        # with its own restart; refusing to start would turn "Ollama is
        # slow to come up" into an outage that needs a human.
        lines.append(f"WARNING         no runtime answering at "
                     f"{inference.host()} — /healthz will report degraded "
                     f"and generations will fail with model_unavailable "
                     f"until it is up")
    return lines


def main(argv=None):
    """Validate, then become gunicorn.

    There is no development server any more. `python -m inference_service`
    execs the production one, so the thing that gets run in development is
    the thing that ships — and gunicorn's own graceful shutdown, worker
    timeouts and signal handling come free rather than being reimplemented
    here badly.
    """
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        require_tokens()
        for line in startup_report():
            log.info("%s", line)
    except Misconfigured as exc:
        sys.exit(str(exc))

    command = [sys.executable, "-m", "gunicorn", "-c", GUNICORN_CONF,
               "inference_service:create_app()", *argv]
    log.info("exec %s", " ".join(command[1:]))
    try:
        os.execv(sys.executable, command)
    except OSError as exc:
        sys.exit(f"could not start gunicorn ({exc}) — install it with "
                 f"`pip install gunicorn`")


def demo():
    """Self-check: the whole contract, with the runtime faked."""
    # The 500 case below logs a traceback on purpose; it is the proof that
    # the detail stays server-side, and it is noise in a self-check.
    logging.disable(logging.CRITICAL)
    answers = {"name": "Ada Okonkwo", "skills": ["python"]}

    class Fake:
        def __init__(self, raises=None):
            self.raises, self.seen = raises, []

        def generate(self, model, prompt, schema, timeout):
            self.seen.append((model, prompt, schema, timeout))
            if self.raises:
                raise self.raises
            return answers

    def client(runtime=None, slots=None):
        app = create_app(runtime or Fake(), accepted={"good"}, slots=slots)
        app.config["TESTING"] = True
        return app.test_client()

    good = {"Authorization": "Bearer good"}
    body = {"model": "qwen3:8b", "prompt": "read this", "schema": {"type": "object"}}

    # Success: the model's answer, plus operational metadata and nothing else.
    fake = Fake()
    reply = client(fake).post("/v1/generate", json=body, headers=good)
    assert reply.status_code == 200, reply.get_json()
    assert reply.get_json()["result"] == answers
    assert set(reply.get_json()) == {"request_id", "model", "duration_ms",
                                     "result"}
    assert fake.seen[0][:3] == ("qwen3:8b", "read this", {"type": "object"})
    # ... and an absent timeout is defaulted rather than passed as None.
    assert fake.seen[0][3] == DEFAULT_TIMEOUT

    # Authentication: missing, malformed and wrong all refuse identically,
    # and none of them reaches the runtime.
    fake = Fake()
    for headers in ({}, {"Authorization": "Bearer "},
                    {"Authorization": "good"},
                    {"Authorization": "Bearer wrong"}):
        reply = client(fake).post("/v1/generate", json=body, headers=headers)
        assert reply.status_code == 401, (headers, reply.status_code)
        assert reply.get_json()["error"]["category"] == "unauthorized"
    assert not fake.seen, "an unauthenticated request must not reach the model"

    # A request id is echoed when given and invented when not.
    reply = client().post("/v1/generate", json=body,
                          headers=dict(good, **{"X-Request-Id": "abc123"}))
    assert reply.headers["X-Request-Id"] == "abc123"
    assert reply.get_json()["request_id"] == "abc123"
    assert len(client().post("/v1/generate", json=body,
                             headers=good).get_json()["request_id"]) == 12

    # Strict schema: unknown keys, missing keys, wrong types, empties.
    for bad, why in (
            ({**body, "stream": True}, "unknown field"),
            ({"prompt": "p"}, "missing field"),
            ({"schema": {"type": "object"}}, "missing field"),
            ({"prompt": "", "schema": {"a": 1}}, "non-empty string"),
            ({"prompt": "   ", "schema": {"a": 1}}, "non-empty string"),
            ({"prompt": 7, "schema": {"a": 1}}, "non-empty string"),
            ({"prompt": "p", "schema": {}}, "non-empty JSON Schema"),
            ({"prompt": "p", "schema": "object"}, "non-empty JSON Schema"),
            ({"prompt": "p", "schema": {"a": 1}, "model": ""}, "model must"),
            ({"prompt": "p", "schema": {"a": 1}, "timeout": "60"}, "must be a number"),
            ({"prompt": "p", "schema": {"a": 1}, "timeout": True}, "must be a number"),
            ({"prompt": "p", "schema": {"a": 1}, "timeout": 0}, "must be positive"),
            ([], "must be a JSON object"),
            (None, "must be a JSON object")):
        reply = client().post("/v1/generate", json=bad, headers=good)
        assert reply.status_code == 400, (bad, reply.status_code)
        assert reply.get_json()["error"]["category"] == "bad_request"
        assert why in reply.get_json()["error"]["message"], (bad, reply.get_json())

    # An over-long timeout is clamped, not refused.
    fake = Fake()
    client(fake).post("/v1/generate", json={**body, "timeout": 9999},
                      headers=good)
    assert fake.seen[0][3] == MAX_TIMEOUT, fake.seen

    # Oversized: refused by the prompt limit, and by Werkzeug before that
    # for a body big enough to matter.
    huge = {**body, "prompt": "x" * (inference.MAX_PROMPT_BYTES + 1)}
    fake = Fake()
    reply = client(fake).post("/v1/generate", json=huge, headers=good)
    assert reply.status_code == 413, reply.status_code
    assert reply.get_json()["error"]["category"] == "payload_too_large"
    assert not fake.seen, "an oversized prompt must not reach the model"

    # Every runtime failure gets its own category and status.
    for error, status, category in (
            (inference.ModelUnavailable("nothing running"), 503,
             "model_unavailable"),
            (inference.ModelTimeout("too slow"), 504, "model_timeout"),
            (inference.BadModelOutput("not json"), 502, "bad_model_output")):
        reply = client(Fake(error)).post("/v1/generate", json=body,
                                         headers=good)
        assert reply.status_code == status, (category, reply.status_code)
        assert reply.get_json()["error"]["category"] == category
        assert str(error) in reply.get_json()["error"]["message"]

    # An unexpected failure is a 500 that says nothing about itself.
    reply = client(Fake(ZeroDivisionError("prompt: Ada Okonkwo, 5 years"))
                   ).post("/v1/generate", json=body, headers=good)
    assert reply.status_code == 500, reply.status_code
    assert reply.get_json()["error"]["category"] == "internal_error"
    assert "Ada" not in reply.get_data(as_text=True), reply.get_data(as_text=True)

    # Wrong routes answer in the same shape rather than in Werkzeug's HTML.
    for reply in (client().get("/v1/generate", headers=good),
                  client().post("/nope", json=body, headers=good)):
        assert reply.get_json()["error"]["category"] == "not_found"

    # A full model slot is 429 with a Retry-After, not 503 — it is
    # capacity, and the caller may sensibly come back.
    app = create_app(Fake(), accepted={"good"}, slots=1)
    app.config["TESTING"] = True
    app.config["QUEUE_WAIT"] = 0.01
    assert app.config["MODEL_SLOT"].acquire(timeout=1)
    try:
        reply = app.test_client().post("/v1/generate", json=body,
                                       headers=good)
    finally:
        app.config["MODEL_SLOT"].release()
    assert reply.status_code == 429, reply.status_code
    assert reply.get_json()["error"]["category"] == "model_busy"
    # Never 0: a Retry-After of zero is an invitation to hot-loop.
    assert int(reply.headers["Retry-After"]) >= 1, reply.headers

    # The log line carries five facts and no sixth. Nothing derived from
    # the request body, and in particular no prompt size.
    records = []

    class Sink(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    sink = Sink()
    log.addHandler(sink)
    # logging.disable() at the top of this demo is a global filter, not a
    # logger flag, so it has to be lifted rather than worked around.
    logging.disable(logging.NOTSET)
    was, log.disabled = log.disabled, False
    log.setLevel(logging.INFO)
    try:
        client().post("/v1/generate",
                      json=dict(body, prompt="Ada Okonkwo, 5 years"),
                      headers=good)
    finally:
        log.removeHandler(sink)
        log.disabled = was
        logging.disable(logging.CRITICAL)
    line = " ".join(records)
    for field in ("request_id=", "model=", "duration_ms=", "outcome=ok",
                  "status=200"):
        assert field in line, (field, line)
    for gone in ("prompt_bytes", "waited_ms", "Okonkwo", "5 years"):
        assert gone not in line, (gone, line)

    # /healthz needs no token and reports what it found.
    health = client().get("/healthz").get_json()
    assert set(health) == {"status", "runtime_reachable", "model",
                           "model_slots"}
    assert health["status"] in ("ok", "degraded")
    assert health["model_slots"] == DEFAULT_WORKERS

    # The model slot is released on every path, including the failing
    # ones — a leaked slot is a service that answers model_busy forever.
    for error in (inference.ModelUnavailable("x"), inference.ModelTimeout("x"),
                  inference.BadModelOutput("x"), ZeroDivisionError("x")):
        app = create_app(Fake(error), accepted={"good"}, slots=1)
        app.config["TESTING"] = True
        app.test_client().post("/v1/generate", json=body, headers=good)
        assert app.config["MODEL_SLOT"].acquire(timeout=0), (
            f"the model slot leaked on {type(error).__name__}")
        app.config["MODEL_SLOT"].release()

    # Deployment settings come from the environment, and a typo is a
    # startup failure rather than a silent default.
    assert bind() == (DEFAULT_BIND, DEFAULT_PORT)
    assert workers() == DEFAULT_WORKERS and queue_wait() == DEFAULT_QUEUE_WAIT
    for name, value in ((WORKERS_ENV, "two"), (WORKERS_ENV, "0"),
                        (PORT_ENV, "-1"), (QUEUE_WAIT_ENV, "nope")):
        os.environ[name] = value
        try:
            bind(), workers(), queue_wait()
            raise AssertionError(f"{name}={value!r} must not be accepted")
        except Misconfigured:
            pass
        finally:
            del os.environ[name]
    os.environ[HOST_BIND_ENV], os.environ[WORKERS_ENV] = "0.0.0.0", "3"
    try:
        assert bind() == ("0.0.0.0", DEFAULT_PORT) and workers() == 3
        assert any("beyond loopback" in line for line in startup_report())
    finally:
        del os.environ[HOST_BIND_ENV], os.environ[WORKERS_ENV]

    # An unconfigured token is a startup failure, at both start paths.
    was = os.environ.get(inference.TOKEN_ENV)
    os.environ[inference.TOKEN_ENV] = ""
    try:
        for start in (require_tokens, create_app):
            try:
                start()
                raise AssertionError(f"{start.__name__} must refuse to start")
            except Misconfigured as exc:
                assert "shared secret" in str(exc), exc
    finally:
        if was is None:
            del os.environ[inference.TOKEN_ENV]
        else:
            os.environ[inference.TOKEN_ENV] = was

    # principal() is the seam a rate limiter will key on.
    assert principal("Bearer good", {"good"}) == "good"
    assert principal("Bearer good", {"other"}) is None
    assert principal(None, {"good"}) is None
    assert principal("bearer good", {"good"}) == "good"

    print("inference_service demo ok")


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        demo()
    else:
        main()
