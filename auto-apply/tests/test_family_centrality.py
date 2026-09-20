"""Doing the work is not the same as being in the profession.

V3 Fix B. `title_gate._families` handed a family's whole board vocabulary to
any family with strong work-mode evidence. For a Salesforce Business Analyst
that meant onboarding dealers bought the recruiting vocabulary, configuring
users and roles bought `infrastructure`, and reports and dashboards bought
`data analyst`. The evidence was real; the professions were not theirs.

  CORE       named by a grounded held title, or matched by the stated target
             -> full FAMILY_TITLES vocabulary, unchanged
  PERIPHERAL strong work-mode evidence only
             -> just the titles the candidate's own evidence already names

What these tests defend, in order of how badly each would hurt:

  * a real profession still gets its full vocabulary. Narrowing a core family
    would starve the very people Step 4 exists to serve
  * provenance stays one-directional. The evidence text is built from candidate
    sources ONLY, so a family vocabulary can never manufacture the hint that
    would readmit the family
  * a peripheral family may still surface a title the candidate genuinely
    names, which is how `salesforce administrator` survives while
    `infrastructure` does not
  * with the flag off, behaviour is the Fix-A baseline
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

import family_centrality as fc   # noqa: E402
import title_gate as tg          # noqa: E402

FT = tg.FAMILY_TITLES


def role(supports=None, titles=None, target_family=None):
    return {"supports": dict(supports or {}),
            "title_families": dict(titles or {}),
            "stated_target": {"family": target_family}}


def gate(role_rec, target="", held_titles=(), hints=(), queries=()):
    """The real title_gate.build, with Fix B on."""
    data = {"title_hints": list(hints),
            "role_keywords": list(queries),
            "role_signals": {"target_field": target,
                             "employment_titles": list(held_titles),
                             "titles": list(held_titles)},
            "role_evidence": role_rec}
    hints_out, record = tg.build(data, ["software engineer"])
    return [h.strip().lower() for h in hints_out], record


class FixBCase(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get(fc.FLAG)
        os.environ[fc.FLAG] = "1"

    def tearDown(self):
        os.environ.pop(fc.FLAG, None)
        if self.saved is not None:
            os.environ[fc.FLAG] = self.saved


class CoreFamilies(FixBCase):

    def test_1_core_via_held_title_gets_full_vocabulary(self):
        r = role({"product": "strong"}, {"product": ["Technical Product Manager"]})
        g, rec = gate(r, held_titles=["Technical Product Manager"])
        self.assertIn("product", rec["family_centrality"]["core"])
        for frag in FT["product"]:
            self.assertIn(frag, g, frag)

    def test_2_core_via_target_field_words_gets_full_vocabulary(self):
        r = role({"business_analysis": "strong"})
        g, rec = gate(r, target="business analysis",
                      held_titles=["Business Analyst"])
        self.assertIn("business_analysis", rec["family_centrality"]["core"])
        for frag in FT["business_analysis"]:
            self.assertIn(frag, g, frag)

    def test_6_no_held_title_at_all_can_still_be_core_via_target(self):
        """The graduate case. Requiring a held title punishes people who have
        none yet."""
        r = role({"software_engineering": "strong"})
        g, rec = gate(r, target="software engineering", hints=["backend"])
        self.assertIn("software_engineering", rec["family_centrality"]["core"])
        self.assertIn("software engineer", g)

    def test_7_two_legitimate_core_families_both_expand(self):
        r = role({"business_analysis": "strong", "functional_consulting": "strong"},
                 {"functional_consulting": ["Functional Consultant"]})
        g, rec = gate(r, target="business analysis",
                      held_titles=["Functional Consultant"])
        core = rec["family_centrality"]["core"]
        self.assertIn("business_analysis", core)
        self.assertIn("functional_consulting", core)
        self.assertIn("business systems analyst", g)
        self.assertIn("functional consultant", g)


class PeripheralFamilies(FixBCase):

    def test_3_support_activity_does_not_buy_the_support_vocabulary(self):
        r = role({"functional_consulting": "strong", "support": "strong"},
                 {"functional_consulting": ["Functional Consultant"]})
        g, rec = gate(r, held_titles=["Functional Consultant"])
        self.assertIn("support", rec["family_centrality"]["peripheral"])
        for frag in ("technical support", "support engineer", "help desk",
                     "support specialist"):
            self.assertNotIn(frag, g, frag)

    def test_4_a_title_the_candidate_names_survives(self):
        r = role({"functional_consulting": "strong", "it_administration": "strong"},
                 {"functional_consulting": ["Functional Consultant"]})
        g, _rec = gate(r, held_titles=["Functional Consultant"],
                       queries=["salesforce administrator"])
        self.assertIn("administrator", g)
        self.assertTrue(tg.admits(g, "Salesforce Administrator"))

    def test_5_and_it_does_not_drag_in_the_rest_of_its_family(self):
        r = role({"functional_consulting": "strong", "it_administration": "strong"},
                 {"functional_consulting": ["Functional Consultant"]})
        g, _rec = gate(r, held_titles=["Functional Consultant"],
                       queries=["salesforce administrator"])
        for frag in ("infrastructure", "systems engineer", "network administrator",
                     "sysadmin", "it support"):
            self.assertNotIn(frag, g, frag)
        self.assertFalse(tg.admits(g, "Infrastructure Security Engineer"))

    def test_8_strong_analytics_does_not_manufacture_data_analyst(self):
        r = role({"functional_consulting": "strong", "data_analytics": "strong"},
                 {"functional_consulting": ["Functional Consultant"]})
        g, _rec = gate(r, held_titles=["Functional Consultant"])
        self.assertNotIn("data analyst", g)
        self.assertFalse(tg.admits(g, "Data Analyst"))

    def test_8b_unless_the_candidate_actually_names_it(self):
        r = role({"functional_consulting": "strong", "data_analytics": "strong"},
                 {"functional_consulting": ["Functional Consultant"]})
        g, _rec = gate(r, held_titles=["Functional Consultant"],
                       queries=["data analyst"])
        self.assertIn("data analyst", g)


class ProvenanceIsDirectional(FixBCase):
    """The safety property: a family vocabulary must never feed itself."""

    def test_the_evidence_text_is_built_from_candidate_sources_only(self):
        text = fc.evidence_text(["business analyst"], ["salesforce admin"],
                                ["functional consultant"], tg.normalize)
        self.assertIn("business analyst", text)
        self.assertIn("functional consultant", text)
        # nothing the candidate did not say
        self.assertNotIn("infrastructure", text)

    def test_a_peripheral_family_cannot_readmit_itself(self):
        """recruiting evidence, nothing naming a recruiting title -> nothing."""
        r = role({"functional_consulting": "strong", "hr_recruiting": "strong"},
                 {"functional_consulting": ["Functional Consultant"]})
        g, rec = gate(r, held_titles=["Functional Consultant"])
        self.assertIn("hr_recruiting", rec["family_centrality"]["peripheral"])
        for frag in FT["hr_recruiting"]:
            self.assertNotIn(frag, g, frag)

    def test_core_families_alone_filter_the_candidates_own_hints(self):
        """A peripheral family does not widen the own-hint filter."""
        r = role({"functional_consulting": "strong", "hr_recruiting": "strong"},
                 {"functional_consulting": ["Functional Consultant"]})
        _g, rec = gate(r, held_titles=["Functional Consultant"],
                       hints=["recruiter"])
        self.assertIn("recruiter", rec["own_hints_dropped"])

    def test_target_words_not_family_of(self):
        """family_of returns None for a description; the word method does not."""
        import role_evidence
        self.assertIsNone(role_evidence.family_of("business analysis"))
        self.assertIn("business_analysis",
                      fc.target_families("business analysis", FT, tg.normalize))

    def test_no_ranking_or_weight_policy_lives_here(self):
        import ast, inspect
        tree = ast.parse(inspect.getsource(fc))
        tree.body = [n for n in tree.body
                     if not (isinstance(n, ast.Expr)
                             and isinstance(n.value, ast.Constant)
                             and isinstance(n.value.value, str))]
        code = "\n".join(ast.unparse(n) for n in tree.body).replace('"""', "")
        for forbidden in ("score", "weight", "SKILL", "roles", "users"):
            self.assertNotIn(forbidden, code)


