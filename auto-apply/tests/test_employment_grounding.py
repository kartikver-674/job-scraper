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

import json
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

    def test_a_row_naming_nobody_cannot_be_checked_and_says_so(self):
        """CONTRACT CHANGE, deliberate.

        This test previously asserted the opposite — that a row with no
        employer and no title is fine. The independent review showed that
        is what let "1999 - Present" with no employer become 27 years:
        with nothing to anchor on, its dates were checked against the
        whole page and of course something matched.

        A row that names nobody cannot be located, so its dates cannot be
        attributed to it. check_years still treats a refused row as a
        floor rather than a zero, so the cost is a conservative number.
        """
        sparse = {"start": "March 2023", "end": "December 2024",
                  "relevant": True}
        why = le.row_problems(sparse, TEXT, NOW)
        self.assertTrue(why)
        self.assertIn("names no employer or title", why[0])

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


# --------------------------------------------------------------------------
# The production path, not the helpers
# --------------------------------------------------------------------------
#
# Every test above exercises route() or check_employment() directly. The
# independent review found the defect one frame further out: read() ignored
# the validated rows and recomputed from the model's originals, and
# local_profile.generate() then derived search titles from those same
# originals. Helper tests cannot see that, which is why these exist.

SOURCE = ("Dana Whitfield\nSoftware Engineer\n"
          "Professional Experience\n"
          "Northwind Robotics — Backend Engineer\n"
          "March 2023 - December 2024\n"
          "- Built billing services in Python.\n"
          "Education\n"
          "BSc Computer Science, 2018 - 2022\n")

SOURCE_FIELDS = {"name": "Dana Whitfield",
                 "companies": ["Northwind Robotics"],
                 "skills": ["python"], "titles": ["Backend Engineer"],
                 "institutions": [], "years_experience": 27}

GENUINE = {"company": "Northwind Robotics", "title": "Backend Engineer",
           "start": "March 2023", "end": "December 2024", "relevant": True}
FABRICATED = {"company": "Initech Global Holdings",
              "title": "Principal Architect", "start": "March 1999",
              "end": "Present", "relevant": True}


def read_with(rows, fields=None, text=SOURCE):
    """local_extract.read() with both model calls stubbed.

    The real function, the real router, the real arithmetic — only the two
    network calls are replaced, because the defect is in what read() does
    with what they return.
    """
    from unittest import mock
    answer = dict(fields or SOURCE_FIELDS)
    with mock.patch.object(le, "extract", lambda *a, **k: (dict(answer), 0.0)), \
         mock.patch.object(le, "employment", lambda *a, **k: dict(rows)):
        return le.read(text=text, now=NOW)


