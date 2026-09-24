"""V2-C4 offline replay: which adaptive paid policy would have stopped, skipped
or cut where — and what it would have saved AND lost.

STRUCTURALLY PAID-UNREACHABLE. It imports paid_adaptive (pure functions) and
the standard library, nothing else: not the engine, not a client, not the
guard, not a launcher. It reads JSON evidence files and builds fixtures in
memory. There is no Apify call to make and no code here that could make one
(pinned by test_paid_adaptive.ReplayToolIsUnreachable and V2-C0's scan).

    .venv/bin/python -m bench.search_v2_paid_adaptive \\
        --output docs/search-v2-evidence/c4-adaptive-replay.json

Two inputs:

  fixtures  eleven adversarial synthetic sweeps (FIXTURES), each built to make
            one seductive policy look good or show where it breaks. Rows are
            projections — (job key, score, eligible, provider position) — run
            through paid_adaptive.replay(), which makes the same checkpoint
            observations the live shadow makes.
  captured  every live paid sweep C1-C3.5 recorded (content-free traces:
            one token per dataset position) joined, for LinkedIn, to V2-C3's
            provider-position map. They carry no score and no job key, so
            top-K and dedupe counterfactuals are UNKNOWN for them; what they
            can answer — yield by the provider's rank, and whether any
            stopping rule would even have been exercised — is reported.
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
EVIDENCE = ROOT / "docs" / "search-v2-evidence"

import paid_adaptive  # noqa: E402

CEILING = {"linkedin": "0.046", "indeed": "0.135"}


def ceiling_at(provider, depth):
    """The B1 / C3.5 formula at another depth, for the fixtures' depth savings
    (depth x FREE-tier result price x 1.5, plus LinkedIn's two start events,
    rounded up to $0.001). The live path uses scraper.max_charge_usd itself."""
    from decimal import ROUND_CEILING, Decimal
    price, start = {"linkedin": ("0.002", "0.0001"), "indeed": ("0.006", "0")}[provider]
    raw = Decimal(depth) * Decimal(price) * Decimal("1.5") + Decimal(start)
    return str((raw / Decimal("0.001")).to_integral_value(ROUND_CEILING) * Decimal("0.001"))


# ===========================================================================
# Fixtures
# ===========================================================================
def unit(i, provider="linkedin", mode="place", kw=None, depth=15):
    return {"unit_id": f"paid_{i:03d}", "plan_index": i, "provider": provider,
            "location_mode": mode, "keyword_fp": kw or f"kw{i:03d}",
            "requested_depth": depth, "charge_ceiling_usd": CEILING[provider]}


def jobs(prefix, scores, positions=None, eligible=True):
    """Rows of one unit: (key, score, eligible, provider position)."""
    positions = positions or range(1, len(scores) + 1)
    return [(f"{prefix}{j}" if isinstance(prefix, str) else prefix[j], s, eligible, p)
            for j, (s, p) in enumerate(zip(scores, positions))]


def dup(keys, score=5, start=1):
    """Rows repeating jobs already acquired, at a low score."""
    return [(k, score, True, start + j) for j, k in enumerate(keys)]


def f_all_useful():
    """1. Every search adds jobs, and each one's best job reaches the top 10."""
    plan = [unit(i) for i in range(8)]
    return plan, {u["unit_id"]: jobs(f"u{i}_", [90 - i, 70 - i, 50 - i, 30 - i, 10 - i])
                  for i, u in enumerate(plan)}, []


def f_early_then_dry():
    """2. Three useful searches, then seven that only repeat what they found."""
    plan = [unit(i) for i in range(10)]
    rows = {u["unit_id"]: jobs(f"u{i}_", [80 - i, 60 - i, 40 - i]) for i, u in enumerate(plan[:3])}
    seen = [k for r in rows.values() for k, *_ in r]
    for i, u in enumerate(plan[3:], 3):
        rows[u["unit_id"]] = dup(seen[i % 3::3][:3])
    return plan, rows, []


def f_dry_spell_then_value():
    """3. A dry spell of five, then the sweep's best job. Any N-zeros-then-stop
    rule with N <= 5 stops before it."""
    plan = [unit(i) for i in range(10)]
    rows = {u["unit_id"]: jobs(f"u{i}_", [60 - i, 40 - i]) for i, u in enumerate(plan[:3])}
    seen = [k for r in rows.values() for k, *_ in r]
    for u in plan[3:8]:
        rows[u["unit_id"]] = dup(seen[:3])
    rows[plan[8]["unit_id"]] = jobs("late_", [99, 88, 77])
    rows[plan[9]["unit_id"]] = jobs("u9_", [30])
    return plan, rows, []


