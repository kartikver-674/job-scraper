"""Where the seven minutes actually went, per model call.

The first real Oracle run took ~7 minutes for one profile, against an
extrapolation of 100-240s in docs/inference-hosting.md. That extrapolation
was built from a published tok/s figure and our own token counts; this
harness replaces it with the runtime's own counters, taken from whichever
host is actually serving.

For every model call it records wall clock, prompt and output tokens, the
prefill and generation rates Ollama itself reports, the context length in
force, and the model load time — which is what separates "the model was
being read off disk" from "the model is just slow here".

    # on the server, once:  SWEEP_INFERENCE_METRICS=1  in the env file
    python -m bench.remote_profile --url https://host --token "$TOKEN"
    python -m bench.remote_profile --local          # same, direct to Ollama
    python -m bench.remote_profile --demo           # self-check, no model

WHAT THIS DOES NOT DO
---------------------
It does not change production logging. The service's access line is the
same six fields with metrics on or off — request id, path, status, model,
duration, outcome. The counters travel in the RESPONSE, to this harness,
and only when SWEEP_INFERENCE_METRICS is explicitly set. No résumé text,
no prompt, no model output and no token ever reaches a log here either:
this prints token COUNTS and durations.
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

import inference
import local_extract as le

RESUMES = os.path.join(HERE, "resumes")


def calls_for(text):
    """The two production calls, exactly as local_extract issues them."""
    return (("fields", le.FIELDS_PROMPT.format(text=text), le.FIELDS_SCHEMA),
            ("employment", le.EMPLOYMENT_PROMPT.format(text=text),
             le.EMPLOYMENT_SCHEMA))


def ns(value):
    """Nanoseconds to seconds. Ollama reports every duration in ns."""
    return (value or 0) / 1e9


def remote_call(url, token, model, prompt, schema, timeout=280):
    """One call through the service. Returns (seconds, metrics or None)."""
    body = json.dumps({"model": model, "prompt": prompt, "schema": schema,
                       "timeout": timeout}).encode()
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/generate", body,
        {"Content-Type": "application/json",
         "Authorization": f"Bearer {token}"})
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout + 60) as reply:
        payload = json.loads(reply.read())
    return time.time() - started, payload.get("metrics")


def local_call(model, prompt, schema, timeout=280):
    """The same call straight at Ollama, for the side-by-side."""
    engine = inference.LocalOllama()
    started = time.time()
    engine.generate(model, prompt, schema, timeout)
    return time.time() - started, engine.last_metrics


def row_for(label, seconds, metrics):
    """One call's numbers, with rates derived where Ollama gave durations."""
    m = metrics or {}
    prompt_tokens, out_tokens = m.get("prompt_eval_count"), m.get("eval_count")
    prefill, generate = ns(m.get("prompt_eval_duration")), ns(m.get("eval_duration"))
    return {
        "call": label, "seconds": round(seconds, 2),
        "prompt_tokens": prompt_tokens, "output_tokens": out_tokens,
        "prefill_s": round(prefill, 2) if prefill else None,
        "prefill_tok_s": round(prompt_tokens / prefill, 1)
                         if prompt_tokens and prefill else None,
        "generate_s": round(generate, 2) if generate else None,
        "generate_tok_s": round(out_tokens / generate, 1)
                          if out_tokens and generate else None,
        "load_s": round(ns(m.get("load_duration")), 2),
        "total_s": round(ns(m.get("total_duration")), 2) or None,
    }


def show(row):
    def n(value, width, unit=""):
        return f"{value}{unit}".rjust(width) if value is not None else "—".rjust(width)
    print(f"  {row['call']:11} {row['seconds']:7.1f}s wall   "
          f"in {n(row['prompt_tokens'], 5)}tok @ {n(row['prefill_tok_s'], 6)} tok/s   "
          f"out {n(row['output_tokens'], 5)}tok @ {n(row['generate_tok_s'], 6)} tok/s   "
          f"load {n(row['load_s'], 6)}s", flush=True)


