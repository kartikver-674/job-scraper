# V2-B2 — deterministic bounded concurrency for Free Lever fetching

Date: 2026-09-23. Baseline: `625c02e` (V2-B1), which is what production runs.
Scope: **Lever ATS board acquisition only.** Default **OFF**. Not deployed.

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a dated
probe), **VERIFIED** (directly observed code or provider documentation),
**INFERRED** (a model with stated assumptions), **UNKNOWN**.

Nothing else moved. No batching, no paid concurrency, no caching, no incremental
persistence, no adaptive depth, no source-health routing, no registry change, no
board removed, prioritised or skipped. Profile Engine V3, title gates, ranking,
weights, dedupe, `job_key`, Search Preferences, location/recency/salary/
arrangement semantics, the shadow tranche, SmartRecruiters pagination, WWR, and
every paid path are untouched. `SWEEP_EXPERIENCE_MISMATCH_GUARD` remains off.

## 0. Why

**MEASURED**, three real production Free Sweeps with V2-A telemetry on
(`SWEEP_SEARCH_V2_TELEMETRY=1`): the Free phase runs 140.19–163.93 s, and Lever
is where it goes.

| Provider | Boards/run | Source-seconds/run | Median | p95 | Retries | Failures |
|---|---:|---:|---:|---:|---:|---:|
| **Lever** | **21** | **~80.4** | **3.14 s** | **8.17 s** | **0** | **0** |
| Greenhouse | 54 | ~42.5 | 0.34 s | 2.08 s | 0 | 0 |
| Ashby | — | — | 0.32 s | — | 0 | 0 |
| SmartRecruiters | — | — | 0.21 s | — | 0 | 0 |
| Breezy | — | — | 0.44 s | — | 0 | 0 |

Zero retries and zero failures across 63 Lever executions. **Lever is not flaky,
it is slow** — roughly 9× Greenhouse's median on a third of the boards. That is
the one shape of problem waiting in parallel actually fixes, and it is why this
change is scoped to one provider rather than generalised.

The slow boards are latency evidence and **nothing else**. `lever:binance`
(~5.84 s) and `lever:coderio` (~4.27 s) gated zero rows in 3/3 runs, but three
runs cannot judge an employer's worth, and **no board was removed, reordered or
deprioritised**.

## 1. Current serial architecture (VERIFIED)

`sources/__init__.py:fetch_free` walks `config.ATS_BOARDS` in dict order,
platform by platform and board by board, and inside each iteration:

```
for platform, boards in ats_boards.items():
    for token, company in boards.items():
        with telemetry.unit(...):          # opens the "current" unit
            try:   rows.extend(ats.fetch(...))     # appends into the shared list
            except: telemetry.failed(exc); log(...)
```

Three properties fall out of that shape, and all three had to survive:

1. **Registry order is result order.** `rows` is built by `extend` in iteration
   order, and that order reaches `finalize` → sort → `dedupe`. Python's sort is
   stable, so **equal scores are broken by arrival order**, which means registry
   order selects the surviving row of a duplicate pair.
2. **Failure is isolated per board** by the inner `try`.
3. **One telemetry unit per board**, opened and closed around the fetch.

## 2. The concurrency boundary (VERIFIED)

Exactly one call site changed, in `fetch_free`:

```python
if concurrency.applies(platform):
    rows.extend(concurrency.fetch_boards(
        platform, boards, keep_title, keep_location, is_home, log))
    continue
for token, company in boards.items():
    ...unchanged serial loop...
```

`concurrency.applies(platform)` is `enabled() and platform in PROVIDERS`, and
`PROVIDERS = ("lever",)`. Everything else — Greenhouse, Ashby, SmartRecruiters,
Breezy, all five feeds, Optum, the enterprise employers, the shadow tranche and
every paid actor — takes the path it took before, byte for byte.

Concurrency wraps **only the independent HTTP acquisition of one provider's
boards**. Normalisation, the title/location acquisition gate, scoring, filtering,
recency, dedupe, ranking and output are downstream of the merge and were not
touched.

## 3. The deterministic merge

```python
items   = list(boards.items())                       # registry order, frozen
futures = [pool.submit(_fetch_one, ...) for ... in items]   # all queued at once
results = [f.result() for f in futures]              # collected in THAT order
```

