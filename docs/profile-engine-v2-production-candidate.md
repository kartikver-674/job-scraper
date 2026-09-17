# Profile Engine v2 — production candidate

The evaluated skill/weight engine, on a clean branch off `main`, prepared for
public beta. **Not merged. Not deployed.**

| | |
|---|---|
| Release branch | `release/profile-engine-v2-beta` |
| Base | `main` @ `05c26f4` |
| Branch SHA | `3b1ab2c` (this handoff adds one more) |
| Evaluated engine | `8e0e428` on `feat/profile-engine-v2-canonical-hardening` |
| Evaluation report | `02dcb66` · [profile-engine-v2-skill-evaluation.md](profile-engine-v2-skill-evaluation.md) |
| Public beta target | **v2** |
| Code fallback | **v1** |
| Role construction | **off, and not shipped** |

---

## 1. What is in the branch, and what is not

A clean branch off `main`, not a merge. The experimental parent carries
unfinished R5 and evaluation harnesses; neither is here.

**Included** — employment and generated-profile safety fixes, the engine-version
contract, canonical concepts, R3 canonical market weighting, R4a occurrence
semantics, R4b evidence strength, R4c clause-scoped attribution, and the
production-path tests for all of them.

**Excluded, and absent from the working tree:**

| Excluded | Why |
|---|---|
| `role_families.py` | R5. Never finished, never evaluated |
| `auto-apply/tests/test_role_families.py` | tests the above |
| `bench/evaluate.py`, `bench/eval_people.py` | evaluation harness, not runtime |
| `bench/resumes/{omar,priya,yuki}-*` | fixtures for that harness |
| the `role_families` branch in `local_search.fields_for` | the only production path that could have activated R5 |

`fields_for` still accepts `importance` and `resume_text` and ignores them, so
the caller's signature does not have to change when R5 is eventually finished.

### 30 files changed against main

Runtime: `skill_concepts.py` (new), `skill_evidence.py` (new), `config.py`,
`scraper.py`, `inference.py`, `local_extract.py`, `local_search.py`,
`auto-apply/make_profile.py`, `sweep/app.py`, `sweep/logic.py`,
`sweep/public.py`, three Sweep templates/CSS, `render.yaml`.

Tests: 8 new suites under `auto-apply/tests/`, plus updates to
`test_make_profile.py`, `test_inference.py`, `bench/test_backends.py`,
`sweep/tests/{test_app,test_public,test_title_corpus}.py`.

## 2. The clean branch IS the evaluated engine

The 30 sealed evaluation candidates were re-run on this branch against the same
frozen corpus and configuration.

| Check | Result |
|---|---|
| Per-candidate SHA-256 of the full prediction artifact | **30 / 30 identical** |
| v1 weights | identical, every candidate |
| v2 weights | identical, every candidate |
| v2 concept set | identical, every candidate |
| Concept rows compared field by field | **306 / 306 identical** |

Fields compared per row: `id`, `tier`, `weight`, `market_separation`,
`evidence_strength`, `independent_entries`, `why`, `sections`, `status_counts`,
the resolved market key, and every occurrence's status, shape, section, span and
digest.

Re-verified twice more after the three release-specific edits below. Still
30 / 30.

## 3. Three release-specific changes, none in the scoring path

1. **`render.yaml` requests v2 explicitly.** The code default stays v1, so this
   one line is the whole decision and the rollback is this one line back.
2. **`skill_concepts.roles_enabled()` is hard-false.** With `role_families.py`
   unshipped, `SWEEP_ROLE_FAMILIES` had nothing to switch on but would still
   have made `/healthz` report roles as ON. A flag that lies is worse than no
   flag. `ROLES_FLAG` stays so the contract test keeps asserting the answer.
3. **A stale comment corrected.** `skill_concepts.py` still claimed the code
   default was v2 — true of the experimental branch, and the exact defect the
   independent review found. Corrected rather than left to mislead whoever reads
   that file during a rollback.

Plus `/healthz` reporting (§7) and two re-measured tests (§9).

## 4. Rollback and profile lifecycle — verified

Twelve cases, each in its own interpreter, because `config` and `scraper` bind
at import and that is the moment under test.

