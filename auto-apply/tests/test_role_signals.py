"""The role signals the model already produces, preserved and kept inert.

V3 Step 1. `titles` (FIELDS call) was read nowhere in production and
`target_field` (EMPLOYMENT call) reached one prose sentence in the profile
docstring; the v2 forensic audit measured that on 60 documents and called it
V3-C10. local_extract.role_signals() preserves both, plus the validated
employment titles, as one additive record.

What these tests are actually defending:

  * the record EXISTS end to end, from read() to the returned profile
  * the three sources stay SEPARATE — a later step has to be able to see
    where they disagree, and merging them here would destroy that
  * model prose is SANITISED before it travels
  * it is INERT: fields_for() sees the same person dict it always saw, the
    rendered profile does not carry it, and PROFILE_SCHEMA does not move

The inertness tests are the important half. A signal nobody consumes cannot
be wrong; a signal that quietly reaches search is a behaviour change nobody
asked for in this batch.
"""

import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_APPLY = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(AUTO_APPLY)
for path in (REPO_ROOT, AUTO_APPLY):
    if path not in sys.path:
        sys.path.insert(0, path)

import local_extract as le          # noqa: E402
import local_search                 # noqa: E402
import local_profile                # noqa: E402
import make_profile                 # noqa: E402
import skill_concepts               # noqa: E402


TEXT = ("Rhea Kapoor\n"
        "Salesforce Business Analyst\n"
        "Clearwater Financial, Salesforce Functional Consultant, "
        "Jun 2022 - Present\n"
        "Gathered requirements and wrote user stories for Sales Cloud.\n"
        "Skills: salesforce, sales cloud, requirements gathering, jira\n")

FIELDS = {"name": "Rhea Kapoor", "years_experience": 3,
          "titles": ["Salesforce Functional Consultant"],
          "skills": ["salesforce", "sales cloud", "requirements gathering",
                     "jira"],
          "companies": ["Clearwater Financial"], "education": [],
          "institutions": [], "projects": [], "certifications": []}

EMPLOYMENT = {"target_field": "salesforce business analysis", "employment": [
    {"company": "Clearwater Financial",
     "title": "Salesforce Functional Consultant",
     "start": "Jun 2022", "end": "Present", "relevant": True}]}

# The same answer with the profession phrase removed, for the A/B that proves
# the phrase cannot move a query. Module level, not built inside a test:
# setUp reads it, so a test that defined it would only work by ordering luck.
EMPTY_TARGET = dict(EMPLOYMENT, target_field="")


def market():
    """A corpus dense enough that role_keywords survive validation."""
    rows = []
    for i in range(80):
        rows.append(("salesforce business analyst", 25 if i % 2 else 5,
                     frozenset({"salesforce", "sales cloud"}), f"sfco{i % 20}"))
    for i in range(300):
        rows.append((f"software engineer {i % 7}", 3,
                     frozenset({"java", "sql"}), f"bigco{i % 25}"))
    return local_search.Market(rows=rows, seniority=("senior", "staff", "lead"))


