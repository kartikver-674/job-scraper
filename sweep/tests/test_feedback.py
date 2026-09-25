"""Beta feedback: the header form, and the one email it sends.

Nothing here reaches a mail provider or a model. The sender is injected
(app.send_feedback) and records what it was handed; the Resend adapter is
tested against a fake urlopen. Every request runs in a SEALED environment
(clear=True) holding only fake values, so a failing assertion can print a
payload but never a real token from the developer's .env.
"""

import contextlib
import io
import json
import logging
import os
import re
import unittest
import urllib.error
from unittest import mock

from sweep import app as app_module
from sweep import feedback, public
from sweep.tests.test_public import PDF, public_app, unlocked
from sweep.tests.test_public_sweep import (give_rows, only_run, render_app,
                                           stack, sweeping)

OPERATOR = "operator@example.test"
SENDER = "Sweep Feedback <feedback@sweep.example>"
API_KEY = "re_FAKE_provider_key_0001"
ENV = {feedback.TO_ENV: OPERATOR, feedback.FROM_ENV: SENDER,
       feedback.KEY_ENV: API_KEY}
USER = "visitor@example.org"
VALID = {"email": USER, "type": "bug", "subject": "Duplicate jobs",
         "feedback": "I saw the same role several times in my results.",
         "website": "", "page": "/results"}


class Recorder:
    """Stands in for the provider: keeps what it was asked to send."""

    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self, message, api_key):
        if self.fail:
            raise self.fail
        self.sent.append((message, api_key))


def ready(sender=None, **kw):
    """A public app past the beta door, with a recording sender."""
    app = public_app(**kw)
    app.send_feedback = sender or Recorder()
    return app, unlocked(app)


def post(client, env=ENV, ip="203.0.113.7", **fields):
    with mock.patch.dict(os.environ, env, clear=True):
        return client.post("/feedback", json={**VALID, **fields},
                           environ_base={"REMOTE_ADDR": ip})


def header_of(page):
    return page[page.index("<header"):page.index("</header>")]


@contextlib.contextmanager
def every_log_line():
    """Every record any logger emits, formatted, while the block runs."""
    lines = []

    class Keep(logging.Handler):
        def emit(self, record):
            lines.append(self.format(record))

    root, handler = logging.getLogger(), Keep(logging.DEBUG)
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


class TestTheControl(unittest.TestCase):
    """One button in the shared header, one dialog, on every public screen."""

    def test_it_is_in_the_public_shared_header(self):
        _app, client = ready()
        page = client.get("/").get_data(as_text=True)
        head = header_of(page)
        self.assertEqual(head.count("data-feedback-open"), 1)
        self.assertIn('aria-haspopup="dialog"', head)
        self.assertIn(">Feedback<", head)
        self.assertEqual(page.count('<dialog id="feedback"'), 1)

    def test_it_is_on_every_screen_of_the_public_flow(self):
        with stack() as (url, store):
            app = render_app(url)
            client = sweeping(app, store)
            give_rows(store, only_run(store))
            for path in ("/", "/review", "/key", "/configure", "/confirm",
                         "/running", "/results"):
                with self.subTest(path=path):
                    r = client.get(path)
                    self.assertEqual(r.status_code, 200)
                    page = r.get_data(as_text=True)
                    self.assertEqual(header_of(page).count("data-feedback-open"), 1)
                    self.assertEqual(page.count('<dialog id="feedback"'), 1)

    def test_the_operator_console_does_not_grow_one(self):
        app = app_module.create_app(derive=lambda t, p: {})
        page = app.test_client().get("/").get_data(as_text=True)
        self.assertNotIn("data-feedback-open", page)
        self.assertNotIn('<dialog id="feedback"', page)


