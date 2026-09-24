# V2-C2 — the budget holds every ceiling it lets start; paid searches wait in parallel

Date: 2026-09-24. Baseline: `a3b4bd2` (V2-C1; C0 `89c8daa` and C1 are local and
unpushed). Production runs V2-B5 with `SWEEP_SEARCH_V2_TELEMETRY=1`, Lever 1/4,
Greenhouse 1/4, `SWEEP_FREE_SOURCE_SHADOW=1` and `SWEEP_RESULTS_READY_EARLY=1` —
none of which this stage touches.
Scope: **reservation-safe paid budget accounting, bounded deterministic
concurrency for provider-bounded paid searches, and the paid checkpoint's CPU.**
Behind `SWEEP_PAID_CONCURRENCY`, default **OFF**. Not deployed; no deployment or
environment file changed.

Not in scope, and not done: plan compaction, batching, fewer actors, adaptive or
changed depth, provider prioritisation, scoring, ranking, dedupe, Search
Preferences, polling, an Indeed/Naukri redesign, pricing UX.
`SWEEP_EXPERIENCE_MISMATCH_GUARD` remains off.

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a dated
probe), **VERIFIED** (directly observed code or configuration), **INFERRED** (a
model with stated assumptions), **UNKNOWN** (evidence unavailable, which is not
the same as zero).

## 0. Headline findings

1. **VERIFIED — the reservation invariant holds by construction and by test.**
   Before a provider-bounded search can start, the coordinator reserves its
   **full provider ceiling**; from the instant before the start request the hold
   is **committed for the rest of the sweep**; no reading — terminal run record,
   account delta, settled research charge — can hand any of it back. The brief's
   13 budget cases are pinned end to end, the budget ones at 1, 2 and 4 workers
   (§3–§6, §21).
2. **MEASURED live — the provider accepted two simultaneous bounded starts and
   the lag was worse than C1 saw.** Two LinkedIn runs started 1 ms apart,
   overlapped 11.1 s, each carried `maxTotalChargeUsd` 0.046, 0 HTTP 429s and 0
   SDK retries. At completion each run record read **$0.00005** — the start event
   alone — and the account delta the old guard reads was **$0.0001 for both runs
   together**; they settled at $0.03005 each 19.5–23.6 s later. The old guard
   would have believed $0.0001 was spent; C2 held $0.092 (§24).
3. **MEASURED (offline) — byte-identical output at every worker count**: serial,
   1, 2, 3, 4 workers write the same CSV, JSON, seen ledger and done ledger,
   including the duplicate tie where the later search finishes first (§19).
4. **MEASURED (offline) — C2 at one worker *is* the serial loop**: the same
   requests in the same order — start, polls, dataset, account read — and the
   same C1 record but for clocks (§19).
5. **MEASURED (offline) — the checkpoint bottleneck is removed, not hidden.**
   `score_job` is 99.7% of a finalize pass; scoring each row once instead of once
   per checkpoint takes V2-C1's 90-search replay from **226.8 s to 5.9 s of CPU
   (38×)** — 63,338 scoring-row equivalents to ~1,453 — with the same 91 passes,
   files and order (§16, §23).
6. **MEASURED (offline, provider time scaled 1:10) — wall time falls as the
   model says it should.** C1's mixed 90-search plan: **509.8 s serial → 289.3 s
   (1 worker) → 221.1 (2) → 197.5 (3) → 187.1 (4)**; its 45 LinkedIn searches
   alone: **194.0 → 137.9 → 69.8 → 46.2 → 36.3 s**. Every C2 arm is within ~4 s
   of the wait-only schedule plus its measured CPU. Concurrency alone gives
   1.98× / 2.98× / 3.80× at 2 / 3 / 4 workers on LinkedIn; on the mixed plan the
   Indeed half, serial by design, caps it at 1.55× (§23).
7. **VERIFIED — a product consequence the review must weigh before C5.** The
   public app caps a sweep at 1.25 × its estimate, which prices a LinkedIn search
   at $0.03375 against a $0.046 ceiling. Holding full ceilings stops a
   LinkedIn-only public sweep of 11 or more searches at ~70% of its plan (18 → 13).
   Deliberate safety behaviour, not hidden in a test — and a reason not to enable
   the flag on the public worker until that cap is computed from ceilings (§20).
8. **VERIFIED — the committed C1 baseline failed one test.** C1's overhead
   benchmark matched C0's reachability scan but was never classified: the scan
   read tracked files only and the file was untracked when C1's suite ran.
   Classified now, and the scan reads untracked files too (§21).
9. **MEASURED — 19 targeted mutations, all caught**, every file restored
   byte-identical (§22).
10. **Spend.** C2 made **2 LinkedIn starts: $0.092 intended, $0.0601 actual**.
    The ledger now holds **$0.276 intended / $0.1803 actual** of the shared $2.00
    C1–C4 ceiling (§25).

## 1. Paid execution before C2 — VERIFIED

