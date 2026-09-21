# Profile presentation stability — the two fixes

Implements Fix 1 and Fix 2 from
[docs/profile-presentation-stability-audit.md](docs/profile-presentation-stability-audit.md),
as confirmed by the production-V3 matrix replay at `1a713ad`.

**Not deployed.** Fix 1 ships behind `SWEEP_MARKET_COMPOUND_SPLIT`, default off.
Fix 2 ships on, because a label that opened no section can only move concepts
towards the section they belong to.

Nothing else changed. Query ranking, `role_evidence` policy, `family_centrality`
policy, `title_gate` policy, hard-drop policy, canonical validation,
`target_field` behaviour, Search Preferences and the experience guard are all
untouched, and the measurements below show each of them still doing exactly
what it did.

---

## 1. Implementation

Four files. 125 lines of production change; the rest is tests.

```
skill_concepts.py                  +98   Fix 1: the market registry and the splitter
skill_evidence.py                  +21   Fix 2: seven heading rows
auto-apply/make_profile.py          +6   Fix 1: the second call site
auto-apply/tests/test_presentation_stability.py   27 tests
```

### Fix 1 — market-aware compound atomisation

`split_compound` refused to atomise an `&`-compound unless **every** half was in
`LOOKUP`, the hand-curated concept table. `LOOKUP` is developer-tool-shaped: it
holds `salesforce` and `soql`, and not `sales cloud`, `service cloud`,
`flow builder`, `reports` or `dashboards` — all of which 22,806 real postings
name as skills.

The rule is unchanged in shape. A half must still be **established by
somebody**; what changed is that "somebody" now includes the market:

```python
def _established(part, known):
    """Is this half a thing somebody already calls a skill?"""
    return _key(part) in LOOKUP or part in known
```

`_established` is the single place either registry is consulted, and it is
reached only through `split_compound`. The parenthetical rule and the separator
rule both call it, so they cannot disagree with each other either.

### Fix 2 — heading recognition

Seven patterns added to `skill_evidence._HEADINGS`, in the existing taxonomy.
No new evidence tier, no new section kind.

---

## 2. The authoritative splitter

The requirement was that `identities()` (before search) and
`make_profile.split_compounds` (in `_finish`, after search) must never
independently decide how to atomise. That drift is audit defect V3-C1, already
fixed once.

**The registry is resolved by the splitter, not by the caller.**

```python
def split_compound(raw, known=None):
    known = market_terms() if known is None else frozenset(known)
```

Neither call site passes anything, so neither chooses — they are identical by
construction rather than by convention. The `known` parameter exists so a test
can pin a vocabulary; production never passes it.

Two supporting details:

* `atomize()` resolves the registry **once for the whole list** and passes it
  down, so a flag flipped mid-loop cannot atomise half a skill set one way and
  half the other.
* `make_profile.split_compounds` does the same, calling
  `skill_concepts.market_terms()` rather than reading the flag itself.

Asserted, not asserted-in-a-comment:

```
OneAuthoritativeSplitter.test_both_call_sites_produce_the_same_atoms
OneAuthoritativeSplitter.test_the_registry_is_resolved_by_the_splitter_not_the_caller
```

The first drives the real `make_profile.split_compounds` and the real
`skill_concepts.atomize` over the fourteen Functional skills and compares the
atom sets. It sets `SWEEP_PROFILE_ENGINE_VERSION=v2` because
`split_compounds` is gated on `skill_concepts.enabled()` — without v2 that path
is a no-op, which is itself worth knowing.

---

## 3. Vocabulary source

`market_terms()` reads **`corpus_signal.frozen_frequencies()`** — the committed
`data/skill_market_frequencies.json`.

| | frozen frequency table | `local_search.frozen_market().vocab` |
|---|---|---|
| terms | **367** | **367** |
| identical sets | — | **yes**, asserted |
| on disk | 15 KB plain JSON | 190 KB gzip of 22,806 rows |
| module dependencies | stdlib only | the whole corpus machinery |

Both are the same frozen market vocabulary. The table is chosen because
`skill_concepts` is a leaf that fifteen modules import, and importing
`local_search` from it would drag corpus loading, indexing and `config` into
every one of them. There is no cycle today — `local_search` imports nothing from
`skill_concepts` — but the coupling is the problem, not the cycle.

