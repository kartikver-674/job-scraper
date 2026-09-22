"""Self-check for the adapters. `python -m sources` (offline) / `--live`.

The offline half asserts the ATS field mapping against response fragments
captured from the real APIs — that's the thing that fails SILENTLY (a wrong
dotted path yields blank titles, not an exception), so it's what needs a test.
"""
import sys

from . import FEED_FETCHERS, ats, enterprise, fetch_free, optum

# Optum fragments, verbatim from live responses (2026-07-29). Two Optum cards and
# one UnitedHealthcare card, because the whole point of the brand class is that
# careers.unitedhealthgroup.com serves every UHG brand from one index.
OPTUM_LISTING = """
<li><a href="/job/gurgaon/full-stack-engineer/34088/98443445328" data-job-id="98443445328" class="brand-facet brand-facet__optum">
<div><h2>Full Stack Engineer</h2><span class="job-id job-info">2378400</span>
<span class="job-divider"> | </span><span class="job-location 1">Gurgaon, Haryana</span></div></a></li>
<li><a href="/job/wausau/senior-software-engineer/34088/96963085648" data-job-id="96963085648" class="brand-facet brand-facet__optum">
<div><h2>Senior Software Engineer</h2><span class="job-id job-info">2364341</span>
<span class="job-divider"> | </span><span class="job-location 1">Wausau, Wisconsin</span>
<span class="job-divider"> | </span><span class="job-info job-worksetting">Remote</span></div></a></li>
<li><a href="/job/minnetonka/actuarial-analyst/34088/97000000001" data-job-id="97000000001" class="brand-facet brand-facet__uhc">
<div><h2>Actuarial Analyst</h2><span class="job-id job-info">2300001</span>
<span class="job-divider"> | </span><span class="job-location 1">Minnetonka, Minnesota</span></div></a></li>
"""

# datePosted is NOT zero-padded in the real payload — that is the bug this guards.
OPTUM_JD = """
<script type="application/ld+json">{"@context":"http://schema.org","@type":"JobPosting",
"datePosted":"2026-7-15","description":"<p>Optum is a global organization</p>"}</script>
<span class="job-id job-info"> <b>Requisition number:</b> 2371064 </span>
<span class="job-date job-info"> <b>Date posted:</b> 07/15/2026 </span>
<a class="btn-internal-apply" href="https://uhg.taleo.net/careersection/10020/jobapply.ftl?job=2371064">Apply</a>
"""


def optum_offline():
    cards = optum._cards(OPTUM_LISTING, brand="optum")
    assert len(cards) == 2, f"brand filter kept {len(cards)} cards, expected 2"
    first = cards[0]
    assert first["req"] == "2378400", first          # the referral requisition
    assert first["title"] == "Full Stack Engineer", first
    assert first["location"] == "Gurgaon, Haryana", first
    assert first["job_id"] == "98443445328", first
    assert first["url"].startswith("https://careers.unitedhealthgroup.com/job/"), first
    assert cards[1]["work_setting"] == "Remote", cards[1]

    jd = optum.parse_jd(OPTUM_JD)
    assert jd["live"] is True, jd
    assert jd["req"] == "2371064", jd
    assert jd["apply_req"] == "2371064", jd
    # "2026-7-15" -> "2026-07-15", or scraper._parse_date can't read it and every
    # Optum row silently becomes undated (and then either stale or unfiltered).
    assert jd["date_posted"] == "2026-07-15", jd
    assert jd["description"] == "Optum is a global organization", jd

YES = lambda _: True  # noqa: E731 — keep-everything predicates for the checks

