"""Offline checks for the backend gate. Run: python -m unittest bench.test_backends."""

import copy
import unittest
from unittest.mock import patch

from bench import backends as bench


def observation():
    return {
        "extracted": {"skills": ["python", "sql"], "titles": ["Backend Engineer"],
                      "years_experience": 3},
        "employment": {"target_field": "software engineering", "employment": [
            {"company": "Acme", "title": "Backend Engineer", "start": "Jan 2023",
             "end": "Jan 2026", "relevant": True}]},
        "decision": {"decision": "corrected", "corrections": [
            {"field": "years_experience", "action": "computed", "from": 4,
             "to": 3, "why": "summed from the extracted date ranges"}],
            "reasons": []},
        "profile": {"role_keywords": ["backend engineer", "python developer"],
                    "skill_weights": [{"term": "python", "weight": 4}],
                    "notes": "display text", "years_experience": 3},
        "config": {"SEARCH": {"experience_years": 3, "locations": ["Remote"]},
                   "SETTINGS": {"max_experience_years": 6},
                   "ATS_TITLE_HINTS": ["backend"], "ATS_TITLE_EXCLUDE": []},
    }


class SemanticGate(unittest.TestCase):
    def compare(self, left, right):
        self.assertTrue(hasattr(bench, "compare_observations"),
                        "benchmark needs a semantic comparison, not only dict equality")
        return bench.compare_observations(left, right, now=(2026, 9))

    def test_order_of_extracted_skills_and_equivalent_dates_are_benign(self):
        left, right = observation(), observation()
        right["extracted"]["skills"].reverse()
        right["employment"]["employment"][0]["start"] = "01/2023"
        result = self.compare(left, right)
        self.assertFalse(result["exact_match"])
        self.assertTrue(result["semantic_match"])
        self.assertTrue(all(d["classification"] == "benign formatting"
                            for d in result["diffs"]))
        self.assertEqual(left, observation(), "normalization must not mutate evidence")

    def test_query_order_dates_relevance_weights_and_limits_cannot_be_hidden(self):
        mutations = [
            lambda x: x["profile"]["role_keywords"].reverse(),
            lambda x: x["employment"]["employment"][0].update(start="Feb 2023"),
            lambda x: x["employment"]["employment"][0].update(relevant=False),
            lambda x: x["employment"]["employment"].clear(),
            lambda x: x["profile"]["skill_weights"][0].update(weight=5),
            lambda x: x["config"]["SETTINGS"].update(max_experience_years=7),
            lambda x: x["config"]["SEARCH"].update(experience_years=4),
            lambda x: x["config"]["ATS_TITLE_EXCLUDE"].append("backend"),
            lambda x: x["decision"].update(decision="accept"),
            lambda x: x["decision"]["corrections"].clear(),
            lambda x: x["extracted"]["skills"].append("java"),
            lambda x: x["config"].update(new_filter=True),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                right = observation()
                mutate(right)
                result = self.compare(observation(), right)
                self.assertFalse(result["semantic_match"])
                self.assertIn("behavior-changing", [d["classification"]
                                                     for d in result["diffs"]])

    def test_unreadable_dates_missing_fields_and_duplicates_remain_visible(self):
        left = observation()
        left["employment"]["employment"][0]["start"] = "unknown"
        right = copy.deepcopy(left)
        right["employment"]["employment"][0]["start"] = "unreadable"
        self.assertFalse(self.compare(left, right)["semantic_match"])
        right = observation()
        right["extracted"]["skills"].append("sql")
        self.assertFalse(self.compare(observation(), right)["semantic_match"])
        self.assertFalse(self.compare({"x": None}, {})["semantic_match"])
        self.assertFalse(self.compare({"x": False}, {"x": 0})["semantic_match"])
        self.assertFalse(self.compare({"x": "<MISSING>"}, {})["semantic_match"])

    def test_title_gate_order_is_benign_but_query_order_is_not(self):
        left, right = observation(), observation()
        left["profile"]["title_hints"] = ["backend", "python"]
        right["profile"]["title_hints"] = ["python", "backend"]
        self.assertTrue(self.compare(left, right)["semantic_match"])
        right["profile"]["role_keywords"].reverse()
        self.assertFalse(self.compare(left, right)["semantic_match"])

    def test_full_corpus_has_52_and_explicit_path_is_not_discarded(self):
        self.assertTrue(hasattr(bench, "LAYOUTS"), "must support all four layouts")
        self.assertEqual(len(bench.documents(layouts=bench.LAYOUTS)), 52)
        self.assertEqual(len(bench.documents(people=["bhaskar", "ada", "hana"])), 3)
        path = str(bench.RESUMES) + "/ada-plain.pdf"
        self.assertEqual(bench.documents(paths=[path]), [("ada-plain", path)])
        with self.assertRaises(ValueError):
            bench.documents(people=["does-not-exist"])


ROLE = {"company": "DealerMatix Technologies", "title": "Software Engineer Onsite",
        "start": "Jan 2025", "end": "Present", "relevant": True}
OTHER = {"company": "Acme Cloud", "title": "Software Engineer",
         "start": "Jun 2023", "end": "Dec 2024", "relevant": True}


def with_rows(rows):
    observed = observation()
    observed["employment"]["employment"] = copy.deepcopy(rows)
    return observed


class ExactDuplicateEmploymentRows(unittest.TestCase):
    """The one rule that lets a raw mismatch pass semantically — and its fence.

    Sarthak: local-direct emitted the single DealerMatix role twice in 8/8
    runs, cold Modal once in 7/7; the résumé lists it once. Sweep de-duplicates
    held titles and merges overlapping ranges, so the duplicate changes no
    downstream value. Every case below that is NOT an exact duplicate must
    still fail, or the rule has grown.
    """

    def compare(self, left_rows, right_rows):
        return bench.compare_observations(with_rows(left_rows), with_rows(right_rows),
                                          now=(2026, 9))

    def assert_duplicate_only(self, result, local_len, remote_len):
        # The raw mismatch must stay visible: never an exact match.
        self.assertFalse(result["exact_match"])
        self.assertTrue(result["semantic_match"])
        rows = [d for d in result["diffs"] if d["path"] == "employment.employment"]
        self.assertEqual(len(rows), 1, "the raw employment difference must still be reported")
        self.assertEqual(rows[0]["classification"], "benign formatting")
        self.assertEqual(rows[0]["normalization"], bench.DUPLICATE_ROWS)
        self.assertIn("exact duplicate", rows[0]["reason"])
        # Evidence is the RAW lists, not the de-duplicated ones.
        self.assertEqual((len(rows[0]["local"]), len(rows[0]["remote"])),
                         (local_len, remote_len))

    # --- the four cases that are exact duplicates, and so must pass --------

    def test_one_role_repeated(self):
        self.assert_duplicate_only(self.compare([ROLE, ROLE], [ROLE]), 2, 1)

    def test_role_repeated_among_two(self):
        self.assert_duplicate_only(self.compare([ROLE, ROLE, OTHER], [ROLE, OTHER]), 3, 2)

    def test_repeat_at_the_end(self):
        self.assert_duplicate_only(self.compare([ROLE, OTHER, OTHER], [ROLE, OTHER]), 3, 2)

    def test_same_month_spelled_differently_is_still_an_exact_repeat(self):
        # Equal only AFTER the comparator's existing parse_month normalization.
        spelled = dict(ROLE, start="January 2025")
        self.assert_duplicate_only(self.compare([ROLE, spelled], [ROLE]), 2, 1)

    # --- the seven genuine differences, which must keep failing ------------

    def assert_still_fails(self, left_rows, right_rows):
        result = self.compare(left_rows, right_rows)
        self.assertFalse(result["exact_match"])
        self.assertFalse(result["semantic_match"])
        self.assertIn("behavior-changing", [d["classification"] for d in result["diffs"]])
        self.assertNotIn(bench.DUPLICATE_ROWS,
                         [d.get("normalization") for d in result["diffs"]])

    def test_a_missing_distinct_role_fails(self):
        self.assert_still_fails([ROLE, OTHER], [ROLE])

    def test_a_different_end_date_fails(self):
        self.assert_still_fails([ROLE], [dict(ROLE, end="Dec 2025")])

    def test_reordered_distinct_roles_fail(self):
        # Held titles are read in row order, so this is not formatting.
        self.assert_still_fails([ROLE, OTHER], [OTHER, ROLE])

    def test_a_flipped_relevance_flag_fails(self):
        self.assert_still_fails([ROLE], [dict(ROLE, relevant=False)])

    def test_a_different_title_fails(self):
        self.assert_still_fails([ROLE], [dict(ROLE, title="Senior Engineer")])

    def test_a_near_duplicate_with_another_start_month_fails(self):
        self.assert_still_fails([ROLE, dict(ROLE, start="Feb 2025")], [ROLE])

    def test_a_company_differing_only_by_case_is_not_a_duplicate(self):
        self.assert_still_fails([ROLE, dict(ROLE, company="dealermatix technologies")],
                                [ROLE])

    # --- where a duplicate DOES reach observable output --------------------

    def test_a_duplicate_that_changes_a_correction_string_still_fails(self):
        """With an unreadable date, a repeated row changes the count inside
        the router's own correction text. That is observable output, so the
        gate must still stop — the rule collapses the rows, never the text."""
        import local_extract as le

        readable = ROLE
        unreadable = dict(ROLE, start="sometime", end="later")
        text = "DealerMatix Technologies Software Engineer Onsite react native"
        parsed = {"name": "", "years_experience": 2, "skills": ["react native"],
                  "titles": ["Software Engineer Onsite"],
                  "companies": ["DealerMatix Technologies"]}

        def observed(rows):
            out = with_rows(rows)
            routed = le.route(dict(parsed), {"employment": copy.deepcopy(rows)}, text,
                              now=(2026, 9))
            out["decision"] = {k: routed[k] for k in ("decision", "corrections", "reasons")}
            return out

        left = observed([readable, unreadable])
        right = observed([readable, unreadable, unreadable])
        self.assertNotEqual(left["decision"]["corrections"], right["decision"]["corrections"],
                            "precondition: production text really does change")
        result = bench.compare_observations(left, right, now=(2026, 9))
        self.assertFalse(result["exact_match"])
        self.assertFalse(result["semantic_match"])
        by_path = {d["path"]: d for d in result["diffs"]}
        self.assertEqual(by_path["employment.employment"]["normalization"], bench.DUPLICATE_ROWS)
        self.assertEqual(by_path["decision.corrections"]["classification"], "behavior-changing")

    def test_normalization_does_not_mutate_the_evidence(self):
        left, right = with_rows([ROLE, ROLE]), with_rows([ROLE])
        bench.compare_observations(left, right, now=(2026, 9))
        self.assertEqual(left["employment"]["employment"], [ROLE, ROLE])

    def test_summary_keeps_raw_and_semantic_apart(self):
        duplicate = bench.compare_observations(with_rows([ROLE, ROLE]), with_rows([ROLE]),
                                               now=(2026, 9))
        identical = bench.compare_observations(with_rows([ROLE]), with_rows([ROLE]),
                                               now=(2026, 9))
        different = bench.compare_observations(with_rows([ROLE]), with_rows([OTHER]),
                                               now=(2026, 9))
        report = {"requested": 4, "rows": [
            {"resume": "dup", "accepted": True, **duplicate},
            {"resume": "same", "accepted": True, **identical},
            {"resume": "diff", "accepted": True, **different},
        ]}
        self.assertFalse(bench.summarise(report, log=lambda *a: None))
        summary = report["summary"]
        self.assertEqual(summary["completed"], 3)
        self.assertEqual(summary["raw_exact_matches"], 1)
        self.assertEqual(summary["semantic_matches"], 2)
        self.assertEqual(summary["duplicate_only_raw_differences"], ["dup"])
        self.assertEqual(summary["semantic_mismatches"], ["diff"])
        self.assertEqual([d["resume"] for d in summary["remaining_differences"]], ["diff"])


class ProductionCapture(unittest.TestCase):
    def test_modal_gate_requires_restore_proof_before_any_inference(self):
        import corpus_signal
        import local_search

        health = {"model": "qwen3:8b", "model_slots": 1}
        with patch.object(bench, "local_identity", return_value={}) as identity, \
                patch.object(bench, "service_up", return_value=(True, health)), \
                patch.object(local_search, "Market", return_value=local_search.Market(rows=[])), \
                patch.object(corpus_signal, "frequencies", return_value={}), \
                patch.object(bench, "observe", return_value=(observation(), 1)) as observe:
            with self.assertRaisesRegex(ValueError, "endpoint-state"):
                bench.run(people=["ada"], label="modal", log=lambda *a: None)
            identity.assert_not_called()
            observe.assert_not_called()
            for label in ("remote", "oracle"):
                report = bench.run(people=["ada"], label=label, log=lambda *a: None)
                self.assertTrue(bench.summarise(report, log=lambda *a: None))

    def test_two_calls_capture_employment_and_finish_and_render_real_profile(self):
        import inference
        import local_extract as le
        import local_search

        self.assertTrue(hasattr(bench, "observe"), "full production capture is required")
        text = ("Ada\nBackend Engineer at Acme Jan 2023 - Jan 2026\n"
                "Skills\npython, sql, redis\nExperience\nBackend Engineer at Acme")
        fields = {"name": "Ada", "years_experience": 4, "titles": ["Backend Engineer"],
                  "skills": ["python", "sql"], "companies": ["Acme"], "education": [],
                  "institutions": [], "projects": [], "certifications": []}
        employment = observation()["employment"]
        bodies = []

        def wire(url, body, timeout, headers=None):
            import json
            bodies.append(body)
            answer = fields if body["format"] == le.FIELDS_SCHEMA else employment
            return 200, {"response": json.dumps(answer)}

        market = local_search.Market(rows=[], seniority=("senior",))
        with patch.object(inference, "_post", side_effect=wire):
            out, _seconds = bench.observe(text, "local-direct", market,
                                          {"redis": (10, 1000)}, now=(2026, 9))
        self.assertEqual(len(bodies), 2, "capturing must not repeat model calls")
        self.assertEqual(bodies[0]["prompt"], le.FIELDS_PROMPT.format(text=text))
        self.assertEqual(bodies[1]["prompt"], le.EMPLOYMENT_PROMPT.format(text=text))
        self.assertTrue(all(b["think"] is False for b in bodies))
        self.assertEqual(out["employment"], employment)
        self.assertEqual(out["extracted"]["years_experience"], 3)
        self.assertEqual(out["decision"]["decision"], "corrected")
        self.assertEqual(len(out["decision"]["corrections"]), 1)
        self.assertEqual(out["config"]["SEARCH"]["experience_years"], 3)
        self.assertEqual(out["config"]["SETTINGS"]["max_experience_years"], 6)
        self.assertIn("redis", out["config"]["SCORING"]["skill_weights"])
        self.assertEqual(out["config"]["FEEDS"]["himalayas"]["queries"],
                         out["profile"]["role_keywords"])

    def test_first_semantic_difference_stops_remaining_documents(self):
        import corpus_signal
        import local_search
        left, right = observation(), observation()
        right["employment"]["employment"].clear()
        health = {"model": "qwen3:8b", "model_slots": 1}
        with patch.object(bench, "local_identity", return_value={}), \
                patch.object(bench, "service_up", return_value=(True, health)), \
                patch.object(local_search, "Market", return_value=local_search.Market(rows=[])), \
                patch.object(corpus_signal, "frequencies", return_value={}), \
                patch.object(bench, "observe", side_effect=[(left, 1), (right, 1)]):
            report = bench.run(people=["ada", "hana"], log=lambda *a: None)
        self.assertEqual(report["requested"], 2)
        self.assertEqual(len(report["rows"]), 1)
        self.assertFalse(bench.summarise(report, log=lambda *a: None))

    def test_same_escalation_on_both_backends_is_not_a_passing_profile(self):
        report = {"requested": 1, "rows": [{"exact_match": True,
                  "semantic_match": True, "accepted": False}]}
        self.assertFalse(bench.summarise(report, log=lambda *a: None))

    def test_http_capture_uses_real_service_without_repeating_or_changing_requests(self):
        import inference
        import inference_service
        import local_search
        import local_extract as le
        import json

        text = "Ada\nBackend Engineer at Acme Jan 2023 - Jan 2026\nSkills: python, sql"
        fields = {"name": "Ada", "years_experience": 4, "skills": ["python", "sql"],
                  "titles": ["Backend Engineer"], "companies": ["Acme"],
                  "institutions": [], "education": [], "projects": [], "certifications": []}
        app = inference_service.create_app(accepted={"benchmark-secret"})
        model_bodies = []

        def wire(url, body, timeout, headers=None):
            if url == "https://benchmark.example/v1/generate":
                reply = app.test_client().post("/v1/generate", data=json.dumps(body),
                                                content_type="application/json", headers=headers)
                self.assertEqual(reply.status_code, 200, "explicit benchmark token must be used")
                return reply.status_code, reply.json
            self.assertTrue(url.endswith("/api/generate"))
            model_bodies.append(copy.deepcopy(body))
            result = fields if body["format"] == le.FIELDS_SCHEMA else observation()["employment"]
            return 200, {"response": json.dumps(result)}

        market = local_search.Market(rows=[], seniority=("senior",))
        with patch.object(inference, "_post", side_effect=wire):
            direct, _ = bench.observe(text, "local-direct", market, {}, now=(2026, 9))
            remote, _ = bench.observe(text, "remote", market, {},
                                       url="https://benchmark.example", token="benchmark-secret",
                                       now=(2026, 9))
        self.assertEqual(len(model_bodies), 4)
        self.assertEqual(json.dumps(model_bodies[:2]), json.dumps(model_bodies[2:]))
        self.assertTrue(bench.compare_observations(direct, remote)["semantic_match"])


if __name__ == "__main__":
    unittest.main()
