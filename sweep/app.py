"""Routes. Business logic lives in sweep.plan and sweep.runs."""

import os
import re
import sys
import threading
from datetime import datetime

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
import scraper  # noqa: E402

# Flask-free logic lives in sweep.logic — form validation, the reachability
# split, the empty-result diagnosis. Re-exported here because these are part
# of this module's surface for its callers and tests, and because the split
# exists to make them reachable WITHOUT a Flask test client, not to hide them.
from sweep.logic import (  # noqa: E402,F401
    SECTIONS, _FormError, _SCOPE, _as_int, _configure_overrides, _parse_int,
    _valid_profile_name, bucket_rows, fill_pct, paid_sites, site_label, step_states,
    worst_filter)

STEPS = [("upload", "Upload"), ("review", "Review"), ("key", "Connect key"),
         ("configure", "Configure"), ("confirm", "Confirm"),
         ("running", "Running"), ("results", "Results")]

# account_usage_usd is a live Apify call. /events polls snapshot() every 2s
# for up to a ~40-minute sweep — read_spend is throttled to once per this
# many seconds and the value cached on app.state in between, or that would
# be ~1200 live requests for a figure that moves slowly.
SPEND_POLL_SECONDS = 15

# The cap POST /run stamps into the profile is the ESTIMATE times this, not
# the estimate itself. SITE_RATES are measured averages, so a real sweep lands
# near the estimate but not on it — and /confirm says so in as many words. A
# cap equal to the estimate would abort a sweep that came in 10% high, having
# already paid for most of it, which is a worse outcome than the small
# overshoot it prevents.
SPEND_CAP_HEADROOM = 1.25
# Naukri alone is $0.50 per run minimum (config.SITE_RATES), so a cap below
# that would stop the sweep before its first search could finish.
SPEND_CAP_FLOOR_USD = 0.50


def spend_cap_for(estimate_usd):
    """The hard stop to write into the profile for a plan estimated at this."""
    return round(max(SPEND_CAP_FLOOR_USD, estimate_usd * SPEND_CAP_HEADROOM), 2)


class PlanUnavailable(Exception):
    """The engine could not produce a plan for this profile.

    Raised at the subprocess boundary only. plan.fetch() shells out to
    `scraper.py --dry-run --json` and raises CalledProcessError for a profile
    that will not import — which reached Flask uncaught, so /configure and
    /confirm answered a broken profile with a bare 500 and /estimate with an
    HTML error page the fetch could not parse.

    Deliberately NOT wrapped around plan.cost(): a KeyError in the costing
    itself is a bug in this code and should surface as one, not be reported
    to the user as a bad profile.
    """

    def __init__(self, profile):
        super().__init__(f"could not plan profile {profile!r}")
        self.profile = profile


def next_token_name(env=None):
    """The first unused APIFY_TOKEN_* slot.

    Chosen by NAME, not by counting keys: a slot emptied by hand in .env must
    be filled rather than skipped, and two keys must never land on one name.
    Deliberately reads the raw names instead of scraper.apify_tokens(), whose
    dedupe hides a slot that holds a redundant copy of another key — writing
    over that name would be a silent overwrite.
    """
    env = os.environ if env is None else env
    taken = {n for n in env if n.startswith("APIFY_TOKEN_")}
    slot = 2
    while f"APIFY_TOKEN_{slot}" in taken:
        slot += 1
    return f"APIFY_TOKEN_{slot}"


def sweep_budget(credits):
    """(what one sweep can spend, total across every key) for verified
    per-key credit figures.

    These are two different numbers and conflating them was a money bug.
    scraper._require_token() builds ONE ApifyClient for the whole run from the
    single account with the most headroom, and the credit-exhausted branch
    tells the user to RERUN with another token — so no sweep spends across two
    accounts. Summing the keys therefore told the meter a $6 plan was
    affordable on two $3 accounts that could not fund it between them, and
    every extra key multiplied the error.

    The total is still worth showing: .done_combos means a stopped sweep
    resumes on the next key without re-billing finished searches, so several
    keys really do finish a sweep one account could not — just across runs,
    not within one.
    """
    figures = [max(0.0, c) for c in credits if c is not None]
    if not figures:
        return None, 0.0
    return max(figures), round(sum(figures), 4)


