"""Optum only — a referral-driven sweep of one employer's careers site.

    python scraper.py --profile optum --site optum      # -> output/optum/

Every other source is switched OFF ({} clears a section, see config._overlay),
so this run costs nothing and touches nothing but careers.unitedhealthgroup.com.

Filter choices differ from the international-remote profiles on purpose, because
a referral changes what "reachable" means:

  remote_scopes = []   OFF. The default ["worldwide", "remote"] exists to throw
      out roles that are geo-locked away from India. Here the target IS Optum
      India (Noida / Gurgaon / Hyderabad / Chennai / Bengaluru / Pune), which is
      onsite or hybrid — leaving the filter on drops precisely the jobs a
      referral makes reachable.

  drop_excluded = False   Nothing is silently deleted; over-senior titles and
      over-experience demands take drop_penalty and sink instead. With a
      referral a "3-5 years" req is a stretch worth seeing, not a wall, so the
      decision belongs to the reader — the score already ranks it last.

  min_comp_usd = None   Optum's India requisitions never disclose pay, and this
      is a single-employer run, so there is nothing to compare against.
"""

# Paid actors, other company boards, and public feeds: all off.
SITES = {}
ATS_BOARDS = {}
FEEDS = {}

OPTUM = {"enabled": True}

# India, because that is where this candidate can work without sponsorship.
# A job whose location is blank is always kept (see scraper.location_allowed).
LOCATION_HINTS = [
    "india", "delhi", "ncr", "gurgaon", "gurugram", "noida", "bengaluru",
    "bangalore", "hyderabad", "chennai", "pune", "mumbai", "kolkata",
    "ahmedabad", "coimbatore", "thiruvananthapuram", "trivandrum", "mohali",
]

SETTINGS = {
    "remote_scopes": [],          # see docstring — must be off for an India sweep
    "drop_excluded": False,       # penalize seniority/experience, never delete
    "max_experience_years": 3,
    "min_comp_usd": None,
    "min_score": None,
    "max_age_days": 30,           # liveness is verified per JD; this bounds staleness
    "drop_undated": False,
    "top_n_console": 25,
}
