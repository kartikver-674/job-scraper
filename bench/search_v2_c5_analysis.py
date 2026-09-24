"""V2-C5: the integrated run, analysed. OFFLINE and paid-unreachable: it reads
the kept run directory (telemetry record, outputs, ledgers) and the probe's
evidence, and writes counts, clocks, amounts and unit ids — never a title,
company, description, URL or query.

    .venv/bin/python -m bench.search_v2_c5_analysis --run-dir output/c5-run \\
        --probe docs/search-v2-evidence/c5-integrated-run.json \\
        --out-dir docs/search-v2-evidence

Writes c5-final-summary.json, c5-provider-cost-settlement.json and
c5-adaptive-analysis.json, and rewrites the probe evidence's per-run
`input_received` (the actor input, which holds the query and search URL) as a
fingerprint — after proving from it that every start carried one search.
"""
import argparse
import csv
import glob
import hashlib
import io
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def ts(value):
    """An aware UTC datetime from the engine's or the provider's clock; a
    naive value is the engine host's local time (dataset_retrieved_at)."""
    if not value:
        return None
    d = datetime.fromisoformat(str(value).replace(" ", "T"))
    return (d.astimezone() if d.tzinfo is None else d).astimezone(timezone.utc)


def dist(values):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    p95 = v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))]
    return {"n": len(v), "min": round(v[0], 3), "median": round(statistics.median(v), 3),
            "p95": round(p95, 3), "max": round(v[-1], 3)}


def spans(rows):
    """Peak simultaneous runs and total pairwise-free overlap, from clocks."""
    edges = sorted([(s, 1) for s, _ in rows] + [(f, -1) for _, f in rows])
    live = peak = 0
    for _, step in edges:
        live += step
        peak = max(peak, live)
    busy = sum((f - s).total_seconds() for s, f in rows)
    first, last = (min(s for s, _ in rows), max(f for _, f in rows)) if rows else (None, None)
    return {"peak_concurrent": peak, "runs": len(rows),
            "run_seconds_total": round(busy, 3),
            "segment_seconds": None if first is None else round((last - first).total_seconds(), 3)}


def no_batching(convergence):
    """Each start's actor input holds ONE search (LinkedIn: one URL; Indeed:
    one position/location, no startUrls)."""
    out = []
    for c in convergence:
        got = (c.get("provider_contract") or {}).get("input_received") or {}
        urls = got.get("urls") if isinstance(got, dict) else None
        out.append({"unit_id": c["unit_id"], "run_id": c["run_id"],
                    "linkedin_urls": None if urls is None else len(urls),
                    "indeed_start_urls": (len(got.get("startUrls") or [])
                                          if isinstance(got, dict) and "position" in got else None),
                    "indeed_positions": (1 if isinstance(got, dict) and got.get("position")
                                         else None)})
    ok = all((r["linkedin_urls"] == 1) or (r["indeed_positions"] == 1
                                            and not r["indeed_start_urls"]) for r in out)
    return ok, out


