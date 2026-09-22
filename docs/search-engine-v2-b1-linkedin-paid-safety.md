# V2-B1 — LinkedIn paid-safety patch

Date: 2026-09-22. Baseline: `b79b071` (V2-A). Scope: the LinkedIn actor contract
mismatch found in [V2-A](search-engine-v2-a-telemetry-and-shadow.md) §13.

This is **not** V2-B. No batching, no concurrency, no Free Sweep change, no new
boards, no ranking change, no Profile Engine V3 change, no role/query generation
change, no Search Preferences change, no dedupe change. **No paid Apify actor
was run.** Not deployed.

Three provider facts were re-read from primary sources before anything was
changed, and one of them **reverses a V2-A recommendation** (§5).

## Summary

| | |
|---|---|
| **OLD INPUT** | `{"urls": [url], "count": 15, "scrapeCompany": false}` |
| **NEW INPUT** | `{"urls": [url], "limitPerSource": 15, "count": 15, "scrapeCompany": false}` |
| **DEPTH GUARANTEE** | `limitPerSource` — the field the actor documents — carries the identical expression that produced `count`. Depth is unchanged, not increased. |
| **CHARGE CEILING** | `maxTotalChargeUsd` per run, passed as a **start argument**, not actor input. **$0.046** at the default depth of 15. |
| **CEILING FORMULA** | `ceil₀.₀₀₁(depth × $0.002 × 1.5 + 2 × $0.00005)`, floored at the actor's own `minimalMaxTotalChargeUsd` |
| **AUTOCONVERT DECISION** | **Left unchanged (not sent).** Setting it false would make Sweep's filters *worse*, not stricter — see §5. |
| **BUDGET INTERACTION** | Additive. Sweep's own cap is untouched and still the only thing bounding a whole sweep. |

## 1. What the provider actually says

Everything below was read on 2026-09-22 from the actor's own store record and
schema, not carried forward from the audit.

**Pricing** — `https://api.apify.com/v2/acts/curious_coder~linkedin-jobs-scraper`,
current `pricingInfos` entry (PAY_PER_EVENT, started 2026-08-14):

| Event | Price |
|---|---|
| `apify-default-dataset-item` ("result") | **FREE tier $0.002**; BRONZE–DIAMOND $0.001 |
| `apify-actor-start` | $0.00005, one-time, **one event per GB of memory, minimum one** |
| `minimalMaxTotalChargeUsd` | **$0.001** |
| `defaultRunOptions.memoryMbytes` | **512** (so one start event) |

**Depth** — the schema documents `limitPerSource`: *"Maximum number of jobs to
scrape from each input URL … Leave empty to scrape as many as LinkedIn returns
for each search (up to ~1000)."* There is no `count` field, and no documented
minimum for `limitPerSource`.

**Unknown input fields** — Apify input schemas default to
`additionalProperties: true`; an actor must opt in to rejecting extras. This is
what makes retaining `count` cheap rather than risky (§3).

## 2. The depth contract

`scraper.build_input` now sends both fields from **one expression**:

```python
depth = max(ACTOR_MIN_RESULTS["linkedin"], s["max_results"])
return {
    "urls": [_build_linkedin_url(s)],
    "limitPerSource": depth,     # authoritative: the documented field
    "count": depth,              # legacy hedge, same value
    "scrapeCompany": False,
}
```

`depth` is the unchanged expression that previously produced `count`. It reads
`effective_search`, which remains the one place billable depth is decided, so
`--dry-run --json`, the printed plan, the cost estimate and the actor input all
still report the same number they did before this patch. **The requested depth
was not increased.** `ACTOR_MIN_RESULTS["linkedin"] = 10` is untouched: changing
the floor would move the estimate, which is out of scope here.

## 3. `count` retained — why

**Retained**, at the identical value, with `limitPerSource` authoritative.

- **It is a working hedge.** The audit's retained logs show intended-depth-15
  searches returning 16–18 rows. That is a build that read `count` and honoured
  it. Sweep runs `latest` and the build is not pinned, so the contract can move
  again — in either direction.
- **It cannot cost anything.** Apify defaults to `additionalProperties: true`,
  and those historical runs succeeded rather than failing validation, so this
  actor evidently does not reject extras. An unread field is inert.
