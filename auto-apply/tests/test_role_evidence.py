"""A tool is not a role, coordination is not ownership, and nobody is starved.

V3 Step 3. Profile Engine v2 reads a résumé as a bag of tools, so a platform
token was enough to make somebody a developer. role_evidence reads what the
sentences say the person DID, before any query exists, and may veto a query
whose role family the document does not support.

What these tests defend, in order of how badly each would hurt:

  * nobody loses their job search. Every fail-open path is tested, and the
    last surviving query can never be taken away
  * a genuine developer keeps developer queries
  * a platform token alone never creates a developer, in any of the shapes the
    audit measured, and the rule is about sentence structure rather than a list
    of technologies
  * with the flag off, v2 behaviour is byte-identical
  * every veto can be explained from the résumé's own words

The fixtures are written fresh here. They are SHAPES, not the wording of any
real document, and no rule in role_evidence names a technology.
"""

import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_APPLY = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(AUTO_APPLY)
for path in (REPO_ROOT, AUTO_APPLY):
    if path not in sys.path:
        sys.path.insert(0, path)

import role_evidence as re_     # noqa: E402


def build(text, titles=(), target="", employment_titles=None):
    signals = {"titles": list(titles),
               "target_field": target,
               "employment_titles": list(
                   titles if employment_titles is None else employment_titles)}
    return re_.build(text, signals)


def gate(record, queries):
    kept, rejected = re_.filter_queries(record, queries)
    return kept, [r for r in rejected if "query" in r]


# --------------------------------------------------------------- the shapes

BA = ("EXPERIENCE\n"
      "Business Analyst, Meridian Freight, 2021 - Present\n"
      "- Gathered business requirements from stakeholders across three "
      "departments and turned them into user stories\n"
      "- Ran process mapping workshops and documented the as-is workflow\n"
      "- Configured validation rules and page layouts on the CRM platform\n"
      "- Led UAT with end users ahead of each go-live\n"
      "SKILLS\nSalesforce, Apex, SOQL, Excel\n")

ADMIN = ("EXPERIENCE\n"
         "Platform Administrator, Colby Health, 2020 - Present\n"
         "- Managed user access, permission sets and licence assignment for "
         "900 users\n"
         "- Maintained sandbox refreshes and release deployment windows\n"
         "- Built reports and dashboards for the operations team\n"
         "- Resolved tickets escalated from the service desk\n"
         "SKILLS\nSalesforce, ServiceNow, SOQL\n")

SELLER = ("EXPERIENCE\n"
          "Account Executive, Brightline Software, 2019 - Present\n"
          "- Carried a 1.4m quota and closed business across a named "
          "territory\n"
          "- Prospected into new logos and ran demos for prospective "
          "customers\n"
          "- Managed renewals and upsell for forty accounts\n"
          "SKILLS\nSalesforce, HubSpot, Outreach\n")

COORDINATOR = ("EXPERIENCE\n"
               "Business Analyst, Tanner Logistics, 2020 - Present\n"
               "- Wrote acceptance criteria for the components the developers "
               "build each sprint\n"
               "- Coordinated with the engineering team on release scope\n"
               "- Gathered requirements from stakeholders and ran UAT\n"
               "- Translated business requirements for the development team\n"
               "SKILLS\nSalesforce, Apex, Lightning Web Components\n")

DEVELOPER = ("EXPERIENCE\n"
             "Platform Developer, Acme Cloud, 2022 - Present\n"
             "- Wrote Apex classes and triggers for the billing integration\n"
             "- Built Lightning Web Components used by three business units\n"
             "- Developed REST endpoints and their unit tests\n"
             "SKILLS\nApex, SOQL, JavaScript\n")

ANALYST = ("EXPERIENCE\n"
           "Finance Analyst, Halloran Group, 2019 - Present\n"
           "- Ran variance analysis and month-end close reconciliation\n"
           "- Built forecasting models and reported KPIs to the board\n"
           "- Queried the warehouse for ad-hoc data analysis\n"
           "SKILLS\nPython, SQL, Excel\n")

