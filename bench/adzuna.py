"""Adzuna search API -> Sweep's internal eight-field schema. MEASUREMENT ONLY.

Phase 1 of docs/superpowers/specs/2026-09-11-apify-independence-audit.md: find
out whether an aggregator search API can recover the employer-discovery function
we currently buy from Apify. Nothing here is wired into production, and nothing
in production imports it.

Credentials come from the environment, never from .env and never from an
argument default:
    ADZUNA_APP_ID, ADZUNA_APP_KEY      https://developer.adzuna.com/

Two deliberate refusals, both copied from scraper._build_linkedin_url's
discipline that a request which would return the wrong data must fail before it
is made rather than after:

  * An unmapped location RAISES. Adzuna takes the country as a PATH segment and
    the place as free text, so a name we have not mapped would silently search
    the wrong country -- the exact failure that made LinkedIn geoIds a hard
    error.
  * A PREDICTED salary is not a disclosed salary. Adzuna estimates a range when
    the posting states none and says so in salary_is_predicted. Feeding that to
    scraper.comp_max_usd would let a guess pass a pay filter, so predictions are
    counted and dropped, never written to the Salary field.

Self-check (offline, no network, no credentials needed):
    python -m bench.adzuna
"""
import json
import os
import time
import urllib.error
import urllib.parse

from sources._http import get_bytes
from sources.ats import BLANK

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
MAX_PER_PAGE = 50          # documented ceiling
MIN_INTERVAL = 1.0         # seconds between calls -- be a good citizen

ID_ENV, KEY_ENV = "ADZUNA_APP_ID", "ADZUNA_APP_KEY"

# Adzuna bills a country as a path segment, so a location has to resolve to one
# before anything is sent. `where` is free text inside that country; None means
# the whole country.
#
# "Remote" is an APPROXIMATION and is labelled as one everywhere it is reported.
# Adzuna exposes no workplace-type facet, and Sweep's "Remote" means LinkedIn's
# f_WT=2 applied to a geography (see profiles/kartik_reachable.py), so the
# closest honest query is the whole country -- which makes it the SAME query as
# "India". The replay dedupes identical resolved queries rather than paying for
# it twice, and remoteness is then read off the rows by enrich.py, which is
# where Sweep reads it for every other source anyway.
LOCATIONS = {
    "India":     ("in", None),
    "Remote":    ("in", None),      # approximation -- see above
    "Delhi":     ("in", "Delhi"),
    "New Delhi": ("in", "New Delhi"),
    "Gurgaon":   ("in", "Gurgaon"),
    "Noida":     ("in", "Noida"),
    "Bengaluru": ("in", "Bengaluru"),
    "Hyderabad": ("in", "Hyderabad"),
    "Pune":      ("in", "Pune"),
    "Mumbai":    ("in", "Mumbai"),
    "Chennai":   ("in", "Chennai"),
    "Chandigarh": ("in", "Chandigarh"),
    "United States":        ("us", None),
    "United Kingdom":       ("gb", None),
    "Canada":               ("ca", None),
    "Australia":            ("au", None),
    "Germany":              ("de", None),
    "Netherlands":          ("nl", None),
    "Singapore":            ("sg", None),
    "Ireland":              ("ie", None),
    "New Zealand":          ("nz", None),
    "Poland":               ("pl", None),
    "South Africa":         ("za", None),
    "Brazil":               ("br", None),
    "Mexico":               ("mx", None),
    "France":               ("fr", None),
    "Spain":                ("es", None),
    # Deliberately absent, so they raise instead of searching the wrong place:
    # United Arab Emirates, Japan, Switzerland, Sweden, Portugal -- Adzuna has
    # no market for them, and defaulting to another country would be the
    # LinkedIn geoId trap with a different name.
}

# Adzuna reports pay in the market's own currency; comp_max_usd reads the
# symbol/code out of the text, so name it explicitly rather than leaving a bare
# number to be guessed at.
CURRENCY = {"in": "INR", "us": "USD", "gb": "GBP", "ca": "CAD", "au": "AUD",
            "de": "EUR", "nl": "EUR", "sg": "SGD", "ie": "EUR", "nz": "NZD",
            "pl": "PLN", "za": "ZAR", "br": "BRL", "mx": "MXN", "fr": "EUR",
            "es": "EUR"}


class NoCredentials(RuntimeError):
    """Neither argument nor environment supplied an app id / key."""


class QuotaExhausted(RuntimeError):
    """Adzuna answered 429. The caller should stop, not retry harder."""


def credentials(app_id=None, app_key=None):
    app_id = (app_id or os.environ.get(ID_ENV) or "").strip()
    app_key = (app_key or os.environ.get(KEY_ENV) or "").strip()
    if not (app_id and app_key):
        raise NoCredentials(
            f"Set {ID_ENV} and {KEY_ENV} in the environment "
            f"(register at https://developer.adzuna.com/).")
    return app_id, app_key


def resolve(location):
    """'Bengaluru' -> ('in', 'Bengaluru'). Raises on anything unmapped."""
    key = (location or "").strip()
    if key not in LOCATIONS:
        raise ValueError(
            f"adzuna: no country mapping for {key!r}. The country is a PATH "
            f"segment, so an unmapped name would search the wrong market at "
            f"full quota. Add it to bench.adzuna.LOCATIONS.")
    return LOCATIONS[key]


def query_url(what, location, app_id, app_key, results=25, max_days_old=None,
              page=1):
    country, where = resolve(location)
    params = {"app_id": app_id, "app_key": app_key,
              "results_per_page": min(int(results), MAX_PER_PAGE),
              "what": what, "content-type": "application/json"}
    if where:
        params["where"] = where
    if max_days_old:
        params["max_days_old"] = int(max_days_old)
    return (API.format(country=country, page=page) + "?"
            + urllib.parse.urlencode(params))