`VocabularySource.test_the_two_frozen_sources_agree` fails if they ever diverge,
which would mean search and splitting disagreed about what a skill is.

Properties:

* **No network.** A committed data file.
* **No dynamic corpus rebuild.** Nothing reads `output/`.
* **Loaded once**, cached in `_MARKET_TERMS`, returned as an immutable
  `frozenset`.
* **Deterministic.** Same checkout, same answer.
* **Degrades to empty.** A missing or unreadable table means the market
  abstains, which is exactly the flag-off behaviour.

---

## 4. Flag behaviour

```
SWEEP_MARKET_COMPOUND_SPLIT     default OFF
```

With the flag absent, `market_terms()` returns `frozenset()` before touching
the filesystem, and every decision is the one `LOOKUP` alone made.

| input | flag OFF | flag ON |
|---|---|---|
| `sales cloud & service cloud` | *(whole)* | `sales cloud`, `service cloud` |
| `reports & dashboards` | *(whole)* | `reports`, `dashboards` |
| `agile & sdlc` | *(whole)* | `agile`, `sdlc` |
| `research & development` | *(whole)* | ***(whole)*** |
| `foo & bar` | *(whole)* | ***(whole)*** |
| `salesforce administration & configuration` | *(whole)* | ***(whole)*** |
| `gap analysis & process improvement` | *(whole)* | ***(whole)*** |
| `client & stakeholder management` | *(whole)* | ***(whole)*** |
| `c++` · `ci/cd` · `node.js` · `socket.io` · `asp.net` · `c#` · `.net` | *(whole)* | ***(whole)*** |

`agile & sdlc` is the mixed case and the reason `_established` takes both
registries: `agile` is in `LOOKUP`, `sdlc` only in the market, and the rule is
that *every* half needs *an* establishment — not that they all come from the
same place.

Byte-identity with the flag off is asserted directly
(`MarketSplit.test_flag_off_*`) and confirmed end to end: condition **A** below
reproduces the `1a713ad` replay exactly.

---

## 5. Heading mappings

| label | opens | was |
|---|---|---|
| `TOOLS & TECHNOLOGIES`, `TOOLS AND TECHNOLOGIES`, `TOOLS & PLATFORMS` | `skills` | **None** |
| `EXPERTISE`, `AREAS OF EXPERTISE`, `AREA OF EXPERTISE` | `skills` | **None** |
| `SKILL SET`, `SKILLS SET` | `skills` | **None** |
| `CAREER HISTORY`, `EMPLOYMENT HISTORY` | `work` | **None** |
| `CAREER OBJECTIVE` | `summary` | **None** |

Three implementation notes that are easy to get wrong:

* **`CAREER OBJECTIVE` is tried before the WORK row**, whose second group
  contains `career`. It is a summary, not an employment section.
* **`CAREER HISTORY` needed its own row.** The existing WORK pattern matches
  `history` alone and `career` alone, but its prefix group
  (`professional|work|relevant|industry`) admits neither word, so the pair
  matched nothing.
* **The tools row avoids a literal `&` requirement**
  (`tools?\s*(?:[&+/]|and)?\s*…`). `_SPACELESS` is generated from these
  patterns by stripping whitespace classes, and it is matched against text that
  `_FOLD` has already stripped of punctuation — so a pattern containing `&`
  would silently never match a PDF-broken heading.

`TECHNOLOGIES` alone already matched. The module's own docstring cites
`"T ools & Platforms:"` as a broken heading it handles; it did not, and now
does.

Nothing else moved: `EDUCATION`, `CERTIFICATIONS`, `SELECTED PROJECTS`,
`RELEVANT COURSEWORK` still classify as before, and a stack line with content
after its colon (`- Technologies: Redis, MongoDB`) is still not a heading.

---

## 6. A / B / C / D measurements

All under the **live V3 matrix**, set before the first import:

```
SWEEP_ROLE_EVIDENCE=1                     SWEEP_CANONICAL_REVALIDATION=1
SWEEP_CANDIDATE_TITLE_GATE=1              SWEEP_CANONICAL_FALLBACK=discard
SWEEP_CANDIDATE_HARD_DROP=1               SWEEP_SEMANTIC_SCOPE=1
SWEEP_ORPHAN_ROLE_GUARD=0                 SWEEP_SEMANTIC_SCOPE_NEGATION_VERBS=0
SWEEP_ROLE_ATTACHMENT_GUARD=1             SWEEP_FAMILY_CENTRALITY_GATE=1
SWEEP_PROFILE_ENGINE_VERSION=v2
```