class RoleSignalRecord(unittest.TestCase):
    """The builder itself: what it keeps, and what it refuses."""

    def test_target_field_and_titles_both_survive(self):
        got = le.role_signals(FIELDS, EMPLOYMENT)
        self.assertEqual(got["target_field"], "salesforce business analysis")
        self.assertEqual(got["titles"], ["Salesforce Functional Consultant"])

    def test_employment_titles_are_a_separate_list(self):
        # Not merged with titles[], not deduplicated against it, not
        # seniority-stripped. The later role work needs to compare them.
        got = le.role_signals(
            FIELDS, dict(EMPLOYMENT, employment=[
                {"title": "Senior Salesforce Functional Consultant"},
                {"title": "Business Analyst"}]))
        self.assertEqual(got["employment_titles"],
                         ["Senior Salesforce Functional Consultant",
                          "Business Analyst"])
        self.assertEqual(got["titles"], ["Salesforce Functional Consultant"])

    def test_it_records_that_target_field_is_not_grounded(self):
        # titles pass check_grounding; target_field comes from the OTHER
        # call and is never checked against the document. A consumer that
        # trusts them equally would be trusting the wrong one.
        got = le.role_signals(FIELDS, EMPLOYMENT)
        self.assertTrue(got["grounded"]["titles"])
        self.assertTrue(got["grounded"]["employment_titles"])
        self.assertFalse(got["grounded"]["target_field"])

    def test_missing_fields_give_an_empty_record_not_an_error(self):
        for checked, rows in ((None, None), ({}, {}),
                              (FIELDS, {}), ({}, EMPLOYMENT)):
            got = le.role_signals(checked, rows)
            self.assertIsInstance(got["target_field"], str)
            self.assertIsInstance(got["titles"], list)
            self.assertIsInstance(got["employment_titles"], list)

    def test_non_string_values_are_dropped_not_coerced(self):
        # A schema-constrained decoder usually returns strings, and
        # "usually" is what bit the employment rows before they were
        # validated. 123 is not a job title.
        got = le.role_signals(
            {"titles": ["Business Analyst", 123, None, {"a": 1}, ["x"]]},
            {"target_field": {"not": "a string"}})
        self.assertEqual(got["titles"], ["Business Analyst"])
        self.assertEqual(got["target_field"], "")

    def test_a_string_where_a_list_belongs_is_refused(self):
        got = le.role_signals({"titles": "Business Analyst"}, {})
        self.assertEqual(got["titles"], [])

    def test_control_characters_and_whitespace_are_removed(self):
        got = le.role_signals(
            {"titles": ["  Business\x00 \n Analyst  "]},
            {"target_field": "\tbusiness\r\n   analysis "})
        self.assertEqual(got["titles"], ["Business Analyst"])
        self.assertEqual(got["target_field"], "business analysis")

    def test_length_and_count_are_bounded(self):
        got = le.role_signals(
            {"titles": [f"Title {i}" for i in range(200)]},
            {"target_field": "x" * 5000})
        self.assertLessEqual(len(got["titles"]), le.SIGNAL_ITEMS)
        self.assertLessEqual(len(got["target_field"]), le.SIGNAL_CHARS)

    def test_duplicate_spellings_collapse_case_insensitively(self):
        got = le.role_signals(
            {"titles": ["Business Analyst", "business analyst",
                        "BUSINESS ANALYST", "Product Owner"]}, {})
        self.assertEqual(got["titles"], ["Business Analyst", "Product Owner"])


class CarriedThroughToTheProfile(unittest.TestCase):
    """read() -> generate() -> the returned profile."""

    def setUp(self):
        self._extract, self._employment = le.extract, le.employment
        le.extract = lambda m, t, *a, **k: (dict(FIELDS), 0.1)
        le.employment = lambda m, t, *a, **k: dict(
            EMPLOYMENT, employment=[dict(EMPLOYMENT["employment"][0])])

    def tearDown(self):
        le.extract, le.employment = self._extract, self._employment

    def profile(self):
        return local_profile.generate(TEXT, {"avoid": []}, market=market(),
                                      log=lambda *a, **k: None)

    def test_the_profile_carries_the_record(self):
        got = self.profile()["role_signals"]
        self.assertEqual(got["target_field"], "salesforce business analysis")
        self.assertEqual(got["titles"], ["Salesforce Functional Consultant"])
        self.assertEqual(got["employment_titles"],
                         ["Salesforce Functional Consultant"])

    def test_it_survives_the_finishing_steps(self):
        # split_compounds -> widen_skills -> reweight_* all rebuild the
        # dict. An additive key has to come out the other side.
        finished = make_profile._finish(self.profile(), TEXT, None,
                                        lambda *a, **k: None)
        self.assertIn("role_signals", finished)
        self.assertEqual(finished["role_signals"]["target_field"],
                         "salesforce business analysis")


class InertUntilAStepConsumesIt(unittest.TestCase):
    """The half that matters: nothing downstream may move."""

    def setUp(self):
        self._extract, self._employment = le.extract, le.employment
        le.extract = lambda m, t, *a, **k: (dict(FIELDS), 0.1)
        le.employment = lambda m, t, *a, **k: dict(
            EMPTY_TARGET if self.blank else EMPLOYMENT,
            employment=[dict(EMPLOYMENT["employment"][0])])
        self.blank = False

    def tearDown(self):
        le.extract, le.employment = self._extract, self._employment

    def test_fields_for_never_sees_the_record(self):
        # The person dict is the whole input to query construction. If a
        # role signal ever appears in it, search has started consuming
        # role intent and this batch has changed behaviour.
        seen = {}
        real = local_search.fields_for

        def spy(person, mkt, *a, **k):
            seen["keys"] = sorted(person)
            seen["extra"] = (list(a), sorted(k))
            return real(person, mkt, *a, **k)

        local_search.fields_for = spy
        try:
            local_profile.generate(TEXT, {"avoid": []}, market=market(),
                                   log=lambda *a, **k: None)
        finally:
            local_search.fields_for = real
        self.assertEqual(seen["keys"], ["employment", "skills"])
        self.assertEqual(seen["extra"], ([], []))

    def test_search_output_is_identical_with_and_without_a_target_field(self):
        # Same résumé, same skills, same employment; only the ungrounded
        # profession phrase differs. Every search field must be byte-equal.
        self.blank = False
        with_field = local_profile.generate(TEXT, {"avoid": []},
                                            market=market(),
                                            log=lambda *a, **k: None)
        self.blank = True
        without = local_profile.generate(TEXT, {"avoid": []}, market=market(),
                                         log=lambda *a, **k: None)
        for key in ("role_keywords", "title_hints", "skill_weights",
                    "local_ranking", "title_exclude", "penalty_terms",
                    "domain_half_a", "domain_half_b", "domain_title_terms",
                    "domain_bonus"):
            self.assertEqual(with_field[key], without[key], key)
        self.assertNotEqual(with_field["role_signals"]["target_field"],
                            without["role_signals"]["target_field"])


