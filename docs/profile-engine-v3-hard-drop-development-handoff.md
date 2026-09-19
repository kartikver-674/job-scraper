# V3 Step 5 — a candidate-aware hard drop

Fixes V3-C8. `config.SCORING` strips manager, architect, director and lead from
every title everywhere, and a title reduced below two words is discarded. An
Engineering Manager's own job title therefore produces no search at all, and on
the development fixture that candidate escalates with no profile whatsoever.

| | |
|---|---|
| Base | `6e0b095` |
| Step 4 | `6e0b095`, verified unmoved |
| Step 3 | `5ddb2e9`, verified unmoved |
| Step 5 flag | `SWEEP_CANDIDATE_HARD_DROP`, off unless set |

---

## 1. The flow, traced before anything was changed

### The configured list is three different things

| kind | terms | exemptible |
|---|---|---|
| **ROLE** — an occupation someone can hold | manager, architect, director, head of, vp, chief, and `lead` from `soft_drop_terms` | **yes** |
| **LEVEL** — a rank within an occupation | principal, staff, senior, sr | no |
| **ENTRY** — the too-junior block | intern, internship, trainee, fresher, apprentice, co-op, new grad, graduate, junior, jr | no |

`lead` is in `soft_drop_terms`, not `hard_drop_terms`, and `Market.seniority` is
the two lists concatenated — so soft-drop words erase a title at query
construction exactly as hard-drop words do. The brief named Technical Lead; it
is affected through the soft list.

### Where a query is actually lost

Matching is **word-boundary on individual words**, not substring or phrase, and
the drop happens at four places on the query side:

1. **`local_search.fragments(title, seniority)`** strips the words when indexing
   the corpus and when producing n-grams, so `engineering manager` yields no
   fragment at all.
2. **`local_search.from_resume(person, seniority)`** strips them from the
   candidate's own held titles, then discards any title left with fewer than two
   words. **This is the sharpest point.**
3. **`local_search.canonical(fragment, titles, seniority)`** prefers a
   seniority-free title and only falls back to a stripped one that already
   exists in the corpus.
4. **`local_search.check_role_keywords`**, via `validate`, deletes any
   `role_keyword` carrying a hard-drop word, because such a keyword buys rows
   config then deletes.

Measured against the live code:

```
from_resume("Engineering Manager")     -> []
from_resume("Solutions Architect")     -> []
from_resume("Program Manager")         -> []
from_resume("Technical Lead")          -> []
from_resume("Director of Engineering") -> ['of engineering']
from_resume("Senior Backend Engineer") -> ['backend engineer']
fragments("engineering manager")       -> set()
```

### Everything else it touches

- **Ranking and stored rows.** `scraper.HARD_DROP_PATTERNS` drops scraped
  postings, and `merge_jobs` re-applies it so rows already on disk are removed.
  That is a separate stage and is untouched here.
- **Title admission.** `ATS_TITLE_EXCLUDE` gates free sources;
  `hard_drop_terms` deliberately does not, because it must also apply to paid
  rows. Step 4's gate is therefore unrelated to this and unmoved.
- **Step 3.** `role_evidence` never reads the drop lists. It is read here and
  not modified.
- **v1 / v2 rollback.** None of the four consumers consults the engine version,
  so the defect is engine-version-independent and a rollback does not escape it.

## 2. What Step 5 does

`hard_drop.restore(profile_data, market)` returns queries to **add back**. It
never removes anything, and it runs in `local_profile.generate` immediately
after `fields_for` and **before** the Step 3 gate, so a restored query faces
that gate exactly as one that had never been dropped would.

Two sources, narrowest first, and nothing else grants an exemption.

**A grounded held title**, restored verbatim. Not a family, not a related title,
not a promotion. An Engineering Manager gets Engineering Manager back; a
Software Engineer gets nothing, because family-level support is precisely the
upward inflation this must not create.

**A stated target plus corroborating work evidence.** `target_field` is
ungrounded and can never bypass a drop alone. Paired with **strong** evidence
for the work mode the role implies — management work for a manager, development
work for an architect — it is a transition the system already has evidence for.

The validator's economic guard survives: a term matching more than a quarter of
the corpus is the catalogue, not a search, and stays dropped however well
grounded it is.

### One guard removed as circular

An earlier revision also required the restored title to appear in the scraped
corpus, and refused `project manager`, `digital marketing manager` and three
others on that basis. The corpus holds no such rows **because the corpus is the
history of searches that were themselves software-shaped**. Requiring corpus
presence makes the bias self-sealing. `from_resume` is documented as the
zero-dependency floor that works with no corpus at all, and a person's own job
title is held to the same standard. Removing that check took
management/architect visibility from 5 of 10 to 10 of 10 with no change to
contamination.

## 3. Development results

