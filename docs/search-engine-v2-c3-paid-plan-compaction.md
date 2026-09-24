# V2-C3 — paid plan compaction: audited, prototyped, measured live, not shipped

Date: 2026-09-24. Baseline: `429b7b6` (V2-C2; C0 `89c8daa`, C1 `a3b4bd2` and C2
are on `origin/main`). Production runs V2-B5 with `SWEEP_SEARCH_V2_TELEMETRY=1`,
Lever 1/4, Greenhouse 1/4, `SWEEP_FREE_SOURCE_SHADOW=1` and
`SWEEP_RESULTS_READY_EARLY=1` — none of which this stage touches.
Scope: **coverage-preserving physical compaction of the paid plan** — exact
duplicate elimination and batching several logical searches into one provider
start — where the provider contract safely permits it.

**Outcome: B.** The audit found no exact duplicate to remove. LinkedIn's actor
takes several URLs per run and names each result's source exactly, but it also
**deduplicates jobs across the sources of one run and back-fills** the source
that lost one. So which search a shared job belongs to is decided inside the
provider, not by the plan. Batching was prototyped, tested, run live once, and is
**not merged**: `scraper.py`, `telemetry.py` and every production file are
byte-identical to `429b7b6`. No flag was added. Nothing was deployed.

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a dated
probe), **VERIFIED** (directly observed code, configuration or provider record),
**INFERRED** (a model with stated assumptions), **UNKNOWN** (evidence
unavailable, which is not the same as zero).

## 0. Headline findings

1. **VERIFIED offline — no exact execution duplicate exists in any of 13 audited
   plans** (repository defaults, four synthetic cohorts, the public app's three
   work scopes, five generic profiles). Of the two ways the planner could make
   one, the public picker already removes one (it drops `Gurugram`, the same
   geoId as `Gurgaon`), and the other never arises (a bare `Remote` never meets
   `remote_only`). A repeated keyword is already skipped by `.done_combos`
   serially and by C2's wait-for-twin (§3). Nothing to build.
2. **VERIFIED from the provider — the LinkedIn contract is the one batching
   needs, on paper**: `urls` takes several URLs, `limitPerSource` is per URL,
   and `count` is "still accepted as a global max" (changelog 2026-08-08) — so a
   two-URL run needs `count` 30, not 15 (§5).
3. **MEASURED — every result names its source exactly**: an undocumented
   `inputUrl` equals the input URL on 90 of 90 items of the six C0–C2 runs and
   30 of 30 in the batched run, 0 unattributed (§6).
4. **MEASURED — the actor deduplicates across sources within one run.** The job
   the two canary queries shared when run separately (Backend Developer
   position 7, Full Stack Developer position 2 — in C1 probe A *and* the C2
   canary) appears in the batch only under Full Stack Developer, the
   later-planned search. Backend Developer's position 7 is missing and its
   position 16 took the place (§7). A shared job goes to whichever source the
   actor pushes first, and here that ran against URL order, so plan order no
   longer decides who owns it.
5. **That breaks C3's contract.** A search's rows now depend on what shares its
   start. C2's first-planned-wins duplicate tie cannot be restored, because the
   other copy is never emitted. C1's overlap signal inside a batch goes to zero.
   In the default plan, batch size 2 pairs exactly `keyword @ India` with
   `keyword @ Remote`, the pair most likely to share jobs, where which copy
   survives decides remote stamping and therefore whether an onsite or remote
   sweep keeps the job (§8).
6. **MEASURED — and it was not faster.** One two-URL run took **45.2 s** of actor
   runtime and a 50.3 s paid phase. C2 ran the same two queries as two
   concurrent starts in **14.6 s / 10.9 s** (19.2 s phase). It made the same 12
   SDK requests. It cost **$0.06005 against $0.0601**: one $0.00005 start event
   saved (§9). One run at a different hour — LinkedIn runtime varies ~3× between
   sessions — so the latency comparison is INFERRED direction, not a law.
7. **MEASURED — dataset order is not LinkedIn's rank.** Each result's link
   carries LinkedIn's own `position`; in every single-URL run it is a
   permutation of 1..15, scrambled in dataset order. So `search_rank` (the
   dataset index) is the actor's push order. C1's yield-by-position table was bucketed by it. Re-bucketed by
   `position`, the "no decay" conclusion still holds (§14).
