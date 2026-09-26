# Multi-Track Phase 1 — Handoff

Phase 1 isolates scoring behind an explicit context. It is a refactor, with
one planned behaviour correction.

**Before this phase:** module-level scoring tables, a process-global engine
bind, `score_job` writing onto the row, and one unkeyed `_scored` memo.

**After this phase:**
- a frozen `ScoringContext`;
- `JobFacts` computed from the row alone;
- a pure `evaluate(row, ctx)`;
- a memo keyed by context;
- `score_job(row)` kept as the one-profile compatibility wrapper.

One-profile output is unchanged except the approved stale-memo fix (§F).

Nothing is committed, pushed or deployed. No provider or model was called.
**Waiting for review.**

---

## A. Git State Before

| | |
|---|---|
| Branch | `feat/multi-track-search` |
| HEAD | `cb602ec` (`test(multi-track): freeze one-track behavior`), on `e05b751`, on `main` `ff06a48` |
| Working tree | clean |
| Upstream | `origin/feat/multi-track-search` at `cb602ec`. The branch had been pushed before this phase began; nothing in this phase pushed |

**Unrelated working-tree change, not made in this phase.**
`MULTI_TRACK_ARCHITECTURE_DESIGN.md` line 1819 now reads "A resucard
redesign." instead of "A results-card redesign.".
- The file was modified at 00:29:25, after both commits (00:25) and after this
  phase's clean `git status`.
- This phase only read that file; it wrote nothing to it.
- It looks like an accidental keystroke in an open editor.
- It is left untouched and is **not** part of Phase 1. Revert it with
  `git checkout -- MULTI_TRACK_ARCHITECTURE_DESIGN.md` if it was not meant.

## B. Production Files Changed

| File | Change | Purpose |
|---|---|---|
| `scraper.py` | +231 / −56 | `ScoringContext`, `scoring_context()`, `default_context()`, `JobFacts`, `job_facts()`, `Evaluation`, `evaluate()`, `evaluate_once()` + `EVALS`; `score_job` rewritten as the wrapper; `_score_once` keyed by context (`SCORED_BY`); `score_and_filter` builds one context per memoised pass; imports `functools`, `dataclasses` |
| `enrich.py` | +14 / −12 | `signals(row, home_offset)`: the six signal fields as a new dict, with no writes. `enrich()` becomes `row.update(signals(...))`: the same keys, values and order |

Not touched: plan construction, `build_input`, the paid scheduler, `AccountPool`, free sources, `to_output`/`OUTPUT_COLUMNS`, exports, `config.py`, `make_profile.py`, the worker, templates, `LAST_STATS`, telemetry and environment files.

**Tests, fixtures and docs**

| File | Change |
|---|---|
| `sweep/tests/test_scoring_context.py` | **new**, 19 tests (§G) |
| `sweep/tests/test_one_track_goldens.py` | the one deliberate assertion (§F), plus its comment |
| `sweep/tests/fixtures/one_track_goldens/expected/scoring_v1.json` | the same field, `true` → `false`, edited by hand (1 line) |
| `sweep/tests/fixtures/one_track_goldens/expected/scoring_v2.json` | the same field, `true` → `false`, edited by hand (1 line) |
| `MULTI_TRACK_PHASE_1_HANDOFF.md` | **new**, this file |

## C. New Internal Architecture (`scraper.py`)

**`ScoringContext`** (frozen dataclass)
- Holds everything scoring reads that a profile sets, already compiled:
  - `engine` (`"v1"` or `"v2"`, validated);
  - `skill_terms`, `concepts` and `penalties`;
  - `frontend`, `backend`, `fullstack_title` and `fullstack_bonus`;
  - `hard_drop`;
  - `max_experience_years` and `candidate_experience_months`.
- Exactly the keys `make_profile.render()` writes into `SCORING` and `SETTINGS` that `score_job` reads (checked against a rendered profile), plus the engine.
- `key` is a SHA-256 of the context's contents: engine, terms, weights, pattern sources, concept aliases, bonus and limits.
  - It is lazily cached.
  - It is the memo key.
  - Equal contents give the same key; any difference gives a new one.
  - It contains no name and no identity of the caller.
