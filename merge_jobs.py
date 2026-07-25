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
from scraper import dedupe

# Honors --profile, so a merge can't silently pick up the default profile's files
# while you're working on someone else's sweep.
OUT_DIR = SETTINGS["output_dir"]


def merge_rows(rows):
    """Sort best-first, then drop duplicate postings (highest score wins)."""
    return dedupe(sorted(rows, key=lambda r: r.get("score", 0), reverse=True))


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
    files = [f for f in sorted(glob.glob(os.path.join(OUT_DIR, "jobs_*.json")))
             if "combined" not in f]
    if not files:
        sys.exit("No output/jobs_*.json files to merge.")
    merged = merge(files)
    json_path = os.path.join(OUT_DIR, "jobs_combined.json")
    csv_path = os.path.join(OUT_DIR, "jobs_combined.csv")
    with open(json_path, "w") as fh:
        json.dump(merged, fh, indent=2, ensure_ascii=False)
    with open(csv_path, "w", newline="") as fh:
        if merged:
            w = csv.DictWriter(fh, fieldnames=list(merged[0].keys()))
            w.writeheader()
            w.writerows(merged)
    print(f"Merged {len(files)} files -> {len(merged)} unique jobs (ranked by score)")
    print(f"  {json_path}\n  {csv_path}")
