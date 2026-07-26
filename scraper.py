"""
Job aggregator -> ranked CSV + JSON. Free sources + paid Apify actors.

Pipeline:
    1. Build a search plan = role_keywords x locations (from config.SEARCH).
    2. Pull jobs from two families, both normalized into one common schema:
         free  — company ATS boards + public remote feeds, via sources/ (no cost)
         paid  — one Apify actor run per (enabled site, search combo)
    3. Score each job against the resume (config.SCORING): weighted skills, a
       full-stack bonus for frontend+backend overlap, and hard down-ranking /
       filtering of wrong-seniority, off-stack, and Salesforce/CRM roles.
    4. Enrich with the signals that decide whether a remote job is reachable
       from here — remote scope, visa sponsorship, employer-of-record, timezone
       overlap (enrich.py) — then filter on freshness, on compensation
       annualized in USD so Indian LPA and international salaries compare on one
       axis (comp_max_usd), and optionally on those signals.
    5. De-duplicate on company + title — NOT location, which is the field that
       varies most across sources for the same posting (job_key).
    6. Sort by score (highest first) and write a timestamped CSV + JSON.

Adding a source never means editing this file: a new ATS platform is a dict
entry in sources/ats.py, a new feed is a function in sources/feeds.py, and a new
company or board token is one line in config.ATS_BOARDS.

Usage:
    pip install -r requirements.txt
    python scraper.py --demo                    # offline self-check, no network
    python scraper.py --site free               # free sources only, zero cost
    python scraper.py --dry-run                 # print the plan, spend nothing
    python scraper.py --test                    # tiny: 1 keyword x 1 location, indeed only
    python scraper.py --site indeed --limit 3   # one site, first 3 combos
    python scraper.py                           # full sweep (confirms if large)

Flags:
    --dry-run        Show the planned searches + per-site actor inputs; no actor runs.
    --demo           Offline self-check of comp parsing + dedupe identity; exit.
    --test           Smallest possible real run (first keyword x first location, indeed).
    --site NAME      One site only: indeed/naukri/linkedin, or ats/feeds/free.
    --limit N        Cap (keyword x location) combos per site to N.
    --no-free        Skip the free sources.
    --yes            Skip the "large sweep" confirmation prompt.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta

import config
import enrich
import sources
from sources._http import strip_html as _strip_html
from config import (SEARCH, SITES, SCORING, SETTINGS, NAUKRI_CITY_IDS,
                    LINKEDIN_GEO_IDS, ATS_BOARDS, FEEDS,
                    LOCATION_HINTS, HOME_LOCATION_HINTS, ATS_TITLE_HINTS)

# ---------------------------------------------------------------------------
# Internal common schema (title-cased keys) produced by normalize(). The final
# output columns (score, matched_skills, ...) are assembled later in to_output().
# ---------------------------------------------------------------------------
FIELD_KEYS = {
    "Title":       ["positionName", "title", "jobTitle", "position", "name"],
    "Company":     ["company", "companyName", "company_name", "employer"],
    "Location":    ["location", "jobLocation", "place", "city", "formattedLocation"],
    "Salary":      ["salary", "salaryInfo", "salaryRange", "salary_text",
                    "compensation", "salaryText"],
    "Experience":  ["experience", "experienceRange", "exp", "experienceYears",
                    "experienceText"],
    "Posted Date": ["postingDateParsed", "postedAt", "postedTime", "postedDate",
                    "date", "publishedAt", "postedDateTime", "listedAt"],
    "Job URL":     ["url", "jobUrl", "link", "externalApplyLink", "applyUrl",
                    "jobPostingUrl", "jobLink"],
    "Description": ["description", "descriptionText", "jobDescription",
                    "descriptionHtml", "jobDesc"],
}

# Final CSV / JSON columns, in order (exactly as requested).
OUTPUT_COLUMNS = [
    "score", "matched_skills", "is_fullstack", "title", "company", "location",
    "remote?", "remote_scope", "hires_home", "tz_gap", "remote_regions",
    "visa", "eor", "timezones",
    "experience_required", "salary", "hr_email", "hr_phone",
    "source_site", "apply_url", "date_posted",
]


# ===========================================================================
# Value flattening / normalization
# ===========================================================================
def _flatten(value):
    """Turn dict/list/None values into a readable string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(_flatten(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        for k in ("text", "name", "label", "value", "displayName"):
            if k in value:
                return _flatten(value[k])
        return ", ".join(f"{k}: {_flatten(v)}" for k, v in value.items())
    return str(value)


def _pick(item, keys):
    for k in keys:
        if k in item and item[k] not in (None, "", [], {}):
            return _flatten(item[k])
    return ""


def _truncate_desc(row):
    limit = SETTINGS["description_max"]
    if len(row["Description"]) > limit:
        row["Description"] = row["Description"][:limit] + "…"
    return row


def normalize_naukri(item):
    """Naukri nests everything under item['jobDetails'] with its own field names,
    so it needs a dedicated mapping (the flat FIELD_KEYS can't reach it)."""
    jd = item.get("jobDetails") or {}
    cd = jd.get("companyDetail") or {}
    locs = jd.get("locations") or []
    location = ", ".join(l.get("label", "") for l in locs
                         if isinstance(l, dict) and l.get("label"))
    sal = jd.get("salaryDetail") or {}
    salary = ""
    if isinstance(sal, dict) and not sal.get("hideSalary"):
        lo, hi = sal.get("minimumSalary") or 0, sal.get("maximumSalary") or 0
        if lo or hi:
            salary = f"{sal.get('currency', '')} {lo}-{hi}".strip()
    job_id = jd.get("jobId", "")
    url = (jd.get("staticUrl") or jd.get("applyRedirectUrl")
           or (f"https://www.naukri.com/job-listings-{job_id}" if job_id else ""))
    row = {
        "Source": "naukri",
        "Title": jd.get("title") or jd.get("jobRole") or "",
        "Company": cd.get("name", "") if isinstance(cd, dict) else "",
        "Location": location,
        "Salary": salary,
        "Experience": jd.get("experienceText", ""),
        "Posted Date": jd.get("createdDate", ""),
        "Job URL": url,
        "Description": _strip_html(jd.get("description", "")),
    }
    return _truncate_desc(row)


def normalize(item, source):
    if source == "naukri":
        return normalize_naukri(item)
    row = {"Source": source}
    for col, keys in FIELD_KEYS.items():
        row[col] = _pick(item, keys)
    return _truncate_desc(row)


# ===========================================================================
# Free sources (company ATS boards + public remote feeds) — see sources/
# ===========================================================================
# The adapters live in sources/; scraper.py only supplies the two policy
# predicates below, so widening coverage never means editing this file.
def location_allowed(loc):
    """Keep a job whose location mentions a config.LOCATION_HINTS entry.

    Empty hints = allow everything (the default now that the target is
    international remote). An unspecified location is always kept — scoring and
    the remote/comp filters sort it out.
    """
    if not LOCATION_HINTS or not loc:
        return True
    low = loc.lower()
    return any(h in low for h in LOCATION_HINTS)


def is_dev_title(title):
    """Free sources return a whole board; keep only software/dev-looking titles."""
    low = (title or "").lower()
    return any(h in low for h in ATS_TITLE_HINTS)


def is_home_location(loc):
    """True if a location is in the country you're applying FROM. Used to ask a
    company board "does this employer hire here at all", not to filter jobs."""
    low = (loc or "").lower()
    return any(h in low for h in HOME_LOCATION_HINTS)


def fetch_free():
    """Every configured ATS board + feed. Free; per-board failures are isolated."""
    rows = sources.fetch_free(ATS_BOARDS, FEEDS, is_dev_title, location_allowed,
                              is_home_location)
    return [_truncate_desc(r) for r in rows]


# ===========================================================================
# Resume-relevance scoring layer
# ===========================================================================
def _compile(term):
    """Case-insensitive, alphanumeric-boundary matcher for a single term.

    Lookarounds (instead of \\b) so punctuated terms match cleanly: ".net",
    "node.js", "socket.io", "c#", "5+ years" all work, and "lead" won't fire
    inside "leadership".
    """
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])")