- Named `ScoringContext` rather than the design's `TrackContext` because it holds scoring only. The track's search intents and title gate are Phase 3, so `TrackContext` can wrap it then.

**`scoring_context(scoring, settings, engine)`**
- Compiles a context from dicts shaped like `SCORING` and `SETTINGS`, the same way import compiles the loaded profile's tables.
- A test proves it produces the same key as the import-time tables.
- This is how Phase 2 will build a track's context.

**`default_context()`**: the loaded profile's context, read from the module tables **at call time**. This is a deliberate deviation from "one context built at load". Existing callers rely on live tables:
- `merge_jobs.py` and `demo()` swap `HARD_DROP_PATTERNS` and `SETTINGS` in place;
- `test_search_prefs` rebinds `PENALTY_PATTERNS`;
- `test_skill_concepts` flips `SWEEP_PROFILE_ENGINE_VERSION` between calls and requires the next call to follow.

A context frozen at import would break all three. The default context's engine is exactly the branch `score_job` has always taken: `"v2"` if `skill_concepts.enabled()`, otherwise `"v1"`. That is the bound profile stamp once one is loaded. Building it costs a few tuples over already-compiled patterns (§H).

**`JobFacts` / `job_facts(row)`** (frozen dataclass; reads the row, never writes it)
- Computed up front:
  - the lower-cased title and text;
  - `blocked` (config blocklist);
  - `years_required` (the experience floor; config `experience_aggregate`);
  - `soft_seniority` (config `soft_drop_terms`).
- `enrichment` is lazy and cached: `enrich.signals()` plus `remote?` and the contacts, in the order `score_job` writes them.
  - It is computed only when a context keeps the row, which is when `score_job` has always computed it.
  - So a dropped row costs no more than before, and nothing is written early.
- It reads a shallow copy of the row taken at creation.
- It is not cached on the row. `evaluate(row, ctx, facts)` accepts one shared facts object, so several contexts can reuse it in Phase 2.

