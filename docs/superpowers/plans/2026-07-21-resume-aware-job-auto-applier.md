# Résumé-Aware Job Auto-Applier (Phase 1 — Email) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read the latest scraper CSV and produce tailored, résumé-grounded application-email drafts that the user reviews and approves before any send, with idempotent tracking.

**Architecture:** New flat Python modules under `auto-apply/`, each with one responsibility (config, résumé parsing, answers, candidate selection, Gemini tailoring, records, email). `apply.py` orchestrates. Dry-run is the default; `--send` is gated by an `approved` status in a review queue plus an interactive `y/N`. All paths resolve from the repo root via `__file__`, so commands work from any cwd.

**Tech Stack:** Python 3, `google-genai` (Gemini `gemini-2.5-flash`), `pypdf`, stdlib `smtplib`/`email`/`csv`/`argparse`, `python-dotenv` (already present). Tests use stdlib `unittest` (no test-framework dependency added).

## Global Constraints

- **New dependencies allowed:** only `google-genai` and `pypdf`. Do NOT add any other dependency (no PyYAML, no pytest) — tests use stdlib `unittest`.
- **Provider:** Google Gemini via AI Studio API key `GEMINI_API_KEY` from `.env`. Model string exactly `gemini-2.5-flash`.
- **Secrets live only in `.env`** (already gitignored): `GEMINI_API_KEY`, `SMTP_USER`, `SMTP_APP_PASSWORD`. Never hardcode them; never write them to any tracked file.
- **Dry-run is the default.** Nothing is sent without `--send` AND a per-email `y/N` confirmation.
- **Never fabricate.** Every factual claim in a draft must be grounded in the résumé text or `answers.yaml`.
- **Idempotent:** `job_key` = the row's `apply_url`. Never draft or send the same `job_key` twice.
- **Threshold:** `MIN_SCORE = 10` (config default; `--min-score` overrides).
- **Style:** match `config.py` — module docstring, section comments, config-driven.
- **CSV encoding:** read with `utf-8-sig` (input files carry a BOM).
- **Git:** repo is not yet initialized. Task 1 runs `git init` once (no-op if already a repo). Commit steps are checkpoints.

**Run commands (from repo root `c:/ReactNative/job-scraper`):**
- App: `python auto-apply/apply.py --dry-run --limit 2`
- Tests: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`

(Running `apply.py` puts `auto-apply/` on `sys.path[0]`, so flat `import apply_config` works; `-t auto-apply` does the same for tests.)

---

### Task 1: Project scaffold, config module, dependencies

**Files:**
- Create: `auto-apply/apply_config.py`
- Create: `auto-apply/__init__` marker NOT needed (flat modules) — instead create `auto-apply/tests/` dir
- Create: `auto-apply/tests/test_apply_config.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: module `apply_config` exposing constants `REPO_ROOT, AUTO_APPLY_DIR, OUTPUT_DIR, INPUT_CSV, MIN_SCORE, PER_RUN_CAP, SEND_DELAY_SECONDS, MODEL, DRY_RUN_DEFAULT, ME (dict), SMTP_HOST, SMTP_PORT, RESUME_PDF, RESUME_TXT, DRAFTS_DIR, REVIEW_QUEUE, APPLICATIONS_LOG, ANSWERS_FILE` and function `latest_input_csv() -> str | None`.

- [ ] **Step 1: Initialize git and dependency file**

Run:
```bash
cd "c:/ReactNative/job-scraper" && git init
```
Then edit `requirements.txt` to add the two approved deps (keep existing lines):
```
apify-client>=1.7.0
python-dotenv>=1.0.0
google-genai>=1.0.0
pypdf>=4.0.0
```
Install:
```bash
python -m pip install google-genai pypdf
```

- [ ] **Step 2: Write the failing test**

Create `auto-apply/tests/test_apply_config.py`:
```python
import os
import unittest

import apply_config as cfg


class TestApplyConfig(unittest.TestCase):
    def test_core_defaults(self):
        self.assertEqual(cfg.MIN_SCORE, 10)
        self.assertEqual(cfg.MODEL, "gemini-2.5-flash")
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apply_config'`.

- [ ] **Step 4: Write the config module**

