import builtins
import contextlib
import csv
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

import make_profile
import local_extract

_REAL_CORPUS_ROOT = None
_CORPUS_TMP = None


def setUpModule():
    """No test in this module may read the developer's real output/.

    generate() re-scores the model's weights against the corpus, so every
    call reaches it — and a suite whose assertions depend on what was last
    scraped is a suite that fails for reasons nobody changed. Patched at the
    module level rather than per call site: the first attempt isolated the
    six calls that existed and missed the four in the model-ladder tests,
    which is the same lesson the sweep suite learned.
    """
    global _REAL_CORPUS_ROOT, _CORPUS_TMP
    import sys as _sys
    if make_profile.cfg.REPO_ROOT not in _sys.path:
        _sys.path.insert(0, make_profile.cfg.REPO_ROOT)
    import corpus_signal

    _CORPUS_TMP = tempfile.mkdtemp(prefix="aa-corpus-")
    _REAL_CORPUS_ROOT = corpus_signal.REPO_ROOT
    corpus_signal.REPO_ROOT = _CORPUS_TMP


def tearDownModule():
    import corpus_signal
    corpus_signal.REPO_ROOT = _REAL_CORPUS_ROOT
    shutil.rmtree(_CORPUS_TMP, ignore_errors=True)


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
    # The free-source title gate. This résumé is the reason it exists: every
    # company board and feed is filtered through config.ATS_TITLE_HINTS, which
    # is a generic SOFTWARE list, so a Salesforce consultant's generated
    # profile saw almost nothing free until the model started supplying these.
    "title_hints": ["Salesforce", "CRM Consultant", "apex"],
    "title_exclude": ["Data Engineer"],
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


class TestTitleGate(unittest.TestCase):
    """ATS_TITLE_HINTS decides what a free source is even scored on: every
    board returns its whole catalogue and scraper.is_dev_title() drops the rest
    BEFORE scoring. Five hand-written profiles set it; the generator never did,
    so every generated profile filtered its free sources through a list built
    for one React/Node résumé."""

    def test_the_resumes_own_titles_reach_the_profile(self):
        ns = rendered_namespace()
        for term in ("salesforce", "crm consultant", "apex"):
            self.assertIn(term, ns["ATS_TITLE_HINTS"])

    def test_the_generic_floor_is_never_lost(self):
        # Unioned, not replaced: a thin or eccentric model answer must not be
        # able to make a profile see LESS than the generic software list.
        ns = rendered_namespace()
        floor = set(make_profile._load_config().ATS_TITLE_HINTS)
        self.assertTrue(floor <= set(ns["ATS_TITLE_HINTS"]),
                        f"lost: {sorted(floor - set(ns['ATS_TITLE_HINTS']))}")

    def test_the_floor_is_copied_verbatim(self):
        # "java ", "ios " and "sre " carry a deliberate trailing space, which
        # is what stops them matching javascript, iOS-anything and "stressed".
        # Normalising the floor the way the model's terms are normalised
        # silently widened all three.
        ns = rendered_namespace()
        self.assertIn("java ", ns["ATS_TITLE_HINTS"])
        self.assertNotIn("java", ns["ATS_TITLE_HINTS"])

    def test_the_models_terms_are_normalised(self):
        ns = rendered_namespace(payload=dict(
            PAYLOAD, title_hints=["  Salesforce ", "APEX", "apex"]))
        hints = ns["ATS_TITLE_HINTS"]
        self.assertIn("salesforce", hints)
        self.assertEqual(hints.count("apex"), 1)
        self.assertNotIn("APEX", hints)

    def test_an_exclude_wins_over_a_hint(self):
        # is_dev_title checks excludes first, so a term on both lists would
        # delete itself — it must not be rendered as a hint at all.
        ns = rendered_namespace(payload=dict(
            PAYLOAD, title_hints=["salesforce", "data engineer"],
            title_exclude=["data engineer"]))
        self.assertNotIn("data engineer", ns["ATS_TITLE_HINTS"])
        self.assertIn("data engineer", ns["ATS_TITLE_EXCLUDE"])

    def test_a_payload_without_the_fields_still_renders(self):
        # Derivations cached before the schema grew these keys are still on
        # disk and still get re-rendered on every Configure change.
        bare = {k: v for k, v in PAYLOAD.items()
                if k not in ("title_hints", "title_exclude")}
        ns = rendered_namespace(payload=bare)
        self.assertEqual(ns["ATS_TITLE_EXCLUDE"], [])
        self.assertTrue(set(make_profile._load_config().ATS_TITLE_HINTS)
                        <= set(ns["ATS_TITLE_HINTS"]))

    def test_the_schema_requires_them_and_the_prompt_explains_them(self):
        for field in ("title_hints", "title_exclude"):
            self.assertIn(field, make_profile.RESPONSE_SCHEMA["properties"])
            self.assertIn(field, make_profile.RESPONSE_SCHEMA["required"])
        prompt = make_profile.build_prompt("a résumé", {
            "locations": ["Remote"], "avoid": [], "exclude_levels": []})
        self.assertIn("title_hints", prompt)
        # The failure mode has to be stated too, or the model emits
        # search-engine phrases instead of title fragments. It is spelled out
        # in the system instruction, which travels separately from the prompt
        # — both are asserted, because either one going missing is silent.
        self.assertIn("substrings", prompt)
        self.assertIn("RULE 3", make_profile.SYSTEM_INSTRUCTION)
        self.assertIn("is_dev_title", make_profile.SYSTEM_INSTRUCTION)


class TestHimalayasQueries(unittest.TestCase):
    """The feed's search endpoint takes one free-text query per request, and
    the résumé's role keywords are what to search for. Before this the adapter
    paged blind through ~96k mostly non-engineering jobs."""

    def test_the_role_keywords_become_the_search(self):
        ns = rendered_namespace()
        self.assertEqual(ns["FEEDS"]["himalayas"]["queries"],
                         PAYLOAD["role_keywords"])

    def test_the_whole_feed_entry_is_written(self):
        # config._overlay merges one level deep (FEEDS.update(override)), so a
        # partial {"himalayas": {"queries": [...]}} REPLACES the real entry and
        # takes "enabled" with it — turning the feed off while appearing to
        # configure it. Same trap _fmt_sites documents for SITES.
        ns = rendered_namespace()
        entry = ns["FEEDS"]["himalayas"]
        self.assertTrue(entry["enabled"])
        self.assertIn("pages", entry)

    def test_the_other_feeds_are_left_to_config(self):
        # dict.update merges per key, so an overlay naming only himalayas
        # leaves remoteok/wwr/remotive/jobicy exactly as config.py has them.
        ns = rendered_namespace()
        self.assertEqual(set(ns["FEEDS"]), {"himalayas"})

    def test_no_keywords_means_no_overlay_at_all(self):
        # Rather than an overlay with an empty query list, which would look
        # deliberate and still page blind.
        ns = rendered_namespace(payload=dict(PAYLOAD, role_keywords=[]))
        self.assertNotIn("FEEDS", ns)


