"""V2-B2: bounded Lever concurrency must be invisible to everything downstream.

One property matters more than the speed-up and every test here exists to pin
it: **completion order must never become result order.** Sweep's row order
decides score ties, and score ties decide which duplicate survives dedupe, which
decides what a person actually sees. A faster board finishing first must change
nothing but the clock.

Stubs, not the network: `ats.fetch` is replaced by a fake with per-board sleeps
chosen so completion order is deliberately the reverse of registry order. That
makes the ordering property testable deterministically instead of hopefully.

NO NETWORK, NO ACTOR. Sockets are denied for the whole module.
"""
import csv
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import scraper                                   # noqa: E402
import telemetry                                 # noqa: E402
from sources import ats, concurrency             # noqa: E402
import sources as sources_pkg                    # noqa: E402

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def deny(*a, **kw):
        raise AssertionError("a concurrency test tried to open a socket")
    socket.socket.connect = deny
    socket.create_connection = deny


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


TODAY = datetime.now().strftime("%Y-%m-%d")

# token -> (seconds to "fetch", rows). The sleeps are the point: registry order
# is a,b,c,d and completion order is d,b,c,a, so any implementation that merges
# on completion produces a visibly different sequence.
BOARDS = {"a": "Alpha", "b": "Beta", "c": "Gamma", "d": "Delta"}
DELAY = {"a": 0.20, "b": 0.05, "c": 0.12, "d": 0.01}


def row(title, company, source, url, desc="react node postgres",
        location="Remote"):
    return {"Title": title, "Company": company, "Location": location,
            "Description": desc, "Posted Date": TODAY, "Source": source,
            "Job URL": url, "Salary": "", "Experience": "", "hires_home": ""}


def board_rows(token):
    """Two rows per board, so row order within a board is observable too."""
    company = BOARDS[token]
    return [row(f"Backend Engineer {token}{i}", company, f"lever:{token}",
                f"https://lever/{token}/{i}") for i in (1, 2)]


class Recorder:
    """A stubbed ats.fetch that records call order and peak concurrency."""

    def __init__(self, rows_for=board_rows, delay=None, fail=()):
        self.rows_for = rows_for
        self.delay = delay if delay is not None else DELAY
        self.fail = set(fail)
        self.calls = []                 # order fetch() was ENTERED
        self.completions = []           # order fetch() RETURNED
        self.lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def __call__(self, platform, token, company, keep_title, keep_location,
                 is_home=None):
        with self.lock:
            self.calls.append(token)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(self.delay.get(token, 0))
            if token in self.fail:
                raise RuntimeError(f"board {token} exploded")
            got = list(self.rows_for(token))
            # The real ats.fetch reports its counts to the OPEN UNIT from
            # inside the worker. Mimicking that is the point: if the open unit
            # were a module global rather than thread-local, these counts would
            # land on whichever board was current, and the per-board assertions
            # in TelemetryUnderConcurrency would see the cross-attribution.
            telemetry.observed(raw=len(got), normalized=len(got),
                               gated=len(got), requests=1)
            return got
        finally:
            with self.lock:
                self.in_flight -= 1
                self.completions.append(token)


class _env:
    def __init__(self, **kv):
        self.kv = kv

    def __enter__(self):
        self.was = {k: os.environ.get(k) for k in self.kv}
        for k, v in self.kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.was.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def run_fetch_free(recorder, boards=None, **env):
    """sources.fetch_free over one Lever registry, with ats.fetch stubbed."""
    real = ats.fetch
    ats.fetch = recorder
    logged = []
    try:
        with _env(**env):
            rows = sources_pkg.fetch_free(
                {"lever": boards if boards is not None else dict(BOARDS)},
                {}, lambda t: True, lambda l: True,
                log=logged.append)
    finally:
        ats.fetch = real
    return rows, logged


