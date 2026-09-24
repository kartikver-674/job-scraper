# V2-C4.5 — one sweep, several authorised accounts: every start placed before it is sent, held twice, and never handed back

Date: 2026-09-24. Baseline: `22a89a2` (V2-C4, local; C0 `89c8daa`, C1 `a3b4bd2`,
C2 `429b7b6`, C3 `61681c9`, C3.5 `ccd5720`). HEAD was verified before starting
and the tree was clean. Nothing is deployed and no flag is set anywhere.
Commits: `51764a0` (the pool), `b6a974b` (the developer clamp, §25), and the
live canary's evidence (§26). **One live paid experiment: the two-start
canary, $0.092 intended, $0.0601 settled.**

**Outcome: A — multi-account execution READY for C5 review** (as of the
second preflight). The first real-account preflight (§19) placed only 84 of
90 on five accounts ($9.879 usable), so the stage first closed as B. With
seven accounts configured, the two-start live canary (§26) ran each search on
its own account, held each ceiling in both ledgers, and was confirmed by the
provider's own run listings; the C5 preflight after it (§27), on the
accounts' REAL capacity with no clamp, places **90 of 90** ($15.894 usable
across seven accounts, five used) with every check green. The mode stays off;
C5 is not run.

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a
dated reading), **VERIFIED** (read in code, or pinned by a test that fails
when it stops being true), **INFERRED** (reasoned, not observed), **UNKNOWN**.

## 0. Headline

- `SWEEP_PAID_MULTI_ACCOUNT` (default **off**, and effective only under
  `SWEEP_PAID_CONCURRENCY`). Off, the C2 scheduler is C4's, byte and request
  for request (VERIFIED, §13). On, every configured account is read once,
  deduplicated by who it is, and **every provider-bounded search is assigned an
  account before the first start** by an exact allocator (§7).
- A start then needs its **full ceiling to fit twice** — the sweep's one
  `PaidExposure` (C2's, unchanged, still `max_spend_usd`) AND its own
  account's snapshot headroom — and its account's memory and a run slot.
  Money committed is never released during the sweep; memory and slots are,
  once the provider reports the run terminal (§5, §6).
- The allocator is exact, not greedy: it proves the longest placeable prefix
  of the plan and fills accounts smallest first. On 500 random pools against
  the real 90-search plan, most-headroom-first admitted fewer searches than it
  in 193 and best-fit in 169; it strands the least ($0.060 mean vs $0.127 and
  $0.113) (MEASURED, §8).
- A durable `paid_account_ledger.json` is written atomically at every
  transition, "committed" before the start request. An unfinished ledger with a
  committed start **stops the next sweep for the operator**; automatic
  cross-account monetary resume is not claimed (§12).
- C0 stays the global research guard: it now wraps the pool's credential step
  too, and `--max-usd` is unaffected by how many accounts exist (§14).

## 1. Scope

In: token discovery, account identity and deduplication, headroom semantics,
the two-layer reservation, runtime capacity, the allocator and its proof, C2
integration, the durable ledger, telemetry, the probe extension for C5, the
zero-paid real-account preflight. Out (unchanged): the plan, depths, provider
order, batching (none, C3), the C4 cap formula, adaptive policy (shadow, C4),
polling, workers (default 2, clamp 1..4), production and deployment.

## 2. Token discovery (VERIFIED)

`pool_tokens()` is `apify_tokens()` narrowed to `APIFY_TOKEN` and
`APIFY_TOKEN_<n>` with `n` a positive integer without a leading zero, in slot
order (`APIFY_TOKEN`, then numeric: `_9` before `_10`). Dynamic environment
discovery, not a fixed scan — `apify_tokens()` already walked every
`APIFY_TOKEN*` name; the pool only refuses names that are not slots
(`APIFY_TOKEN_0`, `_02`, `_X`, `_2A`, `APIFY_TOKENS`, lower case, empty
values). `apify_tokens()` itself is untouched, so the single-account path,
`rescore_from_apify.py` and the UI read exactly what they read before.
Identical values in two slots were already one entry. Tests: one, five, ten,
sparse (`APIFY_TOKEN`, `_3`, `_8`), malformed.

The pool's credential step is `_require_token_pool()` (load `.env`, return the
slots) — the pool's twin of `_require_token()`, and wrapped by the guard (§14).

## 3. Account identity and deduplication (VERIFIED)

