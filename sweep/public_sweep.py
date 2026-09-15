"""The public beta's sweep half: configure, run, watch, results, export.

The local console runs a sweep as a child process and prices it against
an Apify balance. Neither is available here — Render Free kills children
when a tab closes, and nobody's card is on file — so the public flow gets
its own screens on top of the Oracle worker:

    review -> configure -> confirm -> run -> running -> results -> export

What that buys, against re-enabling the console's routes: the paid plan,
the spend meter, the key screens and the subprocess stay exactly where
they are, untouched, and nothing public can reach them. What it costs is
four small templates, which is the cheaper half of the trade.

The filtering, sorting and bucketing are NOT reimplemented: results run
through the same sweep.logic functions the local screen uses, so a row
lands in the same section here as it does there. The scoring happened on
Oracle, in the same engine, from the same rendered profile.

Ownership is the cookie's: every worker call carries an owner derived
from the signed session (sweep.public.owner_for_session), so a visitor
sees their own run and no one else's — including after Render restarts,
when the run id comes back from the cookie and this process has no memory
of them at all.
"""

import time

from flask import (Response, abort, redirect, render_template, request,
                   url_for)

from sweep import exports, public, worker_client
from sweep.logic import (DEFAULT_SORT, SECTION_CAP, SECTIONS, SORTS,
                         _configure_overrides, _FormError, bucket_rows,
                         posted_age, searchable_locations, shortlist)

# The public tracker. Five steps, all of them reachable by a visitor.
PUBLIC_STEPS = [("upload", "Upload"), ("review", "Review"),
                ("beta_configure", "Configure"), ("beta_running", "Sweep"),
                ("beta_results", "Results")]

ENDPOINTS = frozenset({
    "beta_configure", "beta_confirm", "beta_run", "beta_running",
    "beta_progress", "beta_stop", "beta_results", "beta_export",
})

# How long the running screen waits between polls. Not SSE: an event
# stream holds one of eight gunicorn threads open for the whole sweep,
# and a sweep is minutes long.
POLL_SECONDS = 4

# A finished state the visitor cannot act on any further.
FINISHED = {"done", "failed", "stopped", "interrupted"}


