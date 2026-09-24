# Audit evidence and reproduction

Observation date: 2026-09-21. Source revision:
`9dc67cc33f99e5d87bd224498bafd0a336354a4f`.
Read [the engine report](../search-engine-v2-forensic-audit.md) and
[Free expansion report](../free-source-expansion-audit.md) for interpretation.

Only documents, explicit public-endpoint probes and offline measurement tools
were added. No production importer references these harnesses. There is no
deployment, actor execution or production configuration change in this audit.
Do not import/run the production scraper's `main` to reproduce these results.

## Offline checks

From the repository root:

```sh
.venv/bin/python bench/search_v2_paid_audit.py
.venv/bin/python bench/search_v2_offline_audit.py --output /tmp/search-v2-identities.json
```

Both deny socket connections. The paid harness regenerates synthetic plans and
anonymized numeric historical log summaries; it never calls an actor. The
identity harness asserts six current dedupe behaviors, including known false-
merge/missed-duplicate conditions; passing means the audit reproduced today's
behavior, not that the identity policy is correct.

`bench/search_v2_replay.py` also denies sockets and renders a synthetic profile
in memory before loading the unchanged scorer. Example:

```sh
.venv/bin/python bench/search_v2_replay.py --case software_fullstack --scope india --baseline /tmp/search-v2-current-free-jobs.json --expansion /tmp/search-v2-expansion-jobs.json --output /tmp/search-v2-replay.json
```

Other cases: `react_native`, `business_salesforce`, `multiple_roles_locations`;
scope: `india` or `remote`. Evidence contains four case/scope replays, not every
combination. It explicitly forces the experience-mismatch guard off. It does not
change profile-generation logic, score weights in production or output/ files.
`bench/search_v2_summarize.py` derives the date-window/identity tables from the
two snapshots and verifies 140 records /134 active /365 probes /187 nonempty.

## Optional public probes

Re-fetch only when required; provider limits and data change over time. These
commands use public network access, **no Apify**. The baseline harness default
without `--live` produces an unmeasured inventory and would overwrite existing
evidence if pointed at the same output directory. Use a separate output path for
a new audit; preserve this dated snapshot.

```sh
.venv/bin/python bench/search_v2_free_audit.py --live --output /tmp/search-v2-new-census --snapshot /tmp/search-v2-new-current-jobs.json
.venv/bin/python -m bench.search_v2_expansion_audit --targets docs/search-v2-evidence/expansion-validation-targets.json --out /tmp/search-v2-new-expansion.json --jobs /tmp/search-v2-new-expansion-jobs.json
.venv/bin/python bench/search_v2_spotcheck.py
```

The last command only lists its 21 explicit requests; `--live` executes them.
Expansion target list contains 365 boards including the five primary-search
additions. The separate extra-target file records their provenance. One attempt
per request, bounded body/socket timeouts, serial pacing; no domain enumeration
or uncontrolled crawl. Socket deadlines are not a strict whole-run deadline.

## Evidence dictionary and limits

- `current-free-*`: literal registry and one snapshot of public endpoints.
  `fresh_job_count` in the original CSV/JSON means **21 days**, while appendix
  F14 in the report is derived separately. Null/UNKNOWN never means zero.
- `expansion-discovery-registry.json`: 15,879 unique provider/board pairs,
  including 81 already in baseline and **15,798 additional candidate IDs**.
  Source URLs/hash provenance retained; list membership is not health/identity.
- `expansion-validation.json`: 365 probes, 187 nonempty, 14 empty, 137 HTTP404,
  27 transport/timeouts. IDs/field completeness are one observation, not proof
  of long-term stability. Slug-derived company names are explicitly labeled.
- `expansion-new-family-probes.json`: eleven explicit new-family probes, nine
  successful; fields/counts/status only. Full job samples/descriptions omitted.
- `first-tranche-spotchecks.json`: two failures reconfirmed, canonical WWR feed
  HTTP200 with 83 items; nine company identities confirmed; eight sampled job
  pages HTTP200, PubMatic HTTP403. No authentication/bot-control bypass.