class TestTheModal(unittest.TestCase):

    def setUp(self):
        _app, client = ready()
        page = client.get("/").get_data(as_text=True)
        self.dialog = page[page.index('<dialog id="feedback"'):page.index("</dialog>")]
        self.page = page

    def test_it_renders_the_agreed_copy(self):
        for text in ("Share feedback",
                     "Help make Sweep better. Bug reports, suggestions and ideas are all welcome.",
                     "Your email", "What is this about?", "Subject", "Your feedback",
                     'placeholder="you@example.com"', 'placeholder="Short summary"',
                     "Tell me what happened or what you'd like to see...",
                     ">Bug<", ">Search results<", ">Feature request<",
                     ">UI / usability<", ">Other<", "Cancel", "Send feedback",
                     "Thanks — feedback sent.", "I read every message during the beta."):
            with self.subTest(text=text):
                self.assertIn(text, self.dialog)

    def test_its_options_are_exactly_the_servers_types(self):
        values = re.findall(r'<option value="(\w+)"', self.dialog)
        self.assertEqual(values, list(feedback.TYPES))

    def test_it_is_labelled_and_its_fields_are_constrained(self):
        self.assertIn('aria-labelledby="feedback-title"', self.dialog)
        self.assertIn('id="feedback-title"', self.dialog)
        self.assertRegex(self.dialog, r'type="email" name="email" required maxlength="254"')
        self.assertIn('name="subject" maxlength="150"', self.dialog)
        self.assertIn('minlength="10" maxlength="5000"', self.dialog)
        self.assertIn('role="alert"', self.dialog)
        # Five fields, five <label>s wrapping them: every one has a name.
        self.assertEqual(len(re.findall(r"<(?:input|select|textarea)\b", self.dialog)), 5)
        self.assertEqual(self.dialog.count("<label"), 5)

    def test_it_asks_for_nothing_sensitive(self):
        names = set(re.findall(r'name="(\w+)"', self.dialog))
        self.assertEqual(names, {"email", "type", "subject", "feedback", "website"})

    def test_the_honeypot_is_off_screen_and_out_of_the_tab_order(self):
        self.assertRegex(self.dialog,
                         r'class="feedback-hp" aria-hidden="true">\s*<label>[^<]*'
                         r'<input type="text" name="website" tabindex="-1"')

    def test_the_script_blocks_a_second_submit_while_one_is_in_flight(self):
        script = self.page[self.page.index('getElementById("feedback")'):]
        self.assertIn("if (sending) return;", script)
        self.assertIn("send.disabled = true", script)
        self.assertIn('send.textContent = "Sending…"', script)
        # Cleared only on success, never on close or failure.
        self.assertEqual(script.count("form.reset()"), 1)
        success = script[script.index("form.reset()") - 200:script.index("form.reset()")]
        self.assertIn(".then(function () {", success)

    def test_the_label_hides_on_a_phone_but_keeps_its_name(self):
        with open(os.path.join(app_module.REPO_ROOT, "sweep", "static",
                               "sweep.css"), encoding="utf-8") as fh:
            css = fh.read()
        rule = css.index(".feedback-label {")
        self.assertTrue(css[:rule].rstrip().rsplit("@media", 1)[1]
                        .lstrip().startswith("(max-width: 40rem)"))
        narrow = css[rule:css.index("\n}\n", rule)]
        self.assertIn("clip: rect(0 0 0 0)", narrow)
        self.assertNotIn("display: none", narrow)