Create `auto-apply/apply_config.py`:
```python
"""
Configuration for the résumé-aware job auto-applier (Phase 1 — email).

Everything that decides WHICH jobs are considered and HOW drafts/sends behave
lives here. Secrets never live here — they come from .env (GEMINI_API_KEY,
SMTP_USER, SMTP_APP_PASSWORD). Paths resolve from the repo root via __file__, so
commands work from any working directory.
"""

import glob
import os

# --- Paths -----------------------------------------------------------------
AUTO_APPLY_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(AUTO_APPLY_DIR)
OUTPUT_DIR = os.path.join(REPO_ROOT, "output")

# Explicit input CSV, or None to auto-pick the newest jobs_combined_*.csv.
INPUT_CSV = None

RESUME_PDF = os.path.join(AUTO_APPLY_DIR, "resume", "resume.pdf")
RESUME_TXT = os.path.join(AUTO_APPLY_DIR, "resume", "resume.txt")
DRAFTS_DIR = os.path.join(AUTO_APPLY_DIR, "drafts")
REVIEW_QUEUE = os.path.join(AUTO_APPLY_DIR, "review_queue.csv")
APPLICATIONS_LOG = os.path.join(AUTO_APPLY_DIR, "applications.csv")
ANSWERS_FILE = os.path.join(AUTO_APPLY_DIR, "answers.yaml")

# --- Selection / pacing ----------------------------------------------------
MIN_SCORE = 10            # drop jobs scoring below this (recruiter/Python noise sinks out)
PER_RUN_CAP = 5           # default cap; --limit overrides
SEND_DELAY_SECONDS = 20   # delay between sends on --send

# --- Tailoring -------------------------------------------------------------
MODEL = "gemini-2.5-flash"
DRY_RUN_DEFAULT = True

# --- Applicant contact block (used in the email signature) -----------------
# Fill from résumé during implementation; email is also the SMTP sender.
ME = {
    "name": "Kartik Verma",
    "email": "kartikverma674@gmail.com",
    "phone": "",
    "linkedin": "",
    "github": "",
}

# --- SMTP (Gmail) ----------------------------------------------------------
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# --- Channels --------------------------------------------------------------
CHANNELS = {"email": True}  # Phase-2 channels intentionally absent


def latest_input_csv():
    """Return INPUT_CSV if set, else the newest output/jobs_combined_*.csv (or None)."""
    if INPUT_CSV:
        return INPUT_CSV
    matches = glob.glob(os.path.join(OUTPUT_DIR, "jobs_combined_*.csv"))
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt auto-apply/apply_config.py auto-apply/tests/test_apply_config.py
git commit -m "feat(auto-apply): config module + deps scaffold"
```

---

### Task 2: `answers.yaml` reader (no PyYAML)

**Files:**
- Create: `auto-apply/answers.py`
- Create: `auto-apply/answers.yaml`
- Create: `auto-apply/tests/test_answers.py`

**Interfaces:**
- Produces: `answers.load_answers(path: str) -> dict[str, str]`.

- [ ] **Step 1: Write the failing test**

Create `auto-apply/tests/test_answers.py`:
```python
import os
import tempfile
import unittest

import answers


class TestAnswers(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_parses_key_values(self):
        path = self._write("notice_period: 30 days\nexpected_ctc: 12 LPA\n")
        result = answers.load_answers(path)
        self.assertEqual(result["notice_period"], "30 days")
        self.assertEqual(result["expected_ctc"], "12 LPA")

    def test_ignores_comments_and_blanks(self):
        path = self._write("# a comment\n\nnotice_period: 30 days\n   \n")
        self.assertEqual(answers.load_answers(path), {"notice_period": "30 days"})

    def test_strips_surrounding_quotes(self):
        path = self._write('location: "Delhi/NCR"\nrole: \'SWE\'\n')
        result = answers.load_answers(path)
        self.assertEqual(result["location"], "Delhi/NCR")
        self.assertEqual(result["role"], "SWE")

    def test_value_may_contain_colon(self):
        path = self._write("note: available: immediately\n")
        self.assertEqual(answers.load_answers(path)["note"], "available: immediately")

    def test_missing_file_returns_empty(self):
        self.assertEqual(answers.load_answers("/no/such/file.yaml"), {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'answers'`.

- [ ] **Step 3: Write the reader + sample file**

Create `auto-apply/answers.py`:
```python
"""
Minimal reader for answers.yaml — a flat `key: value` file of facts that are NOT
in the résumé (notice period, expected CTC, ...). Deliberately dependency-free:
we only need flat key/value pairs, so no PyYAML.
"""

import os


def load_answers(path):
    """Parse a flat key: value file into a dict[str, str]. Missing file -> {}."""
    if not os.path.exists(path):
        return {}
    result = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                result[key] = value
    return result
```

Create `auto-apply/answers.yaml`:
```
# Facts not in the résumé. Edit freely — flat key: value only.
notice_period: 30 days
expected_ctc: 12 LPA
current_location: Delhi/NCR
willing_to_relocate: yes
work_authorization: India (citizen)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add auto-apply/answers.py auto-apply/answers.yaml auto-apply/tests/test_answers.py
git commit -m "feat(auto-apply): dependency-free answers.yaml reader"
```

---

### Task 3: Résumé parser with mtime-based caching

**Files:**
- Create: `auto-apply/resume_parser.py`
- Create: `auto-apply/tests/test_resume_parser.py`

