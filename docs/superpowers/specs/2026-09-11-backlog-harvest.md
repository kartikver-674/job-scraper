# Target 2 at Scale — Does the Apify-Only Backlog Hold Public ATS Boards?

**Measurement only. Nothing promoted into `ATS_BOARDS`.** No Apify call, no
Gemini call, no provider seam, no Workable field-path work.

**Headline: 12.9% ± 3.0 of backlog employers run a public ATS board — above the
repo's 8.2% baseline. Geography predicts it; company size does not. Workable is
finished as a candidate: 0 signals in 424 unresolved companies.**

---

## Coverage — the real denominator

| | |
|---|---:|
| paid-discovered companies in `output/` | 2,653 |
| minus any also seen free → the audit's Bucket E | **2,602** |
| minus the 90 probed last round | 2,512 |
| minus companies already in the 70 `ATS_BOARDS` entries | **2,512** (none overlapped) |
| **actually probed in this run** | **486 (19.3%)** |

The run was stopped on time, not on blocking or quota — at ~8 companies/min the
full 2,512 needed ~5 hours. **486 is the denominator for everything below.**
Nothing is extrapolated to 2,512 beyond the stated confidence interval.

**The sample is stratified-random, not an alphabetical prefix.** The first 61
companies were probed alphabetically before I noticed that starting letter is a
poor sampling frame; the remaining ~425 were drawn round-robin across eight
strata (posting count × ever-posted-outside-India) with a fixed seed, so any
prefix of the run is proportional to the backlog. The checkpoint is JSONL, one
line per finished company, so the switch cost no re-probing and the run can be
resumed to completion at any time.

**Platform health: no platform was disabled.** All five hosts answered normally
throughout — no 429s, no latency spike, no block pattern. The run wrapped
`harvest_ats.get_json` to observe status codes without modifying `probe()`, and
would have disabled any platform after 5 consecutive 429/403s or a rolling
median latency above 6s. Neither trigger fired.

---

## The funnel

| | count | rate |
|---|---:|---:|
| companies covered | 486 | |
| staffing-tagged (excluded from rates) | 4 | **0 resolved** |
| **employers** | **482** | |
| board resolved | **62** | **12.9%** (95% CI ±3.0) |
| resolved **and** carries a posting we have seen | 39 | 8.1% |
| …on a JD-bearing platform | 38 | |

Platforms: greenhouse 21, ashby 18, lever 11, smartrecruiters 10, breezy 2.
Those 62 boards carry **9,193 postings, 854 India-located**.

**The match rate inside a resolved board is far better than last round: 39 of 62
(63%), against 13 of 36 (36%) for the Adzuna set.** That is the expected
direction — a paid sweep and the employer's own board see the same employer in
the same window, whereas Adzuna's mid-market rows often had no counterpart.

Staffing tiers unchanged (`named` + `pattern_strong` only; `pattern_weak` stays
discarded). Only 4 staffing companies appeared in this sample and none resolved,
continuing 0-for-14 across both rounds.

---

## Does employer profile predict resolution?

Proxies taken from existing `output/` data only: how many distinct postings the
repo has ever seen for that company, and whether it has ever posted outside India.

| postings | geography | n | resolved | rate |
|---|---|---:|---:|---:|
| 1 | India-only | 56 | 4 | 7.1% |
| 1 | intl | 56 | 5 | 8.9% |
| 2–4 | India-only | 72 | 11 | 15.3% |
| 2–4 | intl | 80 | 13 | 16.2% |
| 5–9 | India-only | 60 | 10 | 16.7% |
| 5–9 | intl | 57 | 10 | 17.5% |
| **10+** | **India-only** | **55** | **1** | **1.8%** |
| **10+** | **intl** | **46** | **8** | **17.4%** |

**Geography predicts. Size does not.**

- By geography alone: **India-only 10.7%, international 15.1%.**
- By posting count alone: 1 → 8.0%, 2–4 → 15.8%, 5–9 → 17.1%, 10+ → **8.9%**.
  **Not monotonic.** "Bigger employer, more likely to run Greenhouse" is false.

The non-monotonicity resolves in the interaction. The `10+ / India-only` cell is
**1 of 55 (1.8%)** against `10+ / intl` at **8 of 46 (17.4%)** — a tenfold gap in
the cell where the two dimensions disagree. The largest India-only employers are
the Indian IT-services and staffing majors, and they do not rent a
Greenhouse/Lever/Ashby board; they hire through Naukri and custom portals. That
is the same population `config.py` already recorded as unresolvable in 2026-07.

