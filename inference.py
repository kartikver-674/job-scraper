"""Where a model call goes. One function, two backends, no fallback.

Every model call Sweep makes is the same shape — a prompt, a JSON Schema
the answer must satisfy, and a deadline — so the seam between Sweep and
whatever runs the model is one function:

    provider().generate(model, prompt, schema, timeout) -> parsed JSON

`local_extract` calls that and nothing else. It no longer knows the word
Ollama. Two backends implement it, chosen by SWEEP_INFERENCE_BACKEND:

  local-direct  urllib straight to Ollama on this machine. The path that
                has always run; kept as the regression baseline.
  remote        HTTP to inference_service.py, which talks to Ollama on
                Sweep's behalf. The POC.

WHY THE PROMPT CROSSES THE WIRE AND THE SCHEMA WITH IT
-----------------------------------------------------
The obvious alternative was a service that owns the prompts — POST
{"text": ..., "call": "fields"} — and it is worse. FIELDS_PROMPT and
EMPLOYMENT_SCHEMA are the measured artefacts; every line of them exists
because the benchmark caught something. Copying them across a process
boundary gives them two homes and one of the two drifts. They stay in
local_extract.py, which is the file the benchmark scores, and the service
stays a dumb pipe that is told what to ask.

That also makes the service provider-agnostic for free: it is handed a
prompt and a schema, not a résumé, so replacing Ollama behind it with
vLLM, llama.cpp or a hosted endpoint changes inference_service.py alone.

WHAT A FAILURE MEANS
--------------------
Two error classes, because they route differently and always have:

  ModelUnavailable    there is no model to ask — nothing running, model
                      not pulled, the answer was not the JSON it was
                      asked for. make_profile's `local-first` engine has
                      always treated this as worth one Gemini call.
  RemoteServiceError  the service itself is wrong — bad token, 5xx, a
                      response that is not the contract, a request too
                      large. A misconfiguration, not a busy laptop, and
                      it is NOT quietly turned into a Gemini call and NOT
                      retried against local Ollama. It stops.

The second class is the whole reason this file exists. A remote backend
that silently answers from the local one is a backend nobody can measure.

    python -m inference --demo
"""

import json
import os
import urllib.error
import urllib.request

# Which backend Sweep uses. Defaults to the path that has always run, so
# turning the service on is a deliberate act and every existing install
# keeps its current behaviour.
BACKEND_ENV = "SWEEP_INFERENCE_BACKEND"
BACKENDS = ("local-direct", "remote")
DEFAULT_BACKEND = "local-direct"

# Where the inference service is, and the shared secret. The token is the
# principal a future per-user rate limiter would key on, which is why it
# is a bearer header rather than a query parameter or a fixed localhost
# allowance — see inference_service.principal().
URL_ENV = "SWEEP_INFERENCE_URL"
TOKEN_ENV = "SWEEP_INFERENCE_TOKEN"
DEFAULT_URL = "http://127.0.0.1:8811"

# Ollama's own variable names, because "install Ollama" is what replaces
# "supply an API key". Read by the local-direct backend and by the service
# — the two places that are allowed to know what a runtime is.
HOST_ENV = "OLLAMA_HOST"
MODEL_ENV = "OLLAMA_MODEL"
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"

# Unload as soon as a request finishes. Left resident, an 8B model is more
# than a laptop has to spare while a sweep is also running.
KEEP_ALIVE = "30s"

# How much longer the HTTP hop waits than the model deadline it is passing
# on, so a slow model comes back as the service's own 504 with a category
# on it rather than as a dead socket the client has to guess about.
HTTP_SLACK = 15

# The contract's own limit, enforced on both sides. A résumé that does not
# fit in 256KB of prompt is not a résumé, and a service that will accept
# any body at all is a service one client can wedge.
MAX_PROMPT_BYTES = 256 * 1024


class InferenceError(RuntimeError):
    """Base: a model call did not produce an answer."""


class ModelUnavailable(InferenceError):
    """There is no model to ask, or it did not answer in the shape asked.

    Carries a short, actionable reason and never the exception text from
    urllib, which can include the request URL.
    """


