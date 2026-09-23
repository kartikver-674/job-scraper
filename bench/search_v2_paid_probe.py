"""Search V2 paid research probe: a deliberately authorised paid search through
the UNCHANGED engine, observed. NETWORK. SPENDS MONEY, but only when told to
twice. V2-C0 made it; V2-C1 made it a research instrument.

Look first — no keys, so the guarded child prints the preflight and stops:

    .venv/bin/python -m bench.search_v2_paid_probe \\
        --max-usd 0.10 --exposed-usd <ledger cumulative> --case software_fullstack \\
        --scope india --site linkedin --keywords "Backend Developer" --location India \\
        --purpose "..." --output docs/search-v2-evidence/<name>.json

then the same with `SWEEP_ALLOW_PAID_BENCH=1` in front and `--allow-paid`.

The engine runs as a bench/paid_guard.py child. It prints the paid preflight
and stops before any client exists unless BOTH keys are present and the
provider-enforced worst case, on top of --exposed-usd, fits under --max-usd. A
temporary profile enables --site only, so no default paid site can ride along;
with --case it is a synthetic cohort rendered by make_profile (no real résumé).

Afterwards, free GETs only: each run's record is re-read on a bounded schedule
until its charge stops moving, the owning account's usage beside it, and the
account's run list since the probe began. Written: contract fields, clocks,
counts, cost readings with timestamps and the engine's own one-token-per-
position trace. No row content, no token, no derived profile.

THE LEDGER (docs/search-v2-evidence/paid-research-ledger.json) is developer
evidence, appended after a run. It is never authorisation: nothing read from
it can add a key, raise a limit or lower --exposed-usd. It can only refuse —
when --exposed-usd understates what it already records, or --max-usd exceeds
its ceiling.

    .venv/bin/python -m bench.search_v2_paid_probe --summarize \\
        docs/search-v2-evidence/c1-probe-*.json --output <summary.json>
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
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

from bench import paid_guard  # noqa: E402

LEDGER = ROOT / "docs" / "search-v2-evidence" / "paid-research-ledger.json"
# Seconds after the engine exits at which each run record and account are
# re-read, up to --observe-s. Stops early once every reading has held still
# twice and each account's delta equals its runs' charge.
SCHEDULE = (0, 15, 30, 60, 120, 240, 480, 900)
UNIT_KEYS = ("unit_id", "plan_index", "provider", "actor", "requested_depth",
             "charge_ceiling_usd", "location_mode", "company_filter", "status",
             "funnel", "trace")
# From the unit's units[] record, joined by unit_id. The query text is already
# in the evidence's `shape`, and the dataset id is not needed.
EXECUTION = ("ok", "failure_category", "failure_type", "started_at", "finished_at",
             "duration_ms", "start_ms", "wait_ms", "poll_count", "poll_get_ms",
             "dataset_ms", "account_read_ms", "checkpoint_ms", "actor_run_id",
             "actor_build_id", "actor_build_number", "actor_status",
             "actor_started_at", "actor_finished_at", "actor_run_time_s",
             "max_total_charge_usd", "provider_ceiling_usd", "charged_events",
             "cost_observations", "reported_cost_usd", "billed_delta_usd",
             "budget_view_usd", "budget_basis", "raw_count", "dataset_retrieved_at")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _seconds(later, earlier):
    try:
        return round((datetime.fromisoformat(str(later))
                      - datetime.fromisoformat(str(earlier))).total_seconds(), 3)
    except (TypeError, ValueError):
        return None


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


# ---------------------------------------------------------------------------
# The guard and the ledger — kept apart
# ---------------------------------------------------------------------------
def guard_argv(args, profile):
    """The guarded child. Both keys and both limits come from THIS command
    line only; the ledger is not an input."""
    return paid_guard.engine_argv(
        ["--profile", profile, "--site", args.site, "--limit", str(args.searches),
         "--keywords", args.keywords, "--yes"],
        allow_paid=args.allow_paid, max_usd=args.max_usd,
        exposed_usd=args.exposed_usd)


def load_ledger(path=LEDGER):
    return json.loads(Path(path).read_text()) if Path(path).exists() else None


def ledger_blocks(ledger, exposed_usd, max_usd):
    """A reason to refuse, or None. Refusal only: the ledger can stop a run
    whose own arguments understate what it records, never permit one."""
    if not ledger:
        return None
    entries = ledger.get("entries") or []
    committed = Decimal(entries[-1]["cumulative_intended_usd"]) if entries else Decimal(0)
    if Decimal(str(exposed_usd)) < committed:
        return (f"--exposed-usd {exposed_usd} is below the ${committed} of intended "
                f"exposure the research ledger already records")
    if Decimal(str(max_usd)) > Decimal(ledger["ceiling_usd"]):
        return (f"--max-usd {max_usd} exceeds the shared research ceiling "
                f"${ledger['ceiling_usd']}")
    return None


def ledger_append(ledger, entry):
    """A copy of the ledger with `entry` appended and its running totals set."""
    entries = list(ledger.get("entries") or [])
    last = entries[-1] if entries else {}
    intended = Decimal(last.get("cumulative_intended_usd", "0")) + Decimal(
        entry["intended_max_usd"])
    actual = Decimal(str(last.get("cumulative_known_actual_usd", 0)))
    if entry.get("actual_final_usd") is not None:
        actual += Decimal(str(entry["actual_final_usd"]))
    entries.append(dict(entry, cumulative_intended_usd=str(intended),
                        cumulative_known_actual_usd=float(actual)))
    return dict(ledger, entries=entries)


# ---------------------------------------------------------------------------
# Reading the engine's own record
# ---------------------------------------------------------------------------
def paid_view(record):
    """The record's paid units joined to their execution records; [] for a
    record from before V2-C1 or a sweep without a paid plan."""
    executed = {u.get("unit_id"): u for u in record.get("units") or []
                if u.get("path") == "paid" and u.get("unit_id")}
    out = []
    for u in record.get("paid_units") or []:
        ex = executed.get(u["unit_id"])
        out.append(dict({k: u.get(k) for k in UNIT_KEYS}, execution=(
            None if ex is None else {k: ex[k] for k in EXECUTION if k in ex})))
    return out


def profile_source(args, name, work):
    """A temporary profile: --site alone, free sources off, outputs in `work`.
    With --case, a synthetic cohort rendered the way the worker renders one."""
    import config
    locations = [x.strip() for x in args.location.split(",") if x.strip()]
    sites = {k: {"enabled": False} for k in config.SITES}
    sites[args.site] = {**config.SITES[args.site], "enabled": True,
                        "locations": locations}
    head = '"""TEMPORARY paid research probe profile; deleted after the run."""\n'
    if args.case:
        sys.path.insert(0, str(ROOT / "auto-apply"))
        import make_profile
        fixtures = json.loads((ROOT / "docs/search-v2-evidence/paid-plans.json")
                              .read_text())["synthetic_profile_fixtures"]
        fixture = next(f for f in fixtures if f["case"] == args.case)
        prefs = dict(fixture["prefs"], work_scope=args.scope,
                     remote_scopes=[] if args.scope == "india"
                     else ["worldwide", "remote"])
        head = make_profile.render(name, fixture["derived"], prefs)
    return (head + f"\nSITES = {sites!r}\nATS_BOARDS = {{}}\nFEEDS = {{}}\n"
            f"SETTINGS = {{**globals().get('SETTINGS', {{}}), "
            f"'output_dir': {str(work)!r}}}\n")


# ---------------------------------------------------------------------------
# Free provider reads, after the run
# ---------------------------------------------------------------------------
def accounts():
    """(name, client) per configured account. The name is the variable's,
    e.g. APIFY_TOKEN_2; the token itself never leaves this function's clients."""
    from apify_client import ApifyClient
    from dotenv import load_dotenv
    import scraper
    load_dotenv()
    return [(name, ApifyClient(token)) for name, token in scraper.apify_tokens()]