8. **VERIFIED from the providers — Indeed and Naukri are now pay-per-event**
   (Indeed $0.006 per result on the FREE tier since 2026-03-26; Naukri since
   2026-09-11 with a $0.10 minimum ceiling), so both *could* take a
   provider-enforced `maxTotalChargeUsd`, which B1 and C2 recorded as absent.
   Neither can batch safely (§12–§13). Documented; not acted on — a follow-up
   safety patch.
9. **Spend.** C3 made **one LinkedIn start: $0.092 intended, $0.06005 actual.**
   The ledger reads **$0.368 intended / $0.24035 actual** of the shared $2.00
   (§19).

## 1. The logical paid plan today — VERIFIED

`scraper.main()` builds, per enabled site in `SITES` order, keyword × location
(× company) with keyword outer (`build_search_plan`), billed depth from
`effective_search`, input from `build_input`, ceiling from `max_charge_usd`.

| Plan (repository defaults) | Logical searches | Structure | Depth | Ceiling | Bounded |
|---|---:|---|---:|---:|---|
| LinkedIn `curious_coder/linkedin-jobs-scraper` | **18** | 9 keywords × {India, Remote} | 15 | $0.046 | yes |
| Indeed `misceres/indeed-scraper` | **72** | 9 keywords × 8 places | 15 | none | no |
| Naukri | 0 | disabled | — | — | — |
| **Total** | **90** | estimate $6.966 | | | |

**Current physical starts: 90** — one per logical search, serially or under C2.
After C3: 90. The other twelve plans are in the audit
([`c3-paid-plan-audit.json`](search-v2-evidence/c3-paid-plan-audit.json)),
e.g. `global_remote` 84 LinkedIn at depth 25, `bigtech_paid` 4 company-filtered
at depth 120, public `india` scope 12 + 12, `remote` 2 + 2, `global` 18 + 18.

## 2. The audit — how

`bench/search_v2_paid_compaction.py`: the current planner and adapters,
never `main()`, never a client, sockets denied. Each synthetic or public-scope
plan is audited under its own lowered `SEARCH`/`SITES` (as `make_profile.render`
lowers the same preferences); each tracked profile in a child process under its
own config. Person-named profiles (`cmp*_<name>`, `kartik_*`, `sarthak_*`) are
derived from real résumés and were not read.

## 3. Plan redundancy audit

| Category | Found | What C3 may do |
|---|---|---|
| 1 exact execution duplicate (identical provider input) | **0 in all 13 plans** | remove — nothing to remove |
| 2 structural overlap | defaults: LinkedIn **9** place/remote pairs (`kw @ India` vs `kw @ Remote` = India + `f_WT=2`); Indeed **189** same-keyword pairs of Indian places (7 places × 9 keywords; `Remote` has no market); public `india` scope 30 per site; `global` 0 | candidates only |
| 3 observed result overlap (C1 `repeat_of_earlier_paid`) | Backend/Full Stack Developer @ India: 2 of 15 (C1), 1 of 15 (C2); React @ Remote: 0 | evidence for C4 |
| 4 final result overlap (`dedupe_lost.other_paid`) | 1 (C1), 1 (C2) | evidence for C4 |
| 5 semantic similarity only | defaults: LinkedIn 8 pairs, Indeed 32 (developer↔engineer, nested words) | nothing |

**How an exact duplicate could arise, and why none does** (VERIFIED):
`build_search_plan` does not deduplicate. A keyword listed twice is one combo
twice: serially the second is `skip (done)`, and under C2 it waits for its twin
and then skips (C2 §13). Two *different* combos with one input: LinkedIn maps
`Gurgaon` and `Gurugram` to one geoId (the public picker drops `Gurugram`,
`sweep/logic.py _INDIA_ALIASES`), and `India` + `remote_only` builds the URL
`Remote` + `remote_geo = India` builds (the public `remote` scope searches
`["Remote"]` alone). No plan combines either. **No mechanism was built** — one
that cannot trigger is not worth its code (the brief's rule).

## 4. The physical plan batching would have made — INFERRED arithmetic

From the prototype's own contiguous formation (§10), nothing done, no budget:

| LinkedIn plan | Logical | Starts at 2 / 3 / 4 per start |
|---|---:|---|
| repository defaults | 18 | 9 / 6 / 5 |
| public `global` scope | 18 | 9 / 6 / 5 |
| public `india` scope | 12 | 6 / 4 / 3 |
| `global_remote` | 84 | 42 / 28 / 21 |
| `bigtech_paid` | 4 | 2 / 2 / 1 |

Indeed and Naukri: unchanged at every size (not batchable, §12–§13). On the
default mixed plan batching could remove at most 13 of 90 starts (14%), all on
the LinkedIn fifth, which is also the fifth C2 already parallelises.

## 5. LinkedIn — the actor contract, build 1.7.17

Read 2026-09-24 from the actor's public record (free, unauthenticated GETs of
`/v2/acts/curious_coder~linkedin-jobs-scraper` and its latest build
`kmTE1iuf8N2sZAj8t`, 1.7.17 — the build C0–C3 ran). Details:
[`c3-provider-contracts.json`](search-v2-evidence/c3-provider-contracts.json).

