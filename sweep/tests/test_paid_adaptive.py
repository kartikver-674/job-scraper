"""Search Engine V2-C4: the automatic spend cap authorises the plan it prices,
LinkedIn's own rank is recorded without touching the output, and adaptive paid
execution is evaluated in shadow — decisions and their losses recorded, the
work exactly as it was.

Four promises, each pinned here:

  1. A cap Sweep GENERATES holds every provider ceiling of the plan it was
     generated for (default: $10.548 of ceilings, cap $10.55). A cap someone
     SUPPLIES — a profile's max_spend_usd, the worker's prefs — is never
     raised; the engine runs what fits under it.
  2. provider_positions is LinkedIn's `position`, UNKNOWN where the provider
     gave none, never the dataset index. search_rank and every output byte
     are unchanged.
  3. SWEEP_PAID_ADAPTIVE_MODE=shadow (and enforce, which has nothing to
     enforce) makes the same starts, inputs, depths, reservations and
     requests as off, and writes the same bytes and ledgers.
  4. Every candidate decision is made from the observations up to it, never
     after, and is priced by what it would have lost as well as saved.

NO REAL CREDENTIAL AND NO REAL CLIENT: V2-C2's KeyedClient, `_require_token`
patched, sockets denied for the module (which does not reach apify-client's
Rust transport; the stand-in is what makes a real call impossible here). See
docs/search-engine-v2-c4-adaptive-paid-execution.md.
"""
import ast
import contextlib
import copy
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path
from unittest import mock

import config
import paid_adaptive
import scraper
from bench import search_v2_paid_adaptive as tool
from bench import search_v2_paid_compaction as c3bench
from sweep import plan as plan_mod
from sweep.app import SPEND_CAP_FLOOR_USD, spend_cap_for
from sweep.tests import test_app as app_tests
from sweep.tests import test_indeed_bounded as c35
from sweep.tests import test_paid_concurrency as c2
from sweep.tests import test_paid_dev_guard as c0
from sweep.tests import test_paid_observability as c1

ROOT = Path(__file__).resolve().parents[2]
MODE = paid_adaptive.MODE_ENV
KW = c2.KW
Script = c2.Script
SERIAL = c2.SERIAL
TODAY = c2.TODAY
RICH = c2.RICH
LINKEDIN, INDEED = c2.LINKEDIN, c2.INDEED

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def deny(*a, **kw):
        raise AssertionError("a C4 test tried to open a socket")
    socket.socket.connect = deny
    socket.create_connection = deny


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


# ===========================================================================
# 1. The automatic spend cap
# ===========================================================================
def priced(case=None, sites=None, depth=None):
    """The public app's pricing path, offline: the engine's own --dry-run
    --json for a plan (lowered as make_profile renders it), plan.cost, and
    spend_cap_for — the three calls sweep.app.costed() makes."""
    out = io.StringIO()
    with c3bench.lowered(scraper, config, case):
        if sites is not None:
            scraper.SITES = {k: dict(v, enabled=k in sites) for k, v in scraper.SITES.items()}
        if depth is not None:
            scraper.SEARCH = dict(scraper.SEARCH, max_results=depth)
        units = c3bench.current_plan(scraper)
        with mock.patch.object(sys, "argv", ["scraper.py", "--dry-run", "--json"]), \
                contextlib.redirect_stdout(out):
            scraper.main()
    raw = json.loads(out.getvalue().strip().splitlines()[-1])
    costed = plan_mod.cost(raw, config.SITE_RATES, config.SITE_RATE_BASIS)
    ceilings = sum((u["ceiling"] for u in units if u["ceiling"] is not None), Decimal(0))
    return raw, costed, Decimal(str(spend_cap_for(costed))), ceilings


def case(name):
    return dict(c3bench.cases())[name]


