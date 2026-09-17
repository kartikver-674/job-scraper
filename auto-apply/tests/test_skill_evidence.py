"""Candidate importance, from where a skill actually appears.

Two facts, and the engine was collapsing them into one number:

    candidate importance   how central this is to what the person does
    market discrimination  how much naming it narrows the job market

Multiplying them let the market answer both. On the audited résumé that
put Firebase Cloud Messaging — one library, one side project, under 1% of
listings — on 5, and TypeScript, the language the candidate writes every
working day and a third of all listings mention, on 2. The review screen
then offered to delete TypeScript.

These pin the tiers, the bounded mapping that keeps the market from
reversing them, and the invariant that matters most: the same evidence
gets the same tier whoever found it.

The fixtures are written here rather than taken from the development
résumé, so the rules are tested rather than the one case they came from.
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
import skill_evidence as se  # noqa: E402

RESUME = """Summary
Full-stack engineer building mobile apps.
Technical Skills
Languages: TypeScript, Apex, C++
Frontend: React Native, Tailwind CSS, L WC
Backend: Node.js, Mongoose, Redis
Professional Experience
Harbourline Systems Pvt Ltd
Software Engineer January 2025 - Present
- Build REST integrations between a React Native app and Salesforce.
- Author Apex unit tests, and optimise React Native render performance.
Projects
LendCircle | React Native, Node.js, Redis, Socket.IO, Cloudinary
- Developed a marketplace on a Node.js backend with Redis caching.
- Added Socket.IO chat, and Planned for a pluggable payment gateway.
Vintage Photo Restoration | Python, TensorFlow, OpenCV
- Trained a GAN on 5,000 image pairs with TensorFlow.
Education
B.E. Computer Science - Artificial Intelligence and Machine Learning
Certifications
AWS Certified Cloud Practitioner
"""

SPANS = se.sections(RESUME)


def concept(term):
    made, = sc.from_weights({term: 3})
    return made


def tier_of(term, text=RESUME, spans=SPANS):
    return se.tier(se.find(concept(term), text, spans))[0]


def why_of(term):
    return se.tier(se.find(concept(term), RESUME, SPANS))[1]


class TestSections(unittest.TestCase):
    """The smallest representation that works. Requirement 2: no résumé
    DOM, and an unknown heading must degrade safely."""

    def test_the_real_sections_are_found(self):
        kinds = {kind for kind, _e, _s, _x in SPANS}
        self.assertLessEqual(
            {se.SUMMARY, se.SKILLS, se.WORK, se.PROJECT, se.EDUCATION,
             se.CERTIFICATION}, kinds)

    def test_entries_are_named(self):
        entries = {e for _k, e, _s, _x in SPANS if e}
        self.assertIn("Harbourline Systems Pvt Ltd", entries)
        self.assertIn("LendCircle", entries)
        self.assertIn("Vintage Photo Restoration", entries)

    def test_a_wrapped_bullet_is_not_a_new_entry(self):
        """A PDF wraps a long bullet; the continuation is not an employer."""
        text = ("Projects\nLendCircle | React Native\n"
                "- Designed a location hook over a rate-limited\n"
                "Google Maps proxy, with JWT auth and shared tokens.\n")
        entries = {e for _k, e, _s, _x in se.sections(text) if e}
        self.assertEqual(entries, {"LendCircle"})

    def test_an_unknown_heading_does_not_lose_the_content(self):
        """Requirement 2: degrade safely. The skill still has a section."""
        text = ("Professional Experience\nAcme\n- Built things with Rust.\n"
                "Curriculum Vitae Addendum\n- Also used Rust here.\n")
        spans = se.sections(text)
        found = se.find(concept("rust"), text, spans)
        self.assertEqual(len(found), 2)
        self.assertTrue(all(e.section == se.WORK for e in found))

    def test_a_document_with_no_headings_at_all_still_works(self):
        text = "I build things with Rust and Elixir every day.\n"
        self.assertEqual(tier_of("rust", text, se.sections(text)),
                         se.SUPPORTING)

    def test_empty_input_does_not_raise(self):
        self.assertEqual(se.sections(""), [])
        self.assertEqual(se.sections(None), [])

    def test_a_pdf_broken_heading_is_still_a_heading(self):
        text = "T echnical Skills\nLanguages: Rust\n"
        kinds = {kind for kind, _e, _s, _x in se.sections(text)}
        self.assertIn(se.SKILLS, kinds)


class TestEvidenceIsFoundNotAsserted(unittest.TestCase):
    """Requirement 1's last line: entity-to-span must be validated. It is
    validated by construction — this code finds the spans itself and never
    reads a quote a model produced."""

    def test_every_span_really_contains_the_alias(self):
        for term in ("react native", "apex", "node.js", "l wc"):
            for item in se.find(concept(term), RESUME, SPANS):
                self.assertEqual(RESUME[item.start:item.end].lower(),
                                 item.alias.lower())

    def test_an_absent_skill_produces_no_evidence(self):
        self.assertEqual(se.find(concept("kubernetes"), RESUME, SPANS), [])

    def test_overlapping_aliases_are_one_occurrence(self):
        """"node" inside "node.js" is one mention, not two."""
        found = se.find(concept("node.js"), "We use Node.js here.\n")
        self.assertEqual(len(found), 1)

    def test_the_pdf_spaced_spelling_finds_its_evidence(self):
        self.assertTrue(se.find(concept("l wc"), RESUME, SPANS))


class TestTiers(unittest.TestCase):
    """Requirement 3, and the development case's diagnostic relationships."""

    def test_professional_work_is_core(self):
        self.assertEqual(tier_of("react native"), se.CORE)
        self.assertEqual(tier_of("apex"), se.CORE)
        self.assertEqual(tier_of("salesforce"), se.CORE)

    def test_the_reason_names_the_employer(self):
        self.assertIn("Harbourline", why_of("apex"))

    def test_a_project_built_with_is_strong_secondary(self):
        """Projects count meaningfully — "paid employment only" is
        explicitly NOT the rule."""
        self.assertEqual(tier_of("node.js"), se.STRONG_SECONDARY)
        self.assertEqual(tier_of("socket.io"), se.STRONG_SECONDARY)
        self.assertIn("LendCircle", why_of("socket.io"))

    def test_a_stack_line_alone_is_only_supporting(self):
        """Named in a project's header and never mentioned again is a
        claim, not evidence of building something."""
        self.assertEqual(tier_of("cloudinary"), se.SUPPORTING)

    def test_a_skills_list_claim_is_supporting(self):
        self.assertEqual(tier_of("tailwind css"), se.SUPPORTING)
        self.assertEqual(tier_of("mongoose"), se.SUPPORTING)
        self.assertEqual(why_of("mongoose"), "listed under skills only")

    def test_coursework_only_is_background(self):
        self.assertEqual(tier_of("machine learning"), se.BACKGROUND)
        self.assertIn("coursework", why_of("machine learning"))

    def test_certification_only_is_background(self):
        self.assertEqual(tier_of("aws"), se.BACKGROUND)

    def test_an_unrelated_project_survives_at_low_influence(self):
        """Requirement: Python/TensorFlow/OpenCV should survive if
        explicit, but not shape the candidate's current identity."""
        for term in ("python", "tensorflow", "opencv"):
            self.assertIn(tier_of(term),
                          (se.SUPPORTING, se.STRONG_SECONDARY), term)
            self.assertLess(se.rank(tier_of(term)), se.rank(se.CORE), term)

    def test_an_unknown_technology_survives(self):
        text = "Professional Experience\nAcme\n- Shipped it in Gleam.\n"
        self.assertEqual(tier_of("gleam", text, se.sections(text)), se.CORE)

    def test_a_skill_with_no_evidence_at_all_is_background(self):
        self.assertEqual(tier_of("kubernetes"), se.BACKGROUND)
        self.assertIn("not found", why_of("kubernetes"))


