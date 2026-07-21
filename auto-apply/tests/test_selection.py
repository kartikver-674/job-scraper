import os
import tempfile
import unittest

import selection

HEADER = ("score,matched_skills,is_fullstack,title,company,location,remote?,"
          "experience_required,salary,hr_email,hr_phone,source_site,apply_url,date_posted\n")


def _row(score, title, company, email, url):
    return (f'{score},"node, react",True,{title},{company},India,False,'
            f'1-3 Yrs,,{email},,naukri,{url},2026-07-20\n')


class TestSelection(unittest.TestCase):
    def _csv(self, rows):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(HEADER)
            for r in rows:
                f.write(r)
        self.addCleanup(os.remove, path)
        return path

    def test_load_jobs_reads_columns(self):
        path = self._csv([_row(28, "Full Stack Developer", "Jinrai", "career@jinraitech.com", "u1")])
        jobs = selection.load_jobs(path)
        self.assertEqual(jobs[0]["company"], "Jinrai")
        self.assertEqual(jobs[0]["hr_email"], "career@jinraitech.com")

    def test_select_filters_threshold_and_email_and_dedupe(self):
        rows = [
            _row(28, "Full Stack Developer", "Jinrai", "career@jinraitech.com", "u_jinrai"),
            _row(7, "Fullstack Python Developer", "Elastiq", "careers@elastiq.ai", "u_elastiq"),
            _row(-5, "IT Recruiter", "Orcapod", "mamatha.b@orcapod.work", "u_orca"),
            _row(40, "Full Stack Engineer", "NoEmailCo", "", "u_noemail"),
        ]
        path = self._csv(rows)
        jobs = selection.load_jobs(path)
        picked = selection.select_candidates(jobs, min_score=10, applied_keys=set(), limit=None)
        keys = [selection.job_key(j) for j in picked]
        self.assertEqual(keys, ["u_jinrai"])  # only Jinrai clears score>=10 AND has email

    def test_dedupe_against_applied(self):
        rows = [_row(28, "Full Stack Developer", "Jinrai", "career@jinraitech.com", "u_jinrai")]
        path = self._csv(rows)
        jobs = selection.load_jobs(path)
        picked = selection.select_candidates(jobs, 10, {"u_jinrai"}, None)
        self.assertEqual(picked, [])

    def test_limit_applied(self):
        rows = [_row(50, "Full Stack A", "A", "a@x.com", "ua"),
                _row(40, "Full Stack B", "B", "b@x.com", "ub")]
        path = self._csv(rows)
        jobs = selection.load_jobs(path)
        picked = selection.select_candidates(jobs, 10, set(), limit=1)
        self.assertEqual([selection.job_key(j) for j in picked], ["ua"])

    def test_load_applied_keys(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write("job_key,company,title,channel,status,timestamp\n")
            f.write("u_jinrai,Jinrai,Full Stack Developer,email,drafted,2026-07-21T10:00:00\n")
        self.addCleanup(os.remove, path)
        self.assertEqual(selection.load_applied_keys(path), {"u_jinrai"})

    def test_load_applied_keys_missing_file(self):
        self.assertEqual(selection.load_applied_keys("/no/such.csv"), set())

    def test_non_numeric_score_treated_as_zero(self):
        """Test that non-numeric scores are safely degraded to 0 without crashing."""
        rows = [
            _row("", "Full Stack Developer", "EmptyScoreCo", "empty@example.com", "u_empty"),
            _row("N/A", "Full Stack Engineer", "NACo", "na@example.com", "u_na"),
        ]
        path = self._csv(rows)
        jobs = selection.load_jobs(path)

        # With min_score=10, both should be filtered out (score 0 < 10)
        picked = selection.select_candidates(jobs, min_score=10, applied_keys=set(), limit=None)
        self.assertEqual(picked, [])

        # With min_score=0, both should be included (score 0 >= 0)
        picked = selection.select_candidates(jobs, min_score=0, applied_keys=set(), limit=None)
        self.assertEqual(len(picked), 2)


if __name__ == "__main__":
    unittest.main()
