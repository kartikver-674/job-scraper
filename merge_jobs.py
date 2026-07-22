"""Merge every output/jobs_*.json into one file, deduped and ranked by score.

Same dedup key as scraper.py's dedupe() — normalized (title, company, location),
highest score wins. Run after a sweep: python merge_jobs.py
"""
import csv
import glob
import json
import os
import re
import sys

OUT_DIR = "output"


def _norm_key(value):  # mirrors scraper._norm_key
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def merge_rows(rows):
    """Sort best-first, drop dups on normalized (title, company, location)."""
    rows = sorted(rows, key=lambda r: r.get("score", 0), reverse=True)
    seen, unique = set(), []
    for r in rows:
        key = (_norm_key(r.get("title")), _norm_key(r.get("company")),
               _norm_key(r.get("location")))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


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
    out = merge_rows([a, b, c])
    assert [r["score"] for r in out] == [9, 7], out  # b beats a (dup), sorted desc
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
