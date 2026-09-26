"""Phase 0b: a résumé's jobs are scored with the engine that derived it.

Render derives with v2; the Oracle worker rendered every public profile with
its own default, v1 (design §A.3). The fix carries the derivation's engine
explicitly — Render records it, the worker renders with it, the engine child
binds it from the file and the dry run says which one it bound — and every
link is optional, so a request without it is exactly today's.

Two things are held apart here on purpose:

  compatibility   render(engine=), the worker's optional `engine`, the
                  client's optional argument, state["derived_engine"] and the
                  opt-in --attest-engine: all present, all inert by default.
  activation      worker_link.ENGINE_FLAG. Off, Render sends nothing new and
                  public scoring stays v1. Turning it on is Phase 0b-B.

No network beyond loopback, no model, no provider. The end-to-end cases run
the real scraper.py dry run in a fresh interpreter with sockets refused.
"""

import ast
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

import make_profile  # noqa: E402
import scraper  # noqa: E402
import skill_concepts  # noqa: E402
from deploy import sweep_worker  # noqa: E402
from deploy.test_sweep_worker import PREFS, PROFILE  # noqa: E402
from sweep import app as app_module  # noqa: E402
from sweep import worker_client, worker_link  # noqa: E402
from sweep.tests.test_app import DERIVED  # noqa: E402
from sweep.tests.test_public_paid import (PLAN, apify,  # noqa: E402
                                          paid_visitor)
from sweep.tests.test_public_sweep import reviewed  # noqa: E402
from sweep.tests.test_worker_link import WORKER_TOKEN, render_app  # noqa: E402

ENGINE_VARS = (skill_concepts.VERSION_ENV, skill_concepts.FLAG,
               skill_concepts.EVIDENCE_FLAG)
BAD_VALUES = ("mixed", "v3", "", "V2 ", "v2; rm -rf")
BAD_TYPES = (2, True, ["v2"], {"engine": "v2"})


@contextlib.contextmanager
def engine_env(version=None):
    """This process's engine variables set to exactly `version` — None is the
    worker's real state, none of them present — and nothing bound."""
    saved = {k: os.environ.get(k) for k in ENGINE_VARS}
    bound = skill_concepts._BOUND
    for k in ENGINE_VARS:
        os.environ.pop(k, None)
    if version:
        os.environ[skill_concepts.VERSION_ENV] = version
    skill_concepts.bind(None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        skill_concepts._BOUND = bound


@contextlib.contextmanager
def propagation(on):
    with mock.patch.dict(os.environ, {worker_link.ENGINE_FLAG: "1" if on else ""}):
        yield


def stamp_of(source):
    """PROFILE_SCHEMA as the file wrote it, read without executing it."""
    for node in ast.parse(source).body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", None) == "PROFILE_SCHEMA"):
            return ast.literal_eval(node.value)
    return None


# A plan child that is the real engine: scraper.py's own dry run, in a fresh
# interpreter, with the checkout's profiles package first on sys.path (the
# golden driver's technique) and every socket refused.
BOOT = (
    "import os, socket, sys\n"
    "def _no(*a, **k):\n"
    "    raise RuntimeError('a plan child must not open a connection')\n"
    "socket.socket.connect = _no\n"
    "socket.create_connection = _no\n"
    "checkout, repo = sys.argv[1], sys.argv[2]\n"
    "sys.argv = ['scraper.py'] + sys.argv[3:]\n"
    "sys.path[:0] = [checkout, repo, os.path.join(repo, 'auto-apply')]\n"
    "import scraper\n"
    "scraper.main()\n")
REAL_RUN = subprocess.run


class PlanChild:
    """Stands in for the worker's `subprocess` module: records each plan
    child's argv and the stamp of the file it was handed, then runs the real
    dry run (or answers `canned` JSON without running anything)."""

    SubprocessError = subprocess.SubprocessError

    def __init__(self, canned=None):
        self.calls, self.canned = [], canned

    def run(self, argv, cwd, env, timeout, **_kw):
        with open(os.path.join(cwd, "profiles", f"{argv[3]}.py"), encoding="utf-8") as fh:
            stamp = stamp_of(fh.read())
        self.calls.append({"argv": list(argv), "stamp": stamp})
        if self.canned is not None:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.canned), "")
        sealed = {k: v for k, v in env.items()
                  if k in ("PATH", "HOME", "LANG") or k in ENGINE_VARS}
        out = REAL_RUN([sys.executable, "-c", BOOT, cwd, REPO_ROOT] + list(argv[2:]),
                       cwd=cwd, env=sealed, capture_output=True, text=True,
                       timeout=timeout)
        self.calls[-1]["stdout"] = out.stdout
        return out


