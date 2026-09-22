"""V2-B1 paid-safety patch: the LinkedIn depth field and the charge ceiling.

V2-A verified that Sweep sent `count`, a field the current actor does not
document, while the documented depth control is `limitPerSource` — and that an
omitted limit scrapes "as many as LinkedIn returns for each search (up to
~1000)". A depth field the actor does not read is not a smaller sweep, it is an
unbounded one: ~$2.00 at the free tier's $0.002/result where Sweep's estimate
said $0.027.

Two things were changed and both are pinned here. `limitPerSource` now carries
the depth, and every LinkedIn run starts under a provider-enforced
`maxTotalChargeUsd`. Everything else about paid search — Indeed's input,
Naukri's input, plan cardinality, query and location expansion, and the cost
estimate — must be byte-for-byte what it was, and most of this file exists to
say so.

NO ACTOR IS INVOKED. Sockets are denied in setUpModule, so a test that
accidentally reached Apify would fail rather than spend.
"""
import os
import socket
import sys
import unittest
import unittest.mock
from decimal import Decimal

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import scraper  # noqa: E402

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def deny(*a, **kw):
        raise AssertionError("a paid-contract test tried to open a socket")
    socket.socket.connect = deny
    socket.create_connection = deny


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


def search(**over):
    base = {"keywords": "Software Engineer", "location": "Bengaluru",
            "company": "", "country": "IN", "experience_years": 2,
            "max_results": 15, "salary_min": None}
    base.update(over)
    return base


def linkedin_input(**over):
    s = search(**over)
    return scraper.build_input("linkedin", scraper.effective_search("linkedin", s))


# ---------------------------------------------------------------------------
# A fake Apify client. It records what it was asked to start and returns a run
# that looks finished, so scrape_search can be exercised end to end without a
# network or an account.
# ---------------------------------------------------------------------------
class FakeRun:
    id = "run_1"
    build_id = "build_1"
    started_at = "2026-09-22T00:00:00Z"
    finished_at = "2026-09-22T00:00:30Z"
    status = "SUCCEEDED"
    usage_total_usd = 0.03
    default_dataset_id = "ds_1"


class FakeActor:
    """`start` deliberately declares max_total_charge_usd, exactly as
    apify-client >= 3.1.0 does — scrape_search introspects for it."""

    def __init__(self, recorder):
        self.recorder = recorder

    def start(self, *, run_input=None, run_timeout=None,
              max_total_charge_usd=None, **kw):
        self.recorder.append({"run_input": run_input, "run_timeout": run_timeout,
                              "max_total_charge_usd": max_total_charge_usd})
        return FakeRun()


class LegacyActor:
    """An older client: no max_total_charge_usd parameter at all."""

    def __init__(self, recorder):
        self.recorder = recorder

    def start(self, *, run_input=None, run_timeout=None, **kw):
        self.recorder.append({"run_input": run_input, "started": True})
        return FakeRun()


class FakeRunClient:
    def get(self):
        return FakeRun()

    def abort(self):
        raise AssertionError("scrape_search aborted a run it should not have")


class FakeDataset:
    def iterate_items(self):
        return iter([{"title": "Software Engineer", "company": "Acme",
                      "location": "Bengaluru", "url": "https://x/1",
                      "description": "react node"}])


class FakeClient:
    def __init__(self, actor_cls=FakeActor):
        self.started = []
        self._actor_cls = actor_cls

    def actor(self, actor_id):
        return self._actor_cls(self.started)

    def run(self, run_id):
        return FakeRunClient()

    def dataset(self, dataset_id):
        return FakeDataset()


def run_one(client, depth=15):
    """One full scrape_search against the fake client. time.sleep is patched
    out so the poll loop does not actually wait five seconds."""
    import time as _time
    real_sleep = _time.sleep
    _time.sleep = lambda *_: None
    try:
        return scraper.scrape_search(client, "linkedin",
                                     "curious_coder/linkedin-jobs-scraper",
                                     search(max_results=depth))
    finally:
        _time.sleep = real_sleep


