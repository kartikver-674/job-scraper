"""Appearing in a résumé is not the same as having used the thing.

R4a. The engine had one semantic bit — `Evidence.planned` — set by a
rule that only looked BEFORE the occurrence and treated any line break
as a sentence end. Everything else that a mention can MEAN was invisible,
so a mention inside a work section became professional experience by
position alone. Measured on the pre-R4a HEAD, 13 of these 16 sentences
tiered CORE, including "Evaluated but never used Kubernetes."

These are adversarial on purpose and split into two halves that pull
against each other: the sentences that must STOP being use, and the
control sentences that must KEEP being use. A rule that passes only the
first half is a rule that has learned to fear the word "not".

The classifier is deterministic and local. No model is asked; there is
no LLM call anywhere in this path.
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

import make_profile  # noqa: E402
import skill_concepts as sc  # noqa: E402
import skill_evidence as se  # noqa: E402


def document(body, heading="Professional Experience", entry="Acme Corp"):
    return (f"Dana Reed\nSenior Engineer\n{heading}\n{entry}\n"
            f"Software Engineer  Jan 2023 - Present\n{body}\n")


def assess(term, body, **kw):
    """One concept through the real assessment path."""
    text = document(body, **kw)
    rows = se.assess_all(sc.from_weights({term: 3}), text)
    return rows[0]


def statuses(term, body, **kw):
    return [o["status"] for o in assess(term, body, **kw)["occurrences"]]


# --------------------------------------------------------------------------
# The 24-case adversarial matrix
# --------------------------------------------------------------------------

class NotUse(unittest.TestCase):
    """Sentences that name a technology without claiming it was used."""

    def check(self, term, body, status, **kw):
        row = assess(term, body, **kw)
        self.assertEqual(row["occurrences"][0]["status"], status,
                         f"{body!r} -> {row['occurrences']}")
        self.assertNotEqual(row["tier"], se.CORE, f"{body!r} -> {row['why']}")
        return row

    # --- planned / future, marker AFTER the name (the whole miss) ---------
    def test_01_is_planned_for(self):
        self.check("kubernetes", "- Kubernetes is planned for next quarter.",
                   se.PLANNED)

    def test_02_will_be_introduced(self):
        self.check("kubernetes", "- Kubernetes will be introduced next year.",
                   se.PLANNED)

    def test_03_is_on_the_roadmap(self):
        self.check("kafka", "- Kafka is on the roadmap for H2.", se.PLANNED)

    # --- planned, marker BEFORE (must keep working) ----------------------
    def test_04_plan_to_migrate(self):
        self.check("kubernetes", "- Plan to migrate to Kubernetes.",
                   se.PLANNED)

    def test_05_considering(self):
        self.check("graphql", "- Considering GraphQL for the next release.",
                   se.PLANNED)

    # --- planned, across a soft wrap -------------------------------------
    def test_06_wrapped_before(self):
        self.check("kubernetes", "- Plan to migrate to\n  Kubernetes.",
                   se.PLANNED)

    def test_07_wrapped_after(self):
        self.check("kubernetes", "- Kubernetes is\n  planned for Q3.",
                   se.PLANNED)

    # --- negation --------------------------------------------------------
    def test_08_never_used(self):
        self.check("kubernetes", "- Evaluated but never used Kubernetes.",
                   se.NEGATED)

    def test_09_not_using(self):
        self.check("docker", "- Not using Docker in this stack.", se.NEGATED)

    def test_10_no_experience_with(self):
        self.check("aws", "- No experience with AWS.", se.NEGATED)

    def test_11_without_using(self):
        self.check("redis", "- Built the cache without using Redis.",
                   se.NEGATED)

    # --- rejection / abandonment ----------------------------------------
    def test_12_rejected_because(self):
        self.check("kubernetes",
                   "- Rejected Kubernetes because of operating costs.",
                   se.NEGATED)

    def test_13_did_not_adopt(self):
        self.check("kubernetes",
                   "- Evaluated Kubernetes but did not adopt it.", se.NEGATED)

    def test_14_chose_something_else(self):
        self.check("redis", "- Considered Redis but chose PostgreSQL.",
                   se.NEGATED)

    def test_15_ruled_out(self):
        self.check("mongodb", "- Ruled out MongoDB after a spike.",
                   se.NEGATED)

    # --- learning / coursework -------------------------------------------
    def test_16_currently_learning(self):
        self.check("rust", "- Currently learning Rust in my own time.",
                   se.LEARNING)

    def test_17_working_through_a_course(self):
        self.check("scala", "- Working through a course on Scala.",
                   se.LEARNING)

    def test_18_relevant_coursework_heading(self):
        row = self.check("tensorflow", "TensorFlow, Computer Vision",
                         se.LEARNING, heading="Relevant Coursework", entry="")
        self.assertEqual(row["tier"], se.BACKGROUND, row["why"])

    def test_19_academic_coursework_heading(self):
        self.check("tensorflow", "TensorFlow and PyTorch", se.LEARNING,
                   heading="Academic Coursework", entry="")

    def test_20_currently_learning_heading(self):
        self.check("rust", "Rust, WebAssembly", se.LEARNING,
                   heading="Currently Learning", entry="")


class StillUse(unittest.TestCase):
    """Control. These must not be swept up by the rules above."""

    def check(self, term, body, **kw):
        row = assess(term, body, **kw)
        self.assertEqual(row["occurrences"][0]["status"], se.USED,
                         f"{body!r} -> {row['occurrences']}")
        return row

    def test_21_false_negation_inside_a_fix(self):
        # "not" is about the reconnect, not about whether Redis was used.
        self.check("redis",
                   "- Fixed an issue where Redis was not reconnecting.")

    def test_22_negative_result_is_still_work(self):
        self.check("kafka",
                   "- Cut Kafka consumer lag so alerts no longer fired.")

    def test_23_planned_marker_governs_only_what_follows(self):
        # The audited résumé's real line. MongoDB was built; the gateway
        # was planned. One line, one comma, two different claims.
        row = self.check(
            "mongodb",
            "- Shipped a wallet escrow ledger in multi-document MongoDB "
            "transactions, Planned for a pluggable payment gateway.")
        self.assertEqual(row["tier"], se.CORE, row["why"])

    def test_24_future_tense_about_the_role_not_the_tool(self):
        self.check("python",
                   "- Automated reporting in Python; the team will double "
                   "next year.")


# --------------------------------------------------------------------------
# Policy: what the statuses do to a tier
# --------------------------------------------------------------------------

class Policy(unittest.TestCase):

    def test_used_requires_positive_evidence(self):
        """A work section is a place, not a claim."""
        listed = assess("kubernetes", "- Redis, Kubernetes, Terraform")
        self.assertEqual(listed["occurrences"][0]["status"], se.MENTIONED)
        self.assertNotEqual(listed["tier"], se.CORE, listed["why"])

    def test_described_work_is_still_core(self):
        used = assess("redis", "- Built the session cache on Redis.")
        self.assertEqual(used["tier"], se.CORE, used["why"])

    def test_project_mention_is_not_project_use(self):
        text = ("Dana Reed\nProjects\n"
                "LendCircle | Node.js, Redis, Stripe\n"
                "- Considered Stripe but shipped with Razorpay.\n"
                "- Built the marketplace on a Node.js backend with Redis.\n")
        rows = {r["id"]: r for r in
                se.assess_all(sc.from_weights({"stripe": 3, "redis": 3}), text)}
        self.assertEqual(rows["redis"]["tier"], se.STRONG_SECONDARY,
                         rows["redis"]["why"])
        self.assertNotEqual(rows["stripe"]["tier"], se.STRONG_SECONDARY,
                            rows["stripe"]["why"])

    def test_skills_list_stays_a_claim_not_a_denial(self):
        """A bare list line is MENTIONED. It is never NEGATED."""
        text = ("Dana Reed\nTechnical Skills\n"
                "Backend: Redis, Kafka, PostgreSQL\n")
        row = se.assess_all(sc.from_weights({"redis": 3}), text)[0]
        self.assertEqual(row["occurrences"][0]["status"], se.MENTIONED)
        self.assertEqual(row["tier"], se.SUPPORTING, row["why"])

    def test_summary_claim_is_supporting_not_core(self):
        text = ("Dana Reed\nSummary\n"
                "Backend engineer who works in Go and Kafka daily.\n")
        row = se.assess_all(sc.from_weights({"kafka": 3}), text)[0]
        self.assertEqual(row["tier"], se.SUPPORTING, row["why"])

    def test_a_negated_skill_does_not_outrank_a_used_one(self):
        text = ("Dana Reed\nProfessional Experience\nAcme Corp\n"
                "- Built the cache on Redis.\n"
                "- Rejected Kubernetes because of operating costs.\n")
        rows = {r["id"]: r for r in se.assess_all(
            sc.from_weights({"redis": 3, "kubernetes": 3}), text)}
        self.assertGreater(se.rank(rows["redis"]["tier"]),
                           se.rank(rows["kubernetes"]["tier"]))
        self.assertGreater(rows["redis"]["weight"],
                           rows["kubernetes"]["weight"])


# --------------------------------------------------------------------------
# Section and entry attribution
# --------------------------------------------------------------------------

class Attribution(unittest.TestCase):

    def test_coursework_after_experience_does_not_inherit_work(self):
        text = ("Dana Reed\nProfessional Experience\nAcme Corp\n"
                "- Built services in Python.\n"
                "Relevant Coursework\n"
                "Distributed Systems, TensorFlow, Computer Vision\n")
        row = se.assess_all(sc.from_weights({"tensorflow": 3}), text)[0]
        self.assertEqual(row["tier"], se.BACKGROUND, row["why"])

    def test_entry_boundary_between_two_employers(self):
        text = ("Dana Reed\nProfessional Experience\n"
                "Northwind Robotics\nBackend Engineer\n"
                "- Built the cache on Redis.\n"
                "Vantage Systems\nPlatform Engineer\n"
                "- Streamed events through Kafka.\n")
        rows = {r["id"]: r for r in se.assess_all(
            sc.from_weights({"redis": 3, "kafka": 3}), text)}
        self.assertEqual(rows["redis"]["why"], "used at Northwind Robotics")
        self.assertEqual(rows["kafka"]["why"], "used at Vantage Systems")

    def test_heading_shapes(self):
        """All-caps, title case, colon, and a PDF-broken spelling."""
        for heading in ("RELEVANT COURSEWORK", "Relevant Coursework",
                        "Relevant Coursework:", "R elevant Coursework",
                        "COURSEWORK", "Professional Development"):
            text = (f"Dana Reed\n{heading}\nTensorFlow\n")
            row = se.assess_all(sc.from_weights({"tensorflow": 3}), text)[0]
            self.assertEqual(row["tier"], se.BACKGROUND, heading)

    def test_a_document_with_no_headings_still_assesses(self):
        text = "Dana Reed. Built the billing service on Redis and Kafka.\n"
        rows = se.assess_all(sc.from_weights({"redis": 3}), text)
        self.assertTrue(rows[0]["occurrences"])


# --------------------------------------------------------------------------
# Provenance survives into the production record
# --------------------------------------------------------------------------

class Provenance(unittest.TestCase):

    RESUME = ("Dana Reed\nProfessional Experience\nAcme Corp\n"
              "Software Engineer\n"
              "- Built the session cache on Redis.\n"
              "- Kubernetes is planned for next quarter.\n")

    def importance(self):
        data = {"skill_weights": [{"term": "redis", "weight": 3},
                                  {"term": "kubernetes", "weight": 3}]}
        out = make_profile.reweight_from_evidence(
            data, self.RESUME, log=lambda *a, **k: None)
        return {r["id"]: r for r in out["skill_importance"]}

    def test_occurrence_status_reaches_the_importance_record(self):
        rows = self.importance()
        self.assertEqual([o["status"] for o in rows["redis"]["occurrences"]],
                         [se.USED])
        self.assertEqual(
            [o["status"] for o in rows["kubernetes"]["occurrences"]],
            [se.PLANNED])

    def test_provenance_is_offsets_and_hashes_not_text(self):
        rows = self.importance()
        blob = repr(rows)
        self.assertNotIn("session cache", blob)
        self.assertNotIn("next quarter", blob)
        for row in rows.values():
            for occurrence in row["occurrences"]:
                start, end = occurrence["span"]
                self.assertLess(start, end)
                self.assertRegex(occurrence["sha"], r"^[0-9a-f]{12}$")

    def test_status_counts_are_recorded(self):
        rows = self.importance()
        self.assertEqual(rows["kubernetes"]["status_counts"], {se.PLANNED: 1})
        self.assertEqual(rows["redis"]["status_counts"], {se.USED: 1})

    def test_the_market_axis_is_untouched(self):
        """R3's record must survive R4a unchanged."""
        rows = self.importance()
        for row in rows.values():
            self.assertIn("market", row)
            self.assertIn("considered", row["market"])


