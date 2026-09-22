"""Offline shadow evaluation of the eight candidate Greenhouse boards.

AUDIT-ONLY. No network, no Apify, no production import path, no output/ write.
Sockets are denied before the engine is imported, so this cannot reach a
provider even by accident.

This answers the question sources/shadow.py deliberately does NOT answer in a
user's sweep: of the rows those eight boards return, how many survive the
UNCHANGED candidate gate, freshness rule, hard filters, scoring and dedupe, how
many are new relative to the current 134-source baseline, and would any of them
reach a top-20 result set. Running that pipeline twice inside a live sweep to
find out would cost the person waiting on it real time for a diagnostic, so it
happens here against the frozen audit snapshots instead.

    .venv/bin/python -m bench.search_v2_shadow \\
        --baseline /tmp/search-v2-current-free-jobs.json \\
        --expansion /tmp/search-v2-expansion-jobs.json \\
        --output docs/search-v2-evidence/shadow-eight-board-replay.json

Scores >0 and >=10 are DESCRIPTIVE fixture metrics reused from the audit's
replay harness. They are not a new eligibility rule and no threshold changed.
"""
import argparse
import hashlib
import json
import os
import socket
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]

# scope per case: the arrangement each fixture was replayed under in the audit,
# kept identical so these numbers sit beside the ones already published.
CASES = [("software_fullstack", "india"), ("software_fullstack", "remote"),
         ("react_native", "india"), ("business_salesforce", "india"),
         ("multiple_roles_locations", "india")]


def load_engine(case, scope):
    """One synthetic profile, rendered in memory, overlaid on config, then the
    UNCHANGED scraper. Same procedure as bench/search_v2_replay.py."""
    fixtures = json.loads(
        (ROOT / "docs/search-v2-evidence/paid-plans.json").read_text())
    fixture = next(f for f in fixtures["synthetic_profile_fixtures"]
                   if f["case"] == case)
    prefs = dict(fixture["prefs"], work_scope=scope,
                 remote_scopes=[] if scope == "india" else ["worldwide", "remote"])
    import make_profile
    import config
    module = ModuleType("synthetic_shadow")
    rendered = make_profile.render("synthetic_shadow", fixture["derived"], prefs)
    exec(compile(rendered, "<synthetic shadow profile>", "exec"), module.__dict__)
    config._overlay(module)
    import importlib
    import scraper
    scraper = importlib.reload(scraper)
    assert not scraper.experience_guard.enabled()
    return scraper, hashlib.sha256(rendered.encode()).hexdigest()