def _run_reading(run):
    return {"observed_at": _now(), "status": run.status,
            "usage_total_usd": run.usage_total_usd,
            "charged_events": dict(run.charged_event_counts or {})}


def _priced(run):
    """The charged events at the run's own published prices."""
    try:
        prices = _plain(run.pricing_info)["pricing_per_event"]["actor_charge_events"]
        return round(sum(n * prices[e]["event_price_usd"]
                         for e, n in (run.charged_event_counts or {}).items()), 6)
    except (TypeError, KeyError, AttributeError):
        return None


def settle(values):
    """Index of the first reading of the final unchanged streak, if that
    streak is at least two readings long; else None (never settled here)."""
    if len(values) < 2 or values[-1] != values[-2]:
        return None
    i = len(values) - 1
    while i > 0 and values[i - 1] == values[-1]:
        i -= 1
    return i


def covered(deltas, charged):
    """Index of the first reading from which the account delta stays at or
    above the runs' charge; None if it never does. An account can also carry
    usage that is no run's (V2-C1 probe A: +$0.00004 over eight minutes), so
    "covers" is the question, not "equals"."""
    ok = [d is not None and d >= charged - 1e-6 for d in deltas]
    return next((i for i in range(len(ok)) if all(ok[i:])), None)


def observe(run_ids, owned, baselines, window_s, sleep=time.sleep):
    """Re-read every run and its account on SCHEDULE until every run charge
    and every account delta has held still twice with the account covering its
    runs, or the window ends. Returns ({run_id: [readings]}, {account:
    [readings]}, the last Run read for each run)."""
    runs = {rid: [] for rid in run_ids}
    acct = {name: [] for name in {owned[r][0] for r in run_ids}}
    last = {}
    began = time.monotonic()
    import scraper
    for offset in SCHEDULE:
        if offset > window_s:
            break
        sleep(max(0.0, began + offset - time.monotonic()))
        for rid in run_ids:
            last[rid] = owned[rid][1].run(rid).get()
            runs[rid].append(_run_reading(last[rid]))
        for name in acct:
            client = next(c for n, c in owned.values() if n == name)
            used = scraper.account_usage_usd(client)
            base = baselines.get(name, {}).get("usd")
            acct[name].append({"observed_at": _now(), "delta_usd": (
                None if used is None or base is None else round(used - base, 6))})
        still = all(settle([r["usage_total_usd"] for r in runs[rid]]) is not None
                    for rid in run_ids)
        matched = all(
            settle([r["delta_usd"] for r in acct[name]]) is not None
            and covered([acct[name][-1]["delta_usd"]], sum(
                runs[r][-1]["usage_total_usd"] or 0 for r in run_ids
                if owned[r][0] == name)) == 0
            for name in acct)
        if still and matched:
            break
    return runs, acct, last


