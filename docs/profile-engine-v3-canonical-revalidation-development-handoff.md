# V3 Step 7 — revalidating the canonical replacement

Fixes V3-C6. `canonical()` swaps a ranked n-gram for a real corpus title, which
is the right transformation. The defect is the boundary: what was valid as a
fragment is not automatically valid as its replacement.

| | |
|---|---|
| Base | `d2b5c74` |
| Step 6 | `d2b5c74`, verified unmoved, flag still off |
| Step 5 | `2c13a88`, verified unmoved |
| Step 4 | `6e0b095`, verified unmoved |
| Step 3 | `5ddb2e9`, verified unmoved |
| Step 7 flag | `SWEEP_CANONICAL_REVALIDATION`, off unless set |

---

## 1. Every `canonical()` consumer, traced before anything was changed

Three call sites, and they do **not** share a validation story.

| | input fragment | pre-canonical checks | post-canonical checks |
|---|---|---|---|
| **`canonicalise`** → corpus keywords | ranked n-gram from `keywords_for` | ranking only | **`validated()`** runs on the replacement: wildcard share, hard-drop words, escalation |
| **`candidates_for_skill`** → orphan path | corpus fragment beside an orphan skill | `>=8` employers, under `MAX_SHARE`, `skill_lift >= 10` | `worth_it()` only: `>=20` listings and a relevance score. **`validate` never runs** |
| **`hints_for`** → title hints | ranked n-gram | ranking only | none here; Step 4 now governs that gate |

```
fields_for
  corpus:  keywords_for -> canonicalise -> canonical() -> validated()  ✓
  orphan:  orphans -> candidates_for_skill -> canonical() -> worth_it -> select_detail
                                                             ↑ validate() is not on this path
  held:    from_resume -> validated()      (no canonical call at all)
```

**The guarantee lost at the boundary is `validate`, and it is lost only on the
orphan path.** That is the seam.

### And a second finding the trace forced

`validated()` would not have caught the offending title anyway. Measured
against the committed corpus:

```
'software engineer -python developer'
    listings 27   share 0.1%   hard-drop words: none   validated() keeps it
```

So routing the orphan path through `validate` is necessary and **not
sufficient**. No existing validator expresses the property that makes this
string wrong.

### What existing rules are, and are not, final-query rules

- **Final-query rules, reused**: the catalogue/wildcard share ceiling, the
  one-word-is-a-fragment rule, and the hard-drop word check. All three are
  re-applied to the final string.
- **Not a final-query rule**: `check_domain`'s reachability bar. It asks
  whether promoting on a title is better than not promoting, which is a
  *scoring* question. Applied to queries it rejects `backend engineer`,
  `software engineer` and `data analyst`, so it is deliberately not reused.
- **Transformations, not predicates**: `fragments()`, seniority stripping and
  `canonical()` itself change a string rather than judging one, and re-running
  them would be a second transformation, not a check.

## 2. The one new rule, and why it had to be new

**A leading hyphen on a token is a negation operator** in every major
job-board search syntax. `software engineer -python developer` does not ask for
an oddly-named job; it asks for software engineers that are NOT python. That is
a property of the string **as a search query**, which is exactly this boundary,
and no rule about corpus rows or economics can see it.

Deliberately not rules, and each would have been deleted by a tidiness check:

```
.net developer        c++ developer        front-end developer
ui developer (angular)    sr. business systems analyst
account executive, small business    associate consultant - technology
```

Punctuation inside a word, a parenthetical, a comma qualifier and an
abbreviation are how boards write titles. Of 123 unique canonical results on
the development set, a naive token-shape probe flagged 21; the operator rule
flags **2**, and both are genuinely broken.

## 3. Step 5 is not undone

The hard-drop check is re-applied to the replacement, but **role** terms route
through Step 5's own function rather than a reimplementation. A grounded
Engineering Manager keeps their title; an ungrounded one does not. LEVEL and
ENTRY terms are refused as before. A test asserts `canonical_guard.check` calls
`hard_drop.dropped_role_terms` and contains no literal `"manager"`.

Step 5's exemption is read by calling `hard_drop.restore` with an **empty**
query list, which answers "what is this candidate grounded for" without
touching the real restoration that runs later. Restorations are verified
identical.

## 4. The V3-C6 surface, measured

Instrumented across all 54 personas with output.

| | |
|---:|---|
| 1,774 | canonical replacements |
| 123 | unique canonical results |
| **16** | post-canonical invalid |
| 15 | by `search_operator` |
| 1 | by `role_term_unexempted` |
| 9 | orphan-derived |
| 7 | non-orphan |
| 14 | malformed strings reaching `role_keywords` |
| 15 | personas affected |

**It was not one Git-derived title.** Two distinct malformed strings reach
queries, across 15 personas, and seven of the sixteen invalid replacements come
from the *non-orphan* path — which does run `validated()`, confirming that the
missing validation and the missing predicate are two separate problems.

## 5. Fallback policy, ablated

