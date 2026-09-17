"""A canonical concept's answer must not depend on how it was spelled.

Three Step-2 defects from the independent review, all of them the same
mistake in different places: something that should be a property of the
CONCEPT was being read off whatever strings the extractor happened to
emit for this one candidate.

  R3   the market signal took max(separation) over the concept's id AND
       its observed raw spellings, so JavaScript alone measured at band
       1, JavaScript+JS at 2, and JavaScript+JS+ES6 at 4 — same person,
       same résumé, same corpus, three different weights.

  R4-adjacent   a shorter concept could claim text belonging to a longer
       one unless the longer one also happened to be extracted. A
       React-only profile scored on "react native developer"; adding
       React Native to the profile changed React's answer. Scoring
       depended on extraction success for a different skill.

  Unknown names   the compound splitter took any separator, so "Foo/Bar"
       became two invented concepts and "AcmeTool (Enterprise)" lost half
       its name.

These run the REAL v2 finishing path — make_profile.reweight_from_evidence
with the shipped frequency table — not scraper.score_job with a weight
already fixed by hand. The defect lived in how that weight was chosen.
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

# One employer, one bullet, one skill named in it. Every variant below
# differs ONLY in how many spellings of that skill the extractor emitted.
WORK = ("Dana Reed\nProfessional Experience\nHarbourline Systems\n"
        "Software Engineer\nJanuary 2024 - Present\n"
        "- Built services in {term}.\n")

EMPTY = {"role_keywords": [], "penalty_terms": [], "domain_half_a": [],
         "domain_half_b": [], "domain_title_terms": [], "domain_bonus": 0,
         "title_hints": [], "title_exclude": [], "field_summary": "",
         "notes": "", "years_experience": 2}


def finish(spellings, term_in_text, output_dir=None):
    """The real v2 weighting path for one candidate."""
    data = dict(EMPTY, skill_weights=[{"term": s, "weight": 3}
                                      for s in spellings])
    out = make_profile.reweight_from_evidence(
        data, WORK.format(term=term_in_text), output_dir=output_dir,
        log=lambda *a: None)
    rows = {r["id"]: r for r in out.get("skill_importance") or []}
    weights = {e["term"]: e["weight"] for e in out["skill_weights"]}
    return rows, weights


class TestMarketSignalIsAPropertyOfTheConcept(unittest.TestCase):
    """R3. Same concept, same evidence, same corpus — same answer,
    however many redundant spellings arrived with it."""

    # Concepts whose aliases genuinely measure at different bands in the
    # shipped table, which is what made this reproducible at all.
    FAMILIES = [
        ("javascript", "javascript",
         [["javascript"],
          ["javascript", "js"],
          ["javascript", "js", "es6", "es6+", "ecmascript", "es2015"]]),
        ("node.js", "node.js",
         [["node.js"], ["node.js", "nodejs"],
          ["node.js", "nodejs", "node", "node js"]]),
        ("react", "react",
         [["react"], ["react", "react.js"],
          ["react", "react.js", "reactjs", "react js"]]),
        ("postgresql", "postgresql",
         [["postgresql"], ["postgresql", "postgres"],
          ["postgresql", "postgres", "psql"]]),
        ("salesforce", "salesforce",
         [["salesforce"], ["salesforce", "sfdc"],
          ["salesforce", "sfdc", "salesforce crm", "force.com"]]),
    ]

    def observations(self, canonical, in_text, variants):
        seen = []
        for spellings in variants:
            rows, weights = finish(spellings, in_text)
            row = rows.get(canonical)
            self.assertIsNotNone(row, (canonical, spellings))
            seen.append({"n": len(spellings), "tier": row["tier"],
                         "separation": row["market_separation"],
                         "weight": row["weight"],
                         "scored": set(weights.values())})
        return seen

    def test_market_separation_does_not_move_with_spelling_count(self):
        for canonical, in_text, variants in self.FAMILIES:
            seen = self.observations(canonical, in_text, variants)
            bands = {o["separation"] for o in seen}
            self.assertEqual(len(bands), 1,
                             f"{canonical}: separation varies {seen}")

    def test_the_final_weight_does_not_move_either(self):
        for canonical, in_text, variants in self.FAMILIES:
            seen = self.observations(canonical, in_text, variants)
            self.assertEqual(len({o["weight"] for o in seen}), 1,
                             f"{canonical}: weight varies {seen}")

    def test_the_tier_was_never_the_problem_and_stays_stable(self):
        for canonical, in_text, variants in self.FAMILIES:
            seen = self.observations(canonical, in_text, variants)
            self.assertEqual(len({o["tier"] for o in seen}), 1, canonical)

    def test_every_spelling_carries_the_same_number(self):
        """The flat interface downstream speaks: one concept, one weight,
        whichever of its spellings a consumer looks up."""
        for canonical, in_text, variants in self.FAMILIES:
            for spellings in variants:
                _rows, weights = finish(spellings, in_text)
                self.assertEqual(len(set(weights.values())), 1,
                                 (canonical, weights))

    def test_an_unknown_concept_still_gets_an_answer(self):
        rows, _weights = finish(["bun"], "bun")
        self.assertIn("bun", rows)
        self.assertEqual(rows["bun"]["tier"], "CORE")


class TestTheMarketKeyIsDeclaredNotObserved(unittest.TestCase):
    """The mechanism, so a reviewer can see it is not max() again."""

    def test_keys_come_from_the_concept_not_the_candidate(self):
        first = sc.market_keys("javascript")
        self.assertEqual(first[0], "javascript")
        # Identical however the candidate spelled it: the function does
        # not take the candidate's spellings at all.
        self.assertEqual(sc.market_keys("javascript"), first)

    def test_the_order_is_fixed_by_the_table(self):
        keys = sc.market_keys("node.js")
        self.assertEqual(keys[0], "node.js")
        self.assertEqual(list(keys), list(sc.market_keys("node.js")))

    def test_an_unknown_concept_offers_only_itself(self):
        self.assertEqual(sc.market_keys("some brand new runtime"),
                         ("some brand new runtime",))

    def test_the_first_measurable_key_wins_not_the_rarest(self):
        import corpus_signal
        freqs = {"javascript": (3000, 10000),   # common, band 1
                 "es6": (3, 10000)}             # rare, band 5
        got = sc.market_signal_for("javascript", freqs, corpus_signal)
        self.assertEqual(got["key"], "javascript")
        self.assertEqual(got["separation"],
                         corpus_signal.separation("javascript", freqs))

    def test_it_falls_through_to_the_next_declared_key(self):
        import corpus_signal
        # The canonical id unmeasured, an alias measured.
        freqs = {"js": (3000, 10000)}
        got = sc.market_signal_for("javascript", freqs, corpus_signal)
        self.assertEqual(got["key"], "js")
        self.assertIsNotNone(got["separation"])

    def test_abstaining_beats_guessing(self):
        import corpus_signal
        got = sc.market_signal_for("javascript", {}, corpus_signal)
        self.assertIsNone(got["separation"])
        self.assertIsNone(got["key"])

    def test_the_observation_is_traceable(self):
        """Part 10: a future audit has to be able to ask why."""
        import corpus_signal
        freqs = {"javascript": (3000, 10000)}
        got = sc.market_signal_for("javascript", freqs, corpus_signal)
        self.assertEqual(got["id"], "javascript")
        self.assertEqual(got["hits"], 3000)
        self.assertEqual(got["seen"], 10000)
        self.assertIn("javascript", got["considered"])


class TestLiveAndFrozenAgree(unittest.TestCase):
    """Part 3. Equivalent measured data, equivalent concept answer."""

    def signals(self, freqs):
        import corpus_signal
        return {cid: sc.market_signal_for(cid, freqs, corpus_signal)
                for cid in ("javascript", "node.js", "react", "postgresql",
                            "salesforce", "typescript", "apex")}

    def test_the_same_table_gives_the_same_answer_twice(self):
        import corpus_signal
        frozen, _source = corpus_signal.market_signal(os.devnull)
        self.assertEqual(self.signals(frozen), self.signals(frozen))

    def test_live_and_frozen_agree_where_the_data_agrees(self):
        import corpus_signal
        live, live_source = corpus_signal.market_signal()
        frozen = corpus_signal.frozen_frequencies()
        if live_source != "live" or not frozen:
            self.skipTest("no live corpus to compare against")
        for cid, got in self.signals(live).items():
            want = self.signals(frozen)[cid]
            if got["key"] is None or want["key"] is None:
                continue
            if live.get(got["key"]) != frozen.get(got["key"]):
                continue          # genuinely different measurements
            self.assertEqual(got["separation"], want["separation"], cid)


class TestRelatedConceptBoundaries(unittest.TestCase):
    """Part 4. Whether React may claim a span must depend on the text and
    the ontology — never on whether React Native was also extracted."""

    PAIRS = [("react", "react native", "react native developer",
              "react and react native"),
             ("firebase", "firebase cloud messaging", "we use firebase fcm",
              "firebase and fcm"),
             ("azure", "azure devops", "azure devops pipelines",
              "azure and azure devops")]

    def points(self, profile, text):
        return sc.score(text.lower(), sc.from_weights(profile))

    def test_the_shorter_concept_alone_cannot_claim_the_longer_text(self):
        for short, _long, embedded, _both in self.PAIRS:
            got, names = self.points({short: 3}, embedded)
            self.assertEqual(got, 0, f"{short} claimed {embedded!r}: {names}")

    def test_the_answer_is_the_same_whether_the_sibling_was_extracted(self):
        """The invariant the review asked for."""
        for short, long_, embedded, _both in self.PAIRS:
            alone, _ = self.points({short: 3}, embedded)
            together, _ = self.points({short: 3, long_: 5}, embedded)
            self.assertEqual(alone, 0, short)
            self.assertEqual(together, 5, long_)

    def test_the_longer_concept_still_scores_its_own_text(self):
        for _short, long_, embedded, _both in self.PAIRS:
            got, names = self.points({long_: 5}, embedded)
            self.assertEqual(got, 5, (long_, names))

    def test_both_count_when_the_text_genuinely_names_both(self):
        for short, long_, _embedded, both in self.PAIRS:
            got, names = self.points({short: 3, long_: 5}, both)
            self.assertEqual(got, 8, (short, long_, names))

    def test_a_standalone_mention_still_counts_for_the_shorter_one(self):
        for short, _long, _embedded, _both in self.PAIRS:
            got, _names = self.points({short: 3}, f"we use {short} daily")
            self.assertEqual(got, 3, short)

    def test_unrelated_concepts_are_not_suppressed(self):
        """Socket.IO and WebSockets are distinct and neither contains the
        other — no shadowing should apply."""
        got, names = self.points({"socket.io": 5, "websockets": 3},
                                 "socket.io over websockets")
        self.assertEqual(got, 8, names)

    def test_salesforce_and_apex_are_independent(self):
        got, _names = self.points({"salesforce": 4, "apex": 5},
                                  "salesforce and apex developer")
        self.assertEqual(got, 9)


class TestTheSameBoundaryAppliesToEvidence(unittest.TestCase):
    """Part 5. Not a tier redesign — just refusing to let one concept's
    evidence be read off another concept's words."""

    WORK_TEXT = WORK.format(term="React Native")

    def tiers(self, profile):
        return {r["id"]: r["tier"]
                for r in se.assess_all(sc.from_weights(profile),
                                       self.WORK_TEXT)}

    def test_react_alone_gets_no_work_evidence_from_react_native(self):
        self.assertNotEqual(self.tiers({"react": 3}).get("react"), "CORE")

    def test_the_tier_is_the_same_whether_the_sibling_was_extracted(self):
        alone = self.tiers({"react": 3})["react"]
        together = self.tiers({"react": 3, "react native": 5})["react"]
        self.assertEqual(alone, together)

    def test_react_native_still_gets_its_own_work_evidence(self):
        self.assertEqual(self.tiers({"react native": 5})["react native"],
                         "CORE")

    def test_a_genuine_standalone_mention_still_gives_evidence(self):
        text = WORK.format(term="React and Redux")
        got = {r["id"]: r["tier"]
               for r in se.assess_all(sc.from_weights({"react": 3}), text)}
        self.assertEqual(got["react"], "CORE")

    def test_existing_tier_policy_is_otherwise_untouched(self):
        """A skills-list claim is still SUPPORTING, a project still
        STRONG_SECONDARY. This batch does not move tiers."""
        text = ("Technical Skills\nTailwind CSS\n"
                "Projects\nLendCircle | Redis\n"
                "- Built it on Redis with a Redis cache.\n")
        got = {r["id"]: r["tier"]
               for r in se.assess_all(
                   sc.from_weights({"tailwind css": 3, "redis": 3}), text)}
        self.assertEqual(got["tailwind css"], "SUPPORTING")
        self.assertEqual(got["redis"], "STRONG_SECONDARY")


