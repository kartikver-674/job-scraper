"""Sharing a clause is not the same as being what the verb acted on.

V3 Fix A. `role_evidence._governed_hits` recorded development evidence whenever
a CONSTRUCT verb and an engineering ARTEFACT appeared anywhere in one governed
clause. It never asked whether the artefact was the verb's object, so

    "Authored FSDs for the Supplier Warranty Recovery module"

read as `authored` + `module` and became development evidence. What was
authored is a specification document. On a real business-analyst résumé this
granted `software_engineering` and `ml_engineering`, and Step 4 then put
twenty-eight engineering titles into the free-source title gate.

What these tests defend, in order of how badly each would hurt:

  * genuine construction still counts. "built an API", "developed the
    platform", "implemented microservices" are the whole point of the mode and
    a guard that lost them would be worse than the defect
  * a coordinated object is still an object -- "built reports and dashboards"
  * delegation is untouched. The React Native rule decided before this guard
    runs and is never re-judged, so "worked with the React Native team" still
    confers nothing and still records consulting
  * with the flag off, behaviour is identical

The regression fixture at the bottom is derived from the résumé that exposed
this. It carries no name, contact details, employer or client names -- only the
wording the mechanism needs.
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_APPLY = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(AUTO_APPLY)
for path in (REPO_ROOT, AUTO_APPLY):
    if path not in sys.path:
        sys.path.insert(0, path)

import attachment_guard as ag      # noqa: E402
import role_evidence as re3        # noqa: E402

VERB = re3._CONSTRUCT_RX
ART = re3._ARTEFACT_RX


# A business analyst on a Salesforce delivery programme. No name, no contact
# details, no employer or client names. Every line below exists because the
# mechanism needs it: the held title, the FSD/module wording, the
# reports/dashboards wording, and the Salesforce functional/admin context.
FIXTURE = """Business Analyst | Salesforce Functional Consultant

PROFESSIONAL SUMMARY
Business Analyst delivering Salesforce-based Dealer Management System and CRM
solutions. Strong in requirement elicitation, BRD and Functional Specification
Document (FSD) authoring, As-Is/To-Be process mapping, and User Acceptance
Testing.

CORE SKILLS
Salesforce Platform: Sales Cloud, Service Cloud, Salesforce Administration,
Lightning Pages, Users, Roles, Profiles & Permission Sets, Reports & Dashboards
Business Analysis: Requirement Elicitation, BRD & FSD Documentation, UAT

PROFESSIONAL EXPERIENCE
Senior Functional Consultant / Business Analyst
- Authored FSDs for the Supplier Warranty Recovery module, defining custom
  screens that let business teams log failed parts.
- Gathered business requirements and authored FSDs across multiple DMS modules,
  managing traceability from requirement to test case.
- Built custom reports and dashboards to give business teams visibility on
  sales and service performance.
- Configured users, roles, profiles, and permission sets aligned to the client
  organisational hierarchy.
- Worked with the React Native team to deliver a Sales Force Automation mobile
  app covering Visit Plan and Order Taking.
"""


def attaches(sentence, artefact):
    """(ok, reason) for one artefact in one clause."""
    low = sentence.lower()
    at = low.index(artefact.lower())
    return ag.governs(low, 0, len(low), (at, at + len(artefact)), VERB)


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get(ag.FLAG)
        os.environ[ag.FLAG] = "1"

    def tearDown(self):
        os.environ.pop(ag.FLAG, None)
        if self.saved is not None:
            os.environ[ag.FLAG] = self.saved


class FalseAttachment(GuardCase):
    """The defect. The verb acted on something else."""

    def test_authored_fsds_for_the_module(self):
        ok, why = attaches(
            "Authored FSDs for the Supplier Warranty Recovery module", "module")
        self.assertFalse(ok)
        self.assertIn("does not act on", why)

    def test_authored_fsds_across_multiple_modules(self):
        self.assertFalse(
            attaches("authored FSDs across multiple DMS modules", "modules")[0])

    def test_a_later_unrelated_artefact_is_not_the_object(self):
        self.assertFalse(attaches(
            "Built custom reports and dashboards to give business teams "
            "visibility on sales and service performance", "service")[0])

    def test_a_job_title_is_not_an_act_of_construction(self):
        self.assertFalse(attaches(
            "Java engineer with 8 years on Spring Boot microservices",
            "microservices")[0])

    def test_a_clause_break_separates_verb_from_artefact(self):
        self.assertFalse(attaches(
            "Authored the specification, and the vendor owned the service",
            "service")[0])


class TrueAttachment(GuardCase):
    """The mode's whole purpose. Losing these would be worse than the defect."""

    def test_built_an_api(self):
        ok, why = attaches("Built an API for dealer onboarding", "API")
        self.assertTrue(ok, why)

    def test_developed_the_customer_portal(self):
        self.assertTrue(attaches("Developed the customer portal application",
                                 "application")[0])

    def test_implemented_microservices(self):
        self.assertTrue(attaches(
            "Implemented microservices for payment processing",
            "microservices")[0])

    def test_intervening_determiners_and_adjectives_are_fine(self):
        for s in ("built a highly available payment service",
                  "developed our new internal reporting application",
                  "implemented the second-generation billing component"):
            self.assertTrue(attaches(s, s.split()[-1])[0], s)

    def test_a_coordinated_object_is_still_the_object(self):
        self.assertTrue(attaches("built reports and dashboards and an API",
                                 "API")[0])

    def test_a_preposition_AFTER_the_artefact_says_nothing(self):
        self.assertTrue(attaches("built an integration with the ERP",
                                 "integration")[0])

    def test_the_verb_may_follow_the_artefact(self):
        self.assertTrue(attaches("the payment service was rebuilt in Go",
                                 "service")[0])

    def test_no_construct_verb_is_not_this_guards_decision(self):
        ok, why = attaches("owns the billing service", "service")
        self.assertTrue(ok)
        self.assertIn("no construct verb", why)


