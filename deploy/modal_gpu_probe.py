import json
import os
import subprocess
import time
import urllib.request

import modal


OLLAMA_VERSION = "0.34.0"
MODEL = "qwen3:8b"
EXPECTED_DIGEST_PREFIX = "500a1f067a9f"

app = modal.App("sweep-ollama-gpu-probe")

model_volume = modal.Volume.from_name(
    "sweep-ollama-models",
    create_if_missing=True,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "ca-certificates", "zstd")
    .run_commands(
        "curl -fsSL "
        "'https://ollama.com/download/ollama-linux-amd64.tar.zst"
        f"?version={OLLAMA_VERSION}' "
        "| zstd -d | tar -xf - -C /usr"
    )
    # The production modules, copied in rather than reimplemented. This is
    # what makes the Modal numbers comparable to the M1 and Oracle ones:
    # the prompts, the schema, the model options, the router and the years
    # arithmetic are the SAME code, not a faithful-looking copy of it.
    # `local_extract` imports `inference` and nothing else outside stdlib.
    .add_local_file(os.path.join(REPO_ROOT, "inference.py"),
                    "/app/inference.py", copy=True)
    .add_local_file(os.path.join(REPO_ROOT, "local_extract.py"),
                    "/app/local_extract.py", copy=True)
    .env({"PYTHONPATH": "/app"})
)


def wait_for_ollama(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:11434/api/tags",
                timeout=2,
            ):
                return
        except Exception:
            time.sleep(0.5)

    raise RuntimeError("Ollama did not become ready")


