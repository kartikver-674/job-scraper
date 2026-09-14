"""Prime, verify and contract-test a deployed Modal inference endpoint.

python deploy/run_modal_benchmark.py [--target production] prime
python deploy/run_modal_benchmark.py [--target production] smoke
python deploy/run_modal_benchmark.py [--target production] contract
python deploy/run_modal_benchmark.py [--target production] url

`--target` defaults to the benchmark app, so every earlier command still
means what it meant. State lives locally under ignored output/, one file
per target. Only these commands use Modal SDK credentials; bench.backends
uses the bearer-authenticated HTTP API. Prime and smoke never send a real
résumé; `contract` uses a synthetic benchmark document, and nothing here
prints or stores a prompt, a model answer or a token.
"""

import argparse
import concurrent.futures
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

SOURCE_FILES = ("inference.py", "inference_service.py", "local_extract.py")
# Two apps, kept apart: separate app, class, state file and token variable,
# so a benchmark command can never act on production by accident.
TARGETS = {
    "benchmark": {"app": "sweep-inference-benchmark", "cls": "BenchmarkEndpoint",
                  "state": ROOT / "output/modal-equivalence/endpoint.json",
                  "token_env": "SWEEP_BENCHMARK_TOKEN"},
    "production": {"app": "sweep-inference-production", "cls": "ProductionEndpoint",
                   "state": ROOT / "output/modal-production/endpoint.json",
                   "token_env": "SWEEP_PRODUCTION_TOKEN"},
}
APP_NAME = TARGETS["benchmark"]["app"]
STATE = TARGETS["benchmark"]["state"]


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


# A genuine GPU-snapshot restore answered in 17.35s on the benchmark app; a
# container that has to CREATE a snapshot (full boot, then the write) took
# ~150s. Sixty seconds sits cleanly between the two.
RESTORE_LIMIT_S = 60


def check_restored(prime, restored, restore_s=None):
    """A restore: a different container from the one primed, the same pinned
    model, and — when timed — fast enough to have been restored, not created.

    Deliberately NOT "the same boot_id as prime". Modal keeps one GPU
    snapshot per worker type. On the production rollout the first cold
    request landed on a second worker type and created a second snapshot
    (logged twice as 'Creating GPU memory snapshot'); the next cold request
    restored from that one in seconds — genuine, and a different boot_id.
    Requiring the primed boot_id failed a correct restore. What this check
    exists to catch is still caught: a reused warm container (same instance)
    and a boot that paid snapshot creation (too slow). Every snapshot is
    captured by the same synthetic-only boot(), so no résumé is in any of them.
    """
    check_identity(restored)
    if prime["instance_id"] == restored["instance_id"]:
        raise GateError("same container reused; wait for actual scale-down and repeat smoke")
    if prime["digest"] != restored["digest"]:
        raise GateError("model changed between prime and smoke")
    if restore_s is not None and restore_s > RESTORE_LIMIT_S:
        raise GateError(f"first request took {restore_s:.0f}s: a snapshot was being created, "
                        f"not restored (Modal keeps one per worker type); run smoke again")


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