def account_view(name, readings, settled_runs_usd):
    """When the account delta first covered the runs' settled charge (and kept
    covering it), and what it carried beyond them at the end of the window."""
    at = covered([r["delta_usd"] for r in readings], settled_runs_usd)
    last = readings[-1]["delta_usd"] if readings else None
    return {"account": name, "settled_runs_usd": settled_runs_usd,
            "readings": readings,
            "covered_at": None if at is None else readings[at]["observed_at"],
            "covered_s_after_last_finish": (None if at is None else
                                            readings[at].get("s_after_last_finish")),
            "residual_usd_at_end": (None if last is None
                                    else round(last - settled_runs_usd, 6))}


def revision():
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return head + ("-dirty" if dirty else "")


# ---------------------------------------------------------------------------
# One probe
# ---------------------------------------------------------------------------
def probe(args):
    ledger = load_ledger(args.ledger)
    reason = ledger_blocks(ledger, args.exposed_usd, args.max_usd)
    if reason:
        sys.exit(f"Probe refused: {reason}.")
    name = f"c1probe_{secrets.token_hex(4)}"
    work = Path(tempfile.mkdtemp())
    profile = ROOT / "profiles" / f"{name}.py"
    profile.write_text(profile_source(args, name, work))
    env = dict(os.environ, SWEEP_SEARCH_V2_TELEMETRY="1", SWEEP_RUN_ID=name)
    env.pop("SWEEP_EXPERIENCE_MISMATCH_GUARD", None)       # off, as in production
    armed = args.allow_paid and paid_guard.env_allows()
    began, owners, baselines = _now(), [], {}
    if armed:       # the account is read only when a run can actually start
        owners = accounts()
        import scraper
        baselines = {n: {"usd": scraper.account_usage_usd(c), "read_at": _now()}
                     for n, c in owners}
    try:
        started = time.perf_counter()
        code = subprocess.run(guard_argv(args, name), cwd=ROOT, env=env).returncode
        wall = round(time.perf_counter() - started, 2)
        exited = _now()
        found = glob.glob(str(work / "telemetry" / "sweep_*.json"))
        record = json.loads(Path(found[0]).read_text()) if found else {}
        found = glob.glob(str(work / "jobs_*.json"))
        final_rows = len(json.loads(Path(found[0]).read_text())) if found else None
    finally:
        profile.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)

    units = paid_view(record)
    run_ids = [u["execution"]["actor_run_id"] for u in units
               if (u.get("execution") or {}).get("actor_run_id")]
    if not run_ids:                    # refused, or nothing started: no evidence
        sys.exit(code or "No actor run was started; nothing to record.")

    owned = {}
    for rid in run_ids:
        for acct_name, client in owners:
            try:
                if client.run(rid).get() is not None:
                    owned[rid] = (acct_name, client)
                    break
            except Exception:
                continue
    anomalies = [f"run {rid}: no configured account could read it"
                 for rid in run_ids if rid not in owned]
    runs, acct, last = observe([r for r in run_ids if r in owned], owned,
                               baselines, args.observe_s)
    listing = {}
    for acct_name, client in owners:
        found = client.runs().list(desc=True, limit=25, started_after=began).items
        listing[acct_name] = [{"id": r.id, "status": r.status} for r in found]
    unexpected = [r["id"] for rows in listing.values() for r in rows
                  if r["id"] not in run_ids]

    convergence, settled_total = [], Decimal(0)
    for u in units:
        ex = u.get("execution") or {}
        rid = ex.get("actor_run_id")
        if rid not in runs:
            continue
        readings = runs[rid]
        for r in readings:
            r["s_after_finish"] = _seconds(r["observed_at"], ex.get("actor_finished_at"))
        engine = [dict(o, s_after_finish=_seconds(o["observed_at"],
                                                  ex.get("actor_finished_at")))
                  for o in ex.get("cost_observations") or []]
        at = settle([r["usage_total_usd"] for r in readings])
        settled = None if at is None else {
            "source": "run_record_settled", "usd": readings[at]["usage_total_usd"],
            "observed_at": readings[at]["observed_at"],
            "s_after_finish": readings[at]["s_after_finish"], "final": True,
            "basis": f"unchanged across the last {len(readings) - at} re-reads"}
        if settled:
            settled_total += Decimal(str(settled["usd"]))
        else:
            anomalies.append(f"{u['unit_id']}: run charge still moving at the end "
                             f"of the {args.observe_s}s window")
        ceiling = u.get("charge_ceiling_usd")
        if ceiling and ex.get("provider_ceiling_usd") != float(ceiling):
            anomalies.append(f"{u['unit_id']}: provider ceiling "
                             f"{ex.get('provider_ceiling_usd')} != configured {ceiling}")
        funnel = u.get("funnel") or {}
        if funnel and funnel.get("raw") != u.get("requested_depth"):
            anomalies.append(f"{u['unit_id']}: returned {funnel.get('raw')} rows for "
                             f"depth {u.get('requested_depth')}")
        if u["status"] != "completed":
            anomalies.append(f"{u['unit_id']}: status {u['status']}")
        convergence.append({"unit_id": u["unit_id"], "run_id": rid,
                            "actor_finished_at": ex.get("actor_finished_at"),
                            "engine_readings": engine, "provider_readings": readings,
                            "settled": settled, "events_priced_usd": _priced(last[rid]),
                            "charge_ceiling_usd": ceiling})
    last_finish = max((c["actor_finished_at"] for c in convergence), default=None)
    accounts_view = []
    for acct_name, readings in acct.items():
        for r in readings:
            r["s_after_last_finish"] = _seconds(r["observed_at"], last_finish)
        view = account_view(acct_name, readings, float(sum(
            (Decimal(str(c["settled"]["usd"])) for c in convergence
             if c["settled"] and owned[c["run_id"]][0] == acct_name), Decimal(0))))
        view["baseline_read_at"] = baselines.get(acct_name, {}).get("read_at")
        accounts_view.append(view)
        if view["covered_at"] is None:
            anomalies.append(f"{acct_name}: account delta never covered its runs' "
                             f"settled charge inside the window")
    if unexpected:
        anomalies.append(f"unexpected runs on the account(s): {unexpected}")

    summary = record.get("paid_summary") or {}
    entries = (ledger or {}).get("entries") or []
    before_actual = Decimal(str(entries[-1]["cumulative_known_actual_usd"])) \
        if entries else Decimal(0)
    intended = summary.get("planned_bounded_exposure_usd")
    out = {
        "status": "MEASURED LIVE: paid, authorised with both keys, through the "
                  "unchanged engine",
        "stage": args.stage, "purpose": args.purpose,
        "observed_at": began, "engine_exited_at": exited, "code_revision": revision(),
        "provider": args.site,
        "actor": units[0]["actor"],
        "actor_build_numbers": sorted({(u.get("execution") or {}).get(
            "actor_build_number") for u in units} - {None}),
        # Developer-chosen generic queries; never derived from a résumé.
        "shape": {"keywords": args.keywords, "locations": args.location,
                  "synthetic_case": args.case, "scope": args.scope},
        "requested_depth": units[0]["requested_depth"],
        "charge_ceiling_usd_per_start": units[0]["charge_ceiling_usd"],
        "actor_starts": len(run_ids), "intended_max_usd": intended,
        "exposed_usd_before": args.exposed_usd,
        "cumulative_intended_usd_after": str(Decimal(str(args.exposed_usd))
                                             + Decimal(intended or "0")),
        "exit": code, "wall_s": wall, "final_rows": final_rows,
        "paid_summary": summary, "units": units,
        "cost_convergence": convergence, "accounts": accounts_view,
        "run_listing": listing, "unexpected_runs": unexpected,
        "known_actual_usd": float(settled_total),
        "cumulative_known_actual_usd_after": float(before_actual + settled_total),
        "anomalies": anomalies,
    }
    import scraper
    text = json.dumps(out, indent=2, default=str)
    if any(token in text for _, token in scraper.apify_tokens()):
        sys.exit("A token reached the evidence; nothing written.")
    out["token_absent_from_evidence"] = True
    Path(args.output).write_text(json.dumps(out, indent=2, default=str) + "\n")
    if ledger is not None:
        entry = {"stage": args.stage, "at": began, "provider": args.site,
                 "actor": out["actor"], "purpose": args.purpose,
                 "actor_starts": len(run_ids),
                 "requested_depth": out["requested_depth"],
                 "ceiling_usd_per_start": out["charge_ceiling_usd_per_start"],
                 "intended_max_usd": intended,
                 "actual_final_usd": (float(settled_total) if len(
                     [c for c in convergence if c["settled"]]) == len(run_ids)
                     else None),
                 "evidence": os.path.relpath(args.output, Path(args.ledger).parent),
                 "status": "; ".join(anomalies) or "completed, no anomaly"}
        Path(args.ledger).write_text(
            json.dumps(ledger_append(ledger, entry), indent=2) + "\n")
    print(json.dumps({k: out[k] for k in (
        "actor_starts", "intended_max_usd", "known_actual_usd",
        "cumulative_intended_usd_after", "cumulative_known_actual_usd_after",
        "final_rows", "anomalies")}, indent=2))


