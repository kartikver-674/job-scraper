"""Which careers get searched for, and who decides.

Search fields were built out of market correlations, and the market is a
record of previous searches. Three of the eight paid queries derived for
the audited résumé described someone else:

    software engineer -python developer   anchored on GIT
    applied ai backend engineer           the fragment was "i backend"
    sde ii, amazon now                    five rows, one company

These pin the order that replaces it — the résumé proposes a direction,
the market offers concrete titles for it, and the résumé then checks the
title it got back — and the guards at each step.

The market fixture is synthetic and deliberately contains every trap:
a Python cluster whose listings name Git, a one-company internal title, an
AI backend market with real overlap, and a sales market that genuinely
wants the CRM. A rule that passes here is a rule, not a fit to one résumé.
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

import local_search  # noqa: E402
import role_families as rf  # noqa: E402
import skill_concepts  # noqa: E402

SENIORITY = ("senior", "staff", "principal", "lead")


def cluster(rows, title, n, skills, companies, score=28):
    for i in range(n):
        rows.append((title, score, frozenset(skills), f"{title[:5]}{i % companies}"))


def corpus():
    rows = []
    cluster(rows, "react native developer", 200,
            {"react native", "typescript", "redux"}, 20)
    cluster(rows, "react native engineer", 60,
            {"react native", "typescript"}, 12)
    cluster(rows, "mobile developer", 120, {"react native", "typescript"}, 14)
    cluster(rows, "salesforce developer", 160,
            {"salesforce", "apex", "soql"}, 18)
    cluster(rows, "full stack developer", 160,
            {"node.js", "react", "mongodb"}, 16)
    cluster(rows, "python developer", 140, {"python", "django", "git"}, 15)
    cluster(rows, "sde ii, acme now", 40, {"java", "node.js", "react"}, 1)
    cluster(rows, "applied ai backend engineer", 140,
            {"python", "tensorflow", "node.js", "react"}, 16)
    # The shared-platform trap, shaped the way the real corpus is. A sales
    # listing asks for the CRM and then for SALES skills; only a minority
    # name anything else a software engineer has. Measured on the audited
    # corpus: 27% of "business development representative" listings want
    # two or more of this candidate's concepts, against 78% for
    # "salesforce developer" and 93% for "react native developer".
    for i in range(140):
        skills = {"crm", "cold calling", "lead generation", "outreach"}
        if i % 4 == 0:                    # the quarter that also says it
            skills.add("salesforce")
        rows.append(("business development representative", 24,
                     frozenset(skills), f"bdr{i % 15}"))
    cluster(rows, "senior full stack developer", 120,
            {"node.js", "react", "mongodb"}, 14)
    for i in range(900):
        rows.append((f"software engineer {i % 11}", 5,
                     frozenset({"java", "sql", "agile"}), f"big{i % 30}"))
    return rows


ROWS = corpus()
MARKET = local_search.Market(rows=ROWS, seniority=SENIORITY)

RESUME = ("Kartik Verma\nFull-Stack Software Engineer\n"
          "Summary\nReact Native and Node.js apps, plus Salesforce work.\n")

IMPORTANCE = [
    {"id": "react native", "display": "React Native", "tier": "CORE"},
    {"id": "salesforce", "display": "Salesforce", "tier": "CORE"},
    {"id": "apex", "display": "Apex", "tier": "CORE"},
    {"id": "node.js", "display": "Node.js", "tier": "STRONG_SECONDARY"},
    {"id": "mongodb", "display": "MongoDB", "tier": "STRONG_SECONDARY"},
    {"id": "react", "display": "React", "tier": "STRONG_SECONDARY"},
    {"id": "typescript", "display": "TypeScript", "tier": "CORE"},
    {"id": "git", "display": "Git", "tier": "CORE"},
    {"id": "ci/cd", "display": "CI/CD", "tier": "CORE"},
    {"id": "agile", "display": "Agile", "tier": "CORE"},
    {"id": "crm", "display": "CRM", "tier": "CORE"},
]

OWN = {row["id"] for row in IMPORTANCE}


def build(**kwargs):
    kwargs.setdefault("held_titles", ["software engineer"])
    kwargs.setdefault("importance", IMPORTANCE)
    kwargs.setdefault("market", MARKET)
    kwargs.setdefault("resume_text", RESUME)
    return rf.build(**kwargs)


class TestGenericToolsCannotCreateACareer(unittest.TestCase):
    """Requirement 1. A version control system is not a profession."""

    def test_git_cannot_anchor_even_at_core(self):
        self.assertFalse(rf.can_anchor("git", "CORE"))

    def test_ci_cd_cannot_anchor_even_at_core(self):
        self.assertFalse(rf.can_anchor("ci/cd", "CORE"))

    def test_agile_and_jira_cannot_anchor(self):
        for tool in ("agile", "scrum", "jira", "postman", "github"):
            self.assertFalse(rf.can_anchor(tool, "CORE"), tool)

    def test_git_produces_no_family_at_all(self):
        keys = {f["key"] for f in build()["families"]}
        self.assertNotIn("skill:git", keys)
        self.assertNotIn("skill:ci/cd", keys)
        self.assertNotIn("skill:agile", keys)

    def test_git_cannot_reach_the_python_market(self):
        """The audit's headline failure: git appeared in 22 of 27 rows at
        one employer and became a Python career."""
        self.assertNotIn("python developer", build()["role_keywords"])

    def test_a_supporting_skill_cannot_anchor(self):
        self.assertFalse(rf.can_anchor("tailwind css", "SUPPORTING"))
        self.assertFalse(rf.can_anchor("mongoose", "BACKGROUND"))

    def test_a_real_technology_can_anchor(self):
        for skill in ("react native", "node.js", "salesforce", "apex"):
            self.assertTrue(rf.can_anchor(skill, "CORE"), skill)
        self.assertTrue(rf.can_anchor("node.js", "STRONG_SECONDARY"))

    def test_docker_and_kubernetes_are_not_workflow(self):
        """"DevOps Engineer" and "Platform Engineer" are real careers."""
        self.assertTrue(rf.can_anchor("docker", "CORE"))
        self.assertTrue(rf.can_anchor("kubernetes", "CORE"))


class TestEvidenceSupportsRealLanes(unittest.TestCase):
    """Requirement 6: grounded breadth, not exact-title copying."""

    def test_react_native_supports_mobile_variants(self):
        built = build()
        native = next(f for f in built["families"]
                      if f["key"] == "skill:react native")
        self.assertTrue(native["titles"])
        self.assertTrue(
            any("react native" in t or "mobile" in t for t in native["titles"]),
            native["titles"])

    def test_full_stack_projects_support_full_stack_roles(self):
        built = build()
        titles = {t for f in built["families"] for t in f["titles"]}
        self.assertTrue(any("full stack" in t for t in titles), titles)

    def test_adjacent_titles_are_reachable_not_just_the_held_one(self):
        """A mobile candidate should reach titles their résumé never used."""
        got = build()["role_keywords"]
        self.assertTrue([t for t in got if t != "software engineer"], got)

    def test_a_family_with_no_market_is_dropped_not_carried_empty(self):
        built = build()
        for family in built["families"]:
            if family["key"].startswith("skill:"):
                continue
            self.assertTrue(family["titles"] or family["rejected"], family)


class TestSalesforceIsALaneNotAnIdentity(unittest.TestCase):
    """Requirement 2. Evidence proves what someone HAS done; it never
    proves what they want next."""

    def test_the_salesforce_lane_exists(self):
        built = build()
        titles = {t for f in built["families"] for t in f["titles"]}
        self.assertIn("salesforce developer", titles)

    def test_a_lane_not_named_in_the_summary_is_not_primary(self):
        """Apex is professional and real, and the person's own summary
        does not describe them by it."""
        built = build()
        apex = next(f for f in built["families"] if f["key"] == "skill:apex")
        self.assertFalse(apex["primary"])

    def test_something_the_summary_names_is_primary(self):
        built = build()
        native = next(f for f in built["families"]
                      if f["key"] == "skill:react native")
        self.assertTrue(native["primary"])

    def test_primary_families_are_searched_before_lanes(self):
        built = build(cap=4)
        lanes = {f["display"] for f in built["families"] if not f["primary"]}
        first = built["why"][0]
        self.assertNotIn(first["family"], lanes, built["why"])


class TestFinalTitleRevalidation(unittest.TestCase):
    """Requirement 4. The guards run on the string that will be SEARCHED,
    not on the fragment that produced it."""

    def check(self, title, own=None, family=None):
        return rf.revalidate(title, ROWS, own or OWN, RESUME,
                             MARKET.vocab, family, SENIORITY)

    def test_a_one_company_title_is_rejected(self):
        ok, why = self.check("sde ii, acme now")
        self.assertFalse(ok)
        self.assertIn("employer", why)

    def test_canonicalisation_may_not_import_a_new_domain(self):
        """"backend" earned its place; "applied ai backend engineer"
        inherited it. The AI has no candidate evidence."""
        ok, why = self.check("applied ai backend engineer")
        self.assertFalse(ok)
        self.assertIn("ai", why)

    def test_a_domain_the_resume_does_evidence_is_allowed(self):
        resume = RESUME + "\nBuilt an AI pipeline in production.\n"
        ok, _why = rf.revalidate("applied ai backend engineer", ROWS, OWN,
                                 resume, MARKET.vocab, None, SENIORITY)
        self.assertTrue(ok)

    def test_a_shared_tool_does_not_import_a_profession(self):
        """Sales jobs genuinely want the CRM. They want nothing else this
        candidate has, and they are not engineering jobs."""
        family = rf.Family("skill:crm", "CRM", "crm", "CORE", "core skill",
                           True)
        ok, why = self.check("business development representative",
                             family=family)
        self.assertFalse(ok)
        self.assertIn("two or more", why)

    def test_the_same_platform_s_engineering_market_passes(self):
        family = rf.Family("skill:apex", "Apex", "apex", "CORE", "core skill",
                           False)
        ok, why = self.check("salesforce developer", family=family)
        self.assertTrue(ok, why)

    def test_a_wildcard_is_rejected(self):
        ok, why = self.check("software engineer")
        self.assertFalse(ok)
        self.assertIn("wildcard", why)

    def test_a_seniority_title_is_rejected(self):
        """from_resume strips seniority for the same reason: the string
        finds only the senior rows, and the reachable ones are the others."""
        ok, why = self.check("senior full stack developer")
        self.assertFalse(ok)
        self.assertIn("seniority", why)

    def test_a_thin_title_is_rejected(self):
        ok, why = self.check("react native architect")
        self.assertFalse(ok)
        self.assertIn("listings", why)

    def test_a_title_must_still_want_the_anchor_that_proposed_it(self):
        family = rf.Family("skill:react native", "React Native",
                           "react native", "CORE", "core skill", True)
        ok, why = self.check("salesforce developer", family=family)
        self.assertFalse(ok)
        self.assertIn("React Native", why)

    def test_a_good_title_passes_with_its_evidence_stated(self):
        ok, why = self.check("react native developer")
        self.assertTrue(ok, why)
        self.assertIn("listings", why)

    def test_role_words_are_never_asked_to_be_proved(self):
        self.assertEqual(
            rf.unsupported_domains("senior software engineer ii", OWN,
                                   RESUME, MARKET.vocab), [])

    def test_a_domain_word_in_the_market_vocabulary_is_caught(self):
        self.assertIn("python",
                      rf.unsupported_domains("python developer", OWN, RESUME,
                                             MARKET.vocab))


class TestTheFinalQueries(unittest.TestCase):
    """What actually gets bought."""

    def test_none_of_the_audit_failures_survive(self):
        got = build()["role_keywords"]
        for bad in ("python developer", "sde ii, acme now",
                    "applied ai backend engineer",
                    "business development representative",
                    "senior full stack developer", "software engineer"):
            self.assertNotIn(bad, got)

    def test_every_query_carries_its_reason(self):
        built = build()
        self.assertEqual({w["query"] for w in built["why"]},
                         set(built["role_keywords"]))
        for entry in built["why"]:
            self.assertTrue(entry["family"] and entry["source"], entry)

    def test_a_query_is_attributed_to_the_family_that_owns_it(self):
        """First-come gave "react native developer" to whichever family was
        assessed first. The query was fine and the reason was nonsense."""
        built = build()
        for entry in built["why"]:
            if "react native" in entry["query"]:
                self.assertEqual(entry["family"], "React Native")

    def test_the_cap_is_respected(self):
        for cap in (1, 3, 5, 8):
            self.assertLessEqual(len(build(cap=cap)["role_keywords"]), cap)

    def test_queries_are_unique_in_shape(self):
        got = build()["role_keywords"]
        shapes = [rf._shape(t) for t in got]
        self.assertEqual(len(shapes), len(set(shapes)), got)

    def test_a_narrow_budget_still_reaches_several_directions(self):
        """Round-robin, not family-by-family: four queries should be four
        directions rather than two directions twice."""
        built = build(cap=4)
        self.assertGreaterEqual(
            len({w["family"] for w in built["why"]}), 3, built["why"])


class TestUserIntentIsAuthoritative(unittest.TestCase):
    """Requirement 2's last line, and the brief's last test."""

    def test_an_explicit_target_role_is_searched(self):
        built = build(preferred=["full stack developer"])
        self.assertIn("full stack developer", built["role_keywords"])

    def test_an_explicit_target_is_primary(self):
        built = build(preferred=["full stack developer"])
        entry = next(w for w in built["why"]
                     if w["query"] == "full stack developer")
        self.assertTrue(entry["primary"])
        self.assertIn("asked for", entry["source"])

    def test_a_preference_expands_rather_than_replaces(self):
        """Asking for one thing must not delete the rest of the search."""
        plain = build()["role_keywords"]
        asked = build(preferred=["full stack developer"])["role_keywords"]
        self.assertGreaterEqual(len(asked), min(len(plain), 4))
        self.assertTrue(set(asked) & set(plain), (asked, plain))

    def test_a_preference_with_no_market_does_not_crash(self):
        built = build(preferred=["underwater basket weaver"])
        self.assertTrue(built["role_keywords"])


class TestStableUnderRowOrder(unittest.TestCase):
    """Requirement 5, carried forward from step 1. Live and frozen corpora
    hold the same rows in different orders."""

    def market(self, rows):
        return local_search.Market(rows=rows, seniority=SENIORITY)

    def test_shuffling_the_corpus_changes_nothing(self):
        import random
        first = build()["role_keywords"]
        self.assertTrue(first)
        rng = random.Random(11)
        for _ in range(6):
            shuffled = list(ROWS)
            rng.shuffle(shuffled)
            self.assertEqual(
                build(market=self.market(shuffled))["role_keywords"], first)

    def test_reversing_the_corpus_changes_nothing(self):
        self.assertEqual(
            build(market=self.market(list(reversed(ROWS))))["role_keywords"],
            build()["role_keywords"])

    def test_the_families_are_stable_too(self):
        import random
        shuffled = list(ROWS)
        random.Random(5).shuffle(shuffled)
        before = [(f["key"], f["titles"]) for f in build()["families"]]
        after = [(f["key"], f["titles"])
                 for f in build(market=self.market(shuffled))["families"]]
        self.assertEqual(after, before)


class TestThinResumes(unittest.TestCase):
    """The case the frozen corpus exists for: a graduate whose every role
    is an internship, so from_resume gives nothing."""

    def test_an_internship_only_graduate_still_gets_queries(self):
        importance = [
            {"id": "react", "display": "React", "tier": "STRONG_SECONDARY"},
            {"id": "node.js", "display": "Node.js",
             "tier": "STRONG_SECONDARY"},
            {"id": "git", "display": "Git", "tier": "CORE"},
        ]
        built = rf.build([], importance, MARKET,
                         "Aisha Khan\nAspiring full stack developer\n")
        self.assertTrue(built["role_keywords"], built)
        self.assertNotIn("python developer", built["role_keywords"])

    def test_it_works_against_the_shipped_frozen_corpus(self):
        if not os.path.exists(local_search.frozen_path()):
            self.skipTest("data/title_corpus.json.gz is not built")
        frozen = local_search.Market(rows=local_search.frozen_rows(),
                                     seniority=SENIORITY)
        importance = [
            {"id": "react", "display": "React", "tier": "STRONG_SECONDARY"},
            {"id": "node.js", "display": "Node.js",
             "tier": "STRONG_SECONDARY"},
            {"id": "mongodb", "display": "MongoDB", "tier": "SUPPORTING"},
        ]
        built = rf.build([], importance, frozen,
                         "Aisha Khan\nAspiring full stack developer\n")
        self.assertTrue(built["role_keywords"], built)

    def test_no_evidence_at_all_returns_nothing_rather_than_guessing(self):
        built = rf.build([], [], MARKET, "")
        self.assertEqual(built["role_keywords"], [])


class TestTheFlag(unittest.TestCase):
    """Not production-default yet."""

    def setUp(self):
        self.before = os.environ.get(skill_concepts.ROLES_FLAG)
        os.environ.pop(skill_concepts.ROLES_FLAG, None)

    def tearDown(self):
        os.environ.pop(skill_concepts.ROLES_FLAG, None)
        if self.before is not None:
            os.environ[skill_concepts.ROLES_FLAG] = self.before

    def test_off_by_default(self):
        self.assertFalse(skill_concepts.roles_enabled())

    def test_fields_for_uses_the_old_path_when_off(self):
        person = {"skills": ["react native", "node.js"],
                  "employment": [{"title": "Software Engineer",
                                  "company": "Acme", "start": "Jan 2024",
                                  "end": "Present", "relevant": True}]}
        got = local_search.fields_for(person, MARKET, importance=IMPORTANCE,
                                      resume_text=RESUME)
        self.assertNotIn("role_families", got)

    def test_fields_for_uses_the_new_path_when_on(self):
        os.environ[skill_concepts.ROLES_FLAG] = "1"
        person = {"skills": ["react native", "node.js"],
                  "employment": [{"title": "Software Engineer",
                                  "company": "Acme", "start": "Jan 2024",
                                  "end": "Present", "relevant": True}]}
        got = local_search.fields_for(person, MARKET, importance=IMPORTANCE,
                                      resume_text=RESUME)
        self.assertIn("role_families", got)
        self.assertIn("title_hints", got)

    def test_without_importance_it_falls_back_even_when_on(self):
        """Families are anchored by tiers. No tiers, no gate, so the old
        path answers rather than an ungated new one."""
        os.environ[skill_concepts.ROLES_FLAG] = "1"
        person = {"skills": ["react native"], "employment": []}
        got = local_search.fields_for(person, MARKET)
        self.assertNotIn("role_families", got)


if __name__ == "__main__":
    unittest.main()
