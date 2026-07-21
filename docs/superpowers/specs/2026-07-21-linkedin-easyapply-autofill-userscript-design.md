# Phase 2 design — LinkedIn Easy Apply autofill (userscript foundation)

**Date:** 2026-07-21
**Status:** Approved shape, pending spec review
**Supersedes:** the automated ATS-submission scope in `2026-07-21-resume-aware-job-auto-applier-design.md` §4 Phase 2a. See "Why the pivot" below.

## Goal

Help apply to the best-matching **LinkedIn** jobs (the ~86 `source_site == linkedin`
rows that have an `apply_url` but no `hr_email`) with minimum friction, while keeping a
human in control of every submission. A Tampermonkey **userscript** running in the user's
own logged-in Chrome auto-fills the LinkedIn **Easy Apply** modal from a résumé-grounded
answer bank; the user reviews and clicks Submit. Nothing is ever auto-submitted.

This phase builds the **foundation**: the answer bank, the generator that bakes it into a
userscript, the userscript runtime with its fill tiers, a ranked job shortlist, and light
tracking. Additional sites (Naukri, Greenhouse) reuse the same foundation later — a new
generated script against the same bank.

## Why the pivot (from automated ATS submission)

The original Phase 2a plan reverse-engineered Greenhouse/Lever application POST endpoints
and auto-submitted. Dropped because:
- **Low coverage:** only ~3 ATS jobs in the run vs. ~86 LinkedIn + ~155 Naukri.
- **Fragile & risky:** CSRF/multipart/hidden-field reverse-engineering breaks constantly;
  auto-submission risks spam flags and account bans.
- **Accuracy:** LLM answers submitted with no human review is how bad answers reach
  recruiters.

Autofill + human-submit in the user's real browser keeps the valuable part (no repetitive
typing) and deletes the risky part (auto-submission, endpoint reverse-engineering). It is
also indistinguishable from the user typing fast, so it carries no ban surface.

## Non-goals / deferred

- **No auto-submission.** The user always clicks Submit.
- **File upload** — not needed; LinkedIn reuses the résumé already on the profile.
  (Userscripts also cannot set file inputs, by browser security design.)
- **Naukri / Greenhouse / Indeed adapters** — later, same foundation.
- **CSV write-back from the browser** — tracking is LinkedIn's native "Applied" badge plus
  a GM-storage export for now; a localhost logging endpoint is a documented upgrade.
- **`tailor.py` / Gemini email path (Phase 1)** — untouched and separate. This phase's LLM
  fallback uses Groq (see Provider), because the Gemini API key has zero quota (verified
  live 2026-07-21: 429 RESOURCE_EXHAUSTED on `gemini-2.0-flash`).

## Architecture

```
answers.yaml  ─┐
apply_config ME│
résumé (.txt)  ├─▶  build_userscript.py  ─▶  linkedin-easyapply.user.js  (gitignored)
GROQ_API_KEY   │        (bakes bank +           │  BANK, FREE_TEXT templates,
prompt template┘         data + key)            │  RESUME_TEXT, GROQ_KEY/MODEL, matcher+filler
                                                ▼
latest CSV ─▶ linkedin_shortlist.py ─▶ shortlist.html   user opens a job ─▶ Easy Apply modal
                                       (clickable list)                     ─▶ userscript fills
                                                                            ─▶ user reviews+Submit
```

Two independent Python entry points (the generator and the shortlist) plus one generated
browser artifact. Each unit has one job and a clear interface.

### Component: answer bank (data in `answers.yaml`)

Extend the existing flat `answers.yaml` with two new sections. Keep it human-editable.

- **Keyword patterns → answer** for recurring screening questions. Each entry is a set of
  lowercase keywords (all must appear in the question label to match) and the answer value.
  Seed set, grounded in résumé + `ME` + existing answers:
  - `notice` → "30 days"
  - `expected` + `ctc` (or `salary`) → "12 LPA"
  - `years` + `react` → "2"  (only facts present in the résumé)
  - `authoriz` / `work permit` → "India (citizen)"
  - `relocat` → "yes"
  - `current` + `location` → "Delhi/NCR"
- **`free_text:` templates** — canned true-about-you paragraphs with `{company}` / `{title}`
  slots the userscript fills from the page DOM (e.g. a generic "why this role").

Grounding rule: values come only from résumé / `answers.yaml` / `ME`. Nothing is invented.

The exact YAML sub-schema (list-of-entries vs. nested keys) is an implementation detail for
the plan; requirement: flat-ish, human-editable, and parseable by the dependency-free
`answers.py` (extend it if a richer shape is needed).

### Component: `build_userscript.py` (generator)

Reads `answers.yaml`, `ME` (from `apply_config.py`), the résumé text (via
`resume_parser.load_resume`), and `GROQ_API_KEY` from `.env`; writes
`auto-apply/linkedin-easyapply.user.js` from a template with these baked in:
`BANK` (keyword→answer), `FREE_TEXT` templates, `RESUME_TEXT`, `GROQ_KEY`, `GROQ_MODEL`,
and the grounded LLM prompt template. No server, no live fetch, no CORS. Regenerate after
editing `answers.yaml`. All string values are JSON-escaped when embedded.

The generated file contains secrets (Groq key) → **must be gitignored** (see Security).

### Component: `linkedin-easyapply.user.js` (runtime)

`@match https://www.linkedin.com/jobs/*`. Detects the Easy Apply modal (including its
multi-step pages). For each question/field, resolves an answer through **fill tiers**, in
order, stopping at the first hit:

