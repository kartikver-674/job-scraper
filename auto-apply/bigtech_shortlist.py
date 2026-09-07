"""Clickable shortlist for the bigtech sweep, GROUPED BY EMPLOYER.

    python auto-apply/bigtech_shortlist.py            # newest output/bigtech CSV
    python auto-apply/bigtech_shortlist.py --top=8    # per employer

Why this exists instead of reusing linkedin_shortlist.render_html: that page is
one flat list sorted by score, and here a flat list would be actively
MISLEADING. Two of the five boards (JPMorgan and Oracle, both Oracle Recruiting
Cloud) publish no full job description — sources/enterprise.py documents the
400 — so their rows are scored on a title plus a ~100-character blurb, while
Amazon, Accenture and SAP rows are scored on 4,000-15,000 characters. More text
means more matched keywords means a higher score, so sorting all five together
ranks by "how much did this employer publish", not by fit.

Grouping by employer makes that comparison honest: within a group every row was
scored on the same kind of text, so the ordering means something. The badge on
the ORC groups says so on the page rather than only here.

Style and click-tracking are imported from linkedin_shortlist so both pages
stay one look and one behaviour.
"""
import csv
import datetime
import glob
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from linkedin_shortlist import STYLE, _script, _tier   # noqa: E402

# Employers whose board publishes no full JD — see the module docstring.
SHORT_TEXT = {"JPMorgan Chase", "Oracle"}

# Reachability order, not score order. Amazon and Accenture lead because they
# state real experience bars and actually post roles at 1-2 years; SAP is last
# because every role it returned wants 10+ whatever it scored. Employers absent
# from the CSV are skipped silently.
ORDER = ["Amazon", "Accenture", "Microsoft", "IBM", "Capgemini", "Deloitte",
         "JPMorgan Chase", "Siemens", "Oracle", "SAP"]


def latest_csv():
    files = glob.glob("output/bigtech/jobs_*.csv")
    return max(files, key=os.path.getmtime) if files else None


def _exp_chip(row):
    """The single most decision-relevant field for a 2-year candidate.

    Blank means the posting never stated a figure, which is NOT the same as "no
    requirement" — so it says "not stated" rather than showing nothing.
    """
    years = (row.get("experience_required") or "").strip()
    if not years:
        return '<span class="chip">exp not stated</span>'
    try:
        n = int(float(years))
    except ValueError:
        return '<span class="chip">' + html.escape(years) + "</span>"
    cls = "chip ok" if n <= 3 else ("chip warn" if n <= 5 else "chip bad")
    return f'<span class="{cls}">{n}+ yrs</span>'


EXTRA_CSS = """
<style>
  .chip { display:inline-block; font-size:11px; font-weight:600; padding:2px 7px;
    border-radius:99px; background:var(--lo-bg); color:var(--muted);
    margin-left:8px; white-space:nowrap; }
  .chip.ok   { background:rgba(13,125,116,.14); color:var(--accent); }
  .chip.warn { background:rgba(190,140,20,.16); color:#8a6400; }
  .chip.bad  { background:rgba(190,60,60,.14); color:#9c3232; }
  @media (prefers-color-scheme: dark) {
    .chip.warn { color:#e8c06a; } .chip.bad { color:#f0908c; }
  }
  h2.emp { font-size:19px; font-weight:650; letter-spacing:-.01em;
    margin:34px 0 4px; display:flex; align-items:baseline; gap:10px;
    flex-wrap:wrap; }
  h2.emp .n { font-size:13px; font-weight:500; color:var(--muted); }
  .note { font-size:12.5px; color:var(--muted); margin:0 0 12px;
    padding:8px 11px; border-left:2px solid var(--line); background:var(--lo-bg);
    border-radius:0 6px 6px 0; }
  .job .meta { display:block; }
</style>
"""


