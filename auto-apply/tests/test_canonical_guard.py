"""What was valid as a fragment is not automatically valid as its replacement.

V3 Step 7, fixing audit defect V3-C6. `canonical()` swaps a ranked n-gram for a
real corpus title. On the corpus path the replacement is revalidated because
`canonicalise` runs before `validated`; on the ORPHAN path it never meets
`validate` at all, so `software engineer -python developer` reached
`role_keywords`.

What these tests defend, in order of how badly each would hurt:

  * Step 5's candidate-aware exemption is not taken back. The old global
    "contains manager -> invalid" rule must not return by this door
  * odd-looking but real titles survive. `.net developer`, `c++ developer` and
    `ui developer (angular)` are how boards write titles, and a tidiness rule
    would have deleted them
  * nothing is starved, and only REPLACEMENTS are judged
  * the original fragment survives into the record, or no decision can be
    explained
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

import canonical_guard as cg    # noqa: E402
import local_search             # noqa: E402

HARD, SOFT = local_search.seniority_lists()
TITLES = ([("react native developer", 30)] * 40
          + [("software engineer -python developer", 5)] * 27
          + [("engineering manager", 30)] * 25
          + [("principal engineer", 30)] * 15
          + [("backend engineer", 25)] * 60
          + [("veterinary nurse", 25)] * 20
          + [("noise role", 5)] * 300)


def check(title, exempt=()):
    return cg.check(title, TITLES, exempt, HARD)


class CleanReplacementsSurvive(unittest.TestCase):
    """A, F, H."""

    def test_a_a_clean_canonical_title_survives(self):
        ok, rule, _why = check("react native developer")
        self.assertTrue(ok)
        self.assertEqual(rule, "accepted")

    def test_f_an_unknown_occupation_is_not_rejected_for_being_unknown(self):
        import role_evidence
        self.assertIsNone(role_evidence.family_of("veterinary nurse"))
        self.assertTrue(check("veterinary nurse")[0])

    def test_punctuation_inside_a_word_is_not_an_operator(self):
        """A tidiness rule would have deleted every one of these."""
        for title in (".net developer", "c++ developer", "node.js developer",
                      "front-end developer", "ui/ux designer",
                      "ui developer (angular)", "sr. business systems analyst",
                      "account executive, small business",
                      "associate consultant - technology",
                      "devops engineer (observability)"):
            self.assertTrue(check(title)[0], title)

    def test_h_a_non_orphan_replacement_gets_the_same_contract(self):
        """Nothing in the rule asks which path produced the title."""
        for title in ("software engineer -python developer", "sf -data cloud"):
            self.assertFalse(check(title)[0], title)


class TheReplacementBoundary(unittest.TestCase):
    """B, E — the defect itself."""

    def test_b_a_malformed_replacement_does_not_survive_unchecked(self):
        ok, rule, why = check("software engineer -python developer")
        self.assertFalse(ok)
        self.assertEqual(rule, "search_operator")
        self.assertIn("negation operator", why)

    def test_b2_the_harm_named_is_the_search_semantics_not_the_look(self):
        self.assertFalse(check("sf -data cloud")[0])
        self.assertTrue(check("sf data cloud")[0],
                        "removing the operator makes the same words fine")

    def test_e_a_degenerate_replacement_is_refused(self):
        for title in ("", "   ", None, "developer", "engineer"):
            ok, rule, _why = check(title)
            self.assertFalse(ok, title)
            self.assertEqual(rule, "degenerate", title)

    def test_e2_the_catalogue_guard_still_applies_to_the_replacement(self):
        flood = [("backend engineer", 25)] * 90 + [("x y", 5)] * 10
        ok, rule, _why = cg.check("backend engineer", flood, (), HARD)
        self.assertFalse(ok)
        self.assertEqual(rule, "catalogue")

    def test_corpus_membership_is_not_validity(self):
        """The malformed title occurs in the fixture corpus 27 times."""
        self.assertEqual(sum(1 for t, _s in TITLES
                             if "software engineer -python developer" in t), 27)
        self.assertFalse(check("software engineer -python developer")[0])


class StepFiveIsNotUndone(unittest.TestCase):
    """C, D. The old global hard drop must not return by this door."""

    def test_c_a_level_term_in_the_replacement_is_refused(self):
        ok, rule, why = check("principal engineer")
        self.assertFalse(ok)
        self.assertEqual(rule, "level_or_entry")
        self.assertIn("rank rather than a role", why)

    def test_d_a_grounded_role_term_is_kept(self):
        ok, _rule, _why = check("engineering manager",
                                exempt=["engineering manager"])
        self.assertTrue(ok, "Step 5's exemption was taken back")

    def test_d2_an_ungrounded_role_term_is_still_refused(self):
        ok, rule, _why = check("engineering manager")
        self.assertFalse(ok)
        self.assertEqual(rule, "role_term_unexempted")

    def test_the_rule_asks_step_five_rather_than_reimplementing_it(self):
        import inspect
        source = inspect.getsource(cg.check)
        self.assertIn("hard_drop.dropped_role_terms", source)
        self.assertNotIn('"manager"', source)
        self.assertNotIn('"architect"', source)

    def test_level_and_entry_classification_comes_from_step_five(self):
        import hard_drop
        for word in ("principal", "staff"):
            self.assertIn(word, hard_drop.LEVEL_TERMS)
            self.assertNotIn(word, hard_drop.ROLE_TERMS)


class OnlyReplacementsAreJudged(unittest.TestCase):
    """G, and the reason this is not another role gate."""

    def test_a_query_that_was_never_canonicalised_is_untouched(self):
        kept, record = cg.revalidate(
            ["software engineer -python developer"], trace=[],
            titles=TITLES, exempt=(), hard_terms=HARD)
        self.assertEqual(kept, ["software engineer -python developer"])
        self.assertEqual(record["rejected"], [])

    def test_g_orphan_provenance_is_not_consumed_here(self):
        """Step 6 and Step 7 answer different questions about the same query."""
        trace = [("se python", "software engineer -python developer")]
        kept, record = cg.revalidate(
            ["backend engineer", "software engineer -python developer"],
            trace, TITLES, (), HARD)
        self.assertEqual(kept, ["backend engineer"])
        self.assertEqual(record["decisions"][0]["original_fragment"],
                         "se python")

    def test_no_role_family_policy_lives_here(self):
        import inspect
        source = inspect.getsource(cg)
        self.assertNotIn("role_evidence", source)
        self.assertNotIn("family_of", source)
        self.assertNotIn("supports", source)

    def test_the_last_query_is_never_taken_away(self):
        trace = [("se python", "software engineer -python developer")]
        kept, record = cg.revalidate(
            ["software engineer -python developer"], trace, TITLES, (), HARD)
        self.assertEqual(kept, ["software engineer -python developer"])
        self.assertIn("starve", record["fail_open"])
        self.assertEqual(record["rejected"], [])


class TheFallbackPolicy(unittest.TestCase):
    """I, and the ablation §13 asked for."""

    TRACE = [("software engineer python", "software engineer -python developer")]

    def test_the_default_discards(self):
        self.assertEqual(cg.FALLBACK, "discard")

    def test_i_discard_drops_the_query(self):
        kept, record = cg.revalidate(
            ["backend engineer", "software engineer -python developer"],
            self.TRACE, TITLES, (), HARD, fallback="discard")
        self.assertEqual(kept, ["backend engineer"])
        self.assertEqual(record["recovered"], [])

    def test_i2_fragment_recovers_the_original_when_it_passes(self):
        kept, record = cg.revalidate(
            ["backend engineer", "software engineer -python developer"],
            self.TRACE, TITLES, (), HARD, fallback="fragment")
        self.assertIn("software engineer python", kept)
        self.assertEqual(record["recovered"], ["software engineer python"])
        self.assertTrue(record["decisions"][0]["fallback_used"])

    def test_i3_a_fragment_that_fails_the_contract_is_not_recovered(self):
        trace = [("developer", "software engineer -python developer")]
        kept, record = cg.revalidate(
            ["backend engineer", "software engineer -python developer"],
            trace, TITLES, (), HARD, fallback="fragment")
        self.assertEqual(kept, ["backend engineer"])
        self.assertEqual(record["recovered"], [],
                         "a one-word fragment was recovered as a query")


class Determinism(unittest.TestCase):
    """J."""

    def test_j_repeated_validation_is_idempotent(self):
        trace = [("se python", "software engineer -python developer")]
        first, _a = cg.revalidate(["backend engineer",
                                   "software engineer -python developer"],
                                  trace, TITLES, (), HARD)
        second, _b = cg.revalidate(first, trace, TITLES, (), HARD)
        third, _c = cg.revalidate(second, trace, TITLES, (), HARD)
        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_j2_the_same_input_always_gives_the_same_answer(self):
        for _ in range(5):
            self.assertEqual(check("react native developer")[0], True)
            self.assertEqual(check("software engineer -python developer")[0],
                             False)

    def test_case_and_spacing_do_not_change_the_verdict(self):
        for variant in ("Software Engineer -Python Developer",
                        "  software engineer -python developer  ",
                        "SOFTWARE ENGINEER -PYTHON DEVELOPER"):
            self.assertFalse(check(variant)[0], variant)


class Provenance(unittest.TestCase):
    """§7. The original fragment must survive the boundary."""

    def test_fields_for_reports_every_replacement_it_made(self):
        market = local_search.frozen_market()
        fields = local_search.fields_for(
            {"skills": ["redis", "java"], "employment": []}, market)
        self.assertIn("canonical_trace", fields)
        self.assertTrue(fields["canonical_trace"])
        for fragment, title in fields["canonical_trace"]:
            self.assertTrue(fragment)
            if title:
                self.assertTrue(title.strip())

    def test_every_orphan_title_appears_in_the_trace(self):
        market = local_search.frozen_market()
        fields = local_search.fields_for(
            {"skills": ["redis", "java"], "employment": []}, market)
        produced = {t for _f, t in fields["canonical_trace"] if t}
        self.assertTrue(fields["from_orphans"])
        for title in fields["from_orphans"]:
            self.assertIn(title, produced)

    def test_a_decision_carries_original_replacement_rule_and_reason(self):
        trace = [("se python", "software engineer -python developer")]
        _kept, record = cg.revalidate(
            ["backend engineer", "software engineer -python developer"],
            trace, TITLES, (), HARD)
        row = record["decisions"][0]
        for field in ("query", "original_fragment", "rule", "accepted",
                      "reason", "fallback_used"):
            self.assertIn(field, row)
        self.assertTrue(cg.explain(record).strip())


class OffByDefault(unittest.TestCase):

    def setUp(self):
        self.saved = os.environ.get(cg.FLAG)
        os.environ.pop(cg.FLAG, None)

    def tearDown(self):
        os.environ.pop(cg.FLAG, None)
        if self.saved is not None:
            os.environ[cg.FLAG] = self.saved

    def test_the_guard_is_off_unless_asked_for(self):
        self.assertFalse(cg.enabled())

    def test_the_flag_is_read_per_call(self):
        os.environ[cg.FLAG] = "1"
        self.assertTrue(cg.enabled())
        os.environ[cg.FLAG] = "0"
        self.assertFalse(cg.enabled())

    def test_canonical_itself_is_unchanged_without_a_trace(self):
        """The trace parameter is additive; omitting it changes nothing."""
        import collections
        titles = collections.Counter({"react native developer": 86,
                                      "senior react native developer": 4})
        self.assertEqual(local_search.canonical("native developer", titles),
                         "react native developer")


if __name__ == "__main__":
    unittest.main(verbosity=2)
