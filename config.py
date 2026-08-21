"""
Configuration for the Salesforce functional-consultant job scraper.

EVERYTHING that decides *what* gets pulled and *how* it's ranked lives here. You
should never need to touch scraper.py to add a keyword, a location, a site, a skill
weight, or an exclude term.

Philosophy — prove it cheaply first:
    Every Apify actor run costs money (pay-per-event). A full sweep is
        len(role_keywords) x len(locations) x (enabled sites)
    actor runs. So start small and widen only once the pipeline + ranking look right:

        python scraper.py --dry-run          # print the plan + per-site inputs, ZERO cost
        python scraper.py --test             # 1 keyword x 1 location, indeed only, tiny
        python scraper.py --site indeed --limit 3   # one site, first 3 combos
        python scraper.py                    # full sweep (asks to confirm if large)

Sections below:
    1. SEARCH   — shared criteria + the role x location matrix
    2. SITES    — which boards to scrape (toggle here)
    3. SCORING  — resume-based relevance weights, functional-consultant bonus, exclude/down-rank
    4. SETTINGS — filtering thresholds, cost guards, output knobs
    5. PROFILES — named overlays, so one scraper serves several people/searches

Everything here is the DEFAULT profile. To run a different search without
editing this file, put the keys you want to change in profiles/<name>.py and run
`python scraper.py --profile <name>` — see section 5.
"""

# ===========================================================================
# 1. SEARCH — shared criteria + the role x location matrix
# ===========================================================================
# The scraper runs the CROSS PRODUCT of role_keywords x locations for every
# enabled site. Ordering matters only cosmetically (results are re-ranked by the
# scoring layer), but the exact-title Salesforce terms are listed first by intent.
SEARCH = {
    "country": "IN",            # Indeed country code (IN, US, GB, ...)
    "experience_years": 2,      # ~1.7 yrs actual (Salesforce FC at Dealermatix since 01/2025,
                                # résumé says "1+ year"); ask sites for 2 so both 1-yr and
                                # 2-yr postings come back. Upper bound is enforced by
                                # SETTINGS["max_experience_years"], which is now 4.
    "salary_min": None,         # DELIBERATELY None: site-side salary filters silently drop the
                                # majority of India listings that don't disclose pay. The CTC
                                # floor is enforced after the fact via SETTINGS["min_comp_usd"].
    "max_results": 15,          # jobs PER (keyword x location) search — keep modest, pay-per-event (~$5/1000 results)

    # Each entry is run as its OWN search term. Salesforce functional/BA titles —
    # config + requirements work, NOT Apex/LWC development (see penalty_terms).
    "role_keywords": [
        # Bare "Salesforce" first, and it is the biggest single volume lever here:
        # it returns admin, BA, consultant AND developer postings, and the scoring
        # layer is what separates them (developer titles are penalized, not
        # searched for). One extra keyword, roughly double the reachable pool.
        "Salesforce",
        "Salesforce Admin",
        "Salesforce Functional Consultant",
        "Salesforce Consultant",
        "Salesforce Business Analyst",
        "Salesforce Administrator",
        "Salesforce Sales Cloud Consultant",
        "Salesforce Service Cloud Consultant",
        "Salesforce Business Systems Analyst",
        "CRM Functional Consultant",
        "Business Analyst CRM",
        "Functional Consultant",
    ],

    # Tricity (current base — Dealermatix is in Mohali) + NCR + the big Salesforce
    # partner hubs + Remote.
    # NOTE: this list drives INDEED only. LinkedIn and Naukri override it with their
    # own SITES[...]["locations"] (they need numeric geo/city IDs), so Chandigarh and
    # Mohali are not searched there — add IDs + widen those lists if you want them.
    "locations": [
        "Chandigarh", "Mohali", "Delhi", "Gurgaon", "Noida",
        "Bengaluru", "Hyderabad", "Pune",
        # Added for reach: both are large Salesforce-partner markets, and home is
        # Kotdwara (Uttarakhand) rather than Tricity, so relocating to a metro is
        # already on the table.
        "Mumbai", "Chennai",
        "Remote",
    ],
}


# ===========================================================================
# 2. SITES — which boards to scrape
# ===========================================================================
# Flip "enabled" to toggle. indeed + naukri on by default (best India signal);
# linkedin available but off. Per-site input differences are handled by the
# adapters in scraper.py (build_input) — the SEARCH matrix maps onto each one.
# Order matters: sites run top-to-bottom, so cheapest-first means a mid-run stop
# (e.g. account usage cap) loses only the expensive tail. LinkedIn (~$0.001/result)
# and Indeed (~$0.09/run) run before Naukri (~$0.50/run minimum).
SITES = {
    # LinkedIn is cheap (~$0.001/result) but needs a numeric geoId for location
    # (see LINKEDIN_GEO_IDS). "Delhi / NCR" isn't a LinkedIn geo; use "India" for
    # broad coverage or a specific city.
    # remote_geo: which region a bare "Remote" location means. LinkedIn's f_WT=2
    # filters workplace type WITHIN a geography — there is no worldwide remote
    # search — so this has to be stated. It was hardcoded to India in the adapter,
    # which silently made every remote sweep an India-remote sweep.
    # remote_only: add f_WT=2 to EVERY search, so a list of countries becomes a
    # list of remote-in-that-country searches. That is how a global remote sweep
    # is expressed (see profiles/global_remote.py).
    "linkedin": {"enabled": True,  "actor": "curious_coder/linkedin-jobs-scraper",
                 "locations": ["India", "Remote"],
                 "remote_geo": "India", "remote_only": False},
    "indeed":   {"enabled": True,  "actor": "misceres/indeed-scraper"},
    # Naukri has a ~$0.50 MINIMUM charge per run, so pulling only a few results is
    # wasteful. results_per_run overrides SEARCH["max_results"] to pull more per
    # run and amortize the floor; "locations" overrides SEARCH["locations"] with a
    # FEW broad regions ("Delhi / NCR" = id 9508 covers Delhi+Gurgaon+Noida in one
    # run). So naukri does few large runs; control keyword count with --limit.
    # Runs LAST — it's the priciest, so a cap sacrifices only its remaining combos.
    "naukri":   {"enabled": True,  "actor": "muhammetakkurtt/naukri-job-scraper",
                 "results_per_run": 50,
                 "locations": ["Delhi / NCR", "Remote"]},
}

