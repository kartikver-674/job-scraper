"""The market a machine uses when it has no sweeps of its own.

Skill weights are decided by document frequency across output/ — the jobs
this machine has actually scraped. A fresh clone and the public beta on
Render have no output/ at all, so every term was unmeasured, the corpus
abstained on all of them, and every skill kept the neutral 3 it arrived
with. A profile of nothing but 3s cannot tell a commodity from a
specialism, and those numbers go on to rank real jobs.

So the repo ships one measured table. The rule is a choice, never a merge:
the live corpus answers for everything, or the frozen table does.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_APPLY = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(AUTO_APPLY)
for path in (REPO_ROOT, AUTO_APPLY):
    if path not in sys.path:
        sys.path.insert(0, path)

import corpus_signal  # noqa: E402
import make_profile  # noqa: E402
from local_profile import NEUTRAL_WEIGHT  # noqa: E402

# The résumé this fallback was audited against: a React Native developer
# with a Spring side project. Skill NAMES only — the terms the extraction
# produced, which is all this measurement ever sees.
SARTHAK_SKILLS = [
    "aws", "axios", "azure", "azure data studio", "bazel", "context api",
    "css3", "docker", "git", "github", "gradle", "hibernate (jpa)", "html5",
    "java", "javascript", "maven", "mysql", "postgresql", "postman", "react",
    "react native", "react navigation", "react.js", "redux", "redux thunk",
    "redux toolkit", "restful apis", "rtk query", "salesforce", "spring boot",
    "spring data jpa", "spring framework", "spring security", "sql",
    "tailwind css", "android", "e-commerce", "hibernate", "ios", "jpa",
    "responsive", "spring",
]


def payload(terms=SARTHAK_SKILLS, weight=NEUTRAL_WEIGHT):
    """What the local engine hands the re-scorer: every skill neutral."""
    return {"skill_weights": [{"term": t, "weight": weight} for t in terms]}


def weights_of(data):
    return {e["term"]: e["weight"] for e in data["skill_weights"]}


def live_corpus_present():
    return corpus_signal.usable(corpus_signal.measured_frequencies())


class TestWhichMarketAnswers(unittest.TestCase):
    """live OR frozen, decided the same way every time."""

    def setUp(self):
        self.empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.empty, ignore_errors=True)

    def corpus(self, rows, name="asweep"):
        """A sweep directory shaped like a real one."""
        sweep = os.path.join(self.empty, name)
        os.makedirs(sweep, exist_ok=True)
        with open(os.path.join(sweep, "jobs.csv"), "w", newline="",
                  encoding="utf-8") as fh:
            fh.write("title,score,matched_skills\n")
            for skills in rows:
                fh.write(f"engineer,5,{skills}\n")
        return self.empty

    def test_an_empty_output_directory_uses_the_frozen_table(self):
        freqs, source = corpus_signal.market_signal(self.empty)
        self.assertEqual(source, "frozen")
        self.assertTrue(freqs)

    def test_a_real_live_corpus_overrides_the_frozen_table(self):
        # 300 listings, every one of them naming react: live says react is
        # a commodity here. The frozen table says 30.5%. Live must win.
        corpus = self.corpus(["react"] * 300)
        freqs, source = corpus_signal.market_signal(corpus)
        self.assertEqual(source, "live")
        self.assertEqual(corpus_signal.separation("react", freqs), 1)
        frozen = corpus_signal.frozen_frequencies()
        self.assertNotEqual(freqs.get("react"), frozen.get("react"))

    def test_a_corpus_too_thin_to_measure_is_treated_as_silence(self):
        # Under MIN_LISTINGS every term abstains, so a handful of rows is
        # not a market — it is the same silence as having none.
        corpus = self.corpus(["react"] * 5)
        self.assertEqual(corpus_signal.market_signal(corpus)[1], "frozen")

    def test_the_two_markets_are_never_merged(self):
        """A weight that depended on which dataset a term happened to land
        in would be reproducible from neither."""
        corpus = self.corpus(["react"] * 300)
        freqs, source = corpus_signal.market_signal(corpus)
        self.assertEqual(source, "live")
        # "maven" is in the frozen table and not in this live corpus. It
        # must be absent, not borrowed.
        self.assertIn("maven", corpus_signal.frozen_frequencies())
        self.assertNotIn("maven", freqs)


class TestTheAuditedResume(unittest.TestCase):
    """The concrete case: all 3s, and what the fallback does about it."""

    def setUp(self):
        self.empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.empty, ignore_errors=True)
        self.scored = weights_of(make_profile.reweight_from_corpus(
            payload(), self.empty, log=lambda *a: None))

    def test_it_no_longer_scores_every_skill_the_same(self):
        self.assertNotEqual(set(self.scored.values()), {NEUTRAL_WEIGHT},
                            "a fresh clone still produces a flat profile")
        self.assertEqual(sorted(set(self.scored.values())), [2, 3, 4])

    def test_the_commodity_terms_are_demoted(self):
        # The UI calls weight <= 2 a commodity; these are in a fifth to a
        # third of all listings and separate almost nothing.
        for term in ("javascript", "react", "java", "aws", "salesforce"):
            self.assertEqual(self.scored[term], 2, term)

    def test_the_rare_terms_are_promoted(self):
        for term in ("maven", "gradle", "spring security", "spring data jpa",
                     "tailwind css", "react navigation"):
            self.assertEqual(self.scored[term], 4, term)

    def test_a_term_the_market_has_never_measured_keeps_its_weight(self):
        # Abstention is not a vote for the middle — it just leaves the
        # number alone, which for the local engine is the neutral 3.
        for term in ("azure data studio", "rtk query", "redux thunk"):
            self.assertEqual(self.scored[term], NEUTRAL_WEIGHT, term)

    @unittest.skipUnless(live_corpus_present(),
                         "no output/ corpus on this machine to compare against")
    def test_the_fallback_matches_the_corpus_it_was_frozen_from(self):
        """The whole promise of the snapshot: the same résumé scores the
        same with the shipped table as it does against the sweeps that
        produced it."""
        live = weights_of(make_profile.reweight_from_corpus(
            payload(), None, log=lambda *a: None))
        self.assertEqual(self.scored, live)


class TestItDegradesRatherThanBreaks(unittest.TestCase):
    """A data file is a thing that can go missing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.real_root = corpus_signal.REPO_ROOT

    def tearDown(self):
        corpus_signal.REPO_ROOT = self.real_root

    def test_a_missing_table_falls_back_to_the_old_neutral_behaviour(self):
        corpus_signal.REPO_ROOT = self.tmp  # no data/ directory at all
        out = make_profile.reweight_from_corpus(
            payload(), self.tmp, log=lambda *a: None)
        self.assertEqual(set(weights_of(out).values()), {NEUTRAL_WEIGHT})
        self.assertEqual(corpus_signal.market_signal(self.tmp)[1], "none")

    def test_a_malformed_table_does_not_break_profile_generation(self):
        corpus_signal.REPO_ROOT = self.tmp
        os.makedirs(os.path.join(self.tmp, "data"), exist_ok=True)
        target = os.path.join(self.tmp, corpus_signal.FROZEN_NAME)
        for junk in ("", "{", "null", "[]", '{"frequencies": 7}',
                     '{"frequencies": {"react": "lots"}}',
                     '{"frequencies": {"react": [5, 1]}}',
                     '{"no frequencies key": true}'):
            with self.subTest(junk=junk[:24]):
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write(junk)
                out = make_profile.reweight_from_corpus(
                    payload(), self.tmp, log=lambda *a: None)
                self.assertEqual(set(weights_of(out).values()),
                                 {NEUTRAL_WEIGHT})

    def test_one_bad_entry_does_not_discard_the_good_ones(self):
        corpus_signal.REPO_ROOT = self.tmp
        os.makedirs(os.path.join(self.tmp, "data"), exist_ok=True)
        with open(os.path.join(self.tmp, corpus_signal.FROZEN_NAME), "w",
                  encoding="utf-8") as fh:
            json.dump({"frequencies": {"react": [300, 1000],
                                       "broken": [9, 1],
                                       "alsobroken": "nonsense"}}, fh)
        freqs, source = corpus_signal.market_signal(self.tmp)
        self.assertEqual(source, "frozen")
        self.assertEqual(freqs, {"react": (300, 1000)})


