"""Does the same career, packaged two ways, produce the same profile?

The diagnostic harness for docs/profile-presentation-stability-audit.md. Two
real résumés for one person — Srishti Rawat, presented once as a Functional
Consultant and once as a Salesforce BA — produced 1 searchable role against 9,
and Salesforce at weight 1 against 4.

The audit traced that to two DETERMINISTIC layers, and those are what is
locked here. The model's own reading of the document is not testable without
it, so the end-to-end variant sweep lives in the audit's numbers rather than
in this file.

  1. skill_concepts.split_compound refuses to atomise an &-joined skill unless
     EVERY half is in LOOKUP. The market knows `sales cloud` and `service
     cloud`; LOOKUP does not. So "Sales Cloud & Service Cloud" reaches
     local_search as one string that matches zero listings, the corpus path
     retrieves nothing, and role_keywords collapses to the held job title.

  2. skill_evidence._heading does not recognise "TOOLS & TECHNOLOGIES" or
     "CAREER HISTORY". The first swallows a tools block into whichever section
     preceded it; the second means no WORK section ever opens, so nothing in
     the document can reach CORE.

These tests assert the defects AS THEY SHIP. Each one names the behaviour that
should replace it, so fixing the engine fails the test loudly rather than
silently passing a stale expectation.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import skill_concepts                                          # noqa: E402
import skill_evidence as se                                    # noqa: E402

# The fourteen skills the local model read from the Functional Consultant
# résumé's SKILLS line, verbatim.
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


class CompoundAtomisation(unittest.TestCase):
    """Defect 1. A compound the market could read stays unreadable."""

    def test_ampersand_compound_of_two_real_products_is_not_split(self):
        # BOTH halves are terms the corpus indexes. Neither is in LOOKUP, so
        # the string survives whole and the market never sees either one.
        self.assertEqual(
            skill_concepts.split_compound("sales cloud & service cloud"),
            ["sales cloud & service cloud"])

    def test_a_known_half_does_not_rescue_the_compound(self):
        # `agile` IS in LOOKUP; `sdlc` is not. The all-or-nothing rule keeps
        # the pair whole, so the known half is lost too.
        self.assertIn("agile", skill_concepts.LOOKUP)
        self.assertEqual(skill_concepts.split_compound("agile & sdlc"),
                         ["agile & sdlc"])

    def test_identities_leaves_every_functional_skill_compound(self):
        # V3 Step 2 resolves identity before search precisely so the corpus and
        # the profile agree. It cannot help here: nothing splits.
        out = skill_concepts.identities(FUNCTIONAL_SKILLS)
        self.assertEqual(out, list(FUNCTIONAL_SKILLS))

    def test_the_ba_list_needs_no_splitting_to_be_searchable(self):
        # The contrast. Same person, same employer, atomic product names.
        out = skill_concepts.identities(BA_SKILLS)
        for product in ("salesforce", "sales cloud", "service cloud",
                        "flow builder", "soql"):
            self.assertIn(product, out)


class SectionHeadings(unittest.TestCase):
    """Defect 2. Two headings real résumés use open no section at all."""

    def test_recognised_skills_synonyms(self):
        for label in ("SKILLS", "CORE COMPETENCIES", "TECHNICAL SKILLS",
                      "KEY SKILLS", "PROFESSIONAL SKILLS"):
            self.assertEqual(se._heading(label), se.SKILLS, label)

    def test_recognised_summary_synonyms(self):
        for label in ("PROFESSIONAL SUMMARY", "SUMMARY", "PROFILE", "ABOUT",
                      "OBJECTIVE"):
            self.assertEqual(se._heading(label), se.SUMMARY, label)

    def test_recognised_work_synonyms(self):
        for label in ("PROFESSIONAL EXPERIENCE", "WORK EXPERIENCE",
                      "EMPLOYMENT", "EXPERIENCE", "WORK HISTORY"):
            self.assertEqual(se._heading(label), se.WORK, label)

    def test_unrecognised_headings_real_resumes_use(self):
        # Each should be the kind named beside it. Delete the line from this
        # test when the table learns the heading.
        for label, should_be in (("TOOLS & TECHNOLOGIES", se.SKILLS),
                                 ("EXPERTISE", se.SKILLS),
                                 ("AREAS OF EXPERTISE", se.SKILLS),
                                 ("SKILL SET", se.SKILLS),
                                 ("CAREER HISTORY", se.WORK),
                                 ("EMPLOYMENT HISTORY", se.WORK),
                                 ("CAREER OBJECTIVE", se.SUMMARY)):
            self.assertIsNone(se._heading(label),
                              f"{label} is now recognised — it should be "
                              f"{should_be}; update the audit and move this "
                              f"line into the recognised list")


# The Functional Consultant résumé's tail, from CERTIFICATIONS down. Enough to
# show the swallow; the full document is not needed and is not reproduced.
_TAIL = """CERTIFICATIONS
• Salesforce Certified Administrator

TOOLS & TECHNOLOGIES
• Platforms & configuration: Salesforce (Sales Cloud, Service Cloud, Flow Builder)
• Other tools: SOQL, Microsoft Excel
"""


class SwallowedToolsBlock(unittest.TestCase):
    """Defect 2, measured: an unrecognised heading costs one evidence band."""

    def _section_of(self, text, needle):
        spans = se.sections(text)
        return se.section_at(text.lower().index(needle.lower()), spans)[0]

    def test_tools_block_is_read_as_certification_text(self):
        self.assertEqual(self._section_of(_TAIL, "sales cloud"),
                         se.CERTIFICATION)

    def test_naming_the_block_technical_skills_moves_it_to_skills(self):
        fixed = _TAIL.replace("TOOLS & TECHNOLOGIES", "TECHNICAL SKILLS")
        self.assertEqual(self._section_of(fixed, "sales cloud"), se.SKILLS)

    def test_the_band_that_costs(self):
        # CERTIFICATION tiers BACKGROUND (weight band 1-2); SKILLS tiers
        # SUPPORTING (2-3). One band, from the heading alone.
        concepts = skill_concepts.from_weights({"sales cloud": 3})
        shipped = se.assess_all(concepts, _TAIL)[0]
        fixed = se.assess_all(
            concepts, _TAIL.replace("TOOLS & TECHNOLOGIES",
                                    "TECHNICAL SKILLS"))[0]
        self.assertEqual(shipped["tier"], se.BACKGROUND)
        self.assertEqual(fixed["tier"], se.SUPPORTING)
        self.assertEqual(fixed["weight"] - shipped["weight"], 1)


class WorkSectionIsLoadBearing(unittest.TestCase):
    """CAREER HISTORY opens no work section, so nothing can reach CORE."""

    BODY = ("{heading}\nSalesforce Functional Consultant | Dealermatix\n"
            "• Configured Salesforce Sales Cloud and Service Cloud solutions.\n")

    def _tier(self, heading):
        text = self.BODY.format(heading=heading)
        concepts = skill_concepts.from_weights({"sales cloud": 3})
        return se.assess_all(concepts, text)[0]

    def test_recognised_heading_reaches_core(self):
        self.assertEqual(self._tier("PROFESSIONAL EXPERIENCE")["tier"], se.CORE)

    def test_career_history_loses_two_bands(self):
        good, bad = self._tier("PROFESSIONAL EXPERIENCE"), self._tier("CAREER HISTORY")
        self.assertEqual(good["tier"], se.CORE)
        self.assertNotEqual(bad["tier"], se.CORE)
        self.assertEqual(good["weight"] - bad["weight"], 2)


if __name__ == "__main__":
    unittest.main()
