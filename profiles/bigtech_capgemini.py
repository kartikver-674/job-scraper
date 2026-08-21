"""Capgemini via LinkedIn — the one big employer with no usable company id.

    python scraper.py --profile bigtech_capgemini --site linkedin --yes

Separate from profiles/bigtech_paid.py because it needs the opposite search
shape. That profile filters by `f_C=<numeric id>` with an empty keyword;
Capgemini has no id we can verify — the guest job cards and posting pages carry
only the "capgemini" slug, never a urn:li:organization — and the number widely
cited online (1409) actually returns Wells Fargo Advisors. Guessing it would buy
someone else's jobs at full price, so this searches the NAME instead.

That is safe here for a reason worth stating: "Capgemini" is a distinctive token.
Probed on the guest endpoint 2026-08-16, keyword=capgemini in India returned
10/10 cards whose employer really was Capgemini. A generic name ("Oracle",
"Amazon") would not behave this way — those words appear in thousands of other
companies' job descriptions, which is exactly why the other four are id-filtered.

The company column is still filtered afterwards (see auto-apply/merge step), so
a posting that merely mentions Capgemini can't sneak into the shortlist.
"""
from .bigtech import (ATS_TITLE_EXCLUDE, ATS_TITLE_HINTS,  # noqa: F401
                      LOCATION_HINTS, SCORING)
from .bigtech_paid import SETTINGS  # noqa: F401 — same gates, same spend cap

ATS_BOARDS = {}
FEEDS = {}
OPTUM = {"enabled": False}
ENTERPRISE = {"enabled": False}

SEARCH = {
    "role_keywords": ["Capgemini"],
    "locations": ["India"],
    "country": "IN",
    "experience_years": None,
    "salary_min": None,
    # 120, not 800, for the reason recorded in bigtech_paid: at 800 this actor
    # TIMED OUT on two of four runs, and a timed-out run bills for the compute
    # and returns nothing.
    "max_results": 120,
}

SITES = {
    "indeed": {"enabled": False},
    "naukri": {"enabled": False},
    "linkedin": {
        "enabled": True,
        "actor": "curious_coder/linkedin-jobs-scraper",
        "locations": ["India"],
        # No "companies" key: that is the whole difference from bigtech_paid.
        "remote_geo": "India",
        "remote_only": False,
    },
}
