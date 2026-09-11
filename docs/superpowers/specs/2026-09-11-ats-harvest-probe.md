# Probe — Can `harvest_ats.py` Deliver the JD That `adzuna.in` Would Not?

**Answer: the uplift is real and the right size. The reach is negligible.**
Across 329 companies and 523 Adzuna postings, the ATS path delivered a full JD
for **9 postings**, and produced **2 additional useful rows**. It does not rescue
the withdrawn 35 → 232 estimate, and nothing here supports a replacement for it.

**Constraints:** read-only, `bench/`-only. **Nothing written to `ATS_BOARDS` or
any production config.** No Apify call, no Gemini call, no provider seam. Probing
used `harvest_ats.probe()` and `harvest_ats.slugs()` unmodified, at their own
0.3s delay and `retries=0`; no 4xx was retried or forced.

Company lists reconstructed from the Phase 1 artifacts through the replay's exact
pipeline — `score_job`, then best-per-`job_key` — reproducing **9 / 90 / 239
across 338 companies and 523 postings** before any probing. No Adzuna call.

---

## The hypothesis, and why resolution rate alone cannot answer it

The `redirect_url` probe measured a median **+11** score gain when a full JD
replaced Adzuna's 500-char snippet (0 → 6 of 11 useful), and found the only door
was scraping `adzuna.in`. A public ATS board would be the same uplift for free.

But a resolved board only helps if it carries **the specific posting** Adzuna
surfaced. So this measures a funnel, not a rate:

| | bucket2 (90) | bucket3 (239) |
|---|---:|---:|
| companies probed | 90 | 239 |
| **board resolved** | 15 (16.7%) | 21 (8.8%) |
| **board carries the posting** | 5 (5.6%) | 8 (3.3%) |
| …on a platform that has descriptions | 4 | 5 |
| **postings actually re-scoreable** | **4** | **5** |

Two thirds of resolutions are lost before a JD is reached.

---

## (6) Resolution rates, against the repo's own baseline

Staffing companies excluded (see below). Baseline in `config.py`: **5 of 61
Indian employers, 8.2%**.

| set | employers | resolved | rate | vs baseline |
|---|---:|---:|---:|---|
| **bucket2** — previously paid-only | 89 | 15 | **16.9%** | ~2× |
| **bucket3** — new to Sweep | 230 | 21 | **9.1%** | ~the same |

**The two buckets do diverge, and the direction makes sense.** bucket2 is
already-known, more global and more established — employers LinkedIn had surfaced
before — and they run public ATS boards at roughly twice the rate. bucket3 is the
India-heavy long tail Adzuna found, and it resolves at essentially the repo's
existing 8.2%. The earlier finding that Indian employers mostly do not expose a
public ATS **replicates on a sample five times larger**.

This is a 329-company sample. It is not extrapolated to the 2,618-company
backlog, and these numbers should not be read as if it were.

---

## (3) Staffing agencies — and a rule of mine that failed

**0 of 10 staffing companies resolved (0%).** bucket2 1, bucket3 9. They are
excluded from every employer rate above.

| tag | bucket2 | bucket3 |
|---|---:|---:|
| `named` (from the brief) | 1 | 5 |
| `pattern_strong` (staffing/recruit/manpower/talent/outsourc…) | 0 | 4 |
| `pattern_weak` (consult/services/solutions) | 5 | 25 |

**I am discarding `pattern_weak` rather than reporting it.** It tagged Tata
Consultancy Services, Colt Technology Services, Evernorth Health Services,
Hitachi Solutions India, Quantiphi Analytics and Dentsu Global Services — all
direct employers hiring their own staff. A rule that calls TCS a staffing agency
is measuring the word "Services", not the business model. Only `named` and
`pattern_strong` are used; the full tag list is in the probe output so the rule
can be audited rather than trusted.

The strong-tier catches are genuine: Erekrut, Livec Staffing Services, Shree
Mahakal Outsourcing, Talent Aspire — plus the named set (Anlage ×2, Artech,
BCforward, Aditi Tech Consulting, Acme Services).

---

## (4)(5) The postings that did reach a JD, re-scored

### bucket2 — 4 postings, all Greenhouse

| Adzuna | ATS | skills | JD chars | posting |
|---:|---:|---|---:|---|
| 4 | **18** | 2 → 6 | 458 → 6,408 | Tower Research Capital — Software Engineer |
| 0 | **13** | 0 → 6 | 500 → 4,242 | Tower Research Capital — Software Engineer II |
| −4 | **9** | 0 → 4 | 500 → 9,652 | Tide — Senior SWE, Flutter |
| 0 | **2** | 1 → 4 | 500 → 3,930 | NICE — Software Engineer, CX |

median score **0 → 11 (+13)** · skills **0.5 → 5** · **useful 0 → 0**

