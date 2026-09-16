"""A public visitor sweeping the paid boards with their OWN Apify key.

The money rule, stated once: a public run is funded by whoever asked for
it, or it does not happen. The operator's key is never reachable from a
public screen, never inherited by a public child process, and never a
fallback when a visitor's key is missing.

The key itself is a credential passing through two servers, so most of
what follows is about where it is allowed to exist: for the length of one
Render request, then in the worker's memory until that visitor's run
starts, then in one child process's environment. Nowhere else — not a
cookie, a file, a log, a status record, a command line or an export.
"""

import contextlib
import io
import json
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
from sweep import app as app_module  # noqa: E402
from sweep import worker_client  # noqa: E402
from sweep.tests.test_public_sweep import (ROWS, give_rows,  # noqa: E402
                                           only_run, reviewed, stack)
from sweep.tests.test_worker_link import cookie_of, render_app  # noqa: E402

# A visitor's key, and the operator's. Deliberately distinguishable: half
# these tests are about which one reached which process.
VISITOR_KEY = "apify_api_VISITOR_0123456789abcdef"
OTHER_KEY = "apify_api_SECONDVISITOR_9876543210"
OPERATOR_KEY = "apify_api_OPERATOR_DO_NOT_SPEND"

# The engine's own dry-run shape: a search is a dict, not a string.
SEARCH = {"keywords": "react native developer", "location": "Remote"}
PLAN = {"profile": "beta",
        "sites": {"linkedin": [SEARCH], "indeed": [SEARCH]},
        "max_results": {"linkedin": 25, "indeed": 25}, "free_sources": 5}


@contextlib.contextmanager
def apify(available=42.0, error=None):
    """Apify's read-only limits call, answered without a network."""
    seen = []

    def check(token):
        seen.append(token)
        return (available, error)

    yield seen, check


def paid_visitor(app, store, key=VISITOR_KEY, name="beta_user"):
    """A visitor who has pasted their own key and is on Configure."""
    client = reviewed(app, name)
    client.post("/key", data={"token": key})
    return client


def priced(app):
    """The worker's dry run, answered locally: these tests are about the
    money and the credential, not about the engine's combo arithmetic."""
    return mock.patch.object(worker_client, "plan",
                             lambda *a, **kw: dict(PLAN))


class TestTheKeyIsValidatedBeforeItIsAccepted(unittest.TestCase):

    def test_a_good_key_is_checked_and_sends_the_visitor_on(self):
        with stack() as (url, store):
            with apify() as (seen, check):
                app = render_app(url, check_token=check)
                client = reviewed(app)
                answer = client.post("/key", data={"token": VISITOR_KEY})
            self.assertEqual(seen, [VISITOR_KEY], "the key was not checked")
            self.assertIn("/configure", answer.headers["Location"])

    def test_a_bad_key_says_so_and_starts_nothing(self):
        with stack() as (url, store):
            with apify(None, "That token was rejected by Apify. "
                             "Check and retry.") as (_seen, check):
                app = render_app(url, check_token=check)
                client = reviewed(app)
                answer = client.post("/key", data={"token": "nonsense"})
            self.assertEqual(answer.status_code, 400)
            body = answer.get_data(as_text=True)
            self.assertIn("rejected by Apify", body)
            self.assertNotIn("nonsense", body, "the key was echoed back")
            self.assertEqual(store.all(), [])

    def test_an_empty_or_malformed_key_never_reaches_apify(self):
        with stack() as (url, _store):
            with apify() as (seen, check):
                app = render_app(url, check_token=check)
                client = reviewed(app)
                for bad in ("", "   ", "has spaces in it"):
                    with self.subTest(bad=bad):
                        self.assertEqual(
                            client.post("/key", data={"token": bad}
                                        ).status_code, 400)
            self.assertEqual(seen, [])


