"""What does paid telemetry (V2-A + V2-C1) cost a paid sweep?

OFFLINE. No client, no token, no network: scraper.main() runs a 90-search paid
plan (9 keywords x 5 places x LinkedIn and Indeed — the repository defaults'
shape) against V2-C1's scripted stand-in for apify_client, each search
returning 15 synthetic rows. Every arm runs in a fresh child process, so its
peak RSS is its own, and arms alternate so neither always runs warm.

    .venv/bin/python -m bench.search_v2_paid_overhead \\
        --output docs/search-v2-evidence/c1-paid-telemetry-overhead.json

What it measures is LOCAL work: 90 checkpoint finalize passes over a growing
row set, the final pass, and — with the flag on — the telemetry hooks and the
record. Polling sleeps are patched out and the provider answers instantly, so
the denominator is the smallest a real sweep could have: a real sweep adds
~40 s of actor wait per search, which telemetry does not touch.
"""
import argparse
import hashlib
import json
import random
import resource
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

KEYWORDS = [f"Role {i}" for i in range(9)]
PLACES = ["Bengaluru", "Hyderabad", "Pune", "Delhi", "India"]
TITLES = ["Software Engineer", "Full Stack Developer", "Backend Engineer",
          "Frontend Developer", "Platform Engineer", "React Developer",
          "Senior Software Engineer", "Staff Engineer", "Data Engineer"]
SKILLS = ("react node postgres typescript python django java spring kubernetes "
          "aws docker graphql redis kafka go rust").split()


def scripts(c1, seed=1):
    """90 searches x 15 rows, deterministic. Companies repeat across searches
    so cross-search duplicates exist; some rows are stale, some onsite."""
    rng = random.Random(seed)
    today = datetime.now()
    out = []
    for unit in range(len(KEYWORDS) * len(PLACES) * 2):
        items = []
        for pos in range(15):
            words = " ".join(rng.choice(SKILLS) for _ in range(40))
            items.append(c1.li(
                f"{unit}-{pos}", rng.choice(TITLES), f"Company {rng.randrange(120)}",
                ("We build things. " + words + " ") * 6,
                location=rng.choice(["Remote", "Remote", "Bengaluru, India"]),
                posted=(today - timedelta(days=rng.choice([1, 3, 7, 30])))
                .strftime("%Y-%m-%d")))
        out.append(c1.Script(items, usage=0.03))
    return out


def arm(on):
    """One sweep in this process; prints its measurements as JSON."""
    import socket

    def deny(*a, **kw):
        raise RuntimeError("offline benchmark: network forbidden")
    socket.socket.connect = deny
    socket.create_connection = deny
    from sweep.tests import test_paid_observability as c1
    plan = scripts(c1)
    account = [round(i * 0.03, 5) for i in range(len(plan) + 1)]
    wall, cpu = time.perf_counter(), time.process_time()
    got = c1.paid_sweep(plan, on=on, keywords=KEYWORDS, locations=PLACES,
                        sites=("linkedin", "indeed"), account=account)
    wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
    digest = hashlib.sha256((got.csv or b"") + (got.json or b"")
                            + (got.seen or b"")).hexdigest()
    print(json.dumps({
        "telemetry": on, "wall_s": round(wall, 4), "cpu_s": round(cpu, 4),
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        // (1024 if sys.platform == "darwin" else 1),
        "starts": sum(1 for c in got.client.calls if c[0] == "start"),
        "final_rows": len(json.loads(got.json)) if got.json else None,
        "outputs_sha256": digest,
        "record_bytes": len(got.telemetry_text or ""),
        "paid_units": len((got.telemetry or {}).get("paid_units") or []),
    }))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", choices=["on", "off"])
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--output")
    args = ap.parse_args()
    if args.arm:
        return arm(args.arm == "on")

    runs = {"off": [], "on": []}
    for i in range(args.repeats):
        for name in (("off", "on") if i % 2 == 0 else ("on", "off")):
            done = subprocess.run([sys.executable, "-m", "bench.search_v2_paid_overhead",
                                   "--arm", name], cwd=ROOT, capture_output=True,
                                  text=True, check=True)
            runs[name].append(json.loads(done.stdout.strip().splitlines()[-1]))

    def med(name, key):
        return statistics.median(r[key] for r in runs[name])

    digests = {r["outputs_sha256"] for rs in runs.values() for r in rs}
    out = {
        "status": "MEASURED OFFLINE; scripted provider, no network, sleeps patched out",
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "plan": {"searches": runs["on"][0]["starts"], "rows_per_search": 15,
                 "providers": ["linkedin", "indeed"]},
        "repeats_per_arm": args.repeats,
        "outputs_identical_across_every_run": len(digests) == 1,
        "outputs_sha256": sorted(digests),
        "final_rows": runs["on"][0]["final_rows"],
        "median": {name: {k: med(name, k) for k in ("wall_s", "cpu_s", "peak_rss_kb")}
                   for name in runs},
        "record_bytes_on": med("on", "record_bytes"),
        "paid_units_on": runs["on"][0]["paid_units"],
        "all_runs": runs,
        "limits": [
            "Telemetry ON here is V2-A and V2-C1 together; the difference to OFF "
            "is an upper bound on V2-C1's share.",
            "The denominator is local work only. Each real search also waits on "
            "its actor (~40 s at depth 15, V2-C0), which telemetry does not add to.",
            "Synthetic rows. Scoring cost grows with description length; these "
            "descriptions are ~2 KB, below description_max.",
            "Peak RSS is the whole child process: interpreter, config, fixtures.",
        ],
    }
    for key in ("wall_s", "cpu_s", "peak_rss_kb"):
        off, on = out["median"]["off"][key], out["median"]["on"][key]
        out["median"].setdefault("difference", {})[key] = round(on - off, 4)
        out["median"].setdefault("ratio", {})[key] = round(on / off, 4) if off else None
    if args.output:
        Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("outputs_identical_across_every_run",
                                          "median", "record_bytes_on")}, indent=2))


if __name__ == "__main__":
    main()
