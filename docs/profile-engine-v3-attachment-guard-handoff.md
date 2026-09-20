# V3 Fix A — did the verb actually act on the artefact?

Fixes the attachment defect found by the free-search forensic report
(`docs/v3-hargun-free-search-regression.md`). Fix A only. Fix B (Step 4 family
expansion) and Fix C (generic-concept inflation) are deliberately untouched.

| | |
|---|---|
| Base | `e627fe9` |
| New flag | `SWEEP_ROLE_ATTACHMENT_GUARD`, **off unless set** |
| Diff | `attachment_guard.py` (new), `role_evidence.py` **+9 −0**, test file, this handoff |
| Ranking, retrieval, weights, `score_job` | untouched |

---

## 1. Root cause

`role_evidence._governed_hits` records a development hit when a CONSTRUCT verb
and an engineering ARTEFACT appear anywhere in the same governed clause:

```python
if not (verb_rx.search(low, lo, match.start())
        or verb_rx.search(low, match.end(), hi)):
    continue
```

Co-occurrence is not government. The verb's object is never identified, so

```
"Authored FSDs for the Supplier Warranty Recovery module"
        ^verb  ^object          the artefact the rule used ^
```

counts as development. What was authored is a specification document; the
module is the complement of "for". On the résumé that exposed this, three such
hits gave a Business Analyst `software_engineering` and `ml_engineering` —
both GATED families needing *strong* evidence — and Step 4 then admitted 28
engineering titles into the free-source title gate.

## 2. The rule

One sentence, in `attachment_guard.governs`:

> A verb governs an artefact only when the words between them form a single
> noun phrase — determiners and modifiers — with no preposition and no clause
> break.

A preposition means the artefact sits in a prepositional phrase, a different
slot from the object. A comma, colon, semicolon, bracket or dash means a
different clause.

Both directions are tried before anything is dropped: the nearest construct
verb *before* the artefact, then the nearest *after*. `built the service` and
`the service was rebuilt` both attach. A hit is rejected only when neither
attaches.

Deliberately **not** rules, each because it would cost a true positive:

| not a rule | why |
|---|---|
| `and` / `or` between them | `built reports and dashboards` — a coordinated object is still the object |
| distance | `implemented a highly available payment service` is fine at any adjective count |
| a preposition *after* the artefact | `built an API for dealer onboarding` — what follows the object says nothing |
| no construct verb in the clause | not this guard's decision; the existing check already handled it |

**Delegation is decided first and never re-judged.** The guard sits in the
`elif` branch after the `_SOMEONE_ELSE_ACTS` / `_DELEGATED` test, so consulting
evidence is byte-identical and "worked with the React Native team" still
confers nothing, exactly as before.

The brief warned against blindly rejecting on any intervening preposition. Two
things make this narrower than that: only the **nearest** verb on each side is
considered, and **either** direction attaching is enough.

## 3. Revalidation — intended V3 stack plus the guard

Stack: S3 ON, S4 ON, S5 ON, S6 OFF, S7 ON, S8 ON, negation ext OFF, fallback
`discard`. 54 personas plus the regression résumé.

```
DEVELOPMENT HITS (non-delegated)   42 -> 30      rejected 12
```

The read-only measurement predicted 41 / 9. The implementation rejects 12 of
42. The measurement script approximated each hit by searching the first
artefact inside the stored `quote`; the implementation uses the real match span
and the real clause bounds. **12 is the true figure; 9 was an artefact of the
approximation.** No rule was tuned to move this number.

### Suite-level

| | before | after |
|---|---:|---:|
| total queries | 364 | 360 |
| **supported queries** | **202** | **202** |
| forbidden queries | 34 | **33** |
| primary role coverage | 51/54 | 51/54 |
| zero-query personas | 0 | 0 |
| total gate hints, all personas | 2,156 | 2,111 |
| **supported-query losses** | — | **0** |

### The regression candidate

| | before | after |
|---|---|---|
| `software_engineering` | strong | **none** |
| `ml_engineering` | strong | **none** |
| supported families | 12 | 10 |
| title gate | 147 | **109** |
| queries | 14 | 14 (identical set) |
| observed bad titles admitted | 8 of 8 | **4 of 8** |

Exactly as predicted. The four that remain:

```
Director of Recruiting, Engineering & IT   recruiting
Staff Infrastructure Security Engineer     infrastructure
Technical Support Engineer 2               technical support
Data Analyst                               data analyst
```

These come from `hr_recruiting`, `it_administration`, `support` and
`data_analytics`, which are strong on their own evidence — `people`,
`administration`, `support` and `analysis` modes that genuinely fired. **That
is Fix B and is not addressed here.**

### Every watched persona

| persona | software_eng | ml_eng | gate | queries |
|---|---|---|---|---|
| **tech_support** | strong → **none** | strong → **none** | 53 → 16 | 8 → 4 |
| **swe_java** | strong → **weak** | strong → **none** | 37 → 29 | 4 → 4 |
| swe_frontend | weak → weak | none → none | 87 → 87 | 9 → 9 |
| swe_backend | strong → strong | strong → strong | 67 → 67 | 9 → 9 |
| swe_data_eng | weak → weak | strong → strong | 72 → 72 | 8 → 8 |
| swe_fullstack | weak → weak | none → none | 59 → 59 | 9 → 9 |
| swe_devops | weak → weak | none → none | 55 → 55 | 6 → 6 |
| swe_ml | none → none | weak → weak | 43 → 43 | 6 → 6 |
| adv_grad_cs | strong → strong | strong → strong | 78 → 78 | 6 → 6 |
| adv_swe_titled_sf_work | strong → strong | strong → strong | 57 → 57 | 10 → 10 |

