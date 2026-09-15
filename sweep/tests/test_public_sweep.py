"""The public beta's sweep half, end to end against a real worker.

resume -> review -> configure -> confirm -> run -> running -> results -> export

Everything below drives the real Flask app through a test client and a
real Oracle worker on a loopback socket. The engine itself is faked — what
is under test is the wiring between a visitor's browser, Render's session
and somebody else's machine, not scraper.py, which has its own tests.
"""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

from deploy import sweep_worker  # noqa: E402
from sweep import worker_client  # noqa: E402
from sweep.tests.test_worker_link import (CODE, WORKER_TOKEN,  # noqa: E402
                                          cookie_of, render_app,
                                          worker_on_a_socket)


@contextlib.contextmanager
def stack():
    """A worker on a socket, with Render's environment pointed at it.

    The environment matters: worker_client reads SWEEP_WORKER_URL and
    SWEEP_WORKER_TOKEN per call, exactly as the real service does, so a
    test that patched them only while the app was built would be testing
    a Render that had forgotten where Oracle is.
    """
    with worker_on_a_socket() as (url, store):
        with mock.patch.dict(os.environ, {
                worker_client.URL_ENV: url,
                worker_client.TOKEN_ENV: WORKER_TOKEN}, clear=False):
            yield url, store


PDF = b"%PDF-1.7 fake"

ROWS = (
    "title,company,score,source_site,url,location,date_posted,remote_scope\n"
    "React Native Developer,Acme,42,greenhouse,https://a/1,Remote,2026-09-14,worldwide\n"
    "Mobile Engineer,Beta Ltd,31,lever,https://a/2,Bengaluru,2026-09-13,\n"
    "Backend Engineer,Gamma,12,remoteok,https://a/3,Berlin,2026-09-12,\n"
)


def visitor(app, name="beta_user"):
    """A browser that has been through the door, the résumé and the review,
    and is therefore standing on the Configure screen."""
    client = app.test_client()
    client.post("/beta", data={"code": CODE})
    client.post("/resume", data={"resume": (io.BytesIO(PDF), "cv.pdf")},
                content_type="multipart/form-data")
    client.post("/derive")
    client.post("/review", data={"name": name})
    return client


def configure(client, scope="remote", locations="Remote", days="30"):
    return client.post("/sweep/configure", data={
        "scope": scope, "locations": locations, "max_age_days": days})


def only_run(store):
    runs = store.all()
    assert len(runs) == 1, f"expected one run, found {len(runs)}"
    return runs[0]["run_id"]


def give_rows(store, run_id, csv=ROWS):
    out = store.output_dir(run_id)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "jobs_1.csv"), "w", encoding="utf-8") as fh:
        fh.write(csv)


class TestTheFlow(unittest.TestCase):
    """The journey the visitor is promised."""

    def test_review_now_leads_into_the_sweep(self):
        with stack() as (url, _store):
            app = render_app(url)
            client = app.test_client()
            client.post("/beta", data={"code": CODE})
            client.post("/resume", data={"resume": (io.BytesIO(PDF), "cv.pdf")},
                        content_type="multipart/form-data")
            client.post("/derive")
            landed = client.post("/review", data={"name": "beta_user"})
            self.assertEqual(landed.status_code, 302)
            self.assertIn("/sweep/configure", landed.headers["Location"])

    def test_configure_confirm_run_running(self):
        with stack() as (url, store):
            client = visitor(render_app(url))

            self.assertEqual(client.get("/sweep/configure").status_code, 200)
            self.assertIn("/sweep/confirm",
                          configure(client).headers["Location"])

            confirm = client.get("/sweep/confirm")
            self.assertEqual(confirm.status_code, 200)
            self.assertIn("Start free sweep", confirm.get_data(as_text=True))

            started = client.post("/sweep/run")
            self.assertIn("/sweep/running", started.headers["Location"])

            run_id = only_run(store)
            self.assertEqual(store.read(run_id)["state"], sweep_worker.RUNNING)
            self.assertTrue(store.read(run_id)["free_only"])
            self.assertEqual(client.get("/sweep/running").status_code, 200)

    def test_an_invalid_configure_field_is_refused_not_applied(self):
        with stack() as (url, _store):
            client = visitor(render_app(url))
            bad = configure(client, locations="Mordor")
            self.assertEqual(bad.status_code, 400)
            self.assertIn("not a location", bad.get_data(as_text=True))

    def test_progress_reports_queue_then_running_then_finished(self):
        with stack() as (url, store):
            app = render_app(url)
            first, second = visitor(app), visitor(app, "other")
            configure(first)
            configure(second)
            first.post("/sweep/run")
            second.post("/sweep/run")

            one = first.get("/sweep/progress").get_json()
            two = second.get("/sweep/progress").get_json()
            self.assertEqual(one["state"], sweep_worker.RUNNING)
            self.assertEqual(one["queue_position"], 0)
            self.assertEqual(two["state"], sweep_worker.QUEUED)
            self.assertEqual(two["queue_position"], 1)
            self.assertFalse(two["finished"])

    def test_stopping_a_sweep_ends_it_and_shows_what_it_found(self):
        with stack() as (url, store):
            client = visitor(render_app(url))
            configure(client)
            client.post("/sweep/run")
            run_id = only_run(store)
            give_rows(store, run_id)

            stopped = client.post("/sweep/stop")
            self.assertIn("/sweep/results", stopped.headers["Location"])
            self.assertEqual(store.read(run_id)["state"], sweep_worker.STOPPED)
            self.assertIn("React Native Developer",
                          client.get("/sweep/results").get_data(as_text=True))