### bucket3 — 5 postings, Greenhouse

| Adzuna | ATS | skills | JD chars | posting |
|---:|---:|---|---:|---|
| 2 | **57** | 1 → 20 | 500 → 3,690 | Capco — AI Engineer, Agentic AI |
| 0 | **36** | 0 → 12 | 500 → 9,667 | Strata Information Group — Full Stack Engineer |
| 2 | 15 | 1 → 7 | 500 → 2,642 | Capco — Gen AI Engineer |
| 0 | 14 | 0 → 7 | 500 → 2,810 | Capco — AI Engineer |
| 6 | 14 | 0 → 2 | 500 → 2,048 | Capco — ML Engineer (Full Stack) |

median score **2 → 15 (+14)** · skills **0 → 7** · **useful 0 → 2**

### Set against the `redirect_url` probe

| | score gain (median) | skills | useful |
|---|---:|---|---|
| `redirect_url`, shared set | **+11** | 1 → 7 | 0 → 6 of 11 |
| ATS board, bucket2 | **+13** | 0.5 → 5 | 0 → 0 of 4 |
| ATS board, bucket3 | **+14** | 0 → 7 | 0 → 2 of 5 |

**The uplift replicates.** A full JD is worth +11 to +14 points and roughly 6×
the matched skills, and it does not matter whether it came from `adzuna.in` or
from the employer's own Greenhouse board. The truncation hypothesis is confirmed
twice, independently.

What does not replicate is the **denominator**. The `redirect_url` probe could
reach a JD for any Adzuna row it tried; the ATS path reached 9 of 523.

### Two effects that cut against the uplift

**44% of postings on a JD-bearing board were hard-dropped.** 7 of 16 — the stated
experience floor fires once the full text is visible (Capco ×3, Tekion, Gradera,
WPP, Bitwarden). Correct behaviour, and the same ~28–44% pattern the
`redirect_url` probe found. A full JD deletes rows as well as raising them.

**Two of the five platforms in `ats.ATS` carry no description at all.** Breezy
and SmartRecruiters map no `Description` field, so a resolution there is *zero*
JD uplift by construction — and worse than zero if the snippet is replaced:

| Adzuna | ATS | posting |
|---:|---:|---|
| 16 | **6** | Version 1 — AI Full-Stack Engineer |
| −2 | **−4** | Version 1 — Senior AI Backend Engineer |

**14 of the 36 boards resolved are SmartRecruiters or Breezy** — 39% of the
harvest is on platforms that cannot answer the question this probe was asking.

---

## (7) Workable signal — noted, not acted on

**Exactly 1 company of ~293 unresolved showed a Workable board: LUXASIA (168
jobs), bucket2.**

Probed as a bare existence check only, excluded from every resolution rate above,
and no field paths were verified or wired up.

**This argues against prioritising Target 3.** One signal in 293 does not justify
the field-path verification work `sources/ats.py` records as the reason Workable
was left out. Adding it would very likely have moved the resolution rates by less
than one company.

---

## The durable asset, which is a different question

Separately from the JD hypothesis: **36 employer boards resolved, carrying 2,228
postings today**, 1,559 of them on JD-bearing platforms. That is 36 employers
Sweep could fetch free and forever, and it is worth more than the 13 postings
that happened to overlap this one Adzuna replay — a board keeps returning new
openings.

That is Target 2 from the audit, and this measurement supports it. It is not what
this probe was testing, and it should not be promoted into `ATS_BOARDS` on the
strength of a measurement aimed at something else. Promotion is its own step,
with its own India-count check.

---

## Plain statement

**No. The harvest path does not recover meaningful JD-enrichment value at the
scale the withdrawn estimate implied.**

The mechanism works — +13 and +14 median gains, matching the `redirect_url`
probe's +11 — but it applies to **9 of 523 postings**, and yields **2 additional
useful rows** across 329 companies. The funnel loses an order of magnitude at each
stage: 11% of companies resolve, a third of those carry the posting, and 39% of
resolutions land on platforms with no description.

The honest summary of both probes together: **Adzuna's 500-char snippet is a real
and confirmed handicap, and there is no route to fixing it at sweep volume.**
`adzuna.in` rate-limits; the ATS path reaches under 2% of rows. Adzuna's value
stays exactly where Phase 1 left it — **employer discovery, from the API, at zero
terms risk** — and the 239 new companies it named are worth more as ATS-harvest
candidates than as scoreable job rows.

---

## Reproducing

```bash
JOB_PROFILE=kartik_reachable .venv/bin/python -m bench.ats_harvest_probe \
    --buckets <buckets.json> --adzuna-cache <replay cache> \
    --probe-cache <cache.json> --out <results.json>
python -m bench.ats_harvest_probe --demo      # offline
```

`--probe-cache` makes re-analysis free; the run is ~2,600 requests at 0.3s.
