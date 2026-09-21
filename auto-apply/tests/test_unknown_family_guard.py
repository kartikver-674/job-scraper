"""A title nobody can classify must earn its place on evidence.

`role_evidence.family_of` returns None for a title the taxonomy has never
learned, and None fails open — so every profession-family check disappears at
once and a one-advert artefact arrives on the same terms as a real profession.
Measured across 15 personas in docs/corpus-role-tail-audit.md:

    node js                                 2 postings, 1 employer
    quality analyst                         1 posting,  1 employer
    ai-native software entwickler (m/w/d)   3 postings, 1 employer
    revenue operations                      6 postings, 4 employers

What these tests defend, in order of how badly each would hurt:

  * a KNOWN family is never touched, however thin. 54 of 73 admitted corpus
    roles rest on fewer than ten postings and most are useful; a blanket floor
    removes three quarters of every search
  * a HELD or grounded résumé title is never rejected — the taxonomy's gaps
    must never stop someone searching their own job
  * nobody is left with zero queries
  * an unknown family with real corpus support survives: the hypothetical
    RevOps case, 40 postings across 12 employers
  * canonical revalidation keeps its own rejections
  * with the flag off, the query list is byte-identical
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import canonical_guard                                      # noqa: E402
import local_search                                         # noqa: E402
import role_evidence                                        # noqa: E402
import skill_concepts                                       # noqa: E402
import unknown_family_guard as guard                         # noqa: E402

FLAG = guard.FLAG

# Every title below is one the audit actually observed, with the counts it
# actually rested on. `revops big` is the hypothetical the policy exists for.
ADMISSION = {
    "quality analyst": {"postings": 1, "companies": 1, "weight": 17.94},
    "node js": {"postings": 2, "companies": 1, "weight": 14.71},
    "ai-native software entwickler (m/w/d)":
        {"postings": 3, "companies": 1, "weight": 24.79},
    "software engineering professional":
        {"postings": 6, "companies": 1, "weight": 44.57},
    "revenue operations": {"postings": 6, "companies": 4, "weight": 55.09},
    "nine postings ten firms": {"postings": 9, "companies": 10, "weight": 90.0},
    "twenty postings two firms": {"postings": 20, "companies": 2, "weight": 200.0},
    "exactly on the floor": {"postings": 10, "companies": 3, "weight": 100.0},
    "revops big": {"postings": 40, "companies": 12, "weight": 400.0},
    # known family, as thin as anything above
    "salesforce administrator": {"postings": 1, "companies": 1, "weight": 11.0},
    "back end developer": {"postings": 1, "companies": 1, "weight": 16.03},
    "product owner": {"postings": 3, "companies": 1, "weight": 33.83},
}


class Flagged(unittest.TestCase):
    def tearDown(self):
        os.environ.pop(FLAG, None)

    def test_off_by_default(self):
        self.assertFalse(guard.enabled())

    def test_flag_off_changes_no_query(self):
        queries = list(ADMISSION)
        kept, record = guard.filter_queries(queries, (), ADMISSION)
        # filter_queries itself is unconditional; local_profile gates it. The
        # contract asserted here is that an empty admission record is inert.
        self.assertEqual(guard.filter_queries(queries, (), {})[0], queries)
        self.assertEqual(record["flag"], FLAG)


class ThePolicy(unittest.TestCase):
    """The five cases the ratified policy names, and their neighbours."""

    def keep(self, title):
        kept, _ = guard.filter_queries([title, "salesforce administrator"],
                                       (), ADMISSION)
        return title in kept

    def test_1_unknown_one_posting_one_company_rejected(self):
        self.assertIsNone(role_evidence.family_of("quality analyst"))
        self.assertFalse(self.keep("quality analyst"))

    def test_2_unknown_nine_postings_ten_companies_rejected(self):
        ok, why = guard.assess("nine postings ten firms", None, ADMISSION)
        self.assertFalse(ok)
        self.assertEqual(why["short_of"], "postings")
        self.assertEqual(why["reason"], guard.REASON)

    def test_3_unknown_twenty_postings_two_companies_rejected(self):
        ok, why = guard.assess("twenty postings two firms", None, ADMISSION)
        self.assertFalse(ok)
        self.assertEqual(why["short_of"], "companies")

    def test_4_unknown_exactly_on_the_floor_kept(self):
        ok, why = guard.assess("exactly on the floor", None, ADMISSION)
        self.assertTrue(ok)
        self.assertEqual(why["rule"], "unknown_family_well_supported")

    def test_5_the_future_revops_case_is_kept(self):
        # 40 postings, 12 employers. The taxonomy still cannot place it; the
        # market can. This is the whole reason the rule is evidence-shaped
        # rather than "unknown family = reject".
        self.assertTrue(self.keep("revops big"))

    def test_the_observed_unknown_titles_all_go(self):
        kept, record = guard.filter_queries(
            ["quality analyst", "node js", "software engineering professional",
             "revenue operations", "ai-native software entwickler (m/w/d)",
             "salesforce administrator"], (), ADMISSION)
        self.assertEqual(kept, ["salesforce administrator"])
        self.assertEqual(len(record["rejected"]), 5)


class KnownFamiliesAreUntouched(unittest.TestCase):
    """54 of 73 admitted corpus roles are thin. None of them is this guard's."""

    THIN_BUT_KNOWN = ("salesforce administrator", "back end developer",
                      "product owner")

    def test_thin_known_family_titles_survive(self):
        kept, record = guard.filter_queries(list(self.THIN_BUT_KNOWN), (),
                                            ADMISSION)
        self.assertEqual(kept, list(self.THIN_BUT_KNOWN))
        self.assertEqual(record["rejected"], [])

    def test_each_one_is_kept_for_its_family_not_its_evidence(self):
        for title in self.THIN_BUT_KNOWN:
            ok, why = guard.assess(title, None, ADMISSION)
            self.assertTrue(ok, title)
            self.assertEqual(why["rule"], "known_family", title)
            self.assertIsNotNone(role_evidence.family_of(title), title)
            # ...and each really is as thin as the rejected ones
            self.assertLess(ADMISSION[title]["postings"], guard.MIN_POSTINGS)


