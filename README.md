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

python scraper.py --profile remote_intl --site free    # a different search
python scraper.py --only-new                          # skip what earlier runs reported
```

Output: `output/jobs_<timestamp>.csv` + `.json`, ranked best-first (or
`output/<profile>/` for a named profile).

## Profiles

One scraper, several searches. A profile is `profiles/<name>.py` listing **only**
the config keys it changes; anything omitted falls through to `config.py`:

```python
# profiles/srishti.py
SEARCH   = {"role_keywords": ["Data Analyst"], "locations": ["Remote"]}
SETTINGS = {"remote_scopes": ["worldwide", "remote"], "min_comp_usd": 30000}
```

```bash
python scraper.py --profile srishti     # -> output/srishti/
JOB_PROFILE=srishti python scraper.py    # equivalent
python merge_jobs.py --profile srishti   # merges that profile's files
```

Merging is one level deep — a profile setting `SCORING["skill_weights"]` replaces
the whole stack but leaves `penalty_terms` alone. An unknown profile name is a
hard error, never a silent fall back to the default (which would run someone
else's search and spend real money doing it).

Ships with `remote_intl`: international remote roles workable from India, free
sources only, `remote_scopes` restricted to worldwide/unstated, `min_comp_usd`
raised to 30000.

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
| `SCORING` | skill weights, full-stack bonus, penalties, seniority tiers |
| `SETTINGS` | freshness, pay floor, remote/visa/EOR filters, spend caps, output |

## Only showing what's new

Running every ~2 weeks against a 14–21 day freshness window means about a week of
postings overlap with the previous run, so a chunk of each report is jobs you
already dismissed. `--only-new` suppresses them:

```bash
python scraper.py --only-new
```

Every posting a run reports is appended to `output/[profile/]seen.tsv` (one line:
date, identity key, title, company) whether or not you use the flag, so the
ledger is always ready for the next sweep. Identity is the same company+title key
as de-duplication, so one job seen on three sources is one ledger entry.

The file is plain TSV, not a database — 26 runs a year is a few thousand lines and
the only question ever asked of it is "have I seen this key". Delete it to start
over; delete individual lines to resurface specific jobs.

## Seniority: two tiers, because a title is not a requirement

`SETTINGS["max_experience_years"]` is the real gate — it reads the years the job
text actually demands. The title lists only handle labelling:

- `SCORING["hard_drop_terms"]` (manager, principal, staff, architect, …) —
  removed outright.
- `SCORING["soft_drop_terms"]` (senior, sr, lead) — **never removed**, only
  down-ranked by `soft_penalty`, so `max_experience_years` decides on the stated
  requirement instead.

Title inflation is why: on a live sweep, hard-dropping "Senior"/"Lead" deleted 13
of 28 reachable remote roles whose own JDs asked for ≤3 years (Twilio, Datadog,
Proxify, Lemon.io, A.Team). Set `drop_excluded: False` to keep hard drops too,
ranked to the bottom.

## International-remote signals

`enrich.py` reads four things out of text the sources already return — no extra
requests — and they appear as columns whether or not you filter on them:

| Column | Values |
|---|---|
| `remote_scope` | `worldwide` · `remote` (scope unstated) · `restricted` (geo-locked) · `hybrid` · `onsite` · blank (not stated) |
| `hires_home` | `yes` · `no` · blank — does this **employer** hire in your country at all? |
| `tz_gap` | hours between your timezone and the closest one the role requires |
| `remote_regions` | recognized regions that gate eligibility, e.g. `US, Canada` |
| `visa` | `yes` · `no` (an explicit refusal) · blank |
| `eor` | employer-of-record providers named, e.g. `Deel`, `EOR`, `contractor` |
| `timezones` | overlap requirements as stated, e.g. `overlap with CET` |

**`hires_home` is the most useful of these.** It's a property of the *employer*,
not the posting: a company with any job listed in `HOME_LOCATION_HINTS`
demonstrably has an entity or EOR relationship there, so its geo-locked "US
Remote" role is worth an application, while a company with none is a dead end
whatever the wording says. `keep_restricted_if_hires_home` uses it to rescue those
roles from the `remote_scopes` filter. Measured: OpenAI (9 India postings of 753)
and Postman (12/114) qualify; Linear (0/25) and Ramp (0/118) don't.

It costs no extra requests — the rows were already fetched and previously thrown
away. It needs a whole company board to compute, so **only ATS sources can answer
it**; feed rows (Remote OK, WWR) are always blank and never rescued.

`tz_gap` down-ranks rather than filters, and only past 5 hours
(`enrich.TZ_FREE_HOURS`). From IST: Europe 4.5h is free, UK 5.5h costs almost
nothing, US 11.5h costs ~10 points. A role open to "US or EMEA" is scored on its
EMEA leg, since that's the side you could actually take.

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
"keep_restricted_if_hires_home": True,      # ...but rescue geo-locked roles at
                                            # employers who hire in your country
"home_utc_offset": 5.5,                     # IST; None to skip tz scoring
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