**Interfaces:**
- Produces: `resume_parser.extract_text(pdf_path: str) -> str` (pypdf wrapper);
  `resume_parser.load_resume(pdf_path: str, cache_path: str, extractor=extract_text) -> str`
  (returns cached text when cache is at least as new as the PDF, else extracts, writes cache, returns).

- [ ] **Step 1: Write the failing test**

Create `auto-apply/tests/test_resume_parser.py`:
```python
import os
import tempfile
import time
import unittest

import resume_parser


class TestLoadResume(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pdf = os.path.join(self.dir, "resume.pdf")
        self.cache = os.path.join(self.dir, "resume.txt")
        with open(self.pdf, "w", encoding="utf-8") as f:
            f.write("pdf-bytes-placeholder")
        self.calls = []

    def _fake_extractor(self, path):
        self.calls.append(path)
        return "EXTRACTED RESUME TEXT"

    def test_no_cache_extracts_and_writes(self):
        text = resume_parser.load_resume(self.pdf, self.cache, extractor=self._fake_extractor)
        self.assertEqual(text, "EXTRACTED RESUME TEXT")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(os.path.exists(self.cache))

    def test_fresh_cache_is_reused_without_extracting(self):
        with open(self.cache, "w", encoding="utf-8") as f:
            f.write("CACHED TEXT")
        # Make cache newer than pdf.
        future = time.time() + 100
        os.utime(self.cache, (future, future))
        text = resume_parser.load_resume(self.pdf, self.cache, extractor=self._fake_extractor)
        self.assertEqual(text, "CACHED TEXT")
        self.assertEqual(self.calls, [])

    def test_stale_cache_triggers_reparse(self):
        with open(self.cache, "w", encoding="utf-8") as f:
            f.write("OLD")
        # Make pdf newer than cache.
        future = time.time() + 100
        os.utime(self.pdf, (future, future))
        text = resume_parser.load_resume(self.pdf, self.cache, extractor=self._fake_extractor)
        self.assertEqual(text, "EXTRACTED RESUME TEXT")
        self.assertEqual(len(self.calls), 1)

    def test_missing_pdf_raises(self):
        with self.assertRaises(FileNotFoundError):
            resume_parser.load_resume(os.path.join(self.dir, "nope.pdf"),
                                      self.cache, extractor=self._fake_extractor)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'resume_parser'`.

- [ ] **Step 3: Write the parser**

Create `auto-apply/resume_parser.py`:
```python
"""
Résumé PDF -> text, with a plain-text cache next to the PDF. Re-extraction only
happens when the PDF is newer than the cache, so repeat runs are fast.
"""

import os

from pypdf import PdfReader


def extract_text(pdf_path):
    """Extract all text from a PDF using pypdf."""
    reader = PdfReader(pdf_path)
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(parts).strip()


def load_resume(pdf_path, cache_path, extractor=extract_text):
    """Return résumé text, using cache_path when it's at least as new as the PDF."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(
            f"Résumé not found at {pdf_path}. Place your resume as resume.pdf there."
        )
    if os.path.exists(cache_path) and os.path.getmtime(cache_path) >= os.path.getmtime(pdf_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()
    text = extractor(pdf_path)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(text)
    return text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add auto-apply/resume_parser.py auto-apply/tests/test_resume_parser.py
git commit -m "feat(auto-apply): pypdf résumé parser with mtime cache"
```

---

### Task 4: Candidate selection (CSV load, filter, dedupe)

**Files:**
- Create: `auto-apply/selection.py`
- Create: `auto-apply/tests/test_selection.py`

**Interfaces:**
- Produces:
  - `selection.load_jobs(csv_path: str) -> list[dict]`
  - `selection.job_key(row: dict) -> str` (returns `row["apply_url"].strip()`)
  - `selection.load_applied_keys(applications_csv: str) -> set[str]`
  - `selection.select_candidates(rows: list[dict], min_score: int, applied_keys: set[str], limit: int | None) -> list[dict]`
    (keeps rows with non-empty `hr_email` and `int(score) >= min_score` and key not in `applied_keys`; preserves input order — CSV is already score-desc; applies `limit`.)

- [ ] **Step 1: Write the failing test**

Create `auto-apply/tests/test_selection.py`:
```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'selection'`.

- [ ] **Step 3: Write the module**

