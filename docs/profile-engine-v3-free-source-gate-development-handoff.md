# V3 Step 4 — a candidate-specific free-source title gate

Fixes V3-C4. The gate that decides which free-board postings a candidate is
even scored on is currently the union of their own hints and a 79-entry generic
software floor, so half of them cannot see their own profession and all of them
can see somebody else's.

| | |
|---|---|
| Base | `062eba3` |
| Step 3 | `5ddb2e9`, frozen and unread as development data |
| Step 4 flag | `SWEEP_CANDIDATE_TITLE_GATE`, off unless set |

---

## 1. The current flow, traced before anything was changed

### Where a candidate's own hints come from

`local_search.hints_for(own, rows, idx, total, seniority, floor=20, ceiling=40)`
at [local_search.py:528](../local_search.py). It takes the ranked corpus
keywords for this person's skills, adds each one's canonical corpus title, then
adds single words that appear in enough listings to be a real title fragment.
It is capped at 40.

**These hints are corpus-derived, not role-derived.** They describe what the
corpus says about the person's skills, and the frozen corpus is 63% software.
A candidate whose skills overlap software vocabulary at all gets software
fragments here before the global floor is even reached.

`local_profile.generate` returns them as the profile's `title_hints`.

### Where the global floor is unioned in

`make_profile._title_gate(data, config)` at
[auto-apply/make_profile.py:831](../auto-apply/make_profile.py):

```python
hints = {str(t).strip().lower() for t in data.get("title_hints") or []}
hints |= set(config.ATS_TITLE_HINTS)          # 79 generic software entries
excludes = {...}                               # NOT unioned; deletes only
return sorted(h for h in hints if h and h not in excludes), sorted(excludes)
```

Called once from `render()` at line 1140. The result is written into the
generated profile as a literal `ATS_TITLE_HINTS = [...]` at line 1241.

**The union is baked in at render time.** It is not recomputed at scrape time,
so the profile file on disk already contains the candidate's hints plus all 79.

### How the baked list becomes the live gate

`config._overlay` at [config.py:944](../config.py) **replaces** list settings
wholesale rather than merging them, and `ATS_TITLE_HINTS` is in `OVERLAYABLE`.
So loading a profile swaps the global list for the profile's already-unioned
one. There is no second union; there does not need to be.

### How matching works

`scraper.is_dev_title(title)` at [scraper.py:215](../scraper.py):

```python
if any(x in low for x in ATS_TITLE_EXCLUDE): return False
return any(h in low for h in ATS_TITLE_HINTS)
```

Plain **substring**, deliberately not word-boundary, because real titles run
words together. Excludes win. `ATS_TITLE_EXCLUDE` is empty by default.

### Which sources consume it

`scraper.fetch_free()` passes `is_dev_title` into
`sources.fetch_free(ATS_BOARDS, FEEDS, ...)`. Every free ATS board and every
free feed is gated by it. Paid and enterprise paths are not.

### What happens when `title_hints` is empty

The union leaves exactly the 79 global entries. **A candidate the model told us
nothing about gets a pure generic-software gate.** This is the worst case and it
is silent.

### What happens under v1 rollback

`_title_gate` never consults the engine version. v1 and v2 both union the floor,
so this defect is engine-version-independent and a rollback does not escape it.

### Does the worker serialise the gate

Yes. `deploy/sweep_worker.py:197` calls `make_profile.render(name, profile,
prefs)`, so the worker re-renders and re-applies the same union. The gate is
frozen into the file it writes.

### Measured consequence, 56 development personas

| | |
|---:|---|
| **52%** | cannot see their own primary family |
| **77%** | have at least one plausible family blocked |
| **100%** | admit at least one forbidden family |
| **196** | forbidden family admissions in total, 3.5 per persona |

---

## 2. What Step 4 does

`title_gate.build(profile_data, base_hints)` returns the same kind of artefact
the gate has always been — lowercase fragments that `scraper.is_dev_title`
substring-matches — built from the candidate instead of from a global list.
`make_profile._title_gate` calls it when `SWEEP_CANDIDATE_TITLE_GATE` is set and
otherwise takes the legacy path unchanged.