class TestTheValidatedRowsAreTheOnlyRowsDownstream(unittest.TestCase):
    """R1, the review's BLOCKER.

    route() rejected the fabricated row and reported 1 year. read() then
    recomputed months from the ORIGINAL list and returned 27 — while still
    carrying the correction that said the row had been excluded. Two
    competing truths, and the wrong one reached the profile.
    """

    ROWS = {"target_field": "software engineering",
            "employment": [GENUINE, FABRICATED]}

    def test_route_alone_already_got_this_right(self):
        """The precondition. If this fails the fixture is wrong, not the
        fix."""
        out = le.route(dict(SOURCE_FIELDS), dict(self.ROWS), SOURCE, now=NOW)
        self.assertEqual(out["decision"], "corrected")
        self.assertEqual(out["result"]["years_experience"], 1)

    def test_read_does_not_recompute_from_the_rejected_rows(self):
        checked, _rows, _decision = read_with(self.ROWS)
        self.assertEqual(checked["years_experience"], 1)
        self.assertLess(checked["experience_months"], 24)

    def test_read_returns_only_the_validated_rows(self):
        _checked, rows, _decision = read_with(self.ROWS)
        companies = [r.get("company") for r in rows["employment"]]
        self.assertEqual(companies, ["Northwind Robotics"])

    def test_the_answer_and_its_explanation_agree(self):
        """The correction may say a row was excluded. The result must not
        then contain it."""
        checked, rows, decision = read_with(self.ROWS)
        excluded = [c for c in decision["corrections"]
                    if c["action"] == "excluded"]
        self.assertTrue(excluded)
        self.assertNotIn("Initech Global Holdings",
                         [r.get("company") for r in rows["employment"]])
        self.assertEqual(checked["years_experience"], 1)

    def test_the_target_field_survives(self):
        """Filtering rows must not drop the rest of the answer."""
        _checked, rows, _decision = read_with(self.ROWS)
        self.assertEqual(rows["target_field"], "software engineering")

    def test_a_clean_answer_is_untouched(self):
        checked, rows, decision = read_with(
            {"target_field": "software engineering", "employment": [GENUINE]},
            fields=dict(SOURCE_FIELDS, years_experience=1))
        self.assertEqual(decision["decision"], "accept")
        self.assertEqual(checked["years_experience"], 1)
        self.assertEqual(len(rows["employment"]), 1)

    def test_an_escalation_still_returns_the_model_answer_for_the_fallback(self):
        """Escalation hands the caller nothing checked — the Gemini
        fallback re-reads the document itself."""
        checked, _rows, decision = read_with(
            {"employment": [FABRICATED]})
        self.assertEqual(decision["decision"], "escalate")
        self.assertIsNone(checked)


class TestARejectedRowCannotReachTheSearch(unittest.TestCase):
    """The second half of R1: local_profile.generate() derived held titles
    from read()'s rows, so a fabricated employer's title became a paid
    search keyword."""

    def generate(self, rows):
        from unittest import mock
        import local_profile
        import local_search
        market = local_search.Market(rows=[], seniority=())
        answer = dict(SOURCE_FIELDS)
        with mock.patch.object(le, "extract",
                               lambda *a, **k: (dict(answer), 0.0)), \
             mock.patch.object(le, "employment", lambda *a, **k: dict(rows)):
            try:
                # No `now`: generate() uses the real clock, and the
                # fixture's dates are historical on purpose so the
                # arithmetic is stable whenever this runs.
                return local_profile.generate(
                    SOURCE, {}, log=lambda *a: None, market=market)
            except local_profile.Escalated as exc:
                return {"escalated": list(exc.args[0])}

    # The SEARCH fields. `notes` is deliberately excluded: the correction
    # explanation is allowed — and wanted — to name what it dropped, and a
    # test that greps the whole profile would forbid saying so.
    SEARCH_FIELDS = ("role_keywords", "title_hints", "title_exclude",
                     "domain_title_terms", "local_ranking",
                     "local_from_orphans")

    def searchable(self, got):
        return json.dumps({k: got.get(k) for k in self.SEARCH_FIELDS}).lower()

    def test_the_fabricated_title_is_not_a_search_candidate(self):
        got = self.generate({"target_field": "software engineering",
                             "employment": [GENUINE, FABRICATED]})
        blob = self.searchable(got)
        self.assertNotIn("principal architect", blob)
        self.assertNotIn("initech", blob)

    def test_the_genuine_title_still_is(self):
        got = self.generate({"target_field": "software engineering",
                             "employment": [GENUINE, FABRICATED]})
        self.assertIn("backend engineer", self.searchable(got))

    def test_the_experience_total_excludes_it_too(self):
        got = self.generate({"target_field": "software engineering",
                             "employment": [GENUINE, FABRICATED]})
        self.assertEqual(got["years_experience"], 1)

    def test_the_explanation_still_says_what_was_dropped(self):
        """Transparency is the point of the correction, so this asserts the
        opposite of the test above — in the one field where it belongs."""
        got = self.generate({"target_field": "software engineering",
                             "employment": [GENUINE, FABRICATED]})
        self.assertIn("initech", got["notes"].lower())


