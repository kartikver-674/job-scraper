# V2-B5 — the user's result as soon as it is final

Date: 2026-09-23. Baseline: `5b167c6` (V2-B4). Production runs V2-B4 with
`SWEEP_SEARCH_V2_TELEMETRY=1`, `SWEEP_FREE_LEVER_CONCURRENCY=1`/`WORKERS=4`,
`SWEEP_FREE_GREENHOUSE_CONCURRENCY=1`/`WORKERS=4` and `SWEEP_FREE_SOURCE_SHADOW=1`.
Scope: **when the user is shown a result, not how it is made.** Behind
`SWEEP_RESULTS_READY_EARLY`, default **OFF**. Not deployed, not enabled, no
deployment or environment file changed.

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a dated
probe), **VERIFIED** (directly observed code or configuration), **INFERRED** (a
model with stated assumptions), **UNKNOWN** (evidence unavailable, which is not
the same as zero).

Nothing else moved. The engine computes exactly what it did: the registry (134
active records, Postman included), adapters, the Lever and Greenhouse executors
and worker counts, B3's eight boards, its evaluator, timeout, stop rules and
telemetry sections, scoring, ranking, dedupe, `job_key`, Search Preferences,
recency, location, arrangement and salary rules, and every paid path.
`deploy/sweep_worker.py`'s `Queue` — the slot, the FIFO, recovery, stop and the
janitor — is not edited at all. No new endpoint, scheduler, worker, queue,
cache, Redis or database. **No Apify call was made.**
`SWEEP_EXPERIENCE_MISMATCH_GUARD` remains off.

## 0. Headline findings

1. **VERIFIED — Render had no notion of "results available".** It decides a
   sweep is over when the worker's execution `state` turns terminal
   (`RemoteRun.poll()`), and the worker writes that state only when the child
   process exits (`Queue._wait`). The rows themselves were never gated:
   `/results` and every export read `/v1/runs/<id>/rows`, which serves the
   newest CSV at any moment. So every second the engine spends after its result
   files are final is, by construction, a second on the user's screen.
2. **VERIFIED — the result is final after `record_seen()`.** Nothing after it
   writes the CSV, the JSON or the seen ledger (§3). B3 had already put its
   shadow stage there; B5 publishes readiness at exactly that line.
3. **MEASURED — with the flag off, the lifecycle is what it was**: the status
   body carries exactly its pre-B5 keys, outputs are byte-identical, and the
   running page's own script, executed against the real `/progress` answer,
   keeps polling until the process exits (§9, §19).
4. **MEASURED — with the flag on, Render reported the sweep finished 28–32 ms
   after the marker and 12.01 s before the process exited**, for an injected
   12 s post-result tail (4.01 s for a 4 s tail), offline, through the real
   worker, Render app and engine; with it off, only after the exit, in every
   run (§21). The slot and the visitor queued behind still waited for the exit.
5. **VERIFIED, then fixed — outputs were written in place** (truncate, then
   write), so a reader at the wrong moment got half a file and a crash left half
   a file for good. Each is now written beside its target and renamed over it.
   This is the one change that is live with the flag off; the bytes are
   identical (§6).
6. **MEASURED — 18 targeted mutations, all caught** (§20).
7. **Recommendation: worth a production experiment.** A failure after readiness
   cannot take the result away, a failure before it cannot expose one, and the
   gain is the post-result tail — B3's shadow stage, observed at ~4–12 s, with
   the occasional 26–30 s stall (§21, §25).

## 1. The lifecycle as it was — VERIFIED

```
browser ─(every 4 s)─► Render GET /progress ─► snapshot() ─► _liveness()
                                                   └─ proc.poll() = RemoteRun.poll()
                                                        └─ worker GET /v1/runs/<id> → state

Render POST /run ─► worker POST /v1/runs ─► status.json {state: queued} ─► Queue.submit
  Queue._pump   len(_children) < MAX_ACTIVE (1) ─► _start: spawn scraper.py,
                _children[id] = child, state = running, a thread in _wait
  Queue._wait   child.wait() ─► pop _children ─► state = done | failed,
                finished_at, exit_code ─► _pump: the next run in line starts

engine  paid searches (checkpoint emit after each) ─► free phase ─► checkpoint emit
        ─► final emit ─► print_summary ─► record_seen ─► shadow.run (B3)
        ─► telemetry.identity ─► telemetry.finish ─► exit
```

- **Navigation.** In public mode `running.html` polls `/progress` every 4 s and,
  when `p.finished && waiting`, moves to `/results` 1.2 s later. `p.finished`
  is `(not running_now) and outstanding == 0`, and `running_now` was
  `proc.poll() is None` — the process.
