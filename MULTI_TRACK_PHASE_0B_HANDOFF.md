# Multi-Track Phase 0b-A — Handoff

Phase 0b fixes an existing one-résumé production mismatch (design D-1, decision
B2): Render derives résumés with engine **v2**, but the Oracle worker renders
every public profile with its own default, **v1**, so public jobs are scored by
a different engine from the one that built the profile.

This is **0b-A only**: backward-compatible explicit engine transport, plus the
offline v1 → v2 comparison.

- **0b-B (Render actually sending the engine) is NOT done.**
  - The switch exists and defaults to off.
  - No environment, `render.yaml` or deploy file was changed.
- Nothing is committed, pushed or deployed. No provider or model was called.
- Phase 4 has not started.
- **Waiting for reviewer sign-off.**

---

## A. Git Starting State

| | |
|---|---|
| Branch | `feat/multi-track-search` |
| HEAD | `287c1517388cee4c8020613959b2b7c01838875f` (`feat(multi-track): add unified search planning`) |
| Upstream | `origin/feat/multi-track-search` at `cb602ec`; local 4 ahead, 0 behind |
| Working tree | clean |

## B. Current Engine Mismatch (recap, from code and the 2026-09-25 production check)

| Where | Engine | Why |
|---|---|---|
| Render derivation | v2 | `render.yaml` sets `SWEEP_PROFILE_ENGINE_VERSION=v2`; live `/healthz` confirmed it (design §A.3) |
| Worker rendering (`/v1/plans`, `/v1/runs`) | v1 | `render_profile` called `make_profile.render(name, profile, prefs)`; with no engine variable and nothing bound, `engine_version()` returns `DEFAULT_VERSION = "v1"` |
| Worker's engine child | v1 | `config.PROFILE_ENGINE` is read from the file's stamp; `scraper.py` binds it at import (`skill_concepts.bind`) |

**What the engine changes.** Only the positive-skill sum and the `matched_skills` text (`scraper.evaluate`, the `ctx.engine == "v2"` branch). Eligibility, filters and dedupe do not read it.

## C. Production Files Modified

| File | Change | What |
|---|---|---|
| `auto-apply/make_profile.py` | +23 / −2 | `stamp_engine(engine=None)`; `render(name, data, prefs, engine=None)` stamps `stamp_engine(engine)` |
| `deploy/sweep_worker.py` | +42 / −11 | `engine` added to `_checked`'s allowlist; `_requested_engine(payload, checkout)`; `render_profile(..., engine=None)`; `/v1/plans` and `/v1/runs` pass it; `/v1/plans` adds `--attest-engine` only for an explicit engine |
| `scraper.py` | +13 | opt-in `--attest-engine` (with `--dry-run --json` only); schema-1 dry run adds `profile_engine = config.PROFILE_ENGINE` when asked |
| `sweep/worker_client.py` | +32 / −8 | `plan(..., engine=None)`, `create_run(..., engine=None)`, sent only when given; `EngineMismatch(WorkerError)`; `check_engine(raw, engine)` |
| `sweep/worker_link.py` | +43 / −2 | the activation switch `ENGINE_FLAG = "SWEEP_SEND_PROFILE_ENGINE"` (default off), `sends_engine()`, `engine_for(state)`; `fetch_plan` sends and verifies; `start_sweep` sends the same value |
| `sweep/app.py` | +8 / −2 | records `state["derived_engine"]` with each successful derivation; clears it on a new upload |
| `sweep/public.py` | +6 | Render `/healthz` reports `engine_propagation` (the switch), so activation and rollback can be confirmed |

**Not touched:** `render.yaml`, environment files, deploy scripts, `config.py`, `skill_concepts.py`, `sweep/plan.py`, `sweep/runs.py`, templates, the schema-2 path (`render_sweep`, `multi_plans`, `finalize_multi`).

**Tests and docs**

| File | Change |
|---|---|
| `sweep/tests/test_engine_propagation.py` | **new**, 39 tests (§J) |
| `MULTI_TRACK_PHASE_0B_HANDOFF.md` | **new**, this file |

No existing test and no fixture was changed.

## D. Exact Explicit-Engine Precedence

