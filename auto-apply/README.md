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
