# V2-C4 — the automatic cap holds the plan, LinkedIn's rank is recorded, and adaptive paid execution is evaluated in shadow

Date: 2026-09-24. Baseline: `ccd5720` (V2-C3.5, local, on top of `origin/main` =
`61681c9`; C0 `89c8daa`, C1 `a3b4bd2`, C2 `429b7b6`). HEAD was verified before
starting. Production runs V2-B5 with `SWEEP_SEARCH_V2_TELEMETRY=1`, Lever 1/4,
Greenhouse 1/4, `SWEEP_FREE_SOURCE_SHADOW=1` and `SWEEP_RESULTS_READY_EARLY=1`,
and `SWEEP_PAID_CONCURRENCY` is set nowhere. None of that is touched.

**Outcome: B — shadow / evidence only, no pruning promoted.** Three separable
changes ship:

1. **Plan-level budget authorisation.** The cap Sweep generates for a paid
   sweep now holds every provider ceiling of the plan it was generated for.
   Default plan: estimate **$6.966**, bounded exposure **$10.548**, generated
   cap **$10.55** (was $8.71, which let a reservation-safe engine start only
   76 of 90 searches). A cap that someone supplies is never raised.
2. **Provider-native rank.** Telemetry records LinkedIn's own `position` for
   every paid row, UNKNOWN where the provider gave none. `search_rank`, every
   output column and every output byte are unchanged. Indeed publishes no
   rank: UNKNOWN.
3. **Adaptive policy engine, shadow only.** `SWEEP_PAID_ADAPTIVE_MODE`
   (default `off`). `shadow` records what twelve candidate policies would have
   decided, from decision-time state only, and prices each decision by what it
   would have saved **and lost** against the final result. `enforce` exists
   but nothing has passed the evidence gate, so it runs as shadow.

Nothing was deployed, no flag was set anywhere, and **no live paid call was
made** (C4 intended exposure $0.00).

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a dated
probe), **VERIFIED** (directly observed code, configuration, provider record or
documentation), **INFERRED** (a model with stated assumptions), **UNKNOWN**
(evidence unavailable — not the same as zero).

## 0. Headline findings

1. **VERIFIED — the cap was generated, displayed, and never chosen by anyone.**
   `sweep/app.py` computes `max_spend_usd` from the estimate, shows it as the
   "hard cap" on Configure and Confirm ("Sweep stops at $X whatever happens"),
   and POST /run stamps it into the profile. No screen, form field or API takes
   a cap from the user (§2).
2. **MEASURED offline — the generated cap now authorises every audited plan
   in full.** Default 90/90 (was 76/90), public `india` 24/24 (was 20/24),
   `global` 36/36 (was 31/36), cohorts 8/8 (was 7/8). The serial loop admits
   exactly what it did before in every plan (§5).
3. **VERIFIED — the ceilings come from the plan, not a constant.** The engine's
   own `--dry-run --json` now carries `charge_ceiling_usd` per site, from the
   same `max_charge_usd` that `scrape_search` applies. `plan.cost` sums it into
   `bounded_exposure` beside the estimate, never mixed with it. Depth and
   pricing-model changes move it (pinned) (§4).
4. **VERIFIED — LinkedIn's rank is recorded; `search_rank` still means dataset
   index.** `provider_positions` is aligned token for token with each unit's
   C1 trace. Missing, malformed or duplicated positions are UNKNOWN, never the
   dataset index (§6).
5. **VERIFIED — Indeed has no provider rank.** Build 0.0.111's documented
   dataset schema has 24 fields, none a rank, position, page or index. Indeed
   depth analysis is UNKNOWN and no Indeed depth policy exists (§6.3).
6. **MEASURED offline — shadow is off, request for request.** At serial and at
   1, 2 and 4 workers, off, shadow and enforce make the same starts, inputs,
   depths, ceilings, reservations and request log, and write the same CSV,
   JSON, seen ledger, done ledger, ranking and duplicate survivor (§12).
7. **MEASURED offline — every stopping and skipping family loses top-10 jobs
   somewhere.** On eleven adversarial fixtures, each marginal-yield, top-K and
   provider-stop candidate loses the sweep's rank-1 job in at least one; the
   pair-skip candidates lose jobs the rule could not see (§10).
8. **MEASURED on captured live traces — depth reduction would cost real
   jobs.** Re-indexed by LinkedIn's position, the five captured single-search
   units reproduce V2-C3's table exactly (final 20 / 21 / 22 by position
   bucket). Depth 10 would have dropped 22 of 63 final rows (upper bound), and
   depth 5 would have dropped 43 (§9).
9. **VERIFIED — no captured sweep can exercise a stopping rule.** The largest
   recorded paid sweep has two searches, so no streak of two or more can form
   in any of them. There is no real sample for any C4 policy. C5 is that
   sample (§10.3).
10. **Promoted: none.** `PROMOTED = ()`. No enforcement threshold was invented
    (§11).
11. **Spend.** $0.00 intended, $0.00 actual. The ledger still reads **$0.773
    intended / $0.51035 actual** of the shared $2.00 (§17).

## 1. Scope

C4 has three workstreams, kept separable:

| Workstream | What shipped | Production effect once deployed |
|---|---|---|
| 1. Plan-level budget | `charge_ceiling_usd` in the dry run; `bounded_exposure` in `plan.cost`; `spend_cap_for` holds it | The public/console hard cap rises to the plan's ceilings (default $8.71 → $10.55). Serial admission is unchanged |
| 2. Provider rank | `provider_positions` per paid unit; `by_provider_position` in `paid_summary`; budget coverage in `paid_summary` | Telemetry only (flag already on in production) |
| 3. Adaptive engine | `paid_adaptive.py`, `paid_adaptive` telemetry section, replay tool | None: `SWEEP_PAID_ADAPTIVE_MODE` is set nowhere, default off |

It does not change scoring, ranking, dedupe, Search Preferences, provider
order, polling, Naukri, LinkedIn or Indeed ceilings, or C2 reservation. It does
not batch. It does not enable C2 or adaptive execution anywhere.