# Precompile everything once from config.
SKILL_PATTERNS   = {t: (w, _compile(t)) for t, w in SCORING["skill_weights"].items()}
PENALTY_PATTERNS = {t: (p, _compile(t)) for t, p in SCORING["penalty_terms"].items()}
FRONTEND_PATTERNS = [_compile(t) for t in SCORING["frontend_terms"]]
BACKEND_PATTERNS  = [_compile(t) for t in SCORING["backend_terms"]]
FULLSTACK_TITLE_PATTERNS = [_compile(t) for t in SCORING["fullstack_title_terms"]]
HARD_DROP_PATTERNS = {t: _compile(t) for t in SCORING["hard_drop_terms"]}
SOFT_DROP_PATTERNS = {t: _compile(t) for t in SCORING["soft_drop_terms"]}

# Any "<n> years/yrs" mention (optionally "n+" or "n-m"); we read the leading n.
YEARS_PATTERN = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)")


def _required_experience_floor(text):
    """Smallest 'N years' figure mentioned — a proxy for the minimum experience
    demanded. 'company founded 5 years ago' plus a real '2 years' requirement
    resolves to 2 (kept); a lone '5+ years' resolves to 5 (over threshold)."""
    nums = [int(m.group(1)) for m in YEARS_PATTERN.finditer(text)]
    return min(nums) if nums else None


def is_remote(row):
    """True when the job can be worked from elsewhere at all.

    Delegates to enrich.remote_scope rather than matching the bare word
    "remote", which fired on "this role is not remote" and on "hybrid, with
    occasional remote days".
    """
    return row.get("remote_scope") in enrich.REMOTE_SCOPES