class HeldTitlesAreExempt(unittest.TestCase):
    def test_7_held_title_unknown_family_one_posting_kept(self):
        kept, record = guard.filter_queries(
            ["node js", "salesforce administrator"], held=["node js"],
            admission=ADMISSION)
        self.assertIn("node js", kept)
        self.assertEqual([d["rule"] for d in record["decisions"]
                          if d["query"] == "node js"], ["held_title"])

    def test_8_a_grounded_resume_title_the_taxonomy_cannot_place(self):
        # Someone whose job IS Revenue Operations Manager must be able to
        # search for it. The taxonomy's silence must never stop them.
        title = "revenue operations manager"
        self.assertIsNone(role_evidence.family_of(title))
        adm = dict(ADMISSION, **{title: {"postings": 1, "companies": 1,
                                         "weight": 9.0}})
        kept, _ = guard.filter_queries([title, "salesforce administrator"],
                                       held=[title], admission=adm)
        self.assertIn(title, kept)

    def test_held_matching_is_case_and_space_insensitive(self):
        kept, _ = guard.filter_queries(["Node JS"], held=["  node js  "],
                                       admission=ADMISSION)
        self.assertEqual(kept, ["Node JS"])


class NeverLeavesNobodyWithNothing(unittest.TestCase):
    def test_the_last_query_is_never_taken_away(self):
        kept, record = guard.filter_queries(["quality analyst"], (), ADMISSION)
        self.assertEqual(kept, ["quality analyst"])
        self.assertTrue(record.get("fail_open"))
        self.assertEqual(record["rejected"], [])

    def test_a_query_with_no_admission_record_is_not_judged(self):
        ok, why = guard.assess("something else entirely", None, ADMISSION)
        self.assertTrue(ok)
        self.assertEqual(why["rule"], "no_admission_record")


class CanonicalKeepsItsOwnRejections(unittest.TestCase):
    def test_9_sf_data_cloud_is_still_canonical_s(self):
        market = local_search.frozen_market()
        if not local_search.usable(market.rows):
            self.skipTest("no frozen corpus in this checkout")
        hard, _soft = local_search.seniority_lists()
        ok, rule, _why = canonical_guard.check(
            "sf -data cloud", getattr(market, "titles", ()), (), hard)
        self.assertFalse(ok)
        self.assertEqual(rule, "search_operator")

    def test_it_never_reaches_this_guard(self):
        # local_profile runs canonical revalidation first, so the string is
        # already gone. Asserted here so the ordering is not silently changed.
        import inspect
        sys.path.insert(0, os.path.join(ROOT, "auto-apply"))
        import local_profile
        src = inspect.getsource(local_profile.generate)
        self.assertLess(src.index("canonical_guard.enabled()"),
                        src.index("unknown_family_guard.enabled()"))
        self.assertLess(src.index("unknown_family_guard.enabled()"),
                        src.index("role_evidence.filter_queries"))


