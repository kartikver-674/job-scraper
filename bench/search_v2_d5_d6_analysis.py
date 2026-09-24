"""V2-D5 / V2-D6: the free path's cost, its dead board, the eight shadow boards,
and provider-native identity — analysed OFFLINE from files already on disk.

    .venv/bin/python -m bench.search_v2_d5_d6_analysis \\
        --run-dir output/c5-run --evidence-dir docs/search-v2-evidence \\
        --out-dir docs/search-v2-evidence

Writes d5-free-path-analysis.json, d5-shadow-boards.json and
d6-identity-audit.json. Reads telemetry records, the kept C5 outputs, committed
evidence, config.py as text and historical output files. Makes no network call
and imports no HTTP client: the only engine import is scraper.job_key, loaded
lazily, because an identity audit of a copy of the key would audit the copy.

Every value written is a count, a clock, a board slug, a family name or a file
name — never a title, company from a row, description, URL, query or account.
"""
import argparse
import glob
import hashlib
import json
import re
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path

from bench.search_v2_c5_analysis import dist, spans, ts

LABELS = {
    "MEASURED": "counted or timed from a recorded run or committed evidence file",
    "VERIFIED": "read directly from code, config or a file's contents",
    "INFERRED": "a model with its assumptions stated beside it",
    "UNKNOWN": "the evidence does not exist; not the same as zero",
}
FEEDS = ("remoteok", "wwr", "remotive", "jobicy", "himalayas")
FAMILY_ORDER = ("lever", "greenhouse", "ashby", "smartrecruiters", "breezy",
                "wwr", "other_feeds", "optum", "enterprise")
CONCURRENT = {"lever": 4, "greenhouse": 4}          # C5 flags: Lever 1/4, Greenhouse 1/4
FUNNEL = ("observed_normalized", "post_hard_filter_and_score", "post_recency",
          "post_location_eligible")
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# What a well-formed native id looks like in each namespace's URL.
VALID = {"greenhouse": r"\d{5,12}", "lever": UUID, "ashby": UUID,
         "smartrecruiters": r"\d{6,16}", "linkedin": r"\d{6,12}",
         "indeed": r"[0-9a-f]{16}"}
RUN_FILE = re.compile(r"jobs_\d{4}-\d{2}-\d{2}_\d{4}\.json$")


def family(board):
    """Telemetry board -> the family D5 reports ("wwr" apart from other feeds)."""
    if board == "wwr":
        return "wwr"
    if board in FEEDS:
        return "other_feeds"
    return board.split(":", 1)[0]


def fifo_wall_ms(durations, workers):
    """Wall of ThreadPoolExecutor(workers) over tasks queued in registry order:
    each task starts on whichever worker frees first (sources/concurrency.py)."""
    free = [0] * max(1, workers)
    for d in durations:
        free[free.index(min(free))] += d
    return max(free) if durations else 0


def busy_s(pairs):
    """Seconds during which at least one (start, finish) was in flight — a
    family's wall even when its units are not contiguous (feeds around wwr)."""
    total, end = 0.0, None
    for s, f in sorted(pairs):
        if end is None or s > end:
            total, end = total + (f - s).total_seconds(), f
        elif f > end:
            total, end = total + (f - end).total_seconds(), f
    return round(total, 3)


def category(value):
    """Upper-cased so the evidence carries no lowercase URL-scheme token."""
    return (value or "").upper()


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# D5.1 — the free families
# ---------------------------------------------------------------------------
def family_table(units, stages, final_by_source):
    free = [u for u in units if u.get("path") == "free"]
    by = defaultdict(list)
    for u in free:
        by[family(u["board"])].append(u)
    phase = spans([(ts(u["started_at"]), ts(u["finished_at"])) for u in free])
    out = {}
    for fam in FAMILY_ORDER:
        us = by.get(fam, [])
        if not us:
            out[fam] = {"sources": 0, "label": "VERIFIED",
                        "note": "no unit in this record (family disabled or absent)"}
            continue
        pairs = [(ts(u["started_at"]), ts(u["finished_at"])) for u in us]
        seg = spans(pairs)
        wall = busy_s(pairs)
        boards = [u["board"] for u in us]
        durs = [u["duration_ms"] for u in us]
        stage = {s: sum(stages.get(s, {}).get("by_source", {}).get(b, 0) for b in boards)
                 for s in FUNNEL}
        out[fam] = {
            "label": "MEASURED",
            "sources": len(us),
            "scheduling": (f"{CONCURRENT[fam]} workers, registry-order FIFO"
                           if fam in CONCURRENT else "serial"),
            "observed_peak_in_flight": seg["peak_concurrent"],
            "ok": sum(1 for u in us if u["ok"]),
            "failed": [{"board": u["board"], "category": category(u["failure_category"]),
                        "ms": u["duration_ms"]} for u in us if not u["ok"]],
            "requests": sum(u["requests"] for u in us),
            "retries": sum(u["retries"] for u in us),
            "summed_s": round(sum(durs) / 1000, 3),
            "segment_wall_s": wall,
            "share_of_free_phase": round(wall / phase["segment_seconds"], 4),
            "board_ms": dist(durs),
            "slowest": [{"board": u["board"], "ms": u["duration_ms"], "raw": u["raw_count"]}
                        for u in sorted(us, key=lambda u: -u["duration_ms"])[:3]],
            "raw": sum(u["raw_count"] for u in us),
            "normalized": sum(u["normalized_count"] for u in us),
            "source_gate": sum(u["source_gate_count"] for u in us),
            "post_hard_filter_and_score": stage["post_hard_filter_and_score"],
            "post_recency": stage["post_recency"],
            "eligible": stage["post_location_eligible"],
            "final": sum(final_by_source.get(b, 0) for b in boards),
            "boards_with_final": {b: final_by_source[b] for b in boards
                                  if final_by_source.get(b)},
            "boards_gating_zero": sum(1 for u in us if not u["source_gate_count"]),
        }
    return out, phase


def durations(units):
    by = defaultdict(list)
    for u in units:
        if u.get("path") == "free":
            by[family(u["board"])].append(u["duration_ms"])
    return by


