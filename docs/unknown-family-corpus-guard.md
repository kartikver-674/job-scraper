# Unknown-family corpus guard

Implements the minimal fix recommended by
[docs/corpus-role-tail-audit.md](docs/corpus-role-tail-audit.md) at `6acfaee`,
plus the `MIN_LISTINGS` rename that audit proved necessary.

**Not deployed.** The guard ships behind `SWEEP_UNKNOWN_FAMILY_GUARD`, default
off. `SWEEP_MARKET_COMPOUND_SPLIT` is still off too.

Nothing else changed: no V3 redesign, no taxonomy change, no change to
`family_of`, Fix B, `role_evidence` policy for known families, `title_gate`
policy, or query ranking.

---

## 1. Exact policy

```
reject  IF  the title is corpus-derived
        AND it is not a held or grounded résumé title
        AND role_evidence.family_of(title) is None
        AND (matched_postings < 10 OR matched_companies < 3)
```

Everything else is kept. In particular:

* **A known family is never touched**, however thin. `salesforce administrator`
  on one matched posting, `back end developer` on one, `product owner` on
  three — all survive, because 54 of 73 admitted corpus roles rest on fewer
  than ten postings and most of them are useful.
* **A held or grounded résumé title is never rejected.** Someone whose job is
  "Revenue Operations Manager" can search for it even though the taxonomy has
  never heard of it. The taxonomy's gaps must not be what stops them.
* **The last surviving query is never taken away** — the same fail-open the
  role gate has, for the same reason.
* **A title with no admission record is not judged.** Unmeasured is not
  evidence of thinness.

### Why evidence and not taxonomy

Fail-closed on unknown families removes the same six titles on today's corpus.
It is the wrong rule anyway: it rejects `revenue operations` for being
*unrecognised*, not for being *unsupported*. This rule rejects for thin
evidence, so a profession the taxonomy has not learned — RevOps posted forty
times by twelve employers — survives, and a duplicate advert does not. The two
rules coincide today; the difference is what happens the first time they do
not.

---

## 2. Insertion point

