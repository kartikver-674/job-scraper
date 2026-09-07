"""Find public ATS boards for the companies your sweep already surfaced.

The paid sources are good at telling you WHICH companies are hiring your stack.
They just make you apply through the worst available door: an aggregator listing,
days old, hundreds of applicants deep. Most of those companies also expose a free
public ATS API with the same roles on it.

So: take the company names out of the last run's output, try each ATS platform's
public endpoint, and print whatever resolves as a paste-ready ATS_BOARDS block.
Each board that answers is also counted for jobs in HOME_LOCATION_HINTS, because
that is what decides whether SETTINGS["keep_restricted_if_hires_home"] can rescue
its geo-locked roles — the same number the hand-written counts in config.py record.

    python harvest_ats.py                    # companies scoring >= 20
    python harvest_ats.py --min-score 0      # everything (many more requests)
    python harvest_ats.py --limit 40         # cap the companies probed

Costs nothing but requests. Nothing is written; you paste what you want to keep.
"""
import argparse
import glob
import json
import os
import re
import time
import urllib.error

from config import ATS_BOARDS, HOME_LOCATION_HINTS, SETTINGS
from sources._http import flat, get_json
from sources.ats import ATS

# One request per (company, platform, slug variant), so keep variants few. These
# two cover nearly every real token: greenhouse/lever/ashby overwhelmingly use
# the squashed lowercase name, occasionally hyphenated.
def slugs(company):
    base = re.sub(r"[^a-z0-9 ]", " ", company.lower())
    # Drop legal suffixes — no board token contains them.
    base = re.sub(r"\b(inc|llc|ltd|limited|pvt|private|corp|corporation|gmbh|"
                  r"technologies|technology|labs|software|solutions|systems)\b",
                  " ", base)
    words = base.split()
    if not words:
        return []
    squashed = "".join(words)
    hyphened = "-".join(words)
    return [squashed] if squashed == hyphened else [squashed, hyphened]


def known_tokens():
    """Every token already in config, so we never re-probe a solved board."""
    return {t.lower() for boards in ATS_BOARDS.values() for t in boards}


def companies_from_output(min_score, path=None):
    """Company names from the newest output JSON, best-scoring first.

    Scoped to SETTINGS["output_dir"] and picked by MTIME. It used to glob
    output/** recursively and take the max by PATH STRING, which is neither the
    newest file nor this profile's: "output/optum/jobs_combined.json" sorts above
    "output/jobs_2026-08-21.json", so a run under one résumé's profile silently
    probed another's companies and reported "1 company, nothing resolved".
    """
    path = path or max(
        glob.glob(os.path.join(SETTINGS["output_dir"], "jobs_*.json")),
        key=lambda p: (p.endswith("jobs_all.json"), os.path.getmtime(p)))
    rows = json.load(open(path))
    seen = {}
    for r in sorted(rows, key=lambda r: -(r.get("score") or 0)):
        name = (r.get("company") or "").strip()
        if name and (r.get("score") or 0) >= min_score:
            seen.setdefault(name, r.get("score"))
    return path, list(seen)


def probe(platform, token):
    """(job_count, home_count) if this board resolves, else None.

    A 4xx means the token is wrong and is the expected answer for most guesses —
    _http.get_bytes already declines to retry those, so a miss costs one request.
    """
    spec = ATS[platform]
    try:
        data = get_json(spec["url"].format(token=token), timeout=15, retries=0)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError,
            TimeoutError, OSError):
        return None
    jobs = data.get(spec["list"]) if spec["list"] else data
    if not isinstance(jobs, list) or not jobs:
        return None          # an empty board is indistinguishable from a wrong token
    loc_path = spec["map"].get("Location")
    home = sum(1 for j in jobs
               if any(h in flat(_dig(j, loc_path)).lower() for h in HOME_LOCATION_HINTS))
    return len(jobs), home


def _dig(obj, path):
    for hop in (path or "").split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(hop)
    return obj


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--min-score", type=int, default=20,
                   help="only probe companies with a job scoring at least this (default 20)")
    p.add_argument("--limit", type=int, default=60,
                   help="max companies to probe (default 60)")
    p.add_argument("--file", help="a specific output JSON instead of the newest")
    p.add_argument("--delay", type=float, default=0.3,
                   help="seconds between requests (default 0.3) — be a polite guest")
    args = p.parse_args()

    path, names = companies_from_output(args.min_score, args.file)
    names = names[:args.limit]
    skip = known_tokens()
    print(f"# from {path}: probing {len(names)} companies "
          f"x {len(ATS)} platforms (score >= {args.min_score})\n")

    found = {}
    for name in names:
        for token in slugs(name):
            if token in skip:
                continue
            for platform in ATS:
                hit = probe(platform, token)
                time.sleep(args.delay)
                if not hit:
                    continue
                jobs, home = hit
                found.setdefault(platform, {})[token] = (name, jobs, home)
                print(f"  {platform:16} {token:24} {jobs:4} jobs, {home:3} in India   {name}")
                break                      # one platform per company is enough
            else:
                continue
            break

    if not found:
        print("\n# nothing resolved. Most employers have no public ATS API — the "
              "config.py comment lists 40+ already-probed misses.")
        return

    print("\n# ---- paste into config.py ATS_BOARDS (merge into the existing "
          "platform keys — a duplicate key replaces the dict) ----")
    for platform, boards in found.items():
        print(f'    "{platform}": {{')
        for token, (name, jobs, home) in sorted(boards.items(), key=lambda kv: -kv[1][2]):
            print(f'        "{token}": "{name}",'.ljust(46)
                  + f"# {home}/{jobs} India")
        print("    },")
    print("\n# Boards with 0 India jobs still work, they just can't have their "
          "geo-locked\n# roles rescued by keep_restricted_if_hires_home.")


def demo():
    """Offline self-check: python harvest_ats.py --demo is not a thing, run this
    as `python -c "import harvest_ats; harvest_ats.demo()"`."""
    assert slugs("Hire Feed") == ["hirefeed", "hire-feed"]
    assert slugs("MongoDB") == ["mongodb"]
    assert slugs("Acme Technologies Pvt Ltd") == ["acme"]
    assert slugs("SWAKIO™") == ["swakio"]
    assert slugs("") == [] and slugs("Pvt Ltd") == []
    assert _dig({"location": {"name": "Pune, India"}}, "location.name") == "Pune, India"
    assert _dig({}, "location.name") is None

    # Input selection: this profile's directory, newest file, jobs_all preferred.
    # The bug it guards was silent — a wrong pick still prints a plausible report.
    import tempfile
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "other_profile"))
    def _write(rel, rows, mtime):
        f = os.path.join(tmp, rel)
        json.dump(rows, open(f, "w"))
        os.utime(f, (mtime, mtime))
        return f
    _write("other_profile/jobs_combined.json", [{"company": "Wrong", "score": 9}], 3000)
    _write("jobs_2026-01-01.json", [{"company": "Older", "score": 9}], 1000)
    want = _write("jobs_2026-06-01.json", [{"company": "Right", "score": 9}], 2000)
    orig_dir = SETTINGS["output_dir"]
    try:
        SETTINGS["output_dir"] = tmp
        assert companies_from_output(0) == (want, ["Right"]), companies_from_output(0)
        allf = _write("jobs_all.json", [{"company": "All", "score": 9}], 500)
        assert companies_from_output(0) == (allf, ["All"])   # preferred despite mtime
    finally:
        SETTINGS["output_dir"] = orig_dir
    print("harvest_ats demo ok")


if __name__ == "__main__":
    main()
