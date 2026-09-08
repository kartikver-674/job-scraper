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

# Needed by snapshot() below (site free/paid classification) on every SSE
# tick — imported once here, at module load, rather than inside snapshot()
# itself, which would grow sys.path without bound over a 40-minute sweep.
sys.path.insert(0, REPO_ROOT)
import config  # noqa: E402

STEPS = [("upload", "Upload"), ("review", "Review"), ("key", "Connect key"),
         ("configure", "Configure"), ("confirm", "Confirm"),
         ("running", "Running"), ("results", "Results")]

# account_usage_usd is a live Apify call. /events polls snapshot() every 2s
# for up to a ~40-minute sweep — read_spend is throttled to once per this
# many seconds and the value cached on app.state in between, or that would
# be ~1200 live requests for a figure that moves slowly.
SPEND_POLL_SECONDS = 15


def planned_keys(state):
    """Ledger keys this sweep intends to write, in plan order."""
    from sweep import runs as runs_mod
    return runs_mod.combo_keys(state["raw_plan"], runs_mod.today())


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


SECTIONS = [
    ("local", "You can work here now",
     "Onsite or hybrid where you already have the right to work"),
    ("remote", "Genuinely remote from anywhere",
     "Reachable from where you are, with no relocation"),
    ("visa", "Needs visa sponsorship",
     "Requires sponsorship or existing work authorisation"),
]


def bucket_rows(rows):
    """Split rows by whether the person can actually take the job.

    Mirrors the split profiles/kartik_reachable.py exists to buy: two thirds of
    a global sweep was onsite abroad and needed sponsorship, so it has to be
    visible rather than mixed in with reachable work.
    """
    buckets = {"local": [], "remote": [], "visa": []}
    for row in rows:
        if (row.get("visa") or "").strip():
            buckets["visa"].append(row)
        elif str(row.get("remote?", "")).lower() == "true":
            buckets["remote"].append(row)
        else:
            buckets["local"].append(row)
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


