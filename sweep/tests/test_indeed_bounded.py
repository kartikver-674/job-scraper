"""Search Engine V2-C3.5: every Indeed start is bounded at the provider, so C2 can
hold its ceiling and run several at once — and the plan, the actor input, the
bytes and the survivors stay what they were.

misceres/indeed-scraper has been pay-per-event since 2026-03-26 with one
event, "result" (a job listing): $0.006 on the FREE tier, no start event, no
provider minimum, platform usage included. Apify never bills a run's events
past its maxTotalChargeUsd and aborts the run there. So V2-B1's rule now covers
Indeed as it covers LinkedIn: every start carries depth x $0.006 x 1.5 — $0.135
at the default depth of 15 — whether or not SWEEP_PAID_CONCURRENCY is on, and
under C2 that ceiling is reserved, committed and never handed back.

NO REAL CREDENTIAL AND NO REAL CLIENT: V2-C2's KeyedClient and V2-B1's FakeClient
only, `_require_token` patched, sockets denied for the module (which does not
reach apify-client's Rust transport; the stand-ins are what make a real call
impossible here). See docs/search-engine-v2-c35-indeed-bounded-execution.md.
"""
import itertools
import json
import re
import socket
import threading
import unittest
from collections import Counter
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import scraper
from bench import paid_guard
from bench import search_v2_paid_probe as probe
from sweep.tests import test_paid_concurrency as c2
from sweep.tests import test_paid_contract as b1

INDEED, LINKEDIN = c2.INDEED, c2.LINKEDIN
NAUKRI = "muhammetakkurtt/naukri-job-scraper"
CEILING = Decimal("0.135")          # 15 x $0.006 x 1.5, by hand
KW = c2.KW
Script = c2.Script
SERIAL = c2.SERIAL
TODAY = c2.TODAY

# What build_input sent Indeed before C3.5, key order included: the ceiling is a
# start ARGUMENT and must not have touched a byte of this.
PRE_C35 = {"position": "Software Engineer", "location": "Bengaluru", "country": "IN",
           "maxItemsPerSearch": 15, "parseCompanyDetails": False,
           "saveOnlyUniqueItems": True, "followApplyRedirects": False}

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def deny(*a, **kw):
        raise AssertionError("a C3.5 test tried to open a socket")
    socket.socket.connect = deny
    socket.create_connection = deny


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def rows(u, n=2):
    """Listings only Indeed search u returns: distinct employers, each final."""
    return [c2.indeed_item(f"{u}{j:02d}", "Full Stack Engineer", f"Indy {u}x{j}")
            for j in range(n)]


def twin(job_id):
    """One posting several Indeed searches return: same employer, title and
    text, so the same job_key and an equal score; only the jk differs."""
    return c2.indeed_item(job_id, "Full Stack Engineer", "Twin Works")


def listing(job_id):
    return f"https://www.indeed.com/viewjob?jk={job_id}"


def run(items, **kw):
    """One Indeed run, charged the way the actor charges: one `result` each."""
    kw.setdefault("usage", round(0.006 * len(items), 6))
    kw.setdefault("events", {"result": len(items)})
    return Script(items, **kw)


def staggered(unit_items):
    """The FIRST planned search finishes LAST, so completion order runs against
    plan order."""
    n = len(unit_items)
    return [run(items, polls=2, delay={"get": 0.03 * (n - u)})
            for u, items in enumerate(unit_items)]


def sweep(scripts, **kw):
    """An Indeed-only paid sweep through scraper.main(), one keyword per script."""
    kw.setdefault("sites", ("indeed",))
    kw.setdefault("keywords", KW[:len(scripts)])
    return c2.c2_sweep(scripts, **kw)


def without_indeed_model():
    """Indeed as it was before C3.5: no charge model, so no ceiling anywhere."""
    return mock.patch.object(scraper, "ACTOR_CHARGE_MODEL", {
        k: v for k, v in scraper.ACTOR_CHARGE_MODEL.items() if k != "indeed"})


def one_start(client, **over):
    """One scrape_search for Indeed against V2-B1's recording FakeClient."""
    with mock.patch.object(scraper.time, "sleep", lambda *_: None):
        return scraper.scrape_search(client, "indeed", INDEED, b1.search(**over))


def indeed_units(got):
    return [u for u in got.execution["units"] if u["provider"] == "indeed"]


