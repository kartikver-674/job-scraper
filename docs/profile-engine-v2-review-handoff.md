# Profile Engine v2 — review handoff packet

For an independent reviewer. This reports what was built, not a case for it.
Incomplete work, weak evidence, deviations and failed experiments are in here
deliberately.

Prepared at branch HEAD `69d54f0`. Nothing is deployed. No code changed while this
packet was written.

---

## 1. Frozen state

| | |
|---|---|
| Branch | `feat/profile-engine-v2` |
| Branch HEAD | `69d54f0cb649130ed8a0308896c484a33973f3fe` |
| Base the work started from | `05c26f4c6e60ab034b14c8363361b2f56abb9f13` |
| Current `main` | `05c26f4c6e60ab034b14c8363361b2f56abb9f13` |
| Merge-base | `05c26f4` — identical to main |
| **Needs rebasing?** | **No.** `git log HEAD..main` is empty; main has not moved. |
| Working tree | clean apart from two untracked docs (below) |

`git status --short`:

```
?? docs/profile-engine-v2-baseline.md
?? docs/resume-skill-engine-audit.md
```

Both are **untracked and were never committed** — the audit itself and the Step 0
baseline note. They predate the branch's commits and were left untracked
deliberately (the audit references personal résumé content in its evidence paths).
A reviewer may want them committed; that is a decision, not an oversight.

`git log --oneline --decorate main..HEAD`:

```
69d54f0 (HEAD -> feat/profile-engine-v2) docs: the deployment and rollback plan, prepared not executed
3edaf4e feat: one switch for the engine version, and a schema stamp on profiles
429bb7c eval: five conditions, sixteen personas, and one blocking regression
133718f feat(search): the résumé proposes the career, the market only stocks it
625a85a feat(skills): importance from evidence, with the market kept separate
e726565 feat(skills): one real concept, one scoring contribution
f4428cb fix: three model-answer boundaries that failed unsafely
3c57ddd fix(profile): a résumé cannot write Python into the profile it generates
790ea21 fix(extract): check the employment rows against the document too
9d82dbc fix(review): a common skill is not a skill you do not have
33b9680 fix(search): the same rows in a different order are the same market
```

### Commit → step

| Step | Commits |
|---|---|
| 0 — baseline | none (untracked `docs/profile-engine-v2-baseline.md`) |
| 1 — correctness/safety | `33b9680`, `9d82dbc`, `790ea21`, `3c57ddd`, `f4428cb` |
| 2 — canonical concepts | `e726565` |
| 3 — evidence importance | `625a85a` |
| 4 — role construction | `133718f` (**held, flag off**) |
| 5 — evaluation | `429bb7c` |
| 6 — migration | `3edaf4e`, `69d54f0` |

Nothing amended or squashed.

---

## 2. Requirement traceability matrix

