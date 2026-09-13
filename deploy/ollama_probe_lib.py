"""Helpers both Modal probes need INSIDE the container. No modal import.

This exists because of a real failure: modal_snapshot_probe.py imported
these from modal_gpu_probe.py, and Modal ships only the ENTRYPOINT module
into the container. Every container died at import with

    ModuleNotFoundError: No module named 'modal_gpu_probe'

and Modal retried, so a run that looked like "a slow snapshot" was fifteen
containers crash-looping in ten minutes.

So: anything that runs in the container lives here, this file is copied
into the image explicitly, and it imports nothing from Modal — a module
with Modal objects at import time is a module that can fail to import on
the far side.
"""

import json
import os
import subprocess
import time
import urllib.request

OLLAMA_VERSION = "0.34.0"
MODEL = "qwen3:8b"
EXPECTED_DIGEST_PREFIX = "500a1f067a9f"
OLLAMA_URL = "http://127.0.0.1:11434"


def wait_for_ollama(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2):
                return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Ollama did not become ready")


def start_ollama():
    env = os.environ.copy()
    env["OLLAMA_HOST"] = "127.0.0.1:11434"
    process = subprocess.Popen(
        ["ollama", "serve"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    wait_for_ollama()
    return process


def ollama_alive(timeout=2):
    """Is the server answering? Used AFTER restore, where it may not be."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=timeout):
            return True
    except Exception:
        return False


def resident():
    """What Ollama holds in VRAM, or {}. Read-only — loads nothing."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=10) as r:
            models = json.load(r).get("models") or []
        return models[0] if models else {}
    except Exception:
        return {}


def metrics_row(label, wall, num_ctx, prompt_chars, metrics):
    """One call's numbers. Counts and durations only — never text."""
    m = metrics or {}

    def seconds(key):
        return (m.get(key) or 0) / 1e9

    prompt_tokens, out_tokens = m.get("prompt_eval_count"), m.get("eval_count")
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


def show(row):
    def cell(value, width, suffix=""):
        return (f"{value}{suffix}" if value is not None else "—").rjust(width)

    print(f"  {row['call']:11} wall {row['wall_s']:8.2f}s   "
          f"load {row['load_s']:7.2f}s   "
          f"prefill {cell(row['prompt_eval_count'], 5)}tok in "
          f"{row['prompt_eval_s']:6.2f}s @ {cell(row['prompt_tok_s'], 7)} tok/s   "
          f"gen {cell(row['eval_count'], 4)}tok in {row['eval_s']:6.2f}s @ "
          f"{cell(row['eval_tok_s'], 6)} tok/s   ctx {row['num_ctx']}",
          flush=True)


def demo():
    """Self-check: the arithmetic, no container needed."""
    row = metrics_row("fields", 12.5, 2048, 1584, {
        "prompt_eval_count": 1100, "prompt_eval_duration": 960_000_000,
        "eval_count": 359, "eval_duration": 9_650_000_000,
        "load_duration": 56_890_000_000, "total_duration": 101_260_000_000})
    assert row["prompt_tok_s"] == 1145.8, row
    assert row["eval_tok_s"] == 37.2, row
    assert row["load_s"] == 56.89 and row["total_s"] == 101.26, row
    blank = metrics_row("x", 9.0, 2048, 10, None)
    assert blank["prompt_tokens" if False else "prompt_eval_count"] is None
    assert metrics_row("x", 1.0, 2048, 1,
                       {"eval_count": 10, "eval_duration": 0})["eval_tok_s"] is None
    show(row)
    show(blank)
    print("ollama_probe_lib demo ok")


if __name__ == "__main__":
    demo()