# LinkedIn job search filters by a numeric geoId, not a place name. A missing or
# wrong geoId is NOT a soft failure: LinkedIn ignores the free-text location and
# returns US results, so you pay full price for the wrong country. The adapter
# therefore REFUSES to build a LinkedIn search with no geoId rather than guessing.
#
# Check any entry (or a raw id) for free, no auth, against LinkedIn's public
# guest search — it reports where the jobs it returns actually are:
#     python verify_geoids.py
#     python verify_geoids.py 103644278
#
# NOTE: the actor requires count >= 10 per run.
LINKEDIN_GEO_IDS = {
    # --- countries, all VERIFIED 2026-07-26 via verify_geoids.py --------------
    "United States": "103644278", "United Kingdom": "101165590",
    "Canada": "101174742", "Ireland": "104738515",
    "Germany": "101282230", "Netherlands": "102890719",
    "France": "105015875", "Spain": "105646813", "Portugal": "100364837",
    "Poland": "105072130", "Sweden": "105117694", "Switzerland": "106693272",
    "Australia": "101452733", "New Zealand": "105490917",
    "Singapore": "102454443", "United Arab Emirates": "104305776",
    "Japan": "101355337", "Brazil": "106057199", "Mexico": "103323778",
    "South Africa": "104035573",
    "India": "102713980",

    # --- Indian cities, all VERIFIED 2026-07-26 ------------------------------
    "Delhi": "106187582",
    "Gurgaon": "115884833", "Gurugram": "115884833",   # LinkedIn labels it Gurugram
    "Bengaluru": "105214831",
    "Hyderabad": "105556991",
    "Pune": "114806696",
    "Mumbai": "106164952",

    # REMOVED after verification — left here so nobody re-adds them:
    #   "New Delhi": "106164932"  -> returns Inner Mongolia, CHINA. Use "Delhi".
    #   "Noida":     "105598789"  -> returns no job cards at all.
    # Both were previously marked "unverified" and would have been billed in full.
}

# LinkedIn's company filter is `f_C=<numeric company id>`, and it has exactly the
# same failure mode as geoId: a wrong id does not error, it silently returns some
# OTHER company's jobs and bills you in full. So every entry here was verified
# against the public guest search before use:
#
#     python verify_geoids.py --companies
#
# Each id below returned 10/10 cards for the named company (2026-08-16).
#
# NOT a hypothetical risk — 1409 is widely cited online as Capgemini and is
# actually **Wells Fargo Advisors**. Verifying caught it before a single run.
LINKEDIN_COMPANY_IDS = {
    "Microsoft": "1035",
    "IBM": "1009",
    "Deloitte": "1038",
    "Siemens": "1043",
    # Capgemini is deliberately ABSENT: its numeric id is not exposed on any
    # guest surface (the job cards and posting pages carry only the "capgemini"
    # slug, no urn:li:organization). Target it by keyword instead and filter on
    # the company column — a distinctive company name makes that precise, and
    # guessing an id here is how you end up paying for Wells Fargo.
}

# ---------------------------------------------------------------------------
# FREE sources: company career boards (ATS) + public remote-job feeds
# ---------------------------------------------------------------------------
# No Apify, no per-result cost, stdlib HTTP only. These are the cheapest way to
# widen coverage, so add liberally.
#
# ATS_BOARDS: {platform: {board_token: "Display Name"}}. The platform must have
# an adapter in sources/ats.py's ATS table (adding one there is a dict entry).
# Find a token from the careers URL:
#   boards.greenhouse.io/<token>        -> greenhouse
#   jobs.lever.co/<token>               -> lever
#   jobs.ashbyhq.com/<token>            -> ashby
#   careers.smartrecruiters.com/<Token> -> smartrecruiters  (case-sensitive)
# Every token below was probed and resolves (2026-07-25).
ATS_BOARDS = {
    "lever": {
        "paytm": "Paytm", "meesho": "Meesho", "mindtickle": "Mindtickle",
        "hevodata": "Hevo Data", "zeta": "Zeta", "fampay": "FamPay",
        "cred": "CRED",
        "coderio": "Coderio",         #  0/22  — harvest_ats.py, 2026-07-27
        # harvest_ats.py, 2026-08-21, from this résumé's own sweep output
        "levelai": "Level AI",        # 17/20  India
    },
    # Indian employers, plus global companies WITH an India presence — the
    # combination that makes SETTINGS["keep_restricted_if_hires_home"] pay off,
    # since their geo-locked remote roles become reachable. India-job counts
    # probed 2026-07-26. ONE greenhouse key only: a second one silently replaces
    # this whole dict rather than adding to it.
    "greenhouse": {
        "phonepe": "PhonePe", "groww": "Groww", "postman": "Postman",
        "druva": "Druva", "slice": "Slice",
        "gitlab": "GitLab",           # 30/187 India — an all-remote company
        "databricks": "Databricks",   # 76/800
        "twilio": "Twilio",           # 25/183
        "mongodb": "MongoDB",         # 17/401
        "elastic": "Elastic",         # 16/204
        "datadog": "Datadog",         #  9/418
        "cloudflare": "Cloudflare",   #  3/271
        "stripe": "Stripe",           # 40/536, 121 dev roles
        "netradyne": "Netradyne",     # 40/53 — India-heavy
        "figma": "Figma",             #  3/174
        # Found by harvest_ats.py from companies the paid sweep had already
        # surfaced — i.e. we were paying to see these roles through LinkedIn and
        # can now fetch them free and direct. Probed 2026-07-27.
        "roku": "Roku",               # 40/234
        "clickhouse": "ClickHouse",   # 10/171
        "flix": "Flix",               #  9/154
        "sumup": "SumUp",             #  2/369
        "ubiquiti": "Ubiquiti",       #  0/159 — no India entity, so its geo-locked
        "justworks": "Justworks",     #  0/98    roles can never be rescued; kept
                                      #          only for worldwide-remote postings
        # harvest_ats.py, 2026-08-21, from this résumé's own sweep output. Already
        # earning its keep: dunnhumby's "Associate Functional Analyst" in Gurugram
        # was one of the highest-scoring rows of the run that found the board.
        "dunnhumby": "dunnhumby",     # 16/64  India
    },
    # Probed 2026-07-26 and NOT resolvable, so nobody burns time re-trying:
    # razorpay, zerodha, dream11, sharechat, unacademy, swiggy, zomato, flipkart,
    # myntra, nykaa, lenskart, browserstack, chargebee, innovaccer, whatfix,
    # moengage, hasura, atlan, upstox, cars24, zepto, porter, rapido, sprinklr,
    # jupiter, navi, khatabook, smallcase, cleartax, scaler, turing, deel,
    # posthog, replit and ~25 more. Only 5 of 61 candidates resolved: most Indian
    # employers don't expose a public ATS API, they hire via Naukri or a custom
    # portal. Free ATS expansion has hit diminishing returns.
    "ashby": {
        "linear": "Linear", "ramp": "Ramp", "openai": "OpenAI",
        "notion": "Notion",           #  5/127
        "teero": "Teero",             #  0/5   — harvest_ats.py, 2026-07-27
    },
    "smartrecruiters": {
        # harvest_ats.py, 2026-08-21. One India job today, but Genpact is an
        # IT-services employer that staffs Salesforce delivery teams, so the
        # board is worth reading every sweep — it costs one request.
        "genpactindia": "Genpact India Pvt. Ltd.",   # 1/1 India
    },
}

