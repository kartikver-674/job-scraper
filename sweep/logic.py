"""Flask-free logic behind the screens: form validation, the reachability
split, and the empty-result diagnosis.

Nothing here imports Flask or touches create_app, which is the point — the
reachability split in particular has to be testable against rows copied
straight out of a real, paid `jobs_combined.csv`, with no test client in the
way. Reviewing it through a three-row hand-written fixture is how it shipped
filing 1046 jobs abroad under "you can work here now".
"""

import datetime
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# bucket() below is the existing, measured reachability classification — the
# spec called this screen a port of it, not a second implementation. Imported
# once here at module load, never inside the function, which would grow
# sys.path on every page render.
sys.path.insert(0, os.path.join(REPO_ROOT, "auto-apply"))
import linkedin_shortlist  # noqa: E402


# A profile name becomes both a filesystem path (profiles/<name>.py) and a
# Python module (config.py does importlib.import_module(f"profiles.{name}")),
# so it is checked against an allowlist rather than merely stripped — a name
# like "../../../../tmp/x" or an absolute path survives os.path.join(), which
# silently discards everything before an absolute later component. A leading
# digit is rejected too since that would not be a valid module name.
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


def _valid_profile_name(name):
    return bool(_NAME_RE.fullmatch(name))


# Configure-screen scope -> real config keys. "locations"/"remote_scopes" feed
# SEARCH.locations / SETTINGS.remote_scopes (every site); "linkedin_locations"
# additionally overrides SITES.linkedin.locations, because
# SITES[site].get("locations", SEARCH["locations"]) means LinkedIn — the most
# expensive site — otherwise keeps searching config.py's default (India +
# Remote) no matter what SEARCH.locations says (scraper.plan_for_site).
#
# Every LinkedIn location below is a config.LINKEDIN_GEO_IDS key, verified per
# that table's own comments (python verify_geoids.py) — never a name invented
# here. make_profile.render() checks this again against the live config, since
# that guard has to hold for the CLI path too, not just this form.
_INDIA_CITIES = ["Delhi", "Gurgaon", "Bengaluru", "Hyderabad", "Pune", "Mumbai"]
# Same set profiles/global_remote.py and profiles/global_all.py already use —
# reused rather than re-picked, so "verified" keeps meaning the same thing.
_VERIFIED_COUNTRIES = ["United States", "United Kingdom", "Canada", "Ireland",
                       "Germany", "Netherlands", "Australia", "Singapore",
                       "United Arab Emirates"]

# "Remote" has no geoId: the adapter special-cases it onto SITES.linkedin's
# remote_geo (config.py's SITES comment). It is a location you can search, so
# it belongs in the picker; it is just not a place.
REMOTE = "Remote"


def searchable_locations():
    """The locations the Configure screen may offer, grouped for the menu.

    Every entry is a config.LINKEDIN_GEO_IDS key. That table is the whole
    guard: "A missing or wrong geoId is NOT a soft failure: LinkedIn ignores
    the free-text location and returns US results, so you pay full price for
    the wrong country" — so a picker of free text would be a way to buy the
    United States by typing "Bangalore". Read live rather than copied, so a
    geoId verified (or removed) in config appears (or stops appearing) here.
    """
    import config
    known = list(config.LINKEDIN_GEO_IDS)
    cities = [c for c in _INDIA_CITY_NAMES if c in known]
    # Everything else, in config's own order. Grouping only: a city added to
    # that table later lands under "Countries" until it is named above, which
    # is untidy rather than wrong.
    countries = [c for c in known
                 if c not in cities and c not in _INDIA_ALIASES]
    return [("Anywhere remote", [REMOTE]),
            ("India", cities),
            ("Countries", countries)]


def allowed_locations():
    """Flat set of everything searchable_locations() offers."""
    return {name for _, names in searchable_locations() for name in names}