56 personas, 54 with output. Graded by the audit's own query classifier and the
persona labels, both written before any of this existed.

| | before | after |
|---|---|---|
| primary role coverage | 49/54 = 91% | 51/54 = **94%** |
| **management/architect/lead visibility** | 0/10 = **0%** | 10/10 = **100%** |
| supported query precision | 0.538 | **0.551** |
| contaminated queries | 38 | **38** |
| technicalization | 4 | **4** |
| query count | 370 | 381 |
| zero-query personas | 0 | **0** |

| | |
|---|---:|
| personas with a role-bearing held title | 10 |
| **hard-drop false negatives fixed** | **11** |
| **upward role inflation** | **0** |
| restored but neither supported nor forbidden | 0 |
| **hard-drop true negatives preserved** | **44/44** |

The eleven restorations are every one of the ten personas' role-bearing titles
(one persona holds two). All eleven are label-supported. **Not one is
label-forbidden**, contamination does not move, and technicalization does not
move — so the restored queries are the person's own role and nothing else.

The forty-four personas with no role-bearing held title gained nothing, which is
the anti-inflation guarantee as a number rather than an intention.

### What was restored

`product manager`, `associate product manager`, `business development manager`,
`customer success manager`, `sales manager`, `project manager`, `technical
product manager`, `digital marketing manager`, `content marketing manager`,
`email marketing manager`. Every one is a title the person actually held.

## 4. Nothing else moved

**Step 4's title gate is identical**, verified by running the Step 4 comparison
with Step 5 on and off: 56 personas, 0 differing gates, identical aggregates.

**Step 3's output changes only by restoration.** Across all 54 personas: 0 lost
a query, 0 gained a query that was not a restoration, 11 queries added, and 0
restorations were then rejected by the Step 3 gate. Those eleven are the
expected restorations and are listed above.

**With the flag absent, behaviour is byte-identical** — the module is not even
consulted, asserted by a test that runs `generate` both ways.

The diff is three files: `hard_drop.py` (new), `auto-apply/local_profile.py`
(+23 −0), and the test file.

### Interactions observed and not patched

- **The one-word rule is doing real work.** `from_resume` discards a title left
  with a single word because "engineer" alone buys the catalogue. Step 5
  inherits that rule rather than routing around it, so a person whose whole
  title is "Manager" still gets nothing.
- **`Director of Engineering` produced the fragment `of engineering`** under the
  old path, because "of" survives the strip. That junk fragment is still
  produced; Step 5 adds the correct title alongside it rather than removing it,
  since removing a query is outside this step's contract.

## 5. Tests

`auto-apply/tests/test_hard_drop.py` — 38 tests: the eleven required regression
shapes, the defect itself asserted against the live search code, role versus
level versus entry classification, the economic guard, explainability, and a
wiring test that runs `generate` with the flag both ways and asserts the profile
is otherwise unchanged.

Mutation-tested. Letting a target bypass without corroboration, restoring
one-word titles, removing the catalogue guard, making level words exemptible,
manufacturing a title from family support, never running the step, and never
adding the restored queries each turn the suite red.

---

## Status

```
BASE SHA:                        6e0b095
STEP 4 SHA:                      6e0b095   verified unmoved
STEP 3 SHA:                      5ddb2e9   verified unmoved
STEP 5 FLAG:                     SWEEP_CANDIDATE_HARD_DROP, off unless set

HARD-DROP TERMS TRACED:          18 hard + 3 soft, classified as
                                 ROLE   manager architect director head-of vp
                                        chief lead        -> exemptible
                                 LEVEL  principal staff senior sr
                                 ENTRY  intern internship trainee fresher
                                        apprentice co-op new-grad graduate
                                        junior jr
EXEMPTION INPUTS:                a grounded held or extracted title; or a stated
                                 target plus STRONG evidence for the work mode
                                 the role implies
UNGROUNDED TARGET CAN EXEMPT:    no

PRIMARY ROLE COVERAGE:           91% -> 94%
HARD-DROP FALSE NEGATIVES:       11 fixed
HARD-DROP TRUE NEGATIVES:        44/44 preserved
MGMT/ARCHITECT/LEAD VISIBILITY:  0/10 -> 10/10
UPWARD ROLE INFLATION:           0 -> 0
ZERO-QUERY:                      0 -> 0
SUPPORTED QUERY PRECISION:       0.538 -> 0.551
CONTAMINATED QUERIES:            38 -> 38

STEP 3 OUTPUT:                   expected restorations only; 0 lost, 0
                                 unexplained additions, 0 gate rejections
STEP 4 TITLE GATE:               identical
TESTS:                           1,860 across four suites
                                 auto-apply 877, sweep 816, bench 125, deploy 42

OLD STEP 3 HOLDOUT USED:         no
READY FOR STEP 5 REVIEW:         yes
```

Not deployed. The flag is off unless set.
