# V2-C1 — every paid search, observed; nothing it does, changed

Date: 2026-09-24. Baseline: `89c8daa` (V2-C0, local). Production runs V2-B5
with `SWEEP_SEARCH_V2_TELEMETRY=1`, Lever 1/4, Greenhouse 1/4,
`SWEEP_FREE_SOURCE_SHADOW=1` and `SWEEP_RESULTS_READY_EARLY=1`.
Scope: **paid-search observability and real-world research traces.** No paid
concurrency, batching, plan reduction, adaptive depth, provider reordering,
budget reservation or deployment. No flag was added and no environment or
deployment file changed.

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a dated
probe), **VERIFIED** (directly observed code or configuration), **INFERRED** (a
model with stated assumptions), **UNKNOWN** (evidence unavailable, which is not
the same as zero).

Nothing the engine decides moved: the plan, its order, depth, B1's actor input
and `maxTotalChargeUsd`, the budget guard, retries, scoring, ranking, dedupe,
`job_key`, Search Preferences, Indeed, Naukri, the Free sweep, B3's shadow, B4's
executors and B5's readiness. `SWEEP_EXPERIENCE_MISMATCH_GUARD` remains off.

## 0. Headline findings

1. **VERIFIED — with telemetry on, the engine makes the same requests, in the
   same order, with the same actor input and ceiling, and writes the same bytes**
   as with it off: an end-to-end fixture of two paid units plus a free source,
   and a 90-search replay (§14, §20).