`scraper.main()`, per search, in plan order (`SITES` order: LinkedIn, Indeed,
Naukri; each site's plan keyword-outer, location-inner):

```
skip if today's .done_combos has it
stop the phase if budget is not None and spent >= budget
telemetry unit ─ scrape_search: build input; LinkedIn: max_charge_usd + fail-closed
                 SDK check; actor.start(max_total_charge_usd); poll every 5 s to a
                 360 s deadline, then abort; dataset read + normalize
               ─ account read; spent = usage - baseline (else += run-record cost)
               ─ provenance; raw_rows.extend; emit(raw_rows): finalize ALL rows,
                 write CSV + JSON (B5: temp + fsync + rename)
               ─ append .done_combos; print
failure (except Exception): recorded failed, NO account read, loop continues
```

The guard's figure `spent` is read after a search and lags (B1 §6, C1 §10). It
cannot bound the next start; B1's provider ceiling bounds one run at the provider.

## 2. C1's evidence, carried forward

| Reading (C1 §10, n = 4) | At completion | Settled |
|---|---|---|
| terminal-poll run record (`usageTotalUsd`) | $0.00205–$0.02805 (7–93% short) | $0.03005 at +6–23 s |
| account delta the guard reads | as low as $0.00005 (the start event) | covered at +22–39 s, then a residual no run owns |
| a "final" flag from Apify | none exists | — |

C2's own canary made it starker (§24): both run records read $0.00005 at
completion and the account delta read $0.0001 for two runs. **No reading
available when the next start is decided says what the previous start cost.**

## 3. The reservation invariant

For every provider-bounded start:

```
decide    view = max(observed, committed + pending + unbounded)
          admit  ⟺  budget is None  or  view + ceiling <= budget     (Decimal)
          else   the start is refused and the paid phase stops (as the cap always has)
before    reserve: pending += ceiling                  (coordinator, under the lock)
start     commit:  pending -= ceiling; committed += ceiling   (the instant before the request)
after     nothing ever lowers `committed` in this sweep
```

The brief's example, pinned: budget $0.10, ceiling $0.046 — first start $0.046
committed, second $0.092, third refused ($0.138 > $0.10), even though the two
runs settle at $0.03005 each (`ReservationBudget.test_1…`, at 1, 2 and 4 workers).

## 4. Pending vs committed, and when a hold may go back

| State | Set by | Meaning |
|---|---|---|
| `pending` | coordinator, `PaidExposure.reserve` | ceiling held; the start request not yet attempted |
| `committed` | worker, `PaidExposure.commit` via `scrape_search(before_start=…)` | the request is about to be sent; permanent for the sweep |
| `released_before_network` | worker, `PaidExposure.release` | the start was never attempted; the hold went back |
| `blocked` | coordinator | refused before any request; nothing held |
| `unbounded` | coordinator, `PaidExposure.admit` | no ceiling exists to hold (§9) |
| `none` | — | skipped as done: no reservation |

**May be released** — only a `pending` hold, only when nothing was sent: the
fail-closed SDK check (an apify-client without `max_total_charge_usd`), anything
raising in `scrape_search` before `before_start` (input build, `client.actor`,
the ceiling), or the worker's own client construction.

**Must never be released** — once `before_start` ran: a successful run, a
`FAILED`, `ABORTED` or `TIMED-OUT` status, the engine's 360 s deadline abort, a
start call that raised (network error, timeout, a 4xx including a usage-limit
refusal) with or without a run id, a dataset or account read failure, a
checkpoint failure, a crash. "No run id means no charge" is not assumed.

## 5. Why observed cost is never reusable budget

1. **No finality**: Apify publishes none; C1's "settled" is stability over
   re-reads, research-only.
2. **Lag**: every production reading was short when taken (§2, §24).
3. **Attribution**: an account delta mixes runs (C1: A2's delta was A1's charge
   arriving) and carries usage no run owns.
4. **Cost of the rule**: a LinkedIn start holds $0.046 and settles ~$0.03005, so
   ~$0.016 a start (35%) of headroom is unused for the rest of the sweep. That is
   the price of never authorising on a number known to be low.

C2 may *report* the difference (`cost_observations` are untouched); it never
turns it into spendable budget.

## 6. The formulas

**Sweep budget** (`SETTINGS["max_spend_usd"]`; the public app writes
`spend_cap_for(estimate)` into the profile):

```
observed   = the pre-C2 `spent` (account usage - baseline, else Σ usage_total_usd),
             folded in with max() so it is monotone
committed  = Σ ceilings of bounded starts that may have reached the provider
pending    = Σ ceilings reserved, not yet attempted
unbounded  = Σ over unbounded searches of max(0, spent_after - spent_before)
view       = max(observed, committed + pending + unbounded)

bounded start    admitted ⟺ view + ceiling <= budget
unbounded start  admitted ⟺ view < budget          (the pre-C2 test, on the C2 view)
```

`observed` can only raise the view, so C2 is never less cautious than the old
guard, and a reading that goes down (a lagging read, a month rollover) returns
nothing (`ExposureLedger.test_observed_readings_only_ever_add`).

**Account headroom** (VERIFIED, unchanged): `left = maxMonthlyUsageUsd −
monthlyUsageUsd`, read once by `_require_token()` at sweep start and only when
two or more tokens are configured; the account with the most is used for the
whole sweep. It is an input to *which* account, not to *whether* a start fits —
there is no per-start account check before or after C2. The provider's own hard
monthly limit refuses starts past it, and C2 counts such a refused start as
committed (§4).

## 7. Token and account selection — the audit

| Question | Answer |
|---|---|
| Can units in one sweep use different accounts? | **No** (VERIFIED). `main()` calls `_require_token()` once and builds one client; nothing re-selects mid-sweep. |
| Is selection headroom-based? | **Yes, once**, at sweep start, among `APIFY_TOKEN`, `APIFY_TOKEN_2`, … when there are two or more (`_token_headroom`: free GET per token). |
| Could two concurrent units choose the same account on stale headroom? | **No.** C2 keeps selection in the coordinator, before any unit; each worker's client is built from the already-chosen token through a closure (`make_client`), and no worker ever selects. Pinned: `_require_token` called once, headroom read once per token, every client built with the chosen token (`AccountSelection`, mutation K). |
| Does the public worker expose only the visitor's BYOK token? | **Yes** (VERIFIED, C0): `default_spawn` strips every `APIFY_TOKEN*` and sets the visitor's as `APIFY_TOKEN`; with one token no headroom is read (`test_one_byok_token_reads_no_headroom_and_is_used_throughout`). C0 risk #7 stands: a `.env` on the worker holding `APIFY_TOKEN_2` would add a second account to the one-time choice (INFERRED absent). |
| Token values recorded? | **Never**: not in telemetry, outputs or logs (`test_no_token_value_reaches_any_output`). |

**Multi-token behaviour under C2**: one account per sweep, chosen once; the
reservation ledger is therefore per sweep *and* per account. No account label
is recorded — with one account per sweep it would say nothing a reader needs.

**Client per search (VERIFIED in apify-client 3.1.0)**: `ApifyClient` keeps
`_statistics` counters incremented without a lock (`calls += 1`,
`requests += 1`) and creates its HTTP client lazily (check-then-set); nothing
documents it as thread-safe. Each paid search builds its own client on the same
token; the coordinator keeps one for account reads. Construction sends nothing.

## 8. External spend — the limit of a local guarantee

Reservations are process-local. Spend on the same account by anything else — the
operator's console, another tool, another Sweep process — is invisible until an
account read shows it, and that read lags (§2). The `max(observed, …)` term
folds it in once visible; it cannot anticipate it. Oracle runs Sweep children one
at a time (`MAX_ACTIVE=1`), which bounds Sweep's *own* concurrency across sweeps,
not other consumers. No global lock, Redis or database was added (per the brief).
**C2's guarantee is: this sweep's bounded starts cannot exceed this sweep's
budget, given no one else spends from the account meanwhile.**

## 9. Bounded and unbounded providers

| Provider | Ceiling today | Under C2 |
|---|---|---|
| **LinkedIn** (`curious_coder/linkedin-jobs-scraper`) | `maxTotalChargeUsd`, B1's model, $0.046 at depth 15; recorded by the provider on every C0/C1/C2 run | **bounded**: reserved, up to `SWEEP_PAID_WORKERS` at once |
| **Indeed** (`misceres/indeed-scraper`) | **none** — `ACTOR_CHARGE_MODEL` has no entry (B1 §4) | **unbounded**: one at a time, as before; admitted on the old test; no reservation number invented |
| **Naukri** (`muhammetakkurtt/naukri-job-scraper`, disabled by default) | **none** | **unbounded**, likewise |

No provider-enforced hard ceiling was found for Indeed or Naukri in current code,
so neither was parallelised and neither was run live.

**Mixed plans** run site by site in plan order. Committed LinkedIn ceilings stay
in the view for every later Indeed decision — $0.10 budget, two LinkedIn starts
hold $0.092, Indeed #1 is admitted, its $0.02 moves the view to $0.112 and
Indeed #2 is refused, where the old guard saw $0.08 and let it run
(`test_linkedin_ceilings_stay_visible_to_indeed`). A profile that orders Indeed
first has its observed spend count against LinkedIn's first ceiling
(`test_a_bounded_site_after_an_unbounded_one_counts_its_spend`).

**What C2 can and cannot guarantee for an unbounded provider**: it cannot bound
one. An admitted Indeed run may cost any amount, and its charge enters the view
only after its account read, lagging — the pre-C2 overshoot, unchanged. C2
guarantees only that the view an unbounded start is judged on is never lower
than the old guard's and always includes every committed ceiling.

## 10. Scheduler design

`paid_phase_c2()` (`scraper.py`), called from `main()` in place of the serial
loop when the flag is on. Per site, the coordinator repeats one step:

```
1. the plan's head has a result waiting?          → integrate it; next head
2. a slot is free and the next search may be sent?
      done today                                   → queued as a skip (integrated in order)
      a repeat of a search still in flight         → wait for its twin (serial skip semantics)
      reserve (bounded) / admit (unbounded) fails  → refused: stop sending
      otherwise                                    → start a daemon worker thread
3. everything sent is integrated?                 → the site is done (or the phase stops)
4. otherwise                                       → wait for any worker's result
```

Width is `SWEEP_PAID_WORKERS` for a bounded site and 1 for an unbounded one.
Integrating before sending is what makes one worker the serial loop exactly
(§19).

**Deterministic merge order.** Results wait in a map keyed by plan position and
only the head is ever integrated, so completion order decides *when* a search is
integrated, never *where*. Rows reach `raw_rows` — and so `finalize`'s stable
sort and `dedupe` — in plan order; the done ledger, the log lines and the
telemetry units follow the same order.

| Worker thread (one search) | Coordinator (the calling thread) |
|---|---|
| its own client on the chosen token | account choice (once), reservation, refusal, stop |
| `scrape_search`: start (commit just before), poll, abort, dataset, normalize | account read, the `spent` figure, the exposure view |
| its own deferred telemetry unit | provenance stamp, `raw_rows`, checkpoint (`emit`), done marker, log lines |
| an isolated result through a queue | C1 statuses, unit attachment in plan order, the C2 section |
| never: raw rows, files, ledger, `LAST_STATS`, shared telemetry | finalize, `LAST_STATS`, readiness path unchanged |

Worker threads are daemons: an interrupt (the console stops a sweep with SIGINT)
still ends the process at once, as serially; runs already started continue at the
provider inside their ceilings. The worker stops a sweep with SIGTERM, which ends
the process regardless of threads.

## 11. Thread safety

- **Telemetry.** A worker opens its unit with `defer=True` (thread-local, the
  B2/B4 mechanism); `scrape_search`'s `paid_run` and `cost_observation` calls land
  on it. The coordinator then `telemetry.resumed(record, opened)`: inside, the
  serial loop's own post-run calls (`observed`, `paid_run`, `cost_observation`,
  `failed`) land on the record; on exit its clock is closed over the whole unit.
  It is attached in plan order. No worker writes the shared run record — pinned
  from inside every worker (`test_workers_never_attach_to_the_shared_record`) and
  per-unit fields land on the right unit under interleaving
  (`test_each_unit_records_its_own_run_under_interleaving`).
