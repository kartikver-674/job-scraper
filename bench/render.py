"""Turn bench/people.py into résumé PDFs across four layouts.

The documents are generated from the answer key rather than written beside
it, so the two cannot drift. Every person is rendered four ways with the
SAME facts, which is what makes layout a controlled variable: when a parser
reads chen correctly as one column and badly as two, that is the layout,
not the content.

  plain    single column, generous leading — the control
  twocol   sidebar for skills/education, the layout ATSs are known to lose
  tables   everything in a grid, including employment history
  messy    the awkward PDF: tight leading, justified text, letter-spacing,
           a running header, and dates in three different formats

Chrome does the PDF, so nothing new is installed — it is already here for
the screenshot work, and an HTML-to-PDF résumé is what most real ones are.

    python -m bench.render          # build every PDF
    python -m bench.render --demo   # self-check, writes nothing
"""

import html
import os
import subprocess
import sys

from bench.people import PEOPLE, truth

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "resumes")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

LAYOUTS = ("plain", "twocol", "tables", "messy")

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_date(iso, style="long"):
    """A date as a résumé would print it — and not the same way twice.

    `messy` uses all three styles on one page, because real résumés do and
    because a parser that only handles one silently drops the rest.
    """
    if iso is None:
        return "Present"
    year, month = iso.split("-")
    if style == "long":
        return f"{MONTHS[int(month) - 1]} {year}"
    if style == "numeric":
        return f"{month}/{year}"
    return year


def _esc(text):
    return html.escape(str(text))


def _skills(person):
    return ", ".join(person["skills"])


def _jobs_plain(person, style="long"):
    out = []
    for job in person["employment"]:
        dates = f"{fmt_date(job['start'], style)} – {fmt_date(job['end'], style)}"
        bullets = "".join(f"<li>{_esc(b)}</li>" for b in job["bullets"])
        out.append(
            f'<div class="job"><div class="jobhead">'
            f'<span class="role">{_esc(job["title"])}</span>'
            f'<span class="firm">{_esc(job["company"])}</span>'
            f'<span class="where">{_esc(job["location"])}</span>'
            f'<span class="when">{_esc(dates)}</span></div>'
            f"<ul>{bullets}</ul></div>")
    return "".join(out)


def _jobs_table(person):
    rows = "".join(
        f"<tr><td>{_esc(j['title'])}</td><td>{_esc(j['company'])}</td>"
        f"<td>{_esc(j['location'])}</td>"
        f"<td>{_esc(fmt_date(j['start']))} – {_esc(fmt_date(j['end']))}</td>"
        f"<td>{_esc(' '.join(j['bullets']))}</td></tr>"
        for j in person["employment"])
    return ("<table class='grid'><tr><th>Role</th><th>Organisation</th>"
            "<th>Location</th><th>Period</th><th>Detail</th></tr>"
            f"{rows}</table>")


def _section(title, body):
    return f"<h2>{_esc(title)}</h2>{body}" if body else ""


def _education(person):
    return "".join(
        f'<div class="row"><b>{_esc(e["degree"])}</b>, {_esc(e["institution"])}'
        f' <span class="when">{_esc(fmt_date(e["start"]))} –'
        f' {_esc(fmt_date(e["end"]))}</span></div>'
        for e in person["education"])


def _projects(person):
    return "".join(
        f'<div class="row"><b>{_esc(p["name"])}</b> '
        f'<span class="stack">{_esc(", ".join(p["stack"]))}</span><br>'
        f'{_esc(p["blurb"])}</div>'
        for p in person["projects"])


def _certs(person):
    return "".join(
        f'<div class="row">{_esc(c["name"])} — {_esc(c["issuer"])}, '
        f'{_esc(fmt_date(c["date"]))}</div>'
        for c in person["certifications"])


def _pubs(person):
    return "".join(
        f'<div class="row">{_esc(p["title"])}. {_esc(p["venue"])}, '
        f'{_esc(fmt_date(p["date"]))}</div>'
        for p in person.get("publications", []))