# A document with TWO dated things in it: one job, and an education entry
# whose years are nowhere near it. Every borrowing case below needs that
# second dated entry to borrow from.
TWO_ENTRIES = ("Rae Lindqvist\nSoftware Engineer\n"
               "Professional Experience\n"
               "Vantage Systems — Backend Engineer\n"
               "March 2023 - December 2024\n"
               "- Built billing services in python.\n"
               "Education\n"
               "BSc Computing, 1999 - 2003\n")

TWO_FIELDS = {"name": "Rae Lindqvist", "companies": ["Vantage Systems"],
              "skills": ["python"], "titles": ["Backend Engineer"],
              "institutions": [], "years_experience": 1}

VANTAGE = {"company": "Vantage Systems", "title": "Backend Engineer",
           "start": "March 2023", "end": "December 2024", "relevant": True}


class TestDatesBelongToTheirOwnEntry(unittest.TestCase):
    """The independent review's date-association findings.

    check_employment looked for each four-digit year ANYWHERE in the
    document. A résumé that mentions 1999 in its education section
    therefore validated an employment row that claimed to start in 1999,
    and the exact arithmetic ran over it.

    The rule added is deliberately small: a row's dates must sit near that
    row's own employer or title. It does not parse résumé structure.
    """

    def years(self, rows, text=TWO_ENTRIES, fields=None, stated=1):
        out = le.route(dict(fields or TWO_FIELDS, years_experience=stated),
                       {"employment": list(rows)}, text, now=NOW)
        if out["result"] is None:
            return "escalate"
        return out["result"]["years_experience"]

    def assertNotInflated(self, got, ceiling=1):
        """The invariant, which is not a particular number.

        A résumé whose only row is unsupported escalates — that is the
        designed answer, and asking Gemini is what escalation is for.
        A résumé with one good row and one bad one keeps the good one.
        Either way the fabricated interval must not become experience.
        """
        if got == "escalate":
            return
        self.assertLessEqual(got, ceiling)

    def test_a_year_cannot_be_borrowed_from_the_education_section(self):
        """Case A: 25 years, entirely from a degree's start year."""
        self.assertNotInflated(
            self.years([dict(VANTAGE, start="March 1999")], stated=25))

    def test_an_invented_present_cannot_extend_a_finished_job(self):
        """Case B: the document says the job ended in December 2024."""
        self.assertNotInflated(
            self.years([dict(VANTAGE, end="Present")], stated=3))

    def test_a_row_with_no_employer_or_title_cannot_be_located(self):
        """Case C: nothing to anchor the dates to, so nothing to check them
        against. 27 years out of a row naming no one."""
        self.assertNotInflated(
            self.years([{"start": "March 1999", "end": "Present",
                         "relevant": True}], stated=27))

    def test_a_future_end_date_is_not_experience(self):
        """Case D: only future STARTS were rejected."""
        text = TWO_ENTRIES + "Certifications\nCloud Practitioner, 2030\n"
        self.assertNotInflated(
            self.years([dict(VANTAGE, end="March 2030")], text=text,
                       stated=7))

    def test_an_impossible_month_is_unreadable_not_january(self):
        """Case E: parse_month silently dropped the 19 and answered
        January."""
        self.assertIsNone(le.parse_month("2023-19", NOW))

    def test_a_reversed_range_is_still_rejected(self):
        """Case F: already safe. Kept so it stays safe."""
        self.assertEqual(
            self.years([VANTAGE,
                        dict(VANTAGE, start="December 2024",
                             end="March 2023")]), 1)

    def test_the_genuine_row_is_still_counted(self):
        """The whole point: none of the above may cost a real job."""
        self.assertEqual(self.years([VANTAGE]), 1)

    def test_a_genuinely_current_job_still_counts(self):
        """A document that really does say Present must keep working."""
        text = ("Rae Lindqvist\nProfessional Experience\n"
                "Vantage Systems — Backend Engineer\n"
                "March 2023 - Present\n"
                "- Built billing services in python.\n")
        row = dict(VANTAGE, end="Present")
        self.assertGreaterEqual(self.years([row], text=text, stated=3), 3)

    def test_ordinary_date_spellings_still_read(self):
        for value, want in (("Jan 2021", (2021, 1)), ("2019/06", (2019, 6)),
                            ("March 2023", (2023, 3)), ("2021", (2021, 1)),
                            ("present", NOW)):
            self.assertEqual(le.parse_month(value, NOW), want, value)


