"""Search Engine V2-C2: paid searches wait on Apify in parallel — and the budget,
the plan, the bytes and the survivors stay what they were.

Under SWEEP_PAID_CONCURRENCY every provider-bounded start's FULL ceiling is
reserved before the start can happen and stays committed for the sweep, whatever
the run later costs; up to SWEEP_PAID_WORKERS such searches run at once; their
results are integrated in plan order; searches with no provider ceiling run one
at a time; and every finalize pass scores each row once.

NO REAL CREDENTIAL AND NO REAL CLIENT. scraper.main() runs in-process against
KeyedClient, a thread-safe stand-in for apify_client.ApifyClient that matches a
start to its script by the actor input (so which worker starts first cannot
change what a search returns) and logs every request, with the thread and the
moment it was made. _require_token is patched except where the account choice
itself is under test, and there dotenv is stubbed so no .env is read. Sockets
are denied for the module — which does not reach apify-client's Rust transport;
the stand-in is what makes a real call impossible here, as in V2-C0 and V2-C1.
"""
import contextlib
import copy
import csv
import glob
import io
import itertools
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import experience_guard
import scraper
import telemetry
from bench import paid_guard
from bench import search_v2_paid_probe as probe
from sources import concurrency, shadow
from sweep.tests import test_paid_observability as c1
from sweep.tests import test_search_v2_shadow_eval as b3

ROOT = Path(__file__).resolve().parents[2]
TOKEN = c1.TOKEN
LINKEDIN = c1.LINKEDIN
INDEED = "misceres/indeed-scraper"
TODAY = c1.TODAY
RICH = c1.RICH
CEILING = Decimal("0.046")
Script = c1.Script
li = c1.li
_sleep = time.sleep                 # real, before any test patches time.sleep
SERIAL = None                       # workers=SERIAL: the flag off, the old loop

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def deny(*a, **kw):
        raise AssertionError("a C2 test tried to open a socket")
    socket.socket.connect = deny
    socket.create_connection = deny


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


class Crash(BaseException):
    """A hard stop no `except Exception` absorbs — what an OOM kill or a
    SIGKILL looks like from inside, as V2-B5's tests use it."""


# ---------------------------------------------------------------------------
# The provider, scripted and thread-safe
# ---------------------------------------------------------------------------
def input_key(actor_id, run_input):
    return actor_id + json.dumps(run_input, sort_keys=True)


class KeyedClient:
    """apify_client.ApifyClient's stand-in for concurrent tests. Each start is
    matched to its Script by (actor, input) and its run id comes from the
    search's plan position, so thread timing changes WHEN a search runs, never
    what it returns. Every request is logged under a lock; `live`/`peak` count
    runs in flight at the provider, per actor and overall ("*")."""

    def __init__(self, by_input, account=None):
        self.by_input = by_input
        self.account, self.readable = list(account or []), account is not None
        self.calls, self.events, self.tokens = [], [], []
        self.runs, self.live, self.peak = {}, Counter(), Counter()
        self._lock = threading.Lock()

    def __call__(self, token=None, **kw):           # ApifyClient(token)
        with self._lock:
            self.tokens.append(token)
        return self

    def _log(self, *call):
        with self._lock:
            self.calls.append(call)
            self.events.append((time.monotonic(), threading.current_thread().name,
                                call))

    def actor(self, actor_id):
        client = self

        class Actor:
            # Declares max_total_charge_usd, as apify-client >= 3.1.0 does.
            def start(self, *, run_input=None, run_timeout=None,
                      max_total_charge_usd=None):
                return client._start(actor_id, run_input, max_total_charge_usd)
        return Actor()

    def _start(self, actor_id, run_input, ceiling):
        index, script = self.by_input[input_key(actor_id, run_input)]
        self._log("start", actor_id, copy.deepcopy(run_input), ceiling)
        _sleep(script.delay.get("start", 0))
        if script.start_error:
            raise script.start_error
        run_id = f"run_{index + 1}"
        with self._lock:
            self.runs[run_id] = [script, 0, actor_id, True]
            for key in (actor_id, "*"):
                self.live[key] += 1
                self.peak[key] = max(self.peak[key], self.live[key])
        return SimpleNamespace(
            id=run_id, build_id=f"build_{run_id}", started_at="2026-09-24T00:00:00Z",
            options=SimpleNamespace(max_total_charge_usd=(
                None if ceiling is None else float(ceiling))))

    def _end(self, run_id):
        with self._lock:
            state = self.runs[run_id]
            if state[3]:
                state[3] = False
                for key in (state[2], "*"):
                    self.live[key] -= 1

    def run(self, run_id):
        def abort():
            self._log("abort", run_id)
            self._end(run_id)
        return SimpleNamespace(get=lambda: self._get(run_id), abort=abort)

    def _get(self, run_id):
        self._log("get", run_id)
        with self._lock:
            state = self.runs[run_id]
            state[1] += 1
            script, polls, live = state[0], state[1], state[3]
        _sleep(script.delay.get("get", 0))
        done = polls >= script.polls or not live
        if done:
            self._end(run_id)
        status = ("ABORTED" if not live else script.status) if done else "RUNNING"
        return SimpleNamespace(
            id=run_id, status=status,
            finished_at="2026-09-24T00:00:30Z" if done else None,
            default_dataset_id=f"ds_{run_id}", usage_total_usd=script.usage,
            charged_event_counts=dict(script.events), build_number="1.7.17",
            stats=SimpleNamespace(run_time_secs=script.run_time))

    def dataset(self, dataset_id):
        run_id = dataset_id[len("ds_"):]

        def items():
            self._log("dataset", dataset_id)
            script = self.runs[run_id][0]
            _sleep(script.delay.get("dataset", 0))
            yield from copy.deepcopy(script.items)
        return SimpleNamespace(iterate_items=items)

    def user(self):
        def limits():
            self._log("limits")
            if not self.readable:
                raise RuntimeError("account unreadable")
            with self._lock:     # the last reading repeats once the script runs out
                usage = self.account.pop(0) if len(self.account) > 1 else self.account[0]
            return SimpleNamespace(model_dump=lambda: {"current": {
                "monthly_usage_usd": usage}})
        return SimpleNamespace(limits=limits)

    # -- what the tests read back ------------------------------------------
    def kinds(self, kind):
        return [c for c in self.calls if c[0] == kind]

    def order(self, kind):
        """Run ids in the order `kind` requests were made for them."""
        return [c[1][len("ds_"):] if kind == "dataset" else c[1]
                for c in self.calls if c[0] == kind]


# ---------------------------------------------------------------------------
# One sweep through scraper.main()
# ---------------------------------------------------------------------------
class Swept:
    def __init__(self, out, log, client, error):
        self.out, self.log, self.client, self.error = out, log, client, error
        base = b3.Result(out, [], log)
        self.seen = base.seen
        self.telemetry, self.telemetry_text = base.telemetry, base.telemetry_text
        self.files = base.files

        def newest(pattern):
            found = sorted(glob.glob(os.path.join(out, pattern)), key=os.path.getmtime)
            return Path(found[-1]).read_bytes() if found else None
        self.csv, self.json = newest("jobs_*.csv"), newest("jobs_*.json")
        self.csvs = {os.path.basename(p): Path(p).read_bytes()
                     for p in glob.glob(os.path.join(out, "jobs_*.csv"))}
        path = os.path.join(out, ".done_combos")
        self.done = Path(path).read_text().splitlines() if os.path.exists(path) else []

    @property
    def rows(self):
        return list(csv.DictReader(io.StringIO(self.csv.decode("utf-8-sig"))))

    @property
    def execution(self):
        return (self.telemetry or {}).get("paid_execution")

    def status(self):
        return [u["status"] for u in self.telemetry["paid_units"]]


def _join_workers():
    """Let any search still in flight finish while the fakes are in place: a
    crash test stops the coordinator, not the daemon threads it started."""
    for t in threading.enumerate():
        if t.name.startswith("sweep-paid-"):
            t.join(10)


def plan_of(keywords, sites):
    """(site, search) in the order main() will visit them."""
    return [(s, q) for s in scraper.SITES if s in sites
            for q in scraper.build_search_plan(list(keywords),
                                               scraper.SITES[s]["locations"])]


