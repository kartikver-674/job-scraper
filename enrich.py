"""International-remote signals, extracted from text we already have.

Zero extra requests: every source already ships a location and (mostly) a
description, and the four questions that decide whether a remote job is
actually reachable from India are answered in that prose:

    remote_scope  can I work from where I am, or is "remote" geo-locked?
    visa          will they sponsor, or do they explicitly refuse?
    eor           will they hire through an employer of record / as a contractor?
    timezones     do the required hours overlap with mine?

A bare "Remote" in a location field answers none of these, which is why it is
nearly useless to someone applying from outside the employer's country.

Everything here is best-effort text matching over messy prose, so it is wired
up as VISIBLE COLUMNS first and optional filters second: an empty string always
means "not stated", never "no", and the filters in config.SETTINGS default to
off. Run `python enrich.py` for the self-check.
"""
import re

# --- shared -----------------------------------------------------------------
def _any(patterns, text):
    return any(p.search(text) for p in patterns)


def _rx(*fragments):
    return [re.compile(f, re.I) for f in fragments]


# --- remote scope -----------------------------------------------------------
# Precedence matters more than the individual patterns here. A JD saying "fully
# remote, with occasional onsite meetups" is remote; one saying "hybrid, 3 days
# in office, occasional remote work" is not. Strong remote wording is therefore
# checked BEFORE the hybrid/onsite markers, and an explicit denial before both.
_NOT_REMOTE = _rx(
    r"\bnot\s+(?:a\s+)?(?:fully\s+)?remote\b",
    r"\bno\s+remote\b",
    r"\bremote\s+work\s+is\s+not\b",
    r"\bdoes\s+not\s+offer\s+remote\b",
    r"\bno\s+work\s+from\s+home\b",
)
_STRONG_REMOTE = _rx(
    r"\bfully\s+remote\b", r"\b100%\s*remote\b", r"\bremote[\s-]first\b",
    r"\bwork\s+from\s+anywhere\b", r"\banywhere\s+in\s+the\s+world\b",
    r"\bglobally\s+distributed\b", r"\bremote\s*[-–(]\s*global\b",
)
_HYBRID = _rx(
    r"\bhybrid\b",
    r"\b\d\s*days?\s+(?:a\s+week\s+)?(?:in|at)\s+(?:the\s+)?office\b",
    r"\bin[-\s]office\b",
)
_ONSITE = _rx(r"\bon[-\s]?site\b", r"\bin\s+person\b", r"\brelocat")
_REMOTE = _rx(r"\bremote\b", r"\bwork\s+from\s+home\b", r"\bwfh\b", r"\banywhere\b",
              r"\btelecommut")
_WORLDWIDE = _rx(r"\bworldwide\b", r"\banywhere\b", r"\bglobal\b",
                 r"\bany\s+time\s*zone\b", r"\bwork\s+from\s+anywhere\b")
# Phrases that geo-lock a remote role even when no region name is nearby.
_RESTRICTION = _rx(
    r"\bmust\s+(?:reside|be\s+(?:located|based))\b",
    r"\b(?:authoriz|authoris)ed\s+to\s+work\s+in\b",
    r"\beligible\s+to\s+work\s+in\b",
    r"\bresidents?\s+of\b",
    r"\bwithin\s+the\s+(?:us|uk|eu|united\s+states)\b",
)
# Region names worth reporting. Longest-first at match time so "united states"
# wins over a bare "us".
_REGIONS = {
    "united states": "US", "usa": "US", "u.s.": "US", "us": "US",
    "united kingdom": "UK", "uk": "UK", "canada": "Canada",
    "european union": "EU", "europe": "Europe", "eu": "EU",
    "emea": "EMEA", "apac": "APAC", "latam": "LATAM", "americas": "Americas",
    "india": "India", "germany": "Germany", "netherlands": "Netherlands",
    "australia": "Australia", "singapore": "Singapore", "ireland": "Ireland",
    "poland": "Poland", "spain": "Spain", "portugal": "Portugal",
}
_REGION_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(k) for k in
                                 sorted(_REGIONS, key=len, reverse=True)) + r")(?![a-z0-9])",
    re.I)


def regions(text):
    """Every region/country name mentioned, normalized and de-duplicated."""
    found = dict.fromkeys(_REGIONS[m.group(1).lower()]
                          for m in _REGION_RE.finditer(text or ""))
    return ", ".join(found)


