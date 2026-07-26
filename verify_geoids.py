"""Verify config.LINKEDIN_GEO_IDS against LinkedIn's public guest search.

Why this exists: LinkedIn IGNORES a free-text location and silently returns US
results, so a wrong or missing geoId doesn't fail — it bills you full price for
the wrong country. At ~$0.001/result across 20 countries that is a real amount of
money spent on data you'd have to throw away.

The guest job-search endpoint needs no auth and returns job cards with their
locations, so a geoId can be checked by asking for jobs and looking at where they
are. Free, and it takes a couple of seconds per entry.

    python verify_geoids.py               # check every entry in the config
    python verify_geoids.py 103644278     # check one raw id
    python verify_geoids.py Germany       # check one configured name
"""
import re
import sys
import time
import urllib.parse

from config import LINKEDIN_GEO_IDS
from sources._http import get_bytes

SEARCH = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/"
          "search?keywords=software%20engineer&geoId={}&start=0")
_LOC_RE = re.compile(r'job-search-card__location">\s*([^<]+?)\s*<', re.S)
# US cards render as "San Francisco, CA" with no country word, so the country
# name alone can't confirm them.
_US_RE = re.compile(
    r",\s*(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]"
    r"|O[HKR]|PA|RI|S[CD]|TN|TX|UT|V[TA]|W[AVIY])\b|United States", re.I)
# Places LinkedIn labels differently from how we search for them. Without these
# a correct geoId reads as a MISMATCH purely on spelling.
_ALIASES = {"gurgaon": "gurugram", "bengaluru": "bangalore",
            "bangalore": "bengaluru", "mumbai": "bombay",
            "new delhi": "delhi", "uae": "united arab emirates"}


def locations_for(geo_id):
    """Job-card locations LinkedIn returns for a geoId."""
    html = get_bytes(SEARCH.format(urllib.parse.quote(str(geo_id)))).decode("utf-8", "replace")
    return _LOC_RE.findall(html)


def check(name, geo_id):
    """(name, geo_id, verdict, detail). Verdict: ok / MISMATCH / empty / error."""
    try:
        locs = locations_for(geo_id)
    except Exception as exc:
        return name, geo_id, "error", f"{type(exc).__name__}: {exc}"
    if not locs:
        return name, geo_id, "empty", "no job cards returned"
    low = name.lower()
    if "united states" in low:
        hits = sum(1 for l in locs if _US_RE.search(l))
    else:
        # Match on the distinctive last word ("United Kingdom" -> "kingdom") so
        # city-level cards like "Manchester, England, United Kingdom" still count.
        wanted = {name.split()[-1].lower()}
        if low in _ALIASES:
            wanted.add(_ALIASES[low])
        hits = sum(1 for l in locs if any(w in l.lower() for w in wanted))
    verdict = "ok" if hits >= len(locs) * 0.6 else "MISMATCH"
    return name, geo_id, verdict, f"{hits}/{len(locs)} · e.g. {locs[0][:38]}"


def main():
    targets = dict(LINKEDIN_GEO_IDS)
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        targets = ({arg: LINKEDIN_GEO_IDS[arg]} if arg in LINKEDIN_GEO_IDS
                   else {f"(raw {arg})": arg})
    # Sequential with a pause: the guest endpoint 429s on ~8 parallel requests,
    # and a rate-limited check reports "error" for a geoId that is perfectly fine,
    # which is worse than being slow.
    bad = 0
    for i, (name, geo_id) in enumerate(targets.items()):
        if i:
            time.sleep(1.5)
        name, geo_id, verdict, detail = check(name, geo_id)
        bad += verdict != "ok"
        print(f"  {verdict:<9} {name:<24} {geo_id:<12} {detail}", flush=True)
    print(f"\n{len(targets) - bad}/{len(targets)} verified")
    if bad:
        print("A MISMATCH means LinkedIn returns jobs from somewhere else for that id —\n"
              "fix or remove it before spending on a sweep that uses it.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
