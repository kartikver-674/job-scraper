# Search Engine V2-A — telemetry, WWR correctness and shadow evaluation

Implementation date: 2026-09-22. Baseline: `a43588c` (the audit commit).
Audit this stage acts on: [engine forensic audit](search-engine-v2-forensic-audit.md),
[Free source expansion audit](free-source-expansion-audit.md).

Two flags were added, **both default OFF**, and one production correctness fix
was made. Nothing was deployed. No paid actor was run and no Apify credit was
spent. Profile Engine V3, résumé extraction, role generation, candidate title
gates, skill weighting, ranking formulas, Search Preferences, location, recency,
salary and avoid-term semantics, `SWEEP_EXPERIENCE_MISMATCH_GUARD` and
production dedupe are all untouched. The source registry is unchanged at 134
active records (129 ATS boards + 5 feeds).

Evidence classes are the audit's: **MEASURED** (a reproducible experiment or a
dated probe), **VERIFIED** (directly observed code or current provider
documentation), **INFERRED** (a model with stated assumptions), **UNKNOWN**
(evidence unavailable, which is not the same as zero).

## 0. Headline findings

Three of these change what V2-B should do.

1. **VERIFIED, re-checked live 2026-09-22 — the LinkedIn contract drift has a
   money consequence, and it is worse than the audit could state.** Sweep sends
   `count`. The published schema has no `count`; it has `limitPerSource`, and it
   documents that **omitting the limit returns up to ~1,000 jobs per search
   URL**. At $2/1,000 results that is ~$2.00 for a search Sweep models at
   $0.027 — about **74×** — and the spend guard checks only *after* a run
   returns. `autoConvertToAiSearch` is documented as enabled by default, which
   also turns Sweep's `f_WT=2`/`f_E`/`f_TPR` URL filters into natural-language
   keywords rather than enforced filters. Whether today's actor build *ignores*
   an unknown `count` is **UNKNOWN** and needs one approved paid run to settle
   (§13, §15). No paid behaviour was changed in this stage.
2. **MEASURED — SmartRecruiters depth past row 100 buys inventory, not
   relevance.** Rows 101–400 across five current boards tripled normalized rows
   (500 → 1,503) and eligible rows (7 → 23), and produced **zero**
   positive-score jobs at either depth. Later pages are also mostly *stale*:
   three of five boards returned zero rows inside the 14-day window beyond page
   two. Depth is entangled with the fact that SmartRecruiters list rows carry no
   description, so every row is scored on its title alone (§11).
3. **MEASURED — the eight-board shadow tranche reproduces the audit's deltas
   exactly, and its value is concentrated in three boards.** +15 final/+3
   positive for full-stack India, +26/+6 remote, +15/+2 React Native, +15/+1
   business/Salesforce — identical to the audit. But in the full-stack India
   fixture only Fivetran, Abnormal Security and Apollo.io produce any
   positive-score job, and four of the eight produce no eligible row at all
   (§10).

Incidental, found while running the verification this stage required:

4. **VERIFIED — `python -m sources --live` has been failing on a dead probe
   board.** Its Greenhouse probe was hardcoded to `postman`, the token the audit
   measured as 404. The self-check aborted before reaching any of its later
   assertions. Fixed by moving the probe to `greenhouse:gitlab`. `config.ATS_BOARDS`
   was **not** touched: whether Postman's token is dead or merely migrated is a
   registry decision, and the audit is explicit that one 404 is not grounds for
   deleting an employer.
5. **MEASURED — three provider-native identity fields this stage first guessed
   at do not exist.** An early draft of the identity map declared Ashby
   `updatedAt`, SmartRecruiters `lastUpdatedOn` and Breezy `updated_date`. All
   three resolved on 0 rows against live responses. They are removed, and the
   live probe now refuses to report a zero as a finding without flagging it as a
   possible wrong path (§14).

## 1. Files and functions instrumented

| File | What changed |
|---|---|
| `telemetry.py` **(new, 495 lines)** | The whole module. `start` / `unit` / `observed` / `failed` / `partial` / `retried` / `request` / `paid_run` / `mark` / `eligible_progress` / `stage` / `identity` / `counts` / `note` / `finish` / `record` / `failure_category`, plus `demo()`. |
| `sources/shadow.py` **(new, 99 lines)** | `BOARDS`, `enabled()`, `run()`. The eight-board shadow tranche. |
| `scraper.py` | `finalize()` — seven `telemetry.stage` boundary reads plus `counts`/`eligible_progress`. `fetch_free()` — the shadow hook. `scrape_search()` — actor/run/build/dataset IDs, poll count, status, timestamps. `main()` — `telemetry.start`, free-phase marks, per-paid-search work unit, `identity`, `finish` (including on the zero-row exit). `demo()` — the passivity assertion. |
| `sources/__init__.py` | `fetch_free()` — one work unit per ATS board and per feed, and `telemetry.failed` inside the existing per-source `except`. |
| `sources/ats.py` | New `NATIVE` identity map. `_row()` captures it under the flag. `fetch()` reports raw/normalized/gated. |
| `sources/feeds.py` | **WWR fix**: `WWR_WHOLE_BOARD`, `_wwr_url()`, per-feed isolation in `wwr()`. New `NATIVE` map for the JSON feeds, captured in `_json_rows()`, `remoteok()` and inline in `wwr()`. Counts in all three. |
| `sources/_http.py` | `telemetry.retried()` in both retry loops — the only frame that knows a retry happened. |
| `sources/__main__.py` | `wwr_offline()`, `native_offline()`, native-availability reporting in `live()`, and the dead probe board fix. |
| `deploy/sweep_worker.py` | One line: `env["SWEEP_RUN_ID"] = run_id`, the join key between a worker status file and a telemetry record. Inert unless telemetry is on. |
| `sweep/tests/test_search_v2_telemetry.py` **(new, 26 tests)** | Non-regression. Runs with the existing suite. |
| `bench/search_v2_shadow.py`, `bench/search_v2_smartrecruiters_pages.py`, `bench/search_v2_telemetry_overhead.py` **(new)** | Audit-only harnesses. No production code imports them. |

## 2. Telemetry schema

`SWEEP_SEARCH_V2_TELEMETRY=1` writes one JSON file per sweep to
`<output_dir>/telemetry/sweep_<sweep_id>.json`. Schema id `search-v2a.1`.

Sweep level:

```
schema, sweep_id, worker_run_id, path, profile, engine_revision, output_dir,
started_at, finished_at, duration_ms,
sources_attempted, sources_succeeded, sources_failed,
raw_rows, normalized_rows, eligible_rows, final_rows,
milestones{}, milestone_granularity{},
units[], shadow_units[], stages{}, identity{}, notes[]
```

Work unit (`units[]`, one per ATS board, per feed, per paid search):

```
path, family, board, query, location, country, requested_limit, timeout_s,
started_at, finished_at, duration_ms, ok, failure_category, failure,
requests, retries, raw_count, normalized_count, source_gate_count,
shadow, partial_failures[]
```

Paid units additionally carry, via the allowlist in `telemetry.paid_run`:

```
actor_id, actor_build_id, actor_run_id, dataset_id, actor_status,
actor_started_at, actor_finished_at, dataset_retrieved_at, poll_count,
estimated_cost_usd, reported_cost_usd, billed_delta_usd
```

`stages{}` holds `{total, by_source{}}` at each boundary `finalize()` already
crosses: `observed_normalized`, `post_hard_filter_and_score`, `post_recency`,
`post_salary_reachability_visa_eor`, `post_arrangement`, `post_location_eligible`,
`final_after_dedupe`.

`identity{}` holds `sweep_unique_keys` and, per source, `rows`, `source_unique`,
`newly_unique_vs_sweep`, `unkeyed` and a `native{}` count of how many rows
carried each provider-native field.

## 3. Metric definitions

Precision here matters more than coverage: the audit's central complaint is that
different denominators were being compared.

| Metric | Definition |
|---|---|
| `raw_count` | Items the endpoint returned, before any mapping. Per source only. |
| `normalized_count` | Items that mapped into the internal row schema. |
| `source_gate_count` | Rows that passed the caller's title **and** location acquisition predicates — the gate that lives inside the adapters. |
| `raw_rows` (sweep) | Sum of every unit's `raw_count`. Derived in `finish()`, because the sweep itself never holds a raw row: adapters normalize before returning. |
| `normalized_rows` (sweep) | Rows the engine accumulated, i.e. `source_gate_count` summed — what `finalize` receives. |
| `eligible_rows` | Rows surviving score + every hard filter + recency + arrangement + geography, **before** dedupe. |
| `final_rows` | After dedupe. The engine's answer. Not the count a user sees: display filters run later (`sweep/logic.py:801`). |
| `duplicate_count` | Per source, `post_location_eligible` minus `final_after_dedupe` — rows this source lost at dedupe. It does **not** say which source won. |
| `source_unique` | Distinct current-engine `job_key`s within that source's contributed rows. |
| `newly_unique_vs_sweep` | Keys this source contributed that no earlier source in registry order had. Order-dependent by construction; it is a marginal-yield measure, not a merit ranking. |
| `first_raw` | First moment any source returned at least one row. |
| `first_eligible` / `first_5_eligible` / `first_20_eligible` | First **checkpoint** at which the finalized count crossed 1/5/20. |
| `failure_category` | One of `http_<code>`, `timeout`, `transport`, `schema_or_parse`, `other`. Coarse on purpose. |
| `partial_failures[]` | A sub-request of a surviving unit failed — e.g. one WWR category of eight. |

## 4. Metrics that are unavailable, and why

Each of these was asked for. None was obtained by restructuring execution.

| Metric | Status | Why |
|---|---|---|
| Free `first_eligible` at row granularity | **PARTLY UNAVAILABLE** | **VERIFIED:** Free holds every row in adapter/collector memory until the last source returns, then calls `emit` once. There is no per-board finalize to observe. Recorded at checkpoint granularity and labelled `milestone_granularity: "checkpoint"`. On a free-only sweep all three eligible milestones collapse to the same instant, which the field states rather than hides. Fixing this means incremental persistence — audit option I, not this stage. |
| Queue-entered / process-started timestamps | **UNAVAILABLE in-engine** | The worker owns them. `SWEEP_RUN_ID` now makes the join possible; reading the worker's status file from inside the engine would invert the dependency. |
| Browser TTFR | **UNAVAILABLE** | Needs a UI event. File mtime is not UI visibility — the audit says so and it is still true. |
| Per-row rejection reason | **UNAVAILABLE** | `finalize` filters with list comprehensions and keeps counts, not reasons. Per-row reasons need a rejection log threaded through every predicate. Per-source counts at each boundary give the funnel without it. |
| `post_arrangement` for a sweep whose `work_scope` is neither `india` nor `global` | **NOT RECORDED** | The filter does not run. An absent stage means the boundary did not exist, never that it passed everything. |
| `estimated_cost_usd` per paid unit | **NOT WIRED** | `sweep/plan.py:34` computes the estimate at *plan* level, before the engine starts; the engine never receives a per-combo figure. `reported_cost_usd` and `billed_delta_usd` are recorded because the engine already computes both. |
| `actual_cost` per run | **UNKNOWN by provider** | Only `usage_total_usd` (self-reported, measured to undercount ~3×) and the account-wide delta are available. Both are recorded, separately and labelled. `chargedEventCounts` is not exposed by the current call path. |
| Actor startup vs running split | **UNKNOWN** | `actor_started_at` / `actor_finished_at` come from the Run object; the transition into RUNNING is not polled. `poll_count` bounds the detection lag at 5s per poll. |
| Response bytes per source | **NOT WIRED** | `sources/_http.get_bytes` returns the body and discards the length. Adding it is one line but it belongs with a bandwidth question nobody is asking yet. Byte totals exist in the audit's own probe evidence. |

## 5. Privacy

**WHAT GOES TO LOGS** — nothing new. Console output is unchanged except two
added lines: the shadow tranche's per-board counts (only when the shadow flag is
on) and the telemetry file path (only when the telemetry flag is on). Paid
sweeps already send stdout to `DEVNULL` in the worker.

**WHAT GOES TO PER-RUN DIAGNOSTICS** — the one JSON file per sweep described in
§2: source names, board tokens, integer counts, durations and timestamps,
failure categories, bounded provider error strings, actor/run/build/dataset IDs,
and the paid search's keyword and location strings.

**WHAT IS PERSISTED** — that file, under the sweep's own output directory, which
already holds the results CSV/JSON and shares their lifecycle and the worker's
48-hour TTL. Nothing is sent anywhere.

**NEVER PERSISTED**, by construction rather than by intention:

- The Apify token. `paid_run()` writes only from a fixed allowlist and silently
  drops every other key, so passing a token to it stores nothing —
  asserted in `telemetry.demo()` and in
  `test_record_holds_no_description_or_credential`.
