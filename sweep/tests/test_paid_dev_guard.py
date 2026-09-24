"""Search Engine V2-C0: developer tooling cannot spend without two keys.

The B3 incident, reproduced harmlessly and then refused: a temporary profile
that overrides only free settings inherits the default paid SITES, the shell
has no APIFY_TOKEN, `--yes` skips the prompt, and load_dotenv() restores a
token from a .env. Through `python scraper.py` (production, unchanged) that
spends. Through bench/paid_guard.py it stops before a client exists unless
--allow-paid AND SWEEP_ALLOW_PAID_BENCH=1 are both present.

NO REAL CREDENTIAL AND NO REAL CLIENT. apify-client 3.x sends through impit, a
Rust HTTP stack that Python socket patches never see, so denying sockets is
not what keeps these tests from spending. What does: the repository's .env is
never opened (dotenv.main.find_dotenv answers a fixture), every APIFY_TOKEN* is
removed from the environment, and apify_client.ApifyClient is a recorder. A
mutation that deleted the guard would reach a fake client holding a fake
token. Child processes get the same through SANDBOX, and are not started
unless the sandbox proves itself first.
"""
import ast
import contextlib
import copy
import glob
import io
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

import scraper
import telemetry
from bench import paid_guard
from sweep.tests import test_search_v2_shadow_eval as b3
from sweep.tests.test_paid_contract import FakeDataset, FakeRun, FakeRunClient

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "apify_api_C0FIXTURE0000000000000000"       # not a real token
DEFAULT_SITES = copy.deepcopy(scraper.SITES)        # what an unset SITES inherits
LINKEDIN = "curious_coder/linkedin-jobs-scraper"
INDEED = "misceres/indeed-scraper"

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def deny(*a, **kw):
        raise AssertionError("a C0 test tried to open a socket")
    socket.socket.connect = deny
    socket.create_connection = deny


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


class Recorder:
    """apify_client.ApifyClient's stand-in: every client built, every start."""

    def __init__(self):
        self.tokens, self.starts = [], []

    def __call__(self, token=None, **kw):
        self.tokens.append(token)
        return _Client(self)


class _Client:
    def __init__(self, rec):
        self.rec = rec

    def actor(self, actor_id):
        return _Actor(self.rec, actor_id)

    def run(self, run_id):
        return FakeRunClient()

    def dataset(self, dataset_id):
        return FakeDataset()

    def user(self):
        raise RuntimeError("no account in tests")    # account_usage_usd -> None


class _Actor:
    def __init__(self, rec, actor_id):
        self.rec, self.actor_id = rec, actor_id

    def start(self, *, run_input=None, run_timeout=None,
              max_total_charge_usd=None, **kw):
        self.rec.starts.append({"actor": self.actor_id, "run_input": run_input,
                                "max_total_charge_usd": max_total_charge_usd})
        return FakeRun()


class Result:
    def __init__(self, code, log, rec, token_after, out_files, credential_calls,
                 restored):
        self.code, self.log, self.rec = code, log, rec
        self.token_after = token_after
        self.files = out_files
        self.credential_calls = credential_calls
        self.restored = restored

    @property
    def blocked(self):
        return paid_guard.BLOCKED in str(self.code or "")

    @property
    def text(self):
        return self.log + str(self.code or "")