# ---------------------------------------------------------------------------
class FlagOff(unittest.TestCase):
    """1. Default off: the serial path, untouched."""

    def test_default_is_off(self):
        with _env(**{concurrency.FLAG: None}):
            self.assertFalse(concurrency.enabled())
            self.assertFalse(concurrency.applies("lever"))

    def test_no_deployment_file_turns_it_on(self):
        import re
        for path in ("render.yaml", "deploy/sweep_worker.py", "requirements.txt"):
            body = open(os.path.join(REPO, path), encoding="utf-8").read()
            for flag in (concurrency.FLAG, concurrency.WORKERS_ENV):
                self.assertEqual(
                    re.findall(r"""\benv\[["']%s["']\]\s*=|\bkey:\s*%s\b"""
                               % (re.escape(flag), re.escape(flag)), body),
                    [], f"{path} sets {flag}")

    def test_serial_call_order_and_row_order(self):
        rec = Recorder()
        rows, _ = run_fetch_free(rec, **{concurrency.FLAG: "0"})
        self.assertEqual(rec.calls, ["a", "b", "c", "d"])
        self.assertEqual(rec.completions, ["a", "b", "c", "d"])
        self.assertEqual([r["Source"] for r in rows],
                         ["lever:a"] * 2 + ["lever:b"] * 2
                         + ["lever:c"] * 2 + ["lever:d"] * 2)

    def test_never_enters_the_executor(self):
        rec = Recorder()
        with _env(**{concurrency.FLAG: "0"}):
            self.assertFalse(concurrency.applies("lever"))
        rows, _ = run_fetch_free(rec, **{concurrency.FLAG: "0"})
        self.assertEqual(rec.max_in_flight, 1)


class WorkerConfig(unittest.TestCase):
    def test_default_workers(self):
        with _env(**{concurrency.WORKERS_ENV: None}):
            self.assertEqual(concurrency.workers(), 4)

    def test_clamped(self):
        for raw, want in (("2", 2), ("3", 3), ("4", 4), ("1", 1),
                          ("0", 1), ("-9", 1), ("21", 8), ("999", 8)):
            with _env(**{concurrency.WORKERS_ENV: raw}):
                self.assertEqual(concurrency.workers(), want, raw)

    def test_nonsense_falls_back_rather_than_crashing(self):
        for raw in ("banana", "4.5", " "):
            with _env(**{concurrency.WORKERS_ENV: raw}):
                self.assertEqual(concurrency.workers(), 4, raw)

    def test_never_unbounded(self):
        with _env(**{concurrency.WORKERS_ENV: "10000"}):
            self.assertLessEqual(concurrency.workers(), concurrency.MAX_WORKERS)


class DeterministicOrder(unittest.TestCase):
    """2, 3. The property the whole design exists for."""

    def serial_rows(self):
        return run_fetch_free(Recorder(), **{concurrency.FLAG: "0"})[0]

    def test_workers_1_matches_serial(self):
        rec = Recorder()
        rows, _ = run_fetch_free(rec, **{concurrency.FLAG: "1",
                                         concurrency.WORKERS_ENV: "1"})
        self.assertEqual(rows, self.serial_rows())
        self.assertEqual(rec.max_in_flight, 1, "workers=1 ran things in parallel")

    def test_completion_order_really_is_out_of_order(self):
        """Guards the guard: a test for out-of-order merging is worthless if
        the stub happens to complete in registry order."""
        rec = Recorder()
        run_fetch_free(rec, **{concurrency.FLAG: "1",
                               concurrency.WORKERS_ENV: "4"})
        self.assertEqual(rec.completions, ["d", "b", "c", "a"])
        self.assertNotEqual(rec.completions, ["a", "b", "c", "d"])

    def test_rows_merge_in_registry_order_not_completion_order(self):
        for n in ("2", "3", "4"):
            rows, _ = run_fetch_free(Recorder(), **{concurrency.FLAG: "1",
                                                    concurrency.WORKERS_ENV: n})
            self.assertEqual([r["Source"] for r in rows],
                             ["lever:a"] * 2 + ["lever:b"] * 2
                             + ["lever:c"] * 2 + ["lever:d"] * 2, n)

    def test_concurrent_rows_are_identical_to_serial(self):
        serial = self.serial_rows()
        for n in ("1", "2", "3", "4"):
            rows, _ = run_fetch_free(Recorder(), **{concurrency.FLAG: "1",
                                                    concurrency.WORKERS_ENV: n})
            self.assertEqual(rows, serial, f"workers={n}")

    def test_log_lines_follow_registry_order(self):
        _, logged = run_fetch_free(Recorder(), **{concurrency.FLAG: "1",
                                                  concurrency.WORKERS_ENV: "4"})
        self.assertEqual([l.split()[1] for l in logged],
                         ["Alpha", "Beta", "Gamma", "Delta"])


