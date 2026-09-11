# Phase 1 Result — Can Adzuna Replace the Discovery Function?

**Verdict: PARTIALLY. It is a genuine, independent discovery channel and it is
not redundant with anything we have — but it cannot retrieve the jobs LinkedIn
retrieves, and its usefulness today is gated by a 500-character description cap
rather than by its inventory.**

**Constraints honoured:** no production code changed (harness lives in `bench/`),
no Apify call, no Gemini call, no provider abstraction. 112 Adzuna calls total
against a documented ~1,000/month free quota. Credentials verified with one
non-search request before any measurement.

**Method.** `bench/adzuna_replay.py` replays the *complete* 64-combo
`.done_combos` ledger of `output/kartik_reachable/` (swept 2026-09-06, five days
before this run) against Adzuna, scores both sides with the same profile config,
and matches with `scraper.job_key`. 64 combos resolve to 56 API calls because
`Remote` and `India` are the same query once resolved. `max_days_old=36` was
derived to span the LinkedIn posting window (2026-08-07 → 2026-09-06).

---

## (a) Job level

| | LinkedIn | Adzuna |
|---|---:|---:|
| distinct postings | 562 | 523 |
| useful (≥20) | **243** | **35** |
| ≥40 | 63 | **0** |
| score min / median / max | −23 / 16 / 69 | −18 / 2 / **39** |
| matched skills per row (median) | **6** | **1** |
| rows matching zero skills | 16 (3%) | **248 (47%)** |

| overlap | |
|---|---:|
| shared via `job_key` | **18** |
| LinkedIn-only | 544 |
| Adzuna-only | 505 |

**The 0% recovery figure needs splitting, because it hides two separate failures.**

| | |
|---|---:|
| LinkedIn useful postings | 243 |
| …Adzuna returned the posting at all | **11 (4.5%)** — *inventory recovery* |
| …and Adzuna also scored it ≥20 | **0 (0%)** — *scored recovery* |

So Adzuna missed ~95% of LinkedIn's useful inventory outright, and under-scored
every one of the few it did have. Those are independent problems and only the
second is fixable.

**The 18 shared postings, scored on both sides.** Every Adzuna description is
exactly 500 characters.

| LinkedIn | Adzuna | skills LI→ADZ | title |
|---:|---:|---|---|
| 62 | 0 | 21 → 0 | App Developer – AI/ML — Warner Bros. Discovery |
| 46 | 13 | 13 → 4 | Node.js Backend Developer — AiLogic |
| 41 | 16 | 9 → 2 | Fullstack Developer — Primathon |
| 33 | 0 | 8 → 0 | Product Engineer — Servify |
| 30 | 16 | 7 → 2 | React Native Developer — Infosys |
| 28 | 4 | 9 → 2 | React Native Developer — Diginnovators |
| 24 | 0 | 7 → 0 | Software Engineer — Priceline |
| 21 | 0 | 11 → 0 | AI Product Engineer — Fusion Practices |

**Median score drop on the same posting: 16 points.** LinkedIn rated 11 of these
18 as useful; Adzuna rated **0 of 18**.

**Not a keying artifact.** Within the 44 employers present on both sides, Adzuna
lists 78 jobs and LinkedIn lists 78, of which `job_key` matches 18 (23%). The
other 60 are genuinely *different openings at the same employer*, not one opening
keyed apart. The low overlap is real inventory divergence.

**Sensitivity — what the 500-char cap costs.** An estimate, not a measurement:
lifting every Adzuna row by the median 16-point drop moves useful rows from
**35 → 232**, against LinkedIn's 243 on the same searches. The cap, not the
inventory, is what suppresses Adzuna's yield. This assumes the drop is uniform,
which it is not; treat it as an order of magnitude.

---

## (b) Company level — the number the POC turns on

338 distinct Adzuna employers, reconciled against **every** source in the repo.
The three buckets are mutually exclusive and exhaustive (asserted in code);
free-source membership wins ties, so an employer we can already fetch for free
can never be counted as discovery value.

| bucket | companies | meaning |
|---|---:|---|
| 1 — already in free-source inventory | **9** | we can already fetch these free |
| 2 — previously only via LinkedIn/Apify | **90** | Adzuna independently confirms them |
| 3 — **genuinely new to Sweep** | **239** | never seen via any source, paid or free |

