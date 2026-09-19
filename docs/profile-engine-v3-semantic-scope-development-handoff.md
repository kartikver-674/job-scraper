# V3 Step 8 — a cue governs its own clause item

Fixes V3-C16. `skill_evidence.classify` already separates USED from PLANNED,
LEARNING and NEGATED. Those definitions are untouched. What was wrong is how
far a cue reaches.

| | |
|---|---|
| Base | `b6b1c92` |
| Step 7 | `b6b1c92`, verified unmoved |
| Step 6 | `d2b5c74`, unmoved, stays OFF in the deployment stack |
| Step 5 | `2c13a88`, verified unmoved |
| Step 4 | `6e0b095`, verified unmoved |
| Step 3 | `5ddb2e9`, verified unmoved |
| Step 8 flag | `SWEEP_SEMANTIC_SCOPE`, off unless set |

---

## 1. The pipeline, traced before anything was changed

```
résumé span
  → skill_concepts.compile_alias(...).finditer      concept match
  → _bounds()        section edge, no window may cross it
  → _sentence()      terminators . ; ! ? and hard newlines
  → _unit()          the logical line, soft wraps rejoined
  → _governed()      predicate segment: a break ends a verb's reach ONLY
                     when what follows STARTS A NEW PREDICATE
  → classify()       the occurrence status
  → shape()          evidence depth
  → strength()/tier()  importance
```

Four windows existed. `classify()` used three of them, and **not the one that
understands coordination**:

| cue | window used today | bounded by a clause? |
|---|---|---|
| NEGATED | `_sentence`, both directions | no |
| PLANNED | `_sentence` backwards; next `_CLAUSE_BREAK` forwards | backwards, no |
| LEARNING | `_sentence`, both directions | no |
| USED | `_unit` (the whole logical line) | deliberately not |
| **evidence depth (`shape`)** | **`_governed`** | **yes** |

**`_governed` already encodes the rule Step 8 needs, and the semantic layer
never adopted it.** That is the whole defect in one line. `shape()` has always
known that `Implemented Redis caching, documented Kafka options` is two
predicates while `Built APIs with Node.js, Express and PostgreSQL` is one;
`classify()` did not.

### Five sentences, as the current layer reads them

```
Planning Salesforce migration, gathered requirements and ran UAT
    salesforce PLANNED  migration PLANNED  requirements PLANNED  uat PLANNED
Learning React, built Node APIs
    react LEARNING  node LEARNING  apis LEARNING
Did not write Apex, but configured Salesforce
    apex USED (!)  salesforce USED
Roadmap for data migration; developed reporting dashboards
    migration PLANNED  dashboards USED          correct -- ';' is a terminator
Built services with Node.js, Express and PostgreSQL
    all three USED                              correct -- one predicate
```

## 2. What the damage actually was

Not the sentence the defect is usually told with. Measured over 54 personas:

```
TOTAL CONCEPT OCCURRENCES  901
  USED 290   MENTIONED 543   PLANNED 56   LEARNING 12   NEGATED 0
MULTI-CONCEPT CLAUSES      119
CLAUSES WITH A CUE          17
MULTI-CONCEPT AND CUED      11      <- the only place any scope rule can act
```

All eleven are **skills lists**, and 52 of the 56 PLANNED occurrences are in the
skills section. The "intent cue" is a substring of a *sibling skill name*:

```
Scrum, Agile, Kanban, Backlog Refinement, Sprint Planning, Retrospectives, Jira
                                                 ^^^^^^^^
API Design, Technical Specifications, Roadmap, SQL, Python, REST APIs, Jira
                                      ^^^^^^^
Demand Planning, Forecasting, Inventory Management, S&OP, Excel, SQL, Power BI
       ^^^^^^^^
```

`pm_technical`'s Python, SQL, REST API, OpenAPI, SLOs, Jira and Confluence were
all PLANNED because "Roadmap" is a skill they listed. **PLANNED is not in
`CLAIMED`**, so every one of those dropped out of the evidence entirely and took
its tier with it. Fifty-three of the fifty-six PLANNED occurrences are this.

## 3. Ablation

Seven deterministic strategies. Fixtures are the §7 shapes plus the two
skills-list shapes the measurement above produced.

| | fixtures | real occurrences changed | direction | coordination |
|---|---:|---:|---|---|
| **A** current | 32/48 | — | — | — |
| **B** every comma bounds | 35/48 | 54 | narrows | **broken** |
| **C** `_governed` everywhere | 34/48 | 35 | **WIDENS 34** | kept |
| **D** intersect with `_governed` | 36/48 | 0 | narrows | kept |
| **E** D + lead-in | 41/48 | 33 | narrows | kept |
| **F** E + cue-ends-its-item | 41/48 | 53 | narrows | kept |
| **G** F + coordination required | **41/48** | **53** | narrows | kept |