class AutomaticCap(unittest.TestCase):
    """Built from the plan, the depth and the charge model — never a constant.
    Each test also recomputes the ceilings independently, straight from the
    planner and max_charge_usd, and requires the cap to hold them."""

    def assertHolds(self, costed, cap, ceilings):
        self.assertEqual(Decimal(costed["bounded_exposure"]), ceilings)
        self.assertGreaterEqual(cap, ceilings)

    def test_the_default_plan_is_authorised_in_full(self):
        raw, costed, cap, ceilings = priced()
        self.assertEqual({s: len(v) for s, v in raw["sites"].items()},
                         {"linkedin": 18, "indeed": 72})
        self.assertEqual(raw["charge_ceiling_usd"], {"linkedin": "0.046", "indeed": "0.135"})
        self.assertEqual(costed["total"], 6.966)                    # the estimate
        self.assertEqual(costed["bounded_exposure"], "10.548")      # the ceilings
        self.assertEqual(cap, Decimal("10.55"))
        self.assertHolds(costed, cap, ceilings)
        # What it replaced, from the same function handed no ceiling.
        self.assertEqual(spend_cap_for({"total": costed["total"]}), 8.71)

    def test_the_public_india_plan(self):
        _, costed, cap, ceilings = priced(case("public_scope_india__software_fullstack"))
        self.assertEqual((costed["total"], costed["bounded_exposure"], cap),
                         (1.404, "2.172", Decimal("2.18")))
        self.assertHolds(costed, cap, ceilings)

    def test_the_public_global_plan(self):
        _, costed, cap, ceilings = priced(case("public_scope_global__software_fullstack"))
        self.assertEqual((costed["total"], costed["bounded_exposure"], cap),
                         (2.106, "3.258", Decimal("3.26")))
        self.assertHolds(costed, cap, ceilings)

    def test_the_public_remote_plan_keeps_the_floor(self):
        _, costed, cap, ceilings = priced(case("public_scope_remote__software_fullstack"))
        self.assertEqual((costed["total"], costed["bounded_exposure"]), (0.234, "0.362"))
        self.assertEqual(cap, Decimal(str(SPEND_CAP_FLOOR_USD)))
        self.assertHolds(costed, cap, ceilings)

    def test_linkedin_only(self):
        _, costed, cap, ceilings = priced(sites=("linkedin",))
        self.assertEqual((costed["bounded_exposure"], cap), ("0.828", Decimal("0.83")))
        self.assertHolds(costed, cap, ceilings)

    def test_indeed_only(self):
        _, costed, cap, ceilings = priced(sites=("indeed",))
        self.assertEqual((costed["bounded_exposure"], cap), ("9.720", Decimal("9.72")))
        self.assertHolds(costed, cap, ceilings)

    def test_no_paid_provider(self):
        raw, costed, cap, ceilings = priced(sites=())
        self.assertEqual(raw["sites"], {})
        self.assertEqual((costed["bounded_exposure"], costed["unbounded_searches"],
                          costed["unbounded_estimate"]), ("0", 0, 0))
        self.assertEqual(cap, Decimal(str(SPEND_CAP_FLOOR_USD)))

    def test_an_unbounded_provider_stays_an_estimate_beside_the_ceilings(self):
        """Naukri has no charge model: its searches are counted and priced by
        estimate, never folded into the bounded exposure as if a maximum."""
        raw, costed, cap, ceilings = priced(case("business_salesforce"))
        self.assertIsNone(raw["charge_ceiling_usd"]["naukri"])
        self.assertEqual({s: len(v) for s, v in raw["sites"].items()},
                         {"linkedin": 6, "indeed": 6, "naukri": 6})
        self.assertEqual(costed["bounded_exposure"], "1.086")       # LinkedIn + Indeed
        self.assertEqual((costed["unbounded_estimate"], costed["unbounded_searches"]),
                         (3.0, 6))
        # every bounded ceiling + the old headroom on the unbounded estimate
        self.assertEqual(cap, Decimal("4.84"))
        self.assertHolds(costed, cap, ceilings)

    def test_depth_moves_the_exposure(self):
        _, costed, cap, ceilings = priced(depth=25)
        self.assertEqual(scraper.max_charge_usd("linkedin", 25), Decimal("0.076"))
        self.assertEqual(costed["bounded_exposure"], str(18 * Decimal("0.076")
                                                         + 72 * Decimal("0.225")))
        self.assertHolds(costed, cap, ceilings)

    def test_a_pricing_change_moves_the_cap(self):
        model = copy.deepcopy(scraper.ACTOR_CHARGE_MODEL)
        model["indeed"]["result_usd"] = Decimal("0.009")
        with mock.patch.object(scraper, "ACTOR_CHARGE_MODEL", model):
            _, costed, cap, ceilings = priced()
        self.assertEqual(costed["bounded_exposure"],
                         str(18 * Decimal("0.046") + 72 * Decimal("0.203")))
        self.assertEqual(cap, Decimal("15.45"))
        self.assertHolds(costed, cap, ceilings)

    def test_rounded_up_never_down(self):
        for total, bounded in ((0.1, "0.501"), (1.0, "3.3301"), (2.0, "2.999"),
                               (0.0, "10.548")):
            cap = Decimal(str(spend_cap_for({"total": total, "bounded_exposure": bounded,
                                             "unbounded_estimate": 0})))
            self.assertGreaterEqual(cap, Decimal(bounded))
            self.assertLess(cap - Decimal(bounded), Decimal("0.01"))

    def test_an_older_engine_plan_keeps_the_estimate_cap(self):
        raw, _, _, _ = priced()
        raw.pop("charge_ceiling_usd")
        costed = plan_mod.cost(raw, config.SITE_RATES, config.SITE_RATE_BASIS)
        self.assertEqual(costed["bounded_exposure"], "0")
        self.assertEqual(spend_cap_for(costed), 8.71)


