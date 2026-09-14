"""Prime and verify the temporary endpoint; never sends a real resume.

python deploy/run_modal_benchmark.py prime
python deploy/run_modal_benchmark.py smoke
python deploy/run_modal_benchmark.py url
State lives locally under ignored output/. Only these commands use Modal SDK
credentials; bench.backends uses the existing bearer-authenticated HTTP API.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import inference
import local_extract as le

APP_NAME = "sweep-inference-benchmark"
STATE = ROOT / "output/modal-equivalence/endpoint.json"
SOURCE_FILES = ("inference.py", "inference_service.py", "local_extract.py")


class GateError(RuntimeError):
    """Operator-facing checks with messages written here, never by a runtime."""


def check_identity(info):
    from deploy.ollama_probe_lib import EXPECTED_DIGEST_PREFIX, MODEL, OLLAMA_VERSION
    expected_sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                        for name in SOURCE_FILES}
    if (info.get("model") != MODEL or info.get("ollama_version") != OLLAMA_VERSION
            or not info.get("digest", "").startswith(EXPECTED_DIGEST_PREFIX)
            or info.get("quantization") != "Q4_K_M"
            or info.get("size_vram", 0) <= 0 or info.get("keep_alive") != "30s"
            or info.get("sources") != expected_sources):
        raise GateError("identity/VRAM/source/keep-alive check failed; do not benchmark")


def check_restored(prime, restored):
    check_identity(restored)
    if prime["boot_id"] != restored["boot_id"]:
        raise GateError("new snapshot creation, not restore; prime again before benchmarking")
    if prime["instance_id"] == restored["instance_id"]:
        raise GateError("same container reused; wait for actual scale-down and repeat smoke")
    if prime["digest"] != restored["digest"]:
        raise GateError("model changed between prime and smoke")


def wait_for_zero(handle, limit=180):
    deadline = time.monotonic() + limit
    print("Waiting for the temporary endpoint to have zero containers…", flush=True)
    while time.monotonic() < deadline:
        stats = handle.diagnostics.get_current_stats()
        if stats.num_total_runners == 0 and stats.num_running_inputs == 0 and stats.backlog == 0:
            return
        time.sleep(5)
    raise GateError("endpoint did not scale to zero; ensure no other benchmark is running")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # platform result-polling is not an agreed service response


def http(url, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url + path,
                                     None if body is None else json.dumps(body).encode(), headers)
    try:
        reply = urllib.request.build_opener(NoRedirect).open(request, timeout=145)
    except urllib.error.HTTPError as exc:
        reply = exc
    with reply:
        return reply.code, json.load(reply)


def smoke_http(url, token):
    status, health = http(url, "/healthz")
    if status != 200 or health.get("runtime_reachable") is not True or health.get("model_slots") != 1:
        raise GateError("HTTPS health check failed")
    body = {"model": inference.DEFAULT_MODEL, "prompt": le.FIELDS_PROMPT.format(text="warmup"),
            "schema": le.FIELDS_SCHEMA, "timeout": 120}
    for credential in (None, "deliberately-wrong-benchmark-token"):
        status, payload = http(url, "/v1/generate", body, credential)
        if status != 401 or payload.get("error", {}).get("category") != "unauthorized":
            raise GateError("bearer authentication check failed")
    status, payload = http(url, "/v1/generate", {**body, "unknown": True}, token)
    if status != 400 or payload.get("error", {}).get("category") != "bad_request":
        raise GateError("strict request schema check failed")
    # Use the actual client provider for successful traffic.
    result = inference.RemoteService(url, token).generate(
        body["model"], body["prompt"], body["schema"], body["timeout"])
    if not isinstance(result, dict) or not set(le.FIELDS_SCHEMA["required"]) <= result.keys():
        raise GateError("successful HTTP response did not contain the expected structured result")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prime", "smoke", "url"))
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--token-env", default="SWEEP_BENCHMARK_TOKEN")
    args = parser.parse_args()
    if args.command == "url":
        state = json.loads(args.state.read_text())
        if not state.get("smoke_passed"):
            raise SystemExit("restored smoke test has not passed")
        print(state["url"])
        return

    import modal
    handle = modal.Cls.from_name(APP_NAME, "BenchmarkEndpoint")()
    state = {}
    if args.command == "prime":
        print("Priming the synthetic snapshot. The first invocation may take several minutes.", flush=True)
        info = handle.diagnostics.remote()
        check_identity(info)
        state = {"url": handle.web.get_web_url(), "prime": info, "smoke_passed": False}
    else:
        state = json.loads(args.state.read_text())
        # Invalidate stale success BEFORE a new attempt, including failures.
        state["smoke_passed"] = False
        args.state.write_text(json.dumps(state, indent=2))
        token = os.environ.get(args.token_env)
        if not token:
            raise SystemExit(f"set {args.token_env} to the endpoint bearer token")
        if handle.web.get_web_url() != state["url"]:
            raise GateError("deployed URL changed; prime again")
        wait_for_zero(handle)
        started = time.perf_counter()
        # The public WSGI method must restore first, before private diagnostics
        # can accidentally warm it on behalf of the smoke test.
        status, health = http(state["url"], "/healthz")
        state["restored_health_s"] = round(time.perf_counter() - started, 2)
        if status != 200 or health.get("status") != "ok":
            raise GateError("restored HTTPS health check failed")
        restored = handle.diagnostics.remote()
        check_restored(state["prime"], restored)
        smoke_http(state["url"], token)
        after = handle.diagnostics.remote()
        check_restored(state["prime"], after)
        state.update(smoke_passed=True, restored=after,
                     smoke_e2e_s=round(time.perf_counter() - started, 2))
        print(f"Restored HTTPS ready: {state['restored_health_s']}s; "
              f"size_vram={after['size_vram']}; auth/schema/generation passed.")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state, indent=2))
    args.state.chmod(0o600)
    print(f"Saved {args.state}; URL {state['url']}")


if __name__ == "__main__":
    try:
        main()
    except GateError as exc:
        print(f"STOP: {exc}")
        sys.exit(1)
    except Exception as exc:
        # Do not echo transport exceptions that may carry credentials or bodies.
        print(f"Endpoint operation failed ({type(exc).__name__}). "
              "Check the private Modal lifecycle diagnostics; equivalence is unproven.")
        sys.exit(1)
