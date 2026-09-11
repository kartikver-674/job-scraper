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

WHAT IS NOT HERE YET, ON PURPOSE
--------------------------------
No per-user accounts, no rate limiting, no registration. The shape that
those will need is here — a bearer token resolved to a principal before
any work happens, one endpoint, one request id — so a limiter keyed on
principal() drops in front of generate() without the contract moving. See
principal().

    SWEEP_INFERENCE_TOKEN=dev-token python -m inference_service
    SWEEP_INFERENCE_TOKEN=dev-token python -m inference_service --demo
"""

import json
import logging
import os
import time
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

PORT_ENV = "SWEEP_INFERENCE_PORT"
DEFAULT_PORT = 8811

# Exactly these keys, and no others. A caller sending something we do not
# understand has a different idea of the contract than we do, and silently
# ignoring the extra key is how the two ideas stay different.
REQUEST_KEYS = {"model", "prompt", "schema", "timeout"}
REQUIRED_KEYS = {"prompt", "schema"}

log = logging.getLogger("inference")


class Refused(Exception):
    """A request we will not run, with the category the client gets back."""

    def __init__(self, status, category, message):
        self.status, self.category, self.message = status, category, message
        super().__init__(message)


def tokens():
    """The bearer tokens this service accepts, from SWEEP_INFERENCE_TOKEN.

    Comma-separated so the value can be rotated without a restart window.
    Empty means the service will not start — an unauthenticated model
    endpoint on a laptop is still an unauthenticated model endpoint, and
    "it's only localhost" stops being true the first time it is not.
    """
    raw = os.environ.get(inference.TOKEN_ENV) or ""
    return {t.strip() for t in raw.split(",") if t.strip()}


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
            min(float(timeout), MAX_TIMEOUT), size)


def runtime_reachable(timeout=3):
    """Is there a model runtime behind us? For /healthz only."""
    try:
        url = inference.host() + "/api/tags"
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def create_app(runtime=None, accepted=None):
    """The service. `runtime` and `accepted` are injected by the tests."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    app.config["RUNTIME"] = runtime or RUNTIME()
    app.config["TOKENS"] = accepted

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
        g.prompt_bytes = 0

    @app.after_request
    def _log(response):
        # Operational metadata only. Note what is NOT here: the prompt, the
        # model's answer, and anything derived from either.
        log.info("request_id=%s path=%s status=%s model=%s prompt_bytes=%d "
                 "duration_ms=%d outcome=%s",
                 getattr(g, "request_id", "-"), request.path,
                 response.status_code, getattr(g, "model", "-"),
                 getattr(g, "prompt_bytes", 0),
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
        # Never the exception text: it can carry the prompt, a file path or
        # an upstream URL. The request id is how you find it in the log.
        log.exception("request_id=%s unhandled %s",
                      getattr(g, "request_id", "-"), type(exc).__name__)
        return fail(500, "internal_error",
                    "the inference service failed to handle the request")

    @app.get("/healthz")
    def healthz():
        """Unauthenticated on purpose: it is a liveness probe and it says
        nothing a caller could not learn by connecting."""
        up = runtime_reachable()
        return jsonify({"status": "ok" if up else "degraded",
                        "runtime_reachable": up,
                        "model": inference.model_name()}), (200 if up else 503)

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
            model, prompt, schema, timeout, size = validate(body)
        except Refused as exc:
            return fail(exc.status, exc.category, exc.message)

        g.model, g.prompt_bytes = model, size
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

        g.category = "ok"
        return jsonify({"request_id": g.request_id, "model": model,
                        "duration_ms": int((time.time() - started) * 1000),
                        "result": result})

    return app


def main(argv=None):
    import sys

    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not tokens():
        sys.exit(f"set {inference.TOKEN_ENV} to a shared secret before "
                 f"starting — an open model endpoint is not a dev "
                 f"convenience, it is a dev incident")
    port = int(os.environ.get(PORT_ENV) or DEFAULT_PORT)
    log.info("inference service on :%d -> %s (model %s)", port,
             RUNTIME().describe(), inference.model_name())
    # threaded so /healthz answers while a generation is in flight.
    create_app().run(host="127.0.0.1", port=port, threaded=True,
                     debug="--debug" in argv)


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

    def client(runtime=None):
        app = create_app(runtime or Fake(), accepted={"good"})
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

    # /healthz needs no token and reports what it found.
    health = client().get("/healthz").get_json()
    assert set(health) == {"status", "runtime_reachable", "model"}
    assert health["status"] in ("ok", "degraded")

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
