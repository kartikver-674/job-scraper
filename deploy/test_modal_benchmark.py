"""Offline endpoint/lifecycle checks; no Modal API calls or real model loads."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
import warnings
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "deploy"))
import inference
from deploy import run_modal_benchmark as driver


class RestoreGate(unittest.TestCase):
    def test_restored_boot_attribute_is_not_mistaken_for_a_new_container(self):
        prime = {"boot_id": "captured", "instance_id": "creator", "digest": "same"}
        restored = {**prime, "instance_id": "new-container"}
        with patch.object(driver, "check_identity"):
            driver.check_restored(prime, restored)
            with self.assertRaises(driver.GateError):
                driver.check_restored(prime, prime)
            with self.assertRaises(driver.GateError):
                driver.check_restored(prime, {**restored, "boot_id": "new-boot"})

    def test_only_matching_source_hashes_and_gpu_residency_pass(self):
        import hashlib
        info = {"ollama_version": "0.34.0", "model": "qwen3:8b",
                "digest": "500a1f067a9f" + "0" * 52, "quantization": "Q4_K_M",
                "size_vram": 5274117078, "keep_alive": "30s",
                "sources": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                            for name in driver.SOURCE_FILES}}
        driver.check_identity(info)
        for change in ({"keep_alive": -1}, {"size_vram": 0}, {"sources": {}},
                       {"ollama_version": "0.33.0"}):
            with self.subTest(change=change), self.assertRaises(driver.GateError):
                driver.check_identity({**info, **change})


@unittest.skipUnless(importlib.util.find_spec("modal"), "install the benchmark Modal SDK")
class EndpointContract(unittest.TestCase):
    """The full WSGI contract, run against BOTH apps.

    Production is only acceptable if it is the wrapper the answer-key gate
    measured. Running the identical contract against both decorated classes
    is what holds that — a drift in either one fails here.
    """

    def setUp(self):
        import modal_serving
        self.serving = modal_serving

    def endpoints(self):
        import modal_benchmark
        import modal_production
        return (("benchmark", modal_benchmark.BenchmarkEndpoint),
                ("production", modal_production.ProductionEndpoint))

    def test_real_wsgi_auth_limits_errors_and_warmup_options(self):
        for label, endpoint in self.endpoints():
            with self.subTest(app=label):
                self.check_contract(endpoint)

    def check_contract(self, endpoint):
        module = self.serving
        seen = []

        def wire(url, body, timeout, headers=None):
            seen.append(body)
            return 200, {"response": json.dumps({"value": "ok"})}

        info = {"ollama_version": "0.34.0", "model": "qwen3:8b",
                "digest": "500a1f067a9f" + "0" * 52, "quantization": "Q4_K_M",
                "size_vram": 5274117078}
        with patch.dict(os.environ, {"SWEEP_INFERENCE_TOKEN": "test-secret"}), \
                patch.object(module, "start_ollama", return_value=Mock()), \
                patch.object(module, "model_identity", return_value=info), \
                patch.object(inference, "_post", side_effect=wire), \
                warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)  # .local has no Volume
            handle = endpoint()
            app = handle.web.local()
            client = app.test_client()
            payload = {"prompt": "fixture", "schema": {"type": "object"}}
            auth = {"Authorization": "Bearer test-secret"}
            self.assertEqual(client.post("/v1/generate", json=payload).status_code, 401)
            self.assertEqual(len(seen), 1, "only synthetic warmup should run before auth")
            response = client.post("/v1/generate", json=payload, headers=auth)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["result"], {"value": "ok"})
            self.assertEqual(seen[0]["keep_alive"], -1)
            self.assertEqual(seen[1]["keep_alive"], "30s")
            self.assertTrue(all(b["think"] is False for b in seen))
            self.assertTrue(all(b["options"]["temperature"] == 0 for b in seen))
            self.assertEqual(client.post("/v1/generate", json={**payload, "extra": 1},
                                         headers=auth).json["error"]["category"], "bad_request")
            big = {**payload, "prompt": "x" * (inference.MAX_PROMPT_BYTES + 1)}
            self.assertEqual(client.post("/v1/generate", json=big, headers=auth).status_code, 413)
            with patch.object(inference, "_post", side_effect=inference.ModelUnavailable("offline")):
                response = client.post("/v1/generate", json=payload, headers=auth)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json["error"]["category"], "model_unavailable")
            self.assertEqual({r.rule for r in app.url_map.iter_rules()} - {"/static/<path:filename>"},
                             {"/healthz", "/v1/generate"})
            # A live request owns the one semaphore; another must get capacity,
            # not start a second model generation or lose the Retry-After header.
            entered, release = threading.Event(), threading.Event()

            def busy(*args, **kwargs):
                entered.set()
                release.wait(3)
                return {"value": "model-output-marker"}

            app.config["QUEUE_WAIT"] = 0.02
            sensitive = {**payload, "prompt": "private-resume-marker"}
            with patch.object(app.config["RUNTIME"], "generate", side_effect=busy), \
                    ThreadPoolExecutor(max_workers=1) as pool, \
                    self.assertLogs("inference", level="INFO") as logs:
                future = pool.submit(lambda: app.test_client().post(
                    "/v1/generate", json=sensitive, headers=auth))
                self.assertTrue(entered.wait(2))
                try:
                    second = client.post("/v1/generate", json=sensitive, headers=auth)
                    self.assertEqual(second.status_code, 429)
                    self.assertEqual(second.json["error"]["category"], "model_busy")
                    self.assertIn("Retry-After", second.headers)
                finally:
                    release.set()
                self.assertEqual(future.result().status_code, 200)
            logged = "\n".join(logs.output)
            for forbidden in ("private-resume-marker", "model-output-marker", "test-secret"):
                self.assertNotIn(forbidden, logged)

    def test_production_is_its_own_app_secret_and_warm_window(self):
        import modal_benchmark
        import modal_production
        self.assertNotEqual(modal_production.APP_NAME, modal_benchmark.APP_NAME)
        self.assertNotEqual(modal_production.SECRET_NAME, modal_benchmark.SECRET_NAME)
        self.assertEqual(modal_production.SCALEDOWN_WINDOW, 60)
        self.assertEqual(modal_production.MAX_CONTAINERS, 1)
        self.assertEqual(driver.TARGETS["production"]["app"], modal_production.APP_NAME)
        options = self.serving.serving_options(modal_production.SECRET_NAME, 60, 1)
        self.assertEqual(options["gpu"], "T4")
        self.assertEqual(options["min_containers"], 0, "production must scale to zero")
        self.assertEqual(options["max_containers"], 1)
        self.assertEqual(options["scaledown_window"], 60)
        self.assertTrue(options["enable_memory_snapshot"])
        self.assertEqual(options["experimental_options"], {"enable_gpu_snapshot": True})
        self.assertEqual(options["timeout"], 360)

    def test_model_identity_refuses_changed_digest_or_cpu_residency(self):
        module = self.serving
        tag = {"name": "qwen3:8b", "digest": "500a1f067a9f" + "0" * 52,
               "details": {"quantization_level": "Q4_K_M"}}
        payloads = {"/api/version": {"version": "0.34.0"},
                    "/api/tags": {"models": [tag]},
                    "/api/ps": {"models": [{**tag, "size_vram": 5274117078,
                                                "size": 5274117078}]}}
        with patch.object(module, "ollama_json", side_effect=lambda path: payloads[path]):
            self.assertGreater(module.model_identity(require_vram=True)["size_vram"], 0)
            payloads["/api/ps"]["models"][0]["size_vram"] = 0
            with self.assertRaises(RuntimeError):
                module.model_identity(require_vram=True)
            tag["digest"] = "different"
            with self.assertRaises(RuntimeError):
                module.model_identity()


class Targets(unittest.TestCase):
    def test_benchmark_and_production_never_share_app_state_or_token(self):
        bench, prod = driver.TARGETS["benchmark"], driver.TARGETS["production"]
        for key in ("app", "cls", "state", "token_env"):
            self.assertNotEqual(bench[key], prod[key], key)
        # Every earlier command still means the benchmark.
        self.assertEqual(driver.APP_NAME, bench["app"])
        self.assertEqual(driver.STATE, bench["state"])


class LiveContractAgainstARealServer(unittest.TestCase):
    """contract_http run against the real service over a real socket, with
    only the model faked — so the live run cannot fail on a checker bug."""

    def serve(self, queue_wait):
        import inference_service
        import local_extract as le
        from werkzeug.serving import make_server

        answer = {key: ([] if key != "name" and key != "years_experience" else
                        ("" if key == "name" else 0)) for key in le.FIELDS_SCHEMA["required"]}

        class Runtime:
            def generate(self, model, prompt, schema, timeout):
                if timeout <= 1:
                    raise inference.ModelTimeout("slower than the deadline")
                import time
                time.sleep(0.3)
                return answer

            def describe(self):
                return "fake"

        app = inference_service.create_app(Runtime(), accepted={"tok"}, slots=1)
        app.config["QUEUE_WAIT"] = queue_wait
        server = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_every_agreed_response_is_provoked_and_recognised(self):
        import inference_service
        with patch.object(inference_service, "runtime_reachable", return_value=True):
            checks = driver.contract_http(self.serve(queue_wait=0.05), "tok", "p")
        for label in ("GET /healthz", "unknown route", "no bearer token", "wrong bearer token",
                      "unknown request field", "oversized prompt", "authenticated generation",
                      "model slower than the request deadline", "client maps model_timeout",
                      "burst answers only 200 or 429", "model_busy under contention"):
            self.assertIn(label, checks)

    def test_a_service_that_never_says_model_busy_fails_the_contract(self):
        import inference_service
        with patch.object(inference_service, "runtime_reachable", return_value=True):
            with self.assertRaisesRegex(driver.GateError, "model_busy"):
                driver.contract_http(self.serve(queue_wait=30), "tok", "p", concurrency=2)


if __name__ == "__main__":
    unittest.main()
