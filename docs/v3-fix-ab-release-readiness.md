# V3 Fix A + Fix B — release readiness

Verification only. No engine behaviour changed, no threshold tuned, Fix C not
started, nothing deployed.

| | |
|---|---|
| Fix A | `376c4e6` — `SWEEP_ROLE_ATTACHMENT_GUARD` |
| Fix B | `5bd8051` — `SWEEP_FAMILY_CENTRALITY_GATE` |
| HEAD | `5bd8051`, working tree clean |
| Benchmark | `personas/defs.py` sha256[:24] `d67352b07011aac6fdc89d1b` (ratified `pm_technical` relabel) |

---

## 1. Production diff — exactly four engine files

```
attachment_guard.py     new,  132 lines      Fix A rule
role_evidence.py        +9  −0               Fix A wiring
family_centrality.py    new,  156 lines      Fix B rule
title_gate.py           +17 −1               Fix B wiring
```

Plus two test files and four documents. Verified **unmoved** against the
pre-Fix-A commit `113896e`:

```
skill_evidence  canonical_guard  hard_drop  orphan_guard  semantic_scope
local_search    config           scraper    skill_concepts
auto-apply/local_profile         auto-apply/make_profile
sources/__init__                 sources/ats
data/  (corpus)                  FAMILY_TITLES definition
```

So ranking (`score_job`), free retrieval, Apify execution, query generation
outside Fix A's attachment rule, the family taxonomy and the market
frequencies are all untouched.

## 2. Flag defaults

Probed in a clean subprocess with every `SWEEP_*` variable stripped:

| environment | A | B |
|---|---|---|
| both absent | False | False |
| both `=0` | False | False |
| both `=1` | True | True |
| `A=1` only | **True** | False |
| `B=1` only | False | **True** |

Both default off, and neither turns the other on.

## 3. Flag matrix — independent rollback confirmed

Regression candidate, full V3 stack, all four combinations:

| A | B | gate | bad titles | wanted titles | errors |
|---|---|---:|---:|---:|---|
| 0 | 0 | 147 | 8/8 | 8/8 | none |
| 0 | 1 | 46 | 2/8 | 8/8 | none |
| 1 | 0 | 109 | 4/8 | 8/8 | none |
| **1** | **1** | **43** | **0/8** | **8/8** | none |

No combination errors, so **B does not require A and A does not require B** —
either can be rolled back alone. Each fix is independently useful (A alone
4/8, B alone 2/8) and only together do they reach 0/8. All eight wanted titles
survive in every combination.

## 4. Test suites

```
auto-apply   1,023   OK      (+22 Fix A, +19 Fix B)
sweep          817   OK
bench           43   OK
deploy          42   OK
                       total 1,925
```

Matches the last known total. `bench` reads 43 rather than the historical 125
because the 82-test role-family-adapter suite is holdout/evaluation-only code
that was deliberately never promoted to `main`.

## 5. Intended A+B result, on the ratified benchmark

| metric | Fix A only | **A + B** | target |
|---|---:|---:|---:|
| primary visibility | 54/54 | **54/54** | 54/54 |
| forbidden-family admissions | 20 | **17** | 17 |
| gate starvation | 0 | **0** | 0 |
| legacy-floor fallbacks | 0 | **0** | 0 |
| supported queries | 202 | **202** | 202 |
| forbidden queries | 33 | **33** | 33 |
| zero-query personas | 0 | **0** | 0 |
| software regressions | — | **0** | 0 |
| non-software regressions | — | **0** | 0 |
| candidate gate | 109 | **43** | 43 |
| candidate bad titles | 4/8 | **0/8** | 0/8 |
| candidate wanted titles | 8/8 | **8/8** | 8/8 |

Every target met. Nothing was forced.

## 6. Software positive controls

The point of these is that a broad engineering gate is **correct** for these
people and must survive.

| persona | gate | primary visible | queries | supported roles |
|---|---|---|---|---|
| `swe_frontend` | 87 → 59 | True → **True** | 9 → 9, **identical** | 7 → 7 |
| `swe_backend` | 67 → 57 | True → **True** | 9 → 9, **identical** | 3 → 3 |
| `swe_data_eng` | 72 → 64 | True → **True** | 8 → 8, **identical** | 2 → 2 |

