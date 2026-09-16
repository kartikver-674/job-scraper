"""A sweep you own is visible everywhere, and stays yours.

The promise the running screen makes — "You can close this tab, Sweep keeps
searching" — was true of the worker and false of the UI. Leaving /running was
indistinguishable from losing the sweep: no screen said one was alive, the
Search stage pointed back at the form that would start a second, and the jobs
simply appeared under /results later with nothing having announced them.

Nothing new stores the run. The id is where it always was (the signed
cookie), ownership is still the HMAC capability re-derived per request, and
the live figures still come from /progress. What these tests hold is that the
existing mechanism is now SURFACED, and that surfacing it did not widen it:
the strip is subject to exactly the ownership rules /running and /results are,
and a stranger — even one holding a real run id — sees nothing.

The letters map to the matrix these were written against.
"""

import contextlib
import html  # noqa: F401  (used inside flat)
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

from deploy import sweep_worker  # noqa: E402
from sweep.logic import run_banner, run_phase  # noqa: E402
from sweep.tests.test_public_sweep import (PDF, give_rows,  # noqa: E402
                                           only_run, stack)
from sweep.tests.test_worker_link import (CODE, cookie_of,  # noqa: E402
                                          render_app)


def sweeping(app):
    """A visitor whose free sweep is away on the worker."""
    client = app.test_client()
    client.post("/beta", data={"code": CODE})
    client.post("/resume", data={"resume": (io.BytesIO(PDF), "cv.pdf")},
                content_type="multipart/form-data")
    client.post("/derive")
    client.post("/review", data={})
    client.post("/key/free")
    client.get("/configure")
    client.post("/run")
    return client


def flat(body):
    """Rendered text, not source formatting.

    Two differences between the two, both of which made a correct page fail
    a correct assertion: HTML collapses a newline inside an element, so a
    label a template wrapped across two lines reads the same to a user and
    differently to `in`; and autoescaping turns "You're next" into
    "You&#39;re next", which is the escaping working."""
    import html
    return " ".join(html.unescape(body).split())


def strip(body):
    """The status strip's text, or "" when it is not on the page."""
    if 'id="runbar"' not in body:
        return ""
    start = body.index('id="runbar"')
    return flat(body[start:body.index("</div>", start)])


@contextlib.contextmanager
def beta():
    with stack() as (url, store):
        yield render_app(url, check_token=lambda t: (4.25, None)), store