1. **Keyword bank** — label matches a `BANK` entry → fill (text / dropdown / radio /
   checkbox). Instant, free, deterministic. Handles the repetitive majority.
2. **Templated slot-fill** — free-text field matching a `FREE_TEXT` template → fill with
   `{company}`/`{title}` interpolated from the page DOM.
3. **Groq LLM fallback** — free-text with no template match → `GM_xmlhttpRequest` POST to
   Groq (`RESUME_TEXT` + JD text from page + the question, grounded prompt). Prompt
   instructs: use only résumé facts; return the literal token `FLAG` if it cannot be
   answered truthfully. A `FLAG` response is treated as tier 4.
4. **Flag for the user** — anything unresolved (incl. `FLAG`) is highlighted (red border +
   "⚠ answer me" note) and left blank.

Safety invariants: never clicks Submit; never touches file inputs; only writes fields it
has a grounded answer for; everything else is visibly flagged. The user reviews the filled
modal and submits.

### Component: `linkedin_shortlist.py`

Reuses `selection.py` (`load_jobs`, `job_key`, `load_applied_keys`) with a LinkedIn
selector (add `select_linkedin_candidates`, or generalize `select_candidates` with a
predicate) filtering `source_site == "linkedin"`, `score >= MIN_SCORE`, not in
`applications.csv`. Writes `auto-apply/shortlist.html`: a ranked list where each job is a real clickable
`<a href="{apply_url}" target="_blank">` anchor (showing title, company, score, location)
that opens the LinkedIn posting in a new browser tab on click — no copy-paste. Opened via
`open auto-apply/shortlist.html`. This is the "top links clickable" surface — the user
opens jobs top-down.

### Config knobs (add to `apply_config.py`)

- `USERSCRIPT_OUT = os.path.join(AUTO_APPLY_DIR, "linkedin-easyapply.user.js")`
- `SHORTLIST_OUT = os.path.join(AUTO_APPLY_DIR, "shortlist.html")`
- `GROQ_MODEL = "llama-3.3-70b-versatile"` (verified live 2026-07-21)
- Groq endpoint constant (`https://api.groq.com/openai/v1/chat/completions`)
- Reuse existing `MIN_SCORE`, `ME`, `ANSWERS_FILE`, `RESUME_PDF/TXT`, `latest_input_csv()`.

`GROQ_API_KEY` is read from `.env` at generate time (never committed).

### Tracking / idempotency

- **Primary dedup:** LinkedIn natively badges applied jobs "Applied ✓"; the shortlist also
  excludes any `apply_url` already in `applications.csv`.
- The userscript records applied LinkedIn job IDs to Tampermonkey storage (`GM_setValue`)
  with an "export applied" button, so the user can reconcile into `applications.csv`
  (channel `linkedin-assist`) manually.
- `ponytail:` no live browser→CSV write-back — LinkedIn's badge + shortlist exclusion is
  enough for one user on one machine. Upgrade to a localhost logging endpoint only if
  cross-machine dedup matters.

### Security

- The generated `.user.js` embeds the Groq key and résumé text → add
  `auto-apply/linkedin-easyapply.user.js` (and `shortlist.html`) to `.gitignore`.
  `.env` is already gitignored.
- Never log or print the key.

## Testing

- **`unittest`** (stdlib, matches Phase 1) for `build_userscript.py`: bank building from
  `answers.yaml`, correct JSON-escaping of embedded values, résumé/key/model presence in
  output, and that the seed keyword patterns produce the expected `BANK`.
- **`unittest`** for the LinkedIn selector in `selection.py` (filters by source_site,
  score, applied-keys) mirroring the existing `select_candidates` tests.
- **Userscript DOM behavior** (tier resolution, flagging, no-submit) is verified by the
  user on 1–2 real Easy Apply jobs in a dry review — documented in the README.
- Provider reachability already verified live (Groq `llama-3.3-70b-versatile` + `.1-8b`
  returned OK on the free tier, 2026-07-21).

## Acceptance criteria

1. `python auto-apply/build_userscript.py` reads `answers.yaml` + résumé + `.env` key and
   writes a gitignored `linkedin-easyapply.user.js` containing the bank, templates, résumé
   text, and Groq config — with all embedded values correctly escaped.
2. `python auto-apply/linkedin_shortlist.py` reads the latest CSV, filters to LinkedIn jobs
   above `MIN_SCORE` not already applied, and writes a clickable `shortlist.html`.
3. Installed in Tampermonkey, on a real Easy Apply modal the script fills bank/template/LLM
   answers, **highlights un-answerable fields**, and **never submits**; grounded values
   only, no fabrication.
4. Re-running the shortlist never re-lists a job already in `applications.csv`; LinkedIn's
   "Applied" badge covers the rest.
5. New unit tests pass alongside the existing ~39; a short README documents install → open
   from shortlist → review autofill → submit, and how to regenerate after editing answers.

## Provider note

LLM tier uses **Groq** (`llama-3.3-70b-versatile`), OpenAI-compatible REST, called
client-side from the userscript via `GM_xmlhttpRequest` with the `.env` key. Gemini was
dropped for this phase: the personal-account API key (`AQ.` format) returns
429 RESOURCE_EXHAUSTED / zero quota (verified live). `tailor.py`'s Gemini path is unchanged
and out of scope here. Swapping providers later is just an endpoint URL + header change.
