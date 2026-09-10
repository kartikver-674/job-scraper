import datetime
import io
import itertools
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from sweep import app as app_module

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REAL_ROOT = app_module.REPO_ROOT
_TEMP_ROOT = None

# The three places in this repo a test can do real damage: the developer's
# API keys, a finished sweep's only copy of its results, and the profiles the
# engine runs from.
_NO_WRITE = tuple(os.path.join(_REAL_ROOT, name)
                  for name in (".env", "output", "profiles"))


def _forbid_real_writes(event, args):
    """Refuse, in the offending test, any write to those three.

    Detection after the fact is not enough: the write that started this was
    IDEMPOTENT — the junk key it left in .env was already there, so a digest
    taken before and after the suite matched while the file was being
    rewritten on every run. Failing at the open() call names the test that
    did it, which a teardown check cannot.
    """
    if event != "open" or len(args) < 2:
        return
    path, mode = args[0], args[1]
    # "r+" writes, so a mode is a write unless it says only read.
    if not isinstance(path, str) or not mode:
        return
    if not any(c in str(mode) for c in "wax+"):
        return
    full = os.path.abspath(path)
    for target in _NO_WRITE:
        if full == target or full.startswith(target + os.sep):
            raise AssertionError(
                f"this test tried to WRITE the repo's real {os.path.relpath(full, _REAL_ROOT)}. "
                "Inject env_path/output_dir into create_app instead.")


def setUpModule():
    """No test may reach the repo's own .env, output/ or profiles/.

    create_app defaults env_path and output_dir to REPO_ROOT, so a fixture
    that merely FORGETS to inject one writes to the developer's real files —
    and one did: test_connecting_a_key_afterwards_leaves_the_free_path posts
    a token with no env_path, and default_write_env duly put
    APIFY_TOKEN=apify_api_xxx into the repo's .env, where it sat as a junk
    key the app counted and tried to verify on every screen.

    Injecting the path is the fix for that fixture. This is the fix for the
    next one: every path create_app derives from REPO_ROOT now lands in a
    temp tree, so forgetting is no longer destructive.
    """
    global _TEMP_ROOT
    sys.addaudithook(_forbid_real_writes)
    _TEMP_ROOT = tempfile.mkdtemp(prefix="sweep-tests-root-")
    os.mkdir(os.path.join(_TEMP_ROOT, "profiles"))
    app_module.REPO_ROOT = _TEMP_ROOT


def tearDownModule():
    app_module.REPO_ROOT = _REAL_ROOT
    shutil.rmtree(_TEMP_ROOT, ignore_errors=True)