# One WWR category feed, trimmed to the fields the adapter reads. "Company: Role"
# in <title> is WWR's own packing and the reason the adapter splits on ": ".
WWR_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Acme: Senior Backend Engineer</title>
<region>Anywhere in the World</region>
<pubDate>Mon, 21 Sep 2026 10:00:00 +0000</pubDate>
<link>https://weworkremotely.com/remote-jobs/acme-senior-backend</link>
<description>&lt;p&gt;Python and Postgres&lt;/p&gt;</description></item>
</channel></rss>"""


def wwr_offline():
    """The concrete failure the V2 audit measured, and the two separate bugs in it.

    1. The catch-all board was configured as a CATEGORY. WWR serves it from the
       root, so /categories/remote-jobs.rss 301s into a failure.
    2. wwr() raised on that failure, which threw away the seven categories that
       had already answered HTTP 200.

    Both are asserted here rather than on somebody's live sweep, because both
    only show up at request time.
    """
    from . import feeds

    # 1 — routes. The whole board is not under /categories/.
    assert feeds._wwr_url("remote-programming-jobs") == (
        "https://weworkremotely.com/categories/remote-programming-jobs.rss")
    assert feeds._wwr_url("remote-jobs") == feeds.WWR_WHOLE_BOARD
    assert "/categories/" not in feeds.WWR_WHOLE_BOARD
    # Every configured category must resolve to a route this adapter can build,
    # so a new entry in config.FEEDS cannot silently repeat the same bug.
    import config
    for category in config.FEEDS["wwr"]["categories"]:
        url = feeds._wwr_url(category)
        assert url.startswith("https://weworkremotely.com/") and url.endswith(".rss"), url

    import xml.etree.ElementTree as ET

    calls = []
    real_get_xml = feeds.get_xml

    def fake(url, **kw):
        """Offline stand-in: every category answers except the catch-all board,
        which is the exact route that failed in the audit — and it is configured
        LAST, so rows from the earlier categories are already in hand."""
        calls.append(url)
        if url == feeds.WWR_WHOLE_BOARD:
            raise OSError("simulated redirect failure")
        return ET.fromstring(WWR_RSS)

    feeds.get_xml = fake
    try:
        cfg = {"categories": ["remote-programming-jobs", "remote-design-jobs",
                              "remote-jobs"]}
        # 2 — partial failure keeps what worked. Two categories answered, one
        # failed: two rows, not zero.
        rows = feeds.wwr(cfg, YES, YES)
        assert len(calls) == 3, calls
        assert len(rows) == 2, f"a failed category erased the successful ones: {rows}"

        # Normalization of a successful row is untouched by any of this.
        row = rows[0]
        assert row["Company"] == "Acme", row
        assert row["Title"] == "Senior Backend Engineer", row
        assert row["Location"] == "Anywhere in the World", row
        assert row["Posted Date"] == "2026-09-21", row
        assert row["Job URL"].endswith("/acme-senior-backend"), row
        assert row["Description"] == "Python and Postgres", row
        assert row["Source"] == "wwr", row

        # Total failure still REPORTS as a failure. A dead feed and a feed with
        # no matching jobs must not look the same to fetch_free's log.
        try:
            feeds.wwr({"categories": ["remote-jobs"]}, YES, YES)
        except RuntimeError as exc:
            assert "every configured feed failed" in str(exc), exc
        else:
            raise AssertionError("a wholly failed WWR feed reported success")

        # Filtered to zero is NOT a failure: every route answered, the
        # predicates kept nothing. Returns empty, raises nothing.
        assert feeds.wwr({"categories": ["remote-design-jobs"]},
                         lambda t: False, YES) == []
    finally:
        feeds.get_xml = real_get_xml


def native_offline():
    """Every ATS platform must declare its provider-native identity fields.

    Without this, adding a platform silently adds one the next dedupe audit has
    no native evidence for — which is the exact hole this table was added to
    close.
    """
    assert set(ats.NATIVE) == set(ats.ATS), (
        f"no NATIVE identity map for: {set(ats.ATS) - set(ats.NATIVE)}")
    for platform, fields in ats.NATIVE.items():
        assert "native_id" in fields, f"{platform} declares no native job id"
        # The diagnostic map must not shadow a scored field: "_native" is
        # metadata for a later audit, never an input to scoring.
        assert not set(fields) & set(ats.BLANK), platform

# Fragments taken verbatim from live responses (2026-07-25).
FIXTURES = {
    "greenhouse": ({"jobs": [{"title": "Backend Engineer", "absolute_url": "https://x/1",
                              "updated_at": "2026-07-01T12:00:00-04:00",
                              "location": {"name": "Bengaluru, India"},
                              "content": "&lt;p&gt;Node.js&lt;/p&gt;"}]},
                   {"Title": "Backend Engineer", "Location": "Bengaluru, India",
                    "Posted Date": "2026-07-01", "Job URL": "https://x/1",
                    "Description": "Node.js"}),
    "lever": ([{"text": "Full Stack Engineer", "hostedUrl": "https://y/2",
                "createdAt": 1782864000000,   # epoch ms -> 2026-07-01
                "categories": {"location": "Remote", "commitment": "Full-time"},
                "descriptionPlain": "React and Node"}],
              {"Title": "Full Stack Engineer", "Location": "Remote",
               "Posted Date": "2026-07-01", "Job URL": "https://y/2",
               "Experience": "Full-time", "Description": "React and Node"}),
    "ashby": ({"jobs": [{"title": "Senior / Staff Fullstack Engineer", "id": "d3b",
                         "location": "Europe", "isRemote": True,
                         "publishedAt": "2021-04-27T20:13:45.158+00:00",
                         "jobUrl": "https://jobs.ashbyhq.com/linear/d3b",
                         "descriptionPlain": "TypeScript"}]},
              {"Title": "Senior / Staff Fullstack Engineer", "Location": "Europe, Remote",
               "Posted Date": "2021-04-27", "Job URL": "https://jobs.ashbyhq.com/linear/d3b",
               "Description": "TypeScript"}),
    "breezy": ([{"name": "Senior Backend Engineer", "id": "98323abf2296",
                 "url": "https://acme.breezy.hr/p/98323abf2296-senior-backend",
                 "published_date": "2026-09-01T10:31:00Z",
                 "location": {"country": {"name": "India", "id": "IN"},
                              "city": "Bengaluru"},
                 "type": {"id": "full-time", "name": "Full-Time"}}],
                {"Title": "Senior Backend Engineer", "Location": "India",
                 "Posted Date": "2026-09-01",
                 "Job URL": "https://acme.breezy.hr/p/98323abf2296-senior-backend"}),
    "smartrecruiters": ({"content": [{"id": "744000139823759", "name": "SAP Specialist",
                                      "releasedDate": "2026-07-25T12:42:34.909Z",
                                      "location": {"fullLocation": "bangalore, , India",
                                                   "remote": False}}]},
                        {"Title": "SAP Specialist", "Location": "bangalore, , India",
                         "Posted Date": "2026-07-25",
                         "Job URL": "https://jobs.smartrecruiters.com/acme/744000139823759"}),
}


def offline():
    for platform, (payload, expect) in FIXTURES.items():
        spec = ats.ATS[platform]
        items = payload if spec["list"] is None else payload[spec["list"]]
        row = ats._row(items[0], platform, "acme", "Acme", spec)
        for field, want in expect.items():
            assert row[field] == want, f"{platform}.{field}: {row[field]!r} != {want!r}"
        assert row["Source"] == f"{platform}:acme"
        assert row["Company"] == "Acme"
        assert set(row) >= set(ats.BLANK), f"{platform} dropped schema keys"
    # Every table entry must be exercised above, or an unverified one slips in.
    assert set(FIXTURES) == set(ats.ATS), (
        f"untested ATS entries: {set(ats.ATS) - set(FIXTURES)}")
    # himalayas: which endpoint a config chooses. The search API did not exist
    # when that adapter was written; a profile with role keywords must use it,
    # and one without must still fall back to paging rather than fetch nothing.
    from .feeds import _himalayas_urls
    searched = _himalayas_urls({"queries": ["react native", "node.js"], "pages": 10})
    assert len(searched) == 2, searched
    assert all("/jobs/api/search?" in u for u in searched), searched
    assert "q=react%20native" in searched[0], searched[0]
    browsed = _himalayas_urls({"pages": 3})
    assert len(browsed) == 3 and all("offset=" in u for u in browsed), browsed
    assert _himalayas_urls({"queries": ["  ", ""], "pages": 3}) == browsed, \
        "blank queries must fall back to paging, not fetch nothing"
    capped = _himalayas_urls({"queries": [f"q{i}" for i in range(30)]})
    assert len(capped) == 8, f"uncapped queries would rate-limit: {len(capped)}"
    wwr_offline()
    native_offline()
    optum_offline()
    # Same contract for the enterprise adapters: their date shapes and the
    # SuccessFactors row regex fail silently (blank dates, blank titles), so
    # they are asserted here rather than discovered on a live sweep.
    enterprise.demo()
    print(f"offline ok — {len(ats.ATS)} ATS platforms, "
          f"{len(FEED_FETCHERS)} feeds, {len(enterprise.EMPLOYERS)} employers, "
          f"+ optum")


def live():
    """One real request per ATS platform and per feed."""
    # greenhouse:gitlab, not greenhouse:postman. Postman's board returns 404 —
    # the V2 audit measured it, and a follow-up spot-check reconfirmed it — so
    # this self-check has been failing on a dead probe board rather than on
    # anything it was written to catch. GitLab's board is large, public and
    # stable. config.ATS_BOARDS is deliberately NOT touched here: whether the
    # Postman token is dead or merely migrated is a registry decision, and one
    # 404 is not grounds for deleting an employer (see the audit's §4).
    probe = {"greenhouse": {"gitlab": "GitLab"}, "lever": {"cred": "CRED"},
             "ashby": {"linear": "Linear"}, "smartrecruiters": {"BoschGroup": "Bosch"},
             # Breezy's own board — a public one that is always up, since this
             # asserts every platform in the table answers.
             "breezy": {"breezy": "Breezy"}}
    feed_cfg = {"remoteok": {"enabled": True},
                "wwr": {"enabled": True, "categories": ["remote-programming-jobs"]},
                "remotive": {"enabled": True},
                "jobicy": {"enabled": True, "count": 20},
                "himalayas": {"enabled": True, "pages": 1}}
    rows = fetch_free(probe, feed_cfg, YES, YES)
    by_source = {}
    for r in rows:
        by_source[r["Source"].split(":")[0]] = by_source.get(r["Source"].split(":")[0], 0) + 1
    print(f"\nlive: {len(rows)} rows {by_source}")
    expected = set(probe) | set(feed_cfg)
    missing = expected - set(by_source)
    assert not missing, f"these sources returned nothing: {sorted(missing)}"
    blank = [r for r in rows if not r["Title"] or not r["Job URL"]]
    assert not blank, f"{len(blank)} rows missing title/url, e.g. {blank[0]}"
    # The structured feeds are the only free source of pay data — if none of
    # them reports a salary, a field name has drifted and the pay filter is
    # quietly filtering nothing.
    paid = [r for r in rows if r["Salary"]]
    assert paid, "no source reported pay; check the salary field maps in feeds.py"
    print(f"live ok ({len(paid)} rows with pay)")

    # Provider-native identity availability, measured rather than assumed. A
    # wrong dotted path in ats.NATIVE / feeds.NATIVE fails exactly the way a
    # wrong path in the field map fails — silently, as a blank — so the only
    # way to know the map is right is to point it at a real response and count.
    # This is what makes the identity table in
    # docs/search-engine-v2-a-telemetry-and-shadow.md evidence and not a guess.
    import os

    import telemetry
    os.environ[telemetry.FLAG] = "1"
    telemetry.start("free", "/tmp")
    try:
        native_rows = fetch_free(probe, feed_cfg, YES, YES)
    finally:
        telemetry.finish()
        os.environ.pop(telemetry.FLAG, None)
    by_source = {}
    for r in native_rows:
        by_source.setdefault(r["Source"].split(":")[0], []).append(r)
    print("\nprovider-native identity fields present (rows with a value / rows):")
    for source in sorted(by_source):
        got = by_source[source]
        fields = sorted({f for r in got for f in (r.get("_native") or {})})
        if not fields:
            print(f"  {source:<16} no NATIVE map")
            continue
        parts = [f"{f}={sum(1 for r in got if (r.get('_native') or {}).get(f))}"
                 for f in fields]
        print(f"  {source:<16} n={len(got):<4} " + " ".join(parts))
    # A declared field that resolves on NO row is a wrong path, not an absent
    # provider field — say so loudly rather than publishing a zero as a finding.
    for source, got in by_source.items():
        for field in sorted({f for r in got for f in (r.get("_native") or {})}):
            if not any((r.get("_native") or {}).get(field) for r in got):
                print(f"  ! {source}.{field} resolved on 0/{len(got)} rows — "
                      f"verify the path before quoting it as unavailable")


if __name__ == "__main__":
    offline()
    if "--live" in sys.argv:
        live()
