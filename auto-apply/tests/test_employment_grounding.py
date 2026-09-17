"""The rows the date arithmetic is allowed to count.

check_grounding has always validated the FIELDS call — companies, skills,
titles, institutions. Employment rows come from a SECOND model call and
were never checked against the document at all, so the arithmetic that
scores 52/52 on the benchmark ran over whatever the model happened to
say. An offline row reading

    Initech Global Holdings / Principal Architect / March 1999 - Present

with no part of it anywhere in the résumé, was accepted as 27 years of
experience. Exact arithmetic over unsupported facts is still exact, and
still wrong, and both consumers of the number DROP jobs when it is wrong
(SEARCH["experience_years"], SETTINGS["max_experience_years"]).

These pin the check. The rule is deliberately the same one check_grounding
already uses: one bad row is a row to drop, most of them bad is a parse
that cannot be trusted row by row.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_APPLY = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(AUTO_APPLY)
for path in (REPO_ROOT, AUTO_APPLY):
    if path not in sys.path:
        sys.path.insert(0, path)

import local_extract as le  # noqa: E402

NOW = (2026, 9)

TEXT = """Kartik Verma
Dealermatix Technologies Pvt Ltd
Software Engineer (promoted from Software Engineer Trainee)
January 2025 - Present
Acme Corp - Backend Developer
March 2023 - December 2024
Skills: React Native, TypeScript, Apex
"""

FIELDS = {"name": "Kartik Verma",
          "companies": ["Dealermatix Technologies Pvt Ltd", "Acme Corp"],
          "skills": ["react native", "typescript", "apex"],
          "titles": ["Software Engineer", "Backend Developer"],
          "institutions": [], "years_experience": 1}

# A row every part of which is in TEXT.
REAL = {"company": "Acme Corp", "title": "Backend Developer",
        "start": "March 2023", "end": "December 2024", "relevant": True}

# The audit's synthetic row. Nothing about it appears in TEXT.
INVENTED = {"company": "Initech Global Holdings", "title": "Principal Architect",
            "start": "March 1999", "end": "Present", "relevant": True}


def route(rows, stated=1, text=TEXT):
    return le.route(dict(FIELDS, years_experience=stated),
                    {"employment": list(rows)}, text, now=NOW)


class TestAFabricatedRowIsNotExperience(unittest.TestCase):

    def test_the_invented_1999_row_does_not_become_27_years(self):
        """The audit's case, exactly. Before this check it was accepted."""
        out = route([INVENTED], stated=27)
        self.assertEqual(out["decision"], "escalate")
        self.assertIsNone(out["result"])
        self.assertTrue(any("not supported by the document" in r
                            for r in out["reasons"]), out["reasons"])

    def test_one_bad_row_among_good_ones_is_dropped_not_escalated(self):
        """Same share rule as check_grounding: a minority is a correction."""
        out = route([REAL, INVENTED], stated=27)
        self.assertEqual(out["decision"], "corrected")
        excluded = [c for c in out["corrections"] if c["action"] == "excluded"]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["removed"], [INVENTED])
        # ...and the arithmetic ran on the surviving row alone.
        self.assertEqual(out["result"]["years_experience"], 1)

    def test_a_majority_of_bad_rows_escalates(self):
        out = route([REAL, INVENTED, dict(INVENTED, company="Globex Widgets")],
                    stated=27)
        self.assertEqual(out["decision"], "escalate")
        self.assertIsNone(out["result"])

    def test_an_invented_employer_alone_is_enough(self):
        row = dict(REAL, company="Initech Global Holdings")
        self.assertIn("company", " ".join(le.row_problems(row, TEXT, NOW)))

    def test_an_invented_title_alone_is_enough(self):
        row = dict(REAL, title="Principal Architect")
        self.assertIn("title", " ".join(le.row_problems(row, TEXT, NOW)))

    def test_an_invented_year_alone_is_enough(self):
        row = dict(REAL, start="March 1999")
        self.assertIn("1999", " ".join(le.row_problems(row, TEXT, NOW)))


class TestImpossibleIntervals(unittest.TestCase):
    """months_between clamps at zero, so a reversed range silently
    contributed nothing and said nothing. Zero is the right number; the
    silence was the defect."""

    def test_a_reversed_range_is_reported_not_swallowed(self):
        backwards = dict(REAL, start="December 2024", end="March 2023")
        why = le.row_problems(backwards, TEXT, NOW)
        self.assertTrue(any("before it starts" in w for w in why), why)

    def test_a_reversed_range_is_excluded_from_the_arithmetic(self):
        backwards = {"company": "Dealermatix Technologies Pvt Ltd",
                     "title": "Software Engineer", "start": "December 2024",
                     "end": "March 2023", "relevant": True}
        out = route([REAL, backwards])
        self.assertEqual(out["decision"], "corrected")
        self.assertEqual([c["removed"] for c in out["corrections"]
                          if c["action"] == "excluded"], [[backwards]])

    def test_a_row_that_starts_in_the_future_is_reported(self):
        ahead = dict(REAL, start="March 2030", end="December 2031")
        why = le.row_problems(ahead, TEXT, NOW)
        self.assertTrue(any("future" in w for w in why), why)