- `replay-*`: current-rule synthetic candidate funnels, winner attribution and
  retrospectively ranked candidate tranches. Scores >0 /≥10 are descriptive
  metrics, not newly enabled relevance thresholds. Do not equate them with
  verified real-candidate fit. Three initial replays have contention-affected
  timings; India full-stack was rerun serially with CPU/RSS counters.
- `snapshot-analysis.json`: baseline 12,533 rows, expansion 8,047, strict F14,
  heuristic identity counts/collisions; snapshot hashes tie the analysis inputs.
- `paid-*`: exact synthetic actor inputs/estimates and published contract
  research. Estimated prices are not guaranteed maximum charges.

Added by the V2-A implementation stage (2026-09-22), documented in
[V2-A telemetry and shadow](../search-engine-v2-a-telemetry-and-shadow.md):

- `shadow-eight-board-replay.json`: the eight recommended Greenhouse boards
  replayed offline against the frozen snapshots through five synthetic cohorts,
  with per-board request/raw/fresh/gated/eligible/positive/marginal/top-20
  attribution. Reproduce with `bench/search_v2_shadow.py` (sockets denied).
  Its deltas match this audit's; its baseline totals differ by 1–3 rows because
  the 14-day window moved one day between the snapshot and the replay.
- `smartrecruiters-pagination.json`: 16 live public requests across five
  already-configured boards, rows 1–400, with per-page freshness, marginal
  uniqueness, duplicate overlap and cumulative eligibility. Reproduce with
  `bench/search_v2_smartrecruiters_pages.py` (`--live`; a dry run prints the
  exact request list first). Zero Apify cost. Production pagination unchanged.
- `telemetry-overhead.json`: SWEEP_SEARCH_V2_TELEMETRY off-vs-on over the frozen
  census, alternating arms, asserting the result set is identical across every
  pass. Reproduce with `bench/search_v2_telemetry_overhead.py`. The denominator
  is local scoring work only, not a whole sweep.

Added by the V2-B2 concurrency experiment (2026-09-23), documented in
[V2-B2 deterministic Free concurrency](../search-engine-v2-b2-deterministic-free-concurrency.md):

- `free-concurrency-replay.json`: serial and 1/2/3/4 Lever workers over ONE
  frozen capture of all 21 configured Lever boards, replaying each board's
  measured latency so completion order differs from registry order. This is the
  only concurrency arm that may claim parity, and it shows an identical source
  sequence and an identical final SHA-256 at every worker count. Reproduce with
  `bench/search_v2_free_concurrency.py --capture` then `--replay`.
- `free-concurrency-live.json`: the same four arms against the live public Lever
  API — wall time, per-board durations, failures, retries and peak RSS. Latency
  and provider behaviour ONLY: inventory changes between arms, so row counts
  here are not parity evidence. Zero Apify credits. Reproduce with `--live`.

Added by the V2-B3 shadow evaluation (2026-09-23), documented in
[V2-B3 shadow production evaluation](../search-engine-v2-b3-shadow-production-evaluation.md):

- `shadow-b3-frozen-equivalence.json`: for all five synthetic cohorts, the
  production evaluator (`sources.shadow.evaluate`) against V2-A's offline method
  (`finalize(baseline + shadow)`) on the SAME date — every board, stage and count
  agrees — plus evaluator CPU beside one production finalize pass. Sockets
  denied. Reproduce with `bench/search_v2_shadow_b3.py --frozen`.
- `shadow-b3-live-benchmark.json`: one public pass over the eight boards (19
  GETs, zero Apify): latency, failures, raw rows, native-id coverage, overlap
  with the frozen baseline, native-id persistence since 2026-09-21, one sampled
  job page per board, and the live rows judged under each cohort against its
  frozen baseline. Reproduce with `--live --reach 1`. One dated observation.