class TestNothingElseMoved(unittest.TestCase):
    """This change touches which MARKET answers. Not the prompts, not the
    schema, not what counts as a skill, not the arithmetic."""

    def setUp(self):
        self.empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.empty, ignore_errors=True)

    def test_only_skill_weights_are_rewritten(self):
        data = dict(payload(), candidate_name="Ada", years_experience=4,
                    role_keywords=["react native developer"],
                    penalty_terms=[{"term": "php", "weight": 6}],
                    domain_half_a=[], domain_bonus=0, notes="unchanged")
        before = json.dumps({k: v for k, v in data.items()
                             if k != "skill_weights"}, sort_keys=True)
        out = make_profile.reweight_from_corpus(data, self.empty,
                                                log=lambda *a: None)
        after = json.dumps({k: v for k, v in out.items()
                            if k != "skill_weights"}, sort_keys=True)
        self.assertEqual(before, after)
        # ... and the terms themselves are the same terms, in order.
        self.assertEqual([e["term"] for e in out["skill_weights"]],
                         [e["term"] for e in data["skill_weights"]])

    def test_the_frozen_table_never_decides_which_skills_exist(self):
        """vocabulary() gates skill_scan's widening. Taking the shipped
        table there would change WHICH skills a résumé yields on a machine
        with no corpus — a different change, needing its own validation.
        widen_skills documents itself as a no-op without output/."""
        self.assertEqual(corpus_signal.vocabulary(self.empty), {})
        self.assertTrue(corpus_signal.frozen_frequencies(),
                        "the frozen table exists, and is still not used here")

    def test_the_weighting_arithmetic_is_untouched(self):
        # The same three properties blend() was written for.
        self.assertEqual(corpus_signal.blend(4, 5), 4)   # strong and rare
        self.assertEqual(corpus_signal.blend(4, 1), 2)   # strong commodity
        self.assertEqual(corpus_signal.blend(1, 5), 2)   # rare, incidental
        self.assertEqual(corpus_signal.blend(3, None), 3)  # abstention
        self.assertEqual(corpus_signal.BANDS,
                         ((0.20, 1), (0.10, 2), (0.04, 3), (0.01, 4)))
        self.assertEqual(corpus_signal.MIN_LISTINGS, 200)

    def test_the_extraction_prompts_and_schema_are_not_part_of_this(self):
        import local_extract
        self.assertIn("skills", make_profile.RESPONSE_SCHEMA["properties"]
                      ["skill_weights"]["items"]["properties"]
                      .get("term", {}).get("type", "string") and "skills")
        # The prompts this change must not have touched still exist and
        # still say nothing about a frozen market.
        for prompt in (local_extract.FIELDS_PROMPT,
                       local_extract.EMPLOYMENT_PROMPT):
            self.assertNotIn("frozen", prompt.lower())
            self.assertNotIn("frequenc", prompt.lower())


class TestTheShippedTable(unittest.TestCase):
    """Provenance: a number in a profile should be traceable to a dataset."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, corpus_signal.FROZEN_NAME),
                  encoding="utf-8") as fh:
            self.payload = json.load(fh)

    def test_it_says_what_produced_it(self):
        corpus = self.payload["corpus"]
        self.assertGreaterEqual(corpus["listings"], 20000)
        self.assertGreaterEqual(corpus["sweeps"], 1)
        self.assertEqual(corpus["min_listings_at_generation"],
                         corpus_signal.MIN_LISTINGS)
        self.assertRegex(self.payload["generated"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("--freeze", self.payload["generated_by"])

    def test_its_own_counts_match_its_contents(self):
        freqs = self.payload["frequencies"]
        self.assertEqual(self.payload["terms"], len(freqs))
        self.assertEqual(
            self.payload["measurable_terms"],
            sum(1 for _h, seen in freqs.values()
                if seen >= corpus_signal.MIN_LISTINGS))

    def test_every_entry_is_a_pair_the_loader_accepts(self):
        loaded = corpus_signal.frozen_frequencies()
        self.assertEqual(len(loaded), self.payload["terms"],
                         "an entry was rejected by the validator")


if __name__ == "__main__":
    unittest.main()