class Invariance(unittest.TestCase):
    """What R4a must NOT have moved."""

    TEXT = ("Dana Reed\nProfessional Experience\nAcme Corp\n"
            "- Built the session cache on Redis.\n"
            "- Kubernetes is planned for next quarter.\n")

    def test_status_ignores_which_extractor_named_the_skill(self):
        """Audit defect C1, now for statuses. Origin is not evidence."""
        for weight in (1, 3, 5):
            rows = se.assess_all(
                sc.from_weights({"redis": weight, "kubernetes": weight}),
                self.TEXT)
            got = {r["id"]: (r["tier"], [o["status"] for o in r["occurrences"]])
                   for r in rows}
            self.assertEqual(got["redis"], (se.CORE, [se.USED]), weight)
            self.assertEqual(got["kubernetes"],
                             (se.BACKGROUND, [se.PLANNED]), weight)

    def test_r3_the_market_key_still_does_not_depend_on_spelling(self):
        """Canonical hardening's invariant, re-checked after R4a."""
        seen = set()
        for spellings in (["javascript"], ["javascript", "js"],
                          ["javascript", "js", "es6"]):
            data = {"skill_weights": [{"term": t, "weight": 3}
                                      for t in spellings]}
            out = make_profile.reweight_from_evidence(
                data, "Dana Reed\nTechnical Skills\nJavaScript, ES6, JS\n",
                log=lambda *a, **k: None)
            row, = [r for r in out["skill_importance"]
                    if r["id"] == "javascript"]
            seen.add((row["weight"], row["market"]["key"]))
        self.assertEqual(len(seen), 1, seen)

    def test_row_order_does_not_change_any_status(self):
        pair = {"redis": 3, "kubernetes": 3}
        forward = se.assess_all(sc.from_weights(pair), self.TEXT)
        backward = se.assess_all(
            list(reversed(sc.from_weights(pair))), self.TEXT)
        self.assertEqual(sorted(map(repr, forward)),
                         sorted(map(repr, backward)))

    def test_classify_is_pure(self):
        """Same inputs, same answer — no state, no clock, no model."""
        line = "- Evaluated Kubernetes but did not adopt it."
        at = line.lower().index("kubernetes")
        answers = {se.classify(line.lower(), at, at + 10) for _ in range(20)}
        self.assertEqual(answers, {se.NEGATED})


