"""How strong is the evidence that this was actually used?

R4b. R4a decided what a mention MEANS; this decides how much a USED
mention shows. The engine was using lexical repetition as the proxy, so
two spellings on a stack line outranked one substantive implementation
bullet:

    "Cachely | Redis / redis"            -> STRONG_SECONDARY
    "- Implemented Redis-backed session
       caching with fail-open degradation." -> SUPPORTING

Repetition is not depth. Naming a thing twice says nothing a single
naming did not already say; describing what was built with it does.

This is NOT proficiency. Nothing here claims the candidate is good at
anything — it measures how much the résumé shows, and stops there. A
quantified result ("served 100k users") is evidence that implementation
happened, never evidence of skill level.

No model is asked anything on this path.
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

PROJECTS = "Dana Reed\nProjects\n"
WORK = "Dana Reed\nProfessional Experience\nAcme Corp\nEngineer\n"


def assess(term, text):
    return se.assess_all(sc.from_weights({term: 3}), text)[0]


def strength(term, text):
    return assess(term, text)["evidence_strength"]


def tier(term, text):
    return assess(term, text)["tier"]


# --------------------------------------------------------------------------
# The 15-case depth matrix
# --------------------------------------------------------------------------

class Matrix(unittest.TestCase):

    def check(self, term, text, expect_strength, expect_tier):
        row = assess(term, text)
        self.assertEqual(row["evidence_strength"], expect_strength,
                         f"{text!r} -> {row['evidence_strength']} {row['why']}")
        self.assertEqual(row["tier"], expect_tier,
                         f"{text!r} -> {row['tier']} {row['why']}")
        return row

    def test_01_project_stack_one_name(self):
        self.check("redis", PROJECTS + "Cachely | Redis, Node.js\n",
                   se.CLAIM_ONLY, se.SUPPORTING)

    def test_02_project_stack_two_spellings(self):
        """The defect. Two spellings are one claim."""
        self.check("redis", PROJECTS + "Cachely | Redis / redis, Node.js\n",
                   se.CLAIM_ONLY, se.SUPPORTING)

    def test_03_project_bullet_implemented(self):
        self.check("redis", PROJECTS + "Cachely\n- Implemented Redis caching.\n",
                   se.SUBSTANTIVE_USE, se.STRONG_SECONDARY)

    def test_04_project_bullet_built_using(self):
        self.check("redis",
                   PROJECTS + "Cachely\n- Built session caching using Redis.\n",
                   se.SUBSTANTIVE_USE, se.STRONG_SECONDARY)

    def test_05_repetition_does_not_beat_one_action(self):
        repeated = assess("redis", PROJECTS + "Cachely | Redis, Redis, Redis\n")
        acted = assess("redis", PROJECTS + "Cachely\n- Implemented Redis-backed "
                       "session caching with fail-open degradation.\n")
        self.assertGreater(se.rank(acted["tier"]), se.rank(repeated["tier"]))
        self.assertGreater(acted["weight"], repeated["weight"])

    def test_06_two_independent_entries(self):
        row = self.check("redis",
                         PROJECTS + "Cachely\n- Implemented Redis caching.\n"
                         "Ledgerly\n- Built a queue on Redis.\n",
                         se.INDEPENDENT_USE, se.STRONG_SECONDARY)
        self.assertEqual(row["independent_entries"], 2)

    def test_07_one_entry_named_five_times(self):
        row = self.check("redis",
                         PROJECTS + "Cachely | Redis, Redis, Redis\n"
                         "- Redis notes and Redis diagrams.\n",
                         se.CLAIM_ONLY, se.SUPPORTING)
        self.assertEqual(row["independent_entries"], 0)

    def test_08_skills_list_repeated(self):
        self.check("redis", "Dana Reed\nTechnical Skills\nRedis, redis, Redis\n",
                   se.CLAIM_ONLY, se.SUPPORTING)

    def test_09_work_stack_line(self):
        self.check("redis", WORK + "- Technologies: Redis, MongoDB\n",
                   se.CLAIM_ONLY, se.SUPPORTING)

    def test_10_work_action(self):
        self.check("redis", WORK + "- Implemented Redis-backed caching.\n",
                   se.SUBSTANTIVE_USE, se.CORE)

    def test_11_rejected_keeps_r4a_answer(self):
        self.check("redis",
                   PROJECTS + "Cachely\n- Considered Redis but chose PostgreSQL.\n",
                   se.NO_EVIDENCE, se.BACKGROUND)

    def test_12_planned_gains_no_depth(self):
        self.check("redis", PROJECTS + "Cachely\n- Planned Redis integration.\n",
                   se.NO_EVIDENCE, se.BACKGROUND)

    def test_13_coursework_repeated(self):
        self.check("redis",
                   "Dana Reed\nRelevant Coursework\nRedis, Redis\n- Redis lab.\n",
                   se.NO_EVIDENCE, se.BACKGROUND)

    def test_14_unknown_technology_substantive(self):
        """No ontology entry, and it still gets real evidence."""
        self.check("acmeflow",
                   PROJECTS + "Cachely\n- Implemented AcmeFlow pipelines "
                   "end to end.\n", se.SUBSTANTIVE_USE, se.STRONG_SECONDARY)

    def test_15_one_action_outranks_two_stack_spellings(self):
        """The key regression this batch exists for."""
        spellings = assess("redis", PROJECTS + "Cachely | Redis / redis\n")
        action = assess("redis",
                        PROJECTS + "Ledgerly\n- Implemented Redis caching.\n")
        self.assertEqual(spellings["evidence_strength"], se.CLAIM_ONLY)
        self.assertEqual(action["evidence_strength"], se.SUBSTANTIVE_USE)
        self.assertGreater(se.rank(action["tier"]), se.rank(spellings["tier"]))


# --------------------------------------------------------------------------
# Repetition, aliases and entries
# --------------------------------------------------------------------------

class Repetition(unittest.TestCase):

    def test_alias_count_does_not_change_strength(self):
        """R3's rule, now for depth. One alias, two, six: same answer."""
        sentence = "Cachely\n- Implemented %s caching.\n"
        answers = set()
        for spellings in (["node.js"], ["node.js", "nodejs"],
                          ["node.js", "nodejs", "node", "node js",
                           "nodejs runtime", "node.js (server)"]):
            text = PROJECTS + sentence % " / ".join(spellings)
            row = se.assess_all(sc.from_weights({s: 3 for s in spellings}),
                                text)
            row = [r for r in row if r["id"] == "node.js"][0]
            answers.add((row["evidence_strength"], row["tier"], row["weight"]))
        self.assertEqual(len(answers), 1, answers)

    def test_mention_count_alone_does_not_change_strength(self):
        one = strength("redis", PROJECTS + "Cachely | Redis\n")
        five = strength("redis", PROJECTS + "Cachely | Redis, Redis, Redis\n"
                        "- Redis and Redis.\n")
        self.assertEqual(one, five)

    def test_independent_entries_beat_repetition_inside_one(self):
        inside = assess("redis", PROJECTS + "Cachely\n"
                        "- Implemented Redis caching.\n"
                        "- Tuned the Redis eviction policy.\n")
        across = assess("redis", PROJECTS + "Cachely\n"
                        "- Implemented Redis caching.\n"
                        "Ledgerly\n- Built a queue on Redis.\n")
        self.assertEqual(inside["evidence_strength"], se.SUBSTANTIVE_USE)
        self.assertEqual(across["evidence_strength"], se.INDEPENDENT_USE)
        self.assertEqual(inside["independent_entries"], 1)
        self.assertEqual(across["independent_entries"], 2)

    def test_an_unnamed_entry_is_not_independent_evidence(self):
        """If the parser cannot name the entries, do not fake breadth."""
        text = PROJECTS + "- Implemented Redis caching.\n- Built a queue on Redis.\n"
        row = assess("redis", text)
        self.assertLess(se.STRENGTHS.index(row["evidence_strength"]),
                        se.STRENGTHS.index(se.INDEPENDENT_USE))