- **C1's shared state** — `_paid_ix`, `_paid_pos`, stages, provenance — is touched
  by the coordinator only (inside `emit` and the status calls).
- **`PaidExposure`**: one lock around every read-decide-write. The only
  cross-thread writes are a worker's `commit` and `release`. Eight threads racing
  for the last $0.046 of headroom: exactly one wins, 20 times out of 20
  (`test_4…`).
- **Rows** are returned by value; only the coordinator extends `raw_rows`
  (mutation G).

## 12. Polling — unchanged

5 s interval, 360 s deadline, abort, SDK retries internal. Each search makes the
same requests as before — every benchmark arm makes identical start/poll/dataset/
account-read counts (§23). Concurrency overlaps whole searches; the per-search
"finish → seen" delay (C1 §9, 3.3–5.6 s) is unchanged and now overlaps other
searches' waits.

## 13. Failure behaviour

| Case | C2 behaviour | Pinned by |
|---|---|---|
| provider `FAILED` with a run id | failed; committed; no account read (as before) | `test_8…` |
| provider `TIMED-OUT` | failed; committed | `test_11b…` |
| engine deadline → abort | abort sent; failed; committed | `test_11…` |
| start raised, no run id | failed; committed | `test_9…`, mutation S |
| fail-closed SDK check, before any request | failed; released | `test_10…` |
| dataset read fails after success | failed (the per-search `except`); committed | by construction (same body) |
| account read fails | `account_usage_usd` → None → the run-record fallback, as before | C1's tests on this body |
| skipped as done | no reservation, no request | `test_12…`, mutation O |
| refused by the budget | nothing held, no request; the phase stops | `test_13…` |
| a repeat of an in-flight search | waits; skipped if its twin succeeded, rerun if it failed | `test_a_repeated_search…`, mutation Q |

## 14. What the per-search checkpoint is for — VERIFIED

| Consumer | Reads | Needs |
|---|---|---|
| live feed (Render `live_feed`, worker `/rows`) | the newest `jobs_*.csv`, at any moment | whole files (B5) that only grow during a sweep |
| progress grid (worker status `done`, console `read_done`) | `.done_combos` lines | one line per finished search |
| resume (CLI/console rerun the same day) | `.done_combos` | never skip a search whose rows are not on disk |
| a stop from the UI (worker SIGTERM, console SIGINT) | the last checkpoint | "an interrupted sweep leaves a correct file behind" |
| B5 readiness | the final emit + `record_seen` | unchanged |