class DelegationIsUntouched(GuardCase):
    """Decided before the guard runs, and never re-judged by it."""

    def test_the_react_native_team_still_confers_nothing(self):
        role = re3.build(FIXTURE, {"employment_titles": [], "titles": [],
                                   "target_field": ""}, [])
        self.assertNotIn("mobile", role["supports"])
        for ev in role["evidence"].get("development", []):
            self.assertNotIn("react native", ev["quote"].lower())

    def test_someone_else_acting_is_still_delegation_not_development(self):
        text = "- Coordinated with the developers who built the Apex triggers."
        low = text.lower()
        hits = re3._governed_hits(text, low, re3.se.sections(text),
                                  re3._ARTEFACT_RX, re3._CONSTRUCT_RX,
                                  re3.DEVELOPMENT)
        self.assertTrue(hits)
        self.assertTrue(all(h["kind"] == "delegated" for h in hits))


class TheRegressionCase(GuardCase):
    """The résumé shape that exposed this, reduced to what the defect needs."""

    def record(self):
        return re3.build(FIXTURE, {
            "employment_titles": ["Senior Functional Consultant / Business Analyst"],
            "titles": ["Senior Functional Consultant / Business Analyst"],
            "target_field": "business analysis"}, [])

    def test_a_business_analyst_is_not_a_software_engineer(self):
        supports = self.record()["supports"]
        self.assertNotIn("software_engineering", supports)
        self.assertNotIn("ml_engineering", supports)

    def test_the_functional_identity_survives(self):
        supports = self.record()["supports"]
        self.assertIn("functional_consulting", supports)

    def test_with_the_guard_off_the_defect_is_still_there(self):
        """Proof the OFF path is the original code, not a quiet fix."""
        os.environ[ag.FLAG] = "0"
        supports = self.record()["supports"]
        self.assertIn("software_engineering", supports)
        self.assertIn("ml_engineering", supports)

    def test_the_offending_hits_are_the_fsd_ones(self):
        os.environ[ag.FLAG] = "0"
        before = re3.build(FIXTURE, {}, [])["evidence"].get("development", [])
        os.environ[ag.FLAG] = "1"
        after = re3.build(FIXTURE, {}, [])["evidence"].get("development", [])
        self.assertGreater(len(before), len(after))
        gone = {" ".join(e["quote"].split()) for e in before} - \
               {" ".join(e["quote"].split()) for e in after}
        self.assertTrue(any("fsd" in g.lower() for g in gone), gone)


class OffByDefault(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get(ag.FLAG)
        os.environ.pop(ag.FLAG, None)

    def tearDown(self):
        os.environ.pop(ag.FLAG, None)
        if self.saved is not None:
            os.environ[ag.FLAG] = self.saved

    def test_the_guard_is_off_unless_asked_for(self):
        self.assertFalse(ag.enabled())

    def test_the_flag_is_read_per_call(self):
        os.environ[ag.FLAG] = "1"
        self.assertTrue(ag.enabled())
        os.environ[ag.FLAG] = "0"
        self.assertFalse(ag.enabled())

    def test_no_role_or_tier_policy_lives_here(self):
        """Scan the CODE, not the prose. The docstring names the families the
        defect produced, which is exactly what it should do."""
        import ast, inspect
        tree = ast.parse(inspect.getsource(ag))
        tree.body = [n for n in tree.body
                     if not (isinstance(n, ast.Expr)
                             and isinstance(n.value, ast.Constant)
                             and isinstance(n.value.value, str))]
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ast.get_docstring(node)  # present, but excluded below
        code = "\n".join(
            ast.unparse(n) for n in tree.body).replace('"""', "")
        for forbidden in ("software_engineering", "ml_engineering", "FAMILIES",
                          "title_gate", "weight", "score"):
            self.assertNotIn(forbidden, code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