class TestResultsAndExports(unittest.TestCase):

    def running_visitor(self, url, store):
        app = render_app(url)
        client = visitor(app)
        configure(client)
        client.post("/sweep/run")
        run_id = only_run(store)
        give_rows(store, run_id)
        return app, client, run_id

    def test_results_show_this_runs_rows(self):
        with stack() as (url, store):
            _app, client, _run = self.running_visitor(url, store)
            page = client.get("/sweep/results").get_data(as_text=True)
            for title in ("React Native Developer", "Mobile Engineer",
                          "Backend Engineer"):
                self.assertIn(title, page)
            self.assertIn("3 jobs for you", page)

    def test_the_filters_are_the_local_screens_filters(self):
        with stack() as (url, store):
            _app, client, _run = self.running_visitor(url, store)
            filtered = client.get("/sweep/results?min_score=30").get_data(
                as_text=True)
            self.assertIn("React Native Developer", filtered)
            self.assertNotIn("Backend Engineer", filtered)

            searched = client.get("/sweep/results?q=mobile").get_data(
                as_text=True)
            self.assertIn("Mobile Engineer", searched)
            self.assertNotIn("React Native Developer", searched)

    def test_every_export_format_hands_back_what_is_on_screen(self):
        with stack() as (url, store):
            _app, client, _run = self.running_visitor(url, store)
            csv = client.get("/sweep/export.csv")
            self.assertEqual(csv.status_code, 200)
            self.assertIn("React Native Developer", csv.get_data(as_text=True))
            self.assertIn("attachment", csv.headers["Content-Disposition"])

            data = client.get("/sweep/export.json").get_json()
            self.assertEqual(len(data), 3)

            xlsx = client.get("/sweep/export.xlsx")
            self.assertEqual(xlsx.status_code, 200)
            self.assertTrue(xlsx.get_data().startswith(b"PK"))

            # The filter on screen is the filter in the file: 42 and 31
            # survive a floor of 30, and the 12 does not.
            filtered = client.get("/sweep/export.json?min_score=30").get_json()
            self.assertEqual(len(filtered), 2)
            self.assertNotIn("Backend Engineer",
                             client.get("/sweep/export.csv?min_score=30"
                                        ).get_data(as_text=True))

    def test_an_unknown_export_format_is_a_404(self):
        with stack() as (url, store):
            _app, client, _run = self.running_visitor(url, store)
            self.assertEqual(client.get("/sweep/export.pdf").status_code, 404)