class Worker(unittest.TestCase):
    """The real worker app, a temp checkout whose profiles/ is a package, and
    a spawn that starts nothing."""

    def setUp(self):
        self.runs = tempfile.mkdtemp(prefix="p0b-runs-")
        self.checkout = tempfile.mkdtemp(prefix="p0b-checkout-")
        os.makedirs(os.path.join(self.checkout, "profiles"))
        open(os.path.join(self.checkout, "profiles", "__init__.py"), "w").close()
        self.addCleanup(shutil.rmtree, self.runs, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.checkout, ignore_errors=True)
        self.store = sweep_worker.RunStore(self.runs)
        self.child = PlanChild()
        patcher = mock.patch.object(sweep_worker, "subprocess", self.child)
        patcher.start()
        self.addCleanup(patcher.stop)

        def spawn(run_id, run_dir, profile_name, checkout, tokens):
            return types.SimpleNamespace(wait=lambda: 0, terminate=lambda: None), None

        queue = sweep_worker.Queue(self.store, spawn=spawn, checkout=self.checkout)
        self.app = sweep_worker.create_app(store=self.store, queue=queue,
                                           accepted={WORKER_TOKEN},
                                           checkout=self.checkout)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def post(self, path, **extra):
        body = {"profile": PROFILE, "prefs": PREFS, "free_only": True,
                "owner": "session-a", **extra}
        return self.client.post(path, json=body,
                                headers={"Authorization": f"Bearer {WORKER_TOKEN}"})

    def run_stamp(self, answer):
        run_id = answer.get_json()["run_id"]
        with open(os.path.join(self.store.dir(run_id), "profile.py"), encoding="utf-8") as fh:
            return run_id, fh.read()


# ===========================================================================
class RenderEngine(unittest.TestCase):
    """make_profile.render(name, data, prefs, engine=None)."""

    NAME = "p0b_render"

    def render(self, **kw):
        return make_profile.render(self.NAME, DERIVED, PREFS, **kw)

    def test_no_engine_is_todays_render_in_every_environment(self):
        # The Phase 0a goldens pin these bytes against fixtures; this pins
        # that passing None is the same call as not passing it.
        for version, stamped in ((None, "v1"), ("v1", "v1"), ("v2", "v2")):
            with self.subTest(env=version), engine_env(version):
                legacy = self.render()
                self.assertEqual(self.render(engine=None), legacy)
                self.assertEqual(stamp_of(legacy)["engine"], stamped)

    def test_explicit_v1_beats_a_v2_environment(self):
        with engine_env("v2"):
            self.assertEqual(stamp_of(self.render(engine="v1"))["engine"], "v1")

    def test_explicit_v2_needs_no_environment(self):
        with engine_env(None):
            self.assertEqual(stamp_of(self.render(engine="v2"))["engine"], "v2")

    def test_explicit_beats_a_bound_profile_too(self):
        with engine_env(None):
            skill_concepts.bind("v1")
            self.assertEqual(stamp_of(self.render(engine="v2"))["engine"], "v2")

    def test_the_stamp_is_the_only_difference(self):
        with engine_env(None):
            legacy, v2 = self.render(), self.render(engine="v2")
        self.assertEqual(legacy.count('"engine": \'v1\''), 1)
        self.assertEqual(legacy.replace('"engine": \'v1\'', '"engine": \'v2\''), v2)

    def test_mixed_unknown_and_malformed_are_refused(self):
        with engine_env("v2"):
            for bad in ("mixed", "v3", "", "   ") + BAD_TYPES:
                with self.subTest(engine=bad), self.assertRaises(ValueError):
                    self.render(engine=bad)

    def test_the_canonical_check_decides(self):
        # skill_concepts.engine_version's own normalisation, not a second list.
        with engine_env(None):
            self.assertEqual(stamp_of(self.render(engine=" V2 "))["engine"], "v2")


