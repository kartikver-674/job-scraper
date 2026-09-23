"""V2-B5 benchmark: how much of a sweep's end does the user stop waiting for?

OFFLINE. No Apify, no job board, no production file: every socket but loopback
is denied. It drives the stack the tests drive — the real worker on a loopback
socket, the public Render app pointed at it, and scraper.main() on fixtures as
the worker's child — with a KNOWN post-result tail injected by delaying each of
the eight shadow boards by tail / 8 seconds. For each arm it records:

  t1  results_ready_at, the engine's marker    (flag on only)
  t2  finished_at, the worker's record of the process exiting
  s   when Render's /progress first said finished, polled every 25 ms

With the flag on, s - t1 is how late the server knew the result was ready, and
t2 - t1 is the tail the user no longer waits for. With it off, s >= t2: the
screen waits for the process. The browser's own 4 s poll and 1.2 s move to
/results are unchanged and not simulated here.

    .venv/bin/python -m bench.search_v2_results_ready \\
        --json docs/search-v2-evidence/results-ready-hidden-wait.json

Nothing here is imported by production code.
"""
import argparse
import json
import logging
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

from sweep.tests import test_results_ready_early as b5   # noqa: E402

# Production shadow stages observed under V2-B3 were ~4 s, ~4.5 s and ~12.1 s.
TAILS = (0.0, 4.0, 12.0)


def one(ready, tail):
    got = b5.measure(ready, tail)
    t1, t2, s = (got["t1_results_ready"], got["t2_process_done"],
                 got["screen_finished_at"])
    return {"ready_early": ready, "tail_s": tail, "state": got["state"],
            "post_result_ms": got["post_result_ms"],
            "hidden_s": None if t1 is None else round(t2 - t1, 3),
            "screen_after_ready_s": None if t1 is None else round(s - t1, 3),
            "screen_before_exit_s": round(t2 - s, 3)}


def median(rows, key):
    values = [r[key] for r in rows if r[key] is not None]
    return round(statistics.median(values), 3) if values else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tails", type=float, nargs="*", default=TAILS)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--json")
    args = ap.parse_args()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    b5.setUpModule()                    # loopback only
    runs = []
    try:
        for tail in args.tails:
            for ready in (False, True):
                for _ in range(args.reps):
                    runs.append(one(ready, tail))
                    print(json.dumps(runs[-1]), flush=True)
    finally:
        b5.tearDownModule()

    summary = []
    for tail in args.tails:
        for ready in (False, True):
            arm = [r for r in runs if r["tail_s"] == tail
                   and r["ready_early"] == ready]
            summary.append({"tail_s": tail, "ready_early": ready, "n": len(arm),
                            **{f"median_{k}": median(arm, k) for k in (
                                "hidden_s", "screen_after_ready_s",
                                "screen_before_exit_s", "post_result_ms")}})
    body = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            # "-dirty" when measured from an uncommitted tree, as B5's was.
            "revision": subprocess.run(["git", "describe", "--always", "--dirty"],
                                       cwd=ROOT, capture_output=True,
                                       text=True).stdout.strip(),
            "method": "offline stack; tail injected as a per-board shadow delay; "
                      "Render /progress polled every 25 ms",
            "summary": summary, "runs": runs}
    print(json.dumps(summary, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(body, indent=2) + "\n")


if __name__ == "__main__":
    main()
