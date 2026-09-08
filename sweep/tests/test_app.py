import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from sweep import app as app_module


def alpine_scope(body):
    """The x-data attribute value as a real HTML parser sees it.

    `|tojson` escapes ' < & so its output can sit inside a SINGLE-quoted
    attribute; it leaves " alone. Interpolated into a double-quoted one, the
    attribute ends at the JSON's first " — so the browser reads x-data as
    '{ p: {' , Alpine fails to evaluate the component, and every binding on
    the screen is dead. That is silent: no error on the page, just a cost
    panel and a progress meter that never populate.
    """
    from html.parser import HTMLParser

    found = []

    class Scan(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key == "x-data":
                    found.append(value or "")

    Scan().feed(body)
    return found


class TestPageShell(unittest.TestCase):
    def _app(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                     derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        return app

    def test_alpine_is_served_locally_not_from_a_cdn(self):
        # /key renders this same shell, and that is where the user pastes the
        # credential this tool exists to protect. A third-party script that
        # can read the DOM does not belong on it — and an unreachable CDN
        # takes the live cost meter down silently.
        client = self._app().test_client()
        body = client.get("/").get_data(as_text=True)
        self.assertNotIn("cdnjs", body)
        self.assertIn("alpine-3.14.1.min.js", body)
        served = client.get("/static/alpine-3.14.1.min.js")
        try:
            self.assertEqual(served.status_code, 200)
            self.assertIn(b"Alpine", served.get_data())
        finally:
            # Flask streams a static file from an open handle; without this
            # the suite reports a ResourceWarning for the unclosed reader.
            served.close()

    def test_the_shell_declares_a_language_and_a_viewport(self):
        body = self._app().test_client().get("/").get_data(as_text=True)
        self.assertIn('lang="en"', body)
        self.assertIn("width=device-width", body)


class TestAlpineScopeSurvivesHtmlParsing(unittest.TestCase):
    """Every screen whose figures update in place depends on one x-data
    attribute parsing. A test that only greps the raw body cannot see this."""

    def test_the_configure_cost_scope_parses_whole(self):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: None
        body = app.test_client().get("/configure").get_data(as_text=True)
        scopes = alpine_scope(body)
        self.assertEqual(len(scopes), 1)
        self.assertIn("total", scopes[0])
        self.assertIn("lines", scopes[0])

    def test_the_running_progress_scope_parses_whole(self):
        app, _, _ = TestRunningScreen()._app()
        body = app.test_client().get("/running").get_data(as_text=True)
        scopes = alpine_scope(body)
        self.assertEqual(len(scopes), 1)
        self.assertIn("spend", scopes[0])
        self.assertIn("tiles", scopes[0])


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

    def test_configure_states_the_depth_the_rates_are_measured_at(self):
        # The rate shown is scaled to the plan's depth, so the screen has to
        # say what depth it was measured at or the figure is unauditable.
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn("measured at 25 results per search", body)
        self.assertIn("line.results", body)

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

    def test_shortfall_is_nonzero_when_credit_is_exactly_exhausted(self):
        # cap_usd=0.0 is a real value (the key has $0 left), not the same as
        # "no cap" (cap_usd=None) — `if cap` treats both as falsy, which
        # made shortfall report $0.00 for a user who is genuinely short the
        # full total. Must use `cap is not None`, same fix as base.html's
        # meter took in an earlier task for the identical falsy-zero bug.
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 0.0},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (0.0, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        data = app.test_client().post("/estimate", json={}).get_json()
        self.assertTrue(data["over_cap"])
        self.assertAlmostEqual(data["shortfall"], 2.70, places=2)
        self.assertNotEqual(data["shortfall"], 0.0)

    def test_meter_binds_to_the_same_estimate_the_panel_uses(self):
        # The meter above the form is server-rendered by base.html; the
        # panel's total is Alpine-driven from `est`. They must share one
        # source of truth (the same x-data scope and the same `est` object)
        # so a form change can never leave the two money figures disagreeing.
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        # Single-quoted: |tojson leaves " raw, so a double-quoted attribute
        # ends at the JSON's first quote. This assertion used to require the
        # double-quoted form, which is how it came to pin a broken screen.
        self.assertIn("x-data='{ est:", body)
        self.assertIn("x-text=\"'$' + est.total.toFixed(2)\"", body)
        self.assertIn(':class="{ over: est.over_cap }"', body)
        # The meter's x-data must be on an ancestor of <main>, not a second,
        # disconnected scope — otherwise Alpine can't reach it from the form.
        data_pos = body.index("x-data='{ est:")
        main_pos = body.index("<main>")
        self.assertLess(data_pos, main_pos)

    def test_paid_rate_and_subtotal_cells_are_amber_free_rows_stay_teal(self):
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn(":class=\"!line.free && 'paid'\"", body)
        # The row itself carries .free for zero-rate sites; per-cell .paid
        # must be conditioned on the same flag so a free row's money stays
        # teal, never amber, and a paid row's search count stays chalk.
        self.assertIn(':class="line.free && \'free\'"', body)

    def test_configure_without_a_profile_goes_back_to_review(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        r = app.test_client().get("/configure")
        self.assertEqual(r.status_code, 302)

    def test_configure_without_a_connected_key_goes_to_the_key_screen(self):
        # Without a key there is no credit figure, so cap_usd stays None and
        # costed()'s over_cap is False for ANY total — the advisory cap is
        # unreachable and the engine quietly runs on whatever APIFY_TOKEN is
        # already in .env. Fail closed, the same shape /run uses.
        app = app_module.create_app(state={"profile": "kanav"},
                                    extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED,
                                    fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        r = app.test_client().get("/configure")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/key", r.headers["Location"])

    def test_estimate_without_a_connected_key_refuses(self):
        # /configure's own twin: it reprices AND rewrites the profile, so a
        # guard on one and not the other leaves the whole screen reachable.
        app = app_module.create_app(state={"profile": "kanav"},
                                    extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED,
                                    fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: self.fail("wrote a profile with no key")
        r = app.test_client().post("/estimate", json={"max_age_days": "7"})
        self.assertEqual(r.status_code, 409)

    # -- POST /estimate actually writing the form back into the profile ----

    def _app_with_spy(self, extra_state=None, hour_now=None):
        """Like _app(), but app.write_profile records every call instead of
        discarding it, so tests can assert on the rendered source."""
        state = {"profile": "kanav", "cap_usd": 8.41, "derived": dict(DERIVED)}
        state.update(extra_state or {})
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            hour_now=hour_now,
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

    def test_estimate_maps_scope_and_the_pay_floor(self):
        app, writes = self._app_with_spy()
        client = app.test_client()

        client.post("/estimate", json={"scope": "remote"})
        self.assertIn('"remote_scopes": [', writes[-1][1])
        self.assertIn("'worldwide'", writes[-1][1])

        client.post("/estimate", json={"min_comp_usd": "20000"})
        self.assertIn('"min_comp_usd": 20000', writes[-1][1])

        # Blank is "no floor", a real choice rather than a validation error.
        client.post("/estimate", json={"min_comp_usd": ""})
        self.assertIn('"min_comp_usd": None', writes[-1][1])

    def test_a_late_start_warns_about_the_midnight_re_bill(self):
        # .done_combos is scoped to a single day, so a sweep still running
        # after midnight re-runs and re-bills everything it had finished.
        # This warning was previously untestable: hardcoding it to False left
        # the whole suite green.
        app, _ = self._app_with_spy(hour_now=lambda: 23)
        app.test_client().post("/estimate", json={})
        body = app.test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("midnight", body.lower())

    def test_an_early_start_does_not_warn(self):
        app, _ = self._app_with_spy(hour_now=lambda: 9)
        app.test_client().post("/estimate", json={})
        body = app.test_client().get("/confirm").get_data(as_text=True)
        self.assertNotIn("midnight", body.lower())

    def test_a_pay_floor_is_never_silently_discarded(self):
        # This screen used to carry a "keep listings that don't state pay"
        # checkbox, default ON, whose only real effect was to throw away the
        # floor the user had just typed. The engine keeps unstated pay either
        # way (scraper.comp_ok returns True when the pay is unstated), so the
        # toggle was offering a choice that did not exist and destroying a
        # real one. Whatever else the form carries, a submitted floor arrives.
        app, writes = self._app_with_spy()
        app.test_client().post("/estimate", json={
            "min_comp_usd": "80000", "keep_unstated": "on", "scope": "india"})
        self.assertIn('"min_comp_usd": 80000', writes[-1][1])
        self.assertNotIn('"min_comp_usd": None', writes[-1][1])

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
        # kartik_reachable.py's own shape: worldwide remote through LinkedIn
        # is inventory this repo already measured as ~94% unreachable (see
        # its docstring), so "remote" buys India-remote only — one
        # geography — through LinkedIn, and leaves worldwide remote to the
        # free feeds, which are built for it and cost nothing.
        self.assertIn("'Remote'", remote_source)
        self.assertNotIn("'United States'", remote_source)
        self.assertIn('"remote_geo": \'India\'', remote_source)

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

    def test_configure_screen_explains_why_remote_stays_on_linkedin_india(self):
        # The user is choosing where their money goes — the reason has to be
        # on the screen, not only in a code comment.
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn("no worldwide-remote search", body)
        self.assertIn("free feeds", body)

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


class FakeProc:
    def __init__(self):
        self.signals = []

    def poll(self):
        return None

    def send_signal(self, sig):
        self.signals.append(sig)


class TestConfirmScreen(unittest.TestCase):
    def _app(self, cap=8.41, start_sweep=None, read_spend=None, check_token=None,
              state=None):
        # output_dir is injected — never the real output/kanav, which holds
        # real paid-sweep results with no git history to fall back on.
        output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, output_dir)
        # "derived" because POST /run re-renders the profile to stamp the
        # spend cap into it, exactly as /estimate does — by the time a real
        # session has a profile, review_post() has already populated it.
        base_state = {"profile": "kanav", "cap_usd": cap, "derived": DERIVED}
        base_state.update(state or {})
        app = app_module.create_app(
            state=base_state,
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=check_token or (lambda t: (cap, None)),
            fetch_plan=lambda profile: RAW_PLAN,
            start_sweep=start_sweep or (lambda profile: FakeProc()),
            read_spend=read_spend or (lambda: 1.00),
            output_dir=output_dir)
        app.config.update(TESTING=True)
        app.output_dir = output_dir  # so tests can assert where run.json landed
        # /second-key's success path writes .env — never the real one just
        # because this harness didn't inject env_path. write_env's own
        # allowlist behaviour is Task 5's to test; here it's a pure stub.
        app.write_env = lambda key, value: None
        # POST /run writes profiles/<name>.py to stamp the spend cap in.
        # Stubbed and RECORDED, never the real profiles/ directory — the
        # sources land in app.written for the cap assertions below.
        app.written = []
        app.write_profile = lambda n, s: app.written.append((n, s))
        return app

    def test_confirm_names_the_amount_on_the_button(self):
        body = self._app().test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("2.70", body)
        self.assertIn("Run the sweep", body)

    def test_confirm_states_the_depth_each_rate_is_priced_at(self):
        body = self._app().test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("each at 25 results", body)
        self.assertIn("measured at 25 results per search", body)

    def test_confirm_states_the_hard_stop_beside_the_estimate(self):
        # The first true statement this screen can make about a real cap, so
        # it sits next to the figure. 2.70 x 1.25 = 3.38.
        body = self._app().test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("$3.38", body)
        self.assertIn("even if searches are left", body)

    def test_over_cap_offers_a_second_key_instead_of_the_run_button(self):
        body = self._app(cap=1.00).test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("second key", body.lower())
        self.assertNotIn("Run the sweep", body)

    def test_over_cap_says_how_much_to_cut(self):
        body = self._app(cap=1.00).test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("1.70", body)

    def test_running_records_the_spend_baseline_so_the_meter_shows_a_delta(self):
        # Order-sensitive: read_spend must run BEFORE start_sweep, since
        # account_usage_usd is month-to-date and the baseline has to be
        # captured before the sweep can add to it. A test where both fakes
        # return fixed, order-insensitive values would pass identically if
        # the two lines in /run were swapped — so both fakes record into a
        # shared list instead.
        calls = []

        def read_spend():
            calls.append("read_spend")
            return 1.00

        def start_sweep(profile):
            calls.append("start_sweep")
            return FakeProc()

        app = self._app(start_sweep=start_sweep, read_spend=read_spend)
        app.test_client().get("/confirm")
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/running", r.headers["Location"])
        self.assertEqual(calls, ["read_spend", "start_sweep"])
        # account_usage_usd is month-to-date, so without a baseline the meter
        # would show the whole month instead of this sweep.
        self.assertAlmostEqual(app.state["baseline_usd"], 1.00, places=2)
        self.assertIsNotNone(app.state["proc"])

    def test_run_stamps_a_real_spend_cap_into_the_profile_before_launching(self):
        # The one guard that actually stops an overspend is
        # SETTINGS["max_spend_usd"] inside scraper.py. Everything this UI
        # displays is advisory, so the cap has to be WRITTEN — and written
        # before the child starts, or the sweep it is meant to bound is
        # already running uncapped. Order-recorded, not just asserted
        # present: a write that happened after the launch would otherwise
        # pass identically.
        calls = []
        app = self._app(start_sweep=lambda profile: calls.append("start_sweep") or FakeProc(),
                        read_spend=lambda: calls.append("read_spend") or 1.00)
        app.write_profile = lambda n, s: calls.append("write_profile") or app.written.append((n, s))
        app.test_client().get("/confirm")
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(calls, ["write_profile", "read_spend", "start_sweep"])

        name, source = app.written[-1]
        self.assertEqual(name, "kanav")
        # 2.70 estimate x 1.25 headroom. A cap equal to the estimate would
        # abort a sweep that lands 10% high, which /confirm tells the user
        # to expect.
        self.assertIn('"max_spend_usd": 3.38', source)
        self.assertAlmostEqual(app.state["max_spend_usd"], 3.38, places=2)

    def test_a_tiny_sweep_is_not_capped_below_a_single_search(self):
        # Naukri alone is $0.50 per run minimum, so a cap under that would
        # stop the sweep before its first search could complete.
        app = self._app()
        with mock.patch.dict(app_module.config.SITE_RATES,
                             {"linkedin": 0.001, "indeed": 0.0}, clear=True):
            app.test_client().get("/confirm")
            app.test_client().post("/run")
        self.assertAlmostEqual(app.state["max_spend_usd"],
                               app_module.SPEND_CAP_FLOOR_USD, places=2)
        self.assertIn('"max_spend_usd": 0.5', app.written[-1][1])

    def test_a_missing_spend_reading_is_recorded_as_unknown_not_zero(self):
        # read_spend() returning None (no token, or the account-usage call
        # failed) must not collapse into a $0.00 baseline — Task 8 would
        # then subtract 0 from month-to-date spend and report the user's
        # entire month as this one sweep's cost.
        app = self._app(read_spend=lambda: None)
        app.test_client().get("/confirm")
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(app.state["baseline_usd"])
        run_path = os.path.join(app.output_dir, "kanav", "run.json")
        with open(run_path) as fh:
            import json
            self.assertIsNone(json.load(fh)["baseline_usd"])

    def test_run_json_lands_in_the_injected_output_dir_not_the_real_one(self):
        # Isolation enforced by a test, not by convention — a hardcoded
        # REPO_ROOT/output/<profile> here would let a later test collide
        # with a real profile's real (unrecoverable, gitignored) results.
        app = self._app()
        app.test_client().get("/confirm")
        app.test_client().post("/run")
        run_path = os.path.join(app.output_dir, "kanav", "run.json")
        self.assertTrue(os.path.exists(run_path))
        real_path = os.path.join(app_module.REPO_ROOT, "output", "kanav", "run.json")
        self.assertFalse(os.path.exists(real_path))

    def test_a_sweep_over_the_cap_cannot_be_started_from_the_ui(self):
        app = self._app(cap=1.00)
        app.test_client().get("/confirm")
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(app.state.get("proc"))

    def test_confirm_without_a_connected_key_goes_to_the_key_screen(self):
        app = app_module.create_app(state={"profile": "kanav"},
                                    extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED,
                                    fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        r = app.test_client().get("/confirm")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/key", r.headers["Location"])

    def test_run_without_a_connected_key_refuses_and_launches_nothing(self):
        # A cap of None makes over_cap False for any total, so this must not
        # fall through to "under cap, go ahead".
        app = self._app(state={"plan": dict(RAW_PLAN, total=2.70,
                                            total_searches=46, over_cap=False,
                                            spend_cap=3.38)})
        app.state.pop("cap_usd")
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(app.state.get("proc"))
        self.assertEqual(app.written, [])

    def test_run_with_no_plan_at_all_fails_closed(self):
        # A direct POST /run against empty state (never went through
        # /confirm, no plan computed at all) must refuse exactly like an
        # over-cap plan does — not launch an uncapped subprocess because a
        # missing plan's .get("over_cap") reads just as falsy as a real
        # "under cap" plan would.
        output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, output_dir)
        app = app_module.create_app(
            state={}, start_sweep=lambda p: FakeProc(), read_spend=lambda: 1.00,
            output_dir=output_dir)
        app.config.update(TESTING=True)
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(app.state.get("proc"))
        self.assertIsNone(app.state.get("baseline_usd"))
        self.assertEqual(os.listdir(output_dir), [])

    def test_second_key_with_no_plan_fails_closed_not_500(self):
        app = app_module.create_app(state={"cap_usd": 8.41})
        app.config.update(TESTING=True)
        r = app.test_client().post("/second-key", data={"token": "whatever"})
        self.assertEqual(r.status_code, 400)
        self.assertAlmostEqual(app.state["cap_usd"], 8.41, places=2)

    def test_second_key_with_a_different_token_raises_the_cap(self):
        app = self._app(cap=1.00, check_token=lambda t: (2.00, None))
        with mock.patch.dict(os.environ, {"APIFY_TOKEN": "primary-tok"}):
            app.test_client().get("/confirm")
            r = app.test_client().post(
                "/second-key", data={"token": "different-tok"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/confirm", r.headers["Location"])
        self.assertAlmostEqual(app.state["cap_usd"], 3.00, places=2)

    def test_second_key_rejects_the_token_already_on_file(self):
        app = self._app(cap=1.00, check_token=lambda t: (2.00, None))
        with mock.patch.dict(os.environ, {"APIFY_TOKEN": "same-tok"}):
            app.test_client().get("/confirm")
            r = app.test_client().post("/second-key", data={"token": "same-tok"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("same key", r.get_data(as_text=True).lower())
        self.assertAlmostEqual(app.state["cap_usd"], 1.00, places=2)

    def test_second_key_rejects_an_empty_token_the_way_the_key_screen_does(self):
        # /key answered this with "Paste your Apify token."; /second-key sent
        # the blank straight to check_token and reported it as "rejected by
        # Apify", blaming the service for the user's empty field.
        calls = []
        app = self._app(check_token=lambda t: calls.append(t) or (5.0, None))
        client = app.test_client()
        client.get("/confirm")          # establishes the plan
        r = client.post("/second-key", data={"token": "  "})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Paste your Apify token", r.get_data(as_text=True))
        self.assertEqual(calls, [])          # no network call for a blank

    def test_second_key_rejects_a_malformed_token_before_the_write_funnel(self):
        # A value with a space used to reach app.write_env, where the
        # allowlist raises ValueError — a 500 rather than a clean message.
        calls = []
        app = self._app(check_token=lambda t: calls.append(t) or (5.0, None))
        client = app.test_client()
        client.get("/confirm")          # establishes the plan
        r = client.post("/second-key", data={"token": "abc def"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("printable characters", r.get_data(as_text=True))
        self.assertEqual(calls, [])

    def test_second_key_that_verifies_with_zero_credit_shows_a_message(self):
        app = self._app(cap=1.00, check_token=lambda t: (0.0, None))
        with mock.patch.dict(os.environ, {"APIFY_TOKEN": "primary-tok"}):
            app.test_client().get("/confirm")
            r = app.test_client().post(
                "/second-key", data={"token": "zero-credit-tok"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no credit", r.get_data(as_text=True).lower())
        self.assertAlmostEqual(app.state["cap_usd"], 1.00, places=2)


import json as _json
import signal as _signal


# RAW_PLAN builds its searches as [{...}] * 32 — 32 references to one
# identical dict, so combo_keys yields 32 byte-identical keys and
# done=keys[:5] collapses to a one-element set. This fixture uses distinct
# keywords per search so done/outstanding counts are real (see task-8-brief
# Amendment A1). 46 searches total, matching every "46" the given tests check.
RUNNING_PLAN = {
    "profile": "kanav",
    "sites": {
        "linkedin": [{"keywords": f"kw{i}", "location": "India", "company": ""}
                     for i in range(32)],
        "indeed": [{"keywords": f"kw{i}", "location": "Pune", "company": ""}
                   for i in range(14)],
    },
    "free_sources": 6,
}


class TestRunningScreen(unittest.TestCase):
    def _app(self, done=(), alive=True, read_spend=None, now=None):
        proc = FakeProc()
        if not alive:
            proc.poll = lambda: 0
        state = {"profile": "kanav", "cap_usd": 8.41, "proc": proc,
                 "baseline_usd": 1.00,
                 "raw_plan": RUNNING_PLAN, "plan": {"total": 2.70,
                                                "total_searches": 46,
                                                "over_cap": False, "lines": []}}
        kwargs = {}
        if now is not None:
            kwargs["now"] = now
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RUNNING_PLAN,
            start_sweep=lambda profile: proc,
            read_spend=read_spend or (lambda: 2.42),
            read_done=lambda profile, day: set(done),
            **kwargs)
        app.config.update(TESTING=True)
        return app, state, proc

    def test_running_renders_one_tile_per_planned_search(self):
        app, _, _ = self._app()
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn("46", body)

    def test_the_header_meter_tracks_the_sweep_instead_of_freezing(self):
        # running.html overrode meter_label but not meter_scope or
        # meter_numeral_bind, so the header numeral sat at its first value
        # for the whole ~40-minute sweep while the body updated over SSE.
        body = self._app()[0].test_client().get("/running").get_data(as_text=True)
        self.assertIn("Spent so far", body)
        # Same Alpine scope the body already uses, and the numeral bound to
        # the same p.spend the body reads.
        self.assertIn("p.spend", body.split("<main>")[0])
        self.assertIn('x-text', body.split("<main>")[0])

    def test_the_meter_bar_shows_the_share_of_credit_used(self):
        # 2.42 read minus a 1.00 baseline is 1.42 of 8.41 credit = 17%.
        body = self._app()[0].test_client().get("/running").get_data(as_text=True)
        self.assertIn("width: 17%", body)

    def test_progress_reports_spend_as_a_delta_from_the_baseline(self):
        app, _, _ = self._app()
        payload = app.test_client().get("/progress").get_json()
        # 2.42 read now minus 1.00 baseline
        self.assertAlmostEqual(payload["spend"], 1.42, places=2)

    def test_progress_counts_finished_searches(self):
        app, state, _ = self._app()
        keys = app_module.planned_keys(state)
        app, state, _ = self._app(done=keys[:5])
        payload = app.test_client().get("/progress").get_json()
        self.assertEqual(payload["done"], 5)
        self.assertEqual(payload["planned"], 46)

    def test_a_dead_process_with_work_left_is_reported_as_interrupted(self):
        app, state, _ = self._app(done=[], alive=False)
        payload = app.test_client().get("/progress").get_json()
        self.assertTrue(payload["interrupted"])
        self.assertEqual(payload["outstanding"], 46)

    def test_a_dead_process_with_no_work_left_is_finished_not_interrupted(self):
        app, state, _ = self._app()
        keys = app_module.planned_keys(state)
        app, state, _ = self._app(done=keys, alive=False)
        payload = app.test_client().get("/progress").get_json()
        self.assertFalse(payload["interrupted"])
        self.assertTrue(payload["finished"])

    def test_the_event_stream_is_sse(self):
        app, _, _ = self._app()
        r = app.test_client().get("/events")
        self.assertTrue(r.headers["Content-Type"].startswith("text/event-stream"))

    def test_stopping_signals_the_process_and_keeps_what_was_fetched(self):
        app, state, proc = self._app()
        r = app.test_client().post("/stop")
        self.assertEqual(r.status_code, 302)
        self.assertIn(_signal.SIGINT, proc.signals)

    # -- Amendment A2: a None baseline must not be subtracted as if it were 0

    def test_an_unknown_baseline_is_not_subtracted_as_zero(self):
        app, state, _ = self._app()
        state["baseline_usd"] = None
        payload = app.test_client().get("/progress").get_json()
        self.assertAlmostEqual(payload["spend"], 2.42, places=2)
        self.assertFalse(payload["baseline_known"])

    def test_an_unavailable_spend_reading_is_null_not_zero(self):
        app, state, _ = self._app(read_spend=lambda: None)
        payload = app.test_client().get("/progress").get_json()
        self.assertIsNone(payload["spend"])
        self.assertFalse(payload["spend_known"])

    # -- Review round 1: /progress and /events reachable on empty state ----

    def test_progress_without_a_plan_is_409_not_a_500(self):
        # Reachable without going through /run — a bookmark, a stale tab
        # after a reset, curl during manual testing. snapshot() reaches
        # state["raw_plan"]/["profile"] by bracket access, so this must not
        # fall through to a KeyError-driven 500.
        app = app_module.create_app(state={})
        app.config.update(TESTING=True)
        r = app.test_client().get("/progress")
        self.assertEqual(r.status_code, 409)

    def test_events_without_a_plan_is_409_not_a_500(self):
        # Same guard as /progress. A plain error status, not the 302
        # /running redirects with — EventSource would follow a redirect
        # into HTML rather than treat it as a closed stream.
        app = app_module.create_app(state={})
        app.config.update(TESTING=True)
        r = app.test_client().get("/events")
        self.assertEqual(r.status_code, 409)

    # -- Review round 1: A3's throttle has to actually skip a re-read -----

    def test_spend_is_read_once_across_two_requests_within_the_poll_window(self):
        calls = []

        def read_spend():
            calls.append(1)
            return 2.42

        clock = [100.0]
        app, _, _ = self._app(read_spend=read_spend, now=lambda: clock[0])
        client = app.test_client()
        client.get("/progress")
        client.get("/progress")
        self.assertEqual(len(calls), 1)

    def test_spend_is_reread_once_the_poll_window_elapses(self):
        calls = []

        def read_spend():
            calls.append(1)
            return 2.42

        clock = [100.0]
        app, _, _ = self._app(read_spend=read_spend, now=lambda: clock[0])
        client = app.test_client()
        client.get("/progress")
        clock[0] += app_module.SPEND_POLL_SECONDS + 1
        client.get("/progress")
        self.assertEqual(len(calls), 2)

    # -- Review round 1: the amber/teal tile mapping needs direct coverage -

    def test_tile_free_flag_matches_site_rates(self):
        plan = {
            "profile": "kanav",
            "sites": {
                "linkedin": [{"keywords": "kw0", "location": "India",
                              "company": ""}],
                # Not in config.SITE_RATES, so it's the free/teal case.
                "remoteok": [{"keywords": "kw0", "location": "Remote",
                             "company": ""}],
            },
            "free_sources": 0,
        }
        state = {"profile": "kanav", "cap_usd": 8.41, "proc": FakeProc(),
                 "baseline_usd": 1.00, "raw_plan": plan,
                 "plan": {"total": 0.045, "total_searches": 2,
                          "over_cap": False, "lines": []}}
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: plan,
            start_sweep=lambda profile: state["proc"],
            read_spend=lambda: 2.42,
            read_done=lambda profile, day: set())
        app.config.update(TESTING=True)
        payload = app.test_client().get("/progress").get_json()
        tiles_by_site = {t["site"]: t for t in payload["tiles"]}
        self.assertFalse(tiles_by_site["linkedin"]["free"])
        self.assertTrue(tiles_by_site["remoteok"]["free"])


# Column names are the REAL ones from output/<profile>/jobs_*.csv, verified
# against output/global_all/jobs_combined.csv. Lowercase snake_case, and the
# remote flag is literally "remote?" including the question mark.
# Six real rows, copied verbatim out of output/global_all/jobs_combined.csv
# (a paid 1607-row sweep), at the CSV line numbers named below. Every column
# is kept, because the classification reads remote_scope / remote_regions /
# location and the previous hand-written fixture omitted remote_scope
# entirely — and used "visa": "needs sponsorship", a value the pipeline
# cannot emit (enrich.visa returns only "", "no" or "yes"). That fixture is
# why review passed a split that mis-filed 1307 of those 1607 rows.
#
# Four of the six were classified WRONGLY by the rule this replaces, so the
# fixture can fail: lines 102, 25, 476 and 890 below.
ROWS = [
    # line 25: bucket() -> 'abroad'. Remote but geo-locked OUTSIDE India, so
    # unreachable from here. The old rule read remote? == "True" and called it
    # "Genuinely remote from anywhere" — 170 rows on the real file.
    {
        'score': '47',
        'matched_skills': 'node, node.js, express, mongodb, redis, websockets, jwt, oauth, restful, mysql, ci/cd, agile',
        'is_fullstack': 'True', 'title': 'Node.JS Developer',
        'company': 'Inetum', 'location': 'Morocco, Remote', 'remote?': 'True',
        'remote_scope': 'restricted', 'hires_home': '', 'tz_gap': '4.5',
        'remote_regions': '', 'visa': '', 'eor': '', 'timezones': 'UTC+1',
        'experience_required': 'Full Time', 'salary': '', 'hr_email': '',
        'hr_phone': '', 'source_site': 'himalayas',
        'apply_url': 'https://himalayas.app/companies/inetum/jobs/node-js-developer-8086996543',
        'date_posted': '2026-08-25', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
    # line 74: bucket() -> 'remote'. Genuinely location-independent.
    {
        'score': '39',
        'matched_skills': 'node, node.js, express, react, react.js, typescript, mongodb, redis, restful, mysql, ci/cd, html, css',
        'is_fullstack': 'True',
        'title': 'Senior Full Stack Developer - PHP Laravel',
        'company': 'Newrich Network', 'location': 'Anywhere in the World',
        'remote?': 'True', 'remote_scope': 'worldwide', 'hires_home': '',
        'tz_gap': '', 'remote_regions': '', 'visa': '', 'eor': '',
        'timezones': '', 'experience_required': '', 'salary': '', 'hr_email': '',
        'hr_phone': '', 'source_site': 'wwr',
        'apply_url': 'https://weworkremotely.com/remote-jobs/newrich-network-senior-full-stack-developer-php-laravel',
        'date_posted': '2026-08-19', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
    # line 102: bucket() -> 'abroad'. Onsite in Taipei. The old rule saw an
    # empty visa and remote? == "False" and filed it under "You can work here
    # now" — 1046 rows on the real file.
    {
        'score': '36',
        'matched_skills': 'node, node.js, react, typescript, javascript, redux, jest, html, css, agile',
        'is_fullstack': 'True', 'title': 'Frontend Developer(UniFi Connect)',
        'company': 'Ubiquiti', 'location': 'Taipei', 'remote?': 'False',
        'remote_scope': '', 'hires_home': 'no', 'tz_gap': '',
        'remote_regions': '', 'visa': '', 'eor': '', 'timezones': '',
        'experience_required': '', 'salary': '', 'hr_email': '', 'hr_phone': '',
        'source_site': 'greenhouse:ubiquiti',
        'apply_url': 'https://job-boards.greenhouse.io/ubiquiti/jobs/4193274009',
        'date_posted': '2026-08-14', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
    # line 413: bucket() -> 'india'. Onsite in Bengaluru.
    {
        'score': '22',
        'matched_skills': 'node, node.js, react, react.js, ci/cd, agile, llm, llms, langchain, agentic, ai agent, ai agents, prompt engineering, python',
        'is_fullstack': 'True', 'title': 'Full Stack Developer (AI Agents)',
        'company': 'Databricks', 'location': 'Bengaluru, India',
        'remote?': 'False', 'remote_scope': '', 'hires_home': 'yes',
        'tz_gap': '', 'remote_regions': '', 'visa': '', 'eor': '',
        'timezones': '', 'experience_required': '3+', 'salary': '',
        'hr_email': '', 'hr_phone': '', 'source_site': 'greenhouse:databricks',
        'apply_url': 'https://databricks.com/company/careers/open-positions/job?gh_jid=8632126002',
        'date_posted': '2026-09-01', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
    # line 476: bucket() -> 'abroad'. visa == "no" is an explicit REFUSAL to
    # sponsor (enrich.visa; scraper.py:1179 prints "refuse visa sponsorship").
    # The old rule filed any non-empty visa under "Needs visa sponsorship" —
    # the inverse of the label, 91 rows on the real file.
    {
        'score': '20',
        'matched_skills': 'typescript, restful, ci/cd, agile, llm, agentic, ai agent, prompt engineering, openai, mcp, python',
        'is_fullstack': 'True',
        'title': 'Software Engineer, Enterprise Integrations',
        'company': 'Cloudflare', 'location': 'Hybrid', 'remote?': 'False',
        'remote_scope': 'hybrid', 'hires_home': 'yes', 'tz_gap': '11.5',
        'remote_regions': 'US', 'visa': 'no', 'eor': 'Multiplier',
        'timezones': '', 'experience_required': '3+', 'salary': '',
        'hr_email': 'hr@cloudflare.com', 'hr_phone': '',
        'source_site': 'greenhouse:cloudflare',
        'apply_url': 'https://boards.greenhouse.io/cloudflare/jobs/8155495?gh_jid=8155495',
        'date_posted': '2026-08-25', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
    # line 890: bucket() -> 'india'. Remote, geo-locked to a list that
    # INCLUDES India, so it is reachable from here. The old rule filed it as
    # "Genuinely remote from anywhere", which it is not.
    {
        'score': '9',
        'matched_skills': 'agile, agentic, python, artificial intelligence',
        'is_fullstack': 'True',
        'title': 'Senior Backend Engineer (Ruby on Rails), Plan: Planning Views',
        'company': 'GitLab', 'location': 'Bangalore, India', 'remote?': 'True',
        'remote_scope': 'restricted', 'hires_home': 'yes', 'tz_gap': '0.0',
        'remote_regions': 'Australia, India, Europe', 'visa': '',
        'eor': 'Multiplier', 'timezones': '', 'experience_required': '',
        'salary': '', 'hr_email': '', 'hr_phone': '',
        'source_site': 'greenhouse:gitlab',
        'apply_url': 'https://job-boards.greenhouse.io/gitlab/jobs/8695815002',
        'date_posted': '2026-08-25', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
]


class TestResultsScreen(unittest.TestCase):
    def _app(self, rows=None, start_rescore=None):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "spend": 2.70,
                   "plan": {"total": 2.70, "total_searches": 46, "lines": []}},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            read_rows=lambda profile: ROWS if rows is None else rows,
            start_rescore=start_rescore)
        app.config.update(TESTING=True)
        return app

    def test_rows_are_grouped_into_the_three_reachability_buckets(self):
        body = self._app().test_client().get("/results").get_data(as_text=True)
        self.assertIn("Onsite and hybrid in India", body)
        self.assertIn("Fully remote", body)
        self.assertIn("Onsite abroad", body)

    def test_each_row_lands_in_exactly_one_bucket(self):
        buckets = app_module.bucket_rows(ROWS)
        # Counted from the real rows, not from the split's own opinion: the
        # Taipei and Morocco rows are abroad, the Cloudflare refusal is
        # abroad too, and the India-locked GitLab remote row is reachable.
        self.assertEqual([r["company"] for r in buckets["india"]],
                         ["Databricks", "GitLab"])
        self.assertEqual([r["company"] for r in buckets["remote"]],
                         ["Newrich Network"])
        self.assertEqual([r["company"] for r in buckets["abroad"]],
                         ["Inetum", "Ubiquiti", "Cloudflare"])
        self.assertEqual(sum(len(v) for v in buckets.values()), len(ROWS))

    def test_the_split_is_the_ported_one_not_a_fourth_rule(self):
        # The spec called this screen a port of linkedin_shortlist.bucket().
        # If this screen ever grows its own classification again, this fails.
        import linkedin_shortlist
        buckets = app_module.bucket_rows(ROWS)
        for key, rows in buckets.items():
            for row in rows:
                self.assertEqual(linkedin_shortlist.bucket(row), key,
                                 row["company"])

    def test_an_explicit_sponsorship_refusal_is_not_labelled_as_needing_one(self):
        # enrich.visa returns "no" when the posting REFUSES to sponsor, "" in
        # the common case where it never mentions it, "yes" when it offers.
        # The old rule read any non-empty value as "needs sponsorship".
        refusal = [r for r in ROWS if r["visa"] == "no"]
        self.assertEqual(len(refusal), 1)
        self.assertEqual(app_module.bucket_rows(refusal),
                         {"india": [], "remote": [], "abroad": refusal})

    def test_filtering_by_score_is_free_and_says_so(self):
        body = self._app().test_client().get("/results?min=40").get_data(as_text=True)
        self.assertIn("Filtering and re-ranking these is free", body)
        self.assertIn("Node.JS Developer", body)          # score 47
        self.assertNotIn("Full Stack Developer (AI Agents)", body)   # score 22

    def test_an_empty_result_names_the_filter_that_removed_the_most(self):
        # Asserted as the rendered SENTENCE with its numbers. "score" alone is
        # satisfied by the "Minimum score" input label and "0 listings" by a
        # static heading, so neither reached the feature: replacing
        # worst_filter's body with `return None` left the suite green.
        body = self._app().test_client().get(
            "/results?min=999&source=wwr").get_data(as_text=True)
        flat = " ".join(body.split())
        self.assertIn("The minimum score filter removed the most — 6 of 6. "
                      "Loosen it to see more.", flat)
        # The clear-link must drop only the blamed filter and carry the rest.
        self.assertIn('href="/results?source=wwr"', body)

    def test_the_clear_link_drops_the_blamed_filter_and_keeps_the_others(self):
        # min=1 removes nothing (every fixture score is >= 9), so the source
        # filter is the culprit and the link has to preserve min.
        body = self._app().test_client().get(
            "/results?min=1&source=nosuchboard").get_data(as_text=True)
        flat = " ".join(body.split())
        self.assertIn("The source filter removed the most — 6 of 6. "
                      "Loosen it to see more.", flat)
        self.assertIn('href="/results?min=1"', body)

    def test_the_text_filter_can_be_the_one_blamed(self):
        body = self._app().test_client().get(
            "/results?min=1&q=nothingmatchesthis").get_data(as_text=True)
        flat = " ".join(body.split())
        self.assertIn("The search text filter removed the most — 6 of 6. "
                      "Loosen it to see more.", flat)
        self.assertIn('href="/results?min=1"', body)

    def test_the_empty_result_diagnosis_is_not_computed_when_rows_survive(self):
        # Three passes over up to 1607 rows, consumed only when total == 0.
        calls = []
        with mock.patch.object(app_module, "worst_filter",
                               lambda *a: calls.append(a)):
            body = self._app().test_client().get("/results").get_data(as_text=True)
        self.assertIn("Onsite abroad", body)
        self.assertEqual(calls, [])

    def test_the_results_meter_never_shows_the_estimate_as_money_spent(self):
        # /configure wrote app.state["spend"] = estimate["total"] and
        # /results never overwrote it, so the meter read the ESTIMATE under
        # the label "Spent" even when nothing had been spent at all.
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "derived": DERIVED},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            read_rows=lambda profile: ROWS)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: None
        client = app.test_client()
        configure = client.get("/configure").get_data(as_text=True)
        self.assertIn("$2.70", configure)             # the estimate, correctly
        header = client.get("/results").get_data(as_text=True).split("<main>")[0]
        self.assertNotIn("2.70", header)
        self.assertIn("not known", header)

    def test_the_results_meter_shows_the_real_delta_once_a_run_recorded_one(self):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41,
                   "baseline_usd": 1.00, "spend_read_val": 2.42},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            read_rows=lambda profile: ROWS)
        app.config.update(TESTING=True)
        header = app.test_client().get("/results").get_data(as_text=True).split("<main>")[0]
        self.assertIn("$1.42", header)

    def test_results_with_no_sweep_yet_goes_back_to_upload(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        self.assertEqual(app.test_client().get("/results").status_code, 302)

    def test_rescoring_runs_the_free_rescore_for_this_profile(self):
        calls = []
        app = self._app(start_rescore=lambda profile, hours: calls.append((profile, hours)))
        r = app.test_client().post("/rescore", data={"hours": "6"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(calls, [("kanav", 6)])

    def test_an_out_of_range_hours_value_is_rejected_and_nothing_runs(self):
        calls = []
        app = self._app(start_rescore=lambda profile, hours: calls.append((profile, hours)))
        r = app.test_client().post("/rescore", data={"hours": "9999"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(calls, [])

    def test_a_site_listed_at_zero_is_free_not_paid(self):
        app = self._app(rows=[dict(ROWS[0], source_site="freebie")])
        with mock.patch.dict(app_module.config.SITE_RATES, {"freebie": 0.0}):
            body = app.test_client().get("/results").get_data(as_text=True)
        self.assertIn('class="free"', body)

    def test_the_source_filter_keeps_only_that_source(self):
        body = self._app().test_client().get(
            "/results?source=wwr").get_data(as_text=True)
        self.assertIn("Senior Full Stack Developer - PHP Laravel", body)
        self.assertNotIn("Node.JS Developer", body)

    def test_the_text_filter_matches_title_and_company(self):
        client = self._app().test_client()
        by_company = client.get("/results?q=databricks").get_data(as_text=True)
        self.assertIn("Full Stack Developer (AI Agents)", by_company)
        self.assertNotIn("Node.JS Developer", by_company)
        by_title = client.get("/results?q=node.js+developer").get_data(as_text=True)
        self.assertIn("Node.JS Developer", by_title)
        self.assertNotIn("Full Stack Developer (AI Agents)", by_title)

    def test_active_filters_survive_in_the_rendered_form(self):
        body = self._app().test_client().get(
            "/results?min=40&source=wwr&q=newrich").get_data(as_text=True)
        self.assertIn('name="min" value="40"', body)
        self.assertIn('value="wwr" selected', body)
        self.assertIn('name="q" value="newrich"', body)

    def test_rows_are_sorted_by_score_within_a_bucket(self):
        # Both rows must land in the SAME bucket, or SECTIONS' own order
        # decides the comparison and the test passes with no sort at all.
        low = dict(ROWS[0], score="10", title="Lower scoring role")
        high = dict(ROWS[0], score="99", title="Higher scoring role")
        app = self._app(rows=[low, high])          # worst first on input
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertLess(body.index("Higher scoring role"),
                        body.index("Lower scoring role"))

    def test_a_running_rescore_says_so_on_the_page_it_redirects_to(self):
        # The signal has to survive the 302: it was previously nested inside
        # the re-rank <details>, which comes back collapsed after navigation.
        class Running:
            def poll(self):
                return None

        app = self._app(start_rescore=lambda profile, hours: Running())
        client = app.test_client()
        client.post("/rescore", data={"hours": "6"})
        body = client.get("/results").get_data(as_text=True)
        self.assertIn("Re-ranking now", body)
        self.assertLess(body.index("Re-ranking now"), body.index("<details"))

    def test_a_busy_rescore_message_is_not_styled_as_an_error(self):
        class Running:
            def poll(self):
                return None

        app = self._app(start_rescore=lambda profile, hours: Running())
        client = app.test_client()
        client.post("/rescore", data={"hours": "6"})
        body = client.post("/rescore", data={"hours": "6"}).get_data(as_text=True)
        self.assertIn("already running", body)
        # class="error" is painted the red reserved for over-cap.
        self.assertNotIn('class="error"', body)
        # The notice IS the response to this click, so the standing banner
        # must not restate the same fact in a second treatment beside it.
        self.assertEqual(body.count("Reload in a moment"), 1)

    def test_an_uppercase_scheme_keeps_its_apply_link(self):
        app = self._app(rows=[dict(ROWS[0], apply_url="HTTPS://Board.example/job")])
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertIn("HTTPS://Board.example/job", body)
        self.assertNotIn("No link", body)

    def test_a_filter_that_removed_nothing_is_not_blamed(self):
        # No shortlist on disk yet: every branch counts 0 removals, and the
        # page must not claim a filter removed "0 of 0".
        app = self._app(rows=[])
        body = app.test_client().get("/results?min=90").get_data(as_text=True)
        self.assertIn("Nothing was found for this profile yet", body)
        self.assertNotIn("removed the most", body)

    def test_a_second_rescore_is_refused_while_one_is_still_running(self):
        calls = []

        class Running:
            def poll(self):
                return None

        app = self._app(start_rescore=lambda profile, hours: (
            calls.append((profile, hours)) or Running()))
        client = app.test_client()
        self.assertEqual(client.post("/rescore", data={"hours": "6"}).status_code, 302)
        second = client.post("/rescore", data={"hours": "6"})
        self.assertEqual(second.status_code, 409)
        self.assertIn("already running", second.get_data(as_text=True))
        # rescore_from_apify.py truncates jobs_combined.csv in place, so the
        # second child must never have been started.
        self.assertEqual(calls, [("kanav", 6)])

    def test_a_finished_rescore_does_not_block_the_next_one(self):
        calls = []

        class Finished:
            def poll(self):
                return 0

        app = self._app(start_rescore=lambda profile, hours: (
            calls.append((profile, hours)) or Finished()))
        client = app.test_client()
        client.post("/rescore", data={"hours": "6"})
        self.assertEqual(client.post("/rescore", data={"hours": "12"}).status_code, 302)
        self.assertEqual(calls, [("kanav", 6), ("kanav", 12)])

    def test_a_non_http_apply_url_is_not_rendered_as_a_link(self):
        app = self._app(rows=[dict(ROWS[0], apply_url="javascript:alert(1)")])
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertNotIn("javascript:alert(1)", body)
        self.assertIn("No link", body)


class TestReadRowsDefault(unittest.TestCase):
    """The default read_rows closure. Every route test injects read_rows, so
    without this the jobs_combined-not-jobs_* rule A2 exists to enforce runs
    in production and nowhere else."""

    def _out_dir(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out)
        os.makedirs(os.path.join(out, "kanav"))
        return out

    def _write(self, out, name, score, title):
        path = os.path.join(out, "kanav", name)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write("score,title\n%s,%s\n" % (score, title))
        return path

    def _app(self, out):
        app = app_module.create_app(
            state={"profile": "kanav"}, extract=lambda p: "x",
            derive=lambda t, p: DERIVED, output_dir=out)
        app.config.update(TESTING=True)
        return app

    def test_the_merged_shortlist_wins_over_a_newer_partial_file(self):
        out = self._out_dir()
        combined = self._write(out, "jobs_combined.csv", "96", "From combined")
        partial = self._write(out, "jobs_2026-09-08_1200.csv", "50", "From partial")
        # Partial file deliberately NEWER — a jobs_*.csv glob would pick it and
        # show a fraction of the sweep as if it were the whole result.
        os.utime(combined, (1_000_000, 1_000_000))
        os.utime(partial, (2_000_000, 2_000_000))
        body = self._app(out).test_client().get("/results").get_data(as_text=True)
        self.assertIn("From combined", body)
        self.assertNotIn("From partial", body)

    def test_a_swept_profile_with_no_merge_still_shows_its_results(self):
        # scraper.py never writes a combined file — only merge_jobs.py and a
        # re-score do. Without the fallback a paid sweep finishes and the
        # screen says nothing was found. 8 of 16 real profile directories in
        # this repo are in exactly this state.
        out = self._out_dir()
        self._write(out, "jobs_2026-09-08_1200.csv", "96", "From the sweep")
        body = self._app(out).test_client().get("/results").get_data(as_text=True)
        self.assertIn("From the sweep", body)
        self.assertNotIn("Nothing was found for this profile yet", body)
        self.assertIn("most recent sweep only", body)

    def test_a_merged_shortlist_is_not_labelled_as_one_sweep(self):
        out = self._out_dir()
        self._write(out, "jobs_combined.csv", "96", "From combined")
        body = self._app(out).test_client().get("/results").get_data(as_text=True)
        self.assertIn("From combined", body)
        self.assertNotIn("most recent sweep only", body)

    def test_no_shortlist_yet_reads_as_empty_not_an_error(self):
        out = self._out_dir()
        r = self._app(out).test_client().get("/results")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Nothing was found for this profile yet",
                      r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