# ===========================================================================
class WorkerRuns(Worker):
    """/v1/runs: the optional `engine` on the existing profile form."""

    def test_a_legacy_body_renders_exactly_as_before(self):
        with engine_env(None):
            answer = self.post("/v1/runs")
            self.assertEqual(answer.status_code, 201)
            run_id, source = self.run_stamp(answer)
            status = self.store.read(run_id)
            expected = make_profile.render(
                status["profile_name"], PROFILE,
                sweep_worker.free_prefs(PREFS, self.checkout))
        self.assertTrue(source.startswith(expected))
        self.assertEqual(stamp_of(source), {"version": 1, "engine": "v1"})

    def test_null_is_the_same_as_absent(self):
        with engine_env(None):
            _run, source = self.run_stamp(self.post("/v1/runs", engine=None))
        self.assertEqual(stamp_of(source)["engine"], "v1")

    def test_an_explicit_engine_is_the_stamp_whatever_the_worker_env(self):
        for env in (None, "v1", "v2"):
            for engine in ("v1", "v2"):
                with self.subTest(env=env, engine=engine), engine_env(env):
                    _run, source = self.run_stamp(self.post("/v1/runs", engine=engine))
                    self.assertEqual(stamp_of(source), {"version": 1, "engine": engine})

    def test_the_worker_env_default_is_still_v1_without_a_field(self):
        # The production fact the design recorded (§A.3), unchanged by 0b.
        with engine_env(None):
            _run, absent = self.run_stamp(self.post("/v1/runs"))
            _run, v2 = self.run_stamp(self.post("/v1/runs", engine="v2"))
        self.assertEqual((stamp_of(absent)["engine"], stamp_of(v2)["engine"]), ("v1", "v2"))


class WorkerRefusals(Worker):
    """Both endpoints refuse a bad engine with a safe 400 and do nothing."""

    def assert_refused(self, answer, message):
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(answer.get_json(), {"error": {"message": message}})

    def test_unknown_values(self):
        for path in ("/v1/runs", "/v1/plans"):
            for bad in BAD_VALUES:
                with self.subTest(path=path, engine=bad):
                    answer = self.post(path, engine=bad)
                    self.assert_refused(answer, "engine must be one of v1, v2")
        self.assertEqual(self.store.all(), [])
        self.assertEqual(self.child.calls, [])

    def test_wrong_types(self):
        for path in ("/v1/runs", "/v1/plans"):
            for bad in BAD_TYPES:
                with self.subTest(path=path, engine=bad):
                    self.assert_refused(self.post(path, engine=bad),
                                        "engine must be a string when given")
        self.assertEqual(self.store.all(), [])
        self.assertEqual(self.child.calls, [])

    def test_unknown_fields_are_still_refused(self):
        for path in ("/v1/runs", "/v1/plans"):
            with self.subTest(path=path):
                self.assert_refused(self.post(path, engines="v2"),
                                    "unknown field(s): engines")
                self.assert_refused(self.post(path, tracks=[], engine="v2"),
                                    "unknown field(s): tracks")


class WorkerPlans(Worker):
    """/v1/plans: the child's argv, and the file it is handed."""

    def setUp(self):
        super().setUp()
        self.child.canned = {"profile": "x", "sites": {}}

    def test_a_legacy_body_runs_todays_dry_run(self):
        with engine_env(None):
            self.assertEqual(self.post("/v1/plans").status_code, 200)
        call, = self.child.calls
        self.assertEqual(call["argv"][1:3] + call["argv"][4:],
                         ["scraper.py", "--profile", "--dry-run", "--json"])
        self.assertEqual(call["stamp"]["engine"], "v1")

    def test_an_explicit_engine_asks_for_the_attestation(self):
        for engine in ("v1", "v2"):
            with self.subTest(engine=engine), engine_env("v2" if engine == "v1" else None):
                self.child.calls.clear()
                self.assertEqual(self.post("/v1/plans", engine=engine).status_code, 200)
                call, = self.child.calls
                self.assertEqual(call["argv"][4:], ["--dry-run", "--json", "--attest-engine"])
                self.assertEqual(call["stamp"]["engine"], engine)