class TestUnknownCompoundsSurvive(unittest.TestCase):
    """Part 6. Split on positive evidence that the parts are concepts —
    never on the presence of a separator."""

    def test_known_and_known_still_splits(self):
        self.assertEqual(sc.split_compound("JWT / OAuth 2.0"),
                         ["jwt", "oauth 2.0"])
        self.assertEqual(sc.split_compound("React Hook Form + Zod"),
                         ["react hook form", "zod"])
        self.assertEqual(sc.split_compound("Agile/Scrum"), ["agile", "scrum"])

    def test_a_known_parenthetical_still_splits(self):
        self.assertEqual(sc.split_compound("Socket.IO (WebSockets)"),
                         ["socket.io", "websockets"])

    def test_an_unknown_compound_survives_whole(self):
        self.assertEqual(sc.split_compound("Foo/Bar"), ["foo/bar"])
        self.assertEqual(sc.split_compound("Research & Development"),
                         ["research & development"])

    def test_an_unknown_parenthetical_name_survives_whole(self):
        self.assertEqual(sc.split_compound("AcmeTool (Enterprise)"),
                         ["acmetool (enterprise)"])

    def test_a_mixed_known_and_unknown_compound_is_kept_whole(self):
        """Documented conservative choice: splitting would invent a
        concept out of the unknown half, and keeping it whole loses
        nothing the scanner cannot recover independently."""
        self.assertEqual(sc.split_compound("Acme/React"), ["acme/react"])
        self.assertEqual(sc.split_compound("React + Acme Widgets"),
                         ["react + acme widgets"])

    def test_product_names_are_still_never_split(self):
        for name in ("CI/CD", "c++", "node.js", "socket.io", "next.js", "c#"):
            self.assertEqual(sc.split_compound(name), [name.lower()], name)

    def test_punctuation_heavy_names_do_not_crash(self):
        for raw in ("A/B/C", "x + y + z", "(((", "a&&b", "-- --", "/", "()",
                    "C++/CLI", "AT&T Labs", ""):
            sc.split_compound(raw)

    def test_an_unknown_name_still_resolves_and_scores(self):
        self.assertEqual(sc.resolve("Foo/Bar"), "foo/bar")
        got, _names = sc.score("we use foo/bar here", sc.from_weights(
            {"foo/bar": 4}))
        self.assertEqual(got, 4)


