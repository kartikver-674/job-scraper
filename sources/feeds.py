"""Free remote-job aggregator feeds — public JSON / RSS, no auth, no cost.

Unlike ats.py these can't be table-driven (one is JSON, one is RSS, and each
packs its fields differently), so a feed adapter is a function with the uniform
signature (cfg, keep_title, keep_location) -> [row]. Adding a feed = one
function here plus one line in sources.FEED_FETCHERS.

These are the highest value-per-line sources in the whole project: one request
returns a whole board of international remote roles.
"""
from email.utils import parsedate_to_datetime

from ._http import get_json, get_xml, strip_html
from .ats import BLANK


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
