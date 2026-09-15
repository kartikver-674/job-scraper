"""What the sweep worker must guarantee before it is put on Oracle.

The worker starts processes that spend a visitor's money, on the same box
as the inference fallback. So these tests are about the boundaries rather
than the happy path: whose run is whose, where a credential is allowed to
exist, whether structured data can become code, and what a restart does
to work that was in flight.

No real sweep runs here. `spawn` is injected, because what is under test
is the worker's bookkeeping, not scraper.py.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

from deploy import sweep_worker  # noqa: E402

TOKEN = "worker-token"
APIFY = "apify_live_SECRETVALUE_0123456789"

# A derived profile, the shape make_profile.render() takes.
PROFILE = {
    "candidate_name": "Ada Okonkwo",
    "field_summary": "React Native developer, about 4 years.",
    "years_experience": 4,
    "role_keywords": ["react native developer", "mobile engineer"],
    "skill_weights": [{"term": "react native", "weight": 4},
                      {"term": "typescript", "weight": 3}],
    "penalty_terms": [{"term": "salesforce", "weight": 5}],
    "domain_half_a": [], "domain_half_b": [], "domain_title_terms": [],
    "domain_bonus": 0, "notes": "",
}
PREFS = {
    "locations": ["Remote"],
    "exclude_levels": ["intern", "fresher"],
    "avoid": [], "min_comp_usd": None, "max_spend_usd": None,
    "remote_scopes": None, "max_age_days": None, "max_results": None,
    "linkedin_locations": None, "linkedin_remote_only": None,
    "sites_enabled": None,
}


class FakeChild:
    """A scraper.py that does nothing, on demand."""

    def __init__(self, code=0, block=None):
        self.code, self.block = code, block
        self.terminated = False
        self.env = None

    def wait(self):
        if self.block:
            self.block.wait(5)
        return self.code

    def terminate(self):
        self.terminated = True
        if self.block:
            self.block.set()


class Harness(unittest.TestCase):
    """A worker with a temp runs directory and a temp checkout."""

    def setUp(self):
        self.runs = tempfile.mkdtemp(prefix="runs-")
        self.checkout = tempfile.mkdtemp(prefix="checkout-")
        os.makedirs(os.path.join(self.checkout, "profiles"), exist_ok=True)
        self.addCleanup(shutil.rmtree, self.runs, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.checkout, ignore_errors=True)
        self.spawned = []
        self.now = [1_000.0]

    def spawn(self, code=0, block=None):
        def spawner(run_id, run_dir, profile_name, checkout, token):
            child = FakeChild(code, block)
            self.spawned.append({"run_id": run_id, "profile": profile_name,
                                 "token": token, "dir": run_dir})
            return child, None
        return spawner

    def build(self, spawner=None, max_active=1):
        store = sweep_worker.RunStore(self.runs)
        queue = sweep_worker.Queue(store, spawn=spawner or self.spawn(),
                                   checkout=self.checkout,
                                   clock=lambda: self.now[0],
                                   max_active=max_active)
        app = sweep_worker.create_app(store=store, queue=queue,
                                      accepted={TOKEN},
                                      checkout=self.checkout,
                                      clock=lambda: self.now[0])
        app.config["TESTING"] = True
        return app, store, queue

    def post_run(self, client, owner="session-a", free_only=True,
                 apify_token=None, profile=None, prefs=None):
        body = {"profile": profile if profile is not None else PROFILE,
                "prefs": prefs if prefs is not None else PREFS,
                "free_only": free_only, "owner": owner}
        if apify_token:
            body["apify_token"] = apify_token
        return client.post("/v1/runs", json=body,
                           headers={"Authorization": f"Bearer {TOKEN}"})

    def auth(self, owner="session-a"):
        return {"Authorization": f"Bearer {TOKEN}", "X-Sweep-Owner": owner}


class TestBearerAuth(Harness):
    """Every endpoint. Only Render calls this, and an unauthenticated
    route is a route somebody else can map."""

    def test_every_endpoint_refuses_without_the_token(self):
        app, _store, _queue = self.build()
        client = app.test_client()
        run = self.post_run(client).get_json()["run_id"]
        for method, path in (("get", "/healthz"),
                             ("post", "/v1/runs"),
                             ("get", f"/v1/runs/{run}"),
                             ("get", f"/v1/runs/{run}/rows"),
                             ("post", f"/v1/runs/{run}/stop")):
            with self.subTest(path=path):
                bare = getattr(client, method)(path)
                self.assertEqual(bare.status_code, 401)
                wrong = getattr(client, method)(
                    path, headers={"Authorization": "Bearer nope"})
                self.assertEqual(wrong.status_code, 401)

    def test_a_worker_with_no_token_configured_refuses_to_start(self):
        with self.assertRaises(SystemExit):
            sweep_worker.accepted_tokens({})


class TestOwnership(Harness):
    """Render is the only caller, but the worker does not take its word
    for whose run is whose."""

    def test_one_session_cannot_read_or_stop_anothers_run(self):
        app, _store, _queue = self.build()
        client = app.test_client()
        mine = self.post_run(client, owner="session-a").get_json()["run_id"]

        for method, path in (("get", f"/v1/runs/{mine}"),
                             ("get", f"/v1/runs/{mine}/rows"),
                             ("post", f"/v1/runs/{mine}/stop")):
            with self.subTest(path=path):
                stranger = getattr(client, method)(
                    path, headers=self.auth("session-b"))
                # 404, not 403: a refusal that confirms the id exists is
                # itself a disclosure.
                self.assertEqual(stranger.status_code, 404)
        # ... and the run is untouched by the attempt.
        self.assertEqual(
            client.get(f"/v1/runs/{mine}",
                       headers=self.auth("session-a")).status_code, 200)

    def test_a_run_id_alone_is_not_authority(self):
        app, _store, _queue = self.build()
        client = app.test_client()
        run = self.post_run(client).get_json()["run_id"]
        no_owner = client.get(f"/v1/runs/{run}",
                              headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(no_owner.status_code, 404)

    def test_the_owner_string_is_never_handed_back(self):
        app, _store, _queue = self.build()
        client = app.test_client()
        run = self.post_run(client, owner="session-a").get_json()["run_id"]
        body = client.get(f"/v1/runs/{run}", headers=self.auth()).get_json()
        self.assertNotIn("owner", body)


class TestTheApifyToken(Harness):
    """A visitor's credential. It exists to reach one child process."""

    def test_it_reaches_the_child_and_nothing_else(self):
        app, store, _queue = self.build()
        client = app.test_client()
        run = self.post_run(client, free_only=False,
                            apify_token=APIFY).get_json()["run_id"]
        # The child got it...
        self.assertEqual(self.spawned[0]["token"], APIFY)
        # ... and nothing on disk has it.
        for root in (self.runs, self.checkout):
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    with open(os.path.join(dirpath, name), "rb") as fh:
                        self.assertNotIn(APIFY.encode(), fh.read(),
                                         f"{os.path.join(dirpath, name)}")
        # ... nor does the status the API hands back.
        body = client.get(f"/v1/runs/{run}", headers=self.auth()).get_json()
        self.assertNotIn(APIFY, json.dumps(body))
        self.assertNotIn(APIFY, json.dumps(store.read(run)))

    def test_it_is_dropped_from_memory_once_the_child_has_it(self):
        app, _store, queue = self.build()
        client = app.test_client()
        run = self.post_run(client, free_only=False,
                            apify_token=APIFY).get_json()["run_id"]
        self.assertNotIn(run, queue._tokens)
        self.assertNotIn(APIFY, json.dumps(list(queue._tokens.values())))

    def test_it_is_never_in_a_log_line(self):
        import logging
        records = []


        class Sink(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage() % () if not record.args
                               else record.getMessage())

        logger = sweep_worker.log
        handler = Sink()
        logger.addHandler(handler)
        previous = logger.level
        logger.setLevel(logging.INFO)
        self.addCleanup(logger.setLevel, previous)
        self.addCleanup(logger.removeHandler, handler)
        app, _store, _queue = self.build()
        self.post_run(app.test_client(), free_only=False, apify_token=APIFY)
        self.assertTrue(records, "the worker logged nothing at all")
        self.assertNotIn(APIFY, " ".join(records))

    def test_a_free_sweep_may_not_carry_one(self):
        app, _store, _queue = self.build()
        r = self.post_run(app.test_client(), free_only=True, apify_token=APIFY)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.spawned, [])

    def test_a_paid_sweep_without_one_is_refused_rather_than_borrowing(self):
        """Never the operator's key: a paid run is funded by whoever asked
        for it or it does not happen."""
        app, _store, _queue = self.build()
        r = self.post_run(app.test_client(), free_only=False)
        self.assertEqual(r.status_code, 400)
        self.assertIn("own Apify token", r.get_json()["error"]["message"])

    def test_the_child_environment_is_scrubbed_of_the_operators_keys(self):
        """default_spawn builds the env; a free run must not inherit an
        APIFY_TOKEN that happens to be set on the box."""
        from unittest import mock
        captured = {}

        class FakePopen:
            def __init__(self, argv, cwd=None, env=None, stdout=None,
                         stderr=None):
                captured["argv"], captured["env"] = argv, env

        run_dir = os.path.join(self.runs, "abc")
        os.makedirs(run_dir, exist_ok=True)
        with mock.patch.dict(os.environ, {"APIFY_TOKEN": "OPERATOR-KEY",
                                          "APIFY_TOKEN_2": "OPERATOR-TWO"}):
            with mock.patch.object(sweep_worker.subprocess, "Popen", FakePopen):
                _child, closer = sweep_worker.default_spawn(
                    "abc", run_dir, "beta_abc", self.checkout, None)
            if closer:
                closer.close()
        self.assertNotIn("APIFY_TOKEN", captured["env"])
        self.assertNotIn("APIFY_TOKEN_2", captured["env"])
        # ... and a token never rides on the command line, where
        # /proc/<pid>/cmdline would publish it to every user on the box.
        self.assertNotIn("APIFY_TOKEN", " ".join(captured["argv"]))


