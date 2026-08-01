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
python verify_geoids.py           # check every LinkedIn geoId is really that place
python harvest_ats.py             # probe the companies a sweep found for a free
                                  # public ATS board -> paste-ready ATS_BOARDS
```

## Applyable is not the same as scraped

The number that matters is not how many jobs a sweep returns, it is how many you
can actually take. Measured on the 2026-07-26 sweep (919 rows):

| score cutoff | scraped | **applyable** | geo-locked elsewhere | repost farms |
|---|---|---|---|---|
| >= 0  | 727 | **37** | 637 | 53 |
| >= 10 | 480 | **16** | 423 | 41 |
| >= 20 | 252 | **5**  | 212 | 35 |

Two filters, both now on by default, do that narrowing:

- `SETTINGS["remote_scopes"] = ["worldwide", "remote"]`. LinkedIn's `f_WT=2` means
  *remote within one country*, so 245 of the top 252 rows were remote-in-Germany /
  Spain / UAE / the UK — real jobs, just not ones an applicant in India can take.
  Rows whose lock is **to** India survive (`keep_restricted_if_hires_home`), which
  is why turning this on does not delete the India-remote roles.
- `SCORING["company_blocklist"]`. Lead-gen farms ("Hired", "Jobs Ai", "Hire Feed",
  "SWAKIO") repost real listings under their own name, one set of titles sprayed
  across country subdomains with sequential job IDs. Because they repost real
  postings they match a résumé exactly as well as the original does, so they land
  at the very top and no score threshold separates them. Dropped on the company
  name, whole-name and punctuation-insensitively.

Both filters are re-applied by `merge_jobs.py`, not just at scrape time. That
matters because the shortlist page and `auto-apply/` read the **merged** file: a
merge can't re-score old rows (the description isn't kept), but reachability and
the blocklist read stored columns, so they can be and are. Rows from files written
before `remote_scope` existed lack the column and are kept — absent means "that
sweep never asked", not "no".

Where the applyable 16 came from is the other half of the story: **12 from the free
ATS boards and feeds, 4 from the paid LinkedIn sweep.** The paid sources are worth
running to discover *which companies* hire your stack — then use `harvest_ats.py`
to find those companies' own boards and apply there instead.

## Working it daily

A sweep every two weeks means applying to week-old postings that are already
hundreds of applicants deep. The free sources cost nothing, so run them daily and
let the `seen.tsv` ledger do the narrowing:

```bash
python scraper.py --site free --only-new     # zero cost, only what's new today
```

That reports the handful of genuinely new postings each morning, which is a small
enough list to apply to properly. Save the paid sweep for once a fortnight.

## Paid sweeps and LinkedIn geoIds

LinkedIn searches by numeric `geoId` and **ignores** a free-text location, so a
wrong or missing one doesn't fail — it returns US results and bills you in full.
Two consequences, both now enforced in code:

- `_build_linkedin_url` **raises** on an unmapped location instead of falling
  back to free text, and `main()` preflights every planned search through it
  before the spend prompt, so bad config costs nothing to discover.
- `f_WT=2` filters remote **within a geography** — there is no worldwide remote
  search. A bare `"Remote"` location needs `SITES["linkedin"]["remote_geo"]` to
  say which region; set `remote_only: True` to make a list of countries into a
  list of remote-in-that-country searches.

`verify_geoids.py` checks any id against LinkedIn's public guest search (no auth,
free) by looking at where the returned jobs actually are. It found two bad
entries already in this config: `New Delhi` pointed at **Inner Mongolia, China**,
and `Noida` returned nothing. Run it before spending on any id you added.

Rough costs, from this repo's measured rates (LinkedIn ~$0.001/result, Indeed
~$0.005/result, Naukri ~$0.50 minimum **per run**):

| Sweep | Results | Cost |
|---|---|---|
| `global_remote` as shipped (7 kw × 12 countries × 25) | 2,100 | **~$2.10** |
| 9 kw × 20 countries × 50, LinkedIn | 9,000 | ~$9 |
| 9 kw × 20 countries × 50, Indeed | 9,000 | ~$45 |

Indeed is 5× the cost for the same volume and its coverage skews domestic and
onsite, so `global_remote` leaves it off. `SETTINGS["max_spend_usd"]` stops
launching new runs once a sweep hits the cap.

## Configure

Everything that decides *what* is pulled and *how* it is ranked lives in
`config.py` — you should not need to touch `scraper.py`.

| Section | Controls |
|---|---|
| `SEARCH` | role keywords x locations, experience, results per search |
| `SITES` | which paid Apify actors run |
| `ATS_BOARDS` | free company career boards, `{platform: {token: "Name"}}` |
| `FEEDS` | free remote-job feeds (Remote OK, WWR, Remotive, Jobicy, Himalayas) |
| `LOCATION_HINTS` | location whitelist for free sources; **empty = allow all** |
| `ATS_TITLE_HINTS` / `ATS_TITLE_EXCLUDE` | which titles a free source keeps; exclude wins |
| `SCORING` | skill weights, full-stack bonus, penalties, seniority tiers, company blocklist |
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
text actually demands, ignoring figures that aren't counting experience ("minimum
16 years of formal education" is a degree, not a career). When a posting states
several, `SETTINGS["experience_aggregate"]` decides which one is the requirement:
`"min"` for short JDs, `"max"` for the long structured kind that give a total
*and* a per-skill figure ("8+ years of total software engineering experience,
including 2+ years hands-on in AI/ML" is an 8-year job — `min` ranked it first of
63 as if it wanted 2). The title lists only handle labelling:

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

## Sources

All free, no credentials, no rate limits worth worrying about:

| Source | Kind | Notes |
|---|---|---|
| Greenhouse / Lever / Ashby / SmartRecruiters | company boards | one dict entry per platform, one token per company |
| We Work Remotely | RSS | best source of genuinely worldwide roles |
| Remote OK | JSON | whole board in one request |
| Remotive | JSON | software-dev category; **reports pay** |
| Jobicy | JSON | engineering industry; **reports pay** |
| Himalayas | JSON | **reports pay and exact UTC offsets** |
| Optum (UHG) | employer site | `sources/optum.py`; **publishes requisition numbers** and is liveness-verified |

The three structured feeds are the only free source that reports compensation —
the ATS boards never do — which is what makes `min_comp_usd` do anything at all.
Coverage is still thin (~3% of raw rows), so treat pay as a bonus signal.

Himalayas has the best metadata and the worst access pattern: its API accepts no
category or search filter and pages 20 at a time through ~96k mostly
non-engineering jobs, so `FEEDS["himalayas"]["pages"]` bounds how hard we chase
it. Its `timezoneRestrictions` come back as real UTC offsets, which beats
inferring a zone from a region name.

Probed and deliberately **not** included: `arbeitnow.com` (100 jobs → 5 remote →
1 dev-titled, and that one onsite in Nuremberg), Workable (endpoint is live but
every slug tried returned zero jobs, so the field names are unverified), and
Workday (needs a POST body and a per-tenant hostname, so it can't be a row in
the ATS table).

### Optum — one employer, by requisition number

```bash
python scraper.py --profile optum --site optum      # free -> output/optum/
```

For applying through a referral, where you need the **requisition number** the
referral is submitted against, not just a link. Two output columns exist for
this: `req_number` and `verified_live` (blank for every other source).

`careers.optum.com` is dead (NXDOMAIN, 2026-07-29). Optum requisitions are served
from `careers.unitedhealthgroup.com`, a Radancy/TalentBrew site that hosts *every*
UHG brand in one index, so rows are filtered by the per-card `brand-facet__optum`
CSS class — the only place the brand appears. The site's own `Brand` facet is
business segments ("Medicare & Retirement"), not brands, so it can't do this.

It is not a row in `sources/ats.py`'s table because the search endpoint returns
HTML *inside* JSON and the listing carries neither a description nor a date —
both need a request per job. That request is also the liveness check: a pulled
requisition 404s, and those rows are dropped.

**A requisition's open/closed state is not publicly checkable beyond that.**
`uhg.taleo.net/.../jobapply.ftl?job=<req>` answers `200` with an identical
privacy-agreement gate for a live req and a nonexistent one alike (probed
against three), so it carries no status signal without a candidate session.
"In the live index AND its JD still 200s" is the strongest available signal —
don't add the Taleo URL as a check, it will confirm anything.

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
- **One employer's own site** (no public ATS API) → its own module, like
  `sources/optum.py`, wired into `sources.fetch_free` and gated by an
  `enabled: False` config block so a normal sweep is unaffected. Worth it when
  the site exposes something the aggregators don't — a requisition number, or a
  way to prove the job is still open.

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