# --- Freshness (posted-date) filter ----------------------------------------
def _parse_date(s):
    """Tolerant parser for the varied 'Posted Date' formats across sources.
    Returns a datetime, or None if it can't be parsed."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)  # YYYY-MM-DD prefix (most sources)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    low = s.lower()
    m = re.search(r"(\d+)\s*\+?\s*day", low)      # "30+ days ago"
    if m:
        return datetime.now() - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*\+?\s*week", low)      # "2 weeks ago"
    if m:
        return datetime.now() - timedelta(weeks=int(m.group(1)))
    if any(w in low for w in ("today", "just posted", "hour", "minute", "moment")):
        return datetime.now()
    return None


def is_recent(date_str, max_age_days):
    d = _parse_date(date_str)
    if d is None:
        return not SETTINGS["drop_undated"]   # unknown date -> keep unless configured to drop
    return (datetime.now() - d).days <= max_age_days


# --- Compensation filter (multi-currency) ----------------------------------
# Annualized and converted to USD so an 18 LPA India role and a $180k US remote
# role land on the same axis. The previous version assumed rupees, which meant
# "$220,000 a year" parsed as 2.2 LPA and got DROPPED by the salary floor — i.e.
# the filter silently deleted the best-paying international roles.
#
# Rates are a hardcoded snapshot ON PURPOSE: this feeds a coarse above/below-floor
# filter, and an FX API would be a dependency plus a network failure mode for a
# number that only needs to be right to ~5%. Refresh occasionally.
USD_PER = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "AUD": 0.65,
           "SGD": 0.74, "CHF": 1.12, "AED": 0.27, "INR": 0.0114, "JPY": 0.0064}

# Longest / most specific markers first: "us$" and "c$" must win over bare "$".
_CURRENCY_TOKENS = [
    ("us$", "USD"), ("usd", "USD"), ("c$", "CAD"), ("cad", "CAD"),
    ("a$", "AUD"), ("aud", "AUD"), ("s$", "SGD"), ("sgd", "SGD"),
    ("₹", "INR"), ("inr", "INR"), ("rs.", "INR"), ("rs ", "INR"),
    ("€", "EUR"), ("eur", "EUR"), ("£", "GBP"), ("gbp", "GBP"),
    ("chf", "CHF"), ("aed", "AED"), ("¥", "JPY"), ("jpy", "JPY"), ("$", "USD"),
]
# Indian scale words: "12-18 LPA" means 12-18 *lakh*, with the scale in the unit
# rather than on the digits.
_SCALE_WORDS = [("crore", 1e7), ("lakh", 1e5), ("lac", 1e5), ("lpa", 1e5)]
_SUFFIX = {"k": 1e3, "m": 1e6, "l": 1e5, "lakh": 1e5, "lac": 1e5,
           "cr": 1e7, "crore": 1e7}
# Value -> pay periods per year. 2080 = 40h x 52w.
_PERIODS = [("hour", 2080), ("hourly", 2080), ("/hr", 2080), ("/h", 2080),
            ("day", 260), ("week", 52),
            ("month", 12), ("monthly", 12), ("/mo", 12), ("p.m", 12),
            ("annum", 1), ("year", 1), ("yearly", 1), ("/yr", 1)]
_UNDISCLOSED = ("not disclosed", "not specified", "unpaid", "competitive",
                "as per", "negotiable", "depending on experience", "doe")
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(k|m|l|lakh|lac|cr|crore)?", re.I)


def comp_max_usd(text):
    """Best-effort MAX annual compensation in USD, or None.

    None means "don't filter on this": undisclosed, unparseable, or — critically
    — no identifiable currency. A bare "50000-80000 per month" could be rupees
    or dollars, an order of magnitude apart, so we fail OPEN and keep the job
    rather than guess and delete it.
    """
    t = (text or "").strip().lower()
    if not t or any(x in t for x in _UNDISCLOSED):
        return None
    currency = next((c for tok, c in _CURRENCY_TOKENS if tok in t), None)
    scale = next((s for w, s in _SCALE_WORDS if w in t), None)
    if scale is None and re.search(r"(?<![a-z])cr(?![a-z])", t):
        scale = 1e7
    if scale and currency is None:
        currency = "INR"            # lakh/crore wording is rupees by definition
    if currency is None:
        return None                 # no currency -> no guess -> no filtering
    values = []
    for m in _NUM_RE.finditer(t.replace(",", "")):
        value = float(m.group(1))
        if value <= 0:
            continue
        values.append(value * (_SUFFIX.get(m.group(2) or "") or scale or 1))
    if not values:
        return None
    top = max(values)
    per_year = next((p for word, p in _PERIODS if word in t), None)
    if per_year is None:
        # No stated period: a five-figure+ number is annual, a small one is an
        # hourly rate.
        per_year = 1 if top >= 10000 else (2080 if top <= 500 else 1)
    return top * per_year * USD_PER[currency]


def comp_ok(salary_text, min_usd):
    top = comp_max_usd(salary_text)
    return True if top is None else top >= min_usd


# --- HR contact extraction (best-effort — only ~a few % of posts include it) ---
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Indian mobile: optional +91/0 prefix, 10 digits starting 6-9, optional separator.
PHONE_RE = re.compile(r"(?<!\d)(?:(?:\+?91|0)[\-\s]?)?[6-9]\d{4}[\-\s]?\d{5}(?!\d)")


def extract_contacts(text):
    """Return (emails, phones) found in text, each a '; '-joined unique string."""
    text = text or ""
    emails = list(dict.fromkeys(m.group(0) for m in EMAIL_RE.finditer(text)))
    phones = list(dict.fromkeys(re.sub(r"[\s\-]", "", m.group(0))
                                for m in PHONE_RE.finditer(text)))
    return "; ".join(emails), "; ".join(phones)


def score_job(row):
    """Attach score/matched_skills/is_fullstack/remote? to a normalized row.

    Returns the row, or None if it's hard-filtered (wrong seniority / too much
    experience and SETTINGS['drop_excluded'] is True).
    """
    title = (row.get("Title") or "").lower()
    # Include Experience (e.g. naukri's "2-4 Yrs") so the over-experience filter
    # sees it — it isn't always repeated in the description.
    text = (title + "\n" + (row.get("Description") or "") + "\n"
            + (row.get("Experience") or "")).lower()

    # --- Hard filters: unreachable title, or more experience than we have -----
    # A title is a LABEL; the years the text demands are the requirement. So only
    # hard_drop_terms (manager/principal/staff/...) and a stated experience floor
    # over the threshold remove a job. "Senior"/"Lead" are handled below as a
    # down-rank, because title inflation would otherwise delete reachable roles.
    excluded = any(pat.search(title) for pat in HARD_DROP_PATTERNS.values())
    floor = _required_experience_floor(text)
    if floor is not None and floor > SETTINGS["max_experience_years"]:
        excluded = True

    if excluded and SETTINGS["drop_excluded"]:
        return None

    soft_seniority = any(pat.search(title) for pat in SOFT_DROP_PATTERNS.values())

    # --- Positive skill matches ---
    score = 0
    matched = []
    for term, (weight, pat) in SKILL_PATTERNS.items():
        if pat.search(text):
            score += weight
            matched.append(term)

    # --- Full-stack detection + bonus ---
    has_frontend = any(pat.search(text) for pat in FRONTEND_PATTERNS)
    has_backend = any(pat.search(text) for pat in BACKEND_PATTERNS)
    title_says_fullstack = any(pat.search(title) for pat in FULLSTACK_TITLE_PATTERNS)
    is_fullstack = (has_frontend and has_backend) or title_says_fullstack
    if is_fullstack:
        score += SCORING["fullstack_bonus"]

    # --- Penalties (off-stack + Salesforce/CRM) ---
    for term, (penalty, pat) in PENALTY_PATTERNS.items():
        if pat.search(text):
            score += penalty

    # --- Down-ranks that keep the job in the list ---
    if soft_seniority:
        score += SCORING["soft_penalty"]
    if excluded:  # only reached when drop_excluded is False
        score += SCORING["drop_penalty"]

    row["score"] = score
    row["matched_skills"] = ", ".join(dict.fromkeys(matched))  # dedup, preserve order
    row["is_fullstack"] = is_fullstack
    enrich.enrich(row, SETTINGS["home_utc_offset"])   # remote/visa/eor/tz signals
    row["remote?"] = is_remote(row)

    # Timezone distance: a down-rank, not a filter. A 13.5h gap to US Pacific is
    # a real cost to weigh against the role, not a disqualification. Rounded so
    # the score column stays integral.
    if isinstance(row.get("tz_gap"), (int, float)):
        over = row["tz_gap"] - enrich.TZ_FREE_HOURS
        if over > 0:
            row["score"] = round(row["score"] + SCORING["timezone_gap_penalty"] * over)
    email, phone = extract_contacts((row.get("Description") or "") + "\n"
                                    + (row.get("Title") or ""))
    row["hr_email"] = email
    row["hr_phone"] = phone
    return row


# ===========================================================================
# Cross-site de-duplication
# ===========================================================================
def _norm_key(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


# "Acme Technologies Pvt Ltd" and "Acme" are the same employer to a job board.
_CORP_SUFFIX_RE = re.compile(
    r"\s+(inc|llc|ltd|limited|corp|corporation|co|pvt|private|gmbh|bv|nv|ab|oy"
    r"|as|sa|sas|srl|plc|group|holdings|technologies|technology|labs|software"
    r"|solutions|systems)$")


def _company_key(name):
    key = _norm_key(name)
    while True:                      # strip stacked suffixes, right to left
        stripped = _CORP_SUFFIX_RE.sub("", key)
        if stripped == key:
            return key
        key = stripped


def _title_key(title):
    """Order-insensitive title key: "Engineer, Backend" == "Backend Engineer"."""
    return " ".join(sorted(_norm_key(title).split()))


def _canonical_url(url):
    """Host + path only — drops the UTM/tracking query that makes the same
    posting look like several different URLs."""
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    return (host + parts.path.rstrip("/")).lower()


def job_key(row):
    """Identity for de-duplication, or None when the row has nothing to key on.

    Keyed on company + title and deliberately NOT on location: location is the
    field that varies MOST across sources for exactly the jobs we care about, so
    including it defeated the dedupe. One remote role listed on LinkedIn
    ("Remote"), We Work Remotely ("Anywhere in the World") and the company's own
    Ashby board ("Europe, Remote") used to produce three rows; now it produces
    one. Falls back to the canonical URL when there's no company name.

    Accepts both the internal schema ("Title") and the output schema ("title"),
    so merge_jobs.py can share it instead of keeping a second copy.
    """
    company = _company_key(row.get("Company") or row.get("company"))
    title = _title_key(row.get("Title") or row.get("title"))
    if company and title:
        return "ct", company, title
    url = _canonical_url(row.get("Job URL") or row.get("apply_url"))
    return ("url", url) if url else None


def dedupe(rows):
    """Drop duplicate postings. Assumes rows are already sorted best-first, so
    the first seen (highest score) wins."""
    seen = set()
    unique = []
    for row in rows:
        key = job_key(row)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        unique.append(row)
    return unique


# ===========================================================================
# Per-site input adapters (one SEARCH combo -> that Actor's input shape)
# ===========================================================================
def _naukri_freshness(max_age_days):
    """Map max_age_days to naukri's freshness enum (all/30/15/7/3/1). 14 -> '15'."""
    if max_age_days is None:
        return "all"
    for cut in (1, 3, 7, 15, 30):
        if max_age_days <= cut:
            return str(cut)
    return "all"