| | A off | B discard | C fragment |
|---|---:|---:|---:|
| total queries | 379 | 364 | 379 |
| supported | 209 | 201 | 209 |
| forbidden | 37 | 34 | 36 |
| supported precision | 0.551 | 0.552 | 0.551 |
| contaminated personas | 20 | 19 | 20 |
| primary role coverage | 51 | 51 | 51 |
| zero-query personas | 0 | 0 | 0 |
| **malformed final queries** | **14** | **0** | **0** |
| replacements rejected | 0 | 16 | 16 |
| fell back to the fragment | 0 | 0 | 16 |

Both policies remove every malformed query. C looks better on every count: it
loses no supported query and keeps the total flat.

**`discard` ships anyway, and the numbers are not the reason.** The fallback
recovers exactly three distinct fragments:

```
python developer   a real job title, and label-forbidden for that candidate
applied ai         a topic, not a job title
data cloud         a product name, not a job title
```

Two of the three are precisely what `canonical()` exists to replace, and the
final-query contract cannot tell a topic n-gram from a title: both are two
words, operator-free, low-share and drop-term-free. §8's own principle — do not
retain a fragment that was never valid as a standalone query — points the same
way. Choosing C would have been taking a better number in exchange for shipping
the defect the transformation exists to fix.

`SWEEP_CANONICAL_FALLBACK` selects between them and both are tested.

## 6. Nothing else moved

- **Step 6 guard verdicts**: 0 changes not attributable to Step 7. One Step 6
  rejection disappears — `swe_ml`'s `software engineer -python developer` —
  because Step 7 removed it first, which is the permitted attribution. Step 6's
  own rule is untouched and its flag stays off.
- **Step 5 restorations**: 0 of 54 personas differ.
- **Step 4 title gate**: 0 of 56 gates differ, aggregates identical.
- **Step 3**: 0 queries lost that Step 7 did not reject, 0 unexpected additions.
- **With the flag absent** the module is not consulted, and `canonical()`
  without a `trace` argument behaves exactly as before.

The corpus was not touched. A malformed historical title stays historical
evidence; Step 7 protects the query boundary.

The diff is four files: `canonical_guard.py` (new), `local_search.py` (+43 −14,
the optional `trace` parameter threaded through four functions),
`auto-apply/local_profile.py` (+27), and the test file.

## 7. Tests

`auto-apply/tests/test_canonical_guard.py` — 31 tests: the ten required shapes,
the ten real titles a tidiness rule would have deleted, Step 5's exemption in
both directions, idempotence, provenance against the committed corpus, and
off-by-default.

Mutation-tested. Skipping post-canonical validation, validating the fragment
instead of the result, bypassing Step 5's exemption, swapping the operator rule
for a tidiness rule, losing the fragment provenance, and ignoring the fallback
policy each turn the suite red. One proposed mutation — removing the
empty-string branch — is behaviourally equivalent to the one-word rule and is
recorded as equivalent rather than as a pass.

---

## Status

```
BASE SHA:                     d2b5c74
STEP 6 SHA:                   d2b5c74   unmoved, SWEEP_ORPHAN_ROLE_GUARD still off
STEP 7 FLAG:                  SWEEP_CANONICAL_REVALIDATION, off unless set
                              SWEEP_CANONICAL_FALLBACK selects discard|fragment

CANONICAL CALL SITES:         canonicalise (corpus, revalidated by validated())
                              candidates_for_skill (orphan, NOT revalidated)
                              hints_for (title hints, Step 4 governs)
FINAL-QUERY VALIDATORS REUSED: catalogue/wildcard share; one-word fragment rule;
                              hard-drop words, with ROLE terms routed through
                              Step 5. One new rule: search-operator syntax,
                              because no existing validator can express it.
FALLBACK POLICY:              discard

TOTAL CANONICAL REPLACEMENTS: 1,774   (123 unique)
POST-CANONICAL INVALID:       16      (15 search_operator, 1 role_term)
ORPHAN INVALID:                9
NON-ORPHAN INVALID:            7
MALFORMED FINAL QUERIES:      14 -> 0
SUPPORTED QUERY PRECISION:    0.551 -> 0.552
CONTAMINATED QUERIES:         37 -> 34
CONTAMINATED PERSONAS:        20 -> 19
PRIMARY ROLE COVERAGE:        51/54 -> 51/54
TECHNICALIZATION:              4 -> 4
ZERO QUERY:                    0 -> 0
SUPPORTED QUERIES LOST:        8

STEP 3:                       unchanged
STEP 4 TITLE GATE:            identical
STEP 5 RESTORATIONS:          identical
STEP 6 GUARD VERDICTS:        identical, one attributable disappearance
TESTS:                        1,920 across four suites
                              auto-apply 937, sweep 816, bench 125, deploy 42

OLD HOLDOUT USED:             no
READY FOR STEP 7 REVIEW:      yes
```

Eight supported queries are lost, all of them malformed replacements the
classifier happened to place in a supported family. A query that reads as a
negation is not a supported query; it is a search that returns the wrong thing
under a supported-looking name.

Not deployed. The flag is off unless set.