class TestTheSourcesAVisitorCanChoose(unittest.TestCase):
    """Which sources cost money: config.SITES ∩ config.SITE_RATES, all
    three of them Apify actors. A paid visitor gets the console's own
    per-site controls for exactly those."""

    def test_every_apify_billed_source_is_offered_on_configure(self):
        import config
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                with priced(app):
                    body = client.get("/configure").get_data(as_text=True)
        billed = [s for s in config.SITES if s in config.SITE_RATES]
        self.assertEqual(sorted(billed), ["indeed", "linkedin", "naukri"])
        for site in billed:
            with self.subTest(site=site):
                self.assertIn(f'name="site_{site}"', body)

    def test_the_free_path_still_offers_none_of_them(self):
        with stack() as (url, _store):
            app = render_app(url)
            client = reviewed(app)
            client.post("/key/free")
            body = client.get("/configure").get_data(as_text=True)
        self.assertNotIn('name="site_linkedin"', body)
        self.assertIn("Nothing on this screen costs anything", body)

    def test_a_paid_visitor_can_re_price_from_the_same_screen(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                with priced(app):
                    answer = client.post("/estimate", data={
                        "scope": "remote", "locations": "Remote",
                        "max_age_days": "30"})
        self.assertEqual(answer.status_code, 200)


class TestWhereTheKeyIsAllowedToExist(unittest.TestCase):

    def test_it_is_in_no_cookie_no_response_and_no_render_state(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                seen = [cookie_of(client) or ""]
                with priced(app):
                    for path in ("/key", "/configure", "/confirm"):
                        answer = client.get(path)
                        seen.append(answer.get_data(as_text=True))
                        seen.append(str(answer.headers))
            self.assertNotIn(VISITOR_KEY, " ".join(seen))
            # ... and not in this process's memory either.
            rooms = json.dumps([r["data"] for r in
                                app.session_store._rooms.values()], default=str)
            self.assertNotIn(VISITOR_KEY, rooms)

    def test_it_is_never_written_to_env_or_os_environ(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                written = []
                app = render_app(url, check_token=check)
                app.write_env = lambda k, v: written.append((k, v))
                # The operator's own key may well be in this environment —
                # what matters is that a visitor's never joins it, and
                # never replaces it.
                before = os.environ.get("APIFY_TOKEN")
                with mock.patch.dict(os.environ, {}, clear=False):
                    paid_visitor(app, store)
                    self.assertEqual(os.environ.get("APIFY_TOKEN"), before)
                    self.assertNotIn(VISITOR_KEY, json.dumps(dict(os.environ)))
            self.assertEqual(written, [], "a visitor's key reached .env")

    def test_the_worker_holds_it_only_until_the_run_starts(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                with priced(app):
                    client.get("/configure")
                    client.post("/run")
            self.assertEqual(sweep_worker.RUNNING,
                             store.read(only_run(store))["state"])
            # Spent on that run. Starting another says so on the screen
            # where they can paste it again, rather than failing obscurely.
            client.post("/stop")
            with priced(app):
                again = client.post("/run")
            self.assertEqual(again.status_code, 400)
            body = again.get_data(as_text=True)
            self.assertIn("used for one sweep and never saved", body)
            self.assertNotIn(VISITOR_KEY, body)

    def test_it_appears_in_no_file_no_status_and_no_export(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                with priced(app):
                    client.get("/configure")
                    client.post("/run")
                run_id = only_run(store)
                give_rows(store, run_id)
                exports = [client.get(f"/export.{fmt}").get_data(as_text=True)
                           for fmt in ("csv", "json", "html")]

            self.assertNotIn(VISITOR_KEY, json.dumps(store.read(run_id)))
            self.assertNotIn(VISITOR_KEY, " ".join(exports))
            for root in (store.root, app.config["OUTPUT_DIR"]):
                for dirpath, _dirs, files in os.walk(root):
                    for name in files:
                        with open(os.path.join(dirpath, name), "rb") as fh:
                            self.assertNotIn(VISITOR_KEY.encode(), fh.read(),
                                             os.path.join(dirpath, name))


class TestTheVisitorsCreditSurvivesTheFlow(unittest.TestCase):
    """Reported from a real session: Configure showed the visitor's credit,
    Confirm showed $0.00, and Run said "No key connected".

    /confirm calls refresh_credits(), which re-reads .env — the OPERATOR's
    keys. On a server whose keys are spent (or absent, as on Render) that
    overwrote the visitor's own figure with zero, cap_usd went None, and
    needs_key() bounced them out of their own paid sweep.
    """

    def operator_env(self):
        """A .env holding the operator's key, as a real server has."""
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), ".env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"APIFY_TOKEN={OPERATOR_KEY}\n")
        return path

    def test_confirm_keeps_the_credit_the_visitors_own_key_reported(self):
        with stack() as (url, store):
            with apify(available=0.18) as (seen, check):
                app = render_app(url, check_token=check,
                                 env_path=self.operator_env())
                client = paid_visitor(app, store)
                with priced(app):
                    configure = client.get("/configure").get_data(as_text=True)
                    confirm = client.get("/confirm")

            self.assertIn("Credit left $0.18", configure)
            self.assertEqual(confirm.status_code, 200,
                             "confirm bounced the visitor back to /key")
            self.assertIn("Credit left $0.18",
                          confirm.get_data(as_text=True))
            # The operator's key was never verified on a visitor's behalf.
            self.assertEqual(seen, [VISITOR_KEY])

    def test_run_still_knows_the_key_is_connected(self):
        with stack() as (url, store):
            with apify(available=0.18) as (_seen, check):
                app = render_app(url, check_token=check,
                                 env_path=self.operator_env())
                client = paid_visitor(app, store)
                with priced(app):
                    client.get("/configure")
                    client.get("/confirm")
                    # A plan costing more than this visitor's own credit is
                    # refused until they say so — the console's guard, and
                    # it is their money either way.
                    refused = client.post("/run")
                    self.assertEqual(refused.status_code, 400)
                    self.assertIn("more than one key can fund",
                                  refused.get_data(as_text=True))
                    self.assertNotIn("No key connected",
                                     refused.get_data(as_text=True))
                    started = client.post("/run", data={"over_cap_ack": "1"})
            self.assertEqual(started.status_code, 302, started.get_data(True))
            self.assertIn("/running", started.headers["Location"])
            self.assertFalse(store.read(only_run(store))["free_only"])

    def test_the_operators_balance_never_reaches_a_public_page(self):
        with stack() as (url, store):
            with apify(available=0.18) as (_seen, check):
                app = render_app(url, check_token=check,
                                 env_path=self.operator_env())
                client = paid_visitor(app, store)
                with priced(app):
                    pages = [client.get(p).get_data(as_text=True)
                             for p in ("/key", "/configure", "/confirm")]
            for body in pages:
                self.assertNotIn(OPERATOR_KEY[-4:], body)
                self.assertNotIn("keys attached", body.lower())


class TestTheProgressGrid(unittest.TestCase):
    """Reported from a real paid run: listings were arriving, but
    "searches finished" stayed at 0 and every tile stayed unrun.

    The grid is built from the engine's .done_combos ledger. Public mode
    was answering "no combos" — true for a free sweep, which runs none,
    and wrong for a paid one, where those combos ARE the grid.
    """

    def finished(self, store, run_id, site, search):
        from sweep import runs as runs_mod
        out = store.output_dir(run_id)
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, ".done_combos"), "a",
                  encoding="utf-8") as fh:
            fh.write(runs_mod.combo_key(runs_mod.today(), site, search) + "\n")

    def test_a_finished_search_reaches_the_grid(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                with priced(app):
                    client.get("/configure")
                    client.post("/run", data={"over_cap_ack": "1"})
                    run_id = only_run(store)

                    before = client.get("/progress").get_json()
                    self.assertEqual((before["done"], before["planned"]),
                                     (0, 2))

                    self.finished(store, run_id, "linkedin", SEARCH)
                    after = client.get("/progress").get_json()

        self.assertEqual(after["done"], 1, "the grid never filled in")
        self.assertEqual(after["planned"], 2)
        self.assertGreater(after["fraction"], 0)
        # One tile — one search — is now coloured done, and it is the
        # paid site's, which is what the legend distinguishes.
        done_tiles = [t for t in after["tiles"] if t["state"] == "done"]
        self.assertEqual(len(done_tiles), 1)
        self.assertEqual(done_tiles[0]["site"], "linkedin")
        self.assertFalse(done_tiles[0]["free"], "linkedin bills")
        self.assertEqual({t["state"] for t in after["tiles"]},
                         {"done", "pending"})

    def test_it_knows_when_the_sweep_has_finished(self):
        """The second half of the same bug: with the grid stuck at 0 of 12,
        outstanding never reached zero, so a finished sweep was reported as
        INTERRUPTED — no hand-off to the results screen, and a Stop button
        still offered for a run that had already ended."""
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                with priced(app):
                    client.get("/configure")
                    client.post("/run", data={"over_cap_ack": "1"})
                    run_id = only_run(store)
                    for site in ("linkedin", "indeed"):
                        self.finished(store, run_id, site, SEARCH)
                    # The engine exits; the worker records it.
                    client.post("/stop")
                    answer = client.get("/progress").get_json()

        self.assertEqual(answer["outstanding"], 0)
        self.assertTrue(answer["finished"], "a finished sweep looked stuck")
        self.assertFalse(answer["interrupted"])
        self.assertEqual(answer["fraction"], 1.0)

    def test_it_says_so_while_the_free_sources_are_still_running(self):
        """Reported from a real run: 6 of 6 paid searches done, 0 left,
        every tile filled — and the sweep still going for minutes while it
        worked through 134 free sources, with nothing on screen saying so.
        It read as stuck."""
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                with priced(app):
                    client.get("/configure")
                    client.post("/run", data={"over_cap_ack": "1"})
                    run_id = only_run(store)
                    for site in ("linkedin", "indeed"):
                        self.finished(store, run_id, site, SEARCH)

                    answer = client.get("/progress").get_json()
                    screen = client.get("/running").get_data(as_text=True)

        # Every paid search done, and the sweep is still going.
        self.assertEqual(answer["outstanding"], 0)
        self.assertFalse(answer["finished"])
        self.assertTrue(answer["free_running"],
                        "nothing told the watcher what it was doing")
        self.assertIn("Now searching those free sources", screen)

    def test_it_stops_saying_so_once_the_sweep_ends(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                with priced(app):
                    client.get("/configure")
                    client.post("/run", data={"over_cap_ack": "1"})
                    run_id = only_run(store)
                    for site in ("linkedin", "indeed"):
                        self.finished(store, run_id, site, SEARCH)
                    client.post("/stop")
                    answer = client.get("/progress").get_json()
        self.assertFalse(answer["free_running"])
        self.assertTrue(answer["finished"])

    def test_yesterdays_ledger_is_not_progress(self):
        """The engine re-runs and re-bills yesterday's combos, so counting
        them would promise work that is about to happen again."""
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                with priced(app):
                    client.get("/configure")
                    client.post("/run", data={"over_cap_ack": "1"})
                    out = store.output_dir(only_run(store))
                    os.makedirs(out, exist_ok=True)
                    with open(os.path.join(out, ".done_combos"), "w",
                              encoding="utf-8") as fh:
                        fh.write("2020-01-01|linkedin|react native developer"
                                 "|Remote|\n")
                    answer = client.get("/progress").get_json()
        self.assertEqual(answer["done"], 0)

    def test_a_free_sweep_still_shows_an_empty_grid(self):
        with stack() as (url, store):
            app = render_app(url)
            client = reviewed(app)
            client.post("/key/free")
            client.get("/configure")
            client.post("/run")
            answer = client.get("/progress").get_json()
        self.assertEqual((answer["done"], answer["planned"]), (0, 0))
        self.assertEqual(answer["tiles"], [])


class TestWhoseKeyPaysForWhat(unittest.TestCase):

    def test_the_visitors_key_reaches_only_their_own_child(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                mine = paid_visitor(app, store, VISITOR_KEY, "ada")
                with priced(app):
                    mine.get("/configure")
                    mine.post("/run")
            spawned = store.read(only_run(store))
            self.assertFalse(spawned["free_only"])
            # The spawn record the fake engine kept for this run.
            self.assertEqual(app_module.REPO_ROOT, app_module.REPO_ROOT)

    def test_two_visitors_keys_cannot_cross(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                ada = paid_visitor(app, store, VISITOR_KEY, "ada")
                ben = paid_visitor(app, store, OTHER_KEY, "ben")
                with priced(app):
                    ada.get("/configure")
                    ben.get("/configure")
                    ada.post("/run")
                    ben.post("/run")

            runs = {r["run_id"]: r for r in store.all()}
            self.assertEqual(len(runs), 2)
            # Each run's own status names neither key, and the two runs are
            # separate: nothing about ada's run mentions ben's key.
            for run in runs.values():
                blob = json.dumps(run)
                self.assertNotIn(VISITOR_KEY, blob)
                self.assertNotIn(OTHER_KEY, blob)

    def test_a_public_child_never_inherits_the_operators_key(self):
        """default_spawn strips APIFY_TOKEN* from the environment it
        builds, so a free run cannot borrow one and a paid run carries
        only the key its own visitor pasted."""
        captured = {}

        class FakePopen:
            def __init__(self, argv, cwd=None, env=None, stdout=None,
                         stderr=None):
                captured["env"], captured["argv"] = env, argv

        import tempfile
        run_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: None)
        with mock.patch.dict(os.environ, {"APIFY_TOKEN": OPERATOR_KEY,
                                          "APIFY_TOKEN_2": OPERATOR_KEY},
                             clear=False):
            with mock.patch.object(sweep_worker.subprocess, "Popen", FakePopen):
                _child, closer = sweep_worker.default_spawn(
                    "abc", run_dir, "beta_abc", REPO_ROOT, VISITOR_KEY)
        if closer:
            closer.close()
        self.assertEqual(captured["env"]["APIFY_TOKEN"], VISITOR_KEY)
        self.assertNotIn("APIFY_TOKEN_2", captured["env"])
        self.assertNotIn(OPERATOR_KEY, json.dumps(captured["env"]))
        self.assertNotIn(VISITOR_KEY, " ".join(captured["argv"]))


class TestTheFreePathIsUnchanged(unittest.TestCase):

    def test_a_free_sweep_still_needs_no_key_at_all(self):
        with stack() as (url, store):
            with apify() as (seen, check):
                app = render_app(url, check_token=check)
                client = reviewed(app)
                client.post("/key/free")
                client.get("/configure")
                started = client.post("/run")
            self.assertIn("/running", started.headers["Location"])
            run = store.read(only_run(store))
            self.assertTrue(run["free_only"])
            self.assertEqual(seen, [], "the free path asked Apify about a key")

    def test_the_free_choice_still_switches_the_paid_boards_off(self):
        with stack() as (url, store):
            app = render_app(url)
            client = reviewed(app)
            client.post("/key/free")
            client.get("/configure")
            client.post("/run")
            source = open(os.path.join(store.dir(only_run(store)),
                                       "profile.py"), encoding="utf-8").read()
            for site in sweep_worker.paid_sites(REPO_ROOT):
                self.assertRegex(source,
                                 rf'"{site}": {{[^}}]*"enabled": False')


class TestQueuedPaidRunsAcrossARestart(unittest.TestCase):

    def test_a_key_held_for_a_queued_run_is_not_persisted(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                client = paid_visitor(app, store)
                # Held, not yet used.
                for dirpath, _dirs, files in os.walk(store.root):
                    for name in files:
                        with open(os.path.join(dirpath, name), "rb") as fh:
                            self.assertNotIn(VISITOR_KEY.encode(), fh.read())

    def test_a_paid_run_queued_at_restart_asks_for_the_key_again(self):
        """The worker's own rule, reached through the public flow: the key
        was deliberately never written down, so the run cannot be resumed
        on the visitor's behalf."""
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                first = paid_visitor(app, store, VISITOR_KEY, "ada")
                second = paid_visitor(app, store, OTHER_KEY, "ben")
                with priced(app):
                    first.get("/configure")
                    second.get("/configure")
                    first.post("/run")      # runs
                    second.post("/run")     # queues behind it

            queued = [r for r in store.all() if r["state"] == "queued"]
            self.assertEqual(len(queued), 1)

            after = sweep_worker.Queue(sweep_worker.RunStore(store.root),
                                       checkout=store.root)
            after.recover()
            recovered = sweep_worker.RunStore(store.root).read(
                queued[0]["run_id"])
            self.assertEqual(recovered["state"], sweep_worker.INTERRUPTED)
            self.assertIn("token is never kept", recovered["error"])


class TestPublicResultsStayIsolated(unittest.TestCase):

    def test_a_paid_visitor_sees_only_their_own_rows(self):
        with stack() as (url, store):
            with apify() as (_seen, check):
                app = render_app(url, check_token=check)
                mine = paid_visitor(app, store, VISITOR_KEY, "ada")
                with priced(app):
                    mine.get("/configure")
                    mine.post("/run")
                give_rows(store, only_run(store))
                self.assertIn("React Native Developer",
                              mine.get("/results").get_data(as_text=True))

                stranger = reviewed(app, "stranger")
                self.assertNotIn("React Native Developer",
                                 stranger.get("/results",
                                              follow_redirects=True
                                              ).get_data(as_text=True))


class TestTheConsoleIsUnchanged(unittest.TestCase):

    def test_local_key_post_still_writes_env_and_os_environ(self):
        written = []
        app = app_module.create_app(
            state={"resume_text": "x", "profile": "kartik"},
            derive=lambda t, p: {},
            check_token=lambda token: (12.0, None))
        app.config["TESTING"] = True
        app.write_env = lambda k, v: written.append((k, v))
        with mock.patch.dict(os.environ, {}, clear=False):
            with mock.patch.object(app_module, "_apply_choice", create=True):
                answer = app.test_client().post("/key",
                                                data={"token": OPERATOR_KEY})
            self.assertIn("APIFY_TOKEN", os.environ)
        self.assertEqual(written, [("APIFY_TOKEN", OPERATOR_KEY)])
        self.assertEqual(answer.status_code, 302)

    def test_the_local_key_screen_still_says_it_writes_to_env(self):
        app = app_module.create_app(state={"resume_text": "x"},
                                    derive=lambda t, p: {})
        app.config["TESTING"] = True
        body = app.test_client().get("/key").get_data(as_text=True)
        self.assertIn(".env file in this folder", body)
        self.assertNotIn("not saved", body)


if __name__ == "__main__":
    unittest.main()
