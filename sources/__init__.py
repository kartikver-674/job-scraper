"""Free (non-Apify) job sources.

    ats.py         company career boards — table-driven, one dict per platform
    feeds.py       public JSON/RSS aggregators — one function per feed
    enterprise.py  big employers running their own platform (amazon.jobs,
                   Workday, Oracle Recruiting Cloud, SuccessFactors) — they need
                   a JD request per job or a POST, so they can't be ats.ATS rows

fetch_free() runs everything configured in config.ATS_BOARDS / config.FEEDS and
returns rows in scraper.py's internal schema. Failures are isolated per board /
per feed, so one dead token or a feed outage never kills a sweep.

Self-check:
    python -m sources           # offline: assert the field mapping (no network)
    python -m sources --live     # one real request per platform + feed
"""
from . import ats, enterprise, feeds

# A feed adapter is (cfg, keep_title, keep_location) -> [row].
# Probed and REJECTED, so nobody re-adds it: arbeitnow.com — 100 jobs returned
# 5 remote, of which 1 was dev-titled and that one was onsite in Nuremberg. A
# German-market board; ~1% yield for this search.
FEED_FETCHERS = {
    "remoteok": feeds.remoteok,
    "wwr": feeds.wwr,
    "remotive": feeds.remotive,
    "jobicy": feeds.jobicy,
    "himalayas": feeds.himalayas,
}


def fetch_free(ats_boards, feed_cfg, keep_title, keep_location, is_home=None,
               log=print, enterprise_cfg=None):
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

    # Big-employer careers platforms. Isolated per employer inside
    # enterprise.fetch, so one site's markup change costs that employer and not
    # the sweep.
    if (enterprise_cfg or {}).get("enabled"):
        try:
            rows.extend(enterprise.fetch(enterprise_cfg, keep_title,
                                         keep_location, log))
        except Exception as exc:
            log(f"  {'enterprise':<16} {'(careers)':<22} ! {exc}")
    return rows
