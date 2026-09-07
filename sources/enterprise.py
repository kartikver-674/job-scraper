"""Large-enterprise careers sites — free, unauthenticated, stdlib.

These are the employers that never show up in ATS_BOARDS: they don't rent a
Greenhouse/Lever board, they run their own recruiting platform. Each platform is
a different shape, but there are only four of them across ten household names,
so this module is FOUR fetchers plus a per-employer spec table. Adding another
company on a platform already here is one dict entry in EMPLOYERS.

Everything below was probed live 2026-08-15; the counts are what each endpoint
actually returned, so a future drift shows up as a number that moved.

  amazon          www.amazon.jobs/en/search.json  -> JSON, description INCLUDED
                  in the listing, so this is the only one costing no JD request.
                  The location filter is `normalized_country_code[]=IND`.
                  `country[]=IND` and `loc_group_id[]=india` are ACCEPTED AND
                  SILENTLY IGNORED (2,093 hits with US rows on top either way) —
                  the same trap as Optum's Location param. 2,593 India jobs.

  orc             Oracle Recruiting Cloud, used by Oracle itself (2,175 reqs)
                  and JPMorgan Chase (7,379). One GET returns a page of 200.
                  Server-side `keyword=` works and is worth using: it cut JPMC
                  7,379 -> 1,535. The listing carries title, location, date and
                  a SHORT description; the full JD needs a second request.

  workday         Accenture (2,000+). POST-only, which is why ats.ATS couldn't
                  hold it. limit is capped at 20 per request by Workday itself.
                  bulletFields = [requisition id, location], so the location
                  gate runs before any JD request.

  successfactors  SAP. HTML, 25 rows a page, `locationsearch=India` genuinely
                  filters (unlike Amazon's country param). No ld+json on the JD
                  page — the description hangs off
                  data-careersite-propertyid="description".

NOT reachable for free, probed and rejected so nobody re-tries them:
  microsoft   jobs.careers.microsoft.com is Eightfold. The old
              gcsservices.../search/api/v1/search is 404 (and its TLS cert no
              longer matches the host); every path under the site host returns
              the SPA shell; microsoft.eightfold.ai/api/apply/v2/jobs answers
              403 with or without Referer/Origin.
  ibm         Next.js, but __NEXT_DATA__ carries only translation strings —
              results are fetched client-side from an endpoint not named in any
              of the 7 page scripts.
  capgemini   the page names https://cg-jobstream-api.azurewebsites.net/api as
              its data host; 11 plausible paths under it all 404.
  siemens     Avature, fully JS-rendered: zero job anchors in the served HTML.
  deloitte    Avature, server-rendered and parseable, BUT apply.deloitte.com is
              the US site — en_IN and a country facet both return the same 10
              en_US rows with no India mention. Deloitte India recruits
              elsewhere.
Those five need a paid source (LinkedIn via Apify) or a browser.
"""
import datetime
import re
import urllib.error
import urllib.parse

from ._http import get_bytes, get_json, post_json, strip_html

# platform-specific bits live here; `company` is what lands in the CSV.
EMPLOYERS = {
    "amazon": {
        "company": "Amazon", "platform": "amazon", "country": "IND",
    },
    "jpmorgan": {
        "company": "JPMorgan Chase", "platform": "orc",
        "host": "jpmc.fa.oraclecloud.com", "site": "CX_1001",
    },
    "oracle": {
        "company": "Oracle", "platform": "orc",
        "host": "eeho.fa.us2.oraclecloud.com", "site": "CX_45001",
    },
    "accenture": {
        "company": "Accenture", "platform": "workday",
        "host": "accenture.wd103.myworkdayjobs.com",
        "tenant": "accenture", "site": "AccentureCareers",
    },
    "sap": {
        "company": "SAP", "platform": "successfactors",
        "host": "jobs.sap.com", "location": "India",
    },
}

BLANK = {"Title": "", "Company": "", "Location": "", "Salary": "",
         "Experience": "", "Posted Date": "", "Job URL": "", "Description": "",
         "hires_home": "", "req_number": ""}


