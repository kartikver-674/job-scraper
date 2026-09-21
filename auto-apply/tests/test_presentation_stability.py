"""Does the same career, packaged two ways, produce the same profile?

The regression suite for docs/profile-presentation-stability-fixes.md. Two real
résumés for one person — Srishti Rawat, presented once as a Functional
Consultant and once as a Salesforce BA — produced 1 searchable role against 9,
and Salesforce at weight 1 against 4.

The audit traced that to two deterministic defects, and this file locks both
fixes. The model's own reading of a document is not testable without it, so the
end-to-end variant sweep lives in the audit's numbers; everything here is
deterministic and needs no model.

  FIX 1  skill_concepts.split_compound atomised an &-joined skill only when
         EVERY half was in LOOKUP. The market knows `sales cloud` and `service
         cloud`; LOOKUP does not. So "Sales Cloud & Service Cloud" reached
         local_search as one string matching zero listings, and role_keywords
         collapsed to the held job title. Behind SWEEP_MARKET_COMPOUND_SPLIT,
         default off.

  FIX 2  skill_evidence._HEADINGS did not recognise "TOOLS & TECHNOLOGIES",
         "CAREER HISTORY" and five other labels real résumés use. The first
         swallowed a tools block into whichever section preceded it; the second
         meant no WORK section ever opened, so nothing in the document could
         reach CORE. No flag — a label that opened nothing can only move
         concepts towards their real section.

The corpus assertions run against the FROZEN corpus, never output/, so growing
the live corpus cannot turn a passing test into a failing one without an engine
change.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import corpus_signal                                           # noqa: E402
import local_search                                            # noqa: E402
import role_evidence                                           # noqa: E402
import skill_concepts                                          # noqa: E402
import skill_evidence as se                                    # noqa: E402

FLAG = skill_concepts.MARKET_SPLIT_FLAG

# The fourteen skills the local model read from the Functional Consultant
# résumé's SKILLS line, verbatim. The document itself is not reproduced: these
# are what reached the engine, and they are what the defect acted on.
FUNCTIONAL_SKILLS = (
    "change management & user adoption", "business operations handling",
    "requirement gathering & analysis", "brd / frd & user story writing",
    "process flows & workflow mapping", "gap analysis & process improvement",
    "client & stakeholder management", "uat, testing & deployment support",
    "project coordination", "agile & sdlc",
    "salesforce administration & configuration", "sales cloud & service cloud",
    "flow builder automation", "reports & dashboards",
)

# The eleven it read from the Salesforce BA résumé's TECHNICAL SKILLS block.
BA_SKILLS = (
    "salesforce", "sales cloud", "service cloud", "experience builder",
    "flow builder", "reports & dashboards", "soql", "microsoft excel",
    "microsoft powerpoint", "english", "hindi",
)


class MarketSplit(unittest.TestCase):
    """The flag, and what it does and does not license."""

    def setUp(self):
        self._before = os.environ.get(FLAG)
        skill_concepts._MARKET_TERMS = None

    def tearDown(self):
        if self._before is None:
            os.environ.pop(FLAG, None)
        else:
            os.environ[FLAG] = self._before
        skill_concepts._MARKET_TERMS = None

    def on(self):
        os.environ[FLAG] = "1"
        skill_concepts._MARKET_TERMS = None

    def off(self):
        os.environ.pop(FLAG, None)
        skill_concepts._MARKET_TERMS = None

    # ---- off is the old engine, exactly --------------------------------
    def test_flag_off_consults_no_market_at_all(self):
        self.off()
        self.assertFalse(skill_concepts.market_split_enabled())
        self.assertEqual(skill_concepts.market_terms(), frozenset())

    def test_flag_off_leaves_every_functional_skill_compound(self):
        self.off()
        self.assertEqual(skill_concepts.identities(FUNCTIONAL_SKILLS),
                         list(FUNCTIONAL_SKILLS))

    def test_flag_off_refuses_the_compounds_the_market_knows(self):
        self.off()
        for compound in ("sales cloud & service cloud", "agile & sdlc",
                         "reports & dashboards"):
            self.assertEqual(skill_concepts.split_compound(compound),
                             [compound], compound)

    # ---- on splits exactly what the market establishes ------------------
    def test_flag_on_splits_two_market_terms(self):
        self.on()
        self.assertEqual(skill_concepts.split_compound("sales cloud & service cloud"),
                         ["sales cloud", "service cloud"])
        self.assertEqual(skill_concepts.split_compound("reports & dashboards"),
                         ["reports", "dashboards"])

    def test_flag_on_splits_a_mixed_lookup_and_market_compound(self):
        # `agile` is in LOOKUP, `sdlc` only in the market. Either registry
        # establishes a half; the rule is that every half needs one.
        self.on()
        self.assertIn("agile", skill_concepts.LOOKUP)
        self.assertNotIn(skill_concepts._key("sdlc"), skill_concepts.LOOKUP)
        self.assertIn("sdlc", skill_concepts.market_terms())
        self.assertEqual(skill_concepts.split_compound("agile & sdlc"),
                         ["agile", "sdlc"])

    def test_an_unknown_half_still_blocks_the_split(self):
        self.on()
        for compound in ("research & development", "foo & bar",
                         "salesforce administration & configuration",
                         "gap analysis & process improvement",
                         "client & stakeholder management"):
            self.assertEqual(skill_concepts.split_compound(compound),
                             [compound], compound)

    def test_punctuated_product_names_survive_the_flag(self):
        self.on()
        for name in ("c++", "ci/cd", "node.js", "socket.io", "asp.net",
                     "c#", ".net"):
            self.assertEqual(skill_concepts.split_compound(name), [name], name)

    def test_the_ba_list_needs_no_splitting_either_way(self):
        for setter in (self.off, self.on):
            setter()
            out = skill_concepts.identities(BA_SKILLS)
            for product in ("salesforce", "sales cloud", "service cloud",
                            "flow builder", "soql"):
                self.assertIn(product, out)


class OneAuthoritativeSplitter(unittest.TestCase):
    """identities() and the _finish path must never disagree about an atom."""

    # make_profile.split_compounds is gated on skill_concepts.enabled(), so
    # the comparison is only meaningful with the engine that runs it.
    KEYS = (FLAG, "SWEEP_PROFILE_ENGINE_VERSION")

    def setUp(self):
        self._before = {k: os.environ.get(k) for k in self.KEYS}
        os.environ[FLAG] = "1"
        os.environ["SWEEP_PROFILE_ENGINE_VERSION"] = "v2"
        skill_concepts._MARKET_TERMS = None

    def tearDown(self):
        for name, value in self._before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        skill_concepts._MARKET_TERMS = None

    def test_both_call_sites_produce_the_same_atoms(self):
        # identities() runs BEFORE search; make_profile.split_compounds runs
        # after it. Neither picks the registry — split_compound resolves it —
        # so they cannot drift. That drift is audit defect V3-C1.
        sys.path.insert(0, os.path.join(ROOT, "auto-apply"))
        import make_profile

        data = {"skill_weights": [{"term": t, "weight": 3}
                                  for t in FUNCTIONAL_SKILLS]}
        via_finish = [e["term"] for e in
                      make_profile.split_compounds(data, log=lambda *a: None)
                      ["skill_weights"]]
        via_identities = skill_concepts.atomize(FUNCTIONAL_SKILLS)
        self.assertEqual(sorted(set(via_finish)), sorted(set(via_identities)))

    def test_the_registry_is_resolved_by_the_splitter_not_the_caller(self):
        # Passing nothing must give the same answer as passing what the
        # module itself would have used.
        for term in FUNCTIONAL_SKILLS + BA_SKILLS:
            self.assertEqual(skill_concepts.split_compound(term),
                             skill_concepts.split_compound(
                                 term, skill_concepts.market_terms()), term)


class VocabularySource(unittest.TestCase):
    """The market registry is the frozen one, and it is the same frozen one
    local_search measures against."""

    def test_the_two_frozen_sources_agree(self):
        # market_terms() reads corpus_signal's committed frequency table
        # rather than gunzipping the title corpus. If these two ever diverge,
        # search and splitting would disagree about what a skill is.
        table = set(corpus_signal.frozen_frequencies())
        corpus = set(local_search.frozen_market().vocab)
        self.assertEqual(table, corpus)

    def test_it_is_loaded_once_and_is_immutable(self):
        before = os.environ.get(FLAG)
        os.environ[FLAG] = "1"
        skill_concepts._MARKET_TERMS = None
        try:
            first = skill_concepts.market_terms()
            self.assertIsInstance(first, frozenset)
            self.assertIs(first, skill_concepts.market_terms())
            self.assertGreater(len(first), 100)
        finally:
            if before is None:
                os.environ.pop(FLAG, None)
            else:
                os.environ[FLAG] = before
            skill_concepts._MARKET_TERMS = None


# A compact body carrying the evidence the real Functional résumé carries, in
# the sections it carries it in. The real document is not reproduced.
#
# The bullets deliberately never say "agile": neither do the real ones, which
# is why "Agile & SDLC" on the skills line was that concept's ONLY occurrence
# and the compound could take it whole. That is the shadowing case.
_BODY = """SRISHTI RAWAT

