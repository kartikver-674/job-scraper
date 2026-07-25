"""Re-score already-paid Apify runs against the CURRENT config — no new scraping.

Why this exists: scoring weights get tuned *after* a sweep has already run (that's
the normal loop — you only see that "Oracle Fusion Functional Consultant" outranks
everything once you look at real results). Re-scraping to pick up new weights pays
twice for the same rows. Apify retains each run's dataset server-side, so we can
pull the raw items back for free and re-run them through config.py as it stands now.

Reading a dataset costs no actor events — this is a free operation.

    python rescore_from_apify.py            # last 200 runs, writes output/jobs_rescored.*
    python rescore_from_apify.py --hours 6   # only runs started in the last 6 hours

Only runs whose actor is in config.SITES is considered, so unrelated Apify usage on
the same account is ignored.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from config import SITES, SETTINGS
from scraper import normalize, finalize, write_outputs, print_summary

load_dotenv()

# actor id -> site key, so a dataset can be normalized with the right adapter.
ACTOR_TO_SITE = {cfg["actor"]: key for key, cfg in SITES.items() if cfg.get("actor")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24,
                    help="only re-score runs started within this many hours (default 24)")
    ap.add_argument("--limit", type=int, default=200,
                    help="how many recent Apify runs to scan (default 200)")
    args = ap.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    # Datasets belong to the ACCOUNT that ran them, so a sweep that spilled from
    # APIFY_TOKEN onto APIFY_TOKEN_2 (free-credit exhaustion) has its results split
    # across two accounts. Scan every token we have, or half the paid rows go
    # missing with no error. Extra/duplicate tokens are harmless — dedupe runs at
    # the end anyway.
    tokens, seen_tokens = [], set()
    for name in ("APIFY_TOKEN", "APIFY_TOKEN_2", "APIFY_TOKEN_3"):
        tok = os.getenv(name)
        if tok and tok not in seen_tokens:
            seen_tokens.add(tok)
            tokens.append((name, tok))
    if not tokens:
        sys.exit("No APIFY_TOKEN* found in the environment or .env.")

    raw_rows, scanned, used = [], 0, 0
    for name, tok in tokens:
        print(f"\n[{name}]")
        rows, sc, us = _collect(tok, cutoff, args.limit)
        raw_rows.extend(rows)
        scanned += sc
        used += us

    print(f"\nscanned {scanned} runs across {len(tokens)} account(s), "
          f"used {used}, pulled {len(raw_rows)} raw rows")
    if not raw_rows:
        sys.exit("Nothing to re-score. Widen --hours/--limit, or check the token.")

    out_rows = finalize(raw_rows)
    # Write to jobs_combined.* — this IS the canonical merged shortlist, and
    # merge_jobs.py skips any filename containing "combined", so re-running the
    # merge can never fold this back in alongside the stale per-run files.
    csv_path = os.path.join(SETTINGS["output_dir"], "jobs_combined.csv")
    json_path = os.path.join(SETTINGS["output_dir"], "jobs_combined.json")
    write_outputs(out_rows, csv_path, json_path)
    print_summary(len(raw_rows), len(out_rows), out_rows)
    print(f"\nWrote {len(out_rows)} re-scored jobs to:\n  {csv_path}\n  {json_path}")


def _collect(token, cutoff, limit):
    """Pull + normalize every in-window job row from one Apify account.

    Returns (rows, runs_scanned, runs_used).
    """
    from apify_client import ApifyClient
    client = ApifyClient(token)
    rows, scanned, used = [], 0, 0

    # desc=True → newest first, so `limit` walks backwards from now.
    for run in client.runs().list(limit=limit, desc=True).items:
        scanned += 1
        # apify-client 3.x yields typed RunShort objects, NOT dicts (same trap the
        # scraper documents at scrape_search) — attribute access, snake_case.
        if run.status != "SUCCEEDED" or not run.default_dataset_id:
            continue
        if isinstance(run.started_at, datetime) and run.started_at < cutoff:
            continue
        site_key = ACTOR_TO_SITE.get(run.act_id) or _guess_site(client, run)
        if site_key is None:
            continue
        try:
            items = list(client.dataset(run.default_dataset_id).iterate_items())
        except Exception as exc:
            print(f"  ! dataset {run.default_dataset_id}: {exc}")
            continue
        rows.extend(normalize(item, site_key) for item in items)
        used += 1
        print(f"  {site_key:<9} {run.default_dataset_id}  {len(items):>3} raw items")
    return rows, scanned, used


def _guess_site(client, run):
    """Resolve a run's actor to a site key by looking up the actor's full name.

    client.runs().list() returns opaque actor IDs; SITES keys off "user/name"
    slugs. One extra lookup per unseen actor id, memoized in ACTOR_TO_SITE.
    """
    actor_id = run.act_id
    if actor_id in ACTOR_TO_SITE:
        return ACTOR_TO_SITE[actor_id]
    try:
        actor = client.actor(actor_id).get()
        # Typed Actor object in 3.x, same as RunShort — not a dict.
        slug = f"{actor.username}/{actor.name}"
    except Exception:
        slug = ""
    site_key = next((k for k, c in SITES.items() if c.get("actor") == slug), None)
    ACTOR_TO_SITE[actor_id] = site_key   # memoize misses too, so we ask once
    return site_key


if __name__ == "__main__":
    main()