[auto-apply/local_profile.py:258](auto-apply/local_profile.py#L258), between
canonical revalidation and the orphan/hard-drop/role-gate chain.

```
fields_for()                      proposes queries, records admission
  → canonical_guard.revalidate()  rejects sf -data cloud
  → unknown_family_guard          ← HERE
  → orphan_guard
  → hard_drop.restore()
  → role_evidence.filter_queries()
  → render() → title_gate         receives an already-cleaned set
```

This is the narrowest point at which all five facts are known simultaneously:
the proposed title, its matched posting count, its matched employer count, its
provenance (corpus vs held), and `family_of(title)`.

**After canonical on purpose.** `sf -data cloud` is canonical's rejection and
stays canonical's, so one layer claims one removal and the record says which.
`test_it_never_reaches_this_guard` asserts the ordering by reading the source,
so it cannot be silently reversed.

**Before the role gate, and nowhere near `title_gate`.** The point is to stop
an unsupported corpus title becoming a query at all. The free gate then
receives the cleaned role set as a consequence rather than needing its own copy
of the rule.

---

## 3. Provenance handling

`local_search.fields_for` now returns two additive keys. Nothing inside
`local_search` reads either; the guard is the only consumer.

```python
"admission":      {fragment: {postings, companies, weight,
                              corpus_listings, corpus_companies}}
"held_keywords":  [...]     # which role_keywords came from the person's titles
```

`held_keywords` was already computed inside `fields_for` and thrown away.

A canonicalised title is measured on the **fragment that earned it**, via the
`canonical_trace` pairs `fields_for` already emits — so
`Revenue Operations Lead`, replaced from fragment `revops big`, is judged on
`revops big`'s counts.

### Telemetry

Every decision is recorded in `unknown_family_record` on the profile dict,
additive like `hard_drop_record` and `canonical_guard_record`. `render()` emits
only `PROFILE_NAMES`, so nothing reaches `profiles/<name>.py` and no schema bump
is needed. Not surfaced in the UI.

```python
{"query": "quality analyst", "kept": False, "family": None,
 "postings": 1, "companies": 1, "weight": 17.94,
 "min_postings": 10, "min_companies": 3,
 "rule": "unknown_family_thin_evidence",
 "reason": "unknown_family_thin_evidence", "short_of": "postings"}
```

`explain(record)` renders one line per rejection in the résumé's own numbers,
and `local_profile` logs it.

---

## 4. Posting and company count semantics

**Stated plainly, because the audit found the old constant lying about exactly
this.**

| metric | what it counts |
|---|---|
| `postings` | **matched rows** carrying the fragment. **Not deduplicated by job identity.** An advert posted three times counts three. |
| `companies` | **distinct employers among those same matched rows** |
| `weight` | `Σ max(1.0, evidence)` — what the admission floor compares against |
| `corpus_listings` / `corpus_companies` | the fragment **anywhere in the corpus**, matched or not. What `MIN_COMPANIES` reads. |

`product owner` reached a candidate on three rows that were **one advert from
one employer listed three times**. `postings = 3`, `companies = 1`. **The
employer floor is what stops that**, which is why both floors are required
rather than either. No deduplication redesign is in this patch.

`matched_companies` is exact, not inferred: `matching_rows` gained an optional
`with_company=False` parameter that appends the employer of *that* row.
Defaulted off, so every existing caller unpacks the three values it always did.

---

## 5. Constant rename

```python
MIN_LISTINGS = 10          →   MIN_EVIDENCE_WEIGHT = 10
keywords_for(min_listings=…) →  keywords_for(min_weight=…)
here / mine                  →  here / frag_weight
```

The value and every decision it makes are unchanged. The comment now says what
the arithmetic does:

> NOT A POSTING COUNT, despite what the old name said. It is compared against a
> sum of ROW WEIGHTS, where each matched listing contributes
> `max(1.0, evidence)` and a real overlap scores 7–18. One posting carrying
> evidence 17.9 therefore clears a floor of 10 on its own.

**A second name was needed.** `MIN_LISTINGS` was used in two different senses
in the same file: at `keywords_for` it was a weight, and in `hints_for` it was
genuinely `idx[word]["listings"]`. Renaming one name to cover both would have
moved the lie rather than removed it.

```python
MIN_WORD_LISTINGS = MIN_EVIDENCE_WEIGHT * 5   # 50 — a real listing count
```

`bench/derive_search.py` imports both and was updated with them; it is the
bench replica of this same function, so leaving it behind would have split the
rename. No other `MIN_LISTINGS` was touched — `corpus_signal.MIN_LISTINGS`,
`ORPHAN_MIN_LISTINGS` and `bench/vocab.MIN_LISTINGS` all genuinely count
listings and are unrelated.

---

## 6–8. Measurements

Frozen corpus (22,806 listings), live V3 matrix, 15 personas — the 13 in
`bench/people.py` plus both Srishti packagings. Canonical revalidation runs
first, exactly as `local_profile` orders it.

### Flag off — `SWEEP_MARKET_COMPOUND_SPLIT=0`

| | |
|---|---|
| roles removed | **5** |
| personas affected | **5 of 15** |
| zero-query personas | **0** |
| held titles lost | **0** |

| persona | before | after | removed |
|---|---|---|---|
| farida | 9 | 8 | `software engineering professional` |
| hana | 5 | 4 | `ai-native software entwickler (m/w/d)` |
| jonas | 8 | 7 | `software engineering professional` |
| lena | 6 | 5 | `node js` |
| srishti_ba | 10 | 9 | `revenue operations` |
| ada, bhaskar, chen, dmitri, esi, gopal, iris, kwame, mateo, srishti_functional | — | unchanged | — |

### Flag on — `SWEEP_MARKET_COMPOUND_SPLIT=1`

| | |
|---|---|
| roles removed | **7** |
| personas affected | **6 of 15** |
| zero-query personas | **0** |
| held titles lost | **0** |
| worst persona | `srishti_ba` 11 → 9 |

Two additions over flag-off: `srishti_functional` 10 → 9 (`revenue operations`)
and `srishti_ba` also loses `quality analyst`.

### Titles removed

| title | postings | companies | short of | verdict from the audit |
|---|---|---|---|---|
| `software engineering professional` | 6 | 1–2 | both | QUESTIONABLE — names no profession |
| `revenue operations` | 4–6 | 3–5 | postings | PLAUSIBLE — real profession, thin evidence |
| `ai-native software entwickler (m/w/d)` | 3 | 1 | both | WRONG |
| `node js` | 2 | 1 | both | WRONG — a technology, not a role |
| `quality analyst` | 1 | 1 | both | QUESTIONABLE |

`sf -data cloud` is **not** in this list: canonical revalidation removed it
first, as designed.

### Delta from the audit's prediction

The audit predicted **8 removed, 6 personas**. Measured: **7 removed, 6
personas** with the flag on.

**The one-role delta is `sf -data cloud`.** The audit's Phase-3 inventory
counted it as an unknown-family instance; in the real pipeline canonical
revalidation takes it one step earlier, so this guard never sees it. That is
the behaviour the brief asked for — canonical keeps its own rejections — and
8 − 1 = 7 accounts for the difference exactly.

The audit's "worst persona `srishti_functional` 11 → 9" also resolves to 10 → 9
for the same reason: the 11 was a pre-canonical count. The endpoint is
unchanged at 9.

### Zero-query check

`ZeroQueryProtection.test_no_persona_is_left_with_nothing` runs all 13
benchmark personas through `fields_for` and the guard in **both** flag states
and asserts every persona keeps at least one query and every held title. It
passes.

---

## 9. Interaction with Fix 1

The guard must remove the thin unknown-family tail without undoing the
evidence recovery. Personas packaged as `&`-joined skill pairs — the case Fix 1
exists for:

| persona (packaged) | split OFF | split ON | **ON + guard** | guard removed |
|---|---|---|---|---|
| **lena** | **0** | 9 | **8** | `software engineering professional` |
| **hana** | **2** | 11 | **10** | `software engineering professional` |
| **mateo** | 4 | 12 | **11** | `software engineering professional` |
| ada | 9 | 10 | 9 | `software engineering professional` |
| chen | 10 | 10 | 10 | — |
| bhaskar | 6 | 5 | 5 | — |
| **srishti_functional** | **1** | 10 | **9** | `revenue operations` |

**Fix 1's benefit survives intact.** `lena` still goes 0 → 8, `hana` 2 → 10,
`srishti_functional` 1 → 9. The guard removes exactly one thin unknown-family
title per affected persona and nothing else.

The guard behaves identically in both flag states — the same five titles go
with the flag off, plus the two the flag surfaces.

---

## 10. Tests

[auto-apply/tests/test_unknown_family_guard.py](auto-apply/tests/test_unknown_family_guard.py)
— 23 tests, no model, frozen corpus. Every case the brief named:

| # | case | test |
|---|---|---|
| 1 | unknown, 1 posting, 1 company → reject | `test_1_unknown_one_posting_one_company_rejected` |
| 2 | unknown, 9 postings, 10 companies → reject on postings | `test_2_…` |
| 3 | unknown, 20 postings, 2 companies → reject on companies | `test_3_…` |
| 4 | unknown, 10 postings, 3 companies → keep | `test_4_unknown_exactly_on_the_floor_kept` |
| 5 | unknown, 40 postings, 12 companies → keep | `test_5_the_future_revops_case_is_kept` |
| 6 | known family, 1 posting → unchanged | `KnownFamiliesAreUntouched` (×2) |
| 7 | held title, unknown family, 1 posting → keep | `test_7_…` |
| 8 | grounded résumé title the taxonomy cannot place → keep | `test_8_…` |
| 9 | canonical still rejects `sf -data cloud`, and first | `CanonicalKeepsItsOwnRejections` (×2) |
| 10 | no new zero-query persona, both flag states | `ZeroQueryProtection` |

Plus: the flag's default, count semantics (`postings` and `companies` are
integers, `companies ≤ postings`), `stats` being opt-in and decision-neutral,
the rename's own point (an admitted fragment resting on fewer postings than the
floor's number), provenance fields, and fragment-based measurement of a
canonicalised title.

