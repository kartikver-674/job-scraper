"""Is a configured ATS board an employer's board, or a repost farm? MEASUREMENT.

Two boards were kept out of the last promotion by eye, during a liveness check:

  lever:jobgether       4,662 postings, 2,023 titles, each reposted across up to
                        42 countries.  -> REPOST FARM
  greenhouse:speechify  1,086 postings, 1,041 titles, one per US city with the
                        city IN the title ("Go-to-Market - Anaheim, CA, USA").
                        -> GEO-SPAM

Eyes do not scale to 130 boards, let alone the ~2,026 companies still unprobed.
This computes both signatures for every board so a threshold can be read off the
distribution rather than guessed, and so the next harvest can vet before it
promotes.

The two signatures are genuinely different and need separate measures:

  span      the largest number of DISTINCT LOCATIONS a single title occupies.
            A real employer opens "Staff Engineer" in two or three offices; a
            repost farm lists one role in every country it can name.
  collapse  how far the distinct-title count falls once a trailing location is
            stripped off the title. Geo-spam bakes the city into the title, so
            job_key (company+title) cannot collapse the duplicates and every
            city becomes its own row.

Neither alone is enough. A big multinational scores high on span with entirely
legitimate roles, which is why `span` is reported next to how SIMILAR the
titles sharing a location-set are -- distinct real roles in many cities look
nothing like one role pasted into many cities.

    python -m bench.board_hygiene --cache <dir> --out <json>
    python -m bench.board_hygiene --demo
"""
import argparse
import collections
import json
import os
import re
import statistics
import sys

from config import ATS_BOARDS
from sources import ats

# Stripping a trailing location off a title is the geo-spam measure, so it has
# to be precise: a regex for "capitalised words at the end" also eats
# "Go-to-Market" and "Engineering Manager, Platform", which would make an honest
# board look spammy. So strip ONLY a tail that matches the row's OWN location
# string. That is self-calibrating -- no gazetteer, no guessing which words are
# places, and it is exactly the pattern speechify exhibits.
_REMOTE = ("remote", "hybrid", "onsite", "anywhere")
_SEP = " -\u2014\u2013(,|/\u00b7"


def base_title(title, location=""):
    """Title with a trailing copy of its own location removed."""
    t = (title or "").strip().rstrip(")]").strip()
    loc = (location or "").strip().rstrip(")]").strip()
    candidates = [loc] if loc else []
    if "," in loc:
        candidates += [loc.split(",")[0].strip(), loc.split(",")[-1].strip()]
    candidates += list(_REMOTE)
    for cand in candidates:
        if len(cand) < 3:
            continue
        if t.lower().endswith(cand.lower()):
            cut = t[: len(t) - len(cand)].rstrip(_SEP).strip()
            if len(cut) >= 4:
                return cut
    return t


def metrics(rows):
    """Both signatures for one board's postings."""
    n = len(rows)
    if not n:
        return None
    titles = [r["Title"] for r in rows]
    locs = [r["Location"] for r in rows]
    bases = [base_title(t, l) for t, l in zip(titles, locs)]
    by_title = collections.defaultdict(set)
    for t, l in zip(titles, locs):
        by_title[t].add(l)
    spans = sorted((len(v) for v in by_title.values()), reverse=True)
    distinct_titles = len(set(titles))
    collapsed = len(set(bases))
    return {
        "postings": n,
        "distinct_titles": distinct_titles,
        "distinct_locations": len(set(locs)),
        "title_ratio": round(distinct_titles / n, 3),
        # span: the worst single title, and how widespread the habit is
        "max_span": spans[0],
        "titles_spanning_5plus": sum(1 for s in spans if s >= 5),
        "share_in_spanning_titles": round(
            sum(len(v) for v in by_title.values() if len(v) >= 5) / n, 3),
        # collapse: 1.0 = titles carry no location; low = the city is the title
        "collapse_ratio": round(collapsed / max(1, distinct_titles), 3),
        "titles_after_collapse": collapsed,
    }


def demo():
    assert base_title("Go-to-Market - Anaheim, CA, USA", "Anaheim, CA, USA") == "Go-to-Market"
    assert base_title("Senior Engineer (Bangalore)", "Bangalore") == "Senior Engineer"
    assert base_title("Backend Engineer, Remote", "Remote") == "Backend Engineer"
    # The tail must be the row's OWN location. These are the cases a
    # capitalised-words regex got wrong, and they are why it was replaced.
    assert base_title("Go-to-Market", "Remote") == "Go-to-Market"
    assert base_title("Staff Software Engineer", "Pune") == "Staff Software Engineer"
    assert base_title("Engineering Manager, Platform", "Pune") == "Engineering Manager, Platform"
    assert base_title("Designer", "Designer") == "Designer"   # never cut to nothing

    geo = [{"Title": f"Go-to-Market - {c}, USA", "Location": f"{c}, USA"}
           for c in ("Anaheim", "Boston", "Chicago", "Denver", "Fresno")]
    m = metrics(geo)
    assert m["collapse_ratio"] == 0.2, m           # 5 titles -> 1
    assert m["max_span"] == 1, m                   # each title in one place

    repost = [{"Title": ".NET Engineer", "Location": c}
              for c in ("Spain", "Germany", "UK", "Ireland", "India", "Canada")]
    m = metrics(repost)
    assert m["max_span"] == 6, m                   # one title, six countries
    assert m["collapse_ratio"] == 1.0, m           # title carries no location

    normal = [{"Title": t, "Location": l} for t, l in
              (("Staff Engineer", "Pune"), ("Staff Engineer", "Dublin"),
               ("Product Manager", "Pune"), ("Designer", "Remote"))]
    m = metrics(normal)
    assert m["max_span"] == 2 and m["collapse_ratio"] == 1.0, m
    print("bench.board_hygiene: all self-checks pass")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out")
    ap.add_argument("--demo", action="store_true")
    args, _ = ap.parse_known_args()
    if args.demo:
        demo()
        return
    os.makedirs(args.cache, exist_ok=True)

    # The two already judged by eye, fetched as CALIBRATION so the thresholds
    # are read against known answers rather than asserted.
    targets = [(p, t, n) for p, b in ATS_BOARDS.items() for t, n in b.items()]
    refs = [("lever", "jobgether", "Jobgether [REF repost]"),
            ("greenhouse", "speechify", "Speechify [REF geo-spam]")]

    out = []
    for i, (plat, tok, name) in enumerate(targets + refs, 1):
        path = os.path.join(args.cache, f"{plat}-{tok}.json")
        try:
            if os.path.exists(path):
                rows = json.load(open(path))
            else:
                rows = ats.fetch(plat, tok, name, lambda t: True, lambda l: True)
                json.dump(rows, open(path, "w"))
        except Exception as exc:
            print(f"  [{i}/{len(targets)+len(refs)}] {name:<28} ! {exc}", flush=True)
            out.append({"platform": plat, "token": tok, "name": name,
                        "error": f"{type(exc).__name__}: {exc}"})
            continue
        m = metrics(rows)
        rec = {"platform": plat, "token": tok, "name": name,
               "reference": name.endswith("]"), **(m or {"postings": 0})}
        out.append(rec)
        print(f"  [{i}/{len(targets)+len(refs)}] {name[:26]:<26} "
              f"n={rec.get('postings',0):>5} span={rec.get('max_span','-'):>3} "
              f"collapse={rec.get('collapse_ratio','-')}", flush=True)
    if args.out:
        json.dump(out, open(args.out, "w"), indent=1)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
