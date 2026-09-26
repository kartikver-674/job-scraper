# Multi-Track Phase 3 — Handoff

Phase 3 builds the schema-2 acquisition and planning path in the engine. For a
Sweep of 1–3 `TrackContext`s, `main()` now:

1. builds each track's units the way `plan_for_site` builds one profile's;
2. identifies each unit by the provider request it would send (`request_key`);
3. runs a request that several tracks share **once**, with every requester in
   its provenance;
4. interleaves units fairly by track within each site, then applies the
   per-site cap to the merged list;
5. stamps `_tracks` on every paid row and `_free` on every free row;
6. fetches the free sources once, through the union of the tracks' own title
   gates, with one Himalayas query list;
7. binds Confirm to execution with a plan hash, and exits 4 on a stale one
   before any account is read;
8. finalizes every checkpoint through `finalize_multi` into
   `MULTI_OUTPUT_COLUMNS`.

The existing scheduler, `AccountPool`, placement and authorization run
unchanged on the merged plan. Schema 1 takes today's code path.

**Scope of what now runs.**
- A schema-2 run works end to end in offline and fake-provider tests.
- No production caller can produce a schema-2 profile: the worker renders only
  with `render()`, and Render never calls `render_sweep`.
- **No real Multi-Track run is approved until Phase 0b is live.**

Nothing is committed, pushed or deployed. No provider or model was called.
**Waiting for review.**

**Local résumé validation: not run.**
- The request to validate against three local résumés arrived without file
  paths.
- When asked, the user chose to skip the validation and to allow no model
  call. Deriving a résumé needs two model calls.
- So no plan structure, dedupe behaviour or provenance was observed on real
  résumés. Every figure below is synthetic.
- No résumé content, derived data or fixture from them exists anywhere.

---

## Single-Résumé P0 Compatibility

| Guarantee | How it holds |
|---|---|
| Schema 1 does not pass through the new planner | `main()` branches on `TRACK_CONTEXTS`. For `()`, which every schema-1 profile has, it runs `plan_for_site`, `fetch_free`, `finalize` and `OUTPUT_COLUMNS` exactly as before. A test patches every schema-2 function to raise, then runs a schema-1 paid + free sweep to completion |
| Frozen functions untouched | Source-identical to `bcaaa55` by AST comparison: `build_search_plan`, `plan_for_site`, `effective_search`, `build_input`, `resolve_sites`, `_build_linkedin_url`, `_indeed_country`, `_linkedin_experience_code`, `max_charge_usd`, `paid_unit`, `place_units`, `placeable_prefix`, `project_assignment`, `AccountPool`, `PaidExposure`, `_paid_worker`, `discover_accounts`, `finalize`, `score_and_filter`, `_sweep_filters`, `rank_rows`, `dedupe`, `job_key`, `to_output`, `score_job`, `evaluate`, `fetch_free`, `is_dev_title`, `title_admits`, `location_allowed`, `print_plan`, and the Phase 2B functions |
| The four functions that changed | `main`: the dispatch. `scrape_search`: a stamp keyed on `_tracks`, which no schema-1 unit has. `paid_phase_c2` and the serial loop: the inline combo key moved into `done_key`, which returns the same string for any unit without `_ledger`. `write_outputs`: an optional `columns`, defaulting to `OUTPUT_COLUMNS` read at call time. Schema 1's `emit` still calls it with today's three arguments (§P explains why that matters) |
| Dry-run JSON | Unchanged for schema 1: the Phase 0a golden asserts no `plan_hash`, `plan_version`, `tracks`, `ledger` or `request_key`. The additions exist only under `TRACK_CONTEXTS` |
| No new mandatory hash | Schema 1 never reads `SWEEP_EXPECTED_PLAN_HASH` (tested with a garbage value) |
| No provenance keys | No schema-1 paid row gets `_tracks` and no free row `_free` (tested on a real paid + free sweep) |
| Outputs | Phase 0a goldens 35/35. No fixture changed |
| Calls, bytes, time | §P and §S: identical provider call sequences and output bytes; timings within noise |

---

## A. Starting Git State

| | |
|---|---|
| Branch | `feat/multi-track-search` |
| HEAD | `bcaaa5579e32cd30eceb8510ea5ffb2f0db64c14` (`feat(multi-track): add multi-context result evaluation`) |
| Upstream | `origin/feat/multi-track-search` at `cb602ec`; local 3 ahead, 0 behind |
| Working tree | clean |

## B. Production Files Modified