# Words that make a nearby country name a HIRING constraint rather than trivia.
# "Remote (US, Canada)" and "open to candidates in EMEA" are restrictions;
# "you'll support our US customers" is not, and counting it would mark most
# worldwide roles as geo-locked.
_ANCHOR_RE = re.compile(
    r"\b(?:remote\w*|resid\w*|located|based|eligib\w*|authoriz\w*|authoris\w*"
    r"|work\s+from|hiring|hire|candidates?|applicants?|open\s+to|anywhere)\b", re.I)


def eligibility_regions(text, before=40, after=90):
    """Regions named NEAR eligibility wording — the ones that actually gate who
    can apply."""
    text = text or ""
    found = {}
    for m in _ANCHOR_RE.finditer(text):
        window = text[max(0, m.start() - before):m.end() + after]
        for region in regions(window).split(", "):
            if region:
                found[region] = None
    return ", ".join(found)


# Words a location field uses to say "remote" without naming a place. Strip
# these and any punctuation; whatever survives IS a place.
_LOCATION_FILLER = re.compile(
    r"\b(?:remote\w*|hybrid|on[-\s]?site|any\s*where|world\s*wide|world|global\w*"
    r"|distributed|flexible|work\s+from\s+home|wfh|full[-\s]?time|part[-\s]?time"
    r"|the|in|or|and|only)\b|[^\w\s]", re.I)


def names_a_place(location):
    """True when a location field names somewhere specific alongside "remote".

    This is the most reliable geo-lock signal there is, and it needs no country
    list: "New York, NY (HQ), Remote" and "Remote (Buenos Aires, Argentina)" are
    both restricted, while "Anywhere in the World" and ", Remote" are not. The
    eligibility prose in a JD is far less trustworthy — Ramp lists per-country
    benefits for US/Canada/UK deep in a benefits section, which reads like an
    eligibility rule to a regex and isn't one.
    """
    return bool(_LOCATION_FILLER.sub(" ", location or "").strip())


def _scope_of(text):
    if _any(_NOT_REMOTE, text):
        return "onsite"
    strong = _any(_STRONG_REMOTE, text)
    if not strong:
        if _any(_HYBRID, text):
            return "hybrid"
        if _any(_ONSITE, text):
            return "onsite"
        if not _any(_REMOTE, text):
            return ""
    # Remote of some kind — the only question left is how far it reaches.
    # Restriction wins over "anywhere": "anywhere in the US" is not worldwide.
    if _any(_RESTRICTION, text) or eligibility_regions(text):
        return "restricted"
    return "worldwide" if _any(_WORLDWIDE, text) else "remote"


def remote_scope(location, title="", description=""):
    """One of worldwide / remote / restricted / hybrid / onsite, or "" if the
    text says nothing either way.

    Location + title decide FIRST and the description is only a fallback: a
    structured "Anywhere in the World" beats prose, and "we're a remote-friendly
    company" in an About Us section should not make an office role look remote.

    The one exception is the "remote, scope unstated" verdict. A location like
    "New York, NY (HQ), Remote" is genuinely silent on who may apply, while the
    description goes on to say US/Canada/UK only — so that case, and only that
    case, is re-checked against the description before being called reachable.
    """
    scope = _scope_of(f"{location or ''} {title or ''}")
    if not scope:
        return _scope_of(description or "")
    if scope == "remote":
        if names_a_place(location):
            return "restricted"
        if description and (_any(_RESTRICTION, description)
                            or eligibility_regions(description)):
            return "restricted"
    return scope


REMOTE_SCOPES = ("worldwide", "remote", "restricted")   # scopes you can work from abroad


# --- visa sponsorship -------------------------------------------------------
# Refusals are checked first: "we do not offer visa sponsorship" contains the
# same words as the offer.
_VISA_NO = _rx(
    r"\b(?:no|not|unable|cannot|can\s*not|won'?t|do(?:es)?\s+not)\b[^.]{0,40}\bsponsor",
    r"\bsponsorship\s+is\s+not\b",
    r"\bwithout\s+(?:visa\s+)?sponsorship\b",
    r"\bno\s+visa\b",
)
_VISA_YES = _rx(
    r"\bvisa\s+(?:sponsorship|support)\b", r"\bsponsorship\s+available\b",
    r"\bwe\s+(?:can\s+)?sponsor\b", r"\bwill\s+sponsor\b",
    r"\bsponsorship\s+(?:is\s+)?(?:provided|offered)\b",
    r"\bwork\s+permit\s+(?:support|sponsorship)\b",
)


