"""Optum India tech openings in a grade band, posted within N days, scored.

    python auto-apply/optum_grade_scan.py --grades=24,25 --days=5

Why this exists rather than a full `--profile optum` sweep: the question is
"what is NEW and in my band", and the search index is sorted newest-first, so a
few listing pages answer it instead of paging all ~5,500 cards. A full sweep is
still the right tool for "everything Optum has"; this one is the standing query.

GRADE is the point. UHG's internal pay grade is the only field stating a
requisition's level as the employer means it, and it is not rendered on the page
— sources/optum.py reads it out of an embedded JSON blob. The visible title
cannot substitute: measured 2026-08-04, "Associate Software Engineer II" and
"Full Stack Engineer" are BOTH grade 26, so a title-based guess at eligibility is
wrong in both directions.

Scoring, the résumé gates, and the experience read all come from the optum
profile, so this agrees with the sweep instead of being a second opinion.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JOB_PROFILE", "optum")     # must precede the config import

import scraper                                     # noqa: E402
from sources import optum                          # noqa: E402
from sources._http import get_json                 # noqa: E402

# Optum JDs state a total AND a per-skill figure; under the default "min" an
# "8+ years ... including 2+ years hands-on" role reads as 2. See
# scraper._required_experience_floor.
scraper.SETTINGS["experience_aggregate"] = "max"


def scan(grades, days, max_pages=20, log=print):
    """-> (in_band, all_fresh). Both are row dicts; in_band is the answer."""
    today = datetime.date.today()
    cards = {}
    for page in range(1, max_pages + 1):
        url = optum._SEARCH.format(page=page, per_page=100, kw="", loc="")
        html = (get_json(url) or {}).get("results") or ""
        got = optum._cards(html, "optum")
        if not got:
            break
        for c in got:
            cards.setdefault(c["job_id"], c)
    log(f"{len(cards)} newest Optum cards")

    tech = [c for c in cards.values()
            if scraper.is_dev_title(c["title"]) and scraper.location_allowed(c["location"])]
    log(f"{len(tech)} tech-titled in India — reading each JD for grade + date")

    fresh = []
    for i, c in enumerate(tech, 1):
        try:
            d = optum.detail(c["url"])
        except Exception as exc:
            log(f"  ! {c['title'][:40]}: {exc}")
            continue
        if not d["live"]:
            continue                       # requisition pulled — not hiring
        posted = scraper._parse_date(d["date_posted"])
        age = (today - posted.date()).days if posted else None
        if age is None or age > days:
            continue
        row = dict(optum.BLANK, Title=c["title"], Company="Optum",
                   Location=c["location"], Description=d["description"])
        row["Posted Date"] = d["date_posted"]
        row["Job URL"] = c["url"]
        scored = scraper.score_job(row)
        fresh.append({
            "req": d["req"] or c["req"], "grade": d["grade"], "age": age,
            "date": d["date_posted"], "title": c["title"], "loc": c["location"],
            "url": c["url"], "score": scored["score"] if scored else None,
            "years": scraper._required_experience_floor(d["description"].lower()),
        })
        if i % 25 == 0:
            log(f"  ...{i}/{len(tech)}")

    in_band = [r for r in fresh if r["grade"] in grades]
    in_band.sort(key=lambda r: -(r["score"] if r["score"] is not None else -99))
    return in_band, fresh


def main():
    opts = dict(a[2:].split("=", 1) for a in sys.argv[1:]
                if a.startswith("--") and "=" in a)
    grades = set(opts.get("grades", "24,25").split(","))
    days = int(opts.get("days", 5))

    in_band, fresh = scan(grades, days)

    from collections import Counter
    spread = dict(sorted(Counter(r["grade"] or "?" for r in fresh).items()))
    print(f"\n{len(fresh)} tech reqs posted in the last {days} days")
    print(f"grade spread: {spread}")
    print(f"\n=== GRADE {'/'.join(sorted(grades))}, last {days} days: {len(in_band)} ===")
    if not in_band:
        print("(none — nothing in the band was posted in this window)")
    for r in in_band:
        years = f"{r['years']}y" if r["years"] else "—"
        print(f"\nREQ {r['req']}  grade {r['grade']}  score {r['score']}  "
              f"asks {years}  posted {r['date']} ({r['age']}d)")
        print(f"  {r['title']} — {r['loc']}")
        print(f"  {r['url']}")
    return in_band


if __name__ == "__main__":
    main()