# ---------------------------------------------------------------------------
class LinkedInDepthField(unittest.TestCase):

    def test_limit_per_source_is_sent(self):
        """The documented depth field. Its absence is the whole bug."""
        self.assertIn("limitPerSource", linkedin_input())

    def test_limit_per_source_carries_the_effective_depth(self):
        for asked in (15, 25, 40):
            got = linkedin_input(max_results=asked)
            self.assertEqual(got["limitPerSource"], asked, asked)

    def test_count_is_retained_and_can_never_disagree(self):
        """`count` is a hedge against an older build, not a second opinion.

        If the two ever diverge, one of them is quietly deciding the bill.
        """
        for asked in (3, 15, 40):
            got = linkedin_input(max_results=asked)
            self.assertEqual(got["count"], got["limitPerSource"], asked)

    def test_the_depth_floor_is_unchanged(self):
        """Below the floor both fields carry the floor, as before this patch."""
        got = linkedin_input(max_results=3)
        self.assertEqual(got["limitPerSource"], scraper.ACTOR_MIN_RESULTS["linkedin"])
        self.assertEqual(got["count"], scraper.ACTOR_MIN_RESULTS["linkedin"])

    def test_requested_depth_is_not_silently_increased(self):
        """A safety patch that quietly deepens the sweep is a cost regression."""
        self.assertEqual(linkedin_input(max_results=15)["limitPerSource"], 15)
        self.assertEqual(
            scraper.effective_search("linkedin", {"max_results": 40})["max_results"],
            40)

    def test_scrape_company_still_off(self):
        self.assertIs(linkedin_input()["scrapeCompany"], False)

    def test_no_other_input_keys_appeared(self):
        self.assertEqual(set(linkedin_input()),
                         {"urls", "limitPerSource", "count", "scrapeCompany"})


class AutoConvertToAiSearch(unittest.TestCase):
    """The decision NOT to send it, pinned so nobody flips it without reading.

    V2-A suspected `autoConvertToAiSearch` was degrading Sweep's URL filters and
    proposed setting it false. Reading the actor's own text settles it the other
    way: "LinkedIn is now forcing AI job search which removed many classic
    filters (experience, job type, workplace, salary, sort, etc.)". The removal
    is LinkedIn's, not the actor's. With the option ON those filters are
    converted to natural language and appended to the keywords — an
    approximation. With it OFF the contract does not say they are enforced; it
    documents no FALSE behaviour at all, and the plain reading is that they are
    simply dropped.

    Crucially the README also says: "Kept as URL filters: f_TPR/f_TP: Date
    posted, f_C: Company, f_AL: Easy apply, f_EA: Under 10 applicants" — so
    Sweep's recency window and company targeting stay enforced either way.

    So setting false would trade an approximation of f_WT/f_E for nothing, and
    the contract is ambiguous about it. The setting is left alone.
    """

    def test_sweep_does_not_send_autoconvert(self):
        self.assertNotIn("autoConvertToAiSearch", linkedin_input())

    def test_recency_and_company_still_ride_in_the_url(self):
        """These two survive AI conversion, so they must still be sent."""
        url = linkedin_input()["urls"][0]
        self.assertIn("f_TPR=", url)
        with unittest.mock.patch.dict(scraper.LINKEDIN_COMPANY_IDS,
                                      {"Acme": "999"}):
            company_url = linkedin_input(company="Acme")["urls"][0]
        self.assertIn("f_C=999", company_url)


