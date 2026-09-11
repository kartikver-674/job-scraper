"""Phase 1: can an aggregator search API recover what we buy from Apify?

Replays a completed LinkedIn sweep's (keyword, location) combos against Adzuna
and compares the two result sets. MEASUREMENT ONLY -- no production module
imports this, nothing here changes Sweep's behaviour, and no Apify actor is run
(the LinkedIn side is read off the sweep's own output files, which cost nothing).

    ADZUNA_APP_ID=... ADZUNA_APP_KEY=... \
    JOB_PROFILE=kartik_reachable .venv/bin/python -m bench.adzuna_replay \
        --profile kartik_reachable --out /tmp/adzuna_phase1.json

Method, and its two honest limits:

  * The combo set replayed is a profile's COMPLETE .done_combos, so the LinkedIn
    rows in that profile's output are exactly what those combos produced.
    Recovery is therefore a real ratio, not a ratio against a denominator that
    includes searches we never replayed. Run it on a partial set with
    --no-recovery and the recovery figures are suppressed rather than quoted
    wrongly.
  * The sweep happened on a date in the past, and a posting that has since been
    filled is gone from Adzuna's index as well. That biases overlap DOWNWARD and
    cannot be corrected for, only reported: --age is set to span the LinkedIn
    posting window, and the report prints the date distribution of both sides.

Scoring uses the SAME profile config that scored the LinkedIn rows, so the two
score distributions are comparable. Matching uses scraper.job_key, which is what
Sweep itself uses to decide two rows are the same posting.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys
import urllib.error

import config
import scraper
from bench import adzuna

PAID = ("linkedin", "indeed", "naukri")
USEFUL = 20          # the audit's "useful posting" threshold
FIELDS = ["Title", "Company", "Location", "Salary", "Experience",
          "Posted Date", "Job URL", "Description"]


# --------------------------------------------------------------------------
# Inputs, all read from disk
# --------------------------------------------------------------------------
def combos(profile, site="linkedin"):
    """Every completed (keyword, location) for one site, in ledger order."""
    path = os.path.join("output", profile, ".done_combos")
    out, seen = [], set()
    for line in open(path):
        p = line.strip().split("|")
        if len(p) >= 4 and p[1] == site and (p[2], p[3]) not in seen:
            seen.add((p[2], p[3]))
            out.append((p[2], p[3]))
    return out


def rows_from(pattern, keep):
    """Output rows matching `keep(source_site)`, deduped by job_key."""
    best = {}
    for f in glob.glob(pattern, recursive=True):
        try:
            data = json.load(open(f))
        except Exception:
            continue
        for r in data:
            if not keep((r.get("source_site") or "")):
                continue
            k = scraper.job_key(r)
            if k is None:
                continue
            if k not in best or (r.get("score") or 0) > (best[k].get("score") or 0):
                best[k] = r
    return best


def company_key(name):
    return scraper._company_key(name or "")


# --------------------------------------------------------------------------
# Measurement helpers
# --------------------------------------------------------------------------
def scored(rows):
    """Run Sweep's own scorer. Returns (kept, hard_dropped).

    score_job calls enrich.enrich() itself (scraper.py:623), so the remote /
    visa / EOR / timezone columns are filled here exactly as they are for a
    real sweep -- nothing about the Adzuna rows takes a different path.
    """
    kept, dropped = [], 0
    for r in rows:
        out = scraper.score_job(r)
        if out is None:
            dropped += 1
        else:
            kept.append(out)
    return kept, dropped


def distribution(values):
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {"n": len(s), "min": s[0], "p25": s[len(s) // 4],
            "median": s[len(s) // 2], "p75": s[3 * len(s) // 4], "max": s[-1],
            "mean": round(sum(s) / len(s), 1)}


def title_matches(keyword, title):
    """Does the returned title contain the query's content words at all?

    Not a quality judgement -- a systematic miss here means the aggregator is
    doing something other than the search we asked for, which is the failure
    mode that would make per-query results untrustworthy.
    """
    stop = {"developer", "engineer", "js", "stack"}
    want = [w for w in re.findall(r"[a-z0-9.+#]+", keyword.lower())
            if w not in stop]
    low = (title or "").lower()
    return (not want) or any(w in low for w in want)


def location_matches(location, text):
    if location in ("India", "Remote"):
        return True     # whole-country query; nothing to mismatch against
    return location.lower() in (text or "").lower()


# --------------------------------------------------------------------------
def main():
    # bench-only, and the same mechanism every other credential in this repo
    # uses. Never echoed, never written back.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, help="output/<profile>/ to replay")
    ap.add_argument("--depth", type=int, default=None,
                    help="results per search (default: the profile's max_results)")
    ap.add_argument("--age", type=int, default=None,
                    help="max_days_old (default: spans the LinkedIn posting window)")
    ap.add_argument("--limit", type=int, default=None, help="cap combos replayed")
    ap.add_argument("--no-recovery", action="store_true",
                    help="partial combo set: suppress recovery ratios")
    ap.add_argument("--out", default=None, help="write the full result JSON here")
    args = ap.parse_args()

    if config.PROFILE != args.profile:
        sys.exit(f"Run with JOB_PROFILE={args.profile} so Adzuna rows are scored "
                 f"by the same config that scored the LinkedIn rows "
                 f"(config.PROFILE is {config.PROFILE!r}).")
    adzuna.credentials()        # fail before any measurement if unset

    plan = combos(args.profile)
    if args.limit is not None:
        plan = plan[:args.limit]
    depth = args.depth or config.SEARCH["max_results"]

    # --- LinkedIn baseline, straight off the sweep's own output --------------
    li = rows_from(f"output/{args.profile}/jobs_*.json",
                   lambda s: s.startswith("linkedin"))
    li_dates = sorted((r.get("date_posted") or "")[:10] for r in li.values()
                      if r.get("date_posted"))
    oldest = li_dates[0] if li_dates else None
    age = args.age
    if age is None:
        if oldest:
            days = (datetime.date.today()
                    - datetime.date(*map(int, oldest.split("-")))).days
            age = min(days + 1, 90)
        else:
            age = config.SETTINGS["max_age_days"] or 30

    # --- everything Sweep already gets for free, across every profile --------
    free = rows_from("output/**/jobs_*.json",
                     lambda s: bool(s) and not s.split(":")[0] in PAID)
    free_companies = {company_key(r.get("company")) for r in free.values()}
    free_companies.discard("")
    li_companies = {company_key(r.get("company")) for r in li.values()}
    li_companies.discard("")

    print(f"profile        {args.profile}")
    print(f"combos         {len(plan)} linkedin searches from .done_combos")
    print(f"linkedin rows  {len(li)} distinct (postings dated {oldest} .. {li_dates[-1] if li_dates else '?'})")
    print(f"free inventory {len(free)} distinct rows / {len(free_companies)} companies "
          f"(every non-paid source, all profiles)")
    print(f"adzuna depth   {depth} results, max_days_old={age}\n")

    # --- replay -------------------------------------------------------------
    cache, per_combo, adz = {}, [], {}
    quota_hit = False
    for i, (keyword, location) in enumerate(plan, 1):
        try:
            country, where = adzuna.resolve(location)
        except ValueError as exc:
            print(f"  [{i}/{len(plan)}] {keyword} @ {location:<12} ! {exc}")
            per_combo.append({"keyword": keyword, "location": location,
                              "error": str(exc)})
            continue
        qkey = (keyword, country, where)
        reused = qkey in cache
        if not reused:
            try:
                cache[qkey] = adzuna.search(keyword, location, results=depth,
                                            max_days_old=age)
            except adzuna.QuotaExhausted as exc:
                print(f"\n  ! {exc} -- stopping after {i - 1} of {len(plan)} combos")
                quota_hit = True
                break
            except urllib.error.HTTPError as exc:
                # A rejected key is wrong for every combo, not this one. Without
                # this the loop cheerfully repeats the same 401 sixty-four times.
                if exc.code in (401, 403):
                    sys.exit(f"adzuna: {exc.code} {exc.reason} -- the app id / key "
                             f"was rejected. Nothing was measured.")
                print(f"  [{i}/{len(plan)}] {keyword} @ {location:<12} ! {exc}")
                per_combo.append({"keyword": keyword, "location": location,
                                  "error": str(exc)})
                continue
            except Exception as exc:
                print(f"  [{i}/{len(plan)}] {keyword} @ {location:<12} ! {exc}")
                per_combo.append({"keyword": keyword, "location": location,
                                  "error": str(exc)})
                continue
        raw = cache[qkey]
        kept, dropped = scored([dict(r) for r in raw])
        useful = [r for r in kept if r["score"] >= USEFUL]
        matched = sum(1 for r in kept if scraper.job_key(r) in li)
        for r in kept:
            k = scraper.job_key(r)
            if k and (k not in adz or r["score"] > adz[k]["score"]):
                adz[k] = r
        per_combo.append({
            "keyword": keyword, "location": location,
            "query": {"country": country, "where": where},
            "reused_response": reused,
            "rows": len(raw), "scored": len(kept), "hard_dropped": dropped,
            "useful": len(useful),
            "matched_linkedin": matched,
            "title_mismatch": sum(1 for r in raw if not title_matches(keyword, r["Title"])),
            "location_mismatch": sum(1 for r in raw
                                     if not location_matches(location, r["Location"])),
            "scores": distribution([r["score"] for r in kept]),
        })
        tag = " (reused)" if reused else ""
        print(f"  [{i}/{len(plan)}] {keyword[:28]:<28} @ {location:<10} "
              f"{len(raw):>3} rows  {len(useful):>3} useful  "
              f"{matched:>3} also-on-linkedin{tag}")

    # --- aggregate ----------------------------------------------------------
    shared = set(adz) & set(li)
    adz_only = set(adz) - set(li)
    li_only = set(li) - set(adz)
    li_useful = {k for k, r in li.items() if (r.get("score") or 0) >= USEFUL}
    adz_useful = {k for k, r in adz.items() if r["score"] >= USEFUL}

    adz_companies = {company_key(r["Company"]) for r in adz.values()}
    adz_companies.discard("")
    adz_only_companies = adz_companies - li_companies
    adz_new_companies = adz_companies - li_companies - free_companies

    completeness = {f: round(100 * sum(1 for r in adz.values()
                                       if str(r.get(f) or "").strip()) / max(1, len(adz)), 1)
                    for f in FIELDS}
    desc_len = distribution([len(r.get("Description") or "") for r in adz.values()])
    # OUTPUT_COLUMNS has no description, so LinkedIn's text length is not
    # recoverable from disk. matched_skills is, and it measures what the text is
    # FOR: how much scoring signal a row's prose actually carried.
    def n_skills(value):
        return len([s for s in (value or "").split(",") if s.strip()])
    adz_skills = distribution([n_skills(r.get("matched_skills")) for r in adz.values()])
    li_skills = distribution([n_skills(r.get("matched_skills")) for r in li.values()])
    predicted = sum(1 for r in adz.values() if r.get("_predicted_salary"))
    total_fetched = sum(len(v) for v in cache.values())
    delivered = sum(c.get("rows", 0) for c in per_combo)

    result = {
        "profile": args.profile,
        "run_date": str(datetime.date.today()),
        "combos_planned": len(plan),
        "combos_replayed": len([c for c in per_combo if "rows" in c]),
        "api_calls": len(cache),
        "quota_exhausted": quota_hit,
        "depth": depth, "max_days_old": age,
        "recovery_valid": not args.no_recovery and not quota_hit,
        "linkedin": {"distinct": len(li), "useful": len(li_useful),
                     "companies": len(li_companies),
                     "scores": distribution([r.get("score") or 0 for r in li.values()])},
        "adzuna": {"rows_fetched": total_fetched, "rows_after_reuse": delivered,
                   "distinct": len(adz), "useful": len(adz_useful),
                   "companies": len(adz_companies),
                   "scores": distribution([r["score"] for r in adz.values()]),
                   "duplicate_rate_pct": (round(100 * (1 - len(adz) / delivered), 1)
                                          if delivered else 0.0)},
        "overlap": {
            "shared": len(shared), "linkedin_only": len(li_only),
            "adzuna_only": len(adz_only),
            "shared_useful": len(adz_useful & li_useful),
            "recovery_of_useful_pct": round(
                100 * len(adz_useful & li_useful) / max(1, len(li_useful)), 1),
            "recovery_of_all_pct": round(100 * len(shared) / max(1, len(li)), 1),
            "new_useful": len(adz_useful - set(li)),
        },
        "companies": {
            "linkedin": len(li_companies), "adzuna": len(adz_companies),
            "shared": len(adz_companies & li_companies),
            "adzuna_not_on_linkedin": len(adz_only_companies),
            "adzuna_new_to_sweep": len(adz_new_companies),
            "adzuna_already_in_free_sources": len(adz_only_companies & free_companies),
        },
        "field_completeness_pct": completeness,
        "description_chars_adzuna": desc_len,
        "matched_skills_per_row": {"adzuna": adz_skills, "linkedin": li_skills},
        "salary": {"disclosed": sum(1 for r in adz.values() if r.get("Salary")),
                   "predicted_dropped": predicted,
                   "pct_disclosed": round(100 * sum(1 for r in adz.values()
                                                    if r.get("Salary")) / max(1, len(adz)), 1)},
        "freshness": dict(collections.Counter(
            (r.get("Posted Date") or "")[:7] for r in adz.values())),
        "linkedin_freshness": dict(collections.Counter(
            (r.get("date_posted") or "")[:7] for r in li.values())),
        "mismatch": {
            "title": sum(c.get("title_mismatch", 0) for c in per_combo),
            "location": sum(c.get("location_mismatch", 0) for c in per_combo),
        },
        "per_combo": per_combo,
        "adzuna_only_company_sample": sorted(adz_new_companies)[:40],
    }

    print("\n" + "=" * 70)
    print(f"api calls          {len(cache)} (of {len(plan)} combos; "
          f"identical resolved queries reused)")
    print(f"adzuna distinct    {len(adz)}  ({result['adzuna']['duplicate_rate_pct']}% "
          f"duplicates within the replay)")
    print(f"shared / li-only / adzuna-only    "
          f"{len(shared)} / {len(li_only)} / {len(adz_only)}")
    if result["recovery_valid"]:
        print(f"recovery of useful LinkedIn rows  "
              f"{result['overlap']['recovery_of_useful_pct']}%  "
              f"({len(adz_useful & li_useful)} of {len(li_useful)})")
    else:
        print("recovery            NOT COMPUTED (partial combo set or quota stop)")
    print(f"new useful rows    {result['overlap']['new_useful']}")
    print(f"companies          adzuna {len(adz_companies)}, "
          f"not on linkedin {len(adz_only_companies)}, "
          f"new to Sweep entirely {len(adz_new_companies)}")
    print(f"field completeness {completeness}")
    print(f"description chars  adzuna median {desc_len.get('median')}")
    print(f"matched skills/row adzuna median {adz_skills.get('median')}, "
          f"linkedin median {li_skills.get('median')}")
    print(f"salary             {result['salary']['pct_disclosed']}% disclosed, "
          f"{predicted} predicted values dropped")
    print("=" * 70)

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
