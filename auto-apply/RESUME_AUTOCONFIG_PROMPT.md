# Feature brief: résumé-driven config — auto-derive search & scoring from any résumé (multi-candidate)

Paste this whole file as your first message into a fresh Claude Code window at the repo root
of `job-scraper`. It is self-contained — assume **no prior conversation context**. Before
designing, invoke the `superpowers:brainstorming` skill (this repo builds features via
brainstorm → spec → plan → `superpowers:subagent-driven-development`; two prior features
followed that flow).

---

## 0. One-line goal

Today the scraper is hand-tuned to ONE person (me — a React/Node full-stack dev). I want to
**drop in anyone's résumé and have the tool automatically derive the search + scoring
parameters** that decide which jobs get fetched and how they're ranked — so the same
pipeline works for a different candidate without hand-editing `config.py`.

---

## 1. Environment setup (new machine / fresh clone)

Repo: `github.com/kartikver-674/job-scraper` (private), `main` has everything.

1. Clone; create a branch: `git checkout -b feat/resume-driven-config`.
2. Python 3.13 + venv; `pip install -r requirements.txt` (`apify-client`, `python-dotenv`,
   `google-genai`, `pypdf`).
3. Recreate `.env` at the repo root (gitignored — not in the clone):
   `APIFY_TOKEN`, `GEMINI_API_KEY`, `SMTP_USER`, `SMTP_APP_PASSWORD`.
   ⚠️ Use a **working** Gemini key (`AIza…` AI Studio key with quota) — a prior key had
   zero `generateContent` quota (`429 limit:0`). This feature needs the LLM, so verify the
   key works first.
4. `output/` (scraper results) is gitignored; you don't need a CSV to build this feature,
   but you will to test the end-to-end scrape afterward.
5. Sanity-check the existing suite: `python -m unittest discover -s auto-apply/tests -t auto-apply -v`.

---

## 2. What exists today (reuse; don't rebuild)

- **`config.py`** — the single, heavily-commented source of truth the scraper reads. It is
  currently hard-tuned to me. The parts this feature must be able to (re)generate:
  - `SEARCH`: `experience_years`, `salary_min`, `role_keywords` (list of search terms),
    `locations` (list), `country`.
  - `SCORING`: `skill_weights` (dict skill→weight, e.g. core stack = 5, strong = 3,
    supporting = 2, minor = 1), `frontend_terms`, `backend_terms`, `fullstack_bonus`,
    `fullstack_title_terms`, `penalty_terms` (stacks to down-rank), `drop_terms` (seniority
    words that remove a job), `drop_penalty`.
  - `SETTINGS`: `max_experience_years`, `min_score`, `min_ctc_lpa`, `max_age_days`.
  - It also has `GREENHOUSE_COMPANIES` / `LEVER_COMPANIES` / geo-id and city-id maps —
    those are infrastructure, NOT candidate-specific; leave them alone.
- **`scraper.py`** — reads `config.py`. Don't change its logic unless the config-loading
  shape changes (see the architecture decision below).
- **`auto-apply/resume_parser.py`** — `load_resume(pdf_path, cache_path)` → résumé text via
  pypdf, mtime-cached. **Reuse for parsing the input résumé.**
- **`auto-apply/tailor.py`** — shows the Gemini pattern: `get_client(api_key)` +
  `client.models.generate_content(..., config=GenerateContentConfig(system_instruction=...,
  response_mime_type="application/json", response_schema=...))`. **Reuse this to extract a
  structured profile from the résumé.** Model is `apply_config.MODEL` (`gemini-2.0-flash`).
- **`auto-apply/answers.py`** — flat `key: value` reader (dependency-free). Good model for a
  per-candidate **preferences** file (see below).

Design docs for reference:
`docs/superpowers/specs/2026-07-21-resume-aware-job-auto-applier-design.md` and the plans in
`docs/superpowers/plans/`.

---

## 3. The core problem: résumé gives skills, but NOT everything

A résumé reliably yields **skills, stack, years of experience, and role titles** — these can
be auto-derived and are grounded (never invent a skill not in the résumé). But several config
knobs are **preferences, not résumé facts**, and must NOT be fabricated:

- `locations` — where the candidate wants to work (may hint from résumé address, but confirm).
- `salary_min` / `min_ctc_lpa` — expected compensation.
- `penalty_terms` / stacks-to-avoid — "not interested in X" (e.g. I avoid Salesforce/CRM).
- Seniority target / `max_experience_years` (a 2-yr dev targeting 0–3 vs a 6-yr targeting 4–8).

So the design should split into: **(a) auto-derived from the résumé** (skills→weights,
role_keywords, experience_years, fullstack terms) and **(b) preferences** supplied by a small
per-candidate file (analogous to `answers.yaml`) or an interactive prompt — never guessed.

---

## 4. Suggested design (refine in brainstorming — get my decisions first)

