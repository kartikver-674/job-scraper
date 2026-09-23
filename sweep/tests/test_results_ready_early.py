"""Search Engine V2-B5: a result is the user's the moment it is final.

The worker marks a run done when its process exits, and a sweep's last
seconds — the shadow tranche, the telemetry record — cannot change the
result. SWEEP_RESULTS_READY_EARLY lets the engine say so: it renames a marker
into place after the CSV, the JSON and the seen ledger are final, the worker
reports `results_ready` beside a state that stays `running`, and Render reads
that as finished.

What these pin, in the brief's order: flag off is the lifecycle as it was;
the marker is published only after every output is final; no reader can see
half a file; the active slot, the queue and the janitor still wait for the
process; the screen moves on at readiness by its usual route, and waits for
DONE without it; results and exports are served at readiness; a failure after
readiness cannot take the result away and one before it cannot expose one;
ownership is unchanged; B3 and B4 carry on; nothing paid moved.

Most of this runs the REAL stack: the worker on a loopback socket, the public
Render app pointed at it, and the engine itself — scraper.main(), offline, on
fixtures — as the worker's child, on a thread. Timing points are gates the
engine waits at, not sleeps. Every socket but loopback is denied for the
module, so nothing here can reach Apify or any job board.
"""
import contextlib
import copy
import csv
import glob
import html
import inspect
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for _path in (REPO, os.path.join(REPO, "auto-apply")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import scraper  # noqa: E402
import telemetry  # noqa: E402
from deploy import sweep_worker  # noqa: E402
from deploy.test_sweep_worker import TOKEN as BEARER  # noqa: E402
from deploy.test_sweep_worker import Harness  # noqa: E402
from sources import ats, concurrency, shadow  # noqa: E402
from sweep import logic, worker_client, worker_link  # noqa: E402
from sweep.tests.test_public_sweep import sweeping  # noqa: E402
from sweep.tests.test_search_v2_shadow_eval import (  # noqa: E402
    BASE_ENV, EIGHT, REGISTRY, SHADOW_ON, _env, production_view, routes)
from sweep.tests.test_worker_link import (WORKER_TOKEN, render_app,  # noqa: E402
                                          unlocked)

READY = {scraper.READY_FLAG: "1"}
TIMEOUT = 20
FIRST_SHADOW = EIGHT[0]
FIRST_PRODUCTION = "lever:alpha"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def host_of(address):
        return address[0] if isinstance(address, tuple) else address

    def connect(sock, address, *a, **kw):
        if host_of(address) not in LOOPBACK:
            raise AssertionError(f"a B5 test tried to reach {host_of(address)}")
        return _REAL_CONNECT(sock, address, *a, **kw)

    def create(address, *a, **kw):
        if host_of(address) not in LOOPBACK:
            raise AssertionError(f"a B5 test tried to reach {host_of(address)}")
        return _REAL_CREATE(address, *a, **kw)
    socket.socket.connect = connect
    socket.create_connection = create


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


# ---------------------------------------------------------------------------
# The engine, driven
# ---------------------------------------------------------------------------
class Crash(BaseException):
    """A stop no `except Exception` absorbs: what an OOM kill or a segfault
    looks like from inside the sweep."""


class Gate:
    """One point the engine thread stops at until the test says go."""

    def __init__(self, board=None):
        self.board = board
        self.here = threading.Event()
        self.go = threading.Event()

    def reached(self):
        self.here.set()
        if not self.go.wait(TIMEOUT):
            raise AssertionError(f"the gate at {self.board} never opened")

    def wait(self):
        if not self.here.wait(TIMEOUT):
            raise AssertionError(f"the engine never reached {self.board}")

    def open(self):
        self.go.set()


@contextlib.contextmanager
def answered(table, holds=(), crash=None, delay=None, events=None):
    """ats.get_json from `table`. A request for a board in `holds` waits at
    that gate; one for `crash` dies with Crash; `delay` sleeps per board."""
    real = ats.get_json
    gates = {g.board: g for g in holds}

    def fake(url, *a, **kw):
        board, body = table[url]
        if events is not None:
            events.append(f"get:{board}")
        if board in gates:
            gates[board].reached()
        if board == crash:
            raise Crash(board)
        time.sleep((delay or {}).get(board, 0))
        return copy.deepcopy(body)
    ats.get_json = fake
    try:
        yield
    finally:
        ats.get_json = real


def run_engine(out, env=None, holds=(), crash=None, delay=None, events=None,
               patches=(), argv=None, registry=None):
    """scraper.main() end to end, offline, writing into `out` and leaving it
    there. Every HTTP response is a fixture through the real adapter."""
    saved = (sys.argv, scraper.ATS_BOARDS, scraper.FEEDS,
             scraper.SETTINGS["output_dir"])
    log = io.StringIO()
    try:
        sys.argv = argv or ["scraper.py", "--site", "free", "--yes"]
        scraper.ATS_BOARDS = copy.deepcopy(registry or REGISTRY)
        scraper.FEEDS = {}
        scraper.SETTINGS["output_dir"] = out
        with contextlib.ExitStack() as stack:
            stack.enter_context(_env(**dict(BASE_ENV, **(env or {}))))
            stack.enter_context(answered(routes(), holds, crash, delay, events))
            for patch in patches:
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(log))
            stack.enter_context(contextlib.redirect_stderr(log))
            scraper.main()
    finally:
        (sys.argv, scraper.ATS_BOARDS, scraper.FEEDS,
         scraper.SETTINGS["output_dir"]) = saved
        # A process that died takes its open record with it.
        telemetry._run = None
    return log.getvalue()


class EngineChild:
    """What the worker's Queue holds for a run — wait() blocks until the
    child exits and returns its status — with the real engine behind it, on
    a thread, writing into that run's own output directory."""

    def __init__(self, out, **engine):
        self.out, self.code, self.log = out, None, ""
        self.exited_at = None
        self.thread = threading.Thread(target=self._run, kwargs=engine,
                                       daemon=True)
        self.thread.start()

    def _run(self, **engine):
        try:
            self.log = run_engine(self.out, **engine)
            self.code = 0
        except SystemExit as exc:
            self.code = (0 if exc.code is None else
                         exc.code if isinstance(exc.code, int) else 1)
        except BaseException:
            self.code = 1
        self.exited_at = time.time()

    def wait(self):
        self.thread.join(TIMEOUT)
        return self.code

    def terminate(self):
        pass


class Immediate:
    """A child that exits the moment it is asked, with nothing written."""

    def __init__(self, out, code=0):
        self.code = code

    def wait(self):
        return self.code

    def terminate(self):
        pass


def engine(**kw):
    return lambda out: EngineChild(out, **kw)


