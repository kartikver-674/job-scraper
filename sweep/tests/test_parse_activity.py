"""Reading a résumé is something Sweep does for you, so it is findable too.

The parse was already more durable than it looked: it is synchronous, but a
gthread worker does not abort a handler when the browser goes away, so the
model call finishes and the result lands in the session room whatever the tab
did. What was missing was any way for a LATER request to know one was open —
and the cost of that gap was measured, not guessed:

    two concurrent POST /derive for ONE résumé
      -> 2 model calls, 4 GPU calls, 2 of a visitor's 3 daily slots

because derived_for_state() cached only the RESULT, and during the twenty to
a hundred and fifty seconds before there was one, `derived` was still None —
so the refresh that deriving.html auto-submits started a second parse.

These hold the marker that closes that window, and the strip that reads it.
Nothing here polls a clock: the "model" is a gate the test opens, so a parse
is in flight for exactly as long as the test wants it to be.

The letters map to the matrix these were written against.
"""

import html
import io
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

from sweep import app as app_module  # noqa: E402
from sweep.logic import parse_banner, pick_activity, run_banner  # noqa: E402
from sweep.tests.test_app import DERIVED  # noqa: E402
from sweep.tests.test_public import (CODE, PDF, public_app,  # noqa: E402
                                     unlocked, upload)


def flat(body):
    return " ".join(html.unescape(body).split())


def strip(body):
    """The activity strip's text, or "" when it is not on the page."""
    if 'id="runbar"' not in body:
        return ""
    start = body.index('id="runbar"')
    return flat(body[start:body.index("</div>", start)])


class Parse:
    """A model call the test holds open, and a count of how many were made."""

    def __init__(self, fails=False):
        self.gate = threading.Event()
        self.calls = 0
        self.fails = fails
        self._lock = threading.Lock()

    def __call__(self, text, prefs):
        with self._lock:
            self.calls += 1
        self.gate.wait(timeout=10)
        if self.fails:
            raise RuntimeError("the model said no")
        return dict(DERIVED)

    def finish(self):
        self.gate.set()


def reading(fails=False):
    """A visitor whose résumé is being read right now."""
    model = Parse(fails=fails)
    app = public_app(derive=model)
    client = unlocked(app)
    upload(client)
    started = threading.Event()

    def go():
        started.set()
        client.post("/derive")

    worker = threading.Thread(target=go, daemon=True)
    worker.start()
    started.wait(timeout=5)
    # Wait for the model to actually be inside the call, so "in flight"
    # means in flight rather than "about to be".
    for _ in range(200):
        if model.calls:
            break
        threading.Event().wait(0.01)
    return app, client, model, worker