**Before (re-verified from code, not the design).** `skill_concepts.engine_version()` with no argument resolves:

1. `_BOUND`, set only by `skill_concepts.bind`, which only `scraper.py:369-370` calls, when a loaded schema-1 profile carries a stamp;
2. `SWEEP_PROFILE_ENGINE_VERSION`, when non-empty, validated to `v1`/`v2`;
3. step flags `SWEEP_SKILL_CONCEPTS` + `SWEEP_SKILL_EVIDENCE`: both on → v2; neither → default; one → `"mixed"`, which `bind()` refuses;
4. `DEFAULT_VERSION = "v1"`.

`render()` wrote that value into `PROFILE_SCHEMA`, in whichever process rendered it. The worker has no `_BOUND` (it never imports `scraper`) and none of the variables, so it stamped `v1`.

**After.**

| `render(..., engine=)` | Stamp |
|---|---|
| `None` (every existing caller) | `skill_concepts.engine_version()`: exactly the precedence above, unchanged |
| `"v1"` / `"v2"` | `skill_concepts.engine_version(engine)`: the canonical check, which ignores `_BOUND` and every environment variable |
| `"mixed"`, unknown string, `""`, whitespace, non-string | `ValueError` (the canonical check refuses `mixed`/unknown; `stamp_engine` refuses empty and non-string input, which `engine_version("")` would silently turn into `None`) |

- The explicit value changes **only** the stamp line. That is tested byte-for-byte: `render(engine="v2")` equals `render()` with `'v1'` replaced by `'v2'` in the stamp.
- The canonical check normalises case and whitespace, so `" V2 "` stamps `v2`.
- The worker boundary is stricter: exact membership in `skill_concepts.VERSIONS`.

**At scoring time (unchanged).** The engine child reads the stamp (`config.PROFILE_ENGINE`) and binds it. The stamp therefore decides scoring, and the worker's environment cannot.

## E. Worker Contract Compatibility

The existing `profile` request form on `/v1/plans` and `/v1/runs` gains one optional top-level field, `engine`.

| Body | Result |
|---|---|
| no `engine` (today's Render) | exactly today: `render(name, profile, prefs)` with three arguments; plan argv `scraper.py --profile <n> --dry-run --json`; response unchanged |
| `"engine": null` | same as absent (the convention `apify_token` and `key_ids` already use) |
| `"engine": "v1"` / `"v2"` | `render(..., engine=<value>)`; the plan child also gets `--attest-engine` |
| unknown string (`mixed`, `v3`, `""`, `"V2 "`, …) | **400** `engine must be one of v1, v2`; nothing rendered, no child run, no run created |
| wrong type (number, bool, list, object) | **400** `engine must be a string when given` |
| unknown field (`engines`, `tracks`, …) | **400** `unknown field(s): …`, as before |

- `_checked`'s return shape is unchanged; `engine` is read by a separate `_requested_engine`.
- Refusals name the type or the allowed values, never what was sent.
- Not implemented here: `tracks`, `plan_hash`, `track_ids`, schema-2 worker support.

## F. Render State Lifetime (`state["derived_engine"]`)

| Event | `derived_engine` |
|---|---|
| Successful derivation (`derived_for_state`) | set to `skill_concepts.engine_version()`, read immediately before the derivation runs and stored with its result |
| Failed derivation | not set |
| Cached re-read (`/derive`, `/review` reload) | unchanged; no second derivation, nothing re-read |
| Process environment changes after derivation | unchanged (tested: derived under v2, environment switched to v1, recorded value still v2 and still sent) |
| Review edits (`POST /review`) and results re-weighting (`POST /rescore`) | kept: they edit **the same derivation** (weights and terms), so its engine is still the one that derived it |
| `/estimate` candidate copy and restore; `commit_prefs` copy | kept (the whole state is copied) |
| New résumé upload (`POST /resume`) | **cleared**, together with `derived` |
| Next successful derivation | **replaced** |
| Public session TTL/cap eviction; Render restart | lost together with `derived` (memory only) |
| Cookie rehydration after a restart | never restored: rehydration restores no `derived` either, so a new sweep needs the résumé again |

**Compatibility rule for state without `derived_engine`.**
- When a `derived` has no recorded engine (only injected or test state), `engine_for` returns `None`. The request is then the legacy one, and the worker stamps its default (v1), exactly as today, even with the switch on.
- This cannot arise in production. The deploy that introduces the field restarts Render, which drops every in-memory session. Every derivation after it records its engine.

**Local console mode** needs nothing. It never uses `worker_link`, and it renders in the same process that derived, so its `render()` calls are unchanged.

## G. Dry-Run Attestation and Mismatch Behaviour

**Attestation.**
- `scraper.py --dry-run --json --attest-engine` appends `"profile_engine": config.PROFILE_ENGINE` to a schema-1 dry run.
- That is the stamp the child's own loader read from the file, and the engine the same import bound.
- It is never the request's own word for it: a mutation that attested the process environment instead was caught.
- Schema 2 never emits it (the `elif` sits after the schema-2 branch).

**Deliberate deviation from the design text.**
- Design §A.4 says the dry-run JSON "adds `profile_engine`". Unconditionally, that changes the schema-1 dry-run bytes that the Phase 0a goldens freeze, and the legacy `/v1/plans` response.
- It is therefore **opt-in**. The worker passes `--attest-engine` only for a request that named an engine.
- As a result:
  - legacy dry runs are byte-identical (goldens 35/35, no fixture change);
  - the child's attested JSON is the legacy JSON plus one trailing key (tested on the child's own stdout).

