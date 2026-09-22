"""Free remote-job aggregator feeds — public JSON / RSS, no auth, no cost.

Unlike ats.py these can't be table-driven (one is JSON, one is RSS, and each
packs its fields differently), so a feed adapter is a function with the uniform
signature (cfg, keep_title, keep_location) -> [row]. Adding a feed = one
function here plus one line in sources.FEED_FETCHERS.

These are the highest value-per-line sources in the whole project: one request
returns a whole board of international remote roles.
"""
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import telemetry

from ._http import dig, flat, get_json, get_xml, strip_html
from .ats import BLANK, _date


# Provider-native identity — DIAGNOSTIC ONLY. The ATS half of this is
# sources/ats.py NATIVE; the reasoning is there and applies identically here.
# The audit recorded "ID dropped" against every one of these feeds: each
# publishes a stable posting id and its own canonical link, and the field map
# above keeps neither. Captured under SWEEP_SEARCH_V2_TELEMETRY into the row's
# "_native" key, which to_output() does not read.
#
# wwr is absent on purpose: its rows come from RSS <item> elements, not dicts,
# so dig() cannot walk them. It is handled inline in wwr() instead.
NATIVE = {
    "remoteok": {"native_id": "id", "canonical_url": "url",
                 "apply_url": "apply_url", "published_at": "date",
                 "board_company": "company"},
    "remotive": {"native_id": "id", "canonical_url": "url",
                 "published_at": "publication_date",
                 "board_company": "company_name",
                 "multi_location": "candidate_required_location"},
    "jobicy": {"native_id": "id", "canonical_url": "url",
               "published_at": "pubDate", "board_company": "companyName",
               "multi_location": "jobGeo"},
    "himalayas": {"native_id": "guid", "apply_url": "applicationLink",
                  "published_at": "pubDate", "board_company": "companyName",
                  "multi_location": "locationRestrictions"},
}


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
        if telemetry.active():
            row["_native"] = {field: flat(dig(item, path))
                              for field, path in NATIVE.get(name, {}).items()}
        # Every feed here is a remote-only board, but the location field carries
        # the SCOPE ("Worldwide" vs "USA Only"), which is the distinction that
        # matters. Keep their text and append the flag so enrich sees both.
        row["Location"] = (row["Location"] + ", Remote").strip(", ")
        if keep_title(row["Title"]) and keep_location(row["Location"]):
            rows.append(row)
    telemetry.observed(raw=len(items), normalized=len(items), gated=len(rows),
                       requests=1)
    return rows


def remoteok(cfg, keep_title, keep_location):
    """remoteok.com/api — the entire board in a single request.

    Job URL points at the Remote OK posting rather than the external apply link
    on purpose: their API terms ask for a link back, and the posting URL is the
    stabler of the two anyway.
    """
    rows = []
    items = get_json("https://remoteok.com/api")
    normalized = 0
    for it in items:
        # The first element is a legal/metadata object, not a job. Detect it by
        # shape rather than by index so a feed reorder can't slip it through.
        if not it.get("id") or not it.get("position"):
            continue
        normalized += 1
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
        if telemetry.active():
            row["_native"] = {field: flat(it.get(path))
                              for field, path in NATIVE["remoteok"].items()}
        if keep_title(row["Title"]) and keep_location(row["Location"]):
            rows.append(row)
    telemetry.observed(raw=len(items), normalized=normalized, gated=len(rows),
                       requests=1)
    return rows


def remotive(cfg, keep_title, keep_location):
    """remotive.com/api — software-dev category, ~40 jobs, one request.
    Location is already a scope word ("Worldwide", "USA Only", "Europe")."""
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
    """jobicy.com API v2 — engineering industry, 50 jobs, structured pay."""
    count = cfg.get("count", 50)
    return _json_rows(
        "jobicy",
        f"https://jobicy.com/api/v2/remote-jobs?count={count}&industry=engineering",
        "jobs",
        {"Title": "jobTitle", "Company": "companyName", "Location": "jobGeo",
         "Posted Date": "pubDate", "Job URL": "url", "Description": "jobDescription",
         "Experience": "jobLevel"},
        keep_title, keep_location,
        salary=lambda it: _salary(it.get("salaryCurrency"), it.get("salaryMin"),
                                  it.get("salaryMax"), it.get("salaryPeriod")))


