# V2-B3 — production-safe evaluation of the eight Greenhouse shadow boards

Date: 2026-09-23. Baseline: `b82b4f8` (V2-B2), which is what production runs.
Scope: **evaluation of the existing shadow tranche. Not source promotion.**
Default **OFF**. Not deployed. No deployment file changed.

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a dated
probe), **VERIFIED** (directly observed code or provider documentation),
**INFERRED** (a model with stated assumptions), **UNKNOWN** (evidence
unavailable, which is not the same as zero).

Nothing else moved. The registry is still 134 active records (129 ATS boards +
5 feeds); no board was promoted; PubMatic stays out; no other discovered board,
Workable, Gem or Recruitee was added. Dedupe, `job_key`, native-id production
semantics, ranking, weights, candidate gates, Search Preferences, recency,
location, arrangement and salary rules, SmartRecruiters pagination, Lever
concurrency, paid execution and the shadow fetch's own HTTP semantics are
unchanged. No caching, Redis, database or background worker was added.
`SWEEP_EXPERIENCE_MISMATCH_GUARD` remains off.

## 0. Headline findings

1. **MEASURED — the question V2-A pushed offline can be answered in production
   safely, and its answer is exact.** The live evaluator's per-board eligible,
   positive, new-final and top-20 counts equal V2-A's offline method —
   `finalize(baseline + shadow)` — on **every board, every funnel stage and
   every count, in all five frozen cohorts** (§8,
   [evidence](search-v2-evidence/shadow-b3-frozen-equivalence.json)).
2. **MEASURED — it costs ~1.3 s of CPU per sweep, about 9% of one production
   finalize pass**, and production already runs two of those at the Free tail
   (§7). It is bounded at 1,000 rows. Only counts are written — no row-level
   artifact exists (§5).
3. **MEASURED — the user's output is byte-identical with shadow on**, in the
   offline end-to-end tests through `scraper.main()` and in two sets of four
   real, worker-shaped free sweeps over public boards (§4, §12,
   [evidence](search-v2-evidence/shadow-b3-free-sweep-parity.json)).
4. **MEASURED — the shadow fetch has a real latency tail.** Across five live
   passes today, 3 of 40 board fetches hit a 25 s socket timeout and succeeded
   on retry, adding **26.7–29.1 s** to that sweep. This laptop's network is the
   likely cause — production telemetry recorded 0 Greenhouse retries in 162
   fetches — but the production rate for these eight boards is **UNKNOWN**, and
   the experiment carries a stop rule for it (§7, §16).
5. **MEASURED — native identity is sound and job_key is not stable across time.**
   All 888 live rows carry a distinct native id that appears in the posting URL.
   857 of 876 ids from the 2026-09-21 snapshot were still live two days later,
   and **19 of those 857 postings had been retitled** under the same id —
   Vercel renamed seven "Member of the Technical Staff" roles to "Software
   Engineer". Current `job_key` calls each of those a different job (§14).
6. **CORRECTION to V2-A §10.** "Every one of the eight earns at least one
   positive-score row somewhere" no longer holds: **Jumio's single remote
   positive row has aged out of the 14-day window**, in today's frozen replay
   and in live data alike. Seven of eight contribute; Jumio contributes nothing
   today. That is exactly why one snapshot cannot decide a board (§20).
7. **A PAID INCIDENT OCCURRED DURING VERIFICATION — not in the shipped code.**
   An unguarded check ran 23 paid Apify searches before it was stopped. The
   facts, the estimated charge and the fix are in §18.

## 1. Existing evidence (carried forward)

| Evidence | Class | Source |
|---|---|---|
| 15,798 candidate ids discovered; 365 probed; 187 nonempty; 8,047 raw rows | MEASURED | [expansion audit](free-source-expansion-audit.md) |
| Raw inventory ≠ useful inventory: all 187 add ~6 positive-score jobs per synthetic cohort | MEASURED | same, §7 |
| The eight boards: 876 raw rows, 5.72 s summed request time | MEASURED | V2-A §10 |
| Deltas +15/+3, +26/+6, +15/+2, +15/+1 across four cohorts | MEASURED, dated 2026-09-22 | V2-A §10 |
| PubMatic's sampled job page returned 403 | MEASURED | [spot-checks](search-v2-evidence/first-tranche-spotchecks.json) |
| Shadow rows cannot reach production: `run()` returns None, units apart | VERIFIED | V2-A §9 |
| Lever concurrency independent of shadow | VERIFIED | V2-B2 `ShadowIndependence` |

## 2. Missing evidence (what this stage had to make obtainable)