def f_duplicate_heavy_late_unique():
    """4. Every later search is 80% repeats — and carries one unique job near
    the top. A duplicate rate is not redundancy."""
    plan = [unit(i) for i in range(6)]
    rows = {plan[0]["unit_id"]: jobs("base_", [50, 45, 40, 35, 30])}
    base = [k for k, *_ in rows[plan[0]["unit_id"]]]
    for i, u in enumerate(plan[1:], 1):
        rows[u["unit_id"]] = dup(base[:4]) + [(f"uniq{i}", 90 + i, True, 5)]
    return plan, rows, []


def f_top20_stable_total_growing():
    """5. The first search fills the top 20; five more add only jobs ranked
    below it. The top 20 never moves while the result keeps growing."""
    plan = [unit(i) for i in range(6)]
    rows = {plan[0]["unit_id"]: jobs("top_", list(range(100, 80, -1)))}
    for i, u in enumerate(plan[1:], 1):
        rows[u["unit_id"]] = jobs(f"low{i}_", [20 - i, 15 - i, 10 - i])
    return plan, rows, []


def f_top20_stable_then_displaced():
    """6. The brief's top-K fixture: search 1 fills the top 20, searches 2-5
    add low-ranked jobs, search 6 brings a new rank-1 job and several more."""
    plan = [unit(i) for i in range(6)]
    rows = {plan[0]["unit_id"]: jobs("top_", list(range(100, 80, -1)))}
    for i, u in enumerate(plan[1:5], 1):
        rows[u["unit_id"]] = jobs(f"low{i}_", [20 - i, 15 - i])
    rows[plan[5]["unit_id"]] = jobs("late_", [120, 99, 98, 97])
    return plan, rows, []


def f_provider_a_low_b_high():
    """7. LinkedIn stops adding after its first search; Indeed adds every time."""
    plan = [unit(i) for i in range(6)] + [unit(i, "indeed") for i in range(6, 12)]
    rows = {plan[0]["unit_id"]: jobs("li_", [70, 60, 50, 40])}
    li = [k for k, *_ in rows[plan[0]["unit_id"]]]
    for u in plan[1:6]:
        rows[u["unit_id"]] = dup(li)
    for i, u in enumerate(plan[6:], 6):
        rows[u["unit_id"]] = [(k, s, e, None) for k, s, e, _ in
                              jobs(f"in{i}_", [80 - i, 55 - i, 35 - i])]
    return plan, rows, []


def _pairs(overlaps):
    """India / Remote pairs, one per keyword: the Remote twin repeats
    overlaps[p] of its India twin's 10 jobs and adds the rest as its own,
    lower-scored."""
    plan, rows = [], {}
    for p, overlap in enumerate(overlaps):
        india, remote = unit(2 * p, kw=f"kw{p}"), unit(2 * p + 1, mode="remote", kw=f"kw{p}")
        plan += [india, remote]
        rows[india["unit_id"]] = jobs(f"in{p}_", [70 - p - j for j in range(10)])
        shared = [k for k, *_ in rows[india["unit_id"]]][:overlap]
        rows[remote["unit_id"]] = dup(shared, 40) + [
            (f"rm{p}_{j}", 30 - j, True, overlap + j + 1) for j in range(10 - overlap)]
    return plan, rows, []


def f_pair_high_overlap():
    """8. High overlap: the first two Remote twins repeat India entirely, the
    next three repeat 9 of 10 — and each of those still adds one job."""
    return _pairs([10, 10, 9, 9, 9])


def f_pair_zero_overlap():
    """9. Remote shares nothing with India."""
    return _pairs([0] * 5)


def _depth(useful_positions):
    """LinkedIn searches whose useful (unique, eligible) jobs sit only at the
    given provider positions; the others repeat earlier jobs. Dataset order
    is REVERSED against position, as the actor's push order often is, so a
    prefix by dataset index would pick the wrong rows."""
    plan = [unit(i) for i in range(6)]
    rows = {}
    for i, u in enumerate(plan):
        out = []
        for position in range(15, 0, -1):          # push order: 15 first
            if position in useful_positions:
                out.append((f"u{i}_p{position}", 60 - position - i, True, position))
            else:
                out.append((f"common_p{position}", 5, True, position))
        rows[u["unit_id"]] = out
    return plan, rows, []


def f_depth_front_loaded():
    """10. Every useful job at positions 1-5."""
    return _depth({1, 2, 3, 4, 5})