def engine(argv, env=None, guarded=True, sites=None, registry=None, shell_token=None,
           dotenv_token=TOKEN, before=None):
    """One engine invocation, in-process. guarded: `python -m bench.paid_guard
    <argv>`; otherwise production, `python scraper.py <argv>`. The profile is
    the incident's: SITES inherited unless given, no boards, no feeds."""
    out, fixtures = tempfile.mkdtemp(), tempfile.mkdtemp()
    dotenv_path = os.path.join(fixtures, ".env")
    with open(dotenv_path, "w") as fh:
        fh.write(f"APIFY_TOKEN={dotenv_token}\n" if dotenv_token else "")
    rec, calls = Recorder(), []
    real_require = scraper._require_token

    def spy_require():
        calls.append(True)
        return real_require()
    saved = (sys.argv, scraper.SITES, scraper.ATS_BOARDS, scraper.FEEDS,
             scraper.SETTINGS["output_dir"])
    code, log = None, io.StringIO()
    try:
        scraper.SITES = copy.deepcopy(DEFAULT_SITES if sites is None else sites)
        scraper.ATS_BOARDS = copy.deepcopy(registry or {})
        scraper.FEEDS = {}
        scraper.SETTINGS["output_dir"] = out
        with mock.patch.dict(os.environ), \
                mock.patch("apify_client.ApifyClient", rec), \
                mock.patch("dotenv.main.find_dotenv", return_value=dotenv_path), \
                mock.patch.object(scraper, "_require_token", spy_require), \
                mock.patch("time.sleep"), \
                b3._served(b3.routes()), \
                contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            for name in [n for n in os.environ if n.startswith("APIFY_TOKEN")]:
                del os.environ[name]
            for name in (paid_guard.FLAG, telemetry.FLAG, "JOB_PROFILE",
                         scraper.READY_FLAG):
                os.environ.pop(name, None)
            if shell_token:
                os.environ["APIFY_TOKEN"] = shell_token
            os.environ.update(env or {})
            if before:
                before()
            try:
                if guarded:
                    paid_guard.main(argv)
                else:
                    sys.argv = ["scraper.py", *argv]
                    scraper.main()
            except SystemExit as exc:
                code = exc.code if exc.code is not None else 0
            token_after = os.environ.get("APIFY_TOKEN")
            restored = scraper._require_token is spy_require
        files = sorted(os.path.relpath(p, out) for p in glob.glob(
            os.path.join(out, "**", "*"), recursive=True) if os.path.isfile(p))
        return Result(code, log.getvalue(), rec, token_after, files, len(calls),
                      restored)
    finally:
        (sys.argv, scraper.SITES, scraper.ATS_BOARDS, scraper.FEEDS,
         scraper.SETTINGS["output_dir"]) = saved
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(fixtures, ignore_errors=True)


BOTH = {paid_guard.FLAG: "1"}
INCIDENT = ["--", "--yes"]                            # what B3's check ran
SMALL = ["--", "--limit", "1", "--yes"]               # one LinkedIn + one Indeed
LINKEDIN_ONE = ["--", "--site", "linkedin", "--limit", "1", "--yes"]


# ---------------------------------------------------------------------------
# 1. The two-key rule itself
# ---------------------------------------------------------------------------
class TwoKeyRule(unittest.TestCase):

    def check(self, cli, env):
        try:
            paid_guard.require_paid_bench_permission(cli, env)
            return None
        except paid_guard.PaidBenchBlocked as exc:
            return str(exc.code)

    def test_neither_key_is_blocked(self):
        self.assertIn(paid_guard.BLOCKED, self.check(False, {}))

    def test_the_environment_key_alone_is_blocked(self):
        msg = self.check(False, BOTH)
        self.assertIn("Missing: --allow-paid.", msg)

    def test_the_cli_key_alone_is_blocked(self):
        msg = self.check(True, {})
        self.assertIn("Missing: SWEEP_ALLOW_PAID_BENCH=1.", msg)

    def test_both_keys_pass(self):
        self.assertIsNone(self.check(True, BOTH))

    def test_only_the_conventional_true_values_count(self):
        for value in ("1", "true", "yes", "on", "TRUE", " 1 ", "On"):
            self.assertIsNone(self.check(True, {paid_guard.FLAG: value}), value)
        for value in ("0", "false", "no", "off", "", " ", "garbage", "2",
                      "enabled", "y", "1.0"):
            self.assertIsNotNone(self.check(True, {paid_guard.FLAG: value}), value)

    def test_the_cli_key_must_be_literally_true(self):
        for value in (1, "yes", "--allow-paid", None):
            self.assertIsNotNone(self.check(value, BOTH), value)

    def test_a_token_is_not_permission(self):
        env = {"APIFY_TOKEN": TOKEN, "APIFY_TOKEN_2": TOKEN + "2"}
        for cli in (False, True):
            msg = self.check(cli, env)
            self.assertIn(paid_guard.BLOCKED, msg)
            self.assertNotIn(TOKEN, msg)

    def test_the_decision_never_reads_a_token(self):
        class Watched(dict):
            read = []

            def get(self, key, default=None):
                self.read.append(key)
                return super().get(key, default)

            def __getitem__(self, key):
                self.read.append(key)
                return super().__getitem__(key)
        env = Watched(APIFY_TOKEN=TOKEN, **BOTH)
        self.check(True, env)
        self.assertEqual(env.read, [paid_guard.FLAG])

    def test_the_message_names_both_keys_and_says_both(self):
        for msg in (self.check(False, {}), self.check(False, BOTH),
                    self.check(True, {})):
            self.assertIn("--allow-paid", msg)
            self.assertIn("SWEEP_ALLOW_PAID_BENCH=1", msg)
            self.assertIn("Both are required", msg)