Two free reads per slot: `users/me` (**who**) and `users/me/limits` (how
much). A token is not an account: slots whose `users/me` returns the same
account id are **one** `PoolAccount` — one balance, one memory limit, one set
of run slots, one exposure ledger — holding the smaller of their readings.
The id is used for grouping and dropped; no id, username or email is kept.
A slot whose account cannot be identified (either read fails, or no id) is
**left out** and reported by exception type only: counted once too often it
would double a balance. Labels are `account_000`, `account_001`, ... in slot
order; telemetry and evidence carry the label and the slot NAMES
(`APIFY_TOKEN_3`), never a value.

## 4. Account headroom (VERIFIED in code; fields MEASURED on the operator's accounts)

The SDK's `users/me` exposes `plan.monthly_usage_credits_usd` and
`plan.max_monthly_usage_usd`; `users/me/limits` exposes
`limits.max_monthly_usage_usd` and `current.monthly_usage_usd`, plus memory
(`max_actor_memory_gbytes`, `actor_memory_gbytes`) and runs
(`max_concurrent_actor_jobs`, `active_actor_job_count`). On every FREE account
read on 2026-09-24 the credit, the plan maximum and the limit were all $5.00.

    headroom = max(0, min(limit, included credit) - usage), floored to $0.001
    capacity = max(0, headroom - ACCOUNT_BUFFER_USD)

The smaller of the hard limit and the included credit: never plan to spend
into overage an account has not prepaid, and never past its own limit. A cap
that cannot be read is no headroom. The plan tier is diagnostic only — a FREE
account with $4.80 is $4.80 of capacity, a paid one with $20 is $20.

**Buffer.** `ACCOUNT_BUFFER_USD = $0.01` per pooled account, kept out of every
actor ceiling. MEASURED basis: the account delta beyond its runs' settled
charges was $0.000009–$0.000039 per probe of one or two runs across V2-C1..C3.5
(`c1-probe-a/b`, `c2-live-concurrency-canary`, `c3-live-batch-canary`,
`c35-*`), about $0.00002 a run, and it lands on the account that ran the run.
A cent is more than 100x what one account running its share of 90 starts would
leave. The old single-account C5 rule (+$0.10 on the one account) is replaced
by this per-account cent.

The capacity is a **snapshot**: read once before any start, never replenished
— not by a run that cost less than its ceiling, a $0 reading, a lagging delta
or a success.

## 5. Global and per-account exposure (VERIFIED)

C2's `PaidExposure` is unchanged and stays authoritative for the sweep:
`committed + pending + ceiling <= max_spend_usd`. Each `PoolAccount` carries
**its own `PaidExposure`**, the same class and rules, whose budget is its
capacity: `committed + pending + ceiling <= capacity`. A start's ceiling is
reserved in the global ledger, then its account's; committed in both
immediately before the request (`_PooledHolds.commit`); released from both
only if the start was never attempted. `max_spend_usd` is not multiplied by
accounts: $49.90 of pool with a $0.10 sweep admits two $0.046 starts
(test, mutation H).

Account readings after a run are that account's own (`pool.read` → its
coordinator client), summed across accounts for the pre-C2 guard's figure;
no account's spend is derived from another's. They enter both ledgers through
`observe()`'s `max`, so a reading can shrink headroom, never add it.

## 6. Runtime resources (VERIFIED)

Per account: memory available at the snapshot (limit less in use) and run
slots (concurrent-run limit less active). Each start holds its actor's
default run memory — read once per actor from its record
(`default_run_options.memory_mbytes`; MEASURED 2026-09-24: LinkedIn 512 MB,
Indeed 4096 MB; Sweep never names a memory, so this is what runs get) — and
one slot. If the record cannot be read, a start holds the whole account.