def start_ollama():
    env = os.environ.copy()
    env["OLLAMA_HOST"] = "127.0.0.1:11434"

    process = subprocess.Popen(
        ["ollama", "serve"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    wait_for_ollama()
    return process


@app.function(
    image=image,
    volumes={"/root/.ollama": model_volume},
    timeout=1800,
)
def seed_model():
    process = start_ollama()

    try:
        print(
            subprocess.check_output(
                ["ollama", "--version"],
                text=True,
            ).strip()
        )

        subprocess.run(
            ["ollama", "pull", MODEL],
            check=True,
        )

        model_list = subprocess.check_output(
            ["ollama", "list"],
            text=True,
        )

        print(model_list)

        if EXPECTED_DIGEST_PREFIX not in model_list:
            raise RuntimeError(
                "qwen3:8b digest does not match Oracle baseline"
            )

        model_volume.commit()

        print("MODEL SEEDED AND COMMITTED")
        print(f"Expected digest: {EXPECTED_DIGEST_PREFIX}")

    finally:
        process.terminate()
        process.wait(timeout=10)


# The two probes below answer "does qwen3:8b reach T4 VRAM at all" with a
# two-token toy request, and they PASSED (size_vram 5,274,117,078). Their
# inline options are a hand-written stand-in and are NOT the production
# body — profile_probe further down imports the real one. Kept as the
# record of the compatibility check; do not copy their options anywhere.
@app.function(
    image=image,
    gpu="T4",
    volumes={"/root/.ollama": model_volume},
    timeout=600,
)
def gpu_probe():
    process = start_ollama()

    try:
        print(
            subprocess.check_output(
                ["ollama", "--version"],
                text=True,
            ).strip()
        )

        model_list = subprocess.check_output(
            ["ollama", "list"],
            text=True,
        )

        print(model_list)

        if EXPECTED_DIGEST_PREFIX not in model_list:
            raise RuntimeError(
                "Model digest differs from Oracle baseline"
            )

        payload = json.dumps(
            {
                "model": MODEL,
                "prompt": "Reply only with READY.",
                "stream": False,
                "think": False,
                "keep_alive": -1,
                "options": {
                    "temperature": 0,
                    "num_ctx": 2048,
                },
            }
        ).encode()

        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        started = time.perf_counter()

        with urllib.request.urlopen(request, timeout=300) as response:
            generation = json.load(response)

        elapsed = time.perf_counter() - started

        print(f"Generation wall time: {elapsed:.2f}s")
        print(f"Response: {generation.get('response', '').strip()!r}")
        print(
            "eval_count:",
            generation.get("eval_count"),
            "eval_duration:",
            generation.get("eval_duration"),
        )

        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/ps",
            timeout=10,
        ) as response:
            ps = json.load(response)

        print("OLLAMA PS:")
        print(json.dumps(ps, indent=2))

        models = ps.get("models", [])

        if not models:
            raise RuntimeError("qwen3:8b is not loaded")

        loaded = models[0]
        size_vram = loaded.get("size_vram", 0)

        print()
        print(f"size_vram = {size_vram:,} bytes")

        if size_vram <= 0:
            raise RuntimeError(
                "FAIL: Ollama loaded qwen3:8b on CPU, not GPU"
            )

        print("PASS: qwen3:8b is resident in GPU VRAM")

        return {
            "size": loaded.get("size"),
            "size_vram": size_vram,
            "context_length": loaded.get("context_length"),
            "generation_seconds": elapsed,
            "eval_count": generation.get("eval_count"),
            "eval_duration": generation.get("eval_duration"),
        }

    finally:
        process.terminate()
        process.wait(timeout=10)

@app.function(
    image=image,
    gpu="T4",
    volumes={"/root/.ollama": model_volume},
    timeout=600,
)
def gpu_warm_probe():
    process = start_ollama()

    try:
        def generate(label):
            payload = json.dumps(
                {
                    "model": MODEL,
                    "prompt": "Reply only with READY.",
                    "stream": False,
                    "think": False,
                    "keep_alive": -1,
                    "options": {
                        "temperature": 0,
                        "num_ctx": 2048,
                    },
                }
            ).encode()

            request = urllib.request.Request(
                "http://127.0.0.1:11434/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )

            started = time.perf_counter()

            with urllib.request.urlopen(request, timeout=300) as response:
                result = json.load(response)

            wall = time.perf_counter() - started

            load_s = result.get("load_duration", 0) / 1e9
            prompt_s = result.get("prompt_eval_duration", 0) / 1e9
            eval_s = result.get("eval_duration", 0) / 1e9

            eval_count = result.get("eval_count", 0)

            tok_s = (
                eval_count / eval_s
                if eval_s > 0
                else 0
            )

            print()
            print(f"{label}")
            print(f"wall:        {wall:.2f}s")
            print(f"load:        {load_s:.2f}s")
            print(f"prompt eval: {prompt_s:.2f}s")
            print(f"generation:  {eval_s:.2f}s")
            print(f"output:      {eval_count} tokens")
            print(f"speed:       {tok_s:.1f} tok/s")
            print(
                f"response:    {result.get('response', '').strip()!r}"
            )

            return wall

        cold = generate("FIRST REQUEST — COLD")
        warm = generate("SECOND REQUEST — WARM")

        print()
        print("SUMMARY")
        print(f"cold request: {cold:.2f}s")
        print(f"warm request: {warm:.2f}s")

        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/ps",
            timeout=10,
        ) as response:
            ps = json.load(response)

        print()
        print("OLLAMA PS:")
        print(json.dumps(ps, indent=2))

    finally:
        process.terminate()
        process.wait(timeout=10)

# ==========================================================================
# The real production profile, cold then warm, in one container
# ==========================================================================
#
# The GPU probe above proved qwen3:8b reaches T4 VRAM. What it measured was
# a two-token toy request: 111.51s cold (64.09s load + 47.34s prompt eval)
# and 0.10s warm. Neither number says what a RÉSUMÉ costs, because a
# two-token prompt barely exercises prefill and not at all the second call.
#
# This runs the actual extraction — both production calls, the router and
# the years arithmetic — against the same document already measured at
# 37.4s on an M1 Pro and 186.9s on Oracle's 2 ARM cores.
#
# NOTHING HERE RESTATES PRODUCTION BEHAVIOUR
# -----------------------------------------
# The prompts, the schema, `think: false`, `temperature: 0`, `num_ctx` from
# ctx_for(), the keep-alive and the JSON parsing all come from the imported
# modules. The only thing added is a recorder that remembers what the
# runtime reported for each call. If this file and production ever
# disagree, this file is wrong by construction — it has no opinions to be
# wrong with.
#
# RUN IT THROUGH THE LOCAL ENTRYPOINT, not the function:
#
#     modal run deploy/modal_gpu_probe.py
#     modal run deploy/modal_gpu_probe.py --resume "/path/to/other.pdf"
#
# `modal run ...::profile_probe` targets the remote function directly, which
# skips main() and so skips parsing the PDF — it would ask for --resume-text
# and expect you to paste a résumé onto the command line. The entrypoint is
# what extracts the text locally with the production parser.


def _metrics_row(label, wall, num_ctx, prompt_chars, metrics):
    """One call's numbers. Counts and durations only — never text."""
    m = metrics or {}

    def seconds(key):
        return (m.get(key) or 0) / 1e9

    prompt_tokens = m.get("prompt_eval_count")
    out_tokens = m.get("eval_count")
    prefill, generate = seconds("prompt_eval_duration"), seconds("eval_duration")
    return {
        "call": label,
        "wall_s": round(wall, 2),
        "load_s": round(seconds("load_duration"), 2),
        "prompt_eval_count": prompt_tokens,
        "prompt_eval_s": round(prefill, 2),
        "prompt_tok_s": round(prompt_tokens / prefill, 1)
                        if prompt_tokens and prefill else None,
        "eval_count": out_tokens,
        "eval_s": round(generate, 2),
        "eval_tok_s": round(out_tokens / generate, 1)
                      if out_tokens and generate else None,
        "total_s": round(seconds("total_duration"), 2),
        "num_ctx": num_ctx,
        "prompt_chars": prompt_chars,
    }


def _show(row):
    def cell(value, width, suffix=""):
        return (f"{value}{suffix}" if value is not None else "—").rjust(width)

    print(f"  {row['call']:11} wall {row['wall_s']:8.2f}s   "
          f"load {row['load_s']:7.2f}s   "
          f"prefill {cell(row['prompt_eval_count'], 5)}tok in "
          f"{row['prompt_eval_s']:6.2f}s @ {cell(row['prompt_tok_s'], 7)} tok/s   "
          f"gen {cell(row['eval_count'], 4)}tok in {row['eval_s']:6.2f}s @ "
          f"{cell(row['eval_tok_s'], 6)} tok/s   ctx {row['num_ctx']}",
          flush=True)


@app.function(
    image=image,
    gpu="T4",
    volumes={"/root/.ollama": model_volume},
    timeout=1800,
)
def profile_probe(resume_text: str):
    """Both production calls, cold then warm, in one container.

    `resume_text` is extracted on the caller's machine by the production
    parser and passed in. It is never written to disk here and never
    printed — only its length and the resulting token counts are reported.
    """
    import sys

    sys.path.insert(0, "/app")

    container_started = time.perf_counter()
    process = start_ollama()
    ollama_ready_s = time.perf_counter() - container_started

    try:
        import inference
        import local_extract as le

        # Pin the model for the life of the container, exactly as the
        # Oracle deployment does. Read by inference.keep_alive().
        os.environ["SWEEP_MODEL_KEEP_ALIVE"] = "-1"
        os.environ["OLLAMA_HOST"] = "http://127.0.0.1:11434"

        captured = []

        class Recording(inference.LocalOllama):
            """The production transport, plus a notebook.

            Subclassed rather than reimplemented: generate() is the real
            one, so the request body is the production body — think
            false, temperature 0, ctx_for's num_ctx, the lot.
            """

            def generate(self, model, prompt, schema, timeout):
                started = time.perf_counter()
                try:
                    return super().generate(model, prompt, schema, timeout)
                finally:
                    captured.append({
                        "wall": time.perf_counter() - started,
                        "num_ctx": inference.ctx_for(prompt),
                        "prompt_chars": len(prompt),
                        "metrics": self.last_metrics,
                    })

        # Every call local_extract makes now goes through the recorder.
        # Patched rather than parameterised so that le.read() — the real
        # entry point, router and arithmetic included — runs untouched.
        inference.provider = lambda backend=None, **kw: Recording(**kw)

        model = inference.model_name()
        print(f"\nmodel {model}   résumé {len(resume_text)} chars   "
              f"ollama ready in {ollama_ready_s:.2f}s", flush=True)

        def run(label):
            captured.clear()
            started = time.perf_counter()
            checked, rows, decision = le.read(model, resume_text)
            total = time.perf_counter() - started
            names = ("fields", "employment")
            out = [_metrics_row(names[i], c["wall"], c["num_ctx"],
                                c["prompt_chars"], c["metrics"])
                   for i, c in enumerate(captured)]
            print(f"\n{label}")
            for row in out:
                _show(row)
            print(f"  {'PROFILE':11} {total:8.2f}s total", flush=True)
            if out[0]["num_ctx"] != out[1]["num_ctx"]:
                print(f"  !! the two calls asked for different context sizes "
                      f"({out[0]['num_ctx']} vs {out[1]['num_ctx']}) — Ollama "
                      f"must RELOAD the model between them", flush=True)
            # Proof the extraction actually worked, without printing the
            # résumé or the model's answer: the decision and the derived
            # number, which is what Sweep consumes.
            print(f"  decision={decision['decision']}  "
                  f"corrections={len(decision['corrections'])}  "
                  f"years={(checked or {}).get('years_experience')}  "
                  f"rows={len((rows or {}).get('employment') or [])}",
                  flush=True)
            return out, total

        cold_rows, cold_total = run("COLD — first profile in a fresh container")
        warm_rows, warm_total = run("WARM — same profile, same container")

        # READ THE WARM NUMBER CAREFULLY. Repeating the identical prompt
        # hits Ollama's prefix cache: measured on an M1, prefill drops from
        # 5.14s to 0.06s — 19,500 tok/s is a cache read, not a GPU. So
        # "warm" here is the BEST case and does not predict what a second
        # user with a different résumé pays.
        #
        # The realistic steady state is already in the cold run: its SECOND
        # call has load_duration ≈ 0 (model resident) and a full, uncached
        # prefill. That row — not the warm total — is what a later request
        # looks like.
        steady = cold_rows[1]
        print(f"\n  steady-state reference (cold run's 2nd call: model "
              f"resident, prompt NOT cached): {steady['wall_s']:.2f}s "
              f"— prefill {steady['prompt_eval_s']:.2f}s @ "
              f"{steady['prompt_tok_s']} tok/s, "
              f"gen {steady['eval_s']:.2f}s @ {steady['eval_tok_s']} tok/s",
              flush=True)

        # --- where did the cold time go? ---------------------------------
        # Reload the model with the page cache now warm and CUDA already
        # initialised. The difference between this load_duration and the
        # cold one separates "reading the GGUF" from "one-time init".
        print("\nATTRIBUTION — reloading with a warm page cache", flush=True)
        unload = json.dumps({"model": MODEL, "keep_alive": 0}).encode()
        urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:11434/api/generate", data=unload,
            headers={"Content-Type": "application/json"}), timeout=300).read()
        time.sleep(3)

        captured.clear()
        le.extract(model, resume_text)
        reload_row = _metrics_row("reload", captured[0]["wall"],
                                  captured[0]["num_ctx"],
                                  captured[0]["prompt_chars"],
                                  captured[0]["metrics"])
        _show(reload_row)

        # How fast can this container read the weights at all? Measured
        # after the fact so it cannot warm the cache before the cold load.
        blob_bytes, blob_read_s = _time_blob_read()

        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps",
                                    timeout=10) as response:
            ps = json.load(response)
        resident = (ps.get("models") or [{}])[0]

        summary = {
            "ollama_ready_s": round(ollama_ready_s, 2),
            "resume_chars": len(resume_text),
            "cold": {"calls": cold_rows, "total_s": round(cold_total, 2)},
            "warm": {"calls": warm_rows, "total_s": round(warm_total, 2)},
            "reload_warm_cache": reload_row,
            "volume_blob_bytes": blob_bytes,
            "volume_read_s": round(blob_read_s, 2) if blob_read_s else None,
            "volume_read_gbps": round(blob_bytes / blob_read_s / 1e9, 2)
                                if blob_bytes and blob_read_s else None,
            "size_vram": resident.get("size_vram"),
            "context_length": resident.get("context_length"),
            # The cold run's second call: model resident, prompt uncached.
            "steady_state_call": steady,
            "baselines_for_comparison": {
                "m1_pro_total_s": 37.4, "oracle_a1_total_s": 186.9},
        }

        print("\n" + "=" * 78)
        print(f"  ollama ready            {summary['ollama_ready_s']:8.2f}s")
        print(f"  COLD profile            {summary['cold']['total_s']:8.2f}s"
              f"   (M1 Pro 37.4s, Oracle A1 186.9s)")
        print(f"  WARM profile            {summary['warm']['total_s']:8.2f}s"
              f"   (prompt cache hit — best case, not a 2nd user)")
        print(f"  cold first-call load    {cold_rows[0]['load_s']:8.2f}s")
        print(f"  reload, warm page cache {reload_row['load_s']:8.2f}s")
        if summary["volume_read_gbps"]:
            print(f"  volume read             "
                  f"{summary['volume_read_s']:8.2f}s for "
                  f"{blob_bytes/1e9:.2f}GB = "
                  f"{summary['volume_read_gbps']}GB/s")
        print(f"  size_vram               {summary['size_vram']:,}")
        print("=" * 78, flush=True)
        return summary

    finally:
        process.terminate()
        process.wait(timeout=10)