`swe_backend`'s gate after A+B still contains the full engineering vocabulary:

```
backend · backend engineer · back end · back end developer · developer ·
devops · android · ai engineer · engineering manager · engineer java ·
associate engineer · application development ...
```

`swe_frontend` also *loses* a forbidden family (`business_analyst`). Nothing
collapsed.

## 7. Non-software controls

| persona | gate | primary visible | forbidden families | queries |
|---|---|---|---|---|
| `ba_generic` | 33 → 23 | True → **True** | 1 → 1 | identical |
| `mkt_digital` | 12 → 12 | True → **True** | 0 → 0 | identical |
| `gtm_ae` | 61 → 43 | True → **True** | **3 → 0** | identical |
| `proj_manager` | 15 → 15 | True → **True** | 0 → 0 | identical |

`gtm_ae` is the clearest independent confirmation: an Account Executive stops
being admitted for `business_analyst`, `data_analyst` and `salesforce_ba`
postings, while keeping all nine of its own supported roles.

**Every query set is byte-identical.** Fix B changes the title gate only; it
never alters query generation. Fix A changes queries only through the role
record, and on these personas it did not.

## 8. Where each flag must exist — they are NOT the same

Traced, not assumed.

**Fix A — Render only.** `attachment_guard` is consumed by
`role_evidence._governed_hits`, reached only from `role_evidence.build()`,
whose single caller is `auto-apply/local_profile.py:217`, reached only from
`make_profile.generate()` at `sweep/app.py:258`. `title_gate` imports
`role_evidence` but uses `_PLATFORM_RX` and `family_of`, never `build`. Setting
A on Oracle is inert.

**Fix B — Render AND Oracle.** `family_centrality` is consumed by
`title_gate.build`, reached from `make_profile._title_gate:854` inside
`make_profile.render()` — and `deploy/sweep_worker.py:197` calls
`make_profile.render()` itself. Fix B inherits Step 4's trap exactly.

Measured, not inferred:

```
role_evidence present & JSON-safe in the worker payload : True
role_keywords present & JSON-safe                       : True
Render gate (B on)                                      : 43
Worker gate (B on)                                      : 43   identical
Worker gate (B OFF)                                     : 109  <- the drift case
```

**If Fix B is set on Render but not Oracle, the worker silently re-renders
every profile with the 109-hint gate and undoes the fix.** Set both flags on
both hosts anyway: A is inert on Oracle, and identical configuration prevents
drift if the worker ever gains a generation path.

## 9. Deployment plan — NOT APPLIED

Add to **Render** (`sweep-beta`) and to **Oracle**
(`/etc/sweep-worker/env`, then `systemctl restart sweep-worker`):

| variable | current | release | rollback |
|---|---|---|---|
| `SWEEP_ROLE_ATTACHMENT_GUARD` | unset → off | `1` | `0`, or remove |
| `SWEEP_FAMILY_CENTRALITY_GATE` | unset → off | `1` | `0`, or remove |

Unchanged, and explicitly **not** to be touched: `SWEEP_PROFILE_ENGINE_VERSION`
(never on the worker), `SWEEP_ORPHAN_ROLE_GUARD=0`,
`SWEEP_SEMANTIC_SCOPE_NEGATION_VERBS=0`, `SWEEP_CANONICAL_FALLBACK=discard`.
No Fix C variable exists and none should be introduced.

Apply both hosts together.

## 10. Runtime verification

Run in the deployed checkout on each host. Uses the module APIs, prints no
secrets:

```
python -c "import sys; sys.path[:0]=['.','auto-apply']; import role_evidence,title_gate,hard_drop,orphan_guard,canonical_guard,semantic_scope as s,attachment_guard as a,family_centrality as b; print('S3',role_evidence.enabled(),'S4',title_gate.enabled(),'S5',hard_drop.enabled(),'S6',orphan_guard.enabled(),'S7',canonical_guard.enabled(),'S8',s.enabled(),'neg',s.negation_verbs(),'fallback',canonical_guard.FALLBACK,'A',a.enabled(),'B',b.enabled())"
```

