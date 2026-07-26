"""Paid LinkedIn sweep for remote roles hiring INTO India.

The one slice of paid inventory that measurably earns its cost. The global
LinkedIn sweep was measured and rejected: of 76 rows from a remote-only query
across US/UK/Canada, 24 came back locked to those countries, 26 read as
hybrid/onsite from their own descriptions (LinkedIn's f_WT=2 filter is leaky),
and only 4 were reachable from India. India-remote inventory, by contrast, is
100% reachable, and it's the one thing the free international boards don't cover.

    python scraper.py --profile india_remote --dry-run
    python scraper.py --profile india_remote --yes

9 keywords x 1 location = 9 runs at the measured $0.013/run, so ~$0.12.

Free sources are OFF here on purpose: this profile exists to fetch the paid
inventory the free sweep can't see, and re-running 700 free rows would just
duplicate what's already in output/remote_intl/. Merge the two for one shortlist.
"""

SEARCH = {
    "role_keywords": [
        "Full Stack Developer",
        "Full Stack Engineer",
        "MERN Stack Developer",
        "React Native Developer",
        "React Developer",
        "Node.js Developer",
        "Frontend Developer",
        "Software Engineer JavaScript",
        "Backend Developer Node",
    ],
    # "Remote" resolves via SITES["linkedin"]["remote_geo"] below to India +
    # f_WT=2. One location, so the run count equals the keyword count.
    "locations": ["Remote"],
    "max_results": 25,
    "experience_years": 2,
}

SITES = {
    "linkedin": {"enabled": True, "actor": "curious_coder/linkedin-jobs-scraper",
                 "locations": ["Remote"],
                 "remote_geo": "India",     # verified geoId 102713980
                 "remote_only": False},     # the "Remote" location sets f_WT=2
    "indeed": {"enabled": False, "actor": "misceres/indeed-scraper"},
    "naukri": {"enabled": False, "actor": "muhammetakkurtt/naukri-job-scraper"},
}

ATS_BOARDS = {}
FEEDS = {}

SETTINGS = {
    # India-remote rows arrive located "India", so they classify as `restricted`
    # with remote_regions "India" — and are kept by
    # keep_restricted_if_hires_home, which rescues a lock TO the home country.
    # That rule exists precisely for this profile.
    "remote_scopes": ["worldwide", "remote"],

    # An INDIA pay floor, not the international one. remote_intl uses $30k, which
    # would delete most of this inventory: 6000 USD is roughly the 5.2 LPA the
    # default profile has always used.
    "min_comp_usd": 6000,

    "max_age_days": 21,
    "top_n_console": 25,
    "max_spend_usd": 1.00,     # hard stop; the estimate is ~$0.12
}
