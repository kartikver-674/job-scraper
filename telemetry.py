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
"""
import json
import os
import socket
import threading
import time
import urllib.error
import uuid
from datetime import datetime, timezone

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


def paid_run(**fields):
    """Actor-run metadata for the open paid unit.

    Allowlisted keys only. A token is not in this list and cannot be added by a
    caller passing one, because unknown keys are dropped rather than stored.
    """
    unit_ = _current()
    if unit_ is None:
        return
    allowed = ("actor_id", "actor_build_id", "actor_run_id", "dataset_id",
               "actor_status", "actor_started_at", "actor_finished_at",
               "dataset_retrieved_at", "poll_count", "estimated_cost_usd",
               "reported_cost_usd", "billed_delta_usd",
               # The provider-enforced ceiling this run was started under.
               # Without it a later cost analysis cannot tell a cheap run from
               # a run that was cheap because it was capped.
               "max_total_charge_usd")
    for key in allowed:
        if key in fields:
            value = fields[key]
            unit_[key] = value if isinstance(value, (int, float, type(None))) \
                else _clip(value)


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
    for row in raw_rows:
        name = row.get("Source") or ""
        rec = per_source.setdefault(name, {
            "rows": 0, "source_unique": 0, "newly_unique_vs_sweep": 0,
            "unkeyed": 0, "native": {}})
        rec["rows"] += 1
        key = job_key(row)
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
    record["raw_rows"] = sum(u["raw_count"] for u in record["units"])
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
                 lambda: identity([{}], lambda r: None)):
        call()
    with unit("free", "nothing"):
        pass
    assert finish() is None and record() is None
    print("telemetry demo ok")


if __name__ == "__main__":
    demo()