def visa(text):
    """"no" / "yes" / "" — "" means the posting never mentions it, which is the
    common case and must not be read as either answer."""
    text = text or ""
    if _any(_VISA_NO, text):
        return "no"
    return "yes" if _any(_VISA_YES, text) else ""


# --- employer of record -----------------------------------------------------
# Named providers are the reliable signal: a company that says "we hire globally
# through Deel" is stating it can pay someone in India without an entity there.
_EOR_TERMS = {
    "deel": "Deel", "remote.com": "Remote.com", "oyster": "Oyster",
    "velocity global": "Velocity Global", "globalization partners": "G-P",
    "papaya global": "Papaya Global", "multiplier": "Multiplier",
    "safeguard global": "Safeguard Global", "rippling": "Rippling",
    "employer of record": "EOR", "eor": "EOR",
    "independent contractor": "contractor", "contractor agreement": "contractor",
}
_EOR_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(re.escape(k) for k in
                                 sorted(_EOR_TERMS, key=len, reverse=True)) + r")(?![a-z0-9])",
    re.I)


def eor(text):
    """Employer-of-record providers / contractor wording mentioned, if any."""
    found = dict.fromkeys(_EOR_TERMS[m.group(1).lower()]
                          for m in _EOR_RE.finditer(text or ""))
    return ", ".join(found)


# --- timezone overlap -------------------------------------------------------
_TZ_RE = re.compile(
    r"(?:"
    r"\b(?:overlap|overlapping)[^.]{0,60}"
    r"|\bwithin\s+\d+\s*hours?\s+of\b[^.]{0,40}"
    r"|\b(?:UTC|GMT)\s*[+-]\s*\d{1,2}(?::\d{2})?"
    r"|\b\d+\s*(?:\+|plus)?\s*hours?\s+of\s+overlap\b"
    r"|\b(?:CET|CEST|PST|PDT|EST|EDT|IST|AEST|MST|CST)\b[^.]{0,30}"
    r"|\b(?:EMEA|APAC|AMER)\s+(?:business\s+)?hours\b"
    r")", re.I)


def timezones(text):
    """Timezone-overlap requirements as stated, de-duplicated and trimmed."""
    hits = dict.fromkeys(re.sub(r"\s+", " ", m.group(0)).strip(" ,;.")
                         for m in _TZ_RE.finditer(text or ""))
    return "; ".join(list(hits)[:3])       # 3 is plenty; JDs repeat themselves


# --- timezone distance -----------------------------------------------------
# Representative UTC offsets. Coarse on purpose: the question is "is this a 3-hour
# stretch or a 12-hour one", and DST or a country spanning zones never changes
# that answer. Named zones beat regions when both appear.
_ZONE_OFFSETS = {"utc": 0, "gmt": 0, "bst": 1, "cet": 1, "cest": 2, "eet": 2,
                 "est": -5, "edt": -4, "cst": -6, "mst": -7, "pst": -8, "pdt": -7,
                 "ist": 5.5, "sgt": 8, "jst": 9, "aest": 10}
_REGION_OFFSETS = {"US": -6, "Canada": -6, "Americas": -5, "LATAM": -4,
                   "UK": 0, "Ireland": 0, "Portugal": 0, "Europe": 1, "EU": 1,
                   "EMEA": 1, "Germany": 1, "Netherlands": 1, "Spain": 1,
                   "Poland": 1, "France": 1, "Italy": 1,
                   "India": 5.5, "APAC": 8, "Singapore": 8, "Australia": 10}
_ZONE_RE = re.compile(
    r"(?<![a-z0-9])(" + "|".join(_ZONE_OFFSETS) + r")(?![a-z0-9])", re.I)
_UTC_OFFSET_RE = re.compile(r"(?:utc|gmt)\s*([+-])\s*(\d{1,2})(?::(\d{2}))?", re.I)

# Gaps up to this many hours are workable without real cost; a 5.5h IST->CET
# stretch is a normal European remote arrangement, 12.5h to US Pacific is not.
TZ_FREE_HOURS = 5.0