def render(groups, generated_on, total):
    out = []
    # ORDER is a preference, not a whitelist. Anything not named in it still has
    # to render — iterating ORDER alone silently DROPPED every employer added by
    # a later sweep (MongoDB, GitLab, Roku, Twilio all vanished from the page
    # while sitting in the CSV). Unlisted employers follow, best score first.
    listed = [c for c in ORDER if groups.get(c)]
    rest = sorted((c for c in groups if c not in ORDER),
                  key=lambda c: -max(float(r.get("score") or 0) for r in groups[c]))
    for company in listed + rest:
        rows = groups.get(company) or []
        if not rows:
            continue
        out.append(f'<h2 class="emp">{html.escape(company)}'
                   f'<span class="n">{len(rows)} shown</span></h2>')
        if company in SHORT_TEXT:
            out.append('<p class="note">This board publishes no full job '
                       'description, so these are scored on the title and a '
                       'one-line blurb only. Their scores are not comparable '
                       'with the groups above — read the titles, not the '
                       'numbers.</p>')
        for r in rows:
            url = html.escape(r.get("apply_url", ""), quote=True)
            req = (r.get("req_number") or "").strip()
            src = "REQ " + req if req else (r.get("source_site") or "")
            posted = (r.get("date_posted") or "")[:10]
            meta = " · ".join(x for x in [r.get("location") or "", posted] if x)
            out.append(
                '<a class="job" href="' + url + '" target="_blank" rel="noopener"'
                ' data-jid="' + url + '">'
                '<span class="score ' + _tier(r.get("score", "")) + '">'
                + html.escape(r.get("score", "")) + "</span>"
                '<span class="jb"><span class="title">'
                + html.escape(r.get("title") or "Untitled role")
                + _exp_chip(r) + "</span>"
                '<span class="meta">' + html.escape(meta) + "</span></span>"
                '<span class="badge">opened</span>'
                '<span class="src">' + html.escape(src) + "</span></a>")

    body = "\n".join(out) or '<p class="empty">Nothing matched.</p>'
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Big-tech India shortlist</title>\n" + STYLE + EXTRA_CSS +
        '</head>\n<body>\n<main class="wrap">'
        '<header><p class="eyebrow">Curated shortlist</p>'
        "<h1>Big-employer roles in India</h1>"
        f'<p class="sub">{total} ranked · {html.escape(generated_on)} · '
        'grouped by employer · <span id="opened-count">none opened yet</span>'
        "</p></header>" + body +
        '<p class="ft">Scored against the résumé; the chip is the experience the '
        "posting demands. Click a role to open it — opened roles stay marked. "
        '<button class="reset" id="reset-opened" type="button">Clear opened'
        "</button></p></main>\n" + _script("bigtech") + "\n</body>\n</html>\n")


def demo():
    """Self-check for the one failure this file had: ORDER read as a whitelist,
    so every employer a later sweep added was in the CSV and off the page."""
    groups = {"Amazon": [{"score": "10", "title": "A", "apply_url": "u"}],
              "Zzz Ltd": [{"score": "40", "title": "Z", "apply_url": "u"}],
              "Mmm Inc": [{"score": "50", "title": "M", "apply_url": "u"}]}
    page = render(groups, "2026-01-01", 3)
    for company in groups:
        assert f">{company}<" in page, f"{company} dropped from the page"
    # Listed employers keep ORDER; the rest follow, best score first.
    assert page.index(">Amazon<") < page.index(">Mmm Inc<") < page.index(">Zzz Ltd<")
    print("bigtech_shortlist demo ok")


def main():
    opts = dict(a[2:].split("=", 1) for a in sys.argv[1:]
                if a.startswith("--") and "=" in a)
    top = int(opts.get("top", 8))
    skip = {s.strip() for s in opts.get("skip", "").split(",") if s.strip()}
    path = opts.get("csv") or latest_csv()
    if not path:
        sys.exit("No output/bigtech/jobs_*.csv yet — run the sweep first.")

    rows = [r for r in csv.DictReader(open(path, encoding="utf-8-sig"))
            if r["company"] not in skip]
    groups = {}
    for r in rows:
        groups.setdefault(r["company"], []).append(r)
    for company in groups:
        groups[company].sort(key=lambda r: -float(r.get("score") or 0))
        groups[company] = groups[company][:top]

    out_path = "auto-apply/shortlist-bigtech.html"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(render(groups, datetime.date.today().isoformat(), len(rows)))
    shown = sum(len(v) for v in groups.values())
    print(f"{out_path}: {shown} of {len(rows)} roles, "
          f"{len(groups)} employers (from {path})")
    return out_path


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
