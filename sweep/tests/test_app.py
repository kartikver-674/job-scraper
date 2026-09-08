import io
import os
import shutil
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