Real-candidate usefulness (every figure above is a synthetic cohort), board
health and latency across dated real sweeps, overlap with the real 134-source
result, native-id stability, and the incremental cost of shadowing — without
changing what anyone sees.

## 3. Chosen diagnostic architecture

### The design question, re-evaluated

V2-A kept production shadow to "does it answer, how fast, how many rows"
because scoring the shadow rows inside a user's sweep "is both a real cost and a
real risk". Both halves were checked against the code rather than assumed:

| Concern | What the code actually does | Consequence |
|---|---|---|
| Calling `finalize()` twice | **VERIFIED unsafe**: it overwrites `telemetry` stages and counts, updates the global `LAST_STATS`, and `score_job` mutates every row it scores; with the experience guard on, `experience_guard.record()` appends to a module list | Never call `finalize()` for shadow |
| Cost of scoring shadow rows | **MEASURED** 5.0–5.2 ms/row; the tranche gates ~253 rows per synthetic cohort → ~1.3 s | Small next to the two finalize passes the Free tail already runs: 2 × 14.2–14.6 s on the same fixture and machine (§7) |
| When the user's result is final | **VERIFIED**: `emit()` writes CSV+JSON, `record_seen()` appends the ledger; the worker marks a run DONE on process exit (`deploy/sweep_worker.py` `_wait`) | Anything after `record_seen` cannot change an output file |
| A row-level offline replay | Would need every shadow row's **description** (scoring reads it) plus the candidate's rules (`profile.py`) copied off the worker | A privacy regression for a diagnostic (§5) |

**Chosen: shape C built from B's parts** — a bounded post-result diagnostic
stage, operating on its own copies, through the production functions
themselves, writing counts only. Offline replay (shape A) was rejected because
it needs JD text and a copy of each candidate's derived profile, and the live
evaluator makes both unnecessary.

### What changed

| File | Change |
|---|---|
| `scraper.py` | `finalize()` split into `score_and_filter()` (the chain, with a pluggable stage sink) and `rank_rows()` (sort + dedupe); finalize calls both — **byte-identical output**, proven on the frozen snapshot and by the full suite. `shadow_engine()` hands the production functions to the evaluator. The shadow call **moved out of `fetch_free()`** to `main()`, after `record_seen()`. |
| `sources/shadow.py` | `run(..., engine=None)` fetches, holds, and — given an engine — calls `evaluate()`. Both return None. Nothing happens without an open telemetry record. |
| `telemetry.py` | `shadow_evaluation(section)`: one top-level section, every value bounded. Absent when shadow did not run. |
| `bench/search_v2_shadow_production.py` | Offline aggregator over production records (§17). |
| `bench/search_v2_shadow_b3.py` | `--frozen` equivalence/cost, `--live` public benchmark, `--sweep` real free-sweep parity. |

### The flow, when both flags are on

```
free phase ─► emit (CSV+JSON) ─► emit ─► print ─► record_seen ─┐   user's result: FINAL
                                                               ▼
   shadow.run: 8 × ats.fetch (serial, unchanged adapter) ─► held rows (module-local)
        └► evaluate: copy + _truncate_desc ─► score_and_filter(private sink)
                     ─► rank_rows(final_rows + shadow rows) ─► counts
                     ─► telemetry.shadow_evaluation
                                                               ▼
                                   telemetry.identity ─► telemetry.finish ─► exit ─► DONE
```

## 4. Why it cannot affect production output

| Property | How it holds | Pinned by |
|---|---|---|
| No shadow request while any output can change | the call sits after `write_outputs` and `record_seen` | `test_shadow_is_requested_only_after_the_results_are_written` |
| No row set to merge | `run()` and `evaluate()` return None; rows live in a local | `test_run_returns_none_even_when_boards_yield_rows` |
| The chain is production's, not a copy | `score_and_filter` / `rank_rows` are the functions finalize calls | `test_every_stage_matches_the_production_chain` |
| Production funnel untouched | the evaluator passes its own stage sink | `test_production_stages_and_stats_untouched` |
| `LAST_STATS` untouched | the evaluator never calls finalize | same |
| No production row mutated | copies are scored; the result list is concatenated, not extended | `test_evaluator_mutates_no_production_row` |
| Experience-guard record untouched | `shadow_engine` truncates `DROPPED` back after each call | `test_experience_guard_record_is_restored` |
| Byte-identical CSV/JSON/seen ledger | all of the above, end to end through `main()` | `ResultIsolation` (4 tests) |
| The parity test can see a leak | the fixture holds a strictly better twin of a production row; merged, the survivor's apply URL and the row count both change | `test_the_fixture_would_expose_a_merge` |
| Source counts and banner stay at the registry | shadow units stay `shadow=True`; `ATS_BOARDS` untouched | `SourceCounts` (3 tests) |

