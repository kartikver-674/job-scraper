"""Offline checks for answer-key scoring. Run: python -m unittest bench.test_answer_key."""

import copy
import unittest
from unittest.mock import patch

from bench import answer_key as ak
from bench import backends as bench
from bench.people import truth
from bench.test_backends import observation

NOW = (2026, 9)
KESTREL = {"company": "Kestrel Systems", "title": "Verification Engineer",
           "start": "Oct 2021", "end": "Present", "relevant": True}
ARAGATS = {"company": "Aragats Robotics", "title": "Systems Engineer",
           "start": "Feb 2019", "end": "Sep 2021", "relevant": True}
RIGHT = ["systems engineer", "verification engineer"]
COMPANY_NAMES = ["kestrel systems", "aragats robotics"]


def swapped(row):
    return dict(row, company=row["title"], title=row["company"])


def dmitri(rows, keywords):
    """An observation shaped like the real ones, for dmitri."""
    out = observation()
    fields, _employment = ak.key_answers("dmitri")
    out["extracted"] = dict(fields)
    out["employment"] = {"target_field": "software engineering",
                         "employment": copy.deepcopy(rows)}
    out["profile"]["role_keywords"] = list(keywords)
    out["config"]["SEARCH"]["role_keywords"] = list(keywords)
    out["config"]["FEEDS"] = {"himalayas": {"enabled": True, "pages": 1,
                                            "queries": list(keywords)}}
    return out


def scored_row(name, local, modal, key, person="dmitri"):
    row = {"resume": name, "accepted": True, **bench.compare_observations(local, modal, NOW)}
    row["scoring"] = ak.score_document(local, modal, key, person, NOW)
    row["disagreements"] = ak.classify_disagreements(row)
    return row


class AnswerKeyInPipelineContract(unittest.TestCase):
    def test_key_answers_are_the_key_in_the_extraction_schemas(self):
        fields, employment = ak.key_answers("dmitri")
        key = truth("dmitri")
        self.assertEqual(fields["skills"], [s.lower() for s in key["skills"]])
        self.assertEqual(fields["titles"], key["titles"])
        self.assertEqual(employment["target_field"], "")
        self.assertEqual([(r["company"], r["title"]) for r in employment["employment"]],
                         [("Kestrel Systems", "Verification Engineer"),
                          ("Aragats Robotics", "Systems Engineer")])
        self.assertEqual(employment["employment"][0]["end"], "present")

    def test_expected_values_come_from_production_not_from_the_benchmark(self):
        """The derived expectations are whatever production computes from a
        perfect extraction — and computing them must make no model call."""
        import inference
        import local_search
        from resume_parser import extract_text

        text = extract_text(str(bench.RESUMES / "ada-plain.pdf"))
        market = local_search.Market(rows=[], seniority=("senior",))
        with patch.object(inference, "_post",
                          side_effect=AssertionError("the key must not call a model")):
            out = ak.key_observation(text, "ada", market, {}, NOW)
        self.assertNotIn("failure", out)
        years = truth("ada")["years_experience"]
        self.assertEqual(out["profile"]["years_experience"], years)
        self.assertEqual(out["config"]["SEARCH"]["experience_years"], years)
        self.assertEqual(out["config"]["SETTINGS"]["max_experience_years"], 8)
        self.assertEqual(out["config"]["FEEDS"]["himalayas"]["queries"],
                         out["profile"]["role_keywords"])

    def test_only_synthetic_documents_have_a_key(self):
        self.assertEqual(ak.person_of("dmitri-plain"), "dmitri")
        self.assertEqual(ak.person_of("hana-twocol"), "hana")
        with self.assertRaises(ValueError):
            ak.person_of("resume")


class Categories(unittest.TestCase):
    def test_the_five_categories_are_kept_apart(self):
        self.assertEqual(ak.category(True, True, True), "both correct")
        self.assertEqual(ak.category(True, False, False), "local correct / Modal wrong")
        self.assertEqual(ak.category(False, True, False), "local wrong / Modal correct")
        self.assertEqual(ak.category(False, False, True), "both wrong, same way")
        self.assertEqual(ak.category(False, False, False), "both wrong, differently")


