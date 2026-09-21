# Confirmed experience mismatch guard — audit, measurement, and a default-off guard

Traced and measured at `7f557ba`. Nothing deployed. `c9c7ef1` untouched, query
generation untouched, Profile Engine V3 untouched.

The reported case: **Hargun Singh, 3 years 1 month of relevant experience,
received postings requiring 6+ years.**

---

## Part 1 — Audit of the current experience pipeline

### The trace, end to end

```
résumé
  → local_extract.months_from(employment rows)          months, integer
  → local_extract.read()                                experience_months = months
                                                        years_experience  = months // 12
  → sweep/logic.with_experience()                       review screen overrides both
  → auto-apply/make_profile.render()                    SEARCH.experience_years   = years
                                                        SETTINGS.max_experience_years = years + 3
                                                        (experience_months is DISCARDED here)
JD
  → scraper._required_experience_floor(text)            one integer, max() across figures
  → scraper.score_job()                                 floor > max_experience_years → drop
  → scraper.to_output()                                 experience_required = f"{floor}+"
  → sweep/logic._stated_years()                         leading int, for the "least experience" sort
  → merge_jobs.applyable()                              re-applies the same > comparison to stored rows
```

### The eight questions

**1. Where does candidate total experience come from?**
`local_extract.months_from()` ([local_extract.py:373](local_extract.py#L373)) —
merged, non-overlapping spans over the employment rows the router kept, filtered
by `is_professional` (no internships) and `is_relevant` (clause 3, the targeted
profession). The model is never asked for the number; it supplies rows and one
boolean, Python does the arithmetic. On the review screen
`sweep.logic.with_experience()` lets the user overwrite it with years + months.

**2. Is it months or floating years internally?**
**Months** — and then it is thrown away. `local_extract.read()`
([local_extract.py:997](local_extract.py#L997)) keeps both
`experience_months` (exact) and `years_experience = months // 12` (floored).
Only the floored integer reaches `make_profile.render()`, so everything
downstream of profile generation sees 3, not 3.08. The comment beside it says
the remainder is "for display only".

**3. Is it confidence-rated?**
Not as a number that travels. `local_extract.route()` returns
accept / corrected / escalate, and `check_years` records when rows had
unreadable dates ("a floor, not a measurement") — but none of that is rendered
into the profile. By the time scoring runs, the figure is an unqualified int.

**4. Where does a result such as "6+" come from?**
`scraper.to_output()` ([scraper.py:1194](scraper.py#L1194)) formats
`row["years_required"]`, set in `score_job` from
`_required_experience_floor(text)`. The `+` is appended unconditionally, so the
column reads "6+" whether the JD said "6+", "6-8", "up to 6", "6 preferred", or
"our team has 6 years of combined experience". Only when nothing parses does it
fall through to the raw source field `row["Experience"]`.

**5. Is it parsed from the JD or generated heuristically?**
Parsed, by regex, from `title + description + the source's own Experience
field`, all lowercased. Two passes: `YEARS_PATTERN` figures that survive four
context tests (not "ago"/"old"/"in business", not a company-age phrase before,
not an education word before an experience word, and an experience cue nearby),
then labelled header fields (`"<label> : 10+ years"`). Several figures are
combined with `SETTINGS["experience_aggregate"]`, default **`max`**.

**6. Is it used in ranking?**
Only as a user-selectable sort ("Least experience first",
[sweep/logic.py:438](sweep/logic.py#L438)). It contributes **nothing** to
`score`. No penalty of any kind exists for an experience shortfall today.

**7. Is it used in filtering?**
Yes, in exactly one comparison, in two places:
`floor > SETTINGS["max_experience_years"]` — [scraper.py:646](scraper.py#L646)
at scrape time and [merge_jobs.py:74](merge_jobs.py#L74) at merge time. Gated on
`SETTINGS["drop_excluded"]`.

**8. Why can a 3.1-year candidate receive a confirmed 6+ job?**

Three independent reasons, and all three have to be fixed to close the case:

| # | cause | evidence |
|---|---|---|
| A | `max_experience_years` is rendered as **`years + 3`** | [make_profile.py:1282](auto-apply/make_profile.py#L1282); Hargun's rendered profile has `experience_years: 3`, `max_experience_years: 6` |
| B | the comparison is a strict `>`, so a floor of exactly 6 is **kept** | `floor > 6` is False for 6 |
| C | candidate months are discarded, so 3y1m and 3y11m are both "3" | `years_experience = months // 12` |

A + B together are the whole defect. Nothing in the pipeline was broken; the
threshold was simply three years wider than the page implies, and it is an
open interval on top of that.

**Confirmed: the displayed Experience column is NOT a separate field.** It is
the same `years_required` the gate uses. But it is *unqualified* — the column
and the gate both treat a preference, a ceiling and a brochure line as a
requirement (measured below: 17 of 37 parsed figures in the phrasing set are
not a confirmed overall minimum).

---

## Part 2 — Critical JD extraction audit

### How multiple experience statements are handled today

They are aggregated to a single integer by `min()` or `max()`
([scraper.py:465](scraper.py#L465)), default `max`. **No type is assigned.** The
current extractor cannot distinguish any of the six categories in the brief:

| category | handled today? | what happens |
|---|---|---|
| overall minimum | — | becomes the number, if it happens to be the largest |
| skill-specific | **no** | competes with the overall figure on equal terms; wins under `max` |
| preferred | **no** | read as a hard requirement |
| range | partly | `YEARS_PATTERN` reads the lower bound, so "3-6" → 3 ✓ |
| maximum ("up to 6") | **no** | read as a minimum of 6 |
| irrelevant numbers | mostly | degree / company-age / age are excluded; team size and salary never match the pattern |

`max` is right for the case it was tuned on ("8+ years total … 2+ years AI/ML"
→ 8) and **wrong in the mirror case** ("3+ years total … 6+ years Salesforce"
→ 6). Measured: the current parser reports **6** for that sentence. That is the
single most dangerous input for a hard drop, and it is why this guard does not
reuse the number.

### What the guard does instead

[experience_guard.py](experience_guard.py) reads the JD itself and classifies
every years figure into `{required, range, preferred, maximum, ambiguous,
not_experience}` × `{overall, skill_specific}` × `{high, low}` confidence.

Scope is resolved by walking the words after the figure until the clause ends.
Generic words (`total`, `overall`, `professional`, `relevant`, `industry`,
`software`, `development`, `engineering`, `business`, `analysis`, `experience`,
…) keep it **overall**; the first word naming something in particular (`react`,
`salesforce`, `java`) makes it **skill_specific**, which can never drop a job.
So:

```
"5+ years of software development experience, including 1+ years React"
     overall 5                                       skill_specific 1   → 5
"3+ years of total experience, 6+ years of Salesforce experience"
     overall 3                       skill_specific 6                   → 3
"Experience: 6+ years overall, 3+ years Salesforce"
                overall 6            skill_specific 3                   → 6
```

Aggregation is **`min` across confirmed overall statements** — deliberately the
opposite of `experience_aggregate="max"`. That setting feeds a displayed number
and a sort order, where over-reading is cheap. This feeds a deletion, where
over-reading removes a job the person could have had.

A figure is **confirmed** only when it is overall-scoped *and* carries a
commitment: an explicit requirement cue (`minimum`, `at least`, `must have`,
`required`), the `+` that means "and up", or a range. A bare "6 years of
experience" is left at low confidence and acts on nothing.

---

## Part 3 — Measurement

### 3a. A limitation that shapes everything below

**No job description text is persisted anywhere in this repo.** `to_output()`
writes 24 columns and `Description` is not one of them. All 22,530 stored rows
carry the already-parsed figure (`"6+"`) and no prose. Verified by scanning
every `.json` and `.csv` under `output/` and `bench/` for a description-shaped
key: zero hits.

So the guard's extractor cannot be replayed against real postings offline. The
measurement is therefore in two halves, and the second half is what turns the
first into a real number.

### 3b. Real corpus, policy arithmetic (upper bound)

Every stored row, per persona, using each profile's own
`SEARCH["experience_years"]`. These rows have **already survived** the existing
`years + 3` gate, so every hard drop counted here is incremental.

```
total rows scanned                         22,530   (100 CSV files, output/, archive excluded)
rows with a parseable experience cell      13,231
  of which numeric (a parsed floor)        12,651   (56.2% of all rows)
  non-numeric raw source text                 580   ("Senior", "Full Time", "Any", …)
rows with no experience cell                9,299
```

Persona-matched, over the 36 generated profiles whose output survives:

| candidate | parsed rows | none | penalty | hard drop (upper bound) | drop % |
|---|---|---|---|---|---|
| 1 year (2 personas) | 16 | 1 | 4 | 11 | 68.8% |
| 2 years (19 personas) | 552 | 89 | 76 | 387 | 70.1% |
| 4 years (13 personas) | 287 | 123 | 124 | 40 | 13.9% |
| 2 years, `kartik_reachable` (control) | 1,436 | 986 | 450 | **0** | 0.0% |
| **all personas** | **2,291** | **1,199** | **654** | **438** | **19.1%** |

`kartik_reachable` is the control: its config leaves `max_experience_years` at
the default 3, so nothing above 3 ever reached disk, and the guard finds nothing
to drop. Correct behaviour, and confirmation that the 438 are all rows a *wide*
gate let through.

Upper-bound hard drops by candidate and job minimum:

| candidate | job min | rows |
|---|---|---|
| 1 | 3 | 5 |
| 1 | 4 | 6 |
| 2 | 4 | 86 |
| 2 | 5 | 301 |
| 4 | 6 | 15 |
| 4 | 7 | 25 |

Of the 438, **208 have a title carrying no seniority word at all** ("Software
Engineer", "UI Developer", "Android Developer", "Salesforce Developer") and 230
do ("Senior …", "Lead …"). That split matters: it is direct evidence that a
title-based rule would have caught barely half of these and would have caught
them for the wrong reason. The guard reads no titles.

### 3c. Extractor accuracy, labelled phrasings

44 labelled JD sentences — the brief's taxonomy plus the live templates
`scraper.py`'s own comments record (Accenture's "Minimum 5 Year(s) Of
Experience", Netradyne's "Business Systems Group : 10+ years", the
founded-N-years-ago case). Stored at
[auto-apply/tests/fixtures/experience_phrasings.py](auto-apply/tests/fixtures/experience_phrasings.py).

```
phrasings                44
exact                    42   (95.5%)
wrong                     2
  over-read (unsafe)      0
  under-read (fail open)  2
```

Both misses are under-reads — the harmless direction — and both are the same
shape: a real requirement the guard refuses to commit to.

* *"Founded 6 years ago, we now have 3+ years of experience shipping ML"* —
  "shipping ML" narrows the 3, so it scores as skill-specific.
* *"In business 12 years. Seeking 3 years of experience"* — "seeking" is not a
  requirement cue and there is no `+`, so the 3 stays at low confidence.

**Zero over-reads means zero false-positive hard drops on the labelled set.**

### 3d. Confirmation rate — turning the upper bound into an estimate

The same 44 phrasings, current parser vs guard:

| | guard confirms | guard declines |
|---|---|---|
| **parser finds a floor** | 20 | 17 |
| **parser finds nothing** | 0 | 7 |

The parser reports a floor on 37 of 44; the guard confirms an overall minimum on
20. **Confirmation rate: 54% of parsed figures.** Of the 17 declines, 15 are the guard correctly
refusing to act — 5 skill-specific, 5 preferred, 2 ceilings, 2 ambiguous, 1
labelled header field — and 2 are the under-reads above.

Applying 54% to the persona measurement:

| | upper bound | estimated real |
|---|---|---|
| hard drops across all personas | 438 (19.1% of parsed) | **≈237 (≈10.3% of parsed)** |
| hard drops, Hargun-shaped candidate (3y1m) | 6.3% of the whole corpus | **≈3.4%** |

### 3e. False-positive review, by candidate band

Whole corpus (12,651 floors), one candidate at a time. This corpus was scraped
for 2–4 year candidates, so the junior bands are reading a senior-skewed market
— the percentages are about the corpus, not about the rule.

| candidate | none | penalty | drop (ub) | drop % | drop (est, ×0.54) |
|---|---|---|---|---|---|
| 0y0m | 357 | 1,615 | 10,679 | 84.4% | 45.6% |
| 0y6m | 1,972 | 3,033 | 7,646 | 60.4% | 32.6% |
| 1y6m | 5,005 | 3,232 | 4,414 | 34.9% | 18.8% |
| 2y0m | 5,005 | 3,232 | 4,414 | 34.9% | 18.8% |
| 2y11m | 8,237 | 899 | 3,515 | 27.8% | 15.0% |
| **3y1m (Hargun)** | **9,136** | **2,717** | **798** | **6.3%** | **3.4%** |
| 4y0m | 9,136 | 2,717 | 798 | 6.3% | 3.4% |
| 6y0m | 12,247 | 169 | 235 | 1.9% | 1.0% |
| 10y0m | 12,591 | 0 | 60 | 0.5% | 0.3% |

Three findings, in order of how much they matter:

1. **0–1 year candidates are the risk band.** At 0 months every confirmed 2+
   bar is a 2.0-year gap, and 84% of this corpus clears 2. The arithmetic is
   correct — a fresher genuinely cannot meet a confirmed 2-year minimum — but
   no fresher persona has ever been measured end to end here, and a rule that
   removes four fifths of a page needs to be watched on a real one before it is
   trusted. **This is a release condition, not a blocker.**
2. **The 2y0m band contains 86 rows at exactly gap 2.0** (floor 4). Every one of
   them is a boundary decision, and a candidate recorded as "2 years" may hold
   anything from 2y0m to 2y11m. This is why the guard **refuses to run without
   `candidate_experience_months`** and why `make_profile` now renders `None`
   rather than `years * 12` when the remainder is unknown. With months, a 2y11m
   candidate against a 4-year bar is a 1.08-year gap — a penalty, not a drop.
3. **Senior candidates are essentially untouched** (0.3–1.0%), as they should
   be: the guard only ever looks downward.

Per-row JD evidence cannot be listed for the stored drops, for the reason in
3a — the postings' prose is gone. Evidence *is* emitted per decision at runtime
(`verdict["evidence"]`, the 90-character window around the matched figure) and
is what `experience_guard.summary()` prints.

---

## Part 4 — Hargun regression

Candidate: **37 months (3 years 1 month)**, from
`output/v3-hargun-regression/profile_rendered.py`
(`experience_years: 3`, `max_experience_years: 6`).

Every case from the brief, measured:

| JD statement | required | guard verdict | gap | action | matches brief |
|---|---|---|---|---|---|
| `6+ years of professional experience` | 6 | confirmed, overall | 2.92 | **hard_drop** | ✓ removed |
| `Minimum 6 years of experience required` | 6 | confirmed, overall | 2.92 | **hard_drop** | ✓ removed |
| `7+ years of industry experience` | 7 | confirmed, overall | 3.92 | **hard_drop** | ✓ removed |
| `8+ years of total experience` | 8 | confirmed, overall | 4.92 | **hard_drop** | ✓ removed |
| `4+ years of experience` | 4 | confirmed, overall | 0.92 | none | ✓ retained |
| `5+ years of software development experience` | 5 | confirmed, overall | 1.92 | **penalty** | ✓ retained, penalised |
| `6+ years of experience preferred` | — | preferred | — | none | ✓ retained |
| `The role involves 6 years of experience` | — | ambiguous (no `+`, no cue) | — | none | ✓ retained |
| `3-6 years of experience` | 3 | confirmed range, floor 3 | −0.08 | none | ✓ retained |
| `5+ years total dev experience and 1+ years React` | 5 | overall 5, skill 1 | 1.92 | penalty | ✓ never 1 |
| `3+ years total, 6+ years Salesforce` | 3 | overall 3, skill 6 | −0.08 | none | ✓ never 6 |

Over the full 44-phrasing set at 37 months: **11 hard drops, 4 penalties, 29 no
action.** Every one of the 29 is a deliberate refusal.

The wiring is regression-tested against Hargun's exact config
(`max_experience_years = 6`, `candidate_experience_months = 37`): with the flag
off `score_job` keeps the 6+ posting, with the flag on it drops it and records
why.

---

## Part 5 — What was implemented

One new module, three small edits, all inert while the flag is off.

**[experience_guard.py](experience_guard.py)** — the whole rule.

```python
assess(jd_text, candidate_months) -> {
    "candidate_years":  3.08,
    "job_min_years":    6,
    "requirement_type": "required",     # required | range | preferred | maximum
                                        # | ambiguous | not_experience
    "confidence":       "high",         # high | low | none
    "gap_years":        2.92,
    "action":           "hard_drop",    # none | penalty | hard_drop
    "evidence":         "we require 6+ years of experience",
}
```

Bands: `gap < 1.0` → none · `1.0 ≤ gap < 2.0` → penalty · `gap ≥ 2.0` →
hard_drop. Acts only on `scope == "overall"` and `confidence == "high"`.

Every fail-open path returns `action: "none"`: no candidate months, no confirmed
statement, unreadable or empty JD, a preference, a ceiling, a skill-specific
figure, a bare uncommitted number, somebody else's combined years. **No title is
ever read.**

**[scraper.py:649](scraper.py#L649)** — one guarded block in `score_job`,
before the existing exclusion check, plus one penalty line beside
`soft_penalty`. With the flag off, `exp_verdict` stays `None` and neither
branch can fire.

**[config.py](config.py)** — `SETTINGS["candidate_experience_months"] = None`
(the guard's off switch for hand-written profiles) and
`SCORING["experience_gap_penalty"] = -8`, sized between `soft_penalty` (−4, an
inflated title) and `drop_penalty` (−15, a title the candidate cannot hold).

**[auto-apply/make_profile.py](auto-apply/make_profile.py)** — renders
`candidate_experience_months` from `data["experience_months"]`, the exact figure
`local_extract` already computes and the review screen already collects. `None`
when absent; never `years * 12`.

Telemetry: `experience_guard.DROPPED` accumulates every non-`none` verdict with
its title, and `summary()` renders them one line each. Not surfaced in the
sweep UI.

### Tests

[auto-apply/tests/test_experience_guard.py](auto-apply/tests/test_experience_guard.py)
— 26 tests, all passing. Every case the brief asked for is there, plus the
multiple-years extraction regression in both directions, the "months not floored
years" case, flag-off byte-identity, and the 44 labelled phrasings themselves,
so the figures in Part 3 cannot rot. `python experience_guard.py` runs a
self-check covering the same ground.

Full suite: **1,033 passed**, 3 pre-existing loader errors (`test_answers`,
`test_apply`, `test_apply_config` — verified identical on a stashed tree).

---

## Part 6 — Release decision

**Recommendation: keep `SWEEP_EXPERIENCE_MISMATCH_GUARD` default-off and run a
canary before enabling. Do not deploy from this commit.**

What the measurement supports:

* the rule is arithmetically correct and conservative by construction — 0
  over-reads in 44 labelled phrasings, 54% of what the current parser would have
  acted on is deliberately declined;
* for the reported candidate shape it removes ≈3.4% of a page and fixes the
  reported defect exactly;
* it is inert while the flag is off, and inert even with the flag on for any
  profile without `candidate_experience_months`.

What it does **not** support, and why the flag stays off:

1. **The extractor has never seen a real JD.** 44 hand-written sentences is a
   taxonomy check, not a hit rate. The real confirmation rate and the real
   false-positive rate on live prose are unknown, and unknowable from this repo
   because descriptions are not persisted.
2. **The 0–1 year band is unmeasured end to end** and is where the rule bites
   hardest (up to 45% estimated).
3. **The guard is strictly stricter than `max_experience_years`.** `gap ≥ 2`
   is `floor ≥ years + 2`; the existing gate is `floor > years + 3`. Enabling
   the guard silently narrows a threshold two profile-generations of users were
   tuned against. That is the intent, but it is a product decision, not a bug
   fix, and it should be taken deliberately.

Conditions to enable:

* **C1.** Persist `Description` (or the first ~2,000 characters of it) for one
  paid sweep, replay the guard over it, and report the real confirmation rate
  and a hand-reviewed sample of 30 hard drops. This is the one measurement that
  is missing, and nothing else substitutes for it.
* **C2.** Run one fresher (0–1 year) persona end to end and read the page.
* **C3.** Decide explicitly whether `max_experience_years = years + 3` should
  narrow now that a typed guard exists. Leaving both is defensible — the guard
  is evidence-gated and the old gate is the backstop — but they should not drift
  apart unnoticed.

Two things worth fixing separately, both surfaced by this audit and both out of
scope here:

* the **Experience column over-reads**: it renders `"{n}+"` for preferences,
  ceilings, ranges and brochure lines alike. 17 of 37 parsed figures in the
  phrasing set are not a confirmed overall minimum. `experience_guard.statements()`
  already returns everything the column would need to be honest.
* **`merge_jobs.applyable()` cannot apply the guard** — stored rows have no JD
  text — so a merge of old files will keep rows a fresh sweep would drop. It
  fails open, which is correct, but the two paths are now knowingly asymmetric.
