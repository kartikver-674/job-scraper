"""Search Engine V2-A execution telemetry — passive, and OFF by default.

    SWEEP_SEARCH_V2_TELEMETRY=1

One question drove this module: the forensic audit could not say where a
reported 10–15 minute sweep spends its time, because nothing in the engine
records a per-source timestamp, a request count, or the funnel a row falls out
of. Every number in that audit is a probe run beside production, never
production itself.

PASSIVE IS THE CONTRACT. This module only ever READS what the engine already
computed. It does not filter, sort, re-order, score, or deduplicate, and it
returns nothing any caller branches on. A sweep with the flag on and a sweep
with the flag off must produce byte-identical CSV/JSON output; that is asserted
in scraper.demo() and sweep/tests/test_search_v2_telemetry.py rather than
merely intended.

The flag is read ONCE, in start(). A record that began under one setting stays
consistent for its whole life, and a mid-run environment change cannot leave
half a sweep measured. When the flag is off, `_run` stays None and every entry
point below is a single `is None` test.

Privacy: this file writes source names, board tokens, counts, durations, actor
and dataset IDs, and the search keyword/location strings the console already
prints. It NEVER writes an Apify token, résumé text, a job description, a full
profile payload, or any user secret — see _clip() and the allowlisted field
sets below. There is no free-text sink: every recorded string passes through a
bounded field. The shadow tranche's evaluation (V2-B3) is counts per board in
its own section and holds no row, title, company or URL either.

Paid units (V2-C1) add one section per sweep that has a paid plan: each
planned search's id, provider, actor, depth, ceiling, a query FINGERPRINT (not
the query), its status, its execution clocks and cost readings, and what its
rows became — counts and a one-token-per-position code. No row content.

Paid execution (V2-C2) adds one more when SWEEP_PAID_CONCURRENCY ran the paid
phase: the reservation ledger (exposure held against the budget — never a cost)
and the scheduler's clocks. A paid search's unit is then opened on its worker
thread (defer=True) and finished on the coordinator (resumed()), so the shared
record is only ever written by the thread that owns it.
"""
import contextlib
import json
import os
import socket
import threading
import time
import urllib.error
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

FLAG = "SWEEP_SEARCH_V2_TELEMETRY"

# Schema version. Bump when a field's MEANING changes, so an analysis can
# refuse a record it would misread.
SCHEMA = "search-v2a.1"

# Bound on any recorded string. A query or a provider error message is metadata,
# not a payload; a cap is what keeps it that way.
_CLIP = 200

_run = None     # the active record, or None whenever telemetry is off

# THREAD SAFETY (added for V2-B2, which fetches Lever boards concurrently).
#
# The open work unit used to be a module global — one "current unit" pointer for
# the whole process. That is correct exactly while acquisition is serial, and
# silently wrong the moment it is not: two threads in unit() would clobber one
# another's pointer, and every observed()/retried()/failed() call in between
# would land its counts on whichever board happened to be current. The result is
# not a crash, it is a telemetry file that confidently attributes one board's
# rows and retries to another.
#
# Two different kinds of state, so two different mechanisms:
#
#   * The OPEN UNIT is thread-confined. Each thread owns its own record from
#     unit() to __exit__, nothing else touches it, so it needs no lock — and a
#     lock would not have helped anyway, because the bug was shared IDENTITY,
#     not unsynchronised mutation.
#   * The RUN record is shared. Its counters are read-modify-write and its
#     milestone map is check-then-set, neither of which the GIL makes atomic, so
#     every mutation of it goes through _LOCK.
_local = threading.local()      # _local.unit: this thread's open unit, if any
_LOCK = threading.RLock()       # guards mutation of the shared _run record


def _current():
    return getattr(_local, "unit", None)


def _set_current(record):
    _local.unit = record