def _linkedin_experience_code(years):
    if years is None:
        return None
    if years < 1:
        return "2"   # entry level
    if years <= 2:
        return "3"   # associate
    if years <= 5:
        return "4"   # mid-senior
    return "5"       # director


def _build_linkedin_url(s):
    """One LinkedIn jobs-search URL for a search combo.

    Two things here cost money if got wrong, so neither is guessed:

    1. LinkedIn honors a numeric geoId and IGNORES the free-text location, so an
       unmapped place silently returns US results. This used to fall back to
       `location=<name>`, which meant paying full price for the wrong country.
       It now raises instead — before any actor is started, so nothing is spent.
    2. f_WT=2 filters workplace type WITHIN a geography; it is not a worldwide
       remote search. A bare "Remote" location therefore needs to be told which
       region it means (SITES["linkedin"]["remote_geo"]). That was hardcoded to
       India, which quietly turned every remote sweep into an India-remote sweep.
       For a global sweep, list countries as locations and set remote_only.
    """
    cfg = SITES.get("linkedin", {})
    loc = (s.get("location") or "").strip()
    remote_only = bool(cfg.get("remote_only"))
    if loc.lower() == "remote":
        remote_only = True
        loc = (cfg.get("remote_geo") or "").strip()
        if not loc:
            raise ValueError(
                "linkedin: location 'Remote' needs SITES['linkedin']['remote_geo'] "
                "to say WHICH region (f_WT=2 filters remote within a geography, it "
                "is not a worldwide search).")
    geo_id = LINKEDIN_GEO_IDS.get(loc)
    if not geo_id:
        raise ValueError(
            f"linkedin: no geoId for '{loc}'. LinkedIn ignores a free-text location "
            f"and returns US results, so this would spend money on the wrong "
            f"country. Add it to config.LINKEDIN_GEO_IDS, then confirm it with "
            f"`python verify_geoids.py`.")
    params = {"keywords": s["keywords"], "geoId": geo_id}
    if remote_only:
        params["f_WT"] = "2"
    code = _linkedin_experience_code(s.get("experience_years"))
    if code:
        params["f_E"] = code
    if SETTINGS["max_age_days"]:  # LinkedIn "posted in last N days" = f_TPR=r<seconds>
        params["f_TPR"] = f"r{int(SETTINGS['max_age_days']) * 86400}"
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)


def build_input(site_key, s):
    """Map one search combo (keywords/location/country/experience/max_results)
    onto the actor's expected input schema."""
    if site_key == "indeed":
        return {
            "position": s["keywords"],
            "location": s.get("location", ""),
            "country": s.get("country", "US"),
            "maxItemsPerSearch": s["max_results"],
            "parseCompanyDetails": False,
            "saveOnlyUniqueItems": True,
            "followApplyRedirects": False,
        }
    if site_key == "linkedin":
        return {
            "urls": [_build_linkedin_url(s)],
            "count": max(10, s["max_results"]),  # actor requires count >= 10
            "scrapeCompany": False,
        }
    if site_key == "naukri":
        # Naukri wants numeric city IDs (not names) and a workMode filter for
        # remote. Names map to IDs via config.NAUKRI_CITY_IDS; "Remote" (no ID)
        # becomes a workMode filter instead.
        inp = {
            "keyword": s["keywords"],
            "maxJobs": s["max_results"],
            "fetchDetails": True,
            "sortBy": "relevance",
            "freshness": _naukri_freshness(SETTINGS["max_age_days"]),  # source-side recency
        }
        loc = (s.get("location") or "").strip()
        if loc.lower() == "remote":
            inp["workMode"] = ["remote"]
        elif loc:
            city_id = NAUKRI_CITY_IDS.get(loc)
            if city_id:
                inp["cities"] = [city_id]
            else:
                print(f"    (naukri: no city ID for '{loc}' — searching all India)")
        if s.get("experience_years") is not None:
            inp["experience"] = str(s["experience_years"])  # valid enum: "0".."30"
        return inp
    raise ValueError(f"No input adapter for site '{site_key}'")


# ===========================================================================
# Search plan
# ===========================================================================
def build_search_plan(keywords, locations):
    """Cross product of keywords x locations (one search dict each). Both are
    per-site overridable (SITES[site]["keywords"|"locations"]) and keywords can be
    overridden per-run with --keywords."""
    plan = []
    for keyword in keywords:
        for location in locations:
            plan.append({
                "keywords": keyword,
                "location": location,
                "country": SEARCH["country"],
                "experience_years": SEARCH["experience_years"],
                "salary_min": SEARCH["salary_min"],
                "max_results": SEARCH["max_results"],
            })
    return plan


FREE_SITES = ("ats", "feeds", "free")   # pseudo-sites: no Apify actor, no cost


def resolve_sites(args):
    """Which Apify sites to run, honoring --site / --test / SITES toggles.
    The FREE_SITES pseudo-sites run no actors and are handled separately."""
    if args.site:
        if args.site in FREE_SITES:
            return []
        if args.site not in SITES:
            sys.exit(f"Unknown site '{args.site}'. "
                     f"Choices: {', '.join(SITES)}, {', '.join(FREE_SITES)}")
        return [args.site]
    if args.test:
        return ["indeed"]
    return [k for k, v in SITES.items() if v.get("enabled")]


def plan_for_site(site_key, args):
    """Build the (capped) search plan for one site."""
    locations = SITES[site_key].get("locations", SEARCH["locations"])
    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    else:
        keywords = SITES[site_key].get("keywords", SEARCH["role_keywords"])
    plan = build_search_plan(keywords, locations)

    if args.test:
        plan = plan[:1]
        for s in plan:
            s["max_results"] = SETTINGS["test_max_results"]

    # Per-site combo cap: --limit overrides config's max_searches_per_site.
    cap = args.limit if args.limit is not None else SETTINGS["max_searches_per_site"]
    if cap is not None:
        plan = plan[:cap]
    return plan


# ===========================================================================
# Running
# ===========================================================================
def effective_search(site_key, search):
    """Apply a site's results_per_run override (some actors, e.g. naukri, have a
    per-run minimum charge so it's wasteful to pull only a few results)."""
    per_run = SITES[site_key].get("results_per_run")
    return {**search, "max_results": per_run} if per_run is not None else search