**Why completion order cannot become result order:** there is no completion-order
code path to get wrong. `as_completed` is never called. Futures are collected by
iterating the submission list, so `results[i]` is board `i` by construction, not
by convention. A board that finished first is simply a future that is already
done when the coordinator reaches it — `.result()` returns immediately and the
work still overlapped.

Three further rules make that airtight:

- **No worker touches shared state.** `_fetch_one` returns an isolated
  `BoardResult(token, company, rows, error, unit)`. Workers never append to
  `rows`, never touch dedupe/score/ranking state, never write a file.
- **The coordinator alone merges.** The calling thread walks `results` in
  registry order to extend the row list, emit the log lines and attach the
  telemetry units.
- **Log order is registry order too**, because the log lines moved to the
  coordinator. *This is the one observable behavioural difference:* Lever's 21
  log lines now arrive together when the provider finishes instead of trickling
  out. It changes when the log is written, not what the sweep produces.

## 4. Flags (VERIFIED)

| | |
|---|---|
| `SWEEP_FREE_LEVER_CONCURRENCY` | default **OFF**. `1/true/yes/on` enables. Read per call, so rollback lands on the next sweep. |
| `SWEEP_FREE_LEVER_WORKERS` | default **4**, clamped to **1..8** |

Clamped rather than rejected: this is a latency knob on a path that is off by
default, and refusing to run a sweep over a bad number would turn a performance
setting into an outage. `0`, `-9` → 1. `999` → 8. `banana`, `4.5`, empty → 4.
**`workers=1` is byte-identical to serial** and is measured as such (§8).

The 8 cap exists because 21 boards are one provider's servers and an environment
typo should not become 21 simultaneous requests at somebody else's host. The
clamp is asserted at the *executor*, not just as a returned integer — a test runs
21 boards at `workers=999` and asserts peak in-flight ≤ 8.

Neither flag is set in `render.yaml`, `deploy/sweep_worker.py` or
`requirements.txt`; a test asserts that.

## 5. Telemetry thread-safety — the real finding

**V2-A telemetry is ON in production, so this was the highest-risk surface, and
it was NOT safe.** Audited every telemetry call reachable during source fetching:

| Call | Reached from | Mutates |
|---|---|---|
| `unit()` | `fetch_free` / `concurrency._fetch_one` | the "current unit" pointer; `_run["units"]` |
| `observed()` | **inside `ats.fetch`** | the current unit's counters |
| `retried()` | **inside `sources/_http.get_bytes`** | the current unit's retry count |
| `failed()`, `partial()` | adapters and `fetch_free` | the current unit |
| `_Unit.__exit__` | every unit | the current unit pointer; `_run` counters |
| `mark()` | `observed()` | `_run["milestones"]` |

**VERIFIED defect.** The open unit was a **module global** — one "current unit"
pointer for the whole process:

```python
_unit = None          # before
def unit(...): global _unit; _unit = {...}
def observed(...):    _unit["raw_count"] += raw
```

Correct exactly while acquisition is serial, and silently wrong the moment it is
not. Two threads in `unit()` clobber one another's pointer, and every
`observed()`/`retried()`/`failed()` between then and `__exit__` lands its counts
on **whichever board happened to be current**. That is not a crash; it is a
telemetry file that confidently attributes one board's rows and retries to
another. Separately, `_run["sources_attempted"] += 1` is read-modify-write and
`mark()` is check-then-set — **neither of which the GIL makes atomic.**

**The fix, smallest that is actually correct.** Two kinds of state, two
mechanisms:

- **The open unit is thread-confined** — `threading.local()`. Each thread owns
  its record from `unit()` to `__exit__`, so it needs no lock. A lock would not
  have helped anyway: the bug was shared *identity*, not unsynchronised mutation.
- **The run record is shared** — every mutation of counters, milestones, notes,
  stages and the units list goes through one `threading.RLock`.

**Deterministic unit ordering.** `unit(defer=True)` opens a unit without
attaching it to the run; the coordinator calls `telemetry.attach(record)` in
registry order once its workers are done. So the telemetry file's unit order is
the registry's, not the order threads happened to start — the same reason the
rows are merged in registry order. `_count()` folds the unit into
`sources_attempted/succeeded/failed` at attach time, so a deferred unit is
counted exactly once.

Telemetry was **not** redesigned: no field changed meaning, the schema id is
still `search-v2a.1`, and the serial path produces byte-identical records.

