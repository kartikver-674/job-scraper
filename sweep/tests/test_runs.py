import os
import tempfile
import unittest

from sweep import runs

TODAY = "2026-09-08"

RAW = {
    "profile": "kanav",
    "sites": {
        "linkedin": [
            {"keywords": "React Native Developer", "location": "India", "company": ""},
            {"keywords": "React Native Developer", "location": "Remote", "company": ""},
        ],
        "indeed": [
            {"keywords": "Full Stack Engineer", "location": "Pune", "company": ""},
        ],
    },
    "free_sources": 0,
}


class TestComboKeys(unittest.TestCase):
    def test_key_format_matches_the_engine_exactly(self):
        keys = runs.combo_keys(RAW, TODAY)
        self.assertEqual(
            keys[0], "2026-09-08|linkedin|React Native Developer|India|")

    def test_one_key_per_planned_search_in_plan_order(self):
        keys = runs.combo_keys(RAW, TODAY)
        self.assertEqual(len(keys), 3)
        self.assertTrue(keys[2].startswith("2026-09-08|indeed|"))

    def test_company_lands_in_the_fifth_field(self):
        raw = {"profile": "x", "sites": {"linkedin": [
            {"keywords": "SDE", "location": "India", "company": "Stripe"}]},
            "free_sources": 0}
        self.assertEqual(
            runs.combo_keys(raw, TODAY)[0], "2026-09-08|linkedin|SDE|India|Stripe")


class TestDoneKeys(unittest.TestCase):
    def _ledger(self, lines):
        fh = tempfile.NamedTemporaryFile("w", suffix=".done", delete=False)
        fh.write("\n".join(lines) + "\n")
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_missing_file_is_no_progress_not_an_error(self):
        self.assertEqual(runs.done_keys("/nonexistent/.done_combos", TODAY), set())

    def test_only_todays_lines_count(self):
        # The engine reloads the ledger filtered to today (scraper.py:1702), so
        # yesterday's work does NOT count as done and WILL be re-billed.
        path = self._ledger([
            "2026-09-07|linkedin|React Native Developer|India|",
            "2026-09-08|linkedin|React Native Developer|Remote|",
        ])
        done = runs.done_keys(path, TODAY)
        self.assertEqual(done, {"2026-09-08|linkedin|React Native Developer|Remote|"})


class TestProgress(unittest.TestCase):
    def test_counts_and_outstanding_split(self):
        planned = runs.combo_keys(RAW, TODAY)
        done = {planned[0]}
        p = runs.progress(planned, done)
        self.assertEqual(p["planned"], 3)
        self.assertEqual(p["done"], 1)
        self.assertEqual(p["outstanding"], 2)

    def test_tiles_carry_site_and_state_in_plan_order(self):
        planned = runs.combo_keys(RAW, TODAY)
        p = runs.progress(planned, {planned[0]})
        self.assertEqual([t["state"] for t in p["tiles"]],
                         ["done", "pending", "pending"])
        self.assertEqual([t["site"] for t in p["tiles"]],
                         ["linkedin", "linkedin", "indeed"])
        self.assertEqual(p["tiles"][1]["label"],
                         "React Native Developer @ Remote")

    def test_a_finished_sweep_has_nothing_outstanding(self):
        planned = runs.combo_keys(RAW, TODAY)
        p = runs.progress(planned, set(planned))
        self.assertEqual(p["done"], 3)
        self.assertEqual(p["outstanding"], 0)

    def test_an_empty_plan_does_not_divide_by_zero(self):
        p = runs.progress([], set())
        self.assertEqual(p["planned"], 0)
        self.assertEqual(p["fraction"], 0.0)


if __name__ == "__main__":
    unittest.main()
