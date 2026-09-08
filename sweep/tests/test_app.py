import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from sweep import app as app_module


class TestUploadScreen(unittest.TestCase):
    def setUp(self):
        self.app = app_module.create_app(state={})
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def test_upload_screen_renders_with_an_empty_meter(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("$0.00", body)
        self.assertIn("Point it at your", body)

    def test_meter_shows_no_cap_before_a_key_is_connected(self):
        # base.html renders the credit block only when cap_usd is set.
        body = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("Credit left", body)

    def test_meter_shows_a_real_zero_cap_once_credit_is_exhausted(self):
        # cap_usd=0.0 is a genuine value (the account has $0 left), not the
        # same as "no key connected yet" (cap_usd=None) — Jinja treats both
        # as falsy under a plain {% if cap_usd %}, so this must use an
        # explicit "is not none" check to tell them apart.
        app = app_module.create_app(state={"cap_usd": 0.0})
        app.config.update(TESTING=True)
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn("Credit left $0.00", body)

    def test_posting_no_file_is_rejected_not_guessed(self):
        r = self.client.post("/resume", data={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Choose a PDF", r.get_data(as_text=True))

    def test_a_pdf_with_no_extractable_text_says_so(self):
        state = {}
        resume_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, resume_dir)
        app = app_module.create_app(
            state=state, extract=lambda path: "", resume_dir=resume_dir)
        app.config.update(TESTING=True)
        r = app.test_client().post(
            "/resume",
            data={"resume": (io.BytesIO(b"%PDF-1.7 fake"), "scan.pdf")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("no text", r.get_data(as_text=True).lower())

    def test_a_good_pdf_is_stored_and_redirects_to_review(self):
        state = {}
        resume_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, resume_dir)
        app = app_module.create_app(
            state=state, extract=lambda path: "Kartik — React Native developer",
            resume_dir=resume_dir)
        app.config.update(TESTING=True)
        r = app.test_client().post(
            "/resume",
            data={"resume": (io.BytesIO(b"%PDF-1.7 fake"), "cv.pdf")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/review", r.headers["Location"])
        self.assertIn("React Native", state["resume_text"])

    def test_the_upload_is_saved_under_the_injected_resume_dir_not_the_real_one(self):
        real_path = os.path.join(app_module.RESUME_DIR, "resume.pdf")
        real_mtime_before = os.path.getmtime(real_path)

        state = {}
        resume_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, resume_dir)
        app = app_module.create_app(
            state=state, extract=lambda path: "Kartik — React Native developer",
            resume_dir=resume_dir)
        app.config.update(TESTING=True)
        app.test_client().post(
            "/resume",
            data={"resume": (io.BytesIO(b"%PDF-1.7 fake"), "cv.pdf")},
            content_type="multipart/form-data")

        self.assertEqual(
            state["resume_path"], os.path.join(resume_dir, "resume.pdf"))
        self.assertTrue(os.path.exists(os.path.join(resume_dir, "resume.pdf")))
        self.assertEqual(os.path.getmtime(real_path), real_mtime_before)

    def test_a_file_over_the_limit_is_rejected_with_the_upload_error_panel(self):
        state = {}
        resume_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, resume_dir)
        app = app_module.create_app(
            state=state, resume_dir=resume_dir, max_upload_bytes=1024)
        app.config.update(TESTING=True)
        oversized = io.BytesIO(b"0" * 2048)
        r = app.test_client().post(
            "/resume",
            data={"resume": (oversized, "big.pdf")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 413)
        self.assertIn("larger than", r.get_data(as_text=True))


DERIVED = {
    "field_summary": "Full-stack React Native developer, about 2 years.",
    "years_experience": 2,
    "role_keywords": ["React Native Developer", "Full Stack Engineer"],
    "skill_weights": [
        {"term": "react native", "weight": 5},
        {"term": "node.js", "weight": 5},
        {"term": "javascript", "weight": 2},
        {"term": "git", "weight": 1},
    ],
    "penalty_terms": [{"term": "salesforce", "weight": 5}],
    "domain_half_a": ["react native"], "domain_half_b": ["node.js"],
    "domain_title_terms": ["react native developer"], "domain_bonus": 5,
    "notes": "Platform terms dominate.",
}


class TestReviewScreen(unittest.TestCase):
    def _app(self, state=None):
        state = state if state is not None else {"resume_text": "a résumé"}
        app = app_module.create_app(
            state=state, extract=lambda p: "x",
            derive=lambda resume_text, prefs: DERIVED)
        app.config.update(TESTING=True)
        return app, state

    def test_shows_the_derived_titles_and_weights(self):
        app, _ = self._app()
        body = app.test_client().get("/review").get_data(as_text=True)
        self.assertIn("React Native Developer", body)
        self.assertIn("react native", body)
        self.assertIn("Full-stack React Native developer", body)

    def test_low_weight_commodity_terms_are_flagged_for_removal(self):
        # 'git' and 'javascript' appear in most postings and carry no signal.
        app, _ = self._app()
        body = app.test_client().get("/review").get_data(as_text=True)
        self.assertIn("Worth removing", body)
        self.assertIn("git", body)

    def test_review_without_a_resume_sends_you_back_to_upload(self):
        app, _ = self._app(state={})
        r = app.test_client().get("/review")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/"),
                        f"should redirect to upload, got {r.headers['Location']}")

    def test_approving_writes_the_profile_and_moves_to_the_key_screen(self):
        app, state = self._app()
        written = {}
        app.write_profile = lambda name, source: written.update(
            {"name": name, "source": source})
        r = app.test_client().post("/review", data={
            "name": "kanav", "drop": ["git"]})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/key", r.headers["Location"])
        self.assertEqual(written["name"], "kanav")
        self.assertNotIn("'git'", written["source"])
        self.assertIn("react native", written["source"])
        self.assertEqual(state["profile"], "kanav")

    def test_a_missing_name_is_rejected_not_defaulted(self):
        app, _ = self._app()
        r = app.test_client().post("/review", data={"name": ""})
        self.assertEqual(r.status_code, 400)
        self.assertIn("name", r.get_data(as_text=True).lower())

    def test_a_name_with_a_path_separator_is_rejected_not_written(self):
        # profile name -> profiles/<name>.py; os.path.join silently drops
        # everything before a later absolute component, so this must be
        # blocked before it ever reaches a filesystem write.
        app, _ = self._app()
        written = {}
        app.write_profile = lambda name, source: written.update(
            {"name": name, "source": source})
        r = app.test_client().post("/review", data={"name": "/tmp/x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("letters, numbers, dashes", r.get_data(as_text=True))
        self.assertEqual(written, {})

    def test_a_name_with_dot_dot_is_rejected_not_written(self):
        app, _ = self._app()
        written = {}
        app.write_profile = lambda name, source: written.update(
            {"name": name, "source": source})
        r = app.test_client().post(
            "/review", data={"name": "../../../../tmp/x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("letters, numbers, dashes", r.get_data(as_text=True))
        self.assertEqual(written, {})

    def test_posting_review_without_a_resume_sends_you_back_to_upload(self):
        app, _ = self._app(state={})
        r = app.test_client().post("/review", data={"name": "kanav"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/"),
                        f"should redirect to upload, got {r.headers['Location']}")

    def test_default_write_profile_also_rejects_a_bad_name(self):
        # Belt and suspenders: default_write_profile is the single write
        # funnel, so it validates independently of the route's own check —
        # a future caller can't reintroduce the path-traversal hole.
        app, _ = self._app()
        with self.assertRaises(ValueError):
            app.write_profile("../evil", "source")


class TestKeyScreen(unittest.TestCase):
    def _app(self, credit=(8.41, None), state=None):
        state = state if state is not None else {"profile": "kanav"}
        app = app_module.create_app(
            state=state, extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda token: credit)
        app.config.update(TESTING=True)
        app.write_env = lambda key, value: None
        return app, state

    def test_key_screen_renders(self):
        app, _ = self._app()
        self.assertEqual(app.test_client().get("/key").status_code, 200)

    def test_a_good_token_sets_the_cap_and_moves_on(self):
        app, state = self._app(credit=(8.41, None))
        r = app.test_client().post("/key", data={"token": "apify_api_xxx"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/configure", r.headers["Location"])
        self.assertAlmostEqual(state["cap_usd"], 8.41, places=2)

    def test_a_rejected_token_stops_here_with_the_reason(self):
        app, state = self._app(credit=(None, "That token was rejected by Apify."))
        r = app.test_client().post("/key", data={"token": "bad"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("rejected", r.get_data(as_text=True))
        self.assertNotIn("cap_usd", state)

    def test_an_empty_token_is_rejected(self):
        app, _ = self._app()
        r = app.test_client().post("/key", data={"token": "  "})
        self.assertEqual(r.status_code, 400)

    def test_the_token_is_never_echoed_back_into_the_page(self):
        app, _ = self._app(credit=(None, "That token was rejected by Apify."))
        body = app.test_client().post(
            "/key", data={"token": "apify_api_SECRET"}).get_data(as_text=True)
        self.assertNotIn("apify_api_SECRET", body)

    def test_a_negative_available_credit_is_clamped_to_zero(self):
        # An account can already be over its own monthly limit — that must
        # never hand a negative number to the meter on later screens.
        app, state = self._app(credit=(-3.5, None))
        r = app.test_client().post("/key", data={"token": "x"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(state["cap_usd"], 0.0)

    def test_a_malformed_limits_response_is_a_400_not_a_500(self):
        # The float() conversion used to sit outside the try/except in the
        # real (non-injected) check_token, so a field Apify returns as a
        # non-numeric value crashed the request into a 500 instead of the
        # graceful 400 every other bad-input path gets.
        class FakeLimits:
            def model_dump(self):
                return {"current": {"monthly_usage_usd": "oops"},
                        "limits": {"max_monthly_usage_usd": 5.0}}

        class FakeUser:
            def limits(self):
                return FakeLimits()

        class FakeClient:
            def __init__(self, token):
                pass

            def user(self):
                return FakeUser()

        with mock.patch("apify_client.ApifyClient", FakeClient):
            app = app_module.create_app(
                state={"profile": "kanav"}, extract=lambda p: "x",
                derive=lambda t, p: DERIVED)
            app.write_env = lambda key, value: None
            r = app.test_client().post("/key", data={"token": "abc"})
        self.assertEqual(r.status_code, 400)


class TestWriteEnv(unittest.TestCase):
    """The real default_write_env, run against a temp file so no test ever
    touches the real .env — which holds live working credentials."""

    def _env_file(self, content):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        self.addCleanup(os.remove, path)
        return path

    def _app(self, env_path):
        return app_module.create_app(
            state={}, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            env_path=env_path)

    def test_upserts_the_target_key_and_leaves_every_other_key_intact(self):
        # Mirrors the real .env: several keys, no trailing newline on the
        # last line (GEMINI_API_KEY in the real file has none).
        path = self._env_file(
            "APIFY_TOKEN=old\nGROQ_API_KEY=g\nGEMINI_API_KEY=e")
        app = self._app(path)
        app.write_env("APIFY_TOKEN", "new")
        with open(path) as fh:
            pairs = dict(ln.split("=", 1) for ln in fh.read().splitlines())
        self.assertEqual(pairs, {
            "APIFY_TOKEN": "new", "GROQ_API_KEY": "g", "GEMINI_API_KEY": "e"})

    def test_a_missing_trailing_newline_does_not_corrupt_the_last_line(self):
        path = self._env_file("GROQ_API_KEY=g\nGEMINI_API_KEY=e")  # no \n
        app = self._app(path)
        app.write_env("APIFY_TOKEN", "new")
        with open(path) as fh:
            lines = fh.read().splitlines()
        self.assertIn("GEMINI_API_KEY=e", lines)
        self.assertIn("APIFY_TOKEN=new", lines)
        self.assertEqual(len(lines), 3)

    def test_a_missing_env_file_is_created(self):
        path = os.path.join(tempfile.mkdtemp(), ".env")
        self.addCleanup(shutil.rmtree, os.path.dirname(path))
        app = self._app(path)
        app.write_env("APIFY_TOKEN", "new")
        with open(path) as fh:
            self.assertEqual(fh.read().splitlines(), ["APIFY_TOKEN=new"])

    def test_a_value_containing_an_equals_sign_round_trips(self):
        path = self._env_file("GROQ_API_KEY=g\n")
        app = self._app(path)
        app.write_env("GEMINI_API_KEY", "a=b=c")
        with open(path) as fh:
            lines = fh.read().splitlines()
        self.assertIn("GEMINI_API_KEY=a=b=c", lines)

    def test_write_env_rejects_a_value_containing_a_newline(self):
        # A newline in the value would turn one write into two lines — the
        # second one an attacker-chosen KEY=VALUE that overwrites whatever
        # real key it names. The write must not happen at all.
        path = self._env_file("GROQ_API_KEY=g\n")
        with open(path) as fh:
            before = fh.read()
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", "abc\nGROQ_API_KEY=INJECTED")
        with open(path) as fh:
            self.assertEqual(fh.read(), before)

    def test_write_env_rejects_a_value_containing_a_carriage_return(self):
        path = self._env_file("GROQ_API_KEY=g\n")
        with open(path) as fh:
            before = fh.read()
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", "abc\rGROQ_API_KEY=INJECTED")
        with open(path) as fh:
            self.assertEqual(fh.read(), before)

    def test_write_env_rejects_a_nul_byte(self):
        # A NUL doesn't split a line, so the newline/CR blacklist let it
        # through — it got written to disk, and os.environ[...] = value
        # then raised ValueError on the NUL, unhandled, for a 500 with a
        # corrupted value already on disk. The allowlist below closes this
        # (and any other non-printable variant) in one rule instead of
        # chasing individual bad characters one bug report at a time.
        path = self._env_file("GROQ_API_KEY=g\n")
        with open(path) as fh:
            before = fh.read()
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", "apify_api_abc\x00INJECT")
        with open(path) as fh:
            self.assertEqual(fh.read(), before)

    def test_write_env_rejects_a_tab(self):
        path = self._env_file("GROQ_API_KEY=g\n")
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", "abc\tdef")

    def test_write_env_rejects_none_as_a_valueerror_not_a_typeerror(self):
        # "in" on a non-str used to raise TypeError before the isinstance
        # check existed — a caller catching ValueError (as the route does)
        # would have missed it entirely.
        path = self._env_file("GROQ_API_KEY=g\n")
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", None)

    def test_write_env_rejects_a_non_string_value(self):
        path = self._env_file("GROQ_API_KEY=g\n")
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", 12345)

    def test_write_env_rejects_a_malformed_key(self):
        path = self._env_file("GROQ_API_KEY=g\n")
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("apify_token", "x")

    def test_a_token_with_a_newline_is_rejected_and_env_is_left_untouched(self):
        # The same injection, arriving through the real /key route rather
        # than a direct write_env call — reproduces the coordinator's exact
        # curl-shaped attack and confirms nothing gets written at all.
        path = self._env_file(
            "APIFY_TOKEN=old\nGROQ_API_KEY=g\nGEMINI_API_KEY=e")
        with open(path) as fh:
            before = fh.read()
        app = app_module.create_app(
            state={"profile": "kanav"}, extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda token: (5.0, None), env_path=path)
        app.config.update(TESTING=True)
        r = app.test_client().post(
            "/key", data={"token": "apify_api_abc\nGROQ_API_KEY=INJECTED"})
        self.assertEqual(r.status_code, 400)
        with open(path) as fh:
            self.assertEqual(fh.read(), before)

    def test_a_token_with_a_carriage_return_is_rejected(self):
        app = app_module.create_app(
            state={"profile": "kanav"}, extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda token: (5.0, None),
            env_path=self._env_file("GROQ_API_KEY=g\n"))
        app.config.update(TESTING=True)
        r = app.test_client().post(
            "/key", data={"token": "apify_api_abc\rGROQ_API_KEY=INJECTED"})
        self.assertEqual(r.status_code, 400)

    def test_a_token_with_a_nul_byte_is_rejected_and_env_is_left_untouched(self):
        # Route-level version of the NUL case: validation must reject this
        # before check_token or write_env ever run, so nothing lands on
        # disk and os.environ[...] = token never gets a chance to crash.
        path = self._env_file("GROQ_API_KEY=g\n")
        with open(path) as fh:
            before = fh.read()
        app = app_module.create_app(
            state={"profile": "kanav"}, extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda token: (5.0, None), env_path=path)
        app.config.update(TESTING=True)
        r = app.test_client().post(
            "/key", data={"token": "apify_api_abc\x00INJECT"})
        self.assertEqual(r.status_code, 400)
        with open(path) as fh:
            self.assertEqual(fh.read(), before)

    def test_a_well_formed_token_still_succeeds(self):
        # The newline/CR guard must not be over-tight: a normal token still
        # makes it all the way through the real route and the real write.
        path = self._env_file("GROQ_API_KEY=g\n")
        app = app_module.create_app(
            state={"profile": "kanav"}, extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda token: (5.0, None), env_path=path)
        app.config.update(TESTING=True)
        r = app.test_client().post("/key", data={"token": "apify_api_ABC123"})
        self.assertEqual(r.status_code, 302)
        with open(path) as fh:
            lines = fh.read().splitlines()
        self.assertIn("APIFY_TOKEN=apify_api_ABC123", lines)


RAW_PLAN = {
    "profile": "kanav",
    "sites": {
        "linkedin": [{"keywords": "A", "location": "India", "company": ""}] * 32,
        "indeed": [{"keywords": "A", "location": "Pune", "company": ""}] * 14,
    },
    "free_sources": 6,
}


class TestConfigureScreen(unittest.TestCase):
    def _app(self):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: None
        return app

    def test_configure_renders_the_measured_rates(self):
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn("0.045", body)
        self.assertIn("linkedin", body)

    def test_estimate_returns_lines_that_multiply_out(self):
        r = self._app().test_client().post("/estimate", json={})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertAlmostEqual(data["total"], 2.70, places=2)
        for line in data["lines"]:
            self.assertAlmostEqual(
                line["subtotal"], line["searches"] * line["rate"], places=6)

    def test_estimate_flags_when_the_plan_exceeds_available_credit(self):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 1.00},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (1.00, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        data = app.test_client().post("/estimate", json={}).get_json()
        self.assertTrue(data["over_cap"])
        self.assertAlmostEqual(data["shortfall"], 1.70, places=2)

    def test_configure_without_a_profile_goes_back_to_review(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        r = app.test_client().get("/configure")
        self.assertEqual(r.status_code, 302)

    # -- POST /estimate actually writing the form back into the profile ----

    def _app_with_spy(self, extra_state=None):
        """Like _app(), but app.write_profile records every call instead of
        discarding it, so tests can assert on the rendered source."""
        state = {"profile": "kanav", "cap_usd": 8.41, "derived": dict(DERIVED)}
        state.update(extra_state or {})
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        writes = []
        app.write_profile = lambda n, s: writes.append((n, s))
        return app, writes

    def test_estimate_writes_submitted_values_and_leaves_the_rest_alone(self):
        app, writes = self._app_with_spy()
        client = app.test_client()

        r1 = client.post("/estimate", json={"max_age_days": "7"})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(len(writes), 1)
        self.assertIn('"max_age_days": 7', writes[-1][1])

        r2 = client.post("/estimate", json={"skip_terms": "docker, on-call"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(writes), 2)
        second_source = writes[-1][1]
        self.assertIn("docker", second_source)
        self.assertIn("on-call", second_source)
        # max_age_days came from an earlier POST that this one never
        # mentioned — it must still be there, not reset.
        self.assertIn('"max_age_days": 7', second_source)

    def test_estimate_rejects_a_bad_number_and_writes_nothing(self):
        app, writes = self._app_with_spy()
        r = app.test_client().post("/estimate", json={"max_age_days": "soon"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())
        self.assertEqual(writes, [])

    def test_estimate_rejects_a_skip_term_with_disallowed_characters(self):
        app, writes = self._app_with_spy()
        r = app.test_client().post(
            "/estimate", json={"skip_terms": "docker; rm -rf /"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(writes, [])

    def test_estimate_maps_scope_and_the_pay_floor_toggle(self):
        app, writes = self._app_with_spy()
        client = app.test_client()

        client.post("/estimate", json={"scope": "remote"})
        self.assertIn('"remote_scopes": [', writes[-1][1])
        self.assertIn("'worldwide'", writes[-1][1])

        client.post("/estimate", json={"keep_unstated": "on"})
        self.assertIn('"min_comp_usd": None', writes[-1][1])

        client.post("/estimate", json={"min_comp_usd": "20000"})
        self.assertIn('"min_comp_usd": 20000', writes[-1][1])

    def test_scope_overrides_linkedins_locations_not_just_search_locations(self):
        # SITES["linkedin"].get("locations", SEARCH["locations"]) means
        # LinkedIn — the priciest site — ignores SEARCH.locations entirely
        # unless the profile ALSO overrides SITES.linkedin. This is the
        # mechanism that made "global" and "india" scope indistinguishable
        # before this fix.
        app, writes = self._app_with_spy()
        client = app.test_client()

        client.post("/estimate", json={"scope": "india"})
        india_source = writes[-1][1]
        self.assertIn('SITES = {', india_source)
        self.assertIn('"linkedin"', india_source)
        self.assertIn("'Delhi'", india_source)
        self.assertIn('"remote_only": False', india_source)
        # enabled/actor must survive the rewrite — SITES["linkedin"] is
        # replaced wholesale by config._overlay's one-level-deep merge, not
        # deep-merged, so dropping them would silently disable the site.
        self.assertIn('"enabled": True', india_source)
        self.assertIn("curious_coder/linkedin-jobs-scraper", india_source)

        client.post("/estimate", json={"scope": "global"})
        global_source = writes[-1][1]
        self.assertIn("'United States'", global_source)
        self.assertNotIn("'Delhi'", global_source)

        client.post("/estimate", json={"scope": "remote"})
        remote_source = writes[-1][1]
        self.assertIn('"remote_only": True', remote_source)
        self.assertIn("'United States'", remote_source)  # countries, not "Remote"

    def test_no_scope_submitted_emits_no_sites_block(self):
        app, writes = self._app_with_spy()
        app.test_client().post("/estimate", json={"max_age_days": "7"})
        self.assertNotIn("SITES = {", writes[-1][1])

    def test_an_unverified_linkedin_location_is_rejected_and_writes_nothing(self):
        # Scope is a closed enum in production, so this can only be reached
        # by a bug in _SCOPE itself — proving render()'s own check catches
        # that, rather than silently billing an unverified geography. Same
        # guard applies to the CLI path, since it lives in render().
        app, writes = self._app_with_spy()
        original = dict(app_module._SCOPE["india"])
        app_module._SCOPE["india"] = dict(
            original, linkedin_locations=["Atlantis"])
        try:
            r = app.test_client().post("/estimate", json={"scope": "india"})
        finally:
            app_module._SCOPE["india"] = original
        self.assertEqual(r.status_code, 400)
        self.assertEqual(writes, [])

    def test_configure_screen_has_controls_for_skip_terms_and_depth(self):
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn('name="skip_terms"', body)
        self.assertIn('name="max_results"', body)

    def test_a_blank_skip_terms_or_depth_field_does_not_reject_other_changes(self):
        # Both fields live in the same <form> as everything else, so a
        # change to max_age_days resubmits them too, blank — that must not
        # 400 the whole screen the very first time anyone touches a control.
        app, writes = self._app_with_spy()
        r = app.test_client().post(
            "/estimate",
            json={"max_age_days": "7", "skip_terms": "", "max_results": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(writes), 1)

    def test_estimate_reprices_after_the_profile_is_rewritten(self):
        # fetch_plan here is intentionally sensitive to whether write_profile
        # has already run, so this proves the ORDER — write, then re-plan —
        # not just that both happen somewhere. A fetch_plan that ignores its
        # argument (as elsewhere in this file) would hide exactly the bug
        # this task fixes.
        written = []
        smaller_plan = {
            "profile": "kanav",
            "sites": {"linkedin": [{"keywords": "A", "location": "India",
                                     "company": ""}] * 5},
            "free_sources": 6,
        }
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "derived": dict(DERIVED)},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: smaller_plan if written else RAW_PLAN)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: written.append((n, s))

        data = app.test_client().post(
            "/estimate", json={"max_results": "5"}).get_json()

        self.assertEqual(len(written), 1)
        self.assertAlmostEqual(data["total"], 0.225, places=3)
        self.assertNotAlmostEqual(data["total"], 2.70, places=2)

    def test_render_survives_hostile_penalty_terms(self):
        # Defense-in-depth check on make_profile.render() itself, independent
        # of sweep.app's character allowlist (which would reject these
        # characters at the HTTP boundary before they ever reached here) —
        # any input that reaches render(), from any source, must still come
        # out as an inert string literal.
        quote_term = "quote's here"
        backslash_term = r"back\slash"
        data = dict(DERIVED)
        data["penalty_terms"] = [
            {"term": quote_term, "weight": 12},
            {"term": backslash_term, "weight": 12},
            {"term": '; import os; os.system("echo pwned") #', "weight": 12},
        ]
        prefs = {"locations": ["Remote"], "exclude_levels": [],
                 "min_comp_usd": None}
        source = app_module.make_profile.render("hostile_check", data, prefs)
        compile(source, "<hostile_check>", "exec")  # still valid Python
        # repr() is what render() uses to write these — checking for the
        # escaped literal, not the raw term, is what proves the quote/
        # backslash round-tripped as DATA rather than breaking the source.
        self.assertIn(repr(quote_term), source)
        self.assertIn(repr(backslash_term), source)
        self.assertIn('import os', source)


if __name__ == "__main__":
    unittest.main()
