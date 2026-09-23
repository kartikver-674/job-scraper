"""Search V2-C0: a deliberately authorised paid search through the UNCHANGED
engine. NETWORK. SPENDS MONEY, but only when told to twice.

    SWEEP_ALLOW_PAID_BENCH=1 .venv/bin/python -m bench.search_v2_paid_probe \\
        --allow-paid --max-usd 0.50 --exposed-usd 0 \\
        --site linkedin --keywords "Software Engineer" --location Bengaluru \\
        --output docs/search-v2-evidence/c1-linkedin-contract.json

The engine runs as a bench/paid_guard.py child. It prints the paid preflight
(provider, actor, starts, depth, per-start ceiling, worst case) and stops before
any client exists unless BOTH keys are present and the provider-enforced worst
case, on top of --exposed-usd, fits under --max-usd. A site with no provider
ceiling can therefore never run here. A temporary profile enables --site only,
so no default paid site can ride along; outputs go to a temporary directory and
are deleted afterwards.

Then the run is read back from Apify with free GETs: the input the actor
received, the ceiling Apify recorded, the events it charged. Only contract
fields and counts are written; no row content, no token.
"""
import argparse
import glob
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
os.environ.pop("JOB_PROFILE", None)     # the default SITES, not someone's profile

from bench import paid_guard  # noqa: E402

UNIT_FIELDS = ("family", "board", "query", "location", "requested_limit", "ok",
               "failure_category", "failure", "duration_ms", "raw_count",
               "actor_run_id", "actor_build_id", "actor_status", "poll_count",
               "max_total_charge_usd", "reported_cost_usd", "billed_delta_usd",
               "actor_started_at", "actor_finished_at")


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def provider_view(run_id):
    """What Apify recorded for one run, read with whichever configured token
    owns it. Free GETs only: run, input record, dataset metadata."""
    from apify_client import ApifyClient
    from dotenv import load_dotenv
    import scraper
    load_dotenv()
    for _name, token in scraper.apify_tokens():
        client = ApifyClient(token)
        try:
            run = client.run(run_id).get()
        except Exception:
            run = None
        if run is None:
            continue
        record = client.key_value_store(run.default_key_value_store_id).get_record("INPUT")
        given = (record or {}).get("value") or {}
        dataset = client.dataset(run.default_dataset_id).get() if run.default_dataset_id else None
        return {
            "status": run.status,
            "build_number": run.build_number,
            "run_time_secs": run.stats.run_time_secs if run.stats else None,
            "max_total_charge_usd": _plain(run.options.max_total_charge_usd)
            if run.options else None,
            "charged_event_counts": _plain(run.charged_event_counts),
            "usage_total_usd": run.usage_total_usd,
            "pricing_info": _plain(run.pricing_info),
            # The whole record: what Sweep sent plus the actor's schema
            # defaults (autoConvertToAiSearch and the like), which B1 decided
            # not to send and so can only be read here.
            "input": given,
            "dataset_items": dataset.item_count if dataset else None,
        }
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="linkedin")
    ap.add_argument("--keywords", required=True)
    ap.add_argument("--location", required=True)
    ap.add_argument("--searches", type=int, default=1)
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-usd", required=True)
    ap.add_argument("--exposed-usd", default="0")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    import config
    sites = {k: {"enabled": False} for k in config.SITES}
    sites[args.site] = {**config.SITES[args.site], "enabled": True,
                        "locations": [args.location]}
    name = f"c0probe_{secrets.token_hex(4)}"
    profile = ROOT / "profiles" / f"{name}.py"
    work = Path(tempfile.mkdtemp())
    profile.write_text(
        '"""TEMPORARY V2-C0 paid probe; deleted after the run."""\n'
        f"SITES = {sites!r}\nATS_BOARDS = {{}}\nFEEDS = {{}}\n"
        f"SETTINGS = {{'output_dir': {str(work)!r}}}\n")
    argv = paid_guard.engine_argv(
        ["--profile", name, "--site", args.site, "--limit", str(args.searches),
         "--keywords", args.keywords, "--yes"],
        allow_paid=args.allow_paid, max_usd=args.max_usd,
        exposed_usd=args.exposed_usd)
    env = dict(os.environ, SWEEP_SEARCH_V2_TELEMETRY="1", SWEEP_RUN_ID=name)
    try:
        started = time.perf_counter()
        code = subprocess.run(argv, cwd=ROOT, env=env).returncode
        wall = round(time.perf_counter() - started, 2)
        found = glob.glob(str(work / "telemetry" / "sweep_*.json"))
        record = json.loads(Path(found[0]).read_text()) if found else {}
        found = glob.glob(str(work / "jobs_*.json"))
        final_rows = len(json.loads(Path(found[0]).read_text())) if found else None
    finally:
        profile.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
    if code:
        sys.exit(code)

    units = [{k: u.get(k) for k in UNIT_FIELDS}
             for u in record.get("units", []) if u.get("path") == "paid"]
    out = {"status": "MEASURED LIVE: paid, authorised with both keys, through "
                     "the unchanged engine",
           "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "site": args.site, "keywords": args.keywords, "location": args.location,
           "searches": args.searches, "max_usd": args.max_usd,
           "exposed_usd_before": args.exposed_usd, "exit": code, "wall_s": wall,
           "final_rows": final_rows, "units": units,
           "provider": [provider_view(u["actor_run_id"]) for u in units
                        if u.get("actor_run_id")]}
    Path(args.output).write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
