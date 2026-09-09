"""
Résumé PDF -> profiles/<name>.py, in one Gemini call.

The scripted replacement for RESUME_AUTOCONFIG_PROMPT.md: same judgment, no
human in a chat window. The model returns JSON only and NEVER writes Python —
this file renders the profile from a template, so a malformed answer is a
KeyError here rather than a syntax error in the config the scraper imports.

    python auto-apply/make_profile.py --name kanav \
        --locations "Bengaluru,Remote" --min-comp-usd 20000 \
        --avoid "Salesforce,CRM" --exclude-levels "intern,fresher,junior"

Preferences a résumé cannot state (locations, pay floor, avoid-list, seniority
band) come from those flags, never from the model — a missing flag is an error,
not a guess. Everything else is inferred from the résumé text.

Verify the result at zero cost before spending:

    python scraper.py --profile <name> --dry-run
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apply_config as cfg
import resume_parser
import tailor

from google.genai import types

# The two rules that make or break the output, both learned from live failures
# documented in RESUME_AUTOCONFIG_PROMPT.md. Keep them together: they are the
# entire reason this is a model call and not a keyword dump.
SYSTEM_INSTRUCTION = (
    "You configure a job scraper from a candidate's résumé. Infer their real "
    "field, seniority and skills from the résumé text alone — do not assume "
    "software or web development, and never output a skill the résumé does not "
    "state.\n\n"
    "RULE 1 — WEIGHT BY DISCRIMINATIVE POWER, NOT CENTRALITY, ON A 1-5 SCALE. "
    "These are different things and confusing them is the main way this config "
    "goes wrong. Ask of every term: 'would this word also appear in a job this "
    "person does NOT want?' If yes, weight it LOW however core it is to their "
    "career. Terms naming their specific platform/domain/niche take 5; "
    "transferable craft vocabulary they share with adjacent fields gets 1, "
    "even when it is genuinely their top skill. A real failure: for a "
    "Salesforce consultant, 'requirement gathering', 'BRD', 'UAT' and "
    "'stakeholder management' were weighted mid-scale because they were her "
    "strongest skills — an 'Oracle Fusion Functional Consultant' then ranked "
    "#1, because every business-analyst posting on earth contains those words "
    "while only the right ones contain 'Salesforce'. Use 1-5 and nothing "
    "higher: every existing profile is on that scale, and score thresholds are "
    "compared across profiles.\n\n"
    "COVER THE SPELLINGS. Terms are matched literally on word boundaries, so "
    "'node.js' does NOT match a posting that says 'Node'. Emit every common "
    "spelling and abbreviation of each skill as its own entry at the same "
    "weight ('node' AND 'node.js'; 'react.js' AND 'react'; 'postgres' AND "
    "'postgresql'). Aim for 40+ skill_weights entries — thin coverage silently "
    "scores real matches at zero.\n\n"
    "RULE 2 — domain_title_terms flags a job as a match from the TITLE ALONE, "
    "bypassing every other piece of evidence, so every entry MUST name the "
    "platform or domain and NEVER a bare job function. 'salesforce business "
    "analyst' is correct; 'business analyst' leaks to 'Business Analyst "
    "(Italian)' and 'Japanese Business Analyst'. Same rule for the two halves: "
    "keep generic category words out of them, or any adjacent-industry job "
    "satisfies a half for free.\n\n"
    "RULE 3 — title_hints is the gate on every FREE source. Company boards and "
    "job feeds return their whole catalogue and scraper.is_dev_title() drops "
    "any posting whose title contains none of these, before it is scored, so a "
    "missing fragment is inventory nobody ever sees. Emit lowercase fragments "
    "as they appear INSIDE real job titles for this person's field, matched as "
    "substrings: 'salesforce', 'crm consultant', 'apex' for a Salesforce "
    "consultant; 'data engineer', 'analytics engineer' for a data engineer. "
    "Include the seniority-free stem ('developer', not 'senior developer'), "
    "every stack they could be hired for, and the adjacent titles their "
    "experience genuinely qualifies them for. Twenty to forty entries. They are "
    "added to a generic software list, so this can only widen the search — "
    "err towards including a title. title_exclude wins over both and is for "
    "DIFFERENT CAREERS that borrow the same words ('data engineer' for a web "
    "developer), never for seniority.\n\n"
    "The two halves (domain_half_a / domain_half_b) encode a 'job mentions "
    "both sides of my field' bonus — frontend+backend for a web developer. If "
    "the candidate's field has no such natural split, return both halves empty "
    "and domain_bonus 0, and say so in notes."
)

# No `additionalProperties` in the Gemini Developer API, so the two term->weight
# maps travel as arrays of {term, weight} and are folded into dicts below.
_WEIGHTED_LIST = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"term": {"type": "string"}, "weight": {"type": "integer"}},
        "required": ["term", "weight"],
    },
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        # The person's own name, used to prefill the profile name on Sweep's
        # review screen. Empty string when the résumé does not state one.
        "candidate_name": {"type": "string"},
        "field_summary": {"type": "string"},
        "years_experience": {"type": "integer"},
        "role_keywords": {"type": "array", "items": {"type": "string"}},
        "skill_weights": _WEIGHTED_LIST,
        # Positive severity 1-12; negated at render time so the model can never
        # get the sign backwards and silently turn a penalty into a bonus.
        "penalty_terms": _WEIGHTED_LIST,
        "domain_half_a": {"type": "array", "items": {"type": "string"}},
        "domain_half_b": {"type": "array", "items": {"type": "string"}},
        "domain_title_terms": {"type": "array", "items": {"type": "string"}},
        # The free-source title gate (config.ATS_TITLE_HINTS). Substring
        # fragments, lowercase — see RULE 3.
        "title_hints": {"type": "array", "items": {"type": "string"}},
        "title_exclude": {"type": "array", "items": {"type": "string"}},
        "domain_bonus": {"type": "integer"},
        "notes": {"type": "string"},
    },
    "required": [
        "candidate_name",
        "field_summary", "years_experience", "role_keywords", "skill_weights",
        "penalty_terms", "domain_half_a", "domain_half_b", "domain_title_terms",
        "title_hints", "title_exclude", "domain_bonus", "notes",
    ],
}


# A profile name is both a filename and an import path (config.py does
# importlib.import_module(f"profiles.{PROFILE}")) and is validated by
# sweep.logic._NAME_RE — [A-Za-z_][A-Za-z0-9_-]*. Everything below has to land
# inside that, or this would prefill the very form that rejects it.
_SLUG_STRIP = re.compile(r"[^a-z0-9_-]+")
_SLUG_RUNS = re.compile(r"_{2,}")


def profile_name_for(data):
    """A default profile name from the résumé's own candidate_name.

    Returns "" when there is nothing usable, and the caller falls back to an
    empty field for the user to fill — which is what the review screen did
    for every résumé before this.
    """
    raw = (data.get("candidate_name") or "").strip().lower()
    # Accents fold to their base letter instead of being stripped: "María
    # Peña" would otherwise slug to mar_a_pe_a. Scripts that do not decompose
    # to ASCII at all (CJK, for one) still come back empty and get typed by
    # hand, which is honest — there is no transliteration here worth trusting
    # with someone's name.
    raw = "".join(c for c in unicodedata.normalize("NFKD", raw)
                  if not unicodedata.combining(c))
    slug = _SLUG_RUNS.sub("_", _SLUG_STRIP.sub("_", raw)).strip("_-")
    # The first character has to be a letter or underscore. A name that
    # transliterates to digits, or to nothing at all, gets typed by hand
    # rather than silently turned into an invalid module name.
    if not slug or not (slug[0].isalpha() or slug[0] == "_"):
        return ""
    # Truncated for a sane filename, and never left ending on a separator.
    return slug[:40].rstrip("_-")


def build_prompt(resume_text, prefs):
    """Assemble the user-content prompt from résumé text and the CLI preferences."""
    return (
        "=== RÉSUMÉ (the only source of truth about this person) ===\n"
        f"{resume_text}\n\n"
        "=== STATED PREFERENCES (already handled; use only as context) ===\n"
        f"Locations: {', '.join(prefs['locations'])}\n"
        f"Wants to avoid: {', '.join(prefs['avoid']) or '(nothing stated)'}\n"
        f"Seniority levels to exclude: {', '.join(prefs['exclude_levels']) or '(none)'}\n\n"
        "Produce the scraper configuration. role_keywords are the job titles "
        "this person should actually be searched for. Include the avoid-list in "
        "penalty_terms alongside any technology obviously off-domain for their "
        "field. title_hints are lowercase fragments that appear INSIDE job "
        "titles in their field, matched as substrings — they are the gate on "
        "every free company board, so a missing one is inventory nobody sees. "
        "candidate_name is the person's own name exactly as the résumé "
        "writes it, or an empty string if it does not state one — never a "
        "guess from an email address or a file name."
    )


class ModelAnswerError(RuntimeError):
    """The call succeeded and the answer was unusable.

    Carries a SHORT reason composed here, from the response's own finish_reason
    enum — never str(exc) from the client library, which can carry the request
    URL. That rule is why the UI could only ever say "the reason is in the
    terminal"; this is what lets it say which failure it was instead.
    """


# gemini-3.6-flash reasons before it answers, and thinking tokens are charged
# against the SAME budget as the answer. With no ceiling set, the default
# applies — and a response that spends it thinking finishes with MAX_TOKENS and
# no text part at all, which reaches json.loads as None. Set explicitly and
# generously: this is a ceiling, not a reservation, so a larger one costs
# nothing on a response that does not need it. The JSON this schema asks for
# runs ~1.5k tokens.
MAX_OUTPUT_TOKENS = 16384


def _unusable(response):
    """Why an answer could not be read, or None if it can be.

    The vocabulary is the library's FinishReason enum, so nothing the model or
    a server wrote is echoed.
    """
    text = response.text
    if text:
        return None
    candidates = response.candidates or []
    reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    name = getattr(reason, "name", None) or str(reason or "no reason given")
    if name == "MAX_TOKENS":
        return ("the model ran out of output budget before it answered — "
                f"raise MAX_OUTPUT_TOKENS (currently {MAX_OUTPUT_TOKENS})")
    if name in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"):
        return f"the model refused to answer ({name.lower()})"
    return f"the model returned no answer ({name})"


class QuotaExhausted(ModelAnswerError):
    """Every model on the ladder is out of daily requests."""


# Free-tier RPD is counted per model and resets on a clock, not a rolling
# window: "Requests per day (RPD) quotas reset at midnight Pacific time"
# (ai.google.dev/gemini-api/docs/rate-limits, checked 2026-09-09). So the wait
# is answerable exactly, and "try again later" is a worse answer than the time.
_QUOTA_TZ = "America/Los_Angeles"


def quota_reset(now=None):
    """(when it resets in local time, how long that is) as strings."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    pacific = ZoneInfo(_QUOTA_TZ)
    now = now or datetime.now(pacific)
    now = now.astimezone(pacific)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    left = midnight - now
    hours, minutes = divmod(int(left.total_seconds()) // 60, 60)
    # Rendered in the reader's OWN timezone: the quota is Pacific, the person
    # waiting for it is not.
    local = midnight.astimezone()
    return (local.strftime("%H:%M %Z").strip(),
            f"{hours}h {minutes:02d}m" if hours else f"{minutes}m")


# A model is out of daily requests, or is not a model any more. Both mean "try
# the next one" rather than "give up" — and neither is retryable on the same
# model, unlike the 503s below.
_SPENT = ("resource_exhausted", "429", "quota")
_GONE = ("not_found", "404", "no longer available")
_BUSY = ("503", "unavailable", "overloaded")


def _is(exc, needles):
    low = str(exc).lower()
    return any(n in low for n in needles)


def generate(client, models, resume_text, prefs, attempts=5, sleep=time.sleep,
             log=print):
    """One structured Gemini call, down a ladder of models.

    `models` is a model id or a sequence of them, tried in order. A model whose
    DAILY quota is spent (429) or that no longer exists (404) is skipped — RPD
    is counted per model, so the next one has its own budget. The transient
    503s are retried on the SAME model first, since they are not about which
    model was asked.

    Raises QuotaExhausted, naming when the quota comes back, only when every
    model on the ladder is spent.
    """
    if isinstance(models, str):
        models = (models,)
    models = tuple(models)
    spent, last = [], None
    for index, model in enumerate(models):
        try:
            return _generate_one(client, model, resume_text, prefs, attempts,
                                 sleep, log)
        except Exception as exc:
            last = exc
            if _is(exc, _SPENT):
                spent.append(model)
                why = "daily quota spent"
            elif _is(exc, _GONE):
                why = "not a model any more"
            elif _is(exc, _BUSY):
                # Its own retries are already spent by here. The endpoint
                # being busy is not about which model was asked, so another
                # one is worth a try — but it is not an exhausted quota, and
                # the error at the end must not claim it is.
                why = "still overloaded after retrying"
            else:
                # An auth failure, a bad schema, an unusable answer: every
                # model on the ladder fails it identically, so walking them
                # spends calls to reach the same place.
                raise
            if index + 1 < len(models):
                log(f"  {model}: {why} — falling back to {models[index + 1]}")
    if not spent:
        # Nothing was exhausted; whatever actually stopped the last model is
        # the truth, and the CLI's own 503 branch still reads it.
        raise last
    at, left = quota_reset()
    raise QuotaExhausted(
        f"every model is out of requests for today ({', '.join(spent)}). "
        f"The free tier resets at midnight Pacific — {at} your time, about {left} from now")


def _generate_one(client, model, resume_text, prefs, attempts, sleep, log):
    """One structured Gemini call, retried on the transient 503s this API throws."""
    prompt = build_prompt(resume_text, prefs)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.2,
    )
    for attempt in range(attempts):
        try:
            response = client.models.generate_content(
                model=model, contents=prompt, config=config)
            # response.text is None when the response carries no text part —
            # a refusal, or a budget spent on thinking. json.loads(None) then
            # raises a TypeError that reads as "the résumé is unreadable".
            unusable = _unusable(response)
            if unusable:
                raise ModelAnswerError(unusable)
            try:
                return json.loads(response.text)
            except json.JSONDecodeError:
                # Truncation lands here instead: a partial answer is still text.
                raise ModelAnswerError(
                    "the model's answer was cut off before it was valid JSON — "
                    f"raise MAX_OUTPUT_TOKENS (currently {MAX_OUTPUT_TOKENS})"
                ) from None
        except Exception as exc:
            # 503 UNAVAILABLE ("high demand") is common on this endpoint — it
            # hit 2 of 6 calls while this was written, then exhausted a 3-try
            # budget on the first real run, which is why the budget is 5 with a
            # widening back-off. Anything else is a real error: fail loudly
            # rather than burning the budget on a retired model's 404.
            if attempt == attempts - 1 or "503" not in str(exc):
                raise
            log(f"  503 from {model}, retrying in {5 * (attempt + 1)}s "
                f"({attempt + 1}/{attempts - 1})")
            sleep(5 * (attempt + 1))


