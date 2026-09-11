# Apify Independence — Dependency Audit and POC Plan

**Status:** read-only audit. No production code changed, no scraping performed,
no Apify or Gemini calls made. Branch `apify-independence`.

**Baseline:** the Gemini-free architecture as merged at `c2a5a4f`. This document
does not propose changing it.

---

## 0. The five questions, answered up front

**1. What are we paying Apify to do?**
One thing, not many: **keyword-targeted retrieval across the whole employer
universe**. Every other job Apify appears to do — HTTP, retries, pagination,
normalization, dedupe, scoring, storage — Sweep already does itself for its free
sources, in `sources/` and `scraper.py`. What Apify uniquely supplies is a
searchable index of employers we have never heard of. Measured across all 101
output files (5,661 distinct postings): Apify-sourced rows are **86.8% of
postings scoring ≥20 and 91.7% of those scoring ≥40**, and **2,618 of 2,937
distinct companies (89%) have only ever been seen through Apify**.

**2. What can we replace with ordinary code?**
The transport, the plan, the budget guard, the schema and everything downstream —
all of it is already ours. The replaceable *acquisition* is the ATS/feed tier,
which already exists and works. The genuinely Apify-dependent part is discovery,
and it is replaceable only by substituting a different searchable index
(aggregator API), not by writing a better fetcher.

**3. Which sources first?**
Ranked by value-per-risk: **(1) an aggregator search API with India coverage
(Adzuna is the concrete candidate), (2) ATS-token harvesting at scale via the
existing `harvest_ats.py`, (3) Workable** as the one unfinished platform in an
otherwise complete ATS table. None requires anti-bot circumvention. See §4.

**4. What does the smallest viable self-hosted system look like?**
Not a new system. `sources.fetch_free()` is already the provider registry; the
smallest viable change is to give it a *search-shaped* provider interface (the
one thing it lacks — it pulls whole boards and filters locally) and let
`scraper.main()` route a search plan through free providers before falling
through to Apify. Roughly one new module plus a signature change. See §3 and §5.

**5. What stays behind Apify?**
LinkedIn, indefinitely, and Indeed for now. LinkedIn is 84.2% of useful
inventory on its own and is the one source whose terms explicitly prohibit
automated access — `hiQ` settled that scraping public data is not a CFAA crime,
but LinkedIn lost nothing on the contract claim. Indeed retired its Publisher
API. Both belong behind a provider we pay to carry that risk.

---

## 1. Apify dependency map

### 1.1 Where the token is read

