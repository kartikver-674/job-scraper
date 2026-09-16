"""The public beta drives the console's OWN screens, against Oracle.

resume -> review -> key (free or paid) -> configure -> confirm -> run ->
running -> results -> export

There are no public copies of those pages. The console's templates have
carried a `free_only` branch since long before this worker existed, so
public mode changes what the routes reach for — the plan, the sweep, the
rows — and leaves the HTML alone. These tests hold that line from both
directions: the public flow renders the console's templates, and the
console itself still renders everything it did, including the controls
public mode hides.
"""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from flask import template_rendered

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

from deploy import sweep_worker  # noqa: E402
from sweep import app as app_module  # noqa: E402
from sweep import worker_client  # noqa: E402
from sweep.tests.test_app import DERIVED  # noqa: E402
from sweep.tests.test_worker_link import (CODE, WORKER_TOKEN,  # noqa: E402
                                          cookie_of, render_app,
                                          worker_on_a_socket)

PDF = b"%PDF-1.7 fake"

ROWS = (
    "title,company,score,source_site,url,location,date_posted\n"
    "React Native Developer,Acme,42,greenhouse,https://a/1,Remote,2026-09-14\n"
    "Mobile Engineer,Beta Ltd,31,lever,https://a/2,Bengaluru,2026-09-13\n"
    "Backend Engineer,Gamma,12,remoteok,https://a/3,Berlin,2026-09-12\n"
)

# Every route that exists for the operator alone: their Apify keys, their
# disk, their earlier sweeps, and the event stream public mode replaces
# with polling.
OPERATOR_POSTS = ("/second-key", "/key/remove", "/rescore",
                  "/merge", "/applied")
OPERATOR_GETS = ("/events",)


@contextlib.contextmanager
def stack():
    """A worker on a socket, with Render's environment pointed at it."""
    with worker_on_a_socket() as (url, store):
        with mock.patch.dict(os.environ, {
                worker_client.URL_ENV: url,
                worker_client.TOKEN_ENV: WORKER_TOKEN}, clear=False):
            yield url, store


@contextlib.contextmanager
def templates_used(app):
    """Which template files a request actually rendered."""
    seen = []

    def record(_sender, template, **_kw):
        seen.append(template.name)

    template_rendered.connect(record, app)
    try:
        yield seen
    finally:
        template_rendered.disconnect(record, app)


def reviewed(app, name="beta_user"):
    """A visitor standing where "Looks right" leaves them."""
    client = app.test_client()
    client.post("/beta", data={"code": CODE})
    client.post("/resume", data={"resume": (io.BytesIO(PDF), "cv.pdf")},
                content_type="multipart/form-data")
    client.post("/derive")
    client.post("/review", data={"name": name})
    return client


def sweeping(app, store, name="beta_user"):
    """A visitor whose free sweep is running on the worker."""
    client = reviewed(app, name)
    client.post("/key/free")
    client.get("/configure")
    client.post("/run")
    return client


def only_run(store):
    runs = store.all()
    assert len(runs) == 1, f"expected one run, found {len(runs)}"
    return runs[0]["run_id"]


def give_rows(store, run_id, csv=ROWS):
    out = store.output_dir(run_id)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "jobs_1.csv"), "w", encoding="utf-8") as fh:
        fh.write(csv)


def local_app(**kw):
    """The operator's console, exactly as it has always been built."""
    kw.setdefault("state", {"resume_text": "x", "derived": dict(DERIVED),
                            "profile": "kartik"})
    kw.setdefault("derive", lambda text, prefs: dict(DERIVED))
    app = app_module.create_app(**kw)
    app.config["TESTING"] = True
    return app


class TestItUsesTheConsolesOwnTemplates(unittest.TestCase):
    """The point of the consolidation: no second copy of any screen."""

    def test_every_screen_renders_the_existing_template(self):
        with stack() as (url, store):
            app = render_app(url)
            client = sweeping(app, store)
            give_rows(store, only_run(store))

            for path, expected in (("/key", "key.html"),
                                   ("/configure", "configure.html"),
                                   ("/confirm", "confirm.html"),
                                   ("/running", "running.html"),
                                   ("/results", "results.html")):
                with self.subTest(path=path):
                    with templates_used(app) as seen:
                        self.assertEqual(client.get(path).status_code, 200)
                    self.assertIn(expected, seen)

    def test_no_public_copies_of_those_screens_exist(self):
        here = os.path.join(REPO_ROOT, "sweep", "templates")
        for gone in ("beta_configure.html", "beta_confirm.html",
                     "beta_running.html", "beta_results.html"):
            self.assertFalse(os.path.exists(os.path.join(here, gone)), gone)

    def test_review_leads_to_the_existing_source_choice_page(self):
        with stack() as (url, _store):
            client = reviewed(render_app(url))
            with templates_used(client.application) as seen:
                self.assertEqual(client.get("/key").status_code, 200)
            self.assertIn("key.html", seen)