- **Data.** `/results` and `/export.*` call `read_rows` → `worker_client.all_rows`
  → `/v1/runs/<id>/rows` → `rows_since()`, the newest `**/*.csv` by mtime. There
  is no state check anywhere on that path; the existing `TestResultsAndExports`
  already reads rows while the fake child is still running.
- **The strip** on other screens reads the cheap worker status
  (`owned_run_banner`) and says "Sweep in progress" while `state == running`.
- **DONE means more than done.** In `_wait` it is the moment the slot is
  released and the next run spawned; it makes the run eligible for the janitor;
  `recover()` turns a `running` run into `interrupted`; and on Render a terminal
  state makes `_sweep_in_flight()` false, which re-enables Start. Marking DONE
  early would have moved every one of those. B5 moves none.

**How did Render know results were available?** It did not. It knew when the
process had ended, and treated that as the same thing.

## 2. Architecture

The engine and the worker already share exactly one channel: files in the run's
own output directory. `.done_combos` is how the paid progress grid fills in
(`done_combos()`, merged into the status body by `public()`); the child's stdout
is not parsed, and for a paid run it goes to `/dev/null`. B5 sends one more fact
down that channel.

| File | Change |
|---|---|
| `scraper.py` | `write_outputs` renames each file into place (`_replaced`). After `record_seen`: `telemetry.mark("results_ready")`, and under the flag `publish_results_ready()` renames `<output_dir>/.results_ready` = `{"at": <epoch>}` into place. A previous run's marker is removed when the output paths are fixed. |
| `deploy/sweep_worker.py` | `results_ready(output_dir)` returns `{"results_ready": true, "results_ready_at": t}` or `{}`; `public()` merges it into the status body. **`Queue` not edited.** |
| `sweep/worker_link.py` | `read_ready()`: the cached status's `results_ready is True`; false on absence or any worker error. |
| `sweep/app.py` | `create_app(read_ready=…)`, local default always false. `_liveness`: `running_now = alive and not read_ready()`. `owned_run_banner`: `running` + ready reads as `done`. |
| `telemetry.py` | `post_result_ms = duration_ms − milestones.results_ready` when the milestone exists. |
| templates, JavaScript | **none** |

Rejected: marking DONE at readiness (§1, last bullet); a status-watching thread
in the worker (a second writer to `status.json`, for a fact the status body can
read on demand the way it reads `.done_combos`); a new endpoint (the existing
status poll already carries engine facts); parsing stdout (not kept for paid
runs, and a format nobody promised).

## 3. The readiness boundary — VERIFIED, pinned

```python
out_rows = emit(raw_rows)            # finalize + write_outputs: both files renamed into place
print_summary(pulled, len(out_rows), out_rows)
added = record_seen(out_rows, seen, today)   # the ledger, appended and closed
print(...)                           # "Wrote N ranked jobs to: ..."
telemetry.mark("results_ready")      # ← the boundary
if results_ready_early():
    publish_results_ready()          # ← the marker, renamed into place
if run_free and shadow.enabled():    # B3: counts into the telemetry record only
    shadow.run(...)
telemetry.identity(raw_rows, job_key)
telemetry.finish()                   # telemetry/sweep_<id>.json only
```

Nothing after the marker writes a result file: `shadow.run()` returns `None`
and records counts only (B3 §4); `identity` and `finish` write only
`telemetry/sweep_<id>.json`, which is JSON under a subdirectory and matches no
reader of results.

**The seen ledger is inside the boundary.** On the worker nothing a user sees
reads it — each run has its own directory, so no later run loads it — and the
result could be served without it. It is inside anyway: it is the last step of
the engine's own "the report exists" transaction ("recorded only once the run
has actually produced its report"), it costs microseconds, and one boundary for
every consumer is simpler than two. A crash while it is written leaves no marker
(`test_the_ledger_is_inside_the_boundary`).

Pinned by `test_published_after_the_last_write_and_the_ledger_before_any_shadow_request`
(the exact call order, and every shadow request after the marker),
`test_every_output_is_final_when_it_is_published` (at the moment of publishing,
the CSV, JSON and ledger on disk equal the final bytes and no temp file
exists), and mutations A1 and L.

No marker is ever written when nothing was scraped (the engine's own
`sys.exit`), on any crash before that line, on `--dry-run`, or with the flag off.

## 4. Execution state vs result availability

| | `state` | `results_ready` |
|---|---|---|
| Describes | the **process** | the **result** |
| Values | `queued running done failed stopped interrupted` — unchanged | absent, or `true` with `results_ready_at` |
| Written by | the worker's `Queue`, as before | the engine, once, by rename |
| Changes later | on exit, stop, restart | never: once published nothing removes it; the janitor deletes it with the run |
| Read by | `Queue`, janitor, `recover()`, `RemoteRun.poll()`, `_sweep_in_flight()` | `_liveness()`, `owned_run_banner()` — nothing else |