A hold is released when the provider reports the run terminal (`SUCCEEDED`,
`FAILED`, `TIMED-OUT`, `ABORTED`, via `scrape_search`'s new `after_run` hook)
or when the start was never attempted. A start that raised, or a run whose
end was never confirmed, keeps its memory and slot for the rest of the sweep
(`runtime_held_unknown`). Money and runtime are separate: runtime is released,
money never.

If its account lacks memory or a slot while an earlier run of that account
still lives, a search **waits** — not a budget failure, never a migration. If
the account can never fit it (nothing of its own is running), the phase stops
(`account_resources`).

## 7. The allocator (VERIFIED; exactness MEASURED against exhaustive search)

`place_units(units, accounts)` returns the **longest prefix of the plan** that
can be placed with every search wholly on one account — a bin-packing
question, not "does the sum fit" (three accounts of $0.10 hold no $0.135
search; test, mutation B). Ceilings are whole mills ($0.001, C1's step).

Proof, not heuristic: `_suffix_tables` computes, for accounts k.., the most of
the smallest ceiling that can sit beside every exact count of the larger
ones, trying every split — for a fixed split of the larger sizes, as many of
the smallest as fit is never worse, so a missing key is a proof that no
placement exists. The current plan has two sizes (46 and 135 mills), so a
table has at most 73 keys. Placeable prefixes are downward-closed, so the
longest is found by binary search.

Assignment: accounts are filled **smallest capacity first** (best fit: a
nearly spent account takes the search it can still hold and the large ones
stay large — `$0.14` beside `$4.00` takes the `$0.135` search), each as full
as it can be while the rest can still be placed (checked against the
tables). Within one size, searches take accounts in that order, in plan order.
Deterministic; 250 random small instances match exhaustive search exactly on
feasibility and on the longest prefix (test).

## 8. Most-headroom vs best-fit vs exact (MEASURED offline, [evidence](search-v2-evidence/c45-allocator-benchmark.json))

Admitted searches (of the plan):

| shape | most-headroom | best-fit | exact |
|---|---|---|---|
| A one large account | 90/90 | 90/90 | 90/90 |
| B five $5 accounts | 90/90 | 90/90 | 90/90 |
| C fragmented (the C5 preflight's) | 84/90 | 84/90 | 85/90 |
| D fresh near $5 | 90/90 | 90/90 | 90/90 |
| E aggregate enough, no account fits | 0/1 | 0/1 | 0/1 |
| F exact-fit boundary | 90/90 | **89/90** | 90/90 |
| F one mill short | 89/90 | 89/90 | 89/90 |
| G mixed, best-fit strands | 5/5 | **4/5** | 5/5 |
| G mixed, most-headroom strands | **2/3** | 3/3 | 3/3 |
| H C5 plan, three fresh accounts | 90/90 | 90/90 | 90/90 |
| H C5 plan, two $5.30 accounts | 90/90 | 90/90 | 90/90 |

500 random pools (2–6 accounts, $0–$5 each) against the real plan: most-
headroom admitted fewer than exact in **193**, best-fit in **169**, exact never
fewer than either. Mean stranded headroom (left below the smallest ceiling
still unplaced): most-headroom $0.127, best-fit $0.113, **exact $0.060**.
Neither greedy order is superior (each has a shape the other wins); exact wins
both. **Chosen: exact, smallest-first fill.**

## 9. Planned vs online assignment

Money is assigned for the whole bounded plan at the start
(`AccountPool.plan`, logged per unit in the ledger as `assigned`/`unassigned`
with its account and ceiling). Runtime is online: the coordinator dispatches
in plan order, a search waiting for ITS account's memory or slot. No search
migrates. Consequence (INFERRED, by construction): a search waiting on a busy
account holds back the searches after it even if another account is idle —
plan order is kept over throughput. At C5's shape (16 GB and 5 runs per FREE
account, 4096 MB Indeed, 2 workers) no wait is expected.

Unbounded searches (Naukri, off by default) have no ceiling to place: under
the pool the phase stops at the first one (`unbounded`). Searches beyond the
placeable prefix stop it at the first (`unassigned`), as the cap stops C2 at
the first search that does not fit. Stopped searches are `skipped_account`.

## 10. C2 integration (VERIFIED)

One scheduler. `paid_phase_c2(..., pool=None)`: the plan's entries are now
built for every site before the first site runs (same numbering, same
content), so the pool can place the whole plan; with `pool=None` every branch
is C4's. The coordinator alone assigns (at plan time) and reserves (before
dispatch); a worker gets its account's client factory, a `_PooledHolds` behind
the `commit`/`release` interface it already calls, and two hooks. It never
chooses (mutations E, F). One client per search (C2), built from its account's
token; one coordinator read client per account; no client is shared across
threads, accounts or searches. Integration is C2's, in plan order (mutation K).

`scrape_search` gains two additive hooks, `after_start(run_id)` and
`after_run(status)`, passed only by the pool (`None` otherwise: the same calls).
Single-account workers are started with C2's exact four arguments.

## 11. Failures (VERIFIED)

| event | global | account | runtime | continue? |
|---|---|---|---|---|
| local failure before the request | released | released | freed | yes |
| `actor.start` raised (ambiguous) | committed | committed | held (unknown) | yes, never retried |
| provider refuses start: credit (`ACCOUNT_CREDIT`) | committed | committed | held | **stop** the phase |
| provider refuses start: memory/concurrency (`ACCOUNT_RESOURCE`) | committed | committed | held | **stop** |
| run `FAILED` / `TIMED-OUT` / `ABORTED` | committed | committed | freed | yes |
| engine deadline → abort → terminal | committed | committed | freed | yes |
| dataset failure after `SUCCEEDED` | committed | committed | freed | yes |
| account read failure after a run | — | — | — | yes, run-record fallback (C2) |
| account unreadable at discovery | — | slot left out | — | yes |

No search is ever re-bought on another account in the same sweep (mutation N).
`ACCOUNT_CREDIT`/`ACCOUNT_RESOURCE` are recognised from the refusal's text
(INFERRED wording; an unrecognised refusal is an ordinary ambiguous start:
held, not retried, the sweep continues). Recognised at arrival, so no further
search is sent; searches already in flight finish.

## 12. Crash and resume (VERIFIED)

`paid_account_ledger.json`, beside the outputs, written whole (temp, fsync,
rename; `_replaced`, B5's) at: assignment, pending, **committed (before the
request)**, run id, provider status, integration, finish. No token, no query.
`.done_combos` keeps its meaning (logical search safely checkpointed) and
carries no account.

On the next pool sweep in that directory: a ledger that did not finish with
any start `committed` **stops the sweep** ("Refusing to start ... Review it,
then move it aside"): its ceilings may still be charged on accounts a new
snapshot would read as having headroom (account usage lags), and which search
was bought where is the operator's to reconcile. Anything else (finished, or
nothing committed) is archived with a timestamp and the sweep runs. Once the
operator moves a stopping ledger aside, `.done_combos` resumes exactly as C2:
only undone searches are bought.

| crash (tests A–G) | ledger shows | next sweep |
|---|---|---|
| A before any assignment is written | nothing | runs |
| B after a pending hold (nothing committed) | pending | runs, archived |
| C committed written, request never sent | committed | **stops** (safe false hold) |
| D after the start, run id not written | committed, no run id | **stops** |
| E after the run id | committed + run id | **stops** |
| F provider success, before the checkpoint | committed + SUCCEEDED | **stops** |
| G checkpoint written, before the done marker | unfinished | **stops** |

Multi-account mode is therefore for one uninterrupted developer/C5 sweep; a
crash is an operator review, not an automatic paid resume. Cross-process
locking is not built.

## 13. Parity (MEASURED offline)

- **Flag off = C4**: `SWEEP_PAID_MULTI_ACCOUNT` unset vs `0`, workers 1/2/4 —
  identical requests (exact sequence at one worker), CSV, JSON, seen, done; no
  `accounts` section, no ledger file (mutation P). Flag on without the
  scheduler: one account, as before, with a note.
- **Pool = single account**: C4's mixed LinkedIn + Indeed plan (structural
  pairs, twin postings, the first planned search finishing last) over three
  accounts vs one, workers 1/2/4 — byte-identical CSV, JSON, seen and done; the
  same starts, inputs and ceilings; the same C4 shadow decisions and losses;
  the same unit ids, fingerprints, funnels, traces and provider positions.
