"""
Write a clickable, shareable HTML shortlist of the top jobs (all sources).

Reuses selection.py to filter the latest ranked CSV to postings above MIN_SCORE
that aren't already in applications.csv, sorted by score. The output is a
self-contained page (inline CSS, no external assets, no secrets) — open it
locally or hand it to someone / publish it as a link. Each job is a clickable
row showing its match score, title, company, location, and source.

Run:  python auto-apply/linkedin_shortlist.py
"""

import datetime
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apply_config as cfg
from selection import load_jobs, load_applied_keys, select_all_candidates

# Self-contained page fragment (inline styles; theme-aware via tokens). No
# <!doctype>/<html>/<head>/<body> so it publishes cleanly and still opens locally.
STYLE = """
<style>
  :root {
    --bg:#f6f8f8; --surface:#ffffff; --ink:#141d1c; --muted:#5a6a68;
    --line:#e6ecea; --accent:#0d7d74; --accent-ink:#ffffff;
    --hi:#0d7d74; --mid:#4f8f88; --lo:#8494; --lo-bg:#eef2f1;
    --seen-bg:#eff4f3;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0e1413; --surface:#151d1c; --ink:#e9efed; --muted:#94a6a3;
      --line:#26302f; --accent:#2dd4bf; --accent-ink:#06201d;
      --hi:#2dd4bf; --mid:#2a8f86; --lo:#7b8c8a; --lo-bg:#1b2423;
    --seen-bg:#111917;
    }
  }
  :root[data-theme="light"] {
    --bg:#f6f8f8; --surface:#ffffff; --ink:#141d1c; --muted:#5a6a68;
    --line:#e6ecea; --accent:#0d7d74; --accent-ink:#ffffff;
    --hi:#0d7d74; --mid:#4f8f88; --lo:#8494; --lo-bg:#eef2f1;
    --seen-bg:#eff4f3;
  }
  :root[data-theme="dark"] {
    --bg:#0e1413; --surface:#151d1c; --ink:#e9efed; --muted:#94a6a3;
    --line:#26302f; --accent:#2dd4bf; --accent-ink:#06201d;
    --hi:#2dd4bf; --mid:#2a8f86; --lo:#7b8c8a; --lo-bg:#1b2423;
    --seen-bg:#111917;
  }
  body { background:var(--bg); color:var(--ink);
    font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    line-height:1.5; -webkit-font-smoothing:antialiased; }
  .wrap { max-width:760px; margin:0 auto; padding:56px 20px 80px; }
  .eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:12px;
    font-weight:600; color:var(--accent); margin:0 0 10px; }
  h1 { font-size:clamp(26px,4vw,36px); font-weight:680; letter-spacing:-.02em;
    line-height:1.1; margin:0; text-wrap:balance; }
  .sub { color:var(--muted); font-size:14px; margin:10px 0 0;
    font-variant-numeric:tabular-nums; }
  .list { margin-top:32px; border:1px solid var(--line); border-radius:14px;
    overflow:hidden; background:var(--surface); }
  .job { display:grid; grid-template-columns:auto 1fr auto auto; align-items:center;
    gap:16px; padding:14px 18px; border-top:1px solid var(--line);
    text-decoration:none; color:inherit; transition:background .12s ease; }
  .job:first-child { border-top:none; }
  .job:hover { background:color-mix(in srgb, var(--accent) 7%, var(--surface)); }
  .job:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
  /* --- Opened/clicked state -------------------------------------------------
     Primary signal is JS + localStorage (survives reload, shows a real badge).
     The :visited rule is a fallback for when storage is unavailable (Safari on
     file://, private mode): browser history alone still recolours the row.
     Colour only — engines refuse other properties on :visited for privacy. */
  .job:visited { color:var(--muted); }
  .job.seen { background:var(--seen-bg); }
  .job.seen .title { color:var(--muted); font-weight:500; }
  .job.seen .score { opacity:.5; }
  .badge { visibility:hidden; font-size:10px; font-weight:700; letter-spacing:.06em;
    text-transform:uppercase; color:var(--accent); border:1px solid var(--accent);
    border-radius:999px; padding:2px 7px; white-space:nowrap; }
  .job.seen .badge { visibility:visible; }
  .reset { background:none; border:1px solid var(--line); color:var(--muted);
    font:inherit; font-size:12px; border-radius:8px; padding:4px 10px;
    cursor:pointer; }
  .reset:hover { color:var(--ink); border-color:var(--muted); }
  .score { min-width:34px; height:34px; padding:0 6px; border-radius:9px;
    display:inline-flex; align-items:center; justify-content:center;
    font-weight:700; font-size:14px; font-variant-numeric:tabular-nums;
    color:var(--accent-ink); background:var(--hi); }
  .score.mid { background:var(--mid); }
  .score.lo { background:var(--lo-bg); color:var(--lo); }
  .jb { min-width:0; }
  .title { display:block; font-weight:600; font-size:15px; letter-spacing:-.01em;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .meta { display:block; color:var(--muted); font-size:13px;
    white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .src { font-size:11px; font-weight:600; letter-spacing:.03em; color:var(--muted);
    text-transform:uppercase; white-space:nowrap; }
  .empty { padding:28px 18px; color:var(--muted); text-align:center; }
  .ft { margin-top:22px; color:var(--muted); font-size:12.5px; text-align:center; }
  @media (prefers-reduced-motion: reduce) { .job { transition:none; } }
</style>
"""


