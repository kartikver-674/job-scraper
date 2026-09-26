# Multi-Track Phase 2B — Handoff

Phase 2B builds the result side of Multi-Track, engine only.
`finalize_multi(rows, tracks)` takes copies that have already been acquired
and returns **one row per physical job**. Each row carries:

- the best track;
- every eligible track's own evaluation, each from that track's own best copy;
- the acquisition provenance of **every acquired copy** of the job.

Relevance comes only from explicit provenance on each copy. Nothing infers it.
Schema 1 is unchanged, and a one-track schema-2 Sweep decides exactly as
schema 1 does. `main()` still refuses to run a schema-2 Sweep.

Nothing is committed, pushed or deployed. No provider or model was called.
**Waiting for review.**

**Review correction (applied).** `found_by` is **acquisition** provenance: the union over every acquired copy of the physical job, including copies later dropped as ineligible, filtered by the Sweep, or beaten as duplicates. It is no longer computed from surviving copies only. Eligibility stays in `track_evals` alone, so a track can appear in `found_by` and be absent from `track_evals` (§G, §I, §M).

---

## Single-Résumé P0 Compatibility

Multi-Track is additive, and the single-résumé path is the primary, default one. A regression there blocks release, whatever Multi-Track does.

| Guarantee | How it holds |
|---|---|
| Schema 1 stays the default path | Nothing in Phase 2B routes a one-résumé user to schema 2. `finalize()` does not dispatch. `TRACK_CONTEXTS` is `()` for every schema-1 profile, and no web, worker or render caller writes schema 2 |
| No Multi-Track metadata there | Schema-1 rows are exactly `OUTPUT_COLUMNS`, with no `best_track`, `track_evals` or `found_by` (tested). No `_tracks`, `_free` or `_facts` key is written on the schema-1 path |
| No Multi-Track evaluation there | `finalize` → `score_and_filter` → `score_job` → `default_context()`, as in Phase 1. The only shared code is the verbatim-moved `_sweep_filters` and `title_admits`, which `is_dev_title` delegates to unchanged |
| Search and provider behaviour untouched | No change to planning, `build_input`, `effective_search`, `combo_key`, placement, the scheduler, `AccountPool`, providers or free acquisition. The plan and authorization goldens are unchanged |
| Ordinary outputs unchanged | Phase 0a goldens 35/35: render, plan, scoring, stages, `LAST_STATS`, dedupe, engine CSV/JSON, CSV/JSON/HTML/XLSX exports. No schema-1 fixture changed (`git diff HEAD -- sweep/tests/fixtures/` is empty) |
| Default-path performance | Below |

**Schema-1 benchmark vs `e58003c` (the Phase 2A commit).**
- The same script and the same 720 synthetic JD-length rows, config's own tables.
- Run against a pristine `git archive` of `e58003c` and against this working tree, alternating 4 rounds each.
- Each round's figure is a median of 5 repetitions; the table shows the median of the 4 rounds.

| Measure | `e58003c` | Phase 2B | Difference |
|---|---|---|---|
| v1 `finalize` | 1,684.0 ms (2.34 ms/row) | 1,688.0 ms (2.34 ms/row) | +0.24% |
| v2 `finalize` | 2,627.1 ms (3.65 ms/row) | 2,609.2 ms (3.62 ms/row) | −0.68% |
| v1 `score_job` | 1,683.8 ms | 1,684.5 ms | +0.04% |
| v2 `score_job` | 2,615.2 ms | 2,619.4 ms | +0.16% |
| memoised `finalize` pass (both engines) | 1.4 ms | 1.4 ms | none |

Every difference is within run-to-run noise, in both directions. Multi-Track adds no measurable overhead to the one-résumé path.

---

## A. Starting Git State

| | |
|---|---|
| Branch | `feat/multi-track-search` |
| HEAD | `e58003c` (`feat(multi-track): add schema-2 track loading`) |
| Upstream | `origin/feat/multi-track-search` at `cb602ec`; local is 2 ahead, 0 behind |
| Working tree | clean |

## B. Production Files Changed

**Production:** `scraper.py` only (+243 / −6).