def _weights(entries, sign=1):
    """Fold [{term, weight}] into {term: weight}, lowercased, sign applied."""
    return {e["term"].strip().lower(): sign * abs(int(e["weight"])) for e in entries
            if e["term"].strip()}


def _load_config():
    """Import config.py with sys.argv neutralised — it reads --profile from
    argv at import time, so importing it under a test runner's own argv (or
    while JOB_PROFILE is set for an unrelated run) would pick the wrong
    profile or exit outright. Shared by validate_keys() and render(), which
    both need the live config as the single source of truth."""
    argv, job_profile = sys.argv, os.environ.pop("JOB_PROFILE", None)
    sys.argv = [argv[0]]
    try:
        # Inserted once, not per call: this runs on every /estimate and every
        # /run, and repeating it grew sys.path by an entry each time — the
        # third instance of a defect fixed twice elsewhere.
        if cfg.REPO_ROOT not in sys.path:
            sys.path.insert(0, cfg.REPO_ROOT)
        import config
        return config
    finally:
        sys.argv = argv
        if job_profile is not None:
            os.environ["JOB_PROFILE"] = job_profile


def validate_keys(rendered_keys):
    """Reject any config key that config.py does not actually define. Returns
    the live config module so callers that already paid for this import (i.e.
    render()) don't have to import it a second time.

    Profile merge is a plain dict.update(), so a misspelled key is accepted in
    silence and does nothing — which is how RESUME_AUTOCONFIG_PROMPT.md came to
    name `SCORING.drop_terms` and `SETTINGS.min_ctc_lpa`, neither of which has
    ever existed. Checked against the live config so it cannot drift again.
    """
    config = _load_config()
    known = {"SEARCH": config.SEARCH, "SETTINGS": config.SETTINGS,
             "SCORING": config.SCORING, "SITES": config.SITES}
    unknown = [f"{section}.{key}" for section, keys in rendered_keys.items()
               for key in keys if key not in known[section]]
    if unknown:
        raise KeyError(f"not real config keys: {', '.join(unknown)}")
    return config