class ModelTimeout(ModelUnavailable):
    """The model did not finish inside the deadline.

    A ModelUnavailable subclass on purpose: to every existing caller a
    model that will not answer in time and a model that is not there are
    the same thing, and that routing is measured behaviour. The service
    needs them apart only to pick a status code.
    """


class BadModelOutput(ModelUnavailable):
    """The runtime answered, but not with the JSON it was asked for."""


class RemoteServiceError(InferenceError):
    """The inference service is unreachable, refusing, or off-contract.

    Deliberately NOT a ModelUnavailable: the caller must not be able to
    mistake a misconfigured service for a laptop with no model on it and
    quietly do something else.
    """


def host():
    """Base URL of the Ollama server, from OLLAMA_HOST.

    Ollama's own variable is routinely written without a scheme
    ("127.0.0.1:11434", "ollama:11434" inside compose), so a missing one
    is supplied rather than producing a urllib error nobody can read.
    """
    return _base(os.environ.get(HOST_ENV), DEFAULT_HOST)


def service_url():
    """Base URL of the inference service, from SWEEP_INFERENCE_URL."""
    return _base(os.environ.get(URL_ENV), DEFAULT_URL)


def _base(value, default):
    value = (value or "").strip() or default
    if "://" not in value:
        value = "http://" + value
    return value.rstrip("/")


def model_name(model=None):
    """The model to ask: an explicit one, else OLLAMA_MODEL, else the
    default. Passed through so a caller can always override."""
    return (model or os.environ.get(MODEL_ENV) or "").strip() or DEFAULT_MODEL


def backend_name(backend=None):
    """The backend to use, validated. An unknown name is an error, not a
    silent fall back to local — the whole point of the remote backend is
    that you can tell which one answered."""
    name = (backend or os.environ.get(BACKEND_ENV) or DEFAULT_BACKEND)
    name = name.strip().lower()
    if name not in BACKENDS:
        raise ValueError(
            f"{BACKEND_ENV}={name!r} is not a backend — expected one of "
            f"{', '.join(BACKENDS)}")
    return name


def ctx_for(prompt, reply_tokens=768, floor=2048, ceiling=8192):
    """A context window sized to the document, not guessed at.

    The KV cache scales with this number whether the tokens are used or
    not, and the first version asked for 16,384 on an 8B model — roughly
    5GB of weights plus another 5GB of cache — against a longest prompt of
    919 tokens. The machine ran out of memory and restarted.

    ~4 chars per token is rough, so `reply_tokens` of headroom covers both
    the estimate being wrong and the JSON coming back. Rounded up to a
    power of two because runtimes allocate in blocks anyway.

    Lives here rather than in local_extract because it sizes a REQUEST,
    not an extraction: the service computes it from the prompt it was
    handed, so a caller cannot ask a runtime for 40k of KV cache.
    """
    need = len(prompt) // 4 + reply_tokens
    size = floor
    while size < need and size < ceiling:
        size *= 2
    return min(size, ceiling)


def _post(url, body, timeout, headers=None):
    """One JSON POST. Returns (status, parsed body). Raises URLError etc."""
    request = urllib.request.Request(
        url, json.dumps(body).encode(),
        dict({"Content-Type": "application/json"}, **(headers or {})))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read())


# --------------------------------------------------------------------------
# local-direct: urllib straight to Ollama
# --------------------------------------------------------------------------

