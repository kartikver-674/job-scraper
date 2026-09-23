"""Search V2-B3 measurements of the shadow evaluator. AUDIT-ONLY.

--frozen   OFFLINE, sockets denied. For each of V2-A's five frozen cohorts, the
           production evaluator (sources.shadow.evaluate) is run against the
           frozen baseline's real result, and V2-A's own offline method —
           finalize(baseline + shadow) — is run beside it. Every board, stage
           and count must agree. Also measures what the evaluator costs next to
           the production finalize pass it follows.

--live     NETWORK: public GETs to the eight boards, nothing else, no Apify.
           One pass through the UNCHANGED adapter: request time, failures,
           raw/normalized rows, native identity, overlap with the frozen
           baseline, which native ids persisted since the 2026-09-21 frozen
           snapshot — then the live rows judged under each frozen cohort
           against that cohort's frozen baseline result. `--reach N` also GETs
           N sampled job pages per board, at the published URL and at the
           provider's own URL for the same native id.

--sweep    NETWORK, FREE ONLY. Worker-shaped `scraper.py --profile X --yes`
           child processes over four public boards: shadow off, on, on with
           production's Lever settings, and every flag off. The user's CSV and
           JSON must be byte-identical in all four. Each child runs through
           bench/paid_guard.py, which refuses any paid plan this harness cannot
           authorise; PAID_MARKERS kills a child on top of that.

    .venv/bin/python -m bench.search_v2_shadow_b3 --frozen \\
        --baseline /tmp/search-v2-current-free-jobs.json \\
        --expansion /tmp/search-v2-expansion-jobs.json \\
        --output docs/search-v2-evidence/shadow-b3-frozen-equivalence.json
    .venv/bin/python -m bench.search_v2_shadow_b3 --live --reach 1 \\
        --baseline ... --expansion ... \\
        --output docs/search-v2-evidence/shadow-b3-live-benchmark.json

One live pass is one dated observation. It is not longitudinal evidence.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GH_ID = re.compile(r"(?:gh_jid=|/jobs/)(\d+)")


def _prepare_env(offline):
    os.environ.pop("JOB_PROFILE", None)
    os.environ["SWEEP_EXPERIENCE_MISMATCH_GUARD"] = "0"
    for key in [k for k in os.environ if k.startswith("APIFY_TOKEN")]:
        del os.environ[key]
    if offline:
        def deny(*a, **kw):
            raise RuntimeError("Offline audit: network forbidden")
        socket.socket.connect = deny
        socket.create_connection = deny
    sys.path[:0] = [str(ROOT), str(ROOT / "auto-apply")]


def _load(path):
    raw = Path(path).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _hires_home(scraper, rows):
    """Board-level, over the unfiltered board, per cohort — as V2-A's replay."""
    home = defaultdict(bool)
    for row in rows:
        home[row.get("Source")] |= scraper.is_home_location(row.get("Location", ""))
    for row in rows:
        if ":" in row.get("Source", ""):
            row["hires_home"] = "yes" if home[row["Source"]] else "no"


def _gate(scraper, rows):
    return [dict(r) for r in rows if scraper.is_dev_title(r.get("Title", ""))
            and scraper.location_allowed(r.get("Location", ""))]


