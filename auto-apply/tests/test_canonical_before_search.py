"""Concept identity is decided before a query is, not after.

V3 Step 2. Search used to match the corpus with whatever spellings extraction
happened to emit, while the profile resolved those same strings to canonical
concepts one stage later, in _finish. The two layers disagreed about what a
concept was: `agile/scrum` was one string nothing had heard of to the corpus,
and two well-known concepts to the profile — but only after every query had
already been chosen (audit V3-C1).

skill_concepts.identities() is the one transformation both now mean, and
local_profile applies it before local_search.fields_for.

What these tests defend, in order of how badly each would hurt:

  * the FINAL v2 skill representation does not move — the profile is still
    built from the raw list, _finish still atomises and weights it, and one
    concept still scores once
  * search really does receive canonical identities
  * the transformation is safe: fail-closed on unknown compounds, so business
    prose is not shredded into invented skills
  * v1 is untouched, and role_signals stay inert
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

import skill_concepts as sc     # noqa: E402
import local_search             # noqa: E402
import local_extract as le      # noqa: E402
import local_profile            # noqa: E402
import make_profile             # noqa: E402


TEXT = ("Dana Okafor\n"
        "Full-Stack Developer\n"
        "Northwind Retail, Full-Stack Developer, Jan 2022 - Present\n"
        "Built React screens and Node.js services, ran agile ceremonies.\n"
        "Skills: javascript, react.js, node, agile/scrum, rest apis\n")

FIELDS = {"name": "Dana Okafor", "years_experience": 3,
          "titles": ["Full-Stack Developer"],
          "skills": ["javascript", "react.js", "node", "agile/scrum",
                     "rest apis"],
          "companies": ["Northwind Retail"], "education": [],
          "institutions": [], "projects": [], "certifications": []}

EMPLOYMENT = {"target_field": "software engineering", "employment": [
    {"company": "Northwind Retail", "title": "Full-Stack Developer",
     "start": "Jan 2022", "end": "Present", "relevant": True}]}


class Engine:
    """Bind the engine per test the way render.yaml binds it in production.

    skill_concepts.enabled() reads the environment on every call, so a
    rollback takes effect on the next request. That also means a test that
    does not say which engine it wants gets the CODE default, v1 — which is
    how the first draft of this file quietly asserted v2 behaviour against
    the v1 path.
    """

    def bind(self, version):
        self._before = os.environ.get(sc.VERSION_ENV)
        os.environ[sc.VERSION_ENV] = version

    def unbind(self):
        if getattr(self, "_before", None) is None:
            os.environ.pop(sc.VERSION_ENV, None)
        else:
            os.environ[sc.VERSION_ENV] = self._before


def market():
    """A corpus dense enough to rank, sparse enough not to trip the guards.

    The full-stack rows have to stay under WILDCARD_SHARE (25%) of the whole
    market or validate() throws the held title away as "the catalogue", and
    then nothing survives and generate() escalates.
    """
    rows = []
    for i in range(120):
        rows.append(("full stack developer", 25 if i % 2 else 5,
                     frozenset({"javascript", "react", "node.js"}),
                     f"co{i % 20}"))
    for i in range(90):
        rows.append(("react developer", 25 if i % 2 else 4,
                     frozenset({"react", "javascript"}), f"rc{i % 15}"))
    for i in range(1200):
        rows.append((f"data engineer {i % 9}", 3,
                     frozenset({"python", "sql"}), f"big{i % 40}"))
    return local_search.Market(rows=rows, seniority=("senior", "staff", "lead"))


class TheSeam(unittest.TestCase):
    """skill_concepts.identities(), on its own."""

    def test_compounds_atomise(self):
        self.assertEqual(sc.identities(["agile/scrum"]), ["agile", "scrum"])

    def test_aliases_resolve_to_one_identity(self):
        self.assertEqual(sc.identities(["react.js"]), ["react"])
        self.assertEqual(sc.identities(["node"]), ["node.js"])
        self.assertEqual(sc.identities(["lwc"]), ["lightning web components"])

    def test_one_concept_appears_once_however_it_is_spelled(self):
        got = sc.identities(["React", "react.js", "REACTJS", "react"])
        self.assertEqual(got, ["react"])

    def test_a_compound_naming_one_concept_twice_collapses(self):
        # "javascript (es6+)" splits to javascript + es6+, and es6+ IS
        # JavaScript. Two parts, one concept, one entry.
        self.assertEqual(sc.identities(["javascript (es6+)"]), ["javascript"])

    def test_related_concepts_stay_separate(self):
        for a, b in (("react", "react native"), ("firebase", "fcm"),
                     ("azure", "azure devops"), ("salesforce", "apex"),
                     ("socket.io", "websockets")):
            got = sc.identities([a, b])
            self.assertEqual(len(got), 2, f"{a} and {b} collapsed: {got}")

    def test_unknown_concepts_survive(self):
        for unknown in ("bun", "watermelondb", "prisma orm", "greenhouse"):
            self.assertIn(unknown, sc.identities([unknown]))

    def test_business_phrases_are_not_shredded(self):
        # split_compound is fail-closed: it splits only when EVERY part is a
        # known concept. The registry knows no business vocabulary, so these
        # must arrive whole rather than as invented fragments.
        for phrase in ("requirements gathering and stakeholder management",
                       "business analysis and process mapping",
                       "user stories & acceptance criteria",
                       "budgeting and forecasting",
                       "recruiting and onboarding",
                       "compensation and benefits",
                       "research & development"):
            self.assertEqual(sc.identities([phrase]), [phrase])

    def test_it_is_order_preserving_and_total(self):
        got = sc.identities(["typescript", "react", "node"])
        self.assertEqual(got, ["typescript", "react", "node.js"])

    def test_empty_input_is_empty_output(self):
        for empty in ([], (), None):
            self.assertEqual(sc.identities(empty), [])

    def test_presentation_changes_do_not_move_the_identities(self):
        base = ["JavaScript", "React", "Node.js", "Agile/Scrum", "REST APIs"]
        for variant in ([s.upper() for s in base],
                        [s.lower() for s in base],
                        list(reversed(base)),
                        ["JS", "React.js", "Node", "Agile / Scrum", "REST API"],
                        base + ["JS", "NodeJS", "react.js"]):
            self.assertEqual(set(sc.identities(variant)), set(sc.identities(base)),
                             variant)


class SearchSeesCanonical(unittest.TestCase, Engine):
    """The wiring: what fields_for actually receives."""

    def setUp(self):
        self.bind("v2")
        self._e, self._m = le.extract, le.employment
        le.extract = lambda m, t, *a, **k: (dict(FIELDS), 0.1)
        le.employment = lambda m, t, *a, **k: dict(
            EMPLOYMENT, employment=[dict(EMPLOYMENT["employment"][0])])

    def tearDown(self):
        le.extract, le.employment = self._e, self._m
        self.unbind()

    def seen(self):
        got = {}
        real = local_search.fields_for

        def spy(person, mkt, *a, **k):
            got["skills"] = list(person["skills"])
            got["keys"] = sorted(person)
            return real(person, mkt, *a, **k)

        local_search.fields_for = spy
        try:
            profile = local_profile.generate(TEXT, {"avoid": []},
                                             market=market(),
                                             log=lambda *a, **k: None)
        finally:
            local_search.fields_for = real
        return got, profile

    def test_fields_for_receives_canonical_identities(self):
        got, _ = self.seen()
        self.assertIn("react", got["skills"])
        self.assertIn("node.js", got["skills"])
        self.assertNotIn("react.js", got["skills"])
        self.assertNotIn("node", got["skills"])

    def test_compound_atomisation_happens_before_fields_for(self):
        got, _ = self.seen()
        self.assertIn("agile", got["skills"])
        self.assertIn("scrum", got["skills"])
        self.assertNotIn("agile/scrum", got["skills"])

    def test_role_signals_are_still_not_passed_in(self):
        got, profile = self.seen()
        self.assertEqual(got["keys"], ["employment", "skills"])
        self.assertIn("role_signals", profile)

    def test_the_profile_keeps_the_resume_spellings(self):
        # v2 keeps raw spellings as the scorer's matcher keys on purpose.
        # Canonicalising the search input must not rewrite the profile.
        _got, profile = self.seen()
        terms = {e["term"] for e in profile["skill_weights"]}
        self.assertIn("react.js", terms)
        self.assertIn("agile/scrum", terms)
        self.assertNotIn("react", terms)


class FinishIsUnchanged(unittest.TestCase, Engine):
    """The finishing path still owns the profile, and does not double up."""

    def setUp(self):
        self.bind("v2")
        self._e, self._m = le.extract, le.employment
        le.extract = lambda m, t, *a, **k: (dict(FIELDS), 0.1)
        le.employment = lambda m, t, *a, **k: dict(
            EMPLOYMENT, employment=[dict(EMPLOYMENT["employment"][0])])
        self.profile = local_profile.generate(TEXT, {"avoid": []},
                                              market=market(),
                                              log=lambda *a, **k: None)

    def tearDown(self):
        le.extract, le.employment = self._e, self._m
        self.unbind()

    def finished(self):
        return make_profile._finish(self.profile, TEXT, None,
                                    lambda *a, **k: None)

    def test_finish_still_atomises_the_profile(self):
        terms = {e["term"] for e in self.finished()["skill_weights"]}
        self.assertIn("agile", terms)
        self.assertIn("scrum", terms)
        self.assertNotIn("agile/scrum", terms)

    def test_no_term_is_split_twice_into_duplicates(self):
        terms = [e["term"] for e in self.finished()["skill_weights"]]
        self.assertEqual(len(terms), len(set(terms)), terms)

    def test_one_concept_is_weighted_once(self):
        rows = self.finished().get("skill_importance") or []
        ids = [r["id"] for r in rows]
        self.assertEqual(len(ids), len(set(ids)), ids)

    def test_aliases_score_once(self):
        concepts = sc.from_weights(
            {e["term"].strip().lower(): e["weight"]
             for e in self.finished()["skill_weights"]})
        # react.js and react are the same concept; the text names it twice.
        score, matched = sc.score("We use React and React.js here.", concepts)
        self.assertEqual(matched.count("React") if "React" in matched else
                         len([m for m in matched if m.lower() == "react"]), 1)


class RollbackAndInertness(unittest.TestCase, Engine):
    """v1 must not see any of this."""

    def test_v1_gets_the_raw_strings(self):
        self.bind("v1")
        self._e, self._m = le.extract, le.employment
        le.extract = lambda m, t, *a, **k: (dict(FIELDS), 0.1)
        le.employment = lambda m, t, *a, **k: dict(
            EMPLOYMENT, employment=[dict(EMPLOYMENT["employment"][0])])
        got = {}
        realf = local_search.fields_for

        def spy(person, mkt, *a, **k):
            got["skills"] = list(person["skills"])
            return realf(person, mkt, *a, **k)

        local_search.fields_for = spy
        try:
            local_profile.generate(TEXT, {"avoid": []}, market=market(),
                                   log=lambda *a, **k: None)
        finally:
            local_search.fields_for = realf
            le.extract, le.employment = self._e, self._m
            self.unbind()
        self.assertIn("react.js", got["skills"])
        self.assertIn("agile/scrum", got["skills"])

    def test_the_engine_contract_is_untouched(self):
        self.assertEqual(sc.DEFAULT_VERSION, "v1")
        self.assertFalse(sc.roles_enabled())
        self.assertEqual(make_profile.PROFILE_SCHEMA, 1)


if __name__ == "__main__":
    unittest.main()