2. **MEASURED — the terminal poll undercounts the run's charge, every time.**
   Four live runs read $0.00205, $0.02805, $0.01005 and $0.02805 on the poll that
   saw them finish; each settled at **$0.03005** (B1's model to the cent). The
   account delta the budget guard reads was **$0.00005 — the start event alone —
   for two of the three C1 runs**, 5–7 s after they finished (§10).
3. **MEASURED — the run record settles within ~6–23 s of the actor finishing;
   the account covers it within ~22–39 s.** It then carries a small residual that
   is no run's (+$0.000009 to +$0.000039). So a run's cost is knowable, reliably,
   about half a minute after it ends — not when the engine first looks (§10).
4. **MEASURED — the ~7.5 s C0 could not explain is mostly polling.** Beyond the
   actor's own runtime a unit spends 5.1–7.6 s: **3.3–5.6 s waiting for the next
   5 s poll to notice the finish**, ~0.6 s starting, 0.6–1.1 s reading the
   dataset, 0.3–0.5 s reading the account (§9).
5. **MEASURED — no decay by position in three 15-row traces.** Positions 1–5,
   6–10 and 11–15 gave 13, 11 and 14 eligible rows and 13, 11 and 13 final rows
   of 15 each. On this sample the last five rows are worth as much as the first
   five (§19).
6. **MEASURED (offline) — checkpointing is the paid path's hidden local cost.**
   A 90-search replay spends ~226 s of CPU re-finalizing the growing row set
   after every search. Telemetry (V2-A + C1) adds **+0.22 s of CPU to ~225 s (0.1%)**, inside run-to-run noise, with identical output (§20).
7. **MEASURED — 15 targeted mutations, all caught**, each file restored
   byte-identical (§22).
8. **Spend.** C1 made **3 LinkedIn actor starts: $0.138 intended (worst case),
   $0.0902 actual.** With C0's run the ledger holds $0.184 intended and $0.1202
   actual, against the shared $2.00 C1–C4 ceiling (§17).

## 1. The paid lifecycle, as it is — VERIFIED

For one paid search in `scraper.main()`, and what was knowable at each step
before C1:

| # | Step | Code | Known before C1 | C1 adds (no new request) |
|---|---|---|---|---|
| 1 | plan | `resolve_sites` → `plan_for_site` → `build_search_plan` | the plan, printed; nothing recorded | `paid_units[]`: id, provider, actor, depth, ceiling, query fingerprint, status |
| 2 | billed depth | `effective_search` | `requested_limit` | — |
| 3 | actor input | `build_input` | — (not recorded) | — (unchanged; not recorded) |
| 4 | ceiling | `max_charge_usd`, `charge_ceiling_supported` | `max_total_charge_usd` | `charge_ceiling_usd` in the plan entry |
| 5 | budget check | `spent >= budget` before the start | — | status `skipped_budget` |
| 6 | start | `actor.start(run_input, run_timeout, max_total_charge_usd)` | run id, build id, `actor_started_at` | `start_ms`; `provider_ceiling_usd` read off the returned `Run` |
| 7 | poll | `sleep(5)` → `rc.get()` until not `READY`/`RUNNING`; 360 s deadline, then abort | `poll_count`, status, `actor_finished_at` | `wait_ms`, `poll_get_ms`; from the terminal `Run`: `actor_run_time_s`, `actor_build_number`, `charged_events`, and `usageTotalUsd` as a provisional cost reading |
| 8 | dataset | `client.dataset(id).iterate_items()` | `dataset_retrieved_at` | `dataset_ms` (read + normalize) |
| 9 | normalize | `normalize()`; `search_query`, `search_rank` stamped | rows (`raw_count` = normalized = gated) | provenance `(unit_id, position)` on each row, in memory only |
| 10 | account | `account_usage_usd` after the run | `reported_cost_usd`, `billed_delta_usd` | `account_read_ms`, `budget_view_usd`, `budget_basis`, the delta as a provisional reading |
| 11 | checkpoint | `emit(raw_rows)`: finalize + write | — | `checkpoint_ms` |
| 12 | done ledger | `.done_combos` append | — | status `completed` / `failed` / `skipped_done` |
| 13 | stale → eligible | `score_and_filter` stage boundaries | per-source counts per stage | per-row last boundary reached, score sign |
| 14 | dedupe → final | `rank_rows` (sort, `dedupe`) | per-source final counts | which unit each survivor came from; what beat each loser |
| 15 | identity | `telemetry.identity` over all raw rows | per-source key counts | per-row: new, repeat in unit, repeat of an earlier paid unit, also acquired free |

A failed search (any exception in the per-search `try`) records `ok: false`
and a category, and — VERIFIED — **does not read the account**, so its charge
reaches the guard only through the next successful search's cumulative delta,
or never if the account is unreadable.

## 2. Telemetry architecture

Extends `SWEEP_SEARCH_V2_TELEMETRY`; **no new flag**. The existing flag is the
right switch: C1 records facts about the same paid units V2-A already records,
into the same record, under the same privacy rules, and everything it adds is
inert when that flag is off.

| Hook | Where | What |
|---|---|---|
| `paid_plan(units)` | `main()`, before the client | one entry per planned search, `status: planned` |
| `paid_status` / `paid_unvisited` | the paid loop | `completed`, `failed`, `skipped_done`, `skipped_budget` |
| `paid_run(...)` (V2-A, allowlist extended) | `scrape_search`, the loop | clocks, `Run` fields, budget view |
| `cost_observation(source, usd)` | after the terminal poll; after the account read | one reading, timestamped, finality from a fixed table |
| provenance stamp | the loop, only when `telemetry.active()` | `row["_paid_unit"] = (unit_id, position)` |
| `stage()` (V2-A) | every `score_and_filter` boundary | also: each paid row's last boundary and score sign |
| `paid_outcome(eligible, final, job_key)` | `finalize()`, after `rank_rows` | dedupe outcome and exclusivity per paid row |
| `identity()` (V2-A) | end of `main()` | also: each paid row's acquisition class |
| `finish()` | end of sweep | assembles `paid_units[].funnel/trace` and `paid_summary` |

Every C1 hook that runs inside `finalize()` or the per-search `try` is wrapped
(`_guarded`): an exception there would turn a search that succeeded into one
that failed, so a failure becomes a note in the record instead
(`test_a_hook_that_breaks_cannot_fail_the_sweep`, mutation K).

## 3. Paid unit identity

`paid_000`, `paid_001`, … in the order the loop visits searches: sites in
`SITES` order, then each site's plan (keyword outer, location inner). Assigned
from the plan before any client exists, so a unit that is skipped, stopped at
the cap, or fails before a run id exists still has one. Deterministic for a
given plan (`test_ids_and_fingerprints_are_deterministic`).

Each entry also carries `query_fp` — the first 12 hex of SHA-256 over
`site|keywords|location|company` — so repeats of one search group together
without the query text, `location_mode` (`remote` when the search itself
constrains to remote, else `place`) and `company_filter`.

The join keys: planner unit ↔ actor start (`units[].unit_id`, then
`actor_run_id`) ↔ rows (`(unit_id, position)`, position = `search_rank`) ↔
funnel (`paid_units[].funnel`).

## 4. Planned vs executed

`paid_summary`: `planned`, `attempted` (= `completed` + `failed`), `completed`,
`failed`, `skipped_done`, `skipped_budget`, `unvisited` (0 unless a bug),
`actor_starts` (units with a run id — a unit that failed before the start POST
returned is attempted but not a start), `planned_bounded_exposure_usd` and
`attempted_bounded_exposure_usd` (Σ provider ceilings, as exact decimal strings),
`planned_unbounded_units` / `attempted_unbounded_units` (units with no ceiling —
counted, never priced), `paid_phase_ms` (milestones `paid_phase_start` →
`paid_phase_done`), and `by_provider` with the same counts plus summed unit
wall, wait, dataset and checkpoint time, actor runtime, and funnel totals.
Pinned by `test_planned_attempted_completed_failed_skipped` and
`test_providers_aggregate_their_own_units`.

## 5. The paid funnel

Per completed unit, `paid_units[i].funnel`:

```
requested   billed depth (effective_search)
raw         rows the dataset returned            (units[].raw_count)
normalized  rows that entered finalize           (== raw: normalize never drops)
stages      rows reaching each score_and_filter boundary that ran
stale       post_hard_filter_and_score − post_recency
eligible    rows reaching post_location_eligible
eligible_positive, final, final_positive, final_marginal
dedupe_lost {same_unit, other_paid, free}
acquired    {new, repeat_in_unit, repeat_of_earlier_paid, unkeyed}
key_also_free
```

`funnel` is `null` for a unit that never ran or failed — there were no rows to
follow — and zeros only for a run that really returned nothing
(`test_a_failed_run_invents_no_rows_and_no_cost`, `test_zero_results_are_zeros`,
mutation N). It describes the **last finalize pass**, the one over the complete
row set: each pass begins at the first boundary and replaces the previous
pass's reading. `stale` counts rows removed by recency *after* the hard filter,
the same order `LAST_STATS["stale"]` counts in (`test_the_funnel_agrees_with_
the_filter_counts_the_engine_prints`). `positive` is score > 0, B3's
definition.

**The trace.** `paid_units[i].trace` is one four-character token per dataset
position, in position order:

| Char | Values |
|---|---|
| 1 — removed by | `H` hard filter or score, `S` stale, `R` salary/reachability/visa/EOR, `A` arrangement, `G` geography, `D` dedupe (eligible, another row won); `F` final |
| 2 — score | `+` positive, `-` zero or less, `.` not scored |
| 3 — acquired | `n` new to the sweep, `u` repeat within this unit, `p` repeat of an earlier paid unit, `k` no `job_key` |
| 4 — free | `f` the same `job_key` was also acquired from a free source, `.` not |

The legend is in every record (`paid_summary.trace_legend`). Counts are
aggregates of the trace (`test_counts_equal_the_trace`).

## 6. Provenance — behaviour-neutral by construction and by test

Paid rows did carry `search_query` and `search_rank`, but the label is not an
identity (two sites share it) and dedupe happens inside `finalize`, where
nothing recorded which unit a surviving row came from. C1 stamps
`row["_paid_unit"] = (unit_id, position)` right after the unit's rows are
normalized, **only while a telemetry record is open** — the bargain V2-A made
for `_native` (`sources/ats.py`):

- `to_output()` reads a fixed key list, so the key cannot reach the CSV or
  JSON, and `csv.DictWriter` would raise on an unknown column anyway.
- `job_key`, `score_job`, `enrich`, every filter and `dedupe` read named
  fields; none iterates a row's keys (checked: `enrich.py`, `sources/shadow.py`,
  `experience_guard.py`). Shadow reads production rows for `job_key` and
  `_native` only.