# ---------------------------------------------------------------------------
# 2. Preflight and exposure
# ---------------------------------------------------------------------------
class Preflight(unittest.TestCase):

    PLAN = {"linkedin": {"actor": LINKEDIN, "starts": 1, "depth": 15,
                         "ceiling_usd": Decimal("0.046")}}
    MIXED = dict(PLAN, indeed={"actor": INDEED, "starts": 2, "depth": 15,
                               "ceiling_usd": None})

    def test_the_ceiling_is_the_current_b1_value(self):
        self.assertEqual(scraper.max_charge_usd("linkedin", 15), Decimal("0.046"))
        self.assertIsNone(scraper.max_charge_usd("indeed", 15))

    def test_worst_case(self):
        self.assertEqual(paid_guard.worst_case(self.PLAN), Decimal("0.046"))
        self.assertIsNone(paid_guard.worst_case(self.MIXED))

    def test_the_preflight_says_everything_before_anything_starts(self):
        text = paid_guard.preflight(self.MIXED, Decimal("0.50"), Decimal("0.10"))
        for part in ("nothing has been started", "linkedin", LINKEDIN, "indeed",
                     INDEED, "1 start(s)", "2 start(s)", "depth 15",
                     "$0.046 per start, provider-enforced",
                     "NONE — not provider-bounded", "UNBOUNDED",
                     "exposure before: $0.10", "limit (--max-usd): $0.50"):
            self.assertIn(part, text)
        text = paid_guard.preflight(self.PLAN, Decimal("0.50"), Decimal("0.10"))
        self.assertIn("worst case this run: $0.046", text)
        self.assertIn("after: $0.146", text)

    def test_the_limit_holds_the_worst_case_including_what_was_spent(self):
        ok = lambda p, m, e="0": paid_guard.authorize(  # noqa: E731
            p, True, Decimal(m), Decimal(e), BOTH)
        ok(self.PLAN, "0.05")
        ok(self.PLAN, "0.50", "0.454")
        for plan, limit, exposed in ((self.PLAN, "0.045", "0"),
                                     (self.PLAN, "0.50", "0.455"),
                                     (self.MIXED, "100", "0")):
            with self.assertRaises(paid_guard.PaidBenchBlocked):
                ok(plan, limit, exposed)

    def test_keys_are_checked_before_the_limit(self):
        with self.assertRaises(paid_guard.PaidBenchBlocked) as caught:
            paid_guard.authorize(self.PLAN, False, Decimal("1"), Decimal(0), BOTH)
        self.assertIn("Missing: --allow-paid", str(caught.exception.code))


