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
import contextlib
import csv
import functools
import hashlib
import inspect
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.parse
from collections import Counter, namedtuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

import config
import enrich
import experience_guard
import paid_adaptive
import skill_concepts
import sources
import telemetry
from sources import shadow
from sources._http import strip_html as _strip_html
from config import (SEARCH, SITES, SCORING, SETTINGS, NAUKRI_CITY_IDS,
                    LINKEDIN_GEO_IDS, LINKEDIN_COMPANY_IDS, INDEED_COUNTRIES,
                    ATS_BOARDS, FEEDS, OPTUM, ENTERPRISE,
                    LOCATION_HINTS, HOME_LOCATION_HINTS, ATS_TITLE_HINTS,
                    ATS_TITLE_EXCLUDE)

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
    "source_site", "apply_url", "date_posted", "req_number", "grade", "verified_live",
    # Appended, never inserted: every existing column keeps its position, so a
    # consumer reading by index is unaffected.
    "search_query", "search_rank",
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
    # The same shape every other path already produces: sources/ strips each
    # free adapter's description (_http.strip_html, called in ats.py, feeds.py,
    # enterprise.py, optum.py) and normalize_naukri strips its own. This was
    # the one acquisition path that did not, while FIELD_KEYS["Description"]
    # accepts descriptionHtml — so markup could reach a parser that has never
    # seen any from the free half.
    #
    # It matters most to _required_experience_floor, which reads a fixed
    # 30/60-character window either side of a years figure: across tags, a
    # cue two words away ("Experience required:</strong></h3><h3>4 to 9
    # years") falls outside the window and the requirement reads as "not
    # stated". Measured over 162 live JDs parsed both ways, 2 flip from "not
    # stated" to the right figure once the markup is gone.
    #
    # Stripped BEFORE the truncation below, so description_max bounds real
    # text rather than counting tags against it.
    row["Description"] = _strip_html(row["Description"])
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

    Matched on alphanumeric boundaries, not as a substring: "india" also matches
    "Indianapolis, Indiana", which on one employer's board was 107 of the 427
    cards an India-only sweep kept — a quarter of it US nursing jobs. Same
    lookaround idiom as _compile() below.
    """
    if not LOCATION_HINTS or not loc:
        return True
    low = loc.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(h)}(?![a-z0-9])", low)
               for h in LOCATION_HINTS)


def is_dev_title(title):
    """Free sources return a whole board; keep only software/dev-looking titles.

    ATS_TITLE_EXCLUDE wins over ATS_TITLE_HINTS, because the titles worth
    excluding contain a hint by construction — "Senior Software Engineer I (Data
    Engineer - Spark, Scala, ETL)" is a data-engineering job wearing a software
    title, and no include vocabulary can tell them apart.

    Substring, not word-boundary (unlike location_allowed): real titles run the
    words together — "ReactJS", "AI/ML", "Devops", "Java FSD".
    """
    low = (title or "").lower()
    if any(x in low for x in ATS_TITLE_EXCLUDE):
        return False
    return any(h in low for h in ATS_TITLE_HINTS)


def is_home_location(loc):
    """True if a location is in the country you're applying FROM. Used to ask a
    company board "does this employer hire here at all", not to filter jobs."""
    low = (loc or "").lower()
    return any(h in low for h in HOME_LOCATION_HINTS)


def in_home_country(loc):
    """Same question as is_home_location, on alphanumeric boundaries.

    is_home_location is a plain substring test and stays one: it decides
    `hires_home`, a board-level signal where a stray match costs nothing.
    This one FILTERS, so it uses location_allowed's matcher — "india" must
    not fire inside "Indianapolis, Indiana", which on one employer's board
    was 107 of 427 kept cards.

    Unstated location is True, the same answer location_allowed gives and for
    the same reason: a blank field is "the posting didn't say", never "no".
    Measured on 7,132 real rows in output/, that case is 0% of them.
    """
    low = (loc or "").strip().lower()
    if not low:
        return True
    return any(re.search(rf"(?<![a-z0-9]){re.escape(h)}(?![a-z0-9])", low)
               for h in HOME_LOCATION_HINTS)


def onsite_or_hybrid(row):
    """True for a row this sweep should treat as an office-based role.

    The COMPLEMENT of enrich.REMOTE_SCOPES, not the pair ("onsite", "hybrid"):
    ~40% of real rows state no arrangement at all, and a blank signal is "the
    posting didn't say", never "no". So this keeps onsite, hybrid and
    unstated, and drops only what the posting positively calls remote —
    worldwide, remote, or geo-restricted remote.
    """
    return row.get("remote_scope") not in enrich.REMOTE_SCOPES


def _optum_scope():
    """Banner wording: a single empty keyword IS the whole index, not "1 queries"."""
    kws = OPTUM.get("keywords") or [""]
    return "whole index" if kws == [""] else f"{len(kws)} queries"


def fetch_free():
    """Every configured ATS board + feed. Free; per-board failures are isolated."""
    rows = sources.fetch_free(ATS_BOARDS, FEEDS, is_dev_title, location_allowed,
                              is_home_location, optum_cfg=OPTUM,
                              enterprise_cfg=ENTERPRISE)
    # The shadow tranche is NOT fetched here any more (V2-B3). It runs from
    # main() once the results are written — see the call there.
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
# The same weights grouped into concepts, so one technology spelled three
# ways scores once instead of three times. Built alongside rather than
# instead of SKILL_PATTERNS: both are cheap, and score_job picks per call,
# so a comparison run can flip SWEEP_SKILL_CONCEPTS without reimporting.
SKILL_CONCEPTS = skill_concepts.from_weights(SCORING["skill_weights"])

# The engine that DERIVED the loaded profile decides how it is scored.
#
# Without this, Render could derive a profile as v1 and the Oracle
# worker's scraper child — reading its own environment, on another
# machine, possibly from an older checkout — could score it as v2, and
# nothing anywhere would say so. The stamp travels with the profile, so
# the two cannot disagree by accident. No profile loaded means nothing to
# bind, and the environment answers as before.
if getattr(config, "PROFILE_ENGINE", None):
    skill_concepts.bind(config.PROFILE_ENGINE)
PENALTY_PATTERNS = {t: (p, _compile(t)) for t, p in SCORING["penalty_terms"].items()}
FRONTEND_PATTERNS = [_compile(t) for t in SCORING["frontend_terms"]]
BACKEND_PATTERNS  = [_compile(t) for t in SCORING["backend_terms"]]
FULLSTACK_TITLE_PATTERNS = [_compile(t) for t in SCORING["fullstack_title_terms"]]
HARD_DROP_PATTERNS = {t: _compile(t) for t in SCORING["hard_drop_terms"]}
SOFT_DROP_PATTERNS = {t: _compile(t) for t in SCORING["soft_drop_terms"]}


def norm_company(name):
    """Company name flattened for comparison: lowercased, punctuation dropped
    ("SWAKIO™" -> "swakio"), whitespace collapsed. Whole-name matching only —
    substring matching here would drop a real "Freshired Labs" along with the
    "Hired" repost farm."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())).strip()


COMPANY_BLOCKLIST = {norm_company(c) for c in SCORING["company_blocklist"]}


def blocked_company(row):
    """True for a lead-gen repost farm. Reads either schema's company key, so
    merge_jobs.py can apply the same rule to already-written output rows."""
    return norm_company(row.get("Company") or row.get("company")) in COMPANY_BLOCKLIST


def reachable(row):
    """True if this remote role is workable from HOME_LOCATION_HINTS, per
    SETTINGS["remote_scopes"]. Module level, and reading only output columns, so
    merge_jobs.py applies the identical rule to rows it can no longer re-score —
    two copies of this predicate drifting apart is how a shortlist ends up full
    of remote-in-Germany roles again.
    """
    if row.get("remote_scope") in SETTINGS["remote_scopes"]:
        return True
    # Two kinds of geo-locked role are still worth an application:
    #   - the employer demonstrably hires in your country (hires_home), so the
    #     entity or EOR that makes it possible already exists;
    #   - the lock is TO your country — a "remote within India" role is the most
    #     reachable kind there is, and dropping it as "not worldwide" is plainly
    #     wrong. This is not hypothetical: every LinkedIn f_WT=2 row comes back
    #     located in its own country, so without this the paid sweep discards
    #     what it paid to fetch.
    if not SETTINGS["keep_restricted_if_hires_home"]:
        return False
    return bool(row.get("remote_scope") == "restricted"
                and (row.get("hires_home") == "yes"
                     or is_home_location(row.get("remote_regions"))))


# Any "<n> years/yrs" mention (optionally "n+" or a range); we read the leading n.
#
# "year(s)" and "yr(s)" are in here because Accenture's JD template writes
# "Minimum 5 Year(s) Of Experience Is Required" — without the optional "(s)" that
# phrasing matched nothing, so an entire employer's experience bar read as
# "not stated" and 5- and 8-year roles ranked as if they had no requirement.
#
# A RANGE reads as its lower bound, so the separator has to cover every way a JD
# writes one. Only the ASCII hyphen was handled, so "10-18+ years of overall IT
# experience" and "5 to 12 years of hands-on experience" both matched on their
# SECOND number and reported 18 and 12 — an en dash and the word "to" are how
# Accenture actually writes it, and under experience_aggregate="max" that buried
# 32 reachable roles as if they wanted a career's worth.
YEARS_PATTERN = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:[-–—]|to)?\s*(?:\d{1,2}\s*\+?\s*)?"
    r"(?:years?|yrs?)(?:\(s\))?")


# A years figure only gates a candidate when it's counting EXPERIENCE. These
# three tests were derived from 63 live requisitions on one employer's board
# (2026-07-30), where the raw pattern above read a degree requirement as a career
# length.
_EXP_CUE_RE = re.compile(r"experience|hands[- ]on")
_EXP_OF_RE = re.compile(r"\s*(?:of|in)\s+[a-z]")   # "8+ years of|in <something>"
# A figure that is counting something else entirely, disqualified by the word
# IMMEDIATELY after it rather than by anything in the 60-character window —
# "founded 5 years ago, we now have 3+ years of experience shipping ML" put
# "experience" inside that window, so the company's age was read as the
# requirement. The docstring below claimed this case was handled; it was not,
# and min() hid it by preferring the smaller number.
_NOT_EXP_RE = re.compile(r"\s*(?:ago|old\b|of age|in business)")
# The same disqualifier on the other side, for the phrasings that put the
# company's age BEFORE the figure: "In business 12 years. Seeking 3 years of
# experience." The phrase has to run right up to the number, so a JD that
# merely mentions a founding date elsewhere is unaffected.
_COMPANY_AGE_RE = re.compile(
    r"(?:in business|been (?:around|operating|serving)|founded|established|"
    r"celebrating|for over)\s*(?:for\s*)?(?:over\s*)?$")
_EDU_RE = re.compile(r"education|schooling|degree program")


