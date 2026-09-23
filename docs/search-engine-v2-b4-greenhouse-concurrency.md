# V2-B4 — deterministic bounded concurrency for production Greenhouse boards

Date: 2026-09-23. Baseline: `ed7a503` (V2-B3). Production runs V2-B3 with
`SWEEP_SEARCH_V2_TELEMETRY=1`, `SWEEP_FREE_LEVER_CONCURRENCY=1`,
`SWEEP_FREE_LEVER_WORKERS=4` and `SWEEP_FREE_SOURCE_SHADOW=1`.
Scope: **acquisition of the 54 production Greenhouse boards only.** Default
**OFF**. Not deployed, not enabled, no deployment or environment file changed.

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a dated
probe), **VERIFIED** (directly observed code or configuration), **INFERRED** (a
model with stated assumptions), **UNKNOWN** (evidence unavailable, which is not
the same as zero).

Nothing else moved. The registry is 134 active records (129 ATS boards, 54 of
them Greenhouse, + 5 feeds), Postman included. No shadow board was promoted or
parallelised, `sources/shadow.py` is untouched, and Lever's flags, defaults,
executor, merge and telemetry are exactly V2-B2's. Adapters, request payloads,
timeouts, retries, scoring, ranking, dedupe, `job_key`, native-id semantics,
Search Preferences, recency, location, arrangement and salary rules and every
paid path are unchanged. No caching, Redis, database or queue. **No Apify call
was made.** `SWEEP_EXPERIENCE_MISMATCH_GUARD` remains off.

## 0. Headline findings

1. **MEASURED — output is byte-identical to serial at every worker count.** On a
   frozen capture of all 54 production boards replayed with their recorded
   latency and errors, serial and 1/2/4/6/8 workers produce the same row
   sequence, the same final output, the same telemetry units and the same
   Postman failure (§8).
2. **MEASURED — completion order never decides a survivor.** A synthetic twin of
   a real Databricks posting, placed right after Databricks and answering in 10
   ms, finished first at 2, 4, 6 and 8 workers; Databricks' own row won in every
   arm, exactly as serial (§8). The same holds in unit and end-to-end tests,
   inside Greenhouse and against the Lever and Ashby blocks either side of it.
3. **MEASURED — the gain is real and saturates after 4.** Live, one host, one
   pass per arm: **34.9 s serial → 21.4 s (2) → 10.9 s (4) → 8.2 s (6) → 6.6 s
   (8)**. Four workers save 24.1 s; six save 2.7 s more; eight 1.6 s more again,
   while p95 board time and peak memory climb (§9).
4. **MEASURED — no provider pressure observed**: 0 retries, 0 HTTP 429, and no
   failure but Postman's known 404 in any arm. One benchmark host and one day is
   not proof of sustained provider safety (§10).
5. **MEASURED — the limit is bytes, not requests.** One Greenhouse pass downloads
   **75 MB** (`content=true`; Databricks alone is 8 MB). Under concurrency the
   large boards slow down (MongoDB 1.29 s → 3.45 s at eight workers) and the
   small ones do not, which is shared bandwidth (§10).
6. **Recommendation from the evidence: worth a later production experiment,
   phased 2 then 4, never 6 or 8 first** (§17).

## 1. Baseline production evidence

**MEASURED** in V2-B2's three production sweeps (serial, before Lever
concurrency): 54 Greenhouse boards per sweep, 162 units, ~127.56 s summed request
time — **~42.5 s a sweep** — median ~0.34 s, p95 ~2.08 s, **0 retries in 162
fetches**, and `greenhouse:postman` failing **HTTP 404 once per run**.

Summed source time is not wall time, and most boards are individually fast, so
the saving was an open question rather than an assumption. That is what §8–§10
measure.

## 2. Architecture

**Option A, narrowly: V2-B2's helper now takes a provider.** `sources/concurrency.py`
already had exactly the right deterministic primitive; the only Lever-specific
things in it were the flag names and a one-entry provider tuple. A Greenhouse
wrapper (option B) would have copied the executor, the merge and the telemetry
attachment — the three places a copy can drift. So the scope became a table:

```python
ENV = {
    "lever": ("SWEEP_FREE_LEVER_CONCURRENCY", "SWEEP_FREE_LEVER_WORKERS"),
    "greenhouse": ("SWEEP_FREE_GREENHOUSE_CONCURRENCY",
                   "SWEEP_FREE_GREENHOUSE_WORKERS"),
}
PROVIDERS = tuple(ENV)
FLAG, WORKERS_ENV = ENV["lever"]           # V2-B2's names, unchanged
```

