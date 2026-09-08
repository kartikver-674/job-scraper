"""Routes. Business logic lives in sweep.plan and sweep.runs."""

import os
import re
import sys

from flask import (Flask, redirect, render_template, request, url_for)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESUME_DIR = os.path.join(REPO_ROOT, "auto-apply", "resume")

# make_profile.render() is used at POST /review time regardless of whether
# extract/derive are injected, so it's imported once here at module load —
# matching how auto-apply's own tests import it, so the DeprecationWarning
# google.genai raises on its first import lands during test collection
# (silenced by Python's default filters) rather than during a test run
# (where unittest turns warnings back on).
sys.path.insert(0, os.path.join(REPO_ROOT, "auto-apply"))
import make_profile  # noqa: E402

STEPS = [("upload", "Upload"), ("review", "Review"), ("key", "Connect key"),
         ("configure", "Configure"), ("confirm", "Confirm"),
         ("running", "Running"), ("results", "Results")]

# A profile name becomes both a filesystem path (profiles/<name>.py) and a
# Python module (config.py does importlib.import_module(f"profiles.{name}")),
# so it is checked against an allowlist rather than merely stripped — a name
# like "../../../../tmp/x" or an absolute path survives os.path.join(), which
# silently discards everything before an absolute later component. A leading
# digit is rejected too since that would not be a valid module name.
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")

# A .env value with an embedded newline turns one write into two lines —
# the second one an attacker-chosen KEY=VALUE the file's own reader (and
# python-dotenv) will parse as a real entry, silently overwriting whichever
# key it names. So env_key/value are checked here, at the single write
# funnel, rather than trusting every future caller to have checked upstream.
#
# The value is checked with an allowlist, not a blacklist of bad characters
# — a blacklist for \n and \r alone still let a NUL byte through (harmless
# to the file's line structure, but os.environ[...] = value raises on it
# unhandled). Printable, non-space ASCII covers every real Apify token and
# closes newline/CR/NUL/tab/unicode line separators in one rule.
_ENV_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_ENV_VALUE_RE = re.compile(r"[\x21-\x7E]+")


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
    # LinkedIn's f_WT=2 filters workplace type WITHIN one geography — there is
    # no worldwide-remote search — so "genuinely remote from anywhere" means
    # remote_only=True over every verified country, same mechanism
    # profiles/global_remote.py already uses, not a bare "Remote" location
    # (which would need exactly one region and defeat the point).
    "remote": {"remote_scopes": ["worldwide", "remote"], "locations": ["Remote"],
               "linkedin_locations": _VERIFIED_COUNTRIES,
               "linkedin_remote_only": True},
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

    # keep_unstated is a checkbox: FormData omits it entirely when unchecked,
    # so its mere presence (however Alpine/HTML encodes "on") means checked.
    if "keep_unstated" in form:
        out["min_comp_usd"] = None
    elif "min_comp_usd" in form:
        raw = str(form["min_comp_usd"]).strip()
        if not raw:
            raise _FormError(
                "Enter a pay floor, or keep listings that don't state pay.")
        out["min_comp_usd"] = _parse_int(raw, 0, 100_000_000, "Minimum pay")

    if form.get("skip_terms"):
        out["skip_terms"] = _parse_chips(form["skip_terms"], "Skip-terms")

    return out