**Render's check** (`worker_client.check_engine`, called in `worker_link.fetch_plan`):

| Recorded engine sent | Worker's `profile_engine` | Result |
|---|---|---|
| v2 | v2 | valid; the plan proceeds |
| v2 | v1 | `EngineMismatch` |
| v2 | absent / null | `EngineMismatch` |
| none sent (switch off, or no recorded engine) | not requested | not checked (legacy) |

- **Fail closed.** `EngineMismatch` is a `WorkerError`, raised *after* the free-sweep fallback. So it is never turned into a free plan.
  - `costed()` turns it into `PlanUnavailable`.
  - `/run` re-prices through `fetch_plan` immediately before every start, so neither a free nor a paid sweep starts.
  - Tested end to end: a paid run on a v1-attested or unattested plan creates no run on the worker; a v2-attested one starts and its run file is stamped v2.
- No retry without the engine, and no fallback to v1.
- **The one intended fallback is unchanged.** An *unreachable* worker still lets a free sweep proceed on a free plan, exactly as today.
- **An old worker with the switch on** refuses the field (400). The free-plan fallback applies, but `create_run` is refused too, so no run is created. It fails loudly and never runs v1 silently (tested). This is why the worker deploys first.

## H. Activation Boundary

| | Compatibility (0b-A, this change) | Activation (0b-B, **not done**) |
|---|---|---|
| Worker | accepts optional `engine`; absent → today | — |
| Render state | records `derived_engine` (inert) | — |
| Client | can send `engine`; omitted → today's body | — |
| Render traffic | sends **nothing new** | `SWEEP_SEND_PROFILE_ENGINE=1` on Render: plan and run carry the recorded engine, and `profile_engine` is verified |
| Public scoring | v1, unchanged | v2 for newly derived v2 profiles |

- `worker_link.ENGINE_FLAG` is the only switch.
- It is read per call, with a strict parse: only `1`/`true`/`yes`/`on` count as on.
- It is off unless set, and nothing sets it: `render.yaml` is unchanged.
- Render `/healthz` reports `"engine_propagation": false|true`.

## I. P0 Single-Résumé Compatibility

