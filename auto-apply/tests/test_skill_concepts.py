"""One real concept, one scoring contribution.

scraper.score_job added a point per matching TERM, and terms are strings.
A profile carrying three spellings of one technology scored it three times
— "Firebase FCM" collected firebase fcm (3) + fcm (5) + firebase (4) = 12,
against TypeScript's 2 — while the spelling the résumé happened to use was
the only one that matched, so a job saying plain "JavaScript" scored zero
against a candidate whose profile said "javascript (es6+)".

These pin both halves: aliases of one concept score ONCE, and every alias
matches. Related-but-distinct technologies stay separate, and overlap
between them is settled by an explicit span rule rather than by whichever
regexes happen to collide.

Weights are not touched here. A concept takes the highest weight among the
spellings that produced it; what a skill is WORTH is a later step.
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

import skill_concepts as sc  # noqa: E402


def points(text, weights):
    return sc.score(text.lower(), sc.from_weights(weights))[0]


def names(text, weights):
    return sorted(sc.score(text.lower(), sc.from_weights(weights))[1])


class TestAliasesResolveToOneConcept(unittest.TestCase):
    """Requirement 1 and 2: the same technology, however it is spelled."""

    def test_javascript(self):
        for spelling in ("JavaScript", "javascript", "JS", "js",
                         "JavaScript (ES6+)", "ES6", "es6+", "ecmascript"):
            self.assertEqual(sc.resolve(spelling), "javascript", spelling)

    def test_typescript(self):
        for spelling in ("TypeScript", "TS", "ts", "typescript"):
            self.assertEqual(sc.resolve(spelling), "typescript", spelling)

    def test_node(self):
        for spelling in ("Node.js", "NodeJS", "node js", "node", "Node"):
            self.assertEqual(sc.resolve(spelling), "node.js", spelling)

    def test_postgres(self):
        for spelling in ("PostgreSQL", "Postgres", "postgres", "psql"):
            self.assertEqual(sc.resolve(spelling), "postgresql", spelling)

    def test_react_spellings(self):
        for spelling in ("React", "React.js", "ReactJS", "react js"):
            self.assertEqual(sc.resolve(spelling), "react", spelling)

    def test_lwc_including_the_pdf_spacing(self):
        """Requirement 2. pypdf split "LWC" into "L WC" and the profile
        carried the broken form, so a job saying LWC matched nothing. The
        folded key makes every spacing of a name one key."""
        for spelling in ("LWC", "lwc", "L WC", "l wc", "L.W.C.",
                         "Lightning Web Components"):
            self.assertEqual(sc.resolve(spelling),
                             "lightning web components", spelling)

    def test_display_names_are_human(self):
        self.assertEqual(sc.display_name(sc.resolve("l wc")),
                         "Lightning Web Components")
        self.assertEqual(sc.display_name(sc.resolve("js")), "JavaScript")


class TestRelatedIsNotTheSame(unittest.TestCase):
    """Requirement 5 and 6, plus the rest of the audit's distinct pairs.

    Merging is permanent and invisible, so the bar is sameness."""

    PAIRS = [("react", "react native"), ("socket.io", "websockets"),
             ("firebase", "fcm"), ("salesforce", "apex"),
             ("salesforce", "lwc"), ("azure devops", "azure"),
             ("redux", "redux toolkit"), ("rest api", "graphql")]

    def test_each_pair_stays_two_concepts(self):
        for left, right in self.PAIRS:
            self.assertNotEqual(sc.resolve(left), sc.resolve(right),
                                f"{left} was folded into {right}")

    def test_react_native_is_not_a_react_job(self):
        self.assertEqual(sc.resolve("React Native"), "react native")
        self.assertEqual(sc.resolve("react-native"), "react native")

    def test_rn_is_left_alone_because_it_is_how_nurses_are_advertised(self):
        self.assertEqual(sc.resolve("rn"), "rn")

    def test_azure_devops_is_not_cloud_experience(self):
        """A CI/CD product that carries the Azure name. Folding it would
        invent cloud engineering the candidate never claimed."""
        self.assertEqual(sc.resolve("Azure DevOps"), "azure devops")
        self.assertEqual(sc.resolve("Microsoft Azure"), "azure")


class TestUnknownSkillsSurvive(unittest.TestCase):
    """Requirement 7. Gating on a dictionary would delete real modern
    tools, which is the failure the audit warns about in §10."""

    def test_an_unknown_tool_resolves_to_itself(self):
        self.assertEqual(sc.resolve("Bun"), "bun")
        self.assertEqual(sc.resolve("Turbopack"), "turbopack")

    def test_an_unknown_multiword_tool_keeps_its_name(self):
        self.assertEqual(sc.resolve("Acme Quantum Runtime"),
                         "acme quantum runtime")

    def test_an_unknown_tool_still_scores(self):
        self.assertEqual(points("We use Bun in production", {"bun": 4}), 4)

    def test_an_unknown_parenthetical_is_not_guessed_at(self):
        """Stripping only happens when the head is known, so an unknown
        product keeps its whole name rather than being truncated."""
        self.assertEqual(sc.resolve("Acme Runtime (fast)"),
                         "acme runtime (fast)")

    def test_an_unknown_tool_is_not_merged_with_a_known_one(self):
        self.assertNotEqual(sc.resolve("reactive streams"), "react")


class TestCompoundsSplitOnlyWhenSafe(unittest.TestCase):
    """Requirement 8. A product name with punctuation is far commoner than
    a compound, so a known name is never split."""

    def test_the_audit_compounds_split(self):
        self.assertEqual(sc.split_compound("JWT / OAuth 2.0"),
                         ["jwt", "oauth 2.0"])
        self.assertEqual(sc.split_compound("React Hook Form + Zod"),
                         ["react hook form", "zod"])
        self.assertEqual(sc.split_compound("Socket.IO (WebSockets)"),
                         ["socket.io", "websockets"])
        self.assertEqual(sc.split_compound("Agile/Scrum"), ["agile", "scrum"])
        self.assertEqual(sc.split_compound("Authentication & Security"),
                         ["authentication", "security"])

    def test_product_names_are_never_split(self):
        for name in ("CI/CD", "ci/cd", "c++", "C++", "node.js", "socket.io",
                     "next.js", "c#", "express.js"):
            self.assertEqual(sc.split_compound(name), [name.lower()], name)

    def test_a_parenthetical_that_is_not_a_concept_is_just_dropped(self):
        self.assertEqual(sc.split_compound("Redis (caching)"), ["redis"])
        self.assertEqual(sc.split_compound("Python (scripting)"), ["python"])

    def test_a_fragment_too_short_aborts_the_whole_split(self):
        """"c++" splits to ['c', '', ''] on '+'. Better one right name than
        two invented ones."""
        self.assertEqual(sc.split_compound("a+b"), ["a+b"])
        self.assertEqual(sc.split_compound("x/y"), ["x/y"])

    def test_an_unknown_compound_still_splits(self):
        self.assertEqual(sc.split_compound("Bun / Deno"), ["bun", "deno"])

    def test_atomize_dedupes_across_terms(self):
        self.assertEqual(
            sc.atomize(["Agile/Scrum", "agile", "JWT / OAuth 2.0"]),
            ["agile", "scrum", "jwt", "oauth 2.0"])

    def test_empty_and_blank_survive(self):
        self.assertEqual(sc.split_compound(""), [])
        self.assertEqual(sc.split_compound(None), [])


class TestOneConceptScoresOnce(unittest.TestCase):
    """Requirements 3, 4 and 9 — the core of this step."""

    FIREBASE = {"firebase fcm": 3, "fcm": 5, "firebase": 4}

    def test_firebase_fcm_no_longer_triples(self):
        """Requirement 4. 3 + 5 + 4 = 12 became 5."""
        self.assertEqual(points("We use Firebase FCM for push",
                                self.FIREBASE), 5)
        self.assertEqual(names("We use Firebase FCM for push", self.FIREBASE),
                         ["Firebase Cloud Messaging"])

    def test_the_platform_still_counts_when_the_job_names_it_separately(self):
        """Not suppression for its own sake: the job really did ask for
        both, so both count."""
        self.assertEqual(points("Firebase and FCM", self.FIREBASE), 9)
        self.assertEqual(names("Firebase and FCM", self.FIREBASE),
                         ["Firebase", "Firebase Cloud Messaging"])

    def test_node_aliases_cannot_add_points(self):
        """Requirement 3."""
        text = "Node.js and Node experience"
        self.assertEqual(points(text, {"node.js": 2}), 2)
        self.assertEqual(points(text, {"node.js": 2, "node": 2}), 2)
        self.assertEqual(
            points(text, {"node.js": 2, "node": 2, "nodejs": 2, "node js": 2}),
            2)

    def test_adding_an_alias_never_raises_the_score(self):
        """Requirement 9, as an invariant over every alias in the table."""
        text = ("JavaScript TypeScript Node.js React React Native MongoDB "
                "Postgres LWC Apex Firebase FCM Socket.IO WebSockets REST")
        for canonical in sc.CONCEPTS:
            base = {canonical: 3}
            grown = dict(base)
            for alias in sc.aliases_for(canonical):
                grown[alias] = 3
                self.assertEqual(points(text, grown), points(text, base),
                                 f"{alias} changed {canonical}'s score")

    def test_the_weight_is_the_highest_of_the_spellings(self):
        """Documented rule: this step changes how often a concept counts,
        never what it is worth."""
        concept, = sc.from_weights({"js": 1, "javascript": 4, "es6": 2})
        self.assertEqual(concept.weight, 4)
        self.assertEqual(concept.id, "javascript")

    def test_every_raw_spelling_is_kept_for_provenance(self):
        concept, = sc.from_weights({"js": 1, "javascript": 4})
        self.assertEqual(sorted(concept.raw), ["javascript", "js"])


class TestMatchingUsesAliasesAsOrMatchers(unittest.TestCase):
    """Requirement 5's regressions: the profile's one spelling must not be
    the only thing that matches."""

    def test_plain_javascript_matches_a_resume_that_said_es6(self):
        """v1 scored this ZERO."""
        self.assertEqual(points("Strong JavaScript required",
                                {"javascript (es6+)": 3}), 3)

    def test_plain_lwc_matches_the_pdf_spaced_profile(self):
        """v1 scored this ZERO."""
        self.assertEqual(points("LWC and Apex", {"l wc": 3}), 3)

    def test_the_resume_spelling_still_matches_too(self):
        self.assertEqual(points("JavaScript (ES6+) required",
                                {"javascript (es6+)": 3}), 3)

    def test_spellings_that_fold_alike_are_both_kept_as_matchers(self):
        """Folding is for LOOKUP — deciding "l wc" and "lwc" are one
        concept. They are different MATCHERS, and the audited résumé says
        "L WC": deduping matchers by folded key found nothing in it."""
        concept, = sc.from_weights({"l wc": 3})
        self.assertIn("l wc", concept.aliases)
        self.assertIn("lwc", concept.aliases)
        self.assertEqual(points("We need L WC and Apex", {"l wc": 3}), 3)
        self.assertEqual(points("We need LWC and Apex", {"l wc": 3}), 3)
        self.assertEqual(
            points("Lightning Web Components required", {"l wc": 3}), 3)

    def test_the_spaced_and_unspaced_forms_still_score_once(self):
        """Both matchers, one concept, one contribution."""
        self.assertEqual(points("LWC, also written L WC", {"l wc": 3}), 3)

    def test_a_concept_brings_aliases_the_profile_never_carried(self):
        concept, = sc.from_weights({"typescript": 2})
        self.assertIn("ts", concept.aliases)
        self.assertEqual(points("TS required", {"typescript": 2}), 2)


class TestParentChildPolicy(unittest.TestCase):
    """Requirement 5's last line: an explicit tested policy, not accidental
    regex overlap.

    A concept contributes only if it has at least one match that is not
    wholly inside a LONGER match of another concept."""

    REACT = {"react": 2, "react native": 3}

    def test_a_react_native_job_does_not_also_score_react(self):
        self.assertEqual(sc.score("react native developer",
                                  sc.from_weights(self.REACT)),
                         (3, ["React Native"]))

    def test_a_react_job_scores_react(self):
        self.assertEqual(sc.score("react developer",
                                  sc.from_weights(self.REACT)),
                         (2, ["React"]))

    def test_a_job_naming_both_scores_both(self):
        got, matched = sc.score("react and react native",
                                sc.from_weights(self.REACT))
        self.assertEqual(got, 5)
        self.assertEqual(sorted(matched), ["React", "React Native"])

    def test_salesforce_crm_scores_salesforce_once(self):
        weights = {"salesforce": 2, "salesforce crm": 3, "crm": 2}
        self.assertEqual(sc.score("salesforce crm consultant",
                                  sc.from_weights(weights)),
                         (3, ["Salesforce"]))

    def test_rest_api_design_scores_rest_once(self):
        weights = {"rest": 2, "rest api": 4, "rest apis": 3,
                   "rest api design": 3, "api design": 4}
        self.assertEqual(sc.score("rest api design experience",
                                  sc.from_weights(weights)),
                         (4, ["REST APIs"]))

    def test_api_design_counts_on_its_own(self):
        weights = {"rest api": 4, "api design": 4}
        got, matched = sc.score("api design and rest api work",
                                sc.from_weights(weights))
        self.assertEqual(got, 8)
        self.assertEqual(sorted(matched), ["API Design", "REST APIs"])

    def test_socket_io_and_websockets_both_count_when_both_named(self):
        """Requirement 6: distinct concepts are not suppressed — neither is
        inside the other."""
        weights = {"socket.io": 5, "websockets": 3}
        self.assertEqual(points("Socket.IO over WebSockets", weights), 8)
        self.assertEqual(points("Socket.IO only", weights), 5)
        self.assertEqual(points("WebSockets only", weights), 3)


class TestDeterminism(unittest.TestCase):
    """Requirement 10."""

    WEIGHTS = {"js": 1, "javascript": 2, "javascript (es6+)": 3, "es6": 1,
               "react": 2, "react native": 4, "bun": 5}

    def shape(self, weights):
        return sorted((c.id, c.weight, c.aliases, tuple(sorted(c.raw)))
                      for c in sc.from_weights(weights))

    def test_repeated_builds_agree(self):
        first = self.shape(self.WEIGHTS)
        for _ in range(10):
            self.assertEqual(self.shape(dict(self.WEIGHTS)), first)

    def test_insertion_order_does_not_matter(self):
        first = self.shape(self.WEIGHTS)
        rotated = dict(reversed(list(self.WEIGHTS.items())))
        self.assertEqual(self.shape(rotated), first)

    def test_resolve_is_pure(self):
        for term in ("JS", "l wc", "Bun", "React Native"):
            self.assertEqual(sc.resolve(term), sc.resolve(term))

    def test_no_folded_key_is_claimed_by_two_different_concepts(self):
        """The real determinism risk. Two concepts wanting the same key
        would make resolve() depend on dict order.

        Within ONE concept a repeat is fine and sometimes wanted: "golang"
        and "go lang" fold alike but are different MATCHERS, and only the
        second matches a job that writes the space."""
        seen = {}
        for canonical, entry in sc.CONCEPTS.items():
            for alias in (canonical,) + tuple(entry[1:]):
                folded = sc._key(alias)
                self.assertEqual(
                    seen.setdefault(folded, canonical), canonical,
                    f"{alias!r} is claimed by {seen.get(folded)} and {canonical}")

    def test_no_alias_is_a_short_ordinary_english_word(self):
        """An alias becomes a matcher for every profile naming the concept,
        so "go" and "rn" would inject false hits into profiles that never
        carried them. Measured, not assumed."""
        prose = ("We go to market fast. Our RN team ships it. You will go "
                 "far here. It is a big deal to be so on top of things.")
        for canonical, entry in sc.CONCEPTS.items():
            for alias in entry[1:]:
                if len(alias) > 3:
                    continue
                self.assertFalse(
                    sc.compile_alias(alias).search(prose),
                    f"{alias!r} ({canonical}) matches ordinary prose")


class TestWhatGetsRecorded(unittest.TestCase):
    """Requirement 6 of the brief: do not build a future corpus carrying
    the same duplication."""

    def test_a_stored_row_translates_forward(self):
        self.assertEqual(sc.canonical_matched("firebase fcm, fcm, firebase"),
                         ["Firebase Cloud Messaging", "Firebase"])

    def test_aliases_collapse_to_one_recorded_name(self):
        self.assertEqual(sc.canonical_matched("js, javascript, es6"),
                         ["JavaScript"])

    def test_a_list_works_as_well_as_a_csv_cell(self):
        self.assertEqual(sc.canonical_matched(["node", "node.js"]), ["Node.js"])

    def test_unknown_terms_are_kept(self):
        self.assertEqual(sc.canonical_matched("bun, react"), ["bun", "React"])

    def test_blanks_are_dropped(self):
        self.assertEqual(sc.canonical_matched("react, , ,node"),
                         ["React", "Node.js"])


class TestTheFlag(unittest.TestCase):
    """Requirement 7: a seam, off by default, not exposed to users yet."""

    def setUp(self):
        self.before = os.environ.get(sc.FLAG)

    def tearDown(self):
        os.environ.pop(sc.FLAG, None)
        if self.before is not None:
            os.environ[sc.FLAG] = self.before

    def test_off_by_default(self):
        os.environ.pop(sc.FLAG, None)
        self.assertFalse(sc.enabled())

    def test_on_when_asked(self):
        for value in ("1", "true", "yes", "on", "TRUE"):
            os.environ[sc.FLAG] = value
            self.assertTrue(sc.enabled(), value)

    def test_anything_else_is_off(self):
        for value in ("0", "", "no", "off", "maybe"):
            os.environ[sc.FLAG] = value
            self.assertFalse(sc.enabled(), value)

    def test_it_is_read_per_call_not_at_import(self):
        """So a comparison run can flip it without reimporting."""
        os.environ[sc.FLAG] = "1"
        self.assertTrue(sc.enabled())
        os.environ[sc.FLAG] = "0"
        self.assertFalse(sc.enabled())


class TestScraperIntegration(unittest.TestCase):
    """Requirements 11 and 12: the seam must be invisible until asked for.

    scraper precompiles BOTH representations at import and picks per call,
    so these flip the flag without reimporting anything."""

    ROW = {"Title": "React Native Developer", "Company": "Synthetic Example",
           "Description": "Firebase FCM, REST API design, Node.js and Node"}

    def setUp(self):
        import scraper
        self.scraper = scraper
        self.before = os.environ.get(sc.FLAG)
        os.environ.pop(sc.FLAG, None)

    def tearDown(self):
        os.environ.pop(sc.FLAG, None)
        if self.before is not None:
            os.environ[sc.FLAG] = self.before

    def scored(self, row=None):
        return self.scraper.score_job(dict(row or self.ROW))

    def test_v1_is_the_default(self):
        self.assertFalse(sc.enabled())

    def test_v1_scoring_is_byte_for_byte_what_it_was(self):
        """The old loop, reproduced here from SKILL_PATTERNS, must agree
        with what score_job returns when the flag is off."""
        text = (self.ROW["Title"] + "\n" + self.ROW["Description"]).lower()
        expected = sum(w for _t, (w, pat) in self.scraper.SKILL_PATTERNS.items()
                       if pat.search(text))
        row = self.scored()
        skills_only = sum(w for _t, (w, pat)
                          in self.scraper.SKILL_PATTERNS.items()
                          if pat.search(text))
        self.assertEqual(expected, skills_only)
        self.assertIsNotNone(row)

    def test_the_flag_changes_the_score_and_changes_back(self):
        first = self.scored()["score"]
        os.environ[sc.FLAG] = "1"
        concept = self.scored()["score"]
        os.environ.pop(sc.FLAG)
        self.assertEqual(self.scored()["score"], first)
        self.assertLess(concept, first, "alias inflation should have gone")

    def test_v2_records_canonical_display_names(self):
        os.environ[sc.FLAG] = "1"
        matched = self.scored()["matched_skills"]
        self.assertIn("React Native", matched)
        self.assertIn("Firebase Cloud Messaging", matched)
        # ...and not the alias soup v1 recorded.
        self.assertNotIn("firebase fcm", matched)
        self.assertNotIn(", fcm", matched)

    def test_v1_still_records_raw_terms(self):
        matched = self.scored()["matched_skills"]
        self.assertIn("react native", matched)

    def test_both_representations_are_built_at_import(self):
        self.assertTrue(self.scraper.SKILL_PATTERNS)
        self.assertTrue(self.scraper.SKILL_CONCEPTS)

    def test_every_configured_term_reaches_a_concept(self):
        """Nothing is dropped on the way into the grouped representation."""
        from config import SCORING
        covered = set()
        for concept in self.scraper.SKILL_CONCEPTS:
            covered.update(concept.raw)
        self.assertEqual(covered, set(SCORING["skill_weights"]))

    def test_no_concept_weight_exceeds_its_sources(self):
        for concept in self.scraper.SKILL_CONCEPTS:
            from config import SCORING
            self.assertEqual(
                concept.weight,
                max(SCORING["skill_weights"][r] for r in concept.raw))


class TestExistingProfilesStillParse(unittest.TestCase):
    """Requirement 11. The profile FORMAT is untouched by this step: the
    grouping happens at scoring time, so every profile on disk is read
    exactly as before and no migration is needed."""

    def profiles(self):
        import glob
        return [p for p in sorted(glob.glob(
            os.path.join(REPO_ROOT, "profiles", "*.py")))
            if not os.path.basename(p).startswith("_")]

    def test_there_are_profiles_to_check(self):
        self.assertGreater(len(self.profiles()), 10)

    def test_every_profile_still_imports(self):
        import importlib
        for path in self.profiles():
            importlib.import_module(
                "profiles." + os.path.basename(path)[:-3])

    def test_every_profile_groups_without_losing_a_term(self):
        import importlib
        for path in self.profiles():
            module = importlib.import_module(
                "profiles." + os.path.basename(path)[:-3])
            weights = getattr(module, "SCORING", {}).get("skill_weights")
            if not weights:
                continue
            covered = set()
            for concept in sc.from_weights(weights):
                covered.update(concept.raw)
            self.assertEqual(covered, set(weights), path)

    def test_grouping_never_invents_a_weight(self):
        import importlib
        for path in self.profiles():
            module = importlib.import_module(
                "profiles." + os.path.basename(path)[:-3])
            weights = getattr(module, "SCORING", {}).get("skill_weights")
            if not weights:
                continue
            for concept in sc.from_weights(weights):
                self.assertIn(concept.weight,
                              [weights[r] for r in concept.raw], path)


class TestSplittingInThePipeline(unittest.TestCase):
    """make_profile.split_compounds, the one place the atomic form is
    actually applied. Behind the same flag as the scorer."""

    def setUp(self):
        import make_profile
        self.mp = make_profile
        self.before = os.environ.get(sc.FLAG)
        os.environ[sc.FLAG] = "1"

    def tearDown(self):
        os.environ.pop(sc.FLAG, None)
        if self.before is not None:
            os.environ[sc.FLAG] = self.before

    def split(self, weights):
        data = {"skill_weights": [{"term": t, "weight": w}
                                  for t, w in weights]}
        out = self.mp.split_compounds(data, log=lambda *a: None)
        return {e["term"]: e["weight"] for e in out["skill_weights"]}

    def test_a_compound_becomes_its_halves(self):
        self.assertEqual(self.split([("jwt / oauth 2.0", 3)]),
                         {"jwt": 3, "oauth 2.0": 3})

    def test_splitting_never_costs_a_term_its_weight(self):
        """"jwt / oauth 2.0" (3) splits onto a scanner "jwt" (4). The
        higher wins — the half must not demote the term it lands on."""
        self.assertEqual(self.split([("jwt / oauth 2.0", 3), ("jwt", 4)]),
                         {"jwt": 4, "oauth 2.0": 3})

    def test_it_holds_whichever_order_they_arrive_in(self):
        self.assertEqual(self.split([("jwt", 4), ("jwt / oauth 2.0", 3)]),
                         {"jwt": 4, "oauth 2.0": 3})

    def test_a_product_name_is_left_whole(self):
        self.assertEqual(self.split([("ci/cd", 2), ("c++", 2)]),
                         {"ci/cd": 2, "c++": 2})

    def test_the_raw_string_is_kept_for_provenance(self):
        data = {"skill_weights": [{"term": "Agile/Scrum", "weight": 3}]}
        out = self.mp.split_compounds(data, log=lambda *a: None)
        self.assertEqual(out["skills_split"],
                         [{"raw": "Agile/Scrum", "into": ["agile", "scrum"]}])

    def test_it_does_nothing_at_all_when_the_flag_is_off(self):
        os.environ.pop(sc.FLAG, None)
        data = {"skill_weights": [{"term": "jwt / oauth 2.0", "weight": 3}]}
        self.assertIs(self.mp.split_compounds(data, log=lambda *a: None), data)


if __name__ == "__main__":
    unittest.main()