def f_depth_back_loaded():
    """11. The useful job is at position 15 only."""
    return _depth({15})


FIXTURES = {
    "all_useful": f_all_useful,
    "early_then_dry": f_early_then_dry,
    "dry_spell_then_value": f_dry_spell_then_value,
    "duplicate_heavy_late_unique": f_duplicate_heavy_late_unique,
    "top20_stable_total_growing": f_top20_stable_total_growing,
    "top20_stable_then_displaced": f_top20_stable_then_displaced,
    "provider_a_low_b_high": f_provider_a_low_b_high,
    "pair_high_overlap": f_pair_high_overlap,
    "pair_zero_overlap": f_pair_zero_overlap,
    "depth_front_loaded": f_depth_front_loaded,
    "depth_back_loaded": f_depth_back_loaded,
}


def run_fixture(name):
    plan, rows, free = FIXTURES[name]()
    return paid_adaptive.replay(plan, rows, free, ceiling_at=ceiling_at)


def gate(reports):
    """Per candidate, across every fixture: where it fired and what it lost.
    The fixtures are adversarial by design, so this is a falsification table,
    not an acceptance test — surviving it is necessary, never sufficient."""
    out = {}
    for name, rep in reports.items():
        for c in rep["candidates"]:
            g = out.setdefault(c["candidate_id"], {
                "fired_in": [], "lost_final_in": [], "lost_top10_in": [],
                "max_final_lost": 0, "max_top10_lost": 0, "best_lost_rank": None,
                "logical_avoided_total": 0})
            if c["decisions"]:
                g["fired_in"].append(name)
            lost = c["loss"]
            if lost["final_lost"]:
                g["lost_final_in"].append(name)
            if lost["top10_lost"]:
                g["lost_top10_in"].append(name)
            g["max_final_lost"] = max(g["max_final_lost"], lost["final_lost"])
            g["max_top10_lost"] = max(g["max_top10_lost"], lost["top10_lost"])
            if lost["best_lost_rank"] is not None:
                g["best_lost_rank"] = min(g["best_lost_rank"] or 10 ** 9,
                                          lost["best_lost_rank"])
            g["logical_avoided_total"] += c["savings"]["logical_avoided"]
    return out


# ===========================================================================
# Captured real traces
# ===========================================================================
CAPTURED = ("c1-probe-a-india-two-shapes.json", "c1-probe-b-remote-react.json",
            "c2-live-concurrency-canary.json", "c35-indeed-contract.json",
            "c35-indeed-concurrency-canary.json")
# The C3 batch canary is left out on purpose: one start carried two searches,
# and the actor then decided which search owned a shared job (C3 §7), so its
# per-search traces do not describe single-search execution.


def position_maps():
    """actor run id -> LinkedIn position by dataset index (V2-C3 §14)."""
    contracts = json.loads((EVIDENCE / "c3-provider-contracts.json").read_text())
    runs = contracts["linkedin"]["measured_on_single_url_runs"]["runs"]
    return {r["run"]: r["link_position_by_dataset_index"] for r in runs.values()}


def captured():
    """Per recorded sweep: its units' funnels and, where the provider gives a
    rank, the trace re-indexed by it. Tokens: reach letter (F final, D lost to
    dedupe, anything else filtered), score sign, acquired code (p: repeat of an
    earlier paid search)."""
    maps, sweeps = position_maps(), []
    for name in CAPTURED:
        ev = json.loads((EVIDENCE / name).read_text())
        units = []
        for u in ev["units"]:
            tokens = (u.get("trace") or "").split()
            run_id = (u.get("execution") or {}).get("actor_run_id")
            positions = maps.get(run_id)
            units.append({
                "unit_id": u["unit_id"], "provider": u["provider"],
                "location_mode": u.get("location_mode"),
                "funnel": {k: (u.get("funnel") or {}).get(k) for k in (
                    "raw", "eligible", "eligible_positive", "final", "final_marginal")},
                "acquired_repeat_of_earlier_paid": ((u.get("funnel") or {}).get("acquired")
                                                    or {}).get("repeat_of_earlier_paid"),
                "positions": (positions if positions and len(positions) == len(tokens)
                              else None),
                "tokens": tokens})
        sweeps.append({"evidence": name, "stage": ev.get("stage"), "units": units})
    return sweeps