class TestScorerInvariance(unittest.TestCase):
    """Part 9. Redundant spellings in the PROFILE cannot buy points; a
    repeated mention in the JOB cannot either."""

    FAMILIES = {
        "javascript": ["javascript", "js", "es6", "ecmascript"],
        "node.js": ["node.js", "node", "nodejs", "node js"],
        "postgresql": ["postgresql", "postgres", "psql"],
        "lightning web components": ["lightning web components", "lwc",
                                     "l wc"],
        "oauth": ["oauth", "oauth2", "oauth 2.0"],
    }

    def test_extra_profile_spellings_never_raise_the_score(self):
        for canonical, spellings in self.FAMILIES.items():
            text = " ".join(spellings)
            base, _ = sc.score(text, sc.from_weights({canonical: 3}))
            for count in range(1, len(spellings) + 1):
                profile = {s: 3 for s in spellings[:count]}
                got, _ = sc.score(text, sc.from_weights(profile))
                self.assertEqual(got, base, (canonical, count))

    def test_a_repeated_mention_in_the_job_scores_once(self):
        for canonical, spellings in self.FAMILIES.items():
            once, _ = sc.score(spellings[0], sc.from_weights({canonical: 3}))
            many, _ = sc.score(" ".join(spellings * 3),
                               sc.from_weights({canonical: 3}))
            self.assertEqual(once, many, canonical)

    def test_each_alias_alone_scores_the_same(self):
        for canonical, spellings in self.FAMILIES.items():
            wanted = None
            for spelling in spellings:
                got, _ = sc.score(f"we want {spelling} here",
                                  sc.from_weights({canonical: 3}))
                wanted = got if wanted is None else wanted
                self.assertEqual(got, wanted, (canonical, spelling))

    def test_a_genuinely_distinct_concept_still_adds(self):
        got, _ = sc.score("socket.io over websockets",
                          sc.from_weights({"socket.io": 3,
                                           "websockets": 3}))
        self.assertEqual(got, 6)


if __name__ == "__main__":
    unittest.main()