| Question | Answer | Class |
|---|---|---|
| more than one URL officially supported? | yes — `urls`: "You can pass multiple search URLs." | VERIFIED |
| accepted by the current build? | yes — the canary's run took 2 | MEASURED |
| `limitPerSource` per URL? | yes — "Maximum number of jobs to scrape from each input URL"; changelog 2026-08-08: "max jobs scraped per input URL" | VERIFIED; 15/15 MEASURED |
| `count` global or per source? | **global** — "`count` removed from the input form (still accepted as a global max for backward compatibility)"; not in the schema | VERIFIED (changelog); not isolated live |
| 2 URLs, `limitPerSource` 15, `count` 15? | up to 15 **total** by the changelog | INFERRED (not run: it would buy nothing but the answer) |
| a result's source field? | `inputUrl`, **undocumented** (the README's output table has none) | MEASURED (§6) |
| order / rank within a source? | dataset order is push order; LinkedIn's rank is `position` in each `link` | MEASURED (§14) |
| zero-result source vs unprocessed source? | indistinguishable in the dataset: no per-source status | UNKNOWN (the run log is an extra request) |
| `autoConvertToAiSearch` and provenance? | unsent → true; `inputUrl` is the ORIGINAL URL, not the converted one | MEASURED |
| sources serial or concurrent inside the actor? | interleaved in the dataset after the first nine items → concurrent at least in part | INFERRED |
| cross-source deduplication? | **yes, with back-fill** | MEASURED (§7) |
| pricing | per result pushed ($0.002 FREE, $0.001 paid tiers) + one $0.00005 start event per GB of memory per run (512 MB default) | VERIFIED; settled charges MEASURED |
| a summed `maxTotalChargeUsd` accepted? | yes — $0.092 set and recorded by the provider | MEASURED |

## 6. Source attribution — exact, MEASURED

Free GETs on the six runs C0–C2 made (field names, booleans and integers only):
every one of 90 items carries `inputUrl`, and it **equals** that run's input URL
exactly. In the batched run 30 of 30 items name one of its two URLs, **0
unattributed**, and the engine's split (15 / 15) equals an independent recount
from the provider's dataset. The field exists on single-URL runs too, so it is
not a batching feature. It is undocumented, so a build can remove it; the
prototype refused a batch whole if any result named no source (§10).

## 7. Cross-source deduplication — MEASURED

| A job both queries returned | Backend Developer alone | Full Stack Developer alone | In the one batched run |
|---|---|---|---|
| shared in C1 probe A (2026-09-23 evening) **and** in the C2 canary (2026-09-24 03:23 UTC) | position 7 (both times) | position 2 (both times) | **under Full Stack only, position 2** |
| shared in C1 probe A only | position 15 | position 5 | absent — not in C2's pair either; LinkedIn's results moved overnight |

In the batch, Backend Developer's positions are
`{1–6, 8–16}` — 7 absent, 16 present — where every single-URL run in the
evidence holds exactly 1..15. LinkedIn job ids in more than one source: **0**
(separately: 1 and 2). The dataset starts with nine Full Stack items. So the
actor keeps a run-wide seen set, drops a later source's copy of a job an earlier
push already emitted, and fetches the next result to refill that source's
limit. The README's split-by-location note ("It will also ignore duplicate
jobs") and the 2025-06-20 changelog line ("Remove duplicate jobs") are this
behaviour's only public trace. One batched run; that the kept copy follows the
actor's internal timing rather than URL order is INFERRED from its going to the
second URL.

## 8. Why that stops batching here

C3's contract is that only physical execution changes: each logical search
keeps its rows, and plan order — not completion or dataset order — decides every
tie. Against this actor:

- **Coverage changes.** Backend Developer bought its position 16 and lost its
  position 7. The union grew by one job for the same price, which is arguably
  better, but it is a logical change, and logical changes belong to C4.
