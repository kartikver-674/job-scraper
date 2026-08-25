"""Paid GLOBAL sweep, EVERY workplace type — onsite, hybrid and remote.

    python scraper.py --profile global_all --dry-run   # cost check, spends nothing
    python scraper.py --profile global_all --yes

The counterpart to profiles/global_remote.py, which puts f_WT=2 on every search
and therefore buys remote inventory only. This one drops that filter, so each
search returns whatever the market actually has in that country. Same number of
searches, same cost — the filter was never what made it cheap.

That means remote_scopes MUST be empty. global_remote keeps
["worldwide", "remote", "restricted"], which is right when every row is remote by
construction and fatal here: an onsite row classifies as "onsite" or "", so that
list would delete the entire half of the sweep this profile exists to buy.

Everything not restated is inherited from config.py, so the résumé scoring model
comes along unchanged — including the Salesforce/Apex/LWC penalties, which are
deliberate (that work is the thing being left, not the thing being sought).

Cost, measured rather than assumed: ~$0.046 per search of 25 results (a
48-search sweep billed $2.20 on 2026-08-25). 8 keywords x 10 countries = 80
searches ~= $3.68, under the $4.20 cap below and inside one free account's $5.
scraper._require_token() picks whichever configured key has the most credit, but
it does NOT switch accounts mid-sweep — so the plan has to fit in one wallet.

Every geoId here was verified with `python verify_geoids.py`. A wrong one does
not fail; LinkedIn returns US results and bills in full.
"""

# Ten, chosen for what is actually reachable at ~2 years rather than for breadth:
#   India                  home market, onsite and remote both real
#   UAE, Singapore         hire Indian engineers onsite routinely, and IST-adjacent
#   Australia              English-language, +4.5h, active sponsorship route
#   UK, Ireland            English-language, 5.5h, strong remote-from-anywhere
#   Germany, Netherlands   EU Blue Card is genuinely open at this level, and
#                          engineering there runs in English
#   US, Canada             onsite needs sponsorship he won't get yet, but their
#                          REMOTE inventory is the largest anywhere and arrives in
#                          the same searches at no extra cost
_COUNTRIES = [
    "India", "United Arab Emirates", "Singapore", "Australia",
    "United Kingdom", "Ireland", "Germany", "Netherlands",
    "United States", "Canada",
]

SEARCH = {
    # Eight. The paid cost is linear in this list, so each one has to earn its
    # place: five name his stack directly, "Software Engineer" is the
    # highest-volume generic term and lets the scoring layer do the sorting, and
    # the last two chase the AI work the résumé does not yet spell out.
    "role_keywords": [
        "Full Stack Developer",
        "Full Stack Engineer",
        "React Native Developer",
        "Node.js Developer",
        "MERN Stack Developer",
        "Software Engineer",
        "AI Engineer",
        "Backend Engineer Node.js",
    ],
    "locations": _COUNTRIES,
    "max_results": 25,          # per keyword x country — the main cost dial
}

SITES = {
    "linkedin": {"enabled": True, "actor": "curious_coder/linkedin-jobs-scraper",
                 "locations": _COUNTRIES,
                 # FALSE — the whole point of this profile. No f_WT filter, so
                 # onsite, hybrid and remote all come back.
                 "remote_only": False,
                 # Unused: no bare "Remote" location in the list above.
                 "remote_geo": None},
    # Off: ~5x LinkedIn's cost per result. Worth turning on for a single-country
    # deep dive, never for a ten-country sweep.
    "indeed": {"enabled": False, "actor": "misceres/indeed-scraper"},
    # India-only with a ~$0.50 floor per run; the India rows here come from
    # LinkedIn at a fraction of that.
    "naukri": {"enabled": False, "actor": "muhammetakkurtt/naukri-job-scraper"},
}

SETTINGS = {
    # EMPTY, and this is the line that makes the profile work. See the docstring:
    # an onsite row's scope is "onsite" or "", so any non-empty list here deletes
    # exactly what this sweep is for. The remote_scope, remote_regions and tz_gap
    # columns still get populated — read them per row instead of filtering.
    "remote_scopes": [],
    # 30 days, and scraper.build_input maps this onto LinkedIn's f_TPR, so the
    # window is enforced at the SOURCE. Without it the credit buys year-old
    # postings that the local filter then throws away.
    "max_age_days": 30,
    "top_n_console": 30,

    # Hard stop above the ~$3.68 estimate but inside one account's $5 free tier.
    "max_spend_usd": 4.20,
    # 80 runs is far past the default prompt threshold; keep the confirmation
    # rather than discovering the bill afterwards.
    "confirm_above_runs": 12,
}