# ---------------------------------------------------------------------------
# OPTUM — one employer's own careers site, kept separate from ATS_BOARDS
# ---------------------------------------------------------------------------
# Adapter: sources/optum.py. Free, stdlib, no auth. Not a row in ats.ATS because
# that table maps a JSON list to dotted paths, and this site returns HTML inside
# JSON with no description or date in the listing — the JD needs a second
# request per job (which is also what verifies the requisition is still live).
#
# careers.optum.com is dead (NXDOMAIN 2026-07-29); Optum requisitions are served
# from careers.unitedhealthgroup.com, which hosts every UHG brand in one index.
# brand="optum" keeps only Optum-branded cards (the per-card CSS class is the
# ONLY place the brand appears — the site's Brand facet holds business segments).
#
# enabled=False by default: this is an employer-specific sweep, switched on by
# profiles/optum.py, so a normal run is unchanged.
OPTUM = {
    # ON here. UnitedHealth Group runs one of the largest captive tech operations
    # in India (Noida, Gurugram, Hyderabad, Bengaluru) and staffs its own
    # Salesforce and CRM teams, so with the title gate retuned to functional
    # vocabulary this whole free index is worth reading. ~59 listing requests.
    "enabled": True,
    "company": "Optum",
    "brand": "optum",
    # ONE empty query = the whole index, which is both cheaper and more complete
    # than a keyword list. Probed 2026-07-30: the index holds 5,872 jobs, and the
    # site's full-text search reads the JD body, so a keyword is a strict SUBSET
    # of "" that also can't be trusted to narrow ("developer" matched 5,787 of
    # 5,872 — nearly every JD says the word somewhere). A 12-keyword list was
    # therefore 12 sweeps of the same index that could still miss a role whose
    # title we want but whose JD never says our words. The title + location gates
    # (ATS_TITLE_HINTS / ATS_TITLE_EXCLUDE / LOCATION_HINTS) do the narrowing, for
    # free, and only survivors cost a JD request. Whole sweep: ~59 listing
    # requests, ~3 min.
    "keywords": [""],
    "locations": [""],
    "per_page": 100,        # verified honoured; the site's own UI uses 15
    "max_pages": 70,        # 5,872 jobs / 100 = 59 pages + headroom to grow
    # Re-fetch every JD and drop anything that 404s — a pulled requisition is
    # gone from the site. See the module docstring for why the Taleo apply URL
    # can NOT be used for this (it answers 200 for nonexistent reqs).
    "verify_live": True,
}

# ---------------------------------------------------------------------------
# ENTERPRISE — household-name employers that run their own recruiting platform
# ---------------------------------------------------------------------------
# Adapter: sources/enterprise.py. Free, stdlib, no auth. Separate from
# ATS_BOARDS because these are not rented boards: they are four different
# platforms (amazon.jobs, Oracle Recruiting Cloud, Workday, SuccessFactors),
# two of which need a JD request per job and one of which needs a POST.
#
# Which employers exist is sources/enterprise.EMPLOYERS; this only says which to
# RUN. Adding a company already on one of those platforms is a dict entry there
# — Workday and Oracle Recruiting Cloud between them run a large share of the
# Fortune 500, so the marginal cost of the next name is one line.
#
# enabled=False by default: switched on by a profile, so a normal run is
# unchanged. keywords=[""] sweeps a whole board and lets the title/location
# gates narrow it; give real keywords only where the board is too big to page.
ENTERPRISE = {
    # ON here, unlike main. Accenture is the largest Salesforce employer in India
    # by a wide margin and its Workday board is fully readable for free, which
    # makes this the single biggest free source for this search — bigger than all
    # 34 ATS_BOARDS put together, since those are product companies that hire
    # Salesforce people only for their own internal BizApps team.
    "enabled": True,
    # oracle and sap are deliberately OUT of the list. Both run their own
    # competing CRM/ERP, so their postings are for their own stacks — the exact
    # roles penalty_terms sinks — and Oracle Recruiting Cloud publishes no full
    # JD anyway, so those rows can't be scored properly either way.
    "employers": ["accenture", "amazon", "jpmorgan"],
    # NOT [""] (the whole index) like main uses. These boards run tens of
    # thousands of postings; an unfiltered read at max_pages would return an
    # arbitrary slice of mostly irrelevant jobs. One keyword is passed straight to
    # each platform's own search, so this asks each employer for its Salesforce
    # openings and nothing else.
    "keywords": ["Salesforce"],
    "max_pages": 8,
    "verify_live": True,
}