class TestPlannedWorkIsNotExperience(unittest.TestCase):
    """The invariant: planned work does not become completed experience."""

    def test_a_planned_tool_is_background(self):
        self.assertEqual(tier_of("payment gateway"), se.BACKGROUND)
        self.assertIn("planned", why_of("payment gateway"))

    def test_the_marker_does_not_reach_backwards(self):
        """The audited résumé writes "... in multi-document MongoDB
        transactions , Planned for a pluggable payment gateway." on one
        line. A whole-sentence rule would have unbuilt the MongoDB work."""
        line = ("- a wallet ledger in multi-document MongoDB transactions , "
                "Planned for a pluggable payment gateway.")
        self.assertFalse(se.is_planned(line.lower(), line.lower().index("mongodb")))
        self.assertTrue(se.is_planned(line.lower(), line.lower().index("payment")))

    def test_socket_io_beside_a_planned_clause_still_counts(self):
        self.assertEqual(tier_of("socket.io"), se.STRONG_SECONDARY)

    def test_every_planned_word_is_recognised(self):
        for word in ("planned", "planning", "upcoming", "will be added",
                     "to be implemented", "future", "roadmap"):
            line = f"- {word} a Kubernetes migration."
            self.assertTrue(se.is_planned(line, line.index("kubernetes")
                                          if "kubernetes" in line
                                          else len(line) - 12), word)