def scrub(probe_path):
    ev = json.loads(Path(probe_path).read_text())
    ok, per_run = no_batching(ev.get("cost_convergence") or [])
    for c in ev.get("cost_convergence") or []:
        pc = c.get("provider_contract") or {}
        got = pc.get("input_received")
        if isinstance(got, dict):
            pc["input_received"] = {
                "fields": sorted(got), "sha256": hashlib.sha256(json.dumps(
                    got, sort_keys=True).encode()).hexdigest()[:16],
                "note": "the actor input, fingerprinted: it holds the query and URL"}
    Path(probe_path).write_text(json.dumps(ev, indent=2, default=str) + "\n")
    return ev, ok, per_run


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--probe", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    run, out = Path(args.run_dir), Path(args.out_dir)
    tpath = glob.glob(str(run / "telemetry" / "sweep_*.json"))[0]
    rec = json.loads(Path(tpath).read_text())
    ev, one_search_per_start, batching_rows = scrub(args.probe)
    pu = {u["unit_id"]: u for u in rec["paid_units"]}
    ex_units = {u["unit_id"]: u for u in rec["units"] if u.get("path") == "paid"}
    sched = {u["unit_id"]: u for u in rec["paid_execution"]["units"]}
    accounts = rec["paid_execution"]["accounts"]
    acct_rows = {a["account"]: a for a in accounts["accounts"]}
    providers = sorted({u["provider"] for u in pu.values()})

    # ---- accounting ---------------------------------------------------------
    accounting = {}
    for p in providers:
        mine = [u for u in pu.values() if u["provider"] == p]
        st = Counter(u["status"] for u in mine)
        accounting[p] = {"planned": len(mine),
                         "attempted": sum(1 for u in mine if sched.get(u["unit_id"], {})
                                          .get("reservation") == "committed"),
                         "provider_starts": sum(1 for u in mine if ex_units.get(
                             u["unit_id"], {}).get("actor_run_id")),
                         "succeeded": st["completed"], "failed": st["failed"],
                         "skipped_done": st["skipped_done"],
                         "blocked_budget": st["skipped_budget"],
                         "skipped_account": st["skipped_account"]}
    by_account = defaultdict(lambda: defaultdict(Counter))
    for uid, s in sched.items():
        by_account[s.get("account")]["actual"][pu[uid]["provider"]] += \
            bool(ex_units.get(uid, {}).get("actor_run_id"))
    for a in accounts["accounts"]:
        by_account[a["account"]]["projected"].update(a["projected_units"])
    allocation = {a: {"projected": dict(v["projected"]), "actual": dict(v["actual"]),
                      "match": dict(v["projected"]) == {k: n for k, n in v["actual"].items()
                                                        if n}}
                  for a, v in sorted(by_account.items()) if a}

    # ---- reservations ---------------------------------------------------------
    order = sorted((s for s in sched.values() if s.get("committed_at")),
                   key=lambda s: s["committed_at"])
    progression, total = [], 0.0
    for s in order:
        total = round(total + float(s["reserved_usd"]), 3)
        progression.append(total)
    before_start = [s["unit_id"] for s in order
                    if ts(s["committed_at"]) and ex_units.get(s["unit_id"], {}).get(
                        "actor_started_at") and ts(s["committed_at"]) > ts(
                        ex_units[s["unit_id"]]["actor_started_at"])]
    reservations = {
        "global": rec["paid_execution"]["exposure"],
        "committed_progression_monotonic": progression == sorted(progression),
        "final_committed_usd": progression[-1] if progression else 0,
        "reserve_before_commit": all(s["reserved_at"] <= s["committed_at"] for s in order),
        "committed_after_provider_start (engine vs provider clock)": before_start,
        "per_account": {a["account"]: {k: a[k] for k in (
            "real_capacity_usd", "effective_capacity_usd", "projected_usd", "committed_usd",
            "pending_peak_usd", "remaining_usd", "starts", "failures", "peak_in_flight",
            "peak_memory_mb", "runtime_held_unknown")} for a in accounts["accounts"]},
        "accounts_used": accounts["accounts_used"],
        "account_switches": accounts["account_switches"],
        "released_before_network": rec["paid_execution"]["exposure"]["released_before_network"],
        "blocked": rec["paid_execution"]["exposure"]["blocked"]}

    # ---- concurrency, polling -------------------------------------------------
    clocks = defaultdict(list)
    per_acct_clocks = defaultdict(list)
    polling = defaultdict(list)
    for uid, e in ex_units.items():
        s, f = ts(e.get("actor_started_at")), ts(e.get("actor_finished_at"))
        if s and f:
            clocks[pu[uid]["provider"]].append((s, f))
            per_acct_clocks[sched.get(uid, {}).get("account")].append((s, f))
        got = ts(e.get("dataset_retrieved_at"))
        if got and f and e.get("dataset_ms") is not None:
            polling[pu[uid]["provider"]].append(
                (got.timestamp() - e["dataset_ms"] / 1000) - f.timestamp())
    sdk = defaultdict(Counter)
    for uid, s in sched.items():
        for k, v in (s.get("sdk") or {}).items():
            sdk[pu[uid]["provider"]][k] += v
    concurrency = {}
    for p in providers:
        mine = [uid for uid in ex_units if pu[uid]["provider"] == p]
        seg = next((g for g in rec["paid_execution"]["segments"] if g["provider"] == p), {})
        waits = [sched[uid].get("buffered_wait_ms") for uid in mine if uid in sched]
        concurrency[p] = {
            "workers": seg.get("workers"), "segment_wall_ms": seg.get("wall_ms"),
            "provider_clocks": spans(clocks[p]),
            "start_times_utc": sorted(str(s)[11:23] for s, _ in clocks[p]),
            "unit_wall_s": dist([ex_units[u].get("duration_ms", 0) / 1000 for u in mine]),
            "provider_runtime_s": dist([ex_units[u].get("actor_run_time_s") for u in mine]),
            "buffered_units": sum(1 for w in waits if w), "buffered_wait_ms": dist(waits),
            "sdk": dict(sdk[p]),
            "sdk_retries": sdk[p]["requests"] - sdk[p]["calls"],
            "http_429": sdk[p]["rate_limit_errors"],
            "poll_detection_delay_s": dist(polling[p])}
    per_account_concurrency = {a: spans(v) for a, v in sorted(per_acct_clocks.items()) if a}

    # ---- funnel and contribution ----------------------------------------------
    keys = ("requested", "raw", "normalized", "stale", "eligible", "eligible_positive",
            "final", "final_positive", "final_marginal")
    funnel = {}
    for p in providers:
        mine = [u for u in pu.values() if u["provider"] == p and u.get("funnel")]
        f = {k: sum(u["funnel"].get(k) or 0 for u in mine) for k in keys}
        f["duplicate_in_or_across_paid"] = sum(
            (u["funnel"]["acquired"].get("repeat_in_unit", 0)
             + u["funnel"]["acquired"].get("repeat_of_earlier_paid", 0)) for u in mine)
        f["dedupe_lost"] = dict(sum((Counter(u["funnel"]["dedupe_lost"]) for u in mine),
                                    Counter()))
        f["key_also_free"] = sum(u["funnel"].get("key_also_free") or 0 for u in mine)
        f["ranges_per_search"] = {k: [min(u["funnel"].get(k) or 0 for u in mine),
                                      max(u["funnel"].get(k) or 0 for u in mine)]
                                  for k in keys if mine}
        funnel[p] = f
    ad = rec.get("paid_adaptive") or {}
    per_unit_ad = {u["unit_id"]: u for u in ad.get("per_unit") or []}
    contribution = []
    for uid, u in sorted(pu.items()):
        f = u.get("funnel") or {}
        alone = (per_unit_ad.get(uid) or {}).get("if_skipped_alone") or {}
        contribution.append({
            "unit_id": uid, "provider": u["provider"], "location_mode": u["location_mode"],
            "keyword_fp": u.get("keyword_fp"), "status": u["status"],
            "final": f.get("final"), "final_marginal": f.get("final_marginal"),
            "repeat_of_earlier_paid": (f.get("acquired") or {}).get("repeat_of_earlier_paid"),
            "dedupe_lost_other_paid": (f.get("dedupe_lost") or {}).get("other_paid"),
            "dedupe_lost_free": (f.get("dedupe_lost") or {}).get("free"),
            "top10_lost_if_skipped": alone.get("top10_lost"),
            "top20_lost_if_skipped": alone.get("top20_lost"),
            "best_lost_rank_if_skipped": alone.get("best_lost_rank")})
    zero = [c["unit_id"] for c in contribution if c["final_marginal"] == 0]
    low = [c["unit_id"] for c in contribution if c["final_marginal"] and c["final_marginal"] <= 2
           and not c["top20_lost_if_skipped"]]
    moved = [c["unit_id"] for c in contribution if c["top10_lost_if_skipped"]]

    # ---- outputs, top-k, free overlap -----------------------------------------
    jpath = glob.glob(str(run / "jobs_*.json"))[0]
    cpath = glob.glob(str(run / "jobs_*.csv"))[0]
    rows = json.loads(Path(jpath).read_text())
    crows = list(csv.DictReader(io.open(cpath, encoding="utf-8-sig")))
    paid_sources = set(providers)

    def klass(r):
        return r.get("source_site") if r.get("source_site") in paid_sources else "free"
    topk = {k: dict(Counter(klass(r) for r in rows[:k])) for k in (10, 20, 50)}
    final_by = dict(Counter(klass(r) for r in rows))
    done = (run / ".done_combos").read_text().splitlines()
    ledger = json.loads((run / "paid_account_ledger.json").read_text())
    marker = run / ".results_ready"
    marker_at = json.loads(marker.read_text())["at"] if marker.exists() else None
    outputs = {
        "csv_rows": len(crows), "json_rows": len(rows), "final_rows_record": rec["final_rows"],
        "counts_consistent": len(crows) == len(rows) == rec["final_rows"],
        "csv_sha256": hashlib.sha256(Path(cpath).read_bytes()).hexdigest()[:16],
        "json_sha256": hashlib.sha256(Path(jpath).read_bytes()).hexdigest()[:16],
        "private_keys_in_output": sorted({k for r in rows for k in r if k.startswith("_")}),
        "tmp_files": [f for f in os.listdir(run) if f.endswith(".tmp")],
        "done_lines": len(done), "done_unique": len(set(done)),
        "done_equals_completed": len(done) == sum(a["succeeded"] for a in accounting.values()),
        "seen_lines": len((run / "seen.tsv").read_text().splitlines()),
        "account_ledger": {"finished": ledger["finished"],
                           "states": dict(Counter(v.get("state") for v in ledger["units"].values())),
                           "provider_status": dict(Counter(v.get("provider_status")
                                                           for v in ledger["units"].values())),
                           "integrated": dict(Counter(v.get("integrated")
                                                      for v in ledger["units"].values())),
                           "units": len(ledger["units"])},
        "results_ready_marker": marker.exists(),
        "marker_after_outputs": (marker_at is not None and marker_at >= max(
            os.path.getmtime(cpath), os.path.getmtime(jpath))),
        "marker_to_finish_s": (None if marker_at is None else round(
            ts(rec["finished_at"]).timestamp() - marker_at, 3))}

    # ---- adaptive ----------------------------------------------------------------
    candidates = []
    for c in ad.get("candidates") or []:
        candidates.append({"candidate_id": c["candidate_id"], "fired": bool(c["decisions"]),
                           "decisions": c["decisions"][:3], "would_execute": c["would_execute"],
                           "would_skip": c["would_skip"],
                           "would_stop_after": c["would_stop_after"],
                           "would_use_depth": c["would_use_depth"],
                           "savings": c["savings"], "loss": c["loss"]})
    compact = len(json.dumps(ad, separators=(",", ":")))
    adaptive = {"status": "MEASURED in the C5 run: shadow, full plan executed",
                "policy_version": ad.get("policy_version"), "promoted": ad.get("promoted"),
                "mode": ad.get("mode"), "final_jobs": ad.get("final_jobs"),
                "candidates": candidates, "pairs": ad.get("pairs"), "depth": ad.get("depth"),
                "per_unit": ad.get("per_unit"),
                "record_size": {"section_compact_bytes": compact,
                                "section_indented_bytes": len(json.dumps(ad, indent=2)),
                                "telemetry_file_bytes": os.path.getsize(tpath)}}

    # ---- cost settlement ------------------------------------------------------
    by_run = {c["run_id"]: c for c in ev.get("cost_convergence") or []}
    settle = []
    for uid, e in sorted(ex_units.items()):
        c = by_run.get(e.get("actor_run_id")) or {}
        s = c.get("settled") or {}
        prov = [o for o in e.get("cost_observations") or []
                if o.get("source") == "run_record_at_completion"]
        settle.append({"unit_id": uid, "provider": pu[uid]["provider"],
                       "account": sched.get(uid, {}).get("account"),
                       "run_id": e.get("actor_run_id"),
                       "ceiling_usd": pu[uid]["charge_ceiling_usd"],
                       "provider_ceiling_usd": e.get("provider_ceiling_usd"),
                       "terminal_provisional_usd": prov[0]["usd"] if prov else None,
                       "settled_usd": s.get("usd"), "settled_final": bool(s.get("final")),
                       "s_to_settle": s.get("s_after_finish"),
                       "charged_events": (c.get("provider_contract") or {}).get(
                           "charged_events")})
    totals = {p: round(sum(x["settled_usd"] or 0 for x in settle if x["provider"] == p), 6)
              for p in providers}
    cost = {"status": "MEASURED: free run and account reads after the run (the probe)",
            "runs": settle,
            "settled_usd_by_provider": totals,
            "settled_usd_total": round(sum(totals.values()), 6),
            "unsettled_runs": [x["unit_id"] for x in settle if not x["settled_final"]],
            "time_to_settle_s": dist([x["s_to_settle"] for x in settle]),
            "ceiling_mismatch": [x["unit_id"] for x in settle
                                 if x["provider_ceiling_usd"] is not None
                                 and float(x["ceiling_usd"]) != x["provider_ceiling_usd"]],
            "accounts": [{k: a.get(k) for k in ("account", "settled_runs_usd",
                                                 "covered_s_after_last_finish",
                                                 "residual_usd_at_end")}
                         | {"delta_usd_at_end": (a["readings"][-1]["delta_usd"]
                                                 if a.get("readings") else None)}
                         for a in ev.get("accounts") or []],
            "provider_runs_listed": {k: len(v) for k, v in (ev.get("run_listing") or {}).items()},
            "unexpected_runs": ev.get("unexpected_runs"), "anomalies": ev.get("anomalies")}

    ms = rec.get("milestones") or {}
    summary = {
        "status": "MEASURED: the one integrated C5 run, analysed offline",
        "code_revision": ev.get("code_revision"), "exit": ev.get("exit"),
        "wall_s_probe": ev.get("wall_s"), "sweep_duration_ms": rec["duration_ms"],
        "milestones_ms": ms, "post_result_ms": rec.get("post_result_ms"),
        "segments": rec["paid_execution"]["segments"],
        "checkpoint": rec["paid_execution"]["checkpoint"],
        "paid_phase_ms": (rec.get("paid_summary") or {}).get("paid_phase_ms"),
        "accounting": accounting, "allocation": allocation,
        "reservations": reservations, "concurrency": concurrency,
        "per_account_concurrency": per_account_concurrency,
        "no_batching": {"one_search_per_start": one_search_per_start,
                        "starts": len(batching_rows), "per_run": batching_rows},
        "funnel": funnel, "by_provider_position": (rec.get("paid_summary") or {}).get(
            "by_provider_position"),
        "contribution": contribution, "zero_marginal_units": zero,
        "low_marginal_units_no_top20_loss": low, "units_moving_top10_if_skipped": moved,
        "final_by_class": final_by, "top_k_by_class": topk,
        "identity": rec.get("identity"), "outputs": outputs,
        "failures": [{"unit_id": u, "type": ex_units[u].get("failure_type"),
                      "category": ex_units[u].get("failure_category"),
                      "account_refusal": sched.get(u, {}).get("account_refusal")}
                     for u in ex_units if not ex_units[u].get("ok")],
        "shadow_tranche_units": len(rec.get("shadow_units") or []),
        "notes": rec.get("notes")}
    for name, body in (("c5-final-summary.json", summary),
                       ("c5-provider-cost-settlement.json", cost),
                       ("c5-adaptive-analysis.json", adaptive)):
        (out / name).write_text(json.dumps(body, indent=2, default=str) + "\n")
    print(json.dumps({"accounting": accounting, "settled": cost["settled_usd_by_provider"],
                      "total": cost["settled_usd_total"], "outputs_ok":
                      outputs["counts_consistent"], "one_search_per_start":
                      one_search_per_start}, indent=1))


if __name__ == "__main__":
    main()