MARKETER = ("EXPERIENCE\n"
            "Digital Marketing Manager, Vendell, 2020 - Present\n"
            "- Ran paid media campaigns and owned the content calendar\n"
            "- Improved click-through rate across the email programme\n"
            "- Edited HTML and CSS inside campaign templates\n"
            "SKILLS\nHTML, CSS, SEO, Marketo\n")

PRODUCT = ("EXPERIENCE\n"
           "Product Manager, Fenwick Labs, 2021 - Present\n"
           "- Owned the backlog and ran sprint planning with two squads\n"
           "- Defined the roadmap and reported delivery milestones\n"
           "- Wrote a small Python script for my own reporting\n"
           "SKILLS\nJira, Git, SQL, Python\n")

SWITCHER = ("EXPERIENCE\n"
            "Store Manager, Northgate Retail, 2016 - 2024\n"
            "- Managed a team of 14 and owned the branch P&L\n"
            "- Led hiring and performance reviews for the floor staff\n"
            "EDUCATION\n"
            "Software engineering bootcamp, 2024\n"
            "- Built three web applications and a REST API as coursework\n"
            "SKILLS\nPython, JavaScript, React\n")


class ATokenIsNotARole(unittest.TestCase):
    """A. B. C. F. G. H. — the platform token shapes."""

    def test_a_platform_tokens_do_not_make_an_analyst_a_developer(self):
        rec = build(BA, ["Business Analyst"])
        self.assertNotIn("software_engineering", rec["supports"])
        self.assertIn("salesforce", rec["platforms"])
        kept, rejected = gate(rec, ["business analyst", "salesforce developer"])
        self.assertEqual(kept, ["business analyst"])
        self.assertEqual(rejected[0]["query_family"], "software_engineering")

    def test_b_an_administrator_is_not_a_developer(self):
        rec = build(ADMIN, ["Platform Administrator"])
        kept, _r = gate(rec, ["salesforce administrator", "salesforce developer"])
        self.assertIn("salesforce administrator", kept)
        self.assertNotIn("salesforce developer", kept)

    def test_c_a_salesperson_with_a_crm_is_not_a_developer(self):
        rec = build(SELLER, ["Account Executive"])
        kept, _r = gate(rec, ["account executive", "salesforce developer"])
        self.assertIn("account executive", kept)
        self.assertNotIn("salesforce developer", kept)

    def test_f_python_and_sql_do_not_make_a_software_engineer(self):
        rec = build(ANALYST, ["Finance Analyst"])
        kept, _r = gate(rec, ["finance analyst", "data analyst",
                              "software engineer", "data engineer"])
        self.assertIn("finance analyst", kept)
        self.assertIn("data analyst", kept)
        self.assertNotIn("software engineer", kept)
        self.assertNotIn("data engineer", kept)

    def test_g_editing_markup_does_not_make_a_frontend_developer(self):
        rec = build(MARKETER, ["Digital Marketing Manager"])
        kept, _r = gate(rec, ["digital marketing manager", "frontend developer"])
        self.assertIn("digital marketing manager", kept)
        self.assertNotIn("frontend developer", kept)

    def test_h_a_personal_script_does_not_make_a_developer(self):
        rec = build(PRODUCT, ["Product Manager"])
        kept, _r = gate(rec, ["product manager", "software engineer"])
        self.assertIn("product manager", kept)
        self.assertNotIn("software engineer", kept)

    def test_one_clause_naming_two_artefacts_is_one_piece_of_evidence(self):
        text = ("EXPERIENCE\nAnalyst, Foo, 2020 - Present\n"
                "- Wrote a script to pull records from the reporting API\n"
                "- Ran month-end close reconciliation and variance analysis\n"
                "- Reported KPIs to the finance leadership\n")
        rec = build(text, ["Analyst"])
        dev = rec["work_modes"].get(re_.DEVELOPMENT, {})
        self.assertLessEqual(dev.get("asserted", 0), 1, rec["evidence"])