class Attribution(unittest.TestCase):

    def test_evidence_does_not_cross_project_entries(self):
        text = (PROJECTS + "Cachely\n- Implemented Redis caching.\n"
                "Streamly\n- Built the pipeline on Kafka.\n")
        rows = {r["id"]: r for r in se.assess_all(
            sc.from_weights({"redis": 3, "kafka": 3}), text)}
        self.assertEqual(rows["redis"]["why"], "built with in Cachely")
        self.assertEqual(rows["kafka"]["why"], "built with in Streamly")
        for row in rows.values():
            self.assertEqual(row["independent_entries"], 1)

    def test_evidence_does_not_cross_employers(self):
        text = ("Dana Reed\nProfessional Experience\n"
                "Northwind Robotics\nBackend Engineer\n"
                "- Implemented the cache on Redis.\n"
                "Vantage Systems\nPlatform Engineer\n"
                "- Built event streaming with Kafka.\n")
        rows = {r["id"]: r for r in se.assess_all(
            sc.from_weights({"redis": 3, "kafka": 3}), text)}
        self.assertEqual(rows["redis"]["why"], "used at Northwind Robotics")
        self.assertEqual(rows["kafka"]["why"], "used at Vantage Systems")

    def test_a_bulleted_list_line_is_not_a_heading(self):
        """`- Technologies: Redis, MongoDB` used to open a SKILLS section
        and orphan its own content — the folded heading matcher let
        `technolog\\w*` swallow the whole line."""
        spans = se.sections(WORK + "- Technologies: Redis, MongoDB\n")
        self.assertEqual({kind for kind, _e, _s, _x in spans}, {"other", "work"})


