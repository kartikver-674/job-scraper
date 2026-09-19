"""The free-source gate asks about this candidate, not about software.

V3 Step 4, fixing audit defect V3-C4. `make_profile._title_gate` used to union
every candidate's hints with a 79-entry generic software floor, so on the
development personas 52% could not see their own profession and 100% could see
somebody else's. The gate now answers "could this title belong to this person".

What these tests defend, in order of how badly each would hurt:

  * nobody is starved. Every fallback is tested, and a profession the sixteen
    coarse families do not represent is still carried by its own held titles
  * with the flag off, the rendered gate is byte-identical to the legacy union
  * a candidate does not admit a family merely because the global config
    mentions it, and does not lose one they genuinely have
  * `target_field` can widen and can never narrow
  * the gate does not move when only formatting moves

Every fixture is written here. No holdout candidate, label or result is used.
"""

import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_APPLY = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(AUTO_APPLY)
for path in (REPO_ROOT, AUTO_APPLY):
    if path not in sys.path:
        sys.path.insert(0, path)

import config                 # noqa: E402
import make_profile           # noqa: E402
import title_gate as tg       # noqa: E402

FLOOR = list(config.ATS_TITLE_HINTS)


def profile(hints=(), held=(), titles=(), target="", supports=None,
            title_families=None):
    return {
        "title_hints": list(hints),
        "title_exclude": [],
        "role_signals": {"employment_titles": list(held), "titles": list(titles),
                         "target_field": target},
        "role_evidence": {"supports": dict(supports or {}),
                          "title_families": dict(title_families or {}),
                          "stated_target": {"family": None, "grounded": False}},
    }


def gate(data):
    hints, _record = tg.build(data, FLOOR)
    return hints


def admits(data, title):
    return tg.admits(gate(data), title)


# ---------------------------------------------------------------- the shapes

DESIGNER = profile(hints=["graphic design", "adobe", "brand"],
                   held=["Senior Graphic Designer", "Brand Designer"],
                   titles=["Graphic Designer"],
                   supports={"design": "strong"},
                   title_families={"design": ["Graphic Designer"]})

FINANCE = profile(hints=["financial analysis", "python", "sql"],
                  held=["Finance Analyst"], titles=["Financial Analyst"],
                  supports={"finance": "strong", "data_analytics": "weak"},
                  title_families={"finance": ["Finance Analyst"]})

SELLER = profile(hints=["account management", "salesforce", "crm"],
                 held=["Account Executive"], titles=["Account Executive"],
                 supports={"sales": "strong"},
                 title_families={"sales": ["Account Executive"]})

PLATFORM_BA = profile(hints=["salesforce business analyst", "salesforce", "apex"],
                      held=["Business Analyst"],
                      titles=["Salesforce Business Analyst"],
                      supports={"business_analysis": "strong",
                                "functional_consulting": "strong"},
                      title_families={"business_analysis": ["Business Analyst"]})

PLATFORM_DEV = profile(hints=["salesforce developer", "apex", "lwc"],
                       held=["Salesforce Developer"],
                       titles=["Salesforce Developer"],
                       supports={"software_engineering": "strong"},
                       title_families={"software_engineering":
                                       ["Salesforce Developer"]})

SOFTWARE = profile(hints=["backend engineer", "python", "microservices"],
                   held=["Backend Engineer"], titles=["Software Engineer"],
                   supports={"software_engineering": "strong"},
                   title_families={"software_engineering": ["Backend Engineer"]})

IT_SUPPORT = profile(hints=["service desk", "active directory"],
                     held=["IT Support Analyst"],
                     titles=["Systems Administrator"],
                     supports={"support": "strong",
                               "it_administration": "strong"},
                     title_families={"it_administration":
                                     ["Systems Administrator"]})

RECRUITER = profile(hints=["talent acquisition", "ats", "greenhouse"],
                    held=["Technical Recruiter"], titles=["Recruiter"],
                    supports={"hr_recruiting": "strong"},
                    title_families={"hr_recruiting": ["Technical Recruiter"]})

TEACHER = profile(hints=[], held=["High School Teacher"], titles=["Teacher"],
                  supports={}, title_families={})

THIN = profile(hints=[], held=[], titles=[], supports={}, title_families={})