def _q(value):
    return urllib.parse.quote_plus(value or "")


def _iso(value):
    """Any of the date shapes these five sites emit -> YYYY-MM-DD, or ""."""
    text = (value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]) and text[:10].count("-") == 2:
        return text[:10]
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(text[:len(fmt) + 6], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Workday says "Posted Yesterday" / "Posted 15 Days Ago" in the LISTING; the
    # detail call gives a real startDate, so this is only a fallback.
    rel = re.search(r"(\d+)\+?\s*Days?\s*Ago", text, re.I)
    today = datetime.date.today()
    if rel:
        return (today - datetime.timedelta(days=int(rel.group(1)))).isoformat()
    if re.search(r"Yesterday", text, re.I):
        return (today - datetime.timedelta(days=1)).isoformat()
    if re.search(r"Today|Just Posted", text, re.I):
        return today.isoformat()
    return ""


# ---------------------------------------------------------------- amazon
_AMZ = ("https://www.amazon.jobs/en/search.json?base_query={kw}"
        "&normalized_country_code%5B%5D={cc}"
        "&result_limit={n}&offset={off}&sort=recent")


def _amazon(spec, keywords, max_pages, log):
    """Cards WITH descriptions — amazon.jobs puts the whole JD in the listing."""
    per, out, seen = 100, [], set()
    for kw in keywords:
        for page in range(max_pages):
            data = get_json(_AMZ.format(kw=_q(kw), cc=spec.get("country", "IND"),
                                        n=per, off=page * per))
            jobs = data.get("jobs") or []
            if page == 0 and log:
                log(f"  amazon {kw!r:<28} {data.get('hits', '?'):>5} hits")
            for j in jobs:
                jid = j.get("id_icims") or j.get("id")
                if jid in seen:
                    continue
                seen.add(jid)
                # The stack keywords live in the qualifications, not the blurb,
                # so scoring gets all three fields or it under-rates every role.
                body = " ".join(strip_html(j.get(k) or "") for k in
                                ("description", "basic_qualifications",
                                 "preferred_qualifications"))
                out.append({
                    "job_id": str(jid), "title": (j.get("title") or "").strip(),
                    "location": j.get("normalized_location") or j.get("location") or "",
                    "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
                    "posted": _iso(j.get("posted_date")), "description": body,
                    "req": str(jid),
                })
            if len(jobs) < per:
                break
    return out


# ---------------------------------------------------------------- oracle recruiting cloud
_ORC = ("https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        "?onlyData=true&expand=requisitionList.secondaryLocations"
        "&finder=findReqs;siteNumber={site}{kw},limit={n},offset={off}"
        ",sortBy=POSTING_DATES_DESC")


def _orc(spec, keywords, max_pages, log):
    host, site, per = spec["host"], spec["site"], 200
    out, seen = [], set()
    for kw in keywords:
        for page in range(max_pages):
            url = _ORC.format(host=host, site=site, n=per, off=page * per,
                              kw=f",keyword={_q(kw)}" if kw else "")
            data = get_json(url)
            item = (data.get("items") or [{}])[0]
            reqs = item.get("requisitionList") or []
            if page == 0 and log:
                log(f"  {spec['company'][:6].lower():<6} {kw!r:<28} "
                    f"{item.get('TotalJobsCount', '?'):>5} hits")
            for r in reqs:
                rid = str(r.get("Id") or "")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                out.append({
                    "job_id": rid, "title": r.get("Title") or "",
                    "location": r.get("PrimaryLocation") or "",
                    "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/"
                           f"{site}/job/{rid}",
                    "posted": _iso(r.get("PostedDate")),
                    # Kept as the floor for scoring: the full JD needs another
                    # request and ORC's detail finder is not public (see _detail).
                    "description": strip_html(r.get("ShortDescriptionStr") or ""),
                    "req": rid,
                })
            if len(reqs) < per:
                break
    return out


# ---------------------------------------------------------------- workday
def _workday(spec, keywords, max_pages, log):
    host, tenant, site = spec["host"], spec["tenant"], spec["site"]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    per, out, seen = 20, [], set()          # 20 is Workday's own cap
    # max_pages means "pages of ~100" everywhere else in this module, so scale
    # it here or Workday silently returns a fifth of what the caller asked for
    # (8 pages = 160 of Accenture's 2,000+ postings).
    for kw in keywords:
        for page in range(max_pages * 5):
            data = post_json(api, {"appliedFacets": {}, "limit": per,
                                   "offset": page * per, "searchText": kw})
            posts = data.get("jobPostings") or []
            if page == 0 and log:
                log(f"  workday {kw!r:<27} {data.get('total', '?'):>5} hits")
            for p in posts:
                path = p.get("externalPath") or ""
                if not path or path in seen:
                    continue
                seen.add(path)
                bullets = p.get("bulletFields") or []
                out.append({
                    "job_id": path, "title": p.get("title") or "",
                    # bulletFields is [req id, location] — the location gate can
                    # therefore run before any JD request.
                    "location": ", ".join(bullets[1:]) or p.get("locationsText", ""),
                    "url": f"https://{host}/en-US/{site}{path}",
                    "posted": _iso(p.get("postedOn")), "description": "",
                    "req": bullets[0] if bullets else "",
                    "_detail": f"https://{host}/wday/cxs/{tenant}/{site}{path}",
                })
            if len(posts) < per:
                break
    return out


# ---------------------------------------------------------------- successfactors
_SF_ROW = re.compile(
    r'<a[^>]*class="jobTitle-link"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'(?P<rest>.*?)</tr>', re.S)
_SF_LOC = re.compile(r'class="jobLocation"[^>]*>(.*?)<', re.S)
_SF_DESC = re.compile(
    r'data-careersite-propertyid="description"[^>]*>(.*?)(?:<div[^>]+class="[^"]*jobdetail'
    r'|<footer|</body)', re.S)
_SF_DATE = re.compile(r'data-careersite-propertyid="date"[^>]*>(.*?)<', re.S)


def _successfactors(spec, keywords, max_pages, log):
    host, loc = spec["host"], spec.get("location", "")
    per, out, seen = 25, [], set()
    for kw in keywords:
        for page in range(max_pages):
            url = (f"https://{host}/search/?q={_q(kw)}&locationsearch={_q(loc)}"
                   f"&startrow={page * per}")
            html = get_bytes(url).decode("utf-8", "replace")
            rows = list(_SF_ROW.finditer(html))
            if page == 0 and log:
                log(f"  sap    {kw!r:<28} {len(rows) or 0:>5} rows/page")
            for m in rows:
                href = m.group("href")
                if href in seen:
                    continue
                seen.add(href)
                loc_m = _SF_LOC.search(m.group("rest"))
                out.append({
                    "job_id": href, "title": strip_html(m.group("title")),
                    "location": strip_html(loc_m.group(1)) if loc_m else "",
                    "url": f"https://{host}{href}",
                    "posted": "", "description": "", "req": "",
                    "_detail": f"https://{host}{href}",
                })
            if len(rows) < per:
                break
    return out


FETCHERS = {"amazon": _amazon, "orc": _orc, "workday": _workday,
            "successfactors": _successfactors}


# ---------------------------------------------------------------- JD detail
def _detail(spec, card):
    """-> {description, posted, live}. Only called for cards that passed the
    title + location gates, so a JD request is never spent on a row we'd drop.

    ORC has no public detail finder (recruitingCEJobRequisitionDetails answers
    400 "finder ByRequisitionId ... is not valid" for every documented spelling,
    probed 2026-08-15), so those rows keep the listing's short description
    rather than pretending to a full JD.
    """
    platform = spec["platform"]
    if platform in ("amazon", "orc"):
        return {"description": card["description"], "posted": card["posted"],
                "live": True}
    try:
        if platform == "workday":
            info = (get_json(card["_detail"]) or {}).get("jobPostingInfo") or {}
            return {"description": strip_html(info.get("jobDescription") or ""),
                    "posted": _iso(info.get("startDate")) or card["posted"],
                    "live": True}
        html = get_bytes(card["_detail"]).decode("utf-8", "replace")
        desc = _SF_DESC.search(html)
        date = _SF_DATE.search(html)
        return {"description": strip_html(desc.group(1)) if desc else "",
                "posted": _iso(strip_html(date.group(1))) if date else "",
                "live": True}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"description": "", "posted": "", "live": False}
        raise


def fetch(cfg, keep_title, keep_location, log=print):
    """Every enabled employer -> scraper.py rows.

    cfg (config.ENTERPRISE): {"enabled": bool, "employers": [slug],
    "keywords": [str], "max_pages": int, "verify_live": bool}.
    """
    keywords = cfg.get("keywords") or [""]
    max_pages = cfg.get("max_pages", 5)
    rows = []
    for slug in (cfg.get("employers") or list(EMPLOYERS)):
        spec = EMPLOYERS.get(slug)
        if not spec:
            log(f"  {slug:<16} ! no spec (see sources/enterprise.EMPLOYERS)")
            continue
        try:
            cards = FETCHERS[spec["platform"]](spec, keywords, max_pages, log)
        except Exception as exc:
            log(f"  {slug:<16} ! {exc}")
            continue

        wanted = [c for c in cards
                  if keep_title(c["title"]) and keep_location(c["location"])]
        log(f"  {slug:<16} {len(cards):>5} cards -> {len(wanted):>4} pass title+location")

        kept = 0
        for card in wanted:
            row = dict(BLANK, Source=f"{slug}:{card['job_id']}",
                       Company=spec["company"])
            row["Title"] = card["title"]
            row["Location"] = card["location"]
            row["Job URL"] = card["url"]
            row["req_number"] = card["req"]
            row["Posted Date"] = card["posted"]
            row["Description"] = card["description"]
            if cfg.get("verify_live", True) and card.get("_detail"):
                try:
                    info = _detail(spec, card)
                except Exception as exc:
                    log(f"  {slug:<16} ! {card['title'][:34]}: {exc}")
                    continue
                if not info["live"]:
                    continue
                row["Description"] = info["description"] or row["Description"]
                row["Posted Date"] = info["posted"] or row["Posted Date"]
            rows.append(row)
            kept += 1
        log(f"  {slug:<16} {kept:>5} scoreable")
    return rows


def demo():
    """Offline self-check — `python -m sources` runs it. Guards the two things
    that fail SILENTLY: a date shape that stops parsing, and the SuccessFactors
    row regex drifting (which yields blank titles, not an exception)."""
    assert _iso("August 15, 2026") == "2026-08-15"
    assert _iso("2026-08-14") == "2026-08-14"
    assert _iso("2026-08-14T00:00:00") == "2026-08-14"
    assert _iso("") == "" and _iso(None) == ""
    assert _iso("Posted Yesterday") == (datetime.date.today()
                                        - datetime.timedelta(days=1)).isoformat()
    assert _iso("Posted 15 Days Ago") == (datetime.date.today()
                                          - datetime.timedelta(days=15)).isoformat()
    assert _iso("Posted 30+ Days Ago") == (datetime.date.today()
                                           - datetime.timedelta(days=30)).isoformat()

    html = ('<tr><td><a class="jobTitle-link" href="/job/Bangalore-Dev-560066/1/">'
            'Full Stack Developer</a></td>'
            '<td><span class="jobLocation">Bangalore, IN, 560066</span></td></tr>')
    m = _SF_ROW.search(html)
    assert m and strip_html(m.group("title")) == "Full Stack Developer"
    assert _SF_LOC.search(m.group("rest")).group(1).strip() == "Bangalore, IN, 560066"

    # Every employer names a platform that actually has a fetcher.
    for slug, spec in EMPLOYERS.items():
        assert spec["platform"] in FETCHERS, slug
        assert spec.get("company"), slug
    print("enterprise demo ok")