| Step | Requirement | Status | Implementation | Tests | Commit | Notes |
|---|---|---|---|---|---|---|
| 0 | Read audit, confirm frozen corpus on main | DONE | — | — | — | Verified all 16 §24 source refs resolve |
| 0 | Record baseline (commit, tests, engine, model, corpus, outputs, failures) | DONE | `docs/profile-engine-v2-baseline.md` | 129 tests reproduced exactly | untracked | **File is untracked** |
| 0 | Preserve résumé as dev regression only | DONE | `output/resume-skill-audit/` | — | — | gitignored |
| 0 | Don't rewrite benchmark answers | DONE | — | 52-doc unchanged | — | |
| 1A | Row-order invariance | DONE | `local_search.rank_title`, `canonical`, `candidates_for_skill` | `test_title_corpus.TestRowOrderCannotChangeTheAnswer` (8) | `33b9680` | Changed the résumé's own keywords — documented |
| 1B | Low-weight grounded skills not auto-deleted | DONE | `sweep/templates/_weights.html`, `sweep/app.py` | `test_app.test_no_skill_arrives_pre_selected_for_deletion` | `9d82dbc` | |
| 1C | Employment rows grounded | DONE | `local_extract.check_employment`, `row_problems` | `test_employment_grounding` (27) | `790ea21` | Year-level date grounding only |
| 1D | Impossible/reversed dates | DONE | `local_extract.row_problems` | `test_a_reversed_range_is_excluded_from_the_arithmetic` | `790ea21` | |
| 1E | Trainee/promotion ambiguity not faked | DONE | `local_extract.ambiguous_span` | `test_the_ambiguity_is_flagged_rather_than_guessed` | `790ea21` | Flags; still counts 0 as floor |
| 1F | Prose cannot escape data boundary | DONE | `make_profile._prose` | `test_the_audit_payload_no_longer_creates_a_statement` | `3c57ddd` | |
| 1G | AST allowlist | DONE | `make_profile.check_module` | `test_an_extra_assignment_is_refused` +9 | `3c57ddd` | Generated source only |
| 1H | Malformed/truncated model response | DONE | `inference.LocalOllama.generate`, `local_extract._object` | `test_done_reason_length_is_refused` | `f4428cb` | |
| 1 | Numeric bounds on weights | **NOT DONE** | — | — | — | `_weights` still `abs(int())` unbounded; −10→+10. Declared out of scope |
| 2 | Canonical concept representation | DONE | `skill_concepts.Concept`, `CONCEPTS`, `resolve` | `test_skill_concepts` (78) | `e726565` | |
| 2 | True aliases canonicalized | DONE | `skill_concepts.CONCEPTS` | `TestAliasesResolveToOneConcept` | `e726565` | |
| 2 | Related-but-distinct kept apart | DONE | same table | `TestRelatedIsNotTheSame` | `e726565` | |
| 2 | Atomic compound splitting | DONE | `skill_concepts.split_compound`, `make_profile.split_compounds` | `TestCompoundsSplitOnlyWhenSafe` | `e726565` | |
| 2 | Aliases score once | DONE | `skill_concepts.score` | `test_adding_an_alias_never_raises_the_score` | `e726565` | |
| 2 | Matcher uses aliases as OR | DONE | `Concept.aliases`/`patterns` | `test_plain_javascript_matches_a_resume_that_said_es6` | `e726565` | |
| 2 | Corpus `matched_skills` canonicalization | **PARTIAL** | `skill_concepts.canonical_matched` | `TestWhatGetsRecorded` | `e726565` | **Written only; READ path untouched** — collapsing terms would move per-term denominators |
| 2 | Feature flag / dual mode | DONE | `SWEEP_SKILL_CONCEPTS` | `TestTheVersionSwitch` | `e726565` | |
| 3 | Evidence context preserved | DONE | `skill_evidence.Evidence`, `sections`, `find` | `test_skill_evidence` (52) | `625a85a` | |
| 3 | Small section representation | DONE | `skill_evidence.sections` | `TestSections` | `625a85a` | 7 kinds, line-based |
| 3 | Importance tiers | CHANGED FROM PLAN | `skill_evidence.tier` | `TestTiers` | `625a85a` | 4th tier named `BACKGROUND`, not `GENERIC_OR_LOW_SIGNAL` — see §18 |
| 3 | Same rules for all origins | DONE | tiers computed from text only | `test_model_reported_and_scanner_recovered_agree` | `625a85a` | |
| 3 | No new LLM call | DONE | `skill_evidence` imports no inference | `test_the_module_imports_no_inference` | `625a85a` | |
| 3 | Market signal separate | DONE | `skill_evidence.weight(tier, separation)` | `TestTheBoundedMapping` | `625a85a` | |
| 3 | Bounded 1–5 mapping | DONE | `skill_evidence.BANDS` | `test_core_and_common_still_beats_supporting_and_rare` | `625a85a` | |
| 3 | UI exposes meaningful importance | DONE | `sweep/logic.importance_badges`, `_weights.html` | `test_app` render tests | `625a85a` | |
| 3 | Per-skill recency | **NOT DONE** | — | — | — | Not implemented; no recency signal exists |
| 3 | Uncertainty field | **NOT DONE** | — | — | — | Proposed in audit §21; not implemented |
| 4 | Role families from evidence | DONE | `role_families.propose` | `test_role_families` (48) | `133718f` | **HELD — flag off** |
| 4 | Generic tools cannot anchor | DONE | `role_families.WORKFLOW`, `can_anchor` | `TestGenericToolsCannotCreateACareer` (8) | `133718f` | |
| 4 | Intent vs experience (primary/lane) | DONE | `Family.primary`, `headline_of` | `TestSalesforceIsALaneNotAnIdentity` | `133718f` | |
| 4 | Market validation | DONE | `role_families.titles_for` | — | `133718f` | |
| 4 | Final title revalidation | DONE | `role_families.revalidate` | `TestFinalTitleRevalidation` (12) | `133718f` | 7 guards |
| 4 | Row-permutation stability preserved | DONE | `rank_title` reused | `TestStableUnderRowOrder` | `133718f` | |
| 4 | Keep useful breadth | **NOT DONE** | — | measured, failed | `133718f` | **Median queries 8.0 → 1.5; 2/16 personas get zero.** This is why step 4 is held |
| 5 | Conditions A–E measured | PARTIAL | `bench/evaluate.py` | — | `429bb7c` | D is not a shippable config; see §12 |
| 5 | Keep 52-doc suite as gate | DONE | untouched | 52/52, F1 0.97628 | `429bb7c` | |
| 5 | New evaluation strata | DONE | `bench/eval_people.py` | 16 personas, 16 strata | `429bb7c` | |
| 5 | Independent labels, double-reviewed | **NOT DONE** | `bench/eval_people.ROLES` | — | `429bb7c` | **Authored by the implementer. No second reviewer.** |
| 5 | Extraction/normalization metrics | DONE | `bench/evaluate.py` | recall 1.000 | `429bb7c` | Alias invariance **untested by fixtures** |
| 5 | Importance metrics | PARTIAL | `bench/evaluate.py` | inversions 19.2%→0% | `429bb7c` | No top-k core coverage |
| 5 | Search metrics | PARTIAL | `role_verdicts` | wrong-career 4→0 | `429bb7c` | Human relevance NOT MEASURED |
| 5 | Ranking metrics | **NOT DONE** | — | — | — | **No labeled job data exists** |
| 5 | Performance metrics | PARTIAL | `bench/evaluate.py` | p50/p95 over 16 | `429bb7c` | Tokens, memory NOT MEASURED |
| 5 | 8 critical invariants | DONE | `bench/evaluate.invariants` | 9/9 pass | `429bb7c` | |
| 6 | v1 available as rollback | DONE | `skill_concepts.engine_version` | `test_v1_is_reachable_and_complete` | `3edaf4e` | v1 reproduces baseline byte-identically |
| 6 | Version the representation | DONE | `make_profile.PROFILE_SCHEMA` | `TestProfileSchemaCompatibility` (9) | `3edaf4e` | |
| 6 | `SWEEP_PROFILE_ENGINE_VERSION` | DONE | `skill_concepts.engine_version` | `TestTheVersionSwitch` (8) | `3edaf4e` | |
| 6 | Default local/dev to v2 | DONE | `DEFAULT_VERSION = "v2"` | `test_the_default_is_v2` | `3edaf4e` | |
| 6 | Public beta stays v1 | DONE | `render.yaml` | `test_render_pins_the_engine_version_the_beta_runs` | `3edaf4e` | |
| 6 | Old profiles readable or explicit failure | DONE | `make_profile.profile_schema`, `load_profile` | 9 tests | `3edaf4e` | All 44 load |
| 6 | Full repo tests | DONE | — | §15 | — | |
| 6 | Fresh résumé regression | DONE | — | 52/52 both versions | — | |
| 6 | Dev résumé run | DONE | — | §11 | — | |
| 6 | Held-out résumés | PARTIAL | `bench/evaluate.py` | 16 personas | — | **Synthetic, not held-out in the strict sense** |
| 6 | Render cold start verified | DONE | — | frozen/frozen, both versions | — | |
| 6 | Oracle change assessment | DONE | — | worker validator run on v2 output | — | No change needed |
| 6 | Modal change assessment | DONE | — | — | — | **Redeploy required** |
| 6 | Deployment/rollback plan | DONE | `docs/profile-engine-v2-deployment.md` | — | `69d54f0` | |

---

## 3. Architecture before vs after

### v1 (unchanged when `SWEEP_PROFILE_ENGINE_VERSION=v1`)

```
PDF
 → resume_parser.extract_text
 → local_extract.extract / employment          (two Qwen calls)
 → local_extract.route                         grounding + date arithmetic
 → local_profile.generate                      every skill starts at weight 3
 → make_profile._finish
      → skill_scan.widen                       scanner adds market terms, gets centrality
      → make_profile.reweight_from_corpus
           → corpus_signal.separation          market rarity 1-5
           → corpus_signal.blend               round(sqrt(c * s))  ← one number
 → local_search.fields_for
      → from_resume / keywords_for / canonicalise / validated / select_detail
 → make_profile.render                         profiles/<name>.py
 → scraper.score_job                           every TERM matched independently
```

### v2 as implemented (`SWEEP_PROFILE_ENGINE_VERSION=v2`)

```
PDF
 → resume_parser.extract_text                              (unchanged)
 → local_extract.extract / employment                      (unchanged prompts + schemas)
 → local_extract.route
      → check_grounding          fields                    (unchanged)
      → check_employment         NEW — rows vs document    ← step 1
      → check_years                                        (unchanged)
 → local_profile.generate                                  (unchanged)
 → make_profile._finish
      → make_profile.split_compounds     NEW  atomic strings          ← step 2
      → skill_scan.widen                      (unchanged)
      → make_profile.reweight_from_evidence   NEW                     ← step 3
           → skill_concepts.from_weights      CANONICALIZATION HERE
           → corpus_signal.separation         MARKET SIGNAL HERE (unchanged)
           → skill_evidence.assess_all        EVIDENCE + IMPORTANCE HERE
                → sections()                  section/entry spans
                → find()                      deterministic alias spans
                → tier()                      CORE/STRONG/SUPPORTING/BACKGROUND
                → weight(tier, separation)    COMPATIBILITY 1-5 HERE
 → local_search.fields_for                    ROLE CANDIDATES — v1 path (step 4 held)
 → make_profile.render                        + PROFILE_SCHEMA stamp   ← step 6
      → _prose / check_module                 data boundary            ← step 1
 → scraper.score_job
      → skill_concepts.score                  SCORER MATCHING HERE     ← step 2
```