class MaxInFlight(unittest.TestCase):
    """7. Bounded, and bounded by the configured number."""

    def test_never_exceeds_configured_workers(self):
        for n in (1, 2, 3, 4):
            rec = Recorder(delay={t: 0.05 for t in BOARDS})
            run_fetch_free(rec, **{concurrency.FLAG: "1",
                                   concurrency.WORKERS_ENV: str(n)})
            self.assertLessEqual(rec.max_in_flight, n, f"workers={n}")

    def test_more_boards_than_workers_still_bounded(self):
        boards = {f"b{i}": f"Co{i}" for i in range(21)}
        rec = Recorder(rows_for=lambda t: [], delay={t: 0.02 for t in boards})
        run_fetch_free(rec, boards=boards, **{concurrency.FLAG: "1",
                                              concurrency.WORKERS_ENV: "4"})
        self.assertLessEqual(rec.max_in_flight, 4)
        self.assertEqual(len(rec.calls), 21, "a board was skipped")

    def test_out_of_range_worker_count_is_still_bounded_in_flight(self):
        """The clamp has to hold at the EXECUTOR, not just in workers().

        21 boards and SWEEP_FREE_LEVER_WORKERS=999 is the typo this guards: a
        bound that exists only as a returned integer is not a bound.
        """
        boards = {f"b{i}": f"Co{i}" for i in range(21)}
        rec = Recorder(rows_for=lambda t: [], delay={t: 0.05 for t in boards})
        run_fetch_free(rec, boards=boards, **{concurrency.FLAG: "1",
                                              concurrency.WORKERS_ENV: "999"})
        self.assertLessEqual(rec.max_in_flight, concurrency.MAX_WORKERS,
                             "an unbounded pool hit the provider")
        self.assertEqual(len(rec.calls), 21)

    def test_concurrency_actually_happens(self):
        """Otherwise 'bounded' would be trivially satisfied by staying serial."""
        rec = Recorder(delay={t: 0.05 for t in BOARDS})
        run_fetch_free(rec, **{concurrency.FLAG: "1",
                               concurrency.WORKERS_ENV: "4"})
        self.assertGreater(rec.max_in_flight, 1)


class ProviderScope(unittest.TestCase):
    """8, 9. Lever only; nothing paid can reach this."""

    def test_only_lever_applies(self):
        with _env(**{concurrency.FLAG: "1"}):
            self.assertTrue(concurrency.applies("lever"))
            for other in ("greenhouse", "ashby", "smartrecruiters", "breezy",
                          "feed", "optum", "enterprise"):
                self.assertFalse(concurrency.applies(other), other)

    def test_paid_sites_can_never_apply(self):
        with _env(**{concurrency.FLAG: "1"}):
            for paid in ("linkedin", "indeed", "naukri"):
                self.assertFalse(concurrency.applies(paid), paid)
        self.assertEqual(set(concurrency.PROVIDERS) & {"linkedin", "indeed",
                                                       "naukri"}, set())

    def test_the_paid_path_does_not_reach_the_executor(self):
        """scrape_search is the whole paid acquisition path. It must not even
        mention the module, let alone submit to a pool."""
        import inspect
        body = inspect.getsource(scraper.scrape_search)
        for banned in ("concurrency", "ThreadPoolExecutor", "submit("):
            self.assertNotIn(banned, body)

    def test_other_providers_stay_serial_in_the_same_sweep(self):
        rec = Recorder(delay={t: 0.05 for t in BOARDS})
        real = ats.fetch
        ats.fetch = rec
        try:
            with _env(**{concurrency.FLAG: "1", concurrency.WORKERS_ENV: "4"}):
                sources_pkg.fetch_free(
                    {"greenhouse": dict(BOARDS)}, {},
                    lambda t: True, lambda l: True, log=lambda *a: None)
        finally:
            ats.fetch = real
        self.assertEqual(rec.max_in_flight, 1, "greenhouse went concurrent")
        self.assertEqual(rec.calls, ["a", "b", "c", "d"])