def what_if(by, groups, workers, inflation):
    """Per serial group: its serial wall now, and the FIFO model's wall at
    `workers`, optimistic (C5 durations) and pessimistic (x `inflation`)."""
    out = {}
    for name, fams in groups.items():
        durs = [d for f in fams for d in by.get(f, [])]
        if not durs:
            continue
        now = sum(durs)
        opt = fifo_wall_ms(durs, workers)
        pes = fifo_wall_ms([d * inflation for d in durs], workers)
        out[name] = {"serial_now_s": round(now / 1000, 3),
                     f"model_{workers}_workers_s": round(opt / 1000, 3),
                     f"model_{workers}_workers_inflated_s": round(pes / 1000, 3),
                     "floor_slowest_board_s": round(max(durs) / 1000, 3),
                     "saving_s": [round((now - pes) / 1000, 3), round((now - opt) / 1000, 3)]}
    return out


def lever_history(evidence, c5_units):
    """Lever on every dated occasion the repository recorded it."""
    http = evidence.get("current-free-http.json") or []
    audit = {r["source"]: r for r in http if r.get("source", "").startswith("lever:")}
    live = (evidence.get("free-concurrency-live.json") or {}).get("arms", {})
    c5 = {u["board"]: u for u in c5_units if u.get("family") == "lever"}
    seg = spans([(ts(u["started_at"]), ts(u["finished_at"])) for u in c5.values()])
    through = []                            # bytes per ms == kB/s
    for board, u in c5.items():
        a = audit.get(board)
        if a and a.get("response_bytes") and u["duration_ms"]:
            through.append((a["response_bytes"] / a["duration_ms"],
                            a["response_bytes"] / u["duration_ms"], board))
    big = max(through, key=lambda t: audit[t[2]]["response_bytes"]) if through else None
    return {
        "label": "MEASURED (clocks); throughput INFERRED: C5 recorded no bytes, so each "
                 "board's 2026-09-21 response size is divided by its C5 duration — raw "
                 "counts match on the largest board (918 both days)",
        "2026-09-21_serial_audit": {
            "file": "current-free-http.json", "boards": len(audit),
            "summed_s": round(sum(r["duration_ms"] for r in audit.values()) / 1000, 3),
            "veeva_ms": round((audit.get("lever:veeva") or {}).get("duration_ms", 0)),
            "response_bytes": sum(r.get("response_bytes") or 0 for r in audit.values())},
        "2026-09-22_live_benchmark": {
            "file": "free-concurrency-live.json",
            **{arm: {"wall_s": v.get("wall_seconds"), "summed_s": v.get("summed_board_seconds"),
                     "veeva_ms": (v.get("per_board_ms") or {}).get("lever:veeva"),
                     "retries": v.get("retries"), "failures": v.get("failures")}
               for arm, v in live.items() if arm in ("serial", "workers=4")}},
        "2026-09-24_c5": {
            "workers": 4, "wall_s": seg["segment_seconds"],
            "summed_s": round(sum(u["duration_ms"] for u in c5.values()) / 1000, 3),
            "veeva_ms": (c5.get("lever:veeva") or {}).get("duration_ms"),
            "retries": sum(u["retries"] for u in c5.values()),
            "requests": sum(u["requests"] for u in c5.values())},
        "per_board_kb_per_s": {
            "2026-09-21_median": round(sorted(t[0] for t in through)[len(through) // 2], 1)
            if through else None,
            "2026-09-24_c5_median": round(sorted(t[1] for t in through)[len(through) // 2], 1)
            if through else None,
            "largest_board": big and {"board": big[2],
                                      "response_bytes_2026_09_21": audit[big[2]]["response_bytes"],
                                      "2026-09-21": round(big[0], 1),
                                      "2026-09-24_c5": round(big[1], 1)}},
    }


# ---------------------------------------------------------------------------
# D5.2 — greenhouse:postman
# ---------------------------------------------------------------------------
SLUG_KEYS = ("board_id", "board", "source", "source_name", "token", "board_identifier")


def slug_mentions(obj, needle, found, file):
    """Every (provider, slug) in an evidence file whose slug contains `needle`."""
    if isinstance(obj, dict):
        for k in SLUG_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and needle in v.lower() and "/" not in v:
                prov, _, slug = v.rpartition(":")
                prov = prov or obj.get("provider") or obj.get("provider_family") or ""
                rec = found.setdefault((prov, slug), {"provider": prov, "slug": slug,
                                                      "files": set(), "facts": {}})
                rec["files"].add(file)
                for f in ("in_baseline", "company_name_status", "validation_status",
                          "status", "fetch_succeeds"):
                    if f in obj and not isinstance(obj[f], (dict, list)):
                        rec["facts"][f] = obj[f]
        for v in obj.values():
            slug_mentions(v, needle, found, file)
    elif isinstance(obj, list):
        for v in obj:
            slug_mentions(v, needle, found, file)


def postman(evidence, records, config_text, tests_dir, registry):
    """registry: {"greenhouse", "ats", "active"} board counts as C5 ran them."""
    obs = []
    for name, rec in records:
        for u in rec.get("units") or []:
            if u.get("board") == "greenhouse:postman":
                obs.append({"at": u["started_at"], "file": name, "kind": "sweep telemetry",
                            "ok": u["ok"], "category": category(u["failure_category"]),
                            "ms": u["duration_ms"], "retries": u["retries"]})
    for r in evidence.get("current-free-http.json") or []:
        if r.get("source") == "greenhouse:postman":
            obs.append({"at": r["started_at"], "file": "current-free-http.json",
                        "kind": "serial audit", "ok": r["status"] == 200,
                        "status": r["status"], "ms": round(r["duration_ms"])})
    for r in evidence.get("first-tranche-spotchecks.json") or []:
        if "/boards/postman/" in (r.get("url") or ""):
            obs.append({"at": r["observed_at"], "file": "first-tranche-spotchecks.json",
                        "kind": "spot-check", "ok": r.get("status") == 200,
                        "status": r.get("status"), "ms": round(1000 * (r.get("seconds") or 0))})
    gl = evidence.get("greenhouse-concurrency-live.json") or {}
    for arm, v in (gl.get("arms") or {}).items():
        if "greenhouse:postman" in (v.get("failures") or []):
            obs.append({"at": gl.get("observed_at"), "file": "greenhouse-concurrency-live.json",
                        "kind": f"live benchmark arm {arm}", "ok": False,
                        "category": ",".join(category(c) for c in v.get("failure_categories") or {}),
                        "ms": (v.get("per_board_ms") or {}).get("greenhouse:postman")})
    gr = evidence.get("greenhouse-concurrency-replay.json") or {}
    if "postman" in (gr.get("captured_failures") or {}):
        obs.append({"at": "2026-09-23T13:07 (date from the B4 write-up §8; not in the file)",
                    "file": "greenhouse-concurrency-replay.json", "kind": "capture pass",
                    "ok": False, "status": 404})
    obs.sort(key=lambda o: o["at"])

    line_no, line = next(((i, ln) for i, ln in enumerate(config_text.splitlines(), 1)
                          if re.search(r'"postman"\s*:', ln)), (None, None))
    found = {}
    for name, body in evidence.items():
        slug_mentions(body, "postman", found, name)
    others = [{"provider": r["provider"], "slug": r["slug"], "files": sorted(r["files"]),
               **r["facts"]} for r in found.values() if r["slug"] != "postman"]
    other_providers = [r for r in found.values()
                       if r["slug"] == "postman" and r["provider"] not in ("", "greenhouse")]
    pinned = []
    for p in sorted(Path(tests_dir).glob("test_*.py")):
        for i, ln in enumerate(p.read_text().splitlines(), 1):
            if (("ATS_BOARDS" in ln and re.search(r"\b(129|54)\)", ln))
                    or 'assertIn("postman", tokens)' in ln or "GREENHOUSE_ORDER_SHA256 = (" in ln
                    or re.search(r"assertEqual\(boards, 129\)", ln)):
                pinned.append(f"{p.parent.parent.name}/{p.parent.name}/{p.name}:{i}")
    fails = [o for o in obs if not o["ok"]]
    days = sorted({o["at"][:10] for o in obs})
    kinds = {o["kind"].split(" arm ")[0] for o in obs}
    return {
        "board": "greenhouse:postman",
        "config": {"label": "VERIFIED", "file": "config.py", "line": line_no,
                   "text": (line or "").strip(),
                   "historical_success_claims_in_config": {
                       "tokens_probed_and_resolve_2026_07_25": "probed and resolves (2026-07-25)"
                       in config_text,
                       "india_share_measured_12_of_114": "Postman 12/114" in config_text},
                   "note": "the only success claims are config comments from 2026-07-25/26; "
                           "no evidence file records a successful fetch"},
        "observations": {"label": "MEASURED", "items": obs},
        "summary": {"label": "MEASURED", "on_file": len(obs), "failures": len(fails),
                    "successes": len(obs) - len(fails),
                    "first": obs[0]["at"] if obs else None, "last": obs[-1]["at"] if obs else None,
                    "distinct_days": len(days), "kinds_of_pass": sorted(kinds),
                    "doc_reported_not_on_file": "V2-B2's three production sweeps, 404 once per "
                                                "run (B4 write-up §1); dates UNKNOWN"},
        "replacement_search": {
            "label": "VERIFIED (absence)",
            "files_scanned": len(evidence),
            "postman_on_another_provider": [r["provider"] for r in other_providers],
            "other_slugs_containing_postman": others,
            "verdict": "no committed evidence shows this employer on any other ATS or under "
                       "any other slug; the two look-alike slugs are different, unverified "
                       "identities (company name inferred from the slug)"},
        "decision": {
            "action": "remove",
            "why": f"{len(obs) - len(fails)} successes in {len(obs)} recorded fetches, "
                   f"{days[0] if days else '?'} to {days[-1] if days else '?'}, over "
                   f"{len(kinds)} kinds of pass; no trusted replacement identity exists in "
                   "the repository",
            "change": {"file": "config.py", "line": line_no,
                       "before": (line or "").strip(),
                       "after": re.sub(r'\s*"postman"\s*:\s*"[^"]*",?', "", (line or "")).strip()},
            "tests_that_pin_the_registry": pinned,
            "effect": "{g} -> {g1} Greenhouse boards, {a} -> {a1} ATS boards, {t} -> {t1} "
                      "active records; one failing unit fewer per sweep (~0.3-0.6 s, run "
                      "beside three other Greenhouse workers)".format(
                          g=registry["greenhouse"], g1=registry["greenhouse"] - 1,
                          a=registry["ats"], a1=registry["ats"] - 1,
                          t=registry["active"], t1=registry["active"] - 1),
        },
    }


# ---------------------------------------------------------------------------
# D5.3 — the eight shadow boards
# ---------------------------------------------------------------------------
def shadow_boards(evidence, records):
    replay = evidence.get("shadow-eight-board-replay.json") or {}
    live = evidence.get("shadow-b3-live-benchmark.json") or {}
    boards = list(replay.get("boards") or live.get("boards") or {})
    per = {b: [] for b in boards}
    sources = []

    if replay:
        sources.append({"id": "frozen", "at": "2026-09-21", "kind": "frozen audit snapshot, "
                        "5 synthetic cohorts", "natural": False,
                        "fields": "eligible = eligible_final_rows, positive = "
                                  "positive_score_rows, per cohort",
                        "file": "shadow-eight-board-replay.json"})
        for b in boards:
            cs = [c["by_board"][b] for c in replay["cases"].values() if b in c.get("by_board", {})]
            if not cs:
                continue
            per[b].append({"obs": "frozen", "at": "2026-09-21", "ok": cs[0]["http_status"]
                           == "valid_nonempty", "ms": round(cs[0]["request_ms"]),
                           "raw": cs[0]["raw_rows"], "retries": None,
                           "eligible_by_cohort": [c["eligible_final_rows"] for c in cs],
                           "positive_by_cohort": [c["positive_score_rows"] for c in cs],
                           "top20_entries": sum(c["enters_top20"] for c in cs),
                           "overlap_with_baseline_raw": cs[0]["overlap_with_baseline_raw"]})
    if live:
        sources.append({"id": "live", "at": live.get("observed_at"), "kind": "live public "
                        "benchmark, 5 synthetic cohorts vs frozen baseline", "natural": False,
                        "fields": "eligible = eligible, positive = final_new_positive, per "
                                  "cohort",
                        "file": "shadow-b3-live-benchmark.json"})
        cohorts = live.get("cohorts_live_shadow_vs_frozen_baseline") or {}
        for b in boards:
            v = live["boards"].get(b) or {}
            cs = [c["by_board"][b] for c in cohorts.values() if b in c.get("by_board", {})]
            per[b].append({"obs": "live", "at": live.get("observed_at"), "ok": v.get("ok"),
                           "ms": v.get("request_ms"), "retries": v.get("retries"),
                           "raw": v.get("raw"),
                           "eligible_by_cohort": [c["eligible"] for c in cs],
                           "positive_by_cohort": [c["final_new_positive"] for c in cs],
                           "top20_entries": sum(c["top_n_new"] for c in cs),
                           "native_id_distinct_of_raw": [v.get("native_id_distinct"), v.get("raw")],
                           "ids_persisted_since_2026_09_21": (v.get("vs_frozen_2026_09_21") or {})
                           .get("persisted_ids"),
                           "persisted_id_retitled": (v.get("vs_frozen_2026_09_21") or {})
                           .get("persisted_id_title_changed")})
    for name in ("shadow-b3-free-sweep-parity.json", "c0-b3-free-sweep-through-guard.json"):
        ev = evidence.get(name) or {}
        for arm, v in (ev.get("arms") or {}).items():
            got = v.get("shadow_unit_ms_requests_retries") or {}
            if not got:
                continue
            oid = f"{name.split('.')[0]}:{arm}"
            sources.append({"id": oid, "at": ev.get("observed_at"), "natural": False,
                            "kind": "controlled developer free sweep over 4 public boards; "
                                    "fetch health only (no per-board evaluation)", "file": name})
            for b, (ms, _req, retries) in got.items():
                # The file records ms/requests/retries per board, not success.
                per.setdefault(b, []).append({"obs": oid, "at": ev.get("observed_at"),
                                              "ok": None, "ms": ms, "retries": retries})
    for name, rec in records:
        ev = rec.get("shadow_evaluation") or {}
        units = {u["board"]: u for u in rec.get("shadow_units") or []}
        if not units:
            continue
        oid = f"telemetry:{rec.get('sweep_id', '')[:8]}"
        sources.append({"id": oid, "at": rec.get("started_at"), "natural": True,
                        "kind": "shadow tranche inside an integrated sweep, judged against "
                                "that sweep's real 224-source result (one synthetic default "
                                "profile)", "file": name,
                        "status": ev.get("status"), "totals": ev.get("totals"),
                        "cost_ms": {k: (ev.get("cost") or {}).get(k)
                                    for k in ("fetch_wall_ms", "evaluation_wall_ms")}})
        for b, u in units.items():
            e = (ev.get("by_board") or {}).get(b) or {}
            per.setdefault(b, []).append({
                "obs": oid, "at": u["started_at"], "ok": u["ok"], "ms": u["duration_ms"],
                "retries": u["retries"], "raw": u["raw_count"], "gated": u["source_gate_count"],
                "funnel": e.get("funnel"), "eligible": e.get("eligible"),
                "eligible_positive": e.get("eligible_positive"),
                "final_new": e.get("final_new"), "final_new_positive": e.get("final_new_positive"),
                "final_replaces": e.get("final_replaces"), "top_n_new": e.get("top_n_new"),
                "best_new_rank": e.get("best_new_rank"),
                "key_overlap_with_production": e.get("key_overlap"),
                "native_overlap_with_production": e.get("native_overlap"),
                "native_id_distinct_of_gated": [e.get("native_id_distinct"), e.get("gated")],
                "job_keys_of_gated": [e.get("source_unique"), e.get("gated")]})

    natural_ids = {s["id"] for s in sources if s["natural"]}
    out = {}
    for b, items in per.items():
        fetches = [i for i in items if "ms" in i and i["ms"] is not None]
        nat = [i for i in items if i["obs"] in natural_ids]
        nat_pos = sum(1 for i in nat if (i.get("final_new_positive") or 0) > 0)
        synth_pos = [sum(1 for p in i.get("positive_by_cohort", []) if p > 0)
                     for i in items if "positive_by_cohort" in i]
        out[b] = {
            "observations": items,
            "summary": {
                "label": "MEASURED",
                "dates": sorted({(i["at"] or "")[:10] for i in items}),
                "fetches": len(fetches), "fetch_ok": sum(1 for i in fetches if i["ok"]),
                "fetch_failures": sum(1 for i in fetches if i["ok"] is False),
                "fetch_outcome_not_recorded": sum(1 for i in fetches if i["ok"] is None),
                "retries": sum(i.get("retries") or 0 for i in fetches),
                "ms": dist([i["ms"] for i in fetches]),
                "raw_range": [min(i["raw"] for i in items if i.get("raw") is not None),
                              max(i["raw"] for i in items if i.get("raw") is not None)],
                "natural_observations": len(nat),
                "natural_with_new_positive_final": nat_pos,
                "natural_final_new": sum(i.get("final_new") or 0 for i in nat),
                "natural_final_new_positive": sum(i.get("final_new_positive") or 0 for i in nat),
                "natural_top20_new": sum(i.get("top_n_new") or 0 for i in nat),
                "natural_overlap_with_production": sum(
                    (i.get("key_overlap_with_production") or 0)
                    + (i.get("native_overlap_with_production") or 0) for i in nat),
                "synthetic_cohorts_with_positive": synth_pos,
            },
            "decision": {"action": "keep_shadow",
                         "why": f"{len(nat)} natural observation(s); promotion needs repeated "
                                "natural evidence and one is never enough"},
        }
    return sources, out


# ---------------------------------------------------------------------------
# D6 — provider-native identity
# ---------------------------------------------------------------------------
def url_native_id(source, url):
    """(namespace, id, well_formed) parsed from an exported apply URL, or None
    when the namespace does not publish one there. Namespace = provider family,
    never the board: the providers' ids are not board-scoped."""
    ns = (source or "").split(":", 1)[0]
    try:
        p = urllib.parse.urlsplit(url or "")
    except ValueError:
        return (ns, "", False) if ns in VALID else None
    q = urllib.parse.parse_qs(p.query)
    got = ""
    if ns == "greenhouse":
        got = (q.get("gh_jid") or [""])[0] or (re.findall(r"/jobs/(\w+)", p.path) or [""])[-1]
    elif ns in ("lever", "ashby"):
        got = (re.findall(UUID, p.path) or re.findall(r"/[^/]+/([^/]+)/?$", p.path) or [""])[0]
    elif ns == "smartrecruiters":
        got = (re.findall(r"/[^/]+/([^/-]+)", p.path) or [""])[0]
    elif ns == "linkedin":
        got = (re.findall(r"(\d+)/?$", p.path) or [""])[0]
    elif ns == "indeed":
        got = (q.get("jk") or [""])[0]
    else:
        return None
    return ns, got, bool(got) and re.fullmatch(VALID[ns], got) is not None


def drift_kind(keys):
    kinds = {k[0] if k else None for k in keys}
    if kinds != {"ct"}:
        return "key_kind"
    companies, titles = {k[1] for k in keys}, {k[2] for k in keys}
    if len(companies) == 1:
        return "title"
    return "company" if len(titles) == 1 else "company_and_title"


def identity_across_runs(runs, key_fn):
    """runs: [(run_name, rows)]. Within one namespace: ids seen under >1 key
    (drift) and keys covering >1 id; plus raw ids that recur across namespaces."""
    keys, where = defaultdict(set), defaultdict(set)
    rows_n, coverage = 0, Counter()
    within_run_drift = 0
    for name, rows in runs:
        per_run = defaultdict(set)
        for r in rows:
            rows_n += 1
            got = url_native_id(r.get("source_site"), r.get("apply_url"))
            if got is None:
                coverage["no_native_namespace"] += 1
                continue
            ns, nid, ok = got
            coverage[f"{ns}:{'valid' if ok else 'missing_or_malformed'}"] += 1
            if not ok:
                continue
            k = key_fn(r)
            keys[(ns, nid)].add(k)
            where[(ns, nid)].add(name)
            per_run[(ns, nid)].add(k)
        within_run_drift += sum(1 for ks in per_run.values() if len(ks) > 1)
    recurring = [i for i in where if len(where[i]) > 1]
    drifted = [i for i in recurring if len(keys[i]) > 1]
    by_key = defaultdict(set)
    for (ns, nid), ks in keys.items():
        for k in ks:
            by_key[(ns, k)].add(nid)
    namespaces = defaultdict(set)
    for ns, nid in keys:
        namespaces[nid].add(ns)
    return {
        "runs": len(runs), "rows": rows_n, "coverage": dict(sorted(coverage.items())),
        "native_ids": len(keys), "ids_in_2plus_runs": len(recurring),
        "ids_in_2plus_runs_by_namespace": dict(Counter(i[0] for i in recurring)),
        "same_id_different_job_key": len(drifted),
        "same_id_different_job_key_by_namespace": dict(Counter(i[0] for i in drifted)),
        "drift_rate_by_namespace": {
            ns: round(n / total, 4) for ns, total in Counter(i[0] for i in recurring).items()
            for n in [sum(1 for i in drifted if i[0] == ns)]},
        "drift_kind": dict(Counter(drift_kind(keys[i]) for i in drifted)),
        "same_id_different_job_key_within_one_run": within_run_drift,
        "same_job_key_several_ids_by_namespace": dict(Counter(
            ns for (ns, _k), ids in by_key.items() if len(ids) > 1)),
        "raw_id_in_several_namespaces": sum(1 for s in namespaces.values() if len(s) > 1),
    }


def seen_ledgers(paths, seen_key_fn):
    """Does each well-formed seen.tsv entry still mean what it meant: does its
    stored key equal the current key recomputed from its own title/company?"""
    c = Counter()
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]) \
                    or parts[1].split("|", 1)[0] not in ("ct", "req", "url"):
                c["malformed_lines"] += 1
                continue
            kind = parts[1].split("|", 1)[0]
            if kind != "ct" or len(parts) < 4:
                c[f"{kind}_not_recomputable"] += 1
                continue
            same = seen_key_fn({"title": parts[2], "company": parts[3]}) == parts[1]
            c["ct_recomputes_identically" if same else "ct_recomputes_differently"] += 1
    return {"ledgers": len(paths), **dict(sorted(c.items()))}


def c5_identity(record):
    """Per family, from the C5 identity section: native-id coverage, and the
    rows current job_key collapsed inside one board although each carried a
    native id (single-response boards, where ids are one per listed posting)."""
    ident = (record.get("identity") or {}).get("by_source") or {}
    reqs = {u["board"]: u["requests"] for u in record.get("units") or []}
    out = defaultdict(Counter)
    for src, r in ident.items():
        a = out[src.split(":", 1)[0]]           # namespace: each feed is its own
        a["sources"] += 1
        a["rows"] += r["rows"]
        a["job_keys"] += r["source_unique"]
        a["with_native_id"] += r["native"].get("native_id", 0)
        a["unkeyed"] += r["unkeyed"]
        a["already_seen_earlier_source"] += r["source_unique"] - r["newly_unique_vs_sweep"]
        collapsed = r["rows"] - r["source_unique"]
        if r["native"].get("native_id") == r["rows"] and reqs.get(src) == 1:
            a["collapsed_with_native_id_single_response"] += collapsed
        else:
            a["collapsed_other"] += collapsed
    return {f: dict(v, native_missing_rate=round(1 - v["with_native_id"] / v["rows"], 4))
            for f, v in out.items() if v["rows"]}


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default="output/c5-run")
    ap.add_argument("--evidence-dir", default="docs/search-v2-evidence")
    ap.add_argument("--out-dir", default="docs/search-v2-evidence")
    ap.add_argument("--telemetry-glob", default="output/**/telemetry/*.json")
    ap.add_argument("--history-root", default="output")
    ap.add_argument("--config", default="config.py")
    ap.add_argument("--tests-dir", default="sweep/tests")
    a = ap.parse_args()
    import scraper                      # job_key only; see the module docstring

    run, ev_dir = Path(a.run_dir), Path(a.evidence_dir)
    evidence = {p.name: load(p) for p in sorted(ev_dir.glob("*.json"))
                if not p.name.startswith(("d5-", "d6-"))}
    evidence = {k: v for k, v in evidence.items() if v is not None}
    tele = set(glob.glob(a.telemetry_glob, recursive=True))
    tele |= {str(p) for p in run.glob("telemetry/*.json")}
    records = sorted(((Path(p).name, load(p)) for p in tele),
                     key=lambda r: r[1].get("started_at", ""))
    here = {p.name for p in run.glob("telemetry/*.json")}
    c5_name, c5 = next((n, r) for n, r in records if n in here
                       and any(u.get("path") == "free" for u in r.get("units") or []))
    final_path = sorted(run.glob("jobs_*.json"))[-1]
    final = load(final_path)
    final_by_source = Counter(r.get("source_site") for r in final)

    # ---------------- D5 ----------------
    fams, phase = family_table(c5["units"], c5["stages"], final_by_source)
    m = c5["milestones"]
    free_ms = m["free_phase_done"] - m["free_phase_start"]
    free_units = [u for u in c5["units"] if u.get("path") == "free"]
    gl = (evidence.get("greenhouse-concurrency-live.json") or {}).get("arms", {})
    inflation = round(gl["workers=4"]["summed_board_seconds"]
                      / gl["serial"]["summed_board_seconds"], 3) if gl else 1.0
    by_fam = durations(c5["units"])
    model = what_if(by_fam, {"ashby": ["ashby"], "smartrecruiters": ["smartrecruiters"],
                             "breezy": ["breezy"], "feeds": ["wwr", "other_feeds"]},
                    4, inflation)
    audit = evidence.get("current-free-http.json") or []
    ashby_med = sorted(r["duration_ms"] for r in audit if r["source"].startswith("ashby:"))
    ashby_med = ashby_med[len(ashby_med) // 2] if ashby_med else 0
    stragglers = [{"board": r["source"], "ms": round(r["duration_ms"])} for r in audit
                  if r["source"].startswith("ashby:") and r["duration_ms"] > 10 * ashby_med]
    sr_bytes = sorted(r["response_bytes"] for r in audit
                      if r["source"].startswith("smartrecruiters:"))
    lever = fams["lever"]
    slowest = max(free_units, key=lambda u: u["duration_ms"])
    lever_start = min(ts(u["started_at"]) for u in free_units if u["family"] == "lever")
    serial_fams = [f for f in FAMILY_ORDER if fams[f]["sources"] and f not in CONCURRENT]
    history_lever = lever_history(evidence, free_units)
    b2_wall = (history_lever["2026-09-22_live_benchmark"].get("workers=4") or {}).get("wall_s")
    registry = {"greenhouse": fams["greenhouse"]["sources"],
                "ats": sum(fams[f]["sources"] for f in FAMILY_ORDER
                           if f not in ("wwr", "other_feeds")),
                "active": len(free_units)}
    d5 = {
        "schema": "search-v2d5.1",
        "status": "MEASURED OFFLINE from recorded telemetry and committed evidence; "
                  "no network, no paid call, no production file changed",
        "generated_from": [c5_name, final_path.name] + sorted(evidence) + [a.config],
        "evidence_labels": LABELS,
        "telemetry_records": {"label": "MEASURED", "items": [
            {"file": n, "started_at": r.get("started_at"), "path": r.get("path"),
             "free_units": sum(1 for u in r.get("units") or [] if u.get("path") == "free"),
             "shadow_units": len(r.get("shadow_units") or [])} for n, r in records]},
        "free_phase": {"label": "MEASURED", "ms": free_ms,
                       "from_unit_clocks_s": phase["segment_seconds"],
                       "share_of_sweep": round(free_ms / c5["duration_ms"], 4),
                       "record_sources_attempted_incl_paid": c5.get("sources_attempted"),
                       "record_sources_failed_incl_paid": c5.get("sources_failed"),
                       "sources_attempted": len(free_units),
                       "sources_ok": sum(1 for u in free_units if u["ok"]),
                       "retries": sum(u["retries"] for u in free_units),
                       "rate_limited_429": sum(1 for u in free_units
                                               if "429" in (u["failure_category"] or ""))},
        "scheduling": {
            "label": "VERIFIED (sources/__init__.py, sources/concurrency.py; C5 flags from the "
                     "C5 write-up §1) and MEASURED (observed peak in flight per family)",
            "order": "families one after another in ATS_BOARDS order, then feeds, then optum "
                     "and enterprise (both disabled in config)",
            "concurrent": {f: f"{w} workers; boards queued in registry order, results "
                              "collected in registry order" for f, w in CONCURRENT.items()},
            "serial": serial_fams,
            "timeout": "25 s is a per-socket-operation timeout (urlopen timeout), not a "
                       "per-board deadline: a steady slow body read never trips it",
        },
        "families": fams,
        "dominance": {
            "label": "MEASURED",
            "lever_segment_s": lever["segment_wall_s"],
            "lever_share_of_free_phase": lever["share_of_free_phase"],
            "slowest_board": slowest["board"],
            "slowest_board_ms": slowest["duration_ms"],
            "slowest_board_share_of_free_phase": round(slowest["duration_ms"] / free_ms, 4),
            "slowest_board_queued_behind_s": round(
                (ts(slowest["started_at"]) - lever_start).total_seconds(), 3),
            "serial_families_s": round(sum(fams[f]["segment_wall_s"] for f in serial_fams), 3),
            "finding": "the Lever segment is {:.0%} of the free phase and one board's fetch "
                       "alone {:.0%}; every other family together is {:.0%}".format(
                           lever["share_of_free_phase"], slowest["duration_ms"] / free_ms,
                           1 - lever["share_of_free_phase"]),
        },
        "slowest_sources": {"label": "MEASURED", "items": [
            {"board": u["board"], "ms": u["duration_ms"], "raw": u["raw_count"],
             "requests": u["requests"], "retries": u["retries"]}
            for u in sorted(free_units, key=lambda u: -u["duration_ms"])[:10]]},
        "lever_across_dates": history_lever,
        "fifo_model": {
            "label": "VERIFIED against C5 (check: model_s vs measured_s); what-if INFERRED",
            "assumption": "a board takes the same time with 4 in flight; the inflated column "
                          f"multiplies every duration by {inflation} (Greenhouse's measured "
                          "summed-time growth from serial to 4 workers, greenhouse-"
                          "concurrency-live.json)",
            "check": {f: {"measured_s": fams[f]["segment_wall_s"],
                          "model_s": round(fifo_wall_ms(d, CONCURRENT.get(f, 1)) / 1000, 3)}
                      for f, d in by_fam.items()},
            "what_if_serial_groups": model,
            "why_not_lever_or_greenhouse": "already at 4; each segment's floor is its slowest "
                                           "board (see floor in families.*.slowest)",
        },
        "benchmark": {
            "label": "INFERRED; NOT RUN",
            "ashby_smartrecruiters": {
                "justified": True,
                "why": "the two largest serial blocks (C5 {:.1f} s + {:.1f} s); SmartRecruiters "
                       "is latency-bound (C5 median {} ms per board; median response {} bytes "
                       "on 2026-09-21) and Ashby's 2026-09-21 audit had {} body reads over 10x "
                       "its median that serial waits out in full; model saving ~{:.0f}-{:.0f} s "
                       "per sweep at 4 workers, small next to C5's {:.1f} s free phase but "
                       "large next to a normal-day one (Lever {} s at 4 workers on "
                       "2026-09-22)".format(
                           fams["ashby"]["segment_wall_s"],
                           fams["smartrecruiters"]["segment_wall_s"],
                           fams["smartrecruiters"]["board_ms"]["median"],
                           sr_bytes[len(sr_bytes) // 2] if sr_bytes else None, len(stragglers),
                           model["ashby"]["saving_s"][0] + model["smartrecruiters"]["saving_s"][0],
                           model["ashby"]["saving_s"][1] + model["smartrecruiters"]["saving_s"][1],
                           free_ms / 1000, b2_wall),
                "ashby_2026_09_21_stragglers": stragglers,
                "prerequisite": "bench/search_v2_free_concurrency.py drives concurrency.ENV["
                                "provider], which admits only lever and greenhouse: each family "
                                "needs its own default-off switch first, a separately reviewed "
                                "change (VERIFIED)",
                "would_test": [
                    "capture once, then socket-denied replay at serial/1/2/4: source "
                    "sequence, acquired and final SHA-256, and the survivor of a synthetic "
                    "cross-board twin identical to serial",
                    "live arms serial/2/4 on one host, 2 passes at different hours: wall, "
                    "summed board seconds, per-board median/p95/max, failure categories, "
                    "retries, 429s, peak RSS",
                    "whether an Ashby straggler recurs, and what it costs serial vs 4 workers "
                    "when it is not the last board",
                    "never above 4 workers; stop on any 429 or new failure category"],
                "not_justified": {"breezy": f"{fams['breezy']['sources']} boards, "
                                            f"{fams['breezy']['segment_wall_s']} s"},
                "deferred": {"feeds": "a different code path (sources.FEED_FETCHERS, not the "
                                      "board executor), five hosts, and WWR/Himalayas page "
                                      "sequentially inside one source; model saving "
                                      f"~{model['feeds']['saving_s'][0]:.0f}-"
                                      f"{model['feeds']['saving_s'][1]:.0f} s"},
            },
            "lever_remeasure": {
                "justified": True, "kind": "latency re-measurement, not a concurrency change",
                "why": "C5's Lever wall is {:.1f}x 2026-09-22's at the same 4 workers with "
                       "0 retries; its floor is one board, so no worker count helps; cause "
                       "UNKNOWN (provider-side throttling INFERRED: Greenhouse's median board "
                       "took {} ms on the same host seconds later)".format(
                           lever["segment_wall_s"] / b2_wall if b2_wall else float("nan"),
                           fams["greenhouse"]["board_ms"]["median"]),
                "would_test": "a serial Lever-only pass, repeated at 2-3 times of day: per "
                              "board response bytes, time to headers vs body-read time, and "
                              "whether C5's ~{} kB/s median per-connection rate recurs".format(
                                  history_lever["per_board_kb_per_s"]["2026-09-24_c5_median"]),
            },
            "workers_above_4": "not recommended: no evidence; the C5 floor is one board",
        },
        "postman": postman(evidence, records, Path(a.config).read_text(), a.tests_dir,
                           registry),
        "other_free_findings": {
            "label": "MEASURED",
            "smartrecruiters_boards_at_100_row_page_cap": [
                u["board"] for u in free_units
                if u["family"] == "smartrecruiters" and u["raw_count"] == 100],
            "families_with_zero_final": [f for f in FAMILY_ORDER
                                         if fams[f]["sources"] and not fams[f]["final"]],
            "note": "SmartRecruiters pagination is its own evidence file "
                    "(smartrecruiters-pagination.json), unchanged here",
        },
        "decision": {
            "dominant_cost": f"{slowest['board']}'s single fetch on a slow Lever day; not "
                             "worker count",
            "postman": "remove the config entry (no verified replacement)",
            "concurrency": "benchmark Ashby + SmartRecruiters (Free-only, after their own "
                           "default-off switches); do not raise Lever/Greenhouse above 4",
            "lever": "re-measure before changing anything",
        },
    }

    # ---------------- D5 shadow ----------------
    sources, boards = shadow_boards(evidence, records)
    d5s = {
        "schema": "search-v2d5-shadow.1",
        "status": "MEASURED OFFLINE; one natural in-sweep evaluation exists (C5)",
        "generated_from": sorted({s["file"] for s in sources}),
        "evidence_labels": LABELS,
        "not_counted_separately": {"shadow-b3-frozen-equivalence.json":
                                   "re-evaluates the same 2026-09-21 snapshot"},
        "observations": {"label": "MEASURED", "items": sources},
        "boards": boards,
        "decision": {
            "promote": [], "keep_shadow": sorted(boards),
            "rule": "promote only on repeated natural evidence; one observation is never "
                    "enough, and synthetic cohorts or developer sweeps are not natural",
            "natural_observations": sum(1 for s in sources if s["natural"]),
            "closest_to_the_bar": sorted(
                (b for b, v in boards.items()
                 if v["summary"]["natural_with_new_positive_final"]
                 and sum(1 for n in v["summary"]["synthetic_cohorts_with_positive"] if n) >= 2),
                key=lambda b: -boards[b]["summary"]["natural_final_new_positive"]),
            "revisit_when": "the B3 experiment's 10-20 natural sweeps exist "
                            "(bench/search_v2_shadow_production.py over their records)",
        },
    }

    # ---------------- D6 ----------------
    hist_files, digests = [], set()
    for p in sorted(glob.glob(f"{a.history_root}/**/jobs_*.json", recursive=True)):
        if not RUN_FILE.search(p):
            continue
        digest = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        if digest not in digests:
            digests.add(digest)
            hist_files.append(p)
    runs = [(p, rows) for p in hist_files if isinstance(rows := load(p), list)]
    dates = sorted(RUN_FILE.search(p).group(0)[5:15] for p, _ in runs)
    history = identity_across_runs(runs, scraper.job_key)
    c5_final = identity_across_runs([(final_path.name, final)], scraper.job_key)
    seen_paths = sorted(glob.glob(f"{a.history_root}/**/seen.tsv", recursive=True))
    ledger = seen_ledgers(seen_paths, scraper._seen_key)
    ids = c5_identity(c5)
    split = sum(v.get("collapsed_with_native_id_single_response", 0) for v in ids.values())
    live = (evidence.get("shadow-b3-live-benchmark.json") or {}).get("boards") or {}
    persisted = sum((v.get("vs_frozen_2026_09_21") or {}).get("persisted_ids", 0)
                    for v in live.values())
    retitled = sum((v.get("vs_frozen_2026_09_21") or {}).get("persisted_id_title_changed", 0)
                   for v in live.values())
    shadow_ev = c5.get("shadow_evaluation") or {}
    cx = evidence.get("identity-counterexamples.json") or {}
    d6 = {
        "schema": "search-v2d6.1",
        "status": "MEASURED OFFLINE; native ids handled only as counts",
        "generated_from": [c5_name, final_path.name, "identity-counterexamples.json",
                           "shadow-b3-live-benchmark.json",
                           f"{len(runs)} dated output files under {a.history_root}/",
                           f"{len(seen_paths)} seen.tsv ledgers under {a.history_root}/"],
        "evidence_labels": LABELS,
        "code": {
            "label": "VERIFIED",
            "job_key": "req_number if present -> ('req', req); else normalized company "
                       "(corporate suffixes stripped) + order-insensitive title -> ('ct', c, t); "
                       "else host+path of the URL -> ('url', u); else None (never deduped)",
            "dedupe": "rank_rows: stable sort by score descending, then the first row per "
                      "job_key survives; unkeyed rows all survive",
            "survivor": "highest score; on a tie, arrival order: paid rows first in plan "
                        "order, then free in registry order (lever, greenhouse, ashby, "
                        "smartrecruiters, breezy, feeds); concurrency keeps registry order",
            "cross_provider": "the same job_key across providers is one posting — the only "
                              "cross-provider identity the engine has",
            "native_capture": "sources/ats.py and sources/feeds.py stash '_native' ONLY while "
                              "telemetry is active; paid rows (linkedin, indeed) get none",
            "exports": "to_output() never reads '_native' (asserted in scraper's self-check); "
                       "CSV/JSON carry apply_url, in which most ATS ids are visible",
            "namespace_precedent": "sources/shadow.py already compares (provider family, "
                                   "native_id), never the raw id alone",
            "seen_ledger": "seen.tsv lines are date<TAB>'|'.join(job_key)<TAB>title<TAB>company, "
                           "appended by record_seen from OUTPUT rows; read by --only-new, which "
                           "the worker never passes",
            "other_ledgers": "sweep applied.tsv and the live feed key on scraper._seen_key of "
                             "output rows; .done_combos keys search combos (date|site|keyword|"
                             "location), not postings",
        },
        "c5_native_availability": {"label": "MEASURED (identity section, gated rows)",
                                   "by_family": ids},
        "c5_final_url_ids": {"label": "MEASURED (201 exported rows; id parsed from apply_url)",
                             **c5_final},
        "c5_within_sweep": {
            "label": "MEASURED; distinct ids inside one board INFERRED except the eight shadow "
                     "boards, where C5 VERIFIED native_id_distinct == gated",
            "same_native_id_different_job_key": {
                "final_rows": c5_final["same_id_different_job_key_within_one_run"],
                "shadow_vs_production": sum(v.get("native_overlap_key_distinct", 0)
                                            for v in (shadow_ev.get("by_board") or {}).values())},
            "same_job_key_distinct_native_ids_at_gate": split,
            "sweep_job_keys_at_gate": (c5.get("identity") or {}).get("sweep_unique_keys"),
            "shadow_boards_job_keys_vs_distinct_ids": {
                b: [v.get("source_unique"), v.get("native_id_distinct")]
                for b, v in (shadow_ev.get("by_board") or {}).items()},
        },
        "across_time": {
            "label": "MEASURED",
            "history": {"dates": [dates[0], dates[-1]] if dates else None, **history},
            "b3_two_day_persistence": {"file": "shadow-b3-live-benchmark.json",
                                       "persisted_ids": persisted, "retitled": retitled,
                                       "rate": round(retitled / persisted, 4) if persisted else None},
        },
        "seen_ledger": {"label": "MEASURED", **ledger},
        "counterexamples": {"label": "VERIFIED (synthetic; no prevalence)",
                            "cases": {k: {"input": v["input"], "output": v["output"]}
                                      for k, v in (cx.get("cases") or {}).items()}},
        "concept": {
            "definition": "native_identity = (provider family, native id) inside one "
                          "provider, job_key when the id is missing or malformed, cross-"
                          "provider identity unchanged",
            "split_effect_in_c5": {
                "label": "MEASURED upper bound at the gate; final effect UNKNOWN",
                "rows": split,
                "meaning": "rows the current key merges inside one board although each has its "
                           "own id (mostly one title posted in several locations): native-first "
                           "identity would show them separately — the opposite of job_key's "
                           "deliberate 'not on location' rule, and a product decision"},
            "merge_effect_in_c5": {"label": "MEASURED", "rows": c5_final[
                "same_id_different_job_key_within_one_run"],
                "meaning": "within one sweep a board lists an id once, so a merge-only rule "
                           "changes nothing"},
            "merge_effect_across_time": {
                "label": "MEASURED",
                "history_rate": round(history["same_id_different_job_key"]
                                      / history["ids_in_2plus_runs"], 4)
                if history["ids_in_2plus_runs"] else None,
                "b3_two_day_rate": round(retitled / persisted, 4) if persisted else None,
                "who_benefits": "seen.tsv (--only-new, CLI only) and applied.tsv (local sweep "
                                "UI); nothing on the public worker path"},
        },
        "migration": {
            "label": "VERIFIED (code) + MEASURED (ledgers)",
            "seen_tsv": "old entries stay meaningful: every well-formed 'ct' entry recomputes "
                        "to its stored key under current job_key; a native key would have to "
                        "be ADDED beside it (dual lookup), never replace it",
            "historical_outputs": "carry no native id; apply_url yields one for greenhouse, "
                                  "lever, ashby, smartrecruiters, linkedin and indeed rows only",
            "done_combos": "untouched by any identity change (keys search combos)",
            "blocker": "native ids exist only when telemetry is on, so dedupe on them would make "
                       "results depend on a diagnostics flag until capture is unconditional",
        },
        "decision": {
            "choice": "b",
            "action": "leave native identity diagnostic-only",
            "why": [
                "C5 would have merged 0 more rows (same id under two keys: 0 in the final "
                "set and 0 between shadow and production)",
                f"native-first identity would instead SPLIT up to {split} gated rows C5 "
                "merged, reversing the deliberate multi-location collapse",
                "the real drift is across time ({} of {} recurring ids in history, {} of {} in "
                "two days on the shadow boards), and only CLI/local ledgers that the public "
                "worker never reads would gain".format(
                    history["same_id_different_job_key"], history["ids_in_2plus_runs"],
                    retitled, persisted),
                "capture is telemetry-gated and paid rows carry no native id",
            ],
            "revisit_when": "a user-facing ledger (applied, dismissed, new-since-last) ships on "
                            "the worker path; then add a merge-only dual lookup keyed "
                            "(family, id) beside job_key, with unconditional capture",
        },
    }

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, body in (("d5-free-path-analysis.json", d5), ("d5-shadow-boards.json", d5s),
                       ("d6-identity-audit.json", d6)):
        (out / name).write_text(json.dumps(body, indent=2, default=str) + "\n")
    print(json.dumps({"free_phase_ms": free_ms, "lever_share": lever["share_of_free_phase"],
                      "postman_observations": d5["postman"]["summary"]["on_file"],
                      "shadow_natural": d5s["decision"]["natural_observations"],
                      "d6_choice": d6["decision"]["choice"]}, indent=2))


if __name__ == "__main__":
    main()