class _record:
    """An open telemetry record in a throwaway directory."""

    def __enter__(self):
        import telemetry
        self.telemetry, self.tmp = telemetry, tempfile.mkdtemp()
        os.environ[telemetry.FLAG] = "1"
        telemetry.start("free", self.tmp)
        return telemetry.record()

    def __exit__(self, *exc):
        import shutil
        self.telemetry.finish()
        os.environ.pop(self.telemetry.FLAG, None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


def _evaluate(scraper, shadow, final, production, held):
    """(section, cpu seconds, wall seconds) for one evaluator pass."""
    with _record() as record:
        cpu, wall = time.process_time(), time.perf_counter()
        shadow.evaluate(held, scraper.shadow_engine(final, production))
        cpu, wall = time.process_time() - cpu, time.perf_counter() - wall
        return copy.deepcopy(record["shadow_evaluation"]), cpu, wall


def frozen(args):
    _prepare_env(offline=True)
    from bench.search_v2_shadow import CASES, load_engine
    from sources import shadow
    tokens = [f"greenhouse:{t}" for t in shadow.BOARDS["greenhouse"]]
    baseline, baseline_sha = _load(args.baseline)
    expansion, expansion_sha = _load(args.expansion)
    shadow_rows = [r for r in expansion if r.get("Source") in tokens]

    cases, agree = {}, True
    for case, scope in CASES:
        scraper, policy_sha = load_engine(case, scope)
        for rows in (baseline, shadow_rows):
            _hires_home(scraper, rows)
        prep = lambda rows: [scraper._truncate_desc(dict(r)) for r in rows]  # noqa: E731
        base_gated = _gate(scraper, baseline)
        held = [(b, _gate(scraper, [r for r in shadow_rows if r["Source"] == b]))
                for b in tokens]

        # The production pass, exactly as main() runs it, then the evaluator.
        production = prep(base_gated)
        cpu = time.process_time()
        final = scraper.finalize(production)
        prod_cpu = time.process_time() - cpu
        section, eval_cpu, eval_wall = _evaluate(scraper, shadow, final,
                                                 production, held)

        # V2-A's offline method: both row sets through one finalize.
        with _record() as record:
            truth = scraper.finalize(prep(base_gated)
                                     + prep([r for _, rows in held for r in rows]))
            stages = copy.deepcopy(record["stages"])
        base_keys = {scraper.job_key(r) for r in final}

        per_board, mismatches = {}, []
        for board in tokens:
            mine = [r for r in truth if r["source_site"] == board]
            new = [r for r in mine if scraper.job_key(r) not in base_keys]
            expect = {
                "eligible": stages["post_location_eligible"]["by_source"].get(board, 0),
                "final_new": len(new),
                "final_replaces": len(mine) - len(new),
                "final_new_positive": sum(r["score"] > 0 for r in new),
                "top_n_new": sum(1 for r in truth[:shadow.TOP_N] if r in new),
            }
            got = section["by_board"][board]
            if {k: got[k] for k in expect} != expect:
                mismatches.append({"board": board, "offline": expect,
                                   "live": {k: got[k] for k in expect}})
            for stage, counts in stages.items():
                if stage != "final_after_dedupe" and \
                        got["funnel"].get(stage) != counts["by_source"].get(board, 0):
                    mismatches.append({"board": board, "stage": stage})
            per_board[board] = {k: got[k] for k in (
                "gated", "fresh", "eligible", "eligible_positive", "final_new",
                "final_new_positive", "final_replaces", "top_n_new",
                "best_new_rank", "key_overlap")}
        totals = section["totals"]
        if totals["combined_final"] != len(truth):
            mismatches.append({"combined_final": [totals["combined_final"], len(truth)]})
        agree &= not mismatches
        cases[f"{case}/{scope}"] = {
            "policy_sha256": policy_sha,
            "production_gated_rows": len(base_gated),
            "shadow_gated_rows": section["rows_evaluated"],
            "totals": totals,
            "offline_combined_final": len(truth),
            "live_equals_offline": not mismatches,
            "mismatches": mismatches,
            "cost_seconds": {
                "production_finalize_cpu_one_pass": round(prod_cpu, 3),
                "production_free_tail_cpu_two_passes": round(2 * prod_cpu, 3),
                "evaluator_cpu": round(eval_cpu, 3),
                "evaluator_wall": round(eval_wall, 3),
                "evaluator_share_of_free_tail_cpu": round(eval_cpu / (2 * prod_cpu), 4)
                if prod_cpu else None,
            },
            "by_board": per_board,
        }
        print(f"{case}/{scope:<8} final {totals['baseline_final']:>4} -> "
              f"{totals['combined_final']:<4} positive {totals['baseline_positive']} -> "
              f"{totals['combined_positive']} top-{shadow.TOP_N} new "
              f"{totals['top_n_new']}  live==offline {not mismatches}  "
              f"eval {eval_cpu:.3f}s cpu vs finalize {prod_cpu:.3f}s")

    out = {
        "status": "MEASURED OFFLINE, sockets denied; synthetic cohorts, frozen "
                  "snapshots; not real-candidate validation",
        "replayed_on": datetime.now(timezone.utc).date().isoformat(),
        "baseline_snapshot_sha256": baseline_sha,
        "expansion_snapshot_sha256": expansion_sha,
        "shadow_raw_rows": len(shadow_rows),
        "top_n": shadow.TOP_N,
        "live_equals_offline_in_every_case": agree,
        "cases": cases,
        "limits": [
            "The recency window moves with the replay date, so absolute counts "
            "differ from V2-A's 2026-09-22 replay; the comparison that matters "
            "here is live evaluator vs offline replay on the SAME date.",
            "CPU is this machine's; Oracle's A1 cores are slower per thread. The "
            "evaluator-to-finalize RATIO is the portable figure.",
            "Frozen rows predate native-id capture, so native-identity and "
            "native-overlap fields are 0 here; --live measures them.",
            "Shadow rows are modelled as arriving after every production row, "
            "so exact score ties go to production (conservative for shadow).",
        ],
    }
    Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    return 0 if agree else 1


def _reach(url, timeout=15):
    """One public GET: status, where it landed, how long. Reads at most 64 KiB."""
    from sources._http import UA
    started = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(65536)
            final = resp.geturl()
            status = resp.status
        error = ""
    except urllib.error.HTTPError as exc:
        status, final, error = exc.code, url, f"HTTPError {exc.code}"
    except Exception as exc:        # transport, timeout: recorded, not raised
        status, final, error = None, url, type(exc).__name__
    host = urllib.parse.urlsplit(final).netloc.lower()
    return {"url": url, "status": status, "error": error,
            "landed_on": "greenhouse" if host.endswith("greenhouse.io") else "employer",
            "redirected": final != url,
            "ms": round((time.perf_counter() - started) * 1000)}


def live(args):
    _prepare_env(offline=False)
    import scraper
    import telemetry
    from sources import ats, shadow
    from bench.search_v2_shadow import CASES, load_engine
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    baseline, baseline_sha = _load(args.baseline)
    expansion, expansion_sha = _load(args.expansion)
    base_keys = {scraper.job_key(r) for r in baseline}

    # One pass, every row, under an open record so native ids are captured and
    # each board gets a real unit: duration, retries, failure category.
    rows_by_board, units = {}, {}
    with _record() as record:
        for token, company in shadow.BOARDS["greenhouse"].items():
            board = f"greenhouse:{token}"
            with telemetry.unit("shadow", "greenhouse", board=board,
                                timeout_s=25, shadow=True):
                try:
                    rows_by_board[board] = ats.fetch(
                        "greenhouse", token, company, lambda t: True,
                        lambda l: True, None)
                except Exception as exc:
                    telemetry.failed(exc)
        units = {u["board"]: dict(u) for u in record["shadow_units"]}
    snapshot = json.dumps(rows_by_board, sort_keys=True).encode()
    if args.snapshot:
        Path(args.snapshot).write_bytes(snapshot)

    boards = {}
    for board, unit in units.items():
        rows = rows_by_board.get(board, [])
        native = [str((r.get("_native") or {}).get("native_id") or "") for r in rows]
        urls = [r.get("Job URL") or "" for r in rows]
        frozen_rows = [r for r in expansion if r.get("Source") == board]
        frozen_ids = {}
        for r in frozen_rows:
            m = GH_ID.search(r.get("Job URL") or "")
            if m:
                frozen_ids[m.group(1)] = r
        live_ids = {n: r for n, r in zip(native, rows) if n}
        persisted = set(live_ids) & set(frozen_ids)
        gone, arrived = set(frozen_ids) - set(live_ids), set(live_ids) - set(frozen_ids)
        arrived_titles = {scraper._title_key(live_ids[n]["Title"]) for n in arrived}
        boards[board] = {
            "ok": unit["ok"], "failure_category": unit["failure_category"],
            "request_ms": unit["duration_ms"], "retries": unit["retries"],
            "raw": unit["raw_count"], "normalized": unit["normalized_count"],
            "fresh_14d": sum(1 for r in rows
                             if scraper._parse_date(r.get("Posted Date")) is not None
                             and scraper.is_recent(r.get("Posted Date"), 14)),
            "native_id": sum(1 for n in native if n),
            "native_id_distinct": len(set(n for n in native if n)),
            "internal_id": sum(1 for r in rows
                               if (r.get("_native") or {}).get("internal_id")),
            "url": sum(1 for u in urls if u),
            "url_carries_native_id": sum(1 for u, n in zip(urls, native)
                                         if u and n and n in u),
            "url_ats_hosted": sum(1 for u in urls if urllib.parse.urlsplit(u)
                                  .netloc.lower().endswith("greenhouse.io")),
            "key_overlap_with_frozen_baseline_raw": sum(
                1 for r in rows if scraper.job_key(r) in base_keys),
            "vs_frozen_2026_09_21": {
                "frozen_rows": len(frozen_rows),
                "frozen_rows_with_extractable_id": len(frozen_ids),
                "persisted_ids": len(persisted),
                "gone_ids": len(gone),
                "new_ids": len(arrived),
                "persisted_id_title_changed": sum(
                    1 for n in persisted
                    if scraper._title_key(frozen_ids[n]["Title"])
                    != scraper._title_key(live_ids[n]["Title"])),
                # Same board, same title, a NEW id: what a repost looks like to
                # native identity — and what current job_key would call one job.
                "gone_id_whose_title_returned_under_a_new_id": sum(
                    1 for n in gone
                    if scraper._title_key(frozen_ids[n]["Title"]) in arrived_titles),
            },
        }
        if args.reach and rows:
            sample = sorted(live_ids.items(), key=lambda kv: -int(kv[0]))[:args.reach]
            token = board.split(":")[1]
            boards[board]["reach"] = []
            for nid, row in sample:
                ats_url = f"https://job-boards.greenhouse.io/{token}/jobs/{nid}"
                checks = {"published": _reach(row["Job URL"])}
                if row["Job URL"] != ats_url:
                    checks["provider_by_native_id"] = _reach(ats_url)
                boards[board]["reach"].append(checks)

    cohorts = {}
    for case, scope in CASES:
        eng, _ = load_engine(case, scope)
        _hires_home(eng, baseline)
        live_rows = copy.deepcopy([r for rows in rows_by_board.values() for r in rows])
        _hires_home(eng, live_rows)
        production = [eng._truncate_desc(dict(r)) for r in _gate(eng, baseline)]
        final = eng.finalize(production)
        held = [(b, _gate(eng, [r for r in live_rows if r["Source"] == b]))
                for b in rows_by_board]
        section, cpu, _wall = _evaluate(eng, shadow, final, production, held)
        cohorts[f"{case}/{scope}"] = {
            "totals": section["totals"], "evaluator_cpu_s": round(cpu, 3),
            "by_board": {b: {k: v[k] for k in (
                "gated", "fresh", "eligible", "eligible_positive", "final_new",
                "final_new_positive", "top_n_new", "best_new_rank", "key_overlap")}
                for b, v in section["by_board"].items()}}
        t = section["totals"]
        print(f"{case}/{scope:<8} frozen final {t['baseline_final']} + live shadow "
              f"-> {t['combined_final']}  positive {t['baseline_positive']} -> "
              f"{t['combined_positive']}  top-{shadow.TOP_N} new {t['top_n_new']}")

    out = {
        "status": "MEASURED LIVE: one public pass over the eight boards, no Apify. "
                  "One dated observation, not longitudinal evidence.",
        "observed_at": observed_at,
        "requests": len(units) + sum(len(c) for b in boards.values()
                                     for c in b.get("reach", [])),
        "live_snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
        "live_snapshot_committed": False,
        "baseline_snapshot_sha256": baseline_sha,
        "expansion_snapshot_sha256": expansion_sha,
        "summed_request_ms": sum(b["request_ms"] for b in boards.values()),
        "raw_rows": sum(b["raw"] for b in boards.values()),
        "boards": boards,
        "cohorts_live_shadow_vs_frozen_baseline": cohorts,
        "limits": [
            "Cohort rows judge LIVE shadow inventory against a FROZEN 2026-09-21 "
            "baseline result: mixed dates, so overlap is understated where a "
            "posting moved between snapshots.",
            "Native-id persistence compares ids parsed from 2026-09-21 URLs with "
            "native ids captured today. An id absent from a URL is not compared.",
            "Title-returned-under-a-new-id is by title key within one board; "
            "multi-location postings sharing a title make it approximate.",
            "A job page answering HTTP 200 does not prove the vacancy is open.",
        ],
    }
    Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    for board, b in boards.items():
        v = b["vs_frozen_2026_09_21"]
        print(f"{board:<28} {'ok ' if b['ok'] else 'ERR'} {b['request_ms']:>6} ms "
              f"raw {b['raw']:>4} native {b['native_id']}/{b['raw']} "
              f"ids kept {v['persisted_ids']}/{v['frozen_rows_with_extractable_id']} "
              f"reach {[c['published']['status'] for c in b.get('reach', [])]}")
    return 0


# Real sweeps must be FREE by construction. `--site free` leaves the paid plan
# empty, every paid site is disabled in the profile as the worker's free_prefs
# does, each child runs through bench/paid_guard.py (whose dry run of that
# exact invocation must show no paid search, since this harness never
# authorises one), and a paid marker in the log kills the run. Unsetting
# APIFY_TOKEN is NOT a guard: the engine's credential step calls load_dotenv(),
# which is how an unguarded check once ran paid searches.
PAID_MARKERS = ("Total Apify spend", "curious_coder", "misceres", "linkedin (",
                "indeed (", "naukri (")
SWEEP_ARMS = {
    "off": {"SWEEP_SEARCH_V2_TELEMETRY": "1"},
    "on": {"SWEEP_SEARCH_V2_TELEMETRY": "1", "SWEEP_FREE_SOURCE_SHADOW": "1"},
    "production_like": {"SWEEP_SEARCH_V2_TELEMETRY": "1",
                        "SWEEP_FREE_SOURCE_SHADOW": "1",
                        "SWEEP_FREE_LEVER_CONCURRENCY": "1",
                        "SWEEP_FREE_LEVER_WORKERS": "4"},
    "all_off": {},
}


def sweep(args):
    """Worker-shaped free sweeps (`scraper.py --profile X --yes` as a child
    process, plus `--site free`) over four public boards, once per arm. The
    user's CSV and JSON must be byte-identical in every arm."""
    import glob
    import subprocess
    from bench import paid_guard
    work = Path(tempfile.mkdtemp())
    python = sys.executable
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("APIFY_TOKEN", "SWEEP_"))}
    profiles, arms = [], {}
    try:
        for arm in SWEEP_ARMS:
            path = ROOT / "profiles" / f"b3sweep_{arm}.py"
            profiles.append(path)
            path.write_text(
                '"""TEMPORARY V2-B3 free-only check; deleted after the run."""\n'
                'SITES = {"linkedin": {"enabled": False}, "indeed": {"enabled": False},'
                ' "naukri": {"enabled": False}}\n'
                'ATS_BOARDS = {"lever": {"cred": "CRED"}, "greenhouse": {"gitlab": '
                '"GitLab", "groww": "Groww"}, "ashby": {"linear": "Linear"}, '
                '"smartrecruiters": {}, "breezy": {}}\nFEEDS = {}\n'
                f'SETTINGS = {{"output_dir": {str(work / arm)!r}}}\n')
        for arm, flags in SWEEP_ARMS.items():
            log_path = work / f"{arm}.log"
            started = time.perf_counter()
            with open(log_path, "w") as log:
                # Through the developer paid guard (V2-C0): the child asks the
                # engine's own dry run what this arm would run and stops before
                # any client if it holds a paid search. This harness never
                # passes --allow-paid, and `base` drops SWEEP_*, so no paid plan
                # can be authorised here.
                child = subprocess.Popen(
                    paid_guard.engine_argv(["--profile", f"b3sweep_{arm}",
                                            "--site", "free", "--yes"], python=python),
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                    env=dict(base, SWEEP_RUN_ID=f"b3sweep{arm}", **flags))
                while child.poll() is None:
                    time.sleep(0.5)
                    if any(m in log_path.read_text() for m in PAID_MARKERS) or \
                            time.perf_counter() - started > 300:
                        child.kill()
                        raise SystemExit(f"{arm}: killed (paid marker or timeout)")
            if child.returncode:
                raise SystemExit(f"{arm}: the engine exited {child.returncode}\n"
                                 + log_path.read_text()[-2000:])

            def one(pattern, arm=arm):
                found = glob.glob(str(work / arm / pattern))
                return Path(found[0]).read_bytes() if found else None
            record = one("telemetry/sweep_*.json")
            record = json.loads(record) if record else None
            arms[arm] = {"exit": child.returncode,
                         "wall_s": round(time.perf_counter() - started, 2),
                         "csv": one("jobs_*.csv"), "json": one("jobs_*.json"),
                         "record": record,
                         "record_bytes": len(json.dumps(record, indent=2))
                         if record else 0}
    finally:
        for path in profiles:
            path.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(work, ignore_errors=True)

    ref = arms["off"]
    out = {"status": "MEASURED LIVE: worker-shaped FREE sweeps over four public "
                     "boards; paid plan verified empty; no Apify",
           "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "arms": {}}
    for arm, got in arms.items():
        record = got["record"] or {}
        ev = record.get("shadow_evaluation")
        marks = record.get("milestones") or {}
        out["arms"][arm] = {
            "flags": SWEEP_ARMS[arm], "exit": got["exit"], "wall_s": got["wall_s"],
            "csv_identical_to_off": got["csv"] == ref["csv"],
            "json_identical_to_off": got["json"] == ref["json"],
            "final_rows": len(json.loads(got["json"])),
            "telemetry_record_written": bool(record),
            "record_bytes": got["record_bytes"],
            "sources_attempted": record.get("sources_attempted"),
            "shadow_units": len(record.get("shadow_units") or []),
            "free_phase_ms": (marks.get("free_phase_done", 0)
                              - marks.get("free_phase_start", 0)) if marks else None,
            "sweep_ms": record.get("duration_ms"),
            "shadow_unit_ms_requests_retries": {
                u["board"]: [u["duration_ms"], u["requests"], u["retries"]]
                for u in record.get("shadow_units") or []},
            "shadow_status": ev and ev.get("status"),
            "shadow_cost": ev and ev.get("cost"),
            "shadow_totals": ev and ev.get("totals"),
        }
        print(arm, {k: out["arms"][arm][k] for k in (
            "exit", "wall_s", "csv_identical_to_off", "json_identical_to_off",
            "final_rows", "sources_attempted", "shadow_units", "shadow_cost")})
    Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    same = all(a["csv_identical_to_off"] and a["json_identical_to_off"]
               for a in out["arms"].values())
    return 0 if same else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--frozen", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--sweep", action="store_true")
    ap.add_argument("--baseline")
    ap.add_argument("--expansion")
    ap.add_argument("--output", required=True)
    ap.add_argument("--reach", type=int, default=0,
                    help="live only: sampled job pages to GET per board (max 2)")
    ap.add_argument("--snapshot", help="live only: where to keep the fetched rows "
                                       "(public data; not for committing)")
    args = ap.parse_args()
    args.reach = max(0, min(args.reach, 2))
    if args.sweep:
        sys.exit(sweep(args))
    if not (args.baseline and args.expansion):
        ap.error("--frozen and --live need --baseline and --expansion")
    sys.exit(frozen(args) if args.frozen else live(args))


if __name__ == "__main__":
    main()