def timezone_gap(regions_text, tz_text, home_offset):
    """Hours between home and the CLOSEST plausible required timezone, or None
    when the posting gives nothing to go on.

    Closest, not average: a role open to "US or EMEA" is reachable on its EMEA
    side, and scoring it by the US leg would bury a job you could actually take.
    """
    blob = f"{regions_text or ''} {tz_text or ''}"
    offsets = [float(f"{m.group(2)}.{'5' if m.group(3) == '30' else '0'}")
               * (1 if m.group(1) == "+" else -1)
               for m in _UTC_OFFSET_RE.finditer(blob)]
    # Blank out the explicit "UTC-8" spans before scanning for bare zone names:
    # the "UTC" inside one otherwise matches the zone table as offset 0, and
    # since we take the CLOSEST offset that spurious zero makes a 13.5h gap read
    # as 5.5h — understating exactly the distances that should disqualify a role.
    offsets += [_ZONE_OFFSETS[m.group(1).lower()]
                for m in _ZONE_RE.finditer(_UTC_OFFSET_RE.sub(" ", blob))]
    if not offsets:
        offsets = [_REGION_OFFSETS[r] for r in (regions_text or "").split(", ")
                   if r in _REGION_OFFSETS]
    if not offsets:
        return None
    return min(abs(home_offset - o) for o in offsets)


# --- one call for the pipeline ---------------------------------------------
def enrich(row, home_offset=None):
    """Add the signal fields to a normalized row, in place. Returns it."""
    location = row.get("Location") or ""
    title = row.get("Title") or ""
    description = row.get("Description") or ""
    body = f"{location}\n{title}\n{description}"
    scope = remote_scope(location, title, description)
    row["remote_scope"] = scope
    # Description-derived regions are only reported when the role might actually
    # be gated. On an explicitly worldwide role they are market or benefits
    # trivia, and "worldwide" next to "regions: UK" just reads as a contradiction.
    row["remote_regions"] = eligibility_regions(f"{location} {title}") or (
        "" if scope == "worldwide" else eligibility_regions(description))
    row["visa"] = visa(body)
    row["eor"] = eor(body)
    # A source that ships real timezone data (Himalayas reports UTC offsets)
    # beats anything read out of prose, so never overwrite what it set.
    row["timezones"] = row.get("timezones") or timezones(description)
    row["tz_gap"] = ("" if home_offset is None else
                     timezone_gap(row["remote_regions"], row["timezones"] + " " + location,
                                  home_offset))
    if row["tz_gap"] is None:
        row["tz_gap"] = ""
    return row