`python unknown_family_guard.py` runs a self-check covering the same ground
with no corpus at all.

### Suites

```
auto-apply/tests    1086 passed, 3 pre-existing loader errors
                    (test_answers, test_apply, test_apply_config — unchanged)
sweep/tests          859 passed, OK
self-checks          unknown_family_guard, local_search, skill_concepts,
                     skill_evidence, bench/derive_search — all green
```

---

## 11. Known residuals

Unchanged by this patch, **by instruction**:

1. **Paid-query vs free-gate asymmetry.** Queries are kept unless proven wrong
   (only `GATED` families are rejected); gate hints dropped unless proven right
   (only `CORE` families survive). 39 query-only titles across 15 personas,
   including `salesforce administrator`.
2. **`family_of("revenue operations")` is `None`.** This guard rejects it for
   thin evidence today; the taxonomy gap is untouched. Once RevOps is learned,
   `revenue operations` returns on its merits — which is the intended
   behaviour.
3. **Multi-family titles.** 210 corpus fragments name two families; `family_of`
   returns one.
4. **`salesforce techno functional consultant`** resolves entirely to
   `functional_consulting`, so its development half is never tested.
5. **`product owner` on one duplicated advert** survives, because `product` is
   a known family. Correct title, bad mechanism.
6. **The global weight floor.** 54 of 73 admitted corpus roles rest on fewer
   than ten postings and this guard touches none with a known family — a
   blanket floor removes three quarters of every search and strands a persona.