# Public remote-job feeds. No auth, no cost. Adapters live in sources/feeds.py
# (registered in sources.FEED_FETCHERS). The three structured JSON feeds below
# are the only free source that reports PAY — the ATS boards never do.
FEEDS = {
    "remoteok": {"enabled": True},
    "wwr": {"enabled": True, "categories": [
        "remote-programming-jobs",
        "remote-front-end-programming-jobs",
        "remote-back-end-programming-jobs",
        "remote-full-stack-programming-jobs",
    ]},
    "remotive": {"enabled": True},
    "jobicy": {"enabled": True, "count": 50},
    # No category filter exists on this API, so it pages blind through ~96k
    # mostly non-engineering jobs at 20 a time. Worth it for the exact UTC
    # offsets it reports, but raise `pages` only if you want the requests.
    # 2 pages, not 10: himalayas alone was ~80% of the wall-clock on a free
    # sweep here (8 minutes of ~10) and returned ONE row. It stays enabled
    # because that row was real, but paged shallowly — a remote Salesforce
    # posting worth having is on page 1, not page 9.
    "himalayas": {"enabled": True, "pages": 2},
}

# Keep a free-source job only if its location mentions one of these.
# EMPTY = allow every location, which is the right default now that the target
# is international remote — remote/visa/comp filters do the narrowing instead of
# a country whitelist. An empty job location is always kept.
# To go back to India-only, copy HOME_LOCATION_HINTS below into this list.
LOCATION_HINTS = [
    # main ships this EMPTY (allow everything) because its target is
    # international remote. Empty here would let the 30-odd free ATS boards pour
    # in US and EU rows that can't be applied to from Mohali. Tricity first, then
    # NCR and the Salesforce partner hubs, matching SEARCH["locations"].
    "india", "chandigarh", "mohali", "panchkula", "zirakpur",
    "delhi", "new delhi", "ncr", "gurgaon", "gurugram", "noida",
    "bengaluru", "bangalore", "hyderabad", "pune", "mumbai",
    "remote",
]

# Where YOU are. Not a filter — this is how a company board is checked for
# whether the employer hires in your country at all
# (SETTINGS["keep_restricted_if_hires_home"]).
HOME_LOCATION_HINTS = [
    "india", "delhi", "ncr", "gurgaon", "gurugram", "noida", "bengaluru",
    "bangalore", "hyderabad", "pune", "mumbai", "chennai", "kolkata",
    "ahmedabad",
]

# Free sources return a whole board (finance, ops, HR, ...), so unlike job boards
# we can't keyword-search. Keep only jobs whose TITLE looks like a software/dev
# role (case-insensitive substring). Scoring then ranks within these.
# Retuned from main's list, which is a FULL-STACK DEVELOPER vocabulary
# ("react", "node", "sde", ...). Left as it arrived, this gate would have kept
# zero functional-consultant roles off any free board — the free-source layer
# would have looked like it worked and returned nothing relevant, which is the
# expensive kind of silence. Substring match, so "salesforce" also catches
# "Salesforce Functional Consultant (Sales Cloud)".
ATS_TITLE_HINTS = [
    "salesforce", "sales cloud", "service cloud", "crm",
    "functional consultant", "functional analyst", "business analyst",
    "business systems analyst", "systems analyst", "solution consultant",
    "administrator", "configuration",
    # Broad on purpose: on a partner or consultancy board this is the main title
    # shape, and a free source costs nothing when it over-returns — scoring sinks
    # what doesn't fit. A missing hint, by contrast, is invisible.
    "consultant", "salesforce admin",
]

# Titles to reject even when they DO match a hint above. Checked first, so it
# wins — which is the only way to keep out a role that borrows a software title
# for a different job ("Senior Software Engineer - Data Engineer, Spark, ETL").
# Empty by default: it earns its keep when the hints are broadened past one
# stack, where a wider net starts catching adjacent careers. Seniority does NOT
# belong here — SCORING["hard_drop_terms"] already handles it, and as a penalty
# rather than a silent delete.
ATS_TITLE_EXCLUDE = []

# Naukri needs numeric city IDs (not names). Map each name you search here to its
# ID (from the actor's schema). "Remote" is special-cased to a workMode filter, so
# it needs no entry. Add more IDs as you widen coverage.
NAUKRI_CITY_IDS = {
    "Delhi / NCR": "9508",   # broad region: Delhi + Gurgaon + Noida (best value)
    "Delhi": "382",
    "New Delhi": "6",
    "Gurgaon": "73", "Gurugram": "73",
    "Noida": "220", "Greater Noida": "350",
    "Bengaluru": "97",
    "Hyderabad": "17",
    "Pune": "139",
    "Mumbai": "134",
}


