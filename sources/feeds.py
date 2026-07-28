"""Free remote-job aggregator feeds — public JSON / RSS, no auth, no cost.

Unlike ats.py these can't be table-driven (one is JSON, one is RSS, and each
packs its fields differently), so a feed adapter is a function with the uniform
signature (cfg, keep_title, keep_location) -> [row]. Adding a feed = one
function here plus one line in sources.FEED_FETCHERS.

These are the highest value-per-line sources in the whole project: one request
returns a whole board of international remote roles.
"""
from email.utils import parsedate_to_datetime

from ._http import dig, flat, get_json, get_xml, strip_html
from .ats import BLANK, _date


def _salary(currency, low, high, period):
    """Compose 'USD 90000-140000 per year' — the canonical, unambiguous form
    scraper.comp_max_usd reads. Worth doing carefully: these structured feeds are
    the ONLY free source that reports pay at all (the ATS boards never do), so
    getting the currency and period across is what makes the pay filter usable.
    """
    high = high or low
    if not high:
        return ""
    per = {"hourly": "per hour", "daily": "per day", "weekly": "per week",
           "monthly": "per month"}.get((period or "").lower(), "per year")
    return f"{(currency or 'USD').upper()} {int(low or 0)}-{int(high)} {per}"


def _json_rows(name, url, list_path, fmap, keep_title, keep_location,
               salary=None, tags_path=None, after=None):
    """Shared builder for the structured JSON job feeds.

    They differ only in field names and how pay is packed, so the per-feed
    functions below stay short enough to read at a glance. `after(item, row)` is
    the escape hatch for the one or two things a feed does that no other does.
    """
    items = dig(get_json(url), list_path) or []
    rows = []
    for item in items:
        row = dict(BLANK, Source=name)
        for field, path in fmap.items():
            row[field] = flat(dig(item, path))
        row["Posted Date"] = _date(dig(item, fmap.get("Posted Date")))
        row["Description"] = strip_html(row["Description"])
        if salary:
            row["Salary"] = salary(item)
        if tags_path:
            tags = flat(dig(item, tags_path))
            if tags:
                # Tags carry the stack and are often the only place it's stated,
                # so scoring has to see them.
                row["Description"] += f"\nTags: {tags}"
        if after:
            after(item, row)
        # Every feed here is a remote-only board, but the location field carries
        # the SCOPE ("Worldwide" vs "USA Only"), which is the distinction that
        # matters. Keep their text and append the flag so enrich sees both.
        row["Location"] = (row["Location"] + ", Remote").strip(", ")
        if keep_title(row["Title"]) and keep_location(row["Location"]):
            rows.append(row)
    return rows


def remoteok(cfg, keep_title, keep_location):
    """remoteok.com/api — the entire board in a single request.

    Job URL points at the Remote OK posting rather than the external apply link
    on purpose: their API terms ask for a link back, and the posting URL is the
    stabler of the two anyway.
    """
    rows = []
    for it in get_json("https://remoteok.com/api"):
        # The first element is a legal/metadata object, not a job. Detect it by
        # shape rather than by index so a feed reorder can't slip it through.
        if not it.get("id") or not it.get("position"):
            continue
        lo, hi = it.get("salary_min") or 0, it.get("salary_max") or 0
        tags = ", ".join(it.get("tags") or [])
        desc = strip_html(it.get("description", ""))
        row = dict(
            BLANK,
            Source="remoteok",
            Title=strip_html(it.get("position", "")),
            Company=strip_html(it.get("company", "")),
            # Feed-wide remote board: every row is remote, but "Worldwide" vs
            # "United States" is exactly the remote-scope distinction that
            # matters, so keep their text and append the flag.
            Location=(it.get("location") or "").strip() + ", Remote",
            # Canonical, unambiguous form for the currency-aware comp parser —
            # these figures are annual USD.
            Salary=f"USD {int(lo)}-{int(hi)} per year" if hi else "",
            **{"Posted Date": (it.get("date") or "")[:10]},
            **{"Job URL": it.get("url") or it.get("apply_url") or ""},
            # Tags carry the stack (react, node, typescript...) and often are
            # the only place it's stated, so scoring must see them.
            Description=f"{desc}\nTags: {tags}" if tags else desc,
        )
        if keep_title(row["Title"]) and keep_location(row["Location"]):
            rows.append(row)
    return rows


def remotive(cfg, keep_title, keep_location):
    """remotive.com/api — newest ~36 jobs, one request.

    The category param below is IGNORED by their API (verified 2026-07-28:
    ?category=sales and ?category=software-dev return an identical set spanning
    Medical, Marketing, Sales, ...). It stays in the URL only because removing it
    changes nothing; don't bother making it configurable, and don't trust it to
    be filtering. Location is already a scope word ("Worldwide", "USA Only").
    """
    return _json_rows(
        "remotive", "https://remotive.com/api/remote-jobs?category=software-dev",
        "jobs",
        {"Title": "title", "Company": "company_name",
         "Location": "candidate_required_location", "Posted Date": "publication_date",
         "Job URL": "url", "Description": "description", "Experience": "job_type"},
        keep_title, keep_location,
        salary=lambda it: (it.get("salary") or "").strip(),   # free text, e.g. "$30k - $100k"
        tags_path="tags")