**MEASURED proof** — reverting the thread-local to a module global (mutation E)
fails three tests, including `test_counts_land_on_the_right_board`, with the
exact cross-attribution predicted above.

## 6. Failure semantics (VERIFIED, unchanged)

`_fetch_one` catches per board exactly as the serial loop does, so:

- one Lever board failing costs that board and nothing else;
- successful rows survive and stay in registry order;
- the failure is recorded once, in that board's own telemetry unit and once in
  the log;
- a sweep never fails because a board did.

**Nothing about HTTP behaviour changed**: same 25 s socket timeout, same
`get_bytes` retry policy (2 retries, 1 s + 2 s backoff, no retry on 4xx), same
failure categories, same exception semantics. No retries added, no timeout
shortened. A test asserts concurrent failure behaviour is identical to serial,
including the log lines.

## 7. Test coverage

`sweep/tests/test_free_concurrency.py` — **47 tests**, sockets denied for the
whole module. Stubs give each board a deliberate delay so registry order is
`a,b,c,d` and completion order is `d,b,c,a`; a test asserts that completion order
really is reversed, because a determinism test whose stub happens to finish in
order proves nothing.

| Group | Pins |
|---|---|
| `FlagOff` (4) | default off; no deployment file sets it; exact serial call order and row order; never enters the executor |
| `WorkerConfig` (4) | default 4; clamping; nonsense falls back; never unbounded |
| `DeterministicOrder` (5) | `workers=1` ≡ serial; completion order really is out of order; rows merge in registry order at 2/3/4 workers; concurrent rows identical to serial; log lines in registry order |
| `MaxInFlight` (4) | peak in-flight ≤ configured workers; 21 boards still bounded; **`workers=999` still bounded at the executor**; concurrency actually happens |
| `ProviderScope` (4) | only Lever applies; paid sites can never apply; `scrape_search` does not even mention the module; Greenhouse stays serial in the same sweep |
| `FailureIsolation` (4) | others survive and order holds; failure recorded once; identical to serial; all-fail returns cleanly |
| `DuplicateSurvivor` (4) | the fixture really is one posting to dedupe; completion order reversed; **same survivor serial and concurrent**; stable across worker counts |
| `DownstreamParity` (3) | final rows identical through the real `finalize`; scores and order identical; **written CSV and JSON byte-identical** |
| `TelemetryUnderConcurrency` (8) | exactly one unit per board, no duplicates or losses; units in registry order; counts land on the right board; source totals match serial; failures attributed only to failing boards; written file valid with no internal keys; per-board durations not cumulative; thread-local isolation directly |
| `ShadowIndependence` (4) | shadow boards are Greenhouse so cannot enter the Lever path; neither flag enables the other |
| `RegistryUntouched` (3) | 129 boards + 5 feeds; 21 Lever boards; no board skipped or reordered |

### The duplicate-survivor test

The one that matters most. Two boards publish the same posting — same company,
same title, so `job_key` collapses them — differing only by apply URL. The
duplicate lives on board `d`, which finishes **first**, and board `a`, which
finishes **last**. Scores are equal, so nothing but arrival order decides.

**MEASURED:** `a` survives under serial and under 1/2/3/4 workers. Merging on
completion would hand the win to `d`, and the test says so by asserting the exact
surviving `apply_url`.

### Mutation checks — all five caught

| # | Mutation | Result |
|---|---|---|
| A | Merge futures in completion order (`as_completed`) | **11 tests fail**, including duplicate-survivor, CSV/JSON parity and telemetry unit order |
| B | Remove the worker clamp | **3 fail**, including the executor-level in-flight bound at `workers=999` |
| C | Let workers append directly to a shared list | **8 fail**, including row order and duplicate survivor |
| D | Add Greenhouse to `PROVIDERS` | **4 fail**, including provider scope and shadow independence |
| E | Revert telemetry to a module-global current unit | **3 fail**, showing cross-board count attribution |

## 8. Offline parity and speed-up (MEASURED, deterministic)

`bench/search_v2_free_concurrency.py --capture` took one serial public pass over
all 21 configured Lever boards on 2026-09-23 (72.99 s, 1,949 items, 0 errors) and
froze each payload with its measured latency. `--replay` then ran serial and
1/2/3/4 workers over that frozen capture, replaying each board's recorded latency
so completion order genuinely differs from registry order.
[Evidence](search-v2-evidence/free-concurrency-replay.json).