| Change | What |
|---|---|
| `title_admits(title, hints, excludes)` | `is_dev_title`'s rule over any gate. `is_dev_title` now delegates to it, with identical behaviour |
| `_sweep_filters(scored, stage)` | The Sweep-preference filters moved **verbatim** out of `score_and_filter`, which now calls it. The diff is only the new `def` line and a `return` |
| `default_context()` refusal text | Now names `finalize_multi` as the schema-2 path. It keeps "multi-context finalization not enabled", which the Phase 2A test checks |
| New: provenance and results | `PAID_TRACKS` / `FREE` / `FACTS`, `MULTI_COLUMNS` / `MULTI_OUTPUT_COLUMNS`, `LAST_TRACK_STATS`, `Cluster`, `provenance()`, `relevant_tracks()`, `score_and_filter_multi()`, `rank_clusters()`, `finalize_multi()` |

Not touched: `finalize`, `score_job`, `rank_rows`, `to_output`, `write_outputs`, `main` (still refusing), `build_search_plan`, `plan_for_site`, `build_input`, `effective_search`, placement, the scheduler, `AccountPool`, `combo_key`, providers, routes, templates and exports.

**Tests and docs**

| File | What |
|---|---|
| `sweep/tests/test_preference_filter_baseline.py` | **new**, 7 tests, **written and passing against the unmodified code before the filters moved**. They pin the visa, EOR, restricted-remote rescue and unreachable branches that no golden covered |
| `sweep/tests/test_multi_results.py` | **new**, 28 tests (§M) |
| `MULTI_TRACK_PHASE_2B_HANDOFF.md` | **new**, this file |

## C. Multi-Context Entry Point

```python
finalize_multi(raw_rows, tracks)          # schema 2; tracks = scraper.TRACK_CONTEXTS
  └ score_and_filter_multi(raw_rows, tracks, stage=None)   # copies eligible for >= 1 track, filtered
  └ rank_clusters(copies, tracks, raw_rows)                # one Cluster per physical job, ranked;
                                                           # found_by from every acquired copy
```

- `finalize()` does **not** dispatch. It stays the schema-1 path, byte for byte. In a schema-2 process it still refuses, through `default_context()`.
- `score_job(row)` keeps refusing under schema 2: "which track?" has no answer.
- Why no dispatch:
  - The only real caller is `main()`'s `emit`, which is blocked.
  - `rescore_from_apify` and the shadow engine read rows that carry no provenance, so under schema 2 they correctly refuse.
  - Phase 3 wires `emit` to `finalize_multi` explicitly.

## D. Provenance and Relevance Contract

**What the code shows today, before this phase:**
- Paid and free rows meet in one list, `raw_rows`, in `main()`:
  - paid rows are appended per search (`scraper.py` serial loop, and `paid_phase_c2` integrate);
  - then `raw_rows.extend(fetch_free())`;
  - every checkpoint runs `emit(raw_rows)` → `finalize`.
- **No reliable paid/free marker exists on a row.**
  - Paid rows carry `Source` = the site key, plus `search_query` ("keywords @ location", a display label) and `search_rank`.
  - Free rows carry `Source` = `platform:token` or a feed name.
  - The paid unit id reaches a row only while telemetry is on (`PAID_UNIT`).
  - Nothing records which track requested a row.
- Descriptions are present at finalize. `to_output` drops them.
- So Phase 2B **does not guess**, from `Source`, the URL or an empty `search_query`. Every copy must carry explicit provenance.

**The contract Phase 3 must satisfy.** Exactly one of these on every copy of a multi-track Sweep:

| Key | Meaning | Rules |
|---|---|---|
| `_tracks` (`scraper.PAID_TRACKS`) | A paid copy: the ids of the tracks whose request fetched it, in requesting order | A non-empty list of strings. Every id must belong to this Sweep. Repeats count once |
| `_free` (`scraper.FREE`) | A free copy, answering no track's query | Exactly `True` |

These are refused with a `ValueError`, which fails the whole pass:
- neither key, or both;
- `_tracks` that is empty, not a list, or has non-string or unknown ids;
- `_free` that is not `True`.

There is **no** "all tracks", "track 0" or score-based fallback. Ids only: no labels, filenames or credentials.

**Relevance (track order):**
- A paid copy is relevant only to the tracks in its `_tracks`.
- A free copy is relevant to each track whose own gate admits its title: `title_admits(title, track.title_hints, track.title_exclude)`, which is `is_dev_title`'s semantics (excludes win, substring hints).
- A copy relevant to no track is not kept.

**Relevance is not eligibility.** `relevant_tracks()` decides who may judge a copy. `evaluate()` then decides eligibility and score. "Not relevant" never appears as a score or an entry.

## E. Per-Copy Evaluation Flow (`score_and_filter_multi`)