def jobicy(cfg, keep_title, keep_location):
    """jobicy.com API v2 — 50 jobs, structured pay, one industry per request.

    industry is config-driven because it genuinely filters (unlike remotive's
    category): hardcoding "engineering" returned nothing but Software Engineering
    rows, i.e. zero usable results for any non-dev search.
    """
    count = cfg.get("count", 50)
    industry = cfg.get("industry", "engineering")
    return _json_rows(
        "jobicy",
        f"https://jobicy.com/api/v2/remote-jobs?count={count}&industry={industry}",
        "jobs",
        {"Title": "jobTitle", "Company": "companyName", "Location": "jobGeo",
         "Posted Date": "pubDate", "Job URL": "url", "Description": "jobDescription",
         "Experience": "jobLevel"},
        keep_title, keep_location,
        salary=lambda it: _salary(it.get("salaryCurrency"), it.get("salaryMin"),
                                  it.get("salaryMax"), it.get("salaryPeriod")))


def himalayas(cfg, keep_title, keep_location):
    """himalayas.app — the richest metadata of any free feed, behind the worst
    access pattern.

    It reports timezoneRestrictions as actual UTC offsets, which is better
    timezone data than anything else here produces, plus structured pay and
    location restrictions. But the API takes no category or search filter (all
    three documented-looking params are silently ignored) and pages 20 at a time
    through ~96k mostly non-engineering jobs, so yield per request is low and we
    page a bounded number of times rather than chase it.
    """
    rows = []
    for page in range(cfg.get("pages", 10)):
        rows += _json_rows(
            "himalayas",
            f"https://himalayas.app/jobs/api?limit=20&offset={page * 20}", "jobs",
            {"Title": "title", "Company": "companyName", "Posted Date": "pubDate",
             "Job URL": "applicationLink", "Description": "description",
             "Experience": "employmentType"},
            keep_title, keep_location,
            salary=lambda it: _salary(it.get("currency"), it.get("minSalary"),
                                      it.get("maxSalary"), it.get("salaryPeriod")),
            after=_himalayas_extras)
    return rows


def _utc_label(offset):
    """5.5 -> "UTC+5:30", -8 -> "UTC-8".

    Half-hour zones are real (India, Iran, parts of Australia) and this feed
    reports them as floats, so they must render as ":30" — enrich's parser reads
    that minutes group, and a plain "UTC+5" would silently lose the half hour.
    """
    hours, frac = divmod(abs(offset), 1)
    return (f"UTC{'+' if offset >= 0 else '-'}{int(hours)}"
            + (":30" if round(frac, 2) == 0.5 else ""))


def _himalayas_extras(item, row):
    # No restrictions on a remote-only board means genuinely worldwide.
    row["Location"] = ", ".join(item.get("locationRestrictions") or []) or "Worldwide"
    # Offsets like [-10,-9,...] -> "UTC-10 UTC-9 ...", which enrich.timezone_gap
    # already parses. Exact numbers beat inferring a zone from a region name.
    offsets = [o for o in (item.get("timezoneRestrictions") or [])
               if isinstance(o, (int, float))]
    if offsets:
        row["timezones"] = " ".join(_utc_label(o) for o in offsets)


WWR_FEED = "https://weworkremotely.com/categories/{category}.rss"


def wwr(cfg, keep_title, keep_location):
    """We Work Remotely category RSS feeds (25 newest jobs each, no auth)."""
    rows = []
    for category in cfg.get("categories", []):
        root = get_xml(WWR_FEED.format(category=category))
        for item in root.findall(".//item"):
            def t(tag):
                el = item.find(tag)
                return (el.text or "").strip() if el is not None else ""

            # WWR packs both names into <title> as "Company: Role".
            company, sep, title = t("title").partition(": ")
            if not sep:
                company, title = "", t("title")
            posted = ""
            if t("pubDate"):
                try:
                    posted = parsedate_to_datetime(t("pubDate")).strftime("%Y-%m-%d")
                except (TypeError, ValueError):
                    posted = ""
            row = dict(
                BLANK,
                Source="wwr",
                Title=title,
                Company=company,
                Location=(t("region") or "Remote"),   # e.g. "Anywhere in the World"
                Description=strip_html(t("description")),
                **{"Posted Date": posted},
                **{"Job URL": t("link")},
            )
            if keep_title(row["Title"]) and keep_location(row["Location"]):
                rows.append(row)
    return rows