class TestDelivery(unittest.TestCase):

    def test_valid_feedback_is_sent_and_says_so(self):
        app, client = ready()
        r = post(client)
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        self.assertEqual(len(app.send_feedback.sent), 1)

    def test_it_goes_to_the_operator_from_sweep_and_replies_to_the_visitor(self):
        app, client = ready()
        post(client)
        message, key = app.send_feedback.sent[0]
        self.assertEqual(message["to"], OPERATOR)
        self.assertEqual(message["reply_to"], USER)
        self.assertEqual(message["from"], SENDER)
        self.assertNotIn(USER, message["from"])
        self.assertEqual(key, API_KEY)

    def test_the_subject_names_the_type_and_the_summary(self):
        app, client = ready()
        post(client)
        post(client, subject="  ")
        subjects = [m["subject"] for m, _ in app.send_feedback.sent]
        self.assertEqual(subjects, ["[Sweep Feedback] Bug — Duplicate jobs",
                                    "[Sweep Feedback] Bug"])

    def test_the_body_reads_like_the_agreed_email(self):
        app, client = ready()
        post(client)
        text = app.send_feedback.sent[0][0]["text"]
        for block in ("Sweep feedback", "Type:\nBug", f"User email:\n{USER}",
                      "Subject:\nDuplicate jobs",
                      f"Feedback:\n{VALID['feedback']}",
                      "-------------------------", "Page:\n/results",
                      "Stage:\nJobs", "Sweep mode:\n", "Run ID:\n",
                      "Browser:\n", "Submitted:\n"):
            with self.subTest(block=block):
                self.assertIn(block, text)

    def test_html_in_the_feedback_is_escaped_in_the_html_email(self):
        app, client = ready()
        post(client, subject="<b>bold</b>",
             feedback='<script>alert("x")</script> & <img src=x onerror=alert(1)>')
        html_body = app.send_feedback.sent[0][0]["html"]
        self.assertNotIn("<script", html_body)
        self.assertNotIn("<img", html_body)
        self.assertNotIn("<b>bold", html_body)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; ", html_body)

    def test_the_resend_adapter_posts_the_documented_request(self):
        seen = {}

        class Reply:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(req, timeout):
            seen.update(url=req.full_url, method=req.get_method(),
                        headers=dict(req.header_items()),
                        body=json.loads(req.data), timeout=timeout)
            return Reply()

        message = {"to": OPERATOR, "from": SENDER, "reply_to": USER,
                   "subject": "s", "text": "t", "html": "<p>h</p>"}
        feedback.send_with_resend(message, API_KEY, urlopen=urlopen)
        self.assertEqual(seen["url"], "https://api.resend.com/emails")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["headers"]["Authorization"], f"Bearer {API_KEY}")
        self.assertEqual(seen["headers"]["Content-type"], "application/json")
        self.assertEqual(seen["body"], message)
        self.assertEqual(seen["timeout"], 10)

    def test_a_provider_refusal_becomes_a_status_and_its_body_is_closed(self):
        body = io.BytesIO(f'{{"message": "invalid reply_to {USER}"}}'.encode())

        def urlopen(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 422, "Unprocessable",
                                         {}, body)

        with self.assertRaises(feedback.DeliveryFailed) as caught:
            feedback.send_with_resend({}, API_KEY, urlopen=urlopen)
        self.assertEqual(str(caught.exception), "HTTP 422")
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertTrue(body.closed)

    def test_the_status_is_what_gets_logged(self):
        _app, client = ready(sender=Recorder(fail=feedback.DeliveryFailed("HTTP 422")))
        with every_log_line() as lines:
            post(client)
        self.assertIn("feedback delivery failed: HTTP 422", "\n".join(lines))


class TestValidation(unittest.TestCase):
    """Server-side, authoritative, and nothing is sent when it refuses."""

    def refused(self, **fields):
        app, client = ready()
        r = post(client, **fields)
        self.assertEqual(r.status_code, 400, r.get_json())
        self.assertEqual(app.send_feedback.sent, [])
        return r.get_json()["error"]

    def test_a_missing_email_is_refused(self):
        self.assertIn("email", self.refused(email=""))

    def test_an_invalid_email_is_refused(self):
        for bad in ("not-an-email", "a@b", "a b@c.de", "<a@b.co>", "a@b.co, c@d.ef",
                    "x" * 250 + "@b.co", 42):
            with self.subTest(bad=bad):
                self.refused(email=bad)

    def test_an_unknown_type_is_refused(self):
        for bad in ("", "Bug", "spam", None):
            with self.subTest(bad=bad):
                self.refused(type=bad)

    def test_empty_feedback_is_refused(self):
        for bad in ("", "   \n\t ", "too short"):
            with self.subTest(bad=bad):
                self.refused(feedback=bad)

    def test_overlong_feedback_is_refused_and_the_limit_itself_is_not(self):
        self.refused(feedback="x" * 5001)
        app, client = ready()
        self.assertEqual(post(client, feedback="x" * 5000).status_code, 200)

    def test_an_overlong_subject_is_refused_and_the_limit_itself_is_not(self):
        self.refused(subject="s" * 151)
        app, client = ready()
        self.assertEqual(post(client, subject="s" * 150).status_code, 200)

    def test_a_header_injection_in_the_email_is_refused(self):
        for bad in ("a@b.co\r\nBcc: evil@x.example", "a@b.co\nCc: evil@x.example",
                    "a@b.co\x00"):
            with self.subTest(bad=bad):
                self.refused(email=bad)

    def test_a_header_injection_in_the_subject_is_flattened(self):
        app, client = ready()
        post(client, subject="Hi\r\nBcc: evil@x.example\u2028X-Evil: 1")
        subject = app.send_feedback.sent[0][0]["subject"]
        self.assertNotRegex(subject, r"[\r\n\u2028\x00]")
        self.assertEqual(subject, "[Sweep Feedback] Bug — Hi Bcc: evil@x.example X-Evil: 1")

    def test_a_body_that_is_not_json_is_refused(self):
        app, client = ready()
        with mock.patch.dict(os.environ, ENV, clear=True):
            r = client.post("/feedback", data=VALID)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(app.send_feedback.sent, [])

    def test_an_oversized_request_is_refused_before_it_is_read(self):
        app, client = ready()
        r = post(client, feedback="x" * (feedback.MAX_BODY_BYTES + 1))
        self.assertEqual(r.status_code, 413)
        self.assertEqual(app.send_feedback.sent, [])