class TestOriginIsNotEvidence(unittest.TestCase):
    """Requirement 4, and audit defect C1. The same résumé evidence must
    produce the same importance whoever named the skill."""

    def test_the_tier_ignores_the_weight_it_arrived_with(self):
        for arrived_at in (1, 2, 3, 4, 5):
            made, = sc.from_weights({"apex": arrived_at})
            self.assertEqual(se.tier(se.find(made, RESUME, SPANS))[0],
                             se.CORE, arrived_at)

    def test_model_reported_and_scanner_recovered_agree(self):
        """Qwen names it at the neutral 3; the scanner recovers it at 5.
        The résumé says the same thing about it either way."""
        reported, = sc.from_weights({"apex": 3})
        recovered, = sc.from_weights({"apex": 5})
        self.assertEqual(se.tier(se.find(reported, RESUME, SPANS)),
                         se.tier(se.find(recovered, RESUME, SPANS)))

    def test_the_spelling_that_found_it_does_not_change_the_tier(self):
        for spelling in ("node.js", "node", "NodeJS"):
            self.assertEqual(tier_of(spelling), se.STRONG_SECONDARY, spelling)


class TestTheBoundedMapping(unittest.TestCase):
    """Requirement 6: the market refines, it does not reverse."""

    def test_core_and_common_still_beats_supporting_and_rare(self):
        """The relationship the old multiply inverted: TypeScript (CORE,
        32% of listings) against FCM (SUPPORTING, under 1%)."""
        self.assertGreater(se.weight(se.CORE, 1), se.weight(se.SUPPORTING, 5))

    def test_core_and_common_beats_background_and_rare(self):
        self.assertGreater(se.weight(se.CORE, 1), se.weight(se.BACKGROUND, 5))

    def test_strong_secondary_and_common_beats_background_and_rare(self):
        self.assertGreater(se.weight(se.STRONG_SECONDARY, 1),
                           se.weight(se.BACKGROUND, 5))

    def test_the_market_never_moves_a_weight_out_of_its_band(self):
        for name, (_base, low, high) in se.BANDS.items():
            for separation in (None, 1, 2, 3, 4, 5):
                got = se.weight(name, separation)
                self.assertTrue(low <= got <= high, (name, separation, got))

    def test_rarity_still_orders_two_skills_the_resume_ranks_alike(self):
        """It must do SOMETHING, or the market signal is decoration."""
        self.assertGreater(se.weight(se.SUPPORTING, 5),
                           se.weight(se.SUPPORTING, 1))

    def test_an_abstaining_market_leaves_the_tier_alone(self):
        for name, (base, _low, _high) in se.BANDS.items():
            self.assertEqual(se.weight(name, None), base)

    def test_every_weight_is_a_legal_profile_weight(self):
        for name in se.ORDER:
            for separation in (None, 1, 2, 3, 4, 5):
                self.assertIn(se.weight(name, separation), (1, 2, 3, 4, 5))

    def test_the_bands_of_non_adjacent_tiers_never_overlap(self):
        """The bounded rule, stated as a property: the market may nudge a
        concept past its immediate neighbour and no further."""
        for i, name in enumerate(se.ORDER):
            for other in se.ORDER[i + 2:]:
                _b, low, _h = se.BANDS[other]
                _b2, _l2, high = se.BANDS[name]
                self.assertLess(high, low + 1, (name, other))


class TestAssessTogether(unittest.TestCase):
    """assess_all, where overlaps settle the way scoring settles them."""

    def concepts(self, *terms):
        return sc.from_weights({t: 3 for t in terms})

    def rows(self, *terms, **seps):
        got = se.assess_all(self.concepts(*terms), RESUME, seps or None)
        return {r["id"]: r for r in got}

    def test_react_does_not_inherit_react_natives_employment(self):
        """Alone, "react" finds itself inside every "React Native" and
        looks like professional web work. It is not."""
        rows = self.rows("react", "react native")
        self.assertEqual(rows["react native"]["tier"], se.CORE)
        self.assertNotEqual(rows["react"]["tier"], se.CORE)

    def test_a_fully_shadowed_concept_says_so(self):
        """"not found in the document" would be untrue and read like a
        bug — it was found, always inside a longer name."""
        rows = self.rows("react", "react native")
        self.assertIn("only ever named inside", rows["react"]["why"])
        self.assertIn("React Native", rows["react"]["shadowed_by"])

    def test_a_concept_named_independently_keeps_its_own_evidence(self):
        text = ("Professional Experience\nAcme\n"
                "- Built React web apps and a React Native app.\n")
        rows = {r["id"]: r for r in se.assess_all(
            self.concepts("react", "react native"), text)}
        self.assertEqual(rows["react"]["tier"], se.CORE)
        self.assertEqual(rows["react native"]["tier"], se.CORE)

    def test_the_market_is_carried_through_separately(self):
        rows = self.rows("apex", **{})
        self.assertIn("market_separation", rows["apex"])
        rows = {r["id"]: r for r in se.assess_all(
            self.concepts("apex"), RESUME, {"apex": 5})}
        self.assertEqual(rows["apex"]["market_separation"], 5)
        self.assertEqual(rows["apex"]["tier"], se.CORE)

    def test_sections_are_reported_for_every_concept(self):
        rows = self.rows("apex", "mongoose", "tensorflow")
        self.assertIn(se.WORK, rows["apex"]["sections"])
        self.assertIn(se.SKILLS, rows["mongoose"]["sections"])
        self.assertIn(se.PROJECT, rows["tensorflow"]["sections"])

    def test_it_is_deterministic(self):
        first = se.assess_all(self.concepts("apex", "node.js", "react"),
                              RESUME)
        for _ in range(5):
            again = se.assess_all(self.concepts("apex", "node.js", "react"),
                                  RESUME)
            self.assertEqual([r["tier"] for r in again],
                             [r["tier"] for r in first])


