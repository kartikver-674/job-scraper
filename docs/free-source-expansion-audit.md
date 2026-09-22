# Free Sweep source expansion audit

2026-09-21 · audit-only · baseline `9dc67cc33f99e5d87bd224498bafd0a336354a4f`.
No production sources, adapters, preferences, profile/ranking logic or deployment
were changed. The first implementation stage acting on this audit is
[V2-A telemetry and shadow](search-engine-v2-a-telemetry-and-shadow.md), which
shadow-measured the eight-board tranche of §8 and resolved the SmartRecruiters
pagination UNKNOWN of §3; no conclusion here was edited. No Apify actor was started. Definitions of **MEASURED**, **VERIFIED**,
**INFERRED**, **UNKNOWN** and the complete execution trace are in the
[engine audit](search-engine-v2-forensic-audit.md).

## Required expansion answers

| Metric | Finding and evidence class |
|---|---|
| CURRENT FREE SOURCE COUNT | **VERIFIED 134 active default records: 129 ATS company boards + 5 feeds.** Six additional employer sources are configured but disabled; 140 total records |
| FUNCTIONING | **MEASURED 132/134 returned nonempty normalized inventory** in one bounded serial census; this is endpoint health, not fully validated useful inventory |
| ZERO-YIELD | 0 successful endpoints returned zero raw jobs; 36 returned no known ≤14-day jobs; 73 returned zero on the broad India/software title-location proxy. Those are different denominators |
| DEAD / FAILING | 2 observed failures: `greenhouse:postman` 404; WWR's last configured RSS category produced 301 redirect failure, discarding the feed's earlier accumulated rows. Permanently dead status UNKNOWN |
| PROVIDER FAMILIES | 5 active ATS adapters + 5 feed adapters. Optional code also supports Amazon, Oracle Recruiting Cloud, Workday, SuccessFactors, Optum/Radancy |
| CURRENT UNIQUE INVENTORY | **MEASURED 12,533 normalized observations → 10,062 current-engine keys; 9,854 host/path URL keys.** Neither identity is ground truth. 6,551 observations dated within 14 days |
| CURRENT ELIGIBLE INVENTORY | No universal number. Frozen synthetic examples: India full-stack 84 final /20 positive-score; India React Native 84/20; India business/Salesforce 93/7; remote full-stack 301/40. Real-candidate fit and link reachability UNKNOWN |
| ADDITIONAL BOARDS DISCOVERED USING EXISTING ADAPTERS | **15,798 additional candidate identifiers**, provenance recorded; discovery is not endpoint or employer validation |
| ADDITIONAL BOARDS VALIDATED | 365 attempted; **187 nonempty compatible JSON endpoints**, 14 valid empty responses with identity unconfirmed, 137 HTTP 404, 27 transport/timeouts. Full identity/stable-ID/link validation remains incomplete |
| ESTIMATED ADDITIONAL RAW JOBS | **MEASURED 8,047** from the 187 nonempty endpoints; no extrapolation to 15,798 candidates |
| ESTIMATED ADDITIONAL UNIQUE JOBS | **MEASURED 6,917 marginal engine keys; 7,282 marginal host/path URL keys** against baseline. Slug-derived employer labels and URL heuristics limit accuracy |
| ESTIMATED ADDITIONAL ELIGIBLE JOBS | Synthetic full-stack India +44 current-filter survivors /+6 positive-score; remote +89/+6; React Native India +44/+4; business/Salesforce India +46/+1. No claim these are actual candidate matches |
| NEW SOURCE FAMILIES WORTH ADDING | Workable public account API first, Gem next; Recruitee XML conditional; Personio XML pending a successful probe. Teamtailor documented access requires a key |
| ESTIMATED BOARD COVERAGE | New-family probe: 3 Workable, 3 Gem, 3 current Recruitee JSON accounts succeeded; 2 Personio attempts failed. Larger family company populations UNKNOWN |
| IMPLEMENTATION COMPLEXITY | Workable/Gem small reusable mappings with validation; XML families small–medium; no new per-company HTML scraper needed |
| EXPECTED UNIQUE INVENTORY GAIN | New families returned 2,041 raw observations; cross-baseline/paid dedupe and candidate eligibility were not replayed, so useful gain UNKNOWN |

The first expansion should be a **small evidence-backed shadow tranche**, not a
commitment to 500 or 1,000 enabled boards. The nine boards identified below cover
all positive-score additions in the four synthetic replays; PubMatic's sampled
job URL returned 403, so hold it out for reachability review. Large raw expansion
produced modest profile-matched gains; broader role/experience cohorts must be
measured before generalizing that result.

## 1. Measurement method and reproducibility

[Census harness](../bench/search_v2_free_audit.py): literal AST extraction of
registry, no profile/token/paid-client imports; existing normalization functions;
title/location predicates deliberately accept all rows so pre-gate inventory can
be inspected. Sequential requests, one attempt, 15s socket timeout, 20MiB body
cap. Disabled sources were not fetched. All default feed pages/categories were
attempted until a feed exception. No continuous crawler was built.

Baseline observation window: **10:11:45–10:18:01 UTC**, 21 September. Audit wall
376.559s, summed instrumented HTTP boundary 330.719s, 150 top-level GET calls,
157,390,349 response-body bytes, audit-process peak RSS 497.20MiB on macOS.
Automatic redirects can make wire-level request counts higher. Error response
bodies/headers/TLS and request bytes are not included in body-byte totals.
The remaining 45.840s includes parsing, analysis and repeated snapshot writes;
it is **not** a pure production normalization measurement. Audit memory retains
whole unfiltered boards and evidence; it is **not production Sweep RSS**.

[Expansion harness](../bench/search_v2_expansion_audit.py): explicit finite 365
targets, serial, 12s socket timeout, one attempt, ≥0.25s pacing; stop a provider
after 429. It recorded 1,021.642s including evidence writes/pacing, 836.892s summed
request+normalization boundaries, 107,531,181 body bytes. Successful board median
0.726s and mean 2.031s. Failures are part of discovery cost, not reasons to run
the same unsuccessful list on every user Sweep.

Artifacts:

- [Exact current inventory CSV](search-v2-evidence/current-free-inventory.csv),
  [JSON](search-v2-evidence/current-free-inventory.json),
  [HTTP observations](search-v2-evidence/current-free-http.json),
  [census summary](search-v2-evidence/current-free-summary.json).
- [Candidate registry with provenance](search-v2-evidence/expansion-discovery-registry.json),
  [365 endpoint outcomes](search-v2-evidence/expansion-validation.json),
  [explicit probe targets](search-v2-evidence/expansion-validation-targets.json).
- [Offline snapshot metrics](search-v2-evidence/snapshot-analysis.json),
  [derivation harness](../bench/search_v2_summarize.py),
  [frozen replay harness](../bench/search_v2_replay.py).