class TestNoArbitraryPython(Harness):
    """Structured data in, literals out — proven, not assumed."""

    def test_a_term_that_looks_like_code_stays_a_string(self):
        app, store, _queue = self.build()
        client = app.test_client()
        nasty = "'); import os; os.system('touch /tmp/pwned'); ('"
        profile = dict(PROFILE, skill_weights=[{"term": nasty, "weight": 3}])
        run = self.post_run(client, profile=profile).get_json()["run_id"]
        source = open(os.path.join(self.runs, run, "profile.py"),
                      encoding="utf-8").read()
        # It appears ONLY as a quoted literal — repr() is what makes the
        # difference between data and code here.
        self.assertIn(repr(nasty.strip().lower()), source)
        sweep_worker.assert_only_literals(source)
        # ... and running the module defines a dict, nothing else.
        namespace = {}
        exec(compile(source, "profile.py", "exec"), namespace)  # noqa: S102
        self.assertIn(nasty.strip().lower(), namespace["SCORING"]["skill_weights"])
        self.assertFalse(os.path.exists("/tmp/pwned"))

    def test_the_literal_check_refuses_anything_that_is_not_data(self):
        for source in ('import os\n',
                       'X = os.system("id")\n',
                       'X = [i for i in range(3)]\n',
                       'def f():\n    pass\n',
                       'X = f"{1+1}"\n',
                       'SETTINGS["output_dir"] = os.getcwd()\n'):
            with self.subTest(source=source.strip()[:28]):
                with self.assertRaises(sweep_worker.Refused):
                    sweep_worker.assert_only_literals(source)

    def test_it_accepts_the_shape_the_renderer_actually_produces(self):
        app, _store, _queue = self.build()
        run = self.post_run(app.test_client()).get_json()["run_id"]
        source = open(os.path.join(self.runs, run, "profile.py"),
                      encoding="utf-8").read()
        self.assertIn("SETTINGS[\"output_dir\"]", source.replace("'", '"'))

    def test_a_malformed_body_never_reaches_the_renderer(self):
        app, _store, _queue = self.build()
        client = app.test_client()
        for body in ({"profile": "not a dict", "owner": "s"},
                     {"profile": PROFILE, "prefs": [], "owner": "s"},
                     {"profile": PROFILE, "owner": ""},
                     {"profile": PROFILE, "owner": "s", "surprise": 1},
                     {"profile": {}, "prefs": PREFS, "owner": "s"}):
            with self.subTest(body=sorted(body)):
                r = client.post("/v1/runs", json=body,
                                headers={"Authorization": f"Bearer {TOKEN}"})
                self.assertEqual(r.status_code, 400)
        self.assertEqual(self.spawned, [])

    def test_the_run_id_cannot_walk_out_of_the_runs_directory(self):
        app, store, _queue = self.build()
        client = app.test_client()
        for bad in ("../../etc", "..%2f..", "beta_x", "NOTHEX"):
            with self.subTest(bad):
                r = client.get(f"/v1/runs/{bad}", headers=self.auth())
                self.assertIn(r.status_code, (404, 308))
        with self.assertRaises(sweep_worker.Refused):
            store.dir("../escape")


