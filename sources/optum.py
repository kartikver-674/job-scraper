"""Optum careers — free, unauthenticated, stdlib. Radancy/TalentBrew + Taleo.

careers.optum.com is DEAD (NXDOMAIN, probed 2026-07-29). Every Optum
requisition is served from careers.unitedhealthgroup.com, which hosts *all* UHG
brands (Optum, UnitedHealthcare, ...) in one index. The brand appears in exactly
one place in the markup — a per-card CSS class, `brand-facet__optum` — so that
class is what separates Optum rows from the rest of UHG. There is no server-side
brand filter that works: the `custom_fields.Brand` facet holds business segments
("Employer & Individual", "Medicare & Retirement"), not the brand.

Why this can't be a row in ats.ATS: that table maps a JSON list to dotted paths.
Here the search endpoint returns JSON whose `results` value is a *string of HTML*,
and neither the description nor the posting date is in it — both need a second
request per job. So this is its own module.

Endpoints, all probed live 2026-07-29:
  GET /search-jobs/results?Keywords=..&CurrentPage=N&RecordsPerPage=100&...
      -> {"filters": html, "results": html, "hasJobs": bool}
      `results` carries <section id="search-results" data-total-job-results=..>
      and one <li> per job. RecordsPerPage=100 is honoured (verified: 100 cards).
  GET /job/<city>/<slug>/34088/<jobId>
      -> full JD page. <script type="application/ld+json"> JobPosting has the
         description and datePosted; the page also prints "Requisition number:"
         (the Taleo req the user actually applies with) and "Date posted:".
      -> 404 for a job id that isn't live (verified against a mutated id).

Liveness, and its one honest limit: a requisition is treated as live when it is
in the search index AND its JD page still returns 200. The index is fed from
Taleo's active requisitions, so a pulled req leaves it. What is NOT publicly
checkable is Taleo's own closed/filled state: uhg.taleo.net/.../jobapply.ftl
answers 200 with an identical "privacy agreement" gate for a live req, another
live req, and a nonexistent one alike (probed: 3 reqs, byte-identical markers),
so it carries zero status signal without a candidate session. Don't add it as a
check — it will "confirm" anything.
"""
import re

from ._http import get_json, strip_html

BASE = "https://careers.unitedhealthgroup.com"
ORG_ID = "34088"

# The search endpoint wants every one of these keys; omitting them 500s or
# silently ignores the keyword. SortCriteria=1/SortDirection=1 = most recent
# first, so a truncated sweep loses the stalest jobs rather than the freshest.
_SEARCH = (
    BASE + "/search-jobs/results?ActiveFacetID=0&CurrentPage={page}"
    "&RecordsPerPage={per_page}&Distance=50&RadiusUnitType=0&Keywords={kw}"
    "&Location={loc}&ShowRadius=False&IsPagination=True&CustomFacetName="
    "&FacetTerm=&FacetType=0&SearchResultsModuleName=Search+Results"
    "&SearchFiltersModuleName=Search+Filters&SortCriteria=1&SortDirection=1"
    "&SearchType=5&PostalCode=&fc=&fl=&fcf=&afc=&afl=&afcf="
)

_TOTAL_RE = re.compile(r'data-total-job-results="(\d+)"')
_CARD_RE = re.compile(
    r'<a\s+href="(?P<url>/job/[^"]+)"\s+data-job-id="(?P<job_id>\d+)"'
    r'\s+class="[^"]*brand-facet__(?P<brand>[a-z0-9_]+)[^"]*"'
    r'(?P<body>.*?)</a>', re.S)
_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
_REQ_RE = re.compile(r'<span class="job-id job-info">\s*([^<\s][^<]*?)\s*</span>')
_LOC_RE = re.compile(r'<span class="job-location[^"]*"[^>]*>(.*?)</span>', re.S)
_WORKSET_RE = re.compile(r'<span class="job-info job-worksetting">(.*?)</span>', re.S)