def profile(text, caller, model, ctx_note=True):
    """Sweep's real two-call sequence, instrumented."""
    rows = []
    started = time.time()
    for label, prompt, schema in calls_for(text):
        seconds, metrics = caller(model, prompt, schema)
        row = row_for(label, seconds, metrics)
        row["num_ctx_requested"] = inference.ctx_for(prompt)
        rows.append(row)
        show(row)
        if metrics is None:
            print("    (no metrics — set SWEEP_INFERENCE_METRICS=1 on the "
                  "server and restart it)", flush=True)
    total = time.time() - started
    if ctx_note and rows[0]["num_ctx_requested"] != rows[1]["num_ctx_requested"]:
        # The hazard documented in inference-hosting.md §8: a different
        # num_ctx forces a reload between the two calls whatever
        # keep-alive says, and it looks exactly like "the host is slow".
        print(f"    !! the two calls asked for different context sizes "
              f"({rows[0]['num_ctx_requested']} vs "
              f"{rows[1]['num_ctx_requested']}) — Ollama must RELOAD the "
              f"model between them", flush=True)
    print(f"  {'PROFILE':11} {total:7.1f}s total\n", flush=True)
    return rows, total


def documents(paths, limit):
    """Résumé text from the paths given, else the benchmark documents."""
    from resume_parser import extract_text

    if paths:
        return [(os.path.basename(p), extract_text(p)) for p in paths]
    from bench.people import PEOPLE
    out = []
    for slug in PEOPLE:
        path = os.path.join(RESUMES, f"{slug}-plain.pdf")
        if os.path.exists(path):
            out.append((slug, extract_text(path)))
    return out[:limit]


def demo():
    """Self-check: the arithmetic and the formatting, no model needed."""
    row = row_for("fields", 12.5, {
        "prompt_eval_count": 430, "prompt_eval_duration": 2_000_000_000,
        "eval_count": 141, "eval_duration": 7_000_000_000,
        "load_duration": 3_100_000_000, "total_duration": 12_400_000_000})
    assert row["prefill_tok_s"] == 215.0, row
    assert row["generate_tok_s"] == 20.1, row
    assert row["load_s"] == 3.1 and row["total_s"] == 12.4, row
    assert row["seconds"] == 12.5

    # Missing metrics must degrade to dashes, not crash: an older server,
    # or one without SWEEP_INFERENCE_METRICS, is the common case.
    blank = row_for("fields", 9.0, None)
    assert blank["prompt_tokens"] is None and blank["generate_tok_s"] is None
    assert blank["seconds"] == 9.0
    show(blank)
    show(row)

    # A zero duration must not divide by zero.
    zero = row_for("x", 1.0, {"eval_count": 10, "eval_duration": 0})
    assert zero["generate_tok_s"] is None, zero

    # The two calls are the production ones, byte for byte.
    fields, employment = calls_for("RESUME TEXT")
    assert fields[1] == le.FIELDS_PROMPT.format(text="RESUME TEXT")
    assert fields[2] is le.FIELDS_SCHEMA
    assert employment[1] == le.EMPLOYMENT_PROMPT.format(text="RESUME TEXT")
    assert employment[2] is le.EMPLOYMENT_SCHEMA

    assert ns(1_500_000_000) == 1.5 and ns(None) == 0
    print("bench.remote_profile demo ok")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=None, help="inference service URL")
    parser.add_argument("--token", default=None, help="bearer token")
    parser.add_argument("--local", action="store_true",
                        help="go straight to Ollama instead")
    parser.add_argument("--model", default=None)
    parser.add_argument("--resume", action="append",
                        help="a .pdf/.txt to use instead of the benchmark set")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--json", help="write the rows here")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.demo:
        return demo()

    model = inference.model_name(args.model)
    if args.local:
        where = inference.LocalOllama().describe()
        caller = lambda m, p, s: local_call(m, p, s)
    else:
        url = args.url or inference.service_url()
        token = args.token or os.environ.get(inference.TOKEN_ENV) or ""
        if not token:
            raise SystemExit(f"--token, or {inference.TOKEN_ENV} in the "
                             f"environment, is required")
        where = url
        caller = lambda m, p, s: remote_call(url, token, m, p, s)

    print(f"model {model} via {where}\n")
    out, totals = [], []
    for name, text in documents(args.resume, args.limit):
        print(f"{name}  ({len(text)} chars of résumé text)")
        rows, total = profile(text, caller, model)
        out.append({"document": name, "chars": len(text), "total_s": total,
                    "calls": rows})
        totals.append(total)

    if len(totals) > 1:
        print(f"{len(totals)} profiles: median {statistics.median(totals):.1f}s  "
              f"min {min(totals):.1f}s  max {max(totals):.1f}s")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
