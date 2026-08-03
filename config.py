"""
Configuration for the job scraper — tuned for a Salesforce Business Analyst
(functional consultant, ~1.5 yrs, Mohali/Punjab; Salesforce Certified Admin).

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
    3. SCORING  — resume-based relevance weights, two-halves bonus, exclude/down-rank
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
# scoring layer), but the Salesforce-qualified titles are listed first by intent.
#
# 7 keywords x 10 locations = 70 Indeed combos (~$6). Start with
# `--limit` (first N combos) before running the whole matrix.
SEARCH = {
    "country": "IN",            # Indeed country code (IN, US, GB, ...)
    "experience_years": 2,      # 1.5 yrs on the resume; rounds to the site filters' nearest band
    "salary_min": None,         # optional minimum salary; None to skip (SETTINGS["min_comp_usd"] filters after the fact)
    "max_results": 15,          # jobs PER (keyword x location) search — keep modest, pay-per-event (~$5/1000 results)

    # Each entry is run as its OWN search term. Platform-qualified titles first;
    # the bare "Business Analyst" is deliberate — plenty of real Salesforce BA
    # roles are titled generically, and the scoring layer demotes the rest.
    "role_keywords": [
        "Salesforce Business Analyst",
        "Salesforce Functional Consultant",
        "Salesforce Consultant",
        "Salesforce Administrator",
        "Sales Cloud Consultant",
        "CRM Business Analyst",
        "Business Analyst",
    ],

    # Home (Chandigarh tri-city) + the Salesforce partner/SI markets + Remote.
    # NOTE: Mohali/Chandigarh have no verified LINKEDIN_GEO_IDS entry and no
    # NAUKRI_CITY_IDS entry, so they run on Indeed only — LinkedIn and Naukri use
    # their own location overrides in SITES.
    "locations": [
        "Mohali", "Chandigarh",
        "Delhi", "Gurgaon", "Noida",
        "Bengaluru", "Hyderabad", "Pune", "Mumbai",
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
    # City coverage, not just "India": measured on the 2026-08-01 sweep, LinkedIn
    # cost $0.19 for 4 rows at score >= 20 while Indeed cost $6.06 for 16 — but
    # Indeed only won on volume because it ran 70 city combos to LinkedIn's 2
    # locations. Per good row LinkedIn was 8x cheaper, so give it the cities and
    # let Indeed be the one you skip. Only VERIFIED geoIds are listed; Mohali and
    # Chandigarh have none, so they stay Indeed-only (the adapter refuses to
    # search without a geoId rather than silently billing for US results).
    # 8 locations x 7 keywords x 15 results ~= $0.84.
    "linkedin": {"enabled": True,  "actor": "curious_coder/linkedin-jobs-scraper",
                 "locations": ["India", "Delhi", "Gurgaon", "Bengaluru",
                               "Hyderabad", "Pune", "Mumbai", "Remote"],
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
    "india", "delhi", "ncr", "gurgaon", "gurugram", "noida", "bengaluru",
    "bangalore", "hyderabad", "pune", "mumbai", "chennai", "kolkata",
    "ahmedabad",
]

# Free sources return a whole board (finance, ops, HR, ...), so unlike job boards
# we can't keyword-search. Keep only jobs whose TITLE looks like a BA / Salesforce
# functional role (case-insensitive substring). Scoring then ranks within these.
# Was a dev-title list; on a BA search that silently filtered out every free row,
# so the ATS boards and feeds contributed nothing at all. Volume is still low —
# the configured boards are dev-heavy product startups.
ATS_TITLE_HINTS = [
    "business analyst", "business analysis", "business systems analyst",
    "functional consultant", "functional analyst", "requirements analyst",
    "salesforce", "crm", "sales cloud", "service cloud", "sales operations",
    "implementation consultant", "solution consultant", "product owner",
]

# Titles to reject even when they DO match a hint above. Checked first, so it
# wins — which is the only way to keep out a role that borrows a software title
# for a different job ("Senior Software Engineer - Data Engineer, Spark, ETL").
# Earns its keep now that the hints include the bare word "salesforce", which on
# a product-company board is mostly Salesforce DEVELOPER work. Seniority does NOT
# belong here — SCORING["hard_drop_terms"] already handles it, and as a penalty
# rather than a silent delete.
ATS_TITLE_EXCLUDE = ["developer", "engineer", "architect", "sdet"]

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
# penalty terms subtract; a bonus rewards jobs naming BOTH halves of the field
# (Salesforce platform + business analysis) — see "two halves" below.
SCORING = {
    # -- Positive skill weights ----------------------------------------------
    # Weighted by DISCRIMINATIVE POWER, not by how central the skill is to her.
    # The test for every term is "would this word appear in a job she does NOT
    # want?" — if yes it stays low however core it is. Requirements gathering,
    # BRD/FRD, UAT and gap analysis ARE her top skills, but they appear in every
    # BA posting on earth; only the platform terms identify her niche. Weighted
    # the other way round (craft 3-4, platform 5) on live data an "Oracle Fusion
    # Functional Consultant" ranked #1 and a generic "Business Analyst" beat the
    # real "Salesforce Business Analyst".
    "skill_weights": {
        # The niche identifier — deliberately dominant
        "salesforce": 10,
        # Clouds she has actually delivered on (L'Oreal, Diamond Beverages)
        "sales cloud": 8, "service cloud": 8, "experience cloud": 7,
        # Salesforce-only artefacts — almost no other product's JD says these
        "soql": 6, "lightning app builder": 6, "salesforce inspector": 6,
        "apex": 5, "sfa": 5, "sales force automation": 5,
        "salesforce administrator": 5,
        "lwc": 4, "lightning web component": 4, "flow builder": 4,
        "salesforce certified": 4,
        # Both forms deliberately: the matcher is boundary-anchored, so
        # "validation rule" does NOT match "validation rules" — and the plural is
        # what JDs actually write. Same reason the old config listed
        # websocket/websockets. A JD using both just scores the term twice.
        "validation rule": 4, "validation rules": 4,
        "permission set": 4, "permission sets": 4,
        "approval process": 4, "approval processes": 4,
        "record type": 3, "record types": 3,
        "custom metadata": 3, "dealer management": 3,
        # BA craft — genuinely hers, but shared with every adjacent BA role, so
        # supporting weight only
        "business analyst": 2, "requirements gathering": 2,
        "requirement gathering": 2, "gap analysis": 2, "elicitation": 2,
        "brd": 2, "frd": 2, "functional specification": 2, "uat": 2,
        "change request": 2, "change requests": 2,
        # Generic delivery vocabulary + the analytics stack, which leaks straight
        # into data-analyst postings she isn't looking for
        "user stories": 1, "acceptance criteria": 1, "user acceptance testing": 1,
        "stakeholder management": 1, "impact analysis": 1, "process mapping": 1,
        "agile": 1, "scrum": 1, "sprint planning": 1, "backlog": 1, "sdlc": 1,
        "power bi": 1, "sql": 1, "rest api": 1, "dms": 1,
    },

    # -- "Two halves" bonus ---------------------------------------------------
    # Same machinery as the old full-stack bonus (scraper.py precompiles these
    # key names), repurposed to the two halves of HER field: the PLATFORM and the
    # BUSINESS-ANALYSIS craft. A job naming both is a real Salesforce functional
    # role; platform alone is a Salesforce developer post, craft alone is a
    # generic BA post. Bare "crm" is deliberately absent from the platform half —
    # it would hand the bonus to every SAP/Dynamics/Zoho job for free.
    "frontend_terms": [        # half A — the platform
        "salesforce", "sales cloud", "service cloud", "experience cloud",
        "apex", "lwc", "soql", "lightning app builder", "salesforce inspector",
        "sfa", "sales force automation",
    ],
    "backend_terms": [         # half B — the BA craft
        "business analyst", "business analysis", "functional consultant",
        "requirements gathering", "requirement gathering", "gap analysis",
        "brd", "frd", "functional specification", "uat",
        "user acceptance testing", "change request",
    ],
    # 10, not 6, measured on the 2026-08-01 LinkedIn sweep: matching BOTH halves
    # is the single cleanest "functional role, not a dev role" signal in the data
    # — every Salesforce Developer row matched half A only (salesforce/apex/lwc/
    # soql, no requirements/UAT/BRD anywhere), so widening the bonus separates
    # them without having to guess at ever-deeper title penalties.
    "fullstack_bonus": 10,
    # Flags a match from the TITLE ALONE, bypassing all other evidence, so every
    # term here MUST name the platform. A bare "business analyst" handed the
    # bonus to "Business Analyst (Italian)", and a bare "functional consultant"
    # to "Oracle Fusion Functional Consultant". Genuine matches still earn it
    # through the two-halves rule above, so nothing real is lost.
    "fullstack_title_terms": [
        "salesforce business analyst", "salesforce functional consultant",
        "salesforce consultant", "salesforce administrator",
        "salesforce analyst", "crm business analyst",
    ],

    # -- Down-ranking (penalty) ----------------------------------------------
    # She is FUNCTIONAL, not a developer: she reads Apex and hands it off. So the
    # coding roles sink. Scoped to whole phrases wherever possible, because a
    # Salesforce BA posting routinely mentions working WITH Apex developers and
    # shouldn't be punished for it.
    "penalty_terms": {
        # -16, raised from -12: on the first live sweep three Salesforce
        # Developer posts still landed in the top 10 (22/12/12), because the
        # platform terms they share with a functional role are worth ~40 on their
        # own. Deep enough to sink them, not so deep they vanish — she may still
        # want to see them.
        "salesforce developer": -16, "apex developer": -16, "lwc developer": -16,
        "crm developer": -16,
        # Hands-on-code signals a functional JD never carries, so a Salesforce
        # DEV post sinks below a functional one without punishing a BA posting
        # that merely mentions Apex once.
        "apex trigger": -5, "apex triggers": -5, "batch apex": -5,
        "visualforce": -5, "trigger framework": -5,
        "apex class": -4, "apex classes": -4,
        "java": -5, ".net": -5, "asp.net": -5, "c#": -5, "php": -5,
        "react": -5, "angular": -5, "node.js": -5, "python developer": -5,
        "full stack": -5, "full-stack": -5,
        "frontend": -4, "backend": -4, "software engineer": -4, "sde": -4,
        "devops": -4, "test automation": -3, "qa engineer": -3,
        # NOT penalized (yet): SAP / Oracle Fusion / Dynamics / ServiceNow. The
        # platform weights above should already outrank them — check the real
        # top 10 after the first sweep and add them here if they leak in.
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
    # hard_drop_terms: never a fit at 1.5 yrs whatever the JD says — including
    # "architect", which in Salesforce-land is a 8-10 yr Technical/Solution
    # Architect track.
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
    "drop_excluded": True,       # True: filter out title-seniority + over-experienced roles
                                 # False: keep them but apply drop_penalty (they sink)
    "max_experience_years": 3,   # roles whose text demands MORE than this (e.g. "5+ years") are dropped/penalized
    # How to combine several "N years" figures in one posting: "min" reads the
    # smallest as the real ask (right for short JDs, where anything larger is a
    # nice-to-have), "max" the largest (right for the long structured kind that
    # state a total AND a per-skill figure). See
    # scraper._required_experience_floor.
    "experience_aggregate": "min",
    "min_score": None,           # drop jobs scoring below this after ranking (None = keep all, just sorted)
    "max_age_days": 14,          # drop jobs posted longer ago than this (older ones are likely closed). None to disable.
    "drop_undated": False,       # if True, also drop jobs whose posted date can't be parsed (default: keep them)
    # Minimum compensation, annualized and in USD, so an Indian LPA figure and a
    # US/EU salary are compared on the same axis (see scraper.comp_max_usd).
    # 9500 USD ~= the 8 LPA floor asked for. Undisclosed, unparseable, or
    # unknown-currency pay is always KEPT — we never drop on a guess, and Indian
    # BA postings disclose pay less often than they hide it.
    "min_comp_usd": 9500,        # None to disable

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
    # OFF ([] = no filtering), and it has to be: this search is mostly ONSITE
    # (Mohali, NCR, the metro SI hubs). A scope list keeps ONLY the scopes named,
    # so ["worldwide", "remote"] silently deleted every office role in the sweep —
    # i.e. nearly everything paid for. Switch it back on only if the target
    # becomes remote-first.
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
    # Credits are tight (2026-08-01: $2.09 left on APIFY_TOKEN, $5.00 on
    # APIFY_TOKEN_2, and Naukri alone would cost $7). This counts spend WITHIN
    # one run, measured against real account billing — so it caps each token's
    # share of a sweep that resumes across both via output/.done_combos.
    "max_spend_usd": 4.50,          # None = no cap. 1.60 for the APIFY_TOKEN leg, 4.50 for APIFY_TOKEN_2
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

OVERLAYABLE = ("SEARCH", "SITES", "SCORING", "SETTINGS", "ATS_BOARDS", "FEEDS")


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
    """
    target = globals() if target is None else target
    changed = []
    for name in OVERLAYABLE:
        if not hasattr(module, name):
            continue
        override = getattr(module, name)
        if override:
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
    print(f"demo ok (active profile: {PROFILE or 'default'})")


if __name__ == "__main__":
    demo()
