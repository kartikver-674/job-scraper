# V3 Step 6 — a rare skill may suggest a title, not confer a profession

Fixes V3-C5. The orphan pass finds a skill nobody is searching for, asks the
corpus which job titles are posted alongside it, and adds the best-anchored one
as a query. That is useful and stays. What it must not do is manufacture an
occupation.

| | |
|---|---|
| Base | `2c13a88` |
| Step 5 | `2c13a88`, verified unmoved |
| Step 4 | `6e0b095`, verified unmoved |
| Step 3 | `5ddb2e9`, verified unmoved |
| Step 6 flag | `SWEEP_ORPHAN_ROLE_GUARD`, off unless set |

---

## 1. The orphan path, traced before anything was changed

```
own skills
  → local_search.orphans()              a skill with >=15 listings at >=8
                                        employers that no chosen keyword
                                        already covers (share < 10%)
  → candidates_for_skill()              every corpus title posted alongside it,
                                        each guarded: >=8 employers, under
                                        MAX_SHARE, and skill_lift >= 10.0
  → canonical()                         the fragment replaced by a real title
  → worth_it()                          >=20 listings and a relevance score
  → select_detail()                     ranked by anchor_evidence, capped at 2
  → fields_for()["from_orphans"]        and into role_keywords
```

`SKILL_LIFT` participates at exactly one place: a candidate title is discarded
unless the skill is at least ten times as common in that title's listings as in
the market. It is a rarity test on the skill, not a test of whether the
candidate does that job. It is untouched here.

### Three derivations, printed from the development matrix

```
swe_frontend      git → 'software engineer -python developer'  [backend, FORBIDDEN]
                  git → 'flutter developer'                    [mobile, neutral]
                  candidate: frontend developer, junior web developer

swe_reactnative   firebase → 'flutter developer'               [mobile, supported]
                  candidate: senior mobile engineer, mobile developer

swe_backend       git   → 'software engineer -python developer' [backend, supported]
                  redis → 'node.js developer'                   [backend, supported]
                  candidate: backend engineer, software engineer
```

The same orphan term and the same proposed title are correct for one candidate
and wrong for another. Nothing in the path consults who the candidate is.

### Interactions

- **Step 2** supplies the canonical skill identities the orphan pass runs over,
  so a concept is orphaned once rather than once per spelling.
- **Step 3** never sees the orphan provenance; its gate judges the finished
  query string like any other.
- **Step 5** restores held titles, which are never orphan-derived, so the two
  do not overlap.
- **Validation** happens before the orphan pass for corpus keywords, and the
  orphan titles carry their own guards instead. There is no revalidation after
  `canonical()` replaces a fragment, which is V3-C6 and stays out of scope.

### One thing the trace exposed and did not fix

`canonical()` produced `software engineer -python developer`, which is not a
job title anybody posts; the hyphen is a corpus artefact that survived the
fragment-to-title replacement. It reaches `role_keywords` as a literal search
string. **Recorded as V3-C6** — fragment-to-canonical revalidation — and
deliberately not patched here.

## 2. What Step 6 does

`orphan_guard.filter_queries(queries, data, orphan_titles, anchors)` may remove
an orphan-derived query and nothing else. Four conditions must all hold, and
each is a fail-open door:

1. the query came from the orphan path
2. the frozen classifier recognises its role family — **unknown is not
   unrelated**
3. the candidate's role record is not thin
4. that family has no independent support

**Independent** means support that the orphan skill did not supply: Step 3's
work-mode evidence, a grounded held title, or a transition Step 3 already
accepted. Never the corpus association that proposed the title, never
`SKILL_LIFT`, never market frequency. A test asserts the support function's code
mentions none of those.

The provenance needed to explain a decision did not previously survive:
`fields_for` kept the orphan titles but threw away which skill anchored each
one. It now returns `orphan_anchors` alongside `from_orphans`. That is additive,
changes no behaviour, and is asserted against the committed corpus.

### The ablation

| | A legacy | B `any` | C `strong` |
|---|---:|---:|---:|
| orphan queries | 22 | 20 | 20 |
| supported | 10 | 9 | 9 |
| forbidden | 7 | 6 | 6 |
| neutral | 5 | 5 | 5 |
| personas with a forbidden orphan | 7 | 6 | 6 |
| contaminated queries | 38 | 37 | 37 |
| supported precision | 0.551 | 0.551 | 0.551 |
| zero-query | 0 | 0 | 0 |

**B and C reject exactly the same seven titles.** `B` ships, because preferring
the stricter rule when it buys nothing measurable is choosing severity for its
own sake. Both remain available; `SWEEP_ORPHAN_GUARD_MODE` selects between them
and a test asserts they differ on a weak-support fixture, so the ablation is
real rather than two names for one thing.

## 3. Development results, shipped variant

| | before | after |
|---|---:|---:|
| orphan-derived queries | 22 | 20 |
| supported orphan queries | 10 | 9 |
| **forbidden orphan queries** | 7 | **6** |
| neutral orphan queries | 5 | 5 |
| **candidates with a forbidden orphan query** | 7 | **6** |
| supported query precision | 0.551 | 0.551 |
| contaminated queries | 38 | **37** |
| contaminated personas | 20 | 20 |
| primary role coverage | 51/54 | 51/54 |
| technicalization | 4 | 4 |
| zero-query personas | 0 | **0** |
| query count | 381 | 379 |