def scrape_search(client, site_key, actor_id, search):
    """Run one actor and return (rows, cost_usd).

    apify-client 3.x returns a typed Run object (not a dict).
    """
    run_input = build_input(site_key, effective_search(site_key, search))
    # NOTE: results are bounded by the actor's OWN input cap (maxItemsPerSearch /
    # maxJobs / count). We do NOT pass call(max_items=...) because on actors with a
    # per-run minimum charge it errors ("less than allowed minimum of $0.50").
    # Launch non-blocking, then poll with a wall-clock deadline. We deliberately
    # AVOID .call()/.wait_for_finish(): both long-poll with timeout='no_timeout',
    # which hangs FOREVER when a TCP socket half-dies (the run finishes on Apify's
    # side but the client never receives the response — observed wedging the whole
    # sweep at 0% CPU on an idle ESTABLISHED connection). Plain .get() uses a
    # bounded 5s HTTP timeout + retries, so a stalled poll raises and the caller's
    # per-search try/except moves on. run_timeout also caps the actor server-side.
    # ponytail: fixed 6-min deadline / 5s poll; raise if a legit pull runs longer.
    run = client.actor(actor_id).start(
        run_input=run_input, run_timeout=timedelta(minutes=5))
    rc = client.run(run.id)
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        time.sleep(5)
        run = rc.get()
        if run is None or run.status not in ("READY", "RUNNING"):
            break
    else:
        rc.abort()          # deadline blown — stop the run server-side
        run = rc.get()
    if run is None or run.status != "SUCCEEDED":
        status = getattr(run, "status", "NO RUN")
        raise RuntimeError(f"run status {status}")
    cost = float(run.usage_total_usd or 0)
    if not run.default_dataset_id:
        return [], cost
    rows = [normalize(item, site_key)
            for item in client.dataset(run.default_dataset_id).iterate_items()]
    return rows, cost


def print_plan(plans):
    """plans: dict of {site_key: [search, ...]}."""
    total_runs = sum(len(p) for p in plans.values())
    print(f"Sites:     {', '.join(plans)}")
    print(f"Actor runs: {total_runs} total\n")
    for site_key, plan in plans.items():
        per_run = SITES[site_key].get("results_per_run", SEARCH["max_results"])
        print(f"  {site_key}: {len(plan)} searches (max {per_run} results each)")
        for s in plan:
            print(f"    · {s['keywords']:<32} @ {s['location']:<14}")
    print()


# ===========================================================================
# Output
# ===========================================================================
def to_output(row):
    return {
        "score": row.get("score", 0),
        "matched_skills": row.get("matched_skills", ""),
        "is_fullstack": row.get("is_fullstack", False),
        "title": row.get("Title", ""),
        "company": row.get("Company", ""),
        "location": row.get("Location", ""),
        "remote?": row.get("remote?", False),
        "remote_scope": row.get("remote_scope", ""),
        "hires_home": row.get("hires_home", ""),
        "tz_gap": row.get("tz_gap", ""),
        "remote_regions": row.get("remote_regions", ""),
        "visa": row.get("visa", ""),
        "eor": row.get("eor", ""),
        "timezones": row.get("timezones", ""),
        "experience_required": row.get("Experience", ""),
        "salary": row.get("Salary", ""),
        "hr_email": row.get("hr_email", ""),
        "hr_phone": row.get("hr_phone", ""),
        "source_site": row.get("Source", ""),
        "apply_url": row.get("Job URL", ""),
        "date_posted": row.get("Posted Date", ""),
    }


# Populated by finalize() each call so main() can report what got filtered.
LAST_STATS = {}


def finalize(raw_rows):
    """Score, filter, rank, and dedupe raw normalized rows into output rows."""
    scored = [r for r in (score_job(row) for row in raw_rows) if r is not None]
    if SETTINGS["min_score"] is not None:
        scored = [r for r in scored if r["score"] >= SETTINGS["min_score"]]

    # Freshness: drop jobs older than max_age_days.
    stale = 0
    if SETTINGS["max_age_days"] is not None:
        fresh = [r for r in scored if is_recent(r.get("Posted Date"), SETTINGS["max_age_days"])]
        stale = len(scored) - len(fresh)
        scored = fresh

    # Compensation: drop jobs whose disclosed MAX annual pay (in USD) is below
    # the floor. Unknown currency / undisclosed pay is kept — see comp_max_usd.
    low_salary = 0
    if SETTINGS["min_comp_usd"] is not None:
        paid = [r for r in scored if comp_ok(r.get("Salary"), SETTINGS["min_comp_usd"])]
        low_salary = len(scored) - len(paid)
        scored = paid

    # International-remote filters. All default to off: these read messy prose,
    # so an unset signal means "not stated" and must never be treated as a no.
    unreachable = rescued = 0
    if SETTINGS["remote_scopes"]:
        def reachable(row):
            if row.get("remote_scope") in SETTINGS["remote_scopes"]:
                return True
            # Two kinds of geo-locked role are still worth an application:
            #   - the employer demonstrably hires in your country (hires_home),
            #     so the entity or EOR that makes it possible already exists;
            #   - the lock is TO your country — a "remote within India" role is
            #     the most reachable kind there is, and dropping it as "not
            #     worldwide" is plainly wrong. This is not hypothetical: every
            #     LinkedIn f_WT=2 row comes back located in its own country, so
            #     without this the paid sweep discards what it paid to fetch.
            if not SETTINGS["keep_restricted_if_hires_home"]:
                return False
            return bool(row.get("remote_scope") == "restricted"
                        and (row.get("hires_home") == "yes"
                             or is_home_location(row.get("remote_regions"))))
        ok = [r for r in scored if reachable(r)]
        rescued = sum(1 for r in ok if r.get("remote_scope") not in SETTINGS["remote_scopes"])
        unreachable = len(scored) - len(ok)
        scored = ok
    no_visa = 0
    if SETTINGS["drop_no_visa"]:
        ok = [r for r in scored if r.get("visa") != "no"]   # only an EXPLICIT refusal
        no_visa = len(scored) - len(ok)
        scored = ok
    no_eor = 0
    if SETTINGS["require_eor"]:
        ok = [r for r in scored if r.get("eor")]
        no_eor = len(scored) - len(ok)
        scored = ok

    scored.sort(key=lambda r: r["score"], reverse=True)
    unique = dedupe(scored)  # sorted first, so highest-scored duplicate wins
    LAST_STATS.update(stale=stale, low_salary=low_salary, kept=len(unique),
                      unreachable=unreachable, rescued=rescued,
                      no_visa=no_visa, no_eor=no_eor)
    return [to_output(r) for r in unique]


# ---------------------------------------------------------------------------
# Seen ledger — postings already reported by an earlier run
# ---------------------------------------------------------------------------
# A sweep every ~2 weeks against a 21-day freshness window means roughly a week
# of postings overlap with the previous run, so a third of each report is jobs
# already reviewed and dismissed. This is deliberately a flat TSV and not a
# database: 26 runs a year is a few thousand rows, and there is no query here
# beyond "have I seen this key".
def _seen_key(row):
    key = job_key(row)
    return "|".join(key) if key else ""


def load_seen():
    """{key: first_seen_date} from previous runs. Missing file -> {}."""
    path = os.path.join(SETTINGS["output_dir"], "seen.tsv")
    if not os.path.exists(path):
        return {}
    seen = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1]:
                seen.setdefault(parts[1], parts[0])
    return seen


