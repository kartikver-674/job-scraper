"""V2-B4: bounded Greenhouse concurrency must be invisible to everything downstream.

The same property V2-B2 pinned for Lever, for the 54 production Greenhouse
boards: **completion order must never become result order.** Row order decides
score ties, ties decide which duplicate survives dedupe, and that decides what a
person sees.

The fixture registry is shaped like production's: Postman second (it answers
404 on every sweep), and two boards publishing the same posting where the EARLIER
board is the slow one — so a later board finishes first, and any merge on
completion hands the win to the wrong board. Lever concurrency is on in the
end-to-end cases, because it is on in production.

NO NETWORK, NO ACTOR. Sockets are denied for the whole module; every response
is a stub or a fixture served through ats.get_json.
"""
import hashlib
import inspect
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import scraper                                   # noqa: E402
import telemetry                                 # noqa: E402
from sources import ats, concurrency, shadow     # noqa: E402
import sources as sources_pkg                    # noqa: E402
from sweep.tests.test_search_v2_shadow_eval import (  # noqa: E402
    EIGHT, RICH, SHADOW_ON, TODAY, _env, gh, lever, production_view, routes,
    sweep)

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def deny(*a, **kw):
        raise AssertionError("a Greenhouse concurrency test tried to open a socket")
    socket.socket.connect = deny
    socket.create_connection = deny


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


GH_ON, GH_OFF = concurrency.GREENHOUSE_FLAG, concurrency.GREENHOUSE_WORKERS_ENV
LEVER_PROD = {concurrency.FLAG: "1", concurrency.WORKERS_ENV: "4"}   # as production


def gh_env(workers=None, lever=None):
    """Greenhouse off (workers None) or on at `workers`; Lever as given."""
    env = {GH_ON: "0" if workers is None else "1",
           GH_OFF: None if workers is None else str(workers)}
    env.update(lever or {concurrency.FLAG: None, concurrency.WORKERS_ENV: None})
    return env


# ---------------------------------------------------------------------------
# Unit level: sources.fetch_free with ats.fetch stubbed
# ---------------------------------------------------------------------------
# Registry order groww, postman, acme, druva, acmemirror, slice. acme is slow and
# acmemirror fast, and both publish one posting, so at 2, 4 and 6 workers the
# later board finishes first.
GH = {"groww": "Groww", "postman": "Postman", "acme": "Acme Corp",
      "druva": "Druva", "acmemirror": "Acme Corp", "slice": "Slice"}
DELAY = {"groww": 0.10, "postman": 0.02, "acme": 0.30, "druva": 0.15,
         "acmemirror": 0.01, "slice": 0.05}
RETRIES = {"druva": 2, "slice": 1}


def job(title, company, source, url, desc="react node postgres"):
    return {"Title": title, "Company": company, "Location": "Remote",
            "Description": desc, "Posted Date": TODAY, "Source": source,
            "Job URL": url, "Salary": "", "Experience": "", "hires_home": ""}


def rows_for(token):
    company, source = GH.get(token, token.title()), f"greenhouse:{token}"
    if token in ("acme", "acmemirror"):
        return [job("Full Stack Engineer", company, source,
                    f"https://gh/{token}/dup", RICH)] + (
            [job("Backend Engineer", company, source, f"https://gh/{token}/2",
                 "python django")] if token == "acme" else [])
    return [job(f"Backend Engineer {token}", company, source,
                f"https://gh/{token}/1")]


