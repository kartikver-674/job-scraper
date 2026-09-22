"""Does fetching past SmartRecruiters row 100 materially improve useful inventory?

AUDIT-ONLY. Public SmartRecruiters API, no auth, no Apify, zero credits. It does
NOT change production pagination: sources/ats.py still requests limit=100 and
still takes the first page. This harness asks whether that ceiling costs
anything a candidate would have wanted.

The audit measured five current boards reporting far more inventory than was
fetched — Renesas 100/917, Sia 100/591, Metro 100/1,622, Informa 100/161,
Version1 100/155 — for at least 2,946 advertised rows nobody has ever looked at.
"Advertised rows" is not "useful jobs", which is the whole point of measuring
rather than assuming.

    .venv/bin/python -m bench.search_v2_smartrecruiters_pages --live \\
        --output docs/search-v2-evidence/smartrecruiters-pagination.json

Without --live it prints the exact request list and exits, so the cost of a run
is reviewable before it happens. Bounded by construction: an explicit board
list, at most MAX_PAGES pages each, one attempt per request, 20s timeout,
PACE_SECONDS between requests, and any 429 stops the whole harness rather than
retrying into a rate limit.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
from collections import Counter
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]

ENDPOINT = ("https://api.smartrecruiters.com/v1/companies/{token}/postings"
            "?limit={limit}&offset={offset}")
PAGE = 100
MAX_PAGES = 4               # rows 1–400 per board. Not thousands of pages.
PACE_SECONDS = 0.5
TIMEOUT = 20

# Five boards ALREADY in config.ATS_BOARDS whose first page the audit measured
# as truncated, spanning three orders of advertised depth so the answer is not
# read off one unusually large employer.
BOARDS = {
    "renesaselectronics": "Renesas Electronics",   # audit: 100 of 917
    "sia": "Sia",                                  # audit: 100 of 591
    "metromakro": "METRO/MAKRO",                   # audit: 100 of 1,622
    "informagroupplc": "Informa Group Plc.",       # audit: 100 of 161
    "version1": "Version 1",                       # audit: 100 of 155
}


def requests_planned():
    return [ENDPOINT.format(token=t, limit=PAGE, offset=p * PAGE)
            for t in BOARDS for p in range(MAX_PAGES)]


def load_engine(case="software_fullstack", scope="india"):
    """The unchanged engine under one synthetic fixture, so "eligible" here means
    exactly what it means in the audit's replays."""
    sys.path[:0] = [str(ROOT), str(ROOT / "auto-apply")]
    os.environ.pop("JOB_PROFILE", None)
    os.environ["SWEEP_EXPERIENCE_MISMATCH_GUARD"] = "0"
    for key in [k for k in os.environ if k.startswith("APIFY_TOKEN")]:
        del os.environ[key]
    fixtures = json.loads(
        (ROOT / "docs/search-v2-evidence/paid-plans.json").read_text())
    fixture = next(f for f in fixtures["synthetic_profile_fixtures"]
                   if f["case"] == case)
    prefs = dict(fixture["prefs"], work_scope=scope,
                 remote_scopes=[] if scope == "india" else ["worldwide", "remote"])
    import make_profile
    import config
    module = ModuleType("synthetic_sr_pages")
    rendered = make_profile.render("synthetic_sr_pages", fixture["derived"], prefs)
    exec(compile(rendered, "<synthetic sr pages profile>", "exec"), module.__dict__)
    config._overlay(module)
    import scraper
    assert not scraper.experience_guard.enabled()
    return scraper, hashlib.sha256(rendered.encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="actually issue the requests listed by a dry run")
    ap.add_argument("--output")
    ap.add_argument("--case", default="software_fullstack")
    ap.add_argument("--scope", default="india", choices=["india", "remote"])
    args = ap.parse_args()

    planned = requests_planned()
    if not args.live:
        print(f"{len(planned)} public GET requests, 0 Apify credits, "
              f"{PACE_SECONDS}s pacing, {TIMEOUT}s timeout, one attempt each:")
        for url in planned:
            print(f"  {url}")
        print("\n(dry run — pass --live to execute)")
        return

    scraper, policy_sha = load_engine(args.case, args.scope)
    from sources import ats
    from sources._http import get_json
    spec = ats.ATS["smartrecruiters"]

    boards = {}
    rate_limited = False
    for token, company in BOARDS.items():
        if rate_limited:
            break
        seen_keys = set()
        pages = []
        rows_by_depth = {}          # page index -> cumulative normalized rows
        cumulative = []
        advertised = None
        for page in range(MAX_PAGES):
            url = ENDPOINT.format(token=token, limit=PAGE, offset=page * PAGE)
            started = time.perf_counter()
            try:
                # retries=0: a failed page is DATA here. Retrying would hide the
                # provider's actual behaviour behind this harness's persistence.
                data = get_json(url, timeout=TIMEOUT, retries=0)
            except urllib.error.HTTPError as exc:
                pages.append({"page": page, "offset": page * PAGE,
                              "error": f"http_{exc.code}",
                              "elapsed_ms": round((time.perf_counter() - started) * 1000)})
                if exc.code == 429:
                    # Stop pressure entirely. A 429 is an instruction, not an
                    # obstacle to work around.
                    rate_limited = True
                break
            except Exception as exc:
                pages.append({"page": page, "offset": page * PAGE,
                              "error": type(exc).__name__,
                              "elapsed_ms": round((time.perf_counter() - started) * 1000)})
                break
            elapsed = round((time.perf_counter() - started) * 1000)
            items = data.get("content") or []
            advertised = data.get("totalFound")
            rows = [ats._row(i, "smartrecruiters", token, company, spec)
                    for i in items]
            keys = [scraper.job_key(r) for r in rows]
            marginal = sum(1 for k in keys if k not in seen_keys)
            duplicate = len(keys) - marginal
            seen_keys.update(k for k in keys if k is not None)
            cumulative += rows
            rows_by_depth[page] = list(cumulative)
            pages.append({
                "page": page,
                "offset": page * PAGE,
                "elapsed_ms": elapsed,
                "returned": len(items),
                "advertised_total": advertised,
                "fresh_14d": sum(
                    scraper._parse_date(r.get("Posted Date")) is not None
                    and scraper.is_recent(r.get("Posted Date"), 14) for r in rows),
                "marginal_unique_vs_earlier_pages": marginal,
                "duplicate_overlap_with_earlier_pages": duplicate,
                "candidate_gated": sum(
                    1 for r in rows
                    if scraper.is_dev_title(r.get("Title", ""))
                    and scraper.location_allowed(r.get("Location", ""))),
                "error": "",
            })
            if len(items) < PAGE:
                break                       # board exhausted; stop asking
            time.sleep(PACE_SECONDS)

        # Eligibility is only meaningful cumulatively: score+filter+dedupe the
        # whole set the engine would hold at each depth and compare.
        depth_eligibility = {}
        for page, rows in rows_by_depth.items():
            gated = [scraper._truncate_desc(dict(r)) for r in rows
                     if scraper.is_dev_title(r.get("Title", ""))
                     and scraper.location_allowed(r.get("Location", ""))]
            final = scraper.finalize(gated)
            depth_eligibility[f"rows_1_to_{(page + 1) * PAGE}"] = {
                "normalized": len(rows),
                "candidate_gated": len(gated),
                "eligible_final": len(final),
                "positive_score": sum(r["score"] > 0 for r in final),
                "score_at_least_10": sum(r["score"] >= 10 for r in final),
            }
        boards[token] = {"company": company, "advertised_total": advertised,
                         "pages": pages, "by_depth": depth_eligibility}

    first = {}
    beyond = {}
    for token, board in boards.items():
        depths = list(board["by_depth"])
        if not depths:
            continue
        first[token] = board["by_depth"][depths[0]]
        beyond[token] = board["by_depth"][depths[-1]]

    out = {
        "status": "MEASURED live public SmartRecruiters API; audit-only, "
                  "production pagination unchanged",
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fixture": f"{args.case}/{args.scope}",
        "policy_sha256": policy_sha,
        "page_size": PAGE, "max_pages": MAX_PAGES,
        "requests_issued": sum(len(b["pages"]) for b in boards.values()),
        "apify_cost_usd": 0,
        "rate_limited": rate_limited,
        "boards": boards,
        "summary": {
            "first_100_only": {
                "normalized": sum(v["normalized"] for v in first.values()),
                "candidate_gated": sum(v["candidate_gated"] for v in first.values()),
                "eligible_final": sum(v["eligible_final"] for v in first.values()),
                "positive_score": sum(v["positive_score"] for v in first.values()),
            },
            "all_pages_fetched": {
                "normalized": sum(v["normalized"] for v in beyond.values()),
                "candidate_gated": sum(v["candidate_gated"] for v in beyond.values()),
                "eligible_final": sum(v["eligible_final"] for v in beyond.values()),
                "positive_score": sum(v["positive_score"] for v in beyond.values()),
            },
            "note": "Per-board sums, not a deduplicated cross-board total, and "
                    "not deduplicated against the other 129 boards or the paid "
                    "half. Cross-source overlap can only reduce these figures.",
        },
        "limits": [
            "One dated observation. Board inventory changes daily.",
            "Eligibility is one synthetic fixture's. A different cohort can have a "
            "different depth curve, and business/early-career cohorts are untested.",
            "SmartRecruiters list rows carry no description, so every row here is "
            "scored on its TITLE alone — depth and JD enrichment are entangled, and "
            "a low eligible count is not proof the jobs are irrelevant.",
            "advertised_total is the provider's claim, not verified inventory.",
            "This measures inventory, not request budget: extra pages cost time on "
            "every sweep whether or not they yield.",
        ],
    }
    if args.output:
        Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out["summary"], indent=2))
    for token, board in boards.items():
        got = [p for p in board["pages"] if not p.get("error")]
        print(f"{token:<22} advertised {str(board['advertised_total']):>6}  "
              f"pages {len(got)}  "
              f"returned {sum(p['returned'] for p in got):>4}  "
              f"marginal {sum(p['marginal_unique_vs_earlier_pages'] for p in got):>4}  "
              f"gated {sum(p['candidate_gated'] for p in got):>3}")


if __name__ == "__main__":
    main()