class TestTheQueue(Harness):
    """One at a time, in the order they arrived."""

    def test_concurrent_submissions_give_one_running_and_a_fifo_queue(self):
        gate = threading.Event()
        app, store, queue = self.build(spawner=self.spawn(block=gate))
        client = app.test_client()
        ids = [self.post_run(client, owner=f"s{i}").get_json()["run_id"]
               for i in range(4)]

        states = [client.get(f"/v1/runs/{r}",
                             headers=self.auth(f"s{i}")).get_json()
                  for i, r in enumerate(ids)]
        self.assertEqual([s["state"] for s in states],
                         [sweep_worker.RUNNING] + [sweep_worker.QUEUED] * 3)
        self.assertEqual([s["queue_position"] for s in states], [0, 1, 2, 3])
        self.assertEqual(len(self.spawned), 1, "more than one sweep started")

        # The first finishes; the next in line starts, and only that one.
        gate.set()
        for _ in range(50):
            if len(self.spawned) > 1:
                break
            time.sleep(0.05)
        self.assertEqual(self.spawned[1]["run_id"], ids[1],
                         "the queue did not run in arrival order")

    def test_stopping_a_queued_run_takes_it_out_of_the_line(self):
        gate = threading.Event()
        app, store, queue = self.build(spawner=self.spawn(block=gate))
        client = app.test_client()
        first = self.post_run(client, owner="s0").get_json()["run_id"]
        second = self.post_run(client, owner="s1").get_json()["run_id"]
        client.post(f"/v1/runs/{second}/stop", headers=self.auth("s1"))
        self.assertEqual(store.read(second)["state"], sweep_worker.STOPPED)
        gate.set()
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(self.spawned[0]["run_id"], first)