def run(scraper, rows):
    """Acquisition gate then the whole unchanged finalize pipeline."""
    gated = [scraper._truncate_desc(dict(r)) for r in rows
             if scraper.is_dev_title(r.get("Title", ""))
             and scraper.location_allowed(r.get("Location", ""))]
    return gated, scraper.finalize(gated)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--expansion", required=True)
    ap.add_argument("--output", required=True)
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

    from sources import shadow
    tokens = {f"greenhouse:{t}": name
              for t, name in shadow.BOARDS["greenhouse"].items()}

    def load(path):
        raw = Path(path).read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    baseline, baseline_sha = load(args.baseline)
    expansion, expansion_sha = load(args.expansion)

    shadow_rows = [r for r in expansion if r.get("Source") in tokens]
    missing = set(tokens) - {r.get("Source") for r in shadow_rows}

    # Per-board request evidence comes from the audit's own endpoint probe, not
    # from a fresh request: this harness does not touch the network.
    probes = {f"{r['provider']}:{r['board_id']}": r for r in json.loads(
        (ROOT / "docs/search-v2-evidence/expansion-validation.json").read_text()
    )["records"]}

    # hires_home is a BOARD-level signal computed over the unfiltered board, so
    # it has to be recomputed per snapshot before any gate — same as the replay.
    import config  # noqa: F401  (imported for its side effect on sys.path order)
    results = {}
    for case, scope in CASES:
        scraper, policy_sha = load_engine(case, scope)
        for rows in (baseline, shadow_rows):
            home = defaultdict(bool)
            for row in rows:
                home[row.get("Source")] |= scraper.is_home_location(
                    row.get("Location", ""))
            for row in rows:
                if ":" in row.get("Source", ""):
                    row["hires_home"] = "yes" if home[row["Source"]] else "no"

        t0 = time.perf_counter()
        base_gated, base_final = run(scraper, baseline)
        combined_gated, combined_final = run(scraper, baseline + shadow_rows)
        seconds = time.perf_counter() - t0

        base_keys = {scraper.job_key(r) for r in base_final}
        base_raw_keys = {scraper.job_key(r) for r in baseline}
        base_top20 = [scraper.job_key(r) for r in base_final[:20]]
        combined_top20 = [scraper.job_key(r) for r in combined_final[:20]]
        entered_top20 = [k for k in combined_top20 if k not in base_keys]

        per_board = {}
        for source, company in sorted(tokens.items()):
            raw = [r for r in shadow_rows if r.get("Source") == source]
            gated = [r for r in combined_gated if r.get("Source") == source]
            final = [r for r in combined_final if r.get("source_site") == source]
            probe = probes.get(source, {})
            per_board[source] = {
                "company": company,
                "request_ms": probe.get("elapsed_ms"),
                "response_bytes": probe.get("bytes"),
                "http_status": probe.get("status"),
                "error": probe.get("error") or "",
                "raw_rows": len(raw),
                "normalized_rows": len(raw),
                "fresh_14d_rows": sum(
                    scraper._parse_date(r.get("Posted Date")) is not None
                    and scraper.is_recent(r.get("Posted Date"), 14) for r in raw),
                "candidate_gated_rows": len(gated),
                "eligible_final_rows": len(final),
                "positive_score_rows": sum(r["score"] > 0 for r in final),
                "score_at_least_10_rows": sum(r["score"] >= 10 for r in final),
                "marginal_unique_vs_baseline_raw": len(
                    {scraper.job_key(r) for r in raw} - base_raw_keys),
                "overlap_with_baseline_raw": len(
                    {scraper.job_key(r) for r in raw} & base_raw_keys),
                "enters_top20": sum(
                    1 for r in final if scraper.job_key(r) in entered_top20),
            }

        results[f"{case}/{scope}"] = {
            "policy_sha256": policy_sha,
            "baseline_final": len(base_final),
            "combined_final": len(combined_final),
            "additional_final": len(combined_final) - len(base_final),
            "baseline_positive": sum(r["score"] > 0 for r in base_final),
            "combined_positive": sum(r["score"] > 0 for r in combined_final),
            "baseline_at_least_10": sum(r["score"] >= 10 for r in base_final),
            "combined_at_least_10": sum(r["score"] >= 10 for r in combined_final),
            "baseline_top20_retained": len(set(base_top20) & set(combined_top20)),
            "shadow_rows_entering_top20": len(entered_top20),
            "replay_seconds": round(seconds, 3),
            "by_board": per_board,
        }

    out = {
        "status": "MEASURED OFFLINE against frozen audit snapshots; synthetic "
                  "fixtures, not real-candidate validation",
        "boards": tokens,
        "boards_absent_from_snapshot": sorted(missing),
        "baseline_snapshot_sha256": baseline_sha,
        "expansion_snapshot_sha256": expansion_sha,
        "shadow_raw_rows": len(shadow_rows),
        "cases": results,
        "cohort_coverage": {
            "covered": [f"{c}/{s}" for c, s in CASES],
            "not_covered": ["early-career", "non-software (trades, healthcare, "
                            "manufacturing)", "business/operations outside the "
                            "Salesforce fixture"],
            "why": "No frozen fixture exists for these cohorts. Authoring one is "
                   "a separate reviewed step: an invented cohort measured by its "
                   "own author is weaker evidence than an honest gap.",
        },
        "limits": [
            "Snapshot rows are one dated observation (2026-09-21), not live inventory.",
            "PubMatic is excluded from the tranche: its sampled job page returned "
            "HTTP 403 in the audit spot-check and it is held for reachability review.",
            "'Eligible' means the current engine kept the row. It is not proof the "
            "posting is open, the URL reachable, or the candidate a fit.",
            "Score >0 / >=10 are descriptive fixture metrics. No gate or weight changed.",
            "Company labels on expansion rows may be slug-derived, so cross-board "
            "identity can be undercounted.",
            "Baseline here is the audit census snapshot, not a live 134-source sweep.",
        ],
    }
    Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    for name, case in results.items():
        print(f"{name:<38} final {case['baseline_final']:>4} -> "
              f"{case['combined_final']:<4} (+{case['additional_final']}) "
              f"positive {case['baseline_positive']:>3} -> "
              f"{case['combined_positive']:<3} "
              f"top20 entrants {case['shadow_rows_entering_top20']}")


if __name__ == "__main__":
    main()
