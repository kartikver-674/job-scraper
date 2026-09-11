"""The boundary between Sweep and whatever runs the model.

Every test here drives a REAL HTTP server on a real loopback socket. A
mocked urlopen would prove the client's happy path and nothing else — and
the cases that matter are the unhappy ones: a body too large to accept, a
socket that never answers, a token that is wrong, a service returning
something that is not the contract. Those only exist over a wire.

The rule these exist to defend, stated once: a remote backend that cannot
answer FAILS. It does not quietly ask the local Ollama instead. A backend
that can silently become a different backend is a backend nobody can
measure, and measuring it is the entire point of the exercise.
"""

import contextlib
import json
import logging
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_APPLY = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(AUTO_APPLY)
for path in (REPO_ROOT, AUTO_APPLY):
    if path not in sys.path:
        sys.path.insert(0, path)

import inference
import inference_service
import local_extract

def setUpModule():
    """Quiet the servers. Every test here starts one, and werkzeug's access
    log plus the service's own would bury the assertions."""
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("inference").setLevel(logging.ERROR)


TOKEN = "test-token"
SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}}}
ANSWER = {"name": "Ada Okonkwo", "skills": ["python"]}

# Nothing in this suite may reach the developer's real Ollama. Every test
# either injects a fake runtime or points at a dead port; this is the
# belt-and-braces check that a regression cannot turn one of them into a
# live model call.
REAL_OLLAMA_PORT = "11434"


class FakeRuntime:
    """Stands in for Ollama behind the service. Records what it was asked."""

    def __init__(self, answer=ANSWER, raises=None, delay=0):
        self.answer, self.raises, self.delay = answer, raises, delay
        self.calls = []

    def generate(self, model, prompt, schema, timeout):
        self.calls.append({"model": model, "prompt": prompt,
                           "schema": schema, "timeout": timeout})
        if self.delay:
            time.sleep(self.delay)
        if self.raises:
            raise self.raises
        return self.answer

    def describe(self):
        return "fake://runtime"


@contextlib.contextmanager
def serving(app):
    """`app` on a real ephemeral port. Yields its base URL."""
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@contextlib.contextmanager
def service(runtime=None, token=TOKEN, slots=None):
    """The real Flask service over a real socket, with a fake runtime."""
    runtime = runtime if runtime is not None else FakeRuntime()
    app = inference_service.create_app(runtime, accepted={token} if token
                                       else set(), slots=slots)
    with serving(app) as url:
        yield url, runtime


class CountingRuntime(FakeRuntime):
    """Records the HIGHEST number of generations ever running at once."""

    def __init__(self, delay=0.3, **kw):
        super().__init__(delay=delay, **kw)
        self._lock = threading.Lock()
        self.running = 0
        self.peak = 0

    def generate(self, model, prompt, schema, timeout):
        with self._lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            return super().generate(model, prompt, schema, timeout)
        finally:
            with self._lock:
                self.running -= 1


@contextlib.contextmanager
def captured_logs(level=logging.DEBUG):
    """Every record this service emits, as a list, whatever the handler."""
    records = []

    class Sink(logging.Handler):
        def emit(self, record):
            records.append(record)

    sink = Sink(level)
    names = ("inference", "inference_service", "werkzeug", "flask.app", "")
    loggers = [logging.getLogger(n) for n in names]
    saved = [(lg, lg.level, lg.disabled) for lg in loggers]
    logging.disable(logging.NOTSET)
    for lg in loggers:
        lg.addHandler(sink)
        lg.setLevel(level)
        lg.disabled = False
    try:
        yield records
    finally:
        for lg in loggers:
            lg.removeHandler(sink)
        for lg, was_level, was_disabled in saved:
            lg.setLevel(was_level)
            lg.disabled = was_disabled


@contextlib.contextmanager
def raw_service(status, body, content_type="application/json"):
    """A server that answers with whatever we say, contract or not."""
    payload = body if isinstance(body, bytes) else json.dumps(body).encode()

    def app(environ, start_response):
        start_response(f"{status} x", [("Content-Type", content_type),
                                       ("Content-Length", str(len(payload)))])
        return [payload]

    with serving(app) as url:
        yield url


def client(url, token=TOKEN):
    return inference.RemoteService(url=url, token=token)


@contextlib.contextmanager
def no_real_ollama():
    """Fail the test if anything opens a socket to the machine's Ollama."""
    real = urllib.request.urlopen

    def guard(request, *args, **kwargs):
        target = getattr(request, "full_url", request)
        if REAL_OLLAMA_PORT in str(target):
            raise AssertionError(
                f"a test reached the real Ollama at {target} — the remote "
                f"backend fell back to local")
        return real(request, *args, **kwargs)

    with mock.patch("urllib.request.urlopen", guard):
        yield


# --------------------------------------------------------------------------