# ===========================================================================
# The charge model
# ===========================================================================
class ChargeModel(unittest.TestCase):

    def test_the_default_depth_is_bounded_at_0135(self):
        """15 x $0.006 x 1.5 = $0.135 exactly: no start event, no minimum."""
        self.assertEqual(scraper.max_charge_usd("indeed", 15), CEILING)

    def test_the_ceiling_follows_the_billed_depth(self):
        for depth, want in ((1, "0.009"), (10, "0.090"), (25, "0.225"), (50, "0.450")):
            self.assertEqual(scraper.max_charge_usd("indeed", depth), Decimal(want), depth)

    def test_the_planned_search_carries_the_ceiling_of_its_configured_depth(self):
        search = scraper.plan_for_site("indeed", b1._Args())[0]
        unit = scraper.paid_unit(0, "indeed", search)
        self.assertEqual((unit["requested_depth"], unit["charge_ceiling_usd"]),
                         (15, "0.135"))

    def test_an_honest_overshoot_fits_and_a_runaway_does_not(self):
        """The actor has overshot maxItemsPerSearch before (its changelog,
        2025-07-11 and 2026-01-15). Apify bills up to the ceiling and aborts
        there: 22 listings ($0.132) fit, the 23rd would take it to $0.138."""
        self.assertLessEqual(22 * Decimal("0.006"), scraper.max_charge_usd("indeed", 15))
        self.assertGreater(23 * Decimal("0.006"), scraper.max_charge_usd("indeed", 15))

    def test_decimal_from_the_formula_to_the_start(self):
        """A float ceiling is how 0.135 becomes 0.13500000000000001."""
        self.assertIsInstance(scraper.max_charge_usd("indeed", 15), Decimal)
        client = b1.FakeClient()
        one_start(client)
        sent = client.started[0]["max_total_charge_usd"]
        self.assertIsInstance(sent, Decimal)
        self.assertEqual(sent, CEILING)

    def test_naukri_still_has_no_model(self):
        self.assertIsNone(scraper.max_charge_usd("naukri", 50))


# ===========================================================================
# The start: the ceiling as an argument, the input untouched, fail closed
# ===========================================================================
class StartContract(unittest.TestCase):

    def test_the_start_carries_exactly_the_computed_ceiling(self):
        client = b1.FakeClient()
        one_start(client)
        self.assertEqual(len(client.started), 1)
        self.assertEqual(client.started[0]["max_total_charge_usd"], Decimal("0.135"))

    def test_the_ceiling_support_check_is_consulted_for_indeed(self):
        asked = []

        def supported(actor):
            asked.append(actor)
            return True
        client = b1.FakeClient()
        with mock.patch.object(scraper, "charge_ceiling_supported", supported):
            one_start(client)
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(client.started), 1)

    def test_a_client_that_cannot_set_the_ceiling_starts_nothing(self):
        """Dropping the argument and running anyway would be starting exactly
        the unbounded Indeed run this exists to prevent."""
        client = b1.FakeClient(actor_cls=b1.LegacyActor)
        with self.assertRaises(RuntimeError) as caught:
            one_start(client)
        self.assertIn("maxTotalChargeUsd", str(caught.exception))
        self.assertIn("indeed", str(caught.exception))
        self.assertEqual(client.started, [], "an unbounded Indeed run was started")

    def test_the_run_input_is_the_pre_c35_object_and_bytes(self):
        client = b1.FakeClient()
        one_start(client)
        sent = client.started[0]["run_input"]
        self.assertEqual(sent, PRE_C35)
        self.assertEqual(json.dumps(sent), json.dumps(PRE_C35))      # key order too
        self.assertNotIn("maxTotalChargeUsd", sent)

    def test_a_remote_search_input_is_unchanged_too(self):
        got = scraper.build_input("indeed", scraper.effective_search(
            "indeed", b1.search(location="Remote")))
        self.assertEqual(got, dict(PRE_C35, location="Remote"))

    def test_the_depth_billed_is_the_depth_asked_no_floor(self):
        for asked in (1, 5, 15, 40):
            got = scraper.build_input("indeed", scraper.effective_search(
                "indeed", b1.search(max_results=asked)))
            self.assertEqual(got["maxItemsPerSearch"], asked, asked)


