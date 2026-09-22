"""Bounded, deterministic concurrency for ONE free provider's board fetches.

    SWEEP_FREE_LEVER_CONCURRENCY=1     default OFF
    SWEEP_FREE_LEVER_WORKERS=4         default 4, clamped to 1..8

Three real production Free Sweeps say the same thing. Lever's 21 boards cost
~80 source-seconds a run at a 3.14s median and an 8.17s p95, against Greenhouse's
0.34s median over 54 boards — and across 63 Lever executions there were zero
retries and zero failures. Lever is not flaky, it is slow, and a slow
independent HTTP GET is the one thing waiting in parallel actually fixes.

WHAT THIS IS NOT. It does not reorder, prioritise, skip, cache or drop a board.
It does not touch any other provider, the feeds, or anything paid. Every board
configured is still fetched, with the same adapter, the same timeout and the
same retry policy. The only thing that changes is how many of them are waiting
on the network at once.

DETERMINISM IS THE WHOLE DESIGN, not a property bolted on afterwards:

    boards          = list(...)          registry order, frozen up front
    futures         = [submit(b) for b in boards]     all queued immediately
    results         = [f.result() for f in futures]   collected in THAT order

There is no completion-order code path to get wrong. `as_completed` is never
called and no worker ever touches a shared list: each task returns its own
isolated BoardResult, and the coordinator — the calling thread, alone — walks
those results in registry order to extend the row list, emit the log lines and
attach the telemetry units. A board that finishes first is simply a future that
is already done when the coordinator reaches it.

Failure isolation is unchanged and for the same reason: `_fetch_one` catches per
board exactly as the serial loop does, so a raising board becomes one recorded
failure and the rest are untouched.
"""
import os
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

import telemetry

from . import ats

FLAG = "SWEEP_FREE_LEVER_CONCURRENCY"
WORKERS_ENV = "SWEEP_FREE_LEVER_WORKERS"

# The ONLY providers this path may ever touch. Lever is here because production
# telemetry measured it as the cost; nothing else has earned a second execution
# model. Adding a name here is a deliberate, separately reviewed decision, which
# is why the scope is a constant and not an argument.
PROVIDERS = ("lever",)

DEFAULT_WORKERS = 4
# Lever's 21 boards are one provider's servers. The measured experiment is 2-4;
# the cap exists so a typo in an environment variable cannot turn a bounded
# experiment into 21 simultaneous requests at somebody else's host.
MAX_WORKERS = 8
MIN_WORKERS = 1

BoardResult = namedtuple("BoardResult", "token company rows error unit")


def enabled():
    """Read per call, so a rollback lands on the next sweep."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def workers():
    """Configured worker count, clamped into MIN_WORKERS..MAX_WORKERS.

    Clamped rather than rejected: this is a latency knob on a path that is off
    by default, and refusing to run a sweep over a bad number would turn a
    performance setting into an outage. A nonsense value falls back to the
    default instead.
    """
    raw = os.environ.get(WORKERS_ENV, "").strip()
    if not raw:
        return DEFAULT_WORKERS
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_WORKERS
    return max(MIN_WORKERS, min(MAX_WORKERS, n))


def applies(platform):
    """Whether this platform's boards go through the concurrent path.

    Both halves in one place so `fetch_free` asks one question, and so the
    provider scope can be asserted without reaching into fetch_free.
    """
    return enabled() and platform in PROVIDERS


def _fetch_one(platform, token, company, keep_title, keep_location, is_home):
    """One board, in a worker thread. Returns; never raises.

    The telemetry unit is DEFERRED: it is opened and filled here, on this
    thread, but attached to the sweep record by the coordinator. `telemetry`
    keeps the open unit in thread-local storage precisely so the counts, retries
    and failure category recorded in here land on this board and no other.
    """
    with telemetry.unit("free", platform, board=f"{platform}:{token}",
                        timeout_s=25, defer=True) as unit:
        try:
            rows = ats.fetch(platform, token, company, keep_title,
                             keep_location, is_home)
            return BoardResult(token, company, rows, None, unit)
        except Exception as exc:        # same isolation as the serial loop
            telemetry.failed(exc)
            return BoardResult(token, company, [], exc, unit)


def fetch_boards(platform, boards, keep_title, keep_location, is_home=None,
                 log=print):
    """Every board for one platform, fetched concurrently, returned in registry
    order. The rows are byte-identical to what the serial loop would return.

    The log lines are emitted here rather than inside the workers, so their
    order is the registry's too — they arrive together once the provider is
    done instead of trickling out, which is a change to when the log is written
    and not to what the sweep produces.
    """
    items = list(boards.items())            # frozen: registry order is the contract
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=workers(),
                            thread_name_prefix=f"sweep-{platform}") as pool:
        futures = [pool.submit(_fetch_one, platform, token, company,
                               keep_title, keep_location, is_home)
                   for token, company in items]
        # Collected in SUBMISSION order. `.result()` on an already-finished
        # future returns immediately, so this is not serialising the work — it
        # is refusing to let completion order become result order.
        results = [f.result() for f in futures]

    rows = []
    for res in results:
        telemetry.attach(res.unit)
        if res.error is not None:
            log(f"  {platform:<16} {res.company:<22} ! {res.error}")
            continue
        rows.extend(res.rows)
        home = f" hires-home={res.rows[0]['hires_home']}" if res.rows else ""
        log(f"  {platform:<16} {res.company:<22} {len(res.rows):>4} jobs{home}")
    return rows


def demo():
    """Offline self-check: `python -m sources.concurrency`."""
    was = {k: os.environ.get(k) for k in (FLAG, WORKERS_ENV)}
    try:
        os.environ.pop(FLAG, None)
        assert not enabled() and not applies("lever"), "concurrency defaults ON"

        os.environ[FLAG] = "1"
        assert applies("lever")
        for other in ("greenhouse", "ashby", "smartrecruiters", "breezy", "feed"):
            assert not applies(other), other

        os.environ.pop(WORKERS_ENV, None)
        assert workers() == DEFAULT_WORKERS
        for raw, want in (("2", 2), ("3", 3), ("4", 4), ("1", 1),
                          ("0", MIN_WORKERS), ("-5", MIN_WORKERS),
                          ("999", MAX_WORKERS), ("banana", DEFAULT_WORKERS),
                          ("", DEFAULT_WORKERS)):
            os.environ[WORKERS_ENV] = raw
            assert workers() == want, f"{raw!r} -> {workers()} != {want}"
    finally:
        for key, value in was.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("concurrency demo ok")


if __name__ == "__main__":
    demo()