class TestTheFreeSweep(unittest.TestCase):
    """The journey, on those screens, against the worker."""

    def test_choosing_free_runs_it_on_oracle(self):
        with stack() as (url, store):
            app = render_app(url)
            client = reviewed(app)
            self.assertIn("/configure",
                          client.post("/key/free").headers["Location"])
            self.assertEqual(client.get("/configure").status_code, 200)
            self.assertEqual(client.get("/confirm").status_code, 200)

            started = client.post("/run")
            self.assertIn("/running", started.headers["Location"])
            run = store.read(only_run(store))
            self.assertEqual(run["state"], sweep_worker.RUNNING)
            self.assertTrue(run["free_only"], "a beta run must never be paid")

    def test_the_progress_endpoint_is_the_consoles_own(self):
        with stack() as (url, store):
            client = sweeping(render_app(url), store)
            p = client.get("/progress").get_json()
            # The console's snapshot shape, with the free path's values.
            self.assertEqual(p["spend"], 0.0)
            self.assertTrue(p["spend_known"])
            self.assertFalse(p["finished"])

    def test_it_writes_nothing_to_the_servers_disk(self):
        """Caught by test_app's audit hook: /run was writing run.json into
        output/<profile>/, which is a shared disk under a name a second
        visitor can choose too. The run is recorded on the worker."""
        with stack() as (url, store):
            app = render_app(url)
            output = app.config["OUTPUT_DIR"]
            before = set(os.listdir(output)) if os.path.isdir(output) else set()
            sweeping(app, store)
            after = set(os.listdir(output)) if os.path.isdir(output) else set()
            self.assertEqual(before, after)

    def test_stop_ends_the_run_on_the_worker(self):
        with stack() as (url, store):
            client = sweeping(render_app(url), store)
            client.post("/stop")
            self.assertEqual(store.read(only_run(store))["state"],
                             sweep_worker.STOPPED)


class TestResultsAndExports(unittest.TestCase):
    """Unchanged behaviour: same filters, same files."""

    def running_visitor(self, url, store):
        app = render_app(url)
        client = sweeping(app, store)
        give_rows(store, only_run(store))
        return app, client

    def test_results_show_this_runs_rows(self):
        with stack() as (url, store):
            _app, client = self.running_visitor(url, store)
            page = client.get("/results").get_data(as_text=True)
            for title in ("React Native Developer", "Mobile Engineer",
                          "Backend Engineer"):
                self.assertIn(title, page)

    def test_the_filters_still_filter(self):
        with stack() as (url, store):
            _app, client = self.running_visitor(url, store)
            filtered = client.get("/results?min=30").get_data(as_text=True)
            self.assertIn("React Native Developer", filtered)
            self.assertNotIn("Backend Engineer", filtered)

    def test_every_export_format_still_works(self):
        with stack() as (url, store):
            _app, client = self.running_visitor(url, store)
            csv = client.get("/export.csv")
            self.assertEqual(csv.status_code, 200)
            self.assertIn("React Native Developer", csv.get_data(as_text=True))

            self.assertEqual(len(client.get("/export.json").get_json()), 3)
            self.assertTrue(client.get("/export.xlsx").get_data()
                            .startswith(b"PK"))

            page = client.get("/export.html")
            self.assertEqual(page.status_code, 200)
            self.assertIn("text/html", page.headers["Content-Type"])
            self.assertIn("attachment", page.headers["Content-Disposition"])
            body = page.get_data(as_text=True)
            for title in ("React Native Developer", "Mobile Engineer",
                          "Backend Engineer"):
                self.assertIn(title, body)
            # One file: it must open in five years with no network.
            for outside in ("http://fonts", "https://fonts", "cdn.",
                            "<script"):
                self.assertNotIn(outside, body)
            # The filter on screen is the filter in the file — in every
            # format, the page included.
            self.assertEqual(
                len(client.get("/export.json?min=30").get_json()), 2)
            filtered = client.get("/export.html?min=30").get_data(as_text=True)
            self.assertIn("React Native Developer", filtered)
            self.assertNotIn("Backend Engineer", filtered)