Required, verified locally under the release environment:

```
S3 True S4 True S5 True S6 False S7 True S8 True neg False fallback discard A True B True
```

If either host differs, stop and do not send traffic.

## 11–13. Canary procedure

**Canary 1 — regression / non-software.** The privacy-safe fixture in
`auto-apply/tests/test_attachment_guard.py`, or the controlled résumé. Through
the real beta path: upload → derive → review → approve. Verify the rendered
`profile.py` carries the `# V3 Step 4` provenance comment, the gate is ≈43
hints, and it contains **no** `software engineer`, `ai engineer`, `backend`,
`infrastructure`, `recruiting`, `technical support` or `data analyst`; and that
Business Analyst, Salesforce Business Analyst, Functional Consultant and
Salesforce Administrator all remain.

**Canary 2 — software positive control.** A known software résumé (`ada`, or
`swe_backend`-shaped). Verify the gate stays broad (≈57–59 hints), software
titles remain searchable, and the sweep completes.

**Free Sweep is mandatory for canary 1.** The original defect appeared only
through whole-board enumeration, which no offline gate test exercises. Run the
real free search to the results page and confirm the wrong-profession titles no
longer appear. An offline gate check is necessary but not sufficient here.

**Paid path smoke.** Confirm profile generation and title gating still work for
the Apify path — the gate is shared, so a single cheap run is enough. No broad
audit and no unnecessary credit spend.

## 14. Rollback

Fastest, no code revert:

```
SWEEP_ROLE_ATTACHMENT_GUARD=0
SWEEP_FAMILY_CENTRALITY_GATE=0
```

on **both** hosts, then redeploy Render and `systemctl restart
sweep-worker`. Verification then returns `A False B False`, and the live V3
behaviour is restored — the off path is the Fix-A code and `_families()` was
never edited. The flags roll back independently if only one is at fault
(§3 shows all four combinations run clean).

Git rollback point if code must be reverted: **`113896e`** (the last commit
before Fix A). Fix A alone is `376c4e6`.

## 15. Known residuals, carried forward

- **The bare `administrator` fragment.** The candidate's own
  `salesforce administrator` query recovers `administrator` from
  `it_administration`, which also admits `Database Administrator`,
  `System Administrator` and `Network Administrator`. It does **not** admit
  `Infrastructure Engineer`, and none of the eight observed bad titles returns.
  Caused by `FAMILY_TITLES["it_administration"]` breadth; not part of Fix B and
  **not to be silently modified in this release**.
- **Fix C / generic-concept inflation.** `roles` and `users` still score 5 each
  and still supply most of the score on wrong-profession jobs. Blocked on
  document-frequency data the corpus cannot currently produce.
- **Free-search whole-board retrieval.** The free path still never uses the
  search queries; the title gate remains the only profession filter there.
- **Benchmark is synthetic**, and `defs.py` is gitignored, so the ratified
  relabel is not verifiable from git history.

---

## Recommendation

**READY TO DEPLOY.**

Evidence: the production diff is four engine files with every adjacent module
verified unmoved; both flags default off and are independently reversible
across all four combinations with no errors; 1,925 tests pass; the intended
A+B stack hits every target on the ratified benchmark with **zero** software
and **zero** non-software regressions, zero starvation and zero fallbacks;
three software controls keep their broad gates and identical query sets; and a
second non-software control (`gtm_ae`) independently loses three forbidden
families while keeping all nine supported roles.

Two conditions, both configuration rather than code:

1. **Set `SWEEP_FAMILY_CENTRALITY_GATE=1` on Oracle as well as Render.**
   Measured: Render-only leaves the worker re-rendering at 109 hints and
   silently undoes Fix B.
2. **Run canary 1 through a real Free Sweep**, not an offline gate check. The
   defect was only ever visible through whole-board enumeration.

Not deployed. Both flags remain off unless set.
