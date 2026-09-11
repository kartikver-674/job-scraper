"""The shortlist as a file you can work with somewhere else.

A download, never "open in Excel for the web". The web option means
uploading a file of the user's own scraped listings — recruiter emails and
phone numbers among them — to a third party before they are allowed to look
at it. Every other part of this app runs on localhost and keeps the data on
the machine that fetched it, and an export is a bad place to break that.

Flask-free on purpose, like logic.py: these are pure functions over rows, so
the tests that check what lands in a cell never start a server.
"""

import csv
import io
import json
import re

# One definition of the export, used by all three formats. A column is
# (header, row key, character width) — the width is Excel's, and the other
# two formats ignore it.
#
# Sixteen columns, not the CSV's twenty-four. The dropped ones (tz_gap,
# eor, req_number, verified_live, ...) are either scoring intermediates or
# near-always empty, and a sheet where a third of the columns are blank is
# harder to read, not more complete. The engine's CSV is still on disk for
# anyone who wants all of it.
COLUMNS = [
    # First, because it is the column a tracker is sorted and filtered by —
    # the question "what have I already done" comes before "where is it".
    ("Applied", "_applied", 9),
    ("Reachable", "_bucket", 16),
    ("Score", "score", 8),
    ("Role", "title", 46),
    ("Company", "company", 26),
    ("Location", "location", 28),
    ("Remote", "remote_scope", 16),
    ("Pay", "salary", 22),
    ("Experience", "experience_required", 13),
    ("Matched skills", "matched_skills", 38),
    ("Source", "source_site", 14),
    ("Posted", "date_posted", 13),
    ("Visa", "visa", 9),
    ("Recruiter email", "hr_email", 26),
    ("Recruiter phone", "hr_phone", 17),
    ("Apply URL", "apply_url", 52),
]

HEADERS = [head for head, _, _ in COLUMNS]
KEYS = [key for _, key, _ in COLUMNS]

# Short labels for the three sections the results screen groups by, so a
# spreadsheet can filter on the same distinction the page makes. Keyed on
# logic.SECTIONS' own keys.
BUCKET_LABELS = {"india": "In India", "remote": "Remote",
                 "abroad": "Needs a visa"}

# Excel and Sheets evaluate a cell that opens with any of these, so a
# scraped job title is a formula the moment it starts with one. The rows
# here come from job boards — untrusted text that a spreadsheet is about to
# execute — which is the whole of CSV injection, and it survives the trip
# through .xlsx just as well.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")

_HTTP = re.compile(r"^https?://", re.I)


def defuse(value):
    """A cell that cannot start a formula. Text unchanged in every other way.

    Prefixed with an apostrophe, which is Excel's own "this is text" mark and
    is not part of the value once the sheet is open. Numbers keep their type
    and are never touched: a real -1 must stay a number.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_LEAD):
        return "'" + value
    return value


def as_number(value):
    """`value` as an int when it is one, else the original text.

    Scores arrive from CSV as strings, and a column of text digits will not
    sort or chart in a spreadsheet — which is most of what a person opens
    this file to do.
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return value


def rows_for_export(buckets, sections):
    """The shortlist flattened back to one list, tagged with its section.

    Takes the SAME buckets the screen renders rather than re-deriving them,
    so a row cannot be filed under one heading on the page and another in
    the file. Section order is the screen's order too.
    """
    out = []
    for key, _, _ in sections:
        for row in buckets.get(key) or ():
            tagged = dict(row)
            tagged["_bucket"] = BUCKET_LABELS.get(key, key)
            # "Yes" or blank rather than True/False: a spreadsheet filters a
            # word, and a blank cell reads as "not yet" where FALSE reads as
            # a fact someone established.
            tagged["_applied"] = "Yes" if row.get("applied") else ""
            out.append(tagged)
    return out


def cells(row):
    """One row as the values the export writes, in COLUMNS order."""
    out = []
    for _, key, _ in COLUMNS:
        value = row.get(key) or ""
        out.append(as_number(value) if key == "score" else defuse(value))
    return out