class SourceInvariance(unittest.TestCase):

    TEXT = PROJECTS + "Cachely\n- Implemented Redis caching.\n"

    def test_depth_ignores_the_weight_the_concept_arrived_with(self):
        answers = {(lambda r: (r["evidence_strength"], r["tier"],
                               r["weight"]))(assess("redis", self.TEXT))
                   for _ in range(3)}
        self.assertEqual(len(answers), 1)
        for weight in (1, 2, 3, 4, 5):
            row = se.assess_all(sc.from_weights({"redis": weight}),
                                self.TEXT)[0]
            self.assertEqual((row["evidence_strength"], row["tier"]),
                             (se.SUBSTANTIVE_USE, se.STRONG_SECONDARY))

    def test_spelling_does_not_change_depth(self):
        for spelling in ("node.js", "nodejs", "node"):
            text = PROJECTS + f"Cachely\n- Implemented the API in {spelling}.\n"
            row = se.assess_all(sc.from_weights({spelling: 3}), text)[0]
            self.assertEqual(row["id"], "node.js")
            self.assertEqual(row["evidence_strength"], se.SUBSTANTIVE_USE,
                             spelling)


# --------------------------------------------------------------------------
# What the action lexicon must not mistake for an action
# --------------------------------------------------------------------------

class NotAnAction(unittest.TestCase):
    """A project called Cachely is not evidence of caching work."""

    def test_a_project_name_is_not_a_verb(self):
        for name in ("Cachely", "Scala Ventures", "Fixture Labs",
                     "Ownership Group", "Designly"):
            text = PROJECTS + f"{name} | Redis, Node.js\n"
            self.assertEqual(strength("redis", text), se.CLAIM_ONLY, name)

    def test_a_job_title_is_not_a_verb(self):
        text = ("Dana Reed\nProfessional Experience\nNorthwind\n"
                "Lead Software Developer\n- Technologies: Redis, Kafka\n")
        self.assertEqual(strength("redis", text), se.CLAIM_ONLY)

    def test_a_labelled_stack_line_is_a_claim(self):
        for label in ("Tech", "Stack", "Technologies", "Tools", "Built with",
                      "Testing"):
            text = PROJECTS + f"Cachely\n- {label}: Redis, Node.js\n"
            self.assertEqual(strength("redis", text), se.CLAIM_ONLY, label)


# --------------------------------------------------------------------------
# Provenance, and what must not have moved
# --------------------------------------------------------------------------

class Record(unittest.TestCase):

    RESUME = (PROJECTS + "Cachely\n- Implemented Redis caching.\n"
              "Ledgerly | Kafka, Node.js\n")

    def importance(self):
        data = {"skill_weights": [{"term": "redis", "weight": 3},
                                  {"term": "kafka", "weight": 3}]}
        out = make_profile.reweight_from_evidence(
            data, self.RESUME, log=lambda *a, **k: None)
        return {r["id"]: r for r in out["skill_importance"]}

    def test_strength_reaches_the_importance_record(self):
        rows = self.importance()
        self.assertEqual(rows["redis"]["evidence_strength"], se.SUBSTANTIVE_USE)
        self.assertEqual(rows["kafka"]["evidence_strength"], se.CLAIM_ONLY)
        self.assertEqual(rows["redis"]["independent_entries"], 1)

    def test_the_record_still_carries_no_resume_text(self):
        """`why` names the entries, as it always has. Nothing else does,
        and no line of the document appears anywhere."""
        rows = self.importance()
        blob = repr({k: {j: v for j, v in r.items() if j != "why"}
                     for k, r in rows.items()})
        for line in (l.strip() for l in self.RESUME.splitlines()):
            if len(line) > 12:
                self.assertNotIn(line, repr(rows), line)
        self.assertNotIn("Cachely", blob)

    def test_r4a_and_r3_records_survive(self):
        rows = self.importance()
        for row in rows.values():
            self.assertIn("status_counts", row)
            self.assertIn("occurrences", row)
            self.assertIn("considered", row["market"])


if __name__ == "__main__":
    unittest.main()
