"""Merge every output/jobs_*.json into one file, deduped and ranked by score.

Reuses scraper.dedupe()/job_key() rather than keeping a second copy of the
dedupe rule — the two drifting apart is how the same job ends up in the merged
file twice. Run after a sweep:

    python merge_jobs.py                      # merges output/
    python merge_jobs.py --profile srishti     # merges output/srishti/
"""
import csv
import datetime
import glob
import json
import os
import sys

from config import SETTINGS
from scraper import (OUTPUT_COLUMNS, blocked_company, dedupe, is_recent,
                     reachable)

# Honors --profile, so a merge can't silently pick up the default profile's files
# while you're working on someone else's sweep.
OUT_DIR = SETTINGS["output_dir"]


def applyable(row):
    """Drop rows a current-config sweep would never have reported.

    A merge spans files written under older configs, and it cannot re-score them
    (the description isn't kept in the output rows). But the reachability and
    repost-farm rules read only stored columns, so unlike scoring they CAN be
    re-applied here — and they must be, because everything downstream (the
    shortlist page, auto-apply) reads the merged file, not the per-run one. Left
    off, a shortlist built today still serves the remote-in-Germany roles that
    the scraper itself now filters out.

    Rows from files written before remote_scope existed lack the key entirely and
    are kept: absent means "this sweep never asked", not "no".

    AGE is re-applied for the same reason, and it bites hardest: the oldest file
    in a merge can predate the newest by over a month, so a page built from the
    merged CSV was offering postings the scraper had already stopped reporting as
    stale (measured: 5-week-old rows from an archived paid run, sitting above
    today's, because they scored well). is_recent honours SETTINGS["drop_undated"],
    so an unparseable date is still "the posting didn't say", not "too old".
    """
    if blocked_company(row):
        return False
    if SETTINGS["remote_scopes"] and "remote_scope" in row:
        return reachable(row)
    if SETTINGS["max_age_days"] is not None:
        return is_recent(row.get("date_posted") or row.get("Posted Date"),
                         SETTINGS["max_age_days"])
    return True


def merge_rows(rows):
    """Sort best-first, drop duplicate postings (highest score wins), then drop
    what the current config wouldn't report."""
    ranked = dedupe(sorted(rows, key=lambda r: r.get("score", 0), reverse=True))
    return [r for r in ranked if applyable(r)]


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
    # No remote_scope key at all -> pre-column file, kept rather than deleted.
    assert len(merge_rows([dict(c, title="Solo Dev")])) == 1

    # Reachability, re-applied to rows that carry the column. PINNED, the way
    # scraper.demo() pins it: these asserts describe what applyable() does when
    # the filter is ON, so read straight from the live config they passed only for
    # a profile that happens to enable it and failed for one that doesn't — a
    # check that depends on whose résumé is loaded isn't a check.
    orig = SETTINGS["remote_scopes"], SETTINGS["keep_restricted_if_hires_home"]
    SETTINGS["remote_scopes"] = ["worldwide", "remote"]
    SETTINGS["keep_restricted_if_hires_home"] = True
    try:
        scoped = lambda **kw: dict(  # noqa: E731
            {"title": "React Dev", "company": "Z", "score": 8}, **kw)
        assert len(merge_rows([scoped(remote_scope="worldwide")])) == 1
        assert len(merge_rows([scoped(remote_scope="restricted", remote_regions="Germany")])) == 0
        # Locked TO home, or an employer that hires here: both still applyable.
        assert len(merge_rows([scoped(remote_scope="restricted", remote_regions="India")])) == 1
        assert len(merge_rows([scoped(remote_scope="restricted", hires_home="yes")])) == 1
        # A repost farm goes whatever its scope says.
        assert len(merge_rows([scoped(company="Hired", remote_scope="worldwide")])) == 0
        # Filter OFF: the geo-locked row comes back, the repost farm still goes.
        SETTINGS["remote_scopes"] = []
        assert len(merge_rows([scoped(remote_scope="restricted", remote_regions="Germany")])) == 1
        assert len(merge_rows([scoped(company="Hired", remote_scope="worldwide")])) == 0

        # Age, re-applied across files of different vintages. Pinned too, since
        # max_age_days is per-résumé and None is a legal value.
        age, undated = SETTINGS["max_age_days"], SETTINGS["drop_undated"]
        try:
            SETTINGS["max_age_days"], SETTINGS["drop_undated"] = 21, False
            old_day = (datetime.date.today()
                       - datetime.timedelta(days=40)).isoformat()
            new_day = datetime.date.today().isoformat()
            assert len(merge_rows([scoped(date_posted=new_day)])) == 1
            assert len(merge_rows([scoped(date_posted=old_day)])) == 0
            assert len(merge_rows([scoped()])) == 1          # undated -> kept
            SETTINGS["max_age_days"] = None                  # filter off -> kept
            assert len(merge_rows([scoped(date_posted=old_day)])) == 1
        finally:
            SETTINGS["max_age_days"], SETTINGS["drop_undated"] = age, undated
    finally:
        SETTINGS["remote_scopes"], SETTINGS["keep_restricted_if_hires_home"] = orig
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