| Arm | Wall | Speed-up | Acquired rows | Final rows | Peak RSS | Final SHA-256 |
|---|---:|---:|---:|---:|---:|---|
| serial | 73.30 s | 1.00× | 217 | 2 | 133.72 MB | `eee692d0fcb3…` |
| workers=1 | 73.31 s | 1.00× | 217 | 2 | 133.72 MB | `eee692d0fcb3…` |
| workers=2 | 38.76 s | 1.89× | 217 | 2 | 133.72 MB | `eee692d0fcb3…` |
| workers=3 | 26.93 s | 2.72× | 217 | 2 | 133.72 MB | `eee692d0fcb3…` |
| workers=4 | **20.49 s** | **3.58×** | 217 | 2 | 133.72 MB | `eee692d0fcb3…` |

**All arms byte-identical to serial**: identical 217-row source sequence,
identical final SHA-256, identical apply URLs, identical scores.
`workers=1` matches serial to 10 ms, which is the property the design claims.

Scheduling reference from the same capture: serial 72.99 s; the perfect-4-way
lower bound is `max(longest board, total/4) = max(12.11, 18.25) = 18.25 s`.
Measured 20.49 s is **12% off that bound** — the greedy scheduling model was
roughly right here, and the residual is Veeva's single 12.11 s board plus
thread-pool overhead. Note the bound is set by the slowest *single* board, so
more workers cannot help much beyond 4 on this registry.

Peak RSS is identical across all five arms. The rows exist either way; only the
order of waiting changed.

## 9. Live public benchmark (MEASURED — latency only)

`--live` ran all four arms back to back against the real public Lever API on
2026-09-23, 21 boards each, 84 GETs total, **zero Apify credits**.
[Evidence](search-v2-evidence/free-concurrency-live.json).

| Arm | Wall | Speed-up | Summed board time | Median board | Slowest board | Failures | Retries | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| serial | 85.21 s | 1.00× | 85.21 s | 3009 ms | 17503 ms | 0 | 0 | 134.48 MB |
| workers=2 | 40.51 s | 2.10× | 76.34 s | 3069 ms | 12493 ms | 0 | 0 | 152.02 MB |
| workers=3 | 30.17 s | 2.82× | 77.97 s | 3054 ms | 12359 ms | 0 | 0 | 155.47 MB |
| workers=4 | 24.25 s | 3.51× | 81.23 s | 3074 ms | 11030 ms | 0 | 0 | 173.66 MB |

Acquired rows were 217 and raw items 1,949 in **every** arm — consistent, but
read that as "inventory happened not to change during these four minutes", not
as parity evidence. §8 is where parity is proven.

The two numbers that matter most for provider safety:

* **Median board time is flat** — 3,009 ms serial against 3,074 ms at four
  workers, a 2% difference well inside run-to-run noise. Boards do **not** get
  slower when four of them are in flight, so the speed-up is real overlap and
  not latency shuffled around.
* **Summed board time did not inflate** — 85.21 s serial against 76–81 s
  concurrent. If Lever were throttling under concurrency this is the number that
  would rise, and it did not.

The slowest board fell from 17.50 s to 11.03 s, which is Veeva's 922-item board
varying between passes rather than anything concurrency did for it.

**This arm may not claim parity.** Lever inventory changes between arms, so a row
difference here is not evidence about determinism. §8 is the parity evidence;
this is the latency and provider-behaviour evidence.

## 10. Benchmark questions, answered

1. **Does concurrency reduce real Lever wall time?** **Yes, MEASURED.** Live Lever wall time fell from
   **85.21 s to 24.25 s** at four workers — 3.51×, about
   **61 s** off a Free phase that production measures at 140–164 s. The offline
   replay agrees independently (3.58×).
2. **Does workers=4 materially beat 2 and 3?** **Materially over 2, marginally over 3.** Live:
   2.10× at two, 2.82× at three,
   3.51× at four. Four buys ~6 s over three and ~16 s over
   two. Returns are visibly flattening, and they must: the perfect-parallel floor
   is the slowest single board (Veeva, 11–17 s), so no worker count takes this
   registry much below ~15 s.
3. **Provider errors or rate limits under concurrency?** **None observed.** Zero failures and **zero retries** at every
   worker count, so no 429, no transport error and no throttle in this window.
   That matches the production serial baseline, which is also zero. **UNKNOWN**
   whether it holds from Oracle's IP over a full day — one benchmark from one
   host is not a rate-limit study.