class TestTheStripSaysTheResumeIsBeingRead(unittest.TestCase):

    def test_A_leaving_the_page_does_not_lose_the_parse(self):
        app, client, model, worker = reading()
        try:
            for path in ("/", "/key"):
                with self.subTest(path=path):
                    said = strip(client.get(path, follow_redirects=True)
                                 .get_data(as_text=True))
                    self.assertIn("Reading your résumé", said)
                    self.assertIn("Sweep is building your job-search profile",
                                  said)
                    self.assertIn("View progress", said)
                    self.assertIn('href="/review"', said)
        finally:
            model.finish()
            worker.join(timeout=10)

    def test_B_a_refresh_mid_parse_does_not_start_a_second(self):
        """The whole reason the marker exists. Measured before the fix: two
        model calls, four GPU calls, and two of three daily slots."""
        app, client, model, worker = reading()
        try:
            # What a browser does on refresh: GET the screen...
            body = client.get("/review").get_data(as_text=True)
            self.assertIn("Reading your résumé", body)
            # ...and the screen must NOT carry the auto-submit that starts one.
            self.assertNotIn("requestSubmit", body)
            # And the POST itself, if it arrives anyway, is refused softly.
            again = client.post("/derive")
            self.assertEqual(again.status_code, 302)
            self.assertIn("/review", again.headers["Location"])
        finally:
            model.finish()
            worker.join(timeout=10)
        self.assertEqual(model.calls, 1, "the résumé was read twice")
        self.assertEqual(app.beta_limit.taken(), 1,
                         "a refresh spent a second daily slot")

    def test_B2_approving_mid_parse_does_not_start_a_second_either(self):
        """POST /review reaches derived_for_state() through the other door."""
        app, client, model, worker = reading()
        try:
            answer = client.post("/review", data={})
            self.assertEqual(answer.status_code, 302)
            self.assertIn("/review", answer.headers["Location"])
        finally:
            model.finish()
            worker.join(timeout=10)
        self.assertEqual(model.calls, 1)

    def test_C_a_returning_browser_finds_the_parse_still_open(self):
        """A second client carrying the same cookie is what a reopened tab
        is: the process still has the session room, and the parse writing
        into it is still going."""
        app, client, model, worker = reading()
        try:
            biscuit = next((c.value for c in client._cookies.values()
                            if c.key == "session"), None)
            self.assertTrue(biscuit)
            reopened = app.test_client()
            reopened.set_cookie("session", biscuit, domain="localhost")
            said = strip(reopened.get("/", follow_redirects=True)
                         .get_data(as_text=True))
            self.assertIn("Reading your résumé", said)
        finally:
            model.finish()
            worker.join(timeout=10)
        self.assertEqual(model.calls, 1)

    def test_D_it_becomes_ready_when_the_parse_lands(self):
        app, client, model, worker = reading()
        try:
            self.assertIn("Reading your résumé",
                          strip(client.get("/key").get_data(as_text=True)))
        finally:
            model.finish()
            worker.join(timeout=10)
        said = strip(client.get("/key").get_data(as_text=True))
        self.assertIn("Your profile is ready", said)
        self.assertIn("Check what Sweep understood before searching", said)
        self.assertIn("Review profile", said)
        self.assertIn('href="/review"', said)

    def test_E_ready_is_there_when_a_closed_tab_reopens(self):
        app, client, model, worker = reading()
        biscuit = next((c.value for c in client._cookies.values()
                        if c.key == "session"), None)
        model.finish()
        worker.join(timeout=10)

        reopened = app.test_client()
        reopened.set_cookie("session", biscuit, domain="localhost")
        said = strip(reopened.get("/", follow_redirects=True)
                     .get_data(as_text=True))
        self.assertIn("Your profile is ready", said)
        self.assertEqual(reopened.get("/review").status_code, 200)

    def test_F_a_failed_parse_says_so_and_offers_a_way_out(self):
        app, client, model, worker = reading(fails=True)
        model.finish()
        worker.join(timeout=10)

        said = strip(client.get("/", follow_redirects=True)
                     .get_data(as_text=True))
        self.assertIn("Sweep couldn't read your résumé", said)
        self.assertIn("Try again", said)
        self.assertIn('href="/"', said)
        # And the review screen does not silently retry it for ever.
        body = client.get("/review").get_data(as_text=True)
        self.assertNotIn("requestSubmit", body)
        self.assertIn("could not read", flat(body))

    def test_the_ready_strip_stands_down_on_the_profile_itself(self):
        app, client, model, worker = reading()
        model.finish()
        worker.join(timeout=10)
        self.assertIn("Your profile is ready",
                      strip(client.get("/key").get_data(as_text=True)))
        self.assertEqual(strip(client.get("/review").get_data(as_text=True)), "")