def captured_report(sweeps):
    by_position, prefix = {}, {}
    for s in sweeps:
        for u in s["units"]:
            if u["positions"] is None:
                continue
            for token, position in zip(u["tokens"], u["positions"]):
                b = by_position.setdefault(paid_adaptive.bucket(position), Counter())
                b["rows"] += 1
                b["eligible"] += token[0] in "FD"
                b["eligible_positive"] += token[0] in "FD" and token[1] == "+"
                b["final"] += token[0] == "F"
                b["final_positive"] += token[0] == "F" and token[1] == "+"
                b["acquired_repeat"] += token[2] == "p"
            for d in paid_adaptive.DEPTHS:
                p = prefix.setdefault(str(d), Counter())
                beyond = [t for t, pos in zip(u["tokens"], u["positions"]) if pos > d]
                p["units"] += 1
                p["rows_not_bought"] += len(beyond)
                # Upper bound: a final row past d may still have survived
                # through another search that also had the job.
                p["final_rows_lost_upper_bound"] += sum(1 for t in beyond if t[0] == "F")
                p["final_positive_rows_lost_upper_bound"] += sum(
                    1 for t in beyond if t[0] == "F" and t[1] == "+")
                p["eligible_rows_lost"] += sum(1 for t in beyond if t[0] in "FD")
    fired = {}
    for family, params in paid_adaptive.CANDIDATES:
        cid = paid_adaptive.candidate_id(family, params)
        fired[cid] = []
        for s in sweeps:
            if len(s["units"]) >= 2 and family == "marginal_streak" and all(
                    (u["funnel"]["final_marginal"] or 0) == 0
                    for u in s["units"][-params["n"]:]) and len(s["units"]) >= params["n"]:
                fired[cid].append(s["evidence"])
    return {
        "sweeps": [{"evidence": s["evidence"], "stage": s["stage"],
                    "units": [{k: u[k] for k in ("unit_id", "provider", "location_mode",
                                                 "funnel", "acquired_repeat_of_earlier_paid")}
                              | {"provider_positions_known": u["positions"] is not None}
                              for u in s["units"]]} for s in sweeps],
        "units": sum(len(s["units"]) for s in sweeps),
        "largest_sweep_units": max(len(s["units"]) for s in sweeps),
        "linkedin_by_provider_position": {k: dict(v) for k, v in sorted(
            by_position.items(), key=lambda kv: int(kv[0].split("-")[0]))},
        "linkedin_depth_prefix": {k: dict(v) for k, v in prefix.items()},
        "indeed_position": "UNKNOWN: build 0.0.111's dataset schema has no rank field; "
                           "dataset order is not evidence of rank",
        "top_k": "UNKNOWN: the recorded traces carry no score and no job key",
        "stopping_policies_exercised": {
            cid: runs for cid, runs in fired.items() if runs},
        "stopping_note": ("no captured sweep has more than "
                          f"{max(len(s['units']) for s in sweeps)} paid searches, so no "
                          "streak of 2 or more can form in one and no stopping, skipping or "
                          "depth rule is exercised by real data; C5 is that sample"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m bench.search_v2_paid_adaptive")
    ap.add_argument("--output")
    args = ap.parse_args(argv)
    reports = {name: run_fixture(name) for name in FIXTURES}
    out = {
        "status": "OFFLINE REPLAY: synthetic adversarial fixtures and captured traces "
                  "through paid_adaptive; no engine, no client, no network",
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy_version": paid_adaptive.POLICY_VERSION,
        "candidates": [paid_adaptive.candidate_id(f, p) for f, p in paid_adaptive.CANDIDATES],
        "promoted": list(paid_adaptive.PROMOTED),
        "fixtures": {name: {
            "doc": FIXTURES[name].__doc__.strip(),
            "final_jobs": r["final_jobs"], "logical_executed": r["logical_executed"],
            "candidates": [{k: c[k] for k in ("candidate_id", "would_stop_after",
                                              "would_skip", "would_use_depth", "loss")}
                           | {"savings": {k: c["savings"][k] for k in (
                               "logical_avoided", "max_exposure_avoided_usd")},
                              "fired_at": [d["unit_id"] for d in c["decisions"]]}
                           for c in r["candidates"]],
            "depth": r["depth"], "pairs": r["pairs"]} for name, r in reports.items()},
        "gate_across_fixtures": gate(reports),
        "captured": captured_report(captured()),
    }
    if args.output:
        Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    for cid, g in out["gate_across_fixtures"].items():
        print(f"{cid:<38} fired {len(g['fired_in']):>2}  lost final in "
              f"{len(g['lost_final_in']):>2}  lost top-10 in {len(g['lost_top10_in']):>2}  "
              f"best lost rank {g['best_lost_rank']}")
    return out


if __name__ == "__main__":
    main()
