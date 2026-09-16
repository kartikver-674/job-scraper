"""Public beta mode: what a stranger on the internet can and cannot reach.

The local app is a single-operator console — one shared `app.state`, the
operator's Apify key in `.env`, a route that spends money. These tests hold
the line that public mode draws around it: a visitor gets the résumé →
profile flow, their own session, no disk, and a daily budget.

Every test drives the real Flask app through a test client. `derive` is
injected everywhere, because nothing here may reach a model: the point of
public mode is what it exposes, not what qwen3 answers.
"""

import io
import os
import re
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from sweep import app as app_module
from sweep import public
from sweep.tests.test_app import DERIVED

PDF = b"%PDF-1.7 fake"
CODE = "let-me-in"
PUBLIC_ENV = {public.PUBLIC_ENV: "1", public.SECRET_ENV: "test-secret",
              public.CODE_ENV: CODE}


def public_app(env_extra=None, **kw):
    """The app as Render would run it, with the model and parser injected."""
    kw.setdefault("extract", lambda path: "Ada Okonkwo, React Native dev")
    # Echoes what it was handed, so a test can prove WHOSE parse a screen
    # shows rather than trusting that two identical fixtures differ.
    kw.setdefault("derive",
                  lambda text, prefs: {**DERIVED, "field_summary": text})
    with mock.patch.dict(os.environ, {**PUBLIC_ENV, **(env_extra or {})},
                         clear=False):
        app = app_module.create_app(**kw)
    app.config["TESTING"] = True
    return app


def unlocked(app):
    """A client that has already answered the beta door."""
    client = app.test_client()
    client.post("/beta", data={"code": CODE})
    return client


def upload(client, name="cv.pdf", body=PDF):
    return client.post("/resume", data={"resume": (io.BytesIO(body), name)},
                       content_type="multipart/form-data")