class Stub:
    """ats.fetch, stubbed: sleeps, raises 404 for Postman, and reports counts
    and retries from INSIDE the worker the way the real adapter and _http do —
    distinct per board, so any cross-attribution shows as a wrong number."""

    def __init__(self, fail=("postman",), boards=GH, delay=DELAY):
        self.fail, self.boards, self.delay = set(fail), boards, delay
        self.calls, self.completions = [], []
        self.lock = threading.Lock()
        self.in_flight = {}
        self.max_in_flight = {}

    def __call__(self, platform, token, company, keep_title, keep_location,
                 is_home=None):
        with self.lock:
            self.calls.append(f"{platform}:{token}")
            self.in_flight[platform] = self.in_flight.get(platform, 0) + 1
            self.max_in_flight[platform] = max(self.max_in_flight.get(platform, 0),
                                               self.in_flight[platform])
        try:
            time.sleep(self.delay.get(token, 0))
            for _ in range(RETRIES.get(token, 0)):
                telemetry.retried()
            if token in self.fail:
                raise urllib.error.HTTPError(
                    f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                    404, "Not Found", None, None)
            got = rows_for(token) if platform == "greenhouse" else [
                job(f"Engineer {token}", company, f"{platform}:{token}",
                    f"https://{platform}/{token}")]
            i = list(self.boards).index(token) if token in self.boards else 0
            telemetry.observed(raw=10 + i, normalized=5 + i, gated=len(got),
                               requests=1)
            return got
        finally:
            with self.lock:
                self.in_flight[platform] -= 1
                self.completions.append(f"{platform}:{token}")


def acquire(env, stub=None, registry=None):
    """sources.fetch_free over `registry` (default: the Greenhouse fixture)."""
    stub = stub or Stub()
    real, logged = ats.fetch, []
    ats.fetch = stub
    try:
        with _env(**env):
            rows = sources_pkg.fetch_free(
                registry or {"greenhouse": dict(GH)}, {}, lambda t: True,
                lambda l: True, log=logged.append)
    finally:
        ats.fetch = real
    return rows, logged, stub


def recorded(env, stub=None, registry=None):
    """acquire() under an open telemetry record; returns the record too."""
    with tempfile.TemporaryDirectory() as tmp, _env(**{telemetry.FLAG: "1"}):
        telemetry.start("free", tmp)
        rows, logged, stub = acquire(env, stub, registry)
        snapshot = {k: v for k, v in telemetry.record().items() if k != "_t0"}
        telemetry.finish()
    return rows, logged, stub, snapshot


class FlagOff(unittest.TestCase):
    """1. Default off: the serial path, untouched."""

    def test_default_is_off(self):
        with _env(**{GH_ON: None}):
            self.assertFalse(concurrency.enabled("greenhouse"))
            self.assertFalse(concurrency.applies("greenhouse"))

    def test_no_deployment_file_turns_it_on(self):
        for path in ("render.yaml", "deploy/sweep_worker.py", "requirements.txt",
                     "gunicorn.conf.py"):
            with open(os.path.join(REPO, path), encoding="utf-8") as fh:
                body = fh.read()
            for flag in (GH_ON, GH_OFF):
                self.assertEqual(re.findall(
                    r"""\benv\[["']%(f)s["']\]\s*=|\bkey:\s*%(f)s\b|^\s*%(f)s\s*="""
                    % {"f": re.escape(flag)}, body, re.M), [], f"{path} sets {flag}")

    def test_serial_path_never_enters_the_executor(self):
        entered = []
        real = concurrency.fetch_boards
        concurrency.fetch_boards = lambda platform, *a, **kw: (
            entered.append(platform), real(platform, *a, **kw))[1]
        try:
            rows, _, stub = acquire(gh_env(None))
        finally:
            concurrency.fetch_boards = real
        self.assertEqual(entered, [])
        self.assertEqual(stub.max_in_flight["greenhouse"], 1)
        self.assertEqual(stub.calls, [f"greenhouse:{t}" for t in GH])
        self.assertEqual(stub.completions, stub.calls)


class WorkerConfig(unittest.TestCase):
    """9. Parsed safely, bounded both ways, independent of Lever's."""

    def test_default_and_clamp(self):
        with _env(**{GH_OFF: None}):
            self.assertEqual(concurrency.workers("greenhouse"), 4)
        for raw, want in (("1", 1), ("2", 2), (" 6 ", 6), ("8", 8), ("0", 1),
                          ("-3", 1), ("999", 8), ("banana", 4), ("", 4),
                          ("4.5", 4)):
            with _env(**{GH_OFF: raw}):
                self.assertEqual(concurrency.workers("greenhouse"), want, repr(raw))

    def test_bounded_at_the_executor_not_just_in_the_number(self):
        boards = {f"b{i}": f"Board {i}" for i in range(20)}
        stub = Stub(fail=(), boards=boards, delay={t: 0.03 for t in boards})
        acquire(gh_env(999), stub, {"greenhouse": boards})
        self.assertLessEqual(stub.max_in_flight["greenhouse"], concurrency.MAX_WORKERS)
        self.assertGreater(stub.max_in_flight["greenhouse"], 1)

    def test_never_more_in_flight_than_configured(self):
        for n in (1, 2, 4, 6):
            _, _, stub = acquire(gh_env(n))
            self.assertLessEqual(stub.max_in_flight["greenhouse"], n, n)


