import contextlib
import io
import json
import unittest

import make_profile


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    """Returns the payload, after raising `fail_with` for the first `fails` calls."""

    def __init__(self, payload, fails=0, fail_with="503 UNAVAILABLE"):
        self._payload = payload
        self.fails = fails
        self.fail_with = fail_with
        self.calls = 0
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.calls <= self.fails:
            raise RuntimeError(self.fail_with)
        return FakeResponse(json.dumps(self._payload))


class FakeClient:
    def __init__(self, payload, fails=0, fail_with="503 UNAVAILABLE"):
        self.models = FakeModels(payload, fails, fail_with)


# A Salesforce consultant: the shape that exposed the weighting bug documented
# in RESUME_AUTOCONFIG_PROMPT.md.
PAYLOAD = {
    "field_summary": "Salesforce functional consultant, 4 years.",
    "years_experience": 4,
    "role_keywords": ["Salesforce Business Analyst", "Salesforce Consultant"],
    "skill_weights": [
        {"term": "Salesforce", "weight": 10},
        {"term": "Apex", "weight": 8},
        {"term": "Stakeholder Management", "weight": 1},
    ],
    "penalty_terms": [{"term": "SAP", "weight": 6}, {"term": "Oracle", "weight": 4}],
    "domain_half_a": ["sales cloud"],
    "domain_half_b": ["apex"],
    "domain_title_terms": ["Salesforce Business Analyst"],
    "domain_bonus": 5,
    "notes": "Platform terms dominate; craft vocabulary demoted.",
}

PREFS = {
    "locations": ["Bengaluru", "Remote"],
    "exclude_levels": ["intern", "fresher"],
    "avoid": ["SAP"],
    "min_comp_usd": 20000,
}


def rendered_namespace(payload=PAYLOAD, prefs=PREFS, name="testperson"):
    """Render a profile and exec it, returning its module namespace."""
    source = make_profile.render(name, payload, prefs)
    namespace = {}
    exec(compile(source, f"profiles/{name}.py", "exec"), namespace)
    return namespace


class TestRender(unittest.TestCase):
    def test_output_is_valid_python_with_the_expected_dicts(self):
        ns = rendered_namespace()
        self.assertEqual(ns["SEARCH"]["role_keywords"], PAYLOAD["role_keywords"])
        self.assertEqual(ns["SEARCH"]["locations"], PREFS["locations"])
        self.assertEqual(ns["SEARCH"]["experience_years"], 4)
        self.assertEqual(ns["SETTINGS"]["min_comp_usd"], 20000)

    def test_skill_weights_fold_to_a_lowercased_dict(self):
        weights = rendered_namespace()["SCORING"]["skill_weights"]
        self.assertEqual(weights["salesforce"], 10)
        # Discriminative power, not centrality: the craft term stays demoted.
        self.assertLess(weights["stakeholder management"], weights["salesforce"])

    def test_penalty_terms_are_negative(self):
        penalties = rendered_namespace()["SCORING"]["penalty_terms"]
        self.assertEqual(penalties, {"sap": -6, "oracle": -4})

    def test_penalties_stay_negative_even_if_the_model_sends_negatives(self):
        payload = dict(PAYLOAD, penalty_terms=[{"term": "SAP", "weight": -6}])
        self.assertEqual(
            rendered_namespace(payload)["SCORING"]["penalty_terms"], {"sap": -6})

    def test_excluded_levels_are_stripped_from_penalty_terms(self):
        # hard_drop_terms already deletes these rows; a penalty as well is dead
        # weight, and double-counts if drop_excluded is ever turned off.
        payload = dict(PAYLOAD, penalty_terms=[
            {"term": "SAP", "weight": 6},
            {"term": "Intern", "weight": 9},
            {"term": "fresher", "weight": 9},
        ])
        penalties = rendered_namespace(payload)["SCORING"]["penalty_terms"]
        self.assertEqual(penalties, {"sap": -6})

    def test_domain_halves_are_lowercased(self):
        # scraper.py:242 lowercases terms when compiling, so mixed case works —
        # but the file should read consistently with every other term list.
        payload = dict(PAYLOAD, domain_half_a=["React Native", "Expo"])
        halves = rendered_namespace(payload)["SCORING"]["frontend_terms"]
        self.assertEqual(halves, ["react native", "expo"])

    def test_title_terms_are_lowercased_and_name_the_platform(self):
        terms = rendered_namespace()["SCORING"]["fullstack_title_terms"]
        self.assertEqual(terms, ["salesforce business analyst"])

    def test_excluded_levels_land_in_hard_drop_terms(self):
        ns = rendered_namespace()
        self.assertEqual(ns["SCORING"]["hard_drop_terms"], ["intern", "fresher"])
        # soft_drop_terms is deliberately inherited from config.py, not emitted.
        self.assertNotIn("soft_drop_terms", ns["SCORING"])

    def test_empty_collections_render_without_crashing(self):
        payload = dict(PAYLOAD, domain_half_a=[], domain_half_b=[],
                       domain_title_terms=[], domain_bonus=0)
        ns = rendered_namespace(payload)
        self.assertEqual(ns["SCORING"]["frontend_terms"], [])
        self.assertEqual(ns["SCORING"]["fullstack_bonus"], 0)