**The global floor is not unioned in.** It survives as the last fallback only.

Four inputs, in priority order, each tagged with its provenance in the record:

1. **Grounded held titles**, from the Step 1 role signals. `Salesforce Business
   Analyst` yields that phrase, and also `business analyst` so a plainly worded
   posting still matches. Both ends of a long title are taken: `Executive
   Assistant to the CEO` needs its head, and an earlier revision that took only
   the tail left that person matching `the ceo` and invisible to every
   Executive Assistant posting on the board.
2. **Role-family vocabulary**, for families the candidate is genuinely in —
   **strong** work-mode evidence, or a grounded held title naming that family.
   Weak support is deliberately not enough. This reads Step 3's record and
   changes nothing about how it is built.
3. **The candidate's own corpus-derived hints**, filtered. They come from a
   corpus that is 63% software, so they carry titles from families the person
   has no evidence for. A hint naming a family the candidate is not admitted in
   is dropped; one the frozen classifier cannot place is kept, because unknown
   is not unrelated.
4. **`target_field`**, only when corroborated by the role record or by a held
   title, and only ever to widen.

### Two traps found while building it, both general

**A bare platform name is not a role.** `salesforce` as a gate fragment admits
every Salesforce posting on the board, developer included. That is the
platform-token defect one layer below where Step 3 fixed it. Any hint that is
nothing but platform names is dropped, using Step 3's own frozen platform
vocabulary.

**`sales` is inside `salesforce`.** The matcher is substring by design, so a
bare `sales` fragment admitted Salesforce Developer to every salesperson. No
family vocabulary contains a fragment that is a prefix of an unrelated common
word; the sales family lists whole roles instead.

### Fallback, and it is never silent

| condition | gate |
|---|---|
| any grounded held title **or** admitted family | candidate-specific gate |
| neither, but corpus hints exist | those hints alone, recorded as a fallback |
| nothing candidate-specific at all | legacy global floor, recorded as a fallback |

An earlier revision counted fragments and called three "thin", which sent a
teacher with a perfectly good held title to the software floor. Usable means
**present**, not plentiful.

Every generated profile carries a one-line comment above `ATS_TITLE_HINTS`
naming the supported families and any fallback, so a reader can see where the
gate came from without running anything.

## 3. Development results

56 personas, the audit's own probe fixture of two real board postings per role
family, and the persona matrix's own primary/plausible/forbidden labels. One
persona's primary is `UNCERTAIN`, which names no family and is excluded from the
visibility denominator rather than counted as a failure.

| | before | after |
|---|---|---|
| **primary-family visibility** | 26/55 = **47%** | 54/55 = **98%** |
| software primary | 14/15 = 93% | 15/15 = **100%** |
| technical non-developer primary | 9/21 = 43% | 20/21 = **95%** |
| **non-technical primary** | 3/19 = **16%** | 19/19 = **100%** |
| candidates admitting a forbidden family | 55/55 = 100% | 21/55 = **38%** |
| admitting a forbidden **software** family | 55/55 = 100% | 17/55 = **31%** |
| forbidden family admissions, total | 192 | **66** |
| **title-gate starvation** | 16/55 = **29%** | 0/55 = **0%** |

Fallback fired for **0 of 56**. Every persona had usable candidate evidence.

### The labelled title fixture, both directions

| | before | after |
|---|---:|---:|
| allowed correct | 144 | **247** |
| allowed forbidden | 383 | **128** |
| rejected correct | 238 | 135 |
| rejected forbidden | 331 | **586** |

Correct admissions rise by 72% and forbidden admissions fall by 67%. Correct
rejections — postings the candidate should have seen and now does not — fall
from 238 to 135, so the gate is both more accurate and more permissive about the
right things.

### Where the residual sits, and it is the right place

