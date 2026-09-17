# Profile Engine v2 — evaluation and migration gate

**Recommendation: ship steps 1–3 (correctness, canonical concepts, evidence-aware
importance) behind their existing opt-in flags. Do NOT ship step 4 (role-query
construction) in its current form — it leaves 2 of 16 evaluation personas with no
job search at all.**

Nothing was deployed. Every flag remains off by default.

Run: `python -m bench.evaluate` · harness [bench/evaluate.py](../bench/evaluate.py) ·
fixtures [bench/eval_people.py](../bench/eval_people.py) · date 17 September 2026 ·
commit `133718f` · corpus: live, 22,806 rows.

---

## 1. What this evaluation can and cannot support

It **can** support claims about: whether the eight known failures are gone, whether
canonicalisation loses skills, whether importance ordering is invariant to
provenance and rarity, whether queries stay inside a persona's plausible
professions, and what each step costs in time.

It **cannot** support any claim about job quality. There is no independently judged
job-ranking data in this repository, so there is **no Precision@10, no nDCG@10, no
pairwise ranking accuracy and no adjacent-career false-positive rate**. Those rows
of the evaluation plan are unmeasured, and nothing below should be read as a
proxy for them.

### The labelling conflict of interest, stated plainly

The evaluation plan asks for independent labels, double-reviewed. That did not
happen and could not: one person wrote the engine, the fixtures and the labels.
Three different grades of evidence are mixed here and the report marks which is
which:

| Grade | What | Why it is worth what it is |
|---|---|---|
| **Pre-existing** | `bench/people.py` skills, titles, employment, `relevant` | Written before v2 existed, for a different question. The PDFs are *generated from* these dicts, so the document cannot disagree with the answer. |
| **Authored** | the `credible` / `wrong` role labels in `bench/eval_people.py` | Written by the engine's author. Deliberately coarse — whole professions, not exact strings — so a label cannot name its own expected output. Still a conflict of interest. |
| **Not labelled** | importance tiers | A tier *is* "where does this appear in the document", which is what the code computes. A hand-written tier key would be the same rule applied by hand and agreement would measure nothing. Importance is judged on **invariants** instead. |

Treat every "wrong-career" number below as author-scored. Treat the invariants and
the stability results as objective — they are properties, not judgements.

---

## 2. Conditions

Model output, résumés, corpus, clock, seniority lists and preferences are pinned.
Extraction is built once per person from the persona's own truth and replayed into
all five conditions, so **a difference between columns is postprocessing and
nothing else**. No model was called.

| | Condition | Flags |
|---|---|---|
| A | v1, production today | none |
| B | canonical concepts only | `SWEEP_SKILL_CONCEPTS` |
| C | concepts + evidence importance | `+ SWEEP_SKILL_EVIDENCE` |
| D | concepts + role validation, v1 weights | `+ SWEEP_ROLE_FAMILIES`, importance discarded |
| E | full v2 candidate | all three |

**D is not a shippable configuration.** Role families are anchored by importance
tiers, so "roles without importance" silently falls back to B — the first run of
this harness produced a D column identical to B, which is itself a finding. D
therefore computes tiers for the gate while keeping v1's weights, purely to
isolate the role change. It is a measurement, not an option.

---

## 3. Results

16 personas across 16 strata. PDFs rendered and extracted through the real
production path (504–2,883 characters of text). **0 failed parses, 0 errors, in
every condition.**

| | Condition | Concept recall | Queries | Wrong-career | Unjudged | **People with 0 queries** | p50 | p95 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | v1 | 1.000 | 116 | **4** | 26 | 0 | 392 ms | 552 ms |
| B | concepts | 1.000 | 116 | 4 | 26 | 0 | 392 ms | 560 ms |
| C | + importance | 1.000 | 116 | 4 | 26 | 0 | 395 ms | 559 ms |
| D | + roles | 1.000 | 41 | **0** | 3 | **2** | 724 ms | 1548 ms |
| E | full v2 | 1.000 | 41 | **0** | 3 | **2** | 724 ms | 1554 ms |

### Extraction / normalization

- **Concept recall 1.000 in every condition.** Canonicalisation loses nothing:
  every skill in every persona's pre-existing list survives to the profile.
- **Unknown-tool recall 1.000.** `dmitri`'s deliberately unheard-of technologies
  pass through as their own concepts.
- **Compound-split errors: 0.** No persona's skills were mis-split.
- **Alias invariance: 0 duplicate entries across all 16 personas.**

That last number is a **limitation, not a success**. `bench/people.py` lists each
skill in exactly one canonical spelling, so these fixtures *do not exhibit the
alias problem at all*. Step 2's measured benefit comes from the audited real
résumé — Firebase FCM 12→5, REST API design 13→4, JavaScript 0→3, LWC 0→3 — and
from unit tests, **not from this fixture set**. The small A→B score movement here
(493→488 across all personas) is span-containment, not deduplication.