The original census field `fresh_job_count` uses **21 days** as an exploratory
window. This document's **F14** and replay use the actual default **14 days**;
both are preserved rather than silently relabeling the original measurement.
Current Greenhouse mapping uses `updated_at`, not original publication date:
“fresh” means the mapped date passes the window, not a newly opened requisition.
Dates are not proof of an open application. Production keeps unknown dates by
default; none were unknown in the successfully normalized baseline snapshot.

Public JD snapshots remain under `/tmp`, excluded from the commit. SHA-256
hashes bind derived statistics to those snapshots. Re-fetching later will yield
a new inventory; evidence is a dated census, not a deterministic internet fixture.

## 2. What the registry represents and how it was discovered

`config.py:196 ATS_BOARDS` is `{provider: {board_token: display_name}}`. It
contains 129 boards, not 129 integrations. Presence enables a board; deletion or
a profile's dictionary overlay changes membership. No per-board enabled flag,
country, priority, last success, schema version, or failure history exists.
`sources/ats.py:22 ATS` contains five URL/field mappings. Board entries differ
mostly by token/company. Country is inferred from job locations, not employer HQ.

`config.py:507 FEEDS` holds enabled flags plus WWR category list, Jobicy count
and Himalayas queries/pages. These are multi-employer feeds and may require
multiple requests. `config.py:456 OPTUM`, `:496 ENTERPRISE` and
`sources/enterprise.py:61 EMPLOYERS` configure the six disabled sources.
Named CLI profiles can replace or extend defaults; the number 134 is not a
guarantee for every profile. Web-rendered profiles also replace Himalayas
browse pages with up to eight role-query requests.

Discovery provenance is partly comments: manually copied career-page tokens,
July 2026 probes, `harvest_ats.py` company names from existing results, and the
September Adzuna harvest (`bench/ats_harvest_probe.py`). `harvest_ats.slugs` guesses
at most two company slugs, probes each ATS and prints candidates; it does not
automatically write the registry. It treats empty boards as unresolved. Comments
do not provide a machine-readable per-board discovery or approval record.
Current coverage is biased by the software-oriented corpus and previous profiles.

## 3. Provider-family breakdown

**MEASURED snapshot** failures are not longitudinal failure rates. Native field
availability below describes the endpoint/current mapping, not every job.

| Family | Boards | Access / common pattern | Current pagination | Native ID / location / remote / date / salary | Probe health; raw; seconds | Maintenance and expansion |
|---|---:|---|---|---|---|---|
| Lever | 21 | JSON `api.lever.co/v0/postings/{token}?mode=json` | One response; API supports skip/limit | ID dropped; primary location; no explicit remote mapping; createdAt; salary not mapped; commitment misnamed Experience | 21/21; 1,963; 66.95 | Low–moderate; 4,349 additional candidate identifiers |
| Greenhouse | 54 | JSON `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | Whole returned list | Native posting/internal ID dropped; location; remote inferred; updated_at; salary not mapped | 53/54; 7,098; 62.94 | Low–moderate; 8,292 candidates; company identity endpoint available |
| Ashby | 27 | JSON `api.ashbyhq.com/posting-api/job-board/{token}` | Whole returned list | URL identity; primary location (secondary locations not mapped); isRemote; publishedAt; optional compensation not requested | 27/27; 2,385; 188.69 | Low–moderate; 3,140 candidates; latency tail substantial in this sample |
| SmartRecruiters | 24 | JSON `api.smartrecruiters.com/v1/companies/{token}/postings?limit=100` | **First 100 only** despite totalFound/offset support | ID used only to construct URL; location/remote/releasedDate; no description or salary from list | 24/24; 665; 9.78 | Moderate: pagination/details needed for completeness; 13 additional candidates |
| Breezy | 3 | JSON `https://{token}.breezy.hr/json` | One response | Native ID not retained; **country-only** location; no remote mapping; published_date; no JD/salary mapped | 3/3; 53; 1.21 | Moderate schema/semantic validation; four candidates |
| Remote OK | 1 feed | JSON `/api`, metadata entry excluded | Latest returned feed | ID dropped; location/date/pay; remote context in source | 1/1; 99; 1.33 | Shared feed refresh; attribution needed |
| WWR | 1 feed | RSS `/categories/{category}.rss` ×8 | Latest category lists, overlap | URL identity; region/pubDate; JD | 0/1 at feed boundary; earlier categories 200; 4.75 | Category config failure, not whole service dead |
| Remotive | 1 feed | JSON `/api/remote-jobs?category=software-dev` | Returned category list | ID dropped; restricted location, publication date, salary | 1/1; 20; 0.26 | Software-only query restricts role coverage |
| Jobicy | 1 feed | JSON `/api/v2/remote-jobs?count=50&industry=engineering` | Latest 50, no offset in code | ID dropped; geo/date/salary fields | 1/1; 50; 0.59 | Engineering selection, response cap; preserve attribution |
| Himalayas | 1 feed | JSON browse or `/jobs/api/search?q=` | Default 10×20 offset rows; profile ≤8 queries, first page only | guid not retained; geography/timezone restrictions, date, pay | 1/1; 200; 2.51 | Query/page cache key required; cursor migration contract noted below |