```
A  neither fix        B  compound only        C  heading only        D  both
```

A and B revert Fix 2 **in the harness** by rebuilding `_HEADINGS` and its three
derived regexes without the new rows. Production code is never modified.

| | concepts | in vocab | matching rows | roles | title hints | title gate | CORE families |
|---|---|---|---|---|---|---|---|
| **Functional A** | 14 | **1** | **0** | **1** | **0** | **10** | functional_consulting |
| **Functional B** | 17 | 6 | 330 | **10** | 40 | 32 | functional_consulting |
| **Functional C** | 14 | 1 | 0 | 1 | 0 | 10 | functional_consulting |
| **Functional D** | 17 | **6** | **330** | **10** | **40** | **32** | functional_consulting |
| **BA A** | 11 | 6 | 308 | 9 | 38 | 37 | business_analysis, functional_consulting |
| **BA B** | 12 | 7 | 398 | 10 | 40 | 41 | business_analysis, functional_consulting |
| **BA C** | 11 | 6 | 308 | 9 | 38 | 37 | business_analysis, functional_consulting |
| **BA D** | 12 | **7** | **398** | **10** | **40** | **41** | business_analysis, functional_consulting |

**A reproduces `1a713ad` exactly** — 1 role against 9, gate 10 against 37.

### The real pair, condition by condition

| | role J | hint J | gate J | skill J | mean \|Δw\| | max Δw |
|---|---|---|---|---|---|---|
| **A** neither | **0.000** | **0.000** | 0.237 | 0.347 | 0.882 | **3** |
| **B** compound only | 0.667 | 0.818 | 0.738 | 0.362 | 0.647 | 3 |
| **C** heading only | 0.000 | 0.000 | 0.237 | 0.347 | 0.647 | **2** |
| **D** both | **0.667** | **0.818** | **0.738** | 0.362 | **0.529** | **2** |

The two fixes are orthogonal and both are needed: **B moves roles and hints, C
moves weights, D gets both.**

### Shared-concept weights, Functional résumé

| concept | A | B | C | **D** | moved by |
|---|---|---|---|---|---|
| salesforce | BACKGROUND 1 | BACKGROUND 1 | SUPPORTING 2 | **SUPPORTING 2** | heading |
| flow builder | BACKGROUND 2 | BACKGROUND 2 | SUPPORTING 3 | **SUPPORTING 3** | heading |
| agile | BACKGROUND 1 | SUPPORTING 2 | BACKGROUND 1 | **SUPPORTING 2** | split |
| sdlc | BACKGROUND 2 | SUPPORTING 3 | BACKGROUND 2 | **SUPPORTING 3** | split |
| sales cloud | BACKGROUND 2 | SUPPORTING 3 | SUPPORTING 3 | **SUPPORTING 3** | either |
| service cloud | BACKGROUND 2 | SUPPORTING 3 | SUPPORTING 3 | **SUPPORTING 3** | either |
| reports · dashboards | CORE 4 | CORE 4 | CORE 4 | CORE 4 | — |
| uat · gap analysis · go-live | CORE 5 | CORE 5 | CORE 5 | CORE 5 | — |

Six concepts move, all upward, all towards the evidence the document actually
carries. **Nothing is demoted.**

### Shared-concept weights, BA résumé

**Identical in all four conditions** — every weight unchanged. Its headings were
already recognised and its skills already atomic, so neither fix has anything to
act on. That is the control.

### Roles under D

```
Functional  salesforce administrator · salesforce business analyst ·
            functional consultant · salesforce techno functional consultant ·
            functional consultant, salesforce core · consultant salesforce ·
            salesforce sales cloud · product owner · revenue operations ·
            salesforce consultant

BA          salesforce administrator · salesforce business analyst ·
            salesforce functional consultant · salesforce techno functional
            consultant · functional consultant, salesforce core ·
            salesforce sales cloud · consultant salesforce ·
            revenue operations · product owner · quality analyst
```

The held title is kept in both (`functional consultant` /
`salesforce functional consultant`), which is the one role that should differ.

---