| # | Case | Result |
|---|---|---|
| 1 | new résumé after rollout, Render env says v2 | derives **v2** |
| 2 | existing v1 profile, worker env says v2 | scores **v1** (8) — the profile wins |
| 3 | v2 profile, worker has no variable | scores **v2** (5) |
| 4 | v1 profile, worker has no variable | scores **v1** (8) |
| 5 | **v2 profile scored AFTER rollback to v1** | still **v2** (5) |
| 6 | v1 profile made just after rollback | **v1** (8) |
| 7 | legacy profile, no stamp, env says v2 | **v1** — documented safe behaviour |
| 8 | unreadable future schema | **exits 1** with the reason, scores nothing |
| 9 | stale step flags vs a v1 rollback | **v1** — flags cannot override |
| 10 | `SWEEP_ROLE_FAMILIES=1` on a v2 profile | roles **False** |
| 11 | no profile, no variable | **v1**, source `default` |
| 12 | `SWEEP_PROFILE_ENGINE_VERSION=v7` | **exits 1**, names the valid values |

**Case 5 is the one to understand before rolling back.** A profile carries the
engine that derived it. Rollback changes what NEW profiles derive with; it does
not reinterpret profiles that already exist, and it must not — that is how a
profile is prevented from silently changing meaning because a machine's
environment changed.

## 5. Deployment matrix

| System | Code update | Env change | Restart / redeploy | Why |
|---|---|---|---|---|
| **Render** (`sweep-beta` web) | **yes** | **yes** | **yes** | Every runtime file. `SWEEP_PROFILE_ENGINE_VERSION=v2` is new in `render.yaml`; if the service's env was set manually rather than synced from the blueprint, add the key by hand |
| **Oracle worker** (`/opt/sweep-worker/app`) | **yes** | **no — and must NOT be given the version variable** | **yes** | It spawns `scraper.py` from its own checkout. `scraper.py`, `config.py`, `skill_concepts.py`, `skill_evidence.py`, `auto-apply/make_profile.py`, `local_extract.py`, `local_search.py` all changed. Without them `config.load_profile_module` does not exist and a v2 profile would be scored by v1 patterns **silently** |
| **Oracle inference** (3-file install) | **yes — `inference.py` only** | no | **yes** (`systemctl restart sweep-inference`) | The truncation guard. `inference_service.py` and `gunicorn.conf.py` are unchanged. This is the fallback host; Render points at Modal |
| **Modal** (`deploy/modal_serving.py`) | **yes** | no | **yes** (`modal deploy deploy/modal_serving.py`) | It ships `inference.py`, `inference_service.py`, `local_extract.py`; **two of the three changed** |

**Deployment order is not free.** The Oracle worker must be on the new checkout
**before** Render starts deriving v2 profiles. An old worker does not read
`PROFILE_SCHEMA`, so it would score a v2 profile with v1 patterns and say
nothing.

    1. Oracle worker: git pull, restart, confirm it still scores a v1 profile
    2. Oracle inference + Modal: ship inference.py / local_extract.py
    3. Render: deploy the branch with SWEEP_PROFILE_ENGINE_VERSION=v2
    4. /healthz: confirm derivation_engine = v2

## 6. Exact public configuration

```yaml
# render.yaml, sweep-beta
- key: SWEEP_PROFILE_ENGINE
  value: local
- key: SWEEP_PROFILE_ENGINE_VERSION
  value: v2          # PUBLIC BETA. Rollback = v1. Code default is v1.
```

Not set anywhere, deliberately: `SWEEP_SKILL_CONCEPTS`, `SWEEP_SKILL_EVIDENCE`,
`SWEEP_ROLE_FAMILIES`. The version variable subsumes the first two, and the
third has nothing to switch on. The worker gets **no** engine variable.

## 7. Health and observability

`/healthz` now carries four more fields, no secrets, no user data, no disk or
model access:

```json
{"status": "ok", "mode": "public-beta",
 "market_signal_source": "frozen", "title_corpus_source": "...",
 "derivation_engine": "v2", "engine_source": "SWEEP_PROFILE_ENGINE_VERSION",
 "role_families": false, "profile_schema": 1}
```

`derivation_engine` is what THIS process would derive with. A profile's own
stamp still beats it, and the worker answers for itself. Two tests assert the
fields and that a rollback is visible in them.

## 8. Smoke results (cached replays, no model call)