class TestSessionIsolation(unittest.TestCase):
    """Somebody else's shortlist is somebody else's."""

    def test_a_second_visitor_sees_none_of_the_first_ones_run(self):
        with stack() as (url, store):
            app = render_app(url)
            mine = visitor(app)
            configure(mine)
            mine.post("/sweep/run")
            run_id = only_run(store)
            give_rows(store, run_id)

            stranger = visitor(app, "stranger")
            # No run of their own: the screens send them back, and show
            # nothing of mine.
            self.assertEqual(stranger.get("/sweep/progress").status_code, 404)
            for path in ("/sweep/results", "/sweep/running"):
                answer = stranger.get(path)
                self.assertEqual(answer.status_code, 302)
                self.assertIn("/sweep/confirm", answer.headers["Location"])
            self.assertNotIn("React Native Developer",
                             stranger.get("/sweep/results",
                                          follow_redirects=True).get_data(
                                              as_text=True))

    def test_holding_the_run_id_is_not_enough(self):
        with stack() as (url, store):
            app = render_app(url)
            mine = visitor(app)
            configure(mine)
            mine.post("/sweep/run")
            run_id = only_run(store)
            give_rows(store, run_id)

            stranger = visitor(app, "stranger")
            with stranger.session_transaction() as sess:
                sess["run_id"] = run_id          # stolen, somehow
            # The owner is derived from THEIR cookie, so the worker does not
            # recognise them: the run is dropped rather than shown.
            self.assertEqual(stranger.get("/sweep/progress").status_code, 404)
            self.assertNotIn("React Native Developer",
                             stranger.get("/sweep/results",
                                          follow_redirects=True).get_data(
                                              as_text=True))
            self.assertEqual(store.read(run_id)["state"],
                             sweep_worker.RUNNING, "a stranger stopped it")

    def test_one_visitor_cannot_stop_anothers_sweep(self):
        with stack() as (url, store):
            app = render_app(url)
            mine = visitor(app)
            configure(mine)
            mine.post("/sweep/run")
            run_id = only_run(store)

            stranger = visitor(app, "stranger")
            with stranger.session_transaction() as sess:
                sess["run_id"] = run_id
            stranger.post("/sweep/stop")
            self.assertEqual(store.read(run_id)["state"],
                             sweep_worker.RUNNING)


class TestRenderRestart(unittest.TestCase):
    """Oracle keeps sweeping while Render redeploys."""

    def test_a_returning_browser_finds_its_running_sweep(self):
        with stack() as (url, store):
            first = render_app(url)
            client = visitor(first)
            configure(client)
            client.post("/sweep/run")
            run_id = only_run(store)
            cookie = cookie_of(client)

            # Render redeploys: new app object, empty memory, same key.
            second = render_app(url)
            self.assertEqual(len(second.session_store), 0)
            returning = second.test_client()
            returning.set_cookie("session", cookie, domain="localhost")

            self.assertEqual(returning.get("/sweep/running").status_code, 200)
            progress = returning.get("/sweep/progress").get_json()
            self.assertEqual(progress["state"], sweep_worker.RUNNING)

            give_rows(store, run_id)
            page = returning.get("/sweep/results").get_data(as_text=True)
            self.assertIn("React Native Developer", page)
            self.assertIn("React Native Developer",
                          returning.get("/sweep/export.csv").get_data(
                              as_text=True))

    def test_a_run_oracle_has_forgotten_does_not_wedge_the_screens(self):
        """After the 48h cleanup the id in the cookie names nothing. The
        visitor should land on Configure, not on an error."""
        with stack() as (url, store):
            app = render_app(url)
            client = visitor(app)
            configure(client)
            client.post("/sweep/run")
            store.delete(only_run(store))

            answer = client.get("/sweep/running")
            self.assertEqual(answer.status_code, 302)
            self.assertIn("/sweep/confirm", answer.headers["Location"])
            self.assertEqual(client.get("/sweep/progress").status_code, 404)


class TestTheOldPathStaysShut(unittest.TestCase):
    """Public mode must not reach the console's subprocess or its money."""

    def test_the_local_run_and_results_routes_are_still_404(self):
        with stack() as (url, _store):
            client = visitor(render_app(url))
            # Each by the method it actually answers on, so a 405 from a
            # method mismatch cannot stand in for a refusal.
            for path in ("/running", "/results", "/export.csv", "/key",
                         "/configure", "/confirm", "/progress", "/events"):
                with self.subTest(get=path):
                    self.assertEqual(client.get(path).status_code, 404)
            for path in ("/run", "/stop", "/rescore", "/estimate", "/merge",
                         "/key/free", "/key/remove", "/second-key"):
                with self.subTest(post=path):
                    self.assertEqual(client.post(path).status_code, 404)

    def test_the_worker_token_never_reaches_the_browser(self):
        with stack() as (url, store):
            client = visitor(render_app(url))
            configure(client)
            started = client.post("/sweep/run")
            give_rows(store, only_run(store))
            seen = [started.get_data(as_text=True)]
            for path in ("/sweep/running", "/sweep/results",
                         "/sweep/confirm", "/sweep/progress"):
                answer = client.get(path)
                seen.append(answer.get_data(as_text=True))
                seen.append(str(answer.headers))
            body = " ".join(seen)
            from sweep.tests.test_worker_link import WORKER_TOKEN
            self.assertNotIn(WORKER_TOKEN, body)
            self.assertNotIn(url, body, "the worker's address is not public")


if __name__ == "__main__":
    unittest.main()