## 2. Budget semantics today — audit (VERIFIED)

| Question | Answer |
|---|---|
| Where does the user see estimated cost? | Configure: the meter's "Estimated cost" (`est.total`, live). Confirm: "Estimated cost" (public) or "Total to spend" (console), the headline figure. Both from `plan.cost()` |
| Is `max_spend_usd` visible? | Yes, as the "hard cap": Configure's meter fact "hard cap $X", Confirm's meter "Hard cap $X", and the copy "Sweep stops at $X whatever happens, even if searches are left — so a wrong estimate cannot overspend your account" (public) / "Hard stop at $X" (console) |
| Does the user explicitly approve a maximum? | They approve the SWEEP, on a screen that states the generated cap. They never choose or type it |
| Purely internal? | No. It is displayed, and it is what the engine enforces |
| Can any API or client supply one? | No UI control. `sweep.logic._configure_overrides` has no such field (pinned: a posted `max_spend_usd` is ignored). The worker's `/v1/runs` renders `prefs.max_spend_usd` verbatim, and Render only ever sends the generated value. Hand-written profiles (`profiles/*.py`, e.g. `global_remote` $4.50) and developer tools (`--sweep-budget`) set explicit caps. The engine reads `SETTINGS["max_spend_usd"]` and never modifies it |
| Free / paid tiers | Two: a Free Sweep (`free_only`) gets cap $0.00 and runs no paid actor; every paid sweep gets `spend_cap_for`. There is no separate "Deep" tier. One formula |
| Can a profile arrive with a manual cap? | Yes, on the CLI path. It is respected as given (§3.3) |
| Which code computes what | Estimate: `sweep/plan.cost` (`config.SITE_RATES` × depth / `SITE_RATE_BASIS`). Profile cap: `sweep/app.spend_cap_for`, called once in `costed()`, stamped by POST /run through `make_profile.render`. Ceilings: `scraper.max_charge_usd` (`ACTOR_CHARGE_MODEL`), now also exported by the dry run |

**Therefore the cap is auto-generated in every product path** and may be
corrected. That is what C4 does. Explicit caps exist only where a person or a
tool wrote one into a profile. Those are never raised.

## 3. Three different numbers

| Name | Meaning | Where it lives | Never called |
|---|---|---|---|
| **Estimated cost** | Expected spend at measured per-search rates | `plan.cost()["total"]`; the screens' headline | a maximum |
| **Authorisation exposure** | The most the provider can charge the planned bounded searches: Σ provider-enforced ceilings | `plan.cost()["bounded_exposure"]`; telemetry `planned_bounded_exposure_usd`; C2's committed reservations | expected spend |
| **Actual cost** | What the providers finally charge | Settled run records, read after the fact by the research probe; production sees only provisional readings (`cost_observations`, `final: false`) | the account delta at the engine's read (it lags) |

The default plan illustrates the gap. It is estimated at $6.966, has $10.548 of
ceilings, and on the C3.5 evidence would settle near $7.02 (18 × ~$0.030 +
72 × $0.090; INFERRED).

### 3.1 Unbounded providers

A site with no charge model (Naukri) has `charge_ceiling_usd: null`. It is
counted in `unbounded_searches` and priced by its estimate
(`unbounded_estimate`). That figure is **not** a maximum and is never added
into `bounded_exposure`; mutation H is caught.

### 3.2 The public cap bug

`spend_cap_for(estimate) = max($0.50, 1.25 × estimate)` priced an Indeed
search at $0.1125 against its $0.135 ceiling and a LinkedIn search at $0.03375
against $0.046. Under C2's full-ceiling reservation, the default plan stopped
at 76 of 90. That was a planning bug, not a reason to weaken a ceiling
(C3.5 §13).

### 3.3 Explicit caps