# ===========================================================================
# 3. SCORING — resume-based relevance layer
# ===========================================================================
# Each job's (title + description) is matched, case-insensitively and on WORD
# BOUNDARIES (so ".net" and "go-live" match cleanly and "lead" won't match
# "leadership"), against the terms below. Positive weights add to the score;
# penalty terms subtract; a "both halves of the job" bonus rewards roles that
# need Salesforce CONFIG *and* BUSINESS ANALYSIS (see the bonus section).
# Terms are matched literally, so avoid "&" — write "dashboards", not
# "reports & dashboards", or the pattern never fires.
SCORING = {
    # -- Positive skill weights (higher = more central to the resume) ---------
    # Everything here is on the resume: Salesforce Certified Administrator,
    # Sales/Service Cloud config, Flow Builder, BRD/FRD/FSD, UAT/SIT, gap
    # analysis, stakeholder management, SOQL, DMS/SFA delivery.
    # WEIGHTED BY DISCRIMINATIVE POWER, not just by how central the skill is to
    # her. Her BA vocabulary (requirement gathering, BRD, UAT, gap analysis...) is
    # real but GENERIC — every business-analyst JD on earth contains it. Measured
    # on live Naukri data with those terms at 3-4: "Oracle Fusion Functional
    # Consultant" scored 43 and a generic "Business Analyst" 26, both OUTRANKING
    # the real "Salesforce Business Analyst" at 25. So Salesforce-platform terms
    # now dominate and the generic craft vocabulary is supporting signal only.
    "skill_weights": {
        # Platform match — must dominate. This is what separates her target roles
        # from any other BA/consultant job.
        "salesforce": 10, "sales cloud": 8, "service cloud": 8,
        # Community Cloud is on the résumé under its OLD name; every JD written
        # since 2021 calls it Experience Cloud, so both have to be here or the
        # postings that want exactly what she has score zero for it.
        "experience cloud": 7, "community cloud": 7,
        "salesforce administrator": 8, "salesforce admin": 8,
        "salesforce certified": 4, "certified administrator": 4,
        "flow builder": 6, "salesforce flow": 6, "soql": 5,
        "salesforce integration": 3, "crm implementation": 3,
        # Platform config skills, EACH IN BOTH INFLECTIONS. Matching is
        # word-boundary, not substring: "approval process" does NOT match
        # "approval processes", and a JD is as likely to write one as the other.
        # Measured on one JD worded both ways: 40 points with the plural spellings
        # in the config, 14 with only the singulars. Same reason "salesforce
        # admin" sits next to "salesforce administrator" above.
        "lightning experience": 4, "experience builder": 4,
        "lightning app builder": 4,
        "validation rule": 4, "validation rules": 4,
        "approval process": 4, "approval processes": 4,
        "custom object": 4, "custom objects": 4,
        "permission set": 4, "permission sets": 4,
        "sharing rule": 4, "sharing rules": 4,
        "record type": 3, "record types": 3,
        "page layout": 3, "page layouts": 3,
        "role hierarchy": 3, "governor limits": 2,
        # Her job title + the title product companies use for the same job +
        # platform category — generic enough to need modest weight.
        "functional consultant": 4, "business systems analyst": 4, "crm": 3,
        # Functional-delivery artefacts she authored. Higher than the rest of the
        # generic vocabulary because they signal functional (not dev) work.
        "brd": 3, "frd": 3, "fsd": 2, "sop": 1,
        # Generic BA / testing craft — resume-stated, but non-discriminative, so
        # supporting weight only. Do NOT raise these: it's what let Oracle and
        # plain-BA roles outrank real Salesforce ones.
        "business analyst": 2, "requirement gathering": 2,
        "gap analysis": 2, "uat": 2, "user acceptance testing": 2,
        "user stories": 2, "business requirement": 2,
        "functional requirement": 2, "business process mapping": 2,
        "system integration testing": 2, "stakeholder management": 2,
        "reports and dashboards": 2, "dashboards": 2, "user management": 2,
        "reports": 1, "process flow": 1, "integration testing": 1,
        "regression testing": 1, "test case": 1, "test cases": 1,
        "client communication": 1, "agile": 1, "sdlc": 1,
        "go-live": 1, "go live": 1, "change request": 1, "change requests": 1,
        "production support": 1, "end user training": 1, "knowledge base": 1,
        "data analysis": 1,
        # Domain experience — SFA/DMS delivery for automotive, FMCG, EV and
        # manufacturing clients (Greaves Electric, Parle Agro, L'Oréal, boAt,
        # JK Papers). This block is the part of her résumé a generic Salesforce BA
        # does NOT have, so it earns real weight on the postings that ask for it.
        "sales force automation": 3, "sfa": 2, "dms": 2,
        "dealer management": 2, "distributor management": 2,
        "secondary sales": 2, "field sales": 2, "sub-dealer": 2,
        "dealer onboarding": 2,
        "incentive": 1, "procurement": 1, "expense management": 1,
        "automotive": 1, "fmcg": 1, "manufacturing": 1,
        "excel": 1, "powerpoint": 1, "user manual": 1,
    },

    # -- "Both halves" bonus (was the full-stack bonus) -----------------------
    # A frontend/backend split is meaningless for a functional consultant, so the
    # two halves are REDEFINED (key names kept — scraper.py reads them by name):
    #   frontend_terms → Salesforce PLATFORM / CONFIGURATION half
    #   backend_terms  → BUSINESS ANALYSIS / delivery half
    # A job needing BOTH is a true functional-consultant role (not admin-only,
    # not BA-only) → is_fullstack=True, prints as "FS", and gets the bonus.
    # A consultant/analyst TITLE flags it outright.
    # "crm" deliberately NOT here: it let any CRM/ERP job satisfy the config half,
    # so the bonus needs genuine Salesforce vocabulary to fire.
    "frontend_terms": [
        "salesforce", "sales cloud", "service cloud", "experience cloud",
        "community cloud", "flow builder", "lightning",
        "validation rule", "validation rules",
        "approval process", "approval processes",
        "custom object", "custom objects",
        "permission set", "permission sets",
        "sharing rule", "sharing rules",
        "page layout", "page layouts", "record type", "record types",
        "role hierarchy", "soql",
    ],
    "backend_terms": [
        "requirement gathering", "business requirement", "functional requirement",
        "brd", "frd", "user stories", "gap analysis", "business analyst",
        "uat", "user acceptance testing", "stakeholder management",
        "business process", "business process mapping", "process flow",
        "process mapping", "change request", "change requests",
        "go-live", "go live", "system integration testing", "test case",
        "production support", "requirement analysis",
    ],
    "fullstack_bonus": 6,
    # Every term here must name the PLATFORM. Bare job-function titles were tried
    # and removed after measuring on live data:
    #   "business analyst"     → gave the bonus to "Business Analyst (Italian)",
    #                            "Japanese Business Analyst" (score 10, no Salesforce)
    #   "functional consultant" → gave it to "Oracle Fusion Functional Consultant",
    #                            which then ranked #1 overall at 43
    # Genuine Salesforce BA/FC roles still earn the bonus via the two-halves rule:
    # "salesforce" sits in the config half, "business analyst" in the analysis half.
    "fullstack_title_terms": [
        "salesforce consultant", "salesforce functional consultant",
        "salesforce business analyst", "salesforce analyst",
        "salesforce administrator", "salesforce admin",
        "salesforce business systems analyst", "salesforce functional analyst",
        "crm consultant", "crm functional consultant", "crm business analyst",
    ],

    # -- Down-ranking (penalty) ----------------------------------------------
    # Three groups. Note Apex is only -6, not -12: a functional JD that says
    # "exposure to Apex is a plus" should still rank — it's the DEVELOPER-titled
    # roles that get sunk, per the avoid-list.
    "penalty_terms": {
        # Salesforce *development* roles — the stated avoid-list
        "salesforce developer": -12, "apex developer": -12,
        "apex": -6, "lwc": -6,
        "lightning web component": -6, "lightning web components": -6,
        "visualforce": -5,
        # Off-domain: software engineering stacks (not a coder)
        "full stack": -8, "fullstack": -8, "mern": -8,
        "react": -6, "node.js": -6, "angular": -6,
        "java": -6, ".net": -6, "c#": -6, "php": -6,
        "javascript": -5, "devops": -5, "python": -4,
        # Competing CRM/ERP platforms with no hands-on experience. LIGHT penalty
        # (-3 to -6) — these weren't on the avoid-list, they're just a poor fit.
        # The Oracle/PeopleSoft entries were added after "Oracle Fusion Functional
        # Consultant" came back as the #1 ranked job on a live Naukri pull: the old
        # "oracle crm" term never matched "Oracle Fusion".
        "sap": -3, "microsoft dynamics": -3, "dynamics 365": -3,
        "dynamics": -3, "zoho": -3, "servicenow": -3, "siebel": -3,
        "oracle crm": -3, "oracle fusion": -6, "oracle erp": -6,
        "oracle ebs": -6, "peoplesoft": -6, "workday": -5, "netsuite": -5,
    },

    # Lead-gen farms, not employers. They repost other companies' listings under
    # their own name — one set of titles sprayed across country subdomains with
    # sequential LinkedIn IDs — so they match the résumé well and score at the
    # very top while being unapplyable. Measured on the 2026-07-26 sweep: 4 names
    # accounted for 11 of the 15 "reachable" rows at score >= 20. Dropped
    # outright, whatever they score. Matched on the company name, case- and
    # punctuation-insensitively ("SWAKIO™" -> "swakio"), whole name only, so a
    # real employer whose name merely contains one of these is unaffected.
    "company_blocklist": ["hired", "hire feed", "jobs ai", "swakio"],

    # -- Seniority filters ----------------------------------------------------
    # Two tiers, because a job TITLE is a label and not a requirement. The real
    # experience gate is SETTINGS["max_experience_years"], which reads the years
    # actually demanded by the text; these lists only handle the title.
    #
    # hard_drop_terms: never a fit at this experience level whatever the JD says.
    # Removed entirely (or penalized, if SETTINGS["drop_excluded"] is False).
    "hard_drop_terms": [
        "principal", "staff", "manager", "architect", "director",
        "head of", "vp", "chief",
    ],
    # soft_drop_terms: usually inflated titling, especially in international
    # remote, where "Senior" routinely means 3-4 years. NEVER dropped — only
    # down-ranked, so max_experience_years decides on the stated requirement
    # instead. Measured on a live sweep: hard-dropping these deleted 13 of 28
    # reachable remote roles whose JDs asked for <= 3 years (Twilio, Datadog,
    # Proxify, Lemon.io, A.Team).
    "soft_drop_terms": ["senior", "sr", "lead"],

    "drop_penalty": -15,   # hard drops, when drop_excluded is False
    "soft_penalty": -4,    # soft title match: sinks it, never removes it

    # Per hour of timezone gap beyond enrich.TZ_FREE_HOURS. Down-ranks rather
    # than drops, because a wide gap is a cost to weigh, not a disqualifier.
    "timezone_gap_penalty": -1.5,
}