The screen reads **finished** when the process has ended *or* the result is
ready. Everything that decides whether a process may start, be stopped or be
deleted still reads the process.

## 5. Queue and active slot

`Queue` is byte-for-byte what it was, and `results_ready()` touches none of
`_children`, `_waiting`, `_pump` or the status writer. With the result ready and
the process alive (`test_ready_releases_no_slot_and_starts_nothing`, race C):

- `_children` still holds the run, `healthz` still reports `running: 1`, and
  with a second visitor waiting `queue.active()` is `(1, 1)`;
- the second run stays `queued` at position 1 and its child is not spawned
  until the first has exited — asserted on timestamps: the first child's exit
  precedes the second's `started_at`;
- the first run's `finished_at` and `exit_code` stay `null` until its exit;
- on Render `_sweep_in_flight()` is still true, so a second Start from the same
  browser creates no second run (`test_render_still_counts_the_process_as_in_flight`).

Mutations B1 (report DONE at readiness) and B2 (release the slot at readiness)
are caught.

## 6. Output atomicity

**VERIFIED before:** `write_outputs` opened `jobs_<stamp>.csv` and `.json` with
mode `"w"` — truncate, then write. The paid path rewrites both after every
search and the Free tail writes them twice, so a `/rows` read could land on an
empty or half-written CSV — `live_feed()`'s own comment, "a reading can land
mid-write and come back short", describes the window. A crash inside it left the
half file, and that was what `/results` then served.

**Now** each file is written to `<name>.<pid>.tmp` in the same directory,
flushed, `fsync`ed (the convention `RunStore.write` already follows) and
`os.replace`d over the target; on any exception the temp file is removed. A
reader gets the previous complete file or the new complete file.

- **The pair.** CSV and JSON are renamed one after the other, so for a moment
  they disagree. No user is served the engine's JSON (Render's JSON export is
  built from the CSV rows), and readiness is published after both renames — that
  is the pair-level guarantee.
- **Temp names match no reader.** Every consumer glob ends in the real
  extension — `**/*.csv`, `jobs_2*.csv`, `jobs_combined*.csv`, `jobs_*.json` — and
  `….csv.<pid>.tmp` matches none of them. A temp left by a SIGKILL is never
  served and goes with its run directory.
- **Bytes identical** to the previous writer, which the test keeps verbatim
  (`test_the_bytes_are_what_the_plain_writer_wrote`), and in every end-to-end
  comparison.
- **Permissions unchanged**: the temp is made by `open()`, not `mkstemp` (0600).
- **The marker** goes through the same helper, so a poll sees no marker or a
  whole one (race E, `test_the_marker_is_whole_or_absent`); anything that is not
  `{"at": <number>}` reads as not ready.

**Live with the flag off, deliberately.** It closes a torn-read window that
exists today on every path and changes no byte; gating it would keep a known
defect alive behind a flag about something else. Its cost is one `fsync` per
file per write — once per paid search, twice at the Free tail. Not measured on
Oracle (**UNKNOWN**); milliseconds beside a 30–60 s paid search or a ~14 s
finalize (**INFERRED**).

## 7. Worker protocol

`GET /v1/runs/<id>`, and the body `POST /v1/runs/<id>/stop` returns, gain two
keys **only once the engine has published its marker**:

```json
{"state": "running", "results_ready": true, "results_ready_at": 1790150400.123, "…": "…"}
```

Without a marker the body is exactly its pre-B5 key set (`PRE_B5_KEYS`,
pinned). `results_ready_at` is the engine's wall clock on the same host as the
worker's `finished_at`, so `finished_at − results_ready_at` is the hidden wait,
interpreter exit included. No other endpoint changed: `/rows` was already
ungated, and `/healthz`, `POST /v1/runs`, `/v1/tokens` and `/v1/plans` are
untouched.

## 8. Render and the frontend

- **`read_ready()`** reads `results_ready is True` from the same per-request
  cached status that `poll()` and `read_queue()` already use — no extra call —
  and answers false on absence, on `RunNotFound`, and when the worker does not
  answer. Only a literal `true` counts (`test_only_a_true_moves_the_screen`).
- **`_liveness()`** — `running_now = alive and not read_ready()`. From there
  `finished`, `interrupted`, `free_running`, `state`, `remaining_text` and the
  banner are computed exactly as they will be once the process exits: "Your
  jobs are ready", "Sweep complete — View jobs".