7. **No corpus deduplication.** `postings` counts rows, and the employer floor
   is the mitigation.

New, from this work:

8. **`software engineering professional` is the most common rejection** — four
   of the seven, across farida, jonas, hana, lena, mateo, ada. It is a real
   corpus phrase (256 listings, 22 employers) that names no profession. It is
   removed here for thin *matched* evidence rather than for being meaningless,
   which is the right outcome by an incidental route. A taxonomy entry or a
   stop-list would be the direct fix.

---

## 12. Deployment recommendation

**Merge both. Enable the guard first, then the split. Do not deploy from this
commit.**

`SWEEP_UNKNOWN_FAMILY_GUARD` is default-off for the same reason every other
guard in this repo is — `hard_drop`, `orphan_guard`, `canonical_guard`,
`title_gate`, `role_evidence`, `attachment_guard`, `semantic_scope`,
`family_centrality`, `experience_guard` are all flag-gated and all ship off.
With the flag absent the query list is the one v2 produces today.

That is a deliberate choice worth stating, because it has a cost: **the guard
protects nobody while it is off**, and the five titles it removes with the
split flag off are reaching real users now. If you would rather ship it on,
the evidence supports that — 0 zero-query personas, 0 held titles lost, 5 roles
removed across 5 of 15 personas, all five independently classified WRONG or
QUESTIONABLE in the audit. Say so and I will flip the default.

### Order

* **G1 — enable `SWEEP_UNKNOWN_FAMILY_GUARD`.** Measured: −5 roles flag-off,
  −7 flag-on, no persona stranded.
* **G2 — the rename is already in and needs nothing.** No behaviour changed.
* **G3 — then enable `SWEEP_MARKET_COMPOUND_SPLIT`**, with the canary from
  [docs/profile-presentation-stability-fixes.md](docs/profile-presentation-stability-fixes.md)
  (C1) run with the guard already live.

Rollback for either is removing its variable.

### Residual risk accepted

`revenue operations` is removed from both Srishti packagings. It is a real
profession and she has no RevOps evidence, so removing it is right on the
merits — but it is removed by an evidence rule, not by a judgement about her
career, and if the corpus grows it will come back without anyone deciding it
should. That is the intended trade and it is worth knowing before enabling.
