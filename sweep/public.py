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
import threading
import time
from collections.abc import MutableMapping

from flask import (Response, redirect, render_template, request, session,
                   url_for)

PUBLIC_ENV = "SWEEP_PUBLIC_MODE"
SECRET_ENV = "SECRET_KEY"
CODE_ENV = "SWEEP_BETA_CODE"
PER_IP_ENV = "SWEEP_BETA_DAILY_PER_IP"
TOTAL_ENV = "SWEEP_BETA_DAILY_TOTAL"
PROXIES_ENV = "SWEEP_TRUSTED_PROXIES"

# How many proxies in front of us write X-Forwarded-For. Render documents
# two: "All inbound traffic to Render web services passes through
# Cloudflare's global network", and then Render's own load balancers.
#
# The count is the whole security property. ProxyFix reads the Nth entry
# from the RIGHT, and every trusted proxy appends the address it received
# from — so with N=2 we read what Cloudflare recorded, which is the real
# visitor. A visitor who sends their own X-Forwarded-For only prepends to
# the LEFT of that, where this never looks.
#
# Too low and every visitor reads as one Cloudflare edge IP and shares a
# single quota; too high and a client-supplied entry becomes the identity.
# Configurable because a different fronting setup is a different count.
DEFAULT_TRUSTED_PROXIES = 2

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
    """Complete résumé derivations per IP per day, and a ceiling for
    everyone together.

    The UNIT is one derivation, not one model call. A profile is two
    inference calls — fields, then employment — and a quota counted per
    call could let someone through the first and refuse them the second,
    leaving them with a half-read résumé and one call's worth of GPU
    spent for nothing. `metered` wraps the whole derivation, so both
    calls happen inside one reserved slot.

    Reserved, not counted afterwards: check-then-spend is a race, and
    with eight gunicorn threads it is a race that a double-click wins.
    A slot is taken BEFORE the model is asked and given back if the
    derivation fails, so a visitor is never billed for an answer they
    did not get — and two concurrent requests cannot both see the last
    slot as free.
    """

    def __init__(self, per_ip=DEFAULT_PER_IP, total=DEFAULT_TOTAL,
                 clock=time.time, window=DAY):
        self.per_ip, self.total = per_ip, total
        self._clock, self._window = clock, window
        self._lock = threading.Lock()
        self._slots = {}

    def _prune(self, now):
        for ip in list(self._slots):
            kept = [at for at in self._slots[ip] if now - at < self._window]
            if kept:
                self._slots[ip] = kept
            else:
                del self._slots[ip]

    def taken(self, ip=None):
        """Slots in use — reserved or completed. For tests and /healthz."""
        with self._lock:
            self._prune(self._clock())
            if ip is None:
                return sum(len(v) for v in self._slots.values())
            return len(self._slots.get(ip, ()))

    def reserve(self, ip):
        """Take one derivation's worth of quota, or raise BetaLimited.

        The whole decision happens under one lock: prune, count, and
        take. Nothing between the count and the take, which is exactly
        where a concurrent request would otherwise slip through.
        """
        with self._lock:
            now = self._clock()
            self._prune(now)
            if sum(len(v) for v in self._slots.values()) >= self.total:
                raise BetaLimited(
                    "Sweep's beta has read as many résumés as it can today. "
                    "Try again tomorrow.")
            if len(self._slots.get(ip, ())) >= self.per_ip:
                raise BetaLimited(
                    f"You have read {self.per_ip} résumés today, which is "
                    f"the beta limit. Try again tomorrow.")
            self._slots.setdefault(ip, []).append(now)
            return (ip, now)

    def release(self, ticket):
        """Give a reserved slot back, for a derivation that failed."""
        ip, at = ticket
        with self._lock:
            held = self._slots.get(ip)
            if held and at in held:
                held.remove(at)
                if not held:
                    del self._slots[ip]


def metered(derive, limit):
    """One complete derivation, behind one slot of the daily limit.

    Wrapping the derivation rather than the route: POST /derive is the
    route that means to spend, but POST /review reaches the same function,
    and a limit that a second route walks around is not a limit. Wrapping
    it here also fixes the unit — everything inside, both model calls
    included, is one beta usage.
    """
    def guarded(resume_text, prefs):
        ticket = limit.reserve(client_ip())
        try:
            return derive(resume_text, prefs)
        except Exception:
            # A failed derivation gives the slot back: the visitor has no
            # profile to show for it, and ending someone's beta on our
            # bug is the wrong trade. The GPU time it spent is bounded by
            # the global ceiling and by Modal's own budget.
            limit.release(ticket)
            raise
    return guarded


def client_ip():
    """The visitor, as the trusted proxies reported them.

    ProxyFix has already rewritten remote_addr by the time this runs, and
    it only ever reads entries written by the trusted hops — see
    DEFAULT_TRUSTED_PROXIES. Nothing here reads a raw header.
    """
    return request.remote_addr or "unknown"


def trusted_proxies(env=None):
    env = os.environ if env is None else env
    raw = (env.get(PROXIES_ENV) or "").strip()
    if not raw:
        return DEFAULT_TRUSTED_PROXIES
    try:
        return max(1, int(raw))
    except ValueError:
        raise NotConfigured(f"{PROXIES_ENV}={raw!r} is not a whole number") from None


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
    # Without this the app sees Render's proxy instead of the visitor and
    # http:// instead of https://.
    #
    # x_for counts the hops that WRITE the header (Cloudflare, then
    # Render's load balancer): read the entry Cloudflare wrote, not the
    # one a client can prepend. x_proto is 1 because the scheme header
    # carries one value written by the nearest proxy.
    #
    # x_host is 0 deliberately: X-Forwarded-Host would let a client
    # rewrite request.host, and request.host is what the cross-site check
    # below compares an Origin against — trusting it would hand an
    # attacker the means to match it. Render passes the real Host through.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxies(env),
                            x_proto=1, x_host=0, x_port=0, x_prefix=0)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        # Strict, with the CSRF check below as the belt to its braces: the
        # public flow is three same-origin POSTs and never a cross-site
        # landing, so nothing legitimate is lost.
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=SESSION_TTL,
    )

    # Which market decides skill weights here, read ONCE at start-up: on
    # a fresh clone or Render there is no output/, so the shipped table
    # answers, and a health check should be able to say so. Reading it per
    # request would rescan every CSV a real corpus holds.
    import corpus_signal
    app.config["MARKET_SIGNAL_SOURCE"] = corpus_signal.market_signal(
        app.config.get("OUTPUT_DIR"))[1]

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
                "sessions": len(store),
                "market_signal_source": app.config["MARKET_SIGNAL_SOURCE"]}

    @app.errorhandler(BetaLimited)
    def beta_limited(exc):
        return render_template("beta.html", limited=str(exc)), 429

    return app