- With telemetry off nothing is stamped
  (`test_nothing_is_stamped_with_telemetry_off`).

`test_csv_json_and_seen_ledger_byte_identical`,
`test_the_provenance_key_reaches_no_output` (no `_paid_unit`, no `paid_00` in any
output byte), mutation D.

## 7. Duplicate attribution

Two levels, both read off what the engine already did:

- **Acquisition** (`identity()`, before any filter): each paid row's `job_key`
  against what the sweep had already acquired in arrival order — paid units in
  plan order, then free — as `new`, `repeat_in_unit`, `repeat_of_earlier_paid`
  or `unkeyed`, plus whether the key also arrived from a free source. This is
  the "repeat work" view C3 needs: a start whose rows were mostly `p` bought
  what an earlier start already had.
- **Dedupe** (`paid_outcome()`, on dedupe's own result): an eligible paid row
  that is not in the survivor list lost to the survivor holding its `job_key`,
  classified `same_unit`, `other_paid` or `free`. The survivor is **looked up,
  not re-decided**: dedupe's output is the input, so a sort change or a new
  tie rule in the engine changes this attribution with it.

Free rows arrive after every paid row, so "duplicates against free inventory"
is exact at acquisition (`key_also_free`) and exact at dedupe (`lost_to: free`).

## 8. Final contribution

A final job belongs to **the unit whose row survived dedupe**, and to no other.
One job is counted once (`test_one_final_job_is_counted_once`, mutation E).
`final_marginal` says the survivor's `job_key` was held by no eligible row of
any other unit or free source: without this unit that job would be absent from
the result. The removal counterfactual is exact for set membership because
nothing in the chain is cross-row except dedupe; rank order would shift.

The fixture (`Attribution`, 10 tests) exercises every rule against the engine's
real output: two paid units with overlapping jobs, a job in paid and free, a
duplicate native id, a duplicate `job_key` under a different native id, a
retitled native id, stale, unreachable, hard-filtered, positive and
non-positive rows. `test_final_positions_are_exactly_the_rows_the_user_got`
asserts that every `F` in a trace is a CSV row with that `search_query` and
`search_rank`, and nothing else is.

**Native identity for paid rows is not captured** (VERIFIED): LinkedIn's job id
lives only in the URL, `normalize` keeps no native field, and `job_key` ignores
it. So a retitled posting is two jobs — as it is to the engine
(`test_a_retitled_native_id_is_a_new_job_as_job_key_says`).

**`--only-new`** filters after `finalize`, so under it "final" means "survived
finalize", not "shown". The worker never passes it.

## 9. Where a unit's time goes — MEASURED

Local monotonic clocks around work the engine already does:

| Unit | Actor runtime (provider) | Unit wall | Start POST | Wait (polls, of which GETs) | Finish → seen | Dataset read + normalize | Account read | Checkpoint | Beyond runtime |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A1 Backend Developer @ India | 22.32 s | 28.47 s | 0.60 | 26.63 (5, 1.59) | 4.38 | 0.90 | 0.28 | 0.06 | 6.15 |
| A2 Full Stack Developer @ India | 34.23 s | 39.29 s | 0.58 | 37.49 (7, 2.45) | 3.34 | 0.64 | 0.47 | 0.12 | 5.06 |
| B React Developer @ Remote | 15.92 s | 23.56 s | 0.56 | 21.43 (4, 1.41) | 5.59 | 1.10 | 0.44 | 0.03 | 7.64 |
| C0 Software Engineer @ Bengaluru | 39.61 s | 47.10 s | — | (8) | — | — | — | — | 7.49 |

"Finish → seen" is the timestamp of the terminal-poll cost reading minus the
provider's `finished_at` (two clocks; skew UNKNOWN, NTP-synced laptop).

- **The polling interval is the largest term**: 3.3–5.6 s of 5.1–7.6 s. With a
  5 s sleep plus a 0.32–0.35 s GET, a finish is seen 0–5.35 s late, ~2.7 s on
  average (INFERRED); these three drew the late half.
- **Start POST ~0.6 s; provider `started_at` precedes its return by ~0.3 s**, so
  container start is inside the provider's runtime, not the engine's overhead.
- **The dataset read is 0.6–1.1 s for 15 rows**, normalization included.
- **C0's 7.5 s**, decomposed by the same arithmetic: 8 polls ≈ 40 s of sleep +
  ~2.7 s of GETs against a 39.8 s actor life, ~0.6 s start, ~0.9 s dataset,
  ~0.4 s account (INFERRED; C0 had no clocks). The remaining ~2.5 s is UNKNOWN.
- **Checkpoint time grows with the sweep.** 0.03–0.12 s for ≤30 rows here; the
  offline 90-search replay spends ~226 s of CPU in checkpoints (§20), because
  each re-finalizes every row so far. Production now measures it per unit.

Polling was not changed.

## 10. Cost observations — separate, provisional, never guessed

`units[].cost_observations`: `{source, usd, observed_at, final}`. `final` comes
from a fixed table (`telemetry.COST_SOURCES`), not from the caller:

| Source | What | Final | Taken by |
|---|---|---|---|
| `run_record_at_completion` | `usageTotalUsd` on the `Run` the terminal poll returned | **false** | production (recorded before the status check, so a failed run keeps it) |
| `account_usage_delta` | month-to-date account usage, before vs after the unit | **false** | production, only when the account is readable |
| `run_record_settled` | the same run record, re-read until unchanged twice | **true** | research probe only |

A missing reading is **absent, never 0** (`test_a_missing_reading_is_absent_not_
zero`, mutation O). V2-A's `reported_cost_usd` and `billed_delta_usd` keep their
meanings. Note the pre-existing asymmetry: `reported_cost_usd` reads **0** when
the run reports nothing (`float(run.usage_total_usd or 0)`); C1's list records
nothing. The configured ceiling (`max_total_charge_usd`) and the
provider-recorded one (`provider_ceiling_usd`) are bounds, never costs.

**What C1 measured** (every value a live reading; seconds after the provider's
`finished_at`):

| Run | Terminal poll: run record | …charged events on that same `Run` | Account delta at the engine's read | Run record re-reads | Settled | Account covered its runs |
|---|---|---|---|---|---|---|
| A1 `ZdC0fF33XiJEEiarL` | **$0.00205** (+4.4 s) | 5 results + start | **$0.00005** (+5.6 s) | $0.03005 at +46 s (first re-read) | $0.03005 | — |
| A2 `yc6JeY2hICKz3Bq2J` | $0.02805 (+3.3 s) | 14 + start | $0.058061 (+4.4 s) — A1's late $0.030 plus $0.028 of A2 | $0.02805 at +6.1 s, **$0.03005 at +21.4 s** | $0.03005 | A (both runs, $0.0601): $0.058111 at +6.4 s, **$0.060111 at +21.7 s**; +$0.000039 beyond at +487 s |
| B `Cgm4Bo5KFEtQqVFXg` | **$0.01005** (+5.6 s) | 12 + start | **$0.00005** (+7.1 s) | $0.02405 at +7.9 s, **$0.03005 at +23.0 s** | $0.03005 | $0.00005 at +8.2 s, $0.02405 at +23.3 s, **$0.030059 at +38.6 s**; +$0.000009 at +68 s |
| C0 `QthNSmbc1XE5pB7o9` | $0.02805 | — | $0.01405 | $0.03005 at ~+2 min | $0.03005 | — |

Every settled charge equals the charged events at the run's own published prices
(`events_priced_usd` = $0.03005 in all three) and 15 × $0.002 + $0.00005.

**Reading it.**

- The terminal-poll figure is a **lower bound, 7–93% short** (n=4). Even the
  event counts on that same object lag (5, 12, 14 of 15).
- The account delta **lags the run record by ~15 s** (B: it reached $0.02405 at
  +23 s, the value the run record had shown at +8 s) and is **not attributable
  to a unit**: A2's "delta" was mostly A1's charge arriving late.
- After covering its runs, the account kept rising slightly: **usage that is no
  run's** (INFERRED: storage or transfer accrual; the cause is UNKNOWN). An
  account delta can therefore exceed the runs it covers.
- The provider recorded the configured ceiling on every start (`provider_ceiling_
  usd` = 0.046 in all three), read off the start response the engine already
  holds.
- **The budget guard's view after A1 was $0.00005** for a search that cost
  $0.03005: on C0's and C1's evidence, the pre-run check sees roughly one run
  behind (VERIFIED in code, MEASURED here).

The probe's re-analysis note: probe A's convergence criterion was written as an
exact match and reported "never matched"; the corrected criterion ("covers")
was applied to the recorded readings afterwards, without a provider call
(`reanalysed` in the evidence).

## 11. No extra production request

Everything C1 records in production is a local clock or a field of an object
the engine already holds: the start response (`options.max_total_charge_usd`),
the terminal `Run` (`usage_total_usd`, `charged_event_counts`,
`stats.run_time_secs`, `build_number`), and the account read the budget guard
already makes. The final run record needs a later GET, so **production never
records it**; the probe does. `test_the_same_requests_in_the_same_order`
compares the full request log (`start`, `get`, `dataset`, `limits`, `abort`)
with telemetry on and off; mutation G (one extra `rc.get()` under telemetry) is
caught.

## 12. Privacy

`paid_units` and `paid_summary` hold ids, provider and actor names, depths,
ceilings, a 12-hex query fingerprint, statuses, counts, stage names and trace
tokens. No title, company, description, URL, token, résumé text or query text
(`test_no_row_text_url_or_token_in_the_record` plants markers in every row
field; `test_the_new_sections_hold_no_query_text`; mutation H). V2-A's unit
record already carried the keyword and location — the console prints them —
and C1 did not widen that. The added unit fields are the allowlisted
`PAID_RUN_FIELDS` plus `cost_observations` (`test_every_new_key_is_known`).

Probe evidence adds only developer-chosen generic queries (`shape`) and the
account **variable name** (`APIFY_TOKEN`); the probe checks every configured
token's value against its output before writing.

## 13. Schema

The record's `schema` stays **`search-v2a.1`**: every change is additive and no
existing field changed meaning — V2-A's convention (B3 and B5 kept it for the
same reason). The new sections are versioned **`search-v2c1.1`**
(`paid_summary.schema`). `stages`, `identity`, `units[]`'s V2-A fields and B3's
sections are unchanged in shape; B3's `production_view` comparison passes as
before. A free sweep's record has no paid section at all
(`test_a_free_sweep_has_no_paid_sections_and_the_same_output`). A reader of an
older record gets `[]` from `paid_view` (`test_an_older_record_still_reads`).

