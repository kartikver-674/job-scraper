"""What does SWEEP_SEARCH_V2_TELEMETRY actually cost?

AUDIT-ONLY, offline, sockets denied. The claim "the instrumentation is cheap"
is worth nothing without a number, so this measures the same frozen row set
through the same unchanged pipeline with the flag off and on, alternating the
order so a warm cache cannot flatter either side.

    .venv/bin/python -m bench.search_v2_telemetry_overhead \\
        --snapshot /tmp/search-v2-current-free-jobs.json \\
        --output docs/search-v2-evidence/telemetry-overhead.json

What it CANNOT measure: network time, which dominates a real sweep and which
telemetry does not touch (it records timestamps the engine already crosses). So
the ratio below is overhead against LOCAL scoring/filter work only — the
pessimistic denominator. Against a whole sweep the share is smaller, and the
report says so rather than quoting the flattering figure.
"""
import argparse
import json
import os
import resource
import socket
import sys
import time
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--output")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--case", default="software_fullstack")
    ap.add_argument("--scope", default="india", choices=["india", "remote"])
    args = ap.parse_args()

    os.environ.pop("JOB_PROFILE", None)
    os.environ["SWEEP_EXPERIENCE_MISMATCH_GUARD"] = "0"
    for key in [k for k in os.environ if k.startswith("APIFY_TOKEN")]:
        del os.environ[key]

    def deny(*a, **kw):
        raise RuntimeError("Offline audit: network forbidden")
    socket.socket.connect = deny
    socket.create_connection = deny
    sys.path[:0] = [str(ROOT), str(ROOT / "auto-apply")]

    import config
    import make_profile
    import telemetry
    fixtures = json.loads(
        (ROOT / "docs/search-v2-evidence/paid-plans.json").read_text())
    fixture = next(f for f in fixtures["synthetic_profile_fixtures"]
                   if f["case"] == args.case)
    prefs = dict(fixture["prefs"], work_scope=args.scope,
                 remote_scopes=[] if args.scope == "india"
                 else ["worldwide", "remote"])
    module = ModuleType("synthetic_overhead")
    rendered = make_profile.render("synthetic_overhead", fixture["derived"], prefs)
    exec(compile(rendered, "<synthetic overhead profile>", "exec"), module.__dict__)
    config._overlay(module)
    import scraper

    rows = json.loads(Path(args.snapshot).read_bytes())
    gated = [scraper._truncate_desc(dict(r)) for r in rows
             if scraper.is_dev_title(r.get("Title", ""))
             and scraper.location_allowed(r.get("Location", ""))]

    import tempfile

    def once(on, tmp):
        """One full finalize pass. Rows are copied per pass so neither arm gets
        a cheaper input than the other."""
        payload = [dict(r) for r in gated]
        if on:
            os.environ[telemetry.FLAG] = "1"
            telemetry.start("free", tmp)
        else:
            os.environ.pop(telemetry.FLAG, None)
        wall, cpu = time.perf_counter(), time.process_time()
        out = scraper.finalize(payload)
        wall = time.perf_counter() - wall
        cpu = time.process_time() - cpu
        written = telemetry.finish() if on else None
        size = os.path.getsize(written) if written else 0
        return {"wall_s": wall, "cpu_s": cpu, "final_rows": len(out),
                "record_bytes": size, "rows": out}

    off_runs, on_runs = [], []
    identical = True
    reference = None
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(args.repeats):
            # Alternate which arm goes first, so neither one systematically pays
            # for a cold cache.
            order = (False, True) if i % 2 == 0 else (True, False)
            for on in order:
                result = once(on, tmp)
                rowset = result.pop("rows")
                if reference is None:
                    reference = rowset
                elif rowset != reference:
                    identical = False
                (on_runs if on else off_runs).append(result)
    os.environ.pop(telemetry.FLAG, None)

    def med(runs, key):
        vals = sorted(r[key] for r in runs)
        return vals[len(vals) // 2]

    off_wall, on_wall = med(off_runs, "wall_s"), med(on_runs, "wall_s")
    off_cpu, on_cpu = med(off_runs, "cpu_s"), med(on_runs, "cpu_s")
    out = {
        "status": "MEASURED OFFLINE; local scoring/filter pipeline only, no network",
        "snapshot": args.snapshot,
        "fixture": f"{args.case}/{args.scope}",
        "input_rows_after_acquisition_gate": len(gated),
        "final_rows": off_runs[0]["final_rows"],
        "repeats_per_arm": args.repeats,
        "result_set_identical_across_every_pass": identical,
        "median_wall_seconds": {"telemetry_off": round(off_wall, 4),
                                "telemetry_on": round(on_wall, 4),
                                "difference": round(on_wall - off_wall, 4),
                                "ratio": round(on_wall / off_wall, 4) if off_wall else None},
        "median_process_cpu_seconds": {"telemetry_off": round(off_cpu, 4),
                                       "telemetry_on": round(on_cpu, 4),
                                       "difference": round(on_cpu - off_cpu, 4),
                                       "ratio": round(on_cpu / off_cpu, 4) if off_cpu else None},
        "telemetry_record_bytes": med(on_runs, "record_bytes"),
        "files_written_per_sweep": 1,
        "process_peak_rss_platform_units":
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "all_runs": {"off": off_runs, "on": on_runs},
        "limits": [
            "Denominator is LOCAL finalize work only. A real sweep is dominated by "
            "network time, which telemetry does not add to, so telemetry's share of "
            "a whole sweep is smaller than the ratio above.",
            "One finalize pass. Paid sweeps re-finalize after every search, so the "
            "per-sweep cost scales with checkpoint count, not with this single pass.",
            "Peak RSS is for this whole audit process with the snapshot loaded; it "
            "is not production incremental memory.",
            "Record size grows with SOURCE count, not row count: the funnel stores "
            "per-source integers, never rows.",
            "Wall time on a shared host includes scheduling noise; the CPU figure is "
            "the steadier of the two.",
        ],
    }
    if args.output:
        Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in (
        "input_rows_after_acquisition_gate", "final_rows",
        "result_set_identical_across_every_pass", "median_wall_seconds",
        "median_process_cpu_seconds", "telemetry_record_bytes")}, indent=2))


if __name__ == "__main__":
    main()
