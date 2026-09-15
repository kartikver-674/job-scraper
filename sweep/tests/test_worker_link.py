"""Does a visitor keep their sweep when Render restarts?

Render Free redeploys and spins down. Oracle keeps running the sweep
either way, which is the whole point of moving it there — but only if the
returning browser can still prove the run is theirs. That proof must
therefore live in the signed cookie, or be derivable from it, and never
in this process's memory.

The test is the scenario, not a unit: a real worker on a real socket, a
real cookie, and a second Flask app built from nothing but the same
SECRET_KEY.
"""

import contextlib
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

from deploy import sweep_worker  # noqa: E402
from deploy.test_sweep_worker import PREFS, PROFILE, FakeChild  # noqa: E402
from sweep import app as app_module  # noqa: E402
from sweep import public, worker_client  # noqa: E402
from sweep.tests.test_app import DERIVED  # noqa: E402

SECRET = "a-secret-that-outlives-the-process"
WORKER_TOKEN = "worker-token"
CODE = "let-me-in"
BETA_ENV = {public.PUBLIC_ENV: "1", public.SECRET_ENV: SECRET,
            public.CODE_ENV: CODE}

# The two routes step 2 will add. Defined here so this test exercises the
# production helpers — the cookie, the derived owner, the client — through
# a real request/response cycle, without waiting for the UI around them.
STANDIN = frozenset({"t_run", "t_status", "t_stop"})


@contextlib.contextmanager
def worker_on_a_socket():
    """The real worker app, served over loopback, with a fake engine."""
    from werkzeug.serving import make_server

    runs = tempfile.mkdtemp(prefix="link-runs-")
    checkout = tempfile.mkdtemp(prefix="link-checkout-")
    os.makedirs(os.path.join(checkout, "profiles"), exist_ok=True)
    started = threading.Event()

    def spawn(run_id, run_dir, profile_name, checkout_dir, token):
        started.set()
        return FakeChild(block=threading.Event()), None

    store = sweep_worker.RunStore(runs)
    queue = sweep_worker.Queue(store, spawn=spawn, checkout=checkout)
    app = sweep_worker.create_app(store=store, queue=queue,
                                  accepted={WORKER_TOKEN}, checkout=checkout)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", store
    finally:
        server.shutdown()
        thread.join(timeout=5)
        shutil.rmtree(runs, ignore_errors=True)
        shutil.rmtree(checkout, ignore_errors=True)


def render_app(worker_url):
    """A Render process: public mode, the shared SECRET_KEY, empty memory.

    Built fresh every call — a new Flask object, a new SessionStore — so
    "restart" in these tests means what it means in production.
    """
    env = {**BETA_ENV, worker_client.URL_ENV: worker_url,
           worker_client.TOKEN_ENV: WORKER_TOKEN}
    with mock.patch.dict(os.environ, env, clear=False):
        app = app_module.create_app(
            extract=lambda path: "Ada Okonkwo, React Native dev",
            derive=lambda text, prefs: dict(DERIVED))
    app.config["TESTING"] = True

    # Step 2's routes, in miniature: create a run and remember it, then
    # read it back and stop it, using only what the cookie carries.
    @app.post("/t/run")
    def t_run():
        from flask import jsonify
        with mock.patch.dict(os.environ, env, clear=False):
            run_id = worker_client.create_run(PROFILE, PREFS, free_only=True)
        public.remember_run(run_id)
        return jsonify({"run_id": run_id})

    @app.get("/t/status")
    def t_status():
        from flask import jsonify
        run_id = public.current_run_id()
        if not run_id:
            return jsonify({"error": "no run in this session"}), 404
        with mock.patch.dict(os.environ, env, clear=False):
            try:
                return jsonify(worker_client.run_status(run_id))
            except worker_client.RunNotFound:
                return jsonify({"error": "not yours"}), 404

    @app.post("/t/stop")
    def t_stop():
        from flask import jsonify
        run_id = public.current_run_id()
        with mock.patch.dict(os.environ, env, clear=False):
            try:
                return jsonify(worker_client.stop_run(run_id))
            except worker_client.RunNotFound:
                return jsonify({"error": "not yours"}), 404

    return app


def unlocked(app):
    client = app.test_client()
    client.post("/beta", data={"code": CODE})
    return client


def cookie_of(client):
    """The browser's signed session cookie, as a string — what actually
    crosses a restart."""
    jar = client.get_cookie("session")
    return jar.value if jar else None


