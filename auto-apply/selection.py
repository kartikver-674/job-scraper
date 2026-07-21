"""
Turn the ranked scraper CSV into the list of jobs we'll draft for: rows that have
a recruiter email, clear the score threshold, and haven't been applied to yet.
The job_key is the apply_url (stable + unique per posting).
"""

import csv


def load_jobs(csv_path):
    """Read the jobs CSV (BOM-tolerant) into a list of dict rows."""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def job_key(row):
    """Stable unique key for a posting."""
    return (row.get("apply_url") or "").strip()


def _score(row):
    try:
        return int(float((row.get("score") or "").strip()))
    except ValueError:
        return 0


def load_applied_keys(applications_csv):
    """Return the set of job_keys already recorded in applications.csv (any status)."""
    import os
    if not os.path.exists(applications_csv):
        return set()
    with open(applications_csv, "r", encoding="utf-8", newline="") as f:
        return {(r.get("job_key") or "").strip()
                for r in csv.DictReader(f) if (r.get("job_key") or "").strip()}


def select_candidates(rows, min_score, applied_keys, limit):
    """Filter to emailable, above-threshold, not-yet-applied rows; keep order; apply limit."""
    picked = []
    for row in rows:
        if not (row.get("hr_email") or "").strip():
            continue
        if _score(row) < min_score:
            continue
        if job_key(row) in applied_keys:
            continue
        picked.append(row)
    if limit is not None:
        picked = picked[:limit]
    return picked


def select_all_candidates(rows, min_score, applied_keys, limit):
    """Filter to any-source jobs with an apply_url, above-threshold, not-yet-applied;
    keep order; apply limit."""
    picked = []
    for row in rows:
        if not job_key(row):
            continue
        if _score(row) < min_score:
            continue
        if job_key(row) in applied_keys:
            continue
        picked.append(row)
    if limit is not None:
        picked = picked[:limit]
    return picked


def select_linkedin_candidates(rows, min_score, applied_keys, limit):
    """Filter to LinkedIn jobs, above-threshold, not-yet-applied; keep order; apply limit."""
    picked = []
    for row in rows:
        if (row.get("source_site") or "").strip().lower() != "linkedin":
            continue
        if _score(row) < min_score:
            continue
        if job_key(row) in applied_keys:
            continue
        picked.append(row)
    if limit is not None:
        picked = picked[:limit]
    return picked