class NobodyIsStarved(unittest.TestCase):
    """J, and the reason every other test is allowed to be strict."""

    def test_a_profession_outside_the_taxonomy_still_sees_itself(self):
        self.assertTrue(admits(TEACHER, "High School Teacher"))
        self.assertTrue(admits(TEACHER, "Teacher - Year 9 Science"))
        self.assertFalse(admits(TEACHER, "Backend Developer"))

    def test_a_long_title_is_matched_from_either_end(self):
        ea = profile(held=["Executive Assistant to the CEO"])
        self.assertTrue(admits(ea, "Executive Assistant"),
                        "the head of the title was lost")
        self.assertTrue(admits(ea, "Senior Executive Assistant"))

    def test_an_empty_record_falls_back_and_says_so(self):
        hints, record = tg.build(THIN, FLOOR)
        self.assertEqual(hints, sorted({tg.normalize(h) for h in FLOOR}))
        self.assertTrue(record["global_floor_used"])
        self.assertTrue(record["fallback"])
        self.assertFalse(record["candidate_specific"])

    def test_hints_without_titles_or_families_use_the_hints_alone(self):
        only = profile(hints=["logistics coordinator", "warehouse"])
        hints, record = tg.build(only, FLOOR)
        self.assertFalse(record["global_floor_used"])
        self.assertTrue(record["fallback"])
        self.assertTrue(tg.admits(hints, "Logistics Coordinator"))

    def test_no_fixture_ever_produces_an_empty_gate(self):
        for name, data in (("designer", DESIGNER), ("finance", FINANCE),
                           ("seller", SELLER), ("ba", PLATFORM_BA),
                           ("dev", PLATFORM_DEV), ("software", SOFTWARE),
                           ("it", IT_SUPPORT), ("recruiter", RECRUITER),
                           ("teacher", TEACHER), ("thin", THIN)):
            self.assertTrue(gate(data), name)


class TheGlobalFloorIsGone(unittest.TestCase):
    """A, B, C, D, G, H — nobody admits software just because config does."""

    def test_a_designer_does_not_admit_software(self):
        self.assertTrue(admits(DESIGNER, "Graphic Designer"))
        self.assertTrue(admits(DESIGNER, "Senior Brand Designer"))
        for title in ("Frontend Developer", "Backend Engineer",
                      "Software Engineer", "Machine Learning Engineer"):
            self.assertFalse(admits(DESIGNER, title), title)

    def test_b_a_finance_analyst_using_python_is_not_a_developer(self):
        self.assertTrue(admits(FINANCE, "Financial Analyst"))
        self.assertTrue(admits(FINANCE, "FP&A Analyst"))
        for title in ("Software Developer", "Backend Engineer"):
            self.assertFalse(admits(FINANCE, title), title)

    def test_c_a_salesperson_with_a_crm_is_not_a_developer(self):
        self.assertTrue(admits(SELLER, "Account Executive"))
        self.assertTrue(admits(SELLER, "Business Development Manager"))
        self.assertFalse(admits(SELLER, "Salesforce Developer"))
        self.assertFalse(admits(SELLER, "Backend Engineer"))

    def test_c2_a_bare_sales_fragment_would_have_matched_salesforce(self):
        """The substring trap that made every salesperson a Salesforce dev."""
        self.assertNotIn("sales", tg.FAMILY_TITLES["sales"])
        self.assertIn("sales", "salesforce developer")

    def test_d_a_platform_analyst_is_not_a_platform_developer(self):
        self.assertTrue(admits(PLATFORM_BA, "Salesforce Business Analyst"))
        self.assertTrue(admits(PLATFORM_BA, "Business Analyst"))
        self.assertTrue(admits(PLATFORM_BA, "Functional Consultant"))
        self.assertFalse(admits(PLATFORM_BA, "Salesforce Developer"))

    def test_g_an_it_candidate_needs_no_software_identity(self):
        self.assertTrue(admits(IT_SUPPORT, "Systems Administrator"))
        self.assertTrue(admits(IT_SUPPORT, "IT Support Specialist"))
        self.assertTrue(admits(IT_SUPPORT, "Service Desk Analyst"))
        self.assertFalse(admits(IT_SUPPORT, "Backend Developer"))

    def test_h_a_recruiter_using_an_ats_is_not_an_engineer(self):
        self.assertTrue(admits(RECRUITER, "Technical Recruiter"))
        self.assertTrue(admits(RECRUITER, "Talent Acquisition Partner"))
        for title in ("Software Engineer", "Frontend Developer"):
            self.assertFalse(admits(RECRUITER, title), title)

    def test_the_floor_itself_is_not_in_a_candidate_specific_gate(self):
        hints, record = tg.build(DESIGNER, FLOOR)
        self.assertFalse(record["global_floor_used"])
        self.assertFalse(set(hints) & (set(FLOOR) - set(hints[:0])) - set(hints),
                         "sanity")
        for entry in ("member of technical staff", "sde", "tech lead"):
            self.assertNotIn(entry, hints)