# Gurugram is the same geoId as Gurgaon under LinkedIn's own label; offering
# both would let someone pay twice for one city.
_INDIA_ALIASES = {"Gurugram"}
# Presentation only — which verified names sit under the "India" heading.
# Separate from _INDIA_CITIES, which is what the india SCOPE searches:
# widening that set would change what an existing choice costs.
_INDIA_CITY_NAMES = ["Delhi", "Gurgaon", "Chandigarh", "Bengaluru",
                     "Hyderabad", "Pune", "Mumbai"]

_SCOPE = {
    # India only, onsite/hybrid: no "Remote" in the mix — that is what the
    # "remote" scope is for.
    "india": {"remote_scopes": [], "locations": _INDIA_CITIES,
              "linkedin_locations": _INDIA_CITIES, "linkedin_remote_only": False},
    # LinkedIn has no worldwide-remote search: f_WT=2 filters workplace type
    # WITHIN one geography, so paying for it across many countries buys
    # inventory this repo already measured as unreachable — see
    # profiles/kartik_reachable.py's docstring: of a 2026-07-26 sweep's 480
    # "remote" rows at score >= 10, only 27 were actually reachable from
    # India; 245 of the top 252 were remote-only-within Germany / Spain /
    # UAE / the UK. So LinkedIn here buys the SAME India-remote-only
    # inventory kartik_reachable.py does — locations=["Remote"], one
    # geography (remote_geo inherits config.py's "India" — see render()) —
    # not nine countries. Worldwide remote is left to the free feeds
    # (RemoteOK, WWR, Remotive, Jobicy, Himalayas): built for exactly this,
    # they carry far more of it than LinkedIn, and they're already on by
    # default, so there's nothing to switch on here.
    "remote": {"remote_scopes": ["worldwide", "remote"], "locations": ["Remote"],
               "linkedin_locations": ["Remote"], "linkedin_remote_only": False},
    # Global onsite: same countries, without the remote filter.
    "global": {"remote_scopes": [], "locations": _VERIFIED_COUNTRIES,
               "linkedin_locations": _VERIFIED_COUNTRIES,
               "linkedin_remote_only": False},
}

# Real stack names need '.', '+', '#', '/', '-' ("node.js", "c++", "c#",
# "ci/cd", "full-stack"); nothing else has a legitimate reason to be in a
# skip-term, so it is rejected rather than guessed at.
_CHIP_RE = re.compile(r"[A-Za-z0-9 .+#/-]+")


class _FormError(ValueError):
    """A Configure-screen field failed validation. str(exc) is safe to show
    the user — never echoes the raw input back."""