class TestTheDoor(unittest.TestCase):
    """Every résumé read spends GPU money, so the URL is not the door."""

    def test_without_the_code_every_screen_redirects_to_the_gate(self):
        client = public_app().test_client()
        for path in ("/", "/review", "/profile"):
            with self.subTest(path):
                r = client.get(path)
                self.assertEqual(r.status_code, 302)
                self.assertIn("/beta", r.headers["Location"])

    def test_a_wrong_code_is_refused_and_does_not_open_anything(self):
        client = public_app().test_client()
        r = client.post("/beta", data={"code": "guess"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(client.get("/").status_code, 302)

    def test_the_right_code_opens_the_upload_screen(self):
        client = unlocked(public_app())
        r = client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("résumé", r.get_data(as_text=True))

    def test_public_mode_refuses_to_start_without_a_secret_key(self):
        env = {**PUBLIC_ENV, public.SECRET_ENV: ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(public.NotConfigured) as caught:
                app_module.create_app(derive=lambda t, p: DERIVED)
        self.assertIn(public.SECRET_ENV, str(caught.exception))

    def test_public_mode_refuses_to_start_without_a_beta_code(self):
        env = {**PUBLIC_ENV, public.CODE_ENV: ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(public.NotConfigured) as caught:
                app_module.create_app(derive=lambda t, p: DERIVED)
        self.assertIn(public.CODE_ENV, str(caught.exception))

    def test_the_health_check_needs_no_code_and_touches_no_model(self):
        called = []
        app = public_app(derive=lambda t, p: called.append(1))
        r = app.test_client().get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "ok")
        self.assertEqual(called, [])


class TestWhatIsReachable(unittest.TestCase):
    """The allowlist, which fails closed."""

    def test_every_operator_route_is_gone(self):
        """The sweep screens are public now — they run on the Oracle
        worker. What stays shut is everything that touches the OPERATOR's
        keys, their disk, or their earlier sweeps."""
        client = unlocked(public_app())
        for path in ("/events",):
            with self.subTest(path):
                self.assertEqual(client.get(path).status_code, 404)
        # POST /key is NOT here: in public mode it takes the VISITOR's
        # own key, validates it and hands it to the worker.
        for path in ("/key/remove", "/second-key", "/merge",
                     "/applied", "/rescore"):
            with self.subTest("POST " + path):
                self.assertEqual(client.post(path).status_code, 404)

    def test_the_sweep_screens_are_the_consoles_own(self):
        client = unlocked(public_app())
        for path in ("/key", "/configure", "/confirm", "/running",
                     "/results"):
            with self.subTest(path):
                # Reachable: their own guards decide whether this visitor
                # has got far enough, but they are not refused outright.
                self.assertNotEqual(client.get(path).status_code, 404)

    def test_the_profile_flow_itself_is_reachable(self):
        client = unlocked(public_app())
        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(upload(client).status_code, 302)
        self.assertEqual(client.post("/derive").status_code, 302)
        self.assertEqual(client.get("/review").status_code, 200)

    def test_the_allowlist_holds_the_dangerous_endpoints_out(self):
        """The allowlist is the whole guard, so this is what keeps it
        honest: it names endpoints, and the app has plenty it omits."""
        app = public_app()
        registered = {r.endpoint for r in app.url_map.iter_rules()}
        self.assertTrue(public.PUBLIC_ENDPOINTS <= registered,
                        public.PUBLIC_ENDPOINTS - registered)
        # The operator's own: their keys, their disk, their earlier
        # sweeps, and the event stream public mode replaces with polling.
        for dangerous in public.OPERATOR_ONLY:
            self.assertIn(dangerous, registered, dangerous)
            self.assertNotIn(dangerous, public.PUBLIC_ENDPOINTS, dangerous)

    def test_the_tracker_shows_four_stages_not_seven_routes(self):
        """A step chip linking to a route that 404s is a lie the header
        tells on every screen — and so, more quietly, is a seven-step
        tracker. The routes are a correct engineering decomposition;
        "Free or paid", "Configure", "Confirm" and "Running" are four
        announcements of one thing a job seeker calls searching."""
        body = unlocked(public_app()).get("/").get_data(as_text=True)
        self.assertIn("1 of 4", body)
        for stage in ("Résumé", "Profile", "Search", "Jobs"):
            self.assertIn(stage, body)
        # The route names the visitor should never be shown as stages.
        for internal in ("Free or paid", "Configure", "Confirm"):
            self.assertNotIn(f'step-label">{internal}', body)

    def test_the_tracker_still_links_only_where_it_can_go(self):
        """Every stage the header offers has to open, in every state — the
        property that made it worth keeping. The Search stage links to
        /key, whose own guard is laxer than the stage's: it renders with
        no profile and then both of its buttons redirect to /review."""
        app = public_app()
        client = unlocked(app)
        for path in ("/", "/review", "/key", "/results"):
            body = client.get(path, follow_redirects=True).get_data(as_text=True)
            for href in re.findall(r'<a href="([^"]+)"\s*\n?\s*>', body):
                if href.startswith("/"):
                    self.assertEqual(
                        client.get(href).status_code, 200,
                        f"from {path}, the tracker offered {href} but it "
                        f"did not render")


class TestSessionIsolation(unittest.TestCase):
    """The blocker public mode exists to fix: one app.state for everyone."""

    def test_two_visitors_never_see_each_others_resume(self):
        texts = []
        app = public_app(extract=lambda path: texts.pop(0))
        ada, ben = unlocked(app), unlocked(app)

        texts.append("ADA-ONLY Postgres")
        upload(ada)
        ada.post("/derive")
        texts.append("BEN-ONLY Kotlin")
        upload(ben)
        ben.post("/derive")

        ada_page = ada.get("/review").get_data(as_text=True)
        ben_page = ben.get("/review").get_data(as_text=True)
        self.assertIn("ADA-ONLY", ada_page)
        self.assertNotIn("BEN-ONLY", ada_page)
        self.assertIn("BEN-ONLY", ben_page)
        self.assertNotIn("ADA-ONLY", ben_page)

    def test_a_visitor_who_uploaded_nothing_gets_nobody_elses_parse(self):
        app = public_app()
        ada, ben = unlocked(app), unlocked(app)
        upload(ada)
        self.assertEqual(ada.post("/derive").status_code, 302)
        self.assertEqual(ada.get("/review").status_code, 200)
        # Ben uploaded nothing: the review screen must send him to the front
        # door, not render Ada's résumé.
        self.assertEqual(ben.get("/review").status_code, 302)
        self.assertEqual(ben.get("/profile").status_code, 302)

    def test_a_second_resume_does_not_inherit_the_first_ones_parse(self):
        texts = ["FIRST-résumé", "SECOND-résumé"]
        app = public_app(extract=lambda path: texts.pop(0))
        client = unlocked(app)
        upload(client)
        client.post("/derive")
        self.assertIn("FIRST", client.get("/review").get_data(as_text=True))
        upload(client)
        client.post("/derive")
        page = client.get("/review").get_data(as_text=True)
        self.assertIn("SECOND", page)
        self.assertNotIn("FIRST", page)

    def test_the_resume_never_travels_in_the_cookie(self):
        client = unlocked(public_app())
        upload(client)
        crumbs = " ".join(
            v for k, v in client.post("/derive").headers
            if k.lower() == "set-cookie")
        crumbs += " " + " ".join(
            v for k, v in client.get("/review").headers
            if k.lower() == "set-cookie")
        for secret in ("Okonkwo", "react native", "Postgres"):
            self.assertNotIn(secret, crumbs)


class TestNoDisk(unittest.TestCase):
    """Render's filesystem is ephemeral and shared; a stranger's PDF is not
    something to leave on it."""

    def test_the_uploaded_pdf_is_deleted_in_the_same_request(self):
        saw = {}

        def extract(path):
            saw["path"] = path
            saw["existed"] = os.path.exists(path)
            return "Ada Okonkwo"

        client = unlocked(public_app(extract=extract))
        upload(client)
        self.assertTrue(saw["existed"], "the parser needs the file")
        self.assertFalse(os.path.exists(saw["path"]),
                         "the résumé outlived its request")

    def test_nothing_is_written_to_the_real_resume_directory(self):
        before = (set(os.listdir(app_module.RESUME_DIR))
                  if os.path.isdir(app_module.RESUME_DIR) else set())
        client = unlocked(public_app())
        upload(client)
        after = (set(os.listdir(app_module.RESUME_DIR))
                 if os.path.isdir(app_module.RESUME_DIR) else set())
        self.assertEqual(before, after)

    def test_a_file_that_is_not_a_pdf_is_refused(self):
        client = unlocked(public_app())
        r = upload(client, "cv.pdf", b"PK\x03\x04 this is a zip")
        self.assertEqual(r.status_code, 400)
        self.assertIn("not a PDF", r.get_data(as_text=True))

    def test_the_profile_is_handed_over_not_written_into_profiles(self):
        app = public_app()
        client = unlocked(app)
        upload(client)
        client.post("/derive")
        r = client.post("/review", data={"name": "ada_beta"})
        self.assertEqual(r.status_code, 302)
        # "Looks right" leads to the console's own source-choice screen;
        # the profile itself is still a download away, not a file on this
        # disk.
        self.assertIn("/key", r.headers["Location"])
        self.assertFalse(os.path.exists(os.path.join(
            app_module.REPO_ROOT, "profiles", "ada_beta.py")))
        body = client.get("/profile.py").get_data(as_text=True)
        self.assertIn("SEARCH", body)


class TestTheBudget(unittest.TestCase):
    """Two GPU calls per résumé, on the operator's Modal account."""

    def test_a_visitor_is_cut_off_after_the_daily_limit(self):
        texts = ["one", "two", "three"]
        app = public_app(env_extra={public.PER_IP_ENV: "2"},
                         extract=lambda path: texts.pop(0))
        client = unlocked(app)
        for _ in range(2):
            upload(client)
            self.assertEqual(client.post("/derive").status_code, 302)
        upload(client)
        refused = client.post("/derive")
        self.assertEqual(refused.status_code, 429)
        self.assertIn("beta limit", refused.get_data(as_text=True))

    def test_the_limit_counts_per_ip_and_only_successful_reads(self):
        clock = [1000.0]
        limit = public.DailyLimit(per_ip=1, total=99, clock=lambda: clock[0])
        boom = []

        def flaky(text, prefs):
            if boom:
                return dict(DERIVED)
            boom.append(1)
            raise RuntimeError("the model fell over")

        guarded = public.metered(flaky, limit)
        with public_app().test_request_context(environ_base={
                "REMOTE_ADDR": "10.0.0.1"}):
            with self.assertRaises(RuntimeError):
                guarded("text", {})
            # The failure spent no allowance, so the retry is allowed.
            self.assertEqual(guarded("text", {}), dict(DERIVED))
            with self.assertRaises(public.BetaLimited):
                guarded("text", {})
        # A different visitor has their own allowance.
        with public_app().test_request_context(environ_base={
                "REMOTE_ADDR": "10.0.0.2"}):
            self.assertEqual(guarded("text", {}), dict(DERIVED))

    def test_a_global_ceiling_stops_a_crowd_of_one_call_each(self):
        limit = public.DailyLimit(per_ip=5, total=2)
        guarded = public.metered(lambda t, p: dict(DERIVED), limit)
        app = public_app()
        for ip in ("10.0.0.1", "10.0.0.2"):
            with app.test_request_context(environ_base={"REMOTE_ADDR": ip}):
                guarded("text", {})
        with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.3"}):
            with self.assertRaises(public.BetaLimited):
                guarded("text", {})

    def test_yesterdays_reads_do_not_count_against_today(self):
        clock = [1000.0]
        limit = public.DailyLimit(per_ip=1, total=9, clock=lambda: clock[0])
        guarded = public.metered(lambda t, p: dict(DERIVED), limit)
        app = public_app()
        with app.test_request_context(environ_base={"REMOTE_ADDR": "10.0.0.1"}):
            guarded("text", {})
            with self.assertRaises(public.BetaLimited):
                guarded("text", {})
            clock[0] += public.DAY + 1
            self.assertEqual(guarded("text", {}), dict(DERIVED))

    def test_the_limited_page_is_a_429_a_person_can_read(self):
        app = public_app()
        app.beta_limit.per_ip = 0
        client = unlocked(app)
        upload(client)
        r = client.post("/derive")
        self.assertEqual(r.status_code, 429)
        self.assertIn("beta limit", r.get_data(as_text=True))


class TestTheQuotaUnit(unittest.TestCase):
    """One complete derivation is one beta usage — not one model call.

    A profile is two inference calls (fields, then employment). Counting
    per call could let someone through the first and refuse the second,
    which spends GPU and hands back nothing.
    """

    def _ctx(self, app, ip="203.0.113.7"):
        return app.test_request_context(environ_base={"REMOTE_ADDR": ip})

    def test_a_two_call_derivation_costs_exactly_one_slot(self):
        calls = []
        limit = public.DailyLimit(per_ip=3, total=99)

        def two_call_derive(text, prefs):
            # What make_profile.generate does underneath: fields, then
            # employment, inside ONE derivation.
            calls.append("fields")
            calls.append("employment")
            return dict(DERIVED)

        guarded = public.metered(two_call_derive, limit)
        with self._ctx(public_app()):
            guarded("text", {})
        self.assertEqual(calls, ["fields", "employment"])
        self.assertEqual(limit.taken("203.0.113.7"), 1,
                         "two model calls must not cost two beta usages")

    def test_nobody_is_refused_halfway_through_their_own_profile(self):
        """The UX rule: the second model call of a derivation already
        holds its slot, so it can never be the call that is refused."""
        limit = public.DailyLimit(per_ip=1, total=99)
        refused = []

        def derive_that_checks_again(text, prefs):
            # Anything reaching the limiter mid-derivation must not be
            # able to take this visitor's own slot away.
            try:
                limit.reserve(public.client_ip())
                refused.append("a second slot was available")
            except public.BetaLimited:
                refused.append("held")
            return dict(DERIVED)

        guarded = public.metered(derive_that_checks_again, limit)
        with self._ctx(public_app()):
            self.assertEqual(guarded("text", {}), dict(DERIVED))
        self.assertEqual(refused, ["held"])

    def test_the_exact_per_ip_boundary(self):
        limit = public.DailyLimit(per_ip=3, total=99)
        guarded = public.metered(lambda t, p: dict(DERIVED), limit)
        with self._ctx(public_app()):
            for _ in range(3):
                guarded("text", {})
            self.assertEqual(limit.taken("203.0.113.7"), 3)
            with self.assertRaises(public.BetaLimited):
                guarded("text", {})
            self.assertEqual(limit.taken("203.0.113.7"), 3,
                             "a refusal must not consume a slot")

    def test_the_exact_global_boundary(self):
        limit = public.DailyLimit(per_ip=99, total=2)
        guarded = public.metered(lambda t, p: dict(DERIVED), limit)
        app = public_app()
        for ip in ("203.0.113.1", "203.0.113.2"):
            with self._ctx(app, ip):
                guarded("text", {})
        self.assertEqual(limit.taken(), 2)
        with self._ctx(app, "203.0.113.3"):
            with self.assertRaises(public.BetaLimited):
                guarded("text", {})
        self.assertEqual(limit.taken(), 2)

    def test_a_failed_derivation_gives_its_slot_back(self):
        limit = public.DailyLimit(per_ip=1, total=99)
        guarded = public.metered(
            lambda t, p: (_ for _ in ()).throw(RuntimeError("model fell over")),
            limit)
        with self._ctx(public_app()):
            with self.assertRaises(RuntimeError):
                guarded("text", {})
            self.assertEqual(limit.taken("203.0.113.7"), 0)

    def test_the_slot_is_held_while_the_derivation_is_still_running(self):
        """Reserved up front, not counted afterwards — otherwise a second
        request sees the last slot as free while the first is mid-flight."""
        limit = public.DailyLimit(per_ip=1, total=99)
        started, release = threading.Event(), threading.Event()
        second = []

        def slow(text, prefs):
            started.set()
            release.wait(5)
            return dict(DERIVED)

        guarded = public.metered(slow, limit)
        app = public_app()

        def run():
            with self._ctx(app):
                guarded("text", {})

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(started.wait(5))
        with self._ctx(app):
            try:
                guarded("text", {})
                second.append("allowed")
            except public.BetaLimited:
                second.append("refused")
        release.set()
        worker.join(5)
        self.assertEqual(second, ["refused"])

    def test_concurrent_requests_cannot_both_take_the_last_slot(self):
        """Eight gunicorn threads, one slot left: exactly one wins."""
        limit = public.DailyLimit(per_ip=1, total=99)
        begin = threading.Event()
        outcomes = []
        lock = threading.Lock()
        app = public_app()

        def racer():
            begin.wait(5)
            with self._ctx(app):
                try:
                    public.metered(lambda t, p: dict(DERIVED), limit)("t", {})
                    result = "allowed"
                except public.BetaLimited:
                    result = "refused"
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=racer) for _ in range(8)]
        for t in threads:
            t.start()
        begin.set()
        for t in threads:
            t.join(5)
        self.assertEqual(outcomes.count("allowed"), 1, outcomes)
        self.assertEqual(outcomes.count("refused"), 7, outcomes)
        self.assertEqual(limit.taken("203.0.113.7"), 1)


class TestBehindRenderProxies(unittest.TestCase):
    """Render fronts every service with Cloudflare AND its own load
    balancer, so the app never sees the visitor's own socket. Each trusted
    hop APPENDS the address it received from, so the visitor is the second
    entry from the right — and anything a client prepends stays to the
    left of it, where nothing looks.

    Every test here goes through the test CLIENT, not a request context:
    ProxyFix is WSGI middleware, and a request context built by hand never
    runs it. A test that skipped the middleware would pass while the real
    thing read the wrong address.
    """

    def app_and_log(self, **env_extra):
        """An app whose derive records who the stack thought was calling.

        The quota comes from the environment, never assigned afterwards:
        create_app wraps `derive` in the limit it builds at start-up, so a
        limit swapped in later is one nothing consults.
        """
        seen = []

        def derive(text, prefs):
            from flask import request as req
            seen.append({"ip": public.client_ip(), "secure": req.is_secure,
                         "host": req.host, "scheme": req.scheme})
            return dict(DERIVED)

        app = public_app(env_extra=env_extra or None, derive=derive)
        return app, seen

    def visit(self, app, chain=None, client=None, extra=None):
        """One derivation, through the whole stack, as a forwarded visitor."""
        client = client or unlocked(app)
        headers = dict(extra or {})
        if chain is not None:
            headers["X-Forwarded-For"] = chain
        upload(client, name="cv.pdf")
        return client.post("/derive", headers=headers)

    def test_the_visitor_is_the_hop_cloudflare_recorded(self):
        app, seen = self.app_and_log()
        # client -> Cloudflare (appends the client) -> Render LB (appends CF)
        self.visit(app, "203.0.113.7, 172.16.0.1")
        self.assertEqual(seen[-1]["ip"], "203.0.113.7")

    def test_a_client_cannot_prepend_a_fake_address_and_become_it(self):
        app, seen = self.app_and_log()
        self.visit(app, "9.9.9.9, 203.0.113.7, 172.16.0.1")
        self.assertEqual(seen[-1]["ip"], "203.0.113.7")
        self.assertNotEqual(seen[-1]["ip"], "9.9.9.9")

    def test_two_forwarded_visitors_get_separate_quotas(self):
        app, _seen = self.app_and_log(**{public.PER_IP_ENV: "1"})
        limit = app.beta_limit
        ada, ben = unlocked(app), unlocked(app)

        self.assertEqual(
            self.visit(app, "203.0.113.7, 172.16.0.1", ada).status_code, 302)
        # A different visitor behind the same Cloudflare edge: own quota.
        self.assertEqual(
            self.visit(app, "198.51.100.4, 172.16.0.1", ben).status_code, 302)
        self.assertEqual(limit.taken("203.0.113.7"), 1)
        self.assertEqual(limit.taken("198.51.100.4"), 1)
        # ... and each is now at their own limit.
        self.assertEqual(
            self.visit(app, "203.0.113.7, 172.16.0.1", ada).status_code, 429)

    def test_a_spoofed_chain_cannot_buy_a_fresh_quota(self):
        app, _seen = self.app_and_log(**{public.PER_IP_ENV: "1"})
        limit = app.beta_limit
        client = unlocked(app)
        self.assertEqual(
            self.visit(app, "203.0.113.7, 172.16.0.1", client).status_code, 302)
        # The same visitor, now dressing the chain up as someone else.
        for disguise in ("9.9.9.9", "1.1.1.1, 8.8.8.8"):
            refused = self.visit(
                app, f"{disguise}, 203.0.113.7, 172.16.0.1", client)
            self.assertEqual(refused.status_code, 429, disguise)
        self.assertEqual(limit.taken("203.0.113.7"), 1)
        self.assertEqual(limit.taken(), 1, "no spoof bought a second slot")

    def test_the_scheme_comes_from_the_proxy_so_https_is_seen_as_https(self):
        app, seen = self.app_and_log()
        self.visit(app, "203.0.113.7, 172.16.0.1",
                   extra={"X-Forwarded-Proto": "https"})
        self.assertTrue(seen[-1]["secure"])
        self.assertEqual(seen[-1]["scheme"], "https")

    def test_the_host_header_is_not_taken_from_a_forwarded_header(self):
        """request.host is what the cross-site check compares an Origin
        against, so a client that could rewrite it could defeat that check."""
        app, seen = self.app_and_log()
        self.visit(app, "203.0.113.7, 172.16.0.1",
                   extra={"X-Forwarded-Host": "evil.example"})
        self.assertNotIn("evil.example", seen[-1]["host"])

    def test_the_hop_count_is_configurable_for_a_different_front_end(self):
        app, seen = self.app_and_log(**{public.PROXIES_ENV: "1"})
        self.visit(app, "203.0.113.7, 172.16.0.1")
        self.assertEqual(seen[-1]["ip"], "172.16.0.1")


class TestSessionStore(unittest.TestCase):
    """Bounded and expiring, because it lives in memory."""

    def test_an_idle_session_is_forgotten(self):
        clock = [0.0]
        store = public.SessionStore(ttl=100, clock=lambda: clock[0])
        app = public_app()
        with app.test_request_context():
            store.room()["resume_text"] = "x"
            self.assertEqual(len(store), 1)
            clock[0] += 101
            store.room()
        self.assertEqual(len(store), 1, "the new session replaced the stale one")

    def test_the_store_cannot_grow_without_limit(self):
        store = public.SessionStore(cap=3)
        app = public_app()
        for _ in range(10):
            with app.test_request_context():
                store.room()["x"] = 1
        self.assertLessEqual(len(store), 3)


class TestCrossSite(unittest.TestCase):
    """No CSRF tokens in the templates, so the origin is the guard."""

    def test_a_post_from_another_site_is_refused(self):
        client = unlocked(public_app())
        r = client.post("/derive", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_a_post_from_the_app_itself_is_allowed(self):
        client = unlocked(public_app())
        upload(client)
        r = client.post("/derive", headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 302)

    def test_the_session_cookie_is_locked_down(self):
        app = public_app()
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Strict")


class TestTheMarketOnRender(unittest.TestCase):
    """Render's disk is ephemeral and output/ is git-ignored, so the beta
    has no corpus of its own. Without the shipped frequency table every
    skill keeps its neutral 3 and the profile a visitor downloads cannot
    tell their specialism from a commodity."""

    def setUp(self):
        self.empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.empty, ignore_errors=True)

    def real_pipeline_app(self):
        """Public mode with the REAL finishing pipeline behind derive —
        widen_skills and reweight_from_corpus — against no corpus at all,
        which is exactly what Render runs."""
        import sys
        for path in (os.path.join(app_module.REPO_ROOT, "auto-apply"),
                     app_module.REPO_ROOT):
            if path not in sys.path:
                sys.path.insert(0, path)
        import make_profile
        from local_profile import NEUTRAL_WEIGHT

        parsed = dict(DERIVED, skill_weights=[
            {"term": t, "weight": NEUTRAL_WEIGHT} for t in
            ("react native", "javascript", "react", "maven", "spring security",
             "gradle", "docker", "java", "postgresql", "tailwind css")])

        def derive(resume_text, prefs):
            return make_profile._finish(parsed, resume_text, self.empty,
                                        lambda *a: None)

        return public_app(derive=derive, output_dir=self.empty)

    def test_the_health_check_says_which_market_answered(self):
        app = self.real_pipeline_app()
        body = app.test_client().get("/healthz").get_json()
        self.assertEqual(body["market_signal_source"], "frozen")

    def test_a_beta_visitors_profile_is_not_a_flat_wall_of_threes(self):
        app = self.real_pipeline_app()
        client = unlocked(app)
        upload(client)
        client.post("/derive")
        self.assertEqual(client.post("/review", data={"name": "beta_user"}
                                     ).status_code, 302)
        source = client.get("/profile.py").get_data(as_text=True)

        import re
        block = source.split('"skill_weights": {', 1)[1].split("}", 1)[0]
        weights = {int(n) for n in re.findall(r":\s*(\d+)", block)}
        self.assertNotEqual(weights, {3},
                            "the downloaded profile is still flat")
        self.assertTrue({2, 4} <= weights, sorted(weights))
        # The commodity and the specialism land on different numbers,
        # which is the whole point of shipping the table.
        self.assertIn("'javascript': 2", block)
        self.assertIn("'maven': 4", block)


class TestLocalModeIsUntouched(unittest.TestCase):
    """The operator's own app must not change shape because public mode
    exists. Everything below is the behaviour the existing suite asserts."""

    def test_without_the_flag_nothing_is_gated_and_state_is_shared(self):
        state = {"resume_text": "x", "derived": dict(DERIVED)}
        app = app_module.create_app(state=state,
                                    derive=lambda t, p: dict(DERIVED))
        app.config["TESTING"] = True
        self.assertIsNone(app.config.get("PUBLIC_MODE"))
        self.assertIs(app.state, state)
        client = app.test_client()
        self.assertEqual(client.get("/key").status_code, 200)
        self.assertNotIn("/beta", client.get("/").headers.get("Location", ""))

    def test_the_operators_key_chrome_still_renders(self):
        app = app_module.create_app(state={"resume_text": "x"},
                                    derive=lambda t, p: dict(DERIVED))
        app.config["TESTING"] = True
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn("No key yet", body)

    def test_public_mode_hides_that_same_chrome(self):
        client = unlocked(public_app())
        body = client.get("/").get_data(as_text=True)
        self.assertNotIn("No key yet", body)
        self.assertNotIn("Current spend", body)


if __name__ == "__main__":
    unittest.main()