class ProvenanceIsRecorded(unittest.TestCase):
    def test_a_rejection_carries_its_numbers(self):
        _kept, record = guard.filter_queries(
            ["quality analyst", "salesforce administrator"], (), ADMISSION)
        row = [d for d in record["decisions"]
               if d["query"] == "quality analyst"][0]
        self.assertEqual(row["reason"], guard.REASON)
        self.assertIsNone(row["family"])
        self.assertEqual(row["postings"], 1)
        self.assertEqual(row["companies"], 1)
        self.assertEqual(row["weight"], 17.94)
        self.assertIn("quality analyst", guard.explain(record))

    def test_a_canonical_replacement_is_measured_on_its_fragment(self):
        kept, _ = guard.filter_queries(
            ["Revenue Operations Lead"], (), ADMISSION,
            trace=[("revops big", "Revenue Operations Lead")])
        self.assertEqual(kept, ["Revenue Operations Lead"])


class AdmissionCounts(unittest.TestCase):
    """keywords_for must report postings and employers, not weights."""

    def test_counts_are_integers_and_weight_is_not(self):
        market = local_search.frozen_market()
        if not local_search.usable(market.rows):
            self.skipTest("no frozen corpus in this checkout")
        stats = {}
        local_search.keywords_for(
            {"salesforce", "sales cloud", "service cloud", "soql"},
            market.rows, market.index, market.total, want=12,
            seniority=market.seniority, stats=stats)
        self.assertTrue(stats)
        for frag, row in stats.items():
            self.assertIsInstance(row["postings"], int, frag)
            self.assertIsInstance(row["companies"], int, frag)
            self.assertGreaterEqual(row["postings"], 1, frag)
            self.assertLessEqual(row["companies"], row["postings"], frag)

    def test_the_weight_floor_is_not_a_posting_count(self):
        # The rename's whole point: one posting can clear a floor of 10.
        self.assertEqual(local_search.MIN_EVIDENCE_WEIGHT, 10)
        market = local_search.frozen_market()
        if not local_search.usable(market.rows):
            self.skipTest("no frozen corpus in this checkout")
        stats = {}
        found = local_search.keywords_for(
            {"salesforce", "sales cloud", "service cloud", "soql"},
            market.rows, market.index, market.total, want=12,
            seniority=market.seniority, stats=stats)
        admitted = [stats[f] for f in found if f in stats]
        self.assertTrue(any(r["postings"] < local_search.MIN_EVIDENCE_WEIGHT
                            for r in admitted),
                        "expected at least one admitted fragment resting on "
                        "fewer postings than the floor's number")

    def test_stats_are_opt_in_and_change_nothing(self):
        market = local_search.frozen_market()
        if not local_search.usable(market.rows):
            self.skipTest("no frozen corpus in this checkout")
        own = {"salesforce", "sales cloud", "service cloud", "soql"}
        args = (market.rows, market.index, market.total)
        without = local_search.keywords_for(own, *args, want=12,
                                            seniority=market.seniority)
        with_ = local_search.keywords_for(own, *args, want=12,
                                          seniority=market.seniority, stats={})
        self.assertEqual(without, with_)


class ZeroQueryProtection(unittest.TestCase):
    """10. No benchmark persona loses its last search to this guard."""

    def test_no_persona_is_left_with_nothing(self):
        market = local_search.frozen_market()
        if not local_search.usable(market.rows):
            self.skipTest("no frozen corpus in this checkout")
        from bench.people import PEOPLE

        for flag in ("", "1"):
            os.environ["SWEEP_MARKET_COMPOUND_SPLIT"] = flag
            skill_concepts._MARKET_TERMS = None
            for slug, person in PEOPLE.items():
                fields = local_search.fields_for(
                    {"skills": person["skills"],
                     "employment": person["employment"]}, market)
                kept, _rec = guard.filter_queries(
                    fields["role_keywords"], fields["held_keywords"],
                    fields["admission"], fields["canonical_trace"])
                self.assertTrue(kept, f"{slug} (split={flag!r}) lost every query")
                for held in fields["held_keywords"]:
                    self.assertIn(held, kept, f"{slug} lost held title {held!r}")
        os.environ.pop("SWEEP_MARKET_COMPOUND_SPLIT", None)
        skill_concepts._MARKET_TERMS = None


if __name__ == "__main__":
    unittest.main()
