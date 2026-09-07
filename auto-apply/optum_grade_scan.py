"""Optum India tech openings in a grade band, posted within N days, scored.

    python auto-apply/optum_grade_scan.py --grades=24,25 --days=5
    python auto-apply/optum_grade_scan.py --demo          # offline self-check

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

THE WATCHLIST, and why it is not optional. "What is new" is only half the
question, and for three days running it was the wrong half: this script reported
"nothing new, act on the ones you have" while all four previously-reported
requisitions were quietly 404ing. A req that closes is the single most
actionable event in a scarce band — it is the difference between a live
shortlist and a stale one — and nothing in a newest-first scan can surface it,
because a pulled req simply stops appearing and absence reads as "not new".

So every in-band req this script reports is written to a ledger, and every open
ledger entry is re-checked (one request each) before the fresh scan runs. Cheap:
a handful of requests against the ~70 the scan already spends.

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

WATCHLIST = os.path.join("output", "optum", "watchlist.tsv")
COLUMNS = ("req", "grade", "score", "years", "posted", "title", "loc", "url",
           "first_seen", "last_checked", "status")


# ---------------------------------------------------------------- ledger
def load_watchlist(path=WATCHLIST):
    """-> {req: row}. A missing or empty ledger is a first run, not an error."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
    except FileNotFoundError:
        return {}
    if not lines:
        return {}
    head = lines[0].split("\t")
    out = {}
    for line in lines[1:]:
        row = dict(zip(head, line.split("\t")))
        if row.get("req"):
            out[row["req"]] = row
    return out


def save_watchlist(watch, path=WATCHLIST):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\t".join(COLUMNS) + "\n")
        for row in watch.values():
            # Tabs in a title would corrupt the row; strip rather than quote,
            # since this is a ledger and not a data interchange format.
            fh.write("\t".join(
                str(row.get(c, "")).replace("\t", " ").replace("\n", " ")
                for c in COLUMNS) + "\n")


def recheck(watch, today, log=print, detail=None):
    """Re-read every open entry's JD. -> (closed, still_open) as row lists.

    A 404 means the requisition is gone from the site, which is the strongest
    public signal it stopped accepting applications (see the sources/optum.py
    note on why the Taleo apply URL cannot be used for this).
    """
    detail = detail or optum.detail
    closed, open_ = [], []
    for row in watch.values():
        if row.get("status") != "open":
            continue
        try:
            info = detail(row["url"])
        except Exception as exc:                  # a transient error is NOT a close
            log(f"  ! recheck {row['req']}: {type(exc).__name__} {exc}")
            open_.append(row)
            continue
        row["last_checked"] = today
        if info.get("live"):
            open_.append(row)
        else:
            row["status"] = "closed"
            row["closed_on"] = today
            closed.append(row)
    return closed, open_


# ---------------------------------------------------------------- scan
def scan(grades, days, max_pages=20, log=print):
    """-> (in_band, all_fresh). Both are row dicts; in_band is the answer."""
    today = datetime.date.today()
    cards = {}
    for page in range(1, max_pages + 1):
        url = optum._SEARCH.format(page=page, per_page=100, kw="", loc="")
        # This host throttles progressively across repeated runs, and a single
        # timed-out listing page used to raise straight out of scan() and kill
        # the whole run — including a completed watchlist re-check. A partial
        # newest-first sweep is still a useful answer; no answer is not.
        try:
            html = (get_json(url) or {}).get("results") or ""
        except Exception as exc:
            log(f"  ! listing page {page}: {type(exc).__name__} — "
                f"continuing with the {len(cards)} cards already read")
            break
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


def _fmt(r):
    years = f"{r['years']}y" if r.get("years") else "—"
    return (f"REQ {r['req']}  grade {r['grade']}  score {r['score']}  "
            f"asks {years}  posted {r['date']} ({r['age']}d)\n"
            f"  {r['title']} — {r['loc']}\n  {r['url']}")