_LDJSON_RE = re.compile(
    r'<script type="application/ld\+json">\s*(\{.*?"@type"\s*:\s*"JobPosting".*?\})\s*</script>',
    re.S)
_JD_REQ_RE = re.compile(
    r'<b>\s*Requisition number:\s*</b>\s*([A-Za-z0-9-]+)', re.I)
# UHG's internal pay grade. Not rendered anywhere on the page — it rides along in
# an embedded JSON blob (custom_fields.Grade / GradeLevel, same value in both).
# It is the ONLY field that states a requisition's level as the employer means it:
# the visible title is the internal/system title, which reads two rungs senior to
# the grade ("Senior Software Engineer I" is G27, "Software Engineer" is G26), so
# ranking on the title alone mis-reads what a candidate is eligible for.
_JD_GRADE_RE = re.compile(r'"Name":"Grade","RawValue":"(\d+)"')
_JD_DATE_RE = re.compile(
    r'<b>\s*Date posted:\s*</b>\s*(\d{1,2})/(\d{1,2})/(\d{4})', re.I)
_APPLY_RE = re.compile(r'jobapply\.ftl\?job=([A-Za-z0-9-]+)')

BLANK = {"Title": "", "Company": "", "Location": "", "Salary": "",
         "Experience": "", "Posted Date": "", "Job URL": "", "Description": "",
         "hires_home": "", "grade": ""}


def _text(html_fragment):
    return strip_html(html_fragment or "").strip()


def _iso(year, month, day):
    """-> YYYY-MM-DD. The LD-JSON date is NOT zero-padded ("2026-7-15"), which
    scraper._parse_date reads as unparseable, so every date goes through here."""
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except (TypeError, ValueError):
        return ""


def search(keywords="", location="", per_page=100, max_pages=6, brand="optum",
           log=None):
    """Cards for one keyword query, Optum-brand only, newest first.

    Returns [{job_id, url, title, req, location, work_setting}]. Cheap: the
    listing alone answers title/location/req, so callers can filter on those
    BEFORE spending a request per job on the JD.
    """
    out, seen = [], set()
    page = 1
    while page <= max_pages:
        url = _SEARCH.format(page=page, per_page=per_page,
                             kw=_quote(keywords), loc=_quote(location))
        data = get_json(url)
        html = (data or {}).get("results") or ""
        if page == 1 and log:
            total = _TOTAL_RE.search(html)
            log(f"  optum  {keywords!r:<28} {total.group(1) if total else '?':>5} "
                f"UHG hits (all brands)")
        cards = _cards(html, brand)
        if not cards and not _CARD_RE.search(html):
            break                       # no rows at all -> past the last page
        for card in cards:
            if card["job_id"] not in seen:
                seen.add(card["job_id"])
                out.append(card)
        total = _TOTAL_RE.search(html)
        if total and page * per_page >= int(total.group(1)):
            break
        page += 1
    return out


def _quote(value):
    from urllib.parse import quote_plus
    return quote_plus(value or "")


def _cards(html, brand="optum"):
    cards = []
    for m in _CARD_RE.finditer(html or ""):
        if brand and m.group("brand") != brand:
            continue
        body = m.group("body")
        title = _H2_RE.search(body)
        req = _REQ_RE.search(body)
        loc = _LOC_RE.search(body)
        ws = _WORKSET_RE.search(body)
        cards.append({
            "job_id": m.group("job_id"),
            "url": BASE + m.group("url"),
            "title": _text(title.group(1)) if title else "",
            "req": _text(req.group(1)) if req else "",
            "location": _text(loc.group(1)) if loc else "",
            "work_setting": _text(ws.group(1)) if ws else "",
        })
    return cards


def detail(url):
    """JD page -> {description, date_posted, req, apply_req, live}.

    live=False means the requisition is GONE (404), which is the strongest
    public signal that it stopped accepting applications — see the module note
    on why the Taleo apply URL can't be used for this.
    """
    import urllib.error

    from ._http import get_bytes
    try:
        html = get_bytes(url).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"live": False, "description": "", "date_posted": "",
                    "req": "", "apply_req": "", "grade": "", "http": 404}
        raise
    return parse_jd(html)


