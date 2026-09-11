"""Can harvest_ats.py reach the JD that adzuna.in would not give us? MEASUREMENT ONLY.

The redirect_url probe (docs/.../2026-09-11-adzuna-jd-probe.md) measured a real
median +11 score gain when a full JD replaced Adzuna's 500-char snippet -- and
found the only door to that JD was scraping adzuna.in, which rate-limited us.
A public ATS board would be the same uplift through a source we already fetch
freely, with no terms exposure.

That is the hypothesis under test. Resolution rate alone does not answer it: a
board that resolves but does not carry the posting Adzuna surfaced delivers no
JD for that row. So this measures four things in order, and reports them apart:

    resolved            the employer has a public board on a platform in ats.ATS
    carries the posting the board contains the SPECIFIC job, by job_key
    re-scored           that posting's JD through score_job
    delta               against the same row's Adzuna-snippet score

bucket2 (90, previously paid-only) and bucket3 (239, new to Sweep) stay separate
throughout. They answer different questions -- cost reduction against new reach
-- and one resolution rate across both would answer neither.

Workable is NOT in this pass. It is not in ats.ATS, its field paths are
unverified, and mixing it in would blur which platform table produced the rate.
Its endpoint is probed only as a SIGNAL, reported separately and excluded from
every resolution number.

    python -m bench.ats_harvest_probe --buckets <buckets.json> --out <results.json>
    python -m bench.ats_harvest_probe --demo        # offline
"""
import argparse
import collections
import json
import os
import re
import statistics
import sys
import time
import urllib.error

import config
import scraper
import harvest_ats
from sources import ats
from sources._http import get_json

USEFUL = 20
WORKABLE = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"

# Third-party recruiters rather than direct employers. A resolved staffing board
# is not the same finding as a resolved employer board: the roles on it are other
# companies' openings, resold, and Sweep has no filter that distinguishes them.
# Named explicitly where known, pattern-matched otherwise, and EVERY tag is
# printed so the rule can be audited rather than trusted.
STAFFING_NAMED = {
    "anlage infotech", "anlage infotech india", "bcforward india", "bcforward",
    "artech infosystems", "artech", "aditi tech consulting", "acme services",
}
# Two tiers, because they are not equally safe. "staffing"/"recruiter" names the
# business model; "consulting" does not -- Capco and BMW TechWorks are
# consultancies that hire their own staff. The weak tier is reported apart and
# never silently folded into the strong one.
STAFFING_STRONG = re.compile(
    r"\b(staffing|recruit\w*|manpower|placement\w*|resourcing|workforce|"
    r"talent|hiring|hr\s+solutions|hr\s+services|outsourc\w*)\b", re.I)
STAFFING_WEAK = re.compile(r"\b(consult\w*|services|solutions)\b", re.I)


def is_staffing(company_key, display):
    """'' | named | pattern_strong | pattern_weak — every tag is printed so the
    rule can be audited instead of trusted."""
    if company_key in STAFFING_NAMED:
        return "named"
    if STAFFING_STRONG.search(display):
        return "pattern_strong"
    if STAFFING_WEAK.search(display):
        return "pattern_weak"
    return ""


def probe_company(display, cache, delay):
    """First platform in ats.ATS that resolves for any slug. (platform, token) or None."""
    for token in harvest_ats.slugs(display):
        for platform in ats.ATS:
            ck = f"{platform}:{token}"
            if ck not in cache:
                hit = harvest_ats.probe(platform, token)
                cache[ck] = [hit[0], hit[1]] if hit else None
                time.sleep(delay)
            if cache[ck]:
                return platform, token, cache[ck]
    return None


def board_rows(platform, token, display):
    """Every posting on a resolved board, unfiltered — the predicates are the
    caller's policy and here we want the whole board to search in."""
    return ats.fetch(platform, token, display, lambda t: True, lambda l: True)


def workable_signal(display, cache, delay):
    """Does this employer appear to run on Workable? SIGNAL ONLY.

    Field paths are unverified (sources/ats.py records why), so this never
    produces a row and never counts toward a resolution rate.
    """
    for token in harvest_ats.slugs(display):
        key = f"workable:{token}"
        if key not in cache:
            try:
                data = get_json(WORKABLE.format(token=token), timeout=15, retries=0)
                jobs = (data or {}).get("jobs")
                cache[key] = len(jobs) if isinstance(jobs, list) else None
            except Exception:
                cache[key] = None
            time.sleep(delay)
        if cache[key]:
            return token, cache[key]
    return None


def n_skills(value):
    return len([s for s in (value or "").split(",") if s.strip()])


