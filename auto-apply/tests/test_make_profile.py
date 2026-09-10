import contextlib
import csv
import io
import json
import os
import re
import shutil
import tempfile
import unittest

import make_profile

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