**B shows no measurable benefit on these personas.** A fixture set that reproduced
the extractor's real spelling noise would be needed to measure it properly.

### Importance — the clearest result in this evaluation

Measured as pairwise ordering: how often a **skills-list-only** concept outranks a
**work-evidenced** concept, across every such pair in all 16 personas.

| | Comparable pairs | Inversions | Rate |
|---|---:|---:|---:|
| A (v1) | 344 | **66** | **19.2 %** |
| C (v2) | 344 | **0** | **0.0 %** |

Nineteen percent of core-versus-list-only pairs were ordered backwards by v1.
None are in v2. Source invariance is separately confirmed by invariant 4: the same
evidence yields the same tier whether the model reported the skill at weight 3 or
the scanner recovered it at weight 5.

### Search — improvement and regression together

v2 removes every wrong-career query the labels identify:

| Persona | v1 wrong query | v2 |
|---|---|---|
| `ada` (backend) | `mobile developer` | removed |
| `farida` (data) | `mobile application developer` | removed |
| `jonas` (data) | `mobile application developer` | removed |
| `mateo` (backend) | `mobile developer` | removed |

**4 → 0 wrong-career queries, and unjudged queries 26 → 3.** Query stability under
corpus row permutation: **32/32 identical** in both A and E.

And it costs breadth badly:

| | A (v1) | E (v2) |
|---|---:|---:|
| Total queries | 116 | 41 |
| Median per person | 8.0 | **1.5** |
| People with **zero** queries | 0 | **2** |
| People with one query | 0 | **6** |

### Performance

p50 392 ms → 724 ms (**1.85×**), p95 552 ms → 1554 ms (**2.8×**). No new inference
calls in any condition; the cost is corpus scanning in role construction. Timeout
rate 0. Token counts unchanged (no model call added). Cold-vs-warm not separately
measured — every run here uses a warm live corpus.

---

## 4. Critical invariants — the ship gate

All nine checks pass. These are objective properties, not author judgements.

| # | Known failure | Status | Evidence |
|---|---|---|---|
| 1 | aliases increase score by duplication | **PASS** | one spelling 3, four spellings 3 |
| 1b | Firebase FCM triple-counts | **PASS** | 5, was 12 |
| 2 | JavaScript/LWC matching fails on formatting | **PASS** | both 3, both were 0 |
| 3 | provenance changes importance | **PASS** | same tier at weight 3 and 5 |
| 4 | generic Git alone creates a Python career | **PASS** | git and ci/cd refused even at CORE |
| 5 | row order changes final queries | **PASS** | 4 fragments stable under shuffle; 32/32 personas stable |
| 6 | unsupported employment facts create experience | **PASS** | fabricated 1999–Present row → escalate |
| 7 | model prose escapes the profile boundary | **PASS** | no marker among top-level names |
| 8 | common core skills recommended for deletion | **PASS** | no `checked` binding on the remove box |

The 52-document regression suite is **unchanged**: 52 documents, years exact 52/52,
macro F1 0.97628, router 38 corrected / 14 accept / 0 escalate. No expected answer
was rewritten. The three new personas live in a separate module precisely so this
baseline could not move.

---

## 5. Every failure, including the ones that decide the recommendation

### 5.1 BLOCKING — two personas lose their job search entirely

**`bhaskar`, student/graduate with no employment.** Every concept is
SUPPORTING (skills-list only, no work bullets to evidence anything). Role families
may only be anchored by CORE or STRONG_SECONDARY concepts, so **zero families,
zero queries**. v1 gave him 6.

```
bhaskar  SUPPORTING: CSS, Git, HTML, Java, MySQL, spring boot
         families: 0 of 0        queries: []
```

This is the exact stratum the frozen title corpus was built for — a graduate whose
every role is an internship. Step 4 re-broke it from the other end.

**`gopal`, QA specialist.** Has four CORE concepts (selenium, appium, jenkins,
playwright) and six proposed families, and the corpus has too few QA listings for
any concrete title to clear `MIN_ROWS = 20`:

```
gopal    qa automation  rejected: only 5 listings, needs 20
         test engineer  rejected: only 9 listings, needs 20
         families: 6, with titles: 0      queries: []
```

A real specialist in a niche the corpus under-covers gets nothing. The failure is
silent — no error, no warning, an empty search.

### 5.2 SERIOUS — breadth collapse for well-evidenced engineers

Six more personas drop to a single query. `ada`, a five-year backend engineer with
eight skills, gets one:

```
ada   CORE:       GraphQL, celery, django
      SUPPORTING: Docker, Kubernetes, PostgreSQL, Python, Redis
      queries: ['backend engineer']
```

Two causes, both real:

1. **Python is SUPPORTING for a Python backend engineer**, because her work bullets
   say "Django monolith" and never the word "Python". The tier rules are too
   literal: a framework should evidence its language.
2. **Seniority rejection removes good titles** without offering the stripped form —
   `senior backend engineer` is rejected and `backend engineer` is only reached by
   luck of the ranking.

### 5.3 Measurement failures

- **Alias invariance is untested by this fixture set** (§3). The benefit is real but
  demonstrated elsewhere.
- **`yuki` produces 12 queries** where others produce 1, without an obvious reason —
  unexplained variance in role construction.
- **26 → 3 "unjudged" queries** partly reflects v2 emitting far fewer queries, not
  only better ones. The unjudged rate is not a quality measure.
- **No cold-corpus condition.** Every run used the live 22,806-row corpus.
- **One layout only.** The evaluation renders `plain`; the 52-doc suite covers all
  four, but the v2 comparison does not.

### 5.4 Not measured at all

Precision@10, nDCG@10, pairwise ranking accuracy, adjacent-career false-positive
rate, human relevance of final queries, token counts, memory. All require either
independently judged job data or instrumentation that does not exist here.

---

## 6. Before / after examples

**Alias inflation** (audited real résumé, not the fixture set):

| Job text | v1 | v2 |
|---|---:|---:|
| `Firebase FCM` | 12 | 5 |
| `REST API design` | 13 | 4 |
| `JavaScript` | **0** | 3 |
| `LWC` | **0** | 3 |
| `Node.js and Node` | 4 | 2 |

**Importance** (audited real résumé):

| Concept | v1 | Tier | Market | v2 |
|---|---:|---|---:|---:|
| React Native | 3 | CORE | 3 | **5** |
| TypeScript | 2 | CORE | 1 (common) | **4** |
| Firebase FCM | **5** | STRONG_SECONDARY | 5 (rare) | 4 |
| Tailwind CSS | **4** | SUPPORTING | 5 (rare) | 3 |

**Queries** (`ada`, backend specialist):

```
v1  backend engineer, python developer, software engineer, backend developer,
    mobile developer  ← wrong career, ...  (10 total)
v2  backend engineer                        (1 total)
```

Both the removal of `mobile developer` and the collapse to one query are step 4.

---

## 7. Recommendation

**Ship steps 1–3 behind their existing opt-in flags. Hold step 4.**

Measured basis:

**Step 1 (correctness/safety) — ship.** Four defects fixed, all with regression
tests that fail without them: fabricated employment rows, docstring escape,
truncated model answers, row-order instability. Invariants 5, 6, 7, 8 pass. No
behavioural cost; the 52-doc suite is unchanged.

**Step 2 (canonical concepts) — ship, with the caveat recorded.** Invariants 1, 1b
and 2 pass; concept recall 1.000 and no compound-split errors across 16 personas.
Its benefit is **not** demonstrated by this fixture set, only by the audited résumé
and unit tests. It costs nothing measurable (p50 +0.6 ms) and fixes two zero-match
bugs, so the risk of shipping it opt-in is low even though the evidence is
narrower than it should be.

**Step 3 (evidence-aware importance) — ship.** The strongest measured result here:
core-versus-list-only inversions 19.2% → 0% across 344 pairs and 16 personas, with
provenance invariance confirmed. Costs 3 ms. It changes weights, not queries, so it
cannot produce an empty search.

**Step 4 (role construction) — do not ship.** It genuinely fixes what it was built
to fix — 4 wrong-career queries to 0, and the three audited failures are gone — but
it **removes the job search entirely for 2 of 16 personas** and cuts the median
person from 8 queries to 1.5, at 1.85× p50 latency. A search engine that returns
nothing for a graduate and nothing for a QA specialist is worse than one that
occasionally suggests the wrong career, because the wrong career is visible and an
empty search is not.

What step 4 needs before it can be reconsidered, in the order the failures rank:

1. A floor that guarantees **every** résumé gets a usable query set — most simply,
   fall back to v1's construction when no family survives.
2. Anchoring that works without employment history (the graduate case).
3. A language-evidences-framework rule, so Django work evidences Python.
4. Seniority **stripping** rather than rejection, so `senior backend engineer`
   becomes `backend engineer` instead of vanishing.
5. A breadth target measured against v1, not only a correctness target.

**Nothing should become production-default on this evidence.** Even for steps 1–3
the claim is "fixes defects, costs nothing measurable, breaks no invariant" — not
"finds better jobs". That claim needs independently judged job-ranking data, and
until that exists the honest position is opt-in with the flags off.