Size: an executed paid unit adds 0.72 KB to its `units[]` record and ~1.0 KB of
`paid_units` entry; the 90-search replay's record is **285 KB** (§20). Execution
facts are not copied into `paid_units` — readers join on `unit_id`.

## 14. Production behaviour equivalence

With telemetry on vs off, through `scraper.main()` against a scripted Apify
stand-in (`ScriptedClient`, which logs every request):

| Property | Pinned by |
|---|---|
| same requests, same order, same count | `test_the_same_requests_in_the_same_order` |
| same actor input and `max_total_charge_usd` (Decimal 0.046) | `test_the_same_actor_input_and_ceiling`, `test_the_b1_contract_with_telemetry_on` |
| same CSV, JSON and seen ledger bytes | `test_csv_json_and_seen_ledger_byte_identical` |
| same budget decision at the same unit | `test_the_same_budget_decision` |
| nothing stamped with telemetry off | `test_nothing_is_stamped_with_telemetry_off` |
| a broken hook cannot fail the sweep | `test_a_hook_that_breaks_cannot_fail_the_sweep` |
| 90-search replay: identical output hash across every run of both arms | §20 |

No retry exists in the paid path beyond the SDK's own (4 retries, not
observable from outside; their time is inside the measured request times), and none was
added.

## 15. The probe — extended, still behind C0

