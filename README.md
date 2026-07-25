# Job Scraper

Pulls software-engineering jobs from **free** sources (company ATS boards +
public remote-job feeds) and **paid** Apify actors (Indeed / LinkedIn / Naukri),
scores every job against a résumé, de-duplicates across sources, and writes a
ranked CSV + JSON.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Only the paid sources need credentials. `.env` (git-ignored):

```
APIFY_TOKEN=apify_api_...
APIFY_TOKEN_2=apify_api_...    # optional fallback when the first hits its cap
```

## Run

```bash
python scraper.py --demo          # offline self-check, no network, no cost
python scraper.py --site free     # free sources only — zero cost
python scraper.py --dry-run       # print the paid plan, spend nothing
python scraper.py --test          # tiny real paid run (indeed, 1 combo)
python scraper.py                 # everything (confirms before a large sweep)
```

Output: `output/jobs_<timestamp>.csv` + `.json`, ranked best-first.

Related tools:

```bash
python rescore_from_apify.py      # re-score already-paid runs against the current
                                  # config, for free -> output/jobs_combined.*
python merge_jobs.py              # merge + dedupe every output/jobs_*.json
```

## Configure

Everything that decides *what* is pulled and *how* it is ranked lives in
`config.py` — you should not need to touch `scraper.py`.

| Section | Controls |
|---|---|
| `SEARCH` | role keywords x locations, experience, results per search |
| `SITES` | which paid Apify actors run |
| `ATS_BOARDS` | free company career boards, `{platform: {token: "Name"}}` |
| `FEEDS` | free remote-job feeds (Remote OK, We Work Remotely) |
| `LOCATION_HINTS` | location whitelist for free sources; **empty = allow all** |
| `SCORING` | skill weights, full-stack bonus, penalties, hard drops |
| `SETTINGS` | freshness, pay floor, remote/visa/EOR filters, spend caps, output |

## International-remote signals

`enrich.py` reads four things out of text the sources already return — no extra
requests — and they appear as columns whether or not you filter on them:

| Column | Values |
|---|---|
| `remote_scope` | `worldwide` · `remote` (scope unstated) · `restricted` (geo-locked) · `hybrid` · `onsite` · blank (not stated) |
| `remote_regions` | recognized regions that gate eligibility, e.g. `US, Canada` |
| `visa` | `yes` · `no` (an explicit refusal) · blank |
| `eor` | employer-of-record providers named, e.g. `Deel`, `EOR`, `contractor` |
| `timezones` | overlap requirements as stated, e.g. `overlap with CET` |

`remote_scope` is decided from the location first, and a location that still
names somewhere specific after the remote wording is stripped
(`New York, NY (HQ), Remote`) is treated as geo-locked. Job-description prose is
only consulted when the location is silent, because benefits sections that list
per-country perks read like eligibility rules and are not.

The matching filters in `SETTINGS` all **default to off** — a blank signal means
"the posting didn't say", never "no":

```python
"remote_scopes": ["worldwide", "remote"],   # drop onsite/hybrid/geo-locked
"drop_no_visa": True,                       # drop only explicit refusals
"require_eor": True,                        # keep only jobs naming an EOR path
```

## Adding a source

- **A company** → one token in `config.ATS_BOARDS` under its platform.
  Token comes from the careers URL: `boards.greenhouse.io/<token>`,
  `jobs.lever.co/<token>`, `jobs.ashbyhq.com/<token>`,
  `careers.smartrecruiters.com/<Token>`.
- **An ATS platform** → one dict entry in `sources/ats.py`'s `ATS` table: a URL
  template, where the job list sits in the response, and a field → dotted-path
  map. Verify it against a live board first — a wrong path yields blank titles
  rather than an error.
- **A feed** → one function in `sources/feeds.py` with the signature
  `(cfg, keep_title, keep_location) -> [row]`, plus a line in
  `sources.FEED_FETCHERS`.

Then extend `sources/__main__.py`'s fixtures and run `python -m sources`
(offline) or `python -m sources --live` (one real request per source).

## Notes

- Apify actors are **pay-per-event**. `--dry-run` and `--site free` cost nothing;
  keep `max_results` modest while testing, and `SETTINGS["max_spend_usd"]` caps a
  sweep.
- A sweep checkpoints after every search, and `output/.done_combos` lets a rerun
  resume the same day without paying twice. It is scoped to today's date, so the
  next day's sweep re-scrapes normally.
- Compensation is annualized and converted to USD (`scraper.comp_max_usd`), so an
  Indian LPA figure and a US salary compare on one axis. Undisclosed or
  unknown-currency pay is always kept — the filter never drops on a guess.
- De-duplication keys on company + title, deliberately **not** location: the same
  remote role is "Remote" on one board and "Anywhere in the World" on the next.
- **Rotate your Apify token** if it has ever been shared in plaintext
  (Apify Console → Settings → Integrations → API tokens).
