import unittest
import linkedin_shortlist as ls


class TestRenderHtml(unittest.TestCase):
    def test_renders_clickable_anchors(self):
        jobs = [{"score": "30", "title": "Full Stack Dev", "company": "Acme",
                 "location": "Delhi", "apply_url": "https://linkedin.com/jobs/1"}]
        out = ls.render_html(jobs)
        self.assertIn('href="https://linkedin.com/jobs/1"', out)
        self.assertIn('target="_blank"', out)
        self.assertIn("Full Stack Dev", out)
        self.assertIn("Acme", out)

    def test_escapes_html_in_fields(self):
        jobs = [{"score": "10", "title": "Dev <script>", "company": "A&B",
                 "location": "X", "apply_url": "u"}]
        out = ls.render_html(jobs)
        self.assertNotIn("<script>", out)      # escaped
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("A&amp;B", out)

    def test_empty_shows_message(self):
        out = ls.render_html([])
        self.assertIn("No jobs above threshold", out)


if __name__ == "__main__":
    unittest.main()