class Determinism(unittest.TestCase):
    """2-4. Same rows, same order, same survivor, at every worker count —
    with completion order genuinely inverted."""

    @classmethod
    def setUpClass(cls):
        cls.serial = acquire(gh_env(None))
        cls.arms = {n: acquire(gh_env(n)) for n in (1, 2, 4, 6)}

    def test_completion_order_really_is_inverted(self):
        for n in (2, 4, 6):
            done = self.arms[n][2].completions
            self.assertLess(done.index("greenhouse:acmemirror"),
                            done.index("greenhouse:acme"), n)

    def test_rows_identical_to_serial(self):
        for n, (rows, _, _) in self.arms.items():
            self.assertEqual(rows, self.serial[0], n)

    def test_log_lines_identical_to_serial(self):
        for n, (_, logged, _) in self.arms.items():
            self.assertEqual(logged, self.serial[1], n)

    def test_the_duplicate_really_is_one_posting(self):
        keys = {scraper.job_key(r) for t in ("acme", "acmemirror")
                for r in rows_for(t)[:1]}
        self.assertEqual(len(keys), 1, "the fixture stopped being a duplicate")

    def test_same_survivor_at_every_worker_count(self):
        def survivor(rows):
            final = scraper.finalize([dict(r) for r in rows])
            return [r["apply_url"] for r in final
                    if r["title"] == "Full Stack Engineer"]
        self.assertEqual(survivor(self.serial[0]), ["https://gh/acme/dup"])
        for n, (rows, _, _) in self.arms.items():
            self.assertEqual(survivor(rows), ["https://gh/acme/dup"], n)

    def test_the_worker_has_nothing_shared_to_append_to(self):
        """15. The worker's only way out is its return value."""
        params = list(inspect.signature(concurrency._fetch_one).parameters)
        self.assertEqual(params, ["platform", "token", "company", "keep_title",
                                  "keep_location", "is_home"])
        source = inspect.getsource(concurrency._fetch_one)
        self.assertNotIn(".extend(", source)
        self.assertNotIn(".append(", source)


class FailureAndPostman(unittest.TestCase):
    """5, 6. Postman's 404 stays Postman's, exactly as serial."""

    def test_postman_404_attributed_identically_serial_and_concurrent(self):
        views = {}
        for name, env in (("serial", gh_env(None)), ("w4", gh_env(4))):
            rows, logged, _, rec = recorded(env)
            units = {u["board"]: u for u in rec["units"]}
            views[name] = (rows, logged, [(u["board"], u["ok"], u["failure_category"])
                                          for u in rec["units"]],
                           {k: rec[k] for k in ("sources_attempted",
                                                "sources_succeeded", "sources_failed")})
            self.assertFalse(units["greenhouse:postman"]["ok"])
            self.assertEqual(units["greenhouse:postman"]["failure_category"], "http_404")
            self.assertEqual(sum(not u["ok"] for u in rec["units"]), 1)
        self.assertEqual(views["w4"], views["serial"])
        self.assertEqual(views["w4"][3], {"sources_attempted": 6,
                                          "sources_succeeded": 5, "sources_failed": 1})
        self.assertIn(f"  {'greenhouse':<16} {'Postman':<22} ! HTTP Error 404: "
                      f"Not Found", views["w4"][1])

    def test_siblings_are_neither_cancelled_nor_lost(self):
        rows, _, stub = acquire(gh_env(4))
        self.assertEqual(sorted(stub.calls), sorted(f"greenhouse:{t}" for t in GH))
        self.assertEqual({r["Source"] for r in rows},
                         {f"greenhouse:{t}" for t in GH if t != "postman"})

    def test_every_board_failing_returns_cleanly(self):
        rows, logged, _ = acquire(gh_env(4), Stub(fail=GH))
        self.assertEqual(rows, [])
        self.assertEqual(len(logged), len(GH))