class CoordinationIsNotOwnership(unittest.TestCase):
    """D. — the coordination shape the audit singled out."""

    def test_d_writing_specs_for_developers_is_not_developing(self):
        rec = build(COORDINATOR, ["Business Analyst"])
        self.assertNotIn("software_engineering", rec["supports"],
                         rec["work_modes"])
        kept, rejected = gate(rec, ["business analyst", "salesforce developer",
                                    "lightning web components developer"])
        self.assertEqual(kept, ["business analyst"])
        self.assertTrue(all(r["query_family"] == "software_engineering"
                            for r in rejected))

    def test_the_verbs_subject_is_checked_not_just_the_words(self):
        mine = ("EXPERIENCE\nEngineer, Foo, 2020 - Present\n"
                "- Built the payment components and shipped the service\n")
        theirs = ("EXPERIENCE\nAnalyst, Foo, 2020 - Present\n"
                  "- Specified the payment components the developers build\n"
                  "- Gathered requirements from stakeholders and ran UAT\n"
                  "- Ran process mapping workshops with the client\n")
        self.assertIn(re_.DEVELOPMENT, build(mine, ["Engineer"])["supports"]
                      and build(mine, ["Engineer"])["work_modes"])
        self.assertEqual(
            build(theirs, ["Analyst"])["work_modes"]
            .get(re_.DEVELOPMENT, {}).get("asserted", 0), 0)

    def test_an_explicit_denial_is_not_development(self):
        text = ("EXPERIENCE\nConsultant, Foo, 2020 - Present\n"
                "- I do not write application code myself; I specify it\n"
                "- Gathered requirements from stakeholders and ran UAT\n"
                "- Ran discovery workshops and advised on solution design\n")
        rec = build(text, ["Consultant"])
        self.assertNotIn("software_engineering", rec["supports"])


class DevelopersKeepTheirSearch(unittest.TestCase):
    """E. J. — the direction that breaks people if it goes wrong."""

    def test_e_someone_who_writes_the_code_is_supported(self):
        rec = build(DEVELOPER, ["Platform Developer"])
        self.assertIn("software_engineering", rec["supports"])
        kept, rejected = gate(rec, ["salesforce developer", "apex developer",
                                    "software engineer"])
        self.assertEqual(len(kept), 3)
        self.assertEqual(rejected, [])

    def test_j_a_held_developer_title_shields_the_family(self):
        thin = ("EXPERIENCE\n"
                "Software Engineer, Foo Corp, 2021 - Present\n"
                "- Took part in sprint planning and reported status weekly\n"
                "- Gathered requirements from stakeholders\n"
                "- Ran process mapping workshops for the client\n")
        rec = build(thin, ["Software Engineer"])
        kept, _r = gate(rec, ["software engineer", "backend developer"])
        self.assertEqual(kept, ["software engineer", "backend developer"])


class CareerSwitchers(unittest.TestCase):
    """I. — the case that must not be collapsed to the longest-held job."""

    def test_i_training_plus_a_stated_target_keeps_the_new_family_alive(self):
        rec = build(SWITCHER, ["Store Manager"], target="software developer")
        self.assertIn(re_.DEVELOPMENT, rec["transition_modes"])
        kept, _r = gate(rec, ["store manager", "software developer"])
        self.assertIn("software developer", kept)
        self.assertIn("store manager", kept)

    def test_a_stated_target_alone_does_not_establish_a_family(self):
        bare = ("EXPERIENCE\nStore Manager, Northgate, 2016 - 2024\n"
                "- Managed a team of 14 and owned the branch P&L\n"
                "- Led hiring and performance reviews\n"
                "- Ran the weekly rota and the stock count\n")
        rec = build(bare, ["Store Manager"], target="software developer")
        self.assertFalse(rec["stated_target"]["grounded"])
        self.assertNotIn("software_engineering", rec["supports"])
        kept, rejected = gate(rec, ["store manager", "software developer"])
        self.assertNotIn("software developer", kept)
        self.assertTrue(rejected)

    def test_the_historical_title_does_not_erase_the_target(self):
        rec = build(SWITCHER, ["Store Manager"], target="software developer")
        self.assertEqual(rec["stated_target"]["family"], "software_engineering")
        self.assertIn("Store Manager", rec["held_titles"])