`bench/search_v2_paid_probe.py` still launches the engine only through
`paid_guard.engine_argv` (`guard_argv`), so both keys and the exposure limit
are C0's. Added:

- `--case/--scope`: a synthetic cohort from `paid-plans.json`, rendered by
  `make_profile.render` the way the worker renders a visitor's profile. No real
  résumé. `--location` takes several places.
- A zero-cost preview is simply the same command without the keys.
- After the run, free GETs only, and only when both keys were present: each run
  record and its account on a bounded schedule (0, 15, 30, 60, 120, 240, 480,
  900 s after the engine exits, capped by `--observe-s`), stopping early once
  every reading has held still twice with the account covering its runs; then
  the account's run list since the probe began.
- Evidence: the engine's own `paid_units`, joined to execution by `unit_id`;
  every cost reading with its timestamp and seconds after finish; the settled
  charge; the charged events at the run's published prices; anomalies.
- `--summarize`: ranges and yield by position across evidence files.

It is not a second engine: rows, funnel and trace come from the child's
telemetry record, not from the probe reading datasets.

## 16. The research ledger

`docs/search-v2-evidence/paid-research-ledger.json`: per live start — stage,
time, provider, actor, purpose, starts, depth, ceiling per start, **intended
maximum decided before the call**, settled actual, running totals, evidence
file, status. No token, no row content (`test_totals_add_up_under_the_ceiling_
and_hold_no_secret`).

**Never authorisation.** `guard_argv` takes nothing from it
(`test_the_ledger_cannot_change_what_the_guard_is_given`, mutation J). It can
only **refuse**: when `--exposed-usd` is below the intended exposure it
already records, or `--max-usd` exceeds its $2.00 ceiling — before a profile is
written or a process started (`test_a_refused_probe_writes_nothing_and_starts_
nothing`). `paid_guard`, `scraper` and `telemetry` never read it. Its running
totals include C0, which makes the C1–C4 check stricter, never looser.

## 17. Live experiments — preflights and accounting

Policy as in the brief: C0's two keys on every call; worst case decided before
the call; a tight per-invocation `--max-usd` so the guard itself refuses one
more start; a zero-cost preview first; LinkedIn only (Indeed and Naukri have no
provider ceiling and were instrumented offline only); stop once answered.

**Before every call** (the full statements are in the session record and the
evidence `purpose` fields):

| | Probe A | Probe B |
|---|---|---|
| Unknowns | Q1 runtime spread, Q2 yield vs depth, Q3 time beyond runtime, Q4–5 per-position fate, Q6 overlap of two related shapes, Q7–8 cost convergence | Q6 remote shape (half the default LinkedIn plan is `Remote`) vs place, a third runtime point, a clean single-run convergence window |
| Why not offline | historical logs have no timings or positions; fixtures have no provider latency, billing lag or real result mix | remote-shape composition under LinkedIn's AI conversion exists only at the provider (B1 UNKNOWN #3) |
| Actor | `curious_coder/linkedin-jobs-scraper` | same |
| Queries (synthetic cohort `software_fullstack`) | "Backend Developer", "Full Stack Developer" @ India; scope India onsite/hybrid | "React Developer" @ Remote (`f_WT=2` in India); scope remote |
| Input (verified offline via `--dry-run`) | `limitPerSource` = `count` = 15, `scrapeCompany` false, `f_E=3`, `f_TPR=r1209600` | same, plus `f_WT=2` |
| Depth / ceiling | 15 / $0.046 per start, provider-enforced | 15 / $0.046 |
| Starts | 2 | 1 |
| Intended before → after | $0.046 → $0.138 | $0.138 → $0.184 |
| `--max-usd` | $0.14 | $0.19 |
| ≤ $2.00 | yes | yes |

**Accounting.**

| Call | Stage | Provider | Starts | Depth | `maxTotalChargeUsd` | Intended max | Cumulative intended | Actual (settled) | Cumulative actual |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| C0 contract check | C0 | LinkedIn | 1 | 15 | $0.046 | $0.046 | $0.046 | $0.03005 | $0.03005 |
| Probe A | C1 | LinkedIn | 2 | 15 | $0.046 each | $0.092 | $0.138 | $0.0601 | $0.09015 |
| Probe B | C1 | LinkedIn | 1 | 15 | $0.046 | $0.046 | **$0.184** | $0.03005 | **$0.1202** |

**C1: 3 starts, $0.138 intended, $0.0902 actual** — 6.9% of the shared C1–C4
ceiling ($0.184, 9.2%, counting C0). The run listings found exactly the probes'
own runs on `APIFY_TOKEN` and none on `APIFY_TOKEN_2`: no unexpected provider
activity. Everything else — previews, dry runs, re-reads, account reads, run
listings — was a free GET or no request at all.