class TelemetryUnderGreenhouseConcurrency(unittest.TestCase):
    """7, 8. One unit per board, in registry order, each with its own numbers."""

    @classmethod
    def setUpClass(cls):
        cls.serial = recorded(gh_env(None))[3]
        cls.conc = recorded(gh_env(6))[3]

    def test_units_in_registry_order_one_per_board(self):
        self.assertEqual([u["board"] for u in self.conc["units"]],
                         [f"greenhouse:{t}" for t in GH])

    def test_counts_retries_and_failure_land_on_the_right_board(self):
        for i, (token, unit) in enumerate(zip(GH, self.conc["units"])):
            self.assertEqual(unit["retries"], RETRIES.get(token, 0), token)
            self.assertEqual(unit["family"], "greenhouse")
            if token == "postman":
                self.assertEqual((unit["raw_count"], unit["requests"]), (0, 0))
                continue
            self.assertEqual((unit["raw_count"], unit["normalized_count"],
                              unit["source_gate_count"], unit["requests"]),
                             (10 + i, 5 + i, len(rows_for(token)), 1), token)

    def test_everything_but_the_clock_matches_serial(self):
        keep = ("board", "ok", "failure_category", "requests", "retries",
                "raw_count", "normalized_count", "source_gate_count", "shadow")
        strip = lambda rec: [{k: u[k] for k in keep} for u in rec["units"]]  # noqa: E731
        self.assertEqual(strip(self.conc), strip(self.serial))

    def test_workers_never_attach_to_the_shared_record(self):
        """Only the coordinator attaches units, after every future is done.
        A worker that attached its own — at start or on completion — would be
        racing its siblings for the order of the units list."""
        seen = []

        class Peek(Stub):
            def __call__(self, *a, **kw):
                seen.append(len(telemetry.record()["units"]))
                return super().__call__(*a, **kw)
        recorded(gh_env(6), Peek())
        self.assertEqual(len(seen), len(GH))
        self.assertEqual(set(seen), {0}, "a worker attached a unit to the run")

    def test_durations_are_per_board(self):
        ms = {u["board"]: u["duration_ms"] for u in self.conc["units"]}
        self.assertGreater(ms["greenhouse:acme"], 250)
        self.assertLess(ms["greenhouse:acmemirror"], 150)

    def test_native_ids_stay_on_their_board_through_the_real_adapter(self):
        """The real ats.fetch under 4 threads: its own telemetry.observed and
        its own `_native` capture, with only the HTTP answer stubbed."""
        bodies = {t: {"jobs": [gh(t, 9000 + 10 * i + k, f"Engineer {t} {k}",
                                  "react node") for k in range(i + 1)]}
                  for i, t in enumerate(GH) if t != "postman"}
        table = {ats.ATS["greenhouse"]["url"].format(token=t): (t, b)
                 for t, b in bodies.items()}
        real = ats.get_json

        def fake(url, *a, **kw):
            if url not in table:
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
            token, body = table[url]
            time.sleep(DELAY[token])
            return body
        ats.get_json = fake
        try:
            with tempfile.TemporaryDirectory() as tmp, \
                    _env(**dict(gh_env(4), **{telemetry.FLAG: "1"})):
                telemetry.start("free", tmp)
                rows = sources_pkg.fetch_free({"greenhouse": dict(GH)}, {},
                                              lambda t: True, lambda l: True,
                                              log=lambda *a: None)
                units = [dict(u) for u in telemetry.record()["units"]]
                telemetry.finish()
        finally:
            ats.get_json = real
        for row in rows:
            token = row["Source"].split(":")[1]
            ids = {str(j["id"]) for j in bodies[token]["jobs"]}
            self.assertIn(row["_native"]["native_id"], ids, token)
        for unit in units:
            token = unit["board"].split(":")[1]
            n = len(bodies[token]["jobs"]) if token in bodies else 0
            self.assertEqual((unit["raw_count"], unit["source_gate_count"]),
                             (n, n), token)