def register(app):
    """Add the public sweep screens. Called only from public.harden."""

    def derived_or_home():
        """The reviewed profile, or None if this visitor has not made one."""
        return app.state.get("derived") if app.state.get("profile") else None

    def prefs():
        from sweep.app import _prefs
        return _prefs(app.state)

    def run_or_none():
        """This visitor's run, from the cookie — never from memory, which
        a redeploy empties."""
        run_id = public.current_run_id()
        if not run_id:
            return None, None
        try:
            return run_id, worker_client.run_status(run_id)
        except worker_client.RunNotFound:
            # Expired on Oracle (48h) or not ours: forget it rather than
            # showing a screen that can never load.
            public.forget_run()
            return None, None
        except worker_client.WorkerError:
            return run_id, None

    @app.get("/sweep/configure")
    def beta_configure():
        if not derived_or_home():
            return redirect(url_for("review"))
        return render_template("beta_configure.html", **app.shell(
            "beta_configure", location_groups=searchable_locations(),
            chosen=app.state.get("locations") or ["Remote"],
            scope=app.state.get("scope") or "remote",
            max_age_days=app.state.get("max_age_days") or 30))

    @app.post("/sweep/configure")
    def beta_configure_post():
        if not derived_or_home():
            return redirect(url_for("review"))
        try:
            # The same validator the local Configure screen uses: one gate
            # for both, so a field cannot be strict on one screen and lax
            # on the other.
            app.state.update(_configure_overrides(request.form))
            # _configure_overrides folds a scope into the keys it implies
            # and does not keep the word itself; this screen re-renders the
            # choice, so it does.
            if request.form.get("scope"):
                app.state["scope"] = request.form["scope"]
        except _FormError as exc:
            return render_template("beta_configure.html", **app.shell(
                "beta_configure", location_groups=searchable_locations(),
                chosen=app.state.get("locations") or ["Remote"],
                scope=request.form.get("scope") or "remote",
                max_age_days=app.state.get("max_age_days") or 30,
                error=str(exc))), 400
        return redirect(url_for("beta_confirm"))

    @app.get("/sweep/confirm")
    def beta_confirm():
        if not derived_or_home():
            return redirect(url_for("review"))
        run_id, status = run_or_none()
        return render_template("beta_confirm.html", **app.shell(
            "beta_configure",
            locations=app.state.get("locations") or ["Remote"],
            max_age_days=app.state.get("max_age_days") or 30,
            running=bool(status and status.get("state") not in FINISHED)))

    @app.post("/sweep/run")
    def beta_run():
        """Start the sweep on Oracle. Free sources only: no key is asked
        for, none is sent, and the worker refuses a paid run without the
        caller's own token anyway."""
        derived = derived_or_home()
        if not derived:
            return redirect(url_for("review"))
        _old, status = run_or_none()
        if status and status.get("state") not in FINISHED:
            return redirect(url_for("beta_running"))
        try:
            run_id = worker_client.create_run(derived, prefs(), free_only=True)
        except worker_client.WorkerError as exc:
            return render_template("beta_confirm.html", **app.shell(
                "beta_configure",
                locations=app.state.get("locations") or ["Remote"],
                max_age_days=app.state.get("max_age_days") or 30,
                error=f"The sweep service is not available right now: {exc}."
                )), 502
        public.remember_run(run_id)
        app.state["run_started_at"] = time.time()
        return redirect(url_for("beta_running"))

    @app.get("/sweep/running")
    def beta_running():
        run_id, status = run_or_none()
        if not run_id:
            return redirect(url_for("beta_confirm"))
        return render_template("beta_running.html", **app.shell(
            "beta_running", status=status or {}, poll_seconds=POLL_SECONDS))

    @app.get("/sweep/progress")
    def beta_progress():
        """What the running screen polls. Small on purpose: it is fetched
        every few seconds for the length of a sweep."""
        from flask import jsonify

        run_id, status = run_or_none()
        if not run_id:
            return jsonify({"state": "none"}), 404
        if status is None:
            # Oracle unreachable for a moment. Not an error the visitor can
            # act on, and not a reason to throw their run away.
            return jsonify({"state": "unknown", "found": 0})
        found = 0
        try:
            found = worker_client.run_rows(run_id, since=0)["total"]
        except worker_client.WorkerError:
            pass
        return jsonify({
            "state": status.get("state"),
            "queue_position": status.get("queue_position", 0),
            "found": found,
            "finished": status.get("state") in FINISHED,
            "error": status.get("error"),
            "results_url": url_for("beta_results"),
        })

    @app.post("/sweep/stop")
    def beta_stop():
        run_id, _status = run_or_none()
        if run_id:
            with_error = None
            try:
                worker_client.stop_run(run_id)
            except worker_client.WorkerError as exc:
                with_error = exc
            if with_error:
                return redirect(url_for("beta_running"))
        return redirect(url_for("beta_results"))

    def _args():
        """The filters on the querystring, same names as the local screen."""
        try:
            min_score = int(request.args.get("min_score") or 0)
        except ValueError:
            min_score = 0
        return (min_score, request.args.get("source") or "",
                (request.args.get("q") or "").strip(),
                request.args.get("sort") or DEFAULT_SORT)

    def _rows(run_id):
        try:
            return worker_client.all_rows(run_id)
        except worker_client.WorkerError:
            return []

    @app.get("/sweep/results")
    def beta_results():
        run_id, status = run_or_none()
        if not run_id:
            return redirect(url_for("beta_confirm"))
        all_rows = _rows(run_id)
        min_score, source, q, sort = _args()
        rows = shortlist(all_rows, min_score, source, q, sort)
        return render_template("beta_results.html", **app.shell(
            "beta_results", buckets=bucket_rows(rows), sections=SECTIONS,
            total=len(rows), all_total=len(all_rows),
            min_score=min_score, source=source, q=q, sort=sort, sorts=SORTS,
            sources=sorted({r.get("source_site") for r in all_rows
                            if r.get("source_site")}),
            section_cap=SECTION_CAP, full=bool(request.args.get("full")),
            posted_age=posted_age, status=status or {},
            still_running=bool(status and status.get("state") not in FINISHED)))

    EXPORTS = {
        "csv": ("text/csv; charset=utf-8", exports.as_csv),
        "json": ("application/json; charset=utf-8", exports.as_json),
        "xlsx": ("application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet", exports.as_xlsx),
    }

    @app.get("/sweep/export.<fmt>")
    def beta_export(fmt):
        """The listings on screen, as a file — the same rows, in the same
        order, because both go through shortlist() and bucket_rows()."""
        if fmt not in EXPORTS:
            abort(404)
        run_id, _status = run_or_none()
        if not run_id:
            return redirect(url_for("beta_confirm"))
        min_score, source, q, sort = _args()
        rows = shortlist(_rows(run_id), min_score, source, q, sort)
        flat = exports.rows_for_export(bucket_rows(rows), SECTIONS)
        mimetype, build = EXPORTS[fmt]
        body = build(flat)
        return Response(body, mimetype=mimetype, headers={
            "Content-Disposition": f'attachment; filename="sweep.{fmt}"'})

    return app
