# V2-D — Search V2, finalised: several Apify accounts per visitor, a full sweep or an explicitly chosen part of it, and the production configuration

Date: 2026-09-24. Baseline **`a87e3c9`** (V2-C5), verified as HEAD with a clean
tree before anything changed. Reviewed history present: C0 `89c8daa`, C1
`a3b4bd2`, C2 `429b7b6`, C3 `61681c9`, C3.5 `ccd5720`, C4 `22a89a2`, C4.5
`51764a0` + `b6a974b` + `8c430ae`, C5 `a87e3c9`. **No paid provider call was
made in V2-D. Nothing was deployed and nothing was pushed.** Commits: `e269e80`
(D1), `3a9445f` (D3/D4), `c064825` (D5/D6), and this record with the mutation
evidence (D7).

This is Search V2's final technical decision record. It summarises A–C5 only
where a D decision rests on it. Evidence classes as in V2-A: **MEASURED** (a
reproducible experiment or a dated reading), **VERIFIED** (read in code, or
pinned by a test that fails when it stops being true), **INFERRED** (reasoned,
not observed), **UNKNOWN**.

**V2-D status: COMPLETE.** Every D section reached an evidence-backed decision;
the full regression is green apart from two known environmental errors
reproduced on the pre-D baseline (§7.3); 28 of 28 mutations are caught.

## 0. Decisions at a glance