class SoftwareCandidatesKeepTheirs(unittest.TestCase):
    """E and F — the direction that breaks people if it goes wrong."""

    def test_e_a_platform_developer_still_admits_platform_developer_jobs(self):
        self.assertTrue(admits(PLATFORM_DEV, "Salesforce Developer"))
        self.assertTrue(admits(PLATFORM_DEV, "Senior Salesforce Developer"))

    def test_f_an_engineer_admits_ordinary_engineering_titles(self):
        for title in ("Backend Engineer", "Software Engineer",
                      "Full Stack Developer", "Frontend Developer",
                      "Senior Python Developer"):
            self.assertTrue(admits(SOFTWARE, title), title)

    def test_weak_support_alone_does_not_admit_a_whole_family(self):
        """One passing mention must not re-open the door the gate just shut."""
        weak = profile(held=["Business Analyst"], titles=["Business Analyst"],
                       supports={"business_analysis": "strong",
                                 "software_engineering": "weak"},
                       title_families={"business_analysis": ["Business Analyst"]})
        self.assertTrue(admits(weak, "Business Analyst"))
        self.assertFalse(admits(weak, "Backend Engineer"))

    def test_a_held_title_admits_its_family_even_without_strong_modes(self):
        titled = profile(held=["Software Engineer"], titles=["Software Engineer"],
                         supports={"software_engineering": "weak"},
                         title_families={"software_engineering":
                                         ["Software Engineer"]})
        self.assertTrue(admits(titled, "Backend Engineer"))


class CorpusHintsAreFiltered(unittest.TestCase):
    """The side door: the candidate's own hints come from a 63%-software corpus."""

    def test_a_hint_naming_an_unsupported_family_is_dropped(self):
        ba = copy.deepcopy(PLATFORM_BA)
        ba["title_hints"].append("salesforce developer")
        hints, record = tg.build(ba, FLOOR)
        self.assertIn("salesforce developer", record["own_hints_dropped"])
        self.assertFalse(tg.admits(hints, "Salesforce Developer"))

    def test_a_bare_platform_name_is_dropped(self):
        """A platform is not a role, one layer further down than Step 3."""
        hints, record = tg.build(PLATFORM_BA, FLOOR)
        self.assertIn("salesforce", record["own_hints_dropped"])
        self.assertFalse(tg.admits(hints, "Salesforce Developer"))
        self.assertTrue(tg.admits(hints, "Salesforce Business Analyst"))

    def test_a_hint_naming_a_supported_family_is_kept(self):
        hints, record = tg.build(PLATFORM_DEV, FLOOR)
        self.assertNotIn("salesforce developer", record["own_hints_dropped"])
        self.assertTrue(tg.admits(hints, "Salesforce Developer"))

    def test_a_hint_the_classifier_cannot_place_is_kept(self):
        """Unknown is not unrelated."""
        odd = profile(hints=["veterinary nurse"], held=["Veterinary Nurse"])
        hints, record = tg.build(odd, FLOOR)
        self.assertEqual(record["own_hints_dropped"], [])
        self.assertTrue(tg.admits(hints, "Veterinary Nurse"))


class TargetFieldOnlyWidens(unittest.TestCase):

    def test_an_uncorroborated_target_contributes_nothing(self):
        switcher = profile(held=["Store Manager"], titles=["Store Manager"],
                           target="software engineering",
                           supports={"management": "strong"},
                           title_families={"management": ["Store Manager"]})
        hints, record = tg.build(switcher, FLOOR)
        self.assertIn("uncorroborated", record["target_field_use"])
        self.assertFalse(tg.admits(hints, "Backend Engineer"))
        self.assertTrue(tg.admits(hints, "Store Manager"))

    def test_a_corroborated_target_widens(self):
        data = profile(held=["Junior Developer"], titles=["Developer"],
                       target="software engineering",
                       supports={"software_engineering": "strong"},
                       title_families={"software_engineering":
                                       ["Junior Developer"]})
        data["role_evidence"]["stated_target"] = {"family": "software_engineering",
                                                  "grounded": False}
        hints, record = tg.build(data, FLOOR)
        self.assertIn("corroborated", record["target_field_use"])
        self.assertTrue(tg.admits(hints, "Backend Engineer"))

    def test_a_target_never_removes_a_held_title(self):
        data = profile(held=["Graphic Designer"], titles=["Graphic Designer"],
                       target="software engineering",
                       supports={"design": "strong"},
                       title_families={"design": ["Graphic Designer"]})
        self.assertTrue(admits(data, "Graphic Designer"))