# ===========================================================================
# Serial — the production path — gets B1's per-run safety and nothing else
# ===========================================================================
class SerialProviderSafety(unittest.TestCase):
    """SWEEP_PAID_CONCURRENCY off: the serial loop, where production runs. Its
    Indeed starts now carry the ceiling, as its LinkedIn starts have since B1,
    and differ from before C3.5 in that argument alone."""

    @classmethod
    def setUpClass(cls):
        scripts = [run(rows(u) + [twin(f"9{u}")]) for u in range(3)]
        account = [1.0, 1.018, 1.036, 1.054]

        def swept(workers, patches=()):
            return sweep(scripts, workers=workers, account=list(account), free=True,
                         patches=patches)
        cls.before = swept(SERIAL, [without_indeed_model()])
        cls.serial, cls.one = swept(SERIAL), swept(1)

    def test_every_serial_indeed_start_carries_the_ceiling(self):
        self.assertEqual([c[3] for c in self.serial.client.kinds("start")],
                         [CEILING] * 3)
        self.assertEqual([c[3] for c in self.before.client.kinds("start")],
                         [None] * 3)

    def test_the_ceiling_is_the_only_difference_from_before(self):
        def unbounded(calls):
            return [c[:3] + (None,) if c[0] == "start" else c for c in calls]
        self.assertEqual(unbounded(self.serial.client.calls), self.before.client.calls)
        for _, _, run_input, _ in self.serial.client.kinds("start"):
            self.assertEqual(set(run_input), set(PRE_C35))

    def test_the_same_bytes_ledgers_funnel_ranking_and_dedupe(self):
        self.assertGreater(len(self.serial.rows), 3)
        for got in (self.serial, self.one):
            self.assertEqual((got.csv, got.json, got.seen, got.done),
                             (self.before.csv, self.before.json, self.before.seen,
                              self.before.done))
            self.assertEqual([(u["funnel"], u["trace"]) for u in got.telemetry["paid_units"]],
                             [(u["funnel"], u["trace"])
                              for u in self.before.telemetry["paid_units"]])

    def test_the_flag_off_never_enters_the_scheduler(self):
        self.assertNotIn("paid_execution", self.serial.telemetry)
        self.assertIn("paid_execution", self.one.telemetry)

    def test_one_worker_makes_the_serial_requests_in_the_serial_order(self):
        self.assertEqual(self.one.client.calls, self.serial.client.calls)

    def test_one_worker_keeps_the_serial_c1_record_but_the_clocks(self):
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
        self.assertEqual(s["identity"], o["identity"])


# ===========================================================================
# C2 classification: a charge model makes Indeed bounded; none keeps it serial
# ===========================================================================
class Classification(unittest.TestCase):

    def test_with_its_model_indeed_is_bounded_and_runs_in_the_pool(self):
        got = sweep(staggered([rows(u) for u in range(3)]), workers=4)
        (segment,) = got.execution["segments"]
        self.assertEqual((segment["provider"], segment["bounded"], segment["workers"]),
                         ("indeed", True, 4))
        self.assertEqual(got.client.peak[INDEED], 3)
        self.assertEqual({(u["reservation"], u["reserved_usd"]) for u in indeed_units(got)},
                         {("committed", "0.135")})

    def test_without_a_model_indeed_is_unbounded_and_one_at_a_time(self):
        got = sweep(staggered([rows(u) for u in range(3)]), workers=4,
                    patches=[without_indeed_model()])
        (segment,) = got.execution["segments"]
        self.assertEqual((segment["bounded"], segment["workers"]), (False, 1))
        self.assertEqual(got.client.peak[INDEED], 1)
        self.assertEqual({(u["reservation"], u["reserved_usd"]) for u in indeed_units(got)},
                         {("unbounded", None)})
        self.assertEqual({c[3] for c in got.client.kinds("start")}, {None})

    def test_naukri_the_provider_still_without_a_model_stays_serial(self):
        naukri = [run([{"jobDetails": {
            "title": "Full Stack Engineer", "jobId": f"n{u}",
            "companyDetail": {"name": f"Nau {u}"}, "locations": [{"label": "Bengaluru"}],
            "description": c2.RICH, "createdDate": TODAY}}], polls=2,
            delay={"get": 0.02}) for u in range(3)]
        got = sweep(naukri, sites=("naukri",), workers=4)
        (segment,) = got.execution["segments"]
        self.assertEqual((segment["provider"], segment["bounded"], segment["workers"]),
                         ("naukri", False, 1))
        self.assertEqual(got.client.peak[NAUKRI], 1)
        self.assertEqual({c[3] for c in got.client.kinds("start")}, {None})
        self.assertEqual(got.telemetry["paid_summary"]["planned_unbounded_units"], 3)