def parse_jd(html):
    """The parsing half of detail(), split out so `python -m sources` can assert
    the field mapping offline — regex drift here yields blank descriptions and
    dateless rows, not an exception."""
    import json

    desc = date_posted = ""
    ld = _LDJSON_RE.search(html)
    if ld:
        try:
            posting = json.loads(ld.group(1))
            desc = strip_html(posting.get("description") or "")
            parts = str(posting.get("datePosted") or "").split("-")
            if len(parts) == 3:
                date_posted = _iso(*parts)
        except (ValueError, TypeError):
            pass
    if not date_posted:                          # fall back to the printed date
        m = _JD_DATE_RE.search(html)
        if m:
            date_posted = _iso(m.group(3), m.group(1), m.group(2))

    req = _JD_REQ_RE.search(html)
    apply_req = _APPLY_RE.search(html)
    grade = _JD_GRADE_RE.search(html)
    return {"live": True, "description": desc, "date_posted": date_posted,
            "req": req.group(1) if req else "",
            "apply_req": apply_req.group(1) if apply_req else "",
            "grade": grade.group(1) if grade else "",
            "http": 200}


def fetch(cfg, keep_title, keep_location, log=print):
    """Every configured keyword query -> scraper.py rows, verified live.

    cfg keys (config.OPTUM): keywords [str], locations [str], per_page,
    max_pages, verify_live (bool), brand, company.
    """
    company = cfg.get("company", "Optum")
    brand = cfg.get("brand", "optum")
    verify = cfg.get("verify_live", True)

    # 1. Listing sweep — cheap, and dedupes across keyword queries by job id.
    cards = {}
    for kw in (cfg.get("keywords") or [""]):
        for loc in (cfg.get("locations") or [""]):
            try:
                for card in search(kw, loc, cfg.get("per_page", 100),
                                   cfg.get("max_pages", 6), brand, log):
                    cards.setdefault(card["job_id"], card)
            except Exception as exc:
                log(f"  optum  {kw!r:<28} ! {exc}")
    log(f"  optum  {'(dedup, brand=' + brand + ')':<28} {len(cards):>5} cards")

    # 2. Filter on the listing fields BEFORE paying a request per JD.
    wanted = [c for c in cards.values()
              if keep_title(c["title"]) and keep_location(c["location"])]
    log(f"  optum  {'(title+location gate)':<28} {len(wanted):>5} to verify")

    # 3. One JD request each: description (for scoring), real posting date, the
    #    requisition number, and the 404 liveness check.
    rows, dead = [], 0
    for card in wanted:
        row = dict(BLANK, Source=f"optum:{card['job_id']}", Company=company)
        row["Title"] = card["title"]
        row["Location"] = card["location"]
        row["Job URL"] = card["url"]
        row["req_number"] = card["req"]
        row["job_id"] = card["job_id"]
        # A card marked "Remote" carries it in its own span, not the location,
        # so fold it in where scraper.is_remote() will see it.
        if "remote" in (card["work_setting"] or "").lower():
            row["Location"] = (row["Location"] + ", Remote").lstrip(", ")

        if verify:
            try:
                info = detail(card["url"])
            except Exception as exc:
                log(f"  optum  {card['req'] or card['job_id']:<28} ! {exc}")
                continue
            if not info["live"]:
                dead += 1
                continue                    # pulled requisition — drop it
            row["Description"] = info["description"]
            row["Posted Date"] = info["date_posted"]
            row["req_number"] = info["req"] or card["req"]
            row["grade"] = info["grade"]
            row["verified_live"] = "yes"
        rows.append(row)

    if dead:
        log(f"  optum  {'(dropped: JD 404, not live)':<28} {dead:>5}")
    log(f"  optum  {'(live, scoreable)':<28} {len(rows):>5} jobs")
    return rows