def create_app(state=None, extract=None, resume_dir=None,
               max_upload_bytes=15 * 1024 * 1024, derive=None,
               check_token=None, env_path=None, fetch_plan=None,
               start_sweep=None, read_spend=None, output_dir=None,
               read_done=None, now=None, read_rows=None, start_rescore=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = max_upload_bytes
    app.state = state if state is not None else {}
    resume_dir = resume_dir if resume_dir is not None else RESUME_DIR
    env_path = env_path if env_path is not None else os.path.join(REPO_ROOT, ".env")
    # Same shape as resume_dir/env_path: a real run writes under
    # REPO_ROOT/output/<profile>, but a test must never be able to reach a
    # real profile's output dir just because it picked a name that collides
    # with one — output/ holds real paid-sweep results with no git history
    # to fall back on.
    output_dir = output_dir if output_dir is not None else os.path.join(REPO_ROOT, "output")
    # Injected so a test can advance the spend-poll throttle (SPEND_POLL_SECONDS
    # below) without a real sleep — a sleeping test is a slow test forever.
    import time as _time_mod
    now = now if now is not None else _time_mod.monotonic

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

    if start_sweep is None:
        from sweep import runs as runs_mod
        start_sweep = runs_mod.start

    if read_spend is None:
        def read_spend():
            """Month-to-date account spend, the authoritative figure. Actor
            self-reports undercount roughly 3x (scraper.py:912).

            Returns None — never a fabricated 0.0 — when the figure isn't
            available: no token set, or the call itself failed (network,
            Apify outage). /run persists whatever this returns as
            baseline_usd; None there means "unknown", and Task 8 must show
            the raw current spend with a caveat rather than treat it as a
            zero baseline, which would report the account's entire
            month-to-date spend as this one sweep's cost."""
            sys.path.insert(0, REPO_ROOT)
            import scraper
            from apify_client import ApifyClient
            token = os.environ.get("APIFY_TOKEN")
            if not token:
                return None
            try:
                return scraper.account_usage_usd(ApifyClient(token))
            except Exception:
                return None

    if read_done is None:
        def read_done(profile, day):
            """Keys finished today, straight from output/<profile>/.done_combos
            — never parsed from stdout, which is a formatting detail the
            engine itself doesn't trust to resume a capped sweep. Reads
            through the injected output_dir, same as _write_run_json, so a
            test can never reach a real profile's real (paid, unrecoverable)
            output directory just by picking a colliding profile name."""
            from sweep import runs as runs_mod
            out_dir = os.path.join(output_dir, profile)
            return runs_mod.done_keys(runs_mod.done_path_for(out_dir), day)

    if read_rows is None:
        def read_rows(profile):
            """Newest merged shortlist for this profile, or [] if none yet.

            jobs_combined*.csv only — the per-sweep jobs_<date>_<time>.csv
            files each hold part of a sweep, and picking one by mtime would
            show a partial set as if it were the whole result. Reads through
            the injected output_dir, same as read_done, so a test can never
            reach a real profile's real (paid, unrecoverable) output
            directory just by picking a colliding profile name.
            """
            import csv
            import glob
            pattern = os.path.join(output_dir, profile, "jobs_combined*.csv")
            files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
            if not files:
                return []
            with open(files[0], newline="", encoding="utf-8") as fh:
                return list(csv.DictReader(fh))

    if start_rescore is None:
        from sweep import runs as runs_mod
        start_rescore = runs_mod.start_rescore

    def snapshot():
        """One progress reading. Spend is a delta from the recorded baseline,
        because account_usage_usd is month-to-date, not per-run."""
        from sweep import runs as runs_mod

        planned = planned_keys(app.state)
        done = read_done(app.state["profile"], runs_mod.today())
        p = runs_mod.progress(planned, done)

        for tile in p["tiles"]:
            # Same rule plan.cost() already uses (a site listed at a $0.00
            # rate is free either way) rather than a second implementation
            # of "is this site free" that could disagree with it.
            tile["free"] = config.SITE_RATES.get(tile["site"], 0.0) == 0.0

        last_at = app.state.get("spend_read_at")
        if last_at is None or (now() - last_at) >= SPEND_POLL_SECONDS:
            app.state["spend_read_val"] = read_spend()
            app.state["spend_read_at"] = now()
        spend_now = app.state["spend_read_val"]

        baseline = app.state.get("baseline_usd")
        p["baseline_known"] = baseline is not None
        p["spend_known"] = spend_now is not None
        if spend_now is None:
            # No reading at all. Don't invent a figure for a money display.
            p["spend"] = None
        elif baseline is None:
            # Month-to-date with no baseline to subtract. Report it as what
            # it is, not as this sweep's cost.
            p["spend"] = round(spend_now, 4)
        else:
            p["spend"] = round(max(0.0, spend_now - baseline), 4)
        app.state["spend"] = p["spend"] or 0.0

        proc = app.state.get("proc")
        running_now = proc is not None and proc.poll() is None
        p["finished"] = (not running_now) and p["outstanding"] == 0
        p["interrupted"] = (not running_now) and p["outstanding"] > 0
        p["remaining_text"] = (
            f"{p['outstanding']} searches left" if running_now else
            ("finished" if p["finished"] else "stopped early"))
        return p

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
        out["shortfall"] = (round(max(0.0, out["total"] - cap), 4)
                             if cap is not None else 0.0)
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
        if not app.state.get("profile"):
            return redirect(url_for("configure"))
        from datetime import datetime
        # Re-costed on every visit, like /configure — the meter shows this
        # screen's own state, never a figure carried over from an earlier one.
        plan = costed(app.state["profile"])
        return render_template("confirm.html", **shell(
            "confirm", plan=plan, spans_midnight=datetime.now().hour >= 22))

    @app.post("/run")
    def run():
        # Fail closed: no plan at all (a /run hit that never went through
        # /confirm) must refuse exactly like an over-cap plan does, not
        # launch an uncapped subprocess because an empty dict's .get()
        # reads as falsy the same as a real "under cap" plan would.
        plan_now = app.state.get("plan")
        if not plan_now:
            return "No plan to run — start from Configure.", 400
        if plan_now.get("over_cap"):
            return render_template("confirm.html", **shell(
                "confirm", plan=plan_now, spans_midnight=False,
                error="Attach a second key or narrow the search first.")), 400

        # baseline_usd may be None (see read_spend's docstring) — recorded
        # as-is, never coerced to 0.0, so Task 8 can tell "unknown" from
        # "no spend yet".
        app.state["baseline_usd"] = read_spend()
        app.state["proc"] = start_sweep(app.state["profile"])
        _write_run_json(app.state, output_dir)
        return redirect(url_for("running"))

    @app.post("/second-key")
    def second_key():
        token = (request.form.get("token") or "").strip()
        # No plan at all (a /second-key hit that never went through
        # /confirm) can't be rendered back into confirm.html — plan.lines
        # etc. would be undefined — so this fails the same simple way /run
        # does for the same precondition, rather than a 500 on a missing key.
        plan_now = app.state.get("plan")
        if not plan_now:
            return "No plan to attach a key to — start from Configure.", 400

        # Re-pasting the key already on file (the first key, or an earlier
        # second key) would otherwise add the same credit again: check_token
        # returns roughly the same available balance, cap_usd is inflated a
        # second time, and the UI believes an unaffordable sweep is fine —
        # exactly the wasted-spend outcome this screen exists to prevent.
        if token and token in (os.environ.get("APIFY_TOKEN"),
                                os.environ.get("APIFY_TOKEN_2")):
            return render_template("confirm.html", **shell(
                "confirm", plan=plan_now, spans_midnight=False,
                error="That's the same key already on file — it adds no "
                      "new credit.")), 400

        available, error = check_token(token)
        if error:
            return render_template("confirm.html", **shell(
                "confirm", plan=plan_now, spans_midnight=False,
                error=error)), 400
        if available <= 0:
            return render_template("confirm.html", **shell(
                "confirm", plan=plan_now, spans_midnight=False,
                error="That key verified, but it has no credit "
                      "available.")), 400

        app.write_env("APIFY_TOKEN_2", token)
        os.environ["APIFY_TOKEN_2"] = token
        app.state["cap_usd"] = (app.state.get("cap_usd") or 0) + available
        # over_cap is not patched here — GET /confirm re-costs the whole
        # plan via costed() on the redirect below, so any value written
        # here would be discarded before ever being read.
        return redirect(url_for("confirm"))

    @app.get("/running")
    def running():
        if not app.state.get("raw_plan"):
            return redirect(url_for("configure"))
        return render_template("running.html", **shell(
            "running", progress=snapshot(), plan=app.state["plan"]))

    @app.get("/progress")
    def progress():
        from flask import jsonify
        # Reachable without going through /run, same as /running — a
        # bookmark, a stale tab after a reset, curl during manual testing.
        # snapshot() reaches state["raw_plan"]/["profile"] by bracket
        # access, so this must not fall through to it on empty state.
        if not app.state.get("raw_plan"):
            return jsonify({"error": "No sweep running."}), 409
        return jsonify(snapshot())

    @app.get("/events")
    def events():
        import json
        import time
        from flask import Response

        # Same guard as /progress. A 302 here is what /running redirects
        # with, but EventSource treats a redirect response as HTML to load,
        # not a stream to read — so this closes the connection with a plain
        # error status instead of redirecting into it.
        if not app.state.get("raw_plan"):
            return Response('{"error": "No sweep running."}', status=409,
                            mimetype="application/json")

        def stream():
            while True:
                p = snapshot()
                yield f"data: {json.dumps(p)}\n\n"
                if p["finished"] or p["interrupted"]:
                    return
                time.sleep(2)

        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache"})

    @app.post("/stop")
    def stop():
        from sweep import runs as runs_mod
        proc = app.state.get("proc")
        if proc is not None:
            runs_mod.stop(proc)
        return redirect(url_for("running"))

    def _results_page(error=None):
        """Shared by GET /results and a failed POST /rescore, so an
        out-of-range hours value re-renders the same screen with an error
        instead of a bare 400."""
        profile = app.state["profile"]
        all_rows = read_rows(profile)

        min_score = request.args.get("min", type=int) or 0
        source = request.args.get("source") or ""
        q = (request.args.get("q") or "").strip()

        rows = [r for r in all_rows if _as_int(r.get("score")) >= min_score]
        if source:
            rows = [r for r in rows if r.get("source_site") == source]
        if q:
            needle = q.lower()
            rows = [r for r in rows if needle in
                    f"{r.get('title', '')} {r.get('company', '')}".lower()]
        # Sorted explicitly rather than trusting the CSV's own order — the
        # real files happen to arrive score-descending today, but that's
        # another script's undocumented behaviour, not a guarantee.
        rows.sort(key=lambda r: _as_int(r.get("score")), reverse=True)

        sources = sorted({r.get("source_site") for r in all_rows
                           if r.get("source_site")})

        return render_template("results.html", **shell(
            "results", buckets=bucket_rows(rows), sections=SECTIONS,
            total=len(rows), all_total=len(all_rows),
            min_score=min_score, source=source, q=q, sources=sources,
            # Same rule plan.cost() and snapshot() use — a site listed at a
            # $0.00 rate is free either way, never a second "is this site
            # free" rule that could disagree with them.
            rates=config.SITE_RATES,
            worst=worst_filter(all_rows, min_score, source, q),
            rescoring=_rescore_in_flight(),
            error=error))

    def _rescore_in_flight():
        """Whether a re-score child is still running. rescore_from_apify.py
        truncates jobs_combined.csv and .json in place with no lock, so two of
        them overlapping would interleave writes to the same files."""
        proc = app.state.get("rescore_proc")
        return proc is not None and proc.poll() is None

    @app.get("/results")
    def results():
        if not app.state.get("profile"):
            return redirect(url_for("upload"))
        return _results_page()

    @app.post("/rescore")
    def rescore():
        if not app.state.get("profile"):
            return redirect(url_for("upload"))
        # Re-reading the shortlist takes seconds to minutes, so without this
        # the page comes back unchanged and the honest reading is that the
        # button did nothing — which invites a second click, and a second
        # child truncating the same file the first is still writing.
        if _rescore_in_flight():
            return _results_page(
                error="A re-score is still running. Reload in a moment to see "
                      "the new ranking."), 409
        try:
            # Label matches the field's own visible text, so the error names
            # the control the user is looking at.
            hours = _parse_int(request.form.get("hours") or "6", 1, 168,
                                "Hours to look back")
        except _FormError as exc:
            return _results_page(error=str(exc)), 400
        app.state["rescore_proc"] = start_rescore(app.state["profile"], hours)
        return redirect(url_for("results"))

    return app


def _write_run_json(state, output_dir):
    """Persist what a reload needs: which profile, and the spend baseline.

    baseline_usd may be None (json.dump writes it as null) — that means
    read_spend() couldn't get a figure, not that the account has spent
    nothing. Task 8 must treat a None baseline as "show the raw current
    spend with a caveat", never subtract it as if it were 0 — that would
    report the account's whole month-to-date spend as this sweep's cost.
    """
    import json
    out_dir = os.path.join(output_dir, state["profile"])
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "run.json"), "w") as fh:
        json.dump({"profile": state["profile"],
                   "baseline_usd": state["baseline_usd"],
                   "planned": state["plan"]["total_searches"]}, fh)


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