def as_csv(rows):
    """UTF-8 with a BOM, because that is what makes Excel read it correctly.

    Without the BOM Excel decodes a .csv as the local ANSI code page, and
    every accented company name arrives mojibake. It costs three bytes.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(HEADERS)
    writer.writerows(cells(row) for row in rows)
    return buf.getvalue().encode("utf-8-sig")


def as_json(rows):
    """Header names as keys, so the JSON says the same thing the sheet does.

    No formula defusing here: JSON is not evaluated by whatever reads it,
    and an apostrophe glued to the front of every title would be a defect in
    the data rather than a protection.
    """
    out = [{head: (as_number(row.get(key) or "") if key == "score"
                   else (row.get(key) or ""))
            for head, key, _ in COLUMNS}
           for row in rows]
    return json.dumps(out, indent=2, ensure_ascii=False).encode("utf-8")


def as_xlsx(rows, about=()):
    """A workbook laid out to be worked in, not just opened.

    Frozen and filtered header, real numbers in Score, the role hyperlinked
    to its posting, and column widths that fit the content — the difference
    between a spreadsheet someone can sort and one they have to repair
    first.

    `about` is (label, value) pairs describing which sweep and which filters
    produced the file, on their own sheet: exporting three times while
    narrowing the filters otherwise leaves three files nobody can tell
    apart.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = "Listings"

    head_font = Font(bold=True, color="FFFFFFFF")
    head_fill = PatternFill("solid", fgColor="FF1F2933")
    for column, (head, _, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column, value=head)
        cell.font = head_font
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center")
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.row_dimensions[1].height = 22

    link_font = Font(color="FF0563C1", underline="single")
    for index, row in enumerate(rows, start=2):
        for column, value in enumerate(cells(row), start=1):
            sheet.cell(row=index, column=column, value=value)
        # The role opens the posting, the same thing clicking the row does on
        # the results screen. The raw URL stays in its own column: a
        # hyperlink is not something you can paste into another tool.
        url = (row.get("apply_url") or "").strip()
        if _HTTP.match(url):
            cell = sheet.cell(row=index, column=KEYS.index("title") + 1)
            cell.hyperlink = url
            cell.font = link_font

    last = get_column_letter(len(COLUMNS))
    # Both, and both matter: the filter is how you narrow 400 rows to the
    # ones you want, and the freeze is how you still know which column you
    # are looking at once you have scrolled past row 40.
    sheet.auto_filter.ref = f"A1:{last}{max(len(rows) + 1, 2)}"
    sheet.freeze_panes = "A2"

    if about:
        info = book.create_sheet("About this export")
        info.column_dimensions["A"].width = 24
        info.column_dimensions["B"].width = 62
        for index, (label, value) in enumerate(about, start=1):
            info.cell(row=index, column=1, value=label).font = Font(bold=True)
            info.cell(row=index, column=2, value=defuse(value))

    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def demo():
    rows = [{"title": "=cmd|calc", "score": "91", "apply_url": "https://x/1",
             "_bucket": "In India", "company": "Acme"},
            {"title": "Staff Mobile", "score": "", "apply_url": "javascript:x"}]

    assert defuse("=1+1") == "'=1+1" and defuse("React") == "React"
    assert defuse(-1) == -1, "a real number must keep its type"
    assert as_number("91") == 91 and as_number("") == ""

    body = as_csv(rows)
    assert body.startswith(b"\xef\xbb\xbf"), "Excel needs the BOM"
    text = body.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("Applied,Reachable,Score,Role")
    assert "'=cmd|calc" in text, "a formula must not reach the sheet live"

    loaded = json.loads(as_json(rows))
    assert loaded[0]["Score"] == 91 and loaded[0]["Role"] == "=cmd|calc"

    tagged = rows_for_export({"abroad": [{"title": "B"}],
                              "india": [{"title": "A", "applied": True}]},
                             [("india", "", ""), ("abroad", "", "")])
    assert [r["title"] for r in tagged] == ["A", "B"], "screen order"
    assert tagged[0]["_bucket"] == "In India"
    # The tick reaches every format, since all three build from COLUMNS.
    assert tagged[0]["_applied"] == "Yes" and tagged[1]["_applied"] == ""
    applied_csv = as_csv(tagged).decode("utf-8-sig").splitlines()
    assert applied_csv[1].startswith("Yes,") and applied_csv[2].startswith(",")
    assert json.loads(as_json(tagged))[0]["Applied"] == "Yes"

    try:
        from openpyxl import load_workbook
    except ImportError:
        print("exports demo ok (openpyxl absent, .xlsx unchecked)")
        return
    book = load_workbook(io.BytesIO(as_xlsx(rows, about=[("Profile", "kanav")])))
    sheet = book["Listings"]
    # Shifted one column by "Applied" at A. The builder derives both the
    # hyperlink column and the filter range from COLUMNS, so only these
    # hand-written letters move.
    assert sheet["A1"].value == "Applied" and sheet["B1"].value == "Reachable"
    assert sheet["C1"].value == "Score" and sheet["D1"].value == "Role"
    assert sheet["C2"].value == 91, "score must be a number, not text"
    assert sheet["D2"].value == "'=cmd|calc"
    assert sheet["D2"].hyperlink.target == "https://x/1"
    assert sheet["D3"].hyperlink is None, "only http(s) may be linked"
    assert sheet.freeze_panes == "A2" and sheet.auto_filter.ref == "A1:P3"
    assert book["About this export"]["B1"].value == "kanav"
    print("exports demo ok")


if __name__ == "__main__":
    demo()
