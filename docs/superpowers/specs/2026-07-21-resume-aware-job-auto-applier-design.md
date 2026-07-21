# Design: résumé-aware job auto-applier (Phase 1 — email)

**Date:** 2026-07-21
**Status:** approved for implementation
**Scope:** Phase 1 only (email applications). Phase 2 (apply-link assist) is explicitly out of scope for this pass.

---

## 1. Goal

Read the ranked scraper output (`output/jobs_combined_*.csv`), and for jobs that
include a recruiter email, produce **tailored, résumé-grounded application email
drafts** that the user reviews and approves before anything is sent. Human-in-the-loop
throughout; dry-run is the default; nothing leaves the machine without an explicit
`--send` flag and per-email confirmation.

Non-goals for Phase 1: browser automation, LinkedIn/Naukri/Indeed form filling,
screening-question answering beyond what a cover email needs, auto-submission of anything.

## 2. Grounding facts (verified 2026-07-21)

- Latest input: `output/jobs_combined_2026-07-21_1603.csv`, 244 rows, columns:
  `score, matched_skills, is_fullstack, title, company, location, remote?,
  experience_required, salary, hr_email, hr_phone, source_site, apply_url, date_posted`.
- Only **5 rows have `hr_email`** (all `naukri`). At `score ≥ 10` exactly **one**
  qualifies today — Jinrai Technologies (score 28, "Full Stack Developer",
  `career@jinraitech.com`). Elastiq (score 7, a Python role) and three Orcapod
  recruiter listings (score −5) fall below the threshold.
- Consequence: `apply.py --dry-run --limit 2` produces **1 draft** now, not 2. This is
  expected given the data. Lowering `MIN_SCORE` to `0` for a run includes Elastiq for a
  2-draft demo. The pipeline is correct regardless of count.
- Existing deps: `apify-client`, `python-dotenv` only. `.env` verified to contain
  working `GEMINI_API_KEY` (Google AI Studio, `gemini-2.5-flash` reachable, HTTP 200),
  `SMTP_USER` (Gmail), `SMTP_APP_PASSWORD` (16-char app password, stored with spaces),
  plus the existing `APIFY_TOKEN`. `.env` is gitignored.

## 3. Decisions (confirmed with user)