- **Attribution becomes the provider's.** Alone, both searches return the shared
  job and dedupe keeps the first-planned copy (C2's `DuplicateTie`). Batched,
  only the copy the actor pushed first exists — here the later search's — and
  nothing downstream can restore plan order. The row changes: `search_query`,
  `search_rank` and the apply URL's tracking parameters.
- **In the default plan it is worse than cosmetic.** Keyword-outer order puts
  `kw @ India` and `kw @ Remote` side by side, so batch size 2 would pair all 9
  of them — the most-overlapping searches in the plan. The Remote copy is
  stamped remote (`remote_was_queried`); an India-scope sweep removes remote rows
  and a remote-scope sweep keeps them. Which copy the actor kept would decide
  whether the job is shown.
- **C1's overlap measure dies inside a batch**: `repeat_of_earlier_paid` and
  `dedupe_lost.other_paid` read zero (both 0 in the canary against 1 in C2),
  which blinds C4's main input.
- **The input has no switch for it** (§5's field list).

The brief's stop conditions hold, and so does its rule that fewer starts alone
is no win: §9 found neither a wall-time nor a request-count gain.

## 9. The live canary — MEASURED

`SWEEP_ALLOW_PAID_BENCH=1 .venv/bin/python -m bench.search_v2_paid_probe --allow-paid
--max-usd 0.37 --exposed-usd 0.276 --case software_fullstack --scope india --site
linkedin --keywords "Backend Developer,Full Stack Developer" --location India
--searches 2 --paid-workers 1 --batch-size 2 --sweep-budget 0.092 --stage C3 …` at
2026-09-24 06:55:29 UTC, on the prototype
([patch](search-v2-evidence/c3-batching-prototype.patch)).
[Evidence](search-v2-evidence/c3-live-batch-canary.json).

**Before the call.** The questions were the brief's nine: whether it takes 2 URLs,
depth per source, which field sets the count, attribution, rank, a zero-result
source, the summed ceiling, runtime against C2, and 429s. Offline cannot answer
any of them. The prototype was implemented and its 53 tests plus C0–C2 and B1
were green (262). The input was checked offline from the exact profile:
`{"urls": [Backend Developer @ India, Full Stack Developer @ India], "limitPerSource":
15, "count": 30, "scrapeCompany": false}`, one start, ceiling $0.092. These are
the same two URLs as the C2 canary and C1 probe A, chosen for runtime
comparability and a likely shared job. The zero-cost preview printed the
preflight and stopped with neither key. Ledger before: $0.276. Intended:
**$0.092**, making the cumulative **$0.368**. Two limits: the guard's
`--max-usd 0.37` refuses any further start, and the engine cap of 0.092 is
exactly the batch's ceiling.

| | C3: one start, two URLs (06:55 UTC) | C2: two concurrent starts (03:23 UTC) | C1 probe A: two serial starts (previous evening) |
|---|---|---|---|
| actor runtime | **45.16 s** (1 run) | 14.64 / 10.87 s | 22.32 / 34.23 s |
| paid phase | 50.29 s | 19.23 s | unit walls 28.5 + 39.3 s |
| physical starts / polls | 1 / 9 | 2 / 3 + 3 | 2 / 5 + 7 |
| SDK requests (429s) | 12 (0) | 6 + 6 (0) | — |
| account reads | 1 | 2 | 2 |
| rows per source | 15 / 15 | 15 / 15 | 15 / 15 |
| distinct LinkedIn jobs | 30 (0 shared: deduped, back-filled) | 29 (1 shared) | 28 (2 shared) |
| provider ceiling | $0.092, recorded | $0.046 × 2 | $0.046 × 2 |
| settled | **$0.06005** (30 × $0.002 + one start) | $0.0601 | $0.0601 |
| funnel (eligible / positive / final) | 13 / 4 / 13 and 14 / 11 / 14 | 13 / — / 13 and 14 / — / 13 | 13 / 5 / 13 and 13 / 10 / 12 |

- **Reservation**: one hold of $0.092 under `batch_000`, committed 2 ms after it
  was taken; `committed_starts` 1; view $0.092 throughout while the guard's own
  reading said $0.058.
- **Cost readings**: terminal poll $0.05805 (29 results counted) at +2.4 s;
  account delta $0.058051 at +3.8 s; settled $0.06005 at +19.5 s; the account
  covered it at +19.8 s and carried +$0.000017 beyond at +65 s (C1's residual).
- **Run listing**: exactly this run on `APIFY_TOKEN`, none on `APIFY_TOKEN_2` —
  no unexpected provider activity; token absent from the evidence (checked by
  value).
- **Checkpoints**: 2 passes (one per search), 119 ms CPU; the second search
  waited 0.3 s for the first's integration.

**The cost model, answered**: 30 results × $0.002 + one start event —
$0.00005 cheaper than two runs. The saving is the start fee and nothing else,
as V2-A predicted.

**Latency**: the batch ran 3.1× longer than the slower of C2's two concurrent
runs, and longer than either serial C1 run. Session-to-session variance is
~3× for the *same* query (C1 22–34 s, C2 11–15 s), so one run cannot say how a
shared run's time grows with its sources (INFERRED: somewhere between the
slowest source and their sum). It does say batching is not a latency win
against C2's concurrency on this evidence.

**Stopped there.** No second canary: the deciding question — does a shared run
preserve each search's semantics — is answered no, and the brief says not to
keep experimenting for a workaround.

## 10. The prototype — built, tested, measured, not merged

The prototype is kept verbatim in
[`c3-batching-prototype.patch`](search-v2-evidence/c3-batching-prototype.patch),
so the canary can be reproduced and C4 can reuse the parts, and so the design
decisions are on record:

- `SWEEP_PAID_COMPACTION`, a mode of C2's scheduler (off without
  `SWEEP_PAID_CONCURRENCY`); `SWEEP_PAID_LINKEDIN_BATCH_SIZE` 2, clamped 1..4; only
  providers in a contract-checked table can batch, and an unbounded site never
  does.
- **Formation**: contiguous plan order from the next unsent search, stopping at a
  done search, a repeat of one in flight, or an input that differs beyond its
  URL. Never reordered, never skipping ahead.
- **Budget-aware shrinking**: the batch is cut to as many ceilings as fit, and one
  that fits alone is a C2 start, exactly. It admitted exactly what C2 admits at
  every budget tested (0.045 → 0.2) and under the public cap for every audited
  plan (e.g. `bigtech_paid` 2 where all-or-nothing batches of 3–4 admit 0;
  `global_all` 59 against 56–58).
- **Ceiling**: the sum of the members' B1 ceilings, reserved once under the batch
  id, committed the instant before the one start request, never released after
  it.
- **Attribution**: exact `inputUrl` match, or the run is refused whole. A member
  that no result named fails and is never marked done, because the provider does
  not say it was searched.
- **Per-search transaction unchanged**: rows → checkpoint → done marker for each
  logical search, in plan order. A crash after the first member's marker leaves
  it done and the second re-bought.
- **Telemetry**: `actor_starts` kept its C1 meaning (logical units with a run);
  `physical_actor_starts` sat beside it. The shared run's readings went to a
  top-level `paid_batches[]`, once, because C2 pins that `paid_execution` holds
  exposure and never a cost. Each search's unit carried only `batch_id` and the
  run's ids.
- **Tests**: 53 (switches, input, formation, reservation, attribution, parity at
  sizes 1–4 × workers 1–4 and three dataset orders, crash/resume, telemetry,
  probe). Its scripted provider did *not* deduplicate across sources — which is
  exactly the property the live run found. Offline parity was byte-identical,
  and live it could not be.
- **Mutation checks**: 16 (the brief's A–P) on the prototype — §17.

Why not merge it behind a flag, off? It would ship a mode that is known not to
preserve C3's semantics on the one provider it serves. The patch keeps the work.

## 11. Batching compatibility rules — the prototype's, for the record

Same provider (and it in the table), same actor, one account per sweep (C2),
provider-bounded, identical actor input but for the URL (so depth, the
`scrapeCompany` flag and every future field match), distinct URLs, contiguous in
plan order, none done, none a repeat of a search in flight.

## 12. Indeed — capability audit (free evidence only)

| Question | Answer | Class |
|---|---|---|
| actor, build | `misceres/indeed-scraper`, latest 0.0.111 | VERIFIED |
| pricing | **PAY_PER_EVENT since 2026-03-26**: "Cost per every job listing returned", $0.006 FREE … $0.001 DIAMOND; no start event; `minimalMaxTotalChargeUsd` null | VERIFIED |
| several searches per run | `position` is one string. `startUrls` is an array, "combined with keyword search — both will execute independently", each capped by `maxItemsPerSearch` — but that means re-expressing every search as an Indeed search URL, a different contract from the `position`/`location`/`country` Sweep sends | VERIFIED |
| source attribution | UNKNOWN (no Indeed run in the evidence window; none made) | — |
| cross-source dedupe | Sweep sends `saveOnlyUniqueItems: true` ("only unique job listings will be scraped"), scope unstated — the LinkedIn finding's risk, by design | INFERRED |
| a provider-enforced ceiling | **now possible**: a pay-per-event actor honours `maxTotalChargeUsd`; at B1's formula, depth 15 × $0.006 × 1.5 = **$0.135** per start (no start event) | INFERRED from the pricing record |

**Decision**: unchanged — serial, one search per start, no ceiling. No live
Indeed call. **Follow-up recommended**: a "B1 for Indeed" safety patch — a charge
model entry, a live contract check that the provider records the ceiling and
`maxItemsPerSearch` bounds the rows — which would let C2 reserve Indeed's
ceilings and run its 72 searches concurrently. That is the largest wall-time
lever left in the default plan, where Indeed is 72 of 90 searches (C2 §23).

## 13. Naukri — capability audit (free evidence only)

| Question | Answer | Class |
|---|---|---|
| actor, build | `muhammetakkurtt/naukri-job-scraper`, 0.0.56; disabled by default | VERIFIED |
| pricing | **PAY_PER_EVENT since 2026-09-11**: start $0.001, `job_item` $0.0015, `job_item_detailed` $0.003 (Sweep sends `fetchDetails: true`); `minimalMaxTotalChargeUsd` **$0.10** | VERIFIED |
| several searches per run | no: `keyword` and `searchUrl` are single strings; `cities` is a union under one `maxJobs` (V2-A) | VERIFIED |
| a provider-enforced ceiling | possible: 50 × $0.003 × 1.5 + $0.001 → $0.226 per start (≥ the $0.10 minimum) | INFERRED |
| Sweep's estimate | `SITE_RATES["naukri"]` $0.50 "per run MINIMUM" predates this pricing; ~$0.151 at 50 detailed results | VERIFIED stale |

**Decision**: no change, no live call. Noted for the same follow-up.

## 14. Dataset order is not LinkedIn's rank — MEASURED, for C4

Each LinkedIn result's `link` carries `position=N&pageNum=0`. In every run it is
a permutation of 1..15, and dataset order is far from it — e.g. C1's A1 read
`15, 14, 13, 12, 11, 10, 6, 8, 4, 9, 5, 3, 7, 2, 1`. The actor fetches details
concurrently and pushes them as they finish. `search_rank` — the dataset index,
which the engine's comment calls "the position within this search" — is
therefore push order. **Not changed** (an output column; out of scope).

C1's yield-by-position table (C1 §19) used it. Re-bucketed by `position` over
the five C1/C2 units (75 rows, content-free):

| LinkedIn position | Rows | Eligible | Eligible positive | Final | Acquired repeats |
|---|---:|---:|---:|---:|---:|
| 1–5 | 25 | 22 | 14 | 20 | **3** |
| 6–10 | 25 | 21 | 15 | 21 | 0 |
| 11–15 | 25 | 22 | 14 | 22 | 0 |

**No decay still** — and every overlap sits at the top of the list, as it
should for related queries. C4's depth work should read `position`, never
`search_rank`.

## 15. Public spend-cap interaction

C3 ships nothing, so C2 §20 stands unchanged: the public cap (1.25 × estimate)
under-prices LinkedIn's $0.046 ceiling and C2 must not be enabled on the public
worker until the cap holds ceilings. The prototype showed batching need not make
it worse: with shrinking it admitted exactly C2's count in every audited plan.
Without shrinking, all-or-nothing batches would have admitted fewer (the audit's
`linkedin_admitted_if_batches_were_all_or_nothing_*`). A summed batch ceiling
leaves total exposure identical, so the C0 guard's worst case (starts ×
ceiling, counted over logical searches) was correct under batching too.

## 16. Diagnostic-only redundancy findings — nothing removed

From the six live units (each one run of one sweep): acquired repeats of an
earlier unit 0–2 of 15; `final_marginal` 12–14 of 12–14 final; no unit was
mostly redundant. By construction the strongest candidates are the default plan's
nine `kw @ India` / `kw @ Remote` pairs (category 2) — measurable in production
telemetry today (`acquired.repeat_of_earlier_paid` on the Remote unit), which is
C4's to read. The developer/engineer keyword pairs are semantic only and carry no
evidence.

## 17. Mutation checks — on the prototype

Each applied alone to the prototype's `scraper.py`; four suites run
(`test_paid_compaction`, `test_paid_concurrency`, `test_paid_observability`,
`test_paid_contract`); reverted; SHA-256 equal before and after.
[Results](search-v2-evidence/c3-prototype-mutations.json).

| # | Mutation | Result | Among the failures |
|---|---|---|---|
| A | batch rows without source attribution (split by dataset position) | **11 fail** | `test_a_result_naming_no_source_fails_the_whole_batch`, `test_rows_go_to_the_search_that_named_them_ranked_within_it`, `test_a_done_search_breaks_a_batch_and_is_never_rebought` |
| B | global dataset rank used as the search's rank | **2 fail** | `test_rows_go_to_the_search_that_named_them_ranked_within_it`, `test_byte_identical_everywhere` |
| C | count=15 for a two-source run (the global cap not summed) | **3 fail** | `test_each_search_keeps_its_full_depth_in_a_shared_run`, `test_each_url_is_the_one_it_would_send_alone_depth_per_search`, `test_the_ceiling_is_the_sum_of_the_searches` |
| D | one $0.046 ceiling reserved for a two-search batch | **8 fail** | `test_1_two_searches_one_hold_of_0092`, `test_2_3_one_commit_per_start_and_no_search_holds_its_own`, `test_4_a_failed_batch_keeps_its_whole_hold` |
| E | each search AND the batch reserved | **5 fail** | `test_1_two_searches_one_hold_of_0092`, `test_2_3_one_commit_per_start_and_no_search_holds_its_own`, `test_4_a_failed_batch_keeps_its_whole_hold` |
| F | the batch's cost copied onto every search | **2 fail** | `test_cost_is_counted_once`, `test_every_search_references_the_one_start_it_was_bought_in` |
| G | a batch's rows integrated in the provider's dataset order | **12 fail, 1 error** | `test_each_search_keeps_its_full_depth_in_a_shared_run`, `test_rows_go_to_the_search_that_named_them_ranked_within_it`, `test_4_crash_after_the_first_searchs_marker` |
| H | every search marked done as soon as its batch finishes | **16 fail** | `test_a_result_naming_no_source_fails_the_whole_batch`, `test_a_search_no_result_named_is_failed_never_done`, `test_1_crash_before_the_batched_start` |
| I | a search already in .done_combos joins a batch (re-bought) | **1 fail** | `test_a_done_search_breaks_a_batch_and_is_never_rebought` |
| J | a two-search batch refused whole when one search still fits | **3 fail** | `test_4_a_failed_batch_keeps_its_whole_hold`, `test_6_budget_0050_shrinks_to_one`, `test_shrinking_admits_exactly_what_one_at_a_time_admits` |
| K | an unbounded provider batched | **0 fail, 1 error** | `test_an_unbounded_provider_never_batches` |
| L | searches with different actor settings share a start | **2 fail** | `test_inputs_that_differ_beyond_the_url_cannot_share`, `test_incompatible_settings_never_share` |
| M | the flag off still batches | **78 fail, 2 errors** | `test_no_token_value_reaches_any_output`, `test_each_unit_records_its_own_run_under_interleaving`, `test_size_one_is_c2` |
| N | batch size 1 still batches two | **2 fail, 1 error** | `test_an_unbounded_provider_never_batches`, `test_physical_starts_fall_logical_ones_do_not`, `test_size_one_is_c2` |
| O | a batch's searches take the first free plan positions (completion order) | **3 fail** | `test_batches_finishing_out_of_order_integrate_in_plan_order`, `test_byte_identical_everywhere`, `test_the_first_planned_twin_wins_whatever_the_dataset_order` |
| P | production telemetry records the raw search URLs | **1 fail** | `test_no_url_query_or_token_in_the_new_sections` |

**16 of 16 caught**, every file restored byte-identical (SHA-256), ~36 s each. Every run also failed C1's ledger test, broken by the canary's new entry and not by any mutation (its two new keys, since allowed in that test). That one failure is subtracted above.

These describe the prototype's tests, which are not merged. The shipped code is
C2's and runs C2's 19 and C1's 15.

## 18. Tests

- `sweep/tests/test_paid_plan_audit.py` — **3 tests** pinning the audit's
  claims on the current planner: the default plan's 18 + 72 structure with no
  exact duplicate or repeated combo; a planted exact duplicate
  (`Gurgaon`/`Gurugram`) is caught; and no person-named profile is read.
- Everything else is the existing suites, unchanged, because no production code
  changed.

## 19. Research ledger

`docs/search-v2-evidence/paid-research-ledger.json`:

| Stage | Physical starts | Logical searches | Intended | Actual | Cumulative intended | Cumulative actual |
|---|---:|---:|---:|---:|---:|---:|
| C0 | 1 | 1 | $0.046 | $0.03005 | $0.046 | $0.03005 |
| C1 probe A | 2 | 2 | $0.092 | $0.0601 | $0.138 | $0.09015 |
| C1 probe B | 1 | 1 | $0.046 | $0.03005 | $0.184 | $0.1202 |
| C2 canary | 2 | 2 | $0.092 | $0.0601 | $0.276 | $0.1803 |
| **C3 canary** | **1** | **2** (batch of 2, depth 15 each) | **$0.092** | **$0.06005** | **$0.368** | **$0.24035** |

18.4% of the shared $2.00 C1–C4 ceiling intended. C3 used $0.092 of its $0.35
soft limit. The C3 entry records the contract finding in its status.

## 20. Unresolved risks

1. **One batched run.** The dedupe finding rests on it plus two earlier separate
   pairs. The mechanism is undocumented and the source is hidden.
2. **`inputUrl` is undocumented** and the build is unpinned (`latest`).
   Anything built on it later must fail closed, as the prototype did.
3. **`search_rank` is push order, not rank** (§14): a pre-existing semantic
   that any rank-based analysis (C4) must route around.
4. **Indeed and Naukri are boundable but not bounded** (§12–§13): C2 still treats
   both as unbounded, and the default plan's 72 serial Indeed searches remain
   the wall-time floor.
5. **`SITE_RATES["naukri"]` is stale** against 2026-09-11 pricing (over-estimate).
6. **The public spend cap** still under-prices LinkedIn ceilings (C2 §20).
7. **A zero-result source is indistinguishable** from an unsearched one in a
   shared run — any future batching must keep failing it closed.

## 21. Implications for C4

- The plan has no exact waste to cut and no physical compaction to take on this
  provider. C4's levers are logical: which searches, which depth.
- The overlap to study is structural and measurable now: the nine place/remote
  pairs. Production telemetry records `repeat_of_earlier_paid` per unit.
- The actor's cross-source dedupe *could* be a deliberate C4 policy — pay once
  per shared job, receive a replacement — but only if C4 accepts
  provider-decided attribution and blind overlap telemetry, and that must be a
  reviewed product decision. The prototype patch is the starting point.
- Depth decisions must read LinkedIn's `position`, not `search_rank`.
- A "B1 for Indeed" patch is the prerequisite for any Indeed concurrency.

## 22. Recommended C3 settings for C5

**None.** `SWEEP_PAID_COMPACTION` does not exist in the shipped code. C5 runs
C1 + C2 (+ C4) as C2 §28 proposes.

## 23. Verification

- **Baseline**: `429b7b6` = `origin/main` at the start; its suites as C2 left them
  (1,319 sweep tests).
- `python -m unittest discover -s sweep/tests -t .` — **1,322 tests, OK** (1,319 +
  3), 210 s, on the final tree.
- `python -m unittest deploy.test_sweep_worker deploy.test_modal_benchmark` —
  **42 tests, OK**.
- `python scraper.py --demo`, `python telemetry.py`, `python -m sources`,
  `python -m sources.concurrency` — pass.
- `auto-apply` suite — 1,102 tests, 2 errors in `test_inference` healthz (a local
  inference service answering 503). **The same two errors reproduce on a clean
  worktree of `429b7b6`** — environmental, as for B3, B5, C0, C1 and C2.
- The prototype, before the canary: its 53 tests plus C0 (45), C1 (51), C2 (81)
  and B1 (32) — **262, OK**. After it: 16/16 mutations caught (§17).
- `git diff 429b7b6 -- scraper.py telemetry.py config.py deploy/ sweep/app.py
  sweep/runs.py sweep/plan.py sweep/worker_link.py sweep/public.py sweep/logic.py
  sources/ rescore_from_apify.py auto-apply/ requirements.txt render.yaml
  gunicorn.conf.py bench/paid_guard.py bench/search_v2_paid_probe.py` — **empty**.
  One test file changed: C1's ledger test admits a batched entry's two keys.
- **Paid**: one LinkedIn start, §9 — preceded by the offline input check and the
  zero-cost preview; both keys on the command line; tight `--max-usd` and engine
  cap; the run listing shows no other activity; the temporary probe profile was
  removed (none left in `profiles/`); outputs lived in a temp directory and were
  deleted. Every other provider read was a free GET on the probes' own runs or a
  public record.
- No configured token's value appears in any new file (checked by value).

## 24. Rollback

Nothing to roll back: no production code, flag, environment or deployment
change. The commit adds an audit tool, one small test module, evidence and this
document.
