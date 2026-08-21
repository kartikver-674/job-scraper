"""Free India sweep — direct company ATS boards + public feeds, last 14 days.

    python scraper.py --profile india_free --site free    # -> output/india_free/

Costs nothing and touches no Apify actor. This is the source aimed squarely at
getting a REPLY rather than a bigger list:

  - Every ATS_BOARDS row is the employer's OWN Greenhouse/Lever/Ashby board, so
    the application lands in their recruiting system directly. No aggregator
    redirect, no reposting farm, and the posting date is the employer's own.
  - The list is weighted toward Indian scaleups (PhonePe, Groww, CRED, Zeta,
    slice, Meesho, Paytm, FamPay, Netradyne, Druva, Postman, MindTickle, Hevo).
    A 2-year MERN engineer is squarely in their hiring band, which is the
    opposite of the Fortune-500 boards where the same résumé is one of thousands
    against a 3-5 year bar.
  - max_age_days = 14, because applying to a three-week-old posting is applying
    behind hundreds of people. Freshness is the single biggest lever on whether
    anyone answers.

ATS_BOARDS and FEEDS are INHERITED from config.py rather than restated — the
token lists there are already probed and annotated with India job counts, and
duplicating them here would mean two places to keep current.

India-only on purpose. Worldwide-remote inventory is covered by
profiles/remote_intl.py, and the measured reachability there is poor (of 480
rows at score >= 10 on one sweep, 16 were actually applyable from India). A
role in Bengaluru that answers beats fifty in Berlin that don't.
"""
from .bigtech import (ATS_TITLE_EXCLUDE, ATS_TITLE_HINTS,  # noqa: F401
                      LOCATION_HINTS, SCORING)

# Paid actors off; the employer-specific sources belong to their own profiles.
SITES = {}
OPTUM = {"enabled": False}
ENTERPRISE = {"enabled": False}

SETTINGS = {
    "remote_scopes": [],          # India onsite/hybrid is the target, not remote
    "drop_excluded": False,       # penalize seniority/over-experience, never delete
    "max_experience_years": 3,
    "experience_aggregate": "max",
    "min_comp_usd": None,         # Indian postings rarely disclose; never drop on a guess
    "min_score": 5,
    "max_age_days": 14,           # see docstring — freshness is the reply lever
    "drop_undated": False,
    "top_n_console": 40,
}