# ===========================================================================
# 4. SETTINGS — filtering thresholds, cost guards, output knobs
# ===========================================================================
SETTINGS = {
    # Filtering
    # False, on purpose, and this is the single biggest volume lever in the file:
    # True DELETES every over-experienced and senior-titled row, so a "3-5 years"
    # posting she'd be a plausible stretch for never appears at all. False keeps
    # them and applies drop_penalty, so they sink below the roles she matches
    # instead of vanishing. min_score is None, so nothing is thrown away.
    "drop_excluded": False,
    # 4, not 3: ~1.7 yrs plus an MBA and a Salesforce Administrator certification
    # is a real candidate for a "2-4 years" posting, and India's Salesforce
    # postings cluster at 2-4. Anything demanding more is penalized, not dropped.
    "max_experience_years": 4,
    # How to combine several "N years" figures in one posting: "min" reads the
    # smallest as the real ask (right for short JDs, where anything larger is a
    # nice-to-have), "max" the largest (right for the long structured kind that
    # state a total AND a per-skill figure). See
    # scraper._required_experience_floor. "min" here: Naukri and LinkedIn
    # consultant postings are short.
    "experience_aggregate": "min",
    "min_score": None,           # drop jobs scoring below this after ranking (None = keep all, just sorted)
    # 21, not 14: consultancy and partner postings in India stay genuinely open
    # for weeks (they hire in batches against a client pipeline), unlike product
    # engineering roles. Two extra weeks of window is the cheapest volume there
    # is; drop back to 14 if replies dry up.
    "max_age_days": 21,
    "drop_undated": False,       # if True, also drop jobs whose posted date can't be parsed (default: keep them)
    # Minimum compensation, annualized and in USD, so an Indian LPA figure and a
    # US/EU salary are compared on the same axis (see scraper.comp_max_usd).
    # This REPLACES the old min_ctc_lpa, which the engine no longer reads at all —
    # leaving 8.0 in here would have silently applied no salary floor whatsoever.
    # 6000 USD ~= 5.2 LPA, so the 8.0 LPA floor converts to ~9200. Undisclosed,
    # unparseable, or unknown-currency pay is always KEPT — never drop on a guess.
    "min_comp_usd": 9200,        # None to disable

    # International-remote filters, from the signals enrich.py reads out of the
    # job text (visible as the remote_scope / visa / eor / timezones columns
    # whether or not you filter on them). ALL DEFAULT TO OFF: these read messy
    # prose, so a blank signal means "the posting didn't say", never "no", and
    # switching one on WILL drop jobs that simply forgot to mention it.
    #
    # remote_scopes: keep only these scopes. Values, most to least reachable:
    #   "worldwide"  explicitly hire from anywhere
    #   "remote"     remote, no geography stated
    #   "restricted" remote but geo-locked (check the remote_regions column)
    #   "hybrid" / "onsite" / "" (not stated)
    #
    # OFF (empty) on purpose, and this is the one place main's default is WRONG
    # here: main targets international remote, so it ships ["worldwide", "remote"],
    # which keeps only remote rows and DELETES every onsite and hybrid one. This
    # search is Tricity + NCR onsite/hybrid Salesforce work, i.e. exactly the rows
    # that filter throws away. Turn it on only if the target changes to remote.
    "remote_scopes": [],
    "drop_no_visa": False,       # drop only jobs that EXPLICITLY refuse to sponsor
    "require_eor": False,        # keep only jobs naming an employer-of-record path

    # Rescue geo-locked roles at employers who demonstrably hire where you are.
    # A company posting ANY job in HOME_LOCATION_HINTS has an entity or EOR there,
    # so its "US Remote" listing is worth an application; one with none is a dead
    # end whatever the wording. Costs nothing: those rows are already fetched.
    # Only ATS boards can answer it (a feed gives us no company board), so feed
    # rows are always "" and never rescued. Inert while remote_scopes is empty.
    "keep_restricted_if_hires_home": True,

    # Timezone distance from home, used to down-rank roles you couldn't sustain.
    # 5.5 = IST. Gaps up to enrich.TZ_FREE_HOURS (5h) are free; beyond that each
    # hour costs SCORING["timezone_gap_penalty"].
    "home_utc_offset": 5.5,      # None to skip timezone scoring entirely

    # Cost guards ("prove it cheaply first")
    # NOTE: misceres/indeed-scraper measured at ~$0.03 per 5 results (~$5 / 1000
    # results). Your $5 free credit is therefore ~1000 results total. max_spend_usd
    # below stops launching new runs once the run's cumulative cost hits it, so a
    # sweep self-limits well under the free tier.
    "max_spend_usd": None,          # None = no cap (cost headroom + backup API key available); set a $ value to self-limit
    "max_searches_per_site": None,  # cap (keyword x location) combos per site (None = full sweep; --limit overrides)
    "confirm_above_runs": 12,       # if planned actor runs exceed this, ask before spending (skip with --yes)
    "test_max_results": 5,          # max_results used by --test

    # Output
    "output_dir": "output",
    "top_n_console": 10,         # how many top jobs to print to the console
    "description_max": 20000,    # cap description length BEFORE scoring; keep large so
                                 # skills aren't cut off (ATS JDs start with long company
                                 # boilerplate). Description isn't an output column — this
                                 # only bounds pathological sizes, it doesn't limit scoring.
}