**`Evaluation`** (`namedtuple`, the repo's record convention)
- Fields: `eligible`, `reason`, `score`, `matched_skills`, `is_fullstack`, `years_required`, `exp_verdict`.
- `reason` is `"blocked_company"` or `"excluded"` when the row is not eligible. The scoring fields are then `None`.

**`evaluate(row, ctx, facts=None)`**
- Pure: today's rule body, line for line, with every value a profile sets read from `ctx`. The choice between concept scoring and per-term scoring is `ctx.engine == "v2"`.
- It writes nothing:
  - not the row;
  - not `experience_guard.DROPPED` (the guard verdict is **returned**);
  - not `skill_concepts._BOUND`.

**Memo model**
- `evaluate_once(row, ctx)`: `row["_evals"][ctx.key] = Evaluation`. This is the new evaluator's per-context memo, and the only thing it writes.
- `_score_once(row, ctx=None)`: the one-profile memo for `finalize(memo=True)`.
  - `_scored` stays the verdict (the goldens freeze its name and values).
  - The new `_scored_by` records the `ctx.key` that verdict was reached under.
  - The verdict is reused only for the same key.
- Why two memos rather than one:
  - Existing tests wrap `scraper.score_job` in a one-argument spy and count its calls under `memo=True` (`test_paid_concurrency.py:412`, `:1277`; `bench/search_v2_replay.py`).
  - So the one-profile memo must keep calling `score_job(row)` by name. That means it can only record `score_job`'s verdict, not an `Evaluation`.
  - Keeping the two apart means Phase 2's `evaluate_once` can never receive a boolean.
- `score_and_filter(..., memo=True)` builds one `default_context()` per pass, so the key is hashed once per pass, not once per row.

**`score_job(row)`**: the compatibility wrapper, with the same signature and return value as before.
1. `job_facts(row)`.
2. `evaluate(row, default_context(), facts)`.
3. `experience_guard.record(...)` when the guard returned a verdict. This is today's append to `DROPPED`, at the same point in the same order.
4. A dropped row returns `None` untouched.
5. A kept row gets today's fields in today's order: `score, matched_skills, is_fullstack, years_required, remote_scope, remote_regions, visa, eor, timezones, tz_gap, remote?, hr_email, hr_phone`.

Every direct caller stays on it unchanged: `rescore_from_apify` (via `finalize`), the shadow engine (`score_and_filter`), `merge_jobs.py` (`title_excluded`, unchanged), `optum_grade_scan.py`, the bench scripts and the tests.

## D. Global Reads Removed

Every global read `score_job` made, and where it lives now:

| # | Former read in `score_job` | Now |
|---|---|---|
| 1 | `SKILL_PATTERNS` | `ctx.skill_terms` |
| 2 | `SKILL_CONCEPTS` | `ctx.concepts` |
| 3 | `skill_concepts.enabled()` (engine bind) | `ctx.engine` |
| 4 | `PENALTY_PATTERNS` | `ctx.penalties` |
| 5 | `FRONTEND_PATTERNS` | `ctx.frontend` |
| 6 | `BACKEND_PATTERNS` | `ctx.backend` |
| 7 | `FULLSTACK_TITLE_PATTERNS` | `ctx.fullstack_title` |
| 8 | `SCORING["fullstack_bonus"]` | `ctx.fullstack_bonus` |
| 9 | `HARD_DROP_PATTERNS` (via `title_excluded`) | `ctx.hard_drop` |
| 10 | `SETTINGS["max_experience_years"]` | `ctx.max_experience_years` |
| 11 | `SETTINGS.get("candidate_experience_months")` | `ctx.candidate_experience_months` |
| 12 | `experience_guard.record` → `DROPPED` (side effect) | returned as `exp_verdict`; the wrapper records it |

**Still global, and why.** `make_profile.render()` never writes these values. That was verified against a rendered profile's `SCORING` and `SETTINGS` keys. So they have one value per Sweep, as design §D.4 says.

| Read | Where | Kind |
|---|---|---|
| `COMPANY_BLOCKLIST` (`blocked_company`) | `job_facts` | config `company_blocklist` |
| `SOFT_DROP_PATTERNS` | `job_facts` | config `soft_drop_terms` |
| `SETTINGS["experience_aggregate"]` (`_required_experience_floor`) | `job_facts` | config |
| `SETTINGS["home_utc_offset"]` | `JobFacts.enrichment` | config |
| `enrich.TZ_FREE_HOURS`, `enrich.REMOTE_SCOPES` | `evaluate`, `enrichment` | module constants |
| `SETTINGS["drop_excluded"]` | `evaluate` | config |
| `SCORING["soft_penalty"]`, `["experience_gap_penalty"]`, `["drop_penalty"]`, `["timezone_gap_penalty"]` | `evaluate` | config |
| `experience_guard.enabled()` | `evaluate` | feature flag, read per call as before |

The preference filters in `score_and_filter` did not move: recency, pay, reachability, **visa/EOR** (not covered by any golden), arrangement and geography. Phase 1 did not need to move them.

## E. Engine Independence

From `EngineIndependence.test_v1_and_v2_side_by_side_without_rebinding`, in one process:
- A v1 context and a v2 context have identical tables. They score one alias-heavy job, "React, React.js and ReactJS with Node.js":

| Context | Score | Matched skills |
|---|---|---|
| v1 | 19 | `react, react.js, reactjs, node.js` |
| v2 | 9 | `React, Node.js` |

- Interleaving v1 → v2 → v1 gives the same answers.
- This holds with the process pinned to `v1`, and again pinned to `v2`. The global engine is never consulted.
- `skill_concepts.bind` was patched to raise, and it was never called.
- `engine_version()` stays at the pinned value, and `_BOUND` stays `None`.

From `ProfileContext`, in a fresh interpreter each:
- A real profile rendered by `make_profile` under each stamp yields `default_context().engine` equal to its stamp.
- Its weights, penalties, hard-drop patterns and experience limits equal the overlaid config.
- `scoring_context(config.SCORING, config.SETTINGS, engine)` reproduces its key.

`ContextShape` shows that the default engine follows the bound stamp, and that a bound stamp beats a run-time variable.

## F. Compatibility Results

**Phase 0a firewall** (`sweep.tests.test_one_track_goldens`): **35/35 OK.**

Identical to Phase 0a, under both v1 and v2 stamps:

| Area | What stayed identical |
|---|---|
| Render | every rendered source, byte for byte (`render.json` unchanged) |
| Plan | every plan, provider input and combo key (`plans.json` unchanged) |
| Engine-stamp matrix | including run-time precedence and unstamped profiles |
| Per-row `score_job` | kept/dropped, added keys, changed keys and every added value, for all 18 rows |
| Filters | `score_and_filter` stage counts and stats |
| `finalize` | rows (with key order), `LAST_STATS`, memoised-finalize identity, `memo_flags` |
| Scopes | remote and global projections (`scoring_scopes.json` unchanged) |
| Engine CSV/JSON | unchanged |
| Downloads | CSV, JSON, HTML and XLSX unchanged |
| Paid authorization | cost, spend cap, coverage and `AccountPool.authorize` (`authorization.json` unchanged) |
| Mutation contract | apart from the one line below |

**The one deliberate change: the stale `_scored = False` memo.**
- Before (frozen as a hazard in Phase 0a): a row carrying `_scored = False` was returned as dropped by `_score_once` without being scored, even when `score_job` keeps it.
- After:
  - `_score_once` trusts `_scored` only together with a matching `_scored_by` (the key of the current context).
  - A stale or foreign verdict is re-evaluated, so that row is kept, with `_scored = True`.
  - A verdict reached under the same context is still reused: the memo still saves the call (`KeyedMemo`).
- Changed expectations:
  - `score_once_honours_a_stale_false_flag` goes from `true` to `false`, in `scoring_v1.json` and `scoring_v2.json` (one line each, edited by hand, no regeneration).
  - `assertTrue` becomes `assertFalse` at `test_one_track_goldens.py:656`.
- Unchanged beside it: `stale_flag_row_would_be_kept_by_score_job` (true), `score_once_flag` (True) and `score_once_flag_key` (`"_scored"`).
- No other expected value changed. `git diff --stat` on `expected/` shows 2 files, 2 lines.

## G. Tests

All runs used a minimal environment (`env -i PATH HOME LANG`), with output in scratch logs.

| Run | Command | Result |
|---|---|---|
| New Phase 1 tests | `.venv/bin/python -m unittest sweep.tests.test_scoring_context` | **19/19 OK** |
| Phase 0a goldens | `.venv/bin/python -m unittest sweep.tests.test_one_track_goldens` | **35/35 OK** (one deliberate change, §F) |
| Overlapping (Sweep) | `… sweep.tests.test_paid_concurrency sweep.tests.test_search_prefs sweep.tests.test_search_v2_shadow_eval sweep.tests.test_search_v2_telemetry sweep.tests.test_results_ready_early` | **287 OK** |
| Overlapping (auto-apply) | `cd auto-apply && … tests.test_skill_concepts tests.test_engine_contract tests.test_experience_guard tests.test_generated_profile_safety` | **176 OK** |
| Self-checks | `scraper.py --demo`, `enrich.py`, `merge_jobs.py --demo` | all `demo ok` |
| Full Sweep | `.venv/bin/python -m unittest discover -s sweep/tests -t .` | **1,716 OK** (1,697 + 19 new) |
| Worker/deploy | `.venv/bin/python -m unittest deploy.test_sweep_worker deploy.test_modal_benchmark` | **42 OK** |
| Bench | `.venv/bin/python -m unittest bench.test_backends bench.test_answer_key` | **43 OK** |
| auto-apply | `cd auto-apply && ../.venv/bin/python -m unittest discover -s tests -t .` | **1,102 run, 2 errors**: the known `test_inference` `/healthz` `HTTPError 503` pair, unchanged and not touched |

**New tests** (`test_scoring_context.py`)

| Class | Tests | What it pins |
|---|---|---|
| ContextShape | 4 | frozen and validated; the default context is the live tables; the default engine follows the bound stamp; the builder equals import-time compilation |
| ContextIndependence | 5 | one untouched job under two contexts gives different scores and skills; A-then-B equals B-then-A; re-evaluating A returns A's memo; B never receives A's entry; one context dropping a job doesn't drop it for the other; key equality and splitting |
| EngineIndependence | 1 | §E |
| PureEvaluation | 3 | `evaluate` and `job_facts` write nothing, including key order; `evaluate_once` writes only `_evals`; the guard verdict is returned, never recorded, while the wrapper still records it |
| CompatibilityWrapper | 2 | kept rows get today's fields in today's order, matching `evaluate`; dropped rows are untouched |
| KeyedMemo | 3 | a stale False no longer suppresses scoring; reuse happens only under the same context, and the memo still saves the call; repeated memo passes equal the plain pass |
| ProfileContext | 1 | real v1 and v2 profiles in fresh interpreters |

## H. Diff Risk Review

| Risk | Finding |
|---|---|
| Unintended plan change | None. No plan code was touched, and the plan goldens are identical under both stamps. |
| Export change | None. The export and engine-output goldens are identical, and `OUTPUT_COLUMNS` is untouched. The new private key `_scored_by` is dropped by `to_output`'s projection exactly as `_scored` is (checked directly; `test_the_memo_key_reaches_no_output` passes). |
| Scoring drift | None. Per-row scores and matched skills for 18 rows × 2 stamps × 3 scopes are identical. The rule body was moved, not rewritten: the same order, arithmetic and rounding. |
| Changed mutation order | None. The golden `added_keys` are identical, and `CompatibilityWrapper` asserts the exact key order. `enrich.enrich` uses `dict.update` from a dict built in its old write order. |
| Global state leakage | `evaluate` writes no module state. The guard's `DROPPED` append moved to the wrapper, at the same point and in the same order. `bind` is never called while evaluating (asserted). Sweep-level constants are still read globally, by design (§D). |
| Cross-context cache leakage | The memo is keyed by `ctx.key` (a content digest). `_score_once` requires `_scored_by` to match. Both are tested. |
| Performance | Same scratch benchmark, 720 synthetic JD-length rows, default config: `score_job` 2.34 → 2.30 ms per row (v1) and 3.61 → 3.56 ms (v2); `finalize` unchanged within noise. The memoised pass that hits the memo went 1.2 → 1.4 ms per 720 rows (one context build and one hash per pass). A dropped row still skips enrichment (lazy `JobFacts.enrichment`). |

**Residual risks, stated plainly**
1. `ctx.key` covers what the context carries, not the Sweep-level constants. If those constants changed within one run, the memo would not notice. The old `_scored` had the same property, and nothing changes them within a run.
2. `default_context()` reads live tables. That is required for compatibility. Phase 2 contexts must be built with `scoring_context()` from explicit data, never from `default_context()`.
3. The one-profile memo is single-slot: one context's fields are written onto a row. Multi-context code must use `evaluate_once`, not `_score_once`.
4. `JobFacts.acquired` is a shallow copy. `enrich` and the contacts read only top-level strings.

## I. Phase 2 Readiness

What exists now: any number of contexts can be built (`scoring_context`) and evaluated side by side in one process. Each context keeps its own engine and its own memo, and rows stay untouched.

Still missing before two **runtime** track contexts can exist:

1. **No second set of tables to build from.** `config` loads one schema-1 profile and overlays it onto globals in place. There is no `TRACKS` (schema 2) loading, and no snapshot of config defaults before the overlay to build track contexts on (design §D.3–D.4).
2. **`finalize` / `score_and_filter` are one-context.**
   - They call `score_job` (the default context) and write one score onto the row.
   - Missing pieces: per-track eligibility, relevance (paid `_tracks` provenance, the per-track free title gate), `job_key` clusters, per-track best copy, best track, and appended columns (design §J–§L, §Q).
3. **The engine per track is not yet data.** Only the loaded profile's stamp or bind supplies an engine. Phase 0b (B2) is still needed before any multi-track run (design §T gate).
4. **Bookkeeping is one funnel.**
   - Guard records carry no track id.
   - `LAST_STATS` and telemetry are unchanged.
5. **No per-copy facts cache.** Phase 2 should compute `job_facts` once per copy and pass it to each context's `evaluate`.

Phase 2 has not been started.