class TestHttpSuccess(unittest.TestCase):
    """The happy path, end to end over a socket."""

    def test_the_model_answer_comes_back_unchanged(self):
        with service() as (url, runtime):
            got = client(url).generate("qwen3:8b", "read this", SCHEMA, 30)
        self.assertEqual(got, ANSWER)
        self.assertEqual(runtime.calls[0]["model"], "qwen3:8b")
        self.assertEqual(runtime.calls[0]["prompt"], "read this")
        self.assertEqual(runtime.calls[0]["schema"], SCHEMA)

    def test_the_prompt_and_schema_cross_the_wire_byte_for_byte(self):
        """The measured artefacts must not be reshaped in transit.

        FIELDS_PROMPT and EMPLOYMENT_SCHEMA are what the benchmark scores.
        If the boundary rewrote either, the remote backend would be
        running a different extractor than the one that was measured.
        """
        text = "Ada Okonkwo\nBackend Engineer — Fettle Health\n£75,000"
        prompt = local_extract.FIELDS_PROMPT.format(text=text)
        with service() as (url, runtime):
            client(url).generate("qwen3:8b", prompt,
                                 local_extract.FIELDS_SCHEMA, 30)
        self.assertEqual(runtime.calls[0]["prompt"], prompt)
        self.assertEqual(runtime.calls[0]["schema"],
                         local_extract.FIELDS_SCHEMA)

    def test_the_whole_extraction_step_runs_through_the_service(self):
        """local_extract.extract against the remote backend."""
        fields = {"name": "Ada Okonkwo", "years_experience": 5,
                  "titles": ["Backend Engineer"], "skills": ["python"],
                  "companies": ["Fettle Health"], "education": [],
                  "institutions": [], "projects": [], "certifications": []}
        with no_real_ollama(), service(FakeRuntime(fields)) as (url, runtime):
            with mock.patch.dict(os.environ, {inference.URL_ENV: url,
                                              inference.TOKEN_ENV: TOKEN}):
                parsed, seconds = local_extract.extract(
                    "qwen3:8b", "Ada Okonkwo, Backend Engineer",
                    backend="remote")
        self.assertEqual(parsed, fields)
        self.assertGreaterEqual(seconds, 0)
        self.assertEqual(len(runtime.calls), 1)

    def test_the_response_carries_metadata_and_no_prompt(self):
        """What comes back is the answer plus operational facts only."""
        with service() as (url, _runtime):
            request = urllib.request.Request(
                url + "/v1/generate",
                json.dumps({"model": "m", "prompt": "SECRET RESUME TEXT",
                            "schema": SCHEMA}).encode(),
                {"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"})
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read().decode()
        body = json.loads(raw)
        self.assertEqual(set(body),
                         {"request_id", "model", "duration_ms", "result"})
        self.assertNotIn("SECRET RESUME TEXT", raw)


class TestMalformedResponse(unittest.TestCase):
    """Two different malformations, two different classes."""

    def test_a_model_answer_that_is_not_json_is_a_model_failure(self):
        # The runtime answered; what it said was not the JSON it was asked
        # for. That is the model's fault, so it routes like one.
        with service(FakeRuntime(raises=inference.BadModelOutput(
                "qwen3:8b returned an answer that was not the JSON it was "
                "asked for"))) as (url, _runtime):
            with self.assertRaises(inference.ModelUnavailable) as caught:
                client(url).generate("qwen3:8b", "p", SCHEMA, 10)
        self.assertIn("not the JSON", str(caught.exception))

    def test_a_body_that_is_not_the_contract_is_a_service_failure(self):
        # A 200 with no "result": the service is wrong, not the model, and
        # it must not be mistaken for a laptop with no model on it.
        for body in ({"ok": True}, {"response": ANSWER}, [], "fine"):
            with self.subTest(body=body), raw_service(200, body) as url:
                with self.assertRaises(inference.RemoteServiceError) as caught:
                    client(url).generate("m", "p", SCHEMA, 10)
                self.assertNotIsInstance(caught.exception,
                                         inference.ModelUnavailable)
                self.assertIn("response shape", str(caught.exception))

    def test_an_error_body_that_is_not_the_contract_is_a_service_failure(self):
        # A plausible-looking 503 from something that is not our service —
        # a proxy, a load balancer, a tunnel. Without the category it is
        # NOT a model failure, or a broken deployment would route round
        # the Gemini fallback forever.
        for status, body in ((503, {"detail": "upstream unavailable"}),
                             (502, b"<html>Bad Gateway</html>"),
                             (503, {"error": "model_unavailable"})):
            with self.subTest(status=status), raw_service(status, body) as url:
                with self.assertRaises(inference.RemoteServiceError) as caught:
                    client(url).generate("m", "p", SCHEMA, 10)
                self.assertNotIsInstance(caught.exception,
                                         inference.ModelUnavailable)
                self.assertIn("agreed error shape", str(caught.exception))

    def test_unparseable_json_from_the_service_is_a_service_failure(self):
        with raw_service(200, b"{not json at all") as url:
            with self.assertRaises(inference.RemoteServiceError):
                client(url).generate("m", "p", SCHEMA, 10)


class TestModelUnavailable(unittest.TestCase):
    """No model behind the service, and no model at all."""

    def test_a_dead_runtime_reaches_the_caller_as_model_unavailable(self):
        down = inference.ModelUnavailable(
            "no local model server is reachable at http://127.0.0.1:11434 "
            "— is Ollama running?")
        with service(FakeRuntime(raises=down)) as (url, _runtime):
            with self.assertRaises(inference.ModelUnavailable) as caught:
                client(url).generate("qwen3:8b", "p", SCHEMA, 10)
        self.assertIn("Ollama running", str(caught.exception))
        self.assertIn("inference service", str(caught.exception))

    def test_a_missing_model_names_the_pull_command(self):
        with service(FakeRuntime(raises=inference.ModelUnavailable(
                "the local model 'qwen3:70b' is not installed — run "
                "`ollama pull qwen3:70b`"))) as (url, _runtime):
            with self.assertRaises(inference.ModelUnavailable) as caught:
                client(url).generate("qwen3:70b", "p", SCHEMA, 10)
        self.assertIn("ollama pull", str(caught.exception))

    def test_the_local_backend_still_reports_it_the_way_it_always_did(self):
        # The regression guard on the whole refactor: local-direct must
        # fail exactly as before the transport moved.
        dead = "http://127.0.0.1:11544/api/generate"
        with self.assertRaises(local_extract.ModelUnavailable) as caught:
            inference.LocalOllama(dead).generate("m", "p", SCHEMA, 3)
        message = str(caught.exception)
        self.assertIn("Ollama running", message)
        self.assertIn("11544", message)
        self.assertNotIn("URLError", message)

    def test_healthz_says_degraded_when_there_is_no_runtime(self):
        with mock.patch.dict(os.environ,
                             {inference.HOST_ENV: "http://127.0.0.1:11544"}):
            with service() as (url, _runtime):
                try:
                    urllib.request.urlopen(url + "/healthz", timeout=10)
                    self.fail("a degraded service must not answer 200")
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, 503)
                    with exc:
                        body = json.loads(exc.read())
        self.assertEqual(body["status"], "degraded")
        self.assertFalse(body["runtime_reachable"])


