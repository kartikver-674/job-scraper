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