The worker never resumes (a directory per run); resume is a console/CLI
property. **The invariant**: a search in `.done_combos` has its surviving rows in
a whole checkpoint written *before* its marker.

## 15. The transaction, and crash semantics

Per search, unchanged: **rows integrated → `emit` (finalize + atomic write) →
done marker**. C2 changes when a search reaches that transaction (plan order,
after its predecessors), not the transaction.

| # | Crash (a `BaseException` no `except Exception` absorbs) | Result, pinned at 1 and 4 workers |
|---|---|---|
| 1 | before a search is integrated | not done; not in the checkpoint; re-bought on resume |
| 2 | rows in memory, checkpoint not begun | the same |
| 3 | during the checkpoint write | the previous checkpoint whole; no temp; not done |
| 4 | after the checkpoint, before the marker | rows on disk; not done → re-bought on resume (serial semantics) |
| 5 | after the marker | done; rows on disk |
| 6–7 | a later search finished and waiting while an earlier one runs | not checkpointed, not marked: lost *and never claimed* — a rerun buys it again |
| 8–9 | resume from the artifacts | done searches skipped, the rest bought; nothing done is rerun |
| 10 | every boundary × every search × 1 and 4 workers | no done search without its rows on disk, no temp, whole JSON |
| 12 | readiness | published after `record_seen`; no write and no provider request after it |

**New under concurrency**: at a crash or stop, up to *workers − 1* in-flight
searches plus any finished-and-waiting ones are lost and re-bought on a rerun;
serially, one. Their rows are never claimed done. **Pre-existing, not C2's**: a
rerun in the same minute overwrites the crashed run's checkpoint (the stamp is
minute-granular), so a done search's rows can vanish from disk that way; serial
too. Recorded as a risk (§26).

## 16. The checkpoint's CPU: score once

**Evidence** (MEASURED, cProfile, C1's synthetic rows): one finalize over 1,350
rows is 4.9 s of CPU, and `score_job` is 99.7% of it; filters, sort, dedupe and
`to_output` together are under 20 ms. A paid sweep re-finalizes every row after
every search, so a 90-search sweep scores 61,425 rows for 1,350 acquired.

**Idempotence** (VERIFIED by reading, pinned by test): `score_job` reads only a
row's acquired fields and rewrites every field it sets from them; the one it
reads back, `timezones`, it rewrites to the same value; a dropped row is dropped
before any write. So a second call on a row changes nothing
(`test_score_job_changes_nothing_the_second_time`, over every row shape the
engine scores).

**Design**: `score_and_filter(…, memo=True)` keeps `score_job`'s verdict on the row
object (`SCORED`) and never scores it again. Every pass still runs the whole
chain over every row — filters, sort, dedupe, write — so each checkpoint is the
same file at the same moment; only O(new rows) of scoring is done per pass.
`to_output` never reads the key (pinned).

**Scope**: every finalize pass of a sweep that has a paid plan with the flag on —
the paid checkpoints, the free tail's checkpoint and the final emit — so a
paid + free sweep's free tail also scores its rows once instead of twice. A free
sweep, the flag off, the shadow evaluator (copies, `memo` off) and a sweep with
the experience guard on (it records a verdict per scoring call) all re-score as
before (`test_a_free_sweep_never_enters_it…`, `test_off_while_the_experience_guard…`,
`test_the_shadow_evaluation_is_the_serial_one`).

**Rejected**: coalescing checkpoints or writing them off-thread (the cost is
scoring, not writing, and either changes what is on disk when); a separate
raw-row journal for recovery (nothing reads one; resume uses `.done_combos` and
the checkpoint as they are).

## 17. The switch and the worker count

| Variable | Default | Parsing |
|---|---|---|
| `SWEEP_PAID_CONCURRENCY` | **OFF** | `1/true/yes/on`; read at sweep start |
| `SWEEP_PAID_WORKERS` | **2** | clamped to **1..4**; `0`, negatives → 1; `999` → 4; `banana`, `4.5`, empty → 2 |

- `SWEEP_PAID_CONCURRENCY=0` — the serial loop, untouched (it is the same code,
  re-indented under an `else`; `git diff -w` shows only the branch).
- `=1` with `WORKERS=1` — reservation accounting and score-once, serial execution:
  the first production step.
- `=1` with `WORKERS>1` — concurrent bounded searches.

One flag, not two: the reservation must run wherever concurrency can, and a
serial-with-reservations mode is the one-worker case of the same code. Four is
the cap because it is the most the benchmark measured (§23) and each worker is a
paid run on one account. Neither variable is set in any deployment file (pinned).

## 18. Telemetry additions

A new top-level section **`paid_execution`**, schema `search-v2c2.1`, present
only when the C2 path ran; the record's `schema` stays `search-v2a.1` and no C1
field changed meaning.

```
mode, workers, peak_in_flight, peak_buffered, buffered_wait_ms{total, max},
exposure{budget_usd, committed_usd, pending_usd, pending_peak_usd,
         unbounded_observed_usd, observed_usd, view_usd,
         committed_starts, released_before_network, blocked},
checkpoint{passes, wall_ms, cpu_ms},          cpu: the coordinator thread's
segments[{provider, bounded, workers, searches, sent, wall_ms}],
units[{unit_id, provider, bounded, reservation, reserved_usd, reserved_at,
       committed_at, released_at, execution_ms, buffered_wait_ms, merge_ms,
       checkpoint_cpu_ms, sdk{calls, requests, rate_limit_errors}}]
```

- **Exposure, never cost.** Nothing in the section is called spent or actual
  (pinned); `cost_observations` keep C1's three sources and finality table
  untouched, and a reservation is never one of them.
- `sdk` is apify-client's own per-client counters (a private attribute, read
  defensively): `requests − calls` is the SDK's retries, `rate_limit_errors` its
  429s — the only view of either from outside it. One client per search makes
  them per search.
- `duration_ms` keeps its V2-A meaning, open to close: under C2 the unit opens
  on its worker and closes on the coordinator after the done marker, so it
  includes `buffered_wait_ms`; the remote part is still `start_ms + wait_ms +
  dataset_ms`.
- Privacy: ids, provider names, amounts, states, clocks and SDK counters. No
  token, row text, URL, query or location (pinned with planted markers).

