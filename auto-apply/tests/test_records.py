import os
import tempfile
import unittest

import records


class TestRecords(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.drafts = os.path.join(self.dir, "drafts")
        self.queue = os.path.join(self.dir, "review_queue.csv")
        self.apps = os.path.join(self.dir, "applications.csv")

    def test_write_draft_md_creates_readable_file(self):
        path = records.write_draft_md(self.drafts, "https://x.com/job/1",
                                      "hr@co.com", "Subject Here",
                                      "Body line one.", "Grounded in X.")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("hr@co.com", text)
        self.assertIn("Subject Here", text)
        self.assertIn("Body line one.", text)
        self.assertIn("Grounded in X.", text)

    def test_review_queue_append_and_read_and_status(self):
        row = {"job_key": "k1", "to": "hr@co.com", "subject": "S",
               "draft_path": "/d/k1.md", "status": "draft"}
        records.append_review_row(self.queue, row)
        rows = records.read_review_rows(self.queue)
        self.assertEqual(rows[0]["status"], "draft")
        records.set_review_status(self.queue, "k1", "sent")
        self.assertEqual(records.read_review_rows(self.queue)[0]["status"], "sent")

    def test_upsert_application_is_idempotent(self):
        records.upsert_application(self.apps, "k1", "Jinrai", "FSD", "email", "drafted")
        records.upsert_application(self.apps, "k1", "Jinrai", "FSD", "email", "drafted")
        import csv
        with open(self.apps, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)

    def test_upsert_application_updates_status(self):
        records.upsert_application(self.apps, "k1", "Jinrai", "FSD", "email", "drafted")
        records.upsert_application(self.apps, "k1", "Jinrai", "FSD", "email", "sent")
        import csv
        with open(self.apps, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "sent")


if __name__ == "__main__":
    unittest.main()