- **`owned_run_banner()`** — pages drawn from the cheap status read `running` +
  ready as `done`, so a strip above another screen cannot say "Sweep in
  progress" beside a finished result.
- **No template or JavaScript change.** `running.html` already moves to
  `/results` when `p.finished && waiting` and stops polling on `finished`; the
  strip's script stops on phase `finished`. `test_the_page_script_moves_to_results_at_readiness`
  runs the page's own `x-init`, exactly as served, in node, against the real
  `/progress` body: it goes to `/results` at readiness; without the field
  (`test_without_the_field_the_page_keeps_polling_until_done`) it keeps polling
  until DONE. Both skip where node is absent.
- The Stop button is drawn only while `p.state === 'running'`, so it goes away
  at readiness: the tail cannot be stopped from the page by accident.
- **Wording unchanged**: the strings of any completed sweep. No stage, flag or
  provider name — shadow, telemetry, diagnostics, Greenhouse, Lever — reaches
  what the user reads (`test_nothing_the_user_reads_names_an_internal_stage`).

## 9. Backward compatibility

| Render | Worker + engine | Flag | Behaviour |
|---|---|---|---|
| old | old | — | today |
| old | new | off | today: no marker, no field |
| old | new | **on** | today: old Render reads `state` only, and `RemoteRun.poll()` is unchanged (`test_an_older_render_ignores_the_field_and_waits_for_done`) |
| new | old | — | today: no field, `read_ready()` false (`test_a_newer_render_on_an_older_worker_waits_for_done`) |
| new | new | off | today (`FlagOff`) |
| new | new | **on** | B5 |

Every mixed state waits for DONE, so no synchronised deploy is needed. The
order is in §23.

## 10. Result endpoint and exports

At readiness, with the process still running, `/v1/runs/<id>/rows`, `/results`,
`/export.csv` and `/export.json` all answer 200. The rows and both exports are
**byte-identical to the same requests after DONE**, the rows equal the final CSV
on disk, and `/results` shows every shortlisted row
(`test_rows_results_and_exports_at_readiness_equal_those_at_done`). No check was
added to any of them — there was none to remove.

## 11. Failure before readiness

No marker, no field: the screen waits for the process, as today.

| Before readiness | `state` | `results_ready` | `/rows` |
|---|---|---|---|
| hard crash before any output | `failed` | absent | empty |
| hard crash halfway through the final write | `failed` | absent | the **checkpoint**, whole — identical to what an uncrashed sweep ends with; no temp left |
| hard crash while the ledger is written | `failed` | absent | complete files |
| nothing scraped | `failed` | absent | empty |

"Hard crash" is a `BaseException` that no `except Exception` absorbs — what an
OOM kill looks like from inside. What happens after the exit is unchanged.

**Pre-existing, not changed:** a Free sweep has no planned searches, so
`outstanding` is always 0 and `_liveness` has always shown *any* ended process —
a failed one included — as "finished", while the strip, reading `state`, says
"Sweep stopped early". B5 neither causes nor fixes that; with atomic writes,
what such a screen shows is at least never half a file.

## 12. Failure after readiness

The result stays; the process's own ending is recorded apart, in `state`,
`exit_code` and `error`, exactly as it always was.

| After readiness | `state` | `results_ready` | The user |
|---|---|---|---|
| a shadow board fails, or `shadow.run` raises | `done` | true | results and exports |
| hard crash during the shadow fetch | `failed`, exit 1 | true | results and exports |
| hard crash immediately after (`telemetry.identity`) | `failed` | true | results and exports |
| hard crash in `telemetry.finish` | `failed` | true | results and exports |
| telemetry record cannot be written | `done` | true | results and exports |
| worker restarts during the tail | `interrupted` | true | results and exports |

No retry exists or was added: the worker never re-runs a sweep, and `recover()`
interrupts a running one rather than restarting it. Nothing removes a run's
marker; mutations E1–E3 try three ways (the engine on a shadow failure, the
worker on a nonzero exit, the worker hiding it once failed) and all are caught.
`failed` with `results_ready: true` means precisely "the result was delivered;
the process then failed", which the experiment counts separately (§23).

## 13. Cleanup and TTL

Unchanged: `cleanup()` skips `queued` and `running` runs and any run with a live
child, and counts the TTL from `finished_at` — the exit. A ready run whose
process is alive survives `cleanup(ttl=0)` and ten TTLs; a run whose readiness
is older than the TTL but whose exit is not is kept
(`test_ttl_runs_from_the_process_exit_not_from_readiness`). Mutations C1 (delete
a ready run) and C2 (TTL from readiness) are caught.

## 14. Authorization