class ScoringDmitri(unittest.TestCase):
    """The case that made local unfit to be the oracle, and its mirror."""

    def setUp(self):
        self.key = dmitri([KESTREL, ARAGATS], RIGHT)
        self.good = dmitri([KESTREL, ARAGATS], RIGHT)
        self.bad = dmitri([swapped(KESTREL), swapped(ARAGATS)], COMPANY_NAMES)

    def test_local_swap_modal_correct_is_local_wrong_not_a_mismatch_to_forgive(self):
        s = ak.score_document(self.bad, self.good, self.key, "dmitri", NOW)
        g = s["groups"]
        for group in ("employment company", "employment title", "role_keywords",
                      "feed queries", "rendered config (all)"):
            self.assertEqual(g[group]["category"], "local wrong / Modal correct", group)
        for group in ("employment rows", "employment dates",
                      "relevant-role classification", "skills", "years_experience",
                      "search experience limits"):
            self.assertEqual(g[group]["category"], "both correct", group)
        self.assertFalse(s["local_driving_correct"])
        self.assertTrue(s["modal_driving_correct"])
        self.assertEqual(s["regressions"], [])
        self.assertEqual(g["employment title"]["values"]["key"],
                         ["Verification Engineer", "Systems Engineer"])

    def test_the_mirror_image_is_a_regression_and_is_named(self):
        s = ak.score_document(self.good, self.bad, self.key, "dmitri", NOW)
        self.assertIn("employment title", s["regressions"])
        self.assertIn("role_keywords", s["regressions"])
        # Company alone is not read downstream, so it is wrong but not a
        # search/filter regression.
        self.assertNotIn("employment company", s["regressions"])
        self.assertEqual(s["groups"]["employment company"]["category"],
                         "local correct / Modal wrong")

    def test_an_exact_duplicate_row_is_scored_by_the_approved_rule(self):
        s = ak.score_document(dmitri([KESTREL, KESTREL, ARAGATS], RIGHT), self.good,
                              self.key, "dmitri", NOW)
        self.assertEqual(s["groups"]["employment rows"]["category"], "both correct")
        self.assertTrue(s["local_driving_correct"])

    def test_both_wrong_the_same_way_and_differently(self):
        other = dmitri([dict(KESTREL, title="Engineer"), ARAGATS], ["engineer"])
        same = ak.score_document(self.bad, copy.deepcopy(self.bad), self.key, "dmitri", NOW)
        diff = ak.score_document(self.bad, other, self.key, "dmitri", NOW)
        self.assertEqual(same["groups"]["employment title"]["category"],
                         "both wrong, same way")
        self.assertEqual(diff["groups"]["employment title"]["category"],
                         "both wrong, differently")

    def test_when_production_refuses_the_key_derived_fields_are_unscorable(self):
        key = {**self.key, "failure": {"category": "escalated", "reasons": ["x"]}}
        s = ak.score_document(self.good, self.good, key, "dmitri", NOW)
        self.assertTrue(s["key_escalated"])
        self.assertIsNone(s["groups"]["role_keywords"]["local_ok"])
        self.assertTrue(s["groups"]["role_keywords"]["category"].startswith("unscorable"))
        # Source fields are still scored against bench/people.py directly.
        self.assertEqual(s["groups"]["employment title"]["category"], "both correct")


class Disagreements(unittest.TestCase):
    def test_every_dmitri_disagreement_resolves_against_the_key(self):
        row = scored_row("dmitri-plain", dmitri([swapped(KESTREL), swapped(ARAGATS)],
                                                COMPANY_NAMES),
                         dmitri([KESTREL, ARAGATS], RIGHT), dmitri([KESTREL, ARAGATS], RIGHT))
        self.assertFalse(row["semantic_match"])
        by_path = {d["path"]: d for d in row["disagreements"]}
        self.assertTrue(all(d["scored"] for d in row["disagreements"]))
        self.assertIn(("employment title", "local wrong / Modal correct"),
                      by_path["employment.employment"]["groups"])
        self.assertEqual(by_path["profile.role_keywords"]["groups"],
                         [("role_keywords", "local wrong / Modal correct")])

    def test_paths_outside_the_key_say_why_and_unknown_paths_are_flagged(self):
        self.assertEqual(ak._group_for("config.SETTINGS.min_comp_usd", {})[0],
                         "rendered config (all)")
        for path in ("profile.notes", "employment.target_field", "decision.corrections"):
            group, reason = ak._group_for(path, {})
            self.assertIsNone(group, path)
            self.assertFalse(reason.startswith("UNKNOWN"), path)
        self.assertTrue(ak._group_for("somewhere.new", {})[1].startswith("UNKNOWN"))


