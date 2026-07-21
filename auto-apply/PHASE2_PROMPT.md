# Phase 2 brief: apply-link assist (browser + ATS form assist)

Paste this whole file as your first message into a fresh Claude Code window opened at the
repo root of `job-scraper` on the new machine. It is self-contained — assume you have **no
prior conversation context**. Everything you need is below. Before designing, invoke the
`superpowers:brainstorming` skill (this repo builds features via brainstorm → spec → plan →
subagent-driven execution; Phase 1 followed that flow).

---

## 0. FIRST: environment setup on this (new) machine

The repo is on GitHub: `github.com/kartikver-674/job-scraper` (private), branch `main` has
everything. Some things are **gitignored and will NOT be in the clone** — recreate them:

1. **Clone + branch:** clone the repo, then create a Phase 2 branch off `main`:
   `git checkout -b feat/auto-apply-phase2`
2. **Python:** Phase 1 was built on Python 3.13. Create/activate a venv.
3. **Install deps:** `pip install -r requirements.txt` (installs `apify-client`,
   `python-dotenv`, `google-genai`, `pypdf`).
4. **Recreate `.env`** at the repo root (it is gitignored — not in the clone). Keys used:
   ```
   APIFY_TOKEN=<apify token, for the scraper>
   GEMINI_API_KEY=<Google AI Studio key — SEE BLOCKER BELOW>
   SMTP_USER=<your gmail address>
   SMTP_APP_PASSWORD=<16-char gmail app password>
   ```
5. **Input CSV:** the scraper's output lives in `output/` which is **gitignored**, so the
   ranked CSV will NOT be in the clone. Either copy the latest
   `output/jobs_combined_*.csv` from the old machine into `output/`, **or** re-run the
   scraper (`python scraper.py` — costs Apify credits; see `config.py`).
6. **Résumé:** `auto-apply/resume/resume.pdf` *was* committed, so it should be present
   after clone — verify it's there. If not, drop your résumé PDF at that path.
7. **Sanity check:** run the Phase 1 test suite from the repo root and confirm green:
   `python -m unittest discover -s auto-apply/tests -t auto-apply -v` (expect ~39 tests OK).

### ⛔ KNOWN BLOCKER — Gemini quota (carried over from Phase 1)
The Gemini key used in Phase 1 (an unusual `AQ.…`-format key) had **zero
`generateContent` quota** — every draft call failed with `429 RESOURCE_EXHAUSTED,
limit: 0`; `gemini-2.5-flash` also 404s ("not available to new users"). Phase 1 code is
correct and fully unit-tested, but **no live draft was ever produced**. Before Phase 2
work that needs the LLM, get a **standard AI Studio key** (`AIza…`, whose free tier
includes flash quota) or enable billing, put it in `.env`, and verify with:
`python auto-apply/apply.py --dry-run --limit 2` — it should draft the Jinrai email.
The model is set in `auto-apply/apply_config.py` → `MODEL` (currently `gemini-2.0-flash`).

---

## 1. What already exists (do NOT rebuild it)

**Phase 0 — scraper** (`scraper.py`, `config.py`): pulls full-stack jobs from Naukri,
LinkedIn, Indeed, and company ATS boards (Greenhouse/Lever), scores them against the
résumé, writes a ranked `output/jobs_combined_*.csv`.

**Phase 1 — email drafter** (`auto-apply/`, merged to `main`, 39 tests): for jobs that have
a recruiter `hr_email`, drafts tailored, résumé-grounded application emails with Gemini;
human-in-the-loop (dry-run default; `--send` gated by an `approved` status in a review
queue **and** a per-email y/N with body preview); idempotent; never fabricates. Reuse its
modules — do not duplicate them:

| File | Responsibility (reuse in Phase 2) |
|---|---|
| `auto-apply/apply_config.py` | All config/knobs; paths resolve from repo root via `__file__`; `ME{}` contact block; `MIN_SCORE=10`; `MODEL`; `latest_input_csv()`. **Add Phase 2 knobs here.** |
| `auto-apply/resume_parser.py` | `load_resume(pdf, cache)` → résumé text, mtime-cached to `resume/resume.txt`. |
| `auto-apply/answers.py` | `load_answers(path)` → dict from flat `answers.yaml` (notice period, expected CTC, etc.). Dependency-free. |
| `auto-apply/selection.py` | `load_jobs`, `job_key` (= `apply_url`), `load_applied_keys`, `select_candidates`. **Phase 2 needs a new selector for the no-email jobs — extend here.** |
| `auto-apply/tailor.py` | `build_prompt`, `get_client`, `draft_email(client, model, job, resume_text, answers)` → grounded `{subject, body, grounding_notes}` via Gemini structured JSON. **Reuse for screening-question answers.** |
| `auto-apply/records.py` | `write_draft_md`, `append_review_row`, `read_review_rows`, `set_review_status`, `upsert_application` (idempotent on `job_key`). **Reuse the review-queue + applications.csv tracking.** |
| `auto-apply/emailer.py` | SMTP send (Phase 1 only). |
| `auto-apply/apply.py` | CLI + orchestration: `run_dry_run`, `run_send`, `main` (argparse: `--dry-run` default, `--send`, `--limit`, `--delay`, `--min-score`). **Phase 2 likely adds a new subcommand/entry (e.g. `--assist`) or a sibling `apply_links.py`.** |

**Tracking files (gitignored, local; start fresh on this machine):**
`auto-apply/applications.csv` (`job_key, company, title, channel, status, timestamp`),
`auto-apply/review_queue.csv`, `auto-apply/drafts/`.

**Design docs (in the clone):** spec at
`docs/superpowers/specs/2026-07-21-resume-aware-job-auto-applier-design.md`, Phase 1 plan
at `docs/superpowers/plans/2026-07-21-resume-aware-job-auto-applier.md`. Read the spec's
**§4 Phase 2** section — Phase 2 was scoped there and deferred.

---

## 2. The input data (know this before designing)

Latest CSV columns: `score, matched_skills, is_fullstack, title, company, location,
remote?, experience_required, salary, hr_email, hr_phone, source_site, apply_url,
date_posted` (sorted by `score` desc). In the Phase 1 run of 244 jobs, only **5 had
`hr_email`** (handled in Phase 1). **The other ~239 jobs are Phase 2's target** — they have
an `apply_url` but no email. Rough source split from that run:

- `naukri` ≈ 155, `linkedin` ≈ 86, `greenhouse:<token>` ≈ 3 (Lever configured in
  `config.py` but returned none that run).
- `source_site` for ATS jobs is formatted `greenhouse:<board_token>` / `lever:<token>` —
  parse it to hit the ATS. `config.py` has `GREENHOUSE_COMPANIES` / `LEVER_COMPANIES`.

My profile (for tailoring/screening answers): Full-Stack SWE, ~2 yrs, Delhi/NCR + remote;
React Native, React, TypeScript, Node.js, Express, MongoDB, Redis, Socket.IO, JWT/OAuth.
Targeting entry-to-mid full-stack (0–3 yrs). **Not** interested in Salesforce/CRM or senior
roles. Never claim skills/experience not in the résumé.

---

## 3. Phase 2 goal & scope (from the spec, to refine in brainstorming)

Help me **apply to the best-matching jobs that have only an `apply_url`** (no email),
keeping me in control of every submission. Two tiers, safest first:

### Phase 2a — ATS structured forms (Greenhouse / Lever) — do this first
Greenhouse and Lever expose structured application flows and are the least ToS-risky.
Investigate their public application endpoints/forms:
- Greenhouse: `boards.greenhouse.io/<token>` and its job API; there is a documented
  application POST for some boards (fields: name, email, résumé upload, custom questions).