def _fmt(value, indent=8):
    """Render a dict or list as readable, line-wrapped Python source."""
    pad = " " * indent
    if isinstance(value, dict):
        body = "".join(f"{pad}{k!r}: {v!r},\n" for k, v in value.items())
    else:
        body = "".join(f"{pad}{v!r},\n" for v in value)
    open_, close = ("{", "}") if isinstance(value, dict) else ("[", "]")
    return f"{open_}\n{body}{' ' * (indent - 4)}{close}" if value else f"{open_}{close}"


def _fmt_sites(overlay):
    """Render a SITES overlay as profile source.

    Each site's WHOLE dict is written out, because config._overlay merges one
    level deep only (SITES.update(override)) — so a partial entry like
    {"enabled": False} replaces the real one and takes "actor" and naukri's
    "results_per_run" with it. Harmless while the site is off, a broken or
    silently re-priced run the moment someone flips it back on by hand.
    """
    out = "SITES = {\n"
    for site in sorted(overlay):
        out += f'    "{site}": {{\n'
        for key in sorted(overlay[site]):
            value = overlay[site][key]
            rendered = (_fmt(value, indent=12) if isinstance(value, list)
                        else repr(value))
            out += f'        "{key}": {rendered},\n'
        out += "    },\n"
    return out + "}\n\n"