## 19. Parity

| Property | Pinned by |
|---|---|
| flag off: the scheduler, its section and the memo never run | `test_flag_off_never_enters_the_scheduler`, mutation L |
| one worker: same requests in the same order, same bytes, same C1 record but clocks, same done ledger | `SerialParity` |
| 1–4 workers: CSV, JSON, seen and done ledgers byte-identical to serial; same starts, inputs and ceilings; same request counts | `ConcurrentParity` |
| completion order really inverted (4 workers: run_4, run_3, run_2, run_1) | `test_completion_order_really_runs_against_plan_order` |
| the twin planned first survives, whoever finished first | `test_the_first_planned_twin_wins…`, `DuplicateTie` |
| log lines, done lines, telemetry units, traces in plan order | `test_log_ledger_and_units_follow_plan_order` |
| mixed LinkedIn + Indeed + free plan byte-identical at 2 and 4 | `test_a_funded_mixed_plan_is_byte_identical` |

**The duplicate tie.** Two searches return one posting — same employer, title and
text, so the same `job_key` and an equal score — differing only by LinkedIn job
id. The first-planned search takes 0.24 s, the second answers at once and is
integrated second anyway; the survivor's apply URL is the first search's at 2, 3
and 4 workers, exactly as serial. Integrating in completion order (mutation F) or
letting workers append rows (mutation G) hands it to the other.

## 20. The product consequence: the public spend cap

`sweep/app.py` writes `max_spend_usd = spend_cap_for(estimate) = max($0.50,
1.25 × estimate)` into the profile. LinkedIn is estimated at $0.027 a search
(`SITE_RATES` $0.045 at depth 25, scaled to 15), so the cap allows $0.03375 a
search; C1 measured $0.03005 settled, so the old guard never reached it.

| LinkedIn searches | Estimate | Cap | Starts C2 allows | Starts before C2 |
|---:|---:|---:|---:|---:|
| 10 | $0.270 | $0.50 | 10 | 10 |
| 11 | $0.297 | $0.50 | 10 | 11 |
| 14 | $0.378 | $0.50 | 10 | 14 |
| 18 | $0.486 | $0.61 | 13 | 18 |
| 30 | $0.810 | $1.01 | 21 | 30 |

The default mixed plan (18 LinkedIn + 72 Indeed, cap $8.71) is unaffected: its
$0.828 of LinkedIn ceilings is small beside the Indeed estimate. **VERIFIED
arithmetic; not changed** — the fix is in pricing (a cap that holds ceilings for
bounded sites), which is out of scope. Until then, C2 belongs on developer
sweeps with an explicit budget, not on the public worker (§28).

## 21. Tests

`sweep/tests/test_paid_concurrency.py` — **81 tests**, ~28 s, sockets denied.
`scraper.main()` in-process against `KeyedClient`, a thread-safe stand-in that
matches a start to its script by actor input (so thread timing cannot change
what a search returns), logs every request with its thread and time, and counts
runs in flight at the provider per actor.

| Group | Pins |
|---|---|
| `FlagAndWorkers` (5) | default off; worker default and clamp; no deployment file sets either; flag off never enters the scheduler; a free sweep never enters it |
| `SerialParity` (5) | §19; the deliberate budget difference |
| `ConcurrentParity` (8) | §19; peak in flight = workers; `workers=999` → 4 at the provider; a repeated search waits for its twin |
| `DuplicateTie` (2) | the twins really tie; the first-planned wins at 2–4 workers |
| `ReservationBudget` (17) | the brief's 13 cases (§3–§4, §13); the reserved ceiling is the one sent; a reservation exists before its worker starts; no budget never blocks |
| `ExposureLedger` (5) | pending → committed, never back; release only before the start; observed only adds; never below the old guard; unbounded admitted on the old test |
| `MixedProviders` (5) | §9 |
| `AccountSelection` (4) | §7 |
| `Checkpoints` (9) | §15 |
| `ScoreOnce` (6) | §16 |
| `Telemetry` (8) | §11, §18 |
| `CanaryProbe` (4) | the probe sets the C2 mode itself; adds no key or limit; the engine cap in the profile; the provider-overlap reader |
| `Isolation` (3) | free executors answer only their own switches; no ledger or developer key in production code; the benchmark cannot reach a real client |

Changed elsewhere: B3's shared test environment clears the two new variables
(so every existing end-to-end suite runs serial whatever the shell holds); C0's
reachability table classifies C1's and C2's offline benchmarks as unreachable,
and its scan now reads untracked files too (§0.8).

## 22. Mutation checks — nineteen, all caught

Each applied alone to `scraper.py` as one exact edit; four suites run
(`test_paid_concurrency`, `test_paid_observability`, `test_paid_contract`,
`test_search_v2_telemetry`); reverted; the file's SHA-256 equal before and after
the set. A–M are the brief's; N–S extra. 548 s for the set, on the final code.