`owned()` runs before `public()`, so a stranger's request never reaches the
readiness read: 404 for status and rows without the owner, 401 without the
bearer, and nothing in either body mentions readiness. The two added values are
a boolean and a number — no path, no string. On Render readiness is read through
the same owner-derived status call; a stranger holding the run id gets
`read_ready() == False`, `RunNotFound`, a silent strip, and redirects from
`/results` and `/export.csv` (`WorkerLevel`, `RenderAuthorization`). Mutation F
(readiness bypasses ownership) is caught.

## 15. Telemetry and timing

| Field | Where | Meaning |
|---|---|---|
| `milestones.results_ready` | telemetry record | ms from sweep start to the boundary; granularity `exact` |
| `post_result_ms` | telemetry record | `duration_ms − milestones.results_ready`: shadow + identity + finish |
| `results_ready_at` | worker status | epoch seconds, from the marker |
| `finished_at` | worker status (existing) | epoch seconds of the exit |

The milestone is recorded **whenever telemetry is on, flag or no flag**, so the
sweeps collected before the flag is turned on measure exactly the tail it would
hide. `free_phase_done` is untouched. The record's `schema` stays
`search-v2a.1` — two fields added, none changed meaning — and B3's
`shadow_units` and `shadow_evaluation` are identical with the flag on and off,
clocks aside (`test_shadow_runs_as_before_with_the_flag_on`). Only timestamps
and durations are new.

## 16. B3

The shadow stage runs where it always did — after the marker — serially,
through the unchanged `sources/shadow.py`, with its timeout, stop rules,
evaluator and schema as they were: all eight units in order, `evaluated`. It
still cannot affect the result; it now starts after the user may already be
reading it. **What changes is who waits for it.** With the flag on, a shadow
stall no longer lands on the user's screen — but it still holds the worker's one
slot, so the visitor queued behind still waits for it. B3's stop rule (stage
over 30 s in more than 2 of 10 sweeps) keeps its purpose, for them.

## 17. B4

No interaction. Readiness names no provider, executor or worker count
(`test_readiness_knows_nothing_about_providers`). Lever 4 + Greenhouse 4 with the
flag on writes outputs byte-identical to the serial sweep with it off
(`test_lever_and_greenhouse_concurrency_with_the_flag_on`); the V2-B2 and V2-B4
suites pass unchanged.

## 18. Paid

**The same boundary, supported generically.** A paid or paid + free sweep is the
same `main()`: every paid search completes inside the paid loop, before the free
phase and the final emit, so once the marker exists no paid call can follow.
Verified offline with a paid-only sweep through `scraper.main()` — the client
stubbed to fail on any use, a placeholder token, sockets denied: the same two
searches in the same order, byte-identical outputs with the flag on and off, the
marker straight after `record_seen`, and no search after it
(`test_a_paid_sweep_searches_and_writes_the_same_and_is_ready_after`). Render's
fold is not free-specific: a paid run whose planned searches all finished reads
"finished"; one with searches outstanding reads the same "stopped early" or "out
of credit" ending it would show at exit, because `outstanding` is final by then.
A real paid public run was not exercised (**UNKNOWN** — not possible without
Apify). **No Apify call was made.**

## 19. Tests

`sweep/tests/test_results_ready_early.py` — **58 tests**. Most drive the real
stack: the worker on loopback, the public Render app pointed at it, and
`scraper.main()` on fixtures as the worker's child, on a thread. Timing points
are gates the engine waits at, not sleeps. Every non-loopback socket is denied.

| Brief | Group | Pins |
|---|---|---|
| 1 | `FlagOff` | default off and parsing; no deployment file sets it; no marker and identical bytes; exactly the pre-B5 status keys, and the screen waits for DONE |
| 2 | `ReadyBoundary` | call order; outputs final at publish; the marker's content; nothing scraped; crash before any output; ledger inside; stale marker cleared |
| 3 | `AtomicOutput`, `AtomicOutputEndToEnd` | first write, rewrite and crash mid-write, on the CSV and the JSON pass; bytes; temp names; a killed process's temp; the marker whole or absent (race E); malformed markers; never ready while the final write is paused (race A) |
| 4–5 | `ActiveSlotAndQueue` | slot held and the second run queued until the exit (race C); no second run from the same browser; healthz |
| 6–7 | `UITransition` | finished on screen while running; the page's script moves at readiness and keeps polling without it; old Render; old worker; only `true`; the strip; wording |
| 8–9 | `ResultsAndExports` | rows, `/results`, CSV and JSON at readiness equal those at DONE and the file |
| 10–14 | `FailureAfterReady`, `FailureBeforeReady` | six endings after readiness, and the result read while the shadow stage runs (races B, D); two endings before it, end to end |
| 15–16 | `WorkerLevel`, `RenderAuthorization` | never cleaned while alive; TTL from exit; stranger 404/401; no queue change; nothing but a bool and a number added |
| 17 | `PaidIsolation` | paid sweep through `main()`; no paid name in readiness code; the flag reaches the child only through the worker's environment |
| 18, B3 | `ShadowAndTelemetry` | shadow unchanged; milestone and `post_result_ms`; measured with the flag off; Lever + Greenhouse concurrency |
| perf | `HiddenWait` | the tail after readiness is not waited for; without the flag the screen waits |

