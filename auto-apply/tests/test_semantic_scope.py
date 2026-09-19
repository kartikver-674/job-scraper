"""A cue governs its own clause item, not everything that shares a comma.

V3 Step 8, fixing audit defect V3-C16. `skill_evidence.classify` already told
USED from PLANNED, LEARNING and NEGATED. Those definitions are untouched here;
only the distance a cue reaches has changed.

The shape that actually did the damage is not the one the defect is usually
told with. It is the skills line:

    Scrum, Agile, Kanban, Backlog Refinement, Sprint Planning, Retrospectives

The word "Planning" inside the skill "Sprint Planning" was read as an intent
cue over the whole line, so Scrum, Kanban and Jira came back PLANNED. PLANNED
is not in CLAIMED, so those skills lost their evidence and their tier.

What these tests defend, in order of how badly each would hurt:

  * coordination survives. "Learning React, TypeScript and Next.js" is three
    LEARNING, and "did not use Java or Kotlin" negates both. A fix that stopped
    leakage by cutting at every comma would score better on leaks and be wrong
  * a cue never reaches into a following independent predicate
  * negation still crosses "but" when the DENIAL follows the concept, and never
    when a new positive predicate does
  * no window is ever WIDENED
  * classification does not depend on formatting
  * with the flag off, behaviour is byte-identical

Every fixture is written here. No holdout candidate, label or result is used.
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

import semantic_scope as ss     # noqa: E402
import skill_evidence as se     # noqa: E402

NEG, PLAN, LEARN = se.NEGATED, se.PLANNED, se.LEARNING
USED, MENT = se.USED, se.MENTIONED


class ScopeCase(unittest.TestCase):
    """Every test here runs with Step 8 ON unless it says otherwise."""

    def setUp(self):
        self.saved = os.environ.get(ss.FLAG)
        os.environ[ss.FLAG] = "1"

    def tearDown(self):
        os.environ.pop(ss.FLAG, None)
        if self.saved is not None:
            os.environ[ss.FLAG] = self.saved

    def status(self, sentence, concept, section=se.OTHER):
        low = sentence.lower()
        at = low.index(concept.lower())
        return se.classify(low, at, at + len(concept), section,
                           (0, len(low)))

    def assertStatus(self, sentence, concept, want):
        got = self.status(sentence, concept)
        self.assertEqual(got, want,
                         f"{concept!r} in {sentence!r} was {got}, wanted {want}")


class RequiredShapes(ScopeCase):
    """§7 A-J, verbatim."""

    def test_a_planning_does_not_reach_the_next_predicate(self):
        s = "Planning Salesforce migration, gathered requirements and ran UAT"
        self.assertStatus(s, "Salesforce", PLAN)
        self.assertStatus(s, "migration", PLAN)
        # The defect: these were PLANNED because they shared a sentence.
        self.assertNotEqual(self.status(s, "requirements"), PLAN)

    def test_b_coordinated_objects_of_one_cue_both_learn(self):
        s = "Learning React and TypeScript"
        self.assertStatus(s, "React", LEARN)
        self.assertStatus(s, "TypeScript", LEARN)

    def test_c_learning_does_not_leak_into_a_built_predicate(self):
        s = "Learning React, built Node APIs"
        self.assertStatus(s, "React", LEARN)
        self.assertStatus(s, "Node", USED)
        self.assertStatus(s, "APIs", USED)

    def test_d_a_semicolon_separates_roadmap_from_delivery(self):
        s = "Roadmap for data migration; developed reporting dashboards"
        self.assertStatus(s, "migration", PLAN)
        self.assertStatus(s, "dashboards", USED)

    def test_e_or_coordinates_two_negated_objects(self):
        s = "Did not write Apex or LWC"
        self.assertStatus(s, "Apex", NEG)
        self.assertStatus(s, "LWC", NEG)

    def test_f_negation_does_not_cross_but_into_a_positive_predicate(self):
        s = "Did not write Apex, but configured Salesforce"
        self.assertStatus(s, "Apex", NEG)
        self.assertStatus(s, "Salesforce", USED)

    def test_g_an_intent_to_learn_is_not_use(self):
        for concept in ("Python", "SQL"):
            self.assertNotEqual(self.status("Plans to learn Python and SQL",
                                            concept), USED)

    def test_h_genuine_coordination_under_one_cue_is_all_planned(self):
        s = "Planned API migration and frontend redesign"
        for concept in ("API", "migration", "frontend", "redesign"):
            self.assertStatus(s, concept, PLAN)

    def test_i_a_comma_list_with_no_coordination_is_not_all_planned(self):
        s = "Planned API migration, React, Node, PostgreSQL"
        self.assertStatus(s, "API", PLAN)
        for concept in ("React", "Node", "PostgreSQL"):
            self.assertNotEqual(self.status(s, concept), PLAN,
                                "a bare comma list was scoped to the cue")

    def test_j_state_is_decided_per_occurrence_then_aggregated(self):
        s = "Built Python services. Learning Python in my own time."
        low = s.lower()
        first, second = low.index("python"), low.rindex("python")
        self.assertEqual(se.classify(low, first, first + 6, se.OTHER,
                                     (0, len(low))), USED)
        self.assertEqual(se.classify(low, second, second + 6, se.OTHER,
                                     (0, len(low))), LEARN)


class TheDefectItself(ScopeCase):
    """The shape that produced 53 of the 56 PLANNED occurrences."""

    SKILLS = ("Scrum, Agile, Kanban, Backlog Refinement, Sprint Planning, "
              "Retrospectives, Jira")
    FIRST = "Demand Planning, Forecasting, Inventory Management, SAP, Excel"

    def test_a_cue_inside_a_sibling_item_governs_nothing_else(self):
        for concept in ("Scrum", "Kanban", "Retrospectives", "Jira"):
            self.assertNotEqual(self.status(self.SKILLS, concept), PLAN,
                                f"{concept} was planned by a sibling skill name")

    def test_a_cue_in_the_FIRST_item_is_still_an_item(self):
        """'Demand Planning' names a skill. It does not plan the rest."""
        for concept in ("Forecasting", "Inventory Management", "SAP", "Excel"):
            self.assertNotEqual(self.status(self.FIRST, concept), PLAN)

    def test_the_skill_that_carries_the_cue_is_a_skill_not_an_intent(self):
        """"Sprint Planning" is a thing this person can do. A cue INSIDE the
        concept's own span was never read as governing it, before or after."""
        self.assertEqual(self.status(self.SKILLS, "Sprint Planning"), MENT)
        self.assertIn(MENT, se.CLAIMED)

    def test_a_planned_occurrence_is_not_claimed_evidence(self):
        """Why the leak mattered: PLANNED drops out of the evidence entirely."""
        self.assertNotIn(se.PLANNED, se.CLAIMED)
        self.assertIn(se.MENTIONED, se.CLAIMED)


