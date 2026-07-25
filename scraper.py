"""
Apify full-stack job scraper -> ranked CSV + JSON.

Pipeline:
    1. Build a search plan = role_keywords x locations (from config.SEARCH).
    2. Run one Apify actor per (enabled site, search combo); normalize every result
       into a common schema (per-site input differences handled by build_input).
    3. Score each job against the resume (config.SCORING): weighted skills, a
       full-stack bonus for frontend+backend overlap, and hard down-ranking /
       filtering of wrong-seniority, off-stack, and Salesforce/CRM roles.
    4. Cross-site de-duplicate on normalized title + company + location.
    5. Sort by score (highest first) and write a timestamped CSV + JSON.

Usage:
    pip install -r requirements.txt
    python scraper.py --dry-run                 # print the plan, spend nothing
    python scraper.py --test                    # tiny: 1 keyword x 1 location, indeed only
    python scraper.py --site indeed --limit 3   # one site, first 3 combos
    python scraper.py                           # full sweep (confirms if large)

Flags:
    --dry-run        Show the planned searches + per-site actor inputs; no actor runs.
    --test           Smallest possible real run (first keyword x first location, indeed).
    --site NAME      Restrict to a single site (overrides SITES toggles for this run).
    --limit N        Cap (keyword x location) combos per site to N.
    --yes            Skip the "large sweep" confirmation prompt.
"""

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta

from dotenv import load_dotenv
from apify_client import ApifyClient

from config import (SEARCH, SITES, SCORING, SETTINGS, NAUKRI_CITY_IDS,
                    LINKEDIN_GEO_IDS, GREENHOUSE_COMPANIES, LEVER_COMPANIES,
                    INDIA_LOCATION_HINTS, ATS_TITLE_HINTS)

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
    "remote?", "experience_required", "salary", "hr_email", "hr_phone",
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


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s):
    # Unescape entities first (Greenhouse returns HTML-entity-encoded content),
    # then drop tags and collapse whitespace.
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html.unescape(s or ""))).strip()


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
# Company career sites via ATS APIs (Greenhouse / Lever) — free, stdlib only
# ===========================================================================
def _http_get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 job-scraper"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def is_india_location(loc):
    """Careers pages list global roles; keep only India-relevant (or unspecified)
    ones so we don't flood the results with overseas jobs."""
    if not loc:
        return True  # unspecified -> keep, scoring will sort it out
    low = loc.lower()
    return any(h in low for h in INDIA_LOCATION_HINTS)


def is_dev_title(title):
    """ATS returns all roles; keep only software/dev-looking titles."""
    low = (title or "").lower()
    return any(h in low for h in ATS_TITLE_HINTS)


def normalize_greenhouse(job, token, company_name):
    loc = (job.get("location") or {}).get("name", "")
    return _truncate_desc({
        "Source": f"greenhouse:{token}",
        "Title": job.get("title", ""),
        "Company": company_name,
        "Location": loc,
        "Salary": "",
        "Experience": "",
        "Posted Date": (job.get("updated_at") or job.get("first_published") or "")[:10],
        "Job URL": job.get("absolute_url", ""),
        "Description": _strip_html(job.get("content", "")),  # HTML-entity encoded
    })