class ChargeCeiling(unittest.TestCase):

    def test_formula_for_the_default_depth(self):
        """15 x $0.002 x 1.5 + 2 x $0.00005 = $0.0451, rounded up to $0.046."""
        self.assertEqual(scraper.max_charge_usd("linkedin", 15), Decimal("0.046"))

    def test_ceiling_scales_with_depth(self):
        self.assertEqual(scraper.max_charge_usd("linkedin", 10), Decimal("0.031"))
        self.assertEqual(scraper.max_charge_usd("linkedin", 25), Decimal("0.076"))

    def test_ceiling_is_far_below_the_unbounded_exposure(self):
        """~1000 results at the free tier's $0.002 is ~$2.00. That is the number
        this guard exists to be nowhere near."""
        unbounded = Decimal("1000") * Decimal("0.002")
        self.assertLess(scraper.max_charge_usd("linkedin", 15), unbounded / 20)

    def test_ceiling_covers_an_honest_run_that_overshoots(self):
        """The actor has returned 16-18 rows for an intended 15. A ceiling that
        aborts such a run would turn a spend guard into a data-loss bug."""
        model = scraper.ACTOR_CHARGE_MODEL["linkedin"]
        worst = Decimal(18) * model["result_usd"] + model["start_usd"]
        self.assertGreater(scraper.max_charge_usd("linkedin", 15), worst)

    def test_ceiling_respects_the_providers_own_minimum(self):
        """minimalMaxTotalChargeUsd is $0.001; a ceiling under it is refused by
        the API, and a refused start is a failed search."""
        model = scraper.ACTOR_CHARGE_MODEL["linkedin"]
        self.assertGreaterEqual(scraper.max_charge_usd("linkedin", 1),
                                model["provider_minimum_usd"])

    def test_no_ceiling_for_sites_without_a_model(self):
        self.assertIsNone(scraper.max_charge_usd("indeed", 15))
        self.assertIsNone(scraper.max_charge_usd("naukri", 50))

    def test_ceiling_is_a_decimal_not_a_float(self):
        """Money in binary floating point is how 0.046 becomes 0.04600000000001."""
        self.assertIsInstance(scraper.max_charge_usd("linkedin", 15), Decimal)


class CeilingReachesTheRunInvocation(unittest.TestCase):
    """The formula being right is worthless if the number never leaves the process."""

    def test_start_receives_max_total_charge_usd(self):
        client = FakeClient()
        run_one(client)
        self.assertEqual(len(client.started), 1)
        self.assertEqual(client.started[0]["max_total_charge_usd"],
                         Decimal("0.046"))

    def test_the_ceiling_matches_the_billed_depth_not_the_asked_depth(self):
        """Below the floor the run is billed for 10, so the ceiling must cover 10."""
        client = FakeClient()
        run_one(client, depth=3)
        self.assertEqual(client.started[0]["max_total_charge_usd"],
                         scraper.max_charge_usd("linkedin", 10))

    def test_limit_per_source_reaches_the_run_input(self):
        client = FakeClient()
        run_one(client)
        self.assertEqual(client.started[0]["run_input"]["limitPerSource"], 15)

    def test_a_client_without_the_parameter_fails_closed(self):
        """The tempting 'handle it' is to drop the argument and run anyway.
        That is starting the exact unbounded run the guard exists to prevent."""
        client = FakeClient(actor_cls=LegacyActor)
        with self.assertRaises(RuntimeError) as caught:
            run_one(client)
        self.assertIn("maxTotalChargeUsd", str(caught.exception))
        self.assertEqual(client.started, [], "an unbounded run was started")

    def test_every_site_with_a_charge_model_gets_its_ceiling(self):
        """A future refactor cannot add a model and forget to pass it: this
        walks the model table rather than naming linkedin."""
        for site_key in scraper.ACTOR_CHARGE_MODEL:
            self.assertIsNotNone(scraper.max_charge_usd(site_key, 15), site_key)
            client = FakeClient()
            import time as _time
            real = _time.sleep
            _time.sleep = lambda *_: None
            try:
                scraper.scrape_search(client, site_key, "some/actor",
                                      search(max_results=15))
            finally:
                _time.sleep = real
            self.assertIsNotNone(client.started[0]["max_total_charge_usd"], site_key)

    def test_support_probe_reads_the_real_sdk(self):
        """Guards the guard: if this ever returns False for the pinned client,
        every paid LinkedIn search stops rather than running unbounded."""
        from apify_client import ApifyClient
        self.assertTrue(
            scraper.charge_ceiling_supported(ApifyClient("x").actor("y")))
        self.assertFalse(scraper.charge_ceiling_supported(LegacyActor([])))