class CoordinationSurvives(ScopeCase):
    """§11. A fix that destroys coordination is not acceptable."""

    def test_a_comma_list_closed_by_and_is_one_coordinate_object(self):
        s = "Learning React, TypeScript and Next.js"
        for concept in ("React", "TypeScript", "Next.js"):
            self.assertStatus(s, concept, LEARN)

    def test_or_coordinates_negation(self):
        s = "Did not use Java or Kotlin"
        self.assertStatus(s, "Java", NEG)
        self.assertStatus(s, "Kotlin", NEG)

    def test_a_verb_governs_its_whole_object_list(self):
        s = "Built services with Node.js, Express and PostgreSQL"
        for concept in ("Node.js", "Express", "PostgreSQL"):
            self.assertStatus(s, concept, USED)

    def test_a_lead_in_is_not_required_to_be_the_first_word(self):
        s = "In 2024 I was learning React, TypeScript and Next.js"
        self.assertStatus(s, "Next.js", LEARN)


class NegationDirection(ScopeCase):
    """§4. Measured separately from planned/learning scope."""

    def test_a_denial_after_the_concept_still_crosses_but(self):
        self.assertStatus("Evaluated Kubernetes but did not adopt it",
                          "Kubernetes", NEG)

    def test_a_new_positive_predicate_after_but_is_not_negated(self):
        self.assertStatus("Did not adopt Kafka, but shipped RabbitMQ",
                          "RabbitMQ", USED)

    def test_no_experience_still_negates(self):
        self.assertStatus("No experience developing APIs", "APIs", NEG)

    def test_a_bare_not_never_negates(self):
        """The old false-negation control, unchanged."""
        self.assertStatus("Fixed an issue where Redis was not reconnecting",
                          "Redis", USED)

    def test_the_denial_verb_list_is_separately_switchable(self):
        """Sub-rule 4 is cue DETECTION, not scope, so review can refuse it."""
        os.environ[ss.FLAG + "_NEGATION_VERBS"] = "0"
        try:
            self.assertNotEqual(self.status("Did not write Apex or LWC",
                                            "Apex"), NEG)
        finally:
            os.environ.pop(ss.FLAG + "_NEGATION_VERBS", None)
        self.assertStatus("Did not write Apex or LWC", "Apex", NEG)

    def test_the_verb_inside_a_denial_is_not_evidence_of_doing_it(self):
        """'write' is an action verb; inside 'did not write' it is not use."""
        self.assertStatus("Did not write Apex, but configured Salesforce",
                          "Apex", NEG)