class TestTimeout(unittest.TestCase):
    """Both halves: the model too slow, and the service too slow."""

    def test_a_slow_model_comes_back_as_a_categorised_504(self):
        with service(FakeRuntime(raises=inference.ModelTimeout(
                "qwen3:8b did not answer within 30s"))) as (url, _runtime):
            with self.assertRaises(inference.ModelUnavailable) as caught:
                client(url).generate("qwen3:8b", "p", SCHEMA, 30)
        self.assertIn("did not answer within", str(caught.exception))

    def test_a_service_that_never_answers_is_a_service_failure(self):
        # A real hang on a real socket. HTTP_SLACK is shortened so the
        # test costs a second rather than sixteen.
        slow = FakeRuntime(delay=5)
        with mock.patch.object(inference, "HTTP_SLACK", -0.5), \
             service(slow) as (url, _runtime):
            started = time.time()
            with self.assertRaises(inference.RemoteServiceError) as caught:
                client(url).generate("m", "p", SCHEMA, 1)
        self.assertLess(time.time() - started, 4)
        self.assertNotIsInstance(caught.exception,
                                 inference.ModelUnavailable)
        self.assertIn("did not answer", str(caught.exception))

    def test_the_deadline_is_passed_on_and_clamped(self):
        with service() as (url, runtime):
            client(url).generate("m", "p", SCHEMA, 30)
            client(url).generate("m", "p", SCHEMA, 9999)
        self.assertEqual(runtime.calls[0]["timeout"], 30)
        self.assertEqual(runtime.calls[1]["timeout"],
                         inference_service.MAX_TIMEOUT)

    def test_a_read_timeout_on_the_local_backend_is_a_model_timeout(self):
        def hang(request, timeout=None):
            raise TimeoutError("timed out")

        with mock.patch("urllib.request.urlopen", hang):
            with self.assertRaises(inference.ModelTimeout) as caught:
                inference.LocalOllama("http://127.0.0.1:11544/api/generate"
                                      ).generate("qwen3:8b", "p", SCHEMA, 7)
        # Still a ModelUnavailable, so every existing caller routes it the
        # way it always has.
        self.assertIsInstance(caught.exception, inference.ModelUnavailable)
        self.assertIn("within 7s", str(caught.exception))


