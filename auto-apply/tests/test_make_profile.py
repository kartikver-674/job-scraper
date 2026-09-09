import contextlib
import io
import json
import re
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

    def test_a_spend_cap_is_emitted_into_settings_when_given(self):
        # SETTINGS["max_spend_usd"] is the ONLY real spend guard: scraper.py
        # reads the account mid-sweep and refuses to launch another search
        # once it is crossed. config.py defaults it to None, and
        # `if budget is not None` means a profile that omits it has no cap at
        # all — so every profile Sweep generates has to carry one.
        ns = rendered_namespace(prefs=dict(PREFS, max_spend_usd=3.38))
        self.assertEqual(ns["SETTINGS"]["max_spend_usd"], 3.38)
        self.assertNotIn("This file sets no max_spend_usd", ns["__doc__"])

    def test_no_spend_cap_means_inherit_from_config_not_an_emitted_none(self):
        # Same rule as every other optional key: absent means "inherit".
        # Emitting None would write the no-cap value in and look deliberate.
        ns = rendered_namespace()
        self.assertNotIn("max_spend_usd", ns["SETTINGS"])
        self.assertIn("This file sets no max_spend_usd", ns["__doc__"])

    def test_empty_collections_render_without_crashing(self):
        payload = dict(PAYLOAD, domain_half_a=[], domain_half_b=[],
                       domain_title_terms=[], domain_bonus=0)
        ns = rendered_namespace(payload)
        self.assertEqual(ns["SCORING"]["frontend_terms"], [])
        self.assertEqual(ns["SCORING"]["fullstack_bonus"], 0)


class TestProfileNameFor(unittest.TestCase):
    """The résumé already names the person, so Sweep's review screen prefills
    the profile name from it. Whatever comes out has to satisfy the regex that
    same form validates against — [A-Za-z_][A-Za-z0-9_-]* — or it would
    prefill a value its own screen rejects."""

    NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")

    def name(self, raw):
        return make_profile.profile_name_for({"candidate_name": raw})

    def test_a_plain_name_becomes_a_slug(self):
        self.assertEqual(self.name("Kartik Verma"), "kartik_verma")

    def test_case_and_padding_do_not_survive(self):
        self.assertEqual(self.name("  KARTIK   VERMA  "), "kartik_verma")

    def test_accents_fold_to_their_base_letter(self):
        # Stripping them instead gave mar_a_pe_a for a real person's name.
        self.assertEqual(self.name("Ana-María Peña"), "ana-maria_pena")
        self.assertEqual(self.name("José Müller"), "jose_muller")

    def test_punctuation_collapses_rather_than_repeating(self):
        self.assertEqual(self.name("J. Doe-Smith"), "j_doe-smith")
        self.assertEqual(self.name("O'Brien"), "o_brien")

    def test_nothing_usable_yields_an_empty_name_not_a_bad_one(self):
        # The caller falls back to an empty field for the user to fill, which
        # is what the screen did for every résumé before this.
        for raw in ("", None, "李明", "3M Corp", "   ", "---"):
            self.assertEqual(self.name(raw), "", repr(raw))
        self.assertEqual(make_profile.profile_name_for({}), "")

    def test_the_result_never_ends_on_a_separator(self):
        self.assertEqual(self.name("Kartik_"), "kartik")
        # The truncation itself has to be cleaned up, not just the input:
        # this one puts the 40-character cut exactly on the separator.
        self.assertEqual(self.name("a" * 39 + " bcd"), "a" * 39)
        self.assertEqual(self.name("a" * 45 + " b"), "a" * 40)

    def test_every_produced_name_passes_the_forms_own_validator(self):
        for raw in ("Kartik Verma", "Ana-María Peña", "J. Doe-Smith",
                     "O'Brien", "José Müller", "a" * 60, "x  y  z"):
            out = self.name(raw)
            self.assertTrue(out, repr(raw))
            self.assertRegex(out, r"\A" + self.NAME_RE.pattern + r"\Z")
        # Inputs that must produce NOTHING rather than something invalid. A
        # leading digit is the case that matters: "3m_corp" is a fine-looking
        # slug that the form would reject and no module could be named.
        for raw in ("3M Corp", "42", "-dash", "_ _"):
            out = self.name(raw)
            self.assertTrue(out == "" or self.NAME_RE.fullmatch(out),
                            f"{raw!r} -> {out!r}")


class TestCandidateNameIsAsked(unittest.TestCase):
    def test_the_schema_requires_the_name_and_the_prompt_defines_it(self):
        # profile_name_for() can only work if the model is actually asked for
        # the field. Drop it from the schema and autofill reverts to an empty
        # box with nothing failing anywhere — so the request is pinned here.
        self.assertIn("candidate_name", make_profile.RESPONSE_SCHEMA["properties"])
        self.assertIn("candidate_name", make_profile.RESPONSE_SCHEMA["required"])
        prompt = make_profile.build_prompt("a résumé", {
            "locations": ["Remote"], "avoid": [], "exclude_levels": []})
        self.assertIn("candidate_name", prompt)
        # And told what NOT to do: a name guessed from an email address or a
        # file name becomes a filename in profiles/.
        self.assertIn("never a guess", prompt)


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