class TestCorpusReweighting(unittest.TestCase):
    """RULE 1 asks the model how much a term narrows the market. output/ is
    the market, so the answer is measured and blended with the model's."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def corpus(self, rows):
        """A sweep directory shaped like a real one."""
        sweep = os.path.join(self.dir, "aprofile")
        os.makedirs(sweep, exist_ok=True)
        with open(os.path.join(sweep, "jobs.csv"), "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["title", "score",
                                               "matched_skills"])
            w.writeheader()
            for skills in rows:
                w.writerow({"title": "engineer", "score": "5",
                            "matched_skills": skills})
        return self.dir

    def payload(self, weights):
        return {"skill_weights": [{"term": t, "weight": w}
                                  for t, w in weights.items()]}

    def weights_of(self, data):
        return {e["term"]: e["weight"] for e in data["skill_weights"]}

    def test_a_commodity_term_is_demoted(self):
        # "react" in every listing separates nothing, whatever the résumé
        # says — which is RULE 1, measured instead of guessed.
        corpus = self.corpus(["react"] * 300)
        out = make_profile.reweight_from_corpus(
            self.payload({"react": 5}), corpus, log=lambda *a: None)
        self.assertEqual(self.weights_of(out)["react"], 2)

    def test_a_rare_term_the_resume_barely_mentions_is_not_promoted(self):
        # The failure that killed rarity-alone: opencv in 0.3% of listings
        # would top a React Native developer's weights and float every
        # computer-vision job to the front of the shortlist.
        corpus = self.corpus(["react"] * 299 + ["opencv"])
        out = make_profile.reweight_from_corpus(
            self.payload({"opencv": 1}), corpus, log=lambda *a: None)
        self.assertEqual(self.weights_of(out)["opencv"], 2)

    def test_strong_and_rare_stays_at_the_top(self):
        corpus = self.corpus(["react"] * 299 + ["maven"])
        out = make_profile.reweight_from_corpus(
            self.payload({"maven": 5}), corpus, log=lambda *a: None)
        self.assertEqual(self.weights_of(out)["maven"], 5)

    def test_an_empty_corpus_changes_nothing(self):
        # A first run has no output/ at all. Abstention is not a vote.
        stated = {"react": 5, "maven": 3}
        out = make_profile.reweight_from_corpus(
            self.payload(stated), self.dir, log=lambda *a: None)
        self.assertEqual(self.weights_of(out), stated)

    def test_the_original_payload_is_not_mutated(self):
        # Sweep keeps the derived payload on session state and re-renders
        # from it; rewriting it in place would make the weights depend on
        # how many times the screen was drawn.
        data = self.payload({"react": 5})
        make_profile.reweight_from_corpus(
            data, self.corpus(["react"] * 300), log=lambda *a: None)
        self.assertEqual(self.weights_of(data)["react"], 5)

    def test_it_says_what_it_changed(self):
        # These are the numbers that decide which jobs reach the top.
        lines = []
        make_profile.reweight_from_corpus(
            self.payload({"react": 5}), self.corpus(["react"] * 300),
            log=lines.append)
        said = " ".join(lines)
        self.assertIn("re-scored 1 weight(s)", said)
        self.assertIn("react", said)
        self.assertIn("5 -> 2", said)

    def test_a_weight_off_the_1_to_5_scale_is_left_alone(self):
        # RESPONSE_SCHEMA bounds weight only to "integer" and the renderer
        # has always passed it through. Re-bounding the scale is a different
        # change from measuring importance.
        out = make_profile.reweight_from_corpus(
            self.payload({"react": 10}), self.corpus(["react"] * 300),
            log=lambda *a: None)
        self.assertEqual(self.weights_of(out)["react"], 10)

    def test_a_payload_with_no_skills_is_returned_untouched(self):
        data = {"skill_weights": []}
        self.assertIs(make_profile.reweight_from_corpus(
            data, self.dir, log=lambda *a: None), data)


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
    """generate() now re-scores the model's weights against output/, so
    every case here passes an empty corpus: reading the developer's real
    sweeps would make these tests depend on what was last scraped, and read
    53 files to assert something about a prompt."""

    def setUp(self):
        self.empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.empty, ignore_errors=True)

    def gen(self, client, resume="résumé", **kw):
        kw.setdefault("output_dir", self.empty)
        return make_profile.generate(client, "m", resume, PREFS, **kw)

    def test_returns_parsed_json(self):
        client = FakeClient(PAYLOAD)
        data = self.gen(client, "résumé text")
        self.assertEqual(data["years_experience"], 4)
        self.assertEqual(client.models.calls, 1)

    def test_retries_past_transient_503s(self):
        client = FakeClient(PAYLOAD, fails=4)
        data = self.gen(client, sleep=lambda s: None)
        self.assertEqual(data["years_experience"], 4)
        self.assertEqual(client.models.calls, 5)

    def test_gives_up_after_the_attempt_budget(self):
        client = FakeClient(PAYLOAD, fails=99)
        with self.assertRaises(RuntimeError):
            self.gen(client, sleep=lambda s: None)
        self.assertEqual(client.models.calls, 5)

    def test_does_not_retry_a_real_error(self):
        # A 404 on a retired model must fail on the first call, not burn the budget.
        client = FakeClient(PAYLOAD, fails=99, fail_with="404 NOT_FOUND")
        with self.assertRaises(RuntimeError):
            self.gen(client, sleep=lambda s: None)
        self.assertEqual(client.models.calls, 1)

    def test_prompt_carries_the_resume_and_the_stated_preferences(self):
        client = FakeClient(PAYLOAD)
        self.gen(client, "UNIQUE_RESUME_MARKER")
        prompt = client.models.last_kwargs["contents"]
        self.assertIn("UNIQUE_RESUME_MARKER", prompt)
        self.assertIn("Bengaluru", prompt)
        self.assertIn("SAP", prompt)


class Truncated:
    """A response with no text part, the way the client library reports one.

    response.text is None whenever there are no candidates, no content or no
    parts — a refusal, or a budget spent thinking before anything was emitted.
    json.loads(None) then raises a TypeError that reads, three layers up, as
    "the model could not read that résumé".
    """

    def __init__(self, finish_reason="MAX_TOKENS", text=None):
        self.text = text
        reason = type("R", (), {"name": finish_reason})() if finish_reason else None
        content = type("C", (), {"parts": None})()
        self.candidates = [type("Cand", (), {"finish_reason": reason,
                                              "content": content})()]


class TestUnusableAnswers(unittest.TestCase):
    """A user hit this on a résumé that had parsed fine the day before, and the
    screen told them their PDF was a scan. Every one of these is the model
    call, not the file."""

    def _client(self, response):
        models = type("M", (), {"generate_content": lambda self, **kw: response,
                                 "calls": 0})()
        return type("C", (), {"models": models})()

    def test_no_answer_at_all_names_the_budget(self):
        with self.assertRaises(make_profile.ModelAnswerError) as caught:
            make_profile.generate(self._client(Truncated()), "m", "résumé", PREFS,
                                   sleep=lambda s: None)
        self.assertIn("output budget", str(caught.exception))
        self.assertIn(str(make_profile.MAX_OUTPUT_TOKENS), str(caught.exception))

    def test_a_refusal_says_so(self):
        with self.assertRaises(make_profile.ModelAnswerError) as caught:
            make_profile.generate(self._client(Truncated("SAFETY")), "m", "r",
                                   PREFS, sleep=lambda s: None)
        self.assertIn("refused", str(caught.exception))

    def test_an_unknown_reason_is_still_named_not_swallowed(self):
        with self.assertRaises(make_profile.ModelAnswerError) as caught:
            make_profile.generate(self._client(Truncated("OTHER")), "m", "r",
                                   PREFS, sleep=lambda s: None)
        self.assertIn("OTHER", str(caught.exception))

    def test_a_half_written_answer_is_not_a_json_error(self):
        # Truncation after some text lands as invalid JSON, which used to
        # surface as a JSONDecodeError with no explanation attached.
        half = Truncated(text='{"field_summary": "Salesforce cons')
        with self.assertRaises(make_profile.ModelAnswerError) as caught:
            make_profile.generate(self._client(half), "m", "r", PREFS,
                                   sleep=lambda s: None)
        self.assertIn("cut off", str(caught.exception))

    def test_an_unusable_answer_is_not_retried(self):
        # It is not transient: five attempts would spend five calls reaching
        # the same place. Counted, because raising happens either way.
        calls = []

        def once(**kw):
            calls.append(kw)
            return Truncated()

        client = FakeClient(PAYLOAD)
        client.models.generate_content = once
        with self.assertRaises(make_profile.ModelAnswerError):
            make_profile.generate(client, "m", "r", PREFS, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_a_503_is_still_retried(self):
        # The other half of the same branch: the transient one must keep its
        # budget, or a busy endpoint becomes a hard failure.
        client = FakeClient(PAYLOAD, fails=2)
        make_profile.generate(client, "m", "r", PREFS, sleep=lambda s: None)
        self.assertEqual(client.models.calls, 3)

    def test_the_reason_never_carries_upstream_text(self):
        # The vocabulary is the library's own finish_reason enum. str(exc) from
        # the client can carry the request URL, and .env holds the key.
        sneaky = Truncated("STOP")
        sneaky.candidates[0].finish_reason.name = "https://api?key=SECRET"
        with self.assertRaises(make_profile.ModelAnswerError) as caught:
            make_profile.generate(self._client(sneaky), "m", "r", PREFS,
                                   sleep=lambda s: None)
        # It is echoed only because the fixture forged the enum; what matters
        # is that a real client error never reaches the user — asserted in the
        # sweep suite, where the screen is rendered.
        self.assertIsInstance(caught.exception, make_profile.ModelAnswerError)

    def test_the_call_asks_for_an_output_budget(self):
        # gemini-3.6-flash thinks before it answers and both come out of the
        # same budget, so leaving it at the default is how a bigger schema
        # starts returning nothing.
        client = FakeClient(PAYLOAD)
        make_profile.generate(client, "m", "résumé", PREFS)
        cfg = client.models.last_kwargs["config"]
        self.assertEqual(cfg.max_output_tokens, make_profile.MAX_OUTPUT_TOKENS)
        self.assertGreaterEqual(make_profile.MAX_OUTPUT_TOKENS, 8192)


class Ladder:
    """A client whose models each fail in a stated way, or answer."""

    def __init__(self, outcomes, payload=None):
        self.outcomes = outcomes          # {model: Exception | "ok"}
        self.asked = []
        self.payload = payload or PAYLOAD
        outer = self

        class Models:
            def generate_content(self, **kw):
                model = kw["model"]
                outer.asked.append(model)
                result = outer.outcomes.get(model, RuntimeError("404 NOT_FOUND"))
                if isinstance(result, Exception):
                    raise result
                return FakeResponse(json.dumps(outer.payload))

        self.models = Models()


class TestModelLadder(unittest.TestCase):
    """Free-tier RPD is counted PER MODEL and resets on a clock. An exhausted
    primary is a reason to ask the next model, not to fail an upload — which
    is what it did, with a message blaming the user's PDF."""

    def _err(self, text):
        return RuntimeError(text)

    def test_an_exhausted_model_falls_through_to_the_next(self):
        client = Ladder({"a": self._err("429 RESOURCE_EXHAUSTED"), "b": "ok"})
        data = make_profile.generate(client, ("a", "b"), "résumé", PREFS,
                                      sleep=lambda s: None, log=lambda m: None)
        self.assertEqual(data["field_summary"], PAYLOAD["field_summary"])
        self.assertEqual(client.asked, ["a", "b"])

    def test_a_retired_model_does_not_stop_the_ladder(self):
        # "Retired models 404 with 'no longer available', which is silent
        # until you spend" — and only the first id on this ladder is one the
        # repo has measured.
        client = Ladder({"a": self._err("404 model not found"), "b": "ok"})
        make_profile.generate(client, ("a", "b"), "r", PREFS,
                              sleep=lambda s: None, log=lambda m: None)
        self.assertEqual(client.asked, ["a", "b"])

    def test_the_first_model_that_answers_wins(self):
        client = Ladder({"a": "ok", "b": "ok"})
        make_profile.generate(client, ("a", "b"), "r", PREFS, log=lambda m: None)
        self.assertEqual(client.asked, ["a"])

    def test_a_real_error_is_not_walked_down_the_ladder(self):
        # An auth failure or a bad schema fails identically on every model, so
        # walking them spends calls to reach the same place.
        client = Ladder({"a": self._err("401 API key not valid"), "b": "ok"})
        with self.assertRaises(RuntimeError):
            make_profile.generate(client, ("a", "b"), "r", PREFS,
                                   sleep=lambda s: None, log=lambda m: None)
        self.assertEqual(client.asked, ["a"])

    def test_a_503_is_retried_on_the_same_model_before_moving_on(self):
        # 503 is about the endpoint being busy, not about which model was
        # asked, so the ladder must not eat the retry budget.
        seen = []
        client = Ladder({"a": self._err("503 UNAVAILABLE"), "b": "ok"})
        make_profile.generate(client, ("a", "b"), "r", PREFS, attempts=3,
                              sleep=lambda s: seen.append(s), log=lambda m: None)
        self.assertEqual(client.asked, ["a", "a", "a", "b"])
        self.assertTrue(seen, "the 503 backoff should still sleep")

    def test_a_ladder_that_is_only_busy_is_not_called_exhausted(self):
        # Nothing is out of quota, so the error must stay the 503 the CLI's
        # own branch reads — not a reset time for a quota that is fine.
        client = Ladder({"a": self._err("503 UNAVAILABLE"),
                         "b": self._err("503 UNAVAILABLE")})
        with self.assertRaises(RuntimeError) as caught:
            make_profile.generate(client, ("a", "b"), "r", PREFS, attempts=2,
                                   sleep=lambda s: None, log=lambda m: None)
        self.assertNotIsInstance(caught.exception, make_profile.QuotaExhausted)
        self.assertIn("503", str(caught.exception))

    def test_everything_exhausted_says_when_it_comes_back(self):
        client = Ladder({"a": self._err("429 RESOURCE_EXHAUSTED"),
                         "b": self._err("429 RESOURCE_EXHAUSTED")})
        with self.assertRaises(make_profile.QuotaExhausted) as caught:
            make_profile.generate(client, ("a", "b"), "r", PREFS,
                                   sleep=lambda s: None, log=lambda m: None)
        message = str(caught.exception)
        self.assertIn("midnight Pacific", message)
        self.assertIn("from now", message)
        # And which models were spent, so the ladder can be widened.
        self.assertIn("a, b", message)

    def test_a_quota_failure_is_a_model_answer_error(self):
        # So the screen shows the reason instead of the fixed "your PDF is
        # probably a scan" message.
        self.assertTrue(issubclass(make_profile.QuotaExhausted,
                                    make_profile.ModelAnswerError))

    def test_the_shipped_ladder_has_somewhere_to_fall(self):
        # A one-entry ladder is the behaviour this replaced: RPD is counted
        # per model, so the fallbacks ARE the feature.
        import apply_config as cfg
        self.assertGreater(len(cfg.MODELS), 1)
        self.assertEqual(cfg.MODELS[0], cfg.MODEL)
        self.assertEqual(len(set(cfg.MODELS)), len(cfg.MODELS))

    def test_a_bare_model_id_still_works(self):
        # apply.py and older callers pass one string.
        client = Ladder({"a": "ok"})
        make_profile.generate(client, "a", "r", PREFS, log=lambda m: None)
        self.assertEqual(client.asked, ["a"])


