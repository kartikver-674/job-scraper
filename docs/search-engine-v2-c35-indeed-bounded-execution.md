# V2-C3.5 — an Indeed start is bounded at the provider, so C2 can hold it and overlap it

Date: 2026-09-24. Baseline: `61681c9` (V2-C3 = `origin/main`; C0 `89c8daa`, C1
`a3b4bd2`, C2 `429b7b6`). Production runs V2-B5 with `SWEEP_SEARCH_V2_TELEMETRY=1`,
Lever 1/4, Greenhouse 1/4, `SWEEP_FREE_SOURCE_SHADOW=1` and
`SWEEP_RESULTS_READY_EARLY=1`. None of those is touched here.
Scope: **Indeed provider-side cost safety, Indeed reservation support and bounded
Indeed concurrency.** One logical search is still one actor start. There is no
batching, no pruning, no depth change and no reordering. No flag was added and
nothing was deployed.

**Outcome: A — Indeed is safely bounded.** The whole production change is one
entry in `scraper.ACTOR_CHARGE_MODEL`. From it, every Indeed start carries a
provider-enforced `maxTotalChargeUsd` of **$0.135** at the default depth, whether
or not `SWEEP_PAID_CONCURRENCY` is on (V2-B1's rule, now covering Indeed). The
same entry makes C2 classify Indeed as bounded, so under the flag it reserves
Indeed's full ceiling and runs up to `SWEEP_PAID_WORKERS` Indeed searches at once.
The actor input is byte-identical to before.

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a dated
probe), **VERIFIED** (directly observed code, configuration, provider record or
provider documentation), **INFERRED** (a model with stated assumptions), and
**UNKNOWN** (evidence unavailable, which is not the same as zero).

## 0. Headline findings