def record_seen(out_rows, seen, today):
    """Append keys this run reported that the ledger didn't already have."""
    os.makedirs(SETTINGS["output_dir"], exist_ok=True)
    path = os.path.join(SETTINGS["output_dir"], "seen.tsv")
    new = []
    for row in out_rows:
        key = _seen_key(row)
        if key and key not in seen:
            seen[key] = today
            new.append((today, key, row.get("title", ""), row.get("company", "")))
    with open(path, "a", encoding="utf-8") as fh:
        for entry in new:
            fh.write("\t".join(str(f).replace("\t", " ") for f in entry) + "\n")
    return len(new)


def write_outputs(out_rows, csv_path, json_path):
    os.makedirs(SETTINGS["output_dir"], exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(out_rows)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out_rows, f, indent=2, ensure_ascii=False)


def print_summary(pulled, after_dedupe, out_rows):
    print("\n" + "=" * 68)
    print(f"Total pulled:       {pulled}")
    filters = []
    if SETTINGS["max_age_days"] is not None:
        filters.append(f"{LAST_STATS.get('stale', 0)} stale (>{SETTINGS['max_age_days']}d)")
    if SETTINGS["min_comp_usd"] is not None:
        filters.append(f"{LAST_STATS.get('low_salary', 0)} below ${SETTINGS['min_comp_usd']:,.0f}/yr")
    if SETTINGS["remote_scopes"]:
        filters.append(f"{LAST_STATS.get('unreachable', 0)} not "
                       f"{'/'.join(SETTINGS['remote_scopes'])}")
        if LAST_STATS.get("rescued"):
            filters.append(f"{LAST_STATS['rescued']} geo-locked but employer "
                           f"hires at home (kept)")
    if SETTINGS["drop_no_visa"]:
        filters.append(f"{LAST_STATS.get('no_visa', 0)} refuse visa sponsorship")
    if SETTINGS["require_eor"]:
        filters.append(f"{LAST_STATS.get('no_eor', 0)} no EOR path")
    if "already_seen" in LAST_STATS:
        filters.append(f"{LAST_STATS['already_seen']} already reported (--only-new)")
    if filters:
        print(f"Filtered out:       {', '.join(filters)}")
    print(f"After scoring/filter+dedupe: {after_dedupe}")
    top_n = SETTINGS["top_n_console"]
    print(f"\nTop {min(top_n, len(out_rows))} by relevance:")
    print("-" * 68)
    for r in out_rows[:top_n]:
        fs = "FS" if r["is_fullstack"] else "  "
        rm = "R" if r["remote?"] else " "
        print(f"  [{r['score']:>3}] {fs} {rm}  {r['title'][:38]:<38} "
              f"{r['company'][:20]:<20} {r['source_site']}")
    print("=" * 68)


# ===========================================================================
# Main
# ===========================================================================
def parse_args():
    p = argparse.ArgumentParser(description="Full-stack job scraper (Apify -> ranked CSV/JSON)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the plan + per-site inputs; run no actors (zero cost).")
    p.add_argument("--test", action="store_true",
                   help="Tiny real run: first keyword x first location, indeed only.")
    p.add_argument("--site", help="Restrict to one site: indeed/naukri/linkedin, "
                                  "or ats/feeds/free for the free sources only.")
    p.add_argument("--limit", type=int, help="Cap (keyword x location) combos per site.")
    p.add_argument("--keywords", help="Comma-separated keywords to run instead of config's role_keywords.")
    p.add_argument("--yes", action="store_true",
                   help="Skip the confirmation prompt for large sweeps.")
    p.add_argument("--no-free", "--no-ats", dest="no_free", action="store_true",
                   help="Skip the free sources (ATS boards + feeds) even if configured.")
    p.add_argument("--demo", action="store_true",
                   help="Run the offline self-check (no network, no cost) and exit.")
    # Declared so --help documents it and it isn't rejected as unknown, but the
    # VALUE is read in config.py at import time — the scoring tables are
    # precompiled at module level, long before this runs. See config.py section 5.
    p.add_argument("--profile", metavar="NAME",
                   help="Use profiles/NAME.py to override config; "
                        "writes to output/NAME/.")
    p.add_argument("--only-new", action="store_true",
                   help="Report only postings no earlier run reported "
                        "(uses output/[profile/]seen.tsv).")
    return p.parse_args()


def _require_token():
    from dotenv import load_dotenv
    load_dotenv()
    token = os.getenv("APIFY_TOKEN")
    if not token:
        sys.exit("APIFY_TOKEN not found. Add it to a .env file in this folder.")
    return token