# --------------------------------------------------------------------------
# "Machine Learning" is a field, not a confession
# --------------------------------------------------------------------------

def statuses_of(text, terms):
    """{term: [status, ...]} for several concepts, assessed together."""
    rows = se.assess_all(sc.from_weights({t: 3 for t in terms}), text)
    return {r["id"]: [o["status"] for o in r["occurrences"]] for r in rows}


def tiers_of(text, terms):
    rows = se.assess_all(sc.from_weights({t: 3 for t in terms}), text)
    return {r["id"]: r["tier"] for r in rows}


FIELDS = ("Machine Learning", "Deep Learning", "Reinforcement Learning")
# Open-ended on purpose: the rule is about grammar, so any modifier works.
MORE_FIELDS = ("Transfer Learning", "Federated Learning", "Representation Learning",
               "Statistical Learning", "Active Learning", "Self-Supervised Learning",
               "Online Learning", "Contrastive Learning", "Few-Shot Learning",
               "Q-Learning", "Meta-Learning", "Imitation Learning",
               "Continual Learning", "Unsupervised Learning")
HEAD = "Dana Reed\ndana@example.com\n"
WORK = HEAD + "Professional Experience\nAcme Corp\nML Engineer  Jan 2023 - Present\n"


class LearningAsAFieldIsNotLearning(unittest.TestCase):
    """P0. `_LEARNING` held a bare "learning", and a skills section has no
    sentence end, so one "Machine Learning" in it made EVERY skill there
    LEARNING_OR_COURSEWORK: unclaimed, and down to BACKGROUND. So did the
    work bullet "Built machine learning models using Python". "Learning" is
    pedagogy only as a verb — never as the head of a compound noun."""

    def assert_claimed(self, text, terms, status=se.MENTIONED):
        got = statuses_of(text, terms)
        for term in terms:
            self.assertTrue(got[term], (term, text))
            self.assertEqual(set(got[term]), {status}, (term, text, got))

    def test_each_field_in_a_comma_skills_list_is_a_claim(self):
        for field in FIELDS:
            text = HEAD + f"Technical Skills\nPython, {field}, FastAPI\n"
            self.assert_claimed(text, ["python", field.lower(), "fastapi"])

    def test_one_field_does_not_unclaim_its_neighbours_on_separate_lines(self):
        for field in FIELDS:
            text = (HEAD + f"Skills\nPython\n{field}\nFastAPI\nPostgreSQL\n"
                    "Docker\n")
            terms = ["python", "fastapi", "postgresql", "docker"]
            self.assert_claimed(text, terms + [field.lower()])
            self.assertEqual(set(tiers_of(text, terms).values()),
                             {se.SUPPORTING}, field)

    def test_one_field_does_not_unclaim_its_neighbours_in_a_comma_list(self):
        text = HEAD + "Skills\nPython, Machine Learning, FastAPI, PostgreSQL, Docker\n"
        terms = ["python", "fastapi", "postgresql", "docker"]
        self.assert_claimed(text, terms)
        self.assertEqual(set(tiers_of(text, terms).values()), {se.SUPPORTING})

    def test_a_labelled_skills_group(self):
        terms = ["pytorch", "scikit-learn", "mlflow", "python", "sql"]
        for group in ("Machine Learning: PyTorch, scikit-learn, MLflow\n",
                      "Machine Learning:\nPyTorch, scikit-learn, MLflow\n",
                      "Deep Learning / Reinforcement Learning: PyTorch, "
                      "scikit-learn, MLflow\n"):
            text = HEAD + "Skills\n" + group + "Languages: Python, SQL\n"
            self.assert_claimed(text, terms)

    def test_building_machine_learning_models_is_work(self):
        text = (HEAD + "Professional Experience\nAcme Corp\n"
                "ML Engineer  Jan 2023 - Present\n"
                "- Built machine learning models using Python.\n")
        self.assert_claimed(text, ["machine learning", "python"], se.USED)
        self.assertEqual(tiers_of(text, ["python"])["python"], se.CORE)

    def test_a_field_hyphenated_or_double_spaced(self):
        for field in ("Machine  Learning", "Machine\tLearning",
                      "machine-learning", "e-learning"):
            text = HEAD + f"Skills\nPython, {field}, FastAPI\n"
            self.assert_claimed(text, ["python", "fastapi"])

    def test_known_limit_a_field_split_by_a_line_wrap(self):
        """KNOWN LIMITATION, pinned, not intended — unchanged by this fix.

        A PDF that wraps "Machine\nLearning" leaves a line that STARTS with
        "Learning", and `_heading` reads any such line as an EDUCATION
        heading: the "(currently )?(learning|studying)|in progress|..." row
        has an ungrouped alternation, so its first branch is anchored at the
        start only. What follows the wrap becomes coursework. A separate,
        pre-existing heading defect (P0_MACHINE_LEARNING_EVIDENCE_FIX_HANDOFF
        §O); fixing it would flip this test."""
        text = HEAD + "Skills\nPython, SQL, Machine\nLearning, FastAPI\n"
        self.assertEqual(se._heading("Learning, FastAPI"), se.EDUCATION)
        got = statuses_of(text, ["python", "fastapi"])
        self.assertEqual(got, {"python": [se.MENTIONED], "fastapi": [se.LEARNING]})

    def test_any_modified_learning_is_a_field(self):
        """Not a three-item exception list: every "<modifier> Learning" is a
        claim, and so are its neighbours."""
        for field in MORE_FIELDS:
            text = HEAD + f"Skills\nPython, {field}, Docker\n"
            self.assert_claimed(text, ["python", field.lower(), "docker"])
            self.assertIsNone(se._LEARNING.search(field), field)

    def test_substantive_use_of_a_field(self):
        for body, terms in (
                ("- Built a federated learning system in Python.",
                 ["federated learning", "python"]),
                ("- Implemented federated learning with PyTorch.",
                 ["federated learning", "pytorch"]),
                ("- Applied transfer learning with PyTorch.", ["transfer learning", "pytorch"]),
                ("- Trained reinforcement learning agents with Ray.",
                 ["reinforcement learning", "ray"])):
            self.assert_claimed(WORK + body + "\n", terms, se.USED)

    def test_the_pattern_itself(self):
        for field in ("Machine Learning", "deep learning", "Reinforcement Learning",
                      "machine-learning", "Transfer Learning"):
            self.assertIsNone(se._LEARNING.search(field), field)
        for phrase in ("Learning React", "currently learning Rust",
                       "I am learning Go", "I'm learning Go", "was learning React",
                       "self-learning Rust", "interested in learning Rust"):
            self.assertIsNotNone(se._LEARNING.search(phrase), phrase)

    def test_semantic_scope_agrees(self):
        """The same fix reaches V3 Step 8's scoped path (default off)."""
        from unittest import mock
        with mock.patch.dict(os.environ, {"SWEEP_SEMANTIC_SCOPE": "1"}):
            self.test_one_field_does_not_unclaim_its_neighbours_on_separate_lines()
            self.test_building_machine_learning_models_is_work()
            got = statuses_of(HEAD + "Summary\nLearning React, TypeScript and Next.js\n",
                              ["react", "typescript", "next.js"])
            self.assertEqual({s for v in got.values() for s in v}, {se.LEARNING})