## 5. Privacy model

**ROW-LEVEL ARTIFACT: NONE.** Every B3 figure is computed in-process and only
counts leave it. What is written is the record V2-A already writes, under
`<output_dir>/telemetry/`, with the same 48-hour lifecycle and deletion.

| Written | Never written |
|---|---|
| board tokens (fixed vocabulary, already in `shadow_units`), integer counts, ranks, durations, CPU ms, peak RSS, failure categories, exception type names | résumé text, job descriptions, titles, company names, URLs, native ids, matched skills, the derived profile, Apify tokens |
| search **scope** only: `work_scope`, `remote_scopes`, `max_age_days` — user choices, never résumé content | role keywords, locations, salary floor, anything inferred about the candidate |

Enforced, not intended: every value passes `telemetry._bounded` (strings
clipped to 200 characters, only plain containers); `test_every_value_is_bounded_
and_every_key_known` pins the exact key set and that the only strings are
`search-v2b3.1`, `evaluated`, `after_production` and scope words;
`test_no_row_text_or_credential_reaches_the_record` plants markers in titles,
descriptions, URLs, company names and `APIFY_TOKEN` and finds none in the file.
The evaluator records an exception's **type**, never its message, which could
quote a row. No new file, no CSV (the worker serves the newest `**/*.csv` as
results, so a diagnostic CSV would have been a live bug), nothing sent anywhere.

**Why no candidate cohort label.** The aggregator groups by search scope only.
Grouping by role family would need a résumé-derived property; the brief forbids
inferring one, so breadth is measured as "sweeps in which a board contributed",
which needs no label. Repeated sweeps by one person are indistinguishable by
design (§15).

## 6. Telemetry schema changes

`shadow_units[]` — **unchanged** (V2-A schema). One per board: duration, ok,
failure category, requests, retries, raw/normalized/gated.

`shadow_evaluation` — **new**, versioned independently as `search-v2b3.1`;
the record's own `schema` stays `search-v2a.1` because no production field
changed meaning.

```
schema, status, arrival, top_n, scope{work_scope, remote_scopes, max_age_days},
production_rows, production_rows_with_native_id, rows_evaluated,
totals{eligible, baseline_final, combined_final, baseline_positive,
       combined_positive, baseline_top_n, baseline_top_n_retained, top_n_new},
cost{fetch_wall_ms, evaluation_wall_ms, evaluation_cpu_ms,
     peak_rss_kb_before, peak_rss_kb_after},
by_board{<board>: {fetched,
   gated, fresh, source_unique, unkeyed, native_id, native_id_distinct,
   requisition, url, url_carries_native_id, url_ats_hosted,
   key_overlap, key_and_native_overlap, key_overlap_native_distinct,
   key_overlap_native_not_comparable, native_overlap, native_overlap_key_distinct,
   funnel{<stage>: n}, eligible, eligible_positive, final_new,
   final_new_positive, final_replaces, top_n_new, top_n_replaces, best_new_rank}}
```

`status` is `evaluated`, `acquisition_only_over_budget` (more than 1,000 gated
rows: acquisition fields kept, funnel `None`) or `failed` (then only
`failure_category` and `error_type`). A board that failed carries every field
as **None — unavailable, never zero**. One deliberate change in meaning:
`milestones.free_phase_done` no longer includes shadow time, because shadow no
longer runs inside the free phase.

## 7. Performance overhead

Four separate costs, measured separately.

| Cost | MEASURED | Evidence |
|---|---|---|
| **Network acquisition** (inherent to shadowing at all; V2-A already paid it) | 5.0 s summed, no stall (live, 2026-09-23); 9.7 s with one slow 5.2 s response; **31.4 / 32.2 / 34.4 s with one timeout+retry each** | [live](search-v2-evidence/shadow-b3-live-benchmark.json), [sweeps](search-v2-evidence/shadow-b3-free-sweep-parity.json) |
| **Diagnostic CPU** (new in B3) | 1.290–1.303 s over 253–254 gated rows (frozen, 5 cohorts); 1.29–1.30 s over 261 rows (real sweeps); one production finalize over ~3,100 rows is 14.2–14.6 s | [frozen](search-v2-evidence/shadow-b3-frozen-equivalence.json) |
| **Diagnostic memory** | peak RSS 80,336 → 106,272 KiB (+25.3 MiB) and 81,376 → 105,392 KiB (+23.5 MiB) across the shadow stage of a 4-board sweep, from holding board payloads (Brex with `content=true` is ~4 MB of JSON). In a full sweep the process peak may not move at all: it is a maximum, and the free phase already sets it | same |
| **Artifact size** | record 5.2 KB → 18.8 KB in a 4-board sweep: +5.4 KB of shadow units, +8.2 KB of section. **Independent of row count** | same |