# ===========================================================================
# Reservation: the full $0.135, before the start, never handed back
# ===========================================================================
class Reservation(unittest.TestCase):
    """End to end through main(); the account never moves (the C1/C2 lag), so
    no observed reading can make room either."""

    def starts(self, budget, n=3, workers=(1, 2, 4)):
        counts = {}
        for w in workers:
            got = sweep([run(rows(u)) for u in range(n)], budget=budget, workers=w,
                        account=[5.0])
            counts[w] = len(got.client.kinds("start"))
        self.assertEqual(len(set(counts.values())), 1, counts)
        return got, counts[workers[-1]]

    def test_a_budget_of_exactly_one_ceiling_starts_one(self):
        got, n = self.starts(0.135)
        self.assertEqual(n, 1)
        self.assertEqual(got.status(), ["completed", "skipped_budget", "skipped_budget"])
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0.135")

    def test_a_budget_of_exactly_two_ceilings_starts_two_and_blocks_the_third(self):
        got, n = self.starts(0.27)
        self.assertEqual(n, 2)
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0.270")
        self.assertEqual(got.execution["exposure"]["blocked"], 1)
        self.assertEqual(got.status(), ["completed", "completed", "skipped_budget"])
        self.assertIn("spend cap $0.27: $0.270 held + the next start's $0.135 ceiling "
                      "would exceed it", got.log)

    def test_a_tenth_of_a_cent_short_does_not_fit(self):
        self.assertEqual(self.starts(0.269)[1], 1)
        self.assertEqual(self.starts(0.134)[1], 0)

    def test_the_lagging_guard_before_c2_let_all_three_through(self):
        old = sweep([run(rows(u)) for u in range(3)], budget=0.135, account=[5.0])
        self.assertEqual(len(old.client.kinds("start")), 3)

    def test_the_reserved_ceiling_is_the_one_the_start_carries(self):
        got = sweep(staggered([rows(u) for u in range(3)]), workers=3)
        sent = [c[3] for c in got.client.kinds("start")]
        self.assertEqual(sent, [CEILING] * 3)
        self.assertEqual([u["reserved_usd"] for u in indeed_units(got)], ["0.135"] * 3)
        self.assertEqual(Decimal(got.execution["exposure"]["committed_usd"]), sum(sent))

    def test_the_reservation_exists_before_its_worker_starts(self):
        seen, real = [], scraper._paid_worker

        def spy(entry, make_client, exposure, results):
            seen.append((entry.unit_id, (exposure.units.get(entry.unit_id) or {})
                         .get("state")))
            return real(entry, make_client, exposure, results)
        sweep([run(rows(u)) for u in range(3)], budget=0.3, workers=4, account=[5.0],
              patches=[mock.patch.object(scraper, "_paid_worker", spy)])
        self.assertEqual(seen, [("paid_000", "pending"), ("paid_001", "pending")])

    def test_the_hold_is_committed_before_the_start_request(self):
        events, lock = [], threading.Lock()
        commit, start = scraper.PaidExposure.commit, c2.KeyedClient._start

        def spy_commit(self, unit_id):
            commit(self, unit_id)
            with lock:
                events.append(("commit", unit_id, self.units[unit_id]["state"]))

        def spy_start(self, actor_id, run_input, ceiling):
            with lock:
                events.append(("start", f"paid_00{KW.index(run_input['position'])}"))
            return start(self, actor_id, run_input, ceiling)
        sweep(staggered([rows(u) for u in range(3)]), workers=3,
              patches=[mock.patch.object(scraper.PaidExposure, "commit", spy_commit),
                       mock.patch.object(c2.KeyedClient, "_start", spy_start)])
        for unit in ("paid_000", "paid_001", "paid_002"):
            self.assertIn(("commit", unit, "committed"), events)
            self.assertLess(events.index(("commit", unit, "committed")),
                            events.index(("start", unit)), unit)

    def test_no_budget_holds_every_ceiling_and_never_blocks(self):
        got = sweep(staggered([rows(u) for u in range(4)]), workers=4)
        self.assertEqual(len(got.client.kinds("start")), 4)
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0.540")
        self.assertIsNone(got.execution["exposure"]["budget_usd"])


