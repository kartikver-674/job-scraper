"""Parul — everything workable WITHOUT a visa: onsite/hybrid India + real remote.

    python scraper.py --profile parul_reachable --dry-run   # cost check, free
    python scraper.py --profile parul_reachable --yes

Same shape as profiles/kartik_reachable.py — see that file for the measurements
behind the paid/free split — retuned to the Salesforce functional-consultant
model in config.py. Only the candidate-specific keys are overridden here, so
ATS_TITLE_HINTS, SCORING and FEEDS all inherit config.py's Salesforce tuning.

WHY THE PAID MONEY GOES TO INDIA AND NOT TO "GLOBAL REMOTE"
LinkedIn has no worldwide-remote search. f_WT=2 filters workplace type WITHIN a
geography, so a "remote" search of Germany returns roles remote *within Germany*
— geo-locked away from India and indistinguishable from the real thing until you
read the JD. Measured on the 2026-07-26 sweep: 480 rows at score >= 10, of which
27 were actually reachable from India. Paying LinkedIn for foreign remote buys
mostly unreachable rows, which is exactly what this profile refuses to do.

So the split that answers "India or remote, no visa":
  - PAID (LinkedIn, below): India at CITY level, plus India-remote. The
    nationwide "India" geoId returns only the top 25 per keyword for the whole
    country, so the city searches are additional inventory, not duplicates.
  - FREE (the 5 feeds — RemoteOK, WWR, Remotive, Jobicy, Himalayas): genuine
    worldwide remote. Built for exactly that, cost nothing, and carry far more
    of it than LinkedIn does. They run alongside automatically.

Chandigarh leads the city list — it is home, and its geoId was only verified
into config.py recently (100139308, 8/10 cards in the tricity; six of them come
back labelled by district as "Sahibzada Ajit Singh Nagar" or "SAS Nagar", both
Mohali). auto-apply/linkedin_shortlist.py's _INDIA regex already knows both
spellings, so those rows bucket as India and not as abroad.

No Noida or New Delhi: both were verified and REMOVED from the LinkedIn list —
Noida returns no job cards at all and "New Delhi" 106164932 returns Inner
Mongolia, CHINA. Delhi + Gurgaon cover NCR. (They stay in config.SEARCH
["locations"], which is Indeed's name-based list, not a geoId.)

remote_scopes MUST stay empty. An onsite Chandigarh row classifies as "onsite"
or "", so any non-empty list deletes the onsite-and-hybrid-India half this
profile exists to buy — see scraper.finalize, which skips the filter entirely
when the list is empty. The onsite-ABROAD rows that slip in (a "Remote" search
can return a foreign row) are labelled and sunk at RENDER time by
linkedin_shortlist.bucket(), not deleted here, so they stay in the CSV if you
ever want to look at what you chose not to apply to.
"""

SITES = {
    "linkedin": {"enabled": True, "actor": "curious_coder/linkedin-jobs-scraper",
                 "locations": ["India", "Chandigarh", "Delhi", "Gurgaon",
                               "Bengaluru", "Hyderabad", "Pune", "Mumbai",
                               "Remote"],
                 # "Remote" means India-remote: f_WT=2 applied to the India geo.
                 # That IS the reachable remote inventory on this platform.
                 "remote_geo": "India",
                 "remote_only": False},
    "indeed": {"enabled": False},   # ~$0.075/search here, and no geoId advantage
    "naukri": {"enabled": False},   # ~$0.50/run MINIMUM — see docstring note
}
OPTUM = {"enabled": False}
ENTERPRISE = {"enabled": False}

SEARCH = {
    # config.py's five verified Salesforce titles, unchanged. A bare "Business
    # Analyst" or "Consultant" here would buy nine locations' worth of
    # adjacent-domain roles at full price.
    "role_keywords": [
        "Salesforce Functional Consultant",
        "Salesforce Business Analyst",
        "Salesforce Consultant",
        "Salesforce Administrator",
        "Salesforce Implementation Consultant",
    ],
    "max_results": 25,      # the $0.046/search rate was measured at 25
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
    # None, not config.py's 9200. That floor reads the TOP of a range, so it is
    # nearly inert on the aggregators — but onsite Chandigarh roles are the ones
    # most likely to disclose a real number and be under 8 LPA, and this sweep is
    # about seeing what is reachable, not pre-filtering it on pay. Sort on the
    # salary column instead.
    "min_comp_usd": None,
    "min_score": None,      # filter at render time with --min, which is reversible
    # 5 keywords x 9 locations = 45 searches ~= $2.07 at the measured rate.
    # The cap reads REAL billing (the actor self-report undercounts ~3x) and
    # refuses to launch a new search once crossed.
    "max_spend_usd": 3.00,
    "confirm_above_runs": 50,
    "top_n_console": 40,
    # output_dir left unset: config.py auto-scopes it to output/parul_reachable/.
}