Primary contracts: [Greenhouse](https://docs.greenhouse.io/job-board.html),
[Lever](https://github.com/lever/postings-api),
[Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api),
[SmartRecruiters endpoints](https://developers.smartrecruiters.com/docs/endpoints).
These establish reusable patterns, not a safe throughput promise.

Five current SmartRecruiters boards explicitly reported more inventory than
fetched: Renesas 100/917, Sia 100/591, Version1 100/155, Informa 100/161,
Metro 100/1,622. **At least 2,946 advertised rows lie beyond those first pages**;
fresh/relevant/unique gain is UNKNOWN, and title-only scoring limits useful yield.
Expansion samples likewise truncate BoschGroup 100/4,805, Freshworks 100/136,
Bosch-HomeComfort 100/190. More pagination is a source-family improvement, not
134 bespoke adapters or permission to count unfetched rows as useful jobs.

## 4. Health, zero/stale sources and concentration

The inventory appendix names every source and its F14/raw/identity count.
Thirty-five successful sources returned only dates older than 21 days; 36 had
zero F14. These include active career boards, so do not auto-delete them without
checking date semantics and persistence across refreshes. No successful source
had zero marginal engine keys in registry order. That says nothing about
candidate relevance: 73 sources had zero India/software title-location proxy.

WWR's first seven category requests returned HTTP 200. The eighth,
`/categories/remote-jobs.rss`, failed with a redirect error; `wwr()` raised before
returning its accumulated rows. The documented whole-feed URL is
`/remote-jobs.rss`, without `/categories/`.
[WWR's own RSS index](https://weworkremotely.com/remote-job-rss-feed).
This is a concrete source-configuration/failure-isolation issue to review after
the audit; it has not been fixed here. Postman needs board migration discovery,
not a conclusion that the company stopped hiring. Follow-up checks are recorded
in [spot-check evidence](search-v2-evidence/first-tranche-spotchecks.json).
Both failing routes failed again in that independent check; the canonical WWR
route returned HTTP200 with 83 RSS items. Those 83 were not silently added to
the baseline census or eligibility totals.

**Useful concentration depends on the candidate.** The exploratory default
software-title + explicit-India-location proxy yields 451 per-board unique
observations: top10 58.98%, top25 82.26%, top50 98%. It ignores recency,
experience, remote arrangement and cross-board overlaps. It is **not useful-job
market share**. The stricter frozen full-stack remote replay gives top10 87.5%,
top25/top50 100% of its 40 positive-score final jobs. Each India fixture's positive
final inventory fits within ten sources. Replays preserve current gates/weights;
the distribution is not transferable to all users.

High within-board identity collapse: Veeva 51.3%, Elastic 47.3%, SumUp 47.0%,
Attio 46.3%, Tide 38.8%. Calling all of this duplicate waste would be wrong.
Across baseline, 898 company/title key groups contain different URLs; 754 also
contain different location strings. They involve 2,629 observations. Review
requisition IDs before assuming they represent the same opportunity.

## 5. Coverage: what the snapshot can actually show

411 company labels occur in the baseline including feed employers; aliases and
staffing firms prevent equating labels with distinct hiring organizations.
Industry coverage is **UNKNOWN**: no consistent industry taxonomy is retained.
All raw counts below overlap; they are title/location string observations before
the candidate gate, freshness, ranking, or dedupe.

| Indian location mention | Rows | Role-title proxy | Rows |
|---|---:|---|---:|
| Bengaluru/Bangalore | 791 | Software/developer/engineer | 4,055 |
| Gurgaon/Gurugram | 170 | Full stack | 174 |
| Hyderabad | 108 | Frontend / React | 74 |
| Pune | 102 | React Native / mobile | 81 |
| Mumbai | 109 | Backend | 260 |
| Delhi / NCR | 50 | Java | 98 |
| Noida | 137 | Python | 40 |
| Chandigarh / Mohali | 1 | QA / automation | 90 |
| Chennai | 46 | DevOps / cloud | 319 |
| India token (not necessarily India-wide permission) | 944 | Data / ML / AI | 1,398 |
| Remote token (worldwide, not specifically India) | 2,748 | Business analyst | 95 |
| Remote reachable from India | **UNKNOWN globally; synthetic frozen reachability replay below** | Salesforce functional / administrator | 1 / 1 |
| Country distribution | **No canonical country column; location strings only** | Product / operations | 944 / 481 |
| Experience distribution | **Structured numeric levels absent; text is heterogeneous** | Early-career title terms | 505 |

`Experience` is present in 2,149 rows, but Lever commitment and Himalayas employment
type are not years of experience. Salary is present in only 134 normalized rows
(1.07%), partly because ATS mappings omit pay. Rich JD evidence may exist despite
blank structured fields. Software-biased acquisition, category restrictions and
the corpus are not evidence of overall job-market coverage. Early-career titles
can be excluded by actual user defaults; raw presence does not mean eligibility.

## 6. Candidate discovery and validation results

Discovery used explicit public lists from
[Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator/tree/main/data)
plus primary career-page search results for SmartRecruiters/Breezy. The snapshot
records source URLs and SHA-256 hashes. This repository is **secondary discovery
evidence only**, not an API contract, quality endorsement or permission grant.
The 15,798 nonbaseline identifiers split Greenhouse 8,292; Lever 4,349; Ashby
3,140; SmartRecruiters 13; Breezy 4. Company names from most lists are inferred
from slugs until employer-owned metadata is checked. No guessed slug is called
a validated employer merely because it is in a list.

The finite sample had 95 prioritized listed identifiers, 265 deterministic
stratified candidates and five primary-search boards. 187/365 (51.2%) returned
jobs; 14 were empty; 137 404; 27 transport/timeouts. The 187 nonempty split:
83 Greenhouse, 38 Lever, 61 Ashby, three SmartRecruiters, two Breezy. Of 265
stratified candidates, 144 returned jobs; selection/frame bias prevents using
that fraction to claim a global compatible-company population. Persistent
health, correct identity, open links and stable IDs require separate checks.

Top observed India/technical title-location proxies include PubMatic 31, Zeta
Global 20, Freshworks 17, Fivetran 14, Rubrik 12, Abnormal 10, DevRev 10 and
Jumio 9. These figures are raw proxies, not final scored counts. Existing-feed
expansion can also widen categories and search pages, but must preserve frozen
candidate title/eligibility gates. Optional enterprise-family expansion remains
tenant-by-tenant validation work; no numerically substantiated thousands-of-
Workday/Oracle-boards claim is made.

| Discovery approach | Coverage / false positives | Maintenance / validation / dead-board risk | Cadence proposal (INFERRED) |
|---|---|---|---|
| Curated registry | Narrow but high-quality if provenance checked | Low runtime cost; manual identity review; stale unless refreshed | Health daily/weekly, curation monthly |
| Manual employer career pages | High precision; expensive broad coverage | Check redirects/tenant migration and source owner | Each requested geography/role tranche |
| Search-engine discovery | Broad indexed public boards; stale pages/aliases | Query quotas and indexed-date bias; endpoint/identity checks mandatory | Weekly bounded batches |
| Public explicit company/board lists | Large demonstrated candidate pool; 45%+ of this sample empty/failing | Version/hash provenance, dedupe, sampled validation; no blind auto-enable | Monthly list diff + bounded validation |
| ATS-domain/slug enumeration | Potential breadth UNKNOWN; many guesses/wrong companies | High request waste and anti-bot risk; not recommended as uncontrolled crawling | Only small approved explicit hypotheses |
| Public company data / existing result harvest | Targets useful employers; biased to prior profiles | Resolve real career link before slug guesses; alias/provenance review | After measured coverage gaps |
| Hybrid (recommended) | Curated seed + provenance-preserving discovered candidates | Quarantine → validate → shadow yield → reviewable enablement | Refresh health independently of user Sweeps |

## 7. Frozen-profile expansion simulation and yield funnel

Four explicit synthetic fixtures use the unchanged renderer, source gate,
experience/title rules, enrichment, recency, arrangement, reachability, scoring
and dedupe. No personal profile is exported. India means all India, with no
city-picker narrowing; remote uses the existing remote policy. Jobicy/Remotive
remain their default categories. Baseline Himalayas was collected via default
browse, whereas a generated candidate normally issues role queries: this is a
**frozen inventory replay**, not a full candidate web Sweep benchmark.

| Frozen fixture | Baseline final | Expanded final | Baseline positive / expanded positive | Baseline score ≥10 / expanded ≥10 |
|---|---:|---:|---:|---:|
| Full-stack, India | 84 | 128 | 20 / 26 | 8 / 9 |
| Full-stack, remote | 301 | 390 | 40 / 46 | 5 / 6 |
| React Native, India | 84 | 128 | 20 / 24 | 0 / 0 |
| Business/Salesforce, India | 93 | 139 | 7 / 8 | 1 / 1 |

“Final” means passes the existing engine, which has no default min-score cutoff.
Positive score is a **descriptive proxy** and ≥10 is a fixture-relative summary,
not an altered threshold or a universal definition of high relevance. JavaScript
alone does not establish React Native suitability. No observed URL was assumed
live just because it appeared in a JSON list. True unique/fresh/eligible/relevant/
reachable opportunity counts remain unproven; the measurable proxies expose
that gap rather than substituting raw inventory for it.

Full-stack India funnel, actual operation order:

| Boundary | Baseline | Baseline % of observed | Expanded total |
|---|---:|---:|---:|
| Observed normalized public rows | 12,533 | 100% | 20,580 |
| Source title/location gate | 3,099 | 24.73% | 4,572 |
| Company/title/experience hard filters + scoring | 2,209 | 17.63% | 3,206 |
| Current 14-day recency rule | 1,103 | 8.80% | 1,543 |
| Onsite/hybrid-or-unstated arrangement | 627 | 5.00% | 904 |
| Current India geography predicate | 93 | 0.74% | 140 |
| Sort + current identity dedupe / final | 84 | 0.67% | 128 |
| Positive-score final subset | 20 | 0.16% | 26 |

Salary/visa/EOR/min-score gates inactive for these fixture inputs; reachability
filter is applicable to remote fixture, not India arrangement. Raw response
entries rejected before feed normalization, source IDs dropped by adapters and
actual open-application reachability cannot be reconstructed from these rows.
Exact counts and per-boundary local seconds:
[India full-stack](search-v2-evidence/replay-software_fullstack-india.json),
[remote full-stack](search-v2-evidence/replay-software_fullstack-remote.json),
[React Native](search-v2-evidence/replay-react_native-india.json),
[business/Salesforce](search-v2-evidence/replay-business_salesforce-india.json).
The initial replays ran in parallel local processes; their wall times are contention-affected,
not production CPU benchmarks. India full-stack was subsequently rerun as one
audit process: baseline scoring/filtering pipeline 46.54s wall /16.99s process
CPU, expansion 21.25s /8.42s, combined 48.29s /24.97s. The host was not isolated;
wall time includes external scheduling effects. Process peak RSS was 405.66MiB
with both unfiltered snapshots loaded, not production incremental RSS.
Dedupe timing is orders smaller than scoring in
these records, but network remains separately measured and cannot be allocated
to a historical 10–15 minute run.

### Next 25 / 50 / 100 / 250

Order is selected **after observing this snapshot**, sorting additional boards
by positive final yield then final count. This is an optimistic descriptive
envelope, not an out-of-sample adaptive policy.

| Additional boards, India full-stack order | Raw added | Marginal raw engine keys | Additional final | Additional positive | Additional ≥10 | Summed measured source seconds |
|---|---:|---:|---:|---:|---:|---:|
| Next 25 | 1,316 | 1,112 | 44 | 6 | 1 | 46.91 |
| Next 50 | 1,638 | 1,403 | 44 | 6 | 1 | 94.67 |
| Next 100 | 4,071 | 3,423 | 44 | 6 | 1 | 137.35 |
| All 187 nonempty | 8,047 | 6,917 | 44 | 6 | 1 | 379.84 |
| Next 250 | **UNKNOWN: only 187 nonempty endpoints validated** | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |

Remote full-stack next25 adds 2,898 raw /2,360 marginal raw keys /89 final /6
positive; the final/positive gain stays 89/6 through 187. In these fixtures,
useful proxy yield flattens before 25, **by construction of retrospective order**.
It does not establish that every 26th real-world board is worthless. More
companies/roles/experience levels can have different yield curves. Tranche JSON
includes exact board selections; per-source latency in endpoint evidence lets
each selection's request time be summed, without fitting a universal jobs/board
ratio. Source seconds include request and normalization, exclude deliberate
pacing/evidence writes, and are not a forecast of a simultaneous future pass.
All listed tranches consume zero Apify credits; each board requires one initial
GET under the current adapter. Response bytes are in tranche JSON. India
full-stack gains 11 company labels and 12 new location strings, but no new broad
role-title proxy family. Eighteen baseline top20 identities remain in expanded
top20; two new jobs enter it, without changing any scores or ranking weights.
Query/location/page marginal yield for paid sources remains UNKNOWN.

## 8. Recommended first expansion tranche

Nine Greenhouse candidates account for every positive-score expansion addition
in these four replays: **Fivetran, Abnormal Security, Apollo.io, PubMatic, Brex,
Vercel, Jumio, Catawiki and Zeta Global**. Tokens are `fivetran`,
`abnormalsecurity`, `apolloio`, `pubmatic`, `brex`, `vercel`, `jumio`, `catawiki`,
`zetaglobal`. Their list endpoints already returned valid normalized jobs.
Board identity plus one sample application page per board is independently
checked in [spot-check evidence](search-v2-evidence/first-tranche-spotchecks.json).
All nine board-identity calls returned HTTP200 with matching employer names.
Eight sampled job pages returned HTTP200; PubMatic's returned **403**. The 403
is a reachability limitation, not proof the vacancy is closed. No bypass was
attempted. HTTP200 itself does not prove an application is still accepting
candidates, nor that all job URLs work or IDs remain stable across weeks.

Recommend **shadow validation of the eight without a failed sample-link check**,
and keep PubMatic in a separate reachability-review queue, preserving current rules, with
additional business/operations/early-career cohorts before registry approval.
Do not exclude Freshworks/Bosch forever because title-only descriptions or this
fixture produce no positive scores: they are candidates for a separate
pagination/detail investigation. Do not add all 187 automatically. Do not remove
the existing 134 to make room for the new boards. The proposed eight returned
876 raw observations in 5.724 summed source seconds in the original probe. They
contribute +15 final /+3 positive-score jobs for full-stack India, +26/+6 for
full-stack remote, +15/+2 for React Native India and +15/+1 for business/Salesforce
India in this snapshot. These are synthetic replay gains, with employer alias
and full job-level reachability validation still outstanding.

## 9. New high-leverage source families

[Dedicated family research](search-v2-evidence/new-provider-families.md) records
public/auth access, IDs, company identifiers, geography, remote/date/description/
URLs, pagination, known limits, implementation size, maintenance and evidence
limits. Workable's official public account endpoint contradicts the obsolete
“all tested widget slugs empty” rationale for leaving it unsupported. Gem has
a documented unpaginated public job-board API. Recruitee JSON's announced
February 2027 token requirement makes XML the more durable candidate. Personio
needs successful sampling; a 429 is a stop signal, not a reason for a bypass.

Numeric compatible-company totals and useful inventory gain for new families
are UNKNOWN; use the nine successful account probes as an observed lower bound
on current technical compatibility only. Prioritize additional validated boards
on existing adapters before investing in new HTML scraping systems.

## 10. Public inventory caching and operational cost

**VERIFIED no shared board-inventory cache** exists in `sources`. Each Sweep
fetches each configured board again. Worker status memoization is per HTTP
request; it is unrelated to inventory caching. A cached object must be public,
provider-scoped inventory **before candidate title/location filters and before
candidate-derived `hires_home`**. Do not share scored rows, candidate preferences,
résumé text, credentials, private tokens, ownership IDs or personalized state.

| Family | Public inventory key | TTL / refresh proposal and evidence | Freshness/cross-user conditions |
|---|---|---|---|
| Greenhouse | provider, region, board, content option, adapter version | **Safe shared HTTP TTL not established**: 83 expansion responses `private,max-age=0,must-revalidate`; use validators/provider review rather than ignoring headers | Normalized public data store is a distinct design decision; revalidate and retain ID/date semantics |
| Lever | provider, region, board, query/limit options, adapter version | No cache header observed in these candidates; a 1–6h application refresh is only a hypothesis | Preserve all locations and complete board for per-user home signal |
| Ashby | board, compensation option, adapter version | **Observed HTTP public max-age=60, stale-while-revalidate=60**; longer retention not justified by that header | Published-at and closures can change; do not cache candidate-local enrichments |
| SmartRecruiters | board, every filter/offset/limit/language, adapter version | TTL UNKNOWN; 1–6h application refresh hypothesis; complete page-set generation required | Partial first100 cannot masquerade as complete board; details need their own keys |
| Breezy | board, adapter version | TTL UNKNOWN; 1–6h hypothesis | Country-only/location missing JD limits preserved, not “fixed” in cached rows |
| Remote OK | endpoint/options, adapter version | TTL UNKNOWN; conditional requests if supported | Attribute source and preserve source links per API metadata |
| WWR | canonical category/feed URL, adapter version | TTL UNKNOWN; 1–6h hypothesis | Isolate category refresh failures; overlap dedupe; source attribution |
| Remotive | category/search/company/limit, adapter version | Publisher advises ≤4 refreshes/day, so 6h shared refresh is aligned, not a freshness guarantee | API publication delay 24h; attribution/access restrictions apply |
| Jobicy | count/industry/geo/tag/options, adapter version | Publisher says no more than hourly; a few/day sufficient | Count/filter variants differ; public canonical Jobicy links must be preserved |
| Himalayas | endpoint, query/country/company/seniority/sort/page or cursor, adapter version | Publisher says dataset refreshes daily; daily reuse is appropriate to examine | Never key all role queries as “himalayas”; offset is now deprecated in favor of cursor |
| Optional enterprise / Optum | tenant+site+query+page+adapter version; separate job detail ID | TTL UNKNOWN, tenant-specific | Public listings may be reusable but query/locale/identity/live verification cannot be dropped |

Primary evidence: [Remotive contract](https://github.com/remotive-com/remote-jobs-api),
[Jobicy usage](https://jobicy.com/jobs-rss-feed),
[Himalayas API](https://himalayas.app/docs/remote-jobs-api),
[Remote OK metadata](https://remoteok.com/api). Each has attribution/usage rules;
public reachability alone is not blanket republication permission. Robots/TOS
review for every tenant and storage mode is **UNKNOWN**. No legal permission
claim is made here. An HTTP `private` directive must not be overridden by putting
the same response into a shared HTTP cache.

**INFERRED model:** B one-request boards, U user Sweeps/day: current board calls
`B*U`; R independent refreshes/day: `B*R` plus conditional revalidation/health
calls, if data reuse is allowed. If U=100 and R=4, a shared batch can save 96%
of board fetches/body transfer at full reuse; if U=1, refreshing four times/day
increases traffic. On-demand cached reuse for ten users in one freshness window
could avoid nine fetches (90%). Expected hit rate and savings are **UNKNOWN**
without arrival rate, query overlap and invalidation traces. Cached freshness
lag adds to provider's own delay. Failed refresh must not silently extend old
inventory forever; stamp stale age and cap use, without changing recency rules.

## 11. Registry scaling and Free depth

Keep current feed configuration fixed and add one-request ATS boards. These are
**INFERRED request-time models**, not measured production completion forecasts.
Baseline summed HTTP time 330.719s; extra-board time brackets the observed
successful expansion median/mean 0.726/2.031s. Bracket is a sensitivity scenario,
not a confidence interval. Excludes retries, fresh failure tail, cache, queue,
normalization/scoring/serialization/UI and extra pagination/details.

| Total enabled records | Added boards | Top-level GETs/pass | Modeled request time, median/mean scenario | Cache / failure management | Expected useful-job gain |
|---|---:|---:|---:|---|---|
| 134 current | 0 | 150 | 5.51 /5.51 min | Already benefits from common feed refresh and source state | Measured baseline fixtures above |
| 200 | 66 | 216 | 6.31 /7.75 min | Shared feed limits; independent board health | UNKNOWN for arbitrary66; only selected-tranche data valid |
| 300 | 166 | 316 | 7.52 /11.13 min | Registry state + provenance + refresh budget increasingly useful | 187-candidate snapshot permits selection, not a universal forecast |
| 500 | 366 | 516 | 9.94 /17.90 min | Repeated per-user full fetch increasingly costly; separate inventory refresh recommended for evaluation | UNKNOWN; not enough validated nonempty boards |
| 1,000 | 866 | 1,016 | 15.99 /34.83 min | Shared inventory lifecycle/provider budgets, failure suppression and completeness metadata needed | UNKNOWN; technically plausible patterns, no demonstrated useful yield |

Doubling/quadrupling board count does not guarantee proportional time because
feeds, payload sizes, failures and details vary. A 500-record pass is 3.44× the
baseline top-level calls in this fixed-feed model, not necessarily 4× wall time.
If 1% of 1,000 boards each costs three 25s timeout attempts +3s backoff,
the simple serial timeout tail alone is roughly 780s. Socket timeout is not a
strict end-to-end deadline; this is only a scenario. Higher result volume can
also increase scoring/serialization memory and time without useful yield.

Static reviewed configuration remains reasonable at 134 and can describe
300/500/1,000 records. The trigger for new architecture is **mutable health,
refresh scheduling, provenance, quotas and multiple consumers**, not an arbitrary
line-count threshold. Keep reviewed definitions versioned; place measured
operational state outside Python source when updates are frequent. A relational
table or simple persisted registry is sufficient; no need to prescribe a complex
service before workload evidence. Proposed fields: provider, board_id,
company_name/aliases, endpoint/region, country coverage, discovery source/date,
enabled, last_success/failure, consecutive_failures, last_job_count,
last_fresh_count, last_unique_yield by policy cohort, latency, schema_version,
snapshot_complete, disabled_reason and next_validation_at.

Free “depth” should mean explicit board/family coverage, page completeness,
inventory age, time/request budget and marginal useful yield. Current paid
`max_results=15` has no meaningful control over whole ATS boards. Avoid a
universal top15-per-board cap: ordering is provider-specific and could silently
erase rare relevant jobs. Evaluate board priority under a time budget only after
per-cohort yield is measured, and expose partial coverage in engine state.

## 12. Source validation and health process — design only

1. Quarantine a candidate with provider, exact employer-owned career URL/slug,
   discovery provenance and expected company identity. Reject unrelated hosts
   and distinguish duplicate aliases/tenant migrations.
2. Fetch within provider limits. Record status/redirect/content-type/schema,
   payload size, latency and list completeness. Nonempty valid jobs or a
   verified legitimate zero state are acceptable; HTTP200 alone is insufficient.
3. Validate title, company, IDs, URL host, location/multi-location, remote,
   published-vs-updated dates, salary and description mappings. Do not silently
   substitute title-only records for complete JDs. Flag unlisted postings.
4. Check a small bounded sample of canonical job URLs, without auth/CAPTCHA.
   Re-fetch on a later day to check stable IDs and closures; retain job-level
   provenance. A one-time sample cannot establish long-term reachability.
5. Shadow-replay unchanged profiles and compare marginal final/positive/high
   score jobs, top-ranked identity, companies, locations and role diversity
   against baseline. Include nonsoftware and early-career cohorts.
6. Review a small enablement diff; monitor latency/failures/yield and maintain a
   reversible per-source disabled flag. No source was enabled by this audit.

| State | Proposed response |
|---|---|
| 404 | Recheck with backoff, inspect verified career-link migration; quarantine only after corroboration, retain provenance |
| Schema drift / malformed rows | Quarantine incompatible schema; preserve prior-good snapshot with explicit bounded staleness, record malformed fraction; no fabricated empty success |
| Zero jobs for months | Mark dormant and reduce refresh, retain verified employer identity; zero is not necessarily failure |
| Repeated timeouts/5xx | Bounded backoff/circuit-breaker state, separate provider outage from one board; preserve independent successful work |
| 429 | Honor Retry-After/provider guidance, stop pressure, do not multiply retries/concurrency |
| Auth/CAPTCHA introduced | Stop unauthenticated acquisition; mark unsupported/access-review, no bypass |
| Large inventory drop | Check completeness/schema/refresh before deleting cached jobs; distinguish true closures from failed pages |

Real repeated failure rates, stable IDs, live job URL coverage, safe cache TTLs,
industry/geography taxonomy, production resource scaling and real-candidate
marginal yield remain **UNKNOWN** until the above longitudinal work. Do not
weaken current relevance or eligibility rules to make expansion appear better.

## 13. Review sequence

First, V2-A telemetry/frozen replay plus review of the concrete WWR failure and
paid actor-contract drift. Next, corroborate health failures and shadow the
eight-board existing-adapter tranche and the PubMatic reachability hold. Evaluate shared public inventory refresh
before a broad rollout, respecting provider update/rate/attribution contracts.
Then test Workable/Gem with overlap and candidate cohorts. Only measured residual
waste should motivate dedupe changes, batching, concurrency or adaptive depth.
The [engine audit](search-engine-v2-forensic-audit.md) includes risks, rollback
and later stages. No target registry size is approved by these measurements.

## Exact current inventory (all 140 configured records)

Source token identifies provider and board. All 129 ATS entries are active by membership; country metadata is absent. Five feeds are enabled; six optional employer entries are disabled. Endpoints/configuration and every requested field are in [the full CSV](search-v2-evidence/current-free-inventory.csv) and [JSON](search-v2-evidence/current-free-inventory.json). Here U is the current engine identity within that source; F14 is dated within 14 days as of 2026-09-21. Eligible and relevant counts per real candidate are UNKNOWN for every row. A successful fetch with F14=0 is not proof jobs have closed. Probe seconds include parsing for ATS and feed work.

| Source / board | Company | Enabled | Fetch | Raw | F14 | U | Within-source identity collapse | Seconds |
|---|---|---|---|---:|---:|---:|---:|---:|
| lever:paytm | Paytm | yes | OK | 203 | 47 | 185 | 8.9% | 6.65 |
| lever:meesho | Meesho | yes | OK | 49 | 4 | 48 | 2.0% | 2.92 |
| lever:mindtickle | Mindtickle | yes | OK | 19 | 3 | 19 | 0.0% | 2.29 |
| lever:hevodata | Hevo Data | yes | OK | 48 | 5 | 45 | 6.2% | 2.59 |
| lever:zeta | Zeta | yes | OK | 21 | 5 | 20 | 4.8% | 2.67 |
| lever:fampay | FamPay | yes | OK | 14 | 0 | 14 | 0.0% | 4.09 |
| lever:cred | CRED | yes | OK | 11 | 0 | 11 | 0.0% | 1.98 |
| lever:coderio | Coderio | yes | OK | 24 | 5 | 24 | 0.0% | 2.90 |
| lever:rws | RWS | yes | OK | 67 | 15 | 67 | 0.0% | 3.22 |
| lever:dozee | Dozee | yes | OK | 20 | 5 | 15 | 25.0% | 2.53 |
| lever:pocketfm | Pocket FM | yes | OK | 6 | 2 | 6 | 0.0% | 1.80 |
| lever:veeva | Veeva Systems | yes | OK | 918 | 50 | 447 | 51.3% | 7.14 |
| lever:portagepointpartners | Portage Point Partners | yes | OK | 52 | 2 | 51 | 1.9% | 2.66 |
| lever:acceldata | Acceldata | yes | OK | 41 | 0 | 37 | 9.8% | 2.62 |
| lever:sophos | Sophos | yes | OK | 88 | 13 | 80 | 9.1% | 3.35 |
| lever:levelai | Level AI | yes | OK | 19 | 0 | 19 | 0.0% | 2.35 |
| lever:jumpcloud | JumpCloud | yes | OK | 22 | 8 | 22 | 0.0% | 2.62 |
| lever:appzen | AppZen | yes | OK | 19 | 2 | 19 | 0.0% | 2.58 |
| lever:cin7 | Cin7 | yes | OK | 8 | 1 | 7 | 12.5% | 2.01 |
| lever:binance | Binance | yes | OK | 313 | 39 | 306 | 2.2% | 6.34 |
| lever:biorender | BioRender | yes | OK | 1 | 0 | 1 | 0.0% | 1.64 |
| greenhouse:groww | Groww | yes | OK | 7 | 4 | 7 | 0.0% | 0.39 |
| greenhouse:postman | Postman | yes | FAIL | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 0.34 |
| greenhouse:druva | Druva | yes | OK | 39 | 16 | 35 | 10.3% | 0.49 |
| greenhouse:slice | Slice | yes | OK | 27 | 18 | 21 | 22.2% | 0.45 |
| greenhouse:gitlab | GitLab | yes | OK | 213 | 213 | 198 | 7.0% | 1.81 |
| greenhouse:databricks | Databricks | yes | OK | 874 | 240 | 593 | 32.2% | 3.05 |
| greenhouse:twilio | Twilio | yes | OK | 142 | 142 | 122 | 14.1% | 2.30 |
| greenhouse:mongodb | MongoDB | yes | OK | 399 | 399 | 250 | 37.3% | 1.69 |
| greenhouse:elastic | Elastic | yes | OK | 357 | 357 | 188 | 47.3% | 9.28 |
| greenhouse:datadog | Datadog | yes | OK | 454 | 454 | 354 | 22.0% | 3.52 |
| greenhouse:cloudflare | Cloudflare | yes | OK | 381 | 142 | 364 | 4.5% | 3.66 |
| greenhouse:stripe | Stripe | yes | OK | 670 | 670 | 604 | 9.9% | 1.46 |
| greenhouse:netradyne | Netradyne | yes | OK | 26 | 6 | 26 | 0.0% | 0.42 |
| greenhouse:figma | Figma | yes | OK | 153 | 27 | 153 | 0.0% | 2.50 |
| greenhouse:roku | Roku | yes | OK | 255 | 239 | 166 | 34.9% | 1.73 |
| greenhouse:flix | Flix | yes | OK | 153 | 79 | 126 | 17.6% | 1.50 |
| greenhouse:sumup | SumUp | yes | OK | 372 | 250 | 197 | 47.0% | 1.77 |
| greenhouse:ubiquiti | Ubiquiti | yes | OK | 173 | 19 | 151 | 12.7% | 0.52 |
| greenhouse:justworks | Justworks | yes | OK | 97 | 39 | 88 | 9.3% | 0.70 |
| greenhouse:tide | Tide | yes | OK | 80 | 80 | 49 | 38.8% | 0.77 |
| greenhouse:bitwarden | Bitwarden | yes | OK | 44 | 24 | 41 | 6.8% | 0.41 |
| greenhouse:nice | NICE | yes | OK | 162 | 59 | 141 | 13.0% | 1.05 |
| greenhouse:towerresearchcapital | Tower Research Capital | yes | OK | 89 | 16 | 78 | 12.4% | 0.48 |
| greenhouse:dunnhumby | dunnhumby | yes | OK | 34 | 29 | 33 | 2.9% | 1.37 |
| greenhouse:elsevier | Elsevier | yes | OK | 9 | 0 | 7 | 22.2% | 0.40 |
| greenhouse:iris | Iris Software | yes | OK | 2 | 0 | 2 | 0.0% | 0.39 |
| greenhouse:wise | Wise | yes | OK | 17 | 2 | 17 | 0.0% | 0.62 |
| greenhouse:capco | Capco | yes | OK | 721 | 721 | 578 | 19.8% | 2.37 |
| greenhouse:wppproduction | WPP Production | yes | OK | 152 | 52 | 133 | 12.5% | 0.62 |
| greenhouse:stratainformationgroup | Strata Information Group | yes | OK | 11 | 1 | 11 | 0.0% | 0.40 |
| greenhouse:indigo | Indigo | yes | OK | 2 | 0 | 2 | 0.0% | 0.34 |
| greenhouse:mcafee | McAfee, Inc. | yes | OK | 1 | 0 | 1 | 0.0% | 0.35 |
| greenhouse:unisonconsulting | Unison Consulting | yes | OK | 2 | 0 | 2 | 0.0% | 0.34 |
| greenhouse:victrix | Victrix Systems & Labs | yes | OK | 3 | 2 | 3 | 0.0% | 0.34 |
| greenhouse:okta | Okta | yes | OK | 325 | 325 | 288 | 11.4% | 0.87 |
| greenhouse:payoneer | Payoneer | yes | OK | 119 | 119 | 112 | 5.9% | 4.59 |
| greenhouse:accordionindia | Accordion India | yes | OK | 21 | 0 | 20 | 4.8% | 0.41 |
| greenhouse:netskope | Netskope | yes | OK | 142 | 54 | 90 | 36.6% | 0.65 |
| greenhouse:avathon | Avathon | yes | OK | 37 | 4 | 37 | 0.0% | 0.43 |
| greenhouse:newrelic | New Relic | yes | OK | 49 | 49 | 38 | 22.4% | 0.92 |
| greenhouse:cloudsek | CloudSEK | yes | OK | 15 | 3 | 15 | 0.0% | 0.42 |
| greenhouse:komodohealth | Komodo Health | yes | OK | 29 | 10 | 28 | 3.4% | 0.53 |
| greenhouse:godaddy | GoDaddy | yes | OK | 38 | 23 | 35 | 7.9% | 0.48 |
| greenhouse:precisionaq | Precision AQ | yes | OK | 42 | 42 | 40 | 4.8% | 0.44 |
| greenhouse:launchdarkly | LaunchDarkly | yes | OK | 52 | 26 | 51 | 1.9% | 0.45 |
| greenhouse:bitgo | BitGo | yes | OK | 39 | 10 | 30 | 23.1% | 0.45 |
| greenhouse:eulerity | Eulerity | yes | OK | 16 | 4 | 16 | 0.0% | 0.41 |
| greenhouse:rtingscom | RTINGS.com | yes | OK | 5 | 3 | 5 | 0.0% | 0.43 |
| greenhouse:fingerprint | Fingerprint | yes | OK | 29 | 29 | 28 | 3.4% | 1.71 |
| greenhouse:breezeway | Breezeway | yes | OK | 9 | 2 | 8 | 11.1% | 0.43 |
| greenhouse:diligent | Diligent | yes | OK | 6 | 1 | 6 | 0.0% | 0.33 |
| greenhouse:cobblestoneenergy | Cobblestone Energy | yes | OK | 2 | 0 | 2 | 0.0% | 0.41 |
| greenhouse:shield | SHIELD | yes | OK | 1 | 0 | 1 | 0.0% | 0.41 |
| greenhouse:bold | BOLD | yes | OK | 1 | 0 | 1 | 0.0% | 0.36 |
| ashby:linear | Linear | yes | OK | 32 | 3 | 25 | 21.9% | 0.96 |
| ashby:ramp | Ramp | yes | OK | 148 | 24 | 146 | 1.4% | 1.90 |
| ashby:openai | OpenAI | yes | OK | 815 | 135 | 767 | 5.9% | 4.09 |
| ashby:notion | Notion | yes | OK | 128 | 8 | 118 | 7.8% | 1.25 |
| ashby:teero | Teero | yes | OK | 4 | 0 | 3 | 25.0% | 0.79 |
| ashby:clickhouse | ClickHouse | yes | OK | 201 | 40 | 127 | 36.8% | 50.62 |
| ashby:tekion | Tekion | yes | OK | 117 | 19 | 86 | 26.5% | 1.54 |
| ashby:gradera | Gradera | yes | OK | 6 | 2 | 6 | 0.0% | 1.44 |
| ashby:whisk | Whisk Software Private Limited | yes | OK | 4 | 0 | 4 | 0.0% | 1.08 |
| ashby:elevenlabs | ElevenLabs | yes | OK | 226 | 10 | 226 | 0.0% | 0.30 |
| ashby:uipath | UiPath | yes | OK | 101 | 18 | 86 | 14.9% | 83.91 |
| ashby:glomo | Glomo | yes | OK | 7 | 0 | 7 | 0.0% | 0.61 |
| ashby:clera | Clera | yes | OK | 274 | 201 | 183 | 33.2% | 0.74 |
| ashby:abound | Abound | yes | OK | 18 | 3 | 18 | 0.0% | 0.46 |
| ashby:maincode | Maincode | yes | OK | 10 | 6 | 9 | 10.0% | 1.35 |
| ashby:xero | Xero | yes | OK | 105 | 39 | 77 | 26.7% | 24.43 |
| ashby:solace | Solace | yes | OK | 26 | 5 | 26 | 0.0% | 1.70 |
| ashby:brainco | Brain Co. | yes | OK | 35 | 2 | 30 | 14.3% | 1.60 |
| ashby:realmalliance | Realm Alliance | yes | OK | 11 | 0 | 11 | 0.0% | 0.48 |
| ashby:nory | Nory | yes | OK | 6 | 1 | 6 | 0.0% | 2.10 |
| ashby:freetrade | Freetrade | yes | OK | 7 | 2 | 7 | 0.0% | 2.99 |
| ashby:omni | Omni | yes | OK | 23 | 22 | 23 | 0.0% | 0.78 |
| ashby:attio | Attio | yes | OK | 41 | 8 | 22 | 46.3% | 1.44 |
| ashby:pylon | Pylon | yes | OK | 17 | 5 | 17 | 0.0% | 0.51 |
| ashby:vantage | Vantage | yes | OK | 5 | 0 | 5 | 0.0% | 0.63 |
| ashby:sitemate | Sitemate | yes | OK | 14 | 7 | 12 | 14.3% | 0.56 |
| ashby:pilgrim | Pilgrim | yes | OK | 4 | 0 | 4 | 0.0% | 0.42 |
| smartrecruiters:jitterbit | Jitterbit | yes | OK | 24 | 1 | 15 | 37.5% | 0.42 |
| smartrecruiters:renesaselectronics | Renesas Electronics | yes | OK | 100 | 100 | 85 | 15.0% | 0.65 |
| smartrecruiters:sia | Sia | yes | OK | 100 | 100 | 74 | 26.0% | 0.64 |
| smartrecruiters:agileengine | AgileEngine | yes | OK | 1 | 0 | 1 | 0.0% | 0.35 |
| smartrecruiters:jadeglobal | Jade Global | yes | OK | 6 | 0 | 6 | 0.0% | 0.37 |
| smartrecruiters:version1 | Version 1 | yes | OK | 100 | 72 | 94 | 6.0% | 0.59 |
| smartrecruiters:informagroupplc | Informa Group Plc. | yes | OK | 100 | 89 | 80 | 20.0% | 0.39 |
| smartrecruiters:quantanite | Quantanite | yes | OK | 9 | 0 | 9 | 0.0% | 0.32 |
| smartrecruiters:blueoptima | BlueOptima | yes | OK | 11 | 6 | 11 | 0.0% | 0.32 |
| smartrecruiters:keywordsstudios | Keywords Studios | yes | OK | 35 | 5 | 31 | 11.4% | 0.39 |
| smartrecruiters:metromakro | METRO/MAKRO | yes | OK | 100 | 100 | 83 | 17.0% | 0.80 |
| smartrecruiters:nisum | Nisum | yes | OK | 1 | 0 | 1 | 0.0% | 0.33 |
| smartrecruiters:technogen | TechnoGen | yes | OK | 49 | 0 | 48 | 2.0% | 0.34 |
| smartrecruiters:vichara | Vichara Technologies | yes | OK | 9 | 3 | 9 | 0.0% | 0.42 |
| smartrecruiters:codeyoung | Codeyoung | yes | OK | 2 | 0 | 2 | 0.0% | 0.34 |
| smartrecruiters:capestart | CapeStart | yes | OK | 1 | 0 | 1 | 0.0% | 0.34 |
| smartrecruiters:genpactindia | Genpact India Pvt. Ltd. | yes | OK | 1 | 0 | 1 | 0.0% | 0.35 |
| smartrecruiters:servicetitan | ServiceTitan | yes | OK | 8 | 0 | 8 | 0.0% | 0.34 |
| smartrecruiters:rebelfoods | Rebel Foods | yes | OK | 1 | 0 | 1 | 0.0% | 0.37 |
| smartrecruiters:gepworldwide | GEP Worldwide | yes | OK | 1 | 0 | 1 | 0.0% | 0.37 |
| smartrecruiters:lingaro | Lingaro | yes | OK | 1 | 0 | 1 | 0.0% | 0.33 |
| smartrecruiters:pentair | Pentair | yes | OK | 1 | 0 | 1 | 0.0% | 0.35 |
| smartrecruiters:spottedzebra | Spotted Zebra | yes | OK | 1 | 0 | 1 | 0.0% | 0.33 |
| smartrecruiters:synechron | Synechron | yes | OK | 3 | 0 | 3 | 0.0% | 0.33 |
| breezy:iqvia | IQVIA | yes | OK | 7 | 0 | 6 | 14.3% | 0.38 |
| breezy:anovia | Anovia Inc. | yes | OK | 13 | 6 | 13 | 0.0% | 0.40 |
| breezy:foundationhealth | Foundation Health | yes | OK | 33 | 12 | 29 | 12.1% | 0.43 |
| remoteok | MULTIPLE | yes | OK | 99 | 24 | 99 | 0.0% | 1.33 |
| wwr | MULTIPLE | yes | FAIL | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 4.75 |
| remotive | MULTIPLE | yes | OK | 20 | 13 | 20 | 0.0% | 0.26 |
| jobicy | MULTIPLE | yes | OK | 50 | 50 | 49 | 2.0% | 0.59 |
| himalayas | MULTIPLE | yes | OK | 200 | 200 | 196 | 2.0% | 2.51 |
| amazon | Amazon | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| jpmorgan | JPMorgan Chase | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| oracle | Oracle | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| accenture | Accenture | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| sap | SAP | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| optum | Optum | no | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