def _time_blob_read():
    """(bytes, seconds) to read the largest model blob off the Volume.

    Run AFTER the cold load so it cannot warm the page cache first. The
    page cache is warm by then, so this is a LOWER bound on read time —
    an upper bound on Volume throughput — which is exactly the direction
    that makes the attribution argument safe: if even a warm read takes a
    material fraction of load_duration, the disk is implicated.
    """
    blobs = "/root/.ollama/models/blobs"
    try:
        largest, size = None, 0
        for name in os.listdir(blobs):
            path = os.path.join(blobs, name)
            this = os.path.getsize(path)
            if this > size:
                largest, size = path, this
        if not largest:
            return None, None
        started = time.perf_counter()
        with open(largest, "rb") as fh:
            while fh.read(16 * 1024 * 1024):
                pass
        return size, time.perf_counter() - started
    except OSError:
        return None, None


@app.local_entrypoint()
def main(resume: str = "auto-apply/resume/resume.pdf"):
    """Extract the text here with the production parser, measure there."""
    import sys

    sys.path.insert(0, REPO_ROOT)
    sys.path.insert(0, os.path.join(REPO_ROOT, "auto-apply"))
    from resume_parser import extract_text

    text = extract_text(resume)
    if not text.strip():
        raise SystemExit(f"no text extracted from {resume} — a scanned PDF?")
    print(f"{os.path.basename(resume)}: {len(text)} chars, "
          f"parsed locally by the production parser")
    result = profile_probe.remote(text)
    print("\nJSON:")
    print(json.dumps(result, indent=2))