| Guarantee | Evidence |
|---|---|
| Legacy (no-engine) render bytes | Pristine `git archive 287c151` vs this tree, same probe, 3 rounds: identical SHA-256 over legacy `render()` of all 53 synthetic profiles, with no engine variable (the worker's state) and with v2 |
| Worker legacy render | identical SHA-256 of `sweep_worker.render_profile(...)` (5-argument call) in both trees |
| Schema-1 dry-run JSON | default dry run identical in both trees (7,005 bytes); Phase 0a plan goldens 35/35; no fixture changed |
| Legacy request bodies | `worker_client.plan` / `create_run` without `engine` produce today's exact bodies (tested) |
| Legacy worker behaviour | legacy `/v1/runs` renders exactly `render(name, profile, free_prefs)`; legacy `/v1/plans` argv exactly today's (tested) |
| Switch off | a full public free sweep through Render → real worker → real dry run sends no engine and runs stamped v1 (tested) |
| Plan, acquisition, cost, authorization, provider requests, filters, dedupe, exports, worker auth, BYOK, kill switch | untouched code paths; the Sweep, deploy and bench suites (§J) |
| Timing | render 0.425 vs 0.424 ms/profile; dry run 0.235–0.248 ms in both trees (medians; one 0.737 ms pristine outlier in round 1) |

## J. Test Results

(all in a sealed environment, `env -i PATH HOME LANG`; logs in scratch, scanned for token-shaped strings before reading)

| Run | Result |
|---|---|
| `sweep.tests.test_engine_propagation` (new, Phase 0b) | **39/39 OK** |
| `sweep.tests.test_one_track_goldens` (Phase 0a) | **35/35 OK**; `git diff -- sweep/tests/fixtures/` empty |
| `sweep.tests.test_scoring_context` (Phase 1) | **19/19 OK** |
| `sweep.tests.test_track_loading` (Phase 2A) | **18/18 OK** |
| `sweep.tests.test_multi_results` + `test_preference_filter_baseline` (Phase 2B) | **35/35 OK** |
| `sweep.tests.test_multi_plan` (Phase 3) | **52/52 OK** |
| Routes: `test_app`, `test_public`, `test_worker_link`, `test_public_sweep`, `test_public_paid`, `test_public_copy`, `test_active_run` | **707 OK**, 1 skipped (`node not installed`, environmental) |
| `auto-apply` `tests.test_engine_contract` | **38/38 OK** |
| Full Sweep (`discover -s sweep/tests`) | **1,860 OK** (1,821 before + 39), 3 skipped (environmental: `node`, absent analysis artifacts) |
| Worker/deploy (`deploy.test_sweep_worker`, `deploy.test_modal_benchmark`) | **42 OK** |
| Bench (`bench.test_backends`, `bench.test_answer_key`) | **43 OK** |
| auto-apply (`discover -s tests`) | **1,102 run, 2 errors**: the known `test_inference` `/healthz` `HTTPError 503` pair, untouched (same as Phases 0a–3) |
| `scraper.py --demo`, `merge_jobs.py --demo`, `config.py`, `python -m sources`, `python -m sweep.exports`, `skill_concepts.py`, `make_profile.py --help` | all exit 0 |
| Token-shaped strings in any log | 0 |

**`test_engine_propagation.py` (39)**

| Class | Covers |
|---|---|
| RenderEngine (7) | `None` is today's render in every environment; explicit v1 beats v2 env; explicit v2 with no env; explicit beats a bound profile; only the stamp differs; `mixed`/unknown/empty/malformed refused; canonical normalisation |
| WorkerRuns (4) | legacy body renders exactly as before; null = absent; explicit engine is the stamp under every worker env; worker default still v1 without the field |
| WorkerRefusals (3) | unknown values and wrong types → 400 on both endpoints, nothing rendered or run; unknown-field strictness kept |
| WorkerPlans (2) | legacy plan argv exact; explicit engine adds `--attest-engine`, file stamped as asked |
| DryRunAttestation (5) | the real `scraper.py` child in a fresh interpreter: legacy plan has no `profile_engine`; attested engine = stamp bound; the child's JSON = today's + one trailing key; explicit v1 holds where worker and child env say v2; `--attest-engine` refused outside `--dry-run --json` |
| ClientBodies (3) | legacy bodies exact; explicit engine sent by plan and run; `check_engine` |
| LinkPropagation (6) | switch off → nothing new sent; switch on → plan and run send the same recorded value; no recorded engine → legacy; mismatch/missing attestation fails closed, free or paid; unreachable worker keeps the free fallback; strict switch parsing |
| RenderState (4) | recorded at derivation; later env change does not alter it; new upload clears, re-derivation replaces; failed derivation records nothing |
| PublicEndToEnd (5) | Render public app → worker on a socket → real dry run: switch off = today; switch on = v2 attested and run stamped v2; old worker fails loudly with no run; paid mismatched plan never starts; paid matching plan starts stamped v2 |

**Mutation check (scratch, not committed).**
- 21 faults were injected one at a time into the seven production files.
- **All 21 were caught** by `test_engine_propagation`.
- The files were restored byte-identically (diff hash verified before and after).
- The faults:
  - `render` ignores the explicit engine;
  - `render` accepts empty/non-string input;
  - the worker allowlist lacks `engine` (an old worker);
  - the worker accepts `mixed`;
  - the worker accepts wrong types;
  - the run ignores the engine;
  - the plan render ignores the engine;
  - the plan always attests;
  - the plan never attests;
  - the dry run always attests;
  - the attestation comes from the process env, not the stamp;
  - the client always sends an `engine` key;
  - `check_engine` accepts a missing attestation;
  - the link sends regardless of the switch;
  - the link re-reads the process engine instead of the recorded one;
  - the mismatch is swallowed by the free fallback;
  - the mismatch is not checked;
  - the run does not send the engine;
  - loose switch parsing;
  - the engine is not recorded;
  - a new upload keeps the old engine.

## K. Offline v1 / v2 Comparison — Methodology

No provider call, no model call, no real résumé. Everything ran in scratch.

**Profiles: 52 synthetic derivations (13 personas × 4 layouts), exactly design §A.2's set, rebuilt.**
- The two model calls were replaced by the committed captures of the local model's own answers for the synthetic personas: `bench/results/qwen3_8b.json` (fields) and `bench/results/dates-qwen3_8b.json` (employment).
- Input was the committed PDFs in `bench/resumes/`.
- Everything after the model calls was today's production engine (`make_profile.generate_local` → `_finish`), under `SWEEP_PROFILE_ENGINE_VERSION=v2` as on Render.
- Empty output dir (frozen corpora, as on Render), sockets refused.
- 52 derived, 0 escalations.
- Never `real-qwen3_8b.json`, never `output/`.
- Personas: software (ada 4y, bhaskar 0, chen 7, dmitri 5, gopal 7, lena 1, mateo 3), full-stack (esi 2), data engineering (farida 3, jonas 6, kwame 1), ML (hana 8), frontend (iris 5).
- **Supplementary 53rd profile:** the committed Phase 0a `scoring_profile.json`. It is synthetic and hand-written with several spellings per concept. Reported separately.

**Job corpus: 183 rows with descriptions.**
- **Committed (27):** Phase 0a `scoring_rows.json` (18) and Phase 2B `test_multi_results.ROWS` (9).
- **Clearly synthetic (156):** generated mechanically.
  - Seed 20260926; 12 jobs per persona.
  - Title from the persona's own role keywords (12% "Senior", 5% "Intern").
  - 3–7 of the persona's own concepts plus 0–2 from any persona.
  - Each concept is spelled with a random spelling from the concept registry (display name or alias).
  - With probability 0.35 it is written two ways ("GraphQL (graphql)"); 77 of the 156 jobs have such a double spelling.
  - A random experience line; Indian city; "1 day ago".
- All parameters were fixed before any run.
- No committed corpus with realistic descriptions spanning these fields exists, which is why the design allowed a synthetic set.

**Scoring.**
- Each derivation was rendered twice with the Phase 0b renderer: `render(engine="v1")` (today's worker stamp) and `render(engine="v2")`, with India preferences.
- Each of the 106 files was loaded in a **fresh interpreter with no engine variable**, so the engine came only from the stamp: the worker's situation.
- In every child, stamp, bound engine and context engine agreed: v1/v1/v1 and v2/v2/v2.
- Per row: `scraper.evaluate` under `default_context()`. Then `scraper.finalize` over the corpus.

**Pairs.** 52 × 183 = **9,516**, plus 183 for the supplementary profile.

## L. Eligibility Comparison

| | v1 | v2 |
|---|---|---|
| Kept (eligible) pairs, 52 profiles | 8,132 | 8,132 |
| Pairs whose kept/dropped status or drop reason differs | **0** | |
| Profiles whose `finalize` kept set differs | **0 of 52** | |
| Supplementary profile: kept / differing / finalize set | 145 / 0 / equal | 145 / 0 / equal |

**Kept/dropped is identical: the approved condition holds.**

## M. Score-Delta Distribution (v2 − v1, pairs eligible under both)

**52 profiles, 8,132 pairs**

| Statistic | Value |
|---|---|
| Changed / unchanged | **460 (5.7%)** / 7,672 |
| Up / down | 428 / 32 |
| Mean / median | +0.133 / 0 |
| Min / max | −4 / +8 |
| p1, p5, p10, p25, p50, p75, p90 | 0 |
| p95 / p99 | +2 / +4 |

**By corpus part** (this is where the direction comes from):

| Part | Pairs | Changed | Mean | Min / max |
|---|---|---|---|---|
| Committed rows | 1,232 | 32 (2.6%) | −0.078 | −4 / 0 |
| Synthetic, one spelling per concept | 3,572 | 176 (4.9%) | +0.139 | 0 / +8 |
| Synthetic, a concept spelled twice | 3,328 | 252 (7.6%) | +0.204 | 0 / +7 |

**Supplementary golden profile (several spellings per concept):** 145 pairs, 47 changed (11 up, 36 down), mean −1.034, min −14, max +3.

**Reading.**
- Two effects pull in opposite directions.
- **v2 lower.** v1 over-counts:
  - a longer phrase's substring (`react` inside "React Native");
  - several spellings of one concept that the profile carries separately ("React, React.js and ReactJS": 35 → 21).
- **v2 higher.** v2 recognises registry aliases the résumé never wrote: a job saying "Postgres", "psql", "python3" or "k8s" matches PostgreSQL, Python or Kubernetes. The newly matched concepts were mostly PostgreSQL (199), Python (78), Tailwind CSS (40), AWS (36) and Kubernetes (32).
- **Limitation.** None of the 52 replayed profiles spells any concept twice (the fixtures are thin, §A.2). So v1's double-count shows up here only through the supplementary profile and the committed rows.
- Real profiles do carry duplicates: design §A.3's inference, and the audited development résumé had 57 terms / 46 concepts. So real downward deltas are likely larger than the 52-profile mean suggests.

## N. Ranking Comparison

**Eligible jobs per profile:** min 117, median 174, max 174. All 52 profiles have ≥ 25.

| Statistic | Min | p10 | Median | Mean | Max |
|---|---|---|---|---|---|
| Top-25 overlap (eligible, `rank_rows` order) | 22/25 (0.88) | 0.92 | 24/25 (0.96) | 0.954 | 25/25 |
| Top-25 overlap (`finalize` output) | 0.88 | — | 0.96 | 0.957 | — |
| Spearman ρ over eligible jobs (average-rank ties) | 0.880 | 0.939 | 0.980 | 0.969 | 1.000 |

- **25/25 overlap:** 16 profiles.
- **Identical top-10 order:** 17 profiles.
- **Lowest:** mateo (all layouts 22/25, ρ 0.947) and esi-plain (23/25, ρ 0.880).

## O. Matched-Skill Examples (synthetic)

**The semantic change.**
- v1 lists every raw profile term that matched, in the profile's own spelling, and each adds its weight.
- v2 collapses aliases into one concept, listed by its display name, counted once. A concept wholly inside a longer matched concept does not count.

**How often the text changes.** In 2,472 of 8,132 pairs (30.4%). In 2,012 of those the score is identical and only the text differs: casing and display names.

| Job (synthetic) | v1 | v2 |
|---|---|---|
| "We build with React, React.js and ReactJS on a Node.js and MongoDB stack…" (golden profile) | 35 `react, react.js, reactjs, node.js, node, mongodb, typescript, rest api` | 21 `React, Node.js, MongoDB, TypeScript, REST APIs` |
| "React Native developer shipping mobile apps with TypeScript." | 4 `react, typescript` | 2 `TypeScript`: the `react` hit was inside "React Native", which this profile does not carry |
| "…GraphQL (graphql), python3 (python), k8s, django, celery, Redis (redis), post gres (postgres)…" | 12 `celery, django, graphql, python, redis` | 16 `celery, django, GraphQL, Kubernetes, PostgreSQL, Python, Redis` |
| "…django, python, redis…" | `django, python, redis` | `django, Python, Redis` (same score) |

## P. Recommendation

**Eligibility condition: met.** Kept/dropped is identical for every pair and every `finalize` set, across 53 synthetic profiles and 9,699 pairs.

- **Magnitude:**
  - 5.7% of eligible pairs change score, typically by ±2–4;
  - top-25 overlap is a median of 24/25 (worst 22/25);
  - rank correlation is a median of 0.98 (worst 0.88).
- **Effects:**
  - removes v1's substring and duplicate-spelling over-counting;
  - adds alias recognition;
  - changes the `matched_skills` text (display names) in about 30% of pairs.
- **Recommendation: activation is supportable**, in the rollout order below, **after the reviewer signs off on this comparison**.
- This is a public scoring change and the decision is the reviewer's.
- Not activated here.

## Q. Worker-First Rollout Plan (documented only; nothing done)

1. **Worker compatibility deploy (Oracle).**
   - Deploy this worker.
   - Old Render sends no `engine`, so the worker behaves exactly as today: v1, legacy argv, legacy response.
   - Render stays as it is, or deploys this code with `SWEEP_SEND_PROFILE_ENGINE` unset (also no change).
2. **Verify the worker.**
   - A legacy free plan and free run smoke test: no `profile_engine` in the plan; the run's `profile.py` is stamped v1.
   - Then an explicit-engine check through the non-paid path: `POST /v1/plans` with `"engine": "v2"` and `free_only: true`. Its answer must carry `"profile_engine": "v2"`.
3. **Reviewer signs off this v1 → v2 comparison.**
4. **Render activation.** Deploy Render with this code and `SWEEP_SEND_PROFILE_ENGINE=1`. From then on, public one-résumé scoring is v2 for résumés derived under v2.
5. **Verify.**
   - Render `/healthz` shows `"engine_propagation": true` and `"derivation_engine": "v2"`.
   - A new derivation's plan (free is enough, no paid provider call) succeeds. It can only succeed when the worker's `profile_engine` equals the recorded `derived_engine`; otherwise the screen shows plan unavailable.
   - The run's `profile.py` on the worker is stamped v2.

## R. Rollback Plan

**Primary.**
- Unset `SWEEP_SEND_PROFILE_ENGINE` on Render and restart. It is read per call, but Render env changes restart the service anyway.
- Render then sends no engine, and the worker renders its default, v1, exactly as before 0b-B.
- No worker change and no code revert.

**Secondary.**
- Revert the worker.
- **Turn the Render switch off first.** An old worker refuses `engine` with 400, so with the switch on, every plan and run would fail loudly. Nothing would run silently, but nobody could search.

**No persisted migration.**
- Engines live in rendered profile files and in Render memory only.

**What happens to v2-period artifacts.**
- Runs already created keep their v2-stamped `profile.py` and their v2-scored results until the worker's 48 h TTL removes them.
- A run in flight at rollback time finishes v2, because its file is already written.
- Only new plans and runs revert.
- Render sessions keep `derived_engine` in memory, but with the switch off it is not sent.
- The `/profile.py` a visitor downloads was always rendered on Render with Render's own v2, before and after 0b.

## S. Remaining Phase-4 Prerequisites

1. **Phase 0b live and verified** (§Q steps 1–5) before any multi-track run.
2. **Worker `tracks` contract** (`tracks` xor `profile`), `render_sweep` with required per-track engines, `plan_hash` carried as `SWEEP_EXPECTED_PLAN_HASH` (and let through `default_spawn`'s environment scrub), exit 4 → `plan_changed`, `track_ids` in `status.json`.
3. **Schema-2 engine attestation.**
   - The dry run emits no `profile_engine` for schema 2.
   - Phase 4 should rely on `plan_hash`, which already binds `[track_id, engine]`, or add a per-track attestation. Either way, Render must verify it.
4. **R-9 (same class as D-1, not addressed here).** `render()` still reads `SWEEP_CANDIDATE_TITLE_GATE` in the *rendering* process: on the worker, for public runs. That flag's worker value has not been established.
5. **Derivation quality** (the real-résumé validation) is a separate workstream, untouched here.
