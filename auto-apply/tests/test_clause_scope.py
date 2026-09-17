"""A verb belongs to its own clause.

R4c. `shape()` read the whole line, so one action verb anywhere on a
multi-clause bullet made every technology named on that line substantive:

    "Implemented Redis caching, documented Kafka migration options."

Redis was implemented. Kafka was documented. Before this batch both came
out SUBSTANTIVE_USE, because "Implemented" and "Kafka" shared a line.

The opposite error is just as wrong. A coordinated list is ONE predicate:

    "Built APIs with Node.js, Express and PostgreSQL."

all three are built with, and splitting at every comma would lose two of
them. So the rule is grammatical rather than mechanical: a clause break
ends the governing verb's reach only when what follows it STARTS A NEW
PREDICATE.

This batch changes no strength level, no tier, no status, and no
threshold. It changes which words `shape()` is allowed to look at.
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

PROJECT = "Dana Reed\nProjects\nLedgerly\n"


def read(terms, body, head=PROJECT):
    """{term: (status, shape)} for one bullet, through the real path."""
    rows = {r["id"]: r for r in se.assess_all(
        sc.from_weights({t: 3 for t in terms}), head + body)}
    out = {}
    for term in terms:
        row = rows.get(sc.resolve(term)) or {}
        found = row.get("occurrences") or [{}]
        out[term] = (found[0].get("status"), found[0].get("shape"))
    return out


def bullet(terms, text):
    return read(terms, "- " + text + "\n")


# --------------------------------------------------------------------------
# The 10 predicate cases
# --------------------------------------------------------------------------

class Predicates(unittest.TestCase):

    def test_01_a_second_predicate_does_not_inherit_the_first_verb(self):
        got = bullet(["redis", "kafka"],
                     "Implemented Redis caching, documented Kafka "
                     "migration options.")
        self.assertEqual(got["redis"], (se.USED, se.SUBSTANTIVE_USE))
        self.assertNotEqual(got["kafka"][1], se.SUBSTANTIVE_USE, got)

    def test_02_a_coordinated_list_shares_its_verb(self):
        got = bullet(["node.js", "express", "postgresql"],
                     "Built APIs with Node.js, Express and PostgreSQL.")
        for term in ("node.js", "express", "postgresql"):
            self.assertEqual(got[term][1], se.SUBSTANTIVE_USE, term)

    def test_03_a_semicolon_predicate_keeps_its_own_status(self):
        got = bullet(["node.js", "kafka"],
                     "Built the API with Node.js; evaluated Kafka but did "
                     "not adopt it.")
        self.assertEqual(got["node.js"], (se.USED, se.SUBSTANTIVE_USE))
        self.assertEqual(got["kafka"][0], se.NEGATED)
        self.assertNotEqual(got["kafka"][1], se.SUBSTANTIVE_USE, got)

    def test_04_a_planned_clause_takes_no_depth_from_the_first(self):
        got = bullet(["redis", "kafka"],
                     "Implemented Redis caching and planned a Kafka "
                     "migration.")
        self.assertEqual(got["redis"], (se.USED, se.SUBSTANTIVE_USE))
        self.assertEqual(got["kafka"][0], se.PLANNED)
        self.assertNotEqual(got["kafka"][1], se.SUBSTANTIVE_USE, got)

    def test_05_one_verb_two_purposes(self):
        got = bullet(["postgresql", "redis"],
                     "Used PostgreSQL for persistence and Redis for caching.")
        for term in ("postgresql", "redis"):
            self.assertEqual(got[term][1], se.SUBSTANTIVE_USE, term)

    def test_06_governed_by_using(self):
        got = bullet(["salesforce", "apex", "oauth"],
                     "Integrated Salesforce using Apex and OAuth.")
        for term in ("salesforce", "apex", "oauth"):
            self.assertEqual(got[term][1], se.SUBSTANTIVE_USE, term)

    def test_07_a_later_action_does_not_reach_backwards(self):
        got = bullet(["redis", "kafka"],
                     "Documented Redis architecture, then implemented "
                     "Kafka consumers.")
        self.assertNotEqual(got["redis"][1], se.SUBSTANTIVE_USE, got)
        self.assertEqual(got["kafka"][1], se.SUBSTANTIVE_USE, got)

    def test_08_two_predicates_two_actions(self):
        got = bullet(["redis", "grafana"],
                     "Implemented caching in Redis and monitoring in Grafana.")
        for term in ("redis", "grafana"):
            self.assertEqual(got[term][1], se.SUBSTANTIVE_USE, term)

    def test_09_a_soft_wrap_preserves_shared_governance(self):
        got = read(["node.js", "express", "mongodb"],
                   "- Built services using Node.js,\n  Express and MongoDB.\n")
        for term in ("node.js", "express", "mongodb"):
            self.assertEqual(got[term], (se.USED, se.SUBSTANTIVE_USE), term)

    def test_10_a_coordinated_list_after_one_verb(self):
        """Policy: no new predicate follows either comma, so `Implemented`
        governs the whole list."""
        got = bullet(["redis", "kafka", "postgresql"],
                     "Implemented Redis caching, Kafka, and PostgreSQL "
                     "replication.")
        for term in ("redis", "kafka", "postgresql"):
            self.assertEqual(got[term][1], se.SUBSTANTIVE_USE, term)


# --------------------------------------------------------------------------
# What must not move
# --------------------------------------------------------------------------

class Preserved(unittest.TestCase):

    def test_a_stack_line_stays_a_claim(self):
        got = read(["redis", "kafka"],
                   "- Technologies: Redis, MongoDB\n"
                   "- Implemented the Kafka consumers.\n")
        self.assertEqual(got["redis"][1], se.CLAIM_ONLY)
        self.assertEqual(got["kafka"][1], se.SUBSTANTIVE_USE)

    def test_an_action_on_the_next_bullet_does_not_reach(self):
        got = read(["redis"], "Ledgerly | Redis, Node.js\n"
                              "- Implemented the Kafka consumers.\n",
                   head="Dana Reed\nProjects\n")
        self.assertEqual(got["redis"][1], se.CLAIM_ONLY)

    def test_clause_windows_do_not_cross_entries(self):
        text = ("Dana Reed\nProjects\nCachely\n- Implemented the cache.\n"
                "Streamly | Kafka, Node.js\n")
        rows = {r["id"]: r for r in se.assess_all(
            sc.from_weights({"kafka": 3}), text)}
        self.assertEqual(rows["kafka"]["occurrences"][0]["shape"],
                         se.CLAIM_ONLY)

    def test_clause_windows_do_not_cross_employers(self):
        text = ("Dana Reed\nProfessional Experience\n"
                "Northwind\n- Implemented the billing service.\n"
                "Vantage\n- Technologies: Kafka, Redis\n")
        rows = {r["id"]: r for r in se.assess_all(
            sc.from_weights({"kafka": 3}), text)}
        self.assertEqual(rows["kafka"]["occurrences"][0]["shape"],
                         se.CLAIM_ONLY)
        self.assertEqual(rows["kafka"]["tier"], se.SUPPORTING)

    def test_r4b_depth_still_holds(self):
        """One action still beats a stack line naming it twice."""
        spellings = se.assess_all(sc.from_weights({"redis": 3}),
                                  "Dana Reed\nProjects\nCachely | Redis / "
                                  "redis\n")[0]
        action = se.assess_all(sc.from_weights({"redis": 3}),
                               PROJECT + "- Implemented Redis caching.\n")[0]
        self.assertEqual(spellings["evidence_strength"], se.CLAIM_ONLY)
        self.assertEqual(action["evidence_strength"], se.SUBSTANTIVE_USE)
        self.assertGreater(se.rank(action["tier"]), se.rank(spellings["tier"]))

    def test_shape_never_overrides_status(self):
        """R4a decides first. No verb can make a rejected thing used."""
        for text, status in (
                ("- Implemented the cache; rejected Kafka on cost.", se.NEGATED),
                ("- Implemented the cache and planned a Kafka rollout.",
                 se.PLANNED)):
            got = read(["kafka"], text + "\n")
            self.assertEqual(got["kafka"][0], status, text)
            row = se.assess_all(sc.from_weights({"kafka": 3}),
                                PROJECT + text + "\n")[0]
            self.assertEqual(row["evidence_strength"], se.NO_EVIDENCE, text)
            self.assertEqual(row["tier"], se.BACKGROUND, text)


class Invariance(unittest.TestCase):

    SENTENCE = "- Implemented Redis caching, documented %s migration options.\n"

    def test_spelling_does_not_change_clause_scope(self):
        answers = set()
        for spelling in ("Node.js", "NodeJS", "node.js"):
            rows = {r["id"]: r for r in se.assess_all(
                sc.from_weights({"redis": 3, spelling.lower(): 3}),
                PROJECT + self.SENTENCE % spelling)}
            row = rows["node.js"]
            answers.add((row["occurrences"][0]["status"],
                         row["occurrences"][0]["shape"],
                         row["evidence_strength"], row["tier"], row["weight"]))
        self.assertEqual(len(answers), 1, answers)

    def test_alias_count_does_not_change_clause_scope(self):
        answers = set()
        for spellings in (["node.js"], ["node.js", "nodejs"],
                          ["node.js", "nodejs", "node"]):
            text = PROJECT + ("- Built APIs with %s and Express.\n"
                              % " / ".join(spellings))
            rows = [r for r in se.assess_all(
                sc.from_weights({s: 3 for s in spellings}), text)
                if r["id"] == "node.js"]
            answers.add((rows[0]["evidence_strength"], rows[0]["tier"],
                         rows[0]["weight"]))
        self.assertEqual(len(answers), 1, answers)

    def test_origin_does_not_change_clause_scope(self):
        for weight in (1, 3, 5):
            got = read(["redis", "kafka"],
                       "- Implemented Redis caching, documented Kafka "
                       "migration options.\n")
            self.assertEqual(got["redis"][1], se.SUBSTANTIVE_USE, weight)
            self.assertNotEqual(got["kafka"][1], se.SUBSTANTIVE_USE, weight)

    def test_an_unknown_technology_is_governed_the_same_way(self):
        got = bullet(["foodb", "barqueue"],
                     "Implemented FooDB replication, documented BarQueue "
                     "options.")
        self.assertEqual(got["foodb"][1], se.SUBSTANTIVE_USE, got)
        self.assertNotEqual(got["barqueue"][1], se.SUBSTANTIVE_USE, got)

    def test_the_importance_record_still_carries_everything(self):
        data = {"skill_weights": [{"term": "redis", "weight": 3},
                                  {"term": "kafka", "weight": 3}]}
        out = make_profile.reweight_from_evidence(
            data, PROJECT + "- Implemented Redis caching, documented Kafka "
            "migration options.\n", log=lambda *a, **k: None)
        rows = {r["id"]: r for r in out["skill_importance"]}
        self.assertEqual(rows["redis"]["evidence_strength"], se.SUBSTANTIVE_USE)
        self.assertEqual(rows["kafka"]["evidence_strength"], se.CLAIM_ONLY)
        for row in rows.values():
            self.assertIn("considered", row["market"])
            self.assertIn("status_counts", row)
            self.assertRegex(row["occurrences"][0]["sha"], r"^[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
