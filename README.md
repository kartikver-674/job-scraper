# Job Scraper (Apify → Excel)

Scrapes job listings from **Indeed**, **LinkedIn**, and **Naukri** via Apify
Actors and writes a single Excel file with one sheet per site plus a combined
"All Jobs" sheet.

## Setup

```bash
cd job-scraper
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell
pip install -r requirements.txt
```

Your Apify token lives in `.env` (already created, git-ignored):

```
APIFY_TOKEN=apify_api_...
```

## Configure the search

Edit `config.py`:

- `SEARCH` — keywords, location, country, experience, salary, max results per site.
- `SITES` — toggle each site `"enabled": True/False`.

Starts with **Indeed only** enabled. Flip `linkedin` / `naukri` to `True` once
you've confirmed a run works.

## Run

```bash
python scraper.py
```

Output lands in `output/jobs_YYYY-MM-DD_HHMM.xlsx`.

## Notes

- All three Actors are **pay-per-event** — each run consumes Apify credits.
  Keep `max_results` modest while testing.
- If one site fails, the others still complete and you still get an Excel file.
- **Rotate your Apify token** if it has ever been shared in plaintext
  (Apify Console → Settings → Integrations → API tokens).
```