Reconciled against 319 free-source companies and **2,653** paid-discovered
companies repo-wide, not just this profile's 410.

**Bucket 3 produced 27 useful (≥20) rows** — under the 500-char handicap, so
that is a floor.

Bucket 3 is not junk. A sample: Addepar, American Express GBT, Baker Hughes,
Bentley, BMW TechWorks, Capco, Carrier, Citigroup, Colt Technology Services,
Criteo, Avaloq, BlueOptima, Applause, Axtria, IQVIA, Houghton Mifflin Harcourt.
It also contains a visible staffing-agency tail (Anlage Infotech, BCforward,
Artech, Aditi Tech Consulting, Acme Services) which Sweep has no current filter
for.

**71% of the employers Adzuna surfaced, we had never seen.** That is the finding
that keeps this alive.

---

## (c) Field completeness, duplication, freshness, salary, mismatches

**Eight-field contract:**

| field | complete |
|---|---:|
| Title, Company, Location, Posted Date, Job URL, Description | **100%** |
| Salary | 8.0% |
| Experience | **0%** |

`Experience` is absent from Adzuna's schema entirely. Harmless — `score_job`
parses a years floor out of the JD text and most sources never populate it.

**Description is the problem.** 98.9% of rows are *exactly* 500 characters;
max 500; min 344. This is a hard truncation, and Sweep's entire value is
résumé-matched scoring over the JD.

**Duplicate rate 55.5%** across the replay (1,174 rows → 523 distinct). Expected:
64 overlapping combos. `job_key` handled it with no special casing.

**Freshness matches almost exactly**, which rules out the temporal confound:

| | Aug 2026 | Sep 2026 |
|---|---:|---:|
| Adzuna | 310 | 213 |
| LinkedIn | 326 | 236 |

**Salary — Adzuna is better than LinkedIn**, which I did not expect:

| | disclosed | predicted |
|---|---:|---:|
| Adzuna | 42/523 (**8.0%**) | **0** |
| LinkedIn | 64/1123 (5.7%) | n/a |

Zero predicted salaries were returned for the India market, so the guard that
keeps Adzuna's estimates out of the `Salary` field never had to fire here. It
stays, because it is not India-specific.

**Location targeting is excellent: 1 mismatch in 1,174 rows.**

| location | rows | useful | shared | mismatch |
|---|---:|---:|---:|---:|
| Bengaluru | 166 | 8 | 5 | 0 |
| Delhi | 86 | 4 | 8 | 0 |
| Gurgaon | 107 | 5 | 4 | 0 |
| Hyderabad | 146 | 10 | 6 | 0 |
| India | 200 | 10 | 8 | 0 |
| Mumbai | 132 | 8 | 7 | 1 |
| Pune | 137 | 10 | 5 | 0 |
| Remote (= India) | 200 | 10 | 8 | 0 |

**One real vocabulary mismatch:** `where=Bengaluru` returns 166 rows that all
read **"Bangalore"**. The query was honoured; the canonical name differs. Harmless
downstream (`HOME_LOCATION_HINTS` carries both), but it means place names must be
*mapped*, never assumed — the same class of error as a LinkedIn geoId.

**Title looseness is real.** Adzuna full-text matches the JD, not the title: only
43% of results for `Node.js Developer` mention "node" in the title at all
("AI Platform Engineer", "Sr Platform Engineer, JavaScript Fmwk"). Combined with
the 500-char snippet this is why `Software Engineer` and `AI Engineer` returned
200 rows each and **zero** useful ones.

**Per keyword** — the stability picture *within this profile*:

| keyword | rows | useful | shared |
|---|---:|---:|---:|
| MERN Stack Developer | 61 | **16** | 0 |
| Node.js Developer | 151 | 22 | 5 |
| Full Stack Developer | 194 | 11 | 1 |
| Full Stack Engineer | 184 | 9 | 16 |
| React Native Developer | 114 | 4 | 19 |
| Backend Engineer Node.js | 70 | 3 | 2 |
| Software Engineer | 200 | **0** | 5 |
| AI Engineer | 200 | **0** | 3 |