class TestTheStripFollowsYouAround(unittest.TestCase):

    def test_A_leaving_running_does_not_lose_the_sweep(self):
        """Start, go to Profile, and the sweep is still on screen with one
        action that returns to it."""
        with beta() as (app, _store):
            client = sweeping(app)
            for path in ("/", "/review", "/key", "/configure", "/results"):
                with self.subTest(path=path):
                    said = strip(client.get(path, follow_redirects=True)
                                 .get_data(as_text=True))
                    self.assertTrue(said, f"{path} shows no active sweep")
                    self.assertIn("Sweep", said)
                    self.assertIn("View progress", said)
                    self.assertIn('href="/running"', said)

    def test_A2_the_running_screen_itself_does_not_repeat_the_strip(self):
        """The whole page is the sweep there; a strip saying so above it is
        the same sentence twice."""
        with beta() as (app, _store):
            client = sweeping(app)
            self.assertEqual(strip(client.get("/running").get_data(as_text=True)), "")

    def test_B_the_search_stage_returns_to_the_running_sweep(self):
        """Not to /configure, which is the form that would start a second.

        Checked from Profile: a stage never links to itself, so on a
        Search-stage page the chip is correctly unlinked and the strip is
        what carries the visitor back."""
        with beta() as (app, _store):
            client = sweeping(app)
            body = client.get("/review", follow_redirects=True).get_data(as_text=True)
            rail = body[body.index('class="tracker"'):body.index("</nav>")]
            self.assertIn('href="/running"', rail)
            self.assertNotIn('href="/key"', rail)
            # And the stage still carries its own name, not the route's.
            self.assertIn("Search", rail)

    def test_B3_on_a_search_page_the_strip_is_the_way_back(self):
        """The chip cannot help there — it is the current stage — so the
        strip has to."""
        with beta() as (app, _store):
            client = sweeping(app)
            said = strip(client.get("/configure").get_data(as_text=True))
            self.assertIn('href="/running"', said)
            self.assertIn("View progress", said)

    def test_B2_with_no_sweep_the_search_stage_is_the_form_again(self):
        with beta() as (app, _store):
            client = app.test_client()
            client.post("/beta", data={"code": CODE})
            client.post("/resume", data={"resume": (io.BytesIO(PDF), "cv.pdf")},
                        content_type="multipart/form-data")
            client.post("/derive")
            client.post("/review", data={})
            body = client.get("/review").get_data(as_text=True)
            rail = body[body.index('class="tracker"'):body.index("</nav>")]
            self.assertIn('href="/key"', rail)
            self.assertNotIn('href="/running"', rail)

    def test_C_a_queued_sweep_says_where_it_is_in_the_queue(self):
        """A REAL queue, not a faked state: the worker runs MAX_ACTIVE=1, so
        a second visitor's sweep genuinely waits behind the first, and the
        position has to travel worker -> worker_link -> the strip."""
        with beta() as (app, store):
            first = sweeping(app)
            second = sweeping(app)
            self.assertEqual(len(store.all()), 2, "the second run was refused")

            said = strip(second.get("/review", follow_redirects=True)
                         .get_data(as_text=True))
            self.assertIn("Sweep queued", said)
            self.assertIn("You're next", said)
            self.assertIn("View progress", said)

            # The one in front is running, not queued.
            ahead = strip(first.get("/review", follow_redirects=True)
                          .get_data(as_text=True))
            self.assertIn("Sweep in progress", ahead)

    def test_C2_the_queue_position_is_the_worker_s_own_count(self):
        with beta() as (app, store):
            sweeping(app)
            sweeping(app)
            third = sweeping(app)
            said = strip(third.get("/review", follow_redirects=True)
                         .get_data(as_text=True))
            self.assertIn("Sweep queued", said)
            self.assertIn("2 sweeps ahead", said)

    def test_D_the_strip_changes_when_the_sweep_finishes(self):
        with beta() as (app, store):
            client = sweeping(app)
            run_id = only_run(store)
            live = strip(client.get("/review").get_data(as_text=True))
            self.assertIn("View progress", live)

            store.update(run_id, state=sweep_worker.DONE)
            give_rows(store, run_id)
            done = strip(client.get("/review").get_data(as_text=True))
            self.assertIn("Sweep complete", done)
            self.assertIn("View jobs", done)
            self.assertIn('href="/results"', done)

    def test_D2_the_poll_carries_the_same_wording_the_page_was_served_with(self):
        """One mapping, two callers. A strip that re-derived its phrasing in
        JavaScript would flicker into different words on the first poll."""
        with beta() as (app, _store):
            client = sweeping(app)
            served = strip(client.get("/review").get_data(as_text=True))
            polled = client.get("/progress").get_json()["banner"]
            self.assertIn(polled["headline"], served)
            self.assertIn(polled["cta"], served)

    def test_I_a_stopped_sweep_still_points_somewhere_useful(self):
        with beta() as (app, store):
            client = sweeping(app)
            run_id = only_run(store)
            give_rows(store, run_id)
            for state, expected in ((sweep_worker.STOPPED, "Sweep stopped"),
                                    (sweep_worker.FAILED, "Sweep stopped early"),
                                    (sweep_worker.INTERRUPTED, "Sweep stopped early")):
                with self.subTest(state=state):
                    store.update(run_id, state=state)
                    said = strip(client.get("/review").get_data(as_text=True))
                    self.assertIn(expected, said)
                    # Never a dead end: what it found is still worth opening.
                    self.assertIn("View jobs", said)
                    self.assertIn('href="/results"', said)

    def test_the_strip_stands_down_on_the_results_screen(self):
        """A "Sweep complete — view jobs" strip above the jobs is one line of
        chrome telling the reader to go where they already are."""
        with beta() as (app, store):
            client = sweeping(app)
            run_id = only_run(store)
            store.update(run_id, state=sweep_worker.DONE)
            give_rows(store, run_id)
            self.assertIn("Sweep complete",
                          strip(client.get("/review").get_data(as_text=True)))
            self.assertEqual(strip(client.get("/results").get_data(as_text=True)), "")

    def test_a_running_sweep_still_shows_on_results(self):
        """The opposite case: rows are arriving, so the strip is the only
        thing saying more are coming."""
        with beta() as (app, store):
            client = sweeping(app)
            give_rows(store, only_run(store))
            said = strip(client.get("/results").get_data(as_text=True))
            self.assertIn("Sweep in progress", said)