**The portable figure is the ratio.** Evaluator CPU ÷ one production finalize
pass = **0.090–0.091** in every cohort; the Free tail runs finalize twice
(checkpoint emit and final emit), so the evaluator is **~4.5% of the tail's
local CPU**. Both sides scale with the same gate, so the ratio should carry to
Oracle's slower cores even though the seconds will not (**INFERRED**).

**Against the Free phase.** V2-B2's production Free phase was 140–164 s before
it removed ~61 s of Lever waiting. Typical shadow cost is ~5 s fetch + ~1.3 s
evaluation ≈ **6.5 s, of which B3 itself adds ~1.3 s** — the fetch is V2-A's.
One stalled board adds ~26–29 s, and under production HTTP semantics (25 s
timeout, 2 retries, 1 s + 2 s backoff) the worst case is ~78 s per board.
**This tail is on the user's critical path**: the worker marks a run DONE on
process exit, so the stage delays completion even though it cannot change the
result. B3 moves the fetch after the result files are written, so the files are
complete ~5 s *earlier* than under V2-A's placement; DONE is not.

The shadow fetch keeps production semantics on purpose. A promoted board would
pay exactly this stall, so how often it happens from Oracle is itself the
evidence a promotion decision needs; a tighter shadow-only timeout would
under-report it. The experiment's stop rule bounds the cost instead (§16).

## 8. Candidate usefulness and top-N — method