class TheAppStampsTheGeneratedCap(unittest.TestCase):
    """/confirm shows the cap that holds the plan's ceilings, and POST /run
    writes that same figure into the profile the engine reads. Through the
    console's own confirm-screen fixture, answering the real default plan."""

    def test_confirm_and_run_carry_the_ceiling_holding_cap(self):
        raw, _, _, _ = priced()
        screen = app_tests.TestConfirmScreen("test_confirm_names_the_amount_on_the_button")
        screen.setUp()
        self.addCleanup(screen.doCleanups)
        with mock.patch.object(app_tests, "RAW_PLAN", raw):
            app = screen._app(cap=50.0)
            client = app.test_client()
            client.get("/confirm")
            plan = app.state["plan"]
            self.assertEqual((plan["total"], plan["bounded_exposure"], plan["spend_cap"]),
                             (6.966, "10.548", 10.55))
            self.assertEqual(client.post("/run").status_code, 302)
        self.assertEqual(app.state["max_spend_usd"], 10.55)
        self.assertIn('"max_spend_usd": 10.55,', app.written[-1][1])


class SuppliedCapsAreNeverRaised(unittest.TestCase):
    """No screen or form takes a cap from the user: the app generates one.
    Where a cap IS supplied — a profile's max_spend_usd, the worker's prefs —
    it is the budget, and the engine runs only what fits under it."""

    def indeed(self, budget, workers=2):
        return c35.sweep([c35.run(c35.rows(u)) for u in range(3)], budget=budget,
                         workers=workers, account=[5.0])

    def test_below_the_plans_exposure_the_engine_runs_what_fits(self):
        for w in (SERIAL, 1, 2):
            got = c35.sweep([c35.run(c35.rows(u)) for u in range(3)], budget=0.2,
                            workers=w, account=[5.0])
            s = got.telemetry["paid_summary"]
            self.assertEqual((s["budget_usd"], s["planned_bounded_exposure_usd"],
                              s["budget_holds_planned_bounded_exposure"]),
                             ("0.2", "0.405", False), w)
            if w is not SERIAL:
                self.assertEqual(got.execution["exposure"]["budget_usd"], "0.2")
                self.assertEqual(len(got.client.kinds("start")), 1)

    def test_above_the_plans_exposure_it_is_the_budget_as_given(self):
        got = self.indeed(5.0)
        self.assertEqual(got.execution["exposure"]["budget_usd"], "5.0")
        self.assertEqual(len(got.client.kinds("start")), 3)
        self.assertTrue(got.telemetry["paid_summary"]["budget_holds_planned_bounded_exposure"])

    def test_no_form_field_sets_a_cap(self):
        from sweep.logic import _configure_overrides
        got = _configure_overrides({"max_spend_usd": "0.10", "max_results": "15"})
        self.assertNotIn("max_spend_usd", got)

    def test_the_worker_renders_a_supplied_cap_verbatim(self):
        from deploy import sweep_worker
        from sweep.tests import test_app as app_tests
        from sweep.app import _prefs
        make_profile = sweep_worker._import_make_profile(str(ROOT))
        source = sweep_worker.render_profile(
            make_profile, "c4probe", app_tests.DERIVED, _prefs({"max_spend_usd": 0.1}),
            "/tmp/c4probe")
        self.assertIn('"max_spend_usd": 0.1,', source)


# ===========================================================================
# 2. The provider's own rank
# ===========================================================================
def link(job_id, position=None, extra="&pageNum=0&refId=r&trackingId=t"):
    base = f"https://www.linkedin.com/jobs/view/{job_id}"
    return base if position is None else f"{base}?position={position}{extra}"


def li_item(job_id, company, position, title="Full Stack Engineer", score_words=RICH):
    item = c1.li(job_id, title, company, score_words)
    item["link"] = link(job_id, position) if isinstance(position, int) else position
    return item


class ProviderPositions(unittest.TestCase):

    def got(self, links, site="linkedin"):
        return scraper.provider_positions(site, links)

    def test_valid_positions_1_and_15(self):
        self.assertEqual(self.got([link("a", 1), link("b", 15)]), [1, 15])

    def test_malformed_is_unknown(self):
        bad = [link("a", "x").replace("x", "") + "?position=abc",
               "https://www.linkedin.com/jobs/view/b?position=0",
               "https://www.linkedin.com/jobs/view/c?position=-3",
               "https://www.linkedin.com/jobs/view/d?position=7.5",
               "https://www.linkedin.com/jobs/view/e?position=",
               "https://www.linkedin.com/jobs/view/f?position=3&position=4"]
        self.assertEqual(self.got(bad), [None] * len(bad))

    def test_missing_is_unknown_never_the_dataset_index(self):
        self.assertEqual(self.got([link("a", 3), link("b"), link("c", 1)]), [3, None, 1])

    def test_no_link_is_unknown(self):
        self.assertEqual(self.got([None, "", 42]), [None, None, None])

    def test_a_duplicate_claim_makes_both_unknown(self):
        self.assertEqual(self.got([link("a", 2), link("b", 2), link("c", 5)]),
                         [None, None, 5])

    def test_past_the_requested_depth_is_kept(self):
        """An overshooting run's 16th result is its 16th (B1: 16-18 rows)."""
        self.assertEqual(self.got([link("a", 16), link("b", 99)]), [16, 99])

    def test_indeed_and_naukri_have_no_provider_rank(self):
        urls = ["https://www.indeed.com/viewjob?jk=1&position=1"]
        self.assertEqual(self.got(urls, "indeed"), [None])
        self.assertEqual(self.got(urls, "naukri"), [None])


