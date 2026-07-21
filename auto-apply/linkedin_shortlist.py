"""
Write a clickable HTML shortlist of the top LinkedIn jobs to apply to.

Reuses selection.py to filter the latest ranked CSV to LinkedIn postings above
MIN_SCORE that aren't already in applications.csv, sorted by the CSV's order
(already score-desc). Open with `open auto-apply/shortlist.html` and click jobs
top-down.

Run:  python auto-apply/linkedin_shortlist.py
"""

import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apply_config as cfg
from selection import load_jobs, load_applied_keys, select_linkedin_candidates


def render_html(jobs):
    items = []
    for j in jobs:
        url = html.escape(j.get("apply_url", ""), quote=True)
        title = html.escape(j.get("title", ""))
        company = html.escape(j.get("company", ""))
        score = html.escape(j.get("score", ""))
        location = html.escape(j.get("location", ""))
        items.append(
            '<li><a href="' + url + '" target="_blank" rel="noopener">'
            "<b>" + score + "</b> — " + title + " @ " + company +
            " <small>(" + location + ")</small></a></li>"
        )
    body = "\n".join(items) or "<li>No LinkedIn jobs above threshold.</li>"
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<title>LinkedIn shortlist</title>"
        "<h1>LinkedIn jobs to apply to</h1>"
        "<ol>" + body + "</ol>"
    )


def generate():
    csv_path = cfg.latest_input_csv()
    if not csv_path:
        raise SystemExit("No input CSV in output/. Run the scraper or copy a CSV first.")
    rows = load_jobs(csv_path)
    applied = load_applied_keys(cfg.APPLICATIONS_LOG)
    jobs = select_linkedin_candidates(rows, cfg.MIN_SCORE, applied, limit=None)
    with open(cfg.SHORTLIST_OUT, "w", encoding="utf-8") as f:
        f.write(render_html(jobs))
    return cfg.SHORTLIST_OUT, len(jobs)


if __name__ == "__main__":
    path, n = generate()
    print("Wrote " + path + " (" + str(n) + " jobs)")