- `shadow-b3-free-sweep-parity.json`: worker-shaped FREE sweeps (`scraper.py
  --profile X --yes`, plus `--site free`) over four public boards — shadow off,
  on, on with production's Lever settings, all flags off. CSV and JSON
  byte-identical in every arm. Reproduce with `--sweep`, which refuses to run
  unless the paid plan is empty (see that document's §18 for why).

Added by the V2-B4 Greenhouse concurrency stage (2026-09-23), documented in
[V2-B4 Greenhouse concurrency](../search-engine-v2-b4-greenhouse-concurrency.md):

- `greenhouse-concurrency-replay.json`: serial and 1/2/4/6/8 workers over ONE
  frozen capture of all 54 production Greenhouse boards, replaying each board's
  recorded latency and recorded error (Postman's 404 replays as a 404), each arm
  under its own telemetry record. Rows, final output and telemetry units are
  compared field by field; all arms identical. Includes a labelled synthetic
  collision arm. Reproduce with `bench/search_v2_free_concurrency.py --provider
  greenhouse --capture` then `--replay --collision`.
- `greenhouse-concurrency-live.json`: serial/2/4/6/8 against the live public
  Greenhouse API, each arm in a fresh process so peak RSS is per arm. Latency,
  failures, retries, 429s and memory ONLY; not parity. Zero Apify credits.
  Reproduce with `--provider greenhouse --live`.

Added by the V2-B5 results-ready stage (2026-09-23), documented in
[V2-B5 early results ready](../search-engine-v2-b5-early-results-ready.md):

- `results-ready-hidden-wait.json`: the offline stack the B5 tests drive — the
  real worker on loopback, the public Render app pointed at it, and
  `scraper.main()` on fixtures as the worker's child — with a known post-result
  tail injected as a per-board shadow delay (0, 4 and 12 s; flag off and on;
  three runs per arm). Per run: when the marker was published, when the worker
  recorded the exit, and when Render's `/progress` first said finished (polled
  every 25 ms). Every socket but loopback denied; zero Apify. Reproduce with
  `bench/search_v2_results_ready.py`.

Added by the V2-C0 and V2-C1 paid stages (2026-09-23/24), documented in
[V2-C0](../search-engine-v2-c0-paid-dev-safety.md) and
[V2-C1 paid observability](../search-engine-v2-c1-paid-observability.md).
**These are the only files here from PAID runs**; every one is also an entry in
the ledger.

- `paid-research-ledger.json`: every live paid actor start for Search V2
  research — the worst case decided before it, the settled charge after it,
  running totals against the shared $2.00 C1–C4 ceiling. Developer evidence
  only: never authorisation, never read by production. No token, no row content.
- `c1-linkedin-contract.json` (C0): one LinkedIn start confirming B1's contract.
- `c1-probe-a-india-two-shapes.json`, `c1-probe-b-remote-react.json` (C1): three
  LinkedIn starts through the unchanged engine behind C0's guard, synthetic
  cohort, developer-chosen generic queries. The engine's own per-unit clocks,
  funnel and one-token-per-position trace; every cost reading with its
  timestamp and seconds after the run finished; free re-reads until the charge
  settled. Probe A's `accounts` were re-derived from its recorded readings with
  a corrected criterion (see its `reanalysed`). Reproduce the form, not the
  numbers, with `bench/search_v2_paid_probe.py` (spends money; needs both keys).
- `c1-cross-probe-summary.json`: ranges and yield by position derived from the
  two probe files; `--summarize`, no request.
- `c1-paid-telemetry-overhead.json`: OFFLINE. A 90-search paid plan through
  `scraper.main()` against a scripted Apify stand-in, telemetry off vs on, each
  arm a fresh process. Zero Apify. Reproduce with
  `bench/search_v2_paid_overhead.py`.

Added by V2-C2 (2026-09-24),
[paid reservations and concurrency](../search-engine-v2-c2-paid-reservations-concurrency.md):

- `c2-live-concurrency-canary.json` (C2, PAID): two LinkedIn starts at once
  through the reservation scheduler (`--paid-workers 2`, engine cap $0.092),
  behind C0's two keys, synthetic cohort, C1 probe A's generic queries. Adds to
  C1's form: the scheduler's own `paid_execution` section (reservation timeline,
  per-search SDK request and 429 counters) and `provider_overlap` from the
  provider's clocks. A ledger entry. Reproduce the form, not the numbers, with
  `bench/search_v2_paid_probe.py` (spends money; needs both keys).
- `c2-paid-concurrency-benchmark.json`: OFFLINE. C1's 90-search replay and its
  45 LinkedIn searches alone, the serial loop against the C2 scheduler at 1-4
  workers, with no provider wait and with C1-measured runtimes scaled 1:10, each
  arm a fresh process against the thread-safe scripted stand-in. Zero Apify.
  Reproduce with `bench/search_v2_paid_concurrency.py`.

Added by V2-C3 (2026-09-24),
[paid plan compaction](../search-engine-v2-c3-paid-plan-compaction.md) — outcome:
no compaction shipped:

- `c3-paid-plan-audit.json`: OFFLINE. Thirteen plans (repository defaults, the
  four synthetic cohorts, the public app's three work scopes, five generic
  profiles) through the current planner and adapters: logical searches, exact
  execution duplicates (none), structure, depth, bounded vs unbounded, overlap
  candidates, hypothetical physical starts per batch size, and the public spend
  cap's admission. Reproduce with `bench/search_v2_paid_compaction.py`.
- `c3-provider-contracts.json`: the LinkedIn, Indeed and Naukri actors' public
  records (free GETs), and content-free measurements on runs C0-C3 made:
  `inputUrl` on every item, LinkedIn's own `position` against dataset order,
  the cross-source dedupe comparison, C1/C2 traces re-bucketed by position.
- `c3-live-batch-canary.json` (C3, PAID): one LinkedIn start carrying two
  searches, through the C3 prototype behind C0's two keys, C1 probe A's and the
  C2 canary's queries. A ledger entry.
- `c3-batching-prototype.patch`: the exact code the canary ran (engine,
  telemetry, probe, tests) — not merged; kept so the evidence is reproducible.
- `c3-prototype-mutations.json`: the prototype's 16 mutation checks.

Added by V2-C3.5 (2026-09-24),
[Indeed bounded execution](../search-engine-v2-c35-indeed-bounded-execution.md) —
outcome A: every Indeed start carries a provider-enforced $0.135 ceiling and C2
may overlap Indeed:

- `c35-indeed-provider-contract.json`: the Indeed actor's public record, build,
  input schema, pricing history and store page (free GETs), the Apify
  documentation the bound rests on, the Free plan's limits, and the Phase 1 run
  record's applied price, options and billing model.
- `c35-indeed-contract.json` (C3.5, PAID): one Indeed start through the serial
  engine path behind C0's two keys: ceiling recorded, 15/15 rows, `result` x 15
  = $0.09, platform usage billed to the developer. A ledger entry.
- `c35-indeed-concurrency-canary.json` (C3.5, PAID): two Indeed starts at once
  through the C2 scheduler: both ceilings recorded, overlap, 0 retries/429s,
  $0.270 held throughout. A ledger entry.
- `c35-paid-concurrency-benchmark.json`: OFFLINE. The repository default's
  shape (18 LinkedIn + 72 Indeed): the serial loop, C2 with Indeed unbounded as
  before, and C3.5 at 1-4 workers, provider time scaled 1:10 with Indeed's
  runtimes from the three live runs. Reproduce with
  `bench/search_v2_paid_concurrency.py --plans default ...`.
- `c35-public-cap-arithmetic.json`: OFFLINE arithmetic. The public app's spend
  cap against every ceiling, and the starts C2 would admit, for the plans C3
  audited. `bench/search_v2_paid_concurrency.py --public-cap`.
- `c35-mutations.json`: the stage's mutation checks.

Public normalized JD snapshots remain only in `/tmp`; they are not committed.
Their absence on another machine means offline row replay needs a new public
census. Numeric artifacts, exact registry and inputs remain reviewable without
full JD duplication. Request-byte totals exclude headers/TLS/error bodies and
automatic redirect requests. Audit snapshots/file writes inflate process memory
and runtime relative to a production candidate-filtered pass.

Validation before commit: all seven harnesses parsed; all JSON parsed; relative
Markdown links checked; inventory/sample/plan arithmetic asserted; offline
planner and identity checks passed; token/private-key patterns absent; tracked
production files unchanged. No production test suite or deployment was needed
for these isolated audit files.