`enabled(platform="lever")`, `workers(platform="lever")` and `applies(platform)`
read the provider's own pair; the defaults keep V2-B2's call shape, so every
V2-B2 caller and test still means Lever. `fetch_boards` sizes its pool with
`workers(platform)`. That is the whole production change: **no generic
framework**, two named providers, and adding a third remains a reviewed edit to
a constant.

`sources/__init__.py:fetch_free` is unchanged apart from its comment: it already
asked `concurrency.applies(platform)` per provider and fell through to the serial
loop otherwise.

## 3. Deterministic merge invariant

Unchanged from V2-B2, now also exercised by Greenhouse:

1. `items = list(boards.items())` — the registry order, frozen.
2. every board submitted at once; at most `workers` in flight.
3. `results = [f.result() for f in futures]` — collected in **submission** order.
   `as_completed` is never called.
4. the coordinator alone walks `results` in that order: attaches each telemetry
   unit, writes each log line, extends the row list.

A worker (`_fetch_one`) receives no shared container and returns an isolated
`BoardResult`; its only way out is its return value (pinned by
`test_the_worker_has_nothing_shared_to_append_to`).

**What "same order" means, precisely.** `fetch_free` walks providers in registry
order — `lever, greenhouse, ashby, smartrecruiters, breezy`, then feeds — and
`fetch_boards` blocks until every one of a provider's futures is done. So the
Greenhouse block still arrives after every Lever row and before every Ashby row,
and inside it rows arrive board by board in registry order and, within a board,
in the adapter's order. That is the exact sequence the serial loop hands to
downstream accumulation. **Nothing is sorted after the fact and no ordering field
was added.**

## 4. Relationship to V2-B2 and to B3's shadow tranche