| Decision | Choice |
|----------|--------|
| Tailoring engine | **Google Gemini** (`gemini-2.5-flash`) via the `google-genai` SDK, AI Studio API-key path. (User's Anthropic account is enterprise-restricted; Gemini key is available and verified.) |
| Email sending | **Gmail SMTP** (`smtp.gmail.com:587`, STARTTLS) with `SMTP_USER` + `SMTP_APP_PASSWORD` from `.env`. |
| PDF parsing | **pypdf** (pure-Python, light). |
| Score threshold | `MIN_SCORE = 10` (config-overridable). |
| `answers.yaml` format | Flat `key: value` file parsed by a tiny built-in reader — **no PyYAML dependency**. |
| New dependencies (approved) | `google-genai`, `pypdf`. |

## 4. Architecture

All new code lives under `auto-apply/`, matching the existing repo's config-driven,
heavily-commented style (see `config.py`).

```
auto-apply/
  apply.py            # CLI entry point + orchestration
  apply_config.py     # all knobs (paths, threshold, cap, delay, ME{}, model, toggles)
  resume_parser.py    # pypdf → text, cached to resume/resume.txt
  tailor.py           # Gemini draft generator (grounded, structured output)
  emailer.py          # SMTP send (only exercised by --send)
  answers.yaml        # facts not in résumé (notice period, expected CTC, ...)
  drafts/             # one readable .md per job (subject + body + grounding notes)
  review_queue.csv    # job_key, to, subject, draft_path, status
  applications.csv    # job_key, company, title, channel, status, timestamp
  README.md           # setup + run instructions
```

### 4.1 `apply_config.py`
- `INPUT_CSV = None` → auto-pick newest `output/jobs_combined_*.csv`; or an explicit path.
- `MIN_SCORE = 10`, `PER_RUN_CAP = 5` (CLI `--limit` overrides), `SEND_DELAY_SECONDS = 20`.
- `ME = {name, email, phone, linkedin, github}` — the applicant's contact block used in
  the signature (email/phone may read from `.env` if preferred).
- `MODEL = "gemini-2.5-flash"`, `DRY_RUN_DEFAULT = True`.
- `SMTP = {host: "smtp.gmail.com", port: 587}`; credentials come from `.env`, never here.
- `CHANNELS = {"email": True}` (Phase 2 channels off).
- `RESUME_PDF`, `RESUME_TXT`, `DRAFTS_DIR`, `REVIEW_QUEUE`, `APPLICATIONS_LOG` paths.

### 4.2 `resume_parser.py`
- Extract text from `resume/resume.pdf` with pypdf; write cache to `resume/resume.txt`.
- Re-parse only when the PDF's mtime is newer than the cache (else load cache).
- Raise a clear, actionable error if `resume.pdf` is missing.

### 4.3 `answers.yaml` + reader
- Flat file, e.g.:
  ```
  notice_period: 30 days
  expected_ctc: 12 LPA
  current_location: Delhi/NCR
  willing_to_relocate: yes
  ```
- Reader: split each non-blank, non-`#` line on the first `:`, strip whitespace and
  surrounding quotes. Returns a `dict[str, str]`. ~10 lines, no dependency.
- Values are passed to the tailor as *available facts only* — never required, never invented.

### 4.4 `tailor.py`
- `genai.Client(api_key=<GEMINI_API_KEY>)`.
- `client.models.generate_content(model=MODEL, contents=<prompt>, config=...)` with:
  - **System instruction (grounding guarantee):** draft a concise, professional
    application email; every factual claim must be supported by the provided résumé text
    or `answers.yaml`; do not invent employers, dates, titles, metrics, or skills; if the
    job asks for something absent from the résumé, omit it (do not claim it); keep it
    tight (roughly 120–180 words) and address the specific role/company.
  - **Structured JSON output** via `response_mime_type="application/json"` +
    `response_schema` → `{ "subject": str, "body": str, "grounding_notes": str }`.
    `grounding_notes` lists which résumé/answers facts each claim rests on (reviewer aid).
  - Inputs in `contents`: résumé text, the single job row (title, company, location,
    remote?, matched_skills, experience_required, salary, source_site), and `answers.yaml`.
- One API call per job. Returns the parsed dict. On API error, skip that job with a
  logged warning (don't abort the whole run).
- Optional (noted, not required for MVP): context-cache the résumé across a run.

### 4.5 `emailer.py`
- `send(to, subject, body, attachments)` over `smtplib.SMTP(host, port)` + `starttls()`
  + `login(SMTP_USER, SMTP_APP_PASSWORD_without_spaces)`.
- Attaches `resume/resume.pdf`. From = `SMTP_USER` (or `ME["email"]`).
- Only imported/called on the `--send` path.

### 4.6 `apply.py` (CLI + orchestration)
Flags: `--dry-run` (default true), `--send`, `--limit N`, `--delay S`, `--min-score X`.

**Dry-run (default):**
1. Load config + `.env`; validate `GEMINI_API_KEY` present.
2. Parse/refresh résumé text; load `answers.yaml`.
3. Load newest CSV → keep rows where `hr_email` non-empty **and** `score ≥ MIN_SCORE`
   → drop rows whose `job_key` is already in `applications.csv`.
4. Sort by score desc (already sorted), take `--limit`.
5. For each: call `tailor.draft(...)` → write `drafts/<job_key>.md` (human-readable:
   To / Subject / Body / Grounding notes) → append `review_queue.csv` row
   (`status=draft`) → upsert `applications.csv` (`status=drafted`, timestamp).
6. Print a summary table. **Send nothing.**

**`--send`:**
1. Read `review_queue.csv`; select rows with `status == approved` (user promotes
   `draft → approved` by editing the CSV).
2. For each (respecting `--limit`): show the draft, ask an interactive `y/N`; on `y`,
   send via `emailer` with `resume.pdf` attached; sleep `--delay` between sends.
3. Flip `review_queue` + `applications.csv` rows to `sent` (timestamp). Skipped/declined
   stay `approved`/`draft`.

### 4.7 Idempotency & tracking
- `job_key` = the `apply_url` (stable, unique per posting).
- `applications.csv` columns: `job_key, company, title, channel, status, timestamp`
  where `status ∈ {drafted, approved, sent, skipped}`.
- Re-running dry-run never re-drafts a job already logged; `--send` never re-sends a
  `sent` job. Both operations are safe to repeat.

## 5. Error handling
- Missing `resume.pdf` → clear message, exit non-zero before any API calls.
- Missing `GEMINI_API_KEY` → clear message, exit.
- Gemini call failure on a job → warn, skip that job, continue others.
- SMTP auth/send failure → report which job failed, leave its status unchanged (not
  marked `sent`), continue to next.
- Empty candidate set (e.g. all already applied) → informative message, exit 0.

## 6. Testing strategy
- `resume_parser`: extraction returns non-empty text for a sample PDF; cache
  freshness logic (mtime) picks cache vs re-parse correctly.
- `answers.yaml` reader: parses key/value, ignores comments/blanks, strips quotes.
- CSV filtering: threshold + `hr_email` presence + dedupe against `applications.csv`
  yields the expected candidate set (unit test with a fixture CSV incl. the recruiter/
  Python rows to prove they're excluded at `score ≥ 10`).
- `tailor` grounding: with a stubbed/fake client, assert the prompt includes résumé +
  job + answers and that output parses to `{subject, body, grounding_notes}`.
- Idempotency: running the dry-run twice does not add duplicate `applications.csv` rows.
- `emailer`: unit-test message assembly (headers, attachment) against a mock SMTP; no
  live send in tests.

## 7. Deliverables / acceptance criteria
1. `python apply.py --dry-run --limit 2` reads the latest CSV, parses the résumé, and
   writes tailored `.md` draft(s) + `review_queue.csv`; sends nothing. (Produces 1 draft
   today given the data; note stated in-tool.)
2. Drafts are visibly tailored per role and contain no fabricated claims (verifiable via
   `grounding_notes`).
3. `applications.csv` tracks state; re-running never duplicates.
4. `--send` sends only after approval + `y/N`, respects cap + delay, updates the log.
5. Config-driven and commented to match the repo; `README.md` explains résumé placement,
   `.env` keys, and the dry-run → approve → send flow.

## 8. Setup notes for README
- Put résumé at `auto-apply/resume/resume.pdf`.
- `.env`: `GEMINI_API_KEY`, `SMTP_USER`, `SMTP_APP_PASSWORD` (Gmail app password; 2FA
  required to mint one).
- `pip install google-genai pypdf` (added to `requirements.txt`).
- Run dry-run first; edit `review_queue.csv` to `approved`; then `--send`.