class NeverWidens(ScopeCase):
    """Step 8 may only narrow. Widening is outside its mandate."""

    def test_narrow_takes_the_intersection(self):
        self.assertEqual(ss.narrow((0, 100), (10, 40)), (10, 40))
        self.assertEqual(ss.narrow((10, 40), (0, 100)), (10, 40))
        self.assertEqual(ss.narrow((0, 50), (25, 100)), (25, 50))

    def test_planned_keeps_its_clause_break_ceiling(self):
        """_governed alone would WIDEN planned forward past a comma list."""
        s = "Salesforce, Sales Cloud, CPQ, Requirements Gathering, Planning"
        self.assertNotEqual(self.status(s, "Salesforce"), PLAN)

    def test_no_status_moves_away_from_used_by_scope_alone(self):
        """Narrowing can only remove a cue, never add one."""
        for sentence, concept in (
                ("Built services with Node.js, Express and PostgreSQL", "Express"),
                ("Implemented Redis caching, documented Kafka options", "Kafka"),
                ("Migrated the ledger to PostgreSQL", "PostgreSQL")):
            os.environ.pop(ss.FLAG, None)
            before = self.status(sentence, concept)
            os.environ[ss.FLAG] = "1"
            self.assertEqual(self.status(sentence, concept), before)


class FormatInvariance(ScopeCase):
    """§10. Classification is about grammar, not about typography."""

    def test_capitalisation_does_not_change_the_verdict(self):
        for s in ("Learning React, built Node APIs",
                  "learning react, built node apis",
                  "LEARNING REACT, BUILT NODE APIS"):
            self.assertEqual(self.status(s, "node"), USED)

    def test_a_soft_wrap_is_not_a_clause_break(self):
        s = "Built services using Node.js,\n  Express and MongoDB"
        self.assertStatus(s, "Express", USED)

    def test_semicolon_and_full_stop_agree_where_meaning_is_equivalent(self):
        a = "Roadmap for data migration; developed reporting dashboards"
        b = "Roadmap for data migration. Developed reporting dashboards"
        self.assertEqual(self.status(a, "dashboards"),
                         self.status(b, "dashboards"))

    def test_skill_order_in_a_list_does_not_decide_the_verdict(self):
        """The leak was order-dependent: everything after the cue changed."""
        a = "Scrum, Agile, Sprint Planning, Retrospectives, Jira"
        b = "Jira, Retrospectives, Sprint Planning, Agile, Scrum"
        self.assertEqual(self.status(a, "Jira"), self.status(b, "Jira"))
        self.assertEqual(self.status(a, "Scrum"), self.status(b, "Scrum"))

    def test_bullet_order_does_not_decide_the_verdict(self):
        one = "- Learning React\n- Built Node APIs"
        two = "- Built Node APIs\n- Learning React"
        self.assertEqual(self.status(one, "Node"), self.status(two, "Node"))

    def test_extra_spacing_does_not_decide_the_verdict(self):
        for s in ("Learning React,  built  Node APIs",
                  "Learning React,\tbuilt Node APIs"):
            self.assertEqual(self.status(s, "Node"), USED)

    def test_a_duplicate_alias_occurrence_is_judged_on_its_own_clause(self):
        s = "Learning Python. Built Python services."
        low = s.lower()
        a, b = low.index("python"), low.rindex("python")
        self.assertEqual(se.classify(low, a, a + 6, se.OTHER, (0, len(low))),
                         LEARN)
        self.assertEqual(se.classify(low, b, b + 6, se.OTHER, (0, len(low))),
                         USED)


