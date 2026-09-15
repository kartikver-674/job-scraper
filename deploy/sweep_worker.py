"""The sweep worker: long-running sweeps on Oracle, driven by Render.

Render Free cannot host a sweep. It spins down after fifteen idle minutes,
which kills any child it started, and its disk is ephemeral, so results
vanish on the next deploy. Oracle's A1 already runs all day with a real
disk — measured headroom beside the inference service is ~5.7 GB of RAM
against a sweep's 262 MB peak, and a sweep is 4% CPU because it waits on
the network — so the sweep moves there and Render keeps the session.

    browser ──► Render (session, UI) ──► THIS (bearer, private) ──► scraper.py

Three rules shape everything below.

1.  **The Apify token is a visitor's own credential.** It arrives in the
    body of one request, lives in memory only long enough to reach the
    child's environment, and is never written to a status file, a log, a
    command line, a URL or an exception. A run whose token is lost (a
    restart while queued) is interrupted rather than silently retried,
    because the alternative is asking someone to hand it over twice.

2.  **Nothing here executes caller-supplied Python.** The API takes
    structured profile data and renders it with make_profile.render, the
    same deterministic renderer the local app uses, whose values go
    through repr(). The rendered source is then parsed and checked to be
    literal assignments and nothing else, so "malformed JSON cannot
    become code" is a property this file proves rather than assumes.

3.  **State lives on disk, not in this process.** One run at a time with
    a FIFO queue behind it, each under its own directory with durable
    status. A restart re-queues what was queued and marks what was
    running as interrupted; it never loses a run silently.

Runs are deleted after TTL_SECONDS (48h), and never while they are
queued or running.
"""

import ast
import contextlib
import csv
import glob
import hmac
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time

from flask import Flask, jsonify, request

log = logging.getLogger("sweep-worker")

# Where runs live. One directory per run: status.json, profile.py, output/.
RUNS_ROOT = os.environ.get("SWEEP_WORKER_RUNS") or "/var/lib/sweep-worker/runs"
# The repo checkout the engine runs from. Its profiles/ package is where a
# run's rendered profile has to land, because config.py selects a profile by
# MODULE name (importlib.import_module(f"profiles.{PROFILE}")).
CHECKOUT = os.environ.get("SWEEP_WORKER_CHECKOUT") or "/opt/sweep-worker/app"
TOKEN_ENV = "SWEEP_WORKER_TOKEN"

TTL_SECONDS = int(os.environ.get("SWEEP_WORKER_TTL") or 48 * 3600)
# One sweep at a time. The A1 has room for several, but a queue of one is
# the honest starting point: two sweeps on one box compete for the same
# network and the same inference service beside them.
MAX_ACTIVE = 1

QUEUED, RUNNING, DONE, FAILED, STOPPED, INTERRUPTED = (
    "queued", "running", "done", "failed", "stopped", "interrupted")
TERMINAL = {DONE, FAILED, STOPPED, INTERRUPTED}
ACTIVE = {QUEUED, RUNNING}

# A run id is also a Python MODULE name (profiles/beta_<id>.py) and a path
# segment, so it is hex: no hyphens, which import would reject, and nothing
# a path could interpret.
RUN_ID_BYTES = 16
MAX_OWNER = 128
MAX_BODY = 512 * 1024


class Refused(Exception):
    """A request that fails for a reason the caller may be told."""

    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


# --------------------------------------------------------------------------
# The profile: structured in, literals out
# --------------------------------------------------------------------------

def _import_make_profile(checkout):
    """make_profile from the checkout, not from wherever this file sits."""
    for path in (os.path.join(checkout, "auto-apply"), checkout):
        if path not in sys.path:
            sys.path.insert(0, path)
    import make_profile
    return make_profile