**Answers to the specific questions:**

| Question | Where |
|---|---|
| Canonicalization | `skill_concepts.resolve` / `from_weights`, called in `reweight_from_evidence` and `scraper` module load |
| Aliases stored | `skill_concepts.CONCEPTS` (module constant), materialised per-profile in `Concept.aliases` |
| Evidence attached | `skill_evidence.find` → `Evidence(section, entry, start, end, alias, planned)` |
| Importance calculated | `skill_evidence.tier` (rules) |
| Market signal applied | `corpus_signal.separation`, passed **separately** into `skill_evidence.weight` |
| Search-role candidates | v2-as-shipping: `local_search.from_resume` + `keywords_for` + `select_detail` (v1 path). Step 4's `role_families.propose` is **held** |
| Final concrete title validated | `role_families.revalidate` — **held, not in the shipping path** |
| Numeric compatibility weight | `skill_evidence.weight(tier, separation)` → `BANDS` |
| Scorer matching | `scraper.score_job` → `skill_concepts.score` (v2) or `SKILL_PATTERNS` loop (v1) |

---

## 4. Data model / schema diff

### v1 skill

```python
{"term": "firebase fcm", "weight": 3}      # a string and a number
```

### v2 concept — what is actually implemented

```python
skill_concepts.Concept:
    id        "firebase cloud messaging"    canonical, stable, lowercase
    display   "Firebase Cloud Messaging"    human name
    aliases   ("firebase cloud messaging", "fcm", "firebase fcm", ...)
    raw       ("firebase fcm", "fcm")       original extracted strings
    weight    int                            highest among the raw spellings
    patterns  compiled OR-matchers

skill_evidence.assess_all() row:
    id, display                              identity
    tier        "CORE" | "STRONG_SECONDARY" | "SUPPORTING" | "BACKGROUND"
    why         human sentence
    weight      1-5 compatibility number
    market_separation  1-5 or None           SEPARATE FIELD
    sections    ["work", "project", ...]
    evidence    [{section, entry, span:[s,e], alias, planned}]
    planned_only  bool
    shadowed_by   [display]                  when every span sits inside another
```

| Field asked for | Implemented? |
|---|---|
| canonical ID | YES — `Concept.id` |
| display name | YES |
| aliases | YES |
| raw/original strings | YES — `Concept.raw` |
| evidence representation | YES — `Evidence`, with validated spans |
| section/context | YES — 7 section kinds + entry label |
| importance tier | YES — 4 tiers |
| market signal | YES — separate field, never multiplied in |
| compatibility numeric weight | YES — `BANDS` + `MARKET_STEP` |
| **uncertainty** | **NOT IMPLEMENTED** — `planned_only` and `shadowed_by` are the only near-equivalents |
| **origin/provenance** | **PARTIAL** — `raw` records the spellings, but *which extractor found it* is deliberately not recorded on the concept, because origin must not influence importance. `skills_added` on the profile still names scanner additions |

### Not implemented from the audit's proposed record

- per-skill **recency** / duration
- explicit **proficiency uncertainty**
- per-skill **responsibility / production-use** flag
- **role-family relevance** stored on the skill

### Profile schema / versioning

```python
PROFILE_SCHEMA = {"version": 1, "engine": "v2"}   # new top-level name
```

| Case | Behaviour |
|---|---|
| Profile without the field | Readable. Reported as version 0, engine `v1`. **Absence is the version** — nothing on disk needs migrating |
| This build's profile | Readable |
| Future `version` | **Refused**: "written by a newer build than this one… regenerate" |
| Malformed stamp | Refused, names the type |

Serialization: `make_profile.render` emits it as a dict literal; `check_module`
allows it; `config._overlay` ignores it (not in `OVERLAYABLE`); the Oracle worker's
`assert_only_literals` accepts it (any `Name` bound to a literal). All three
verified by execution, not by reading.

---

## 5. Feature flags / rollback

| Flag | Values | Code default | Local | Test | Public beta | Render expected | Rollback |
|---|---|---|---|---|---|---|---|
| `SWEEP_PROFILE_ENGINE_VERSION` | `v1`, `v2` | **`v2`** | v2 | v2 unless pinned | **v1** | `v1` now → `v2` at migration | `v1` |
| `SWEEP_SKILL_CONCEPTS` | truthy | unset | unset | unset | unset | unset | — |
| `SWEEP_SKILL_EVIDENCE` | truthy | unset | unset | unset | unset | unset | — |
| `SWEEP_ROLE_FAMILIES` | truthy | unset | unset | unset | unset | unset | — |

The three per-step flags are **experiment overrides**: they force a step on under
v1, which is how `bench/evaluate.py` isolates B, C and D. They are not part of the
production switch.

Unknown values raise. Empty string means unset.

### IS V2 CURRENTLY THE DEFAULT ANYWHERE?

**Yes — in code.** `skill_concepts.DEFAULT_VERSION = "v2"`. Any process with no
`SWEEP_PROFILE_ENGINE_VERSION` set runs v2. That includes local CLI runs, developer
shells, and the test suite.

### IS PUBLIC BETA BEHAVIOR CHANGED IF THIS BRANCH IS DEPLOYED WITHOUT NEW ENV VARS?

**No — provided `render.yaml` is deployed with it.** `render.yaml` is committed on
this branch and sets `SWEEP_PROFILE_ENGINE_VERSION=v1` explicitly, so a Render
deploy from this branch runs v1.

**The caveat a reviewer should weigh:** the protection is a committed manifest
entry, not a code default. If Render's environment were managed outside
`render.yaml`, or if that variable were deleted in the dashboard, the beta would
silently become v2. There is a test asserting `render.yaml` carries the pin
(`test_render_pins_the_engine_version_the_beta_runs`), but no runtime guard that
public mode refuses v2.

---

## 6. Step 1 correctness evidence