# ===========================================================================
# 5. PROFILES — named overlays so one scraper serves several people/searches
# ===========================================================================
# A profile is profiles/<name>.py defining ONLY the keys it wants to change:
#
#     SEARCH   = {"role_keywords": [...], "locations": [...]}
#     SETTINGS = {"remote_scopes": ["worldwide", "remote"]}
#
# Merge is one level deep: each top-level dict is .update()d, so a profile that
# sets SCORING["skill_weights"] replaces the whole stack while leaving
# penalty_terms alone. That is almost always what you want — a different person
# has a different stack, not extra terms bolted onto this one.
#
# Named profiles also get their own output/<name>/ directory, so two people's
# sweeps stop landing in the same folder (which is why output/ currently has
# hand-made archive-* subdirectories). The default profile keeps plain output/,
# so existing tooling and auto-apply/ are unaffected.
#
#     python scraper.py --profile srishti
#     JOB_PROFILE=srishti python scraper.py        # equivalent
#
# NOTE: the name is read HERE, at config import time, straight from sys.argv —
# not from parsed arguments. scraper.py precompiles its regex tables from SCORING
# at module level, so a profile applied any later would be silently ignored by
# the scoring layer. That is the one thing about this design worth remembering.
import os
import sys

OVERLAYABLE = ("SEARCH", "SITES", "SCORING", "SETTINGS", "ATS_BOARDS", "FEEDS",
               "OPTUM", "ENTERPRISE", "LOCATION_HINTS", "ATS_TITLE_HINTS",
               "ATS_TITLE_EXCLUDE")