Create `auto-apply/selection.py`:
```python
"""
Turn the ranked scraper CSV into the list of jobs we'll draft for: rows that have
a recruiter email, clear the score threshold, and haven't been applied to yet.
The job_key is the apply_url (stable + unique per posting).
"""

import csv


def load_jobs(csv_path):
    """Read the jobs CSV (BOM-tolerant) into a list of dict rows."""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def job_key(row):
    """Stable unique key for a posting."""
    return (row.get("apply_url") or "").strip()


def _score(row):
    try:
        return int(float((row.get("score") or "").strip()))
    except ValueError:
        return 0


def load_applied_keys(applications_csv):
    """Return the set of job_keys already recorded in applications.csv (any status)."""
    import os
    if not os.path.exists(applications_csv):
        return set()
    with open(applications_csv, "r", encoding="utf-8", newline="") as f:
        return {(r.get("job_key") or "").strip()
                for r in csv.DictReader(f) if (r.get("job_key") or "").strip()}


def select_candidates(rows, min_score, applied_keys, limit):
    """Filter to emailable, above-threshold, not-yet-applied rows; keep order; apply limit."""
    picked = []
    for row in rows:
        if not (row.get("hr_email") or "").strip():
            continue
        if _score(row) < min_score:
            continue
        if job_key(row) in applied_keys:
            continue
        picked.append(row)
    if limit is not None:
        picked = picked[:limit]
    return picked
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add auto-apply/selection.py auto-apply/tests/test_selection.py
git commit -m "feat(auto-apply): candidate selection with threshold + dedupe"
```

---

### Task 5: Gemini tailoring engine (grounded, structured JSON)

**Files:**
- Create: `auto-apply/tailor.py`
- Create: `auto-apply/tests/test_tailor.py`

**Interfaces:**
- Consumes: résumé text (Task 3), a job row dict (Task 4), answers dict (Task 2).
- Produces:
  - `tailor.build_prompt(resume_text: str, job: dict, answers: dict) -> str`
  - `tailor.get_client(api_key: str)` -> a `genai.Client`
  - `tailor.draft_email(client, model: str, job: dict, resume_text: str, answers: dict) -> dict`
    returning keys `subject`, `body`, `grounding_notes` (all `str`). `client` is any object
    exposing `.models.generate_content(model=..., contents=..., config=...)` returning an
    object with a `.text` attribute holding a JSON string.

- [ ] **Step 1: Write the failing test**

Create `auto-apply/tests/test_tailor.py`:
```python
import json
import unittest

import tailor


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, payload):
        self._payload = payload
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeResponse(json.dumps(self._payload))


class FakeClient:
    def __init__(self, payload):
        self.models = FakeModels(payload)


JOB = {
    "title": "Full Stack Developer", "company": "Jinrai Technologies",
    "location": "India", "remote?": "False",
    "matched_skills": "node, react, typescript, mongodb",
    "experience_required": "1-3 Yrs", "salary": "", "source_site": "naukri",
}


class TestTailor(unittest.TestCase):
    def test_prompt_includes_job_resume_and_answers(self):
        prompt = tailor.build_prompt("RESUME: React Native, Node.js",
                                     JOB, {"notice_period": "30 days"})
        self.assertIn("Jinrai Technologies", prompt)
        self.assertIn("Full Stack Developer", prompt)
        self.assertIn("React Native, Node.js", prompt)
        self.assertIn("30 days", prompt)

    def test_draft_email_returns_structured_fields(self):
        client = FakeClient({
            "subject": "Application: Full Stack Developer",
            "body": "Dear Hiring Team, ...",
            "grounding_notes": "Node.js and React from résumé skills section.",
        })
        result = tailor.draft_email(client, "gemini-2.5-flash", JOB,
                                    "RESUME TEXT", {"notice_period": "30 days"})
        self.assertEqual(result["subject"], "Application: Full Stack Developer")
        self.assertIn("Dear", result["body"])
        self.assertIn("grounding_notes", result)
        # It actually asked the model with our model string + JSON mime type.
        self.assertEqual(client.models.last_kwargs["model"], "gemini-2.5-flash")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tailor'`.

- [ ] **Step 3: Write the module**

Create `auto-apply/tailor.py`:
```python
"""
Draft a tailored, résumé-grounded application email with Gemini. The system
instruction forbids inventing any fact; the model receives ONLY the résumé text,
the single job row, and answers.yaml. Output is structured JSON so parsing is
reliable. One API call per job.
"""

import json

from google import genai
from google.genai import types

SYSTEM_INSTRUCTION = (
    "You write concise, professional job-application emails for a software engineer. "
    "RULES: Every factual claim MUST be supported by the provided résumé text or the "
    "answers block. Never invent employers, job titles, dates, metrics, or skills the "
    "résumé does not state. If the job asks for something absent from the résumé, omit "
    "it — do not claim it. Keep the body ~120-180 words, address the specific role and "
    "company, and end with the candidate's name only (no invented contact details). "
    "Return ONLY a JSON object with keys: subject (string), body (string), "
    "grounding_notes (string listing which résumé/answers facts each claim rests on)."
)

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "grounding_notes": {"type": "string"},
    },
    "required": ["subject", "body", "grounding_notes"],
}


def build_prompt(resume_text, job, answers):
    """Assemble the user-content prompt from résumé, the job row, and answers."""
    answers_block = "\n".join(f"- {k}: {v}" for k, v in answers.items()) or "(none)"
    return (
        "=== JOB ===\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}  Remote: {job.get('remote?', '')}\n"
        f"Experience required: {job.get('experience_required', '')}\n"
        f"Salary: {job.get('salary', '')}\n"
        f"Skills the posting matched: {job.get('matched_skills', '')}\n"
        f"Source: {job.get('source_site', '')}\n\n"
        "=== RÉSUMÉ (the only source of truth about the candidate) ===\n"
        f"{resume_text}\n\n"
        "=== ANSWERS (extra facts not in the résumé; use only if relevant) ===\n"
        f"{answers_block}\n\n"
        "Write the application email now."
    )


def get_client(api_key):
    """Construct a Gemini client from an AI Studio API key."""
    return genai.Client(api_key=api_key)


def draft_email(client, model, job, resume_text, answers):
    """Return {'subject','body','grounding_notes'} for one job via one Gemini call."""
    prompt = build_prompt(resume_text, job, answers)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
            temperature=0.4,
        ),
    )
    data = json.loads(response.text)
    return {
        "subject": data.get("subject", "").strip(),
        "body": data.get("body", "").strip(),
        "grounding_notes": data.get("grounding_notes", "").strip(),
    }
```