CSS = {
    "plain": """
      body { font: 11pt/1.5 Georgia, serif; margin: 0; color: #111; }
      h1 { font-size: 20pt; margin: 0 0 2pt; }
      h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: .08em;
           border-bottom: 1px solid #999; margin: 16pt 0 6pt; padding-bottom: 2pt; }
      .contact { color: #444; font-size: 9.5pt; margin-bottom: 4pt; }
      .jobhead { display: flex; flex-wrap: wrap; gap: 8pt; align-items: baseline; }
      .role { font-weight: bold; } .firm { font-style: italic; }
      .where, .when { color: #555; font-size: 9.5pt; margin-left: auto; }
      ul { margin: 3pt 0 8pt 16pt; } li { margin: 1pt 0; }
      .row { margin: 4pt 0; } .stack { color: #555; font-size: 9.5pt; }
    """,
    "twocol": """
      body { font: 10.5pt/1.45 Helvetica, Arial, sans-serif; margin: 0; color: #111; }
      .wrap { display: grid; grid-template-columns: 34% 66%; gap: 18pt; }
      .side { background: #f2f2f2; padding: 10pt; }
      h1 { font-size: 18pt; margin: 0 0 2pt; }
      h2 { font-size: 9.5pt; text-transform: uppercase; letter-spacing: .1em;
           color: #444; margin: 12pt 0 4pt; }
      .contact { font-size: 9pt; color: #444; }
      .jobhead { display: block; } .role { font-weight: bold; display: block; }
      .firm { display: block; } .where, .when { color: #555; font-size: 9pt; }
      ul { margin: 3pt 0 8pt 14pt; }
      .row { margin: 4pt 0; } .stack { color: #555; font-size: 9pt; }
    """,
    "tables": """
      body { font: 10pt/1.4 Verdana, sans-serif; margin: 0; color: #111; }
      h1 { font-size: 16pt; margin: 0 0 4pt; }
      h2 { font-size: 10pt; background: #333; color: #fff; padding: 3pt 6pt;
           margin: 12pt 0 4pt; }
      table.grid { border-collapse: collapse; width: 100%; font-size: 9pt; }
      table.grid th { background: #ddd; text-align: left; }
      table.grid th, table.grid td { border: 1px solid #999; padding: 3pt 5pt;
                                     vertical-align: top; }
      .row { margin: 3pt 0; }
    """,
    # The point of this one is to be hard to read correctly.
    "messy": """
      body { font: 9pt/1.05 "Times New Roman", serif; margin: 0; color: #222;
             text-align: justify; letter-spacing: .3pt; word-spacing: -.5pt; }
      h1 { font-size: 13pt; margin: 0; letter-spacing: 2pt; }
      h2 { font-size: 9pt; font-variant: small-caps; margin: 7pt 0 1pt;
           letter-spacing: 1.5pt; }
      .contact { font-size: 7.5pt; }
      .jobhead { display: inline; } .role { font-weight: bold; }
      .firm::before { content: " | "; } .when::before { content: " | "; }
      ul { margin: 1pt 0 4pt 11pt; } li { margin: 0; }
      .row { margin: 1pt 0; }
      .runner { position: running(hdr); font-size: 7pt; color: #777; }
    """,
}


def to_html(person, layout):
    """One person, one layout, as a standalone HTML document."""
    style = "numeric" if layout == "messy" else "long"
    head = (f'<h1>{_esc(person["name"])}</h1>'
            f'<div class="contact">{_esc(person["email"])} · '
            f'{_esc(person["phone"])} · {_esc(person["location"])}'
            + (f' · DOB {_esc(person["dob"])}' if person.get("dob") else "")
            + f'</div><div class="headline">{_esc(person["headline"])}</div>')

    if layout == "tables":
        body = (head
                + _section("Skills", f'<div class="row">{_esc(_skills(person))}</div>')
                + _section("Experience", _jobs_table(person))
                + _section("Education", _education(person))
                + _section("Projects", _projects(person))
                + _section("Certifications", _certs(person))
                + _section("Publications", _pubs(person)))
    elif layout == "twocol":
        side = (_section("Skills", f'<div class="row">{_esc(_skills(person))}</div>')
                + _section("Education", _education(person))
                + _section("Certifications", _certs(person)))
        main = (_section("Experience", _jobs_plain(person))
                + _section("Projects", _projects(person))
                + _section("Publications", _pubs(person)))
        body = (head + f'<div class="wrap"><div class="side">{side}</div>'
                f'<div class="main">{main}</div></div>')
    else:
        body = (head
                + _section("Skills", f'<div class="row">{_esc(_skills(person))}</div>')
                + _section("Experience", _jobs_plain(person, style))
                + _section("Education", _education(person))
                + _section("Projects", _projects(person))
                + _section("Certifications", _certs(person))
                + _section("Publications", _pubs(person)))

    margin = "10mm" if layout == "messy" else "16mm"
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<title>{_esc(person["name"])}</title><style>'
            f'@page {{ size: A4; margin: {margin}; }}{CSS[layout]}'
            f'</style></head><body>{body}</body></html>')