def demo():
    """Offline self-check for the logic that fails SILENTLY — currency parsing
    and job identity. `python scraper.py --demo`, no network, no cost."""
    usd = lambda t: (None if comp_max_usd(t) is None      # noqa: E731
                     else round(comp_max_usd(t)))
    # The regression that mattered: a US salary used to parse as 2.2 "LPA" and
    # get dropped by the pay floor.
    assert usd("$180,000 - $220,000 a year") == 220000
    assert comp_ok("$180,000 - $220,000 a year", 6000) is True
    assert usd("$150k - $190k") == 190000          # "k" suffix was missed entirely
    assert usd("$75 - $95 an hour") == 197600      # 95 x 2080
    assert usd("€90,000 per year") == 97200
    assert usd("12-18 LPA") == 20520               # 18 lakh INR
    assert usd("₹40,000 per month") == 5472        # 40k x 12 x 0.0114
    assert usd("Not disclosed") is None
    assert usd("") is None
    assert usd("50,000 - 80,000 per month") is None    # no currency -> fail OPEN
    assert comp_ok("50,000 - 80,000 per month", 6000) is True
    assert comp_ok("₹3-4 LPA", 6000) is False          # genuinely below the floor

    # One posting seen on three sources, each naming the location differently.
    same = [{"Title": "Senior Backend Engineer", "Company": "Acme", "Location": "Remote"},
            {"Title": "Engineer, Senior Backend", "Company": "Acme Inc.", "Location": "Worldwide"},
            {"Title": "Senior Backend Engineer", "Company": "Acme Technologies Pvt Ltd",
             "Location": "Europe, Remote"}]
    assert len(dedupe(same)) == 1, dedupe(same)
    # Genuinely different jobs at the same company must survive.
    assert len(dedupe(same + [{"Title": "Frontend Engineer", "Company": "Acme",
                               "Location": "Remote"}])) == 2
    # Output-schema keys work too, so merge_jobs.py can share job_key.
    assert job_key({"title": "Backend Engineer", "company": "Acme"}) == \
        job_key({"Title": "Engineer Backend", "Company": "acme ltd"})
    # No company -> fall back to the URL, tracking params stripped.
    assert job_key({"Job URL": "https://WWW.x.com/jobs/1/?utm_source=a"}) == \
        job_key({"apply_url": "https://x.com/jobs/1"})
    # Nothing to key on -> no identity, so rows are never collapsed into each other.
    assert job_key({}) is None
    assert len(dedupe([{}, {}])) == 2

    # Two-tier seniority: an inflated title label must not delete a role whose
    # stated requirement is within reach, but a genuinely senior one still goes.
    sj = lambda t, d="": score_job({"Title": t, "Description": d})   # noqa: E731
    assert sj("Engineering Manager") is None                     # hard drop
    assert sj("Staff Software Engineer") is None
    assert sj("Principal Architect") is None
    assert sj("Senior React Developer", "2 years of React experience.") is not None
    plain = sj("React Developer", "2 years of React experience.")
    senior = sj("Senior React Developer", "2 years of React experience.")
    assert senior["score"] == plain["score"] + SCORING["soft_penalty"]  # kept, lower
    assert sj("Senior React Developer", "8+ years of React required.") is None

    # Timezone gap down-ranks but never removes, and only past the free window.
    near = sj("React Developer", "Remote across Europe. 2 years experience.")
    far = sj("React Developer", "Remote in the US. 2 years experience.")
    assert near is not None and far is not None
    assert near["tz_gap"] == 4.5 and far["tz_gap"] == 11.5
    assert near["score"] > far["score"], (near["score"], far["score"])
    # 4.5h is inside TZ_FREE_HOURS, so the near role pays nothing at all.
    assert near["score"] == sj("React Developer", "2 years experience.")["score"]

    # A geo-locked role is rescued only when the EMPLOYER hires at home.
    orig = SETTINGS["remote_scopes"], SETTINGS["keep_restricted_if_hires_home"]
    SETTINGS["remote_scopes"] = ["worldwide"]
    SETTINGS["keep_restricted_if_hires_home"] = True
    try:
        base = {"Title": "React Developer", "Description": "2 years experience.",
                "Location": "New York, NY (HQ), Remote"}
        assert len(finalize([dict(base, Company="A", hires_home="yes")])) == 1
        assert len(finalize([dict(base, Company="B", hires_home="no")])) == 0
        assert len(finalize([dict(base, Company="C", hires_home="")])) == 0  # feeds
        # ...or when the lock is TO home: "remote within India" is the most
        # reachable role there is, whatever the employer's other postings say.
        home = {"Title": "React Developer", "Description": "2 years experience.",
                "Location": "Remote, India", "Company": "D", "hires_home": ""}
        got = finalize([dict(home)])
        assert len(got) == 1 and got[0]["remote_scope"] == "restricted", got
        assert got[0]["remote_regions"] == "India", got
        # A lock to somewhere else is still dropped.
        away = dict(home, Location="Remote, Germany", Company="E")
        assert len(finalize([dict(away)])) == 0, finalize([dict(away)])
        SETTINGS["keep_restricted_if_hires_home"] = False
        assert len(finalize([dict(base, Company="A", hires_home="yes")])) == 0
    finally:
        SETTINGS["remote_scopes"], SETTINGS["keep_restricted_if_hires_home"] = orig

    # LinkedIn URLs: never guess a geography, because a wrong one bills full
    # price for US results. Raising happens before any actor starts, so it's free.
    orig_li = dict(SITES.get("linkedin", {}))
    try:
        SITES.setdefault("linkedin", {}).update(remote_geo=None, remote_only=False)
        url = _build_linkedin_url({"keywords": "react", "location": "Germany"})
        assert "geoId=101282230" in url and "f_WT" not in url, url
        # remote_only turns every country search into a remote-in-that-country one.
        SITES["linkedin"]["remote_only"] = True
        assert "f_WT=2" in _build_linkedin_url({"keywords": "react", "location": "Germany"})
        # A bare "Remote" must say WHICH region — it used to silently mean India.
        SITES["linkedin"]["remote_only"] = False
        try:
            _build_linkedin_url({"keywords": "react", "location": "Remote"})
            raise AssertionError("bare 'Remote' with no remote_geo must raise")
        except ValueError as exc:
            assert "remote_geo" in str(exc)
        SITES["linkedin"]["remote_geo"] = "Germany"
        url = _build_linkedin_url({"keywords": "react", "location": "Remote"})
        assert "geoId=101282230" in url and "f_WT=2" in url, url
        # An unmapped place raises instead of falling back to free text.
        for bad in ("Atlantis", "Bhutan"):
            try:
                _build_linkedin_url({"keywords": "react", "location": bad})
                raise AssertionError(f"unmapped '{bad}' must raise, not guess")
            except ValueError as exc:
                assert "no geoId" in str(exc)
    finally:
        SITES["linkedin"] = orig_li

    # Seen ledger: the same posting from two sources must collapse to ONE key,
    # or --only-new would keep re-reporting it.
    assert _seen_key({"title": "Backend Engineer", "company": "Acme Ltd"}) == \
        _seen_key({"Title": "Engineer, Backend", "Company": "Acme"})
    assert _seen_key({}) == ""                    # no identity -> never suppressed

    # location_allowed reads config.LOCATION_HINTS, so exercise both branches by
    # swapping the module global rather than by shipping a second parameter.
    global LOCATION_HINTS
    original, LOCATION_HINTS = LOCATION_HINTS, []
    try:
        assert location_allowed("Berlin, Germany") is True      # no hints = allow all
        LOCATION_HINTS = ["india", "remote"]
        assert location_allowed("Berlin, Germany") is False
        assert location_allowed("Pune, India") is True
        assert location_allowed("") is True                     # unspecified -> keep
    finally:
        LOCATION_HINTS = original
    print("demo ok")


