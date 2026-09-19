"""A globally undesirable title term is not undesirable for everyone.

V3 Step 5, fixing audit defect V3-C8. `config.SCORING` strips manager,
architect, director and lead from every title everywhere, and a title reduced
below two words is discarded, so an Engineering Manager's own job title produced
no search at all. A query is restored only when the candidate has grounded
evidence for that exact role.

What these tests defend, in order of how badly each would hurt:

  * no upward role inflation. A Software Engineer never gains Engineering
    Manager, a Project Coordinator never gains Program Manager, and family-level
    support never restores anything
  * `target_field` alone never bypasses a drop
  * with the flag off, behaviour is byte-identical
  * a grounded held title really does come back
  * level words and the too-junior block are never exempted

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

import hard_drop as hd        # noqa: E402
import local_extract as le    # noqa: E402
import local_profile          # noqa: E402
import local_search           # noqa: E402
import skill_concepts as sc   # noqa: E402


class Market:
    """Just enough of a Market for the cost guard."""

    def __init__(self, titles=None):
        self.titles = titles if titles is not None else CORPUS


CORPUS = ([("engineering manager", 5)] * 30 + [("solutions architect", 5)] * 20
          + [("software engineer", 5)] * 60 + [("program manager", 5)] * 20
          + [("technical lead", 5)] * 15 + [("director of engineering", 5)] * 10
          + [("project coordinator", 5)] * 10 + [("backend engineer", 5)] * 40)
MARKET = Market()


def person(held=(), titles=(), target="", modes=None, queries=()):
    return {
        "role_keywords": list(queries),
        "role_signals": {"employment_titles": list(held), "titles": list(titles),
                         "target_field": target},
        "role_evidence": {"work_modes": {m: {"strength": s}
                                         for m, s in (modes or {}).items()}},
    }


def restored(**kw):
    got, _record = hd.restore(person(**kw), MARKET)
    return got


class TheDefectIsReal(unittest.TestCase):
    """The behaviour being fixed, asserted against the live search code."""

    def test_a_managers_own_title_produces_no_query(self):
        hard, soft = local_search.seniority_lists()
        seniority = tuple(hard) + tuple(soft)
        for title in ("Engineering Manager", "Solutions Architect",
                      "Program Manager", "Technical Lead"):
            got = local_search.from_resume(
                {"employment": [{"title": title, "start": "2020-01",
                                 "end": "2024-01", "company": "X"}]}, seniority)
            self.assertEqual(got, [], f"{title} unexpectedly survived")

    def test_an_ordinary_title_is_unaffected(self):
        hard, soft = local_search.seniority_lists()
        got = local_search.from_resume(
            {"employment": [{"title": "Senior Backend Engineer",
                             "start": "2020-01", "end": "2024-01",
                             "company": "X"}]}, tuple(hard) + tuple(soft))
        self.assertEqual(got, ["backend engineer"])


class GroundedTitlesComeBack(unittest.TestCase):
    """A, C, E, G, I."""

    def test_a_an_engineering_manager_keeps_the_manager_query(self):
        self.assertEqual(restored(held=["Engineering Manager"]),
                         ["engineering manager"])

    def test_c_a_solutions_architect_keeps_the_architect_query(self):
        self.assertEqual(restored(held=["Solutions Architect"]),
                         ["solutions architect"])

    def test_e_a_program_manager_keeps_the_programme_query(self):
        self.assertEqual(restored(held=["Program Manager"]), ["program manager"])

    def test_g_a_director_title_is_restored(self):
        self.assertEqual(restored(held=["Director of Engineering"]),
                         ["director of engineering"])

    def test_i_a_grounded_lead_title_is_restored(self):
        self.assertEqual(restored(held=["Technical Lead"]), ["technical lead"])

    def test_a_grounded_extracted_title_counts_too(self):
        self.assertEqual(restored(titles=["Engineering Manager"]),
                         ["engineering manager"])

    def test_a_title_already_present_is_not_duplicated(self):
        self.assertEqual(restored(held=["Engineering Manager"],
                                  queries=["engineering manager"]), [])


class NoUpwardInflation(unittest.TestCase):
    """B, D, F, H, J — the direction that would make this change harmful."""

    def test_b_an_engineer_with_no_management_evidence_gains_nothing(self):
        self.assertEqual(restored(held=["Software Engineer"]), [])
        self.assertEqual(restored(held=["Software Engineer"],
                                  modes={"development": "strong"}), [])

    def test_d_architecture_tools_do_not_restore_an_architect_role(self):
        self.assertEqual(
            restored(held=["Backend Engineer"],
                     modes={"development": "strong"}), [])

    def test_f_a_project_coordinator_does_not_become_a_programme_manager(self):
        self.assertEqual(restored(held=["Project Coordinator"],
                                  modes={"delivery": "strong"}), [])

    def test_h_working_with_a_director_is_not_being_one(self):
        """Only a held TITLE grounds an exemption; prose never reaches this."""
        self.assertEqual(restored(held=["Software Engineer"],
                                  modes={"delivery": "strong"}), [])

    def test_j_an_uncorroborated_target_is_not_enough(self):
        got, record = hd.restore(person(held=["Software Engineer"],
                                        target="Engineering Manager"), MARKET)
        self.assertEqual(got, [])
        refused = [d for d in record["decisions"]
                   if d["source"] == "stated_target"]
        self.assertTrue(refused)
        self.assertIn("ungrounded", refused[0]["reason"])

    def test_family_support_alone_restores_nothing(self):
        data = person(held=["Software Engineer"], modes={"development": "strong"})
        data["role_evidence"]["supports"] = {"software_engineering": "strong",
                                             "management": "strong"}
        self.assertEqual(hd.restore(data, MARKET)[0], [])

    def test_an_administrator_does_not_become_a_director(self):
        self.assertEqual(restored(held=["Systems Administrator"],
                                  modes={"administration": "strong"}), [])


class CareerTransitions(unittest.TestCase):
    """K — a target plus real corroborating work evidence."""

    def test_k_a_target_corroborated_by_management_work_is_restored(self):
        self.assertEqual(
            restored(held=["Team Coordinator"], target="Engineering Manager",
                     modes={"management": "strong"}),
            ["engineering manager"])

    def test_weak_management_evidence_is_not_corroboration(self):
        self.assertEqual(
            restored(target="Engineering Manager", modes={"management": "weak"}),
            [])

    def test_the_wrong_work_mode_is_not_corroboration(self):
        self.assertEqual(
            restored(target="Engineering Manager", modes={"delivery": "strong"}),
            [])

    def test_an_architect_target_needs_development_work(self):
        self.assertEqual(
            restored(target="Solutions Architect",
                     modes={"management": "strong"}), [])
        self.assertEqual(
            restored(target="Solutions Architect",
                     modes={"development": "strong"}), ["solutions architect"])


class RoleIsNotLevel(unittest.TestCase):
    """§5. The configured list is three different things wearing one name."""

    def test_level_words_are_never_exemptible(self):
        for word in ("principal", "staff", "senior", "sr"):
            self.assertNotIn(word, hd.ROLE_TERMS)
            self.assertIn(word, hd.LEVEL_TERMS)

    def test_the_too_junior_block_is_never_exemptible(self):
        for word in ("intern", "trainee", "graduate", "junior", "jr"):
            self.assertNotIn(word, hd.ROLE_TERMS)
            self.assertIn(word, hd.ENTRY_TERMS)

    def test_a_level_title_restores_nothing(self):
        self.assertEqual(restored(held=["Principal Engineer"]), [])
        self.assertEqual(restored(held=["Staff Engineer"]), [])

    def test_an_entry_title_restores_nothing(self):
        self.assertEqual(restored(held=["Graduate Engineer"]), [])
        self.assertEqual(restored(held=["Engineering Intern"]), [])

    def test_every_role_term_is_in_the_configured_drop_lists(self):
        import config
        configured = (set(config.SCORING["hard_drop_terms"])
                      | set(config.SCORING["soft_drop_terms"]))
        for term in hd.ROLE_TERMS:
            self.assertIn(term, configured, term)


class TheCostGuardSurvives(unittest.TestCase):

    def test_a_catalogue_keyword_stays_dropped_however_grounded(self):
        flood = Market([("engineering manager", 5)] * 90 + [("x", 5)] * 10)
        got, record = hd.restore(person(held=["Engineering Manager"]), flood)
        self.assertEqual(got, [])
        self.assertIn("catalogue", record["decisions"][0]["reason"])

    def test_a_one_word_title_is_never_restored(self):
        self.assertEqual(restored(held=["Manager"]), [])
        self.assertEqual(restored(held=["Architect"]), [])

    def test_a_title_the_corpus_has_never_seen_is_still_restored(self):
        """The corpus is the history of software-shaped searches, not the
        market. Requiring corpus presence made the bias self-sealing."""
        self.assertEqual(restored(held=["Digital Marketing Manager"]),
                         ["digital marketing manager"])

    def test_an_absent_market_does_not_crash_or_block(self):
        got, _record = hd.restore(person(held=["Engineering Manager"]), None)
        self.assertEqual(got, ["engineering manager"])


class Explainability(unittest.TestCase):
    """§11. Every decision names its term, its evidence and its reason."""

    def test_a_granted_exemption_records_all_four(self):
        _got, record = hd.restore(person(held=["Engineering Manager"]), MARKET)
        row = record["decisions"][0]
        self.assertEqual(row["query"], "engineering manager")
        self.assertEqual(row["matched_terms"], ["manager"])
        self.assertTrue(row["granted"])
        self.assertIn("grounded", row["reason"])
        self.assertTrue(hd.explain(record).strip())

    def test_a_refusal_records_all_four(self):
        _got, record = hd.restore(
            person(held=["Software Engineer"], target="Engineering Manager"),
            MARKET)
        row = [d for d in record["decisions"] if d["source"] == "stated_target"][0]
        self.assertEqual(row["matched_terms"], ["manager"])
        self.assertFalse(row["granted"])
        self.assertIn("no strong management evidence", row["evidence"])

    def test_the_record_says_which_terms_are_never_exempted(self):
        _got, record = hd.restore(person(held=["Engineering Manager"]), MARKET)
        self.assertIn("principal", record["level_terms_never_exempted"])
        self.assertIn("intern", record["entry_terms_never_exempted"])


FIELDS = {"name": "Ada Okonkwo", "years_experience": 9,
          "titles": ["Engineering Manager"],
          "skills": ["python", "postgresql", "kubernetes"],
          "companies": ["Northwind"], "education": [], "institutions": [],
          "projects": [], "certifications": []}
EMPLOYMENT = {"target_field": "",
              "employment": [{"title": "Engineering Manager",
                              "company": "Northwind", "start": "2019-02",
                              "end": "2025-01", "is_current": False,
                              "same_career": True}]}


RESUME = (
    "Ada Okonkwo\n"
    "Engineering Manager\n\n"
    "EXPERIENCE\n"
    "Engineering Manager, Northwind, Feb 2019 - Jan 2025\n"
    "- Managed a team of 11 engineers and owned the platform roadmap\n"
    "- Ran hiring, performance reviews and the team budget\n"
    "- Built the deployment tooling the team runs on\n\n"
    "SKILLS\n"
    "python, postgresql, kubernetes\n")


def wiring_market():
    """Dense enough to rank, sparse enough that nothing trips the catalogue cap.

    Every title has to stay under WILDCARD_SHARE (25%) of the whole market or
    validate() throws it away as "the catalogue", which is why the noise rows
    are here: without them "backend engineer" alone is half the corpus and the
    flag-off arm escalates with no queries at all.
    """
    rows = []
    for i in range(140):
        rows.append(("backend engineer", 25 if i % 2 else 5,
                     frozenset({"python", "postgresql"}), f"co{i % 20}"))
    for i in range(110):
        rows.append(("platform engineer", 20 if i % 2 else 4,
                     frozenset({"kubernetes", "python"}), f"pl{i % 15}"))
    for i in range(90):
        rows.append(("engineering manager", 30 if i % 2 else 6,
                     frozenset({"python", "kubernetes"}), f"em{i % 12}"))
    for i in range(600):
        rows.append((f"noise role {i % 40}", 10,
                     frozenset({f"skill{i % 50}"}), f"nz{i % 60}"))
    return local_search.Market(rows=rows)


class TheWiring(unittest.TestCase):
    """generate() actually consults this, and only when the flag is on."""

    def setUp(self):
        self._flag = os.environ.get(hd.FLAG)
        self._engine = os.environ.get(sc.VERSION_ENV)
        os.environ[sc.VERSION_ENV] = "v2"
        self._e, self._m = le.extract, le.employment
        le.extract = lambda m, t, *a, **k: (dict(FIELDS), 0.1)
        le.employment = lambda m, t, *a, **k: dict(
            EMPLOYMENT, employment=[dict(EMPLOYMENT["employment"][0])])

    def tearDown(self):
        le.extract, le.employment = self._e, self._m
        for name, saved in ((hd.FLAG, self._flag),
                            (sc.VERSION_ENV, self._engine)):
            os.environ.pop(name, None)
            if saved is not None:
                os.environ[name] = saved

    def run_generate(self, flag):
        os.environ.pop(hd.FLAG, None)
        if flag:
            os.environ[hd.FLAG] = "1"
        return local_profile.generate(RESUME, {"avoid": []},
                                      market=wiring_market(),
                                      log=lambda *a, **k: None)

    def test_the_manager_query_is_absent_with_the_flag_off(self):
        profile = self.run_generate(False)
        self.assertNotIn("engineering manager",
                         [q.lower() for q in profile["role_keywords"]])
        self.assertEqual(profile["hard_drop_restored"], [])
        self.assertIsNone(profile["hard_drop_record"])

    def test_the_manager_query_comes_back_with_the_flag_on(self):
        profile = self.run_generate(True)
        self.assertIn("engineering manager",
                      [q.lower() for q in profile["role_keywords"]])
        self.assertEqual(profile["hard_drop_restored"], ["engineering manager"])
        self.assertTrue(profile["hard_drop_record"]["decisions"])

    def test_nothing_else_about_the_profile_moves(self):
        off, on = self.run_generate(False), self.run_generate(True)
        self.assertEqual(off["skill_weights"], on["skill_weights"])
        self.assertEqual(off["title_hints"], on["title_hints"])
        self.assertEqual(off["years_experience"], on["years_experience"])
        added = set(q.lower() for q in on["role_keywords"]) - set(
            q.lower() for q in off["role_keywords"])
        self.assertEqual(added, {"engineering manager"})
        self.assertFalse(set(q.lower() for q in off["role_keywords"])
                         - set(q.lower() for q in on["role_keywords"]),
                         "the step removed a query")


class OffByDefault(unittest.TestCase):

    def setUp(self):
        self.saved = os.environ.get(hd.FLAG)
        os.environ.pop(hd.FLAG, None)

    def tearDown(self):
        os.environ.pop(hd.FLAG, None)
        if self.saved is not None:
            os.environ[hd.FLAG] = self.saved

    def test_the_step_is_off_unless_asked_for(self):
        self.assertFalse(hd.enabled())

    def test_the_flag_is_read_per_call(self):
        os.environ[hd.FLAG] = "1"
        self.assertTrue(hd.enabled())
        os.environ[hd.FLAG] = "0"
        self.assertFalse(hd.enabled())

    def test_restore_never_removes_anything(self):
        existing = ["backend engineer", "software engineer"]
        got, _record = hd.restore(
            person(held=["Engineering Manager"], queries=existing), MARKET)
        self.assertEqual(set(existing) - set(existing + got), set())
        self.assertNotIn("backend engineer", got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