| # | Mutation | Result | Among the failures |
|---|---|---|---|
| A | release the committed ceiling after actor success | **13 fail** | `test_1_budget_010_two_starts_the_third_blocked`, `test_linkedin_ceilings_stay_visible_to_indeed`, `test_13_a_blocked_unit_holds_nothing_and_sends_nothing` |
| B | replace the ceiling with the provisional observed cost | **12 fail** | `test_linkedin_ceilings_stay_visible_to_indeed`, `test_an_unbounded_start_is_admitted_on_the_old_test`, `test_12_a_skipped_done_unit_reserves_nothing` |
| C | use the account delta as spendable headroom | **17 fail** | `test_11_an_engine_deadline_abort_keeps_its_ceiling`, `test_11b_a_provider_timeout_keeps_its_ceiling`, `test_linkedin_ceilings_stay_visible_to_indeed` |
| D | reserve inside the worker, after it was started (the coordinator no longer reserves) | **8 fail** | `test_1_budget_010_two_starts_the_third_blocked`, `test_13_a_blocked_unit_holds_nothing_and_sends_nothing`, `test_a_reservation_exists_before_its_worker_starts` |
| E | release the reservation on timeout/abort (any failure after the start) | **5 fail** | `test_11_an_engine_deadline_abort_keeps_its_ceiling`, `test_11b_a_provider_timeout_keeps_its_ceiling`, `test_8_a_run_that_failed_after_its_start_keeps_its_ceiling` |
| F | integrate completed searches in completion order | **8 fail** | `test_the_first_planned_twin_wins_whoever_finished_first`, `test_a_later_unit_finishing_first_does_not_take_the_tie`, `test_byte_identical_at_every_worker_count` |
| G | worker threads extend the shared `raw_rows` themselves | **5 fail** | `test_a_later_unit_finishing_first_does_not_take_the_tie`, `test_byte_identical_at_every_worker_count`, `test_log_ledger_and_units_follow_plan_order` |
| H | an unbounded provider enters the concurrent pool | **3 fail** | `test_indeed_never_enters_the_pool`, `test_indeed_runs_exactly_as_serial`, `test_linkedin_ceilings_stay_visible_to_indeed` |
| I | mark done before the checkpoint is durable | **26 fail** | `test_10_no_done_unit_ever_lacks_its_rows_at_any_boundary`, `test_2_crash_with_rows_in_memory_before_the_checkpoint`, `test_3_crash_during_the_checkpoint_write` |
| J | the checkpoint optimisation drops the per-search checkpoint | **36 fail** | `test_10_no_done_unit_ever_lacks_its_rows_at_any_boundary`, `test_8_9_resume_skips_what_was_safe_and_buys_only_the_rest`, `test_12_readiness_after_the_final_result_under_c2` |
| K | each worker selects its own account | **2 fail** | `test_two_accounts_the_one_with_headroom_chosen_once_for_every_worker`, `test_one_byok_token_reads_no_headroom_and_is_used_throughout` |
| L | the flag off still enters the scheduler | **14 fail, 1 error** | `test_flag_off_never_enters_the_scheduler`, `test_completion_order_really_runs_against_plan_order`, C1's `test_nothing_is_stamped_with_telemetry_off` |
| M | C2 telemetry changes a user-visible field | **7 fail** | `test_byte_identical_at_every_worker_count`, `test_a_later_unit_finishing_first_does_not_take_the_tie`, `test_a_funded_mixed_plan_is_byte_identical` |
| N | score-once left on while the experience guard records per call | **1 fail** | `test_off_while_the_experience_guard_records_per_call` |
| O | a skipped-done search reserves a ceiling | **1 fail** | `test_12_a_skipped_done_unit_reserves_nothing` |
| P | the worker clamp removed | **2 fail** | `test_an_absurd_worker_count_is_still_four_at_the_provider`, `test_workers_default_two_clamped_one_to_four` |
| Q | a repeated search no longer waits for its twin | **1 fail** | `test_a_repeated_search_waits_for_its_twin_as_the_serial_loop_would` |
| R | the budget boundary off by one (an exactly fitting start refused) | **2 fail** | `test_3_budget_exactly_0092_two_starts`, `test_4_two_workers_racing_for_the_last_reservation_only_one_wins` |
| S | the hold committed after the start request instead of before | **1 fail** | `test_9_a_start_that_raised_keeps_its_ceiling_run_id_or_not` |

D, read carefully: a first form of it removed reservation outright, which made
every commit fail on a missing key — 81 tests, too crude to count. The form
above moves reservation into the started worker (refused → that search fails);
what catches it is the refusal arriving too late and in the wrong place, as the
brief intends. S is the ambiguous-start case in miniature: commit after the
request, and a start that raises is never committed.

## 23. Offline benchmarks

**MEASURED 2026-09-24 11:03 IST**, `bench/search_v2_paid_concurrency.py`
([evidence](search-v2-evidence/c2-paid-concurrency-benchmark.json), revision
`a3b4bd2-dirty`, i.e. this change before it was committed). Each arm a fresh
child process against the thread-safe scripted stand-in: V2-C1's plan and
synthetic rows (9 keywords × 5 places × LinkedIn and Indeed, 15 rows a search),
arms alternating, zero Apify.

**Host control.** A first run was discarded: from its sixth scaled arm on, the
same scoring work cost ~2.8× more CPU per row while other desktop apps took the
performance cores, and the 3- and 4-worker arms came out *slower* than 2 — an
artefact, not a finding. Each arm now (a) asks macOS for user-interactive QoS
(the calibration read 3.6–14.9 ms/row at default QoS, 3.57–3.68 at
user-interactive), (b) times a fixed 300-row scoring workload before and after
its sweep, and (c) is measured again if either reading exceeds 1.25× the best
seen in the whole run. In the run reported here every calibration read
3.53–3.63 ms/row, **no arm was flagged and none was rerun**.