class TestRestart(Harness):
    """A worker restart is normal. Losing somebody's sweep silently is not."""

    def test_queued_work_and_finished_results_survive(self):
        gate = threading.Event()
        app, store, _queue = self.build(spawner=self.spawn(block=gate))
        client = app.test_client()
        running = self.post_run(client, owner="s0").get_json()["run_id"]
        queued = self.post_run(client, owner="s1").get_json()["run_id"]

        # A second worker over the same directory: the restart.
        store2 = sweep_worker.RunStore(self.runs)
        queue2 = sweep_worker.Queue(store2, spawn=self.spawn(),
                                    checkout=self.checkout,
                                    clock=lambda: self.now[0])
        queue2.recover()

        self.assertEqual(store2.read(queued)["state"], sweep_worker.RUNNING,
                         "the queued run was not picked up after restart")
        after = store2.read(running)
        self.assertEqual(after["state"], sweep_worker.INTERRUPTED)
        self.assertIn("restarted", after["error"])
        gate.set()

    def test_a_paid_run_queued_at_restart_is_interrupted_not_retried(self):
        """Its token was deliberately never written down, so it cannot be
        started again on the visitor's behalf — and must never reach for
        anybody else's."""
        gate = threading.Event()
        app, store, _queue = self.build(spawner=self.spawn(block=gate))
        client = app.test_client()
        self.post_run(client, owner="s0").get_json()["run_id"]
        paid = self.post_run(client, owner="s1", free_only=False,
                             apify_token=APIFY).get_json()["run_id"]

        queue2 = sweep_worker.Queue(sweep_worker.RunStore(self.runs),
                                    spawn=self.spawn(),
                                    checkout=self.checkout,
                                    clock=lambda: self.now[0])
        queue2.recover()
        after = sweep_worker.RunStore(self.runs).read(paid)
        self.assertEqual(after["state"], sweep_worker.INTERRUPTED)
        self.assertIn("token is never kept", after["error"])
        gate.set()

    def test_a_completed_run_keeps_its_result_metadata(self):
        app, store, _queue = self.build()
        client = app.test_client()
        run = self.post_run(client).get_json()["run_id"]
        for _ in range(50):
            if store.read(run)["state"] in sweep_worker.TERMINAL:
                break
            time.sleep(0.05)
        before = store.read(run)
        self.assertEqual(before["state"], sweep_worker.DONE)

        queue2 = sweep_worker.Queue(sweep_worker.RunStore(self.runs),
                                    spawn=self.spawn(),
                                    checkout=self.checkout,
                                    clock=lambda: self.now[0])
        queue2.recover()
        self.assertEqual(sweep_worker.RunStore(self.runs).read(run), before)


