# V3 Step 2 — concept identity moved ahead of search

Concept identity is now decided before a single query exists, by one pure
transformation that search and the profile both mean. The final v2 skill
representation is provably unchanged. **Nothing was deployed.**

The honest headline: this batch makes query construction **stable**, not
better. Supported-query precision moves +0.002. The reason is measured below
and it matters for Step 3.

| | |
|---|---|
| Step 1 commit | `6e115290c507a35f170ef7a350adde06d4a96013` |
| Step 2 base | `8ba32a2` (Step 1 plus its own SHA correction) |
| Engine | v2, unchanged. `render.yaml` untouched |
| Profile schema | 1, unchanged |
| Role construction | off and absent, unchanged |

---

## 1. Step 1, frozen

Reviewed the diff against the handoff before committing: 103 insertions, 0
deletions, two production files, purely additive. Targeted tests 21/21 and the
zero-behaviour-change proof re-run clean (54/54 personas, 2/2 escalations,
4/4 real cases).

Three commits:

| SHA | What |
|---|---|
| `86929ad` | the v2 forensic audit that scopes v3, with Step 1's four documentation-only corrections |
| **`6e11529`** | **Step 1 — role signals preserved without being consumed** |
| `8ba32a2` | records the Step 1 SHA in its own handoff, which was written before the commit existed |

No gitignored artifact, résumé file or unrelated change is in any of them, and
the worker race found during Step 1 was deliberately left unfixed.

Three v2-audit-era documents remain untracked, as previous sessions left them.
One of them names a personal résumé file, so it is not committed. The tree is
clean of all v3 work.

---

## 2. Trace, before changing anything

| Operation | Depends on | Affects weights | Verdict |
|---|---|---|---|
| `skill_concepts.split_compound` | `LOOKUP` only. Pure | no | **safe to move** |
| `skill_concepts.resolve` | `LOOKUP` only. Pure | no | **safe to move** |
| `local_search.clean_skills` | corpus vocabulary | no | already runs at search time; left where it is |
| `make_profile.widen_skills` | résumé text **and** live `output/` vocabulary | yes | **stays** — and is a documented no-op in production |
| `make_profile.reweight_from_evidence` | résumé text, evidence, market | yes | **stays** — this is Step 3+ territory at the earliest |
| `skill_evidence.*` | résumé text | yes | **stays** |

Two things were deliberately *not* moved even though they live in `_finish`.
`widen_skills` and `reweight_from_evidence` both read the résumé text, and this
batch is about identity, not evidence.

One coupling mattered more than the rest. `local_profile` built the profile's
`skill_weights` from `fields["skills"]`, the very set `fields_for` matches on,
so canonicalising the search input would silently have rewritten the profile's
terms from the résumé's own spellings to canonical ids. v2 keeps raw spellings
on purpose — they are the scorer's matcher keys — so the two were decoupled
instead (§3).

---

## 3. The seam

**`skill_concepts.identities(terms)`** — one pure function, `atomize` then
`resolve`, order-preserving, deduplicated on the canonical id.

```python
identities(["agile/scrum"])        -> ["agile", "scrum"]
identities(["javascript (es6+)"])  -> ["javascript"]          # one concept, once
identities(["aws (ec2, rds)"])     -> ["aws"]
identities(["React","react.js"])   -> ["react"]
identities(["user stories & acceptance criteria"])
                                   -> ["user stories & acceptance criteria"]
```

Order is load-bearing: resolving first would hand `split_compound` a string the
lookup table had already rewritten, and its whole protection is that it refuses
to split anything the table recognises.

It infers nothing. No evidence tier, no market, no role family, no weight.

`local_profile.generate` now cleans the raw skills once, passes
`identities(kept)` to `fields_for`, and **keeps `kept` for the profile**, so
the review screen, the rendered profile and the scorer see exactly what they
saw before. Gated on `skill_concepts.enabled()`, so a v1 rollback gets the raw
strings.

### Files changed

| File | Change |
|---|---|
| `skill_concepts.py` | **+30 / −0** — `identities()` |
| `auto-apply/local_profile.py` | **+30 / −5** — clean once, canonicalise, keep the raw list for the profile |
| `auto-apply/tests/test_canonical_before_search.py` | new, 20 tests |

---

## 4. Query churn, and why every change is attributable

Attribution is mechanical rather than argued. `fields_for` is a pure function
of `(person, market)`; the market is the same frozen corpus and the employment
rows are byte-identical, so the only changed input is `person["skills"]`. The
harness additionally replays the OLD skill set through the CURRENT `fields_for`
and requires it to reproduce the recorded BEFORE queries — if that reproduction
fails, something other than canonicalisation moved and the run stops. It did
not fail.

**58 candidates compared. 10 changed. 473 queries before, 468 after.**