class Invariance(unittest.TestCase):
    """§15. Formatting moves; the gate does not."""

    def variants(self, data):
        out = {"plain": data}
        upper = copy.deepcopy(data)
        upper["role_signals"]["employment_titles"] = [
            t.upper() for t in data["role_signals"]["employment_titles"]]
        out["upper"] = upper
        hyphen = copy.deepcopy(data)
        hyphen["role_signals"]["employment_titles"] = [
            t.replace(" ", "-") for t in data["role_signals"]["employment_titles"]]
        out["hyphenated"] = hyphen
        spaced = copy.deepcopy(data)
        spaced["role_signals"]["employment_titles"] = [
            t.replace(" ", "  ") for t in data["role_signals"]["employment_titles"]]
        out["double_spaced"] = spaced
        wrapped = copy.deepcopy(data)
        wrapped["role_signals"]["employment_titles"] = [
            t.replace(" ", "\n", 1) for t in data["role_signals"]["employment_titles"]]
        out["wrapped"] = wrapped
        dup = copy.deepcopy(data)
        dup["title_hints"] = list(data["title_hints"]) * 3
        dup["role_signals"]["employment_titles"] = (
            list(data["role_signals"]["employment_titles"]) * 2)
        out["duplicated"] = dup
        reordered = copy.deepcopy(data)
        reordered["title_hints"] = list(reversed(data["title_hints"]))
        out["reordered"] = reordered
        return out

    def check(self, data, label):
        base = set(gate(data))
        for name, variant in self.variants(data).items():
            self.assertEqual(set(gate(variant)), base, f"{label}: {name} moved")

    def test_the_gate_is_stable_for_a_designer(self):
        self.check(DESIGNER, "designer")

    def test_the_gate_is_stable_for_a_platform_analyst(self):
        self.check(PLATFORM_BA, "platform ba")

    def test_the_gate_is_stable_for_an_engineer(self):
        self.check(SOFTWARE, "software")

    def test_the_same_input_twice_gives_the_same_gate(self):
        self.assertEqual(gate(DESIGNER), gate(DESIGNER))


class Explainability(unittest.TestCase):
    """§14. A decision names its evidence, and there is no score."""

    def test_a_rejection_explains_itself(self):
        hints, record = tg.build(PLATFORM_BA, FLOOR)
        got = tg.explain("Salesforce Developer", hints, record)
        self.assertEqual(got["decision"], "reject")
        self.assertIn("global ATS floor is not part", got["reason"])
        self.assertIn("business_analysis", got["candidate_supported_families"])
        self.assertEqual(got["candidate_title_hints_matched"], [])
        self.assertFalse(got["fallback_used"])

    def test_an_admission_names_the_evidence_that_carried_it(self):
        hints, record = tg.build(DESIGNER, FLOOR)
        got = tg.explain("Senior Brand Designer", hints, record)
        self.assertEqual(got["decision"], "admit")
        self.assertTrue(got["candidate_title_hints_matched"])
        self.assertIn("from", got["reason"])

    def test_the_record_carries_provenance_for_every_hint(self):
        hints, record = tg.build(DESIGNER, FLOOR)
        for hint in hints:
            self.assertIn(hint, record["provenance"])


class OffByDefault(unittest.TestCase):

    def setUp(self):
        self.saved = os.environ.get(tg.FLAG)
        os.environ.pop(tg.FLAG, None)

    def tearDown(self):
        os.environ.pop(tg.FLAG, None)
        if self.saved is not None:
            os.environ[tg.FLAG] = self.saved

    def test_the_gate_is_off_unless_asked_for(self):
        self.assertFalse(tg.enabled())

    def test_with_the_flag_off_the_rendered_gate_is_the_legacy_union(self):
        data = {"title_hints": ["data analyst", "sql"], "title_exclude": []}
        hints, excludes, record = make_profile._title_gate(data, config)
        self.assertIsNone(record)
        self.assertEqual(hints,
                         sorted({"data analyst", "sql"} | set(FLOOR)))
        self.assertEqual(excludes, [])

    def test_with_the_flag_on_the_floor_is_not_unioned(self):
        os.environ[tg.FLAG] = "1"
        hints, _excludes, record = make_profile._title_gate(
            copy.deepcopy(DESIGNER), config)
        self.assertIsNotNone(record)
        self.assertNotIn("member of technical staff", hints)

    def test_excludes_still_win_under_both_flags(self):
        data = copy.deepcopy(DESIGNER)
        data["title_exclude"] = ["graphic design"]
        for flag in ("0", "1"):
            os.environ[tg.FLAG] = flag
            hints, excludes, _r = make_profile._title_gate(data, config)
            self.assertNotIn("graphic design", hints, flag)
            self.assertEqual(excludes, ["graphic design"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
