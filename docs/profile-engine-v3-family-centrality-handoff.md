# V3 Fix B — performing an activity is not being in the profession

Implements variant F from `docs/v3-fix-b-family-expansion-measurement.md`.
Fix B only. Fix A untouched, Fix C untouched, ranking and free retrieval
unchanged, no taxonomy refactor.

| | |
|---|---|
| Base | Fix A `376c4e6`, measurement `113896e` |
| New flag | `SWEEP_FAMILY_CENTRALITY_GATE`, **off unless set** |
| Diff | `family_centrality.py` (new), `title_gate.py` **+16 −1**, test file, this handoff |
| Benchmark | `personas/defs.py` sha256[:24] `d67352b07011aac6fdc89d1b` |

---

## 1. The implementation

`family_centrality.families(role, signals, held, own_hints, queries,
FAMILY_TITLES, normalize)` returns `(fragments, core_families, record)`.

```
CORE        the family is named by a grounded held title
            OR  role["stated_target"]["family"] == family  and the family is strong
            OR  the normalised target_field matches the family's vocabulary
                and the family is strong
            -> every fragment in FAMILY_TITLES[family], exactly as before

PERIPHERAL  any other family that is strong on work-mode evidence
            -> only fragments that ALREADY APPEAR in the candidate's evidence
```

`title_gate.build` branches once:

```python
centrality = None
if family_centrality.enabled():
    family_hints, supported, centrality = family_centrality.families(
        role, signals, held, own_raw, data.get("role_keywords") or (),
        FAMILY_TITLES, normalize)
else:
    family_hints, supported = _families(role)
```

`_families` is not edited. The off path is the Fix-A code, reached by the same
call it always was.

## 2. Provenance is one-directional

This is the safety property the brief asked for, and it is structural rather
than defended by a check.

```python
def evidence_text(held, own_hints, queries, normalize):
    parts = ([normalize(h) for h in own_hints]
             + [normalize(q) for q in queries]
             + list(held))
    return " | ".join(p for p in parts if p).lower()
```

`FAMILY_TITLES` is **never** a source. A peripheral family may contribute a
fragment only when that exact fragment already appears in text the candidate's
own document, corpus hints or validated queries produced. A family vocabulary
therefore cannot manufacture the evidence that would readmit the family.

The returned `core_families` is what filters the candidate's own corpus hints,
exactly as the strong-family set did before — so a peripheral family does not
widen that filter either. A test asserts a `recruiter` hint is still dropped
when `hr_recruiting` is peripheral.

## 3. Target-word corroboration, preserved as measured

```python
def target_families(target_field, family_titles, normalize):
    stated = normalize(target_field or "")
    return {family for family, fragments in family_titles.items()
            if any(f in stated or stated in f for f in fragments)}
```

Deliberately **not** `role_evidence.family_of`. The measurement showed that
path is inert: `target_field` holds descriptions — `"business analysis"`,
`"software engineering"` — and `family_of` is built for job titles, so it
returns `None`. Containment is tested in **both** directions, as measured.
`role["stated_target"]["family"]` is still consulted as well, because it was
part of the measured rule even though it is usually `None`.

No taxonomy refactor is included.

## 4. Revalidation

Stack: S3 ON, S4 ON, S5 ON, S6 OFF, S7 ON, S8 ON, negation ext OFF, fallback
`discard`, `SWEEP_ROLE_ATTACHMENT_GUARD=1`, `SWEEP_FAMILY_CENTRALITY_GATE=1`.
54 personas plus the regression candidate, benchmark hash above.

| metric | Fix A | Fix A+B | measured |
|---|---:|---:|---:|
| total gate hints, 54 personas | 2111 | **1586** | — |
| primary visibility | 54/54 | **54/54** | 54/54 |
| forbidden-family admissions | 20 | **17** | 17 |
| gate starvation | 0 | **0** | 0 |
| legacy-floor fallbacks | 0 | **0** | 0 |
| supported queries | 202 | **202** | — |
| forbidden queries | 33 | **33** | — |
| primary role coverage | 52 | **52** | — |
| zero-query personas | 0 | **0** | — |
| total queries | 360 | **360** | — |
| personas whose gate changed | — | 35 | — |
| **software regressions** | — | **0** | 0 |
| **non-software regressions** | — | **0** | 0 |
| regression candidate gate | 109 | **43** | 43 |
| regression candidate bad titles | 4/8 | **0/8** | 0/8 |
| regression candidate wanted titles | 8/8 | **8/8** | 8/8 |

### The one apparent discrepancy, resolved

The measurement reported 2220 → 1629 gate hints; this reports 2111 → 1586.
Both are right and they are the same numbers: the measurement summed **55**
subjects, the revalidation sums the **54 personas** and reports the regression
candidate separately.

```
2111 + 109 = 2220     1586 + 43 = 1629
```

Nothing was tuned. Every other measured figure reproduces exactly.

`primary role coverage` reads 52 rather than the 51 of earlier runs because the
ratified `pm_technical` relabel makes `product_manager` its primary, and its
`product owner` query now counts. That is the benchmark correction showing up,
not an engine change.