Every changed candidate, with its cause:

| Candidate | Cause | Added | Removed |
|---|---|---|---|
| `swe_frontend` | alias: `html5`→`html`, `css3`→`css` | full stack web developer, front end engineer | react developer |
| `swe_reactnative` | alias: `rest apis`→`rest api` | react js developer | flutter developer, ios developer |
| `swe_backend` | alias: `rest apis`→`rest api` | associate engineer, mern stack developer, agentic ai engineer, web developer associate | back end developer, node.js developer, salesforce engineer, ui developer (angular) |
| `pm_technical` | alias: `rest apis`→`rest api` | associate engineer, applied ai architect education, ai ml engineer | solutions engineer, flutter developer, software engineer -python developer, cloud security engineer |
| `sol_consultant` | alias: `rest apis`→`rest api` | sf data cloud consultant | flutter developer, salesforce engineer |
| `tech_support` | alias: `rest apis`→`rest api` | fullstack developer, associate engineer, node js, back end developer, applied ai architect education, ai ml engineer | solutions engineer, react native developer, salesforce engineer, associate software engineer, software engineer -python developer |
| `adv_swe_titled_sf_work` | alias: `rest apis`→`rest api` | salesforce administrator, salesforce cpq developer | flutter developer, mobile developer |
| `adv_dev_to_pm` | alias: `rest apis`→`rest api` | node js | solutions engineer, flutter developer, salesforce engineer, ui developer (angular) |
| **`caseA_sfba`** | **compound: `agile/scrum`** | **salesforce developer** | sf -data cloud, salesforce consultant |
| `caseD_student` | alias ×4 + parenthetical: `aws (ec2, rds)`→`aws` | full stack web developer, node js | frontend developer, sde ii amazon now |

**QUERY CHANGES FULLY EXPLAINED BY CANONICALISATION: yes.**

### The churn is larger than the change, and that is the finding

One alias rename, `rest apis` → `rest api`, moves 464 listings to 465 — and
rewrites up to nine queries for a single candidate. That is not proportionate,
and the reason is in the corpus:

| alias pair | rows A | rows B | rows in **both** | Jaccard |
|---|---:|---:|---:|---:|
| `rest api` / `rest apis` | 465 | 464 | **9** | 0.010 |
| `rest` / `restful` | 583 | 1,682 | **0** | 0.000 |
| `tailwind css` / `tailwind` | 4 | 178 | **0** | 0.000 |
| `postgresql` / `postgres` | 704 | 34 | 4 | 0.005 |
| `html` / `html5` | 1,138 | 228 | 26 | 0.019 |
| `css` / `css3` | 1,340 | 193 | 51 | 0.034 |
| `react` / `react.js` | 5,068 | 703 | 659 | 0.129 |
| `node.js` / `node` | 2,827 | 2,769 | 2,483 | 0.798 |

**The corpus indexes spellings, not concepts, and the spelling is a fingerprint
of which past profile scraped the row — not of the job.** Each sweep tagged its
rows with the one spelling its profile carried, so alias row sets are nearly
disjoint. Canonicalising only the candidate therefore swaps one historical
slice of the corpus for another rather than seeing one concept more completely.

### What the other half would be worth

Measured, not wired (`output/profile-engine-v3-step2/both_sides.py`), over the
54 labelled personas:

| variant | queries | supported | contaminated | precision | primary coverage |
|---|---:|---:|---:|---:|---:|
| A — raw candidate, raw corpus (before) | 429 | 198 | 78 | 0.462 | 90.7% |
| **B — canonical candidate, raw corpus (this batch)** | **425** | **197** | **74** | **0.464** | **90.7%** |
| C — canonical candidate, canonical corpus | 422 | 199 | 71 | **0.472** | 90.7% |

Resolving the corpus too collapses 367 terms to 345 and puts real weight behind
them — `rest api` 465 → 2,894 rows, `node.js` 2,827 → 3,116, `tailwind css`
4 → 182 — and still only buys **+0.010 precision**. Concept identity is simply
not what is holding query quality back. The audit already said so: the binding
constraints are the missing role object, the corpus vocabulary's coverage, and
the free gate.

### Case A regressed, and it is worth understanding rather than patching

The Salesforce BA **gains `salesforce developer`**. Cause: `agile/scrum`
correctly atomises, `agile` and `scrum` become corpus-visible, retrieval
changes, and the unguarded search layer turns the extra true signal into one
more wrong query. It also loses the malformed `sf -data cloud`.

That is canonicalisation working and V3-C2/V3-C5 failing. It is an argument for
the role gate in Step 3, not against this batch — and it is a concrete warning
that **query churn without a semantic gate is close to a coin flip.**

---

## 5. Corpus visibility