# ---------------------------------------------------------------------------
# 3. The guarded engine, in-process: the brief's matrix
# ---------------------------------------------------------------------------
class GuardedEngine(unittest.TestCase):

    def assertNothingPaidBegan(self, got):
        self.assertEqual(got.rec.tokens, [], "a client was built")
        self.assertEqual(got.rec.starts, [], "an actor was started")
        self.assertEqual(got.credential_calls, 0, "the credential step ran")
        self.assertIsNone(got.token_after, "load_dotenv restored a token")
        self.assertNotIn(TOKEN, got.text)

    def test_free_only_needs_no_key(self):
        got = engine(["--", "--site", "free", "--yes"], sites={
            k: {**v, "enabled": False} for k, v in DEFAULT_SITES.items()},
            registry=b3.REGISTRY)
        self.assertIn(got.code, (None, 0), got.text)
        self.assertNotIn("PAID PREFLIGHT", got.log)
        self.assertTrue(any(f.startswith("jobs_") for f in got.files), got.files)
        self.assertNothingPaidBegan(got)

    def test_explicit_free_needs_no_key_even_with_paid_sites_inherited(self):
        got = engine(["--", "--site", "free", "--yes"], registry=b3.REGISTRY)
        self.assertIn(got.code, (None, 0), got.text)
        self.assertNotIn("PAID PREFLIGHT", got.log)
        self.assertNothingPaidBegan(got)

    def test_inherited_paid_sites_with_no_key_are_blocked(self):
        got = engine(INCIDENT)
        self.assertTrue(got.blocked, got.text)
        self.assertIn("Missing: --allow-paid and SWEEP_ALLOW_PAID_BENCH=1", got.text)
        self.assertNothingPaidBegan(got)
        self.assertEqual(got.files, [], "the engine wrote something")

    def test_the_preflight_shows_the_inherited_plan(self):
        got = engine(INCIDENT)
        plan = {s: scraper.plan_for_site(s, b3_args()) for s in ("linkedin", "indeed")}
        self.assertIn(f"{len(plan['linkedin']):>3} start(s)", got.log)
        self.assertIn(f"{len(plan['indeed']):>3} start(s)", got.log)
        self.assertIn(LINKEDIN, got.log)
        self.assertIn(INDEED, got.log)
        self.assertIn("worst case this run: UNBOUNDED", got.log)

    def test_the_environment_key_alone_is_blocked(self):
        got = engine(INCIDENT, env=BOTH)
        self.assertTrue(got.blocked)
        self.assertIn("Missing: --allow-paid.", got.text)
        self.assertNothingPaidBegan(got)

    def test_the_cli_key_alone_is_blocked(self):
        got = engine(["--allow-paid", *INCIDENT])
        self.assertTrue(got.blocked)
        self.assertIn("Missing: SWEEP_ALLOW_PAID_BENCH=1.", got.text)
        self.assertNothingPaidBegan(got)

    def test_a_garbage_environment_value_is_blocked(self):
        for value in ("0", "false", "off", "", "garbage"):
            got = engine(["--allow-paid", *INCIDENT], env={paid_guard.FLAG: value})
            self.assertTrue(got.blocked, value)
            self.assertNothingPaidBegan(got)

    def test_a_token_in_the_shell_is_not_permission(self):
        got = engine(INCIDENT, shell_token=TOKEN,
                     env={"APIFY_TOKEN_2": TOKEN + "2"})
        self.assertTrue(got.blocked)
        self.assertEqual(got.rec.tokens, [])
        self.assertEqual(got.rec.starts, [])
        self.assertNotIn(TOKEN, got.text)

    def test_both_keys_run_the_plan_under_the_b1_ceiling(self):
        got = engine(["--allow-paid", *SMALL], env=BOTH)
        self.assertIn(got.code, (None, 0), got.text)
        self.assertEqual(got.rec.tokens, [TOKEN])      # from the fixture .env
        self.assertEqual([s["actor"] for s in got.rec.starts], [LINKEDIN, INDEED])
        linkedin, indeed = got.rec.starts
        self.assertEqual(linkedin["max_total_charge_usd"], Decimal("0.046"))
        self.assertEqual(linkedin["run_input"]["limitPerSource"], 15)
        self.assertEqual(linkedin["run_input"]["count"], 15)
        self.assertIsNone(indeed["max_total_charge_usd"])
        self.assertIn("PAID PREFLIGHT", got.log)
        self.assertLess(got.log.index("PAID PREFLIGHT"), got.log.index(f"({LINKEDIN})"))
        self.assertNotIn(TOKEN, got.text)

    def test_within_the_limit_runs_and_over_it_does_not(self):
        got = engine(["--allow-paid", "--max-usd", "0.05", *LINKEDIN_ONE], env=BOTH)
        self.assertIn(got.code, (None, 0), got.text)
        self.assertEqual(len(got.rec.starts), 1)
        for over in (["--max-usd", "0.045"],
                     ["--max-usd", "0.50", "--exposed-usd", "0.455"]):
            got = engine(["--allow-paid", *over, *LINKEDIN_ONE], env=BOTH)
            self.assertTrue(got.blocked, over)
            self.assertNothingPaidBegan(got)

    def test_a_site_without_a_provider_ceiling_cannot_run_under_a_limit(self):
        got = engine(["--allow-paid", "--max-usd", "0.50", "--",
                      "--site", "indeed", "--limit", "1", "--yes"], env=BOTH)
        self.assertTrue(got.blocked)
        self.assertIn("no provider-side charge ceiling", got.text)
        self.assertNothingPaidBegan(got)

    def test_dry_run_and_demo_need_no_key(self):
        got = engine(["--", "--dry-run"])
        self.assertIn(got.code, (None, 0), got.text)
        self.assertIn("dry run — no actors executed", got.log)
        self.assertNothingPaidBegan(got)

    def test_a_plan_that_turns_paid_after_the_check_is_blocked(self):
        """The preflight saw no paid search (stubbed), the engine then planned
        one: the credential step refuses, with or without the keys."""
        for keys, env in (([], {}), (["--allow-paid"], BOTH)):
            with mock.patch.object(paid_guard, "paid_plan", return_value={}):
                got = engine([*keys, *INCIDENT], env=env)
            self.assertTrue(got.blocked, got.text)
            self.assertIn("credential step", got.text)
            self.assertEqual(got.rec.tokens, [])
            self.assertEqual(got.rec.starts, [])
            self.assertIsNone(got.token_after)

    def test_the_credential_step_is_restored_afterwards(self):
        for got in (engine(["--", "--site", "free", "--yes"], registry=b3.REGISTRY),
                    engine(["--allow-paid", *SMALL], env=BOTH)):
            self.assertTrue(got.restored)