- **Duplicate tie**: `paid_000` on one account finishing last, `paid_001` on
  another finishing first, one shared posting — `paid_000` keeps it.
- **No batching**: one logical search, one start, under the pool (C3).

## 14. C0, the profile cap and the C4 cap (VERIFIED)

`bench/paid_guard.py` wraps both credential steps, `_require_token` and
`_require_token_pool`: a developer run whose plan turns paid after the check
reaches neither, with or without keys (test). `--max-usd` stays the cumulative
research limit, independent of the pool: 0.773 + 10.548 fails at $11.32 and
passes at $11.33 (test). `max_spend_usd` stays the sweep's own, global cap.
C4's generated-cap formula is untouched.

## 15. Telemetry (VERIFIED)

`paid_execution.accounts` (only under the pool; schema `search-v2c2.1`
unchanged, the section additive): `account_count`, `buffer_per_account_usd`,
`aggregate_headroom_usd`, `aggregate_capacity_usd` (a sum of independent
accounts, not a provider guarantee), `bounded_units`, `bounded_exposure_usd`,
`placed_units`, `allocation_feasible`, `stranded_usd`, `actor_memory_mb`,
`excluded_slots`, `accounts_used`, `account_switches` (between consecutive
starts, in dispatch order), and per account: label, slots, plan tier,
headroom, capacity, projected units by provider, projected/committed/remaining
USD, pending peak, starts, failures, peak in-flight, peak memory,
`runtime_held_unknown`, stranded. Each `paid_execution.units[]` entry gains
`account`, `provider_status`, `runtime_freed`, `account_refusal`. `paid_units`
(C1/C4 identity, fingerprints, positions, contribution) is untouched.