def _himalayas_urls(cfg):
    """Search URLs when the profile named its roles, browse pages otherwise.

    Split out so the choice can be asserted offline (python -m sources): it is
    a branch between two access patterns with very different yields, and it is
    driven by config, so getting it wrong is silent.
    """
    queries = [str(q).strip() for q in (cfg.get("queries") or []) if str(q).strip()]
    if queries:
        return [f"https://himalayas.app/jobs/api/search?limit=20&q={quote(q)}"
                for q in queries[:cfg.get("max_queries", 8)]]
    return [f"https://himalayas.app/jobs/api?limit=20&offset={page * 20}"
            for page in range(cfg.get("pages", 10))]


def himalayas(cfg, keep_title, keep_location):
    """himalayas.app — the richest metadata of any free feed.

    It reports timezoneRestrictions as actual UTC offsets, which is better
    timezone data than anything else here produces, plus structured pay and
    location restrictions.

    Two access patterns, and `queries` picks between them:

      SEARCH (/jobs/api/search?q=) is used when the profile supplies role
      keywords. It did not exist when this adapter was written — the old
      comment here recorded that no filter was available — so the feed paged
      blind through ~96k mostly non-engineering jobs at 20 a time, and 200 rows
      per run yielded the 76 distinct postings it ever contributed across every
      sweep in output/.

      BROWSE (/jobs/api?offset=) is the fallback for a profile with no
      keywords, so nothing breaks without them.

    The two endpoints return the SAME job shape, so one field map serves both.
    Queries are capped because this API rate-limits (429) and the cap keeps a
    keyword-rich profile from making more requests than the blind paging did.
    """
    rows = []
    for url in _himalayas_urls(cfg):
        rows += _json_rows(
            "himalayas", url, "jobs",
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

# The whole board is NOT a category. WWR publishes its catch-all feed at the
# ROOT — https://weworkremotely.com/remote-jobs.rss — and the audit measured
# what asking for it as a category costs: /categories/remote-jobs.rss answers
# with a 301 whose redirect then fails, wwr() raised on it, and the SEVEN
# categories that had already returned HTTP 200 went in the bin with it. The
# canonical route returned 83 items in the same follow-up check.
# https://weworkremotely.com/remote-job-rss-feed
WWR_WHOLE_BOARD = "https://weworkremotely.com/remote-jobs.rss"


def _wwr_url(category):
    """Feed URL for one configured WWR entry.

    Split out, and asserted offline in `python -m sources`, because a wrong feed
    URL here fails at request time on someone else's machine rather than in a
    test: it is exactly the shape of bug the audit found.
    """
    name = (category or "").strip()
    return (WWR_WHOLE_BOARD if name in ("remote-jobs", "")
            else WWR_FEED.format(category=name))


def wwr(cfg, keep_title, keep_location):
    """We Work Remotely category RSS feeds (25 newest jobs each, no auth).

    Each configured feed is isolated. One category failing costs that category
    and nothing else — before this, it cost every category fetched before it,
    which is a far more expensive failure than the one that actually happened.
    A feed where EVERY route fails still raises, so fetch_free reports it as a
    dead source rather than as a source that legitimately returned zero.
    """
    rows = []
    failures = []
    for category in cfg.get("categories", []):
        try:
            root = get_xml(_wwr_url(category))
        except Exception as exc:
            failures.append(f"{category}: {exc}")
            telemetry.partial(f"wwr {category}: {telemetry.failure_category(exc)}")
            continue
        items = root.findall(".//item")
        kept_before = len(rows)
        for item in items:
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
            if telemetry.active():
                # RSS, not JSON: <guid> is WWR's own posting identity and
                # <link> its canonical URL, and the field map keeps neither.
                row["_native"] = {"native_id": t("guid"),
                                  "canonical_url": t("link"),
                                  "published_at": t("pubDate"),
                                  "multi_location": t("region"),
                                  "feed_category": category}
            if keep_title(row["Title"]) and keep_location(row["Location"]):
                rows.append(row)
        telemetry.observed(raw=len(items), normalized=len(items),
                           gated=len(rows) - kept_before, requests=1)
    # Nothing came back from anywhere: that is a failed source, not a source
    # with no jobs, and the two must not look the same to whoever reads the log.
    if failures and not rows:
        raise RuntimeError("wwr: every configured feed failed — "
                           + "; ".join(failures))
    return rows