**Lever (V2-B2)** keeps its flag, worker variable, default 4, clamp 1..8,
executor, thread prefix, merge and telemetry. The Greenhouse switch cannot turn
Lever on, and neither worker variable moves the other: pinned by
`LeverIndependence` (Lever stays serial with only Greenhouse on; each executor is
sized by its own setting — `{"sweep-lever": 4, "sweep-greenhouse": 2}` —
and Lever's rows are identical whether Greenhouse is concurrent or not).

One V2-B2 test asserted `"greenhouse" not in concurrency.PROVIDERS`. Its intent
was "shadow cannot reach an executor through the Lever path"; it now asserts
that the **Lever** switch never routes Greenhouse there, and the stronger claim
lives in B4's `ShadowIndependence`.

**B3's eight shadow boards are Greenhouse boards and never touch this code.**
`sources/shadow.py` calls `ats.fetch` directly, serially, after the result files
are written, outside the registry — and it was not edited. With Greenhouse
concurrency **on** and shadow on, through `scraper.main()`:

- a spy on `concurrency._fetch_one` sees only the production Lever and
  Greenhouse boards, never one of the eight;
- the eight shadow units are still all present, in order, and **strictly
  serial** — each starts after the previous finished;
- production telemetry and the CSV are identical with shadow on and off, and
  `sources_attempted` counts production boards only.

## 5. Flags and defaults

| Variable | Default | Parsing |
|---|---|---|
| `SWEEP_FREE_GREENHOUSE_CONCURRENCY` | **OFF** | `1/true/yes/on` enables; read per call |
| `SWEEP_FREE_GREENHOUSE_WORKERS` | **4** | clamped to **1..8**; `0`, negatives → 1; `999` → 8; `banana`, `4.5`, empty → 4 |

Identical rules to V2-B2's, deliberately. The clamp is asserted **at the
executor**: 20 boards at `workers=999` never exceed 8 in flight. `workers=1`
matches serial byte for byte. Neither variable is set in `render.yaml`,
`deploy/sweep_worker.py`, `requirements.txt` or `gunicorn.conf.py` (tested).

## 6. Telemetry under Greenhouse threads

V2-B2's repairs were verified against Greenhouse rather than assumed: the open
unit is thread-local, the run record mutates under one lock, and units are
opened with `defer=True` and attached by the coordinator in registry order.

**VERIFIED by test, per board, at six workers:** its own duration (a 300 ms
board reads > 250 ms while a 10 ms board reads < 150 ms), requests, retries
(distinct per board), failure category, raw, normalized and gated counts
(distinct per board, so any cross-attribution shows as a wrong number), and —
through the **real** adapter with only the HTTP answer stubbed — every row's
`_native.native_id` belongs to its own board's payload. Units appear in registry
order, one per board; everything but the clock equals serial.

**Found while mutation-checking (§12):** the ordering tests alone did not catch a
worker attaching its own unit at the moment it *started* (`defer=False`),
because the executor starts tasks in submission order, so the order usually
survives. It is still a race: two workers dequeuing together can swap their
appends. `test_workers_never_attach_to_the_shared_record` now asserts the rule
V2-B2 depends on directly — from inside every worker, the run's unit list is
still empty.

## 7. Failure and duplicate-survivor semantics

**Failure.** `_fetch_one` catches per board exactly as the serial loop does. One
board raising costs that board only: siblings are neither cancelled nor lost,
the failure is recorded once on its own unit, and a sweep never fails because a
board did. No retry was added and no timeout changed (25 s socket timeout, 2
retries, 1 s + 2 s backoff, no retry on 4xx).

**Postman.** Serial and concurrent produce the same unit (`ok: false`,
`failure_category: "http_404"`), the same totals (6 attempted, 5 succeeded, 1
failed in the fixture), and the same log line,
`greenhouse       Postman                ! HTTP Error 404: Not Found`. In the
frozen replay the captured 404 replays **as a 404**, so its category is
production's, not a stand-in. It remains part of the baseline and must be
reported apart from any new failure (§15).

**Duplicate survivor.** Registry order decides equal-score ties, so:

| Case | Fixture | Result |
|---|---|---|
| Inside Greenhouse | `acme` (early, 300 ms) and `acmemirror` (late, 10 ms) publish one posting | `acme` wins in serial and at 1/2/4/6 workers; completion inverted at 2/4/6 |
| Greenhouse vs the Ashby block after it | `acme` and `acmeashby` publish one posting | Greenhouse wins, as serial |
| Lever block vs Greenhouse | `acmelever` and `acmemirror` publish one posting | Lever wins, as serial |
| Real data | synthetic twin of a Databricks posting, finishing first | Databricks wins at every worker count (§8) |

`test_the_twins_really_tie_so_order_is_what_decides` proves each pair scores
identically, so order — not score — is what these tests exercise.

**VERIFIED in the current registry:** no two Greenhouse boards share a
normalized company label, so a cross-board Greenhouse `job_key` collision cannot
happen today. The rule is tested anyway, because a future registry edit could
create one.

## 8. Frozen replay

**MEASURED, deterministic.** `bench/search_v2_free_concurrency.py --provider
greenhouse --capture` took one serial public pass over all 54 production boards
(2026-09-23 13:07 UTC): 53 answered, Postman 404; 30.68 s summed recorded
latency; slowest Databricks 1.30 s, Capco 1.09 s, Elastic 1.08 s. `--replay`
served each board's payload **or its recorded error** after its recorded
latency, with sockets denied, through the real adapter and the real `finalize`,
each arm under its own telemetry record.
[Evidence](search-v2-evidence/greenhouse-concurrency-replay.json).

| Arm | Wall | Summed board s | Rows | Final | Acquired SHA-256 | Final SHA-256 | Telemetry units | Failures |
|---|---:|---:|---:|---:|---|---|---|---|
| serial | 33.89 s | 33.89 | 2,029 | 109 | `dffff6f57093…` | `a49dbbf0b8db…` | 54 | postman |
| workers=1 | 33.73 s | 33.72 | 2,029 | 109 | identical | identical | identical | postman |
| workers=2 | 16.99 s | 33.84 | 2,029 | 109 | identical | identical | identical | postman |
| workers=4 | 8.64 s | 34.00 | 2,029 | 109 | identical | identical | identical | postman |
| workers=6 | 5.79 s | 34.22 | 2,029 | 109 | identical | identical | identical | postman |
| workers=8 | 4.88 s | 37.82 | 2,029 | 109 | identical | identical | identical | postman |

"Identical" is checked field by field against serial: source sequence, acquired
rows, final output, final apply URLs, scores, remote scopes, every telemetry
unit's board/ok/category/requests/retries/raw/normalized/gated, and failures.
**All five arms: identical.**

**Collision arm (synthetic, labelled so in the artifact).** A twin of one real
Databricks posting (same item, different apply URL, same company label) was
inserted right after Databricks and answered in 10 ms. The posting was chosen
from rows kept as remote/worldwide, so its eligibility cannot depend on the
board-level hires-home signal. The twin **finished first at 2, 4, 6 and 8
workers**; **Databricks' row survived and the twin's did not in all six arms**;
the result had the same 109 rows with the twin as without it, so the twin really
was deduplicated against the original.

Summed board time only rises at eight workers (+3.9 s): with no network in the
replay, that is local CPU contention — JSON parsing and HTML stripping of large
payloads competing for the interpreter lock.

## 9. Live public benchmark

**MEASURED 2026-09-23 13:15 UTC**, `--live`: one pass per arm over the 54
production boards, **each arm in a fresh child process** so peak RSS is that
arm's own, public GETs only, **zero Apify**.
[Evidence](search-v2-evidence/greenhouse-concurrency-live.json).
The harness drives `sources.fetch_free` for one provider and never the engine's
entry point, so it has no path to paid execution at all (pinned by
`PaidIsolation`).

| Arm | Wall | Saved | Speed-up | Summed board s | Median board | p95 board | Slowest | Failures | Retries | 429 | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| serial | 34.95 s | — | 1.00× | 34.95 | 554 ms | 1,314 ms | 1,513 ms | postman 404 | 0 | 0 | 219.3 MB |
| workers=2 | 21.41 s | 13.5 s | 1.63× | 42.61 | 646 ms | 1,814 ms | 2,084 ms | postman 404 | 0 | 0 | 233.9 MB |
| workers=4 | 10.88 s | 24.1 s | 3.21× | 43.19 | 584 ms | 2,155 ms | 2,537 ms | postman 404 | 0 | 0 | 254.0 MB |
| workers=6 | 8.18 s | 26.8 s | 4.27× | 45.13 | 600 ms | 2,045 ms | 3,645 ms | postman 404 | 0 | 0 | 231.5 MB |
| workers=8 | 6.60 s | 28.3 s | 5.29× | 51.29 | 653 ms | 2,799 ms | 3,670 ms | postman 404 | 0 | 0 | 288.3 MB |

Raw rows 7,169, normalized 7,169 and gated 2,028 in **every** arm — consistent,
but read that as "inventory did not change during these minutes", not as parity;
§8 is the parity evidence. Workers=8 ran only because 2, 4 and 6 were clean (no
failure beyond serial's, no retry, no 429), a rule the harness enforces.

**Reading it.** Four workers take ~69% off the Greenhouse wait; the next two
workers buy 2.7 s and the two after that 1.6 s. Against production's ~42.5 s
summed Greenhouse time, a 3.21× ratio would be **~29 s saved a sweep at four
workers** (**INFERRED**: Oracle's network and cores differ from this laptop's).

## 10. Memory and provider pressure

**Memory (MEASURED, per-arm process).** Peak RSS 219.3 MB serial, +14.7 MB at
two workers, **+34.7 MB at four**, +12.2 MB at six, +69.0 MB at eight. It is not
monotonic because the peak depends on which large payloads happen to be parsed
at the same moment (Databricks 8.0 MB, Capco 6.4 MB, Elastic 6.1 MB, Cloudflare
5.6 MB of JSON). The service limit is `MemoryMax=2G`, `MemorySwapMax=0`, with the
sweep child inside that cgroup; V2-A's historical whole-sweep peak was 262 MB.
Four workers' increment is small beside that (**INFERRED**; the production
experiment records peak RSS to confirm). The limits were not changed.

**Provider pressure (MEASURED).** No 429, no retry, no new failure category in
any arm. Median board time is flat (554–653 ms), but summed board time rises
from 34.9 s to 42.6 s at just two workers — which the replay says is **not** CPU
(its two-worker summed time is flat). The per-board pattern locates it: large
payloads slow down under concurrency (MongoDB 1,286 → 3,452 ms, Stripe 1,031 →
2,496 ms at eight) while small ones do not (Shield 443 → 315 ms). That is
**shared bandwidth** on this link: 75 MB per pass cannot download faster than
the pipe, which is also why returns flatten.

**Burstiness.** Serial holds one request open at a time; workers=N holds up to N
against one provider's API. The 54 requests are the same requests either way —
concurrency changes how close together they arrive, not how many there are.

## 11. Tests

`sweep/tests/test_free_concurrency_greenhouse.py` — **37 tests**, sockets denied.
Unit cases stub `ats.fetch` with per-board delays (so a later board finishes
first) and a Postman 404; end-to-end cases drive `scraper.main()` through B3's
offline harness, with Lever concurrency on as in production.

| Brief | Group | Pins |
|---|---|---|
| 1 | `FlagOff` | default off; no deployment file sets it; the serial path never enters the executor and completes in registry order |
| 2–4 | `Determinism` | completion order really inverted at 2/4/6; rows and log lines identical to serial at 1/2/4/6; one survivor everywhere; the worker has nothing shared to append to |
| 5–6 | `FailureAndPostman` | 404 attributed identically serial vs concurrent, totals 6/5/1, same log line; siblings neither cancelled nor lost; all-fail returns cleanly |
| 7–8 | `TelemetryUnderGreenhouseConcurrency` | registry order; per-board counts, retries and failure; equal to serial but the clock; per-board durations; workers never attach to the shared record; native ids stay on their board through the real adapter |
| 9 | `WorkerConfig` | default and clamp table; bounded at the executor at 999; never more in flight than configured |
| 10 | `LeverIndependence` | Greenhouse on leaves Lever serial; independent worker counts; each executor sized by its own setting; Lever rows unchanged |
| 11 | `ShadowIndependence` | only production boards enter the executor; the eight still run, strictly serial; production counts and output untouched |
| 12 | `RegistryUntouched` | 129 / 54 / 5; platform order; SHA-256 of the 54 tokens in order; Postman present; no shadow token |
| 13 | `PaidIsolation` | the benchmark never names an engine entry point or the paid client; the paid path never reaches the executor |
| 14 | `ExportParity` | CSV, JSON and seen ledger byte-identical at 1/2/4/6; every survivor the serial one; twins tie; production telemetry identical, Postman included |
| 15 | (with 3 and 7) | the ordering tests catch any worker-side merge (mutation B); the worker signature has no shared container |

B3's test harness gained two backwards-compatible options (per-board response
delay and a configurable failure status) and clears the new variables in its
base environment. V2-B2's one test whose premise B4 changes deliberately was
re-expressed (§4).