1. **VERIFIED — Indeed's price is one custom event and nothing else.**
   `misceres/indeed-scraper` has been PAY_PER_EVENT since 2026-03-26 with a
   single event, `result` ("Job listing — Cost per every job listing
   returned"). It costs $0.006 on the FREE tier, down to $0.001 on DIAMOND.
   There is no start event and no `minimalMaxTotalChargeUsd`. Platform usage is
   "Included" on every plan (`isUserPayingForPlatformUsage: false`) (§2).
2. **VERIFIED — the ceiling rests on Apify, not on the actor counting right.**
   `result` is charged by the actor's own code. Apify's rule is what bounds it:
   a user is "never charged for produced events over the defined limit", and the
   platform stops charging, stops `pushData` and aborts the run there (§2.2).
3. **MEASURED live, Phase 1 (one start, serial — production's path).** Apify
   accepted and recorded `maxTotalChargeUsd` 0.135
   (`isMaxTotalChargeUsdSetByUser: true`). The actor received
   `maxItemsPerSearch: 15` and returned **15 rows**. It charged `result` × 15
   and nothing else. The run settled at **$0.09 = 15 × $0.006 exactly**, with
   `platform_usage_billing_model: DEVELOPER`, 4096 MB, build 0.0.111 and a
   **4.1 s** actor runtime (§10).
4. **MEASURED live, Phase 2 (two starts at once through C2).** The account took
   two 4 GB runs simultaneously: they started 62 ms apart and overlapped 4.85 s.
   Each carried and recorded $0.135 and returned 15/15. There were 0 HTTP 429s
   and 0 SDK retries. C2 reserved **$0.270** before either start was sent,
   committed each hold the instant before its start request, and held it to the
   end of the sweep, while every production reading said $0.00. Each run
   settled at $0.09, 18.5 s after it finished (§11).
5. **Formula.** `ceil₀.₀₀₁(depth × $0.006 × 1.5)`, floored at the provider
   minimum ($0, none), in `Decimal` end to end. That is **$0.135 at depth 15**:
   22 listings fit, the 23rd would not. It is B1's formula with Indeed's numbers
   and B1's 1.5× headroom. The headroom is there because the actor's own
   changelog records past overshoots of `maxItemsPerSearch` (§3).
6. **VERIFIED — the input did not move.** `run_input` is the pre-C3.5 object,
   key order included. The ceiling is a start argument. Serially, the only
   difference in the request log is the start's ceiling, and the CSV, JSON, seen
   ledger, done ledger, funnel and trace are byte-identical to the pre-C3.5
   serial path (§4).
7. **MEASURED offline — deterministic at every width.** At 1, 2, 3 and 4
   workers the outputs match serial byte for byte, including the Indeed
   duplicate tie where the later search finishes first. Committed exposure is
   one shared view across LinkedIn and Indeed (§6–§8).
8. **MEASURED (offline, provider time scaled 1:10, Indeed calibrated on the
   three live runs).** On the default 90-search plan, C3.5 against C2 before it
   is **1.55× faster at 2 workers, 2.08× at 3 and 2.53× at 4, and equal at 1**.
   Indeed's segment divides by the width, and outputs and the done ledger are
   identical in all nine arms (§12). This is an offline modelled improvement, not
   a production speed-up.
9. **VERIFIED arithmetic — a product consequence.** The public app caps a sweep
   at 1.25 × its estimate. That prices an Indeed search at $0.1125 against a
   $0.135 ceiling. With C2 on, the default plan would stop at 76 of 90 searches
   (Indeed 58 of 72), the public `india` scope at 20 of 24 and `global` at 31 of
   36. **C2 stays off the public worker** until the cap holds ceilings (§13).
   Production (flag off) admits exactly what it did before.
10. **MEASURED — 16 targeted mutations, all caught.** They include the brief's
    A–N, and every mutated file was restored byte-identical (§15).
11. **Spend.** C3.5 made **3 Indeed starts: $0.405 intended, $0.27 actual.** The
    ledger reads **$0.773 intended / $0.51035 actual** of the shared $2.00 (§16).

## 1. What motivated this stage

C3 (§12) read Indeed's store record and found it pay-per-event, so it could in
principle take a `maxTotalChargeUsd`, which B1 and C2 had recorded as
impossible. C3 inferred $0.135 and did not validate it. Meanwhile C2 ran Indeed
one search at a time, unreserved, because it had no ceiling. In the repository
default plan (18 LinkedIn + 72 Indeed) that serial Indeed half is 80% of the
starts and the part of the paid phase C2 could not overlap. This stage re-read
the provider record, did not trust C3's number, and changed production code only
after the provider accepted, recorded and billed within the ceiling live.

## 2. The current Indeed contract — audit

Read on 2026-09-24 from the actor's public record (free, unauthenticated GETs of
`/v2/acts/misceres~indeed-scraper`, re-read at 07:55:19 UTC immediately before
Phase 1, and of its latest build `QCL4zRJXp3sSD0xkl`), its store pricing page,
and Apify's documentation. Details:
[`c35-indeed-provider-contract.json`](search-v2-evidence/c35-indeed-provider-contract.json).

| Question | Answer | Class |
|---|---|---|
| actor, build | `misceres/indeed-scraper` (id `hMvNSpz3JnHgl5jkh`, "Maintained by Apify"); latest **0.0.111**, built 2026-09-22; Sweep runs `latest` (unpinned) | VERIFIED; the build ran in all 3 canary runs (MEASURED) |
| pricing model | PAY_PER_EVENT since 2026-03-26 (before that: PRICE_PER_DATASET_ITEM $0.005 from 2023, and six PPE drafts 2026-03-16/17) | VERIFIED |
| price per result | event `result`, "Job listing": FREE **$0.006**, BRONZE $0.005, SILVER $0.004, GOLD $0.003, PLATINUM $0.002, DIAMOND $0.001; store page "$6.00 / 1,000" on Free | VERIFIED; this account pays **$0.006** (the run record's applied `event_price_usd`, MEASURED) |
| actor-start fee | none: no `apify-actor-start` in the pricing entry, none charged | VERIFIED; MEASURED on 3 runs |
| other billed events | none: the entry has exactly one event | VERIFIED |
| `minimalMaxTotalChargeUsd` | `null` (no provider minimum) | VERIFIED (actor record and each run's pricing info) |
| platform usage | "Included" on every plan; `isUserPayingForPlatformUsage: false`; each run's `platform_usage_billing_model: DEVELOPER`, and `usage_total_usd` equals the events alone | VERIFIED; MEASURED |
| `maxTotalChargeUsd` supported | yes: Apify enforces it for every PPE actor (below). Every run recorded 0.135 with `isMaxTotalChargeUsdSetByUser: true` | VERIFIED (docs); MEASURED (3/3 run records) |
| `maxItemsPerSearch` | "Maximum number of job listings to scrape for each keyword search and each Start URL"; Sweep sends 15; the provider-recorded input says 15 | VERIFIED |
| a hard row bound? | 15 requested → 15 returned, charged 15, in 3 of 3 runs. Historically not always: the changelog records "Fixed slight overflows for max items limits" (2025-07-11) and "Fixed `maxItemsPerSearch` exceeding the specified limit per start URL" (2026-01-15) | MEASURED for build 0.0.111 (n=3); a guarantee is not claimed. The ceiling is the hard bound |
| billable events unrelated to rows | none priced. The README says a search that finds nothing pushes a `FOUND_NO_RESULTS` error item; whether that item is charged is not stated | VERIFIED / charge UNKNOWN (bounded by the ceiling either way) |
| retries / pagination beyond depth | internal, not exposed; any overshoot is billable only up to the ceiling | INFERRED |
| failed / aborted runs | events already charged stand, never above the ceiling; at the ceiling Apify aborts the run | VERIFIED (docs) |
| optional paid features in Sweep's input | none. `parseCompanyDetails: false` (and "costs no extra requests" anyway), `followApplyRedirects: false` ("Has no effect"), `saveOnlyUniqueItems: true` (a filter); `startUrls` not sent | VERIFIED |
| default memory | 4096 MB (actor maximum 4096); `timeoutSecs` is Sweep's 300 | VERIFIED; MEASURED |
| account limits (`APIFY_TOKEN`) | 16 GB actor RAM, 5 concurrent runs, 0 in use before each canary; Apify Free plan: 16 GB and 5 runs | MEASURED; VERIFIED (apify.com/pricing) |

### 2.1 One logical search, one start — unchanged

`position` is a single string. `startUrls` would let one run carry several
searches, but that is a different contract, and `saveOnlyUniqueItems: true`
would raise exactly the cross-search dedupe and attribution risk that made C3
reject LinkedIn batching. Neither is used; nothing batches.

### 2.2 Where the bound actually comes from

`result` is a **custom** event, not the synthetic `apify-default-dataset-item`.
The actor's own code decides when to call `Actor.charge()`. Its counting is
therefore not what C3.5 relies on. Apify's documentation is:

- Store actors, pay per event: "When starting a run, you can define a maximum
  charge limit. The Actor terminates gracefully when it reaches that limit - and
  even if it does not stop immediately, **you are never charged for produced
  events over the defined limit**."
- PPE developers, respect user spending limits: "They won't be billed beyond the
  limit. The Apify platform enforces this limit. Once it's reached,
  `Actor.charge()` stop charging and `Actor.pushData()` stops pushing data over
  the limit. The platform then aborts the run automatically."
- The charge API, status 201: "Above the limit, the charges reported as
  successful in API will not be added to your payouts, but you will still bear
  the associated costs." (The developer bears them, not the user.)

So even a build that miscounts or ignores `maxItemsPerSearch` cannot bill this
account more than the ceiling for one run. That is the property B1 established
for LinkedIn, and it is the only reason Indeed may now enter C2's bounded pool.

### 2.3 What the ceiling does not cover

- **Post-run dataset reads.** "After a run finishes, any interactions with the
  dataset … incur standard platform usage costs" (Apify docs). This is the small
  account residual C1 measured for LinkedIn: here +$0.000017 and +$0.000034 over
  the runs' charge within 4–8 minutes. It is not a start's cost, and C2 already
  folds it in through `observed` once an account read shows it.
- **Other consumers of the same account.** Unchanged from C2 §8.

## 3. The charge model

```python
"indeed": {
    "result_usd": Decimal("0.006"),      # FREE tier, the dearest published
    "start_usd": Decimal("0"),           # no start event is priced
    "start_events": 0,
    "overshoot": Decimal("1.5"),
    "provider_minimum_usd": Decimal("0"),       # minimalMaxTotalChargeUsd: null
},
```

It is an entry in B1's existing table, read by B1's existing `max_charge_usd`:

```
ceiling = ceil_to_0.001( depth × result_usd × overshoot + start_events × start_usd )
ceiling = max(ceiling, provider_minimum_usd)
```

| Term | Value | Why |
|---|---|---|
| `result_usd` | $0.006 | The FREE tier, the dearest published, and the one this account pays (MEASURED). A paid tier only adds headroom. |
| `overshoot` | 1.5 | Kept from B1, for B1's reason. The provider aborts a run at its ceiling, and `scrape_search` discards a non-`SUCCEEDED` run's dataset. A ceiling at exactly the depth would turn one overshooting row, which this actor's changelog says has happened, into a lost search. Not reduced because the three live runs charged exactly 15: observed cost being lower is no reason to cut safety headroom. |
| `start_events`, `start_usd` | 0, $0 | No start event is priced. If the developer adds one later (Apify recommends `apify-actor-start`), at 4096 MB it is 4 × $0.00005 = $0.0002, which comes out of the $0.045 headroom. |
| rounding | up to $0.001 | B1's step. 0.135 needs no rounding. |
| `provider_minimum_usd` | $0 | `minimalMaxTotalChargeUsd` is null, so no minimum constrains the formula. |

| Depth | Ceiling | Honest charge (FREE) | Listings that fit |
|---:|---:|---:|---:|
| 1 | $0.009 | $0.006 | 1 |
| 10 | $0.090 | $0.060 | 15 |
| **15 (default)** | **$0.135** | **$0.090** | **22** |
| 25 | $0.225 | $0.150 | 37 |
| 50 | $0.450 | $0.300 | 75 |

**Decimal.** The prices are `Decimal` literals, the arithmetic is `Decimal`, the
start receives a `Decimal`, `PaidExposure` holds `Decimal`, and telemetry
records the decimal string ("0.135"). A float mutation is caught (§15, B).

**Naukri is still absent.** Its pricing changed on 2026-09-11 (PPE: start
$0.001, `job_item` $0.0015, `job_item_detailed` $0.003, minimum $0.10), but it
is disabled by default and no run has measured it. It stays unbounded: serial
under C2, refused under a `--max-usd` (§17).

## 4. Serial provider safety — the B1 architecture, applied to Indeed

**Decision: the ceiling protects every Indeed start, serial or concurrent.** B1
made LinkedIn's ceiling a property of the run, not of the scheduler:
`scrape_search` applies it wherever `max_charge_usd` returns a value, with the
fail-closed SDK check before it. Indeed now takes that same path, so the choice
here was only whether to special-case Indeed out of the serial loop. That would
be a second mechanism, and it would leave production — which runs the serial
loop — with an unbounded Indeed run for no benefit. So there is one rule: B1
decides whether a run is bounded, and C2's flag decides reservation and
concurrency.

**What changes in production once deployed** (flag off, the serial loop):

| | Before C3.5 | After |
|---|---|---|
| Indeed `run_input` | the literal object | **identical** (object and bytes; pinned) |
| start arguments | `run_input`, `run_timeout` | + `max_total_charge_usd=Decimal("0.135")` |
| an honest run (≤ 22 listings) | its rows | **the same rows**, the same charge |
| a runaway run (the actor ignores its depth) | unbounded: every listing billed and kept | aborted by Apify at $0.135 (22 listings), so the search fails and its partial rows are discarded, as for any non-`SUCCEEDED` run (B1 UNKNOWN #5) |
| an apify-client without the parameter | ran unbounded | the search fails closed, nothing started. `requirements.txt` has pinned `apify-client>=3.1.0` since B1, and LinkedIn already needs it in production |
| the budget guard, plan, estimate, polling | — | unchanged |

**Proof that intended results do not change** (`SerialProviderSafety`, offline).
The same Indeed plan (three searches, a twin posting and a free source) runs
three ways: the pre-C3.5 serial path (the charge model removed), the C3.5
serial path, and C2 at one worker. The request logs are equal once each start's
ceiling element is masked. CSV, JSON, the seen ledger, the done ledger and every
unit's funnel and trace are byte-identical across all three. C2 at one worker
makes the serial loop's requests in the serial order, and its C1 record matches
except for the clocks.

**Fail closed.** Unchanged code, now reached by Indeed. If
`charge_ceiling_supported(actor)` is false, `scrape_search` raises before the
start. Serially that is one failed search. Under C2 the pending hold is
released, because nothing was sent (C2 §4). Nothing is started unbounded.
Pinned for Indeed specifically: `StartContract` and
`FailuresKeepTheirCeiling.test_an_sdk_without_the_ceiling_starts_nothing_and_holds_nothing`.

## 5. C2 classification — no new flag

`paid_phase_c2` has always classified a site as bounded when every search on it
has a ceiling (`bounded = all(e.ceiling is not None …)`) and given it
`SWEEP_PAID_WORKERS` width. With the model entry Indeed is bounded, and nothing
in the scheduler changed.

| Provider | Charge model | Under `SWEEP_PAID_CONCURRENCY=1` |
|---|---|---|
| LinkedIn | B1, $0.046 | bounded: reserved, up to `SWEEP_PAID_WORKERS` at once |
| **Indeed** | **C3.5, $0.135** | **bounded: reserved, up to `SWEEP_PAID_WORKERS` at once** |
| Naukri | none | unbounded: one at a time, admitted on the old test, no number invented |

A separate Indeed-concurrency flag was not added. The reservation must run
wherever concurrency can, one worker is the serial case of the same code (C2
§17), and the provider-side ceiling is not something to switch off. The
existing switch is enough. `SWEEP_PAID_CONCURRENCY=0` is the serial loop,
`=1` is the C2 scheduler, and no deployment file sets either (pinned; mutation N).

## 6. Reservation — the same invariant, holding $0.135

Nothing in `PaidExposure` changed. For each Indeed start:

```
coordinator   reserve(unit, 0.135)   admit  ⟺  view + 0.135 <= budget   (Decimal)
worker        commit(unit)           the instant before actor.start
              actor.start(run_input, run_timeout, max_total_charge_usd=0.135)
afterwards    committed never falls for the rest of the sweep
```

The reserved number is the number the start carries (`Reservation`, mutation
E). Pinned end to end through `scraper.main()` at 1, 2 and 4 workers with an
account that never moves (C1's lag), so no observed reading can make room:

| Budget | Indeed starts | Why |
|---:|---:|---|
| $0.134 | 0 | 0.135 > 0.134 |
| $0.135 | 1 | exact equality fits |
| $0.269 | 1 | a tenth of a cent short of two |
| $0.270 | 2 | "spend cap $0.27: $0.270 held + the next start's $0.135 ceiling would exceed it" |
| none | all | held ($0.540 for four), never blocking |

The old guard, under the same $0.135 cap and the same lagging account, started
all three.

**Nothing after the start hands the ceiling back.** These are pinned
(`FailuresKeepTheirCeiling`: two searches, one worker, a budget with room for
one ceiling). In every case the first search's hold stays `committed` at 0.135
and the second is refused:

| First search | Status | Hold |
|---|---|---|
| provider `FAILED` | failed | committed |
| provider `TIMED-OUT` | failed | committed |
| provider `ABORTED` (what Apify does at the ceiling) | failed | committed |
| engine deadline → abort sent | failed | committed |
| `actor.start` raised, no run id (ambiguous) | failed | committed |
| the provider rejected the ceiling (start raised) | failed | committed |
| dataset read failed | failed | committed |
| account read failed (run-record fallback) | completed | committed |
| zero rows | completed | committed |
| the actor's `FOUND_NO_RESULTS` error item | completed (1 raw row, 0 final) | committed |
| the full depth returned (15 rows) | completed | committed |
| a run that settled at $0.00005 | completed | committed |
| an SDK without `max_total_charge_usd` | failed, nothing sent | **released before network** (the only release) |

## 7. LinkedIn and Indeed — one budget, one view

There is one `PaidExposure` per sweep. Sites run as segments in `SITES` order
(LinkedIn, Indeed, Naukri), so a plan "LinkedIn, Indeed, LinkedIn, Indeed"
cannot interleave. Each segment's decisions see every ceiling committed before
it:

- $0.30 budget, LinkedIn first. Two LinkedIn holds ($0.092), Indeed #1 fits
  ($0.227), and Indeed #2 would make $0.362, so it is refused. The account
  never moved.
- $0.30, Indeed first (a profile can order it so). Two Indeed holds ($0.270),
  then LinkedIn #1 ($0.316) is refused.
- Unbudgeted, 2 + 2: committed $0.362 over 4 starts. Both segments are bounded
  and 4 wide.
- Funded mixed plan with a free source, at 1, 2, 3 and 4 workers: byte-identical
  to serial.

## 8. Concurrency

**Worker count.** `SWEEP_PAID_WORKERS` is unchanged: default 2, clamped 1..4.
No provider-specific cap was added, because the evidence does not call for one:

- Memory. Each Indeed run is 4096 MB, and this account's limit is 16 GB (the
  Free plan's), so 4 fit at once, exactly `PAID_WORKERS_MAX`. Two use 8 GB,
  which the provider accepted live (§11). At four there is **no headroom**: any
  other run or build on the account then makes the fourth start fail. Apify's
  rule is that "the user cannot start a new Actor". C2 counts such a start as
  committed and fails that search, which costs coverage, never money
  (INFERRED; not triggered live).
- Runs. The Free plan allows 5 concurrent runs; 4 workers use 4.
- LinkedIn and Indeed segments never overlap, so their memory does not add.

**Deterministic merge — unchanged.** Results wait by plan position and only the
head is integrated. Four Indeed searches returning one twin posting, the first
planned finishing last (dataset order `run_4, run_3, run_2, run_1` at four
workers), give the same CSV, JSON, seen and done ledgers at serial and 1–4
workers. The twin that survives is the first planned one's
(`…viewjob?jk=90`, `K0 Engineer @ Bengaluru`). Integrating in completion order
is caught (mutation K).

**Client per search — unchanged, now pinned for Indeed.** Every search builds
its own apify-client on the chosen token; the coordinator keeps one for account
reads. `ClientIsolation` wraps every client construction: each client is used by
exactly one thread, and there are as many search clients as starts. Handing
every worker the coordinator's client is caught (mutation J).

**Account and token behaviour — unchanged.** One account per sweep, chosen once
by `_require_token` before any client exists (C2 §7). Indeed workers get the
chosen token through the same closure, and no token value reaches telemetry,
logs or evidence (checked by value on both canaries).

## 9. Telemetry

No telemetry code changed. The additions are values that follow from the model:

- **C1.** `paid_units[].charge_ceiling_usd` is "0.135" for Indeed.
  `units[].max_total_charge_usd` and the provider's own `provider_ceiling_usd`
  are 0.135. `paid_summary.planned_bounded_exposure_usd` includes Indeed, and
  `planned_unbounded_units` now counts Naukri alone. `cost_observations` keep
  their three sources and their finality table, and a ceiling is never one of
  them.
- **C2.** `paid_execution.segments[]` shows Indeed `bounded: true` at the worker
  count. Each Indeed unit shows `reservation: committed`, `reserved_usd: "0.135"`
  and its own `sdk` counters (calls, requests, 429s).
- The ceiling is authorisation state, never spend. The canaries show the gap:
  the terminal-poll readings counted 0–5 of 15 results and the account delta at
  the engine's read was $0.00, against $0.09 settled per run.

## 10. Phase 1 — the live contract canary (MEASURED)

`SWEEP_ALLOW_PAID_BENCH=1 .venv/bin/python -m bench.search_v2_paid_probe --allow-paid
--max-usd 0.51 --exposed-usd 0.368 --site indeed --keywords "Software Engineer"
--location Bengaluru --searches 1 --stage C3.5 …` at 2026-09-24 07:55:51 UTC.
[Evidence](search-v2-evidence/c35-indeed-contract.json).

**Before the call.** Preconditions:

- The full sweep suite (1,381) and the deploy tests (42) were green.
- The provider record was re-read at 07:55:19 UTC: build 0.0.111, pricing
  unchanged.
- The input was checked offline from the probe's exact profile through the
  guard's dry run:
  `{"position": "Software Engineer", "location": "Bengaluru", "country": "IN",
  "maxItemsPerSearch": 15, "parseCompanyDetails": false,
  "saveOnlyUniqueItems": true, "followApplyRedirects": false}`, with 1 search,
  depth 15 and no free source.
- The zero-cost preview printed `indeed misceres/indeed-scraper 1 start(s) depth
  15 ceiling $0.135 per start, provider-enforced / worst case this run: $0.135 /
  exposure before: $0.368 after: $0.503 limit (--max-usd): $0.51` and stopped
  with neither key.

The pricing source is the provider record above: $0.006 × 15 × 1.5 = $0.135.
Intended exposure was $0.135, moving the ledger from $0.368 to $0.503, which is
≤ $2.00. `--max-usd 0.51` refuses a second start. The shape is generic and
synthetic, under the default config, with no résumé. It is the same shape as
C0's LinkedIn contract check.

| | Sweep's side (telemetry) | Apify's side (run record) |
|---|---|---|
| run | `vqb8iBDFe9hw1qbXx`, build id `QCL4zRJXp3sSD0xkl` | SUCCEEDED, build 0.0.111 |
| ceiling | started with `max_total_charge_usd` 0.135; the start response's `provider_ceiling_usd` 0.135 | `options.maxTotalChargeUsd` **0.135**, `isMaxTotalChargeUsdSetByUser: true` |
| input | built by `build_input`, unchanged | the INPUT record: the same seven fields, `maxItemsPerSearch: 15` |
| rows | requested 15, raw 15 | 15 |
| charged | terminal poll: `result` × 5, $0.00; account delta $0.00 | settled **`result` × 15 = $0.09** at +18.1 s (unchanged over 5 re-reads); applied `event_price_usd` 0.006; no other event |
| platform usage | — | `platform_usage_billing_model: DEVELOPER`; `usage_usd` null; 0.0046 CU borne by the developer |
| resources | — | 4096 MB, 8192 MB disk, timeout 300 s |
| time | unit 7.29 s = start 0.79 + wait 5.32 (1 poll) + dataset 0.81 + account 0.28 + checkpoint 0.09; tool wall 10.2 s | actor runtime **4.13 s** |
| account | limits before: 16 GB RAM, 5 concurrent runs, 0 in use | covered $0.09 at +33.3 s; +$0.000017 beyond at +244 s |
| run listing | `APIFY_TOKEN`: exactly this run. `APIFY_TOKEN_2`: none | — |
| funnel (default config) | 15 raw → 1 final (the default config's worldwide-remote scope removes India-onsite rows) | — |

**Answered.** The actor accepts the ceiling and Apify records it, the depth is
honoured, and the charge is the documented model to the cent: 15 × $0.006, with
no start fee and no platform usage billed to the user. The run used 66.7% of its
ceiling. No stop condition held, so Phase 2 was allowed.

## 11. Phase 2 — the live concurrency canary (MEASURED)

`SWEEP_ALLOW_PAID_BENCH=1 .venv/bin/python -m bench.search_v2_paid_probe --allow-paid
--max-usd 0.78 --exposed-usd 0.503 --site indeed --keywords "Backend Developer,Full
Stack Developer" --location Bengaluru --searches 2 --paid-workers 2 --sweep-budget
0.27 --stage C3.5 …` at 2026-09-24 08:03:00 UTC.
[Evidence](search-v2-evidence/c35-indeed-concurrency-canary.json).

**Why it was run** (not because budget existed). C2's concurrency canary was
LinkedIn at 512 MB; nothing had shown that this account accepts two 4 GB runs at
once. No Indeed run had ever been reserved, overlapped or rate-limit counted.
The benchmark needed more than one Indeed runtime.

**Before the call.**

- The tests were green again after the probe changes.
- The inputs were checked offline: Backend Developer and Full Stack Developer @
  Bengaluru (IN), the same fixed fields, depth 15, and the engine cap
  `max_spend_usd` 0.27 in the profile.
- The preview printed `2 start(s) depth 15 ceiling $0.135 per start,
  provider-enforced / worst case this run: $0.270 / exposure before: $0.503 after:
  $0.773 limit: $0.78` and stopped.
- Intended exposure was $0.270, moving the ledger to $0.773. Two limits: the
  guard refuses a third start ($0.908), and C2 refuses a third reservation
  ($0.405 > $0.27).

| | `paid_000` Backend Developer | `paid_001` Full Stack Developer |
|---|---|---|
| run | `C42sLRdkZOdkNB9kC`, 0.0.111, SUCCEEDED | `b2gcIv8yxgGe1jQfC`, 0.0.111, SUCCEEDED |
| provider start → finish | 08:03:07.312 → 08:03:12.225 | 08:03:07.374 → 08:03:12.659 |
| actor runtime | 4.69 s | 5.12 s |
| `maxTotalChargeUsd` recorded | 0.135 | 0.135 |
| input received | `maxItemsPerSearch: 15` | `maxItemsPerSearch: 15` |
| memory | 4096 MB | 4096 MB |
| rows | 15/15 | 15/15 |
| reservation | reserved .394, committed .398 | reserved .395, committed .400 |
| unit: start / wait (1 poll) / dataset | 1.39 / 5.30 / 0.96 s | 1.38 / 5.29 / 0.83 s |
| waited for the earlier search | 0 | 0.54 s |
| SDK calls / requests / 429s | 4 / 4 / 0 | 4 / 4 / 0 |
| terminal poll | `result` × 0, $0.00 | `result` × 0, $0.00 |
| account delta at the engine's read | $0.00 | $0.00 |
| settled | **$0.09** (`result` × 15) at +18.6 s | **$0.09** at +18.4 s |
| platform usage | DEVELOPER | DEVELOPER |

- **Overlap 4.85 s; peak concurrent at the provider 2** (the provider's own
  clocks). The Indeed segment took **8.50 s** for two searches whose unit
  executions summed 15.16 s. The same two, serially, would be about the sum.
- **Reservation**: `pending_peak` $0.270 = `committed` $0.270 = the cap. The
  view never moved off $0.270, while `observed` stayed $0. Nothing was released
  and nothing was blocked. A third search would have been refused.
- **Accounting**: the account covered both runs' $0.18 at +18.7 s after the last
  finish and carried +$0.000034 beyond it at +484 s (the post-run read residual).
- **No throttling**: 4 requests per search is exactly start + 1 poll + 2
  dataset pages, so 0 SDK retries and 0 rate-limit errors, read from the SDK's
  own counters.
- **Run listing**: exactly these two runs on `APIFY_TOKEN`, none on
  `APIFY_TOKEN_2`. The token was absent from the evidence (checked by value).
- **Checkpoint**: 2 passes, 207 ms CPU (score-once).

Stopped there. The questions are answered, and one failure-free pair is what the
brief allowed.

## 12. Offline benchmark

**MEASURED 2026-09-24 14:00 IST**, `bench/search_v2_paid_concurrency.py --plans
default --waits scaled --arms serial,old1,old2,old3,old4,1,2,3,4 --indeed-runtimes
4.134,4.693,5.123` ([evidence](search-v2-evidence/c35-paid-concurrency-benchmark.json),
revision `61681c9-dirty`, i.e. this change before commit). Each arm is a fresh
child process running `scraper.main()` against C2's thread-safe scripted
stand-in, with zero Apify.

- **Plan.** The repository default's shape: 9 keywords × {India, Remote} on
  LinkedIn (18) and × the eight default places on Indeed (72), with 15
  synthetic rows a search.
- **Old arms.** `oldN` is C2 at N workers as it was before C3.5: Indeed's charge
  model is removed, so Indeed runs one at a time, unreserved. `1`–`4` are C3.5.
  `serial` is the flag-off loop.
- **Waits, scaled 1:10.** LinkedIn uses C1's measured runtimes (16, 22, 34, 40 s).
  Indeed uses **the three live C3.5 runtimes (4.13, 4.69, 5.12 s)**. The draw is
  seeded, the poll is 5 s, start 0.6 s and dataset 0.9 s, all divided by 10. CPU
  is not scaled, so local work weighs ten times more here than in a real sweep.
- **Host control** (C2's). Every calibration read 3.50–3.53 ms/row, so no arm
  was flagged and none was rerun.

| Arm | Wall | Wait model | CPU | LinkedIn segment | Indeed segment | Peak in flight (LI / IN) | Committed | vs old, same workers |
|---|---:|---:|---:|---:|---:|---|---:|---:|
| serial (flag off) | 339.6 s | 111.5 | 224.5 s | — | — | 1 / 1 | — | — |
| old1 | 123.2 s | 111.5 | 8.4 s | 53.8 | 69.2 | 1 / 1 | $0.828 | — |
| old2 | 97.0 s | 86.7 | 8.3 s | 27.7 | 69.2 | 2 / 1 | $0.828 | — |
| old3 | 88.8 s | 78.7 | 8.4 s | 19.4 | 69.1 | 3 / 1 | $0.828 | — |
| old4 | 85.2 s | 75.6 | 8.2 s | 15.9 | 69.1 | 4 / 1 | $0.828 | — |
| **C3.5 1** | 123.1 s | 111.5 | 8.5 s | 53.8 | 69.1 | 1 / 1 | $10.548 | 1.00× |
| **C3.5 2** | **62.6 s** | 56.8 | 7.6 s | 27.6 | **34.8** | 2 / 2 | $10.548 | **1.55×** |
| **C3.5 3** | **42.6 s** | 38.7 | 7.2 s | 19.4 | **23.0** | 3 / 3 | $10.548 | **2.08×** |
| **C3.5 4** | **33.7 s** | 30.5 | 6.8 s | 15.9 | **17.6** | 4 / 4 | $10.548 | **2.53×** |

The wait model is the pure schedule: each provider's searches in plan order,
greedy over its width, LinkedIn's segment first, no CPU. Every arm lands within
~4 s of the model plus its measured CPU.

- **The same work in every arm.** Every arm made 90 starts, 196 polls and 91
  account reads, wrote 484 final rows, and produced **one output SHA-256 and one
  done ledger across all nine**. Concurrency moves requests; it never adds them.
- **Indeed's segment falls with width**: 69.1 s → 34.8 / 23.0 / 17.6 s, which
  is 1.99× / 3.00× / 3.93×. LinkedIn's is the same in old and new arms, as it
  must be.
- **At one worker C3.5 is old C2** (123.1 s against 123.2 s). The difference is
  the exposure it holds: $10.548 (18 × $0.046 + 72 × $0.135) against $0.828.
- **Memory.** Peak RSS was 66–68 MiB in every arm, so no measurable cost from
  concurrency.
- **Buffering.** Peak buffered 1 / 2 / 3 at 2 / 3 / 4 workers. Buffered wait
  totals 8.1 / 12.7 / 17.0 s, with no single wait over 2.1 s.
- **Checkpoint CPU.** 90 passes, 6.4–7.9 s over the sweep (score-once, C2 §16),
  against 223.4 s of finalize CPU for the serial loop. At 1:10 compression this
  serial coordinator work is what keeps the Indeed segment above its model: 72
  searches of ~0.65 s scaled wait each, plus ~0.09 s of checkpoint apiece.

**Reading it in real time (INFERRED).** Multiply the wait model by 10 and add the
arm's CPU:

| Default plan | Modelled real wall |
|---|---:|
| serial loop (production today, flag off) | ~22 min (1,115 s of waits + ~224 s of re-scoring; real descriptions score slower) |
| C2 before C3.5, 2 workers (C2's proposed C5 setting) | **~14.6 min** |
| C2 before C3.5, 4 workers | ~12.7 min |
| **C3.5, 2 workers** | **~9.6 min** |
| C3.5, 3 workers | ~6.6 min |
| C3.5, 4 workers | ~5.2 min |

Each arm also pays ~0.3 s per account read (91 of them, in the coordinator, not
modelled).

- **The old floor.** Before C3.5 the floor was Indeed's serial segment: 72 × ~6.5 s
  ≈ 7.8 min of waiting that no worker count could shorten.
- **The new one.** With C3.5 that segment divides by the width. At two workers
  the modelled paid phase falls by about a third, at four by about 60%.
- **Smaller than C2 assumed.** Indeed's live runtime is 4–5 s, not LinkedIn's
  16–40 s as C2 had to assume, so each Indeed search is about 6.5 s including
  its one 5 s poll. The lever is real but smaller than C2's model implied.
- **Not a production speed-up.** This is an offline modelled improvement; no
  live full plan was run (that is C5).

## 13. Public spend cap — the arithmetic (VERIFIED, not changed)

`sweep/app.py` writes `max_spend_usd = spend_cap_for(estimate) = max($0.50,
1.25 × estimate)`. Indeed is estimated at $0.09 a search at depth 15
(`SITE_RATES` $0.09, basis 15), so the cap allows **$0.1125** against a
**$0.135** ceiling: it under-prices each Indeed ceiling by $0.0225 (17%).
LinkedIn is under-priced by $0.01225 (C2 §20). Through the real `spend_cap_for`,
`plan.cost`, `max_charge_usd` and `PaidExposure`, for the plans C3 audited
([`c35-public-cap-arithmetic.json`](search-v2-evidence/c35-public-cap-arithmetic.json)):

| Plan | Searches (LI + IN + NK) | Estimate | Cap | All ceilings | C2 admits, Indeed unbounded (before) | **C2 admits, C3.5** | Serial, flag off |
|---|---|---:|---:|---:|---:|---:|---:|
| repository defaults | 18 + 72 | $6.966 | $8.71 | $10.548 | 90 | **76** (18 + 58) | 90 |
| public `india` scope | 12 + 12 | $1.404 | $1.75 | $2.172 | 24 | **20** (12 + 8) | 24 |
| public `global` scope | 18 + 18 | $2.106 | $2.63 | $3.258 | 36 | **31** (18 + 13) | 36 |
| public `remote` scope | 2 + 2 | $0.234 | $0.50 | $0.362 | 4 | 4 | 4 |
| cohort software_fullstack / react_native | 4 + 4 | $0.468 | $0.59 | $0.724 | 8 | **7** | 8 |
| cohort business_salesforce | 6 + 6 + 6 | $3.702 | $4.63 | $1.086 + 6 unbounded | 18 | 18 | 18 |
| cohort multiple_roles_locations | 16 + 16 + 8 | $5.872 | $7.34 | $2.896 + 8 unbounded | 40 | 40 | 40 |

(An unbounded Naukri search is admitted while the view is under the cap and adds
its estimate. That is optimistic, because a real reading lags and admits more.)

**The product question, answered with the brief's numbers** (default plan):

| | Per Indeed search | 72 Indeed searches |
|---|---:|---:|
| current estimate | $0.090 | $6.480 |
| public cap allowance (1.25×) | $0.1125 | $8.100 |
| C3.5 provider ceiling | $0.135 | $9.720 |

Holding every ceiling (LinkedIn's $0.828 plus Indeed's $9.720 = $10.548) needs
**1.21×** the default plan's own cap. With the flag on, the public app would
stop Indeed-heavy sweeps at 83–88% of their plan. That is deliberate safety
behaviour, and it is **why C2 must not be enabled publicly** until one combined
pricing change makes the cap hold ceilings for bounded sites. That change is
deferred until the C4/C5 architecture is stable, as the brief prefers.
**Nothing changes for the public app today**: the flag is off everywhere, and
the serial path admits exactly what it did.

## 14. Tests

`sweep/tests/test_indeed_bounded.py`, **58 tests**, ~10 s, sockets denied. It
runs `scraper.main()` in-process against C2's `KeyedClient` and B1's
`FakeClient`, with no real credential.

| Group | Pins | Brief |
|---|---|---|
| `ChargeModel` (6) | $0.135 at 15; the depth table; the planned unit's ceiling from the configured depth; 22 fit, 23 do not; Decimal to the start; Naukri has no model | contract 1, 2 |
| `StartContract` (6) | the start carries exactly 0.135; the support check is consulted; an old client starts nothing; `run_input` is the pre-C3.5 object and bytes (and Remote); depth is the depth asked | contract 3–5, 8–10 |
| `SerialProviderSafety` (6) | serial starts carry the ceiling; the ceiling is the only request-log difference from before; bytes, ledgers, funnel and trace identical; the flag off never enters the scheduler; one worker is the serial requests and C1 record | flag-off parity, C2 serial parity |
| `Classification` (3) | with its model Indeed is bounded and 4 wide; without one it is unbounded and serial; Naukri stays serial | contract 6, 7 |
| `Reservation` (8) | exactly one at $0.135; two at $0.27, the third refused; a tenth of a cent short; the old guard let all through; reserved = sent; pending before the worker starts; committed before the request; no budget, never blocked | reservation matrix |
| `FailuresKeepTheirCeiling` (13) | §6's table | failure tests |
| `MixedExposure` (4) | §7 | mixed-provider |
| `ConcurrentParity` (5) | byte-identical at serial and 1–4; the same starts, inputs, ceilings and request counts; really w in flight; the first-planned twin wins; logs, ledger and units in plan order | concurrency |
| `DuplicateTie` (1) | the later Indeed search finishing first does not take the tie | duplicate tie |
| `ClientIsolation` (1) | one client per search, one thread per client | client isolation |
| `Telemetry` (2) | C1 plans and records 0.135; C2 holds it as exposure, never as cost | C1/C2 |
| `DeveloperGuard` (1) | the guard can now hold an Indeed plan's worst case under a limit | — |
| `ProbeContractReadings` (2) | the probe's run-contract and account-limit readers | — |

**Changed elsewhere.** Eleven assertions said "Indeed has no ceiling" and now
say what is true:

- B1 `test_no_ceiling_for_sites_without_a_model` checks Naukri only.
- C0 `Preflight` uses Naukri as the unbounded example. The default plan's
  preflight now reads "worst case this run: $10.548". "Both keys" starts Indeed
  at 0.135. "A site without a provider ceiling cannot run under a limit" uses
  `--site naukri`.
- C1 `test_providers_aggregate_their_own_units` expects bounded $0.362 and 0
  unbounded.
- C3 `test_the_default_plan_has_no_exact_duplicate` expects Indeed bounded.
- C2's four `MixedProviders` tests pin what C2 does with a provider that has
  **no** ceiling. They keep doing exactly that, with Indeed's model removed
  (`unbounded_indeed()`), because Indeed played that part until now.
  `Classification` covers the real unbounded provider, Naukri.

`test_a_funded_mixed_plan_is_byte_identical` now runs with both providers
bounded, unchanged, and passes.

## 15. Mutation checks

Each mutation is one exact edit, applied alone. Five suites run
(`test_indeed_bounded`, `test_paid_concurrency`, `test_paid_contract`,
`test_paid_observability`, `test_paid_dev_guard`), then the file is restored and
its SHA-256 compared with its value before the set.
[Results](search-v2-evidence/c35-mutations.json). A–N are the brief's; P and Q
are extra.

| # | Mutation | Result | Among the failures |
|---|---|---|---|
| A | Indeed charge model removed (entry deleted) | **41 fail, 1 error** | `ChargeModel.test_the_default_depth_is_bounded_at_0135`, `Classification.test_with_its_model_indeed_is_bounded_and_runs_in_the_pool`, C0 `GuardedEngine.test_both_keys_run_the_plan_under_the_b1_ceiling` |
| B | a float ceiling instead of Decimal | **8 fail, 43 errors** | `ChargeModel.test_decimal_from_the_formula_to_the_start`, `DeveloperGuard.test_an_indeed_plan_has_a_worst_case_the_guard_can_hold`, and every reservation (Decimal + float raises) |
| C | `max_total_charge_usd` not passed to an Indeed start | **7 fail** | `StartContract.test_the_start_carries_exactly_the_computed_ceiling`, `SerialProviderSafety.test_every_serial_indeed_start_carries_the_ceiling`, B1 `test_every_site_with_a_charge_model_gets_its_ceiling` |
| D | Indeed bounded without the ceiling-support check | **3 fail** | `StartContract.test_a_client_that_cannot_set_the_ceiling_starts_nothing`, `StartContract.test_the_ceiling_support_check_is_consulted_for_indeed`, `FailuresKeepTheirCeiling.test_an_sdk_without_the_ceiling_starts_nothing_and_holds_nothing` |
| E | an Indeed reservation below its provider ceiling | **23 fail** | `Reservation.test_the_reserved_ceiling_is_the_one_the_start_carries`, `FailuresKeepTheirCeiling.test_provider_failed`, `Classification.test_with_its_model_indeed_is_bounded_and_runs_in_the_pool` |
| F | a committed Indeed ceiling released after success | **12 fail** | `FailuresKeepTheirCeiling.test_the_full_depth_returned`, `Reservation.test_a_budget_of_exactly_two_ceilings_starts_two_and_blocks_the_third`, `MixedExposure.test_linkedin_starts_see_the_indeed_ceilings_already_held` |
| G | released after FAILED / timeout / abort | **8 fail** | `FailuresKeepTheirCeiling.test_provider_failed`, `…test_provider_timed_out`, `…test_engine_deadline_then_abort`, `…test_an_ambiguous_start_with_no_run_id` |
| H | bounded Indeed accidentally kept at width 1 | **4 fail** | `ConcurrentParity.test_indeed_really_runs_w_at_a_time`, `Classification.test_with_its_model_indeed_is_bounded_and_runs_in_the_pool`, `MixedExposure.test_both_providers_ceilings_accumulate_in_one_view` |
| I | Indeed classified bounded whatever its charge model | **4 fail** | `Classification.test_without_a_model_indeed_is_unbounded_and_one_at_a_time`, C2 `MixedProviders.test_indeed_never_enters_the_pool`, C2 `MixedProviders.test_indeed_runs_exactly_as_serial` |
| J | every search shares the coordinator's client | **2 fail** | `ClientIsolation.test_every_search_has_its_own_client_used_by_its_own_thread`, C2 `AccountSelection.test_two_accounts_the_one_with_headroom_chosen_once_for_every_worker` |
| K | results integrated in completion order | **13 fail** | `DuplicateTie.test_a_later_indeed_search_finishing_first_does_not_take_the_tie`, `ConcurrentParity.test_byte_identical_at_every_worker_count`, `ConcurrentParity.test_the_first_planned_twin_wins_whoever_finished_first` |
| L | Indeed `run_input` changed while adding the ceiling | **4 fail, 6 errors** | `StartContract.test_the_run_input_is_the_pre_c35_object_and_bytes`, B1 `OtherSitesUnchanged.test_indeed_input_is_byte_for_byte_unchanged`, B1 `…test_neither_gained_a_depth_or_ceiling_field` |
| M | the account delta used as new headroom | **23 fail** | `FailuresKeepTheirCeiling.test_a_cheap_settled_run_is_still_the_whole_ceiling`, `Reservation.test_a_budget_of_exactly_one_ceiling_starts_one`, `MixedExposure.test_indeed_starts_see_the_linkedin_ceilings_already_held` |
| N | `SWEEP_PAID_CONCURRENCY=1` set by the public worker's spawn | **1 fail** | C2 `FlagAndWorkers.test_no_deployment_file_turns_it_on` |
| P | the probe records the account's limits under the wrong names | **1 fail** | `ProbeContractReadings.test_the_accounts_memory_and_concurrency_limits` |
| Q | no overshoot headroom (Indeed ceiling at exactly the depth) | **36 fail** | `ChargeModel.test_an_honest_overshoot_fits_and_a_runaway_does_not`, `ChargeModel.test_the_default_depth_is_bounded_at_0135`, `DeveloperGuard.test_an_indeed_plan_has_a_worst_case_the_guard_can_hold` |

**16 of 16 caught.** Every mutated file (`scraper.py`, `deploy/sweep_worker.py`,
`bench/search_v2_paid_probe.py`) was restored byte-identical (SHA-256), in
~41 s per mutation, 11 min for the set.

- **A.** The first pass renamed the entry's key, which also added a bogus site,
  so it was re-run as a true deletion.
- **B and L.** They were re-run alone so that every failing test is on record.
  L's errors are the pre-C3.5 comparison arms, where `max_charge_usd` is `None`.
- **The probe's run-contract reader** was written before its test. Mutation P
  shows the account-limits test can fail. A tier-pricing reader, written the same
  way, was deleted once Phase 1 showed the run record already carries the
  applied price.

## 16. Research ledger

`docs/search-v2-evidence/paid-research-ledger.json` (appended by the probe;
refuse-only, never authorisation, read by no production code):

| Stage | Provider | Starts | Depth | Ceiling / start | Intended | Actual | Cumulative intended | Cumulative actual |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| C0 | LinkedIn | 1 | 15 | $0.046 | $0.046 | $0.03005 | $0.046 | $0.03005 |
| C1 probe A | LinkedIn | 2 | 15 | $0.046 | $0.092 | $0.0601 | $0.138 | $0.09015 |
| C1 probe B | LinkedIn | 1 | 15 | $0.046 | $0.046 | $0.03005 | $0.184 | $0.1202 |
| C2 canary | LinkedIn | 2 | 15 | $0.046 | $0.092 | $0.0601 | $0.276 | $0.1803 |
| C3 canary | LinkedIn | 1 (2 searches) | 15 | $0.092 | $0.092 | $0.06005 | $0.368 | $0.24035 |
| **C3.5 Phase 1** | **Indeed** | **1** | **15** | **$0.135** | **$0.135** | **$0.09** | **$0.503** | **$0.33035** |
| **C3.5 Phase 2** | **Indeed** | **2** | **15** | **$0.135** | **$0.270** | **$0.18** | **$0.773** | **$0.51035** |

C3.5 spent $0.405 intended, inside the brief's ~$0.45 soft ceiling, and $0.27
actual. Of the shared $2.00, 38.7% is now intended. The ledger's
`ceiling_covers` now lists C3.5.

## 17. Naukri status

Unchanged and out of scope. It is disabled by default, has no charge model, is
unbounded, runs serially under C2 and is refused under a `--max-usd`. Its
current record (C3 §13) is PPE since 2026-09-11 with a $0.10 minimum ceiling and
`fetchDetails: true` → `job_item_detailed` at $0.003, so a "B1 for Naukri" is
possible in principle: at depth 50, 50 × $0.003 × 1.5 + a start event ≈ $0.226.
It needs its own contract canary (a disabled provider's first live run) and a
look at the stale `SITE_RATES["naukri"]` $0.50 "per run MINIMUM". Recommended as
a follow-up only if Naukri is ever enabled.

## 18. Unresolved risks

1. **Three Indeed runs are not a distribution.** One account, one hour, one city
   and three generic queries. Every one returned exactly 15 rows and ran in
   4–5 s. Other queries, markets or hours may be slower. A zero-result search
   was not run, so whether its `FOUND_NO_RESULTS` error item is charged is
   UNKNOWN; it is bounded by the ceiling either way.
2. **Build drift.** Sweep runs `latest`. The changelog shows `maxItemsPerSearch`
   overflows fixed twice, and blocking outages three times in 2026. A regression
   that overshoots beyond 22 listings now aborts at $0.135 and fails the search
   rather than billing on. That is the safe direction, but it is lost coverage,
   and it would show as `failed` with a non-`SUCCEEDED` status in C1 telemetry.
3. **Pricing drift.** A FREE-tier price above $0.009 (15 × p > $0.135) would
   abort honest depth-15 runs. `SWEEP_SEARCH_V2_TELEMETRY` records each unit's
   `charged_events` and ceiling, so the symptom is visible. The model's price is
   a reviewed constant, not read live.
4. **Four workers leave no memory headroom** on a 16 GB account (§8). Two is the
   recommendation until four has run live.
5. **The public spend cap under-prices both providers' ceilings** (§13). C2 is
   still not for the public worker.
6. **Custom-event enforcement is documented, not observed at the limit.** No run
   reached its ceiling, so the abort-at-limit behaviour was never triggered live.
   Apify documents it for every PPE actor, and every run recorded
   `isMaxTotalChargeUsdSetByUser: true`.
7. **Post-run storage costs are outside every ceiling** (+$0.000017 and
   +$0.000034 here). They are negligible, but they mean an account delta is never
   a run's cost (C1 §10).
8. **The engine's 5 s poll is now as long as the actor.** An Indeed run takes
   4.1–5.1 s, and its unit spends 5.3 s waiting for one poll. Polling was not in
   scope and was not changed; this is recorded for C4.
9. **Unused ceiling is not reusable within a sweep.** Each Indeed start holds
   $0.135 and settles at $0.09, so $0.045 (33%) of headroom sits idle per start.
   This is deliberate (C2 §5).

## 19. Readiness for V2-C4

C4 starts from a paid plan in which **both enabled paid providers are
provider-bounded, reserved and overlappable**:

- Every paid unit has a hard per-run maximum known before it starts, which is
  the precondition for any adaptive decision that spends against a budget.
- Unit telemetry now covers Indeed on the same terms as LinkedIn: funnel,
  trace, `sdk`, reservation.
- The live evidence adds Indeed's runtime (4–5 s), its cost convergence
  (settled in ~18 s, the same lag class as LinkedIn) and its yield under the
  default config (1–2 final rows of 15 in the India-onsite shapes here).

C4's levers are logical: which searches and at what depth. Its constraint is §13,
since a cap that cannot hold ceilings truncates whatever C4 decides.

## 20. Recommended C3.5 settings for C5 — NOT executed

```
SWEEP_SEARCH_V2_TELEMETRY=1      # unchanged
SWEEP_PAID_CONCURRENCY=1
SWEEP_PAID_WORKERS=2             # 8 of 16 GB for Indeed; 4 only after 2 is clean live
max_spend_usd                    # explicit, >= sum of ceilings: $10.548 for the default plan
```

Run it as a developer sweep through the guard with both keys and a stated
budget, **not** on the public worker until §13 is fixed. There is no C3.5 flag:
the charge model is always on. Watch these:

- `paid_execution.segments` (Indeed `bounded: true`, `workers: 2`);
- committed exposure (never above the budget, never falling);
- per-search `sdk` 429s and retries (baseline 0);
- `failed` Indeed units with `ABORTED` status (a ceiling reached);
- `charged_events.result` against rows.

## 21. Verification

- **Baseline.** `61681c9` (= `origin/main`) at the start. Its sweep suite is as
  C3 left it: 1,322 tests.
- **Sweep suite.** `python -m unittest discover -s sweep/tests -t .` —
  **1,380 tests, OK** (1,322 + 58), 218.5 s, on the final tree. It was also
  1,381 OK before the canaries, when the new module still held the probe's
  tier-pricing reader and its test.
- **Deploy tests.** `python -m unittest deploy.test_sweep_worker
  deploy.test_modal_benchmark` — **42 tests, OK**.
- **Self-checks.** `python scraper.py --demo`, `python telemetry.py`,
  `python -m sources` and `python -m sources.concurrency` pass.
- **auto-apply.** `python -m unittest discover -s auto-apply/tests -t auto-apply`
  — 1,102 tests with 2 errors in `test_inference` healthz (the local inference
  service). **The same two errors reproduce on a clean worktree of `61681c9`**,
  which also skips 2 tests needing a local corpus that a fresh checkout lacks.
  This is environmental, as for B3, B5, C0, C1, C2 and C3.
- **Mutations.** 16/16 caught; every mutated file restored byte-identical (§15).
- **Benchmark.** One output SHA-256 and one done ledger across all nine arms;
  no arm measured on a slowed host (§12).
- **Production isolation.** `git diff 61681c9 -- config.py telemetry.py deploy/
  sweep/app.py sweep/runs.py sweep/plan.py sweep/worker_link.py sweep/public.py
  sweep/logic.py sources/ rescore_from_apify.py auto-apply/ requirements.txt
  render.yaml gunicorn.conf.py bench/paid_guard.py bench/search_v2_paid_overhead.py
  bench/search_v2_paid_compaction.py` is **empty**. The one changed production
  file is `scraper.py` (+30 / −6): the Indeed entry, its provenance comment, the
  Naukri comment, and one `PaidExposure` docstring line. There is no flag,
  environment or deployment change.
- **Developer tooling.** `bench/search_v2_paid_probe.py` gains two free-GET
  evidence readers (the input the provider recorded, the run's options and
  billing model, and the account's memory and concurrency limits).
  `bench/search_v2_paid_concurrency.py` gains the `default` plan, `oldN` arms,
  `--indeed-runtimes` and `--public-cap`. Both stay behind C0's classification
  and guard; the C0 reachability scan passes.
- **Paid.** Three Indeed starts in two canaries (§10–§11). Each was preceded by
  the offline input check from the exact profile and a zero-cost preview. Both
  keys were on the command line, with a tight `--max-usd`, and for Phase 2 an
  engine cap of exactly two ceilings. The run listings show no other activity on
  either account. The temporary probe profiles were removed (none left in
  `profiles/`), and outputs lived in temp directories that were deleted. Every
  other provider read was a free GET: the public actor record, build and
  pricing page, the probes' own run records, and the account's limits.
- **Tokens.** No configured token's value appears in any new or changed file
  (checked by value, for `APIFY_TOKEN` and `APIFY_TOKEN_2`).

## 22. Rollback

Two ways back:

- To drop the concurrency, unset `SWEEP_PAID_CONCURRENCY` (it is set nowhere).
  The serial loop still gives every Indeed start its provider ceiling.
- To drop the ceiling itself, remove the `indeed` entry from
  `ACTOR_CHARGE_MODEL`. Indeed is then exactly as C2 left it: unbounded, serial,
  no ceiling. The four C2 tests that pin that behaviour already run in that
  configuration.

No environment, deployment or data change needs undoing.
