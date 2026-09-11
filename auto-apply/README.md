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

## Local profile generation, and where the model runs

`SWEEP_PROFILE_ENGINE` picks who reads the résumé — `gemini` (default),
`local-first` (local, Gemini only when a deterministic check escalates), or
`local` (local only, no API key). `SWEEP_INFERENCE_BACKEND` then picks *where
the local model runs*, and changes nothing else about the pipeline:

| `SWEEP_INFERENCE_BACKEND` | path |
| --- | --- |
| `local-direct` (default) | Sweep → Ollama → qwen3:8b, in the user's own process |
| `remote` | Sweep → `inference_service.py` → Ollama → qwen3:8b |

The service is a POC for moving the 5GB resident out of Sweep's process. It is
stateless, never writes a résumé or a prompt down, and never falls back to
local Ollama — a service that cannot answer fails loudly.

```bash
# shell 1 — the model service (refuses to start without a token)
SWEEP_INFERENCE_TOKEN=dev-token python -m inference_service

# shell 2 — Sweep, pointed at it
export SWEEP_PROFILE_ENGINE=local
export SWEEP_INFERENCE_BACKEND=remote
export SWEEP_INFERENCE_TOKEN=dev-token
python auto-apply/make_profile.py --engine local ...
```

`SWEEP_INFERENCE_URL` (default `http://127.0.0.1:8811`) and
`SWEEP_INFERENCE_PORT` move it; `OLLAMA_HOST` / `OLLAMA_MODEL` are read by the
*service*, which is the only component that knows Ollama exists.

Equivalence between the two backends is measured, not assumed:

```
python -m bench.backends --people ada,hana,kwame     # both backends, same corpus
python -m unittest discover -s auto-apply/tests -t auto-apply -p "test_inference.py"
```

Runtime requirements, every environment variable, the health check and the full
list of failure modes: [docs/inference-service.md](../docs/inference-service.md).
Where to host it and what it costs: [docs/inference-hosting.md](../docs/inference-hosting.md).
How to provision that host: [docs/oracle-deployment.md](../docs/oracle-deployment.md).

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

## Shareable job shortlist (for yourself or a friend)

`linkedin_shortlist.py` writes `shortlist.html` — a self-contained, theme-aware
page (no external assets, no secrets) listing every ranked job (all sources) as
a clickable row: match score, title, company, location, source. It's a complete
standalone document — open it in any browser or send the file to anyone (email,
WhatsApp, AirDrop); it needs no server and carries no secrets.

### For yourself
```
python auto-apply/linkedin_shortlist.py "Your Name"   # name is optional
open auto-apply/shortlist.html
```

### For someone else (their résumé → their shortlist)
The scoring lives in `config.py` and is tuned per person, so retune it first:

1. Drop their résumé at `auto-apply/resume/resume.pdf`.
2. Retune `config.py` to that résumé: paste `auto-apply/RESUME_AUTOCONFIG_PROMPT.md`
   into a fresh Claude Code window at the repo root (Claude reads the PDF and edits
   which skills/titles to search + rank — it asks you for locations, CTC, etc.).
3. Fetch + build in one step (⚠ `--scrape` uses Apify credits):
   ```
   python auto-apply/make_shortlist.py --scrape "Their Name"
   ```
   (Omit `--scrape` to just rebuild the HTML from the latest CSV, free.)
4. Send them `auto-apply/shortlist.html` — it opens in any browser, no setup.