Three results decided this.

**C is disqualified for widening.** Bounding PLANNED by `_governed` alone makes
it *wider*, because its existing forward bound is already a bare clause break.
On the development set that turned 34 MENTIONED occurrences into PLANNED —
`plat_sf_consultant`'s CPQ and Fit-Gap Analysis among them. Widening is outside
Step 8's mandate, so every window here is an **intersection** with what the
layer already did.

**B is disqualified for destroying coordination**, exactly as §11 warns. It
scores best-but-one on raw leak count and turns "Learning React, TypeScript and
Next.js" into one LEARNING and two MENTIONED, and stops "Evaluated Kubernetes
but did not adopt it" negating at all.

**G is free.** Its 53 changed occurrences are the *identical set* to F's —
verified element by element — so the coordination requirement costs nothing
measurable and buys fixtures A, s2 and §7-I. That is the one place a rule was
added without a real-occurrence justification, and it was added because it is
provably free rather than because it scored better.

## 4. The rule

```
1. no cue window is ever wider than _governed(), and never wider than it was
2. a cue separated from the concept by a COMMA must LEAD the clause
3. a cue immediately followed by a comma ENDS its own item, so it introduces
   nothing; and a lead-in crosses a comma only into a list closed by and/or
```

Rule 1 is reuse, not invention — `_governed` is the segmentation `shape()` has
always used. Rules 2 and 3 are the new part, and they are one idea: *are the cue
and the concept in the same list item?*

Coordination is preserved on purpose. `Learning React, TypeScript and Next.js`
is still three LEARNING and `did not use Java or Kotlin` still negates both.

## 5. Results

```
SEMANTIC ASSIGNMENTS CHANGED   53 of 901
  PLANNED -> MENTIONED         36
  PLANNED -> USED              17
  LEARNING -> USED              0        no LEARNING leak exists on this set
  NEGATED -> USED               0        NEGATED never fires on this set
  USED -> PLANNED / -> NEGATED  0        nothing is demoted
```

Every change is in one direction: a skill the candidate listed stops being read
as something they intend to do.

| | |
|---|---:|
| EVIDENCE DEPTH CHANGES | 53 |
| IMPORTANCE TIER CHANGES | **41, every one `BACKGROUND -> SUPPORTING`** |
| CORE / STRONG_SECONDARY changes | **0** |
| FINAL WEIGHT CHANGES | 41, each +1 |
| SEARCH QUERY CHANGES | 1 persona, 366 → 364 |

No skill is promoted past SUPPORTING, so the fix returns erased evidence
without inventing any. `§13`'s thresholds, weights and formulas are untouched;
only their inputs moved.

### The one discovery change, explained

`ops_supplychain` loses `ai solutions engineer` and `aem full stack developer`.
Neither is a supported query — the first classifies `ml_engineer`, which that
persona's labels mark **forbidden**.

The mechanism is Step 3's own fail-open, not a new rule. With the leak, that
candidate's role record was every-family-weak, which Step 3 reads as *thin* and
refuses to filter on. With SAP, Power BI, Forecasting and Inventory Management
no longer erased, the record is real, Step 3 engages, and it rejects two
software queries a supply-chain analyst should never have had. Corrected
evidence let an existing gate do its job.

### Steps 3–7

```
STEP 3 ROLE EVIDENCE   7 of 54 differ, all attributable and all ADDITIVE
STEP 4 TITLE GATE      0 of 54 differ
STEP 5 RESTORATIONS    0 of 54 differ
STEP 7 CANONICAL       16 -> 16 rejections, identical
```

The seven are exactly the seven personas whose skills list was leaking. Every
difference adds work-mode support or lifts weak to strong; none removes any.
No file in `role_evidence.py`, `title_gate.py`, `hard_drop.py`,
`orphan_guard.py` or `canonical_guard.py` was touched.

## 6. Two things the trace found that Step 8 does not fix

**The spec's premise on negation is false.** §4 says not to weaken
`did not write Apex or LWC`. It never worked: `_NEGATED` lists
`did not (use|adopt|ship|pursue|proceed)` and not `write`, so Apex came back
**USED** — the verb inside the denial was read as evidence of doing the thing
denied. No scope rule can fix that; the cue never fires.

This is cue DETECTION, not scope, so it is a **separate sub-flag**:
`SWEEP_SEMANTIC_SCOPE_NEGATION_VERBS`, on when Step 8 is on, refusable on its
own. It changes 0 occurrences on the development set, because NEGATED fires
nowhere on it. Review can take Step 8 and refuse this.

