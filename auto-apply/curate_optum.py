"""Curate an Optum sweep down to the openings actually worth an application.

The scraper ranks by résumé overlap. That is necessary and not sufficient: a
requisition can match the stack perfectly and still ask for 8 years, and a
30-day-old req at a company this size is usually deep in applicants. So this
re-reads each JD and keeps only rows that clear three bars at once:

    fresh        posted within --days (default 14)
    reachable    the years the JD demands is <= --max-years (default 4; 3 is the
                 résumé, +1 because a referral makes one year of stretch sane)
    relevant     score >= --min-score

Prints the survivors with the requisition number, because that is what a referral
is submitted against. Reads the ranked CSV a sweep already wrote — no re-scrape.

    python auto-apply/curate_optum.py output/optum/jobs_combined.csv
"""
import csv
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper
from sources import optum

# Importing scraper loads the DEFAULT profile's config, not profiles/optum.py, so
# this has to be stated: Optum JDs give a total AND a per-skill figure, and under
# the default "min" an "8+ years of full stack engineering experience" lead role
# reported as 2 years and sailed through the reachable check. Set explicitly
# rather than relying on JOB_PROFILE being exported.
scraper.SETTINGS["experience_aggregate"] = "max"


def curate(csv_path, days=14, max_years=4, min_score=10):
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    today = datetime.date.today()
    kept, dropped = [], {"stale": 0, "over_experience": 0, "low_score": 0}

    for row in rows:
        try:
            score = int(float(row.get("score") or 0))
        except ValueError:
            score = 0
        if score < min_score:
            dropped["low_score"] += 1
            continue

        posted = scraper._parse_date(row.get("date_posted"))
        age = (today - posted.date()).days if posted else None
        if age is not None and age > days:
            dropped["stale"] += 1
            continue

        # The JD is not in the CSV (descriptions aren't an output column), so the
        # experience ask has to come from a re-read. One request per candidate,
        # and only for rows that already cleared score + freshness.
        info = optum.detail(row["apply_url"])
        if not info.get("live"):
            dropped["stale"] += 1
            continue
        floor = scraper._required_experience_floor(info["description"].lower())
        if floor is not None and floor > max_years:
            dropped["over_experience"] += 1
            continue

        row["_age"] = age
        row["_years"] = floor
        kept.append(row)

    kept.sort(key=lambda r: (-int(float(r["score"])), r["_age"] if r["_age"] is not None else 99))
    return kept, dropped


def main():
    global opts
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    path = args[0] if args else "output/optum/jobs_combined.csv"
    kept, dropped = curate(path,
                           days=int(opts.get("days", 14)),
                           max_years=int(opts.get("max-years", 4)),
                           min_score=int(opts.get("min-score", 10)))
    print(f"\n{len(kept)} worth applying to "
          f"(dropped: {dropped['low_score']} off-profile, {dropped['stale']} stale/pulled, "
          f"{dropped['over_experience']} want more years than we have)\n")
    print(f"{'REQ':<9} {'SCORE':>5} {'AGE':>4} {'ASKS':>5}  {'LOCATION':<22} TITLE")
    for r in kept:
        years = f"{r['_years']}y" if r["_years"] is not None else "—"
        age = f"{r['_age']}d" if r["_age"] is not None else "—"
        print(f"{r['req_number']:<9} {r['score']:>5} {age:>4} {years:>5}  "
              f"{r['location'][:22]:<22} {r['title'][:52]}")

    # --out writes the survivors back out in the same schema, so the existing
    # shortlist page can render them without knowing anything about this filter.
    if opts.get("out") and kept:
        cols = [c for c in kept[0] if not c.startswith("_")]
        with open(opts["out"], "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows({c: r.get(c, "") for c in cols} for r in kept)
        print(f"\n-> {opts['out']}")
    return kept


if __name__ == "__main__":
    main()