class TestSpam(unittest.TestCase):

    def test_a_filled_honeypot_is_thanked_and_sends_nothing(self):
        app, client = ready()
        r = post(client, website="https://spam.example")
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        self.assertEqual(app.send_feedback.sent, [])
        self.assertEqual(app.feedback_limit.taken(), 0)

    def test_the_rate_limit_stops_the_sixth_message_in_an_hour(self):
        app, client = ready()
        codes = [post(client).status_code for _ in range(6)]
        self.assertEqual(codes, [200] * 5 + [429])
        self.assertEqual(len(app.send_feedback.sent), 5)
        self.assertEqual(post(client).get_json()["error"], feedback.LIMITED)
        # Per visitor: someone else still gets through.
        self.assertEqual(post(client, ip="198.51.100.9").status_code, 200)

    def test_a_failed_delivery_gives_the_slot_back(self):
        app, client = ready(sender=Recorder(fail=OSError("down")))
        for _ in range(7):
            self.assertEqual(post(client).status_code, 502)
        self.assertEqual(app.feedback_limit.taken(), 0)

    def test_the_beta_door_and_the_origin_check_stand_in_front(self):
        app = public_app()
        app.send_feedback = Recorder()
        stranger = app.test_client()
        r = post(stranger)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/beta", r.headers["Location"])
        client = unlocked(app)
        with mock.patch.dict(os.environ, ENV, clear=True):
            r = client.post("/feedback", json=VALID,
                            headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(app.send_feedback.sent, [])


class TestFailure(unittest.TestCase):
    """The visitor learns it did not go; never why, in the provider's words."""

    LEAKY = RuntimeError(f"resend: key {API_KEY} rejected sending to {USER} "
                         f"from {SENDER} Traceback")

    def test_a_provider_failure_is_a_safe_message(self):
        for exc in (self.LEAKY, OSError("timed out"),
                    feedback.DeliveryFailed("HTTP 422")):
            with self.subTest(exc=type(exc).__name__):
                _app, client = ready(sender=Recorder(fail=exc))
                r = post(client)
                self.assertEqual(r.status_code, 502)
                self.assertEqual(r.get_json(), {"error": feedback.FAILED})

    def test_the_providers_exception_never_reaches_the_browser(self):
        _app, client = ready(sender=Recorder(fail=self.LEAKY))
        body = post(client).get_data(as_text=True)
        for secret in (API_KEY, "resend", "Traceback", SENDER, OPERATOR):
            self.assertNotIn(secret, body)

    def test_missing_configuration_is_temporarily_unavailable(self):
        for missing in ENV:
            with self.subTest(missing=missing):
                app, client = ready()
                env = {k: v for k, v in ENV.items() if k != missing}
                with every_log_line() as lines:
                    r = post(client, env=env)
                self.assertEqual(r.status_code, 503)
                self.assertEqual(r.get_json(), {"error": feedback.UNAVAILABLE})
                for name in ENV:
                    self.assertNotIn(name, r.get_data(as_text=True))
                self.assertTrue(any(missing in line for line in lines), lines)
                self.assertEqual(app.send_feedback.sent, [])


class TestPrivacy(unittest.TestCase):

    def test_nothing_the_visitor_wrote_reaches_a_log(self):
        typed = {"email": "private.person@example.org",
                 "subject": "PRIVATE-SUBJECT-4417",
                 "feedback": "PRIVATE-FEEDBACK-BODY-9921 with details"}
        with every_log_line() as lines:
            _app, client = ready()
            self.assertEqual(post(client, **typed).status_code, 200)
            _app, failing = ready(sender=Recorder(fail=TestFailure.LEAKY))
            self.assertEqual(post(failing, **typed).status_code, 502)
            _app, unconfigured = ready()
            post(unconfigured, env={}, **typed)
        self.assertTrue(any("feedback delivery failed: RuntimeError" in line
                            for line in lines), lines)
        joined = "\n".join(lines)
        for value in (*typed.values(), USER, API_KEY, SENDER, OPERATOR):
            self.assertNotIn(value, joined)

    def test_no_resume_profile_query_or_token_is_attached(self):
        resume = "RESUME-SENTINEL Ada Okonkwo, 12 Private Road"
        app, client = ready(extract=lambda path: resume)
        client.post("/resume", data={"resume": (io.BytesIO(PDF), "SECRET-FILENAME.pdf")},
                    content_type="multipart/form-data")
        client.post("/derive")
        for room in app.session_store._rooms.values():
            room["data"].update(
                locations=["QUERY-SENTINEL-CITY"], avoid=["QUERY-SENTINEL-AVOID"],
                byok_keys=[{"id": "ACCOUNT-SENTINEL", "credit_usd": 5.0}],
                profile="PROFILE-SENTINEL")
        env = {**ENV, "APIFY_TOKEN": "apify_api_TOKEN_SENTINEL",
               "APIFY_TOKEN_2": "apify_api_TOKEN2_SENTINEL",
               public.SECRET_ENV: "SECRET-KEY-SENTINEL",
               "SWEEP_WORKER_TOKEN": "WORKER-TOKEN-SENTINEL",
               "SWEEP_INFERENCE_TOKEN": "INFERENCE-TOKEN-SENTINEL"}
        self.assertEqual(post(client, env=env).status_code, 200)
        message = app.send_feedback.sent[0][0]
        self.assertEqual(set(message), {"to", "from", "reply_to", "subject",
                                        "text", "html"})
        payload = json.dumps(message)
        for sentinel in ("RESUME-SENTINEL", "Private Road", "SECRET-FILENAME",
                         "QUERY-SENTINEL", "ACCOUNT-SENTINEL", "PROFILE-SENTINEL",
                         "TOKEN_SENTINEL", "TOKEN2_SENTINEL", "SECRET-KEY-SENTINEL",
                         "WORKER-TOKEN-SENTINEL", "INFERENCE-TOKEN-SENTINEL",
                         API_KEY, "field_summary"):
            with self.subTest(sentinel=sentinel):
                self.assertNotIn(sentinel, payload)

    def test_safe_page_and_stage_context_is_attached(self):
        app, client = ready()
        with client.session_transaction() as s:
            s["run_id"] = "run_7f3a9c"
        env = {**ENV, "RENDER_GIT_COMMIT": "314cb1e0123456789abcdef"}
        for page, stage in (("/", "Résumé"), ("/review", "Profile"),
                            ("/key", "Search"), ("/running", "Search"),
                            ("/results", "Jobs")):
            with mock.patch.dict(os.environ, env, clear=True):
                client.post("/feedback", json={**VALID, "page": page},
                            headers={"User-Agent": "TestBrowser/1.0\tX-Evil: 1"})
            text = app.send_feedback.sent[-1][0]["text"]
            with self.subTest(page=page):
                self.assertIn(f"Page:\n{page}\n", text)
                self.assertIn(f"Stage:\n{stage}\n", text)
                self.assertIn("Run ID:\nrun_7f3a9c\n", text)
                self.assertIn("Browser:\nTestBrowser/1.0 X-Evil: 1\n", text)
                self.assertIn("Version:\n314cb1e01234\n", text)
                self.assertIn("Sweep mode:\nNot chosen yet\n", text)
                self.assertRegex(text, r"Submitted:\n\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00")

    def test_a_page_that_is_not_a_path_is_not_repeated(self):
        app, client = ready()
        post(client, page="javascript:alert(document.cookie)")
        post(client, page="/results?q=private+search")
        for message, _ in app.send_feedback.sent:
            self.assertIn("Page:\nunknown\n", message["text"])
            self.assertNotIn("private", message["text"])

    def test_the_free_choice_is_reported_as_the_mode(self):
        with stack() as (url, store):
            app = render_app(url)
            app.send_feedback = Recorder()
            client = sweeping(app, store)
            with mock.patch.dict(os.environ, ENV, clear=True):
                client.post("/feedback", json={**VALID, "page": "/running"})
            text = app.send_feedback.sent[0][0]["text"]
            self.assertIn("Sweep mode:\nFree\n", text)
            self.assertIn(f"Run ID:\n{only_run(store)}\n", text)
            self.assertIn("Stage:\nSearch\n", text)


if __name__ == "__main__":
    unittest.main()