def b3_args():
    class Args:
        keywords, limit, test = None, None, False
    return Args()


# ---------------------------------------------------------------------------
# 4. The incident's dotenv path, reproduced and refused
# ---------------------------------------------------------------------------
class DotenvIncident(unittest.TestCase):

    def test_control_production_restores_the_token_and_spends(self):
        """Not a guard failure: this is what B3's check did, with fakes."""
        got = engine(SMALL[1:], guarded=False)
        self.assertIsNone(got.code, got.text)
        self.assertEqual(got.token_after, TOKEN, "load_dotenv did not restore it")
        self.assertEqual(got.rec.tokens, [TOKEN])
        self.assertEqual(len(got.rec.starts), 2)

    def test_the_same_invocation_through_the_guard_is_blocked(self):
        got = engine(SMALL)
        self.assertTrue(got.blocked)
        self.assertEqual(got.rec.tokens, [])
        self.assertIsNone(got.token_after)

    def test_a_token_already_restored_by_load_dotenv_is_still_not_permission(self):
        def restore():
            from dotenv import load_dotenv
            load_dotenv()
            assert os.environ.get("APIFY_TOKEN") == TOKEN
        for keys, env in (([], {}), ([], BOTH), (["--allow-paid"], {})):
            got = engine([*keys, *SMALL], env=env, before=restore)
            self.assertTrue(got.blocked, (keys, env))
            self.assertEqual(got.rec.tokens, [])
            self.assertEqual(got.rec.starts, [])
            self.assertNotIn(TOKEN, got.text)


# ---------------------------------------------------------------------------
# 5. Production is untouched and never asks
# ---------------------------------------------------------------------------
PRODUCTION = ("scraper.py", "config.py", "telemetry.py", "deploy/sweep_worker.py",
              "rescore_from_apify.py", "auto-apply/make_shortlist.py",
              *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "sweep").glob("*.py")),
              *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "sources").glob("*.py")))


class ProductionIsolation(unittest.TestCase):

    def test_production_code_does_not_know_the_guard(self):
        for rel in PRODUCTION:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for name in ("paid_guard", "SWEEP_ALLOW_PAID_BENCH", "allow-paid",
                         "allow_paid", "from bench", "import bench"):
                self.assertNotIn(name, text, f"{rel} mentions {name}")

    def test_a_production_paid_run_needs_no_developer_key(self):
        got = engine(SMALL[1:], guarded=False, shell_token=TOKEN)
        self.assertIsNone(got.code, got.text)
        self.assertEqual(got.rec.tokens, [TOKEN])
        self.assertEqual([s["actor"] for s in got.rec.starts], [LINKEDIN, INDEED])
        self.assertNotIn("PAID PREFLIGHT", got.log)
        self.assertNotIn(paid_guard.BLOCKED, got.log)

    def test_production_never_consults_the_guard(self):
        def refuse(*a, **kw):
            raise AssertionError("production consulted the developer guard")
        with mock.patch.object(paid_guard, "require_paid_bench_permission", refuse), \
                mock.patch.object(paid_guard, "authorize", refuse), \
                mock.patch.object(paid_guard, "env_allows", refuse):
            for value in (None, "0", "1"):
                got = engine(SMALL[1:], guarded=False, shell_token=TOKEN,
                             env={} if value is None else {paid_guard.FLAG: value})
                self.assertIsNone(got.code, got.text)
                self.assertEqual(len(got.rec.starts), 2, value)

    def test_the_guard_changes_nothing_the_actor_is_given(self):
        """Same starts, inputs and ceilings as production: B1's depth fields,
        its maxTotalChargeUsd and Indeed's input all pass through untouched."""
        guarded = engine(["--allow-paid", *SMALL], env=BOTH)
        production = engine(SMALL[1:], guarded=False)
        self.assertEqual(guarded.rec.starts, production.rec.starts)
        expect = scraper.build_input("linkedin", scraper.effective_search(
            "linkedin", scraper.plan_for_site("linkedin", b3_args())[0]))
        self.assertEqual(production.rec.starts[0]["run_input"], expect)
        self.assertEqual(expect["limitPerSource"], 15)
        self.assertEqual(expect["count"], 15)

    def test_the_worker_and_the_console_launch_the_engine_directly(self):
        from deploy import sweep_worker
        from sweep import runs
        seen = {}

        class FakePopen:
            def __init__(self, argv, cwd=None, env=None, **kw):
                seen["argv"], seen["env"] = argv, env
        run_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, run_dir, True)
        with mock.patch.object(sweep_worker.subprocess, "Popen", FakePopen), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(paid_guard.FLAG, None)
            sweep_worker.default_spawn("abc", run_dir, "beta_abc", str(ROOT), TOKEN)
        self.assertEqual(seen["argv"][1:], ["scraper.py", "--profile", "beta_abc", "--yes"])
        self.assertEqual(seen["env"]["APIFY_TOKEN"], TOKEN)     # BYOK, as before
        self.assertNotIn(paid_guard.FLAG, seen["env"])
        runs.start("someone", env={}, popen=FakePopen)
        self.assertEqual(seen["argv"][1:], ["scraper.py", "--profile", "someone", "--yes"])