def enabled():
    """Whether the flag is set. Read by start(); exposed for tests."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def active():
    """True once start() has opened a record. Cheapest possible guard."""
    return _run is not None


def _clip(value):
    return ("" if value is None else str(value))[:_CLIP]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# Sweep-level record
# ---------------------------------------------------------------------------
def start(path, output_dir, engine_revision=None, profile=""):
    """Open a record for this sweep, or do nothing at all when the flag is off.

    `path` is "free", "paid" or "paid+free" — the audit asked for free-vs-paid
    separation, and a full sweep is genuinely both.
    """
    global _run
    _run = None
    _set_current(None)
    if not enabled():
        return None
    _run = {
        "schema": SCHEMA,
        "sweep_id": uuid.uuid4().hex,
        # The public worker's own run ID, when it exported one. This is the join
        # key between a telemetry record and a worker status file; without it the
        # two accounts of the same sweep cannot be matched.
        "worker_run_id": _clip(os.environ.get("SWEEP_RUN_ID", "")),
        "path": path,
        "profile": _clip(profile),
        # Blank unless the deployment exports it. Deliberately an env var and
        # not a `git rev-parse` subprocess: a sweep should not shell out on
        # every run to label a diagnostic.
        "engine_revision": _clip(engine_revision if engine_revision is not None
                                 else os.environ.get("SWEEP_ENGINE_REVISION", "")),
        "output_dir": _clip(output_dir),
        "started_at": _now(),
        "finished_at": None,
        "duration_ms": None,
        "sources_attempted": 0,
        "sources_succeeded": 0,
        "sources_failed": 0,
        # raw_rows is DERIVED in finish() by summing the units, because "raw"
        # only exists per source: an adapter normalizes before it returns, so
        # the sweep never holds a raw row. Conflating the two would have made
        # the record claim a distinction the engine does not have.
        "raw_rows": None,
        "normalized_rows": None,
        "eligible_rows": None,
        "final_rows": None,
        # first_* milestones. Free holds every row in memory until the last
        # source returns, so its eligible milestones can only be observed at the
        # single tail emit — recorded honestly in "milestone_granularity".
        "milestones": {},
        "milestone_granularity": {},
        "units": [],
        # Shadow boards are counted apart from the sweep's own sources, on
        # purpose — see unit(shadow=True).
        "shadow_units": [],
        "stages": {},
        "identity": {},
        "notes": [],
    }
    _run["_t0"] = time.monotonic()
    return _run["sweep_id"]


def note(text):
    """An explicit caveat, carried with the record instead of lost in prose."""
    if _run is not None:
        with _LOCK:
            _run["notes"].append(_clip(text))


def mark(name, granularity="exact"):
    """First-occurrence timestamp. Later calls for the same name are ignored —
    a milestone is the FIRST time something happened."""
    if _run is None:
        return
    # Check-then-set under concurrency: without the lock two threads can both
    # pass the membership test and the milestone becomes whichever wrote last.
    with _LOCK:
        if name in _run["milestones"]:
            return
        _run["milestones"][name] = round((time.monotonic() - _run["_t0"]) * 1000)
        _run["milestone_granularity"][name] = granularity


def eligible_progress(count, granularity="checkpoint"):
    """Called with the finalized row count at every checkpoint, so the 1/5/20
    milestones land on the first checkpoint that crossed each one.

    On a free-only sweep every checkpoint is the tail, so all three collapse to
    the same instant. The granularity field says so rather than pretending the
    engine streamed.
    """
    if _run is None:
        return
    for threshold, name in ((1, "first_eligible"), (5, "first_5_eligible"),
                            (20, "first_20_eligible")):
        if count >= threshold:
            mark(name, granularity)


# ---------------------------------------------------------------------------
# Work units — one logical acquisition unit (an ATS board, a feed, a paid search)
# ---------------------------------------------------------------------------
class _Unit:
    """Context manager for one acquisition unit. Swallows nothing: the caller's
    own try/except still owns failure isolation, this only records what happened.
    """

    def __init__(self, record):
        self.record = record

    def __enter__(self):
        return self.record

    def __exit__(self, exc_type, exc, tb):
        if self.record is None:
            return False
        self.record["finished_at"] = _now()
        self.record["duration_ms"] = round(
            (time.monotonic() - self.record.pop("_t0")) * 1000)
        if exc is not None:
            self.record["ok"] = False
            self.record["failure_category"] = failure_category(exc)
            self.record["failure"] = _clip(exc)
        # A deferred unit is not attached to the run yet: its caller holds it and
        # hands it to attach() in a deterministic order. Counting it here would
        # count it twice.
        if not self.record.pop("_deferred", False):
            _count(self.record)
        _set_current(None)
        return False        # never suppress; isolation is the caller's job


def unit(path, family, board="", query="", location="", country="",
         requested_limit=None, timeout_s=None, shadow=False, defer=False):
    """Open a work unit. A no-op context manager when telemetry is off.

    `shadow=True` keeps the unit OUT of the sweep's source counters and in its
    own list. A shadow board is not one of the sweep's sources and must never
    make the record say the sweep attempted 142 sources when it attempted 134.

    `defer=True` opens the unit WITHOUT attaching it to the run. The caller gets
    the record back (from the `with` block) and passes it to attach() later.
    Concurrent acquisition uses this so the order units appear in the telemetry
    file is the caller's registry order and not the order threads happened to
    start — the same reason the rows themselves are merged in registry order.
    """
    if _run is None:
        return _Unit(None)
    record = {
        "path": path,
        "family": _clip(family),
        "board": _clip(board),
        "query": _clip(query),
        "location": _clip(location),
        "country": _clip(country),
        "requested_limit": requested_limit,
        "timeout_s": timeout_s,
        "started_at": _now(),
        "finished_at": None,
        "duration_ms": None,
        "ok": True,
        "failure_category": "",
        "failure": "",
        "requests": 0,
        "retries": 0,
        "raw_count": 0,
        "normalized_count": 0,
        "source_gate_count": 0,
        "shadow": shadow,
        "_t0": time.monotonic(),
        "_deferred": defer,
    }
    _set_current(record)
    if not defer:
        with _LOCK:
            _run["shadow_units" if shadow else "units"].append(record)
    return _Unit(record)


def _count(record):
    """Fold one finished unit into the sweep's source counters."""
    if _run is None or record.get("shadow"):
        return
    with _LOCK:
        _run["sources_attempted"] += 1
        _run["sources_succeeded" if record["ok"] else "sources_failed"] += 1