## 18. Anonymized traces

| Unit | Trace (positions 1→15) |
|---|---|
| A1 Backend Developer @ India | `H.n. F+n. F-n. F-n. F-n. F+n. H.n. F-n. F+n. F-n. F-n. F+n. F+n. F-n. F-n.` |
| A2 Full Stack Developer @ India | `F+n. F+n. F-n. F+n. F-n. F+n. F+n. F+n. H.p. H.n. F-n. D+p. F+n. F+n. F+n.` |
| B React Developer @ Remote | `H.n. F+n. F+n. F+n. F+n. F+n. F+n. F+n. F+n. H.n. H.n. F+n. F+n. F+n. F+n.` |

Enough to replay, offline, what a depth of 5 or 10 would have kept, which
positions repeat other units, and which positions score. No hashes were
stored: overlap within a sweep is measured by the engine itself (`p`), and
cross-sweep identity was not a C1 question.

## 19. Across the probes — n = 3 units, 45 rows (plus C0 where it compares)

Descriptive of these three searches, one synthetic cohort, one evening
(2026-09-23 18:50–19:02 UTC). **Not a provider population.**
[Summary](search-v2-evidence/c1-cross-probe-summary.json).

| | Range (C1) | With C0 |
|---|---|---|
| Actor runtime | 15.9–34.2 s | 15.9–39.6 s |
| Unit wall | 23.6–39.3 s | 23.6–47.1 s |
| Rows returned / requested | 15/15 every time | 15/15 (4 of 4) |
| Stale | 0 in every unit | 0 |
| Eligible / returned | 80–87% | C0 used the default config; not comparable |
| Positive (eligible, score > 0) / returned | 33–80% | — |
| Final / returned | 80–87% | — |
| Duplicate (acquired repeat) / returned | 0–13% | — |
| Settled cost | $0.03005 every time | same |
| Cost per returned row | $0.0020 | $0.0020 |
| Cost per eligible row | $0.0023–0.0025 | — |
| Cost per positive row | $0.0025–0.0060 | — |
| Cost per final row | $0.0023–0.0025 | — |

Per unit: A1 13 eligible / 5 positive / 13 final; A2 13 / 10 / 12 (one lost to
A1, two acquired repeats of A1); B 12 / 12 / 12 (all 12 India-restricted remote
rows, kept by `keep_restricted_if_hires_home`). Removals were hard filters
only: 2, 2 and 3 rows.

**Yield by position**, all three traces:

| Positions | Rows | Stale | Eligible | Eligible positive | Final | Acquired repeats |
|---|---:|---:|---:|---:|---:|---:|
| 1–5 | 15 | 0 | 13 | 8 | 13 | 0 |
| 6–10 | 15 | 0 | 11 | 9 | 11 | 1 |
| 11–15 | 15 | 0 | 14 | 10 | 13 | 1 |

## 20. Instrumentation overhead — MEASURED offline

