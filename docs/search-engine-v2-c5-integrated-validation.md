# V2-C5 — one integrated live Sweep: 90 paid searches on five accounts, every ceiling held, the result correct, and the paid plan mostly redundant

Date: 2026-09-24. Frozen revision **`8c430ae`** (V2-C4.5 on C4 `22a89a2`; C0 `89c8daa`,
C1 `a3b4bd2`, C2 `429b7b6`, C3 `61681c9`, C3.5 `ccd5720`). The tracked tree was
identical to `8c430ae` throughout; the evidence's `-dirty` stamp is two untracked
non-code files (the operator's own zero-paid `c5-preflight.json` and this stage's
offline analysis tool). No code was changed before or during the run. Nothing was
deployed; no flag was set anywhere but in the developer child.

**C5 status: PASS.** One Sweep, the full default plan, every paid search executed
and correct; no retry, no second run.

Evidence classes: **MEASURED** (this run's readings), **VERIFIED** (read in code or
pinned by a test), **INFERRED**, **UNKNOWN**.

Evidence: [integrated run](search-v2-evidence/c5-integrated-run.json) (the probe,
actor inputs fingerprinted), [final summary](search-v2-evidence/c5-final-summary.json),
[cost settlement](search-v2-evidence/c5-provider-cost-settlement.json),
[adaptive analysis](search-v2-evidence/c5-adaptive-analysis.json), preflights
([operator's](search-v2-evidence/c5-preflight.json),
[immediate](search-v2-evidence/c5-preflight-immediate.json)). Offline analysis:
`bench/search_v2_c5_analysis.py`. Outputs kept in the gitignored `output/c5-run/`.

## 0. Headline

| | |
|---|---|
| Plan | 18 LinkedIn + 72 Indeed = **90 logical searches, 90 starts**, depth 15, no batching |
| Estimate / bounded exposure / engine cap | $6.966 / **$10.548** / **$10.55** |
| Committed | **$10.548** (90 starts; pending peak $0.270; 0 blocked; 0 released) |
| Settled actor cost | **$6.9609** (LinkedIn $0.5409, Indeed $6.42) |
| Account delta / residual | $6.962307 / $0.001407 across five accounts |
| Wall | Sweep **22.0 min**: paid 12.7 min (LinkedIn 5.8, Indeed 6.6), free 8.8 min; result ready at 21.8 min |
| Result | **201 final jobs**: free 146, LinkedIn 35, Indeed 20. Top-10: LinkedIn 7, Indeed 2, free 1 |
| Failures | 0 paid (1 free board, `greenhouse:postman` HTTP 404) |
| 429s / SDK retries | 0 / 3 (Indeed) |

## 1. Preflight and authorization (MEASURED, zero paid)

Immediately before the run: 90/90 placed across seven distinct accounts
($15.894 usable, five used), bounded $10.548 ≤ engine cap $10.55, C0
$0.865 → $11.413 ≤ `--max-usd 11.42`, C5's ledger budget $11.42, every placed
account 4+ Indeed runs at once, 0 active runs anywhere, and
`SWEEP_PAID_ACCOUNT_CAP_USD` unset in the shell and absent from the child.
Preview blocked without keys. Then one invocation of
`bench/search_v2_paid_probe.py --allow-paid --full-plan --stage C5
--multi-account --paid-workers 2 --adaptive-mode shadow --sweep-budget 10.55
--exposed-usd 0.865 --max-usd 11.42` with `SWEEP_ALLOW_PAID_BENCH=1`. Child
flags: telemetry 1, C2 1 / workers 2, pool 1, adaptive shadow, free shadow 1,
Lever 1/4, Greenhouse 1/4, results-ready early 1.

## 2. Accounts and allocation (MEASURED; VERIFIED against the provider)

| account | slot | real usable | projected = actual | committed | peak in flight | peak memory |
|---|---|---|---|---|---|---|
| account_000 | APIFY_TOKEN | $3.367 | 2 LI + 24 IN | $3.332 | 2 | 8192 MB |
| account_002 | APIFY_TOKEN_3 | $0.469 | 10 LI | $0.460 | 2 | 1024 MB |
| account_003 | APIFY_TOKEN_4 | $0.983 | 6 LI + 5 IN | $0.951 | 2 | 8192 MB |
| account_005 | APIFY_TOKEN_6 | $1.525 | 11 IN | $1.485 | 2 | 8192 MB |
| account_006 | APIFY_TOKEN_7 | $4.550 | 32 IN | $4.320 | 2 | 8192 MB |
| account_001 / account_004 | `_2` / `_5` | $0.011 / $4.989 | — | $0 | 0 | 0 |

Projected placement = actual starts on every account. Each account's run
listing on the provider holds exactly its own runs (26, 10, 11, 11, 32; `_2`
and `_5` none): **no unexpected run, no second account per search, no third
party**. 6 account switches between consecutive starts. No account ever held
a run whose end was unconfirmed. **Head-of-line account waits: none** — with
two workers, no account exceeded 2 runs / 8,192 MB of its 5 runs / 16,384 MB,
so the pool never had to wait (VERIFIED by construction and by the peaks).

## 3. Reservations (VERIFIED)

Global: committed rose monotonically to **$10.548**, pending peaked at $0.270
(two Indeed ceilings), every reservation preceded its commit, and no commit
was observed after its provider start. Per account, committed = projected, and
never above its capacity (remaining $0.009–$0.040 on the busy accounts). The
account deltas the engine read ($5.92 at the end of the paid phase, lagging)
never re-opened anything.

## 4. Concurrency and timing (MEASURED)

| | LinkedIn | Indeed |
|---|---|---|
| workers / peak at provider | 2 / 2 | 2 / 2 |
| segment wall | 348 s | 398 s |
| unit wall median / p95 / max | 36.4 / 65.3 / 78.9 s | 8.9 / 21.2 / 33.1 s |
| provider runtime median / p95 / max | 27.1 / 52.0 / 73.7 s | 4.1 / 8.1 / 14.9 s |
| provider run-seconds / segment seconds | 577 / 342 (1.7x) | 353 / 394 |
| buffered (finished before the head) | 13 units, max 45.8 s | 50 units, max 25.4 s |
| SDK calls / requests / 429 | 168 / 168 / 0 | 298 / 301 / 0 |
| **poll detection delay** median / p95 / max | **2.4 / 5.1 / 6.3 s** | **1.7 / 4.5 / 19.4 s** |

Two 4,096 MB Indeed runs overlapped on one FREE account with no refusal.
Finish order ran against plan order 10 times; integration, the log and the done
ledger stayed in strict plan order. Checkpoints: 90 passes, 8.1 s wall, 8.0 s
CPU (score-once, C2). Free phase 527.8 s (223/224 sources; Postman 404).
Result-ready marker written after the CSV and JSON, **10.15 s** before the
process finished; the post-result tail (shadow tranche, 8 boards; adaptive;
identity; telemetry) changed no output.

## 5. Cost settlement (MEASURED)

69 Indeed runs settled at $0.09 (15 `result` events), three short ones at
$0.078, $0.072 and $0.06 (13, 12 and 10 rows — the provider had fewer; not a
failure); every LinkedIn run $0.03005 (15 items + 1 start). The terminal poll
read $0.3729 (LinkedIn) and $0.336 (Indeed) — provisional, far below the settled
figures, as C1 found. All 90 runs were final at the first post-run read; that
read came ~593 s after the last paid run finished (the free phase ran between),
so time-to-settle here is an upper bound only — the canary measured 55–67 s.
Account residual beyond the runs: $0.000183–$0.000463 per account (~$0.000015
a run); the $0.01 per-account buffer held ~20x the largest.

| | |
|---|---|
| estimated cost | $6.966 |
| authorization exposure = committed | $10.548 |
| generated hard cap | $10.55 |
| settled actor cost | **$6.9609** |
| account usage delta | $6.962307 |
| residual platform usage | $0.001407 |

## 6. Output correctness (VERIFIED)

CSV 201 = JSON 201 = the record's final rows; no `_`-prefixed key in the
output; no temp file; `seen.tsv` 201; `.done_combos` 90 unique lines, one per
completed search, in plan order; `paid_account_ledger.json` finished, 90
committed / 90 SUCCEEDED / 90 completed. Every start carried one search
(LinkedIn: one URL; Indeed: one position, no `startUrls`) — **no batching**.
No token, raw account id, username, email, job content, query or URL in any
committed evidence (scanned).

## 7. What the paid plan contributed (MEASURED)

| | LinkedIn | Indeed |
|---|---|---|
| requested / returned | 270 / 270 | 1,080 / 1,070 |
| stale | 0 | 33 |
| duplicate within/across paid searches | 162 | **644** |
| eligible / positive | 97 / 96 | 31 / 27 |
| final / final positive / final-marginal | 35 / 34 / 8 | 20 / 16 / 14 |
| top-10 / top-20 / top-50 of the result | **7 / 15 / 26** | 2 / 3 / 10 |
| settled cost | $0.54 | **$6.42** |
| per final job | $0.015 | $0.32 |

- **76 of 90 searches added no final-marginal job**; 10 more added one or two
  low-ranked ones. Skipping any single search alone would move the top-10 only
  for `paid_005`, `paid_007` (LinkedIn Remote twins) and `paid_038` (Indeed).
- **Paid vs free**: no paid row lost dedupe to a free row (paid integrates
  first); 12 Indeed rows and 0 LinkedIn rows share a key with free inventory.
  Free supplied 146 of 201 finals but only 1 of the top 10.
- Indeed's eight India locations largely return the same postings (644 of
  1,070 rows duplicate another paid search) and few survive eligibility (31).
  Recorded for V2-D, not acted on.

