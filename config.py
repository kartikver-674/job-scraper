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
"""

# ===========================================================================
# 1. SEARCH — shared criteria + the role x location matrix
# ===========================================================================
# The scraper runs the CROSS PRODUCT of role_keywords x locations for every
# enabled site. Ordering matters only cosmetically (results are re-ranked by the
# scoring layer), but the exact-title Salesforce terms are listed first by intent.
SEARCH = {
    "country": "IN",            # Indeed country code (IN, US, GB, ...)
    "experience_years": 2,      # ~1.5 yrs actual (Salesforce FC since 01/2025); ask sites for 1
                                # so 1-yr postings aren't filtered out. Upper bound is enforced
                                # by SETTINGS["max_experience_years"].
    "salary_min": None,         # DELIBERATELY None: site-side salary filters silently drop the
                                # majority of India listings that don't disclose pay. The CTC
                                # floor is enforced after the fact via SETTINGS["min_ctc_lpa"].
    "max_results": 15,          # jobs PER (keyword x location) search — keep modest, pay-per-event (~$5/1000 results)

    # Each entry is run as its OWN search term. Salesforce functional/BA titles —
    # config + requirements work, NOT Apex/LWC development (see penalty_terms).
    "role_keywords": [
        "Salesforce Functional Consultant",
        "Salesforce Consultant",
        "Salesforce Business Analyst",
        "Salesforce Administrator",
        "Salesforce Sales Cloud Consultant",
        "Salesforce Service Cloud Consultant",
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
# Company career sites via ATS APIs (free — no Apify, no per-result cost)
# ---------------------------------------------------------------------------
# Most companies host jobs on an ATS with a free public JSON API. Add companies
# as {board_token: "Display Name"}. Find the token from the careers URL, e.g.
#   boards.greenhouse.io/<token>   ->  GREENHOUSE_COMPANIES
#   jobs.lever.co/<token>          ->  LEVER_COMPANIES
# Only tokens that actually resolve are kept (verified by probing). ATS results
# are filtered to India-relevant locations via INDIA_LOCATION_HINTS below.
# Verified 2026-07-21 (tokens resolve + have India jobs). Add more freely.
GREENHOUSE_COMPANIES = {
    "phonepe": "PhonePe",
    "groww": "Groww",
    "postman": "Postman",
    "druva": "Druva",
    "slice": "Slice",
}
LEVER_COMPANIES = {
    "paytm": "Paytm",
    "meesho": "Meesho",
    "mindtickle": "Mindtickle",
    "hevodata": "Hevo Data",
    "zeta": "Zeta",
    "fampay": "FamPay",
    "cred": "CRED",
}

# Keep an ATS job if its location mentions any of these (empty location is kept).
INDIA_LOCATION_HINTS = [
    "india", "delhi", "ncr", "gurgaon", "gurugram", "noida", "bengaluru",
    "bangalore", "hyderabad", "pune", "mumbai", "chennai", "kolkata",
    "ahmedabad", "remote",
]

# ATS APIs return a company's ENTIRE job list (finance, ops, HR, ...), so unlike
# job boards we can't keyword-search. Keep only jobs whose TITLE looks like a
# software/dev role (case-insensitive substring). Scoring then ranks within these.
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
        "salesforce administrator": 8,
        "flow builder": 6, "salesforce flow": 6, "soql": 5,
        # Platform config skills (Salesforce-specific vocabulary)
        "lightning experience": 4, "experience builder": 4,
        "lightning app builder": 4, "validation rules": 4,
        "approval process": 4, "custom objects": 4, "permission sets": 4,
        "record types": 3, "page layouts": 3,
        # Her job title + platform category — generic enough to need modest weight
        "functional consultant": 4, "crm": 3,
        # Functional-delivery artefacts she authored. Higher than the rest of the
        # generic vocabulary because they signal functional (not dev) work.
        "brd": 3, "frd": 3, "fsd": 2,
        # Generic BA / testing craft — resume-stated, but non-discriminative, so
        # supporting weight only. Do NOT raise these: it's what let Oracle and
        # plain-BA roles outrank real Salesforce ones.
        "business analyst": 2, "requirement gathering": 2,
        "gap analysis": 2, "uat": 2, "user acceptance testing": 2,
        "user stories": 2, "business requirement": 2,
        "functional requirement": 2, "business process mapping": 2,
        "system integration testing": 2, "stakeholder management": 2,
        "dashboards": 2, "user management": 2, "reports": 1,
        "process flow": 1, "integration testing": 1, "regression testing": 1,
        "test case": 1, "test cases": 1, "client communication": 1,
        "agile": 1, "sdlc": 1, "go-live": 1, "change request": 1,
        "production support": 1,
        # Domain experience — DMS/SFA for automotive, FMCG, manufacturing clients
        "dms": 2, "dealer management": 2, "sfa": 2,
        "automotive": 1, "fmcg": 1, "manufacturing": 1,
        "excel": 1, "user manual": 1,
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
        "salesforce", "sales cloud", "service cloud", "flow builder",
        "lightning", "validation rules", "approval process", "custom objects",
        "permission sets", "page layouts", "record types", "soql",
    ],
    "backend_terms": [
        "requirement gathering", "business requirement", "functional requirement",
        "brd", "frd", "user stories", "gap analysis", "business analyst",
        "uat", "user acceptance testing", "stakeholder management",
        "business process", "process flow", "change request", "go-live",
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
        "salesforce administrator", "crm consultant",
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
    "max_experience_years": 3,   # ~1.5 yrs experience → roles demanding MORE than 3 yrs
                                 # (e.g. "5+ years") are dropped/penalized
    "min_score": None,           # drop jobs scoring below this after ranking (None = keep all, just sorted)
    "max_age_days": 14,          # drop jobs posted longer ago than this (older ones are likely closed). None to disable.
    "drop_undated": False,       # if True, also drop jobs whose posted date can't be parsed (default: keep them)
    "min_ctc_lpa": 8.0,          # if a job's MAX salary is DISCLOSED and below this (in LPA), drop it.
                                 # Undisclosed/unparseable salary is always kept. None to disable.

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