class DryRunAttestation(Worker):
    """The real engine child says which engine it bound — from the file."""

    def plan(self, **extra):
        answer = self.post("/v1/plans", **extra)
        self.assertEqual(answer.status_code, 200, answer.get_data(as_text=True))
        return answer.get_json()

    def test_legacy_plans_carry_no_attestation(self):
        with engine_env(None):
            raw = self.plan()
        self.assertNotIn("profile_engine", raw)
        # jsonify sorts keys, as it always has: the set is the contract here.
        self.assertEqual(sorted(raw), sorted(["profile", "sites", "max_results",
                                              "charge_ceiling_usd", "free_sources",
                                              "public_paid"]))

    def test_the_attested_engine_is_the_stamp_the_child_bound(self):
        with engine_env(None):
            legacy = self.plan()
            v2 = self.plan(engine="v2")
            v1 = self.plan(engine="v1")
        self.assertEqual((v1["profile_engine"], v2["profile_engine"]), ("v1", "v2"))
        # An addition only: the plan itself is the legacy plan.
        for attested in (v1, v2):
            self.assertEqual({k: v for k, v in attested.items()
                              if k not in ("profile", "profile_engine")},
                             {k: v for k, v in legacy.items() if k != "profile"})

    def test_the_childs_own_json_is_todays_plus_one_trailing_key(self):
        # Key order is part of the dry-run contract (the Phase 0a goldens
        # compare it), so it is checked on the child's stdout, before the
        # worker re-serialises it.
        with engine_env(None):
            self.plan()
            self.plan(engine="v2")
        legacy, attested = (json.loads(c["stdout"]) for c in self.child.calls)
        self.assertEqual(list(legacy), ["profile", "sites", "max_results",
                                        "charge_ceiling_usd", "free_sources",
                                        "public_paid"])
        self.assertEqual(list(attested), list(legacy) + ["profile_engine"])

    def test_explicit_v1_holds_even_where_the_worker_and_child_say_v2(self):
        with engine_env("v2"):
            self.assertEqual(self.plan(engine="v1")["profile_engine"], "v1")
            self.assertEqual(self.plan()["public_paid"], self.plan(engine="v2")["public_paid"])

    def test_the_flag_is_refused_outside_dry_run_json(self):
        for argv in (["scraper.py", "--attest-engine"],
                     ["scraper.py", "--dry-run", "--attest-engine"]):
            with self.subTest(argv=argv), mock.patch.object(sys, "argv", argv), \
                    contextlib.redirect_stderr(open(os.devnull, "w")), \
                    self.assertRaises(SystemExit) as caught:
                scraper.parse_args()
            self.assertEqual(caught.exception.code, 2)


# ===========================================================================
class ClientBodies(unittest.TestCase):
    """worker_client: an omitted engine is today's body, exactly."""

    def capture(self):
        seen = []

        def call(method, path, body=None, **kw):
            seen.append((method, path, body))
            return {"run_id": "abc", "profile_engine": "v2"}
        return seen, mock.patch.object(worker_client, "_call", call)

    def test_legacy_bodies(self):
        seen, patch = self.capture()
        with patch:
            worker_client.plan(PROFILE, PREFS, free_only=False, owner="o")
            worker_client.create_run(PROFILE, PREFS, free_only=False, owner="o",
                                     key_ids=["k1"])
            worker_client.create_run(PROFILE, PREFS, owner="o", engine=None)
        self.assertEqual(seen, [
            ("POST", "/v1/plans", {"profile": PROFILE, "prefs": PREFS,
                                   "free_only": False, "owner": "o"}),
            ("POST", "/v1/runs", {"profile": PROFILE, "prefs": PREFS, "free_only": False,
                                  "owner": "o", "key_ids": ["k1"]}),
            ("POST", "/v1/runs", {"profile": PROFILE, "prefs": PREFS,
                                  "free_only": True, "owner": "o"})])

    def test_an_explicit_engine_is_sent_by_both(self):
        seen, patch = self.capture()
        with patch:
            worker_client.plan(PROFILE, PREFS, owner="o", engine="v2")
            worker_client.create_run(PROFILE, PREFS, owner="o", engine="v2")
        self.assertEqual([body["engine"] for _m, _p, body in seen], ["v2", "v2"])

    def test_check_engine(self):
        raw = {"sites": {}, "profile_engine": "v2"}
        self.assertIs(worker_client.check_engine(raw, "v2"), raw)
        for bad in ({"sites": {}, "profile_engine": "v1"}, {"sites": {}},
                    {"profile_engine": None}):
            with self.subTest(raw=bad), self.assertRaises(worker_client.EngineMismatch):
                worker_client.check_engine(bad, "v2")
        # A WorkerError, so the public app's existing handler catches it.
        self.assertTrue(issubclass(worker_client.EngineMismatch, worker_client.WorkerError))