| | Old behaviour | New behaviour | Test | Result |
|---|---|---|---|---|
| **A** row-order invariance | `canonical()` kept the first title at the winning count; `most_common` truncated on insertion order. Same 22,806 rows in a different order moved "systems engineer" between 5 titles and displaced `salesforce developer` | `rank_title` = `(-count, word_count, title)`, total order | `test_canonical_is_the_same_under_every_permutation`, `test_reversed_is_a_permutation_too`, `test_the_shipped_corpus_permutes_to_the_same_keywords` (8 total) | PASS. 3 of 8 fail on pre-fix code |
| **B** low-weight skills auto-deleted | Remove checkbox pre-ticked for every weight ≤2 — on the audited résumé that was TypeScript, Node.js, MongoDB, Salesforce (12 of 58) | Never pre-ticked; the term is still labelled "Common" | `test_no_skill_arrives_pre_selected_for_deletion`, `test_common_terms_are_named_but_not_ticked_for_removal`, `test_a_common_skill_survives_approving_the_form_untouched` | PASS |
| **C** employment grounding | `check_grounding` validated only fields. A row "Initech Global Holdings / Principal Architect / March 1999–Present", absent from the document, was accepted as 27 years | `check_employment` runs before `check_years`; employer, title and each date's **year** must be in the text | `test_the_invented_1999_row_does_not_become_27_years`, `test_one_bad_row_among_good_ones_is_dropped_not_escalated` (27 total) | PASS. 16 of 19 fail pre-fix |
| **D** reversed/impossible dates | `months_between` clamped at 0 — silently contributed nothing and said nothing | Row excluded with an explicit reason; future starts too | `test_a_reversed_range_is_reported_not_swallowed`, `test_a_reversed_range_is_excluded_from_the_arithmetic` | PASS |
| **E** trainee/promotion ambiguity | Title containing "trainee" → row silently counted 0 | Flagged as a **floor, not a measurement**, with a "needs review" correction | `test_the_ambiguity_is_flagged_rather_than_guessed`, `test_no_promotion_date_is_invented` | PASS. **Count is still 0** — the number did not change, only its honesty |
| **F** prose escaping | `field_summary`/`notes` f-stringed raw into a `"""..."""` docstring; a closing triple quote made the next line a top-level statement | `_prose` removes quote-runs and backslashes | `test_the_audit_payload_no_longer_creates_a_statement`, `test_a_long_run_of_quotes_does_not_rebuild_the_escape` | PASS |
| **G** AST allowlist | none | `check_module` — docstring + literal assignments to 8 names only | `test_an_extra_assignment_is_refused` + 9 | PASS. **Generated source only**; the 9 hand-written profiles are deliberately out of scope |
| **H** malformed/truncated response | `json.loads(payload["response"])` with no finish check; malformed shapes raised `AttributeError` frames away | `done_reason == "length"` → `BadModelOutput`; `_object` type-checks both calls; `check_employment` guards the shared seam | `test_done_reason_length_is_refused`, `TestAMalformedAnswerFailsSafely` (8) | PASS |

### Intentionally left unchanged

- **`_weights` numeric bounds.** `abs(int(weight))` is still unbounded: −10 renders
  as +10, 99 as 99. Real (audit §3), and declared out of scope because it is the
  weight scale, and `PAYLOAD` in `test_make_profile` asserts weight 10 today.
- **Trainee span arithmetic.** The floor stays 0; only the silence was fixed.
- **Date grounding is year-level.** "Jan 2025" vs "January 2025" cannot be string
  compared; only the 4-digit year is checked. Marked as a ceiling in the code.

---

## 7. Canonicalization / alias evidence

| Raw forms | Canonical concept | Aliases (matchers) | Kept separate from | Score contribution |
|---|---|---|---|---|
| JavaScript, JS, ES6, ES6+, `javascript (es6+)`, ecmascript | `javascript` | 8 | TypeScript | once |
| TypeScript, TS | `typescript` | 2 | JavaScript | once |
| LWC, `l wc`, L.W.C., Lightning Web Components | `lightning web components` | 3 | Salesforce, Apex | once |
| Node.js, node, NodeJS, `node js` | `node.js` | 4 | — | once |
| React, React.js, ReactJS, `react js` | `react` | 4 | **React Native** | once |
| React Native, reactnative, react-native | `react native` | 3 | **React** | once |
| Firebase | `firebase` | 1 | **FCM** | once |
| FCM, Firebase FCM, Firebase Cloud Messaging | `firebase cloud messaging` | 4 | **Firebase** | once |
| Socket.IO, socketio, `socket io` | `socket.io` | 3 | **WebSockets** | once |
| WebSockets, websocket, `web sockets` | `websockets` | 3 | **Socket.IO** | once |
| Salesforce, `salesforce crm`, sfdc, force.com | `salesforce` | 4 | **Apex, LWC** | once |
| Apex | `apex` | 1 | **Salesforce** | once |
| REST, REST API(s), RESTful, `rest api design` | `rest api` | 8 | **API Design** | once |
| OAuth, OAuth2, `oauth 2.0` | `oauth` | 4 | JWT | once |
| JWT, json web token(s) | `jwt` | 3 | OAuth | once |

Relations are **not modelled as parent/child**. Overlap is settled by a span rule:
a concept scores only if it has a match not wholly inside a longer matched
concept's span (`skill_concepts.score`, `_inside`).

### Synthetic scorer, before and after

| Job text | v1 | v2 | Test |
|---|---:|---:|---|
| `JavaScript` (profile said `javascript (es6+)`) | **0** | **3** | `test_plain_javascript_matches_a_resume_that_said_es6` |
| `LWC` (profile said `l wc`) | **0** | **3** | `test_plain_lwc_matches_the_pdf_spaced_profile` |
| `Firebase FCM` | **12** | **5** | `test_firebase_fcm_no_longer_triples` |
| `REST API design` | 13 | 4 | `test_rest_api_design_scores_rest_once` |
| `Node.js and Node` | 4 | 2 | `test_node_aliases_cannot_add_points` |
| `Salesforce CRM` | 7 | 3 | `test_salesforce_crm_scores_salesforce_once` |
| `Firebase and FCM` | 9 | 9 | both count — the job asked for both |
| `Socket.IO over WebSockets` | 8 | 8 | distinct, both count |
| `React and React Native` | 5 | 6 | both count |
| `React Native developer` | 5 | 3 | React suppressed |

**Aliases score once**: `test_adding_an_alias_never_raises_the_score` iterates
*every alias of every concept in the table* and asserts the score is unchanged.

**Unknown skills survive**: `test_an_unknown_tool_resolves_to_itself`,
`test_an_unknown_tool_still_scores`, `test_an_unknown_tool_is_not_merged_with_a_known_one`.

---

## 8. Evidence / importance engine — exact implementation

### Signals, and which axis each feeds

| Signal | Source | Feeds CANDIDATE IMPORTANCE | Feeds MARKET DISCRIMINATION |
|---|---|---|---|
| Section of each occurrence | `skill_evidence.sections` | **YES** | no |
| Containing entry (employer/project) | same | provenance text only | no |
| Repeated evidence within one entry | `tier`, `PROJECT_DEPTH = 2` | **YES** | no |
| Work-section evidence | `tier` | **YES** → CORE | no |
| Project evidence | `tier` | **YES** → STRONG/SUPPORTING | no |
| Skills-list evidence | `tier` | **YES** → SUPPORTING | no |
| Education / certification | `tier` | **YES** → BACKGROUND | no |
| Planned/future clause | `is_planned` | **YES** — excluded from tiering | no |
| Shadowing by a longer concept | `assess_all` | **YES** — excluded | no |
| Recency / duration | — | **NOT IMPLEMENTED** | — |
| Responsibility / production use | — | **NOT IMPLEMENTED** | — |
| Uncertainty | — | **NOT IMPLEMENTED** | — |
| Document frequency | `corpus_signal.separation` | **no** | **YES** |
| Discovery origin (model vs scanner) | — | **deliberately NOT used** | no |

### The exact rules, in order (`skill_evidence.tier`)

```
real = [e for e in evidence if not e.planned]
if not real:                       BACKGROUND  ("only named as planned" | "not found")
if any evidence in WORK:           CORE
if any evidence in PROJECT:
     deepest = max occurrences within one project entry
     if deepest >= 2 or more than one project entry:   STRONG_SECONDARY
     else:                                              SUPPORTING
if any in SKILLS:                  SUPPORTING
if any in SUMMARY:                 SUPPORTING
if any in EDUCATION or CERT:       BACKGROUND
otherwise:                         SUPPORTING
```

First match wins. The market is not consulted anywhere in this function.