class ProviderPositionsEndToEnd(unittest.TestCase):
    """One LinkedIn search whose dataset order is the REVERSE of LinkedIn's
    rank — the push order C3 measured — through scraper.main()."""

    ITEMS = [li_item(f"j{p}", f"Pos {p} Works", p) for p in (5, 4, 3, 2, 1)]

    @classmethod
    def setUpClass(cls):
        cls.on = c2.c2_sweep([Script(cls.ITEMS)], env={MODE: None})
        cls.off = c2.c2_sweep([Script(cls.ITEMS)], on=False, env={MODE: None})

    def test_the_record_holds_the_providers_rank_in_dataset_order(self):
        (unit,) = self.on.telemetry["paid_units"]
        self.assertEqual(unit["provider_positions"], [5, 4, 3, 2, 1])
        self.assertEqual(len(unit["trace"].split()), 5)

    def test_search_rank_is_still_the_dataset_index(self):
        for row in self.on.rows:
            position = int(re.search(r"position=(\d+)", row["apply_url"]).group(1))
            self.assertEqual(int(row["search_rank"]), 6 - position)

    def test_output_bytes_do_not_see_it(self):
        self.assertEqual((self.on.csv, self.on.json), (self.off.csv, self.off.json))
        self.assertNotIn("provider_position", self.on.csv.decode())

    def test_the_summary_buckets_by_the_providers_rank(self):
        b = self.on.telemetry["paid_summary"]["by_provider_position"]["linkedin"]
        self.assertEqual(list(b), ["1-5"])
        self.assertEqual((b["1-5"]["rows"], b["1-5"]["final"],
                          b["1-5"]["beyond_requested_depth"]), (5, 5, 0))

    def test_unknown_and_indeed_are_counted_as_unknown(self):
        items = [li_item("a", "A Works", 1), li_item("b", "B Works", link("b")),
                 li_item("c", "C Works", 2), li_item("d", "D Works", 2)]
        got = c2.c2_sweep([Script(items)], env={MODE: None})
        (unit,) = got.telemetry["paid_units"]
        self.assertEqual(unit["provider_positions"], [1, None, None, None])
        b = got.telemetry["paid_summary"]["by_provider_position"]["linkedin"]
        self.assertEqual((b["1-5"]["rows"], b["unknown"]["rows"]), (1, 3))
        ind = c35.sweep([c35.run(c35.rows(0, 3))])
        self.assertEqual(ind.telemetry["paid_units"][0]["provider_positions"],
                         [None] * 3)
        self.assertEqual(list(ind.telemetry["paid_summary"]["by_provider_position"]
                              ["indeed"]), ["unknown"])


# ===========================================================================
# 3. Shadow: the same work, request for request, byte for byte
# ===========================================================================
LI_IN = ("linkedin", "indeed")
PLACES = {"linkedin": ["Bengaluru", "Remote"], "indeed": ["Bengaluru"]}


def mixed():
    """LinkedIn K0/K1 x {Bengaluru, Remote} (two structural pairs), then
    Indeed K0/K1. A twin posting in every search; each Remote twin repeats
    two of its place twin's jobs; dataset order against LinkedIn's rank; the
    first planned search finishes last."""
    scripts = []
    for u in range(4):
        kw, remote = u // 2, u % 2
        own = [li_item(f"{u}{j}", f"Solo {u}x{j}", 15 - j * 5) for j in range(3)]
        shared = [li_item(f"{kw}s{j}", f"Pair {kw}x{j}", 2 + j) for j in range(2)]
        twin = [li_item(f"tw{u}", "Twin Works", 1)]
        scripts.append(Script(own + shared + twin, polls=2,
                              delay={"get": 0.02 * (4 - u)}))
    for u in range(2):
        scripts.append(c35.run(c35.rows(u) + [c35.twin(f"9{u}")], polls=2,
                               delay={"get": 0.02 * (2 - u)}))
    return scripts


def swept(mode, workers=SERIAL, **kw):
    kw.setdefault("free", True)
    kw.setdefault("account", [1.0, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06])
    return c2.c2_sweep(mixed(), keywords=KW[:2], site_locations=PLACES, sites=LI_IN,
                       workers=workers, env={MODE: mode}, **kw)


def untimed(value):
    if isinstance(value, dict):
        return {k: untimed(v) for k, v in value.items()
                if not re.search(r"(_ms|_at|_s)$", k) and k != "elapsed_ms"}
    if isinstance(value, list):
        return [untimed(v) for v in value]
    return value


def starts(got):
    return sorted(json.dumps([c[1], c[2], str(c[3])], sort_keys=True)
                  for c in got.client.kinds("start"))