Specific keywords work; generic ones return volume and no signal.

**India:** the entire replay is India. All eight locations are Indian markets or
India-wide, and Adzuna's India market is enabled, correctly filtered, and fresh.
India is where Adzuna is *most* credible, not least.

---

## (4) Limitation — stated plainly, not as a footnote

**This covers one profile: `kartik_reachable`.** It is the only sweep whose
`.done_combos` is complete *and* whose profile module still exists, so it is the
only one where Adzuna rows can be scored by the same config that scored the
LinkedIn rows. `kanav_india` and the `val_*` ledgers lost their profile modules
when the experiment artifacts were deleted.

**Stability across query types was therefore NOT tested.** What was tested is
stability across eight keywords and eight locations *inside a single
full-stack/JavaScript profile in India*. Nothing here says how Adzuna behaves for
the Salesforce profile, for non-India markets, or for a résumé unlike this one.
The per-keyword table above is variation within one family, not across families.

Second limitation: postings filled since 2026-09-06 are gone from Adzuna's index
too. That biases overlap downward and cannot be corrected — only bounded, which
the matched freshness distributions do.

---

## (5) Verdict, on five dimensions

| dimension | result |
|---|---|
| 1. recovery of useful LinkedIn jobs | **4.5% inventory, 0% scored.** Fails the 50% reference criterion, and fails the 25% floor. |
| 2. genuinely new useful jobs/companies | **239 new employers (71%), 35 new useful rows, 27 from new employers** — from 56 free calls. Strong. |
| 3. overlap with existing free inventory | **9 of 338 companies (2.7%).** Almost no redundancy with what we already fetch free. |
| 4. field completeness | 6 of 8 fields at 100%; salary better than LinkedIn; **description capped at 500 chars — the binding constraint.** |
| 5. stability | Good within this profile; **untested across profiles.** Generic keywords degrade to zero yield. |

**Classification: PARTIALLY REPLACES the discovery function.**

It is not a LinkedIn substitute. On the single criterion Phase 1 was designed
around — recovering the useful jobs we currently pay for — it fails decisively,
and the audit's own stopping rule (below ~25%) is met. If the question is "can we
turn Apify off and lose nothing", the answer is **no**.

It is also not redundant. It overlaps our free inventory by 2.7% and our paid
inventory by 3.2%, and 71% of the employers it returned were new to Sweep
entirely. Adzuna is looking at a **different slice of the Indian market** — more
mid-market and staffing-led, less global-tech — and it costs nothing.

The decisive nuance is that Adzuna's weak yield is **not an inventory problem**.
Location targeting is near-perfect, freshness matches LinkedIn, salary disclosure
beats it, and six of eight fields are complete. What suppresses it is a 500-char
description cap that strips the text `score_job` exists to read: median matched
skills 1 against LinkedIn's 6, 47% of rows matching nothing, and a hard ceiling
of 39 where LinkedIn produced 63 rows at 40+.

---

## Recommended next step — one cheap probe, not a build

**Does `redirect_url` yield a fetchable full JD?** If it does, the sensitivity
estimate says Adzuna goes from 35 useful rows to roughly 230 at zero marginal
cost, and it becomes a serious complementary source. If it does not — redirect
chains, interstitials, or anti-bot — Adzuna stays a thin discovery signal worth
running for employer names alone.

This is one HTTP request against a handful of cached `redirect_url` values. It is
**not verified** and nothing above depends on it. Sweep already does exactly this
shape of per-job JD fetch in `sources/optum.py` and `sources/enterprise.py`, so
if it works there is a proven pattern to copy.

**Do not build the provider seam yet.** On today's measurement Adzuna would add
523 rows of which 35 are useful. The probe above decides whether that number is
35 or 230, and that is the difference between a footnote and a source.

---

## Reproducing this

```bash
ADZUNA_APP_ID / ADZUNA_APP_KEY in .env
JOB_PROFILE=kartik_reachable .venv/bin/python -m bench.adzuna_replay \
    --profile kartik_reachable --cache <dir> --out <results.json>
```

Raw responses are cached per query, so every figure above is re-derivable
offline at zero quota.