| File | Change | What |
|---|---|---|
| `scraper.py` | +250 / −26 | Schema-2 planner (`REQUEST_KEY`, `DONE_ID` — the unit's `_ledger` key, `RUN_TIMEOUT_S`, `PLAN_VERSION`, `PLAN_HASH_ENV`, `PLAN_CHANGED_EXIT`, `_sha256_json`, `track_plan`, `request_key`, `round_robin`, `multi_plans`, `plan_hash`, `done_key`); free side (`himalayas_queries`, `fetch_free_multi`); the `_tracks` stamp in `scrape_search`; `done_key` in both paid loops; `write_outputs(columns=)`; `main()` dispatch; the 2A refusal removed; two stale comments updated |
| `sweep/runs.py` | +7 | `combo_key` uses a dry-run entry's `ledger` when present |

Not touched: the worker, `sweep/app.py`, `sweep/plan.py`, `config.py`, `make_profile.py`, `sources/`, templates, exports, environment and deploy files.

**Tests and docs**

| File | Change |
|---|---|
| `sweep/tests/test_multi_plan.py` | **new**, 52 tests (§R) |
| `sweep/tests/test_track_loading.py` | one deliberate flip: `LoadedSweep` asserted that `main()` refuses schema 2. It now asserts that the dry run is the unified plan (`plan_version` 2). The one-profile refusals beside it (`default_context`, `score_job`, `finalize`) are unchanged |
| `sweep/tests/test_multi_results.py` | the same flip in `OneTrackParity`. Its probe captures `main()`'s stdout, which is now a JSON plan, and the docstring no longer says `main()` refuses |
| `MULTI_TRACK_PHASE_3_HANDOFF.md` | **new**, this file |

## C. P0 Schema-1 Compatibility Statement

- Schema 1 is the primary path, and Phase 3 does not route it through anything new.
- `TRACK_CONTEXTS == ()` selects today's planner, combo keys, provider inputs, free predicate, Himalayas queries, scheduler, `AccountPool` authorization, checkpoint `finalize`, `OUTPUT_COLUMNS` and dry-run JSON.
- The only schema-1 lines that changed are the dispatch conditions and two call sites that now go through `done_key`, which returns the identical string.
- Evidence:
  - the goldens (35/35);
  - AST source identity of every frozen function;
  - a dispatch test with every schema-2 function patched to raise;
  - byte-identical outputs and provider call logs against `bcaaa55` (§P);
  - timings within noise (§S).

## D. Exact Schema-2 Planning Entry Point

### D.0 The path as traced before editing (`bcaaa55` line numbers)

| Step | Where |
|---|---|
| Profile | `config` selects it at import; schema 1 overlays the globals, schema 2 loads `TRACKS` (2A); `scraper.TRACK_CONTEXTS` is built at import |
| Sites and their order | `resolve_sites` (`scraper.py:1269`): `config.SITES` dict order, filtered by `enabled`, `--site`, `--test` (`:1281`) |
| Units | `plan_for_site` (`:1284`) → `build_search_plan` (`:1241`): keyword, then location, then company |
| `--limit` / `max_searches_per_site` | `plan_for_site`, a prefix per site (`:1300`) |
| Dry run | `main` (`:4268`): `sites`, `max_results` and `charge_ceiling_usd` per site from `plan[0]`, `free_sources`, `public_paid` |
| Plan cost / Confirm | Render: `state["raw_plan"]` (the dry run) → `runs.remaining_plan` → `plan.cost` → `state["plan"]`. The engine re-plans at run time, and until this phase nothing binds the two |
| Charge ceilings | `max_charge_usd` (`:1393`), from the dry run (`:4288`), `PaidEntry` (`:2709`), `scrape_search` (`:1580`) and `paid_unit` (`:1489`) |
| Provider input | `build_input` (`:1177`) via `effective_search` (`:1427`), in `scrape_search` (`:1561`), the preflight (`:4322`) and the human dry run (`:4305`) |
| `combo_key` | written inline in `paid_phase_c2` (`:2707`) and the serial loop (`:4500`). Read by the done filters (`:2414`, `:2445`, `:2868`, `:4502`) and the in-flight twin wait. Appended to `.done_combos` (`:2805`, `:4562`). Mirrored by `sweep/runs.combo_key`, parsed by `runs.progress`, served by the worker's `done_combos` |
| Done ledger | loaded filtered to today (`:4403`) |
| Authorization | `PaidEntry` → `AccountPool.open` (`:4448`) → `plan` → `authorize` → `placeable_prefix` |
| Execution | `paid_phase_c2` (`:4469`) or the serial loop → `scrape_search` |
| Integration | `raw_rows.extend(rows)` in C2's `integrate` (`:2795`) and the serial loop (`:4556`); free rows via `raw_rows.extend(fetch_free())` (`:4605`) |
| Checkpoint | `emit` (`:4373`) → `finalize` → `write_outputs`, after every search, after the free phase, and at the end |

### D.1 The schema-2 path now

```
main()
├ resolve_sites(args)                                     unchanged
├ TRACK_CONTEXTS ? multi_plans(enabled, args, TRACK_CONTEXTS)
│                : {site: plan_for_site(site, args)}      today
│   multi_plans, per site in `enabled` order:
│     track_plan(site, args, t) per track      plan_for_site's body per track, no cap
│     request_key(site, unit) per unit         digest of the built provider request
│     requesters[key] = track ids              track order, each once, before ordering/cap
│     round_robin(per-track sequences)         one new unit per track per turn
│     cap (--limit / max_searches_per_site)    a prefix of the MERGED list
├ drop empty sites                                        unchanged
├ schema 2, not a dry run: SWEEP_EXPECTED_PLAN_HASH set and ≠ plan_hash → exit 4
├ --dry-run --json (+ per-entry tracks/ledger, plan_version, plan_hash under schema 2)
├ preflight, confirmation, output paths, telemetry.start  unchanged
├ emit: finalize_multi + MULTI_OUTPUT_COLUMNS  |  finalize + OUTPUT_COLUMNS
├ paid: paid_plan telemetry, paid_adaptive (off for schema 2), kill switch,
│   AccountPool.open | _require_token, then paid_phase_c2 or the serial loop:
│   PaidEntry.combo_key = done_key(...) → scrape_search stamps _tracks → emit → done marker
├ free: fetch_free_multi(TRACK_CONTEXTS) | fetch_free()
├ final emit, summary, seen.tsv, results-ready marker
└ shadow tranche (schema 1 only), telemetry
```

A bad geography (`build_input`'s `ValueError`) now refuses schema 2 while planning, dry runs included, with the preflight's message.

## E. Request-Key Format

```python
request_key = sha256(json.dumps({
    "actor":     SITES[site]["actor"],
    "input":     build_input(site, effective_search(site, unit)),
    "ceiling":   str(max_charge_usd(site, depth)),   # or null where no ceiling
    "timeout_s": 300,                                # RUN_TIMEOUT_S
}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
```

- This is exactly what `scrape_search` sends. A test captures the start call and recomputes the key from the input, timeout and ceiling actually sent.
- It contains no credential and no account state.
- Built once per unit per track while planning, and stored on the unit as `_request_key`.
- **Output safety.** `build_input` prints an unmapped-Naukri-city note to stdout, which would land inside `--dry-run --json`.
  - While `multi_plans` builds keys, stdout is redirected to stderr, so nothing is suppressed.
  - Schema 1's `build_input` is untouched and still prints to stdout. Both behaviours are tested.

| Collapses or splits (tested) | Result |
|---|---|
| Same actual LinkedIn request from two tracks | collapses; `_tracks = [A, B]` |
| Different `f_E` band (2 vs 6 years) | splits |
| Same band from different years (3 and 5 → `f_E` 4) | collapses |
| Geo, remote location, depth, recency (`f_TPR`), `remote_only`, actor, ceiling model, LinkedIn company, Indeed country, keyword case | each splits |
| `salary_min` (no adapter reads it); Indeed experience; Indeed company | collapse |
| LinkedIn `Remote` vs `India` with `remote_only` | one URL (`f_WT=2` in India), so one request |

## F. Dedupe Rules

- Two units execute once **if and only if** their `request_key`s match. No field-by-field equality decides it.
- The merged unit is the first-placed unit's dict plus:
  - `_request_key`;
  - `_tracks`: every requester, in track order, each once;
  - `_ledger`.
- Requesters are gathered over each track's **full** sequence, before the round-robin and before the cap.
  - So a track keeps a shared request even when its own turn at it falls past the cap.
- A field the request does not read keeps the first placer's value. Example: Indeed's `experience_years` on a unit MERN (2 years) and AI (6 years) share.
  - The request, key and label are identical either way.
- Within one track, a repeated request runs once and lists that track once.
  - Sweep-generated location lists repeat no geography: one-track parity holds for India, remote, global-custom and India + Naukri.
  - A hand-written profile that repeats a request (LinkedIn `India` + `Remote` with `remote_only`) runs it twice as schema 1 and once as schema 2. That is tested.

## G. Round-Robin Algorithm

```python
def round_robin(seqs, key):
    iters, placed, out = [iter(s) for s in seqs], set(), []
    while iters:
        for it in list(iters):                       # track order
            item = next((x for x in it if key(x) not in placed), None)
            if item is None: iters.remove(it); continue
            placed.add(key(item)); out.append(item)
    return out
```

- Each track gains one **new** unit per turn. A request another track already placed is skipped and costs no turn.
- A shared unit sits at the earliest slot any requester reaches it.
- The units a track places itself keep its order. A shared unit may only move earlier.
- Site-major is unchanged: all LinkedIn units, interleaved, before any Indeed unit.
- One track gives exactly `plan_for_site`'s units.

| Tracks | Merged |
|---|---|
| `[a1 a2 a3] [b1 b2] [c1]` | `a1 b1 c1 a2 b2 a3` |
| `[x a1 a2] [b1 x b2]` | `x(1,2) b1 a1 b2 a2` |
| `[a1 x] [x b1]` | `a1 x(1,2) b1` |
| `[a1 a2 a3 x] [x]`, cap 2 | `a1 x(1,2)` |

The synthetic MERN / Mobile / AI fixture, one location per site:

| LinkedIn (8) | Indeed (7) |
|---|---|
| mern stack (M), react native (Mo), ml engineer (AI), **react developer (M, Mo)**, android (Mo), react developer (AI, `f_E` 5), node.js (M), ai engineer (AI) | mern stack (M), react native (Mo), ml engineer (AI), **react developer (M, Mo, AI)**, android (Mo), ai engineer (AI), node.js (M) |

## H. Paid Provenance Stamping

- `scrape_search` stamps each row it returns with `row["_tracks"] = list(unit["_tracks"])`, when the unit carries `_tracks`.
  - It is the one place any paid row is made, so the rows carry provenance before they can enter `raw_rows`.
  - Each row gets its own list, never the plan's.
- Every integration path goes through it, and each is tested with fake providers, asserting every row of every checkpoint:
  - the serial loop;
  - C2 (2 workers);
  - the developer account pool;
  - BYOK multi-key;
  - BYOK serial.
- Repeated checkpoints never change a row's provenance.
- **Fail-closed, tested on the serial loop.** Rows whose stamp is removed make every later checkpoint raise `ValueError` (2B's contract).
  - The serial loop records each of those checkpoints as a failed search, and nothing is written for it. C2's `integrate` wraps its checkpoint in the same per-search handler; that is from reading the code, not from a test.
  - The final emit raises.
  - The CSV holds only what checkpointed before, and `.done_combos` holds only those units.
- No schema-1 row is ever stamped.

## I. Free Union Acquisition and Provenance

- `fetch_free_multi(tracks)` fetches every board and feed **once**.
- The title predicate is `any(title_admits(title, t.title_hints, t.title_exclude) for t in tracks)`: each track uses its own hints **and its own excludes**, never a union of excludes.
- Location (`location_allowed`, `LOCATION_HINTS`) and `hires_home` stay the Sweep's, unchanged.
- Every returned row is truncated as today and stamped `_free = True`, never `_tracks`.
  - Stamping the returned list covers every adapter: ATS serial and concurrent, feeds, Optum, enterprise.

| Tested | Result |
|---|---|
| MERN excludes "mobile" and Mobile admits it: "Mobile Engineer" | fetched; `track_evals` has Mobile only |
| Both reject ("Sales Manager"); AI's hint with AI's exclude ("Machine Learning Intern") | not fetched |
| Shared location gate ("Berlin" with `LOCATION_HINTS = ["bengaluru"]`) | not fetched |
| One track built from config's gate against schema 1's `fetch_free` on 15 titles | identical rows (minus `_free`) and identical Himalayas requests |
| Schema 1 | `fetch_free` passes `is_dev_title`, `location_allowed`, `ATS_BOARDS` and `FEEDS` by identity; no `_free` |

## J. Himalayas Union

- `himalayas_queries(tracks)` is the round-robin over the tracks' `role_keywords`.
  - Each track's order is kept, exact repeats appear once, and blanks are dropped the way `_fmt_feeds` drops them.
- When the list is non-empty, `FEEDS["himalayas"]` becomes `{"enabled": True, "pages": 10, "queries": list}`. That is the exact entry schema 1 renders.
- The list is not cut. `feeds._himalayas_urls` applies today's `max_queries` (8) to the **whole** list: 8 in total, never 8 per track.
- With no keywords, `FEEDS` is left as configured (browse mode), as for a schema-1 profile with none.
- `A1 A2 A3 A4 / B1 A1 B2 B3 / C1 ␣ C2 C3` gives `A1 B1 C1 A2 B2 C2 A3 B3 C3 A4`, and the URLs are the first 8.
- One track gives exactly the schema-1 URLs (its first 8). Schema 1 is unchanged.

## K. Ledger Identity

- `_ledger = f"{site}|{keywords}|{location}|{company or ''}|{request_key[:16]}"`.
- `done_key(today, site, unit)` is `f"{today}|{unit['_ledger']}"` for a schema-2 unit. For anything else it is today's combo key, byte for byte.
- `PaidEntry.combo_key` carries it. So `AccountPool.plan`/`authorize`'s done filter, C2's twin wait, the done check and the done marker all use it, with no pool or scheduler change.
- **Render side.** `sweep/runs.combo_key` uses a dry-run entry's `ledger` when present, so the progress grid and `remaining_plan` pricing follow it.
  - `runs.progress` splits on `|` at most 4 times, so the suffix falls into the ignored company slot.
  - Labels still read "keywords @ location" (tested on a real schema-2 dry run).

| Tested | Result |
|---|---|
| A request shared by MERN and Mobile | one unit, one ledger line |
| AI's "react developer @ India" | the same display fields, a different suffix, its own line |
| A run | writes exactly the plan's ledger lines, in plan order |
| A same-day rerun | starts nothing |
| Schema 1 | `done_key` = `runs.combo_key` = today's format, with or without a company |

## L. Plan-Hash Payload and Check

```python
plan_hash = sha256(canonical JSON {
    "v": 2,
    "tracks": [[track_id, engine], ...],                   # selected order
    "units":  [[site, actor, request_key, ceiling, [requesting track ids]], ...],   # plan order
})
```

- Taken over the **capped** plan, **before** done-filtering.
- Bound: the version, the tracks and their order, each track's engine, every unit's site, actor, request key, ceiling and provenance, and the unit order.
- Not included: the profile or module name, output dir, run id, labels, filenames, balances, account assignment, done state, spend cap and partial approval.
- The schema-2 dry run returns `plan_version: 2` and `plan_hash`, plus per-entry `tracks` and `ledger`. These are additions only; key order is tested.
- **Check.** `SWEEP_EXPECTED_PLAN_HASH` (`PLAN_HASH_ENV`, the design's worker-to-child variable) is read for schema 2 only, and not on a dry run.
  - If it is set and differs from the rebuilt plan's hash, the engine prints `PLAN_CHANGED` to stderr and exits **4**.
  - That happens right after planning: before printing the plan, before the output dir, telemetry or any account read, and before any client is built.
  - Unset means unbound. Local and test runs work that way until Phase 4.

| Tested | Result |
|---|---|
| Recomputed | identical |
| Track order changed; track list alone; one track's engine; the cap; one unit's provenance; `max_age_days` | each changes the hash |
| `config.PROFILE`, output dir, spend cap, partial approval | do not change it |
| Fresh interpreters: two names, two dirs, random hash seeds | same hash |
| Fresh interpreters: order swapped; an engine changed | different hash; same units |
| Stale hash, fake BYOK pool | exit 4; zero provider calls; no client built; **no file written** |
| Priced hash | runs all 15 searches |
| Real process, stale hash | exit 4; `PLAN_CHANGED` on stderr; the probe's Apify client never built |
| Real process, priced hash | gets past the check to the account read (exit 3 there, because the probe refuses clients) |
| Schema 1 with a garbage value set | runs normally |

## M. Partial Authorization Behaviour

- The existing `AccountPool.plan` / `authorize` / `placeable_prefix` runs on the merged plan, source-identical.
- Within a site every unit has the same ceiling, so the round-robin changes **which** units fit a partial prefix, never **how many**.

| Tested (BYOK fake pools) | Result |
|---|---|
| Full ($5 account) | 15/15 run |
| Refused ($0.30, partial not chosen) | exit 3, nothing started, no CSV |
| Partial ($0.30, partial chosen) | the first 6 units (all LinkedIn) start |
| Kill switch (`SWEEP_PUBLIC_PAID=0`) | exit 3; no account read, no client built |

**Per-track coverage under that partial** (shared units count for every requester):

| Track | Covered / planned |
|---|---|
| MERN | 2 / 6 |
| Mobile | 3 / 6 |
| AI | 2 / 6 |

- **Same count either way.** The placeable count is identical for the round-robin order and for track-by-track concatenation at $0.10, $0.30, $0.50, $0.70, $1.00 and $5.
- **Fairer mix.** At $0.30 the round-robin gives the worst-served track 2 units; concatenation gives it 1.
- Nothing promises equal coverage.
- **LinkedIn first.** $0.50 covers every LinkedIn unit and no Indeed unit, for all tracks alike.

## N. Checkpoint / Finalize Wiring

- `emit` under `TRACK_CONTEXTS`:
  - `finalize_multi(rows, TRACK_CONTEXTS)`;
  - `--only-new` as before;
  - `write_outputs(out, csv, json, MULTI_OUTPUT_COLUMNS)`.
- Schema 1: `finalize(rows[, memo])` and `write_outputs(out, csv, json)`, today's three-argument call byte for byte (§P says why that matters).
- The column list is an explicit argument, never inferred from the rows.
- Checkpoints happen when they always did: after every search, after the free phase, at the end. The results-ready marker is unchanged.
- One-profile scoring is never reached in a schema-2 run: `score_job` is patched to raise in every schema-2 run test.
- The shadow tranche is skipped for schema 2 (tested), because it judges with one profile's gate and scoring (design §I).

## O. Telemetry Decision

**Decision A: keep `telemetry.paid_outcome` and `paid_adaptive` off for schema 2.**

- **What they drive, from the code.**
  - `paid_outcome` records, per paid position, "lost_to" and "marginal" against `finalize`'s single survivor list.
  - `paid_adaptive` is V2-C4's shadow evaluator. Its own docstring: "ENFORCEMENT does not exist … the same starts, inputs, depths, reservations, requests, rows and bytes."
  - Neither affects spend, the plan, rows or bytes. Both are observation only, so their absence is safe.
- **Mechanism.**
  - `paid_outcome` is called only by `finalize`, so it was already off for schema 2 (2B).
  - `paid_adaptive.start` is handed no plan under schema 2, which keeps it off and resets any prior state.
- **What stays on.** Job-level telemetry: the stage funnel, counts, eligible progress, `paid_plan`, per-unit execution records and identity.
- **Tested.** The telemetry record of a schema-2 run has no `paid_adaptive` section, 15 `paid_units`, and **no track id anywhere**.
- Schema-1 telemetry is unchanged.

## P. Schema-1 Parity Results

| Check | Result |
|---|---|
| Phase 0a goldens: render, plan (site, keyword, location order, inputs, combo keys, ceilings, dry-run JSON), scoring, stages, `LAST_STATS`, dedupe, exports, cost, spend cap, coverage, full / partial / refused authorization | **35/35**; `git diff HEAD -- sweep/tests/fixtures/` empty |
| AST source identity of the frozen functions (§C) | identical |
| Dispatch: every schema-2 function patched to raise, schema-1 paid + free sweep | completes; `OUTPUT_COLUMNS`; no `_tracks` or `_free`; legacy done lines |
| Against `bcaaa55`, the same probe in both trees (§S) | **identical**: default-profile dry-run bytes (90 units), free-predicate rows over a 2,000-post board, and for 12-search paid + free sweeps (serial and C2) the output CSV bytes, `.done_combos`, and the provider call sequence and counts |

**A schema-1 regression the first full run caught, and its fix.**
- The first full Sweep run failed 35 existing crash and resume tests: V2-C2 `Checkpoints`, C4.5 `Crashes`, V2-B5 results-ready, V2-D resume.
- Cause: schema 1's `emit` passed `write_outputs` a fourth argument (`None`). Every existing 3-argument stand-in of `write_outputs` then raised `TypeError`. An outside wrapper would have broken the same way.
- Fix: schema 1 calls `write_outputs(out, csv, json)` exactly as before; only schema 2 passes `MULTI_OUTPUT_COLUMNS`.
- `SchemaOneDispatch` now runs a schema-1 sweep with a 3-argument stand-in, and the mutation run includes this fault.
- The same run failed `ProbeStaysBehindTheGuard.test_nothing_that_decides_reads_the_ledger`. That guard forbids the text `LEDGER` anywhere in `scraper.py`, and my constant was named `LEDGER`. It is now `DONE_ID`; the unit's key stays `_ledger`.
- Neither was visible to the goldens or the Phase 1–3 suites. Both were caught by the full run and fixed before this handoff.

## Q. Schema-2 One-Track Parity (internal proof, not the product path)

| Level | Compared | Result |
|---|---|---|
| Planner, in process | `multi_plans` with one track vs `plan_for_site`, across default, custom locations with `--limit 3`, `--test`, `--keywords`, Naukri enabled, LinkedIn companies with `remote_only` | identical units, private keys removed |
| Fresh interpreters | `render()` vs `render_sweep()` of the same synthetic résumé, for ada (India, remote, global custom, India + Naukri), chen (India) and bhaskar (India) | dry run identical (minus the name and schema-2 additions); every unit's search dict and built provider input identical, in order; `plan.cost`, spend cap and pool authorization (full, $1 partial, $0.04) identical |
| Whole run | one-track schema 2 vs schema 1 through `main()`, BYOK partial, paid + free | same provider starts (input and ceiling), the same authorization record, the same rows on `OUTPUT_COLUMNS`, the same `LAST_STATS`, the same done lines minus the ledger suffix |

The one intended difference is a hand-written profile that repeats a request, which runs once under schema 2 (§F).

## R. Full Multi-Track Offline Flow and Tests

**The flow** (`EndToEnd`, 3 tracks, serial, fake provider, a served free board, telemetry on):

| Aspect | Result |
|---|---|
| Plan | 15 unique requests from 18 track units (LinkedIn 8, Indeed 7) |
| Execution | each run once, in plan order, with the inputs the plan built |
| Rows | 18 paid rows, all stamped with their unit's requesters; 3 free rows stamped `_free` (the union gate kept 3 of 5 posts) |
| Checkpoints | 17 (15 searches, free, final) |
| Output | `MULTI_OUTPUT_COLUMNS`, 18 jobs |
| "React Developer at Probe" | acquired by MERN + Mobile's shared request, AI's own request, the Indeed request all three share, and the free board. `found_by` = `MERN;Mobile;AI;free`; `track_evals` = all three; best = MERN |
| Free-only jobs | "Android Developer": `found_by` `free`, evaluated only for Mobile. "Mobile Engineer": only for Mobile, because MERN's gate excludes it |
| Paid-only jobs | `found_by` = their unit's `_tracks` exactly; `best_track` among them |
| Himalayas | asked the first 8 union queries |
| `.done_combos` | the plan's ledger lines |
| Privacy | no credential in the CSV, the log or telemetry; no track id in telemetry |

**Test runs** (all in a minimal environment, `env -i PATH HOME LANG`):

| Run | Result |
|---|---|
| `sweep.tests.test_multi_plan` (new) | **52/52 OK** |
| `sweep.tests.test_multi_results` (Phase 2B) | **28/28 OK** |
| `sweep.tests.test_preference_filter_baseline` | **7/7 OK** |
| `sweep.tests.test_track_loading` (Phase 2A) | **18/18 OK** |
| `sweep.tests.test_scoring_context` (Phase 1) | **19/19 OK** |
| `sweep.tests.test_one_track_goldens` (Phase 0a) | **35/35 OK** |
| Plan / paid suites: `test_plan`, `test_runs`, `test_paid_contract`, `test_paid_plan_audit`, `test_indeed_bounded`, `test_paid_concurrency`, `test_paid_multi_account`, `test_paid_observability`, `test_paid_dev_guard`, `test_paid_adaptive`, `test_search_v2_d`, `test_results_ready_early` | **604/604 OK** |
| Full Sweep (`discover -s sweep/tests`) | **1,821 OK** (1,769 + 52), after the §P fix. The first run failed 35 existing tests (§P) |
| Worker/deploy (`deploy.test_sweep_worker`, `deploy.test_modal_benchmark`) | **42 OK** |
| Bench (`bench.test_backends`, `bench.test_answer_key`) | **43 OK** |
| auto-apply (`discover -s tests`) | **1,102 run, 2 errors**: the known `test_inference` `/healthz` `HTTPError 503` pair, untouched |
| `scraper.py --demo`, `merge_jobs.py --demo`, `config.py`, `python -m sources`, `python -m sweep.exports` | all ok |

Logs went to scratch files. Only summary lines were read, after a scan found no token-shaped string in any of them.

**`test_multi_plan.py` (52)**

| Class | Covers |
|---|---|
| RequestIdentity (4) | collapse with every requester; every read dimension splits; unread dimensions collapse; the key hashes exactly what `scrape_search` sends |
| Ordering (7) | round-robin; shared earliest slot, no lost turn; each track's own order; cap on the merged plan (flag and setting), requesters past the cap kept; site order; one track = `plan_for_site` (6 configurations); a hand-written repeat runs once, its track listed once |
| PaidProvenance (3) | 5 integration paths, every row, every checkpoint; each row its own list; a lost stamp never reaches a written checkpoint |
| FreeAcquisition (6) | union of own gates; shared location gate; one track = schema 1's rows and Himalayas requests; every free path stamped; schema 1's `fetch_free` by identity |
| Himalayas (3) | round-robin, exact dedupe, 8 in total; one track = schema 1's first 8; the entry handed to the feed, and none without keywords |
| Ledger (4) | shared → one line, different request → own line; the run writes them and a rerun skips all; schema-1 key; Render progress and pricing follow `ledger` |
| PlanHash (5) | binds what runs and nothing name- or authorization-dependent; dry-run shape additions only; stale → exit 4 before any account; priced → runs; schema 1 never reads it |
| Authorization (5) | full; refused; partial prefix with per-track coverage; within-site order keeps k; kill switch reads no account |
| EndToEnd (7) | the flow above, including the shadow tranche staying off |
| OneTrackRunParity (1) | §Q, whole run |
| DryRunStdout (3) | the Naukri note on stderr with valid JSON; schema 1 still prints it on stdout; an unmapped place refuses at plan time with nothing printed |
| SchemaOneDispatch (2) | no schema-2 function reached; `write_outputs` columns explicit |
| FreshInterpreters (3) | one-track dry run, inputs, cost, cap and authorization parity over 6 cases; the hash ignores names and paths and binds tracks and engines; a stale hash exits 4 in a real process |

**Mutation check (scratch, not committed).**
- 23 deliberate faults were injected one at a time into `scraper.py`. The file was restored byte-identical afterwards (hash verified).
- `test_multi_plan` caught **23/23**.
- The faults: the paid stamp removed or shared, concatenation instead of round-robin, the first requester only, a per-track cap, the hash ignoring engines or provenance, no expected-hash check, a union of excludes, no free stamp, the ledger ignored, Himalayas concatenated, `write_outputs` ignoring its columns, schema 2 emitting through `finalize`, no stdout redirect, the key ignoring the ceiling, the key built from fields, the Himalayas entry not replaced, schema-2 free acquisition through `fetch_free`, the legacy key dropping the company, requesters not deduplicated, the shadow tranche on for schema 2, and schema 1's `emit` passing a fourth argument.
- Two faults (undeduplicated requesters, the shadow tranche) survived the first round. The two assertions that catch them were then added.

## S. Performance Comparisons

**Schema 1 against `bcaaa55`.**

*Method.*
- A pristine `git archive` of `bcaaa55`, and this working tree.
- The same scratch probe (not committed) in fresh interpreters, alternating trees for 4 rounds each.
- Each round's figure is the median of 5 repetitions; the table shows the median of the 4 rounds.
- Minimal environment, sockets denied, no provider.

| Measure | `bcaaa55` | Phase 3 | Difference |
|---|---|---|---|
| Dry-run planning: `main()` `--dry-run --json`, config's own 90-unit plan (per call) | 0.243 ms | 0.241 ms | −0.8% |
| Free-predicate path: `fetch_free()` over a served 2,000-post ATS board (1,000 kept) | 18.55 ms | 17.75 ms | −4.3% (one 34 ms outlier in a `bcaaa55` round) |
| Offline paid + free sweep, serial loop: 12 LinkedIn searches × 15 rows + a free board, 14 checkpoints, 181 output rows | 355.6 ms | 295.3 ms | −17.0%: noise, not a speed-up. Two slow `bcaaa55` rounds (409, 439 ms); per-round minimum 288.5 vs 287.9 ms |
| The same sweep through C2 (2 workers) | 91.8 ms | 89.8 ms | −2.1% |

- An earlier run of the same probe, with onsite rows the default config drops (1 output row), gave: dry run −1.3%, free −0.6%, serial +1.5%, C2 −1.7%.
- Every difference is within run-to-run noise, in both directions. Phase 3 adds no measurable cost to the one-résumé path.

**Parity from the same probe, every round, both trees:**
- identical default dry-run JSON bytes;
- identical free rows;
- for the serial and the C2 sweep: identical CSV bytes, `.done_combos` lines, provider call sequence and call counts (12 starts, 12 polls, 12 dataset reads, 1 account read).

**Schema 2 (observational).**
- Planning a 3-track Sweep (the three synthetic personas, India): **3.95 ms** per `--dry-run --json`.
  - That covers 312 track units keyed, merged into 288 searches, and hashed.
  - The one-track schema-1 dry run of one persona takes 0.24 ms (median of 7 × 10 calls, fresh interpreters).
- Finalization cost is Phase 2B's, unchanged: at worst about 2.6× per checkpoint for three tracks, with memoised re-passes near zero.

**Synthetic scale (design R-2): the three committed personas as one India Sweep.** Dry runs only.

| | Unified 3-track | Three one-track plans |
|---|---|---|
| LinkedIn searches | 156 (none shared: the tracks' `f_E` bands differ, 5 / 11 / 0 years) | 156 |
| Indeed searches | 132 (24 shared by ada and chen) | 156 |
| Estimate | $16.09 | $18.25 |
| Bounded exposure | $25.00 | — |

## T. Remaining Phase-4 Requirements

1. **Worker contract.**
   - `/v1/plans` and `/v1/runs` accept `tracks` xor `profile`.
   - Render with `render()` for one track and `render_sweep()` for 2–3, with the explicit engine and the render projection.
   - **Carry `plan_hash`** on `/v1/runs`, required for a paid multi-track run, into the child as `SWEEP_EXPECTED_PLAN_HASH`. Make sure `default_spawn`'s environment scrub passes it through.
   - Map exit 4 to `plan_changed`.
   - Record `track_ids` in `status.json`.
2. **Phase 0b live and verified** before any multi-track run.
3. **Identical plan-shaping flags** for the dry run and the run. The worker and local `/run` pass none today; keep it that way.
4. **Render (Phase 5).**
   - Store `plan_hash` with the costed plan and send it with the run.
   - Build the per-track coverage statement ("covers x of y of this résumé's searches; n are shared") from the dry run's per-entry `tracks`. Only the tests compute it today; a `sweep/plan.py` helper belongs with the Confirm screen.
   - `runs.combo_key` already reads `ledger`.

## U. Risks and Open Questions

1. **`main()` no longer refuses schema 2.**
   - No production path produces schema 2, but the Phase 0b gate is now procedural.
   - A developer running `scraper.py --profile <schema-2 file>` with real keys would run a real Multi-Track sweep.
   - Verification runs should go through `bench.paid_guard`, as today.
2. **Plan size (R-2).** The synthetic 3-track India Sweep is 288 searches and a $16.09 estimate. With visitors' free accounts, partial sweeps become the norm.
3. **LinkedIn sharing needs equal `f_E` bands.** Tracks derived with different years share nothing on LinkedIn. Real multi-résumé candidates usually have one career, but each résumé's years are derived separately (design D.1 "ambiguous").
4. **Unset hash means unbound.** Phase 4 must make it required for paid multi-track runs. A worker deploy between Confirm and Run will refuse in-flight Confirms, which is correct and visible.
5. **Stamping is total through `scrape_search`.** A future paid path that bypasses it must stamp too; a miss fails every later checkpoint, closed.
6. **Bad geography now fails a schema-2 dry run** (`/v1/plans`) rather than the run's preflight. That is earlier and safer, but it is an error where schema 1 returns a plan.
7. **Developer flags under schema 2.**
   - `--test` gives each track's first unit (up to 3).
   - `--keywords` replaces every track's keywords (per-track years remain).
   - The worker passes neither.
8. **A merged unit keeps the first placer's unread fields** (for example Indeed `experience_years`). The request, key and label are unaffected, and nothing downstream reads the value: neither the dry-run JSON nor telemetry carries an experience field.
9. **Dry-run cost.** A schema-2 dry run builds every provider input once per track unit. For the synthetic 288-search Sweep that takes milliseconds.

Phase 4 and Phase 0b have not been started.