For each copy, in arrival order:

1. **Relevant tracks** (§D).
2. For each relevant track, the Phase-1 `evaluate(row, track.scoring, facts)`:
   - `JobFacts` is computed **once per copy** and cached on it (`_facts`).
   - The evaluation is memoised on the copy per context key (`_evals`, the Phase-1 memo).
   - Nothing is written that belongs to one track.
3. **Eligible** means `evaluation.eligible` and, when set, `score >= SETTINGS["min_score"]`, a per-track test.
4. A copy with **no eligible track is discarded**. Other tracks are unaffected when one drops a copy.
5. A kept copy gets its **job-level** facts written onto it: `years_required` and the enrichment (remote scope, regions, visa, EOR, timezones, tz gap, `remote?`, contacts). These are identical for every track, and the filters and `to_output` read them from the row.

The memo and facts cache make a repeated checkpoint pass nearly free (§N). Their validity argument is Phase 1's: they read only acquired fields.

## F. Sweep-Wide Filters

- They run **once per kept copy**, after evaluation, through `_sweep_filters`. That is the very function `score_and_filter` runs: recency, pay, reachability and rescue, visa, EOR, work arrangement, India geography.
- The same stage names and counts go to the `stage` hook.
- Order is job facts → per-track evaluation → keep if any eligible → Sweep filters.
- The filters are conjunctive and job-level, so this order changes counts only, never the survivors.
- For one track it is exactly schema 1's order, and the one-track parity test proves the funnel counts are equal.

## G. Cluster and Dedupe Algorithm (`rank_clusters`)

- Only surviving copies are clustered, and only **after** every relevant track has evaluated them.
- They group by the existing `job_key`. A copy with no key is a cluster of its own, as in `dedupe`.
- **Per track**, `chosen_T` is the eligible copy with the highest T score, ties to the earliest arrival. That is today's survivor rule applied per track, so tracks may choose different copies.
- **Acquisition provenance** is gathered first, over **every** acquired copy:
  - `provenance(row, tracks)` validates each copy and gives its paid ids or `free`.
  - It is unioned per `job_key` across all copies, whether kept, ineligible, filtered or beaten.
  - A surviving cluster takes the union for its `job_key`. A keyless copy is a job of its own and carries only its own provenance.
  - Only surviving copies form clusters, so a job whose every copy failed leaves **no** result: provenance alone never makes one, and discarded copies are never persisted.

## H. Best Track, Representative, Rank