def attach(record):
    """Attach a deferred unit to the run, in the caller's chosen order.

    Called from the coordinating thread once its workers are done, so the units
    list is ordered by the registry and not by who finished first. A record that
    was never deferred, or telemetry being off, makes this a no-op.
    """
    if _run is None or record is None:
        return
    with _LOCK:
        _run["shadow_units" if record.get("shadow") else "units"].append(record)
    _count(record)


@contextlib.contextmanager
def resumed(record, opened=None):
    """V2-C2: finish, on the coordinator's thread, a paid unit a worker thread
    opened (defer=True) and ran. Inside, `record` is this thread's open unit,
    so the serial loop's own calls — observed, paid_run, cost_observation,
    failed — land on it exactly as they did there. On the way out its clock is
    closed from `opened` (time.monotonic(), when the worker began): the span
    the serial loop's unit always covered, from the start request to the done
    marker. The caller attaches it afterwards. None (telemetry off): nothing."""
    if record is None:
        yield None
        return
    saved = _current()
    _set_current(record)
    try:
        yield record
    finally:
        _set_current(saved)
        if opened is not None:
            record["finished_at"] = _now()
            record["duration_ms"] = round((time.monotonic() - opened) * 1000)


def observed(raw=0, normalized=0, gated=0, requests=0):
    """Counts from inside the open unit. Additive, because one logical unit can
    be several requests (a feed's categories, himalayas' queries)."""
    unit_ = _current()
    if unit_ is None:
        return
    # No lock: this record belongs to this thread alone between unit() and
    # __exit__, so nothing else can be mutating it.
    unit_["raw_count"] += raw
    unit_["normalized_count"] += normalized
    unit_["source_gate_count"] += gated
    unit_["requests"] += requests
    if raw and not unit_["shadow"]:
        mark("first_raw")


def failed(exc):
    """Mark the open unit failed from inside a caller's own `except`.

    fetch_free catches per board and per feed — that isolation is the point and
    predates this module — so the exception never reaches the context manager.
    This is how a source that failed gets recorded as failed rather than as a
    source that succeeded with zero jobs.
    """
    unit_ = _current()
    if unit_ is None:
        return
    unit_["ok"] = False
    unit_["failure_category"] = failure_category(exc)
    unit_["failure"] = _clip(exc)


def partial(reason):
    """One sub-request of the open unit failed while the unit itself survived.

    The audit's point exactly: "success", "legitimately zero", "schema-empty",
    "filtered to zero" and "one route of several failed" are five different
    states and a single log line made them look like one.
    """
    unit_ = _current()
    if unit_ is not None:
        unit_.setdefault("partial_failures", []).append(_clip(reason))


def retried():
    """One transient HTTP retry inside the open unit — called from sources._http,
    which is the only place that knows a retry happened."""
    unit_ = _current()
    if unit_ is not None:
        unit_["retries"] += 1


def request():
    unit_ = _current()
    if unit_ is not None:
        unit_["requests"] += 1


PAID_RUN_FIELDS = (
    "actor_id", "actor_build_id", "actor_run_id", "dataset_id",
    "actor_status", "actor_started_at", "actor_finished_at",
    "dataset_retrieved_at", "poll_count", "estimated_cost_usd",
    "reported_cost_usd", "billed_delta_usd",
    # The provider-enforced ceiling this run was started under. Without it a
    # later cost analysis cannot tell a cheap run from a run that was cheap
    # because it was capped.
    "max_total_charge_usd",
    # V2-C1. Local clocks around work the engine already does, and fields of
    # Run objects it already holds — never a new request.
    "unit_id", "start_ms", "wait_ms", "poll_get_ms", "dataset_ms",
    "account_read_ms", "checkpoint_ms", "actor_run_time_s",
    "actor_build_number", "provider_ceiling_usd", "charged_events",
    "budget_view_usd", "budget_basis", "failure_type")