class OtherSitesUnchanged(unittest.TestCase):

    def test_indeed_input_is_byte_for_byte_unchanged(self):
        self.assertEqual(
            scraper.build_input("indeed", scraper.effective_search(
                "indeed", search(location="Bengaluru"))),
            {"position": "Software Engineer", "location": "Bengaluru",
             "country": "IN", "maxItemsPerSearch": 15,
             "parseCompanyDetails": False, "saveOnlyUniqueItems": True,
             "followApplyRedirects": False})

    def test_naukri_input_is_byte_for_byte_unchanged(self):
        got = scraper.build_input("naukri", scraper.effective_search(
            "naukri", search(location="Remote")))
        self.assertEqual(got, {
            "keyword": "Software Engineer", "maxJobs": 50, "fetchDetails": True,
            "sortBy": "relevance",
            "freshness": scraper._naukri_freshness(scraper.SETTINGS["max_age_days"]),
            "workMode": ["remote"], "experience": "2"})

    def test_neither_gained_a_depth_or_ceiling_field(self):
        for site, loc in (("indeed", "Bengaluru"), ("naukri", "Remote")):
            got = scraper.build_input(site, scraper.effective_search(
                site, search(location=loc)))
            self.assertNotIn("limitPerSource", got, site)
            self.assertNotIn("maxTotalChargeUsd", got, site)

    def test_the_ceiling_is_never_smuggled_into_actor_input(self):
        """It is a start ARGUMENT. In run_input it would be an unread field
        that bounds nothing while looking like it does."""
        client = FakeClient()
        run_one(client)
        self.assertNotIn("maxTotalChargeUsd", client.started[0]["run_input"])


class PlanAndBudgetUnchanged(unittest.TestCase):
    """The provider ceiling is an ADDITIONAL layer. Sweep's own budget model,
    plan cardinality and cost estimate must be exactly as they were."""

    def plan(self):
        return {site: scraper.plan_for_site(site, _Args())
                for site in ("linkedin", "indeed")}

    def test_plan_cardinality_unchanged(self):
        plan = self.plan()
        self.assertEqual(len(plan["linkedin"]), 18)
        self.assertEqual(len(plan["indeed"]), 72)

    def test_query_and_location_expansion_unchanged(self):
        plan = self.plan()
        self.assertEqual([s["keywords"] for s in plan["linkedin"][:2]],
                         ["Full Stack Developer", "Full Stack Developer"])
        self.assertEqual([s["location"] for s in plan["linkedin"][:2]],
                         ["India", "Remote"])
        self.assertEqual(sorted({s["location"] for s in plan["indeed"]}),
                         ["Bengaluru", "Delhi", "Gurgaon", "Hyderabad",
                          "New Delhi", "Noida", "Pune", "Remote"])

    def test_reported_billable_depth_unchanged(self):
        for site in ("linkedin", "indeed"):
            self.assertEqual(
                scraper.effective_search(site, {"max_results": 15})["max_results"],
                15, site)

    def test_the_cost_estimate_is_unchanged(self):
        """plan.cost reads the dry-run depth, which this patch does not move."""
        from sweep import plan as plan_mod
        import config
        raw = {"profile": "", "free_sources": 134,
               "sites": {"linkedin": [{}] * 18, "indeed": [{}] * 72},
               "max_results": {"linkedin": 15, "indeed": 15}}
        costed = plan_mod.cost(raw, config.SITE_RATES, config.SITE_RATE_BASIS)
        self.assertEqual(costed["total"], 6.966)
        self.assertEqual(costed["total_searches"], 90)

    def test_the_engine_budget_guard_still_exists(self):
        """Not redesigned here. If the overall cap were removed, the provider
        ceiling would be bounding each run while nothing bounded the sweep."""
        import inspect as _inspect
        body = _inspect.getsource(scraper.main)
        self.assertIn('budget = SETTINGS["max_spend_usd"]', body)
        self.assertIn("if budget is not None and spent >= budget:", body)
        self.assertIn("stopped_early = True", body)

    def test_per_run_ceiling_cannot_cover_the_whole_sweep(self):
        """Stated as a test so the two layers are never confused: 90 runs each
        capped at $0.046 is $4.14, which only the sweep budget bounds."""
        per_run = scraper.max_charge_usd("linkedin", 15)
        self.assertGreater(per_run * 90, Decimal("4"))


class _Args:
    """The subset of the argparse namespace plan_for_site reads."""
    keywords = None
    limit = None
    test = False


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main()
