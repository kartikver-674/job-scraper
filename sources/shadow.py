"""Shadow source evaluation — measured, never shipped to a user.

    SWEEP_FREE_SOURCE_SHADOW=1     default OFF; records into the telemetry
                                   record, so it does nothing unless
                                   SWEEP_SEARCH_V2_TELEMETRY is on as well

The Free expansion audit discovered 15,798 candidate board identifiers, probed
365 of them and found 187 that answer with compatible public JSON. It
recommended enabling almost none of them: across four frozen candidate
fixtures, all 187 together contributed six positive-score jobs, and every one
of those six came from the nine boards below. That is the whole case for a
shadow tranche rather than a registry expansion.

WHAT SHADOW MEANS HERE, precisely:

  * nothing is fetched unless the flag is on, and nothing at all happens
    unless a telemetry record is open to hold the evidence;
  * it runs AFTER the user's results, CSV and JSON and seen ledger alike, are
    already on disk (scraper.main). No shadow request is made while any
    production output can still change;
  * rows are fetched through the UNCHANGED greenhouse adapter and never leave
    this module: `run()` and `evaluate()` both return None, so there is no row
    set for a caller to merge, however a call site is later edited;
  * config.ATS_BOARDS is untouched, so the sweep's own source count, banner and
    registry are exactly what they were;
  * every board is isolated, the evaluation is isolated again, and the caller
    wraps the whole call a third time: a diagnostic cannot fail a sweep;
  * what is written is counts only, into the telemetry record — `shadow_units`
    (one per board, kept out of the sweep's source counters) and a
    `shadow_evaluation` section. No row, title, company, URL or description.

V2-A deliberately stopped at "does the board answer, how fast, how many rows".
V2-B3 adds the question that decides promotion — would this board have put a
job in front of THIS user — by judging the held rows with the production
functions themselves, handed over by scraper.shadow_engine():

  score_and_filter   the exact chain finalize runs, with a private stage
                     collector so the production funnel is not overwritten
  rank_rows          finalize's own sort + dedupe
  job_key            current production identity

TOP-N IS MEASURED AGAINST THE REAL RESULT, not by ranking shadow rows alone:
the production final rows plus the shadow rows, appended AFTER them, through
rank_rows. That equals finalize(production_rows + shadow_rows) exactly — a
production duplicate that lost to its best twin can never beat a shadow row
its twin beat — and appending last means every exact score tie goes to the
production row, so shadow gains can be undercounted but never overstated.

PubMatic is in the audit's nine but deliberately NOT here: its sampled job page
returned HTTP 403 in the spot-check, so its rows may not be reachable at all.
It waits in a reachability-review queue instead of being measured as if it were
comparable to the rest.
"""
import os
import sys
import time
import urllib.parse
from collections import Counter, namedtuple

import telemetry

from . import ats

FLAG = "SWEEP_FREE_SOURCE_SHADOW"

# Version of the `shadow_evaluation` section. bench/search_v2_shadow_production.py
# refuses a section it would misread.
SCHEMA = "search-v2b3.1"

# The eight boards recommended for shadow validation by
# docs/free-source-expansion-audit.md §8. Every token's list endpoint already
# returned valid normalized jobs, and every board identity was confirmed
# against the provider's own company endpoint in
# docs/search-v2-evidence/first-tranche-spotchecks.json.
#
# This is NOT config.ATS_BOARDS and must not be merged into it. A board becomes
# a real source through a reviewed registry change, after shadow evidence — not
# by someone moving a line from this file into that one.
BOARDS = {
    "greenhouse": {
        "fivetran": "Fivetran",
        "abnormalsecurity": "Abnormal Security",
        "apolloio": "Apollo.io",
        "brex": "Brex",
        "vercel": "Vercel",
        "jumio": "Jumio",
        "catawiki": "Catawiki",
        "zetaglobal": "Zeta Global",
    },
}