**So the bucket2/bucket3 divergence was real, but the axis was misread.** It
looked like "already-known and established resolves better". It is actually
"*international* resolves better", and bucket2 happened to be the more
international set. Size is a confound, not a cause — and at the top end it
reverses.

**Caveat, stated rather than smoothed:** posting count is a weak size proxy. It
measures how often a company appeared in *our* sweeps, which is a function of our
keywords and locations as much as the employer. The geography signal is the more
trustworthy of the two, and it is the one that survives.

---

## JD re-scoring — and a setup error of mine

**First pass was invalid and I am reporting the corrected result.** I re-scored
every ATS row under the *default* profile, while the paid rows came from a dozen
different profiles' sweeps. A Salesforce role scored 50 under `parul_reachable`
came back at −18 under a full-stack JavaScript config. That measured the
difference between two profiles, not between two JDs. The tell was in the titles:
"Sales Development Representative", "Salesforce Administrator", "Account
Executive, EMEA".

Corrected by re-scoring each posting under the profile that produced its paid
row, restricted to the profiles whose modules still exist (`global_all`,
`kartik_reachable`, default). 47 matched postings, 41 scoreable.

| | paid source | employer's ATS board |
|---|---:|---:|
| median score | 12 | **12** |
| median matched skills | 4 | 2 |
| rows ≥20 | 10 | 8 |
| median JD chars | (full) | 3,840 |

**Median delta: +0.0. 80% of rows land within ±5 points.** Six of 47 (13%) were
hard-dropped by the experience floor.

### Set against the benchmark — below it, and correctly so

| comparison | median delta |
|---|---:|
| `redirect_url` (Adzuna snippet → full JD) | **+11** |
| ATS board, bucket2 (Adzuna snippet → full JD) | **+13** |
| ATS board, bucket3 (Adzuna snippet → full JD) | **+14** |
| **this run (LinkedIn full JD → ATS full JD)** | **+0.0** |

**This lands far below the benchmark range, and that is the right answer rather
than a disappointing one.** The benchmark measured *snippet → full JD*. Adzuna
caps descriptions at 500 characters; LinkedIn does not — `description_max` is
20,000, so a paid row already carries the whole posting. There was no truncation
here to repair, so there was no uplift available. A median of exactly 0 with 80%
of rows within ±5 is two independent sources agreeing about the same job, which
is the best possible outcome for this comparison.

**The practical reading: harvesting the backlog does not make existing paid rows
score better. It makes them free.**

---

## Workable — Target 3 is finished

**0 signals in 424 unresolved companies (0.0%).**

Existence check only, on every company that resolved on none of the five
configured platforms. No field paths verified, no wiring, excluded from every
resolution rate above.

The prior sample found 1 in ~293 on Adzuna's India-skewed discoveries. This one
is larger and drawn from the actual Apify backlog, and found **none**. Between
them: **1 signal in 717 companies.** Target 3 should be closed, not deprioritised
— the field-path verification work `sources/ats.py` records as the reason
Workable was left out cannot pay for itself against that rate.

---

## Plain closing statement

**Yes, further backlog harvesting is worth doing — and yes, the profile-based
ordering hypothesis is real enough to act on, with one correction.**

At 12.9% ± 3.0 over 482 employers, the backlog resolves better than the 8.2%
baseline that made the repo call free ATS expansion "diminishing returns". This
19.3% sample alone found **62 boards carrying 9,193 postings**, against the 36
boards just promoted. The remaining ~2,000 companies plausibly hold a comparable
density, though this run does not measure them.

**Order the remaining backlog by geography, not by size.** An employer that has
ever posted outside India resolves at 15.1% against 10.7%; and in the cell where
the signals conflict — large *and* India-only — resolution collapses to 1.8%,
which is the cheapest 55 companies to skip in the whole sample. Ordering by
posting count alone would have put that worst cell near the front.

Two things this run does **not** support. It does not support a JD-enrichment
argument for harvesting: paid rows already carry full descriptions and re-scoring
them from the employer's board moves nothing (+0.0 median). And it does not
support any claim about the full 2,618 — 486 companies were covered, the run is
resumable from its checkpoint, and finishing it is a separate decision.

---

## Reproducing

```bash
.venv/bin/python -m bench.backlog_harvest \
    --companies <backlog.json> --state <state dir>
python -m bench.backlog_harvest --demo        # offline
```

Resumes from `<state>/checkpoint.jsonl`; re-probes nothing already finished.
