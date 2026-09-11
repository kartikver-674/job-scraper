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
    # OFF by default, and re-measured 2026-09-12 across every run in output/
    # (22,745 rows): naukri has still produced ZERO rows and appears in no
    # .done_combos on any profile — in a year it has never executed once,
    # because at a $0.50 per-run MINIMUM it is the most expensive source in the
    # table and its inventory is largely what LinkedIn already returns for the
    # same searches. Turn it on per profile if you want India-specific boards
    # LinkedIn misses.
    #
    # Deleting the entry outright was tried on 2026-09-12 and REVERTED. It is
    # not a one-line removal: make_profile.validate_keys rejects a SITES key
    # that config does not define, so every generated profile breaks, and eight
    # sweep tests assert naukri's presence in the UI and that its per-run floor
    # is not scaled by the depth control. enabled=False already means it cannot
    # be planned or run; the cascade costs more than the dead line it removes.
    "naukri":   {"enabled": False, "actor": "muhammetakkurtt/naukri-job-scraper",
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
    # Found by scraping the public jobs-search page for the location name, after
    # five blind guesses resolved to Galway, Glasgow, Ahmedabad, Gurugram and
    # nothing. verify_geoids.py reported MISMATCH until its alias table learned
    # the district spellings -- that checker rejects a correct id whenever
    # LinkedIn's label and our name disagree, so read the locations it prints.
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
        # --- Adzuna Phase 1 harvest, 2026-09-11 -------------------------------
        # Employer names came from the Adzuna API (docs/superpowers/specs/
        # 2026-09-11-adzuna-phase1-results.md); the boards themselves were
        # resolved by harvest_ats.py against the platforms already in
        # sources/ats.py, and every one was re-probed live the day it was added.
        # Counts are india/total at that probe. b2 = the employer was already
        # known through a paid sweep, so this is one we can now stop paying to
        # see; b3 = never seen through any source before.
        "rws": "RWS",                         #  10/72   b2
        "dozee": "Dozee",                     #  15/27   b3
        "pocketfm": "Pocket FM",              #   2/5    b3
        # --- backlog harvest, 2026-09-11 --------------------------------------
        # From the Apify-only backlog (docs/superpowers/specs/
        # 2026-09-11-backlog-harvest.md): 486 of 2,512 companies probed,
        # 12.9% resolved. Re-probed live the day they were added. Counts are
        # india/total. Trailing flags: i = this employer has only ever posted
        # inside India; m = the board carries a posting a PAID sweep already
        # found, so it converts a job we were paying to see into a free one.
        "veeva": "Veeva Systems",             #  33/898   m
        "portagepointpartners": "Portage Point Partners",   #  31/52   im
        "acceldata": "Acceldata",             #  19/46   im
        "sophos": "Sophos",                   #  16/115   m
        "levelai": "Level AI",                #  16/19    m
        "jumpcloud": "JumpCloud",             #  11/20   im
        "appzen": "AppZen",                   #  10/22   im
        "cin7": "Cin7",                       #   2/10    m
        "binance": "Binance",                 #   2/296   m
        "biorender": "BioRender",             #   0/1

    },
    # Indian employers, plus global companies WITH an India presence — the
    # combination that makes SETTINGS["keep_restricted_if_hires_home"] pay off,
    # since their geo-locked remote roles become reachable. India-job counts
    # probed 2026-07-26. ONE greenhouse key only: a second one silently replaces
    # this whole dict rather than adding to it.
    "greenhouse": {
        # phonepe removed 2026-09-11: its greenhouse AND lever boards both 404.
        "groww": "Groww", "postman": "Postman",
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
        "flix": "Flix",               #  9/154
        "sumup": "SumUp",             #  2/369
        "ubiquiti": "Ubiquiti",       #  0/159 — no India entity, so its geo-locked
        "justworks": "Justworks",     #  0/98    roles can never be rescued; kept
                                      #          only for worldwide-remote postings
        # --- Adzuna Phase 1 harvest, 2026-09-11 -------------------------------
        # Employer names came from the Adzuna API (docs/superpowers/specs/
        # 2026-09-11-adzuna-phase1-results.md); the boards themselves were
        # resolved by harvest_ats.py against the platforms already in
        # sources/ats.py, and every one was re-probed live the day it was added.
        # Counts are india/total at that probe. b2 = the employer was already
        # known through a paid sweep, so this is one we can now stop paying to
        # see; b3 = never seen through any source before.
        "tide": "Tide",                       #  24/81   b2
        "bitwarden": "Bitwarden",             #  22/46   b2
        "nice": "NICE",                       #  22/180  b2
        "towerresearchcapital": "Tower Research Capital",   #  12/86   b2
        "dunnhumby": "dunnhumby",             #   2/35   b2
        "elsevier": "Elsevier",               #   0/9    b2
        "iris": "Iris Software",              #   0/2    b2
        "wise": "Wise",                       #   0/18   b2
        "capco": "Capco",                     # 162/697  b3 — the largest single
                                              #          board in this table
        "wppproduction": "WPP Production",    #  31/158  b3
        "stratainformationgroup": "Strata Information Group",  #   2/11   b3
        "indigo": "Indigo",                   #   0/2    b3
        "mcafee": "McAfee, Inc.",             #   0/4    b3
        "unisonconsulting": "Unison Consulting",            #   0/2    b3
        "victrix": "Victrix Systems & Labs",  #   0/3    b3
        # --- backlog harvest, 2026-09-11 --------------------------------------
        # From the Apify-only backlog (docs/superpowers/specs/
        # 2026-09-11-backlog-harvest.md): 486 of 2,512 companies probed,
        # 12.9% resolved. Re-probed live the day they were added. Counts are
        # india/total. Trailing flags: i = this employer has only ever posted
        # inside India; m = the board carries a posting a PAID sweep already
        # found, so it converts a job we were paying to see into a free one.
        "okta": "Okta",                       # 105/324   m
        "payoneer": "Payoneer",               #  47/122  im
        "accordionindia": "Accordion India",  #  21/21   i
        "netskope": "Netskope",               #  20/139   m
        "avathon": "Avathon",                 #  16/35   im
        "newrelic": "New Relic",              #  12/56    m
        "cloudsek": "CloudSEK",               #  11/14   im
        "komodohealth": "Komodo Health",      #   8/31   im
        "godaddy": "GoDaddy",                 #   6/31    m
        "precisionaq": "Precision AQ",        #   5/40   im
        "launchdarkly": "LaunchDarkly",       #   5/51   im
        "bitgo": "BitGo",                     #   4/37   im
        "eulerity": "Eulerity",               #   1/17    m
        "rtingscom": "RTINGS.com",            #   0/5     m
        "fingerprint": "Fingerprint",         #   0/23   im
        "breezeway": "Breezeway",             #   0/10
        "diligent": "Diligent",               #   0/5
        "cobblestoneenergy": "Cobblestone Energy",          #   0/2
        "shield": "SHIELD",                   #   0/1
        "bold": "BOLD",                       #   0/1    i
        # Speechify resolved (greenhouse:speechify, 1,086 postings) and is NOT
        # here: it publishes one opening per US city with the city IN THE TITLE
        # ("Go-to-Market - Anaheim, CA, USA"), so 1,041 distinct titles across
        # 329 locations. job_key is company+title, so dedupe cannot collapse
        # them and a sweep would carry all of them.

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
        # Moved here from greenhouse 2026-09-11: that board now 404s and the
        # company has re-platformed onto Ashby. Same employer, live board.
        "clickhouse": "ClickHouse",   # was greenhouse, 184 jobs on ashby
        # --- Adzuna Phase 1 harvest, 2026-09-11 -------------------------------
        # Employer names came from the Adzuna API (docs/superpowers/specs/
        # 2026-09-11-adzuna-phase1-results.md); the boards themselves were
        # resolved by harvest_ats.py against the platforms already in
        # sources/ats.py, and every one was re-probed live the day it was added.
        # Counts are india/total at that probe. b2 = the employer was already
        # known through a paid sweep, so this is one we can now stop paying to
        # see; b3 = never seen through any source before.
        "tekion": "Tekion",                   #  88/110  b3
        "gradera": "Gradera",                 #   6/8    b3
        "whisk": "Whisk Software Private Limited",          #   0/4    b3
        # --- backlog harvest, 2026-09-11 --------------------------------------
        # From the Apify-only backlog (docs/superpowers/specs/
        # 2026-09-11-backlog-harvest.md): 486 of 2,512 companies probed,
        # 12.9% resolved. Re-probed live the day they were added. Counts are
        # india/total. Trailing flags: i = this employer has only ever posted
        # inside India; m = the board carries a posting a PAID sweep already
        # found, so it converts a job we were paying to see into a free one.
        "elevenlabs": "ElevenLabs",           #  10/248   m
        "uipath": "UiPath",                   #   5/109  i
        "glomo": "Glomo",                     #   4/8    im
        "clera": "Clera",                     #   1/267   m
        "abound": "Abound",                   #   0/19    m
        "maincode": "Maincode",               #   0/15    m
        "xero": "Xero",                       #   0/123   m
        "solace": "Solace",                   #   0/27    m
        "brainco": "Brain Co.",               #   0/34    m
        "realmalliance": "Realm Alliance",    #   0/11    m
        "nory": "Nory",                       #   0/7     m
        "freetrade": "Freetrade",             #   0/8     m
        "omni": "Omni",                       #   0/22    m
        "attio": "Attio",                     #   0/43    m
        "pylon": "Pylon",                     #   0/12
        "vantage": "Vantage",                 #   0/5
        "sitemate": "Sitemate",               #   0/15
        "pilgrim": "Pilgrim",                 #   0/4    i

    },
    # smartrecruiters and breezy list NO DESCRIPTION (sources/ats.py maps no
    # Description field for either), so their postings are scored on the TITLE
    # ALONE. That is a known, accepted limitation of those adapters and not a
    # new one — but it means a board here contributes less per posting than a
    # greenhouse/lever/ashby board of the same size.
    "smartrecruiters": {
        # --- Adzuna Phase 1 harvest, 2026-09-11 -------------------------------
        # Employer names came from the Adzuna API (docs/superpowers/specs/
        # 2026-09-11-adzuna-phase1-results.md); the boards themselves were
        # resolved by harvest_ats.py against the platforms already in
        # sources/ats.py, and every one was re-probed live the day it was added.
        # Counts are india/total at that probe. b2 = the employer was already
        # known through a paid sweep, so this is one we can now stop paying to
        # see; b3 = never seen through any source before.
        "jitterbit": "Jitterbit",             #  10/25   b2
        "renesaselectronics": "Renesas Electronics",        #  10/100  b2
        "sia": "Sia",                         #   2/100  b2
        "agileengine": "AgileEngine",         #   0/1    b2
        "jadeglobal": "Jade Global",          #   0/6    b2
        "version1": "Version 1",              #  24/100  b3
        "informagroupplc": "Informa Group Plc.",            #  14/100  b3
        "quantanite": "Quantanite",           #   8/9    b3
        "blueoptima": "BlueOptima",           #   5/12   b3
        "keywordsstudios": "Keywords Studios",              #   1/51   b3
        "metromakro": "METRO/MAKRO",          #   1/100  b3
        "nisum": "Nisum",                     #   0/1    b3
        "technogen": "TechnoGen",             #   0/49   b3
        "vichara": "Vichara Technologies",    #   0/8    b3
        # --- backlog harvest, 2026-09-11 --------------------------------------
        # From the Apify-only backlog (docs/superpowers/specs/
        # 2026-09-11-backlog-harvest.md): 486 of 2,512 companies probed,
        # 12.9% resolved. Re-probed live the day they were added. Counts are
        # india/total. Trailing flags: i = this employer has only ever posted
        # inside India; m = the board carries a posting a PAID sweep already
        # found, so it converts a job we were paying to see into a free one.
        "codeyoung": "Codeyoung",             #   2/2    i
        "capestart": "CapeStart",             #   1/1    i
        "genpactindia": "Genpact India Pvt. Ltd.",          #   1/1    i
        "servicetitan": "ServiceTitan",       #   0/8    i
        "rebelfoods": "Rebel Foods",          #   0/1    i
        "gepworldwide": "GEP Worldwide",      #   0/1    i
        "lingaro": "Lingaro",                 #   0/1    i
        "pentair": "Pentair",                 #   0/1    i
        "spottedzebra": "Spotted Zebra",      #   0/1
        "synechron": "Synechron",             #   0/3

    },
    "breezy": {
        # --- Adzuna Phase 1 harvest, 2026-09-11 -------------------------------
        # Employer names came from the Adzuna API (docs/superpowers/specs/
        # 2026-09-11-adzuna-phase1-results.md); the boards themselves were
        # resolved by harvest_ats.py against the platforms already in
        # sources/ats.py, and every one was re-probed live the day it was added.
        # Counts are india/total at that probe. b2 = the employer was already
        # known through a paid sweep, so this is one we can now stop paying to
        # see; b3 = never seen through any source before.
        "iqvia": "IQVIA",                     #   0/7    b2
        # --- backlog harvest, 2026-09-11 --------------------------------------
        # From the Apify-only backlog (docs/superpowers/specs/
        # 2026-09-11-backlog-harvest.md): 486 of 2,512 companies probed,
        # 12.9% resolved. Re-probed live the day they were added. Counts are
        # india/total. Trailing flags: i = this employer has only ever posted
        # inside India; m = the board carries a posting a PAID sweep already
        # found, so it converts a job we were paying to see into a free one.
        "anovia": "Anovia Inc.",              #   9/12   im
        "foundationhealth": "Foundation Health",            #   0/31

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
    # Measured the best free source in the project (output/, 2026-09-09): 58% of
    # its distinct postings scored >= 40, against LinkedIn's 27% and
    # greenhouse's 11%. It was fetched four categories deep; these are the rest
    # of the ones a software search should see. One request each, no cost.
    "wwr": {"enabled": True, "categories": [
        "remote-programming-jobs",
        "remote-front-end-programming-jobs",
        "remote-back-end-programming-jobs",
        "remote-full-stack-programming-jobs",
        "remote-devops-sysadmin-jobs",
        "remote-design-jobs",
        "remote-product-jobs",
        "remote-jobs",                     # the catch-all board
    ]},
    "remotive": {"enabled": True},
    "jobicy": {"enabled": True, "count": 50},
    # `queries` uses himalayas.app/jobs/api/search (q=, 20 per page, no auth),
    # which did not exist when this was written — the old comment said no filter
    # was available and the adapter paged blind through ~96k mostly
    # non-engineering jobs, 200 at a time, for the 76 distinct postings it ever
    # contributed. make_profile writes the résumé's own role keywords here.
    # Empty `queries` keeps the old blind paging, so nothing breaks without one.
    "himalayas": {"enabled": True, "pages": 10, "queries": []},
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
# THE most consequential list in this file for free sources: every ATS board and
# feed returns its whole catalogue, and scraper.is_dev_title() drops anything
# whose title matches none of these BEFORE it is ever scored. A term missing
# here is inventory nobody ever sees.
#
# This is the generic software floor. It used to be 21 entries built around one
# React/Node résumé, which is why the five hand-tuned profiles all replace it —
# and why a Salesforce or Java résumé, whose generated profile does NOT replace
# it, lost most of every free board before scoring. make_profile now writes a
# résumé-specific list UNIONED with this one, so a profile can widen the net but
# never narrow it below this.
ATS_TITLE_HINTS = [
    # Core software engineering. "engineer" alone is deliberately absent —
    # it matches sales engineer, process engineer, mechanical engineer.
    "software engineer", "software development", "software dev", "developer",
    "development engineer", "sde", "programmer", "engineering manager",
    "member of technical staff", "tech lead", "technical lead", "staff engineer",
    "principal engineer", "software architect", "solutions architect",
    # Stack positions
    "full stack", "fullstack", "full-stack", "frontend", "front end", "front-end",
    "backend", "back end", "back-end", "web developer", "mern", "mean stack",
    # Named stacks, so a title that only says the technology still lands
    "react", "node", "javascript", "typescript", "python", "java ", "golang",
    ".net", "php", "ruby", "rails", "django", "spring boot", "c#",
    # Mobile
    "mobile developer", "mobile engineer", "android", "ios ", "ios engineer",
    "ios developer", "flutter", "react native",
    # Application / API / integration
    "application developer", "api developer", "api engineer",
    "integration engineer", "systems engineer",
    # AI / ML — a large and growing share of what these boards post
    "ml engineer", "ai engineer", "machine learning engineer", "applied ai",
    "genai", "gen ai", "generative ai", "llm engineer",
    # Platform / infrastructure / reliability
    "platform engineer", "infrastructure engineer", "devops", "site reliability",
    "sre ", "cloud engineer", "build engineer", "release engineer",
    "automation engineer", "developer productivity", "developer experience",
    # Quality
    "qa engineer", "test engineer", "sdet", "quality engineer",
    # Security
    "security engineer", "application security",
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

# Measured cost per paid search, in USD. Every figure here came from a real
# sweep's billing, not from an actor's self-report — those undercount roughly 3x
# (see scraper.account_usage_usd). A site absent from this table is free.
#
# These are ESTIMATES for planning only. The real guard is
# SETTINGS["max_spend_usd"], which reads the account mid-sweep and refuses to
# launch another search once crossed.
SITE_RATES = {
    "linkedin": 0.045,   # measured at max_results=25
    "indeed": 0.09,      # ~$0.09 per run
    "naukri": 0.50,      # $0.50 per run MINIMUM — bad value at small budgets
}

# The result count each rate above was measured at. A pay-per-event actor bills
# per result, so a rate means nothing without the depth it was measured at, and
# these three were measured at three different depths:
#   linkedin — the comment above says max_results=25.
#   indeed   — $0.09/run at the ~$0.03-per-5-results rate noted in SETTINGS
#              below is 15 results, which is also SEARCH["max_results"].
#   naukri   — its own results_per_run is 50 and the $0.50 is a per-run
#              MINIMUM, not a per-result price. Its basis therefore equals the
#              depth it always runs at, which makes it unscalable on purpose:
#              the depth control cannot move it, and a floor does not halve.
# Scaling every rate from one basis over-charges naukri 2x unconditionally and
# under-states indeed by 40% at the default depth.
SITE_RATE_BASIS = {
    "linkedin": 25,
    "indeed": 15,
    "naukri": 50,
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

    # Lead-gen farms, not employers. They repost other companies' listings under
    # their own name — one set of titles sprayed across country subdomains with
    # sequential LinkedIn IDs — so they match the résumé well and score at the
    # very top while being unapplyable. Measured on the 2026-07-26 sweep: 4 names
    # accounted for 11 of the 15 "reachable" rows at score >= 20. Dropped
    # outright, whatever they score. Matched on the company name, case- and
    # punctuation-insensitively ("SWAKIO™" -> "swakio"), whole name only, so a
    # real employer whose name merely contains one of these is unaffected.
    # jobgether and speechify were both kept out of ATS_BOARDS on 2026-09-11 and
    # are blocked here too, because a retroactive audit found them ALREADY IN
    # paid output — 41 and 12 LinkedIn rows respectively. They fail in two
    # different ways, and only the first is what this list was written for:
    #
    #   jobgether   a repost aggregator, not an employer. Its Lever board is
    #               4,669 postings, 2,023 titles reposted across up to 42
    #               countries. 19 distinct postings reached our output, 12 of
    #               them scoring >= 20 and 5 >= 40 — all of them other
    #               companies' jobs, applied for through the wrong door.
    #   speechify   A REAL EMPLOYER, blocked for posting SHAPE rather than
    #               provenance: it publishes one row per city with the city in
    #               the TITLE ("Software Engineer, Platform - Sydney,
    #               Australia"), which job_key cannot collapse because it keys
    #               on company+title. Its board would carry 920 rows for 4
    #               distinct roles. Blocking it does delete genuine openings —
    #               today all 12 score 11-12, none >= 20 — so if that trade ever
    #               looks wrong, remove this one entry rather than the pair.
    "company_blocklist": ["hired", "hire feed", "jobs ai", "swakio",
                          "jobgether", "speechify"],

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
        # TOO JUNIOR, which nothing in the model caught. max_experience_years
        # reads the years a posting DEMANDS, so it stops "8+ years" and has no
        # opinion whatever about a req that wants zero. Measured on the free
        # global sweep of 2026-09-01: Notion's "Software Engineer, New Grad
        # (Dec 2026)" ranked 3rd of the abroad rows at 44, Mactores' "Full Stack
        # Product Engineer Intern" ranked 2nd of the remote rows at 30, and
        # Stripe's "Software Engineer, Intern" made the India list. They score
        # well because a new-grad JD lists the same stack; the mismatch is
        # entirely in the band.
        #
        # Here rather than in ATS_TITLE_EXCLUDE on purpose: that list only gates
        # the free sources (see scraper.is_dev_title), while hard_drop_terms runs
        # on paid rows too AND is re-applied to stored rows at merge time, so
        # rows already on disk get dropped instead of lingering with old scores.
        #
        # "intern" and "internship" are both listed because the matcher is
        # word-boundary: "intern" does not fire inside "internship" -- nor,
        # usefully, inside "internal" or "international".
        "intern", "internship", "trainee", "fresher", "apprentice", "co-op",
        "new grad", "graduate", "junior", "jr",
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
    # How to combine several "N years" figures in one posting. "max" reads the
    # largest as the real ask; "min" the smallest.
    #
    # Defaulted to "max" on the evidence, 2026-09-09: all five hand-tuned
    # profiles in this repo already set it, scraper's own docstring records 63
    # requisitions where the two disagreed 21 times and max was right every
    # time, and a user reported the symptom "min" produces — the results
    # column reading 2+ or 3+ on postings whose JD asks for 5+ or 8+, because
    # a structured JD states a per-skill figure next to its total.
    #
    # Under-reading is the dangerous direction: it puts a senior role at the
    # top of a junior candidate's shortlist, while over-reading only drops a
    # reachable one. See scraper._required_experience_floor.
    "experience_aggregate": "max",
    "min_score": None,           # drop jobs scoring below this after ranking (None = keep all, just sorted)
    "max_age_days": 14,          # drop jobs posted longer ago than this (older ones are likely closed). None to disable.
    "drop_undated": False,       # if True, also drop jobs whose posted date can't be parsed (default: keep them)
    # Minimum compensation, annualized and in USD, so an Indian LPA figure and a
    # US/EU salary are compared on the same axis (see scraper.comp_max_usd).
    # 6000 USD ~= the old 5.2 LPA floor. Undisclosed, unparseable, or
    # unknown-currency pay is always KEPT — we never drop on a guess.
    # Raise this to ~40000+ once the sweep is weighted toward international remote.
    "min_comp_usd": 6000,        # None to disable

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