# ===========================================================================
# Nothing that happens after the start hands the ceiling back
# ===========================================================================
class FailuresKeepTheirCeiling(unittest.TestCase):
    """Two Indeed searches at one worker under $0.20 — room for ONE ceiling —
    so the second is decided after the first's outcome is in: exactly where a
    released hold would let it through."""

    def after(self, first, patches=(), account=(5.0,)):
        return sweep([first, run(rows(1))], budget=0.2, workers=1, patches=patches,
                     account=None if account is None else list(account))

    def assertKept(self, got, status):
        self.assertEqual(got.status(), [status, "skipped_budget"])
        self.assertEqual(len(got.client.kinds("start")), 1)
        unit = got.execution["units"][0]
        self.assertEqual((unit["reservation"], unit["reserved_usd"]), ("committed", "0.135"))
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0.135")

    def test_provider_failed(self):
        self.assertKept(self.after(run(rows(0), status="FAILED", usage=0.0)), "failed")

    def test_provider_timed_out(self):
        self.assertKept(self.after(run(rows(0), status="TIMED-OUT")), "failed")

    def test_provider_aborted_at_the_ceiling(self):
        """What Apify does to a run whose charges reach maxTotalChargeUsd."""
        self.assertKept(self.after(run(rows(0, 22), status="ABORTED", usage=0.132)),
                        "failed")

    def test_engine_deadline_then_abort(self):
        clock, lock = itertools.count(0, 61), threading.Lock()

        def monotonic():
            with lock:
                return float(next(clock))
        got = self.after(run(rows(0), polls=10 ** 6),
                         patches=[mock.patch.object(scraper.time, "monotonic", monotonic)])
        self.assertIn(("abort", "run_1"), got.client.calls)
        self.assertKept(got, "failed")

    def test_an_ambiguous_start_with_no_run_id(self):
        got = self.after(Script(start_error=RuntimeError("connection reset by peer")))
        self.assertNotIn("actor_run_id", got.telemetry["units"][0])
        self.assertKept(got, "failed")

    def test_a_ceiling_the_provider_rejected(self):
        got = self.after(Script(start_error=RuntimeError(
            "maxTotalChargeUsd is below the minimum this Actor allows")))
        self.assertKept(got, "failed")

    def test_a_dataset_read_that_fails(self):
        real = c2.KeyedClient.dataset

        def dataset(self, dataset_id):
            if dataset_id != "ds_run_1":
                return real(self, dataset_id)

            def items():
                self._log("dataset", dataset_id)
                raise RuntimeError("dataset read failed")
            return SimpleNamespace(iterate_items=items)
        got = self.after(run(rows(0)),
                         patches=[mock.patch.object(c2.KeyedClient, "dataset", dataset)])
        self.assertIn(("dataset", "ds_run_1"), got.client.calls)
        self.assertKept(got, "failed")

    def test_an_account_read_that_fails(self):
        readings = iter([5.0])                 # the baseline, then unreadable
        got = self.after(run(rows(0)), patches=[mock.patch.object(
            scraper, "account_usage_usd", lambda client: next(readings, None))])
        self.assertEqual(got.telemetry["units"][0]["budget_basis"], "run_record_sum")
        self.assertKept(got, "completed")

    def test_zero_rows(self):
        self.assertKept(self.after(run([])), "completed")

    def test_the_actors_no_results_error_item(self):
        """The README: a search that finds nothing pushes an error item. It is
        one raw row that never reaches the output, and the hold stays."""
        got = self.after(run([{"error": "FOUND_NO_RESULTS",
                               "errorDescription": "Scraper didn't find any jobs"}]))
        self.assertKept(got, "completed")
        self.assertEqual(got.telemetry["paid_units"][0]["funnel"]["raw"], 1)
        self.assertEqual(got.telemetry["paid_units"][0]["funnel"]["final"], 0)

    def test_the_full_depth_returned(self):
        got = self.after(run(rows(0, 15)))
        self.assertEqual(got.telemetry["paid_units"][0]["funnel"]["raw"], 15)
        self.assertKept(got, "completed")

    def test_a_cheap_settled_run_is_still_the_whole_ceiling(self):
        got = self.after(run(rows(0), usage=0.00005), account=None)
        self.assertEqual(got.execution["exposure"]["observed_usd"], "0.00005")
        self.assertKept(got, "completed")

    def test_an_sdk_without_the_ceiling_starts_nothing_and_holds_nothing(self):
        got = sweep([run(rows(0)), run(rows(1))], budget=0.2, workers=1, account=[5.0],
                    patches=[mock.patch.object(scraper, "charge_ceiling_supported",
                                               lambda actor: False)])
        self.assertEqual(got.client.kinds("start"), [])
        self.assertEqual(got.status(), ["failed", "failed"])
        self.assertEqual([u["reservation"] for u in got.execution["units"]],
                         ["released_before_network"] * 2)
        self.assertEqual(got.execution["exposure"]["committed_usd"], "0")