def _tier(score_str):
    try:
        s = int(float(score_str))
    except (ValueError, TypeError):
        return "lo"
    return "hi" if s >= 25 else ("mid" if s >= 15 else "lo")


def render_html(jobs, candidate="", generated_on=""):
    rows = []
    for j in jobs:
        url = html.escape(j.get("apply_url", ""), quote=True)
        title = html.escape(j.get("title", "") or "Untitled role")
        company = html.escape(j.get("company", "") or "—")
        score = html.escape(j.get("score", ""))
        location = html.escape(j.get("location", "") or "Location N/A")
        source = html.escape(j.get("source_site", ""))
        # data-jid is the identity used to remember "opened" across reloads. The
        # apply_url is the only genuinely stable per-job key here (titles and
        # companies repeat across postings), so it doubles as the storage key.
        rows.append(
            '<a class="job" href="' + url + '" target="_blank" rel="noopener"'
            ' data-jid="' + url + '">'
            '<span class="score ' + _tier(j.get("score", "")) + '">' + score + "</span>"
            '<span class="jb"><span class="title">' + title + "</span>"
            '<span class="meta">' + company + " · " + location + "</span></span>"
            '<span class="badge">opened</span>'
            '<span class="src">' + source + "</span></a>"
        )
    body = "\n".join(rows) or '<p class="empty">No jobs above threshold.</p>'
    who = (" for " + html.escape(candidate)) if candidate else ""
    sub = str(len(jobs)) + " roles"
    if generated_on:
        sub += " · " + html.escape(generated_on)
    sub += " · sorted by match"
    title_txt = "Job shortlist" + (" — " + html.escape(candidate) if candidate else "")
    main = (
        '<main class="wrap">'
        '<header><p class="eyebrow">Curated shortlist</p>'
        "<h1>Roles worth a look" + who + "</h1>"
        '<p class="sub">' + sub + ' · <span id="opened-count">none opened yet</span>'
        '</p></header>'
        '<div class="list">' + body + "</div>"
        '<p class="ft">Ranked by keyword match to the résumé. '
        "Click a role to open its application page — opened roles are marked and "
        'stay marked when you come back. <button class="reset" id="reset-opened" '
        'type="button">Clear opened</button></p>'
        "</main>"
    )
    # Complete, self-contained document — send the file to anyone; it opens in any
    # browser with no external assets. Light/dark follows the viewer's OS setting.
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>" + title_txt + "</title>\n"
        + STYLE +
        "</head>\n<body>\n" + main + "\n" + _script(candidate) + "\n</body>\n</html>\n"
    )


def _script(candidate):
    """Click-tracking: remember which roles were opened, across reloads.

    Kept vanilla and inline so the page stays a single self-contained file with no
    external assets (a CSP-safe Artifact publish and a plain file:// open both work).
    Storage is namespaced per candidate so two people's shortlists in the same
    browser don't share state. Every storage call is guarded — Safari on file:// and
    private mode throw on localStorage, and a shortlist that crashes is worse than
    one that merely forgets.
    """
    key = json.dumps("shortlist-opened:" + (candidate or "default"))
    return """<script>
(function () {
  var KEY = %s;
  function read() {
    try { return new Set(JSON.parse(localStorage.getItem(KEY) || "[]")); }
    catch (e) { return new Set(); }
  }
  function write(set) {
    try { localStorage.setItem(KEY, JSON.stringify(Array.from(set))); } catch (e) {}
  }
  var opened = read();
  var jobs = Array.prototype.slice.call(document.querySelectorAll(".job"));
  var count = document.getElementById("opened-count");
  function paint() {
    jobs.forEach(function (a) { a.classList.toggle("seen", opened.has(a.dataset.jid)); });
    if (count) {
      count.textContent = opened.size
        ? opened.size + " of " + jobs.length + " opened"
        : "none opened yet";
    }
  }
  jobs.forEach(function (a) {
    // 'click' (not the href navigation) so it also fires for middle-click and
    // cmd-click, which open a tab without ever unloading this page.
    a.addEventListener("click", function () { opened.add(a.dataset.jid); write(opened); paint(); });
    a.addEventListener("auxclick", function () { opened.add(a.dataset.jid); write(opened); paint(); });
  });
  var reset = document.getElementById("reset-opened");
  if (reset) {
    reset.addEventListener("click", function () { opened = new Set(); write(opened); paint(); });
  }
  paint();
})();
</script>""" % key


def generate(candidate="", out_path=None, csv_path=None):
    """Write the shortlist page and return (path, job_count).

    out_path defaults to cfg.SHORTLIST_OUT. Pass it to keep one person's shortlist
    from overwriting another's — the shortlists are per-résumé, but SHORTLIST_OUT is
    a single fixed filename.
    csv_path defaults to the newest output/jobs_combined*.csv.
    """
    csv_path = csv_path or cfg.latest_input_csv()
    if not csv_path:
        raise SystemExit("No input CSV in output/. Run the scraper or copy a CSV first.")
    out_path = out_path or cfg.SHORTLIST_OUT
    rows = load_jobs(csv_path)
    applied = load_applied_keys(cfg.APPLICATIONS_LOG)
    jobs = select_all_candidates(rows, cfg.MIN_SCORE, applied, limit=None)
    today = datetime.date.today().strftime("%d %b %Y")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(render_html(jobs, candidate=candidate, generated_on=today))
    return out_path, len(jobs)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    name = args[0] if args else ""
    path, n = generate(candidate=name, out_path=opts.get("out"), csv_path=opts.get("csv"))
    print("Wrote " + path + " (" + str(n) + " jobs)")