class TestQuotaReset(unittest.TestCase):
    """ai.google.dev/gemini-api/docs/rate-limits: "Requests per day (RPD)
    quotas reset at midnight Pacific time." So the wait is answerable exactly,
    and "try again later" is a worse answer than the time."""

    def _at(self, hour, minute=0, month=9, day=9):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, month, day, hour, minute,
                        tzinfo=ZoneInfo("America/Los_Angeles"))

    def test_it_counts_to_the_next_pacific_midnight(self):
        _, left = make_profile.quota_reset(self._at(23, 30))
        self.assertEqual(left, "30m")

    def test_just_after_midnight_is_nearly_a_whole_day(self):
        _, left = make_profile.quota_reset(self._at(0, 5))
        self.assertEqual(left, "23h 55m")

    def test_the_time_is_shown_where_the_reader_is(self):
        # The quota is Pacific; the person waiting for it is not.
        at, _ = make_profile.quota_reset(self._at(12))
        self.assertTrue(at, "a local time must be rendered")
        self.assertNotIn("PST", at + " ")   # unless the reader is in Pacific
        self.assertNotIn("PDT", at + " ")

    def test_it_counts_to_pacific_midnight_from_anywhere(self):
        # The caller's clock is not Pacific. Counting to midnight in the
        # reader's own zone would be right only in California — from India it
        # is out by half a day.
        from datetime import datetime, timedelta, timezone
        ist = timezone(timedelta(hours=5, minutes=30))
        # 09:00 IST is 20:30 the previous day in Pacific: 3h 30m to reset.
        _, left = make_profile.quota_reset(datetime(2026, 9, 10, 9, 0, tzinfo=ist))
        self.assertEqual(left, "3h 30m")

    def test_it_survives_the_dst_boundary(self):
        # Pacific shifts by an hour in November; a hardcoded UTC offset would
        # put the answer an hour out for half the year.
        _, november = make_profile.quota_reset(self._at(12, month=11, day=20))
        _, july = make_profile.quota_reset(self._at(12, month=7, day=20))
        self.assertEqual(november, july)


