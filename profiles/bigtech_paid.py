"""The four big employers that have no free door — via LinkedIn, company-filtered.

    python scraper.py --profile bigtech_paid --site linkedin --yes

profiles/bigtech.py reaches Amazon, Accenture, JPMorgan, Oracle and SAP for free
off their own careers platforms. Microsoft, IBM, Siemens and Deloitte have no
free door at all — Eightfold answers 403, IBM fetches results client-side from an
endpoint named in none of its scripts, Siemens renders zero job anchors
server-side, and Deloitte's portal serves US roles only (all recorded in
sources/enterprise.py). LinkedIn is the remaining route, and it costs money.

WHY COMPANY-FILTERED AND NOT KEYWORD-SEARCHED. `f_C=<numeric id>` asks LinkedIn
for one employer's postings. Searching the company NAME as a keyword instead
matches the JD body, so "Microsoft" returns every consultancy whose posting
mentions Azure. At ~$0.001/result the difference is not the money, it's that
half the rows would be unusable.

The ids are verified, not looked up once and trusted: a wrong f_C does not
error, it returns a different employer's jobs at full price. `1409`, widely
cited as Capgemini, is Wells Fargo Advisors. All four below returned 10/10 cards
for the right employer on 2026-08-16 (`python verify_geoids.py --companies`).

CAPGEMINI IS NOT HERE. Its numeric id is not exposed on any guest surface, and
guessing one is precisely the mistake above. It is run separately, by keyword,
with a company-name filter applied afterwards — see profiles/bigtech_capgemini.py.

Scoring, title gates and location gates are imported from profiles/bigtech so the
paid rows rank on exactly the same résumé as the free ones and the two can be
merged into one list.
"""
from .bigtech import (ATS_TITLE_EXCLUDE, ATS_TITLE_HINTS,  # noqa: F401
                      LOCATION_HINTS, SCORING)

# Everything free is off here: this profile exists to spend money on the gap,
# and re-running the free boards would just duplicate output/bigtech/.
ATS_BOARDS = {}
FEEDS = {}
OPTUM = {"enabled": False}
ENTERPRISE = {"enabled": False}

SEARCH = {
    # ONE empty keyword per company. With f_C already narrowing to the employer,
    # an empty query returns everything they have posted in India, which is both
    # cheaper and more complete than guessing role words — the same lesson the
    # Optum sweep taught on a free board. The title gates do the narrowing.
    "role_keywords": [""],
    "locations": ["India"],
    "country": "IN",
    # None on purpose: f_E would clamp the search to one LinkedIn seniority
    # bucket ("2 years" maps to Associate), which drops both the graduate roles
    # and the mid-level ones worth stretching for. Experience is read from the
    # JD text instead, by scraper._required_experience_floor.
    "experience_years": None,
    "salary_min": None,
    # MEASURED 2026-08-16, and the reason this is not 800: at count=800 the actor
    # completed for Microsoft (140 jobs) and IBM (495) but the Deloitte and
    # Siemens runs hit TIMED-OUT. A timed-out run still bills for the compute and
    # returns nothing, which is the worst possible trade — those two runs are
    # most of why the account moved $3.88 while the actor self-reported $1.21.
    # Keep this small: the cost is per result, so a low count loses tail rows,
    # whereas a high one risks losing the whole run.
    "max_results": 120,
}

SITES = {
    "indeed": {"enabled": False},   # 5x the cost per result, skews domestic
    "naukri": {"enabled": False},   # $0.50 MINIMUM per run — awful for narrow searches
    "linkedin": {
        "enabled": True,
        "actor": "curious_coder/linkedin-jobs-scraper",
        "locations": ["India"],
        "companies": ["Microsoft", "IBM", "Deloitte", "Siemens"],
        "remote_geo": "India",
        "remote_only": False,
    },
}

SETTINGS = {
    "remote_scopes": [],          # India onsite/hybrid is the target
    "drop_excluded": False,       # penalize seniority/over-experience, never delete
    "max_experience_years": 3,
    "experience_aggregate": "max",
    "min_comp_usd": None,
    "min_score": 5,
    "max_age_days": 30,           # also becomes LinkedIn's f_TPR window
    "drop_undated": False,
    "top_n_console": 40,
    # Hard stop well inside the $5 credit. Read from real account spend, not the
    # actor's self-report — scraper.account_usage_usd documents why the
    # self-report undercounts by ~3x.
    "max_spend_usd": 4.80,
    "confirm_above_runs": 99,     # the plan is 4 runs; don't prompt
}