B3's harness also clears the new variable in its base environment.

## 20. Mutation checks — all eighteen caught

Each applied alone to the source; seven suites run (`test_results_ready_early`,
`deploy.test_sweep_worker`, `test_public_sweep`, `test_worker_link`,
`test_active_run`, `test_search_v2_telemetry`, `test_search_v2_shadow_eval` —
230 tests); then reverted and checked byte-identical with `cmp`, and the
mutated files' SHA-256 after the whole run equal those before it. The brief's A–F are
A1–A2, B1–B2, C1–C2, D1–D2, E1–E3 and F; G–M are extra.

| # | Mutation | Result | Among the failures |
|---|---|---|---|
| A1 | readiness published before the final write | **6 fail** | `test_never_ready_while_the_final_write_is_paused`, `test_published_after_the_last_write_and_the_ledger_before_any_shadow_request`, `test_a_crash_while_writing_the_result_serves_no_partial_file` |
| A2 | outputs written in place: no temp, no rename | **7 fail, 1 error** | `test_a_reader_during_a_rewrite_reads_the_previous_complete_file`, `test_a_crash_mid_write_keeps_the_previous_file_and_no_temp`, `test_the_marker_is_whole_or_absent` |
| B1 | worker reports DONE at readiness | **9 fail** | `test_ready_releases_no_slot_and_starts_nothing`, `test_render_still_counts_the_process_as_in_flight`, `test_an_older_render_ignores_the_field_and_waits_for_done` |
| B2 | worker releases the slot at readiness | **2 fail** | `test_ready_releases_no_slot_and_starts_nothing`, `test_ready_changes_no_queue_state` |
| C1 | janitor may delete a ready run whose process is alive | **1 fail** | `test_a_ready_run_whose_process_is_alive_is_never_cleaned` |
| C2 | TTL counted from readiness, not from the exit | **1 fail** | `test_ttl_runs_from_the_process_exit_not_from_readiness` |
| D1 | the screen waits for the exit despite readiness | **5 fail** | `test_running_and_ready_is_finished_on_the_screen`, `test_the_page_script_moves_to_results_at_readiness`, `test_the_tail_after_readiness_is_no_longer_waited_for` |
| D2 | the strip on other screens ignores readiness | **1 fail** | `test_the_strip_on_other_screens_says_complete` |
| E1 | the engine withdraws its marker when the shadow call fails | **2 errors** | `test_a_shadow_board_failing_after_ready`, B3's `test_the_shadow_call_itself_raising_cannot_fail_the_sweep` |
| E2 | the worker withdraws readiness on a nonzero exit | **3 errors** | `test_a_crash_during_the_shadow_fetch`, `test_a_crash_immediately_after_ready`, `test_a_crash_while_finishing_telemetry` |
| E3 | the worker hides readiness once the run failed or was interrupted | **4 errors** | the three above and `test_a_worker_restart_after_ready` |
| F | readiness bypasses the owner check | **3 fail** | `test_a_stranger_gets_nothing_from_a_ready_run`, `test_a_stranger_holding_the_run_id_sees_no_readiness_and_no_rows` |
| G | any marker file counts, whatever it holds | **10 fail** | `test_a_marker_that_is_not_the_engines_reads_as_not_ready` (nine bodies), `test_the_tail_after_readiness_is_no_longer_waited_for` |
| H | Render accepts any truthy value as ready | **5 fail** | `test_only_a_true_moves_the_screen` (five values) |
| J | a previous run's marker is not cleared | **1 fail** | `test_a_stale_marker_from_an_earlier_run_is_cleared_at_start` |
| K | the readiness milestone is not recorded | **1 fail, 4 errors** | `test_the_record_says_when_the_result_was_ready_and_how_long_after`, `test_measured_with_the_flag_off_too`, both `HiddenWait` tests |
| L | readiness published before the seen ledger | **4 fail** | `test_the_ledger_is_inside_the_boundary`, `test_every_output_is_final_when_it_is_published`, `test_a_paid_sweep_searches_and_writes_the_same_and_is_ready_after` |
| M | the flag ignored: the marker always published | **6 fail** | `test_off_writes_no_marker_and_the_same_bytes`, `test_the_lifecycle_without_the_flag_is_what_it_always_was`, `test_without_the_field_the_page_keeps_polling_until_done` |