| Step | Rule |
|---|---|
| Best track | The track whose chosen copy scores highest; ties go to track order (`TRACK_CONTEXTS`, which is the file's `TRACKS` order) |
| Representative | The best track's chosen copy. Its ordinary fields become the row's columns |
| Losing tracks | Keep **their own** copy's score. They are never re-scored against the representative (tested) |
| Cluster order | `(-best score, representative's arrival index)` |

For one track this is `rank_rows`: a stable sort by score, then first per `job_key`.

## I. Result Schema

Each multi-track output row is `to_output(representative with the best track's score, matched_skills and is_fullstack)` plus appended columns. `MULTI_OUTPUT_COLUMNS = OUTPUT_COLUMNS + MULTI_COLUMNS`, appended and never inserted.

| Column | Value |
|---|---|
| `best_track` | the best track's id |
| `track_evals` | compact, deterministic JSON `{"<id>": {"s": score, "m": "matched skills"}}` for **every eligible track** (each from its own chosen copy), in track order. Ineligible and irrelevant tracks are absent |
| `found_by` | `;`-joined **acquisition** provenance: the ids of tracks whose paid requests fetched **any** acquired copy of this job (kept, ineligible, filtered or beaten), in track order, then `free` if any free copy was acquired. It says which searches surfaced the job, never which tracks it suits: a track can be in `found_by` and absent from `track_evals` |

Example (real output from the tests; ids are synthetic):

```text
best_track  = a1a1a1a1a1a1a1a1
track_evals = {"a1a1a1a1a1a1a1a1":{"s":19,"m":"React, Node.js, MongoDB, TypeScript"},"b2b2b2b2b2b2b2b2":{"s":2,"m":"TypeScript"}}
found_by    = a1a1a1a1a1a1a1a1;b2b2b2b2b2b2b2b2
```

- There is no description, label, filename or résumé content, and no private key reaches a row: the exact column list is tested.
- The in-memory richer result (each track's chosen copy, its `is_fullstack` and its arrival index) is the `Cluster` from `rank_clusters`.
- `write_outputs` still writes `OUTPUT_COLUMNS`. Writing multi rows needs `MULTI_OUTPUT_COLUMNS`, and that belongs to Phase 3/4, when `main` can produce multi rows. The CSV round trip with those columns is tested.
- Download exports are unchanged (Phase 6).

## J. Stats, Guard and Telemetry

- **`LAST_STATS`**: `finalize_multi` fills the same job-level keys `finalize` does (`stale`, `low_salary`, `kept` = clusters, `unreachable`, `rescued`, `no_visa`, `no_eor`, `wrong_arrangement`, `off_geography`). There are no track keys in it.
- **`LAST_TRACK_STATS`** (multi-track only, ids only):
  - `copies`: entering evaluation;
  - `eligible_copies`: eligible for at least one track, before the Sweep filters;
  - `eligible_by_track`: `{id: n}`;
  - `clusters`.
- **Experience guard**: a multi-track verdict is recorded as `dict(verdict, track=<id>)`. That is additive; `summary()` ignores the extra key.
  - It is recorded **once per (copy, track)**, when first evaluated.
  - Schema 1 is untouched: its wrapper records per scoring call, and its memo stays off while the guard is on.
- **Telemetry**:
  - `finalize_multi` calls only the job-level, track-free hooks: the `stage` funnel, `stage("final_after_dedupe")`, `counts` and `eligible_progress`.
  - It does **not** call `telemetry.paid_outcome` or `paid_adaptive.capture`. Their "survivor versus beaten" semantics assume one survivor rule, and a copy can now be one track's chosen copy without being the representative.
  - Defining them for multi-track is Phase 3/4 work.
  - No résumé-derived value reaches telemetry.

## K. Schema-1 Compatibility

| Check | Result |
|---|---|
| Phase 0a goldens | **35/35** unchanged: scoring, stages, `LAST_STATS`, dedupe, exports, plans |
| Phase 1 tests | **19/19** |
| Phase 2A tests | **18/18** |
| Filter baseline | **7/7**, before and after the extraction |
| Code paths | `finalize`, `score_and_filter` (now calling the moved filters), `rank_rows`, `to_output`, `OUTPUT_COLUMNS` and `score_job` are schema 1's. Schema-1 rows never get multi-track columns (tested) |

## L. Schema-2 One-Track Parity (`OneTrackParity`, fresh interpreters)

The same résumé runs through both paths, under **v1 and v2**:

| | |
|---|---|
| Schema 1 | the golden scoring profile via `render()` |
| Schema 2 | a one-track `render_sweep()` of the same derivation, preferences and engine |
| Rows | the 18 Phase 0a scoring rows, plus a free second copy of a paid job. Paid rows carry `_tracks=[id]`, free rows `_free=True`, and schema 1 sees the rows its title gate admits, as acquisition would |

| Compared | Result |
|---|---|
| Every `OUTPUT_COLUMNS` field of every row, in order | equal |
| `LAST_STATS` | equal |
| Every stage count | equal |
| `best_track` | always the one track |
| `track_evals` | holds exactly that track |
| `found_by` | includes `free` |
| In the same schema-2 process | `main()` still refuses before planning |

## M. Tests and Results

All runs used a minimal environment (`env -i PATH HOME LANG`).

| Run | Result |
|---|---|
| `sweep.tests.test_multi_results` (new) | **28/28 OK** |
| `sweep.tests.test_preference_filter_baseline` (new) | **7/7 OK** (also before the extraction) |
| `sweep.tests.test_track_loading` (Phase 2A) | **18/18 OK** |
| `sweep.tests.test_scoring_context` (Phase 1) | **19/19 OK** |
| `sweep.tests.test_one_track_goldens` (Phase 0a) | **35/35 OK** |
| `scraper.py --demo`, `merge_jobs.py --demo` | `demo ok` |
| Full Sweep | **1,769 OK** (1,734 + 28 + 7) |
| Worker/deploy | **42 OK** |
| Bench | **43 OK** |
| auto-apply | **1,102 run, 2 errors**: the known `test_inference` `/healthz` `HTTPError 503` pair |

**`test_multi_results.py`**

| Class | What it covers |
|---|---|
| Relevance (4) | Paid rows go only to their `_tracks` (track order); free rows go to each track whose gate admits them, and an exclude wins; repeats count once; neither/both/empty/string/unknown/non-True provenance is refused, and fails the whole pass |
| Evaluation (8) | Relevance vs eligibility vs score (MERN-only, AI-only, too senior for A, eligible only for B); A-wins and B-wins keep both evaluations; each score equals that track's direct `evaluate`; `JobFacts` once per copy across three tracks and two passes; track order and process engine are irrelevant (`bind` patched to raise); nothing one track owns is written to a copy; `min_score` per track; guard verdicts labelled and recorded once |
| SweepFilters (2) | Stale, low pay, remote-under-India and abroad copies each dropped once, with counts once; remote reachability |
| Clusters (5) | Two copies where A prefers copy 1 and B prefers copy 2: one result, each track's own copy, representative from the best track, losing score not recomputed; company/title duplicate tie goes to arrival; best-track tie goes to track order (both orders); rank; `found_by` order |
| ResultRows (3) | Exact `MULTI_OUTPUT_COLUMNS`; `best_track` in `track_evals`; only `{"s", "m"}`; no description text; deterministic JSON; CSV round trip; `LAST_TRACK_STATS` |
| AcquisitionProvenance (4) | A discarded paid copy (A too senior) still puts A in `found_by` while A is **absent from `track_evals`**; a Sweep-filtered free copy still adds `free`; a job whose every copy fails, and a failing keyless copy, leave no result; every arrival order of three copies gives `found_by` in track order, then `free` |
| OneTrackParity (1) | §L |
| SchemaOneUntouched (1) | `finalize` rows are exactly `OUTPUT_COLUMNS` |

## N. Performance

Scratch benchmark: 720 synthetic JD-length rows, config's own tables in every track, engine v2. Worst case: every copy is relevant to every track.

| Run | Total | Per row |
|---|---|---|
| Legacy `finalize` | 2,250 ms | 3.12 ms |
| `finalize_multi`, 1 track | 2,246 ms | 3.12 ms |
| `finalize_multi`, 3 tracks | 5,801 ms | 8.06 ms (≈ 2.6×; facts, enrichment and filters are shared) |
| 3 tracks, memoised re-pass | 3 ms | 0.005 ms |

`job_facts` was called **720 times for 720 copies** across two three-track passes, so reuse works. Nothing is pathological, and nothing was optimised.

## O. Remaining Phase-3 Requirements

1. **Stamp provenance on every row of a schema-2 run.**
   - `_tracks` on paid rows, from the unified plan unit's requesters (deduped, requesting order).
   - `_free = True` on every row `fetch_free` returns.
   - Schema-1 runs stamp nothing.
2. **Wire `emit`**: under `TRACK_CONTEXTS`, `finalize_multi(rows, TRACK_CONTEXTS)` and `write_outputs(..., MULTI_OUTPUT_COLUMNS)`. Only then lift `main()`'s refusal, together with:
   - the unified plan (per-track intents, `request_key`, round-robin, provenance);
   - the union free-acquisition predicate and the Himalayas union;
   - the ledger and the plan hash.
3. **Define multi-track semantics** for `telemetry.paid_outcome` and `paid_adaptive.capture`, or keep them off for schema 2.
4. **Phase 0b** must be live before any multi-track run.

## P. Risks and Open Questions

1. **A missing stamp crashes the pass**, and in a run that means the checkpoint `emit`. That is deliberate and fail-closed. Phase 3 must make stamping total, and test it with a fake provider.
2. **Resolved in review: `found_by` counts every acquired copy.** The union is taken per `job_key` over all acquired copies, as design §L's "any copy" intends. A keyless copy's provenance is its own, so a surviving keyless job never borrows another copy's.
3. **Broad free relevance.** With the candidate gate off, every track's hints include config's generic floor, so most developer titles are relevant to every developer track (design §J risk). That is intended, and it makes views longer.
4. **Memory.** `_facts` keeps a `JobFacts` (with a copy of the row's fields) on every copy for the run's life, roughly doubling per-row memory for kept rows. Fine at today's volumes; watch it at thousands of rows.
5. **Guard records** are once per (copy, track) in multi-track, but once per scoring call in schema 1, where it is off by default. Different by design.
6. **Unsupported under schema 2:** `rescore_from_apify` and the shadow tranche. Both refuse through `default_context()`. The design keeps shadow off for multi-track, and a rescore cannot recover provenance from raw datasets.
7. **Best-track ties** follow `TRACKS` order. Phase 5 must write `TRACKS` in the user's display order.

Phase 3 has not been started.