# "<label> : 10+ years" — a header field whose entire value is a years figure.
# Netradyne's template writes the experience row as "Business Systems Group :
# 10+ years", so no experience cue sits anywhere near the number and the
# cue-based test read the posting as "didn't say". It was a 10-year job ranked
# first on a 2-year candidate's shortlist.
_FIELD_YEARS_RE = re.compile(
    # COLON only. A hyphen here matched the range "10-18+ years" as
    # label="10", value=18, which under the "max" aggregate turned a 10-year
    # posting into an 18-year one — the opposite of the bug being fixed.
    r"([\w /&()]{0,40}?)\s*:\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)
# ...but plenty of labelled year-values are not experience at all.
_DURATION_LABEL_RE = re.compile(
    r"contract|duration|notice|tenure|validity|bond|period|term|warranty|"
    r"internship|course|degree|education|age\b", re.I)


def _required_experience_floor(text):
    """The experience a posting demands, in years, or None if it doesn't say.

    Only counts figures that are talking about experience: "minimum 16 years of
    formal education" is a degree, not a career, and reading it as one made a
    4-year role look like a 16-year one. Prose like "founded 5 years ago" is
    likewise ignored rather than resolved to a requirement.

    SETTINGS["experience_aggregate"] picks how several figures combine, because
    the right answer depends on how the employer writes:

      "max" (default)  A JD that states a total AND a per-skill figure. "8+
          years of total software engineering experience ... 2+ years hands-on
          in AI/ML" is an 8-year job, and min() ranked it first out of 63 as if
          it wanted 2. Across those 63: 21 read differently, all 21 in favour
          of max. Every hand-tuned profile in this repo had already set this,
          and a user reported the symptom the default caused: the results
          column reading 2+ or 3+ on postings whose JD asks for 5+ or 8+.
      "min"  Short JDs where the smallest number is the real ask and anything
          larger is a nice-to-have. Under-reads a structured JD, and
          under-reading is the dangerous direction — it ranks a senior role at
          the top of a junior candidate's shortlist, where over-reading only
          drops a reachable one.
    """
    vals = []
    for m in YEARS_PATTERN.finditer(text):
        after = text[m.end():m.end() + 60]
        before = text[max(0, m.start() - 30):m.start()]
        if _NOT_EXP_RE.match(after) or _COMPANY_AGE_RE.search(before):
            continue
        edu, exp = _EDU_RE.search(after), _EXP_CUE_RE.search(after)
        # Whichever word comes FIRST decides what the figure is counting. Both
        # can appear inside the same 60 characters: Accenture writes "minimum 3
        # Year(s) Of Experience Is Required. Educational Qualification: 15 Years
        # Full Time Education", where a plain "is there an education word nearby"
        # test throws away the 3 and keeps nothing.
        if edu and (not exp or edu.start() < exp.start()):
            continue
        if (exp or _EXP_OF_RE.match(after)
                or _EXP_CUE_RE.search(text[max(0, m.start() - 30):m.start()])):
            vals.append(int(m.group(1)))
    # Second pass: labelled fields, for the templates that never say "experience".
    for m in _FIELD_YEARS_RE.finditer(text):
        if not _DURATION_LABEL_RE.search(m.group(1)):
            vals.append(int(m.group(2)))
    if not vals:
        return None
    return min(vals) if SETTINGS.get("experience_aggregate") == "min" else max(vals)


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


def title_excluded(title):
    """Does this TITLE alone hard-disqualify the role?

    Split out of score_job so merge_jobs.py can re-apply it to a stored row.
    A merged file spans sweeps run under older term lists, and the title is one of
    the columns that IS kept — so unlike scoring, this rule can be re-checked
    later, and it has to be: after "developer"/"engineer" were added to
    hard_drop_terms, 26 of one shortlist's 50 rows were roles the current config
    would never have reported, still sitting there with their old scores.
    """
    low = (title or "").lower()
    return any(pat.search(low) for pat in HARD_DROP_PATTERNS.values())


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


@dataclass(frozen=True)
class ScoringContext:
    """Everything scoring reads that a profile sets, compiled once.

    Exactly the SCORING and SETTINGS keys make_profile.render() writes for a
    résumé — skill_weights, penalty_terms, the domain halves and bonus,
    hard_drop_terms; max_experience_years, candidate_experience_months — plus
    the engine that decides how skills count. The rest of what scoring reads
    (soft_drop_terms, the soft/drop/experience-gap/timezone penalties,
    company_blocklist, drop_excluded, experience_aggregate, home_utc_offset)
    is never rendered, so it stays in config: one value per Sweep.

    Frozen, so nothing evaluated under one context can change another.
    """
    engine: str                  # "v1": every matching term; "v2": each concept once
    skill_terms: tuple           # ((term, weight, pattern), ...)
    concepts: tuple              # skill_concepts.Concept, ...
    penalties: tuple             # ((term, penalty, pattern), ...)
    frontend: tuple              # patterns
    backend: tuple
    fullstack_title: tuple
    fullstack_bonus: float
    hard_drop: tuple             # patterns
    max_experience_years: float
    candidate_experience_months: object   # int, or None

    def __post_init__(self):
        if self.engine not in skill_concepts.VERSIONS:
            raise ValueError(f"unknown scoring engine {self.engine!r}")

    @functools.cached_property
    def key(self):
        """A digest of what this context scores with — the memo key. Contexts
        share it only when they would evaluate every row identically, so no
        name, and nothing about who asked, can make two of them collide."""
        return hashlib.sha256(json.dumps([
            self.engine,
            [[t, w, p.pattern] for t, w, p in self.skill_terms],
            [[c.id, c.weight, list(c.aliases)] for c in self.concepts],
            [[t, w, p.pattern] for t, w, p in self.penalties],
            [p.pattern for p in self.frontend], [p.pattern for p in self.backend],
            [p.pattern for p in self.fullstack_title], self.fullstack_bonus,
            [p.pattern for p in self.hard_drop],
            self.max_experience_years, self.candidate_experience_months,
        ], ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def scoring_context(scoring, settings, engine):
    """A context compiled from SCORING- and SETTINGS-shaped dicts, the way this
    module compiles the loaded profile's tables at import."""
    return ScoringContext(
        engine=engine,
        skill_terms=tuple((t, w, _compile(t)) for t, w in scoring["skill_weights"].items()),
        concepts=tuple(skill_concepts.from_weights(scoring["skill_weights"])),
        penalties=tuple((t, p, _compile(t)) for t, p in scoring["penalty_terms"].items()),
        frontend=tuple(_compile(t) for t in scoring["frontend_terms"]),
        backend=tuple(_compile(t) for t in scoring["backend_terms"]),
        fullstack_title=tuple(_compile(t) for t in scoring["fullstack_title_terms"]),
        fullstack_bonus=scoring["fullstack_bonus"],
        hard_drop=tuple(_compile(t) for t in scoring["hard_drop_terms"]),
        max_experience_years=settings["max_experience_years"],
        candidate_experience_months=settings.get("candidate_experience_months"))


def default_context():
    """The loaded profile's context, read from this module's tables NOW.

    Built per call rather than frozen at import, because the one-profile
    callers have always read the live tables: merge_jobs.py and demo() swap
    HARD_DROP_PATTERNS and SETTINGS in place, a test rebinds PENALTY_PATTERNS,
    and a SWEEP_PROFILE_ENGINE_VERSION rollback takes effect on the very next
    call (skill_concepts.enabled). The engine is the branch score_job has
    always taken: whatever enabled() says, which is the profile's own stamp
    once one is bound. A few tuples over compiled patterns, next to
    milliseconds of regex per evaluation.

    A schema-2 Sweep has no default context. Its globals hold the Sweep and
    config's defaults, not any track, so one-profile scoring there would
    score every row against somebody else's tables — refused rather than
    quietly run, and never answered with one of the tracks.
    """
    if config.TRACKS:
        raise RuntimeError(
            f"multi-context finalization not enabled: {config.PROFILE!r} is a "
            f"Multi-Track Sweep with {len(config.TRACKS)} tracks, and one-profile "
            f"scoring would read config's defaults rather than any track")
    return ScoringContext(
        engine="v2" if skill_concepts.enabled() else "v1",
        skill_terms=tuple((t, w, p) for t, (w, p) in SKILL_PATTERNS.items()),
        concepts=tuple(SKILL_CONCEPTS),
        penalties=tuple((t, p, pat) for t, (p, pat) in PENALTY_PATTERNS.items()),
        frontend=tuple(FRONTEND_PATTERNS),
        backend=tuple(BACKEND_PATTERNS),
        fullstack_title=tuple(FULLSTACK_TITLE_PATTERNS),
        fullstack_bonus=SCORING["fullstack_bonus"],
        hard_drop=tuple(HARD_DROP_PATTERNS.values()),
        max_experience_years=SETTINGS["max_experience_years"],
        candidate_experience_months=SETTINGS.get("candidate_experience_months"))


@dataclass(frozen=True)
class TrackContext:
    """One résumé track of a schema-2 Sweep, as the engine holds it: an opaque
    id, its search intent, its free-source title gate, and its ScoringContext,
    which carries its engine. No display name, résumé text or credential."""
    id: str
    role_keywords: tuple
    experience_years: int
    title_hints: tuple
    title_exclude: tuple
    scoring: ScoringContext

    @property
    def engine(self):
        return self.scoring.engine


def track_context(track, sweep_scoring):
    """A TrackContext from one checked TRACKS entry.

    Its tables are config's pristine defaults, then the Sweep's own SCORING
    (hard_drop_terms), then the track's own: the one-level merge
    config._overlay gives a profile. Never the live globals — the Sweep's
    overlay has already written those — so no other track's values are
    reachable from this one. The engine is the track's, never the process's.
    """
    scoring = {**config.BASE_SCORING, **sweep_scoring, **track["SCORING"]}
    return TrackContext(
        id=track["id"],
        role_keywords=tuple(track["SEARCH"]["role_keywords"]),
        experience_years=track["SEARCH"]["experience_years"],
        title_hints=tuple(track["ATS_TITLE_HINTS"]),
        title_exclude=tuple(track["ATS_TITLE_EXCLUDE"]),
        scoring=scoring_context(scoring, track["SETTINGS"], track["engine"]))


# The loaded Sweep's tracks in the file's order; () for any schema-1 profile.
# Loaded only: nothing plans, filters, scores or ranks through them yet.
TRACK_CONTEXTS = tuple(track_context(t, config.SWEEP_SCORING) for t in config.TRACKS)


@dataclass(frozen=True)
class JobFacts:
    """What scoring reads from one job copy that no profile changes.

    The gate facts are computed up front. The enrichment waits until some
    context keeps the row — which is when score_job has always computed it —
    so a row every context drops costs what it always did.
    """
    title: str                   # lower-cased
    text: str                    # lower-cased title, description and Experience
    blocked: bool                # a repost farm (config company_blocklist)
    years_required: object       # the experience floor the text states, or None
    soft_seniority: bool         # a config soft_drop_terms word in the title
    acquired: dict = field(repr=False, compare=False)   # a copy of the row as read

    @functools.cached_property
    def enrichment(self):
        """The job-level fields a kept row gets, in the order score_job writes
        them: enrich's remote/visa/eor/timezone signals, remote?, contacts."""
        got = enrich.signals(self.acquired, SETTINGS["home_utc_offset"])
        got["remote?"] = is_remote(got)
        got["hr_email"], got["hr_phone"] = extract_contacts(
            (self.acquired.get("Description") or "") + "\n"
            + (self.acquired.get("Title") or ""))
        return got


def job_facts(row):
    """JobFacts for a normalized row. Reads the row, never writes it."""
    title = (row.get("Title") or "").lower()
    # Include Experience (e.g. naukri's "2-4 Yrs") so the over-experience filter
    # sees it — it isn't always repeated in the description.
    text = (title + "\n" + (row.get("Description") or "") + "\n"
            + (row.get("Experience") or "")).lower()
    return JobFacts(
        title=title, text=text, blocked=blocked_company(row),
        years_required=_required_experience_floor(text),
        soft_seniority=any(pat.search(title) for pat in SOFT_DROP_PATTERNS.values()),
        acquired=dict(row))


# One context's verdict on one job copy. `reason` says why it is not eligible
# ("blocked_company" or "excluded"); the scoring fields are None when it is not.
# `exp_verdict` is experience_guard.assess()'s answer when that guard is on:
# returned, not recorded, so evaluating twice can never record twice.
Evaluation = namedtuple("Evaluation", "eligible reason score matched_skills "
                                      "is_fullstack years_required exp_verdict",
                        defaults=(None,) * 6)


def evaluate(row, ctx, facts=None):
    """ctx's verdict on this row. Pure: it reads the row, ctx and Sweep-level
    config, and writes nothing — not the row, not experience_guard.DROPPED,
    not skill_concepts' engine bind. score_job's rules, with every value a
    profile sets read from ctx. `facts` lets several contexts share one
    job_facts(row)."""
    facts = job_facts(row) if facts is None else facts
    title, text = facts.title, facts.text

    # --- Hard filter: repost farm ---------------------------------------------
    # Checked before scoring because these rank at the very top — they repost real
    # listings, so they match the résumé as well as the original does, and no
    # score threshold can separate them.
    if facts.blocked:
        return Evaluation(False, "blocked_company")

    # --- Hard filters: unreachable title, or more experience than we have -----
    # A title is a LABEL; the years the text demands are the requirement. So only
    # hard_drop_terms (manager/principal/staff/...) and a stated experience floor
    # over the threshold remove a job. "Senior"/"Lead" are handled below as a
    # down-rank, because title inflation would otherwise delete reachable roles.
    excluded = any(pat.search(title) for pat in ctx.hard_drop)
    floor = facts.years_required
    if floor is not None and floor > ctx.max_experience_years:
        excluded = True

    # SWEEP_EXPERIENCE_MISMATCH_GUARD (default off). The gate above compares one
    # max()-aggregated number against years_experience + 3, which is why a
    # 3y1m candidate kept 6+ roles: 6 is not > 6. This asks the narrower
    # question — does the JD CONFIRM an overall minimum the candidate is
    # materially short of — and only then acts. It never reads the title, never
    # acts on a preference, and returns "none" for everything it cannot resolve.
    exp_verdict = None
    if experience_guard.enabled():
        exp_verdict = experience_guard.assess(text, ctx.candidate_experience_months)
        if exp_verdict["action"] == "hard_drop":
            excluded = True

    if excluded and SETTINGS["drop_excluded"]:
        return Evaluation(False, "excluded", years_required=floor,
                          exp_verdict=exp_verdict)

    # --- Positive skill matches ---
    # v2: one technology contributes once however many ways the profile spells
    # it, and matched_skills records the concept's display name rather than
    # whichever alias happened to fire. v1: every term scores on its own.
    if ctx.engine == "v2":
        score, matched = skill_concepts.score(text, ctx.concepts)
    else:
        score = 0
        matched = []
        for term, weight, pat in ctx.skill_terms:
            if pat.search(text):
                score += weight
                matched.append(term)

    # --- Full-stack detection + bonus ---
    has_frontend = any(pat.search(text) for pat in ctx.frontend)
    has_backend = any(pat.search(text) for pat in ctx.backend)
    title_says_fullstack = any(pat.search(title) for pat in ctx.fullstack_title)
    is_fullstack = (has_frontend and has_backend) or title_says_fullstack
    if is_fullstack:
        score += ctx.fullstack_bonus

    # --- Penalties (off-stack + Salesforce/CRM) ---
    for _term, penalty, pat in ctx.penalties:
        if pat.search(text):
            score += penalty

    # --- Down-ranks that keep the job in the list ---
    if facts.soft_seniority:
        score += SCORING["soft_penalty"]
    # A one-to-two-year shortfall sinks the row rather than removing it — the
    # same trade soft_drop_terms makes, and for the same reason: "5+ years" on
    # an international remote posting is routinely negotiable.
    if exp_verdict is not None and exp_verdict["action"] == "penalty":
        score += SCORING["experience_gap_penalty"]
    if excluded:  # only reached when drop_excluded is False
        score += SCORING["drop_penalty"]

    # Timezone distance: a down-rank, not a filter. A 13.5h gap to US Pacific is
    # a real cost to weigh against the role, not a disqualification. Rounded so
    # the score column stays integral.
    gap = facts.enrichment["tz_gap"]
    if isinstance(gap, (int, float)):
        over = gap - enrich.TZ_FREE_HOURS
        if over > 0:
            score = round(score + SCORING["timezone_gap_penalty"] * over)

    return Evaluation(True, None, score,
                      ", ".join(dict.fromkeys(matched)),  # dedup, preserve order
                      is_fullstack, floor, exp_verdict)


# The per-context memo on a row object: {ScoringContext.key: Evaluation}.
EVALS = "_evals"


def evaluate_once(row, ctx):
    """evaluate(), at most once per row object and context. Keyed by ctx.key,
    so one context never receives another's verdict. Writes only EVALS; it
    is valid for the reason SCORED is (below): evaluate reads nothing a
    score_job write changes."""
    memo = row.setdefault(EVALS, {})
    if ctx.key not in memo:
        memo[ctx.key] = evaluate(row, ctx)
    return memo[ctx.key]


def score_job(row):
    """Attach score/matched_skills/is_fullstack/remote? to a normalized row.

    Returns the row, or None if it's hard-filtered (wrong seniority / too much
    experience and SETTINGS['drop_excluded'] is True).

    The one-profile API, unchanged for its callers: evaluate() under the loaded
    profile's context, then the two effects evaluate() leaves out — the
    experience guard's record, and the fields a kept row carries. A dropped row
    is returned untouched.
    """
    facts = job_facts(row)
    got = evaluate(row, default_context(), facts)
    if got.exp_verdict is not None:
        experience_guard.record(got.exp_verdict, row.get("Title") or "")
    if not got.eligible:
        return None
    row["score"] = got.score
    row["matched_skills"] = got.matched_skills
    row["is_fullstack"] = got.is_fullstack
    # Keep the PARSED figure, not just the filtering decision it fed. The
    # experience_required column used to read row["Experience"], a raw field only
    # a couple of sources ever set (naukri's "2-4 Yrs", lever's commitment), so it
    # was blank for LinkedIn, Amazon, Workday, SuccessFactors and Optum alike —
    # every row whose requirement lives in the JD prose, which is most of them.
    # The number is already computed here for the over-experience gate; it's the
    # single most decision-relevant field for a candidate with a fixed number of
    # years, so it belongs in the output rather than being thrown away.
    row["years_required"] = got.years_required
    row.update(facts.enrichment)   # remote/visa/eor/tz signals, remote?, contacts
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

    A published REQUISITION NUMBER outranks all of that, because it is the
    employer's own identity for the opening and it is what an application is
    submitted against. Company+title is a heuristic for "same posting seen
    twice"; a req number is a fact. Without this, one employer's board collapses
    genuinely separate openings — "Senior Full Stack Engineer" in Bengaluru and
    in Hyderabad are two requisitions, two hiring managers, two applications, and
    company+title made them one row. Measured on the 2026-07-30 Optum sweep: 107
    live requisitions collapsed to 63. Only sources that publish a req id are
    affected (today: Optum); every other row keys exactly as before.
    """
    req = (row.get("req_number") or "").strip()
    if req:
        return "req", req.lower()
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
    # 3. f_C=<numeric company id> is the third thing that bills you for the wrong
    #    data when guessed. A wrong id doesn't error — it returns some other
    #    employer's jobs. Measured: 1409, widely cited as Capgemini, is Wells
    #    Fargo Advisors. So an unmapped name raises here, before any spend.
    company = (s.get("company") or "").strip()
    if company:
        company_id = LINKEDIN_COMPANY_IDS.get(company)
        if not company_id:
            raise ValueError(
                f"linkedin: no company id for '{company}'. A wrong f_C silently "
                f"returns a DIFFERENT company's jobs at full price. Add it to "
                f"config.LINKEDIN_COMPANY_IDS and confirm with "
                f"`python verify_geoids.py --companies`, or target the employer "
                f"by keyword instead.")
        params["f_C"] = company_id
    if remote_only:
        params["f_WT"] = "2"
    code = _linkedin_experience_code(s.get("experience_years"))
    if code:
        params["f_E"] = code
    if SETTINGS["max_age_days"]:  # LinkedIn "posted in last N days" = f_TPR=r<seconds>
        params["f_TPR"] = f"r{int(SETTINGS['max_age_days']) * 86400}"
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)


def _indeed_country(s):
    """Which Indeed market one search combo runs in.

    Indeed is a per-country site. The code used to come from
    SEARCH["country"], a single value no generated profile could override, so
    a sweep scoped to "Onsite, anywhere in the world" sent
    {"location": "United Kingdom", "country": "IN"} and paid the Indian site
    to look for a country. The market now follows the LOCATION being
    searched, through config.INDEED_COUNTRIES.

    REFUSES an unmapped place rather than falling back, exactly as
    _build_linkedin_url refuses an unmapped geoId, and for the same reason: a
    wrong market does not raise, it bills for the wrong country's jobs. Every
    planned search is put through build_input in main()'s preflight before a
    single actor starts, so this surfaces before anything is spent.
    """
    loc = (s.get("location") or "").strip()
    if not loc or loc.lower() == config.LOCATION_NOT_A_PLACE.lower():
        # "Remote" is an arrangement, not a place, and Indeed has no
        # worldwide-remote search — so a bare remote search runs in the home
        # market, which is the same call SITES["linkedin"]["remote_geo"]
        # already makes for LinkedIn.
        return SEARCH["country"]
    code = INDEED_COUNTRIES.get(loc)
    if not code:
        raise ValueError(
            f"indeed: no country code for '{loc}'. Indeed searches one "
            f"country's site, so running this would bill for the wrong "
            f"market. Add it to config.INDEED_COUNTRIES.")
    return code


def build_input(site_key, s):
    """Map one search combo (keywords/location/country/experience/max_results)
    onto the actor's expected input schema."""
    if site_key == "indeed":
        return {
            "position": s["keywords"],
            "location": s.get("location", ""),
            "country": _indeed_country(s),
            "maxItemsPerSearch": s["max_results"],
            "parseCompanyDetails": False,
            "saveOnlyUniqueItems": True,
            "followApplyRedirects": False,
        }
    if site_key == "linkedin":
        # The depth field the CURRENT actor documents is limitPerSource. The
        # published schema has no `count` at all, and it says an omitted limit
        # scrapes "as many as LinkedIn returns for each search (up to ~1000)" —
        # so a depth field the actor does not read is not a smaller sweep, it is
        # an unbounded one. V2-A verified the mismatch; this sends the field the
        # contract defines.
        #
        # `count` is KEPT, at the identical value, as a hedge against an older
        # build: the audit's retained logs show intended-depth-15 searches
        # returning 16-18 rows, which is a build that read `count` and honoured
        # it. Apify's input schema defaults to additionalProperties: true, and
        # those historical runs succeeded rather than failing validation, so an
        # unread extra field costs nothing. Both read ONE expression, so the two
        # can never disagree about depth — asserted in test_paid_contract.py.
        depth = max(ACTOR_MIN_RESULTS["linkedin"], s["max_results"])
        return {
            "urls": [_build_linkedin_url(s)],
            "limitPerSource": depth,     # authoritative: the documented field
            "count": depth,              # legacy hedge, same value
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
def build_search_plan(keywords, locations, companies=None):
    """Cross product of keywords x locations x companies (one search dict each).

    All three are per-site overridable (SITES[site]["keywords"|"locations"|
    "companies"]) and keywords can be overridden per-run with --keywords.
    companies defaults to [None] — no company dimension — so a normal sweep is
    the same keywords x locations plan it always was. Only LinkedIn consumes it
    (f_C); other adapters ignore the key.
    """
    plan = []
    for keyword in keywords:
        for location in locations:
            for company in (companies or [None]):
                plan.append({
                    "keywords": keyword,
                    "location": location,
                    "company": company,
                    "country": SEARCH["country"],
                    "experience_years": SEARCH["experience_years"],
                    "salary_min": SEARCH["salary_min"],
                    "max_results": SEARCH["max_results"],
                })
    return plan


FREE_SITES = ("ats", "feeds", "free", "optum")  # pseudo-sites: no Apify actor, no cost


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
    plan = build_search_plan(keywords, locations,
                             SITES[site_key].get("companies"))

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
# Actor-side minimums on the per-search result count. Applied in
# effective_search below — the ONE place a search's billable depth is decided
# — rather than inside build_input, so `--dry-run --json` (and therefore
# Sweep's cost estimate) reports the depth that will actually be BILLED, not
# the smaller one that was asked for.
ACTOR_MIN_RESULTS = {"linkedin": 10}   # apimaestro/linkedin actor requires count >= 10


# ---------------------------------------------------------------------------
# Provider-enforced charge ceiling
# ---------------------------------------------------------------------------
# Sweep's spend guard checks `spent >= budget` BEFORE the next run, using the
# account delta from the PREVIOUS one. It cannot bound the run it is about to
# start. That was survivable while the depth field was believed to work; V2-A
# showed the actor does not document `count`, so the true worst case for one
# "15 result" search was ~1,000 results — about $2.00 where the estimate said
# $0.027, discovered only after the charge.
#
# maxTotalChargeUsd is the provider's own limit on a pay-per-event run. It is
# passed as a START ARGUMENT, not as actor input: the SDK exposes it as
# ActorClient.start(max_total_charge_usd=...) and sends it as the
# `maxTotalChargeUsd` query parameter on POST /v2/acts/.../runs. Putting the
# same name inside run_input would be an ordinary unread input field and would
# bound nothing. https://docs.apify.com/api/v2/actors-runs-post
#
# Numbers read from the store's own pricing record on 2026-09-22:
#   https://api.apify.com/v2/acts/curious_coder~linkedin-jobs-scraper
# Current entry, PAY_PER_EVENT, started 2026-08-14:
#   apify-default-dataset-item ("result")  FREE $0.002 / paid tiers $0.001
#   apify-actor-start                      $0.00005, one per GB, minimum one
#   minimalMaxTotalChargeUsd               $0.001
#   defaultRunOptions.memoryMbytes         512  (so one start event)
# FREE-tier prices are used because they are the HIGHEST published: a ceiling
# computed from the cheaper tier would abort a free-plan account's legitimate
# run. This is the safe direction to be wrong in.
ACTOR_CHARGE_MODEL = {
    "linkedin": {
        "result_usd": Decimal("0.002"),      # FREE tier, the dearest published
        "start_usd": Decimal("0.00005"),     # per GB of memory, minimum one
        # 512MB default bills one start event; two covers a memory bump.
        "start_events": 2,
        # Headroom so the ceiling never truncates an honest run. The actor has
        # historically returned 16-18 rows for an intended 15, so a ceiling at
        # exactly the intended depth would abort a run that behaved normally.
        "overshoot": Decimal("1.5"),
        "provider_minimum_usd": Decimal("0.001"),   # minimalMaxTotalChargeUsd
    },
    # V2-C3.5. Read on 2026-09-24 from misceres~indeed-scraper's record (build
    # 0.0.111) and its store pricing page. Current entry, PAY_PER_EVENT, started
    # 2026-03-26, one event only:
    #   result ("Job listing", "Cost per every job listing returned")
    #                              FREE $0.006 / BRONZE $0.005 ... DIAMOND $0.001
    #   no apify-actor-start event is priced
    #   minimalMaxTotalChargeUsd   null
    #   platform usage             included (isUserPayingForPlatformUsage: false)
    # `result` is a CUSTOM event, charged by the actor's own code, so the bound
    # rests on Apify's rule rather than on the actor counting right: a user is
    # "never charged for produced events over the defined limit", and the
    # platform aborts the run there. See
    # docs/search-engine-v2-c35-indeed-bounded-execution.md.
    "indeed": {
        "result_usd": Decimal("0.006"),      # FREE tier, the dearest published
        "start_usd": Decimal("0"),           # no start event is priced
        "start_events": 0,
        # The changelog records this actor overshooting maxItemsPerSearch
        # ("slight overflows", 2025-07-11; "exceeding the specified limit",
        # 2026-01-15). LinkedIn's headroom, for LinkedIn's reason: a ceiling at
        # exactly the depth would abort an honest run that ran one row over.
        "overshoot": Decimal("1.5"),
        "provider_minimum_usd": Decimal("0"),       # minimalMaxTotalChargeUsd: null
    },
    # Naukri is deliberately absent: disabled by default, and its own
    # minimalMaxTotalChargeUsd ($0.10) and per-event prices have not been
    # measured against a run. Absent means "no ceiling computed", which
    # max_charge_usd reports as None — and C2 then runs it one at a time.
}

# Rounded UP to a tenth of a cent. Coarser than the published $0.00005 event
# granularity on purpose: rounding up can only ever raise the ceiling, and a
# figure carried to more decimal places than the model supports would be
# invented precision.
CHARGE_CEILING_STEP = Decimal("0.001")


def max_charge_usd(site_key, depth):
    """Provider-enforced maximum charge for ONE run at `depth`, or None.

    None means this site has no charge model, which is not the same as "no
    limit is needed" — scrape_search decides what to do about it.
    """
    model = ACTOR_CHARGE_MODEL.get(site_key)
    if model is None:
        return None
    ceiling = (Decimal(depth) * model["result_usd"] * model["overshoot"]
               + Decimal(model["start_events"]) * model["start_usd"])
    ceiling = (ceiling / CHARGE_CEILING_STEP).to_integral_value(
        rounding=ROUND_CEILING) * CHARGE_CEILING_STEP
    # The provider refuses a ceiling below its own minimum, and a refused start
    # is a failed search rather than an unbounded one; raise it instead.
    return max(ceiling, model["provider_minimum_usd"])


def charge_ceiling_supported(actor_client):
    """Whether this apify-client can actually apply the ceiling.

    requirements.txt asks for a version that has it, but a deployed environment
    is not a requirements file. Introspected rather than assumed, because the
    failure it guards against is silent: an older client would raise TypeError
    only if we passed the argument, and the tempting "handle it" is to drop the
    argument and run anyway — which is the exact outcome this exists to prevent.
    """
    try:
        return "max_total_charge_usd" in inspect.signature(
            actor_client.start).parameters
    except (TypeError, ValueError):
        return False


def effective_search(site_key, search):
    """Apply a site's results_per_run override (some actors, e.g. naukri, have a
    per-run minimum charge so it's wasteful to pull only a few results), then
    any actor-side minimum on the result count.

    Every build_input() call goes through here (scraper.py:951, 1650, 1667), so
    this is the authoritative billable depth for a search.
    """
    per_run = SITES[site_key].get("results_per_run")
    if per_run is not None:
        search = {**search, "max_results": per_run}
    floor = ACTOR_MIN_RESULTS.get(site_key)
    if floor is not None and search["max_results"] < floor:
        search = {**search, "max_results": floor}
    return search


def account_usage_usd(client):
    """Real month-to-date spend on the account, or None if it can't be read.

    The authoritative number. run.usage_total_usd — what the actor reports for
    its own run — UNDERCOUNTS: a measured 84-run sweep self-reported $0.53 while
    the account actually moved $1.61, so a spend cap built on the self-report
    would have let roughly 3x through before stopping. Platform usage beyond the
    actor's own accounting is evidently not included in it.
    """
    try:
        current = client.user().limits().model_dump().get("current") or {}
        return float(current.get("monthly_usage_usd") or 0)
    except Exception:
        return None


def remote_was_queried(site_key, search):
    """True when the SEARCH ITSELF constrained results to remote roles.

    Worth trusting over the text, because the text often doesn't say. Measured on
    a real LinkedIn f_WT=2 sweep: of 76 rows, every one arrived located by city
    ("Toronto, Ontario, Canada", "New York, NY") with no remote marker in the
    data at all — the remoteness lived in the query. 22 of them classified as
    "not stated" and would have been filtered out as non-remote despite being
    exactly what we paid to ask for.

    A literal "Remote" location means remote for every adapter: LinkedIn maps it
    to f_WT=2, naukri to workMode=remote, indeed passes it as the location.
    """
    if (search.get("location") or "").strip().lower() == "remote":
        return True
    return site_key == "linkedin" and bool(SITES.get("linkedin", {}).get("remote_only"))


def paid_unit_id(n):
    return f"paid_{n:03d}"


def paid_unit(n, site_key, search):
    """V2-C1: planned paid search `n` as telemetry records it, with the depth
    and ceiling scrape_search will use. No query text: a fingerprint tells a
    repeat of one search from a different one without saying what it was.
    V2-C4's keyword_fp leaves the location out, so the same keyword searched
    in two places — India and Remote — can be paired without naming either."""
    depth = effective_search(site_key, search)["max_results"]
    ceiling = max_charge_usd(site_key, depth)
    keyword = (site_key, search.get("keywords") or "", search.get("company") or "")
    shape = "|".join((site_key, search.get("keywords") or "",
                      search.get("location") or "", search.get("company") or ""))
    return {"unit_id": paid_unit_id(n), "plan_index": n, "provider": site_key,
            "actor": SITES[site_key]["actor"], "requested_depth": depth,
            "charge_ceiling_usd": None if ceiling is None else str(ceiling),
            "query_fp": hashlib.sha256(shape.encode()).hexdigest()[:12],
            "keyword_fp": hashlib.sha256("|".join(keyword).encode()).hexdigest()[:12],
            "location_mode": ("remote" if remote_was_queried(site_key, search)
                              else "place"),
            "company_filter": bool(search.get("company"))}


_POSITION_RE = re.compile(r"[1-9][0-9]*")


def provider_positions(site_key, links):
    """V2-C4: each result's rank as the PROVIDER gave it, or None — UNKNOWN.

    search_rank is the dataset index, and the dataset is the actor's PUSH
    order: LinkedIn's actor fetches details concurrently, and V2-C3 measured
    six runs whose dataset order was nothing like LinkedIn's rank (C1's first
    read 15, 14, 13, 12, 11, 10, 6, 8, 4, 9, 5, 3, 7, 2, 1). LinkedIn's own rank
    is the `position` parameter of each result's `link`. Indeed publishes none
    (build 0.0.111's dataset schema has no rank field), so it is UNKNOWN —
    never its dataset order.

    Per run: a position that is missing, malformed or not a positive integer is
    UNKNOWN, and so is every claim to one position made twice, since a
    duplicate says a claim is wrong and nothing says which. A position past the
    requested depth is kept: an overshooting run's 16th result is its 16th.
    """
    if site_key != "linkedin":
        return [None] * len(links)
    got = []
    for link in links:
        values = (urllib.parse.parse_qs(urllib.parse.urlsplit(link).query).get("position")
                  if isinstance(link, str) else None)
        got.append(int(values[0]) if values and len(values) == 1
                   and _POSITION_RE.fullmatch(values[0]) else None)
    claims = Counter(p for p in got if p is not None)
    return [None if p is None or claims[p] > 1 else p for p in got]


# V2-D4: seconds between status polls of a started run. 5 s everywhere except
# Indeed: its runs take ~4 s at the provider (C5 median 4.1 s), so a 5 s poll
# worked by coincidence with that runtime; C5's timestamps simulated at 2 s cut
# its median detection lag 1.67 -> 1.24 s and p95 4.52 -> 2.83 s, about 17 s off
# a 90-search plan's Indeed segment, for ~87 more plain GETs (no charge) and no
# 429 in evidence. 3 s simulated WORSE than 5 s. LinkedIn's ~27 s runs gain
# ~10 s for twice the requests: unchanged. See the V2-D doc, section 4.
POLL_DEFAULT_SECONDS = 5
POLL_SECONDS = {"indeed": 2}


def scrape_search(client, site_key, actor_id, search, before_start=None,
                  after_start=None, after_run=None):
    """Run one actor and return (rows, cost_usd).

    apify-client 3.x returns a typed Run object (not a dict).

    `before_start` (V2-C2) is called once, immediately before the start
    request: the last point at which nothing can yet have been charged. The
    serial loop passes nothing.

    V2-C4.5, for the account pool only: `after_start(run_id)` once the start
    returned, and `after_run(status)` with the status the polling ended on,
    so a run's memory and run slot are released only once the provider says
    it has ended. Neither may raise; neither is passed outside the pool.
    """
    effective = effective_search(site_key, search)
    run_input = build_input(site_key, effective)
    # NOTE: results are bounded by the actor's OWN input cap (maxItemsPerSearch /
    # maxJobs / limitPerSource). We do NOT pass call(max_items=...) because on actors with a
    # per-run minimum charge it errors ("less than allowed minimum of $0.50").
    # Launch non-blocking, then poll with a wall-clock deadline. We deliberately
    # AVOID .call()/.wait_for_finish(): both long-poll with timeout='no_timeout',
    # which hangs FOREVER when a TCP socket half-dies (the run finishes on Apify's
    # side but the client never receives the response — observed wedging the whole
    # sweep at 0% CPU on an idle ESTABLISHED connection). Plain .get() uses a
    # bounded 5s HTTP timeout + retries, so a stalled poll raises and the caller's
    # per-search try/except moves on. run_timeout also caps the actor server-side.
    # ponytail: fixed 6-min deadline; raise if a legit pull runs longer. The poll
    # interval is per provider (POLL_SECONDS).
    #
    # The charge ceiling is the second half of the depth fix and the half that
    # does not depend on the actor reading our input at all: run_timeout bounds
    # the clock, limitPerSource asks for a depth, and maxTotalChargeUsd is the
    # only one of the three the PROVIDER enforces against the bill.
    actor = client.actor(actor_id)
    ceiling = max_charge_usd(site_key, effective["max_results"])
    start_kwargs = {}
    if ceiling is not None:
        if not charge_ceiling_supported(actor):
            # Fail CLOSED. Starting anyway would be starting the exact run this
            # guard exists to bound, and the per-search try/except in main()
            # turns this into one failed search rather than a spent one.
            raise RuntimeError(
                f"{site_key}: this apify-client cannot set maxTotalChargeUsd, so "
                f"the ${ceiling} per-run charge ceiling could not be applied. "
                f"Refusing to start an unbounded paid run — upgrade apify-client "
                f"(requirements.txt pins the verified minimum).")
        start_kwargs["max_total_charge_usd"] = ceiling
        telemetry.paid_run(max_total_charge_usd=str(ceiling))
    if before_start is not None:
        before_start()
    starting = time.monotonic()
    run = actor.start(run_input=run_input, run_timeout=timedelta(minutes=5),
                      **start_kwargs)
    start_ms = round((time.monotonic() - starting) * 1000)
    if after_start is not None:
        after_start(getattr(run, "id", None))
    rc = client.run(run.id)
    # Recorded the moment they exist. Without a run ID and a build ID no future
    # measurement can join Sweep's account of a search to Apify's — which is
    # precisely why the audit's paid cost-per-eligible-job is UNKNOWN. The token
    # is not among these fields and paid_run() drops anything unlisted.
    telemetry.paid_run(actor_id=actor_id, actor_run_id=getattr(run, "id", ""),
                       actor_build_id=getattr(run, "build_id", ""),
                       actor_started_at=getattr(run, "started_at", ""),
                       # V2-C1: the ceiling as the PROVIDER recorded it, read
                       # off the Run the start call returned.
                       provider_ceiling_usd=getattr(
                           getattr(run, "options", None), "max_total_charge_usd", None),
                       start_ms=start_ms)
    deadline = time.monotonic() + 360
    polls = 0
    waiting, polling_s = time.monotonic(), 0.0
    interval = POLL_SECONDS.get(site_key, POLL_DEFAULT_SECONDS)
    while time.monotonic() < deadline:
        time.sleep(interval)
        polls += 1
        asked = time.monotonic()
        run = rc.get()
        polling_s += time.monotonic() - asked
        if run is None or run.status not in ("READY", "RUNNING"):
            break
    else:
        rc.abort()          # deadline blown — stop the run server-side
        run = rc.get()
    if after_run is not None:
        after_run(getattr(run, "status", None))
    telemetry.paid_run(poll_count=polls,
                       actor_status=getattr(run, "status", "NO RUN"),
                       actor_finished_at=getattr(run, "finished_at", ""),
                       dataset_id=getattr(run, "default_dataset_id", ""),
                       # V2-C1: where the wait went, and what the terminal Run
                       # already says — fields of an object in hand, not requests.
                       wait_ms=round((time.monotonic() - waiting) * 1000),
                       poll_get_ms=round(polling_s * 1000),
                       actor_run_time_s=getattr(
                           getattr(run, "stats", None), "run_time_secs", None),
                       actor_build_number=getattr(run, "build_number", None),
                       charged_events=getattr(run, "charged_event_counts", None))
    # Provisional: C0's run read one result short here against its settled
    # charge. Recorded before the status check, so a failed run keeps it too.
    telemetry.cost_observation("run_record_at_completion",
                               getattr(run, "usage_total_usd", None))
    if run is None or run.status != "SUCCEEDED":
        status = getattr(run, "status", "NO RUN")
        raise RuntimeError(f"run status {status}")
    cost = float(run.usage_total_usd or 0)
    if not run.default_dataset_id:
        return [], cost
    # enumerate: the dataset preserves the actor's result order, so the index IS
    # the position within this search.
    label = f"{search.get('keywords') or '(all)'} @ {search.get('location') or ''}"
    rows, links = [], []
    reading = time.monotonic()
    for rank, item in enumerate(
            client.dataset(run.default_dataset_id).iterate_items(), 1):
        row = normalize(item, site_key)
        row["search_query"] = label
        # The dataset index — the actor's push order, NOT the provider's rank
        # (V2-C3 §14). Kept as it is: an output column. The rank is below.
        row["search_rank"] = rank
        rows.append(row)
        links.append(item.get("link") if isinstance(item, dict) else None)
    if telemetry.active():
        # V2-C4, the "_native" bargain: a key to_output() never reads, only
        # while a record is open, for depth analysis by the provider's rank.
        for row, position in zip(rows, provider_positions(site_key, links)):
            row[telemetry.PROVIDER_POSITION] = position
    telemetry.paid_run(dataset_retrieved_at=datetime.now().isoformat(
        timespec="milliseconds"),
        dataset_ms=round((time.monotonic() - reading) * 1000))  # read + normalize
    if remote_was_queried(site_key, search):
        # Stamp what the query already guarantees, the same way the remote-only
        # feeds do, so enrich sees it. Their own location text is kept because it
        # still carries the SCOPE (a city means the remote role is locked to that
        # country), which is the distinction that decides reachability.
        for row in rows:
            row["Location"] = (row["Location"] + ", Remote").strip(", ")
    return rows, cost


# ===========================================================================
# V2-C2 — reservation-safe, bounded, deterministic paid concurrency
# ===========================================================================
# SWEEP_PAID_CONCURRENCY (default OFF). Off, main() runs its serial paid loop
# exactly as before. On, paid_phase_c2() runs the SAME plan — order, inputs,
# ceilings — through a coordinator that reserves every start's full provider
# ceiling before the start can happen, lets up to SWEEP_PAID_WORKERS
# provider-bounded searches wait on Apify at once, and integrates their results
# in plan order. At one worker it is the serial loop plus reservation
# accounting. See docs/search-engine-v2-c2-paid-reservations-concurrency.md.
PAID_CONCURRENCY_FLAG = "SWEEP_PAID_CONCURRENCY"
PAID_WORKERS_ENV = "SWEEP_PAID_WORKERS"
PAID_WORKERS_DEFAULT = 2
# Every start is a paid run on one account. Four is the most the offline
# benchmark measured; the cap keeps an environment typo from becoming a burst.
PAID_WORKERS_MAX = 4


def paid_concurrency():
    return os.environ.get(PAID_CONCURRENCY_FLAG, "").strip().lower() in (
        "1", "true", "yes", "on")


def paid_workers():
    """Clamped, never rejected — the free providers' rule (sources/concurrency):
    a bad number falls back to the default instead of failing a sweep."""
    raw = os.environ.get(PAID_WORKERS_ENV, "").strip()
    try:
        n = int(raw) if raw else PAID_WORKERS_DEFAULT
    except ValueError:
        return PAID_WORKERS_DEFAULT
    return max(1, min(PAID_WORKERS_MAX, n))


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class PaidExposure:
    """What this sweep has put at risk, for deciding whether the next paid
    search may start. AUTHORIZATION EXPOSURE, never a cost: a $0.046 hold is
    not $0.046 spent.

      committed  the full provider ceiling of every start that may have reached
                 the provider. Never released during the sweep, whatever the run
                 later costs: V2-C1 measured the terminal poll 7-93% low and the
                 account delta at the $0.00005 start event alone, and Apify
                 publishes no "this charge is final". Headroom handed back from
                 those readings would be headroom read off a number known to lag.
      pending    ceilings reserved for starts not yet attempted — the guard
                 between deciding and starting. Released only when the start
                 was never attempted.
      unbounded  what the pre-C2 guard observed across searches that have no
                 provider ceiling (Naukri since V2-C3.5): lagging, but all
                 there is.
      observed   the pre-C2 guard's own figure for the sweep (account delta,
                 else run-record sum). It enters through max(), so it can only
                 make the view larger — C2 is never less cautious than the old
                 guard — and a reading that goes DOWN hands nothing back.

    One lock around every read-decide-write, so two threads can never both see
    the same headroom. Process-local: spend by anything else on the account is
    invisible here until an account read shows it.
    """

    def __init__(self, budget):
        self.budget = None if budget is None else Decimal(str(budget))
        self.committed = self.pending = self.pending_peak = Decimal(0)
        self.unbounded = self.observed = Decimal(0)
        self.units = {}
        self._lock = threading.Lock()

    def view(self):
        return max(self.observed, self.committed + self.pending + self.unbounded)

    def reserve(self, unit_id, ceiling):
        """Hold a bounded start's full ceiling, or refuse it: it fits only if
        view + ceiling <= budget."""
        with self._lock:
            view = self.view()
            if self.budget is not None and view + ceiling > self.budget:
                self.units[unit_id] = {"state": "blocked", "usd": ceiling,
                                       "view_usd": view}
                return False
            self.pending += ceiling
            self.pending_peak = max(self.pending_peak, self.pending)
            self.units[unit_id] = {"state": "pending", "usd": ceiling,
                                   "reserved_at": _utc_now()}
            return True

    def admit(self, unit_id):
        """An unbounded start has no ceiling to hold. It gets the pre-C2 test —
        stop once the budget is reached — on the C2 view, so every ceiling
        committed before it still counts against it."""
        with self._lock:
            view = self.view()
            if self.budget is not None and view >= self.budget:
                self.units[unit_id] = {"state": "blocked", "usd": None,
                                       "view_usd": view}
                return False
            self.units[unit_id] = {"state": "unbounded", "usd": None}
            return True

    def commit(self, unit_id):
        """The start request is about to be sent. From here the provider may
        charge — whether the call returns, raises, times out, fails or is
        aborted, with or without a run id — so the hold is now permanent."""
        with self._lock:
            u = self.units[unit_id]
            if u["state"] == "pending":
                self.pending -= u["usd"]
                self.committed += u["usd"]
                u.update(state="committed", committed_at=_utc_now())

    def release(self, unit_id):
        """The start was never attempted (a local failure before the request):
        the pending hold goes back. A committed hold never does."""
        with self._lock:
            u = self.units.get(unit_id)
            if u is not None and u["state"] == "pending":
                self.pending -= u["usd"]
                u.update(state="released_before_network", released_at=_utc_now())

    def observe(self, spent, unbounded_delta=None):
        """The pre-C2 guard's reading after a search; for an unbounded one,
        also what it moved by across that search."""
        with self._lock:
            self.observed = max(self.observed, Decimal(str(round(spent, 6))))
            if unbounded_delta is not None:
                self.unbounded += max(Decimal(0),
                                      Decimal(str(round(unbounded_delta, 6))))

    def snapshot(self):
        with self._lock:
            states = Counter(u["state"] for u in self.units.values())
            return {"budget_usd": None if self.budget is None else str(self.budget),
                    "committed_usd": str(self.committed),
                    "pending_usd": str(self.pending),
                    "pending_peak_usd": str(self.pending_peak),
                    "unbounded_observed_usd": str(self.unbounded),
                    "observed_usd": str(self.observed),
                    "view_usd": str(self.view()),
                    "committed_starts": states["committed"],
                    "released_before_network": states["released_before_network"],
                    "blocked": states["blocked"]}


# ===========================================================================
# V2-C4.5 — one sweep, several authorised accounts
# ===========================================================================
# SWEEP_PAID_MULTI_ACCOUNT (default OFF, and only under SWEEP_PAID_CONCURRENCY).
# Off, _require_token picks one account for the whole sweep, as it always has.
# On, every configured account is read once, before any start, and each
# provider-bounded search is ASSIGNED to one of them — the whole plan, exactly,
# before the first start — so a sweep whose bounded exposure no single account
# can hold may still run. Every start must then fit, in full, in BOTH the
# sweep's PaidExposure (the one budget, unchanged) and its own account's, and
# its account must have the memory and a run slot for it. The only difference
# at the provider is whose token a start carries. See
# docs/search-engine-v2-c45-multi-account-paid-execution.md.
PAID_MULTI_ACCOUNT_FLAG = "SWEEP_PAID_MULTI_ACCOUNT"
# Kept back from each account's own headroom for the platform usage that lands
# on it beside its runs' charges (dataset and API reads): measured at
# $0.000009-0.000039 per probe of one or two runs across V2-C1..C3.5, so about
# $0.00002 a run. A cent is more than 100x what one account running its share
# of a 90-search plan leaves. Never folded into an actor's ceiling.
ACCOUNT_BUFFER_USD = Decimal("0.01")
POOL_RECORD = "paid_account_ledger.json"
# A run in one of these holds neither memory nor a run slot at the provider.
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"})
_POOL_SLOT_RE = re.compile(r"APIFY_TOKEN(_[1-9][0-9]*)?")
_MILL = Decimal("0.001")
# How a provider says no to a start for the account's reasons, not the run's.
_ACCOUNT_REFUSALS = (
    ("ACCOUNT_CREDIT", re.compile(r"usage hard limit|monthly usage|not enough credit"
                                  r"|insufficient (?:credit|funds)", re.I)),
    ("ACCOUNT_RESOURCE", re.compile(r"memory limit|concurrent|too many (?:running|runs)",
                                    re.I)))


def paid_multi_account(env=None):
    return (os.environ if env is None else env).get(
        PAID_MULTI_ACCOUNT_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


# Developer-only (V2-C4.5's live canary): the most ONE pooled account may be
# given in this sweep. It clamps the pool's own usable capacity and nothing
# else — not the provider's reading, not the sweep's budget, not a ceiling —
# and can only lower it. Read only under the pool; unset, it does not exist.
PAID_ACCOUNT_CAP_ENV = "SWEEP_PAID_ACCOUNT_CAP_USD"


def paid_account_cap(env=None):
    """The clamp as a Decimal, or None when unset or empty. Any other value
    that is not a positive finite amount refuses the paid phase: a clamp
    someone set and mistyped must not silently become no clamp."""
    env = os.environ if env is None else env
    raw = (env.get(PAID_ACCOUNT_CAP_ENV) or "").strip()
    if not raw:
        return None
    try:
        cap = Decimal(raw)
    except InvalidOperation:
        cap = None
    if cap is None or not cap.is_finite() or cap <= 0:
        sys.exit(f"Refusing to start: {PAID_ACCOUNT_CAP_ENV}={raw!r} is not a positive "
                 f"dollar amount. Unset it to use each account's real capacity.")
    return cap


def pool_tokens(env=None):
    """apify_tokens(), narrowed to APIFY_TOKEN and APIFY_TOKEN_<n> (n >= 1, no
    leading zero): the pool spends on every account it is given, so a name
    that is not a slot is not one. apify_tokens() itself is untouched — the
    single-account path, the rescore tool and the UI read it as before."""
    return [(n, t) for n, t in apify_tokens(env) if _POOL_SLOT_RE.fullmatch(n)]


# V2-D1: where a sweep's keys come from. Two sources feed the one pool (and
# _require_token), and they never mix:
#   developer  APIFY_TOKEN, APIFY_TOKEN_<n>, from the environment and .env
#   public     a visitor's own keys (BYOK), piped to this process's stdin by
#              deploy/sweep_worker.py, which sets SWEEP_BYOK_CREDENTIALS=stdin
# A BYOK run never reads .env or an APIFY_TOKEN* variable — a key that happens
# to be on the box must not join a visitor's pool, or pay for it — and never
# the developer clamp. Its keys are in no variable, argument or file.
BYOK_ENV = "SWEEP_BYOK_CREDENTIALS"
BYOK_MAX_KEYS = 20          # deploy/sweep_worker.MAX_KEYS: the same limit
_byok = None                # the keys, once read
# V2-D closeout. A visitor's keys are spent ONLY through the pool's
# authorization — one key is a pool of one account — whatever the concurrency
# and multi-account flags say; no configuration reaches the credit-unaware
# single-account engines with them. This is the one lever above that: off, a
# BYOK run starts no paid search at all. On unless switched off, and any value
# but a clear yes (a typo included) is off: the failure direction is "no spend".
PUBLIC_PAID_FLAG = "SWEEP_PUBLIC_PAID"


def public_paid_enabled(env=None):
    raw = ((os.environ if env is None else env).get(PUBLIC_PAID_FLAG) or "").strip().lower()
    return raw in ("", "1", "true", "yes", "on")


def public_paid_mode(env=None):
    """How a BYOK run would spend, for the dry run and the public screens:
    "multi" (every connected account), "single" (the one with the most
    usable capacity), or "off" (no paid search). Never a credit-unaware mode:
    there is none for a visitor's keys."""
    if not public_paid_enabled(env):
        return "off"
    return "multi" if paid_multi_account(env) else "single"


def byok_tokens(stream=None):
    """The visitor's keys, in the order they added them, or None when this is
    not a BYOK run. Read once; anything malformed refuses the paid phase
    without echoing what was sent."""
    global _byok
    if (os.environ.get(BYOK_ENV) or "").strip() != "stdin":
        return None
    if _byok is None:
        try:
            keys = json.load(stream or sys.stdin)["apify_tokens"]
            ok = (isinstance(keys, list) and 0 < len(keys) <= BYOK_MAX_KEYS
                  and all(isinstance(k, str) and k.strip() for k in keys))
        except Exception:
            ok = False
        if not ok:
            sys.exit("Refusing to start: the visitor's Apify keys could not be read.")
        _byok = list(dict.fromkeys(k.strip() for k in keys))
    return list(_byok)


def _require_token_pool():
    """The pool's credential step, as _require_token is the single account's —
    the developer guard wraps both, so an unauthorised developer run reaches
    neither. Every configured slot; which account runs what is decided on
    facts read afterwards, never on a variable's name. A BYOK run's slots are
    key_1, key_2, ... in the order the visitor added them."""
    byok = byok_tokens()
    if byok is not None:
        return [(f"key_{i}", token) for i, token in enumerate(byok, 1)]
    from dotenv import load_dotenv
    load_dotenv()
    tokens = pool_tokens()
    if not tokens:
        sys.exit("APIFY_TOKEN not found. Add it to a .env file in this folder.")
    return tokens


class PoolAccount:
    """One underlying Apify account, however many slots hold a key to it.

    Its money is a PaidExposure of its own, the C2 ledger, whose budget is the
    headroom read before the sweep less ACCOUNT_BUFFER_USD: a snapshot, never
    replenished — not by a run that cost less than its ceiling, a $0 reading or
    a lagging account delta. Its memory and run slots are the other half: held
    while a run lives and freed once the provider reports the run terminal.
    The token is held here and handed only to that account's clients; repr()
    and snapshot() carry the label.

    Three figures, kept apart: `headroom`, the provider's reading;
    `real_capacity`, that less the buffer; and the ledger's budget, the
    EFFECTIVE capacity — the real one, or less where the developer-only
    SWEEP_PAID_ACCOUNT_CAP_USD clamps it (`cap`). The allocator and the
    account's ledger use the effective one; the readings are never rewritten."""

    def __init__(self, label, slots, token, reading, make_client, cap=None):
        self.label, self.slots, self._token = label, list(slots), token
        self.plan = reading["plan"]
        self.headroom = reading["headroom_usd"]
        self.baseline = reading["used_usd"]
        self.memory_mb, self.run_slots = reading["memory_mb"], reading["run_slots"]
        self.real_capacity = usable_capacity(self.headroom)
        self.cap = cap
        self.exposure = PaidExposure(self.real_capacity if cap is None
                                     else min(self.real_capacity, cap))
        self.reserved_mb = self.in_flight = self.live = self.held_unknown = 0
        self.peak_mb = self.peak_in_flight = self.starts = self.failures = 0
        self.spent = 0.0
        self._make_client = make_client
        self.read_client = make_client(token)   # the coordinator's, for account reads

    def __repr__(self):
        return f"PoolAccount({self.label})"

    def client(self):
        """A new client per search on this account, as C2 builds them."""
        return self._make_client(self._token)


def usable_capacity(headroom):
    """An account's real usable capacity: its headroom less ACCOUNT_BUFFER_USD,
    never below zero. The one formula the pool and the public screens share."""
    return max(Decimal(0), Decimal(headroom) - ACCOUNT_BUFFER_USD)


def _account_reading(me, limits):
    """What one account can hold, from users/me and users/me/limits. Headroom
    is the SMALLER of the hard monthly limit and the plan's included credit,
    less this cycle's usage, floored to $0.001: never spending into overage
    the account has not prepaid, and never more than its own limit allows. A
    limit that cannot be read is no headroom and no capacity."""
    lim, cur = limits.get("limits") or {}, limits.get("current") or {}
    plan = getattr(me, "plan", None)
    caps = [Decimal(str(c)) for c in (lim.get("max_monthly_usage_usd"),
                                      getattr(plan, "monthly_usage_credits_usd", None))
            if c is not None]
    used = Decimal(str(cur.get("monthly_usage_usd") or 0))
    headroom = max(Decimal(0), min(caps) - used) if caps else Decimal(0)

    def free(limit, in_use, scale=1):
        return (0 if lim.get(limit) is None or cur.get(in_use) is None
                else max(0, int((lim[limit] - cur[in_use]) * scale)))
    return {"plan": getattr(plan, "id", None),
            "headroom_usd": headroom.quantize(_MILL, rounding=ROUND_FLOOR),
            "used_usd": float(used),
            "memory_mb": free("max_actor_memory_gbytes", "actor_memory_gbytes", 1024),
            "run_slots": free("max_concurrent_actor_jobs", "active_actor_job_count")}


def discover_accounts(tokens, make_client, cap=None):
    """([PoolAccount] in slot order, one per underlying account, [excluded]).

    Two free reads per slot: users/me says WHO — a token is not an account,
    and two slots holding keys to one account are one balance, one memory
    limit and one set of run slots — and users/me/limits says how much. A slot
    whose account cannot be identified is left out: counted once too often it
    would double a balance. Where two slots are one account, the smaller of
    their readings stands. Labels are account_000, account_001, ... in slot
    order; no account id, username or email is kept. `cap`: the
    developer-only clamp (paid_account_cap), applied to each account's
    effective capacity only."""
    found, excluded = {}, []
    for slot, token in tokens:
        try:
            client = make_client(token)
            me = client.user().get()
            reading = _account_reading(me, client.user().limits().model_dump())
            ident = getattr(me, "id", None)
        except Exception as exc:        # the type only: a message can quote a request
            excluded.append({"slot": slot, "reason": f"unreadable ({type(exc).__name__})"})
            continue
        if not ident:
            excluded.append({"slot": slot, "reason": "no account id"})
        elif ident in found:
            first = found[ident]
            first["slots"].append(slot)
            excluded.append({"slot": slot, "reason": f"same account as {first['slots'][0]}"})
            first["reading"] = dict(first["reading"], **{
                k: min(first["reading"][k], reading[k])
                for k in ("headroom_usd", "memory_mb", "run_slots")})
        else:
            found[ident] = {"slots": [slot], "token": token, "reading": reading}
    accounts = [PoolAccount(f"account_{i:03d}", f["slots"], f["token"], f["reading"],
                            make_client, cap)
                for i, f in enumerate(found.values())]
    return accounts, excluded


def _placements(cap, sizes, limits):
    """Every tuple of counts of `sizes` (each within its limit) that fits `cap`."""
    if not sizes:
        yield ()
        return
    for n in range(min(limits[0], cap // sizes[0]) + 1):
        for rest in _placements(cap - n * sizes[0], sizes[1:], limits[1:]):
            yield (n,) + rest


def _suffix_tables(caps, sizes, totals):
    """tables[k]: {counts of every size but the smallest, placed on accounts
    k.. : the most of the smallest size that can go beside them}. Exact —
    every split over those accounts is tried, and for a fixed split of the
    larger sizes, as many of the smallest as fit is never worse — so a missing
    key or a short count is a proof that no placement exists."""
    big, small = sizes[:-1], sizes[-1]
    tables = [None] * len(caps) + [{(0,) * len(big): 0}]
    for k in range(len(caps) - 1, -1, -1):
        table = {}
        for key, have in tables[k + 1].items():
            room = [t - h for t, h in zip(totals, key)]
            for y in _placements(caps[k], big, room):
                left = caps[k] - sum(a * b for a, b in zip(y, big))
                new = tuple(a + b for a, b in zip(key, y))
                n = min(totals[-1], have + left // small)
                if table.get(new, -1) < n:
                    table[new] = n
        tables[k] = table
    return tables


def place_units(units, accounts):
    """The allocator. units: [(unit_id, ceiling)] in plan order, every ceiling
    a whole number of mills; accounts: [(label, capacity)]. Returns
    {unit_id: label} for the LONGEST PREFIX of the plan that can be placed
    whole, each unit wholly on one account — a bin-packing question, not "does
    the sum fit": two accounts with $0.10 each hold no $0.135 search.

    Deterministic and exact: accounts are filled smallest first (best fit —
    a nearly spent account takes the unit it can still hold, and the large
    ones stay large), each as full as it can be while what is left can still
    be placed on the rest, which _suffix_tables proves. Within one size, units
    take accounts in that fill order, in plan order."""
    if not units or not accounts:
        return {}
    mills = [int((c / _MILL).to_integral_value(rounding=ROUND_CEILING)) for _, c in units]
    order = sorted(range(len(accounts)), key=lambda i: (accounts[i][1], i))
    caps = [int((accounts[i][1] / _MILL).to_integral_value(rounding=ROUND_FLOOR))
            for i in order]
    sizes = sorted(set(mills), reverse=True)

    def counts(n):
        c = Counter(mills[:n])
        return [c[s] for s in sizes]

    def feasible(n):
        totals = counts(n)
        return _suffix_tables(caps, sizes, totals)[0].get(tuple(totals[:-1]), -1) >= totals[-1]

    lo, hi = 0, len(units)                  # placeable prefixes are downward-closed
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if feasible(mid) else (lo, mid - 1)
    if lo == 0:
        return {}
    remaining = counts(lo)
    tables = _suffix_tables(caps, sizes, remaining)
    quota = []
    for k, cap in enumerate(caps):
        best = None
        for y in _placements(cap, sizes[:-1], remaining[:-1]):
            left = cap - sum(a * b for a, b in zip(y, sizes))
            x = min(remaining[-1], left // sizes[-1])
            rest = tuple(r - a for r, a in zip(remaining[:-1], y))
            if tables[k + 1].get(rest, -1) >= remaining[-1] - x:
                score = (cap - left + x * sizes[-1], y)
                if best is None or score > best[0]:
                    best = (score, y + (x,))
        quota.append(dict(zip(sizes, best[1])))
        remaining = [r - a for r, a in zip(remaining, best[1])]
    out = {}
    for (unit_id, _), size in zip(units[:lo], mills[:lo]):
        k = next(k for k, q in enumerate(quota) if q.get(size))
        quota[k][size] -= 1
        out[unit_id] = accounts[order[k]][0]
    return out


def placeable_prefix(units, placed, budget=None):
    """V2-D1: how many of `units` — [(unit_id, ceiling)] in plan order, ceiling
    None where the provider has none — can run before the first that cannot:
    one with no ceiling, one not in `placed` (the unit ids with an account able
    to run them), or one whose ceiling would take the plan's held total past
    `budget`. A partial sweep is exactly that prefix: a later search never runs
    because it happens to fit where an earlier one did not."""
    budget = None if budget is None else Decimal(str(budget))
    held = Decimal(0)
    for k, (unit_id, ceiling) in enumerate(units):
        if (ceiling is None or unit_id not in placed
                or (budget is not None and held + ceiling > budget)):
            return k
        held += ceiling
    return len(units)


def plan_estimate(entries):
    """The estimate for exactly these searches, by sweep/plan.cost — the pricing
    contract the screens quote — never a pro-rata share of a larger plan's."""
    from sweep import plan as plan_mod
    sites = {}
    for e in entries:
        sites.setdefault(e.site_key, []).append(e.search)
    raw = {"profile": config.PROFILE or "", "sites": sites,
           "max_results": {s: effective_search(s, q[0])["max_results"]
                           for s, q in sites.items()}}
    return plan_mod.cost(raw, config.SITE_RATES, config.SITE_RATE_BASIS)["total"]


def project_assignment(units, accounts):
    """The pool's plan for `units` ([(unit_id, provider, ceiling)], bounded,
    plan order) over `accounts` ([PoolAccount]): ({unit_id: account}, the
    report a preflight prints). Money only — memory and run slots are decided
    as the searches go, and never move a search to another account."""
    by_label = {a.label: a for a in accounts}
    placed = place_units([(u, c) for u, _, c in units],
                         [(a.label, a.exposure.budget) for a in accounts])
    # Stranded: what an account has left that no UNPLACED search could use.
    # With the whole plan placed nothing is stranded; what is left is unused.
    smallest = min((c for u, _, c in units if u not in placed), default=None)
    rows = []
    for a in accounts:
        mine = [(p, c) for u, p, c in units if placed.get(u) == a.label]
        left = a.exposure.budget - sum((c for _, c in mine), Decimal(0))
        rows.append({"account": a.label, "slots": a.slots, "plan": a.plan,
                     "headroom_usd": str(a.headroom),               # the provider's
                     "real_capacity_usd": str(a.real_capacity),     # less the buffer
                     "effective_capacity_usd": str(a.exposure.budget),   # what is used
                     "developer_clamped": a.exposure.budget < a.real_capacity,
                     "projected_units": dict(Counter(p for p, _ in mine)),
                     "projected_usd": str(sum((c for _, c in mine), Decimal(0))),
                     "leftover_usd": str(left),
                     "stranded_usd": str(left if smallest is not None and left < smallest
                                         else Decimal(0)),
                     "memory_mb": a.memory_mb, "run_slots": a.run_slots})
    cap = next((a.cap for a in accounts if a.cap is not None), None)
    report = {"account_count": len(accounts),
              "buffer_per_account_usd": str(ACCOUNT_BUFFER_USD),
              "developer_account_cap": None if cap is None else {
                  "usd": str(cap),
                  "what": f"{PAID_ACCOUNT_CAP_ENV}: a developer-only clamp imposed "
                          f"locally on each account's usable capacity for this sweep; "
                          f"not provider capacity and not the sweep's budget"},
              "aggregate_headroom_usd": str(sum((a.headroom for a in accounts), Decimal(0))),
              "aggregate_real_capacity_usd": str(sum((a.real_capacity for a in accounts),
                                                     Decimal(0))),
              "aggregate_effective_capacity_usd": str(sum(
                  (a.exposure.budget for a in accounts), Decimal(0))),
              "bounded_units": len(units),
              "bounded_exposure_usd": str(sum((c for _, _, c in units), Decimal(0))),
              "placed_units": len(placed),
              "allocation_feasible": len(placed) == len(units),
              "stranded_usd": str(sum((Decimal(r["stranded_usd"]) for r in rows),
                                      Decimal(0))),
              "accounts": rows}
    return {u: by_label[label] for u, label in placed.items()}, report


def actor_memory_mb(client, actor_id):
    """The memory a start gets when it names none — Sweep never names one, so
    this is what each run holds on its account. One free read of the actor's
    record; None when unreadable, which the pool treats as the whole account."""
    try:
        return int(client.actor(actor_id).get().default_run_options.memory_mbytes)
    except Exception:
        return None


class AccountLedger:
    """paid_account_ledger.json beside the outputs: per unit, the account it
    was assigned to, its ceiling, the reservation state, and — once they
    exist — the run id and the status the provider ended it on. Written whole
    (temp, fsync, rename) at every transition, and "committed" BEFORE the start
    request, so a crash can leave a hold for a request never sent (safe) but
    never forget one that may have spent. No token, no query."""

    def __init__(self, path):
        self.path, self._lock = path, threading.Lock()
        self.doc = {"schema": "search-v2c45.1", "started_at": _utc_now(),
                    "finished": False, "units": {}}

    @classmethod
    def open(cls, output_dir):
        """The ledger for this sweep's paid phase. An earlier one that did not
        finish with any start committed STOPS the sweep: its ceilings may
        still be charged on accounts this sweep would read as having headroom,
        and which search was bought where is the operator's to reconcile, not
        a resume's to guess. Anything else is kept, renamed, never lost."""
        path = os.path.join(output_dir, POOL_RECORD)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    old = json.load(fh)
            except (OSError, ValueError):
                old = None
            held = [u for u, v in ((old or {}).get("units") or {}).items()
                    if v.get("state") == "committed"]
            if old is None or (held and not old.get("finished")):
                sys.exit(f"Refusing to start: {path} records "
                         f"{len(held) if old else 'unreadable'} committed paid start(s) "
                         f"from a paid phase that did not finish. Their ceilings may "
                         f"still be charged. Review it, then move it aside to run again.")
            os.replace(path, f"{path}.{datetime.now():%Y%m%d_%H%M%S}")
        return cls(path)

    def set(self, unit_id, **fields):
        with self._lock:
            self.doc["units"].setdefault(unit_id, {}).update(fields)
            self._write()

    def finish(self):
        with self._lock:
            self.doc.update(finished=True, finished_at=_utc_now())
            self._write()

    def _write(self):
        with _replaced(self.path, encoding="utf-8") as fh:
            json.dump(self.doc, fh, indent=1)


class _PooledHolds:
    """One pooled search's three holds — the sweep's ceiling, its account's
    ceiling, its account's memory and run slot — behind the commit/release
    interface _paid_worker already calls on a PaidExposure."""

    def __init__(self, pool, exposure, acct, unit_id, mb):
        self.pool, self.exposure, self.acct = pool, exposure, acct
        self.unit_id, self.mb = unit_id, mb
        self.attempted = self.freed = False
        self.run_id = self.status = None

    def commit(self, unit_id):
        """Both ceilings committed, then persisted, then the start. If the
        write fails, the start is never sent and both holds stay (safe)."""
        self.exposure.commit(unit_id)
        self.acct.exposure.commit(unit_id)
        self.pool.ledger.set(unit_id, state="committed", committed_at=_utc_now())
        self.attempted = True

    def started(self, run_id):
        self.run_id = run_id
        with contextlib.suppress(OSError):     # the run exists: never fail its poll
            self.pool.ledger.set(self.unit_id, run_id=run_id)

    def ended(self, status):
        self.status = status
        if status in TERMINAL_STATUSES:
            self.pool.free(self)
        with contextlib.suppress(OSError):
            self.pool.ledger.set(self.unit_id, provider_status=status)

    def release(self, unit_id):
        """The worker is done. Pending holds go back only if the start was
        never attempted; memory and slot then too. A run whose end the
        provider never confirmed keeps them for the rest of the sweep."""
        self.exposure.release(unit_id)
        self.acct.exposure.release(unit_id)
        if not self.attempted:
            self.pool.free(self)
            with contextlib.suppress(OSError):
                self.pool.ledger.set(unit_id, state="released_before_network")
        self.pool.done(self)

    def hooks(self):
        return {"after_start": self.started, "after_run": self.ended}


class AccountPool:
    """The accounts a C2 coordinator may assign searches to, the plan's
    assignment, and the runtime capacity each account has left. The
    coordinator alone assigns and reserves; a worker gets its account's client
    and a _PooledHolds, never a choice."""

    def __init__(self, accounts, excluded, ledger):
        self.accounts, self.excluded, self.ledger = accounts, excluded, ledger
        self.assignment, self.memory, self.report = {}, {}, None
        self.dispatched = []
        self.authorization, self.runnable, self.skipped = None, None, []
        self.stop_reason = None
        self.single = False
        self._lock = threading.Lock()

    @classmethod
    def open(cls, output_dir, make_client, single=False):
        """`single` (a visitor's run with SWEEP_PAID_MULTI_ACCOUNT off): every
        key is read, and only the ONE account with the most usable capacity
        (the first added, on a tie) is pooled — never their capacities
        combined, and still every ceiling placed on it before the first start."""
        # A bad clamp stops it here. Never on a visitor's keys (V2-D1): their
        # capacity is what their own accounts report, whatever this box's env.
        cap = None if byok_tokens() is not None else paid_account_cap()
        ledger = AccountLedger.open(output_dir)      # before any credential
        accounts, excluded = discover_accounts(_require_token_pool(), make_client, cap)
        if single and len(accounts) > 1:
            keep = max(accounts, key=lambda a: (a.exposure.budget, -accounts.index(a)))
            excluded += [{"slot": a.slots[0], "reason": "not used: one account per sweep "
                                                         f"({PAID_MULTI_ACCOUNT_FLAG} off)"}
                         for a in accounts if a is not keep]
            accounts = [keep]
        pool = cls(accounts, excluded, ledger)
        pool.single = single
        if cap is not None:
            print(f"  ({PAID_ACCOUNT_CAP_ENV}={cap}: developer-only clamp on each "
                  f"account's usable capacity, not provider capacity)")
        for a in accounts:
            print(f"  {a.label}  {', '.join(a.slots):<30} ${a.headroom} headroom, "
                  f"${a.real_capacity} usable"
                  + (f" (${a.exposure.budget} under the clamp)"
                     if a.exposure.budget < a.real_capacity else "")
                  + f", {a.memory_mb} MB, {a.run_slots} run slot(s)")
        for x in excluded:
            print(f"  {x['slot']:<16} not pooled: {x['reason']}")
        return pool

    def plan(self, site_entries, done):
        """Assign every bounded search not already done, in plan order, before
        the first start; read each provider's run memory once."""
        units = [(e.unit_id, e.site_key, e.ceiling) for entries in site_entries.values()
                 for e in entries if e.ceiling is not None and e.combo_key not in done]
        self.assignment, self.report = project_assignment(units, self.accounts)
        for site in site_entries:
            self.memory[site] = (actor_memory_mb(self.accounts[0].read_client,
                                                 SITES[site]["actor"])
                                 if self.accounts else None)
        for u, site, ceiling in units:
            acct = self.assignment.get(u)
            self.ledger.set(u, provider=site, ceiling_usd=str(ceiling),
                            account=acct.label if acct else None,
                            state="assigned" if acct else "unassigned")
        r = self.report
        print(f"  pool: {r['placed_units']}/{r['bounded_units']} bounded search(es) "
              f"placed, ${r['bounded_exposure_usd']} of ceilings on "
              f"${r['aggregate_effective_capacity_usd']} usable across "
              f"{r['account_count']} "
              f"account(s) (not a provider guarantee)")

    def _mb(self, e, acct):
        mb = self.memory.get(e.site_key)
        return acct.memory_mb if mb is None else mb

    def authorize(self, site_entries, done, budget, partial):
        """V2-D1: may this plan run, and how much of it. Decided on the readings
        this sweep took just now (open); whatever a screen showed earlier is
        never consulted. FULL, the default, needs every search not already done
        bounded, placed on an account that can run it, and inside the sweep's
        budget — short of that NOTHING starts. PARTIAL, only when the user
        chose it, runs the longest prefix that can, in plan order. A prefix of
        nothing is refused either way: there is nothing to run."""
        todo = [e for entries in site_entries.values() for e in entries
                if e.combo_key not in done]
        can_run = {e.unit_id for e in todo if e.unit_id in self.assignment
                   and self.assignment[e.unit_id].run_slots >= 1
                   and self._mb(e, self.assignment[e.unit_id])
                   <= self.assignment[e.unit_id].memory_mb}
        k = placeable_prefix([(e.unit_id, e.ceiling) for e in todo], can_run, budget)
        full = k == len(todo)
        outcome = "full" if full else ("partial" if partial and k else "refused")
        self.runnable = {e.unit_id for e in todo[:k]} if outcome != "refused" else set()
        self.skipped = [e.unit_id for e in todo[len(self.runnable):]]
        # Why the prefix ends where it does: the accounts (an unplaced search,
        # or one its account can never run), a search with no ceiling to hold,
        # or the sweep's own budget.
        stop = None if full else todo[k]
        self.stop_reason = (None if stop is None else "unbounded" if stop.ceiling is None
                            else "insufficient_capacity" if stop.unit_id not in can_run
                            else "budget")
        self.authorization = {
            "full_plan_requested": not partial,
            "partial_authorized_by_user": bool(partial),
            "full_plan_placeable": full,
            "outcome": outcome,
            "stop_reason": self.stop_reason,
            "total_planned_paid_units": len(todo),
            "placeable_paid_units": k,
            "skipped_insufficient_capacity": (0 if self.stop_reason == "budget"
                                              else len(self.skipped)),
            "skipped_budget": len(self.skipped) if self.stop_reason == "budget" else 0,
            "total_planned_bounded_exposure_usd": str(sum(
                (e.ceiling for e in todo if e.ceiling is not None), Decimal(0))),
            "placeable_bounded_exposure_usd": str(sum(
                (e.ceiling for e in todo[:k]), Decimal(0))),
            "full_estimate_usd": plan_estimate(todo),
            "partial_estimate_usd": plan_estimate(todo[:k]),
            "budget_usd": None if budget is None else str(budget)}
        return self.authorization

    def verdict(self, e):
        """"go" | "wait" (its account's earlier run still holds what it needs)
        | the reason it can never start on its account in this sweep."""
        if e.ceiling is None:
            return "unbounded"
        acct = self.assignment.get(e.unit_id)
        if acct is None:
            return "unassigned"
        mb = self._mb(e, acct)
        with self._lock:
            if (acct.reserved_mb + mb <= acct.memory_mb
                    and acct.in_flight + 1 <= acct.run_slots):
                return "go"
            return "wait" if acct.live else "account_resources"

    def hold(self, e, exposure):
        """Its account's ceiling and runtime capacity, after the sweep's own
        ceiling is held. None if the account cannot hold the ceiling."""
        acct = self.assignment[e.unit_id]
        if not acct.exposure.reserve(e.unit_id, e.ceiling):
            return None
        mb = self._mb(e, acct)
        with self._lock:
            acct.reserved_mb += mb
            acct.in_flight += 1
            acct.live += 1
            acct.starts += 1
            acct.peak_mb = max(acct.peak_mb, acct.reserved_mb)
            acct.peak_in_flight = max(acct.peak_in_flight, acct.in_flight)
            self.dispatched.append(acct.label)
        self.ledger.set(e.unit_id, state="pending", reserved_at=_utc_now())
        return _PooledHolds(self, exposure, acct, e.unit_id, mb)

    def free(self, holds):
        with self._lock:
            if not holds.freed:
                holds.freed = True
                holds.acct.reserved_mb -= holds.mb
                holds.acct.in_flight -= 1

    def done(self, holds):
        with self._lock:
            holds.acct.live -= 1
            if not holds.freed:
                holds.acct.held_unknown += 1

    def refusal(self, holds, error):
        """ACCOUNT_CREDIT / ACCOUNT_RESOURCE when the provider refused the
        START for the account's reasons (attempted, no run), else None."""
        if holds is None or error is None or not holds.attempted or holds.run_id:
            return None
        return next((kind for kind, pattern in _ACCOUNT_REFUSALS
                     if pattern.search(str(error))), None)

    def read(self, unit_id):
        return account_usage_usd(self.assignment[unit_id].read_client)

    def settle(self, unit_id, actual, cost):
        """The pre-C2 guard's figure, per account from that account's own
        reading (never one account's delta derived from another's), summed.
        It only ever enlarges an account's view, as C2's observe does."""
        acct = self.assignment[unit_id]
        acct.spent = (actual - acct.baseline if actual is not None else acct.spent + cost)
        acct.exposure.observe(acct.spent)
        return sum(a.spent for a in self.accounts)

    def snapshot(self):
        with self._lock:
            used = [a for a in self.accounts if a.starts]
            per = []
            for a, row in zip(self.accounts, (self.report or {}).get("accounts") or
                              [{}] * len(self.accounts)):
                held = a.exposure.snapshot()
                per.append(dict(row, account=a.label, slots=a.slots,
                                committed_usd=held["committed_usd"],
                                pending_peak_usd=held["pending_peak_usd"],
                                remaining_usd=str(a.exposure.budget - a.exposure.committed
                                                  - a.exposure.pending),
                                starts=a.starts, failures=a.failures,
                                peak_in_flight=a.peak_in_flight, peak_memory_mb=a.peak_mb,
                                runtime_held_unknown=a.held_unknown))
            return dict({k: v for k, v in (self.report or {}).items() if k != "accounts"},
                        single_account=self.single,
                        actor_memory_mb=dict(self.memory), excluded_slots=self.excluded,
                        accounts_used=len(used),
                        account_switches=sum(1 for a, b in zip(self.dispatched,
                                                                self.dispatched[1:])
                                             if a != b),
                        accounts=per)


PaidEntry = namedtuple("PaidEntry", "n i of unit_id site_key actor_id search "
                                    "label combo_key ceiling")
PaidResult = namedtuple("PaidResult", "n rows cost error record opened finished sdk")


def _sdk_counts(client):
    """apify-client's own per-client counters: API calls made, HTTP requests
    sent (requests - calls = the SDK's own retries) and HTTP 429s it retried
    through — the only view of those retries from outside it. A private
    attribute, so read defensively."""
    try:
        stats = client._statistics
        return {"calls": int(stats.calls), "requests": int(stats.requests),
                "rate_limit_errors": int(sum(stats.rate_limit_errors.values()))}
    except Exception:
        return None


def _paid_worker(entry, make_client, exposure, results, hooks=None):
    """One paid search on its own thread: the provider's half, nothing else.

    It builds its own client — apify-client 3.1 keeps unsynchronised request
    counters and creates its HTTP client lazily, so one instance is never
    shared between threads — commits its reservation the instant before the
    start request, and hands back an isolated result. It never touches
    raw_rows, an output file, the done ledger, LAST_STATS, the coordinator's
    spend figures or any shared telemetry: its unit is its own, deferred, until
    the coordinator attaches it in plan order.

    V2-C4.5: under the account pool, `make_client` is its ASSIGNED account's,
    `exposure` holds both ceilings and that account's runtime capacity, and
    `hooks` tell it when the run exists and when it ended. It chooses nothing.
    """
    opened = time.monotonic()
    rows = cost = error = record = sdk = None
    try:
        eff = effective_search(entry.site_key, entry.search)
        with telemetry.unit("paid", entry.site_key, board=entry.actor_id,
                            query=entry.search.get("keywords") or "(all)",
                            location=entry.search.get("location") or "",
                            country=SEARCH.get("country", ""),
                            requested_limit=eff["max_results"], defer=True) as record:
            telemetry.paid_run(unit_id=entry.unit_id)
            client = make_client()
            try:
                rows, cost = scrape_search(
                    client, entry.site_key, entry.actor_id, entry.search,
                    before_start=lambda: exposure.commit(entry.unit_id), **(hooks or {}))
            finally:
                sdk = _sdk_counts(client)
    except BaseException as exc:        # the coordinator decides what it means
        error = exc
    finally:
        exposure.release(entry.unit_id)     # a no-op once the start was attempted
        results.put(PaidResult(entry.n, rows, cost, error, record, opened,
                               time.monotonic(), sdk))


class PaidPlanRefused(Exception):
    """V2-D1: the full plan cannot be safely placed on the connected accounts
    and a partial sweep was not chosen (or nothing at all can run). Raised
    before any start: nothing was sent, nothing can have been charged."""


# How main() exits on PaidPlanRefused, so the worker and the screens can tell
# "the credit no longer covers the full sweep" from a crash.
PAID_REFUSED_EXIT = 3
# The authorization outcome beside the outputs, for the worker to report: counts
# and amounts only, rewritten with what executed once the paid phase ends.
AUTH_RECORD = "paid_authorization.json"


def write_authorization(output_dir, record):
    with contextlib.suppress(OSError):
        with _replaced(os.path.join(output_dir, AUTH_RECORD), encoding="utf-8") as fh:
            json.dump(record, fh, indent=1)


def paid_phase_c2(plans, make_client, account_client, baseline, budget,
                  raw_rows, done, done_path, today, emit, failures, pool=None,
                  partial=False, workers=None):
    """The paid phase under SWEEP_PAID_CONCURRENCY. Returns (spent,
    stopped_early, site_key) as main()'s serial loop leaves them.

    Site by site, in plan order: a site whose provider enforces a charge
    ceiling runs up to paid_workers() searches at once; any other site runs one
    at a time, as it always has. Before a search may start, the coordinator —
    this thread, alone — reserves its full ceiling (PaidExposure.reserve) or,
    for an unbounded site, applies the old `spent >= budget` test to the same
    view (PaidExposure.admit). The first search that does not fit stops the
    paid phase, as the cap always has.

    Results are integrated in PLAN order, never finish order: a search that
    finished early waits in memory for every search before it. Integration is
    the serial loop's own per-search body — account read, rows, checkpoint,
    then the done marker — so the checkpoint-before-marker transaction, and
    which duplicate dedupe keeps, are exactly the serial ones.

    Worker threads are daemons, so an interrupt (the console stops a sweep
    with SIGINT) still ends the process at once, as it did serially: the runs
    already started go on at the provider, inside their ceilings.

    V2-C4.5, `pool` (SWEEP_PAID_MULTI_ACCOUNT): every bounded search not yet
    done is assigned an account before the first start (AccountPool.plan).
    A search then starts only when its account has the memory and a run slot
    for it (else it WAITS for that account's earlier run — it never moves),
    the sweep's PaidExposure holds its ceiling, and so does its account's.
    Its account's reading is the one integrated. An unbounded or unplaced
    search, an account that can never fit it, or a provider refusing a start
    for the account's credit or resources stops the phase: no search is
    retried elsewhere. Nothing else — plan, inputs, order, bytes — differs.
    """
    # V2-D closeout: a visitor's run with SWEEP_PAID_CONCURRENCY off still goes
    # through this scheduler — at one worker, one search at a time, as the
    # serial loop would — so its accounts are authorised all the same.
    workers = paid_workers() if workers is None else workers
    exposure = PaidExposure(budget)
    results = queue.Queue()
    spent, stopped_early, site_key = 0.0, False, None
    peak = {"in_flight": 0, "buffered": 0}
    checkpoint = {"passes": 0, "wall_ms": 0, "cpu_ms": 0}
    visited, segments = [], []
    holds = {}                                  # unit_id -> _PooledHolds
    n = 0
    site_entries = {}
    for site_key, plan in plans.items():
        actor_id = SITES[site_key]["actor"]
        entries = site_entries[site_key] = []
        for i, search in enumerate(plan, 1):
            who = f" [{search['company']}]" if search.get("company") else ""
            depth = effective_search(site_key, search)["max_results"]
            entries.append(PaidEntry(
                n, i, len(plan), paid_unit_id(n), site_key, actor_id, search,
                f"{search['keywords'] or '(all)'}{who} @ {search['location']}",
                f"{today}|{site_key}|{search['keywords']}|{search['location']}|"
                f"{search.get('company') or ''}",
                max_charge_usd(site_key, depth)))
            n += 1
    site_key = None
    limit = None                    # V2-D1: a partial sweep's runnable units
    if pool is not None:
        pool.plan(site_entries, done)
        auth = pool.authorize(site_entries, done, budget, partial)
        write_authorization(os.path.dirname(pool.ledger.path), auth)
        print(f"  authorization: {auth['outcome']} — {auth['placeable_paid_units']} of "
              f"{auth['total_planned_paid_units']} paid search(es) can be safely placed")
        # Every search this authorization leaves out, labelled now: not run
        # for capacity (or the budget) — never failed, never done.
        for u in pool.skipped:
            telemetry.paid_status(u, "skipped_budget" if pool.stop_reason == "budget"
                                  else "skipped_insufficient_capacity")
        if auth["outcome"] == "refused":
            pool.ledger.doc["authorization"] = auth
            pool.ledger.finish()
            telemetry.paid_execution({"mode": "reservation_scheduler", "workers": workers,
                                      "authorization": auth, "accounts": pool.snapshot(),
                                      "units": []})
            covers = (f"{auth['placeable_paid_units']} of the "
                      f"{auth['total_planned_paid_units']} paid searches")
            raise PaidPlanRefused(
                (f"The spend cap ${budget:.2f} holds {covers}"
                 if pool.stop_reason == "budget" else
                 f"The connected Apify accounts can safely cover {covers} right now")
                + ", so " + ("nothing could run" if partial else
                             "the full sweep was not started")
                + " and nothing was charged."
                + ("" if pool.stop_reason == "budget" else
                   " Add another key" + ("." if partial else
                                         ", or choose to run with the available credit.")))
        if auth["outcome"] == "partial":
            limit = pool.runnable

    def integrate(e, r):
        nonlocal spent
        began = time.monotonic()
        seen = {"unit_id": e.unit_id, "provider": e.site_key,
                "bounded": e.ceiling is not None}
        if pool is not None:
            acct = pool.assignment.get(e.unit_id)
            seen["account"] = acct.label if acct else None
        if r is None:
            print(f"  [{e.i}/{e.of}] {e.label:<46} — skip (done)")
            telemetry.paid_status(e.unit_id, "skipped_done")
            visited.append(dict(seen, reservation="none"))
            return
        cpu_ms, ok = None, False
        with telemetry.resumed(r.record, r.opened):
            try:
                if r.error is not None:
                    raise r.error
                rows, cost = r.rows, r.cost
                reading = time.monotonic()
                if pool is None:
                    actual = (account_usage_usd(account_client)
                              if baseline is not None else None)
                else:
                    actual = pool.read(e.unit_id)
                read_ms = round((time.monotonic() - reading) * 1000)
                before = spent
                if pool is None:
                    spent = (actual - baseline if actual is not None
                             else spent + cost)
                else:
                    spent = pool.settle(e.unit_id, actual, cost)
                exposure.observe(spent, None if e.ceiling is not None
                                 else spent - before)
                telemetry.observed(raw=len(rows), normalized=len(rows),
                                   gated=len(rows))
                telemetry.paid_run(
                    reported_cost_usd=cost,
                    billed_delta_usd=round(spent - before, 6),
                    budget_view_usd=round(spent, 6),
                    budget_basis=("account_delta" if actual is not None
                                  else "run_record_sum"),
                    account_read_ms=(read_ms if baseline is not None or pool is not None
                                     else None))
                if actual is not None:
                    telemetry.cost_observation("account_usage_delta",
                                               spent - before)
                if telemetry.active():
                    for position, row in enumerate(rows, 1):
                        row[telemetry.PAID_UNIT] = (e.unit_id, position)
                raw_rows.extend(rows)
                writing, cpu = time.monotonic(), time.thread_time()
                emit(raw_rows)                                # checkpoint
                wall_ms = round((time.monotonic() - writing) * 1000)
                cpu_ms = round((time.thread_time() - cpu) * 1000)
                telemetry.paid_run(checkpoint_ms=wall_ms)
                checkpoint["passes"] += 1
                checkpoint["wall_ms"] += wall_ms
                checkpoint["cpu_ms"] += cpu_ms
                with open(done_path, "a") as fh:              # mark done
                    fh.write(e.combo_key + "\n")
                done.add(e.combo_key)
                print(f"  [{e.i}/{e.of}] {e.label:<46} {len(rows):>3} jobs  "
                      f"(${cost:.3f} actor, ${spent:.2f} billed)")
                telemetry.paid_status(e.unit_id, "completed")
                ok = True
            except Exception as exc:  # isolate failures per search
                telemetry.failed(exc)
                telemetry.paid_run(failure_type=type(exc).__name__)
                telemetry.paid_status(e.unit_id, "failed")
                failures.append((e.site_key, e.label, str(exc)))
                print(f"  [{e.i}/{e.of}] {e.label:<46} ! {exc}")
                if pool is not None:
                    pool.assignment[e.unit_id].failures += 1
            # V2-C4 (default off): what this search added, in plan order,
            # read off the checkpoint just made. Observes; decides nothing.
            paid_adaptive.integrated(e.unit_id, r.rows if ok else None, ok,
                                     first + sent, exposure.committed)
        telemetry.attach(r.record)
        held = exposure.units.get(e.unit_id, {})
        extra = {}
        if e.unit_id in holds:
            h = holds[e.unit_id]
            extra = {"provider_status": h.status, "runtime_freed": h.freed,
                     "account_refusal": pool.refusal(h, r.error)}
            with contextlib.suppress(OSError):
                pool.ledger.set(e.unit_id, integrated="completed" if ok else "failed")
        visited.append(dict(
            seen, reservation=held.get("state"),
            reserved_usd=None if held.get("usd") is None else str(held["usd"]),
            reserved_at=held.get("reserved_at"), committed_at=held.get("committed_at"),
            released_at=held.get("released_at"),
            execution_ms=round((r.finished - r.opened) * 1000),
            buffered_wait_ms=round((began - r.finished) * 1000),
            merge_ms=round((time.monotonic() - began) * 1000),
            checkpoint_cpu_ms=cpu_ms, sdk=r.sdk, **extra))

    for site_key, entries in site_entries.items():
        if stopped_early:
            break
        actor_id = SITES[site_key]["actor"]
        print(f"\n{site_key} ({actor_id})")
        bounded = all(e.ceiling is not None for e in entries)
        width = workers if bounded else 1
        first = entries[0].n
        head = sent = running = 0       # next to integrate, next to send, in flight
        waiting, open_keys, refused = {}, set(), None
        refusal = halted = None         # V2-C4.5: why the pool stopped it
        began = time.monotonic()
        # One step per turn, in this priority: integrate the head of the plan
        # if it is ready; else send the next search if a worker is free; else,
        # once everything sent is integrated, stop; else wait for any result.
        while True:
            if head < sent and head in waiting:
                integrate(entries[head], waiting.pop(head))
                open_keys.discard(entries[head].combo_key)
                head += 1
                continue
            e = entries[sent] if sent < len(entries) else None
            # A repeat of a search still in flight waits for it: the serial
            # loop would decide it against .done_combos after its twin.
            if (refused is None and halted is None and e is not None
                    and e.combo_key not in open_keys):
                if e.combo_key in done:
                    waiting[sent] = None                     # integrated in order
                    sent += 1
                    continue
                if limit is not None and e.unit_id not in limit:
                    refused, refusal = e, "insufficient_capacity"
                    continue
                if running < width:
                    verdict = "go" if pool is None else pool.verdict(e)
                    if verdict not in ("go", "wait"):
                        refused, refusal = e, verdict
                        continue
                    if verdict == "go":
                        if not (exposure.reserve(e.unit_id, e.ceiling)
                                if e.ceiling is not None else exposure.admit(e.unit_id)):
                            refused = e
                            continue
                        held_by, client_for, hooks = exposure, make_client, None
                        if pool is not None:
                            held_by = pool.hold(e, exposure)
                            if held_by is None:
                                exposure.release(e.unit_id)
                                refused, refusal = e, "account_capacity"
                                continue
                            holds[e.unit_id] = held_by
                            client_for, hooks = held_by.acct.client, held_by.hooks()
                        open_keys.add(e.combo_key)
                        # Single-account: the same four arguments as ever.
                        threading.Thread(target=_paid_worker,
                                         args=(e, client_for, held_by, results)
                                         + ((hooks,) if hooks else ()),
                                         name=f"sweep-paid-{e.unit_id}",
                                         daemon=True).start()
                        running += 1
                        peak["in_flight"] = max(peak["in_flight"], running)
                        sent += 1
                        continue
            if head == sent:
                break
            r = results.get()
            running -= 1
            waiting[r.n - first] = r
            if pool is not None and halted is None:
                kind = pool.refusal(holds.get(entries[r.n - first].unit_id), r.error)
                if kind:
                    halted = (entries[r.n - first], kind)
            peak["buffered"] = max(peak["buffered"], sum(
                1 for k, v in waiting.items() if v is not None and k != head))
        segments.append({"provider": site_key, "bounded": bounded, "workers": width,
                         "searches": len(entries), "sent": sent,
                         "wall_ms": round((time.monotonic() - began) * 1000)})
        if halted is not None:
            unit, kind = halted
            print(f"  ⚠ {kind}: the provider refused {unit.unit_id}'s start on "
                  f"{pool.assignment[unit.unit_id].label} — stopping; its ceiling "
                  f"stays held and it is not retried elsewhere.")
            telemetry.note(f"V2-C4.5: {kind} refusal on {unit.unit_id} stopped the "
                           f"paid phase; nothing was retried on another account.")
            telemetry.paid_unvisited("skipped_account")
            stopped_early = True
        if refused is not None and refusal == "insufficient_capacity":
            visited.append({"unit_id": refused.unit_id, "provider": site_key,
                            "bounded": refused.ceiling is not None,
                            "reservation": "none", "reason": pool.stop_reason})
            print(f"  partial sweep: {refused.unit_id} and every search after it do not "
                  f"run — "
                  + (f"the spend cap ${budget:.2f} cannot hold them"
                     if pool.stop_reason == "budget" else
                     "the connected accounts cannot safely hold them")
                  + ", and running what can was chosen.")
            stopped_early = True
        elif refused is not None and refusal is not None:
            visited.append({"unit_id": refused.unit_id, "provider": site_key,
                            "bounded": refused.ceiling is not None,
                            "reservation": "blocked_account", "reason": refusal})
            print(f"  ⚠ account pool: {refused.unit_id} cannot start ({refusal}) — "
                  f"stopping.")
            telemetry.paid_unvisited("skipped_account")
            stopped_early = True
        elif refused is not None:
            held = exposure.units[refused.unit_id]
            visited.append({"unit_id": refused.unit_id, "provider": site_key,
                            "bounded": refused.ceiling is not None,
                            "reservation": "blocked"})
            if refused.ceiling is not None:
                print(f"  ⚠ spend cap ${budget:.2f}: ${held['view_usd']} held + the next "
                      f"start's ${refused.ceiling} ceiling would exceed it — stopping.")
            else:
                print(f"  ⚠ spend cap ${budget:.2f} reached (${held['view_usd']} "
                      f"held and observed) — stopping.")
            stopped_early = True

    held = exposure.snapshot()
    print(f"\n  held for {held['committed_starts']} provider-bounded start(s): "
          f"${held['committed_usd']} of ceiling (exposure, not spend)")
    waits = [u["buffered_wait_ms"] for u in visited if "buffered_wait_ms" in u]
    section = {
        "mode": "reservation_scheduler", "workers": workers,
        "exposure": held, "peak_in_flight": peak["in_flight"],
        "peak_buffered": peak["buffered"],
        "buffered_wait_ms": {"total": sum(waits), "max": max(waits, default=0)},
        "checkpoint": checkpoint, "segments": segments, "units": visited}
    if pool is not None:
        section["accounts"] = pool.snapshot()
        section["authorization"] = dict(
            pool.authorization, executed_paid_units=held["committed_starts"],
            executed_bounded_exposure_usd=held["committed_usd"])
        write_authorization(os.path.dirname(pool.ledger.path), section["authorization"])
        pool.ledger.doc["authorization"] = section["authorization"]
        pool.ledger.finish()
    telemetry.paid_execution(section)
    return spent, stopped_early, site_key


def print_plan(plans):
    """plans: dict of {site_key: [search, ...]}."""
    total_runs = sum(len(p) for p in plans.values())
    print(f"Sites:     {', '.join(plans)}")
    print(f"Actor runs: {total_runs} total\n")
    for site_key, plan in plans.items():
        per_run = SITES[site_key].get("results_per_run", SEARCH["max_results"])
        floor = ACTOR_MIN_RESULTS.get(site_key)
        if floor is not None:
            per_run = max(floor, per_run)   # match what the actor is sent
        print(f"  {site_key}: {len(plan)} searches (max {per_run} results each)")
        for s in plan:
            # The company matters more than the keyword when a plan is
            # company-filtered: without it four paid runs print as four
            # identical blank lines, and you can't see what you're buying.
            who = f" [{s['company']}]" if s.get("company") else ""
            print(f"    · {(s['keywords'] or '(all)') + who:<40} @ {s['location']:<14}")
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
        # Prefer what the JD actually demands (parsed in score_job) over the raw
        # source field, which most sources never populate. Rendered as "3+" so a
        # floor isn't mistaken for an exact requirement.
        "experience_required": (f"{row['years_required']}+"
                                if row.get("years_required") is not None
                                else row.get("Experience", "")),
        "salary": row.get("Salary", ""),
        "hr_email": row.get("hr_email", ""),
        "hr_phone": row.get("hr_phone", ""),
        "source_site": row.get("Source", ""),
        "apply_url": row.get("Job URL", ""),
        "date_posted": row.get("Posted Date", ""),
        # The employer's own requisition id, where a source exposes one — it is
        # what a referral is submitted against, so it has to survive to output.
        # Blank for every source that doesn't publish one.
        "req_number": row.get("req_number", ""),
        # UHG's internal pay grade, from sources/optum.detail(). This line was
        # MISSING while "grade" sat in OUTPUT_COLUMNS, so every sweep wrote the
        # column and left it empty — and an empty grade column reads as "the
        # employer didn't publish one", not as "we dropped it on the floor".
        # It is the whole eligibility filter for the Optum runs (24/25 are in
        # band, 26 asks 3+ years), so a silent blank there is the difference
        # between a scan that answers the question and one that only looks like
        # it did. Verified against a live JD: the extractor and the source were
        # both fine, this assembler was the only broken link.
        "grade": row.get("grade", ""),
        "verified_live": row.get("verified_live", ""),
        # WHICH paid search returned this row, and at what position in it.
        # Two measurement rounds wanted this and had to work around not having
        # it: keyword attribution was unrecoverable without re-reading Apify
        # datasets, and result rank survived only by accident inside LinkedIn's
        # own apply_url. Free sources answer no query and leave both blank.
        #
        # Stamped in scrape_search BEFORE finalize() dedupes, so the value
        # describes the search that actually produced the row. search_rank is
        # the DATASET index, i.e. the actor's push order, not the provider's
        # rank (V2-C3 §14): LinkedIn's is the `position` in apply_url, which
        # V2-C4 telemetry records as provider_positions. NOTE that dedupe
        # then keeps one row per posting and the survivor's rank is whichever
        # search sorted first, NOT the lowest rank across searches — a depth
        # analysis built on this is an upper bound on what a shallower sweep
        # would lose, not an exact figure.
        "search_query": row.get("search_query", ""),
        "search_rank": row.get("search_rank", ""),
    }


# Populated by finalize() each call so main() can report what got filtered.
LAST_STATS = {}

# V2-C2: score_job's verdict for this row object — kept (True) or dropped
# (False) — once it has been scored with memo=True. A paid sweep re-finalizes
# every row acquired so far after every search, and score_job is 99.7% of a
# pass (V2-C1 §20 measured ~225 s of CPU over 90 searches). It reads only a
# row's acquired fields and rewrites every field it sets from them (the one it
# reads back, `timezones`, it rewrites to the same value), so a second call on
# the same row changes nothing; this skips that call. to_output() never reads
# the key, and nothing that keys, filters, ranks or dedupes a row looks at it.
#
# SCORED_BY is the ScoringContext.key the verdict was reached under, and a
# verdict is reused only under that same context. SCORED alone used to be
# trusted, so a verdict left by any other profile — a stale False included —
# suppressed a real evaluation.
SCORED = "_scored"
SCORED_BY = "_scored_by"


def _score_once(row, ctx=None):
    key = (ctx or default_context()).key
    if row.get(SCORED_BY) != key:
        row[SCORED] = score_job(row) is not None
        row[SCORED_BY] = key
    return row if row[SCORED] else None


def score_and_filter(raw_rows, stage=None, memo=False):
    """Score, then every hard filter finalize applies, in finalize's order.
    Returns (the rows still eligible, in arrival order; the filter counts).

    finalize() is this plus rank_rows() and to_output(). Split out for Search
    V2-B3 so the shadow evaluator (sources/shadow.py) runs the SAME chain
    rather than a copy of it: a filter added here reaches both callers, and
    there is no second list of predicates to drift out of step.

    `stage` receives the boundary counts. The default is telemetry — the
    production pass. A diagnostic pass passes its own collector, so it cannot
    overwrite the production funnel in the telemetry record.

    `memo` scores each row object at most once across calls (SCORED), per
    context. Only main() under SWEEP_PAID_CONCURRENCY asks for it; every other
    caller re-scores, as before.
    """
    stage = stage or telemetry.stage
    stage("observed_normalized", raw_rows)
    # One context for the whole pass, so its memo key is computed once.
    score = functools.partial(_score_once, ctx=default_context()) if memo else score_job
    scored = [r for r in (score(row) for row in raw_rows) if r is not None]
    if SETTINGS["min_score"] is not None:
        scored = [r for r in scored if r["score"] >= SETTINGS["min_score"]]
    # Per-source counts at each boundary the engine ALREADY crosses. Nothing is
    # moved, re-ordered or re-filtered to obtain them; telemetry.stage only
    # counts the list finalize is holding at that instant, and is a single
    # `is None` test when the flag is off.
    stage("post_hard_filter_and_score", scored)

    # Freshness: drop jobs older than max_age_days.
    stale = 0
    if SETTINGS["max_age_days"] is not None:
        fresh = [r for r in scored if is_recent(r.get("Posted Date"), SETTINGS["max_age_days"])]
        stale = len(scored) - len(fresh)
        scored = fresh
    stage("post_recency", scored)

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
    stage("post_salary_reachability_visa_eor", scored)

    # Work arrangement, from Sweep's "which jobs should Sweep include" choice.
    # The "remote" answer needs nothing here — it is already expressed as
    # remote_scopes above, which is the filter that was measured and tuned.
    # The other two are the inverse, and they exist because without them
    # "In India — onsite or hybrid" and "Onsite, anywhere in the world" were
    # the SAME sweep: both left remote_scopes empty, so neither filtered at
    # all, and on the free sources (which have no location parameter) the two
    # answers returned byte-identical job sets.
    #
    # Applied to paid rows as well as free ones, deliberately: LinkedIn's
    # geoId narrows WHERE a search runs, never what arrangement comes back,
    # so a paid India search returns remote rows too.
    wrong_arrangement = off_geography = 0
    scope = SETTINGS["work_scope"]
    if scope in ("india", "global"):
        ok = [r for r in scored if onsite_or_hybrid(r)]
        wrong_arrangement = len(scored) - len(ok)
        scored = ok
        stage("post_arrangement", scored)
        if scope == "india":
            here = [r for r in scored
                    if in_home_country(r.get("Location") or r.get("location"))]
            off_geography = len(scored) - len(here)
            scored = here

    stage("post_location_eligible", scored)
    return scored, {"stale": stale, "low_salary": low_salary,
                    "unreachable": unreachable, "rescued": rescued,
                    "no_visa": no_visa, "no_eor": no_eor,
                    "wrong_arrangement": wrong_arrangement,
                    "off_geography": off_geography}


def rank_rows(rows):
    """Best first, then one row per posting: the order a user is shown.

    Sorts `rows` IN PLACE, exactly as finalize always has. The sort is stable,
    so equal scores keep arrival order, and arrival order is what decides which
    of two duplicates dedupe keeps. Split out beside score_and_filter so the
    shadow evaluator ranks by this function, not by a second copy of it.
    """
    rows.sort(key=lambda r: r["score"], reverse=True)
    return dedupe(rows)  # sorted first, so highest-scored duplicate wins


def finalize(raw_rows, memo=False):
    """Score, filter, rank, and dedupe raw normalized rows into output rows.
    `memo`: see score_and_filter."""
    scored, stats = score_and_filter(raw_rows, memo=memo)
    unique = rank_rows(scored)
    telemetry.stage("final_after_dedupe", unique)
    # V2-C1: which paid unit each survivor came from and what beat the rest,
    # read off dedupe's own result. A no-op unless the sweep has a paid plan.
    telemetry.paid_outcome(scored, unique, job_key)
    # V2-C4: the same two lists, held for the adaptive shadow's observation of
    # this checkpoint. A single `is None` test unless it is on.
    paid_adaptive.capture(scored, unique)
    # normalized_rows, not raw_rows: by the time finalize sees them the
    # adapters have already mapped every row into the internal schema. The raw
    # endpoint counts live per source and are summed in telemetry.finish().
    telemetry.counts(normalized_rows=len(raw_rows), eligible_rows=len(scored),
                     final_rows=len(unique))
    telemetry.eligible_progress(len(unique))
    LAST_STATS.update(stale=stats["stale"], low_salary=stats["low_salary"],
                      kept=len(unique),
                      unreachable=stats["unreachable"], rescued=stats["rescued"],
                      no_visa=stats["no_visa"], no_eor=stats["no_eor"],
                      wrong_arrangement=stats["wrong_arrangement"],
                      off_geography=stats["off_geography"])
    return [to_output(r) for r in unique]


def shadow_engine(final_rows, production_rows):
    """What sources/shadow.py judges a shadow row with: this sweep's own
    functions, handed over rather than re-implemented, and the finished result
    it is judged against.

    Nothing here is a second copy of a rule. score_and_filter and rank_rows ARE
    finalize; the only wrapper is the one that keeps a diagnostic from leaving
    a trace in module state a production summary reads.
    """
    def isolated(rows, stage):
        # experience_guard.record() appends every acted-on verdict to a module
        # list when that guard is on. It is off in production; if it is ever
        # on, a shadow row's verdict must still not land in the sweep's record.
        mark = len(experience_guard.DROPPED)
        try:
            return score_and_filter(rows, stage)
        finally:
            del experience_guard.DROPPED[mark:]

    days = SETTINGS["max_age_days"]
    return shadow.Engine(
        final_rows=final_rows, production_rows=production_rows,
        prepare=_truncate_desc, score_and_filter=isolated, rank_rows=rank_rows,
        job_key=job_key, to_output=to_output,
        recent=None if days is None else (lambda d: is_recent(d, days)),
        # Search SCOPE only — what the user chose, never what the résumé says —
        # so the aggregator can group sweeps without a profile property.
        scope={"work_scope": SETTINGS["work_scope"],
               "remote_scopes": list(SETTINGS["remote_scopes"] or []),
               "max_age_days": days})


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


@contextlib.contextmanager
def _replaced(path, **open_kw):
    """Write `path` whole or not at all (V2-B5).

    The public worker serves the newest CSV to whoever asks, at any moment,
    and truncate-then-write left a window in which that was half a file — and
    a crash inside it left the half for good. Written beside the target and
    renamed over it, a reader sees the previous complete file or the new one.
    The temp name ends in .tmp, so no *.csv / jobs_*.json glob can pick it up.
    """
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", **open_kw) as fh:
            yield fh
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def write_outputs(out_rows, csv_path, json_path):
    os.makedirs(SETTINGS["output_dir"], exist_ok=True)
    with _replaced(csv_path, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(out_rows)
    with _replaced(json_path, encoding="utf-8") as f:
        json.dump(out_rows, f, indent=2, ensure_ascii=False)


# SWEEP_RESULTS_READY_EARLY (V2-B5, default off). The worker marks a run done
# when this process exits, and a sweep's last seconds — the shadow tranche,
# the telemetry record — cannot change what the user gets. With the flag on,
# main() says so the moment the result is final, and the worker tells Render.
READY_FLAG = "SWEEP_RESULTS_READY_EARLY"
# Read under the same name by deploy/sweep_worker.py, beside .done_combos.
READY_MARKER = ".results_ready"


def results_ready_early():
    return os.environ.get(READY_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def publish_results_ready():
    """The marker, written the way the outputs are: whole or not at all."""
    path = os.path.join(SETTINGS["output_dir"], READY_MARKER)
    with _replaced(path, encoding="utf-8") as fh:
        json.dump({"at": time.time()}, fh)
    return path


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
    if SETTINGS["work_scope"] in ("india", "global"):
        filters.append(f"{LAST_STATS.get('wrong_arrangement', 0)} remote "
                       f"(this sweep wants onsite/hybrid)")
        if SETTINGS["work_scope"] == "india":
            filters.append(f"{LAST_STATS.get('off_geography', 0)} outside India")
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
    p.add_argument("--json", action="store_true",
                   help="With --dry-run, print the plan as JSON instead of prose.")
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
    args = p.parse_args()
    # --json only ever changes --dry-run's output. On its own it was accepted
    # and silently did nothing, so a caller expecting machine-readable output
    # got prose and no indication why.
    if args.json and not args.dry_run:
        p.error("--json only applies with --dry-run. "
                "Use: --dry-run --json to print the plan as JSON.")
    return args


def _token_headroom(token):
    """(used, cap) month-to-date for a token, or None if it can't be read."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"https://api.apify.com/v2/users/me/limits?token={token}",
                timeout=15) as r:
            d = json.load(r)["data"]
        return (d["current"]["monthlyUsageUsd"],
                d["limits"]["maxMonthlyUsageUsd"])
    except Exception:
        return None


def _token_slot(name):
    """Sort key putting APIFY_TOKEN first, then APIFY_TOKEN_2, _3, ... in
    NUMERIC order. A plain string sort plausibly puts _10 before _9, which
    would silently reorder which account a run reports first."""
    if name == "APIFY_TOKEN":
        return (0, 0, "")
    suffix = name[len("APIFY_TOKEN_"):]
    return (1, int(suffix), "") if suffix.isdigit() else (2, 0, suffix)


def apify_tokens(env=None):
    """Every distinct Apify token configured, as (name, token) pairs in slot
    order.

    APIFY_TOKEN, APIFY_TOKEN_2, ... are separate free accounts with their own
    $5 caps, and an Apify dataset belongs to the account that ran it. So every
    caller that walks accounts has to agree on this list or it silently reads
    a subset: rescore_from_apify.py hardcoded three names, which meant a
    fourth key's paid results went missing from every re-rank with no error —
    the exact failure that file's own comment warns about.

    Pure in the environment handed to it; load_dotenv() is the caller's job.
    That matters because this is imported by the sweep web UI and by tests
    that patch os.environ — reading .env in here would pull real tokens into
    a patched environment.
    """
    env = os.environ if env is None else env
    names = [n for n in env
             if n == "APIFY_TOKEN" or n.startswith("APIFY_TOKEN_")]
    seen, out = set(), []
    for name in sorted(names, key=_token_slot):
        value = (env.get(name) or "").strip()
        # dedupe: the same key pasted into two slots is one wallet, not two
        if value and value not in seen:
            seen.add(value)
            out.append((name, value))
    return out


def _require_token():
    """The configured token with the most credit left.

    APIFY_TOKEN, APIFY_TOKEN_2, ... are separate free accounts with their own $5
    caps. This used to return APIFY_TOKEN unconditionally, which made every
    additional key decorative: with $2.50 left on the first account and $5.00
    untouched on the second, an 84-search sweep costing ~$3.86 died at search 54
    on "Monthly usage hard limit exceeded" — and because each search is wrapped
    in its own try/except, it exited 0 with a normal-looking summary.

    Reading the limits endpoint is free, so the choice is made on facts rather
    than on which variable was named first. Unreadable tokens sort last but stay
    usable, since a network blip is not proof a key is spent.

    NOT a mid-run switch: the client is built once, so a sweep still cannot span
    two accounts. It picks the best single account for the whole sweep, which is
    enough whenever one account's headroom covers the plan — check --dry-run
    against the numbers below if it might not.
    """
    if byok_tokens() is not None:
        # V2-D closeout: a visitor's key never reaches this single-account
        # engine, which checks the sweep's cap and never the account's credit.
        # main() sends every BYOK run through the pool; this is the backstop.
        sys.exit("Refusing to start: a visitor's Apify keys are spent only through "
                 "the account pool's authorization. Nothing was started.")
    from dotenv import load_dotenv
    load_dotenv()
    tokens = {token: name for name, token in apify_tokens()}
    if not tokens:
        sys.exit("APIFY_TOKEN not found. Add it to a .env file in this folder.")
    if len(tokens) == 1:
        return next(iter(tokens))

    scored = []
    for token, name in tokens.items():
        h = _token_headroom(token)
        left = -1.0 if h is None else h[1] - h[0]
        scored.append((left, name, token, h))
    scored.sort(key=lambda t: -t[0])
    for left, name, _, h in scored:
        detail = "unreadable" if h is None else f"${h[0]:.2f} of ${h[1]:.2f} used"
        print(f"  {name:<16} {detail:<24} "
              + ("" if h is None else f"${left:.2f} left"))
    best = scored[0]
    print(f"  -> using {best[1]}"
          + ("" if best[3] is None else f" (${best[0]:.2f} available)"))
    return best[2]


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

    # Markup never reaches the scorer, whichever half fetched the row. The
    # paid path was the one that let it through, and the cost lands on the
    # experience window: the cue and the figure are two words apart in the
    # text and 20 characters apart in the markup.
    html_jd = ("<h3><strong>Experience required:</strong></h3>"
               "<h3>4 to 9 years (B2 or B1 level)</h3>")
    assert "<" not in normalize({"description": html_jd}, "linkedin")["Description"]
    assert _required_experience_floor(html_jd.lower()) is None          # as fetched
    assert _required_experience_floor(
        normalize({"description": html_jd}, "linkedin")["Description"].lower()) == 4

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

    # A requisition number is the employer's own identity for the opening, so two
    # cities' postings of one title stay two rows — but the SAME req seen twice
    # still collapses.
    reqs = [{"Title": "Senior Full Stack Engineer", "Company": "Optum",
             "Location": "Bengaluru", "req_number": "2378405"},
            {"Title": "Senior Full Stack Engineer", "Company": "Optum",
             "Location": "Hyderabad", "req_number": "2378409"}]
    assert len(dedupe(reqs)) == 2, dedupe(reqs)
    assert len(dedupe(reqs + [dict(reqs[0], Location="Bangalore")])) == 2
    assert job_key(reqs[0]) != job_key(reqs[1])

    # Every DECLARED output column must actually be produced. This is the whole
    # bug class, not one field: "grade" sat in OUTPUT_COLUMNS while to_output()
    # never copied it, so csv.DictWriter dutifully wrote the header and left the
    # column empty on every row of every sweep. An empty column reads as "the
    # employer didn't publish this", which is why it survived — nothing looked
    # broken. Comparing the two sets catches the next forgotten column for free.
    assert set(to_output({})) == set(OUTPUT_COLUMNS), (
        set(OUTPUT_COLUMNS) ^ set(to_output({})))
    # And specifically that the pay grade rides through, since it IS the
    # eligibility filter on the Optum runs (24/25 in band, 26 wants 3+ years).
    assert to_output({"grade": "25"})["grade"] == "25"

    # Two-tier seniority: an inflated title label must not delete a role whose
    # stated requirement is within reach, but a genuinely senior one still goes.
    #
    # FULLY PINNED — the flag AND the vocabularies. These asserts are about the
    # mechanism, not about anyone's word lists, and every part of them read live
    # has now broken on a legitimate config: drop_excluded=False (a résumé that
    # keeps over-experienced rows and penalizes them) and then hard_drop_terms
    # containing "developer" (a functional-consultant résumé, for which every
    # developer title IS a hard no) both failed here on correct settings. So the
    # test titles are nonsense words no real config scores, and the term lists are
    # substituted for the duration.
    sj = lambda t, d="": score_job({"Title": t, "Description": d})   # noqa: E731
    _saved = (SETTINGS["drop_excluded"], dict(HARD_DROP_PATTERNS),
              dict(SOFT_DROP_PATTERNS), SETTINGS["max_experience_years"])
    try:
        SETTINGS["drop_excluded"] = True
        SETTINGS["max_experience_years"] = 3
        HARD_DROP_PATTERNS.clear()
        HARD_DROP_PATTERNS.update({t: _compile(t) for t in ("principal", "manager")})
        SOFT_DROP_PATTERNS.clear()
        SOFT_DROP_PATTERNS.update({"senior": _compile("senior")})

        assert sj("Zorb Manager") is None                       # hard-drop term
        assert sj("Principal Zorb") is None
        assert sj("Senior Zorb", "2 years of experience.") is not None
        plain = sj("Zorb", "2 years of experience.")
        senior = sj("Senior Zorb", "2 years of experience.")
        assert senior["score"] == plain["score"] + SCORING["soft_penalty"]
        # A title label is not a requirement; a STATED floor over the threshold
        # is. This one goes even though "Senior" alone wouldn't do it.
        assert sj("Senior Zorb", "8+ years of experience.") is None

        # drop_excluded=False: the same rows are KEPT and sink by drop_penalty
        # rather than disappearing. That branch shipped with no coverage at all.
        SETTINGS["drop_excluded"] = False
        assert sj("Zorb Manager") is not None
        within = sj("Zorb", "2 years of experience.")
        over = sj("Zorb", "12 years of experience.")
        assert over is not None, "over-experienced row must be kept, not dropped"
        assert over["score"] == within["score"] + SCORING["drop_penalty"]
    finally:
        (SETTINGS["drop_excluded"], _h, _s,
         SETTINGS["max_experience_years"]) = _saved
        HARD_DROP_PATTERNS.clear(); HARD_DROP_PATTERNS.update(_h)
        SOFT_DROP_PATTERNS.clear(); SOFT_DROP_PATTERNS.update(_s)

    # Repost farms go whatever they score; whole-name match, so a real employer
    # whose name merely contains one is untouched.
    # Nonsense title for the same reason as the block above: this asserts what
    # blocked_company does, and a real job title drags whatever the loaded config
    # thinks of that title into the result ("Full Stack Engineer" is a hard drop
    # on a functional-consultant résumé). "2 years" clears any sane threshold.
    farm = {"Title": "Zorb", "Description": "2 years of experience."}
    assert score_job(dict(farm, Company="Hired")) is None
    assert score_job(dict(farm, Company="SWAKIO™")) is None
    assert score_job(dict(farm, Company="  jobs ai ")) is None
    assert score_job(dict(farm, Company="Freshired Labs")) is not None
    assert score_job(dict(farm, Company="")) is not None   # unknown != blocked

    # Experience floor: only figures that count EXPERIENCE, and the aggregate
    # decides which of several wins. All three cases are verbatim from live JDs.
    floor = _required_experience_floor
    assert floor("we were founded 5 years ago and love react") is None
    # A company's own age, next to a real requirement. The cue window looks 60
    # characters PAST the figure, so the "experience" in the second sentence
    # made the first number a requirement — 5 years read as the ask. min() hid
    # this by preferring the smaller number; the max default exposed it.
    assert floor("founded 5 years ago, we now have 3+ years of experience "
                 "shipping ml") == 3
    assert floor("in business 12 years. seeking 3 years of experience.") == 3
    assert floor("established for over 20 years. requires 5+ years of "
                 "experience.") == 5
    assert floor("our ceo is 40 years old. we want 4+ years of experience.") == 4
    assert floor("b.tech (minimum 16 years of formal education) "
                 "4+ years in a software engineer role") == 4      # degree != career
    both = ("8+ years of total software engineering experience, "
            "including 2+ years hands-on in ai/ml")
    # THE case the default decides. A structured JD states a total and a
    # per-skill figure; the total is the job. Reading the smaller one put a
    # senior role at the top of a junior candidate's shortlist, which is what
    # the results column showing 2+ on an 8+ posting was.
    assert floor(both) == 8                                        # default: max
    # A labelled field whose whole value is a years figure counts, even when no
    # experience word is anywhere near it — and a labelled DURATION does not.
    # Verbatim from the Netradyne template that put a 10-year role at the top of a
    # 2-year candidate's shortlist.
    assert floor("job title : salesforce techno functional consultant "
                 "department/group : business systems group "
                 "business systems group : 10+ years location : bangalore") == 10
    assert floor("experience : 3+ years") == 3
    assert floor("contract duration : 2 years") is None
    assert floor("internship : 1 year") is None
    assert floor("education : 15 years full time") is None
    assert floor("with growth exceeding 4x year over year") is None

    agg = SETTINGS.get("experience_aggregate")
    SETTINGS["experience_aggregate"] = "min"
    try:
        # The other aggregate still works, for the short-JD profiles that pick
        # it deliberately.
        assert floor(both) == 2
    finally:
        SETTINGS["experience_aggregate"] = agg

    # An unrecognised value falls back to MAX, not min: a typo in a profile
    # ("maximum", "average") must not silently switch the reading to the
    # direction that under-reports a senior job as a junior one.
    SETTINGS["experience_aggregate"] = "maximum"
    try:
        assert floor(both) == 8
    finally:
        SETTINGS["experience_aggregate"] = agg

    SETTINGS["experience_aggregate"] = "max"
    try:
        assert floor(both) == 8
        assert floor("3+ years of experience in full stack development") == 3
        # Accenture's template, verbatim. The "(s)" used to make this read as
        # "not stated", so every 5- and 8-year role ranked as if unbounded.
        assert floor("minimum 5 year(s) of experience is required") == 5
        assert floor("skills : java full stack development minimum 3 year(s) of "
                     "experience is required educational qualification : 15 "
                     "years full time education") == 3      # 15 is the degree
        assert floor("1 year of experience in software development") == 1
        # Ranges read as their LOWER bound whichever separator is used. Both
        # verbatim from Accenture JDs; both used to report the upper number.
        assert floor("experience 10-18+ years of overall it experience") == 10
        assert floor("10–18+ years of overall it experience") == 10
        assert floor("experience: 5 to 12 years of hands-on experience in "
                     "full-stack development") == 5
        assert floor("3-5 years of experience building web apps") == 3
    finally:
        SETTINGS["experience_aggregate"] = agg

    # The title gate fails SILENTLY — a wrong answer doesn't raise, it quietly
    # changes which jobs exist. An exclude must beat an include, because the
    # titles worth excluding contain an include term by construction.
    saved = ATS_TITLE_HINTS[:], ATS_TITLE_EXCLUDE[:]
    try:
        ATS_TITLE_HINTS[:] = ["software engineer", "ml engineer", "full stack"]
        ATS_TITLE_EXCLUDE[:] = ["data engineer", "automation testing"]
        assert is_dev_title("Senior AI/ML Engineer - LLM, RAG and Agentic AI")
        assert is_dev_title("Lead Full Stack Engineer - Java FSD")
        assert not is_dev_title("Senior Software Engineer I (Data Engineer - Spark)")
        assert not is_dev_title("Senior Software Engineer I - AWS Automation Testing")
        assert not is_dev_title("Senior Data Analyst - Power BI")   # no hint at all
        ATS_TITLE_EXCLUDE[:] = []
        assert is_dev_title("Senior Software Engineer I (Data Engineer - Spark)")
    finally:
        ATS_TITLE_HINTS[:], ATS_TITLE_EXCLUDE[:] = saved

    # The SHIPPED floor, not a stand-in: this list is what every free source is
    # filtered through for any profile that does not replace it, and the
    # generated ones did not. Measured against five live greenhouse boards
    # (2,567 open jobs, 2026-09-09) it admits 33% where the previous
    # 21-entry list admitted 16% — these are the titles that were being
    # dropped before anything could score them.
    assert ATS_TITLE_EXCLUDE == [], "the floor ships with no excludes"
    for title in ("Staff Engineer, Payments", "Site Reliability Engineer",
                  "Platform Engineer (Kubernetes)", "Senior SRE ",
                  "Machine Learning Engineer", "Principal Software Architect",
                  "iOS Engineer", "Android Developer", "SDET II",
                  "Security Engineer, AppSec", "Golang Engineer",
                  "Ruby on Rails Developer", "Technical Lead - Payments",
                  "Member of Technical Staff", "SDE-2", "Programmer Analyst",
                  "Software Development Engineer II", "Engineering Manager"):
        assert is_dev_title(title), f"floor drops a software title: {title}"
    # And it still has to keep out the jobs that share our vocabulary. These
    # are real titles from remoteok's public feed.
    for title in ("Store Manager", "Vehicle Maintenance Technician",
                  "Sales Development Representative", "Customer Success Manager",
                  "Financial Analyst", "Warehouse Merchandiser",
                  "Mechanical Engineer", "Process Engineer"):
        assert not is_dev_title(title), f"floor admits a non-software job: {title}"
    # "java " and two others carry a deliberate trailing space: without it they
    # match javascript, iOS-anything and "stressed".
    assert "java " in ATS_TITLE_HINTS and "java" not in ATS_TITLE_HINTS

    # Timezone gap down-ranks but never removes, and only past the free window.
    near = sj("Zorb", "Remote across Europe. 2 years experience.")
    far = sj("Zorb", "Remote in the US. 2 years experience.")
    assert near is not None and far is not None
    assert near["tz_gap"] == 4.5 and far["tz_gap"] == 11.5
    assert near["score"] > far["score"], (near["score"], far["score"])
    # 4.5h is inside TZ_FREE_HOURS, so the near role pays nothing at all.
    assert near["score"] == sj("Zorb", "2 years experience.")["score"]

    # A geo-locked role is rescued only when the EMPLOYER hires at home.
    orig = SETTINGS["remote_scopes"], SETTINGS["keep_restricted_if_hires_home"]
    SETTINGS["remote_scopes"] = ["worldwide"]
    SETTINGS["keep_restricted_if_hires_home"] = True
    try:
        base = {"Title": "Zorb", "Description": "2 years experience.",
                "Location": "New York, NY (HQ), Remote"}
        assert len(finalize([dict(base, Company="A", hires_home="yes")])) == 1
        assert len(finalize([dict(base, Company="B", hires_home="no")])) == 0
        assert len(finalize([dict(base, Company="C", hires_home="")])) == 0  # feeds
        # ...or when the lock is TO home: "remote within India" is the most
        # reachable role there is, whatever the employer's other postings say.
        home = {"Title": "Zorb", "Description": "2 years experience.",
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

    # A remote-constrained query must mark its rows, or they get filtered out as
    # non-remote for not repeating in prose what the query already guaranteed.
    assert remote_was_queried("linkedin", {"location": "Remote"})
    assert remote_was_queried("naukri", {"location": "remote"})
    assert not remote_was_queried("linkedin", {"location": "Germany"})
    orig_flag = SITES.get("linkedin", {}).get("remote_only")
    try:
        SITES.setdefault("linkedin", {})["remote_only"] = True
        assert remote_was_queried("linkedin", {"location": "Germany"})
        assert not remote_was_queried("indeed", {"location": "Germany"})
    finally:
        SITES["linkedin"]["remote_only"] = orig_flag
    # The stamp is what makes an India-locked remote row survive: city location
    # alone reads as "not stated" and is dropped.
    plain = enrich.enrich({"Location": "Bengaluru, Karnataka, India",
                           "Title": "Zorb", "Description": "Build UIs."})
    stamped = enrich.enrich({"Location": "Bengaluru, Karnataka, India, Remote",
                             "Title": "Zorb", "Description": "Build UIs."})
    assert plain["remote_scope"] == "", plain
    assert stamped["remote_scope"] == "restricted", stamped
    assert stamped["remote_regions"] == "India", stamped

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
        # Word boundaries, not substrings: US Indiana is not India, and matched a
        # quarter of the cards an India-only sweep was keeping.
        assert location_allowed("Indianapolis, Indiana") is False
        assert location_allowed("New Albany, Indiana") is False
        assert location_allowed("Indiana, Pennsylvania") is False
    finally:
        LOCATION_HINTS = original

    # Token discovery. Belongs in the silent-failure self-check because that
    # is how it broke: rescore_from_apify.py scanned a hardcoded three names,
    # so a fourth key's datasets were skipped with no error and its paid rows
    # simply never appeared in a re-rank.
    assert apify_tokens({}) == []
    assert apify_tokens({"NOT_A_TOKEN": "x"}) == []
    assert apify_tokens({"APIFY_TOKEN": "a"}) == [("APIFY_TOKEN", "a")]
    # A blank slot is not a key, and neither is a whitespace-only one.
    assert apify_tokens({"APIFY_TOKEN": "a", "APIFY_TOKEN_2": "  "}) == [
        ("APIFY_TOKEN", "a")]
    # The same key in two slots is one wallet: counting it twice is what
    # inflated the sweep budget.
    assert apify_tokens({"APIFY_TOKEN": "a", "APIFY_TOKEN_2": "a"}) == [
        ("APIFY_TOKEN", "a")]
    # Numeric slot order, so _10 lands after _9 rather than after _1.
    assert [n for n, _ in apify_tokens(
        {"APIFY_TOKEN_10": "j", "APIFY_TOKEN_9": "i", "APIFY_TOKEN": "a"})] == [
        "APIFY_TOKEN", "APIFY_TOKEN_9", "APIFY_TOKEN_10"]
    # Every key is found, however many: the cap that broke this was three.
    assert len(apify_tokens({"APIFY_TOKEN": "a", "APIFY_TOKEN_2": "b",
                             "APIFY_TOKEN_3": "c", "APIFY_TOKEN_4": "d",
                             "APIFY_TOKEN_5": "e"})) == 5

    # --- Telemetry is PASSIVE ------------------------------------------------
    # The claim that a flag changes nothing is worth exactly as much as the test
    # behind it. finalize() is the whole funnel — score, every hard filter, sort
    # and dedupe — so running it twice over one row set with the flag flipped
    # asserts the property that actually matters: identical rows, identical
    # order. Not "similar counts": the same list.
    #
    # Remote, fresh, and several rows deliberately TIED on score: the sort is
    # stable, so a tie is broken by arrival order, and arrival order is exactly
    # what an observer must not perturb. Two of them (Beta Labs / Beta) share a
    # dedupe key, so which one survives is order-dependent too.
    today = datetime.now().strftime("%Y-%m-%d")

    def _row(title, company, source, url, desc, location="Remote"):
        return {"Title": title, "Company": company, "Location": location,
                "Description": desc, "Posted Date": today, "Source": source,
                "Job URL": url, "Salary": "", "Experience": ""}

    sample = [
        _row("Backend Engineer", "Beta Labs", "lever:beta", "https://x/2",
             "python django"),
        _row("Engineer, Backend", "Beta", "remoteok", "https://x/3",
             "python django", "Worldwide"),
        _row("Full Stack Developer", "Gamma", "greenhouse:gamma", "https://x/4",
             "react node postgres"),
        _row("Software Engineer", "Delta", "ashby:delta", "https://x/5",
             "react node postgres"),
        _row("Frontend Engineer", "Epsilon", "jobicy", "https://x/6",
             "react typescript", "Anywhere in the World"),
        _row("React Native Developer", "Zeta", "himalayas", "https://x/7",
             "react native typescript"),
    ]
    was = os.environ.get(telemetry.FLAG)
    os.environ.pop(telemetry.FLAG, None)
    assert not telemetry.enabled()
    off = finalize([dict(r) for r in sample])
    off_stats = dict(LAST_STATS)
    # Guard the guard: a sample that collapses to one row would pass this test
    # no matter what telemetry did, and the first version of it did exactly
    # that. Order can only be asserted if there is an order, and a tie can only
    # be mis-broken if there is a tie.
    assert len(off) >= 4, f"passivity sample too weak to detect reordering: {off}"
    assert len({r["score"] for r in off}) < len(off), "no score ties to break"

    os.environ[telemetry.FLAG] = "1"
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        telemetry.start("free", tmp)
        on = finalize([dict(r) for r in sample])
        rec = telemetry.record()
        assert rec["stages"]["final_after_dedupe"]["total"] == len(on), rec["stages"]
        telemetry.finish()
    if was is None:
        os.environ.pop(telemetry.FLAG, None)
    else:
        os.environ[telemetry.FLAG] = was
    assert on == off, "telemetry changed the result set"
    assert dict(LAST_STATS) == off_stats, "telemetry changed the filter stats"
    # And the diagnostic native-identity key cannot reach an output row, which
    # is what makes capturing it safe at all.
    assert "_native" not in to_output({"_native": {"native_id": "1"}})

    print("demo ok")


def main():
    args = parse_args()
    if args.demo:
        demo()
        return
    # A Multi-Track Sweep loads, but nothing here can plan, fetch or score one
    # yet: every step below reads the one-profile globals, which for schema 2
    # are config's defaults — another person's keywords and tables.
    if config.TRACKS:
        sys.exit(f"profiles/{config.PROFILE}.py is a Multi-Track Sweep "
                 f"({len(config.TRACKS)} tracks). This build loads it but "
                 f"cannot run it yet; nothing was searched or charged.")
    if config.PROFILE and not (args.dry_run and args.json):
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
    n_optum = 1 if OPTUM.get("enabled") else 0
    n_ent = len(ENTERPRISE.get("employers") or []) if ENTERPRISE.get("enabled") else 0
    run_free = ((args.site in FREE_SITES or args.site is None)
                and not args.test and not args.no_free
                and bool(n_boards or n_feeds or n_optum or n_ent))

    if not plans and not run_free:
        sys.exit("Nothing to run — no sites enabled and no free sources configured.")

    if plans and not (args.dry_run and args.json):
        print_plan(plans)
    if run_free and not (args.dry_run and args.json):
        print(f"Free sources: {n_boards} ATS boards "
              f"({', '.join(k for k, v in ATS_BOARDS.items() if v)}) "
              f"+ {n_feeds} feeds ({', '.join(k for k, v in FEEDS.items() if v.get('enabled'))})"
              + (f" + {OPTUM['company']} careers ({_optum_scope()}, "
                 f"live-verified)" if n_optum else "")
              + (f" + {n_ent} enterprise careers sites "
                 f"({', '.join(ENTERPRISE['employers'])})" if n_ent else "") + "\n")

    if args.dry_run and args.json:
        print(json.dumps({
            "profile": config.PROFILE,
            "sites": {site_key: [{"keywords": s["keywords"],
                                  "location": s["location"],
                                  "company": s.get("company") or ""}
                                 for s in plan]
                      for site_key, plan in plans.items()},
            # Results per search, which is what a pay-per-event actor bills
            # on (build_input maps it to maxItemsPerSearch / count / maxJobs).
            # Read through effective_search, the SAME call the actor input
            # goes through, so the figure a cost estimate is built from and
            # the figure the actor is handed cannot drift apart.
            "max_results": {
                site_key: effective_search(site_key, plan[0])["max_results"]
                for site_key, plan in plans.items()},
            # V2-C4: the provider-enforced ceiling ONE search carries at that
            # depth, from the function scrape_search applies — null where the
            # provider has none. What a cap must hold to authorise the plan;
            # not a cost estimate.
            "charge_ceiling_usd": {
                site_key: (lambda c: None if c is None else str(c))(max_charge_usd(
                    site_key, effective_search(site_key, plan[0])["max_results"]))
                for site_key, plan in plans.items()},
            "free_sources": n_boards + n_feeds + n_optum + n_ent,
            # V2-D: how a visitor's run on this engine, in this environment,
            # would spend — "multi", "single" or "off" (public_paid_mode). Read
            # by the public app, whose worker runs this dry run with its own
            # flags; anything else there (an older engine) means unavailable.
            "public_paid": public_paid_mode(),
        }))
        return

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
    # An earlier run's marker would vouch for files this one has not written.
    # The worker's directory is per run so it never holds one; a console
    # profile's is reused.
    with contextlib.suppress(OSError):
        os.remove(os.path.join(SETTINGS["output_dir"], READY_MARKER))

    # SWEEP_SEARCH_V2_TELEMETRY (default off). Opened here because this is the
    # first point at which the sweep knows both what it will run and where it
    # will write. Everything it records is read off work the engine does anyway.
    telemetry.start("paid+free" if (plans and run_free) else
                    ("paid" if plans else "free"),
                    SETTINGS["output_dir"], profile=config.PROFILE or "")
    if telemetry.active():
        telemetry.note("Free holds every row until the last source returns, so "
                       "its eligible milestones are checkpoint-granular, not "
                       "streamed. See docs/search-engine-v2-a-telemetry-and-shadow.md.")

    # Loaded ONCE, before anything is written: --only-new must filter against
    # what EARLIER runs reported, and the ledger is appended to only at the end.
    seen = load_seen()
    if args.only_new and seen:
        print(f"--only-new: {len(seen)} postings already reported by earlier runs\n")

    # SWEEP_PAID_CONCURRENCY (V2-C2) on a sweep with a paid plan: the paid
    # phase runs through paid_phase_c2(), and every finalize pass of this sweep
    # scores each row once rather than once per checkpoint (score_and_filter's
    # memo) — same passes, same files, same order. Not while the experience
    # guard records a verdict per scoring call.
    c2 = bool(plans) and paid_concurrency()
    score_once = c2 and not experience_guard.enabled()

    def emit(rows):
        """finalize + optional new-only filter + write. Used for checkpoints too,
        so an interrupted sweep leaves a correct file behind."""
        out = finalize(rows, memo=True) if score_once else finalize(rows)
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
        telemetry.mark("paid_phase_start")
        units = None
        if telemetry.active():
            # V2-C1: every planned search gets its id now, in the order the
            # loop below visits them, so one that is skipped, stopped or fails
            # before any actor run exists still has an identity.
            units = [paid_unit(n, site_key, search) for n, (site_key, search) in
                     enumerate((s, q) for s, p in plans.items() for q in p)]
            telemetry.paid_plan(units, budget)
        # V2-C4, SWEEP_PAID_ADAPTIVE_MODE (default off; shadow needs telemetry).
        # Shadow records what candidate policies WOULD have decided; nothing is
        # enforced, so the plan, the requests and every byte are as off.
        if paid_adaptive.start(units, budget, job_key) == "enforce":
            telemetry.note("SWEEP_PAID_ADAPTIVE_MODE=enforce: no policy has passed "
                           "the V2-C4 evidence gate, so it ran as shadow — the "
                           "full plan, nothing skipped or cut.")
        from apify_client import ApifyClient
        # V2-C4.5 (default off): every configured account, pooled, under C2 only.
        # V2-D closeout: a visitor's keys (BYOK) ALWAYS go through the pool —
        # every account read afresh, every ceiling placed before the first
        # start — at whatever width SWEEP_PAID_CONCURRENCY allows, on all of
        # their accounts or (SWEEP_PAID_MULTI_ACCOUNT off) the one with the
        # most capacity. SWEEP_PUBLIC_PAID off starts none. There is no
        # credit-unaware path for them.
        pool = None
        byok = byok_tokens() is not None
        if byok and not public_paid_enabled():
            print(f"\n⚠ Paid searches are switched off for public sweeps "
                  f"({PUBLIC_PAID_FLAG}); no account was read and nothing was charged.")
            telemetry.note(f"V2-D: {PUBLIC_PAID_FLAG} is off — no paid search started.")
            telemetry.mark("paid_phase_done")
            written = telemetry.finish()
            if written:
                print(f"  telemetry: {written}")
            sys.exit(PAID_REFUSED_EXIT)
        if paid_multi_account() and not c2 and not byok:
            print(f"  ({PAID_MULTI_ACCOUNT_FLAG} needs {PAID_CONCURRENCY_FLAG}; "
                  f"one account, as before)")
            telemetry.note(f"{PAID_MULTI_ACCOUNT_FLAG} was set without "
                           f"{PAID_CONCURRENCY_FLAG}: one account, as before.")
        if byok or (c2 and paid_multi_account()):
            pool = AccountPool.open(SETTINGS["output_dir"], ApifyClient,
                                    single=byok and not paid_multi_account())
            token = client = baseline = None
        else:
            token = _require_token()
            client = ApifyClient(token)
            # Month-to-date spend BEFORE this sweep. Everything below measures
            # against the account rather than the actors' self-reports, so the cap
            # counts money that actually left. Falls back to summing usage_total_usd
            # if the account can't be read — better an undercount than no guard.
            baseline = account_usage_usd(client)
            if baseline is None:
                print("  (could not read account usage — spend cap falls back to the "
                      "actor self-report, which undercounts)")
        stopped_early = False
        n = 0
        if c2 or pool is not None:
            # V2-C2: the same plan through the reservation scheduler. The
            # token was chosen once, above; each worker gets its own client
            # on it, and never chooses an account itself.
            try:
                spent, stopped_early, site_key = paid_phase_c2(
                    plans, lambda: ApifyClient(token), client, baseline, budget,
                    raw_rows, done, done_path, today, emit, failures, pool=pool,
                    partial=bool(SETTINGS.get("allow_partial_paid_sweep")),
                    workers=None if c2 else 1)
            except PaidPlanRefused as exc:
                # V2-D1: nothing started, nothing charged, nothing written — a
                # full sweep never silently becomes a smaller one, and a
                # free-only result would be exactly that.
                print(f"\n⚠ {exc}")
                telemetry.note(f"V2-D1: {exc}")
                telemetry.mark("paid_phase_done")
                written = telemetry.finish()
                if written:
                    print(f"  telemetry: {written}")
                sys.exit(PAID_REFUSED_EXIT)
        else:
            for site_key, plan in plans.items():
                if stopped_early:
                    break
                actor_id = SITES[site_key]["actor"]
                print(f"\n{site_key} ({actor_id})")
                for i, search in enumerate(plan, 1):
                    unit_id = paid_unit_id(n)
                    n += 1
                    # The company is part of a combo's identity, not decoration:
                    # four company-filtered searches share an empty keyword and one
                    # location, so without it they collapse to a single key and
                    # three of the four paid runs silently "skip (done)".
                    who = f" [{search['company']}]" if search.get("company") else ""
                    label = f"{search['keywords'] or '(all)'}{who} @ {search['location']}"
                    combo_key = (f"{today}|{site_key}|{search['keywords']}|"
                                 f"{search['location']}|{search.get('company') or ''}")
                    if combo_key in done:
                        print(f"  [{i}/{len(plan)}] {label:<46} — skip (done)")
                        telemetry.paid_status(unit_id, "skipped_done")
                        continue
                    if budget is not None and spent >= budget:
                        print(f"  ⚠ spend cap ${budget:.2f} reached (${spent:.2f}) — stopping.")
                        stopped_early = True
                        break
                    # One telemetry work unit per paid search. `eff` is read
                    # through effective_search — the same call build_input goes
                    # through — so the depth recorded is the depth actually billed,
                    # not the smaller figure the plan asked for.
                    eff = effective_search(site_key, search)
                    with telemetry.unit(
                            "paid", site_key, board=actor_id,
                            query=search.get("keywords") or "(all)",
                            location=search.get("location") or "",
                            country=SEARCH.get("country", ""),
                            requested_limit=eff["max_results"]):
                        telemetry.paid_run(unit_id=unit_id)
                        ok = False
                        try:
                            rows, cost = scrape_search(client, site_key, actor_id,
                                                       search)
                            reading = time.monotonic()
                            actual = (account_usage_usd(client)
                                      if baseline is not None else None)
                            read_ms = round((time.monotonic() - reading) * 1000)
                            before = spent
                            spent = (actual - baseline if actual is not None
                                     else spent + cost)
                            # The actor's self-report AND the account delta, kept
                            # apart: the audit measured a sweep self-reporting $0.53
                            # against an account that moved $1.61, and a single
                            # "cost" field is exactly how that went unnoticed.
                            telemetry.observed(raw=len(rows), normalized=len(rows),
                                               gated=len(rows))
                            telemetry.paid_run(
                                reported_cost_usd=cost,
                                billed_delta_usd=round(spent - before, 6),
                                # V2-C1: what the budget guard now believes, and
                                # which reading it believes it from.
                                budget_view_usd=round(spent, 6),
                                budget_basis=("account_delta" if actual is not None
                                              else "run_record_sum"),
                                account_read_ms=read_ms if baseline is not None else None)
                            if actual is not None:
                                telemetry.cost_observation("account_usage_delta",
                                                           spent - before)
                            if telemetry.active():
                                # V2-C1 provenance, the "_native" bargain: a key
                                # to_output() never reads, only while a record is open.
                                for position, row in enumerate(rows, 1):
                                    row[telemetry.PAID_UNIT] = (unit_id, position)
                            raw_rows.extend(rows)
                            writing = time.monotonic()
                            emit(raw_rows)                            # checkpoint
                            telemetry.paid_run(checkpoint_ms=round(
                                (time.monotonic() - writing) * 1000))
                            with open(done_path, "a") as fh:          # mark done
                                fh.write(combo_key + "\n")
                            done.add(combo_key)
                            print(f"  [{i}/{len(plan)}] {label:<46} {len(rows):>3} jobs  "
                                  f"(${cost:.3f} actor, ${spent:.2f} billed)")
                            telemetry.paid_status(unit_id, "completed")
                            ok = True
                        except Exception as exc:  # isolate failures per search
                            telemetry.failed(exc)
                            telemetry.paid_run(failure_type=type(exc).__name__)
                            telemetry.paid_status(unit_id, "failed")
                            failures.append((site_key, label, str(exc)))
                            print(f"  [{i}/{len(plan)}] {label:<46} ! {exc}")
                        # V2-C4 (default off): as in paid_phase_c2's integrate.
                        paid_adaptive.integrated(unit_id, rows if ok else None, ok, n)
        if stopped_early:
            telemetry.paid_unvisited("skipped_budget")
        telemetry.mark("paid_phase_done")
        print(f"\nTotal Apify spend this run: ${spent:.2f}"
              + ("" if baseline is None else "  (billed to the account, not self-reported)"))
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
        telemetry.mark("free_phase_start")
        raw_rows.extend(fetch_free())
        telemetry.mark("free_phase_done")
        emit(raw_rows)                                          # checkpoint

    pulled = len(raw_rows)
    if pulled == 0:
        # Written before the exit, deliberately: a sweep that scraped nothing is
        # the run whose per-source failure categories are most worth having, and
        # the audit's whole complaint is that this case leaves no trace at all.
        telemetry.counts(normalized_rows=0, eligible_rows=0, final_rows=0)
        telemetry.paid_adaptive(paid_adaptive.finish(telemetry.record(), max_charge_usd))
        written = telemetry.finish()
        if written:
            print(f"  telemetry: {written}")
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

    # The user's result is final here: CSV and JSON renamed into place, the
    # ledger appended, and nothing below writes any of the three. Marked
    # whenever telemetry is on, so the tail after it is measured whether or
    # not it is hidden; published only under SWEEP_RESULTS_READY_EARLY. A
    # marker that cannot be written costs the early signal, never the sweep:
    # the worker still says done when this process exits.
    telemetry.mark("results_ready")
    if results_ready_early():
        try:
            publish_results_ready()
        except OSError as exc:
            print(f"  (results-ready marker not written: {exc})")

    # SWEEP_FREE_SOURCE_SHADOW (default off). Only now, with the CSV, the JSON
    # and the seen ledger all written: no shadow request is made while any
    # production output can still change. The eight boards are judged against
    # out_rows by this sweep's own rules, and only counts come back — into the
    # telemetry record; shadow.run returns None, so there is nothing here to
    # merge. Wrapped on top of shadow's own per-board and evaluation isolation.
    if run_free and shadow.enabled():
        print("\nshadow tranche (diagnostic only — not in the results above)")
        telemetry.note("Shadow tranche fetched after the results were written "
                       "and judged in isolation; its rows never reach them and "
                       "it is excluded from source counters.")
        try:
            shadow.run(is_dev_title, location_allowed, is_home_location,
                       engine=shadow_engine(out_rows, raw_rows))
        except Exception as exc:
            print(f"  (shadow tranche skipped: {exc})")

    # V2-C4 (default off): every candidate policy's decisions, priced against
    # the final result — after it is written, like the shadow tranche. The
    # last finalize pass above is the one it reads.
    telemetry.paid_adaptive(paid_adaptive.finish(telemetry.record(), max_charge_usd))

    # Identity BEFORE dedupe, over the complete accumulated row set: the audit
    # could only report the current heuristic's own output, never what it merged
    # away. job_key is PASSED rather than imported, so a later experiment can
    # measure an alternative key without touching this file.
    telemetry.identity(raw_rows, job_key)
    written = telemetry.finish()
    if written:
        print(f"  telemetry: {written}")


if __name__ == "__main__":
    main()
