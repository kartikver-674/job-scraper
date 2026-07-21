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

## Phase 2 — LinkedIn Easy Apply autofill (userscript)

Auto-fills the LinkedIn Easy Apply modal from a résumé-grounded answer bank.
**It never submits — you review and click Submit.** No new Python deps.

### Setup (once)
1. Ensure `GROQ_API_KEY=gsk_...` is in the repo-root `.env` (free key from
   https://console.groq.com — powers the LLM fallback for novel free-text
   questions). Without it, tiers 1–2 still work and unknowns are flagged.
2. Install the **Tampermonkey** browser extension.
3. Generate the userscript:
   ```
   python auto-apply/build_userscript.py
   ```
   This writes `auto-apply/linkedin-easyapply.user.js` (gitignored — it embeds
   your key + résumé). Open that file in Tampermonkey (dashboard → Utilities →
   Import, or drag it in) to install.

### Each session
1. Build the shortlist of top LinkedIn jobs not yet applied to:
   ```
   python auto-apply/linkedin_shortlist.py
   open auto-apply/shortlist.html
   ```
2. Click a job → LinkedIn opens → click **Easy Apply**.
3. Click the floating **⚡ Autofill** button. Fields fill in four tiers:
   keyword bank → templated free-text → Groq LLM → **⚠ flagged** (red outline)
   for anything not truthfully answerable.
4. **Review every field**, fix any flagged ones, then click **Submit yourself**.

### After editing answers
Edit `answers.yaml` (facts) or `build_userscript.py` (`BANK_PATTERNS` /
`FREE_TEXT`), then re-run `python auto-apply/build_userscript.py` and re-import
into Tampermonkey.

### Notes
- Dedup: the shortlist excludes jobs already in `applications.csv`; LinkedIn also
  badges applied jobs "Applied ✓".
- Grounding: answers come only from `answers.yaml` / `ME` / résumé; the LLM is
  told to reply `FLAG` (→ flagged) rather than invent anything.


Linkedin = .venv/bin/python auto-apply/linkedin_shortlist.py
open auto-apply/shortlist.html
