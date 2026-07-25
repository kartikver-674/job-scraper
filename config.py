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
"""

# ===========================================================================
# 1. SEARCH — shared criteria + the role x location matrix
# ===========================================================================
# The scraper runs the CROSS PRODUCT of role_keywords x locations for every
# enabled site. Ordering matters only cosmetically (results are re-ranked by the
# scoring layer), but full-stack terms are listed first by intent.
SEARCH = {
    "country": "IN",            # Indeed country code (IN, US, GB, ...)
    "experience_years": 2,      # target ~2 yrs (0-3 acceptable); passed to sites that support it
    "salary_min": None,         # optional minimum salary; None to skip
    "max_results": 15,          # jobs PER (keyword x location) search — keep modest, pay-per-event (~$5/1000 results)

    # Each entry is run as its OWN search term. Weighted toward full-stack.
    "role_keywords": [
        "Full Stack Developer",
        "Full Stack Engineer",
        "MERN Stack Developer",
        "React Native Developer",
        "React Native Engineer",
        "React Developer",
        "Node.js Developer",
        "Frontend Developer",
        "Software Engineer JavaScript",
    ],

    # India-focused (Delhi/NCR heavy) + Remote.
    "locations": [
        "Delhi", "New Delhi", "Gurgaon", "Noida",
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
    "linkedin": {"enabled": True,  "actor": "curious_coder/linkedin-jobs-scraper",
                 "locations": ["India", "Remote"]},
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

# LinkedIn job search filters by numeric geoId, not city name (a bare location name
# is ignored → US results). "Remote" is special-cased in the adapter (f_WT=2).
# NOTE: the actor requires count >= 10 per run.
# VERIFIED (tested, return correct-location jobs): India, Delhi.
# UNVERIFIED (best-effort — confirm by opening a LinkedIn jobs search in the browser
# and copying geoId from the URL before relying on them):
LINKEDIN_GEO_IDS = {
    "India": "102713980",      # verified
    "Delhi": "106187582",      # verified
    "New Delhi": "106164932",  # unverified
    "Gurgaon": "115884833", "Gurugram": "115884833",  # unverified
    "Noida": "105598789",      # unverified
    "Bengaluru": "105214831",  # unverified
    "Hyderabad": "105556991",  # unverified
    "Pune": "114806696",       # unverified
    "Mumbai": "106164952",     # unverified
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
    "greenhouse": {
        "phonepe": "PhonePe", "groww": "Groww", "postman": "Postman",
        "druva": "Druva", "slice": "Slice",
    },
    "lever": {
        "paytm": "Paytm", "meesho": "Meesho", "mindtickle": "Mindtickle",
        "hevodata": "Hevo Data", "zeta": "Zeta", "fampay": "FamPay",
        "cred": "CRED",
    },
    # International / remote-friendly boards — the point of the free tier.
    "ashby": {
        "linear": "Linear", "ramp": "Ramp", "openai": "OpenAI",
    },
    "smartrecruiters": {},   # e.g. {"BoschGroup": "Bosch"}
}

# Public remote-job feeds. One request each, no auth. Adapters live in
# sources/feeds.py (registered in sources.FEED_FETCHERS).
FEEDS = {
    "remoteok": {"enabled": True},
    "wwr": {"enabled": True, "categories": [
        "remote-programming-jobs",
        "remote-front-end-programming-jobs",
        "remote-back-end-programming-jobs",
        "remote-full-stack-programming-jobs",
    ]},
}

# Keep a free-source job only if its location mentions one of these.
# EMPTY = allow every location, which is the right default now that the target
# is international remote — remote/visa/comp filters do the narrowing instead of
# a country whitelist. An empty job location is always kept.
# To go back to India-only, put the old list back:
#   ["india", "delhi", "ncr", "gurgaon", "gurugram", "noida", "bengaluru",
#    "bangalore", "hyderabad", "pune", "mumbai", "chennai", "kolkata", "remote"]
LOCATION_HINTS = []

# Free sources return a whole board (finance, ops, HR, ...), so unlike job boards
# we can't keyword-search. Keep only jobs whose TITLE looks like a software/dev
# role (case-insensitive substring). Scoring then ranks within these.
ATS_TITLE_HINTS = [
    "developer", "full stack", "fullstack", "full-stack", "frontend", "front end",
    "front-end", "backend", "back end", "back-end", "software engineer",
    "software development", "sde", "react", "node", "javascript", "typescript",
    "web developer", "mern", "mobile developer", "application developer",
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
        # Core full-stack stack — highest signal
        "node": 5, "node.js": 5, "express": 5,
        "react": 5, "react native": 5, "react.js": 5,
        "typescript": 5, "mongodb": 5,
        # Strong supporting skills
        "redis": 3, "socket.io": 3, "websocket": 3, "websockets": 3,
        "jwt": 3, "oauth": 3, "rest api": 3, "restful": 3, "mongoose": 3,
        "mysql": 3, "javascript": 3,
        # Real-time / auth / concurrency — stated resume strengths
        "firebase": 2, "fcm": 2, "concurrency": 2, "authentication": 2,
        # General relevant tooling / practices (from resume)
        "redux": 2, "expo": 2, "tailwind": 2, "next.js": 2, "jest": 2,
        "azure devops": 2, "ci/cd": 2,
        "zod": 1, "react hook form": 1, "html": 1, "css": 1, "es6": 1, "agile": 1,
    },

    # -- Full-stack bonus -----------------------------------------------------
    # A job mentioning BOTH a frontend AND a backend term is a true full-stack
    # role → is_fullstack=True and fullstack_bonus added. Explicit full-stack /
    # MERN wording in the TITLE also flags it as full-stack outright.
    "frontend_terms": [
        "react", "react native", "react.js", "redux", "expo", "tailwind",
        "next.js", "zod", "react hook form", "frontend", "front-end", "front end", "ui",
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
        # Salesforce / CRM — hard down-rank
        "salesforce": -12, "apex": -12, "lwc": -12,
        "lightning web component": -12, "crm developer": -12, "crm": -6,
    },

    # -- Hard filters (wrong seniority / too much experience) -----------------
    # If any drop_term appears in the TITLE, the job is removed entirely (unless
    # SETTINGS["drop_excluded"] is False, in which case drop_penalty is applied
    # instead). Over-experience is detected separately from the text (see
    # SETTINGS["max_experience_years"]).
    "drop_terms": [
        "senior", "sr", "lead", "principal", "staff",
        "manager", "architect",
    ],
    "drop_penalty": -15,   # used only when drop_excluded is False
}


# ===========================================================================
# 4. SETTINGS — filtering thresholds, cost guards, output knobs
# ===========================================================================
SETTINGS = {
    # Filtering
    "drop_excluded": True,       # True: filter out title-seniority + over-experienced roles
                                 # False: keep them but apply drop_penalty (they sink)
    "max_experience_years": 3,   # roles whose text demands MORE than this (e.g. "5+ years") are dropped/penalized
    "min_score": None,           # drop jobs scoring below this after ranking (None = keep all, just sorted)
    "max_age_days": 14,          # drop jobs posted longer ago than this (older ones are likely closed). None to disable.
    "drop_undated": False,       # if True, also drop jobs whose posted date can't be parsed (default: keep them)
    # Minimum compensation, annualized and in USD, so an Indian LPA figure and a
    # US/EU salary are compared on the same axis (see scraper.comp_max_usd).
    # 6000 USD ~= the old 5.2 LPA floor. Undisclosed, unparseable, or
    # unknown-currency pay is always KEPT — we never drop on a guess.
    # Raise this to ~40000+ once the sweep is weighted toward international remote.
    "min_comp_usd": 6000,        # None to disable

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
