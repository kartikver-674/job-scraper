"""
Configuration for a SALES / BUSINESS DEVELOPMENT job scraper.

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
    3. SCORING  — resume-based relevance weights, hunter+closer bonus, exclude/down-rank
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
# scoring layer), but full-stack terms are listed first by intent.
SEARCH = {
    "country": "IN",            # Indeed country code (IN, US, GB, ...)
    # ~1 yr: PlanetSpark intern Sep 2025 -> BD Associate Dec 2025 -> Senior BD
    # Associate Apr 2026. Ask sites for 1 so fresher and 1-yr postings both come
    # back; the ceiling is SETTINGS["max_experience_years"].
    "experience_years": 1,
    "salary_min": None,         # DELIBERATELY None: Indian sales postings quote a
                                # low fixed CTC plus incentives, and a site-side
                                # salary filter drops most of them outright.
    "max_results": 15,          # jobs PER (keyword x location) search — pay-per-event

    # Each entry is run as its OWN search term. Sales / business-development
    # titles only — nothing technical, which is the whole point of this profile.
    "role_keywords": [
        "Business Development Associate",
        "Business Development Executive",
        "Business Development Manager",
        "Inside Sales Executive",
        "Inside Sales Associate",
        "Sales Development Representative",
        "Sales Executive",
        "Account Executive",
        "Academic Counsellor",
        "Admission Counsellor",
        "Client Relationship Executive",
    ],

    # Home is Gurugram, and NCR is where the EdTech sales market actually is
    # (PlanetSpark, upGrad, Unacademy, Vedantu, Physics Wallah all hire here).
    # Bengaluru and Mumbai are added for reach, not because a move is planned.
    "locations": [
        "Gurgaon", "Gurugram", "Delhi", "New Delhi", "Noida",
        "Bengaluru", "Mumbai", "Remote",
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
    "smartrecruiters": {},   # e.g. {"BoschGroup": "Bosch"}
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
    # OFF. UnitedHealth Group's India centres are technology and healthcare
    # operations; they do not run a sales floor here, so ~59 free requests would
    # buy nothing. Left wired in case that changes.
    "enabled": False,
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
    # ON. Amazon and Accenture both hire sales and account-management roles in
    # India at this level ("Account Manager", "Business Development Manager",
    # "Sales Capture"), and both boards read for free. JPMorgan is included
    # because its Oracle board carries relationship-management seats.
    "enabled": True,
    # oracle and sap are OUT: their India postings are overwhelmingly product
    # engineering, and Oracle Recruiting Cloud publishes no JD body to score.
    "employers": ["amazon", "accenture", "jpmorgan"],
    # NOT [""] (the whole index) — these boards run tens of thousands of
    # postings. The keyword goes straight to each platform's own search, so this
    # asks each employer for its sales openings and nothing else.
    "keywords": ["Business Development", "Sales"],
    "max_pages": 6,
    "verify_live": True,
}

# Public remote-job feeds. No auth, no cost. Adapters live in sources/feeds.py
# (registered in sources.FEED_FETCHERS). The three structured JSON feeds below
# are the only free source that reports PAY — the ATS boards never do.
FEEDS = {
    "remoteok": {"enabled": True},
    # main's categories here are the four PROGRAMMING feeds, which for this
    # résumé is four requests that can only return rows the title gate then
    # deletes. Probed 2026-08-25: "remote-sales-jobs" and "remote-business-jobs"
    # both 301 (they do not exist), "remote-sales-and-marketing-jobs" returns 64
    # items, "remote-customer-support-jobs" 16. Verified, not guessed — a wrong
    # category is a silent zero, not an error.
    "wwr": {"enabled": True, "categories": [
        "remote-sales-and-marketing-jobs",
        "remote-customer-support-jobs",
    ]},
    "remotive": {"enabled": True},
    "jobicy": {"enabled": True, "count": 50},
    # No category filter exists on this API, so it pages blind through ~96k
    # mostly non-engineering jobs at 20 a time. Worth it for the exact UTC
    # offsets it reports, but raise `pages` only if you want the requests.
    "himalayas": {"enabled": True, "pages": 10},
}

# Keep a free-source job only if its location mentions one of these.
# EMPTY = allow every location, which is the right default now that the target
# is international remote — remote/visa/comp filters do the narrowing instead of
# a country whitelist. An empty job location is always kept.
# To go back to India-only, copy HOME_LOCATION_HINTS below into this list.
LOCATION_HINTS = [
    # main ships this EMPTY (allow everything) because its target is
    # international remote. Empty here would pour US and EU rows off ~37 free
    # boards into an NCR sales search. Gurugram first — that is where she lives
    # and where the EdTech sales market is.
    "india", "gurugram", "gurgaon", "delhi", "new delhi", "ncr", "noida",
    "greater noida", "faridabad", "ghaziabad",
    "bengaluru", "bangalore", "mumbai", "pune", "hyderabad",
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
# Free sources return a whole board (engineering, finance, ops, HR, ...), so
# unlike a job board we can't keyword-search. Keep only jobs whose TITLE looks
# like a SALES role. Retuned wholesale from main's software vocabulary
# ("developer", "react", "sde"), which would have kept exactly zero relevant rows
# and made the free-source layer look like it worked while returning nothing.
#
# Deliberately NOT here: bare "analyst", "marketing", "operations", "consultant",
# "associate", "executive". Each was considered and each is a different job far
# more often than it is hers — on a free board "Executive" alone matches finance,
# HR and admin seats, and there is no cheap way to tell them apart later.
ATS_TITLE_HINTS = [
    "business development", "inside sales", "sales development",
    "sales executive", "sales associate", "sales representative",
    "sales manager", "sales specialist", "field sales", "channel sales",
    "account executive", "account manager", "key account",
    "client relationship", "client servicing", "customer success",
    "counsellor", "counselor", "admission", "enrollment", "enrolment",
    "revenue", "growth associate", "growth executive",
]

# Titles to reject even when they DO match a hint above. Checked first, so it
# wins — which is the only way to keep out a role that borrows a software title
# for a different job ("Senior Software Engineer - Data Engineer, Spark, ETL").
# Empty by default: it earns its keep when the hints are broadened past one
# stack, where a wider net starts catching adjacent careers. Seniority does NOT
# belong here — SCORING["hard_drop_terms"] already handles it, and as a penalty
# rather than a silent delete.
# Checked BEFORE the hints, so it wins. This is the belt to hard_drop_terms'
# braces: a title can satisfy a sales hint and still be a technical seat
# ("Sales Engineer", "Technical Account Manager", "Solutions Architect - Revenue
# Systems"). Catching it here means the row never even costs a JD request.
ATS_TITLE_EXCLUDE = [
    "engineer", "developer", "architect", "technical", "software",
    "data analyst", "data scientist", "analytics", "devops", "qa",
    "designer", "recruiter", "marketing manager",
]

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
# BOUNDARIES (so ".net" and "node.js" match cleanly and "lead" won't match
# "leadership"), against the terms below. Positive weights add to the score;
# penalty terms subtract; a full-stack bonus rewards frontend+backend overlap.
SCORING = {
    # -- Positive skill weights (higher = more central to the resume) ---------
    # Everything here is on the résumé: consultative selling, lead generation and
    # qualification, customer acquisition, sales presentations, negotiation,
    # client relationship management, CRM and pipeline management, sales strategy,
    # market research, target achievement — all of it B2C EdTech at PlanetSpark.
    #
    # WEIGHTED BY DISCRIMINATIVE POWER, not by how central it is to her. "Sales"
    # and "communication" appear in half the job ads in India; "consultative
    # selling", "inside sales", "lead qualification" and "SDR" appear in the ones
    # she actually wants. So the function-specific vocabulary dominates and the
    # soft-skill vocabulary is supporting signal only.
    "skill_weights": {
        # The function itself — must dominate.
        "business development": 10, "inside sales": 9,
        "consultative selling": 9, "consultative sales": 9,
        "sales development representative": 9, "sdr": 7, "bdr": 7,
        "account executive": 7, "sales executive": 7,
        # The sales cycle, as her résumé describes it.
        "lead generation": 6, "lead qualification": 6, "prospecting": 6,
        "customer acquisition": 6, "sales pipeline": 6, "pipeline management": 5,
        "cold calling": 5, "outbound": 5, "new business": 5,
        "sales presentation": 4, "sales presentations": 4,
        "product demo": 3, "negotiation": 4, "closing": 3, "conversion": 3,
        "upsell": 3, "cross-sell": 3, "revenue growth": 4, "sales target": 5,
        "quota": 4, "target achievement": 3, "targets": 2,
        # Accounts and relationships — the second half of her job.
        "client relationship": 6, "relationship management": 5,
        "account management": 5, "account manager": 6, "key account": 5,
        "client servicing": 5,
        "customer engagement": 4, "customer success": 4, "retention": 3,
        "stakeholder engagement": 3, "follow-up": 2, "follow up": 2,
        # Tools and operations she names.
        "crm": 5, "crm management": 5, "salesforce": 3, "hubspot": 3,
        "leadsquared": 3, "zoho": 2, "sales strategy": 4, "market research": 3,
        "excel": 2,
        # Her domain. EdTech B2C counselling is the single most transferable
        # thing on the résumé, so it is weighted like a skill, not a nice-to-have.
        "edtech": 7, "ed-tech": 7, "counsellor": 6, "counselor": 6,
        "counselling": 5, "counseling": 5, "admission": 5, "admissions": 5,
        "enrollment": 5, "enrolment": 5, "student": 3, "learner": 2,
        "b2c": 5, "b2b": 3, "saas": 2,
        # Adjacent markets a B.Tech Biotechnology degree genuinely opens, and
        # nothing else on this résumé does: pharma / medical-device / life-science
        # sales hires science graduates into exactly this kind of role.
        "pharma": 3, "pharmaceutical": 3, "medical device": 3,
        "life sciences": 3, "biotech": 3, "healthcare": 2, "diagnostics": 2,
        # Generic strengths — real, but every ad says them. Support only.
        "communication": 1, "interpersonal": 1, "team collaboration": 1,
        "presentation": 1, "customer service": 1,
    },

    # -- "Both halves" bonus (was the full-stack bonus) -----------------------
    # A frontend/backend split is meaningless for a salesperson, so the two halves
    # are REDEFINED (key names kept — scraper.py reads them by name):
    #   frontend_terms → HUNTING: finding and winning new business
    #   backend_terms  → CLOSING / FARMING: converting and then keeping the client
    # Her résumé is explicitly both ("own the sales pipeline end to end"), and a
    # role needing both is a full-cycle sales job rather than a pure dialler seat
    # or a pure support desk → is_fullstack=True, prints as "FS", gets the bonus.
    "frontend_terms": [
        "business development", "lead generation", "lead qualification",
        "prospecting", "cold calling", "outbound", "new business",
        "customer acquisition", "sdr", "bdr", "market research",
    ],
    "backend_terms": [
        "negotiation", "closing", "conversion", "client relationship",
        "relationship management", "account management", "customer engagement",
        "customer success", "retention", "upsell", "cross-sell", "crm",
        "pipeline management", "follow-up", "follow up", "counselling",
        "counseling",
    ],
    "fullstack_bonus": 6,
    # Titles that ARE the job, so they earn the bonus outright.
    "fullstack_title_terms": [
        "business development associate", "business development executive",
        "business development manager", "business development representative",
        "inside sales", "sales development representative",
        "account executive", "account manager", "sales executive",
        "sales associate", "customer success",
        "academic counsellor", "academic counselor",
        "admission counsellor", "admission counselor",
        "client relationship", "key account",
    ],

    # -- Down-ranking (penalty) ----------------------------------------------
    # TECHNICAL VOCABULARY IN THE BODY. hard_drop_terms below removes technical
    # TITLES outright; this is the second line, for a sales-titled posting that
    # turns out to be a technical seat ("Sales Engineer", "Technical Account
    # Manager", a developer role at a startup that called it "Growth"). Heavy,
    # because she is a Biotechnology graduate in sales, not a technologist, and a
    # technical role is not a weaker match — it is the wrong job.
    "penalty_terms": {
        "software development": -12, "software engineering": -12,
        "programming": -10, "coding": -10, "codebase": -10,
        "python": -8, "java": -8, "javascript": -8, "typescript": -8,
        "react": -8, "node.js": -8, "angular": -8, ".net": -8, "c++": -8,
        "php": -8, "golang": -8, "kotlin": -8, "swift": -8,
        "sql": -6, "nosql": -6, "mongodb": -6, "postgresql": -6,
        "aws": -6, "azure": -6, "gcp": -6, "kubernetes": -8, "docker": -8,
        "devops": -8, "ci/cd": -8, "microservices": -8, "rest api": -6,
        "machine learning": -8, "deep learning": -8, "tensorflow": -8,
        "data pipeline": -8, "etl": -8, "hadoop": -8, "spark": -8,
        "linux": -6, "git": -6, "github": -4, "jira": -2,
        "figma": -6, "ux design": -6, "ui design": -6, "wireframe": -6,
        # Functions that are not sales, however the title is dressed up.
        "recruitment": -8, "talent acquisition": -8, "payroll": -8,
        "accounts payable": -8, "bookkeeping": -8, "taxation": -8,
        "audit": -6, "legal counsel": -8, "litigation": -8,
        # Seniority she cannot reach at ~1 year, as a penalty not a delete —
        # Indian sales titling is inflated and "Manager" often means 2 years.
        "10+ years": -10, "8+ years": -8,
    },

    # -- Employers whose postings are never worth an application --------------
    # Whole-name match (see scraper.norm_company), so a real employer whose name
    # merely CONTAINS one of these is untouched.
    "company_blocklist": ["hired", "hire feed", "jobs ai", "swakio"],

    # -- Hard filters (wrong seniority / wrong profession entirely) -----------
    # hard_drop_terms is matched against the TITLE ONLY, and removed outright
    # (or penalized, if SETTINGS["drop_excluded"] is False).
    #
    # This is the list that delivers "not one technical job". It is long on
    # purpose: the engine's own defaults are a software-engineering vocabulary,
    # so every technical word has to be named here rather than assumed absent.
    # Note "engineer" is here without qualification, which also removes "Sales
    # Engineer" and "Solutions Engineer" — correct for this résumé, those are
    # pre-sales technical seats.
    "hard_drop_terms": [
        # Technical
        "developer", "engineer", "engineering", "sde", "programmer",
        "architect", "devops", "sre", "qa", "quality assurance", "tester",
        "testing", "technician", "technical", "software", "full stack",
        "fullstack", "frontend", "front-end", "backend", "back-end",
        "data scientist", "data science", "data engineer", "data analyst",
        "analytics", "machine learning", "ai/ml", "cybersecurity",
        "network", "database", "dba", "sysadmin", "system administrator",
        "cloud", "scrum master", "designer", "ux", "ui/ux",
        # Adjacent-but-not-sales roles that leaked through on a title check
        # scoring 0: they are neither technical nor hers, and a row that scores
        # zero still occupies a line on the shortlist.
        "business analyst", "product manager", "product owner", "growth hacker",
        # Other professions entirely
        "recruiter", "recruitment", "talent acquisition", "human resources",
        "accountant", "finance manager", "auditor", "lawyer", "advocate",
        "teacher", "tutor", "faculty", "professor", "trainer",
        "nurse", "doctor", "physician", "pharmacist", "therapist",
        "driver", "warehouse", "chef", "receptionist", "security guard",
        # Seniority genuinely out of reach at ~1 year. "Manager" is NOT here:
        # in Indian sales, Business Development Manager is routinely a 1-3 year
        # title and dropping it would delete her most likely next step.
        "head of", "vp", "vice president", "avp", "director", "chief",
        "president", "general manager", "zonal", "national head",
    ],
    # soft_drop_terms: inflated titling. NEVER dropped — only down-ranked, so
    # max_experience_years decides on the stated requirement instead.
    "soft_drop_terms": ["senior", "sr", "lead"],

    "drop_penalty": -15,   # hard drops, when drop_excluded is False
    "soft_penalty": -4,    # soft title match: sinks it, never removes it

    # Timezone distance from home, in points per hour past enrich.TZ_FREE_HOURS.
    "timezone_gap_penalty": -1.5,
}


# ===========================================================================
# 4. SETTINGS — filtering thresholds, cost guards, output knobs
# ===========================================================================
SETTINGS = {
    # Filtering
    "drop_excluded": True,       # True: filter out title-seniority + over-experienced roles
                                 # False: keep them but apply drop_penalty (they sink)
    # 2, her real ceiling (~1 yr at PlanetSpark, Sep 2025 to now). Note the
    # comparison is `floor > max_experience_years`, so 2 lets a "2+ years" and a
    # "1-3 years" posting through and stops "3+ years" — one higher and every
    # 3-year role reappears.
    "max_experience_years": 2,
    # How to combine several "N years" figures in one posting: "min" reads the
    # smallest as the real ask (right for short JDs, where anything larger is a
    # nice-to-have), "max" the largest (right for the long structured kind that
    # state a total AND a per-skill figure). See
    # scraper._required_experience_floor.
    "experience_aggregate": "min",
    # 5, and it is the LAST line of defence behind hard_drop_terms and
    # ATS_TITLE_EXCLUDE. Anything technical that gets past both lists arrives
    # carrying the penalty_terms above and lands at or below zero, so a positive
    # floor removes it whatever its title said. A genuine sales posting clears 5
    # on its title alone ("Business Development Associate" scores 16 with an
    # empty description), so this costs nothing real.
    "min_score": 5,
    # 14. Indian sales hiring moves fast and reposts constantly, so an old
    # posting is usually a closed one — the opposite of the consultancy pattern.
    "max_age_days": 14,
    "drop_undated": False,       # if True, also drop jobs whose posted date can't be parsed (default: keep them)
    # Minimum compensation, annualized and in USD, so an Indian LPA figure and a
    # US/EU salary are compared on the same axis (see scraper.comp_max_usd).
    # 6000 USD ~= the old 5.2 LPA floor. Undisclosed, unparseable, or
    # unknown-currency pay is always KEPT — we never drop on a guess.
    # None, deliberately. Entry-level Indian sales is quoted as a low fixed CTC
    # plus incentives (3-5 LPA fixed is normal and the earnings are in the
    # variable), so any floor here filters on the least meaningful half of the
    # package. Comp is a conversation to have at offer stage, not a gate.
    "min_comp_usd": None,

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
    # For remote roles workable from India, start with ["worldwide", "remote"].
    #
    # ON, because off was worse than useless: the 2026-07-26 sweep returned 480
    # jobs at score >= 10 of which 27 were actually reachable from India — 245 of
    # the top 252 were "remote" only within Germany / Spain / UAE / the UK. The
    # filter keeps "restricted" rows whose lock is TO India, so India-remote roles
    # (which LinkedIn labels restricted) survive — see scraper.finalize.
    # OFF (empty). main targets international remote; this search is NCR sales,
    # which is onsite and field work — the exact rows ["worldwide", "remote"]
    # deletes. Leaving main's default here would have emptied the sweep.
    "remote_scopes": [],
    "drop_no_visa": False,       # drop only jobs that EXPLICITLY refuse to sponsor
    "require_eor": False,        # keep only jobs naming an employer-of-record path

    # Rescue geo-locked roles at employers who demonstrably hire where you are.
    # A company posting ANY job in HOME_LOCATION_HINTS has an entity or EOR there,
    # so its "US Remote" listing is worth an application; one with none is a dead
    # end whatever the wording. Measured: Postman 12/114 India jobs, OpenAI 9/753,
    # Druva 11/31 -> yes. Linear 0/25, Ramp 0/118 -> no. Costs nothing: those rows
    # are already fetched. Only ATS boards can answer it (a feed gives us no
    # company board), so feed rows are always "" and never rescued.
    "keep_restricted_if_hires_home": True,

    # Timezone distance from home, used to down-rank roles you couldn't sustain.
    # 5.5 = IST. Gaps up to enrich.TZ_FREE_HOURS (5h) are free; beyond that each
    # hour costs SCORING["timezone_gap_penalty"]. IST->CET is 4.5h (fine),
    # IST->US-Pacific is 13.5h (why so many US companies won't hire from India).
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