class TestNobodyElsesResume(unittest.TestCase):

    def test_G_a_stranger_sees_no_parse_activity(self):
        app, client, model, worker = reading()
        try:
            stranger = unlocked(app)
            body = stranger.get("/").get_data(as_text=True)
            # No strip. Asserted on the strip and not on the phrase: the
            # upload button has its own "Reading your résumé" sending
            # indicator, which is about the click the visitor is making and
            # has nothing to do with anybody's parse.
            self.assertEqual(strip(body), "")
            self.assertNotIn('id="runbar"', body)
            # And the endpoint the strip polls tells them nothing either.
            self.assertIsNone(stranger.get("/activity").get_json()["banner"])
        finally:
            model.finish()
            worker.join(timeout=10)

    def test_H_no_resume_text_path_or_content_reaches_the_page(self):
        app, client, model, worker = reading()
        try:
            body = client.get("/", follow_redirects=True).get_data(as_text=True)
            for leak in ("resume_text", "Ada Okonkwo", "sweep-", ".pdf\"",
                         "/tmp/", "parse", "Modal", "inference"):
                with self.subTest(leak=leak):
                    self.assertNotIn(leak, strip(body))
        finally:
            model.finish()
            worker.join(timeout=10)

    def test_H2_a_tampered_session_cannot_borrow_a_parse(self):
        """The room is keyed on the signed cookie's own sid, so there is
        nothing to plant — a stranger gets their own empty room."""
        app, client, model, worker = reading()
        try:
            thief = unlocked(app)
            with thief.session_transaction() as sess:
                sess["parse"] = {"state": "reading", "at": 0}
            self.assertEqual(strip(thief.get("/").get_data(as_text=True)), "")
            self.assertIsNone(thief.get("/activity").get_json()["banner"])
        finally:
            model.finish()
            worker.join(timeout=10)


class TestOneStripNotTwo(unittest.TestCase):

    def test_J_a_live_sweep_outranks_a_resume_being_read(self):
        self.assertEqual(
            pick_activity(run_banner("running"), parse_banner("reading")),
            run_banner("running"))
        self.assertEqual(
            pick_activity(run_banner("queued", 1), parse_banner("failed")),
            run_banner("queued", 1))

    def test_J2_a_finished_sweep_does_not_outrank_a_resume_being_read(self):
        """A visitor whose sweep finished and who has since uploaded a new
        résumé is having their résumé read. "Sweep complete" over that points
        at yesterday's news while the thing in progress goes unmentioned."""
        self.assertEqual(
            pick_activity(run_banner("finished"), parse_banner("reading")),
            parse_banner("reading"))

    def test_J3_between_two_finished_things_the_sweep_is_the_later_one(self):
        self.assertEqual(
            pick_activity(run_banner("finished"), parse_banner("ready")),
            run_banner("finished"))

    def test_J4_nothing_happening_is_no_strip(self):
        self.assertIsNone(pick_activity(None, None))

    def test_only_one_strip_is_ever_drawn(self):
        app, client, model, worker = reading()
        try:
            body = client.get("/", follow_redirects=True).get_data(as_text=True)
            self.assertEqual(body.count('id="runbar"'), 1)
        finally:
            model.finish()
            worker.join(timeout=10)

    def test_one_poll_loop_serves_both(self):
        """Two independent loops would be two opinions about what Sweep is
        doing. The strip polls /activity for either half."""
        app, client, model, worker = reading()
        try:
            body = client.get("/", follow_redirects=True).get_data(as_text=True)
            self.assertIn("/activity", body)
            self.assertNotIn('data-poll="/progress"', body)
        finally:
            model.finish()
            worker.join(timeout=10)

    def test_the_endpoint_answers_the_same_thing_the_page_was_served(self):
        app, client, model, worker = reading()
        try:
            said = strip(client.get("/key").get_data(as_text=True))
            polled = client.get("/activity?on=key").get_json()["banner"]
            self.assertIn(polled["headline"], said)
            self.assertIn(polled["cta"], said)
            self.assertEqual(polled["kind"], "profile")
        finally:
            model.finish()
            worker.join(timeout=10)


class TestNoInternalWordsEscape(unittest.TestCase):

    def test_the_profile_states_speak_english(self):
        for phase in ("reading", "ready", "failed"):
            said = parse_banner(phase)
            text = f"{said['headline']} {said['detail']} {said['cta']}".lower()
            for ours in ("modal", "inference", "parse", "worker", "run id",
                         "owner", "process", "capability", "gpu", "derive"):
                with self.subTest(phase=phase, word=ours):
                    self.assertNotIn(ours, text)


if __name__ == "__main__":
    unittest.main()