### Mapping to the 1–5 interface (`skill_evidence.weight`)

```
BANDS = {CORE: (5, 4, 5), STRONG_SECONDARY: (4, 3, 4),
         SUPPORTING: (3, 2, 3), BACKGROUND: (2, 1, 2)}      # (base, low, high)
MARKET_STEP = 1

separation is None  -> base
separation <= 2     -> clamp(base - 1, low, high)
separation >= 4     -> clamp(base + 1, low, high)
else                -> base
```

### Can a SUPPORTING rare skill outrank a CORE common skill solely because of rarity?

**No.** SUPPORTING's band ceiling is 3; CORE's band floor is 4.

```python
assert se.weight(CORE, 1) == 4          # core, most common
assert se.weight(SUPPORTING, 5) == 3    # supporting, rarest
```

Tests: `test_core_and_common_still_beats_supporting_and_rare`,
`test_the_market_never_moves_a_weight_out_of_its_band` (every tier × every
separation), `test_the_bands_of_non_adjacent_tiers_never_overlap`.

Adjacent bands **do** overlap by 1 — CORE and STRONG_SECONDARY can tie at 4. That
is deliberate and documented.

---

## 9. Model-vs-scanner source invariance

**Status: DONE.**

The tier is computed from the résumé text alone. The weight a concept arrived with
is used only as the fallback when no importance row exists.

```python
def test_model_reported_and_scanner_recovered_agree(self):
    reported,  = sc.from_weights({"apex": 3})   # Qwen's neutral 3
    recovered, = sc.from_weights({"apex": 5})   # scanner's centrality 5
    self.assertEqual(se.tier(se.find(reported, RESUME, SPANS)),
                     se.tier(se.find(recovered, RESUME, SPANS)))
```

Also `test_the_tier_ignores_the_weight_it_arrived_with` (all weights 1–5) and
`test_the_spelling_that_found_it_does_not_change_the_tier`. Independently re-checked
by the evaluation harness as invariant 4 (`bench/evaluate.invariants`).

**Caveat:** the invariance holds for the **tier**. The final compatibility weight
also depends on `market_separation`, which is looked up over the concept id **and
its raw spellings** — so a concept that arrived with extra spellings can find a
measurement that a bare one would not. Not observed to change a tier; the reviewer
may want to probe it.

---

## 10. Role / search query construction — **HELD, flag off**

Implemented in `role_families.py`, **not in the shipping path**.

```
candidate evidence (skill_evidence tiers + held titles + headline + user preference)
 → role_families.propose      Family(key, display, anchor, tier, source, primary)
 → role_families.titles_for   frequency-ranked market titles, rank_title ordering
 → role_families.revalidate   7 guards on the FINAL string
 → role_families.queries      round-robin, primary families first
```

### The seven validation rules

| Rule | Implementation | Bar |
|---|---|---|
| Seniority | `words_of(title) ∩ seniority` | reject |
| Volume | `len(matched) >= MIN_ROWS` | 20 |
| Wildcard | `matched / rows <= MAX_SHARE` | 0.25 |
| One-company pollution | distinct employers | ≥ 5 |
| Concept alignment | ≥1 candidate concept | ≥ 10% of listings |
| Generic-tool-only | must match a non-`WORKFLOW` concept | ≥1 |
| Semantic alignment | `unsupported_domains` — market vocab ∪ `DOMAIN_MARKERS` | none unsupported |
| Anchor retained | listings wanting the proposing anchor | ≥1 |
| Profession match | ≥2 candidate concepts per listing | ≥ 50% |

User target role: `propose(preferred=...)` creates a primary family first; tested by
`test_an_explicit_target_role_is_searched`, `test_a_preference_expands_rather_than_replaces`.

### Regression evidence

| Required | Test | Result |
|---|---|---|
| Git alone cannot produce Python Developer | `test_git_cannot_reach_the_python_market`, `test_git_produces_no_family_at_all` | PASS |
| CI/CD, Agile cannot create a lane | `test_ci_cd_cannot_anchor_even_at_core`, `test_agile_and_jira_cannot_anchor` | PASS |
| backend → unsupported AI backend | `test_canonicalisation_may_not_import_a_new_domain` | PASS |
| React Native supports mobile/RN roles | `test_react_native_supports_mobile_variants` | PASS |
| Full-stack evidence supports full-stack roles | `test_full_stack_projects_support_full_stack_roles` | PASS |
| Salesforce lane without forcing primary | `test_the_salesforce_lane_exists`, `test_a_lane_not_named_in_the_summary_is_not_primary` | PASS |
| Graduate/frozen corpus still succeeds | `test_an_internship_only_graduate_still_gets_queries`, `test_it_works_against_the_shipped_frozen_corpus` | **PASS in the unit test, FAILS in the evaluation** — see below |
| Row order cannot alter queries | `TestStableUnderRowOrder` (3), evaluation stability 32/32 | PASS |

**The graduate contradiction is the most important thing in this section.** The unit
test passes because its fixture hands the persona STRONG_SECONDARY concepts. In the
evaluation, `bhaskar` — a real rendered graduate résumé with no employment — has
every concept at SUPPORTING, so nothing can anchor a family and **he gets zero
queries**. The unit test was written to the rule, not to the stratum. This is why
step 4 is held.

---

## 11. Development résumé, v1 vs v2

Engineering comparison only. Detailed artifacts stay in the gitignored
`output/resume-skill-audit/` and `output/profile-engine-v2-review/devcmp.json`.

| | v1 | v2 |
|---|---:|---:|
| Raw extracted strings | 58 | 57 |
| Canonical concepts | 49 | 46 |
| Compatibility weights | 2:12 3:33 4:11 5:2 | 2:6 3:26 4:17 5:8 |
| Importance tiers | n/a | CORE 7, STRONG 12, SUPPORTING 25, BACKGROUND 2 |
| Market separation | (multiplied in) | 1:3 2:6 3:13 4:13 5:6, unmeasured 5 |
| Review pre-ticked for deletion | **12** | **6** |
| Aliases merged | 0 | 7 groups (Express, FCM, Node.js, React, REST, Salesforce, Socket.IO) |
| Compounds split | 0 | 6 |
| Primary role families | n/a (v1 has no families) | **not produced — step 4 held** |
| Final role queries | 8 | **8, identical** |

| Concept | v1 | v2 | Tier | Market |
|---|---:|---:|---|---:|
| React Native | 3 | **5** | CORE | 3 |
| TypeScript | 2 | **4** | CORE | 1 |
| Apex | 3 | **5** | CORE | 3 |
| Salesforce | 3 | 4 | CORE | 2 |
| Node.js | 2 | 3 | STRONG_SECONDARY | 2 |
| MongoDB | 2 | 3 | STRONG_SECONDARY | 2 |
| Firebase Cloud Messaging | **5** | 4 | STRONG_SECONDARY | 5 |
| Socket.IO | 5 | 4 | STRONG_SECONDARY | 5 |
| Lightning Web Components | 3 | 3 | SUPPORTING | 3 |
| Tailwind CSS | **4** | 3 | SUPPORTING | 5 |
| Mongoose | **4** | 3 | SUPPORTING | 5 |
| Python | absent | absent | — | — |
| TensorFlow | absent | absent | — | — |
| OpenCV | absent | absent | — | — |

