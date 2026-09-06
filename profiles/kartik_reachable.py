"""Kartik — everything workable WITHOUT a visa: onsite/hybrid India + real remote.

    python scraper.py --profile kartik_reachable --dry-run   # cost check, free
    python scraper.py --profile kartik_reachable --yes

The counterpart to profiles/global_all.py, which buys ten countries' onsite
inventory. Two thirds of that sweep's output (114 of 170 rows on 2026-08-25,
82 of 110 on 2026-09-01) was onsite abroad and needs sponsorship, so this
profile stops paying for it.

WHY THE PAID MONEY GOES TO INDIA AND NOT TO "GLOBAL REMOTE"
LinkedIn has no worldwide-remote search. f_WT=2 filters workplace type WITHIN a
geography, so a "remote" search of Germany returns roles that are remote *within
Germany* — geo-locked away from India, and indistinguishable from the real thing
until you read the JD. Measured on the 2026-07-26 sweep: 480 rows at score >= 10,
of which 27 were actually reachable from India, and 245 of the top 252 were
remote-only-within Germany / Spain / UAE / the UK. Paying LinkedIn for foreign
remote buys mostly unreachable rows.

So the split is:
  - PAID (this profile): India, at CITY level. The nationwide "India" geoId
    returns only the top 25 per keyword for the whole country, which is why
    NCR roles kept getting crowded out; the city searches are additional
    inventory, not duplicates.
  - FREE (the feeds — RemoteOK, WWR, Remotive, Jobicy, Himalayas): genuine
    worldwide remote. They are built for exactly that, they cost nothing, and
    they carry far more of it than LinkedIn does. Run them with --site free.

remote_scopes MUST stay empty. An onsite Gurgaon row classifies as "onsite" or
"", so any non-empty list deletes the half this profile exists to buy. The
onsite-abroad rows that do slip in (a "Remote" search can return a foreign row)
are dropped at RENDER time by bucket(), not here, so they stay visible in the
CSV if you ever want to look.

No Noida or New Delhi geoId: both were verified and removed — Noida returns no
job cards at all, "New Delhi" 106164932 returns Inner Mongolia, CHINA. Delhi +
Gurgaon cover NCR. Every id below is verified (python verify_geoids.py).
"""

SITES = {
    "linkedin": {"enabled": True, "actor": "curious_coder/linkedin-jobs-scraper",
                 "locations": ["India", "Delhi", "Gurgaon", "Bengaluru",
                               "Hyderabad", "Pune", "Mumbai", "Remote"],
                 # "Remote" means India-remote: f_WT=2 applied to the India geo.
                 # That IS the reachable remote inventory on this platform.
                 "remote_geo": "India",
                 "remote_only": False},
    "indeed": {"enabled": False},   # ~$0.09/run, and no geoId advantage here
    "naukri": {"enabled": False},   # ~$0.50/run MINIMUM — bad value at this budget
}
OPTUM = {"enabled": False}
ENTERPRISE = {"enabled": False}

SEARCH = {
    "role_keywords": [
        "Full Stack Developer",
        "Full Stack Engineer",
        "React Native Developer",
        "MERN Stack Developer",
        "Node.js Developer",
        "Backend Engineer Node.js",
        "Software Engineer",
        "AI Engineer",
    ],
    "max_results": 25,      # the $0.045/search rate was measured at 25
    "experience_years": 2,
    "country": "IN",
    "salary_min": None,
}

SETTINGS = {
    # See docstring — non-empty deletes every onsite and hybrid India row.
    "remote_scopes": [],
    "max_age_days": 30,
    "drop_undated": False,
    # Indian JDs are the long structured kind that state a total AND a per-skill
    # figure, so the largest number is the real ask.
    "experience_aggregate": "max",
    "min_comp_usd": None,   # Indian postings rarely disclose; never drop on a guess
    "min_score": None,      # filter at render time with --min, which is reversible
    # 8 keywords x 8 locations = 64 searches ~= $2.94 at the measured rate.
    # The cap reads REAL billing (the actor self-report undercounts ~3x) and
    # refuses to launch a new search once crossed.
    "max_spend_usd": 4.00,
    "confirm_above_runs": 70,
    "top_n_console": 40,
    # output_dir left unset: config.py:781 auto-scopes it to
    # output/kartik_reachable/.
}