class FailureIsolation(unittest.TestCase):
    """6. One board raising costs that board and nothing else."""

    def test_others_survive_and_order_holds(self):
        rec = Recorder(fail={"a", "c"})
        rows, logged = run_fetch_free(rec, **{concurrency.FLAG: "1",
                                              concurrency.WORKERS_ENV: "4"})
        self.assertEqual([r["Source"] for r in rows],
                         ["lever:b"] * 2 + ["lever:d"] * 2)
        self.assertEqual(len(rec.calls), 4, "a failure stopped later boards")

    def test_failure_recorded_exactly_once(self):
        rec = Recorder(fail={"a"})
        _, logged = run_fetch_free(rec, **{concurrency.FLAG: "1",
                                           concurrency.WORKERS_ENV: "4"})
        self.assertEqual(sum(1 for l in logged if "exploded" in l), 1)

    def test_matches_serial_failure_behaviour(self):
        serial, serial_log = run_fetch_free(Recorder(fail={"a", "c"}),
                                            **{concurrency.FLAG: "0"})
        conc, conc_log = run_fetch_free(Recorder(fail={"a", "c"}),
                                        **{concurrency.FLAG: "1",
                                           concurrency.WORKERS_ENV: "4"})
        self.assertEqual(conc, serial)
        self.assertEqual(conc_log, serial_log)

    def test_every_board_failing_still_returns_cleanly(self):
        rows, logged = run_fetch_free(Recorder(fail=set(BOARDS)),
                                      **{concurrency.FLAG: "1",
                                         concurrency.WORKERS_ENV: "4"})
        self.assertEqual(rows, [])
        self.assertEqual(len(logged), 4)


class DuplicateSurvivor(unittest.TestCase):
    """5. The test this whole task hinges on.

    Two boards publish the SAME posting: same company, same title, so
    scraper.job_key collapses them. They differ only by apply URL, which is what
    makes the survivor observable. The duplicate lives on board `d`, which
    finishes FIRST, and on board `a`, which finishes LAST — so merging on
    completion would hand the win to `d`, and merging on registry order keeps
    `a`. Scores are equal, so nothing but arrival order decides.
    """

    DUP = {"a": "Alpha", "d": "Delta"}

    def rows_for(self, token):
        return [row("Senior Backend Engineer", "Acme Corp", f"lever:{token}",
                    f"https://lever/{token}/dup")]

    def survivor(self, flag, workers="4"):
        rec = Recorder(rows_for=self.rows_for,
                       delay={"a": 0.20, "d": 0.01})
        rows, _ = run_fetch_free(rec, boards=dict(self.DUP),
                                 **{concurrency.FLAG: flag,
                                    concurrency.WORKERS_ENV: workers})
        final = scraper.finalize([dict(r) for r in rows])
        return rows, final, rec

    def test_the_two_rows_really_are_one_posting_to_dedupe(self):
        keys = {scraper.job_key(r) for r in self.rows_for("a") + self.rows_for("d")}
        self.assertEqual(len(keys), 1, "the fixture stopped being a duplicate")

    def test_completion_order_is_reversed(self):
        _, _, rec = self.survivor("1")
        self.assertEqual(rec.completions, ["d", "a"])

    def test_same_survivor_serial_and_concurrent(self):
        serial_rows, serial_final, _ = self.survivor("0")
        conc_rows, conc_final, _ = self.survivor("1")
        self.assertEqual(len(serial_final), 1, "fixture did not dedupe")
        self.assertEqual(conc_rows, serial_rows)
        self.assertEqual(conc_final, serial_final)
        self.assertEqual(conc_final[0]["apply_url"],
                         "https://lever/a/dup",
                         "the board that finished first won the tie")

    def test_survivor_is_stable_across_worker_counts(self):
        want = self.survivor("0")[1][0]["apply_url"]
        for n in ("1", "2", "3", "4"):
            self.assertEqual(self.survivor("1", n)[1][0]["apply_url"], want, n)


