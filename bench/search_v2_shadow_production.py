"""Per-board evidence from production shadow sweeps (Search V2-B3).

Reads telemetry records — `<run>/output/telemetry/sweep_*.json`, copied off the
worker inside its 48-hour TTL — and summarises each of the eight shadow boards
across every sweep that ran them. Counts in, counts out: a record holds no row,
title, company, URL or description, and neither does this report.

    .venv/bin/python -m bench.search_v2_shadow_production RECORD_OR_DIR ... \\
        [--json summary.json] [--markdown summary.md]

There is deliberately no winner and no composite score. Each table answers
one of the promotion questions in
docs/search-engine-v2-b3-shadow-production-evaluation.md §17 — healthy? new?
useful? useful for more than one kind of search? credible identity? fast
enough? cheap enough? — and a board can pass one and fail another. A single
number would hide exactly the tradeoff the decision is about.
"""
import argparse
import glob
import json
import math
import os
import statistics
from collections import Counter, defaultdict

SCHEMA = "search-v2b3.1"     # sources.shadow.SCHEMA; not imported, so this
                             # reads records without importing the engine


def load(paths):
    """Every record with shadow units, once per sweep, oldest first."""
    files = []
    for path in paths:
        if os.path.isdir(path):
            files += glob.glob(os.path.join(path, "**", "sweep_*.json"),
                               recursive=True)
        else:
            files.append(path)
    records = {}
    for path in sorted(set(files)):
        try:
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            continue
        if record.get("shadow_units"):
            records[record.get("sweep_id") or path] = record
    return sorted(records.values(), key=lambda r: r.get("started_at") or "")


def _p95(values):
    values = sorted(values)
    return values[math.ceil(0.95 * len(values)) - 1] if values else None


def _med(values):
    return statistics.median(values) if values else None


def _dist(values):
    values = [v for v in values if v is not None]
    return {"n": len(values), "sum": sum(values), "min": min(values, default=None),
            "median": _med(values), "max": max(values, default=None)}


def _ratio(num, den):
    return round(num / den, 3) if den else None


def _cohort(scope):
    scope = scope or {}
    return (f"scope={scope.get('work_scope') or 'none'} "
            f"remote={'+'.join(scope.get('remote_scopes') or []) or 'none'} "
            f"recency={scope.get('max_age_days')}")