## 16. Privacy (VERIFIED)

Fixture tokens (`SUPER_SECRET_TOKEN_*`) are absent from stdout, stderr,
telemetry, CSV, JSON, seen, done, the durable ledger and its archives, even
when an account's own read error quotes its token (discovery keeps the
exception type only) (test, mutation L). `PoolAccount.__repr__` is its label.
The preflight scans its output for every configured token before writing.

## 17. Tests and mutations

`sweep/tests/test_paid_multi_account.py`: flag, flag-off parity, discovery
(1–7 and identity, headroom), allocator (1–9, exhaustive exactness,
determinism, projection), execution (assignment, both ceilings pending at
worker start, per-account caps at workers 1–4, global budget, simultaneous
reservation, lag safety, waiting, per-account memory, never-fit, unplaced, no
batching, section), failures (pre-network, ambiguous, FAILED/TIMED-OUT/ABORTED,
dataset, credit, resource, account read), output parity (workers 1/2/4),
duplicate tie, crashes A–G and resume, privacy, the guard, the probe extension
and the benchmark's reachability. Mutations A–P: see
[c45-mutations.json](search-v2-evidence/c45-mutations.json).

## 18. Overhead (MEASURED offline)

Planning the 90-search plan: 47 ms CPU / 19 KiB at 5 accounts, 108 ms / 31 KiB
at 10, 232 ms / 55 KiB at 20 — once per sweep. Per start, the pool's verdict,
holds, hooks and release with four fsynced ledger writes: ~1.7 ms. Requests
added under the pool: two free reads per slot and one per paid actor, once;
account reads after each search stay one per search (C2). All negligible next
to a provider search (~7–30 s).

## 19. Zero-paid real-account preflight (MEASURED 2026-09-24, [evidence](search-v2-evidence/c45-c5-preflight.json))

    .venv/bin/python -m bench.search_v2_paid_probe --preflight --full-plan \
        --stage C5 --paid-workers 2 --adaptive-mode shadow --multi-account \
        --sweep-budget 10.55 --max-usd 11.33 --exposed-usd 0.773 \
        --output docs/search-v2-evidence/c45-c5-preflight.json

Free reads only (users/me and users/me/limits per slot, each paid actor's
record once), through `ReadOnlyClient`; the guarded child ran without keys
and was blocked. **Paid calls: 0.**