# The cut V2-A's offline replay reported, so live and frozen evidence line up.
TOP_N = 20

# Scoring costs ~5 ms a row (MEASURED, docs/search-engine-v2-b3-shadow-
# production-evaluation.md §7). The eight boards held 876 raw rows in total
# when measured, so a gated set past this means a board changed shape — and
# scoring it would stop being a small fraction of the sweep. Past it, the
# cheap acquisition metrics are still recorded and the funnel is skipped.
MAX_EVAL_ROWS = 1000

# What scraper.shadow_engine() hands over. Production functions, not copies:
#   final_rows        the ranked output rows the user was given
#   production_rows   every row the sweep acquired, before scoring
#   prepare           what fetch_free applies to a row before finalize
#   score_and_filter  finalize's chain; called with a private stage collector
#   rank_rows         finalize's sort + dedupe
#   job_key, to_output
#   recent            the user's recency rule on a date, or None if unset
#   scope             the search's scope settings, for cohort grouping
Engine = namedtuple("Engine", "final_rows production_rows prepare "
                              "score_and_filter rank_rows job_key to_output "
                              "recent scope")

# Per-board fields of the section, in the order they are written. A board that
# failed carries every one of them as None: unavailable, never zero.
ACQUISITION = ("gated", "fresh", "source_unique", "unkeyed", "native_id",
               "native_id_distinct", "requisition", "url",
               "url_carries_native_id", "url_ats_hosted")
OVERLAP = ("key_overlap", "key_and_native_overlap",
           "key_overlap_native_distinct", "key_overlap_native_not_comparable",
           "native_overlap", "native_overlap_key_distinct")
USEFUL = ("funnel", "eligible", "eligible_positive", "final_new",
          "final_new_positive", "final_replaces", "top_n_new",
          "top_n_replaces", "best_new_rank")