## 21. Measured hidden-wait benchmark

**MEASURED 2026-09-23 22:08 IST**, `bench/search_v2_results_ready.py`
([evidence](search-v2-evidence/results-ready-hidden-wait.json), revision
`…5b167c6-dirty`, i.e. this change before it was committed). Offline: the stack
of §19 with a post-result tail injected as a delay on each of the eight shadow
boards, three runs per arm, Render's `/progress` polled every 25 ms. `t1` is the
marker's time, `t2` the worker's `finished_at`.

| Tail | Flag | Hidden wait `t2 − t1` | `/progress` said finished, after `t1` | …before `t2` | `post_result_ms` |
|---:|---|---:|---:|---:|---:|
| 0 s | off | — | — | 26–31 ms **after** | 2–3 |
| 0 s | on | 2–3 ms | 28–31 ms | 26–29 ms after | 1–2 |
| 4 s | off | — | — | 13–25 ms **after** | 4,030–4,048 |
| 4 s | on | 4.039–4.040 s | 30–31 ms | **4.009–4.010 s** | 4,037–4,039 |
| 12 s | off | — | — | 14–27 ms **after** | 12,031–12,044 |
| 12 s | on | 12.042–12.046 s | 31–32 ms | **12.010–12.016 s** | 12,038–12,043 |

- **Flag off**: in every run the screen said finished only after the process
  exited — one poll interval after.
- **Flag on**: one poll interval after the marker, and the whole tail before the
  exit. With no tail there is nothing to hide: identity and finish take 2–3 ms.
- **`t2 − t1` equals `post_result_ms` to within ~3 ms** (interpreter exit and
  the worker's bookkeeping), so the telemetry field alone measures the hidden
  wait in production, for every sweep, flag or not.
- **The slot**: held for the whole tail in every run (§5); the queue behind is
  no faster.

What this does not show: the browser. The page polls every 4 s and moves 1.2 s
after it sees `finished` — both unchanged — so a user gets the saving in 4 s
steps, averaging the tail (**INFERRED**). And an injected tail is not a
production one.

**Likely production benefit — INFERRED from B3's evidence, not from one run.**
The hidden part is `post_result_ms`: B3's shadow stage (≈ 5 s fetch + 1.3 s
evaluation, ~6.5 s typical by B3 §7; observed in production at ~4 s, ~4.5 s and
~12.1 s; one 25 s timeout-and-retry adds 26–29 s, worst case ~78 s a board), plus
identity and finish. In the brief's example (76.81 s total, 12.13 s shadow
stage) the result would appear ~12 s sooner, ~16% of that sweep. The real
distribution comes from §23, where `post_result_ms` is already recorded.

## 22. Unresolved risks

1. **A second Start from the same browser during the tail.** For those seconds
   `_sweep_in_flight()` is still true, so Configure says "You already have a
   Sweep in progress" — literally true — while the strip says "Sweep complete",
   and Start sends them to the running screen, which says their jobs are ready.
   Brief and harmless, and deliberate: the alternative is a second run this
   browser can no longer stop.
2. **Readiness is only as right as its one call site.** The worker trusts the
   marker. A future edit that writes a user-visible file after
   `publish_results_ready()` would serve a result that then changes; the order
   test catches one made through `write_outputs` or `record_seen`, not a new
   writer.
3. **Queue latency is not reduced.** The tail still holds the slot; a visitor
   waiting behind waits exactly as long as before.
4. **The tail becomes invisible to anyone watching the UI.** A slow or stuck
   post-result stage (worst case ~78 s per shadow board, B3 §7) no longer shows
   as a slow sweep on screen. Watch `post_result_ms` and
   `finished_at − results_ready_at` instead (§23).
5. **Browser granularity.** The page polls every 4 s and moves 1.2 s after it
   sees `finished`, so the saving arrives in 4 s steps: in expectation it is the
   whole tail, on a given sweep a 2 s tail may save 0 s or 4 s (**INFERRED**).
6. **`fsync` cost on Oracle — UNKNOWN**, expected milliseconds (§6).
7. **The Free "failed reads as finished" behaviour** (§11) is pre-existing and
   out of scope.
8. **Local console unaffected**: `read_ready` is always false there. A console
   run with the flag on writes a marker nothing reads, and its next run clears
   it.

## 23. Proposed production experiment — NOT executed