**A residual, left deliberately.** `Planning Salesforce migration, gathered
requirements and ran UAT` still marks UAT as PLANNED, because `ran` is in
neither `_ACTION` nor `_OTHER_VERB`, so no new predicate opens and the lead-in
crosses a coordinate list. The obvious fix is to extend the clause-verb
lexicon, and it was measured and rejected: of the 22 verb-shaped tokens
following a comma on the development set, **20 are skill names** —
`forecasting`, `budgeting`, `onboarding`, `prospecting`, `coaching`,
`sourcing`, `wireframing`, `hiring`. Teaching the segmenter that those open
predicates would fragment every skills list. One residual is cheaper.

Related, and also not fixed: `requirements` in that sentence lands MENTIONED
rather than USED, because `gathered` is absent from `_ACTION`. That is the
evidence-depth lexicon, not scope. **Recorded as V3-C17.**

## 7. Tests

`auto-apply/tests/test_semantic_scope.py` — 45 tests: the ten §7 shapes, the
skills-list shape that produced the real damage, coordination in both
directions, negation direction across "but", the never-widen property, seven
format-invariance tests (§10), explainability, and off-by-default.

Mutation-tested. Reverting to clause-wide planned scope, breaking and/or
coordination, allowing negation across "but", treating every comma as a
boundary, treating no comma as a boundary, letting a cue that ends its item
still govern, and leaking learning into a following predicate each turn the
suite red.

The first run found a real defect in my own module: `governs()` searched for
the coordinating conjunction to the end of the document instead of to the cue
family's window, so `Planning Salesforce migration, gathered requirements`
still scoped PLANNED across the comma. The simulation had bounded it and the
shipped code had not. Caught by shape A, fixed, and the surface re-verified
against the simulation element by element afterwards.

---

## Status

```
BASE SHA:                     b6b1c92
STEP 7 SHA:                   b6b1c92   verified unmoved
STEP 8 FLAG:                  SWEEP_SEMANTIC_SCOPE, off unless set
                              SWEEP_SEMANTIC_SCOPE_NEGATION_VERBS, sub-rule 4,
                              on when Step 8 is on, refusable on its own

CURRENT SCOPE MODEL:          NEGATED and LEARNING read the whole sentence in
                              both directions. PLANNED reads the whole sentence
                              backwards and to the next clause break forwards.
                              No cue consults clause structure; _governed()
                              exists and only the evidence-depth layer uses it.
NEW SCOPE MODEL:              every cue window is intersected with _governed()
                              -- never replaced, so nothing widens -- and a cue
                              in a different comma item from the concept does
                              not govern it. A cue crosses a comma only when it
                              leads the clause, does not end its own list item,
                              and the list states coordination (and/or).

TOTAL OCCURRENCES:            901 across 54 personas
MULTI-CONCEPT CLAUSES:        119   (17 cued; 11 both)
SEMANTIC ASSIGNMENTS CHANGED: 53

PLANNED -> USED:              17
PLANNED -> MENTIONED:         36
LEARNING -> USED:              0    no LEARNING leak on this set
NEGATED -> USED:               0    NEGATED fires nowhere on this set
USED -> PLANNED/NEGATED:       0    nothing demoted
FALSE COORDINATION REGRESSIONS: 0

EVIDENCE DEPTH CHANGES:       53
IMPORTANCE TIER CHANGES:      41, all BACKGROUND -> SUPPORTING
                              CORE 0, STRONG_SECONDARY 0, SUPPORTING +41
FINAL WEIGHT CHANGES:         41, each +1
SEARCH QUERY CHANGES:         1 persona, 366 -> 364
                              ops_supplychain loses 'ai solutions engineer'
                              (ml_engineer, FORBIDDEN) and 'aem full stack
                              developer'. 0 supported queries lost, 0 added.

STEP 3 ROLE EVIDENCE:         attributable changes, 7 of 54, all additive
STEP 4 TITLE GATE:            identical, 0 of 54
STEP 5 RESTORATIONS:          identical, 0 of 54
STEP 7 CANONICAL VALIDATION:  identical, 16 -> 16

INTENDED DEPLOYMENT STACK:    Step 3 ON, Step 4 ON, Step 5 ON,
                              Step 6 OFF, Step 7 ON, Step 8 ON

TESTS:                        1,965 across four suites, all passing
                              auto-apply 982, sweep 816, bench 125, deploy 42
                              45 new; 7 mutations, 0 survivors
DIFF:                         semantic_scope.py (new), skill_evidence.py
                              (+44 -1, the original classify body untouched),
                              the test file, this handoff. No other production
                              file changed; data/ untouched.

OLD HOLDOUT USED:             no
READY FOR STEP 8 REVIEW:      yes
```

Not deployed. The flag is off unless set, and with it absent `classify()` runs
the original code path — verified byte-identical over all 901 occurrences.