class Governance(ScopeCase):
    """The rule itself, and the reasons it gives."""

    def test_same_item_needs_no_decision(self):
        ok, why = ss.governs("learning react", 0, 14, (0, 8), 9, 14)
        self.assertTrue(ok)
        self.assertIn("same clause item", why)

    def test_a_sibling_item_is_refused_with_its_reason(self):
        text = "scrum, agile, sprint planning, jira"
        ok, why = ss.governs(text, 0, len(text), (21, 29), 31, 35)
        self.assertFalse(ok)
        self.assertIn("sibling", why)

    def test_a_cue_that_ends_its_item_is_refused(self):
        text = "demand planning, forecasting, excel"
        ok, why = ss.governs(text, 0, len(text), (7, 15), 30, 35)
        self.assertFalse(ok)
        self.assertIn("ends its own", why)

    def test_a_lead_in_without_coordination_is_refused(self):
        text = "planned api migration, react, node"
        ok, why = ss.governs(text, 0, len(text), (0, 7), 30, 34)
        self.assertFalse(ok)
        self.assertIn("coordination", why)

    def test_no_role_or_tier_policy_lives_here(self):
        import inspect
        source = inspect.getsource(ss)
        for forbidden in ("CORE", "STRONG_SECONDARY", "SUPPORTING",
                          "role_evidence", "family_of", "weight"):
            self.assertNotIn(forbidden, source)


class Explainability(ScopeCase):
    """§16. Span, cue, boundary, verdict. No score."""

    def test_a_decision_carries_the_cue_and_its_boundary(self):
        s = "learning react, built node apis"
        record = {}
        se.classify(s, 9, 14, se.OTHER, (0, len(s)), record)
        row = record["decisions"][0]
        for field in ("span", "cue", "cue_family", "cue_position", "scope",
                      "reason"):
            self.assertIn(field, row)
        self.assertEqual(row["cue"], "learning")
        self.assertEqual(row["cue_family"], "LEARNING")
        self.assertTrue(ss.explain({"decisions": [dict(row, old=MENT,
                                                       new=LEARN)]}).strip())

    def test_the_record_is_optional_and_additive(self):
        s = "learning react, built node apis"
        self.assertEqual(se.classify(s, 9, 14, se.OTHER, (0, len(s))), LEARN)


class OffByDefault(unittest.TestCase):
    """§15. Clean rollback."""

    def setUp(self):
        self.saved = os.environ.get(ss.FLAG)
        os.environ.pop(ss.FLAG, None)

    def tearDown(self):
        os.environ.pop(ss.FLAG, None)
        if self.saved is not None:
            os.environ[ss.FLAG] = self.saved

    def test_the_guard_is_off_unless_asked_for(self):
        self.assertFalse(ss.enabled())

    def test_the_flag_is_read_per_call(self):
        os.environ[ss.FLAG] = "1"
        self.assertTrue(ss.enabled())
        os.environ[ss.FLAG] = "0"
        self.assertFalse(ss.enabled())

    def test_with_the_flag_off_the_old_leak_is_still_there(self):
        """Proof the OFF path is the original code, not a quiet fix."""
        s = "learning react, built node apis"
        self.assertEqual(se.classify(s, 22, 26, se.OTHER, (0, len(s))), LEARN)

    def test_off_reproduces_the_documented_baseline_shapes(self):
        for sentence, concept, want in (
                ("planning salesforce migration, gathered requirements", 
                 "requirements", PLAN),
                ("scrum, agile, sprint planning, jira", "jira", PLAN),
                ("built services with node.js, express and postgresql",
                 "express", USED)):
            at = sentence.index(concept)
            self.assertEqual(
                se.classify(sentence, at, at + len(concept), se.OTHER,
                            (0, len(sentence))), want, concept)


if __name__ == "__main__":
    unittest.main(verbosity=2)
