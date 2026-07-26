"""International remote roles workable from India, ~2 years experience.

The default profile in config.py is tuned for the Delhi/NCR market: Indian cities
in SEARCH["locations"], a pay floor set from an Indian salary, and no remote
filtering. This profile points the same pipeline at remote roles hiring across
borders instead.

    python scraper.py --profile remote_intl --site free     # zero cost
    python scraper.py --profile remote_intl                 # includes paid sites

Output lands in output/remote_intl/, so it never mixes with a default sweep.
"""

# Remote-first phrasing, and no city cross-product: for the paid boards the
# location axis is "remote", not a list of cities, which also keeps the actor-run
# count (keywords x locations) small.
SEARCH = {
    "role_keywords": [
        "Full Stack Engineer",
        "Full Stack Developer",
        "React Native Developer",
        "React Developer",
        "Node.js Developer",
        "Backend Engineer JavaScript",
        "Frontend Engineer TypeScript",
    ],
    "locations": ["Remote"],
    "country": "US",          # Indeed country code; the remote boards ignore this
}

# Only the free, international sources by default. The paid actors are still
# available with an explicit --site, but Indeed/Naukri are India-heavy and
# LinkedIn's remote filter needs a geoId per country, so they earn their cost
# less here than on a domestic sweep.
SITES = {
    "linkedin": {"enabled": False, "actor": "curious_coder/linkedin-jobs-scraper",
                 "locations": ["Remote"]},
    "indeed":   {"enabled": False, "actor": "misceres/indeed-scraper"},
    "naukri":   {"enabled": False, "actor": "muhammetakkurtt/naukri-job-scraper",
                 "results_per_run": 50, "locations": ["Remote"]},
}

# Remote-first companies on ATS platforms with public boards. Verified to resolve
# 2026-07-26; the India-focused boards from the default profile are dropped since
# they post local roles.
#
# These all geo-lock their remote postings ("New York, NY (HQ), Remote",
# "Europe"), so every row lands in "restricted" and used to be filtered out
# wholesale. SETTINGS["keep_restricted_if_hires_home"] now rescues the ones whose
# employer demonstrably hires in India, which is what makes these boards earn
# their place: OpenAI (9 India postings of 753) contributes 5 rescued roles
# including the #3 overall result, Postman (12/114) and Druva (11/31) qualify too,
# while Linear (0/25) and Ramp (0/118) correctly stay filtered.
# Prefer GLOBAL companies with an India presence here — an India-only board
# (PhonePe, Paytm) posts local roles and adds nothing to a remote sweep.
ATS_BOARDS = {
    "greenhouse": {
        "gitlab": "GitLab",            # 30/187 India — an all-remote company
        "databricks": "Databricks",    # 76/800
        "twilio": "Twilio",            # 25/183
        "mongodb": "MongoDB",          # 17/401
        "elastic": "Elastic",          # 16/204
        "postman": "Postman",          # 12/114
        "druva": "Druva",              # 11/31
        "datadog": "Datadog",          #  9/418
        "cloudflare": "Cloudflare",    #  3/271
    },
    "lever": {},
    "ashby": {
        "openai": "OpenAI",            #  9/753 India -> hires_home yes
        "notion": "Notion",            #  5/127
        "linear": "Linear",            #  0/25  India -> correctly filtered out
        "ramp": "Ramp",                #  0/118 India -> correctly filtered out
    },
    "smartrecruiters": {},
}

# The feeds are the single best source of genuinely worldwide remote roles, and
# they cost nothing. remotive/jobicy/himalayas are also the ONLY free source that
# reports pay, which is what makes min_comp_usd below do anything at all.
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
    "himalayas": {"enabled": True, "pages": 15},   # unfiltered API: low yield/page
}

SETTINGS = {
    # The whole point: drop onsite, hybrid and geo-locked roles. "restricted" is
    # excluded because a US-only or EU-only remote role is not applicable from
    # India — add it back to see them, and read the remote_regions column.
    "remote_scopes": ["worldwide", "remote"],

    # Left OFF deliberately. Only ~1% of postings mention sponsorship at all, so
    # filtering on it discards hundreds of viable jobs to remove a handful of
    # explicit refusals. The visa and eor COLUMNS are still populated — read
    # them, don't filter on them, until the corpus proves otherwise.
    "drop_no_visa": False,
    "require_eor": False,

    # International remote pay, in USD. The default 6000 is an Indian-market
    # floor and would let through roles paying a fraction of the market rate.
    "min_comp_usd": 30000,

    # Remote boards move fast and stale listings are closed listings.
    "max_age_days": 21,

    "top_n_console": 20,
}