class Isolated(unittest.TestCase):
    """A test case whose APIFY_TOKEN* environment is put back afterwards.

    read_env_tokens() reconciles os.environ with .env deliberately — the
    engine inherits this process's environment, so a key deleted from the
    file has to disappear from both. That makes it a global this module
    writes to, and a fixture that leaves APIFY_TOKEN set changes what a
    LATER test's no_key_yet() decides. The failure lands in whichever test
    happens to run next, which is the worst possible place for it.
    """

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.dict(os.environ))
        # And its own REPO_ROOT, so profiles/ and output/ are per-test too.
        # Module-scoped, they were shared: POST /run writes the profile it
        # stamps the spend cap into, so one fixture that did not stub
        # write_profile left profiles/kanav.py behind and the next test's
        # "does this name clash" answered yes.
        root = tempfile.mkdtemp(prefix="sweep-test-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.mkdir(os.path.join(root, "profiles"))
        self.enterContext(mock.patch.object(app_module, "REPO_ROOT", root))


def alpine_scope(body):
    """The x-data attribute value as a real HTML parser sees it.

    `|tojson` escapes ' < & so its output can sit inside a SINGLE-quoted
    attribute; it leaves " alone. Interpolated into a double-quoted one, the
    attribute ends at the JSON's first " — so the browser reads x-data as
    '{ p: {' , Alpine fails to evaluate the component, and every binding on
    the screen is dead. That is silent: no error on the page, just a cost
    panel and a progress meter that never populate.
    """
    from html.parser import HTMLParser

    found = []

    class Scan(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key == "x-data":
                    found.append(value or "")

    Scan().feed(body)
    return found


class TestProfileNameCollision(Isolated):
    """POST /review wrote profiles/<name>.py with no existence check, so
    typing a name that already existed destroyed it — and /estimate rewrites
    the same file on every configure change, so there was no second chance.
    profiles/kartik_reachable.py carries hand-tuning from a real sweep, and
    an untracked profile has no git fallback at all."""

    def _app(self, existing=()):
        app = app_module.create_app(
            state={"derived": dict(DERIVED), "resume_text": "x"},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            profile_exists=lambda name: name in existing)
        app.config.update(TESTING=True)
        self.writes = []
        app.write_profile = lambda n, src: self.writes.append(n)
        return app

    def test_an_existing_name_is_refused_and_nothing_is_written(self):
        app = self._app(existing={"kartik_reachable"})
        r = app.test_client().post("/review", data={"name": "kartik_reachable"})
        self.assertEqual(r.status_code, 409)
        body = r.get_data(as_text=True)
        self.assertIn("already exists", body)
        self.assertEqual(self.writes, [])          # the profile survives

    def test_a_fresh_name_is_written_without_a_prompt(self):
        app = self._app(existing={"kartik_reachable"})
        r = app.test_client().post("/review", data={"name": "handtest_1"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.writes, ["handtest_1"])

    def test_an_explicit_confirmation_replaces_it(self):
        app = self._app(existing={"kartik_reachable"})
        r = app.test_client().post("/review", data={
            "name": "kartik_reachable", "overwrite": "yes"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.writes, ["kartik_reachable"])

    def test_the_refusal_offers_the_confirmation_control(self):
        app = self._app(existing={"kartik_reachable"})
        body = app.test_client().post(
            "/review", data={"name": "kartik_reachable"}).get_data(as_text=True)
        self.assertIn('name="overwrite"', body)


class TestEngineFlagAndPathHygiene(Isolated):
    """Two fixes the last round claimed were pinned and were not — nothing in
    the repo referenced either."""

    def test_json_without_dry_run_is_refused(self):
        # It used to be accepted and silently do nothing, so a caller
        # expecting machine-readable output got prose and no reason why.
        #
        # Deliberately NOT a subprocess. `scraper.py --json` is only harmless
        # BECAUSE this guard exists — without it the process falls through to
        # the real run path, so a test that spawned it would start a paid
        # sweep at the exact moment the guard regressed. Found the hard way:
        # mutating the guard to check this test hung the suite on a live
        # engine. Parsing argv in-process cannot reach a network call.
        sys.path.insert(0, REPO_ROOT)
        import scraper
        with mock.patch.object(sys, "argv", ["scraper.py", "--json"]):
            with self.assertRaises(SystemExit) as caught:
                with mock.patch("sys.stderr", io.StringIO()) as err:
                    scraper.parse_args()
        self.assertNotEqual(caught.exception.code, 0)
        self.assertIn("--json only applies with --dry-run", err.getvalue())

    def test_json_with_dry_run_is_accepted(self):
        sys.path.insert(0, REPO_ROOT)
        import scraper
        with mock.patch.object(sys, "argv",
                                ["scraper.py", "--dry-run", "--json"]):
            args = scraper.parse_args()
        self.assertTrue(args.json and args.dry_run)

    def test_repeated_profile_writes_do_not_grow_sys_path(self):
        # make_profile.load_config ran sys.path.insert on every call, and
        # /estimate calls it on every configure change.
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "derived": dict(DERIVED)},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, src: None
        client = app.test_client()
        # A real value each time: an empty form submits no overrides, so it
        # never reaches make_profile.render and the path never moves.
        client.post("/estimate", json={"max_age_days": "7"})
        before = len(sys.path)
        for i in range(25):
            client.post("/estimate", json={"max_age_days": "7"})
        self.assertEqual(len(sys.path), before)

    def test_the_default_done_reader_stays_inside_the_injected_output_dir(self):
        # Every running-screen test injects read_done, so the closure that
        # feeds a 40-minute paid sweep's progress display was exercised
        # nowhere — and its docstring makes a paid-data safety claim. Pointing
        # it at the real tree used to leave all 186 tests green.
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out)
        os.makedirs(os.path.join(out, "kanav"))
        plan = {"profile": "kanav", "sites": {"linkedin": [
            {"keywords": "A", "location": "India", "company": ""}]},
            "max_results": {"linkedin": 25}, "free_sources": 0}
        from sweep import runs as runs_mod
        key = runs_mod.combo_keys(plan, runs_mod.today())[0]
        with open(os.path.join(out, "kanav", ".done_combos"), "w") as fh:
            fh.write(key + "\n")

        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "raw_plan": plan,
                   "plan": {"total": 0.045, "total_searches": 1,
                            "over_cap": False, "lines": []},
                   "baseline_usd": 1.0, "proc": FakeProc()},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            fetch_plan=lambda p: plan, read_spend=lambda: 2.0,
            output_dir=out)                 # read_done NOT injected
        app.config.update(TESTING=True)
        payload = app.test_client().get("/progress").get_json()
        # It read the ledger under the INJECTED dir. The real
        # output/kanav/.done_combos has no line for this synthetic combo, so
        # a default that ignored output_dir would report 0 done.
        self.assertEqual(payload["done"], 1)
        self.assertEqual(payload["planned"], 1)

    def test_repeated_progress_reads_do_not_grow_sys_path(self):
        # read_spend ran sys.path.insert on every call — about 160 times a
        # sweep — which is the defect R42 fixed in one function and left in
        # two others.
        plan = {"profile": "p", "sites": {"linkedin": [
            {"keywords": "k", "location": "India", "company": ""}]},
            "max_results": {"linkedin": 25}, "free_sources": 0}
        # read_spend is deliberately NOT injected: the default closure is
        # the thing that grew sys.path, and injecting a fake would leave it
        # unexecuted — the same injection blindness that hid a dead Alpine
        # layer. With APIFY_TOKEN absent it returns before any network call,
        # and the insert sat ahead of that check.
        env = {k: v for k, v in os.environ.items() if k != "APIFY_TOKEN"}
        ticks = itertools.count(0, app_module.SPEND_POLL_SECONDS + 1)
        app = app_module.create_app(
            state={"profile": "p", "cap_usd": 8.41, "raw_plan": plan,
                   "plan": {"total": 0.045, "total_searches": 1,
                            "over_cap": False, "lines": []},
                   "baseline_usd": 1.0, "proc": FakeProc()},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            fetch_plan=lambda p: plan, read_done=lambda p, d: set(),
            # The spend throttle reads at most once per SPEND_POLL_SECONDS,
            # so a rapid loop would call the closure ONCE and the test could
            # not see it grow. Advance the injected clock past the window on
            # every tick so each request really re-reads.
            now=lambda: next(ticks),
            output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        client = app.test_client()
        with mock.patch.dict(os.environ, env, clear=True):
            client.get("/progress")
            before = len(sys.path)
            for _ in range(50):
                client.get("/progress")
        self.assertEqual(len(sys.path), before)


class TestPaidSourceDefaults(Isolated):
    """Which paid sources a profile inherits when it says nothing. This is the
    only place the decision is recorded as a test rather than a comment."""

    def test_naukri_is_off_until_someone_asks_for_it(self):
        # Measured across every run in output/ (9,932 rows, 2026-09-09): naukri
        # produced zero rows and appears in no .done_combos — it has never run,
        # because it is a $0.50-per-run MINIMUM and its inventory is largely
        # what LinkedIn already returns. Flipping this back on costs real money
        # on the next unconfigured sweep, so it is pinned.
        import config as live
        self.assertFalse(live.SITES["naukri"]["enabled"])

    def test_the_cheap_paid_sources_stay_on(self):
        # The other half of the same decision: turning naukri off must not
        # quietly become "no paid sources at all".
        import config as live
        self.assertTrue(live.SITES["linkedin"]["enabled"])
        self.assertTrue(live.SITES["indeed"]["enabled"])


class TestRunningMeterBindings(Isolated):
    """The header meter's bindings reference names that must exist in the
    Alpine scope. A missing one is a ReferenceError at runtime, which no
    server-side assertion can see — the blind spot that hid a dead Alpine
    layer for ten tasks."""

    def _body(self, baseline=1.0, spend=2.5):
        plan = {"profile": "p", "sites": {"linkedin": [
            {"keywords": f"k{i}", "location": "India", "company": ""}
            for i in range(4)]}, "max_results": {"linkedin": 25},
            "free_sources": 2}
        app = app_module.create_app(
            state={"profile": "p", "cap_usd": 8.41, "raw_plan": plan,
                   "plan": {"total": 0.18, "total_searches": 4,
                            "over_cap": False, "lines": [], "spend_cap": 0.5},
                   "baseline_usd": baseline,
                   "proc": FakeProc()},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            fetch_plan=lambda p: plan, read_done=lambda p, d: set(),
            read_spend=lambda: spend, output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        return app.test_client().get("/running").get_data(as_text=True)

    def test_every_name_the_header_bindings_use_is_in_the_scope(self):
        # Parse the scope's real shape rather than substring-matching it: a
        # substring check passes when a name is renamed to a superstring, and
        # when a top-level name merely appears inside the nested progress
        # object. Both produce a false green over bindings that throw.
        body = self._body()
        scope = re.search(r"x-data='(\{.*\})'\s", body, re.S).group(1)
        top = re.findall(r"(?:^\{|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", scope)
        self.assertIn("cap", top)
        self.assertIn("p", top)
        inner = json.loads(re.search(r"p:\s*(\{.*?\}),\s*cap:", scope, re.S).group(1))
        for name in ("spend", "baseline_known", "tiles"):
            self.assertIn(name, inner,
                          f"p.{name} is referenced by a binding but is not in "
                          f"the progress payload, so it throws at runtime")

    def test_the_header_label_follows_whether_the_baseline_is_known(self):
        # With no baseline, p.spend is the account's month-to-date total,
        # which is NOT this sweep's spend — so the header must not call it
        # "Spent so far", and must not paint it the over-cap red.
        body = self._body(baseline=None, spend=40.0)
        # Both the Alpine expression and the first paint must say it: the
        # server-rendered fallback used to read "Spent so far" over a
        # month-to-date figure until Alpine corrected it.
        self.assertIn("Account spend this month", body)
        self.assertNotIn(">Spent so far</span>", body)
        # The LABEL's own expression has to branch on it. Asserting that both
        # strings merely appear in the page cannot fail: a mutation to
        # `true ? 'Spent so far' : 'Account spend this month'` leaves both
        # literals sitting there, and "p.baseline_known" survives elsewhere
        # in the :class binding.
        label = re.search(r"<span x-text=\"([^\"]*)\">", body).group(1)
        self.assertIn("p.baseline_known", label)
        over = re.search(r':class="\{ over: ([^}]*)\}"', body).group(1)
        self.assertIn("baseline_known", over)
        # The FILL too, not just the colour: an ungated bar paints 100% hard
        # against the cap marker under "Account spend this month" — reading
        # as "you have spent your whole budget" on a figure that is not this
        # sweep's spend at all.
        fill = re.search(r':style="([^"]*)"', body).group(1)
        self.assertIn("baseline_known", fill)
        # And the server-rendered first paint must not fill it, nor paint it
        # the over-cap red: with spend 40.0 against a cap of 8.41 the
        # ungated template rendered class="meter over" before Alpine ran.
        self.assertIn('class="meter-fill" style="width: 0%"', body)
        meter = re.search(r'<div class="meter([^"]*)"', body).group(1)
        self.assertNotIn("over", meter)


class TestPageShell(Isolated):
    def _app(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                     derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        return app

    def test_alpine_is_served_locally_not_from_a_cdn(self):
        # /key renders this same shell, and that is where the user pastes the
        # credential this tool exists to protect. A third-party script that
        # can read the DOM does not belong on it — and an unreachable CDN
        # takes the live cost meter down silently.
        client = self._app().test_client()
        body = client.get("/").get_data(as_text=True)
        self.assertNotIn("cdnjs", body)
        self.assertIn("alpine-3.14.1.min.js", body)
        served = client.get("/static/alpine-3.14.1.min.js")
        try:
            self.assertEqual(served.status_code, 200)
            self.assertIn(b"Alpine", served.get_data())
        finally:
            # Flask streams a static file from an open handle; without this
            # the suite reports a ResourceWarning for the unclosed reader.
            served.close()

    def test_the_shell_declares_a_language_and_a_viewport(self):
        body = self._app().test_client().get("/").get_data(as_text=True)
        self.assertIn('lang="en"', body)
        self.assertIn("width=device-width", body)


class TestFixesThatHadNoTest(Isolated):
    """Six fixes from the whole-branch review shipped with no test, so each
    could be reverted with the suite still green. The review found them by
    mutating the code; these pin them instead."""

    def _results_app(self, rows=None):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            read_rows=lambda profile: ROWS if rows is None else rows,
            start_rescore=lambda profile, hours: None,
            output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        return app

    def test_the_rescore_form_carries_the_active_filters(self):
        body = self._results_app().test_client().get(
            "/results?min=40&source=linkedin&q=react").get_data(as_text=True)
        # A bare /rescore action is why the error paths lost the filters.
        self.assertIn("min=40", body)
        self.assertIn("source=linkedin", body)
        self.assertIn("q=react", body)

    def test_a_successful_rescore_redirect_keeps_the_filters(self):
        # The error paths were fixed for this and the success path was left a
        # bare redirect — the common path, on the branch's own worst habit.
        app = self._results_app()
        r = app.test_client().post(
            "/rescore?min=40&source=linkedin&q=react", data={"hours": "6"})
        self.assertEqual(r.status_code, 302)
        for fragment in ("min=40", "source=linkedin", "q=react"):
            self.assertIn(fragment, r.headers["Location"])

    def test_every_account_scanner_shares_one_token_list(self):
        # rescore_from_apify.py enumerated ("APIFY_TOKEN", "_2", "_3") while
        # scraper._require_token() discovered any APIFY_TOKEN_*. An Apify
        # dataset belongs to the account that ran it, so with a fourth key
        # attached the re-rank read a subset of the paid rows and reported
        # success — no error, just missing money's worth of results. Both
        # callers now go through scraper.apify_tokens(); this fails if either
        # grows its own list again.
        import scraper
        rescore = (pathlib.Path(scraper.__file__).parent
                   / "rescore_from_apify.py").read_text()
        self.assertIn("apify_tokens()", rescore)
        # The specific shape of the bug: a literal tuple/list of slot names.
        self.assertNotRegex(
            rescore, r'"APIFY_TOKEN"\s*,\s*"APIFY_TOKEN_2"')

    def test_the_results_table_scrolls_inside_its_own_box(self):
        # overflow-x alone let a 989-row shortlist scroll the page body.
        body = self._results_app().test_client().get("/results").get_data(as_text=True)
        self.assertIn('<div class="listings">', body)
        # The cap lives in the stylesheet now, so that is where it is checked.
        # Matching "max-height" anywhere in the HTML passed for any unrelated
        # inline style; this pins the rule that actually bounds the box.
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        rule = re.search(r"\.listings\s*\{([^}]*)\}", css).group(1)
        self.assertIn("max-height", rule)
        self.assertIn("overflow", rule)
        # And the page itself must not scroll sideways. Clipping the box is
        # not enough: Chrome propagates a min-width table's layout overflow to
        # the viewport anyway (measured: documentElement.scrollWidth 951 on a
        # 390 viewport), and containment is what stops it.
        self.assertIn("contain", rule)

    def test_the_results_table_has_real_header_cells(self):
        body = self._results_app().test_client().get("/results").get_data(as_text=True)
        # All eight, not just one: pinning a single cell let the other seven
        # revert to <td> silently.
        for col in ("Source", "Score", "Role", "Location", "Pay",
                     "Experience", "Matched skills"):
            self.assertIn(f'<th scope="col">{col}</th>', body)
        # One header row per non-empty bucket, so a whole multiple of eight —
        # and never a plain <td> standing in for a header cell.
        count = body.count('<th scope="col">')
        self.assertGreaterEqual(count, 8)
        self.assertEqual(count % 8, 0)

    def test_the_depth_control_says_which_sites_it_moves(self):
        # "Raising this raises the cost" is false for naukri, the priciest
        # line — its charge is a per-run minimum at a depth this control
        # cannot change. The assertion used to land on the other paragraph.
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        body = app.test_client().get("/configure").get_data(as_text=True)
        self.assertIn("It does not change Naukri", body)

    def test_the_single_sweep_note_shows_even_with_everything_filtered_out(self):
        # It used to hide on `total`, i.e. exactly when the user most needs to
        # know which file was read.
        body = self._results_app().test_client().get(
            "/results?min=99999").get_data(as_text=True)
        self.assertIn("most recent sweep only", body)

    def test_an_unknown_baseline_is_not_reported_as_this_sweeps_spend(self):
        # spend_delta subtracting a None baseline as 0.0 left all 164 tests
        # green: /progress returns before reaching it, and /results was the
        # only consumer of that branch.
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41,
                   "baseline_usd": None, "spend_read_val": 40.0},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            read_rows=lambda profile: ROWS, output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertIn("not known", body)
        self.assertNotIn("$40.00", body)


class TestAlpineScopeSurvivesHtmlParsing(Isolated):
    """Every screen whose figures update in place depends on one x-data
    attribute parsing. A test that only greps the raw body cannot see this."""

    def test_the_configure_cost_scope_parses_whole(self):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: None
        body = app.test_client().get("/configure").get_data(as_text=True)
        scopes = alpine_scope(body)
        # Every scope on the page, not just the first: the failure this
        # guards is an attribute that ENDS at a stray quote, which leaves a
        # component that never initialises and a screen that silently stops
        # updating. A truncated one cannot close its own brace.
        self.assertTrue(scopes)
        for scope in scopes:
            self.assertTrue(scope.strip().startswith("{"), scope[:60])
            self.assertTrue(scope.strip().endswith("}"), scope[-60:])
        cost = [s for s in scopes if "total" in s]
        self.assertEqual(len(cost), 1, "exactly one cost scope")
        self.assertIn("lines", cost[0])
        # The location picker is the other one, and it holds a JSON list
        # rendered by |tojson — the exact shape that truncates a
        # double-quoted attribute.
        picker = [s for s in scopes if "picked" in s]
        self.assertEqual(len(picker), 1)

    def test_the_running_progress_scope_parses_whole(self):
        app, _, _ = TestRunningScreen()._app()
        body = app.test_client().get("/running").get_data(as_text=True)
        scopes = alpine_scope(body)
        self.assertEqual(len(scopes), 1)
        self.assertIn("spend", scopes[0])
        self.assertIn("tiles", scopes[0])


class TestUploadScreen(Isolated):
    def setUp(self):
        self.app = app_module.create_app(state={})
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def test_upload_screen_renders_with_an_empty_meter(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("$0.00", body)
        self.assertIn("Point it at your", body)

    def test_meter_shows_no_cap_before_a_key_is_connected(self):
        # base.html renders the credit block only when cap_usd is set.
        body = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("Credit left", body)

    def test_meter_shows_a_real_zero_once_credit_is_exhausted(self):
        # A verified 0.0 is a genuine value (every account is spent), not the
        # same as "no key connected yet" (None) — Jinja treats both as falsy
        # under a plain {% if %}, so the header must use an explicit
        # "is not none" check to tell them apart or a real zero goes missing
        # exactly when it matters most.
        app = app_module.create_app(
            state={"cap_usd": 0.0, "credit_total_usd": 0.0})
        app.config.update(TESTING=True)
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn("Credit left $0.00", body)

    def test_the_header_credit_is_the_total_across_every_key(self):
        # It showed cap_usd, which is what ONE sweep can spend — the best
        # single account. On four keys holding $8.33 the header read $5.00
        # while the confirm screen said "$8.33 between them" two lines below.
        app = app_module.create_app(
            state={"cap_usd": 5.00, "credit_total_usd": 8.33})
        app.config.update(TESTING=True)
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn("Credit left $8.33", body)
        self.assertNotIn("Credit left $5.00", body)

    def test_posting_no_file_is_rejected_not_guessed(self):
        r = self.client.post("/resume", data={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Choose a PDF", r.get_data(as_text=True))

    def test_a_pdf_with_no_extractable_text_says_so(self):
        state = {}
        resume_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, resume_dir)
        app = app_module.create_app(
            state=state, extract=lambda path: "", resume_dir=resume_dir)
        app.config.update(TESTING=True)
        r = app.test_client().post(
            "/resume",
            data={"resume": (io.BytesIO(b"%PDF-1.7 fake"), "scan.pdf")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("no text", r.get_data(as_text=True).lower())

    def test_a_good_pdf_is_stored_and_redirects_to_review(self):
        state = {}
        resume_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, resume_dir)
        app = app_module.create_app(
            state=state, extract=lambda path: "Kartik — React Native developer",
            resume_dir=resume_dir)
        app.config.update(TESTING=True)
        r = app.test_client().post(
            "/resume",
            data={"resume": (io.BytesIO(b"%PDF-1.7 fake"), "cv.pdf")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/review", r.headers["Location"])
        self.assertIn("React Native", state["resume_text"])

    def test_the_upload_is_saved_under_the_injected_resume_dir_not_the_real_one(self):
        real_path = os.path.join(app_module.RESUME_DIR, "resume.pdf")
        real_mtime_before = os.path.getmtime(real_path)

        state = {}
        resume_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, resume_dir)
        app = app_module.create_app(
            state=state, extract=lambda path: "Kartik — React Native developer",
            resume_dir=resume_dir)
        app.config.update(TESTING=True)
        app.test_client().post(
            "/resume",
            data={"resume": (io.BytesIO(b"%PDF-1.7 fake"), "cv.pdf")},
            content_type="multipart/form-data")

        self.assertEqual(
            state["resume_path"], os.path.join(resume_dir, "resume.pdf"))
        self.assertTrue(os.path.exists(os.path.join(resume_dir, "resume.pdf")))
        self.assertEqual(os.path.getmtime(real_path), real_mtime_before)

    def test_a_file_over_the_limit_is_rejected_with_the_upload_error_panel(self):
        state = {}
        resume_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, resume_dir)
        app = app_module.create_app(
            state=state, resume_dir=resume_dir, max_upload_bytes=1024)
        app.config.update(TESTING=True)
        oversized = io.BytesIO(b"0" * 2048)
        r = app.test_client().post(
            "/resume",
            data={"resume": (oversized, "big.pdf")},
            content_type="multipart/form-data")
        self.assertEqual(r.status_code, 413)
        self.assertIn("larger than", r.get_data(as_text=True))


DERIVED = {
    "field_summary": "Full-stack React Native developer, about 2 years.",
    "years_experience": 2,
    "role_keywords": ["React Native Developer", "Full Stack Engineer"],
    "skill_weights": [
        {"term": "react native", "weight": 5},
        {"term": "node.js", "weight": 5},
        {"term": "javascript", "weight": 2},
        {"term": "git", "weight": 1},
    ],
    "penalty_terms": [{"term": "salesforce", "weight": 5}],
    "domain_half_a": ["react native"], "domain_half_b": ["node.js"],
    "domain_title_terms": ["react native developer"], "domain_bonus": 5,
    "notes": "Platform terms dominate.",
}


class TestFrontDoor(Isolated):
    """The landing screen: the résumé control, and the three facts beside it.

    Everything here is one page, but two of these guard money and one guards
    the upload itself.
    """

    def _app(self, state=None, **kw):
        # derive raises: nothing this screen renders may call the model. It is
        # step 1 and it costs nothing, and `read` exists precisely so a
        # returning visitor's cached derivation can be shown WITHOUT a call.
        def no_model(text, prefs):
            raise AssertionError("the front door must not call the model")
        app = app_module.create_app(
            state={} if state is None else state,
            extract=lambda p: "x", derive=no_model, **kw)
        app.config.update(TESTING=True)
        return app

    def body(self, state=None, **kw):
        return self._app(state, **kw).test_client().get("/").get_data(as_text=True)

    # ---- the upload control ---------------------------------------------
    def test_the_dropzone_is_a_label_around_the_real_file_input(self):
        # The whole box is clickable because a <label> wraps the input — no
        # script involved. If that nesting breaks, the panel still looks
        # right and nothing can be uploaded by clicking it.
        from html.parser import HTMLParser

        seen = []

        class Scan(HTMLParser):
            depth = None

            def handle_starttag(self, tag, attrs):
                d = dict(attrs)
                if tag == "label" and "drop" in (d.get("class") or ""):
                    self.depth = 0
                elif self.depth is not None and tag == "input":
                    seen.append(d)

            def handle_endtag(self, tag):
                if tag == "label":
                    self.depth = None

        Scan().feed(self.body())
        self.assertEqual(len(seen), 1, "one file input, inside the dropzone")
        # The server contract: request.files["resume"], PDFs only.
        self.assertEqual(seen[0].get("name"), "resume")
        self.assertEqual(seen[0].get("type"), "file")

    def test_the_dropzone_scope_parses_whole(self):
        # The longest x-data in the app. Same failure as the cost panel's:
        # one stray quote and the picked-file card, the client-side check and
        # the drop handler are all silently dead.
        scopes = alpine_scope(self.body())
        self.assertEqual(len(scopes), 1)
        for name in ("sent", "file", "bad", "take()", "drop(e)"):
            self.assertIn(name, scopes[0])

    def test_the_size_limit_shown_is_the_size_limit_enforced(self):
        # Both the copy and the client-side check read the injected limit.
        # Hardcoding 15 in the template makes the page say 15 MB while the
        # app rejects at 1 — the user is told the wrong number by the only
        # thing that told them anything.
        body = self.body(max_upload_bytes=1024 * 1024)
        self.assertIn("up to 1 MB", " ".join(body.split()))
        self.assertNotIn("15 MB", body)
        self.assertIn("1 * 1048576", body)

    def test_a_rejected_upload_comes_back_to_the_same_screen(self):
        # All four ways in render one template through one helper; an error
        # path that lost `max_mb` would raise on the size copy instead.
        r = self._app().test_client().post("/resume", data={})
        self.assertEqual(r.status_code, 400)
        body = r.get_data(as_text=True)
        self.assertIn("Choose a PDF to upload.", body)
        self.assertIn("Drop your résumé here", body)

    # ---- what the next screen will get ----------------------------------
    def test_nothing_read_yet_reads_as_absent_not_as_zero(self):
        body = self.body()
        self.assertEqual(body.count("not read yet"), 3)
        self.assertNotIn(">0<", body)

    def test_a_cached_derivation_is_shown_without_a_model_call(self):
        # derive() raises in this fixture, so reaching the model at all fails
        # the test rather than quietly costing a second call.
        body = self.body({"resume_text": "x", "derived": DERIVED})
        self.assertNotIn("not read yet", body)
        facts = " ".join(body.split())
        self.assertIn("Titles it will search for</dt> <dd>2</dd>", facts)
        self.assertIn("Skill weights</dt> <dd>4</dd>", facts)
        self.assertIn("Years of experience</dt> <dd>2</dd>", facts)

    def test_zero_years_of_experience_is_still_a_figure(self):
        # A graduate's résumé derives 0, which is a value, not a blank —
        # the same defect the meter refuses on money, pointing the other way.
        body = self.body({"resume_text": "x",
                          "derived": dict(DERIVED, years_experience=0)})
        self.assertIn("<dd>0</dd>", " ".join(body.split()))

    # ---- the metering strip ---------------------------------------------
    def test_the_paid_boards_are_named_the_way_the_boards_spell_them(self):
        body = self.body()
        self.assertIn("LinkedIn, Indeed, Naukri bill per search", " ".join(body.split()))
        self.assertNotIn("Linkedin", body)

    def test_a_paid_site_with_no_label_still_appears_in_the_sentence(self):
        # A site added to config.SITE_RATES must not drop out of the copy
        # just because nobody wrote its display name down.
        self.assertEqual(app_module.site_label("linkedin"), "LinkedIn")
        self.assertEqual(app_module.site_label("wellfound"), "wellfound")


class TestReviewScreen(Isolated):
    def _app(self, state=None):
        # derived pre-seeded: GET /review no longer makes the model call, it
        # hands back the working screen when there is nothing derived yet.
        # This is the state POST /derive leaves behind.
        state = (state if state is not None
                 else {"resume_text": "a résumé", "derived": DERIVED})
        app = app_module.create_app(
            state=state, extract=lambda p: "x",
            derive=lambda resume_text, prefs: DERIVED)
        app.config.update(TESTING=True)
        return app, state

    # ---- what the screen now surfaces from the parse --------------------

    def _body(self, state=None):
        app, _ = self._app(state)
        return app.test_client().get("/review").get_data(as_text=True)

    def test_it_names_the_resume_the_findings_came_from(self):
        body = self._body({"resume_text": "a résumé", "derived": DERIVED,
                           "resume_path": "/tmp/uploads/Kanav_CV_2026.pdf"})
        self.assertIn("Kanav_CV_2026.pdf", body)
        # The name, not the path: it is this machine's filesystem and says
        # nothing the reader needs.
        self.assertNotIn("/tmp/uploads", body)

    def test_a_long_filename_cannot_widen_the_page(self):
        # It is user-supplied and has no spaces to break at, so at phone
        # width one long name would stretch the whole layout rather than
        # just this line. The only unbounded token this screen renders.
        body = self._body({"resume_text": "x", "derived": DERIVED,
                           "resume_path": "/tmp/" + "a" * 90 + ".pdf"})
        self.assertIn('<b class="filename">', body)
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        self.assertIn(".filename { overflow-wrap: anywhere; }", css)

    def test_a_rejected_submit_still_names_the_resume(self):
        # FIVE routes render this template. resume_name lives on shell() for
        # exactly this reason — supplied by one render, it is a fact the
        # four error paths each drop, which is the defect _confirm_page was
        # written to fix on the screen after this one.
        app, _ = self._app({"resume_text": "a résumé", "derived": DERIVED,
                            "resume_path": "/tmp/uploads/Kanav_CV_2026.pdf"})
        r = app.test_client().post("/review", data={"name": ""})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Kanav_CV_2026.pdf", r.get_data(as_text=True))

    def test_no_resume_path_leaves_the_sentence_whole(self):
        # A session resumed after a restart has the derived data and no path.
        body = self._body()
        self.assertIn("scoring terms.", " ".join(body.split()))

    def test_the_model_note_is_shown_rather_than_only_written_to_disk(self):
        # derived["notes"] goes into the profile and was never displayed —
        # the one place the parse explains itself, on the screen whose whole
        # job is checking the parse.
        body = self._body()
        self.assertIn("Worth knowing", body)
        self.assertIn(DERIVED["notes"], body)

    def test_the_penalty_terms_are_shown(self):
        # The other half of the scoring: these push a listing DOWN, and a
        # wrong one quietly buries good jobs.
        body = self._body()
        self.assertIn("Terms that push a listing down", body)
        self.assertIn("salesforce", body)

    def test_a_parse_with_no_note_or_penalties_renders_neither_panel(self):
        bare = dict(DERIVED, notes="", penalty_terms=[])
        body = self._body({"resume_text": "x", "derived": bare})
        self.assertNotIn("Worth knowing", body)
        self.assertNotIn("Terms that push a listing down", body)

    def test_the_titles_are_chips_with_a_count(self):
        body = self._body()
        self.assertIn("2 titles", " ".join(body.split()))
        # Matched from the class inward, so template line wrapping is not
        # part of the contract.
        self.assertIn('class="tag">React Native Developer</span>', body)

    def test_the_commodity_terms_read_as_an_english_list(self):
        # "javascript and git" for two; three used to render as
        # "a and b and c", which is a list nobody wrote.
        three = dict(DERIVED, skill_weights=DERIVED["skill_weights"]
                     + [{"term": "rest api", "weight": 1}])
        flat = " ".join(self._body(
            {"resume_text": "x", "derived": three}).split())
        self.assertIn("javascript, git and rest api appear", flat)

    def test_the_weight_bar_follows_the_stepper(self):
        # Server-rendered width AND a binding: correct with no JavaScript,
        # and it moves as you click rather than showing the weight the page
        # was loaded with.
        body = self._body()
        # A class, not a width interpolated into a style attribute: that is
        # not CSS, and an editor validating the attribute says so.
        self.assertNotIn('style="width: {{', body)
        self.assertIn('class="lv5"', body)
        self.assertIn(':style="\'width: \' + (n * 20) + \'%\'"', body)
        # The scope is the row, or the bar cannot see the stepper's n.
        self.assertIn("<tr x-data=", body)

    def test_every_level_class_has_a_width(self):
        # The class is the whole of the server-rendered bar now, so a rule
        # that is missing draws nothing and the markup still looks right.
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        for level, width in enumerate((20, 40, 60, 80, 100), start=1):
            self.assertIn(f".weights .bar .lv{level} {{ width: {width}%; }}",
                          css)

    def test_a_weight_outside_one_to_five_still_draws_a_bar(self):
        # The response schema types the weight as an integer and does not
        # bound it, so a model can hand back a 9. Unclamped it lands on no
        # rule and the bar disappears.
        wild = dict(DERIVED, skill_weights=[{"term": "react", "weight": 9},
                                            {"term": "git", "weight": 0}])
        body = self._body({"resume_text": "x", "derived": wild})
        self.assertIn('class="lv5"', body)
        self.assertIn('class="lv1"', body)

    def test_no_template_puts_jinja_inside_a_style_attribute(self):
        # style="" is CSS, and an editor validates it as CSS. The one place
        # this is unavoidable is a continuous percentage; everything else
        # has discrete steps and can say so with a class.
        import glob
        offenders = []
        for path in glob.glob(os.path.join(
                os.path.dirname(app_module.__file__), "templates", "*.html")):
            for n, line in enumerate(open(path), 1):
                if re.search(r'\sstyle="[^"]*\{[{%]', line):
                    offenders.append(f"{os.path.basename(path)}:{n}")
        # base.html's meter fill is a continuous 0-100 and has no discrete
        # steps to enumerate, so it stays and is the only one allowed.
        self.assertEqual(offenders, ["base.html:109"])

    def test_removing_a_term_strikes_the_term_not_the_rank(self):
        # The rule targeted td:first-child, which was the skill name until a
        # rank column went in front of it. A struck-through number reads as
        # a typo rather than as a removal.
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        self.assertIn(".weights tr:has(.remove input:checked) .term", css)
        self.assertNotIn(".weights tr:has(.remove input:checked) td:first-child",
                         css)

    def test_shows_the_derived_titles_and_weights(self):
        app, _ = self._app()
        body = app.test_client().get("/review").get_data(as_text=True)
        self.assertIn("React Native Developer", body)
        self.assertIn("react native", body)
        self.assertIn("Full-stack React Native developer", body)

    def test_low_weight_commodity_terms_are_flagged_for_removal(self):
        # 'git' and 'javascript' appear in most postings and carry no signal.
        app, _ = self._app()
        body = app.test_client().get("/review").get_data(as_text=True)
        self.assertIn("Worth removing", body)
        self.assertIn("git", body)

    def test_review_without_a_resume_sends_you_back_to_upload(self):
        app, _ = self._app(state={})
        r = app.test_client().get("/review")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/"),
                        f"should redirect to upload, got {r.headers['Location']}")

    def test_approving_writes_the_profile_and_moves_to_the_key_screen(self):
        app, state = self._app()
        written = {}
        app.write_profile = lambda name, source: written.update(
            {"name": name, "source": source})
        r = app.test_client().post("/review", data={
            "name": "kanav", "drop": ["git"]})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/key", r.headers["Location"])
        self.assertEqual(written["name"], "kanav")
        self.assertNotIn("'git'", written["source"])
        self.assertIn("react native", written["source"])
        self.assertEqual(state["profile"], "kanav")

    def test_a_missing_name_is_rejected_not_defaulted(self):
        app, _ = self._app()
        r = app.test_client().post("/review", data={"name": ""})
        self.assertEqual(r.status_code, 400)
        self.assertIn("name", r.get_data(as_text=True).lower())

    def test_a_name_with_a_path_separator_is_rejected_not_written(self):
        # profile name -> profiles/<name>.py; os.path.join silently drops
        # everything before a later absolute component, so this must be
        # blocked before it ever reaches a filesystem write.
        app, _ = self._app()
        written = {}
        app.write_profile = lambda name, source: written.update(
            {"name": name, "source": source})
        r = app.test_client().post("/review", data={"name": "/tmp/x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("letters, numbers, dashes", r.get_data(as_text=True))
        self.assertEqual(written, {})

    def test_a_name_with_dot_dot_is_rejected_not_written(self):
        app, _ = self._app()
        written = {}
        app.write_profile = lambda name, source: written.update(
            {"name": name, "source": source})
        r = app.test_client().post(
            "/review", data={"name": "../../../../tmp/x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("letters, numbers, dashes", r.get_data(as_text=True))
        self.assertEqual(written, {})

    def test_posting_review_without_a_resume_sends_you_back_to_upload(self):
        app, _ = self._app(state={})
        r = app.test_client().post("/review", data={"name": "kanav"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/"),
                        f"should redirect to upload, got {r.headers['Location']}")

    def test_default_write_profile_also_rejects_a_bad_name(self):
        # Belt and suspenders: default_write_profile is the single write
        # funnel, so it validates independently of the route's own check —
        # a future caller can't reintroduce the path-traversal hole.
        app, _ = self._app()
        with self.assertRaises(ValueError):
            app.write_profile("../evil", "source")


class TestProfileNameAutofill(Isolated):
    def _app(self, derived, exists=()):
        app = app_module.create_app(
            state={"resume_text": "a résumé", "derived": derived},
            extract=lambda p: "x", derive=lambda t, p: derived,
            profile_exists=lambda n: n in exists)
        app.config.update(TESTING=True)
        return app

    def test_the_name_field_is_prefilled_from_the_resume(self):
        named = dict(DERIVED, candidate_name="Kartik Verma")
        body = self._app(named).test_client().get("/review").get_data(as_text=True)
        self.assertIn('name="name" value="kartik_verma"', body)

    def test_a_resume_with_no_name_leaves_the_field_empty(self):
        body = self._app(dict(DERIVED, candidate_name="")).test_client().get(
            "/review").get_data(as_text=True)
        self.assertIn('name="name" value=""', body)

    def test_a_derivation_without_the_field_at_all_still_renders(self):
        # DERIVED predates candidate_name, and so does every profile derived
        # before this change — a missing key must not 500 the screen.
        body = self._app(DERIVED).test_client().get("/review").get_data(as_text=True)
        self.assertIn('name="name" value=""', body)

    def test_a_profile_already_chosen_this_session_wins(self):
        # That is the name the rest of the flow is already using.
        named = dict(DERIVED, candidate_name="Kartik Verma")
        app = self._app(named)
        app.state["profile"] = "kartik_reachable"
        body = app.test_client().get("/review").get_data(as_text=True)
        self.assertIn('name="name" value="kartik_reachable"', body)

    def test_a_clash_with_the_suggested_name_is_flagged_on_arrival(self):
        # Autofilling straight into a guaranteed 409 would trade one piece of
        # friction for another, so the replace option is offered up front.
        named = dict(DERIVED, candidate_name="Kartik Verma")
        body = self._app(named, exists=("kartik_verma",)).test_client().get(
            "/review").get_data(as_text=True)
        self.assertIn('name="overwrite"', body)
        self.assertIn("a profile already uses it", " ".join(body.split()))
        # A heads-up, not a rejection: never painted the over-cap red.
        self.assertNotIn('class="error"', body)

    def test_no_clash_notice_when_the_name_is_free(self):
        named = dict(DERIVED, candidate_name="Kartik Verma")
        body = self._app(named).test_client().get("/review").get_data(as_text=True)
        self.assertNotIn('name="overwrite"', body)


class TestSkillWeightEditing(Isolated):
    def _app(self):
        app = app_module.create_app(
            state={"resume_text": "a résumé", "cap_usd": 8.41,
                   "derived": DERIVED},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            profile_exists=lambda n: False)
        app.config.update(TESTING=True)
        app.written = []
        app.write_profile = lambda n, src: app.written.append(src)
        return app

    def _weights(self, source):
        """skill_weights as {term: weight}, read out of rendered profile."""
        block = source[source.index("skill_weights"):]
        block = block[:block.index("}")]
        return {t: int(w) for t, w in re.findall(r"'([^']*)':\s*(\d+)", block)}

    def test_an_edited_weight_reaches_the_profile(self):
        # The screen showed the model's weight as static text, so a wrong one
        # could only be removed, never corrected — and the model gets these
        # wrong in a repeatable way (it scored `git` at 1 and `render` at 3 on
        # a real résumé).
        app = self._app()
        r = app.test_client().post("/review", data={
            "name": "tmp_weights",
            "term": ["react native", "node.js", "javascript", "git"],
            "weight": ["5", "3", "2", "1"]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._weights(app.written[-1])["node.js"], 3)

    def test_an_edited_weight_survives_the_next_configure_change(self):
        # /estimate re-renders the same profile from state["derived"], so a
        # weight edited here was silently reverted by the first click on the
        # Configure screen.
        app = self._app()
        client = app.test_client()
        client.post("/review", data={
            "name": "tmp_weights", "term": ["node.js"], "weight": ["3"]})
        client.post("/estimate", json={"max_age_days": "7"})
        self.assertEqual(self._weights(app.written[-1])["node.js"], 3)

    def test_a_removed_skill_survives_the_next_configure_change(self):
        # Same defect, and it was already live before weights were editable:
        # prune a term on Review, touch anything on Configure, and the term
        # was back in the profile that scores the listings.
        app = self._app()
        client = app.test_client()
        client.post("/review", data={"name": "tmp_weights",
                                     "drop": ["javascript", "git"]})
        self.assertNotIn("javascript", self._weights(app.written[-1]))
        client.post("/estimate", json={"max_age_days": "7"})
        self.assertNotIn("javascript", self._weights(app.written[-1]))

    def test_a_weight_outside_one_to_five_is_refused(self):
        app = self._app()
        r = app.test_client().post("/review", data={
            "name": "tmp_weights", "term": ["node.js"], "weight": ["9"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Weight for node.js", r.get_data(as_text=True))
        self.assertEqual(app.written, [])

    def test_mismatched_term_and_weight_lists_fail_closed(self):
        # These are parallel lists: a desync would reassign weights to the
        # wrong terms, silently, on the numbers that decide the ranking.
        app = self._app()
        r = app.test_client().post("/review", data={
            "name": "tmp_weights", "term": ["node.js", "git"],
            "weight": ["3"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(app.written, [])

    def test_the_weight_column_is_an_editable_control_not_static_text(self):
        body = self._app().test_client().get("/review").get_data(as_text=True)
        # A real number input, so the weight is still typable with no JS.
        self.assertRegex(body, r'name="weight"[^>]*min="1"[^>]*max="5"')
        # And explicit minus/plus, which is what a stepper is.
        self.assertIn('@click="n = Math.max(1, n - 1)"', body)
        self.assertIn('@click="n = Math.min(5, n + 1)"', body)
        # The term rides along so the POST does not depend on the server
        # reproducing this sort order.
        self.assertIn('name="term"', body)


EMPTY_PLAN = {"profile": "kanav", "sites": {}, "max_results": {},
              "free_sources": 39}


class TestEmptyAndPendingStates(Isolated):
    """States the screens can actually reach: no paid sources (newly
    reachable once Configure could switch them off), nothing derived, and a
    profile the engine can no longer plan."""

    def _app(self, plan=None, state=None, fetch=None):
        base = {"profile": "kanav", "cap_usd": 8.41, "derived": DERIVED}
        base.update(state or {})
        app = app_module.create_app(
            state=base, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=fetch or (lambda profile: plan or RAW_PLAN),
            # One of these reaches /running, whose feed reads the filesystem.
            output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        app.write_profile = lambda n, src: None
        return app

    # ---- no paid sources ------------------------------------------------
    def test_configure_says_what_a_zero_source_sweep_does(self):
        body = self._app(EMPTY_PLAN).test_client().get(
            "/configure").get_data(as_text=True)
        self.assertIn("No paid sources selected", body)
        # Correct without JavaScript: the message shows and the table hides,
        # decided server-side rather than waiting on Alpine.
        self.assertRegex(body, r'class="empty"[^>]*x-show="!est\.lines\.length"')
        self.assertRegex(body, r'class="lines"[^>]*style="display:none"')

    def test_configure_hides_the_empty_state_when_there_are_lines(self):
        # The other half of the same gate. Pinning only the empty case let
        # both render at once, or neither.
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertRegex(
            body, r'class="empty" x-show="!est\.lines\.length"\s*style="display:none"')

    def test_confirm_says_what_a_zero_source_sweep_does(self):
        body = self._app(EMPTY_PLAN).test_client().get(
            "/confirm").get_data(as_text=True)
        self.assertIn("No paid sources in this sweep", body)
        # And it must still be runnable: a free sweep is a real choice.
        self.assertIn("Run the sweep", body)

    def test_running_says_there_is_nothing_metered_to_track(self):
        # An empty tile grid reads as "nothing is happening" rather than
        # "nothing is billed".
        app = self._app(EMPTY_PLAN, state={
            "raw_plan": EMPTY_PLAN, "baseline_usd": 1.0,
            "plan": {"total": 0.0, "total_searches": 0, "over_cap": False,
                     "lines": [], "spend_cap": 0.5, "free_sources": 39}})
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn("No paid searches in this sweep", body)
        self.assertRegex(body, r'class="grid-groups"[^>]*style="display:none"')

    # ---- nothing derived ------------------------------------------------
    def test_review_says_when_no_skills_came_back(self):
        bare = dict(DERIVED, skill_weights=[])
        app = app_module.create_app(
            state={"resume_text": "a résumé", "derived": bare},
            extract=lambda p: "x", derive=lambda t, p: bare)
        app.config.update(TESTING=True)
        body = app.test_client().get("/review").get_data(as_text=True)
        self.assertIn("No skills came back", body)
        # And the table goes with them: the empty state first rendered ABOVE a
        # bare "SKILL / WEIGHT" header row with no body under it.
        self.assertNotIn('<th scope="col">Weight</th>', body)

    def test_review_says_when_no_titles_were_derived(self):
        # Every paid search is one title in one location, so no titles means
        # a sweep that searches nothing at all.
        bare = dict(DERIVED, role_keywords=[])
        app = app_module.create_app(
            state={"resume_text": "a résumé", "derived": bare},
            extract=lambda p: "x", derive=lambda t, p: bare)
        app.config.update(TESTING=True)
        body = app.test_client().get("/review").get_data(as_text=True)
        self.assertIn("No titles derived", body)

    # ---- the plan cannot be computed ------------------------------------
    def _broken(self):
        def boom(profile):
            raise subprocess.CalledProcessError(1, ["scraper.py"], stderr="nope")
        return self._app(fetch=boom)

    def test_an_unplannable_profile_is_a_page_not_a_500_traceback(self):
        # plan.fetch raises CalledProcessError for a profile that will not
        # import, and it reached Flask uncaught — a bare 500 on both screens.
        for route in ("/configure", "/confirm"):
            r = self._broken().test_client().get(route)
            self.assertEqual(r.status_code, 500, route)
            body = r.get_data(as_text=True)
            self.assertIn("can't be priced", body)
            self.assertIn("nothing has been spent", body)

    def test_the_plan_failure_page_never_echoes_engine_output(self):
        # Engine stderr is unbounded output, and this shell is also the screen
        # where the key is pasted. The detail goes to the log instead.
        body = self._broken().test_client().get("/configure").get_data(as_text=True)
        self.assertNotIn("nope", body)

    def test_estimate_answers_a_plan_failure_in_json_not_html(self):
        # Its caller does r.json(); an HTML error page would fail to parse and
        # read as "the network is down" on the live-cost screen.
        r = self._broken().test_client().post("/estimate", json={"max_age_days": "7"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("could not price", r.get_json()["error"].lower())

    # ---- in-flight and failure on the live cost -------------------------
    def test_a_failed_estimate_never_becomes_the_estimate(self):
        # `.then(d => est = d)` assigned WHATEVER came back, so a 400 set est
        # to {error: ...} and est.total.toFixed threw — the live cost layer
        # died silently on an invalid skip-term.
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn("if (res.ok) { est = res.d; }", body)
        self.assertNotIn("then(d => est = d)", body)
        # A failure has to be visible, not just survivable.
        self.assertIn('x-show="err"', body)

    def test_the_figures_are_marked_stale_while_being_re_priced(self):
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn("busy = true", body)
        self.assertIn(":class=\"busy && 'updating'\"", body)
        self.assertIn("busy = false", body)

    # ---- submitting -----------------------------------------------------
    def test_the_run_button_cannot_be_clicked_twice(self):
        body = self._app().test_client().get("/confirm").get_data(as_text=True)
        self.assertIn(':disabled="sent"', body)
        self.assertIn("Starting the sweep", " ".join(body.split()))

    def test_the_resume_upload_says_the_model_call_is_running(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn(':disabled="sent"', body)
        self.assertIn("Reading your résumé", body)

    def test_cloak_is_styled_or_every_pending_note_flashes_on_load(self):
        # x-show sets display on init, so without this rule each of these
        # notes is briefly visible on every page load — "Reading your
        # résumé…" on a page nobody has submitted yet.
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        self.assertRegex(css, r"\[x-cloak\]\s*\{[^}]*display:\s*none")
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn("x-cloak", body)


class TestResumeParsingScreen(Isolated):
    """The model call is the longest wait in the app. It used to happen inside
    GET /review's render, so the browser sat on the PREVIOUS page for the
    whole thing and there was no response the server could put a loading
    state into."""

    def _app(self, derive=None):
        calls = []

        def default(resume_text, prefs):
            calls.append(resume_text)
            return DERIVED

        app = app_module.create_app(
            state={"resume_text": "a résumé"}, extract=lambda p: "x",
            derive=derive or default)
        app.config.update(TESTING=True)
        app.calls = calls
        return app

    def test_review_hands_back_a_working_screen_without_calling_the_model(self):
        app = self._app()
        body = app.test_client().get("/review").get_data(as_text=True)
        self.assertIn("Reading your résumé", body)
        # The whole point: the render must not block on the model, or there is
        # no page to show while it runs.
        self.assertEqual(app.calls, [])

    def test_the_working_screen_submits_itself_so_it_stays_on_screen(self):
        body = self._app().test_client().get("/review").get_data(as_text=True)
        self.assertIn('action="/derive"', body)
        self.assertIn("requestSubmit", body)

    def test_the_submit_does_not_wait_for_alpine(self):
        # It used to be an x-init. Alpine is deferred, and a deferred script's
        # execution also waits on the render-blocking font stylesheet, so this
        # screen could sit showing only its fallback button with no bar — the
        # one screen that has no other way forward.
        body = self._app().test_client().get("/review").get_data(as_text=True)
        self.assertIn("<script>", body[body.index("<main>"):])
        self.assertNotIn("x-init", body)
        self.assertNotIn("x-data", body)

    def test_the_submit_waits_for_a_paint(self):
        # Submitting during the load can cancel it before the browser paints,
        # which leaves the PREVIOUS page on screen for the whole model call —
        # the exact problem this screen exists to solve.
        body = self._app().test_client().get("/review").get_data(as_text=True)
        self.assertIn("requestAnimationFrame", body)
        # And a backstop, because frames do not fire in a hidden tab.
        self.assertIn("setTimeout", body)

    def test_the_working_screen_has_no_button_to_press(self):
        # A control on a screen that is already working does nothing. The
        # only submit left is the one <noscript> needs, so a browser with
        # scripting off still has a way forward.
        body = self._app().test_client().get("/review").get_data(as_text=True)
        self.assertEqual(body.count("<button"), 1)
        before = body[:body.index("<button")]
        self.assertIn("<noscript>", before)
        self.assertNotIn("</noscript>", before)

    def test_the_working_screen_looks_busy_from_its_first_frame(self):
        # The bar was gated on the submit having fired, so the first moment of
        # this screen was a button and nothing else.
        body = self._app().test_client().get("/review").get_data(as_text=True)
        self.assertIn('<div class="working-bar"><span></span></div>', body)
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        # Entrance written with `both`, or reduced-motion leaves it invisible.
        self.assertRegex(css, r"\.working \{ animation: rise [^}]*both")

    def test_derive_makes_the_call_then_lands_on_the_real_screen(self):
        app = self._app()
        client = app.test_client()
        r = client.post("/derive")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/review", r.headers["Location"])
        self.assertEqual(app.calls, ["a résumé"])
        body = client.get("/review").get_data(as_text=True)
        self.assertIn("react native", body)
        self.assertNotIn("Reading your résumé", body)

    def test_the_model_is_not_paid_for_twice(self):
        app = self._app()
        client = app.test_client()
        client.post("/derive")
        client.post("/derive")
        client.get("/review")
        self.assertEqual(len(app.calls), 1)

    def test_a_failed_derivation_is_a_message_not_a_500(self):
        def boom(resume_text, prefs):
            raise RuntimeError("upstream said no")
        app = self._app(derive=boom)
        r = app.test_client().post("/derive")
        self.assertEqual(r.status_code, 502)
        body = r.get_data(as_text=True)
        self.assertIn("The model call failed", body)

    def test_an_unknown_failure_does_not_blame_the_pdf_outright(self):
        # A quota, an expired key and a network failure all land here. Telling
        # someone their résumé is a scan sends them to re-export a file that
        # was never the problem — which is what a user hit on a PDF that had
        # parsed fine the day before.
        def boom(resume_text, prefs):
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        body = self._app(derive=boom).test_client().post(
            "/derive").get_data(as_text=True)
        for cause in ("quota", "key that no longer works", "scanned PDF"):
            self.assertIn(cause, body)
        # And still never the upstream text, which can carry the request URL.
        self.assertNotIn("RESOURCE_EXHAUSTED", body)

    def test_the_default_derive_walks_the_model_ladder(self):
        # Not the single pin: free-tier RPD is counted per model, so an
        # exhausted primary must reach the next one rather than fail an
        # upload. Asserted on the REAL default derive, which is the only
        # place that choice is made.
        import apply_config as cfg
        seen = {}

        def fake_generate(client, models, resume_text, prefs):
            seen["models"] = models
            return DERIVED

        app = app_module.create_app(state={"resume_text": "x"},
                                    extract=lambda p: "x")
        app.config.update(TESTING=True)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
             mock.patch.object(app_module.make_profile, "generate", fake_generate), \
             mock.patch("tailor.get_client", lambda key: object()):
            r = app.test_client().post("/derive")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(tuple(seen["models"]), tuple(cfg.MODELS))
        self.assertGreater(len(seen["models"]), 1)

    def test_a_named_model_failure_is_named_on_the_screen(self):
        # ModelAnswerError is the one exception whose text this app composed
        # itself, from the response's own finish_reason enum — so it is safe
        # to show, and it is the difference between "your PDF is broken" and
        # "the model ran out of output budget".
        def boom(resume_text, prefs):
            raise app_module.make_profile.ModelAnswerError(
                "the model ran out of output budget before it answered")
        body = self._app(derive=boom).test_client().post(
            "/derive").get_data(as_text=True)
        self.assertIn("ran out of output budget", body)
        self.assertIn("Your résumé is fine", body)

    def test_a_failed_derivation_does_not_retry_itself_forever(self):
        # The error screen must NOT carry the auto-submit: a résumé the model
        # keeps refusing would loop, paying for a call every time round.
        def boom(resume_text, prefs):
            raise RuntimeError("upstream said no")
        body = self._app(derive=boom).test_client().post(
            "/derive").get_data(as_text=True)
        self.assertNotIn("requestSubmit", body)

    def test_the_error_screen_keeps_a_real_button(self):
        # The working screen's only button lives in <noscript> because the
        # page submits itself. The error screen does NOT submit itself, so
        # its button has to be a real one or the screen is a dead end.
        def boom(resume_text, prefs):
            raise RuntimeError("upstream said no")
        body = self._app(derive=boom).test_client().post(
            "/derive").get_data(as_text=True)
        main = body[body.index("<main>"):]
        self.assertEqual(main.count("<button"), 1)
        self.assertNotIn("<noscript>", main)
        self.assertIn("Upload another PDF", main)

    def test_a_failed_derivation_never_echoes_the_upstream_error(self):
        # A client library's exception can carry the request URL, and the keys
        # live in .env.
        def boom(resume_text, prefs):
            raise RuntimeError("key=SECRET123 rejected")
        body = self._app(derive=boom).test_client().post(
            "/derive").get_data(as_text=True)
        self.assertNotIn("SECRET123", body)

    def test_a_derivation_that_returns_nothing_cannot_loop(self):
        # derived_for_state() caches whatever comes back, and its "have I
        # derived yet" check is `is None` — so a falsy result left state
        # looking un-derived. GET /review then serves the working screen,
        # which submits itself, which calls the model again: a runaway that
        # pays for a call every lap. Introduced by the auto-submit, so it is
        # guarded at the same time.
        for empty in (None, {}):
            app = self._app(derive=lambda t, p: empty)
            r = app.test_client().post("/derive")
            self.assertEqual(r.status_code, 502, repr(empty))
            body = r.get_data(as_text=True)
            self.assertIn("returned nothing", body)
            # The thing that actually stops the loop.
            self.assertNotIn("requestSubmit", body)

    def test_derive_without_a_resume_goes_back_to_upload(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        r = app.test_client().post("/derive")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/")


class TestStepTracker(Isolated):
    """The header was seven plain tabs, four of which redirect away on a
    fresh session. The tracker has to agree with the route guards, so this
    checks it against the actual routes rather than against a second copy of
    the rules."""

    def _app(self, state):
        app = app_module.create_app(
            state=dict(state), extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            read_rows=lambda profile: list(ROWS),
            read_done=lambda profile, day: [],
            read_spend=lambda: 4.12, output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        app.write_profile = lambda n, src: None
        return app

    STATES = {
        "fresh": {},
        "uploaded": {"resume_text": "x"},
        "reviewed": {"resume_text": "x", "profile": "kanav",
                      "derived": DERIVED},
        "keyed": {"resume_text": "x", "profile": "kanav", "derived": DERIVED,
                   "cap_usd": 8.41},
        # No key at all, and none wanted: step 3 was answered the other way.
        "free": {"resume_text": "x", "profile": "kanav", "derived": DERIVED,
                  "free_only": True,
                  "sites_enabled": {"linkedin": False, "indeed": False,
                                     "naukri": False}},
    }

    def test_no_offered_step_ever_redirects(self):
        # The property that makes the tracker worth having: if it is a link,
        # it goes somewhere. Verified by opening every step it offers, in
        # four different states, against the real guards.
        for name, state in self.STATES.items():
            app = self._app(state)
            steps = app_module.step_states(
                app_module.STEPS, app.state, None)
            offered = [s["slug"] for s in steps if s["open"]]
            self.assertTrue(offered, name)
            for slug in offered:
                with app.test_client() as client:
                    r = client.get(f"/{'' if slug == 'upload' else slug}")
                self.assertEqual(
                    r.status_code, 200,
                    f"{name}: tracker offered /{slug} but it redirected")

    def test_a_locked_step_is_not_a_link(self):
        body = self._app(self.STATES["fresh"]).test_client().get(
            "/").get_data(as_text=True)
        # review is locked with no résumé, so it must render without an href.
        review = re.search(
            r'<li class="step[^"]*locked[^"]*">\s*<a\s*\n?\s*([^>]*)>\s*'
            r'<span class="marker"[^>]*>\s*2', body)
        self.assertIsNotNone(review, "no locked step rendered for review")
        self.assertNotIn("href", review.group(1))

    def test_the_current_step_is_marked_and_not_a_link_to_itself(self):
        body = self._app(self.STATES["uploaded"]).test_client().get(
            "/").get_data(as_text=True)
        self.assertIn('class="step current"', body)
        self.assertIn('aria-current="step"', body)
        # Exactly one current step, whatever the state.
        self.assertEqual(body.count('aria-current="step"'), 1)
        # Not the escaped form: see the next test.
        self.assertNotIn("aria-current=&#34;", body)

    def test_no_template_emits_an_attribute_through_autoescape(self):
        # {{ 'aria-current="step"' if cond }} escapes its own quotes, so the
        # page receives aria-current=&#34;step&#34; — an attribute that never
        # applies and that nothing visibly breaks over. The tab nav this
        # tracker replaces shipped that for the whole branch. Bare words like
        # {{ 'checked' if ... }} are fine; a name="value" pair is not.
        templates = pathlib.Path(app_module.__file__).parent / "templates"
        for f in sorted(templates.glob("*.html")):
            # Jinja comments are stripped first: the comments in these
            # templates quote the defects they explain, this one included, so
            # a check that read prose as code would fire on its own docs.
            code = re.sub(r"\{#.*?#\}", "", f.read_text(), flags=re.S)
            self.assertNotRegex(
                code, r"""\{\{ *['"][a-zA-Z-]+=""", f.name)

    def test_finished_steps_are_marked_done_and_stay_reachable(self):
        body = self._app(self.STATES["keyed"]).test_client().get(
            "/configure").get_data(as_text=True)
        # upload, review and key are all behind us here.
        self.assertEqual(body.count('class="step done"'), 3)

    def test_the_step_you_are_on_is_never_also_marked_done(self):
        # upload is "done" once a résumé exists, but standing on it, the
        # useful fact is that you are there.
        steps = app_module.step_states(
            app_module.STEPS, {"resume_text": "x"}, "upload")
        upload = next(s for s in steps if s["slug"] == "upload")
        self.assertTrue(upload["current"])
        self.assertFalse(upload["done"])

    def test_running_is_not_offered_before_a_sweep_starts(self):
        # /running's own guard only wants raw_plan, which costed() writes on
        # every /configure visit — so guard parity alone advertised a
        # progress screen for a sweep that had not started.
        after_configure = {"resume_text": "x", "profile": "kanav",
                            "cap_usd": 8.41, "raw_plan": RAW_PLAN}
        steps = app_module.step_states(
            app_module.STEPS, after_configure, "configure")
        running = next(s for s in steps if s["slug"] == "running")
        self.assertFalse(running["open"])
        launched = dict(after_configure, proc=object())
        steps = app_module.step_states(
            app_module.STEPS, launched, "configure")
        running = next(s for s in steps if s["slug"] == "running")
        self.assertTrue(running["open"])

    def test_the_next_step_is_pointed_at(self):
        steps = app_module.step_states(
            app_module.STEPS, self.STATES["keyed"], "configure")
        self.assertEqual([s["slug"] for s in steps if s["next"]], ["confirm"])

    def test_a_zero_credit_key_still_counts_as_connected(self):
        # no_key_yet() checks `cap_usd is not None`; bool(cap) would call an
        # exhausted account no key and lock the rest of the flow.
        steps = app_module.step_states(
            app_module.STEPS,
            {"resume_text": "x", "profile": "kanav", "cap_usd": 0.0}, None)
        by = {s["slug"]: s for s in steps}
        self.assertTrue(by["key"]["done"])
        self.assertTrue(by["configure"]["open"])

    def test_the_position_is_stated_once_not_hardcoded_per_screen(self):
        # Seven templates each carried a literal "Step N of 7", which would
        # drift the moment a step moved. The tracker owns it now.
        app = self._app(self.STATES["keyed"])
        body = app.test_client().get("/configure").get_data(as_text=True)
        self.assertNotIn("Step 4 of 7", body)
        self.assertIn("4 of 7", body)
        templates = (pathlib.Path(app_module.__file__).parent / "templates")
        for f in templates.glob("*.html"):
            self.assertNotRegex(f.read_text(), r"Step \d of 7", f.name)

    def test_the_narrow_layout_shows_where_you_are_and_what_is_next(self):
        # Seven steps cannot fit a phone, and a sideways-scrolling tracker
        # hides the one thing it exists to show.
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        self.assertIn(".step:not(.current):not(.next) { display: none; }", css)
        self.assertRegex(css, r"\.tracker-at \{[^}]*\}")


class TestBrandMark(Isolated):
    def test_every_screen_carries_the_mark_and_a_favicon(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn('rel="icon"', body)
        self.assertIn("favicon.svg", body)
        self.assertIn('class="mark"', body)

    def test_the_favicon_carries_its_own_ground(self):
        # A transparent mark disappears against a light tab bar, which is
        # where a favicon most often sits.
        svg = (pathlib.Path(app_module.__file__).parent
               / "static" / "favicon.svg").read_text()
        self.assertIn("#03161c", svg)
        # And it cannot use CSS variables — nothing resolves them there.
        self.assertNotIn("var(--", svg)


class TestFreeOnlyPath(Isolated):
    """Step 3 is a fork: connect a key, or search only what costs nothing.

    The free answer opens /configure, /confirm and /run with no verified key
    at all, so the tests that matter most here are the ones proving it cannot
    become a way to spend money without one.
    """

    FREE_PLAN = {"profile": "kanav", "sites": {}, "max_results": {},
                 "free_sources": 39}

    def _app(self, state=None, plan=None, **kw):
        def no_token(token):
            raise AssertionError("the free path must not verify a token")

        def no_account():
            raise AssertionError("the free path must not read the account")

        state = {"resume_text": "x", "profile": "kanav", "derived": DERIVED} \
            if state is None else state
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=kw.pop("check_token", no_token),
            fetch_plan=lambda profile: plan or self.FREE_PLAN,
            read_rows=lambda profile: [], read_done=lambda profile, day: [],
            read_spend=kw.pop("read_spend", no_account),
            # POST /key writes the token it accepts. Without a path of its
            # own that landed in the repo's real .env.
            env_path=kw.pop("env_path",
                            os.path.join(tempfile.mkdtemp(), ".env")),
            output_dir=tempfile.mkdtemp(), **kw)
        app.config.update(TESTING=True)
        app.written = {}
        app.write_profile = lambda n, src: app.written.update({n: src})
        return app

    def free(self, app):
        """Take the free path the way the screen does."""
        r = app.test_client().post("/key/free")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:400])
        self.assertIn("/configure", r.headers["Location"])
        return app

    # ---- the choice -----------------------------------------------------
    def test_step_three_offers_both_answers(self):
        body = self._app().test_client().get("/key").get_data(as_text=True)
        self.assertIn('action="/key/free"', body)
        self.assertIn('action="/key"', body)
        # And names the boards the paid answer buys, from paid_sites().
        self.assertIn("LinkedIn, Indeed, Naukri", " ".join(body.split()))

    def test_the_free_answer_verifies_no_token_and_sets_no_cap(self):
        # check_token raises in this fixture: reaching Apify at all fails.
        app = self.free(self._app())
        self.assertTrue(app.state["free_only"])
        # cap_usd stays absent, not 0.0 — a zero cap is an exhausted account,
        # which is a different thing to say than "no account".
        self.assertIsNone(app.state.get("cap_usd"))

    def test_the_free_answer_switches_every_paid_site_off(self):
        app = self.free(self._app())
        self.assertEqual(app.state["sites_enabled"],
                         {s: False for s in app_module.paid_sites()})

    def test_the_free_answer_rewrites_the_profile_it_will_price(self):
        # /configure prices the profile FILE. Without this rewrite the free
        # path would be quoted the paid plan it just declined.
        app = self.free(self._app())
        source = app.written["kanav"]
        # Scoped to the SITES block: the profile also carries a FEEDS overlay
        # whose himalayas entry is legitimately enabled.
        sites = source.split("SITES = {", 1)[1].split("\n}\n", 1)[0]
        self.assertEqual(sites.count('"enabled": False'),
                         len(app_module.paid_sites()))
        self.assertNotIn('"enabled": True', sites)

    def test_the_free_answer_needs_a_profile_to_apply_to(self):
        for state in ({}, {"resume_text": "x"}, {"profile": "kanav"}):
            app = self._app(state=dict(state))
            r = app.test_client().post("/key/free")
            self.assertEqual(r.status_code, 302)
            self.assertIn("/review", r.headers["Location"], str(state))
            self.assertNotIn("free_only", app.state)

    # ---- what the choice opens ------------------------------------------
    def test_the_flow_continues_with_no_key(self):
        app = self.free(self._app())
        client = app.test_client()
        for path in ("/configure", "/confirm"):
            self.assertEqual(client.get(path).status_code, 200, path)
        r = client.post("/estimate", json={"max_age_days": "7"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["total"], 0.0)

    def test_the_sweep_starts_with_no_key(self):
        launched = []
        app = self.free(self._app(
            start_sweep=lambda profile: launched.append(profile) or FakeProc()))
        client = app.test_client()
        client.get("/confirm")           # costs the plan, as the screen does
        r = client.post("/run")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/running", r.headers["Location"])
        self.assertEqual(launched, ["kanav"])

    def test_a_free_sweep_promises_no_cap_it_does_not_have(self):
        app = self.free(self._app())
        app.test_client().get("/confirm")
        # spend_cap_for() has a $0.50 floor because naukri alone costs that
        # much per run — but nothing here runs, so a $0.50 "hard cap" would
        # be a promise about a purchase that cannot happen.
        self.assertEqual(app.state["plan"]["spend_cap"], 0.0)

    # ---- and what it must never open ------------------------------------
    def test_run_refuses_a_free_sweep_that_somehow_prices_paid_searches(self):
        # The one that matters. free_only opened /run without a verified key,
        # so if a paid site is enabled after all, the engine would launch a
        # paid actor on whatever APIFY_TOKEN is already in .env.
        app = self.free(self._app(plan=RAW_PLAN))
        client = app.test_client()
        client.get("/confirm")
        self.assertGreater(app.state["plan"]["total"], 0)
        r = client.post("/run")
        self.assertEqual(r.status_code, 400)
        self.assertIn("free sources only", r.get_data(as_text=True))

    def test_configure_cannot_price_a_paid_site_back_on(self):
        app = self.free(self._app())
        r = app.test_client().post("/estimate", json={
            "sites_present": "1", "site_linkedin": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(app.state["sites_enabled"],
                         {s: False for s in app_module.paid_sites()})

    def test_configure_renders_no_paid_toggles_at_all_in_free_mode(self):
        # Not rendered disabled: a disabled checkbox posts nothing, so a form
        # still carrying the sites_present marker would read as "switch them
        # all off" — the marker has to go with the boxes.
        body = self.free(self._app()).test_client().get(
            "/configure").get_data(as_text=True)
        self.assertNotIn('name="site_linkedin"', body)
        self.assertNotIn('name="sites_present"', body)

    # ---- the money display ----------------------------------------------
    def test_a_free_sweep_reports_a_known_zero_not_an_unknown_figure(self):
        # read_spend raises in this fixture: there is no account to read, and
        # "not known" over a sweep that cannot spend is worse than the truth.
        app = self.free(self._app(
            start_sweep=lambda profile: FakeProc()))
        client = app.test_client()
        client.get("/confirm")
        client.post("/run")
        body = client.get("/running").get_data(as_text=True)
        self.assertIn('"spend": 0.0', body)
        self.assertIn('"spend_known": true', body)
        self.assertNotIn("numeral unknown", body)
        numeral = re.search(r'<span class="numeral[^"]*"[^>]*>([^<]*)<', body)
        self.assertEqual(numeral.group(1), "$0.00")
        self.assertIn("$0.00", client.get("/results").get_data(as_text=True))

    def test_every_screen_says_which_path_it_is_on(self):
        app = self.free(self._app())
        body = app.test_client().get("/configure").get_data(as_text=True)
        self.assertIn("Free sources only", body)
        self.assertNotIn("No key yet", body)
        self.assertNotIn("Key connected", body)

    # ---- re-ranking ------------------------------------------------------
    def test_a_free_sweep_is_not_offered_a_re_rank(self):
        # Re-ranking re-reads what an Apify ACTOR returned. A free sweep runs
        # no actors, so there is nothing to re-read.
        body = self.free(self._app()).test_client().get(
            "/results").get_data(as_text=True)
        self.assertIn("Re-ranking needs a paid sweep", body)
        self.assertNotIn('action="/rescore', body)
        # And the lede must not promise it either.
        self.assertNotIn("Filtering and re-ranking these is free", body)

    def test_a_posted_re_rank_is_refused_on_a_free_sweep(self):
        # Hiding the form is not a guard. rescore_from_apify.py writes
        # jobs_combined.csv, which read_rows PREFERS — so a re-rank that
        # found another profile's paid runs on a shared key would replace
        # this shortlist with them.
        started = []
        app = self.free(self._app(
            start_rescore=lambda profile, hours: started.append(profile)))
        r = app.test_client().post("/rescore", data={"hours": "6"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(started, [])
        self.assertIn("nothing to re-read", r.get_data(as_text=True))

    # ---- changing your mind ---------------------------------------------
    def test_connecting_a_key_afterwards_leaves_the_free_path(self):
        app = self.free(self._app(check_token=lambda t: (8.41, None)))
        r = app.test_client().post("/key", data={"token": "apify_api_xxx"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("free_only", app.state)
        # The free path's own side effect is undone with it: unset means
        # "inherit config.py's SITES", where all three are on. Left as {}
        # every site would stay off with no control rendered to say so.
        self.assertNotIn("sites_enabled", app.state)
        self.assertNotIn('"enabled": False', app.written["kanav"])

    def test_the_tracker_counts_step_three_as_answered(self):
        app = self.free(self._app())
        steps = app_module.step_states(app_module.STEPS, app.state, "configure")
        by_slug = {s["slug"]: s for s in steps}
        self.assertTrue(by_slug["key"]["done"])
        self.assertEqual(by_slug["key"]["label"], "Free or paid")
        self.assertTrue(by_slug["configure"]["open"])
        self.assertTrue(by_slug["confirm"]["open"])


class TestKeyScreen(Isolated):
    def _app(self, credit=(8.41, None), state=None):
        state = state if state is not None else {"profile": "kanav"}
        # A real .env of its own, and the real writer: POST /key sets the cap
        # by re-reading the FILE it has just written, so a stubbed writer
        # leaves the key nowhere and the fixture then disagrees with the
        # route about what a verified key does.
        app = app_module.create_app(
            state=state, extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda token: credit,
            env_path=os.path.join(tempfile.mkdtemp(), ".env"))
        app.config.update(TESTING=True)
        return app, state

    def test_key_screen_renders(self):
        app, _ = self._app()
        self.assertEqual(app.test_client().get("/key").status_code, 200)

    def test_a_good_token_sets_the_cap_and_moves_on(self):
        app, state = self._app(credit=(8.41, None))
        r = app.test_client().post("/key", data={"token": "apify_api_xxx"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/configure", r.headers["Location"])
        self.assertAlmostEqual(state["cap_usd"], 8.41, places=2)

    def test_a_rejected_token_stops_here_with_the_reason(self):
        app, state = self._app(credit=(None, "That token was rejected by Apify."))
        r = app.test_client().post("/key", data={"token": "bad"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("rejected", r.get_data(as_text=True))
        self.assertNotIn("cap_usd", state)

    def test_an_empty_token_is_rejected(self):
        app, _ = self._app()
        r = app.test_client().post("/key", data={"token": "  "})
        self.assertEqual(r.status_code, 400)

    def test_the_token_is_never_echoed_back_into_the_page(self):
        app, _ = self._app(credit=(None, "That token was rejected by Apify."))
        body = app.test_client().post(
            "/key", data={"token": "apify_api_SECRET"}).get_data(as_text=True)
        self.assertNotIn("apify_api_SECRET", body)

    def test_a_negative_available_credit_is_clamped_to_zero(self):
        # An account can already be over its own monthly limit — that must
        # never hand a negative number to the meter on later screens.
        app, state = self._app(credit=(-3.5, None))
        r = app.test_client().post("/key", data={"token": "x"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(state["cap_usd"], 0.0)

    def test_a_malformed_limits_response_is_a_400_not_a_500(self):
        # The float() conversion used to sit outside the try/except in the
        # real (non-injected) check_token, so a field Apify returns as a
        # non-numeric value crashed the request into a 500 instead of the
        # graceful 400 every other bad-input path gets.
        class FakeLimits:
            def model_dump(self):
                return {"current": {"monthly_usage_usd": "oops"},
                        "limits": {"max_monthly_usage_usd": 5.0}}

        class FakeUser:
            def limits(self):
                return FakeLimits()

        class FakeClient:
            def __init__(self, token):
                pass

            def user(self):
                return FakeUser()

        with mock.patch("apify_client.ApifyClient", FakeClient):
            app = app_module.create_app(
                state={"profile": "kanav"}, extract=lambda p: "x",
                derive=lambda t, p: DERIVED)
            app.write_env = lambda key, value: None
            r = app.test_client().post("/key", data={"token": "abc"})
        self.assertEqual(r.status_code, 400)


class TestWriteEnv(Isolated):
    """The real default_write_env, run against a temp file so no test ever
    touches the real .env — which holds live working credentials."""

    def _env_file(self, content):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        self.addCleanup(os.remove, path)
        return path

    def _app(self, env_path):
        return app_module.create_app(
            state={}, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            env_path=env_path)

    def test_upserts_the_target_key_and_leaves_every_other_key_intact(self):
        # Mirrors the real .env: several keys, no trailing newline on the
        # last line (GEMINI_API_KEY in the real file has none).
        path = self._env_file(
            "APIFY_TOKEN=old\nGROQ_API_KEY=g\nGEMINI_API_KEY=e")
        app = self._app(path)
        app.write_env("APIFY_TOKEN", "new")
        with open(path) as fh:
            pairs = dict(ln.split("=", 1) for ln in fh.read().splitlines())
        self.assertEqual(pairs, {
            "APIFY_TOKEN": "new", "GROQ_API_KEY": "g", "GEMINI_API_KEY": "e"})

    def test_a_missing_trailing_newline_does_not_corrupt_the_last_line(self):
        path = self._env_file("GROQ_API_KEY=g\nGEMINI_API_KEY=e")  # no \n
        app = self._app(path)
        app.write_env("APIFY_TOKEN", "new")
        with open(path) as fh:
            lines = fh.read().splitlines()
        self.assertIn("GEMINI_API_KEY=e", lines)
        self.assertIn("APIFY_TOKEN=new", lines)
        self.assertEqual(len(lines), 3)

    def test_a_missing_env_file_is_created(self):
        path = os.path.join(tempfile.mkdtemp(), ".env")
        self.addCleanup(shutil.rmtree, os.path.dirname(path))
        app = self._app(path)
        app.write_env("APIFY_TOKEN", "new")
        with open(path) as fh:
            self.assertEqual(fh.read().splitlines(), ["APIFY_TOKEN=new"])

    def test_a_value_containing_an_equals_sign_round_trips(self):
        path = self._env_file("GROQ_API_KEY=g\n")
        app = self._app(path)
        app.write_env("GEMINI_API_KEY", "a=b=c")
        with open(path) as fh:
            lines = fh.read().splitlines()
        self.assertIn("GEMINI_API_KEY=a=b=c", lines)

    def test_write_env_rejects_a_value_containing_a_newline(self):
        # A newline in the value would turn one write into two lines — the
        # second one an attacker-chosen KEY=VALUE that overwrites whatever
        # real key it names. The write must not happen at all.
        path = self._env_file("GROQ_API_KEY=g\n")
        with open(path) as fh:
            before = fh.read()
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", "abc\nGROQ_API_KEY=INJECTED")
        with open(path) as fh:
            self.assertEqual(fh.read(), before)

    def test_write_env_rejects_a_value_containing_a_carriage_return(self):
        path = self._env_file("GROQ_API_KEY=g\n")
        with open(path) as fh:
            before = fh.read()
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", "abc\rGROQ_API_KEY=INJECTED")
        with open(path) as fh:
            self.assertEqual(fh.read(), before)

    def test_write_env_rejects_a_nul_byte(self):
        # A NUL doesn't split a line, so the newline/CR blacklist let it
        # through — it got written to disk, and os.environ[...] = value
        # then raised ValueError on the NUL, unhandled, for a 500 with a
        # corrupted value already on disk. The allowlist below closes this
        # (and any other non-printable variant) in one rule instead of
        # chasing individual bad characters one bug report at a time.
        path = self._env_file("GROQ_API_KEY=g\n")
        with open(path) as fh:
            before = fh.read()
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", "apify_api_abc\x00INJECT")
        with open(path) as fh:
            self.assertEqual(fh.read(), before)

    def test_write_env_rejects_a_tab(self):
        path = self._env_file("GROQ_API_KEY=g\n")
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", "abc\tdef")

    def test_write_env_rejects_none_as_a_valueerror_not_a_typeerror(self):
        # "in" on a non-str used to raise TypeError before the isinstance
        # check existed — a caller catching ValueError (as the route does)
        # would have missed it entirely.
        path = self._env_file("GROQ_API_KEY=g\n")
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", None)

    def test_write_env_rejects_a_non_string_value(self):
        path = self._env_file("GROQ_API_KEY=g\n")
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("APIFY_TOKEN", 12345)

    def test_write_env_rejects_a_malformed_key(self):
        path = self._env_file("GROQ_API_KEY=g\n")
        app = self._app(path)
        with self.assertRaises(ValueError):
            app.write_env("apify_token", "x")

    def test_a_token_with_a_newline_is_rejected_and_env_is_left_untouched(self):
        # The same injection, arriving through the real /key route rather
        # than a direct write_env call — reproduces the coordinator's exact
        # curl-shaped attack and confirms nothing gets written at all.
        path = self._env_file(
            "APIFY_TOKEN=old\nGROQ_API_KEY=g\nGEMINI_API_KEY=e")
        with open(path) as fh:
            before = fh.read()
        app = app_module.create_app(
            state={"profile": "kanav"}, extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda token: (5.0, None), env_path=path)
        app.config.update(TESTING=True)
        r = app.test_client().post(
            "/key", data={"token": "apify_api_abc\nGROQ_API_KEY=INJECTED"})
        self.assertEqual(r.status_code, 400)
        with open(path) as fh:
            self.assertEqual(fh.read(), before)

    def test_a_token_with_a_carriage_return_is_rejected(self):
        app = app_module.create_app(
            state={"profile": "kanav"}, extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda token: (5.0, None),
            env_path=self._env_file("GROQ_API_KEY=g\n"))
        app.config.update(TESTING=True)
        r = app.test_client().post(
            "/key", data={"token": "apify_api_abc\rGROQ_API_KEY=INJECTED"})
        self.assertEqual(r.status_code, 400)

    def test_a_token_with_a_nul_byte_is_rejected_and_env_is_left_untouched(self):
        # Route-level version of the NUL case: validation must reject this
        # before check_token or write_env ever run, so nothing lands on
        # disk and os.environ[...] = token never gets a chance to crash.
        path = self._env_file("GROQ_API_KEY=g\n")
        with open(path) as fh:
            before = fh.read()
        app = app_module.create_app(
            state={"profile": "kanav"}, extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda token: (5.0, None), env_path=path)
        app.config.update(TESTING=True)
        r = app.test_client().post(
            "/key", data={"token": "apify_api_abc\x00INJECT"})
        self.assertEqual(r.status_code, 400)
        with open(path) as fh:
            self.assertEqual(fh.read(), before)

    def test_a_well_formed_token_still_succeeds(self):
        # The newline/CR guard must not be over-tight: a normal token still
        # makes it all the way through the real route and the real write.
        path = self._env_file("GROQ_API_KEY=g\n")
        app = app_module.create_app(
            state={"profile": "kanav"}, extract=lambda p: "x",
            derive=lambda t, p: DERIVED,
            check_token=lambda token: (5.0, None), env_path=path)
        app.config.update(TESTING=True)
        r = app.test_client().post("/key", data={"token": "apify_api_ABC123"})
        self.assertEqual(r.status_code, 302)
        with open(path) as fh:
            lines = fh.read().splitlines()
        self.assertIn("APIFY_TOKEN=apify_api_ABC123", lines)


RAW_PLAN = {
    "profile": "kanav",
    "sites": {
        "linkedin": [{"keywords": "A", "location": "India", "company": ""}] * 32,
        "indeed": [{"keywords": "A", "location": "Pune", "company": ""}] * 14,
    },
    "free_sources": 6,
}

# RAW_PLAN carries no depths and no naukri, so pricing every rate from one
# basis and pricing each from its own both come to $2.70 — it cannot tell the
# two apart, which is how the app's own cost(..., SITE_RATE_BASIS) argument
# came to be unpinned. This plan can:
#   per-site basis: linkedin 32 x $0.045 @25 + indeed 14 x $0.09 @15
#                   + naukri 2 x $0.50 @50 = $1.44 + $1.26 + $1.00 = $3.70
#   one basis of 25: indeed drops to $0.054 and naukri doubles to $1.00 each
#                   = $1.44 + $0.756 + $2.00 = $4.196
PRICED_PLAN = {
    "profile": "kanav",
    "sites": {
        "linkedin": [{"keywords": "A", "location": "India", "company": ""}] * 32,
        "indeed": [{"keywords": "A", "location": "Pune", "company": ""}] * 14,
        "naukri": [{"keywords": "A", "location": "Delhi / NCR", "company": ""}] * 2,
    },
    "max_results": {"linkedin": 25, "indeed": 15, "naukri": 50},
    "free_sources": 6,
}


class TestConfigureScreen(Isolated):
    def _app(self):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: None
        return app

    def _writing_app(self):
        """Configure app that keeps the profile source it writes, since that
        source is what the engine actually reads."""
        app = app_module.create_app(
            # derived included on purpose: /estimate renders the profile from
            # it, and the base fixture leaves it out, so every POST there
            # returned a 400 before reaching state.
            state={"profile": "kanav", "cap_usd": 8.41, "derived": DERIVED},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        app.written = []
        app.write_profile = lambda n, src: app.written.append((n, src))
        return app

    def test_a_source_can_be_switched_off_and_leaves_the_written_profile(self):
        # The screen offered no way to drop a paid site, so the only lever on
        # an over-budget plan was depth — and naukri, the priciest line, does
        # not move with depth at all.
        app = self._writing_app()
        app.test_client().post("/estimate", json={
            "sites_present": "1", "site_linkedin": "on", "site_indeed": "on"})
        self.assertEqual(app.state["sites_enabled"],
                         {"linkedin": True, "indeed": True, "naukri": False})
        _, source = app.written[-1]
        self.assertRegex(source, r'"naukri":\s*\{[^}]*"enabled":\s*False')
        self.assertRegex(source, r'"linkedin":\s*\{[^}]*"enabled":\s*True')

    def test_a_switched_off_source_keeps_the_fields_needed_to_switch_it_back(self):
        # config._overlay merges one level deep, so SITES["naukri"] is
        # REPLACED wholesale. Writing {"enabled": False} alone would drop
        # naukri's actor and its results_per_run: switch it back on by hand
        # later and the run either breaks or is silently re-priced at a
        # different depth than the rate was measured at.
        app = self._writing_app()
        app.test_client().post("/estimate", json={
            "sites_present": "1", "site_linkedin": "on"})
        _, source = app.written[-1]
        self.assertIn("muhammetakkurtt/naukri-job-scraper", source)
        self.assertIn('"results_per_run"', source)

    def test_every_source_gets_its_own_input_name(self):
        # NOT one repeated name: /estimate posts
        # Object.fromEntries(new FormData(form)), which keeps only the last
        # value for a repeated key — a group named "site" would arrive as a
        # single site and switch the other two off on the first click
        # anywhere on the screen.
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        for site in ("linkedin", "indeed", "naukri"):
            self.assertIn(f'name="site_{site}"', body)
        self.assertNotIn('name="site"', body)
        # The marker that separates "all unchecked" from "form has no sources".
        self.assertIn('name="sites_present"', body)

    def test_sources_render_the_state_config_actually_has(self):
        # An unset profile inherits config.py's SITES, so the box has to show
        # what config says — in BOTH directions. Rendering a state the profile
        # does not have is how the next change to any other field posts that
        # lie back; with naukri defaulted off, a hardcoded "checked" would be
        # an instruction to spend $0.50 a run.
        import config as live
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        checked = 0
        for site in app_module.paid_sites():
            on = live.SITES[site].get("enabled", True)
            checked += on
            if on:
                self.assertRegex(body, rf'name="site_{site}"\s+checked')
            else:
                self.assertRegex(body, rf'name="site_{site}"\s*>')
        # And the test itself must not silently become vacuous.
        self.assertTrue(0 < checked < len(app_module.paid_sites())
                        or checked == len(app_module.paid_sites()))

    def test_dropping_every_paid_source_is_a_zero_plan_not_an_error(self):
        # A free-feeds-only sweep is a legitimate choice and costs nothing.
        # It must not 400, and must not fabricate a cost.
        app = self._writing_app()
        r = app.test_client().post("/estimate", json={"sites_present": "1"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(app.state["sites_enabled"],
                         {"linkedin": False, "indeed": False, "naukri": False})

    def test_configure_renders_the_measured_rates(self):
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn("0.045", body)
        self.assertIn("linkedin", body)

    def test_configure_states_the_depth_the_rates_are_measured_at(self):
        # The rate shown is scaled to the plan's depth, so the screen has to
        # say what depth it was measured at or the figure is unauditable.
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn("scaled from the depth it was measured at", body)
        self.assertIn("line.results", body)
        # Naukri's charge is a per-run minimum at its own fixed depth, so
        # "raising this raises the cost" is false for the priciest line and
        # the screen has to say which sites the control actually moves.
        self.assertIn("Naukri is the exception", body)

    def test_estimate_returns_lines_that_multiply_out(self):
        r = self._app().test_client().post("/estimate", json={})
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertAlmostEqual(data["total"], 2.70, places=2)
        for line in data["lines"]:
            self.assertAlmostEqual(
                line["subtotal"], line["searches"] * line["rate"], places=6)

    def test_estimate_flags_when_the_plan_exceeds_available_credit(self):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 1.00},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (1.00, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        data = app.test_client().post("/estimate", json={}).get_json()
        self.assertTrue(data["over_cap"])
        self.assertAlmostEqual(data["shortfall"], 1.70, places=2)

    def test_shortfall_is_nonzero_when_credit_is_exactly_exhausted(self):
        # cap_usd=0.0 is a real value (the key has $0 left), not the same as
        # "no cap" (cap_usd=None) — `if cap` treats both as falsy, which
        # made shortfall report $0.00 for a user who is genuinely short the
        # full total. Must use `cap is not None`, same fix as base.html's
        # meter took in an earlier task for the identical falsy-zero bug.
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 0.0},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (0.0, None),
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        data = app.test_client().post("/estimate", json={}).get_json()
        self.assertTrue(data["over_cap"])
        self.assertAlmostEqual(data["shortfall"], 2.70, places=2)
        self.assertNotEqual(data["shortfall"], 0.0)

    def test_meter_binds_to_the_same_estimate_the_panel_uses(self):
        # The meter above the form is server-rendered by base.html; the
        # panel's total is Alpine-driven from `est`. They must share one
        # source of truth (the same x-data scope and the same `est` object)
        # so a form change can never leave the two money figures disagreeing.
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        # Single-quoted: |tojson leaves " raw, so a double-quoted attribute
        # ends at the JSON's first quote. This assertion used to require the
        # double-quoted form, which is how it came to pin a broken screen.
        self.assertIn("x-data='{ est:", body)
        self.assertIn("x-text=\"'$' + est.total.toFixed(2)\"", body)
        self.assertIn(':class="{ over: est.over_cap }"', body)
        # The meter's x-data must be on an ancestor of <main>, not a second,
        # disconnected scope — otherwise Alpine can't reach it from the form.
        data_pos = body.index("x-data='{ est:")
        main_pos = body.index("<main>")
        self.assertLess(data_pos, main_pos)

    def test_paid_rate_and_subtotal_cells_are_amber_free_rows_stay_teal(self):
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn(":class=\"!line.free && 'paid'\"", body)
        # The row itself carries .free for zero-rate sites; per-cell .paid
        # must be conditioned on the same flag so a free row's money stays
        # teal, never amber, and a paid row's search count stays chalk.
        self.assertIn(':class="line.free && \'free\'"', body)

    def test_configure_without_a_profile_goes_back_to_review(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        r = app.test_client().get("/configure")
        self.assertEqual(r.status_code, 302)

    def test_configure_without_a_connected_key_goes_to_the_key_screen(self):
        # Without a key there is no credit figure, so cap_usd stays None and
        # costed()'s over_cap is False for ANY total — the advisory cap is
        # unreachable and the engine quietly runs on whatever APIFY_TOKEN is
        # already in .env. Fail closed, the same shape /run uses.
        app = app_module.create_app(state={"profile": "kanav"},
                                    extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED,
                                    fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        r = app.test_client().get("/configure")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/key", r.headers["Location"])

    def test_estimate_without_a_connected_key_refuses(self):
        # /configure's own twin: it reprices AND rewrites the profile, so a
        # guard on one and not the other leaves the whole screen reachable.
        app = app_module.create_app(state={"profile": "kanav"},
                                    extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED,
                                    fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: self.fail("wrote a profile with no key")
        r = app.test_client().post("/estimate", json={"max_age_days": "7"})
        self.assertEqual(r.status_code, 409)

    # -- POST /estimate actually writing the form back into the profile ----

    def _app_with_spy(self, extra_state=None, hour_now=None):
        """Like _app(), but app.write_profile records every call instead of
        discarding it, so tests can assert on the rendered source."""
        state = {"profile": "kanav", "cap_usd": 8.41, "derived": dict(DERIVED)}
        state.update(extra_state or {})
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            hour_now=hour_now,
            fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        writes = []
        app.write_profile = lambda n, s: writes.append((n, s))
        return app, writes

    def test_estimate_writes_submitted_values_and_leaves_the_rest_alone(self):
        app, writes = self._app_with_spy()
        client = app.test_client()

        r1 = client.post("/estimate", json={"max_age_days": "7"})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(len(writes), 1)
        self.assertIn('"max_age_days": 7', writes[-1][1])

        r2 = client.post("/estimate", json={"skip_terms": "docker, on-call"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(len(writes), 2)
        second_source = writes[-1][1]
        self.assertIn("docker", second_source)
        self.assertIn("on-call", second_source)
        # max_age_days came from an earlier POST that this one never
        # mentioned — it must still be there, not reset.
        self.assertIn('"max_age_days": 7', second_source)

    def test_estimate_rejects_a_bad_number_and_writes_nothing(self):
        app, writes = self._app_with_spy()
        r = app.test_client().post("/estimate", json={"max_age_days": "soon"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())
        self.assertEqual(writes, [])

    def test_estimate_rejects_a_skip_term_with_disallowed_characters(self):
        app, writes = self._app_with_spy()
        r = app.test_client().post(
            "/estimate", json={"skip_terms": "docker; rm -rf /"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(writes, [])

    def test_the_app_prices_each_site_at_its_own_measured_depth(self):
        # Guards the argument, not the arithmetic: plan.cost's own tests can
        # pass while the app forgets to hand it config.SITE_RATE_BASIS, which
        # would put a $22.37 figure on a $15.97 plan.
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "derived": dict(DERIVED)},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: PRICED_PLAN)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: None
        out = app.test_client().post("/estimate", json={}).get_json()
        self.assertAlmostEqual(out["total"], 3.70, places=2)
        by_site = {l["site"]: l for l in out["lines"]}
        # indeed at its own basis of 15, not understated from 25.
        self.assertAlmostEqual(by_site["indeed"]["rate"], 0.09, places=4)
        # naukri's per-run minimum, not doubled by a foreign basis.
        self.assertAlmostEqual(by_site["naukri"]["rate"], 0.50, places=4)

    def test_estimate_maps_scope_and_the_pay_floor(self):
        app, writes = self._app_with_spy()
        client = app.test_client()

        client.post("/estimate", json={"scope": "remote"})
        self.assertIn('"remote_scopes": [', writes[-1][1])
        self.assertIn("'worldwide'", writes[-1][1])

        client.post("/estimate", json={"min_comp_usd": "20000"})
        self.assertIn('"min_comp_usd": 20000', writes[-1][1])

        # Blank is "no floor", a real choice rather than a validation error.
        client.post("/estimate", json={"min_comp_usd": ""})
        self.assertIn('"min_comp_usd": None', writes[-1][1])

    def test_a_late_start_warns_about_the_midnight_re_bill(self):
        # .done_combos is scoped to a single day, so a sweep still running
        # after midnight re-runs and re-bills everything it had finished.
        # This warning was previously untestable: hardcoding it to False left
        # the whole suite green.
        app, _ = self._app_with_spy(hour_now=lambda: 23)
        app.test_client().post("/estimate", json={})
        body = app.test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("midnight", body.lower())

    def test_an_early_start_does_not_warn(self):
        app, _ = self._app_with_spy(hour_now=lambda: 9)
        app.test_client().post("/estimate", json={})
        body = app.test_client().get("/confirm").get_data(as_text=True)
        self.assertNotIn("midnight", body.lower())

    def test_a_pay_floor_is_never_silently_discarded(self):
        # This screen used to carry a "keep listings that don't state pay"
        # checkbox, default ON, whose only real effect was to throw away the
        # floor the user had just typed. The engine keeps unstated pay either
        # way (scraper.comp_ok returns True when the pay is unstated), so the
        # toggle was offering a choice that did not exist and destroying a
        # real one. Whatever else the form carries, a submitted floor arrives.
        app, writes = self._app_with_spy()
        app.test_client().post("/estimate", json={
            "min_comp_usd": "80000", "keep_unstated": "on", "scope": "india"})
        self.assertIn('"min_comp_usd": 80000', writes[-1][1])
        self.assertNotIn('"min_comp_usd": None', writes[-1][1])

    def test_scope_overrides_linkedins_locations_not_just_search_locations(self):
        # SITES["linkedin"].get("locations", SEARCH["locations"]) means
        # LinkedIn — the priciest site — ignores SEARCH.locations entirely
        # unless the profile ALSO overrides SITES.linkedin. This is the
        # mechanism that made "global" and "india" scope indistinguishable
        # before this fix.
        app, writes = self._app_with_spy()
        client = app.test_client()

        client.post("/estimate", json={"scope": "india"})
        india_source = writes[-1][1]
        self.assertIn('SITES = {', india_source)
        self.assertIn('"linkedin"', india_source)
        self.assertIn("'Delhi'", india_source)
        self.assertIn('"remote_only": False', india_source)
        # enabled/actor must survive the rewrite — SITES["linkedin"] is
        # replaced wholesale by config._overlay's one-level-deep merge, not
        # deep-merged, so dropping them would silently disable the site.
        self.assertIn('"enabled": True', india_source)
        self.assertIn("curious_coder/linkedin-jobs-scraper", india_source)

        client.post("/estimate", json={"scope": "global"})
        global_source = writes[-1][1]
        self.assertIn("'United States'", global_source)
        self.assertNotIn("'Delhi'", global_source)

        client.post("/estimate", json={"scope": "remote"})
        remote_source = writes[-1][1]
        # kartik_reachable.py's own shape: worldwide remote through LinkedIn
        # is inventory this repo already measured as ~94% unreachable (see
        # its docstring), so "remote" buys India-remote only — one
        # geography — through LinkedIn, and leaves worldwide remote to the
        # free feeds, which are built for it and cost nothing.
        self.assertIn("'Remote'", remote_source)
        self.assertNotIn("'United States'", remote_source)
        self.assertIn('"remote_geo": \'India\'', remote_source)

    def test_no_scope_submitted_emits_no_sites_block(self):
        app, writes = self._app_with_spy()
        app.test_client().post("/estimate", json={"max_age_days": "7"})
        self.assertNotIn("SITES = {", writes[-1][1])

    def test_an_unverified_linkedin_location_is_rejected_and_writes_nothing(self):
        # Scope is a closed enum in production, so this can only be reached
        # by a bug in _SCOPE itself — proving render()'s own check catches
        # that, rather than silently billing an unverified geography. Same
        # guard applies to the CLI path, since it lives in render().
        app, writes = self._app_with_spy()
        original = dict(app_module._SCOPE["india"])
        app_module._SCOPE["india"] = dict(
            original, linkedin_locations=["Atlantis"])
        try:
            r = app.test_client().post("/estimate", json={"scope": "india"})
        finally:
            app_module._SCOPE["india"] = original
        self.assertEqual(r.status_code, 400)
        self.assertEqual(writes, [])

    def test_configure_screen_has_controls_for_skip_terms_and_depth(self):
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn('name="skip_terms"', body)
        self.assertIn('name="max_results"', body)

    def test_configure_screen_explains_why_remote_stays_on_linkedin_india(self):
        # The user is choosing where their money goes — the reason has to be
        # on the screen, not only in a code comment.
        body = self._app().test_client().get("/configure").get_data(as_text=True)
        self.assertIn("no worldwide-remote search", body)
        self.assertIn("free feeds", body)

    def test_a_blank_skip_terms_or_depth_field_does_not_reject_other_changes(self):
        # Both fields live in the same <form> as everything else, so a
        # change to max_age_days resubmits them too, blank — that must not
        # 400 the whole screen the very first time anyone touches a control.
        app, writes = self._app_with_spy()
        r = app.test_client().post(
            "/estimate",
            json={"max_age_days": "7", "skip_terms": "", "max_results": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(writes), 1)

    def test_estimate_reprices_after_the_profile_is_rewritten(self):
        # fetch_plan here is intentionally sensitive to whether write_profile
        # has already run, so this proves the ORDER — write, then re-plan —
        # not just that both happen somewhere. A fetch_plan that ignores its
        # argument (as elsewhere in this file) would hide exactly the bug
        # this task fixes.
        written = []
        smaller_plan = {
            "profile": "kanav",
            "sites": {"linkedin": [{"keywords": "A", "location": "India",
                                     "company": ""}] * 5},
            "free_sources": 6,
        }
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "derived": dict(DERIVED)},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: smaller_plan if written else RAW_PLAN)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: written.append((n, s))

        data = app.test_client().post(
            "/estimate", json={"max_results": "5"}).get_json()

        self.assertEqual(len(written), 1)
        self.assertAlmostEqual(data["total"], 0.225, places=3)
        self.assertNotAlmostEqual(data["total"], 2.70, places=2)

    def test_render_survives_hostile_penalty_terms(self):
        # Defense-in-depth check on make_profile.render() itself, independent
        # of sweep.app's character allowlist (which would reject these
        # characters at the HTTP boundary before they ever reached here) —
        # any input that reaches render(), from any source, must still come
        # out as an inert string literal.
        quote_term = "quote's here"
        backslash_term = r"back\slash"
        data = dict(DERIVED)
        data["penalty_terms"] = [
            {"term": quote_term, "weight": 12},
            {"term": backslash_term, "weight": 12},
            {"term": '; import os; os.system("echo pwned") #', "weight": 12},
        ]
        prefs = {"locations": ["Remote"], "exclude_levels": [],
                 "min_comp_usd": None}
        source = app_module.make_profile.render("hostile_check", data, prefs)
        compile(source, "<hostile_check>", "exec")  # still valid Python
        # repr() is what render() uses to write these — checking for the
        # escaped literal, not the raw term, is what proves the quote/
        # backslash round-tripped as DATA rather than breaking the source.
        self.assertIn(repr(quote_term), source)
        self.assertIn(repr(backslash_term), source)
        self.assertIn('import os', source)


class FakeProc:
    def __init__(self):
        self.signals = []

    def poll(self):
        return None

    def send_signal(self, sig):
        self.signals.append(sig)


class TestCostMarkers(Isolated):
    """Which controls carry the asterisk is not a judgement call. plan.cost()
    multiplies searches by a rate scaled to the depth, and the plan payload
    the engine hands it carries only `sites`, `max_results` and the combos
    (keywords x locations x companies). Anything absent from that payload
    cannot change the price — which is every filter on this screen."""

    def _body(self):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "derived": DERIVED},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN, output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        return app.test_client().get("/configure").get_data(as_text=True)

    def test_every_control_that_moves_the_price_is_marked(self):
        body = " ".join(self._body().split())
        for control in ("Sources", "Where you can work", "Locations",
                        "Results per search"):
            self.assertRegex(
                body, re.escape(control) + r'<span class="costs"',
                f"{control} changes the cost and carries no mark")

    def test_no_filter_is_marked(self):
        # Marking one would claim a filter spends credit, which sends someone
        # looking for savings to the control that cannot give them any.
        body = " ".join(self._body().split())
        for control in ("How recent", "Pay floor", "Skip these"):
            self.assertNotRegex(body, re.escape(control) + r'<span class="costs"')

    def test_the_mark_is_explained_where_it_is_used(self):
        body = " ".join(self._body().split())
        self.assertIn("Changes what this sweep costs", body)
        # And it says WHY those and not the rest — "these cost money" with no
        # rule invites the guess that everything might.
        self.assertIn("filters and re-ranks what has already been fetched", body)

    def test_the_glyph_is_not_read_aloud_as_an_asterisk(self):
        body = self._body()
        self.assertIn('<span class="costs" aria-hidden="true">*</span>', body)
        self.assertIn("(changes the cost)", body)

    def test_the_marked_count_matches_the_footnote_claim(self):
        # The footnote says three things reach the price: sources, locations
        # (from two controls) and depth. Four marks, no strays.
        body = self._body()
        self.assertEqual(body.count('<span class="costs" aria-hidden="true">'), 4)


class TestLocationPicker(Isolated):
    """Locations are the one field on Configure where a typo costs money in
    the wrong currency: LinkedIn answers an unverified location with United
    States results and bills for them (config.LINKEDIN_GEO_IDS). So the
    control is a picker over that table, and the form is checked against it
    again on the way in."""

    def _app(self, state=None):
        self.written = {}
        state = state if state is not None else {
            "profile": "kanav", "cap_usd": 8.41, "derived": DERIVED}
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN, output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        app.write_profile = lambda n, src: self.written.update({n: src})
        self.state = state
        return app

    def body(self, state=None):
        return self._app(state).test_client().get(
            "/configure").get_data(as_text=True)

    # ---- what it offers --------------------------------------------------
    def test_only_verified_locations_are_offered(self):
        import config as live
        body = self.body()
        offered = set(re.findall(r'<input type="checkbox" value="([^"]+)"', body))
        self.assertTrue(offered)
        for name in offered:
            self.assertTrue(name == "Remote" or name in live.LINKEDIN_GEO_IDS,
                            f"{name} has no verified geoId")

    def test_the_documented_traps_are_not_offered(self):
        body = self.body()
        # config records this one as returning Inner Mongolia, CHINA.
        self.assertNotIn('value="New Delhi"', body)
        # Same geoId as Gurgaon under LinkedIn's own label — offering both
        # lets someone pay twice for one city.
        self.assertNotIn('value="Gurugram"', body)

    def test_it_posts_one_field_not_a_repeated_one(self):
        # /estimate reads Object.fromEntries(new FormData(form)), which keeps
        # only the LAST value of a repeated key — the trap sites_present
        # exists for. One comma-joined hidden field cannot hit it.
        body = self.body()
        self.assertEqual(body.count('name="locations"'), 1)
        # The whole pick in ONE value, not one input per location — which is
        # what an x-for around this field would produce, and which renders
        # as a single tag either way, so the binding is what to check.
        self.assertRegex(
            body, r'type="hidden" name="locations" :value="picked\.join')

    def test_the_estimate_is_asked_after_the_field_is_written(self):
        # Alpine writes the hidden input on the NEXT tick, so dispatching the
        # change straight after a pick prices the PREVIOUS selection — the
        # cost panel would trail the control by one click, which on a money
        # display is the whole problem.
        body = self.body()
        self.assertRegex(body, r"\$nextTick\(\(\) =(&gt;|>) this\.\$dispatch")

    def test_the_current_pick_comes_back_into_the_control(self):
        body = self.body({"profile": "kanav", "cap_usd": 8.41,
                          "derived": DERIVED, "locations": ["Delhi", "Germany"],
                          "linkedin_locations": ["Delhi", "Germany"]})
        self.assertIn('picked: ["Delhi", "Germany"]', body)

    def test_a_scope_default_is_not_shown_as_a_deliberate_pick(self):
        # state["locations"] is also what the scope choice sets. Rendering
        # that as chips would show a choice the user never made — and post it
        # straight back as one.
        body = self.body({"profile": "kanav", "cap_usd": 8.41,
                          "derived": DERIVED, "locations": ["Delhi", "Mumbai"]})
        self.assertIn("picked: []", body)

    # ---- what it accepts -------------------------------------------------
    def test_picking_locations_narrows_both_lists(self):
        # SITES[site].get("locations", SEARCH["locations"]) means LinkedIn —
        # the most expensive site — keeps config's own default unless
        # linkedin_locations is set too.
        app = self._app()
        r = app.test_client().post("/estimate", json={
            "scope": "india", "locations": "Delhi, Germany"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.state["locations"], ["Delhi", "Germany"])
        self.assertEqual(self.state["linkedin_locations"], ["Delhi", "Germany"])

    def test_an_empty_pick_leaves_the_scope_alone(self):
        # "All locations" is the absence of a narrowing, not a location.
        app = self._app()
        app.test_client().post("/estimate", json={"scope": "india",
                                                   "locations": ""})
        self.assertEqual(self.state["locations"],
                         app_module._SCOPE["india"]["locations"])

    def test_an_unverified_location_is_refused_and_nothing_is_applied(self):
        app = self._app()
        for bad in ("Bangalore", "New Delhi", "Gurugram", "Atlantis"):
            r = app.test_client().post("/estimate", json={
                "scope": "india", "locations": f"Delhi, {bad}"})
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("verified geoId", r.get_json()["error"])
            # A partial form must never partially write.
            self.assertNotIn("locations", self.state)
            self.assertEqual(self.written, {})

    def test_a_location_reaches_the_rendered_profile(self):
        app = self._app()
        app.test_client().post("/estimate", json={"scope": "india",
                                                   "locations": "Bengaluru"})
        source = self.written["kanav"]
        self.assertIn("'Bengaluru'", source)
        # And in the LinkedIn block, not only in SEARCH.
        sites = source.split("SITES = {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("'Bengaluru'", sites)


class TestConfirmScreen(Isolated):
    def _app(self, cap=8.41, start_sweep=None, read_spend=None, check_token=None,
              state=None):
        # output_dir is injected — never the real output/kanav, which holds
        # real paid-sweep results with no git history to fall back on.
        output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, output_dir)
        # "derived" because POST /run re-renders the profile to stamp the
        # spend cap into it, exactly as /estimate does — by the time a real
        # session has a profile, review_post() has already populated it.
        base_state = {"profile": "kanav", "cap_usd": cap, "derived": DERIVED}
        base_state.update(state or {})
        app = app_module.create_app(
            state=base_state,
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=check_token or (lambda t: (cap, None)),
            fetch_plan=lambda profile: RAW_PLAN,
            start_sweep=start_sweep or (lambda profile: FakeProc()),
            read_spend=read_spend or (lambda: 1.00),
            env_path=os.path.join(output_dir, ".env"),
            output_dir=output_dir)
        app.config.update(TESTING=True)
        app.output_dir = output_dir  # so tests can assert where run.json landed
        self.env_path = os.path.join(output_dir, ".env")
        # write_env is NOT stubbed: it writes the temp .env above, and the app
        # then re-reads that file to re-verify every key. Stubbing it left the
        # attach flow writing nowhere and reading nothing, which is not the
        # flow. Tests that want to observe the write override it themselves.
        # POST /run writes profiles/<name>.py to stamp the spend cap in.
        # Stubbed and RECORDED, never the real profiles/ directory — the
        # sources land in app.written for the cap assertions below.
        app.written = []
        app.write_profile = lambda n, s: app.written.append((n, s))
        # A key on file, because that is this class's whole premise: the
        # confirm screen re-verifies what .env holds, so a fixture with a cap
        # in state and an empty file describes a session that has no key at
        # all — and every route here would redirect back to step 3.
        self.on_file({"APIFY_TOKEN": "primary-tok"})
        return app

    def on_file(self, keys):
        """Put these keys in the .env the app reads.

        A real file, not a patched os.environ: the two disagree the moment
        someone edits .env by hand, and telling them apart is the whole point
        of read_env_tokens(). Tests that patched the environment were
        describing a place the app no longer treats as the record.
        """
        pathlib.Path(self.env_path).write_text(
            "".join(f"{name}={token}\n" for name, token in keys.items()))

    def test_confirm_names_the_amount_on_the_button(self):
        body = self._app().test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("2.70", body)
        self.assertIn("Run the sweep", body)

    def test_confirm_states_the_depth_each_rate_is_priced_at(self):
        body = self._app().test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("each at 25 results", body)
        self.assertIn("scaled from the depth it was measured at", body)
        self.assertIn("per-run minimum", body)

    def test_confirm_states_the_hard_stop_beside_the_estimate(self):
        # The first true statement this screen can make about a real cap, so
        # it sits next to the figure. 2.70 x 1.25 = 3.38.
        body = self._app().test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("$3.38", body)
        self.assertIn("even if searches are left", body)

    def test_over_cap_still_offers_the_remedies_first(self):
        # Was "...instead of the run button", and passed after the button
        # came back only because the over-cap one is worded differently —
        # green for the wrong reason. The remedies are still what the panel
        # leads with; the difference is that it is no longer a dead end.
        body = self._app(cap=1.00).test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("another key", body.lower())
        self.assertIn("Narrow the search instead", body)

    def test_over_cap_can_be_started_anyway_but_not_by_one_click(self):
        # It is the user's account, and a sweep that stops partway loses
        # nothing: the engine stops when the account is spent, .done_combos
        # keeps the finished searches from being re-billed. What must not
        # happen is starting one by mis-click, so the control is gated.
        body = self._app(cap=1.00).test_client().get("/confirm").get_data(as_text=True)
        self.assertIn('name="over_cap_ack"', body)
        self.assertIn("Start it anyway", body)
        # Disabled until the box is ticked, and the copy says what "anyway"
        # actually gets you.
        self.assertIn(":disabled=\"sent || !ack\"", body)
        flat = " ".join(body.split())
        self.assertIn("stop, partway through the plan", flat)

    def test_a_funded_plan_is_not_gated(self):
        # The gate is the difference between the two states; putting it on
        # both would make it noise that gets ticked without reading.
        body = self._app(cap=8.41).test_client().get("/confirm").get_data(as_text=True)
        self.assertNotIn('name="over_cap_ack"', body)
        self.assertIn(':disabled="sent"', body)

    def test_an_over_cap_run_without_the_acknowledgement_starts_nothing(self):
        launched = []
        app = self._app(cap=1.00,
                        start_sweep=lambda p: launched.append(p) or FakeProc())
        client = app.test_client()
        client.get("/confirm")
        r = client.post("/run")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Tick the box", r.get_data(as_text=True))
        self.assertEqual(launched, [])
        self.assertNotIn("proc", app.state)

    def test_an_acknowledged_over_cap_run_starts(self):
        launched = []
        app = self._app(cap=1.00,
                        start_sweep=lambda p: launched.append(p) or FakeProc())
        client = app.test_client()
        client.get("/confirm")
        r = client.post("/run", data={"over_cap_ack": "yes"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/running", r.headers["Location"])
        self.assertEqual(launched, ["kanav"])
        # The hard stop is still stamped into the profile: proceeding past
        # the cap does not mean proceeding without one.
        self.assertAlmostEqual(app.state["max_spend_usd"],
                               app.state["plan"]["spend_cap"], places=2)

    def test_over_cap_says_how_much_to_cut(self):
        body = self._app(cap=1.00).test_client().get("/confirm").get_data(as_text=True)
        self.assertIn("1.70", body)

    def test_running_records_the_spend_baseline_so_the_meter_shows_a_delta(self):
        # Order-sensitive: read_spend must run BEFORE start_sweep, since
        # account_usage_usd is month-to-date and the baseline has to be
        # captured before the sweep can add to it. A test where both fakes
        # return fixed, order-insensitive values would pass identically if
        # the two lines in /run were swapped — so both fakes record into a
        # shared list instead.
        calls = []

        def read_spend():
            calls.append("read_spend")
            return 1.00

        def start_sweep(profile):
            calls.append("start_sweep")
            return FakeProc()

        app = self._app(start_sweep=start_sweep, read_spend=read_spend)
        app.test_client().get("/confirm")
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/running", r.headers["Location"])
        self.assertEqual(calls, ["read_spend", "start_sweep"])
        # account_usage_usd is month-to-date, so without a baseline the meter
        # would show the whole month instead of this sweep.
        self.assertAlmostEqual(app.state["baseline_usd"], 1.00, places=2)
        self.assertIsNotNone(app.state["proc"])

    def test_a_second_sweep_cannot_be_launched_while_one_is_running(self):
        # The paid operation had no in-flight guard while /rescore, the FREE
        # one twelve lines below it, had a lock and a docstring explaining
        # this exact hazard. Two children on one profile append to the same
        # .done_combos and truncate the same jobs_*.json, so the second
        # re-bills searches the first already paid for — and app.state["proc"]
        # holds one child, so the first is orphaned and /stop cannot see it.
        launches = []
        app = self._app(start_sweep=lambda profile: (
            launches.append(profile) or FakeProc()))
        client = app.test_client()
        client.get("/confirm")
        first = client.post("/run")
        self.assertEqual(first.status_code, 302)

        second = client.post("/run")
        self.assertEqual(second.status_code, 409)
        self.assertIn("already running", second.get_data(as_text=True))
        self.assertEqual(launches, ["kanav"])       # one child, not two

    def test_two_simultaneous_run_posts_launch_one_sweep(self):
        # The GUARD is pinned by the test above, but the LOCK is not: with
        # _run_lock removed, two sequential posts still behave correctly while
        # two concurrent ones launch two paid children on one profile. The
        # route makes a live Apify call before it redirects and the dev server
        # is threaded, so this is a double-click away.
        import threading

        launches = []
        # Synchronise BEFORE the request, not inside read_spend: with the lock
        # working only one thread ever reaches read_spend, so a barrier in
        # there would deadlock on the correct behaviour.
        at_the_door = threading.Barrier(2)

        def slow_read_spend():
            time.sleep(0.1)            # widen the check-to-set window
            return 1.00

        app = self._app(read_spend=slow_read_spend,
                        start_sweep=lambda profile: (
                            launches.append(profile) or FakeProc()))
        app.test_client().get("/confirm")

        codes = []
        errors = []

        def post():
            try:
                at_the_door.wait(timeout=5)
                codes.append(app.test_client().post("/run").status_code)
            except Exception as exc:       # a swallowed thread error would
                errors.append(repr(exc))   # otherwise read as an empty list

        threads = [threading.Thread(target=post) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(sorted(codes), [302, 409])
        self.assertEqual(launches, ["kanav"])      # one paid child, not two

    def test_a_finished_sweep_does_not_block_the_next_one(self):
        class Finished:
            def poll(self):
                return 0

        launches = []
        app = self._app(start_sweep=lambda profile: (
            launches.append(profile) or Finished()))
        client = app.test_client()
        client.get("/confirm")
        client.post("/run")
        self.assertEqual(client.post("/run").status_code, 302)
        self.assertEqual(launches, ["kanav", "kanav"])

    def test_a_refused_run_does_not_rewrite_the_profile(self):
        # The profile rewrite used to sit outside the lock and before the
        # in-flight check, so a request answered with 409 had already
        # rewritten profiles/<name>.py.
        writes = []
        app = self._app(start_sweep=lambda profile: FakeProc())
        app.write_profile = lambda n, s: writes.append(n)
        client = app.test_client()
        client.get("/confirm")
        client.post("/run")
        self.assertEqual(writes, ["kanav"])
        self.assertEqual(client.post("/run").status_code, 409)
        self.assertEqual(writes, ["kanav"])        # unchanged by the refusal

    def test_run_stamps_a_real_spend_cap_into_the_profile_before_launching(self):
        # The one guard that actually stops an overspend is
        # SETTINGS["max_spend_usd"] inside scraper.py. Everything this UI
        # displays is advisory, so the cap has to be WRITTEN — and written
        # before the child starts, or the sweep it is meant to bound is
        # already running uncapped. Order-recorded, not just asserted
        # present: a write that happened after the launch would otherwise
        # pass identically.
        calls = []
        app = self._app(start_sweep=lambda profile: calls.append("start_sweep") or FakeProc(),
                        read_spend=lambda: calls.append("read_spend") or 1.00)
        app.write_profile = lambda n, s: calls.append("write_profile") or app.written.append((n, s))
        app.test_client().get("/confirm")
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(calls, ["write_profile", "read_spend", "start_sweep"])

        name, source = app.written[-1]
        self.assertEqual(name, "kanav")
        # 2.70 estimate x 1.25 headroom. A cap equal to the estimate would
        # abort a sweep that lands 10% high, which /confirm tells the user
        # to expect.
        self.assertIn('"max_spend_usd": 3.38', source)
        self.assertAlmostEqual(app.state["max_spend_usd"], 3.38, places=2)

    def test_a_tiny_sweep_is_not_capped_below_a_single_search(self):
        # Naukri alone is $0.50 per run minimum, so a cap under that would
        # stop the sweep before its first search could complete.
        app = self._app()
        with mock.patch.dict(app_module.config.SITE_RATES,
                             {"linkedin": 0.001, "indeed": 0.0}, clear=True):
            app.test_client().get("/confirm")
            app.test_client().post("/run")
        self.assertAlmostEqual(app.state["max_spend_usd"],
                               app_module.SPEND_CAP_FLOOR_USD, places=2)
        self.assertIn('"max_spend_usd": 0.5', app.written[-1][1])

    def test_a_missing_spend_reading_is_recorded_as_unknown_not_zero(self):
        # read_spend() returning None (no token, or the account-usage call
        # failed) must not collapse into a $0.00 baseline — Task 8 would
        # then subtract 0 from month-to-date spend and report the user's
        # entire month as this one sweep's cost.
        app = self._app(read_spend=lambda: None)
        app.test_client().get("/confirm")
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(app.state["baseline_usd"])
        run_path = os.path.join(app.output_dir, "kanav", "run.json")
        with open(run_path) as fh:
            import json
            self.assertIsNone(json.load(fh)["baseline_usd"])

    def test_run_json_lands_in_the_injected_output_dir_not_the_real_one(self):
        # Isolation enforced by a test, not by convention — a hardcoded
        # REPO_ROOT/output/<profile> here would let a later test collide
        # with a real profile's real (unrecoverable, gitignored) results.
        app = self._app()
        app.test_client().get("/confirm")
        app.test_client().post("/run")
        run_path = os.path.join(app.output_dir, "kanav", "run.json")
        self.assertTrue(os.path.exists(run_path))
        real_path = os.path.join(app_module.REPO_ROOT, "output", "kanav", "run.json")
        self.assertFalse(os.path.exists(real_path))

    def test_a_sweep_over_the_cap_cannot_be_started_from_the_ui(self):
        app = self._app(cap=1.00)
        app.test_client().get("/confirm")
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(app.state.get("proc"))

    def test_confirm_without_a_connected_key_goes_to_the_key_screen(self):
        app = app_module.create_app(state={"profile": "kanav"},
                                    extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED,
                                    fetch_plan=lambda profile: RAW_PLAN)
        app.config.update(TESTING=True)
        r = app.test_client().get("/confirm")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/key", r.headers["Location"])

    def test_run_without_a_connected_key_refuses_and_launches_nothing(self):
        # A cap of None makes over_cap False for any total, so this must not
        # fall through to "under cap, go ahead".
        app = self._app(state={"plan": dict(RAW_PLAN, total=2.70,
                                            total_searches=46, over_cap=False,
                                            spend_cap=3.38)})
        app.state.pop("cap_usd")
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(app.state.get("proc"))
        self.assertEqual(app.written, [])

    def test_run_with_no_plan_at_all_fails_closed(self):
        # A direct POST /run against empty state (never went through
        # /confirm, no plan computed at all) must refuse exactly like an
        # over-cap plan does — not launch an uncapped subprocess because a
        # missing plan's .get("over_cap") reads just as falsy as a real
        # "under cap" plan would.
        output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, output_dir)
        app = app_module.create_app(
            state={}, start_sweep=lambda p: FakeProc(), read_spend=lambda: 1.00,
            output_dir=output_dir)
        app.config.update(TESTING=True)
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(app.state.get("proc"))
        self.assertIsNone(app.state.get("baseline_usd"))
        self.assertEqual(os.listdir(output_dir), [])

    def test_second_key_with_no_plan_fails_closed_not_500(self):
        app = app_module.create_app(state={"cap_usd": 8.41})
        app.config.update(TESTING=True)
        r = app.test_client().post("/second-key", data={"token": "whatever"})
        self.assertEqual(r.status_code, 400)
        self.assertAlmostEqual(app.state["cap_usd"], 8.41, places=2)

    def test_another_key_raises_the_cap_to_the_best_account_not_the_sum(self):
        # This asserted cap == 3.00, i.e. 1.00 + 2.00. Summing is wrong:
        # scraper._require_token() builds ONE ApifyClient for the whole run
        # from the single account with the most headroom, and the
        # credit-exhausted branch tells the user to RERUN with another token.
        # So no sweep spends across two accounts, and the summed cap told the
        # meter a $2.50 plan was affordable on a $1 and a $2 account that
        # could not fund it between them — with over_cap False, so /run let
        # it start. The total is still reported, separately, because
        # .done_combos does let a stopped sweep resume on the next key.
        # A real $1 account and a real $2 one, each verified on its own: the
        # figures used to lean on state's cap as a stand-in for the primary
        # key's balance, which no longer exists — every key is re-read.
        balances = {"primary-tok": 1.00, "different-tok": 2.00}
        app = self._app(cap=1.00,
                        check_token=lambda t: (balances.get(t, 0.0), None))
        self.on_file({"APIFY_TOKEN": "primary-tok"})
        app.test_client().get("/confirm")
        r = app.test_client().post(
            "/second-key", data={"token": "different-tok"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/confirm", r.headers["Location"])
        self.assertAlmostEqual(app.state["cap_usd"], 2.00, places=2)
        self.assertAlmostEqual(app.state["credit_total_usd"], 3.00, places=2)

    def test_a_weaker_extra_key_does_not_raise_what_one_sweep_can_spend(self):
        # The direction that matters for fail-closed: attaching a $0.50 key
        # beside a $4.00 one must not move the cap at all, because the sweep
        # still runs on the $4.00 account. Under the old sum it read $4.50.
        balances = {"primary-tok": 4.00, "small-tok": 0.50}
        app = self._app(cap=4.00,
                        check_token=lambda t: (balances.get(t, 0.0), None))
        self.on_file({"APIFY_TOKEN": "primary-tok"})
        app.test_client().get("/confirm")
        app.test_client().post("/second-key", data={"token": "small-tok"})
        self.assertAlmostEqual(app.state["cap_usd"], 4.00, places=2)
        # The total still rises: .done_combos lets a stopped sweep resume on
        # the smaller key without re-billing what finished.
        self.assertAlmostEqual(app.state["credit_total_usd"], 4.50, places=2)
        self.assertAlmostEqual(app.state["credit_total_usd"], 4.50, places=2)

    def test_a_third_and_fourth_key_get_their_own_slots(self):
        # /second-key wrote APIFY_TOKEN_2 unconditionally, so a third key
        # overwrote the second: its credit was counted while its token was
        # gone from .env, and the engine never saw it.
        app = self._app(cap=1.00, check_token=lambda t: (2.00, None))
        written = {}
        app.write_env = lambda name, value: written.__setitem__(name, value)
        self.on_file({"APIFY_TOKEN": "tok-1", "APIFY_TOKEN_2": "tok-2"})
        app.test_client().get("/confirm")
        app.test_client().post("/second-key", data={"token": "tok-3"})
        # The second attach only lands in a fresh slot if the first one
        # reached the file — write_env is stubbed here, so it is written by
        # hand, the way .env would already hold it.
        self.on_file({"APIFY_TOKEN": "tok-1", "APIFY_TOKEN_2": "tok-2",
                      "APIFY_TOKEN_3": "tok-3"})
        app.test_client().post("/second-key", data={"token": "tok-4"})
        self.assertEqual(written, {"APIFY_TOKEN_3": "tok-3",
                                    "APIFY_TOKEN_4": "tok-4"})

    def test_a_key_already_in_a_later_slot_is_still_rejected(self):
        # The duplicate check looked at APIFY_TOKEN and APIFY_TOKEN_2 only, so
        # re-pasting the key sitting in APIFY_TOKEN_3 counted its credit a
        # second time — the inflated-cap outcome the check exists to stop.
        app = self._app(cap=1.00, check_token=lambda t: (2.00, None))
        self.on_file({"APIFY_TOKEN": "tok-1", "APIFY_TOKEN_3": "tok-3"})
        app.test_client().get("/confirm")
        before = app.state["cap_usd"]
        r = app.test_client().post("/second-key", data={"token": "tok-3"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("same key", r.get_data(as_text=True).lower())
        self.assertAlmostEqual(app.state["cap_usd"], before, places=2)

    def test_the_over_cap_panel_does_not_promise_a_mid_sweep_key_switch(self):
        # The copy said the sweep "runs on your first key until its credit is
        # gone, then continues on the second". It cannot: the client is built
        # once per run. Promising it is how a user attaches a key and lets an
        # unaffordable sweep start.
        app = self._app(cap=0.10, check_token=lambda t: (2.00, None))
        body = app.test_client().get("/confirm").get_data(as_text=True)
        flat = " ".join(body.split())
        self.assertNotIn("then continues on the second", flat)
        self.assertIn("one account", flat)

    def test_second_key_rejects_the_token_already_on_file(self):
        app = self._app(cap=1.00, check_token=lambda t: (2.00, None))
        self.on_file({"APIFY_TOKEN": "same-tok"})
        app.test_client().get("/confirm")
        before = app.state["cap_usd"]
        r = app.test_client().post("/second-key", data={"token": "same-tok"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("same key", r.get_data(as_text=True).lower())
        # The point of the check: a key already on file adds no credit.
        self.assertAlmostEqual(app.state["cap_usd"], before, places=2)

    def test_second_key_rejects_an_empty_token_the_way_the_key_screen_does(self):
        # /key answered this with "Paste your Apify token."; /second-key sent
        # the blank straight to check_token and reported it as "rejected by
        # Apify", blaming the service for the user's empty field.
        calls = []
        app = self._app(check_token=lambda t: calls.append(t) or (5.0, None))
        client = app.test_client()
        client.get("/confirm")          # establishes the plan
        calls.clear()                   # ...and re-verifies what is on file
        r = client.post("/second-key", data={"token": "  "})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Paste your Apify token", r.get_data(as_text=True))
        self.assertEqual(calls, [])          # no network call for a blank

    def test_second_key_rejects_a_malformed_token_before_the_write_funnel(self):
        # A value with a space used to reach app.write_env, where the
        # allowlist raises ValueError — a 500 rather than a clean message.
        calls = []
        app = self._app(check_token=lambda t: calls.append(t) or (5.0, None))
        client = app.test_client()
        client.get("/confirm")          # establishes the plan
        calls.clear()
        r = client.post("/second-key", data={"token": "abc def"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("printable characters", r.get_data(as_text=True))
        self.assertEqual(calls, [])

    def test_second_key_that_verifies_with_zero_credit_shows_a_message(self):
        balances = {"primary-tok": 1.00, "zero-credit-tok": 0.0}
        app = self._app(cap=1.00,
                        check_token=lambda t: (balances.get(t, 0.0), None))
        app.test_client().get("/confirm")
        before = app.state["cap_usd"]
        r = app.test_client().post(
            "/second-key", data={"token": "zero-credit-tok"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no credit", r.get_data(as_text=True).lower())
        # Refused, so it changed nothing — including not being written to
        # .env and counted on the next refresh.
        self.assertAlmostEqual(app.state["cap_usd"], before, places=2)
        self.assertNotIn("zero-credit-tok",
                         pathlib.Path(self.env_path).read_text())


import json as _json
import signal as _signal


# RAW_PLAN builds its searches as [{...}] * 32 — 32 references to one
# identical dict, so combo_keys yields 32 byte-identical keys and
# done=keys[:5] collapses to a one-element set. This fixture uses distinct
# keywords per search so done/outstanding counts are real (see task-8-brief
# Amendment A1). 46 searches total, matching every "46" the given tests check.
RUNNING_PLAN = {
    "profile": "kanav",
    "sites": {
        "linkedin": [{"keywords": f"kw{i}", "location": "India", "company": ""}
                     for i in range(32)],
        "indeed": [{"keywords": f"kw{i}", "location": "Pune", "company": ""}
                   for i in range(14)],
    },
    "free_sources": 6,
}


class TestKeysOnFileAreTheRecord(Isolated):
    """os.environ is loaded once at start-up and load_dotenv() does not
    override what is already there, so a key deleted from .env by hand stayed
    visible to this process for as long as the server ran — and to the engine,
    which inherits the environment. That is why deleting a key and re-adding
    it still answered "same key already on file"."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.env_path = os.path.join(self.dir, ".env")
        self.balances = {}

    def on_file(self, keys):
        pathlib.Path(self.env_path).write_text(
            "".join(f"{n}={t}\n" for n, t in keys.items()))

    def _app(self, state=None):
        app = app_module.create_app(
            state=state if state is not None else {
                "profile": "kanav", "cap_usd": 5.00, "derived": DERIVED},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (self.balances.get(t, 1.00), None),
            fetch_plan=lambda profile: RAW_PLAN,
            start_sweep=lambda profile: FakeProc(),
            read_spend=lambda: 1.00,
            env_path=self.env_path, output_dir=self.dir)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, src: None
        return app

    def test_a_key_deleted_from_the_file_can_be_added_again(self):
        # The reported bug, end to end.
        self.on_file({"APIFY_TOKEN": "tok-a"})
        app = self._app()
        client = app.test_client()
        client.get("/confirm")
        # The user deletes it by hand and pastes it back.
        self.on_file({})
        r = client.post("/second-key", data={"token": "tok-a"})
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True))
        self.assertIn("tok-a", pathlib.Path(self.env_path).read_text())

    def test_a_stale_environment_variable_is_not_a_key_on_file(self):
        self.on_file({})
        app = self._app()
        client = app.test_client()
        with mock.patch.dict(os.environ, {"APIFY_TOKEN_9": "ghost-tok"}):
            client.get("/confirm")
            r = client.post("/second-key", data={"token": "ghost-tok"})
            self.assertEqual(r.status_code, 302, r.get_data(as_text=True))
            # And the ghost is gone from the environment the ENGINE inherits,
            # or a sweep would go on spending from a detached account.
            self.assertNotIn("APIFY_TOKEN_9", os.environ)

    def test_a_key_that_really_is_on_file_is_still_refused(self):
        # The check still earns its keep: re-pasting a key adds no credit
        # while looking like it did.
        self.on_file({"APIFY_TOKEN": "tok-a"})
        app = self._app()
        client = app.test_client()
        client.get("/confirm")          # establishes the plan
        r = client.post("/second-key", data={"token": "tok-a"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("same key", r.get_data(as_text=True).lower())

    def test_a_quoted_value_is_the_same_key(self):
        # load_dotenv strips quotes, so a reader that does not would compare
        # a quoted string against a bare one and call one key two.
        pathlib.Path(self.env_path).write_text('APIFY_TOKEN="tok-a"\n')
        client = self._app().test_client()
        client.get("/confirm")
        r = client.post("/second-key", data={"token": "tok-a"})
        self.assertEqual(r.status_code, 400)

    def test_a_freed_slot_is_refilled_rather_than_skipped(self):
        # next_token_name picks by NAME so a slot emptied by hand gets
        # reused — which only works if it is reading the file that was
        # edited.
        self.on_file({"APIFY_TOKEN": "tok-a", "APIFY_TOKEN_3": "tok-c"})
        app = self._app()
        written = {}
        app.write_env = lambda name, value: written.__setitem__(name, value)
        client = app.test_client()
        client.get("/confirm")
        client.post("/second-key", data={"token": "tok-new"})
        self.assertEqual(written, {"APIFY_TOKEN_2": "tok-new"})


class TestCreditLeft(Isolated):
    """Credit left is every account's credit added up, and it has to keep up
    with a run rather than standing still at what it was when a key was last
    verified."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.env_path = os.path.join(self.dir, ".env")
        pathlib.Path(self.env_path).write_text(
            "APIFY_TOKEN=tok-a\nAPIFY_TOKEN_2=tok-b\n")
        self.balances = {"tok-a": 5.00, "tok-b": 3.33}
        self.reads = []

    def _app(self, state=None, **kw):
        def check(token):
            self.reads.append(token)
            return self.balances.get(token, 0.0), None
        app = app_module.create_app(
            state=state if state is not None else {
                "profile": "kanav", "cap_usd": 5.00, "derived": DERIVED},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=check, fetch_plan=lambda profile: RAW_PLAN,
            read_rows=lambda profile: [],
            read_done=kw.pop("read_done", lambda p, d: []),
            read_spend=kw.pop("read_spend", lambda: 1.00),
            start_sweep=lambda profile: FakeProc(),
            env_path=self.env_path, output_dir=self.dir, **kw)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, src: None
        return app

    def test_the_confirm_screen_re_reads_every_key(self):
        app = self._app()
        body = app.test_client().get("/confirm").get_data(as_text=True)
        self.assertEqual(sorted(self.reads), ["tok-a", "tok-b"])
        self.assertIn("Credit left $8.33", body)
        # And the cap stays the best SINGLE account, because a sweep spends
        # from one.
        self.assertAlmostEqual(app.state["cap_usd"], 5.00, places=2)

    def test_the_price_of_a_plan_is_not_taken_off_the_remaining_credit(self):
        # /configure and /confirm pass the ESTIMATE as `spend` — it is the
        # meter's "about to spend", not a payment. Subtracting it showed
        # $5.63 of $8.33 left before a single search had run, and the figure
        # this screen exists to make trustworthy is exactly that one: it is
        # what someone reads to decide whether the plan is affordable.
        app = self._app()
        client = app.test_client()
        # /confirm first: it is what verifies the keys, so the total exists.
        for path in ("/confirm", "/configure"):
            body = client.get(path).get_data(as_text=True)
            self.assertIn("Credit left $8.33", body, path)

    def test_a_key_that_cannot_be_read_keeps_its_last_known_figure(self):
        # The limits endpoint is a live call. On a blip every key comes back
        # unknown; if that discarded the figures, cap_usd would go None and
        # needs_key() reads exactly that — so a hiccup would bounce someone
        # back to step 3 mid-flow. A read that failed is not evidence of
        # anything, least of all of an empty account.
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 5.00, "derived": DERIVED,
                   "key_credit": {"APIFY_TOKEN": 5.00, "APIFY_TOKEN_2": 3.33}},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (None, "network is down"),
            fetch_plan=lambda profile: RAW_PLAN,
            read_spend=lambda: 1.00, start_sweep=lambda p: FakeProc(),
            env_path=self.env_path, output_dir=self.dir)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, src: None
        r = app.test_client().get("/confirm")
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(app.state["credit_total_usd"], 8.33, places=2)
        self.assertAlmostEqual(app.state["cap_usd"], 5.00, places=2)
        self.assertIn("Credit left $8.33", r.get_data(as_text=True))

    def test_the_figure_drops_by_what_this_sweep_has_spent(self):
        # Otherwise it stands still at the start-of-run total for forty
        # minutes while the money goes out.
        app = self._app(state={
            "profile": "kanav", "cap_usd": 5.00, "derived": DERIVED,
            "credit_total_usd": 8.33, "baseline_usd": 1.00,
            "raw_plan": RAW_PLAN, "proc": FakeProc(),
            "plan": {"total": 2.70, "total_searches": 46, "over_cap": False,
                     "lines": [], "spend_cap": 3.38}},
            read_spend=lambda: 2.50, read_done=lambda p, d: [])
        body = app.test_client().get("/running").get_data(as_text=True)
        # 8.33 total less 1.50 spent by this sweep.
        self.assertIn("Credit left $6.83", body)

    def test_the_live_figure_charges_this_sweep_once_not_twice(self):
        # The header figure already has this sweep's spend taken off. The
        # live expression takes p.spend off its OWN base on every tick, so
        # that base must be the total — starting it from credit_left billed
        # the sweep a second time the moment the first batch landed.
        app = self._app(state={
            "profile": "kanav", "cap_usd": 5.00, "derived": DERIVED,
            "credit_total_usd": 8.33, "baseline_usd": 1.00,
            "raw_plan": RAW_PLAN, "proc": FakeProc(),
            "plan": {"total": 2.70, "total_searches": 46, "over_cap": False,
                     "lines": [], "spend_cap": 3.38}},
            read_spend=lambda: 2.50, read_done=lambda p, d: [])
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn("8.33 - p.spend", body)
        self.assertNotIn("6.83 - p.spend", body)

    def test_the_results_screen_re_reads_the_keys_after_the_sweep(self):
        # The sweep has just spent the money, so this is the one moment the
        # balances are certain to have moved.
        app = self._app(state={"profile": "kanav", "cap_usd": 5.00,
                               "derived": DERIVED, "credit_total_usd": 8.33})
        self.balances = {"tok-a": 3.10, "tok-b": 1.00}
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertEqual(sorted(self.reads), ["tok-a", "tok-b"])
        self.assertIn("Credit left $4.10", body)

    def test_a_month_to_date_figure_is_never_subtracted_as_this_sweep(self):
        # With no baseline there is no way to tell this sweep's spend from
        # the account's month, and subtracting the month would understate the
        # remaining credit by everything spent before today.
        app = self._app(state={
            "profile": "kanav", "cap_usd": 5.00, "derived": DERIVED,
            "credit_total_usd": 8.33, "baseline_usd": None,
            "raw_plan": RAW_PLAN, "proc": FakeProc(),
            "plan": {"total": 2.70, "total_searches": 46, "over_cap": False,
                     "lines": [], "spend_cap": 3.38}},
            read_spend=lambda: 40.0, read_done=lambda p, d: [])
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn("Credit left $8.33", body)


class TestResultsDensity(Isolated):
    """A real sweep is 1600 rows with a dozen matched skills each. The screen
    has to stay scannable at that size, not only at the fixture's six."""

    def _app(self, rows):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "derived": DERIVED},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None), fetch_plan=lambda p: RAW_PLAN,
            read_rows=lambda profile: rows, read_spend=lambda: None,
            env_path=os.path.join(tempfile.mkdtemp(), ".env"),
            output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        return app

    def _many(self, n):
        # All in one bucket, so the cap is what decides the count on screen.
        return [dict(ROWS[3], title=f"Role {i}", score=str(100 - i))
                for i in range(n)]

    def test_a_long_section_shows_its_best_and_offers_the_rest(self):
        cap = app_module.SECTION_CAP
        body = self._app(self._many(cap + 12)).test_client().get(
            "/results").get_data(as_text=True)
        self.assertEqual(body.count('class="rowlink"'), cap)
        flat = " ".join(body.split())
        self.assertIn(f"Showing the top {cap} of {cap + 12}", flat)
        self.assertIn(f"Show all {cap + 12}", flat)
        # The best ones, not an arbitrary N: the cap is applied after the
        # sort, so the row ranked last must be the one held back.
        self.assertIn("Role 0", body)
        self.assertNotIn(f"Role {cap + 11}", body)

    def test_show_all_lifts_the_cap_and_offers_the_way_back(self):
        cap = app_module.SECTION_CAP
        body = self._app(self._many(cap + 12)).test_client().get(
            "/results?full=1").get_data(as_text=True)
        self.assertEqual(body.count('class="rowlink"'), cap + 12)
        flat = " ".join(body.split())
        self.assertIn(f"Showing all {cap + 12}", flat)
        self.assertIn(f"Back to the top {cap}", flat)

    def test_a_short_section_is_offered_no_expansion(self):
        body = self._app(self._many(3)).test_client().get(
            "/results").get_data(as_text=True)
        self.assertNotIn("Showing the top", body)
        self.assertNotIn("section-foot", body)

    def test_the_cap_is_display_only_and_the_export_is_whole(self):
        # The file is what someone analyses. Handing them 25 of 37 rows
        # because a screen was paginated would be a silent data loss.
        client = self._app(self._many(app_module.SECTION_CAP + 12)).test_client()
        rows = json.loads(client.get("/export.json").get_data())
        self.assertEqual(len(rows), app_module.SECTION_CAP + 12)

    def test_a_dozen_matched_skills_collapse_to_a_counted_chip(self):
        # Twelve of them wrap to three lines and make every row three times
        # as tall, on the screen whose job is scanning down a list.
        row = dict(ROWS[0], matched_skills="a, b, c, d, e, f, g, h, i")
        body = self._app([row]).test_client().get("/results").get_data(as_text=True)
        self.assertEqual(body.count('<span class="tag">'), 6)
        # The rest are still reachable, and the count says they exist.
        self.assertIn('title="g, h, i">+3<', body)

    def test_six_or_fewer_skills_are_all_shown_with_no_chip(self):
        row = dict(ROWS[0], matched_skills="a, b, c")
        body = self._app([row]).test_client().get("/results").get_data(as_text=True)
        self.assertEqual(body.count('<span class="tag">'), 3)
        self.assertNotIn("tag more", body)

    def test_a_row_says_how_old_the_posting_is(self):
        # "Newest first" was offered with no date anywhere on the screen, so
        # the order it produced could not be checked from the page.
        row = dict(ROWS[0],
                   date_posted=(datetime.date.today()
                                - datetime.timedelta(days=9)).isoformat())
        body = self._app([row]).test_client().get("/results").get_data(as_text=True)
        self.assertIn('<span class="posted">1w ago</span>', body)

    def test_an_undated_posting_claims_no_age(self):
        row = dict(ROWS[0], date_posted="")
        body = self._app([row]).test_client().get("/results").get_data(as_text=True)
        self.assertNotIn('class="posted"', body)


class TestPostedAge(Isolated):
    TODAY = datetime.date(2026, 9, 10)

    def test_it_reads_as_an_age_not_a_date(self):
        for iso, want in (("2026-09-10", "today"), ("2026-09-08", "2d ago"),
                          ("2026-08-25", "2w ago"), ("2026-06-02", "3mo ago")):
            self.assertEqual(app_module.posted_age(iso, self.TODAY), want, iso)

    def test_a_timestamp_is_read_by_its_date_prefix(self):
        # 353 real rows carry a full ISO-8601 timestamp rather than a date.
        self.assertEqual(
            app_module.posted_age("2026-08-25T10:00:00Z", self.TODAY), "2w ago")

    def test_nothing_usable_says_nothing(self):
        for iso in ("", None, "shortly", "2026-13-45"):
            self.assertEqual(app_module.posted_age(iso, self.TODAY), "", repr(iso))

    def test_a_date_in_the_future_is_bad_data_not_a_negative_age(self):
        self.assertEqual(app_module.posted_age("2026-09-20", self.TODAY), "")


class TestHowASweepEnded(Isolated):
    """Four endings, not two. "Stopped early" used to cover the user pressing
    Stop, the credit running out and the engine dying — three problems with
    three different answers, reported identically."""

    def _app(self, done=(), alive=False, stopped=False, credit=8.41,
             read_spend=None):
        proc = FakeProc()
        if not alive:
            proc.poll = lambda: 0
        state = {"profile": "kanav", "cap_usd": 8.41, "proc": proc,
                 "credit_total_usd": credit, "baseline_usd": 1.00,
                 "raw_plan": RUNNING_PLAN,
                 "plan": {"total": 2.70, "total_searches": 46,
                          "over_cap": False, "spend_cap": 3.38,
                          "lines": [{"site": "linkedin", "rate": 0.045,
                                     "searches": 32, "free": False,
                                     "subtotal": 1.44, "results": 25},
                                    {"site": "indeed", "rate": 0.09,
                                     "searches": 14, "free": False,
                                     "subtotal": 1.26, "results": 15}]}}
        if stopped:
            state["stopped_by_user"] = True
        env = os.path.join(tempfile.mkdtemp(), ".env")
        pathlib.Path(env).write_text("APIFY_TOKEN=tok-a\n")
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (credit, None),
            fetch_plan=lambda profile: RUNNING_PLAN,
            start_sweep=lambda profile: proc,
            read_spend=read_spend or (lambda: 2.42),
            read_done=lambda profile, day: set(done),
            read_rows=lambda profile: [],
            env_path=env, output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        return app, state

    def _state(self, **kw):
        app, _ = self._app(**kw)
        return app.test_client().get("/progress").get_json()

    # ---- the five states -------------------------------------------------

    def test_a_live_child_is_running(self):
        self.assertEqual(self._state(alive=True)["state"], "running")

    def test_every_search_done_is_finished(self):
        keys = app_module.planned_keys({"raw_plan": RUNNING_PLAN})
        self.assertEqual(self._state(done=keys)["state"], "finished")

    def test_the_user_pressing_stop_is_recorded_not_guessed(self):
        # The one ending the user caused. It ends as a dead child with work
        # left, exactly like running out of credit does.
        self.assertEqual(self._state(stopped=True)["state"], "stopped")

    def test_no_credit_left_for_even_the_cheapest_search_is_out_of_credit(self):
        # Cheapest outstanding search is linkedin at $0.045.
        self.assertEqual(self._state(credit=0.02)["state"], "out_of_credit")

    def test_an_early_end_with_credit_still_there_is_not_blamed_on_money(self):
        # Naming a cause we cannot evidence is how someone tops up a key
        # that was never the problem.
        p = self._state(credit=8.41)
        self.assertEqual(p["state"], "halted")
        self.assertIn("The reason is not recorded", self._body(credit=8.41))

    def test_pressing_stop_is_what_records_it(self):
        # The other tests seed the flag; this is the one that proves the
        # button sets it. Without it the ending falls through to whatever
        # the balance happens to say.
        app, state = self._app(alive=True)
        r = app.test_client().post("/stop")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(state["stopped_by_user"])
        # The child is signalled, not waited on, so the state it reports
        # next is what the screen will show.
        state["proc"].poll = lambda: 0
        self.assertEqual(app.test_client().get("/progress").get_json()["state"],
                         "stopped")

    def test_no_key_on_file_at_all_is_not_an_empty_key(self):
        # sweep_budget() reports a 0.00 TOTAL for an empty ledger, which is
        # "we know of nothing" rather than "there is nothing". Told apart by
        # whether any key was actually read.
        app, state = self._app(credit=0.0)
        state["key_credit"] = {}
        state["credit_total_usd"] = 0.0
        state["interrupt_credit_read"] = True  # do not re-read and refill it
        self.assertEqual(app.test_client().get("/progress").get_json()["state"],
                         "halted")

    def test_the_grid_marks_the_searches_that_never_ran(self):
        # "Not run YET" and "did not run" are the same cell until the sweep
        # stops, and then they are not.
        body = self._body(stopped=True)
        self.assertIn("p.state === 'running' ? '' : 'parked'", body)
        self.assertIn("did not run", body)

    def test_stop_wins_over_an_empty_balance(self):
        # Both true at once: the user stopped it AND the keys are spent. The
        # one they did deliberately is the one to report.
        self.assertEqual(self._state(stopped=True, credit=0.0)["state"],
                         "stopped")

    # ---- what it costs to carry on ---------------------------------------

    def test_the_screen_prices_what_is_left_to_run(self):
        # The figure the decision to resume turns on, and it was nowhere.
        keys = app_module.planned_keys({"raw_plan": RUNNING_PLAN})
        p = self._state(done=keys[:40])
        # 6 left: RUNNING_PLAN is 32 linkedin then 14 indeed, so these are
        # indeed at $0.09.
        self.assertEqual(p["outstanding"], 6)
        self.assertAlmostEqual(p["remaining_cost"], 0.54, places=2)

    def test_nothing_outstanding_costs_nothing_to_finish(self):
        keys = app_module.planned_keys({"raw_plan": RUNNING_PLAN})
        self.assertEqual(self._state(done=keys)["remaining_cost"], 0.0)

    # ---- the screen ------------------------------------------------------

    def _body(self, **kw):
        app, _ = self._app(**kw)
        return app.test_client().get("/running").get_data(as_text=True)

    def test_each_ending_names_itself(self):
        keys = app_module.planned_keys({"raw_plan": RUNNING_PLAN})
        # The heading says what happened; the banner says why. Both, so
        # neither can drift into contradicting the other.
        for kw, title, why in (
                (dict(done=keys), "Sweep finished", "Every search ran"),
                (dict(stopped=True), "Sweep stopped early", "You stopped it"),
                (dict(credit=0.0), "Sweep stopped early", "The credit ran out"),
                (dict(credit=8.41), "Sweep stopped early",
                 "The reason is not recorded")):
            body = self._body(**kw)
            self.assertIn(f"<h1 x-text=", body)
            self.assertIn(f">{title}</h1>", body, kw)
            self.assertIn(why, body, kw)

    def test_only_the_current_ending_is_shown_on_arrival(self):
        # All four blocks are in the DOM for Alpine to switch between over
        # SSE. Without the server-side display:none they would all paint at
        # once for the moment before Alpine boots — and with no JavaScript,
        # forever.
        body = self._body(stopped=True)
        block = body.split('x-show="p.state === \'stopped\'"')[1][:60]
        self.assertNotIn("display:none", block)
        for other in ("finished", "out_of_credit", "halted"):
            after = body.split("x-show=\"p.state === '%s'\"" % other)[1][:60]
            self.assertIn("display:none", after, other)

    def test_a_stopped_sweep_offers_to_carry_on_with_the_keys_it_has(self):
        body = self._body(stopped=True)
        self.assertIn("Continue the sweep", body)
        self.assertIn('href="/confirm"', body)

    def test_an_out_of_credit_sweep_leads_with_another_key(self):
        body = self._body(credit=0.0)
        self.assertIn("nothing left to spend on the keys you have", body)
        self.assertIn('action="/second-key"', body)

    def test_every_ending_can_keep_what_was_already_found(self):
        # Partial results are still results, and the export reads the file
        # already on disk.
        body = self._body(stopped=True)
        self.assertIn("/export.xlsx", body)
        self.assertIn("See what it\n            found", body)

    def test_a_finished_sweep_is_offered_no_recovery(self):
        keys = app_module.planned_keys({"raw_plan": RUNNING_PLAN})
        body = self._body(done=keys)
        self.assertIn("style=\"display:none\"",
                      body.split("Finish the remaining")[0][-400:])

    def test_a_new_run_forgets_the_last_one_s_ending(self):
        # Carried over, a resumed sweep that later died on its own would
        # report "you stopped it".
        app, state = self._app(stopped=True)
        state["derived"] = DERIVED
        app.test_client().post("/run")
        self.assertNotIn("stopped_by_user", state)

    def test_a_free_sweep_is_never_reported_as_out_of_credit(self):
        app, state = self._app(credit=0.0)
        state["free_only"] = True
        self.assertEqual(app.test_client().get("/progress").get_json()["state"],
                         "halted")


class TestPricingWhatIsLeft(Isolated):
    """The engine skips today's finished combos and does not re-bill them.
    scraper.py prints --dry-run --json before it loads that ledger, so the
    plan the UI prices is always the whole sweep."""

    def _app(self, done=()):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "derived": DERIVED},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RUNNING_PLAN,
            read_done=lambda profile, day: set(done),
            read_rows=lambda profile: [], read_spend=lambda: None,
            start_sweep=lambda profile: FakeProc(),
            env_path=os.path.join(tempfile.mkdtemp(), ".env"),
            output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        return app

    def test_a_fresh_plan_is_priced_whole(self):
        app = self._app()
        app.test_client().get("/confirm")
        self.assertEqual(app.state["plan"]["total_searches"], 46)
        self.assertEqual(app.state["plan"]["already_done"], 0)

    def test_resuming_is_priced_on_what_is_left(self):
        keys = app_module.planned_keys({"raw_plan": RUNNING_PLAN})
        app = self._app(done=keys[:40])
        body = app.test_client().get("/confirm").get_data(as_text=True)
        self.assertEqual(app.state["plan"]["total_searches"], 6)
        self.assertEqual(app.state["plan"]["already_done"], 40)
        # And it says why the figure shrank, or it reads as a pricing bug.
        self.assertIn("already done", body)

    def test_the_progress_grid_still_counts_the_whole_sweep(self):
        # Only the PRICE is of the remainder: a resumed sweep showing 0 of 6
        # would lose the 40 it already did.
        keys = app_module.planned_keys({"raw_plan": RUNNING_PLAN})
        app = self._app(done=keys[:40])
        app.test_client().get("/confirm")
        self.assertEqual(len(app_module.planned_keys(app.state)), 46)

    def test_a_second_sweep_the_same_day_says_it_would_fetch_nothing(self):
        # scraper.py's own comment records this shipping as a run that
        # "skipped every combo, scraped nothing, and still printed a
        # normal-looking summary".
        keys = app_module.planned_keys({"raw_plan": RUNNING_PLAN})
        body = self._app(done=keys).test_client().get(
            "/confirm").get_data(as_text=True)
        self.assertIn("Every search in this plan already ran today", body)

    def test_an_affordable_remainder_is_not_refused_as_over_cap(self):
        # The gate compared the WHOLE plan against the key. After an
        # interruption that refuses a resume the remainder easily affords.
        keys = app_module.planned_keys({"raw_plan": RUNNING_PLAN})
        app = self._app(done=keys[:44])
        app.state["cap_usd"] = 0.50
        app.test_client().get("/confirm")
        self.assertFalse(app.state["plan"]["over_cap"])


class TestExports(Isolated):
    """The shortlist as a file. It re-reads what is already on disk, so it
    costs nothing — but it has to describe the same rows the screen does."""

    def _app(self, rows=None):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "derived": DERIVED},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None), fetch_plan=lambda p: RAW_PLAN,
            read_rows=lambda profile: ROWS if rows is None else rows,
            read_spend=lambda: None,
            env_path=os.path.join(tempfile.mkdtemp(), ".env"),
            output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        return app

    def _sheet(self, body, name="Listings"):
        from openpyxl import load_workbook
        return load_workbook(io.BytesIO(body))[name]

    def test_every_format_downloads_as_a_named_file(self):
        client = self._app().test_client()
        for fmt, kind in (("csv", "text/csv"),
                          ("json", "application/json"),
                          ("xlsx", "spreadsheetml")):
            r = client.get(f"/export.{fmt}")
            self.assertEqual(r.status_code, 200, fmt)
            self.assertIn(kind, r.headers["Content-Type"], fmt)
            # An attachment with a name: without the disposition the browser
            # renders the CSV as a page of text instead of saving it.
            self.assertIn(f'attachment; filename="sweep-kanav-',
                          r.headers["Content-Disposition"], fmt)
            self.assertTrue(r.headers["Content-Disposition"].endswith(
                f'.{fmt}"'), fmt)

    def test_an_unknown_format_is_a_404_not_a_csv(self):
        # The extension in the path IS the format. Falling back to CSV would
        # hand someone a file that is not what its name says.
        self.assertEqual(self._app().test_client().get(
            "/export.pdf").status_code, 404)

    def test_the_file_holds_the_rows_the_screen_is_showing(self):
        # The count on the button comes from the page's own filter; the file
        # is built by a separate request. They read the same query string
        # through the same function, and this is what says so.
        client = self._app().test_client()
        page = client.get("/results?min=39").get_data(as_text=True)
        self.assertIn("Export 2 listings", page)
        rows = json.loads(client.get("/export.json?min=39").get_data())
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["Score"] >= 39 for r in rows))

    def test_the_search_and_sort_travel_with_the_export(self):
        client = self._app().test_client()
        rows = json.loads(client.get(
            "/export.json?q=gitlab").get_data())
        self.assertEqual([r["Company"] for r in rows], ["GitLab"])
        by_score = json.loads(client.get("/export.json").get_data())
        by_date = json.loads(client.get("/export.json?sort=recent").get_data())
        self.assertNotEqual([r["Role"] for r in by_score],
                            [r["Role"] for r in by_date])

    def test_each_row_carries_the_section_it_was_filed_under(self):
        # The screen's three headings are the most useful thing to filter a
        # spreadsheet on, and they are not derivable from any single column.
        rows = json.loads(self._app().test_client().get(
            "/export.json").get_data())
        self.assertEqual(set(r["Reachable"] for r in rows),
                         {"In India", "Remote", "Needs a visa"})

    def test_the_sheet_is_laid_out_to_be_worked_in(self):
        sheet = self._sheet(self._app().test_client().get("/export.xlsx").data)
        self.assertEqual(sheet["A1"].value, "Reachable")
        self.assertTrue(sheet["A1"].font.bold)
        # Freeze and filter are the two things that make 400 rows usable.
        self.assertEqual(sheet.freeze_panes, "A2")
        self.assertTrue(sheet.auto_filter.ref.startswith("A1:O"))
        # A column of text digits will not sort or chart.
        self.assertIsInstance(sheet["B2"].value, int)

    def test_the_role_links_to_the_posting(self):
        sheet = self._sheet(self._app().test_client().get("/export.xlsx").data)
        self.assertTrue(sheet["C2"].hyperlink.target.startswith("https://"))

    def test_a_row_with_no_usable_link_is_not_hyperlinked(self):
        # Same allowlist the results table applies to the row click: a
        # javascript: URL must not become a clickable cell in a spreadsheet
        # either.
        rows = [dict(ROWS[0], apply_url="javascript:alert(1)")]
        sheet = self._sheet(self._app(rows).test_client().get(
            "/export.xlsx").data)
        self.assertIsNone(sheet["C2"].hyperlink)

    def test_a_scraped_title_cannot_become_a_formula(self):
        # Job titles come off web pages, and a spreadsheet executes any cell
        # that opens with = + - or @. This is the whole of CSV injection and
        # .xlsx is no safer.
        rows = [dict(ROWS[0], title="=HYPERLINK(\"http://x\",\"click\")")]
        app = self._app(rows)
        sheet = self._sheet(app.test_client().get("/export.xlsx").data)
        self.assertTrue(sheet["C2"].value.startswith("'="))
        body = app.test_client().get("/export.csv").get_data()
        self.assertIn("'=HYPERLINK", body.decode("utf-8-sig"))

    def test_the_csv_opens_in_excel_with_the_right_encoding(self):
        # Without the BOM Excel decodes a .csv as the local code page and
        # every accented company name arrives mojibake.
        body = self._app().test_client().get("/export.csv").get_data()
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))

    def test_the_sheet_records_which_filters_made_it(self):
        # Three exports taken while narrowing a filter are otherwise three
        # files nobody can tell apart.
        about = self._sheet(self._app().test_client().get(
            "/export.xlsx?min=5&q=gitlab").data, "About this export")
        pairs = {row[0].value: row[1].value for row in about.iter_rows()}
        self.assertEqual(pairs["Profile"], "kanav")
        self.assertEqual(pairs["Minimum score"], 5)
        self.assertEqual(pairs["Search text"], "gitlab")
        self.assertEqual(pairs["Listings"], 1)

    def test_a_missing_openpyxl_is_a_message_not_a_500(self):
        # A fresh clone that has not reinstalled. CSV and JSON still work,
        # so the screen says which one command fixes it.
        app = self._app()
        with mock.patch.dict(sys.modules, {"openpyxl": None}):
            r = app.test_client().get("/export.xlsx")
        self.assertEqual(r.status_code, 503)
        self.assertIn("pip install -r requirements.txt",
                      r.get_data(as_text=True))
        self.assertEqual(app.test_client().get("/export.csv").status_code, 200)

    def test_exporting_before_a_sweep_goes_back_to_the_start(self):
        app = app_module.create_app(state={})
        app.config.update(TESTING=True)
        r = app.test_client().get("/export.csv")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/", r.headers["Location"])

    def test_a_profile_name_cannot_break_the_download_header(self):
        # The profile reaches a response header here. A quote or a newline in
        # one would end the header early.
        app = self._app()
        app.state["profile"] = 'ka"nav\r\nX-Evil: 1'
        name = app.test_client().get("/export.csv").headers[
            "Content-Disposition"]
        self.assertEqual(name, 'attachment; filename="sweep-kanavX-Evil1-'
                         + datetime.date.today().isoformat() + '.csv"')

    def test_nothing_to_export_offers_no_button(self):
        page = self._app().test_client().get(
            "/results?min=999").get_data(as_text=True)
        self.assertNotIn("Export 0 listings", page)
        self.assertNotIn("/export.xlsx", page)


class TestRunningScreen(Isolated):
    def _app(self, done=(), alive=True, read_spend=None, now=None):
        proc = FakeProc()
        if not alive:
            proc.poll = lambda: 0
        state = {"profile": "kanav", "cap_usd": 8.41, "proc": proc,
                 "baseline_usd": 1.00,
                 "raw_plan": RUNNING_PLAN, "plan": {"total": 2.70,
                                                "total_searches": 46,
                                                "over_cap": False, "lines": []}}
        kwargs = {}
        if now is not None:
            kwargs["now"] = now
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RUNNING_PLAN,
            start_sweep=lambda profile: proc,
            read_spend=read_spend or (lambda: 2.42),
            read_done=lambda profile, day: set(done),
            output_dir=tempfile.mkdtemp(),
            **kwargs)
        app.config.update(TESTING=True)
        return app, state, proc

    def test_running_renders_one_tile_per_planned_search(self):
        app, _, _ = self._app()
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn("46", body)

    def test_the_header_meter_tracks_the_sweep_instead_of_freezing(self):
        # running.html overrode meter_label but not meter_scope or
        # meter_numeral_bind, so the header numeral sat at its first value
        # for the whole ~40-minute sweep while the body updated over SSE.
        body = self._app()[0].test_client().get("/running").get_data(as_text=True)
        self.assertIn("Spent so far", body)
        # Same Alpine scope the body already uses, and the numeral bound to
        # the same p.spend the body reads.
        self.assertIn("p.spend", body.split("<main>")[0])
        self.assertIn('x-text', body.split("<main>")[0])

    def test_the_meter_bar_shows_the_share_of_credit_used(self):
        # 2.42 read minus a 1.00 baseline is 1.42 of 8.41 credit = 17%.
        body = self._app()[0].test_client().get("/running").get_data(as_text=True)
        self.assertIn("width: 17%", body)

    def test_progress_reports_spend_as_a_delta_from_the_baseline(self):
        app, _, _ = self._app()
        payload = app.test_client().get("/progress").get_json()
        # 2.42 read now minus 1.00 baseline
        self.assertAlmostEqual(payload["spend"], 1.42, places=2)

    def test_progress_counts_finished_searches(self):
        app, state, _ = self._app()
        keys = app_module.planned_keys(state)
        app, state, _ = self._app(done=keys[:5])
        payload = app.test_client().get("/progress").get_json()
        self.assertEqual(payload["done"], 5)
        self.assertEqual(payload["planned"], 46)

    def test_a_dead_process_with_work_left_is_reported_as_interrupted(self):
        app, state, _ = self._app(done=[], alive=False)
        payload = app.test_client().get("/progress").get_json()
        self.assertTrue(payload["interrupted"])
        self.assertEqual(payload["outstanding"], 46)

    def test_a_dead_process_with_no_work_left_is_finished_not_interrupted(self):
        app, state, _ = self._app()
        keys = app_module.planned_keys(state)
        app, state, _ = self._app(done=keys, alive=False)
        payload = app.test_client().get("/progress").get_json()
        self.assertFalse(payload["interrupted"])
        self.assertTrue(payload["finished"])

    def test_the_event_stream_is_sse(self):
        app, _, _ = self._app()
        r = app.test_client().get("/events")
        self.assertTrue(r.headers["Content-Type"].startswith("text/event-stream"))

    def test_stopping_signals_the_process_and_keeps_what_was_fetched(self):
        app, state, proc = self._app()
        r = app.test_client().post("/stop")
        self.assertEqual(r.status_code, 302)
        self.assertIn(_signal.SIGINT, proc.signals)

    # -- Amendment A2: a None baseline must not be subtracted as if it were 0

    def test_an_unknown_baseline_is_not_subtracted_as_zero(self):
        app, state, _ = self._app()
        state["baseline_usd"] = None
        payload = app.test_client().get("/progress").get_json()
        self.assertAlmostEqual(payload["spend"], 2.42, places=2)
        self.assertFalse(payload["baseline_known"])

    def test_an_unavailable_spend_reading_is_null_not_zero(self):
        app, state, _ = self._app(read_spend=lambda: None)
        payload = app.test_client().get("/progress").get_json()
        self.assertIsNone(payload["spend"])
        self.assertFalse(payload["spend_known"])

    # -- Review round 1: /progress and /events reachable on empty state ----

    def test_progress_without_a_plan_is_409_not_a_500(self):
        # Reachable without going through /run — a bookmark, a stale tab
        # after a reset, curl during manual testing. snapshot() reaches
        # state["raw_plan"]/["profile"] by bracket access, so this must not
        # fall through to a KeyError-driven 500.
        app = app_module.create_app(state={})
        app.config.update(TESTING=True)
        r = app.test_client().get("/progress")
        self.assertEqual(r.status_code, 409)

    def test_events_without_a_plan_is_409_not_a_500(self):
        # Same guard as /progress. A plain error status, not the 302
        # /running redirects with — EventSource would follow a redirect
        # into HTML rather than treat it as a closed stream.
        app = app_module.create_app(state={})
        app.config.update(TESTING=True)
        r = app.test_client().get("/events")
        self.assertEqual(r.status_code, 409)

    # -- Review round 1: A3's throttle has to actually skip a re-read -----

    def test_spend_is_read_once_across_two_requests_within_the_poll_window(self):
        calls = []

        def read_spend():
            calls.append(1)
            return 2.42

        clock = [100.0]
        app, _, _ = self._app(read_spend=read_spend, now=lambda: clock[0])
        client = app.test_client()
        client.get("/progress")
        client.get("/progress")
        self.assertEqual(len(calls), 1)

    def test_spend_is_reread_once_the_poll_window_elapses(self):
        calls = []

        def read_spend():
            calls.append(1)
            return 2.42

        clock = [100.0]
        app, _, _ = self._app(read_spend=read_spend, now=lambda: clock[0])
        client = app.test_client()
        client.get("/progress")
        clock[0] += app_module.SPEND_POLL_SECONDS + 1
        client.get("/progress")
        self.assertEqual(len(calls), 2)

    # -- Review round 1: the amber/teal tile mapping needs direct coverage -

    def test_each_cell_says_which_location_it_is_searching(self):
        # The grid was colour alone: you could not tell what a box was for
        # without hovering it.
        app, _, _ = self._app()
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn('x-text="t.short"', body)
        # Grouped by site, so the site is stated once per row instead of
        # squeezed into every 44px cell.
        self.assertIn('x-for="site in p.sites"', body)
        self.assertIn('x-text="site"', body)
        # And the hover still carries the keyword, which is the part that
        # does NOT change between neighbouring cells.
        self.assertIn(':title="t.label"', body)

    def test_a_finished_cell_is_readable(self):
        # A done cell is filled amber or teal; chalk text on either is
        # unreadable, which is why the state is a class and not an inline
        # background.
        app, _, _ = self._app()
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertRegex(body, r":class=\"t\.state === 'done'")
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        done = re.search(r"\.cell\.done \{([^}]*)\}", css)
        self.assertIsNotNone(done)
        self.assertIn("color: var(--ground)", done.group(1))

    def test_tile_free_flag_matches_site_rates(self):
        plan = {
            "profile": "kanav",
            "sites": {
                "linkedin": [{"keywords": "kw0", "location": "India",
                              "company": ""}],
                # Not in config.SITE_RATES, so it's the free/teal case.
                "remoteok": [{"keywords": "kw0", "location": "Remote",
                             "company": ""}],
            },
            "free_sources": 0,
        }
        state = {"profile": "kanav", "cap_usd": 8.41, "proc": FakeProc(),
                 "baseline_usd": 1.00, "raw_plan": plan,
                 "plan": {"total": 0.045, "total_searches": 2,
                          "over_cap": False, "lines": []}}
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: plan,
            start_sweep=lambda profile: state["proc"],
            read_spend=lambda: 2.42,
            read_done=lambda profile, day: set(),
            output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        payload = app.test_client().get("/progress").get_json()
        tiles_by_site = {t["site"]: t for t in payload["tiles"]}
        self.assertFalse(tiles_by_site["linkedin"]["free"])
        self.assertTrue(tiles_by_site["remoteok"]["free"])


# Column names are the REAL ones from output/<profile>/jobs_*.csv, verified
# against output/global_all/jobs_combined.csv. Lowercase snake_case, and the
# remote flag is literally "remote?" including the question mark.
# Six real rows, copied verbatim out of output/global_all/jobs_combined.csv
# (a paid 1607-row sweep), at the CSV line numbers named below. Every column
# is kept, because the classification reads remote_scope / remote_regions /
# location and the previous hand-written fixture omitted remote_scope
# entirely — and used "visa": "needs sponsorship", a value the pipeline
# cannot emit (enrich.visa returns only "", "no" or "yes"). That fixture is
# why review passed a split that mis-filed 1307 of those 1607 rows.
#
# Four of the six were classified WRONGLY by the rule this replaces, so the
# fixture can fail: lines 102, 25, 476 and 890 below.
ROWS = [
    # line 25: bucket() -> 'abroad'. Remote but geo-locked OUTSIDE India, so
    # unreachable from here. The old rule read remote? == "True" and called it
    # "Genuinely remote from anywhere" — 170 rows on the real file.
    {
        'score': '47',
        'matched_skills': 'node, node.js, express, mongodb, redis, websockets, jwt, oauth, restful, mysql, ci/cd, agile',
        'is_fullstack': 'True', 'title': 'Node.JS Developer',
        'company': 'Inetum', 'location': 'Morocco, Remote', 'remote?': 'True',
        'remote_scope': 'restricted', 'hires_home': '', 'tz_gap': '4.5',
        'remote_regions': '', 'visa': '', 'eor': '', 'timezones': 'UTC+1',
        'experience_required': 'Full Time', 'salary': '', 'hr_email': '',
        'hr_phone': '', 'source_site': 'himalayas',
        'apply_url': 'https://himalayas.app/companies/inetum/jobs/node-js-developer-8086996543',
        'date_posted': '2026-08-25', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
    # line 74: bucket() -> 'remote'. Genuinely location-independent.
    {
        'score': '39',
        'matched_skills': 'node, node.js, express, react, react.js, typescript, mongodb, redis, restful, mysql, ci/cd, html, css',
        'is_fullstack': 'True',
        'title': 'Senior Full Stack Developer - PHP Laravel',
        'company': 'Newrich Network', 'location': 'Anywhere in the World',
        'remote?': 'True', 'remote_scope': 'worldwide', 'hires_home': '',
        'tz_gap': '', 'remote_regions': '', 'visa': '', 'eor': '',
        'timezones': '', 'experience_required': '', 'salary': '', 'hr_email': '',
        'hr_phone': '', 'source_site': 'wwr',
        'apply_url': 'https://weworkremotely.com/remote-jobs/newrich-network-senior-full-stack-developer-php-laravel',
        'date_posted': '2026-08-19', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
    # line 102: bucket() -> 'abroad'. Onsite in Taipei. The old rule saw an
    # empty visa and remote? == "False" and filed it under "You can work here
    # now" — 1046 rows on the real file.
    {
        'score': '36',
        'matched_skills': 'node, node.js, react, typescript, javascript, redux, jest, html, css, agile',
        'is_fullstack': 'True', 'title': 'Frontend Developer(UniFi Connect)',
        'company': 'Ubiquiti', 'location': 'Taipei', 'remote?': 'False',
        'remote_scope': '', 'hires_home': 'no', 'tz_gap': '',
        'remote_regions': '', 'visa': '', 'eor': '', 'timezones': '',
        'experience_required': '', 'salary': '', 'hr_email': '', 'hr_phone': '',
        'source_site': 'greenhouse:ubiquiti',
        'apply_url': 'https://job-boards.greenhouse.io/ubiquiti/jobs/4193274009',
        'date_posted': '2026-08-14', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
    # line 413: bucket() -> 'india'. Onsite in Bengaluru.
    {
        'score': '22',
        'matched_skills': 'node, node.js, react, react.js, ci/cd, agile, llm, llms, langchain, agentic, ai agent, ai agents, prompt engineering, python',
        'is_fullstack': 'True', 'title': 'Full Stack Developer (AI Agents)',
        'company': 'Databricks', 'location': 'Bengaluru, India',
        'remote?': 'False', 'remote_scope': '', 'hires_home': 'yes',
        'tz_gap': '', 'remote_regions': '', 'visa': '', 'eor': '',
        'timezones': '', 'experience_required': '3+', 'salary': '',
        'hr_email': '', 'hr_phone': '', 'source_site': 'greenhouse:databricks',
        'apply_url': 'https://databricks.com/company/careers/open-positions/job?gh_jid=8632126002',
        'date_posted': '2026-09-01', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
    # line 476: bucket() -> 'abroad'. visa == "no" is an explicit REFUSAL to
    # sponsor (enrich.visa; scraper.py:1179 prints "refuse visa sponsorship").
    # The old rule filed any non-empty visa under "Needs visa sponsorship" —
    # the inverse of the label, 91 rows on the real file.
    {
        'score': '20',
        'matched_skills': 'typescript, restful, ci/cd, agile, llm, agentic, ai agent, prompt engineering, openai, mcp, python',
        'is_fullstack': 'True',
        'title': 'Software Engineer, Enterprise Integrations',
        'company': 'Cloudflare', 'location': 'Hybrid', 'remote?': 'False',
        'remote_scope': 'hybrid', 'hires_home': 'yes', 'tz_gap': '11.5',
        'remote_regions': 'US', 'visa': 'no', 'eor': 'Multiplier',
        'timezones': '', 'experience_required': '3+', 'salary': '',
        'hr_email': 'hr@cloudflare.com', 'hr_phone': '',
        'source_site': 'greenhouse:cloudflare',
        'apply_url': 'https://boards.greenhouse.io/cloudflare/jobs/8155495?gh_jid=8155495',
        'date_posted': '2026-08-25', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
    # line 890: bucket() -> 'india'. Remote, geo-locked to a list that
    # INCLUDES India, so it is reachable from here. The old rule filed it as
    # "Genuinely remote from anywhere", which it is not.
    {
        'score': '9',
        'matched_skills': 'agile, agentic, python, artificial intelligence',
        'is_fullstack': 'True',
        'title': 'Senior Backend Engineer (Ruby on Rails), Plan: Planning Views',
        'company': 'GitLab', 'location': 'Bangalore, India', 'remote?': 'True',
        'remote_scope': 'restricted', 'hires_home': 'yes', 'tz_gap': '0.0',
        'remote_regions': 'Australia, India, Europe', 'visa': '',
        'eor': 'Multiplier', 'timezones': '', 'experience_required': '',
        'salary': '', 'hr_email': '', 'hr_phone': '',
        'source_site': 'greenhouse:gitlab',
        'apply_url': 'https://job-boards.greenhouse.io/gitlab/jobs/8695815002',
        'date_posted': '2026-08-25', 'req_number': '', 'grade': '',
        'verified_live': ''
    },
]


class TestArrivalsFeed(Isolated):
    """A paid sweep takes about 40 minutes and, between searches, nothing on
    the running screen moves — which reads as broken. The feed shows the
    listings the engine has actually checkpointed.

    These exercise the REAL reader, not a stub: the globbing, the mtime floor
    and the diff between two readings are where the defects live.
    """

    COLUMNS = "score,title,company,source_site,req_number,apply_url"

    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out)
        self.dir = os.path.join(self.out, "kanav")
        os.makedirs(self.dir)
        self.clock = [1_000_000.0]

    def write(self, name, rows, mtime=None):
        """One of the engine's output files. It rewrites this whole file after
        every search, which is why the feed diffs readings instead of tailing.
        """
        path = os.path.join(self.dir, name)
        lines = [self.COLUMNS]
        for row in rows:
            lines.append(",".join(str(c) for c in row))
        pathlib.Path(path).write_text("\n".join(lines) + "\n")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def job(self, n, score=70, site="linkedin"):
        return (score, f"Engineer {n}", f"Company {n}", site, "", "")

    def _app(self, state=None, **kw):
        state = state if state is not None else {
            "profile": "kanav", "cap_usd": 8.41, "proc": FakeProc(),
            "baseline_usd": 1.0, "raw_plan": RUNNING_PLAN,
            "plan": {"total": 2.70, "total_searches": 46, "over_cap": False,
                     "lines": []},
            "run_started_at": self.clock[0]}
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RUNNING_PLAN,
            read_spend=lambda: 2.42, read_done=lambda profile, day: set(),
            output_dir=self.out, wall_now=lambda: self.clock[0], **kw)
        app.config.update(TESTING=True)
        return app

    def tick(self, seconds=1.0):
        self.clock[0] += seconds

    def feed(self, app):
        body = app.test_client().get("/running").get_data(as_text=True)
        return json.loads(alpine_scope(body)[0]
                          .split("p: ", 1)[1].rsplit(", cap:", 1)[0])

    # ---- what counts as this sweep's output -----------------------------
    def test_the_previous_sweeps_file_does_not_open_the_feed(self):
        # At the moment a sweep launches, the newest stamped file is still the
        # LAST sweep's. Without the mtime floor the feed opens by presenting
        # last month's listings as things that just arrived.
        self.write("jobs_2026-08-01_1200.csv", [self.job(i) for i in range(5)],
                   mtime=self.clock[0] - 3600)
        p = self.feed(self._app())
        self.assertEqual(p["found"], 0)
        self.assertEqual(p["latest"], [])
        self.assertIsNone(p["since"])

    def test_the_merged_file_is_never_the_live_one(self):
        # jobs_combined.csv is merge_jobs.py's output and spans every earlier
        # sweep. read_rows PREFERS it, which is right for /results and wrong
        # here.
        self.write("jobs_combined.csv", [self.job(i) for i in range(9)])
        p = self.feed(self._app())
        self.assertEqual(p["found"], 0)

    def test_rows_from_this_sweep_arrive(self):
        self.write("jobs_2026-09-09_1800.csv", [self.job(1), self.job(2)])
        p = self.feed(self._app())
        self.assertEqual(p["found"], 2)
        self.assertEqual([j["title"] for j in p["latest"]],
                         ["Engineer 1", "Engineer 2"])
        self.assertEqual(p["latest"][0]["company"], "Company 1")
        self.assertEqual(p["latest"][0]["site"], "linkedin")
        self.assertEqual(p["latest"][0]["score"], 70)

    # ---- the diff between two readings ----------------------------------
    def test_the_same_file_read_twice_reports_nothing_new(self):
        app = self._app()
        self.write("jobs_2026-09-09_1800.csv", [self.job(1)])
        self.assertEqual(len(self.feed(app)["latest"]), 1)
        self.tick(30)
        again = self.feed(app)
        # Still on screen — it is a feed, not a queue — but not re-reported,
        # so "last one 30s ago" stays true.
        self.assertEqual(len(again["latest"]), 1)
        self.assertEqual(again["since"], 30)

    def test_a_new_batch_goes_on_top(self):
        app = self._app()
        self.write("jobs_2026-09-09_1800.csv", [self.job(1)])
        self.feed(app)
        self.tick(60)
        # The engine rewrites the file whole and re-ranks it, so the new row
        # can land anywhere in it — here, first.
        self.write("jobs_2026-09-09_1800.csv", [self.job(2), self.job(1)])
        p = self.feed(app)
        self.assertEqual([j["title"] for j in p["latest"]],
                         ["Engineer 2", "Engineer 1"])
        self.assertEqual(p["found"], 2)
        self.assertEqual(p["since"], 0)

    def test_the_feed_is_capped(self):
        app = self._app()
        self.write("jobs_2026-09-09_1800.csv",
                   [self.job(i) for i in range(40)])
        p = self.feed(app)
        # Every row counted, a sample shown: this payload ships on every SSE
        # tick for forty minutes.
        self.assertEqual(p["found"], 40)
        self.assertEqual(len(p["latest"]), app_module.FEED_LEN)

    def test_a_torn_read_does_not_walk_the_count_backwards(self):
        # A reading can land while the engine is rewriting the file. During a
        # sweep it only ever grows, so a shorter one is a torn read — and a
        # count that drops is exactly the "is this broken?" signal this whole
        # panel exists to remove.
        app = self._app()
        self.write("jobs_2026-09-09_1800.csv",
                   [self.job(i) for i in range(10)])
        self.assertEqual(self.feed(app)["found"], 10)
        self.write("jobs_2026-09-09_1800.csv", [self.job(0), self.job(1)])
        self.assertEqual(self.feed(app)["found"], 10)

    def test_a_row_with_no_identity_is_counted_but_not_shown(self):
        # job_key() returns None for a row with no req number, no
        # company+title and no URL. Shown, it would arrive again on every
        # single tick, because nothing can tell it from itself.
        app = self._app()
        self.write("jobs_2026-09-09_1800.csv",
                   [self.job(1), (50, "", "", "indeed", "", "")])
        p = self.feed(app)
        self.assertEqual(p["found"], 2)
        self.assertEqual([j["title"] for j in p["latest"]], ["Engineer 1"])

    # ---- the honest counterpart -----------------------------------------
    def test_a_stall_is_stated_not_hidden(self):
        # A moving list is otherwise just a nicer way to look busy while
        # nothing is happening.
        app = self._app()
        self.write("jobs_2026-09-09_1800.csv", [self.job(1)])
        self.feed(app)
        self.tick(420)
        self.assertEqual(self.feed(app)["since"], 420)

    def test_the_clock_runs_from_the_launch(self):
        app = self._app()
        self.tick(95)
        self.assertEqual(self.feed(app)["elapsed"], 95)

    def test_no_clock_rather_than_a_wrong_one(self):
        # run_started_at is absent after a server restart. A clock reading 0s
        # beside a sweep that is minutes old is worse than no clock.
        state = {"profile": "kanav", "cap_usd": 8.41, "proc": FakeProc(),
                 "baseline_usd": 1.0, "raw_plan": RUNNING_PLAN,
                 "plan": {"total": 2.70, "total_searches": 46,
                          "over_cap": False, "lines": []}}
        p = self.feed(self._app(state=state))
        self.assertIsNone(p["elapsed"])

    def test_the_screen_says_nothing_has_landed_yet(self):
        body = self._app().test_client().get("/running").get_data(as_text=True)
        self.assertIn("Nothing yet", body)
        # Server-correct as well as live: with no rows the empty state must
        # not be the thing that is hidden.
        self.assertNotIn('<p class="empty" x-show="!p.latest.length"\n'
                         '          style="display:none"', body)

    def test_the_launch_records_when_it_started(self):
        # Both the clock and the feed's mtime floor hang off this one value:
        # without it the previous sweep's output file opens the feed.
        state = {"profile": "kanav", "cap_usd": 8.41, "derived": DERIVED,
                 "raw_plan": RUNNING_PLAN,
                 "plan": {"total": 2.70, "total_searches": 46,
                          "over_cap": False, "lines": [], "spend_cap": 3.38}}
        app = self._app(state=state,
                        start_sweep=lambda profile: FakeProc(),
                        hour_now=lambda: 9)
        app.write_profile = lambda n, src: None
        r = app.test_client().post("/run")
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:300])
        self.assertEqual(state["run_started_at"], self.clock[0])

    def test_the_screen_carries_a_clock_that_ticks_between_frames(self):
        # The SSE stream lands every two seconds and, between searches,
        # nothing in it changes for up to a minute. A seconds counter is the
        # one thing on screen that always moves — which is the difference
        # between "working" and "broken" to someone watching it.
        body = self._app().test_client().get("/running").get_data(as_text=True)
        self.assertIn("hms(p.elapsed + drift)", body)
        self.assertIn("setInterval", body)
        # Anchored to the server on every frame, so it cannot drift away
        # from the figure the sweep actually reports.
        self.assertIn("drift = 0", body)

    # ---- the free path ---------------------------------------------------
    def test_a_free_sweep_gets_no_feed_and_never_reads_the_file(self):
        # fetch_free() returns everything in one pass at the end, so a feed
        # would sit empty for the whole run and then flash the lot. The
        # indeterminate bar is the honest signal there.
        def no_read(profile, since):
            raise AssertionError("the free path has nothing to feed from")
        app = self._app(state={
            "profile": "kanav", "free_only": True, "proc": FakeProc(),
            "raw_plan": {"profile": "kanav", "sites": {}, "max_results": {},
                          "free_sources": 39},
            "plan": {"total": 0.0, "total_searches": 0, "over_cap": False,
                     "lines": [], "free_sources": 39},
            "run_started_at": self.clock[0]}, read_live=no_read)
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertNotIn("Listings arriving", body)
        self.assertIn("working-bar", body)

    def test_the_clock_is_shown_on_the_free_path_too(self):
        app = self._app(state={
            "profile": "kanav", "free_only": True, "proc": FakeProc(),
            "raw_plan": {"profile": "kanav", "sites": {}, "max_results": {},
                          "free_sources": 39},
            "plan": {"total": 0.0, "total_searches": 0, "over_cap": False,
                     "lines": [], "free_sources": 39},
            "run_started_at": self.clock[0]})
        self.tick(140)
        p = self.feed(app)
        self.assertEqual(p["elapsed"], 140)
        self.assertEqual(p["latest"], [])


class TestFinishedSweepNavigation(Isolated):
    """After forty minutes of waiting, the last thing the screen should ask
    for is another click. It takes you to the results itself — but only from
    the two states where that is what you want."""

    def _body(self, done=(), alive=True, interrupted=False):
        app, _, _ = TestRunningScreen()._app(done=done, alive=alive)
        return app.test_client().get("/running").get_data(as_text=True)

    def test_a_finished_sweep_opens_the_results_itself(self):
        body = self._body(alive=True)
        self.assertIn("location = '/results'", body)
        # Gated on BOTH: on finished, and on this page having been opened
        # while the sweep was still going.
        self.assertRegex(body, r"if \(p\.finished &amp;&amp; waiting\)")

    def test_it_does_not_fire_for_a_sweep_that_stopped_early(self):
        # p.interrupted is the "ran out of credit" state, and its panel — the
        # one offering another key — is on THIS screen. Whisking someone away
        # from it is how a stopped sweep looks like a finished one.
        body = self._body()
        self.assertNotIn("p.interrupted) location", body)
        self.assertIn("if (p.finished || p.interrupted) es.close()", body)

    def test_a_page_opened_after_the_fact_stays_put(self):
        # The tracker still offers Running once a sweep has launched, so
        # coming back to look at it must not bounce straight out again.
        # `waiting` is decided server-side, from the state at render.
        RUNNING = TestRunningScreen()
        app, _, proc = RUNNING._app(alive=False)
        body = app.test_client().get("/running").get_data(as_text=True)
        self.assertIn("waiting: false", body)
        live = self._body(alive=True)
        self.assertIn("waiting: true", live)

    def test_the_link_stays_for_a_browser_that_cannot_navigate_itself(self):
        # No JavaScript, and the after-the-fact visit above: both need a way
        # to the results that is not a script.
        body = self._body()
        self.assertIn('href="/results"', body)
        self.assertIn("See the results", body)

    def test_the_screen_only_promises_the_jump_when_it_will_happen(self):
        # "Opening them now" beside a page that is not going to open
        # anything is the same class of lie as a fabricated figure.
        body = self._body()
        self.assertRegex(body, r'x-show="waiting"[^>]*>Opening the results')


class TestResultsScreen(Isolated):
    def _app(self, rows=None, start_rescore=None):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "spend": 2.70,
                   "plan": {"total": 2.70, "total_searches": 46, "lines": []}},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            read_rows=lambda profile: ROWS if rows is None else rows,
            start_rescore=start_rescore, output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        return app

    def test_rows_are_grouped_into_the_three_reachability_buckets(self):
        body = self._app().test_client().get("/results").get_data(as_text=True)
        self.assertIn("Onsite and hybrid in India", body)
        self.assertIn("Fully remote", body)
        self.assertIn("Onsite abroad", body)

    def test_each_row_lands_in_exactly_one_bucket(self):
        buckets = app_module.bucket_rows(ROWS)
        # Counted from the real rows, not from the split's own opinion: the
        # Taipei and Morocco rows are abroad, the Cloudflare refusal is
        # abroad too, and the India-locked GitLab remote row is reachable.
        self.assertEqual([r["company"] for r in buckets["india"]],
                         ["Databricks", "GitLab"])
        self.assertEqual([r["company"] for r in buckets["remote"]],
                         ["Newrich Network"])
        self.assertEqual([r["company"] for r in buckets["abroad"]],
                         ["Inetum", "Ubiquiti", "Cloudflare"])
        self.assertEqual(sum(len(v) for v in buckets.values()), len(ROWS))

    def test_the_split_is_the_ported_one_not_a_fourth_rule(self):
        # The spec called this screen a port of linkedin_shortlist.bucket().
        # If this screen ever grows its own classification again, this fails.
        import linkedin_shortlist
        buckets = app_module.bucket_rows(ROWS)
        for key, rows in buckets.items():
            for row in rows:
                self.assertEqual(linkedin_shortlist.bucket(row), key,
                                 row["company"])

    def test_an_explicit_sponsorship_refusal_is_not_labelled_as_needing_one(self):
        # enrich.visa returns "no" when the posting REFUSES to sponsor, "" in
        # the common case where it never mentions it, "yes" when it offers.
        # The old rule read any non-empty value as "needs sponsorship".
        refusal = [r for r in ROWS if r["visa"] == "no"]
        self.assertEqual(len(refusal), 1)
        self.assertEqual(app_module.bucket_rows(refusal),
                         {"india": [], "remote": [], "abroad": refusal})

    def test_filtering_by_score_is_free_and_says_so(self):
        body = self._app().test_client().get("/results?min=39").get_data(as_text=True)
        self.assertIn("Filtering and re-ranking these is free", body)
        self.assertIn("Node.JS Developer", body)          # score 47
        self.assertNotIn("Full Stack Developer (AI Agents)", body)   # score 22

    def test_an_empty_result_names_the_filter_that_removed_the_most(self):
        # Asserted as the rendered SENTENCE with its numbers. "score" alone is
        # satisfied by the "Minimum score" input label and "0 listings" by a
        # static heading, so neither reached the feature: replacing
        # worst_filter's body with `return None` left the suite green.
        body = self._app().test_client().get(
            "/results?min=999&source=wwr").get_data(as_text=True)
        flat = " ".join(body.split())
        self.assertIn("The minimum score filter removed the most — 6 of 6. "
                      "Loosen it to see more.", flat)
        # The clear-link must drop only the blamed filter and carry the rest.
        self.assertIn('href="/results?source=wwr"', body)

    def test_the_clear_link_drops_the_blamed_filter_and_keeps_the_others(self):
        # min=1 removes nothing (every fixture score is >= 9), so the source
        # filter is the culprit and the link has to preserve min.
        body = self._app().test_client().get(
            "/results?min=1&source=nosuchboard").get_data(as_text=True)
        flat = " ".join(body.split())
        self.assertIn("The source filter removed the most — 6 of 6. "
                      "Loosen it to see more.", flat)
        self.assertIn('href="/results?min=1"', body)

    def test_the_text_filter_can_be_the_one_blamed(self):
        body = self._app().test_client().get(
            "/results?min=1&q=nothingmatchesthis").get_data(as_text=True)
        flat = " ".join(body.split())
        self.assertIn("The search text filter removed the most — 6 of 6. "
                      "Loosen it to see more.", flat)
        self.assertIn('href="/results?min=1"', body)

    def test_the_empty_result_diagnosis_is_not_computed_when_rows_survive(self):
        # Three passes over up to 1607 rows, consumed only when total == 0.
        calls = []
        with mock.patch.object(app_module, "worst_filter",
                               lambda *a: calls.append(a)):
            body = self._app().test_client().get("/results").get_data(as_text=True)
        self.assertIn("Onsite abroad", body)
        self.assertEqual(calls, [])

    def test_the_results_meter_never_shows_the_estimate_as_money_spent(self):
        # /configure wrote app.state["spend"] = estimate["total"] and
        # /results never overwrote it, so the meter read the ESTIMATE under
        # the label "Spent" even when nothing had been spent at all.
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41, "derived": DERIVED},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            read_rows=lambda profile: ROWS, output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        app.write_profile = lambda n, s: None
        client = app.test_client()
        configure = client.get("/configure").get_data(as_text=True)
        self.assertIn("$2.70", configure)             # the estimate, correctly
        header = client.get("/results").get_data(as_text=True).split("<main>")[0]
        self.assertNotIn("2.70", header)
        self.assertIn("not known", header)

    def test_the_results_meter_shows_the_real_delta_once_a_run_recorded_one(self):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41,
                   "baseline_usd": 1.00, "spend_read_val": 2.42},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            read_rows=lambda profile: ROWS, output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        header = app.test_client().get("/results").get_data(as_text=True).split("<main>")[0]
        self.assertIn("$1.42", header)

    def test_results_with_no_sweep_yet_goes_back_to_upload(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        self.assertEqual(app.test_client().get("/results").status_code, 302)

    def test_rescoring_runs_the_free_rescore_for_this_profile(self):
        calls = []
        app = self._app(start_rescore=lambda profile, hours: calls.append((profile, hours)))
        r = app.test_client().post("/rescore", data={"hours": "6"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(calls, [("kanav", 6)])

    def test_an_out_of_range_hours_value_is_rejected_and_nothing_runs(self):
        calls = []
        app = self._app(start_rescore=lambda profile, hours: calls.append((profile, hours)))
        r = app.test_client().post("/rescore", data={"hours": "9999"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(calls, [])

    def test_a_site_listed_at_zero_is_free_not_paid(self):
        app = self._app(rows=[dict(ROWS[0], source_site="freebie")])
        with mock.patch.dict(app_module.config.SITE_RATES, {"freebie": 0.0}):
            body = app.test_client().get("/results").get_data(as_text=True)
        self.assertIn('class="free"', body)

    def test_the_source_filter_keeps_only_that_source(self):
        body = self._app().test_client().get(
            "/results?source=wwr").get_data(as_text=True)
        self.assertIn("Senior Full Stack Developer - PHP Laravel", body)
        self.assertNotIn("Node.JS Developer", body)

    def test_the_text_filter_matches_title_and_company(self):
        client = self._app().test_client()
        by_company = client.get("/results?q=databricks").get_data(as_text=True)
        self.assertIn("Full Stack Developer (AI Agents)", by_company)
        self.assertNotIn("Node.JS Developer", by_company)
        by_title = client.get("/results?q=node.js+developer").get_data(as_text=True)
        self.assertIn("Node.JS Developer", by_title)
        self.assertNotIn("Full Stack Developer (AI Agents)", by_title)

    def test_active_filters_survive_in_the_rendered_form(self):
        body = self._app().test_client().get(
            "/results?min=40&source=wwr&q=newrich").get_data(as_text=True)
        self.assertIn('name="min" value="40"', body)
        self.assertIn('value="wwr" selected', body)
        self.assertIn('name="q" value="newrich"', body)

    def test_rows_are_sorted_by_score_within_a_bucket(self):
        # Both rows must land in the SAME bucket, or SECTIONS' own order
        # decides the comparison and the test passes with no sort at all.
        low = dict(ROWS[0], score="10", title="Lower scoring role")
        high = dict(ROWS[0], score="99", title="Higher scoring role")
        app = self._app(rows=[low, high])          # worst first on input
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertLess(body.index("Higher scoring role"),
                        body.index("Lower scoring role"))

    def test_a_running_rescore_says_so_on_the_page_it_redirects_to(self):
        # The signal has to survive the 302: it was previously nested inside
        # the re-rank <details>, which comes back collapsed after navigation.
        class Running:
            def poll(self):
                return None

        app = self._app(start_rescore=lambda profile, hours: Running())
        client = app.test_client()
        client.post("/rescore", data={"hours": "6"})
        body = client.get("/results").get_data(as_text=True)
        self.assertIn("Re-ranking now", body)
        self.assertLess(body.index("Re-ranking now"), body.index("<details"))

    def test_a_busy_rescore_message_is_not_styled_as_an_error(self):
        class Running:
            def poll(self):
                return None

        app = self._app(start_rescore=lambda profile, hours: Running())
        client = app.test_client()
        client.post("/rescore", data={"hours": "6"})
        body = client.post("/rescore", data={"hours": "6"}).get_data(as_text=True)
        self.assertIn("already running", body)
        # class="error" is painted the red reserved for over-cap.
        self.assertNotIn('class="error"', body)
        # The notice IS the response to this click, so the standing banner
        # must not restate the same fact in a second treatment beside it.
        self.assertEqual(body.count("Reload in a moment"), 1)

    def test_an_uppercase_scheme_keeps_its_apply_link(self):
        app = self._app(rows=[dict(ROWS[0], apply_url="HTTPS://Board.example/job")])
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertIn("HTTPS://Board.example/job", body)
        self.assertNotIn("No link", body)

    def test_a_filter_that_removed_nothing_is_not_blamed(self):
        # No shortlist on disk yet: every branch counts 0 removals, and the
        # page must not claim a filter removed "0 of 0".
        app = self._app(rows=[])
        body = app.test_client().get("/results?min=90").get_data(as_text=True)
        self.assertIn("Nothing was found for this profile yet", body)
        self.assertNotIn("removed the most", body)

    def test_a_second_rescore_is_refused_while_one_is_still_running(self):
        calls = []

        class Running:
            def poll(self):
                return None

        app = self._app(start_rescore=lambda profile, hours: (
            calls.append((profile, hours)) or Running()))
        client = app.test_client()
        self.assertEqual(client.post("/rescore", data={"hours": "6"}).status_code, 302)
        second = client.post("/rescore", data={"hours": "6"})
        self.assertEqual(second.status_code, 409)
        self.assertIn("already running", second.get_data(as_text=True))
        # rescore_from_apify.py truncates jobs_combined.csv in place, so the
        # second child must never have been started.
        self.assertEqual(calls, [("kanav", 6)])

    def test_two_simultaneous_rescore_posts_launch_one_child(self):
        # _rescore_lock had the same gap _run_lock did: sequential posts
        # behave correctly with the lock deleted, and only concurrency sees
        # it. Two children both rewrite jobs_combined.csv in place, on data
        # the user already paid for.
        import threading

        calls = []
        at_the_door = threading.Barrier(2)

        class Running:
            def poll(self):
                return None

        def slow_start(profile, hours):
            time.sleep(0.1)             # widen the check-to-set window
            calls.append((profile, hours))
            return Running()

        app = self._app(start_rescore=slow_start)
        codes, errors = [], []

        def post():
            try:
                at_the_door.wait(timeout=5)
                codes.append(app.test_client().post(
                    "/rescore", data={"hours": "6"}).status_code)
            except Exception as exc:
                errors.append(repr(exc))

        threads = [threading.Thread(target=post) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(sorted(codes), [302, 409])
        self.assertEqual(len(calls), 1)

    def test_a_finished_rescore_does_not_block_the_next_one(self):
        calls = []

        class Finished:
            def poll(self):
                return 0

        app = self._app(start_rescore=lambda profile, hours: (
            calls.append((profile, hours)) or Finished()))
        client = app.test_client()
        client.post("/rescore", data={"hours": "6"})
        self.assertEqual(client.post("/rescore", data={"hours": "12"}).status_code, 302)
        self.assertEqual(calls, [("kanav", 6), ("kanav", 12)])

    def test_a_non_http_apply_url_is_not_rendered_as_a_link(self):
        app = self._app(rows=[dict(ROWS[0], apply_url="javascript:alert(1)")])
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertNotIn("javascript:alert(1)", body)
        self.assertIn("No link", body)

    # ---- the whole row opens the listing --------------------------------
    def test_the_row_carries_the_apply_link(self):
        app = self._app(rows=[dict(ROWS[0], apply_url="https://board.example/job")])
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertIn('<tr class="rowlink" data-href="https://board.example/job">',
                      body)
        # And the anchor stays: it is the keyboard and screen-reader path, and
        # the row is a mouse convenience on top of it. Matched on target
        # rather than on the tag opening, which the row's own data-href does
        # not carry — so this cannot pass on the <tr> alone.
        self.assertIn('href="https://board.example/job" target="_blank"', body)

    def test_an_unsafe_url_makes_the_row_unclickable_too(self):
        # data-href is a second place the scraped URL reaches the page. It is
        # set from the SAME allowlist expression as the anchor, so a scheme
        # the anchor refuses cannot arrive through the row instead.
        app = self._app(rows=[dict(ROWS[0], apply_url="javascript:alert(1)")])
        body = app.test_client().get("/results").get_data(as_text=True)
        markup = body[:body.index("<script>", body.index("<main>"))]
        self.assertNotIn("data-href", markup)
        self.assertNotIn("rowlink", markup)

    def test_a_row_with_no_link_is_not_clickable(self):
        app = self._app(rows=[dict(ROWS[0], apply_url="")])
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertNotIn("rowlink", body)
        self.assertIn("No link", body)

    def test_the_row_handler_defers_to_the_controls_inside_it(self):
        # Clicking Apply must open one tab, not two, and selecting a company
        # name to copy it must not navigate.
        body = self._app().test_client().get("/results").get_data(as_text=True)
        script = body[body.index("<script>", body.index("<main>")):]
        self.assertIn('closest("a, button, input, label, summary")', script)
        self.assertIn("getSelection", script)
        self.assertIn('"_blank", "noopener"', script)

    # ---- rows you have already opened -----------------------------------
    def test_an_opened_row_is_remembered_in_this_browser(self):
        body = self._app().test_client().get("/results").get_data(as_text=True)
        script = body[body.index("<script>", body.index("<main>")):]
        self.assertIn('"sweep.opened"', script)
        # Marked on BOTH ways of opening a listing: the row, and the anchor
        # that the row handler deliberately steps aside for.
        self.assertEqual(script.count("markOpened(row);"), 2)

    def test_a_click_that_only_ends_a_selection_is_not_an_open(self):
        # It does not navigate, so it must not grey the row out either —
        # otherwise copying a company name marks it as read.
        body = self._app().test_client().get("/results").get_data(as_text=True)
        script = body[body.index("<script>", body.index("<main>")):]
        self.assertLess(script.index("getSelection"),
                        script.index("markOpened(row);"),
                        "the selection check has to come first")

    def test_the_record_is_a_set_and_is_bounded(self):
        # Two rows can carry the same apply URL, and a row re-clicked after
        # the cap dropped it would append again — either way the list grows
        # without recording anything new.
        body = self._app().test_client().get("/results").get_data(as_text=True)
        self.assertIn("if (seen.indexOf(row.dataset.href) !== -1) return;", body)
        self.assertIn("seen.slice(-OPENED_CAP)", body)

    def test_storage_failing_cannot_take_the_shortlist_with_it(self):
        # localStorage throws outright in some privacy modes. A shortlist
        # that will not render is a far worse outcome than one that forgets
        # which rows were clicked, so every access is wrapped.
        body = self._app().test_client().get("/results").get_data(as_text=True)
        script = body[body.index("<script>", body.index("<main>")):]
        for access in ("localStorage.getItem", "localStorage.setItem"):
            before = script[:script.index(access)]
            self.assertIn("try", before[-120:], f"{access} is not wrapped")

    def test_an_opened_row_reads_as_seen_not_as_disabled(self):
        css = (pathlib.Path(app_module.__file__).parent
               / "static" / "sweep.css").read_text()
        faded = re.search(r"tr\.opened td \{[^}]*opacity: ([\d.]+)", css)
        self.assertIsNotNone(faded)
        # Disabled controls in this file sit at .5; a row matching that
        # would read as unavailable rather than already read.
        self.assertGreater(float(faded.group(1)), 0.5)
        self.assertLess(float(faded.group(1)), 0.85)
        # Hover restores it, which is the other half of saying it is live.
        self.assertRegex(css, r"tr\.opened:hover td \{[^}]*opacity: 1")

    def test_the_row_is_not_a_second_tab_stop(self):
        # A shortlist runs to hundreds of rows; giving each one a tabindex
        # would put a duplicate stop in front of every Apply link on the page.
        body = self._app().test_client().get("/results").get_data(as_text=True)
        self.assertNotIn("tabindex", body)
        self.assertNotIn('role="link"', body)


class TestFilterAndDisclosureUi(Isolated):
    """Two shapes the results screen gets wrong easily: where a form's action
    sits relative to its fields, and what a collapsed section looks like next
    to the panels it sits between."""

    def _body(self, state=None):
        app = app_module.create_app(
            state=state if state is not None else {"profile": "kanav"},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            read_rows=lambda profile: list(ROWS),
            read_done=lambda profile, day: [], read_spend=lambda: 4.12,
            output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        return app.test_client().get("/results").get_data(as_text=True)

    def css(self):
        return (pathlib.Path(app_module.__file__).parent
                / "static" / "sweep.css").read_text()

    def _row_children(self, body):
        """Tag names inside every <div class="row">, in order."""
        from html.parser import HTMLParser

        class Scan(HTMLParser):
            def __init__(self):
                super().__init__()
                self.depth = None
                self.rows = []

            def handle_starttag(self, tag, attrs):
                d = dict(attrs)
                if tag == "div" and (d.get("class") or "") == "row":
                    self.depth = 0
                    self.rows.append([])
                elif self.depth is not None:
                    if tag == "div":
                        self.depth += 1
                    self.rows[-1].append(tag)

            def handle_endtag(self, tag):
                if tag == "div" and self.depth is not None:
                    if self.depth == 0:
                        self.depth = None
                    else:
                        self.depth -= 1

        scan = Scan()
        scan.feed(body)
        return scan.rows

    def test_the_action_is_not_inside_the_field_row(self):
        # It used to be the row's last item, where button.secondary's own
        # align-self: flex-start beat the row's align-items: flex-end — so it
        # rendered level with the LABELS and read as a control sitting above
        # the inputs rather than after them.
        rows = self._row_children(self._body())
        self.assertTrue(rows, "no field row rendered")
        for children in rows:
            self.assertNotIn("button", children)
        self.assertNotIn(".row > button", self.css())

    def test_a_lone_field_does_not_stretch_to_the_panel(self):
        # The re-rank row holds one number input, which grew to the full
        # width of the panel to hold the digit 6.
        self.assertIn(".row > label:only-child", self.css())

    def test_a_collapsed_section_is_headed_like_the_panels_around_it(self):
        # It was a 15px sentence with the count folded into an em-dash string,
        # sitting between panels with an h2 and a right-aligned count.
        body = self._body()
        summaries = re.findall(r"<summary>(.*?)</summary>", body, re.S)
        self.assertTrue(summaries)
        for inner in summaries:
            self.assertIn("<h2>", inner)
        # And the disclosure box is the same box as its neighbours.
        self.assertNotIn("<details>", body)

    def test_the_marker_is_drawn_not_a_rotated_glyph(self):
        # A rotated "›" turns around the middle of a box taller than its ink,
        # which left a mark floating above the text that read as a stray tick.
        css = self.css()
        self.assertNotIn('content: "›"', css)
        self.assertRegex(css, r"summary::before \{[^}]*border-left-color")

    def test_a_disclosure_keeps_its_content_spaced(self):
        # Chrome wraps a details' content in ::details-content, so the
        # children are not flex items of the details and .panel's own gap
        # never reaches them. Narrowing this rule to details:not(.panel) —
        # which is every details in the app — butted the paragraphs together.
        self.assertRegex(self.css(),
                         r"details > \*:not\(summary\) \{[^}]*margin-top")


class TestReRankWeights(Isolated):
    """The panel said "re-rank against your current weights" and offered no
    way to see or change them — they were two screens back and unreachable
    from here. Editing them is the point of the panel.

    rescore_from_apify.py scores against profiles/<name>.py, so the edit has
    to reach that FILE before the child reads it.
    """

    def _app(self, state=None, **kw):
        self.written = {}
        self.started = []
        state = state if state is not None else {
            "profile": "kanav", "cap_usd": 8.41, "derived": DERIVED}
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            read_rows=lambda profile: list(ROWS),
            read_done=lambda profile, day: [], read_spend=lambda: 4.12,
            start_rescore=kw.pop(
                "start_rescore",
                lambda profile, hours: self.started.append((profile, hours))
                                       or FakeProc()),
            output_dir=tempfile.mkdtemp(), **kw)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, src: self.written.update({n: src})
        self.state = state
        return app

    def post(self, app, **fields):
        weights = fields.pop("weights", {})
        data = {"hours": fields.pop("hours", "6"),
                "term": list(weights), "weight": [str(w) for w in weights.values()],
                "drop": list(fields.pop("drop", ()))}
        data.update(fields)
        return app.test_client().post("/rescore", data=data)

    def weights_of(self, source):
        """The weights as the rendered profile states them."""
        block = source.split('"skill_weights": {', 1)[1].split("}", 1)[0]
        return {m.group(1): int(m.group(2))
                for m in re.finditer(r"'([^']+)':\s*(\d+)", block)}

    # ---- the editor is there --------------------------------------------
    def test_the_panel_carries_the_weight_editor(self):
        body = self._app().test_client().get("/results").get_data(as_text=True)
        # Same macro the review screen uses, so the two cannot drift.
        self.assertIn('class="weights"', body)
        self.assertIn('name="weight"', body)
        self.assertIn('name="drop"', body)
        self.assertIn("react native", body)
        # A stepper per skill, not a bare number box — plus the one on the
        # add-a-skill row.
        self.assertEqual(body.count('class="stepper"'),
                         len(DERIVED["skill_weights"]) + 1)

    def test_the_editor_and_the_review_screen_render_the_same_table(self):
        app = self._app(state={"profile": "kanav", "cap_usd": 8.41,
                                "derived": DERIVED, "resume_text": "x"})
        client = app.test_client()
        results = client.get("/results").get_data(as_text=True)
        review = client.get("/review").get_data(as_text=True)
        for markup in ('<input type="hidden" name="term"',
                       'name="weight" min="1" max="5"',
                       'aria-label="Raise the weight for react native"'):
            self.assertIn(markup, results, markup)
            self.assertIn(markup, review, markup)

    # ---- and it reaches the file the re-score reads ----------------------
    def test_a_changed_weight_is_written_before_the_child_starts(self):
        app = self._app()
        r = self.post(app, weights={"react native": 2, "node.js": 5})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.weights_of(self.written["kanav"])["react native"], 2)
        self.assertEqual(self.started, [("kanav", 6)])
        # And state carries the edit, so the panel shows what it will use
        # next time rather than snapping back to the old number.
        self.assertEqual(
            {w["term"]: w["weight"] for w in self.state["derived"]["skill_weights"]}
            ["react native"], 2)

    def test_a_removed_term_leaves_the_scoring(self):
        app = self._app()
        self.post(app, weights={"react native": 5}, drop=["javascript", "git"])
        written = self.weights_of(self.written["kanav"])
        self.assertNotIn("javascript", written)
        self.assertNotIn("git", written)
        self.assertIn("react native", written)
        self.assertEqual(len(self.state["derived"]["skill_weights"]), 2)

    def test_re_ranking_without_changing_anything_writes_no_profile(self):
        # A profile can carry weeks of hand-tuning; a re-rank that only wants
        # newer data has no business rewriting it.
        app = self._app()
        current = {w["term"]: w["weight"] for w in DERIVED["skill_weights"]}
        r = self.post(app, weights=current)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.written, {})
        self.assertEqual(self.started, [("kanav", 6)])

    def test_a_session_with_no_weights_still_re_ranks(self):
        # No derivation after a restart: the panel renders no editor, so the
        # POST carries no term fields. That is a plain re-rank against the
        # profile as it stands — not an instruction to drop every skill.
        app = self._app(state={"profile": "kanav", "cap_usd": 8.41})
        body = app.test_client().get("/results").get_data(as_text=True)
        self.assertNotIn('name="weight"', body)
        self.assertIn("not in memory", body)
        r = self.post(app)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.written, {})
        self.assertEqual(self.started, [("kanav", 6)])

    # ---- refusals leave no trace ----------------------------------------
    def test_a_weight_out_of_range_changes_nothing(self):
        app = self._app()
        r = self.post(app, weights={"react native": 9})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Weight for react native", r.get_data(as_text=True))
        self.assertEqual(self.written, {})
        self.assertEqual(self.started, [])
        self.assertEqual(self.state["derived"], DERIVED)

    def test_a_desynced_form_changes_nothing(self):
        # Two parallel lists: a desync would reassign weights to the wrong
        # terms, silently, on the numbers that decide the ranking.
        app = self._app()
        r = app.test_client().post("/rescore", data={
            "hours": "6", "term": ["react native", "node.js"],
            "weight": ["3"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.written, {})
        self.assertEqual(self.started, [])

    def test_a_refused_second_re_rank_does_not_rewrite_the_profile(self):
        # The 409 path: a request refused because one is already running must
        # leave no trace, which is why the write is inside the lock.
        app = self._app(state={"profile": "kanav", "cap_usd": 8.41,
                                "derived": DERIVED,
                                "rescore_proc": FakeProc()})
        r = self.post(app, weights={"react native": 1})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.written, {})
        self.assertEqual(self.started, [])
        self.assertEqual(self.state["derived"], DERIVED)


class TestMergeOffer(Isolated):
    """The screen used to tell the user to go and run merge_jobs.py — and told
    them so whenever it was showing an unmerged file, including when there was
    one sweep on disk and nothing to fold in.

    These exercise the REAL detector against temp files: it has to see exactly
    what merge_jobs.py's own glob sees, or the offer claims sweeps the merge
    will not read.
    """

    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out)
        self.dir = os.path.join(self.out, "kanav")
        os.makedirs(self.dir)
        self.launched = []

    def sweep_file(self, name, when=None):
        """One of the engine's per-sweep outputs. merge_jobs.py merges the
        JSON, not the CSV."""
        path = os.path.join(self.dir, name)
        pathlib.Path(path).write_text("[]")
        if when is not None:
            os.utime(path, (when, when))
        return path

    def _app(self, **kw):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            read_rows=kw.pop("read_rows", lambda profile: list(ROWS)),
            start_merge=kw.pop(
                "start_merge",
                lambda profile: self.launched.append(profile) or FakeProc()),
            output_dir=self.out, **kw)
        app.config.update(TESTING=True)
        return app

    def body(self, **kw):
        return self._app(**kw).test_client().get("/results").get_data(as_text=True)

    # ---- when there is nothing to fold in -------------------------------
    def test_one_sweep_is_not_offered_a_merge(self):
        self.sweep_file("jobs_2026-09-09_1800.json")
        body = self.body()
        # Still says which file it read — that is exactly what a user needs
        # when every row has been filtered out.
        self.assertIn("Showing your most recent sweep only", body)
        self.assertIn("only one on disk", " ".join(body.split()))
        self.assertNotIn('action="/merge"', body)

    def test_no_files_at_all_offers_nothing(self):
        self.assertNotIn('action="/merge"', self.body())

    def test_the_merged_file_is_not_counted_as_an_earlier_sweep(self):
        # merge_jobs.py skips its own output, so the offer must too — or a
        # merged profile is told it has one more sweep than it has.
        self.sweep_file("jobs_2026-09-09_1800.json")
        self.sweep_file("jobs_combined.json")
        self.sweep_file("jobs_all.json")
        self.assertNotIn('action="/merge"', self.body())

    # ---- when there is ---------------------------------------------------
    def test_earlier_sweeps_are_counted_and_dated(self):
        base = 1_600_000_000
        self.sweep_file("jobs_2026-09-09_1800.json", when=base + 300)
        self.sweep_file("jobs_2026-08-26_1651.json", when=base + 200)
        self.sweep_file("jobs_2026-08-26_1701.json", when=base + 100)
        body = " ".join(self.body().split())
        # Two earlier files, one earlier DAY: the offer is about which days
        # are on disk, not how many files.
        self.assertIn("2 earlier sweeps on disk", body)
        self.assertEqual(body.count("26 Aug"), 1)
        self.assertNotIn("9 Sep", body)
        self.assertIn('action="/merge"', body)

    def test_one_earlier_sweep_reads_as_one(self):
        base = 1_600_000_000
        self.sweep_file("jobs_2026-09-09_1800.json", when=base + 200)
        self.sweep_file("jobs_2026-08-26_1651.json", when=base + 100)
        self.assertIn("1 earlier sweep on disk", " ".join(self.body().split()))

    def test_a_hand_named_file_is_still_a_sweep_the_merge_will_read(self):
        # output directories hold these (jobs_chandigarh.json), merge_jobs.py
        # globs jobs_*.json, and a stamp-only detector would undercount.
        base = 1_600_000_000
        self.sweep_file("jobs_2026-09-09_1800.json", when=base + 200)
        self.sweep_file("jobs_chandigarh.json", when=base + 100)
        self.assertIn("1 earlier sweep on disk", " ".join(self.body().split()))

    def test_a_merged_view_is_not_offered_a_merge(self):
        # read_rows reports _merged on rows from jobs_combined*, and there is
        # nothing to fold in that is not already folded.
        self.sweep_file("jobs_2026-09-09_1800.json")
        self.sweep_file("jobs_2026-08-26_1651.json")
        merged = [dict(r, _merged="1") for r in ROWS]
        self.assertNotIn('action="/merge"',
                         self.body(read_rows=lambda profile: merged))

    # ---- running it ------------------------------------------------------
    def test_the_button_launches_the_merge(self):
        app = self._app()
        r = app.test_client().post("/merge")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/results", r.headers["Location"])
        self.assertEqual(self.launched, ["kanav"])

    def test_a_merge_needs_a_profile(self):
        app = app_module.create_app(state={}, extract=lambda p: "x",
                                    derive=lambda t, p: DERIVED)
        app.config.update(TESTING=True)
        r = app.test_client().post("/merge")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/")

    def test_two_clicks_launch_one_child(self):
        # merge_jobs.py truncates jobs_combined.csv and .json in place with no
        # lock of its own, so two children interleave writes to both.
        app = self._app()
        client = app.test_client()
        self.assertEqual(client.post("/merge").status_code, 302)
        second = client.post("/merge")
        self.assertEqual(second.status_code, 409)
        self.assertIn("merge is already running", second.get_data(as_text=True))
        self.assertEqual(len(self.launched), 1)

    def test_a_merge_and_a_re_score_cannot_overlap(self):
        # They write the SAME two files, so each has to refuse while the
        # other runs — and name the one that is actually running.
        app = self._app(start_rescore=lambda profile, hours: FakeProc())
        client = app.test_client()
        client.post("/merge")
        r = client.post("/rescore", data={"hours": "6"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("merge is already running", r.get_data(as_text=True))

        app2 = self._app(start_rescore=lambda profile, hours: FakeProc())
        client2 = app2.test_client()
        client2.post("/rescore", data={"hours": "6"})
        r2 = client2.post("/merge")
        self.assertEqual(r2.status_code, 409)
        self.assertIn("re-score is already running", r2.get_data(as_text=True))
        # Only the first client's merge ever started: the second app refused
        # before launching one.
        self.assertEqual(self.launched, ["kanav"])

    def test_a_running_merge_says_so_instead_of_offering_again(self):
        base = 1_600_000_000
        self.sweep_file("jobs_2026-09-09_1800.json", when=base + 200)
        self.sweep_file("jobs_2026-08-26_1651.json", when=base + 100)
        app = self._app()
        client = app.test_client()
        client.post("/merge")
        body = client.get("/results").get_data(as_text=True)
        self.assertIn("Merging now", body)
        self.assertNotIn('action="/merge"', body)


class TestAddingASkill(Isolated):
    """The parse misses things. Until now the editor could re-weight and
    remove what the model found and nothing else, so a skill it never saw
    could not be scored on at all."""

    def _app(self, state=None, **kw):
        self.written = {}
        state = state if state is not None else {
            "resume_text": "x", "derived": DERIVED, "cap_usd": 8.41,
            "profile": "kanav"}
        app = app_module.create_app(
            state=state, extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            read_rows=lambda profile: list(ROWS),
            read_done=lambda profile, day: [], read_spend=lambda: 4.12,
            profile_exists=lambda n: False,
            output_dir=tempfile.mkdtemp(), **kw)
        app.config.update(TESTING=True)
        app.write_profile = lambda n, src: self.written.update({n: src})
        self.state = state
        return app

    def skills_of(self, source):
        block = source.split('"skill_weights": {', 1)[1].split("}", 1)[0]
        return {m.group(1): int(m.group(2))
                for m in re.finditer(r"'([^']+)':\s*(\d+)", block)}

    def review(self, app, **fields):
        data = {"name": "kanav", "term": [w["term"] for w in DERIVED["skill_weights"]],
                "weight": [str(w["weight"]) for w in DERIVED["skill_weights"]]}
        data.update(fields)
        return app.test_client().post("/review", data=data)

    # ---- the control is there, on both screens --------------------------
    def test_both_editors_offer_it(self):
        app = self._app()
        client = app.test_client()
        for path in ("/review", "/results"):
            body = client.get(path).get_data(as_text=True)
            self.assertIn('name="add_skills"', body, path)
            self.assertIn('name="add_weight"', body, path)

    def test_it_is_offered_even_when_the_model_found_nothing(self):
        # The résumé that most needs this is the one the parse read no skills
        # from, and that screen used to offer only an apology.
        bare = dict(DERIVED, skill_weights=[])
        body = self._app(state={"resume_text": "x", "derived": bare}).test_client(
            ).get("/review").get_data(as_text=True)
        self.assertIn("No skills came back", body)
        self.assertIn('name="add_skills"', body)

    def test_a_profile_can_be_built_from_added_skills_alone(self):
        # The résumé the model read nothing from: before this the screen
        # offered an apology and a submit button that saved an unscoreable
        # profile. Now the skills can be named by hand.
        bare = dict(DERIVED, skill_weights=[])
        app = self._app(state={"resume_text": "x", "derived": bare,
                                "profile": "kanav"})
        r = app.test_client().post("/review", data={
            "name": "kanav", "add_skills": "salesforce, apex",
            "add_weight": "5"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.skills_of(self.written["kanav"]),
                         {"salesforce": 5, "apex": 5})

    def test_the_added_weight_is_not_named_weight(self):
        # "weight" is one of the two parallel lists. A third value under that
        # name desyncs them, which reassigns every weight to the wrong term.
        body = self._app().test_client().get("/review").get_data(as_text=True)
        self.assertEqual(body.count('name="weight"'),
                         len(DERIVED["skill_weights"]))

    # ---- what it writes --------------------------------------------------
    def test_an_added_skill_reaches_the_profile(self):
        app = self._app()
        self.review(app, add_skills="kubernetes, Terraform", add_weight="4")
        written = self.skills_of(self.written["kanav"])
        self.assertEqual(written["kubernetes"], 4)
        # Lowercased, because that is the form the profile stores and scraper
        # matches on.
        self.assertEqual(written["terraform"], 4)
        # And the skills that were already there are untouched.
        self.assertEqual(written["react native"], 5)

    def test_adding_a_skill_does_not_shift_the_existing_weights(self):
        app = self._app()
        self.review(app, add_skills="kubernetes", add_weight="1")
        written = self.skills_of(self.written["kanav"])
        for w in DERIVED["skill_weights"]:
            self.assertEqual(written[w["term"]], w["weight"], w["term"])

    def test_an_added_skill_that_already_exists_updates_it(self):
        # Asserted on STATE, not the rendered profile: _weights() folds the
        # list into a lowercased dict, so a duplicate collapses there and the
        # file looks fine while state carries the same term twice — and state
        # is what /estimate re-renders from and the re-rank editor shows.
        app = self._app()
        before = len(DERIVED["skill_weights"])
        self.review(app, add_skills="react native", add_weight="2")
        weights = self.state["derived"]["skill_weights"]
        self.assertEqual(len(weights), before)
        self.assertEqual([w for w in weights if w["term"] == "react native"],
                         [{"term": "react native", "weight": 2}])

    def test_a_differently_cased_term_is_the_same_term(self):
        # Terms are stored and matched lowercase. Without normalising, "React
        # Native" becomes a SECOND entry beside "react native", and the editor
        # then shows one skill twice with two different weights.
        app = self._app()
        before = len(DERIVED["skill_weights"])
        self.review(app, add_skills="React Native", add_weight="1")
        weights = self.state["derived"]["skill_weights"]
        self.assertEqual(len(weights), before)
        self.assertNotIn("React Native", [w["term"] for w in weights])
        self.assertEqual([w for w in weights if w["term"] == "react native"],
                         [{"term": "react native", "weight": 1}])

    def test_adding_a_term_takes_it_back_off_the_remove_list(self):
        # The remove column is pre-checked for commodity skills, so someone
        # typing one back has said the more specific thing.
        app = self._app()
        self.review(app, drop=["javascript"], add_skills="javascript",
                    add_weight="5")
        self.assertEqual(self.skills_of(self.written["kanav"])["javascript"], 5)

    def test_a_skill_can_be_added_from_the_re_rank_panel_too(self):
        # Same macro, same parser — and the whole point of that panel is to
        # change what a re-rank scores against.
        started = []
        app = self._app(start_rescore=lambda profile, hours: started.append(profile)
                        or FakeProc())
        r = app.test_client().post("/rescore", data={
            "hours": "6",
            "term": [w["term"] for w in DERIVED["skill_weights"]],
            "weight": [str(w["weight"]) for w in DERIVED["skill_weights"]],
            "add_skills": "kubernetes", "add_weight": "5"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.skills_of(self.written["kanav"])["kubernetes"], 5)
        self.assertEqual(started, ["kanav"])

    def test_removing_still_works_when_nothing_is_added(self):
        app = self._app()
        self.review(app, drop=["javascript"])
        self.assertNotIn("javascript", self.skills_of(self.written["kanav"]))

    # ---- and what it refuses --------------------------------------------
    def test_a_term_with_odd_characters_is_refused_not_sanitised(self):
        # It becomes a scoring pattern and a line in a generated Python file.
        app = self._app()
        r = self.review(app, add_skills="react; drop table")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Added skills", r.get_data(as_text=True))
        self.assertEqual(self.written, {})

    def test_the_stacks_that_look_like_punctuation_are_allowed(self):
        app = self._app()
        self.review(app, add_skills="node.js, c++, c#, ci/cd, .net",
                    add_weight="3")
        written = self.skills_of(self.written["kanav"])
        for term in ("node.js", "c++", "c#", "ci/cd", ".net"):
            self.assertIn(term, written)

    def test_a_weight_out_of_range_is_refused(self):
        app = self._app()
        r = self.review(app, add_skills="kubernetes", add_weight="9")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.written, {})

    def test_an_empty_box_is_not_an_error(self):
        # The overwhelmingly common submit: the form carries the control
        # whether or not anyone typed in it.
        app = self._app()
        r = self.review(app, add_skills="", add_weight="")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(self.skills_of(self.written["kanav"])),
                         len(DERIVED["skill_weights"]))


class TestResultSorting(Isolated):
    """The screen could narrow a shortlist but not reorder it. Everything here
    is a total order with a deterministic tie-break, because a list that
    reshuffles between two identical requests is worse than one bad order."""

    ROWS = [
        dict(ROWS[0], title="old-strong", score="90",
             date_posted="2026-01-01", experience_required="8+"),
        dict(ROWS[0], title="new-weak", score="20",
             date_posted="2026-09-01", experience_required="1+"),
        dict(ROWS[0], title="mid", score="50",
             date_posted="2026-05-05T16:05:10.325Z", experience_required="1+"),
        dict(ROWS[0], title="undated", score="40",
             date_posted="last tuesday", experience_required=""),
    ]

    def _app(self, rows=None):
        app = app_module.create_app(
            state={"profile": "kanav", "cap_usd": 8.41},
            extract=lambda p: "x", derive=lambda t, p: DERIVED,
            check_token=lambda t: (8.41, None),
            fetch_plan=lambda profile: RAW_PLAN,
            read_rows=lambda profile: list(self.ROWS if rows is None else rows),
            read_done=lambda profile, day: [], read_spend=lambda: 4.12,
            output_dir=tempfile.mkdtemp())
        app.config.update(TESTING=True)
        return app

    def order(self, query=""):
        body = self._app().test_client().get(
            f"/results{query}").get_data(as_text=True)
        return re.findall(r"<b>(old-strong|new-weak|mid|undated)</b>", body)

    # ---- the orders ------------------------------------------------------
    def test_best_match_is_the_default(self):
        self.assertEqual(self.order(), ["old-strong", "mid", "undated", "new-weak"])
        self.assertEqual(self.order("?sort=score"), self.order())

    def test_newest_first(self):
        self.assertEqual(self.order("?sort=recent")[:3],
                         ["new-weak", "mid", "old-strong"])

    def test_a_timestamp_is_a_date_not_a_missing_one(self):
        # 353 rows on disk carry a full ISO-8601 timestamp rather than a bare
        # date. Requiring an exact YYYY-MM-DD sank every one of them to the
        # bottom of "newest first" while looking like it worked.
        self.assertEqual(self.order("?sort=recent")[1], "mid")

    def test_a_row_with_no_usable_date_sorts_last_not_first(self):
        # Sorting an unreadable date as text would place it by whatever its
        # first character happens to be. "last tuesday" lands at the bottom
        # by luck; a year on its own lands at the TOP, above every real date
        # of that year, because the key is a prefix of all of them.
        self.assertEqual(self.order("?sort=recent")[-1], "undated")
        rows = self.ROWS + [dict(ROWS[0], title="year-only", score="10",
                                  date_posted="2026")]
        body = self._app(rows=rows).test_client().get(
            "/results?sort=recent").get_data(as_text=True)
        found = re.findall(r"<b>(old-strong|new-weak|mid|undated|year-only)</b>",
                           body)
        self.assertEqual(found[0], "new-weak")
        self.assertIn(found[-1], ("undated", "year-only"))
        self.assertGreater(found.index("year-only"), found.index("old-strong"))

    def test_least_experience_first(self):
        # Two rows state 1+; the better-scoring one wins the tie.
        self.assertEqual(self.order("?sort=experience")[:2], ["mid", "new-weak"])

    def test_an_unstated_experience_sorts_last_not_as_zero(self):
        # 44% of rows state none. Reading that as 0 would rank every unknown
        # as the easiest job on the page.
        self.assertEqual(self.order("?sort=experience")[-1], "undated")

    # ---- the order is a total one ---------------------------------------
    def test_ties_are_broken_by_score(self):
        tied = [dict(ROWS[0], title=t, score=s, date_posted="2026-05-05",
                     experience_required="2+")
                for t, s in (("low", "10"), ("high", "80"), ("mid2", "40"))]
        body = self._app(rows=tied).test_client().get(
            "/results?sort=recent").get_data(as_text=True)
        self.assertEqual(re.findall(r"<b>(low|high|mid2)</b>", body),
                         ["high", "mid2", "low"])

    def test_the_same_request_twice_gives_the_same_order(self):
        self.assertEqual(self.order("?sort=recent"), self.order("?sort=recent"))

    # ---- and it is chosen from a query string ---------------------------
    def test_an_unknown_sort_falls_back_rather_than_failing(self):
        self.assertEqual(self.order("?sort=%20;drop"), self.order())

    def test_the_chosen_order_is_selected_in_the_control(self):
        body = self._app().test_client().get(
            "/results?sort=recent").get_data(as_text=True)
        # The control has to post under the name the route reads, or the
        # whole thing is a dropdown that does nothing.
        self.assertIn('<select name="sort">', body)
        self.assertRegex(body, r'<option value="recent" selected>')
        self.assertNotIn('<option value="score" selected>', body)

    def test_a_forged_sort_does_not_reach_the_rendered_form(self):
        # The route normalises it before render, so the re-rank form's own
        # action cannot be built around a value the redirect will then throw
        # away — the two would disagree about which order you are in.
        body = self._app().test_client().get(
            "/results?sort=../etc").get_data(as_text=True)
        self.assertNotIn("../etc", body)
        self.assertNotIn("sort=", body[body.index("action=\"/rescore"):][:200])

    def test_the_order_survives_a_re_rank(self):
        # Coming back from a re-rank into a different order is the same
        # surprise as coming back with the filters cleared.
        app = self._app()
        app.write_profile = lambda n, src: None
        r = app.test_client().post(
            "/rescore?sort=recent&min=10", data={"hours": "6"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("sort=recent", r.headers["Location"])
        self.assertIn("min=10", r.headers["Location"])

    def test_the_default_order_is_not_written_into_every_url(self):
        # sort=score is what /results does anyway; carrying it makes every
        # shared link noisier for nothing.
        app = self._app()
        app.write_profile = lambda n, src: None
        r = app.test_client().post("/rescore?sort=score", data={"hours": "6"})
        self.assertNotIn("sort=", r.headers["Location"])

    def test_a_forged_sort_is_not_carried_into_the_redirect(self):
        app = self._app()
        app.write_profile = lambda n, src: None
        r = app.test_client().post("/rescore?sort=../etc", data={"hours": "6"})
        self.assertNotIn("sort=", r.headers["Location"])

    def test_the_submit_does_not_read_as_applying_for_a_job(self):
        # It applies the sort as well as the filters now, and "Apply" is what
        # the link in every row means on this screen.
        body = self._app().test_client().get("/results").get_data(as_text=True)
        form = body[body.index('<form class="panel" method="get">'):]
        self.assertNotIn(">Apply filter<", form[:form.index("</form>")])
        self.assertIn("Update the list", form[:form.index("</form>")])

    def test_pay_is_not_offered_as_a_sort(self):
        # Measured on output/ (10,397 rows): 5% state pay at all, in mixed
        # currencies and periods. The order would be 516 rows above 9,881
        # arbitrary ones, which reads as a broken sort rather than a sparse
        # column.
        self.assertNotIn("salary", app_module.SORTS)
        body = self._app().test_client().get("/results").get_data(as_text=True)
        self.assertNotIn('value="salary"', body)


class TestReadRowsDefault(Isolated):
    """The default read_rows closure. Every route test injects read_rows, so
    without this the jobs_combined-not-jobs_* rule A2 exists to enforce runs
    in production and nowhere else."""

    def _out_dir(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out)
        os.makedirs(os.path.join(out, "kanav"))
        return out

    def _write(self, out, name, score, title):
        path = os.path.join(out, "kanav", name)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write("score,title\n%s,%s\n" % (score, title))
        return path

    def _app(self, out):
        app = app_module.create_app(
            state={"profile": "kanav"}, extract=lambda p: "x",
            derive=lambda t, p: DERIVED, output_dir=out)
        app.config.update(TESTING=True)
        return app

    def test_the_merged_shortlist_wins_over_a_newer_partial_file(self):
        out = self._out_dir()
        combined = self._write(out, "jobs_combined.csv", "96", "From combined")
        partial = self._write(out, "jobs_2026-09-08_1200.csv", "50", "From partial")
        # Partial file deliberately NEWER — a jobs_*.csv glob would pick it and
        # show a fraction of the sweep as if it were the whole result.
        os.utime(combined, (1_000_000, 1_000_000))
        os.utime(partial, (2_000_000, 2_000_000))
        body = self._app(out).test_client().get("/results").get_data(as_text=True)
        self.assertIn("From combined", body)
        self.assertNotIn("From partial", body)

    def test_a_swept_profile_with_no_merge_still_shows_its_results(self):
        # scraper.py never writes a combined file — only merge_jobs.py and a
        # re-score do. Without the fallback a paid sweep finishes and the
        # screen says nothing was found. 8 of 16 real profile directories in
        # this repo are in exactly this state.
        out = self._out_dir()
        self._write(out, "jobs_2026-09-08_1200.csv", "96", "From the sweep")
        body = self._app(out).test_client().get("/results").get_data(as_text=True)
        self.assertIn("From the sweep", body)
        self.assertNotIn("Nothing was found for this profile yet", body)
        self.assertIn("most recent sweep only", body)

    def test_a_merged_shortlist_is_not_labelled_as_one_sweep(self):
        out = self._out_dir()
        self._write(out, "jobs_combined.csv", "96", "From combined")
        body = self._app(out).test_client().get("/results").get_data(as_text=True)
        self.assertIn("From combined", body)
        self.assertNotIn("most recent sweep only", body)

    def test_no_shortlist_yet_reads_as_empty_not_an_error(self):
        out = self._out_dir()
        r = self._app(out).test_client().get("/results")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Nothing was found for this profile yet",
                      r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
