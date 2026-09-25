# Multi-Track Phase 0a — Handoff

Phase 0a freezes how Sweep behaves **today** for one profile, under both
engine stamps (v1 and v2). The tests in this phase are the reference for
Phase 1 (TrackContext / `job_facts` / `evaluate`). Phase 1 must leave every
golden here unchanged. The one exception is the planned stale-flag change
described in §I.

**Test-only phase.** No production file changed. Nothing was committed,
pushed or deployed. No provider or model was called.

---

## A. Git State Before

| | |
|---|---|
| Branch | `main` |
| HEAD | `ff06a48` (`feat(feedback): add in-app beta feedback form`) |
| Tracking | `main...origin/main`, in sync (local tracking ref also at `ff06a48`) |
| Tracked changes | none |
| Untracked | `MULTI_TRACK_ARCHITECTURE_AUDIT.md`, `MULTI_TRACK_ARCHITECTURE_DESIGN.md` (both left untouched) |

## B. Tests / Fixtures Added

All new, all under `sweep/tests/`, plus this handoff file.

| File | Role |
|---|---|
| `test_one_track_goldens.py` | 35 tests in 9 classes (below) |
| `one_track_golden_driver.py` | Test-only driver. Always runs in a **fresh interpreter** (`config` and `scraper` bind the profile at import). Modes `render` and `run`. Blocks sockets and reads no `.env`. |
| `fixtures/one_track_goldens/derived_personas.json` | Synthetic inputs: v2 local-engine derivations of the bench personas ada (5 years), chen (11 years) and bhaskar (0 years), replayed offline from committed captures `bench/results/qwen3_8b.json` and `dates-qwen3_8b.json` |
| `fixtures/one_track_goldens/scoring_profile.json` | Hand-written synthetic MERN derivation (aliases, react native overlap, penalty, domain halves) |
| `fixtures/one_track_goldens/scoring_rows.json` | 18 synthetic job rows on `example.test`. Each row's `_case` says what it exercises. |
| `fixtures/one_track_goldens/expected/render.json` | v2 rendered source per case, as lines |
| `fixtures/one_track_goldens/expected/plans.json` | Plan golden per case (compacted, see §C) |
| `fixtures/one_track_goldens/expected/scoring_v1.json`, `scoring_v2.json` | Full scoring, finalize, mutation and export golden per stamp |
| `fixtures/one_track_goldens/expected/scoring_scopes.json` | Compact scoring projection under the remote and global scopes |
| `fixtures/one_track_goldens/expected/authorization.json` | Cost, spend cap, coverage and `AccountPool.authorize` |

**Test classes (35 tests)**

| Class | Tests |
|---|---|
| RenderGoldens | 5 |
| PlanGoldens | 7 |
| EngineStampMatrix | 2 |
| ScoringGoldens | 6 |
| ScopeGoldens | 2 |
| ScoreJobMutationContract | 1 |
| DedupeContract | 6 |
| ExportGoldens | 2 |
| PaidAuthorizationGoldens | 4 |

**Cases**

| Case | Persona | Configure form |
|---|---|---|
| `golden_ada_india` | ada | India |
| `golden_ada_remote` | ada | Remote |
| `golden_ada_global_custom` | ada | Global; UK + Bengaluru; 25 results; 7 days; $30k floor; avoid php, salesforce |
| `golden_ada_india_naukri` | ada | India, plus Naukri |
| `golden_ada_free_only` | ada | Free only |
| `golden_chen_india` | chen | India |
| `golden_bhaskar_india` | bhaskar | India |
| `golden_scoring` | scoring | India; $20k floor; avoid php |
| `golden_scoring_remote` | scoring | Remote (scope projection only) |
| `golden_scoring_global` | scoring | Global custom (scope projection only) |

Every form goes through the production validator, `logic._configure_overrides` + `app._prefs`. The prefs are therefore exactly what the app would hand to `make_profile.render()`.

**Fixture safety.** The fixtures contain:
- no résumé text;
- no real candidate data (persona names are the fictional ones in `bench/people.py`);
- no emails except the reserved `careers@example.com`;
- no phone numbers, tokens, account ids, worker payloads or status files;
- no absolute paths, timestamps, pids or random ids.