- Lever: `jobs.lever.co/<token>` postings + `/apply`.
Draft answers to their questions grounded in the résumé + `answers.yaml`, produce a
**review artifact** (like Phase 1's draft `.md` + review queue), and only submit after I
approve. When a required question can't be answered truthfully from the résumé/answers,
**flag it for me — never guess.**

### Phase 2b — browser assist for job boards (LinkedIn / Naukri / Indeed) — stretch, opt-in
Use **Playwright** ONLY to *open* the apply page and *pre-fill* fields, then hand control
to me to review and click submit myself. **Do NOT auto-submit** these — that violates their
ToS and risks account bans. Persist my login session locally (storage-state) so I don't
re-auth each run. Handle common field patterns; when a form asks something not answerable
truthfully from my résumé, pause and ask me.

### Tailoring / screening answers
Reuse `tailor.py` + `answers.yaml` to answer common screening questions ("years of React?",
"notice period?", "expected CTC?") from résumé facts + `answers.yaml`. **Never invent
values.**

---

## 4. Hard constraints (unchanged from Phase 1 — read before designing)

- **NOT a fire-and-forget bot.** Human-in-the-loop: draft/pre-fill → I review → I approve →
  it submits (and for LinkedIn/Naukri/Indeed, **I** click submit, not the tool).
- **Dry-run is the default.** Nothing is submitted/sent without an explicit flag
  (e.g. `--submit`) AND per-item confirmation.
- **Never fabricate.** Every field/answer must be grounded in the résumé or `answers.yaml`;
  flag anything that can't be answered truthfully.
- **Idempotent + tracked.** Extend `applications.csv` (channel e.g. `greenhouse`, `lever`,
  `linkedin-assist`); never apply to the same `job_key` (= `apply_url`) twice.
- **Rate-limit & cap.** Per-run cap (`--limit`) and a delay between actions.
- **Ask before adding ANY dependency.** Playwright (and its browser binaries) is a new,
  heavy dependency — confirm with me before installing. Tests use stdlib `unittest` (no
  pytest was added in Phase 1).
- **Provider:** tailoring uses Google Gemini via `google-genai` (`gemini-2.0-flash`), not
  Anthropic (my Anthropic account is enterprise-restricted).

---

## 5. Suggested approach (prove it cheaply first)

1. **Brainstorm + confirm** with me: which tier first (recommend 2a — ATS), whether to add
   Playwright now or defer, and the submission-safety model. Get decisions before coding.
2. **Bucket the CSV** by `source_site` so we know how many jobs each path covers (parse
   `greenhouse:`/`lever:` prefixes; group `linkedin`/`naukri`/`indeed`).
3. **Phase 2a**: investigate one real Greenhouse and one Lever posting's application flow
   (read-only first), design a grounded-answer + review-and-submit path reusing
   `records.py`/`tailor.py`, prove it on 1 job in dry-run.
4. **Then stop and show me** before Phase 2b (browser automation).
5. Write the design to `docs/superpowers/specs/`, then a plan to `docs/superpowers/plans/`,
   then execute with `superpowers:subagent-driven-development` (fresh subagent per task,
   review after each, TDD, frequent commits) — same flow as Phase 1.

## 6. Acceptance criteria (Phase 2a first)

1. A dry-run reads the latest CSV, filters to ATS (`greenhouse:`/`lever:`) jobs above the
   score threshold and not already in `applications.csv`, drafts grounded answers, and
   writes a review artifact — **submitting nothing**.
2. Answers are clearly grounded (no fabricated values); un-answerable required fields are
   flagged for me, not guessed.
3. With an explicit submit flag + my per-item approval, it submits to the ATS and updates
   `applications.csv`; re-runs never duplicate.
4. Config-driven and commented to match the repo; a short README explains setup and the
   dry-run → approve → submit flow. Ask me before adding Playwright or any dependency.

## 7. Start by

Setting up the environment (§0), confirming the Gemini key works (or noting it's still
blocked), reading spec §4 and the existing `auto-apply/` modules, bucketing the latest CSV
by source, then brainstorming the Phase-2a plan with me **before** writing code or adding
Playwright.