class DownstreamParity(unittest.TestCase):
    """4. Through the real finalize, to real CSV and JSON bytes."""

    def finalized(self, flag):
        rows, _ = run_fetch_free(Recorder(), **{concurrency.FLAG: flag,
                                                concurrency.WORKERS_ENV: "4"})
        return scraper.finalize([dict(r) for r in rows])

    def test_final_rows_identical(self):
        serial, conc = self.finalized("0"), self.finalized("1")
        self.assertTrue(serial, "fixture produced no final rows")
        self.assertEqual(conc, serial)

    def test_scores_and_order_identical(self):
        serial, conc = self.finalized("0"), self.finalized("1")
        self.assertEqual([r["score"] for r in conc], [r["score"] for r in serial])
        self.assertEqual([r["apply_url"] for r in conc],
                         [r["apply_url"] for r in serial])

    def test_written_csv_and_json_are_byte_identical(self):
        out = {}
        for flag in ("0", "1"):
            rows = self.finalized(flag)
            with tempfile.TemporaryDirectory() as tmp:
                csv_path = os.path.join(tmp, "jobs.csv")
                json_path = os.path.join(tmp, "jobs.json")
                real_dir = scraper.SETTINGS["output_dir"]
                scraper.SETTINGS["output_dir"] = tmp
                try:
                    scraper.write_outputs(rows, csv_path, json_path)
                    out[flag] = (open(csv_path, "rb").read(),
                                 open(json_path, "rb").read())
                finally:
                    scraper.SETTINGS["output_dir"] = real_dir
        self.assertEqual(out["1"][0], out["0"][0], "CSV differs")
        self.assertEqual(out["1"][1], out["0"][1], "JSON differs")
        self.assertGreater(len(out["0"][1]), 2)