def enabled():
    """Read per call, so turning the flag off lands on the next sweep."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def run(keep_title, keep_location, is_home=None, log=print, engine=None):
    """Fetch, measure and — given an engine — evaluate the tranche. Returns
    None, always.

    The return type is the safety property: there is no row set here for a
    caller to extend a sweep with, however the call site is later edited.
    """
    if not enabled():
        return None
    if not telemetry.active():
        # Every number this produces lands in the telemetry record. With none
        # open there is nowhere to put them, and eight requests whose only
        # trace is a log line are cost without evidence.
        log(f"  shadow tranche skipped: {telemetry.FLAG} is off, so there is "
            f"nowhere to record it")
        return None
    rss_before = _peak_rss_kb()
    started = time.perf_counter()
    held = []                   # (board, gated rows) — never leaves this module
    for platform, boards in BOARDS.items():
        if platform not in ats.ATS:                 # a typo, not a live source
            log(f"  shadow {platform:<9} {'-':<22} ! no adapter")
            continue
        for token, company in boards.items():
            board, got = f"{platform}:{token}", None
            with telemetry.unit("shadow", platform, board=board,
                                timeout_s=25, shadow=True):
                try:
                    got = ats.fetch(platform, token, company, keep_title,
                                    keep_location, is_home)
                except Exception as exc:
                    telemetry.failed(exc)
                    log(f"  shadow {platform:<9} {company:<22} ! {exc}")
            if got is not None:
                held.append((board, got))
                log(f"  shadow {platform:<9} {company:<22} "
                    f"{len(got):>4} gated (diagnostic only)")
    fetch_ms = round((time.perf_counter() - started) * 1000)
    if engine is not None:
        try:
            evaluate(held, engine, fetch_wall_ms=fetch_ms,
                     rss_before_kb=rss_before)
        except Exception as exc:
            # Recorded, never raised: the category and the exception's type,
            # not its message, which could quote a row.
            telemetry.shadow_evaluation({
                "schema": SCHEMA, "status": "failed",
                "failure_category": telemetry.failure_category(exc),
                "error_type": type(exc).__name__})
            log(f"  shadow evaluation failed ({type(exc).__name__}); "
                f"the results above are unaffected")
    return None


def evaluate(held, engine, fetch_wall_ms=None, rss_before_kb=None):
    """Judge held shadow rows by this sweep's own rules. Returns None.

    `held` is [(board, rows)] for the boards that answered. Every row is
    copied and prepared the way fetch_free prepares a production row, then
    goes through the production chain with a private stage collector — so the
    production funnel, LAST_STATS and the result rows are never touched.
    """
    wall, cpu = time.perf_counter(), time.process_time()
    rows_by_board = {board: [engine.prepare(dict(r)) for r in rows]
                     for board, rows in held}
    total = sum(len(rows) for rows in rows_by_board.values())
    by_key, by_native = _production_index(engine)

    by_board = {}
    for platform, boards in BOARDS.items():
        for token in boards:
            board = f"{platform}:{token}"
            by_board[board] = {"fetched": board in rows_by_board,
                               **dict.fromkeys(ACQUISITION + OVERLAP + USEFUL)}
            if board in rows_by_board:
                by_board[board].update(_acquisition(
                    rows_by_board[board], engine, by_key, by_native))

    totals = None
    status = "acquisition_only_over_budget" if total > MAX_EVAL_ROWS else "evaluated"
    if status == "evaluated":
        totals = _usefulness(rows_by_board, engine, by_board)

    telemetry.shadow_evaluation({
        "schema": SCHEMA,
        "status": status,
        # Shadow rows join the ranking after every production row, so an exact
        # score tie always goes to production. See the module docstring.
        "arrival": "after_production",
        "top_n": TOP_N,
        "scope": engine.scope,
        "production_rows": len(engine.production_rows),
        "production_rows_with_native_id": sum(
            1 for row in engine.production_rows if _native_id(row)),
        "rows_evaluated": total if totals is not None else 0,
        "totals": totals,
        "cost": {
            "fetch_wall_ms": fetch_wall_ms,
            "evaluation_wall_ms": round((time.perf_counter() - wall) * 1000),
            "evaluation_cpu_ms": round((time.process_time() - cpu) * 1000),
            "peak_rss_kb_before": rss_before_kb,
            "peak_rss_kb_after": _peak_rss_kb(),
        },
        "by_board": by_board,
    })
    return None


def _usefulness(rows_by_board, engine, by_board):
    """The candidate funnel, the combined ranking and top-N, filled into
    by_board. Returns the sweep-level totals."""
    funnel = {board: {} for board in rows_by_board}

    def collect(name, rows):
        counts = Counter(row.get("Source") for row in rows)
        for board, stages in funnel.items():
            stages[name] = counts.get(board, 0)

    everything = [row for rows in rows_by_board.values() for row in rows]
    eligible, _ = engine.score_and_filter(everything, collect)
    shadow_out = [engine.to_output(row) for row in eligible]
    mine = {id(row) for row in shadow_out}

    baseline = list(engine.final_rows)
    baseline_keys = {engine.job_key(row) for row in baseline} - {None}
    # Production first, shadow after — see the module docstring for why this
    # equals ranking both row sets together and why ties go to production.
    combined = engine.rank_rows(baseline + shadow_out)
    top = {id(row) for row in combined[:TOP_N]}

    per = {board: Counter() for board in rows_by_board}
    best = {}
    for position, row in enumerate(combined, 1):
        if id(row) not in mine:
            continue
        board, key = row.get("source_site"), engine.job_key(row)
        c = per[board]
        if key is None or key not in baseline_keys:
            c["final_new"] += 1
            c["final_new_positive"] += row.get("score", 0) > 0
            c["top_n_new"] += id(row) in top
            best.setdefault(board, position)
        else:           # outranked the production twin it would displace
            c["final_replaces"] += 1
            c["top_n_replaces"] += id(row) in top
    positive = Counter(row.get("Source") for row in eligible
                       if row.get("score", 0) > 0)
    kept = Counter(row.get("Source") for row in eligible)
    for board, c in per.items():
        by_board[board].update(
            funnel=funnel[board], eligible=kept[board],
            eligible_positive=positive[board], final_new=c["final_new"],
            final_new_positive=c["final_new_positive"],
            final_replaces=c["final_replaces"], top_n_new=c["top_n_new"],
            top_n_replaces=c["top_n_replaces"], best_new_rank=best.get(board))

    head = baseline[:TOP_N]
    return {
        "eligible": len(eligible),
        "baseline_final": len(baseline),
        "combined_final": len(combined),
        "baseline_positive": sum(row.get("score", 0) > 0 for row in baseline),
        "combined_positive": sum(row.get("score", 0) > 0 for row in combined),
        "baseline_top_n": len(head),
        "baseline_top_n_retained": sum(id(row) in top for row in head),
        "top_n_new": sum(c["top_n_new"] for c in per.values()),
    }


def _acquisition(rows, engine, by_key, by_native):
    """What one board's gated rows are, before any candidate rule beyond the
    acquisition gate: freshness, native identity, links, and overlap with the
    inventory the sweep already had."""
    keys = [engine.job_key(row) for row in rows]
    ids = [_native_id(row) for row in rows]
    urls = [row.get("Job URL") or "" for row in rows]
    overlap = Counter()
    for row, key, native in zip(rows, keys, ids):
        ident = (_provider(row), native)
        if key is not None and key in by_key:
            overlap["key_overlap"] += 1
            twins = by_key[key]
            if native and ident in twins:
                overlap["key_and_native_overlap"] += 1
            elif native and any(p == ident[0] and n for p, n in twins):
                overlap["key_overlap_native_distinct"] += 1
            else:       # the twin is another provider, or carries no native id
                overlap["key_overlap_native_not_comparable"] += 1
        if native and ident in by_native:
            overlap["native_overlap"] += 1
            if key is None or key not in by_native[ident]:
                overlap["native_overlap_key_distinct"] += 1
    return dict(
        gated=len(rows),
        fresh=None if engine.recent is None else sum(
            1 for row in rows if engine.recent(row.get("Posted Date"))),
        source_unique=len({k for k in keys if k is not None}),
        unkeyed=keys.count(None),
        native_id=sum(1 for n in ids if n),
        native_id_distinct=len({n for n in ids if n}),
        requisition=sum(1 for row in rows
                        if (row.get("_native") or {}).get("internal_id")),
        url=sum(1 for u in urls if u),
        url_carries_native_id=sum(1 for u, n in zip(urls, ids) if u and n and n in u),
        url_ats_hosted=sum(1 for u in urls if _host(u).endswith("greenhouse.io")),
        **{name: overlap[name] for name in OVERLAP})


def _production_index(engine):
    """Two maps over every row the sweep acquired: current job_key -> the
    (provider, native id) pairs carrying it, and (provider, native id) -> the
    job_keys it was seen under."""
    by_key, by_native = {}, {}
    for row in engine.production_rows:
        key, native = engine.job_key(row), _native_id(row)
        ident = (_provider(row), native)
        if key is not None:
            by_key.setdefault(key, set()).add(ident)
        if native:
            by_native.setdefault(ident, set()).add(key)
    return by_key, by_native


def _provider(row):
    """"greenhouse:gitlab" -> "greenhouse"; a feed or paid site is its own name.
    Native ids are only comparable inside one provider's namespace."""
    return (row.get("Source") or "").split(":", 1)[0]


def _native_id(row):
    return str((row.get("_native") or {}).get("native_id") or "")


def _host(url):
    try:
        return urllib.parse.urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _peak_rss_kb():
    """This process's peak resident set so far, in KiB, or None."""
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:           # not every platform has it
        return None
    return peak // 1024 if sys.platform == "darwin" else peak   # macOS: bytes
