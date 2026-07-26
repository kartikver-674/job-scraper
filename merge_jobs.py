"""Merge every output/jobs_*.json into one file, deduped and ranked by score.

Reuses scraper.dedupe()/job_key() rather than keeping a second copy of the
dedupe rule — the two drifting apart is how the same job ends up in the merged
file twice. Run after a sweep:

    python merge_jobs.py                      # merges output/
    python merge_jobs.py --profile srishti     # merges output/srishti/
"""
import csv
import glob
import json
import os
import sys

from config import SETTINGS
from scraper import OUTPUT_COLUMNS, dedupe

# Honors --profile, so a merge can't silently pick up the default profile's files
# while you're working on someone else's sweep.
OUT_DIR = SETTINGS["output_dir"]


def merge_rows(rows):
    """Sort best-first, then drop duplicate postings (highest score wins)."""
    return dedupe(sorted(rows, key=lambda r: r.get("score", 0), reverse=True))


def csv_columns(rows):
    """Canonical column order, plus anything unexpected the rows happen to carry.

    Derived from scraper.OUTPUT_COLUMNS rather than from rows[0], because a merge
    spans files written at different times: an older sweep predates a column that
    a newer one has, and keying off the first row alone made DictWriter raise
    partway through — leaving a truncated jobs_combined.csv behind that looked
    like a successful merge to everything downstream.
    """
    cols = list(OUTPUT_COLUMNS)
    cols += [k for r in rows for k in r if k not in cols]
    return cols


def merge(files):
    rows = []
    for f in files:
        with open(f) as fh:
            rows.extend(json.load(fh))
    return merge_rows(rows)


def demo():
    a = {"title": "Full Stack Dev", "company": "X", "location": "Delhi", "score": 5}
    b = {"title": "full-stack  dev", "company": "x", "location": "delhi", "score": 9}
    c = {"title": "React Dev", "company": "Y", "location": "Remote", "score": 7}
    # Same job, different source, different location text — one row now, not two.
    d = {"title": "Dev Full Stack", "company": "X Ltd", "location": "Worldwide", "score": 3}
    out = merge_rows([a, b, c, d])
    assert [r["score"] for r in out] == [9, 7], out  # b beats a and d, sorted desc
    print("demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit()
    # --all spans every profile directory as well as output/ itself, so one
    # shortlist can cover several sweeps. Dedupe is by company+title, so the same
    # posting found by two profiles collapses to its highest-scoring copy.
    if "--all" in sys.argv:
        # archive-* is skipped deliberately. Those directories hold finished
        # sweeps — including OTHER PEOPLE's searches — scored under whatever
        # config was current at the time. Merging them put another person's
        # Salesforce roles at the top of a shortlist that explicitly penalizes
        # Salesforce, because merge reuses stored scores and cannot re-score
        # (the description isn't kept in the output rows). Move a directory to
        # archive-<name>/ when you want it out of --all.
        roots = ["output"] + sorted(
            d for d in glob.glob(os.path.join("output", "*"))
            if os.path.isdir(d) and not os.path.basename(d).startswith("archive"))
        out_dir = "output"
        out_name = "jobs_all"
    else:
        roots, out_dir, out_name = [OUT_DIR], OUT_DIR, "jobs_combined"

    files = [f for r in roots
             for f in sorted(glob.glob(os.path.join(r, "jobs_*.json")))
             if "combined" not in f and "jobs_all" not in f]
    if not files:
        sys.exit(f"No jobs_*.json files to merge under {', '.join(roots)}.")
    merged = merge(files)
    json_path = os.path.join(out_dir, f"{out_name}.json")
    csv_path = os.path.join(out_dir, f"{out_name}.csv")
    with open(json_path, "w") as fh:
        json.dump(merged, fh, indent=2, ensure_ascii=False)
    with open(csv_path, "w", newline="") as fh:
        if merged:
            w = csv.DictWriter(fh, fieldnames=csv_columns(merged))
            w.writeheader()
            # Fill per row: files written before a column existed simply lack the
            # key, and DictWriter raises on a missing fieldname.
            cols = csv_columns(merged)
            w.writerows({c: r.get(c, "") for c in cols} for r in merged)
    print(f"Merged {len(files)} files -> {len(merged)} unique jobs (ranked by score)")
    print(f"  {json_path}\n  {csv_path}")