def _selected_profile(argv=None, env=None):
    """Profile name from --profile NAME / --profile=NAME, else $JOB_PROFILE."""
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    for i, arg in enumerate(argv):
        if arg == "--profile" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--profile="):
            return arg.split("=", 1)[1]
    return env.get("JOB_PROFILE") or ""


def _overlay(module, target=None):
    """Apply one profile module's dicts onto the config globals. Returns the
    names it changed, for the run banner.

    A profile that OMITS a name inherits it. A profile that sets it to {} clears
    it — that is how a section is switched off. Those two cases have to be
    distinguished by presence, not truthiness: skipping falsy overrides meant
    `FEEDS = {}` silently inherited every default feed instead of disabling them,
    and a profile ran 28 sources it had explicitly opted out of.

    Dicts merge one level deep (.update); LIST settings (LOCATION_HINTS,
    ATS_TITLE_HINTS, ATS_TITLE_EXCLUDE) REPLACE wholesale, because they are
    single filter vocabularies — appending someone else's cities to yours would
    widen the filter instead of changing it. Without the isinstance branch a
    list override raised AttributeError (list has no .update), which is why
    those three could not be overlaid at all. Mutated in place either way, since
    scraper.py imports these names directly.
    """
    target = globals() if target is None else target
    changed = []
    for name in OVERLAYABLE:
        if not hasattr(module, name):
            continue
        override = getattr(module, name)
        if isinstance(target[name], list):
            target[name][:] = override or []
        elif override:
            target[name].update(override)
        else:
            target[name].clear()
        changed.append(name)
    return changed


PROFILE = _selected_profile()
PROFILE_CHANGED = []
if PROFILE:
    import importlib
    try:
        _module = importlib.import_module(f"profiles.{PROFILE}")
    except ImportError as exc:
        # Loud, not silent: falling back to the default profile would quietly run
        # someone else's search and cost real money doing it.
        _available = sorted(
            f.removesuffix(".py")
            for f in os.listdir(os.path.join(os.path.dirname(__file__), "profiles"))
            if f.endswith(".py") and not f.startswith("_"))
        sys.exit(f"Unknown profile '{PROFILE}' ({exc}). "
                 f"Available: {', '.join(_available) or '(none)'}")
    PROFILE_CHANGED = _overlay(_module)
    # Keep each person's sweeps apart unless the profile picks its own directory.
    if "SETTINGS" not in PROFILE_CHANGED or "output_dir" not in getattr(_module, "SETTINGS", {}):
        SETTINGS["output_dir"] = os.path.join("output", PROFILE)


def demo():
    """Self-check for the overlay rules. `python config.py` — offline."""
    assert _selected_profile(["scraper.py"], {}) == ""
    assert _selected_profile(["s", "--profile", "bob"], {}) == "bob"
    assert _selected_profile(["s", "--profile=bob"], {}) == "bob"
    assert _selected_profile(["s"], {"JOB_PROFILE": "bob"}) == "bob"
    assert _selected_profile(["s", "--profile", "bob"], {"JOB_PROFILE": "eve"}) == "bob"
    assert _selected_profile(["s", "--profile"], {}) == ""       # no value, no crash

    # One level deep: the named sub-dict is REPLACED, its siblings survive.
    class Fake:
        SCORING = {"skill_weights": {"go": 9}}
        SETTINGS = {"min_comp_usd": 40000}
    target = {"SCORING": {"skill_weights": {"react": 5}, "penalty_terms": {"php": -6}},
              "SETTINGS": {"min_comp_usd": 6000, "max_age_days": 14},
              "SEARCH": {}, "SITES": {}, "ATS_BOARDS": {}, "FEEDS": {}}
    changed = _overlay(Fake, target)
    assert target["SCORING"]["skill_weights"] == {"go": 9}         # replaced
    assert target["SCORING"]["penalty_terms"] == {"php": -6}       # untouched sibling
    assert target["SETTINGS"] == {"min_comp_usd": 40000, "max_age_days": 14}
    assert sorted(changed) == ["SCORING", "SETTINGS"]

    # Omitted = inherit, {} = switch off. Distinguished by presence, not
    # truthiness — treating {} as "nothing to do" made a profile silently run
    # every source it had opted out of.
    class Off:
        FEEDS = {}
    target = {"SEARCH": {}, "SITES": {}, "SCORING": {}, "SETTINGS": {},
              "ATS_BOARDS": {"greenhouse": {"x": "X"}},
              "FEEDS": {"wwr": {"enabled": True}}}
    changed = _overlay(Off, target)
    assert target["FEEDS"] == {}, target["FEEDS"]                  # cleared
    assert target["ATS_BOARDS"] == {"greenhouse": {"x": "X"}}      # omitted -> kept
    assert changed == ["FEEDS"]

    # LIST settings replace wholesale rather than merging, and an empty list
    # clears them. Before the isinstance branch this raised AttributeError, so a
    # profile's LOCATION_HINTS / ATS_TITLE_* were not overlayable at all — a
    # profile could name its own title gates and silently run the defaults.
    class Lists:
        LOCATION_HINTS = ["india", "noida"]
        ATS_TITLE_EXCLUDE = []
    target = {"SEARCH": {}, "SITES": {}, "SCORING": {}, "SETTINGS": {},
              "ATS_BOARDS": {}, "FEEDS": {},
              "LOCATION_HINTS": ["berlin"],
              "ATS_TITLE_HINTS": ["developer"],
              "ATS_TITLE_EXCLUDE": ["sre"]}
    changed = _overlay(Lists, target)
    assert target["LOCATION_HINTS"] == ["india", "noida"]   # replaced, not merged
    assert target["ATS_TITLE_EXCLUDE"] == []               # [] clears it
    assert target["ATS_TITLE_HINTS"] == ["developer"]      # omitted -> kept
    assert sorted(changed) == ["ATS_TITLE_EXCLUDE", "LOCATION_HINTS"]
    print(f"demo ok (active profile: {PROFILE or 'default'})")


if __name__ == "__main__":
    demo()