4. **Does any board become materially slower?** **No.** Median board time is 3,009 ms serial against
   3074 ms at four workers (+2%, inside noise), and summed board
   time did not rise. No board became materially slower.
5. **Does memory increase meaningfully?** **A modest, real increase.** Peak RSS 134.48 MB serial →
   173.66 MB at four workers (+39 MB, +29%),
   from holding up to four board payloads in flight — Veeva alone is 922 items.
   Reported rather than dismissed: the historical worker figure is 262 MB peak
   for a whole sweep, so this is not free. The frozen replay showed *no* RSS
   change (133.72 MB in all five arms) because its payloads were already in
   memory, which is exactly why the live figure is the one to quote.
6. **Is output byte-identical on deterministic replay?** **Yes.** §8: identical
   source sequence, final SHA-256, apply URLs and scores at every worker count.
7. **Does telemetry remain valid?** **Yes**, once made thread-safe. Exactly one
   unit per board, registry-ordered, correct per-board attribution, source totals
   matching serial. Mutation E shows what it looks like when it is not.
8. **Is 4 a reasonable first production shadow setting?** **Reasonable as a target, not as a first step.** Four is the
   best measured setting and shows no provider stress here, but this evidence is
   one host and one window. The proposal in §12 starts at **two** — which already
   captures 2.10× of the available 3.51× — and moves
   to four only after a clean production phase.

## 11. Unresolved risks

1. **UNKNOWN: Lever's tolerance over a full production day.** One benchmark on
   one host from one IP is not a rate-limit study. Production runs from Oracle,
   not this laptop.
2. **UNKNOWN: behaviour under concurrent *sweeps*.** `MAX_ACTIVE=1` means one
   sweep at a time today, so 4 workers is 4 sockets. If that ever changes, the
   bound is per sweep, not global.
3. **UNKNOWN: whether any Lever board rate-limits under sustained concurrency.**
   Retries are recorded per board, and the current production baseline is zero,
   so a nonzero retry count in a shadow run is the signal to watch.
4. **INFERRED: the speed-up transfers to production.** The replay used recorded
   latencies and the live arm used this network. Oracle's network is different.
5. **VERIFIED but worth stating: the slowest single board sets the floor.**
   Veeva's 12.11 s (922 items) means no worker count takes this registry below
   ~12 s. Past 4 workers the return is small and the provider pressure is not.
6. **UNKNOWN: interaction with the shadow tranche.** Untested together and
   deliberately so — the first concurrency experiment should not have two
   variables.
7. **Log timing changed** (§3). Harmless, but a human watching `engine.log` will
   see Lever's lines arrive in a block.

## 12. Proposed production experiment — NOT executed

Do not deploy with this commit. Proposed shape, for separate approval:

**Phase 1 — `workers=2`**

```
SWEEP_FREE_LEVER_CONCURRENCY=1
SWEEP_FREE_LEVER_WORKERS=2
SWEEP_FREE_SOURCE_SHADOW=0        # unchanged: one variable at a time
SWEEP_SEARCH_V2_TELEMETRY=1       # unchanged: this is how we measure
```

A small number of real Free Sweeps. Compare against the three-run serial
baseline: total Free phase, Lever summed source-seconds, per-board durations,
failures by category, retries (baseline is **0** — any increase is the signal),
raw/normalized/gated counts, `first_raw`, `first_eligible`, peak RSS.

**Phase 2 — `workers=4`**, only if Phase 1 shows no new failures, no retries and
no board materially slower.

**Rollback is instant and total:** `SWEEP_FREE_LEVER_CONCURRENCY=0`. The flag is
read per call, so the next sweep is serial; no deploy, no restart, no state to
unwind.

**Success criteria for the experiment** (not for this commit): Lever wall time
down materially, zero new failure categories, retries still zero, final row
counts in the normal run-to-run range, and telemetry units still one-per-board.

## 13. Optional all-source model — NOT implemented

The offline scheduling model in the task brief (4-way all-source makespan of
35–41 s against 140–164 s serial) is **retained as a model only**. V2-B2 is Lever
only. Greenhouse's 0.34 s median means its 54 boards cost ~42 s of a ~150 s
phase, and parallelising a fast provider trades real provider pressure for less
time than Lever gives. All-source concurrency is a separate decision that should
wait for this one's production evidence.
