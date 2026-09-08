"""Routes. Business logic lives in sweep.plan and sweep.runs."""

import os

from flask import (Flask, redirect, render_template, request, url_for)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESUME_DIR = os.path.join(REPO_ROOT, "auto-apply", "resume")

STEPS = [("upload", "Upload"), ("review", "Review"), ("key", "Connect key"),
         ("configure", "Configure"), ("confirm", "Confirm"),
         ("running", "Running"), ("results", "Results")]


def create_app(state=None, extract=None):
    app = Flask(__name__)
    app.state = state if state is not None else {}
    if extract is None:
        import sys
        sys.path.insert(0, os.path.join(REPO_ROOT, "auto-apply"))
        import resume_parser
        extract = resume_parser.extract_text

    def shell(step, **kw):
        """Every screen gets the meter reflecting ITS OWN state, never a
        figure carried over from another step."""
        return dict(steps=STEPS, step=step,
                    spend=app.state.get("spend", 0.0),
                    cap_usd=app.state.get("cap_usd"),
                    fill_pct=app.state.get("fill_pct", 0), **kw)

    @app.get("/")
    def upload():
        return render_template("upload.html", **shell("upload"))

    @app.post("/resume")
    def resume():
        upload_file = request.files.get("resume")
        if upload_file is None or not upload_file.filename:
            return render_template(
                "upload.html", **shell("upload", error="Choose a PDF to upload.")), 400

        os.makedirs(RESUME_DIR, exist_ok=True)
        path = os.path.join(RESUME_DIR, "resume.pdf")
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

    @app.get("/review")
    def review():
        return "review"          # Task 4 replaces this

    @app.get("/key")
    def key():
        return "key"             # Task 5 replaces this

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