A supplied cap is the budget. Under C2 the engine starts in plan order until
the next ceiling does not fit (C2's existing rule). Serially it keeps the old
`spent >= budget` test. Pinned: an Indeed plan with $0.405 of ceilings under a
profile cap of $0.20 records `budget_usd: "0.2"`, reports
`budget_holds_planned_bounded_exposure: false` and starts one search at 1 and 2
workers. Mutation G (the engine raising a supplied cap) is caught.

## 4. The generated cap

```
legacy  = round(max($0.50, 1.25 × estimate), 2)                 # unchanged
need    = bounded_exposure + 1.25 × unbounded_estimate          # Decimal
cap     = max(legacy, ceil_to_cent(need))   if bounded_exposure > 0
        = legacy                            otherwise
```

- `bounded_exposure` = Σ over sites with a ceiling of searches × the engine's
  per-search ceiling at the plan's depth, from the dry run.
- Rounded **up** to the cent, so the cap can never sit below the exposure it
  must hold. Mutation Q (round to nearest) is caught.
- With no known ceiling (a plan from an engine older than C4) it is the
  estimate-based cap it always was, so a version-skewed worker degrades to
  today's behaviour.
- Priced on what is left to run (`remaining_plan`), as the estimate already
  was: the engine skips today's finished combos.
- A free sweep stays at $0.00.

It is the brief's candidate formula with the $0.50 floor and the 1.25×
headroom kept from the existing product semantics. It is not a copy: the
unbounded term is priced only for unbounded searches, and the bounded term is
the plan's own ceilings, not an estimate.

## 5. The cap, recomputed through current code (MEASURED offline)

`python -m bench.search_v2_paid_concurrency --public-cap` runs the real
`plan.cost`, `spend_cap_for`, `max_charge_usd` and `PaidExposure` over V2-C3's
audited plans
([evidence](search-v2-evidence/c4-public-cap-arithmetic.json)):

| Plan | Searches (LI + IN + NK) | Estimate | Bounded exposure | Unbounded est. | Cap before → **C4** | C2 admits before → **C4** | Serial (flag off) |
|---|---|---:|---:|---:|---:|---:|---:|
| repository defaults | 18 + 72 | $6.966 | $10.548 | — | $8.71 → **$10.55** | 76 → **90** | 90 → 90 |
| public `india` | 12 + 12 | $1.404 | $2.172 | — | $1.75 → **$2.18** | 20 → **24** | 24 → 24 |
| public `global` | 18 + 18 | $2.106 | $3.258 | — | $2.63 → **$3.26** | 31 → **36** | 36 → 36 |
| public `remote` | 2 + 2 | $0.234 | $0.362 | — | $0.50 → **$0.50** (floor) | 4 → 4 | 4 → 4 |
| cohort software_fullstack / react_native | 4 + 4 | $0.468 | $0.724 | — | $0.59 → **$0.73** | 7 → **8** | 8 → 8 |
| cohort business_salesforce | 6 + 6 + 6 | $3.702 | $1.086 | $3.00 (6) | $4.63 → **$4.84** | 18 → 18 | 18 → 18 |
| cohort multiple_roles_locations | 16 + 16 + 8 | $5.872 | $2.896 | $4.00 (8) | $7.34 → **$7.90** | 40 → 40 | 40 → 40 |

The test matrix pins, through the engine's dry run in-process:

- default, India, global and remote;
- LinkedIn only ($0.828 → cap $0.83) and Indeed only ($9.720 → $9.72);
- no paid provider (exposure 0, the $0.50 floor);
- Naukri in the plan (bounded $1.086 and unbounded $3.00 kept apart, cap
  $4.84);
- depth 25 ($17.568);
- an Indeed price change to $0.009 (ceiling $0.203, cap $15.45);
- rounding up;
- an older engine's plan (legacy $8.71);
- and, through the console's own Confirm fixture, /confirm showing $10.55 and
  POST /run writing `"max_spend_usd": 10.55` into the profile.

**User-visible change once deployed.** The "hard cap" shown on Configure and
Confirm rises (default $8.71 → $10.55). The estimate does not move. Every
bounded plan's worst-case charge was already the sum of its ceilings; the
generated cap now says so instead of stopping short of it. With C2 off
(production), the serial guard stops on the account delta, which for these
plans settles near the estimate, so executed work is unchanged in practice.

**Public enablement status.** Unchanged: C2 and adaptive execution are enabled
nowhere. C3.5 §13's blocker (the cap could not hold ceilings) is removed by
this change. Enabling C2 publicly is C5's decision after its validation.

## 6. Provider-native position

### 6.1 LinkedIn

`scraper.provider_positions(site, links)` reads each result's `link`
`position` parameter — the field V2-C3 measured (90 of 90 items in six
single-URL runs; a permutation of 1..15 per run).

| Input | Recorded |
|---|---|
| `position=1`, `position=15` | 1, 15 |
| `position=16` (overshoot) | 16 — kept; counted as `beyond_requested_depth` |
| missing, `position=`, `abc`, `0`, `-3`, `7.5`, `3&position=4` | UNKNOWN |
| no link (None, "", a number) | UNKNOWN |
| two rows claiming the same position | both UNKNOWN (one claim is wrong; nothing says which) |
| Indeed, Naukri | UNKNOWN |

Stamped on the row only while telemetry is recording, under a key `to_output()`
never reads (the `_native` bargain, as C1's `_paid_unit`). C1 records it per
unit as `paid_units[].provider_positions`, aligned with the trace. `paid_summary.by_provider_position`
buckets rows, eligible, eligible-positive, final, final-positive and
final-marginal by five-wide provider-position bucket per provider, with an
`unknown` bucket.

### 6.2 `search_rank`

Unchanged, and still the dataset index. That index is the actor's push order
(V2-C3 §14), and the comments in `scrape_search` and `to_output` now say so.
Pinned end to end: a search whose dataset order is the reverse of LinkedIn's
rank records `provider_positions [5, 4, 3, 2, 1]`. Each output row's
`search_rank` stays its dataset index. The CSV and JSON are byte-identical with
telemetry on and off. Mutations A (the rank used as the position), B (a missing
position filled from the index) and S (telemetry filling it) are all caught.

**Old analyses.** C1's yield-by-position table (C1 §19) was bucketed by push
order. C3's re-analysis by `position` is the correct LinkedIn depth view. C1's
evidence is not rewritten.

### 6.3 Indeed — UNKNOWN (VERIFIED)

The public build record of `misceres/indeed-scraper` 0.0.111 (free,
unauthenticated GET; [evidence](search-v2-evidence/c4-provider-rank-audit.json))
documents 24 dataset fields. None is a rank, position, page or index:
`positionName` is the job title and `url` is `viewjob?jk=<id>`. Dataset order
is what the actor pushed. Indeed rows are therefore UNKNOWN. The depth
evaluation reports Indeed as `UNKNOWN`, and the depth candidates cannot fire on
UNKNOWN positions. No Indeed call was made to ask.

### 6.4 Progressive depth — UNKNOWN, not implemented

Neither actor documents a continuation: LinkedIn takes `urls` +
`limitPerSource`, Indeed `maxItemsPerSearch`, and neither has an offset, page
or cursor. A second run at a larger depth is a new purchase that may repeat or
reorder the first. So only **static** depth is evaluated (counterfactually),
and progressive depth is not built.

## 7. Adaptive architecture

`paid_adaptive.py` (new, pure: imports `os`, `time`, `collections`, `decimal`
and three names from `telemetry`; pinned).

```
engine (scraper.py)                      paid_adaptive
───────────────────                      ─────────────
main: paid_plan(units, budget) ───────▶  start(plan, budget, job_key)   reads the mode once
finalize: paid_outcome(...)    ───────▶  capture(eligible, final)       holds refs, O(1)
serial loop / C2 integrate(e)  ───────▶  integrated(unit, rows, ok,     one OBSERVATION per
   (plan order, after the checkpoint)        started, committed)        integrated search
main, after the result is written ────▶  finish(record, max_charge_usd) DECISIONS + EVALUATION
                                          → telemetry.paid_adaptive(section)
```

- **Observation** (decision time). Read off the checkpoint the engine makes
  after each integrated search, in plan order, whatever finished first. It
  holds only what the sweep had then: no later search, and no free rows,
  which come after the paid phase.
- **Decision.** `fires(family, params, history)` is handed
  `observations[:i + 1]` and nothing else. `simulate()` walks the sweep as an
  online policy would.
- **Evaluation** (evaluation time). The ground truth is the final finalize
  pass: every eligible row, paid and free. A counterfactual keeps the rows a
  decision would have bought and re-ranks them with the engine's rule. The
  projection ranking is pinned equal to `scraper.rank_rows`, ties and unkeyed
  rows included.

**Modes.**

| Mode | Behaviour |
|---|---|
| `off` (default; anything unrecognised) | exactly C3.5/C2 — `start` holds nothing and every hook is one `is None` test |
| `shadow` | the same paid work, byte for byte; writes `paid_adaptive` into the telemetry record. Needs `SWEEP_SEARCH_V2_TELEMETRY` (without a record there is nowhere to write) |
| `enforce` | runs as `shadow`, with a note, because `PROMOTED` is empty. Default-disabled and set nowhere |

One mode variable rather than booleans, as the brief preferred: the three
states are exclusive, and "enforce without shadow" has no meaning.

**Failure isolation.** `integrated()` cannot fail a search: an exception stops
observation for the sweep and is reported as `error` in the section. `finish()`
never raises; the result is already written when it runs.

## 8. Policy inputs (decision time, counts only)

Per integrated search: provider, location mode, keyword fingerprint (a hash of
provider | keyword | company, for pairing), requested depth, status, raw,
eligible, eligible-positive, final and final-marginal **at that checkpoint**,
rows repeating an earlier paid search, positions known, final and marginal by
provider-position bucket, top-10/20/50 movement (`entered`, `displaced`,
`moved`, `entered_from_unit`, `entered_marginal`), searches started so far,
committed exposure (C2's ledger, or the ceilings run so far serially), and
elapsed ms. No title, company, URL, query or description. No model call.

## 9. Depth — evaluated by provider position only

For every provider with positions, the evaluation reports prefixes 5, 10 and
the requested depth. For each it gives eligible, eligible-positive, final,
final-marginal, top-10/20/50 contribution, and for the shorter prefixes the
counterfactual loss and savings. An UNKNOWN position counts as **not** bought,
so the loss is an upper bound. The note "a lower-depth run is NOT proven to
return exactly these rows" travels with it.

**Captured live traces (MEASURED; 5 LinkedIn single-search units, 75 rows).**
Re-indexed by LinkedIn's `position` via V2-C3's map, the replay reproduces
C3 §14 exactly:

| LinkedIn position | Rows | Eligible | Eligible + | Final | Final + | Repeats |
|---|---:|---:|---:|---:|---:|---:|
| 1–5 | 25 | 22 | 14 | 20 | 12 | 3 |
| 6–10 | 25 | 21 | 15 | 21 | 15 | 0 |
| 11–15 | 25 | 22 | 14 | 22 | 14 | 0 |

| Static depth (counterfactual) | Rows not bought | Final rows lost (upper bound) | of them positive |
|---|---:|---:|---:|
| 15 (baseline) | 0 | 0 | 0 |
| 10 | 25 | 22 of 63 | 14 |
| 5 | 50 | 43 of 63 | 29 |

The upper bound is because a final row past the cut may have survived through
another search that also had the job. The traces carry no score or job key, so
top-K loss is UNKNOWN for them.

**Fixtures.** Front-loaded (every useful job at 1–5): depth 5 loses no marginal
and no top-10 job. Back-loaded (the useful job at 15): depth 10 loses rank 1 and
several top-10 jobs, and `depth_prefix:d=10` never fires. One trace favouring a
cut is not evidence for it.

**Decision: no depth policy.** LinkedIn stays at depth 15.

## 10. Candidate policies and what they would cost

### 10.1 Families (a sensitivity grid, not proposals)

| Family | Fires when (decision time) | Would |
|---|---|---|
| `marginal_streak:n` (2, 3, 5) | the last n completed searches added no final-marginal job | stop the sweep |
| `top_k_stable:k,m` (10/3, 20/3, 20/5) | the last m completed searches moved nothing into the top k | stop the sweep |
| `provider_marginal_streak:n` (3, 5) | the provider's last n added no marginal job | skip that provider's rest |
| `remote_after_places:m` (1, 2) | m earlier Remote twins added nothing beyond their place twins | skip that provider's later Remote twins |
| `depth_prefix:d,m` (5/3, 10/3) | m searches in a row, all positions known, had no marginal job past d | use depth d for that provider's rest |

Every value is a probe point spanning the range a reviewer would ask about. None
is an enforcement threshold.

### 10.2 Fixtures (MEASURED offline; [evidence](search-v2-evidence/c4-adaptive-replay.json))

`python -m bench.search_v2_paid_adaptive` builds eleven adversarial sweeps as
projections and runs them through `paid_adaptive.replay()`, which makes the live
path's observations.

| Fixture | What it shows |
|---|---|
| 1 all useful | no stop or skip candidate fires |
| 2 early then dry | `marginal_streak:n=2` stops after search 5 and loses nothing, avoiding 5 searches and $0.230 of exposure — the case a stopping rule is for |
| 3 dry spell (5) then value | every `marginal_streak` and `top_k_stable` candidate stops before search 9 and loses its rank-1 job and 4 top-10 jobs. Any N-zeros rule is beaten by a dry spell of N |
| 4 duplicate-heavy, late unique | 80% repeats per search, yet each adds a top job: no marginal rule fires. A duplicate rate is not redundancy |
| 5 top-20 stable, total growing | `top_k_stable:k=20,m=3` loses no top-20 job but 6 final jobs |
| 6 top-20 stable, then displaced (the brief's) | `top_k_stable:k=20,m=3` stops after search 4. It loses the new rank-1 job and 4 top-10 jobs |
| 7 provider A low, B high | `provider_marginal_streak:n=3` skips LinkedIn's last 2 at no loss. `marginal_streak:n=2` stops everything and loses 18 Indeed jobs, 7 of them top-10 |
| 8 pairs, high overlap (100, 100, 90, 90, 90%) | `remote_after_places:m=1` fires after the first pair and loses the 3 jobs the later twins still added |
| 9 pairs, zero overlap | skipping any Remote twin loses all 10 of its jobs. The rule never fires |
| 10 depth front-loaded | depth 5 loses nothing marginal |
| 11 depth back-loaded | depth 10 loses the rank-1 job |

Across the fixtures, every stop and provider-skip candidate loses its sweep's
rank-1 job in at least one fixture. The pair-skip candidates lose jobs in one.
The depth candidates lose nothing only because they fire where nothing lies past
the cut, and not where something does.

### 10.3 Captured real traces (MEASURED)

C1 probe A (2), C1 probe B (1), the C2 canary (2), C3.5 Phase 1 (1) and C3.5
Phase 2 (2). The C3 batch canary is excluded: one start carried two searches
and the actor chose the owner of a shared job. **No recorded sweep has more than
two paid searches.** So no streak of two or more can form in any of them, and
no stop, skip or depth candidate is exercised by real data. Every LinkedIn
unit contributed 12–13 of its 15 raw rows as final-marginal. The three Indeed
units contributed 1, 2 and 0 final jobs of 15 each, under the default config's
worldwide-remote scope, which removes India-onsite rows (C3.5 §10). Top-K is
UNKNOWN (no scores in the traces). India/Remote pair overlap is UNKNOWN on real
data: no live sweep has run a pair.

## 11. The evidence gate

A policy may run under `enforce` only when **all** of these hold, recorded in
this document with the evidence:

1. Deterministic fixtures pass, including every adversarial one aimed at its
   family.
2. No invariant is violated: plan order, C2 reservation, done-ledger meaning,
   the provider ceiling.
3. It has been evaluated against every available captured real trace, and at
   least one real full-plan sample (C5) exercises it.
4. Its loss metrics are reported: final, final-marginal, top-10/20/50 and best
   lost rank, against savings.
5. It depends on no future information (the prefix property, pinned).
6. Output stays deterministic at every worker count.
7. Budget safety is unchanged: skipping may lower exposure, never raise it.
8. Crash/resume semantics are defined, with skipped-by-policy state kept apart
   from `.done_combos`.
9. It shows no significant top-K loss **under a product-accepted threshold**.

**Result: no candidate passes.** Items 1 and 3 fail for every stop and skip
family (§10.2–10.3). Item 9 has no threshold: no product evidence defines an
acceptable top-10 or top-20 loss, and C4 does not invent one. Depth fails on
real data (§9). `PROMOTED = ()`, and `enforce` runs as shadow.

## 12. Shadow equivalence (MEASURED offline)

`test_paid_adaptive.ShadowIsOff` runs one mixed plan in each mode at each
worker count: 4 LinkedIn searches (two keyword × {place, Remote} pairs,
dataset order against LinkedIn's rank, a twin posting in every search), 2 Indeed
searches and a free source, with the first planned search finishing last. It
runs off, shadow and enforce at serial and at 1, 2 and 4 workers:

- **Requests.** The same starts (actor, input, ceiling), the same request
  counts by kind, and at serial and 1 worker the exact request sequence.
- **Actor inputs.** Every start carries depth 15 with its own ceiling ($0.046 /
  $0.135).
- **Output.** CSV, JSON, seen ledger and done ledger are byte-identical to
  serial-off, and the one Twin Works survivor is the same.
- **Reservations and telemetry.** C2's exposure snapshot and every unit's
  reservation state and amount are identical. C1's `paid_units` are equal and
  `paid_summary` is equal but for clocks.
- **The shadow section.** Only shadow and enforce write it; its decisions and
  losses are identical at every worker count.
- **Budget.** With a $0.10 budget, the same 2 starts and the same block in
  both modes.
- **Resume.** Skipped-done searches are not observed. The done ledger and
  the bytes are unchanged, and the output directory holds the same files.
- **Telemetry off.** Shadow without telemetry is a no-op.

Mutations C (shadow skips a search), D (shadow changes depth) and L (the
adaptive run bypasses the reservation budget) are caught.

## 13. No-future-information proof

- **Online prefix property.** For every candidate, every fixture and every cut
  i, `simulate(observations[:i])` equals the full run's decisions restricted to
  the first i searches.
- **A changed future changes no past decision.** Rewriting every observation
  after a firing point leaves the decision unchanged.
- **Live observation.** The observations of searches 1–2 are identical whether
  the plan has 2 searches or 6, so nothing later leaks into a checkpoint.

Mutation I (a decision reads the next observation) is caught.

## 14. Top-K and counterfactual metrics

For every executed search, `per_unit[]` records two counterfactuals.
`if_stopped_after` is "had the sweep stopped here", and `if_skipped_alone` is
the leave-one-out. Each gives `final_lost`, `final_marginal_lost`,
`top10_lost`, `top20_lost`, `top50_lost` and `best_lost_rank`. The stop case
also gives savings: logical searches avoided, `avoidable_as_scheduled` (only
searches not yet sent at the decision point, which differs under concurrency),
physical starts, maximum exposure avoided (ceilings), provisional cost avoided
(terminal-poll readings, never final), and unit wall ms. Jobs are counted by
job key, never by row: a job two skipped searches share is one lost job
(mutation J is caught). The section never reports savings without loss.

## 15. Structural pairs

`pairs[]` pairs each Remote search with the same-keyword place searches before
it, by keyword fingerprint (no query text). It records the Remote twin's
eligible jobs, its overlap with its place twins, and the loss if the twin
alone were skipped. C3's nine LinkedIn India/Remote pairs are the structural
candidates, and Indeed's Remote search is paired with its seven places.
Evidence today is fixtures only. Neither "skip Remote after India" nor the
inverse is shipped.

## 16. Privacy

Paid-row provenance keys (`_paid_unit`, `_provider_position`) exist only while a
record is open and never reach output. The `paid_adaptive` section holds unit
ids, fingerprints, counts and amounts. Pinned: no title, company, URL, query,
location or token marker appears in it. Job keys stay in memory. Mutation M (a
job key written to an observation) is caught.

## 17. Research ledger, live calls

**No live paid call and no account read.** The only provider contact was one
free, unauthenticated GET of Indeed's public actor record and build definition
(documentation). The ledger (`paid-research-ledger.json`) is unchanged:

| | Intended | Actual |
|---|---:|---:|
| C4 | $0.00 | $0.00 |
| Cumulative C0–C4 | **$0.773** | **$0.51035** |

Of the shared $2.00, 38.7% is intended.

## 18. Overhead (MEASURED offline)

`bench/search_v2_paid_concurrency.py --plans default --waits none --arms
serial,serial+shadow,2,2+shadow --repeats-none 2`
([evidence](search-v2-evidence/c4-adaptive-overhead.json)). Each arm is a
fresh child process running `scraper.main()` on the 90-search default shape
(18 LinkedIn + 72 Indeed, 15 synthetic rows each) against C2's scripted
stand-in. There are no provider waits, so this is local work only: the worst
case for relative overhead. Arms alternate in order. Host calibration was
3.51–3.62 ms/row throughout; no arm ran on a slowed host.

| Arm (median of 2) | Wall | CPU | Peak RSS | Record | Section | Requests |
|---|---:|---:|---:|---:|---:|---:|
| serial, off | 225.4 s | 223.5 s | 64.9 MiB | 312 KB | — | 361 |
| serial, shadow | 223.7 s (0.99×) | 222.8 s (1.00×) | 68.6 MiB | 513 KB | 129 KB | 361 |
| 2 workers, off | 5.93 s | 5.87 s | 67.7 MiB | 353 KB | — | 361 |
| 2 workers, shadow | 6.00 s (1.01×) | 5.95 s (1.01×) | 69.0 MiB | 554 KB | 129 KB | 361 |

- **Requests.** Exactly zero added: 90 starts, 90 polls and 91 account reads in
  every run, and each shadow arm's request log (as a multiset) equals its off
  twin's.
- **Output.** One output SHA-256 and one done ledger across all eight runs.
- **Cost.** Shadow costs ~0.08 s of CPU over 90 searches at two workers, where
  the observation of each checkpoint is proportional to the rows so far. The
  serial arm's difference is noise against its 223 s of re-scoring.
- **Size.** The real cost: the section is ~129 KB compact (~200 KB as the
  record is written, indented), mostly the 90 observations and the 90
  per-search counterfactual pairs. It is written only in shadow, which is set
  nowhere. Trimming it (e.g. dropping top-50) is a C5-review decision, not a
  correctness one.

## 19. Tests

`sweep/tests/test_paid_adaptive.py`, **69 tests**, ~10 s, sockets denied. It
runs `scraper.main()` in-process against V2-C2's `KeyedClient`, V2-C1's
fixtures and the console's own Confirm fixture, with no real credential.

| Group | Pins |
|---|---|
| `AutomaticCap` (12) | default, India, global, remote (floor); LinkedIn only, Indeed only, no paid provider; Naukri kept apart; depth 25; a price change; rounding up; an older engine's plan. Each also recomputes the ceilings from the planner independently |
| `TheAppStampsTheGeneratedCap` (1) | /confirm shows $10.55 on the real default plan; POST /run stamps it into the profile |
| `SuppliedCapsAreNeverRaised` (4) | a profile cap below exposure is the budget (serial, 1, 2 workers); above it, as given; no form field sets a cap; the worker renders a supplied cap verbatim |
| `ProviderPositions` (7) | valid 1 and 15; malformed; missing; no link; duplicate; past the depth; Indeed and Naukri |
| `ProviderPositionsEndToEnd` (5) | reversed push order recorded as the provider's rank; `search_rank` still the dataset index; output bytes identical with telemetry on and off; the summary buckets; UNKNOWN and Indeed counted as unknown |
| `ShadowIsOff` (10) | §12 at serial and 1/2/4 workers for off, shadow and enforce: requests, inputs, depths, ceilings, bytes, ledgers, survivor, reservations, C1 record, the section, schedule-independent decisions, enforce's note, telemetry off, no new file |
| `BudgetSafetyUnderShadow`, `ResumeUnderShadow` (2) | the same block under a tight budget; a resume observes only what it ran |
| `TheRankIsRankRows` (1) | the projection ranking equals `scraper.rank_rows` on ties, duplicates and unkeyed rows |
| `NoFutureInformation` (4) | §13 |
| `LossesAreCountedAgainstTheWholeResult` (8) | the brief's top-K fixture; the dry spell; the genuinely dry tail; all useful; duplicate-heavy; stable top-20 vs total; provider stop; a job shared by two skipped searches counted once |
| `StructuralPairs` (2) | high and zero overlap |
| `DepthByProviderPosition` (5) | front- and back-loaded; Indeed UNKNOWN; live positions not push order; the captured traces reproduce C3's table |
| `ModeDefaults` (4) | off unless asked; nothing promoted; no section without the variable; no deployment file sets it |
| `Privacy` (1) | no title, company, URL, query, location or token in the section (case-insensitive) |
| `ReplayToolIsUnreachable` (3) | the tool's imports; the C0 scan finds nothing to classify in it; `paid_adaptive` imports no engine |

**Changed elsewhere.** C1's `test_every_new_key_is_known` lists the two additive
`paid_units` keys (`keyword_fp`, `provider_positions`). The two benches that
called `spend_cap_for(float)` now pass the costed plan.

## 20. Mutation checks

Each mutation is one exact edit, applied alone. Six suites run
(`test_paid_adaptive`, `test_indeed_bounded`, `test_paid_concurrency`,
`test_paid_observability`, `test_paid_contract`, `test_paid_dev_guard`); then
the file is restored and its SHA-256 compared.
[Results](search-v2-evidence/c4-mutations.json). A–O are the brief's; P, Q and
S are extra.

| # | Mutation | Result | Among the failures |
|---|---|---|---|
| A | `search_rank` used as LinkedIn's provider position | **3 fail** | `ProviderPositionsEndToEnd.test_the_record_holds_the_providers_rank_in_dataset_order`, `DepthByProviderPosition.test_live_positions_not_push_order` |
| B | a missing position falls back to the dataset rank | **3 fail** | `ProviderPositions.test_missing_is_unknown_never_the_dataset_index`, `…test_malformed_is_unknown`, `…test_no_link_is_unknown` |
| C | shadow skips a real search | **6 fail** | `ShadowIsOff.test_the_same_requests`, `…test_the_same_bytes_ledgers_ranking_and_survivor`, `NoFutureInformation.test_live_observations_do_not_see_later_searches` |
| D | shadow changes the requested depth | **8 fail** | `ShadowIsOff.test_the_same_requests`, `…test_the_same_reservations`, `BudgetSafetyUnderShadow.test_the_reservation_ledger_is_the_same` |
| E | the automatic cap ignores Indeed's ceilings | **9 fail** | `AutomaticCap.test_the_default_plan_is_authorised_in_full`, `…test_indeed_only`, `TheAppStampsTheGeneratedCap.test_confirm_and_run_carry_the_ceiling_holding_cap` |
| F | the automatic cap ignores LinkedIn's ceilings | **9 fail** | `AutomaticCap.test_linkedin_only`, `…test_the_default_plan_is_authorised_in_full` |
| G | a supplied cap is silently raised | **39 fail** | `SuppliedCapsAreNeverRaised.test_below_the_plans_exposure_the_engine_runs_what_fits`, and every C2/C3.5 reservation test |
| H | the unbounded estimate is counted as a ceiling | **2 fail** | `AutomaticCap.test_an_unbounded_provider_stays_an_estimate_beside_the_ceilings`, `…test_an_older_engine_plan_keeps_the_estimate_cap` |
| I | a decision reads the next search's observation | **7 fail** | `NoFutureInformation.test_every_decision_is_the_one_its_prefix_makes`, `LossesAreCountedAgainstTheWholeResult.test_the_top_k_fixture_shows_the_rank_one_job_a_naive_stop_misses` |
| J | the top-K counterfactual counts one job twice | **3 fail** | `LossesAreCountedAgainstTheWholeResult.test_a_job_two_skipped_searches_share_is_one_lost_job`, `TheRankIsRankRows.test_equal_on_ties_duplicates_and_unkeyed_rows` |
| K | depth analysis reads the dataset push order | **1 fail** | `DepthByProviderPosition.test_live_positions_not_push_order` |
| L | an adaptive run bypasses C2's reservation | **1 fail** | `BudgetSafetyUnderShadow.test_the_reservation_ledger_is_the_same` |
| M | adaptive telemetry carries a job key (company + title) | **1 fail** (second pass) | `Privacy.test_the_section_holds_no_row_query_url_or_token` |
| N | the replay tool can build a real paid client | **3 fail** | `ReplayToolIsUnreachable.test_its_imports`, C0 `Reachability.test_every_file_that_can_reach_paid_code_is_classified` |
| O | the adaptive mode defaults to shadow | **3 fail** | `ModeDefaults.test_off_unless_asked`, `…test_a_sweep_without_the_variable_writes_no_section` |
| P | a duplicate position claim is trusted | **2 fail** | `ProviderPositions.test_a_duplicate_claim_makes_both_unknown` |
| Q | the cap rounds to nearest, not up | **3 fail** | `AutomaticCap.test_rounded_up_never_down`, `…test_the_public_india_plan` |
| S | telemetry fills an unknown position with the dataset index | **1 fail** | `ProviderPositionsEndToEnd.test_unknown_and_indeed_are_counted_as_unknown` |

**18 of 18 caught, after one test fix.** On the first pass M survived. The
privacy test compared upper-case markers, and a job key is the *normalised*,
lower-cased company and title, so a leaked key passed. The test now compares
case-insensitively, and M re-run alone is caught. Both passes are in the
evidence. Every mutated file (`scraper.py`, `sweep/plan.py`, `sweep/app.py`,
`telemetry.py`, `paid_adaptive.py`, `bench/search_v2_paid_adaptive.py`) was
restored byte-identical, at ~51 s per mutation.

## 21. Crash and resume

- **Off and shadow.** C3.5/C2 semantics exactly. Shadow persists nothing: no
  skip ledger, no second file. A resumed sweep observes only the searches it
  runs, so its section describes the remainder.
- **`.done_combos`.** Keeps one meaning: a search completed and its rows
  checkpointed.
- **Enforce.** Not implemented (it runs as shadow), so no skipped-by-policy
  state exists. A future enforced policy must keep that state apart from the
  done ledger and re-derive it on resume (gate item 8).

## 22. C5 preparation — NOT executed

**Default plan, current code.**

| | |
|---|---|
| Estimate | **$6.966** (18 × $0.027 + 72 × $0.090) |
| Bounded exposure | **$10.548** (18 × $0.046 + 72 × $0.135) |
| Generated cap | $10.55 |
| Expected settled | ~$7.0 (INFERRED from the C1–C3.5 settled charges) |

**Recommended C5 execution** (unchanged from C3.5 except the mode):

```
SWEEP_SEARCH_V2_TELEMETRY=1
SWEEP_PAID_CONCURRENCY=1
SWEEP_PAID_WORKERS=2
SWEEP_PAID_ADAPTIVE_MODE=shadow
max_spend_usd = 10.55        # explicit, >= $10.548; through bench.paid_guard with both keys
```

**Two preconditions this stage surfaced.**

1. **Budget.** $10.548 worst case is five times the shared $2.00 C1–C4 research
   ceiling, and the guard's `--max-usd` must cover $0.773 + $10.548 = $11.321.
   C5 needs its own explicit budget decision.
2. **Account credit.** The expected ~$7.0 exceeds one free-plan account's
   monthly credit if that is $5, and `_require_token` picks one account for the
   whole sweep. Its monthly headroom is UNKNOWN here (not read, by design). Read
   the account's limits (a free GET) before C5. An account that runs out
   refuses starts, and C2 counts each refused start as committed and failed.

**Questions one C5 run answers, and where.**

| Question | Field |
|---|---|
| Was full-plan authorisation correct? | `paid_summary.budget_holds_planned_bounded_exposure`, `paid_execution.exposure` (committed, blocked 0) |
| Estimate vs ceiling vs actual? | the probe's settled runs vs `planned_bounded_exposure_usd` vs the estimate |
| How many searches materially contributed? | `paid_units[].funnel.final_marginal`; `per_unit[].if_skipped_alone` |
| How many altered the top 10 / top 20? | `per_unit[].if_skipped_alone.top10_lost/top20_lost`; `observations[].top` |
| Which searches were mostly duplicate? | `observations[].acquired_repeat_of_earlier_paid`; `funnel.acquired` |
| Which India/Remote pairs overlapped? | `paid_adaptive.pairs[]` |
| Did useful yield decay with provider rank? | `paid_summary.by_provider_position`; `paid_adaptive.depth.linkedin` |
| Which provider had low marginal value? | `paid_summary.by_provider`; `observations[]` by provider |
| Where would each shadow policy have stopped, and what would it have lost and avoided? | `paid_adaptive.candidates[]` (decisions, loss, savings) |

## 23. Unresolved risks

1. **The grid is not evidence.** It spans plausible settings; C5 is one sample
   (one config, one day). A policy that looks safe on one run still needs the
   gate's product threshold.
2. **Decision-time top-K cannot see free rows.** Free sources run after the
   paid phase, so a live top-K observation is over paid rows only. The
   evaluation's ground truth includes them.
3. **A second copy of the rank rule.** `paid_adaptive.rank` re-implements
   `rank_rows` on projections. It is pinned equal by a test, so a change to the
   rule fails that test rather than drifting silently.
4. **`--only-new`** is not modelled in the counterfactual (it filters output
   after finalize).
5. **The displayed hard cap rises** for every bounded plan. It is the truthful
   worst case, but it is a larger number on a money screen.
6. **Version skew.** A worker older than C4 sends no `charge_ceiling_usd`, and
   Render then generates the legacy cap. That is safe serially and would
   under-authorise C2, which is off.
7. **Link format drift.** If LinkedIn's link changes, positions become UNKNOWN
   (the safe direction), visible as a growing `unknown` bucket.
8. **Serial `committed_usd`** in an observation is the ceilings of searches
   attempted, which includes a fail-closed search that never started. It is an
   upper bound.
9. **Telemetry size** grows by the section (§18).
10. **C5's account and budget** (§22).

## 24. Rollback

- To drop the adaptive engine, leave `SWEEP_PAID_ADAPTIVE_MODE` unset (it is
  set nowhere). It is then inert.
- To drop the cap change, revert `sweep/plan.py` and `spend_cap_for`. The dry
  run's extra field is ignored by the old costing.
- The provider-position fields are additive telemetry.

No environment, deployment or data change needs undoing.

## 25. Verification

- **Baseline.** HEAD `ccd5720` (C3.5 on `origin/main` `61681c9`), clean tree,
  at the start.
- **Sweep suite.** `python -m unittest discover -s sweep/tests -t .` —
  **1,449 tests, OK** (1,380 + 69), 228 s, on the final tree. It includes
  C0's reachability scan over the new files.
- **Deploy tests.** `python -m unittest deploy.test_sweep_worker
  deploy.test_modal_benchmark` — **42 tests, OK**.
- **Self-checks.** `python scraper.py --demo`, `python telemetry.py`,
  `python -m sources` and `python -m sources.concurrency` pass.
- **auto-apply.** `python -m unittest discover -s auto-apply/tests -t auto-apply`
  — 1,102 tests with the 2 `test_inference` healthz errors (the local
  inference service). **The same two errors reproduce on a clean worktree of
  `ccd5720`**, which also skips 2 corpus tests a fresh checkout lacks. This is
  environmental, as in every stage since B3.
- **Mutations.** 18/18 caught after one test fix; every file restored
  byte-identical (§20).
- **Replay and arithmetic.** The replay tool reproduces C3's captured
  position table exactly. The public-cap arithmetic runs through the real
  functions.
- **Benchmark.** Zero added requests; identical outputs and ledgers across all
  eight runs (§18).
- **Production isolation.** `git diff ccd5720 -- config.py deploy/
  sweep/runs.py sweep/worker_link.py sweep/public.py sweep/logic.py
  sweep/templates sweep/static sources/ rescore_from_apify.py auto-apply/
  requirements.txt render.yaml gunicorn.conf.py bench/paid_guard.py
  bench/search_v2_paid_probe.py docs/search-v2-evidence/paid-research-ledger.json`
  is **empty**. The changed production files:
  - `scraper.py`: the dry run's `charge_ceiling_usd`, `provider_positions`
    and its stamp, `keyword_fp`, and the adaptive hooks;
  - `telemetry.py`: additive fields and the section;
  - `sweep/plan.py`: exposure beside the estimate;
  - `sweep/app.py`: `spend_cap_for`;
  - `paid_adaptive.py` (new).

  No flag, environment or deployment change.
- **Paid.** None. One free, unauthenticated GET of Indeed's public actor and
  build record. No account was read.
- **Tokens.** No configured token's value appears in any new or changed file
  (checked by value, for `APIFY_TOKEN` and `APIFY_TOKEN_2`).