Note: the `FakeClient` in the test passes plain `**kwargs` including `config=`; since
the fake ignores `config`, the test does not construct real `types` objects — but the
real `types.GenerateContentConfig(...)` is still evaluated. That import must succeed
(`google-genai` installed in Task 1). If `response_schema` as a dict is rejected by the
installed SDK version at runtime against the real API, drop the `response_schema` line and
rely on `response_mime_type="application/json"` + the JSON instruction in
`SYSTEM_INSTRUCTION` (the parse path is identical).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add auto-apply/tailor.py auto-apply/tests/test_tailor.py
git commit -m "feat(auto-apply): Gemini grounded email tailoring"
```

---

### Task 6: Records — drafts, review queue, applications log

**Files:**
- Create: `auto-apply/records.py`
- Create: `auto-apply/tests/test_records.py`

**Interfaces:**
- Produces:
  - `records.write_draft_md(drafts_dir, job_key, to, subject, body, grounding_notes) -> str` (returns draft path; filename is a filesystem-safe slug of `job_key`).
  - `records.append_review_row(path, row: dict)` where row has keys `job_key, to, subject, draft_path, status`.
  - `records.read_review_rows(path) -> list[dict]`
  - `records.set_review_status(path, job_key, status)`
  - `records.upsert_application(path, job_key, company, title, channel, status)` (idempotent on `job_key`: insert if new, else update `status`+`timestamp`).
- Consumes: `apply_config` for nothing directly (paths passed in by caller).

- [ ] **Step 1: Write the failing test**

Create `auto-apply/tests/test_records.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'records'`.

- [ ] **Step 3: Write the module**

Create `auto-apply/records.py`:
```python
"""
Persistence for drafts and tracking:
- drafts/<slug>.md      : human-readable draft (subject/body/grounding notes)
- review_queue.csv      : job_key,to,subject,draft_path,status (draft->approved->sent)
- applications.csv      : job_key,company,title,channel,status,timestamp (idempotent)
"""

import csv
import os
import re
from datetime import datetime

REVIEW_FIELDS = ["job_key", "to", "subject", "draft_path", "status"]
APP_FIELDS = ["job_key", "company", "title", "channel", "status", "timestamp"]