`bench/search_v2_paid_overhead.py`, 2026-09-24 00:42 IST.
[Evidence](search-v2-evidence/c1-paid-telemetry-overhead.json). A 90-search
paid plan (9 keywords × 5 places × LinkedIn and Indeed — the repository
defaults' shape), 15 synthetic rows per search, through `scraper.main()`
against `ScriptedClient` with sleeps patched out; each arm a fresh child
process, arms alternating, three runs each.

| Arm | Wall (median) | CPU (median) | Peak RSS (median) | Runs (CPU) |
|---|---:|---:|---:|---|
| telemetry off | 225.20 s | 224.57 s | 64.0 MiB | 227.0, 224.1, 224.6 s |
| telemetry on (V2-A + C1) | 225.35 s | 224.79 s | 62.4 MiB | 224.9, 224.8, 223.6 s |
| difference | +0.15 s (+0.07%) | +0.22 s (+0.10%) | none measurable | |

- **Outputs identical across all six runs** (one SHA-256 over CSV, JSON and
  seen ledger); 453 final rows; 90 starts in each run.
- **The difference is inside the noise**: the off arm alone spans 224.1–227.0 s
  of CPU. Read it as "no measurable overhead", bounded above by ~0.3%.
- **Peak RSS**: per-run spread 60.0–66.3 MiB in both arms; no difference.
- **Record**: 284,554 bytes for 90 executed units (~3.2 KB per unit, of which
  C1 is ~1.7 KB: 0.72 KB of fields in `units[]` and ~1.0 KB of `paid_units`
  entry).
- **The denominator is the finding.** ~225 s of local CPU for 90 searches is
  the engine re-finalizing every accumulated row after every search (~61,000
  row-visits; ~3.7 ms each here). In a real sweep this sits serially between
  actor runs, on top of ~40 s of actor wait per search. Synthetic descriptions
  are ~1.6 KB; real ones are longer, so production's figure is likely higher
  (INFERRED). `checkpoint_ms` now measures it per unit in production.
- Two live probes ran during part of this benchmark (their processes were idle,
  waiting on the provider); arms alternated, so any effect fell on both.

## 21. Tests

`sweep/tests/test_paid_observability.py` — **51 tests**. `scraper.main()`
in-process against `ScriptedClient` (every request logged; `_require_token`
patched; sockets denied); free rows served through B3's offline harness.

| Brief | Group / test | Pins |
|---|---|---|
| 1, 13, 24 | `BehaviourEquivalence` (6) | requests, input, ceiling, bytes, budget decision, no stamping when off |
| 2 | `UnitIdentity` (3) | ids in plan order across sites; deterministic ids and fingerprints; an id without a run |
| 3–7, 9, 10 | `OneUnit` (5) | clocks and `Run` fields; failed run (no rows, no invented cost, no account reading); zero results; the funnel of every exit; agreement with the printed stale count |
| 8, 11, 12 + fixture | `Attribution` (10) | F ↔ CSV rows; one job counted once; who beat each loser; tie to the earlier unit; higher score wins across units; free twin wins; marginal; acquisition classes; retitled native id; counts equal the trace |
| 14 | `Privacy` (4) | markers absent; no query text in new sections; known keys; the probe's join |
| 15–17 | `CostObservations` (4) | separate sources (C0's own numbers); nothing production records is final; finality not the caller's; missing is absent |
| 18–19 | `Summary` (2) | planned/attempted/completed/failed/skipped; per-provider aggregation and exposure |
| 20, 22, 25 | `Compatibility` (5) | B1 contract; free sweep unchanged with no paid sections; schema and V2-A fields; older records; a broken hook |
| 21, J | `ProbeStaysBehindTheGuard` (5) | keys and limits from the command line only; the ledger changes nothing the guard gets; refuse-only; refusal writes and starts nothing; nothing that decides reads it |
| ledger | `ResearchLedger` (2) | totals, ceiling, no secret or row field, evidence files exist; running totals |
| probe logic | `ProbeReadings` (5) | settled = unchanged twice; covers-not-equals; early stop; bounded window; summary bands and ratios |
| 23 | existing suites | B1 32, C0 45, B3, B4, B5, telemetry — unchanged, all green in the full run |

## 22. Mutation checks — all fifteen caught

Each applied alone; four suites run (`test_paid_observability`,
`test_paid_contract`, `test_search_v2_telemetry`, `test_paid_dev_guard`);
reverted; SHA-256 of every touched file equal before and after. A–J are the
brief's; K–O extra. 64 s for the set.

| # | Mutation | Result | Among the failures |
|---|---|---|---|
| A | account delta collapsed into the run cost | **1 fail** | `test_run_record_and_account_delta_stay_apart` |
| B | provisional account delta marked final | **2 fail** | `test_nothing_production_records_is_final`, `test_finality_is_not_the_callers_to_choose` |
| C | unit provenance dropped before dedupe | **7 fail, 1 error** | `test_final_positions_are_exactly_the_rows_the_user_got`, `test_a_tie_goes_to_the_earlier_unit_as_dedupe_decides`, `test_marginal_means_no_one_else_had_it` |
| D | provenance written into the user CSV | **4 fail** | `test_csv_json_and_seen_ledger_byte_identical`, `test_the_provenance_key_reaches_no_output` |
| E | one final job credited to two units | **4 fail** | `test_one_final_job_is_counted_once`, `test_final_positions_are_exactly_the_rows_the_user_got` |
| F | actor input changed when telemetry is on | **4 fail** | `test_the_same_actor_input_and_ceiling`, `test_the_same_requests_in_the_same_order`, `test_the_b1_contract_with_telemetry_on` |
| G | an extra provider GET for telemetry | **2 fail** | `test_the_same_requests_in_the_same_order`, `test_the_same_budget_decision` |
| H | title, company and URL stored in telemetry | **1 fail** | `test_no_row_text_url_or_token_in_the_record` |
| I | the probe always passes `--allow-paid` | **2 fail** | `test_both_keys_and_both_limits_come_from_the_command_line` |
| J | the ledger used as budget state (`--exposed-usd` from it) | **1 fail** | `test_the_ledger_cannot_change_what_the_guard_is_given` |
| K | a broken hook allowed to fail the sweep | **1 error** | `test_a_hook_that_breaks_cannot_fail_the_sweep` |
| L | every survivor called marginal | **1 fail** | `test_marginal_means_no_one_else_had_it` |
| M | repeat-in-unit checked after repeat-of-earlier-paid | **2 fail** | `test_repeat_work_is_visible_before_any_filter` |
| N | a failed unit given a zero funnel | **1 fail** | `test_a_failed_run_invents_no_rows_and_no_cost` |
| O | a missing cost reading recorded as 0 | **2 fail** | `test_a_missing_reading_is_absent_not_zero` |

## 23. Unresolved risks

1. **Three searches are not a distribution.** One cohort, one evening, one
   account tier. Runtime, yield and positivity will vary by query, place, hour
   and LinkedIn's AI conversion; the ranges in §19 are these runs'.
2. **Settling is observed, not promised.** `run_record_settled` is final by
   stability (unchanged across ≥ 2 re-reads); Apify publishes no finality flag
   (UNKNOWN whether a record can move after minutes).
3. **The account residual's cause is UNKNOWN** (+$0.000009 to +$0.000039 beyond
   the runs, still rising slowly at +487 s). Negligible in dollars; it means an
   account delta can never be equated with a run's cost.
4. **No live failure was observed.** What a run aborted at its ceiling, timed
   out or refused returns (partial dataset? charge?) is UNKNOWN (B1 UNKNOWN #5
   stands); C1 records whatever the terminal `Run` says, offline-tested only.
5. **Indeed and Naukri** are instrumented identically but were not run: no
   provider ceiling, so no defensible bound; their live behaviour and cost
   convergence are UNKNOWN.
6. **The clocks compare two machines** for "finish → seen" and all
   seconds-after-finish figures; skew is UNKNOWN (laptop NTP).
7. **Record size grows ~1.7 KB per executed paid unit**; a 90-search sweep's
   record is 285 KB. Fine for a 48 h diagnostic; worth watching if plans grow.
8. **`positive` is score > 0 under a synthetic cohort's weights**, not relevance
   to a real person.
9. **The traces are for LinkedIn at depth 15**; they say nothing about depth
   beyond 15 or about Indeed's ordering.

## 24. For C2 — accounting and concurrency

- **Trust immediately:** the configured ceiling, and the provider's own record
  of it on the start response (`provider_ceiling_usd`, equal to the configured
  $0.046 on every start). It is the only hard number available at start time.
- **Lags:** the terminal-poll run record (7–93% short, n=4) and, further, the
  account delta (at +5–7 s it showed the $0.00005 start event alone for two of
  three runs; it trails the run record by ~15 s and is not attributable to a
  unit).
- **Convergence:** run record settled between +6 and +23 s after finish (A1 by
  its first re-read at +46 s); the account covered its runs by +22–39 s.
- **Reserve the full provider ceiling while a run is active.** Nothing lower is
  knowable before ~20 s after it ends.
- **Release/reconcile** when the run is terminal *and* its run record has held
  still across two re-reads — observed ≤ 23 s; a reconciliation re-read at
  ~+30 s, with a +60 s fallback, would have settled every run here (INFERRED).
  Reconcile to the **run record**, not the account; use the account only as an
  audit that it covers the sum. Release `ceiling − settled`.
- **On failure** the engine holds: run id (if the start returned), status,
  terminal `usageTotalUsd` and charged events. It does **not** read the account
  on the failure path, so today a failed run's charge reaches the guard only via
  the next success's cumulative delta — and never when the account is
  unreadable (fallback sums successes only). A reservation system should hold
  the ceiling for failed runs until their record settles too.
- **When cost is not final**, C2 has: the reservation (ceiling), the provisional
  run record and its event counts, and the time since finish.
- **Concurrency:** the account delta cannot attribute per run even serially
  (A2's delta was A1's); with concurrent runs only per-run records can.
- The checkpoint cost (§20) is serial local CPU between starts that grows with
  the sweep — relevant to any concurrency design, which should not multiply it.

## 25. For C3 — plan compaction

- **Survival to eligibility was high**: 80–87% of returned rows (n=3); losses
  were hard filters only (2–3 rows per 15). No stale rows: `f_TPR` holds.
- **Positive/final**: 5, 10 and 12 positive rows per 15; 12–13 final.
- **Duplicate waste was small here**: 2 of 15 rows between two related India
  searches in one sweep (13%), 0 elsewhere; one final job lost to the other
  search. `final_marginal` was 12, 12 and 12: nearly everything each start
  contributed, only it contributed. Production records now carry `acquired`
  and `dedupe_lost` per unit across real multi-keyword sweeps, where overlap is
  likely larger (18 LinkedIn + 72 Indeed at defaults).
- **Shape mattered for positivity, not for survival**: the remote React search
  gave 12/15 positive, the two broad India searches 5 and 10. Three searches
  cannot say which shape wins in general.
- **No evidence yet of many low-value starts**: each start returned 15 rows,
  ~13 of them eligible and mostly exclusive. Start fees are negligible
  ($0.00005); the cost is per row. Compaction's value is more likely latency
  (a start is ~23–47 s of wall) than dollars.

## 26. For C4 — adaptive execution

- **Useful yield did not decay by position**: final rows 13 / 11 / 13 and
  positive 8 / 9 / 10 across positions 1–5 / 6–10 / 11–15.
- **Marginal value of 5 → 10 → 15 was flat here**: a depth of 5 would have kept
  about 13 of 37 final rows, 10 about 24 (approximate: at a shallower depth a
  different row can win dedupe).
- **Not enough to justify reducing depth; enough to justify measuring it.**
  The production trace now records exactly this per unit, so real sweeps can
  answer it without a paid experiment.
- **State C4 would observe during execution**: per unit, as it completes —
  rows returned vs requested, the trace (eligible/positive/final by position),
  acquisition repeats against earlier units, `final_marginal`, the unit's wall
  and wait time, the reservation outstanding, and the provisional vs settled
  cost. All of it exists after the unit's checkpoint; the funnel for *earlier*
  units can still change when later units' rows win dedupe, so a decision taken
  mid-sweep should read the checkpoint's view, not the final one.

## 27. Verification

- `python -m unittest discover -s sweep/tests -t .` — **1,238 tests, OK** (1,187
  before, +51), 180 s; also run once before the probe's final edits: 1,237, OK.
- `python -m unittest deploy.test_sweep_worker deploy.test_modal_benchmark` —
  **42 tests, OK**.
- `python scraper.py --demo`, `python telemetry.py` (its flag-off inertness
  check now includes every C1 entry point), `python -m sources`,
  `python -m sources.concurrency` — pass.
- `auto-apply` suite — 1,102 tests, 2 errors in `test_inference` healthz (a local
  inference service answering 503); **the same two errors reproduce on a clean
  worktree of `89c8daa`**, as for B3, B5 and C0 — environmental.
- Mutation checks — 15/15 caught; every touched file restored byte-identical.
- Overhead — §20: identical output in all six runs; +0.1% CPU, inside noise.
- `git diff 89c8daa -- config.py deploy/ sweep/app.py sweep/runs.py sweep/plan.py
  sweep/worker_link.py sources/ rescore_from_apify.py auto-apply/ requirements.txt
  render.yaml gunicorn.conf.py bench/paid_guard.py` — **empty**. Changed
  production code: `scraper.py` (+91 / −4 lines: `paid_unit`, hooks and clocks) and
  `telemetry.py` (+351 / −14: the C1 section). No flag, environment or deployment change.
- **Paid:** three LinkedIn starts in two probes, §17. Each preceded by a
  zero-cost preview and an offline `--dry-run` of the exact input; both keys on
  the command line; tight `--max-usd`; run listings show no other activity.
- Temporary probe profiles were removed (none left in `profiles/`); outputs
  lived in temp directories and were deleted.

## 28. Rollback

Nothing to roll back in production: the flag is V2-A's, and with it off every C1
path is a single `is None` test. Reverting the commit removes the paid sections;
records already written stay readable (they are additive).