- **It cannot disagree.** Both fields read one local, so there is no state in
  which one of them is quietly deciding the bill.
  `test_count_is_retained_and_can_never_disagree` pins that.
- **The downside is now bounded anyway.** If a future build ignores *both*
  fields, the charge ceiling caps the run at $0.046 instead of ~$2.00. That is
  what makes keeping a belt *and* braces cheap rather than superstitious.

Removing it would have been a change with no upside and a small regression risk
against exactly the build the historical evidence describes.

## 4. The charge ceiling

### Mechanism

**`maxTotalChargeUsd` is a run parameter, not actor input.** The installed
`apify-client 3.1.0` exposes it as
`ActorClient.start(max_total_charge_usd: Decimal | None)`, and its body puts it
in `_build_params(..., maxTotalChargeUsd=max_total_charge_usd)` — a query
parameter on `POST /v2/acts/.../runs`, matching the
[Run Actor API](https://docs.apify.com/api/v2/actors-runs-post). Verified by
reading the installed SDK source, not assumed.

Putting the same name inside `run_input` would have produced an ordinary unread
input field that bounds nothing while looking like a guard —
`test_the_ceiling_is_never_smuggled_into_actor_input` exists because that is the
tempting mistake.

### Formula

```
ceiling = ceil_to_0.001( depth × result_usd × overshoot
                         + start_events × start_usd )
ceiling = max(ceiling, provider_minimum_usd)
```

| Term | Value | Why |
|---|---|---|
| `result_usd` | `$0.002` | The **FREE** tier price — the dearest published. A ceiling computed from the $0.001 paid-tier price would abort a free-plan account's legitimate run. Wrong in the safe direction. |
| `overshoot` | `1.5` | The actor has returned 16–18 rows for an intended 15. A ceiling at exactly the intended depth would abort a run that behaved normally, turning a spend guard into a data-loss bug. |
| `start_events` | `2` | Memory default is 512 MB → one event; two leaves room for a memory bump. |
| `start_usd` | `$0.00005` | Published per-GB start-event price, minimum one event. |
| rounding | up to `$0.001` | Coarser than the $0.00005 event granularity **on purpose**. Rounding up can only raise the ceiling, and carrying more decimals than the model supports would be invented precision. |
| `provider_minimum_usd` | `$0.001` | The actor's own `minimalMaxTotalChargeUsd`. The API refuses a lower ceiling, and a refused start is a failed search. |

| Depth | Ceiling | Expected honest charge (free tier) |
|---:|---:|---:|
| 10 (floor) | $0.031 | $0.02005 |
| **15 (default)** | **$0.046** | **$0.03005** |
| 25 | $0.076 | $0.05005 |
| 50 | $0.151 | $0.10005 |

**No conflict with the provider minimum.** $0.046 is 46× the actor's $0.001
floor, so the case the brief said to stop and report on does not arise.

**Against the exposure it replaces:** ~1,000 results × $0.002 = **$2.00**. The
ceiling is **~43× below** that.

### Fail closed

`requirements.txt` now pins `apify-client>=3.1.0` (was `>=1.7.0`) — the version
the parameter was verified against. But a deployed environment is not a
requirements file, so `scrape_search` introspects the SDK before every run:

```python
if ceiling is not None:
    if not charge_ceiling_supported(actor):
        raise RuntimeError(...)          # never starts
    start_kwargs["max_total_charge_usd"] = ceiling
```

An older client raises **before** the run starts, so the per-search `try/except`
in `main()` records one failed search rather than one unbounded spent one. The
tempting "handle it" — drop the argument and run anyway — is precisely the
outcome the guard exists to prevent, so
`test_a_client_without_the_parameter_fails_closed` asserts nothing was started.

**Indeed and Naukri get no ceiling.** Their inputs are unchanged, and Naukri's
own `minimalMaxTotalChargeUsd` is $0.10 against a per-run model Sweep already
treats as a floor — adding a ceiling there would be a second, unmeasured
behaviour change in a patch scoped to LinkedIn. `ACTOR_CHARGE_MODEL` simply has
no entry for them, and `max_charge_usd` returns `None`.

## 5. `autoConvertToAiSearch` — V2-A's recommendation reversed

**Decision: leave it unchanged. Sweep does not send the field.**

V2-A proposed setting it `false` to preserve classic URL-filter semantics. The
actor's own text says otherwise, and I got this wrong in V2-A:

> "LinkedIn is now forcing AI job search which removed many classic filters
> (experience, job type, workplace, salary, sort, etc.) When this option is
> enabled, the filters in old search URLs are converted to natural language and
> appended to search keywords. **Date posted, Company, Easy apply, and Under 10
> applicants stay as URL filters on AI search.**"

Three consequences:

1. **The removal is LinkedIn's, not the actor's.** `f_WT` and `f_E` are not
   being weakened by a setting Sweep could turn off; they no longer exist as
   classic filters. `autoConvertToAiSearch=true` is the actor's *mitigation* —
   it converts them into natural-language terms so they still influence the
   search. Turning it off would most likely drop them entirely: an
   approximation traded for nothing.
2. **`f_TPR` and `f_C` are explicitly preserved as real URL filters.** So
   Sweep's recency window and company targeting — the two filters that matter
   most to cost and correctness — are enforced either way. **V2-A §0.1 and §13
   stated that `f_TPR` becomes a natural-language keyword. That was wrong**, and
   the V2-A document has been corrected in place with a pointer here.
3. **The `false` behaviour is not documented at all.** Per the brief's own rule,
   an ambiguous contract is not a licence to guess. The setting is left alone.

`AutoConvertToAiSearch` in `test_paid_contract.py` pins the decision *and* its
reasoning, and asserts `f_TPR` and `f_C` are still in the built URL — so the
next person to consider flipping this has to read the contract first.

## 6. Budget interaction — the ceiling is additive

Traced end to end; nothing in this chain was redesigned.

| Stage | Behaviour | Changed? |
|---|---|---|
| Plan cardinality (`build_search_plan` → `plan_for_site`) | 18 LinkedIn + 72 Indeed at defaults | **No** |
| Intended depth (`effective_search`) | 15, floored at 10 | **No** |
| Plan estimate (`sweep/plan.py:cost`) | reads `--dry-run --json` `max_results`; $6.966 at defaults | **No** |
| Pre-run guard (`scraper.py` paid loop) | `if budget is not None and spent >= budget: stopped_early` | **No** |
| **Per-run provider ceiling** | `maxTotalChargeUsd` on each LinkedIn start | **NEW — additional layer** |
| Post-run accounting | `account_usage_usd` delta, falling back to `usage_total_usd` | **No** |

The two layers bound different things and neither substitutes for the other:

- The **provider ceiling** bounds *one run*, at the provider, before Sweep can
  observe anything. It is the layer that did not exist.
- The **sweep budget** bounds *the whole sweep*, locally, between runs. It is
  still the only thing that does. 90 runs each capped at $0.046 is $4.14 —
  asserted as `test_per_run_ceiling_cannot_cover_the_whole_sweep`, precisely so
  the two are never confused.

The pre-run guard's known weakness — it cannot reserve the next run's charge —
is *mitigated* but not removed: the worst single overshoot past the cap is now
one ceiling ($0.046) instead of one unbounded run (~$2.00). Redesigning the
accounting is explicitly out of scope.

## 7. Tests

`sweep/tests/test_paid_contract.py`, **32 tests**, sockets denied in
`setUpModule` so a test that reached Apify fails rather than spends.

| Group | Pins |
|---|---|
| `LinkedInDepthField` (7) | `limitPerSource` sent and carries effective depth; `count` retained and always equal; floor unchanged; depth not silently increased; no stray input keys |
| `AutoConvertToAiSearch` (2) | the field is not sent; `f_TPR` and `f_C` still ride in the URL |
| `ChargeCeiling` (7) | $0.046 at depth 15; scales with depth; ≥43× below the $2.00 exposure; covers an 18-row overshoot; respects `minimalMaxTotalChargeUsd`; `None` for Indeed/Naukri; is a `Decimal`, not a float |
| `CeilingReachesTheRunInvocation` (6) | the ceiling reaches `start()`; matches *billed* not *asked* depth; fails closed on an old client with nothing started; **walks `ACTOR_CHARGE_MODEL` rather than naming linkedin**, so a future site cannot be added and forgotten; the support probe is checked against the real SDK |
| `OtherSitesUnchanged` (4) | Indeed and Naukri inputs asserted as whole literal dicts; neither gained a depth or ceiling field; the ceiling never appears in `run_input` |
| `PlanAndBudgetUnchanged` (6) | 18/72 cardinality; query/location expansion; depth 15; `cost()` total still $6.966 / 90 searches; the engine's budget guard still present; per-run ceiling cannot cover the sweep |

One existing test was **strengthened**: `test_plan.py`'s `BILLED_KEY` mapped
linkedin to `count` — the field the actor ignores. It now maps to
`limitPerSource`. Both carry the same value, so it would have passed either way;
pointing it at the authoritative field is the difference between checking the
contract and checking ourselves.

### Mutation checks — all six caught

| Mutation | Result |
|---|---|
| Remove `limitPerSource` | **FAILED** (2 failures, 5 errors) |
| Compute the ceiling but never send it | **FAILED** (3) |
| Fail open instead of closed on an old client | **FAILED** (1) |
| Send `autoConvertToAiSearch: false` | **FAILED** (2) |
| Silently deepen `limitPerSource` to 10× | **FAILED** (5) |
| Put the ceiling in `run_input` instead of the start call | **FAILED** (4) |

Suites run: full `sweep/tests` (**945 tests, OK**), `scraper.py --demo`,
`python -m sources`, `python telemetry.py`, and
`bench/search_v2_paid_audit.py` (offline, sockets blocked, "paid calls: 0").

## 8. Remaining UNKNOWNs

1. **Whether this actor build honours `limitPerSource`.** The contract documents
   it; no run has confirmed it. Now bounded rather than unknown-and-unbounded.
2. **Whether it still reads `count`.** Unknowable without an old build. Harmless
   either way.
3. **How AI-search conversion changes result quality** for `f_WT`/`f_E`.
   Natural-language approximation is not the same as a filter, and Sweep's local
   arrangement and experience rules still run afterwards — so the cost is
   wasted paid rows, not wrong results. Size unmeasured.
4. **Whether the account is on the FREE tier.** The ceiling assumes the dearest
   published price, so a paid-tier account simply has more headroom.
5. **Whether an aborted-at-ceiling run returns partial data or nothing.**
   `scrape_search` discards a non-`SUCCEEDED` run's dataset, so a truncated run
   would contribute nothing and be recorded as a failure. The 1.5× overshoot
   allowance exists so this should not trigger on an honest run.
6. **Indeed and Naukri have no provider ceiling.** Both use documented fields
   Sweep sends correctly, so neither has LinkedIn's unbounded shape — but
   neither is provider-bounded either.
7. **Actor build drift.** Still unpinned; Sweep runs `latest`.

## 9. Safe paid validation plan — proposed, NOT run

The V2-A experiment (send `count=15` with no ceiling, unknown maximum) is
**withdrawn**. It is no longer worth exposing money to an UNKNOWN maximum when
the corrected input bounds it.

| | |
|---|---|
| **ACTOR** | `curious_coder/linkedin-jobs-scraper` |
| **RUNS** | 1 |
| **INPUT** | `{"urls": ["https://www.linkedin.com/jobs/search/?keywords=Software+Engineer&geoId=105214831&f_E=2&f_TPR=r1209600"], "limitPerSource": 15, "count": 15, "scrapeCompany": false}` — exactly what Sweep now sends |
| **START ARGUMENT** | `max_total_charge_usd=Decimal("0.046")` |
| **ESTIMATED CHARGE** | $0.03005 expected (15 results + one start event, free tier) |
| **ENFORCEABLE MAXIMUM** | **$0.046, enforced by Apify, not by an estimate.** This is the difference from the withdrawn experiment. |
| **ANSWERS** | Did the actor respect `limitPerSource` (row count == 15)? How many rows came back? What was the actual charge, from `chargedEventCounts` and the account delta? Were `f_TPR` and the geoId respected (dates inside 14 days, results in Bengaluru)? Actor startup / runtime / dataset-retrieval timing, now captured by V2-A telemetry. |

Run it with `SWEEP_SEARCH_V2_TELEMETRY=1` so the run ID, build ID, dataset ID,
poll count, timings, the ceiling and both cost figures are recorded — the paid
telemetry added in V2-A has never seen a real run.

**This was not executed and needs explicit approval.**