# ---------------------------------------------------------------------------
# 6. Reachability: every file that can launch the engine or build a client is
#    classified, and every developer one goes through the guard.
# ---------------------------------------------------------------------------
# Direct reach (the engine, its credential step, a client, an actor start) and
# indirect reach through the production launchers (the worker's spawn, the
# console's sweep.runs), so a tool cannot borrow either unclassified.
REACH_PATTERN = re.compile(
    r"""["']scraper\.py["']|scraper\.main\(|scrape_search\(|_require_token\("""
    r"""|ApifyClient\(|\.start\(run_input|\.call\(run_input|engine_argv\("""
    r"""|default_spawn|sweep\.runs\b|\bruns\.start""")
REACH = {
    # production: the engine and what launches it. Not guarded, by design.
    "scraper.py": "production",
    "deploy/sweep_worker.py": "production",
    "sweep/runs.py": "production",
    "sweep/plan.py": "production",              # --dry-run --json only
    "sweep/app.py": "production",               # account reads; starts nothing
    "rescore_from_apify.py": "production",      # reads finished runs; starts nothing
    "auto-apply/make_shortlist.py": "production",   # --scrape: the user's own sweep
    # developer tooling
    "bench/paid_guard.py": "guard",
    "bench/search_v2_paid_probe.py": "guarded",
    "bench/search_v2_shadow_b3.py": "guarded",
    "bench/search_v2_results_ready.py": "unreachable",  # B5 test harness: fixtures, stubbed client
    # V2-C1's and V2-C2's offline benchmarks: scraper.main() in a child of
    # their own, against the test modules' scripted Apify stand-ins, with
    # _require_token patched and sockets denied. No real client exists there.
    "bench/search_v2_paid_overhead.py": "unreachable",
    "bench/search_v2_paid_concurrency.py": "unreachable",
    "bench/search_v2_free_audit.py": "unreachable",     # parses scraper.py's source text
    "config.py": "unreachable",                         # a self-test string
    "profiles/global_all.py": "unreachable",            # a comment
}


