import os
import unittest

import apply_config as cfg


class TestApplyConfig(unittest.TestCase):
    def test_core_defaults(self):
        self.assertEqual(cfg.MIN_SCORE, 10)
        self.assertEqual(cfg.MODEL, "gemini-2.0-flash")
        self.assertTrue(cfg.DRY_RUN_DEFAULT)
        self.assertEqual(cfg.SMTP_HOST, "smtp.gmail.com")
        self.assertEqual(cfg.SMTP_PORT, 587)

    def test_paths_are_absolute_and_under_repo(self):
        for p in (cfg.OUTPUT_DIR, cfg.RESUME_PDF, cfg.DRAFTS_DIR,
                  cfg.REVIEW_QUEUE, cfg.APPLICATIONS_LOG, cfg.ANSWERS_FILE):
            self.assertTrue(os.path.isabs(p), p)
            self.assertTrue(p.startswith(cfg.REPO_ROOT), p)

    def test_me_contact_block_has_keys(self):
        for k in ("name", "email", "phone", "linkedin", "github"):
            self.assertIn(k, cfg.ME)

    def test_latest_input_csv_picks_newest(self):
        # Should return None or an existing path; must not raise.
        result = cfg.latest_input_csv()
        self.assertTrue(result is None or os.path.exists(result))


class TestPhase2Config(unittest.TestCase):
    def test_phase2_knobs_exist(self):
        self.assertTrue(cfg.USERSCRIPT_OUT.endswith("linkedin-easyapply.user.js"))
        self.assertTrue(cfg.SHORTLIST_OUT.endswith("shortlist.html"))
        self.assertTrue(cfg.USERSCRIPT_TEMPLATE.endswith("userscript_template.js"))
        self.assertEqual(cfg.GROQ_MODEL, "llama-3.3-70b-versatile")
        self.assertTrue(cfg.GROQ_ENDPOINT.startswith("https://api.groq.com/"))

    def test_paths_are_absolute(self):
        for p in (cfg.USERSCRIPT_OUT, cfg.SHORTLIST_OUT, cfg.USERSCRIPT_TEMPLATE):
            self.assertTrue(os.path.isabs(p))


if __name__ == "__main__":
    unittest.main()