## 7. V3-on regression results

**No V3 layer was changed, and every one of them still fires.**

| guard | under D | evidence |
|---|---|---|
| role-evidence gate | **still rejects `salesforce engineer`** on the BA résumé | `missing_work_modes: ['development']` — platform evidence exists, substantive development ownership does not |
| canonical revalidation | **still rejects `sf -data cloud`** on the Functional résumé | leading hyphen is a board negation operator |
| hard-drop restoration | nothing restored, as before | unchanged |
| orphan guard | off, as the matrix specifies | unchanged |
| family centrality | CORE families unchanged in every condition | Functional `functional_consulting`; BA `business_analysis, functional_consulting` |
| `target_field` | unchanged in every condition | unchanged |

`AtomisationIsNotPermission.test_salesforce_engineer_is_still_rejected` pins
this as a unit test: with the full role-evidence stack on and the compound flag
on, a document with Salesforce platform evidence and no construction verb still
has `salesforce engineer` vetoed, with `development` named as the missing mode.

**Atomisation is evidence recovery, not permission.** The recovered atoms give
the corpus more to retrieve on; they do not give the candidate a work mode.

### Headline behaviour is unchanged

| headline | roles | role J | gate | gate J (D) | gate J (`1a713ad`) | CORE families |
|---|---|---|---|---|---|---|
| `SALESFORCE BUSINESS ANALYST` | 10 | — | 41 | — | — | business_analysis, functional_consulting |
| `BUSINESS ANALYST` | 10 | **1.000** | 39 | 0.951 | 0.946 | business_analysis, functional_consulting |
| `FUNCTIONAL CONSULTANT` | 10 | **1.000** | 34 | 0.786 | 0.763 | functional_consulting |
| `SALESFORCE FUNCTIONAL CONSULTANT` | 10 | **1.000** | 34 | 0.786 | 0.763 | functional_consulting |

Same shape as the replay measured before the patch: roles perfectly invariant,
the gate proportionately sensitive. The patch did not change headline
behaviour.

---

## 8. Invariance results

Pure-presentation variants, one canonical body, only the named label changed.

| variant | role J before | **role J after** | max \|Δw\| before | **max \|Δw\| after** |
|---|---|---|---|---|
| `PROFESSIONAL EXPERIENCE` → `CAREER HISTORY` | 1.000 | **1.000** | **2** | **0** |
| `TECHNICAL SKILLS` → `TOOLS & TECHNOLOGIES` | 1.000 | **1.000** | **1** | **0** |
| `CORE COMPETENCIES` → `SKILLS` | **0.091** | **0.538** | 1 | 1 |

**Both heading variants are now exactly invariant** — every weight identical,
`mean |Δw| 0.000`. The eight concepts `CAREER HISTORY` used to demote by two
bands no longer move at all.

Beyond the aggregate threshold, exact equivalence is asserted deterministically
in `HeadingEquivalence`: thirteen label substitutions, each required to produce
an identical tier map, plus `test_work_evidence_reaches_core_under_every_work_synonym`.

### The `SKILLS` variant still fails the threshold, and this is expected

`role_jaccard 0.538` against the proposed `≥ 0.90`. **But the collapse is
gone**: that variant now yields **10 roles instead of 1**, on 94 matching rows.
Only three of its concepts are in the market vocabulary — `acceptance
criteria`, and `agile` / `sdlc` recovered by the split — and without the split
a single in-vocabulary term cannot satisfy `matching_rows(need=2)` at all,
which is why the pre-fix figure is zero.

The residual is **root cause 2** — when a document has two skill-shaped blocks,
which one the model reads depends on the labels. That is model
section-selection in `local_extract.read`, upstream of both fixes and out of
scope for this patch. Fix 1 removes its catastrophic amplification; it does not
remove the preference itself.

### Frozen corpus

Every corpus assertion in the test file runs against
`local_search.frozen_market()`, never `output/`. Corpus growth cannot turn a
passing test into a failing one — the 9-vs-11 discrepancy that made the first
audit disagree with the reported screenshot cannot recur in the suite.

---

## 9. Remaining known issues

Carried forward, unaddressed by this patch **by instruction**:

1. **Paid-query vs free-gate disagreement.** `title_gate`'s
   `own_hints_dropped` discards `salesforce administrator` and
   `salesforce sales cloud` while the query list keeps them. Still true under
   D. Separate audit.
