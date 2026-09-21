"""A confirmed experience bar the candidate is years short of.

The reported defect: Hargun, 3 years 1 month, received postings demanding 6+.
`SETTINGS["max_experience_years"]` is rendered as years + 3 = 6 and the gate is
a strict `>`, so 6 was never going to be dropped.

What these tests defend, in order of how badly each would hurt:

  * a per-skill figure is never mistaken for the overall minimum. "3+ years
    total, 6+ years Salesforce" is a 3-year job, and reading it as 6 deletes a
    job the candidate could have had
  * a preference, a ceiling, a range's upper bound, a degree and the company's
    own age are never a bar
  * a title is never evidence. Senior/Staff/Lead/Principal drop nothing
  * both unknowns fail open
  * with the flag off, score_job behaves exactly as before

Candidate figures are months, because whole years are floored and a 2y11m
candidate must not be treated as 2y0m at a 2.0-year drop threshold.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import experience_guard                                        # noqa: E402

HARGUN = 37          # 3 years 1 month
SENIOR = 72          # 6 years


def act(jd, months=HARGUN):
    return experience_guard.assess(jd, months)["action"]


class HargunRegression(unittest.TestCase):
    """The reported case and the band either side of it, at 3y1m."""

    def test_six_plus_explicitly_required_is_dropped(self):
        self.assertEqual(act("We require 6+ years of experience."), "hard_drop")
        self.assertEqual(act("Minimum 6 years of experience required."),
                         "hard_drop")
        self.assertEqual(act("At least 6 years of professional experience."),
                         "hard_drop")

    def test_seven_and_eight_plus_are_dropped(self):
        self.assertEqual(act("7+ years of experience."), "hard_drop")
        self.assertEqual(act("8+ years of total experience required."),
                         "hard_drop")

    def test_five_plus_is_penalised_not_dropped(self):
        v = experience_guard.assess("5+ years of experience.", HARGUN)
        self.assertEqual(v["action"], "penalty")
        self.assertEqual(v["gap_years"], 1.92)

    def test_four_plus_is_kept_untouched(self):
        self.assertEqual(act("4+ years of experience."), "none")

    def test_preferred_six_plus_is_kept(self):
        self.assertEqual(act("6+ years of experience preferred."), "none")
        self.assertEqual(act("Ideally 6+ years of experience."), "none")
        self.assertEqual(act("8 years of experience would be a plus."), "none")

    def test_an_ambiguous_six_year_mention_is_kept(self):
        # No "+", no requirement cue, no range: the JD did not commit.
        self.assertEqual(act("The role involves 6 years of experience."),
                         "none")

    def test_the_verdict_is_structured_and_auditable(self):
        v = experience_guard.assess("Minimum 6 years of experience required.",
                                    HARGUN)
        self.assertEqual(v["candidate_years"], 3.08)
        self.assertEqual(v["job_min_years"], 6)
        self.assertEqual(v["requirement_type"], "required")
        self.assertEqual(v["confidence"], "high")
        self.assertEqual(v["gap_years"], 2.92)
        self.assertEqual(v["action"], "hard_drop")
        self.assertIn("6 years", v["evidence"])


class MultipleYearsExtraction(unittest.TestCase):
    """Several figures in one JD. The overall minimum, never the other one."""

    def test_total_plus_skill_specific_reads_the_total(self):
        jd = "5+ years total development experience and 1+ years React."
        self.assertEqual(experience_guard.required_minimum(jd)[0], 5)

    def test_a_larger_skill_figure_never_becomes_the_total(self):
        jd = "3+ years of total experience, 6+ years of Salesforce experience."
        self.assertEqual(experience_guard.required_minimum(jd)[0], 3)
        self.assertEqual(act(jd), "none")

    def test_overall_stated_alongside_a_smaller_skill_figure(self):
        jd = "Experience: 6+ years overall, 3+ years Salesforce."
        self.assertEqual(experience_guard.required_minimum(jd)[0], 6)
        self.assertEqual(act(jd), "hard_drop")

    def test_experience_in_a_named_technology_is_not_an_overall_bar(self):
        jd = "6+ years of experience in Salesforce Sales Cloud."
        self.assertEqual(experience_guard.required_minimum(jd)[0], None)
        self.assertEqual(act(jd), "none")


class NotABar(unittest.TestCase):
    """Figures that are not a minimum, or not experience at all."""

    def test_a_range_is_read_at_its_floor(self):
        self.assertEqual(act("3-6 years of experience."), "none")
        self.assertEqual(act("3 to 6 years of experience."), "none")
        self.assertEqual(experience_guard.required_minimum(
            "5-8 years of experience.")[0], 5)

    def test_a_ceiling_is_not_a_floor(self):
        self.assertEqual(act("Up to 6 years of experience."), "none")
        self.assertEqual(act("No more than 8 years of experience."), "none")

    def test_education_is_not_experience(self):
        for jd in ("A 4-year bachelor's degree is required.",
                   "Minimum 16 years of formal education.",
                   "6 years of full time education required."):
            self.assertIsNone(experience_guard.required_minimum(jd)[0], jd)

    def test_company_age_and_team_size_are_not_experience(self):
        for jd in ("Founded 6 years ago, we now hire engineers.",
                   "In business for over 12 years.",
                   "You will manage a team of 8.",
                   "Salary $100k per year."):
            self.assertIsNone(experience_guard.required_minimum(jd)[0], jd)

    def test_somebody_elses_years_are_not_the_applicants(self):
        jd = "Our team has 30 years of combined experience."
        self.assertIsNone(experience_guard.required_minimum(jd)[0])

    def test_a_title_alone_never_drops(self):
        for title in ("Senior Software Engineer", "Staff Engineer",
                      "Lead Business Analyst", "Principal Consultant"):
            self.assertEqual(act(title + " — join our team."), "none", title)


class FailOpen(unittest.TestCase):
    def test_candidate_experience_unknown(self):
        for months in (None, "", -1, "3"):
            v = experience_guard.assess("10+ years of experience required.",
                                        months)
            self.assertEqual(v["action"], "none", months)
            self.assertEqual(v["confidence"], "none")

    def test_job_experience_unknown(self):
        v = experience_guard.assess("A great place to work.", HARGUN)
        self.assertEqual(v["action"], "none")
        self.assertIsNone(v["job_min_years"])

    def test_empty_and_none_text(self):
        self.assertEqual(act(""), "none")
        self.assertEqual(act(None), "none")


class SeniorCandidate(unittest.TestCase):
    def test_six_years_clears_a_six_year_bar(self):
        self.assertEqual(act("6+ years of experience required.", SENIOR),
                         "none")

    def test_months_are_used_not_floored_years(self):
        # 2y11m against a 4-year bar is a 1.08-year gap, not 2.0. Floored
        # years would hard-drop this; months penalise it.
        self.assertEqual(act("4+ years of experience.", 35), "penalty")
        self.assertEqual(act("4+ years of experience.", 24), "hard_drop")


class LabelledPhrasings(unittest.TestCase):
    """The 44-sentence measurement set docs/experience-mismatch-guard-audit.md
    quotes. An OVER-read is a false-positive hard drop and must stay at zero;
    the two under-reads are known, named, and harmless."""

    UNDER_READ = {"Founded 6 years ago, we now have 3+ years of experience "
                  "shipping ML.",
                  "In business 12 years. Seeking 3 years of experience."}

    def test_no_over_reads_and_only_the_two_known_under_reads(self):
        sys.path.insert(0, os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "fixtures"))
        import experience_phrasings

        over, under = [], []
        for text, want in experience_phrasings.CASES:
            got = experience_guard.required_minimum(text)[0]
            if got == want:
                continue
            (over if want is None or (got is not None and got > want)
             else under).append(text)
        self.assertEqual(over, [], "an over-read is a false-positive drop")
        self.assertEqual(set(under), self.UNDER_READ)
        self.assertEqual(len(experience_phrasings.CASES), 44)


class Wiring(unittest.TestCase):
    """score_job, with the flag off and on."""

    JD = "We require 6+ years of professional experience."

    def setUp(self):
        import config
        import scraper
        self.scraper, self.config = scraper, config
        self._flag = os.environ.pop(experience_guard.FLAG, None)
        self._months = config.SETTINGS.get("candidate_experience_months")
        # Hargun's rendered profile exactly: years_experience 3, so
        # max_experience_years is 6 and the existing gate (a strict >) keeps
        # every 6+ posting. That is the regression under test.
        self._max = config.SETTINGS["max_experience_years"]
        config.SETTINGS["max_experience_years"] = 6
        config.SETTINGS["candidate_experience_months"] = HARGUN
        experience_guard.DROPPED.clear()

    def tearDown(self):
        self.config.SETTINGS["max_experience_years"] = self._max
        self.config.SETTINGS["candidate_experience_months"] = self._months
        if self._flag is None:
            os.environ.pop(experience_guard.FLAG, None)
        else:
            os.environ[experience_guard.FLAG] = self._flag
        experience_guard.DROPPED.clear()

    def _score(self):
        return self.scraper.score_job({"Title": "Business Analyst",
                                       "Description": self.JD})

    def test_flag_off_keeps_the_row(self):
        self.assertIsNotNone(self._score())
        self.assertEqual(experience_guard.DROPPED, [])

    def test_flag_on_drops_the_row_and_records_why(self):
        os.environ[experience_guard.FLAG] = "1"
        self.assertIsNone(self._score())
        self.assertEqual(len(experience_guard.DROPPED), 1)
        self.assertIn("Business Analyst", experience_guard.summary())

    def test_no_candidate_months_is_a_no_op_even_with_the_flag_on(self):
        os.environ[experience_guard.FLAG] = "1"
        self.config.SETTINGS["candidate_experience_months"] = None
        self.assertIsNotNone(self._score())
        self.assertEqual(experience_guard.DROPPED, [])


if __name__ == "__main__":
    unittest.main()