def create_app(state=None, extract=None, resume_dir=None,
               max_upload_bytes=15 * 1024 * 1024, derive=None,
               check_token=None, env_path=None, fetch_plan=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = max_upload_bytes
    app.state = state if state is not None else {}
    resume_dir = resume_dir if resume_dir is not None else RESUME_DIR
    env_path = env_path if env_path is not None else os.path.join(REPO_ROOT, ".env")

    if extract is None:
        import resume_parser
        extract = resume_parser.extract_text

    if derive is None:
        import apply_config as cfg
        import tailor

        def derive(resume_text, prefs):
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY is missing from .env.")
            return make_profile.generate(
                tailor.get_client(api_key), cfg.MODEL, resume_text, prefs)

    def default_write_profile(name, source):
        if not _valid_profile_name(name):
            raise ValueError(f"not a valid profile name: {name!r}")
        path = os.path.join(REPO_ROOT, "profiles", f"{name}.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(source)
        return path

    app.write_profile = default_write_profile

    if check_token is None:
        def check_token(token):
            """Return (available_usd, error). Available credit is the account's
            monthly limit minus month-to-date usage, both from the same call
            scraper.py trusts for its spend cap."""
            from apify_client import ApifyClient
            try:
                limits = ApifyClient(token).user().limits().model_dump()
                current = (limits.get("current") or {}).get("monthly_usage_usd") or 0
                allowed = (limits.get("limits") or {}).get("max_monthly_usage_usd")
                if allowed is None:
                    return None, (
                        "Apify did not report a monthly limit for this "
                        "account, so Sweep cannot work out your remaining "
                        "credit. Enter your budget on the next screen.")
                return float(allowed) - float(current), None
            except Exception:
                return None, "That token was rejected by Apify. Check and retry."

    def default_write_env(env_key, value):
        """Upsert one key in .env, leaving every other line untouched. Lines
        are normalised to end with '\\n' before appending — the real .env
        does not end with one, and writelines() glues text together with no
        separator otherwise, corrupting whichever key happened to be last."""
        if not _ENV_KEY_RE.fullmatch(env_key):
            raise ValueError(f"not a valid env key: {env_key!r}")
        if not isinstance(value, str) or not _ENV_VALUE_RE.fullmatch(value):
            raise ValueError("env value must be printable, non-space ASCII")
        lines = []
        if os.path.exists(env_path):
            with open(env_path) as fh:
                lines = [ln if ln.endswith("\n") else ln + "\n"
                         for ln in fh if not ln.startswith(f"{env_key}=")]
        lines.append(f"{env_key}={value}\n")
        with open(env_path, "w") as fh:
            fh.writelines(lines)

    app.write_env = default_write_env

    if fetch_plan is None:
        from sweep import plan as plan_mod
        fetch_plan = plan_mod.fetch

    def costed(profile):
        """Cost the plan and say whether it exceeds the key's credit. The
        over-cap flag is advisory: SETTINGS["max_spend_usd"] is the real guard."""
        import sys
        sys.path.insert(0, REPO_ROOT)
        import config
        from sweep import plan as plan_mod

        raw = fetch_plan(profile)
        out = plan_mod.cost(raw, config.SITE_RATES)
        cap = app.state.get("cap_usd")
        out["over_cap"] = bool(cap is not None and out["total"] > cap)
        out["shortfall"] = round(max(0.0, out["total"] - cap), 4) if cap else 0.0
        app.state["raw_plan"] = raw
        app.state["plan"] = out
        return out

    def shell(step, **kw):
        """Every screen gets the meter reflecting ITS OWN state, never a
        figure carried over from another step."""
        return dict(steps=STEPS, step=step,
                    spend=app.state.get("spend", 0.0),
                    cap_usd=app.state.get("cap_usd"),
                    fill_pct=app.state.get("fill_pct", 0), **kw)

    limit_mb = max_upload_bytes / (1024 * 1024)

    @app.errorhandler(413)
    def too_large(e):
        return render_template("upload.html", **shell(
            "upload",
            error=f"That file is larger than {limit_mb:g} MB. "
                  "Export a smaller PDF and try again.")), 413

    @app.get("/")
    def upload():
        return render_template("upload.html", **shell("upload"))

    @app.post("/resume")
    def resume():
        upload_file = request.files.get("resume")
        if upload_file is None or not upload_file.filename:
            return render_template(
                "upload.html", **shell("upload", error="Choose a PDF to upload.")), 400

        os.makedirs(resume_dir, exist_ok=True)
        path = os.path.join(resume_dir, "resume.pdf")
        upload_file.save(path)

        text = extract(path)
        if not text.strip():
            return render_template("upload.html", **shell(
                "upload",
                error="That PDF has no text in it — it is probably a scan. "
                      "Export a text PDF and try again.")), 400

        app.state["resume_path"] = path
        app.state["resume_text"] = text
        return redirect(url_for("review"))

    COMMODITY_WEIGHT = 2

    def derived_for_state():
        """The model call is made once per résumé and cached on state — a
        cost, so never repeated just because the review screen reloads."""
        derived = app.state.get("derived")
        if derived is None:
            derived = derive(app.state["resume_text"], _prefs(app.state))
            app.state["derived"] = derived
        return derived

    @app.get("/review")
    def review():
        if not app.state.get("resume_text"):
            return redirect(url_for("upload"))
        derived = derived_for_state()
        commodity = [w["term"] for w in derived["skill_weights"]
                     if w["weight"] <= COMMODITY_WEIGHT]
        return render_template("review.html", **shell(
            "review", derived=derived, commodity=commodity,
            suggested_name=app.state.get("profile", "")))

    @app.post("/review")
    def review_post():
        if not app.state.get("resume_text"):
            return redirect(url_for("upload"))
        name = (request.form.get("name") or "").strip()
        derived = derived_for_state()
        commodity = [w["term"] for w in derived["skill_weights"]
                     if w["weight"] <= COMMODITY_WEIGHT]
        if not name:
            return render_template("review.html", **shell(
                "review", derived=derived, commodity=commodity,
                suggested_name="",
                error="Give the profile a name.")), 400
        if not _valid_profile_name(name):
            return render_template("review.html", **shell(
                "review", derived=derived, commodity=commodity,
                suggested_name=name,
                error="Use letters, numbers, dashes and underscores only "
                      "— this becomes a filename.")), 400

        dropped = set(request.form.getlist("drop"))
        kept = dict(derived)
        kept["skill_weights"] = [w for w in derived["skill_weights"]
                                 if w["term"] not in dropped]
        source = make_profile.render(name, kept, _prefs(app.state))
        app.write_profile(name, source)
        app.state["profile"] = name
        return redirect(url_for("key"))

    @app.get("/key")
    def key():
        return render_template("key.html", **shell("key"))

    @app.post("/key")
    def key_post():
        token = (request.form.get("token") or "").strip()
        if not token:
            return render_template("key.html", **shell(
                "key", error="Paste your Apify token.")), 400
        if not _ENV_VALUE_RE.fullmatch(token):
            # Never echo the token back — say what's wrong, not what it was.
            return render_template("key.html", **shell(
                "key", error="That doesn't look like a token — remove any "
                              "extra characters and paste it again.")), 400

        available, error = check_token(token)
        if error:
            # Never render the token back into the page.
            return render_template("key.html", **shell("key", error=error)), 400

        app.write_env("APIFY_TOKEN", token)
        os.environ["APIFY_TOKEN"] = token
        # A cap can never go negative — the account may already be over its
        # own monthly limit, but a negative number makes the meter meaningless.
        app.state["cap_usd"] = max(0.0, available)
        app.state["token_ok"] = True
        return redirect(url_for("configure"))

    @app.get("/configure")
    def configure():
        if not app.state.get("profile"):
            return redirect(url_for("review"))
        estimate = costed(app.state["profile"])
        app.state["spend"] = estimate["total"]
        return render_template("configure.html", **shell(
            "configure", estimate=estimate))

    @app.post("/estimate")
    def estimate():
        from flask import jsonify
        if not app.state.get("profile"):
            return jsonify({"error": "No profile yet — approve the review first."}), 409

        form = request.get_json(silent=True) or {}
        try:
            overrides = _configure_overrides(form)
        except _FormError as exc:
            return jsonify({"error": str(exc)}), 400

        # A partial form only patches the keys it named — everything else in
        # the profile stays exactly what an earlier POST (or the review
        # screen) left it as. Built on a COPY of state/derived, not applied
        # to app.state directly, until make_profile.render() has actually
        # succeeded — render() is where an unverified LinkedIn geography or
        # an unknown config key gets caught (shared with the CLI path), and
        # that failure must leave nothing applied either, same as a plain
        # invalid field.
        skip_terms = overrides.pop("skip_terms", None)
        new_state = dict(app.state)
        new_state.update(overrides)
        if skip_terms is not None:
            # By the time a profile exists (checked above), review_post()
            # has already populated app.state["derived"] — Configure never
            # regenerates it from a résumé, only patches it in place.
            derived = dict(app.state["derived"])
            penalty_terms = list(derived.get("penalty_terms") or [])
            seen = {p["term"].strip().lower() for p in penalty_terms}
            for term in skip_terms:
                if term.lower() not in seen:
                    # Severity on the 1-12 scale generate() uses for
                    # penalty_terms (see make_profile.RESPONSE_SCHEMA) — a
                    # term the person explicitly asked to skip is as strong a
                    # signal as this scale has.
                    penalty_terms.append({"term": term, "weight": 12})
                    seen.add(term.lower())
            derived["penalty_terms"] = penalty_terms
            new_state["derived"] = derived

        name = app.state["profile"]
        if overrides or skip_terms is not None:
            try:
                source = make_profile.render(
                    name, new_state["derived"], _prefs(new_state))
            except KeyError as exc:
                return jsonify({"error": str(exc)}), 400
            app.write_profile(name, source)
            app.state.clear()
            app.state.update(new_state)

        return jsonify(costed(app.state["profile"]))

    @app.get("/confirm")
    def confirm():
        return "confirm"         # Task 7 replaces this

    @app.get("/running")
    def running():
        return "running"         # Task 8 replaces this

    @app.get("/results")
    def results():
        return "results"         # Task 9 replaces this

    return app


def _prefs(state):
    return {
        "locations": state.get("locations") or ["Remote"],
        "exclude_levels": state.get("exclude_levels")
                          or ["intern", "internship", "fresher", "trainee",
                              "new grad", "junior", "jr"],
        "avoid": state.get("avoid") or [],
        "min_comp_usd": state.get("min_comp_usd"),
        # Unset (None) means "not touched by the Configure screen yet" —
        # make_profile.render() omits the key entirely in that case, so
        # config.py's own default silently applies instead of being reset.
        "remote_scopes": state.get("remote_scopes"),
        "max_age_days": state.get("max_age_days"),
        "max_results": state.get("max_results"),
        "linkedin_locations": state.get("linkedin_locations"),
        "linkedin_remote_only": state.get("linkedin_remote_only"),
    }