def c2_sweep(scripts, *, workers=SERIAL, on=True, keywords=("Alpha Engineer",),
             locations=("Bengaluru",), site_locations=None, sites=("linkedin",),
             site_order=None, account=None, budget=None, free=False, shadow_on=False,
             done=(), env=None, patches=(), out=None, token_patch=True):
    """A paid (or paid + free) sweep, offline. `scripts` are in plan order.
    workers=SERIAL runs with SWEEP_PAID_CONCURRENCY unset; an int, with it on
    and SWEEP_PAID_WORKERS set to it. `out` keeps the directory (resume)."""
    keep = out is not None
    out = out or tempfile.mkdtemp()
    saved = (sys.argv, scraper.SITES, scraper.ATS_BOARDS, scraper.FEEDS,
             dict(scraper.SETTINGS))
    error = None
    try:
        base = copy.deepcopy(saved[1])
        places = dict.fromkeys(base, list(locations)) | dict(site_locations or {})
        scraper.SITES = {k: dict(base[k], enabled=k in sites, locations=list(places[k]))
                         for k in (site_order or base)}
        scraper.ATS_BOARDS = copy.deepcopy(c1.FREE_REGISTRY) if free else {}
        scraper.FEEDS = {}
        scraper.SETTINGS.update(output_dir=out, max_spend_usd=budget)
        if done:
            with open(os.path.join(out, ".done_combos"), "a") as fh:
                fh.writelines(f"{TODAY}|{d}|\n" for d in done)
        by_input = {}
        for index, ((site, search), script) in enumerate(
                zip(plan_of(keywords, sites), scripts)):
            run_input = scraper.build_input(site, scraper.effective_search(site, search))
            by_input.setdefault(input_key(scraper.SITES[site]["actor"], run_input),
                                (index, script))
        client = KeyedClient(by_input, account)
        mode = ({scraper.PAID_CONCURRENCY_FLAG: None, scraper.PAID_WORKERS_ENV: None}
                if workers is SERIAL else
                {scraper.PAID_CONCURRENCY_FLAG: "1",
                 scraper.PAID_WORKERS_ENV: str(workers)})
        sys.argv = (["scraper.py", "--keywords", ",".join(keywords), "--yes"]
                    + ([] if free else ["--no-free"]))
        log = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(b3._env(**dict(
                b3.BASE_ENV, **{telemetry.FLAG: "1" if on else None},
                **({shadow.FLAG: "1"} if shadow_on else {}), **mode, **(env or {}))))
            stack.enter_context(b3._served(
                b3.routes(c1.FREE_BOARDS, None if shadow_on else {}) if free else {}))
            if token_patch:
                stack.enter_context(mock.patch.object(scraper, "_require_token",
                                                      return_value=TOKEN))
            stack.enter_context(mock.patch("apify_client.ApifyClient", client))
            stack.enter_context(mock.patch.object(scraper.time, "sleep",
                                                  lambda *_: None))
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(log))
            stack.enter_context(contextlib.redirect_stderr(log))
            try:
                scraper.main()
            except SystemExit as exc:
                log.write(f"\nSystemExit: {exc}")
            except Crash as exc:
                error = exc
            finally:
                _join_workers()
        return Swept(out, log.getvalue(), client, error)
    finally:
        (sys.argv, scraper.SITES, scraper.ATS_BOARDS, scraper.FEEDS) = saved[:4]
        scraper.SETTINGS.clear()
        scraper.SETTINGS.update(saved[4])
        if not keep:
            shutil.rmtree(out, ignore_errors=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
KW = tuple(f"K{u} Engineer" for u in range(6))


def solo(u, n=2):
    """Rows only unit u returns: distinct employers, each final and positive."""
    return [li(f"{u}{j:02d}", "Full Stack Engineer", f"Solo {u}x{j}", RICH)
            for j in range(n)]


def twin(job_id):
    """One posting that several units return: same employer, title and text,
    so the same job_key and an equal score — only arrival can separate them."""
    return li(job_id, "Full Stack Engineer", "Twin Works", RICH)


def label(u, location="Bengaluru"):
    return f"{KW[u]} @ {location}"


def staggered(unit_items, slow_first=True, usage=0.03005):
    """One Script per unit; with slow_first the FIRST planned unit is the last
    to finish, so completion order runs against plan order."""
    n = len(unit_items)
    return [Script(items, usage=usage, polls=2,
                   delay={"get": 0.03 * ((n - u) if slow_first else (u + 1))})
            for u, items in enumerate(unit_items)]


def indeed_item(job_id, title, company, desc=RICH, location="Remote"):
    return {"positionName": title, "company": company, "location": location,
            "description": desc, "postingDateParsed": TODAY,
            "url": f"https://www.indeed.com/viewjob?jk={job_id}"}


def lines_in_plan_order(log):
    return re.findall(r"^\s+\[(\d+)/\d+\] (\S+ Engineer)", log, re.M)


# ===========================================================================
# The switch and the worker count
# ===========================================================================
class FlagAndWorkers(unittest.TestCase):

    def test_default_is_off(self):
        with b3._env(**{scraper.PAID_CONCURRENCY_FLAG: None}):
            self.assertFalse(scraper.paid_concurrency())
        for value, on in (("1", True), ("true", True), ("ON", True), (" yes ", True),
                          ("0", False), ("", False), ("off", False), ("2", False),
                          ("enabled", False)):
            with b3._env(**{scraper.PAID_CONCURRENCY_FLAG: value}):
                self.assertEqual(scraper.paid_concurrency(), on, value)

    def test_workers_default_two_clamped_one_to_four(self):
        for value, n in ((None, 2), ("", 2), ("1", 1), ("2", 2), ("3", 3), ("4", 4),
                         ("0", 1), ("-9", 1), ("999", 4), ("banana", 2), ("4.5", 2)):
            with b3._env(**{scraper.PAID_WORKERS_ENV: value}):
                self.assertEqual(scraper.paid_workers(), n, value)

    def test_no_deployment_file_turns_it_on(self):
        for flag in (scraper.PAID_CONCURRENCY_FLAG, scraper.PAID_WORKERS_ENV):
            f = re.escape(flag)
            for path in ("render.yaml", "deploy/sweep_worker.py", "requirements.txt",
                         "gunicorn.conf.py", "config.py"):
                body = (ROOT / path).read_text(encoding="utf-8")
                self.assertEqual(re.findall(
                    r"""\benv\[["']%(f)s["']\]\s*=|\bkey:\s*%(f)s\b|^\s*%(f)s\s*="""
                    % {"f": f}, body, re.M), [], f"{path} sets {flag}")

    def test_flag_off_never_enters_the_scheduler(self):
        """Mutation L's test: with the flag off the serial loop runs, and the
        scheduler, its section and its memo are nowhere."""
        entered = []
        real = scraper.paid_phase_c2

        def spy(*a, **kw):
            entered.append(1)
            return real(*a, **kw)
        got = c2_sweep([Script(solo(0)), Script(solo(1))], keywords=KW[:2],
                       patches=[mock.patch.object(scraper, "paid_phase_c2", spy)])
        self.assertEqual(entered, [])
        self.assertNotIn("paid_execution", got.telemetry)
        self.assertEqual(len(got.client.kinds("start")), 2)
        on = c2_sweep([Script(solo(0)), Script(solo(1))], keywords=KW[:2], workers=2,
                      patches=[mock.patch.object(scraper, "paid_phase_c2", spy)])
        self.assertEqual(entered, [1])
        self.assertIn("paid_execution", on.telemetry)

    def test_a_free_sweep_never_enters_it_even_with_the_flag(self):
        entered, scored = [], []
        real_score = scraper.score_job

        def count(row):
            scored.append(row.get(scraper.SCORED))
            return real_score(row)
        with mock.patch.object(scraper, "paid_phase_c2",
                               lambda *a, **kw: entered.append(1)), \
                mock.patch.object(scraper, "score_job", count):
            on = b3.sweep({scraper.PAID_CONCURRENCY_FLAG: "1",
                           scraper.PAID_WORKERS_ENV: "4"})
        off = b3.sweep()
        self.assertEqual(entered, [])
        self.assertEqual((on.csv, on.json, on.seen), (off.csv, off.json, off.seen))
        self.assertTrue(scored)
        self.assertEqual(set(scored), {None}, "a free sweep must re-score as before")
        self.assertNotIn("paid_execution", on.telemetry)


# ===========================================================================
# Serial parity: the scheduler at one worker IS the serial loop
# ===========================================================================
class SerialParity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        def run(workers):
            return c1.attribution(on=True, env={
                scraper.PAID_CONCURRENCY_FLAG: None if workers is SERIAL else "1",
                scraper.PAID_WORKERS_ENV: None if workers is SERIAL else str(workers)})
        cls.serial, cls.one = run(SERIAL), run(1)

    def test_the_same_requests_inputs_ceilings_in_the_same_order(self):
        self.assertEqual(self.serial.client.calls, self.one.client.calls)
        starts = [c for c in self.one.client.calls if c[0] == "start"]
        self.assertEqual([c[3] for c in starts], [CEILING, CEILING])
        for _, actor, run_input, _ in starts:
            self.assertEqual((run_input["limitPerSource"], run_input["count"],
                              run_input["scrapeCompany"]), (15, 15, False))

    def test_the_same_bytes(self):
        self.assertEqual(self.serial.csv, self.one.csv)
        self.assertEqual(self.serial.json, self.one.json)
        self.assertEqual(self.serial.seen, self.one.seen)

    def test_the_same_c1_record_but_the_clocks(self):
        def untimed(value):
            if isinstance(value, dict):
                return {k: untimed(v) for k, v in value.items()
                        if not re.search(r"(_ms|_at)$", k)}
            if isinstance(value, list):
                return [untimed(v) for v in value]
            return value
        s, o = self.serial.telemetry, self.one.telemetry
        self.assertEqual(untimed(s["units"]), untimed(o["units"]))
        self.assertEqual(s["paid_units"], o["paid_units"])
        self.assertEqual(untimed(s["paid_summary"]), untimed(o["paid_summary"]))
        self.assertEqual(s["stages"], o["stages"])
        self.assertEqual(s["identity"], o["identity"])
        self.assertNotIn("paid_execution", s)
        self.assertEqual(o["paid_execution"]["workers"], 1)
        self.assertEqual(o["paid_execution"]["peak_in_flight"], 1)

    def test_the_done_ledger_is_the_serial_ledger(self):
        def ledger(workers):
            got = c2_sweep(staggered([solo(u) for u in range(3)]), keywords=KW[:3],
                           workers=workers)
            return got.done
        self.assertEqual(ledger(SERIAL), ledger(1))
        self.assertEqual(ledger(1), [f"{TODAY}|linkedin|{KW[u]}|Bengaluru|"
                                     for u in range(3)])

    def test_where_the_reservation_stops_what_the_lagging_guard_let_through(self):
        """The one designed difference, not hidden: $0.10 of budget, an
        account that has not caught up (delta $0), runs that settle cheap.
        The old guard sees $0 and starts all four; C2 holds two ceilings
        ($0.092) and refuses the third, at one worker and at four."""
        scripts = [Script(solo(u)) for u in range(4)]
        old = c2_sweep(scripts, keywords=KW[:4], budget=0.10, account=[5.0])
        self.assertEqual(len(old.client.kinds("start")), 4)
        for workers in (1, 4):
            new = c2_sweep(scripts, keywords=KW[:4], budget=0.10, account=[5.0],
                           workers=workers)
            self.assertEqual(len(new.client.kinds("start")), 2, workers)
            self.assertEqual(new.status(), ["completed", "completed",
                                            "skipped_budget", "skipped_budget"])
            self.assertIn("spend cap $0.10: $0.092 held + the next start's $0.046 "
                          "ceiling would exceed it", new.log)


# ===========================================================================
# Concurrent parity: the same result at every worker count
# ===========================================================================
class ConcurrentParity(unittest.TestCase):
    """Four units, every one returning the same twin posting, the FIRST
    planned finishing LAST. Plus a free source, so the paid rows are merged
    with free ones as in production."""

    @classmethod
    def setUpClass(cls):
        units = [solo(u) + [twin(f"9{u}")] for u in range(4)]
        cls.runs = {w: c2_sweep(staggered(units), workers=w, keywords=KW[:4],
                                free=True, account=[1.0, 1.03, 1.06, 1.09, 1.12])
                    for w in (SERIAL, 1, 2, 3, 4)}

    def test_byte_identical_at_every_worker_count(self):
        serial = self.runs[SERIAL]
        self.assertGreater(len(serial.rows), 8)
        for w, got in self.runs.items():
            self.assertEqual((got.csv, got.json, got.seen), (serial.csv, serial.json,
                                                             serial.seen), w)
            self.assertEqual(got.done, serial.done, w)

    def test_the_same_starts_with_the_same_inputs_and_ceilings(self):
        def starts(got):
            return sorted(json.dumps([c[1], c[2], str(c[3])], sort_keys=True)
                          for c in got.client.kinds("start"))
        for w, got in self.runs.items():
            self.assertEqual(starts(got), starts(self.runs[SERIAL]), w)
            self.assertEqual(len(got.client.kinds("start")), 4)
            # Same polls and account reads: concurrency moves requests, never adds.
            self.assertEqual(Counter(c[0] for c in got.client.calls),
                             Counter(c[0] for c in self.runs[SERIAL].client.calls), w)

    def test_completion_order_really_runs_against_plan_order(self):
        """Otherwise a determinism test whose stub finishes in order proves
        nothing (V2-B2's rule)."""
        self.assertEqual(self.runs[SERIAL].client.order("dataset"),
                         ["run_1", "run_2", "run_3", "run_4"])
        for w in (2, 3, 4):
            order = self.runs[w].client.order("dataset")
            self.assertNotEqual(order, sorted(order), w)
        self.assertEqual(self.runs[4].client.order("dataset"),
                         ["run_4", "run_3", "run_2", "run_1"])

    def test_the_first_planned_twin_wins_whoever_finished_first(self):
        for w, got in self.runs.items():
            twins = [r for r in got.rows if r["company"] == "Twin Works"]
            self.assertEqual([(r["search_query"], r["apply_url"]) for r in twins],
                             [(label(0), "https://www.linkedin.com/jobs/view/90")], w)

    def test_log_ledger_and_units_follow_plan_order(self):
        for w, got in self.runs.items():
            self.assertEqual(lines_in_plan_order(got.log),
                             [(str(u + 1), KW[u]) for u in range(4)], w)
            self.assertEqual(got.done, [f"{TODAY}|linkedin|{KW[u]}|Bengaluru|"
                                        for u in range(4)], w)
            paid = [u["unit_id"] for u in got.telemetry["units"] if u["path"] == "paid"]
            self.assertEqual(paid, [f"paid_00{u}" for u in range(4)], w)
            self.assertEqual([u["trace"] for u in got.telemetry["paid_units"]],
                             [u["trace"] for u in self.runs[SERIAL].telemetry["paid_units"]])

    def test_concurrency_actually_happens_and_stays_bounded(self):
        for w in (1, 2, 3, 4):
            ex = self.runs[w].execution
            self.assertEqual(ex["workers"], w)
            self.assertEqual(self.runs[w].client.peak[LINKEDIN], w)
            self.assertEqual(ex["peak_in_flight"], w)
        self.assertEqual(self.runs[SERIAL].client.peak[LINKEDIN], 1)
        self.assertGreater(self.runs[4].execution["peak_buffered"], 0)

    def test_a_repeated_search_waits_for_its_twin_as_the_serial_loop_would(self):
        """No dedup in build_search_plan: a keyword given twice is one combo
        twice. Serially the second is skipped once the first is done; run
        concurrently it would be bought twice. It waits for its twin."""
        twice = (KW[0], KW[0], KW[1])
        scripts = [Script(solo(0), polls=2, delay={"get": 0.05}), None,
                   Script(solo(1))]
        for workers in (SERIAL, 2, 4):
            got = c2_sweep(scripts, keywords=twice, workers=workers)
            self.assertEqual(len(got.client.kinds("start")), 2, workers)
            self.assertEqual(got.status(), ["completed", "skipped_done", "completed"])
        failed = [Script(solo(0), status="FAILED"), None, Script(solo(1))]
        for workers in (SERIAL, 4):          # a failed twin is retried, as serially
            got = c2_sweep(failed, keywords=twice, workers=workers)
            self.assertEqual(got.status(), ["failed", "failed", "completed"], workers)

    def test_an_absurd_worker_count_is_still_four_at_the_provider(self):
        scripts = staggered([solo(u) for u in range(6)])
        got = c2_sweep(scripts, workers=999, keywords=KW[:6])
        self.assertEqual(got.execution["workers"], 4)
        self.assertLessEqual(got.client.peak["*"], 4)
        self.assertEqual(len(got.client.kinds("start")), 6)


# ===========================================================================
# The duplicate tie — the critical concurrency test
# ===========================================================================
class DuplicateTie(unittest.TestCase):

    def test_the_twins_really_tie_so_only_arrival_decides(self):
        a = scraper.normalize(twin("90"), "linkedin")
        b = scraper.normalize(twin("91"), "linkedin")
        self.assertEqual(scraper.job_key(a), scraper.job_key(b))
        self.assertEqual(scraper.score_job(a)["score"], scraper.score_job(b)["score"])
        self.assertNotEqual(a["Job URL"], b["Job URL"])

    def test_a_later_unit_finishing_first_does_not_take_the_tie(self):
        scripts = [Script([twin("90")] + solo(0), polls=3, delay={"get": 0.08}),
                   Script([twin("91")] + solo(1), polls=1)]
        serial = c2_sweep(scripts, keywords=KW[:2])
        for w in (2, 3, 4):
            got = c2_sweep(scripts, keywords=KW[:2], workers=w)
            self.assertEqual(got.client.order("dataset"), ["run_2", "run_1"], w)
            twins = [r for r in got.rows if r["company"] == "Twin Works"]
            self.assertEqual([r["apply_url"] for r in twins],
                             ["https://www.linkedin.com/jobs/view/90"], w)
            self.assertEqual(twins[0]["search_query"], label(0))
            self.assertEqual((got.csv, got.json), (serial.csv, serial.json), w)
            b = [u for u in got.telemetry["paid_units"] if u["unit_id"] == "paid_001"][0]
            self.assertEqual(b["trace"].split()[0][0], "D")     # lost the tie to A


# ===========================================================================
# Reservation: the full ceiling, before the start, never handed back
# ===========================================================================
def bounded(n, **kw):
    return [Script(solo(u), **kw) for u in range(n)]


class ReservationBudget(unittest.TestCase):
    """The brief's matrix, end to end through main(). Every budget case runs at
    one, two and four workers: at four every reservation is taken before any
    run finishes, at one each is taken after the previous run's readings are
    in — which is where released headroom would show."""

    WORKERS = (1, 2, 4)

    def starts(self, budget, n=3, usage=0.03005, account=(5.0,)):
        runs = {}
        for workers in self.WORKERS:
            got = c2_sweep(bounded(n, usage=usage), keywords=KW[:n], budget=budget,
                           workers=workers,
                           account=None if account is None else list(account))
            runs[workers] = (got, len(got.client.kinds("start")))
        counts = {n for _, n in runs.values()}
        self.assertEqual(len(counts), 1, {w: n for w, (_, n) in runs.items()})
        return runs[1][0], counts.pop()

    def test_1_budget_010_two_starts_the_third_blocked(self):
        got, n = self.starts(0.10)
        self.assertEqual(n, 2)
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0.092")
        self.assertEqual(got.execution["exposure"]["blocked"], 1)
        self.assertEqual(got.status(), ["completed", "completed", "skipped_budget"])

    def test_2_budget_0091_one_start(self):
        got, n = self.starts(0.091)
        self.assertEqual(n, 1)
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0.046")

    def test_3_budget_exactly_0092_two_starts(self):
        got, n = self.starts(0.092)
        self.assertEqual(n, 2)
        self.assertEqual(got.execution["exposure"]["view_usd"], "0.092")

    def test_4_two_workers_racing_for_the_last_reservation_only_one_wins(self):
        for _ in range(20):
            exposure = scraper.PaidExposure(0.092)
            self.assertTrue(exposure.reserve("paid_000", CEILING))
            gate, wins = threading.Barrier(8), []

            def race(i):
                gate.wait()
                if exposure.reserve(f"paid_{i + 1:03d}", CEILING):
                    wins.append(i)
            threads = [threading.Thread(target=race, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(wins), 1)
            self.assertEqual(exposure.committed + exposure.pending, Decimal("0.092"))

    def test_5_an_account_delta_of_zero_adds_no_capacity(self):
        got, n = self.starts(0.10, account=[5.0])            # never moves
        self.assertEqual(n, 2)
        self.assertEqual(got.execution["exposure"]["observed_usd"], "0")
        old = c2_sweep(bounded(3), keywords=KW[:3], budget=0.10, account=[5.0])
        self.assertEqual(len(old.client.kinds("start")), 3)      # the lag, before C2

    def test_6_a_terminal_run_reading_of_000205_adds_no_capacity(self):
        got, n = self.starts(0.10, usage=0.00205, account=None)   # run-record sum
        self.assertEqual(n, 2)
        self.assertEqual(got.execution["exposure"]["observed_usd"], "0.0041")
        old = c2_sweep(bounded(3, usage=0.00205), keywords=KW[:3], budget=0.10)
        self.assertEqual(len(old.client.kinds("start")), 3)      # the lag, before C2

    def test_7_a_settled_research_reading_adds_no_capacity(self):
        exposure = scraper.PaidExposure(0.10)
        for u in ("paid_000", "paid_001"):
            self.assertTrue(exposure.reserve(u, CEILING))
            exposure.commit(u)
        for settled in (0.03005, 0.0601, 0.0):
            exposure.observe(settled)
        self.assertEqual(exposure.view(), Decimal("0.092"))
        self.assertFalse(exposure.reserve("paid_002", CEILING))
        # And nothing a cost reading can say reaches the exposure at all.
        self.assertFalse({"settle", "refund", "credit", "reconcile"} & set(dir(exposure)))

    # 8-11: one worker, so the second search's reservation is decided AFTER the
    # first one's outcome is in — the moment a released hold would show.
    def test_8_a_run_that_failed_after_its_start_keeps_its_ceiling(self):
        got = c2_sweep([Script(solo(0), status="FAILED", usage=0.004), Script(solo(1))],
                       keywords=KW[:2], budget=0.09, workers=1, account=[5.0])
        self.assertEqual(len(got.client.kinds("start")), 1)
        self.assertEqual(got.status(), ["failed", "skipped_budget"])
        unit = got.execution["units"][0]
        self.assertEqual((unit["reservation"], unit["reserved_usd"]), ("committed", "0.046"))
        old = c2_sweep([Script(solo(0), status="FAILED"), Script(solo(1))],
                       keywords=KW[:2], budget=0.09, account=[5.0])
        self.assertEqual(len(old.client.kinds("start")), 2)

    def test_9_a_start_that_raised_keeps_its_ceiling_run_id_or_not(self):
        got = c2_sweep([Script(start_error=RuntimeError("connection reset by peer")),
                        Script(solo(1))], keywords=KW[:2], budget=0.09, workers=1,
                       account=[5.0])
        self.assertEqual(len(got.client.kinds("start")), 1)    # the request was made
        self.assertEqual(got.execution["units"][0]["reservation"], "committed")
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0.046")
        self.assertNotIn("actor_run_id", got.telemetry["units"][0])
        self.assertEqual(got.status(), ["failed", "skipped_budget"])

    def test_10_a_local_failure_before_any_request_releases_the_hold(self):
        got = c2_sweep(bounded(2), keywords=KW[:2], budget=0.05, workers=1,
                       account=[5.0], patches=[mock.patch.object(
                           scraper, "charge_ceiling_supported", lambda actor: False)])
        self.assertEqual(got.client.kinds("start"), [])
        self.assertEqual(got.status(), ["failed", "failed"])  # each fails closed, alone
        ex = got.execution
        self.assertEqual([u["reservation"] for u in ex["units"]],
                         ["released_before_network"] * 2)
        self.assertEqual(ex["exposure"]["committed_usd"], "0")
        self.assertEqual(ex["exposure"]["pending_usd"], "0.000")

    def test_11_an_engine_deadline_abort_keeps_its_ceiling(self):
        clock = itertools.count(0, 61)          # every reading a minute later
        lock = threading.Lock()

        def monotonic():
            with lock:
                return float(next(clock))
        got = c2_sweep([Script(solo(0), polls=10 ** 6), Script(solo(1))],
                       keywords=KW[:2], budget=0.09, workers=1, account=[5.0],
                       patches=[mock.patch.object(scraper.time, "monotonic", monotonic)])
        self.assertIn(("abort", "run_1"), got.client.calls)
        self.assertEqual(got.status(), ["failed", "skipped_budget"])
        self.assertEqual(got.execution["units"][0]["reservation"], "committed")

    def test_11b_a_provider_timeout_keeps_its_ceiling(self):
        got = c2_sweep([Script(solo(0), status="TIMED-OUT"), Script(solo(1))],
                       keywords=KW[:2], budget=0.09, workers=1, account=[5.0])
        self.assertEqual(got.status(), ["failed", "skipped_budget"])
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0.046")

    def test_12_a_skipped_done_unit_reserves_nothing(self):
        got = c2_sweep(bounded(3), keywords=KW[:3], budget=0.10, workers=4,
                       account=[5.0], done=(f"linkedin|{KW[0]}|Bengaluru",))
        self.assertEqual(got.status(), ["skipped_done", "completed", "completed"])
        self.assertEqual(got.execution["units"][0]["reservation"], "none")
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0.092")
        self.assertEqual(len(got.client.kinds("start")), 2)

    def test_13_a_blocked_unit_holds_nothing_and_sends_nothing(self):
        got, _ = self.starts(0.10)
        third = scraper.build_input("linkedin", scraper.effective_search(
            "linkedin", scraper.build_search_plan([KW[2]], ["Bengaluru"])[0]))
        self.assertNotIn(third, [c[2] for c in got.client.kinds("start")])
        self.assertNotIn("run_3", {c[1] for c in got.client.calls if len(c) > 1})
        unit = got.execution["units"][-1]
        self.assertEqual((unit["unit_id"], unit["reservation"]), ("paid_002", "blocked"))
        self.assertNotIn("committed_at", unit)

    def test_the_reserved_ceiling_is_the_one_the_start_carries(self):
        got = c2_sweep(bounded(2), keywords=KW[:2], workers=2)
        self.assertEqual([c[3] for c in got.client.kinds("start")], [CEILING, CEILING])
        self.assertEqual([u["reserved_usd"] for u in got.execution["units"]],
                         ["0.046", "0.046"])

    def test_a_reservation_exists_before_its_worker_starts(self):
        """Mutation D's test: at the moment a worker thread begins, its unit
        already holds a reservation — made by the coordinator, not by it."""
        seen, real = [], scraper._paid_worker

        def spy(entry, make_client, exposure, results):
            seen.append((entry.unit_id, (exposure.units.get(entry.unit_id) or {})
                         .get("state")))
            return real(entry, make_client, exposure, results)
        c2_sweep(bounded(4), keywords=KW[:4], budget=0.10, workers=4, account=[5.0],
                 patches=[mock.patch.object(scraper, "_paid_worker", spy)])
        self.assertEqual(seen, [("paid_000", "pending"), ("paid_001", "pending")])

    def test_no_budget_holds_but_never_blocks(self):
        got = c2_sweep(bounded(5), keywords=KW[:5], workers=4)
        self.assertEqual(len(got.client.kinds("start")), 5)
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0.230")
        self.assertIsNone(got.execution["exposure"]["budget_usd"])


class ExposureLedger(unittest.TestCase):
    """PaidExposure on its own: the arithmetic the scheduler relies on."""

    def test_pending_then_committed_and_never_back(self):
        e = scraper.PaidExposure(1)
        self.assertTrue(e.reserve("a", CEILING))
        self.assertEqual((e.pending, e.committed), (CEILING, 0))
        e.commit("a")
        self.assertEqual((e.pending, e.committed), (0, CEILING))
        e.release("a")                           # too late: the request went out
        self.assertEqual(e.committed, CEILING)
        self.assertEqual(e.units["a"]["state"], "committed")

    def test_release_only_before_the_start(self):
        e = scraper.PaidExposure(1)
        e.reserve("a", CEILING)
        e.release("a")
        self.assertEqual((e.pending, e.committed), (0, 0))
        e.commit("a")                            # a released hold cannot come back
        self.assertEqual(e.committed, 0)

    def test_observed_readings_only_ever_add(self):
        e = scraper.PaidExposure(1)
        e.observe(0.5)
        e.observe(0.1)                           # a month rollover, a lagging read
        self.assertEqual(e.view(), Decimal("0.5"))
        e.observe(0.2, unbounded_delta=-0.3)     # a negative delta hands nothing back
        self.assertEqual(e.unbounded, 0)

    def test_the_view_is_never_below_the_old_guard(self):
        e = scraper.PaidExposure(1)
        e.reserve("a", CEILING)
        e.commit("a")
        e.observe(0.07)                          # e.g. someone else spent meanwhile
        self.assertEqual(e.view(), Decimal("0.07"))
        self.assertFalse(e.reserve("b", Decimal("0.931")))

    def test_an_unbounded_start_is_admitted_on_the_old_test(self):
        e = scraper.PaidExposure(Decimal("0.092"))
        e.reserve("a", CEILING)
        e.commit("a")
        self.assertTrue(e.admit("b"))                     # 0.046 < 0.092
        e.observe(0.03, unbounded_delta=0.046)
        self.assertFalse(e.admit("c"))                    # 0.092 >= 0.092
        self.assertEqual(e.units["b"], {"state": "unbounded", "usd": None})


# ===========================================================================
# Bounded vs unbounded providers
# ===========================================================================
def mixed(n_li, n_in, usage_in=0.02):
    li_scripts = [Script(solo(u), polls=2, delay={"get": 0.05}) for u in range(n_li)]
    in_scripts = [Script([indeed_item(f"{u}", "Full Stack Engineer", f"Indy {u}")],
                         usage=usage_in, polls=2, delay={"get": 0.02})
                  for u in range(n_in)]
    return li_scripts, in_scripts


def unbounded_indeed():
    """Indeed without its V2-C3.5 charge model: no ceiling anywhere, as before."""
    return mock.patch.object(scraper, "ACTOR_CHARGE_MODEL", {
        k: v for k, v in scraper.ACTOR_CHARGE_MODEL.items() if k != "indeed"})


BOTH = ("linkedin", "indeed")
# Two LinkedIn searches and three Indeed ones for one keyword.
PLACES = {"linkedin": ["Bengaluru", "Hyderabad"],
          "indeed": ["Bengaluru", "Hyderabad", "Pune"]}


class MixedProviders(unittest.TestCase):
    """What C2 does with a provider that has NO charge ceiling. Indeed played
    that part until V2-C3.5 bounded it, and plays it here with its model
    removed (unbounded_indeed); Naukri is the real one today
    (test_indeed_bounded.Classification)."""

    def test_indeed_never_enters_the_pool(self):
        li_s, in_s = mixed(3, 3)
        got = c2_sweep(li_s + in_s, keywords=KW[:3], sites=BOTH, workers=4,
                       patches=[unbounded_indeed()])
        self.assertEqual(got.client.peak[INDEED], 1)
        self.assertEqual(got.client.peak[LINKEDIN], 3)
        segments = {s["provider"]: s for s in got.execution["segments"]}
        self.assertEqual((segments["linkedin"]["bounded"], segments["linkedin"]["workers"]),
                         (True, 4))
        self.assertEqual((segments["indeed"]["bounded"], segments["indeed"]["workers"]),
                         (False, 1))
        indeed = [u for u in got.execution["units"] if u["provider"] == "indeed"]
        self.assertEqual({(u["reservation"], u["reserved_usd"]) for u in indeed},
                         {("unbounded", None)})
        self.assertEqual({u["charge_ceiling_usd"] for u in got.telemetry["paid_units"]
                          if u["provider"] == "indeed"}, {None})
        # Indeed's starts carry no ceiling, and no ceiling was invented for them.
        self.assertEqual({c[3] for c in got.client.kinds("start") if c[1] == INDEED},
                         {None})

    def test_indeed_runs_exactly_as_serial(self):
        """From the first Indeed start on, the request log is the serial one:
        start, polls, dataset, account read — one search at a time."""
        li_s, in_s = mixed(2, 3)
        account = [0.0, 0.03, 0.06, 0.08, 0.1, 0.12]
        serial = c2_sweep(li_s + in_s, keywords=KW[:1], site_locations=PLACES,
                          sites=BOTH, account=account,
                          patches=[unbounded_indeed()])
        got = c2_sweep(li_s + in_s, keywords=KW[:1], site_locations=PLACES,
                       sites=BOTH, workers=4, account=account,
                       patches=[unbounded_indeed()])

        def tail(calls):
            first = next(i for i, c in enumerate(calls)
                         if c[0] == "start" and c[1] == INDEED)
            return calls[first:]
        self.assertEqual(len(tail(got.client.calls)), 3 * (1 + 2 + 1 + 1))
        self.assertEqual(tail(got.client.calls), tail(serial.client.calls))
        self.assertEqual((got.csv, got.json), (serial.csv, serial.json))

    def test_linkedin_ceilings_stay_visible_to_indeed(self):
        """$0.10 budget. Two LinkedIn starts hold $0.092; the account shows
        $0.06 for them. Indeed #1 is admitted ($0.092 < $0.10) and moves the
        account $0.02; Indeed #2 then sees $0.112 held and observed and is
        refused. The old guard saw $0.08 and let Indeed #2 run."""
        li_s, in_s = mixed(2, 3)
        account = [0.0, 0.03, 0.06, 0.08, 0.10, 0.12]
        new = c2_sweep(li_s + in_s, keywords=KW[:1], site_locations=PLACES, sites=BOTH,
                       workers=4, budget=0.10, account=account,
                       patches=[unbounded_indeed()])
        old = c2_sweep(li_s + in_s, keywords=KW[:1], site_locations=PLACES, sites=BOTH,
                       budget=0.10, account=account, patches=[unbounded_indeed()])
        by_actor = lambda got: Counter(c[1] for c in got.client.kinds("start"))
        self.assertEqual(by_actor(new), {LINKEDIN: 2, INDEED: 1})
        self.assertEqual(by_actor(old), {LINKEDIN: 2, INDEED: 2})
        ex = new.execution["exposure"]
        self.assertEqual((ex["committed_usd"], ex["unbounded_observed_usd"]),
                         ("0.092", "0.02"))
        self.assertIn("spend cap $0.10 reached ($0.112 held and observed)", new.log)

    def test_a_bounded_site_after_an_unbounded_one_counts_its_spend(self):
        """A profile can order Indeed first. Its observed $0.07 counts against
        LinkedIn's first ceiling: $0.07 + $0.046 > $0.10, so LinkedIn never
        starts; the old guard saw $0.07 < $0.10 and started both."""
        li_s, in_s = mixed(2, 2, usage_in=0.035)
        order = ("indeed", "linkedin", "naukri")
        account = [0.0, 0.035, 0.07, 0.07, 0.07]
        got = c2_sweep(in_s + li_s, keywords=KW[:2], sites=BOTH, site_order=order,
                       workers=4, budget=0.10, account=account,
                       patches=[unbounded_indeed()])
        self.assertEqual(Counter(c[1] for c in got.client.kinds("start")), {INDEED: 2})
        self.assertEqual(got.execution["units"][-1]["reservation"], "blocked")
        self.assertEqual(Decimal(got.execution["exposure"]["unbounded_observed_usd"]),
                         Decimal("0.07"))
        old = c2_sweep(in_s + li_s, keywords=KW[:2], sites=BOTH, site_order=order,
                       budget=0.10, account=account, patches=[unbounded_indeed()])
        self.assertEqual(Counter(c[1] for c in old.client.kinds("start")),
                         {INDEED: 2, LINKEDIN: 2})

    def test_a_funded_mixed_plan_is_byte_identical(self):
        li_s, in_s = mixed(4, 4)
        serial = c2_sweep(li_s + in_s, keywords=KW[:4], sites=BOTH, free=True)
        for w in (2, 4):
            got = c2_sweep(li_s + in_s, keywords=KW[:4], sites=BOTH, free=True,
                           workers=w)
            self.assertEqual((got.csv, got.json, got.seen, got.done),
                             (serial.csv, serial.json, serial.seen, serial.done), w)


# ===========================================================================
# One account per sweep, chosen once
# ===========================================================================
TOKEN_A = "apify_api_C2ACCOUNTA000000000000000"   # not real tokens
TOKEN_B = "apify_api_C2ACCOUNTB000000000000000"


class AccountSelection(unittest.TestCase):
    """_require_token picks the account with the most headroom ONCE, before any
    client exists. C2 keeps that: the coordinator chooses, every worker is
    handed the chosen token, and no worker ever chooses — so there is no stale
    headroom for two of them to share."""

    def sweep(self, env, headroom, workers=4, n=6):
        reads, chose, real = [], [], scraper._require_token

        def token_headroom(token):
            reads.append(token)
            return headroom[token]

        def choose():
            chose.append(1)
            return real()
        # Every APIFY_TOKEN* this shell may hold is removed, and .env is never
        # read, so the only accounts are the fixtures below.
        clean = {name: None for name in os.environ if name.startswith("APIFY_TOKEN")}
        got = c2_sweep(staggered([solo(u) for u in range(n)]), keywords=KW[:n],
                       workers=workers, token_patch=False, env=dict(clean, **env),
                       patches=[mock.patch("dotenv.load_dotenv", lambda *a, **k: None),
                                mock.patch.object(scraper, "_token_headroom",
                                                  token_headroom),
                                mock.patch.object(scraper, "_require_token", choose)])
        return got, reads, chose

    def test_two_accounts_the_one_with_headroom_chosen_once_for_every_worker(self):
        got, reads, chose = self.sweep(
            {"APIFY_TOKEN": TOKEN_A, "APIFY_TOKEN_2": TOKEN_B},
            {TOKEN_A: (4.95, 5.0), TOKEN_B: (1.0, 5.0)})
        self.assertEqual(chose, [1])
        self.assertEqual(sorted(reads), sorted([TOKEN_A, TOKEN_B]))   # once each
        self.assertEqual(set(got.client.tokens), {TOKEN_B})
        self.assertEqual(len(got.client.tokens), 1 + 6)    # coordinator + each search
        self.assertEqual(len(got.client.kinds("start")), 6)

    def test_the_first_account_when_it_has_more_headroom(self):
        got, _, _ = self.sweep({"APIFY_TOKEN": TOKEN_A, "APIFY_TOKEN_2": TOKEN_B},
                               {TOKEN_A: (0.5, 5.0), TOKEN_B: (4.0, 5.0)})
        self.assertEqual(set(got.client.tokens), {TOKEN_A})

    def test_one_byok_token_reads_no_headroom_and_is_used_throughout(self):
        """The public worker's path: the visitor's key alone."""
        got, reads, chose = self.sweep({"APIFY_TOKEN": TOKEN_A}, {})
        self.assertEqual((chose, reads), ([1], []))
        self.assertEqual(set(got.client.tokens), {TOKEN_A})

    def test_no_token_value_reaches_any_output(self):
        got, _, _ = self.sweep({"APIFY_TOKEN": TOKEN_A, "APIFY_TOKEN_2": TOKEN_B},
                               {TOKEN_A: (4.95, 5.0), TOKEN_B: (1.0, 5.0)})
        for body in (got.csv, got.json, got.seen, got.telemetry_text.encode(),
                     got.log.encode()):
            self.assertNotIn(TOKEN_A.encode(), body)
            self.assertNotIn(TOKEN_B.encode(), body)


# ===========================================================================
# Checkpoints, the done ledger, crashes and resume
# ===========================================================================
class CrashAt:
    """Raise Crash at one boundary of one unit's integration (0-based plan
    position among EXECUTED units), everything else real."""

    def __init__(self, where, unit):
        self.where, self.unit = where, unit
        self.reads = self.writes = 0

    def patches(self):
        real_read, real_write = scraper.account_usage_usd, scraper.write_outputs

        def read(client):
            n, self.reads = self.reads, self.reads + 1
            if self.where == "before_integration" and n == self.unit + 1:
                raise Crash("before the unit was integrated")
            return real_read(client)

        def write(out_rows, csv_path, json_path):
            n, self.writes = self.writes, self.writes + 1
            if n == self.unit and self.where == "before_checkpoint":
                raise Crash("rows in memory, checkpoint not begun")
            if n == self.unit and self.where == "during_checkpoint":
                def torn(src, dst):
                    raise Crash("halfway through the checkpoint")
                with mock.patch.object(scraper.os, "replace", torn):
                    return real_write(out_rows, csv_path, json_path)
            result = real_write(out_rows, csv_path, json_path)
            if n == self.unit and self.where == "after_checkpoint":
                raise Crash("checkpoint durable, done marker not written")
            return result
        return [mock.patch.object(scraper, "account_usage_usd", read),
                mock.patch.object(scraper, "write_outputs", write)]


class Checkpoints(unittest.TestCase):
    """The transaction the serial loop defines, per search: rows integrated →
    checkpoint written whole (V2-B5's rename) → done marker appended. A crash
    at any boundary must leave: no unit marked done whose rows are not in the
    checkpoint on disk; the checkpoint whole; no temp file."""

    UNITS = 4

    def crash(self, where, unit, workers=2):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        crash = CrashAt(where, unit)
        got = c2_sweep(staggered([solo(u) for u in range(self.UNITS)], slow_first=False),
                       keywords=KW[:self.UNITS], workers=workers, out=out,
                       account=[1.0, 1.03, 1.06, 1.09, 1.12], patches=crash.patches())
        self.assertIsInstance(got.error, Crash, where)
        return got

    def assertDurable(self, got):
        on_disk = {r["search_query"] for r in got.rows} if got.csv else set()
        for line in got.done:
            keyword = line.split("|")[2]
            self.assertIn(f"{keyword} @ Bengaluru", on_disk,
                          f"{keyword} is marked done but its rows are not on disk")
        self.assertEqual([f for f in got.files if f.endswith(".tmp")], [])
        if got.json:
            json.loads(got.json)

    def done_units(self, got):
        return [line.split("|")[2] for line in got.done]

    def test_1_crash_before_a_unit_is_integrated(self):
        got = self.crash("before_integration", 2)
        self.assertEqual(self.done_units(got), list(KW[:2]))
        self.assertDurable(got)
        self.assertNotIn(label(2), {r["search_query"] for r in got.rows})

    def test_2_crash_with_rows_in_memory_before_the_checkpoint(self):
        got = self.crash("before_checkpoint", 2)
        self.assertEqual(self.done_units(got), list(KW[:2]))
        self.assertDurable(got)

    def test_3_crash_during_the_checkpoint_write(self):
        got = self.crash("during_checkpoint", 2)
        self.assertEqual(self.done_units(got), list(KW[:2]))
        self.assertDurable(got)
        # The previous checkpoint, whole: units 0 and 1, not 2.
        self.assertEqual({r["search_query"] for r in got.rows}, {label(0), label(1)})

    def test_4_crash_after_the_checkpoint_before_the_marker(self):
        got = self.crash("after_checkpoint", 2)
        self.assertEqual(self.done_units(got), list(KW[:2]))
        self.assertDurable(got)
        self.assertIn(label(2), {r["search_query"] for r in got.rows})   # re-bought on resume

    def test_5_crash_after_the_marker(self):
        got = self.crash("before_integration", 3)
        self.assertEqual(self.done_units(got), list(KW[:3]))
        self.assertDurable(got)

    def test_6_7_a_finished_later_unit_waits_and_is_lost_only_undone(self):
        """Unit 0 slow, units 1-3 fast: they finish first and wait in memory.
        A crash before unit 0 is integrated leaves nothing marked done and no
        checkpoint — the buffered rows are lost, and so is any claim they
        were saved: a rerun buys them again rather than skipping them."""
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        crash = CrashAt("before_integration", 0)
        got = c2_sweep(staggered([solo(u) for u in range(4)]), keywords=KW[:4],
                       workers=4, out=out, account=[1.0, 1.03], patches=crash.patches())
        self.assertIsInstance(got.error, Crash)
        self.assertEqual(got.client.order("dataset")[:3], ["run_4", "run_3", "run_2"])
        self.assertEqual(got.done, [])
        self.assertIsNone(got.csv)

    def test_8_9_resume_skips_what_was_safe_and_buys_only_the_rest(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        crash = CrashAt("after_checkpoint", 2)
        first = c2_sweep(staggered([solo(u) for u in range(4)], slow_first=False),
                         keywords=KW[:4], workers=2, out=out,
                         account=[1.0, 1.03, 1.06, 1.09], patches=crash.patches())
        self.assertIsInstance(first.error, Crash)
        # A rerun a minute later writes a new stamp; renamed here so this test
        # does not depend on the clock (a same-minute rerun overwrites — a
        # pre-existing property of the minute-granular stamp, serial too).
        for path in glob.glob(os.path.join(out, "jobs_*")):
            os.replace(path, path.replace("jobs_", "jobs_run1_"))
        second = c2_sweep(staggered([solo(u) for u in range(4)], slow_first=False),
                          keywords=KW[:4], workers=2, out=out, account=[2.0, 2.03, 2.06])
        self.assertIsNone(second.error)
        started = [c[2]["urls"][0] for c in second.client.kinds("start")]
        self.assertEqual(len(started), 2)                     # units 2 and 3 only
        self.assertTrue(all("K2" in u or "K3" in u for u in started), started)
        self.assertEqual(second.status(), ["skipped_done", "skipped_done",
                                           "completed", "completed"])
        run1 = next(v for k, v in second.csvs.items() if k.startswith("jobs_run1_"))
        run1_labels = {r["search_query"] for r in csv.DictReader(
            io.StringIO(run1.decode("utf-8-sig")))}
        self.assertEqual(run1_labels, {label(0), label(1), label(2)})
        self.assertEqual({r["search_query"] for r in second.rows}, {label(2), label(3)})
        self.assertEqual(sorted(second.done), sorted(
            f"{TODAY}|linkedin|{KW[u]}|Bengaluru|" for u in range(4)))

    def test_10_no_done_unit_ever_lacks_its_rows_at_any_boundary(self):
        for where in ("before_integration", "before_checkpoint", "during_checkpoint",
                      "after_checkpoint"):
            for unit in range(self.UNITS):
                for workers in (1, 4):
                    with self.subTest(where=where, unit=unit, workers=workers):
                        self.assertDurable(self.crash(where, unit, workers))

    def test_12_readiness_after_the_final_result_under_c2(self):
        events, published = [], []

        def spy(name, real):
            def wrapped(*a, **kw):
                events.append(name)
                if name == "publish_results_ready":
                    published.append(time.monotonic())
                return real(*a, **kw)
            return mock.patch.object(scraper, name, wrapped)
        patches = [spy(n, getattr(scraper, n)) for n in
                   ("write_outputs", "record_seen", "publish_results_ready")]
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        on = c2_sweep(staggered([solo(u) for u in range(3)]), keywords=KW[:3],
                      workers=3, free=True, env={scraper.READY_FLAG: "1"},
                      patches=patches, out=out)
        off = c2_sweep(staggered([solo(u) for u in range(3)]), keywords=KW[:3],
                       workers=3, free=True)
        self.assertEqual((on.csv, on.json, on.seen), (off.csv, off.json, off.seen))
        at = events.index("publish_results_ready")
        self.assertEqual(events[at - 1], "record_seen")
        self.assertNotIn("write_outputs", events[at:])
        self.assertEqual(events.count("write_outputs"), 3 + 2)    # 3 paid + free + final
        self.assertEqual([e for e in on.client.events if e[0] > published[0]], [])
        self.assertIn(scraper.READY_MARKER, os.listdir(out))
        self.assertEqual([t for t in threading.enumerate()
                          if t.name.startswith("sweep-paid-")], [])


# ===========================================================================
# Score once: the checkpoint's CPU, without changing a byte
# ===========================================================================
def varied_rows():
    """Every row shape the engine scores: the C1 attribution items, stale,
    unreachable, hard-dropped and free rows, and the overhead benchmark's."""
    from bench import search_v2_paid_overhead as overhead
    rows = [scraper.normalize(i, "linkedin") for i in c1.A_ITEMS + c1.B_ITEMS]
    rows += [scraper.normalize(indeed_item(f"x{i}", t, c), "indeed")
             for i, (t, c) in enumerate([("Staff Engineer", "A"), ("Salesforce Developer", "B"),
                                         ("Senior Full Stack Engineer", "C")])]
    for script in overhead.scripts(c1)[:6]:
        rows += [scraper.normalize(i, "linkedin") for i in script.items]
    return rows


class ScoreOnce(unittest.TestCase):

    def test_score_job_changes_nothing_the_second_time(self):
        """The one property the memo stands on: a row scored twice is the row
        scored once, and a dropped row is dropped untouched. If this fails,
        score-once is wrong and SCORED must go."""
        kept = dropped = 0
        for row in varied_rows():
            once = copy.deepcopy(row)
            first = scraper.score_job(once)
            twice = copy.deepcopy(row)
            scraper.score_job(twice)
            second = scraper.score_job(twice)
            if first is None:
                dropped += 1
                self.assertIsNone(second)
                self.assertEqual(twice, row)
            else:
                kept += 1
                self.assertIs(second, twice)
                self.assertEqual(twice, once)
        self.assertGreater(kept, 50)
        self.assertGreater(dropped, 0)

    def test_growing_passes_with_and_without_the_memo_agree(self):
        rows = varied_rows()
        plain, memo = [], []
        with b3._env(**b3.BASE_ENV), b3.telemetry_record():
            for k in range(1, len(rows) + 1, 7):
                out = scraper.finalize(copy.deepcopy(rows[:k]))
                plain.append((out, dict(scraper.LAST_STATS),
                              copy.deepcopy(telemetry.record()["stages"])))
        with b3._env(**b3.BASE_ENV), b3.telemetry_record():
            held = []
            for k in range(1, len(rows) + 1, 7):
                held += copy.deepcopy(rows[len(held):k])
                out = scraper.finalize(held, memo=True)
                memo.append((out, dict(scraper.LAST_STATS),
                             copy.deepcopy(telemetry.record()["stages"])))
        self.assertEqual(plain, memo)

    def test_each_row_is_scored_once_under_c2_and_every_pass_before(self):
        calls, real = [], scraper.score_job

        def count(row):
            calls.append(id(row))
            return real(row)
        patch = mock.patch.object(scraper, "score_job", count)
        serial = c2_sweep(bounded(3), keywords=KW[:3], free=True, patches=[patch])
        n_serial = len(calls)
        calls.clear()
        memo = c2_sweep(bounded(3), keywords=KW[:3], free=True, workers=2,
                        patches=[mock.patch.object(scraper, "score_job", count)])
        self.assertEqual((serial.csv, serial.json), (memo.csv, memo.json))
        self.assertEqual(len(calls), len(set(calls)))        # one call per row
        self.assertEqual(len(calls), 3 * 2 + 1)              # 6 paid rows + 1 free
        self.assertEqual(n_serial, 2 + 4 + 6 + 7 + 7)        # every pass re-scored

    def test_off_while_the_experience_guard_records_per_call(self):
        calls, real = [], scraper.score_job

        def count(row):
            calls.append(id(row))
            return real(row)
        c2_sweep(bounded(2), keywords=KW[:2], workers=2,
                 env={experience_guard.FLAG: "1"},
                 patches=[mock.patch.object(scraper, "score_job", count)])
        self.assertGreater(len(calls), len(set(calls)))

    def test_the_memo_key_reaches_no_output(self):
        got = c2_sweep(bounded(2), keywords=KW[:2], workers=2, free=True)
        for body in (got.csv, got.json, got.seen):
            self.assertNotIn(scraper.SCORED.encode(), body)
        self.assertEqual(set(scraper.to_output({"Title": "x", scraper.SCORED: True})),
                         set(scraper.OUTPUT_COLUMNS))

    def test_the_shadow_evaluation_is_the_serial_one(self):
        serial = c2_sweep(bounded(2), keywords=KW[:2], free=True, shadow_on=True)
        got = c2_sweep(bounded(2), keywords=KW[:2], free=True, shadow_on=True, workers=2)
        self.assertEqual(serial.telemetry["shadow_evaluation"]["status"], "evaluated")

        def counts(record):
            section = copy.deepcopy(record["shadow_evaluation"])
            section.pop("cost", None)
            return section
        self.assertEqual(counts(serial.telemetry), counts(got.telemetry))
        self.assertEqual((serial.csv, serial.json), (got.csv, got.json))


# ===========================================================================
# Telemetry under concurrency
# ===========================================================================
class Telemetry(unittest.TestCase):

    def test_each_unit_records_its_own_run_under_interleaving(self):
        scripts = [Script(solo(u), polls=u + 2, usage=0.001 * (u + 1),
                          delay={"get": 0.015 * ((u % 2) + 1)}) for u in range(4)]
        got = c2_sweep(scripts, keywords=KW[:4], workers=4)
        units = [u for u in got.telemetry["units"] if u["path"] == "paid"]
        for i, unit in enumerate(units):
            self.assertEqual(unit["unit_id"], f"paid_00{i}")
            self.assertEqual(unit["actor_run_id"], f"run_{i + 1}")
            self.assertEqual(unit["poll_count"], i + 2)
            self.assertEqual(unit["query"], KW[i])
            self.assertEqual(unit["cost_observations"][0]["usd"], 0.001 * (i + 1))
            self.assertEqual(unit["raw_count"], 2)
        self.assertIsNone(telemetry._current())

    def test_workers_never_attach_to_the_shared_record(self):
        seen, real = [], scraper.scrape_search

        def spy(client, site_key, actor_id, search, before_start=None):
            seen.append([u.get("unit_id") for u in telemetry.record()["units"]])
            seen.append(threading.current_thread().name)
            return real(client, site_key, actor_id, search, before_start=before_start)
        c2_sweep(staggered([solo(u) for u in range(3)]), keywords=KW[:3], workers=3,
                 patches=[mock.patch.object(scraper, "scrape_search", spy)])
        self.assertEqual(seen[0::2], [[], [], []])
        self.assertTrue(all(n.startswith("sweep-paid-paid_") for n in seen[1::2]))

    def test_the_section_is_bounded_and_known(self):
        got = c2_sweep(staggered([solo(u) for u in range(3)]), keywords=KW[:3],
                       workers=2, budget=0.10, account=[5.0])
        ex = got.execution
        self.assertEqual(ex["schema"], "search-v2c2.1")
        self.assertEqual(set(ex), {"schema", "mode", "workers", "exposure",
                                   "peak_in_flight", "peak_buffered", "buffered_wait_ms",
                                   "checkpoint", "segments", "units"})
        self.assertEqual(set(ex["exposure"]), {
            "budget_usd", "committed_usd", "pending_usd", "pending_peak_usd",
            "unbounded_observed_usd", "observed_usd", "view_usd", "committed_starts",
            "released_before_network", "blocked"})
        states = {"none", "committed", "released_before_network", "blocked", "unbounded"}
        for unit in ex["units"]:
            self.assertIn(unit["reservation"], states)
            self.assertLessEqual(set(unit), {
                "unit_id", "provider", "bounded", "reservation", "reserved_usd",
                "reserved_at", "committed_at", "released_at", "execution_ms",
                "buffered_wait_ms", "merge_ms", "checkpoint_cpu_ms", "sdk"})
        self.assertEqual(ex["checkpoint"]["passes"], 2)

    def test_reservations_are_never_called_cost(self):
        got = c2_sweep(bounded(2), keywords=KW[:2], workers=2, account=[1.0, 1.03, 1.06])
        for unit in got.telemetry["units"]:
            for obs in unit.get("cost_observations", []):
                self.assertIn(obs["source"], telemetry.COST_SOURCES)
                self.assertNotEqual(obs["usd"], 0.046)
        self.assertEqual(set(telemetry.COST_SOURCES), {
            "run_record_at_completion", "account_usage_delta", "run_record_settled"})
        section = json.dumps(got.execution)
        for word in ("spent", "actual", "cost_usd"):
            self.assertNotIn(word, section)
        self.assertNotIn("paid_execution", json.dumps(got.telemetry["paid_summary"]))

    def test_no_row_text_url_query_or_token(self):
        items = [li("MARKERURL1", "Full Stack Engineer", "MARKERCO", RICH + " MARKERJD")]
        got = c2_sweep([Script(items), Script(solo(1))], keywords=("QUERYMARKER Engineer",
                                                                   KW[1]), workers=2)
        section = json.dumps(got.execution)
        for marker in ("MARKERURL", "MARKERCO", "MARKERJD", "QUERYMARKER", TOKEN,
                       "linkedin.com", "Bengaluru"):
            self.assertNotIn(marker, section)
        self.assertNotIn(TOKEN, got.telemetry_text)

    def test_a_unit_clock_covers_start_to_marker_and_says_how_long_it_waited(self):
        got = c2_sweep(staggered([solo(u) for u in range(3)]), keywords=KW[:3], workers=3)
        units = {u["unit_id"]: u for u in got.execution["units"]}
        records = {u["unit_id"]: u for u in got.telemetry["units"] if u["path"] == "paid"}
        self.assertGreater(units["paid_002"]["buffered_wait_ms"], 30)  # finished first
        for unit_id, u in units.items():
            self.assertGreaterEqual(records[unit_id]["duration_ms"] + 2,
                                    u["execution_ms"] + u["buffered_wait_ms"])
        self.assertEqual(got.execution["buffered_wait_ms"]["max"],
                         max(u["buffered_wait_ms"] for u in units.values()))

    def test_the_sdks_own_counters_are_read_never_trusted(self):
        from collections import defaultdict
        stats = SimpleNamespace(calls=5, requests=7,
                                rate_limit_errors=defaultdict(int, {0: 1, 1: 1}))
        self.assertEqual(scraper._sdk_counts(SimpleNamespace(_statistics=stats)),
                         {"calls": 5, "requests": 7, "rate_limit_errors": 2})

        class Refuses:
            def __getattr__(self, name):
                raise AssertionError("the client was used")
        for client in (SimpleNamespace(), Refuses(), None):
            self.assertIsNone(scraper._sdk_counts(client))
        # The installed SDK still keeps them where this reads them — checked
        # in its source, so no real client is ever built here.
        import inspect
        import apify_client
        from apify_client._statistics import ClientStatistics
        self.assertIn("self._statistics = ClientStatistics()",
                      inspect.getsource(apify_client.ApifyClient.__init__))
        self.assertTrue({"calls", "requests", "rate_limit_errors"}
                        <= set(ClientStatistics.__dataclass_fields__))

    def test_telemetry_off_still_reserves_and_writes_no_record(self):
        got = c2_sweep(bounded(3), keywords=KW[:3], budget=0.10, workers=4, on=False,
                       account=[5.0])
        self.assertIsNone(got.telemetry)
        self.assertEqual(len(got.client.kinds("start")), 2)


# ===========================================================================
# Isolation: Free, B2/B4, B3, C0, the ledger
# ===========================================================================
class CanaryProbe(unittest.TestCase):
    """The one paid-capable tool, extended for C2's canary, still behind C0."""

    def args(self, **over):
        return SimpleNamespace(**{**dict(
            site="linkedin", searches=2, keywords="Backend Developer",
            location="India", case=None, scope="india", allow_paid=False,
            max_usd="0.28", exposed_usd="0.184", ledger=str(probe.LEDGER),
            paid_workers=None, sweep_budget=None), **over})

    def test_the_mode_is_the_probes_to_set_not_the_shells(self):
        shell = {scraper.PAID_CONCURRENCY_FLAG: "1", scraper.PAID_WORKERS_ENV: "4",
                 paid_guard.FLAG: None}
        with b3._env(**shell):
            off = probe.child_env(self.args(), "p")
            on = probe.child_env(self.args(paid_workers=2), "p")
        for key in (scraper.PAID_CONCURRENCY_FLAG, scraper.PAID_WORKERS_ENV):
            self.assertNotIn(key, off)
        self.assertEqual((on[scraper.PAID_CONCURRENCY_FLAG], on[scraper.PAID_WORKERS_ENV]),
                         ("1", "2"))
        for env in (off, on):                      # never a spending key
            self.assertNotIn(paid_guard.FLAG, env)

    def test_the_new_options_add_no_key_and_no_limit(self):
        plain = probe.guard_argv(self.args(), "p")
        extended = probe.guard_argv(self.args(paid_workers=2, sweep_budget="0.10"), "p")
        self.assertEqual(plain, extended)
        self.assertNotIn("--allow-paid", extended)

    def test_the_sweep_budget_is_the_engines_cap_in_the_profile(self):
        with_cap = probe.profile_source(self.args(sweep_budget="0.1"), "p", Path("w"))
        self.assertIn("'max_spend_usd': 0.1, 'output_dir': 'w'", with_cap)
        self.assertNotIn("max_spend_usd", probe.profile_source(self.args(), "p", Path("w")))

    def test_overlap_reads_the_providers_clocks(self):
        units = [{"unit_id": "paid_000", "execution": {
                     "actor_started_at": "2026-09-24T10:00:00+00:00",
                     "actor_finished_at": "2026-09-24T10:00:30+00:00"}},
                 {"unit_id": "paid_001", "execution": {
                     "actor_started_at": "2026-09-24T10:00:02+00:00",
                     "actor_finished_at": "2026-09-24T10:00:40+00:00"}}]
        got = probe.overlap(units)
        self.assertEqual(got["pairs"], [{"units": ["paid_000", "paid_001"],
                                         "overlap_s": 28.0}])
        self.assertEqual(got["peak_concurrent_at_provider"], 2)
        serial = copy.deepcopy(units)
        serial[1]["execution"]["actor_started_at"] = "2026-09-24T10:00:31+00:00"
        self.assertEqual(probe.overlap(serial)["peak_concurrent_at_provider"], 1)


class Isolation(unittest.TestCase):

    def test_the_free_executors_answer_only_their_own_switches(self):
        with b3._env(**{scraper.PAID_CONCURRENCY_FLAG: "1",
                        scraper.PAID_WORKERS_ENV: "4",
                        concurrency.FLAG: None, concurrency.GREENHOUSE_FLAG: None}):
            self.assertFalse(concurrency.applies("lever"))
            self.assertFalse(concurrency.applies("greenhouse"))
        for name in ("lever", "greenhouse"):
            self.assertNotIn("PAID", "".join(concurrency.ENV[name]))

    def test_production_c2_code_reads_no_ledger_and_no_developer_key(self):
        text = (ROOT / "scraper.py").read_text(encoding="utf-8")
        for word in ("paid-research-ledger", "SWEEP_ALLOW_PAID_BENCH", "paid_guard"):
            self.assertNotIn(word, text)

    def test_the_benchmark_cannot_reach_a_real_client(self):
        source = (ROOT / "bench" / "search_v2_paid_concurrency.py").read_text()
        self.assertIn("test_paid_concurrency", source)
        for banned in ("apify_client import", "_require_token(", "engine_argv(",
                       "APIFY_TOKEN"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
