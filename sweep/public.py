"""Public beta mode: the profile half of Sweep, safe to put on the internet.

Sweep is a single-operator console. `app.state` is ONE dict for the whole
process, `.env` holds the operator's Apify token, `POST /run` launches a
subprocess that spends real money, and profiles are Python files written
into the repo. Put that on a public URL unchanged and any visitor sees the
last résumé uploaded, spends the operator's balance, or deletes their key.

So public mode does not "harden" the whole app — it exposes a SUBSET:
upload a résumé, read it with the model, review the weights, take the
profile away. Everything else is refused by an allowlist, which fails
closed: a route added later is invisible to the public app until someone
puts it in PUBLIC_ENDPOINTS deliberately.

The three things that make the subset safe:

  isolation  `app.state` becomes a per-session view (SessionState), so the
             50 call sites in app.py keep working and no two visitors can
             see each other's résumé.
  no disk    the PDF is parsed from a temp file that is deleted in the same
             request, and the profile is handed back as a download instead
             of being written into profiles/.
  a budget   every extraction is two GPU calls on the operator's Modal
             account, so a beta code gates the door and a per-IP daily
             limit gates the spend.
"""

import hmac
import os
import secrets
import time
from collections.abc import MutableMapping

from flask import (Response, redirect, render_template, request, session,
                   url_for)

PUBLIC_ENV = "SWEEP_PUBLIC_MODE"
SECRET_ENV = "SECRET_KEY"
CODE_ENV = "SWEEP_BETA_CODE"
PER_IP_ENV = "SWEEP_BETA_DAILY_PER_IP"
TOTAL_ENV = "SWEEP_BETA_DAILY_TOTAL"

# What the public app is allowed to reach. Endpoint names, not paths, so a
# renamed URL cannot quietly widen this. Everything else — /key, /run,
# /stop, /results, /export, /rescore, the SSE feed — answers 404.
PUBLIC_ENDPOINTS = frozenset({
    "upload", "resume", "review", "derive_post", "review_post",
    "profile_done", "profile_download", "beta_gate", "healthz", "static",
})

# The public flow's own tracker. The console's seven steps end in a paid
# sweep; four of them 404 here, and a tracker that offers them is a tracker
# that lies. step_states() knows "profile_done" (sweep/logic.py).
PUBLIC_STEPS = [("upload", "Upload"), ("review", "Review"),
                ("profile_done", "Profile")]

# A session is a browser that uploaded a résumé. Two hours is longer than
# anyone spends on a three-screen flow and short enough that a shared
# laptop does not hand the next person a stranger's parse.
SESSION_TTL = 2 * 60 * 60
# Bounded because this lives in memory: a flood of cookie-less visitors
# must not be able to grow it without limit. Oldest goes first.
MAX_SESSIONS = 500

# One résumé is two qwen3:8b calls on a T4. Conservative on purpose: the
# operator's Modal budget is $25 and a beta is not a product launch.
DEFAULT_PER_IP = 3
DEFAULT_TOTAL = 60
DAY = 24 * 60 * 60


class BetaLimited(RuntimeError):
    """The visitor may not spend another extraction today."""


class NotConfigured(RuntimeError):
    """Public mode is missing a setting it refuses to start without."""


