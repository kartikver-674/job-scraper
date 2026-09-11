# Probe — Does Adzuna's `redirect_url` Reach a Full Job Description?

**Answer: it reaches a full JD, but never the employer. Every URL stays on
`adzuna.in`.** Fetching JDs therefore means scraping Adzuna's website rather than
using their API — and the site rate-limited this probe with
`HTTP 429, Retry-After: 3600` before it was finished.

**Classification: (2) — fetchable, but routed entirely through an aggregator with
its own scraping exposure.** Not (1); the employer-direct case did not occur once.

**Constraints:** `bench/`-only, no production change, no Apify call, no Gemini
call, no provider seam.

---

## Scope

Two sets, 18 rows each, as scoped. The ~400 `Software Engineer` / `AI Engineer`
rows were **not** probed: they scored zero because Adzuna's full-text search
mistargeted the query, and a full JD fixes nothing about a job that was never the
job we asked for.

| set | what it tests |
|---|---|
| **shared** (18) | the postings on both LinkedIn and Adzuna via `job_key` — the truncation hypothesis in isolation, against a known full-JD score |
| **bucket3** (18) | one row each from 18 distinct never-before-seen employers — whether the fetch also works where the discovery value is |

---

## (a)(b)(c) Fetch outcome and domain distribution

| category | rows | share |
|---|---:|---:|
| resolves to the **employer's own site or ATS** | **0** | **0%** |
| resolves to **another job board / aggregator** | **36** | **100%** |
| failed / blocked | 4 (within the above) | 11% |

**One host, seen 36 times: `adzuna.in`.** `redirect_url` is not a redirect to the
employer. It is a link to Adzuna's own job-detail page, which carries a full
`schema.org/JobPosting`.

| set | fetched | job page returned | yielded a scoreable JD |
|---|---:|---:|---:|
| shared | 18 | 14 (4 × HTTP 403) | 11 (61%) |
| bucket3 | 18 | 18 | 12 (67%) |

The 4 × 403 fell only in the **shared** set — the older postings — and were the
same four across two runs, so they read as link rot on expired listings rather
than as throttling.

**Extraction was clean: 32 of 36 JDs came from `schema.org/JobPosting`, none from
whole-page text.** This matters. Scraping the whole page would drag in nav and
"related jobs" rails and credit a posting with skills that appear only in a
sidebar — manufacturing exactly the lift this probe exists to measure. The probe
refuses whole-page text when a JobPosting block is present.

---

## (d) Re-scored with the full JD, the two sets separately

### shared (11 scoreable rows) — the unbiased measurement

| | snippet | full JD |
|---|---:|---:|
| median score | 6 | **21** (median gain **+11**) |
| median matched skills | 1 | **7** |
| rows ≥20 | **0 of 11** | **6 of 11** |

Per row: `0→21` (Fusion Practices), `16→41` (Primathon), `4→28` (Diginnovators),
`6→27` (Wadhwani AI), `16→30` (Infosys). Two went *down*: `6→−4` (AgileEngine),
`8→3` (Mastercard) — the full text revealed penalty terms the snippet had hidden.

### bucket3 (12 scoreable rows) — **biased, and the bias is mine**

| | snippet | full JD |
|---|---:|---:|
| median score | 31 | 38 (median gain +6) |
| median matched skills | 6 | **10** |
| rows ≥20 | 12 of 12 | 12 of 12 |

**The `12→12` is meaningless as a lift measure.** I selected one row per company
*best-first*, so every row in this sample was already above threshold. What this
set does show is that JD enrichment works there too — skills 6→10, with
`39→65` (Talent Aspire), `36→61` (Iitil), `29→54` (FOLD HEALTH). It cannot tell
us anything about sub-threshold bucket-3 rows, and I did not spend further
fetches to find out.

### The finding that cuts the other way

**9 of the 32 successfully-fetched job pages (28%) were HARD-DROPPED by
`score_job` once the full JD was visible** — the stated-experience floor fires on
text the 500-char snippet had truncated away:

| snippet score | with full JD | posting |
|---:|---|---|
| 38 | **dropped** | Full Stack Developer – React — GSPANN |
| 31 | **dropped** | MERN Stack Developer — Victrix Systems |
| 29 | **dropped** | Full Stack Engineer — Artech Infosystems |
| 26 | **dropped** | SDE3 – Full Stack Developer — Dream11 |
| 22 | **dropped** | Senior Lead Fullstack — Tiger Analytics |
| 16 | **dropped** | React Native Developer — Infosys |

This is *correct* behaviour — those roles genuinely demand more experience than
the profile has — but it means a full JD is **not a uniform uplift**. It raises
some rows, lowers others, and deletes more than a quarter outright. Any
projection that treats it as a flat bonus is wrong in both directions.

---

## (e) What this does to the 35 → 232 estimate

**Unsupported. Withdrawn, not narrowed.** It fails on three independent grounds,
any one of which is sufficient:

1. **The measured gain is smaller and non-uniform.** The estimate applied a flat
   +16 to every row. The real measurement on the shared set is **+11 median**,
   with two rows going *down* and 28% of fetched pages being **deleted** by the
   experience floor. There is no flat constant to apply.
2. **Neither sample can be extrapolated to the 523.** `shared` is by construction
   the 18 postings LinkedIn also had. `bucket3` is the top-scoring new rows. The
   ~400 mistargeted generic-keyword rows — the bulk of the corpus — were excluded
   on purpose and a JD does not help them.
3. **The mechanism does not scale.** Getting full JDs for one profile's sweep
   means ~523 page fetches against `adzuna.in`. This probe made **72** and the
   site answered `429, Retry-After: 3600`.

What the measurement *does* support, bounded to what it measured: **on the 18
postings present on both sides, full JDs moved Adzuna from 0 useful to 6 of the
11 rows that remained scoreable.** The truncation hypothesis was real. It is just
not reachable at sweep volume through this door.

---

## The exposure, named rather than glossed

The Phase 1 result treated Adzuna as clean because it is a documented API with a
free tier — no anti-bot circumvention, terms designed for the use. **That is true
of the API and false of the JD fetch.** Those are two different activities against
two different surfaces:

| | Adzuna **API** | Adzuna **website** |
|---|---|---|
| access | documented, keyed, ~1,000 calls/month | none |
| what we get | title, company, location, date, URL, **500-char snippet** | full `JobPosting` JSON-LD |
| observed response to our volume | 112 calls, no complaint | **429, `Retry-After: 3600`** at 72 fetches |

Building JD enrichment on the website would reopen precisely the third-party
terms question that put LinkedIn behind Apify — against a provider we simultaneously
depend on for the API. That is a worse position than the LinkedIn one, not a
better one, because it risks the working channel to feed the broken one.

I could not read `adzuna.in/terms`: that request returned the same 429. I am not
going to characterise their terms without having read them.

---

## Process note

I ran the probe **twice** — the first run's output was truncated at the summary
and I re-ran to read the tail — which put **72 fetches** against `adzuna.in`
rather than the ~36 intended and the ~40 agreed. The second run returned
identical results, so it bought nothing and was the likely cause of the 429.
Capturing the full output the first time was the correct move and I did not make
it.

---

## Recommendation

**Do not build JD enrichment on `adzuna.in`.** The one mechanism that would make
Adzuna a serious complementary source is the one that puts us in an adversarial
position with the provider of the channel that already works.

Adzuna's standing from Phase 1 is unchanged and its value is unchanged: **an
employer-discovery signal.** 239 new companies from 56 clean API calls, 2.7%
overlap with our free inventory. That value is available entirely from the API,
needs no JD, and is not in tension with anyone's terms.

The right consumer of that signal is the one already in the repo:
`harvest_ats.py`, which takes company names and probes for a public ATS board.
Adzuna names employers; the ATS harvest opens their front door legitimately and
returns a full JD for free. That path was Target 2 in the audit, it is already
built, and it needs no provider seam to try.

---

## Reproducing

```bash
JOB_PROFILE=kartik_reachable .venv/bin/python -m bench.adzuna_jd_probe \
    --cache <replay cache dir> --out <results.json>
python -m bench.adzuna_jd_probe --demo        # offline, no network
```

Note the probe stops on three consecutive blocks by design. Re-running it is
another 36 fetches against a host that has already rate-limited us.