def summarise(records):
    boards = defaultdict(lambda: defaultdict(list))
    cohorts = defaultdict(lambda: defaultdict(Counter))
    sweeps = {"runs": 0, "evaluated": 0, "failed": 0, "over_budget": 0,
              "no_section": 0, "unknown_schema": 0, "dates": set(),
              "fetch_wall_ms": [], "evaluation_cpu_ms": [],
              "evaluation_wall_ms": [], "rss_delta_kb": [],
              "share_of_free_phase": [], "section_bytes": []}
    for record in records:
        sweeps["runs"] += 1
        sweeps["dates"].add((record.get("started_at") or "")[:10])
        section = record.get("shadow_evaluation")
        status = (section or {}).get("status")
        if section is None:
            sweeps["no_section"] += 1
        elif section.get("schema") != SCHEMA:
            sweeps["unknown_schema"] += 1
            section = None
        elif status == "failed":
            sweeps["failed"] += 1
        elif status == "acquisition_only_over_budget":
            sweeps["over_budget"] += 1
        elif status == "evaluated":
            sweeps["evaluated"] += 1
        by_board = (section or {}).get("by_board") or {}
        cohort = _cohort((section or {}).get("scope"))
        if section and section.get("cost"):
            cost = section["cost"]
            for key in ("fetch_wall_ms", "evaluation_cpu_ms", "evaluation_wall_ms"):
                if cost.get(key) is not None:
                    sweeps[key].append(cost[key])
            if None not in (cost.get("peak_rss_kb_before"), cost.get("peak_rss_kb_after")):
                sweeps["rss_delta_kb"].append(
                    cost["peak_rss_kb_after"] - cost["peak_rss_kb_before"])
            marks = record.get("milestones") or {}
            phase = (marks.get("free_phase_done") or 0) - (marks.get("free_phase_start") or 0)
            if phase > 0 and cost.get("fetch_wall_ms") is not None:
                sweeps["share_of_free_phase"].append(
                    (cost["fetch_wall_ms"] + (cost.get("evaluation_wall_ms") or 0)) / phase)
            sweeps["section_bytes"].append(len(json.dumps(section, indent=2)))

        for unit in record["shadow_units"]:
            name = unit.get("board")
            b = boards[name]
            b["runs"].append(1)
            b["ok"].append(bool(unit.get("ok")))
            if not unit.get("ok"):
                b["failures"].append(unit.get("failure_category") or "unknown")
            b["duration_ms"].append(unit.get("duration_ms") or 0)
            b["retries"].append(unit.get("retries") or 0)
            if unit.get("ok"):
                b["raw"].append(unit.get("raw_count"))
                b["gated"].append(unit.get("source_gate_count"))
            fields = by_board.get(name) or {}
            if not fields.get("fetched"):
                continue
            for key in ("fresh", "native_id", "native_id_distinct", "url",
                        "url_carries_native_id", "url_ats_hosted",
                        "key_overlap", "native_overlap",
                        "key_overlap_native_distinct",
                        "key_overlap_native_not_comparable",
                        "native_overlap_key_distinct"):
                b[key].append(fields.get(key))
            b["acq_gated"].append(fields.get("gated") or 0)
            if fields.get("eligible") is None:        # funnel not run
                continue
            for key in ("eligible", "eligible_positive", "final_new",
                        "final_new_positive", "final_replaces", "top_n_new"):
                b[key].append(fields.get(key) or 0)
            if fields.get("best_new_rank") is not None:
                b["best_new_rank"].append(fields["best_new_rank"])
            c = cohorts[cohort][name]
            c["runs"] += 1
            c["runs_with_new_positive"] += (fields.get("final_new_positive") or 0) > 0
            c["final_new"] += fields.get("final_new") or 0
            c["top_n_new"] += fields.get("top_n_new") or 0

    out = {"sweeps": _sweep_summary(sweeps), "boards": {}, "cohorts": {}}
    for name in sorted(boards):
        b = boards[name]
        sum_ = lambda k: sum(v for v in b[k] if v is not None)   # noqa: E731
        out["boards"][name] = {
            "runs": len(b["runs"]),
            "ok": sum(b["ok"]),
            "success_rate": _ratio(sum(b["ok"]), len(b["ok"])),
            "failure_categories": dict(Counter(b["failures"])),
            "duration_ms_median": _med(b["duration_ms"]),
            "duration_ms_p95": _p95(b["duration_ms"]),
            "retries": sum(b["retries"]),
            "raw": _dist(b["raw"]),
            "gated": _dist(b["gated"]),
            "fresh": _dist(b["fresh"]),
            "native_id_coverage": _ratio(sum_("native_id"), sum(b["acq_gated"])),
            "native_id_distinct_share": _ratio(sum_("native_id_distinct"), sum_("native_id")),
            "url_carries_native_id_share": _ratio(sum_("url_carries_native_id"), sum_("url")),
            "url_ats_hosted_share": _ratio(sum_("url_ats_hosted"), sum_("url")),
            "key_overlap_share": _ratio(sum_("key_overlap"), sum(b["acq_gated"])),
            "native_overlap": sum_("native_overlap"),
            "key_overlap_native_distinct": sum_("key_overlap_native_distinct"),
            "key_overlap_native_not_comparable": sum_("key_overlap_native_not_comparable"),
            "native_overlap_key_distinct": sum_("native_overlap_key_distinct"),
            "evaluated_runs": len(b["eligible"]),
            "eligible": _dist(b["eligible"]),
            "eligible_positive": _dist(b["eligible_positive"]),
            "final_new": _dist(b["final_new"]),
            "final_new_positive": _dist(b["final_new_positive"]),
            "final_replaces": sum(b["final_replaces"]),
            "top_n_new": _dist(b["top_n_new"]),
            "runs_with_new_positive": sum(1 for v in b["final_new_positive"] if v),
            "runs_with_top_n_entry": sum(1 for v in b["top_n_new"] if v),
            "best_new_rank_seen": min(b["best_new_rank"], default=None),
        }
    for cohort, per in sorted(cohorts.items()):
        out["cohorts"][cohort] = {name: dict(c) for name, c in sorted(per.items())}
    return out


def _sweep_summary(s):
    return {
        "runs": s["runs"], "evaluated": s["evaluated"], "failed": s["failed"],
        "over_budget": s["over_budget"], "no_section": s["no_section"],
        "unknown_schema": s["unknown_schema"],
        "observation_dates": sorted(d for d in s["dates"] if d),
        **{f"{k}_median": _med(s[k]) for k in (
            "fetch_wall_ms", "evaluation_cpu_ms", "evaluation_wall_ms",
            "rss_delta_kb", "section_bytes")},
        **{f"{k}_p95": _p95(s[k]) for k in (
            "fetch_wall_ms", "evaluation_cpu_ms", "evaluation_wall_ms")},
        "share_of_free_phase_median": (round(_med(s["share_of_free_phase"]), 4)
                                       if s["share_of_free_phase"] else None),
    }