def _title_gate(data, config):
    """(hints, excludes) for the free-source title gate.

    UNIONED with config.ATS_TITLE_HINTS, never replacing it: the gate decides
    what a company board is even scored on, and a thin or eccentric model
    answer must not be able to make a profile see LESS than the generic
    software floor. Measured on five live greenhouse boards (2,567 open jobs,
    2026-09-09), the floor alone admits 33% of them; the old 21-entry floor
    admitted 16%.

    Excludes are NOT unioned with anything — they delete, so only what the
    model asked for is honoured.
    """
    hints = {str(t).strip().lower() for t in data.get("title_hints") or []}
    hints |= set(config.ATS_TITLE_HINTS)
    excludes = {str(t).strip().lower() for t in data.get("title_exclude") or []}
    # A term on both lists would delete itself: exclude wins in is_dev_title.
    return sorted(h for h in hints if h and h not in excludes), sorted(excludes)


def _fmt_feeds(queries):
    """A FEEDS overlay carrying this résumé's himalayas search terms.

    The WHOLE himalayas entry is written for the reason _fmt_sites spells out:
    config._overlay merges one level deep (FEEDS.update(override)), so a
    partial {"himalayas": {"queries": [...]}} replaces the real entry and takes
    "enabled" and "pages" with it — leaving the feed silently off.

    Only this one feed is written. The others are absent from the overlay, so
    dict.update leaves config.py's own entries in place.
    """
    if not queries:
        return ""
    return ("FEEDS = {\n"
            '    "himalayas": {\n'
            '        "enabled": True,\n'
            '        "pages": 10,\n'
            f'        "queries": {_fmt(queries, indent=12)},\n'
            "    },\n"
            "}\n\n")