def enabled(env=None):
    """Public mode is opt-in and explicit. Anything but a clear yes is no,
    because the failure direction matters: a local console that thinks it
    is public loses routes, a public app that thinks it is local exposes
    the operator's money."""
    env = os.environ if env is None else env
    return (env.get(PUBLIC_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


class SessionStore:
    """Per-session state, in memory, bounded and expiring.

    In memory because the public flow needs nothing to survive a restart:
    a visitor who loses their parse uploads the PDF again. That is also
    why Render's ephemeral disk is not a problem for this subset — and why
    the public app must run ONE worker, since this is not shared between
    processes.
    """

    def __init__(self, ttl=SESSION_TTL, cap=MAX_SESSIONS, clock=time.monotonic):
        self._rooms = {}
        self._ttl, self._cap, self._clock = ttl, cap, clock

    def _sid(self):
        sid = session.get("sid")
        if not sid:
            sid = secrets.token_urlsafe(18)
            session["sid"] = sid
        return sid

    def room(self):
        """This browser's own dict, created on first touch."""
        now = self._clock()
        sid = self._sid()
        room = self._rooms.get(sid)
        if room is None:
            room = self._rooms[sid] = {"seen": now, "data": {}}
        room["seen"] = now
        # After the insert, never before: evicting first leaves room for one
        # more and the cap is really cap + 1. This room is the newest, so it
        # is never the one dropped.
        self._evict(now)
        return room["data"]

    def _evict(self, now):
        for sid in [s for s, r in self._rooms.items()
                    if now - r["seen"] > self._ttl]:
            del self._rooms[sid]
        while len(self._rooms) > self._cap:
            oldest = min(self._rooms, key=lambda s: self._rooms[s]["seen"])
            del self._rooms[oldest]

    def __len__(self):
        return len(self._rooms)


class SessionState(MutableMapping):
    """`app.state`, per browser.

    A mapping rather than a rewrite of app.py: the routes do
    state.get(...), state[...] = ..., setdefault and pop, and every one of
    those lands in the calling session's own dict. Nothing in app.py has
    to know which mode it is running in.
    """

    def __init__(self, store):
        self._store = store

    def __getitem__(self, key):
        return self._store.room()[key]

    def __setitem__(self, key, value):
        self._store.room()[key] = value

    def __delitem__(self, key):
        del self._store.room()[key]

    def __iter__(self):
        return iter(self._store.room())

    def __len__(self):
        return len(self._store.room())


class DailyLimit:
    """Extractions per IP per day, and a ceiling for everyone together.

    Both are needed: the per-IP limit stops one visitor burning the
    budget, the total stops a hundred visitors doing it one call each.
    """

    def __init__(self, per_ip=DEFAULT_PER_IP, total=DEFAULT_TOTAL,
                 clock=time.time, window=DAY):
        self.per_ip, self.total = per_ip, total
        self._clock, self._window = clock, window
        self._spent = {}

    def _prune(self, now):
        for ip in list(self._spent):
            kept = [t for t in self._spent[ip] if now - t < self._window]
            if kept:
                self._spent[ip] = kept
            else:
                del self._spent[ip]

    def check(self, ip):
        """Raises BetaLimited if this extraction may not happen."""
        now = self._clock()
        self._prune(now)
        if sum(len(v) for v in self._spent.values()) >= self.total:
            raise BetaLimited(
                "Sweep's beta has read as many résumés as it can today. "
                "Try again tomorrow.")
        if len(self._spent.get(ip, ())) >= self.per_ip:
            raise BetaLimited(
                f"You have read {self.per_ip} résumés today, which is the "
                f"beta limit. Try again tomorrow.")

    def spend(self, ip):
        self._spent.setdefault(ip, []).append(self._clock())


def metered(derive, limit):
    """The model call, behind the daily limit.

    Wrapping the call itself rather than the route: POST /derive is the
    route that means to spend, but POST /review reaches the same function,
    and a limit that a second route walks around is not a limit.
    """
    def guarded(resume_text, prefs):
        ip = client_ip()
        limit.check(ip)
        answer = derive(resume_text, prefs)
        # Counted only when a model actually answered. A failed extraction
        # spends GPU time but gives the visitor nothing, and charging them
        # for it would end their beta on our bug.
        limit.spend(ip)
        return answer
    return guarded


def client_ip():
    """The visitor, as far as the proxy will say. ProxyFix has already
    rewritten remote_addr from X-Forwarded-For by the time this runs."""
    return request.remote_addr or "unknown"


def looks_like_pdf(path):
    """A PDF says so in its first bytes. Cheap, and it keeps the parser
    from being handed whatever a stranger felt like uploading."""
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def secret_key(env=None):
    env = os.environ if env is None else env
    key = (env.get(SECRET_ENV) or "").strip()
    if not key:
        raise NotConfigured(
            f"{SECRET_ENV} is not set. Public mode signs the session cookie "
            f"that keeps one visitor's résumé away from another's, so it "
            f"will not start without a stable one.")
    return key


def beta_code(env=None):
    env = os.environ if env is None else env
    code = (env.get(CODE_ENV) or "").strip()
    if not code:
        raise NotConfigured(
            f"{CODE_ENV} is not set. Every résumé read costs GPU money on "
            f"the operator's account, so public mode will not run without "
            f"a code on the door.")
    return code


def limit_from_env(env=None):
    """The daily budget, from the environment. Render sets both; the
    defaults are deliberately small."""
    env = os.environ if env is None else env

    def read(name, fallback):
        raw = (env.get(name) or "").strip()
        if not raw:
            return fallback
        try:
            return max(0, int(raw))
        except ValueError:
            raise NotConfigured(f"{name}={raw!r} is not a whole number") from None
    return DailyLimit(read(PER_IP_ENV, DEFAULT_PER_IP),
                      read(TOTAL_ENV, DEFAULT_TOTAL))


def harden(app, env=None, store=None, limit=None):
    """Turn a local console into the public profile app.

    Called from create_app only when `enabled()`. Everything here either
    narrows what is reachable or isolates what is stored; nothing changes
    how a résumé is read, which is the whole point of the Modal work this
    sits on top of.
    """
    env = os.environ if env is None else env
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.secret_key = secret_key(env)
    code = beta_code(env)
    app.config["PUBLIC_MODE"] = True
    # Render terminates TLS and forwards one hop. Without this the app
    # sees http:// and the proxy's own address instead of the visitor's,
    # which would make the per-IP limit count everyone as one person.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        # Strict, with the CSRF check below as the belt to its braces: the
        # public flow is three same-origin POSTs and never a cross-site
        # landing, so nothing legitimate is lost.
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=SESSION_TTL,
    )

    store = store if store is not None else SessionStore()
    app.session_store = store
    app.state = SessionState(store)
    # create_app already built one and wrapped `derive` in it — replacing
    # it here would meter a limit nothing consults.
    if limit is not None:
        app.beta_limit = limit
    elif not hasattr(app, "beta_limit"):
        app.beta_limit = limit_from_env(env)

    # A profile is handed back, never written into profiles/: that
    # directory is a shared namespace on an ephemeral disk, so one
    # visitor's name could replace another's and neither would survive a
    # restart.
    def keep_profile(name, source):
        app.state["profile_source"] = source
        return None

    app.write_profile = keep_profile
    app.config["AFTER_REVIEW_ENDPOINT"] = "profile_done"
    app.config["STEPS"] = PUBLIC_STEPS

    @app.before_request
    def gate():
        # 1. Only the public subset exists. Unknown endpoint (404 already)
        #    falls through to Flask's own handler.
        if request.endpoint is not None and request.endpoint not in PUBLIC_ENDPOINTS:
            return Response("Not available in the public beta.", status=404,
                            mimetype="text/plain")
        # 2. Same-origin for anything that changes state. Origin is sent by
        #    every browser on a cross-site POST, and a forged one cannot be
        #    set by a page — which is exactly the attack this stops.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if origin and origin.split("//")[-1] != request.host:
                return Response("Cross-site request refused.", status=403,
                                mimetype="text/plain")
        # 3. The door.
        if request.endpoint in {"beta_gate", "healthz", "static"}:
            return None
        if not session.get("beta_ok"):
            return redirect(url_for("beta_gate"))
        return None

    @app.get("/beta")
    @app.post("/beta")
    def beta_gate():
        if request.method == "POST":
            given = (request.form.get("code") or "").strip()
            # Constant time: the code is the only thing between a stranger
            # and the operator's GPU budget.
            if hmac.compare_digest(given, code):
                session["beta_ok"] = True
                session.permanent = True
                return redirect(url_for("upload"))
            return render_template("beta.html",
                                   error="That code is not right."), 403
        if session.get("beta_ok"):
            return redirect(url_for("upload"))
        return render_template("beta.html")

    @app.get("/healthz")
    def healthz():
        """Render's health check. Cheap on purpose: it touches no model,
        no session and no disk, so a health probe can never wake the GPU
        or cost anything."""
        return {"status": "ok", "mode": "public-beta",
                "sessions": len(store)}

    @app.errorhandler(BetaLimited)
    def beta_limited(exc):
        return render_template("beta.html", limited=str(exc)), 429

    return app
