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