PROFESSIONAL SUMMARY
Functional Consultant delivering business-process and CRM implementations.

SKILLS
Agile & SDLC | Sales Cloud & Service Cloud | Reports & Dashboards

PROFESSIONAL EXPERIENCE
Functional Consultant | Dealermatix Technologies Pvt Ltd
• Built reports and dashboards for day-to-day operational use.
• Coordinated with developers and supported UAT and go-live sign-off.
• Configured CRM solutions in Sales Cloud and Service Cloud.

CERTIFICATIONS
• Salesforce Certified Administrator

TOOLS & TECHNOLOGIES
• Platforms: Salesforce (Sales Cloud, Service Cloud, Flow Builder)
• Other tools: SOQL, Microsoft Excel
"""


class Shadowing(unittest.TestCase):
    """An unsplit compound must not swallow its own atom's evidence.

    assess_all resolves overlapping occurrences to the LONGER concept, so
    while "agile & sdlc" is one concept it owns every "agile" on the page and
    the atom is left with nothing. Splitting removes the longer concept, which
    is why the fix has to help here rather than hurt.
    """

    WATCH = ("salesforce", "sales cloud", "service cloud", "flow builder",
             "agile", "sdlc", "reports", "dashboards")

    def setUp(self):
        self._before = os.environ.get(FLAG)
        skill_concepts._MARKET_TERMS = None

    def tearDown(self):
        if self._before is None:
            os.environ.pop(FLAG, None)
        else:
            os.environ[FLAG] = self._before
        skill_concepts._MARKET_TERMS = None

    def _tiers(self, terms):
        concepts = skill_concepts.from_weights({t: 3 for t in terms})
        return {r["display"].lower(): (r["tier"], r["weight"])
                for r in se.assess_all(concepts, _BODY)}

    def test_the_compound_shadows_its_atom_today(self):
        # The defect, asserted as a relationship rather than a tier name so
        # it stays true of any body carrying the same shape of evidence.
        alone = self._tiers(["agile"])["agile"]
        with_compound = self._tiers(["agile", "agile & sdlc"])["agile"]
        self.assertLess(se.rank(with_compound[0]), se.rank(alone[0]),
                        f"{alone} -> {with_compound}")
        self.assertLess(with_compound[1], alone[1])

    def test_splitting_removes_the_shadow(self):
        os.environ[FLAG] = "1"
        skill_concepts._MARKET_TERMS = None
        atoms = skill_concepts.identities(["agile & sdlc"])
        self.assertEqual(atoms, ["agile", "sdlc"])
        # The atom is now its own concept, so it keeps the evidence the
        # compound was taking. Same answer as if it had been alone.
        self.assertEqual(self._tiers(atoms)["agile"], self._tiers(["agile"])["agile"])

    def test_no_watched_atom_loses_evidence_to_the_fix(self):
        # The general property the brief asks for: atomisation is evidence
        # RECOVERY, so no concept may come out weaker than it went in.
        os.environ.pop(FLAG, None)
        skill_concepts._MARKET_TERMS = None
        before = self._tiers(skill_concepts.identities(FUNCTIONAL_SKILLS)
                             + list(self.WATCH))
        os.environ[FLAG] = "1"
        skill_concepts._MARKET_TERMS = None
        after = self._tiers(skill_concepts.identities(FUNCTIONAL_SKILLS)
                            + list(self.WATCH))
        for term in self.WATCH:
            self.assertIn(term, before, term)
            self.assertIn(term, after, term)
            self.assertGreaterEqual(after[term][1], before[term][1],
                                    f"{term} lost weight to atomisation: "
                                    f"{before[term]} -> {after[term]}")


class FunctionalConsultantSearch(unittest.TestCase):
    """The reported defect, against the FROZEN corpus."""

    def setUp(self):
        self._before = os.environ.get(FLAG)
        skill_concepts._MARKET_TERMS = None
        self.market = local_search.frozen_market()
        if not local_search.usable(self.market.rows):
            self.skipTest("no frozen corpus in this checkout")

    def tearDown(self):
        if self._before is None:
            os.environ.pop(FLAG, None)
        else:
            os.environ[FLAG] = self._before
        skill_concepts._MARKET_TERMS = None

    def _search(self):
        market = self.market
        own = set(local_search.clean_skills(
            skill_concepts.identities(FUNCTIONAL_SKILLS), market.vocab)[0])
        rows = local_search.matching_rows(own, market.rows, 2, market.vocab,
                                          market.total)
        found = local_search.keywords_for(own, market.rows, market.index,
                                          market.total, want=12,
                                          seniority=market.seniority)
        roles = local_search.validated(
            ["functional consultant"]
            + local_search.canonicalise(found, market.rows, market.seniority),
            market.rows, market.seniority)
        return own, rows, roles

    def test_flag_off_still_starves_the_search(self):
        os.environ.pop(FLAG, None)
        skill_concepts._MARKET_TERMS = None
        own, rows, roles = self._search()
        self.assertEqual(len(rows), 0)
        self.assertEqual(roles, ["functional consultant"])

    def test_flag_on_recovers_the_corpus_atoms(self):
        os.environ[FLAG] = "1"
        skill_concepts._MARKET_TERMS = None
        own, rows, roles = self._search()
        in_market = [t for t in own if t in self.market.vocab]
        self.assertGreaterEqual(len(in_market), 6, sorted(in_market))
        self.assertGreater(len(rows), 0)

    def test_flag_on_keeps_the_held_role_and_proposes_salesforce_ones(self):
        os.environ[FLAG] = "1"
        skill_concepts._MARKET_TERMS = None
        _own, _rows, roles = self._search()
        self.assertIn("functional consultant", roles)
        self.assertGreater(len(roles), 5, roles)
        salesforce = [r for r in roles if "salesforce" in r or "sf " in r]
        self.assertGreaterEqual(len(salesforce), 3, roles)


class AtomisationIsNotPermission(unittest.TestCase):
    """Recovered evidence still faces V3. The role gate is not relaxed."""

    def setUp(self):
        self._flags = {k: os.environ.get(k) for k in
                       ("SWEEP_ROLE_EVIDENCE", "SWEEP_ROLE_ATTACHMENT_GUARD",
                        "SWEEP_SEMANTIC_SCOPE", FLAG)}
        os.environ.update({"SWEEP_ROLE_EVIDENCE": "1",
                           "SWEEP_ROLE_ATTACHMENT_GUARD": "1",
                           "SWEEP_SEMANTIC_SCOPE": "1", FLAG: "1"})
        skill_concepts._MARKET_TERMS = None

    def tearDown(self):
        for name, value in self._flags.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        skill_concepts._MARKET_TERMS = None

    def test_salesforce_engineer_is_still_rejected(self):
        # Platform evidence exists; substantive development ownership does
        # not. Atomising the skills line must not change that answer.
        record = role_evidence.build(
            _BODY, {"titles": ["Functional Consultant"],
                    "employment_titles": ["Functional Consultant"],
                    "target_field": "functional consulting"},
            [{"title": "Functional Consultant"}])
        self.assertIn("salesforce", record["platforms"])
        kept, rejected = role_evidence.filter_queries(
            record, ["functional consultant", "salesforce engineer"])
        self.assertNotIn("salesforce engineer", kept)
        self.assertEqual([r["query"] for r in rejected], ["salesforce engineer"])
        self.assertIn("development", rejected[0]["missing_work_modes"])


class SectionHeadings(unittest.TestCase):
    """Fix 2. Every label a real résumé uses opens the section it names."""

    def test_skills_synonyms(self):
        for label in ("SKILLS", "CORE COMPETENCIES", "TECHNICAL SKILLS",
                      "KEY SKILLS", "PROFESSIONAL SKILLS", "TECHNOLOGIES",
                      # newly recognised
                      "TOOLS & TECHNOLOGIES", "TOOLS AND TECHNOLOGIES",
                      "TOOLS & PLATFORMS", "EXPERTISE", "AREAS OF EXPERTISE",
                      "AREA OF EXPERTISE", "SKILL SET", "SKILLS SET"):
            self.assertEqual(se._heading(label), se.SKILLS, label)

    def test_summary_synonyms(self):
        for label in ("PROFESSIONAL SUMMARY", "SUMMARY", "PROFILE", "ABOUT",
                      "OBJECTIVE", "CAREER OBJECTIVE"):
            self.assertEqual(se._heading(label), se.SUMMARY, label)

    def test_work_synonyms(self):
        for label in ("PROFESSIONAL EXPERIENCE", "WORK EXPERIENCE",
                      "EMPLOYMENT", "EXPERIENCE", "WORK HISTORY",
                      "CAREER HISTORY", "EMPLOYMENT HISTORY"):
            self.assertEqual(se._heading(label), se.WORK, label)

    def test_the_new_rows_did_not_swallow_their_neighbours(self):
        self.assertEqual(se._heading("EDUCATION"), se.EDUCATION)
        self.assertEqual(se._heading("CERTIFICATIONS"), se.CERTIFICATION)
        self.assertEqual(se._heading("SELECTED PROJECTS"), se.PROJECT)
        self.assertEqual(se._heading("RELEVANT COURSEWORK"), se.EDUCATION)
        # Still not headings: a stack line has content after its colon.
        self.assertIsNone(se._heading("- Technologies: Redis, MongoDB"))
        self.assertIsNone(se._heading("Tools: Jira, Confluence"))


class HeadingEquivalence(unittest.TestCase):
    """A synonym must not change what the document says."""

    WATCH = ("salesforce", "sales cloud", "service cloud", "flow builder",
             "soql", "reports", "dashboards", "uat", "go-live")

    def _tiers(self, text):
        concepts = skill_concepts.from_weights({t: 3 for t in self.WATCH})
        return {r["display"].lower(): (r["tier"], r["weight"])
                for r in se.assess_all(concepts, text)}

    def _section_of(self, text, needle):
        return se.section_at(text.lower().index(needle.lower()),
                             se.sections(text))[0]

    def test_tools_and_technologies_equals_technical_skills(self):
        a = _BODY
        b = _BODY.replace("TOOLS & TECHNOLOGIES", "TECHNICAL SKILLS")
        self.assertEqual(self._section_of(a, "soql"), se.SKILLS)
        self.assertEqual(self._section_of(a, "soql"),
                         self._section_of(b, "soql"))
        self.assertEqual(self._tiers(a), self._tiers(b))

    def test_career_history_equals_professional_experience(self):
        a = _BODY
        b = _BODY.replace("PROFESSIONAL EXPERIENCE", "CAREER HISTORY")
        self.assertEqual(self._section_of(a, "go-live"), se.WORK)
        self.assertEqual(self._section_of(a, "go-live"),
                         self._section_of(b, "go-live"))
        self.assertEqual(self._tiers(a), self._tiers(b))

    def test_every_recognised_synonym_is_equivalent(self):
        base = self._tiers(_BODY)
        for old, new in (("SKILLS", "CORE COMPETENCIES"),
                         ("SKILLS", "EXPERTISE"),
                         ("SKILLS", "AREAS OF EXPERTISE"),
                         ("SKILLS", "SKILL SET"),
                         ("TOOLS & TECHNOLOGIES", "TECHNICAL SKILLS"),
                         ("TOOLS & TECHNOLOGIES", "TOOLS AND TECHNOLOGIES"),
                         ("PROFESSIONAL EXPERIENCE", "WORK EXPERIENCE"),
                         ("PROFESSIONAL EXPERIENCE", "EMPLOYMENT"),
                         ("PROFESSIONAL EXPERIENCE", "CAREER HISTORY"),
                         ("PROFESSIONAL EXPERIENCE", "EMPLOYMENT HISTORY"),
                         ("PROFESSIONAL SUMMARY", "SUMMARY"),
                         ("PROFESSIONAL SUMMARY", "PROFILE"),
                         ("PROFESSIONAL SUMMARY", "CAREER OBJECTIVE")):
            self.assertEqual(self._tiers(_BODY.replace(old, new)), base,
                             f"{old} -> {new}")

    def test_work_evidence_reaches_core_under_every_work_synonym(self):
        for label in ("PROFESSIONAL EXPERIENCE", "WORK EXPERIENCE",
                      "EMPLOYMENT", "CAREER HISTORY", "EMPLOYMENT HISTORY"):
            tiers = self._tiers(_BODY.replace("PROFESSIONAL EXPERIENCE", label))
            self.assertEqual(tiers["go-live"][0], se.CORE, label)
            self.assertEqual(tiers["reports"][0], se.CORE, label)


if __name__ == "__main__":
    unittest.main()