# ===========================================================================
# LinkedIn and Indeed: one budget, one view
# ===========================================================================
LI_IN = ("linkedin", "indeed")
PLACES = {"linkedin": ["Bengaluru", "Hyderabad"], "indeed": ["Bengaluru", "Hyderabad"]}


def mixed_scripts():
    li = [Script(c2.solo(u), polls=2, delay={"get": 0.04 * (2 - u)}) for u in range(2)]
    return li, [run(rows(u), polls=2, delay={"get": 0.03 * (2 - u)}) for u in range(2)]


class MixedExposure(unittest.TestCase):

    def test_both_providers_ceilings_accumulate_in_one_view(self):
        li, ind = mixed_scripts()
        got = c2.c2_sweep(li + ind, keywords=KW[:1], site_locations=PLACES, sites=LI_IN,
                          workers=4)
        ex = got.execution["exposure"]
        self.assertEqual((ex["committed_usd"], ex["committed_starts"]), ("0.362", 4))
        self.assertEqual({s["provider"]: (s["bounded"], s["workers"])
                          for s in got.execution["segments"]},
                         {"linkedin": (True, 4), "indeed": (True, 4)})

    def test_indeed_starts_see_the_linkedin_ceilings_already_held(self):
        """$0.30: two LinkedIn holds ($0.092), Indeed #1 fits ($0.227), Indeed #2
        would make $0.362 — refused, though the account never moved."""
        li, ind = mixed_scripts()
        got = c2.c2_sweep(li + ind, keywords=KW[:1], site_locations=PLACES, sites=LI_IN,
                          workers=4, budget=0.30, account=[5.0])
        self.assertEqual(Counter(c[1] for c in got.client.kinds("start")),
                         {LINKEDIN: 2, INDEED: 1})
        self.assertIn("spend cap $0.30: $0.227 held + the next start's $0.135 ceiling "
                      "would exceed it", got.log)

    def test_linkedin_starts_see_the_indeed_ceilings_already_held(self):
        li, ind = mixed_scripts()
        got = c2.c2_sweep(ind + li, keywords=KW[:1], site_locations=PLACES, sites=LI_IN,
                          site_order=("indeed", "linkedin", "naukri"), workers=4,
                          budget=0.30, account=[5.0])
        self.assertEqual(Counter(c[1] for c in got.client.kinds("start")), {INDEED: 2})
        self.assertEqual(got.execution["units"][-1]["reservation"], "blocked")

    def test_a_funded_mixed_plan_is_byte_identical_at_every_worker_count(self):
        li, ind = mixed_scripts()
        serial = c2.c2_sweep(li + ind, keywords=KW[:1], site_locations=PLACES,
                             sites=LI_IN, free=True)
        for w in (1, 2, 3, 4):
            got = c2.c2_sweep(li + ind, keywords=KW[:1], site_locations=PLACES,
                              sites=LI_IN, free=True, workers=w)
            self.assertEqual((got.csv, got.json, got.seen, got.done),
                             (serial.csv, serial.json, serial.seen, serial.done), w)


