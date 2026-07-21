import os
import tempfile
import types
import unittest

import apply
import records
import selection


HEADER = ("score,matched_skills,is_fullstack,title,company,location,remote?,"
          "experience_required,salary,hr_email,hr_phone,source_site,apply_url,date_posted\n")


def _row(score, title, company, email, url):
    return (f'{score},"node, react",True,{title},{company},India,False,'
            f'1-3 Yrs,,{email},,naukri,{url},2026-07-20\n')


def _make_cfg(tmp):
    csv_path = os.path.join(tmp, "jobs.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write(HEADER)
        f.write(_row(28, "Full Stack Developer", "Jinrai", "career@jinraitech.com", "u_jinrai"))
        f.write(_row(-5, "IT Recruiter", "Orcapod", "mamatha.b@orcapod.work", "u_orca"))
    cfg = types.SimpleNamespace(
        MIN_SCORE=10, PER_RUN_CAP=5, MODEL="gemini-2.5-flash",
        DRAFTS_DIR=os.path.join(tmp, "drafts"),
        REVIEW_QUEUE=os.path.join(tmp, "review_queue.csv"),
        APPLICATIONS_LOG=os.path.join(tmp, "applications.csv"),
        RESUME_PDF=os.path.join(tmp, "resume.pdf"),
        ME={"name": "Kartik Verma", "email": "me@gmail.com"},
        SMTP_HOST="smtp.gmail.com", SMTP_PORT=587,
    )
    cfg.latest_input_csv = lambda: csv_path
    return cfg


class TestDryRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = _make_cfg(self.tmp)

    def _tailor(self, job):
        return {"subject": f"Application: {job['title']}",
                "body": "Dear Hiring Team, grounded body.",
                "grounding_notes": "Node/React from résumé."}

    def test_dry_run_drafts_only_qualifying_job(self):
        n = apply.run_dry_run(self.cfg, limit=2, min_score=10,
                              tailor_fn=self._tailor,
                              resume_text_fn=lambda: "RESUME",
                              answers_fn=lambda: {})
        self.assertEqual(n, 1)  # only Jinrai (recruiter row is below threshold)
        rows = records.read_review_rows(self.cfg.REVIEW_QUEUE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "draft")
        self.assertEqual(selection.load_applied_keys(self.cfg.APPLICATIONS_LOG), {"u_jinrai"})

    def test_dry_run_is_idempotent(self):
        opts = dict(limit=2, min_score=10, tailor_fn=self._tailor,
                    resume_text_fn=lambda: "RESUME", answers_fn=lambda: {})
        apply.run_dry_run(self.cfg, **opts)
        n2 = apply.run_dry_run(self.cfg, **opts)
        self.assertEqual(n2, 0)  # already applied -> nothing new
        self.assertEqual(len(records.read_review_rows(self.cfg.REVIEW_QUEUE)), 1)


class TestSend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = _make_cfg(self.tmp)
        # Seed one approved queue row + a drafted application, backed by a real
        # draft file (with proper --- fences) so the empty-body guard doesn't
        # skip it.
        self.draft_path = records.write_draft_md(
            self.cfg.DRAFTS_DIR, "u_jinrai", "career@jinraitech.com",
            "Application: Full Stack Developer",
            "Dear Hiring Team, grounded body.", "Node/React from résumé.")
        records.append_review_row(self.cfg.REVIEW_QUEUE, {
            "job_key": "u_jinrai", "to": "career@jinraitech.com",
            "subject": "Application: Full Stack Developer",
            "draft_path": self.draft_path, "status": "approved"})
        records.upsert_application(self.cfg.APPLICATIONS_LOG, "u_jinrai",
                                   "Jinrai", "Full Stack Developer", "email", "drafted")

    def test_send_only_approved_and_confirmed(self):
        sent = []
        n = apply.run_send(self.cfg, limit=5, delay=0,
                           confirm_fn=lambda row: True,
                           send_fn=lambda to, subject, body: sent.append(to))
        self.assertEqual(n, 1)
        self.assertEqual(sent, ["career@jinraitech.com"])
        self.assertEqual(records.read_review_rows(self.cfg.REVIEW_QUEUE)[0]["status"], "sent")

    def test_send_skips_when_declined(self):
        sent = []
        n = apply.run_send(self.cfg, limit=5, delay=0,
                           confirm_fn=lambda row: False,
                           send_fn=lambda to, subject, body: sent.append(to))
        self.assertEqual(n, 0)
        self.assertEqual(sent, [])
        self.assertEqual(records.read_review_rows(self.cfg.REVIEW_QUEUE)[0]["status"], "approved")

    def test_send_skips_and_does_not_mark_sent_when_draft_body_missing(self):
        # A separate approved row pointing at a draft_path that does not exist
        # on disk (e.g. deleted/moved draft, or one that never had fences).
        queue_path = os.path.join(self.tmp, "review_queue_missing.csv")
        records.append_review_row(queue_path, {
            "job_key": "u_missing", "to": "career@jinraitech.com",
            "subject": "Application: Full Stack Developer",
            "draft_path": os.path.join(self.tmp, "does_not_exist.md"),
            "status": "approved"})
        cfg2 = _make_cfg(self.tmp)
        cfg2.REVIEW_QUEUE = queue_path

        sent = []
        n = apply.run_send(cfg2, limit=5, delay=0,
                           confirm_fn=lambda row: True,
                           send_fn=lambda to, subject, body: sent.append(to))
        self.assertEqual(n, 0)
        self.assertEqual(sent, [])
        self.assertEqual(
            records.read_review_rows(cfg2.REVIEW_QUEUE)[0]["status"], "approved")


class TestReadBodyFromDraft(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_missing_path_returns_empty_string(self):
        self.assertEqual(apply._read_body_from_draft(""), "")
        self.assertEqual(
            apply._read_body_from_draft(os.path.join(self.tmp, "nope.md")), "")

    def test_no_fences_returns_empty_string_not_whole_file(self):
        path = os.path.join(self.tmp, "no_fences.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Draft application\n\n**To:** x@y.com\n\n"
                    "**Subject:** hi\n\nNo fences here at all.\n")
        self.assertEqual(apply._read_body_from_draft(path), "")

    def test_well_formed_draft_extracts_body(self):
        path = records.write_draft_md(
            self.tmp, "job1", "x@y.com", "Subject line",
            "This is the body.", "notes")
        self.assertEqual(apply._read_body_from_draft(path), "This is the body.")


if __name__ == "__main__":
    unittest.main()
