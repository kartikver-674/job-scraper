"""
Configuration for the job scraper — tuned for a Software Engineer (~1.5 yrs,
Chandigarh/Mohali) LEAVING Salesforce for Java / backend / frontend / Flutter /
AI work.

The one thing to understand before editing anything below: his RÉSUMÉ is a
Salesforce résumé (Apex, LWC, Batch Apex, SOQL — the whole current job) but his
TARGET is everything except that. So Salesforce terms are the biggest PENALTY
block in SCORING, not the biggest weight. Anyone who "fixes" that by reading the
résumé and re-adding `"salesforce": 10` will hand him his own current job as the
#1 result. That is the intent, not a bug.

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
# scoring layer), but the five target tracks are grouped by intent.
#
# COST WARNING, because this matrix is bigger than the old one: --dry-run reports
# 180 actor runs — 90 Indeed combos (9 keywords x 10 locations, ~$7), 72 LinkedIn
# (~$1), and 18 Naukri, which at Naukri's ~$0.50 per-run FLOOR is ~$9 on its own
# for the least useful third of the sweep. Never run the whole thing first:
#     python scraper.py --dry-run                  # the plan, zero cost
#     python scraper.py --site indeed --limit 10   # ~$0.8, enough to tune weights
# and once you have paid for one sweep, `python rescore_from_apify.py` re-ranks it
# for free — never re-scrape to try new weights.
SEARCH = {
    "country": "IN",            # Indeed country code (IN, US, GB, ...)
    "experience_years": 2,      # 1.5+ yrs on the résumé; rounds to the site filters' nearest band
    "salary_min": None,         # optional minimum salary; None to skip (SETTINGS["min_comp_usd"] filters after the fact)
    "max_results": 15,          # jobs PER (keyword x location) search — keep modest, pay-per-event (~$5/1000 results)

    # Each entry is run as its OWN search term, one per target track. Kept to 9
    # deliberately: five tracks x every synonym would triple the bill for
    # overlapping results, and the scoring layer re-ranks whatever comes back.
    "role_keywords": [
        "Java Developer",              # track 1 — Java / enterprise backend
        "Backend Developer",
        "Node.js Developer",           # track 2 — JS backend (résumé: Node.js, REST)
        "Flutter Developer",           # track 3 — his deepest hands-on skill
        "React Developer",             # track 4 — frontend
        "Full Stack Developer",
        "Software Engineer",           # broadest net; the scoring layer does the narrowing
        "Machine Learning Engineer",   # track 5 — AI/CV (see the weights caveat in SCORING)
        "AI Engineer",
    ],

    # Home tri-city first, then the metros he named, then Remote.
    # NOTE: Mohali and Chandigarh have NO verified LINKEDIN_GEO_IDS entry and no
    # NAUKRI_CITY_IDS entry, so they are INDEED-ONLY — LinkedIn and Naukri use
    # their own location overrides in SITES. Panchkula is deliberately absent:
    # Indeed's city search is radius-based, so "Mohali" + "Chandigarh" already
    # cover it, and a third combo would be paid for near-zero extra inventory.
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
    # City coverage, not just "India": LinkedIn is ~5x cheaper per result than
    # Indeed, so it is the one to widen and Indeed is the one to --limit. Only
    # VERIFIED geoIds are listed — Mohali and Chandigarh have none, so they stay
    # Indeed-only (the adapter refuses to search without a geoId rather than
    # silently billing for US results). 9 keywords x 8 locations x 15 ~= $1.08.
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
# we can't keyword-search. Keep only jobs whose TITLE looks like a software/dev
# role (case-insensitive substring). Scoring then ranks within these.
# The dev-title list already fits him (he wants engineering roles, unlike the
# BA/functional retunes on other branches) — widened with the mobile and AI
# tracks his résumé supports.
ATS_TITLE_HINTS = [
    "developer", "full stack", "fullstack", "full-stack", "frontend", "front end",
    "front-end", "backend", "back end", "back-end", "software engineer",
    "software development", "sde", "react", "node", "javascript", "typescript",
    "web developer", "mern", "mobile developer", "application developer",
    # Mobile track (Flutter/Dart + Android Studio on the résumé)
    "flutter", "dart", "android", "mobile engineer", "cross-platform",
    # Java / enterprise backend
    "java",
    # AI / CV track — one academic project, so kept to the exact wording he can
    # defend rather than the whole ML job market
    "machine learning", "computer vision", "ml engineer", "ai engineer",
]

# Titles to reject even when they DO match a hint above. Checked first, so it
# wins — which is the only way to keep out a role that borrows a software title
# for a different job ("Senior Software Engineer - Data Engineer, Spark, ETL").
# Earns its keep here for one reason: he is leaving Salesforce, and "Salesforce
# Developer" matches the hint "developer" above. A -15 penalty sinks those rows
# but they still occupy the list; on the FREE boards there is no reason to carry
# them at all. Seniority does NOT belong here — SCORING["hard_drop_terms"]
# already handles it, and as a penalty rather than a silent delete.
ATS_TITLE_EXCLUDE = ["salesforce", "apex", "crm"]

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
    # -- Positive skill weights ----------------------------------------------
    # ONLY terms the résumé actually names, weighted by DISCRIMINATIVE POWER
    # rather than by how central the skill is to him. The test for every term is
    # "would this word appear in a job he does NOT want?" — if yes it stays low
    # however core it is. That is why "sql" and "docker" sit at 1 while "dart"
    # sits at 6: half the postings on earth say SQL, almost none say Dart unless
    # they mean Flutter.
    #
    # KNOWN THIN SPOTS, stated rather than papered over: the résumé lists "Java"
    # and "Machine Learning / Computer Vision" as skills but names no framework
    # for either — no Spring/Hibernate/JPA, no PyTorch/TensorFlow/OpenCV. Those
    # words are therefore NOT here, because inventing them would claim skills he
    # can't defend in an interview. Consequence: a Spring Boot JD scores from
    # "java" alone and ranks below an equally good Flutter JD. If he does know
    # Spring or PyTorch, adding those two lines is the single highest-value edit
    # to this file.
    "skill_weights": {
        # -- Track identifiers: near-zero false positives, so they decide rank --
        "flutter": 8, "dart": 6,
        # 8, not 7: a JD saying "Java" is unambiguous about what it wants, it is
        # the track he named first, and with no Spring/Hibernate terms to stack on
        # (see above) this one word carries the whole track's score.
        "java": 8,                 # \bjava\b does NOT match "javascript" — safe to weight high
        "react": 7,
        "computer vision": 6, "instance segmentation": 6,
        # -- Strong, résumé-backed stack terms ---------------------------------
        # node + node.js both fire on "Node.js" (10 total) by design, the same way
        # the plural forms are listed separately: a JD that says it twice means it.
        "node.js": 5, "node": 5,
        "machine learning": 5, "firebase": 4, "android": 4,
        ".net": 4, "c#": 4, "worker service": 3,   # .NET Worker Services, Dealermatix
        # 2, not 4: annotation is the genuine part of his CV project but it is
        # also what every data-labeling BPO posting advertises — it fails the
        # "would this appear in a job he does NOT want?" test badly. At 4 the two
        # of them stacked to +8 and put the AI track above Flutter on the check.
        "image annotation": 2, "data annotation": 2,
        # -- Genuinely his, but shared with plenty of adjacent roles ------------
        "javascript": 3, "python": 3, "postgresql": 3, "mysql": 3,
        "rest api": 2, "restful": 2, "rest integration": 2,
        "cross-platform": 2, "asynchronous": 2, "batch processing": 2,
        "reusable components": 2, "erp": 2,
        # -- Everyday software vocabulary: in almost every JD, so ~no signal ----
        "sql": 1, "docker": 1, "postman": 1, "figma": 1, "responsive": 1,
    },

    # -- Frontend + backend bonus ---------------------------------------------
    # He asked for BOTH frontend and backend roles, so the stock full-stack bonus
    # is still the right notion for him and is kept as-is in shape — but at 5,
    # not 6, and deliberately NOT higher. It is a tiebreaker, not a track
    # decider: cranking it up would rank every web full-stack post above the
    # Flutter and Java roles that are the actual targets.
    #
    # Bare "ui", "api" and "server" are removed from the halves on purpose —
    # "ui" handed the frontend half to UI/UX designer posts and "api"/"server"
    # handed the backend half to literally any web job, so both halves matched
    # for free and the bonus stopped meaning anything.
    #
    # flutter/dart/responsive are NOT in the frontend half, for the same reason
    # and measured on the fake-job check: every Flutter JD says "responsive UI"
    # and mentions a REST API, which satisfied BOTH halves, so the whole mobile
    # track collected +5 for free and out-ranked Java 32-21. A mobile role is not
    # a web full-stack role; it wins here on the flutter/dart weights themselves,
    # which is honest. ("responsive" keeps its skill weight of 1 — it just no
    # longer certifies half a job.)
    "frontend_terms": [
        "react", "react.js", "javascript",
        "frontend", "front-end", "front end",
    ],
    "backend_terms": [
        "node", "node.js", "java", ".net", "c#", "rest api", "restful",
        "postgresql", "mysql", "backend", "back-end", "back end",
    ],
    "fullstack_bonus": 5,
    # Flags a match from the TITLE ALONE, bypassing all other evidence, so every
    # term here must name the domain and never a bare job function. These four do
    # (they mean web full-stack development, nothing else); a "Salesforce Full
    # Stack Developer" still sinks, because the penalty block below outweighs the
    # bonus. Genuine matches also earn it through the two-halves rule, so nothing
    # real depends on this list.
    "fullstack_title_terms": ["full stack", "full-stack", "fullstack", "mern", "mean"],

    # -- Down-ranking (penalty) ----------------------------------------------
    # THE INVERSION. Every Salesforce term below is on his résumé and is the work
    # he is trying to leave, so it is penalized rather than rewarded. This has to
    # be deep, not mild: a Salesforce JD shares "rest api", "sql", "asynchronous"
    # and "batch processing" with him, so on shared vocabulary alone his own
    # current job would out-score a real Java or Flutter role.
    "penalty_terms": {
        "salesforce": -15, "apex": -12, "lwc": -12,
        "lightning web component": -12, "lightning web components": -12,
        "salesforce developer": -12,   # stacks with "salesforce" — deliberately unrecoverable
        "visualforce": -10, "soql": -10, "sosl": -10, "aura": -8,
        "sales cloud": -10, "service cloud": -10,
        "veeva": -10, "ncino": -10, "crm": -5,
        # Adjacent enterprise-platform work: the same "configure someone else's
        # product" job he is leaving, and none of it is on his résumé anyway.
        "abap": -8, "servicenow": -8, "pega": -8, "oracle fusion": -8,
        "dynamics 365": -8, "sap": -6, "mulesoft": -6, "sharepoint": -6,
        # Stacks he doesn't have. "Frontend/backend developer" as a target does
        # NOT mean any framework will do — these are real re-learning costs, so
        # they sink but never drop (he may still want to see them).
        "php": -6, "laravel": -6, "drupal": -6, "wordpress": -6,
        "ruby on rails": -6, "angular": -5, "angularjs": -5,
        # Roles that borrow a dev title for non-development work.
        "manual testing": -6, "qa engineer": -5, "sdet": -5, "selenium": -5,
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
    # hard_drop_terms: never a fit at 1.5 yrs whatever the JD says.
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
    "max_experience_years": 3,   # 1.5 yrs on the résumé; a JD demanding "5+ years" is dropped/penalized
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
    # 11400 USD ~= the 10 LPA floor he asked for (1 lakh ~= $1140, see
    # scraper.demo). Undisclosed, unparseable, or unknown-currency pay is always
    # KEPT — we never drop on a guess, and most Indian postings state nothing.
    "min_comp_usd": 11400,       # None to disable

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
    "remote_scopes": ["worldwide", "remote"],
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