**Skills dropped: none by v2.** Python, TensorFlow and OpenCV are absent in **both**
— Qwen never extracted them (audit defect C6, an extraction-recall problem this work
did not address). v2's tier logic handles them correctly when present
(`test_an_unrelated_project_survives_at_low_influence`), but that is a unit test,
not this résumé.

**Uncertain items**: 2 concepts are `BACKGROUND` because every occurrence sits inside
a longer name (API Design inside REST APIs; Firebase inside Firebase FCM). Whether
they should be kept at weight 2 or dropped is a judgement a reviewer may disagree
with.

**This table is not evidence that v2 is right.** It matches the audit's independent
interpretation, and that interpretation was written by the same process that
designed the tiers.

---

## 12. Evaluation: A / B / C / D / E

All five were executed. **D is not a shippable configuration** — role families are
anchored by importance tiers, so "roles without importance" silently falls back to
B. The first harness run produced a D column identical to B. D now computes tiers
for the gate while keeping v1's weights, purely to isolate the role change.

**Fixed across all conditions:** revision `69d54f0`; model **not called** —
extraction is built once per persona from `bench/people.py` truth and replayed, so
column differences are postprocessing only; corpus live, 22,806 rows; clock system
(2026-09-17); input 16 personas rendered to PDF and extracted through
`resume_parser.extract_text`; 0 failures, 0 errors, 0 failed parses everywhere.

| Metric | A | B | C | D | E |
|---|---:|---:|---:|---:|---:|
| Concept recall | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Unknown-tool recall | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| Compound-split errors | 0 | 0 | 0 | 0 | 0 |
| Alias duplicates | 0 | 0 | 0 | 0 | 0 |
| Importance inversions (core vs list-only) | **19.2%** | 19.2% | **0%** | 19.2% | **0%** |
| Queries | 116 | 116 | 116 | 41 | 41 |
| Wrong-career queries | 4 | 4 | 4 | **0** | **0** |
| People with **zero** queries | 0 | 0 | 0 | **2** | **2** |
| p50 | 401 ms | 432 ms | 394 ms | 740 ms | 790 ms |
| p95 | 567 ms | 602 ms | 589 ms | 1566 ms | 1561 ms |
| Errors | 0 | 0 | 0 | 0 | 0 |

**Ranking metrics: NOT MEASURED.** Precision@10, nDCG@10, pairwise accuracy and
adjacent-career false-positive rate all require independently judged job data, which
does not exist in this repository.

**Also NOT MEASURED:** human relevance of queries, token counts, memory,
cold-vs-warm split, top-k core coverage.

**Alias invariance is reported as 0 duplicates and that is a limitation, not a
result** — `bench/people.py` lists each skill in one canonical spelling, so these
fixtures do not exhibit the alias problem. B's benefit is demonstrated only on the
audited résumé and in unit tests.

---

## 13. Benchmark coverage

| | |
|---|---|
| Fresh 52-document run, v1 | 52 docs, years 52/52, macro F1 0.97628, router 38/14/0 |
| Fresh 52-document run, v2 | **identical** |
| Historical-cache results used? | Yes — the replay uses stored Qwen outputs (`bench/route.load`). No fresh model calls |
| New v2-specific fixtures | **3** personas (`priya`, `omar`, `yuki`) + role labels for all 16 |
| Realistic/held-out candidates | **0 in the strict sense** |
| Independently labeled | **0** |
| Double-reviewed | **0** |

### Strata

| Stratum | Persona | Source |
|---|---|---|
| mobile specialist | `priya` | **new** |
| frontend specialist | `iris` | pre-existing |
| backend specialist | `ada` | pre-existing |
| full-stack generalist | `esi` | pre-existing |
| ML professional | `hana` | pre-existing |
| student/graduate | `bhaskar` | pre-existing |
| internship-heavy | `lena`, `bhaskar` | pre-existing |
| senior engineer | `chen` | pre-existing |
| career switcher | `kwame`, `hana` | pre-existing |
| project-heavy | `esi` | pre-existing |
| skills-list-heavy | `omar` (60 terms) | **new** |
| coursework-heavy | `farida` | pre-existing |
| older + new stack | `yuki` | **new** |
| unknown technology | `dmitri` | pre-existing |
| QA specialist | `gopal` | pre-existing |
| employment gaps | `jonas` | pre-existing |

**The planned 24-case independently-labelled held-out set was NOT built.** What
exists is 16 **synthetic development fixtures**, 13 of which predate this work.
They are not a holdout: they are generated from truth dicts in the same repository,
and the role labels were authored by the implementer.

---

## 14. Critical invariants

| # | Invariant | Status | Test |
|---|---|---|---|
| 1 | Alias duplication cannot increase score | **PASS** | `test_adding_an_alias_never_raises_the_score`, `test_node_aliases_cannot_add_points`; harness invariant 1 |
| 2 | JavaScript matches normalized forms | **PASS** | `test_plain_javascript_matches_a_resume_that_said_es6`, `test_javascript` |
| 3 | LWC matches PDF spacing variants | **PASS** | `test_lwc_including_the_pdf_spacing`, `test_plain_lwc_matches_the_pdf_spaced_profile`, `test_spellings_that_fold_alike_are_both_kept_as_matchers` |
| 4 | Provenance alone cannot change importance | **PASS** | `test_model_reported_and_scanner_recovered_agree`; harness invariant 4 |
| 5 | Git alone cannot create a Python career query | **PASS** | `test_git_cannot_reach_the_python_market`; harness invariant 5. *Applies to step 4, which is held; under v2-as-shipping the v1 role path still runs and the audited résumé's Git-anchored query is NOT removed* |
| 6 | Row order cannot change final queries | **PASS** | `TestRowOrderCannotChangeTheAnswer` (8), `TestStableUnderRowOrder` (3), harness stability 32/32 |
| 7 | Unsupported employment facts cannot create experience | **PASS** | `test_the_invented_1999_row_does_not_become_27_years`; harness invariant 7 |
| 8 | Generated prose cannot escape config | **PASS** | `test_the_audit_payload_no_longer_creates_a_statement`; harness invariant 8 |
| 9 | Common core skills not recommended for removal | **PASS** | `test_no_skill_arrives_pre_selected_for_deletion`; harness invariant 9 |
| 10 | Unknown explicit skills survive | **PASS** | `TestUnknownSkillsSurvive` (5), `test_an_unknown_technology_survives` |
| 11 | Planned work is not completed experience | **PASS** | `test_a_planned_tool_is_background`, `test_the_marker_does_not_reach_backwards` |
| 12 | Related-but-distinct not collapsed | **PASS** | `TestRelatedIsNotTheSame` (4), `test_no_folded_key_is_claimed_by_two_different_concepts` |
| 13 | Frozen and live equivalent corpora behave equivalently | **PARTIAL** | Row-order equivalence PASS (32/32). Cold-start weights **differ** between live and frozen because the scanner's vocabulary is live-only — a pre-existing asymmetry (audit §12), not introduced here, and **not fixed** |
| 14 | v1 remains available as rollback | **PASS** | `test_v1_is_reachable_and_complete`, `test_the_version_changes_the_score_and_changes_back`; v1 reproduces the audited baseline profile byte-identically |