def fetch_greenhouse(token, company_name):
    """Greenhouse public board API (free). content=true includes descriptions."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    data = _http_get_json(url)
    return [normalize_greenhouse(j, token, company_name)
            for j in data.get("jobs", [])
            if is_dev_title(j.get("title", ""))
            and is_india_location((j.get("location") or {}).get("name", ""))]


def normalize_lever(job, token, company_name):
    cats = job.get("categories") or {}
    posted = ""
    if job.get("createdAt"):
        try:
            posted = datetime.fromtimestamp(job["createdAt"] / 1000).strftime("%Y-%m-%d")
        except (ValueError, OSError, TypeError):
            posted = ""
    desc = job.get("descriptionPlain") or _strip_html(job.get("description", ""))
    return _truncate_desc({
        "Source": f"lever:{token}",
        "Title": job.get("text", ""),
        "Company": company_name,
        "Location": cats.get("location", ""),
        "Salary": "",
        "Experience": cats.get("commitment", ""),
        "Posted Date": posted,
        "Job URL": job.get("hostedUrl", "") or job.get("applyUrl", ""),
        "Description": desc,
    })


def fetch_lever(token, company_name):
    """Lever public postings API (free)."""
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    data = _http_get_json(url)  # returns a list
    return [normalize_lever(j, token, company_name)
            for j in data
            if is_dev_title(j.get("text", ""))
            and is_india_location((j.get("categories") or {}).get("location", ""))]


def fetch_ats():
    """Fetch all configured Greenhouse + Lever companies. Free; failures per
    company are isolated (a wrong token / removed board just logs and continues)."""
    rows = []
    sources = ([("greenhouse", t, n) for t, n in GREENHOUSE_COMPANIES.items()]
               + [("lever", t, n) for t, n in LEVER_COMPANIES.items()])
    for ats, token, name in sources:
        try:
            fetched = fetch_greenhouse(token, name) if ats == "greenhouse" else fetch_lever(token, name)
            rows.extend(fetched)
            print(f"  {ats:<10} {name:<22} {len(fetched):>3} India jobs")
        except Exception as exc:
            print(f"  {ats:<10} {name:<22} ! {exc}")
    return rows


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
DROP_PATTERNS = {t: _compile(t) for t in SCORING["drop_terms"]}

REMOTE_PATTERN = re.compile(r"(?<![a-z0-9])(remote|work from home|wfh|anywhere)(?![a-z0-9])")
# Any "<n> years/yrs" mention (optionally "n+" or "n-m"); we read the leading n.
YEARS_PATTERN = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)")


def _required_experience_floor(text):
    """Smallest 'N years' figure mentioned — a proxy for the minimum experience
    demanded. 'company founded 5 years ago' plus a real '2 years' requirement
    resolves to 2 (kept); a lone '5+ years' resolves to 5 (over threshold)."""
    nums = [int(m.group(1)) for m in YEARS_PATTERN.finditer(text)]
    return min(nums) if nums else None


def is_remote(row):
    hay = " ".join([row.get("Location", ""), row.get("Title", ""),
                    row.get("Description", "")]).lower()
    return bool(REMOTE_PATTERN.search(hay))


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


# --- Salary filter ---------------------------------------------------------
def salary_max_lpa(text):
    """Best-effort MAX salary in LPA (lakhs per annum). Returns None when there's
    no disclosed/parseable figure (so we never filter on a guess)."""
    t = (text or "").lower()
    if not t or any(x in t for x in ("not disclosed", "not specified", "unpaid",
                                     "competitive", "as per", "negotiable")):
        return None
    tc = t.replace(",", "")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", tc)]
    nums = [n for n in nums if n > 0]
    if not nums:
        return None
    mx = max(nums)
    if "crore" in tc or re.search(r"\bcr\b", tc):
        return mx * 100.0                       # 1 crore = 100 LPA
    if any(u in tc for u in ("lakh", "lac", "lpa")) or re.search(r"\bl\b", tc):
        return mx                               # already in lakhs
    if any(u in tc for u in ("month", "/mo", "monthly", "p.m", "per month", "a month")):
        return mx * 12.0 / 100000.0             # monthly rupees -> LPA
    if mx >= 100000:                            # big number -> annual rupees
        return mx / 100000.0
    if mx >= 10000:                             # 10k–100k -> most likely monthly rupees
        return mx * 12.0 / 100000.0
    if mx <= 100:                               # small bare number -> assume lakhs (Indian norm)
        return mx
    return None


def salary_ok(salary_text, min_ctc_lpa):
    mx = salary_max_lpa(salary_text)
    if mx is None:
        return True                             # undisclosed -> keep
    return mx >= min_ctc_lpa


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

    # --- Hard-filter checks (seniority in title, or over-experience anywhere) ---
    excluded = False
    for term, pat in DROP_PATTERNS.items():
        if pat.search(title):
            excluded = True
            break
    floor = _required_experience_floor(text)
    if floor is not None and floor > SETTINGS["max_experience_years"]:
        excluded = True

    if excluded and SETTINGS["drop_excluded"]:
        return None

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

    # --- Keep-but-penalize path for excluded roles ---
    if excluded:  # only reached when drop_excluded is False
        score += SCORING["drop_penalty"]

    row["score"] = score
    row["matched_skills"] = ", ".join(dict.fromkeys(matched))  # dedup, preserve order
    row["is_fullstack"] = is_fullstack
    row["remote?"] = is_remote(row)
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


def dedupe(rows):
    """Drop duplicates keyed on normalized title + company + location.
    Assumes rows are already sorted best-first, so the first seen (highest score)
    wins."""
    seen = set()
    unique = []
    for row in rows:
        key = (_norm_key(row.get("Title")), _norm_key(row.get("Company")),
               _norm_key(row.get("Location")))
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
    # LinkedIn's job search honors a numeric geoId, NOT the free-text location
    # (a bare "location=Delhi" is ignored and defaults to US results). Map the
    # location name to a geoId via config.LINKEDIN_GEO_IDS.
    loc = (s.get("location") or "").strip()
    params = {"keywords": s["keywords"]}
    if loc.lower() == "remote":
        # LinkedIn filters remote via f_WT=2 (workplace type), not location text.
        params["geoId"] = LINKEDIN_GEO_IDS.get("India", "")
        params["f_WT"] = "2"
    else:
        geo_id = LINKEDIN_GEO_IDS.get(loc)
        if geo_id:
            params["geoId"] = geo_id
        elif loc:
            params["location"] = loc  # fallback (unreliable) if no geoId mapped
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


def resolve_sites(args):
    """Which Apify sites to run, honoring --site / --test / SITES toggles.
    'ats' is a pseudo-site (free company career sites), handled separately."""
    if args.site:
        if args.site == "ats":
            return []  # ATS-only run: no Apify sites
        if args.site not in SITES:
            sys.exit(f"Unknown site '{args.site}'. Choices: {', '.join(SITES)}, ats")
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

    # Salary: drop jobs whose disclosed MAX pay is below min_ctc_lpa.
    low_salary = 0
    if SETTINGS["min_ctc_lpa"] is not None:
        paid = [r for r in scored if salary_ok(r.get("Salary"), SETTINGS["min_ctc_lpa"])]
        low_salary = len(scored) - len(paid)
        scored = paid

    scored.sort(key=lambda r: r["score"], reverse=True)
    unique = dedupe(scored)  # sorted first, so highest-scored duplicate wins
    LAST_STATS.update(stale=stale, low_salary=low_salary, kept=len(unique))
    return [to_output(r) for r in unique]


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
    if SETTINGS["min_ctc_lpa"] is not None:
        filters.append(f"{LAST_STATS.get('low_salary', 0)} below {SETTINGS['min_ctc_lpa']} LPA")
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
    p.add_argument("--site", help="Restrict to a single site: indeed/naukri/linkedin/ats.")
    p.add_argument("--limit", type=int, help="Cap (keyword x location) combos per site.")
    p.add_argument("--keywords", help="Comma-separated keywords to run instead of config's role_keywords.")
    p.add_argument("--yes", action="store_true",
                   help="Skip the confirmation prompt for large sweeps.")
    p.add_argument("--no-ats", action="store_true",
                   help="Skip company career sites (Greenhouse/Lever) even if configured.")
    return p.parse_args()


def _require_token():
    load_dotenv()
    token = os.getenv("APIFY_TOKEN")
    if not token:
        sys.exit("APIFY_TOKEN not found. Add it to a .env file in this folder.")
    return token


def main():
    args = parse_args()
    enabled = resolve_sites(args)

    plans = {site_key: plan_for_site(site_key, args) for site_key in enabled}
    plans = {k: v for k, v in plans.items() if v}  # drop sites with empty plans

    # ATS (free company career sites): on for full runs / --site ats, unless
    # --no-ats or a specific Apify --site was requested.
    run_ats = ((args.site == "ats" or args.site is None)
               and not args.test and not args.no_ats
               and bool(GREENHOUSE_COMPANIES or LEVER_COMPANIES))

    if not plans and not run_ats:
        sys.exit("Nothing to run — no sites enabled and no ATS companies configured.")

    if plans:
        print_plan(plans)
    if run_ats:
        n = len(GREENHOUSE_COMPANIES) + len(LEVER_COMPANIES)
        print(f"ATS (free): {n} company career sites (Greenhouse/Lever)\n")

    if args.dry_run:
        print("Sample actor inputs (first combo per site):")
        for site_key, plan in plans.items():
            print(f"\n  {site_key}:")
            sample = build_input(site_key, effective_search(site_key, plan[0]))
            print("    " + json.dumps(sample, indent=2).replace("\n", "\n    "))
        if run_ats:
            print("\n  ats companies:", ", ".join(list(GREENHOUSE_COMPANIES.values())
                                                   + list(LEVER_COMPANIES.values())))
        print("\n(dry run — no actors executed)")
        return

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

    raw_rows = []
    spent = 0.0
    failures = []   # (site, label, reason) per failed search — reported at the end
    budget = SETTINGS["max_spend_usd"]

    # Resume ledger: one "site|keyword|location" per completed combo. Lets a rerun
    # (e.g. after an account hits its usage cap) skip what's already scraped and
    # only pay for what's left. Delete output/.done_combos to force a full re-scrape.
    done_path = os.path.join(SETTINGS["output_dir"], ".done_combos")
    done = set()
    if os.path.exists(done_path):
        with open(done_path) as fh:
            done = {ln.strip() for ln in fh if ln.strip()}

    # --- Paid Apify sites (checkpoint after every search so a stop never loses data) ---
    if plans:
        client = ApifyClient(_require_token())
        stopped_early = False
        for site_key, plan in plans.items():
            if stopped_early:
                break
            actor_id = SITES[site_key]["actor"]
            print(f"\n{site_key} ({actor_id})")
            for i, search in enumerate(plan, 1):
                label = f"{search['keywords']} @ {search['location']}"
                combo_key = f"{site_key}|{search['keywords']}|{search['location']}"
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
                    write_outputs(finalize(raw_rows), csv_path, json_path)  # checkpoint
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

    # --- Free company career sites (Greenhouse / Lever) ---
    if run_ats:
        print("\nats (company career sites — free)")
        raw_rows.extend(fetch_ats())
        write_outputs(finalize(raw_rows), csv_path, json_path)  # checkpoint

    pulled = len(raw_rows)
    if pulled == 0:
        sys.exit("\nNo jobs scraped — nothing to write.")

    out_rows = finalize(raw_rows)
    write_outputs(out_rows, csv_path, json_path)

    print_summary(pulled, len(out_rows), out_rows)
    print(f"\nWrote {len(out_rows)} ranked jobs to:")
    print(f"  {csv_path}")
    print(f"  {json_path}")


if __name__ == "__main__":
    main()
