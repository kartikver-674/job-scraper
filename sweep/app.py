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


def _valid_profile_name(name):
    return bool(_NAME_RE.fullmatch(name))


def create_app(state=None, extract=None, resume_dir=None,
               max_upload_bytes=15 * 1024 * 1024, derive=None,
               check_token=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = max_upload_bytes
    app.state = state if state is not None else {}
    resume_dir = resume_dir if resume_dir is not None else RESUME_DIR

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
            except Exception:
                return None, "That token was rejected by Apify. Check and retry."
            current = (limits.get("current") or {}).get("monthly_usage_usd") or 0
            allowed = (limits.get("limits") or {}).get("max_monthly_usage_usd")
            if allowed is None:
                return None, ("Apify did not report a monthly limit for this "
                              "account, so Sweep cannot work out your remaining "
                              "credit. Enter your budget on the next screen.")
            return float(allowed) - float(current), None

    def default_write_env(env_key, value):
        """Upsert one key in .env, leaving every other line untouched."""
        path = os.path.join(REPO_ROOT, ".env")
        lines = []
        if os.path.exists(path):
            with open(path) as fh:
                lines = [ln for ln in fh if not ln.startswith(f"{env_key}=")]
        lines.append(f"{env_key}={value}\n")
        with open(path, "w") as fh:
            fh.writelines(lines)

    app.write_env = default_write_env

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

        available, error = check_token(token)
        if error:
            # Never render the token back into the page.
            return render_template("key.html", **shell("key", error=error)), 400

        app.write_env("APIFY_TOKEN", token)
        os.environ["APIFY_TOKEN"] = token
        app.state["cap_usd"] = available
        app.state["token_ok"] = True
        return redirect(url_for("configure"))

    @app.get("/configure")
    def configure():
        return "configure"       # Task 6 replaces this

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
    }