| Stratum | n | before | after |
|---|---:|---|---|
| software | 13 | 92/144 — 63.9% | 92/144 — 63.9% |
| tech non-developer | 14 | 73/156 — 46.8% | 73/156 — 46.8% |
| non-technical | 15 | 37/148 — 25.0% | 37/148 — 25.0% |
| hybrid / adversarial | 12 | 74/129 — 57.4% | 74/129 — 57.4% |
| real résumés | 4 | 36/71 — 50.7% | **40/72 — 55.6%** |
| **all** | 58 | **312/648 — 48.1%** | **316/649 — 48.7%** |

Almost all of the gain is on the four real résumés, because they are the ones
with messy strings — `aws (ec2, rds)`, `restful apis`, `express.js`,
`agile/scrum`. The synthetic personas were authored with clean skill names and
had little for canonicalisation to recover.

**This does not fix V3-C3 and does not claim to.** The corpus vocabulary is
still software-biased; non-technical visibility is unchanged at 25.0%. What
this measures is only how much visibility was being lost to compound and alias
strings, and the answer is: on clean documents, almost none; on real ones,
about five points.

---

## 6. Required cases

| Input | `split_compound` | `identities` |
|---|---|---|
| `agile/scrum` | `['agile','scrum']` | `['agile','scrum']` |
| `jwt / oauth 2.0` | `['jwt','oauth 2.0']` | `['jwt','oauth']` |
| `aws (ec2, rds)` | kept whole | `['aws']` (parenthetical) |
| `cnn(convolutional neural network)` | kept whole | kept whole |
| `rnn(recurrent neural networks)` | kept whole | kept whole |
| `user stories & acceptance criteria` | kept whole | kept whole |
| `security & sharing` | kept whole | kept whole |
| `socket.io (websockets)` | `['socket.io','websockets']` | both, separate |
| `javascript (es6+)` | `['javascript','es6+']` | `['javascript']` — one concept |

Business phrases verified intact, not destructively split: requirements
gathering and stakeholder management, business analysis and process mapping,
recruiting and onboarding, budgeting and forecasting, sales and marketing,
reports & dashboards, research & development, negotiation and closing,
coaching and mentoring, demand planning, compensation and benefits,
month-end close.

`split_compound` is fail-closed — it splits only when **every** part is a known
concept — and the registry knows no business vocabulary, so the gate protects
exactly the population that most needs protecting. Tested, not assumed.

---

## 7. Invariance

**SEAM** — pure, no inference. `identities()` returns the same set under all
seven presentation changes: capitalisation, lowercase, alias spelling,
duplicate aliases, skill ordering, compound formatting, line wrapping. **7/7.**

**SEARCH** — role_keywords across the four layout matrices the audit captured
with live inference, 12 personas present in all of them:

| perturbation | before | after |
|---|---|---|
| PDF letter-spacing | 12/12 — 100% | 12/12 — 100% |
| **duplicated aliases** | **8/12 — 66.7%** | **11/12 — 91.7%** |
| skills reordered | 12/12 — 100% | 12/12 — 100% |
| SKILLS section moved | 12/12 — 100% | 12/12 — 100% |

**This is the batch's real result.** The audit's §14.1 finding — that adding
alias spellings changes the emitted queries for a third of candidates — is
three-quarters closed.

The one remaining failure is instructive. `ba_generic` gains three identities
under duplicated aliases: `requirement gathering`, `user story`,
`microsoft excel`. The registry knows none of them, so `identities()` cannot
merge them with `requirements gathering`, `user stories` and `excel`. Its query
*set* is unchanged; only the order moves. That is V3-C11 — unknown concepts get
exactly one matcher — and canonicalisation can only merge what the registry
knows.

**PROFILE** invariance is untouched by this batch: the finishing path receives
the same raw list it always did, and §9 proves the output identical.

---

## 8. V2 regression contract

The final v2 skill representation was re-derived both ways and compared by
SHA-256 across `skill_weights`, every `skill_importance` row (id, tier, weight,
market separation, evidence strength, status counts, resolved market key), the
derived concept set, and scorer output on a fixed job text.

| | |
|---|---|
| identical | **54 / 54** |
| moved | **0** |
| skipped | 4 real cases — source PDFs deliberately not re-read |

Employment rows, years, `role_signals`, evidence statuses, evidence strength,
importance tiers, final weights, market separation, generated-profile safety
and engine binding are all unchanged.

**V2 REGRESSIONS: none.**

---

## 9. Tests

| Suite | Step 1 | Step 2 | Delta |
|---|---:|---:|---|
| auto-apply | 749 OK | **769 OK** | +20, the new file |
| Sweep | 816 OK | **816 OK** | 0 |
| bench | 43 OK | **43 OK** | 0 |
| deploy / worker | 42 OK | **42 OK** | 0 |
| **total** | 1,650 | **1,670** | **+20, 0 failures** |