def build(slugs=None, layouts=None, out_dir=None):
    """Write the HTML and drive Chrome to PDF. Returns the paths written."""
    out_dir = out_dir or OUT
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for slug in (slugs or PEOPLE):
        for layout in (layouts or LAYOUTS):
            stem = os.path.join(out_dir, f"{slug}-{layout}")
            with open(stem + ".html", "w", encoding="utf-8") as fh:
                fh.write(to_html(PEOPLE[slug], layout))
            cmd = [CHROME, "--headless=old", "--disable-gpu", "--no-sandbox",
                   "--virtual-time-budget=3000",
                   f"--print-to-pdf={stem}.pdf",
                   # Chrome's own running header is noise a real export
                   # often carries. Kept for `messy` on purpose.
                   *([] if layout == "messy" else ["--no-pdf-header-footer"]),
                   "file://" + stem + ".html"]
            subprocess.run(cmd, capture_output=True, timeout=120)
            if os.path.exists(stem + ".pdf"):
                written.append(stem + ".pdf")
    return written


def demo():
    for slug, person in PEOPLE.items():
        for layout in LAYOUTS:
            doc = to_html(person, layout)
            assert doc.startswith("<!doctype html>")
            assert doc.count("<body>") == 1 and doc.endswith("</body></html>")
            # Every fact the ANSWER KEY claims must be on the page, or the
            # benchmark is asking for something the document never said.
            #
            # Driven off truth() rather than off the source dict, which is
            # the version that would have caught the titles bug: titles
            # used to be a hand-written list that render() never rendered,
            # so it could say anything at all and the loop below — checking
            # the employment rows instead — passed regardless. bhaskar's
            # claimed two titles that appear nowhere on his page, and the
            # model was marked wrong for reading the page correctly.
            key = truth(slug)
            for field in ("name", "email", "location", "titles", "skills",
                          "companies", "education", "institutions",
                          "projects", "certifications"):
                value = key[field]
                for item in ([value] if isinstance(value, str) else value):
                    assert html.escape(item) in doc, (slug, layout, field, item)

    # Escaping, not string-building: a name with an ampersand must not
    # become markup.
    rogue = dict(PEOPLE["ada"], name="A & B <script>", employment=[],
                 projects=[], certifications=[], education=[])
    assert "<script>" not in to_html(rogue, "plain")
    assert "&amp;" in to_html(rogue, "plain")

    # messy prints dates a different way, which is the difficulty.
    assert fmt_date("2023-04") == "Apr 2023"
    assert fmt_date("2023-04", "numeric") == "04/2023"
    assert fmt_date("2023-04", "year") == "2023"
    assert fmt_date(None) == "Present", "an open-ended job is not undated"
    assert "04/2023" in to_html(PEOPLE["ada"], "messy")
    assert "Apr 2023" in to_html(PEOPLE["ada"], "plain")
    print(f"render demo ok — {len(PEOPLE)} people x {len(LAYOUTS)} layouts "
          f"= {len(PEOPLE) * len(LAYOUTS)} documents")


def main():
    if "--demo" in sys.argv:
        return demo()
    if not os.path.exists(CHROME):
        sys.exit(f"Chrome not found at {CHROME}")
    written = build()
    print(f"wrote {len(written)} PDFs to {OUT}")
    for path in written[:6]:
        print(f"  {os.path.basename(path)}  {os.path.getsize(path):,} bytes")
    if len(written) > 6:
        print(f"  ... and {len(written) - 6} more")


if __name__ == "__main__":
    main()