### Largest gate reductions

```
plat_sf_admin              60 -> 31      adv_sfba_coordinates_devs  45 -> 21
swe_frontend               87 -> 59      adv_dev_to_pm              79 -> 57
adv_sales_manager          48 -> 22      adv_grad_cs                78 -> 56
```

35 of 55 gates narrowed and **none** lost primary visibility, which is the
result the rule was chosen for: the breadth removed was breadth nobody's
evidence asked for.

### The regression candidate's gate, in full

```
43 hints, all business-analysis / Salesforce-functional shaped:
administrator · analyst salesforce · application consultant ·
application development · application development analyst · business analysis ·
business analyst · business systems analyst · co founder · consultant salesforce ·
crm consultant · data cloud · erp consultant · excel utility · financial services ·
functional consultant · functional consultant / business analyst ·
functional consultant salesforce core · implementation consultant ·
operations analyst · process analyst · product owner · requirements analyst ·
revenue operations · salesforce business analyst · salesforce consultant ·
salesforce cpq · salesforce techno functional consultant ·
senior functional consultant / business analyst · sf data cloud ·
solution consultant · solutions consultant · sport leader ·
sr business systems analyst · swr supplier · systems analyst ·
technical consultant · techno functional · warranty recovery ...
```

No `software engineer`, no `ai engineer`, no `backend`, no `infrastructure`,
no `recruiting`, no `technical support`, no `data analyst`.

## 5. Tests

`auto-apply/tests/test_family_centrality.py` — 19 tests, all ten required
cases: core via held title, core via target words, peripheral support family
contributing nothing, peripheral evidence-specific title surviving, peripheral
unrelated vocabulary excluded, the no-held-title graduate, two core families,
incidental analytics both ways, flag off, flag on. Plus the directional
provenance property, the own-hint filter, `family_of` versus target words, and
that the flag is distinct from Step 4's.

## 6. Off-path identity

```
flag absent, flag "0"        peripheral families still expand fully
                             record carries no "family_centrality" key
_families()                  not edited
```

Verified across the suite: with the flag off, all 54 personas produce the Fix-A
gate and query set, and the totals in the table's Fix A column are the Fix-A
baseline unchanged.

## 7. Known limitations

- **The bare `administrator` fragment.** The regression candidate's own query
  `salesforce administrator` recovers the fragment `administrator` from
  `it_administration`, because the fragment is a substring of the evidence. That
  single hint also admits `Database Administrator`, `System Administrator` and
  `Network Administrator`. It does **not** admit `Infrastructure Engineer`, and
  none of the eight observed bad titles returns — but the breadth is real. The
  cause is that `FAMILY_TITLES["it_administration"]` carries a bare
  `administrator` fragment, and splitting that vocabulary was explicitly out of
  scope. A future refinement could require the fragment to match the evidence
  title more fully rather than by substring.
- **Recovery is substring-based**, matching what was measured. `built an api`
  style variation is not normalised; a title the candidate phrases differently
  from the family vocabulary will not be recovered.
- **`role["stated_target"]["family"]` remains largely inert**, as the
  measurement found. It is retained because it was in the measured rule, not
  because it contributes.
- **35 of 55 gates narrowed.** No persona lost primary visibility on this
  benchmark, but the benchmark is synthetic and single-domain, and the
  regression candidate is the only long, activity-dense document in it.

## 8. Not included, by instruction

`role_evidence.family_of`, `FAMILY_TITLES`, the `it_administration` split,
`score_job`, skill weights, `roles`/`users` handling, market frequencies,
free-source retrieval, profession-fit ranking, query-generation semantics and
Fix A's attachment rule are all unchanged.

---

## Status

```
BASE:                      Fix A 376c4e6, measurement 113896e
FLAG:                      SWEEP_FAMILY_CENTRALITY_GATE, off unless set
                           distinct from SWEEP_CANDIDATE_TITLE_GATE
BENCHMARK:                 personas/defs.py sha256[:24] d67352b07011aac6fdc89d1b
                           pm_technical primary=product_manager (ratified)
GATE HINTS (54 personas):  2111 -> 1586      (+ candidate 109 -> 43 = 2220 -> 1629)
PRIMARY VISIBILITY:        54/54 -> 54/54
FORBIDDEN ADMISSIONS:      20 -> 17
STARVATION / FALLBACKS:    0 / 0  unchanged
SUPPORTED QUERIES:         202 -> 202
FORBIDDEN QUERIES:         33 -> 33
ZERO-QUERY:                0 -> 0
SOFTWARE REGRESSIONS:      0
NON-SOFTWARE REGRESSIONS:  0
CANDIDATE:                 gate 109 -> 43, bad 4/8 -> 0/8, wanted 8/8 kept
OFF PATH:                  Fix-A identical, _families untouched
TESTS:                     1,925 across four suites, all OK
                           auto-apply 1,023 (+19 new), sweep 817, bench 43,
                           deploy 42
```

Not deployed. The flag is off unless set.
