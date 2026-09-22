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
import telemetry

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
    "breezy": {
        # Verified live 2026-09-09. Like smartrecruiters the LIST carries no
        # description, so a posting is scored on its title alone — and unlike
        # the others the URL is already absolute in the payload.
        # ponytail: title-only text for this platform too; both would improve
        # together when lazy JD enrichment lands.
        #
        # Added mostly for harvest_ats.py, which iterates this table: every
        # platform here is one more public API it probes for each company a
        # sweep surfaced, and the 40+ Indian employers recorded as "no public
        # ATS" in config.py were only ever probed against four of them.
        "url": "https://{token}.breezy.hr/json",
        "list": None,                      # the response IS the list
        "map": {"Title": "name", "Location": "location.country.name",
                "Job URL": "url", "Posted Date": "published_date"},
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

# ---------------------------------------------------------------------------
# Provider-native identity — DIAGNOSTIC ONLY (Search Engine V2-A)
# ---------------------------------------------------------------------------
# The identity the engine deduplicates on is a HEURISTIC: requisition number if
# a source publishes one, else normalized company + sorted title words, else
# host+path. The V2 audit produced executable counterexamples in both
# directions — two distinct Bengaluru/Hyderabad requisitions collapsing into
# one row, and one posting surviving twice under two company spellings.
#
# Every one of those counterexamples exists because the native identity the
# provider DID publish was thrown away during normalization. `_row` builds a
# row from `map` alone, so a Greenhouse posting's numeric `id` — a stable,
# employer-namespaced key — never reaches the engine at all.
#
# This table records what each provider natively offers, so the NEXT audit can
# tell "same requisition" from "separate vacancy", "repost", "multi-location
# posting", "URL alias" and "tracking-URL variation" using facts rather than
# string heuristics. It is captured under SWEEP_SEARCH_V2_TELEMETRY and stashed
# on the row under "_native", a key to_output() does not read: it CANNOT reach
# ranking, dedupe, the CSV or the JSON. Changing dedupe is a separate, later,
# separately-reviewed decision — this only stops discarding the evidence.
NATIVE = {
    "greenhouse": {"native_id": "id", "internal_id": "internal_job_id",
                   "canonical_url": "absolute_url", "board_company": "company_name",
                   "multi_location": "offices", "updated_at": "updated_at",
                   "requisition": "requisition_id"},
    "lever": {"native_id": "id", "canonical_url": "hostedUrl",
              "apply_url": "applyUrl", "multi_location": "categories.allLocations",
              "published_at": "createdAt", "workplace_type": "workplaceType"},
    "ashby": {"native_id": "id", "canonical_url": "jobUrl",
              "apply_url": "applyUrl", "multi_location": "secondaryLocations",
              "published_at": "publishedAt", "remote_flag": "isRemote",
              # isListed distinguishes a public posting from an unlisted one,
              # which the audit called out as needing a flag of its own.
              "is_listed": "isListed", "workplace_type": "workplaceType"},
    "smartrecruiters": {"native_id": "id", "internal_id": "refNumber",
                        # uuid is a second provider-side identity, and ref is
                        # the employer's own. Both were being discarded.
                        "global_uuid": "uuid", "employer_ref": "ref",
                        "board_company": "company.identifier",
                        "multi_location": "location.region",
                        "published_at": "releasedDate"},
    "breezy": {"native_id": "id", "friendly_id": "friendly_id",
               "canonical_url": "url", "multi_location": "locations",
               "published_at": "published_date"},
}

# No update timestamp is declared for ashby, smartrecruiters or breezy, and that
# is a MEASURED absence, not an oversight: `python -m sources --live` reports how
# many rows each declared field resolves on, an earlier draft of this table
# guessed `updatedAt` / `lastUpdatedOn` / `updated_date`, and all three resolved
# on 0 rows. The real payloads carry no such field. Greenhouse's `updated_at` is
# the only update timestamp any of these five publishes — which is also why the
# audit warns that Greenhouse "freshness" means last-touched, not newly opened.

# Internal schema every adapter must fill (scraper.py's normalized row shape).
# hires_home is filled per BOARD, not per job — see fetch().
BLANK = {"Title": "", "Company": "", "Location": "", "Salary": "",
         "Experience": "", "Posted Date": "", "Job URL": "", "Description": "",
         "hires_home": ""}


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
    # Diagnostic only, and only under the telemetry flag — see NATIVE above.
    # "_native" is not in OUTPUT_COLUMNS and to_output() never reads it, so it
    # cannot reach ranking, dedupe or a written file.
    if telemetry.active():
        row["_native"] = {field: flat(dig(item, path))
                          for field, path in NATIVE.get(platform, {}).items()}
    return row


def fetch(platform, token, company, keep_title, keep_location, is_home=None):
    """All matching jobs from one company's board.

    keep_title / keep_location / is_home are predicates supplied by the caller,
    so this module stays ignorant of which titles or countries you care about.
    """
    spec = ATS[platform]
    data = get_json(spec["url"].format(token=token))
    items = dig(data, spec["list"]) if spec["list"] else data
    rows = [_row(i, platform, token, company, spec) for i in (items or [])]

    # Board-level signal, computed over the UNFILTERED board — this is the whole
    # point and it is why it can't be derived per job. A company posting ANY role
    # in your country demonstrably has an entity or EOR relationship there, so
    # its geo-locked "US Remote" roles are worth pursuing; a company with none is
    # a dead end however the posting is worded. Measured: Postman 12/114 India,
    # OpenAI 9/753, Druva 11/31 -> yes; Linear 0/25, Ramp 0/118 -> no.
    # Free: these rows are already fetched, and were previously just discarded.
    if is_home is not None:
        hires_home = "yes" if any(is_home(r["Location"]) for r in rows) else "no"
        for row in rows:
            row["hires_home"] = hires_home

    survivors = [r for r in rows
                 if keep_title(r["Title"]) and keep_location(r["Location"])]
    # raw = what the endpoint returned, normalized = what mapped into a row,
    # gated = what survived the caller's title/location acquisition predicates.
    # Three different denominators, and the audit had to guess at two of them.
    telemetry.observed(raw=len(items or []), normalized=len(rows),
                       gated=len(survivors), requests=1)
    return survivors