# ===========================================================================
class LinkPropagation(unittest.TestCase):
    """worker_link: what Render sends, by the activation switch."""

    def link(self, state, answer=None, error=None):
        sent = {"plan": [], "run": []}

        def plan(profile, prefs, free_only=True, **kw):
            sent["plan"].append(kw)
            if error:
                raise error
            return dict(answer if answer is not None else PLAN)

        def create_run(profile, prefs, free_only=True, **kw):
            sent["run"].append(kw)
            return "run-1"

        app = types.SimpleNamespace(state=dict(state))
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(worker_client, "plan", plan))
        stack.enter_context(mock.patch.object(worker_client, "create_run", create_run))
        stack.enter_context(mock.patch.object(worker_link.public, "remember_run",
                                              lambda run_id: None))
        return worker_link.injections(app), sent, stack

    STATE = {"derived": DERIVED, "derived_engine": "v2", "free_only": False}

    def test_off_by_default_nothing_new_is_sent(self):
        env = {k: v for k, v in os.environ.items() if k != worker_link.ENGINE_FLAG}
        with mock.patch.dict(os.environ, env, clear=True):
            fns, sent, stack = self.link(self.STATE)
            with stack:
                fns["fetch_plan"]("p")
                fns["start_sweep"]("p")
        self.assertEqual(sent, {"plan": [{}], "run": [{"key_ids": None}]})

    def test_on_both_calls_send_the_recorded_engine(self):
        with propagation(True):
            fns, sent, stack = self.link(self.STATE,
                                         answer=dict(PLAN, profile_engine="v2"))
            with stack:
                raw = fns["fetch_plan"]("p")
                fns["start_sweep"]("p")
        self.assertEqual(raw["profile_engine"], "v2")
        self.assertEqual([sent["plan"][0]["engine"], sent["run"][0]["engine"]], ["v2", "v2"])

    def test_a_derivation_with_no_recorded_engine_stays_legacy(self):
        state = {k: v for k, v in self.STATE.items() if k != "derived_engine"}
        with propagation(True):
            fns, sent, stack = self.link(state)
            with stack:
                fns["fetch_plan"]("p")
                fns["start_sweep"]("p")
        self.assertNotIn("engine", sent["plan"][0])
        self.assertNotIn("engine", sent["run"][0])

    def test_a_mismatch_fails_closed_free_or_paid(self):
        for free_only in (False, True):
            for answer in (dict(PLAN, profile_engine="v1"), dict(PLAN)):
                with self.subTest(free_only=free_only, answer=answer.get("profile_engine")), \
                        propagation(True):
                    fns, _sent, stack = self.link(dict(self.STATE, free_only=free_only),
                                                  answer=answer)
                    with stack, self.assertRaises(worker_client.EngineMismatch):
                        fns["fetch_plan"]("p")

    def test_an_unreachable_worker_still_leaves_a_free_sweep_its_free_plan(self):
        with propagation(True):
            fns, _sent, stack = self.link(dict(self.STATE, free_only=True),
                                          error=worker_client.WorkerError("down"))
            with stack:
                raw = fns["fetch_plan"]("p")
        self.assertEqual(raw["sites"], {})

    def test_only_a_clear_yes_switches_it_on(self):
        for raw, on in (("1", True), ("true", True), (" ON ", True), ("yes", True),
                        ("", False), ("0", False), ("off", False), ("false", False),
                        ("2", False)):
            with self.subTest(raw=raw):
                self.assertIs(worker_link.sends_engine({worker_link.ENGINE_FLAG: raw}), on)
        self.assertIs(worker_link.sends_engine({}), False)