## 8. LinkedIn provider position (MEASURED; `provider_position`, not push order)

| position | rows | eligible | final | final-marginal | top-10 kept by a prefix |
|---|---|---|---|---|---|
| 1–5 | 90 | 36 | 11 | 1 | 0 (depth 5 keeps 0 of LinkedIn's 7 top-10) |
| 6–10 | 90 | 29 | 11 | 4 | 2 (depth 10 keeps 2) |
| 11–15 | 90 | 32 | 13 | 3 | 7 (depth 15) |

Five of LinkedIn's seven top-10 jobs sit at positions 11–15. **No depth
reduction is supported.** Indeed depth: **UNKNOWN** (no provider position).

## 9. Structural pairs (MEASURED)

- **LinkedIn India/Remote (9 pairs)**: Remote twins 91 eligible, 6 shared with
  their India twin (6.6%); skipping all nine would lose 8 final jobs, **2 top-10**,
  2 top-20, for $0.414 of exposure.
- **Indeed Remote vs its seven places (9)**: Remote twins 23 eligible, 0 shared;
  skipping all nine would lose 10 final jobs (best rank 21), 0 top-20, for
  $1.215 of exposure.

## 10. Adaptive shadow — every frozen C4 policy (MEASURED; `c4.1`, PROMOTED empty)

Savings are exposure and starts avoided; losses are against the complete final
result, Free included. Online decisions used only decision-time state (C4).

| policy | fires after | starts avoided | exposure avoided | final lost | top-10 lost | top-20 lost | best lost rank |
|---|---|---|---|---|---|---|---|
| marginal_streak n=2 | paid_003 | 86 | $10.364 | 45 | **7** | 14 | 1 |
| marginal_streak n=3 | paid_004 | 85 | $10.318 | 45 | **7** | 14 | 1 |
| marginal_streak n=5 | paid_020 | 69 | $9.315 | 19 | 2 | 3 | 1 |
| top_k_stable 10/3 | paid_004 | 85 | $10.318 | 45 | **7** | 14 | 1 |
| top_k_stable 20/3 | paid_004 | 85 | $10.318 | 45 | **7** | 14 | 1 |
| top_k_stable 20/5 | paid_016 | 73 | $9.766 | 19 | 2 | 3 | 1 |
| provider_marginal_streak n=3 | paid_004 | 82 | $9.913 | 45 | **7** | 14 | 1 |
| provider_marginal_streak n=5 | paid_022 | 67 | $9.045 | 19 | 2 | 3 | 1 |
| remote_after_places m=1 | paid_003 | 7 | $0.322 | 22 | 4 | 9 | 2 |
| remote_after_places m=2 | paid_017 | 0 | $0 | 0 | 0 | 0 | — |
| depth_prefix 5/3 (LinkedIn) | paid_004 | 0 (depth) | $0.540 | 14 | 5 | 8 | 2 |
| depth_prefix 10/3 (LinkedIn) | paid_004 | 0 (depth) | $0.270 | 6 | 3 | 5 | 2 |

Every policy that saves anything loses top-10 jobs; the streak and top-K
families would lose the **#1** job. **Most promising: none.** Nothing promoted.
Record size: section 120,538 bytes compact / 190,219 indented; the whole
telemetry file 727,362 bytes.

## 11. Recommendations

- **Public C2: GO**, at **2 workers**, on the evidence: full plan authorized,
  no invariant broken, ceilings on every start, plan-order integration under
  reversed completion, 0 429s, two Indeed runs on one FREE account without
  refusal, result and checkpoints correct, paid wall 12.7 min. Condition: the
  public path is ONE account (the visitor's) — the pool is developer-only — and
  whether the public app stops a plan whose $10.55 cap exceeds a FREE account's
  $5 limit is **UNKNOWN**; verify before enabling.
- **Adaptive: OFF** for production. No candidate is close to lossless; the
  actionable redundancy (Indeed locations) is visible in C1's funnels without
  it; the section adds ~120 KB and CPU per sweep for decisions none of which
  would pass the gate. Keep the code and use shadow in developer runs.
- **Workers: 2.** Nothing here measures 3 or 4 live.
- **Hard cap: correct.** The generated $10.55 held the plan's $10.548 of
  ceilings exactly; the estimate ($6.966) is likely spend and landed within
  $0.006 of the settled $6.9609. Three different numbers, as designed.
- **Polling follow-up: YES** (V2-D, small). Indeed runs take 4.1 s median at the
  provider but 8.9 s per unit; detection is 1.7 s median, 4.5 s p95, 19.4 s max
  on a 5 s poll.
- **Multi-account**: worked as designed; developer/C5 infrastructure only.

## 12. Unresolved risks

1. The public single-account cap vs a FREE account's $5 limit (§11) — UNKNOWN.
2. One synthetic default profile; contribution and redundancy are this plan's.
3. The 19.4 s Indeed detection outlier is unexplained (INFERRED: one of the 3
   SDK retries).
4. Time-to-settle at scale is an upper bound only (§5).
5. The free phase (8.8 min) now dominates less than paid (12.7 min) but more
   than either provider segment.

## 13. Research ledger

C5 entry: 90 logical searches, 90 starts, 5 accounts, **$10.548 intended,
$6.9609 settled**. Cumulative **$11.413 intended / $7.53135 actual**, within C5's
separate $11.42 (outside the C1–C4 $2.00).

## 14. Readiness for V2-D

Ready. C5 measured; nothing was tuned. V2-D inputs: public C2 at 2 workers
(after the single-account credit check), adaptive off, polling, Indeed
location redundancy, and the free phase's wall.

## 15. Verification (local only, after the run)

C4.5, C0, C1, C2 suites (182 tests OK) with two tests corrected to stop
assuming the ledger's pre-C5 totals; the analysis tool over the kept outputs;
output, ledger, telemetry and privacy checks above. No provider was rerun.