- Résumé text, the derived profile payload, full job descriptions, job titles,
  company names or apply URLs. The record holds no row, only counts of rows.
  The test above puts a marker string in every description and asserts it is
  absent from the written file.
- Any string over 200 characters: every recorded string passes through `_clip`,
  so a provider error cannot become a payload sink.

The paid keyword and location strings *are* recorded. They are user-chosen
search terms, already printed to the console today, and the audit's own
telemetry proposal permits "public query/location only as necessary". If that is
judged too much, `telemetry.unit(query=..., location=...)` is the single place
to hash them.

## 6. Runtime overhead

**MEASURED**, [evidence](search-v2-evidence/telemetry-overhead.json), three
alternating passes per arm over the frozen 12,533-row census snapshot
(3,099 rows after the acquisition gate, 82 final):

| | Telemetry OFF | Telemetry ON | Difference |
|---|---:|---:|---:|
| Median wall | 14.5762 s | 14.5661 s | −0.0102 s (noise) |
| Median process CPU | 14.4194 s | 14.4583 s | **+0.0389 s (+0.27%)** |
| Files written | 0 | 1 | +1 |
| Record size | — | 14,773 bytes | — |

`result_set_identical_across_every_pass: true` across all six passes.

The honest denominator is stated in the artifact: this is local scoring/filter
work only. A real sweep is dominated by network time that telemetry does not add
to, so its share of a whole sweep is *smaller* than +0.27%. The wall-clock
difference came out negative, which means the true cost is below this host's
scheduling noise — the CPU figure is the one to quote.

The record grows with **source** count, not row count: the funnel stores
per-source integers and never a row. 14.8 KB for 134 sources.

**What the milestones bought immediately.** The §18 end-to-end run — four
sources, five final rows — recorded `first_raw: 2,460 ms` against
`first_eligible: 56,224 ms`. The first useful row was in memory at 2.5 s and the
first eligible result was not persisted until 56 s, because Free waits for every
source before it finalizes anything. That is the audit's **VERIFIED** claim about
Free TTFR, now with a number attached, on a four-source sweep. On 134 sources the
gap is larger. This single pair of numbers is the strongest argument for
incremental persistence (audit option I) and it did not exist before this stage.

## 7. WWR root cause

**VERIFIED from code and MEASURED by the audit.** Two independent defects, in
one function, whose effects compounded.

