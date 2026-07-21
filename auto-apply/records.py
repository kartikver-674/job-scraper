"""
Persistence for drafts and tracking:
- drafts/<slug>.md      : human-readable draft (subject/body/grounding notes)
- review_queue.csv      : job_key,to,subject,draft_path,status (draft->approved->sent)
- applications.csv      : job_key,company,title,channel,status,timestamp (idempotent)
"""

import csv
import os
import re
from datetime import datetime

REVIEW_FIELDS = ["job_key", "to", "subject", "draft_path", "status"]
APP_FIELDS = ["job_key", "company", "title", "channel", "status", "timestamp"]


def _slug(job_key):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", job_key)[-120:] or "job"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def write_draft_md(drafts_dir, job_key, to, subject, body, grounding_notes):
    """Write a readable .md draft and return its path."""
    os.makedirs(drafts_dir, exist_ok=True)
    path = os.path.join(drafts_dir, _slug(job_key) + ".md")
    content = (
        f"# Draft application\n\n"
        f"**To:** {to}\n\n"
        f"**Subject:** {subject}\n\n"
        f"---\n\n{body}\n\n"
        f"---\n\n"
        f"**Grounding notes (reviewer aid — not sent):**\n\n{grounding_notes}\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _read_rows(path, fields):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_rows(path, fields, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fields})


def append_review_row(path, row):
    rows = _read_rows(path, REVIEW_FIELDS)
    if any(r.get("job_key") == row["job_key"] for r in rows):
        return  # idempotent: don't duplicate a queued job
    rows.append(row)
    _write_rows(path, REVIEW_FIELDS, rows)


def read_review_rows(path):
    return _read_rows(path, REVIEW_FIELDS)


def set_review_status(path, job_key, status):
    rows = _read_rows(path, REVIEW_FIELDS)
    for r in rows:
        if r.get("job_key") == job_key:
            r["status"] = status
    _write_rows(path, REVIEW_FIELDS, rows)


def upsert_application(path, job_key, company, title, channel, status):
    """Insert a new application row, or update status+timestamp if job_key exists."""
    rows = _read_rows(path, APP_FIELDS)
    for r in rows:
        if r.get("job_key") == job_key:
            r["status"] = status
            r["timestamp"] = _now()
            _write_rows(path, APP_FIELDS, rows)
            return
    rows.append({"job_key": job_key, "company": company, "title": title,
                 "channel": channel, "status": status, "timestamp": _now()})
    _write_rows(path, APP_FIELDS, rows)