| Case | Terms | Concepts | Weight spread | Renders | Stamp | R5 in output |
|---|---:|---:|---|---|---|---|
| development résumé (audited) | 57 | 46 | 2–5 | yes | `engine: 'v2'` | **no** |
| graduate / internship-heavy | 9 | 9 | 2–3 | yes | `engine: 'v2'` | **no** |
| backend / full-stack | 14 | 13 | 2–5 | yes | `engine: 'v2'` | **no** |
| platform / Salesforce | 12 | 9 | 2–5 | yes | `engine: 'v2'` | **no** |
| unknown technologies | 7 | 7 | 2 and 5 | yes | `engine: 'v2'` | **no** |

Development résumé top skills: Apex 5, OAuth 5, REST APIs 5, React Native 5
(INDEPENDENT_USE). Unknown-technology case: all four invented technologies at
CORE 5 while the three listed-only real ones stay at 2.

Every case produced a search configuration, and no output contains
`role_families`.

## 9. Tests

| Suite | Tests | Result |
|---|---:|---|
| auto-apply | 728 | OK |
| Sweep | 773 | OK |
| bench | 43 | OK |
| deploy / worker | 42 | OK |
| **Total** | **1586** | **0 failures** |

52-document regression, both engines: 52/52 years exact, macro F1 0.97628,
router 38 / 14 / 0 — unchanged from every prior batch.

Module self-checks pass: `skill_concepts`, `skill_evidence`, `local_extract`,
`corpus_signal`, `local_search`.

**Two tests changed, both re-measured rather than relaxed:**

- `test_a_beta_visitors_profile_is_not_a_flat_wall_of_threes` asserted
  `maven: 4` for a build tool that appears nowhere in that fixture — whose
  extracted text is the single line *"Ada Okonkwo, React Native dev"*. v1 scored
  it off the market table alone, which is the defect this engine exists to fix.
  It now asserts that v2 refuses, and a **new** test gives the same ten skills a
  résumé that describes the work and requires the engine to reach the top of the
  scale.
- `test_render_pins_the_engine_version_the_beta_runs` now asserts v2 in
  `render.yaml`, v1 as the code default, and roles off.

**There is no R5 `expectedFailure` on this branch.** The evaluator-isolation
tests were removed with `bench/evaluate.py`, and the role test suite is not
here, because the code both document is not here either. The contract they
protected — a bound profile beats the environment — is still asserted from
production paths (§4).

## 10. Privacy

The complete 8,105-line branch diff was scanned. **Zero** matches for Apify,
Modal, Oracle or AWS tokens, bearer headers, private keys, base64 blobs, or
local paths.

One new private reference was found and removed: a `skill_evidence.py` comment
quoted a real employer name and city as its example of a raw entry label. It now
describes the shape without naming anyone, and Phase 2 equivalence was re-run
after the edit (still 30/30).

Two pre-existing references remain and are **out of scope for this release**
because they are already on `main`: `profiles/kartik_reachable.py` (a tracked
profile the repo owner keeps) and a `DealerMatix` employer string in
`bench/test_backends.py`, present on main four times. Worth a separate cleanup;
not something a release branch should quietly widen.

Evaluation artifacts stay in gitignored `output/profile-engine-v2-evaluation/`
and `output/profile-engine-v2-release/`.

## 11. Production smoke plan

Run in order after deploying. Each step names the evidence that proves it.

| # | Step | Evidence |
|---|---|---|
| 1 | `GET /healthz` | `derivation_engine: "v2"`, `engine_source: "SWEEP_PROFILE_ENGINE_VERSION"`, `role_families: false`, `profile_schema: 1` |
| 2 | Upload a valid résumé through the beta gate | 302 to `/derive`, no 5xx |
| 3 | Profile parse succeeds | `/review` renders; skills list is non-empty |
| 4 | Review shows v2 weights | weights are **not** a flat wall of 3s; at least one 4 or 5 on a résumé with described work |
| 5 | No unexpected preselected removals | nothing the résumé describes is preselected for deletion |
| 6 | Download `/profile.py` | contains `PROFILE_SCHEMA = {"version": 1, "engine": 'v2'}` |
| 7 | Start a Free Sweep | worker accepts; run id returned |
| 8 | Worker scores with the bound engine | worker log shows the profile loaded; scores are non-zero |
| 9 | Results return | rows rendered, `matched_skills` uses concept display names |
| 10 | Export CSV / XLSX / JSON | all three download and open |
| 11 | One paid run **only if** a controlled, inexpensive smoke is appropriate | one search, one site, key not persisted |
| 12 | No R5 activation | no `role_families` key anywhere in the profile or results; `/healthz` still `role_families: false` |
| 13 | No secret or key persistence | Apify key absent from the worker after the run; no key in any log line |