class TestOperatorControlsAreNotPublic(unittest.TestCase):
    """A visitor may act on their own run and nothing else."""

    def test_the_operator_routes_are_all_refused(self):
        with stack() as (url, store):
            client = sweeping(render_app(url), store)
            for path in OPERATOR_POSTS:
                with self.subTest(post=path):
                    self.assertEqual(client.post(path).status_code, 404)
            for path in OPERATOR_GETS:
                with self.subTest(get=path):
                    self.assertEqual(client.get(path).status_code, 404)

    def test_no_screen_offers_them(self):
        """404 is the guard; not drawing the control is the courtesy."""
        with stack() as (url, store):
            client = sweeping(render_app(url), store)
            give_rows(store, only_run(store))
            for path in ("/key", "/configure", "/confirm", "/running",
                         "/results"):
                body = client.get(path).get_data(as_text=True)
                for control in ("/second-key", "/rescore", "/merge",
                                "/applied", "/events"):
                    with self.subTest(path=path, control=control):
                        self.assertNotIn(control, body)

    def test_the_operators_own_key_state_never_reaches_a_page(self):
        with stack() as (url, store):
            client = sweeping(render_app(url), store)
            for path in ("/key", "/configure", "/confirm", "/running"):
                body = client.get(path).get_data(as_text=True)
                self.assertNotIn("Key connected", body)
                self.assertNotIn("Credit left", body)

    def test_the_running_screen_polls_instead_of_streaming(self):
        with stack() as (url, store):
            client = sweeping(render_app(url), store)
            body = client.get("/running").get_data(as_text=True)
            self.assertIn("/progress", body)
            self.assertNotIn("EventSource", body)


class TestSessionIsolation(unittest.TestCase):

    def test_a_second_visitor_sees_nothing_of_the_first(self):
        with stack() as (url, store):
            app = render_app(url)
            mine = sweeping(app, store)
            give_rows(store, only_run(store))
            self.assertIn("React Native Developer",
                          mine.get("/results").get_data(as_text=True))

            stranger = reviewed(app, "stranger")
            self.assertNotIn("React Native Developer",
                             stranger.get("/results", follow_redirects=True
                                          ).get_data(as_text=True))

    def test_holding_the_run_id_is_not_enough(self):
        with stack() as (url, store):
            app = render_app(url)
            sweeping(app, store)
            run_id = only_run(store)
            give_rows(store, run_id)

            stranger = reviewed(app, "stranger")
            with stranger.session_transaction() as sess:
                sess["run_id"] = run_id
            self.assertNotIn("React Native Developer",
                             stranger.get("/results", follow_redirects=True
                                          ).get_data(as_text=True))
            stranger.post("/stop")
            self.assertEqual(store.read(run_id)["state"],
                             sweep_worker.RUNNING, "a stranger stopped it")


class TestRenderRestart(unittest.TestCase):
    """Oracle keeps sweeping while Render redeploys."""

    def test_a_returning_browser_still_sees_its_sweep(self):
        with stack() as (url, store):
            first = render_app(url)
            client = sweeping(first, store)
            run_id = only_run(store)
            cookie = cookie_of(client)

            second = render_app(url)          # new process, empty memory
            self.assertEqual(len(second.session_store), 0)
            returning = second.test_client()
            returning.set_cookie("session", cookie, domain="localhost")

            with templates_used(second) as seen:
                self.assertEqual(returning.get("/running").status_code, 200)
            self.assertIn("running.html", seen)

            give_rows(store, run_id)
            self.assertIn("React Native Developer",
                          returning.get("/results").get_data(as_text=True))
            self.assertIn("React Native Developer",
                          returning.get("/export.csv").get_data(as_text=True))


class TestTheConsoleIsUnchanged(unittest.TestCase):
    """Everything above must cost the operator nothing."""

    def test_it_still_offers_the_key_form(self):
        key_page = local_app().test_client().get("/key").get_data(as_text=True)
        self.assertIn('action="/key"', key_page)
        self.assertIn("Verify key", key_page)
        self.assertNotIn("not in the beta yet", key_page)

    def test_its_running_screen_still_streams(self):
        app = local_app(state={
            "resume_text": "x", "derived": dict(DERIVED), "profile": "kartik",
            "free_only": True,
            "raw_plan": {"profile": "kartik", "sites": {}, "max_results": {}},
            "plan": {"profile": "kartik", "lines": [], "total": 0,
                     "total_searches": 0, "free_sources": 3}})
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn("EventSource", body)

    def test_its_operator_routes_are_reachable(self):
        client = local_app().test_client()
        # Not 404: the console owns these. Their own guards may redirect or
        # refuse on state, which is a different thing from "gone".
        for path in ("/key", "/second-key", "/rescore", "/merge"):
            with self.subTest(path=path):
                self.assertNotEqual(client.post(path).status_code, 404)
        self.assertNotEqual(client.get("/events").status_code, 404)

    def test_it_is_not_in_public_mode_and_keeps_one_shared_state(self):
        state = {"resume_text": "x", "derived": dict(DERIVED)}
        app = local_app(state=state)
        self.assertIsNone(app.config.get("PUBLIC_MODE"))
        self.assertIs(app.state, state)


if __name__ == "__main__":
    unittest.main()
