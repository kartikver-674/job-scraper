import unittest

from sweep import plan


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


if __name__ == "__main__":
    unittest.main()