52-document date regression: 52/52 years exact. All module self-checks pass.

The 20 new tests cover the seam (atomisation, alias resolution, one-concept-once,
related concepts staying separate, unknown survival, business phrases intact,
order preservation, presentation invariance), the wiring (fields_for receives
canonical, atomisation happens before it, role_signals still not passed in, the
profile keeps the résumé's spellings), the finishing path (still atomises, no
double-split, one weight per concept, aliases score once), and rollback (v1 gets
raw strings, schema and role flag unmoved).

One test defect was found and fixed while writing them: the first draft asserted
v2 behaviour without binding the engine, and `skill_concepts.enabled()` reads
the environment per call, so it was silently asserting against the v1 path. The
suite now binds the engine the way `render.yaml` does.

---

## 10. Independent holdout

Unchanged from Step 1: **protocol only**, at
[docs/profile-engine-v3-holdout-protocol.md](profile-engine-v3-holdout-protocol.md).
No candidates supplied, no labels written, no lock taken.

That is acceptable for this step, which infers no role and gates nothing.

**It is not acceptable for the next one. No `role_evidence` behaviour may be
implemented until the holdout labels are collected and hash-locked**, because
a role gate is exactly the change whose evaluation an engine-aligned labeller
would contaminate. `output/profile-engine-v3-holdout/lock_labels.py` refuses to
lock an empty or malformed set.

---

## 11. Known limits

1. **The benefit is stability, not quality.** Precision +0.002. Say so plainly
   rather than presenting the invariance win as a search improvement.
2. **The corpus still indexes spellings.** Until it is resolved too, candidate
   canonicalisation swaps row sets rather than merging them, and the churn it
   produces is arbitrary in direction. The measurement for doing both sides is
   in §4; it is worth +0.010 and belongs in the corpus work the audit put
   outside the Step 1–5 sequence.
3. **Case A gained `salesforce developer`.** Explained, not patched.
4. **Only 12 personas have all four layouts**, so the search-invariance figures
   rest on 12, not 56.
5. **The four real résumés were not re-derived** — source PDFs are not re-read
   under the v3 data-access policy — so their weight half is proven only on the
   54 personas.
6. Measurements use synthetic, self-labelled development data. §10.

---

## Final summary

```
STEP 1 COMMIT:                     6e115290c507a35f170ef7a350adde06d4a96013
STEP 2 BASE:                       8ba32a2

FILES CHANGED:                     skill_concepts.py (+30/-0)
                                   auto-apply/local_profile.py (+30/-5)
                                   auto-apply/tests/test_canonical_before_search.py (new, 20 tests)
                                   docs/profile-engine-v3-canonical-before-search-handoff.md (new)

FINAL SKILL REPRESENTATION:        identical — 54/54 by SHA-256, 0 moved
SEARCH INPUT REPRESENTATION:       canonical

PERSONAS WITH QUERY CHANGES:       10 / 58 candidates
                                   (8 of 54 labelled personas, 2 of 4 real résumés)

TOTAL QUERIES BEFORE:              473   (429 over the 54 labelled personas)
TOTAL QUERIES AFTER:               468   (425 over the 54 labelled personas)

SUPPORTED QUERY PRECISION BEFORE:  0.462
SUPPORTED QUERY PRECISION AFTER:   0.464   (+0.002; 0.472 if the corpus were
                                            canonicalised too — measured, not shipped)

SEVERE CONTAMINATION BEFORE:       78 queries, 32/54 personas (59.3%)
SEVERE CONTAMINATION AFTER:        74 queries, 31/54 personas (57.4%)

CORPUS VISIBILITY BEFORE:          48.1%  (software 63.9 / tech-nondev 46.8 /
                                           non-technical 25.0 / real résumés 50.7)
CORPUS VISIBILITY AFTER:           48.7%  (software 63.9 / tech-nondev 46.8 /
                                           non-technical 25.0 / real résumés 55.6)

SEARCH INVARIANCE, duplicated aliases:  66.7% -> 91.7%
SEAM INVARIANCE:                   7/7 perturbations stable

QUERY CHANGES FULLY EXPLAINED BY CANONICALISATION:  yes
V2 REGRESSIONS:                    none

TESTS:                             auto-apply 769 OK, Sweep 816 OK, bench 43 OK,
                                   deploy 42 OK — 1,670 total, 0 failures
                                   52/52 date regression, all self-checks ok

INDEPENDENT HOLDOUT:               protocol only

READY FOR ROLE_EVIDENCE:           no — the holdout must be collected and
                                   hash-locked first
```

Stopping here. `role_evidence` was not implemented, the free gate was not
changed, `target_field` and `role_signals` are still consumed by nothing,
orphan anchors and `SKILL_LIFT` were not touched, and nothing was deployed.