Stop and roll back if step 1 does not say v2, if step 4 is flat, or if step 12
finds any role output.

## 12. Rollback plan

**Primary rollback is one value.**

    render.yaml:  SWEEP_PROFILE_ENGINE_VERSION: v2  ->  v1
    then redeploy the Render service (or change the env var in the
    Render dashboard and restart — no code change, no other service)

| | |
|---|---|
| Services needing a restart | **Render only** |
| Oracle worker | **nothing** — it holds no engine variable and its checkout is version-agnostic |
| Oracle inference / Modal | **nothing** — neither reads the engine version |
| Code revert needed | **no** |

**Behaviour after rollback:**

- **Profiles already derived as v2 keep scoring as v2.** Their stamp binds them
  and the worker honours it (case 5, §4). This is intended: a profile must not
  change meaning because a machine's environment changed. If a specific v2
  profile must become v1, re-derive it after the rollback.
- **New profiles derive as v1** from the next `/derive`.
- Legacy profiles with no stamp continue to score v1, as they always have.

**Smoke after rollback:** `/healthz` reports `derivation_engine: "v1"`; a fresh
upload produces a profile stamped `engine: 'v1'`; an existing v2 profile still
scores with v2 and that is correct.

**Full code rollback**, only if the branch itself is faulty rather than the
engine choice: redeploy `main` @ `05c26f4` to Render, `git checkout main` on the
Oracle worker and restart, re-deploy Modal from `main`. Profiles stamped v2
become unreadable-by-omission — old code ignores `PROFILE_SCHEMA` and scores
them with v1 patterns — so any v2 profile in flight should be re-derived.

## 13. Known limitations, carried forward unchanged

Every one of these was measured in the evaluation and is **deliberately not
fixed**. Fixing any would produce an engine nobody has evaluated.

- **CORE is over-populated.** Against the evaluation labels, v2's CORE precision
  is **0.409**: of 110 CORE predictions, 62 were labelled STRONG_SECONDARY. The
  cause is the tier mapping's work rule — any described professional use is CORE
  — not the `_ATTACH` preposition lexicon, which accounts for only 11 of them.
- **STRONG_SECONDARY is effectively not predicted** (per-tier F1 0.125).
- **`learned` is missing from the learning lexicon.** Past-tense self-teaching
  ("Learned Python in evenings…") reads as professional use.
- **The market adjustment hurts** agreement with labelled candidate importance:
  it breaks 119 ties at the cost of 73 new inversions. Bounded — no inversion
  crosses more than one tier — but not free.
- **PDF letter spacing still moves weights** (43 across 30 candidates).
- **`classify()` is line-scoped while `shape()` is clause-scoped**, so a concept
  can be USED from a verb elsewhere on its line while its shape is correctly
  CLAIM_ONLY.
- **v2 depends on the extracted résumé TEXT.** Where v1 produced a market-shaped
  spread regardless of the document, v2 will flatten to 1–2 if extraction is
  poor. This is correct behaviour and a new operational sensitivity: a bad PDF
  extraction is now visible in the weights instead of hidden by the market
  table.
- **The evaluation's labeller was the same agent that built the engine**, and
  the rubric shares its frame with v2's policy. Structural controls bound this;
  they do not remove it.

---

## Status

**PUBLIC BETA TARGET:** v2

**CODE FALLBACK:** v1

**ROLE CONSTRUCTION:** off — and `role_families.py` is not in the branch

**R5:** not included

**CORE CALIBRATION:** known open issue, unchanged

**MARKET ADJUSTMENT:** known evaluation concern, unchanged

**PROVEN BETTER CANDIDATE REPRESENTATION:** yes, within the current evaluation protocol

**PROVEN BETTER JOBS:** no

---

Not merged. Not deployed. Returned for final review.
