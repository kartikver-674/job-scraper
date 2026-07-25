"""Free (non-Apify) job sources.

    ats.py    company career boards — table-driven, one dict entry per platform
    feeds.py  public JSON/RSS aggregators — one function per feed

fetch_free() runs everything configured in config.ATS_BOARDS / config.FEEDS and
returns rows in scraper.py's internal schema. Failures are isolated per board /
per feed, so one dead token or a feed outage never kills a sweep.

Self-check:
    python -m sources           # offline: assert the field mapping (no network)
    python -m sources --live     # one real request per platform + feed
"""
from . import ats, feeds

# A feed adapter is (cfg, keep_title, keep_location) -> [row].
FEED_FETCHERS = {"remoteok": feeds.remoteok, "wwr": feeds.wwr}


def fetch_free(ats_boards, feed_cfg, keep_title, keep_location, is_home=None,
               log=print):
    """Every configured free source. keep_title / keep_location / is_home are
    predicates from the caller, so policy stays in config.py + scraper.py.

    Only ATS boards can answer is_home — it needs a whole company board to look
    at, which a feed doesn't give us. Feed rows keep hires_home = "" (unknown).
    """
    rows = []
    for platform, boards in (ats_boards or {}).items():
        if platform not in ats.ATS:
            log(f"  {platform:<16} {'-':<22} ! no adapter (see sources/ats.py ATS)")
            continue
        for token, company in boards.items():
            try:
                got = ats.fetch(platform, token, company, keep_title, keep_location,
                                is_home)
                rows.extend(got)
                home = f" hires-home={got[0]['hires_home']}" if got else ""
                log(f"  {platform:<16} {company:<22} {len(got):>4} jobs{home}")
            except Exception as exc:
                log(f"  {platform:<16} {company:<22} ! {exc}")
    for name, cfg in (feed_cfg or {}).items():
        if not cfg.get("enabled"):
            continue
        fetcher = FEED_FETCHERS.get(name)
        if fetcher is None:
            log(f"  {name:<16} {'-':<22} ! no adapter (see sources.FEED_FETCHERS)")
            continue
        try:
            got = fetcher(cfg, keep_title, keep_location)
            rows.extend(got)
            log(f"  {name:<16} {'(feed)':<22} {len(got):>4} jobs")
        except Exception as exc:
            log(f"  {name:<16} {'(feed)':<22} ! {exc}")
    return rows