class TestOversizedRequest(unittest.TestCase):
    """Refused on both sides of the wire, and never reaching the model."""

    def test_the_client_refuses_before_opening_a_socket(self):
        huge = "x" * (inference.MAX_PROMPT_BYTES + 1)
        with self.assertRaises(inference.RemoteServiceError) as caught:
            # A dead port: reaching it at all would be the failure.
            client("http://127.0.0.1:11544").generate("m", huge, SCHEMA, 10)
        self.assertIn("over the", str(caught.exception))

    def test_the_service_refuses_a_client_that_did_not_check(self):
        huge = "x" * (inference.MAX_PROMPT_BYTES + 1)
        with service() as (url, runtime):
            request = urllib.request.Request(
                url + "/v1/generate",
                json.dumps({"model": "m", "prompt": huge,
                            "schema": SCHEMA}).encode(),
                {"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(caught.exception.code, 413)
            with caught.exception as reply:
                body = json.loads(reply.read())
        self.assertEqual(body["error"]["category"], "payload_too_large")
        self.assertEqual(runtime.calls, [],
                         "an oversized prompt must never reach the model")

    def test_a_body_over_the_transport_limit_is_cut_off_by_werkzeug(self):
        # Bigger than MAX_BODY_BYTES, so it is refused on Content-Length
        # before the body is read at all.
        blob = "x" * (inference_service.MAX_BODY_BYTES + 1024)
        with service() as (url, runtime):
            request = urllib.request.Request(
                url + "/v1/generate",
                json.dumps({"model": "m", "prompt": blob,
                            "schema": SCHEMA}).encode(),
                {"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(caught.exception.code, 413)
            caught.exception.close()
        self.assertEqual(runtime.calls, [])

    def test_a_real_resume_sized_prompt_is_comfortably_inside_the_limit(self):
        # The limit has to be generous enough for the thing it exists for.
        # hana's is the longest benchmark document at three pages.
        prompt = local_extract.FIELDS_PROMPT.format(text="x" * 20000)
        self.assertLess(len(prompt.encode()), inference.MAX_PROMPT_BYTES)


class TestAuthenticationFailure(unittest.TestCase):
    """No token, wrong token, wrong scheme — and nothing reaches the model."""

    def test_a_wrong_token_is_refused_and_is_not_a_model_failure(self):
        with service() as (url, runtime):
            with self.assertRaises(inference.RemoteServiceError) as caught:
                client(url, token="wrong").generate("m", "p", SCHEMA, 10)
        self.assertNotIsInstance(caught.exception,
                                 inference.ModelUnavailable)
        self.assertIn("unauthorized", str(caught.exception))
        self.assertEqual(runtime.calls, [])

    def test_an_absent_token_is_refused(self):
        with service() as (url, runtime):
            with self.assertRaises(inference.RemoteServiceError):
                client(url, token="").generate("m", "p", SCHEMA, 10)
        self.assertEqual(runtime.calls, [])

    def test_the_token_is_read_from_the_environment_at_call_time(self):
        with service() as (url, runtime):
            remote = inference.RemoteService(url=url)
            with mock.patch.dict(os.environ, {inference.TOKEN_ENV: "nope"}):
                with self.assertRaises(inference.RemoteServiceError):
                    remote.generate("m", "p", SCHEMA, 10)
            with mock.patch.dict(os.environ, {inference.TOKEN_ENV: TOKEN}):
                self.assertEqual(remote.generate("m", "p", SCHEMA, 10), ANSWER)
        self.assertEqual(len(runtime.calls), 1)

    def test_a_comma_separated_list_of_tokens_is_accepted(self):
        # So a token can be rotated without a restart window.
        with mock.patch.dict(os.environ,
                             {inference.TOKEN_ENV: " old , new ,"}):
            self.assertEqual(inference_service.tokens(), {"old", "new"})
            self.assertEqual(
                inference_service.principal("Bearer new",
                                            inference_service.tokens()), "new")

    def test_the_service_refuses_to_start_with_no_token_configured(self):
        with mock.patch.dict(os.environ, {inference.TOKEN_ENV: ""}):
            with self.assertRaises(SystemExit) as caught:
                inference_service.main([])
        self.assertIn("shared secret", str(caught.exception))

    def test_healthz_needs_no_token(self):
        # It is a liveness probe and says nothing a caller could not learn
        # by connecting.
        with service() as (url, _runtime):
            with urllib.request.urlopen(url + "/healthz", timeout=10) as reply:
                self.assertEqual(reply.status, 200)


class TestRemoteServiceFailure(unittest.TestCase):
    """The service itself unreachable, broken, or refusing the request."""

    def test_an_unreachable_service_is_explicit_about_it(self):
        with self.assertRaises(inference.RemoteServiceError) as caught:
            client("http://127.0.0.1:11544").generate("m", "p", SCHEMA, 5)
        message = str(caught.exception)
        self.assertIn("11544", message)
        self.assertIn("no local fallback", message)

    def test_a_crash_inside_the_service_is_a_500_that_leaks_nothing(self):
        import logging
        boom = ZeroDivisionError("prompt was: Ada Okonkwo, ada@example.com")
        logging.disable(logging.CRITICAL)
        try:
            with service(FakeRuntime(raises=boom)) as (url, _runtime):
                with self.assertRaises(inference.RemoteServiceError) as caught:
                    client(url).generate("m", "p", SCHEMA, 10)
        finally:
            logging.disable(logging.NOTSET)
        message = str(caught.exception)
        self.assertIn("internal_error", message)
        self.assertNotIn("Ada Okonkwo", message)
        self.assertNotIn("example.com", message)

    def test_a_rejected_request_names_the_category(self):
        with raw_service(400, {"error": {"category": "bad_request",
                                         "message": "unknown field(s): stream"}}
                         ) as url:
            with self.assertRaises(inference.RemoteServiceError) as caught:
                client(url).generate("m", "p", SCHEMA, 10)
        self.assertIn("bad_request", str(caught.exception))
        self.assertIn("unknown field", str(caught.exception))


class TestNeverFallsBack(unittest.TestCase):
    """The rule this whole file exists for."""

    def test_a_dead_remote_service_does_not_reach_local_ollama(self):
        with no_real_ollama(), mock.patch.dict(os.environ, {
                inference.URL_ENV: "http://127.0.0.1:11544",
                inference.TOKEN_ENV: TOKEN,
                inference.HOST_ENV: "http://127.0.0.1:11434"}):
            with self.assertRaises(inference.RemoteServiceError):
                local_extract.extract("qwen3:8b", "résumé", backend="remote")

    def test_an_auth_failure_does_not_reach_local_ollama(self):
        with no_real_ollama(), service() as (url, _runtime):
            with mock.patch.dict(os.environ, {
                    inference.URL_ENV: url,
                    inference.TOKEN_ENV: "wrong",
                    inference.HOST_ENV: "http://127.0.0.1:11434"}):
                with self.assertRaises(inference.RemoteServiceError):
                    local_extract.employment("qwen3:8b", "résumé",
                                             backend="remote")

    def test_a_service_failure_is_not_dressed_up_as_a_model_failure(self):
        """The distinction make_profile's local-first engine routes on.

        ModelUnavailable means "no model to ask" and has always been worth
        one Gemini call. A misconfigured service is not that, and turning
        it into that would hide a broken deployment behind a paid API.
        """
        with raw_service(500, {"error": {"category": "internal_error",
                                         "message": "boom"}}) as url:
            with self.assertRaises(inference.InferenceError) as caught:
                client(url).generate("m", "p", SCHEMA, 10)
        self.assertNotIsInstance(caught.exception,
                                 inference.ModelUnavailable)

    def test_local_first_does_not_swallow_a_broken_service(self):
        import local_profile
        import make_profile

        asked = []
        with no_real_ollama(), mock.patch.dict(os.environ, {
                inference.URL_ENV: "http://127.0.0.1:11544",
                inference.TOKEN_ENV: TOKEN,
                inference.BACKEND_ENV: "remote"}):
            with mock.patch.object(make_profile, "_generate_one",
                                   lambda *a, **kw: asked.append(a)):
                with self.assertRaises(inference.RemoteServiceError):
                    make_profile.generate(object(), ("m",), "résumé", {},
                                          engine="local-first",
                                          log=lambda *a: None)
        self.assertEqual(asked, [],
                         "a broken inference service reached Gemini")
        # And the local engine does not exist to be a silent proxy either.
        self.assertIsNot(local_profile.Escalated, inference.RemoteServiceError)


class TestBackendSelection(unittest.TestCase):
    """Configuration, and what it does and does not change."""

    def test_the_default_is_the_path_that_has_always_run(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(inference.BACKEND_ENV, None)
            self.assertIsInstance(inference.provider(), inference.LocalOllama)

    def test_both_backends_are_selectable_by_environment(self):
        for name, want in (("local-direct", inference.LocalOllama),
                           ("remote", inference.RemoteService),
                           ("  REMOTE  ", inference.RemoteService)):
            with self.subTest(name), mock.patch.dict(
                    os.environ, {inference.BACKEND_ENV: name}):
                self.assertIsInstance(inference.provider(), want)

    def test_an_unknown_backend_is_an_error_not_a_quiet_local_run(self):
        with mock.patch.dict(os.environ,
                             {inference.BACKEND_ENV: "gemini"}):
            with self.assertRaises(ValueError) as caught:
                inference.provider()
        self.assertIn("not a backend", str(caught.exception))

    def test_local_extract_still_exports_every_name_it_used_to(self):
        """bench/, Sweep and the existing suite all read these from here."""
        for name in ("ModelUnavailable", "ctx_for", "host", "model_name",
                     "endpoint", "OLLAMA", "KEEP_ALIVE", "DEFAULT_MODEL",
                     "DEFAULT_HOST", "HOST_ENV", "MODEL_ENV"):
            self.assertTrue(hasattr(local_extract, name), name)
        # Same object, so `except local_extract.ModelUnavailable` catches
        # what inference.py raises.
        self.assertIs(local_extract.ModelUnavailable,
                      inference.ModelUnavailable)
        self.assertTrue(local_extract.OLLAMA.endswith("/api/generate"))

    def test_the_prompts_and_schemas_live_in_exactly_one_place(self):
        """The service must not have grown a copy of the measured prompts.

        Two homes for FIELDS_PROMPT is one home too many: the benchmark
        scores local_extract's copy and the service would run the other.
        """
        for module in (inference, inference_service):
            with open(module.__file__, encoding="utf-8") as fh:
                source = fh.read()
            for marker in ("Extract structured data from this résumé",
                           "List every EMPLOYMENT entry",
                           "years_experience", "target_field"):
                self.assertNotIn(marker, source,
                                 f"{module.__name__} has grown its own copy "
                                 f"of the extraction contract")


class TestProductionEntryPoint(unittest.TestCase):
    """`python -m inference_service` is the production command.

    There is no development server to forget to replace: main() validates
    the configuration and then BECOMES gunicorn.
    """

    def gunicorn_conf(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "gunicorn_conf", inference_service.GUNICORN_CONF)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_main_execs_gunicorn_with_the_config_and_the_factory(self):
        called = {}

        def fake_execv(path, argv):
            called["path"], called["argv"] = path, argv

        with mock.patch.dict(os.environ, {inference.TOKEN_ENV: TOKEN}), \
             mock.patch("os.execv", fake_execv):
            inference_service.main([])
        self.assertEqual(called["path"], sys.executable)
        self.assertEqual(called["argv"][:4],
                         [sys.executable, "-m", "gunicorn", "-c"])
        self.assertEqual(called["argv"][4], inference_service.GUNICORN_CONF)
        self.assertEqual(called["argv"][5], "inference_service:create_app()")

    def test_extra_arguments_are_passed_through_to_gunicorn(self):
        called = {}
        with mock.patch.dict(os.environ, {inference.TOKEN_ENV: TOKEN}), \
             mock.patch("os.execv", lambda p, a: called.update(argv=a)):
            inference_service.main(["--log-level", "debug"])
        self.assertEqual(called["argv"][-2:], ["--log-level", "debug"])

    def test_a_missing_token_stops_it_before_it_execs_anything(self):
        execed = []
        with mock.patch.dict(os.environ, {inference.TOKEN_ENV: ""}), \
             mock.patch("os.execv", lambda p, a: execed.append(a)):
            with self.assertRaises(SystemExit) as caught:
                inference_service.main([])
        self.assertIn("shared secret", str(caught.exception))
        self.assertEqual(execed, [], "it execed gunicorn with no token set")

    def test_a_missing_gunicorn_says_so_rather_than_tracebacking(self):
        with mock.patch.dict(os.environ, {inference.TOKEN_ENV: TOKEN}), \
             mock.patch("os.execv", mock.Mock(side_effect=OSError("nope"))):
            with self.assertRaises(SystemExit) as caught:
                inference_service.main([])
        self.assertIn("pip install gunicorn", str(caught.exception))

    def test_the_factory_gunicorn_is_pointed_at_actually_builds_the_app(self):
        # "inference_service:create_app()" is only a string until gunicorn
        # calls it. If the factory needed arguments, the worker would fail
        # to boot and this suite would never notice.
        with mock.patch.dict(os.environ, {inference.TOKEN_ENV: TOKEN}):
            app = inference_service.create_app()
        self.assertTrue(app.config["MODEL_SLOT"])
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"],
                         inference_service.MAX_BODY_BYTES)

    def test_the_factory_refuses_to_boot_a_worker_with_no_token(self):
        with mock.patch.dict(os.environ, {inference.TOKEN_ENV: ""}):
            with self.assertRaises(inference_service.Misconfigured):
                inference_service.create_app()

    def test_the_worker_timeouts_can_never_fire_before_the_app_deadline(self):
        """The invariant that would break silently if MAX_TIMEOUT moved.

        gunicorn KILLS a worker that exceeds `timeout`. Its default is 30s
        against extractions measured at 92s — on the defaults this service
        would kill itself on its ordinary workload, and the caller would
        see a dropped socket instead of a `model_timeout` category.
        """
        conf = self.gunicorn_conf()
        self.assertGreater(conf.timeout, inference_service.MAX_TIMEOUT)
        self.assertGreater(conf.graceful_timeout,
                           inference_service.MAX_TIMEOUT)

    def test_the_config_pins_one_worker_process(self):
        # The model slot is a threading.Semaphore, so it is per-process.
        # Two worker processes would mean two concurrent generations
        # against one Ollama — the exact thing the semaphore prevents.
        conf = self.gunicorn_conf()
        self.assertEqual(conf.workers, 1)
        self.assertEqual(conf.worker_class, "gthread")
        self.assertGreater(conf.threads, 1,
                           "one thread would block /healthz behind a "
                           "90-second generation")

    def test_the_config_binds_where_the_environment_says(self):
        with mock.patch.dict(os.environ, {
                inference_service.HOST_BIND_ENV: "0.0.0.0",
                inference_service.PORT_ENV: "9001"}):
            self.assertEqual(self.gunicorn_conf().bind, "0.0.0.0:9001")

    def test_gunicorns_own_access_log_is_off(self):
        # The app logs every request itself, with a request id and
        # without the prompt. A second copy is redundant at best.
        self.assertIsNone(self.gunicorn_conf().accesslog)

    def test_the_app_log_is_adopted_by_gunicorns_handlers(self):
        """Without this the access line is silently dropped in production.

        gunicorn configures its own handlers and leaves everyone else's
        logger bare, so "inference" falls back to lastResort, which is
        WARNING-only — a service that looks healthy and logs nothing.
        """
        parent = logging.getLogger("gunicorn.error")
        sink = logging.Handler()
        parent.addHandler(sink)
        parent.setLevel(logging.INFO)
        saved = list(inference_service.log.handlers)
        try:
            self.assertTrue(inference_service.adopt_gunicorn_logging())
            self.assertIn(sink, inference_service.log.handlers)
            self.assertEqual(inference_service.log.level, logging.INFO)
        finally:
            parent.removeHandler(sink)
            inference_service.log.handlers = saved

    def test_deployment_settings_come_from_the_environment(self):
        self.assertEqual(inference_service.bind(),
                         (inference_service.DEFAULT_BIND,
                          inference_service.DEFAULT_PORT))
        with mock.patch.dict(os.environ, {
                inference_service.HOST_BIND_ENV: "0.0.0.0",
                inference_service.PORT_ENV: "9001",
                inference_service.WORKERS_ENV: "2",
                inference_service.QUEUE_WAIT_ENV: "5.5"}):
            self.assertEqual(inference_service.bind(), ("0.0.0.0", 9001))
            self.assertEqual(inference_service.workers(), 2)
            self.assertEqual(inference_service.queue_wait(), 5.5)

    def test_a_mistyped_setting_is_a_startup_failure_not_a_default(self):
        # Somebody set it because they meant something by it.
        for name, value in ((inference_service.WORKERS_ENV, "two"),
                            (inference_service.WORKERS_ENV, "0"),
                            (inference_service.PORT_ENV, "-1"),
                            (inference_service.QUEUE_WAIT_ENV, "soon")):
            with self.subTest(f"{name}={value}"), \
                 mock.patch.dict(os.environ, {name: value}):
                with self.assertRaises(inference_service.Misconfigured):
                    inference_service.bind()
                    inference_service.workers()
                    inference_service.queue_wait()

    def test_binding_beyond_loopback_is_said_out_loud_at_startup(self):
        with mock.patch.dict(os.environ, {
                inference_service.HOST_BIND_ENV: "0.0.0.0"}):
            report = " ".join(inference_service.startup_report())
        self.assertIn("beyond loopback", report)
        self.assertIn("bearer token is the only thing", report)

    def test_keep_alive_is_configurable_and_still_defaults_to_measured(self):
        self.assertEqual(inference.keep_alive(), "30s")
        self.assertEqual(inference.DEFAULT_KEEP_ALIVE, "30s")
        with mock.patch.dict(os.environ,
                             {inference.KEEP_ALIVE_ENV: "10m"}):
            self.assertEqual(inference.keep_alive(), "10m")
            sent = {}
            with mock.patch.object(inference, "_post",
                                   lambda url, body, t, h=None: sent.update(body)
                                   or (200, {"response": "{}"})):
                inference.LocalOllama("http://x/api/generate").generate(
                    "m", "p", SCHEMA, 10)
            self.assertEqual(sent["keep_alive"], "10m")


class TestConcurrentRequests(unittest.TestCase):
    """Accept concurrently, run the model one at a time."""

    def post(self, url, body=None, token=TOKEN, timeout=30):
        """Returns (status, parsed body, headers)."""
        request = urllib.request.Request(
            url + "/v1/generate",
            json.dumps(body or {"model": "m", "prompt": "p",
                                "schema": SCHEMA}).encode(),
            {"Content-Type": "application/json",
             "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                return reply.status, json.loads(reply.read()), reply.headers
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read()), exc.headers

    def test_eight_concurrent_requests_never_load_two_models(self):
        runtime = CountingRuntime(delay=0.25)
        results = []
        with service(runtime, slots=1) as (url, _r):
            threads = [threading.Thread(
                target=lambda: results.append(self.post(url)[0]))
                for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
        self.assertEqual(results, [200] * 8)
        self.assertEqual(len(runtime.calls), 8)
        self.assertEqual(runtime.peak, 1,
                         f"{runtime.peak} generations ran at once — the "
                         f"runtime was asked to load a second model")

    def test_the_slot_count_is_configurable_and_is_the_ceiling(self):
        runtime = CountingRuntime(delay=0.3)
        with service(runtime, slots=3) as (url, _r):
            threads = [threading.Thread(target=lambda: self.post(url))
                       for _ in range(9)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
        self.assertEqual(len(runtime.calls), 9)
        self.assertLessEqual(runtime.peak, 3)

    def test_a_request_that_cannot_get_a_slot_is_refused_explicitly(self):
        # Not queued forever and not silently dropped: a category, a
        # status and a Retry-After.
        runtime = CountingRuntime(delay=2.0)
        app = inference_service.create_app(runtime, accepted={TOKEN}, slots=1)
        app.config["QUEUE_WAIT"] = 0.15
        with serving(app) as url:
            first = threading.Thread(target=lambda: self.post(url))
            first.start()
            time.sleep(0.3)
            status, body, headers = self.post(url)
            first.join(timeout=30)
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["category"], "model_busy")
        self.assertIn("busy for", body["error"]["message"])
        self.assertEqual(headers["Retry-After"], "0")
        self.assertEqual(len(runtime.calls), 1,
                         "the refused request still reached the model")

    def test_a_busy_service_routes_like_an_absent_model_not_a_broken_one(self):
        """make_profile's local-first engine should spend its Gemini call.

        The model exists; this caller cannot have it right now. That is
        the same thing to a caller as a laptop with no model on it, and
        failing the user's upload over a queue is the wrong answer.
        """
        with raw_service(503, {"error": {"category": "model_busy",
                                         "message": "all 1 slot(s) busy"}}
                         ) as url:
            with self.assertRaises(inference.ModelUnavailable):
                client(url).generate("m", "p", SCHEMA, 10)

    def test_healthz_answers_while_a_generation_holds_the_slot(self):
        # The reason to accept concurrently at all. One thread would put
        # the health probe behind a 90-second generation.
        runtime = CountingRuntime(delay=1.5)
        with service(runtime, slots=1) as (url, _r):
            busy = threading.Thread(target=lambda: self.post(url))
            busy.start()
            time.sleep(0.3)
            started = time.time()
            with urllib.request.urlopen(url + "/healthz", timeout=10) as reply:
                body = json.loads(reply.read())
            elapsed = time.time() - started
            busy.join(timeout=30)
        self.assertLess(elapsed, 1.0, "/healthz queued behind the model")
        self.assertEqual(body["model_slots"], 1)

    def test_an_auth_failure_answers_while_the_model_is_busy(self):
        runtime = CountingRuntime(delay=1.5)
        with service(runtime, slots=1) as (url, _r):
            busy = threading.Thread(target=lambda: self.post(url))
            busy.start()
            time.sleep(0.3)
            started = time.time()
            status, _body, _h = self.post(url, token="wrong")
            elapsed = time.time() - started
            busy.join(timeout=30)
        self.assertEqual(status, 401)
        self.assertLess(elapsed, 1.0,
                        "an unauthenticated request waited for a model slot")

    def test_the_slot_is_released_on_every_failure_path(self):
        # A slot leaked on an error is a service that answers model_busy
        # forever and needs a restart to recover.
        for error in (inference.ModelUnavailable("no runtime"),
                      inference.ModelTimeout("too slow"),
                      inference.BadModelOutput("not json"),
                      ZeroDivisionError("boom")):
            with self.subTest(type(error).__name__):
                logging.disable(logging.CRITICAL)
                try:
                    app = inference_service.create_app(
                        FakeRuntime(raises=error), accepted={TOKEN}, slots=1)
                    app.config["TESTING"] = True
                    app.test_client().post(
                        "/v1/generate",
                        json={"model": "m", "prompt": "p", "schema": SCHEMA},
                        headers={"Authorization": f"Bearer {TOKEN}"})
                finally:
                    logging.disable(logging.NOTSET)
                self.assertTrue(app.config["MODEL_SLOT"].acquire(timeout=0),
                                "the model slot leaked")
                app.config["MODEL_SLOT"].release()

    def test_the_semaphore_is_bounded_so_a_release_bug_is_loud(self):
        app = inference_service.create_app(FakeRuntime(), accepted={TOKEN},
                                           slots=1)
        with self.assertRaises(ValueError):
            app.config["MODEL_SLOT"].release()


class TestGracefulModelUnavailable(unittest.TestCase):
    """A runtime that is down, comes up, or goes away mid-life."""

    def test_the_service_starts_with_no_runtime_and_says_so(self):
        # A warning, not a refusal: the runtime is a separate process with
        # its own restart, and refusing to start would turn "Ollama is slow
        # to come up" into an outage that needs a human.
        with mock.patch.dict(os.environ, {
                inference.HOST_ENV: "http://127.0.0.1:11544",
                inference.TOKEN_ENV: TOKEN}):
            report = " ".join(inference_service.startup_report())
            app = inference_service.create_app()
        self.assertIn("no runtime answering", report)
        self.assertIn("model_unavailable", report)
        self.assertIsNotNone(app)

    def test_a_dead_runtime_is_degraded_not_crashed(self):
        with mock.patch.dict(os.environ,
                             {inference.HOST_ENV: "http://127.0.0.1:11544"}):
            with service() as (url, _runtime):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(url + "/healthz", timeout=10)
                with caught.exception as exc:
                    self.assertEqual(exc.code, 503)
                    body = json.loads(exc.read())
        self.assertEqual(body["status"], "degraded")
        self.assertFalse(body["runtime_reachable"])

    def test_the_three_model_failures_stay_distinguishable(self):
        """Model trouble, malformed request and internal error must never
        collapse into one another — they route differently."""
        cases = [
            (FakeRuntime(raises=inference.ModelUnavailable("no runtime")),
             None, 503, "model_unavailable"),
            (FakeRuntime(raises=inference.ModelTimeout("too slow")),
             None, 504, "model_timeout"),
            (FakeRuntime(raises=inference.BadModelOutput("not json")),
             None, 502, "bad_model_output"),
            (FakeRuntime(), {"model": "m", "prompt": "p", "schema": SCHEMA,
                             "stream": True}, 400, "bad_request"),
            (FakeRuntime(raises=ZeroDivisionError("boom")), None, 500,
             "internal_error"),
        ]
        logging.disable(logging.CRITICAL)
        try:
            for runtime, body, status, category in cases:
                with self.subTest(category):
                    app = inference_service.create_app(runtime,
                                                       accepted={TOKEN})
                    app.config["TESTING"] = True
                    reply = app.test_client().post(
                        "/v1/generate",
                        json=body or {"model": "m", "prompt": "p",
                                      "schema": SCHEMA},
                        headers={"Authorization": f"Bearer {TOKEN}"})
                    self.assertEqual(reply.status_code, status)
                    self.assertEqual(reply.get_json()["error"]["category"],
                                     category)
        finally:
            logging.disable(logging.NOTSET)

    def test_a_runtime_that_comes_back_is_served_again(self):
        # Nothing is cached, latched or circuit-broken: the next request
        # after a recovery just works.
        runtime = FakeRuntime(raises=inference.ModelUnavailable("down"))
        with service(runtime) as (url, _r):
            with self.assertRaises(inference.ModelUnavailable):
                client(url).generate("m", "p", SCHEMA, 10)
            runtime.raises = None
            self.assertEqual(client(url).generate("m", "p", SCHEMA, 10),
                             ANSWER)


class TestNoSensitiveLogging(unittest.TestCase):
    """The service must be un-incriminating to run.

    Nothing is persisted, and the log carries request id, model, duration,
    outcome and sizes — never the prompt, never the model's answer.
    """

    SECRET = "Ada Okonkwo ada.okonkwo@example.com +44 7700 900123 Fettle"

    def body(self):
        return {"model": "qwen3:8b",
                "prompt": local_extract.FIELDS_PROMPT.format(text=self.SECRET),
                "schema": local_extract.FIELDS_SCHEMA}

    def assert_clean(self, records, also=()):
        text = "\n".join(
            f"{r.getMessage()} {r.exc_text or ''}" for r in records)
        for needle in ("Okonkwo", "ada.okonkwo", "900123", "Fettle",
                       "Extract structured data", *also):
            self.assertNotIn(needle, text,
                             f"{needle!r} reached the log:\n{text}")
        return text

    def run_once(self, runtime):
        with captured_logs() as records:
            app = inference_service.create_app(runtime, accepted={TOKEN})
            app.config["TESTING"] = True
            reply = app.test_client().post(
                "/v1/generate", json=self.body(),
                headers={"Authorization": f"Bearer {TOKEN}"})
        return reply, records

    def test_a_successful_request_logs_metadata_and_nothing_else(self):
        answer = {"name": "Ada Okonkwo", "skills": ["python"],
                  "companies": ["Fettle Health"]}
        reply, records = self.run_once(FakeRuntime(answer))
        self.assertEqual(reply.status_code, 200)
        text = self.assert_clean(records)
        # The metadata that must be there, so this cannot pass by logging
        # nothing at all.
        self.assertIn("request_id=", text)
        self.assertIn("model=qwen3:8b", text)
        self.assertIn("duration_ms=", text)
        self.assertIn("outcome=ok", text)
        self.assertIn("status=200", text)
        # The size is fine — it reconstructs nothing.
        self.assertIn("prompt_bytes=", text)

    def test_no_failure_path_logs_the_prompt_or_the_answer(self):
        cases = [
            FakeRuntime(raises=inference.ModelUnavailable("no runtime")),
            FakeRuntime(raises=inference.ModelTimeout("too slow")),
            FakeRuntime(raises=inference.BadModelOutput("not json")),
            FakeRuntime(raises=ZeroDivisionError(
                f"failed on: {SECRET_IN_EXC}")),
        ]
        for runtime in cases:
            with self.subTest(runtime.raises and type(runtime.raises).__name__):
                _reply, records = self.run_once(runtime)
                text = self.assert_clean(records)
                self.assertIn("outcome=", text)

    def test_an_unhandled_crash_logs_a_traceback_but_returns_nothing(self):
        """The traceback is the operator's; the response is the caller's.

        A crash MUST be logged in full or nobody can fix it — so this
        asserts the separation rather than pretending the log is clean:
        the request id is in both, the detail only in the log.
        """
        boom = ZeroDivisionError("internal detail")
        reply, records = self.run_once(FakeRuntime(raises=boom))
        self.assertEqual(reply.status_code, 500)
        self.assertEqual(reply.get_json()["error"]["category"],
                         "internal_error")
        self.assertNotIn("internal detail", reply.get_data(as_text=True))
        logged = "\n".join(r.getMessage() for r in records)
        self.assertIn("ZeroDivisionError", logged)
        # The frames survive so the WHERE is intact...
        self.assertIn("inference_service.py", logged)
        # ...but the message does not, because that is where a library
        # puts the thing it choked on.
        self.assertNotIn("internal detail", logged)
        # ... and the prompt is in neither.
        self.assert_clean(records)
        self.assertNotIn("Okonkwo", reply.get_data(as_text=True))

    def test_the_token_is_never_logged(self):
        _reply, records = self.run_once(FakeRuntime())
        self.assertNotIn(TOKEN,
                         "\n".join(r.getMessage() for r in records))

    def test_a_rejected_token_is_not_logged_either(self):
        with captured_logs() as records:
            app = inference_service.create_app(FakeRuntime(),
                                               accepted={TOKEN})
            app.config["TESTING"] = True
            app.test_client().post(
                "/v1/generate", json=self.body(),
                headers={"Authorization": "Bearer hunter2-leaked-secret"})
        text = self.assert_clean(records, also=("hunter2",))
        self.assertIn("outcome=unauthorized", text)

    def test_nothing_is_written_to_disk(self):
        """Stateless means stateless: no cache, no spool, no audit file."""
        import tempfile

        before = set(os.listdir(tempfile.gettempdir()))
        answer = {"name": "Ada Okonkwo", "skills": ["python"]}
        with service(FakeRuntime(answer)) as (url, _r):
            client(url).generate("qwen3:8b",
                                 local_extract.FIELDS_PROMPT.format(
                                     text=self.SECRET),
                                 local_extract.FIELDS_SCHEMA, 10)
        new = set(os.listdir(tempfile.gettempdir())) - before
        for name in new:
            self.assertNotIn("inference", name.lower())
            self.assertNotIn("sweep", name.lower())

    def test_the_service_source_never_reads_a_prompt_into_a_log_call(self):
        # A grep, deliberately: the next person to add a log line should
        # trip this rather than a reviewer.
        with open(inference_service.__file__, encoding="utf-8") as fh:
            source = fh.read()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("log.info", "log.warning", "log.debug",
                                    "log.error", "log.exception")):
                self.assertNotIn("prompt", stripped.replace("prompt_bytes", ""),
                                 f"a log call names the prompt: {stripped}")
                self.assertNotIn("result", stripped)


SECRET_IN_EXC = "Ada Okonkwo ada.okonkwo@example.com"


if __name__ == "__main__":
    unittest.main()