**Sequencing.** B5 moves nothing B3 measures — the shadow stage's code,
placement and HTTP semantics are identical, and its records only gain two
timing fields — so it could be enabled while B3's sample is still collecting
without confounding it. If the review prefers one variable at a time, enable it
after.

1. **Oracle, flag absent.** `sudo -u sweepworker git -C /opt/sweep-worker/app pull`,
   then `sudo systemctl restart sweep-worker` while no sweep is running. Nothing
   changes for users. Check one natural Free Sweep: no `results_ready` in its
   status, outputs normal, and `post_result_ms` in its telemetry record.
2. **Render.** Deploy main. Nothing changes: no field is sent yet.
3. **Enable.** Add `SWEEP_RESULTS_READY_EARLY=1` to `/etc/sweep-worker/env` and
   `sudo systemctl restart sweep-worker` while idle.

Then over 10–20 natural sweeps, from each run's `status.json`, its
`output/.results_ready` and its telemetry record — timestamps and counts only:

| Watch | Healthy |
|---|---|
| hidden wait `finished_at − results_ready_at` | ≈ `post_result_ms`, on every ready run |
| results served at readiness | `GET /v1/runs/<id>/rows` 200 between the two times in the worker's access log (journald), where a visitor was watching |
| nothing rewritten after readiness | `mtime(jobs_*.csv) ≤ results_ready_at` on every run |
| queue overlap | no two runs' `[started_at, finished_at]` intersect |
| cleanup | no run directory removed while its state was `queued` or `running` |
| failures | `failed` runs split by whether `results_ready` was true; no rise overall |

**Stop rule**: turn the flag off on any overlap, any CSV modified after its
readiness, any run cleaned while active, or any `/rows` or export error between
readiness and exit.

## 24. Rollback

`SWEEP_RESULTS_READY_EARLY=0`, or delete the line, in `/etc/sweep-worker/env`,
then `sudo systemctl restart sweep-worker` while no sweep is running. **A restart
is required** (**VERIFIED**, as in V2-B4 §16): the unit loads its
`EnvironmentFile` at process start and each child inherits the worker's
environment (`default_spawn` copies `os.environ`). A restart marks an in-flight
sweep interrupted and re-queues queued free ones. Render needs nothing — without
the field it waits for DONE. Markers already written stay with their finished
runs and go with the janitor. The atomic write stays; it needs no rollback.

## 25. Recommendation

**Worth the production experiment in §23, and likely worth keeping on.**

- The **gain** is the whole post-result tail, taken off the user's wait: B3's
  shadow stage — observed in production at ~4 s, ~4.5 s and ~12.1 s, estimated
  by B3 at ~6.5 s typical, 26–30 s with one stalled board — plus identity and
  finish. On the production example in the brief (76.81 s total, 12.13 s shadow
  stage) that would be ~12 s of 77 s (**INFERRED from one sweep, illustration
  only**).
- The **risk to the result** is nil on the evidence: same bytes, same rows at
  readiness as at DONE, never half a file, and nothing after readiness can take
  it away.
- The **risk to the worker** is nil on the evidence: the slot, the queue, the
  janitor, recovery and ownership read what they always read, and `Queue` was
  not edited.
- It does **not** shorten the queue for the next visitor, and it hides slow
  tails from the screen, so the tail has to be watched in telemetry instead.

It stays OFF in this commit.

## 26. Verification

- `python -m unittest discover -s sweep/tests -t .` — **1142 tests, OK** (1084
  before, +58). The new module passed three runs in a row with no stray thread
  tracebacks.
- `python -m unittest deploy.test_sweep_worker deploy.test_modal_benchmark` —
  **42 tests, OK**.
- The V2-B2, B3, B4 and telemetry suites — 165 tests — with
  `SWEEP_RESULTS_READY_EARLY=1` forced into their shared end-to-end harness
  environment: **OK**, 36 markers published along the way. B3 and B4 behave
  identically with readiness published.
- `python scraper.py --demo`, `python telemetry.py`, `python -m sources`,
  `python -m sources.concurrency` — pass.
- `auto-apply` suite — 1,102 tests, 2 errors in `test_inference` healthz (a
  local inference service answering 503); **the same two errors reproduce on a
  clean checkout of `5b167c6`**, as they did for B3 — environmental.
- Mutation checks — 18/18 caught; every file restored byte-identical (`cmp` per
  mutation, and the SHA-256 of all five touched sources equal before and after).
- Hidden-wait benchmark — §21, run twice; the passes agree to within ~5 ms per
  arm.
- **No Apify call and no real sweep**: every end-to-end run was `scraper.main()`
  on fixtures with sockets denied (loopback allowed for the worker in this
  stage's module), and the one paid path exercised ran against a client stubbed
  to fail on use.