def _parse_int(raw, lo, hi, label):
    """Strict integer parse within [lo, hi]. Never pass an unvalidated string
    from a form into config — this is the one gate every numeric field goes
    through before it can reach a profile."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise _FormError(f"{label} must be a whole number.")
    if not (lo <= value <= hi):
        raise _FormError(f"{label} must be between {lo} and {hi}.")
    return value


def _parse_chips(raw, label):
    """Comma-separated free text -> a validated list of terms. Rejected, not
    sanitised, so the response says exactly what is wrong."""
    terms = [t.strip() for t in str(raw).split(",") if t.strip()]
    for term in terms:
        if not _CHIP_RE.fullmatch(term):
            raise _FormError(
                f"{label} can only use letters, digits, spaces and . + # / - "
                f"— check {term!r}.")
    return terms


def step_states(steps, state, current):
    """Each step's number, whether it can be opened, and whether it is done.

    The rule is asymmetric on purpose. A step is never offered when its
    route would redirect — that is the floor, and the reason this reads the
    same facts the guards do rather than holding a second opinion about the
    flow. It may be STRICTER than a guard where the screen would render with
    nothing to say: /running only requires raw_plan, which costed() writes on
    every /configure visit, so guard parity alone would advertise a progress
    screen for a sweep that has not started. Being stricter cannot send
    anyone somewhere unexpected; being laxer can.

    The header used to render all seven as plain tabs, four of which redirect
    away on a fresh session.

    Flask-free and state-in/list-out so the table below can be tested against
    the guards directly, with no test client in the way.
    """
    has_resume = bool(state.get("resume_text"))
    has_profile = bool(state.get("profile"))
    # `cap_usd is not None` is what no_key_yet() checks, and a real zero cap
    # is a verified key — `bool(cap)` would call an exhausted account no key.
    has_key = state.get("cap_usd") is not None
    # Chose the free path on step 3. A CHOICE, not the absence of a key: the
    # two states are opposites downstream ("has not connected one yet" must
    # still send you to step 3, "declined one" must not), and only a recorded
    # choice can tell them apart.
    free_only = bool(state.get("free_only"))
    has_access = has_key or free_only
    has_plan = bool(state.get("raw_plan"))
    # Set by POST /run only, so it is the one cheap fact that says a sweep
    # was launched. Nothing here may read .done_combos or the account: this
    # runs on every page render, including the money screens.
    launched = state.get("proc") is not None

    opens = {"upload": True,
             "review": has_resume,
             "key": True,
             "configure": has_profile and has_access,
             "confirm": has_profile and has_access,
             # launched, not has_plan: see the docstring.
             "running": launched,
             "results": has_profile}
    done = {"upload": has_resume,
            "review": has_profile,
            "key": has_access,
            "configure": has_plan,
            "confirm": launched,
            "running": launched,
            # The last step. Nothing is downstream of it to prove it finished.
            "results": False}

    slugs = [slug for slug, _ in steps]
    at = slugs.index(current) if current in slugs else None
    return [{"slug": slug, "label": label, "n": i + 1,
             "current": slug == current,
             # Never mark the step being viewed as done, whatever state says:
             # you are standing on it, which is the more useful fact.
             "done": done.get(slug, False) and slug != current,
             "open": opens.get(slug, False),
             "next": at is not None and i == at + 1}
            for i, (slug, label) in enumerate(steps)]


# config keys are lowercase, and prose that names a job board should spell it
# the way the board does: "Linkedin" out of Jinja's |title filter is a typo a
# reader notices. A key with no entry falls back to itself, so a paid site
# added to config appears in the sentence (lowercase) rather than vanishing
# from it.
SITE_LABELS = {"linkedin": "LinkedIn", "indeed": "Indeed", "naukri": "Naukri"}


def site_label(name):
    return SITE_LABELS.get(name, name)


# The orders /results offers, and the label each one wears. A key returns a
# tuple that always sorts ASCENDING — no reverse= anywhere — because reverse
# would also flip the "no value" flag and float every row missing the field to
# the top. Every key ends in -score, so rows that tie on the chosen field are
# still ranked by how well they match.
#
# Pay is deliberately absent. Measured across output/ (10,397 rows,
# 2026-09-10) only 5% state one at all, and those are multi-currency free text
# ("₹5,00,000 - ₹15,00,000 a year", "Up to ₹1,80,000 a month"), so the order
# would be 516 rows above 9,881 arbitrary ones. A filter for "states pay"
# would be the honest version of that, not a sort.
SORTS = {
    "score": ("Best match", lambda r: (-_as_int(r.get("score")),)),
    "recent": ("Newest first",
               lambda r: (0, _neg_date(_sortable_date(r.get("date_posted"))),
                          -_as_int(r.get("score")))
               if _sortable_date(r.get("date_posted"))
               else (1, "", -_as_int(r.get("score")))),
    "experience": ("Least experience first",
                   lambda r: (0, _stated_years(r.get("experience_required")),
                              -_as_int(r.get("score")))
                   if _stated_years(r.get("experience_required")) is not None
                   else (1, 0, -_as_int(r.get("score")))),
}
DEFAULT_SORT = "score"

_ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_LEADING_YEARS = re.compile(r"\s*(\d{1,2})")


def _sortable_date(value):
    """The YYYY-MM-DD at the start of a date cell, or None.

    A PREFIX, not a full match: 353 rows on disk carry a full ISO-8601
    timestamp ("2026-07-27T16:05:10.325Z") rather than a bare date, and a
    fullmatch called every one of them undated — sinking 353 well-dated rows
    to the bottom of "newest first". Anything with no leading date really is
    undated and sorts last.

    The time is dropped rather than used. Only 3% of rows carry one, so
    ordering within a day would be false precision for the other 97%; rows
    from the same day tie and fall through to the score tie-break.
    """
    m = _ISO_DATE.match(str(value or "").strip())
    return m.group(1) if m else None


def _neg_date(value):
    """Newest first, as an ascending key: invert each digit of the date."""
    return "".join(str(9 - int(c)) if c.isdigit() else c for c in value)


def _stated_years(value):
    """The leading integer of a stored experience cell ("3+" -> 3), or None.

    The column holds what the parser found; older rows hold raw text. Only a
    leading integer is trusted, and anything else is "the posting didn't say"
    — which must sort LAST rather than as zero, which would rank every
    unknown as the easiest job on the page.
    """
    m = _LEADING_YEARS.match(str(value or ""))
    return int(m.group(1)) if m else None


def sort_rows(rows, sort):
    """`rows` in the chosen order. An unknown order falls back to the default
    rather than raising: it arrives from a query string."""
    _, key = SORTS.get(sort) or SORTS[DEFAULT_SORT]
    return sorted(rows, key=key)


# How many listings a section shows before it offers the rest. A real sweep
# is 1600 rows; three sections of everything is a page nobody scrolls to the
# bottom of, and the rows past the first screenful of a score-ranked list are
# the ones least worth reading first.
SECTION_CAP = 25


# Every way a sweep can be sitting when the child is no longer running.
# One name per state, decided in one place, because the screen, the SSE
# payload and the tests each used to re-derive "is it finished" from counts.
SWEEP_STATES = ("running", "finished", "stopped", "out_of_credit", "halted")


def sweep_state(running, outstanding, stopped_by_user=False,
                credit_left=None, cheapest_search=None):
    """Which of SWEEP_STATES this sweep is in.

    "halted" is the honest default and it exists on purpose: a sweep that
    ended early for a reason we cannot name must not be labelled as one we
    can. Only two reasons are ever claimed — the user pressed Stop, which is
    recorded when they do, and there is not enough credit left to buy even
    the cheapest search still outstanding, which is a comparison of two
    figures rather than a guess about a crash.
    """
    if running:
        return "running"
    if outstanding == 0:
        return "finished"
    if stopped_by_user:
        return "stopped"
    if (credit_left is not None and cheapest_search is not None
            and credit_left < cheapest_search):
        return "out_of_credit"
    return "halted"


def remaining_cost(tiles, rates):
    """What the searches that have NOT run would cost, at plan rates.

    `rates` are the EFFECTIVE per-search rates the plan was costed at (each
    already scaled to the depth this sweep runs), so this and the plan total
    cannot disagree about what a search on a given site costs.
    """
    return round(sum(rates.get(t["site"], 0.0)
                     for t in tiles if t.get("state") != "done"), 4)


def cheapest_rate(tiles, rates):
    """The lowest per-search rate among the searches still outstanding, or
    None when nothing paid is left to run."""
    left = [rates.get(t["site"], 0.0) for t in tiles
            if t.get("state") != "done"]
    paid = [r for r in left if r > 0]
    return min(paid) if paid else None


def mask_token(token, keep=4):
    """A token as it may appear on screen: the last few characters only.

    Enough to tell two keys apart and to match one against the Apify
    console, and not enough to use. The whole value never reaches the page —
    the remove control posts the .env slot NAME, not the secret.
    """
    tail = (token or "")[-keep:] if keep > 0 else ""
    return "****" + tail


def key_pills(tokens, credits=None):
    """One row per configured key: what to call it, enough of it to tell
    them apart, what is left on it, and the slot it lives in.

    Numbered by position rather than by slot, because the slots have gaps —
    deleting APIFY_TOKEN_2 by hand leaves APIFY_TOKEN and APIFY_TOKEN_3, and
    a list that reads "Token 1, Token 3" invites the question of where Token
    2 went. The slot name travels along for the hover and for the control
    that removes it, so the link to .env is never lost.
    """
    credits = credits or {}
    return [{"name": name, "label": f"Token {position}",
             "tail": mask_token(token), "credit": credits.get(name)}
            for position, (name, token) in enumerate(tokens, start=1)]


def and_list(items):
    """"a", "a and b", "a, b and c" — an English list, not "a and b and c"."""
    items = [str(i) for i in items]
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def posted_age(iso, today=None):
    """"3d ago" for a posting date, "" when there is not a usable one.

    The results screen offers a "Newest first" sort and then showed no date
    at all, so the order it produced was unverifiable from the page. This is
    the smallest thing that fixes that: relative, because the question a
    shortlist answers is "is this stale", not "what was the date".
    """
    from datetime import date

    stamp = _sortable_date(iso)
    if not stamp:
        return ""
    try:
        when = date.fromisoformat(stamp)
    except ValueError:
        return ""
    days = ((today or date.today()) - when).days
    if days < 0:
        # A posting dated in the future is a source's bad data, not a
        # reason to render "-3d ago".
        return ""
    if days == 0:
        return "today"
    if days < 7:
        return f"{days}d ago"
    if days < 60:
        return f"{days // 7}w ago"
    return f"{days // 30}mo ago"


def shortlist(all_rows, min_score=0, source="", q="", sort=DEFAULT_SORT):
    """The rows the results screen is showing, in the order it shows them.

    Shared with the export routes rather than copied into them: a file
    labelled "Export CSV (128)" has to contain the same 128 rows the screen
    is displaying, and two copies of this filter would eventually disagree
    about which ones those are.
    """
    rows = [r for r in all_rows if _as_int(r.get("score")) >= min_score]
    if source:
        rows = [r for r in rows if r.get("source_site") == source]
    if q:
        needle = q.lower()
        rows = [r for r in rows if needle in
                f"{r.get('title', '')} {r.get('company', '')}".lower()]
    # Sorted explicitly rather than trusting the CSV's own order — the real
    # files happen to arrive score-descending today, but that is another
    # script's undocumented behaviour, not a guarantee.
    return sort_rows(rows, sort)


def sweep_dates(isos, limit=4):
    """Distinct sweep dates as "26 Aug", newest first, at most `limit`.

    Takes ISO strings so the caller's clock and filesystem stay out of here.
    Two sweeps on one day are one label: the offer is about which days are on
    disk, not how many files there are.
    """
    from datetime import date

    seen, out = set(), []
    for iso in isos:
        if iso in seen:
            continue
        seen.add(iso)
        try:
            out.append(date.fromisoformat(iso).strftime("%-d %b"))
        except ValueError:
            # An unparseable stamp is still a file the merge will read, so it
            # is counted by the caller — it just cannot be named here.
            continue
        if len(out) == limit:
            break
    return out


def reweighted(derived, terms, weights, dropped, add_raw="", add_weight=""):
    """`derived` with the posted weight edits applied. Raises _FormError.

    Shared by the review screen and the results screen's re-rank panel, so
    the two cannot disagree about what a posted weight means.

    The term travels with its weight in the form, so this never has to
    reproduce a sort order to know which weight is whose — but the two lists
    must still be the same length. A desync would reassign weights to the
    wrong terms, silently, on the numbers that decide the ranking; browsers
    submit in document order, and this fails closed rather than trust that.

    A term with no posted weight keeps the one it has: a form that renders
    only some of the weights must not zero the rest.

    `add_raw` is comma-separated free text for the skills the model missed —
    the whole reason the editor is not read-only. Added terms are lowercased,
    because that is the form the profile stores and scraper matches on, and an
    added term that is already in the list UPDATES it rather than appearing
    twice: skill_weights renders into a dict literal, where a duplicate term
    would silently keep whichever copy was written last.

    Adding a term also UN-DROPS it, by way of the append below rather than a
    special case: the remove column is pre-checked for the commodity skills on
    the review screen, so a user who types one of those back has said the more
    specific thing, and the alternative is their typing doing nothing at all.
    """
    if len(terms) != len(weights):
        raise _FormError("The weights didn't come through — reload the page "
                         "and try again.")
    edited = {term: _parse_int(raw, 1, 5, f"Weight for {term}")
              for term, raw in zip(terms, weights)}
    added = {t.lower(): None for t in _parse_chips(add_raw, "Added skills")}
    if added:
        # Only parsed when something was actually added, so an untouched form
        # cannot fail on the weight beside an empty box.
        weight = _parse_int(add_weight or 3, 1, 5, "Weight for the added skills")
        added = {term: weight for term in added}
    dropped = set(dropped)
    kept = dict(derived)
    kept["skill_weights"] = [
        dict(w, weight=added.get(w["term"], edited.get(w["term"], w["weight"])))
        for w in derived["skill_weights"] if w["term"] not in dropped]
    known = {w["term"] for w in kept["skill_weights"]}
    kept["skill_weights"] += [{"term": term, "weight": weight}
                              for term, weight in added.items()
                              if term not in known]
    return kept


def with_experience(derived, years_raw, months_raw):
    """`derived` with the experience the user corrected on the review screen.

    Raises _FormError. Separate from reweighted() because the results
    screen's re-rank panel posts weights without this field, and a missing
    field there must leave the number alone rather than reset it to zero.

    Only the whole years reach the profile — render() writes them into
    SEARCH["experience_years"] (LinkedIn's seniority band) and
    SETTINGS["max_experience_years"], and both compare against a posting's
    stated floor, which is always in years. The months are stored for the
    display that showed them.
    """
    if years_raw is None and months_raw is None:
        return derived
    # 60 rather than no ceiling: this is a career length, and an unbounded
    # one reaches config as max_experience_years and silently stops
    # filtering anything.
    years = _parse_int(years_raw or 0, 0, 60, "Years of experience")
    months = _parse_int(months_raw or 0, 0, 11, "Months of experience")
    return dict(derived, years_experience=years,
                experience_months=years * 12 + months)


# ---------------------------------------------------------------------------
# The applied ledger
# ---------------------------------------------------------------------------
#
# A file of its own, not a column in jobs_combined.csv, because merge_jobs.py
# and rescore_from_apify.py both TRUNCATE that file in place — a tick stored
# there would be wiped by the next merge, silently, with nothing on screen to
# say it had been. Nor app.state, which is gone on restart.
#
# Same shape and same reasoning as the engine's seen.tsv (scraper.py:1180): a
# few hundred lines a year, keyed on scraper._seen_key, which is the identity
# dedupe already uses — so a tick survives a re-score, a merge, and a fresh
# sweep that re-fetches the same posting under a new row.
#
# Rewritten rather than appended, because unticking has to remove a line.
APPLIED_FILE = "applied.tsv"

# The key arrives from the browser, and a tab or a newline in it would forge
# extra columns or extra rows in the ledger. Scrubbed rather than rejected:
# the same treatment record_seen gives every field it writes.
_TSV_BREAK = re.compile(r"[\t\r\n]+")


def _tsv(value):
    return _TSV_BREAK.sub(" ", str(value if value is not None else "")).strip()


def applied_path(output_dir, profile):
    return os.path.join(output_dir, profile, APPLIED_FILE)


def read_applied(path):
    """The keys ticked as applied. A missing ledger is an empty set, never an
    error — no one has ticked anything yet is the normal first state."""
    if not os.path.exists(path):
        return set()
    keys = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1]:
                keys.add(parts[1])
    return keys


def set_applied(path, key, on, title="", company="", today=None):
    """Tick or untick one job. Returns the keys applied afterwards.

    The title and company ride along so the ledger is readable on its own —
    a file of bare hashes tells whoever opens it nothing, and this one
    outlives the output files it refers to.
    """
    key = _tsv(key)
    if not key:
        return read_applied(path)
    kept = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                parts = line.split("\t")
                if len(parts) >= 2 and parts[1] == key:
                    continue          # dropped, then re-added below if on
                if line.strip():
                    kept.append(line)
    if on:
        stamp = today or datetime.date.today().isoformat()
        kept.append("\t".join(_tsv(v) for v in (stamp, key, title, company)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for line in kept:
            fh.write(line + "\n")
    return read_applied(path)


def paid_sites():
    """Sites the Configure screen can switch off, in run order.

    Derived from the intersection of config.SITES (what the engine knows how
    to run) and config.SITE_RATES (what costs money), rather than listed here
    — a hardcoded list is how rescore_from_apify.py came to miss a key, and a
    name in one table but not the other would KeyError in make_profile
    instead of being quietly skipped.

    Free feeds are deliberately absent: switching one off saves nothing, and
    the panel says so rather than offering a control that cannot move the
    figure above it.
    """
    import config
    return [s for s in config.SITES if s in config.SITE_RATES]


def _configure_overrides(form):
    """Validate the posted Configure-screen form and map it onto the state
    keys _prefs() understands. Returns {} for a form with no recognised
    field (the plain re-plan the estimate route always does). Raises
    _FormError, with nothing applied yet, on the first invalid field — a
    partial form must never partially write, since that could widen the
    sweep on a field the caller thought they hadn't touched.
    """
    out = {}

    if "scope" in form:
        scope = form["scope"]
        if scope not in _SCOPE:
            raise _FormError("Choose where you can work.")
        out.update(_SCOPE[scope])

    # Locations, when the picker sent any. Applied AFTER the scope above, so
    # an empty box means "whatever the scope covers" and picking narrows it —
    # which is what "All locations" means on that control.
    #
    # Validated against the verified table, never trusted: an unknown name
    # reaches LinkedIn as free text, and LinkedIn answers free text with US
    # results at full price (config.LINKEDIN_GEO_IDS). This is the one field
    # on the screen where a typo costs money in the wrong currency.
    if "locations" in form:
        picked = _parse_chips(form["locations"], "Locations")
        unknown = [p for p in picked if p not in allowed_locations()]
        if unknown:
            raise _FormError(
                f"{unknown[0]!r} is not a location this can search. LinkedIn "
                "needs a verified geoId for each one, or it silently returns "
                "United States results and bills for them.")
        if picked:
            # linkedin_locations too: SITES[site].get("locations", ...) means
            # the most expensive site keeps config.py's default otherwise.
            out["locations"] = picked
            out["linkedin_locations"] = picked

    if "max_age_days" in form:
        out["max_age_days"] = _parse_int(
            form["max_age_days"], 1, 365, "Freshness window")

    # Both are plain text/number inputs that live in the same <form> as every
    # other control, so an unrelated change elsewhere in the form resubmits
    # them too, blank, every time — not just on their own change event.
    # Blank has to mean "no opinion this round", the same as absent, or the
    # very first click anywhere on the screen would 400.
    if form.get("max_results"):
        out["max_results"] = _parse_int(
            form["max_results"], 1, 200, "Results per search")

    # A pay floor and "keep listings with no stated pay" are not a choice the
    # engine offers: comp_ok() returns True whenever the pay is unstated
    # (scraper.py, `return True if top is None else top >= min_usd`), so
    # unstated rows survive a floor unconditionally. The screen used to carry
    # a checkbox for it, default on, whose only real effect was to discard
    # whatever floor the user had just typed. The floor now always applies,
    # and the copy states what actually happens to unstated pay.
    if "min_comp_usd" in form:
        raw = str(form["min_comp_usd"]).strip()
        # Blank means "no floor", which is a legitimate choice, not an error.
        out["min_comp_usd"] = (
            _parse_int(raw, 0, 100_000_000, "Minimum pay") if raw else None)

    if form.get("skip_terms"):
        out["skip_terms"] = _parse_chips(form["skip_terms"], "Skip-terms")

    # One name per site, never a checkbox GROUP sharing a name: /estimate
    # posts Object.fromEntries(new FormData(form)), which keeps only the LAST
    # value of a repeated key — three boxes named "site" would arrive as one
    # and silently switch the other two off. The hidden marker separates "the
    # panel was on screen and the user unchecked everything" from "this form
    # doesn't carry sources at all", which an absent checkbox cannot do.
    if form.get("sites_present"):
        out["sites_enabled"] = {site: bool(form.get(f"site_{site}"))
                                for site in paid_sites()}

    return out


# Keys are exactly what linkedin_shortlist.bucket() returns, so an unknown
# bucket raises here rather than silently dropping paid rows off a page that
# still looks complete.
SECTIONS = [
    ("india", "Onsite and hybrid in India",
     "Where you already have the right to work. Includes remote roles that "
     "are geo-locked to India, since those are reachable from here too."),
    ("remote", "Fully remote",
     "The posting states worldwide or unqualified remote, so there is no "
     "visa question to answer."),
    ("abroad", "Onsite abroad — needs a visa",
     "Kept and labelled rather than dropped. Each of these needs sponsorship "
     "or an existing right to work in that country, and it includes remote "
     "roles geo-locked to somewhere you are not."),
]


def bucket_rows(rows):
    """Split rows by whether the person can actually take the job.

    Delegates to linkedin_shortlist.bucket(), which reads remote_scope and the
    location/remote_regions text. This screen must NOT invent a fourth rule:
    the one it shipped with split on `visa` and `remote?` instead, and over
    1607 real paid rows that filed 1046 onsite-abroad jobs under "you can work
    here now", 170 geo-locked rows under "genuinely remote from anywhere", and
    91 explicit sponsorship REFUSALS under "needs visa sponsorship" — the
    inverse of that label, because `visa` is "no" when the posting refuses
    (enrich.visa) and "" in the common case where it never says.

    Sponsorship-shaped rows are left where bucket() puts them: `visa` answers
    a different question from "can this person work there", and the sweep
    already drops explicit refusals when SETTINGS["drop_no_visa"] is on.
    """
    buckets = {key: [] for key, _heading, _note in SECTIONS}
    for row in rows:
        buckets[linkedin_shortlist.bucket(row)].append(row)
    return buckets


def _as_int(value):
    """Scores arrive from CSV as text and can be blank or non-numeric."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def worst_filter(all_rows, min_score, source, q):
    """Which single active filter would remove the most rows alone, and how
    many. Returns (name, count), or None when no filter is active.

    Applied one at a time against the UNFILTERED set — with three filters
    combined, a hardcoded guess at which one is "the" culprit can be simply
    false.
    """
    removed = {}
    if min_score:
        removed["minimum score"] = sum(
            1 for r in all_rows if _as_int(r.get("score")) < min_score)
    if source:
        removed["source"] = sum(
            1 for r in all_rows if r.get("source_site") != source)
    if q:
        needle = q.lower()
        removed["search text"] = sum(
            1 for r in all_rows
            if needle not in f"{r.get('title', '')} {r.get('company', '')}".lower())
    # A filter that removed nothing is not the culprit. With no shortlist on
    # disk yet, every branch counts 0 and max() would still name one — telling
    # the user "the minimum score filter removed the most — 0 of 0" and
    # pointing them at the wrong remedy.
    if not removed or max(removed.values()) == 0:
        return None
    name = max(removed, key=removed.get)
    return name, removed[name]



def fill_pct(spend, cap):
    """How much of the connected credit a figure represents, 0-100.

    The meter bar and the cap marker at left:100% are the only thing on the
    page that shows PROXIMITY to the limit rather than an absolute figure, so
    it has to be a real number — it was hardcoded to 0% on every screen.
    Clamped at 100 because a plan can exceed the credit, and the bar turning
    red (.meter.over) is what says so, not a bar overflowing its track.
    """
    if spend is None or cap is None:
        return 0
    if cap <= 0:
        # A real zero-credit account. Anything at all is "all of it".
        return 100 if spend > 0 else 0
    return min(100, round(spend / cap * 100))