class TestCli(unittest.TestCase):
    def test_preferences_a_resume_cannot_state_are_required(self):
        # Never guessed: argparse must reject a run that omits them.
        for argv in ([], ["--name", "x"], ["--name", "x", "--locations", "Pune"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                make_profile.main(argv)


if __name__ == "__main__":
    unittest.main()


class TestWidenSkills(unittest.TestCase):
    """The market-vocabulary scan, wired in before reweight_from_corpus.

    A model reads a skills section well and under-reads prose, so about a
    third of the technologies on a real résumé never reach the profile.
    These cover the seam and the gate, not the scanner — skill_scan has
    its own self-check.
    """

    RESUME = ("Technical Skills\n"
              "Frontend: Lightning Web Components, Aura\n"
              "Experience\n"
              "- Built with Apex, LWC, SOQL for a field sales platform.\n")
    VOCAB = {"lwc": 35, "lightning web components": 33, "apex": 45,
             "aura": 20, "field sales": 4, "soql": 28}

    def _widen(self, data, text=None):
        import sys as _sys
        if make_profile.cfg.REPO_ROOT not in _sys.path:
            _sys.path.insert(0, make_profile.cfg.REPO_ROOT)
        import skill_scan

        return skill_scan.widen(data, self.RESUME if text is None else text,
                                log=lambda *a: None, vocab=self.VOCAB)

    def test_it_adds_a_skill_the_model_did_not_report(self):
        out = self._widen({"skill_weights": [{"term": "aura", "weight": 4}]})
        self.assertIn("lightning web components",
                      {e["term"] for e in out["skill_weights"]})

    def test_a_real_technology_named_once_in_prose_is_still_rejected(self):
        # Apex is a genuine skill and appears once, in a bullet, with no
        # expansion listed. The gate cannot tell it from "field sales
        # platform" and does not try: one passing mention is context.
        # This is the recall the gate trades away for precision — a
        # deliberate cost, not an oversight.
        out = self._widen({"skill_weights": []})
        self.assertNotIn("apex", {e["term"] for e in out["skill_weights"]})

    def test_an_abbreviation_inherits_its_expansions_evidence(self):
        # LWC appears once, in a bullet. Its expansion is in the skills
        # list two lines above, so it is a claim and not passing context.
        out = self._widen({"skill_weights": []})
        self.assertIn("lwc", {e["term"] for e in out["skill_weights"]})

    def test_a_one_off_product_noun_is_not_a_skill(self):
        # "field sales platform" describes what was built, not a skill.
        out = self._widen({"skill_weights": []})
        self.assertNotIn("field sales",
                         {e["term"] for e in out["skill_weights"]})

    def test_the_models_own_weight_is_never_overwritten(self):
        out = self._widen({"skill_weights": [{"term": "aura", "weight": 5}]})
        aura = [e for e in out["skill_weights"] if e["term"] == "aura"]
        self.assertEqual(aura, [{"term": "aura", "weight": 5}])

    def test_every_added_skill_says_where_it_came_from(self):
        out = self._widen({"skill_weights": [], "notes": "original"})
        # The model's own remark survives, the added terms are named,
        # and the per-term reasons live in skills_added rather than in
        # prose a person has to read past.
        self.assertTrue(out["notes"].startswith("original"))
        self.assertIn("lwc", out["notes"])
        self.assertIn("its expansion", out["skills_added"]["lwc"])

    def test_the_notes_stay_short_however_much_is_added(self):
        # notes is prose a person reads on the review screen and in the
        # profile docstring. Kavya's résumé yields around thirty scanned
        # terms; thirty parenthetical explanations there would bury the
        # sentences that actually describe the parse.
        import sys as _sys
        if make_profile.cfg.REPO_ROOT not in _sys.path:
            _sys.path.insert(0, make_profile.cfg.REPO_ROOT)
        import skill_scan

        vocab = {f"skill {i}": 40 for i in range(20)}
        text = "Technical Skills\n" + ", ".join(vocab) + "\n"
        out = skill_scan.widen({"skill_weights": []}, text, vocab=vocab,
                               log=lambda *a: None)
        self.assertGreaterEqual(len(out["skills_added"]), 20)
        self.assertLess(len(out["notes"]), 250)
        self.assertIn("more", out["notes"])

    def test_the_reasons_survive_outside_the_prose(self):
        out = self._widen({"skill_weights": []})
        self.assertIn("its expansion", out["skills_added"]["lwc"])
        self.assertNotIn("its expansion", out["notes"])

    def test_the_notes_are_deterministic(self):
        first = self._widen({"skill_weights": []})["notes"]
        second = self._widen({"skill_weights": []})["notes"]
        self.assertEqual(first, second)

    def test_render_carries_every_reason_into_the_profile(self):
        out = self._widen({"skill_weights": []})
        data = dict(_MINIMAL_PROFILE, **out)
        source = make_profile.render("demo", data, _MINIMAL_PREFS)
        for term, why in out["skills_added"].items():
            self.assertIn(term, source)
            self.assertIn(why, source)
        # And they are comments beside the weights, not docstring prose.
        self.assertIn("#   lwc", source)

    def test_render_is_unchanged_when_nothing_was_added(self):
        source = make_profile.render("demo", dict(_MINIMAL_PROFILE),
                                     _MINIMAL_PREFS)
        self.assertNotIn("named by the market", source)

    def test_the_callers_data_is_not_mutated(self):
        data = {"skill_weights": [{"term": "aura", "weight": 4}]}
        self._widen(data)
        self.assertEqual(data["skill_weights"],
                         [{"term": "aura", "weight": 4}])

    def test_no_vocabulary_means_no_change(self):
        import sys as _sys
        if make_profile.cfg.REPO_ROOT not in _sys.path:
            _sys.path.insert(0, make_profile.cfg.REPO_ROOT)
        import skill_scan

        data = {"skill_weights": [{"term": "aura", "weight": 4}]}
        # An absent or unscraped output/ yields an empty vocabulary, and
        # the profile must pass through untouched rather than empty.
        self.assertIs(skill_scan.widen(data, self.RESUME, vocab={},
                                       log=lambda *a: None), data)
        self.assertIs(skill_scan.widen(data, "", vocab=self.VOCAB,
                                       log=lambda *a: None), data)

    def test_generate_widens_before_rescoring(self):
        # The seam itself: generate() must call this, or none of the
        # above reaches a real profile.
        seen = {}
        real = make_profile.widen_skills

        def spy(data, resume_text, output_dir=None, log=print):
            seen["called_with"] = resume_text
            return real(data, resume_text, output_dir, log)

        with _patched(make_profile, "widen_skills", spy), \
                _patched(make_profile, "_generate_one",
                         lambda *a, **k: {"skill_weights": []}):
            make_profile.generate(None, ("m",), self.RESUME, {},
                                  log=lambda *a: None)
        self.assertEqual(seen.get("called_with"), self.RESUME)


_MINIMAL_PROFILE = {
    "candidate_name": "X", "field_summary": "A developer.",
    "years_experience": 2, "role_keywords": ["dev"], "skill_weights": [],
    "penalty_terms": [], "domain_half_a": [], "domain_half_b": [],
    "domain_title_terms": [], "title_hints": [], "title_exclude": [],
    "domain_bonus": 0, "notes": "n",
}

_MINIMAL_PREFS = {
    "locations": ["X"], "min_comp_usd": None, "exclude_levels": [],
    "avoid": [], "max_results": None, "max_spend_usd": None,
    "max_age_days": None, "remote_scopes": None, "linkedin_locations": None,
}


@contextlib.contextmanager
def _patched(module, name, value):
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, original)


class TestYearsExperience(unittest.TestCase):
    """The number is derived, never asked for — and it is a release gate.

    Two production consumers read it and BOTH drop rows when it is wrong:
    SEARCH["experience_years"] becomes LinkedIn's f_E band, and
    SETTINGS["max_experience_years"] is years + 3, which deletes any
    posting demanding more. An under-read removes reachable jobs before
    scoring ever runs, so there is no threshold that can recover them.

    Asked directly, the local model was off by three or more years on 12
    of the 52 benchmark documents. These are those twelve cases plus each
    clause of the definition.
    """

    NOW = (2026, 9)

    def years(self, rows, **kw):
        return local_extract.years_from(rows, self.NOW, **kw)

    def test_completed_years_are_floored_never_rounded(self):
        # ada: Jan 2021-Mar 2023 then Apr 2023-present is 5y7m. round()
        # made it 6 and disagreed with the answer key on the control case.
        rows = [{"title": "Backend Engineer", "start": "Jan 2021",
                 "end": "Mar 2023", "relevant": True},
                {"title": "Senior Backend Engineer", "start": "Apr 2023",
                 "end": "Present", "relevant": True}]
        self.assertEqual(self.years(rows), 5)

    def test_internships_do_not_count(self):
        # bhaskar's answer key is 0 and he has a three-month internship.
        self.assertEqual(self.years(
            [{"title": "Software Engineering Intern", "start": "May 2025",
              "end": "Jul 2025", "relevant": True}]), 0)
        # lena: an internship converted at the same employer is 1, not 2.
        lena = [{"title": "Engineering Intern", "start": "Jan 2024",
                 "end": "Jan 2025", "relevant": True},
                {"title": "Software Engineer", "start": "Jan 2025",
                 "end": "Jan 2026", "relevant": True}]
        self.assertEqual(self.years(lena), 1)
        self.assertEqual(
            self.years([dict(r, title="Engineer") for r in lena]), 2,
            "without the internship clause lena reads as two years")

    def test_a_prior_career_is_excluded_and_the_exclusion_is_visible(self):
        # hana: 12 years in the workforce, 4 in the field she is targeting.
        # Answering 12 filters for director-level roles she will not get.
        hana = [{"title": "Structural Engineer", "start": "Jan 2014",
                 "end": "Dec 2021", "relevant": False},
                {"title": "ML Engineer", "start": "Jan 2022",
                 "end": "Present", "relevant": True}]
        self.assertEqual(self.years(hana), 4)
        self.assertEqual(self.years(hana, ignore_relevance=True), 12)

    def test_concurrent_roles_count_once(self):
        # mateo held two real jobs at once; summing gives him six.
        mateo = [{"title": "Engineer", "start": "Jan 2022", "end": "Jan 2026",
                  "relevant": True},
                 {"title": "Consultant", "start": "Jan 2023",
                  "end": "Jan 2025", "relevant": True}]
        self.assertEqual(self.years(mateo), 4)
        naive = sum(local_extract.months_between(
            local_extract.parse_month(r["start"], self.NOW),
            local_extract.parse_month(r["end"], self.NOW)) for r in mateo) // 12
        self.assertEqual(naive, 6, "the wrong answer this clause prevents")

    def test_gaps_are_not_counted(self):
        # jonas: two three-year spans either side of two years out.
        jonas = [{"title": "Dev", "start": "Jan 2015", "end": "Jan 2018",
                  "relevant": True},
                 {"title": "Dev", "start": "Jan 2020", "end": "Jan 2023",
                  "relevant": True}]
        self.assertEqual(self.years(jonas), 6)
        self.assertEqual(local_extract.months_between(
            (2015, 1), (2023, 1)) // 12, 8,
            "first-to-last gives jonas two years he did not work")

    def test_part_time_is_counted_not_prorated(self):
        # Sweep is asking how senior a role fits, and two years of
        # part-time work is two years of standing.
        self.assertEqual(self.years(
            [{"title": "Engineer (16h/week)", "start": "Jan 2024",
              "end": "Jan 2026", "relevant": True}]), 2)

    def test_present_tracks_the_real_clock(self):
        # A frozen default would silently stop advancing, and every
        # current role would start shrinking a month at a time.
        rows = [{"title": "Dev", "start": f"Jan {local_extract.today()[0] - 3}",
                 "end": "present", "relevant": True}]
        self.assertEqual(local_extract.years_from(rows), 3)

    def test_unreadable_dates_contribute_nothing_rather_than_raising(self):
        self.assertEqual(self.years(
            [{"title": "Dev", "start": "sometime", "end": "later",
              "relevant": True}]), 0)
        self.assertEqual(self.years([]), 0)
        self.assertEqual(self.years(None), 0)

    def test_the_dates_a_resume_actually_writes(self):
        for text, want in (("Apr 2023", (2023, 4)), ("04/2023", (2023, 4)),
                           ("2023", (2023, 1)), ("September 2017", (2017, 9)),
                           ("2019-06", (2019, 6)),
                           ("present", (2026, 9)), ("Till Date", (2026, 9))):
            self.assertEqual(local_extract.parse_month(text, self.NOW), want,
                             text)
        for text in ("", "shortly", None):
            self.assertIsNone(local_extract.parse_month(text, self.NOW), text)

    def test_the_twelve_documents_the_model_got_wrong(self):
        """Every benchmark case where the direct answer was off by 3+.

        (person, the model's own figure, the answer key). Each row set
        reproduces that document's STRUCTURE and its answer — the reason
        the direct question failed — rather than transcribing the résumé,
        which lives in bench/people.py and is measured there at 52/52.
        The four people below cover all twelve failing documents: chen,
        farida and gopal at four layouts each, hana at two.
        """
        cases = [
            # chen: 11 years over six employers with internal promotions;
            # the model read one span and said 6-7.
            ("chen", 7, 11, [
                {"title": "Software Engineer", "start": "Jan 2015",
                 "end": "Jan 2018", "relevant": True},
                {"title": "Senior Software Engineer", "start": "Jan 2018",
                 "end": "Jan 2021", "relevant": True},
                {"title": "Staff Engineer", "start": "Jan 2021",
                 "end": "Jan 2024", "relevant": True},
                {"title": "Principal Engineer", "start": "Jan 2024",
                 "end": "Jan 2026", "relevant": True}]),
            # farida: dates everywhere — courses, publications, certs —
            # and the model counted the wrong ones.
            ("farida", 3, 6, [
                {"title": "Data Scientist", "start": "Mar 2020",
                 "end": "Aug 2023", "relevant": True},
                {"title": "Senior Data Scientist", "start": "Sep 2023",
                 "end": "Present", "relevant": True}]),
            # gopal: a tabular résumé; the model read two of three rows.
            ("gopal", 6, 9, [
                {"title": "Analyst", "start": "Jul 2017", "end": "Jul 2020",
                 "relevant": True},
                {"title": "Senior Analyst", "start": "Jul 2020",
                 "end": "Jul 2023", "relevant": True},
                {"title": "Lead Analyst", "start": "Jul 2023",
                 "end": "Jul 2026", "relevant": True}]),
            # hana: the OPPOSITE error — the model over-read, counting a
            # career she left. This is the one that filters for roles she
            # will not get rather than dropping ones she would.
            ("hana", 8, 4, [
                {"title": "Structural Engineer", "start": "Jan 2014",
                 "end": "Dec 2021", "relevant": False},
                {"title": "ML Engineer", "start": "Jan 2022",
                 "end": "Present", "relevant": True}]),
        ]
        for who, stated, want, rows in cases:
            with self.subTest(who):
                self.assertEqual(self.years(rows), want)
                self.assertNotEqual(stated, want, "not a case of the two agreeing")
                # And the router logs it as a correction with both figures,
                # so a wrong number is never applied in silence.
                fixes, stops = local_extract.check_years(
                    {"years_experience": stated}, {"employment": rows},
                    self.NOW)
                self.assertEqual(stops, [])
                self.assertEqual(fixes[0]["from"], stated)
                self.assertEqual(fixes[0]["to"], want)

    def test_an_unreadable_history_escalates_rather_than_answering_zero(self):
        # Zero would render max_experience_years = 3 and drop most of the
        # market. Escalating asks Gemini instead, which is the point of
        # keeping it behind the flag.
        _fixes, stops = local_extract.check_years(
            {"years_experience": 4},
            {"employment": [{"title": "Dev", "start": "?", "end": "?",
                             "relevant": True}]}, self.NOW)
        self.assertTrue(stops)
        self.assertIn("could be read", stops[0])

    def test_a_history_entirely_flagged_as_another_career_escalates(self):
        _fixes, stops = local_extract.check_years(
            {"years_experience": 3},
            {"employment": [{"title": "Teacher", "start": "Jan 2014",
                             "end": "Dec 2021", "relevant": False}]},
            self.NOW)
        self.assertTrue(stops)
        self.assertIn("different career", stops[0])

    def test_the_rendered_profile_carries_the_derived_number_both_places(self):
        # The two consumers, asserted on the rendered source rather than
        # on the dict, because that is what the scraper imports.
        source = make_profile.render(
            "demo", dict(_MINIMAL_PROFILE, years_experience=4),
            _MINIMAL_PREFS)
        self.assertIn('"experience_years": 4,', source)
        self.assertIn('"max_experience_years": 7,', source)


class TestEngineSelection(unittest.TestCase):
    """Which engine reads the résumé, and that turning it on is deliberate."""

    def test_the_default_is_still_gemini(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(make_profile.engine_name(), "gemini")

    def test_the_environment_selects_it_so_neither_entry_point_needs_a_flag(self):
        with mock.patch.dict(os.environ,
                             {make_profile.ENGINE_ENV: "local-first"}):
            self.assertEqual(make_profile.engine_name(), "local-first")
        # An explicit argument wins over the environment.
        with mock.patch.dict(os.environ, {make_profile.ENGINE_ENV: "local"}):
            self.assertEqual(make_profile.engine_name("gemini"), "gemini")

    def test_an_unknown_engine_is_an_error_not_a_fallback_to_gemini(self):
        # Falling back would spend a call the user asked not to spend.
        with mock.patch.dict(os.environ, {make_profile.ENGINE_ENV: "locl"}):
            with self.assertRaises(ValueError):
                make_profile.engine_name()

    def test_the_local_engine_never_calls_gemini(self):
        calls = []

        def spy(*a, **kw):
            calls.append(a)
            raise AssertionError("Gemini must not be called")

        with _patched(make_profile, "_generate_one", spy), \
             _patched(make_profile, "generate_local",
                      lambda *a, **kw: dict(_MINIMAL_PROFILE)):
            got = make_profile.generate(None, ("m",), "résumé", {},
                                        engine="local", log=lambda *a: None)
        self.assertEqual(calls, [])
        self.assertEqual(got["candidate_name"], "X")

    def test_local_first_falls_back_to_gemini_on_an_escalation(self):
        import local_profile
        reached = []

        def escalate(*a, **kw):
            raise local_profile.Escalated(["dates could not be read"])

        def gemini(client, model, resume_text, prefs, attempts, sleep, log):
            reached.append(model)
            return dict(_MINIMAL_PROFILE, candidate_name="from-gemini")

        with _patched(make_profile, "generate_local", escalate), \
             _patched(make_profile, "_generate_one", gemini):
            got = make_profile.generate(object(), ("m",), "résumé", {},
                                        engine="local-first",
                                        log=lambda *a: None)
        self.assertEqual(reached, ["m"])
        self.assertEqual(got["candidate_name"], "from-gemini")

    def test_the_local_engine_re_raises_instead_of_falling_back(self):
        import local_profile

        def escalate(*a, **kw):
            raise local_profile.Escalated(["dates could not be read"])

        with _patched(make_profile, "generate_local", escalate):
            with self.assertRaises(local_profile.Escalated):
                make_profile.generate(object(), ("m",), "résumé", {},
                                      engine="local", log=lambda *a: None)

    def test_a_missing_local_model_falls_back_but_only_if_asked_to(self):
        # No Ollama is the commonest local failure and must not fail an
        # upload when Gemini is available — and must not be swallowed
        # when the user chose local outright.
        def missing(*a, **kw):
            raise local_extract.ModelUnavailable("Ollama is not running")

        with _patched(make_profile, "generate_local", missing), \
             _patched(make_profile, "_generate_one",
                      lambda *a: dict(_MINIMAL_PROFILE)):
            got = make_profile.generate(object(), ("m",), "r", {},
                                        engine="local-first",
                                        log=lambda *a: None)
        self.assertEqual(got["candidate_name"], "X")

        with _patched(make_profile, "generate_local", missing):
            with self.assertRaises(local_extract.ModelUnavailable):
                make_profile.generate(object(), ("m",), "r", {},
                                      engine="local", log=lambda *a: None)

    def test_local_first_cannot_fall_back_with_no_client(self):
        # A missing key plus a local escalation has nowhere to go, and
        # must say so rather than raising something unrelated.
        import local_profile

        def escalate(*a, **kw):
            raise local_profile.Escalated(["no"])

        with _patched(make_profile, "generate_local", escalate):
            with self.assertRaises(local_profile.Escalated):
                make_profile.generate(None, ("m",), "r", {},
                                      engine="local-first",
                                      log=lambda *a: None)

    def test_every_engine_goes_through_widen_and_reweight(self):
        # The scanned-skill widening and the corpus re-scoring are not
        # Gemini's; a profile that skipped them would be scored on a
        # different basis from every other one.
        order = []
        with _patched(make_profile, "widen_skills",
                      lambda d, *a, **kw: (order.append("widen"), d)[1]), \
             _patched(make_profile, "reweight_from_corpus",
                      lambda d, *a, **kw: (order.append("reweight"), d)[1]), \
             _patched(make_profile, "_generate_one",
                      lambda *a: dict(_MINIMAL_PROFILE)):
            make_profile.generate(object(), ("m",), "r", {},
                                  engine="gemini", log=lambda *a: None)
            import local_profile
            with _patched(local_profile, "generate",
                          lambda *a, **kw: dict(_MINIMAL_PROFILE)):
                make_profile.generate(None, ("m",), "r", {},
                                      engine="local", log=lambda *a: None)
        self.assertEqual(order, ["widen", "reweight", "widen", "reweight"])


class TestLocalProfile(unittest.TestCase):
    """The local engine's output, in the shape render() demands."""

    RESUME = ("Lovish Kumar\n"
              "Salesforce Developer at Acme Cloud, Jan 2023 - Present\n"
              "Skills: apex, soql, lwc, javascript, SOLID principles\n")

    FIELDS = {"name": "Lovish Kumar", "years_experience": 9,
              "titles": ["Salesforce Developer"],
              "skills": ["apex", "soql", "lwc", "javascript",
                         "solid principles"],
              "companies": ["Acme Cloud"], "education": [],
              "institutions": [], "projects": [], "certifications": []}

    ROWS = {"target_field": "software engineering", "employment": [
        {"company": "Acme Cloud", "title": "Salesforce Developer",
         "start": "Jan 2023", "end": "Present", "relevant": True}]}

    def market(self):
        import local_search
        rows = [("salesforce developer", 25 if i % 2 else 5,
                 frozenset({"apex", "soql", "lwc"}), f"sfco{i % 20}")
                for i in range(60)]
        rows += [(f"software engineer {i % 7}", 3, frozenset({"java", "sql"}),
                  f"bigco{i % 25}") for i in range(400)]
        return local_search.Market(rows=rows,
                                   seniority=("senior", "staff", "lead"))

    def build(self, prefs=None, fields=None, rows=None):
        import local_extract as le
        import local_profile
        with _patched(le, "extract", lambda m, t, *a, **k: (
                fields or self.FIELDS, 0.1)), \
             _patched(le, "employment", lambda m, t, *a, **k: rows or self.ROWS):
            return local_profile.generate(
                self.RESUME, prefs or {"avoid": []},
                market=self.market(), log=lambda *a: None)

    def test_it_renders_to_an_importable_profile(self):
        # The real bar: not that the dict looks right, but that the file
        # the scraper imports is valid Python with the right numbers.
        got = self.build()
        source = make_profile.render("demo_local", got, _MINIMAL_PREFS)
        namespace = {}
        exec(compile(source, "demo_local.py", "exec"), namespace)
        self.assertEqual(namespace["SEARCH"]["experience_years"], 3)
        self.assertEqual(namespace["SETTINGS"]["max_experience_years"], 6)
        self.assertIn("salesforce developer",
                      namespace["SEARCH"]["role_keywords"])
        self.assertTrue(namespace["ATS_TITLE_HINTS"])
        self.assertEqual(namespace["ATS_TITLE_EXCLUDE"], [])

    def test_the_years_are_computed_not_the_models_answer(self):
        got = self.build()
        self.assertEqual(got["years_experience"], 3)
        self.assertNotEqual(got["years_experience"],
                            self.FIELDS["years_experience"])
        self.assertIn("the model said 9", got["notes"])

    def test_concept_filler_is_dropped_and_real_skills_are_not(self):
        terms = {w["term"] for w in self.build()["skill_weights"]}
        self.assertNotIn("solid principles", terms)
        self.assertLessEqual({"apex", "soql", "lwc"}, terms)

    def test_the_persons_own_title_leads_the_keywords(self):
        got = self.build()
        self.assertEqual(got["role_keywords"][0], "salesforce developer")
        self.assertEqual(got["local_ranking"][0][1], 1)
        self.assertIn("held", got["local_ranking"][0][2])

    def test_weights_are_neutral_because_the_corpus_sets_them_next(self):
        import local_profile
        weights = {w["weight"] for w in self.build()["skill_weights"]}
        self.assertEqual(weights, {local_profile.NEUTRAL_WEIGHT})

    def test_only_the_users_own_avoid_list_becomes_a_penalty(self):
        import local_profile
        got = self.build(prefs={"avoid": ["CRM", "  ", "Java"]})
        self.assertEqual(got["penalty_terms"],
                         [{"term": "crm", "weight": local_profile.AVOID_SEVERITY},
                          {"term": "java", "weight": local_profile.AVOID_SEVERITY}])

    def test_the_deliberately_empty_fields_stay_empty(self):
        # title_exclude is checked FIRST by scraper.is_dev_title, so a
        # wrong entry deletes a target role before anything scores it —
        # and the local answers measured 46% wrong. config.py's own base
        # ships zero entries too.
        got = self.build()
        for field in ("domain_half_a", "domain_half_b", "domain_title_terms",
                      "title_exclude"):
            self.assertEqual(got[field], [], field)
        self.assertEqual(got["domain_bonus"], 0)

    def test_it_emits_every_key_render_reads(self):
        got = self.build()
        for key in make_profile.RESPONSE_SCHEMA["required"]:
            self.assertIn(key, got, key)

    def test_a_confabulated_answer_escalates_rather_than_rendering(self):
        import local_profile
        with self.assertRaises(local_profile.Escalated) as caught:
            self.build(fields=dict(self.FIELDS,
                                   skills=["cobol", "fortran", "rpg"]))
        self.assertIn("not found in the document", str(caught.exception))

    def test_an_empty_corpus_still_yields_the_persons_own_titles(self):
        # The cold start this was designed to survive: no scraped jobs at
        # all, and the profile is still the person's own job title.
        import local_profile
        import local_search
        import local_extract as le
        with _patched(le, "extract", lambda m, t, *a, **k: (self.FIELDS, 0.1)), \
             _patched(le, "employment", lambda m, t, *a, **k: self.ROWS):
            got = local_profile.generate(
                self.RESUME, {"avoid": []},
                market=local_search.Market(rows=[], seniority=("senior",)),
                log=lambda *a: None)
        self.assertEqual(got["role_keywords"], ["salesforce developer"])


class TestResumeIsTheOneNamed(unittest.TestCase):
    """--resume must read the file it names.

    load_resume() caches to ONE shared path and decides by mtime alone, so
    a cache newer than the named PDF is handed back for it. That is how a
    run reported "594 chars from ada-plain.pdf" while reading 3,634 chars
    of a different person — the same defect that once had one person
    measured twice under two names, and it would silently corrupt any
    local-versus-Gemini comparison.
    """

    def test_a_named_resume_bypasses_the_shared_cache(self):
        tmp = tempfile.mkdtemp()
        try:
            other = os.path.join(tmp, "other.pdf")
            open(other, "wb").write(b"%PDF-1.4 stub")
            cache = os.path.join(tmp, "resume.txt")
            with open(cache, "w", encoding="utf-8") as fh:
                fh.write("SOMEBODY ELSE'S RESUME")
            # The cache is newer than the named PDF, which is exactly when
            # load_resume returns it.
            os.utime(other, (1, 1))
            seen = {}

            def fake_extract(path):
                seen["path"] = path
                return "THE NAMED PDF"

            with _patched(make_profile.cfg, "RESUME_PDF",
                          os.path.join(tmp, "resume.pdf")), \
                 _patched(make_profile.cfg, "RESUME_TXT", cache), \
                 _patched(make_profile.resume_parser, "extract_text",
                          fake_extract):
                text = (make_profile.resume_parser.load_resume(
                            other, cache)
                        if os.path.abspath(other)
                        == os.path.abspath(make_profile.cfg.RESUME_PDF)
                        else make_profile.resume_parser.extract_text(other))
            self.assertEqual(text, "THE NAMED PDF")
            self.assertEqual(seen["path"], other)
            # And the defect is real: the unguarded call returns the cache.
            self.assertEqual(
                make_profile.resume_parser.load_resume(other, cache),
                "SOMEBODY ELSE'S RESUME")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_default_resume_still_uses_the_cache(self):
        # The cache exists to avoid re-parsing a PDF on every /estimate.
        tmp = tempfile.mkdtemp()
        try:
            pdf = os.path.join(tmp, "resume.pdf")
            open(pdf, "wb").write(b"%PDF-1.4 stub")
            cache = os.path.join(tmp, "resume.txt")
            with open(cache, "w", encoding="utf-8") as fh:
                fh.write("CACHED")
            os.utime(pdf, (1, 1))
            with _patched(make_profile.cfg, "RESUME_PDF", pdf), \
                 _patched(make_profile.cfg, "RESUME_TXT", cache):
                self.assertEqual(os.path.abspath(pdf),
                                 os.path.abspath(make_profile.cfg.RESUME_PDF))
                self.assertEqual(
                    make_profile.resume_parser.load_resume(pdf, cache),
                    "CACHED")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestEngineIsolation(unittest.TestCase):
    """The boundary: engine=local must be incapable of reaching Gemini.

    Not "does not happen to call it" — incapable. Every one of these tests
    arms every Gemini entry point to raise, so reaching any of them is a
    failure rather than a silent network call. A key present in .env is the
    normal case on a developer's machine and must change nothing.
    """

    LOCAL = {"candidate_name": "Local Person", "field_summary": "s",
             "years_experience": 3, "role_keywords": ["backend engineer"],
             "skill_weights": [{"term": "python", "weight": 3}],
             "penalty_terms": [], "domain_half_a": [], "domain_half_b": [],
             "domain_title_terms": [], "title_hints": ["developer"],
             "title_exclude": [], "domain_bonus": 0, "notes": "n"}

    @contextlib.contextmanager
    def _gemini_armed(self):
        """Every route to Gemini raises if taken."""
        tripped = []

        def trip(name):
            def boom(*a, **kw):
                tripped.append(name)
                raise AssertionError(f"engine=local reached Gemini via {name}")
            return boom

        # tailor is armed only if it can be imported at all. Where the SDK
        # is absent it cannot be, and that absence is a stronger guarantee
        # than any patch — but the test must still RUN there, which is the
        # environment the guarantee is for.
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                _patched(make_profile, "_generate_one", trip("_generate_one")))
            try:
                import tailor
            except ImportError:
                pass
            else:
                stack.enter_context(
                    _patched(tailor, "get_client", trip("tailor.get_client")))
                stack.enter_context(_patched(tailor, "genai", None))
            stack.enter_context(mock.patch.dict(
                os.environ, {"GEMINI_API_KEY": "a-real-looking-key"}))
            yield tripped

    def _local_ok(self):
        import local_profile
        return _patched(local_profile, "generate",
                        lambda *a, **kw: dict(self.LOCAL))

    # -- engine=local ----------------------------------------------------

    def test_local_never_reaches_gemini_even_with_a_key_present(self):
        with self._gemini_armed() as tripped, self._local_ok():
            got = make_profile.generate(None, ("m",), "résumé", {},
                                        engine="local", log=lambda *a: None)
        self.assertEqual(tripped, [])
        self.assertEqual(got["candidate_name"], "Local Person")

    def test_local_never_reaches_gemini_even_when_handed_a_live_client(self):
        # main() builds a client whenever a key exists and passes it in.
        # Holding one must not make it reachable.
        class ExplodingClient:
            def __getattr__(self, name):
                raise AssertionError(f"engine=local touched the client ({name})")

        with self._gemini_armed() as tripped, self._local_ok():
            got = make_profile.generate(ExplodingClient(), ("m1", "m2"),
                                        "résumé", {}, engine="local",
                                        log=lambda *a: None)
        self.assertEqual(tripped, [])
        self.assertEqual(got["candidate_name"], "Local Person")

    def test_local_does_not_walk_the_model_ladder(self):
        # The ladder is the loop that spends a call per model. It must not
        # be entered at all, not merely exited early.
        seen = []
        with self._local_ok(), \
             _patched(make_profile, "_generate_one",
                      lambda *a: seen.append(a) or dict(self.LOCAL)):
            make_profile.generate(object(), ("m1", "m2", "m3"), "r", {},
                                  engine="local", log=lambda *a: None)
        self.assertEqual(seen, [])

    def test_an_unavailable_ollama_fails_locally_and_names_the_cause(self):
        """No silent fallback, and an actionable message.

        Driven through the REAL transport at a dead port, so this covers
        urllib and the error translation rather than a mocked exception.
        """
        import local_extract as le
        dead = "http://127.0.0.1:11544/api/generate"
        # The URL is a DEFAULT ARGUMENT in four places, so patching
        # le.OLLAMA does not reach it — an earlier version of this test
        # therefore made a real model call and asserted the wrong failure.
        # The transport function is looked up on the module at call time,
        # so redirecting it keeps real urllib in the loop and needs no
        # cooperation from the callers' signatures.
        real = le._generate
        with self._gemini_armed() as tripped, \
             _patched(le, "_generate",
                      lambda m, prompt, schema, timeout, url=None:
                          real(m, prompt, schema, 3, dead)):
            with self.assertRaises(le.ModelUnavailable) as caught:
                make_profile.generate(object(), ("m",), "résumé", {},
                                      engine="local", log=lambda *a: None)
        self.assertEqual(tripped, [], "an absent local model reached Gemini")
        message = str(caught.exception)
        self.assertIn("Ollama", message)
        self.assertIn("11544", message)
        # And never the raw urllib text, which can carry the request URL
        # in a form nobody can act on.
        self.assertNotIn("URLError", message)

    def test_a_local_escalation_is_raised_not_swallowed(self):
        import local_profile
        with self._gemini_armed() as tripped, \
             _patched(local_profile, "generate",
                      lambda *a, **kw: (_ for _ in ()).throw(
                          local_profile.Escalated(["dates unreadable"]))):
            with self.assertRaises(local_profile.Escalated) as caught:
                make_profile.generate(object(), ("m",), "r", {},
                                      engine="local", log=lambda *a: None)
        self.assertEqual(tripped, [])
        self.assertIn("dates unreadable", str(caught.exception))

    def test_the_cli_key_gate_is_engine_aware(self):
        # The whole point of the local engine: no key needed. And the
        # default engine must still demand one.
        self.assertEqual(make_profile.engine_name("local"), "local")
        for engine, needs_key in (("gemini", True), ("local-first", True),
                                  ("local", False)):
            with self.subTest(engine):
                self.assertEqual(engine != "local", needs_key)

    # -- engine=local-first: exactly when fallback is allowed ------------

    def test_local_first_falls_back_only_for_these_two_causes(self):
        """Fallback is allowed for evidence of a problem, never for a bug.

        Escalated and ModelUnavailable are the two the local engine raises
        deliberately. Anything else is a defect and must surface, not be
        masked by a Gemini call that happens to succeed.
        """
        import local_profile
        import local_extract as le

        allowed = [local_profile.Escalated(["unreadable dates"]),
                   le.ModelUnavailable("Ollama is not running")]
        forbidden = [ValueError("a bug in local_search"),
                     KeyError("skills"),
                     TypeError("None is not iterable"),
                     RuntimeError("something else entirely")]

        for exc in allowed:
            with self.subTest(allowed=type(exc).__name__):
                reached = []
                with _patched(local_profile, "generate",
                              lambda *a, **kw: (_ for _ in ()).throw(exc)), \
                     _patched(make_profile, "_generate_one",
                              lambda *a: reached.append(1) or dict(self.LOCAL)):
                    got = make_profile.generate(object(), ("m",), "r", {},
                                                engine="local-first",
                                                log=lambda *a: None)
                self.assertEqual(reached, [1], "fallback should have happened")
                self.assertEqual(got["candidate_name"], "Local Person")

        for exc in forbidden:
            with self.subTest(forbidden=type(exc).__name__):
                reached = []
                with _patched(local_profile, "generate",
                              lambda *a, **kw: (_ for _ in ()).throw(exc)), \
                     _patched(make_profile, "_generate_one",
                              lambda *a: reached.append(1) or dict(self.LOCAL)):
                    with self.assertRaises(type(exc)):
                        make_profile.generate(object(), ("m",), "r", {},
                                              engine="local-first",
                                              log=lambda *a: None)
                self.assertEqual(reached, [], "a bug was masked by Gemini")

    def test_local_first_cannot_fall_back_without_a_client(self):
        # A missing key plus a local escalation has nowhere to go. It must
        # re-raise the local cause, not a confusing AttributeError from
        # calling a method on None.
        import local_profile
        with _patched(local_profile, "generate",
                      lambda *a, **kw: (_ for _ in ()).throw(
                          local_profile.Escalated(["unreadable"]))):
            with self.assertRaises(local_profile.Escalated):
                make_profile.generate(None, ("m",), "r", {},
                                      engine="local-first",
                                      log=lambda *a: None)

    def test_local_first_prefers_local_and_does_not_call_gemini_on_success(self):
        with self._gemini_armed() as tripped, self._local_ok():
            got = make_profile.generate(object(), ("m",), "r", {},
                                        engine="local-first",
                                        log=lambda *a: None)
        self.assertEqual(tripped, [])
        self.assertEqual(got["candidate_name"], "Local Person")

    def test_the_fallback_says_out_loud_that_it_happened(self):
        # A profile silently produced by a different engine than the user
        # selected is the one outcome nobody could debug.
        import local_profile
        said = []
        with _patched(local_profile, "generate",
                      lambda *a, **kw: (_ for _ in ()).throw(
                          local_profile.Escalated(["unreadable dates"]))), \
             _patched(make_profile, "_generate_one",
                      lambda *a: dict(self.LOCAL)):
            make_profile.generate(object(), ("m",), "r", {},
                                  engine="local-first", log=said.append)
        joined = " ".join(str(s) for s in said)
        self.assertIn("escalated", joined)
        self.assertIn("Gemini", joined)
        self.assertIn("unreadable dates", joined)

    def test_sweeps_injected_seam_passes_the_engine_through(self):
        # Sweep's derive() is the other entry point and must not be able
        # to run a different engine than the CLI.
        import sweep.app as app_module
        with mock.patch.dict(os.environ,
                             {make_profile.ENGINE_ENV: "local",
                              "GEMINI_API_KEY": "k"}):
            seen = {}

            def spy(client, models, resume_text, prefs, engine=None):
                seen["engine"], seen["client"] = engine, client
                return dict(self.LOCAL)

            app = app_module.create_app(state={"resume_text": "x"},
                                        extract=lambda p: "x")
            app.config.update(TESTING=True)
            with mock.patch.object(app_module.make_profile, "generate", spy), \
                 mock.patch.object(app_module.make_profile, "engine_name",
                                   lambda *a: "local"):
                app.test_client().post("/derive")
        self.assertEqual(seen["engine"], "local")


class TestOllamaConfiguration(unittest.TestCase):
    """Endpoint and model come from the environment, resolved per call.

    "Install Ollama" is what replaces "supply an API key", so a user
    running it on another port, in Docker, or on a second machine has to
    be able to say so. And these must NOT be default arguments: freezing
    them at import is what made the endpoint unreachable from a test,
    which let an earlier version of the isolation test make a real model
    call and assert the wrong failure.
    """

    def env(self, **kw):
        base = {k: v for k, v in os.environ.items()
                if k not in (local_extract.HOST_ENV, local_extract.MODEL_ENV)}
        base.update({k: v for k, v in kw.items() if v is not None})
        return mock.patch.dict(os.environ, base, clear=True)

    # -- defaults --------------------------------------------------------

    def test_the_defaults_are_the_measured_ones(self):
        with self.env():
            self.assertEqual(local_extract.host(), "http://127.0.0.1:11434")
            self.assertEqual(local_extract.endpoint(),
                             "http://127.0.0.1:11434/api/generate")
            self.assertEqual(local_extract.model_name(), "qwen3:8b")
        self.assertEqual(local_extract.DEFAULT_HOST, "http://127.0.0.1:11434")
        self.assertEqual(local_extract.DEFAULT_MODEL, "qwen3:8b")

    def test_an_empty_or_blank_variable_falls_back_to_the_default(self):
        for value in ("", "   "):
            with self.subTest(value=repr(value)), \
                 self.env(OLLAMA_HOST=value, OLLAMA_MODEL=value):
                self.assertEqual(local_extract.host(),
                                 local_extract.DEFAULT_HOST)
                self.assertEqual(local_extract.model_name(),
                                 local_extract.DEFAULT_MODEL)

    # -- custom values ---------------------------------------------------

    def test_a_custom_host_is_honoured(self):
        with self.env(OLLAMA_HOST="http://10.0.0.7:9999"):
            self.assertEqual(local_extract.host(), "http://10.0.0.7:9999")
            self.assertEqual(local_extract.endpoint(),
                             "http://10.0.0.7:9999/api/generate")

    def test_a_host_without_a_scheme_gets_one(self):
        # Ollama's own variable is routinely written bare, and inside a
        # compose file it is a service name.
        for given, want in (("127.0.0.1:11434", "http://127.0.0.1:11434"),
                            ("ollama:11434", "http://ollama:11434"),
                            ("localhost", "http://localhost")):
            with self.subTest(given), self.env(OLLAMA_HOST=given):
                self.assertEqual(local_extract.host(), want)

    def test_a_trailing_slash_does_not_double_up(self):
        with self.env(OLLAMA_HOST="http://10.0.0.7:9999/"):
            self.assertEqual(local_extract.endpoint(),
                             "http://10.0.0.7:9999/api/generate")

    def test_an_https_host_keeps_its_scheme(self):
        with self.env(OLLAMA_HOST="https://ollama.internal"):
            self.assertEqual(local_extract.endpoint(),
                             "https://ollama.internal/api/generate")

    def test_a_custom_model_is_honoured_and_an_explicit_one_wins(self):
        with self.env(OLLAMA_MODEL="qwen3:14b"):
            self.assertEqual(local_extract.model_name(), "qwen3:14b")
            # An explicit argument beats the environment, so a caller can
            # always override — which is how the benchmark pins a model.
            self.assertEqual(local_extract.model_name("llama3:8b"),
                             "llama3:8b")

    # -- resolved per call, not frozen at import -------------------------

    def test_the_endpoint_is_read_at_call_time_not_bound_as_a_default(self):
        """The regression this section exists for.

        If any of these carried `url=OLLAMA` as a default argument, the
        environment set here would be ignored and the request would go to
        the machine's real Ollama.
        """
        seen = {}

        def capture(model, prompt, schema, timeout, url=None):
            seen["url"], seen["model"] = url, model
            return {"name": "", "years_experience": 0, "titles": [],
                    "skills": [], "companies": [], "education": [],
                    "institutions": [], "projects": [], "certifications": []}

        with self.env(OLLAMA_HOST="http://10.0.0.7:9999",
                      OLLAMA_MODEL="custom:7b"), \
             _patched(local_extract, "_generate", capture):
            local_extract.extract(text="x")
            self.assertEqual(seen["url"], None,
                             "extract passes url through; _generate resolves")
            self.assertEqual(seen["model"], "custom:7b")

        # _generate itself is where the resolution happens, so that is
        # where it must be asserted.
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            raise urllib.error.URLError("no")

        with self.env(OLLAMA_HOST="http://10.0.0.7:9999"), \
             mock.patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(local_extract.ModelUnavailable):
                local_extract.extract(text="x")
        self.assertEqual(calls, ["http://10.0.0.7:9999/api/generate"])

    def test_every_entry_point_reaches_the_configured_endpoint(self):
        # extract, employment and read are three doors into the same
        # transport; a frozen default on any one of them is a live bug.
        for name, call in (
                ("extract", lambda: local_extract.extract(text="x")),
                ("employment", lambda: local_extract.employment(text="x")),
                ("read", lambda: local_extract.read(text="x"))):
            with self.subTest(name):
                urls = []

                def fake_urlopen(request, timeout=None):
                    urls.append(request.full_url)
                    raise urllib.error.URLError("no")

                with self.env(OLLAMA_HOST="http://10.0.0.7:9999"), \
                     mock.patch("urllib.request.urlopen", fake_urlopen):
                    with self.assertRaises(local_extract.ModelUnavailable):
                        call()
                self.assertEqual(urls[0], "http://10.0.0.7:9999/api/generate")

    def test_local_profile_resolves_both_and_says_which_it_used(self):
        # The log names the model and host actually asked, so a run
        # against the wrong machine is visible rather than mysterious.
        import local_profile
        import local_search
        said = []
        with self.env(OLLAMA_HOST="http://10.0.0.7:9999",
                      OLLAMA_MODEL="custom:7b"), \
             _patched(local_extract, "extract",
                      lambda *a, **kw: ({"name": "", "years_experience": 0,
                                         "titles": [], "skills": [],
                                         "companies": [], "education": [],
                                         "institutions": [], "projects": [],
                                         "certifications": []}, 0.0)), \
             _patched(local_extract, "employment",
                      lambda *a, **kw: {"target_field": "x",
                                        "employment": []}):
            try:
                local_profile.generate(
                    "x", {"avoid": []},
                    market=local_search.Market(rows=[], seniority=("senior",)),
                    log=said.append)
            except local_profile.Escalated:
                pass  # no keywords from an empty corpus; the log is the point
        joined = " ".join(str(s) for s in said)
        self.assertIn("custom:7b", joined)
        self.assertIn("http://10.0.0.7:9999", joined)

    def test_bench_still_sees_the_module_level_name(self):
        # bench/ reads local_extract.OLLAMA as a constant in five places.
        # Measurement-only, but it must not break.
        self.assertTrue(local_extract.OLLAMA.endswith("/api/generate"))


class TestNoGeminiSdkRequired(unittest.TestCase):
    """The local engine must run where google-genai is not installed.

    make_profile is unavoidable for engine=local — render() and the engine
    seam live in it — so a module-level Gemini import made the SDK a hard
    install dependency of the local path, which is the whole thing the
    local engine exists to remove.
    """

    GEMINI_MODULES = ("google", "google.genai", "genai", "tailor")

    def test_make_profile_has_no_module_level_gemini_import(self):
        source = open(make_profile.__file__, encoding="utf-8").read()
        # Everything before the first function or class definition.
        head = re.split(r"^(?:def|class) ", source, maxsplit=1,
                        flags=re.MULTILINE)[0]
        for bad in ("from google", "import google", "import tailor"):
            self.assertNotIn(bad, head,
                             f"{bad!r} is back at module level in "
                             f"make_profile — the local engine would need "
                             f"the Gemini SDK again")

    def test_the_local_modules_import_with_the_sdk_blocked(self):
        import importlib.abc

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in ("google", "genai", "tailor"):
                    raise ImportError(f"No module named {fullname!r}")
                return None

        blocker = Blocker()
        # Dropped from sys.modules so the import really re-runs rather
        # than being served from the cache.
        saved = {name: sys.modules.pop(name)
                 for name in list(sys.modules)
                 if name.split(".")[0] in ("google", "genai", "tailor")}
        sys.meta_path.insert(0, blocker)
        try:
            with self.assertRaises(ImportError):
                importlib.import_module("google.genai")
            for name in ("local_extract", "local_search", "local_profile",
                         "skill_scan", "corpus_signal", "make_profile"):
                with self.subTest(name):
                    importlib.reload(importlib.import_module(name))
        finally:
            sys.meta_path.remove(blocker)
            sys.modules.update(saved)
            # Leave the modules as the rest of the suite expects them.
            for name in ("make_profile", "local_profile"):
                importlib.reload(importlib.import_module(name))

    def test_generating_locally_never_imports_the_sdk(self):
        """The runtime claim, not just the import-time one."""
        import local_profile
        touched = []
        real_import = builtins.__import__

        def watch(name, *a, **kw):
            if name.split(".")[0] in ("google", "genai", "tailor"):
                touched.append(name)
            return real_import(name, *a, **kw)

        with _patched(local_profile, "generate",
                      lambda *a, **kw: dict(_MINIMAL_PROFILE)), \
             mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
             _patched(builtins, "__import__", watch):
            make_profile.generate(None, ("m",), "résumé", {},
                                  engine="local", log=lambda *a: None)
        self.assertEqual(touched, [])

    def test_the_gemini_path_still_imports_what_it_needs(self):
        # Isolating the import must not break the engine that uses it.
        # Asserted by reaching the point where `types` is used.
        reached = []

        class Response:
            text = json.dumps(dict(_MINIMAL_PROFILE))
            candidates = []

        class Models:
            def generate_content(self, model, contents, config):
                # config is built from google.genai types, so arriving
                # here with one proves the in-function import ran.
                reached.append(type(config).__name__)
                return Response()

        class Client:
            models = Models()

        got = make_profile._generate_one(Client(), "m", "résumé",
                                         {"locations": ["X"], "avoid": [],
                                          "exclude_levels": []},
                                         1, lambda s: None, lambda *a: None)
        self.assertEqual(reached, ["GenerateContentConfig"])
        self.assertEqual(got["candidate_name"], "X")

    def test_sweep_starts_without_the_sdk(self):
        # sweep/app.py imports make_profile at module load and used to
        # import tailor when building the app.
        source = open(os.path.join(
            make_profile.cfg.REPO_ROOT, "sweep", "app.py"),
            encoding="utf-8").read()
        head = re.split(r"^def create_app", source, maxsplit=1,
                        flags=re.MULTILINE)[0]
        self.assertNotIn("import tailor", head)
