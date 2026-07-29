"""Self-check for the adapters. `python -m sources` (offline) / `--live`.

The offline half asserts the ATS field mapping against response fragments
captured from the real APIs — that's the thing that fails SILENTLY (a wrong
dotted path yields blank titles, not an exception), so it's what needs a test.
"""
import sys

from . import FEED_FETCHERS, ats, fetch_free, optum

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
    optum_offline()
    print(f"offline ok — {len(ats.ATS)} ATS platforms, {len(FEED_FETCHERS)} feeds, "
          f"+ optum")


def live():
    """One real request per ATS platform and per feed."""
    probe = {"greenhouse": {"postman": "Postman"}, "lever": {"cred": "CRED"},
             "ashby": {"linear": "Linear"}, "smartrecruiters": {"BoschGroup": "Bosch"}}
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


if __name__ == "__main__":
    offline()
    if "--live" in sys.argv:
        live()