1. **A wrong route.** `sources/feeds.py` built every feed URL as
   `https://weworkremotely.com/categories/{category}.rss`, and
   `config.FEEDS["wwr"]["categories"]` lists eight entries whose last is
   `remote-jobs` — the catch-all board. WWR does not serve the whole board as a
   category: it is at the **root**, `https://weworkremotely.com/remote-jobs.rss`
   ([WWR's own RSS index](https://weworkremotely.com/remote-job-rss-feed)).
   `/categories/remote-jobs.rss` answered with a 301 whose redirect then failed.
   The audit's follow-up spot-check reconfirmed both halves: the configured
   route failed again, and the canonical route returned HTTP 200 with 83 items.
2. **No isolation inside the feed.** `wwr()` looped over categories and called
   `get_xml` unguarded, accumulating rows in a local list. The eighth request
   raised, the exception propagated out of `wwr()` to `fetch_free`'s per-feed
   `except`, and the **seven categories that had already returned HTTP 200 went
   with it**. The log then said `wwr ! <error>` — indistinguishable from a feed
   that was wholly down.

The second defect is the expensive one. Failure isolation existed at the feed
boundary and stopped there, so the blast radius of one dead route was the entire
feed. `fetch_free` never got the chance to isolate anything, because by the time
it saw the exception the rows were already unreachable.

## 8. WWR fix

Smallest change that fixes both, and nothing else:

- `WWR_WHOLE_BOARD` names the canonical root feed, and `_wwr_url(category)`
  returns it for `remote-jobs` (and for an empty entry) and the category
  template otherwise. **`config.FEEDS` is not edited** — the configured
  category list is unchanged, so this is not a registry change and cannot be
  mistaken for one.
- `wwr()` wraps each feed request in its own `try`, records the failure via
  `telemetry.partial`, and continues. Rows already collected survive.
- If *every* configured route fails, it raises `RuntimeError` so `fetch_free`
  still reports a dead source. **A failed feed and a feed with no matching jobs
  must not look the same**, and after this change they do not: total failure
  raises, filtered-to-zero returns `[]`.

Feed execution was not redesigned. No other adapter was touched. No CAPTCHA,
auth or anti-bot mechanism is involved or bypassed.

**Confirmed live.** In the end-to-end run of §18, WWR was configured with two
entries — one category and the catch-all board — and the telemetry unit records
`raw=108, requests=2, ok=True`. That is 25 rows from the category plus **83 from
the canonical whole-board feed**: exactly the 83 items the audit's spot-check
measured at that route, now reaching the engine instead of being thrown away.

Regression tests, in `sources/__main__.py:wwr_offline()` (offline, stubbed
fetch) and `sweep/tests/test_search_v2_telemetry.py:WwrFeedRoutes`:

| Test | Asserts |
|---|---|
| `test_whole_board_is_not_requested_as_a_category` | `/categories/` absent from the whole-board URL; both routes exact |
| `test_every_configured_category_resolves` | every entry in `config.FEEDS` builds a well-formed WWR route, so a new entry cannot repeat this bug |
| `test_one_failing_feed_does_not_erase_the_successful_ones` | 3 routes attempted, the last fails, **2 rows returned, not 0** |
| `test_total_failure_still_reports_as_a_failure` | all-routes-fail raises with a clear message |
| `test_filtered_to_zero_is_not_a_failure` | every route answers, predicates keep nothing → `[]`, no raise |
| `test_successful_normalization_unchanged` | company/title split, region, `pubDate` → `YYYY-MM-DD`, link, stripped description, source |

**Mutation-checked.** Reverting the route to `/categories/remote-jobs.rss` fails
`test_whole_board_is_not_requested_as_a_category`; removing the per-feed `try`
lets the simulated failure escape and fails the suite. Both were run and both
failed as required.

## 9. Shadow architecture

`SWEEP_FREE_SOURCE_SHADOW=1`. Default off. `sources/shadow.py`.

| Requirement | How it holds |
|---|---|
| Fetched only when explicitly enabled | `run()` returns immediately unless `enabled()` |
| Uses the existing Greenhouse adapter | calls `ats.fetch` unchanged |
| Rows never alter user-visible results | **`run()` returns `None`.** There is no row set for a caller to merge, however the call site is later edited. This is the safety property, and it is a type property rather than a promise. |
| Rows never affect ranking | they never reach `finalize` |
| Baseline source counts unchanged | `config.ATS_BOARDS` untouched; the console banner is computed from it; shadow units go to `shadow_units[]` and are excluded from `sources_attempted/succeeded/failed` |
| Cannot fail the user's sweep | per-board `try/except` inside `run()`, and the whole call wrapped again in `fetch_free()` |
| Disabled instantly | environment variable, read per call |
| 134-source behaviour unchanged | asserted by `test_registry_size_unchanged` (129 boards + 5 feeds) and `test_shadow_boards_are_not_in_the_production_registry` |

**What production shadow deliberately does not compute:** eligibility,
positive-score, marginal-unique and top-set entry. Answering those means running
score + every hard filter + sort + dedupe a second time over a different row
set, inside a sweep a person is waiting on, for a diagnostic. That is a real
cost and a real risk to their run. The measurement happens offline instead,
against the frozen snapshots, in `bench/search_v2_shadow.py`. Production shadow
records only what it can answer cheaply and safely: does the board respond, how
fast, how many rows, how many pass the acquisition gate, and what failed.

**PubMatic is excluded from the eight.** The audit named nine; PubMatic's sampled
job page returned HTTP 403 in the spot-check, so its rows may not be reachable
at all. It is held in a reachability-review queue rather than measured as if it
were comparable. `test_eight_expected_boards_and_no_pubmatic` pins this.

## 10. Eight-board shadow results

**MEASURED OFFLINE**, [evidence](search-v2-evidence/shadow-eight-board-replay.json).
876 raw rows across the eight boards, 5.72 s summed request time, zero Apify
cost. Snapshots SHA-256-bound in the artifact.

Per-cohort, current rules unchanged:

| Frozen cohort | Baseline final | With shadow | Δ final | Baseline pos. | With shadow | Δ pos. | Entering top-20 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full-stack, India | 82 | 97 | **+15** | 19 | 22 | **+3** | 3 |
| Full-stack, remote | 300 | 326 | **+26** | 40 | 46 | **+6** | 2 |
| React Native, India | 82 | 97 | **+15** | 19 | 21 | **+2** | 2 |
| Business/Salesforce, India | 90 | 105 | **+15** | 7 | 8 | **+1** | 1 |
| Multiple roles/locations, India | 90 | 105 | **+15** | 19 | 22 | **+3** | 3 |

**The deltas reproduce the audit exactly** (+15/+3, +26/+6, +15/+2, +15/+1).
The *baseline* totals differ from the audit's 84/301/84/93 by 1–3 rows, and the
cause is benign and worth stating rather than smoothing over: the snapshot is
dated 2026-09-21, this replay ran on 2026-09-22, and the 14-day recency window
moved one day. The audit's numbers are not wrong and neither are these; they are
one day apart. This does **not** contradict any audit conclusion.

Per board, full-stack India:

| Board | Request | Raw | Fresh 14d | Gated | Eligible | Positive | Marginal unique | Overlap | Top-20 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Fivetran | 827 ms | 197 | 191 | 71 | 6 | **1** | 114 | 1 | 1 |
| Abnormal Security | 609 ms | 78 | 42 | 24 | 4 | **1** | 74 | 0 | 1 |
| Apollo.io | 450 ms | 47 | 14 | 8 | 2 | **1** | 43 | 0 | 1 |
| Zeta Global | 1,506 ms | 145 | 48 | 49 | 3 | 0 | 108 | 0 | 0 |
| Brex | 781 ms | 251 | 77 | 56 | 0 | 0 | 85 | 0 | 0 |
| Vercel | 635 ms | 84 | 84 | 27 | 0 | 0 | 84 | 0 | 0 |
| Catawiki | 507 ms | 47 | 47 | 6 | 0 | 0 | 40 | 0 | 0 |
| Jumio | 410 ms | 27 | 9 | 12 | 0 | 0 | 25 | 0 | 0 |

Two readings, and both belong in a V2-B decision:

- **Within any one cohort the tranche is concentrated.** In full-stack India all
  three positive-score jobs come from three boards, and four of the eight
  contribute no eligible row at all. Overlap with the 134-source baseline is
  essentially nil (1 row total), so these are genuinely new employers — they
  simply do not score.
- **Across the five cohorts, every one of the eight earns at least one
  positive-score row somewhere.** Brex, Catawiki, Jumio and Vercel each
  contribute one in the *remote* fixture; Zeta Global contributes one in React
  Native. So "four boards are worthless" is **false** — it is cohort-specific,
  and picking the three winners would be fitting to one fixture.

**Cohort coverage gap.** Early-career, non-software and business/operations
outside the Salesforce fixture are **not covered**: no frozen fixture exists.
Authoring one is a separate reviewed step. An invented cohort measured by its
own author is weaker evidence than an honest gap, and the audit warns
specifically that these are the cohorts a software-biased corpus mis-serves.

## 11. SmartRecruiters pagination benchmark

**MEASURED live 2026-09-22**, 16 public GET requests, 0 Apify credits, no rate
limiting encountered. [Evidence](search-v2-evidence/smartrecruiters-pagination.json).
Production pagination is **unchanged**: `sources/ats.py` still requests
`limit=100` and takes the first page.

| Board | Advertised | Pages | Returned | Marginal unique | Gated |
|---|---:|---:|---:|---:|---:|
| Renesas Electronics | 916 | 4 | 400 | 364 | 59 |
| Sia | 592 | 4 | 400 | 377 | 33 |
| METRO/MAKRO | 1,611 | 4 | 400 | 337 | 3 |
| Informa Group Plc. | 143 | 2 | 143 | 134 | 10 |
| Version 1 | 160 | 2 | 160 | 144 | 47 |

Cumulative, under the full-stack India fixture:

| Depth | Normalized | Gated | Eligible | Positive score |
|---|---:|---:|---:|---:|
| Rows 1–100 only (today) | 500 | 54 | 7 | **0** |
| All pages fetched | 1,503 | 152 | 23 | **0** |

**Does fetching past row 100 materially improve useful inventory? On this
evidence, no — it improves *inventory* and not *usefulness*.** Three findings:

1. **Positive-score yield is zero at both depths.** Not "smaller": zero. And
   this is entangled, not conclusive: the SmartRecruiters *list* endpoint
   carries no description, so every one of these 1,503 rows is scored on its
   title alone. Depth and JD enrichment cannot be evaluated apart. A low score
   here is not proof the jobs are irrelevant — it is proof the engine cannot
   see them.
2. **Later pages are largely stale.** The API returns newest-first, so the
   14-day window does most of the filtering: Sia returned 100 fresh rows on page
   1, 93 on page 2, then **0 and 0**. Informa and Version 1 likewise dropped to
   0 fresh beyond page 1. Renesas and METRO stayed fresh through page 3 because
   they post heavily. Depth buys *old* postings on most boards.
3. **Within-board duplicate overlap grows with depth** — METRO/MAKRO 0%, 19%,
   20%, 24% across its four pages. Deeper paging re-returns rows.

Cost side: 11 extra requests bought 1,003 extra normalized rows here, at
~360 ms each. Extrapolated naively across 24 SmartRecruiters boards that is a
per-sweep cost paid whether or not a board has depth — and these five were
*selected* for having depth. The other 19 mostly returned fewer than 100 rows
in the census.

## 12. Provider-native identity fields

**MEASURED live 2026-09-22**, via `python -m sources --live`, one board or feed
per provider. Counts are rows-with-a-value / rows-observed. Nothing here affects
ranking or dedupe: fields land on the row under `_native`, which `to_output()`
does not read, and only when telemetry is on.

| Provider | n | Native ID | Requisition / internal | Canonical URL | Board/company | Multi-location | Publish / update |
|---|---:|---|---|---|---|---|---|
| Greenhouse | 205 | `id` 205 | `internal_job_id` 205, `requisition_id` 205 | `absolute_url` 205 | `company_name` 205 | `offices` 132 | `updated_at` 205 |
| Lever | 11 | `id` 11 | — | `hostedUrl` 11, `applyUrl` 11 | — | `categories.allLocations` 11 | `createdAt` 11 |
| Ashby | 32 | `id` 32 | — | `jobUrl` 32, `applyUrl` 32 | — | `secondaryLocations` 3 | `publishedAt` 32 |
| SmartRecruiters | 100 | `id` 100, `uuid` 100 | `refNumber` 100, `ref` 100 | — (built from token+id) | `company.identifier` 100 | `location.region` 77 | `releasedDate` 100 |
| Breezy | 3 | `id` 3, `friendly_id` 3 | — | `url` 3 | — | `locations` 1 | `published_date` 3 |
| Remote OK | 99 | `id` 99 | — | `url` 99, `apply_url` 99 | `company` 99 | — | `date` 99 |
| WWR | 25 | `guid` 25 | — | `link` 25 | — | `region` 25 | `pubDate` 25 |
| Remotive | 18 | `id` 18 | — | `url` 18 | `company_name` 18 | `candidate_required_location` 18 | `publication_date` 18 |
| Jobicy | 20 | `id` 20 | — | `url` 20 | `companyName` 20 | `jobGeo` 20 | `pubDate` 20 |
| Himalayas | 20 | `guid` 20 | — | `applicationLink` 20 | `companyName` 20 | `locationRestrictions` 19 | `pubDate` 20 |

**Every one of the ten providers publishes a stable native posting ID, and the
engine was discarding all ten.** That is the finding. `scraper.job_key` falls
back to normalized-company-plus-sorted-title-words for almost every free row —
the heuristic the audit produced six counterexamples against — while a
provider-namespaced identity sat in the response and was dropped during mapping.

Additional fields, not in the original request but worth having for the same
question: Ashby `isListed` (32/32) separates listed from unlisted postings, which
the audit flagged as needing its own state; Ashby and Lever `workplaceType`
(32/32, 11/11) is a native arrangement signal; SmartRecruiters `uuid` is a second
provider-side identity and `ref` is the employer's own.

**No update timestamp exists for Ashby, SmartRecruiters or Breezy** — a
**MEASURED absence, not an omission**. This stage first guessed `updatedAt`,
`lastUpdatedOn` and `updated_date`; all three resolved on 0 rows, and the field
lists in the live payloads confirm no such key. Greenhouse's `updated_at` is the
only update timestamp any of the five ATS providers publishes, which is also
exactly why the audit warns that Greenhouse "freshness" means last-touched and
not newly-opened.

`test_native_map_cannot_shadow_a_scored_field` asserts no native field name
collides with a scored field, and `native_offline()` asserts every ATS platform
and every JSON feed declares a map — so adding a provider cannot silently add
one with no native evidence.

## 13. Paid actor-contract verification

No actor was run. No credit was spent. Schemas re-fetched 2026-09-22.

### LinkedIn — `curious_coder/linkedin-jobs-scraper`

| Question | Finding |
|---|---|
| Input Sweep sends | **VERIFIED FROM CURRENT CODE** (`scraper.py:966`): `{"urls": [one URL], "count": max(10, max_results), "scrapeCompany": false}`. Filters ride in the URL: `keywords`, `geoId`, optional `f_C`, `f_WT=2` when remote, `f_E`, `f_TPR=r<days*86400>`. |
| `count` | **VERIFIED FROM CURRENT PROVIDER CONTRACT: the field does not exist.** The published schema lists `urls`, `keywords`, `location`, `geoId`, `distance`, `datePosted`, `companyIds`, `under10Applicants`, `autoConvertToAiSearch`, `scrapeCompany`, `limitPerSource`, `splitByLocation`, `splitCountry`. Depth is `limitPerSource`. |
| Omitted limit | **VERIFIED FROM CONTRACT:** "Leave empty to scrape as many as LinkedIn returns for each search (up to ~1000)." |
| Multiple queries | **VERIFIED:** one `keywords` string, but `urls` is an array, so multiple queries are expressible as multiple URLs. Sweep sends exactly one. |
| Multiple locations | **VERIFIED:** via multiple URLs, or `splitByLocation` + `splitCountry`. Sweep sends one URL per location. |
| Country | **VERIFIED:** not a field. Geography is `geoId` inside the URL — a single value per URL. |
| Remote filter | **VERIFIED code**, **INFERRED runtime:** Sweep sets `f_WT=2`. With `autoConvertToAiSearch` documented as enabled by default, classic URL filters are "converted to natural language and appended to search keywords" — so `f_WT`/`f_E`/`f_TPR` may be *search terms*, not enforced filters. |
| `max_results` semantics | **UNKNOWN / REQUIRES PAID VALIDATION.** Sweep's intended 15 is expressed in a field the schema does not document. Whether the build ignores it, errors, or honours it is unobservable without a run. |
| Pagination | **UNKNOWN.** Not exposed; depth is a single limit. |
| Pricing basis visible to Sweep | **VERIFIED code:** `$0.045 / 25` = $0.0018 per intended row (`config.py`, `SITE_RATE_BASIS["linkedin"]=25`). Store: $2/1,000 results + a start event. |
| Failure isolation if batched | **VERIFIED by inspection:** one URL per run today, so one failure costs one query×location. Batching URLs would merge failure domains and, with only `limitPerSource` available, share one depth budget across sources — and `search_rank` provenance would no longer map to a query. |
| **Do Sweep's inputs still match the contract?** | **NO.** This is the stage's most consequential finding. |

**The concrete bug, and its size.** If the actor ignores unknown fields, Sweep
sends no depth limit and a search can return ~1,000 rows. At $2/1,000 that is
~$2.00 against a modelled $0.027 — roughly **74×** — and `scraper.py:2118`
checks `spent >= budget` only *before* the next run, so the overshoot is
discovered after it is charged. The repository-default plan schedules 18
LinkedIn runs and estimates $6.966 total; at ~$2.00 per LinkedIn run the
LinkedIn half alone would be ~$36.

Counter-evidence, which is why this is not stated as certain: the audit's
retained historical logs show intended-depth-15 searches returning 16–18 rows,
so `count` *was* being honoured by whatever build ran then. The actor is mutable
and its build is not pinned (`requirements.txt` says only
`apify-client>=1.7.0`). So the finding is **a verified contract mismatch with an
UNKNOWN runtime consequence and a plausible 74× cost exposure**.

Per the brief, paid budgeting behaviour was **not** changed. The documented
options, none taken here: pass `limitPerSource` alongside `count`; supply
`maxTotalChargeUsd` on the run (the API documents it and Sweep does not use it);
pin the actor build. Each is a separately reviewed change, and the first two are
cheap. This belongs at the top of V2-B.

### Indeed — `misceres/indeed-scraper`

| Question | Finding |
|---|---|
| Input Sweep sends | **VERIFIED CODE** (`scraper.py:945`): `position`, `location`, `country`, `maxItemsPerSearch`, `parseCompanyDetails=false`, `saveOnlyUniqueItems=true`, `followApplyRedirects=false`. |
| Contract match | **VERIFIED FROM CURRENT CONTRACT: every field Sweep sends exists and is named correctly.** The only note is that `followApplyRedirects` is documented as legacy and "has no effect" — harmless. |
| Multiple queries | **VERIFIED:** `position` is scalar; `startUrls` is an array. Sweep uses `position` only. |
| Multiple locations | **VERIFIED:** scalar `location`; multiple via `startUrls`. |
| Country | **VERIFIED single value.** Sweep maps it from the *location* through `config.INDEED_COUNTRIES` and refuses an unmapped place before spending — a correct guard. |
| Remote filter | **VERIFIED absent.** No work-mode field. A bare "Remote" location is passed as text. Onsite/hybrid narrowing therefore happens locally, after paying. |
| Recency | **VERIFIED absent.** No date-posted field. `max_age_days` is applied locally only, so Sweep pays for rows the recency rule then drops. |
| `maxItemsPerSearch` | **VERIFIED:** per keyword search, and per start URL independently. |
| Pagination | **INFERRED:** actor-internal; not exposed. |
| Pricing basis | **VERIFIED code:** `$0.09 / 15` = $0.006/row. Store: $6/1,000 returned listings. Consistent at full depth. |
| Failure isolation if batched | **INFERRED:** `startUrls` each carry their own limit, so depth would survive batching, but one run failing would lose every URL in it and per-query attribution would need reconstructing from the rows. |

### Naukri — `muhammetakkurtt/naukri-job-scraper` (disabled by default)

| Question | Finding |
|---|---|
| Input Sweep sends | **VERIFIED CODE** (`scraper.py:970`): `keyword`, `maxJobs`, `fetchDetails=true`, `sortBy="relevance"`, `freshness`, plus `cities:[id]` or `workMode:["remote"]`, plus `experience` when known. |
| Contract match | **VERIFIED FROM CURRENT CONTRACT: all field names match.** |
| `maxJobs` | **VERIFIED minimum 50, default 100.** Sweep sets `results_per_run=50`, i.e. exactly the floor — so the depth control genuinely cannot move it, as `config.py` already documents. |
| Multiple queries | **VERIFIED:** `keyword` is single-valued. |
| Multiple locations | **VERIFIED:** `cities` is a multi-select array. Sweep sends one. Sending several is **not** equivalent to each city getting depth 50 — they would share one `maxJobs`. |
| Country | **VERIFIED:** not a field; Naukri is India (and NaukriGulf via `searchUrl`). |
| Remote filter | **VERIFIED source-side:** `workMode` is a real multi-select (`office`/`remote`/`hybrid`). |
| Recency | **VERIFIED:** `freshness` enum `all/30/15/7/3/1`. Sweep's 14-day window rounds **up to 15**, so it over-fetches by a day and filters locally. |
| Pagination | **UNKNOWN:** actor-internal. |
| Pricing basis | **VERIFIED code:** `$0.50` per-run *minimum*, depth 50. Store: $1.50/1,000 standard + $3/1,000 detail + $0.001 init. `fetchDetails=true` is material; the $0.50 floor does not appear in the current table. |
| Failure isolation if batched | **INFERRED:** a city union under one `maxJobs` loses per-location attribution and per-location depth. |
| Note | `config.py:93`'s "never executed" comment is contradicted by retained logs showing Naukri runs and rows. Not grounds for removing or zero-ranking it. |

No batching and no concurrency were implemented.

## 14. Unresolved UNKNOWNs

Carried forward or newly opened. None is a blocker for review; each is a reason
some V2-B option is not yet decidable.

1. **Whether the LinkedIn actor honours, ignores or rejects `count`** — and
   therefore whether current paid sweeps have an unbounded per-run charge.
   Needs one approved paid run (§15). Highest-value unknown in this document.
2. **Whether `autoConvertToAiSearch` defaults on for the build Sweep invokes**,
   and what happens to `f_WT`/`f_E`/`f_TPR` if so. Same run resolves it.
3. **Production sweep timing.** Telemetry is in place but **has never run in
   production** — it is off, and nothing here was deployed. Every timing figure
   in this document is a local probe, exactly as in the audit.
4. **Real per-source failure rates.** One census plus one spot-check is not a
   longitudinal rate. `failure_category` now makes accumulating them possible.
5. **Whether SmartRecruiters depth would pay off with descriptions.** Depth and
   the missing JD are entangled; §11's zero cannot separate them.
6. **Whether the eight boards' URLs stay reachable and their IDs stable** across
   refreshes. One dated sample cannot say. PubMatic's 403 is unexplained.
7. **Eligibility for early-career, non-software and general business cohorts** —
   for the shadow tranche, for pagination, and for the existing 134.
8. **Safe shared cache TTLs.** Unchanged from the audit: Greenhouse sends
   `private, max-age=0, must-revalidate`; Ashby sends `public, max-age=60`.
9. **True duplicate prevalence and the cost of the current `job_key`.** The
   native fields are now captured, but no replay against them has been run —
   deliberately, since that is the input to a dedupe decision, not this stage.
10. **Whether Postman's Greenhouse token is dead or migrated.** Still 404.
    Telemetry will now record it as `http_404` on every sweep, which is the
    evidence a registry decision needs.
11. **Response bytes per source** — not wired (§4).

## 15. Recommendation for V2-B

In order, and the first is not close:

1. **Resolve the LinkedIn depth contract before anything else.** It is the only
   finding here with an unbounded cost. Cheapest safe first step needs **no paid
   run at all**: send `limitPerSource` alongside `count`, and supply
   `maxTotalChargeUsd` on the run so the platform enforces a ceiling Sweep
   currently cannot. That is a small, separately reviewable change to the paid
   input builder, and it converts an unknown exposure into a bounded one. Only
   then is the one-run probe worth its money, and with a hard cap in place it is
   also safe.
2. **Turn telemetry on for a small number of real sweeps.** Everything else in
   this list is gated on production numbers that do not exist yet, and the
   measured cost of getting them is +0.27% CPU and one 15 KB file.
3. **Shadow the eight boards for a few real sweeps**, then decide per board
   rather than per tranche. Do not enable them on this snapshot alone, and do
   not drop the four that score zero in one fixture — they score in others.
4. **Do not paginate SmartRecruiters yet.** §11 says depth buys stale,
   increasingly duplicated, title-only inventory with zero positive-score yield.
   Lazy JD enrichment for the two description-less platforms is the change that
   would make depth worth re-measuring, and it is the better investment.
5. **Then** identity/dedupe, using the native fields now being captured.
6. Workable/Gem, shared inventory cache, paid batching and bounded concurrency
   stay where the audit put them: after the measurements above exist.

## 16. Decision gates for V2-B

Not a recommendation to implement. Evidence for a choice.

### A. Enable some or all eight Greenhouse boards

- **EVIDENCE NOW AVAILABLE** — 876 raw rows, 5.72 s, zero credits; +15/+3,
  +26/+6, +15/+2, +15/+1 across four cohorts, reproducing the audit exactly;
  per-board attribution showing 3 of 8 carry full-stack India and all 8 carry
  something in at least one cohort; near-zero baseline overlap; all eight board
  identities confirmed against the provider's company endpoint; eight of nine
  sampled job pages HTTP 200.
- **EXPECTED BENEFIT** — a few genuinely new employers per cohort, three of them
  reaching a top-20; +6% to +9% final inventory. Zero Apify cost.
- **KNOWN RISK** — eight more per-sweep requests (~5.7 s serial) whether or not
  they yield; slug-derived company labels can hide cross-board duplicates;
  PubMatic's 403 shows a board can list rows nobody can apply to.
- **MISSING MEASUREMENT** — reachability and ID stability across refreshes;
  early-career/non-software/business cohorts; real-candidate fit. Shadow for a
  few real sweeps first (§15.3).

### B. Paginate SmartRecruiters deeper

- **EVIDENCE NOW AVAILABLE** — §11, measured live: 500→1,503 normalized,
  54→152 gated, 7→23 eligible, **0→0 positive score**; freshness collapsing to
  zero beyond page 1–2 on three of five boards; duplicate overlap rising to 24%.
- **EXPECTED BENEFIT** — on this evidence, **no useful yield**.
- **KNOWN RISK** — up to 3 extra requests per board per sweep across 24 boards,
  for stale and partly duplicated rows; more scoring and serialization work for
  none of it.
- **MISSING MEASUREMENT** — the same experiment with descriptions available.
  Depth cannot be judged while every row is title-only. **Do not take this
  option; take JD enrichment first.**

### C. Add Workable/Gem prototypes

- **EVIDENCE NOW AVAILABLE** — unchanged from the audit: Workable's official
  public account endpoint contradicts the obsolete "all slugs empty" note in
  `ats.py`; Gem has a documented unpaginated public API; 3 Workable and 3 Gem
  account probes succeeded. Nothing was added in this stage.
- **EXPECTED BENEFIT** — **UNKNOWN**. No overlap replay and no candidate-cohort
  measurement exists.
- **KNOWN RISK** — a new family is new mapping code, new schema drift and new
  terms to review; the audit's own measurement is that large raw expansion
  yielded modest profile-matched gain.
- **MISSING MEASUREMENT** — a live sample through a frozen replay, overlap
  against the 134, and per-cohort eligibility. Same shape of evidence §10 now
  provides for the eight boards. Rank below A.

### D. Improve identity/dedupe

- **EVIDENCE NOW AVAILABLE** — §12: all ten providers publish a stable native
  ID and the engine discarded every one; six executable counterexamples from the
  audit; capture is now in place behind the telemetry flag, and `identity{}`
  records per-source unique/newly-unique/unkeyed counts.
- **EXPECTED BENEFIT** — recovering false merges (distinct requisitions
  collapsing) and catching missed duplicates; the audit measured 898
  company/title groups spanning different URLs and 754 also spanning different
  locations, across 2,629 observations.
- **KNOWN RISK** — the highest-risk option here. Dedupe decides which row a user
  sees; the survivor must remain identical or ranking changes silently. Needs
  shadow comparison before any switch.
- **MISSING MEASUREMENT** — no replay under a native-ID key has been run. That
  is deliberate: it is the input to this decision and not part of this stage.
  It is now cheap — one offline replay over a snapshot carrying `_native`.

### E. Shared public inventory refresh/cache

- **EVIDENCE NOW AVAILABLE** — **VERIFIED** no shared cache exists; every sweep
  refetches every board. Per-board durations are now recorded, so the saving is
  becoming computable. Observed headers: Greenhouse `private, max-age=0,
  must-revalidate`; Ashby `public, max-age=60`.
- **EXPECTED BENEFIT** — the audit's model: at U=100 sweeps/day and R=4
  refreshes, up to 96% of board fetches avoided. At U=1 it *increases* traffic.
- **KNOWN RISK** — must cache public inventory *before* candidate filters and
  before `hires_home`; a `private` directive must not be overridden into a
  shared HTTP cache; a failed refresh must not silently extend stale inventory.
- **MISSING MEASUREMENT** — arrival rate, query overlap and concurrent-user
  count. All three come from telemetry running in production. **Gated on §15.2.**

### F. Reduce paid actor starts

- **EVIDENCE NOW AVAILABLE** — 90 serial starts at repository defaults
  (measured offline by the audit); §13's full contract table; start fees
  measured as negligible (~$0.00085 for 17 LinkedIn starts).
- **EXPECTED BENEFIT** — mainly latency, not money. Batching saves start events
  that cost almost nothing.
- **KNOWN RISK** — merged failure domains; shared depth budgets under
  `limitPerSource`; lost per-query `search_rank` provenance; and doing any of it
  *before* §15.1 would batch searches whose depth contract is unresolved.
- **MISSING MEASUREMENT** — per-run timings from a real paid sweep with
  telemetry on. **Strictly after §15.1.**

### G. Bounded concurrency

- **EVIDENCE NOW AVAILABLE** — per-source durations now exist in the schema;
  the audit's census showed a heavy tail (Ashby UiPath 83.9 s, ClickHouse
  50.6 s, Xero 24.4 s against a 0.73 s median), so wall-time upside is real.
- **EXPECTED BENEFIT** — lower Free wall time. No inventory gain whatsoever.
- **KNOWN RISK** — determinism: completion order decides score ties, so
  concurrency changes which duplicate survives unless merging is made
  order-independent. Plus per-host limits, memory, and simultaneous paid spend.
- **MISSING MEASUREMENT** — production per-source latency distribution and
  provider rate-limit behaviour. **Gated on §15.2.** Do not take this because
  the tail looks addressable; take it when the distribution is known and the
  merge is provably order-independent.

## 17. Paid call safety

**No paid Apify actor was run and no credit was spent.** Nothing in this stage
required one. The one experiment that would resolve §14.1–2 is specified here
and **not executed**; it needs explicit approval.

| | |
|---|---|
| **ACTOR** | `curious_coder/linkedin-jobs-scraper` |
| **NUMBER OF RUNS** | 1 |
| **INPUT SHAPE** | Exactly what Sweep sends today, unchanged, so the answer describes production: `{"urls": ["https://www.linkedin.com/jobs/search/?keywords=Software+Engineer&geoId=105214831&f_E=2&f_TPR=r1209600"], "count": 15, "scrapeCompany": false}` (the first `software_fullstack` unit in `paid-plans.json`) |
| **REQUESTED RESULTS** | Intended 15 |
| **ESTIMATED MAXIMUM COST** | **UNKNOWN, and that is the point.** If `count` is honoured: ~$0.03 (15 results at $2/1,000 plus one start event). If it is ignored: up to ~**$2.00** (~1,000 results). No enforceable ceiling exists in Sweep today. |
| **EXACT MEASUREMENT IT WOULD UNLOCK** | Whether `count` is recognised; the actual returned row count; whether `f_WT`/`f_E`/`f_TPR` survive as filters or become AI-search keywords; real startup/runtime/retrieval timings; the charged-event shape |

**Recommendation: do not approve this run yet.** Per §15.1, first add
`maxTotalChargeUsd` to the run input — the API documents it, Sweep does not use
it — so the experiment carries a provider-enforced ceiling instead of an
estimate. The audit's own rule applies: *do not run an experiment with an
unknown maximum merely under its point estimate.*

## 18. Verification

- `python scraper.py --demo` — passes, including the new passivity assertion.
- `python -m sources` — passes (offline; WWR routes, isolation, normalization, native maps).
- `python -m sources --live` — passes; 533 rows across all 10 providers, and it
  now reports native-field availability. It also *failed* before this stage, on
  a dead probe board (§0.4).
- `python telemetry.py` — passes.
- `python -m unittest discover -s sweep/tests -t .` — **913 tests, OK**
  (887 before, +26 new).
- **Mutation-checked:** reverting the WWR route, removing WWR per-feed
  isolation, making `telemetry.stage` touch the engine's list, and leaking
  `_native` into `to_output` each fail the suite. All four were run.
- **End-to-end**, a small real free sweep (2 ATS boards + 2 feeds) run three
  times — flags off, telemetry on, both on:

  | Check | Result |
  |---|---|
  | Rows returned | 5 / 5 / 5 |
  | Result JSON identical to flags-off | yes / yes |
  | Result CSV identical to flags-off | yes / yes |
  | Telemetry files written | 0 / 1 / 1 |
  | Shadow sources in user-visible results | **NONE** |
  | `Free sources:` banner identical | yes (`2 ATS boards … + 2 feeds`, not 10) |
  | Shadow units recorded, excluded from `sources_attempted` | 8, and `sources_attempted` = 4 |

Nothing was deployed. `SWEEP_PROFILE_ENGINE_VERSION` is untouched and still
defaults to v1. `SWEEP_EXPERIENCE_MISMATCH_GUARD` remains off.

## 19. Relationship to the audit

No audit conclusion was edited. Where this stage's evidence differs:

| Audit | This stage | Reading |
|---|---|---|
| Baseline finals 84 / 301 / 84 / 93 | 82 / 300 / 82 / 90 | **Not a contradiction.** The 14-day window moved one day between 2026-09-21 and 2026-09-22. The shadow *deltas* reproduce exactly. |
| LinkedIn sends `count`, schema advertises `limitPerSource` | Confirmed live, **plus** an omitted limit returns ~1,000 rows/search and `autoConvertToAiSearch` defaults on | **Extends** the audit: the same mismatch, with a quantified ~74× cost exposure the audit could not state. |
| "Postman needs board migration discovery" | Still 404, **and** it was silently breaking `python -m sources --live` | **Extends** the audit with a consequence it did not look for. |
| SmartRecruiters "at least 2,946 advertised rows lie beyond those first pages; fresh/relevant/unique gain is UNKNOWN" | Measured: mostly stale, increasingly duplicated, 0 positive score | **Resolves** an UNKNOWN, in the direction the audit's caution implied. |
| Ashby/SmartRecruiters/Breezy update timestamps implied available | Measured absent | **Corrects** an assumption this stage itself first made; the audit never claimed them by name. |