class LeverIndependence(unittest.TestCase):
    """10. Greenhouse's switch cannot move Lever's executor or settings."""

    REG = {"lever": {"l1": "L1", "l2": "L2", "l3": "L3"}, "greenhouse": dict(GH)}
    LDELAY = dict(DELAY, l1=0.05, l2=0.05, l3=0.05)

    def test_greenhouse_on_leaves_lever_serial(self):
        stub = Stub(delay=self.LDELAY)
        acquire(gh_env(4), stub, self.REG)
        self.assertEqual(stub.max_in_flight["lever"], 1)
        self.assertGreater(stub.max_in_flight["greenhouse"], 1)

    def test_worker_counts_are_independent(self):
        with _env(**{concurrency.WORKERS_ENV: "3", GH_OFF: "6"}):
            self.assertEqual((concurrency.workers("lever"),
                              concurrency.workers("greenhouse")), (3, 6))
            self.assertEqual(concurrency.workers(), 3)       # V2-B2's call shape
        with _env(**{concurrency.FLAG: None, GH_ON: "1"}):
            self.assertFalse(concurrency.applies("lever"))
            self.assertFalse(concurrency.enabled())
        with _env(**{concurrency.FLAG: "1", GH_ON: None}):
            self.assertFalse(concurrency.applies("greenhouse"))

    def test_each_executor_is_sized_by_its_own_setting(self):
        sized = {}
        real = concurrency.ThreadPoolExecutor

        def spy(max_workers, thread_name_prefix):
            sized[thread_name_prefix] = max_workers
            return real(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
        concurrency.ThreadPoolExecutor = spy
        try:
            acquire(gh_env(2, LEVER_PROD), Stub(delay=self.LDELAY), self.REG)
        finally:
            concurrency.ThreadPoolExecutor = real
        self.assertEqual(sized, {"sweep-lever": 4, "sweep-greenhouse": 2})

    def test_lever_rows_unchanged_by_the_greenhouse_switch(self):
        off = acquire(gh_env(None, LEVER_PROD), Stub(delay=self.LDELAY), self.REG)[0]
        on = acquire(gh_env(6, LEVER_PROD), Stub(delay=self.LDELAY), self.REG)[0]
        self.assertEqual(on, off)


# ---------------------------------------------------------------------------
# End to end: scraper.main(), real adapters, fixture HTTP
# ---------------------------------------------------------------------------
def ashby(jid, title, desc):
    return {"id": jid, "title": title, "location": "Remote",
            "jobUrl": f"https://jobs.ashbyhq.com/acmeashby/{jid}",
            "publishedAt": f"{TODAY}T00:00:00Z", "descriptionPlain": desc,
            "isRemote": False}


# Three Acme postings, each published twice, so arrival order is what decides
# every survivor: inside Greenhouse (acme early and slow, acmemirror late and
# fast), Greenhouse against the Ashby block after it, and the Lever block before
# it against Greenhouse.
E2E_REGISTRY = {
    "lever": {"lv1": "Lever One", "acmelever": "Acme Corp"},
    "greenhouse": {"gh1": "Gh One", "postman": "Postman", "acme": "Acme Corp",
                   "gh3": "Gh Three", "acmemirror": "Acme Corp", "gh4": "Gh Four"},
    "ashby": {"acmeashby": "Acme Corp"},
}
E2E_BODIES = {
    "lever:lv1": [lever("lv1a", "Backend Engineer", "python django")],
    "lever:acmelever": [lever("lvacme", "Frontend Engineer", "react typescript")],
    "greenhouse:gh1": {"jobs": [gh("gh1", 1101, "Full Stack Developer",
                                   "react node postgres")]},
    "greenhouse:postman": {"jobs": []},
    "greenhouse:acme": {"jobs": [
        gh("acme", 1201, "Full Stack Engineer", RICH),
        gh("acme", 1202, "Platform Engineer", "python django react")]},
    "greenhouse:gh3": {"jobs": [gh("gh3", 1301, "Backend Engineer", "python django")]},
    "greenhouse:acmemirror": {"jobs": [
        gh("acmemirror", 1401, "Full Stack Engineer", RICH),
        gh("acmemirror", 1402, "Frontend Engineer", "react typescript")]},
    "greenhouse:gh4": {"jobs": [gh("gh4", 1501, "Software Engineer", "java spring")]},
    "ashby:acmeashby": {"jobs": [ashby("a1", "Platform Engineer", "python django react")]},
}
E2E_DELAY = {"lever:lv1": 0.03, "lever:acmelever": 0.01, "greenhouse:gh1": 0.05,
             "greenhouse:postman": 0.02, "greenhouse:acme": 0.30,
             "greenhouse:gh3": 0.08, "greenhouse:acmemirror": 0.01,
             "greenhouse:gh4": 0.04}


def e2e(workers, extra=None):
    done = []
    got = sweep(dict(gh_env(workers, LEVER_PROD), **(extra or {})),
                fail={"greenhouse:postman"}, status=404, done=done,
                registry=E2E_REGISTRY, delay=E2E_DELAY,
                table=routes(production=E2E_BODIES))
    return got, done


def survivor(result, title):
    import json
    return [r["apply_url"] for r in json.loads(result.json)
            if r["company"] == "Acme Corp" and r["title"] == title]


class ExportParity(unittest.TestCase):
    """3, 4, 14. Through scraper.main to the bytes a user downloads."""

    @classmethod
    def setUpClass(cls):
        cls.serial, cls.serial_done = e2e(None)
        cls.arms = {n: e2e(n) for n in (1, 2, 4, 6)}

    def test_completion_order_inverted_inside_greenhouse(self):
        for n in (2, 4, 6):
            done = self.arms[n][1]
            self.assertLess(done.index("greenhouse:acmemirror"),
                            done.index("greenhouse:acme"), n)
        self.assertLess(self.serial_done.index("greenhouse:acme"),
                        self.serial_done.index("greenhouse:acmemirror"))

    def test_csv_json_and_seen_ledger_byte_identical(self):
        self.assertTrue(self.serial.csv)
        for n, (got, _) in self.arms.items():
            self.assertEqual(got.csv, self.serial.csv, n)
            self.assertEqual(got.json, self.serial.json, n)
            self.assertEqual(got.seen, self.serial.seen, n)

    def test_every_survivor_is_the_serial_one(self):
        want = {"Full Stack Engineer": ["https://job-boards.greenhouse.io/acme/jobs/1201"],
                "Platform Engineer": ["https://job-boards.greenhouse.io/acme/jobs/1202"],
                "Frontend Engineer": ["https://jobs.lever.co/x/lvacme"]}
        for title, url in want.items():
            self.assertEqual(survivor(self.serial, title), url, title)
            for n, (got, _) in self.arms.items():
                self.assertEqual(survivor(got, title), url, (n, title))

    def test_the_twins_really_tie_so_order_is_what_decides(self):
        rows = {(r["Source"], r["Title"]): r for r in scraper.score_and_filter(
            [scraper._truncate_desc(dict(r)) for r in _fixture_rows()],
            lambda *a: None)[0]}
        pairs = [(("greenhouse:acme", "Full Stack Engineer"),
                  ("greenhouse:acmemirror", "Full Stack Engineer")),
                 (("greenhouse:acme", "Platform Engineer"),
                  ("ashby:acmeashby", "Platform Engineer")),
                 (("lever:acmelever", "Frontend Engineer"),
                  ("greenhouse:acmemirror", "Frontend Engineer"))]
        for a, b in pairs:
            self.assertEqual(rows[a]["score"], rows[b]["score"], (a, b))

    def test_production_telemetry_identical_including_postman(self):
        base = production_view(self.serial.telemetry)
        for n, (got, _) in self.arms.items():
            self.assertEqual(production_view(got.telemetry), base, n)
        units = {u["board"]: u for u in self.serial.telemetry["units"]}
        self.assertEqual(units["greenhouse:postman"]["failure_category"], "http_404")
        self.assertEqual(self.serial.telemetry["sources_failed"], 1)
        self.assertEqual(self.serial.telemetry["sources_attempted"], 9)


def _fixture_rows():
    """The end-to-end registry's acquired rows, fetched serially offline."""
    from sweep.tests.test_search_v2_shadow_eval import _served
    out = []
    with _served(routes(production=E2E_BODIES)):
        for platform, boards in E2E_REGISTRY.items():
            for token, company in boards.items():
                out += ats.fetch(platform, token, company, scraper.is_dev_title,
                                 scraper.location_allowed, scraper.is_home_location)
    return out


class ShadowIndependence(unittest.TestCase):
    """11. The B3 shadow boards never enter the Greenhouse executor and stay
    serial, after the results, with production counts untouched."""

    @classmethod
    def setUpClass(cls):
        cls.entered = []
        real = concurrency._fetch_one

        def spy(platform, token, *a, **kw):
            cls.entered.append(f"{platform}:{token}")
            return real(platform, token, *a, **kw)
        concurrency._fetch_one = spy
        try:
            cls.on, _ = e2e(4, SHADOW_ON)
        finally:
            concurrency._fetch_one = real
        cls.off, _ = e2e(4)

    def test_only_production_boards_enter_the_executor(self):
        production = {f"{p}:{t}" for p in ("lever", "greenhouse")
                      for t in E2E_REGISTRY[p]}
        self.assertEqual(set(self.entered), production)
        self.assertFalse(set(self.entered) & set(EIGHT))

    def test_shadow_still_ran_all_eight_one_at_a_time(self):
        units = self.on.telemetry["shadow_units"]
        self.assertEqual([u["board"] for u in units], EIGHT)
        for before, after in zip(units, units[1:]):
            self.assertLessEqual(before["finished_at"], after["started_at"])

    def test_production_counts_and_output_untouched_by_shadow(self):
        self.assertEqual(production_view(self.on.telemetry),
                         production_view(self.off.telemetry))
        self.assertEqual(self.on.csv, self.off.csv)
        self.assertEqual(self.on.telemetry["sources_attempted"], 9)


class RegistryUntouched(unittest.TestCase):
    """12. The production registry this stage must not move."""

    # sha256 of the 54 Greenhouse tokens in registry order, taken 2026-09-23.
    GREENHOUSE_ORDER_SHA256 = (
        "9ab5cfe2362e879c0f766ba3962c7b0275fc7ee50867064e3c075bfd7e25aedc")

    def test_counts(self):
        import config
        self.assertEqual(sum(len(b) for b in config.ATS_BOARDS.values()), 129)
        self.assertEqual(len(config.ATS_BOARDS["greenhouse"]), 54)
        self.assertEqual(sum(1 for c in config.FEEDS.values() if c.get("enabled")), 5)
        self.assertEqual(list(config.ATS_BOARDS),
                         ["lever", "greenhouse", "ashby", "smartrecruiters", "breezy"])

    def test_greenhouse_boards_neither_added_removed_nor_reordered(self):
        import config
        tokens = list(config.ATS_BOARDS["greenhouse"])
        self.assertEqual(hashlib.sha256("\n".join(tokens).encode()).hexdigest(),
                         self.GREENHOUSE_ORDER_SHA256)
        self.assertIn("postman", tokens)       # the known 404 stays in the baseline
        self.assertFalse(set(tokens) & set(shadow.BOARDS["greenhouse"]))


class PaidIsolation(unittest.TestCase):
    """13. The benchmark cannot reach a paid path, and neither can this one."""

    def test_the_benchmark_never_runs_the_engine_entry_point(self):
        path = os.path.join(REPO, "bench", "search_v2_free_concurrency.py")
        with open(path, encoding="utf-8") as fh:
            code = fh.read()
        for banned in ("scrape_search", "scraper.main", '"scraper.py"',
                       "--profile", "ApifyClient", "apify_client"):
            self.assertNotIn(banned, code)

    def test_the_concurrent_path_is_not_reachable_from_the_paid_path(self):
        body = inspect.getsource(scraper.scrape_search)
        for banned in ("concurrency", "ThreadPoolExecutor", "submit("):
            self.assertNotIn(banned, body)


if __name__ == "__main__":
    unittest.main()