def markdown(summary):
    s, boards = summary["sweeps"], summary["boards"]
    fmt = lambda v: "—" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))  # noqa: E731
    lines = [
        f"# Shadow tranche — {s['runs']} production sweeps",
        "",
        f"Dates: {', '.join(s['observation_dates']) or '—'}. Evaluated "
        f"{s['evaluated']}, failed {s['failed']}, over budget {s['over_budget']}, "
        f"no section {s['no_section']}. Repeated sweeps by one person are "
        f"indistinguishable here by design; N is sweeps, not people.",
        "",
        "## Healthy? Fast enough?",
        "",
        "| Board | Runs | OK | Success | Failures | Median ms | p95 ms | Retries |",
        "|---|---:|---:|---:|---|---:|---:|---:|"]
    for name, b in boards.items():
        lines.append(f"| {name} | {b['runs']} | {b['ok']} | {fmt(b['success_rate'])} | "
                     f"{', '.join(f'{k}×{v}' for k, v in b['failure_categories'].items()) or '—'} | "
                     f"{fmt(b['duration_ms_median'])} | {fmt(b['duration_ms_p95'])} | {b['retries']} |")
    lines += ["", "## New? Credible identity?", "",
              "| Board | Raw med | Gated med | Fresh med | Native id | Id distinct | URL has id "
              "| ATS-hosted | Key overlap | Native overlap | Key dup, native distinct "
              "| Native dup, key distinct |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, b in boards.items():
        lines.append(
            f"| {name} | {fmt(b['raw']['median'])} | {fmt(b['gated']['median'])} | "
            f"{fmt(b['fresh']['median'])} | {fmt(b['native_id_coverage'])} | "
            f"{fmt(b['native_id_distinct_share'])} | {fmt(b['url_carries_native_id_share'])} | "
            f"{fmt(b['url_ats_hosted_share'])} | {fmt(b['key_overlap_share'])} | "
            f"{b['native_overlap']} | {b['key_overlap_native_distinct']} | "
            f"{b['native_overlap_key_distinct']} |")
    lines += ["", "## Useful? For more than one sweep?", "",
              "| Board | Evaluated | Eligible Σ | Positive eligible Σ | New final Σ "
              "| New positive Σ | Sweeps with a new positive | Top-N entries Σ "
              "| Sweeps with a top-N entry | Best rank | Replaces Σ |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, b in boards.items():
        lines.append(
            f"| {name} | {b['evaluated_runs']} | {b['eligible']['sum']} | "
            f"{b['eligible_positive']['sum']} | {b['final_new']['sum']} | "
            f"{b['final_new_positive']['sum']} | {b['runs_with_new_positive']} | "
            f"{b['top_n_new']['sum']} | {b['runs_with_top_n_entry']} | "
            f"{fmt(b['best_new_rank_seen'])} | {b['final_replaces']} |")
    lines += ["", "## Cheap enough? (per sweep, whole tranche)", "",
              f"Fetch wall median {fmt(s['fetch_wall_ms_median'])} ms "
              f"(p95 {fmt(s['fetch_wall_ms_p95'])}); evaluation CPU median "
              f"{fmt(s['evaluation_cpu_ms_median'])} ms (p95 {fmt(s['evaluation_cpu_ms_p95'])}); "
              f"evaluation wall median {fmt(s['evaluation_wall_ms_median'])} ms; "
              f"share of the production free phase median "
              f"{fmt(s['share_of_free_phase_median'])}; peak-RSS rise median "
              f"{fmt(s['rss_delta_kb_median'])} KiB; section size median "
              f"{fmt(s['section_bytes_median'])} bytes.",
              "", "## By search scope (sweeps with a new positive-score row / sweeps)", ""]
    for cohort, per in summary["cohorts"].items():
        cells = ", ".join(f"{name.split(':')[1]} {c.get('runs_with_new_positive', 0)}/"
                          f"{c.get('runs', 0)}" for name, c in per.items())
        lines.append(f"- `{cohort}`: {cells}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="telemetry records or directories")
    ap.add_argument("--json")
    ap.add_argument("--markdown")
    args = ap.parse_args()
    summary = summarise(load(args.paths))
    text = markdown(summary)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
            fh.write("\n")
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