class TelemetryUnderConcurrency(unittest.TestCase):
    """10. Telemetry is ON in production, so this is the highest-risk surface."""

    def capture(self, flag, workers="4", fail=()):
        rec = Recorder(fail=fail)
        with tempfile.TemporaryDirectory() as tmp, \
                _env(**{telemetry.FLAG: "1"}):
            telemetry.start("free", tmp)
            rows, _ = run_fetch_free(rec, **{concurrency.FLAG: flag,
                                             concurrency.WORKERS_ENV: workers})
            record = telemetry.record()
            snapshot = json.loads(json.dumps(record, default=str))
            path = telemetry.finish()
            with open(path, encoding="utf-8") as fh:
                written = json.load(fh)
        return rows, snapshot, written, rec

    def test_exactly_one_unit_per_board_no_duplicates_no_losses(self):
        _, rec_snap, _, _ = self.capture("1")
        boards = [u["board"] for u in rec_snap["units"]]
        self.assertEqual(len(boards), 4)
        self.assertEqual(len(set(boards)), 4)
        self.assertEqual(set(boards),
                         {f"lever:{t}" for t in BOARDS})

    def test_units_appear_in_registry_order(self):
        _, rec_snap, _, _ = self.capture("1")
        self.assertEqual([u["board"] for u in rec_snap["units"]],
                         ["lever:a", "lever:b", "lever:c", "lever:d"])

    def test_counts_land_on_the_right_board(self):
        """The bug a module-global 'current unit' would cause: board A's rows
        recorded against board B. Each stub board yields 2 rows, so any
        cross-attribution shows up as a count that is not 2."""
        _, rec_snap, _, _ = self.capture("1")
        for unit in rec_snap["units"]:
            self.assertEqual(unit["source_gate_count"], 2, unit["board"])
            self.assertEqual(unit["raw_count"], 2, unit["board"])
            self.assertEqual(unit["requests"], 1, unit["board"])
            self.assertTrue(unit["ok"], unit["board"])
            self.assertEqual(unit["family"], "lever")

    def test_source_totals_match_serial(self):
        _, serial, _, _ = self.capture("0")
        _, conc, _, _ = self.capture("1")
        for key in ("sources_attempted", "sources_succeeded", "sources_failed"):
            self.assertEqual(conc[key], serial[key], key)
        self.assertEqual(conc["sources_attempted"], 4)

    def test_failures_attributed_to_the_failing_board_only(self):
        _, snap, _, _ = self.capture("1", fail={"a", "c"})
        failed = {u["board"] for u in snap["units"] if not u["ok"]}
        self.assertEqual(failed, {"lever:a", "lever:c"})
        self.assertEqual(snap["sources_failed"], 2)
        self.assertEqual(snap["sources_succeeded"], 2)
        for unit in snap["units"]:
            if not unit["ok"]:
                self.assertEqual(unit["failure_category"], "other")

    def test_written_file_is_valid_json_with_no_internal_keys(self):
        _, _, written, _ = self.capture("1")
        self.assertEqual(len(written["units"]), 4)
        for unit in written["units"]:
            self.assertNotIn("_t0", unit)
            self.assertNotIn("_deferred", unit)
            self.assertIsNotNone(unit["duration_ms"])

    def test_durations_are_per_board_not_cumulative(self):
        """Under concurrency the wall clock overlaps, so a unit's duration must
        still be its own request and not the provider's total."""
        _, snap, _, _ = self.capture("1")
        by_board = {u["board"]: u["duration_ms"] for u in snap["units"]}
        self.assertGreater(by_board["lever:a"], by_board["lever:d"])
        self.assertLess(by_board["lever:d"], 150)

    def test_thread_local_unit_context_is_isolated(self):
        """Directly: two threads holding open units must not see each other's."""
        seen = {}
        barrier = threading.Barrier(2)

        def worker(name):
            with telemetry.unit("free", "lever", board=f"lever:{name}",
                                defer=True):
                barrier.wait(timeout=5)
                telemetry.observed(raw=1, gated=1)
                seen[name] = telemetry._current()["board"]

        with tempfile.TemporaryDirectory() as tmp, _env(**{telemetry.FLAG: "1"}):
            telemetry.start("free", tmp)
            threads = [threading.Thread(target=worker, args=(n,))
                       for n in ("x", "y")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
            telemetry.finish()
        self.assertEqual(seen, {"x": "lever:x", "y": "lever:y"})


class ShadowIndependence(unittest.TestCase):
    """11. The shadow tranche is a separate flag and a separate list."""

    def test_shadow_boards_are_not_in_the_concurrent_registry(self):
        from sources import shadow
        with _env(**{concurrency.FLAG: "1"}):
            for token in shadow.BOARDS["greenhouse"]:
                self.assertFalse(concurrency.applies("greenhouse"), token)

    def test_shadow_is_greenhouse_so_it_cannot_enter_the_lever_path(self):
        """Since V2-B4 Greenhouse has a concurrent path of its own, behind its
        own switch — so the property is now: the LEVER switch never routes
        Greenhouse there, and shadow never calls this module at all (pinned
        with the Greenhouse switch on in test_free_concurrency_greenhouse)."""
        from sources import shadow
        self.assertEqual(set(shadow.BOARDS), {"greenhouse"})
        with _env(**{concurrency.FLAG: "1", concurrency.GREENHOUSE_FLAG: None}):
            self.assertFalse(concurrency.applies("greenhouse"))

    def test_concurrency_flag_does_not_enable_shadow(self):
        from sources import shadow
        with _env(**{concurrency.FLAG: "1", shadow.FLAG: None}):
            self.assertFalse(shadow.enabled())

    def test_shadow_flag_does_not_enable_concurrency(self):
        from sources import shadow
        with _env(**{shadow.FLAG: "1", concurrency.FLAG: None}):
            self.assertFalse(concurrency.enabled())


class RegistryUntouched(unittest.TestCase):
    def test_active_source_count_unchanged(self):
        import config
        # 128 since V2-D5 removed greenhouse:postman (a 404 on every fetch).
        self.assertEqual(sum(len(b) for b in config.ATS_BOARDS.values()), 128)
        self.assertEqual(sum(1 for c in config.FEEDS.values()
                             if c.get("enabled")), 5)

    def test_lever_board_count_unchanged(self):
        import config
        self.assertEqual(len(config.ATS_BOARDS["lever"]), 21)

    def test_no_board_is_skipped_or_reordered(self):
        import config
        lever = config.ATS_BOARDS["lever"]
        rec = Recorder(rows_for=lambda t: [], delay={})
        run_fetch_free(rec, boards=lever, **{concurrency.FLAG: "1",
                                             concurrency.WORKERS_ENV: "4"})
        self.assertEqual(sorted(rec.calls), sorted(lever))
        self.assertEqual(len(rec.calls), len(lever))


if __name__ == "__main__":
    unittest.main()