class TestComingBackAfterClosingTheTab(unittest.TestCase):
    """The scenario the whole component exists for. A new Flask object with
    empty memory is what a redeploy or a spun-down Render instance IS."""

    def test_E_a_reopened_browser_finds_its_running_sweep(self):
        with beta() as (app, store):
            client = sweeping(app)
            biscuit = cookie_of(client)
            self.assertTrue(biscuit)

            fresh = render_app(os.environ["SWEEP_WORKER_URL"])
            self.assertEqual(len(fresh.session_store), 0)
            returning = fresh.test_client()
            returning.set_cookie("session", biscuit, domain="localhost")

            # follow_redirects because that is what a browser does, and a
            # process that has forgotten the résumé sends /review back to the
            # upload screen — deliberately (worker_link.rehydrate restores the
            # run, never the parse). The sweep has to be visible wherever the
            # visitor actually lands.
            landed = returning.get("/review", follow_redirects=True)
            said = strip(landed.get_data(as_text=True))
            self.assertIn("Sweep in progress", said)
            self.assertIn('href="/running"', said)
            # And the action works, on a process that never started this run.
            self.assertEqual(returning.get("/running").status_code, 200)

    def test_E2_the_strip_is_on_the_screen_a_returning_visitor_lands_on(self):
        """A reopened browser with no résumé in memory lands on the upload
        screen. If the sweep were invisible there, it would be invisible at
        exactly the moment this component exists for."""
        with beta() as (app, store):
            client = sweeping(app)
            biscuit = cookie_of(client)
            fresh = render_app(os.environ["SWEEP_WORKER_URL"])
            returning = fresh.test_client()
            returning.set_cookie("session", biscuit, domain="localhost")
            front = returning.get("/")
            self.assertEqual(front.status_code, 200)
            self.assertIn("Sweep in progress",
                          strip(front.get_data(as_text=True)))

    def test_F_a_sweep_that_finished_while_the_tab_was_shut(self):
        with beta() as (app, store):
            client = sweeping(app)
            biscuit = cookie_of(client)
            run_id = only_run(store)
            store.update(run_id, state=sweep_worker.DONE)
            give_rows(store, run_id)

            fresh = render_app(os.environ["SWEEP_WORKER_URL"])
            returning = fresh.test_client()
            returning.set_cookie("session", biscuit, domain="localhost")

            landed = returning.get("/review", follow_redirects=True)
            said = strip(landed.get_data(as_text=True))
            self.assertIn("Sweep complete", said)
            self.assertIn("View jobs", said)
            self.assertEqual(returning.get("/results").status_code, 200)

    def test_coming_back_is_not_a_forced_redirect(self):
        """Persistent status, not a kidnapping: a visitor who deliberately
        opens their profile gets their profile."""
        with beta() as (app, _store):
            client = sweeping(app)
            for path in ("/", "/review", "/key", "/configure"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 200)


class TestItIsStillYourSweepAndNobodyElses(unittest.TestCase):

    def test_G_a_visitor_with_no_run_sees_no_strip_and_costs_no_call(self):
        with beta() as (app, _store):
            stranger = app.test_client()
            stranger.post("/beta", data={"code": CODE})
            body = stranger.get("/").get_data(as_text=True)
            self.assertEqual(strip(body), "")
            self.assertNotIn("runbar", body)

    def test_G2_a_second_visitor_cannot_see_the_first_ones_sweep(self):
        with beta() as (app, _store):
            sweeping(app)
            other = app.test_client()
            other.post("/beta", data={"code": CODE})
            self.assertEqual(strip(other.get("/review").get_data(as_text=True)), "")

    def test_H_holding_a_real_run_id_is_not_enough(self):
        """Ownership is the HMAC derived from the browser's OWN cookie, so a
        planted run id buys nothing — the same rule /running has always had,
        and the strip must not be a way around it."""
        with beta() as (app, store):
            sweeping(app)
            run_id = only_run(store)

            thief = app.test_client()
            thief.post("/beta", data={"code": CODE})
            with thief.session_transaction() as sess:
                sess["run_id"] = run_id
            body = thief.get("/review").get_data(as_text=True)
            self.assertEqual(strip(body), "")
            self.assertNotIn(run_id, body)
            # And the run itself stays shut to them.
            self.assertEqual(thief.get("/progress").status_code, 409)

    def test_H2_the_strip_never_prints_the_run_id_or_anything_of_the_worker(self):
        with beta() as (app, store):
            client = sweeping(app)
            run_id = only_run(store)
            body = client.get("/review").get_data(as_text=True)
            self.assertNotIn(run_id, body)
            for leak in ("X-Sweep-Owner", "Bearer", "worker", "run_id",
                         "apify_token", "rehydrat", "queue_position"):
                with self.subTest(leak=leak):
                    self.assertNotIn(leak, strip(body))