class TestValidateKeys(unittest.TestCase):
    def test_real_config_keys_pass(self):
        make_profile.validate_keys({"SEARCH": ["role_keywords", "locations"]})

    def test_stale_key_names_are_rejected(self):
        # Both of these are named by RESUME_AUTOCONFIG_PROMPT.md and neither has
        # ever existed; profile merge would accept them in silence.
        with self.assertRaises(KeyError):
            make_profile.validate_keys({"SCORING": ["drop_terms"]})
        with self.assertRaises(KeyError):
            make_profile.validate_keys({"SETTINGS": ["min_ctc_lpa"]})


class TestGenerate(unittest.TestCase):
    def test_returns_parsed_json(self):
        client = FakeClient(PAYLOAD)
        data = make_profile.generate(client, "m", "résumé text", PREFS)
        self.assertEqual(data["years_experience"], 4)
        self.assertEqual(client.models.calls, 1)

    def test_retries_past_transient_503s(self):
        client = FakeClient(PAYLOAD, fails=4)
        data = make_profile.generate(client, "m", "résumé", PREFS, sleep=lambda s: None)
        self.assertEqual(data["years_experience"], 4)
        self.assertEqual(client.models.calls, 5)

    def test_gives_up_after_the_attempt_budget(self):
        client = FakeClient(PAYLOAD, fails=99)
        with self.assertRaises(RuntimeError):
            make_profile.generate(client, "m", "résumé", PREFS, sleep=lambda s: None)
        self.assertEqual(client.models.calls, 5)

    def test_does_not_retry_a_real_error(self):
        # A 404 on a retired model must fail on the first call, not burn the budget.
        client = FakeClient(PAYLOAD, fails=99, fail_with="404 NOT_FOUND")
        with self.assertRaises(RuntimeError):
            make_profile.generate(client, "m", "résumé", PREFS, sleep=lambda s: None)
        self.assertEqual(client.models.calls, 1)

    def test_prompt_carries_the_resume_and_the_stated_preferences(self):
        client = FakeClient(PAYLOAD)
        make_profile.generate(client, "m", "UNIQUE_RESUME_MARKER", PREFS)
        prompt = client.models.last_kwargs["contents"]
        self.assertIn("UNIQUE_RESUME_MARKER", prompt)
        self.assertIn("Bengaluru", prompt)
        self.assertIn("SAP", prompt)


class TestCli(unittest.TestCase):
    def test_preferences_a_resume_cannot_state_are_required(self):
        # Never guessed: argparse must reject a run that omits them.
        for argv in ([], ["--name", "x"], ["--name", "x", "--locations", "Pune"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                make_profile.main(argv)


if __name__ == "__main__":
    unittest.main()