PROMOTED_SOURCE = ("Priya Raman\nProfessional Experience\n"
                   "Dealermatix Technologies Pvt Ltd\n"
                   "Software Engineer (promoted from Software Engineer "
                   "Trainee)\nJanuary 2025 - Present\n"
                   "- Built REST integrations in python.\n")

PROMOTED_FIELDS = {"name": "Priya Raman",
                   "companies": ["Dealermatix Technologies Pvt Ltd"],
                   "skills": ["python"], "titles": ["Software Engineer"],
                   "institutions": [], "years_experience": 1}


class TestUncertaintySurvivesASimplifiedTitle(unittest.TestCase):
    """The review's promotion finding.

    The document says "Software Engineer (promoted from Software Engineer
    Trainee)". The model returns "Software Engineer" — which is true, and
    grounds perfectly, and silently drops the one word that made the
    interval uncertain. ambiguous_span() read the model's title, so the
    qualifier disappearing took the uncertainty with it.

    The fix reads the DOCUMENT beside the row, using the same anchors the
    date checks use. No promotion date is invented; the interval is still
    counted as a floor. What changes is that the floor is declared.
    """

    ROW = {"company": "Dealermatix Technologies Pvt Ltd",
           "title": "Software Engineer", "start": "January 2025",
           "end": "Present", "relevant": True}

    def route(self, row=None, text=PROMOTED_SOURCE):
        return le.route(dict(PROMOTED_FIELDS),
                        {"employment": [dict(row or self.ROW)]}, text,
                        now=NOW)

    def test_the_simplified_title_is_still_flagged(self):
        flagged = [c for c in self.route()["corrections"]
                   if c["action"] == "flagged"]
        self.assertEqual(len(flagged), 1, self.route()["corrections"])
        self.assertIn("promotion", flagged[0]["why"])

    def test_the_uncertainty_reaches_the_result_metadata(self):
        """Downstream review has to be able to see it."""
        out = self.route()
        self.assertTrue(any("needs review" in c["why"].lower()
                            for c in out["corrections"]))

    def test_no_promotion_date_is_invented(self):
        """The interval is not split at a date nobody wrote."""
        out = self.route()
        self.assertIsNotNone(out["result"])
        self.assertIn(out["result"]["years_experience"], (0, 1))

    def test_the_full_title_is_still_flagged(self):
        """The old path must keep working."""
        row = dict(self.ROW,
                   title="Software Engineer (promoted from Software "
                         "Engineer Trainee)")
        flagged = [c for c in self.route(row)["corrections"]
                   if c["action"] == "flagged"]
        self.assertEqual(len(flagged), 1)

    def test_an_ordinary_role_is_not_flagged(self):
        """No promotion in the document, no uncertainty invented."""
        text = ("Priya Raman\nProfessional Experience\n"
                "Dealermatix Technologies Pvt Ltd\nSoftware Engineer\n"
                "January 2025 - Present\n"
                "- Built REST integrations in python.\n")
        flagged = [c for c in self.route(text=text)["corrections"]
                   if c["action"] == "flagged"]
        self.assertEqual(flagged, [])

    def test_a_promotion_in_another_entry_does_not_leak(self):
        """The qualifier has to belong to THIS row, like the dates do."""
        text = ("Priya Raman\nProfessional Experience\n"
                "Dealermatix Technologies Pvt Ltd\nSoftware Engineer\n"
                "January 2025 - Present\n"
                "- Built REST integrations in python.\n"
                "Education\n"
                "Promoted from Trainee scheme, 2019\n")
        flagged = [c for c in self.route(text=text)["corrections"]
                   if c["action"] == "flagged"]
        self.assertEqual(flagged, [])


if __name__ == "__main__":
    unittest.main()