The funnel is the production chain run over the shadow rows: `observed_
normalized` (= the acquisition gate, candidate-relative because it uses the
candidate's own title and location predicates) → `post_hard_filter_and_score`
→ `post_recency` → `post_salary_reachability_visa_eor` → `post_arrangement` (when
the scope applies) → `post_location_eligible` = **eligible**. `eligible_positive`
is eligible with score > 0.

**Top-N compares against the real result, never shadow rows alone.**
`rank_rows(final_rows + shadow_eligible_as_output)`, with shadow rows appended
after every production row:

- **It equals `finalize(production + shadow)` exactly.** Dropping from the
  combined sort every production row that lost to its own best twin changes no
  survivor: that row's twin precedes it with the same key, so it was dropped
  anyway, and it could never beat a shadow row its twin beat. **VERIFIED by
  proof, MEASURED on real data** (§0.1) and pinned by `CandidateFunnel`.
- **Ties go to production.** The sort is stable, so arrival decides a tie;
  arriving last, a shadow row loses every exact tie. That can only undercount a
  shadow gain, never overstate it. (V2-A's replay used the same order.)
- **New vs replaces.** A surviving shadow row whose `job_key` was already in the
  result is `final_replaces` — it outranked its production twin and would change
  which duplicate the user sees. It is **not** counted as new inventory.
  `top_n_new` counts only new keys entering the top 20 — V2-A's definition.
- `baseline_top_n_retained` says how many of the real top 20 would survive;
  `best_new_rank` is the best position a board's new row reached.

`test_a_tie_at_the_cut_goes_to_production`, `test_a_better_twin_replaces_and_is_
not_counted_as_new` and `test_an_equal_twin_loses_to_production` pin each rule
at exact ranks.

## 9. Overlap and native identity

Two concepts, never merged, neither affecting dedupe:

| Field | Definition |
|---|---|
| `key_overlap` | a shadow gated row whose **current production `job_key`** matches any row the sweep acquired — "would current dedupe already treat this as something we have?" |
| `native_overlap` | a shadow row whose **(provider, native id)** matches an acquired row. Namespaced: a Lever id and a Greenhouse id never compare, even as equal strings (pinned) |
| `key_and_native_overlap` | both agree: the same posting |
| `key_overlap_native_distinct` | **job_key says duplicate, native says distinct** — the twin is a Greenhouse row with a different id |
| `key_overlap_native_not_comparable` | job_key matches only another provider's row, or one without a native id |
| `native_overlap_key_distinct` | **native says same posting, job_key says distinct** — e.g. a retitled posting or a second board token for one employer |

Identity quality per board: `native_id` coverage, `native_id_distinct`,
`requisition` (Greenhouse `internal_job_id`), `url`, `url_carries_native_id`,
`url_ats_hosted`, `source_unique` / `unkeyed`. Production rows carry native ids
because telemetry is on during the free phase (`production_rows_with_native_id`
says how many), so the comparison is available whenever shadow can run.

Cross-run id **stability** cannot be measured from production records without
persisting ids, which would be row-level data linked to a candidate's gate; it
is measured by the public benchmark instead (§14).

## 10. Lever concurrency interaction

Shadow boards never enter the Lever executor. Shadow calls `ats.fetch` directly,
serially, after `sources.fetch_free` has returned; `BOARDS` has only a
`greenhouse` key; `concurrency.PROVIDERS` is still `("lever",)`; worker count is
read from its own variable only. Pinned by `LeverIndependence` (a spy on
`concurrency._fetch_one` and `fetch_boards` under `SWEEP_FREE_LEVER_CONCURRENCY=1`
sees only the two production Lever boards), by V2-B2's `ShadowIndependence`, and
MEASURED in the `production_like` real sweep (Lever workers=4 + shadow): output
identical to the serial sweep.

## 11. Failure semantics

| Failure | Behaviour | Pinned by |
|---|---|---|
| One board fails | recorded in its unit (`http_503`); the other seven are evaluated; its section fields are None | `test_one_board_failing_costs_that_board_only` |
| All eight fail | sweep completes; section `evaluated` with 0 rows | `test_every_board_failing_still_completes_the_sweep` |
| Evaluator raises | section `failed` + category + type; sweep completes | `test_an_evaluator_failure_is_recorded_not_raised` |
| `shadow.run` itself raises | caught in `main()`; record still written | `test_the_shadow_call_itself_raising_cannot_fail_the_sweep` |
| Record cannot be written | `telemetry.finish` logs to stderr; sweep completes; outputs identical | `test_a_record_that_cannot_be_written_cannot_fail_the_sweep` |
| Over 1,000 gated rows | acquisition kept, funnel skipped | `test_over_budget_keeps_acquisition_and_skips_the_funnel` |
| Shadow on, telemetry off | nothing fetched; one log line | `test_on_without_telemetry_fetches_nothing` |

The last row is the one behavioural change to V2-A's shadow: with nowhere to
record evidence it used to fetch eight boards for a log line. Now "shadow on"
is one coherent mode — fetch, judge, record — and needs the record.

## 12. Tests

`sweep/tests/test_search_v2_shadow_eval.py` — **55 tests**, sockets denied for
the module, every response a fixture served through `ats.get_json` so the real
adapter maps it. End-to-end cases drive `scraper.main()` in-process.

| Group (brief's number) | Pins |
|---|---|
| `ShadowOff` (1) | zero shadow requests; no section, no units, no log line; no extra file; serial and Lever paths fetch exactly the registry; inert when disabled; no fetch without telemetry |
| `TheEightBoards` (2) | exactly the eight, in order; PubMatic absent; shadow requested only after the results are written |
| `ResultIsolation` (3) | the fixture would expose a merge; CSV, JSON and seen ledger byte-identical; rows, ranking, survivor and count identical; the shadow really ran |
| `SourceCounts` (4) | production telemetry identical except shadow fields; units separate; banner unchanged |
| `FailureSemantics` (5–7) | one, all, evaluator, outer call, record write, over budget |
| `Overlap` (8) | all six overlap categories on purpose-built rows; namespacing; identity fields; dedupe unchanged |
| `CandidateFunnel` (9) | per-board counts == `finalize(P+S)`; every stage == the production chain's stage counts; every exit exercised; production stages/stats untouched; no production row mutated; experience-guard record restored |
| `TopN` (10) | real ranks against a 25-row result; tie at the cut; better twin; equal twin; totals; result list not reordered |
| `Privacy` (11) | markers absent; key set and string vocabulary pinned; no file beyond the record |
| `LeverIndependence` (12) | executor spies; worker count; shadow uses the adapter |
| `PaidIsolation` (13) | no paid name in shadow's code; nothing paid reachable from a shadow sweep |
| `Aggregator` | the offline tool reads real records: health, usefulness, breadth, cohorts, no row text, one record per sweep |

V2-A's two shadow tests that stubbed a fetch without a telemetry record would
now pass without fetching anything, so both were strengthened: they open a
record and assert the eight fetches happened.

## 13. Mutation checks — all eleven caught

Each applied alone to the source, the three relevant suites run
(`test_search_v2_shadow_eval`, `test_search_v2_telemetry`,
`test_free_concurrency`), then reverted and verified byte-identical with `cmp`.

| # | Mutation | Result | Among the failures |
|---|---|---|---|
| A | shadow boards merged into production fetch when the flag is on | **15 fail** | `test_csv_and_json_byte_identical`, `test_rows_ranking_survivor_and_count_identical`, `test_production_fields_identical` |
| B | shadow units counted in `sources_attempted` | **2 fail** | `test_production_fields_identical`, V2-A `test_shadow_units_do_not_inflate_sweep_source_counts` |
| C | PubMatic added | **10 fail** | `test_exactly_the_eight_in_order_and_no_pubmatic`, V2-A `test_eight_expected_boards_and_no_pubmatic` |
| D | one board's exception escapes per-board isolation | **5 fail** | `test_one_board_failing_costs_that_board_only`, `test_every_board_failing_still_completes_the_sweep` |
| D2 | no outer wrapper in `main()` | **1 error** | `test_the_shadow_call_itself_raising_cannot_fail_the_sweep` |
| E | a job description leaks into the section | **2 fail** | `test_no_row_text_or_credential_reaches_the_record`, `test_every_value_is_bounded_and_every_key_known` |
| F | shadow boards fed through the Lever executor | **9 fail** | `test_shadow_boards_never_enter_the_lever_executor`, `test_production_fields_identical` |
| G | evaluator ranks in place over the user's result list | **6 fail** | `test_evaluator_mutates_no_production_row`, `test_the_result_list_was_not_reordered` |
| H | evaluator writes its funnel into production stages | **5 fail** | `test_production_stages_and_stats_untouched`, `test_production_fields_identical` |
| I | shadow fetched inside `fetch_free` again, before the write | **8 fail** | `test_shadow_is_requested_only_after_the_results_are_written` |
| J | tie rule flipped (shadow ahead of production) | **6 fail** | `test_a_tie_at_the_cut_goes_to_production`, `test_final_survivors_positive_and_top_n_match` |

## 14. Live public benchmark

**MEASURED 2026-09-23 11:44 UTC**, `--live --reach 1`: 19 public GETs (8 boards,
8 sampled job pages, 3 provider-by-id pages), **zero Apify**, through the
unchanged adapter. [Evidence](search-v2-evidence/shadow-b3-live-benchmark.json);
the fetched rows are kept outside the repository with their SHA-256 recorded.
**One dated observation — not longitudinal evidence.**

| Board | ms | Retries | Raw | Fresh 14d | Native id | Distinct | Req. id | URL has id | ATS-hosted | Overlap w/ frozen baseline | Ids kept since 09-21 | Gone | New | Kept id, retitled | Job page |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| fivetran | 1230 | 0 | 198 | 192 | 198 | 198 | 197 | 198 | 0 | 1 | 191/197 | 6 | 7 | 4 | 200 employer; by id 200 → employer |
| abnormalsecurity | 452 | 0 | 82 | 48 | 82 | 82 | 82 | 82 | 0 | 0 | 76/78 | 2 | 6 | 1 | 200 employer; by id 200 → employer |
| apolloio | 603 | 0 | 49 | 16 | 49 | 49 | 49 | 49 | 49 | 0 | 46/47 | 1 | 3 | 3 | 200 greenhouse |
| brex | 777 | 0 | 253 | 77 | 253 | 253 | 253 | 253 | 0 | 0 | 246/251 | 5 | 7 | 4 | 200 employer; by id 200 → employer |
| vercel | 462 | 0 | 89 | 89 | 89 | 89 | 89 | 89 | 89 | 0 | 83/84 | 1 | 6 | 7 | 200 greenhouse |
| jumio | 396 | 0 | 27 | 8 | 27 | 27 | 27 | 27 | 27 | 0 | 27/27 | 0 | 0 | 0 | 200 greenhouse |
| catawiki | 437 | 0 | 46 | 46 | 46 | 46 | 46 | 46 | 46 | 0 | 45/47 | 2 | 1 | 0 | 200 greenhouse |
| zetaglobal | 688 | 0 | 144 | 54 | 144 | 144 | 144 | 144 | 144 | 0 | 143/145 | 2 | 1 | 0 | 200 greenhouse |

888 raw rows (876 two days earlier), 5.05 s summed, 0 failures, 0 retries in
this pass.

**Identity is credible.** Every row has a native id, every id is distinct,
every URL embeds it, and for the three employer-hosted boards the provider's own
URL for the id redirects to the employer's posting (HTTP 200). **97.8%** of
two-day-old ids persisted. Retitling under a stable id (19 postings, 2.2%) is
the concrete case for native identity: `job_key` would call each one a new job,
and the title gate can move — Vercel's renames turn non-matching titles into
"Software Engineer". One gone Vercel id's title returned under a new id (a
repost, by title within the board — approximate). A 200 does not prove a vacancy
is open, and one sampled page per board is not a reachability rate.

**Live inventory, synthetic cohorts** (live shadow rows judged against each
cohort's frozen 2026-09-21 baseline result — mixed dates, labelled so in the
artifact):

| Cohort | Final | Positive | New in top 20 |
|---|---|---|---:|
| Full-stack, India | 81 → 96 (+15) | 19 → 22 (+3) | 3 |
| Full-stack, remote | 293 → 317 (+24) | 39 → 44 (+5) | 2 |
| React Native, India | 81 → 96 (+15) | 19 → 21 (+2) | 2 |
| Business/Salesforce, India | 89 → 104 (+15) | 7 → 8 (+1) | 1 |
| Multiple roles, India | 89 → 104 (+15) | 19 → 22 (+3) | 3 |

New positive-score rows per board, live, across the five cohorts
(FS-India / FS-remote / RN / Salesforce / multi = total): Fivetran 1/0/1/0/1 = 3;
Abnormal Security 1/2/0/1/1 = 5; Apollo.io 1/0/0/0/1 = 2; Brex 0/1/0/0/0 = 1;
Vercel 0/1/0/0/0 = 1; Catawiki 0/1/0/0/0 = 1; Zeta Global 0/0/1/0/0 = 1;
**Jumio 0/0/0/0/0 = 0**. Identical to today's frozen replay.

## 15. Unresolved risks

1. **UNKNOWN — stall rate from Oracle for these eight boards.** Each stall costs
   the user ~26–29 s (worst case ~78 s per board). Production Greenhouse retries
   were 0 in 162 fetches, which is encouraging and not proof.
2. **UNKNOWN — real-candidate usefulness.** Everything above is five synthetic
   cohorts. The experiment exists to replace them.
3. **Repeated sweeps by one person inflate N** and cannot be told apart in a
   record, by design. The experiment proposal limits this by counting natural
   traffic and reporting sweeps, never "users".
4. **INFERRED — the CPU ratio transfers to Oracle.** Seconds will not.
5. **Title-gate drift.** A board's gated count moves when an employer retitles
   (Vercel, §14), so per-board yield varies week to week for reasons unrelated
   to the board's value. Read distributions, not a single run.
6. **Slug-derived company labels** (`BOARDS` names) still decide `job_key`, so a
   cross-board alias under different labels shows up only as
   `native_overlap_key_distinct` — the field exists to surface exactly that.
7. **The tie rule is conservative**, so shadow gains are a lower bound.
8. **Application reachability** is one sample page per board per benchmark; a
   rate needs repeated bounded samples outside user sweeps.
9. **`--only-new` is not modelled.** That CLI flag filters the result list after
   finalize; the worker never passes it. Under it, shadow's baseline is the
   filtered list and shadow rows are not filtered against the seen ledger, so
   new and top-N counts are approximate there.

## 16. Proposed production experiment — NOT executed

Only after V2-B2's `workers=4` phase is considered stable.

```
SWEEP_SEARCH_V2_TELEMETRY=1
SWEEP_FREE_LEVER_CONCURRENCY=1
SWEEP_FREE_LEVER_WORKERS=4
SWEEP_FREE_SOURCE_SHADOW=1
```

Run over **10–20 natural Free Sweeps** from beta traffic. No manufactured copies
of one résumé. Copy `output/telemetry/sweep_*.json` off the worker inside the
48-hour TTL (counts only — safe to copy), then:

```
.venv/bin/python -m bench.search_v2_shadow_production <records dir> \
    --json shadow-production.json --markdown shadow-production.md
```

**Stop rule** (decided before starting, so a slow week cannot be rationalised):
turn the flag off if the shadow stage (`fetch_wall_ms + evaluation_wall_ms`)
exceeds 30 s in more than 2 of the first 10 sweeps, if its median exceeds 10 s,
or if any record shows a shadow board among the production `units` or a
`sources_attempted` that differs from the production unit count — either would
be an isolation bug, not a slow board. Rollback is the flag; it is read per
sweep.

Watch: per-board failure categories and retries (a nonzero Greenhouse retry
count is new information), the shadow stage's share of the free phase, the
`production_rows_with_native_id` field (0 would mean telemetry was off during
fetch), and `status` — `failed` or `acquisition_only_over_budget` in production
would each be a finding.

## 17. What evidence would justify promoting a board

No board is promoted automatically, and there is no composite score — a board
can pass one question and fail another, and a single number would hide the
tradeoff. The aggregator answers each question per board:

| Question | Evidence | Suggested bar, for review — not a rule in code |
|---|---|---|
| Does it stay healthy? | success rate, failure categories, retries | no failure category other than rare transients across the sample |
| Is its latency reasonable? | median / p95 duration | p95 within Greenhouse's production p95 (2.08 s, V2-B2) |
| Genuinely new inventory? | `key_overlap` share, `native_overlap`, `final_replaces` | low overlap; replaces understood case by case |
| Useful inventory? | `final_new_positive`, `top_n_new`, `best_new_rank` | at least one new positive-score row in real sweeps |
| Useful for more than one search shape? | sweeps with a new positive row; by-scope breakdown | positive contribution in ≥2 sweeps, ideally ≥2 scopes |
| Credible identity and reachability? | native coverage, distinctness, URL-embeds-id; bounded page samples | full coverage (all eight have it today) and no 403 pattern |
| Material cost to Free sweeps? | fetch ms, evaluation ms, share of free phase | promoting one board adds its fetch (~0.4–1.2 s) and its rows to finalize — measured here per board |

Boards that score in no real sweep over the sample are not "worthless" — Jumio
scored in V2-A's replay and not today — but they have not earned a place yet.

## 18. Paid calls — an incident during verification

**The shipped code makes no paid call and cannot:** shadow's code names no paid
path, a shadow sweep is pinned to reach nothing paid (`PaidIsolation`), and the
evaluator's engine contains no paid function.

**My verification did make paid calls.** To check the real worker-shaped path,
I wrote temporary profiles overriding only `ATS_BOARDS`, `FEEDS` and
`SETTINGS`, and ran `scraper.py --profile … --yes` with `APIFY_TOKEN` unset.
The profiles inherited the default paid sites. **`_require_token()` calls
`load_dotenv()`**, which read the token from the repository's `.env` despite
the unset variable, and the sweep began the default paid plan.

| | |
|---|---|
| **Searches completed** | **23** — 18 LinkedIn (`curious_coder/linkedin-jobs-scraper`, each under the V2-B1 $0.046 ceiling) and 5 Indeed (`misceres/indeed-scraper`) — 17:17–17:27 local, 2026-09-23, per `.done_combos` |
| **In flight when stopped** | probably one more Indeed search; stopping the local process does not abort a run on Apify's side |
| **Estimated charge** | **~$1.0–1.1** at published prices (18 × $0.030 + 6 × $0.090); **upper bound ~$1.37** (18 × $0.046 ceiling + 6 × $0.090) |
| **Actual charge** | **UNKNOWN** — the process was killed before printing its spend line, and the account was not queried |
| **Data** | returned rows existed only in a scratch directory and were deleted |

**How it was found and stopped:** the first run took minutes instead of
seconds; its output directory held `.done_combos` and checkpoint files, which
only paid searches write. The process was killed and the profiles deleted.

**The fix is structural, not a promise:** `bench/search_v2_shadow_b3.py --sweep`
passes `--site free` (an empty paid plan), disables every paid site in the
profile the way the worker's `free_prefs` does, refuses to run unless a
`--dry-run --json` pre-check shows an empty plan, and kills a run on the first
paid marker in its log. Every real sweep reported above was run that way.

## 19. Verification

- `python -m unittest discover -s sweep/tests -t .` — **1047 tests, OK** (992
  before, +55 new).
- `python scraper.py --demo`, `python -m sources`, `python telemetry.py`,
  `python -m sources.concurrency` — pass.
- `auto-apply` suite — 1,102 tests, 2 errors in `test_inference` healthz (a
  local inference service answering 503); **the same two errors reproduce on a
  clean checkout of `b82b4f8`**, so they are environmental and unrelated.
- `finalize` refactor — old and new produce **byte-identical** output and
  `LAST_STATS` on the frozen snapshot (full-stack India and remote).
- Frozen equivalence — 5/5 cohorts, live == offline.
- Real free sweeps — two runs of four arms, CSV and JSON byte-identical in all.
- Mutation checks — 11/11 caught.

## 20. Relationship to V2-A

| V2-A said | B3 | Reading |
|---|---|---|
| §9: production shadow "deliberately does not compute" eligibility, positive-score, marginal-unique or top-set entry | computed, isolated, counts only | **Superseded**, for the reasons in §3 |
| §9: shadow runs inside `fetch_free()` | runs after `record_seen()` | **Moved**, so no shadow request is made while output can change |
| §10: every board earns a positive row somewhere | Jumio does not, today | **Corrected**: date-sensitive, not a V2-A error |
| §10: +15/+3, +26/+6, +15/+2, +15/+1 | +14/+3, +24/+5, +14/+2, +14/+1 (frozen, 2026-09-23) | **Not a contradiction**: the 14-day window moved two days |
| §14.6: id stability across refreshes UNKNOWN | 97.8% of ids persisted over two days; 2.2% retitled under a stable id | **Partly resolved**; two dates are not a longitudinal rate |
