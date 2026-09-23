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