def demo():
    assert is_staffing("bcforward india", "BCforward India") == "named"
    assert is_staffing("x", "Talent Aspire") == "pattern_strong"
    assert is_staffing("x", "Aditi Tech Consulting") == "pattern_weak"
    assert is_staffing("x", "Anlage Infotech") == ""   # only the named variants
    assert is_staffing("x", "BMW TechWorks") == ""
    assert is_staffing("x", "Citigroup") == ""
    # slugs/probe are harvest_ats's, unmodified — the point is to test THAT code
    assert harvest_ats.slugs("Acme Technologies Pvt Ltd") == ["acme"]
    print("bench.ats_harvest_probe: all self-checks pass")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buckets")
    ap.add_argument("--adzuna-cache")
    ap.add_argument("--out")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--probe-cache")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.demo:
        demo()
        return

    buckets = json.load(open(args.buckets))
    # The Adzuna rows, rebuilt the way the replay built them, so the "before"
    # score in every delta is the exact number Phase 1 reported.
    adz = {}
    for f in sorted(__import__("glob").glob(os.path.join(args.adzuna_cache, "*.json"))):
        for raw in json.load(open(f)):
            sc = scraper.score_job(dict(raw))
            if sc is None:
                continue
            k = scraper.job_key(sc)
            if k and (k not in adz or sc["score"] > adz[k]["score"]):
                adz[k] = sc

    cache = {}
    if args.probe_cache and os.path.exists(args.probe_cache):
        cache = json.load(open(args.probe_cache))

    results = {}
    for bucket in ("bucket2", "bucket3"):
        names = buckets[bucket]
        keys = sorted(names)[:args.limit] if args.limit else sorted(names)
        print(f"\n=== {bucket}: {len(keys)} companies x {len(ats.ATS)} platforms ===",
              flush=True)
        out = []
        for i, ck in enumerate(keys, 1):
            display = names[ck]
            rec = {"company_key": ck, "display": display,
                   "staffing": is_staffing(ck, display),
                   "postings": [k for k in adz if scraper._company_key(adz[k]["Company"]) == ck]}
            try:
                hit = probe_company(display, cache, args.delay)
            except Exception as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"
                hit = None
            if hit:
                platform, token, (jobs, home) = hit
                rec.update(resolved=True, platform=platform, token=token,
                           board_jobs=jobs, board_india=home)
                try:
                    rows = board_rows(platform, token, display)
                    by_key = {}
                    for r in rows:
                        k = scraper.job_key(r)
                        if k:
                            by_key[k] = r
                    matches = []
                    for k in rec["postings"]:
                        if k in by_key:
                            full = scraper.score_job(dict(by_key[k]))
                            before = adz[k]
                            matches.append({
                                "title": before["Title"],
                                "adzuna_score": before["score"],
                                "adzuna_skills": n_skills(before.get("matched_skills")),
                                "adzuna_desc": len(before.get("Description") or ""),
                                "ats_desc": len(by_key[k].get("Description") or ""),
                                "ats_score": full["score"] if full else None,
                                "ats_skills": (n_skills(full.get("matched_skills"))
                                               if full else None),
                            })
                    rec["matches"] = matches
                except Exception as exc:
                    rec["board_error"] = f"{type(exc).__name__}: {exc}"
                tag = "  [staffing]" if rec["staffing"] else ""
                print(f"  [{i}/{len(keys)}] {display[:34]:<34} {platform}:{token} "
                      f"{jobs} jobs, {len(rec.get('matches', []))} of "
                      f"{len(rec['postings'])} postings on it{tag}", flush=True)
            else:
                rec["resolved"] = False
            out.append(rec)
            if args.probe_cache and i % 20 == 0:
                json.dump(cache, open(args.probe_cache, "w"))
        results[bucket] = out
        if args.probe_cache:
            json.dump(cache, open(args.probe_cache, "w"))

    # --- Workable, SIGNAL ONLY, on the unresolved -------------------------
    print("\n=== workable signal (unresolved only; NOT a resolution) ===", flush=True)
    for bucket in ("bucket2", "bucket3"):
        for rec in results[bucket]:
            if rec.get("resolved"):
                continue
            sig = workable_signal(rec["display"], cache, args.delay)
            if sig:
                rec["workable_signal"] = {"token": sig[0], "jobs": sig[1]}
                print(f"  {bucket} {rec['display'][:38]:<38} workable:{sig[0]} "
                      f"({sig[1]} jobs)", flush=True)
    if args.probe_cache:
        json.dump(cache, open(args.probe_cache, "w"))
    if args.out:
        json.dump(results, open(args.out, "w"), indent=1)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
