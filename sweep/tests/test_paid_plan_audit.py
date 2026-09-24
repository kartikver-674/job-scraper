"""Search Engine V2-C3: the paid plan audit's claims, pinned on the current
planner — no exact execution duplicate in the default plan, one would be seen
if a plan made it, and no profile made from a real person's résumé is read.
Offline: the planner and adapters only, no client."""
import re
import subprocess
import unittest
from pathlib import Path

import config
import scraper
from bench import search_v2_paid_compaction as audit

ROOT = Path(__file__).resolve().parents[2]


class PlanAudit(unittest.TestCase):

    def test_the_default_plan_has_no_exact_duplicate(self):
        with audit.lowered(scraper, config, None):
            report = audit.audit_plan(config, "defaults", audit.current_plan(scraper))
        li, ind = report["by_provider"]["linkedin"], report["by_provider"]["indeed"]
        self.assertEqual((li["logical_searches"], ind["logical_searches"]), (18, 72))
        self.assertEqual(report["current_physical_starts"], 90)
        for site in (li, ind):
            self.assertEqual((site["exact_execution_duplicates"], site["repeated_combos"]),
                             ([], []))
        self.assertEqual((li["bounded"], ind["bounded"]), (True, False))
        self.assertEqual(li["hypothetical_physical_starts_by_batch_size"],
                         {1: 18, 2: 9, 3: 6, 4: 5})
        self.assertEqual(li["overlap_candidates"]
                         ["linkedin_place_with_and_without_remote_pairs"], 9)

    def test_a_planted_exact_duplicate_is_caught(self):
        """Gurgaon and Gurugram are one LinkedIn geoId: two combos, one input.
        The public picker drops Gurugram for exactly this reason."""
        case = {"keywords": ["Software Engineer"], "locations": ["Delhi"],
                "linkedin_locations": ["Gurgaon", "Gurugram"]}
        with audit.lowered(scraper, config, case):
            report = audit.audit_plan(config, "planted", audit.current_plan(scraper))
        li = report["by_provider"]["linkedin"]
        self.assertEqual(li["exact_execution_duplicates"], [[0, 1]])
        self.assertEqual(li["repeated_combos"], [])
        self.assertEqual(li["hypothetical_physical_starts_by_batch_size"][2], 2)

    def test_no_profile_made_from_a_resume_is_read(self):
        tracked = subprocess.run(["git", "ls-files", "profiles"], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout
        for name in audit.PROFILES:
            self.assertIn(f"profiles/{name}.py", tracked)
            self.assertFalse(re.search(r"^cmp|kartik|sarthak", name), name)


if __name__ == "__main__":
    unittest.main()