def render(name, data, prefs):
    """Render profiles/<name>.py source from the model's JSON and the preferences.

    max_spend_usd / max_results / max_age_days / remote_scopes /
    linkedin_locations are
    optional overrides (from Sweep's Configure screen, sweep/app.py) —
    omitted from `prefs` (None), they are left out of the rendered section
    entirely so config.py's own default silently applies, per the
    one-level-deep profile merge in config.py's PROFILES section. A profile
    must never widen the sweep by accident, so "not set" has to mean
    "inherit", not "reset to some default picked here".
    """
    sections = {
        "SEARCH": ["role_keywords", "experience_years", "locations", "salary_min",
                   "max_results"],
        "SETTINGS": ["max_experience_years", "min_comp_usd", "max_age_days",
                     "remote_scopes", "max_spend_usd"],
        "SCORING": ["skill_weights", "penalty_terms", "frontend_terms",
                    "backend_terms", "fullstack_title_terms", "fullstack_bonus",
                    "hard_drop_terms"],
        "SITES": ["linkedin", "indeed", "naukri"],
    }
    config = validate_keys(sections)

    years = int(data["years_experience"])
    skills = _weights(data["skill_weights"])

    extra_search = (f'    "max_results": {int(prefs["max_results"])!r},\n'
                     if prefs.get("max_results") is not None else "")
    extra_settings = ""
    # The only real spend guard in the system: scraper.py:1748 re-reads the
    # account after every search and stops launching once this is crossed.
    # config.py defaults it to None and that check is `if budget is not None`,
    # so a profile that omits this key has NO cap — which is why Sweep always
    # passes one. Omitted here still means "inherit from config.py", the same
    # rule as every other optional key: never emit None, which would write the
    # no-cap value in and read as a deliberate choice.
    if prefs.get("max_spend_usd") is not None:
        extra_settings += (
            f'    "max_spend_usd": {float(prefs["max_spend_usd"])!r},\n')
    if prefs.get("max_age_days") is not None:
        extra_settings += f'    "max_age_days": {int(prefs["max_age_days"])!r},\n'
    if prefs.get("remote_scopes") is not None:
        extra_settings += f'    "remote_scopes": {_fmt(prefs["remote_scopes"])},\n'

    # SITES[site].get("locations", SEARCH["locations"]) means LinkedIn — the
    # most expensive site — keeps searching whatever config.py's SITES.linkedin
    # already says (India + Remote) no matter what SEARCH.locations above is
    # set to, unless this profile overrides it too (see scraper.plan_for_site).
    # A wrong or invented LinkedIn location doesn't error, it silently returns
    # US results and bills in full (see config.LINKEDIN_GEO_IDS), so every
    # location here is checked against that VERIFIED table — never passed
    # through on trust, and never trusted just because it came from this
    # app's own code instead of a form.
    overlay = {}
    if prefs.get("linkedin_locations") is not None:
        locations = list(prefs["linkedin_locations"])
        # A bare "Remote" isn't itself a LINKEDIN_GEO_IDS entry — it is a
        # special token _build_linkedin_url resolves via remote_geo (f_WT=2
        # filters workplace type WITHIN that one geography). So it is
        # verified differently: remote_geo itself has to be a real,
        # verified geoId, not "Remote" against the geography table.
        non_remote = [loc for loc in locations if loc.lower() != "remote"]
        unverified = [loc for loc in non_remote if loc not in config.LINKEDIN_GEO_IDS]
        if unverified:
            raise KeyError(
                f"not a verified LinkedIn geography: {', '.join(unverified)} "
                f"— add to config.LINKEDIN_GEO_IDS and confirm with "
                f"`python verify_geoids.py` before using it here.")
        # The whole SITES["linkedin"] dict is REPLACED, not deep-merged (see
        # config._overlay), so "enabled"/"actor"/"remote_geo" have to be
        # carried forward explicitly or the profile would silently switch
        # LinkedIn off (or leave a bare "Remote" location with no region).
        linkedin_site = dict(config.SITES["linkedin"])
        if len(non_remote) < len(locations):
            remote_geo = linkedin_site.get("remote_geo")
            if not remote_geo or remote_geo not in config.LINKEDIN_GEO_IDS:
                raise KeyError(
                    f"config.SITES['linkedin']['remote_geo'] is "
                    f"{remote_geo!r}, not a verified LinkedIn geography — "
                    f"a bare 'Remote' location needs one to mean anything "
                    f"(see scraper._build_linkedin_url).")
        linkedin_site["locations"] = locations
        linkedin_site["remote_only"] = bool(prefs.get("linkedin_remote_only"))
        overlay["linkedin"] = linkedin_site

    # Per-site on/off from Configure's Sources panel. Applied after the
    # LinkedIn block so switching LinkedIn off still keeps the locations it
    # would search if switched back on, rather than the two settings
    # clobbering each other depending on which ran last.
    for site, on in (prefs.get("sites_enabled") or {}).items():
        overlay.setdefault(site, dict(config.SITES[site]))["enabled"] = bool(on)

    extra_sites = _fmt_sites(overlay) if overlay else ""
    hints, excludes = _title_gate(data, config)
    # The résumé's own role keywords are what himalayas is searched for; its
    # search endpoint takes one free-text query per request.
    extra_feeds = _fmt_feeds([k for k in data["role_keywords"] if str(k).strip()])

    spend_note = (
        f'Spending stops at ${float(prefs["max_spend_usd"]):.2f}: '
        f'max_spend_usd below is re-checked against the account after every '
        f'search, and the sweep stops there even with searches left.'
        if prefs.get("max_spend_usd") is not None else
        "COSTS MONEY BY DEFAULT. This file sets no max_spend_usd."
    )

    sites_note = (
        "This file sets no SITES, so it inherits config.py's — LinkedIn and "
        "Indeed on, Naukri off (it has never returned a row and costs ~$0.50 "
        "per run minimum). Run --dry-run first and read the run count. To "
        "narrow it, copy the SITES block from profiles/kartik_reachable.py. "
        "LinkedIn searches the geoIds in SITES, NOT the locations above, so "
        "city-level LinkedIn needs that block too."
        if not extra_sites else
        "This file DOES set SITES (below) — Configure chose it. LinkedIn "
        "searches only the geoIds listed there, not SEARCH.locations above; "
        "any site absent from that block follows config.py's default. Run "
        "--dry-run first and read the cost."
    )

    # The model reliably copies the excluded seniority words into penalty_terms
    # as well, even when told they are already handled. With drop_excluded True
    # (the default) hard_drop_terms deletes those rows outright, so the penalty
    # is dead weight — and if that flag is ever flipped off they would be
    # penalized twice, once by drop_penalty and once here. Strip them.
    excluded = set(prefs["exclude_levels"])
    penalties = {term: weight
                 for term, weight in _weights(data["penalty_terms"], sign=-1).items()
                 if term not in excluded}
    return f'''"""{name} — generated from a résumé by auto-apply/make_profile.py.

{data["field_summary"]}

    python scraper.py --profile {name} --dry-run   # cost check, free
    python scraper.py --profile {name} --yes

HOW THE MODEL READ THIS RÉSUMÉ
{data["notes"]}

Skill weights are by DISCRIMINATIVE POWER, not centrality: a term that would
also appear in an unwanted job is weighted low however core it is to this
person. Locations, pay floor, avoid-list and excluded seniority came from the
command line, not from the résumé. Anything absent here inherits from config.py.

{spend_note} {sites_note}

Re-scoring is free — after editing weights run `python rescore_from_apify.py`
rather than paying to scrape again.
"""

{extra_sites}{extra_feeds}SEARCH = {{
    "role_keywords": {_fmt(data["role_keywords"])},
    "experience_years": {years},
    "locations": {_fmt(prefs["locations"])},
    "salary_min": None,
{extra_search}}}

SETTINGS = {{
    # Title bands are a label; this reads the years a posting actually demands.
    "max_experience_years": {years + 3},
    "min_comp_usd": {prefs["min_comp_usd"]!r},
{extra_settings}}}

SCORING = {{
    "skill_weights": {_fmt(skills)},

    # The avoid-list plus technologies off-domain for this field.
    "penalty_terms": {_fmt(penalties)},

    # Two halves of this field; a job naming both earns fullstack_bonus.
    # Empty halves with a 0 bonus mean the field has no such natural split.
    "frontend_terms": {_fmt([t.lower() for t in data["domain_half_a"]])},
    "backend_terms": {_fmt([t.lower() for t in data["domain_half_b"]])},
    # Each term MUST name the platform/domain — these match on TITLE ALONE.
    "fullstack_title_terms": {_fmt([t.lower() for t in data["domain_title_terms"]])},
    "fullstack_bonus": {int(data["domain_bonus"])},

    # From --exclude-levels. soft_drop_terms is left to config.py on purpose:
    # "senior" routinely means 3-4 years, so it down-ranks instead of dropping.
    "hard_drop_terms": {_fmt(prefs["exclude_levels"])},
}}

# The gate on every FREE source: a company board or feed returns its whole
# catalogue and scraper.is_dev_title() drops any title matching none of these
# BEFORE scoring, so a fragment missing here is inventory nobody sees. This is
# the résumé's own vocabulary UNIONED with config.py's generic software floor,
# so it can only ever widen the search.
ATS_TITLE_HINTS = {_fmt(hints, indent=4)}

# Checked first, so it wins: different CAREERS that borrow the same words.
ATS_TITLE_EXCLUDE = {_fmt(excludes, indent=4)}
'''