class LocalOllama:
    """The path that has always run, moved verbatim out of local_extract."""

    name = "local-direct"

    def __init__(self, url=None):
        # Resolved per call when not given, never bound as a default
        # argument. Freezing OLLAMA_HOST at import time is what made it
        # unreachable from a test: a test that patched the constant made a
        # real model call and asserted the wrong failure.
        self._url = url

    def endpoint(self):
        return (self._url or host() + "/api/generate")

    def describe(self):
        return self.endpoint().rsplit("/api/", 1)[0]

    def generate(self, model, prompt, schema, timeout):
        """One schema-constrained generation. Returns the parsed JSON.

        `think: False` is not optional. Without it the model reasons at
        length before extracting, which measured 37.4s per document
        against 9.4s with it — and scored WORSE, because copying a date
        out of a table is not a reasoning task.
        """
        url = self.endpoint()
        try:
            _status, payload = _post(url, {
                "model": model,
                "prompt": prompt,
                "format": schema,
                "stream": False,
                "keep_alive": KEEP_ALIVE,
                "think": False,
                "options": {"temperature": 0, "num_ctx": ctx_for(prompt)},
            }, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ModelUnavailable(
                    f"the local model {model!r} is not installed — "
                    f"run `ollama pull {model}`") from None
            raise ModelUnavailable(
                f"the local model server answered {exc.code}") from None
        except json.JSONDecodeError:
            raise BadModelOutput(
                f"the local model server returned a body that is not "
                f"JSON") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # A read timeout arrives as TimeoutError; a connect timeout
            # arrives wrapped in URLError. Both are the model taking too
            # long rather than the machine having none, and only the
            # service cares about the difference — see ModelTimeout.
            if isinstance(exc, TimeoutError) or isinstance(
                    getattr(exc, "reason", None), TimeoutError):
                raise ModelTimeout(
                    f"{model} did not answer within {timeout:g}s") from None
            raise ModelUnavailable(
                "no local model server is reachable at "
                f"{self.describe()} — is Ollama running?") from None
        try:
            return json.loads(payload["response"])
        except (KeyError, TypeError, json.JSONDecodeError):
            raise BadModelOutput(
                f"{model} returned an answer that was not the JSON it was "
                f"asked for") from None


# --------------------------------------------------------------------------
# remote: HTTP to the inference service
# --------------------------------------------------------------------------

# The service's own error categories, and which of the two exception
# classes each one is. Anything unrecognised is a service problem, not a
# model problem — an unknown category means the contract moved.
MODEL_CATEGORIES = ("model_unavailable", "model_timeout", "bad_model_output")


class RemoteService:
    """HTTP to inference_service.py. Never falls back to local Ollama."""

    name = "remote"

    def __init__(self, url=None, token=None):
        self._url = url
        self._token = token

    def endpoint(self):
        return (self._url or service_url()) + "/v1/generate"

    def describe(self):
        return self._url or service_url()

    def token(self):
        return (self._token if self._token is not None
                else os.environ.get(TOKEN_ENV) or "")

    def generate(self, model, prompt, schema, timeout):
        """One generation, through the service. Same return as local."""
        # Checked here as well as server-side so an oversized prompt costs
        # nothing to reject and fails the same way whichever backend is on.
        size = len(prompt.encode())
        if size > MAX_PROMPT_BYTES:
            raise RemoteServiceError(
                f"the prompt is {size} bytes, over the "
                f"{MAX_PROMPT_BYTES}-byte limit the inference service "
                f"accepts")
        url = self.endpoint()
        try:
            _status, payload = _post(
                url, {"model": model, "prompt": prompt, "schema": schema,
                      "timeout": timeout}, timeout + HTTP_SLACK,
                {"Authorization": f"Bearer {self.token()}"})
        except urllib.error.HTTPError as exc:
            raise _from_error(exc, model) from None
        except json.JSONDecodeError:
            raise RemoteServiceError(
                f"the inference service at {self.describe()} answered with "
                f"a body that is not JSON") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RemoteServiceError(
                f"the inference service at {self.describe()} did not "
                f"answer ({type(exc).__name__}) — no local fallback was "
                f"tried") from None
        if not isinstance(payload, dict) or "result" not in payload:
            raise RemoteServiceError(
                "the inference service returned a body that is not the "
                "agreed response shape (no 'result')")
        return payload["result"]


def _from_error(exc, model):
    """An HTTP error from the service, as the right exception class.

    The category is the contract; the status code is a hint. A body that
    is not the agreed error shape is itself a service failure — that is
    the malformed-response case, and treating it as a model problem would
    send a broken deployment round the Gemini fallback forever.
    """
    try:
        body = json.loads(exc.read())
        error = body["error"]
        category, message = error["category"], error["message"]
    except (AttributeError, KeyError, TypeError, ValueError):
        return RemoteServiceError(  # noqa: TRY300 -- closed in `finally`
            f"the inference service answered {exc.code} with a body that "
            f"is not the agreed error shape")
    finally:
        # An HTTPError holds an open response — a spooled temp file once
        # the body is big enough — and leaking it is a ResourceWarning
        # per failed request.
        exc.close()
    if category in MODEL_CATEGORIES:
        return ModelUnavailable(f"{message} (via the inference service)")
    return RemoteServiceError(
        f"the inference service refused the request [{category}]: {message}")


# --------------------------------------------------------------------------

def provider(backend=None, **kwargs):
    """The backend named by SWEEP_INFERENCE_BACKEND, or the one asked for."""
    name = backend_name(backend)
    return RemoteService(**kwargs) if name == "remote" else LocalOllama(**kwargs)


def demo():
    """Self-check: no model needed, no service needed."""
    import io
    import unittest.mock as mock

    assert ctx_for("x" * 100) == 2048
    assert ctx_for("x" * 40000) == 8192

    # Hosts are normalised the way Ollama's own variable gets written.
    for given, want in (("", DEFAULT_HOST), ("127.0.0.1:11434",
                                             "http://127.0.0.1:11434"),
                        ("ollama:11434", "http://ollama:11434"),
                        ("https://box.internal/", "https://box.internal")):
        assert _base(given, DEFAULT_HOST) == want, (given, _base(given, DEFAULT_HOST))

    # Backend selection, and an unknown name is an error rather than a
    # silent local run.
    assert isinstance(provider("local-direct"), LocalOllama)
    assert isinstance(provider("remote"), RemoteService)
    assert isinstance(provider("REMOTE "), RemoteService)
    try:
        provider("gemini")
        raise AssertionError("an unknown backend must not be accepted")
    except ValueError as exc:
        assert "not a backend" in str(exc), exc

    # A service error is classified by CATEGORY, not by status code.
    def http_error(code, body):
        return urllib.error.HTTPError(
            "http://x/v1/generate", code, "no", {},
            io.BytesIO(json.dumps(body).encode()))

    model_down = _from_error(http_error(503, {"error": {
        "category": "model_unavailable", "message": "no runtime"}}), "qwen3:8b")
    assert isinstance(model_down, ModelUnavailable), model_down

    for category in ("unauthorized", "payload_too_large", "bad_request"):
        err = _from_error(http_error(400, {"error": {
            "category": category, "message": "no"}}), "qwen3:8b")
        assert isinstance(err, RemoteServiceError), category
        assert not isinstance(err, ModelUnavailable), category

    # ... and a body that is not the contract is a SERVICE failure, never
    # a model one, however plausible the status code looks.
    junk = _from_error(http_error(503, {"detail": "gateway"}), "qwen3:8b")
    assert isinstance(junk, RemoteServiceError) and not isinstance(
        junk, ModelUnavailable), junk

    # An oversized prompt is refused before a socket is opened.
    try:
        RemoteService("http://127.0.0.1:1").generate(
            "m", "x" * (MAX_PROMPT_BYTES + 1), {}, 1)
        raise AssertionError("an oversized prompt must be refused")
    except RemoteServiceError as exc:
        assert "over the" in str(exc), exc

    # A 200 that is not the agreed shape is a service failure too.
    with mock.patch(f"{__name__}._post", return_value=(200, {"ok": True})):
        try:
            RemoteService("http://x").generate("m", "p", {}, 1)
            raise AssertionError("a bodyless 200 must not be accepted")
        except RemoteServiceError as exc:
            assert "not the agreed response shape" in str(exc), exc

    # An unreachable service is explicit and says no fallback happened.
    try:
        RemoteService("http://127.0.0.1:1").generate("m", "p", {}, 1)
        raise AssertionError("an unreachable service must raise")
    except RemoteServiceError as exc:
        assert "no local fallback" in str(exc), exc

    # An unreachable Ollama names Ollama, and is a MODEL failure.
    try:
        LocalOllama("http://127.0.0.1:1/api/generate").generate("m", "p", {}, 1)
        raise AssertionError("an unreachable Ollama must raise")
    except ModelUnavailable as exc:
        assert "Ollama running" in str(exc), exc

    print("inference demo ok")


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        demo()
    else:
        print(__doc__)