**Note on #5:** the invariant is proven for `role_families`, which is held. Deploying
v2 does **not** remove the Git→Python query from the audited résumé, because role
construction is unchanged in the shipping path.

---

## 15. Test report — run from HEAD `69d54f0`

Raw logs: `output/profile-engine-v2-review/` (gitignored, 516 KB).

| Suite | Command | Ran | Failed | Skipped | Elapsed | Log |
|---|---|---:|---:|---:|---:|---|
| auto-apply | `cd auto-apply && python -m unittest discover -s tests -t .` | **581** | 0 | 2 | 45.4 s | `auto-apply.log` |
| Sweep | `python -m unittest discover -s sweep/tests -t .` | **770** | 0 | 1 | 115.5 s | `sweep.log` |
| bench | `python -m unittest bench.test_backends bench.test_answer_key` | **43** | 0 | 0 | 1.7 s | `bench.log` |
| worker + Modal | `python -m unittest deploy.test_modal_benchmark deploy.test_sweep_worker` | **42** | 0 | 0 | 3.0 s | `deploy.log` |
| **Total** | | **1436** | **0** | **3** | ~166 s | |

Profile-engine-specific subset (**239**, included in the auto-apply total):
`test_skill_concepts` 78 · `test_skill_evidence` 52 · `test_role_families` 48 ·
`test_employment_grounding` 27 · `test_generated_profile_safety` 34.

Module self-checks: `local_extract`, `local_search`, `corpus_signal`, `skill_scan`,
`skill_concepts`, `skill_evidence`, `role_families` — 7/7 pass.

**There is no single repository-wide runner.** The four suites use different working
directories and `sys.path` setups (`auto-apply/` must be the cwd for its own tests),
so they are run separately. That is pre-existing structure, not a limitation
introduced here. No suite was skipped or excluded.

---

## 16. Performance — measured only

| Measurement | v1 | v2 | Kind |
|---|---:|---:|---|
| Inference calls per résumé | 2 | **2** | exact — no call added |
| `_finish` on the dev résumé | 409 ms | 421 ms | **SINGLE OBSERVATION** (best of 3) |
| p50 over 16 personas | 401 ms | 394 ms | p50, n=16 |
| p95 over 16 personas | 567 ms | 589 ms | p95, n=16 |
| Render cold start, frozen data | 1.5 ms | 27.9 ms | **SINGLE OBSERVATION** |
| `assess_all`, 46 concepts | — | ~9 ms | **SINGLE OBSERVATION** |
| Timeout rate | 0 | 0 | over 16 personas × 5 conditions |
| Deterministic processing | included above | included above | |

**NOT MEASURED:** model time (no model called in the evaluation), memory, context
size, output-token budget, tokens, warm-vs-cold split for the model itself.

The p50/p95 figures are over 16 personas in one run, not repeated trials.

---

## 17. Gemini

**GEMINI COMPARISON NOT PERFORMED.**

No Gemini call was made at any point on this branch. The audit's §5 Gemini run was
never authorised and remains outstanding; every "Gemini" cell in the audit's §7
table still reads `Pending approval`.

No new Gemini code paths were added (`git diff main..HEAD` on `make_profile.py`
shows 0 additions matching `generate_content|get_client|GEMINI_API_KEY`). The
existing Gemini path is untouched. No API key was read, printed, copied or
committed.

Nothing in this work implies anything about provider quality in either direction.

---

## 18. Deviations from the approved audit

| Planned | Implemented | Why | Risk | Reviewer decision? |
|---|---|---|---|---|
| Tier named `GENERIC_OR_LOW_SIGNAL` | `BACKGROUND` | "Generic" is a market property; this axis is deliberately not the market | Low — naming | **No**, but flagged |
| Skill record with recency, proficiency uncertainty, responsibility | Not implemented | Out of the approved step scope | Medium — audit §21 expects them later | **Yes** |
| "Canonical identities with match aliases" as a full ontology seam | Small hand-written table + unknown passthrough | Audit §10 warns an ontology gates out real tools | Medium — table needs maintenance | **Yes** |
| Corpus `matched_skills` canonicalized | **Write path only**; read path untouched | Collapsing terms changes per-term denominators, which moves weights — outside "don't tune weights" | Medium — a future corpus will be mixed-format | **Yes** |
| Role families as the shipping path | **Built and held** | Evaluation measured 2/16 personas losing their search entirely | **High** — the audit's C5 defect is NOT fixed in production | **Yes** |
| Independently labelled 24-case holdout | 16 synthetic fixtures, implementer-labelled | No second labeller available | **High** — weakens every search metric | **Yes** |
| Weight bounds (audit §3, −10→+10) | Not fixed | Declared out of scope in step 1; a test asserts weight 10 today | Medium | **Yes** |
| `SKILL_LIFT` reused for title selection | Replaced by frequency + revalidation | 10× lift is unreachable for a skill that is 16% of the market | Medium — a measured guard was dropped | **Yes** |
| `MIN_PAIR_SHARE` | **New threshold, 0.50** | Invented in step 4 to separate professions sharing a platform | Medium — one corpus, one calibration | **Yes** |
| `DOMAIN_MARKERS` | **New hand-maintained list** | Market vocab lacks `ai`/`ml`/`data` as tokens | Medium — will need additions | **Yes** |
| `go`, `rn` as aliases | **Excluded** | Measured: `go` fires twice in ordinary prose, `rn` is Registered Nurse | Low | No |
| Public beta protected by code | Protected by `render.yaml` only | Keeps the flip a config change, not a deploy | **Medium** — no runtime guard | **Yes** |
| Condition D as specified | Not achievable | Roles require tiers; "roles without importance" = B | Low — measurement only | No |
| Three test fixtures reshaped | Dates added to 2; sales cluster reshaped in 1 | They asserted year arithmetic/normalization and had never mentioned the years their rows claimed | **Medium** — reshaping a fixture to pass a guard needs review | **Yes** |

---

## 19. Known weaknesses

Ordered by how likely they are to make v2 wrong.

1. **Labels are not independent.** One person wrote the engine, the fixtures and the
   labels. Every search metric inherits this.
2. **Alias inflation is untested by the fixtures.** 0 duplicates across 16 personas
   is a property of `bench/people.py`, not evidence for step 2.
3. **Section classifier ambiguity.** `sections()` is line-based. Observed failures
   during development: wrapped bullet continuations were read as new entries (fixed);
   a two-line work header overwrote the employer (fixed); PDF-broken headings needed
   a second matcher (fixed). An unusual layout will still mis-attribute, and a
   mis-attributed section changes a tier.
4. **Evidence misattribution, observed.** `ada` is a Python backend engineer whose
   **Python is SUPPORTING**, because her bullets say "Django monolith" and never
   "Python". A framework does not evidence its language. This is a live defect in
   the shipping path.
5. **Padded skills lists.** `omar` lists 60 terms with 4 evidenced. Tiers handle it
   (SUPPORTING), but every padded term still enters the profile and scores.
6. **Undated projects / recency.** No recency signal at all. A 2019 project and a
   2026 project are tiered identically.
7. **Career switchers.** `kwame` and `hana` pass the 52-doc date gate, but nothing
   in the importance engine knows a skill belongs to an abandoned career.
