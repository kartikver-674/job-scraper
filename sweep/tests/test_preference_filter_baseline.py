"""Current behaviour of the Sweep-preference filters no golden covers.

The one-track goldens exercise recency, pay, arrangement, geography and the
remote scopes' "unreachable" count, but never visa, employer-of-record or the
"rescued" restricted-remote path: in every golden those counts are 0. Pinned
here, through the real score_and_filter, BEFORE Multi-Track Phase 2B reuses
the same filter code for multi-track results.

Every SETTINGS value the filters read is set explicitly, so nothing depends on
config.py's defaults. Offline, synthetic rows.
"""
import copy
import unittest
from unittest import mock

import scraper
import skill_concepts

FILTERS = {"max_age_days": None, "min_comp_usd": None, "min_score": None,
           "drop_undated": False, "work_scope": None, "remote_scopes": [],
           "drop_no_visa": False, "require_eor": False,
           "keep_restricted_if_hires_home": True}


def row(n, location, description, **over):
    base = {"Source": "greenhouse:probe", "Title": "React Developer", "Company": f"Probe {n}",
            "Location": location, "Salary": "", "Experience": "", "Posted Date": "1 day ago",
            "Job URL": f"https://example.test/filters/{n}", "hires_home": "",
            "Description": f"React. {description}"}
    base.update(over)
    return base


ROWS = [
    row("visa-yes", "Worldwide, Remote", "Fully remote. We offer visa sponsorship."),
    row("visa-no", "Worldwide, Remote", "Fully remote. We do not offer visa sponsorship."),
    row("visa-silent", "Worldwide, Remote", "Fully remote."),
    row("eor-deel", "Worldwide, Remote", "Fully remote, hired through Deel."),
    row("restricted-hires-home", "Europe, Remote", "Remote in Europe.", hires_home="yes"),
    row("restricted-to-home", "India, Remote", "Remote for candidates in India."),
    row("restricted-abroad", "Germany, Remote", "Remote within Germany only.", hires_home="no"),
    row("onsite", "Bengaluru, Karnataka", "Onsite in our Bengaluru office."),
]


class PreferenceFilterBaseline(unittest.TestCase):

    def setUp(self):
        bound = skill_concepts._BOUND
        self.addCleanup(setattr, skill_concepts, "_BOUND", bound)
        skill_concepts.bind("v1")

    def run_filters(self, **settings):
        stages = []
        with mock.patch.dict(scraper.SETTINGS, dict(FILTERS, **settings)):
            kept, stats = scraper.score_and_filter(
                copy.deepcopy(ROWS), stage=lambda s, got: stages.append([s, len(got)]))
        return [r["Job URL"].rsplit("/", 1)[1] for r in kept], stats, stages

    def test_the_signals_the_filters_read(self):
        got = {}
        for r in copy.deepcopy(ROWS):
            scored = scraper.score_job(r)
            got[r["Job URL"].rsplit("/", 1)[1]] = (scored["remote_scope"], scored["visa"],
                                                    scored["eor"])
        self.assertEqual(got, {
            "visa-yes": ("worldwide", "yes", ""), "visa-no": ("worldwide", "no", ""),
            "visa-silent": ("worldwide", "", ""), "eor-deel": ("worldwide", "", "Deel"),
            "restricted-hires-home": ("restricted", "", ""),
            "restricted-to-home": ("restricted", "", ""),
            "restricted-abroad": ("restricted", "", ""), "onsite": ("onsite", "", "")})

    def test_no_filter_on_keeps_everything(self):
        kept, stats, _ = self.run_filters()
        self.assertEqual(len(kept), len(ROWS))
        self.assertEqual({k: v for k, v in stats.items() if v}, {})

    def test_drop_no_visa_drops_only_an_explicit_refusal(self):
        kept, stats, _ = self.run_filters(drop_no_visa=True)
        self.assertNotIn("visa-no", kept)
        self.assertIn("visa-silent", kept)
        self.assertEqual(stats["no_visa"], 1)

    def test_require_eor_keeps_only_a_named_provider(self):
        kept, stats, _ = self.run_filters(require_eor=True)
        self.assertEqual(kept, ["eor-deel"])
        self.assertEqual(stats["no_eor"], len(ROWS) - 1)

    def test_remote_scopes_rescue_restricted_roles_reachable_from_home(self):
        kept, stats, stages = self.run_filters(remote_scopes=["worldwide", "remote"])
        self.assertEqual(kept, ["visa-yes", "visa-no", "visa-silent", "eor-deel",
                                "restricted-hires-home", "restricted-to-home"])
        self.assertEqual((stats["rescued"], stats["unreachable"]), (2, 2))
        self.assertEqual(stages[-2:], [["post_salary_reachability_visa_eor", 6],
                                       ["post_location_eligible", 6]])

    def test_without_the_rescue_restricted_roles_are_unreachable(self):
        kept, stats, _ = self.run_filters(remote_scopes=["worldwide", "remote"],
                                          keep_restricted_if_hires_home=False)
        self.assertEqual(kept, ["visa-yes", "visa-no", "visa-silent", "eor-deel"])
        self.assertEqual((stats["rescued"], stats["unreachable"]), (0, 4))

    def test_the_filters_compose_in_order(self):
        kept, stats, stages = self.run_filters(remote_scopes=["worldwide", "remote"],
                                               drop_no_visa=True, require_eor=True)
        self.assertEqual(kept, ["eor-deel"])
        self.assertEqual((stats["unreachable"], stats["no_visa"], stats["no_eor"]), (2, 1, 4))
        self.assertEqual([s for s, _n in stages],
                         ["observed_normalized", "post_hard_filter_and_score", "post_recency",
                          "post_salary_reachability_visa_eor", "post_location_eligible"])


if __name__ == "__main__":
    unittest.main()