- Plan (the child's own preview): LinkedIn 18 x $0.046, Indeed 72 x $0.135,
  depth 15 — 90 logical searches, 90 physical starts, bounded exposure
  **$10.548**, engine cap **$10.55**, guard $0.773 → $11.321 ≤ $11.33.
- Token slots: `APIFY_TOKEN`, `_2`, `_3`, `_4`, `_5` — **five distinct
  accounts**, no duplicate slot, none excluded (earlier the same day `_5`
  held `_4`'s key; it now holds a fresh account's). All FREE; 16 GB actor
  memory, 0 in use; 5 run slots, 0 active, on each.
- Actor memory read: LinkedIn 512 MB, Indeed 4096 MB (4 Indeed runs at once
  per account, so workers = 2 is never account-throttled).

| account | slot | headroom | usable | projected | leftover |
|---|---|---|---|---|---|
| account_000 | APIFY_TOKEN | $3.407 | $3.397 | 25 Indeed ($3.375) | $0.022 |
| account_001 | APIFY_TOKEN_2 | $0.021 | $0.011 | — | $0.011 |
| account_002 | APIFY_TOKEN_3 | $0.509 | $0.499 | 2 LinkedIn + 3 Indeed ($0.497) | $0.002 |
| account_003 | APIFY_TOKEN_4 | $0.993 | $0.983 | 15 LinkedIn + 2 Indeed ($0.960) | $0.023 |
| account_004 | APIFY_TOKEN_5 | $4.999 | $4.989 | 1 LinkedIn + 36 Indeed ($4.906) | $0.083 |
| **total** | | **$9.929** | **$9.879** | **84 of 90** | stranded $0.141 |

Aggregate usable $9.879 < $10.548: **allocation infeasible**, and the
shortfall is not only the $0.669 sum — $0.141 is stranded in fragments no
remaining $0.135 search fits. The smallest single extra account that makes
all 90 placeable holds **$0.767 usable ($0.777 headroom)** (computed offline
through `place_units` on these capacities). No single account holds the cap
either (best $4.999, the old single-account rule's $10.65).

Checks: preview blocked without keys ✓, every search bounded ✓, engine cap
holds the exposure ✓, C0 cumulative within `--max-usd` ✓, research ledger
allows (C5's separate budget) ✓, **pool places every bounded search ✗**,
placed accounts support 2 workers ✓. **Not ready for C5 review.**

## 20. C5 tooling (VERIFIED)

`bench/search_v2_paid_probe.py`, still behind C0: `--full-plan` (the
repository's default plan — every enabled paid site, the free sources — with
production's free flags B2–B5 set in the child), `--adaptive-mode`,
`--multi-account` (needs `--paid-workers`), `--keep-output DIR`, run listing
sized to the starts, one account per underlying account in its post-run reads,
run ownership from the engine's own section, per-provider evidence fields,
per-stage ledger ceilings (C5's own, §21), and `--preflight`: free reads
through `ReadOnlyClient` (no start, no abort), placement, and the guarded
child without keys, parsed back as the plan it previews.

## 21. Research ledger

Until the canary, no entry. `separate_budgets` then records two refuse-only
ceilings outside the C1–C4 $2.00: **C4.5, $0.87** (0.773 + 0.092 for the
two-start canary) and **C5, $11.42** — recomputed after the canary as 0.865
recorded + 10.548 bounded = 11.413 (it was $11.33 on 0.773). The canary's
entry: stage C4.5, 2 logical searches, 2 starts, 2 accounts, $0.092 intended,
**$0.0601 settled**; cumulative **$0.865 intended / $0.57045 actual**. The
C5 sweep's own `max_spend_usd` stays **$10.55**: the guard's limit is the
cumulative research exposure, the engine's is the sweep's authorisation.

## 22. Unresolved risks

1. **Head-of-line waits** (§9): plan order over throughput when an account is
   busy. Not expected at C5's shape; measurable in C5's `paid_execution`.
2. **Snapshot staleness**: anything else spending on a pooled account during
   the sweep is invisible until a reading shows it (as C2 for one account).
3. **Refusal wording** (§11) is INFERRED; an unrecognised refusal is held and
   not retried, but the sweep continues onto that account's other searches.
4. **Resume is manual** (§12). A finished ledger does not protect a rerun
   minutes later from account lag — pre-existing for one account under C2.
5. **Residual platform usage** per account is covered by $0.01 on evidence
   from one- and two-run probes; C5 measures it at full scale per account.
6. **Paid plans**: headroom is capped at the included credit, so overage an
   operator would accept is not used. Deliberate.
7. **The canary did not reverse the finish order**: `paid_001` finished after
   `paid_000` live, so the out-of-order case (a later search finishing first)
   is proved offline only (§13), not by the canary.
8. **Two starts is not 90.** Per-account residuals, head-of-line waits and
   provider throttling at C5's scale are measured by C5 itself.

## 23. Optional live canary

Run after separate approval: §25 and §26.

## 24. Verification

- **Baseline.** HEAD `22a89a2`, clean, at the start.
- **Sweep suite.** `python -m unittest discover -s sweep/tests -t .` —
  **1,524 tests, OK** (1,449 + 75), 243 s, on the final tree. It includes
  C0's reachability scan: `bench/search_v2_paid_accounts.py` matches none of
  its reach patterns (and a test pins that it names no client, token or start).
- **Deploy tests.** `deploy.test_sweep_worker deploy.test_modal_benchmark` —
  **42 tests, OK**.
- **Self-checks.** `scraper.py --demo`, `telemetry.py`, `config.py`,
  `-m sources`, `-m sources.concurrency` pass.
- **auto-apply.** 1,102 tests with the 2 `test_inference` healthz errors (the
  local inference service), the same two reproduced on the clean `22a89a2`
  tree at the start of this session. One run also failed
  `test_nothing_is_written_to_disk` because a sweep test ran concurrently and
  created a `sweep-test-*` temp dir; rerun alone: only the two healthz errors.
- **Mutations.** 16/16 caught (A–P), `scraper.py` restored byte-identical
  after each ([evidence](search-v2-evidence/c45-mutations.json)).
- **Production isolation.** `config.py`, `deploy/`, `render.yaml`,
  `sweep/app.py`, `sweep/plan.py`, `sweep/runs.py`, `sweep/public.py`,
  `sources/`, `telemetry.py`, `paid_adaptive.py`, `requirements.txt` are
  unchanged. Changed: `scraper.py` (the pool, two additive `scrape_search`
  hooks, `paid_phase_c2(pool=None)`, the `main()` branch), `bench/paid_guard.py`
  (wraps both credential steps), `bench/search_v2_paid_probe.py`, the ledger
  (`separate_budgets`), one C1 ledger test made stage-aware. No flag,
  environment or deployment change.
- **After the clamp (`b6a974b`).** Sweep suite **1,535 tests, OK**; deploy 42
  OK; `scraper.py --demo` OK; clamp mutations 7/7.
- **After the canary.** C4.5, C0, C2, C1, C3.5, C4 and telemetry suites
  (**416 tests, OK**), `telemetry.py`, and the canary's own outputs, ledger
  and telemetry parsed and checked (§26). No provider was rerun.
- **Paid.** Only the canary (§26): two LinkedIn starts, $0.092 intended,
  $0.0601 settled. Otherwise free account and actor reads (§4, §6, §19, §27).
- **Tokens.** No configured token value appears in any new or changed file
  (the preflight scans its own output; checked again before commit).

## 25. The developer clamp, `SWEEP_PAID_ACCOUNT_CAP_USD` (VERIFIED; `b6a974b`)

The first canary preflight put **both** $0.046 searches on one account
(`account_002`, $0.499 usable): the allocator fills the smallest account that
fits as full as it can (§7), and no account had between $0.046 and $0.092 —
so two searches could never split. Correct for C5, useless for a two-start
proof of account separation.

`SWEEP_PAID_ACCOUNT_CAP_USD` (developer-only; unset or empty: nothing; read
only under the pool):

    effective capacity = min(real usable capacity, cap)        (Decimal)

It only lowers. The provider's headroom reading, the real usable capacity
(headroom less the buffer), the sweep's `max_spend_usd`, every provider
ceiling and C0's arithmetic are untouched; the single-account path and C2
with the pool off never read it. Telemetry keeps the three figures apart
(`headroom_usd`, `real_capacity_usd`, `effective_capacity_usd`,
`developer_clamped`) and names the clamp: `developer_account_cap` = "a
developer-only clamp imposed locally on each account's usable capacity for
this sweep; not provider capacity and not the sweep's budget".

Values: a positive finite amount is the clamp; anything else — `0`, negative,
`abc`, `nan`, `inf`, `$0.046` — **refuses the paid phase before any
credential** (fail closed: a clamp someone set and mistyped must not become
no clamp). The probe passes it to the child only (`--account-cap-usd`,
multi-account only and **never with `--full-plan`**), and drops a shell
leftover, so C5 cannot inherit it.

Tests (14, `AccountCap` in `test_paid_multi_account.py`): unset = `51764a0`
exactly (requests, bytes, assignment, effective = real); real wins above the
cap, the cap wins below; one ceiling = one search per account; two searches
go to two accounts and never the $0.011 one; `max_spend_usd` and the $0.046
ceiling unchanged; ignored serial and with the pool off, even malformed;
malformed refuses before any credential; no token; the readings stay the
provider's. Mutations A–G ([evidence](search-v2-evidence/c45-cap-mutations.json)):
raises capacity, rewrites headroom, reaches the single-account path, stays
on when unset, becomes the sweep budget, parses through a float (0.046 →
0.04599…, which holds no search), ignored by the allocator — **7/7 caught**.

## 26. The live multi-account canary (MEASURED 2026-09-24, [evidence](search-v2-evidence/c45-live-multi-account-canary.json))

**Preflight** ([evidence](search-v2-evidence/c45-canary-preflight.json), zero
paid): the ceiling recomputed from code, $0.046; clamp $0.046; 2 LinkedIn
searches at depth 15 (the C2 canary's generic shape: "Backend Developer" and
"Full Stack Developer" @ India, synthetic `software_fullstack` cohort), 2
starts, $0.092; seven distinct accounts, none excluded; projected
`paid_000` → `account_000` (`APIFY_TOKEN`, real usable $3.397) and
`paid_001` → `account_002` (`APIFY_TOKEN_3`, real usable $0.499), each with
16 GB and 5 run slots free; the $0.011 account unassigned; guard 0.773 →
0.865 ≤ 0.87; preview blocked without keys.

**Run** (code `b6a974b`, clean; one invocation; `--allow-paid` and
`SWEEP_ALLOW_PAID_BENCH=1`; `SWEEP_SEARCH_V2_TELEMETRY=1`,
`SWEEP_PAID_CONCURRENCY=1`, `SWEEP_PAID_WORKERS=2`, `SWEEP_PAID_MULTI_ACCOUNT=1`,
`SWEEP_PAID_ACCOUNT_CAP_USD=0.046`, `SWEEP_PAID_ADAPTIVE_MODE=shadow` in the
child only; sweep budget $0.092; outputs kept in the gitignored
`output/c45-live-canary/`):

| | paid_000 | paid_001 |
|---|---|---|
| account (assigned) | account_000, `APIFY_TOKEN` | account_002, `APIFY_TOKEN_3` |
| account (provider: whose run listing holds the run) | `APIFY_TOKEN` | `APIFY_TOKEN_3` |
| run id | `bJm8mYeqsTQTuCslB` | `4yjIWLtaaR19ILV4n` |
| reserved (both ledgers) → committed | 15:52:25.516 → .519 | 15:52:25.517 → .522 |
| provider start → finish (UTC) | 15:52:26.413 → 15:52:45.280 | 15:52:26.409 → 15:53:33.984 |
| provider-side ceiling (run options) | $0.046 | $0.046 |
| memory | 512 MB | 512 MB |
| status | SUCCEEDED | SUCCEEDED |
| charged events | 15 results + 1 start | 15 results + 1 start |
| settled (run record, final) | $0.03005, 55 s after finish | $0.03005, 67 s after finish |
| account delta at end of window | $0.030072 (residual $0.000022) | $0.030073 (residual $0.000023) |
| rows / eligible / final / final-marginal | 15 / 15 / 15 / 14 | 15 / 14 / 13 / 13 |

- **Separation**: two distinct underlying accounts, by the local assignment
  AND by the provider — each run appears in its own account's run listing
  only; the other five accounts list no run since the probe began. No third
  start, no retry, no migration (VERIFIED).
- **Reservation**: global pending peak $0.092, committed $0.092, released 0,
  blocked 0; each account's pending peak $0.046 and committed $0.046
  (effective capacity $0.046, remaining $0.000); the other five accounts
  committed $0. Both runs' runtime holds freed on `SUCCEEDED`; nothing held
  unknown (VERIFIED from the section and the ledger).
- **Concurrency**: both starts 4 ms apart; overlap at the provider **18.9 s**;
  peak 2 in flight globally, 1 per account; workers 2.
- **Integration**: plan order (`[1/2]` then `[2/2]`); here `paid_001` finished
  last, so plan and finish order agree (the reversed case is offline, §13).
- **Outputs**: CSV and JSON 28 rows each (30 pulled), readable, no provenance
  key, no temp file; `.done_combos` holds exactly the two searches;
  `paid_account_ledger.json` `finished: true`, each unit with its account,
  provider, $0.046 ceiling, reserved/committed times, run id, `SUCCEEDED`,
  `completed`. Adaptive ran as shadow, nothing promoted.
- **Cost**: $0.0601 settled for $0.092 of ceilings; account residual
  ~$0.00002 per run, as every earlier probe (the $0.01 buffer holds 400x).
- **Privacy**: every token value, and every account's raw id, username and
  email (read in memory, never printed), absent from stdout/stderr, the kept
  outputs, telemetry, the durable ledger and all evidence (71 files).

**Canary: PASS.**

## 27. C5 preflight after the canary (MEASURED 2026-09-24, [evidence](search-v2-evidence/c45-c5-preflight-after-canary.json))

Real capacities, **no clamp** (unset in the shell and in the child), full
plan, `--exposed-usd 0.865 --max-usd 11.42`, sweep budget $10.55. Seven
distinct FREE accounts, $15.964 headroom / **$15.894 usable**; **90 of 90
placed**, stranded $0, across five accounts:

| account | slot | usable | projected |
|---|---|---|---|
| account_000 | APIFY_TOKEN | $3.367 | 2 LinkedIn + 24 Indeed ($3.332) |
| account_002 | APIFY_TOKEN_3 | $0.469 | 10 LinkedIn ($0.460) |
| account_003 | APIFY_TOKEN_4 | $0.983 | 6 LinkedIn + 5 Indeed ($0.951) |
| account_005 | APIFY_TOKEN_6 | $1.525 | 11 Indeed ($1.485) |
| account_006 | APIFY_TOKEN_7 | $4.550 | 32 Indeed ($4.320) |
| account_001, account_004 | `_2`, `_5` | $0.011, $4.989 | — |

Every placed account runs 4+ of its heaviest runs at once (workers 2 are
never account-throttled); bounded exposure $10.548 ≤ engine cap $10.55; guard
0.865 → 11.413 ≤ 11.42; preview blocked without keys. **Ready for C5 review.**
(The canary's two charges show in `account_000` and `account_002`'s lower
headroom than at §19.)