8. **Parent/child scoring is a span rule, not a relation.** It gives the right
   answer on every tested case, and it is positional: an unusual phrasing that
   separates "React" and "Native" would score both.
9. **Corpus bias.** `gopal` (QA) gets nothing from step 4 because the corpus
   under-covers QA. The corpus is a record of previous searches by previous users.
10. **Role-family inference over-constrains** — the measured 8.0 → 1.5 median.
11. **`MIN_PAIR_SHARE = 0.50` and `DOMAIN_MARKERS`** are calibrated on one corpus.
12. **Latency** is 1.85× p50 for step 4; ~1.03× for what ships.

---

## 20. Security / privacy review

| Check | Result |
|---|---|
| No résumé text newly committed | **Confirmed.** `git diff main..HEAD -- output/` is empty; `output/` is gitignored (`.gitignore:5`) |
| No API tokens committed | **Confirmed.** `.env` is untracked (0 hits in `git ls-files`); diff scan for `AIza…`, `sk-…`, `gsk_…`, private-key headers and `api_key=` literals returns nothing |
| No owner capability / run IDs committed | **Confirmed.** No run IDs or capability tokens in the diff |
| Audit outputs remain gitignored | **Confirmed.** `output/resume-skill-audit/` (78 entries) and `output/profile-engine-v2-review/` (516 KB) both ignored |
| Profile hardening tests pass | **34/34** (`test_generated_profile_safety`) |
| Model content validated before execution | **Confirmed.** `check_module` + `_prose`; `ast.parse`/`literal_eval` only — nothing generated is executed |

**One pre-existing finding, not introduced here.** `docs/modal-production.md` and
`docs/superpowers/plans/2026-07-21-resume-aware-job-auto-applier.md` contain a
personal email address and are **already tracked on `main`** (`git diff main..HEAD`
shows both untouched). Reported because a privacy review should surface it, not
because this branch caused it.

The three new fixture PDFs committed to `bench/resumes/` contain **synthetic**
people generated from `bench/eval_people.py` — no real personal data.

---

## 21. Deployment impact

| Target | Required? | Why |
|---|---|---|
| **RENDER** | **YES** | Deploy the branch. `render.yaml` gains one env var (pinned `v1`). The migration itself is a dashboard variable change, no redeploy |
| **ORACLE WORKER** | **NO** | Verified by execution: `sweep_worker.render_profile` + `assert_only_literals` accept v1- and v2-stamped profiles unchanged; 42 deploy tests pass. The worker renders already-derived data, so its engine version affects only the stamp |
| **ORACLE INFERENCE** | **NO** | Not touched. The Oracle worker runs sweeps, not inference |
| **MODAL** | **YES** | `deploy/modal_serving.SOURCE_FILES` ships `inference.py`, `inference_service.py`, `local_extract.py`. Two changed. `inference.py`'s `done_reason` guard is inside `LocalOllama.generate`, which Modal's warmup calls. `local_extract.py` changed but Modal uses only `FIELDS_PROMPT`/`FIELDS_SCHEMA`, both byte-identical — functionally inert, though the service reports a SHA256 of all three |

Nothing was deployed.

---

## 22. Reviewer starting points

In the order I would read them.

| # | What | Path |
|---|---|---|
| 1 | **The held decision** — why step 4 is off | `docs/profile-engine-v2-evaluation.md` §5 |
| 2 | Importance rules and the bounded mapping | `skill_evidence.py` → `tier()`, `BANDS`, `weight()` |
| 3 | Canonical concepts and the alias table | `skill_concepts.py` → `CONCEPTS`, `resolve()`, `_key()` |
| 4 | Scorer matching and the span rule | `skill_concepts.py` → `score()`, `_inside()`; `scraper.py:score_job` |
| 5 | The feature flag | `skill_concepts.py` → `engine_version()`, `DEFAULT_VERSION`; `render.yaml` |
| 6 | Where v2 joins the pipeline | `auto-apply/make_profile.py` → `_finish()`, `reweight_from_evidence()` |
| 7 | Employment grounding | `local_extract.py` → `check_employment()`, `row_problems()`, `is_planned()` |
| 8 | Render/AST validation | `auto-apply/make_profile.py` → `_prose()`, `check_module()`, `profile_schema()` |
| 9 | Section parser (weakest component) | `skill_evidence.py` → `sections()`, `_is_entry()`, `_heading()` |
| 10 | Query validation (held) | `role_families.py` → `revalidate()`, `can_anchor()`, `MIN_PAIR_SHARE` |
| 11 | Evaluation harness and its limits | `bench/evaluate.py` docstring; `bench/eval_people.py` docstring |
| 12 | Row-order fix | `local_search.py` → `rank_title()`, `canonical()` |

**Three things I would challenge first if I were reviewing:**
`MIN_PAIR_SHARE = 0.50` (one corpus, one calibration) · `bench/eval_people.ROLES`
(implementer-authored labels) · `skill_evidence.sections()` (a mis-parsed section
silently changes a tier, and `ada`'s Python already shows it happening).

---

## 23. Diff summary

`git diff --stat 05c26f4..69d54f0` — 35 files, **+6640 / −68**.

### Highest risk by semantic importance, not line count

| Rank | File | Lines | Why |
|---|---|---:|---|
| 1 | `skill_evidence.py` | +622 | Decides every weight under v2. A section mis-parse silently changes a tier |
| 2 | `skill_concepts.py` | +671 | Canonicalization is **irreversible in effect** — a wrong merge hides a skill everywhere |
| 3 | `scraper.py` | +25 | Only 25 lines, but it changes **what every job scores**. Smallest diff, largest blast radius |
| 4 | `auto-apply/make_profile.py` | +353 | Engine seam, profile rendering, AST validation, schema stamp |
| 5 | `local_extract.py` | +190 | Can now REJECT a parse that v1 accepted — a new failure mode |
| 6 | `local_search.py` | +72 | `rank_title` changes real query output; `fields_for` grew a branch |
| 7 | `role_families.py` | +676 | Largest new file, **entirely inert** — held behind a flag |
| 8 | `render.yaml` | +7 | One variable stands between the public beta and v2 |
| 9 | `inference.py` | +10 | Forces a Modal redeploy |
| 10 | `sweep/*` | +160 | UI and review defaults; user-visible, low blast radius |

Machine-readable manifest: `output/profile-engine-v2-review/manifest.json`
(base/head SHAs, 11 commits, flags and defaults, test commands and results,
evaluation conditions, known failures, 35 changed modules, artifact paths). No
secrets, no résumé text.

---

## 24. Final self-assessment

**IMPLEMENTATION COMPLETENESS:**
partial

**EVALUATION STRENGTH:**
moderate

**PRODUCTION READINESS:**
needs review

**BIGGEST UNRESOLVED TECHNICAL RISK:**
Importance tiers rest on a line-based section parser that already mis-attributes on
a benchmark persona — `ada`'s Python is SUPPORTING because her bullets say "Django"
— so an unusual résumé layout can silently move a skill one or two tiers and change
what ranks first.

**BIGGEST EVIDENCE GAP:**
No independently labelled data of any kind: the fixtures are synthetic, the role
labels were written by the implementer, and there is no judged job-ranking set — so
v2 is supported as "fixes defects and breaks no invariant" but has never been shown
to produce better job results.
