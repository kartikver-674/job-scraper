"""
Configuration for the full-stack job scraper.

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
    3. SCORING  — resume-based relevance weights, full-stack bonus, exclude/down-rank
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
    "experience_years": 4,      # 4+ yrs (Jun 2022 -> present); passed to sites that support it
    "salary_min": None,         # optional minimum salary; None to skip
    "max_results": 15,          # jobs PER (keyword x location) search — keep modest, pay-per-event (~$5/1000 results)

    # Each entry is run as its OWN search term. Kanav ships THREE resumes for
    # three different job families, so the keyword list covers all three rather
    # than optimising one: React Native / mobile, pure frontend React, and
    # full-stack MERN. They are the same skill set framed three ways, so a role
    # from any family is a real target and none of them should be a stretch.
    "role_keywords": [
        # -- React Native / mobile (KanavReactNative.pdf) ---------------------
        "React Native Developer",
        "React Native Engineer",
        "Mobile Application Developer",
        # -- Frontend React (KanavFrontEnd.pdf) ------------------------------
        "Frontend Developer",
        "React Developer",
        "React.js Developer",
        "Frontend Engineer",
        "UI Developer",
        # -- Full-stack MERN (KanavKhera-FullStack.pdf) ----------------------
        "Full Stack Developer",
        "MERN Stack Developer",
        "Node.js Developer",
        "Software Engineer JavaScript",
    ],

    # He lives in New Delhi and currently works in Chandigarh/Mohali, so the
    # Chandigarh belt is a first-class location here and not an afterthought.
    #
    # NOTE for paid LinkedIn runs: there is NO verified Chandigarh or Mohali
    # geoId. Five plausible candidates were probed against the free guest search
    # and every one of them was somewhere else entirely -- 106262505 is Galway
    # (Ireland), 115976306 is Glasgow (Scotland), 104990346 is Ahmedabad,
    # 115884833 is Gurugram. Adding any of them would have billed a full search
    # for the wrong city. The plain "India" geoId returns Mohali rows on its own
    # (LinkedIn writes them "Sahibzada Ajit Singh Nagar, Punjab, India"), and the
    # free ATS/feed sources are not geoId-bound at all, so the belt stays covered
    # without guessing. The names below are used by Indeed and the free sources.
    "locations": [
        "Chandigarh", "Mohali", "Delhi", "New Delhi", "Gurgaon", "Noida",
        "Bengaluru", "Hyderabad", "Pune", "Remote",
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
    # Chandigarh tricity, VERIFIED 2026-08-26: 10/10 job cards land in it --
    # four written "Chandigarh, Chandigarh, India", six by district as
    # "Sahibzada Ajit Singh Nagar, Punjab, India" or "Sas Nagar" (both Mohali).
    # Found by scraping the public jobs-search page for the location, after five
    # blind guesses resolved to Galway, Glasgow, Ahmedabad, Gurugram and nothing.
    # verify_geoids.py reported MISMATCH on it until its alias table learned the
    # district spellings, which is worth remembering: that checker rejects a
    # correct id whenever LinkedIn's label and our name disagree.
    "Chandigarh": "100139308",
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
    "enabled": False,
    "employers": ["amazon", "jpmorgan", "oracle", "accenture", "sap"],
    "keywords": [""],
    "max_pages": 5,
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
    "himalayas": {"enabled": True, "pages": 10},
}

# Keep a free-source job only if its location mentions one of these.
# EMPTY = allow every location, which is the right default now that the target
# is international remote — remote/visa/comp filters do the narrowing instead of
# a country whitelist. An empty job location is always kept.
# To go back to India-only, copy HOME_LOCATION_HINTS below into this list.
LOCATION_HINTS = []

# Where YOU are. Not a filter — this is how a company board is checked for
# whether the employer hires in your country at all
# (SETTINGS["keep_restricted_if_hires_home"]).
HOME_LOCATION_HINTS = [
    "india", "delhi", "ncr", "gurgaon", "gurugram", "noida", "faridabad",
    "ghaziabad", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai",
    "chennai", "kolkata", "ahmedabad", "jaipur", "indore",
    # The Chandigarh belt, where he works now. LinkedIn writes Mohali as
    # "Sahibzada Ajit Singh Nagar, Punjab, India", hence the district and state.
    "chandigarh", "mohali", "panchkula", "zirakpur", "sahibzada ajit singh",
    "punjab", "haryana",
]

# Free sources return a whole board (finance, ops, HR, ...), so unlike job boards
# we can't keyword-search. Keep only jobs whose TITLE looks like a software/dev
# role (case-insensitive substring). Scoring then ranks within these.
ATS_TITLE_HINTS = [
    "developer", "full stack", "fullstack", "full-stack", "frontend", "front end",
    "front-end", "backend", "back end", "back-end", "software engineer",
    "software development", "sde", "react", "node", "javascript", "typescript",
    "web developer", "mern", "mobile developer", "application developer",
    # His three resumes, three job families.
    "react native", "mobile application", "mobile engineer", "ui developer",
    "ui engineer", "web engineer", "javascript engineer", "software developer",
]

# Titles to reject even when they DO match a hint above. Checked first, so it
# wins — which is the only way to keep out a role that borrows a software title
# for a different job ("Senior Software Engineer - Data Engineer, Spark, ETL").
# Empty by default: it earns its keep when the hints are broadened past one
# stack, where a wider net starts catching adjacent careers. Seniority does NOT
# belong here — SCORING["hard_drop_terms"] already handles it, and as a penalty
# rather than a silent delete.
ATS_TITLE_EXCLUDE = [
    # The hints above are deliberately wide (three job families), and a wide net
    # starts catching adjacent careers that merely borrow a software title.
    # Checked BEFORE the hints and wins, so these are removed outright.
    "data engineer", "data scientist", "machine learning", "ml engineer",
    "devops", "sre", "site reliability", "salesforce", "sap", "dynamics",
    "android developer", "ios developer",   # native-only; he is React Native
    "flutter", "unity", ".net", "java developer", "python developer",
    "php", "wordpress", "drupal", "qa ", "test engineer", "automation engineer",
    "embedded", "firmware", "game developer", "business analyst",
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
    "skill_weights": {
        # -- Core, on all three resumes ---------------------------------------
        "react": 6, "react.js": 6, "react native": 6,
        "javascript": 5, "typescript": 5,
        "node": 5, "node.js": 5, "express": 5, "mongodb": 5,
        # -- React ecosystem, named explicitly --------------------------------
        # The frontend resume lists these as first-class skills, so they are
        # scored as skills and not as incidental mentions.
        "redux": 4, "redux toolkit": 4, "react hooks": 3, "context api": 3,
        "react router": 3, "react navigation": 3, "hooks": 2, "vite": 2,
        "next.js": 2,          # not on the resume; a React meta-framework is
                               # still a strong signal that this IS a React job
        # -- React Native depth. The distinctive ones: a posting that says
        # "TurboModules" or "Hermes" was written by someone who actually does
        # this work, and he has shipped against the New Architecture.
        "turbomodules": 5, "turbo modules": 5, "fabric": 4, "jsi": 4,
        "hermes": 4, "native modules": 4, "offline-first": 3, "offline first": 3,
        "expo": 3, "eas build": 3, "flatlist": 3, "detox": 3,
        "swift": 3, "kotlin": 3,       # native modules written in both
        "fastlane": 3, "gradle": 2, "xcode": 2, "android studio": 2,
        "play store": 2, "google play console": 2, "app center": 2,
        "ios": 2, "android": 2, "cross-platform": 3, "cross platform": 3,
        # -- Backend / real-time / auth ---------------------------------------
        "redis": 3, "socket.io": 3, "websocket": 3, "websockets": 3,
        "jwt": 3, "oauth": 2, "rest api": 3, "restful": 3, "mongoose": 3,
        "firebase": 3, "fcm": 2, "authentication": 2, "concurrency": 2,
        "sql": 2, "mysql": 2, "docker": 2, "ci/cd": 3, "cloudinary": 1,
        # -- Frontend craft. The whole point of KanavFrontEnd.pdf, and worth
        # real points rather than the 1 they carried on the branch this was cut
        # from: for a frontend role these ARE the job.
        "html": 2, "html5": 2, "css": 2, "css3": 2, "es6": 2,
        "responsive design": 3, "responsive": 2, "design system": 3,
        "design systems": 3, "component library": 3, "reusable components": 3,
        "cross-browser": 2, "accessibility": 1,
        # -- Performance work, quantified on every resume (10,000+ record
        # tables, low-end Android, 5,000+ users).
        "memoization": 3, "code splitting": 3, "lazy loading": 3,
        "virtualization": 3, "virtualized": 3, "pagination": 2,
        "debounce": 2, "caching": 2, "performance optimization": 3,
        "performance profiling": 2, "bundle size": 2, "web vitals": 1,
        # -- Testing / quality ------------------------------------------------
        "jest": 3, "rntl": 3, "react testing library": 3, "unit testing": 2,
        "unit tests": 2, "e2e": 1, "code review": 2, "code reviews": 2,
        "agile": 1, "scrum": 1,
        # -- AI-assisted development. Genuinely resume-backed here, not
        # aspirational: Claude Code, Copilot, Cursor, Google Antigravity and
        # prompt engineering are listed as a skills section, and ParkAssist
        # ships LLM orchestration over statutory text.
        "claude": 3, "claude code": 3, "github copilot": 2, "copilot": 2,
        "cursor": 2, "ai-assisted": 3, "ai assisted": 3,
        "prompt engineering": 3, "llm": 3, "llms": 3, "gen ai": 2,
        "generative ai": 2, "ai agent": 2, "ai agents": 2, "agentic": 2,
        "rag": 2, "langchain": 1, "openai": 1, "mcp": 1,
        # -- Named engineering work from the projects, distinctive enough to be
        # worth points on their own: compare-and-swap and multi-document Mongo
        # transactions, rotating refresh-token families with theft detection,
        # 2dsphere geospatial search, a rate limiter, an escrow ledger.
        "transactions": 2, "rate limiting": 2, "rate limiter": 2,
        "refresh token": 2, "geospatial": 2, "compare-and-swap": 2,
        "security headers": 1, "deep linking": 2, "push notifications": 2,
        # -- Domain. Four years of it, across both employers: dealer management,
        # CRM, field sales, order lifecycle, plus e-commerce and marketplace
        # side projects. See the penalty block for why "crm" is scored here
        # rather than penalised.
        "crm": 2, "dealer management": 2, "field sales": 2, "e-commerce": 1,
        "ecommerce": 1, "marketplace": 1, "erp": 1, "dms": 1,
        # AI / agentic work. Absent from the base model until now, which meant the
        # default profile scored his MCP and agent work at zero — the two hits
        # that looked like coverage, "tailwind" and "html", were substring
        # coincidences ("ai" in tailwind, "ml" in html). Same weights as
        # profiles/optum.py and profiles/bigtech.py already use, so the three
        # agree instead of ranking the same posting differently.
        "llm": 2, "llms": 2, "gen ai": 2, "rag": 2, "langchain": 2,
        "agentic": 2, "ai agent": 2, "ai agents": 2, "prompt engineering": 2,
        "hugging face": 2, "huggingface": 2, "transformers": 2, "nlp": 2,
        "openai": 1, "embeddings": 1, "vector database": 1, "mcp": 1,
        # RÉSUMÉ-BACKED and missing until now. B.E. Computer Science —
        # Artificial Intelligence & Machine Learning, plus a GAN image-restoration
        # project in Python/TensorFlow/OpenCV. Python especially: it is asked for
        # by a large share of the postings this sweep will see, and the model
        # scored it at zero.
        "python": 3, "machine learning": 2, "deep learning": 2,
        "tensorflow": 2, "opencv": 1, "artificial intelligence": 1, "gan": 1,
        "c++": 1,
        # Named engineering work from the projects, all of it distinctive enough
        # to be worth points: multi-document Mongo transactions and
        # compare-and-swap in MediCart, rotating refresh-token families and
        # 2dsphere geospatial search in RentKaro.
        "transactions": 2, "rate limiting": 1, "rate limiter": 1,
        "refresh token": 1, "geospatial": 1,
    },

    # -- Full-stack bonus -----------------------------------------------------
    # A job mentioning BOTH a frontend AND a backend term is a true full-stack
    # role → is_fullstack=True and fullstack_bonus added. Explicit full-stack /
    # MERN wording in the TITLE also flags it as full-stack outright.
    "frontend_terms": [
        "react", "react native", "react.js", "redux", "redux toolkit", "expo",
        "next.js", "vite", "html", "css", "frontend", "front-end", "front end",
        "ui", "ux", "responsive", "design system", "react hooks",
    ],
    "backend_terms": [
        "node", "node.js", "express", "mongodb", "mongoose", "mysql", "redis",
        "socket.io", "rest api", "restful", "firebase", "backend", "back-end",
        "back end", "api", "server",
    ],
    "fullstack_bonus": 6,
    "fullstack_title_terms": ["full stack", "full-stack", "fullstack", "mern", "mean"],

    # -- Down-ranking (penalty) ----------------------------------------------
    # Stacks I don't do + Salesforce/CRM. Salesforce/CRM are penalized HARD so
    # pure-CRM roles sink to the bottom (or drop out via SETTINGS["min_score"]).
    "penalty_terms": {
        ".net": -6, "asp.net": -6, "c#": -6,
        "java": -5, "java spring": -6, "spring boot": -6, "spring mvc": -6,
        "php": -6, "laravel": -5,
        "angular": -4, "angularjs": -4,
        # Salesforce specifically — a different career, hard down-rank.
        #
        # But NOT bare "crm", which carried -6 on the branch this was cut from.
        # That penalty belongs to a resume that is running AWAY from CRM work.
        # Kanav's is built on it: four years of dealer-management, CRM and
        # field-sales platforms at Dealermatix and Tata, and the word appears on
        # all three resumes. Left at -6 it would have down-ranked his single
        # strongest domain match by six points, which is the difference between
        # the top of the page and off it. Scored +2 in skill_weights instead.
        # "crm developer" stays penalised: in a job title that means Salesforce.
        "salesforce": -12, "apex": -12, "lwc": -12,
        "lightning web component": -12, "crm developer": -12,
        # Stacks he does not write. Python and the ML terms were REMOVED from
        # skill_weights rather than moved here: absent from all three resumes,
        # but a Python line in a React JD is normal and not a mismatch worth
        # punishing. Scoring them positive, as the parent branch did, would have
        # ranked jobs against someone else's resume.
        "django": -4, "flask": -3, "ruby": -5, "rails": -5, "golang": -3,
        # NOT "go ": the matcher is a word-boundary one, so a trailing
        # space still fires on "go live" / "go-to-market" in ordinary JD
        # prose. "golang" is the only unambiguous spelling.
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
    # At 4+ years with four engineers mentored, "Senior" and "Lead" are titles
    # he should be APPLYING to, so both lists shrank. What is left is the band
    # that still needs 8-10 years however the JD is worded.
    "hard_drop_terms": [
        "principal", "staff engineer", "director", "head of", "vp", "chief",
        "cto", "engineering manager",
        # TOO JUNIOR, which nothing in the model caught until now. The experience
        # gate reads the years a posting DEMANDS, so it stops "8+ years" and has
        # no opinion at all about a req that wants zero — and the first free
        # sweep duly ranked Notion's "Software Engineer, New Grad (Dec 2026)"
        # third overall, plus two Notion internships, for a man with four years
        # and four mentees. They score well because a new-grad JD lists the same
        # stack; the mismatch is entirely in the band.
        #
        # Here rather than in ATS_TITLE_EXCLUDE on purpose: that list only gates
        # the free sources (see scraper.is_dev_title), while hard_drop_terms runs
        # on paid rows too AND is re-applied to stored rows at merge time.
        #
        # "intern" and "internship" are both listed because the matcher is
        # word-boundary — "intern" does not fire inside "internship" (nor, usefully,
        # inside "internal" or "international").
        "intern", "internship", "trainee", "fresher", "apprentice", "co-op",
        "new grad", "graduate", "junior", "jr",
    ],
    # "architect" and bare "manager" were REMOVED from the hard drops:
    # "Solutions Architect" and "Product Manager" are genuinely out of band, but
    # the word-boundary matcher also killed "Frontend Architect (React)" style
    # postings and anything containing "manager" as a product noun -- e.g. a
    # "Dealer Management" or "Order Manager" platform role, which is literally
    # his current job. max_experience_years now does that work on the stated
    # requirement, which is the honest gate.
    # soft_drop_terms: usually inflated titling, especially in international
    # remote, where "Senior" routinely means 3-4 years. NEVER dropped — only
    # down-ranked, so max_experience_years decides on the stated requirement
    # instead. Measured on a live sweep: hard-dropping these deleted 13 of 28
    # reachable remote roles whose JDs asked for <= 3 years (Twilio, Datadog,
    # Proxify, Lemon.io, A.Team).
    # Only titles that outrun 4 years. "senior", "sr" and "lead" were removed
    # outright -- he has led a team of 4, run architecture reviews and owned
    # releases, so a Senior/Lead posting is the target and not a stretch.
    # Penalising them sank the most appropriate half of the market.
    "soft_drop_terms": ["architect", "manager", "principal"],

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
    "drop_excluded": True,       # True: filter out title-seniority + over-experienced roles
                                 # False: keep them but apply drop_penalty (they sink)
    # 4+ years shipped (Jun 2022 -> present), leading a team of 4. The gate is
    # `floor > max`, so 6 ADMITS a posting asking for exactly 6 and drops 7+.
    # That is deliberate: a 6-year ask is reachable for a 4-year lead, a 8-year
    # one is not.
    "max_experience_years": 6,
    # How to combine several "N years" figures in one posting: "min" reads the
    # smallest as the real ask (right for short JDs, where anything larger is a
    # nice-to-have), "max" the largest (right for the long structured kind that
    # state a total AND a per-skill figure). See
    # scraper._required_experience_floor.
    "experience_aggregate": "min",
    "min_score": None,           # drop jobs scoring below this after ranking (None = keep all, just sorted)
    # 30, not the parent branch's 14. Freshness is still the biggest lever on
    # getting a reply, but this is a FIRST sweep — seen.tsv holds nothing of his,
    # so a 14-day window discarded 300 rows he has never had the chance to look
    # at. Tighten to 14 once the backfill is done and runs are incremental.
    "max_age_days": 30,
    "drop_undated": False,       # if True, also drop jobs whose posted date can't be parsed (default: keep them)
    # Minimum compensation, annualized and in USD, so an Indian LPA figure and a
    # US/EU salary are compared on the same axis (see scraper.comp_max_usd).
    # 6000 USD ~= the old 5.2 LPA floor. Undisclosed, unparseable, or
    # unknown-currency pay is always KEPT — we never drop on a guess.
    # Raise this to ~40000+ once the sweep is weighted toward international remote.
    # ~7.5 LPA. A 4-year React Native lead in NCR/Chandigarh sits well above
    # this, so the floor exists only to drop fresher-band listings; undisclosed
    # pay is always KEPT, so it cannot silently delete a real role.
    "min_comp_usd": 9000,        # None to disable

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
    # EMPTY for Kanav, where the parent branch had ["worldwide", "remote"].
    # That list is right for someone hunting international remote and wrong for
    # him: he lives in New Delhi, works in Chandigarh, and an onsite Gurgaon or
    # Mohali row classifies as "onsite" or "" — so the filter deleted exactly the
    # inventory he most wants. Measured on the first free sweep: 1093 rows pulled,
    # 337 thrown away as "not worldwide/remote", and every one of the top ten was
    # an international remote listing.
    #
    # Nothing is lost by keeping them. linkedin_shortlist.py --sections splits the
    # rendered page into "Onsite & hybrid in India" / "Fully remote" / "Onsite
    # abroad", so the distinction is visible on the page instead of being decided
    # here by deletion.
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
    # output/kanav, not output/. The root is shared on disk across branches (it
    # is gitignored, so a checkout does not swap it), and the first free run
    # landed his rows next to a different person's jobs_2026-08-21 files. Anything
    # that later globs the root — merge_jobs --all, harvest_ats.companies_from_output
    # — would then be mixing two résumés' results.
    "output_dir": "output/kanav",
    "top_n_console": 40,         # how many top jobs to print to the console
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