class TestOneSweepAtATime(unittest.TestCase):

    def test_J_reloading_running_does_not_start_a_second_sweep(self):
        with beta() as (app, store):
            client = sweeping(app)
            for _ in range(4):
                self.assertEqual(client.get("/running").status_code, 200)
                client.get("/progress")
            self.assertEqual(len(store.all()), 1)

    def test_K_starting_another_sweep_returns_you_to_the_live_one(self):
        with beta() as (app, store):
            client = sweeping(app)
            answer = client.post("/run")
            self.assertEqual(answer.status_code, 302)
            self.assertIn("/running", answer.headers["Location"])
            self.assertEqual(len(store.all()), 1, "a second run was created")

    def test_K2_the_start_button_is_not_offered_while_one_is_live(self):
        """The refusal above is the backstop. The screen should not have
        invited it."""
        with beta() as (app, _store):
            client = sweeping(app)
            body = flat(client.get("/configure").get_data(as_text=True))
            self.assertNotIn("Start Free Sweep", body)
            self.assertIn("View my Sweep", body)
            self.assertIn("You already have a Sweep in progress", body)


class TestTheWordingIsDecidedInOnePlace(unittest.TestCase):
    """run_banner is pure, so the phrasing is testable without a worker."""

    def test_a_queue_of_one_says_you_are_next(self):
        said = run_banner("queued", queue_position=1)
        self.assertEqual(said["detail"], "You're next")

    def test_a_longer_queue_counts_it(self):
        self.assertEqual(run_banner("queued", queue_position=3)["detail"],
                         "3 sweeps ahead")

    def test_a_count_is_only_shown_when_there_is_one(self):
        """A free sweep emits no rows until the end, so "0 jobs found so far"
        would be a fabricated figure — the defect the running screen's own
        headline was gated to avoid."""
        self.assertEqual(run_banner("running", found=0)["detail"],
                         "Searching for jobs")
        self.assertEqual(run_banner("running", found=None)["detail"],
                         "Searching for jobs")
        self.assertEqual(run_banner("running", found=184)["detail"],
                         "184 jobs found so far")
        self.assertEqual(run_banner("running", found=1)["detail"],
                         "1 job found so far")

    def test_queued_wins_over_a_snapshot_that_says_running(self):
        """snapshot() reports state="running" for a run the worker has taken
        but not started, because the child it asks about is the one it would
        have. "Sweep in progress" over a waiting run is the claim this
        component exists to stop making."""
        self.assertEqual(run_phase("running", queued=True), "queued")
        self.assertEqual(run_phase("queued"), "queued")

    def test_both_vocabularies_fold_to_the_same_phases(self):
        for worker_state, expected in (("done", "finished"),
                                       ("interrupted", "failed"),
                                       ("stopped", "stopped")):
            with self.subTest(worker_state):
                self.assertEqual(run_phase(worker_state), expected)
        for snap_state, expected in (("finished", "finished"),
                                     ("halted", "failed"),
                                     ("out_of_credit", "failed")):
            with self.subTest(snap_state):
                self.assertEqual(run_phase(snap_state), expected)

    def test_no_user_facing_string_uses_our_own_words(self):
        for phase in ("queued", "running", "finished", "stopped", "failed"):
            said = run_banner(phase, queue_position=2, found=7)
            text = f"{said['headline']} {said['detail']} {said['cta']}".lower()
            for ours in ("worker", "run_id", "rehydrat", "poll", "process",
                         "checkpoint", "oracle", "actor", "child"):
                with self.subTest(phase=phase, word=ours):
                    self.assertNotIn(ours, text)


if __name__ == "__main__":
    unittest.main()