Seven titles were refused; only two changed the final query count, because
Step 3's gate was already rejecting the other five. The guard and the gate agree
more often than not, which is what you would hope.

### Every rejection, graded

| persona | title refused | family | label |
|---|---|---|---|
| adv_sfba_coordinates_devs | `salesforce developer` | salesforce_dev | **forbidden** |
| tech_support | `inside sales representative` | account_executive | **forbidden** |
| swe_devops | `ai/ml engineer` | ml_engineer | **forbidden** |
| swe_ml | `flutter developer` | mobile | **forbidden** |
| swe_ml | `software engineer -python developer` | backend | neutral |
| sol_consultant | `flutter developer` | mobile | neutral |
| tech_support | `customer success associate` | customer_success | **supported** |

**One rejection is wrong.** A technical-support candidate lost
`customer success associate`, which their labels call plausible. Their role
record carries `support` but not `sales`, and the sixteen families put customer
success under sales. That is a taxonomy boundary, not a guard defect, and it is
the price of the rule. It is one query out of 381.

### What still gets through, and why

Six candidates keep a forbidden orphan title. All are `flutter developer` or the
malformed backend title, for candidates whose record supports
`software_engineering`. The coarse taxonomy has one software family, so it
cannot tell a Java developer that mobile is not their specialism. That is a
limit of the sixteen families rather than of this guard, and fixing it would
mean a finer taxonomy, which is not this step.

### Career switchers

Not measurable here. The one career-switch persona escalates in v2 for an
unrelated reason and produces no queries in either arm, so the guard's
transition path is covered by unit tests only.

## 4. Does the skill survive its title being refused?

Partly, and `orphan_guard.skill_survives` returns the honest answer rather than
a reassuring one. The skill stays in `skills`, keeps its scorer weight, and
still reaches `title_hints`. What is lost is the one corpus-derived **search
string** that skill would have bought.

There is no separate "search this skill as a concept" channel to fall back on:
`role_keywords` are job-title queries by construction. Building one is a change
to query generation and is out of scope.

## 5. Nothing else moved

- **Step 5 restorations identical**: 0 of 54 personas differ.
- **Step 4 title gate identical**: 0 of 56 gates differ, aggregates identical.
- **Step 3**: 0 queries lost that were not orphan-derived, 0 unexpected
  additions. Every one of the two removals is an orphan title.
- **With the flag absent** the module is not consulted.

The diff is four files: `orphan_guard.py` (new), `local_search.py` (+6, the
additive provenance), `auto-apply/local_profile.py` (+22), and the test file.
`SKILL_LIFT`, the rarity calculation and the corpus statistics are untouched.

## 6. Tests

`auto-apply/tests/test_orphan_guard.py` — 29 tests: the ten required shapes,
the no-circular-evidence property asserted against the support function's own
code, the ablation, explainability, and a provenance test against the committed
corpus.

Mutation-tested, and the first pass **found three weak tests of my own**: the
starvation guard was masking both the thin-record check and the
orphan-set-membership check, and the provenance test passed vacuously on a
fixture that produced no orphan. All three are fixed and now fail when the code
is broken.

---

## Status

```
BASE SHA:                       2c13a88
STEP 5 SHA:                     2c13a88   verified unmoved
STEP 6 FLAG:                    SWEEP_ORPHAN_ROLE_GUARD, off unless set
                                SWEEP_ORPHAN_GUARD_MODE selects any|strong

ORPHAN PATH:                    orphans -> candidates_for_skill (SKILL_LIFT>=10)
                                -> canonical -> worth_it -> select_detail
                                -> fields_for["from_orphans"] -> role_keywords
INDEPENDENT SUPPORT SOURCES:    Step 3 work-mode evidence, grounded held
                                titles, a transition Step 3 already accepted.
                                Never the orphan skill, the proposing corpus
                                row, market frequency or SKILL_LIFT.

ORPHAN QUERIES:                 22 -> 20
SUPPORTED ORPHAN QUERIES:       10 -> 9
FORBIDDEN ORPHAN QUERIES:        7 -> 6
CANDIDATES W/ FORBIDDEN ORPHAN:  7 -> 6
SUPPORTED QUERY PRECISION:      0.551 -> 0.551
CONTAMINATED QUERIES:           38 -> 37
CONTAMINATED PERSONAS:          20 -> 20
PRIMARY ROLE COVERAGE:          51/54 -> 51/54
TECHNICALIZATION:                4 -> 4
ZERO QUERY:                      0 -> 0
CAREER SWITCHER:                not measurable on this set; unit-tested only

STEP 3 OUTPUT:                  only attributable orphan changes
STEP 4 TITLE GATE:              identical
STEP 5 RESTORATIONS:            identical
TESTS:                          1,889 across four suites
                                auto-apply 906, sweep 816, bench 125, deploy 42

OLD HOLDOUT USED:               no
READY FOR STEP 6 REVIEW:        yes
```

Recorded for a future batch, not fixed here: **V3-C6**, fragment-to-canonical
revalidation. `canonical()` emitted `software engineer -python developer`, which
no board posts, and it reaches the query list as a literal search string.

Not deployed. The flag is off unless set.