def _csv(value):
    return [part.strip() for part in value.split(",") if part.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--name", required=True, help="profile name -> profiles/<name>.py")
    parser.add_argument("--resume", default=cfg.RESUME_PDF, help="résumé PDF")
    parser.add_argument("--locations", required=True, help="comma-separated, e.g. 'Bengaluru,Remote'")
    parser.add_argument("--exclude-levels", required=True,
                        help="comma-separated seniority words to drop, e.g. 'intern,fresher,junior'")
    parser.add_argument("--min-comp-usd", type=int, default=None,
                        help="annual pay floor in USD; omit to keep undisclosed postings")
    parser.add_argument("--avoid", default="", help="comma-separated roles/stacks to down-rank")
    parser.add_argument("--force", action="store_true", help="overwrite an existing profile")
    args = parser.parse_args(argv)

    out = os.path.join(cfg.REPO_ROOT, "profiles", f"{args.name}.py")
    if os.path.exists(out) and not args.force:
        sys.exit(f"{out} exists. Re-run with --force to overwrite (git has the old one).")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY missing from .env.")

    prefs = {
        "locations": _csv(args.locations),
        "exclude_levels": [w.lower() for w in _csv(args.exclude_levels)],
        "avoid": _csv(args.avoid),
        "min_comp_usd": args.min_comp_usd,
    }

    resume_text = resume_parser.load_resume(args.resume, cfg.RESUME_TXT)
    print(f"Résumé: {len(resume_text)} chars from {args.resume}")

    try:
        data = generate(tailor.get_client(api_key), cfg.MODELS, resume_text, prefs)
    except QuotaExhausted as exc:
        # Nothing was written, and running it again today reaches the same
        # place — so the exit says when it will not.
        sys.exit(f"Nothing was written: {exc}.")
    except Exception as exc:
        if "503" in str(exc):
            sys.exit(f"{cfg.MODELS[0]} is overloaded (503) and did not recover. "
                     "Nothing was written — just run this again.")
        raise
    source = render(args.name, data, prefs)

    with open(out, "w", encoding="utf-8") as f:
        f.write(source)

    print(f"\n{data['field_summary']}\n")
    print(f"Wrote {out}")
    print(f"  role_keywords : {len(data['role_keywords'])}")
    print(f"  skill_weights : {len(data['skill_weights'])}")
    print(f"  penalty_terms : {len(data['penalty_terms'])}")
    print(f"\nRead it, then verify at zero cost:\n"
          f"  python scraper.py --profile {args.name} --dry-run")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(cfg.REPO_ROOT, ".env"))
    main()