# ---------------------------------------------------------------------------
# Across probes
# ---------------------------------------------------------------------------
BANDS = ((1, 5), (6, 10), (11, 15))


def summarize(paths):
    """Ranges and position yield over every probe unit given. Descriptive of
    these runs only: a handful of searches is not a provider population."""
    units, costs = [], {}
    for path in paths:
        ev = json.loads(Path(path).read_text())
        for c in ev.get("cost_convergence", []):
            if c.get("settled"):
                costs[c["unit_id"], path] = c["settled"]["usd"]
        for u in ev.get("units", []):
            if u.get("funnel"):
                units.append((path, ev.get("shape"), u))

    def rng(values):
        values = [v for v in values if v is not None]
        return [min(values), max(values)] if values else None

    def rate(num, den):
        return round(num / den, 4) if den else None

    per_unit = []
    for path, shape, u in units:
        f, ex = u["funnel"], u["execution"]
        usd = costs.get((u["unit_id"], path))
        dup = (f["acquired"] or {}).get("repeat_in_unit", 0) + \
            (f["acquired"] or {}).get("repeat_of_earlier_paid", 0)
        per_unit.append({
            "evidence": os.path.basename(path), "unit_id": u["unit_id"],
            "shape": shape, "location_mode": u["location_mode"],
            "actor_run_time_s": ex.get("actor_run_time_s"),
            "unit_wall_s": round(ex["duration_ms"] / 1000, 3),
            "raw": f["raw"], "requested": f["requested"], "stale": f["stale"],
            "eligible": f["eligible"], "eligible_positive": f["eligible_positive"],
            "final": f["final"], "final_marginal": f["final_marginal"],
            "duplicates_acquired": dup, "settled_usd": usd,
            "eligible_rate": rate(f["eligible"], f["raw"]),
            "positive_rate": rate(f["eligible_positive"], f["raw"]),
            "final_rate": rate(f["final"], f["raw"]),
            "duplicate_rate": rate(dup, f["raw"]),
            "usd_per_returned": rate(usd, f["raw"]) if usd is not None else None,
            "usd_per_eligible": rate(usd, f["eligible"]) if usd is not None else None,
            "usd_per_positive": (rate(usd, f["eligible_positive"])
                                 if usd is not None else None),
            "usd_per_final": rate(usd, f["final"]) if usd is not None else None,
        })
    bands = {}
    for _, _, u in units:
        for i, token in enumerate(u["trace"].split(), 1):
            band = next((f"{a}-{b}" for a, b in BANDS if a <= i <= b), "16+")
            b = bands.setdefault(band, dict.fromkeys(
                ("rows", "stale", "eligible", "eligible_positive", "final",
                 "duplicate_acquired"), 0))
            b["rows"] += 1
            b["stale"] += token[0] == "S"
            b["eligible"] += token[0] in "DF"
            b["eligible_positive"] += token[0] in "DF" and token[1] == "+"
            b["final"] += token[0] == "F"
            b["duplicate_acquired"] += token[2] in "up"
    return {
        "status": "DERIVED from the listed live probe evidence; descriptive, "
                  "not a provider population",
        "sample": {"evidence_files": [os.path.basename(p) for p in paths],
                   "units": len(per_unit)},
        "ranges": {k: rng([u[k] for u in per_unit]) for k in (
            "actor_run_time_s", "unit_wall_s", "raw", "eligible_rate",
            "positive_rate", "final_rate", "duplicate_rate", "settled_usd",
            "usd_per_returned", "usd_per_eligible", "usd_per_positive",
            "usd_per_final")},
        "yield_by_position": bands,
        "units": per_unit,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--summarize", nargs="+", metavar="EVIDENCE")
    ap.add_argument("--site", default="linkedin")
    ap.add_argument("--keywords")
    ap.add_argument("--location", help="one place, or several comma-separated")
    ap.add_argument("--searches", type=int, default=1)
    ap.add_argument("--case", help="synthetic cohort in paid-plans.json")
    ap.add_argument("--scope", choices=["india", "remote"], default="india")
    ap.add_argument("--purpose", default="")
    ap.add_argument("--stage", default="C1")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-usd")
    ap.add_argument("--exposed-usd", default="0")
    ap.add_argument("--observe-s", type=int, default=480)
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    if args.summarize:
        out = summarize(args.summarize)
        Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
        print(json.dumps(out["ranges"], indent=2))
        return
    if not (args.keywords and args.location and args.max_usd):
        ap.error("a probe needs --keywords, --location and --max-usd")
    os.environ.pop("JOB_PROFILE", None)   # the default SITES, not someone's profile
    probe(args)


if __name__ == "__main__":
    main()