# ===========================================================================
# Concurrent Indeed: the same result at every worker count
# ===========================================================================
class ConcurrentParity(unittest.TestCase):
    """Four Indeed searches, every one returning the same twin posting, the
    FIRST planned finishing LAST, beside a free source."""

    @classmethod
    def setUpClass(cls):
        units = [rows(u) + [twin(f"9{u}")] for u in range(4)]
        cls.runs = {w: sweep(staggered(units), workers=w, free=True,
                             account=[1.0, 1.018, 1.036, 1.054, 1.072])
                    for w in (SERIAL, 1, 2, 3, 4)}

    def test_byte_identical_at_every_worker_count(self):
        serial = self.runs[SERIAL]
        self.assertGreater(len(serial.rows), 8)
        for w, got in self.runs.items():
            self.assertEqual((got.csv, got.json, got.seen, got.done),
                             (serial.csv, serial.json, serial.seen, serial.done), w)

    def test_the_same_starts_inputs_and_ceilings(self):
        def starts(got):
            return sorted(json.dumps([c[1], c[2], str(c[3])], sort_keys=True)
                          for c in got.client.kinds("start"))
        for w, got in self.runs.items():
            self.assertEqual(starts(got), starts(self.runs[SERIAL]), w)
            self.assertEqual(Counter(c[0] for c in got.client.calls),
                             Counter(c[0] for c in self.runs[SERIAL].client.calls), w)

    def test_indeed_really_runs_w_at_a_time(self):
        for w in (1, 2, 3, 4):
            self.assertEqual(self.runs[w].client.peak[INDEED], w)
            self.assertEqual(self.runs[w].execution["peak_in_flight"], w)
        self.assertEqual(self.runs[SERIAL].client.peak[INDEED], 1)
        self.assertEqual(self.runs[4].client.order("dataset"),
                         ["run_4", "run_3", "run_2", "run_1"])

    def test_the_first_planned_twin_wins_whoever_finished_first(self):
        for w, got in self.runs.items():
            twins = [r for r in got.rows if r["company"] == "Twin Works"]
            self.assertEqual([(r["search_query"], r["apply_url"]) for r in twins],
                             [(f"{KW[0]} @ Bengaluru", listing("90"))], w)

    def test_log_ledger_and_units_follow_plan_order(self):
        for w, got in self.runs.items():
            self.assertEqual(c2.lines_in_plan_order(got.log),
                             [(str(u + 1), KW[u]) for u in range(4)], w)
            self.assertEqual(got.done, [f"{TODAY}|indeed|{KW[u]}|Bengaluru|"
                                        for u in range(4)], w)


class DuplicateTie(unittest.TestCase):

    def test_a_later_indeed_search_finishing_first_does_not_take_the_tie(self):
        scripts = [run([twin("90")] + rows(0), polls=3, delay={"get": 0.08}),
                   run([twin("91")] + rows(1), polls=1)]
        serial = sweep(scripts)
        for w in (2, 3, 4):
            got = sweep(scripts, workers=w)
            self.assertEqual(got.client.order("dataset"), ["run_2", "run_1"], w)
            twins = [r for r in got.rows if r["company"] == "Twin Works"]
            self.assertEqual([r["apply_url"] for r in twins], [listing("90")], w)
            self.assertEqual((got.csv, got.json), (serial.csv, serial.json), w)


# ===========================================================================
# One client per search, never shared between threads
# ===========================================================================
class ClientIsolation(unittest.TestCase):

    def test_every_search_has_its_own_client_used_by_its_own_thread(self):
        made, lock = [], threading.Lock()
        real = c2.KeyedClient.__call__

        class Handle:
            def __init__(self, inner):
                self._inner, self.threads = inner, set()

            def __getattr__(self, name):
                with lock:
                    self.threads.add(threading.current_thread().name)
                return getattr(self._inner, name)

        def construct(self, token=None, **kw):
            real(self, token, **kw)
            handle = Handle(self)
            with lock:
                made.append(handle)
            return handle
        got = sweep(staggered([rows(u) for u in range(4)]), workers=4,
                    patches=[mock.patch.object(c2.KeyedClient, "__call__", construct)])
        used = [h for h in made if h.threads]
        self.assertEqual([len(h.threads) for h in used], [1] * len(used))
        searches = [h for h in used if h.threads != {"MainThread"}]
        self.assertEqual(len(searches), len(got.client.kinds("start")))
        self.assertEqual(len({next(iter(h.threads)) for h in searches}), 4)