class FailOpen(unittest.TestCase):
    """§9. Every one of these returns the queries untouched."""

    QUERIES = ["software engineer", "data engineer"]

    def test_an_empty_record_rejects_nothing(self):
        self.assertEqual(re_.filter_queries(None, self.QUERIES)[0], self.QUERIES)
        self.assertEqual(re_.filter_queries({}, self.QUERIES)[0], self.QUERIES)

    def test_a_thin_document_rejects_nothing(self):
        rec = build("Jamie Rivers\nSKILLS\nSalesforce, Apex\n", [])
        self.assertTrue(rec["thin"])
        self.assertEqual(re_.filter_queries(rec, self.QUERIES)[0], self.QUERIES)

    def test_an_unrecognised_query_family_is_never_rejected(self):
        rec = build(BA, ["Business Analyst"])
        self.assertIsNone(re_.family_of("zzzz qqqq"))
        kept, _r = gate(rec, ["zzzz qqqq"])
        self.assertEqual(kept, ["zzzz qqqq"])

    def test_the_last_query_is_never_taken_away(self):
        rec = build(BA, ["Business Analyst"])
        kept, rejected = re_.filter_queries(rec, ["salesforce developer"])
        self.assertEqual(kept, ["salesforce developer"])
        self.assertTrue(any("fail_open" in r for r in rejected))

    def test_a_document_with_no_other_kind_of_work_rejects_nothing(self):
        rec = build("EXPERIENCE\nContractor, Foo, 2020\n- Various duties\n", [])
        kept, _r = re_.filter_queries(rec, self.QUERIES)
        self.assertEqual(kept, self.QUERIES)

    def test_ungated_families_are_never_rejected(self):
        rec = build(DEVELOPER, ["Platform Developer"])
        odd = ["recruiter", "graphic designer", "accountant", "chef"]
        kept, _r = gate(rec, odd + ["salesforce developer"])
        for query in odd:
            self.assertIn(query, kept)

    def test_no_persona_shaped_input_ever_returns_an_empty_list(self):
        for text, titles in ((BA, ["Business Analyst"]), (ADMIN, ["Admin"]),
                             (SELLER, ["Account Executive"]),
                             (MARKETER, ["Marketing Manager"]),
                             (ANALYST, ["Finance Analyst"])):
            rec = build(text, titles)
            kept, _r = re_.filter_queries(rec, ["software engineer"])
            self.assertTrue(kept, text[:40])


class Invariance(unittest.TestCase):
    """§14. Formatting moves; the record does not."""

    def variants(self, text):
        lines = text.splitlines()
        return {
            "plain": text,
            "spaced": "\n\n".join(lines),
            "pdf_wrapped": text.replace(" and ", " and\n"),
            "upper": text.upper(),
            "extra_spaces": text.replace(" ", "  "),
        }

    def check(self, text, titles):
        base = build(text, titles)
        for name, variant in self.variants(text).items():
            rec = build(variant, titles)
            self.assertEqual(set(rec["does_not_support"]),
                             set(base["does_not_support"]),
                             f"{name}: gated families moved")

    def test_the_gated_set_is_stable_for_a_non_developer(self):
        self.check(BA, ["Business Analyst"])

    def test_the_gated_set_is_stable_for_a_developer(self):
        self.check(DEVELOPER, ["Platform Developer"])

    def test_duplicated_skill_aliases_change_nothing(self):
        rec = build(BA, ["Business Analyst"])
        dup = build(BA.replace("Salesforce, Apex, SOQL",
                               "Salesforce, salesforce, Apex, apex, SOQL"),
                    ["Business Analyst"])
        self.assertEqual(set(rec["does_not_support"]),
                         set(dup["does_not_support"]))

    def test_skill_order_changes_nothing(self):
        rec = build(BA, ["Business Analyst"])
        swapped = build(BA.replace("Salesforce, Apex, SOQL, Excel",
                                   "Excel, SOQL, Apex, Salesforce"),
                        ["Business Analyst"])
        self.assertEqual(set(rec["does_not_support"]),
                         set(swapped["does_not_support"]))

    def test_the_same_input_twice_gives_the_same_record(self):
        a, b = build(BA, ["Business Analyst"]), build(BA, ["Business Analyst"])
        self.assertEqual(a, b)