class TestOwnershipSurvivesARenderRestart(unittest.TestCase):

    def setUp(self):
        self.allow = mock.patch.object(
            public, "PUBLIC_ENDPOINTS", public.PUBLIC_ENDPOINTS | STANDIN)
        self.allow.start()
        self.addCleanup(self.allow.stop)

    def test_a_new_process_with_the_same_key_can_still_drive_the_run(self):
        with worker_on_a_socket() as (worker_url, store):
            # 1. A visitor starts a sweep.
            first = render_app(worker_url)
            browser = unlocked(first)
            run_id = browser.post("/t/run").get_json()["run_id"]
            self.assertEqual(store.read(run_id)["state"],
                             sweep_worker.RUNNING)

            # 2. Serialize the browser's cookie, and confirm the sweep's
            #    ownership is NOT hiding in this process's memory.
            cookie = cookie_of(browser)
            self.assertTrue(cookie)
            # Ownership is not hiding in this process: no room holds it.
            rooms = list(first.session_store._rooms.values())
            self.assertFalse(any("run_id" in room["data"] for room in rooms),
                             "the run id lives in memory, not the cookie")

            # 3. Render redeploys: a different app object, empty memory,
            #    the same SECRET_KEY. Nothing of the old process remains.
            second = render_app(worker_url)
            self.assertIsNot(second, first)
            self.assertEqual(len(second.session_store), 0)

            # 4. The same browser comes back.
            returning = second.test_client()
            returning.set_cookie("session", cookie, domain="localhost")

            status = returning.get("/t/status")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.get_json()["run_id"], run_id)
            self.assertEqual(status.get_json()["state"], sweep_worker.RUNNING)

            stopped = returning.post("/t/stop")
            self.assertEqual(stopped.status_code, 200)
            self.assertEqual(store.read(run_id)["state"],
                             sweep_worker.STOPPED)

    def test_a_different_browser_cannot_touch_that_run(self):
        with worker_on_a_socket() as (worker_url, store):
            first = render_app(worker_url)
            mine = unlocked(first)
            run_id = mine.post("/t/run").get_json()["run_id"]

            # Same Render process, different browser: its own cookie, its
            # own session id, so a different derived owner.
            stranger = unlocked(first)
            self.assertIsNone(stranger.get("/t/status").get_json().get("run_id"))

            # And even holding the run id, it cannot act on it: the owner
            # is derived from the cookie, not from the id.
            with stranger.session_transaction() as sess:
                sess["run_id"] = run_id
            refused = stranger.get("/t/status")
            self.assertEqual(refused.status_code, 404)
            self.assertEqual(stranger.post("/t/stop").status_code, 404)
            self.assertEqual(store.read(run_id)["state"],
                             sweep_worker.RUNNING, "a stranger stopped it")

    def test_the_owner_is_derived_not_stored(self):
        """Same cookie plus same key must give the same capability in a
        process that has never seen that visitor before."""
        with worker_on_a_socket() as (worker_url, _store):
            first = render_app(worker_url)
            browser = unlocked(first)
            browser.post("/t/run")
            cookie = cookie_of(browser)

            owners = []
            for app in (first, render_app(worker_url)):
                client = app.test_client()
                client.set_cookie("session", cookie, domain="localhost")
                with app.test_request_context(
                        headers={"Cookie": f"session={cookie}"}):
                    owners.append(public.owner_for_session())
            self.assertEqual(owners[0], owners[1])
            self.assertEqual(len(owners[0]), 64)  # sha256 hex

    def test_a_different_secret_key_cannot_forge_the_owner(self):
        """The capability is an HMAC under Render's key: another Render
        with a different key derives a different owner from the same
        cookie, so a leaked cookie alone is not the run."""
        with worker_on_a_socket() as (worker_url, _store):
            app = render_app(worker_url)
            with app.test_request_context():
                public.session_id()
                real = public.owner_for_session()
                forged = public.owner_for_session(secret="a-different-key")
            self.assertNotEqual(real, forged)

    def test_the_cookie_carries_the_run_id_and_no_credential(self):
        with worker_on_a_socket() as (worker_url, _store):
            app = render_app(worker_url)
            browser = unlocked(app)
            run_id = browser.post("/t/run").get_json()["run_id"]
            cookie = cookie_of(browser)

            # The id is in there (that is what survives the restart)...
            with app.test_request_context(
                    headers={"Cookie": f"session={cookie}"}):
                self.assertEqual(public.current_run_id(), run_id)
            # ... and nothing that authorises anything is.
            for secret in (WORKER_TOKEN, SECRET, "apify_api_"):
                self.assertNotIn(secret, cookie)


if __name__ == "__main__":
    unittest.main()