def _tracked_sources():
    # Untracked (not ignored) files too: a new tool is scanned before it is
    # committed, not after. Tracked-only is how V2-C1's overhead benchmark
    # reached main unclassified — the suite ran while the file was untracked.
    out = subprocess.run(["git", "ls-files", "--cached", "--others",
                          "--exclude-standard", "*.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.split()
    return [p for p in out if not re.search(r"(^|/)tests?/|(^|/)test_[^/]*$|^docs/", p)]


class Reachability(unittest.TestCase):

    def test_every_file_that_can_reach_paid_code_is_classified(self):
        files = set(_tracked_sources()) | {"bench/paid_guard.py",
                                           "bench/search_v2_paid_probe.py"}
        matched = {p for p in files if (ROOT / p).exists()
                   and REACH_PATTERN.search((ROOT / p).read_text(encoding="utf-8"))}
        self.assertEqual(sorted(matched - set(REACH)), [],
                         "classify these in REACH (and the C0 doc)")
        self.assertEqual(sorted(set(REACH) - matched), [], "stale REACH entries")

    def test_every_guarded_tool_launches_through_the_guard_only(self):
        """Code, not prose: no engine call and no "scraper.py" argument in a
        guarded tool, and at least one launch through engine_argv."""
        for rel, kind in REACH.items():
            if kind != "guarded":
                continue
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            calls = [ast.unparse(n.func) for n in ast.walk(tree)
                     if isinstance(n, ast.Call)]
            self.assertIn("paid_guard.engine_argv", calls, rel)
            for name in ("scraper.main", "scraper.scrape_search",
                         "scraper._require_token"):
                self.assertNotIn(name, calls, rel)
            self.assertFalse([n for n in ast.walk(tree) if isinstance(n, ast.Constant)
                              and n.value == "scraper.py"], rel)

    def test_the_guard_launches_the_engine_as_a_guarded_child(self):
        argv = paid_guard.engine_argv(["--site", "free"], python="py")
        self.assertEqual(argv, ["py", "-u", "-m", "bench.paid_guard", "--", "--site", "free"])
        argv = paid_guard.engine_argv(["--yes"], allow_paid=True, max_usd="0.5",
                                      exposed_usd="0.1", python="py")
        self.assertEqual(argv, ["py", "-u", "-m", "bench.paid_guard", "--allow-paid",
                                "--max-usd", "0.5", "--exposed-usd", "0.1", "--", "--yes"])
        for loose in (1, "yes", None):
            self.assertNotIn("--allow-paid",
                             paid_guard.engine_argv([], allow_paid=loose))


# ---------------------------------------------------------------------------
# 7. Real child processes, in a sandbox
# ---------------------------------------------------------------------------
SANDBOX = r'''"""V2-C0 test sandbox: no network, no real Apify client, no real .env."""
import json, os, runpy, socket, sys, time
def _deny(*a, **k):
    raise OSError("c0 sandbox: network denied")
socket.socket.connect = _deny
socket.create_connection = _deny
time.sleep = lambda *_: None
import dotenv.main
dotenv.main.find_dotenv = lambda *a, **k: os.environ["C0_SANDBOX_DOTENV"]
def log(event, **kw):
    with open(os.environ["C0_SANDBOX_LOG"], "a") as fh:
        fh.write(json.dumps(dict(kw, event=event)) + "\n")
class Run:
    id, build_id, started_at, finished_at = "run_c0", "build_c0", "", ""
    status, usage_total_usd, default_dataset_id = "SUCCEEDED", 0.0, "ds_c0"
class RunClient:
    def get(self): return Run()
    def abort(self): pass
class Dataset:
    def iterate_items(self):
        return iter([{"title": "Software Engineer", "company": "Acme",
                      "location": "Bengaluru", "url": "https://example.invalid/1",
                      "description": "react node"}])
class Actor:
    def __init__(self, name): self.name = name
    def start(self, *, run_input=None, run_timeout=None, max_total_charge_usd=None, **kw):
        log("start", actor=self.name, run_input=run_input,
            max_total_charge_usd=None if max_total_charge_usd is None
            else str(max_total_charge_usd))
        return Run()
class SandboxClient:
    def __init__(self, token=None, **kw):
        log("client", fixture_token=token == os.environ["C0_SANDBOX_TOKEN"])
    def actor(self, name): return Actor(name)
    def run(self, run_id): return RunClient()
    def dataset(self, ds): return Dataset()
    def user(self): raise RuntimeError("c0 sandbox: no account")
import apify_client
apify_client.ApifyClient = SandboxClient
log("sandbox")
args = sys.argv[1:]
sys.path.insert(0, os.getcwd())
# The fixture profile lives outside the repository: profiles/ is never written.
import profiles
profiles.__path__.append(os.environ["C0_SANDBOX_PROFILES"])
if args[0] == "-m":
    sys.argv = [args[1], *args[2:]]
    runpy.run_module(args[1], run_name="__main__", alter_sys=True)
else:
    sys.argv = args
    runpy.run_path(args[0], run_name="__main__")
'''


class Subprocess(unittest.TestCase):
    """The parent/child boundary with real processes. The parent's --allow-paid
    reaches the child only as the literal flag; SWEEP_ALLOW_PAID_BENCH only by
    inheritance. Lose either and the child refuses."""

    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp())
        cls.boot = cls.work / "boot.py"
        cls.boot.write_text(SANDBOX)
        cls.dotenv = cls.work / ".env"
        cls.dotenv.write_text(f"APIFY_TOKEN={TOKEN}\n")
        cls.name = f"c0test_{secrets.token_hex(4)}"
        (cls.work / "profiles").mkdir()
        cls.profile = cls.work / "profiles" / f"{cls.name}.py"
        cls.profile.write_text(
            '"""TEMPORARY V2-C0 test profile, the B3 incident\'s shape: only free '
            'settings overridden, paid SITES inherited."""\n'
            f"ATS_BOARDS = {{}}\nFEEDS = {{}}\n"
            f"SETTINGS = {{'output_dir': {str(cls.work / 'out')!r}}}\n")
        proof = cls.child([], probe=True)
        if proof.returncode or '"sandbox"' not in proof.log:
            cls.tearDownClass()
            raise unittest.SkipTest(f"sandbox did not prove itself: {proof.stderr}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    @classmethod
    def child(cls, argv, env=None, probe=False):
        """Run argv (as a tool would build it) inside the sandbox."""
        log = cls.work / f"log_{secrets.token_hex(4)}.jsonl"
        # Fresh outputs per child: the engine's resume ledger (.done_combos)
        # would otherwise skip a search an earlier child completed.
        shutil.rmtree(cls.work / "out", ignore_errors=True)
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("APIFY_TOKEN", "SWEEP_")) and k != "JOB_PROFILE"}
        base.update(C0_SANDBOX_DOTENV=str(cls.dotenv), C0_SANDBOX_LOG=str(log),
                    C0_SANDBOX_TOKEN=TOKEN,
                    C0_SANDBOX_PROFILES=str(cls.work / "profiles"), **(env or {}))
        if probe:
            script = cls.work / "probe.py"
            script.write_text(
                "import apify_client, dotenv.main, os, socket\n"
                "assert apify_client.ApifyClient.__name__ == 'SandboxClient'\n"
                "assert socket.create_connection.__name__ == '_deny'\n"
                "assert socket.socket.connect.__name__ == '_deny'\n"
                "assert dotenv.main.find_dotenv() == os.environ['C0_SANDBOX_DOTENV']\n")
            argv = [str(script)]
        # A tool's argv starts with the interpreter (and -u); the sandbox's
        # bootstrap goes between the interpreter and what it runs.
        if argv[:1] == [sys.executable]:
            argv = argv[1:]
        if argv[:1] == ["-u"]:
            argv = argv[1:]
        cmd = [sys.executable, "-u", str(cls.boot), *argv]
        done = subprocess.run(cmd, cwd=ROOT, env=base, capture_output=True,
                              text=True, timeout=120)
        done.log = log.read_text() if log.exists() else ""
        done.events = [json.loads(line) for line in done.log.splitlines()]
        done.text = done.stdout + done.stderr + done.log
        return done

    def tool(self, allow_paid, env, scraper_args=("--limit", "1", "--yes"), **kw):
        return self.child(paid_guard.engine_argv(
            ["--profile", self.name, *scraper_args], allow_paid=allow_paid, **kw), env)

    def clients(self, got):
        return [e for e in got.events if e["event"] == "client"]

    def starts(self, got):
        return [e for e in got.events if e["event"] == "start"]

    def test_control_the_incident_command_spends_in_the_sandbox(self):
        got = self.child([sys.executable, "scraper.py", "--profile", self.name,
                          "--limit", "1", "--yes"])
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(self.clients(got), [{"event": "client", "fixture_token": True}])
        self.assertEqual([s["actor"] for s in self.starts(got)], [LINKEDIN, INDEED])

    def test_through_the_guard_with_no_key_it_is_blocked(self):
        got = self.tool(False, {})
        self.assertNotEqual(got.returncode, 0)
        self.assertIn(paid_guard.BLOCKED, got.stderr)
        self.assertIn("PAID PREFLIGHT", got.stdout)
        self.assertEqual(self.clients(got), [])

    def test_the_environment_key_alone_is_blocked(self):
        got = self.tool(False, BOTH)
        self.assertIn("Missing: --allow-paid.", got.stderr)
        self.assertEqual(self.clients(got), [])

    def test_the_cli_key_alone_is_blocked(self):
        """Also what a tool gets when it drops SWEEP_* from its child's
        environment, as B3's harness does: a key lost is a refusal."""
        got = self.tool(True, {})
        self.assertNotEqual(got.returncode, 0)
        self.assertIn("Missing: SWEEP_ALLOW_PAID_BENCH=1.", got.stderr)
        self.assertEqual(self.clients(got), [])

    def test_both_keys_survive_the_boundary(self):
        got = self.tool(True, BOTH, ("--site", "linkedin", "--limit", "1", "--yes"),
                        max_usd="0.05", exposed_usd="0")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(self.clients(got), [{"event": "client", "fixture_token": True}])
        (start,) = self.starts(got)
        self.assertEqual(start["actor"], LINKEDIN)
        self.assertEqual(start["max_total_charge_usd"], "0.046")
        self.assertEqual((start["run_input"]["limitPerSource"],
                          start["run_input"]["count"]), (15, 15))
        self.assertIn("worst case this run: $0.046", got.stdout)

    def test_the_token_never_reaches_output(self):
        for got in (self.tool(False, {}), self.tool(True, BOTH),
                    self.tool(True, BOTH, ("--site", "linkedin", "--limit", "1",
                                           "--yes"), max_usd="0.05")):
            self.assertNotIn(TOKEN, got.stdout + got.stderr)


if __name__ == "__main__":
    unittest.main()