def _salary(item, country):
    """Disclosed pay only, in the market's own currency, in the 'CUR lo-hi per
    year' form scraper.comp_max_usd parses. ('', True) when Adzuna predicted it."""
    if item.get("salary_is_predicted") in (1, "1", True):
        return "", True
    lo, hi = item.get("salary_min"), item.get("salary_max")
    if not (lo or hi):
        return "", False
    hi = hi or lo
    return f"{CURRENCY.get(country, 'USD')} {int(lo or 0)}-{int(hi)} per year", False


def row(item, country, source):
    """One Adzuna result -> Sweep's internal schema. Keys match sources.ats.BLANK
    exactly, so every downstream stage (score_job, enrich, job_key, finalize)
    reads it with no special case."""
    salary, predicted = _salary(item, country)
    out = dict(BLANK, Source=source)
    out["Title"] = (item.get("title") or "").strip()
    out["Company"] = ((item.get("company") or {}).get("display_name") or "").strip()
    out["Location"] = ((item.get("location") or {}).get("display_name") or "").strip()
    out["Salary"] = salary
    out["Posted Date"] = (item.get("created") or "")[:10]
    out["Job URL"] = item.get("redirect_url") or ""
    # Adzuna returns a SNIPPET, not the JD. Kept because score_job has nothing
    # else to read, but the replay reports its length so the gap is visible
    # rather than assumed away.
    out["Description"] = (item.get("description") or "").strip()
    out["_predicted_salary"] = predicted
    out["_category"] = ((item.get("category") or {}).get("label") or "")
    out["_id"] = str(item.get("id") or "")
    return out


def search(what, location, results=25, max_days_old=None, app_id=None,
           app_key=None, fetch=None, pause=True):
    """One (keyword, location) combo -> normalized rows. One request.

    `fetch` is injected for the offline self-check; the default is the same
    bounded-timeout, backoff-on-transient helper every free source uses.
    """
    app_id, app_key = credentials(app_id, app_key)
    country, _ = resolve(location)
    url = query_url(what, location, app_id, app_key, results, max_days_old)
    getter = fetch or get_bytes
    try:
        raw = getter(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise QuotaExhausted("adzuna: 429 -- free quota reached") from exc
        raise
    if pause:
        time.sleep(MIN_INTERVAL)
    data = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    source = f"adzuna:{country}"
    return [row(i, country, source) for i in (data.get("results") or [])]


def demo():
    """Offline: the field mapping and both refusals, with no network."""
    item = {
        "title": " Senior Full Stack Engineer ", "created": "2026-09-01T10:00:00Z",
        "company": {"display_name": "Acme Pvt Ltd"},
        "location": {"display_name": "Bengaluru, Karnataka", "area": ["India"]},
        "redirect_url": "https://www.adzuna.in/land/ad/123",
        "description": "React and Node.js...", "salary_min": 1800000,
        "salary_max": 2400000, "salary_is_predicted": 0,
        "category": {"label": "IT Jobs"}, "id": "123",
    }
    r = row(item, "in", "adzuna:in")
    assert r["Title"] == "Senior Full Stack Engineer", r["Title"]
    assert r["Company"] == "Acme Pvt Ltd"
    assert r["Location"] == "Bengaluru, Karnataka"
    assert r["Posted Date"] == "2026-09-01", r["Posted Date"]
    assert r["Job URL"].endswith("/123")
    assert r["Salary"] == "INR 1800000-2400000 per year", r["Salary"]
    assert r["_predicted_salary"] is False
    # Every key the rest of Sweep reads must exist, or a downstream stage
    # KeyErrors on a source-specific gap instead of seeing "not stated".
    assert set(BLANK) <= set(r), set(BLANK) - set(r)

    # A predicted salary must never reach the Salary field -- comp_ok would
    # treat Adzuna's guess as the employer's disclosure.
    pred = row({**item, "salary_is_predicted": 1}, "in", "adzuna:in")
    assert pred["Salary"] == "", pred["Salary"]
    assert pred["_predicted_salary"] is True

    # Undisclosed pay is blank, not zero.
    assert row({**item, "salary_min": None, "salary_max": None},
               "in", "adzuna:in")["Salary"] == ""

    # An unmapped location raises instead of guessing a market.
    for bad in ("United Arab Emirates", "Japan", "", "Atlantis"):
        try:
            resolve(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"resolve({bad!r}) should have raised")

    assert resolve("Remote") == resolve("India"), "Remote must dedupe onto India"

    url = query_url("Full Stack Developer", "Delhi", "id", "key", results=25,
                    max_days_old=30)
    assert url.startswith("https://api.adzuna.com/v1/api/jobs/in/search/1?"), url
    for expect in ("where=Delhi", "results_per_page=25", "max_days_old=30",
                   "what=Full+Stack+Developer"):
        assert expect in url, expect
    # The ceiling is the API's, not ours to exceed.
    assert "results_per_page=50" in query_url("x", "India", "i", "k", results=999)
    # Whole-country searches must not send an empty where=.
    assert "where=" not in query_url("x", "India", "i", "k")

    # Credentials are never defaulted into a request.
    try:
        credentials(None, None) if not os.environ.get(ID_ENV) else None
    except NoCredentials:
        pass

    # The transport is injectable, so the self-check never touches the network.
    payload = json.dumps({"results": [item]}).encode()
    rows = search("x", "India", app_id="i", app_key="k",
                  fetch=lambda u, **kw: payload, pause=False)
    assert len(rows) == 1 and rows[0]["Source"] == "adzuna:in"
    print("bench.adzuna: all self-checks pass")


if __name__ == "__main__":
    demo()