| Location | What it does |
|---|---|
| [scraper.py:1309](scraper.py#L1309) `apify_tokens(env)` | Enumerates `APIFY_TOKEN`, `APIFY_TOKEN_2…N` from the environment, ordered by slot, deduped by value. The single source of truth — a hardcoded list here is a bug the codebase has already had. |
| [scraper.py:1338](scraper.py#L1338) `_require_token()` | Picks the first token with headroom; exits if none. |
| [scraper.py:1284](scraper.py#L1284) `_token_headroom(token)` | `GET https://api.apify.com/v2/users/me/limits?token=…` — the only raw Apify HTTP call in the repo. |
| [sweep/app.py:250](sweep/app.py#L250) `check_token()` | Validates a pasted token via `ApifyClient(token).user().limits()`. |
| [sweep/app.py:1169](sweep/app.py#L1169) | Writes the accepted token to `.env` as `APIFY_TOKEN`. |
| [sweep/app.py:596](sweep/app.py#L596) `snapshot()` | Polls `scraper.account_usage_usd()` for the live spend gauge. |
| [rescore_from_apify.py](rescore_from_apify.py) | Reads *already-paid* datasets back for free re-scoring. Reading a dataset costs no events. |

`.env` is hand-parsed for `APIFY_TOKEN*` in `sweep/app.py`; `python-dotenv` loads
it for the CLI entry points.

### 1.2 Which Actors are invoked

Declared in [config.py:83-104](config.py#L83-L104):

| Site | Actor | Enabled | Measured rate ([config.py:426](config.py#L426)) | Basis |
|---|---|---|---|---|
| linkedin | `curious_coder/linkedin-jobs-scraper` | yes | $0.045 / search | 25 results |
| indeed | `misceres/indeed-scraper` | yes | $0.09 / run | 15 results |
| naukri | `muhammetakkurtt/naukri-job-scraper` | **no** | $0.50 / run *minimum* | 50 results |

Naukri has produced **zero rows across every run in `output/`** and appears in no
`.done_combos` — it has never actually executed. Treat it as declared, not used.

### 1.3 What is sent

[`build_input(site_key, search)`](scraper.py#L811) is the whole request surface —
three adapters, one per site, each mapping one `(keyword, location)` combo:

- **indeed** — `{position, location, country, maxItemsPerSearch,
  parseCompanyDetails: False, saveOnlyUniqueItems: True, followApplyRedirects: False}`
- **linkedin** — `{urls: [<one linkedin.com/jobs/search URL>], count, scrapeCompany: False}`.
  The URL is built by [`_build_linkedin_url`](scraper.py#L752) from `keywords`,
  `geoId`, optional `f_C` (company), `f_WT=2` (remote), `f_E` (experience band),
  `f_TPR=r<seconds>` (recency). It **raises rather than guesses** on an unmapped
  geoId or company id, because LinkedIn silently returns the wrong country's or
  the wrong employer's jobs at full price.
- **naukri** — `{keyword, maxJobs, fetchDetails, sortBy, freshness, cities[]|workMode[], experience}`

Billable depth is decided in exactly one place,
[`effective_search()`](scraper.py#L938), so the dry-run cost estimate and the
actor input cannot drift apart.

### 1.4 What is consumed from the response

[`normalize(item, source)`](scraper.py#L161) maps any actor payload onto the
internal schema via [`FIELD_KEYS`](scraper.py#L69) — a field → candidate-key list,
first non-empty wins. **Eight fields, and nothing else is read:**

```
Title, Company, Location, Salary, Experience, Posted Date, Job URL, Description
```

Naukri needs [`normalize_naukri`](scraper.py#L130) because it nests everything
under `jobDetails`. `sources/ats.py` fills the *same* eight keys plus
`hires_home`. That schema is the real provider contract, and it already exists.

### 1.5 Pagination, concurrency, retries, errors

- **Pagination:** none. Depth is a per-search input cap (`maxItemsPerSearch` /
  `count` / `maxJobs`); breadth comes from running more `(keyword × location)`
  combos. Results are read with `client.dataset(id).iterate_items()`.
- **Concurrency:** none. Strictly sequential, sites in config order (cheapest
  first, so a mid-run stop sacrifices the expensive tail).
- **Run handling** ([scraper.py:989](scraper.py#L989)): deliberately **not**
  `.call()` / `.wait_for_finish()` — both long-poll with `timeout='no_timeout'`
  and hang forever on a half-dead TCP socket. Instead: `.start()` with a
  server-side `run_timeout=5min`, then `.get()` polling every 5s against a
  360s wall-clock deadline, then `.abort()`. A non-`SUCCEEDED` status raises.
- **Error isolation:** per-search `try/except` appends to `failures` and
  continues. Because that means a sweep can fail almost entirely and still exit
  0, `main()` reports the failure count loudly and special-cases
  "monthly usage hard limit" with the next-token instructions.
- **Preflight:** every planned search is run through `build_input()` *before any
  spend*, and the run is refused if any would cost money for bad data.
- **Budget:** `SETTINGS["max_spend_usd"]` checked before each search, measured
  against `account_usage_usd()` (month-to-date account spend, not the actor's
  self-report, which undercounted a measured 84-run sweep 3×: $0.53 vs $1.61).
- **Resume:** `output/<profile>/.done_combos`, one `date|site|keyword|location|company`
  line per completed combo, date-scoped so it expires daily. Written after each
  search; outputs are re-emitted as a checkpoint after each search too, so an
  interrupted sweep leaves a correct file.

### 1.6 The complete path

```
config.SEARCH.role_keywords × locations
  └─ build_search_plan()            scraper.py:862      keyword-major ordering
      └─ plan_for_site()            scraper.py:905      per-site keyword/location overrides, --limit cap
          └─ effective_search()     scraper.py:938      authoritative billable depth
              └─ build_input()      scraper.py:811      → actor input JSON
                  └─ scrape_search()scraper.py:989      start → poll → dataset
                      └─ normalize()scraper.py:161      → the 8-field internal schema
                                                        ── merges here with sources.fetch_free() ──
  └─ finalize()                     scraper.py:1109
      ├─ score_job()                scraper.py:550      skill weights, full-stack bonus, penalties
      ├─ min_score / max_age_days / min_comp_usd filters
      ├─ enrich signals             enrich.py           remote_scope, visa, eor, timezones (text-only, zero requests)
      ├─ sort by score, then dedupe()  scraper.py:712   job_key = req_number > company+title > canonical URL
      └─ to_output()                scraper.py:1059     → the 23 OUTPUT_COLUMNS
  └─ write_outputs()                → output/<profile>/jobs_<stamp>.csv|.json
  └─ record_seen()                  → output/seen.tsv   cross-run "already reviewed" ledger
```

### 1.7 Apify-dependent vs. already ours

| Capability | Owner today | Apify-dependent? |
|---|---|---|
| Search-plan construction | `build_search_plan`, `local_search.budget_order` | **No** |
| Budget modelling / spend cap | `SITE_RATES`, `account_usage_usd`, `sweep/logic.py` | Cost *model* is Apify-shaped; mechanism is ours |
| HTTP with bounded timeout + backoff | `sources/_http.py` | **No** — and it is stricter than the Apify path |
| Pagination | `sources/enterprise.py`, `sources/optum.py` | **No** |
| Normalization to a common schema | `FIELD_KEYS`, `sources/ats.py:BLANK` | **No** |
| Scoring / ranking / centrality | `scraper.score_job`, `corpus_signal.py` | **No** |
| Remote/visa/EOR/timezone enrichment | `enrich.py` | **No** |
| Dedupe across sources | `job_key`, `dedupe` | **No** |
| Freshness, comp, seniority filtering | `finalize` | **No** |
| Storage / resume / seen-ledger | `.done_combos`, `seen.tsv` | **No** |
| **Employer discovery by keyword** | — | **YES. This is the entire dependency.** |
| **Anti-bot / auth-wall traversal** | — | **YES** (LinkedIn, Indeed) |

---

## 2. Source classification

Cited from the code and from probe results already recorded in-repo.

### Bucket A — public API / ATS / feed, accessible directly (working today)

| Source | Endpoint | Where |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | `sources/ats.py` |
| Lever | `api.lever.co/v0/postings/{token}?mode=json` | `sources/ats.py` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{token}` | `sources/ats.py` |
| Breezy | `{token}.breezy.hr/json` | `sources/ats.py` |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/{token}/postings?limit=100` | `sources/ats.py` |
| Amazon | `www.amazon.jobs/en/search.json` | `sources/enterprise.py` |
| Oracle Recruiting Cloud | `{host}.oraclecloud.com` — Oracle, JPMorgan Chase | `sources/enterprise.py` |
| Workday | POST `accenture.wd103.myworkdayjobs.com` | `sources/enterprise.py` |
| RemoteOK / WWR / Remotive / Jobicy / Himalayas | public JSON + RSS | `sources/feeds.py` |

Caveats already recorded: SmartRecruiters and Breezy listings carry **no
description**, so those postings are scored on title alone. Amazon is the only
enterprise platform whose listing includes the JD.

### Bucket B — public career page, plain HTTP, HTML parsing

| Source | Endpoint | Note |
|---|---|---|
| SAP (SuccessFactors) | `jobs.sap.com`, `locationsearch=India` genuinely filters | working, `sources/enterprise.py` |
| Optum / UHG (Radancy) | `careers.unitedhealthgroup.com` | working; needs one JD request per job, which doubles as a live-requisition check |
| Deloitte (Avature) | `apply.deloitte.com` | server-rendered and parseable, **but** the India facet returns US rows — Deloitte India recruits elsewhere. Parked. |

### Bucket C — JavaScript-rendered, would require a browser

All four probed 2026-08-15 and rejected, recorded in `sources/enterprise.py`:

- **Microsoft** — Eightfold SPA; old search API 404s with a mismatched TLS cert;
  `microsoft.eightfold.ai/api/apply/v2/jobs` answers 403 regardless of headers.
- **IBM** — Next.js; `__NEXT_DATA__` holds only translation strings.
- **Capgemini** — page names `cg-jobstream-api.azurewebsites.net/api`; 11 plausible paths 404.
- **Siemens** — Avature, zero job anchors in served HTML.

### Bucket D — explicit automation restrictions or unclear access

- **LinkedIn** — User Agreement explicitly prohibits scraping and automated
  harvesting. `hiQ v. LinkedIn` (9th Cir. 2022) held that scraping public data
  is not a CFAA violation, but hiQ still lost the breach-of-contract claim and
  shut down. Auth walls and rate limiting are enforced in practice. **84.2% of
  our useful inventory.**
- **Indeed** — Publisher API retired (2023–24); the remaining Apply API is
  partner-only. Site terms prohibit scraping.
- **Naukri** — declared in config, never run, no measured behaviour. Unclear.

### Bucket E — unknown

- **2,618 companies** surfaced only via Apify and never probed for a public ATS.
  This is the single largest unmeasured quantity in the audit and the one
  `harvest_ats.py` exists to answer.
- **Workable** — `apply.workable.com/api/v1/widget/accounts/{slug}?details=true`
  is live (200) and returns a `jobs` array, but every slug probed in-repo
  returned zero jobs, so the field paths are unverified. `sources/ats.py` records
  it as a deliberate gap rather than guessing paths — a wrong path yields a board
  of blank titles, silently.

---

## 3. Proposed `JobSource` / `AcquisitionProvider` abstraction

**The abstraction largely exists.** `sources.fetch_free()` is a provider
registry, `ats.ATS` is a table-driven platform adapter, `BLANK`/`FIELD_KEYS` is
the normalized schema, and `main()` already treats "free" and "paid" as two
families merging into one list. The proposal is to close the one real gap — free
providers pull *whole boards* and filter locally, while Apify providers answer a
*search* — and to demote Apify to a row in the same table.

### 3.1 Normalized schema (already the de-facto contract, written down)

Required: `Title`, `Company`, `Location`, `Job URL`, `Source`.
Optional, empty string = "not stated", never "no":
`Salary`, `Experience`, `Posted Date`, `Description`, `hires_home`,
`req_number`, `grade`, `verified_live`.

No new fields. Everything downstream — `score_job`, `enrich`, `job_key`,
`finalize`, `to_output` — already reads exactly this and nothing more.

### 3.2 Provider interface

```python
class JobSource:
    name: str            # lands in the Source column; "greenhouse:stripe", "apify:linkedin"
    kind: str            # "search" | "board"
    cost_per_search: float   # 0.0 for free providers; feeds SITE_RATES and the UI gauge

    def supports(self, search) -> bool:
        """False for a search this provider cannot express — e.g. a LinkedIn
        geoId we don't have. Checked in preflight, before any spend."""

    def fetch(self, search, keep_title, keep_location) -> list[dict]:
        """One search combo -> normalized rows. Raises on failure; the caller
        isolates per-search."""
```

Two `kind`s, because the difference is real and load-bearing:

- **`search`** providers answer a `(keyword, location)` combo. Apify's three
  actors and an aggregator API are search providers. They cost money or quota
  per call, so the budget model applies.
- **`board`** providers return a whole employer board, filtered locally by the
  `keep_title` / `keep_location` predicates. Every source in `sources/` is a
  board provider. They cost one request per board regardless of the plan, so the
  search plan does not multiply them — which is exactly why they are cheap and
  exactly why they cannot discover an employer we have not configured.

Keeping `supports()` separate from `fetch()` preserves the property that
`build_input()` has today and that has already saved money twice: **a request
that would return the wrong data fails before anything is spent.**

### 3.3 Error, retry and rate-limit semantics

Adopt `sources/_http.py` as the standard, because it is already the stricter of
the two paths: bounded timeout (25s), 2 retries with exponential backoff, **4xx
never retried** (a dead token should fail on the first try), and
`http.client.HTTPException` caught explicitly so a truncated body is treated as
transient. Per-provider failures stay isolated; a dead board or a rate-limited
API never kills a sweep. Rate limiting belongs *inside* the provider — an
aggregator with a 1,000-call monthly quota needs its own counter, and that is
not the sweep's business.

### 3.4 Deduplication, freshness, caching

All three stay where they are. `job_key` already reconciles the same posting
seen on LinkedIn, We Work Remotely and the company's own Ashby board into one
row, and it already prefers a published requisition number over the
company+title heuristic. Freshness is `max_age_days` in `finalize`, applied
uniformly. The only new caching boundary worth introducing is a **board cache**:
a whole-board pull is idempotent within a day, so a provider-level on-disk cache
keyed by `(provider, token, date)` would let a multi-profile sweep stop
re-fetching the same 800-job Databricks board per profile. That is an
optimization, not a requirement.

### 3.5 Coexistence and Apify's place in it

Providers are ordered cheapest-first (as sites already are), and the plan is
consumed in that order with the existing budget guard. Apify becomes:

```python
ApifySearchProvider(site="linkedin", actor="curious_coder/linkedin-jobs-scraper",
                    cost_per_search=0.045)
```

— one row among several, selected last because it is the most expensive, and
skipped entirely when no token is present. Sweep's `/key/free` route
([sweep/app.py:1136](sweep/app.py#L1136)) already proves the no-Apify path is a
first-class product state.

---

## 4. Minimum viable first replacement target

### 4.1 What the numbers say

Distinct postings across all 101 output files, best score per `(source, company, title)`:

| Family | Distinct | Share | ≥20 | of all ≥20 | ≥40 | of all ≥40 | India-located |
|---|---:|---:|---:|---:|---:|---:|---:|
| linkedin (Apify) | 3,724 | 65.8% | 1,680 | **84.2%** | 576 | **90.1%** | 49.6% |
| free: ATS boards | 1,107 | 19.6% | 134 | 6.7% | 17 | 2.7% | 21.1% (all free) |
| free: feeds | 397 | 7.0% | 94 | 4.7% | 28 | 4.4% | ” |
| indeed (Apify) | 255 | 4.5% | 51 | 2.6% | 10 | 1.6% | 86.3% |
| free: Optum | 135 | 2.4% | 31 | 1.6% | 6 | 0.9% | ” |
| free: Amazon | 22 | 0.4% | 1 | 0.1% | 0 | 0.0% | ” |

**Read this carefully — it is partly self-fulfilling.** LinkedIn rows are
retrieved by the profile's own role keywords; ATS boards are pulled whole and
filtered by a coarse title gate. So the table measures *yield per row retrieved
under the current architecture*, not intrinsic source quality. The honest
statement is: **keyword-targeted retrieval is worth roughly 6× the hit rate of
whole-board pulling**, and today only Apify does keyword-targeted retrieval.

The company-discovery number is not self-fulfilling and is the sharper one:
**2,618 of 2,937 companies (89%) exist in our data only because Apify found them.**

### 4.2 The three targets

**Target 1 — an aggregator search API (highest value).**
This is the only candidate that replaces the *function* rather than a source.
[Adzuna](https://developer.adzuna.com/) is the concrete proposal: RESTful,
documented, App ID + key, ~1,000 calls/month free, and **India is among its ~18
markets** — which matters, since 49.6% of our LinkedIn rows and 86.3% of our
Indeed rows are India-located. Its search response carries title, company,
parsed location, category, truncated description, `salary_min`/`salary_max` with
a `salary_is_predicted` flag, and a redirect URL — that maps onto seven of our
eight required fields directly, with `Experience` left empty (which `finalize`
already handles; `score_job` parses a years floor from the JD text anyway).
*Structured data, no anti-bot circumvention, terms designed for this use.*

**Target 2 — ATS-token harvesting at scale.**
`harvest_ats.py` already takes the companies a sweep surfaced and probes each
ATS platform's public endpoint. It has never been run against the full
2,618-company backlog. Every board it resolves converts a company we currently
pay LinkedIn to see into a free, direct, first-party source — with a better
apply path, which was the tool's original motivation. Measured yield so far:
**5 of 61 Indian employers resolved (8%)**, better for global tech firms (the
2026-07-27 harvest added Roku, ClickHouse, Flix, SumUp, Ubiquiti, Justworks).

**Target 3 — Workable.**
The one unfinished row in an otherwise complete ATS table. Low effort, bounded
risk, and it widens what Target 2 can resolve — `harvest_ats.py` probes exactly
the platforms in `ats.ATS`, so the 40+ Indian employers recorded as "no public
ATS" were only ever probed against four of them.

### 4.3 Coverage estimate — bounded, not invented

What can be stated from repo evidence:

- Free sources deliver **13.2% of useful (≥20) postings today**, with zero
  keyword targeting and 24 configured boards.
- Every additional resolved ATS board adds its employer's full inventory at zero
  marginal cost, but only for employers we have already discovered.
- Targets 2 + 3 therefore **cannot raise discovery coverage at all** — they
  convert already-discovered employers to a free channel. Their value is cost
  and apply-path quality, not reach.
- Target 1 is the only one that can move discovery coverage, and **its overlap
  with our LinkedIn inventory is unmeasured.**

**I will not put a percentage on Target 1 without measuring it.** The measurement
is cheap and is Phase 1 below: replay ~50 historical `(keyword, location)` combos
against the aggregator, and compute overlap against the LinkedIn rows those same
combos returned, using the existing `job_key`. That produces a real number in one
afternoon and within the free quota.

---

## 5. Phased POC plan

Each phase ends with a decision, and any phase may end the POC.

### Phase 0 — instrument, change nothing *(no new dependencies)*
Record per-provider attribution in the output rows so every later claim is
measurable rather than reconstructed from `source_site` strings. Re-run the
attribution in §4.1 as a committed script rather than an ad-hoc query.
**Exit:** the coverage numbers above are reproducible on demand.

### Phase 1 — measure the aggregator, buy nothing *(the decisive phase)*
Register for an Adzuna key. Replay ~50 historical combos from `.done_combos`
against it. Compute, via `job_key`: overlap with the LinkedIn rows for the same
combos, unique-to-aggregator rows, score distribution, India share, and field
completeness.
**Exit criterion:** if the aggregator recovers **≥50% of LinkedIn's ≥20
postings** for the same searches, the POC continues. Below ~25%, stop — the
honest conclusion is that Apify stays, and the remaining work is cost control,
not replacement.

### Phase 2 — the provider seam *(production change, behind a flag)*
Introduce `JobSource` per §3. Port `sources.fetch_free()` to it unchanged;
wrap the three Apify actors as search providers. Ship with routing identical to
today's behaviour and a contract test per provider — `sources/__main__.py`
already has the offline/`--live` self-check idiom to extend.
**Exit:** full suite green, a sweep produces byte-identical output to the
pre-change run.

### Phase 3 — the aggregator as a first-class provider
Add it as a search provider ordered *before* Apify. Cheapest-first ordering plus
the existing budget guard means it naturally absorbs the budget and Apify picks
up only what it cannot express.
**Exit:** a sweep at the same spend cap surfaces ≥ as many ≥20 postings as today.

### Phase 4 — harvest at scale
Add Workable to `ats.ATS` with verified field paths. Run `harvest_ats.py` across
the full company backlog. Promote what resolves into `ATS_BOARDS`.
**Exit:** measured reduction in paid searches needed per useful posting.

### Phase 5 — decide about LinkedIn
Explicitly: **do not attempt it.** Revisit only if Phase 1 and 3 leave a
demonstrated gap that only LinkedIn fills, and then evaluate *paid providers*,
not self-hosted acquisition.

**Not in this plan, deliberately:** any browser-based provider. Bucket C is four
household-name employers whose combined measured contribution to useful
inventory is approximately zero. Headless browsers are the largest operational
burden in the design and the least evidenced value.

---

## 6. Assumptions and unknowns

**Assumptions, stated so they can be falsified:**
1. `output/` is representative. It spans many profiles with different scoring
   configs, so cross-source score comparisons carry that noise. Company-discovery
   attribution (§1.7) does not.
2. Score ≥20 as the "useful" threshold follows the current default. A move to
   the deferred `min_score = 12` would change every percentage in §4.1, though
   not their ordering.
3. Free-tier quotas and coverage for any aggregator are as documented today.
   Unverified by us — Phase 1 verifies them.

**Unknowns, not filled with guesses:**
1. **Aggregator ↔ LinkedIn overlap.** The number the whole POC turns on. Unmeasured.
2. **ATS resolution rate across the 2,618-company backlog.** Known only for a
   61-company Indian sample (8%).
3. **Workable field paths.** Endpoint live, response shape unverified.
4. **Naukri.** Declared, never executed, no data.
5. **Long-run stability of the free enterprise adapters.** Every one is an
   undocumented endpoint that can change without notice; the four in
   `sources/enterprise.py` were probed once, 2026-08-15.
6. **Whether keyword targeting can be simulated over whole-board pulls.** If
   enough boards were configured, local filtering might approximate search
   without any aggregator. Untested, and bounded by discovery, not by retrieval.

---

## 7. What this audit did not do

No production code was modified. No scraping was performed. No Apify actor was
run and no Apify spend was incurred. No Gemini call was made. The only network
requests were web searches for the external facts cited in §2 (Indeed API
status, LinkedIn terms, Adzuna and Workable API shapes).
