"""Paid GLOBAL sales sweep — LinkedIn remote-only across 12 countries.

    python scraper.py --profile sales_global --dry-run   # cost check, spends nothing
    python scraper.py --profile sales_global --yes

Everything not restated here is inherited from config.py, which means the whole
sales scoring model, the technical hard-drops, ATS_TITLE_EXCLUDE and the
min_score floor all still apply. This profile changes only WHERE we look.

Why remote-only, and why that is the honest framing of "global": a business
development associate in India is not going to be sponsored for a US or EU work
visa at ~1 year of experience. What IS reachable is remote sales inventory from
companies that hire internationally — SDR, BDR and account-executive seats that
run on a phone and a CRM, which is exactly the shape of her job. So every search
here carries LinkedIn's f_WT=2 (remote), and nothing looks for onsite roles
abroad.

LOCATION_HINTS is cleared to []. That is REQUIRED, not cosmetic: config.py
whitelists Indian cities, and left in place it would delete every foreign row
this profile pays for.

Cost = keywords x countries x max_results x ~$0.001 per LinkedIn result.
As configured: 6 x 12 x 25 = 1,800 results, about $1.80 a sweep. max_spend_usd
below is a hard stop under that; raise it deliberately, never "just to see".

Every geoId below was verified with `python verify_geoids.py`. A wrong one does
not fail — LinkedIn silently returns US results and bills in full.
"""

# remote_only turns each of these into "remote jobs in <country>", which is the
# only way LinkedIn expresses remote: f_WT=2 filters workplace type WITHIN a
# geography, there is no worldwide remote search.
_COUNTRIES = [
    "United States", "United Kingdom", "Canada", "Ireland",
    "Germany", "Netherlands", "Spain", "Portugal",
    "Australia", "Singapore", "United Arab Emirates", "India",
]

SEARCH = {
    # Six, not the eleven in config.py: the paid cost is linear in this list, and
    # these are the titles that actually appear on international remote sales
    # postings. "Academic Counsellor" and "Admission Counsellor" are dropped here
    # because they are India-EdTech titles that barely exist abroad.
    "role_keywords": [
        "Business Development Representative",
        "Sales Development Representative",
        "Inside Sales Representative",
        "Account Executive",
        "Business Development Associate",
        "Customer Success Associate",
    ],
    "locations": _COUNTRIES,
    "max_results": 25,          # per keyword x country — the main cost dial
}

SITES = {
    "linkedin": {"enabled": True, "actor": "curious_coder/linkedin-jobs-scraper",
                 "locations": _COUNTRIES,
                 "remote_only": True,      # f_WT=2 on every search
                 "remote_geo": None},      # unused: no bare "Remote" location here
    # Off: ~5x LinkedIn's cost per result and its coverage skews domestic onsite,
    # which is the one thing this profile is not looking for.
    "indeed": {"enabled": False, "actor": "misceres/indeed-scraper"},
    # India-only with a ~$0.50 floor per run. Never useful here.
    "naukri": {"enabled": False, "actor": "muhammetakkurtt/naukri-job-scraper"},
}

# REQUIRED. config.py whitelists Indian cities; leaving that in place would
# delete every foreign row this profile just paid for.
LOCATION_HINTS = []

# Employer-specific free sources are India-shaped; leave them to the default
# profile rather than paying attention to them on a global run.
OPTUM = {"enabled": False}
ENTERPRISE = {"enabled": False}

SETTINGS = {
    # "restricted" is INCLUDED, on the measurement recorded in
    # profiles/global_remote.py: every f_WT=2 row arrives located by city, so it
    # all classifies as geo-locked, and filtering to worldwide/remote threw away
    # 54 of 76 paid rows. Keep them and read the remote_regions and tz_gap
    # columns to see where each one is locked and how far the timezone is.
    "remote_scopes": ["worldwide", "remote", "restricted"],
    # Still None. An international remote SDR seat may quote anything from a
    # local-market Indian rate to a US band, and dropping on a disclosed figure
    # would filter on the least meaningful half of a commission package.
    "min_comp_usd": None,
    "max_age_days": 21,
    "top_n_console": 30,

    # Hard stop under the ~$1.80 estimate above, so a mis-set dial cannot run
    # away with the credit.
    "max_spend_usd": 4.00,
    # 72 planned runs is far past the default prompt threshold; keep the
    # confirmation rather than discovering the bill afterwards.
    "confirm_above_runs": 12,
}
