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
        # Assert on the FIELD, not the document: the page carries its own
        # <script> block for opened-state tracking, so "no script tag anywhere"
        # tests the wrong thing and fails on a correctly-escaped page.
        self.assertNotIn("Dev <script>", out)  # never emitted raw
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("A&amp;B", out)

    def test_empty_shows_message(self):
        out = ls.render_html([])
        self.assertIn("No jobs above threshold", out)


class BucketTests(unittest.TestCase):
    """Which section a row lands in. Every case here is a real row shape from a
    paid sweep, because the failure mode is silent: a misfiled row still renders
    perfectly, just under the wrong heading."""

    def test_free_remote_is_remote(self):
        self.assertEqual(ls.bucket({"remote_scope": "worldwide",
                                    "location": "Anywhere in the World"}), "remote")
        self.assertEqual(ls.bucket({"remote_scope": "remote",
                                    "location": "United States"}), "remote")

    def test_india_onsite_and_hybrid(self):
        # Verbatim location strings from the 2026-08-25 paid sweep. The third is
        # the one that matters: a district name no city list would carry, filed
        # correctly only because LinkedIn appends the state.
        for loc in ("New Delhi, Delhi, India", "Pune Division, Maharashtra",
                    "Sahibzada Ajit Singh Nagar, Punjab, India",
                    "Bengaluru, Karnataka, India", "Thane, Maharashtra"):
            self.assertEqual(ls.bucket({"remote_scope": "", "location": loc}),
                             "india", loc)
        self.assertEqual(ls.bucket({"remote_scope": "hybrid",
                                    "location": "Gurugram, Haryana"}), "india")

    def test_geo_locked_remote_follows_its_lock(self):
        # "restricted" means remote-but-locked, so it belongs where it is locked.
        self.assertEqual(ls.bucket({"remote_scope": "restricted",
                                    "remote_regions": "India",
                                    "location": "Remote"}), "india")
        self.assertEqual(ls.bucket({"remote_scope": "restricted",
                                    "remote_regions": "Morocco",
                                    "location": "Morocco, Remote"}), "abroad")

    def test_onsite_abroad(self):
        self.assertEqual(ls.bucket({"remote_scope": "onsite",
                                    "location": "Toronto, Ontario, Canada"}), "abroad")

    def test_india_matched_on_word_boundaries(self):
        # The trap: a substring test files US rows under Delhi NCR. One employer's
        # board had 107 "Indiana" rows an India-only sweep would have kept.
        for loc in ("Indianapolis, Indiana", "New Albany, Indiana"):
            self.assertEqual(ls.bucket({"remote_scope": "", "location": loc}),
                             "abroad", loc)


class SectionRenderTests(unittest.TestCase):
    JOBS = [
        {"score": "50", "title": "A", "company": "C", "apply_url": "u1",
         "location": "New Delhi, India", "remote_scope": ""},
        {"score": "40", "title": "B", "company": "C", "apply_url": "u2",
         "location": "Anywhere", "remote_scope": "worldwide"},
        {"score": "30", "title": "D", "company": "C", "apply_url": "u3",
         "location": "Berlin, Germany", "remote_scope": "onsite"},
    ]

    def test_sections_off_by_default(self):
        out = ls.render_html(self.JOBS)
        self.assertNotIn('class="sec"', out)      # other pages must not change

    def test_three_headings_and_every_row_kept(self):
        out = ls.render_html(self.JOBS, sections=True)
        self.assertEqual(out.count('class="sec"'), 3)
        self.assertIn("Onsite &amp; hybrid in India", out)
        self.assertIn("Fully remote", out)
        self.assertIn("needs a visa", out)
        for j in self.JOBS:                        # nothing silently dropped
            self.assertIn(j["apply_url"], out)

    def test_empty_sections_are_omitted(self):
        out = ls.render_html([self.JOBS[0]], sections=True)
        self.assertEqual(out.count('class="sec"'), 1)


if __name__ == "__main__":
    unittest.main()
