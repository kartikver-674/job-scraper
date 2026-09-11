# Adzuna — Final Position

Closes the Adzuna line of work opened by
[the Apify independence audit](2026-09-11-apify-independence-audit.md). Three
measurements, one conclusion. No new measurement here.

| | |
|---|---|
| [Phase 1 replay](2026-09-11-adzuna-phase1-results.md) | 64 combos → 56 API calls, 523 postings, 338 employers |
| [`redirect_url` probe](2026-09-11-adzuna-jd-probe.md) | 36 fetches, two sets |
| [ATS harvest probe](2026-09-11-ats-harvest-probe.md) | 329 companies, ~2,600 requests |

---

## The position

**1. Adzuna's API is a real, independent, zero-terms-risk employer-discovery
signal.** 56 calls returned 338 employers, of which **239 (71%) had never been
seen through any source** — paid or free — and only 9 overlapped what we already
fetch for free. Location targeting was near-perfect (1 mismatch in 1,174 rows),
freshness matched LinkedIn's distribution, and salary disclosure beat it (8.0%
against 5.7%, with zero predicted values). It is a documented API with a free
tier used inside its quota. There is nothing to defend here.

**2. Adzuna cannot substitute for LinkedIn as a job-retrieval source.** Of
LinkedIn's 243 useful postings for the same searches, Adzuna returned **11
(4.5%)** and scored **none** of them at threshold. It reaches a different slice
of the Indian market — mid-market and staffing-led rather than global-tech — and
that is its value; it is not the same inventory arriving more cheaply.

**3. The 500-character snippet is a real handicap, confirmed twice, and not
fixable at volume through either door tried.**

| door | uplift from a full JD | why it closed |
|---|---|---|
| `adzuna.in` page | median **+11**, skills 1 → 7, useful 0 → 6 of 11 | 100% of `redirect_url`s stay on `adzuna.in`; the site answered `429, Retry-After: 3600` at 72 fetches. A sweep needs ~523. |
| employer's own ATS board | median **+13** (b2) / **+14** (b3), skills → 5–7 | reaches **9 of 523 postings**; 11% of companies resolve, a third of those carry the posting, 39% of resolutions land on description-less platforms |

The mechanism is not in doubt — two independent sources agree a full JD is worth
+11 to +14 points and roughly 6× the matched skills. What is settled is that
there is no route to it at sweep volume. **Stop spending on this hypothesis.**

---

## What Adzuna is for, going forward

**A periodic company-name generator feeding `harvest_ats.py`. Nothing more.**

Its output that survives all three measurements is a list of employer names. Those
names are worth more as ATS-harvest candidates than as scoreable job rows — this
step promoted 36 boards found exactly that way, carrying 2,229 live postings.

**This means Adzuna does NOT need a `JobSource` / search-provider wrapper under
the audit's §3 abstraction.** A search provider exists to return scoreable rows
into a sweep, and Adzuna's rows are not scoreable — 47% match zero skills and the
ceiling is 39 where LinkedIn produced 63 rows above 40. Wrapping it as one would
add a provider that costs quota per search and contributes almost nothing per row.

What it needs instead is **a company-list export, run occasionally by hand or on a
schedule** — a few dozen API calls, a list of names, piped into the harvester that
already exists. That is a much smaller thing than a provider seam, and it is not
built as part of this step. Whether to schedule it at all is a separate decision.

The audit's Phase 2 (the provider seam) and Phase 3 (an aggregator as a
first-class provider) should be re-read in that light: **Phase 3's premise did not
survive contact with the data.**

---

## Guardrail for future work — empty fields must never overwrite populated ones

**Two of the five platforms in `sources/ats.py` map no `Description` field at
all**: SmartRecruiters and Breezy. Their postings are scored on the title alone.
That is a known, accepted limitation of those adapters.

It became a *measured* hazard in the ATS harvest probe. Replacing an Adzuna
snippet with the SmartRecruiters row for the same posting made two rows score
**worse** than before:

| Adzuna snippet | SmartRecruiters | posting |
|---:|---:|---|
| 16 | **6** | Version 1 — AI Full-Stack Engineer |
| −2 | **−4** | Version 1 — Senior AI Backend Engineer |

Today this is harmless. `scraper.dedupe()` sorts best-first and keeps the winner
whole, discarding the loser entirely — no field from one row ever lands on
another.

**If that is ever replaced by a merge-style consolidation** — taking the best
field from each source for the same posting, which is a natural thing to want
once several sources carry the same job — then it **must never let an empty field
from one source overwrite a populated field from another.** An empty
`Description` from SmartRecruiters is "this adapter does not carry descriptions",
not "this job has no description", and the same is true of `Salary`, `Experience`
and `Posted Date` across most adapters. The correct rule is that a populated
field always wins over an empty one regardless of which row ranked higher.

Flagged here so it is designed for rather than rediscovered as a production bug.

---

## What stays open

The audit's Target 2 — ATS harvesting at scale across the 2,618-company backlog —
is the one thread this work strengthened rather than closed. bucket2 resolved at
**16.9%** and bucket3 at **9.1%**, against the repo's prior 8.2% baseline. The
already-known global employers resolve at roughly twice the rate of the
India-heavy long tail, which is the first evidence we have that the backlog is
not uniform and that ordering it by employer profile would pay.

Workable (Target 3) showed **one signal in ~293 unresolved companies**. That does
not justify the field-path verification work, and it should drop down the list.