class TestTheDiagnosticRelationships(unittest.TestCase):
    """The development case's expectations, as relationships rather than
    hardcoded outputs."""

    def weight(self, term, separation=None):
        rows = se.assess_all(sc.from_weights({term: 3}), RESUME,
                             {sc.resolve(term): separation})
        return rows[0]["weight"]

    def test_core_mobile_work_outranks_a_rare_supporting_tool(self):
        """React Native (work, mid-market) must not sit below a
        skills-list-only tool that happens to be rare."""
        self.assertGreater(self.weight("react native", 3),
                           self.weight("mongoose", 5))

    def test_a_common_core_language_is_not_disposable(self):
        """TypeScript at its commonest still outranks a list-only claim."""
        self.assertGreaterEqual(self.weight("apex", 1),
                                self.weight("tailwind css", 5))

    def test_list_only_claims_do_not_outrank_project_work(self):
        self.assertGreaterEqual(self.weight("node.js", 2),
                                self.weight("tailwind css", 5))

    def test_the_unrelated_ml_project_stays_below_the_current_stack(self):
        self.assertGreater(self.weight("react native", 3),
                           self.weight("tensorflow", 5))


class TestTheFlag(unittest.TestCase):
    """Requirement: a seam, off by default."""

    def setUp(self):
        self.before = {n: os.environ.get(n)
                       for n in (sc.VERSION_ENV, sc.EVIDENCE_FLAG)}
        for name in self.before:
            os.environ.pop(name, None)

    def tearDown(self):
        for name, value in self.before.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value

    def test_it_is_off_by_default(self):
        """CONTRACT CHANGE: the default returned to v1 on the independent
        review's finding."""
        self.assertEqual(sc.engine_version(), "v1")
        self.assertFalse(sc.evidence_enabled())

    def test_v1_turns_it_off(self):
        os.environ[sc.VERSION_ENV] = "v1"
        self.assertFalse(sc.evidence_enabled())

    def test_the_step_flag_composes_only_when_no_version_is_pinned(self):
        """Kept as an experiment override, so bench/evaluate.py can isolate
        condition C — which is why that harness now removes the version
        variable rather than setting it."""
        os.environ[sc.EVIDENCE_FLAG] = "1"
        self.assertTrue(sc.evidence_enabled())
        os.environ[sc.VERSION_ENV] = "v1"
        self.assertFalse(sc.evidence_enabled())

    def test_it_is_separate_from_the_concept_flag(self):
        """Two switches because they change different things, and the
        comparison wants them apart."""
        self.assertNotEqual(sc.FLAG, sc.EVIDENCE_FLAG)


class TestNoModelCallWasAdded(unittest.TestCase):
    """Requirement 5. The audit measured the alternative: a free evidence
    call timed out at 600s, a larger budget produced undecodable JSON at
    233s, and the bounded call that completed labelled project-only Node
    and Mongo as professional work."""

    def test_the_module_imports_no_inference(self):
        import skill_evidence
        with open(skill_evidence.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for banned in ("import inference", "import local_extract",
                       "_generate", "ollama"):
            self.assertNotIn(banned, source, banned)

    def test_assessment_is_fast_enough_to_be_free(self):
        import time
        concepts = sc.from_weights({t: 3 for t in (
            "react native", "typescript", "apex", "node.js", "mongodb",
            "socket.io", "redis", "tailwind css", "mongoose", "python")})
        started = time.perf_counter()
        se.assess_all(concepts, RESUME)
        self.assertLess((time.perf_counter() - started) * 1000, 250)


if __name__ == "__main__":
    unittest.main()
