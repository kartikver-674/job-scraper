"""Flask-free logic behind the screens: form validation, the reachability
split, and the empty-result diagnosis.

Nothing here imports Flask or touches create_app, which is the point — the
reachability split in particular has to be testable against rows copied
straight out of a real, paid `jobs_combined.csv`, with no test client in the
way. Reviewing it through a three-row hand-written fixture is how it shipped
filing 1046 jobs abroad under "you can work here now".
"""

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
