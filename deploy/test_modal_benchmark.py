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
    def setUp(self):
        self.assertTrue((ROOT / "deploy/modal_benchmark.py").exists(),
                        "temporary endpoint has not been implemented")
        import modal_benchmark
        self.module = modal_benchmark

    def test_real_wsgi_auth_limits_errors_and_warmup_options(self):
        module = self.module
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
            handle = module.BenchmarkEndpoint()
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

    def test_model_identity_refuses_changed_digest_or_cpu_residency(self):
        module = self.module
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


if __name__ == "__main__":
    unittest.main()