2. **`family_of` returns `None` for `revenue operations`**, so an unrecognised
   family is never rejected. Separate audit.
3. **`salesforce techno functional consultant` resolves to
   `functional_consulting`**, so its development claim never reaches the gate.
   Separate audit.
4. **`MIN_LISTINGS = 10` is evidence-weight, not rows**, so a 3-posting
   fragment clears it. Separate audit.
5. **`product owner` corpus-tail behaviour.** Separate audit.

Found during this work, also unaddressed:

6. **Root cause 2 — model section-selection.** Section 8. The residual
   `role_jaccard 0.538` on the `SKILLS` variant. Not fixable in deterministic
   code; it is a prompt or extraction-schema question.
7. **More matching rows means a longer corpus tail.** The BA résumé gains
   `quality analyst` under the flag (9 roles → 10), from the extra `reports` /
   `dashboards` atoms raising matching rows 308 → 398. `family_of("quality
   analyst")` returns **`None`**, so it survives through exactly the fail-open
   in item 2 — not through any evidence. The `SKILLS` variant similarly picks
   up `application development analyst`, `sr. business systems analyst` and
   `application support engineer`.

   This is not a defect the splitter introduced — it is items 2, 4 and 5
   becoming more visible once the corpus has more to retrieve on. It is the
   strongest reason to canary Fix 1 rather than ship it on, and the reason C2
   below exists.
8. **`make_profile.split_compounds` is a no-op without v2.** Discovered writing
   the splitter-agreement test. Correct today — the market split is a v2-era
   feature — but it means the two call sites are only comparable under v2.

---

## 10. Deployment recommendation

**Ship Fix 2 now. Hold Fix 1 behind its flag and canary it.**

### Fix 2 — ship

* Seven labels that previously opened **no** section now open the one they
  name. The change is one-directional: a concept can only move from a weaker
  section to its real one, never the reverse. Measured: six concepts moved on
  the Functional résumé, all upward, none demoted.
* Zero blast radius on the existing suite. The only tests that changed
  behaviour were the four written in the audit to fail loudly when the defect
  was fixed.
* The BA résumé — a document with no unrecognised headings — is bit-identical
  in all four conditions.
* It makes two pure-presentation variants exactly invariant, which is the
  product behaviour the audit asked for.

### Fix 1 — flag on, canary first

The flag is off and the engine is byte-identical with it absent, so the patch
is safe to merge as it stands. Before turning it on:

* **C1. One real sweep with the flag on, read the page.** The measurement here
  is two résumés. The splitter now trusts 367 market terms it did not trust
  before, and the failure mode — a compound split into two atoms that retrieve
  a profession the candidate is not in — would show up as a wrong query, not as
  an error.
* **C2. Decide on the corpus tail (item 7).** More matching rows means more
  tail, and the new entries — `quality analyst`, `application support engineer`
  — arrive through the unrecognised-family fail-open, not through evidence.
  That is Fix 4 from the audit plus the `family_of` gap; one or both should
  land before this flag does, or the canary should be read with them in mind.
* **C3. Turn it on with `SWEEP_PROFILE_ENGINE_VERSION=v2` already pinned.**
  `make_profile.split_compounds` is gated on v2 (item 8); with the market flag
  on and v2 off, `identities()` would atomise and the `_finish` path would not.
  Both are reached in production today only under v2, but the combination is
  worth stating rather than discovering.

Recommended rollout order: merge both, deploy with Fix 2 live and
`SWEEP_MARKET_COMPOUND_SPLIT` absent, run C1 and C2, then enable the flag.

Rollback for Fix 1 is removing the variable. Rollback for Fix 2 is a revert of
seven lines in one table.

---

## Test suites

```
auto-apply/tests    1063 passed, 3 pre-existing loader errors
                    (test_answers, test_apply, test_apply_config — unchanged)
sweep/tests          859 passed, OK
```

27 of those tests are new, in
[auto-apply/tests/test_presentation_stability.py](auto-apply/tests/test_presentation_stability.py):
the flag's on/off contract, the two registries, the authoritative-splitter
agreement, vocabulary-source equivalence, shadowing, the frozen-corpus search
regression, V3 non-relaxation, the heading table, and thirteen heading
equivalences.