## 12. Mutation checks — all caught

Each applied alone, four suites run (`test_free_concurrency_greenhouse`,
`test_free_concurrency`, `test_search_v2_shadow_eval`, `test_search_v2_telemetry`),
then reverted and verified byte-identical with `cmp`.

| # | Mutation | Result | Among the failures |
|---|---|---|---|
| A | merge futures with `as_completed` | **23 fail** | `test_every_survivor_is_the_serial_one`, `test_csv_json_and_seen_ledger_byte_identical`, `test_log_lines_follow_registry_order` |
| B | workers extend a shared result list | **16 fail** | `test_concurrent_rows_are_identical_to_serial`, `test_every_survivor_is_the_serial_one` |
| C | worker clamp removed (upper bound) | **5 fail** | `test_bounded_at_the_executor_not_just_in_the_number`, `test_default_and_clamp` |
| D | shadow boards added to the Greenhouse executor | **13 fail** | `test_only_production_boards_enter_the_executor`, `test_production_counts_and_output_untouched_by_shadow` |
| E1 | workers attach their own units at **start** (`defer=False`) | **1 fail** | `test_workers_never_attach_to_the_shared_record` — added after the first run missed this (§6) |
| E2 | workers attach their own units on **completion** | **6 fail** | `test_units_in_registry_order_one_per_board`, `test_production_telemetry_identical_including_postman` |
| F | a failed future silently skipped | **11 fail, 1 error** | `test_postman_404_attributed_identically_serial_and_concurrent`, `test_every_board_failing_returns_cleanly` |
| G | a new Greenhouse board added | **4 fail** | `test_greenhouse_boards_neither_added_removed_nor_reordered`, V2-B2 `test_active_source_count_unchanged` |