class OffByDefault(unittest.TestCase):

    def setUp(self):
        self.saved = os.environ.get(fc.FLAG)
        os.environ.pop(fc.FLAG, None)

    def tearDown(self):
        os.environ.pop(fc.FLAG, None)
        if self.saved is not None:
            os.environ[fc.FLAG] = self.saved

    def test_9_the_gate_is_off_unless_asked_for(self):
        self.assertFalse(fc.enabled())

    def test_the_flag_is_read_per_call(self):
        os.environ[fc.FLAG] = "1"
        self.assertTrue(fc.enabled())
        os.environ[fc.FLAG] = "0"
        self.assertFalse(fc.enabled())

    def test_9b_flag_off_is_the_fix_a_baseline(self):
        """Strong peripheral families still expand fully with the flag off."""
        r = role({"functional_consulting": "strong", "support": "strong"},
                 {"functional_consulting": ["Functional Consultant"]})
        for value in ("0", None):
            os.environ.pop(fc.FLAG, None)
            if value:
                os.environ[fc.FLAG] = value
            g, rec = gate(r, held_titles=["Functional Consultant"])
            self.assertIn("support engineer", g)
            self.assertNotIn("family_centrality", rec)

    def test_10_flag_on_changes_exactly_that(self):
        r = role({"functional_consulting": "strong", "support": "strong"},
                 {"functional_consulting": ["Functional Consultant"]})
        os.environ[fc.FLAG] = "1"
        g, rec = gate(r, held_titles=["Functional Consultant"])
        self.assertNotIn("support engineer", g)
        self.assertIn("family_centrality", rec)

    def test_it_is_not_the_same_flag_as_step_4(self):
        self.assertNotEqual(fc.FLAG, tg.FLAG)


if __name__ == "__main__":
    unittest.main(verbosity=2)
