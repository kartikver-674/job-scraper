"""A rare skill can suggest a job title. It cannot confer the profession.

V3 Step 6, fixing audit defect V3-C5. The orphan pass finds a skill nobody is
searching for and asks the corpus which titles are posted alongside it. On the
development corpus `git` anchors `software engineer -python developer`, and for
a business analyst who coordinates with developers a platform token anchors
`salesforce developer`. Left alone the chain is circular: the skill proposes the
role, and the role is then treated as supported because the skill proposed it.

What these tests defend, in order of how badly each would hurt:

  * discovery is not starved. Every fail-open door is tested, and the last
    query is never taken away
  * a genuine engineer keeps orphan-derived engineering titles
  * a platform or tool never confers a profession on its own
  * an occupation the sixteen families cannot place is never refused
  * with the flag off, behaviour is byte-identical

Every fixture is written here. No holdout candidate, label or result is used,
and no technology name appears in the implementation.
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

import orphan_guard as og    # noqa: E402
import role_evidence         # noqa: E402


def role(supports=None, titles=None, thin=False, transition=(), target=None):
    return {"role_evidence": {
        "supports": dict(supports or {}),
        "title_families": dict(titles or {}),
        "transition_modes": list(transition),
        "stated_target": {"family": target},
        "thin": thin}}


def gate(queries, orphans, anchors=None, **kw):
    kept, _record = og.filter_queries(queries, role(**kw), orphans, anchors)
    return kept


class ToolsDoNotConferProfessions(unittest.TestCase):
    """A, B, C, D, E. The proposed title is what is judged, never the tool."""

    def test_a_a_delivery_manager_does_not_gain_a_developer_title(self):
        kept = gate(["programme manager", "flutter developer"],
                    ["flutter developer"],
                    anchors=[("flutter developer", "git")],
                    supports={"project_delivery": "strong"},
                    titles={"project_delivery": ["Programme Manager"]})
        self.assertEqual(kept, ["programme manager"])

    def test_b_an_analyst_does_not_gain_an_engineering_title(self):
        kept = gate(["data analyst", "data engineer"], ["data engineer"],
                    supports={"data_analytics": "strong"},
                    titles={"data_analytics": ["Data Analyst"]})
        self.assertEqual(kept, ["data analyst"])

    def test_c_a_functional_consultant_does_not_gain_a_developer_title(self):
        kept = gate(["functional consultant", "salesforce developer"],
                    ["salesforce developer"],
                    supports={"functional_consulting": "strong",
                              "business_analysis": "strong"},
                    titles={"functional_consulting": ["Functional Consultant"]})
        self.assertEqual(kept, ["functional consultant"])

    def test_d_a_salesperson_does_not_gain_a_developer_title(self):
        kept = gate(["account executive", "salesforce developer"],
                    ["salesforce developer"],
                    supports={"sales": "strong"},
                    titles={"sales": ["Account Executive"]})
        self.assertEqual(kept, ["account executive"])

    def test_e_a_marketer_does_not_gain_a_frontend_title(self):
        kept = gate(["digital marketing manager", "frontend developer"],
                    ["frontend developer"],
                    supports={"marketing": "strong"},
                    titles={"marketing": ["Marketing Manager"]})
        self.assertEqual(kept, ["digital marketing manager"])

    def test_the_rule_reads_the_proposed_title_not_the_anchor(self):
        """Swapping the anchoring tool changes nothing; only the title is judged."""
        for tool in ("git", "some-unheard-of-sdk", None):
            kept = gate(["programme manager", "flutter developer"],
                        ["flutter developer"],
                        anchors=[("flutter developer", tool)],
                        supports={"project_delivery": "strong"})
            self.assertEqual(kept, ["programme manager"], tool)

    def test_no_technology_name_appears_in_the_implementation(self):
        """The RULES, excluding the module docstring and the self-check.

        demo() names technologies because a self-check has to exercise a real
        shape; the rules must not, because naming one is how a general guard
        becomes a list of special cases."""
        import inspect
        import ast
        tree = ast.parse(inspect.getsource(og))
        if ast.get_docstring(tree):
            tree.body = tree.body[1:]
        tree.body = [n for n in tree.body
                     if not (isinstance(n, ast.FunctionDef) and n.name == "demo")]
        body = "\n".join(line.split("#", 1)[0]
                         for line in ast.unparse(tree).splitlines()).lower()
        for token in ("salesforce", "apex", "git", "python", "sql", "html",
                      "flutter", "jira", "sap", "css"):
            self.assertNotIn(token, body, f"{token} is named in the code")


class GenuineCandidatesKeepTheirs(unittest.TestCase):
    """F, G, H — the direction that would make this change harmful."""

    def test_f_an_engineer_keeps_orphan_derived_engineering_titles(self):
        kept = gate(["backend engineer", "flutter developer"],
                    ["flutter developer"],
                    supports={"software_engineering": "strong"})
        self.assertEqual(kept, ["backend engineer", "flutter developer"])

    def test_g_a_data_engineer_keeps_orphan_derived_data_titles(self):
        kept = gate(["data engineer", "analytics engineer"],
                    ["analytics engineer"],
                    supports={"data_engineering": "strong"})
        self.assertIn("analytics engineer", kept)

    def test_h_a_corroborated_transition_keeps_its_target_family(self):
        kept = gate(["store manager", "backend developer"],
                    ["backend developer"],
                    supports={"management": "strong"},
                    transition=["development"],
                    target="software_engineering")
        self.assertIn("backend developer", kept)

    def test_an_uncorroborated_target_does_not_keep_it(self):
        kept = gate(["store manager", "backend developer"],
                    ["backend developer"],
                    supports={"management": "strong"},
                    target="software_engineering")
        self.assertNotIn("backend developer", kept)

    def test_a_held_title_in_the_family_is_enough(self):
        kept = gate(["business analyst", "salesforce developer"],
                    ["salesforce developer"],
                    supports={"business_analysis": "strong"},
                    titles={"software_engineering": ["Salesforce Developer"]})
        self.assertIn("salesforce developer", kept)


class NothingIsStarved(unittest.TestCase):
    """I, J, and the reason the other tests are allowed to be strict."""

    def test_i_a_thin_record_judges_nobody(self):
        """Enough surviving queries that the starvation guard is not what
        saves this; the thin check has to be doing the work."""
        kept, record = og.filter_queries(
            ["graphic designer", "visual designer", "flutter developer"],
            role(supports={"design": "strong"}, thin=True),
            ["flutter developer"])
        self.assertEqual(kept, ["graphic designer", "visual designer",
                                "flutter developer"])
        self.assertIn("thin", record["fail_open"])
        self.assertEqual(record["rejected"], [])

    def test_i2_no_role_record_judges_nobody(self):
        kept, record = og.filter_queries(["flutter developer"], {},
                                         ["flutter developer"])
        self.assertEqual(kept, ["flutter developer"])
        self.assertTrue(record["fail_open"])

    def test_i3_a_candidate_with_no_supported_family_is_not_judged(self):
        kept, record = og.filter_queries(
            ["flutter developer", "x"], role(supports={}), ["flutter developer"])
        self.assertIn("flutter developer", kept)
        self.assertIn("absence of evidence", record["fail_open"])

    def test_j_an_unrecognised_occupation_is_never_refused(self):
        self.assertIsNone(role_evidence.family_of("zzzz qqqq"))
        kept = gate(["support specialist", "zzzz qqqq"], ["zzzz qqqq"],
                    supports={"support": "strong"})
        self.assertIn("zzzz qqqq", kept)

    def test_the_last_query_is_never_taken_away(self):
        kept, record = og.filter_queries(
            ["flutter developer"], role(supports={"marketing": "strong"}),
            ["flutter developer"])
        self.assertEqual(kept, ["flutter developer"])
        self.assertIn("starve", record["fail_open"])
        self.assertEqual(record["rejected"], [])

    def test_a_non_orphan_query_is_never_touched(self):
        kept = gate(["flutter developer", "programme manager"], [],
                    supports={"project_delivery": "strong"})
        self.assertEqual(kept, ["flutter developer", "programme manager"])

    def test_only_the_named_orphan_titles_are_eligible(self):
        """Two unsupported software titles, one of them an orphan. Only the
        orphan may go, and enough survives that starvation is not the reason."""
        kept, record = og.filter_queries(
            ["marketing manager", "brand manager", "backend developer",
             "frontend developer"],
            role(supports={"marketing": "strong"}), ["frontend developer"])
        self.assertIn("backend developer", kept,
                      "a query outside the orphan set was judged")
        self.assertNotIn("frontend developer", kept)
        self.assertEqual(record["rejected"], ["frontend developer"])


class NoCircularEvidence(unittest.TestCase):
    """§2. Support must come from somewhere the orphan skill did not."""

    def test_support_is_read_from_the_frozen_record_only(self):
        """Code only. The docstring is allowed to say what it does NOT read."""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(og.supported_families).strip())
        fn = tree.body[0]
        if ast.get_docstring(fn):
            fn.body = fn.body[1:]
        body = ast.unparse(fn)
        self.assertIn("supports", body)
        self.assertIn("title_families", body)
        for forbidden in ("lift", "corpus", "market", "frequency", "listings"):
            self.assertNotIn(forbidden, body, forbidden)

    def test_the_orphan_term_itself_never_grants_support(self):
        """The anchoring skill is recorded for the explanation and nowhere else."""
        kept_with = gate(["programme manager", "flutter developer"],
                         ["flutter developer"],
                         anchors=[("flutter developer", "flutter")],
                         supports={"project_delivery": "strong"})
        kept_without = gate(["programme manager", "flutter developer"],
                            ["flutter developer"],
                            supports={"project_delivery": "strong"})
        self.assertEqual(kept_with, kept_without)


class TheAblation(unittest.TestCase):
    """§11. Both variants exist and the least aggressive one ships."""

    def test_the_default_is_the_least_aggressive_variant(self):
        self.assertEqual(og.MODE, "any")

    def test_any_accepts_weak_support_and_strong_does_not(self):
        weak = role(supports={"software_engineering": "weak",
                              "project_delivery": "strong"})
        kept_any, _a = og.filter_queries(["programme manager", "flutter developer"],
                                         weak, ["flutter developer"], mode="any")
        kept_strict, _b = og.filter_queries(["programme manager", "flutter developer"],
                                            weak, ["flutter developer"],
                                            mode="strong")
        self.assertIn("flutter developer", kept_any)
        self.assertNotIn("flutter developer", kept_strict)

    def test_a_held_title_satisfies_both_variants(self):
        data = role(supports={"project_delivery": "strong"},
                    titles={"software_engineering": ["Software Engineer"]})
        for mode in ("any", "strong"):
            kept, _r = og.filter_queries(["programme manager", "flutter developer"],
                                         data, ["flutter developer"], mode=mode)
            self.assertIn("flutter developer", kept, mode)


class Explainability(unittest.TestCase):
    """§18. Every decision names its evidence, and there is no score."""

    def test_a_rejection_records_everything_required(self):
        _kept, record = og.filter_queries(
            ["programme manager", "flutter developer"],
            role(supports={"project_delivery": "strong"}),
            ["flutter developer"], anchors=[("flutter developer", "git")])
        row = [d for d in record["decisions"] if not d["accepted"]][0]
        self.assertEqual(row["orphan_term"], "git")
        self.assertEqual(row["query"], "flutter developer")
        self.assertEqual(row["proposed_family"], "software_engineering")
        self.assertIn("project_delivery", row["candidate_support"])
        self.assertIn("no support outside the orphan skill", row["reason"])
        self.assertTrue(og.explain(record).strip())

    def test_the_skill_survival_question_is_answered_honestly(self):
        got = og.skill_survives("anything")
        self.assertTrue(got["kept_in_skills"])
        self.assertTrue(got["kept_in_title_hints"])
        self.assertFalse(got["kept_as_a_search_query"])


class OffByDefault(unittest.TestCase):

    def setUp(self):
        self.saved = os.environ.get(og.FLAG)
        os.environ.pop(og.FLAG, None)

    def tearDown(self):
        os.environ.pop(og.FLAG, None)
        if self.saved is not None:
            os.environ[og.FLAG] = self.saved

    def test_the_guard_is_off_unless_asked_for(self):
        self.assertFalse(og.enabled())

    def test_the_flag_is_read_per_call(self):
        os.environ[og.FLAG] = "1"
        self.assertTrue(og.enabled())
        os.environ[og.FLAG] = "0"
        self.assertFalse(og.enabled())


class TheProvenanceExists(unittest.TestCase):
    """fields_for must hand the anchor forward, or the explanation is blind."""

    def test_fields_for_reports_which_skill_anchored_each_orphan(self):
        """Against the committed corpus, so this is the real orphan path.

        Without the anchor the explanation can say a title was manufactured but
        not by what, which is the whole point of the record."""
        import local_search
        market = local_search.frozen_market()
        fields = local_search.fields_for(
            {"skills": ["redis", "java"], "employment": []}, market)
        self.assertIn("orphan_anchors", fields)
        self.assertTrue(fields["from_orphans"],
                        "the fixture produced no orphan, so this proves nothing")
        self.assertEqual([t for t, _s in fields["orphan_anchors"]],
                         fields["from_orphans"])
        self.assertTrue(all(skill for _t, skill in fields["orphan_anchors"]),
                        "an orphan title arrived with no anchoring skill")


if __name__ == "__main__":
    unittest.main(verbosity=2)