def paid_run(**fields):
    """Actor-run metadata for the open paid unit.

    Allowlisted keys only (PAID_RUN_FIELDS). A token is not in the list and
    cannot be added by a caller passing one: unknown keys are dropped.
    """
    unit_ = _current()
    if unit_ is None:
        return
    for key in PAID_RUN_FIELDS:
        if key in fields:
            value = fields[key]
            unit_[key] = (_bounded(value) if isinstance(value, dict) else value
                          if isinstance(value, (int, float, type(None)))
                          else _clip(value))


def failure_category(exc):
    """A bounded category, so a source's health can be counted without parsing
    provider prose. Deliberately coarse: the audit needs 404-vs-timeout-vs-
    schema, not a taxonomy of every library's wording."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        return "transport"
    if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError)):
        return "schema_or_parse"
    if isinstance(exc, OSError):
        return "transport"
    return "other"


# ---------------------------------------------------------------------------
# Funnel — read off the lists finalize() already built
# ---------------------------------------------------------------------------
def stage(name, rows):
    """Per-source counts at one funnel boundary.

    Called from finalize() with the list it is already holding. finalize runs
    once per checkpoint, so each call overwrites the last: the record describes
    the FINAL pass over the complete row set, which is the pass whose numbers a
    reader means.
    """
    if _run is None:
        return
    counts = {}
    for row in rows:
        key = row.get("Source") or row.get("source_site") or ""
        counts[key] = counts.get(key, 0) + 1
    with _LOCK:
        _run["stages"][name] = {"total": len(rows), "by_source": counts}
    if "_paid_ix" in _run:
        _guarded(_paid_reach, name, rows)


def identity(raw_rows, job_key, native_key="_native"):
    """Per-source identity and native-field availability, before dedupe.

    `job_key` is scraper.job_key, passed in rather than imported so this module
    stays free of engine imports (and so a harness can measure an alternative
    key without touching production). Sources are walked in the order they
    appear, so "newly unique" means new relative to the sources ahead of it —
    the same registry order the engine fetched them in.
    """
    if _run is None:
        return
    seen = set()
    per_source = {}
    track = "_paid_ix" in _run
    paid_keys = []      # (paid provenance or None, key) per row, arrival order
    for row in raw_rows:
        name = row.get("Source") or ""
        rec = per_source.setdefault(name, {
            "rows": 0, "source_unique": 0, "newly_unique_vs_sweep": 0,
            "unkeyed": 0, "native": {}})
        rec["rows"] += 1
        key = job_key(row)
        if track:
            paid_keys.append((row.get(PAID_UNIT), key))
        if key is None:
            rec["unkeyed"] += 1
        else:
            fresh = key not in seen
            if fresh:
                seen.add(key)
                rec["newly_unique_vs_sweep"] += 1
            rec.setdefault("_keys", set()).add(key)
        for field, value in (row.get(native_key) or {}).items():
            if value not in (None, "", [], {}):
                rec["native"][field] = rec["native"].get(field, 0) + 1
    for rec in per_source.values():
        rec["source_unique"] = len(rec.pop("_keys", ()))
    _run["identity"] = {
        "sweep_unique_keys": len(seen),
        "by_source": per_source,
        "note": "current engine job_key, before dedupe; not verified opportunity "
                "identity — see the V2 forensic audit",
    }
    if track:
        _guarded(_paid_acquired, paid_keys)


def counts(**fields):
    """Sweep-level totals: raw_rows, normalized_rows, eligible_rows, final_rows."""
    if _run is None:
        return
    for key in ("raw_rows", "normalized_rows", "eligible_rows", "final_rows"):
        if key in fields:
            _run[key] = fields[key]


def shadow_evaluation(section):
    """The shadow tranche's evaluation (sources/shadow.py, V2-B3), as its own
    top-level section — beside the production fields, never inside them.

    Absent from every record where shadow did not run, so a sweep with the
    flag off writes exactly what it wrote before. Bounded on the way in like
    everything else here: numbers, booleans and None pass, every string is
    clipped, and nothing but plain containers survives.
    """
    if _run is not None:
        with _LOCK:
            _run["shadow_evaluation"] = _bounded(section)


def _bounded(value):
    if isinstance(value, dict):
        return {_clip(k): _bounded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bounded(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clip(value)


# ---------------------------------------------------------------------------
# Paid units (V2-C1) — planned vs executed, and what each actor start yielded
# ---------------------------------------------------------------------------
PAID_SCHEMA = "search-v2c1.1"

# What scraper stamps on a paid row while a record is open: (unit_id, position
# in that run's dataset). The "_native" bargain again — to_output() never reads
# it, so it cannot reach the CSV or the JSON, and nothing that keys, scores,
# ranks or dedupes a row looks at it.
PAID_UNIT = "_paid_unit"

# Which cost reading may ever be called final. V2-C0's one live run read
# $0.02805 on its terminal poll and $0.01405 as an account delta against a
# settled $0.03005, so neither reading production takes is final, and the one
# that can be is taken only by the research probe, after the fact.
COST_SOURCES = {
    "run_record_at_completion": False,  # usageTotalUsd of the terminal poll's Run
    "account_usage_delta": False,       # month-to-date account usage across the unit
    "run_record_settled": True,         # research probe: re-read until it stops moving
}

# score_and_filter's boundaries in order. A paid row's trace letter is the LAST
# one it reached, which names what removed it.
PAID_STAGES = ("observed_normalized", "post_hard_filter_and_score", "post_recency",
               "post_salary_reachability_visa_eor", "post_arrangement",
               "post_location_eligible", "final_after_dedupe")
_REACH_CODE = dict(zip(PAID_STAGES, "HSRAGDF"))
_ACQUIRED_CODE = {"new": "n", "repeat_in_unit": "u",
                  "repeat_of_earlier_paid": "p", "unkeyed": "k"}
TRACE_LEGEND = ("one token per dataset position. Removed by: H hard filter or "
                "score, S stale, R salary/reachability/visa/EOR, A arrangement, "
                "G geography, D dedupe (eligible, another row won); F final. "
                "Score: + positive, - zero or less, . not scored. Acquired: n new "
                "to the sweep, u repeat within this unit, p repeat of an earlier "
                "paid unit, k no job_key. f: the job_key was also acquired free.")


def _guarded(fn, *args):
    """A V2-C1 hook that cannot fail the sweep. These run inside finalize() and
    the paid loop's per-search try, where an exception would turn a search that
    succeeded into one that failed; a miss becomes a note in the record."""
    try:
        fn(*args)
    except Exception as exc:
        note(f"paid telemetry skipped in {getattr(fn, '__name__', 'a hook')}: "
             f"{type(exc).__name__}")


def paid_plan(units):
    """Every planned paid search, before anything runs (scraper.paid_unit)."""
    if _run is not None:
        _guarded(_paid_plan, units)


def _paid_plan(units):
    entries = [dict(_bounded(u), status="planned") for u in units]
    with _LOCK:
        _run["paid_units"] = entries
        _run["_paid_ix"] = {e["unit_id"]: e for e in entries}


def paid_status(unit_id, status):
    """completed | failed | skipped_done | skipped_budget, from the paid loop."""
    if _run is not None:
        entry = _run.get("_paid_ix", {}).get(unit_id)
        if entry is not None:
            entry["status"] = status


def paid_unvisited(status):
    """Every unit the loop never reached — it only stops early at the spend cap."""
    if _run is not None:
        for entry in _run.get("paid_units", ()):
            if entry["status"] == "planned":
                entry["status"] = status


def cost_observation(source, usd):
    """One cost reading for the open paid unit, kept apart from every other.

    Never summed into, averaged with or overwritten by another source. `final`
    comes from COST_SOURCES, not from the caller, so a provisional reading
    cannot be recorded as final. A reading that does not exist is not
    recorded at all — never as 0.
    """
    unit_ = _current()
    if unit_ is None or usd is None or source not in COST_SOURCES:
        return
    try:
        usd = round(float(usd), 6)
    except (TypeError, ValueError):
        return
    unit_.setdefault("cost_observations", []).append(
        {"source": source, "usd": usd, "observed_at": _now(),
         "final": COST_SOURCES[source]})


def _paid_reach(name, rows):
    """From stage(): the last boundary each paid row reached in this pass, and
    its score sign once scored. A pass starts at the first boundary, so what
    survives is the last finalize pass — the one over the complete row set."""
    first = name == PAID_STAGES[0]
    if first:
        _run["_paid_pos"], _run["_paid_stages"] = {}, []
    positions = _run.setdefault("_paid_pos", {})
    _run.setdefault("_paid_stages", []).append(name)
    for row in rows:
        where = row.get(PAID_UNIT)
        if where is None:
            continue
        p = positions.setdefault(where, {})
        p["reach"] = name
        if not first and "score" in row:
            p["positive"] = row["score"] > 0


def paid_outcome(eligible, final, job_key):
    """What dedupe did to each eligible paid row, read off dedupe's own result.

    `final` is the survivor list, so this names the row that won rather than
    re-deciding it: a paid row missing from `final` lost to the survivor that
    holds its job_key — in the same unit, another paid unit, or a free source.
    A survivor is credited to its own unit only, so one final job is counted
    once. `marginal`: no other unit and no free source had an eligible row with
    that job_key, so without this unit the job would not be in the result.
    """
    if _run is not None and "_paid_ix" in _run:
        _guarded(_paid_outcome, eligible, final, job_key)


def _paid_outcome(eligible, final, job_key):
    positions = _run.setdefault("_paid_pos", {})
    keys = {id(r): job_key(r) for r in eligible}
    kept = {id(r) for r in final}
    survivor, origins = {}, {}
    for r in final:
        if keys.get(id(r)) is not None:
            survivor[keys[id(r)]] = r
    for r in eligible:
        if keys[id(r)] is not None:
            where = r.get(PAID_UNIT)
            origins.setdefault(keys[id(r)], set()).add(where[0] if where else None)
    for r in eligible:
        where = r.get(PAID_UNIT)
        if where is None:
            continue
        p, key = positions.setdefault(where, {}), keys[id(r)]
        if id(r) in kept:
            p["marginal"] = key is None or origins[key] == {where[0]}
        else:
            won = survivor[key].get(PAID_UNIT)
            p["lost_to"] = ("free" if won is None else
                            "same_unit" if won[0] == where[0] else "other_paid")


def _paid_acquired(paid_keys):
    """From identity(): each paid row's job_key against what the sweep had
    already acquired, in arrival order — paid units in plan order, then free —
    so repeat work shows before any filter or score touches it."""
    positions = _run.setdefault("_paid_pos", {})
    free = {key for where, key in paid_keys if where is None and key is not None}
    seen, per_unit = set(), {}
    for where, key in paid_keys:
        if where is None:
            continue
        mine = per_unit.setdefault(where[0], set())
        p = positions.setdefault(where, {})
        p["acquired"] = ("unkeyed" if key is None else
                         "repeat_in_unit" if key in mine else
                         "repeat_of_earlier_paid" if key in seen else "new")
        p["key_also_free"] = key is not None and key in free
        if key is not None:
            mine.add(key)
            seen.add(key)


PAID_EXECUTION_SCHEMA = "search-v2c2.1"


def paid_execution(section):
    """V2-C2's own section, beside C1's paid sections and never inside them:
    how the paid phase was scheduled and what it held against the budget.
    Reservations are authorization exposure, not cost — the cost readings stay
    in cost_observations, untouched. Absent unless SWEEP_PAID_CONCURRENCY ran
    the phase. Amounts, states, clocks and unit ids only."""
    if _run is not None:
        with _LOCK:
            _run["paid_execution"] = dict(_bounded(section),
                                          schema=PAID_EXECUTION_SCHEMA)


def _paid_finish(record):
    """paid_units[i] gets its funnel and trace; paid_summary the sweep-level
    planned-vs-executed view. Execution facts stay in the unit's units[]
    record, joined by unit_id, rather than being written twice."""
    positions = record.get("_paid_pos") or {}
    seen = set(record.get("_paid_stages") or ())
    stages = [s for s in PAID_STAGES if s in seen]
    executed = {u.get("unit_id"): u for u in record["units"]
                if u.get("path") == "paid" and u.get("unit_id")}
    by_unit = {}
    for (unit_id, position), p in positions.items():
        by_unit.setdefault(unit_id, []).append((position, p))
    for entry in record["paid_units"]:
        rows = [p for _, p in sorted(by_unit.get(entry["unit_id"], []),
                                     key=lambda t: t[0])]
        entry["funnel"] = _funnel(entry, executed.get(entry["unit_id"]), rows, stages)
        entry["trace"] = (None if entry["funnel"] is None
                          else " ".join(_token(p) for p in rows))
    record["paid_summary"] = _paid_summary(record, executed)


def _funnel(entry, ex, rows, stages):
    """None when there are no rows to follow — never executed, or failed.
    Zeros only for a run that really returned nothing."""
    if ex is None or not ex.get("ok"):
        return None
    ix = {s: i for i, s in enumerate(PAID_STAGES)}
    reached = [ix.get(p.get("reach"), -1) for p in rows]
    counts = {s: sum(1 for r in reached if r >= ix[s]) for s in stages}
    eligible = [p for p in rows if p.get("reach") in PAID_STAGES[-2:]]
    final = [p for p in rows if p.get("reach") == PAID_STAGES[-1]]
    lost = Counter(p["lost_to"] for p in eligible if "lost_to" in p)
    known = all("acquired" in p for p in rows)
    acquired = Counter(p.get("acquired") for p in rows)
    return {
        "requested": entry.get("requested_depth"),
        "raw": ex.get("raw_count"),
        "normalized": len(rows),
        "stages": counts,
        "stale": (counts["post_hard_filter_and_score"] - counts["post_recency"]
                  if "post_recency" in counts else None),
        "eligible": len(eligible),
        "eligible_positive": sum(1 for p in eligible if p.get("positive")),
        "final": len(final),
        "final_positive": sum(1 for p in final if p.get("positive")),
        "final_marginal": sum(1 for p in final if p.get("marginal")),
        "dedupe_lost": {k: lost.get(k, 0) for k in ("same_unit", "other_paid", "free")},
        "acquired": ({k: acquired.get(k, 0) for k in _ACQUIRED_CODE} if known else None),
        "key_also_free": (sum(1 for p in rows if p.get("key_also_free"))
                          if known else None),
    }


def _token(p):
    score = "." if "positive" not in p else "+" if p["positive"] else "-"
    return (_REACH_CODE.get(p.get("reach"), "?") + score
            + _ACQUIRED_CODE.get(p.get("acquired"), "?")
            + ("f" if p.get("key_also_free") else "."))


def _exposure(units):
    """Worst case = sum of provider ceilings. An unbounded unit is counted,
    never priced."""
    bounded = sum((Decimal(u["charge_ceiling_usd"]) for u in units
                   if u.get("charge_ceiling_usd")), Decimal(0))
    return str(bounded), sum(1 for u in units if not u.get("charge_ceiling_usd"))


def _paid_summary(record, executed):
    units = record["paid_units"]
    status = Counter(u["status"] for u in units)
    attempted = [u for u in units if u["status"] in ("completed", "failed")]
    providers, costs = {}, {}
    for u in units:
        ex, f = executed.get(u["unit_id"]) or {}, u.get("funnel") or {}
        p = providers.setdefault(u["provider"], dict.fromkeys((
            "planned", "completed", "failed", "skipped_done", "skipped_budget",
            "actor_starts", "unit_wall_ms", "wait_ms", "dataset_ms",
            "checkpoint_ms", "actor_run_time_s", "raw", "eligible",
            "eligible_positive", "final", "final_positive", "final_marginal"), 0))
        p["planned"] += 1
        if u["status"] in p:
            p[u["status"]] += 1
        p["actor_starts"] += 1 if ex.get("actor_run_id") else 0
        p["unit_wall_ms"] += ex.get("duration_ms") or 0
        for key in ("wait_ms", "dataset_ms", "checkpoint_ms"):
            p[key] += ex.get(key) or 0
        p["actor_run_time_s"] = round(p["actor_run_time_s"]
                                      + (ex.get("actor_run_time_s") or 0), 3)
        for key in ("raw", "eligible", "eligible_positive", "final",
                    "final_positive", "final_marginal"):
            p[key] += f.get(key) or 0
        for obs in ex.get("cost_observations") or ():
            c = costs.setdefault(obs["source"], {"usd": 0.0, "units": 0,
                                                 "final": obs["final"]})
            c["usd"] = round(c["usd"] + obs["usd"], 6)
            c["units"] += 1
    planned_usd, planned_unbounded = _exposure(units)
    attempted_usd, attempted_unbounded = _exposure(attempted)
    marks = record["milestones"]
    return {
        "schema": PAID_SCHEMA,
        "planned": len(units),
        "attempted": len(attempted),
        "completed": status["completed"],
        "failed": status["failed"],
        "skipped_done": status["skipped_done"],
        "skipped_budget": status["skipped_budget"],
        "unvisited": status["planned"],
        "actor_starts": sum(p["actor_starts"] for p in providers.values()),
        "planned_bounded_exposure_usd": planned_usd,
        "planned_unbounded_units": planned_unbounded,
        "attempted_bounded_exposure_usd": attempted_usd,
        "attempted_unbounded_units": attempted_unbounded,
        "paid_phase_ms": (marks["paid_phase_done"] - marks["paid_phase_start"]
                          if {"paid_phase_start", "paid_phase_done"} <= set(marks)
                          else None),
        "by_provider": providers,
        # Sums of provisional readings are provisional; `final` says so.
        "cost_observed": costs,
        "trace_legend": TRACE_LEGEND,
    }


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------
def finish():
    """Write the record and return its path, or None when telemetry is off.

    A write failure is swallowed: telemetry is an observer, and an observer that
    can fail a sweep is worse than no observer. It reports the miss on stderr
    and the sweep carries on.
    """
    global _run
    if _run is None:
        return None
    record, _run = _run, None
    _set_current(None)
    record["finished_at"] = _now()
    record["duration_ms"] = round((time.monotonic() - record.pop("_t0")) * 1000)
    ready = record["milestones"].get("results_ready")
    if ready is not None:
        # V2-B5: how long the sweep ran after the user's result was final —
        # the wait SWEEP_RESULTS_READY_EARLY takes off the user's screen.
        record["post_result_ms"] = record["duration_ms"] - ready
    record["raw_rows"] = sum(u["raw_count"] for u in record["units"])
    if "paid_units" in record:
        try:
            _paid_finish(record)
        except Exception as exc:
            record["notes"].append(f"paid units not assembled: {type(exc).__name__}")
    for key in ("_paid_ix", "_paid_pos", "_paid_stages"):
        record.pop(key, None)
    path = os.path.join(record["output_dir"], "telemetry",
                        f"sweep_{record['sweep_id']}.json")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2, ensure_ascii=False, default=str)
    except Exception as exc:
        import sys
        print(f"  (telemetry: could not write {path}: {exc})", file=sys.stderr)
        return None
    return path


def record():
    """The open record, for a test or an in-process harness. Not a copy — read
    it, do not mutate it."""
    return _run


def demo():
    """Offline self-check: `python telemetry.py`."""
    os.environ[FLAG] = "1"
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        sid = start("free", tmp, engine_revision="deadbeef")
        assert sid and active()
        with unit("free", "greenhouse", board="greenhouse:acme"):
            observed(raw=10, normalized=10, gated=4, requests=1)
            retried()
        try:
            with unit("free", "wwr", board="wwr"):
                raise urllib.error.HTTPError("u", 404, "nf", None, None)
        except urllib.error.HTTPError:
            pass        # the unit records it; the context manager must not swallow
        rec = record()
        # failed() reaches the same state from inside a caller's own except —
        # which is how fetch_free reports, since it catches per source itself.
        with unit("free", "lever", board="lever:acme"):
            failed(urllib.error.HTTPError("u", 500, "err", None, None))
        assert rec["units"][2]["failure_category"] == "http_500", rec["units"][2]
        assert rec["sources_failed"] == 2, rec
        rec["units"].pop()
        rec["sources_attempted"] -= 1
        rec["sources_failed"] -= 1
        assert rec["sources_attempted"] == 2, rec
        assert rec["sources_succeeded"] == 1 and rec["sources_failed"] == 1, rec
        assert rec["units"][0]["retries"] == 1, rec["units"][0]
        assert rec["units"][1]["failure_category"] == "http_404", rec["units"][1]
        assert "first_raw" in rec["milestones"], rec["milestones"]
        # A token handed to paid_run() must not survive: unknown keys are dropped.
        with unit("paid", "linkedin", query="Engineer", location="Bengaluru"):
            paid_run(actor_id="x/y", actor_run_id="r1", token="SECRET",
                     APIFY_TOKEN="SECRET")
            assert "token" not in rec["units"][2], rec["units"][2]
            assert not any("SECRET" in str(v) for v in rec["units"][2].values())
        before = rec["sources_attempted"]
        with unit("shadow", "greenhouse", board="greenhouse:fivetran", shadow=True):
            observed(raw=7, normalized=7, gated=2, requests=1)
        assert rec["sources_attempted"] == before, "shadow inflated source count"
        assert len(rec["shadow_units"]) == 1, rec["shadow_units"]
        assert rec["shadow_units"][0]["raw_count"] == 7
        stage("post_recency", [{"Source": "greenhouse:acme"}] * 3)
        assert rec["stages"]["post_recency"]["total"] == 3
        identity([{"Source": "a", "_native": {"native_id": "1"}}],
                 lambda r: ("ct", "a", "b"))
        assert rec["identity"]["sweep_unique_keys"] == 1, rec["identity"]
        assert rec["identity"]["by_source"]["a"]["native"]["native_id"] == 1
        out = finish()
        assert out and os.path.exists(out), out
        assert not active()
        body = open(out, encoding="utf-8").read()
        assert "SECRET" not in body

    # Flag off: every entry point is inert and nothing is written.
    os.environ[FLAG] = "0"
    assert start("free", "/nonexistent") is None
    assert not active()
    for call in (lambda: observed(raw=1), lambda: mark("x"), retried, request,
                 lambda: stage("s", [{}]), lambda: counts(raw_rows=1),
                 lambda: note("n"), lambda: eligible_progress(50),
                 lambda: paid_run(actor_id="a"), lambda: partial("p"),
                 lambda: failed(ValueError("v")),
                 lambda: identity([{}], lambda r: None),
                 lambda: paid_plan([{"unit_id": "paid_000"}]),
                 lambda: paid_status("paid_000", "completed"),
                 lambda: paid_unvisited("skipped_budget"),
                 lambda: cost_observation("account_usage_delta", 1.0),
                 lambda: paid_outcome([{}], [{}], lambda r: None),
                 lambda: paid_execution({"workers": 2})):
        call()
    with resumed(None, time.monotonic()) as nothing:
        assert nothing is None and _current() is None
    with unit("free", "nothing"):
        pass
    assert finish() is None and record() is None
    print("telemetry demo ok")


if __name__ == "__main__":
    demo()