def _slug(job_key):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", job_key)[-120:] or "job"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def write_draft_md(drafts_dir, job_key, to, subject, body, grounding_notes):
    """Write a readable .md draft and return its path."""
    os.makedirs(drafts_dir, exist_ok=True)
    path = os.path.join(drafts_dir, _slug(job_key) + ".md")
    content = (
        f"# Draft application\n\n"
        f"**To:** {to}\n\n"
        f"**Subject:** {subject}\n\n"
        f"---\n\n{body}\n\n"
        f"---\n\n"
        f"**Grounding notes (reviewer aid — not sent):**\n\n{grounding_notes}\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _read_rows(path, fields):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path, fields, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})


def append_review_row(path, row):
    rows = _read_rows(path, REVIEW_FIELDS)
    if any(r.get("job_key") == row["job_key"] for r in rows):
        return  # idempotent: don't duplicate a queued job
    rows.append(row)
    _write_rows(path, REVIEW_FIELDS, rows)


def read_review_rows(path):
    return _read_rows(path, REVIEW_FIELDS)


def set_review_status(path, job_key, status):
    rows = _read_rows(path, REVIEW_FIELDS)
    for r in rows:
        if r.get("job_key") == job_key:
            r["status"] = status
    _write_rows(path, REVIEW_FIELDS, rows)


def upsert_application(path, job_key, company, title, channel, status):
    """Insert a new application row, or update status+timestamp if job_key exists."""
    rows = _read_rows(path, APP_FIELDS)
    for r in rows:
        if r.get("job_key") == job_key:
            r["status"] = status
            r["timestamp"] = _now()
            _write_rows(path, APP_FIELDS, rows)
            return
    rows.append({"job_key": job_key, "company": company, "title": title,
                 "channel": channel, "status": status, "timestamp": _now()})
    _write_rows(path, APP_FIELDS, rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add auto-apply/records.py auto-apply/tests/test_records.py
git commit -m "feat(auto-apply): draft writer + review queue + applications log"
```

---

### Task 7: Emailer (SMTP message build + send)

**Files:**
- Create: `auto-apply/emailer.py`
- Create: `auto-apply/tests/test_emailer.py`

**Interfaces:**
- Produces:
  - `emailer.clean_password(pw: str) -> str` (removes spaces from a Gmail app password).
  - `emailer.build_message(from_addr, to, subject, body, attachment_path=None) -> email.message.EmailMessage`.
  - `emailer.send_message(msg, host, port, user, password)` (SMTP STARTTLS login + send; no return).

- [ ] **Step 1: Write the failing test**

Create `auto-apply/tests/test_emailer.py`:
```python
import os
import tempfile
import unittest

import emailer


class TestEmailer(unittest.TestCase):
    def test_clean_password_strips_spaces(self):
        self.assertEqual(emailer.clean_password("Agtps nsvh ehta pfef"),
                         "Agtpsnsvhehtapfef")

    def test_build_message_sets_headers(self):
        msg = emailer.build_message("me@gmail.com", "hr@co.com", "Subj", "Body text")
        self.assertEqual(msg["From"], "me@gmail.com")
        self.assertEqual(msg["To"], "hr@co.com")
        self.assertEqual(msg["Subject"], "Subj")
        self.assertIn("Body text", msg.get_content())

    def test_build_message_attaches_file(self):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"%PDF-1.4 fake")
        os.close(fd)
        self.addCleanup(os.remove, path)
        msg = emailer.build_message("me@gmail.com", "hr@co.com", "S", "B",
                                    attachment_path=path)
        attachments = [p for p in msg.iter_attachments()]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'emailer'`.

- [ ] **Step 3: Write the module**

Create `auto-apply/emailer.py`:
```python
"""
Compose and send application emails over Gmail SMTP (STARTTLS). Only used on the
--send path. Credentials are passed in from .env by the caller — never stored here.
"""

import mimetypes
import os
import smtplib
from email.message import EmailMessage


def clean_password(pw):
    """Gmail shows app passwords in 4 space-separated groups; SMTP wants them joined."""
    return (pw or "").replace(" ", "")


def build_message(from_addr, to, subject, body, attachment_path=None):
    """Build an EmailMessage, optionally attaching a file (e.g. resume.pdf)."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment_path:
        ctype, _ = mimetypes.guess_type(attachment_path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(attachment_path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                               filename=os.path.basename(attachment_path))
    return msg


def send_message(msg, host, port, user, password):
    """Send via SMTP STARTTLS. Raises on auth/send failure."""
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, clean_password(password))
        server.send_message(msg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add auto-apply/emailer.py auto-apply/tests/test_emailer.py
git commit -m "feat(auto-apply): SMTP emailer (message build + send)"
```

---

### Task 8: `apply.py` orchestration + CLI (dry-run and --send)

**Files:**
- Create: `auto-apply/apply.py`
- Create: `auto-apply/tests/test_apply.py`

**Interfaces:**
- Consumes: every module above.
- Produces:
  - `apply.run_dry_run(cfg, *, limit, min_score, tailor_fn, resume_text_fn, answers_fn) -> int` (returns number of drafts written). Dependency-injected functions let the test stub Gemini and file IO.
  - `apply.run_send(cfg, *, limit, delay, confirm_fn, send_fn) -> int` (returns number sent).
  - `apply.main(argv=None)` — argparse wiring (`--dry-run` default, `--send`, `--limit`, `--delay`, `--min-score`).
- The injected signatures:
  - `tailor_fn(job) -> {"subject","body","grounding_notes"}`
  - `resume_text_fn() -> str`
  - `answers_fn() -> dict`
  - `confirm_fn(review_row) -> bool`
  - `send_fn(to, subject, body) -> None`

- [ ] **Step 1: Write the failing test**

Create `auto-apply/tests/test_apply.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apply'`.

- [ ] **Step 3: Write the orchestrator**

Create `auto-apply/apply.py`:
```python
"""
Résumé-aware job auto-applier — Phase 1 (email).

Default (dry-run): read the latest ranked CSV, draft tailored emails for jobs with
a recruiter email and score >= MIN_SCORE (skipping already-applied), write .md drafts
+ a review queue, and record them. Sends nothing.

--send: send ONLY review-queue rows you've marked `approved`, with a per-email y/N
confirmation, résumé attached, a delay between sends, and the log updated to `sent`.

Run from the repo root:
    python auto-apply/apply.py --dry-run --limit 2
    python auto-apply/apply.py --send --limit 2
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv

import apply_config as cfg
import answers as answers_mod
import emailer
import records
import resume_parser
import selection
import tailor


# --- dependency-injected core (tested) -------------------------------------

def run_dry_run(cfg, *, limit, min_score, tailor_fn, resume_text_fn, answers_fn):
    """Draft emails for qualifying jobs. Returns the number of drafts written."""
    csv_path = cfg.latest_input_csv()
    if not csv_path:
        print("No input CSV found in output/. Run the scraper first.")
        return 0
    rows = selection.load_jobs(csv_path)
    applied = selection.load_applied_keys(cfg.APPLICATIONS_LOG)
    candidates = selection.select_candidates(rows, min_score, applied, limit)
    if not candidates:
        print("No new emailable jobs at score >= %d (all applied or below threshold)."
              % min_score)
        return 0

    resume_text = resume_text_fn()
    answers = answers_fn()
    count = 0
    for job in candidates:
        key = selection.job_key(job)
        draft = tailor_fn(job)
        path = records.write_draft_md(cfg.DRAFTS_DIR, key, job["hr_email"],
                                      draft["subject"], draft["body"],
                                      draft["grounding_notes"])
        records.append_review_row(cfg.REVIEW_QUEUE, {
            "job_key": key, "to": job["hr_email"], "subject": draft["subject"],
            "draft_path": path, "status": "draft"})
        records.upsert_application(cfg.APPLICATIONS_LOG, key, job["company"],
                                   job["title"], "email", "drafted")
        print(f"  drafted: {job['company']} — {job['title']}  ->  {path}")
        count += 1
    print(f"\n{count} draft(s) written. Review them, set status to 'approved' in "
          f"{os.path.basename(cfg.REVIEW_QUEUE)}, then run with --send.")
    return count


def run_send(cfg, *, limit, delay, confirm_fn, send_fn):
    """Send approved+confirmed drafts. Returns the number sent."""
    rows = records.read_review_rows(cfg.REVIEW_QUEUE)
    approved = [r for r in rows if r.get("status") == "approved"]
    if not approved:
        print("Nothing approved. Set status to 'approved' in the review queue first.")
        return 0
    sent = 0
    for row in approved[:limit] if limit is not None else approved:
        if not confirm_fn(row):
            print(f"  skipped: {row['to']}")
            continue
        # Re-read the body from the draft file (source of truth the user reviewed).
        body = _read_body_from_draft(row.get("draft_path", ""))
        send_fn(row["to"], row["subject"], body)
        records.set_review_status(cfg.REVIEW_QUEUE, row["job_key"], "sent")
        records.upsert_application(cfg.APPLICATIONS_LOG, row["job_key"], "", "",
                                   "email", "sent")
        print(f"  sent: {row['to']}")
        sent += 1
        if delay:
            time.sleep(delay)
    print(f"\n{sent} email(s) sent.")
    return sent


def _read_body_from_draft(draft_path):
    """Extract the body between the two '---' fences of a draft .md (best-effort)."""
    if not draft_path or not os.path.exists(draft_path):
        return ""
    with open(draft_path, encoding="utf-8") as f:
        text = f.read()
    parts = text.split("\n---\n")
    return parts[1].strip() if len(parts) >= 2 else text


# --- real wiring (not unit-tested; exercised manually) ---------------------

def _real_tailor(client, model):
    def _fn(job):
        return tailor.draft_email(client, model, job,
                                  _real_tailor.resume_text, _real_tailor.answers)
    return _fn


def _interactive_confirm(row):
    ans = input(f"Send to {row['to']} — subject '{row['subject']}'? [y/N] ").strip().lower()
    return ans == "y"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Résumé-aware job auto-applier (Phase 1).")
    parser.add_argument("--send", action="store_true",
                        help="Send approved drafts (default is dry-run, sends nothing).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Explicit dry-run (default behaviour).")
    parser.add_argument("--limit", type=int, default=cfg.PER_RUN_CAP)
    parser.add_argument("--delay", type=int, default=cfg.SEND_DELAY_SECONDS)
    parser.add_argument("--min-score", type=int, default=cfg.MIN_SCORE)
    args = parser.parse_args(argv)

    load_dotenv(os.path.join(cfg.REPO_ROOT, ".env"))

    if args.send:
        user = os.environ.get("SMTP_USER")
        password = os.environ.get("SMTP_APP_PASSWORD")
        if not user or not password:
            print("SMTP_USER / SMTP_APP_PASSWORD missing from .env.")
            return 1
        from_addr = cfg.ME.get("email") or user

        def send_fn(to, subject, body):
            msg = emailer.build_message(from_addr, to, subject, body,
                                        attachment_path=cfg.RESUME_PDF)
            emailer.send_message(msg, cfg.SMTP_HOST, cfg.SMTP_PORT, user, password)

        run_send(cfg, limit=args.limit, delay=args.delay,
                 confirm_fn=_interactive_confirm, send_fn=send_fn)
        return 0

    # Dry-run (default).
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY missing from .env.")
        return 1
    client = tailor.get_client(api_key)
    _real_tailor.resume_text = resume_parser.load_resume(cfg.RESUME_PDF, cfg.RESUME_TXT)
    _real_tailor.answers = answers_mod.load_answers(cfg.ANSWERS_FILE)
    run_dry_run(cfg, limit=args.limit, min_score=args.min_score,
                tailor_fn=_real_tailor(client, cfg.MODEL),
                resume_text_fn=lambda: _real_tailor.resume_text,
                answers_fn=lambda: _real_tailor.answers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: PASS (all suites green).

- [ ] **Step 5: Commit**

```bash
git add auto-apply/apply.py auto-apply/tests/test_apply.py
git commit -m "feat(auto-apply): apply.py orchestration + CLI (dry-run/--send)"
```

---

### Task 9: README, .gitignore, and end-to-end dry-run verification

**Files:**
- Create: `auto-apply/README.md`
- Modify: `.gitignore`
- Verify: real dry-run against the live CSV + résumé.

**Interfaces:** none (docs + manual verification).

- [ ] **Step 1: Update .gitignore for generated artifacts**

Append to the existing `.gitignore`:
```
# Auto-apply generated artifacts
auto-apply/drafts/
auto-apply/review_queue.csv
auto-apply/applications.csv
auto-apply/resume/resume.txt
auto-apply/resume/resume.pdf
```

- [ ] **Step 2: Write the README**

Create `auto-apply/README.md`:
```markdown
# Auto-Apply (Phase 1 — Email)

Reads the scraper's ranked output and drafts tailored, résumé-grounded application
emails for jobs that include a recruiter email. Human-in-the-loop: draft → you review
→ you approve → it sends. Dry-run is the default; nothing sends without `--send`.

## Setup

1. Put your résumé at `auto-apply/resume/resume.pdf`.
2. Install deps (from repo root): `pip install google-genai pypdf`
3. In `.env` (repo root, gitignored) set:
   - `GEMINI_API_KEY` — Google AI Studio key
   - `SMTP_USER` — your Gmail address
   - `SMTP_APP_PASSWORD` — 16-char Gmail App Password (needs 2FA)
4. Fill `auto-apply/answers.yaml` (notice period, expected CTC, …) and the `ME` block
   in `auto-apply/apply_config.py` (name, phone, LinkedIn, GitHub).

## Use

Dry-run (writes drafts, sends nothing):
```
python auto-apply/apply.py --dry-run --limit 2
```
This writes `auto-apply/drafts/*.md` and appends `auto-apply/review_queue.csv`.

Review each `.md`, then open `review_queue.csv` and change the `status` of the ones
you want to send from `draft` to `approved`.

Send approved drafts (asks y/N per email, attaches your résumé):
```
python auto-apply/apply.py --send --limit 2
```

## Notes

- `MIN_SCORE` defaults to 10, so low-scoring recruiter/off-stack rows are skipped.
  With the current data only one job (Jinrai) qualifies; lower `--min-score 0` to
  include more for a demo.
- Idempotent: `applications.csv` tracks every job by its `apply_url`; re-runs never
  duplicate drafts or re-send.
- Tests: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
```

- [ ] **Step 3: Full test suite green**

Run: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`
Expected: PASS (all tests across tasks 1–8).

- [ ] **Step 4: Live dry-run smoke test**

Precondition: `auto-apply/resume/resume.pdf` exists and `.env` has `GEMINI_API_KEY`.
Run:
```bash
python auto-apply/apply.py --dry-run --limit 2
```
Expected: prints `drafted: Jinrai Technologies — Full Stack Developer -> …`, creates
one file under `auto-apply/drafts/`, and one `draft` row in `auto-apply/review_queue.csv`.
Open the draft and confirm the body is tailored to Jinrai and contains no fabricated
employers/skills (cross-check against `grounding_notes`). Run the command again and
confirm it reports 0 new drafts (idempotent).

- [ ] **Step 5: Commit**

```bash
git add .gitignore auto-apply/README.md
git commit -m "docs(auto-apply): README + gitignore generated artifacts"
```

---

## Notes for the implementer

- All `auto-apply/*` modules are flat (no package) and imported by bare name; this
  works because `apply.py` and the unittest `-t auto-apply` top-dir both put
  `auto-apply/` on `sys.path`. Do not add `auto-apply/__init__.py`.
- If `response_schema` as a plain dict is rejected by the installed `google-genai`
  version at real call time (Task 5), remove that one kwarg — `response_mime_type` plus
  the JSON instruction in `SYSTEM_INSTRUCTION` already force valid JSON.
- Never mark an application `sent` unless `send_fn` returned without raising.