# ===========================================================================
class RenderState(unittest.TestCase):
    """state["derived_engine"]: recorded at derivation, never re-read."""

    def setUp(self):
        self.resume_dir = tempfile.mkdtemp(prefix="p0b-resume-")
        self.addCleanup(shutil.rmtree, self.resume_dir, ignore_errors=True)
        self.state = {"resume_text": "a résumé"}
        self.derive = mock.Mock(side_effect=lambda text, prefs: dict(DERIVED))
        env = {k: v for k, v in os.environ.items() if k != "SWEEP_PUBLIC_MODE"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.app = app_module.create_app(state=self.state, derive=self.derive,
                                             extract=lambda path: "a new résumé",
                                             resume_dir=self.resume_dir)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def upload(self):
        import io
        return self.client.post("/resume", data={"resume": (io.BytesIO(b"%PDF-1.7 x"),
                                                            "cv.pdf")},
                                content_type="multipart/form-data")

    def test_recorded_with_the_derivation(self):
        with engine_env("v2"):
            self.client.post("/derive")
        self.assertEqual(self.state["derived_engine"], "v2")

    def test_a_later_environment_does_not_change_it(self):
        with engine_env("v2"):
            self.client.post("/derive")
        with engine_env(None):
            self.client.post("/derive")          # cached: no second derivation
            self.client.get("/review")
        self.assertEqual(self.derive.call_count, 1)
        self.assertEqual(self.state["derived_engine"], "v2")

    def test_a_new_resume_clears_it_and_its_derivation_replaces_it(self):
        with engine_env("v2"):
            self.client.post("/derive")
        self.upload()
        self.assertNotIn("derived", self.state)
        self.assertNotIn("derived_engine", self.state)
        with engine_env(None):
            self.client.post("/derive")
        self.assertEqual(self.state["derived_engine"], "v1")

    def test_a_failed_derivation_records_nothing(self):
        self.derive.side_effect = RuntimeError("the model did not answer")
        with engine_env("v2"):
            self.client.post("/derive")
        self.assertNotIn("derived_engine", self.state)


# ===========================================================================
class PublicEndToEnd(Worker):
    """Render's public app -> the worker on a socket -> the real dry run."""

    def setUp(self):
        super().setUp()
        from werkzeug.serving import make_server
        server = make_server("127.0.0.1", 0, self.app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"
        patch = mock.patch.dict(os.environ, {worker_client.URL_ENV: self.url,
                                             worker_client.TOKEN_ENV: WORKER_TOKEN})
        patch.start()
        self.addCleanup(patch.stop)

    def free_sweep(self):
        """Derived under Render's v2, then run with the process saying v1:
        only the recorded engine can make the run v2."""
        render = render_app(self.url)
        with engine_env("v2"):
            client = reviewed(render)
        with engine_env(None):
            client.post("/key/free")
            client.get("/configure")
            answer = client.post("/run")
        return answer

    def run_engine(self):
        runs = self.store.all()
        self.assertEqual(len(runs), 1)
        _run, source = self.run_stamp(types.SimpleNamespace(
            get_json=lambda: {"run_id": runs[0]["run_id"]}))
        return stamp_of(source)["engine"]

    def test_switch_off_is_todays_public_behaviour(self):
        with propagation(False):
            self.free_sweep()
        self.assertTrue(self.child.calls)
        self.assertTrue(all("--attest-engine" not in c["argv"] for c in self.child.calls))
        self.assertTrue(all(c["stamp"]["engine"] == "v1" for c in self.child.calls))
        self.assertEqual(self.run_engine(), "v1")

    def test_switch_on_the_derivation_engine_reaches_the_run(self):
        with propagation(True):
            answer = self.free_sweep()
        self.assertEqual(answer.status_code, 302, answer.get_data(as_text=True)[:400])
        self.assertTrue(self.child.calls)
        self.assertTrue(all(c["argv"][-1] == "--attest-engine" for c in self.child.calls))
        self.assertTrue(all(c["stamp"]["engine"] == "v2" for c in self.child.calls))
        self.assertEqual(self.run_engine(), "v2")

    def test_an_old_worker_fails_loudly_never_silently_v1(self):
        # Render switched on before the worker was deployed: the old worker's
        # allowlist refuses the field, and no run is created at all.
        def old(payload, checkout):
            if "engine" in payload:
                raise sweep_worker.Refused(400, "unknown field(s): engine")
            return None
        with mock.patch.object(sweep_worker, "_requested_engine", old), propagation(True):
            answer = self.free_sweep()
        self.assertNotEqual(answer.headers.get("Location", ""), "/running")
        self.assertEqual(self.store.all(), [])

    def paid(self, attested):
        render = None
        with apify() as (_seen, check):
            render = render_app(self.url, check_token=check)
            with engine_env("v2"):
                client = paid_visitor(render, self.store)
            with engine_env(None), propagation(True), mock.patch.object(
                    worker_client, "plan",
                    lambda *a, **kw: dict(PLAN, **({"profile_engine": attested}
                                                   if attested else {}))):
                client.get("/configure")
                return client.post("/run")

    def test_a_paid_run_on_a_mismatched_plan_never_starts(self):
        for attested in ("v1", None):
            with self.subTest(attested=attested):
                answer = self.paid(attested)
                self.assertNotEqual(answer.status_code, 302)
                self.assertEqual(self.store.all(), [])

    def test_a_paid_run_on_a_matching_plan_starts_stamped_v2(self):
        answer = self.paid("v2")
        self.assertEqual(answer.status_code, 302, answer.get_data(as_text=True)[:400])
        self.assertEqual(self.run_engine(), "v2")


if __name__ == "__main__":
    unittest.main()