| persona group | n | before | after |
|---|---:|---:|---:|
| software | 15 | 15 | 14 |
| technical non-developer | 21 | 21 | 10 |
| **non-technical** | 19 | 19 | **4** |

The population the audit measured as worst served is the one that improved
most. What remains among software candidates is mostly one family of software
seeing an adjacent one, which the coarse gate cannot separate and which costs
that person very little. Nine of the seventeen remaining are platform
specialists whose gate admits `Salesforce Developer` through a held title
fragment they genuinely hold.

### One persona still cannot see its own family

`pm_technical` has a primary of `technical_pm`, and its evidence supports
`product` and `software_engineering` rather than `project_delivery`, so neither
admitted vocabulary contains `program manager`. This is a families-vocabulary
gap, not the management hard-drop, and it is reported rather than patched by
adding that fragment to the product family where it does not belong.

### `hard_drop_terms` is still separate

The management and seniority hard-drop remains untouched and out of scope, per
the batch instruction. It did not block any persona's gate here, because it acts
on query generation rather than on title admission.

## 4. Invariance and tests

`auto-apply/tests/test_title_gate.py` — 35 tests: the ten required regression
shapes, every fallback path, the corpus-hint filter in both directions,
`target_field` widening and never narrowing, seven invariance variants
(capitalisation, hyphenation, double spacing, line wrapping, duplicated hints,
reordered hints, repeated calls), explainability, and off-by-default.

Mutation-tested. Unioning the global floor back in, keeping bare platform names,
accepting weak family support, dropping the title-head fragment, letting an
uncorroborated target widen, removing hyphen normalisation, and disabling the
step entirely each turn the suite red.

## 5. Nothing else moved

**Step 3 query output is identical**, verified by running the Step 3 development
comparison with the Step 4 flag on and off: 56 personas, 0 differing query sets,
identical aggregates. The gate is applied in `render`, one stage after the
queries exist, so it cannot reach them.

**With the flag absent, the rendered gate is byte-identical to the legacy
union**, asserted by test rather than claimed.

The diff is three files: `title_gate.py` (new), `auto-apply/make_profile.py`
(+34 −4), and the test file. Nothing on the do-not-touch list was touched.

---

## Status

```
BASE SHA:                        062eba3
STEP 3 SHA:                      5ddb2e9   unchanged, not read as development data
FILES CHANGED:                   title_gate.py                        new
                                 auto-apply/tests/test_title_gate.py  new
                                 auto-apply/make_profile.py           +34 -4

STEP 4 FLAG:                     SWEEP_CANDIDATE_TITLE_GATE, off unless set
OLD GLOBAL ATS FLOOR USED IN V3: no — last-resort fallback only, and recorded
GATE INPUTS:                     grounded held titles; role families with strong
                                 support or a grounded title; candidate corpus
                                 hints filtered by family and platform; target_field
                                 only when corroborated, only widening
FALLBACK:                        own hints alone, then the legacy floor; both
                                 recorded in the profile and in the record

DEVELOPMENT PERSONAS:            56
PRIMARY-FAMILY VISIBILITY:       47% -> 98%
FORBIDDEN-FAMILY ADMISSION:      100% -> 38%   (192 -> 66 admissions)
NON-TECH PRIMARY VISIBILITY:     16% -> 100%
SOFTWARE PRIMARY VISIBILITY:     93% -> 100%
TECH-NONDEV PRIMARY VISIBILITY:  43% -> 95%
CANDIDATES ADMITTING FORBIDDEN SOFTWARE: 100% -> 31%
TITLE-GATE STARVATION:           29% -> 0%
FALLBACK RATE:                   0 / 56

STEP 3 QUERY OUTPUT:             identical
V2 SKILL REPRESENTATION:         identical
TESTS:                           1,822 pass across 4 suites
                                 auto-apply 839, sweep 816, bench 125, deploy 42

OLD STEP 3 HOLDOUT USED FOR DEVELOPMENT:  no
READY FOR STEP 4 REVIEW:         yes
```

Not deployed. The flag is off unless set.