**Two personas changed, and both changed for the better.** The measurement
report predicted only `tech_support`; `swe_java` is new, so it was examined
rather than assumed:

```
swe_java, guard OFF -- two development hits:
   "Java engineer with 8 years on Spring Boot microservices in banking"   <- a JOB TITLE
   "- Built JSP and Spring MVC modules for a core insurance system"       <- genuine
swe_java, guard ON:
   the job-title hit is rejected; the genuine one remains -> dev weak
```

`engineer` is a CONSTRUCT inflection and `microservices` an artefact, so the
person's own job title was being read as an act of construction. `ml_engineer`
is a **forbidden** family for `swe_java`, so losing it is a correction, and the
8 hints the gate loses are exactly the `ml_engineering` vocabulary. Its
software identity survives through its held title, which Step 4 admits
independently of mode strength — gate 37 → 29, queries unchanged.

`tech_support` is the same shape: both its hits were the words "Technical
Support **Engineer**" beside "**Software**"/"**platform**". `backend`,
`frontend` and `ml_engineer` are all forbidden for that persona. Its four lost
queries are `solutions engineer`, `salesforce engineer`,
`associate software engineer` and `react native developer` — none supported.

### Off is off

```
guard = "0", run twice                 identical
guard absent  ==  guard = "0"          identical
queries match the pre-Fix-A V3 baseline    54/54
```

## 4. Tests

`auto-apply/tests/test_attachment_guard.py` — 22 tests: the four false
attachments, eight true attachments (determiners, adjectives, compound
artefacts, prepositional complements, verb-after-artefact, no-verb), delegation
untouched including React Native, the regression fixture in both flag states,
and off-by-default.

The regression fixture is a reduced résumé carrying **no name, contact
details, employer or client names** — only the held title, the FSD/module
wording, the reports/dashboards wording and the Salesforce functional/admin
context that the mechanism needs. It asserts both directions: with the guard
on, no engineering family; with it off, the defect is still present, which is
what proves the off path is the original code rather than a quiet fix.

One test of my own was wrong on the first run: the "no role policy lives here"
check scanned the whole module source, including a docstring that legitimately
*names* the families the defect produced. It now parses the source and strips
docstrings, so it tests the code.

## 5. Known risks

- **Colon-introduced appositive lists are rejected.** `Built the customer
  portal end to end: React front end, Node.js and Express API, PostgreSQL` —
  the API *was* built, and the colon makes the guard drop it. `swe_fullstack`
  keeps its families from other hits, so nothing is lost here, but on a résumé
  whose only development evidence is written that way it would be a false
  negative. Handling apposition needs more than a punctuation rule.
- **`to` is treated as a phrase opener.** It covers both the preposition and
  the infinitive, which is right for `built ... to give business teams
  visibility`, but a sentence like `migrated the API to Go` is unaffected only
  because the artefact precedes the `to`.
- **The guard does not catch every job-title false positive.** `Technical
  Support Engineer | Kite Software` still attaches, because nothing separates
  the words. It survives only as *weak* evidence, below the GATED floor, so it
  no longer confers a family — but the underlying "a job title is not an act of
  construction" problem is only partly addressed.
- **Scope.** The guard applies to all three `_governed_hits` callers
  (development, data pipeline, configuration), not just development, because
  it is the same grammatical relation. The suite-level numbers above cover all
  three; no configuration or pipeline family changed on any persona.

## 6. Not addressed, by instruction

- **Fix B** — Step 4 family expansion has no cap; 10 strong families still
  produce a 109-hint gate for the regression candidate, and 4 of 8 bad titles
  still pass. Separate measurement and design decision.
- **Fix C** — `roles`, `users`, skill weights, market separation, ranking
  contribution and `score_job` are untouched. The forensic report showed
  "unmeasured" is not a valid generic-skill criterion; that work needs document
  frequency the corpus cannot currently provide.
- **Free-search retrieval** is unchanged, and it remains true that the free
  path never uses the search queries.

---

## Status

```
BASE SHA:                  e627fe9
FLAG:                      SWEEP_ROLE_ATTACHMENT_GUARD, off unless set
DEVELOPMENT HITS:          42 -> 30   (12 rejected; measurement predicted 9,
                           explained in §3)
SUPPORTED QUERY LOSSES:    0
FORBIDDEN QUERIES:         34 -> 33
PRIMARY ROLE COVERAGE:     51/54 -> 51/54
ZERO QUERY:                0 -> 0
PERSONAS CHANGED:          2, both losing a FORBIDDEN family
                           tech_support, swe_java
SOFTWARE PERSONAS HARMED:  0
REGRESSION CANDIDATE:      se strong -> none, ml strong -> none,
                           gate 147 -> 109, bad titles 8/8 -> 4/8
OFF PATH:                  identical to pre-Fix-A V3 on 54/54 personas
TESTS:                     1,906 across four suites, all OK
                           auto-apply 1,004 (+22 new), sweep 817, bench 43,
                           deploy 42
                           bench is 43 on main, not 125: the 82-test
                           role-family-adapter suite is holdout/evaluation-only
                           code that was correctly never promoted
FIX B / FIX C:             not started
```

Not deployed. The flag is off unless set.