class TestUnresolvedPromotions(unittest.TestCase):
    """"Software Engineer (promoted from Software Engineer Trainee)" is
    twenty months of which an unknown prefix does not count. The document
    does not say when the traineeship ended, so neither does Sweep."""

    ROW = {"company": "Dealermatix Technologies Pvt Ltd",
           "title": "Software Engineer (promoted from Software Engineer Trainee)",
           "start": "January 2025", "end": "Present", "relevant": True}

    def test_the_ambiguity_is_flagged_rather_than_guessed(self):
        out = route([self.ROW])
        flagged = [c for c in out["corrections"] if c["action"] == "flagged"]
        self.assertEqual(len(flagged), 1, out["corrections"])
        self.assertIn("unknown", flagged[0]["why"])
        self.assertIn("floor", flagged[0]["why"])

    def test_no_promotion_date_is_invented(self):
        """The count stays the conservative floor. What changed is that it
        is now SAID to be a floor."""
        out = route([self.ROW])
        self.assertEqual(out["result"]["years_experience"], 0)

    def test_an_ordinary_traineeship_is_not_flagged(self):
        """Only the unresolved case. A plain internship is clause 2, which
        countable() has always handled."""
        plain = dict(self.ROW, title="Software Engineer Trainee")
        self.assertFalse(le.ambiguous_span(plain))

    def test_an_ordinary_promotion_is_not_flagged(self):
        promoted = dict(self.ROW, title="Software Engineer (promoted)")
        self.assertFalse(le.ambiguous_span(promoted))


class TestValidRowsAreUntouched(unittest.TestCase):
    """The 52/52 arithmetic is the thing this must not cost."""

    def test_a_clean_row_still_accepts(self):
        out = route([REAL], stated=1)
        self.assertEqual(out["decision"], "accept")
        self.assertEqual(out["corrections"], [])

    def test_an_open_ended_row_is_not_asked_to_ground_the_word_present(self):
        """"Present" is a word, not a date. Requiring it in the document
        would reject every current job."""
        current = {"company": "Dealermatix Technologies Pvt Ltd",
                   "title": "Software Engineer", "start": "January 2025",
                   "end": "Present", "relevant": True}
        self.assertEqual(le.row_problems(current, TEXT, NOW), [])

    def test_every_present_word_parse_month_knows_is_accepted(self):
        for word in le.PRESENT_WORDS:
            row = {"company": "Acme Corp", "title": "Backend Developer",
                   "start": "March 2023", "end": word, "relevant": True}
            self.assertEqual(le.row_problems(row, TEXT, NOW), [], word)

    def test_an_unreadable_date_is_left_to_check_years(self):
        """Not a grounding failure. check_years already counts these as a
        floor, and dropping the row instead would lose a real job."""
        vague = dict(REAL, start="sometime", end="later")
        self.assertEqual(le.row_problems(vague, TEXT, NOW), [])

    def test_a_row_with_no_company_or_title_is_not_punished_for_it(self):
        sparse = {"start": "March 2023", "end": "December 2024", "relevant": True}
        self.assertEqual(le.row_problems(sparse, TEXT, NOW), [])

    def test_no_employment_rows_at_all_still_behaves(self):
        out = route([], stated=0)
        self.assertEqual(out["decision"], "accept")


class TestAMalformedAnswerFailsSafely(unittest.TestCase):
    """A schema-constrained decoder usually returns the right shape, and
    "usually" is the problem: these all reached row.get() and raised
    AttributeError several frames from the model that caused it. The guard
    is at the shared seam, so every path to the arithmetic is covered."""

    def decision(self, rows):
        return le.route(dict(FIELDS), rows, TEXT, now=NOW)["decision"]

    def test_rows_of_strings_escalate_rather_than_crash(self):
        self.assertEqual(self.decision({"employment": ["Backend Dev at X"]}),
                         "escalate")

    def test_employment_as_a_bare_string_escalates(self):
        self.assertEqual(self.decision({"employment": "Backend Developer"}),
                         "escalate")

    def test_employment_as_an_object_escalates(self):
        self.assertEqual(self.decision({"employment": {"company": "X"}}),
                         "escalate")

    def test_the_whole_answer_being_a_list_escalates(self):
        self.assertEqual(self.decision(["a", "b"]), "escalate")

    def test_one_bad_row_among_objects_still_escalates(self):
        """Not a partial parse: a list that is not uniformly objects is a
        malformed answer, not a row to drop."""
        self.assertEqual(self.decision({"employment": [REAL, "oops"]}),
                         "escalate")

    def test_the_reason_names_the_shape_it_got(self):
        out = le.route(dict(FIELDS), {"employment": "x"}, TEXT, now=NOW)
        self.assertIn("not objects", " ".join(out["reasons"]))

    def test_a_non_object_model_answer_is_refused_at_the_call(self):
        """_object guards the two local calls themselves, so the error
        names the model rather than surfacing as a type error later."""
        for bad in ([], "text", 3, None):
            with self.assertRaises(le.InferenceError):
                le._object(bad, "fields")

    def test_a_real_object_passes_through_unchanged(self):
        answer = {"employment": []}
        self.assertIs(le._object(answer, "employment"), answer)


if __name__ == "__main__":
    unittest.main()