| area | decision | class |
|---|---|---|
| D1 multiple keys | a visitor may connect up to 20 of their own Apify accounts; one pool, two credential sources; public keys reach the engine on stdin only | VERIFIED |
| D1 affordability | full = exact placement of every provider ceiling on the connected accounts, inside the generated cap, re-read just before the first start; never the estimate, never summed balances | VERIFIED |
| D1 partial sweep | only when the visitor ticks "Run with my available credit anyway" (the console's existing over-cap box, reused): the longest safely placeable prefix, in plan order; the rest `skipped_insufficient_capacity`, never done | VERIFIED |
| D1 full mode short of credit | zero paid starts, exit 3, "Your Apify credit changed" | VERIFIED |
| D2 paid engine | C2 on, 2 workers, the AccountPool (`SWEEP_PAID_MULTI_ACCOUNT=1`) as the production engine | MEASURED (C5) |
| D2 adaptive | off; PROMOTED empty | MEASURED (C5) |
| D2 LinkedIn depth | 15 | MEASURED (C5) |
| D3 Indeed locations | **unchanged** (outcome B); Gurgaon + Noida flagged, single-source | INFERRED |
| D4 polling | LinkedIn 5 s; **Indeed 2 s** | INFERRED (simulated, calibrated on C5) |
| D5 Postman | removed (nine 404s, no verified replacement) | MEASURED |
| D5 free concurrency | unchanged (Lever/Greenhouse 4); the 8.8 min was one slow Lever board | MEASURED |
| D5 B3 shadow boards | promote none; keep shadow | MEASURED |
| D6 native identity | diagnostic-only | MEASURED |
| D6 telemetry | core on, adaptive off publicly, pool telemetry when the pool runs | VERIFIED |
| D7 flags | no new configuration flag; the production env in §7.1 | VERIFIED |

Corrections to C5 found by D3 (C5's figures otherwise reproduced): removing
all Remote twins loses 13–14 finals, not 10; the Indeed plan is 7 India places
+ Remote, not 8 + Remote; removing Indeed loses 19 finals, not 20.

## 1. D1 — public BYOK: several keys, exact affordability, an explicit partial sweep

### 1.1 The public BYOK path as it was (VERIFIED, read end to end)

| question | answer before V2-D1 |
|---|---|
| where the token is entered | `/key` (`key.html`, field `token`), POST `/key` → `key_post` |
| a "run anyway" option? | yes — Confirm's `over_cap_ack` checkbox, "Start it anyway": shown when the **estimate** exceeds the best single key's credit; the engine then ran until the account refused |
| browser → Render | the form POST body; never returned to the browser, never in the cookie |
| Render → worker | `worker_client.hold_token` → `POST /v1/tokens {owner, apify_token}` (bearer auth; owner = HMAC of the session id) |
| persisted? encrypted? | never at rest anywhere, so nothing to encrypt: Render keeps a number (`cap_usd`), the worker keeps the token in process memory keyed by owner (45 min TTL), a restart drops it |
| session-scoped | yes: one held key per owner, overwritten by a second paste |
| in the generated profile | no |
| in process environment | **yes** — the worker put it in the child's `APIFY_TOKEN` (after stripping every `APIFY_TOKEN*` of its own) |
| passed to Oracle | yes (the worker runs there) |
| telemetry | never (C1's privacy tests) |
| validated when | POST `/key`, one `users/me/limits` read (`check_token`: limit − usage) |
| account identity read | never — no `users/me`, so no deduplication was possible |
| who knows the account | Configure/Confirm know a credit figure only; `POST /run` knows nothing; only the child uses the key |
| `_require_token()` publicly | `load_dotenv()`, then every `APIFY_TOKEN*` — so a `.env` in the worker's checkout **could have added operator slots beside the visitor's key** (latent; whether the Oracle checkout has one is UNKNOWN) |
| C5's open question | answered: the public app did **not** stop a $10.55-cap plan on a $5 FREE account — only the estimate was compared, and "Start it anyway" let it run until the provider refused |

The architecture could take several credentials without a security redesign:
the worker already held keys only in memory, per owner, and a list of handles
is the same thing. So D1 went ahead (the brief's STOP condition did not apply).

### 1.2 What changed

**One pool, two credential sources** (VERIFIED, `scraper.py`). The C4.5
AccountPool, its exact allocator and its two exposure ledgers are unchanged and
shared. What differs is only where keys come from:

| source | how the engine gets keys | reads `.env` / `APIFY_TOKEN*`? | developer clamp |
|---|---|---|---|
| developer | `_require_token_pool()` → `pool_tokens()` after `load_dotenv()`, as C4.5 | yes, as before | honoured (`SWEEP_PAID_ACCOUNT_CAP_USD`) |
| public BYOK | `byok_tokens()`: the worker pipes `{"apify_tokens": [...]}` on the child's **stdin** and sets `SWEEP_BYOK_CREDENTIALS=stdin`; read once, slots named `key_1`, `key_2`, … | **never** | **never** (engine ignores it; the worker strips it) |

Public keys never become environment variables, never reach the command line
or disk. `_require_token()` (the single-account engine) takes one BYOK key and
refuses several — only the pool spends across accounts. A malformed or empty
key list refuses the paid phase without echoing it.

**Worker** (`deploy/sweep_worker.py`). A visitor may hold up to `MAX_KEYS` =
20 keys (generous: a FREE account holds $5 and the default plan's ceilings are
$10.55, so three suffice; the limit exists because each key is two provider
reads before the first start, and held keys are memory). Each held key has an
opaque id — the only name Render ever has for it; the same key pasted twice is
one id. `DELETE /v1/tokens/<id>` forgets one. `POST /v1/runs` names the keys
that fund the run (`key_ids`); every one must still be held, or the run is
refused (409, ask again); they are **frozen** for that run — popped into the
child and released — so nothing a browser does afterwards can move an in-flight
sweep to other accounts. The child's environment loses every `APIFY_TOKEN*`,
`SWEEP_PAID_ACCOUNT_CAP_USD` and any inherited `SWEEP_BYOK_CREDENTIALS`. A run's
status now carries `paid_authorization`: only whitelisted counts, amounts and
words from the engine's record.

**Render** (`sweep/app.py`, `sweep/plan.py`, templates). Adding a key
(`POST /key`, from the key step or from Confirm's "Add another Apify key"):

1. format check, then `read_account`: `users/me` (who) and `users/me/limits`
   (how much) — the same two free reads, and the same arithmetic
   (`scraper._account_reading`: the smaller of the monthly limit and the
   plan's credit, less usage), the engine's pool uses;
2. an account the provider does not identify is refused;
3. the account is kept as a **keyed digest** of its id (HMAC under Render's
   secret) — a second key to the same account is refused with *"This key
   belongs to an Apify account already added."*, and no id, email or username
   is shown or kept;
4. the key goes to the worker, which returns its id; the session keeps the id,
   the digest, the headroom and the usable capacity
   (`scraper.usable_capacity`: headroom − $0.01), never the key;
5. Confirm re-prices: `plan.coverage` runs the engine's own allocator
   (`scraper.place_units` + `placeable_prefix`) over every connected account's
   capacity, inside the generated hard cap.

Removing one (`POST /key/forget`, public only) drops it from the session and
releases it at the worker; coverage recomputes. When a run starts the worker
has spent every held key, so the session forgets them too (and again if the
worker ever answers "no key held") — otherwise a re-pasted key would be
refused as a duplicate of nothing.

**Which engine is running is the worker's answer, not a Render flag.** The dry
run now reports `account_pool` (`SWEEP_PAID_CONCURRENCY` and
`SWEEP_PAID_MULTI_ACCOUNT` in the worker's own environment). With it, Confirm
authorises by placement and funds the run from every connected account; without
it (the rollback), Confirm keeps the console's estimate-over-credit gate and the
run is funded by the one account with the most credit, exactly as before.

### 1.3 Full or partial — the authorization (VERIFIED)

`AccountPool.authorize`, immediately after the pool re-reads every account and
before the first start:

- the not-yet-done searches in **plan order**;
- `placeable_prefix`: the prefix ends at the first search that has no provider
  ceiling, was not placed by the allocator, is placed on an account that can
  never run it (memory, run slots), or would take the held total past the
  sweep's `max_spend_usd`;
- **full** — the whole plan fits: it runs, as C5 did;
- **partial** — only if `SETTINGS["allow_partial_paid_sweep"]` is True: the
  prefix runs, nothing after it, even a later search that would fit;
- **refused** — anything else, and a prefix of nothing even when partial:
  **zero paid starts**, nothing written, the engine exits with
  `PAID_REFUSED_EXIT` (3), the running screen says *"Your Apify credit
  changed"*.

Three money numbers stay apart: the estimate is shown, the bounded exposure
(sum of ceilings) is what placement holds, the generated cap bounds it, and the
settled cost is what the provider later bills. Authorization never uses the
estimate (mutation A) or summed balances (mutation B). Every start still needs
its full ceiling in the global PaidExposure **and** its own account's, plus the
account's memory and a run slot — C2/C4.5 unchanged.

Searches a partial sweep leaves out are `skipped_insufficient_capacity` (or
`skipped_budget` when the sweep's own cap is what ends the prefix) — never
`failed`, never written to `.done_combos`. So a **same-day rerun** on more
credit runs exactly what was left (test 17). A **public** run cannot resume:
each has its own run directory on the worker, so a new public sweep is a new
plan (VERIFIED, `RunStore`).

The partial choice is the console's own over-cap checkbox (`over_cap_ack`),
reused rather than duplicated, relabelled publicly *"Run with my available
credit anyway"*. POST `/run` turns a ticked box on an over-cap plan into
`allow_partial_paid_sweep: True` in the rendered profile, and resets it on every
run. Nothing else sets it: not low credit, not a key's presence, not a small
`max_spend_usd`, not the normal Run button. On the single-account engine the
setting is inert and the box keeps its old meaning.

Recorded for every pooled sweep (telemetry `paid_execution.authorization`,
`paid_authorization.json` beside the outputs, the account ledger):
`full_plan_requested`, `full_plan_placeable`, `partial_authorized_by_user`,
`outcome`, `stop_reason`, `total_planned_paid_units`, `placeable_paid_units`,
`executed_paid_units`, `skipped_insufficient_capacity`, `skipped_budget`,
`total_planned_bounded_exposure_usd`, `placeable_bounded_exposure_usd`,
`executed_bounded_exposure_usd`, `full_estimate_usd`, `partial_estimate_usd`
(priced by `plan.cost` over exactly the prefix, never pro rata). No token, no
query.

Unchanged from C4.5 and stated for D1.22/23: the snapshot is the capacity; a
later reading only ever enlarges an account's observed spend, so external spend
can stop later starts and never re-opens authorization; assigned work never
migrates. An unbounded provider (Naukri) has no ceiling to place: it ends the
prefix, so no plan containing it is ever authorised as full. Naukri stays off.

### 1.4 The flag

`SWEEP_PAID_MULTI_ACCOUNT` becomes the **production execution-engine flag**: on
(under `SWEEP_PAID_CONCURRENCY`) it means "spend through the AccountPool". It
does not decide where credentials come from — the worker's per-run marker does —
so developer and public discovery stay separate with no new configuration flag.
`SWEEP_BYOK_CREDENTIALS` is not configuration: only the worker sets it, per
child, and strips an inherited one.

### 1.5 D1 test matrix (VERIFIED, `sweep/tests/test_search_v2_d.py`)

| # | test |
|---|---|
| 1 | `test_1_one_account_can_place_the_full_plan` |
| 2 | `test_2_no_single_account_but_the_pool_places_it` |
| 3 | `test_3_fragmented_capacity_is_not_the_full_plan`, `Coverage.test_3_fragmented_credit_is_not_coverage` |
| 4 | `ByokSource.test_4_two_keys_to_one_account_are_one_account` (+ C4.5 discovery) |
| 5 | `PublicMultiKey.test_5_7_8_another_key_turns_partial_into_full` |
| 6 | `WorkerKeys.test_6_one_key_can_be_released`, `PublicMultiKey.test_6_removing_a_key_recalculates` |
| 7 | `test_7_insufficient_and_partial_not_chosen_starts_nothing` (+ Render refuses `/run`) |
| 8 | `test_8_insufficient_and_partial_chosen_runs_the_prefix`, `PublicMultiKey.test_8_the_ticked_box_is_the_profiles_partial_setting` |
| 9, 10 | `test_9_10_the_exact_boundary_and_a_mill_below_it` |
| 11 | `test_11_ceilings_not_expected_spend_authorise_it`, `PublicMultiKey.test_11_the_estimate_fitting_is_not_the_plan_fitting` |
| 12 | `test_12_the_partial_estimate_is_of_the_selected_searches` |
| 13, 14 | `test_13_14_order_is_kept_and_nothing_leapfrogs`, `test_14_a_later_search_never_leapfrogs_one_that_cannot_run` |
| 15, 16 | `test_15_16_left_out_is_neither_failed_nor_done` |
| 17 | `test_17_a_rerun_with_more_credit_runs_only_what_was_left` |
| 18, 19 | `test_18_the_fresh_reading_decides_a_full_sweep`, `test_19_the_fresh_reading_sets_a_partial_sweeps_prefix` |
| 20, 21 | `test_20_an_unreadable_account_is_not_counted`, `test_21_nothing_readable_fails_closed_even_when_partial` |
| 22 | `PublicMultiKey.test_22_a_second_key_to_the_same_account_is_refused_safely` |
| 23 | `ByokSource.test_23_no_key_reaches_telemetry_the_log_or_an_output`, `PublicMultiKey.test_no_key_or_account_id_in_any_page_or_session`, the public suite's key-placement tests |
| 24 | `ByokSource.test_24_the_same_placement_as_the_developer_source` |
| 25 | `ByokSource.test_25_the_developer_source_is_untouched_without_the_marker` |
| 26 | the C4.5 suite, 86 tests (seven updated: a sweep the pool cannot fully place now needs the partial choice) |
| 27 | `ByokSource.test_27_…`, `WorkerKeys.test_27_…`, `PublicMultiKey.test_27_no_form_or_api_field_reaches_the_developer_clamp` |
| 28 | `ProfileAndCap.test_28_the_generated_cap_holds_the_default_plans_ceilings` |
| 29 | `ProfileAndCap.test_29_unset_is_config_false_and_only_a_yes_is_written` |
| 30 | the C0 suite (`test_paid_dev_guard`), unchanged and green |

The C4.5 tests changed on purpose: seven asserted that a pooled sweep the
accounts could not wholly hold ran its prefix and stopped. That is exactly the
silent truncation D1 forbids, so they now either ask for the partial choice
(and assert the new `skipped_insufficient_capacity` / `skipped_budget` labels)
or assert the refusal.

## 2. D2 — paid production consolidation

- **Paid concurrency: on, 2 workers** (C5 MEASURED: 90/90, 0 × 429, plan-order
  integration under 10 finish-order inversions, paid phase 12.7 min). The code
  default is already 2 (`PAID_WORKERS_DEFAULT`, pinned by a test — mutation M
  caught); `PAID_WORKERS_MAX` 4 stays for developer benchmarks only. 3 or 4 is
  not recommended: nothing live measured them.
- **Adaptive: off.** Every C4 policy that saved money lost top-10 jobs, some the
  #1 job; the section is ~120 KB compact per sweep. `paid_adaptive.py`, the
  replay tool and developer shadow stay; `PROMOTED` stays empty; the default is
  off (pinned — mutation L caught).
- **LinkedIn depth: 15.** Positions 11–15 held five of LinkedIn's seven top-10
  jobs in C5. Not reopened (depth reduction mutation N caught, 14 tests).

### 2.1 Every Search V2 flag (VERIFIED in code; production values are the recommendation of §7)

| flag | stage | code default | recommended production | class |
|---|---|---|---|---|
| `SWEEP_SEARCH_V2_TELEMETRY` | A | off | `1` | permanent production config (operational record) |
| `SWEEP_FREE_LEVER_CONCURRENCY` / `_WORKERS` | B2 | off / 4 (1..8) | `1` / `4` | permanent config; rollback switch |
| `SWEEP_FREE_GREENHOUSE_CONCURRENCY` / `_WORKERS` | B4 | off / 4 (1..8) | `1` / `4` | permanent config; rollback switch |
| `SWEEP_FREE_SOURCE_SHADOW` | B3 | off | `1` (unchanged) | research-only: the eight boards' natural evidence, fetched after the result is written |
| `SWEEP_RESULTS_READY_EARLY` | B5 | off | `1` | permanent config; rollback switch |
| `SWEEP_PAID_CONCURRENCY` | C2 | off | `1` | permanent config; rollback switch |
| `SWEEP_PAID_WORKERS` | C2 | 2 (1..4) | `2` | permanent config |
| `SWEEP_PAID_ADAPTIVE_MODE` | C4 | off | unset (off) | research-only (developer shadow) |
| `SWEEP_PAID_MULTI_ACCOUNT` | C4.5 → D1 | off | `1` | public product feature (the AccountPool engine); rollback switch |
| `SWEEP_PAID_ACCOUNT_CAP_USD` | C4.5 | unset | **never set** | developer-only (the worker strips it; BYOK ignores it) |
| `SWEEP_ALLOW_PAID_BENCH` | C0 | unset | never set on a server | developer-only (second key of the paid guard) |
| `SWEEP_BYOK_CREDENTIALS` | D1 | — | never configured | internal: set per child by the worker only |
| `SWEEP_RUN_ID`, `SWEEP_ENGINE_REVISION` | A | — | set by the worker / deploy | internal telemetry join keys |
| `SWEEP_EXPERIENCE_MISMATCH_GUARD` | pre-V2 | off | unchanged (off, as reviewed) | product setting, outside V2 |
| `SWEEP_PROFILE_ENGINE_VERSION` | profile | — | `v2` on Render (`render.yaml`) | product setting, outside V2 |

Obsolete or removable: **none**. Every switch above is either live
configuration, a rollback for it, or a developer/research tool that is off
unless a developer sets it. No new configuration flag was added in V2-D.

Paid concurrency without the pool (`SWEEP_PAID_CONCURRENCY=1`,
`SWEEP_PAID_MULTI_ACCOUNT` off) is **not** a recommended production state, and
neither is today's serial single-account engine for a public paid sweep: both
check each start against the sweep's cap only, never against the account's
credit — the path by which a $10.55-cap plan starts on a $5 FREE account and
runs until the provider refuses (§1.1). The pool engine, even with one key,
places every ceiling on an account before the first start.

## 3. D3 — Indeed structural redundancy (offline)

Evidence: [d3-indeed-location-analysis.json](search-v2-evidence/d3-indeed-location-analysis.json),
`bench/search_v2_d3_d4_analysis.py`. No paid call. C5 kept no per-row job key
for paid rows, only per-unit counts, one trace token per dataset position and
the engine's own exact losses for single searches, plan tails and Remote twins.
So a location's loss was bounded by enumerating every assignment of the 11
discarded eligible Indeed rows that fits the record's flags, plan order and
those exact losses (176,530 assignments → 49 location-level scenarios); figures
are **[min, max]** over them. The model reproduces the engine's own 438/438
tail losses and 432/432 single-search losses (VERIFIED).

**The plan is 9 keywords × 8 location values: seven India places and one Remote
twin** (C5 wrote "8 India locations + Remote" — corrected). Each location is 9
starts, $1.215 of exposure, ~$0.81 settled.

| location | returned | duplicate (in-search + earlier paid) | new | eligible | final | location-marginal | top-10 / 20 / 50 | best rank | settled |
|---|---|---|---|---|---|---|---|---|---|
| Delhi | 133 | 55 (2 + 53) | 78 | 2 | 1 | [0, 1] | 0 / 0 / 1 | 38 | $0.798 |
| New Delhi | 135 | 67 (8 + 59) | 68 | 0 | 0 | 0 | 0 / 0 / 0 | — | $0.81 |
| Gurgaon | 135 | 135 (8 + 127) | **0** | 0 | 0 | 0 | 0 / 0 / 0 | — | $0.81 |
| Noida | 135 | 135 (8 + 127) | **0** | 0 | 0 | 0 | 0 / 0 / 0 | — | $0.81 |
| Bengaluru | 135 | 47 (15 + 32) | 88 | 3 | 2 | 1 | 1 / 1 / 2 | 3 | $0.81 |
| Hyderabad | 132 | 72 (23 + 49) | 60 | 1 | 1 | 1 | 0 / 0 / 1 | 49 | $0.792 |
| Pune | 135 | 78 (25 + 53) | 57 | 2 | 2 | 2 | 0 / 0 / 0 | 140 | $0.81 |
| Remote | 130 | 55 (9 + 46) | 75 | 23 | 14 | [13, 14] | 1 / 2 / 6 | **1** | $0.78 |

(MEASURED per location; location-marginal INFERRED. C5's "644 duplicates" is
98 repeats within a search + 546 repeats of an earlier paid search.)

**Overlap.** New Delhi, Gurgaon and Noida return the same list: their traces
agree position for position on 9/9 keywords (135/135, chance 0.545), and every
Gurgaon and Noida row was a repeat of an earlier paid search (MEASURED; "same
list" INFERRED, strong). Delhi is not New Delhi (0/9 keywords agree). Every
other raw pair overlap is UNKNOWN (no per-row key was kept); eligible and final
overlap is 0 for every pair but Delhi–Remote (0–2 shared). Remote shares no
eligible job with any place on the same keyword (MEASURED).

**Counterfactuals** (savings: starts / exposure / estimate; losses INFERRED unless marked):

| removed | saves | loses |
|---|---|---|
| New Delhi, Gurgaon or Noida alone | 9 / $1.215 / $0.81 | nothing (exact) |
| Delhi | 9 / $1.215 / $0.81 | 0–1 finals (rank 38) |
| Bengaluru | 9 / $1.215 / $0.81 | 1 final, **rank 3** (top-10 −1) |
| Hyderabad / Pune | 9 / $1.215 / $0.81 | 1 (rank 49) / 2 (best rank 140) |
| Remote | 9 / $1.215 / $0.81 | 13–14 finals, top-20 −1 to −2, best lost rank **1** or 12 |
| Gurgaon + Noida | 18 / $2.43 / $1.62 | nothing (exact) |
| NCR to Delhi only (drop New Delhi, Gurgaon, Noida) | 27 / $3.645 / $2.43 | nothing (exact) — but New Delhi's 68 new rows were only ineligible *for this profile* |
| all of NCR | 36 / $4.86 / $3.24 | 0–1 |
| Remote twins only | 63 / $8.505 / $5.67 | 4–5 finals, top-10 −1, best lost rank 3 |
| Indeed entirely | 72 / $9.72 / $6.48 | 19 finals (not 20: one was also found free), top-10 −2, best lost rank 1 (MEASURED) |

**Correction to C5 (§9 of the C5 doc):** removing all nine Remote twins loses
**13–14 finals, 1–2 top-20 and possibly the #1 job**, not "10 finals, best rank
21, 0 top-20". The 10 was the sum of nine single-twin removals; the twins
duplicate each other across keywords, so removing them together loses more.
The $1.215 exposure was right.

**Decision: outcome B — the Indeed default plan is unchanged.** Gurgaon and
Noida are the only real candidates (0 new rows, the same list as New Delhi,
$1.62 settled and $2.43 exposure a sweep), but outcome A needs redundancy across
several evidence sources and this is **one run, one date, one synthetic
profile** — no earlier probe ran Indeed at an NCR location (C3.5 covered
Bengaluru only; `paid-plans.json` has plan shapes, no rows). Remote stays: it
is the most valuable Indeed search (14 of 20 finals, the #1 job). To reach A: a
second observation on a different keyword set or date showing Gurgaon and Noida
again add nothing after New Delhi; persisting a salted hash of each paid
search's job keys would make every overlap here exact.

## 4. D4 — polling (simulated)

Evidence: [d4-polling-simulation.json](search-v2-evidence/d4-polling-simulation.json).
The poll (VERIFIED, `scrape_search`): sleep, then one plain `RunClient.get()`
(not a long poll), 360 s deadline; the SDK's own per-request timeout is 5 s,
doubled per re-send, backoff 0.5 s × 2ⁿ with jitter, at most 4 re-sends. The
simulation replays C5's provider timestamps; at 5 s it reproduces C5's measured
detection lag — Indeed 1.674 / 4.516 / 19.431 s (median / p95 / max) against
measured 1.679 / 4.527 / 19.439 (0 of 72 poll-count mismatches); LinkedIn
2.208 / 4.938 / 5.034 against 2.419 / 5.062 / 6.32 (one unit, paid_005, off by
one poll); segment walls within 0.08 s.

| provider @ interval | lag mean / median / p95 / max (s) | polls a run | extra polls a plan | segment wall |
|---|---|---|---|---|
| LinkedIn 1 s | 0.90 / 0.78 / 1.63 / 1.67 | 23.0 | +301 | −12.3 s |
| LinkedIn 2 s | 1.26 / 1.28 / 2.10 / 2.39 | 13.6 | +132 | −10.4 s |
| LinkedIn 3 s | 1.97 / 2.01 / 3.43 / 3.46 | 9.8 | +64 | −2.5 s |
| LinkedIn 4 s | 2.48 / 2.76 / 3.96 / 4.22 | 7.7 | +26 | +1.6 s |
| **LinkedIn 5 s (kept)** | 2.38 / 2.21 / 4.94 / 5.03 | 6.28 | 0 | 348.2 s |
| Indeed 1 s | 1.37 / 0.84 / 2.67 / 19.37 | 3.74 | +187 | −29.3 s |
| **Indeed 2 s (new)** | 1.74 / 1.24 / 2.83 / 18.74 | 2.35 | +87 | **−17.2 s** |
| Indeed 3 s | 2.93 / 2.72 / 4.44 / 20.74 | 2.01 | +63 | **+28.3 s** |
| Indeed 4 s | 2.31 / 1.15 / 4.37 / 22.74 | 1.43 | +21 | +2.6 s |
| Indeed 5 s (was) | 2.18 / 1.67 / 4.52 / 19.43 | 1.14 | 0 | 398.2 s |

Segment walls at 1–4 s are **INFERRED (simulated)**, at 2 workers with
plan-order integration — not measured production latency. Polls are free: on
90/90 runs the settled charge equals the priced events, none of which is an API
read (VERIFIED). C5 had 0 HTTP 429 at 5 s; the provider's published API limit
could not be checked offline (UNKNOWN); the peak poll rate at 2 workers stays
around 1.5 requests/s even at 1 s.

**Decision.** LinkedIn stays at **5 s**: the best alternative saves ~10 s of a
348 s segment for twice its poll requests. Indeed goes to **2 s**
(`scraper.POLL_SECONDS = {"indeed": 2}`, everything else
`POLL_DEFAULT_SECONDS = 5`): median lag 1.67 → 1.24 s, p95 4.52 → 2.83 s, ~17 s
off the 398 s segment, for ~87 more free GETs a plan. The improvement is modest
but real; the load is small and costs nothing; nothing in evidence argues
against it. It is also more robust than 5 s: Indeed runs take ~4 s, so 5 s
worked partly by coincidence, and 3 s — the "obvious" middle — is worse than
5 s because it lands just before most finishes. Not 1 s: each extra request
buys less and poll traffic triples. The change is INFERRED until the first
production sweeps' telemetry (C1's `poll_count` and detection fields) confirms
it; reverting is one constant. A test pins both intervals (mutations O: the
faster poll leaking to LinkedIn, and one aggressive interval for everything).

**The 19.4 s Indeed outlier** is paid_020 (keyword 1, Gurgaon). VERIFIED: one
poll, sent 0.839 s after the run had already finished; that single request took
18.592 s; the SDK made 4 calls and 6 requests (two re-sends, 0 × 429), and the
timeouts mean both re-sends happened inside that poll; not C2 buffering (5 ms),
not the provider (4.3 s runtime, SUCCEEDED). MEASURED: another thread's account
read on the same account stalled over the same window (18.590 s, overlapping by
18.055 s) against a typical 0.328 s. INFERRED: a stall upstream of both clients
— the network path or the provider's API. The underlying cause is **UNKNOWN**;
no poll interval fixes a stall inside one request (the lever there is the
request timeout, a separate measured change).

## 5. D5 — the free path

Evidence: [d5-free-path-analysis.json](search-v2-evidence/d5-free-path-analysis.json),
[d5-shadow-boards.json](search-v2-evidence/d5-shadow-boards.json),
`bench/search_v2_d5_d6_analysis.py`. No request was made.

**What the 8.8 minutes were** (MEASURED, C5: 134 free sources, 133 healthy; the
record's 224/223 counted paid sources too):

| family | sources | scheduling | wall | share | slowest | failures | raw → gated → eligible → final |
|---|---|---|---|---|---|---|---|
| Lever | 21 | 4 workers | **423.7 s** | **80.3%** | veeva 359.5 s, sophos 153.2 s, binance 146.3 s | 0 | 1,915 → 216 → 4 → 4 |
| Greenhouse | 54 | 4 workers | 30.8 s | 5.8% | sumup 22.8 s | 1 (postman 404) | 7,162 → 2,027 → 164 → 109 |
| Ashby | 27 | serial | 31.5 s | 6.0% | ramp 4.1 s | 0 | 2,430 → 682 → 19 → 15 |
| SmartRecruiters | 24 | serial | 20.7 s | 3.9% | median 0.8 s | 0 | 665 → 110 → 0 → 0 |
| Breezy | 3 | serial | 2.5 s | 0.5% | — | 0 | 54 → 3 → 0 → 0 |
| WWR | 1 (8 requests) | serial | 6.2 s | 1.2% | — | 0 | 195 → 80 → 12 → 5 |
| other feeds | 4 | serial | 12.5 s | 2.4% | remoteok 6.2 s | 0 | 369 → 93 → 14 → 13 |

No retries, no 429 anywhere. **One Lever board, `lever:veeva`, was 68% of the
free phase** (359.5 s for 918 rows, one request): its response body arrived
slowly (INFERRED ~36 kB/s against 1,927 kB/s on 2026-09-21), and the per-read
25 s socket timeout never trips on a steady slow download (VERIFIED,
`sources/_http.py`). On 2026-09-22 the whole Lever family took 24.3 s at the
same 4 workers. The slowdown's cause is UNKNOWN (provider throttling INFERRED:
Greenhouse's median board took 0.88 s from the same host seconds later). A
registry-order FIFO model reproduces every family segment exactly (VERIFIED),
and shows the Lever floor was that one board: more workers could not have
helped.

**Free concurrency: unchanged.** Lever and Greenhouse stay at 4 workers. No
serial family dominates: Ashby (6.0%) and SmartRecruiters (3.9%) together are
~52 s. A Free-only concurrency benchmark for them is justified *later*
(~36–39 s a sweep, INFERRED), but it needs each family's own default-off switch
first — a separately reviewed change — so none was run. What the Lever tail
needs is a re-measurement (a serial Lever-only pass at two or three times of
day, recording time-to-headers against body-read time) and then, if it
recurs, a per-board wall-clock deadline: both follow-ups, not D changes.

**`greenhouse:postman`: removed.** Nine fetches on file, every one HTTP 404
(2026-09-21 audit and spot-check, 2026-09-23 capture pass and five benchmark
arms, 2026-09-24 C5), 0 retries. No trusted replacement exists in the
repository (65 evidence files scanned; the only look-alike slugs,
`kellerpostman` and `postmanlaw`, are not Postman). `config.py` loses the one
entry: Greenhouse 54 → 53 boards, 129 → 128 ATS boards, 134 → 133 active
records. The registry-order hash test was re-pinned to the old order minus
postman (verified: re-inserting it reproduces the old hash), and now asserts
postman is gone (mutation P caught).

**The eight B3 shadow boards: promote none, keep all in shadow.** Five
observations exist (2026-09-21 frozen snapshot and 2026-09-23 live benchmark,
both against synthetic cohorts; two controlled developer runs, fetch health
only; C5) and only C5's is natural. Each board was fetched 7 times with 0
failures; in C5 each had 0 key or native-id overlap with production and added 0
rows to the top 20 (the tranche would have taken the list from 201 to 212).
`abnormalsecurity` (3 eligible-new, best rank 29, positive in 4/5 cohorts twice)
is the closest; `jumio` was inconsistent (1/5 then 0/5). One natural
observation is not repeated natural evidence. `SWEEP_FREE_SOURCE_SHADOW` stays
on to collect it.

**Results-ready: unchanged.** C5 proved both result files were final before the
marker and nothing after it changed an output; no output-writing work moved.

## 6. D6 — identity, telemetry, research tooling

Evidence: [d6-identity-audit.json](search-v2-evidence/d6-identity-audit.json).

**Identity (VERIFIED from code).** `job_key` is the requisition number if
present, else normalised company + sorted title words, else URL host + path.
Dedupe sorts by score (stable) and keeps the first row per key, so on a tie the
earlier arrival wins — paid rows in plan order, then free rows in registry
order. Provider-native ids (`_native`) are captured only while telemetry is on;
paid rows carry none; `to_output()` never writes them. `seen.tsv` is date, key,
title, company, read only by `--only-new`, which the worker never passes.

MEASURED: native ids are present on 100% of free ATS and feed rows (3,211 in
C5) and 0% of LinkedIn and Indeed rows. Within C5, the same native id never
appeared under two job keys; across 65 dated output files (2026-08-01..09-24)
29 of 1,912 recurring ids (1.5%) did — 28 retitles, 1 company rename. The
reverse is larger: C5 merged 718 gated rows inside single boards that had
distinct native ids — mostly one role posted in several locations, which the
title-based key deliberately collapses. The same raw id never appeared under
two providers. All 13,744 well-formed `seen.tsv` company+title entries
recompute to the same key today.

**Decision: native identity stays diagnostic-only.** A merge-only native rule
(provider namespace + id, falling back to `job_key`) would have merged **0**
extra rows in C5; letting native ids split keys would undo up to 718
deliberate location collapses — a product decision, not an identity fix. The
real drift (1.5–2.2% retitles over weeks) touches only local ledgers the public
worker never reads. Revisit when a user-facing ledger ships on the worker path:
then a merge-only lookup keyed on (family, id) beside `job_key`, with capture
made unconditional. Because no production code consumes native ids, the brief's
mutations Q (cross-provider collision) and R (missing-id fallback) have no code
to mutate; they become live requirements only with that change.

**Telemetry.** Core Search V2 telemetry stays on in production (it is what C5's
analysis, D3 and D4 were built from). Adaptive shadow stays off publicly.
Account-pool telemetry (`paid_execution.accounts`, `.authorization`) appears
only when the pool runs; its `developer_account_cap` is null on every BYOK run.
No field was removed: C1/C4 parsers keep working. Recommended next telemetry
addition (not made): a salted per-sweep hash of each paid search's job keys,
which would make D3's overlaps exact.

**Research tooling** — all developer/research only, and all offline unless
through the C0 guard: `bench/paid_guard.py` (the guard),
`bench/search_v2_paid_probe.py` (the only guarded paid tool),
`search_v2_paid_concurrency.py` and `search_v2_paid_overhead.py` (offline
benchmarks), `search_v2_paid_adaptive.py` (replay), `search_v2_paid_accounts.py`
(allocator benchmark), `search_v2_c5_analysis.py`, and this stage's
`search_v2_d3_d4_analysis.py` and `search_v2_d5_d6_analysis.py` (offline, no
network import, pinned by their own tests). The C0 reachability scan is green:
no new file reaches paid code (mutation S — a new tool gaining a client call —
caught). The D3/D4 script matches the scan's pattern because it reads
`scrape_search`'s source text; it is classified `unreachable` in the C0 map and
the C0 doc, like `search_v2_free_audit.py`.

## 7. D7 — production configuration, regression, safety

### 7.1 The recommended production environment

Engine flags are read by the child from the **worker's** environment
(`/etc/sweep-worker/env` on Oracle; restart `sweep-worker` while it is idle).
Render needs **no new variable**: it learns the engine from the worker's dry
run (`account_pool`).

```
# /etc/sweep-worker/env — Search V2 (in addition to the worker's own settings)
SWEEP_SEARCH_V2_TELEMETRY=1
SWEEP_FREE_LEVER_CONCURRENCY=1
SWEEP_FREE_LEVER_WORKERS=4
SWEEP_FREE_GREENHOUSE_CONCURRENCY=1
SWEEP_FREE_GREENHOUSE_WORKERS=4
SWEEP_FREE_SOURCE_SHADOW=1
SWEEP_RESULTS_READY_EARLY=1
SWEEP_PAID_CONCURRENCY=1
SWEEP_PAID_WORKERS=2
SWEEP_PAID_MULTI_ACCOUNT=1
SWEEP_PAID_ADAPTIVE_MODE=off
# never set here: SWEEP_PAID_ACCOUNT_CAP_USD, SWEEP_ALLOW_PAID_BENCH,
# SWEEP_BYOK_CREDENTIALS, any APIFY_TOKEN*
# unchanged: SWEEP_EXPERIENCE_MISMATCH_GUARD (off, as reviewed)
```

Render unchanged: `SWEEP_PROFILE_ENGINE_VERSION=v2` (`render.yaml`) and the
existing public-mode settings.

### 7.2 Deploying it (a recommendation; nothing was deployed)

1. **Oracle checkout first**, flags as they are today. The new worker accepts
   the old Render's requests (one key, no `key_ids`) and the new engine takes
   a single key on stdin; public behaviour is the single-account path until the
   flags change.
2. **Render second.** Against a worker whose dry run says no pool, the new
   Render keeps the one-key flow exactly (VERIFIED,
   `test_one_account_engine_keeps_the_one_key_view`).
3. **Flip the paid flags** on the worker (`SWEEP_PAID_CONCURRENCY=1`,
   `SWEEP_PAID_WORKERS=2`, `SWEEP_PAID_MULTI_ACCOUNT=1`), restart while idle.
   Confirm now shows the visitor's accounts, coverage, "Add another Apify key"
   and — only when the plan does not fit — "Run with my available credit
   anyway".
4. Watch the first sweeps' telemetry: `paid_execution.authorization`, the
   Indeed `poll_count` and detection fields (D4), and any 429.

**Rollback:** `SWEEP_PAID_MULTI_ACCOUNT=0` returns public paid sweeps to one
account (Render follows on its next plan); `SWEEP_PAID_CONCURRENCY=0` returns
the serial engine. Neither loses a run. Reverting the Indeed poll is one
constant.

### 7.3 Regression (local; no provider)

| suite | result |
|---|---|
| Sweep suite (`python -m unittest discover -s sweep/tests -t .`: B1–B5, C0–C5, C4.5, D and every app test) | **1,605 OK** (pre-D baseline 1,535; +70) |
| Deploy (`deploy.test_sweep_worker`, `deploy.test_modal_benchmark`) | **42 OK** |
| auto-apply | 1,102 run, **2 errors** — `test_healthz_needs_no_token`, `test_healthz_answers_while_a_generation_holds_the_slot`: the same two on the pre-D baseline (environmental) |
| `scraper.py --demo`, `python -m sources.concurrency` | OK |
| C5 offline analysis, rerun on the kept outputs | `c5-provider-cost-settlement.json` and `c5-adaptive-analysis.json` byte-identical; `c5-final-summary.json` identical except `no_batching`, which by design needs the raw actor inputs that C5's evidence replaced with fingerprints |
| D3/D4 and D5/D6 analyses | deterministic (byte-identical reruns); 8 + 11 tests OK |

The pre-D baseline ran in a worktree of `a87e3c9`, where three
`test_runs` cwd tests fail on the worktree path alone; in the repository they
pass.

### 7.4 Mutations (MEASURED, [d-mutations.json](search-v2-evidence/d-mutations.json))

| id | brief | mutation | verdict | failing tests |
|---|---|---|---|---|
| D1-A | A | Full authorization uses the estimate, not the ceilings (engine) | caught | 21 |
| D1-A2 | A | Public Confirm authorizes on the estimate against credit | caught | 1 |
| D1-B | B | Aggregate balance instead of exact placement (engine) | caught | 17 |
| D1-B2 | B | Aggregate balance instead of exact placement (public coverage) | caught | 1 |
| D1-C | C | Duplicate account counted twice (engine discovery) | caught | 1 |
| D1-C2 | C | Duplicate account counted twice (public add-key) | caught | 1 |
| D1-D | D | Full sweep silently truncates with partial unchecked | caught | 5 |
| D1-E | E | Partial checkbox ignored (engine never reads the setting) | caught | 15 |
| D1-E2 | E | Partial checkbox ignored (Render never writes it) | caught | 1 |
| D1-F | F | Partial runs every search that fits, not the prefix | caught | 1 |
| D1-G | G | LinkedIn prioritised by C5 contribution (allocator and authorization) | caught | 1 |
| D1-H | H | A skipped-for-capacity search is written to .done_combos | caught | 2 |
| D1-I | I | A skipped-for-capacity search is labelled failed | caught | 9 |
| D1-J | J (V2-D I) | Configure-time headroom trusted (no fresh account read) | caught | 42 |
| D1-K | K (V2-D T) | A visitor's raw key becomes its slot label | caught | 2 |
| D1-L | L (V2-D K) | Developer clamp applies to a visitor's accounts (engine) | caught | 1 |
| D1-L2 | L (V2-D K) | Developer clamp reaches a public child (worker) | caught | 1 |
| D1-M | M | Per-account exposure ignored | caught | 5 |
| D1-N | N | Global max_spend_usd ignored by authorization | caught | 2 |
| D1-O | O (V2-D J) | Unreadable account treated as unlimited | caught | 4 |
| D-L | V2-D L | Adaptive defaults to shadow | caught | 3 |
| D-M | V2-D M | Paid workers default above 2 | caught | 1 |
| D-N | V2-D N | LinkedIn depth reduced below 15 | caught | 14 |
| D-P | V2-D P | The dead Postman board is back in the registry | caught | 2 |
| D-S | V2-D S | A research script gains unguarded paid reachability | caught | 1 |
| D-O | V2-D O | The faster poll applies to LinkedIn too | caught | 1 |
| D-O2 | V2-D O | One aggressive interval for every provider | caught | 1 |
| D-T | V2-D T | A second public key leaks into the worker's log | caught | 1 |

The first run left four survivors (A2, B2, F, G): no test yet separated the
estimate from placement on Confirm, summed from placed credit on Confirm, a
memory-blocked search from the prefix, and the G mutant reordered only the
authorization. Each now has a test (or, for G, a faithful two-edit mutant) and
is caught. Q and R have no code to mutate (native identity is diagnostic-only,
§6).

### 7.5 Static safety (VERIFIED, scanned)

- No real Apify token in any tracked or new file, in `output/c5-run` or in any
  evidence file: the seven configured values, 558 files, 0 hits; no
  token-shaped string outside test fixtures.
- No token, account id, username or email in telemetry, evidence, logs, pages or
  session state (tests 23 and the public page/session scans).
- V2-D evidence: no job title, company from a job row, description, URL or
  query; ATS board slugs appear as source identifiers from the committed
  registry.
- The developer clamp is not reachable from any form or API, is stripped from a
  public child and ignored under BYOK (mutations L, L2).
- Adaptive is off by default (mutation L). No research tool gained paid
  reachability (C0 scan green; mutation S).
- No deployment value changed: `render.yaml`, the worker's service and env are
  untouched; the only deploy-doc edit corrects a statement about `.env`.

**One incident, handled.** While running the full suite, `test_25` (V2-D's own)
compared against the live environment, which an earlier test had filled from
the developer's `.env`; its failure message printed one real token into a local
test log. The log was deleted, the test now runs in a sealed environment (as do
two other new tests whose failure messages would quote the environment), and
the rerun's log was scanned for every real token before being read (0 hits).
The key itself is the operator's own; rotating it is advisable.

## 8. Unresolved and follow-ups

1. **Indeed 2 s** is simulated, not measured: confirm on the first production
   sweeps (poll counts, detection lag, 429s); revert the constant if not.
2. **Gurgaon + Noida** are a second observation away from removal ($1.62 settled
   a sweep).
3. **The Lever straggler** (`lever:veeva`, 359.5 s) is UNKNOWN in cause: a
   repeat measurement, then possibly a per-board deadline.
4. **Ashby/SmartRecruiters concurrency**: justified to benchmark (~36–39 s
   INFERRED) once each has its own default-off switch.
5. **Native identity**: revisit when a user-facing ledger ships on the worker
   path.
6. **The Apify API rate limit** for polling is UNKNOWN offline; 0 × 429 in C5.
7. A public sweep cannot resume: each is its own run directory, so a partial
   sweep's left-out searches are run by a new sweep, not a resume.

## 9. Research ledger

No paid provider call in V2-D. `paid-research-ledger.json` is unchanged:
**$11.413 intended / $7.53135 actual** cumulative.
