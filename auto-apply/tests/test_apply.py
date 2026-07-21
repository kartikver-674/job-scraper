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
        # Seed one approved queue row + a drafted application.
        records.append_review_row(self.cfg.REVIEW_QUEUE, {
            "job_key": "u_jinrai", "to": "career@jinraitech.com",
            "subject": "Application: Full Stack Developer",
            "draft_path": "/d/u_jinrai.md", "status": "approved"})
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


if __name__ == "__main__":
    unittest.main()
