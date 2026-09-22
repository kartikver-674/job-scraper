# Additional reusable public families — 2026-09-21

These are research findings, not adapters or registry edits. Nine successful
public JSON probes returned 2,041 job observations across Workable, Gem and
Recruitee. Two Personio probes failed (404 and 429); further Personio probing
stopped. These counts are not unique, fresh or eligible jobs. Exact endpoints,
field names, payload sizes and latency are in
[probe evidence](expansion-new-family-probes.json).

## Workable — strongest next adapter candidate

**VERIFIED primary contract:** public published-job endpoint
`https://www.workable.com/api/accounts/{account_subdomain}?details=true`;
account identity plus job list, no token for this route. Do not confuse it with
authenticated `spi/v3/jobs`. [Workable documentation](https://help.workable.com/hc/en-us/articles/115012771647-Using-the-Workable-API-to-create-a-careers-page).

**MEASURED:** `keywords-intl1` 283 jobs, `weekday-1` 1,040, `serko-ltd` 20;
all HTTP 200, 0.76–2.24s in these probes. IDs `shortcode`/`code`, board slug,
city/state/country and locations array, telecommuting, published_on/created_at,
HTML description and public URL were present. Three company accounts verified
at endpoint level; correct company identity and recruiting-agency provenance
still need review. In particular, large agency inventory is not necessarily
new employer inventory. India/global/remote yield and overlap were not measured
for these new-family snapshots. No numeric company-coverage estimate is justified.

One full job list was returned; pagination or size ceilings for this public route
are **UNKNOWN**. Implementation **S–M**, maintenance **moderate**, no HTML crawler
required. Native IDs, multiple locations and dates should be retained from the
start. Rate limits and reuse permission beyond the documented integration use
case need provider review. Existing `sources/ats.py` comments about another
widget route returning empty lists do not establish that Workable is unusable.

**VERIFIED alternative:** Workable publishes an aggregate XML feed for listings
approved on Jobs by Workable, refreshed hourly; download then filter, with no
company-side server filtering. Preserve URLs for attribution. This may cover
many accounts but file size/overlap were not measured, so do not download it on
every user Sweep. [XML feed documentation](https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed).

## Gem — small, clear public contract

**VERIFIED:** `https://api.gem.com/job_board/v0/{vanity_url_path}/job_posts/`
is a public read endpoint, no pagination; applications require authentication.
The documented 20 requests/s limit is per API key, so do **not** assume that
limit authorizes anonymous traffic at the same rate.
[Official API](https://api.gem.com/job_board/v0/reference),
[Gem integration guide](https://help.gem.com/databases/gem-help-center/the-job-board-api).

**MEASURED:** practice-by-numbers 25, bolna 7, umbrella-incorporated 18 jobs;
HTTP 200 in 1.39–2.07s. IDs `id`, `internal_job_id`, `requisition_id`; board slug;
location/offices, location_type; first_published_at/created_at/updated_at;
content/content_plain and absolute_url are present in inspected schemas.
Namespacing requisition IDs is essential. Public posting detail route exists.
Implementation **S**, maintenance **low–moderate**. Three working boards are a
lower bound on compatible accounts, not a global market-size estimate. India,
remote and unique eligible inventory are **UNKNOWN** until local replay. Good
small validation tranche after existing-adapter expansion.

## Recruitee — prefer XML; JSON has a published authentication transition

**MEASURED:** current public `/api/offers/` returned 14 offers for
kcoverseaseducation, 620 for transperfect, 14 for skycellag; 1.66–4.79s. Schema
contains id/guid/slug, company_name, locations/country/city, remote/hybrid/on_site,
published_at/created_at/updated_at, description/requirements and careers_url.
No pagination was required in these responses; general ceiling **UNKNOWN**.

**VERIFIED:** the Careers API documentation announces mandatory authorization
by **10 February 2027**, while excluding XML offer feeds and widgets from that
token requirement. A new long-lived unauthenticated JSON adapter is therefore
not the preferred investment. [Authentication notice](https://docs.recruitee.com/reference/authentication-1).

Use the documented `https://{company}.recruitee.com/api/feeds/offers.xml`
or raw XML `/api/offers.xml` only after schema/identity/latency validation.
XML offers reference ID, company/company_id, description, posted/updated date,
locations, URLs and optional pay. Those XML routes were **not live-probed in
this audit**, so JSON success is not XML validation.
[Feed endpoints](https://support.recruitee.com/en/articles/8213076-faq-api),
[XML schema](https://docs.recruitee.com/docs/feed).

Implementation **S–M**, maintenance **moderate** due to transition. Three JSON
accounts validated; XML compatible company population and additional eligible
gain remain **UNKNOWN**. Geography breadth inferred from multinational employer
sample, not a measured India/remote coverage claim.

## Personio — documented reusable XML; current sample not validated

**VERIFIED:** per-company `/xml?language=en` at `.jobs.personio.com` or `.de`,
enabled by the employer, is the documented careers integration. Do not mistake
the authenticated Recruiting API for this feed.
[XML reference](https://developer.personio.de/v1.0/reference/get_xml),
[enablement documentation](https://support.personio.de/hc/en-us/articles/207576365-Integrate-jobs-from-Personio-into-your-website-via-XML).

**MEASURED:** amina `.com` route 404, locad `.com` 429. Neither validates a usable
board; neither establishes the whole family is dead. No bypass or aggressive
retry performed. IDs, office/location, descriptions and employment fields need
validation against a successful response; posted date/remote mapping and
pagination/size limits remain **UNKNOWN for this sample**. Implementation **S–M**,
maintenance **moderate**, strongest geographic opportunity **INFERRED European
employers**, India/remote and worldwide compatible board counts **UNKNOWN**.

## Teamtailor — investigate only with appropriate access

The documented API requires a key, including a Public Read key for published
career-site data; “public read” is not equivalent to anonymous universal access.
No unauthorized token extraction or speculative anonymous API recommended.
IDs, relationships, locations, descriptions and pagination should be assessed
only within the documented access model. Numeric company coverage and Sweep
incremental value **UNKNOWN**. Priority below validated unauthenticated families.
[Official access guide](https://support.teamtailor.com/en/articles/5963369-use-our-teamtailor-api).

## Already-supported enterprise families are not new adapters

`sources/enterprise.py` already has Workday CXS POST, Oracle Recruiting Cloud
JSON, Amazon JSON and SuccessFactors HTML+detail, plus Optum/Radancy separately.
Default registry disables them. Workday/Oracle have reusable tenant configuration;
Amazon and Optum are bespoke; SuccessFactors carries HTML/JSON-LD maintenance
risk. None of these disabled boards was live-run during the default census.
Do not count them as newly discovered source families or promise thousands of
companies simply because the vendor has a large customer base.

For each proposed tenant: verify employer-owned career URL and tenant/site IDs,
listing and detail schema, pagination, geographic query behavior, stable IDs,
remote/date/pay fields, unauthenticated access and endpoint-specific traffic
limits. Current Workday discovery and detail fan-out can dominate requests;
using an existing adapter does not make one tenant equal to one request.

## Reuse and legal/robots status

Public reachability and documented careers integration establish technical
access, not blanket republication rights. Per-provider attribution, permitted
retention and rate limits must be retained in registry provenance. Job URLs
should stay canonical and attributed. Provider/tenant robots rules and broader
terms were not exhaustively reviewed: **UNKNOWN**, not “approved”. Stop on
authentication requirements, CAPTCHA or provider rate-limit responses; do not
add bypass logic. This is an operational due-diligence requirement, not a legal
conclusion. Numeric expected useful-job gain for each new family remains
**UNKNOWN**; the 2,041 observed rows are only a ceiling before overlap/filters
within these nine sampled endpoints.