class LearningAsPedagogyIsKept(unittest.TestCase):
    """Control. The genuine learning and coursework cues keep working —
    including when the thing being learned is itself "Machine Learning"."""

    def assert_learning(self, text, terms):
        got = statuses_of(text, terms)
        for term in terms:
            self.assertEqual(set(got[term]), {se.LEARNING}, (term, text, got))

    def test_verb_forms_of_learning(self):
        work = HEAD + "Professional Experience\nAcme Corp\nEngineer  Jan 2023 - Present\n"
        for body, terms in (
                ("- Currently learning Rust in my own time.", ["rust"]),
                ("- Learning Kubernetes", ["kubernetes"]),
                ("- I am learning Go on weekends.", ["go"]),
                ("- Self-learning Rust.", ["rust"]),
                ("- Built Python services. Learning Rust in my own time.", ["rust"]),
                ("- Currently learning Machine Learning with PyTorch.",
                 ["machine learning", "pytorch"])):
            self.assert_learning(work + body + "\n", terms)

    def test_summary_forms(self):
        for body, terms in (
                ("In 2024 I was learning React, TypeScript and Next.js",
                 ["react", "typescript", "next.js"]),
                ("Studying AWS for the associate exam.", ["aws"]),
                ("Coursework in Machine Learning and Python.",
                 ["machine learning", "python"]),
                ("Interested in learning Rust and Go.", ["rust", "go"])):
            self.assert_learning(HEAD + "Summary\n" + body + "\n", terms)

    def test_a_section_that_opens_with_the_verb(self):
        """The line break after a heading is not a modifier."""
        for heading in ("Summary", "SUMMARY", "Profile"):
            self.assert_learning(
                HEAD + f"{heading}\nLearning React, TypeScript and Next.js\n",
                ["react", "typescript", "next.js"])
        self.assert_learning(HEAD + "Summary\nBuilt services in Go.\n"
                             "Learning Rust in my own time.\n", ["rust"])

    def test_coursework_and_currently_learning_sections(self):
        for heading in ("Relevant Coursework", "Currently Learning"):
            self.assert_learning(HEAD + f"{heading}\nMachine Learning, Python\n",
                                 ["machine learning", "python"])

    def test_pedagogical_context_wins_for_any_field(self):
        """The field name never decides: the context around it does."""
        for field in ("Transfer Learning", "Federated Learning", "Reinforcement Learning",
                      "Machine Learning"):
            for body in (f"Currently learning {field}.",
                         f"Completed a course in {field}.",
                         f"Studying {field.lower()}."):
                self.assert_learning(HEAD + "Summary\n" + body + "\n", [field.lower()])

    def test_a_completed_course_is_learning(self):
        """Regression: "Completed a machine learning course." was LEARNING on
        d1c44bc only through the word inside the field name. The explicit
        course language is the cue now."""
        for body in ("Completed a machine learning course.",
                     "Took a machine learning course.",
                     "Completed a course in machine learning.",
                     "Completed an online machine learning course on Coursera.",
                     "Finished a deep learning course in 2024.",
                     "Machine learning course, 2025.",
                     "Machine learning course (2025)."):
            field = "deep learning" if "deep" in body else "machine learning"
            self.assert_learning(HEAD + "Summary\n" + body + "\n", [field])
        self.assert_learning(WORK + "- Completed a machine learning course.\n",
                             ["machine learning"])
        self.assert_learning(HEAD + "Summary\nCompleted a React course.\n", ["react"])

    def test_course_as_a_product_is_not_learning(self):
        """Control. An e-learning engineer builds courses; "course" followed by
        another noun, or not taken, is the product, not the pedagogy."""
        for body, term in (("- Built the course catalogue service in Go.", "go"),
                           ("- Completed the course-catalog migration to Kubernetes.",
                            "kubernetes"),
                           ("- Completed the course migration to PostgreSQL.",
                            "postgresql"),
                           ("- Designed course recommendations with Python.", "python")):
            got = statuses_of(WORK + body + "\n", [term])
            self.assertNotIn(se.LEARNING, got[term], (body, got))

    def test_a_specialization_is_learning_only_under_a_certifications_heading(self):
        """"Specialization" is no cue anywhere (it also means a focus area).
        The bench's own "Deep Learning Specialisation" sits under
        Certifications, which is structural and still LEARNING."""
        self.assert_learning(HEAD + "Certifications\nDeep Learning Specialization, "
                             "Coursera, 2024\n", ["deep learning"])
        got = statuses_of(HEAD + "Summary\nDeep Learning Specialization, 2024.\n",
                          ["deep learning"])
        self.assertEqual(got, {"deep learning": [se.MENTIONED]})

    def test_known_ambiguity_a_clause_initial_learning_noun(self):
        """KNOWN AMBIGUITY, pinned, unchanged from d1c44bc: a noun phrase that
        STARTS with "Learning" ("Learning Management Systems") has the shape of
        the verb ("Learning React"), so it still fires and still takes its
        sentence with it. Telling them apart needs the concept spans the
        document names (§O of the handoff), not a longer regex."""
        text = HEAD + "Skills\nLearning Management Systems, Moodle, SCORM\n"
        self.assertEqual(statuses_of(text, ["moodle"]),
                         {"moodle": [se.LEARNING]})

    def test_a_learning_cue_still_governs_its_own_sentence_only(self):
        text = (HEAD + "Professional Experience\nAcme Corp\n"
                "Engineer  Jan 2023 - Present\n"
                "- Built machine learning models using Python.\n"
                "- Currently learning Rust in my own time.\n")
        got = statuses_of(text, ["python", "rust"])
        self.assertEqual(got, {"python": [se.USED], "rust": [se.LEARNING]})


if __name__ == "__main__":
    unittest.main()