# ===========================================================================
# Telemetry: C1 and C2 describe a bounded Indeed unit like any bounded unit
# ===========================================================================
class Telemetry(unittest.TestCase):

    def test_c1_plans_and_records_the_indeed_ceiling(self):
        li, ind = mixed_scripts()
        got = c2.c2_sweep(li + ind, keywords=KW[:1], site_locations=PLACES, sites=LI_IN)
        s = got.telemetry["paid_summary"]
        self.assertEqual((s["planned_bounded_exposure_usd"], s["planned_unbounded_units"]),
                         ("0.362", 0))
        units = {u["unit_id"]: u for u in got.telemetry["units"] if u["path"] == "paid"}
        for u in got.telemetry["paid_units"]:
            if u["provider"] == "indeed":
                self.assertEqual(u["charge_ceiling_usd"], "0.135")
                self.assertEqual(units[u["unit_id"]]["max_total_charge_usd"], "0.135")
                self.assertEqual(units[u["unit_id"]]["provider_ceiling_usd"], 0.135)

    def test_c2_holds_the_ceiling_as_exposure_never_as_cost(self):
        got = sweep(staggered([rows(u) for u in range(2)]), workers=2, account=[5.0])
        for u in indeed_units(got):
            self.assertEqual((u["bounded"], u["reservation"], u["reserved_usd"]),
                             (True, "committed", "0.135"))
        for unit in got.telemetry["units"]:
            sources = {o["source"] for o in unit.get("cost_observations") or []}
            self.assertLessEqual(sources, {"run_record_at_completion",
                                           "account_usage_delta"})


# ===========================================================================
# The developer guard now prices Indeed; Naukri is what it refuses
# ===========================================================================
class DeveloperGuard(unittest.TestCase):

    def test_an_indeed_plan_has_a_worst_case_the_guard_can_hold(self):
        plan = {"indeed": {"actor": INDEED, "starts": 2, "depth": 15,
                           "ceiling_usd": scraper.max_charge_usd("indeed", 15)}}
        self.assertEqual(paid_guard.worst_case(plan), Decimal("0.270"))
        paid_guard.authorize(plan, True, Decimal("0.643"), Decimal("0.368"),
                             {paid_guard.FLAG: "1"})
        with self.assertRaises(paid_guard.PaidBenchBlocked):
            paid_guard.authorize(plan, True, Decimal("0.637"), Decimal("0.368"),
                                 {paid_guard.FLAG: "1"})


class ProbeContractReadings(unittest.TestCase):
    """The probe's C3.5 readers, offline: what the provider says a run was
    given, and the account's memory and concurrency limits."""

    def test_the_run_as_the_provider_ran_it(self):
        stored = {"position": "Software Engineer", "maxItemsPerSearch": 15}
        client = SimpleNamespace(key_value_store=lambda store_id: SimpleNamespace(
            get_record=lambda key: {"key": key, "value": stored} if (
                store_id, key) == ("kv_1", "INPUT") else None))
        run = SimpleNamespace(
            options=SimpleNamespace(memory_mbytes=4096, timeout_secs=300, max_items=None,
                                    max_total_charge_usd=0.135),
            platform_usage_billing_model="DEVELOPER", pricing_info={
                "pricing_model": "PAY_PER_EVENT"}, charged_event_counts={"result": 15},
            usage_total_usd=0.09, default_key_value_store_id="kv_1")
        got = probe.run_contract(client, run)
        self.assertEqual((got["memory_mbytes"], got["max_total_charge_usd"],
                          got["platform_usage_billing_model"], got["usage_total_usd"],
                          got["input_received"]),
                         (4096, 0.135, "DEVELOPER", 0.09, stored))

    def test_the_accounts_memory_and_concurrency_limits(self):
        client = SimpleNamespace(user=lambda: SimpleNamespace(limits=lambda: SimpleNamespace(
            model_dump=lambda: {"limits": {"max_actor_memory_gbytes": 16,
                                           "max_concurrent_actor_jobs": 5},
                                "current": {"actor_memory_gbytes": 0,
                                            "active_actor_job_count": 0}})))
        self.assertEqual(probe.account_limits(client), {
            "max_actor_memory_gbytes": 16, "max_concurrent_actor_jobs": 5,
            "actor_memory_gbytes_in_use": 0, "active_actor_jobs": 0})


if __name__ == "__main__":
    unittest.main()
