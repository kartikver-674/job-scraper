"""Paid LinkedIn sweep for Kanav — India onsite/hybrid/remote, last 30 days.

    python scraper.py --profile kanav_india --dry-run   # cost check, spends nothing
    python scraper.py --profile kanav_india --yes

LinkedIn only, on purpose. It is the cheapest per result of the three paid
actors (~$0.046 per search of 25 rows, measured 2026-08-25) and the only one
whose India geography is verified. Indeed bills ~$0.09 a run and Naukri has a
~$0.50 MINIMUM per run, so at the credit actually on hand — $2.50 on the fullest
key — either one would buy a fraction of the coverage for the same money.

remote_scopes MUST stay empty here. It is ["worldwide", "remote"] in config.py,
which is right for an international-remote search and fatal for this one: an
onsite Gurgaon row classifies as "onsite" or "", so any non-empty list deletes
the India onsite and hybrid inventory that is the whole point. Same trap as
profiles/global_all.py documents.

No Chandigarh or Mohali entry, even though that is where he works. There is no
verified geoId for either, five plausible candidates all resolved somewhere else
(Galway, Glasgow, Ahmedabad, Gurugram), and a wrong geoId does not fail — it
returns US results and bills in full. The plain "India" geoId already surfaces
that belt on its own; LinkedIn writes Mohali as "Sahibzada Ajit Singh Nagar,
Punjab, India" and config.HOME_LOCATION_HINTS matches it.

Everything not restated is inherited from config.py, so the whole résumé model
comes along: the 6-year experience ceiling, Senior/Lead left unpenalised, the
React Native New Architecture weights, and CRM scored positive rather than
penalised.
"""

SITES = {
    "linkedin": {"enabled": True, "actor": "curious_coder/linkedin-jobs-scraper",
                 # India nationwide, then three city searches for depth. LinkedIn
                 # caps each search at max_results, so a nationwide search returns
                 # the top 25 in the country while a Delhi one returns the top 25
                 # in Delhi — the city rows are genuinely additional inventory,
                 # not duplicates. All four geoIds verified via verify_geoids.py.
                 "locations": ["India", "Delhi", "Gurgaon", "Bengaluru", "Remote"],
                 # What a bare "Remote" means. f_WT=2 filters workplace type
                 # WITHIN a geography — there is no worldwide-remote search — so
                 # "Remote" above is India-remote, which is what he can take.
                 "remote_geo": "India",
                 "remote_only": False},
    "indeed": {"enabled": False},
    "naukri": {"enabled": False},
}
OPTUM = {"enabled": False}
ENTERPRISE = {"enabled": False}

SEARCH = {
    # Eight, down from the twelve in config.py. Paid cost is linear in this list,
    # so the four dropped ones were the near-synonyms whose results overlap
    # almost completely with a term already here ("React Native Engineer",
    # "React.js Developer", "Frontend Engineer", "Software Engineer JavaScript").
    # What is left covers all three résumé variants: three mobile, two frontend,
    # three full-stack/backend.
    "role_keywords": [
        "React Native Developer",
        "Mobile Application Developer",
        "React Developer",
        "Frontend Developer",
        "UI Developer",
        "Full Stack Developer",
        "MERN Stack Developer",
        "Node.js Developer",
    ],
    "max_results": 25,      # the rate above was measured at 25/search
    "experience_years": 4,
    "country": "IN",
    "salary_min": None,
}

SETTINGS = {
    # See docstring — non-empty here deletes every onsite and hybrid row.
    "remote_scopes": [],
    "max_age_days": 30,
    "drop_undated": False,
    "drop_excluded": True,
    "max_experience_years": 6,
    # Indian JDs are the long structured kind that state a total AND a per-skill
    # figure, so the largest number is the real ask. "min" would read "2+ years
    # React" out of a posting whose header says 7-10 and call it a match.
    "experience_aggregate": "max",
    "min_comp_usd": None,   # Indian postings rarely disclose; never drop on a guess
    "min_score": None,      # filter at render time with --min, which is reversible
    # 8 keywords x 5 locations = 40 searches ~= $1.84. The cap is the real guard:
    # it measures actual billing (not the actor's self-report, which undercounts
    # ~3x) and refuses to LAUNCH a new search once spend crosses it, so the
    # sweep stops itself well inside the $2.50 on the fullest key.
    "max_spend_usd": 2.00,
    "confirm_above_runs": 60,
    "top_n_console": 40,
    # output_dir deliberately NOT set: config.py:781 auto-scopes it to
    # output/kanav_india/ when a profile stays quiet, and naming it explicitly
    # would dump his rows into the shared output/ root alongside everyone
    # else's — which is also what harvest_ats scopes its company list by.
}
