"""Paid global remote sweep — LinkedIn across many countries, remote-only.

The one thing worth spending Apify credit on for an international search, and
the only paid actor that makes sense for it: LinkedIn is ~$0.001/result against
Indeed's ~$0.005, and Indeed's strength is domestic listings that skew onsite.

    python scraper.py --profile global_remote --dry-run    # cost check, spends nothing
    python scraper.py --profile global_remote --yes

Cost = keywords x countries x max_results x $0.001. As configured below that is
5 x 12 x 25 = 1,500 results ~= $1.50 per sweep. Widen `countries` or
`max_results` and it scales linearly; see the table in the README.

Every geoId used here was verified with `python verify_geoids.py` — a wrong one
is not a soft failure, LinkedIn returns US results and bills you in full.

Free sources run alongside at no cost, so a sweep on this profile is
free-sources + paid LinkedIn together.
"""

# remote_only below turns each of these into "remote jobs in <country>", which
# is the only way LinkedIn expresses remote — f_WT=2 filters workplace type
# within a geography, there is no worldwide remote search.
_COUNTRIES = [
    "United States", "United Kingdom", "Canada", "Ireland",
    "Germany", "Netherlands", "Spain", "Portugal", "Poland",
    "Australia", "Singapore", "United Arab Emirates",
]

SEARCH = {
    # Salesforce-specific titles only. A bare "Business Analyst" or "Consultant"
    # here would buy 12 countries' worth of adjacent-domain BA roles at full price.
    "role_keywords": [
        "Salesforce Functional Consultant",
        "Salesforce Business Analyst",
        "Salesforce Consultant",
        "Salesforce Administrator",
        "Salesforce Implementation Consultant",
    ],
    "locations": _COUNTRIES,
    "max_results": 25,        # per keyword x country; the main cost dial
}

SITES = {
    "linkedin": {"enabled": True, "actor": "curious_coder/linkedin-jobs-scraper",
                 "locations": _COUNTRIES,
                 "remote_only": True,      # f_WT=2 on every search
                 "remote_geo": None},      # unused: no bare "Remote" location here
    # Off on purpose: 5x LinkedIn's cost per result, and its coverage is domestic
    # listings that skew onsite. Turn on only for a country-specific deep dive.
    "indeed": {"enabled": False, "actor": "misceres/indeed-scraper"},
    # India-only, and charges a ~$0.50 floor per run. Never useful here.
    "naukri": {"enabled": False, "actor": "muhammetakkurtt/naukri-job-scraper"},
}

# Non-dev slices, matching config.py — see the verification notes there for which
# of these filters actually do anything.
FEEDS = {
    "remoteok": {"enabled": True},
    "wwr": {"enabled": True, "categories": [
        "remote-sales-and-marketing-jobs",
        "remote-management-and-finance-jobs",
        "remote-product-jobs",
        "remote-customer-support-jobs",
    ]},
    "remotive": {"enabled": True},
    "jobicy": {"enabled": True, "count": 50, "industry": "business"},
    "himalayas": {"enabled": True, "pages": 15},
}

# Kept as-is even though these are tech companies and this is not a dev search:
# every one of them RUNS Salesforce internally and hires admins / business systems
# analysts into its Business Technology org, which is exactly the kind of in-house
# Salesforce role that pays well and is easy to miss on a job board. The corrected
# ATS_TITLE_HINTS is what lets those rows through.
ATS_BOARDS = {
    "greenhouse": {
        "gitlab": "GitLab", "databricks": "Databricks", "twilio": "Twilio",
        "mongodb": "MongoDB", "elastic": "Elastic", "postman": "Postman",
        "druva": "Druva", "datadog": "Datadog", "cloudflare": "Cloudflare",
    },
    "lever": {},
    "ashby": {"openai": "OpenAI", "notion": "Notion"},
    "smartrecruiters": {},
}

SETTINGS = {
    # "restricted" is INCLUDED here, unlike remote_intl. This profile exists to
    # buy LinkedIn's cross-border remote inventory, and measurement showed every
    # f_WT=2 row arrives located by city — so it all classifies as geo-locked.
    # Filtering to worldwide/remote threw away 54 of 76 paid rows and produced a
    # single job. Keep them and let the remote_regions and tz_gap columns say
    # where each one is locked and how far the timezone is, rather than deleting
    # what was just paid for. Read those columns; don't just read the score.
    "remote_scopes": ["worldwide", "remote", "restricted"],
    "min_comp_usd": 30000,
    "max_age_days": 21,
    "top_n_console": 25,

    # A hard stop well under the estimate above, so a mis-set dial can't run away
    # with the credit. Raise it deliberately, never "just to see".
    "max_spend_usd": 4.00,
    # 84 planned runs is far past the default prompt threshold; keep the
    # confirmation rather than discovering the bill afterwards.
    "confirm_above_runs": 12,
}