def planned_keys(state):
    """Ledger keys this sweep intends to write, in plan order."""
    from sweep import runs as runs_mod
    return runs_mod.combo_keys(state["raw_plan"], runs_mod.today())


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


def create_app(state=None, extract=None, resume_dir=None,
               max_upload_bytes=15 * 1024 * 1024, derive=None,
               check_token=None, env_path=None, fetch_plan=None,
               start_sweep=None, read_spend=None, output_dir=None,
               read_done=None, now=None, read_rows=None,
               start_rescore=None, hour_now=None, profile_exists=None):
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

    if profile_exists is None:
        def profile_exists(name):
            return os.path.exists(
                os.path.join(REPO_ROOT, "profiles", f"{name}.py"))

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
                        "credit and will not guess at it. Set a monthly "
                        "limit on the account, or run from the command line, "
                        "where the profile's own cap is the only guard.")
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
            """This profile's shortlist, newest first, or [] if none yet.

            Prefers jobs_combined*.csv, which merge_jobs.py and
            rescore_from_apify.py write and which spans every sweep. Falls
            back to the newest jobs_<date>_<time>.csv, because **scraper.py
            never writes a combined file** — only merge_jobs.py and a
            re-score do. Without the fallback a UI-driven sweep finishes, is
            billed, and the results screen says nothing was found: 8 of the 16
            profile directories in this repo today have stamped sweep output
            and no combined file at all.

            The fallback is safe to show whole: scraper.py's emit() writes the
            cumulative row set at every checkpoint (scraper.py:1686-1697), so
            a stamped file is the full sweep so far, not a fragment of it.

            Reads through the injected output_dir, same as read_done, so a
            test can never reach a real profile's real (paid, unrecoverable)
            output directory by picking a colliding profile name.
            """
            import csv
            import glob
            base = os.path.join(output_dir, profile)
            merged = sorted(glob.glob(os.path.join(base, "jobs_combined*.csv")),
                            key=os.path.getmtime, reverse=True)
            stamped = sorted(glob.glob(os.path.join(base, "jobs_2*.csv")),
                             key=os.path.getmtime, reverse=True)
            files = merged or stamped
            if not files:
                return []
            with open(files[0], newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            for row in rows:
                # Which file this came from, so the screen can say whether it
                # is showing every sweep or only the most recent one.
                row.setdefault("_merged", "1" if merged else "")
            return rows

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
            p["spend"] = spend_delta()

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
        from sweep import plan as plan_mod

        try:
            raw = fetch_plan(profile)
        except Exception as exc:
            # Logged, not rendered: the detail is useful in the terminal the
            # user is already running this from, and engine stderr is
            # unbounded output that has no business being echoed into a page
            # on the same screen where a key gets pasted.
            app.logger.warning("plan failed for %r: %s", profile, exc)
            raise PlanUnavailable(profile) from exc
        out = plan_mod.cost(raw, config.SITE_RATES, config.SITE_RATE_BASIS)
        cap = app.state.get("cap_usd")
        out["over_cap"] = bool(cap is not None and out["total"] > cap)
        out["shortfall"] = (round(max(0.0, out["total"] - cap), 4)
                             if cap is not None else 0.0)
        # The hard stop POST /run will write into the profile. Computed here,
        # once, so the figure /confirm promises the user and the figure the
        # engine enforces cannot be two different numbers.
        out["spend_cap"] = spend_cap_for(out["total"])
        app.state["raw_plan"] = raw
        app.state["plan"] = out
        return out

    def no_key_yet():
        """No verified key on file, so no credit figure to be honest against.

        cap_usd is the flag with a reader: costed()'s over_cap is
        `cap is not None and total > cap`, which is False for ANY total while
        it is None — so every money screen downstream of here would show an
        advisory cap that can never trip, and the engine would run on
        whatever APIFY_TOKEN happens to already be in .env. Every route that
        prices or launches a sweep fails closed on this, not just one.
        """
        return app.state.get("cap_usd") is None

    def shell(step, spend=0.0, spend_is_this_sweep=True, **kw):
        """Every screen gets the meter reflecting ITS OWN state, never a
        figure carried over from another step.

        `spend` is an ARGUMENT, not a lookup: it used to be read from
        app.state["spend"], which /configure set to the estimate and no later
        screen overwrote — so /results showed the estimate under the label
        "Spent" with nothing spent at all. A shared mutable figure cannot
        satisfy the rule in the line above, so there isn't one.

        None means "not known" and renders as that, never as $0.00: a
        fabricated zero on a money display is the same defect pointing the
        other way.

        `spend_is_this_sweep` is False when the figure is the account's
        month-to-date total with no baseline to subtract. The meter then
        must not label it "spent so far", must not paint it the over-cap
        red, and must not fill the bar against the cap — R74 gated the
        Alpine layer of exactly this and left the server-rendered layer,
        so the first paint was the dishonest state it removed.
        """
        cap = app.state.get("cap_usd")
        return dict(steps=step_states(STEPS, app.state, step),
                    step=step, spend=spend, cap_usd=cap,
                    spend_is_this_sweep=spend_is_this_sweep,
                    fill_pct=fill_pct(spend, cap) if spend_is_this_sweep else 0,
                    # Counted from the environment, not from state: that is
                    # what the engine will actually discover, so a key left in
                    # .env by an earlier session is included rather than the
                    # screen claiming fewer keys than the sweep will see.
                    keys_attached=len(scraper.apify_tokens()),
                    credit_total_usd=app.state.get("credit_total_usd"),
                    **kw)

    def spend_delta():
        """This sweep's own spend, or None when it cannot be known.

        account_usage_usd is month-to-date, so a baseline is subtracted; a
        None baseline means "unknown" and must never be subtracted as zero,
        which would report the account's whole month as this sweep's cost.
        Shared with snapshot() so the two screens cannot disagree about a
        KNOWN figure. They do differ deliberately when the baseline is
        unknown: snapshot() branches before this and shows month-to-date with
        a caveat, because a running sweep is better served by a real number
        it can qualify than by nothing, while /results shows "not known"
        rather than attribute a whole month to one sweep.
        """
        spend_now = app.state.get("spend_read_val")
        baseline = app.state.get("baseline_usd")
        if spend_now is None or baseline is None:
            return None
        return round(max(0.0, spend_now - baseline), 4)

    limit_mb = max_upload_bytes / (1024 * 1024)

    def upload_screen(**kw):
        """The front door, from all four ways it is reached: a fresh visit,
        the two rejected-file paths, and the 413 handler.

        The size limit is injected, so the copy and the client-side check both
        read it from here — a template that names 15 MB beside an app
        configured for 1 MB tells the user the wrong number.

        `read` is the CACHED derivation and never a fresh model call: coming
        back to step 1 with a résumé already read should show what the model
        found, not claim nothing has been read, and this screen must not be
        able to spend anything.
        """
        return render_template("upload.html", **shell(
            "upload", max_mb=limit_mb, read=app.state.get("derived"),
            paid=[site_label(s) for s in paid_sites()], **kw))

    @app.errorhandler(413)
    def too_large(e):
        return upload_screen(
            error=f"That file is larger than {limit_mb:g} MB. "
                  "Export a smaller PDF and try again."), 413

    @app.get("/")
    def upload():
        return upload_screen()

    @app.post("/resume")
    def resume():
        upload_file = request.files.get("resume")
        if upload_file is None or not upload_file.filename:
            return upload_screen(error="Choose a PDF to upload."), 400

        os.makedirs(resume_dir, exist_ok=True)
        path = os.path.join(resume_dir, "resume.pdf")
        upload_file.save(path)

        text = extract(path)
        if not text.strip():
            return upload_screen(
                error="That PDF has no text in it — it is probably a scan. "
                      "Export a text PDF and try again."), 400

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
        # The model call takes seconds, so it deliberately does NOT happen in
        # this render. It used to, which meant the browser sat on the PREVIOUS
        # page for the whole wait with nothing the server could show — there
        # was no response to put a loading state in. Hand back the working
        # screen instead and let POST /derive make the call: a form POST keeps
        # that screen on display until the redirect lands.
        if app.state.get("derived") is None:
            return render_template("deriving.html", **shell("review"))
        derived = derived_for_state()
        commodity = [w["term"] for w in derived["skill_weights"]
                     if w["weight"] <= COMMODITY_WEIGHT]
        # The model already read the résumé, so the person's own name is
        # right there — typing it again was busywork. A profile chosen
        # earlier in the session still wins: that is the name the rest of the
        # flow is already using.
        suggested = (app.state.get("profile")
                     or make_profile.profile_name_for(derived))
        return render_template("review.html", **shell(
            "review", derived=derived, commodity=commodity,
            suggested_name=suggested,
            # Surfaced on arrival, not after a rejected submit: a suggested
            # name is very often one the user already has a profile for, and
            # autofilling straight into a guaranteed 409 would just trade one
            # piece of friction for another.
            clash=(suggested if suggested and profile_exists(suggested)
                   else None)))

    @app.post("/derive")
    def derive_post():
        """Make the one model call, then send the user to the real screen.

        A POST, not a GET, because it is not idempotent — it spends a model
        call. derived_for_state() caches on state, so a reload after this
        lands on the review screen rather than paying twice.
        """
        if not app.state.get("resume_text"):
            return redirect(url_for("upload"))
        try:
            derived = derived_for_state()
        except Exception as exc:
            # The message is fixed, not str(exc): a client library's error can
            # carry the request URL, and this app's whole job is to be careful
            # with the credentials in .env.
            app.logger.warning("derive failed: %s", exc)
            return render_template("deriving.html", **shell(
                "review",
                error="The model could not read that résumé. The reason is in "
                      "the terminal running Sweep — a scanned PDF with no "
                      "text layer is the usual cause.")), 502
        # A falsy derivation is indistinguishable from "not derived yet" on
        # state, so GET /review would hand back the working screen — which
        # submits ITSELF, calling the model again, once per lap, forever.
        # Reported rather than redirected: the error screen carries no
        # auto-submit, which is what breaks the loop.
        if not derived:
            app.logger.warning("derive returned %r for the résumé", derived)
            return render_template("deriving.html", **shell(
                "review",
                error="The model returned nothing for that résumé. Try a "
                      "text-based PDF export.")), 502
        return redirect(url_for("review"))

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
        # Two parallel lists, so a desync would reassign weights to the wrong
        # terms — silently, and on the numbers that decide the ranking.
        # Browsers submit in document order, but fail closed rather than
        # trust that.
        terms = request.form.getlist("term")
        weights = request.form.getlist("weight")
        if len(terms) != len(weights):
            return render_template("review.html", **shell(
                "review", derived=derived, commodity=commodity,
                suggested_name=name,
                error="The weights didn't come through — reload the page "
                      "and try again.")), 400
        try:
            edited = {term: _parse_int(raw, 1, 5, f"Weight for {term}")
                      for term, raw in zip(terms, weights)}
        except _FormError as exc:
            return render_template("review.html", **shell(
                "review", derived=derived, commodity=commodity,
                suggested_name=name, error=str(exc))), 400

        kept = dict(derived)
        kept["skill_weights"] = [
            dict(w, weight=edited.get(w["term"], w["weight"]))
            for w in derived["skill_weights"] if w["term"] not in dropped]
        # Refuse to overwrite an existing profile unless the user says so.
        # This screen writes profiles/<name>.py, /estimate rewrites the same
        # file on every configure change, and a profile can carry weeks of
        # hand-tuning — profiles/kartik_reachable.py exists precisely because
        # someone tuned it against a real sweep.
        if profile_exists(name) and not request.form.get("overwrite"):
            return render_template("review.html", **shell(
                "review", derived=derived, suggested_name=name,
                clash=name,
                error=f"A profile named {name} already exists. Pick another "
                      f"name, or confirm you want to replace it.")), 409

        source = make_profile.render(name, kept, _prefs(app.state))
        app.write_profile(name, source)
        # The reviewed list becomes the state, not just the file. /estimate
        # re-renders this same profile from state["derived"] on every
        # Configure change, so leaving the model's original here put every
        # dropped term and every un-edited weight straight back — a skill
        # pruned on this screen was silently restored by the first click on
        # the next one, with the profile then scoring against it.
        app.state["derived"] = kept
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
        # This screen sets the PRIMARY key, so it replaces the per-key ledger
        # rather than adding to it: re-pasting a first key must not leave the
        # credit of a key no longer on file still counted in the budget.
        # A cap can never go negative — the account may already be over its
        # own monthly limit, but a negative number makes the meter meaningless.
        app.state["key_credit"] = {"APIFY_TOKEN": available}
        app.state["cap_usd"], app.state["credit_total_usd"] = sweep_budget(
            app.state["key_credit"].values())
        return redirect(url_for("configure"))

    @app.errorhandler(PlanUnavailable)
    def plan_unavailable(exc):
        """One handler for every screen that prices a plan.

        Registered rather than caught per route so a route added later gets
        this instead of a bare 500 — /configure and /confirm both reached
        Flask uncaught before, and /run reads the plan they store.
        """
        return render_template("plan_error.html", **shell(
            None, profile=exc.profile)), 500

    @app.get("/configure")
    def configure():
        if not app.state.get("profile"):
            return redirect(url_for("review"))
        if no_key_yet():
            return redirect(url_for("key"))
        estimate = costed(app.state["profile"])
        chosen = app.state.get("sites_enabled") or {}
        return render_template("configure.html", **shell(
            "configure", spend=estimate["total"], estimate=estimate,
            # Default True, not False: an unset profile inherits config.py's
            # SITES, where all three are on. Rendering them unchecked would
            # show a state the profile does not have, and the first change to
            # any other field would then post that lie back and switch them
            # all off.
            sites=[{"name": site, "on": chosen.get(site, True),
                    # A site bills per run when config.py pins its depth —
                    # the reason the depth control cannot move naukri.
                    "per_run": bool(config.SITES[site].get("results_per_run"))}
                   for site in paid_sites()]))

    @app.post("/estimate")
    def estimate():
        from flask import jsonify
        if not app.state.get("profile"):
            return jsonify({"error": "No profile yet — approve the review first."}), 409
        if no_key_yet():
            return jsonify({"error": "Connect your Apify key first."}), 409

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

        # The caller is a fetch() doing r.json(), so this cannot fall through
        # to the HTML handler below: an error page would fail to parse and
        # read as "the network is down" on the screen whose whole job is a
        # live cost.
        try:
            return jsonify(costed(app.state["profile"]))
        except PlanUnavailable:
            return jsonify({"error": "The engine could not price that "
                                     "combination. Nothing was charged."}), 400

    @app.get("/confirm")
    def confirm():
        if not app.state.get("profile"):
            return redirect(url_for("configure"))
        if no_key_yet():
            return redirect(url_for("key"))
        # Re-costed on every visit, like /configure — the meter shows this
        # screen's own state, never a figure carried over from an earlier one.
        plan = costed(app.state["profile"])
        return render_template("confirm.html", **shell(
            "confirm", spend=plan["total"], plan=plan,
            spans_midnight=_spans_midnight()))

    @app.post("/run")
    def run():
        # Fail closed: no plan at all (a /run hit that never went through
        # /confirm) must refuse exactly like an over-cap plan does, not
        # launch an uncapped subprocess because an empty dict's .get()
        # reads as falsy the same as a real "under cap" plan would.
        plan_now = app.state.get("plan")
        if not plan_now:
            return "No plan to run — start from Configure.", 400
        if no_key_yet():
            return "No key connected — connect one before running.", 400
        if plan_now.get("over_cap"):
            return _confirm_page(error="Attach a second key or narrow the search first.", status=400)

        # Stamp the real cap into the profile BEFORE the child starts.
        # SETTINGS["max_spend_usd"] is the only guard that can actually stop
        # an overspend — scraper.py:1748 re-reads the account after every
        # search — and make_profile.render() omitting the key means "inherit
        # config.py's None", i.e. no cap at all. So this is what makes the
        # README's "a wrong estimate cannot cause an overspend" true.
        # Written through render(), never an f-string: it is also what
        # validates every key against the live config.
        # Checked and set under one lock, the same shape /rescore uses. The
        # dev server is threaded, this route makes a live Apify call before it
        # redirects, and /second-key sends the user back to /confirm with a
        # live Run button while the first sweep is still going — so a second
        # launch is a double-click or a documented gesture away, and it spends
        # real money. The profile rewrite is inside too: a request refused
        # with 409 should leave no trace, and it must still land before the
        # child starts.
        with _run_lock:
            if _sweep_in_flight():
                return _confirm_page(
                    error="A sweep is already running. Watch it on the "
                          "running screen, or stop it before starting "
                          "another.", status=409)
            app.state["max_spend_usd"] = plan_now["spend_cap"]
            app.write_profile(app.state["profile"], make_profile.render(
                app.state["profile"], app.state["derived"], _prefs(app.state)))
            # baseline_usd may be None (see read_spend's docstring) —
            # recorded as-is, never coerced to 0.0, so the meter can tell
            # "unknown" from "no spend yet".
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
        # Every key on file, not the first two: with a third or fourth
        # attached, re-pasting one of those passed this check and its credit
        # was counted twice.
        if token and token in {tok for _, tok in scraper.apify_tokens()}:
            return _confirm_page(error="That's the same key already on file — it adds no "
                      "new credit.", status=400)

        if not token:
            return _confirm_page(error="Paste your Apify token.", status=400)
        if not _ENV_VALUE_RE.fullmatch(token):
            return _confirm_page(
                error="That doesn't look like an Apify token — printable "
                      "characters, no spaces.", status=400)
        available, error = check_token(token)
        if error:
            return _confirm_page(error=error, status=400)
        if available <= 0:
            return _confirm_page(error="That key verified, but it has no credit "
                      "available.", status=400)

        slot = next_token_name()
        app.write_env(slot, token)
        os.environ[slot] = token
        credits = dict(app.state.get("key_credit") or {})
        if not credits and app.state.get("cap_usd") is not None:
            # A cap with no per-key ledger behind it (a session resumed after
            # a restart): that cap is one account's credit, so carry it as one
            # entry rather than dropping it on the floor here.
            credits["APIFY_TOKEN"] = app.state["cap_usd"]
        credits[slot] = available
        app.state["key_credit"] = credits
        # max, not sum — see sweep_budget. Adding a key raises what one sweep
        # can spend only if that key alone is bigger than the best already on
        # file; it always raises the total you can finish the sweep across.
        app.state["cap_usd"], app.state["credit_total_usd"] = sweep_budget(
            credits.values())
        # over_cap is not patched here — GET /confirm re-costs the whole
        # plan via costed() on the redirect below, so any value written
        # here would be discarded before ever being read.
        return redirect(url_for("confirm"))

    @app.get("/running")
    def running():
        if not app.state.get("raw_plan"):
            return redirect(url_for("configure"))
        progress_now = snapshot()
        return render_template("running.html", **shell(
            "running", spend=progress_now["spend"],
            spend_is_this_sweep=progress_now["baseline_known"],
            progress=progress_now, plan=app.state["plan"]))

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

    def _results_page(error=None, notice=None):
        """Shared by GET /results and a failed POST /rescore, so an
        out-of-range hours value re-renders the same screen with an error
        instead of a bare 400."""
        profile = app.state["profile"]
        all_rows = read_rows(profile)
        # A single sweep's file is complete for that sweep but does not span
        # earlier ones, and merge_jobs.py is what combines them.
        merged = bool(all_rows) and all_rows[0].get("_merged") == "1"

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
            # What this sweep actually cost, or None when no run recorded a
            # baseline to subtract from. Never the estimate.
            "results", spend=spend_delta(),
            buckets=bucket_rows(rows), sections=SECTIONS,
            total=len(rows), all_total=len(all_rows),
            min_score=min_score, source=source, q=q, sources=sources,
            # Same rule plan.cost() and snapshot() use — a site listed at a
            # $0.00 rate is free either way, never a second "is this site
            # free" rule that could disagree with them.
            rates=config.SITE_RATES, merged=merged,
            # Only consumed when nothing survived the filters, and it makes
            # one pass over every unfiltered row per active filter — three
            # passes over up to 1607 rows, thrown away, on every page load.
            worst=(worst_filter(all_rows, min_score, source, q)
                   if not rows else None),
            rescoring=_rescore_in_flight(),
            notice=notice, error=error))

    # Wall-clock hour, injectable so the midnight re-bill warning is
    # testable. Separate from `now`, which is a monotonic clock for the
    # spend-poll throttle and says nothing about the time of day.
    if hour_now is None:
        def hour_now():
            return datetime.now().hour

    def _spans_midnight():
        """A sweep started this late will still be running after midnight,
        and .done_combos is scoped to a single day — so every search it had
        already finished gets re-run and re-billed."""
        return hour_now() >= 22

    def _confirm_page(error=None, status=200):
        """confirm.html with the live plan. Four routes rendered this inline
        with spans_midnight hardcoded False, so any error after 22:00 threw
        away the one warning that prevents a real double bill."""
        plan_now = app.state.get("plan")
        return render_template("confirm.html", **shell(
            "confirm", spend=plan_now["total"], plan=plan_now,
            spans_midnight=_spans_midnight(), error=error)), status

    _run_lock = threading.Lock()
    _rescore_lock = threading.Lock()

    def _sweep_in_flight():
        """Whether a sweep child is still running.

        Two sweeps on one profile append to the same .done_combos and
        truncate the same jobs_*.json, so the second re-bills searches the
        first already paid for and the output interleaves. Worse,
        app.state["proc"] holds one child, so a second launch orphans the
        first: POST /stop can only signal the one it can see.
        """
        proc = app.state.get("proc")
        return proc is not None and proc.poll() is None

    # ponytail: in-memory and single-process, like the re-score flag. run.json
    # carries no pid, so restarting the server mid-sweep reports idle and the
    # next POST /run launches a second paid child over a live one — the
    # original defect, through a narrower door. Persist the pid alongside
    # run.json if the server is ever restarted mid-sweep in practice.

    def _results_url():
        """/results carrying whatever filters the request arrived with, so a
        redirect does not silently clear them. The error paths were fixed for
        this and the success path was left bare, which is the same
        one-instance-only fix this branch keeps making."""
        return url_for("results",
                       min=request.args.get("min", type=int) or None,
                       source=request.args.get("source") or None,
                       q=(request.args.get("q") or "").strip() or None)

    def _rescore_in_flight():
        """Whether a re-score child is still running.

        rescore_from_apify.py truncates jobs_combined.csv and .json in place
        with no lock of its own, so two overlapping children interleave writes
        to the same two files.

        ponytail: in-memory and single-process. Restarting the server while a
        child runs loses this and orphans it, and there is no way to cancel a
        running re-score from the UI — POST /stop signals app.state["proc"]
        only. Both are acceptable for one local user driving one button by
        hand; persist it alongside run.json if a second entry point ever
        starts a re-score.
        """
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
        try:
            # Label matches the field's own visible text, so the error names
            # the control the user is looking at.
            hours = _parse_int(request.form.get("hours") or "6", 1, 168,
                                "Hours to look back")
        except _FormError as exc:
            return _results_page(error=str(exc)), 400
        # Checked and set under one lock. The dev server runs threaded, so
        # without it two clicks a few milliseconds apart both read "nothing
        # running" and both spawn a child truncating the same file.
        with _rescore_lock:
            if _rescore_in_flight():
                # A status, not a failure — so it must not go through `error`,
                # which is painted the red reserved for over-cap.
                return _results_page(
                    notice="A re-score is already running. Reload in a moment "
                           "to see the new ranking."), 409
            app.state["rescore_proc"] = start_rescore(
                app.state["profile"], hours)
        return redirect(_results_url())

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
        # Set by POST /run only (see spend_cap_for). Unset means the profile
        # inherits config.py's None, i.e. no cap — so /run must always set it.
        "max_spend_usd": state.get("max_spend_usd"),
        # Unset (None) means "not touched by the Configure screen yet" —
        # make_profile.render() omits the key entirely in that case, so
        # config.py's own default silently applies instead of being reset.
        "remote_scopes": state.get("remote_scopes"),
        "max_age_days": state.get("max_age_days"),
        "max_results": state.get("max_results"),
        "linkedin_locations": state.get("linkedin_locations"),
        "linkedin_remote_only": state.get("linkedin_remote_only"),
        # Unset means "inherit config.py's SITES", the same rule as every
        # other optional key here — never {} , which would render an overlay
        # switching every paid site off.
        "sites_enabled": state.get("sites_enabled"),
    }
