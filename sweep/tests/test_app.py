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


if __name__ == "__main__":
    unittest.main()