def main():
    opts = dict(a[2:].split("=", 1) for a in sys.argv[1:]
                if a.startswith("--") and "=" in a)
    grades = set(opts.get("grades", "24,25").split(","))
    days = int(opts.get("days", 5))
    path = opts.get("watchlist", WATCHLIST)
    today = datetime.date.today().isoformat()

    # 1. What CLOSED since last time — the half a newest-first scan cannot see.
    watch = load_watchlist(path)
    closed, still_open = [], []
    if watch:
        print(f"re-checking {sum(1 for r in watch.values() if r.get('status') == 'open')} "
              f"previously reported reqs")
        closed, still_open = recheck(watch, today)
        # Save NOW. Closure is the expensive half of this script's answer, and
        # writing it only at the end meant a later listing timeout threw it away.
        save_watchlist(watch, path)

    in_band, fresh = scan(grades, days)

    from collections import Counter
    spread = dict(sorted(Counter(r["grade"] or "?" for r in fresh).items()))
    print(f"\n{len(fresh)} tech reqs posted in the last {days} days")
    print(f"grade spread: {spread}")

    if closed:
        print(f"\n=== CLOSED since last run: {len(closed)} ===")
        for r in closed:
            print(f"REQ {r['req']}  grade {r['grade']}  (reported {r['first_seen']})"
                  f"\n  {r['title']} — {r['loc']}")
    if still_open:
        print(f"\n=== STILL OPEN from earlier runs: {len(still_open)} ===")
        for r in sorted(still_open, key=lambda r: -float(r.get("score") or -99)):
            print(f"REQ {r['req']}  grade {r['grade']}  score {r['score']}  "
                  f"asks {r['years'] or '—'}  first seen {r['first_seen']}"
                  f"\n  {r['title']} — {r['loc']}\n  {r['url']}")

    known = set(watch)
    new = [r for r in in_band if r["req"] not in known]
    print(f"\n=== GRADE {'/'.join(sorted(grades))}, last {days} days: "
          f"{len(in_band)} in band, {len(new)} new ===")
    if not in_band:
        print("(none — nothing in the band was posted in this window)")
    for r in in_band:
        print(("\n[NEW] " if r["req"] in {x["req"] for x in new} else "\n") + _fmt(r))

    # 2. Record this run's in-band reqs so the NEXT run can report their closure.
    for r in in_band:
        entry = watch.get(r["req"]) or {"first_seen": today}
        entry.update({"req": r["req"], "grade": r["grade"], "score": r["score"],
                      "years": r["years"] or "", "posted": r["date"],
                      "title": r["title"], "loc": r["loc"], "url": r["url"],
                      "last_checked": today, "status": "open"})
        entry.setdefault("first_seen", today)
        watch[r["req"]] = entry
    save_watchlist(watch, path)
    print(f"\nwatchlist: {sum(1 for r in watch.values() if r.get('status') == 'open')} open, "
          f"{sum(1 for r in watch.values() if r.get('status') == 'closed')} closed "
          f"-> {path}")
    return in_band


def demo():
    """Offline self-check. The ledger round-trip and the open->closed transition
    are the whole point of this file, and both fail silently: a ledger that
    doesn't persist reports nothing as closed, forever."""
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(), "sub", "watchlist.tsv")

    assert load_watchlist(tmp) == {}, "missing ledger must read as a first run"
    rows = {"111": {"req": "111", "grade": "25", "score": "42", "years": "2",
                    "posted": "2026-08-10", "title": "Full Stack\tEngineer",
                    "loc": "Noida", "url": "u1", "first_seen": "2026-08-10",
                    "last_checked": "2026-08-10", "status": "open"},
            "222": {"req": "222", "grade": "25", "score": "7", "years": "1",
                    "posted": "2026-06-21", "title": "ASE II", "loc": "Noida",
                    "url": "u2", "first_seen": "2026-06-21",
                    "last_checked": "2026-08-10", "status": "open"}}
    save_watchlist(rows, tmp)
    back = load_watchlist(tmp)
    assert set(back) == {"111", "222"}, back
    # A tab inside a title would shift every later column by one.
    assert back["111"]["title"] == "Full Stack Engineer", back["111"]["title"]
    assert back["111"]["url"] == "u1" and back["222"]["status"] == "open"

    # 404 -> closed; live -> stays open; an exception must NOT close a req.
    def fake(url):
        if url == "u1":
            return {"live": False}
        if url == "u2":
            return {"live": True}
        raise TimeoutError("boom")
    closed, open_ = recheck(back, "2026-08-18", log=lambda *_: None, detail=fake)
    assert [r["req"] for r in closed] == ["111"], closed
    assert [r["req"] for r in open_] == ["222"], open_
    assert back["111"]["status"] == "closed" and back["222"]["status"] == "open"

    back["333"] = {"req": "333", "url": "u3", "status": "open", "grade": "25",
                   "first_seen": "x", "title": "t", "loc": "l"}
    closed2, open2 = recheck(back, "2026-08-18", log=lambda *_: None, detail=fake)
    assert [r["req"] for r in closed2] == [], "a fetch error must not close a req"
    assert {r["req"] for r in open2} == {"222", "333"}
    # Closed entries are not re-checked, so a close is reported exactly once.
    assert all(r["req"] != "111" for r in open2)
    print("optum_grade_scan demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