## 13. Paid-call safety

**No Apify call was made and none was reachable.** Nothing in this stage ran
`scraper.py` as a sweep: the benchmark drives `sources.fetch_free` for one
provider in-process or in children of itself, the tests drive `scraper.main()`
with `--site free` and every response a fixture, and sockets are denied
throughout the test modules. The B3 incident's lesson was applied as structure:
there was no paid plan to prove empty because no code path here can build one.

## 14. Unresolved risks

1. **UNKNOWN — provider tolerance from Oracle over a production day.** One host,
   one day, one pass per arm. Greenhouse's production baseline is 0 retries in
   162 fetches; a nonzero retry or any 429 in the experiment is the signal.
2. **INFERRED — the speed-up transfers.** This link was bandwidth-limited at 75 MB
   a pass; Oracle's datacenter link may do better (more gain) and its slower
   cores may do worse on parsing (the replay's eight-worker CPU effect).
3. **UNKNOWN — interaction with Lever concurrency in one sweep's wall time.** The
   two executors never overlap (providers run in sequence), so the effects should
   add; measured only offline.
4. **Tail inflation.** p95 board time rises from 1.3 s to 2.2 s at four workers
   and 2.8 s at eight. Per-board durations under concurrency are not comparable
   to serial ones; compare wall spans, not per-board times, in the experiment.
5. **Memory is payload-dependent** and not monotonic in workers; a board that
   grows (Databricks is 885 postings) raises the ceiling for every arm.
6. **B3's shadow experiment is running.** Enabling Greenhouse concurrency during
   it would add a second latency variable to shadow's measurements; hence the
   sequencing in §15.

## 15. Proposed production experiment — NOT executed

Only after B3's shadow sample is complete, or shadow is explicitly paused.

**Phase 1 — two workers, one new variable**

```
SWEEP_SEARCH_V2_TELEMETRY=1
SWEEP_FREE_LEVER_CONCURRENCY=1
SWEEP_FREE_LEVER_WORKERS=4
SWEEP_FREE_SOURCE_SHADOW=0            # off, so Greenhouse is the only change
SWEEP_FREE_GREENHOUSE_CONCURRENCY=1
SWEEP_FREE_GREENHOUSE_WORKERS=2
```

A small number of natural Free Sweeps. Monitor against the serial baseline
(~42.5 s summed, 0 retries): Greenhouse board count (must stay 54), Greenhouse
**wall span** (first Greenhouse unit start to last finish), summed Greenhouse
duration, retries, failures by category, HTTP 429, p50/p95 per-board duration,
total Free phase, final result counts in their normal range, peak RSS.
**Report Postman's 404 separately**: it is the baseline, not a regression.

**Phase 2 — four workers**, only if phase 1 shows no new failure category, no
retry, no 429, and a wall-span saving in line with §9.

Do not jump to 6 or 8: §9 says they buy 1.6–2.7 s each for more simultaneous
requests, a higher tail and more memory.

**Success criteria** (for the experiment, not this commit): Greenhouse wall span
down materially, zero new failure categories, retries still zero, no 429, final
counts normal, telemetry still one unit per board in registry order.

## 16. Rollback

One flag: `SWEEP_FREE_GREENHOUSE_CONCURRENCY=0` (or remove it). No code revert.

**A service restart is required** (**VERIFIED** from `docs/oracle-sweep-worker.md`
and `deploy/sweep_worker.py`): the worker's systemd unit loads
`EnvironmentFile=/etc/sweep-worker/env` at process start, and each sweep child
inherits the worker process's environment (`default_spawn` copies
`os.environ`). The flag is read per call *inside* a sweep, but a sweep only sees
what the worker was started with. So: edit `/etc/sweep-worker/env`, then
`sudo systemctl restart sweep-worker` while no sweep is running — a restart
marks an in-flight sweep interrupted and re-queues queued free ones. The same
applies to enabling it.

## 17. Recommendation from the measured evidence

**Greenhouse concurrency is worth a later production experiment, at two then
four workers, and not beyond four without new evidence.**

- The **gain** is material: ~24 s of a ~35 s wait at four workers here,
  plausibly ~29 s of production's ~42.5 s — about half of what V2-B2 took off
  Lever (~61 s).
- The **risk to output** is nil on the evidence: byte-identical in every replay
  arm, survivors identical under inverted completion, telemetry exact.
- The **cost** is modest at four: +35 MB peak, a higher per-board tail, no
  retries, no 429.
- **Past four the return collapses**: 2.7 s for six workers, 1.6 s more for eight,
  at +69 MB and a 2.8 s p95 — the pipe, not the worker count, is the limit.

It stays OFF in this commit, and should stay off until B3's sample is done.