Dates are fixed (`2026-01-01`) or relative ("1 day ago"). This was checked by a grep over the fixture tree.

**Harness rules**
- There is no update mode. An expected value changes only when someone edits the committed JSON on purpose.
- The minimal subprocess environment is `PATH`, `HOME`, `LANG`, and the engine variable only where a test sets it.
- Each run gets its own temp workdir, removed in `tearDownModule`.
- The goldens were generated twice. The two collections were byte-identical (random hash seed in every subprocess).

## C. What Each Golden Freezes

**Render**
- `make_profile.render()` output, byte for byte, for 8 cases.
- v1 differs from v2 **only** in the stamp line, `PROFILE_SCHEMA = {"version": 1, "engine": 'vX'}`.
- "Variable absent" (the worker's real state) renders exactly like explicit v1.
- The top-level names each case emits: `LOCATION_HINTS` is present for every case except remote.
- `role_keywords` appear in derivation order.

**Plan**
- The dry-run JSON exactly as `scraper.py --dry-run --json` prints it: `profile`, `sites`, `max_results`, `charge_ceiling_usd`, `free_sources`, `public_paid`.
- Every unit's built provider input, `build_input(site, effective_search(site, unit))`.
- `combo_keys` (`date|site|keywords|location|company`) and `profile_changed`.
- Order is part of the contract: site-major in `config.SITES` order (linkedin, indeed, naukri), keyword-major, location inner.
- There are no future fields (`request_key`, `ledger`, `tracks`, `plan_hash`, `plan_version`).
- The LinkedIn `f_E` experience band: ada 4, chen 5, bhaskar 2.
- The free-only plan contains nothing paid.
- The stamp changes no plan.
- Compaction: four small cases store every unit and combo key. The four India cases store:
  - the full dry-run site lists;
  - per site: the count, the first and last unit, and a SHA-256 over all units;
  - the same four things for the combo keys.

  This is still an exact check.

**Engine-stamp matrix**
- Render environment → file stamp → `config.PROFILE_ENGINE`, `skill_concepts.effective()`, `enabled()` and `evidence_enabled()`, with no engine variable at run time.
- Run-time precedence: a v1 file with `SWEEP_PROFILE_ENGINE_VERSION=v2` still scores v1, and a v2 file with the variable set to v1 still scores v2.
- An **unstamped** file binds v1 even with the variable set to v2. This is by design: `make_profile.profile_schema` says "absence of a stamp means v1 output".
- This is the local engine contract, **not** the Render-v2 / worker-v1 deployment state, which Phase 0b addresses.

**Scoring and finalize** (`golden_scoring`, 18 rows, both stamps)
- Per row, `score_job` on its own copy: whether the row is kept, which keys it adds, which it changes (none), and the added values.
- `score_and_filter` stage counts, through its own `stage` hook: 18 → 15 → 14 → 13 → 12 → 11.
- Stats: stale 1, low_salary 1, wrong_arrangement 1, off_geography 1, kept 10.
- `finalize` rows, including key order, and `LAST_STATS`.
- Memoised `finalize` is identical, and its `_scored` flags are recorded.

**Scopes** (the same profile and rows under remote and global)
- Stage counts, stats, eligible URLs, final `[url, score, remote_scope]` rows and export sections.
- Remote: 13 rows are "unreachable" (onsite) and the worldwide-remote row survives.
- Global ("onsite or hybrid, worldwide"): the remote row is dropped as wrong-arrangement. Berlin is kept, because finalize filters geography only for India.

**`score_job` mutation contract**
- A kept row gains exactly `eor, hr_email, hr_phone, is_fullstack, matched_skills, remote?, remote_regions, remote_scope, score, timezones, tz_gap, visa, years_required` and changes nothing else.
- A dropped row gains and changes nothing.
- Repeating `score_job` gives the same result.
- `_score_once` sets `row["_scored"]`.
- **Hazard frozen as current behaviour:** a stale `_scored = False` flag is honoured without re-scoring, even for a row `score_job` would keep (design §K).

**Dedupe** (pure, in-process)
- `job_key` precedence: requisition number, then (company without suffix, sorted title tokens), then canonical URL.
- Location is not part of a job's identity.
- The URL fallback strips tracking parameters and `www`.
- Rows with no identity are always kept.
- The highest score wins; on a tie, arrival order wins.
- **The loser's provenance is lost.** In the scoring golden, the paid LinkedIn copy (row 10) loses to the free Greenhouse copy (row 11), and the survivor's `search_query` is `""`.

**Exports**
- Engine CSV: BOM and lines, with the header equal to `OUTPUT_COLUMNS` (26 columns).
- Engine JSON equals the `finalize` output.
- Download exports, built from the CSV read back as the results screen reads it (`shortlist`, `bucket_rows`, `rows_for_export`):
  - CSV: BOM and lines, header equal to `exports.HEADERS` (16 columns);
  - JSON records, with key order equal to `HEADERS`;
  - HTML lines;
  - XLSX structure: sheets, cell values, hyperlinks, freeze panes, auto-filter. The binary is not compared.

**Paid authorization** (offline; fixed account readings, no provider)
- `plan.cost` and `spend_cap_for` for India and India + Naukri.
- `plan.coverage`, and `AccountPool.plan` + `authorize` over seven cases.
- Render coverage and engine authorization agree on the placeable count.
- The runnable units are always a prefix of the plan, and that prefix is LinkedIn-first.

## D. v1 vs v2 Baseline

Both columns are today's behaviour. **No test asserts that they should agree**; the tests only confirm which fields really are engine-independent.

**Identical under both stamps**
- The rendered source, apart from the stamp line.
- Every plan, provider input and combo key.
- For every row: kept/dropped, `added_keys`, `is_fullstack`, `years_required`, `remote_scope`, `remote?`, `tz_gap`, `hr_email`, `visa`, `eor`, `remote_regions`.
- Every stage count and filter stat, in all three scopes.

**What differs**
- **Only `score` and `matched_skills`.**
- `matched_skills`:
  - v1 lists the raw alias terms, e.g. `react, react.js, reactjs, node.js, node, mongodb, typescript, rest api`;
  - v2 lists each concept's display name once, e.g. `React, Node.js, MongoDB, TypeScript, REST APIs`.

| Row (case) | v1 | v2 | Why |
|---|---|---|---|
| 01 three React spellings + stack | 35 | 21 | v1 counts every alias; v2 counts each concept once |
| 02 React Native | 12 | 7 | v2's overlap policy doesn't count `react` inside `react native` |
| 03 MERN | 20 | 16 | `node` and `node.js` are one concept in v2 |
| 04 salesforce penalty | 9 | 5 | |
| 05 php avoid | −4 | −8 | |
| 06 senior | 4 | 4 | |
| 08 manager | 5 | 5 | single concept |
| 10 / 11 duplicate pair | 11 / 23 | 7 / 19 | |
| 12, 14, 15 (preference-dropped) | 17 | 13 | |
| 13 Berlin | 5 | 5 | |
| 16 sparse | 0 | 0 | |
| 18 UTC-8 overlap | 4 | 0 | the timezone penalty removes v2's smaller base |

**Final order**

| Scope | v1 | v2 | Difference |
|---|---|---|---|
| India | 01, 11, 03, 02, 04, 08, 06, **18, 16**, 05 | 01, 11, 03, 02, 04, 08, 06, **16, 18**, 05 | 16 and 18 swap |
| Global | 01, 11, 03, 02, **04**, 08, 13, 06, 18, 16, 05 | 01, 11, 03, 02, 08, 13, 06, **04**, 16, 18, 05 | 04 drops to 1 under v2, because the avoid list raises salesforce to −12 |

Current-behaviour notes a reviewer may find surprising. All are frozen as-is, not endorsed:
- The title "Engineering Manager, React" is **not** hard-dropped for a generated profile, because the rendered `exclude_levels` replaces config's longer list.
- Dedupe keeps one dict and merges nothing.
- An unstamped profile is v1 whatever the environment says.

## E. Existing Test Results

All runs used a minimal environment (`env -i PATH HOME LANG`), with output to scratch logs.

| Suite | Command | Result |
|---|---|---|
| New goldens | `.venv/bin/python -m unittest sweep.tests.test_one_track_goldens` | **35 / 35 OK** (≈5 s) |
| Overlapping modules | `… -m unittest sweep.tests.test_plan sweep.tests.test_runs sweep.tests.test_paid_multi_account sweep.tests.test_paid_contract sweep.tests.test_paid_plan_audit sweep.tests.test_search_v2_d sweep.tests.test_app` | **779 OK** |
| Full Sweep | `.venv/bin/python -m unittest discover -s sweep/tests -t .` | **1,697 OK** (1,662 existing + 35 new) |
| Worker / deploy | `.venv/bin/python -m unittest deploy.test_sweep_worker deploy.test_modal_benchmark` | **42 OK** |
| Bench | `.venv/bin/python -m unittest bench.test_backends bench.test_answer_key` | **43 OK** |
| auto-apply | `cd auto-apply && ../.venv/bin/python -m unittest discover -s tests -t .` | **1,102 run, 2 errors** (known, §F) |

Existing tests total 1,662 + 42 + 43 + 1,102 = **2,849**, the figure the design's Phase 1 invariant uses. None changed.

## F. Known Existing Failures

The two known auto-apply errors remain, unchanged and not touched:

| Test | Error |
|---|---|
| `tests.test_inference.TestAuthenticationFailure.test_healthz_needs_no_token` | `HTTPError 503` |
| `tests.test_inference.TestConcurrentRequests.test_healthz_answers_while_a_generation_holds_the_slot` | `HTTPError 503` |

There are no other failures.

## G. Production Files Changed

**NONE.** No application, worker, config, template, environment or deploy file was modified. `git diff --stat` is empty.

## H. Git Diff Summary

```
?? MULTI_TRACK_ARCHITECTURE_AUDIT.md        (pre-existing, untouched)
?? MULTI_TRACK_ARCHITECTURE_DESIGN.md       (pre-existing, untouched)
?? MULTI_TRACK_PHASE_0A_HANDOFF.md          (this file)
?? sweep/tests/fixtures/                    (3 inputs + 6 expected JSON)
?? sweep/tests/one_track_golden_driver.py
?? sweep/tests/test_one_track_goldens.py
```

No tracked file changed. Nothing was staged, committed, pushed or deployed.

## I. Phase 1 Readiness

**Ready.** Per the design's gates, Phase 1 may begin after 0a. The goldens give Phase 1 a byte-level check on render, plan, per-row scoring, stage counts, `LAST_STATS`, memoised finalize, the mutation contract, dedupe and every export shape, under both stamps.

**One golden must change deliberately in Phase 1.** `score_once_honours_a_stale_false_flag` freezes the §K hazard. When Phase 1 introduces the per-context memo, it flips that single assertion, in the same change and with the reason.

Gaps. None blocks Phase 1 as the design scopes it, but each is uncovered by goldens:

1. **Visa / EOR / rescue filter branches.** In every golden, `no_visa`, `no_eor`, `rescued` and `unreachable` (outside the remote scope) are 0, and `visa`/`eor` take only their "no signal" values. If Phase 1 moves that stage into `evaluate`, add a golden with those preferences on first.
2. **The fetch-time free-source prefilters.** `is_dev_title` and `location_allowed` are passed to `sources.fetch_free` (`scraper.py:289`). They are frozen only as rendered `ATS_TITLE_HINTS`, `ATS_TITLE_EXCLUDE` and `LOCATION_HINTS`, not as decisions per title or location. Phase 3's union predicate needs its own golden.
3. **Row-level scoring uses one synthetic profile.** The ada, chen and bhaskar derivations are frozen at render and plan, not scored.
4. **Not in goldens** (covered only by the existing suite):
   - the paid execution loop (`paid_phase_c2`, a real `AccountPool.open`);
   - the worker HTTP path;
   - the results-screen template.

   Phase 1 touches none of these.
5. **Out of Phase 1's scope.** The Render-v2 / worker-v1 deployment mismatch is deliberately *not* encoded. It is Phase 0b, and 0b must be live before any multi-track run (design §T gates).