def request(url, path, body=None, token=None, timeout=145):
    """(status, JSON body, lower-cased headers). Redirects are refused."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url + path,
                                 None if body is None else json.dumps(body).encode(), headers)
    try:
        reply = urllib.request.build_opener(NoRedirect).open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        reply = exc
    with reply:
        return (reply.code, json.load(reply),
                {k.lower(): v for k, v in reply.headers.items()})


def http(url, path, body=None, token=None):
    status, payload, _headers = request(url, path, body, token)
    return status, payload


def category(payload):
    return payload.get("error", {}).get("category") if isinstance(payload, dict) else None


def contract_http(url, token, prompt, concurrency=8):
    """Provoke every agreed response from the live endpoint. {check: detail}.

    Raises GateError at the first break. The prompt is a synthetic
    benchmark document long enough that generation outlasts a one-second
    deadline, and that eight of them outlast the thirty-second slot wait.
    """
    checks = {}

    def expect(ok, label, detail):
        if not ok:
            raise GateError(f"contract broken: {label}")
        checks[label] = detail

    body = {"model": inference.DEFAULT_MODEL, "prompt": prompt,
            "schema": le.FIELDS_SCHEMA, "timeout": 120}
    status, health = http(url, "/healthz")
    expect(status == 200 and health.get("status") == "ok"
           and health.get("runtime_reachable") is True and health.get("model_slots") == 1,
           "GET /healthz", "200 ok, runtime reachable, 1 model slot")
    status, payload = http(url, "/does-not-exist")
    expect(status == 404 and category(payload) == "not_found", "unknown route", "404 not_found")
    for label, credential in (("no bearer token", None),
                              ("wrong bearer token", "deliberately-wrong-token")):
        status, payload = http(url, "/v1/generate", body, credential)
        expect(status == 401 and category(payload) == "unauthorized", label, "401 unauthorized")
    status, payload = http(url, "/v1/generate", {**body, "unexpected": 1}, token)
    expect(status == 400 and category(payload) == "bad_request",
           "unknown request field", "400 bad_request")
    status, payload = http(url, "/v1/generate",
                           {**body, "prompt": "x" * (inference.MAX_PROMPT_BYTES + 1)}, token)
    expect(status == 413 and category(payload) == "payload_too_large",
           "oversized prompt", "413 payload_too_large")

    started = time.perf_counter()
    result = inference.RemoteService(url, token).generate(
        body["model"], prompt, le.FIELDS_SCHEMA, 120)
    expect(isinstance(result, dict) and set(le.FIELDS_SCHEMA["required"]) <= result.keys(),
           "authenticated generation",
           f"200 structured result in {time.perf_counter() - started:.2f}s")

    status, payload = http(url, "/v1/generate", {**body, "timeout": 1}, token)
    expect(status == 504 and category(payload) == "model_timeout",
           "model slower than the request deadline", "504 model_timeout")
    try:
        inference.RemoteService(url, token).generate(body["model"], prompt, le.FIELDS_SCHEMA, 1)
        mapped = "no error"
    except inference.ModelBusy:
        mapped = "ModelBusy"
    except inference.ModelUnavailable:
        mapped = "ModelUnavailable"
    except inference.RemoteServiceError:
        mapped = "RemoteServiceError"
    expect(mapped == "ModelUnavailable", "client maps model_timeout",
           "ModelUnavailable — never ModelBusy, never a silent success")

    def one(_):
        code, reply, headers = request(url, "/v1/generate", body, token)
        return code, category(reply), headers.get("retry-after")

    with concurrent.futures.ThreadPoolExecutor(concurrency) as pool:
        replies = list(pool.map(one, range(concurrency)))
    statuses = sorted(code for code, *_ in replies)
    busy = [r for r in replies if r[0] == 429]
    expect(all(code in (200, 429) for code, *_ in replies),
           "burst answers only 200 or 429", f"statuses {statuses}")
    expect(busy and all(cat == "model_busy" and ra and ra.isdigit() and int(ra) >= 1
                        for _, cat, ra in busy),
           "model_busy under contention",
           f"{len(busy)} of {concurrency} -> 429 model_busy, Retry-After "
           f"{sorted({ra for *_, ra in busy})}s")
    return checks


def contract_prompt():
    """A synthetic benchmark person, never a real résumé."""
    sys.path.insert(0, str(ROOT / "auto-apply"))
    from resume_parser import extract_text
    return le.FIELDS_PROMPT.format(text=extract_text(str(ROOT / "bench/resumes/hana-plain.pdf")))


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
    parser.add_argument("command", choices=("prime", "smoke", "contract", "url"))
    parser.add_argument("--target", choices=tuple(TARGETS), default="benchmark")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--token-env")
    args = parser.parse_args()
    target = TARGETS[args.target]
    args.state = args.state or target["state"]
    args.token_env = args.token_env or target["token_env"]
    if args.command == "url":
        state = json.loads(args.state.read_text())
        if not state.get("smoke_passed"):
            raise SystemExit("restored smoke test has not passed")
        print(state["url"])
        return

    import modal
    handle = modal.Cls.from_name(target["app"], target["cls"])()
    state = {}
    if args.command == "contract":
        state = json.loads(args.state.read_text())
        if not state.get("smoke_passed"):
            raise GateError("restored smoke test has not passed; run smoke first")
        token = os.environ.get(args.token_env)
        if not token:
            raise SystemExit(f"set {args.token_env} to the endpoint bearer token")
        state["contract_passed"] = False
        args.state.write_text(json.dumps(state, indent=2))
        checks = contract_http(state["url"], token, contract_prompt())
        after = handle.diagnostics.remote()
        check_restored(state["prime"], after)
        state.update(contract_passed=True, contract=checks)
        for label, detail in checks.items():
            print(f"  PASS  {label:40} {detail}")
    elif args.command == "prime":
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
        try:
            status, health = http(state["url"], "/healthz")
        except TimeoutError:
            state["restored_health_s"] = None
            args.state.write_text(json.dumps(state, indent=2))
            raise GateError("the first cold request outlasted 145s: a snapshot was being "
                            "created, not restored (Modal keeps one per worker type); "
                            "run smoke again") from None
        state["restored_health_s"] = round(time.perf_counter() - started, 2)
        if status != 200 or health.get("status") != "ok":
            raise GateError("restored HTTPS health check failed")
        restored = handle.diagnostics.remote()
        # Recorded before judging, so a failure still leaves the evidence.
        state["restored"] = restored
        state["restored_from"] = ("the primed snapshot"
                                  if restored["boot_id"] == state["prime"]["boot_id"]
                                  else "another worker type's snapshot")
        args.state.write_text(json.dumps(state, indent=2))
        check_restored(state["prime"], restored, state["restored_health_s"])
        smoke_http(state["url"], token)
        after = handle.diagnostics.remote()
        check_restored(state["prime"], after)
        state.update(smoke_passed=True, restored=after,
                     smoke_e2e_s=round(time.perf_counter() - started, 2))
        print(f"Restored HTTPS ready: {state['restored_health_s']}s from "
              f"{state['restored_from']}; size_vram={after['size_vram']}; "
              f"auth/schema/generation passed.")
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