def demo():
    """Self-check. `python enrich.py` — offline, no network."""
    # -- scope precedence: the cases a bare keyword match gets wrong ----------
    assert remote_scope("Berlin", "Engineer",
                        "This role is not remote; hybrid 3 days onsite.") == "onsite"
    assert remote_scope("Berlin", "Engineer",
                        "Hybrid: 3 days a week in the office.") == "hybrid"
    # "Fully remote" says remote, not *worldwide* — don't upgrade what wasn't said.
    assert remote_scope("", "Engineer",
                        "Fully remote, with occasional onsite meetups.") == "remote"
    assert remote_scope("Anywhere in the World", "Backend Engineer") == "worldwide"
    assert remote_scope("Europe, Remote", "Engineer") == "restricted"
    assert remote_scope("United States, Remote", "Engineer") == "restricted"
    assert remote_scope("", "Engineer", "Remote, but you must reside in Canada.") == "restricted"
    assert remote_scope("", "Engineer", "Remote anywhere in the US.") == "restricted"
    assert remote_scope(", Remote", "Backend Developer") == "remote"
    # A plain office location with no remote wording anywhere: "" (not stated),
    # never "restricted" — restricted means "remote, but geo-locked".
    assert remote_scope("Bengaluru, India", "Engineer") == ""
    assert remote_scope("", "Engineer", "Great team, competitive pay.") == ""
    # KNOWN CEILING, asserted so it can't change silently: an office location is
    # not itself evidence of onsite, so we fall through to the description and
    # "we are a remote-friendly company" boilerplate reads as remote.
    # ponytail: accepted false positive; fix by scoring the JD's remote wording
    # by section (requirements vs About Us) if this shows up in real results.
    assert remote_scope("Munich Office", "Engineer",
                        "We are a remote-friendly company.") == "remote"
    # Real Ashby data. Scoring these "remote" put US-only roles on an India
    # shortlist; the location names a place, so they are geo-locked.
    assert remote_scope("New York, NY (HQ), Remote", "Software Engineer") == "restricted"
    assert remote_scope("Remote (Buenos Aires, Argentina)", "Engineer") == "restricted"
    assert remote_scope("San Francisco, CA, Remote", "Engineer") == "restricted"
    assert remote_scope("New York, NY (HQ), Remote", "Software Engineer",
                        "You can work remotely from the US, Canada, or the UK.") \
        == "restricted"
    # No place named -> genuinely unstated scope, not a geo-lock.
    assert names_a_place("Anywhere in the World") is False
    assert names_a_place(", Remote") is False
    assert names_a_place("Remote - Worldwide") is False
    assert names_a_place("New York, NY (HQ), Remote") is True
    assert names_a_place("Remote (Buenos Aires, Argentina)") is True
    # ...but a genuinely worldwide role keeps its scope even if the description
    # name-drops countries, so the fix can't quietly geo-lock everything.
    assert remote_scope("Anywhere in the World", "Engineer",
                        "You'll support our US and India customers.") == "worldwide"
    assert regions("Remote - EMEA or United States") == "EMEA, US"
    # Only country names near eligibility wording count as a restriction.
    assert eligibility_regions("Remote in the US only.") == "US"
    assert eligibility_regions("You will support our customers in Germany.") == ""

    # -- visa: refusal must win over the offer wording -----------------------
    assert visa("We are unable to sponsor visas for this role.") == "no"
    assert visa("Please note: no visa sponsorship is available.") == "no"
    assert visa("Candidates must be authorized to work without sponsorship.") == "no"
    assert visa("Visa sponsorship and relocation support provided.") == "yes"
    assert visa("We will sponsor the right candidate.") == "yes"
    assert visa("Competitive salary and equity.") == ""

    # -- eor -----------------------------------------------------------------
    assert eor("We hire globally through Deel.") == "Deel"
    assert eor("Employment via our employer of record, Oyster.") == "EOR, Oyster"
    assert eor("You will be engaged as an independent contractor.") == "contractor"
    assert eor("We use Redis and Postgres.") == ""

    # -- timezones -----------------------------------------------------------
    assert "overlap" in timezones("Requires 4 hours of overlap with CET.").lower()
    assert timezones("UTC+2 preferred") == "UTC+2"
    assert timezones("No timing requirements listed.") == ""

    # -- timezone distance from IST (5.5) ------------------------------------
    IST = 5.5
    assert timezone_gap("Europe", "", IST) == 4.5          # workable
    assert timezone_gap("UK", "", IST) == 5.5
    assert timezone_gap("US", "", IST) == 11.5             # brutal
    assert timezone_gap("APAC", "", IST) == 2.5
    assert timezone_gap("India", "", IST) == 0.0
    assert timezone_gap("", "overlap with CET", IST) == 4.5
    assert timezone_gap("", "UTC+2", IST) == 3.5
    # Explicit offsets, as Himalayas reports them. The bare "UTC" inside these
    # must NOT also count as offset 0 — that made a 13.5h gap read as 5.5h.
    assert timezone_gap("", "UTC-8", IST) == 13.5
    assert timezone_gap("", "UTC+5:30", IST) == 0.0          # half-hour zones
    assert timezone_gap("", "UTC-10 UTC-9 UTC-8", IST) == 13.5
    assert timezone_gap("", "", IST) is None               # nothing stated
    # A named zone beats a region guess when both appear.
    assert timezone_gap("US", "overlap with CET required", IST) == 4.5
    # "US or EMEA" is reachable on its EMEA side, so score the CLOSEST leg —
    # averaging or taking the worst would bury a job that is actually takeable.
    assert timezone_gap("US, EMEA", "", IST) == 4.5

    # -- whole-row wiring ----------------------------------------------------
    row = enrich({"Location": "Anywhere in the World", "Title": "Backend Engineer",
                  "Description": "Fully remote. We hire through Deel. "
                                 "Visa sponsorship available. Overlap with CET required."})
    assert row["remote_scope"] == "worldwide", row
    assert row["visa"] == "yes" and row["eor"] == "Deel", row
    assert "CET" in row["timezones"], row
    assert enrich({})["remote_scope"] == ""        # empty row must not explode
    # A worldwide role must not also report gating regions — that reads as a
    # contradiction, and on real WWR rows the regions came from benefits prose.
    wide = enrich({"Location": "Anywhere in the World", "Title": "Engineer",
                   "Description": "Remote. Our UK team handles billing."})
    assert (wide["remote_scope"], wide["remote_regions"]) == ("worldwide", ""), wide
    narrow = enrich({"Location": "Remote", "Title": "Engineer",
                     "Description": "Open to candidates in the US and Canada."})
    assert (narrow["remote_scope"], narrow["remote_regions"]) \
        == ("restricted", "US, Canada"), narrow
    print("demo ok")


if __name__ == "__main__":
    demo()