class ShadowIsOff(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.runs = {(m, w): swept(m, w) for m in (None, "shadow", "enforce")
                    for w in (SERIAL, 1, 2, 4)}

    def test_the_same_requests(self):
        for (m, w), got in self.runs.items():
            off = self.runs[(None, w)]
            self.assertEqual(starts(got), starts(off), (m, w))
            self.assertEqual(Counter(c[0] for c in got.client.calls),
                             Counter(c[0] for c in off.client.calls), (m, w))
            if w in (SERIAL, 1):                 # one thread: the exact sequence
                self.assertEqual(got.client.calls, off.client.calls, (m, w))

    def test_the_same_inputs_depths_and_ceilings(self):
        for (m, w), got in self.runs.items():
            for _, actor, run_input, ceiling in got.client.kinds("start"):
                depth = run_input.get("limitPerSource", run_input.get("maxItemsPerSearch"))
                self.assertEqual(depth, 15, (m, w))
                self.assertEqual(ceiling, Decimal("0.046") if actor == LINKEDIN
                                 else Decimal("0.135"), (m, w))

    def test_the_same_bytes_ledgers_ranking_and_survivor(self):
        for (m, w), got in self.runs.items():
            off = self.runs[(None, SERIAL)]
            self.assertGreater(len(got.rows), 10)
            self.assertEqual((got.csv, got.json, got.seen, got.done),
                             (off.csv, off.json, off.seen, off.done), (m, w))
            twins = [r["apply_url"] for r in got.rows if r["company"] == "Twin Works"]
            self.assertEqual(len(twins), 1, (m, w))

    def test_the_same_reservations(self):
        for (m, w), got in self.runs.items():
            if w is SERIAL:
                self.assertNotIn("paid_execution", got.telemetry)
                continue
            off = self.runs[(None, w)].execution
            ex = got.execution
            self.assertEqual(untimed(ex["exposure"]), untimed(off["exposure"]), (m, w))
            self.assertEqual([(u["unit_id"], u["reservation"], u["reserved_usd"])
                              for u in ex["units"]],
                             [(u["unit_id"], u["reservation"], u["reserved_usd"])
                              for u in off["units"]], (m, w))

    def test_the_c1_record_is_untouched(self):
        for (m, w), got in self.runs.items():
            off = self.runs[(None, w)].telemetry
            self.assertEqual(got.telemetry["paid_units"], off["paid_units"], (m, w))
            self.assertEqual(untimed(got.telemetry["paid_summary"]),
                             untimed(off["paid_summary"]), (m, w))

    def test_only_shadow_and_enforce_write_the_section(self):
        for (m, w), got in self.runs.items():
            section = got.telemetry.get("paid_adaptive")
            if m is None:
                self.assertIsNone(section, w)
                continue
            self.assertEqual((section["mode"], section["requested_mode"],
                              section["enforce_available"], section["promoted"]),
                             ("shadow", m, False, []), w)
            self.assertEqual((section["logical_planned"], section["logical_executed"],
                              section["physical_starts"]), (6, 6, 6), w)
            self.assertEqual([o["unit_id"] for o in section["observations"]],
                             [f"paid_00{u}" for u in range(6)], w)

    def test_decisions_do_not_depend_on_the_schedule(self):
        def decisions(got):
            return [(c["candidate_id"], [(d["unit_id"], d["action"]) for d in c["decisions"]],
                     c["loss"]) for c in got.telemetry["paid_adaptive"]["candidates"]]
        base = decisions(self.runs[("shadow", SERIAL)])
        for w in (1, 2, 4):
            self.assertEqual(decisions(self.runs[("shadow", w)]), base, w)
            self.assertEqual(decisions(self.runs[("enforce", w)]), base, w)

    def test_enforce_says_it_ran_as_shadow(self):
        notes = self.runs[("enforce", 2)].telemetry["notes"]
        self.assertTrue(any("ran as shadow" in n for n in notes), notes)

    def test_shadow_without_telemetry_is_a_no_op(self):
        off = swept(None, 2, on=False)
        shadow = swept("shadow", 2, on=False)
        self.assertEqual((shadow.csv, shadow.json, shadow.done), (off.csv, off.json, off.done))
        self.assertIsNone(shadow.telemetry)
        self.assertFalse(paid_adaptive.active())

    def test_the_output_directory_holds_nothing_new(self):
        """No skip ledger, no second record: the files off writes, by name."""
        def names(got):
            return sorted(re.sub(r"sweep_[0-9a-f]+|\d{4}-\d\d-\d\d_\d{4}", "<id>", f)
                          for f in got.files)
        self.assertEqual(names(self.runs[("shadow", 2)]), names(self.runs[(None, 2)]))


class BudgetSafetyUnderShadow(unittest.TestCase):
    """A budget with room for two LinkedIn ceilings: the same two starts, the
    same block, the same held exposure, whatever the mode."""

    def test_the_reservation_ledger_is_the_same(self):
        for w in (1, 2):
            off, shadow = (swept(m, w, budget=0.1, account=[5.0]) for m in (None, "shadow"))
            self.assertEqual(len(shadow.client.kinds("start")), 2)
            self.assertEqual(untimed(shadow.execution["exposure"]),
                             untimed(off.execution["exposure"]), w)
            self.assertEqual(shadow.execution["exposure"]["blocked"], 1)
            self.assertEqual(shadow.status(), off.status())
            self.assertEqual(shadow.telemetry["paid_adaptive"]["logical_executed"], 2)


class ResumeUnderShadow(unittest.TestCase):

    def test_a_resumed_sweep_observes_only_what_it_ran(self):
        done = [f"linkedin|{KW[0]}|Bengaluru", f"linkedin|{KW[0]}|Remote"]
        off = swept(None, 2, done=done)
        shadow = swept("shadow", 2, done=done)
        self.assertEqual((shadow.csv, shadow.done), (off.csv, off.done))
        self.assertEqual(shadow.status()[:2], ["skipped_done", "skipped_done"])
        section = shadow.telemetry["paid_adaptive"]
        self.assertEqual([o["unit_id"] for o in section["observations"]],
                         ["paid_002", "paid_003", "paid_004", "paid_005"])
        # .done_combos keeps its one meaning: searches completed and checkpointed.
        self.assertEqual(len(shadow.done), len(off.done))


# ===========================================================================
# 4. Decisions from the past only; losses against the whole result
# ===========================================================================
def fixture(name):
    return tool.run_fixture(name)


def candidate(report, cid):
    return next(c for c in report["candidates"] if c["candidate_id"] == cid)


class TheRankIsRankRows(unittest.TestCase):
    """paid_adaptive.rank is scraper.rank_rows on projections: same order,
    same tie-break (arrival), same survivor per job, unkeyed rows kept."""

    def test_equal_on_ties_duplicates_and_unkeyed_rows(self):
        rows = [{"Title": "Backend Engineer", "Company": "Beta", "score": 50},
                {"Title": "Engineer, Backend", "Company": "Beta Ltd", "score": 50},
                {"Title": "Frontend Engineer", "Company": "Gamma", "score": 70},
                {"Title": "Data Engineer", "Company": "Delta", "score": 50},
                {"Title": "", "Company": "", "score": 60},
                {"Title": "", "Company": "", "score": 60},
                {"Title": "Frontend Engineer", "Company": "Gamma", "score": 90}]
        for r in rows:
            r.setdefault("Job URL", "")
        expected = [id(r) for r in scraper.rank_rows(list(rows))]
        proj = [(id(r), scraper.job_key(r) or ("unkeyed", id(r)), r["score"], None)
                for r in rows]
        self.assertEqual([o for o, _, _ in paid_adaptive.rank(proj)], expected)


class NoFutureInformation(unittest.TestCase):

    def test_every_decision_is_the_one_its_prefix_makes(self):
        """Online: what a policy decides at search i is what it decides when
        the sweep ends at i. Checked for every candidate, fixture and cut."""
        for name in tool.FIXTURES:
            obs = fixture(name)["observations"]
            for family, params in paid_adaptive.CANDIDATES:
                full = paid_adaptive.simulate(family, params, obs)
                for cut in range(1, len(obs) + 1):
                    self.assertEqual(
                        paid_adaptive.simulate(family, params, obs[:cut]),
                        [d for d in full if d["plan_index"] < obs[cut - 1]["plan_index"] + 1],
                        (name, family, params, cut))

    def test_a_different_future_changes_no_past_decision(self):
        obs = fixture("dry_spell_then_value")["observations"]
        fired = paid_adaptive.simulate("marginal_streak", {"n": 2}, obs)
        future = copy.deepcopy(obs)
        for o in future[fired[0]["plan_index"] + 1:]:
            o.update(final_marginal=99)
        self.assertEqual(paid_adaptive.simulate("marginal_streak", {"n": 2}, future), fired)

    def test_live_observations_do_not_see_later_searches(self):
        """The observation of search i is the same whether or not searches
        after it were planned at all."""
        four = swept("shadow", free=False)
        two = c2.c2_sweep(mixed()[:2], keywords=KW[:1], site_locations=PLACES,
                          sites=("linkedin",), env={MODE: "shadow"}, account=[1.0])
        a = untimed(four.telemetry["paid_adaptive"]["observations"][:2])
        b = untimed(two.telemetry["paid_adaptive"]["observations"])
        for o in a + b:
            o.pop("committed_usd")
        self.assertEqual(a, b)

    def test_the_same_inputs_make_the_same_trace(self):
        self.assertEqual(fixture("dry_spell_then_value"), fixture("dry_spell_then_value"))


class LossesAreCountedAgainstTheWholeResult(unittest.TestCase):

    def test_the_top_k_fixture_shows_the_rank_one_job_a_naive_stop_misses(self):
        """Search 1 fills the top 20, 2-5 add low jobs, 6 adds a new rank 1."""
        r = fixture("top20_stable_then_displaced")
        c = candidate(r, "top_k_stable:k=20,m=3")
        self.assertEqual(c["would_stop_after"], "paid_003")
        self.assertEqual(c["loss"]["best_lost_rank"], 1)
        self.assertGreaterEqual(c["loss"]["top10_lost"], 1)
        self.assertGreater(c["savings"]["logical_avoided"], 0)
        after = {u["unit_id"]: u["if_stopped_after"] for u in r["per_unit"]}
        self.assertEqual(after["paid_004"]["best_lost_rank"], 1)
        self.assertEqual(after["paid_005"]["final_lost"], 0)

    def test_a_dry_spell_breaks_every_streak_rule_it_outlasts(self):
        r = fixture("dry_spell_then_value")
        for n in (2, 3, 5):
            c = candidate(r, f"marginal_streak:n={n}")
            self.assertIsNotNone(c["would_stop_after"], n)
            self.assertEqual(c["loss"]["best_lost_rank"], 1, n)

    def test_where_the_tail_really_is_dry_the_same_rule_loses_nothing(self):
        r = fixture("early_then_dry")
        c = candidate(r, "marginal_streak:n=2")
        self.assertEqual(c["loss"]["final_lost"], 0)
        self.assertEqual(c["savings"]["logical_avoided"], 5)
        self.assertEqual(c["savings"]["max_exposure_avoided_usd"], "0.230")

    def test_useful_to_the_end_nothing_stops(self):
        r = fixture("all_useful")
        for c in r["candidates"]:
            if c["family"] in ("marginal_streak", "top_k_stable",
                               "provider_marginal_streak"):
                self.assertEqual(c["decisions"], [], c["candidate_id"])

    def test_a_duplicate_rate_is_not_redundancy(self):
        r = fixture("duplicate_heavy_late_unique")
        self.assertTrue(all(o["acquired_repeat_of_earlier_paid"] == 4
                            for o in r["observations"][1:]))
        self.assertEqual(candidate(r, "marginal_streak:n=2")["decisions"], [])

    def test_a_stable_top_20_can_still_lose_the_rest(self):
        c = candidate(fixture("top20_stable_total_growing"), "top_k_stable:k=20,m=3")
        self.assertEqual((c["loss"]["top20_lost"], c["loss"]["final_lost"]), (0, 6))

    def test_one_provider_can_stop_while_the_other_runs(self):
        r = fixture("provider_a_low_b_high")
        own = candidate(r, "provider_marginal_streak:n=3")
        self.assertEqual((own["would_skip"], own["loss"]["final_lost"]), (2, 0))
        self.assertEqual(own["decisions"][0]["scope"], {"provider": "linkedin"})
        self.assertGreater(candidate(r, "marginal_streak:n=2")["loss"]["final_lost"], 0)

    def test_a_job_two_skipped_searches_share_is_one_lost_job(self):
        truth = [("paid_000", "a", 10, 1), ("paid_001", "x", 50, 1),
                 ("paid_002", "x", 40, 1), ("paid_002", "b", 5, 2)]
        full = paid_adaptive.rank(truth)
        cf = paid_adaptive.counterfactual(truth, lambda o, p: o == "paid_000")
        lost = paid_adaptive.loss(full, cf, truth)
        self.assertEqual((lost["final_lost"], lost["top10_lost"],
                          lost["final_marginal_lost"], lost["best_lost_rank"]),
                         (2, 2, 1, 1))


class StructuralPairs(unittest.TestCase):

    def test_high_overlap(self):
        r = fixture("pair_high_overlap")
        self.assertEqual([p["overlap_with_places"] for p in r["pairs"]], [10, 10, 9, 9, 9])
        self.assertEqual([p["if_remote_skipped"]["final_lost"] for p in r["pairs"]],
                         [0, 0, 1, 1, 1])
        c = candidate(r, "remote_after_places:m=1")
        self.assertEqual(c["decisions"][0]["unit_id"], "paid_001")
        self.assertEqual(c["loss"]["final_lost"], 3)          # the three it could not see

    def test_zero_overlap(self):
        r = fixture("pair_zero_overlap")
        self.assertEqual([p["overlap_with_places"] for p in r["pairs"]], [0] * 5)
        self.assertEqual({p["if_remote_skipped"]["final_lost"] for p in r["pairs"]}, {10})
        self.assertEqual(candidate(r, "remote_after_places:m=1")["decisions"], [])


class DepthByProviderPosition(unittest.TestCase):

    def test_front_loaded_depth_5_loses_nothing_that_mattered(self):
        d = fixture("depth_front_loaded")["depth"]["linkedin"]
        self.assertEqual((d["5"]["loss"]["final_marginal_lost"], d["5"]["loss"]["top10_lost"]),
                         (0, 0))
        self.assertEqual(d["15"]["final"], d["5"]["final"] + 10)

    def test_back_loaded_depth_10_loses_the_top(self):
        r = fixture("depth_back_loaded")
        d = r["depth"]["linkedin"]
        self.assertEqual(d["10"]["loss"]["best_lost_rank"], 1)
        self.assertGreater(d["10"]["loss"]["top10_lost"], 0)
        self.assertEqual(candidate(r, "depth_prefix:d=10,m=3")["decisions"], [])

    def test_indeed_depth_is_unknown(self):
        d = fixture("provider_a_low_b_high")["depth"]["indeed"]
        self.assertEqual(d["status"], "UNKNOWN")

    def test_live_positions_not_push_order(self):
        """mixed(): each LinkedIn search's own jobs sit at positions 15, 10, 5
        in dataset slots 1-3. By the provider's rank, the top slot is 15."""
        got = swept("shadow", free=False)
        o = got.telemetry["paid_adaptive"]["observations"][0]
        self.assertEqual(o["final_by_position"], {"11-15": 1, "6-10": 1, "1-5": 4})
        d = got.telemetry["paid_adaptive"]["depth"]["linkedin"]
        self.assertEqual(d["5"]["final"] + 4 * 2, d["15"]["final"])

    def test_the_captured_traces_reproduce_c3s_rebucketing(self):
        rep = tool.captured_report(tool.captured())
        b = rep["linkedin_by_provider_position"]
        self.assertEqual([b[k]["final"] for k in ("1-5", "6-10", "11-15")], [20, 21, 22])
        self.assertEqual([b[k]["rows"] for k in ("1-5", "6-10", "11-15")], [25, 25, 25])
        self.assertEqual(rep["stopping_policies_exercised"], {})


# ===========================================================================
# 5. Mode, privacy, reachability
# ===========================================================================
class ModeDefaults(unittest.TestCase):

    def test_off_unless_asked(self):
        self.assertEqual(paid_adaptive.mode({}), "off")
        for value, want in (("shadow", "shadow"), (" Shadow ", "shadow"),
                            ("enforce", "enforce"), ("1", "off"), ("on", "off"),
                            ("", "off"), ("shadow!", "off")):
            self.assertEqual(paid_adaptive.mode({MODE: value}), want, value)

    def test_nothing_is_promoted_and_nothing_can_enforce(self):
        self.assertEqual(paid_adaptive.PROMOTED, ())
        r = fixture("early_then_dry")
        self.assertFalse(any(c["promoted"] for c in r["candidates"]))

    def test_a_sweep_without_the_variable_writes_no_section(self):
        got = c2.c2_sweep([Script(c2.solo(0))], env={MODE: None})
        self.assertNotIn("paid_adaptive", got.telemetry)

    def test_no_deployment_file_sets_it(self):
        f = re.escape(MODE)
        for path in ("render.yaml", "deploy/sweep_worker.py", "requirements.txt",
                     "gunicorn.conf.py", "config.py"):
            body = (ROOT / path).read_text(encoding="utf-8")
            self.assertEqual(re.findall(
                r"""\benv\[["']%(f)s["']\]\s*=|\bkey:\s*%(f)s\b|^\s*%(f)s\s*=""" % {"f": f},
                body, re.M), [], path)


class Privacy(unittest.TestCase):

    def test_the_section_holds_no_row_query_url_or_token(self):
        items = [li_item("MARKERURL1", "MARKERCO", 1, title="MARKERTITLE Engineer"),
                 li_item("MARKERURL2", "Acme", 2)]
        got = c2.c2_sweep([Script(items)], keywords=("QUERYMARKER Engineer",),
                          env={MODE: "shadow"})
        # Lower-cased on both sides: a job key is the NORMALISED company and
        # title ("markerco"), so a leaked key would pass a case-exact check.
        body = json.dumps(got.telemetry["paid_adaptive"]).lower()
        for marker in ("MARKERURL", "MARKERCO", "MARKERTITLE", "QUERYMARKER",
                       "Bengaluru", "linkedin.com", c1.TOKEN, "acme"):
            self.assertNotIn(marker.lower(), body)
        self.assertNotIn("QUERYMARKER", json.dumps(got.telemetry["paid_units"]))


class ReplayToolIsUnreachable(unittest.TestCase):
    """The replay tool imports paid_adaptive and the standard library only:
    it cannot reach the engine, a client, the guard or a launcher."""

    PATH = ROOT / "bench" / "search_v2_paid_adaptive.py"

    def test_its_imports(self):
        tree = ast.parse(self.PATH.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertLessEqual(names, {"argparse", "json", "sys", "collections",
                                     "datetime", "pathlib", "decimal", "paid_adaptive"})

    def test_the_c0_scan_finds_nothing_to_classify(self):
        self.assertIsNone(c0.REACH_PATTERN.search(self.PATH.read_text(encoding="utf-8")))
        self.assertNotIn("bench/search_v2_paid_adaptive.py", c0.REACH)

    def test_paid_adaptive_itself_imports_no_engine(self):
        tree = ast.parse((ROOT / "paid_adaptive.py").read_text(encoding="utf-8"))
        mods = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertLessEqual(mods, {"os", "time", "collections", "decimal", "telemetry"})


if __name__ == "__main__":
    unittest.main()