class TestCleanup(Harness):
    """48 hours, and never something somebody is watching."""

    def test_it_deletes_only_what_is_past_its_ttl(self):
        app, store, queue = self.build()
        client = app.test_client()
        old = self.post_run(client).get_json()["run_id"]
        for _ in range(50):
            if store.read(old)["state"] in sweep_worker.TERMINAL:
                break
            time.sleep(0.05)
        self.now[0] += sweep_worker.TTL_SECONDS + 1
        fresh = self.post_run(client).get_json()["run_id"]

        removed = queue.cleanup()
        self.assertEqual(removed, [old])
        self.assertFalse(os.path.exists(os.path.join(self.runs, old)))
        self.assertTrue(os.path.exists(os.path.join(self.runs, fresh)))

    def test_it_cannot_delete_a_running_or_queued_run(self):
        gate = threading.Event()
        app, store, queue = self.build(spawner=self.spawn(block=gate))
        client = app.test_client()
        running = self.post_run(client, owner="s0").get_json()["run_id"]
        queued = self.post_run(client, owner="s1").get_json()["run_id"]

        # Far past any TTL, and still somebody's sweep.
        self.now[0] += sweep_worker.TTL_SECONDS * 10
        self.assertEqual(queue.cleanup(), [])
        self.assertEqual(store.read(running)["state"], sweep_worker.RUNNING)
        self.assertEqual(store.read(queued)["state"], sweep_worker.QUEUED)
        gate.set()


class TestResults(Harness):
    """What Render reads back while a sweep runs and after it ends."""

    def test_rows_are_served_from_the_runs_own_output_directory(self):
        app, store, _queue = self.build()
        client = app.test_client()
        run = self.post_run(client).get_json()["run_id"]
        out = store.output_dir(run)
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "jobs_1.csv"), "w", encoding="utf-8") as fh:
            fh.write("title,company,score\nRN Dev,Acme,42\nMobile Eng,Beta,31\n")

        body = client.get(f"/v1/runs/{run}/rows",
                          headers=self.auth()).get_json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["rows"][0]["title"], "RN Dev")

        later = client.get(f"/v1/runs/{run}/rows?since=1",
                           headers=self.auth()).get_json()
        self.assertEqual([r["title"] for r in later["rows"]], ["Mobile Eng"])

    def test_no_rows_yet_is_not_an_error(self):
        app, _store, _queue = self.build()
        client = app.test_client()
        run = self.post_run(client).get_json()["run_id"]
        body = client.get(f"/v1/runs/{run}/rows",
                          headers=self.auth()).get_json()
        self.assertEqual((body["rows"], body["total"]), ([], 0))


if __name__ == "__main__":
    unittest.main()
