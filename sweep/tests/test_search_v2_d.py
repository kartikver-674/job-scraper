"""Search Engine V2-D's production changes. Mostly D1: a public visitor funds
one sweep from several of their own Apify accounts, and the plan runs WHOLE or
— only when they say so — as its longest safely placeable prefix. Never
silently smaller. Also D4's per-provider poll interval.

Three layers, each against its own fakes, none with a network or a real key:

  engine   scraper.main() in-process through V2-C2's c2_sweep and C4.5's
           PoolClient (accounts, memory, run slots, readings); the visitor's
           keys arrive on a patched stdin exactly as the worker pipes them.
  worker   deploy/sweep_worker's own app and Queue: several held keys per
           visitor, released one at a time, frozen per run, piped to the child.
  render   the public app against a real worker on a loopback socket, with a
           dry run that says the account pool is on.

The numbers in the matrix comments are the brief's D1.26 items.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from decimal import Decimal
from unittest import mock

import config
import scraper
import telemetry
from deploy import sweep_worker
from sweep import logic
from sweep import plan as plan_mod
from sweep import worker_client
from sweep.tests import test_paid_concurrency as c2
from sweep.tests import test_paid_multi_account as c45
from sweep.tests.test_public_sweep import reviewed
from sweep.tests.test_worker_link import (WORKER_TOKEN, FakeChild, account_reader,
                                          render_app)

Acct, pooled, Script = c45.Acct, c45.pooled, c45.Script
LI, IN = c45.LI, c45.IN
KW = c2.KW
SECRETS = c45.SECRETS
OPERATOR = "OPERATOR_DEVELOPER_KEY_NEVER_PUBLIC"

setUpModule, tearDownModule = c45.setUpModule, c45.tearDownModule


def li(n, **kw):
    return c45.li_units(n, **kw)


def indeed(n, **kw):
    return c45.indeed_units(n, **kw)


def auth_of(got):
    return got.execution.get("authorization") or {}


def started(got):
    return len(got.client.kinds("start"))


def done_lines(got):
    return [line for line in got.done if line.strip()]


def refused(got):
    return f"SystemExit: {scraper.PAID_REFUSED_EXIT}" in got.log


def byok(scripts, accts, keys=None, *, env=None, patches=(), partial=False, **kw):
    """c2_sweep with the pool on, its credentials from the BYOK source — the
    visitor's keys on stdin, as deploy/sweep_worker pipes them — and NOT from
    _require_token_pool's developer branch. An OPERATOR key sits in the
    environment throughout: it must never be read."""
    accounts = {SECRETS[i]: a for i, a in enumerate(accts)}
    keys = list(accounts) if keys is None else keys

    def factory(by_input, account=None):
        return c45.PoolClient(by_input, accounts=accounts)

    def no_dotenv(*a, **k):
        raise AssertionError("a BYOK run read .env")
    stdin = io.StringIO(json.dumps({"apify_tokens": keys}))
    extra = [mock.patch.object(scraper, "_byok", None),
             mock.patch.object(scraper.sys, "stdin", stdin),
             mock.patch("dotenv.load_dotenv", no_dotenv),
             mock.patch.object(scraper, "_require_token", c45.forbid_single)]
    if partial:
        extra.append(mock.patch.dict(scraper.SETTINGS, allow_partial_paid_sweep=True))
    kw.setdefault("workers", 2)
    with mock.patch.object(c2, "KeyedClient", factory):
        got = c2.c2_sweep(scripts, env=dict({c45.FLAG: "1", scraper.BYOK_ENV: "stdin",
                                             "APIFY_TOKEN": OPERATOR}, **(env or {})),
                          patches=[*extra, *patches], **kw)
    return got, accounts


# ===========================================================================
# Engine: full or partial, decided on this sweep's own readings
# ===========================================================================
class FullOrPartial(unittest.TestCase):

    def test_1_one_account_can_place_the_full_plan(self):
        got, _ = pooled(li(3), [Acct("u1")], keywords=KW[:3])
        self.assertEqual(auth_of(got)["outcome"], "full")
        self.assertTrue(auth_of(got)["full_plan_placeable"])
        self.assertEqual(started(got), 3)

    def test_2_no_single_account_but_the_pool_places_it(self):
        accts = [Acct("u1", capacity="0.092"), Acct("u2", capacity="0.092")]
        got, _ = pooled(li(4), accts, keywords=KW[:4])
        self.assertEqual(auth_of(got)["outcome"], "full")
        self.assertEqual(started(got), 4)

    def test_3_fragmented_capacity_is_not_the_full_plan(self):
        """Mutation B: $0.20 across two accounts, one $0.135 search — no
        single account holds it, so nothing starts."""
        accts = [Acct("u1", capacity="0.10"), Acct("u2", capacity="0.10")]
        got, _ = pooled(indeed(1), accts, keywords=KW[:1], sites=("indeed",))
        self.assertEqual(started(got), 0)
        self.assertEqual(auth_of(got)["outcome"], "refused")
        self.assertTrue(refused(got))

    def test_7_insufficient_and_partial_not_chosen_starts_nothing(self):
        """Mutation D: a full sweep never silently becomes a smaller one —
        not a paid start, not a result file, and a distinct exit."""
        got, _ = pooled(li(4), [Acct("u1", capacity="0.092")], keywords=KW[:4])
        self.assertEqual(got.client.kinds("start"), [])
        self.assertTrue(refused(got))
        self.assertEqual(done_lines(got), [])
        self.assertIsNone(got.csv)
        self.assertEqual(got.status(), ["skipped_insufficient_capacity"] * 4)
        self.assertIn("can safely cover 2 of the 4 paid searches", got.log)
        self.assertIn("or choose to run with the available credit", got.log)
        self.assertEqual(auth_of(got)["full_plan_requested"], True)
        self.assertEqual(auth_of(got)["partial_authorized_by_user"], False)

    def test_8_insufficient_and_partial_chosen_runs_the_prefix(self):
        """Mutation E: the user's choice is what lets any of it run."""
        got, _ = pooled(li(4), [Acct("u1", capacity="0.092")], keywords=KW[:4],
                        partial=True)
        self.assertEqual(started(got), 2)
        self.assertEqual(auth_of(got)["outcome"], "partial")
        self.assertEqual(auth_of(got)["placeable_paid_units"], 2)
        self.assertEqual(auth_of(got)["executed_paid_units"], 2)
        self.assertEqual(auth_of(got)["skipped_insufficient_capacity"], 2)
        self.assertEqual(Decimal(auth_of(got)["executed_bounded_exposure_usd"]), 2 * LI)
        self.assertEqual(Decimal(auth_of(got)["total_planned_bounded_exposure_usd"]), 4 * LI)
        self.assertIsNotNone(got.csv)

    def test_9_10_the_exact_boundary_and_a_mill_below_it(self):
        for cap, runs in (("0.092", 2), ("0.091", 1)):
            with self.subTest(cap=cap):
                got, _ = pooled(li(3), [Acct("u1", capacity=cap)], keywords=KW[:3],
                                partial=True)
                self.assertEqual(started(got), runs)
                self.assertEqual(auth_of(got)["placeable_paid_units"], runs)

    def test_11_ceilings_not_expected_spend_authorise_it(self):
        """Mutation A: $0.10 buys three LinkedIn searches at their estimate
        ($0.03 each) but holds only two of their $0.046 ceilings."""
        est = plan_mod.cost({"profile": "x", "sites": {"linkedin": [{}] * 3},
                             "max_results": {"linkedin": 15}},
                            config.SITE_RATES, config.SITE_RATE_BASIS)["total"]
        self.assertLess(est, 0.10)
        got, _ = pooled(li(3), [Acct("u1", capacity="0.10")], keywords=KW[:3])
        self.assertEqual(started(got), 0)
        self.assertEqual(auth_of(got)["placeable_paid_units"], 2)

    def test_12_the_partial_estimate_is_of_the_selected_searches(self):
        """Two LinkedIn fit, the Indeed searches after them do not: the
        partial estimate is two LinkedIn searches' price — not half of the
        four-search plan's."""
        got, _ = pooled(li(2) + indeed(2), [Acct("u1", capacity="0.10")],
                        keywords=KW[:2], sites=("linkedin", "indeed"), partial=True)
        a = auth_of(got)
        two_li = plan_mod.cost({"profile": "x", "sites": {"linkedin": [{}] * 2},
                                "max_results": {"linkedin": 15}},
                               config.SITE_RATES, config.SITE_RATE_BASIS)["total"]
        self.assertEqual(a["placeable_paid_units"], 2)
        self.assertAlmostEqual(a["partial_estimate_usd"], two_li)
        self.assertNotAlmostEqual(a["partial_estimate_usd"], a["full_estimate_usd"] / 2)

    def test_13_14_order_is_kept_and_nothing_leapfrogs(self):
        """Mutations F and G: Indeed is planned first here and fits nowhere;
        LinkedIn after it would fit, and still never runs — a partial sweep
        is a prefix of the plan, not the searches that happen to fit, and
        not the ones an earlier sweep found most useful."""
        accts = [Acct("u1", capacity="0.10"), Acct("u2", capacity="0.10")]
        got, _ = pooled(indeed(2) + li(2), accts, keywords=KW[:2],
                        sites=("indeed", "linkedin"), site_order=("indeed", "linkedin"),
                        partial=True)
        self.assertEqual(started(got), 0)
        self.assertEqual(auth_of(got)["placeable_paid_units"], 0)
        # ...and where a prefix runs, it is the first units in plan order.
        got, _ = pooled(li(4), [Acct("u1", capacity="0.092")], keywords=KW[:4],
                        partial=True)
        ran = [u["unit_id"] for u in got.telemetry["paid_units"]
               if u["status"] == "completed"]
        self.assertEqual(ran, ["paid_000", "paid_001"])

    def test_14_a_later_search_never_leapfrogs_one_that_cannot_run(self):
        """Mutation F: money places Indeed #1 on an account with no memory for
        it; Indeed #2, placed on the large account, could run — and does not:
        the partial sweep ends at the first search that cannot."""
        accts = [Acct("big", capacity="0.181"), Acct("small", capacity="0.135", mem_gb=2)]
        got, _ = pooled([Script(c2.solo(0))] + indeed(2), accts, keywords=KW[:1],
                        sites=("linkedin", "indeed"),
                        site_locations={"indeed": ["Bengaluru", "Pune"]}, partial=True)
        self.assertEqual(auth_of(got)["placeable_paid_units"], 1)
        self.assertEqual(started(got), 1)
        self.assertEqual(got.status(), ["completed", "skipped_insufficient_capacity",
                                        "skipped_insufficient_capacity"])

    def test_15_16_left_out_is_neither_failed_nor_done(self):
        """Mutations H and I."""
        got, _ = pooled(li(4), [Acct("u1", capacity="0.092")], keywords=KW[:4],
                        partial=True)
        self.assertEqual(got.status(), ["completed", "completed",
                                        "skipped_insufficient_capacity",
                                        "skipped_insufficient_capacity"])
        self.assertNotIn("FAILED", got.log)
        self.assertEqual(got.telemetry["paid_summary"]["failed"], 0)
        self.assertEqual(got.telemetry["paid_summary"]["skipped_insufficient_capacity"], 2)
        self.assertEqual(len(done_lines(got)), 2)
        for line in done_lines(got):
            self.assertTrue(line.split("|")[2] in KW[:2], line)

    def test_17_a_rerun_with_more_credit_runs_only_what_was_left(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        first, _ = pooled(li(4), [Acct("u1", capacity="0.092")], keywords=KW[:4],
                          partial=True, out=out)
        self.assertEqual(started(first), 2)
        shutil.rmtree(os.path.join(out, "telemetry"))     # read the second run's own
        second, _ = pooled(li(4), [Acct("u1", capacity="0.092"),
                                   Acct("u2", capacity="0.092")], keywords=KW[:4],
                           out=out)
        self.assertEqual(auth_of(second)["outcome"], "full")
        self.assertEqual(auth_of(second)["total_planned_paid_units"], 2)
        self.assertEqual(started(second), 2)
        self.assertEqual(second.status(), ["skipped_done", "skipped_done",
                                           "completed", "completed"])

    def test_18_the_fresh_reading_decides_a_full_sweep(self):
        """Mutation J: the screen saw room for the whole plan; by the time the
        sweep starts the account holds less. The engine reads every account
        itself, before any start, and starts nothing."""
        screen = plan_mod.coverage(
            {"profile": "x", "sites": {"linkedin": [{}] * 3},
             "charge_ceiling_usd": {"linkedin": "0.046"}}, [Decimal("0.20")])
        self.assertTrue(screen["full"])
        got, _ = pooled(li(3), [Acct("u1", capacity="0.092")], keywords=KW[:3])
        reads = [c for c in got.client.calls if c[0] in ("me", "limits")]
        self.assertTrue(reads, "the engine never read the account")
        self.assertEqual(started(got), 0)
        self.assertEqual(auth_of(got)["outcome"], "refused")

    def test_19_the_fresh_reading_sets_a_partial_sweeps_prefix(self):
        got, _ = pooled(li(3), [Acct("u1", capacity="0.046")], keywords=KW[:3],
                        partial=True)
        self.assertEqual(started(got), 1)
        self.assertEqual(auth_of(got)["placeable_paid_units"], 1)

    def test_20_an_unreadable_account_is_not_counted(self):
        got, _ = pooled(li(2), [Acct("bad", unreadable=True), Acct("u2", capacity="0.046")],
                        keywords=KW[:2], partial=True)
        self.assertEqual(started(got), 1)
        self.assertEqual(c45.section(got)["account_count"], 1)

    def test_21_nothing_readable_fails_closed_even_when_partial(self):
        """Mutation O: an account that cannot be read holds nothing."""
        for partial in (False, True):
            with self.subTest(partial=partial):
                got, _ = pooled(li(2), [Acct("bad", unreadable=True)], keywords=KW[:2],
                                partial=partial)
                self.assertEqual(got.client.kinds("start"), [])
                self.assertTrue(refused(got))

    def test_the_budget_still_holds_a_full_sweep_whole(self):
        """Mutation N: a full sweep the sweep's own cap cannot hold does not
        start; the accounts had room."""
        got, _ = pooled(li(3), [Acct("u1")], keywords=KW[:3], budget=0.092)
        self.assertEqual(started(got), 0)
        self.assertEqual(auth_of(got)["stop_reason"], "budget")
        self.assertIn("The spend cap $0.09 holds 2 of the 3", got.log)

    def test_the_authorization_record_is_beside_the_outputs(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        pooled(li(4), [Acct("u1", capacity="0.092")], keywords=KW[:4], partial=True,
               out=out)
        with open(os.path.join(out, scraper.AUTH_RECORD), encoding="utf-8") as fh:
            record = json.load(fh)
        self.assertEqual((record["outcome"], record["executed_paid_units"]), ("partial", 2))
        self.assertEqual(set(record) >= set(sweep_worker.AUTH_FIELDS), True)


# ===========================================================================
# Engine: the visitor's keys are the only keys
# ===========================================================================
class ByokSource(unittest.TestCase):

    def test_4_two_keys_to_one_account_are_one_account(self):
        """Mutation C."""
        one = Acct("same", capacity="0.092")
        got, _ = byok(li(4), [one, one], partial=True, keywords=KW[:4])
        self.assertEqual(c45.section(got)["account_count"], 1)
        self.assertEqual(started(got), 2)

    def test_23_no_key_reaches_telemetry_the_log_or_an_output(self):
        """Mutation K/T: the second and third keys too."""
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        accts = [Acct(f"u{i}", capacity="0.046") for i in range(3)]
        got, _ = byok(li(3), accts, keywords=KW[:3], out=out)
        self.assertEqual(started(got), 3)
        blobs = [got.log.encode(), json.dumps(got.telemetry).encode(),
                 *c45.tree_bytes(out).values()]
        for key in SECRETS[:3] + [OPERATOR]:
            for blob in blobs:
                self.assertNotIn(key.encode(), blob)

    def test_24_the_same_placement_as_the_developer_source(self):
        accts = [Acct("u1", capacity="0.092"), Acct("u2", capacity="0.138")]
        public, _ = byok(li(5), accts, keywords=KW[:5])
        dev, _ = pooled(li(5), accts, keywords=KW[:5])
        self.assertEqual(c45.assigned(public), c45.assigned(dev))
        self.assertEqual([r["slots"] for r in c45.section(public)["accounts"]],
                         [["key_1"], ["key_2"]])

    def test_25_the_developer_source_is_untouched_without_the_marker(self):
        # A sealed environment: the suite's other tests may have loaded the
        # real .env into os.environ, and no real key belongs in an assertion.
        with mock.patch.dict(os.environ, {"APIFY_TOKEN": "dev-a", "APIFY_TOKEN_2": "dev-b"},
                             clear=True):
            with mock.patch.object(scraper, "_byok", None), \
                    mock.patch("dotenv.load_dotenv", lambda *a, **k: None):
                self.assertIsNone(scraper.byok_tokens())
                self.assertEqual(scraper._require_token_pool(),
                                 [("APIFY_TOKEN", "dev-a"), ("APIFY_TOKEN_2", "dev-b")])

    def test_a_byok_run_never_reads_the_environment_or_dotenv(self):
        got, _ = byok(li(1), [Acct("u1")], keywords=KW[:1])
        self.assertEqual(started(got), 1)
        self.assertNotIn(OPERATOR, [t for t in got.client.tokens if t])

    def test_malformed_keys_refuse_without_echoing(self):
        for body in ('{"apify_tokens": []}', '{"apify_tokens": [""]}', "not json",
                     json.dumps({"apify_tokens": ["k"] * (scraper.BYOK_MAX_KEYS + 1)})):
            with self.subTest(body=body[:30]), \
                    mock.patch.dict(os.environ, {scraper.BYOK_ENV: "stdin"}), \
                    mock.patch.object(scraper, "_byok", None):
                with self.assertRaises(SystemExit) as caught:
                    scraper.byok_tokens(io.StringIO(body))
                self.assertNotIn("not json", str(caught.exception))

    def test_27_the_developer_clamp_never_touches_a_visitors_accounts(self):
        """Mutation L, the engine's half: set in the box's environment, it
        is still not applied to BYOK capacity."""
        got, _ = byok(li(3), [Acct("u1", capacity="0.20")], keywords=KW[:3],
                      env={scraper.PAID_ACCOUNT_CAP_ENV: "0.046"})
        self.assertEqual(started(got), 3)
        self.assertIsNone(c45.section(got)["developer_account_cap"])

    def test_one_account_engine_takes_one_key_and_refuses_several(self):
        with mock.patch.dict(os.environ, {scraper.BYOK_ENV: "stdin",
                                          "APIFY_TOKEN": OPERATOR}):
            with mock.patch.object(scraper, "_byok", None), \
                    mock.patch.object(scraper.sys, "stdin",
                                      io.StringIO('{"apify_tokens": ["v1"]}')):
                self.assertEqual(scraper._require_token(), "v1")
            with mock.patch.object(scraper, "_byok", None), \
                    mock.patch.object(scraper.sys, "stdin",
                                      io.StringIO('{"apify_tokens": ["v1", "v2"]}')):
                with self.assertRaises(SystemExit):
                    scraper._require_token()


# ===========================================================================
# Worker: several held keys, one run's keys frozen, none in the environment
# ===========================================================================
class WorkerKeys(unittest.TestCase):

    def setUp(self):
        self.runs = tempfile.mkdtemp(prefix="d1-runs-")
        self.checkout = tempfile.mkdtemp(prefix="d1-checkout-")
        os.makedirs(os.path.join(self.checkout, "profiles"))
        self.addCleanup(shutil.rmtree, self.runs, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.checkout, ignore_errors=True)
        self.spawned = []
        store = sweep_worker.RunStore(self.runs)

        def spawn(run_id, run_dir, profile_name, checkout, tokens):
            self.spawned.append(tokens)
            return FakeChild(block=threading.Event()), None
        self.queue = sweep_worker.Queue(store, spawn=spawn, checkout=self.checkout)
        self.app = sweep_worker.create_app(store=store, queue=self.queue,
                                           accepted={WORKER_TOKEN}, checkout=self.checkout)
        self.client = self.app.test_client()
        self.store = store

    def hold(self, token, owner="ada"):
        r = self.client.post("/v1/tokens", json={"owner": owner, "apify_token": token},
                             headers={"Authorization": f"Bearer {WORKER_TOKEN}"})
        return r.status_code, (r.get_json() or {}).get("key_id")

    def start_run(self, key_ids=None, owner="ada"):
        from deploy.test_sweep_worker import PREFS, PROFILE
        body = {"profile": PROFILE, "prefs": PREFS, "free_only": False, "owner": owner}
        if key_ids is not None:
            body["key_ids"] = key_ids
        return self.client.post("/v1/runs", json=body,
                                headers={"Authorization": f"Bearer {WORKER_TOKEN}"})

    def test_several_keys_each_an_id_and_the_same_key_once(self):
        _, a = self.hold("key-a")
        _, b = self.hold("key-b")
        _, a2 = self.hold("key-a")
        self.assertNotEqual(a, b)
        self.assertEqual(a, a2)
        self.assertEqual(self.queue.held_tokens("ada"), ["key-a", "key-b"])

    def test_the_limit_is_generous_and_refused_past_it(self):
        for i in range(sweep_worker.MAX_KEYS):
            self.assertEqual(self.hold(f"k{i}")[0], 201)
        self.assertEqual(self.hold("one-too-many")[0], 400)

    def test_6_one_key_can_be_released(self):
        _, a = self.hold("key-a")
        _, b = self.hold("key-b")
        r = self.client.delete(f"/v1/tokens/{a}", headers={
            "Authorization": f"Bearer {WORKER_TOKEN}", "X-Sweep-Owner": "ada"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.queue.held_tokens("ada"), ["key-b"])

    def test_a_run_spends_exactly_the_keys_it_names_frozen(self):
        _, a = self.hold("key-a")
        _, b = self.hold("key-b")
        _, c = self.hold("key-c")
        self.assertEqual(self.start_run([a, c]).status_code, 201)
        self.assertEqual(self.spawned, [["key-a", "key-c"]])
        # Frozen: whatever is held or released now, the run has its keys.
        self.hold("key-d")
        self.assertEqual(self.spawned, [["key-a", "key-c"]])
        self.assertIsNone(self.queue.held_tokens("ada", [b]))

    def test_a_named_key_no_longer_held_refuses_the_run(self):
        _, a = self.hold("key-a")
        self.assertEqual(self.start_run([a, "not-a-held-key"]).status_code, 409)
        self.assertEqual(self.spawned, [])

    def test_27_the_developer_clamp_is_stripped_from_the_child(self):
        """Mutation L/K, the worker's half."""
        captured = {}

        class Pipe(io.BytesIO):
            def close(self):
                captured["stdin"] = self.getvalue()

        class FakePopen:
            def __init__(self, argv, cwd=None, env=None, stdin=None, stdout=None,
                         stderr=None):
                captured["env"], captured["argv"] = env, argv
                self.stdin = Pipe()
        with mock.patch.dict(os.environ, {"SWEEP_PAID_ACCOUNT_CAP_USD": "0.046",
                                          "SWEEP_BYOK_CREDENTIALS": "forged",
                                          "APIFY_TOKEN": OPERATOR}, clear=True):
            with mock.patch.object(sweep_worker.subprocess, "Popen", FakePopen):
                sweep_worker.default_spawn("abc", self.runs, "beta_abc", self.checkout,
                                           ["key-a", "key-b"])
        env = captured["env"]
        self.assertNotIn("SWEEP_PAID_ACCOUNT_CAP_USD", env)
        self.assertEqual(env["SWEEP_BYOK_CREDENTIALS"], "stdin")
        self.assertNotIn(OPERATOR, json.dumps(env))
        self.assertNotIn("key-a", json.dumps(env) + " ".join(captured["argv"]))
        self.assertEqual(json.loads(captured["stdin"]), {"apify_tokens": ["key-a", "key-b"]})

    def test_the_status_carries_the_authorization_and_nothing_else_of_it(self):
        _, a = self.hold("key-a")
        run_id = self.start_run([a]).get_json()["run_id"]
        with open(os.path.join(self.store.output_dir(run_id), scraper.AUTH_RECORD),
                  "w", encoding="utf-8") as fh:
            json.dump({"outcome": "refused", "placeable_paid_units": 2,
                       "total_planned_paid_units": 4, "accounts": ["x"],
                       "anything": "else"}, fh)
        body = self.client.get(f"/v1/runs/{run_id}", headers={
            "Authorization": f"Bearer {WORKER_TOKEN}", "X-Sweep-Owner": "ada"}).get_json()
        self.assertEqual(body["paid_authorization"]["outcome"], "refused")
        self.assertNotIn("accounts", body["paid_authorization"])
        self.assertNotIn("anything", body["paid_authorization"])


# ===========================================================================
# Render: the visitor's accounts, placed exactly, and an explicit choice
# ===========================================================================
KEY_A, KEY_B, KEY_A2 = ("apify_api_VISITOR_AAAA_0123456789",
                        "apify_api_VISITOR_BBBB_0123456789",
                        "apify_api_VISITOR_AAAA_SECOND_KEY")
CREDIT = {KEY_A: 0.20, KEY_B: 0.30, KEY_A2: 0.20}
SEARCH = {"keywords": "react native developer", "location": "Remote"}


def pool_plan(pool=True):
    plan = {"profile": "beta",
            "sites": {"linkedin": [dict(SEARCH, location=f"L{i}") for i in range(2)],
                      "indeed": [dict(SEARCH, location=f"I{i}") for i in range(2)]},
            "max_results": {"linkedin": 15, "indeed": 15},
            "charge_ceiling_usd": {"linkedin": "0.046", "indeed": "0.135"},
            "free_sources": 5}
    if pool:
        plan["account_pool"] = True
    return plan


@contextlib.contextmanager
def recording_worker():
    """The real worker app on a loopback socket, recording which keys reached
    each child."""
    from werkzeug.serving import make_server
    runs, checkout = tempfile.mkdtemp(), tempfile.mkdtemp()
    os.makedirs(os.path.join(checkout, "profiles"))
    spawned = []

    def spawn(run_id, run_dir, profile_name, checkout_dir, tokens):
        spawned.append(tokens)
        return FakeChild(block=threading.Event()), None
    store = sweep_worker.RunStore(runs)
    queue = sweep_worker.Queue(store, spawn=spawn, checkout=checkout)
    app = sweep_worker.create_app(store=store, queue=queue, accepted={WORKER_TOKEN},
                                  checkout=checkout)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with mock.patch.dict(os.environ, {worker_client.URL_ENV: url,
                                          worker_client.TOKEN_ENV: WORKER_TOKEN}):
            yield url, store, queue, spawned
    finally:
        server.shutdown()
        thread.join(timeout=5)
        shutil.rmtree(runs, ignore_errors=True)
        shutil.rmtree(checkout, ignore_errors=True)


class PublicSockets(unittest.TestCase):
    """The loopback worker needs real sockets; the engine classes above deny
    them module-wide, so this class restores them for its own tests."""

    def setUp(self):
        patcher = mock.patch.multiple(
            "socket", create_connection=c45._REAL_CREATE)
        patcher.start()
        self.addCleanup(patcher.stop)
        real = c45._REAL_CONNECT
        saved = __import__("socket").socket.connect
        __import__("socket").socket.connect = real
        self.addCleanup(setattr, __import__("socket").socket, "connect", saved)


class PublicMultiKey(PublicSockets):

    def visitor(self, url, pool=True, same=None):
        check = lambda token: (CREDIT[token], None)     # noqa: E731
        app = render_app(url, read_account=account_reader(check, same))
        client = reviewed(app, "beta_user")
        self.plan = mock.patch.object(worker_client, "plan",
                                      lambda *a, **kw: pool_plan(pool))
        self.plan.start()
        self.addCleanup(self.plan.stop)
        return app, client

    def profile_of(self, store):
        run = store.all()[-1]
        with open(os.path.join(store.dir(run["run_id"]), "profile.py"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_5_7_8_another_key_turns_partial_into_full(self):
        with recording_worker() as (url, store, queue, spawned):
            app, client = self.visitor(url)
            client.post("/key", data={"token": KEY_A})
            page = client.get("/confirm").get_data(as_text=True)
            self.assertIn("safely covers 2 of", page)
            self.assertIn("Run with my available credit anyway", page)
            # 7: no silent partial — refused, nothing created.
            refused_run = client.post("/run")
            self.assertEqual(refused_run.status_code, 400)
            self.assertIn("can&#39;t safely cover the full Sweep",
                          refused_run.get_data(as_text=True))
            self.assertEqual(store.all(), [])
            # 5: a second account and the plan is whole.
            added = client.post("/key", data={"token": KEY_B, "back": "confirm"})
            self.assertIn("/confirm", added.headers["Location"])
            page = client.get("/confirm").get_data(as_text=True)
            self.assertIn("can cover the full Sweep", page)
            self.assertNotIn("Run with my available credit anyway", page)
            self.assertEqual(client.post("/run").status_code, 302)
            self.assertEqual(spawned, [[KEY_A, KEY_B]])
            self.assertNotIn('"allow_partial_paid_sweep": True', self.profile_of(store))

    def test_8_the_ticked_box_is_the_profiles_partial_setting(self):
        with recording_worker() as (url, store, queue, spawned):
            app, client = self.visitor(url)
            client.post("/key", data={"token": KEY_A})
            client.get("/confirm")
            self.assertEqual(client.post("/run", data={"over_cap_ack": "yes"}).status_code,
                             302)
            self.assertIn('"allow_partial_paid_sweep": True', self.profile_of(store))
            self.assertEqual(spawned, [[KEY_A]])

    def test_11_the_estimate_fitting_is_not_the_plan_fitting(self):
        """Mutation A, public half: $0.25 of credit is more than the $0.234
        estimate and less than the $0.362 of ceilings — three of four."""
        with recording_worker() as (url, store, queue, spawned):
            CREDIT["apify_api_VISITOR_CCCC_ESTIMATE_ONLY"] = 0.26
            self.addCleanup(CREDIT.pop, "apify_api_VISITOR_CCCC_ESTIMATE_ONLY", None)
            app, client = self.visitor(url)
            client.post("/key", data={"token": "apify_api_VISITOR_CCCC_ESTIMATE_ONLY"})
            page = client.get("/confirm").get_data(as_text=True)
            self.assertIn("safely covers 3 of", page)
            self.assertEqual(client.post("/run").status_code, 400)
            self.assertEqual(store.all(), [])

    def test_a_key_pasted_again_after_its_run_is_accepted(self):
        """The worker spends every held key on the run it starts; this
        session forgets them too, so the same key is welcome next time."""
        with recording_worker() as (url, store, queue, spawned):
            app, client = self.visitor(url)
            client.post("/key", data={"token": KEY_A})
            client.post("/key", data={"token": KEY_B, "back": "confirm"})
            client.get("/confirm")
            self.assertEqual(client.post("/run").status_code, 302)
            client.post("/stop")
            again = client.post("/key", data={"token": KEY_A})
            self.assertEqual(again.status_code, 302, again.get_data(as_text=True)[:300])

    def test_6_removing_a_key_recalculates(self):
        with recording_worker() as (url, store, queue, spawned):
            app, client = self.visitor(url)
            client.post("/key", data={"token": KEY_A})
            client.post("/key", data={"token": KEY_B, "back": "confirm"})
            self.assertIn("can cover the full Sweep",
                          client.get("/confirm").get_data(as_text=True))
            ids = [k["id"] for k in next(iter(app.session_store._rooms.values()))
                   ["data"]["byok_keys"]]
            client.post("/key/forget", data={"key": ids[1], "back": "confirm"})
            self.assertIn("safely covers 2 of",
                          client.get("/confirm").get_data(as_text=True))
            owner = next(iter(queue._held))
            self.assertEqual(queue.held_tokens(owner), [KEY_A])

    def test_22_a_second_key_to_the_same_account_is_refused_safely(self):
        with recording_worker() as (url, store, queue, spawned):
            app, client = self.visitor(url, same={KEY_A2: f"acct-{KEY_A}"})
            client.post("/key", data={"token": KEY_A})
            dup = client.post("/key", data={"token": KEY_A2, "back": "confirm"})
            body = dup.get_data(as_text=True)
            self.assertEqual(dup.status_code, 400)
            self.assertIn("belongs to an Apify account already added", body)
            self.assertNotIn(KEY_A2, body)
            self.assertNotIn(f"acct-{KEY_A}", body)
            owner = next(iter(queue._held))
            self.assertEqual(queue.held_tokens(owner), [KEY_A])

    def test_no_key_or_account_id_in_any_page_or_session(self):
        with recording_worker() as (url, store, queue, spawned):
            app, client = self.visitor(url)
            client.post("/key", data={"token": KEY_A})
            client.post("/key", data={"token": KEY_B, "back": "confirm"})
            pages = " ".join(client.get(p).get_data(as_text=True)
                             for p in ("/configure", "/confirm"))
            rooms = json.dumps([r["data"] for r in app.session_store._rooms.values()],
                               default=str)
            for secret in (KEY_A, KEY_B, f"acct-{KEY_A}", f"acct-{KEY_B}"):
                self.assertNotIn(secret, pages)
                self.assertNotIn(secret, rooms)

    def test_27_no_form_or_api_field_reaches_the_developer_clamp(self):
        with recording_worker() as (url, store, queue, spawned):
            app, client = self.visitor(url)
            client.post("/key", data={"token": KEY_A})
            client.post("/key", data={"token": KEY_B, "back": "confirm"})
            client.get("/confirm")
            client.post("/run", data={"SWEEP_PAID_ACCOUNT_CAP_USD": "0.01",
                                      "account_cap_usd": "0.01"})
            source = self.profile_of(store)
            self.assertNotIn("0.01", source)
            self.assertNotIn("ACCOUNT_CAP", source.upper().replace("MAX_SPEND", ""))

    def test_one_account_engine_keeps_the_one_key_view(self):
        """No pool at the worker: the console's estimate-over-credit gate as
        before, and the run funded by the one account with the most credit."""
        with recording_worker() as (url, store, queue, spawned):
            app, client = self.visitor(url, pool=False)
            client.post("/key", data={"token": KEY_A})
            client.post("/key", data={"token": KEY_B, "back": "confirm"})
            page = client.get("/confirm").get_data(as_text=True)
            self.assertNotIn("Your Apify accounts", page)
            client.post("/run", data={"over_cap_ack": "yes"})
            self.assertEqual(spawned, [[KEY_B]])

    def test_the_running_screen_names_what_the_engine_decided(self):
        with recording_worker() as (url, store, queue, spawned):
            app, client = self.visitor(url)
            client.post("/key", data={"token": KEY_A})
            client.get("/confirm")
            client.post("/run", data={"over_cap_ack": "yes"})
            run_id = store.all()[-1]["run_id"]
            with open(os.path.join(store.output_dir(run_id), scraper.AUTH_RECORD), "w",
                      encoding="utf-8") as fh:
                json.dump({"outcome": "refused", "placeable_paid_units": 0,
                           "total_planned_paid_units": 4}, fh)
            client.post("/stop")
            progress = client.get("/progress").get_json()
            self.assertEqual(progress["state"], "credit_changed")


class States(unittest.TestCase):

    def test_refused_is_credit_changed_and_partial_is_its_own_ending(self):
        self.assertEqual(logic.sweep_state(False, 4, authorization={"outcome": "refused"}),
                         "credit_changed")
        partial = {"outcome": "partial", "skipped_insufficient_capacity": 2}
        self.assertEqual(logic.sweep_state(False, 2, authorization=partial), "partial")
        # More left over than the choice left out: something else stopped it.
        self.assertEqual(logic.sweep_state(False, 3, authorization=partial), "halted")
        self.assertEqual(logic.sweep_state(True, 2, authorization=partial), "running")
        self.assertEqual(logic.run_phase("partial"), "finished")
        self.assertEqual(logic.run_phase("credit_changed"), "failed")


class Coverage(unittest.TestCase):
    """plan.coverage, the public screens' advisory placement (V2-D1)."""

    RAW = {"profile": "x", "sites": {"linkedin": [{}], "indeed": [{}]},
           "charge_ceiling_usd": {"linkedin": "0.046", "indeed": "0.135"}}

    def test_3_fragmented_credit_is_not_coverage(self):
        """Mutation B, public half: $0.20 across two accounts holds the
        LinkedIn search and not the Indeed one, though it sums past both."""
        cov = plan_mod.coverage(self.RAW, [Decimal("0.10"), Decimal("0.10")], 0.50)
        self.assertEqual((cov["placeable_units"], cov["full"]), (1, False))
        self.assertGreater(cov["available_usd"], cov["bounded_exposure_usd"])

    def test_the_same_credit_on_one_account_covers_it(self):
        cov = plan_mod.coverage(self.RAW, [Decimal("0.181")], 0.50)
        self.assertEqual((cov["placeable_units"], cov["full"]), (2, True))

    def test_the_prefix_is_priced_by_its_own_searches(self):
        cov = plan_mod.coverage(self.RAW, [Decimal("0.05")], 0.50)
        self.assertEqual(cov["prefix"]["sites"], {"linkedin": [{}]})


class Polling(unittest.TestCase):
    """D4 (mutation O): Indeed polls every 2 s; LinkedIn keeps 5 s."""

    def intervals(self, scripts, sites, **kw):
        seen = []
        got, _ = pooled(scripts, [Acct("u1")], sites=sites, keywords=KW[:1],
                        patches=[mock.patch.object(scraper.time, "sleep", seen.append)],
                        **kw)
        return set(seen), got

    def test_each_provider_polls_at_its_own_interval(self):
        self.assertEqual(scraper.POLL_SECONDS, {"indeed": 2})
        self.assertEqual(scraper.POLL_DEFAULT_SECONDS, 5)
        li_seen, got = self.intervals(li(1), ("linkedin",))
        self.assertEqual(started(got), 1)
        self.assertEqual(li_seen, {5})
        in_seen, got = self.intervals(indeed(1), ("indeed",))
        self.assertEqual(started(got), 1)
        self.assertEqual(in_seen, {2})


class ProfileAndCap(unittest.TestCase):

    def test_28_the_generated_cap_holds_the_default_plans_ceilings(self):
        from sweep import app as app_module
        raw = {"profile": "x",
               "sites": {"linkedin": [{}] * 18, "indeed": [{}] * 72},
               "max_results": {"linkedin": 15, "indeed": 15},
               "charge_ceiling_usd": {"linkedin": "0.046", "indeed": "0.135"}}
        costed = plan_mod.cost(raw, config.SITE_RATES, config.SITE_RATE_BASIS)
        self.assertEqual(Decimal(costed["bounded_exposure"]), Decimal("10.548"))
        self.assertEqual(app_module.spend_cap_for(costed), 10.55)
        self.assertTrue(plan_mod.coverage(raw, [Decimal("10.548")], 10.55)["full"])
        self.assertFalse(plan_mod.coverage(raw, [Decimal("10.547")], 10.55)["full"])

    def test_29_unset_is_config_false_and_only_a_yes_is_written(self):
        import make_profile
        self.assertIs(config.SETTINGS["allow_partial_paid_sweep"], False)
        from deploy.test_sweep_worker import PREFS, PROFILE
        for value, expect in ((None, None), (True, "True"), (False, "False")):
            source = make_profile.render("p", PROFILE, dict(PREFS,
                                                             allow_partial_paid_sweep=value))
            if expect is None:
                self.assertNotIn("allow_partial_paid_sweep", source)
            else:
                self.assertIn(f'"allow_partial_paid_sweep": {expect}', source)

    def test_the_dry_run_says_whether_the_pool_is_the_engine(self):
        env = {scraper.PAID_CONCURRENCY_FLAG: "1", c45.FLAG: "1"}
        with mock.patch.dict(os.environ, env):
            self.assertTrue(scraper.paid_concurrency() and scraper.paid_multi_account())


if __name__ == "__main__":
    unittest.main()