class Acceptance(unittest.TestCase):
    def rows(self):
        key = dmitri([KESTREL, ARAGATS], RIGHT)
        good = dmitri([KESTREL, ARAGATS], RIGHT)
        bad = dmitri([swapped(KESTREL), swapped(ARAGATS)], COMPANY_NAMES)
        return {"modal_better": scored_row("a", bad, good, key),
                "identical": scored_row("b", good, copy.deepcopy(good), key),
                "regression": scored_row("c", good, bad, key)}

    def test_modal_better_with_no_regression_qualifies(self):
        rows = self.rows()
        report = {"requested": 2, "rows": [rows["modal_better"], rows["identical"]]}
        self.assertTrue(ak.summarise(report, log=lambda *a: None))
        t = report["answer_key_summary"]["totals"]
        self.assertEqual((t["local_fully_correct"], t["modal_fully_correct"],
                          t["both_fully_correct"], t["modal_only_correct"],
                          t["local_only_correct"], t["both_incorrect"]), (1, 2, 1, 1, 0, 0))
        self.assertEqual(t["behaviour_changing_disagreements"], ["a"])

    def test_one_regression_blocks_acceptance_even_when_modal_is_better_overall(self):
        rows = self.rows()
        report = {"requested": 4, "rows": [rows["modal_better"],
                                           copy.deepcopy(rows["modal_better"]),
                                           rows["identical"], rows["regression"]]}
        self.assertFalse(ak.summarise(report, log=lambda *a: None))
        s = report["answer_key_summary"]
        self.assertGreater(s["decision"]["driving_correct_totals"]["modal"],
                           s["decision"]["driving_correct_totals"]["local"])
        self.assertEqual({r["resume"] for r in s["regressions"]}, {"c"})
        self.assertFalse(s["decision"]["criteria"][
            "no regression where local is correct and Modal wrong"])

    def test_an_unscored_document_is_never_a_pass(self):
        rows = self.rows()
        report = {"requested": 3, "rows": [rows["identical"],
                                           {"resume": "x", "error": "TimeoutError"}]}
        self.assertFalse(ak.summarise(report, log=lambda *a: None))
        self.assertEqual(report["answer_key_summary"]["totals"]["errors"],
                         [("x", "TimeoutError")])


class RunKeepsGoing(unittest.TestCase):
    def test_answer_key_mode_scores_every_document_despite_disagreement(self):
        import corpus_signal
        import local_search

        left, right = observation(), observation()
        right["employment"]["employment"].clear()
        health = {"model": "qwen3:8b", "model_slots": 1}
        with patch.object(bench, "local_identity", return_value={}), \
                patch.object(bench, "service_up", return_value=(True, health)), \
                patch.object(local_search, "Market", return_value=local_search.Market(rows=[])), \
                patch.object(corpus_signal, "frequencies", return_value={}), \
                patch.object(ak, "key_observation", return_value=observation()), \
                patch.object(bench, "observe",
                             side_effect=[(left, 1), (right, 1), (left, 1), (right, 1)]):
            report = bench.run(people=["ada", "hana"], log=lambda *a: None, answer_key=True)
        self.assertEqual(len(report["rows"]), 2, "a disagreement must not stop the corpus")
        self.assertTrue(all("scoring" in r for r in report["rows"]))

    def test_answer_key_mode_refuses_explicit_paths(self):
        with self.assertRaisesRegex(ValueError, "synthetic"):
            bench.run(paths=["whatever.pdf"], answer_key=True, log=lambda *a: None)


if __name__ == "__main__":
    unittest.main()


class MergeBatches(unittest.TestCase):
    """Batches exist so a reset loses one batch; the merge must not let
    batches from different conditions masquerade as one run."""

    @staticmethod
    def batch(slugs, now=(2026, 9)):
        return {"now": list(now), "market_sha256": "m", "url": "u",
                "baseline": {"digest": "d"}, "requested": len(slugs),
                "rows": [{"resume": s} for s in slugs]}

    def test_rows_come_back_in_corpus_order_and_unrun_documents_are_named(self):
        from bench import merge_answer_key as merge
        combined = merge.merge([self.batch(["hana-plain"]), self.batch(["ada-plain"])])
        self.assertEqual([r["resume"] for r in combined["rows"]], ["ada-plain", "hana-plain"])
        self.assertEqual(combined["requested"], 52)
        self.assertEqual(len(combined["not_run"]), 50)

    def test_batches_from_different_months_are_refused(self):
        from bench import merge_answer_key as merge
        with self.assertRaisesRegex(ValueError, "identical conditions"):
            merge.merge([self.batch(["ada-plain"]), self.batch(["hana-plain"], now=(2026, 10))])

    def test_a_document_in_two_batches_is_refused(self):
        from bench import merge_answer_key as merge
        with self.assertRaisesRegex(ValueError, "more than one batch"):
            merge.merge([self.batch(["ada-plain"]), self.batch(["ada-plain"])])