class Stack:
    def __init__(self, url, store, queue, started, app):
        self.url, self.store, self.queue = url, store, queue
        self.started, self.app = started, app
        self.gates = []          # opened on the way out, so no engine hangs

    def run_id(self, n=0):
        return self.started[n][0]

    def child(self, n=0):
        return self.started[n][1]

    def owner(self, run_id):
        return self.store.read(run_id)["owner"]

    def status(self, run_id):
        """What Render is told, over the wire, with the right owner."""
        return worker_client.run_status(run_id, owner=self.owner(run_id))

    def rows(self, run_id):
        return worker_client.all_rows(run_id, owner=self.owner(run_id))

    def exited(self, run_id):
        """Wait for the worker to record the process's exit."""
        for _ in range(TIMEOUT * 50):
            if self.store.read(run_id)["state"] in sweep_worker.TERMINAL:
                return self.store.read(run_id)
            time.sleep(0.02)
        raise AssertionError(f"run {run_id} never finished")


@contextlib.contextmanager
def stack(*children):
    """The real worker on loopback and a public Render app pointed at it.
    The n-th run the worker starts gets children[n], a function of that
    run's output directory returning its child."""
    from werkzeug.serving import make_server

    runs = tempfile.mkdtemp(prefix="b5-runs-")
    checkout = tempfile.mkdtemp(prefix="b5-checkout-")
    os.makedirs(os.path.join(checkout, "profiles"))
    pending, started = list(children), []

    def spawn(run_id, run_dir, profile_name, checkout_dir, token):
        child = pending.pop(0)(os.path.join(run_dir, "output"))
        started.append((run_id, child))
        return child, None

    store = sweep_worker.RunStore(runs)
    queue = sweep_worker.Queue(store, spawn=spawn, checkout=checkout)
    worker = sweep_worker.create_app(store=store, queue=queue,
                                     accepted={WORKER_TOKEN}, checkout=checkout)
    server = make_server("127.0.0.1", 0, worker, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    s = None
    try:
        with mock.patch.dict(os.environ, {worker_client.URL_ENV: url,
                                          worker_client.TOKEN_ENV: WORKER_TOKEN}):
            s = Stack(url, store, queue, started, render_app(url))
            try:
                yield s
            finally:
                # Every engine must be out before the environment it runs
                # under is put back.
                for g in s.gates:
                    g.open()
                for _run_id, child in started:
                    if isinstance(child, EngineChild):
                        child.thread.join(TIMEOUT)
                for _ in range(TIMEOUT * 50):
                    if queue.active() == (0, 0):
                        break
                    time.sleep(0.02)
                time.sleep(0.05)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        shutil.rmtree(runs, ignore_errors=True)
        shutil.rmtree(checkout, ignore_errors=True)


def final_csv_rows(out):
    [path] = glob.glob(os.path.join(out, "jobs_*.csv"))
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def on_disk(out):
    """Every file the sweep left, by name, as bytes (the telemetry record
    aside: it carries clocks and ids)."""
    got = {}
    for name in sorted(os.listdir(out)):
        path = os.path.join(out, name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                got[name] = fh.read()
    return got


def outputs(out):
    return {n: b for n, b in on_disk(out).items()
            if n.startswith("jobs_") or n == "seen.tsv"}


def record_of(out):
    [path] = glob.glob(os.path.join(out, "telemetry", "sweep_*.json"))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def crash_in(target, name):
    def die(*a, **kw):
        raise Crash(name)
    return mock.patch.object(target, name, die)


def spy(target, name, events, label=None):
    real = getattr(target, name)

    def wrapped(*a, **kw):
        events.append(label or name)
        return real(*a, **kw)
    return mock.patch.object(target, name, wrapped)


class Midway(list):
    """Rows that do something halfway through being written — wait at a
    gate, or crash — on one pass: 1 is the CSV, 2 is the JSON."""

    def __init__(self, rows, action, at_pass=1):
        super().__init__(rows)
        self.action, self.at_pass, self.passes = action, at_pass, 0

    def __iter__(self):
        self.passes += 1
        half = len(self) // 2
        for i, row in enumerate(list.__iter__(self)):
            if self.passes == self.at_pass and i == half:
                self.action()
            yield row


def write_interrupted(call, action, at_pass=1):
    """scraper.write_outputs with its `call`-th invocation interrupted."""
    real = scraper.write_outputs
    count = [0]

    def wrapped(out_rows, csv_path, json_path):
        count[0] += 1
        if count[0] == call:
            out_rows = Midway(out_rows, action, at_pass)
        return real(out_rows, csv_path, json_path)
    return mock.patch.object(scraper, "write_outputs", wrapped)


def publish(out):
    """The engine's own marker, by the engine's own function."""
    saved = scraper.SETTINGS["output_dir"]
    scraper.SETTINGS["output_dir"] = out
    try:
        return scraper.publish_results_ready()
    finally:
        scraper.SETTINGS["output_dir"] = saved


def temp_dir(test):
    out = tempfile.mkdtemp(prefix="b5-out-")
    test.addCleanup(shutil.rmtree, out, ignore_errors=True)
    return out


# Every key the worker's status carried before V2-B5, for a run that has
# started. Nothing may be added to it unless the engine published a marker.
PRE_B5_KEYS = {"run_id", "state", "created_at", "started_at", "finished_at",
               "free_only", "profile_name", "profile_path", "exit_code",
               "error", "queue_position", "done"}
READY_KEYS = {"results_ready", "results_ready_at"}


# ---------------------------------------------------------------------------
# The running page's own script, run by node against a real /progress body
# ---------------------------------------------------------------------------
NODE = shutil.which("node")

PAGE_SCRIPT = r"""
const {script, payload, waiting} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const timers = [];
let went = null;
const scope = {
  p: {finished: false, interrupted: false}, drift: 0, waiting,
  fetch: () => Promise.resolve({ok: true, json: () => Promise.resolve(payload)}),
  setTimeout: (fn) => { timers.push(fn); return timers.length; },
  setInterval: () => 0,
  get location() { return went; },
  set location(v) { went = String(v); },
};
with (scope) { eval(script); }
(async () => {
  for (let i = 0; i < 6 && timers.length && went === null; i++) {
    timers.shift()();
    await new Promise(r => setImmediate(r));
  }
  process.stdout.write(JSON.stringify({went}));
})();
"""


def where_the_page_goes(page, payload):
    """Run /running's x-init — the poll and the move to results, exactly as
    served — against one /progress payload. Where it sent the browser, or
    None if it would keep polling."""
    scripts = [html.unescape(s) for s in re.findall(r'x-init="([^"]*)"', page)]
    [script] = [s for s in scripts if "/progress" in s]
    waiting = re.search(r"waiting: (true|false)", page).group(1) == "true"
    done = subprocess.run(
        [NODE, "-e", PAGE_SCRIPT], capture_output=True, text=True, timeout=30,
        input=json.dumps({"script": script, "payload": payload,
                          "waiting": waiting}))
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)["went"]


# ===========================================================================
# 1. Flag off: the lifecycle as it was
# ===========================================================================
class FlagOff(unittest.TestCase):

    def test_default_is_off(self):
        with _env(**{scraper.READY_FLAG: None}):
            self.assertFalse(scraper.results_ready_early())
        for value, on in (("1", True), ("true", True), ("on", True),
                          ("yes", True), ("0", False), ("", False),
                          ("off", False), ("banana", False)):
            with _env(**{scraper.READY_FLAG: value}):
                self.assertEqual(scraper.results_ready_early(), on, value)

    def test_no_deployment_file_turns_it_on(self):
        flag = re.escape(scraper.READY_FLAG)
        for path in ("render.yaml", "deploy/sweep_worker.py", "requirements.txt",
                     "gunicorn.conf.py"):
            with open(os.path.join(REPO, path), encoding="utf-8") as fh:
                body = fh.read()
            self.assertEqual(re.findall(
                r"""\benv\[["']%(f)s["']\]\s*=|\bkey:\s*%(f)s\b|^\s*%(f)s\s*="""
                % {"f": flag}, body, re.M), [], f"{path} sets the flag")

    def test_off_writes_no_marker_and_the_same_bytes(self):
        off, on = temp_dir(self), temp_dir(self)
        run_engine(off, SHADOW_ON)
        run_engine(on, {**SHADOW_ON, **READY})
        self.assertNotIn(scraper.READY_MARKER, os.listdir(off))
        self.assertIn(scraper.READY_MARKER, os.listdir(on))
        self.assertEqual(outputs(off), outputs(on))
        self.assertTrue(outputs(off), "the fixture wrote nothing to compare")

    def test_the_lifecycle_without_the_flag_is_what_it_always_was(self):
        """Held in the shadow stage — after the result is written — the
        status carries exactly its old keys and the screen says running;
        only the process's exit moves either."""
        child, hold = held_at(FIRST_SHADOW, SHADOW_ON)
        with stack(child) as s:
            s.gates.append(hold)
            client = sweeping(s.app, s.store)
            hold.wait()
            status = s.status(s.run_id())
            self.assertEqual(set(status), PRE_B5_KEYS)
            self.assertEqual(status["state"], sweep_worker.RUNNING)
            p = client.get("/progress").get_json()
            self.assertFalse(p["finished"])
            self.assertEqual(p["state"], "running")
            self.assertEqual(p["banner"]["headline"], "Sweep in progress")
            hold.open()
            self.assertEqual(s.exited(s.run_id())["state"], sweep_worker.DONE)
            self.assertTrue(client.get("/progress").get_json()["finished"])
            self.assertEqual(set(s.status(s.run_id())), PRE_B5_KEYS)


def held_at(board, env, **kw):
    """A child whose engine stops at `board`'s request, and that gate. The
    caller hands the gate to the stack, which opens it on the way out."""
    gate = Gate(board)
    return engine(env=env, holds=(gate,), **kw), gate


# ===========================================================================
# 2. The boundary
# ===========================================================================
class ReadyBoundary(unittest.TestCase):

    def test_published_after_the_last_write_and_the_ledger_before_any_shadow_request(self):
        out, events = temp_dir(self), []
        run_engine(out, {**SHADOW_ON, **READY}, events=events, patches=[
            spy(scraper, "write_outputs", events),
            spy(scraper, "record_seen", events),
            spy(scraper, "publish_results_ready", events, "publish"),
            spy(telemetry, "identity", events),
            spy(telemetry, "finish", events)])
        at = events.index("publish")
        before, after = events[:at], events[at + 1:]
        self.assertEqual([e for e in before if not e.startswith("get:")],
                         ["write_outputs", "write_outputs", "record_seen"])
        self.assertNotIn("write_outputs", after)
        self.assertNotIn("record_seen", after)
        self.assertEqual([e[4:] for e in after if e.startswith("get:")], EIGHT)
        self.assertFalse(any(e[4:] in EIGHT for e in before if e.startswith("get:")))
        self.assertEqual([e for e in after if not e.startswith("get:")],
                         ["identity", "finish"])

    def test_every_output_is_final_when_it_is_published(self):
        out, seen = temp_dir(self), {}
        real = scraper.publish_results_ready

        def look():
            seen.update(names=sorted(os.listdir(out)), bytes=outputs(out))
            return real()
        run_engine(out, {**SHADOW_ON, **READY}, patches=[
            mock.patch.object(scraper, "publish_results_ready", look)])
        self.assertEqual(seen["bytes"], outputs(out))
        self.assertEqual(sorted(n.split("_")[0] for n in seen["bytes"]),
                         ["jobs", "jobs", "seen.tsv"])
        self.assertFalse([n for n in seen["names"] if n.endswith(".tmp")])
        self.assertNotIn(scraper.READY_MARKER, seen["names"])

    def test_the_marker_is_a_time_and_nothing_else(self):
        out = temp_dir(self)
        before = time.time()
        run_engine(out, READY)
        with open(os.path.join(out, scraper.READY_MARKER), encoding="utf-8") as fh:
            body = json.load(fh)
        self.assertEqual(set(body), {"at"})
        self.assertGreaterEqual(body["at"], before)
        self.assertLessEqual(body["at"], time.time())

    def test_nothing_scraped_is_never_ready(self):
        """The engine's own "No jobs scraped" exit: its one board fails."""
        out = temp_dir(self)
        with self.assertRaises(SystemExit) as stopped:
            run_engine(out, READY, registry={"greenhouse": {"nosuchboard": "X"}})
        self.assertIn("No jobs scraped", str(stopped.exception.code))
        self.assertNotIn(scraper.READY_MARKER, os.listdir(out))

    def test_a_crash_before_any_output_is_never_ready(self):
        out = temp_dir(self)
        with self.assertRaises(Crash):
            run_engine(out, READY, crash="greenhouse:gamma")
        self.assertEqual([n for n in os.listdir(out) if not n.startswith(".")
                          and n != "telemetry"], [])
        self.assertNotIn(scraper.READY_MARKER, os.listdir(out))

    def test_the_ledger_is_inside_the_boundary(self):
        """A crash while the seen ledger is written leaves the CSV and JSON
        complete, and still no marker: readiness includes the ledger."""
        out = temp_dir(self)
        with self.assertRaises(Crash):
            run_engine(out, READY, patches=[crash_in(scraper, "record_seen")])
        self.assertEqual(len(final_csv_rows(out)), len(final_csv_rows(
            self._complete())))
        self.assertNotIn(scraper.READY_MARKER, os.listdir(out))

    def test_a_stale_marker_from_an_earlier_run_is_cleared_at_start(self):
        """A reused directory (a console profile's) cannot inherit one."""
        out, reached = temp_dir(self), {}
        publish(out)
        real = scraper.write_outputs

        def first_write(*a, **kw):
            reached.setdefault("marker", os.path.exists(
                os.path.join(out, scraper.READY_MARKER)))
            return real(*a, **kw)
        run_engine(out, patches=[mock.patch.object(scraper, "write_outputs",
                                                   first_write)])
        self.assertFalse(reached["marker"])
        self.assertNotIn(scraper.READY_MARKER, os.listdir(out))

    def _complete(self):
        out = temp_dir(self)
        run_engine(out)
        return out


# ===========================================================================
# 3. Output atomicity
# ===========================================================================
def big_rows(tag, n=400):
    """Enough rows that a half-written file reaches the disk, not just a
    buffer — so a torn read is visible if one is possible."""
    return [{"title": f"{tag} engineer {i}", "company": f"Co {i}",
             "location": "Remote", "score": i, "apply_url":
             f"https://example.invalid/{tag}/{i}", "matched_skills": "x" * 60}
            for i in range(n)]


class AtomicOutput(unittest.TestCase):

    def setUp(self):
        self.out = temp_dir(self)
        self.csv = os.path.join(self.out, "jobs_2026-09-23_1200.csv")
        self.json = os.path.join(self.out, "jobs_2026-09-23_1200.json")
        saved = scraper.SETTINGS["output_dir"]
        scraper.SETTINGS["output_dir"] = self.out
        self.addCleanup(scraper.SETTINGS.__setitem__, "output_dir", saved)

    def write(self, rows):
        scraper.write_outputs(rows, self.csv, self.json)

    def paused(self, rows, at_pass=1):
        """Start a write that stops halfway; returns (gate, thread)."""
        gate = Gate("write")
        thread = threading.Thread(target=self.write, args=(
            Midway(rows, gate.reached, at_pass),), daemon=True)
        thread.start()
        gate.wait()
        # Before the directory goes: the writer must not rename into it.
        self.addCleanup(thread.join, TIMEOUT)
        self.addCleanup(gate.open)
        return gate, thread

    def served(self):
        return sweep_worker.rows_since(self.out, 0, limit=10_000)

    def test_a_reader_during_the_first_write_finds_no_file_not_half_of_one(self):
        gate, thread = self.paused(big_rows("new"))
        self.assertEqual(self.served(), ([], 0))
        self.assertFalse(os.path.exists(self.csv))
        gate.open()
        thread.join(TIMEOUT)
        rows, total = self.served()
        self.assertEqual(total, 400)

    def test_a_reader_during_a_rewrite_reads_the_previous_complete_file(self):
        self.write(big_rows("old"))
        for at_pass in (1, 2):
            with self.subTest(during="csv" if at_pass == 1 else "json"):
                gate, thread = self.paused(big_rows("new"), at_pass)
                rows, total = self.served()
                self.assertEqual(total, 400)
                expect_csv = "new" if at_pass == 2 else "old"
                self.assertTrue(all(r["title"].startswith(expect_csv)
                                    for r in rows))
                with open(self.json, encoding="utf-8") as fh:
                    whole = json.load(fh)          # never a torn document
                self.assertEqual(len(whole), 400)
                self.assertTrue(all(r["title"].startswith("old") for r in whole))
                gate.open()
                thread.join(TIMEOUT)
                with open(self.json, encoding="utf-8") as fh:
                    self.assertTrue(json.load(fh)[0]["title"].startswith("new"))
                self.write(big_rows("old"))

    def test_a_crash_mid_write_keeps_the_previous_file_and_no_temp(self):
        self.write(big_rows("old"))
        before = on_disk(self.out)

        def die():
            raise Crash("mid-write")
        with self.assertRaises(Crash):
            self.write(Midway(big_rows("new"), die))
        self.assertEqual(on_disk(self.out), before)
        rows, total = self.served()
        self.assertEqual(total, 400)
        self.assertTrue(all(r["title"].startswith("old") for r in rows))

    def test_the_bytes_are_what_the_plain_writer_wrote(self):
        rows = big_rows("same", 30)
        self.write(rows)
        with open(self.csv, "rb") as fh:
            atomic_csv = fh.read()
        with open(self.json, "rb") as fh:
            atomic_json = fh.read()
        # The writer as it was before V2-B5, verbatim but for the paths.
        plain_csv, plain_json = self.csv + ".plain", self.json + ".plain"
        with open(plain_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=scraper.OUTPUT_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        with open(plain_json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
        with open(plain_csv, "rb") as fh:
            self.assertEqual(atomic_csv, fh.read())
        with open(plain_json, "rb") as fh:
            self.assertEqual(atomic_json, fh.read())

    def test_the_temp_file_matches_no_reader_of_the_output_directory(self):
        self.paused(big_rows("new"))
        temps = [n for n in os.listdir(self.out) if n.endswith(".tmp")]
        self.assertEqual(len(temps), 1, os.listdir(self.out))
        for pattern in ("**/*.csv", "jobs_2*.csv", "jobs_combined*.csv",
                        "jobs_*.json"):
            found = glob.glob(os.path.join(self.out, pattern), recursive=True)
            self.assertFalse([f for f in found if f.endswith(".tmp")], pattern)
        self.assertEqual(self.served(), ([], 0))

    def test_a_temp_left_by_a_killed_process_is_never_served(self):
        self.write(big_rows("old"))
        with open(self.csv + ".4242.tmp", "w", encoding="utf-8") as fh:
            fh.write("title,company\nhalf a ro")
        rows, total = self.served()
        self.assertEqual(total, 400)

    def test_the_marker_is_whole_or_absent(self):
        """Race E: a poll exactly while readiness is being published."""
        gate = Gate("marker")
        real = scraper._replaced

        @contextlib.contextmanager
        def slow(path, **kw):
            with real(path, **kw) as fh:
                yield fh
                if path.endswith(scraper.READY_MARKER):
                    fh.flush()
                    gate.reached()      # written in full, not yet renamed
        with mock.patch.object(scraper, "_replaced", slow):
            thread = threading.Thread(target=publish, args=(self.out,),
                                      daemon=True)
            thread.start()
            gate.wait()
            self.assertEqual(sweep_worker.results_ready(self.out), {})
            gate.open()
            thread.join(TIMEOUT)
        got = sweep_worker.results_ready(self.out)
        self.assertEqual(set(got), READY_KEYS)
        self.assertIs(got["results_ready"], True)

    def test_a_marker_that_is_not_the_engines_reads_as_not_ready(self):
        path = os.path.join(self.out, scraper.READY_MARKER)
        for body in ("", "{", '{"at": ', "[]", "null", '{"at": "soon"}',
                     '{"at": true}', '{"when": 1}', "\x00\x01"):
            with self.subTest(body=body):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(body)
                self.assertEqual(sweep_worker.results_ready(self.out), {})
        os.remove(path)
        self.assertEqual(sweep_worker.results_ready(self.out), {})

    def test_worker_and_engine_agree_on_the_marker(self):
        self.assertEqual(sweep_worker.READY_MARKER, scraper.READY_MARKER)
        publish(self.out)
        self.assertEqual(set(sweep_worker.results_ready(self.out)), READY_KEYS)


class AtomicOutputEndToEnd(unittest.TestCase):
    """Race A: the final write paused halfway, polled from Render."""

    def test_never_ready_while_the_final_write_is_paused(self):
        pause = Gate("final write")
        child = engine(env=READY, patches=[write_interrupted(2, pause.reached)])
        with stack(child) as s:
            s.gates.append(pause)
            client = sweeping(s.app, s.store)
            pause.wait()
            run_id = s.run_id()
            status = s.status(run_id)
            self.assertEqual(status["state"], sweep_worker.RUNNING)
            self.assertFalse(READY_KEYS & set(status))
            self.assertFalse(client.get("/progress").get_json()["finished"])
            # What a reader gets meanwhile is the checkpoint, whole.
            during = s.rows(run_id)
            pause.open()
            s.exited(run_id)
            self.assertIs(s.status(run_id)["results_ready"], True)
            self.assertEqual(during, s.rows(run_id))
            self.assertTrue(during)


# ===========================================================================
# 4-5. The active slot and the queue (race C)
# ===========================================================================
class ActiveSlotAndQueue(unittest.TestCase):

    def test_ready_releases_no_slot_and_starts_nothing(self):
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child, lambda out: Immediate(out)) as s:
            s.gates.append(hold)
            first = sweeping(s.app, s.store)
            hold.wait()
            a = s.run_id()
            status = s.status(a)
            self.assertEqual(status["state"], sweep_worker.RUNNING)
            self.assertIs(status["results_ready"], True)
            self.assertIsNone(status["finished_at"])
            self.assertIsNone(status["exit_code"])
            self.assertTrue(first.get("/progress").get_json()["finished"])

            second = sweeping(s.app, s.store, name="second_visitor")
            [b] = [r["run_id"] for r in s.store.all() if r["run_id"] != a]
            self.assertEqual(s.store.read(b)["state"], sweep_worker.QUEUED)
            self.assertEqual(s.status(b)["queue_position"], 1)
            self.assertEqual(len(s.started), 1, "a second sweep started early")
            self.assertEqual(s.queue.active(), (1, 1))
            self.assertIn(a, s.queue._children)
            self.assertTrue(second.get("/progress").get_json()["queued"])

            hold.open()
            self.assertEqual(s.exited(a)["state"], sweep_worker.DONE)
            s.exited(b)
            self.assertEqual([r for r, _c in s.started], [a, b])
            self.assertLessEqual(s.child(0).exited_at,
                                 s.store.read(b)["started_at"])

    def test_render_still_counts_the_process_as_in_flight(self):
        """A second Start from the same browser is not a second run."""
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            client = sweeping(s.app, s.store)
            hold.wait()
            again = client.post("/run")
            self.assertIn("/running", again.headers["Location"])
            self.assertEqual(len(s.store.all()), 1)

    def test_healthz_still_counts_the_child(self):
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            sweeping(s.app, s.store)
            hold.wait()
            import urllib.request
            req = urllib.request.Request(
                s.url + "/healthz",
                headers={"Authorization": f"Bearer {WORKER_TOKEN}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                self.assertEqual(json.loads(r.read())["running"], 1)


# ===========================================================================
# 6-10. The screen, the result endpoint and the exports
# ===========================================================================
class UITransition(unittest.TestCase):

    def test_running_and_ready_is_finished_on_the_screen(self):
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            client = sweeping(s.app, s.store)
            hold.wait()
            self.assertEqual(s.store.read(s.run_id())["state"],
                             sweep_worker.RUNNING)
            p = client.get("/progress").get_json()
            self.assertTrue(p["finished"])
            self.assertFalse(p["interrupted"])
            self.assertFalse(p["free_running"])
            self.assertEqual(p["state"], "finished")
            self.assertEqual(p["remaining_text"], "finished")
            self.assertEqual(p["banner"]["headline"], "Sweep complete")
            self.assertEqual(p["banner"]["to"], "results")
            page = client.get("/running").get_data(as_text=True)
            self.assertIn("Your jobs are ready", page)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_page_script_moves_to_results_at_readiness(self):
        early = Gate(FIRST_PRODUCTION)
        hold = Gate(FIRST_SHADOW)
        child = engine(env={**SHADOW_ON, **READY}, holds=(early, hold))
        with stack(child) as s:
            s.gates += [early, hold]
            client = sweeping(s.app, s.store)
            early.wait()
            page = client.get("/running").get_data(as_text=True)
            self.assertIn("waiting: true", page)
            self.assertIsNone(where_the_page_goes(
                page, client.get("/progress").get_json()))
            early.open()
            hold.wait()
            self.assertEqual(s.store.read(s.run_id())["state"],
                             sweep_worker.RUNNING)
            self.assertEqual(where_the_page_goes(
                page, client.get("/progress").get_json()), "/results")

    @unittest.skipUnless(NODE, "node is not installed")
    def test_without_the_field_the_page_keeps_polling_until_done(self):
        early = Gate(FIRST_PRODUCTION)
        hold = Gate(FIRST_SHADOW)
        child = engine(env=SHADOW_ON, holds=(early, hold))
        with stack(child) as s:
            s.gates += [early, hold]
            client = sweeping(s.app, s.store)
            early.wait()
            page = client.get("/running").get_data(as_text=True)
            early.open()
            hold.wait()
            self.assertIsNone(where_the_page_goes(
                page, client.get("/progress").get_json()))
            hold.open()
            s.exited(s.run_id())
            self.assertEqual(where_the_page_goes(
                page, client.get("/progress").get_json()), "/results")

    def test_an_older_render_ignores_the_field_and_waits_for_done(self):
        """Deployment order: new worker, old Render. The old Render asks the
        process, which is RemoteRun.poll(), and that has not changed."""
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            client = sweeping(s.app, s.store)
            hold.wait()
            cookie = client.get_cookie("session").value
            with s.app.test_request_context(
                    headers={"Cookie": f"session={cookie}"}):
                self.assertIsNone(worker_link.RemoteRun(s.run_id()).poll())
            # The same browser on a Render without V2-B5, which is this app
            # with the one new reading switched off: it rehydrates the run
            # from the worker and reads it as still running.
            old = render_app(s.url, read_ready=lambda: False).test_client()
            old.set_cookie("session", cookie, domain="localhost")
            p = old.get("/progress").get_json()
            self.assertFalse(p["finished"])
            self.assertEqual(p["state"], "running")

    def test_a_newer_render_on_an_older_worker_waits_for_done(self):
        """Deployment order: new Render, old worker — no field at all."""
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            client = sweeping(s.app, s.store)
            hold.wait()
            with mock.patch.object(sweep_worker, "results_ready",
                                   lambda out: {}):
                self.assertFalse(READY_KEYS & set(s.status(s.run_id())))
                self.assertFalse(client.get("/progress").get_json()["finished"])

    def test_only_a_true_moves_the_screen(self):
        child, hold = held_at(FIRST_SHADOW, SHADOW_ON)
        with stack(child) as s:
            s.gates.append(hold)
            client = sweeping(s.app, s.store)
            hold.wait()
            for value in ("true", 1, "yes", [True], {"ready": True}):
                with self.subTest(value=value), mock.patch.object(
                        sweep_worker, "results_ready",
                        lambda out, v=value: {"results_ready": v}):
                    self.assertFalse(client.get("/progress").get_json()["finished"])

    def test_the_strip_on_other_screens_says_complete(self):
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            client = sweeping(s.app, s.store)
            hold.wait()
            page = client.get("/configure").get_data(as_text=True)
            self.assertEqual(re.search(r"data-run-headline>([^<]*)<",
                                       page).group(1), "Sweep complete")
            self.assertIsNone(client.get("/activity?on=results")
                              .get_json()["banner"])

    def test_nothing_the_user_reads_names_an_internal_stage(self):
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            client = sweeping(s.app, s.store)
            hold.wait()
            p = client.get("/progress").get_json()
            said = " ".join(str(v) for v in (
                p["remaining_text"], p["state"], *p["banner"].values()))
            for word in ("shadow", "telemetry", "diagnos", "greenhouse",
                         "lever", "post-result", "ready_early"):
                self.assertNotIn(word, said.lower())


class ResultsAndExports(unittest.TestCase):
    """8-9: at readiness, the same result the user gets at DONE."""

    def test_rows_results_and_exports_at_readiness_equal_those_at_done(self):
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            client = sweeping(s.app, s.store)
            hold.wait()
            run_id = s.run_id()
            self.assertEqual(s.store.read(run_id)["state"], sweep_worker.RUNNING)
            early = {"rows": s.rows(run_id),
                     "results": client.get("/results?full=1"),
                     "csv": client.get("/export.csv"),
                     "json": client.get("/export.json")}
            for name in ("results", "csv", "json"):
                self.assertEqual(early[name].status_code, 200, name)
            hold.open()
            s.exited(run_id)
            self.assertEqual(early["rows"], s.rows(run_id))
            self.assertEqual(early["rows"], final_csv_rows(
                s.store.output_dir(run_id)))
            self.assertEqual(early["csv"].get_data(),
                             client.get("/export.csv").get_data())
            self.assertEqual(early["json"].get_data(),
                             client.get("/export.json").get_data())
            titles = [r["title"] for r in logic.shortlist(early["rows"])]
            self.assertTrue(titles)
            self.assertEqual(len(early["json"].get_json()), len(titles))
            page = early["results"].get_data(as_text=True)
            for title in titles:
                self.assertIn(html.escape(title), page)


# ===========================================================================
# 10-14. Failure after readiness (race B, race D) and before it
# ===========================================================================
class FailureAfterReady(unittest.TestCase):
    """The result stays; the process's own ending is recorded apart."""

    def ended_after_ready(self, child):
        with stack(child) as s:
            client = sweeping(s.app, s.store)
            run_id = s.run_id()
            final = s.exited(run_id)
            status = s.status(run_id)
            self.assertIs(status["results_ready"], True)
            self.assertLess(status["results_ready_at"], final["finished_at"])
            rows = s.rows(run_id)
            self.assertTrue(rows)
            self.assertEqual(rows, final_csv_rows(s.store.output_dir(run_id)))
            self.assertEqual(client.get("/results").status_code, 200)
            # The screen's own filter, so the count is the one it shows.
            self.assertEqual(len(client.get("/export.json").get_json()),
                             len(logic.shortlist(rows)))
            self.assertTrue(client.get("/progress").get_json()["finished"])
            return final, s.child().log

    def test_a_shadow_board_failing_after_ready(self):
        final, _log = self.ended_after_ready(engine(
            env={**SHADOW_ON, **READY}, patches=[
                mock.patch.object(shadow, "run", side_effect=RuntimeError("x"))]))
        self.assertEqual(final["state"], sweep_worker.DONE)

    def test_a_crash_during_the_shadow_fetch(self):
        final, _log = self.ended_after_ready(engine(
            env={**SHADOW_ON, **READY}, crash=FIRST_SHADOW))
        self.assertEqual(final["state"], sweep_worker.FAILED)
        self.assertEqual(final["exit_code"], 1)
        self.assertIn("exited with status 1", final["error"])

    def test_a_crash_immediately_after_ready(self):
        final, _log = self.ended_after_ready(engine(
            env=READY, patches=[crash_in(telemetry, "identity")]))
        self.assertEqual(final["state"], sweep_worker.FAILED)

    def test_a_crash_while_finishing_telemetry(self):
        final, _log = self.ended_after_ready(engine(
            env={**SHADOW_ON, **READY}, patches=[crash_in(telemetry, "finish")]))
        self.assertEqual(final["state"], sweep_worker.FAILED)

    def test_a_record_that_cannot_be_written(self):
        def block(out):
            open(os.path.join(out, "telemetry"), "w").close()
            return EngineChild(out, env={**SHADOW_ON, **READY})
        final, log = self.ended_after_ready(block)
        self.assertEqual(final["state"], sweep_worker.DONE)
        self.assertIn("telemetry: could not write", log)

    def test_a_worker_restart_after_ready(self):
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            sweeping(s.app, s.store)
            hold.wait()
            run_id = s.run_id()
            expected = s.rows(run_id)
            restarted = sweep_worker.Queue(sweep_worker.RunStore(s.store.root),
                                           spawn=lambda *a: None)
            restarted.recover()
            status = s.status(run_id)
            self.assertEqual(status["state"], sweep_worker.INTERRUPTED)
            self.assertIs(status["results_ready"], True)
            self.assertEqual(s.rows(run_id), expected)

    def test_the_result_is_readable_while_the_shadow_stage_runs(self):
        """Race B, with a gate where the brief had a two-second sleep."""
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            client = sweeping(s.app, s.store)
            hold.wait()
            self.assertEqual(s.store.read(s.run_id())["state"],
                             sweep_worker.RUNNING)
            self.assertEqual(client.get("/results").status_code, 200)
            self.assertTrue(s.rows(s.run_id()))
            hold.open()
            s.exited(s.run_id())
            record = record_of(s.store.output_dir(s.run_id()))
            self.assertEqual([u["board"] for u in record["shadow_units"]], EIGHT)
            self.assertEqual(record["shadow_evaluation"]["status"], "evaluated")


class FailureBeforeReady(unittest.TestCase):
    """No marker, so nothing is announced; and never half a file."""

    def test_a_crash_before_the_result_is_final(self):
        with stack(engine(env=READY, crash="greenhouse:gamma")) as s:
            sweeping(s.app, s.store)
            run_id = s.run_id()
            final = s.exited(run_id)
            self.assertEqual(final["state"], sweep_worker.FAILED)
            status = s.status(run_id)
            self.assertFalse(READY_KEYS & set(status))
            self.assertEqual(s.rows(run_id), [])

    def test_a_crash_while_writing_the_result_serves_no_partial_file(self):
        def die():
            raise Crash("final write")
        with stack(engine(env=READY, patches=[write_interrupted(2, die)])) as s:
            sweeping(s.app, s.store)
            run_id = s.run_id()
            self.assertEqual(s.exited(run_id)["state"], sweep_worker.FAILED)
            self.assertFalse(READY_KEYS & set(s.status(run_id)))
            out = s.store.output_dir(run_id)
            self.assertFalse([n for n in os.listdir(out) if n.endswith(".tmp")])
            # The checkpoint written before the crash, whole — identical to
            # what a sweep that did not crash would have ended with.
            complete = tempfile.mkdtemp(prefix="b5-complete-")
            self.addCleanup(shutil.rmtree, complete, ignore_errors=True)
            run_engine(complete)
            self.assertEqual(s.rows(run_id), final_csv_rows(complete))


# ===========================================================================
# 15. Cleanup and TTL; 16. authorization — the worker alone
# ===========================================================================
class WorkerLevel(Harness):

    def started(self, block):
        app, store, queue = self.build(spawner=self.spawn(block=block))
        client = app.test_client()
        run_id = self.post_run(client).get_json()["run_id"]
        # Runs before Harness removes the directory: let every child exit and
        # the worker record it, so no status write lands in a deleted tree.
        self.addCleanup(self.drain, block, queue)
        return app, store, queue, client, run_id

    def drain(self, block, queue):
        block.set()
        for _ in range(TIMEOUT * 50):
            if queue.active() == (0, 0):
                break
            time.sleep(0.02)
        time.sleep(0.05)

    def test_a_ready_run_whose_process_is_alive_is_never_cleaned(self):
        block = threading.Event()
        _app, store, queue, _client, run_id = self.started(block)
        publish(store.output_dir(run_id))
        self.now[0] += sweep_worker.TTL_SECONDS * 10
        self.assertEqual(queue.cleanup(), [])
        self.assertEqual(queue.cleanup(ttl=0), [])
        self.assertTrue(os.path.exists(store.output_dir(run_id)))
        self.assertEqual(store.read(run_id)["state"], sweep_worker.RUNNING)

    def test_ttl_runs_from_the_process_exit_not_from_readiness(self):
        block = threading.Event()
        _app, store, queue, _client, run_id = self.started(block)
        ready_at = self.now[0]
        with open(os.path.join(store.output_dir(run_id), scraper.READY_MARKER),
                  "w", encoding="utf-8") as fh:
            json.dump({"at": ready_at}, fh)
        self.now[0] += 3600          # an hour of post-result work, say
        block.set()
        for _ in range(100):
            if store.read(run_id)["state"] in sweep_worker.TERMINAL:
                break
            time.sleep(0.02)
        finished_at = store.read(run_id)["finished_at"]
        self.assertEqual(finished_at, ready_at + 3600)
        self.now[0] = ready_at + sweep_worker.TTL_SECONDS + 10
        self.assertEqual(queue.cleanup(), [], "cleaned from readiness")
        self.now[0] = finished_at + sweep_worker.TTL_SECONDS
        self.assertEqual(queue.cleanup(), [run_id])

    def test_a_stranger_gets_nothing_from_a_ready_run(self):
        block = threading.Event()
        _app, store, _queue, client, run_id = self.started(block)
        publish(store.output_dir(run_id))
        mine = client.get(f"/v1/runs/{run_id}", headers=self.auth())
        self.assertIs(mine.get_json()["results_ready"], True)
        self.assertNotIn("owner", mine.get_json())
        for path in (f"/v1/runs/{run_id}", f"/v1/runs/{run_id}/rows"):
            with self.subTest(path=path):
                other = client.get(path, headers=self.auth("session-b"))
                self.assertEqual(other.status_code, 404)
                self.assertNotIn("results_ready", other.get_data(as_text=True))
                bare = client.get(path, headers={
                    "Authorization": f"Bearer {BEARER}"})
                self.assertEqual(bare.status_code, 404)
                self.assertEqual(client.get(path).status_code, 401)

    def test_ready_changes_no_queue_state(self):
        block = threading.Event()
        app, store, queue, client, run_id = self.started(block)
        second = self.post_run(client, owner="session-b").get_json()["run_id"]
        publish(store.output_dir(run_id))
        client.get(f"/v1/runs/{run_id}", headers=self.auth())
        self.assertEqual(queue.active(), (1, 1))
        self.assertEqual(store.read(second)["state"], sweep_worker.QUEUED)
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(store.read(run_id)["state"], sweep_worker.RUNNING)

    def test_readiness_adds_no_path_and_no_string(self):
        block = threading.Event()
        _app, store, _queue, client, run_id = self.started(block)
        publish(store.output_dir(run_id))
        body = client.get(f"/v1/runs/{run_id}", headers=self.auth()).get_json()
        added = {k: body[k] for k in READY_KEYS}
        self.assertFalse([v for v in added.values() if isinstance(v, str)])


class RenderAuthorization(unittest.TestCase):
    """16: readiness is read through the same owner capability as the rest."""

    def test_a_stranger_holding_the_run_id_sees_no_readiness_and_no_rows(self):
        child, hold = held_at(FIRST_SHADOW, {**SHADOW_ON, **READY})
        with stack(child) as s:
            s.gates.append(hold)
            sweeping(s.app, s.store)
            hold.wait()
            run_id = s.run_id()
            stranger = unlocked(s.app)
            with stranger.session_transaction() as sess:
                sess["run_id"] = run_id
            self.assertIsNone(stranger.get("/activity").get_json()["banner"])
            self.assertEqual(stranger.get("/results").status_code, 302)
            self.assertEqual(stranger.get("/export.csv").status_code, 302)
            cookie = stranger.get_cookie("session").value
            ready = worker_link.injections(s.app)["read_ready"]
            with s.app.test_request_context(
                    headers={"Cookie": f"session={cookie}"}):
                self.assertFalse(ready())
                with self.assertRaises(worker_client.RunNotFound):
                    worker_client.run_status(run_id)


# ===========================================================================
# B3 and B4 carry on; telemetry measures the tail
# ===========================================================================
class ShadowAndTelemetry(unittest.TestCase):

    def test_shadow_runs_as_before_with_the_flag_on(self):
        off, on = temp_dir(self), temp_dir(self)
        run_engine(off, SHADOW_ON)
        run_engine(on, {**SHADOW_ON, **READY})
        a, b = record_of(off), record_of(on)
        self.assertEqual(production_view(a), production_view(b))
        strip = ("duration_ms",)
        self.assertEqual([{k: v for k, v in u.items() if k not in strip
                           and not k.endswith("_at")} for u in a["shadow_units"]],
                         [{k: v for k, v in u.items() if k not in strip
                           and not k.endswith("_at")} for u in b["shadow_units"]])
        self.assertEqual([u["board"] for u in b["shadow_units"]], EIGHT)
        ev_a, ev_b = a["shadow_evaluation"], b["shadow_evaluation"]
        self.assertEqual({k: v for k, v in ev_a.items() if k != "cost"},
                         {k: v for k, v in ev_b.items() if k != "cost"})
        self.assertEqual(a["schema"], b["schema"])

    def test_the_record_says_when_the_result_was_ready_and_how_long_after(self):
        out = temp_dir(self)
        run_engine(out, {**SHADOW_ON, **READY},
                   delay={board: 0.05 for board in EIGHT})
        record = record_of(out)
        ready = record["milestones"]["results_ready"]
        self.assertEqual(record["milestone_granularity"]["results_ready"],
                         "exact")
        self.assertGreaterEqual(ready, record["milestones"]["free_phase_done"])
        self.assertEqual(record["post_result_ms"],
                         record["duration_ms"] - ready)
        self.assertGreaterEqual(record["post_result_ms"], 8 * 50)
        self.assertEqual(record["schema"], "search-v2a.1")

    def test_measured_with_the_flag_off_too(self):
        """The tail is measured whether or not it is hidden, so the period
        before the flag goes on says what it would hide."""
        out = temp_dir(self)
        run_engine(out, SHADOW_ON)
        record = record_of(out)
        self.assertIn("results_ready", record["milestones"])
        self.assertIn("post_result_ms", record)

    def test_lever_and_greenhouse_concurrency_with_the_flag_on(self):
        serial, both = temp_dir(self), temp_dir(self)
        run_engine(serial)
        run_engine(both, {concurrency.FLAG: "1", concurrency.WORKERS_ENV: "4",
                          concurrency.GREENHOUSE_FLAG: "1",
                          concurrency.GREENHOUSE_WORKERS_ENV: "4", **READY})
        self.assertEqual(outputs(serial), outputs(both))
        self.assertIn(scraper.READY_MARKER, os.listdir(both))

    def test_readiness_knows_nothing_about_providers(self):
        for fn in (scraper.publish_results_ready, scraper.results_ready_early,
                   sweep_worker.results_ready):
            body = inspect.getsource(fn).lower()
            for word in ("lever", "greenhouse", "concurrency", "shadow",
                         "worker_count", "workers"):
                self.assertNotIn(word, body, f"{fn.__name__} names {word}")


# ===========================================================================
# 17. Paid: the same boundary, the same searches, no call
# ===========================================================================
TODAY = datetime.now().strftime("%Y-%m-%d")


class NoClient:
    """The Apify client, if anything reached for it."""

    def __init__(self, *a, **kw):
        pass

    def __getattr__(self, name):
        raise AssertionError(f"the paid client was used ({name})")


def paid_engine(out, env=None, events=None):
    """A paid-only sweep through scraper.main(), every search a fixture."""
    calls = []

    def scrape(client, site_key, actor_id, search):
        calls.append((site_key, search["keywords"], search["location"]))
        if events is not None:
            events.append("scrape")
        n = len(calls)
        item = {"title": f"React Native Developer {n}", "companyName": f"Acme {n}",
                "location": "Remote", "description": "react native typescript",
                "link": f"https://example.invalid/{n}", "postedAt": TODAY}
        return [scraper.normalize(item, site_key)], 0.01
    patches = [mock.patch.object(scraper, "_require_token",
                                 return_value="not-a-real-token"),
               mock.patch.object(scraper, "scrape_search", scrape),
               mock.patch.object(scraper, "account_usage_usd", return_value=None),
               mock.patch("apify_client.ApifyClient", NoClient)]
    if events is not None:
        patches += [spy(scraper, "write_outputs", events),
                    spy(scraper, "record_seen", events),
                    spy(scraper, "publish_results_ready", events, "publish")]
    run_engine(out, env, patches=patches,
               argv=["scraper.py", "--site", "linkedin", "--limit", "2", "--yes"])
    return calls


class PaidIsolation(unittest.TestCase):

    def test_a_paid_sweep_searches_and_writes_the_same_and_is_ready_after(self):
        off, on, events = temp_dir(self), temp_dir(self), []
        calls_off = paid_engine(off)
        calls_on = paid_engine(on, READY, events)
        self.assertEqual(calls_off, calls_on)
        self.assertEqual(len(calls_on), 2)
        self.assertEqual(outputs(off), outputs(on))
        self.assertNotIn(scraper.READY_MARKER, os.listdir(off))
        self.assertIn(scraper.READY_MARKER, os.listdir(on))
        at = events.index("publish")
        self.assertEqual(events[at - 1], "record_seen")
        self.assertNotIn("scrape", events[at:])
        self.assertEqual(events.count("write_outputs"), 3)   # 2 checkpoints + final

    def test_readiness_code_names_nothing_paid(self):
        start = inspect.getsource(scraper.main)
        block = start[start.index('telemetry.mark("results_ready")'):
                      start.index("# SWEEP_FREE_SOURCE_SHADOW")]
        for body in (block, inspect.getsource(scraper.publish_results_ready),
                     inspect.getsource(scraper.results_ready_early),
                     inspect.getsource(sweep_worker.results_ready)):
            for word in ("apify", "actor", "token", "scrape_search", "spend",
                         "SITES"):
                self.assertNotIn(word.lower(), body.lower())

    def test_the_child_environment_is_the_workers(self):
        """The flag reaches the engine the way every other flag does:
        default_spawn copies the worker's environment, which is where a
        deployment would set it; nothing sets it on the child's behalf."""
        seen = {}

        class FakePopen:
            def __init__(self, cmd, cwd, env, stdout, stderr):
                seen["env"] = env
        out = temp_dir(self)
        with mock.patch.object(sweep_worker.subprocess, "Popen", FakePopen), \
                _env(**{scraper.READY_FLAG: None}):
            child, closer = sweep_worker.default_spawn("ab12", out, "beta_ab12",
                                                       out, None)
            closer.close()
        self.assertNotIn(scraper.READY_FLAG, seen["env"])
        with mock.patch.object(sweep_worker.subprocess, "Popen", FakePopen), \
                _env(**READY):
            child, closer = sweep_worker.default_spawn("ab12", out, "beta_ab12",
                                                       out, None)
            closer.close()
        self.assertEqual(seen["env"][scraper.READY_FLAG], "1")


# ===========================================================================
# The measured tail: result final at T1, process done at T2
# ===========================================================================
def measure(ready, tail_s, poll_s=0.025):
    """One sweep through the real stack with a known post-result tail (every
    shadow board delayed tail_s / 8). Returns T1 (marker), T2 (worker's
    finished_at), and when Render's /progress first said finished."""
    env = {**SHADOW_ON, **(READY if ready else {})}
    delay = {board: tail_s / len(EIGHT) for board in EIGHT}
    with stack(engine(env=env, delay=delay)) as s:
        client = sweeping(s.app, s.store)
        run_id, seen_at = s.run_id(), None
        deadline = time.time() + TIMEOUT
        while seen_at is None and time.time() < deadline:
            if client.get("/progress").get_json()["finished"]:
                seen_at = time.time()
            else:
                time.sleep(poll_s)
        final = s.exited(run_id)
        status = s.status(run_id)
        record = record_of(s.store.output_dir(run_id))
        return {"ready": ready, "tail_s": tail_s,
                "t1_results_ready": status.get("results_ready_at"),
                "t2_process_done": final["finished_at"],
                "screen_finished_at": seen_at,
                "post_result_ms": record.get("post_result_ms"),
                "state": final["state"]}


class HiddenWait(unittest.TestCase):

    def test_the_tail_after_readiness_is_no_longer_waited_for(self):
        got = measure(True, 1.2)
        t1, t2, seen = (got["t1_results_ready"], got["t2_process_done"],
                        got["screen_finished_at"])
        self.assertGreaterEqual(t2 - t1, 1.2)
        self.assertLess(seen - t1, 0.5)
        self.assertGreater(t2 - seen, 0.7)
        self.assertGreaterEqual(got["post_result_ms"], 1200)

    def test_without_the_flag_the_screen_waits_for_the_process(self):
        got = measure(False, 0.6)
        self.assertIsNone(got["t1_results_ready"])
        self.assertGreaterEqual(got["screen_finished_at"], got["t2_process_done"])
        self.assertGreaterEqual(got["post_result_ms"], 600)


if __name__ == "__main__":
    unittest.main()
