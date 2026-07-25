"""Company career sites via public ATS APIs — free, unauthenticated, stdlib.

Adding an ATS PLATFORM is a dict entry in ATS below: a URL template, where the
job list lives in the response, and a field -> dotted-path map. No new function,
no new file. Adding a COMPANY is one token in config.ATS_BOARDS.

Every entry here was probed against a live board (2026-07-25) and its field
paths read off the real response. Do NOT add an unverified entry: a wrong path
doesn't raise, it silently yields a board full of blank titles.

Known gaps, deliberately left out rather than guessed at:
  workable  — apply.workable.com/api/v1/widget/accounts/<slug>?details=true is
              the live endpoint (200), but every slug probed returned zero jobs,
              so the per-job field names are unverified.
  workday   — needs a POST body and a per-tenant hostname, so it can't be a
              row in this table without adding a request-body key.
"""
from ._http import dig, flat, get_json, strip_html

ATS = {
    "greenhouse": {
        "url": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
        "list": "jobs",
        "map": {"Title": "title", "Location": "location.name",
                "Job URL": "absolute_url", "Posted Date": "updated_at",
                "Description": "content"},
    },
    "lever": {
        "url": "https://api.lever.co/v0/postings/{token}?mode=json",
        "list": None,                      # the response IS the list
        "map": {"Title": "text", "Location": "categories.location",
                "Job URL": "hostedUrl", "Posted Date": "createdAt",  # epoch ms
                "Experience": "categories.commitment",
                "Description": "descriptionPlain"},
    },
    "ashby": {
        "url": "https://api.ashbyhq.com/posting-api/job-board/{token}",
        "list": "jobs",
        "remote_flag": "isRemote",         # explicit remote signal — rare, use it
        "map": {"Title": "title", "Location": "location",
                "Job URL": "jobUrl", "Posted Date": "publishedAt",
                "Description": "descriptionPlain"},
    },
    "smartrecruiters": {
        # The postings LIST carries neither a description nor a job URL, so the
        # URL is built from the token + id and the job is scored on its title
        # alone.
        # ponytail: title-only text for this platform; wire the per-posting
        # detail fetch (/postings/<id>) in when lazy JD enrichment lands.
        "url": "https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100",
        "list": "content",
        "url_fmt": "https://jobs.smartrecruiters.com/{token}/{id}",
        "id": "id",
        "remote_flag": "location.remote",
        "map": {"Title": "name", "Location": "location.fullLocation",
                "Posted Date": "releasedDate"},
    },
}

# Internal schema every adapter must fill (scraper.py's normalized row shape).
BLANK = {"Title": "", "Company": "", "Location": "", "Salary": "",
         "Experience": "", "Posted Date": "", "Job URL": "", "Description": ""}


def _date(value):
    """ATS date fields are either an ISO string or epoch ms (lever). Both ->
    YYYY-MM-DD, which is what scraper._parse_date reads best."""
    if isinstance(value, (int, float)) and value > 0:
        from datetime import datetime, timezone
        secs = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(secs, timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return ""
    return flat(value)[:10]


def _row(item, platform, token, company, spec):
    row = dict(BLANK, Source=f"{platform}:{token}", Company=company)
    for field, path in spec["map"].items():
        row[field] = flat(dig(item, path))
    row["Posted Date"] = _date(dig(item, spec["map"].get("Posted Date")))
    row["Description"] = strip_html(row["Description"])
    if spec.get("url_fmt"):
        row["Job URL"] = spec["url_fmt"].format(token=token, id=flat(dig(item, spec["id"])))
    # A platform-native remote flag beats guessing from prose, so surface it
    # where scraper.is_remote() will see it.
    if spec.get("remote_flag") and dig(item, spec["remote_flag"]) is True:
        row["Location"] = (row["Location"] + ", Remote").lstrip(", ")
    return row


def fetch(platform, token, company, keep_title, keep_location):
    """All matching jobs from one company's board.

    keep_title / keep_location are predicates supplied by the caller, so this
    module stays ignorant of which titles or countries you care about.
    """
    spec = ATS[platform]
    data = get_json(spec["url"].format(token=token))
    items = dig(data, spec["list"]) if spec["list"] else data
    return [r for r in (_row(i, platform, token, company, spec) for i in (items or []))
            if keep_title(r["Title"]) and keep_location(r["Location"])]