class Explainability(unittest.TestCase):
    """§16. No black box: a veto quotes the document."""

    def test_a_rejection_carries_its_whole_reason(self):
        rec = build(BA, ["Business Analyst"])
        _kept, rejected = gate(rec, ["salesforce developer"] * 1
                               + ["business analyst"])
        row = rejected[0]
        for field in ("query", "query_family", "candidate_supports",
                      "missing_work_modes", "platforms_present", "reason"):
            self.assertIn(field, row)
        self.assertIn("salesforce", row["platforms_present"])
        self.assertIn("business_analysis", row["candidate_supports"])
        self.assertTrue(re_.explain(rec, rejected).strip())

    def test_the_record_keeps_the_span_each_verdict_rests_on(self):
        rec = build(DEVELOPER, ["Platform Developer"])
        spans = rec["evidence"].get(re_.DEVELOPMENT) or []
        self.assertTrue(spans)
        self.assertTrue(all(s["quote"] for s in spans))
        self.assertTrue(any("apex" in s["quote"].lower()
                            or "component" in s["quote"].lower()
                            for s in spans))

    def test_the_record_says_where_it_came_from(self):
        rec = build(BA, ["Business Analyst"])
        self.assertIn("no model call", rec["provenance"])
        self.assertEqual(rec["schema"], re_.SCHEMA)


class NoTechnologyIsNamedByAnyRule(unittest.TestCase):
    """The general-rule requirement, checked against the source itself."""

    BANNED = ("salesforce", "apex", "soql", "lwc", "python", "java",
              "javascript", "html", "css", "sql", "git", "sap", "servicenow")

    def test_no_gating_rule_mentions_a_technology(self):
        import inspect
        source = inspect.getsource(re_)
        # The platform COLLECTOR names platforms on purpose; it grants nothing.
        start = source.index("_PLATFORM_RX")
        end = source.index(")", source.index("re.I", start))
        without = source[:start] + source[end:]
        for token in self.BANNED:
            for name in ("_CONSTRUCT", "_ARTEFACT", "_PIPELINE",
                         "_CONFIG_OBJECT", "_CONFIG_VERB"):
                block = without[without.index(name + " = "):]
                block = block[:block.index("\n\n")]
                self.assertNotIn(token, block.lower(),
                                 f"{name} names {token}")

    def test_a_platform_never_appears_in_a_work_mode_decision(self):
        rec = build(BA, ["Business Analyst"])
        stripped = build(BA.replace("Salesforce, Apex, SOQL, Excel",
                                    "Zorblax, Quuxian, Frobnitz"),
                         ["Business Analyst"])
        self.assertEqual(set(rec["does_not_support"]),
                         set(stripped["does_not_support"]))
        self.assertEqual(rec["work_modes"].keys(),
                         stripped["work_modes"].keys())


class OffByDefault(unittest.TestCase):

    def setUp(self):
        self.saved = os.environ.get(re_.FLAG)
        os.environ.pop(re_.FLAG, None)

    def tearDown(self):
        os.environ.pop(re_.FLAG, None)
        if self.saved is not None:
            os.environ[re_.FLAG] = self.saved

    def test_the_gate_is_off_unless_asked_for(self):
        self.assertFalse(re_.enabled())

    def test_the_flag_is_read_per_call(self):
        os.environ[re_.FLAG] = "1"
        self.assertTrue(re_.enabled())
        os.environ[re_.FLAG] = "0"
        self.assertFalse(re_.enabled())

    def test_building_the_record_has_no_side_effects(self):
        before = copy.deepcopy(re_.FAMILIES)
        build(BA, ["Business Analyst"])
        build(DEVELOPER, ["Platform Developer"])
        self.assertEqual(re_.FAMILIES, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