**No provider wait** — local work only (V2-C1 §20's denominator). CPU is also
given in scoring-row equivalents (CPU ÷ the host's ms/row at that moment), a
unit that does not depend on how fast the host was.

| Plan | Arm | Wall | CPU | finalize row-eq. | finalize calls | Peak RSS |
|---|---|---:|---:|---:|---:|---:|
| C1's 90 | serial | 229.7 s | 226.8 s | 63,338 | 91 | 64.7 MiB |
| C1's 90 | C2 1 / 2 / 3 / 4 | 5.99 / 5.93 / 5.94 / 6.02 s | 5.9 s | 1,450–1,457 | 91 | 67.2 MiB |
| 45 LinkedIn | serial | 59.7 s | 58.9 s | 16,334 | 46 | 62.3 MiB |
| 45 LinkedIn | C2 1–4 | 2.91–2.93 s | 2.9 s | 706–707 | 46 | 63.6 MiB |

The serial figure is the arithmetic of re-scoring: Σ 15k for k = 1…90 plus the
final 1,350 is 62,775 rows; measured 63,338 (+1%, the filters). C2's is 1,350
rows scored once plus the same filters. Same number of passes, 38× less work.

**Provider time scaled 1:10** — each search runs one of C1's measured runtimes
(16, 22, 34, 40 s by a seeded draw; mean 27.2 s), polled every 0.5 s, start
0.06 s, dataset 0.09 s. The model column is the pure wait schedule (greedy, plan
order) plus the arm's measured CPU.

| Plan | Arm | Wall | Model | vs serial | vs C2 at 1 | Peak in flight (LinkedIn / Indeed) | Peak buffered | Buffered wait (total / max) |
|---|---|---:|---:|---:|---:|---|---:|---|
| C1's 90 | serial | 509.8 s | 506.0 | 1.00× | — | 1 / 1 | — | — |
| | C2 1 | 289.3 s | 285.6 | 1.76× | 1.00× | 1 / 1 | 0 | 0 s |
| | C2 2 | 221.1 s | 220.3 | 2.31× | 1.31× | 2 / 1 | 1 | 7.7 s / 2.1 s |
| | C2 3 | 197.5 s | 197.5 | 2.58× | 1.46× | 3 / 1 | 2 | 9.5 s / 1.6 s |
| | C2 4 | 187.1 s | 187.6 | 2.72× | 1.55× | 4 / 1 | 3 | 19.4 s / 2.1 s |
| 45 LinkedIn | serial | 194.0 s | 191.8 | 1.00× | — | 1 | — | — |
| | C2 1 | 137.9 s | 136.3 | 1.41× | 1.00× | 1 | 0 | 0 s |
| | C2 2 | 69.8 s | 70.9 | 2.78× | 1.98× | 2 | 1 | 7.5 s / 1.9 s |
| | C2 3 | 46.2 s | 47.9 | 4.20× | 2.98× | 3 | 2 | 9.4 s / 1.6 s |
| | C2 4 | 36.3 s | 38.1 | 5.35× | 3.80× | 4 | 3 | 19.3 s / 2.1 s |

In every arm of a plan: the same 90 (45) starts, the same 528 (251) polls, the
same 91 (46) account reads — concurrency moves requests, never adds them — the
same output bytes and the same done ledger; committed exposure $2.070 (45 ×
$0.046) under C2. Peak RSS rose ~3 MiB at four workers.

**Reading it in real time (INFERRED).** Scaling the waits back up, C1's plan
waits 2,775 s serially; the old path adds its ~227 s of scoring between searches
(3,002 s, ~50 min). C2 at one worker saves the scoring (−7%); two workers
−29%; four −40%. On production's default shape (18 LinkedIn + 72 Indeed) only
a fifth of the waiting can overlap, so two workers save ~280 s and four ~415 s,
while score-once saves its ~220 s on every paid sweep whatever the worker count.
Real descriptions score ~1.6× slower than these synthetic ones (the canary:
88 ms for 15 rows), so real savings from score-once are likely larger (INFERRED);
Oracle's cores are UNKNOWN.

**The brief's three questions.**

1. *Did concurrency hide the checkpoint work?* It no longer needs to: score-once
   removed 96–98% of it. What remains, ~6 s over 90 searches, is 3% of even the
   time-compressed arm and ~0.3% of a real one.
2. *Did C2 reduce the number or cost of checkpoint passes?* Not the number — one
   per completed search plus the final, as serially, so the files on disk are the
   same at every moment. The cost: from O(rows so far) of scoring per pass to
   O(new rows).
3. *Is local CPU now the limiting factor?* No. At 1:10 compression the C2 arms
   track the pure wait schedule to within ~4 s; the limit is the provider's
   runtime, and on mixed plans the serial Indeed half.

**Worker count.** Returns follow the schedule, not diminishing CPU: two workers
already take the mixed plan to 1.31× of its one-worker time, four to 1.55×; on a
LinkedIn-only plan four give 3.80×. What four costs is not local — it is four
paid runs at the provider at once, which no live run has tried (§24 tried two).
Hence two for the first production experiment (§28).

## 24. The live canary — MEASURED

`SWEEP_ALLOW_PAID_BENCH=1 .venv/bin/python -m bench.search_v2_paid_probe --allow-paid
--max-usd 0.28 --exposed-usd 0.184 --case software_fullstack --scope india --site
linkedin --keywords "Backend Developer,Full Stack Developer" --location India
--searches 2 --paid-workers 2 --sweep-budget 0.092 --stage C2 …` at 2026-09-24
03:23:31 UTC. [Evidence](search-v2-evidence/c2-live-concurrency-canary.json).

**Before the call.** Unknowns: does the provider accept two simultaneous
provider-bounded starts on this account; does each carry its ceiling; any
throttling; any accounting or reservation anomaly real concurrency creates.
Offline cannot answer any of them. Deterministic tests green (1,318 sweep — the 81st C2
test came after — and 42 deploy), 19/19 mutations caught. Inputs checked offline from the exact profile:
C1 probe A's two queries (for runtime comparability), `limitPerSource` = `count`
= 15, `scrapeCompany` false, India geoId, ceiling $0.046 each. Ledger before:
$0.184 intended / $0.1202 actual. Intended: **$0.092** (2 × $0.046) → cumulative
**$0.276** ≤ $2.00. Two limits: the guard's `--max-usd 0.28` refuses a third
start ($0.322), and the engine's own cap `0.092` — exactly two ceilings — makes
C2 refuse one too. Zero-cost preview first: the preflight printed and the child
stopped with neither key.

| | `paid_000` Backend Developer @ India | `paid_001` Full Stack Developer @ India |
|---|---|---|
| run | `bCfByvJrRfnpCwvx6`, build 1.7.17, SUCCEEDED | `FAejWhRqDC4jfqx0l`, build 1.7.17, SUCCEEDED |
| provider start → finish | 03:23:37.508 → 03:23:52.362 | 03:23:37.507 → 03:23:48.572 |
| actor runtime | 14.64 s (C1, same query, serial: 22.32 s) | 10.87 s (C1: 34.23 s) |
| `maxTotalChargeUsd` recorded by the provider | 0.046 | 0.046 |
| rows | 15/15 | 15/15 |
| reservation | reserved .590, committed .596 | reserved .592, committed .597 |
| unit: start / wait (3 polls) / dataset | 1.22 / 16.15 / 0.96 s | 1.20 / 16.15 / 0.97 s |
| waited for the earlier search | 0 | 0.50 s (its account read + checkpoint) |
| SDK requests / 429s | 6 / 0 | 6 / 0 |
| run record at completion | **$0.00005** (0 of 15 results counted) | **$0.00005** (4 of 15) |
| account delta at the engine's read | $0.0001 | $0.0000 |
| settled (run record unchanged ×6) | $0.03005 at +19.5 s | $0.03005 at +23.6 s |
| funnel | 15 raw / 13 eligible / 13 final | 15 / 14 / 13 (one lost to `paid_000`) |

- **Overlap 11.06 s; peak concurrent at the provider 2** (from the provider's own
  clocks). The paid segment took **19.23 s** for two searches whose unit
  executions summed 36.65 s.
- **The account** covered both runs' $0.0601 at +20.1 s after the last finish and
  carried +$0.000036 beyond it at +486 s (C1's residual again).
- **No throttling**: 0 rate-limit errors; 6 requests per search is exactly start
  + 3 polls + 2 dataset pages (the SDK pages until an empty page), so 0 SDK
  retries (INFERRED from that arithmetic; the per-search `calls` counter was added
  after this run, making it direct from now on).
- **Reservation**: both committed, `pending_peak` $0.092 = `committed` = the cap;
  none released; none blocked. The view never moved off $0.092 though the
  guard's own reading said $0.0001.
- **Run listing**: exactly these two runs on `APIFY_TOKEN`, none on
  `APIFY_TOKEN_2` — no unexpected provider activity. Token absent from the
  evidence (checked by value).
- **Checkpoint**: 2 passes, 173 ms CPU; the second pass over 30 rows cost what the
  first did over 15 — score-once, live.

Stopped there: the question is answered, and one failure-free pair is what the
brief asked for. Actual **$0.0601**.

## 25. The research ledger

`docs/search-v2-evidence/paid-research-ledger.json`, appended by the probe:

| Stage | Starts | Intended | Actual | Cumulative intended | Cumulative actual |
|---|---:|---:|---:|---:|---:|
| C0 | 1 | $0.046 | $0.03005 | $0.046 | $0.03005 |
| C1 probe A | 2 | $0.092 | $0.0601 | $0.138 | $0.09015 |
| C1 probe B | 1 | $0.046 | $0.03005 | $0.184 | $0.1202 |
| **C2 canary** | **2** | **$0.092** | **$0.0601** | **$0.276** | **$0.1803** |

Evidence only: never authorisation, refuse-only for developer checks, read by no
production code (pinned). 13.8% of the shared $2.00 C1–C4 ceiling intended.

## 26. Unresolved risks

1. **The public spend cap truncates LinkedIn-heavy sweeps under C2** (§20). Not a
   C2 defect — the cap under-prices a start's worst case — but it must be fixed
   before the flag is on for public paid sweeps.
2. **Two concurrent runs are not a rate-limit study.** One account, one pair, one
   evening. Four at once, or two from Oracle's IP, is UNKNOWN; the per-search
   `sdk` counters are the signal to watch.
3. **Unused ceiling is not reusable within a sweep** (~35% of each LinkedIn
   start). Deliberate; revisiting it needs a provider finality contract.
4. **Indeed and Naukri stay unbounded.** C2 cannot bound them and did not try.
5. **External spend is invisible until the account shows it** (§8).
6. **More is lost at a crash or stop**: up to workers − 1 in-flight plus waiting
   searches, re-bought on a rerun (never claimed done). A slow head search holds
   finished ones in memory; `peak_buffered` measures it.
7. **Same-minute rerun overwrites a crashed run's checkpoint** — pre-existing,
   serial too (§15).
8. **Score-once rests on `score_job` staying idempotent.** A future cross-row
   scoring rule (e.g. "penalise employers with many postings") would make the memo
   stale; `test_score_job_changes_nothing_the_second_time` and the memo-vs-plain
   pass test are there to fail first.
9. **`_statistics` is apify-client's private attribute.** A rename makes `sdk`
   absent, never wrong.
10. **Indeed's runtime is UNKNOWN**; the benchmark assumes LinkedIn's
    distribution for it.

## 27. Readiness for V2-C3

C3 (plan compaction) can start from what C2 now records per unit —
`paid_execution.units` beside C1's funnel, trace and acquisition classes — and
from the reservation view it must respect: fewer, larger searches change how
many ceilings a budget holds, which is §20's arithmetic. Nothing in C2 decides
*what* runs: the plan, its order and depth are the serial loop's.

## 28. Proposed C5 settings — NOT executed

C5 is one integrated live paid sweep for C1–C4 together. For the C2 part:

```
SWEEP_SEARCH_V2_TELEMETRY=1      # unchanged: how it is measured
SWEEP_PAID_CONCURRENCY=1
SWEEP_PAID_WORKERS=2             # the canary's setting; 4 only after 2 is clean
max_spend_usd                    # explicit, >= Σ bounded ceilings + unbounded estimate x 1.25
```

run as a developer sweep through the guard with both keys and a stated budget —
**not** on the public worker until §20's cap is fixed. Watch: `paid_execution`
exposure (committed never above the budget, never falling), `peak_in_flight` ≤
workers, per-search `sdk` retries and 429s (baseline 0), `buffered_wait_ms`,
checkpoint CPU, the segment walls against the serial sum, output parity where a
serial arm exists. Stop on any 429 pattern, any committed exposure above the
budget, or any search integrated out of plan order.

## 29. Rollback

`SWEEP_PAID_CONCURRENCY` unset (or `0`): the next sweep takes the serial loop,
which is the pre-C2 code. On the worker, like every flag, it lives in
`/etc/sweep-worker/env` and needs `systemctl restart sweep-worker` while idle — it
is not set there, and nothing sets it.

## 30. Verification

- **Baseline first**: `a3b4bd2` itself runs 1,238 sweep tests with **1 failure**,
  C0's reachability scan meeting C1's unclassified overhead benchmark (§0.8).
- `python -m unittest discover -s sweep/tests -t .` — **1,319 tests, OK** (1,238 +
  81), 209 s. The C2 module alone passed five runs in a row.
- `python -m unittest deploy.test_sweep_worker deploy.test_modal_benchmark` —
  **42 tests, OK**.
- `python scraper.py --demo`, `python telemetry.py` (its flag-off inertness check
  now includes `paid_execution` and `resumed`), `python -m sources`,
  `python -m sources.concurrency` — pass.
- `auto-apply` suite — 1,102 tests, 2 errors in `test_inference` healthz (a local
  inference service answering 503); **the same two errors reproduce on a clean
  worktree of `a3b4bd2`**, as for B3, B5, C0 and C1 — environmental.
- Mutation checks — 19/19 caught on the final code; `scraper.py` SHA-256 equal
  before and after the set.
- Offline benchmark — §23: identical output and done ledger in every arm of every
  group; no arm measured on a slowed host.
- `git diff a3b4bd2 -- config.py deploy/ sweep/app.py sweep/runs.py sweep/plan.py
  sweep/worker_link.py sweep/public.py sweep/logic.py sources/ rescore_from_apify.py
  auto-apply/ requirements.txt render.yaml gunicorn.conf.py bench/paid_guard.py
  bench/search_v2_paid_overhead.py` — **empty**. Changed production code:
  `scraper.py` (+452 / −10 ignoring whitespace; the serial loop is re-indented
  under the flag's `else`, byte-for-byte otherwise) and `telemetry.py` (+49 / −1).
  No flag, environment or deployment change.
- **Paid**: two LinkedIn starts in one canary, §24 — preceded by the offline
  input check and a zero-cost preview; both keys on the command line; tight
  `--max-usd` and engine cap; the run listing shows no other activity. The
  temporary probe profile was removed (none left in `profiles/`); outputs lived
  in a temp directory and were deleted.
