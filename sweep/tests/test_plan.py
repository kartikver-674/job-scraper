import os
import subprocess
import sys
import unittest

from sweep import plan

RATES = {"linkedin": 0.045, "indeed": 0.09}


def raw_at(depth, linkedin=32, indeed=14):
    """A plan whose every site runs at `depth` results per search."""
    return {
        "profile": "kanav",
        "sites": {
            "linkedin": [{"keywords": "A", "location": "India", "company": ""}] * linkedin,
            "indeed": [{"keywords": "A", "location": "Pune", "company": ""}] * indeed,
        },
        "max_results": {"linkedin": depth, "indeed": depth},
        "free_sources": 6,
    }


class TestCostPlan(unittest.TestCase):
    def test_each_line_multiplies_out_and_subtotals_sum_to_total(self):
        raw = {
            "profile": "kanav",
            "sites": {
                "linkedin": [{"keywords": "A", "location": "India", "company": ""}] * 32,
                "indeed": [{"keywords": "A", "location": "Pune", "company": ""}] * 14,
            },
            "free_sources": 6,
        }
        costed = plan.cost(raw, rates={"linkedin": 0.045, "indeed": 0.09})

        by_site = {line["site"]: line for line in costed["lines"]}
        self.assertEqual(by_site["linkedin"]["searches"], 32)
        self.assertAlmostEqual(by_site["linkedin"]["subtotal"], 1.44, places=2)
        self.assertAlmostEqual(by_site["indeed"]["subtotal"], 1.26, places=2)
        for line in costed["lines"]:
            self.assertAlmostEqual(
                line["subtotal"], line["searches"] * line["rate"], places=6)
        self.assertAlmostEqual(
            costed["total"], sum(l["subtotal"] for l in costed["lines"]), places=6)
        self.assertAlmostEqual(costed["total"], 2.70, places=2)

    def test_a_site_with_no_rate_is_free_and_marked_free(self):
        raw = {"profile": "x", "sites": {"remoteok": [{"keywords": "A", "location": "", "company": ""}] * 6}, "free_sources": 0}
        costed = plan.cost(raw, rates={"linkedin": 0.045})
        line = costed["lines"][0]
        self.assertEqual(line["subtotal"], 0.0)
        self.assertTrue(line["free"])
        self.assertEqual(costed["total"], 0.0)

    def test_total_searches_counts_every_planned_search(self):
        raw = {"profile": "x", "sites": {
            "linkedin": [{"keywords": "A", "location": "India", "company": ""}] * 3,
            "indeed": [{"keywords": "A", "location": "Pune", "company": ""}] * 2,
        }, "free_sources": 0}
        costed = plan.cost(raw, rates={"linkedin": 0.045, "indeed": 0.09})
        self.assertEqual(costed["total_searches"], 5)


class TestDepthIsPriced(unittest.TestCase):
    """max_results reaches the actors as maxItemsPerSearch / count / maxJobs
    (scraper.py:797,805,814) — the billable per-search result count on a
    pay-per-event actor. The Configure screen promises "every change updates
    the cost above", so the estimate has to move with it."""

    def test_doubling_the_depth_doubles_the_total(self):
        shallow = plan.cost(raw_at(25), RATES)
        deep = plan.cost(raw_at(50), RATES)
        self.assertAlmostEqual(shallow["total"], 2.70, places=2)
        self.assertAlmostEqual(deep["total"], shallow["total"] * 2, places=4)

    def test_the_rate_is_the_measured_one_at_the_basis_depth(self):
        # config.py:374 records the LinkedIn rate as "measured at
        # max_results=25", so at 25 nothing is scaled.
        line = {l["site"]: l for l in plan.cost(raw_at(25), RATES)["lines"]}
        self.assertAlmostEqual(line["linkedin"]["rate"], 0.045, places=6)

    def test_the_arithmetic_invariant_holds_at_a_non_default_depth(self):
        # rate stays the EFFECTIVE per-search rate, so rate x searches ==
        # subtotal and the subtotals still sum to the total.
        costed = plan.cost(raw_at(15), RATES)
        for line in costed["lines"]:
            self.assertAlmostEqual(
                line["subtotal"], line["searches"] * line["rate"], places=6)
        self.assertAlmostEqual(
            costed["total"], sum(l["subtotal"] for l in costed["lines"]), places=6)
        # 32 x 0.045 x 0.6 + 14 x 0.09 x 0.6
        self.assertAlmostEqual(costed["total"], 1.62, places=2)

    def test_a_free_site_stays_free_at_any_depth(self):
        raw = raw_at(200)
        costed = plan.cost(raw, rates={"linkedin": 0.045})
        indeed = [l for l in costed["lines"] if l["site"] == "indeed"][0]
        self.assertTrue(indeed["free"])
        self.assertEqual(indeed["subtotal"], 0.0)

    def test_a_plan_with_no_depth_map_costs_at_the_measured_basis(self):
        # An older plan JSON, or a site the scraper did not report a depth
        # for, must not silently reprice to zero.
        raw = raw_at(25)
        del raw["max_results"]
        self.assertAlmostEqual(plan.cost(raw, RATES)["total"], 2.70, places=2)


class TestDryRunJsonCarriesDepth(unittest.TestCase):
    """The estimate can only track depth if the plan JSON reports it, and it
    has to come from the same place the actor input does or the two diverge.

    Spawns the real `scraper.py --dry-run --json`, which is free: it returns
    before any actor is launched and before any output file is written.
    """

    BILLED_KEY = {"linkedin": "count", "indeed": "maxItemsPerSearch",
                  "naukri": "maxJobs"}

    def test_the_reported_depth_is_the_depth_the_actor_is_billed_for(self):
        # LinkedIn's actor requires count >= 10, so a plan asking for 5
        # results per search is still billed for 10 — and an estimate built
        # from the 5 under-states by half. Whatever the plan reports has to
        # be what the actor input carries, for every site.
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        import scraper
        for site, billed_key in self.BILLED_KEY.items():
            asked = {"max_results": 5, "keywords": "x", "location": "Remote",
                     "country": "IN", "experience_years": 2, "salary_min": None}
            effective = scraper.effective_search(site, asked)
            self.assertEqual(effective["max_results"],
                             scraper.build_input(site, effective)[billed_key],
                             site)
        # The equality above holds trivially if both sides read the same
        # field, so assert the floor itself: LinkedIn's actor will not accept
        # a count below 10 and bills for the 10 it returns.
        self.assertEqual(
            scraper.effective_search("linkedin", {"max_results": 5})["max_results"],
            10)
        # And it is a floor, not an override.
        self.assertEqual(
            scraper.effective_search("linkedin", {"max_results": 40})["max_results"],
            40)

    def test_the_plan_json_reports_a_depth_for_every_planned_site(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        raw = plan.fetch("kartik_reachable", runner=lambda argv: subprocess.run(
            argv, cwd=repo_root, capture_output=True, text=True, check=True).stdout)
        self.assertTrue(raw["sites"])
        self.assertEqual(set(raw["max_results"]), set(raw["sites"]))
        for site, depth in raw["max_results"].items():
            self.assertIsInstance(depth, int)
            self.assertGreater(depth, 0, site)


if __name__ == "__main__":
    unittest.main()