class RenderedProfileUnchanged(unittest.TestCase):
    """The file config.py imports must not gain a name or a schema."""

    def profile(self):
        return {
            "candidate_name": "Rhea Kapoor",
            "field_summary": "salesforce business analysis — 3 years.",
            "years_experience": 3, "experience_months": 39,
            "role_keywords": ["salesforce business analyst"],
            "skill_weights": [{"term": "salesforce", "weight": 4}],
            "penalty_terms": [], "domain_half_a": [], "domain_half_b": [],
            "domain_title_terms": [], "domain_bonus": 0,
            "title_hints": ["salesforce business analyst"],
            "title_exclude": [], "notes": "Generated locally.",
            "role_signals": {"target_field": "salesforce business analysis",
                             "titles": ["Salesforce Functional Consultant"],
                             "employment_titles":
                                 ["Salesforce Functional Consultant"],
                             "grounded": {"titles": True,
                                          "target_field": False,
                                          "employment_titles": True}},
        }

    def source(self):
        return make_profile.render("stepone", self.profile(), {
            "locations": ["Remote"], "exclude_levels": ["intern"],
            "min_comp_usd": None, "avoid": []})

    def test_the_rendered_file_does_not_carry_the_record(self):
        # render() emits only PROFILE_NAMES. Keeping role_signals out of
        # the file is what makes this batch schema-neutral: config.py's
        # loader sys.exits on a future schema, so a new emitted name would
        # force a PROFILE_SCHEMA bump and a worker-ordering requirement.
        source = self.source()
        self.assertNotIn("role_signals", source)
        self.assertNotIn("target_field", source)

    def test_the_rendered_file_is_still_ast_safe(self):
        source = make_profile.check_module(self.source())
        names = {t.id for node in ast.parse(source).body
                 if isinstance(node, ast.Assign)
                 for t in node.targets if isinstance(t, ast.Name)}
        self.assertTrue(names <= make_profile.PROFILE_NAMES,
                        f"rendered a name outside the allowlist: "
                        f"{sorted(names - make_profile.PROFILE_NAMES)}")

    def test_prose_in_a_role_signal_cannot_escape_the_docstring(self):
        # The record is not rendered, so this is belt and braces — but the
        # day it IS rendered, this test is already here.
        nasty = dict(self.profile())
        nasty["role_signals"]["target_field"] = '""" \nimport os\nX = "'
        source = make_profile.check_module(
            make_profile.render("stepone", nasty, {
                "locations": ["Remote"], "exclude_levels": ["intern"],
                "min_comp_usd": None, "avoid": []}))
        self.assertFalse(
            [n for n in ast.walk(ast.parse(source))
             if isinstance(n, (ast.Import, ast.ImportFrom, ast.Call))])


class ContractsThatMustNotMove(unittest.TestCase):
    """Schema and engine binding are untouched by an additive record."""

    def test_profile_schema_is_still_one(self):
        self.assertEqual(make_profile.PROFILE_SCHEMA, 1)

    def test_a_legacy_profile_with_no_stamp_is_still_readable(self):
        module = type(sys)("legacy")
        got = make_profile.profile_schema(module)
        self.assertTrue(got["readable"])
        self.assertEqual(got["version"], make_profile.LEGACY_SCHEMA)

    def test_a_schema_one_profile_is_still_readable(self):
        for stamp in (1, {"version": 1, "engine": "v1"},
                      {"version": 1, "engine": "v2"}):
            module = type(sys)("stamped")
            module.PROFILE_SCHEMA = stamp
            self.assertTrue(make_profile.profile_schema(module)["readable"],
                            stamp)

    def test_a_future_schema_is_still_refused(self):
        module = type(sys)("future")
        module.PROFILE_SCHEMA = {"version": make_profile.PROFILE_SCHEMA + 1,
                                 "engine": "v9"}
        self.assertFalse(make_profile.profile_schema(module)["readable"])

    def test_engine_version_binding_is_unchanged(self):
        self.assertEqual(skill_concepts.DEFAULT_VERSION, "v1")
        self.assertFalse(skill_concepts.roles_enabled())


if __name__ == "__main__":
    unittest.main()