def main():
    args = parse_args()
    if args.demo:
        demo()
        return
    if config.PROFILE:
        print(f"Profile:   {config.PROFILE} "
              f"(overrides {', '.join(config.PROFILE_CHANGED) or 'nothing'}) "
              f"-> {SETTINGS['output_dir']}/\n")
    enabled = resolve_sites(args)

    plans = {site_key: plan_for_site(site_key, args) for site_key in enabled}
    plans = {k: v for k, v in plans.items() if v}  # drop sites with empty plans

    # Free sources: on for full runs and for --site ats/feeds/free, unless
    # --no-free or a specific Apify --site was requested.
    n_boards = sum(len(b) for b in ATS_BOARDS.values())
    n_feeds = sum(1 for c in FEEDS.values() if c.get("enabled"))
    run_free = ((args.site in FREE_SITES or args.site is None)
                and not args.test and not args.no_free
                and bool(n_boards or n_feeds))

    if not plans and not run_free:
        sys.exit("Nothing to run — no sites enabled and no free sources configured.")

    if plans:
        print_plan(plans)
    if run_free:
        print(f"Free sources: {n_boards} ATS boards "
              f"({', '.join(k for k, v in ATS_BOARDS.items() if v)}) "
              f"+ {n_feeds} feeds ({', '.join(k for k, v in FEEDS.items() if v.get('enabled'))})\n")

    if args.dry_run:
        print("Sample actor inputs (first combo per site):")
        for site_key, plan in plans.items():
            print(f"\n  {site_key}:")
            sample = build_input(site_key, effective_search(site_key, plan[0]))
            print("    " + json.dumps(sample, indent=2).replace("\n", "\n    "))
        if run_free:
            for platform, boards in ATS_BOARDS.items():
                if boards:
                    print(f"\n  {platform}: {', '.join(boards.values())}")
        print("\n(dry run — no actors executed)")
        return

    # Preflight every planned search through its input adapter. build_input()
    # raises on anything that would cost money and return the wrong data (an
    # unmapped LinkedIn geoId being the expensive one), so surface it ONCE here
    # rather than as N identical failures after N paid runs.
    problems = {}
    for site_key, plan in plans.items():
        for search in plan:
            try:
                build_input(site_key, effective_search(site_key, search))
            except ValueError as exc:
                problems[str(exc)] = None      # dict = dedup, keeps order
    if problems:
        sys.exit("\n".join(["Refusing to run — these would spend money on bad data:", ""]
                           + [f"  · {p}" for p in problems]))

    total_runs = sum(len(p) for p in plans.values())
    if (not args.yes and not args.test
            and total_runs > SETTINGS["confirm_above_runs"]
            and sys.stdin.isatty()):
        reply = input(f"This will run {total_runs} paid actor runs. Continue? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            sys.exit("Aborted. Try --dry-run or --test first.")

    # Fix output paths up front so we can checkpoint into them as we go.
    os.makedirs(SETTINGS["output_dir"], exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    csv_path = os.path.join(SETTINGS["output_dir"], f"jobs_{stamp}.csv")
    json_path = os.path.join(SETTINGS["output_dir"], f"jobs_{stamp}.json")

    # Loaded ONCE, before anything is written: --only-new must filter against
    # what EARLIER runs reported, and the ledger is appended to only at the end.
    seen = load_seen()
    if args.only_new and seen:
        print(f"--only-new: {len(seen)} postings already reported by earlier runs\n")

    def emit(rows):
        """finalize + optional new-only filter + write. Used for checkpoints too,
        so an interrupted sweep leaves a correct file behind."""
        out = finalize(rows)
        if args.only_new:
            before = len(out)
            out = [r for r in out if _seen_key(r) not in seen]
            # Reported in the summary: without it, "0 jobs" reads as a broken
            # sweep rather than "everything here was already reviewed".
            LAST_STATS["already_seen"] = before - len(out)
        write_outputs(out, csv_path, json_path)
        return out

    raw_rows = []
    spent = 0.0
    failures = []   # (site, label, reason) per failed search — reported at the end
    budget = SETTINGS["max_spend_usd"]

    # Resume ledger: one "YYYY-MM-DD|site|keyword|location" per completed combo.
    # Lets a rerun (e.g. after an account hits its usage cap) skip what's already
    # scraped and only pay for what's left.
    # The DATE is load-bearing: without it the ledger never expires, so the next
    # day's sweep skipped every combo, scraped nothing, and still printed a
    # normal-looking summary. Scoped to today, it resumes an interrupted run and
    # gets out of the way tomorrow. Delete output/.done_combos to force a re-scrape.
    today = datetime.now().strftime("%Y-%m-%d")
    done_path = os.path.join(SETTINGS["output_dir"], ".done_combos")
    done = set()
    if os.path.exists(done_path):
        with open(done_path) as fh:
            done = {ln.strip() for ln in fh if ln.startswith(today)}

    # --- Paid Apify sites (checkpoint after every search so a stop never loses data) ---
    if plans:
        from apify_client import ApifyClient
        client = ApifyClient(_require_token())
        stopped_early = False
        for site_key, plan in plans.items():
            if stopped_early:
                break
            actor_id = SITES[site_key]["actor"]
            print(f"\n{site_key} ({actor_id})")
            for i, search in enumerate(plan, 1):
                label = f"{search['keywords']} @ {search['location']}"
                combo_key = f"{today}|{site_key}|{search['keywords']}|{search['location']}"
                if combo_key in done:
                    print(f"  [{i}/{len(plan)}] {label:<46} — skip (done)")
                    continue
                if budget is not None and spent >= budget:
                    print(f"  ⚠ spend cap ${budget:.2f} reached (${spent:.2f}) — stopping.")
                    stopped_early = True
                    break
                try:
                    rows, cost = scrape_search(client, site_key, actor_id, search)
                    spent += cost
                    raw_rows.extend(rows)
                    emit(raw_rows)                                # checkpoint
                    with open(done_path, "a") as fh:                        # mark done
                        fh.write(combo_key + "\n")
                    done.add(combo_key)
                    print(f"  [{i}/{len(plan)}] {label:<46} {len(rows):>3} jobs  "
                          f"(${cost:.3f}, ${spent:.2f} total)")
                except Exception as exc:  # isolate failures per search
                    failures.append((site_key, label, str(exc)))
                    print(f"  [{i}/{len(plan)}] {label:<46} ! {exc}")
        print(f"\nTotal Apify spend this run: ${spent:.2f}")
        # Per-search try/except means a sweep can fail almost entirely and still
        # exit 0 with a normal-looking summary — the usual cause is an Apify
        # account hitting "Monthly usage hard limit exceeded" partway through, which
        # then fails EVERY remaining search. Say so loudly, or the run reads as
        # complete when it isn't. (Rerunning resumes from output/.done_combos, so
        # nothing already scraped is paid for twice.)
        if failures:
            planned = sum(len(p) for p in plans.values())
            print(f"\n⚠ {len(failures)} of {planned} searches FAILED — this sweep is INCOMPLETE.")
            reasons = Counter(reason for _, _, reason in failures)
            for reason, count in reasons.most_common(3):
                print(f"    {count:>3}x {reason[:96]}")
            if any("usage hard limit" in r.lower() or "monthly usage" in r.lower()
                   for r in reasons):
                print("    → Apify credit exhausted. Add APIFY_TOKEN_2 to .env and rerun with:")
                print('      APIFY_TOKEN="$(grep -E \'^APIFY_TOKEN_2=\' .env | cut -d= -f2-)" '
                      f"python scraper.py --site {site_key} --yes")
            print("    Rerun to retry only the failed combos (output/.done_combos "
                  "skips what succeeded).")

    # --- Free sources (company ATS boards + public remote feeds) ---
    if run_free:
        print("\nfree sources (company boards + remote feeds)")
        raw_rows.extend(fetch_free())
        emit(raw_rows)                                          # checkpoint

    pulled = len(raw_rows)
    if pulled == 0:
        sys.exit("\nNo jobs scraped — nothing to write.")

    out_rows = emit(raw_rows)

    print_summary(pulled, len(out_rows), out_rows)
    # Recorded only once the run has actually produced its report, so a crash
    # mid-sweep can't mark jobs as already-reviewed that you never saw.
    added = record_seen(out_rows, seen, today)
    print(f"\nWrote {len(out_rows)} ranked jobs to:")
    print(f"  {csv_path}")
    print(f"  {json_path}")
    print(f"  seen.tsv: +{added} new ({len(seen)} known)"
          + ("" if args.only_new else "  — next run: --only-new to skip these"))


if __name__ == "__main__":
    main()