### 4.1 Résumé → structured profile (LLM extraction, grounded)
Parse the résumé text, then use Gemini structured JSON to extract a **profile** object, e.g.:
```
{
  candidate_name, years_experience,
  skills: [{name, tier: "core"|"strong"|"supporting"|"minor"}],   # grounded in résumé only
  target_role_titles: [...],          # derived from titles/summary
  primary_domain: "full-stack" | "frontend" | "backend" | ...,
  seniority_target: "entry" | "mid" | ...,
}
```
System instruction MUST forbid inventing skills/titles not supported by the résumé text.

### 4.2 Profile → config parameters (deterministic mapping)
Map the profile to the `config.py` shapes with a clear, testable function (not the LLM):
- `skills[].tier` → `skill_weights` (core=5, strong=3, supporting=2, minor=1).
- `target_role_titles` → `role_keywords`.
- `years_experience` → `SEARCH.experience_years`; `seniority_target` → `max_experience_years`
  and the `drop_terms` seniority list (e.g. an entry/mid candidate drops "senior/lead/…").
- `primary_domain` + skills → `frontend_terms` / `backend_terms` / `fullstack_title_terms`.
- Preferences file → `locations`, `salary_min`, `min_ctc_lpa`, extra `penalty_terms`
  (stacks to avoid).

### 4.3 KEY ARCHITECTURE DECISION (ask me)
How should the derived config be stored/consumed? Options — recommend **B**:
- **A. Overwrite `config.py`** in place. Simple, but destroys my hand-tuned defaults and is
  single-candidate.
- **B. Introduce a `profiles/` directory** (one data file per candidate, e.g.
  `profiles/<name>.yaml` or `.py`) + a small loader so `config.py` selects the active profile
  (env var / CLI flag / `ACTIVE_PROFILE`). Keeps my current setup as the default profile,
  makes it genuinely multi-candidate, and the scraper + auto-apply both read the active
  profile. **Recommended.**
- **C. Generate a candidate config and require manual copy.** Least magic, most friction.

Also decide: does a "candidate" bundle the **résumé PDF + derived profile + preferences**
together (so `auto-apply` tailoring uses the same candidate's résumé)? Ideally yes — one
`profiles/<name>/` folder holding `resume.pdf`, `profile.yaml` (derived), `preferences.yaml`.

### 4.4 Human-in-the-loop (constraint)
- Default run is a **dry preview**: show the proposed parameters (ideally a diff vs the
  current/active profile — skills+weights, role_keywords, experience, penalties), and any
  **preference fields it could NOT derive** (flagged for me to fill). Nothing is written
  until I approve.
- On approval (explicit flag), write the profile file(s). Never fabricate a skill or a
  preference; flag gaps instead of guessing.

---

## 5. Hard constraints

- **Never fabricate.** Skills/roles must be grounded in the résumé; preferences come from a
  file or a prompt, never invented.
- **Human-in-the-loop + preview-by-default.** Show proposed config, get approval, then write.
- **Don't break the existing setup.** My current `config.py` behavior must remain available
  (as the default profile, or unchanged if you go with option B/C).
- **Provider:** Google Gemini via `google-genai` (`gemini-2.0-flash`), not Anthropic.
- **Ask before adding ANY dependency.** Expect to reuse `pypdf` + `google-genai` only; no
  PyYAML was added before (there's a dependency-free flat-file reader in `auto-apply/answers.py`
  to copy if you need one). Tests use stdlib `unittest`.
- **Config-driven + heavily commented**, matching `config.py`'s existing style.

## 6. Acceptance criteria

1. `python <new_entry>.py --resume path/to/resume.pdf` (name TBD) parses the résumé, derives
   a profile, and **prints the proposed SEARCH + SCORING parameters** (skills with weights,
   role_keywords, experience, penalties, fullstack terms) plus a list of preference fields it
   couldn't derive — **writing nothing** by default.
2. No fabricated skills/roles; preference-only fields (locations, expected CTC, avoid-list)
   are prompted/flagged, not invented.
3. With an explicit write/approve flag, it persists the profile so the scraper (and
   auto-apply) target that candidate; the existing candidate still works.
4. Deterministic profile→config mapping is unit-tested (stdlib `unittest`); LLM extraction is
   tested with a stubbed client (no real API call in tests), mirroring `auto-apply/tests/test_tailor.py`.
5. Short README section explaining: add résumé → run preview → fill any flagged preferences →
   approve → scrape for that candidate.

## 7. Start by

Setting up the env (§1) and confirming the Gemini key works, reading `config.py` end-to-end
(so you know every parameter you must be able to generate) and the reusable `auto-apply/`
modules (`resume_parser.py`, `tailor.py`, `answers.py`), then brainstorming with me — lead
with the §4.3 architecture decision (recommend the `profiles/` approach) and the
derived-vs-preference split — **before** writing code or adding any dependency.