def assert_only_literals(source):
    """Parse the rendered profile and refuse anything that is not data.

    This is the guard that makes rule 2 a property rather than a promise.
    A profile is a module of constant assignments; an import, a call, a
    comprehension or an f-string in there would mean caller data had
    become caller code, and this raises instead.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # the docstring
        if not isinstance(node, ast.Assign):
            raise Refused(400, "the rendered profile is not pure data")
        for target in node.targets:
            if isinstance(target, ast.Name):
                continue
            # The one subscript this worker writes itself: the run's own
            # output directory. Still checked, never trusted.
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and isinstance(target.slice, ast.Constant)):
                continue
            raise Refused(400, "the rendered profile assigns something odd")
        try:
            ast.literal_eval(node.value)
        except (ValueError, SyntaxError, TypeError):
            raise Refused(400,
                          "the rendered profile contains a non-literal value") from None
    return source


def _checked(payload):
    """The structured request, validated before it reaches the renderer."""
    if not isinstance(payload, dict):
        raise Refused(400, "the request body must be an object")
    unknown = set(payload) - {"profile", "prefs", "free_only", "apify_token",
                              "owner"}
    if unknown:
        raise Refused(400, f"unknown field(s): {', '.join(sorted(unknown))}")

    profile, prefs = payload.get("profile"), payload.get("prefs") or {}
    if not isinstance(profile, dict) or not isinstance(prefs, dict):
        raise Refused(400, "profile and prefs must be objects")
    owner = payload.get("owner")
    if not isinstance(owner, str) or not 1 <= len(owner) <= MAX_OWNER:
        raise Refused(400, "owner must be a short opaque string")
    free_only = payload.get("free_only", True)
    if not isinstance(free_only, bool):
        raise Refused(400, "free_only must be true or false")

    token = payload.get("apify_token")
    if token is not None and (not isinstance(token, str) or not token.strip()):
        raise Refused(400, "apify_token must be a non-empty string when given")
    if free_only and token:
        raise Refused(400, "a free sweep must not carry an Apify token")
    if not free_only and not token:
        # Never this worker's own key, and never the operator's: a paid run
        # is funded by whoever asked for it or it does not happen.
        raise Refused(400, "a paid sweep needs the caller's own Apify token")
    return profile, prefs, bool(free_only), token, owner


def paid_sites(checkout=None):
    """The sites that bill. Read from the live config, never a list typed
    here — a site added there must not quietly become free."""
    _import_make_profile(checkout or CHECKOUT)
    import config
    return [site for site in config.SITES if site in config.SITE_RATES]


def free_prefs(prefs, checkout=None):
    """The caller's preferences with every paid board switched off.

    Enforced here rather than trusted from Render, because this is the
    boundary where money would actually be spent: the engine only reaches
    for an Apify token when a paid plan exists (scraper.py: `if plans:`),
    so a free run that leaves the paid sites enabled either dies asking
    for a credential it must never have, or — if one were ever present on
    this box — spends somebody else's.

    Written through the renderer's own sites_enabled, so config._overlay
    still receives each site's WHOLE dict; a hand-rolled SITES literal
    would drop the actor and the rates with it.
    """
    return dict(prefs, sites_enabled={site: False
                                      for site in paid_sites(checkout)})


def render_profile(make_profile, name, profile, prefs, output_dir):
    """Rendered profile source for one run, proven to be data."""
    try:
        source = make_profile.render(name, profile, prefs)
    except Refused:
        raise
    except Exception as exc:
        # The renderer validates every key against the live config, so its
        # complaints are about the caller's data. The type is named; the
        # text is not echoed, because it can quote what was sent.
        raise Refused(400, f"the profile was refused by the renderer "
                           f"({type(exc).__name__})") from None
    # This run's own output directory, written by the worker as a literal
    # so nothing the caller sent can reach the path.
    source += (f'\n# Written by sweep_worker for this run.\n'
               f'SETTINGS["output_dir"] = {output_dir!r}\n')
    return assert_only_literals(source)


# --------------------------------------------------------------------------
# Runs on disk
# --------------------------------------------------------------------------

class RunStore:
    """One directory per run, with durable status.

    Disk rather than memory because a queue that a restart empties is a
    queue that loses somebody's sweep without telling them.
    """

    def __init__(self, root=None):
        self.root = root or RUNS_ROOT
        os.makedirs(self.root, exist_ok=True)

    def dir(self, run_id):
        # Defence in depth: run ids are generated here and hex, but a path
        # built from an id is exactly where a traversal would land.
        if not run_id or not all(c in "0123456789abcdef" for c in run_id):
            raise Refused(404, "no such run")
        return os.path.join(self.root, run_id)

    def output_dir(self, run_id):
        return os.path.join(self.dir(run_id), "output")

    def _status_path(self, run_id):
        return os.path.join(self.dir(run_id), "status.json")

    def write(self, run_id, status):
        """Atomically, so a crash mid-write cannot leave unreadable JSON."""
        path = self._status_path(run_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(status, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return status

    def read(self, run_id):
        try:
            with open(self._status_path(run_id), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            raise Refused(404, "no such run") from None

    def update(self, run_id, **fields):
        status = self.read(run_id)
        status.update(fields)
        return self.write(run_id, status)

    def all(self):
        out = []
        for run_id in sorted(os.listdir(self.root)):
            with contextlib.suppress(Refused):
                out.append(self.read(run_id))
        return out

    def delete(self, run_id):
        shutil.rmtree(self.dir(run_id), ignore_errors=True)


def default_spawn(run_id, run_dir, profile_name, checkout, token):
    """The engine, as its own process, with the token in its env alone.

    Not on the command line: /proc/<pid>/cmdline is world-readable, and a
    credential there is a credential published to every user on the box.
    stdout and stderr go nowhere for a paid run — a traceback from a
    library we do not control is exactly where a token would surface.
    """
    env = dict(os.environ)
    env.pop("APIFY_TOKEN", None)
    for name in [n for n in env if n.startswith("APIFY_TOKEN")]:
        env.pop(name, None)
    if token:
        env["APIFY_TOKEN"] = token
    env["JOB_PROFILE"] = profile_name
    # A free run holds no credential, so its engine output is safe to keep
    # and worth keeping: it is the only account of what the sweep did.
    if token:
        out = err = subprocess.DEVNULL
        closer = None
    else:
        closer = open(os.path.join(run_dir, "engine.log"), "wb")
        out = err = closer
    try:
        return subprocess.Popen(
            [sys.executable, "scraper.py", "--profile", profile_name, "--yes"],
            cwd=checkout, env=env, stdout=out, stderr=err), closer
    except Exception:
        if closer:
            closer.close()
        raise


class Queue:
    """One run at a time, FIFO behind it, recovered from disk on start."""

    def __init__(self, store, spawn=None, checkout=None, clock=time.time,
                 max_active=MAX_ACTIVE):
        self.store = store
        self.spawn = spawn or default_spawn
        self.checkout = checkout or CHECKOUT
        self.clock = clock
        self.max_active = max_active
        self._lock = threading.RLock()
        # run_id -> the caller's Apify token, held ONLY until the child has
        # it. Never written anywhere, and gone the moment the run ends.
        self._tokens = {}
        self._children = {}
        self._waiting = []

    # -- recovery ---------------------------------------------------------

    def recover(self):
        """What a restart does to runs that were in flight.

        Documented rather than clever: a child does not survive its
        parent's restart, so anything RUNNING is interrupted and says so.
        A QUEUED free run is re-queued in the order it arrived. A QUEUED
        PAID run is interrupted, because its token was deliberately never
        written down and asking the engine to start without one would
        either fail obscurely or, far worse, reach for somebody else's.
        """
        with self._lock:
            for status in sorted(self.store.all(),
                                 key=lambda s: s.get("created_at") or 0):
                run_id, state = status.get("run_id"), status.get("state")
                if state == RUNNING:
                    self.store.update(run_id, state=INTERRUPTED,
                                      finished_at=self.clock(),
                                      error="the worker restarted while this "
                                            "run was in flight")
                elif state == QUEUED:
                    if status.get("free_only"):
                        self._waiting.append(run_id)
                    else:
                        self.store.update(
                            run_id, state=INTERRUPTED,
                            finished_at=self.clock(),
                            error="the worker restarted before this run "
                                  "started, and an Apify token is never "
                                  "kept across a restart — start it again")
            self._pump()

    # -- submission -------------------------------------------------------

    def submit(self, run_id, token=None):
        with self._lock:
            if token:
                self._tokens[run_id] = token
            self._waiting.append(run_id)
            self._pump()

    def _pump(self):
        """Start whatever the slots allow, in arrival order."""
        while self._waiting and len(self._children) < self.max_active:
            run_id = self._waiting.pop(0)
            try:
                self._start(run_id)
            except Exception as exc:
                self._tokens.pop(run_id, None)
                with contextlib.suppress(Refused):
                    self.store.update(run_id, state=FAILED,
                                      finished_at=self.clock(),
                                      error=f"could not start "
                                            f"({type(exc).__name__})")

    def _start(self, run_id):
        status = self.store.read(run_id)
        token = self._tokens.pop(run_id, None)  # popped: used once, then gone
        child, closer = self.spawn(run_id, self.store.dir(run_id),
                                   status["profile_name"], self.checkout,
                                   token)
        del token
        self._children[run_id] = (child, closer)
        self.store.update(run_id, state=RUNNING, started_at=self.clock())
        threading.Thread(target=self._wait, args=(run_id,), daemon=True).start()

    def _wait(self, run_id):
        child, closer = self._children[run_id]
        code = child.wait()
        with self._lock:
            self._children.pop(run_id, None)
            if closer:
                with contextlib.suppress(Exception):
                    closer.close()
            # A run whose directory went away while it finished (a stop
            # racing the janitor) is not an error worth raising inside a
            # worker thread, where nothing can catch it.
            try:
                status = self.store.read(run_id)
            except Refused:
                self._pump()
                return
            if status.get("state") == STOPPED:
                pass  # asked for; the exit code is not news
            else:
                self.store.update(
                    run_id, state=DONE if code == 0 else FAILED,
                    finished_at=self.clock(), exit_code=code,
                    error=None if code == 0 else
                    f"the engine exited with status {code}")
            self._pump()

    # -- control ----------------------------------------------------------

    def stop(self, run_id):
        with self._lock:
            entry = self._children.get(run_id)
            if entry:
                self.store.update(run_id, state=STOPPED,
                                  finished_at=self.clock())
                with contextlib.suppress(Exception):
                    entry[0].terminate()
                return True
            if run_id in self._waiting:
                self._waiting.remove(run_id)
                self._tokens.pop(run_id, None)
                self.store.update(run_id, state=STOPPED,
                                  finished_at=self.clock())
                return True
        return False

    def position(self, run_id):
        with self._lock:
            return (self._waiting.index(run_id) + 1
                    if run_id in self._waiting else 0)

    def active(self):
        with self._lock:
            return len(self._children), len(self._waiting)

    # -- housekeeping -----------------------------------------------------

    def cleanup(self, ttl=TTL_SECONDS):
        """Delete runs past their TTL. Never one that is queued or running:
        a sweep somebody is watching is not old, whatever its clock says."""
        now, removed = self.clock(), []
        with self._lock:
            for status in self.store.all():
                run_id, state = status.get("run_id"), status.get("state")
                if state in ACTIVE or run_id in self._children:
                    continue
                stamp = status.get("finished_at") or status.get("created_at") or now
                if now - stamp >= ttl:
                    # The run's rendered profile lives in the checkout's
                    # profiles/ package, because that is how the engine
                    # selects one. Deleting the run directory alone would
                    # leave it there for good, and a beta that ran for a
                    # month would leave a month of strangers' profiles.
                    profile_path = status.get("profile_path")
                    if profile_path:
                        with contextlib.suppress(OSError):
                            os.remove(profile_path)
                    self.store.delete(run_id)
                    removed.append(run_id)
        return removed


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

def rows_since(output_dir, since=0, limit=500):
    """Rows the engine has written so far, newest file first.

    The engine writes jobs_<stamp>.csv as it goes, which is what makes a
    live feed possible without this worker parsing the child's stdout.
    """
    files = sorted(glob.glob(os.path.join(output_dir, "**", "*.csv"),
                             recursive=True), key=os.path.getmtime)
    if not files:
        return [], 0
    rows = []
    try:
        with open(files[-1], newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, csv.Error, UnicodeDecodeError):
        return [], 0
    return rows[since:since + limit], len(rows)


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------

def accepted_tokens(env=None):
    env = os.environ if env is None else env
    raw = (env.get(TOKEN_ENV) or "").strip()
    tokens = {t.strip() for t in raw.split(",") if t.strip()}
    if not tokens:
        raise SystemExit(
            f"{TOKEN_ENV} is not set. This worker starts sweeps that spend a "
            f"visitor's money; it will not run without a bearer token.")
    return tokens


def create_app(store=None, queue=None, accepted=None, checkout=None,
               clock=time.time):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY
    store = store if store is not None else RunStore()
    checkout = checkout or CHECKOUT
    queue = queue if queue is not None else Queue(store, checkout=checkout,
                                                  clock=clock)
    accepted = accepted if accepted is not None else accepted_tokens()
    app.store, app.queue, app.checkout = store, queue, checkout

    def authorised():
        """Every endpoint, health included. Only Render calls this service,
        and an unauthenticated route is a route somebody else can map."""
        header = request.headers.get("Authorization", "")
        given = header[7:] if header.startswith("Bearer ") else ""
        return any(hmac.compare_digest(given, t) for t in accepted)

    def owned(run_id):
        """The run, if the caller owns it. A stranger gets the same answer
        as someone asking for a run that does not exist: 404, not 403,
        which would confirm the id."""
        status = store.read(run_id)
        given = request.headers.get("X-Sweep-Owner", "")
        if not given or not hmac.compare_digest(given, status.get("owner", "")):
            raise Refused(404, "no such run")
        return status

    def public(status):
        """What Render may see. No token — there is none to leak here,
        because one was never written to this file."""
        return {k: v for k, v in status.items() if k != "owner"} | {
            "queue_position": queue.position(status["run_id"])}

    @app.errorhandler(Refused)
    def refused(exc):
        return jsonify({"error": {"message": exc.message}}), exc.status

    @app.before_request
    def gate():
        if not authorised():
            return jsonify({"error": {"message": "a bearer token is required"}}), 401
        return None

    @app.get("/healthz")
    def healthz():
        running, waiting = queue.active()
        return jsonify({"status": "ok", "running": running, "queued": waiting,
                        "max_active": queue.max_active})

    @app.post("/v1/runs")
    def create_run():
        profile, prefs, free_only, token, owner = _checked(
            request.get_json(silent=True))
        make_profile = _import_make_profile(checkout)
        run_id = secrets.token_hex(RUN_ID_BYTES)
        profile_name = f"beta_{run_id}"
        run_dir = store.dir(run_id)
        output_dir = store.output_dir(run_id)
        os.makedirs(output_dir, exist_ok=True)

        source = render_profile(make_profile, profile_name, profile,
                                free_prefs(prefs, checkout) if free_only
                                else prefs, output_dir)
        # Into the checkout's profiles/ package, because that is how the
        # engine selects one. Named by run id, so two visitors cannot
        # collide and neither can reach the operator's own profiles.
        profile_path = os.path.join(checkout, "profiles", f"{profile_name}.py")
        with open(profile_path, "w", encoding="utf-8") as fh:
            fh.write(source)
        with open(os.path.join(run_dir, "profile.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(source)

        store.write(run_id, {
            "run_id": run_id, "owner": owner, "state": QUEUED,
            "created_at": clock(), "started_at": None, "finished_at": None,
            "free_only": free_only, "profile_name": profile_name,
            "profile_path": profile_path, "exit_code": None, "error": None,
        })
        queue.submit(run_id, token)
        del token
        # The id is opaque and is not, on its own, authority: every read
        # still needs this worker's bearer token AND the owner string.
        log.info("run=%s created free_only=%s", run_id, free_only)
        return jsonify({"run_id": run_id}), 201

    @app.get("/v1/runs/<run_id>")
    def run_status(run_id):
        return jsonify(public(owned(run_id)))

    @app.get("/v1/runs/<run_id>/rows")
    def run_rows(run_id):
        owned(run_id)
        try:
            since = max(0, int(request.args.get("since") or 0))
        except ValueError:
            raise Refused(400, "since must be a whole number") from None
        rows, total = rows_since(store.output_dir(run_id), since)
        return jsonify({"rows": rows, "total": total, "since": since})

    @app.post("/v1/runs/<run_id>/stop")
    def run_stop(run_id):
        owned(run_id)
        queue.stop(run_id)
        return jsonify(public(store.read(run_id)))

    return app


def janitor(queue, every=3600, ttl=TTL_SECONDS, stop=None):
    """Delete expired runs on a timer. Started by main(), not by
    create_app, so a test never grows a background thread it did not ask
    for."""
    stop = stop or threading.Event()

    def loop():
        while not stop.wait(every):
            with contextlib.suppress(Exception):
                for run_id in queue.cleanup(ttl):
                    log.info("run=%s deleted (past ttl)", run_id)

    threading.Thread(target=loop, daemon=True).start()
    return stop


def check_checkout(checkout=None):
    """Refuse to start against a checkout this cannot run a sweep from.

    A worker that boots and then 500s on the first visitor is worse than
    one that does not boot: the failure surfaces in front of somebody's
    résumé rather than in the deploy that caused it.
    """
    checkout = checkout or CHECKOUT
    # The FILE, not merely a successful import: once make_profile is in
    # sys.modules an import proves nothing about this checkout, which is
    # exactly how a bad one reached a live request the first time.
    renderer = os.path.join(checkout, "auto-apply", "make_profile.py")
    if not os.path.isfile(renderer):
        raise SystemExit(
            f"{renderer} is missing. The worker renders every run's profile "
            f"with make_profile, so the checkout has to be the whole repo.")
    profiles = os.path.join(checkout, "profiles")
    if not os.path.isdir(profiles) or not os.access(profiles, os.W_OK):
        raise SystemExit(
            f"{profiles} must exist and be writable: the engine selects a "
            f"profile by module name, so each run's profile is written "
            f"there.")
    # Last, because it has a side effect the checks above do not: it puts
    # this checkout on sys.path for the life of the process.
    try:
        _import_make_profile(checkout)
    except ImportError as exc:
        raise SystemExit(f"the checkout at {checkout} has make_profile but "
                         f"it will not import ({exc}).") from None
    return checkout


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    check_checkout()
    store = RunStore()
    queue = Queue(store)
    queue.recover()
    janitor(queue)
    return create_app(store=store, queue=queue)


if __name__ == "__main__":
    main().run(host="127.0.0.1", port=8812)
