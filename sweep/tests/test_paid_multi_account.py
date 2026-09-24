"""Search Engine V2-C4.5: one sweep across several authorised Apify accounts —
and the budget, the plan, the bytes and the survivors stay what they were.

Under SWEEP_PAID_MULTI_ACCOUNT (with SWEEP_PAID_CONCURRENCY) every configured
account is read once, deduplicated by who it is, and every provider-bounded
search is assigned to one of them — exactly, before the first start. A start
then needs its full ceiling to fit the sweep's one budget AND its own
account's snapshot headroom, and its account's memory and a run slot. Money
committed is never handed back; memory and slots are, once the provider says
the run ended.

NO REAL CREDENTIAL AND NO REAL CLIENT. scraper.main() runs in-process through
V2-C2's c2_sweep against PoolClient: V2-C2's scripted, thread-safe KeyedClient
with accounts — ApifyClient(token) is that token's account, which can only
read its own runs. Tokens are fixtures (SUPER_SECRET_TOKEN_*), and every
artifact a sweep leaves is scanned for them. Sockets are denied, as in C2.
"""
import contextlib
import copy
import io
import json
import os
import random
import re
import shutil
import socket
import tempfile
import threading
import unittest
from collections import Counter
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import scraper
import telemetry
from bench import paid_guard
from sweep.tests import test_paid_adaptive as adaptive
from sweep.tests import test_paid_concurrency as c2
from sweep.tests import test_paid_dev_guard as c0
from sweep.tests import test_paid_observability as c1
from sweep.tests import test_search_v2_shadow_eval as b3

ROOT = Path(__file__).resolve().parents[2]
Script = c1.Script
KW = c2.KW
FLAG = scraper.PAID_MULTI_ACCOUNT_FLAG
SECRETS = [f"SUPER_SECRET_TOKEN_{c * 3}" for c in "ABCDEFGHIJKLMNOPQRST"]
MEMORY = {c2.LINKEDIN: 512, c2.INDEED: 4096}
LI, IN = Decimal("0.046"), Decimal("0.135")
BUFFER = scraper.ACCOUNT_BUFFER_USD

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def deny(*a, **kw):
        raise AssertionError("a C4.5 test tried to open a socket")
    socket.socket.connect = deny
    socket.create_connection = deny


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


def slot(i):
    return "APIFY_TOKEN" if i == 0 else f"APIFY_TOKEN_{i + 1}"


# ---------------------------------------------------------------------------
# The provider, with accounts
# ---------------------------------------------------------------------------
class Acct:
    """One fake Apify account. `capacity` is what the pool may commit on it
    (headroom less the buffer); `headroom` overrides it."""

    def __init__(self, ident, capacity=None, headroom=None, limit=5.0, credits=5.0,
                 mem_gb=16, mem_used=0, jobs=5, active=0, refuse=None,
                 unreadable=False):
        room = (Decimal(str(headroom)) if headroom is not None else
                Decimal(str(capacity)) + BUFFER if capacity is not None else Decimal(5))
        self.ident, self.limit, self.credits = ident, limit, credits
        self.used = float(Decimal(str(min(limit, credits))) - room)
        self.mem_gb, self.mem_used, self.jobs, self.active = mem_gb, mem_used, jobs, active
        self.refuse, self.unreadable = refuse, unreadable


class PoolClient(c2.KeyedClient):
    """C2's KeyedClient with accounts. ApifyClient(token) returns that token's
    View; a run is readable only by the account that started it; each
    account's usage is its own baseline plus the usage of its ended runs; the
    provider refuses a start that would exceed an account's memory or runs."""

    def __init__(self, by_input, account=None, accounts=None, memory=None):
        super().__init__(by_input, [0.0])
        self.accounts, self.memory = accounts, dict(memory or MEMORY)
        self.owner, self.live_mb, self.peak_mb = {}, Counter(), Counter()
        self.live_runs, self.peak_runs = Counter(), Counter()

    def __call__(self, token=None, **kw):
        with self._lock:
            self.tokens.append(token)
        if token not in self.accounts:
            raise AssertionError("a client was built on a token no account holds")
        return View(self, token)

    def _end(self, run_id):
        with self._lock:
            state = self.runs[run_id]
            if state[3]:
                token = self.owner[run_id]
                self.live_mb[token] -= self.memory[state[2]]
                self.live_runs[token] -= 1
        super()._end(run_id)

    def dataset(self, dataset_id):
        script = self.runs[dataset_id[len("ds_"):]][0]
        error = getattr(script, "dataset_error", None)
        if error is not None:
            def items():
                self._log("dataset", dataset_id)
                raise error
                yield                                              # noqa: unreachable
            return SimpleNamespace(iterate_items=items)
        return super().dataset(dataset_id)

    # -- what the tests read back ------------------------------------------
    def started_on(self):
        """{run_id: token} for every run the provider created."""
        return dict(self.owner)

    def attempts(self, kind="start"):
        return [c for c in self.calls if c[0] == kind]


class View:
    def __init__(self, pool, token):
        self.pool, self.token = pool, token

    @property
    def acct(self):
        return self.pool.accounts[self.token]

    def actor(self, actor_id):
        view = self

        class Actor:
            def start(self, *, run_input=None, run_timeout=None,
                      max_total_charge_usd=None):
                return view._start(actor_id, run_input, max_total_charge_usd)

            def get(self):
                view.pool._log("actor_get", actor_id)
                return SimpleNamespace(default_run_options=SimpleNamespace(
                    memory_mbytes=view.pool.memory[actor_id]))
        return Actor()

    def _start(self, actor_id, run_input, ceiling):
        a, p, mb = self.acct, self.pool, self.pool.memory[actor_id]
        if a.refuse is not None:
            p._log("refused", self.token, copy.deepcopy(run_input), ceiling)
            raise a.refuse
        with p._lock:
            if (p.live_mb[self.token] + mb > (a.mem_gb - a.mem_used) * 1024
                    or p.live_runs[self.token] + 1 > a.jobs - a.active):
                over = True
            else:
                over = False
                p.live_mb[self.token] += mb
                p.live_runs[self.token] += 1
                p.peak_mb[self.token] = max(p.peak_mb[self.token], p.live_mb[self.token])
                p.peak_runs[self.token] = max(p.peak_runs[self.token],
                                              p.live_runs[self.token])
        if over:
            p._log("refused", self.token, copy.deepcopy(run_input), ceiling)
            raise RuntimeError("By launching this job you will exceed the memory limit")
        try:
            run = p._start(actor_id, run_input, ceiling)
        except BaseException:
            with p._lock:
                p.live_mb[self.token] -= mb
                p.live_runs[self.token] -= 1
            raise
        with p._lock:
            p.owner[run.id] = self.token
            p.calls.append(("start_on", run.id, self.token, ceiling))
        return run

    def _mine(self, run_id):
        if self.pool.owner.get(run_id) != self.token:
            raise RuntimeError("run not found on this account")

    def run(self, run_id):
        self._mine(run_id)
        return self.pool.run(run_id)

    def dataset(self, dataset_id):
        self._mine(dataset_id[len("ds_"):])
        return self.pool.dataset(dataset_id)

    def user(self):
        a, view = self.acct, self

        def get():
            view.pool._log("me", a.ident)
            if a.unreadable:
                raise RuntimeError(f"401 for token {view.token}")    # quotes the token
            return SimpleNamespace(id=a.ident, plan=SimpleNamespace(
                id="FREE", monthly_usage_credits_usd=a.credits))

        def limits():
            view.pool._log("limits", a.ident)
            if a.unreadable:
                raise RuntimeError(f"401 for token {view.token}")
            with view.pool._lock:
                ended = sum(s[0].usage for rid, s in view.pool.runs.items()
                            if not s[3] and view.pool.owner.get(rid) == view.token)
            return SimpleNamespace(model_dump=lambda: {
                "limits": {"max_monthly_usage_usd": a.limit,
                           "max_actor_memory_gbytes": a.mem_gb,
                           "max_concurrent_actor_jobs": a.jobs},
                "current": {"monthly_usage_usd": a.used + ended,
                            "actor_memory_gbytes": a.mem_used,
                            "active_actor_job_count": a.active}})
        return SimpleNamespace(get=get, limits=limits)


def forbid_single():
    raise AssertionError("the pool must never fall back to _require_token")


def pooled(scripts, accts, *, env=None, patches=(), memory=None, **kw):
    """c2.c2_sweep with the pool on over `accts` (a list of Acct, slot order),
    each on its own SECRET token. Returns (Swept, {token: Acct})."""
    accounts = {SECRETS[i]: a for i, a in enumerate(accts)}
    tokens = [(slot(i), t) for i, t in enumerate(accounts)]

    def factory(by_input, account=None):
        return PoolClient(by_input, accounts=accounts, memory=memory)
    kw.setdefault("workers", 2)
    with mock.patch.object(c2, "KeyedClient", factory):    # c2_sweep builds it first
        got = c2.c2_sweep(
            scripts, env=dict({FLAG: "1"}, **(env or {})),
            patches=[mock.patch.object(scraper, "_require_token_pool", lambda: tokens),
                     mock.patch.object(scraper, "_require_token", forbid_single),
                     *patches], **kw)
    return got, accounts


def section(got):
    return got.execution["accounts"]


def assigned(got):
    """{unit_id: account label} as telemetry recorded the assignment."""
    return {u["unit_id"]: u.get("account") for u in got.execution["units"]
            if u.get("account")}


def owners(got, accounts):
    """{unit_id: account label} as the PROVIDER saw it: whose token started it."""
    label_of = {}
    for row in section(got)["accounts"]:
        for s in row["slots"]:
            label_of[SECRETS[0 if s == "APIFY_TOKEN" else int(s.rsplit("_", 1)[1]) - 1]] = \
                row["account"]
    return {scraper.paid_unit_id(int(rid.split("_")[1]) - 1): label_of[tok]
            for rid, tok in got.client.started_on().items()}


def li_units(n, **kw):
    return [Script(c2.solo(u), **kw) for u in range(n)]


def indeed_units(n, **kw):
    return [Script([c2.indeed_item(f"i{u}{j}", "Full Stack Engineer", f"Idx {u}x{j}")
                    for j in range(2)], **kw) for u in range(n)]


def tree_bytes(out):
    """Every file a sweep left, as bytes, for the secret scan."""
    found = {}
    for base, _, names in os.walk(out):
        for name in names:
            found[os.path.relpath(os.path.join(base, name), out)] = \
                Path(base, name).read_bytes()
    return found


def ledger_of(out):
    path = os.path.join(out, scraper.POOL_RECORD)
    return json.loads(Path(path).read_text()) if os.path.exists(path) else None


# ===========================================================================
# 1. The switch
# ===========================================================================
class Flag(unittest.TestCase):

    def test_default_is_off_and_only_the_conventional_values_count(self):
        with b3._env(**{FLAG: None}):
            self.assertFalse(scraper.paid_multi_account())
        for value, on in (("1", True), ("true", True), ("ON", True), (" yes ", True),
                          ("0", False), ("", False), ("off", False), ("2", False)):
            with b3._env(**{FLAG: value}):
                self.assertEqual(scraper.paid_multi_account(), on, value)

    def test_no_deployment_file_turns_it_on(self):
        f = re.escape(FLAG)
        for path in ("render.yaml", "deploy/sweep_worker.py", "requirements.txt",
                     "gunicorn.conf.py", "config.py"):
            body = (ROOT / path).read_text(encoding="utf-8")
            self.assertEqual(re.findall(
                r"""\benv\[["']%(f)s["']\]\s*=|\bkey:\s*%(f)s\b|^\s*%(f)s\s*="""
                % {"f": f}, body, re.M), [], f"{path} sets {FLAG}")

    def test_without_the_scheduler_it_is_one_account_as_before(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        got = c2.c2_sweep(li_units(2), keywords=KW[:2], env={FLAG: "1"}, out=out)
        self.assertIn(f"{FLAG} needs", got.log)
        self.assertEqual(set(got.client.tokens), {c2.TOKEN})
        self.assertEqual(len(got.client.kinds("start")), 2)
        self.assertFalse(os.path.exists(os.path.join(out, scraper.POOL_RECORD)))


class FlagOffIsC4(unittest.TestCase):
    """Mutation P's test: with the flag off (unset or "0") the C2 scheduler is
    C4's — the same requests, bytes, section and no pool artifact."""

    def test_the_same_requests_bytes_and_section(self):
        for workers in (1, 2, 4):
            runs = [c2.c2_sweep(c2.staggered([c2.solo(u) for u in range(4)]),
                                keywords=KW[:4], workers=workers, account=[1.0],
                                env={FLAG: value})
                    for value in (None, "0")]
            a, b = runs
            self.assertEqual(Counter(map(repr, a.client.calls)),
                             Counter(map(repr, b.client.calls)), workers)
            if workers == 1:
                self.assertEqual(a.client.calls, b.client.calls)
            self.assertEqual((a.csv, a.json, a.seen, a.done),
                             (b.csv, b.json, b.seen, b.done))
            for got in runs:
                self.assertNotIn("accounts", got.execution)
                self.assertFalse(any(u.get("account") for u in got.execution["units"]))
                self.assertEqual(set(got.client.tokens), {c2.TOKEN})

    def test_the_scheduler_gets_no_pool(self):
        seen, real = [], scraper.paid_phase_c2

        def spy(*a, **kw):
            seen.append(kw.get("pool"))
            return real(*a, **kw)
        c2.c2_sweep(li_units(1), keywords=KW[:1], workers=2,
                    patches=[mock.patch.object(scraper, "paid_phase_c2", spy)])
        self.assertEqual(seen, [None])


# ===========================================================================
# 2. Discovery: slots, identity, headroom
# ===========================================================================
class Fakes:
    """make_client for discover_accounts: token -> a View on `accounts`."""

    def __init__(self, accounts):
        self.pool = PoolClient({}, accounts=accounts)

    def __call__(self, token):
        return self.pool(token)


def discover(accts, names=None):
    accounts = {SECRETS[i]: a for i, a in enumerate(accts)}
    names = names or [slot(i) for i in range(len(accts))]
    return scraper.discover_accounts(list(zip(names, accounts)), Fakes(accounts))


class Discovery(unittest.TestCase):

    def env(self, **names):
        return {n: v for n, v in names.items()}

    def test_1_one_token_one_account(self):
        self.assertEqual(scraper.pool_tokens(self.env(APIFY_TOKEN="t1")),
                         [("APIFY_TOKEN", "t1")])
        accounts, excluded = discover([Acct("u1")])
        self.assertEqual(([a.label for a in accounts], excluded), (["account_000"], []))

    def test_2_3_five_and_ten_tokens_are_five_and_ten_accounts(self):
        for n in (5, 10):
            env = {slot(i): f"t{i}" for i in range(n)}
            self.assertEqual([s for s, _ in scraper.pool_tokens(env)],
                             [slot(i) for i in range(n)])
            accounts, _ = discover([Acct(f"u{i}") for i in range(n)])
            self.assertEqual([a.label for a in accounts],
                             [f"account_{i:03d}" for i in range(n)])

    def test_4_sparse_slots_in_numeric_order(self):
        env = {"APIFY_TOKEN_8": "c", "APIFY_TOKEN": "a", "APIFY_TOKEN_3": "b",
               "APIFY_TOKEN_10": "d", "APIFY_TOKEN_9": "e"}
        self.assertEqual([s for s, _ in scraper.pool_tokens(env)],
                         ["APIFY_TOKEN", "APIFY_TOKEN_3", "APIFY_TOKEN_8",
                          "APIFY_TOKEN_9", "APIFY_TOKEN_10"])

    def test_5_malformed_names_are_not_slots(self):
        env = {"APIFY_TOKEN": "a", "APIFY_TOKEN_0": "z0", "APIFY_TOKEN_02": "z2",
               "APIFY_TOKEN_X": "zx", "APIFY_TOKEN_2A": "z2a", "APIFY_TOKENS": "zs",
               "apify_token_4": "zl", "APIFY_TOKEN_": "ze", "APIFY_TOKEN_-3": "zn",
               "APIFY_TOKEN_5": "   "}
        self.assertEqual(scraper.pool_tokens(env), [("APIFY_TOKEN", "a")])

    def test_6_two_slots_on_one_account_are_one_account(self):
        """Mutations A and M's test: one balance, one memory limit, one set of
        run slots, one exposure ledger — the smaller of the two readings."""
        accounts, excluded = discover([Acct("u1", headroom=3.0), Acct("u2", headroom=1.0),
                                       Acct("u1", headroom=2.5, mem_gb=8)])
        self.assertEqual([a.label for a in accounts], ["account_000", "account_001"])
        a = accounts[0]
        self.assertEqual(a.slots, ["APIFY_TOKEN", "APIFY_TOKEN_3"])
        self.assertEqual((a.headroom, a.memory_mb), (Decimal("2.500"), 8 * 1024))
        self.assertEqual(a.exposure.budget, Decimal("2.500") - BUFFER)
        self.assertEqual(excluded, [{"slot": "APIFY_TOKEN_3",
                                     "reason": "same account as APIFY_TOKEN"}])
        self.assertEqual(len({id(x.exposure) for x in accounts}), 2)
        _, report = scraper.project_assignment([], accounts)
        self.assertEqual(Decimal(report["aggregate_headroom_usd"]), Decimal("3.500"))

    def test_an_account_that_cannot_be_identified_is_left_out(self):
        accounts, excluded = discover([Acct("u1"), Acct("u2", unreadable=True)])
        self.assertEqual([a.label for a in accounts], ["account_000"])
        self.assertEqual(excluded, [{"slot": "APIFY_TOKEN_2",
                                     "reason": "unreadable (RuntimeError)"}])

    def test_headroom_is_the_smaller_cap_less_usage_floored_to_a_mill(self):
        me = SimpleNamespace(id="u", plan=SimpleNamespace(id="STARTER",
                                                          monthly_usage_credits_usd=39.0))
        limits = {"limits": {"max_monthly_usage_usd": 100.0, "max_actor_memory_gbytes": 32,
                             "max_concurrent_actor_jobs": 25},
                  "current": {"monthly_usage_usd": 1.5921809, "actor_memory_gbytes": 4.0,
                              "active_actor_job_count": 3}}
        r = scraper._account_reading(me, limits)
        self.assertEqual(r["headroom_usd"], Decimal("37.407"))     # credit, not limit
        self.assertEqual((r["memory_mb"], r["run_slots"], r["plan"]),
                         (28 * 1024, 22, "STARTER"))
        limits["limits"]["max_monthly_usage_usd"] = 5.0
        me.plan.monthly_usage_credits_usd = 5.0
        self.assertEqual(scraper._account_reading(me, limits)["headroom_usd"],
                         Decimal("3.407"))
        limits["current"]["monthly_usage_usd"] = 9.0                 # over the limit
        self.assertEqual(scraper._account_reading(me, limits)["headroom_usd"], Decimal(0))
        self.assertEqual(scraper._account_reading(
            SimpleNamespace(id="u", plan=None), {"limits": {}, "current": {}}),
            {"plan": None, "headroom_usd": Decimal(0), "used_usd": 0.0, "memory_mb": 0,
             "run_slots": 0})

    def test_7_no_token_value_in_repr_snapshot_or_exclusion(self):
        accounts, excluded = discover([Acct("u1"), Acct("u2", unreadable=True)])
        pool = scraper.AccountPool(accounts, excluded, None)
        text = repr(accounts) + json.dumps(pool.snapshot(), default=str) + repr(excluded)
        for secret in SECRETS:
            self.assertNotIn(secret, text)


# ===========================================================================
# 3. The allocator
# ===========================================================================
def units(*ceilings):
    return [(f"u{i:03d}", Decimal(c)) for i, c in enumerate(ceilings)]


def caps(*values):
    return [(f"account_{i:03d}", Decimal(str(v))) for i, v in enumerate(values)]


def greedy(units_, accounts, pick):
    """A plan-order online allocator, for comparison: `pick` chooses among the
    accounts that still fit the unit."""
    left = {label: cap for label, cap in accounts}
    out = {}
    for unit_id, ceiling in units_:
        fits = [label for label, _ in accounts if left[label] >= ceiling]
        if not fits:
            break
        label = pick(fits, left)
        left[label] -= ceiling
        out[unit_id] = label
    return out


def brute_feasible(ceilings, capacities):
    """Every unit on every account: exhaustive, for small cases only."""
    order = sorted(ceilings, reverse=True)

    def go(i, left):
        if i == len(order):
            return True
        tried = set()
        for k, room in enumerate(left):
            if room >= order[i] and room not in tried:
                tried.add(room)
                left[k] -= order[i]
                if go(i + 1, left):
                    return True
                left[k] += order[i]
        return False
    return go(0, list(capacities))


class Allocator(unittest.TestCase):

    def check(self, units_, accounts, placed):
        """Every placed unit on one account, no account over its capacity."""
        cap = dict(accounts)
        load = Counter()
        for unit_id, ceiling in units_:
            if unit_id in placed:
                load[placed[unit_id]] += ceiling
        for label, used in load.items():
            self.assertLessEqual(used, cap[label], label)

    def test_1_one_account_fits_the_whole_plan(self):
        u = units(*["0.046"] * 18, *["0.135"] * 72)
        got = scraper.place_units(u, caps(20))
        self.assertEqual(set(got.values()), {"account_000"})
        self.assertEqual(len(got), 90)

    def test_2_no_single_account_fits_but_the_pool_places_every_unit(self):
        u = units(*["0.046"] * 18, *["0.135"] * 72)
        accounts = caps(4.99, 4.99, 4.99)
        self.assertTrue(all(Decimal("10.548") > c for _, c in accounts))
        got = scraper.place_units(u, accounts)
        self.assertEqual(len(got), 90)
        self.check(u, accounts, got)
        self.assertGreater(len(set(got.values())), 1)

    def test_3_aggregate_enough_but_no_account_fits_one_unit(self):
        """Mutation B's test: the sum is no account."""
        accounts = caps(0.10, 0.10, 0.10)
        self.assertEqual(scraper.place_units(units("0.135"), accounts), {})
        self.assertEqual(scraper.place_units(units("0.046", "0.135"), accounts),
                         {"u000": "account_000"})

    def test_4_5_exact_equality_fits_and_a_mill_less_does_not(self):
        u = units(*["0.046"] * 2, *["0.135"] * 2)
        self.assertEqual(len(scraper.place_units(u, caps("0.362"))), 4)
        self.assertEqual(len(scraper.place_units(u, caps("0.361"))), 3)
        self.assertEqual(len(scraper.place_units(units("0.135"), caps("0.134"))), 0)

    def test_6_mixed_ceilings_fill_the_fragments(self):
        """0.182 holds one of each; 0.135 holds one Indeed; greedy by plan
        order puts both LinkedIn on the first account and strands an Indeed."""
        u = units("0.046", "0.046", "0.135", "0.135")
        accounts = caps("0.182", "0.181")
        got = scraper.place_units(u, accounts)
        self.assertEqual(len(got), 4)
        self.check(u, accounts, got)

    def test_7_many_tiny_fragments_hold_nothing(self):
        accounts = caps(*["0.045"] * 30)
        self.assertEqual(scraper.place_units(units("0.046"), accounts), {})
        self.assertEqual(len(scraper.place_units(units(*["0.046"] * 3),
                                                 caps(*["0.045"] * 30, "0.139"))), 3)

    def test_8_best_fit_keeps_the_large_account_large(self):
        """The prompt's case: the nearly spent account takes the unit it can."""
        got = scraper.place_units(units("0.135"), caps("4.00", "0.14"))
        self.assertEqual(got, {"u000": "account_001"})

    def test_8b_each_greedy_order_strands_a_plan_the_exact_one_places(self):
        """Neither online order is superior: most-headroom spreads the small
        searches and fragments both accounts; best-fit packs them where a
        large one was needed. The exact allocator places both plans whole."""
        most = lambda fits, left: max(fits, key=lambda k: left[k])      # noqa: E731
        best = lambda fits, left: min(fits, key=lambda k: left[k])      # noqa: E731
        for u, accounts, loser in (
                (units("0.046", "0.046", "0.135"), caps("0.092", "0.135"), most),
                (units("0.046", "0.046", "0.046", "0.135", "0.135"),
                 caps("0.181", "0.227"), best)):
            winner = best if loser is most else most
            self.assertLess(len(greedy(u, accounts, loser)), len(u))
            self.assertEqual(len(greedy(u, accounts, winner)), len(u))
            got = scraper.place_units(u, accounts)
            self.assertEqual(len(got), len(u))
            self.check(u, accounts, got)

    def test_9_the_c5_plan_across_fresh_and_fragmented_accounts(self):
        u = units(*["0.046"] * 18, *["0.135"] * 72)
        fresh = caps(*[Decimal("5") - BUFFER] * 3)
        self.assertEqual(len(scraper.place_units(u, fresh)), 90)
        c5 = [Decimal(h) - BUFFER for h in ("4.560", "3.407", "1.535", "0.510", "0.021")]
        got = scraper.place_units(u, caps(*c5))
        self.assertLess(len(got), 90)                 # 10.00 usable < 10.548
        self.assertEqual(list(got), [x for x, _ in u[:len(got)]])   # a plan prefix
        self.check(u, caps(*c5), got)

    def test_exact_against_exhaustive_search_no_false_negative(self):
        """Mutation J's test too: any allocator that strands a placeable plan
        fails here."""
        rng = random.Random(45)
        for _ in range(250):
            n = rng.randint(1, 7)
            ceilings = [rng.choice((46, 135, 90)) for _ in range(n)]
            capacities = [rng.randint(0, 300) for _ in range(rng.randint(1, 4))]
            u = units(*[str(Decimal(c) / 1000) for c in ceilings])
            a = caps(*[str(Decimal(c) / 1000) for c in capacities])
            got = scraper.place_units(u, a)
            self.check(u, a, got)
            self.assertEqual(len(got) == n, brute_feasible(ceilings, capacities),
                             (ceilings, capacities))
            longest = max(k for k in range(n + 1)
                          if brute_feasible(ceilings[:k], capacities))
            self.assertEqual(len(got), longest, (ceilings, capacities))

    def test_deterministic(self):
        u = units(*["0.046"] * 18, *["0.135"] * 72)
        a = caps(4.99, 3.2, 2.9, 0.5)
        self.assertEqual(scraper.place_units(u, a), scraper.place_units(u, a))

    def test_the_projection_reports_leftover_and_stranded(self):
        accounts, _ = discover([Acct("u1", capacity="0.160"), Acct("u2", capacity="0.100")])
        plan = [("paid_000", "indeed", IN), ("paid_001", "linkedin", LI)]
        placed, report = scraper.project_assignment(plan, accounts)
        self.assertTrue(report["allocation_feasible"])
        rows = {r["account"]: r for r in report["accounts"]}
        self.assertEqual(rows["account_001"]["projected_units"], {"linkedin": 1})
        self.assertEqual(rows["account_000"]["projected_units"], {"indeed": 1})
        self.assertEqual(Decimal(rows["account_000"]["leftover_usd"]), Decimal("0.025"))
        self.assertEqual(Decimal(report["stranded_usd"]), 0)       # all placed: unused
        self.assertEqual(Decimal(report["bounded_exposure_usd"]), IN + LI)
        # One more Indeed search has nowhere to go: 0.025 + 0.054 are stranded.
        _, report = scraper.project_assignment(plan + [("paid_002", "indeed", IN)],
                                               accounts)
        self.assertEqual((report["placed_units"], report["allocation_feasible"]),
                         (2, False))
        self.assertEqual(Decimal(report["stranded_usd"]), Decimal("0.079"))


# ===========================================================================
# 4. Execution: assignment, both budgets, runtime capacity
# ===========================================================================
def three_accounts():
    """Room for 2, 1 and 3 LinkedIn searches: 6 in all, none alone."""
    return [Acct("uA", capacity="0.092"), Acct("uB", capacity="0.046"),
            Acct("uC", capacity="0.138")]


class Execution(unittest.TestCase):

    def test_every_start_carries_its_assigned_accounts_token_and_ceiling(self):
        """Mutations E and F's test: the account is the coordinator's, fixed
        before the worker exists, and the worker uses exactly it."""
        for workers in (1, 2, 3, 4):
            got, _ = pooled(c2.staggered([c2.solo(u) for u in range(6)]),
                            three_accounts(), keywords=KW[:6], workers=workers)
            self.assertEqual(len(got.client.kinds("start")), 6, workers)
            self.assertEqual(owners(got, None), assigned(got), workers)
            self.assertEqual(section(got)["placed_units"], 6)
            for c in got.client.kinds("start"):
                self.assertEqual(c[3], LI)

    def test_a_worker_begins_with_both_ceilings_pending_and_its_accounts_client(self):
        seen, real = [], scraper._paid_worker

        def spy(entry, make_client, holds, results, hooks=None):
            # Never raises: a spy that dies in its thread would hang the sweep.
            seen.append((entry.unit_id, holds.exposure.units[entry.unit_id]["state"],
                         holds.acct.exposure.units[entry.unit_id]["state"],
                         holds.acct.label,
                         getattr(make_client, "__self__", None) is holds.acct))
            return real(entry, make_client, holds, results, hooks)
        got, _ = pooled(li_units(3), three_accounts(), keywords=KW[:3], workers=2,
                        patches=[mock.patch.object(scraper, "_paid_worker", spy)])
        self.assertEqual(len(seen), 3)
        for unit_id, glob_state, acct_state, label, own in seen:
            self.assertEqual((glob_state, acct_state, own), ("pending", "pending", True))
            self.assertEqual(assigned(got)[unit_id], label)

    def test_per_account_committed_never_exceeds_its_capacity(self):
        for workers in (1, 2, 3, 4):
            got, accounts = pooled(li_units(6), three_accounts(), keywords=KW[:6],
                                   workers=workers)
            by = Counter(owners(got, accounts).values())
            self.assertEqual(by, Counter({"account_000": 2, "account_001": 1,
                                          "account_002": 3}), workers)
            for row in section(got)["accounts"]:
                self.assertLessEqual(Decimal(row["committed_usd"]),
                                     Decimal(row["capacity_usd"]))
                self.assertEqual(Decimal(row["committed_usd"]),
                                 by[row["account"]] * LI)

    def test_the_sweep_budget_still_binds_however_much_the_pool_holds(self):
        """Mutation H's test: $49.90 across ten accounts, a $0.10 sweep."""
        got, _ = pooled(li_units(3), [Acct(f"u{i}") for i in range(10)],
                        keywords=KW[:3], budget=0.10, workers=4)
        self.assertEqual(len(got.client.kinds("start")), 2)
        ex = got.execution["exposure"]
        self.assertEqual(Decimal(ex["committed_usd"]), 2 * LI)
        self.assertLessEqual(Decimal(ex["committed_usd"]) + Decimal(ex["pending_usd"]),
                             Decimal("0.10"))
        self.assertEqual(got.status(), ["completed", "completed", "skipped_budget"])
        self.assertIn("spend cap $0.10", got.log)

    def test_simultaneous_reservations_on_one_account_never_overcommit_it(self):
        accounts, _ = discover([Acct("u1", capacity="0.092")])
        a, wins = accounts[0], []
        barrier = threading.Barrier(8)

        def race(i):
            barrier.wait()
            if a.exposure.reserve(f"u{i}", LI):
                wins.append(i)
        threads = [threading.Thread(target=race, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(wins), 2)
        self.assertEqual(a.exposure.pending, 2 * LI)

    def test_committed_is_never_released_whatever_a_run_cost(self):
        """Mutations C and G's test: a $0 run, a $0 account delta, success —
        each account keeps every ceiling it committed; nothing reopens."""
        got, _ = pooled([Script(c2.solo(u), usage=0.0) for u in range(6)],
                        three_accounts(), keywords=KW[:6], workers=4)
        for row in section(got)["accounts"]:
            self.assertEqual(Decimal(row["remaining_usd"]),
                             Decimal(row["capacity_usd"]) - Decimal(row["committed_usd"]))
        self.assertEqual(Decimal(got.execution["exposure"]["committed_usd"]), 6 * LI)
        # A seventh search has nowhere to go, although every run cost nothing.
        got7, _ = pooled([Script(c2.solo(u), usage=0.0) for u in range(6)]
                         + [Script(c2.solo(6))], three_accounts(),
                         keywords=(*KW, "K6 Engineer"), workers=4)
        self.assertEqual(len(got7.client.kinds("start")), 6)
        self.assertEqual(got7.status()[-1], "skipped_account")

    def test_a_busy_account_waits_it_is_not_a_budget_failure(self):
        """Memory for one 4096 MB run: three Indeed searches on it run one at
        a time at two workers, and all complete."""
        got, accounts = pooled(indeed_units(3, polls=3), [Acct("u1", mem_gb=4)],
                               keywords=KW[:3], sites=("indeed",), workers=2)
        self.assertEqual(got.status(), ["completed"] * 3)
        self.assertEqual(got.client.peak_runs[SECRETS[0]], 1)
        self.assertEqual(got.client.attempts("refused"), [])
        self.assertEqual(section(got)["accounts"][0]["peak_memory_mb"], 4096)

    def test_memory_is_counted_per_account_at_the_provider(self):
        """Mutation I's test: two accounts with room for one Indeed run each,
        four workers — never two runs on one account at once."""
        got, accounts = pooled(indeed_units(4, polls=3),
                               [Acct("u1", mem_gb=4, capacity="0.270"),
                                Acct("u2", mem_gb=4, capacity="0.270")],
                               keywords=KW[:4], sites=("indeed",), workers=4)
        self.assertEqual(got.status(), ["completed"] * 4)
        self.assertEqual(got.client.attempts("refused"), [])
        for token in accounts:
            self.assertLessEqual(got.client.peak_mb[token], 4096)

    def test_an_account_that_can_never_fit_the_run_stops_the_phase(self):
        got, _ = pooled(indeed_units(2), [Acct("u1", mem_gb=2)], keywords=KW[:2],
                        sites=("indeed",), workers=2)
        self.assertEqual(got.client.kinds("start"), [])
        self.assertEqual(got.status(), ["skipped_account"] * 2)
        self.assertIn("account_resources", got.log)

    def test_unplaced_searches_stop_at_the_first_one_as_the_cap_does(self):
        got, _ = pooled(li_units(4), [Acct("u1", capacity="0.092")], keywords=KW[:4])
        self.assertEqual(len(got.client.kinds("start")), 2)
        self.assertEqual(got.status(), ["completed", "completed", "skipped_account",
                                        "skipped_account"])
        self.assertFalse(section(got)["allocation_feasible"])

    def test_no_batching_one_logical_search_one_start(self):
        got, _ = pooled(adaptive.mixed(), [Acct("u1"), Acct("u2")],
                        keywords=adaptive.KW[:2], site_locations=adaptive.PLACES,
                        sites=adaptive.LI_IN, workers=2)
        self.assertEqual(len(got.client.kinds("start")), 6)
        self.assertEqual(len({json.dumps(c[2], sort_keys=True)
                              for c in got.client.kinds("start")}), 6)

    def test_the_section_says_what_was_planned_and_what_ran(self):
        got, _ = pooled(li_units(6), three_accounts(), keywords=KW[:6], workers=2)
        s = section(got)
        self.assertEqual((s["account_count"], s["accounts_used"], s["placed_units"],
                          s["bounded_units"], s["allocation_feasible"]),
                         (3, 3, 6, 6, True))
        self.assertEqual(s["actor_memory_mb"], {"linkedin": 512})
        self.assertGreaterEqual(s["account_switches"], 2)
        for row in s["accounts"]:
            self.assertEqual(row["starts"], sum(row["projected_units"].values()))
            self.assertEqual(row["runtime_held_unknown"], 0)
        self.assertEqual(telemetry.PAID_EXECUTION_SCHEMA,
                         got.execution["schema"])


# ===========================================================================
# 5. Failures
# ===========================================================================
class Failures(unittest.TestCase):

    def one(self, script, accts=None, **kw):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        got, accounts = pooled([Script(c2.solo(0)), script, Script(c2.solo(2))],
                               accts or three_accounts(), keywords=KW[:3], workers=1,
                               out=out, **kw)
        return got, accounts, ledger_of(out)

    def rows(self, got):
        return {r["account"]: r for r in section(got)["accounts"]}

    def unit(self, got, unit_id="paid_001"):
        return next(u for u in got.execution["units"] if u["unit_id"] == unit_id)

    def test_pre_network_local_failure_releases_everything(self):
        with mock.patch.object(scraper, "charge_ceiling_supported",
                               side_effect=[True, False, True]):
            got, _, ledger = self.one(Script(c2.solo(1)))
        u = self.unit(got)
        self.assertEqual((u["reservation"], u["runtime_freed"]),
                         ("released_before_network", True))
        self.assertEqual(ledger["units"]["paid_001"]["state"], "released_before_network")
        self.assertEqual(Decimal(got.execution["exposure"]["committed_usd"]), 2 * LI)
        self.assertEqual(sum(Decimal(r["committed_usd"]) for r in self.rows(got).values()),
                         2 * LI)

    def test_an_ambiguous_start_keeps_both_ceilings_and_is_never_retried(self):
        """Mutation N's test."""
        got, _, ledger = self.one(Script(start_error=ConnectionError("reset")))
        u = self.unit(got)
        self.assertEqual((u["reservation"], u["runtime_freed"]), ("committed", False))
        self.assertEqual(ledger["units"]["paid_001"]["state"], "committed")
        self.assertNotIn("run_id", ledger["units"]["paid_001"])
        starts = Counter(json.dumps(c[2], sort_keys=True) for c in got.client.kinds("start"))
        self.assertEqual(set(starts.values()), {1})           # nothing ran twice
        self.assertEqual(Decimal(got.execution["exposure"]["committed_usd"]), 3 * LI)
        acct = self.rows(got)[u["account"]]
        self.assertEqual(Decimal(acct["committed_usd"]),
                         Counter(assigned(got).values())[u["account"]] * LI)
        self.assertEqual(acct["runtime_held_unknown"], 1)
        self.assertEqual(got.status(), ["completed", "failed", "completed"])

    def test_provider_failed_timed_out_aborted_keep_money_free_runtime(self):
        """Mutation D's test."""
        for status in ("FAILED", "TIMED-OUT", "ABORTED"):
            got, _, ledger = self.one(Script(status=status))
            u = self.unit(got)
            self.assertEqual((u["reservation"], u["provider_status"], u["runtime_freed"]),
                             ("committed", status, True), status)
            self.assertEqual(ledger["units"]["paid_001"]["provider_status"], status)
            self.assertEqual(Decimal(got.execution["exposure"]["committed_usd"]), 3 * LI)
            self.assertEqual(sum(r["runtime_held_unknown"]
                                 for r in self.rows(got).values()), 0)

    def test_a_dataset_failure_after_the_run_keeps_its_ceiling(self):
        script = Script(c2.solo(1))
        script.dataset_error = OSError("dataset read failed")
        got, _, _ = self.one(script)
        u = self.unit(got)
        self.assertEqual((u["reservation"], u["provider_status"], u["runtime_freed"]),
                         ("committed", "SUCCEEDED", True))
        self.assertEqual(got.status(), ["completed", "failed", "completed"])

    def test_an_account_credit_refusal_stops_and_retries_nothing(self):
        accts = [Acct("uA", capacity="0.046"),
                 Acct("uB", capacity="0.046",
                      refuse=RuntimeError("Monthly usage hard limit exceeded")),
                 Acct("uC", capacity="0.046")]
        got, _ = pooled(li_units(3), accts, keywords=KW[:3], workers=1)
        u = self.unit(got)
        self.assertEqual((u["account"], u["account_refusal"], u["reservation"]),
                         ("account_001", "ACCOUNT_CREDIT", "committed"))
        self.assertEqual(len(got.client.attempts("refused")), 1)
        self.assertEqual(len(got.client.kinds("start")), 1)     # paid_000 only
        self.assertEqual(got.status(), ["completed", "failed", "skipped_account"])
        self.assertIn("ACCOUNT_CREDIT", got.log)
        rows = self.rows(got)
        self.assertEqual(Decimal(rows["account_001"]["committed_usd"]), LI)
        self.assertEqual(rows["account_002"]["starts"], 0)

    def test_a_provider_resource_refusal_is_account_resource(self):
        """The pool's model was wrong (someone else's run took the memory):
        the provider says no, the phase stops, the ceiling stays."""
        accts = [Acct("uA", capacity="0.046", active=5)]
        with mock.patch.object(scraper.PoolAccount, "__init__",
                               _with_slots(scraper.PoolAccount.__init__, 5)):
            got, _ = pooled(li_units(2), accts, keywords=KW[:2], workers=1)
        u = self.unit(got, "paid_000")
        self.assertEqual(u["account_refusal"], "ACCOUNT_RESOURCE")
        self.assertEqual(got.status(), ["failed", "skipped_account"])

    def test_an_account_read_failure_after_a_run_falls_back_as_c2_does(self):
        with mock.patch.object(scraper, "account_usage_usd", return_value=None):
            got, _, _ = self.one(Script(c2.solo(1)))
        self.assertEqual(got.status(), ["completed"] * 3)
        bases = {u["budget_basis"] for u in got.telemetry["units"] if u.get("path") == "paid"}
        self.assertEqual(bases, {"run_record_sum"})


def _with_slots(init, slots):
    """PoolAccount.__init__ that believes it has `slots` run slots."""
    def patched(self, *a, **kw):
        init(self, *a, **kw)
        self.run_slots = slots
    return patched


# ===========================================================================
# 6. Output parity: the account is invisible to the result
# ===========================================================================
def untimed(value):
    if isinstance(value, dict):
        return {k: untimed(v) for k, v in value.items()
                if not re.search(r"(_ms|_at|_s)$|_ms_", k)}
    if isinstance(value, list):
        return [untimed(v) for v in value]
    return value


# 4 LinkedIn + 2 Indeed = $0.454 of ceilings, exactly what the three hold.
PARITY_ACCOUNTS = [Acct("uA", capacity="0.092"), Acct("uB", capacity="0.135"),
                   Acct("uC", capacity="0.227")]


class OutputParity(unittest.TestCase):
    """Mutation K's test: single-account C4 against the pool over three
    accounts, the same scripted provider, the first planned search finishing
    last — byte-identical results, ledgers and shadow decisions."""

    @classmethod
    def setUpClass(cls):
        def args(workers):
            return dict(keywords=adaptive.KW[:2], site_locations=adaptive.PLACES,
                        sites=adaptive.LI_IN, workers=workers, free=True,
                        env={adaptive.MODE: "shadow"})
        cls.runs = {}
        for w in (1, 2, 4):
            cls.runs[w] = (c2.c2_sweep(adaptive.mixed(), account=[1.0], **args(w)),
                           pooled(adaptive.mixed(), PARITY_ACCOUNTS, **args(w))[0])

    def test_byte_identical_at_every_worker_count(self):
        for w, (single, multi) in self.runs.items():
            self.assertEqual(single.csv, multi.csv, w)
            self.assertEqual(single.json, multi.json, w)
            self.assertEqual(single.seen, multi.seen, w)
            self.assertEqual(single.done, multi.done, w)

    def test_the_searches_really_ran_on_several_accounts(self):
        for w, (_, multi) in self.runs.items():
            self.assertEqual(len(set(assigned(multi).values())), 3, w)

    def test_the_same_starts_inputs_and_ceilings(self):
        for w, (single, multi) in self.runs.items():
            self.assertEqual(adaptive.starts(single), adaptive.starts(multi), w)

    def test_the_same_shadow_decisions_and_losses(self):
        for w, (single, multi) in self.runs.items():
            a, b = (untimed(r.telemetry["paid_adaptive"]) for r in (single, multi))
            self.assertEqual(a, b, w)

    def test_unit_identity_and_funnels_are_the_accounts_business_not(self):
        for w, (single, multi) in self.runs.items():
            keep = ("unit_id", "plan_index", "provider", "keyword_fp", "status",
                    "funnel", "trace", "provider_positions")
            for a, b in zip(single.telemetry["paid_units"], multi.telemetry["paid_units"]):
                self.assertEqual({k: a.get(k) for k in keep}, {k: b.get(k) for k in keep})


class DuplicateTie(unittest.TestCase):

    def test_the_first_planned_twin_wins_on_another_account_finishing_last(self):
        items = [[c2.twin("t1"), *c2.solo(0, 1)], [c2.twin("t1"), *c2.solo(1, 1)]]
        scripts = c2.staggered(items, slow_first=True)
        single = c2.c2_sweep(scripts, keywords=KW[:2], workers=2, account=[1.0])
        multi, _ = pooled(c2.staggered(items, slow_first=True),
                          [Acct("uA", capacity="0.046"), Acct("uC", capacity="0.050")],
                          keywords=KW[:2], workers=2)
        self.assertNotEqual(assigned(multi)["paid_000"], assigned(multi)["paid_001"])
        self.assertEqual(multi.client.order("dataset")[0], "run_2")   # finished first
        twin = [r for r in multi.rows if r["company"] == "Twin Works"]
        self.assertEqual([r["search_query"] for r in twin], [c2.label(0)])
        self.assertEqual((single.csv, single.json), (multi.csv, multi.json))


# ===========================================================================
# 7. Crashes and the durable ledger
# ===========================================================================
class Dies:
    """The process dies at one ledger transition: the write that names it
    lands (or, `before`, does not) and none after it — as a SIGKILL leaves the
    file — then Crash. Other threads may run on; their writes are lost."""

    def __init__(self, when, before=False):
        self.when, self.before, self.dead = when, before, False
        self.real_set, self.real_write = scraper.AccountLedger.set, \
            scraper.AccountLedger._write

    def patches(self):
        dies = self

        def write(ledger):
            if not dies.dead:
                dies.real_write(ledger)

        def set_(ledger, unit_id, **fields):
            hit = not dies.dead and dies.when(unit_id, fields)
            if hit and dies.before:
                dies.dead = True
                raise c2.Crash("before the write")
            dies.real_set(ledger, unit_id, **fields)
            if hit:
                dies.dead = True
                raise c2.Crash("after the write")
        return [mock.patch.object(scraper.AccountLedger, "_write", write),
                mock.patch.object(scraper.AccountLedger, "set", set_)]


def at(key, value, unit="paid_001"):
    return lambda unit_id, fields: unit_id == unit and fields.get(key) == value


class Crashes(unittest.TestCase):
    """A-G. The rule: an unfinished ledger with a committed start STOPS the
    next sweep for the operator; anything less proceeds, archived."""

    def crash_then_rerun(self, dies=None, extra=()):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        first, _ = pooled(li_units(3, polls=2), three_accounts(), keywords=KW[:3],
                          workers=1, out=out, patches=[*(dies.patches() if dies else ()),
                                                       *extra])
        self.assertIsInstance(first.error, c2.Crash)
        c2._join_workers()
        ledger = ledger_of(out)
        again, _ = pooled(li_units(3), three_accounts(), keywords=KW[:3], workers=1,
                          out=out)
        return first, ledger, again, out

    def assertStopped(self, again):
        self.assertIn("Refusing to start", again.log)
        self.assertEqual(again.client.kinds("start"), [])

    def test_a_crash_before_any_assignment_is_written_proceeds(self):
        first, ledger, again, _ = self.crash_then_rerun(
            Dies(lambda u, f: f.get("state") == "assigned", before=True))
        self.assertIsNone(ledger)
        self.assertEqual(first.client.kinds("start"), [])
        self.assertEqual(again.status(), ["completed"] * 3)

    def test_b_a_crash_after_a_pending_hold_is_written_proceeds(self):
        first, ledger, again, out = self.crash_then_rerun(
            Dies(at("state", "pending", unit="paid_000")))
        self.assertEqual(ledger["units"]["paid_000"]["state"], "pending")
        self.assertEqual(first.client.kinds("start"), [])
        self.assertNotIn("Refusing", again.log)
        self.assertEqual(again.status(), ["completed"] * 3)
        self.assertTrue([f for f in os.listdir(out)
                         if f.startswith(scraper.POOL_RECORD + ".")], "archived")

    def test_c_committed_written_start_never_sent_stops_the_next_sweep(self):
        first, ledger, again, _ = self.crash_then_rerun(Dies(at("state", "committed")))
        self.assertEqual(ledger["units"]["paid_001"]["state"], "committed")
        self.assertEqual(len(first.client.kinds("start")), 1)   # paid_001's never sent
        self.assertStopped(again)

    def test_d_started_but_no_run_id_written_stops(self):
        first, ledger, again, _ = self.crash_then_rerun(
            Dies(lambda u, f: u == "paid_001" and "run_id" in f, before=True))
        self.assertEqual(ledger["units"]["paid_001"]["state"], "committed")
        self.assertNotIn("run_id", ledger["units"]["paid_001"])
        self.assertEqual(len(first.client.kinds("start")), 2)
        self.assertStopped(again)

    def test_e_run_id_written_stops(self):
        first, ledger, again, _ = self.crash_then_rerun(
            Dies(lambda u, f: u == "paid_001" and "run_id" in f))
        self.assertEqual(ledger["units"]["paid_001"]["run_id"], "run_2")
        self.assertStopped(again)

    def test_f_provider_success_before_the_checkpoint_stops(self):
        crash = c2.CrashAt("before_checkpoint", 1)
        first, ledger, again, _ = self.crash_then_rerun(extra=crash.patches())
        self.assertEqual(ledger["units"]["paid_001"]["provider_status"], "SUCCEEDED")
        self.assertEqual(len(first.done), 1)
        self.assertStopped(again)

    def test_g_checkpoint_written_before_the_done_marker_stops(self):
        crash = c2.CrashAt("after_checkpoint", 1)
        first, ledger, again, _ = self.crash_then_rerun(extra=crash.patches())
        self.assertFalse(ledger["finished"])
        self.assertEqual(len(first.done), 1)
        self.assertStopped(again)

    def test_the_operator_moves_it_aside_and_only_the_undone_is_bought(self):
        first, _, again, out = self.crash_then_rerun(Dies(at("state", "committed")))
        os.replace(os.path.join(out, scraper.POOL_RECORD),
                   os.path.join(out, "reviewed.json"))
        third, _ = pooled(li_units(3), three_accounts(), keywords=KW[:3], workers=1,
                          out=out)
        self.assertEqual(len(third.client.kinds("start")), 3 - len(first.done))
        self.assertEqual(len(third.done), 3)
        self.assertEqual(third.done[:len(first.done)], first.done)

    def test_a_finished_ledger_is_archived_and_the_next_sweep_runs(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        one, _ = pooled(li_units(2), three_accounts(), keywords=KW[:2], out=out)
        self.assertTrue(ledger_of(out)["finished"])
        two, _ = pooled(li_units(3), three_accounts(), keywords=KW[:3], out=out)
        self.assertNotIn("Refusing", two.log)
        self.assertEqual(len(two.client.kinds("start")), 1)      # K0, K1 done today
        self.assertEqual(len([f for f in os.listdir(out)
                              if f.startswith(scraper.POOL_RECORD + ".")]), 1)

    def test_the_ledger_records_each_transition_and_no_secret(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        got, _ = pooled(li_units(3), three_accounts(), keywords=KW[:3], out=out)
        ledger = ledger_of(out)
        self.assertTrue(ledger["finished"])
        for unit_id, u in ledger["units"].items():
            self.assertEqual((u["state"], u["provider_status"], u["integrated"]),
                             ("committed", "SUCCEEDED", "completed"))
            self.assertEqual(u["account"], assigned(got)[unit_id])
            self.assertEqual(Decimal(u["ceiling_usd"]), LI)
            self.assertTrue(u["run_id"].startswith("run_"))
        text = Path(out, scraper.POOL_RECORD).read_text()
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertEqual([f for f in os.listdir(out) if f.endswith(".tmp")], [])


# ===========================================================================
# 8. Privacy and the guard
# ===========================================================================
class Privacy(unittest.TestCase):

    def test_no_token_value_reaches_any_artifact(self):
        """Mutation L's test: stdout, stderr, telemetry, CSV, JSON, seen, done,
        the durable ledger — even when an account's error quotes its token."""
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        accts = [*three_accounts(), Acct("uX", unreadable=True)]
        got, _ = pooled([Script(c2.solo(0)), Script(start_error=ConnectionError(
            "reset")), *li_units(4)[2:]], accts,
            keywords=KW[:4], workers=2, out=out)
        bodies = [got.log.encode(), got.telemetry_text.encode(),
                  *tree_bytes(out).values()]
        self.assertIn(b"unreadable (RuntimeError)", got.telemetry_text.encode())
        for secret in SECRETS:
            for body in bodies:
                self.assertNotIn(secret.encode(), body)


class Guard(unittest.TestCase):

    def test_the_pool_credential_step_is_guarded_and_restored(self):
        """A developer run whose plan turned paid after the check cannot reach
        the pool's tokens, with or without the keys."""
        for keys, env in (([], {}), (["--allow-paid"], c0.BOTH)):
            with mock.patch.object(paid_guard, "paid_plan", return_value={}):
                got = c0.engine([*keys, "--", "--yes"], env={
                    **env, scraper.PAID_CONCURRENCY_FLAG: "1", FLAG: "1"})
            self.assertTrue(got.blocked, got.text)
            self.assertIn("credential step", got.text)
            self.assertEqual((got.rec.tokens, got.rec.starts), ([], []))
        self.assertEqual(scraper._require_token_pool.__name__, "_require_token_pool")

    def test_the_guard_limit_is_global_whatever_the_pool_holds(self):
        plan = {"linkedin": {"actor": c2.LINKEDIN, "starts": 18, "depth": 15,
                             "ceiling_usd": LI},
                "indeed": {"actor": c2.INDEED, "starts": 72, "depth": 15,
                           "ceiling_usd": IN}}
        self.assertEqual(paid_guard.worst_case(plan), Decimal("10.548"))
        with self.assertRaises(paid_guard.PaidBenchBlocked):
            paid_guard.authorize(plan, True, Decimal("11.32"), Decimal("0.773"),
                                 env={paid_guard.FLAG: "1"})
        paid_guard.authorize(plan, True, Decimal("11.33"), Decimal("0.773"),
                             env={paid_guard.FLAG: "1"})


# ===========================================================================
# 9. The guarded probe, extended for C5 (V2-C4.5)
# ===========================================================================
from bench import search_v2_paid_probe as probe       # noqa: E402


def probe_args(**over):
    return SimpleNamespace(**{**dict(
        site="linkedin", searches=1, keywords="Backend Developer", location="India",
        case=None, scope="india", allow_paid=False, max_usd="11.33",
        exposed_usd="0.773", ledger=str(probe.LEDGER), paid_workers=2,
        sweep_budget="10.55", stage="C5", full_plan=True, adaptive_mode="shadow",
        multi_account=True, keep_output=None), **over})


class C5Probe(unittest.TestCase):

    def test_the_full_plan_is_the_profiles_whole_plan_behind_both_keys(self):
        argv = probe.guard_argv(probe_args(), "p")
        self.assertEqual(argv[argv.index("--") + 1:], ["--profile", "p", "--yes"])
        self.assertNotIn("--allow-paid", argv)
        self.assertEqual(argv[argv.index("--max-usd") + 1], "11.33")
        self.assertEqual(argv[argv.index("--exposed-usd") + 1], "0.773")
        self.assertIn("--allow-paid", probe.guard_argv(probe_args(allow_paid=True), "p"))

    def test_the_full_plan_profile_changes_only_the_cap_and_the_outputs(self):
        text = probe.profile_source(probe_args(), "p", Path("w"))
        self.assertIn("SETTINGS = {'max_spend_usd': 10.55, 'output_dir': 'w'}", text)
        for name in ("SITES", "ATS_BOARDS", "FEEDS", "SEARCH", "SCORING"):
            self.assertNotIn(f"{name} =", text)

    def test_the_modes_are_the_probes_to_set_never_a_key(self):
        shell = {probe.ADAPTIVE_MODE: "enforce", probe.MULTI_ACCOUNT: "1",
                 paid_guard.FLAG: None}
        with b3._env(**shell):
            on = probe.child_env(probe_args(), "p")
            off = probe.child_env(probe_args(adaptive_mode=None, multi_account=False,
                                             full_plan=False), "p")
        self.assertEqual((on[probe.ADAPTIVE_MODE], on[probe.MULTI_ACCOUNT]), ("shadow", "1"))
        self.assertEqual({k: on[k] for k in probe.PRODUCTION_FREE_FLAGS},
                         probe.PRODUCTION_FREE_FLAGS)
        self.assertEqual((on[scraper.PAID_CONCURRENCY_FLAG], on[scraper.PAID_WORKERS_ENV]),
                         ("1", "2"))
        for key in (probe.ADAPTIVE_MODE, probe.MULTI_ACCOUNT):
            self.assertNotIn(key, off)
        for env in (on, off):
            self.assertNotIn(paid_guard.FLAG, env)

    def test_c5_has_its_own_ceiling_and_nothing_else_does(self):
        ledger = probe.load_ledger()
        c5 = ledger["separate_budgets"]["C5"]
        self.assertEqual(Decimal(c5["ceiling_usd"]),
                         (Decimal(ledger["entries"][-1]["cumulative_intended_usd"])
                          + Decimal("10.548")).quantize(Decimal("0.01"),
                                                        rounding="ROUND_CEILING"))
        self.assertIsNone(probe.ledger_blocks(ledger, "0.773", "11.33", "C5"))
        self.assertIn("exceeds stage C5", probe.ledger_blocks(ledger, "0.773", "11.34",
                                                              "C5"))
        self.assertIn("no research budget for stage C4.5",
                      probe.ledger_blocks(ledger, "0.773", "0.10", "C4.5"))
        self.assertIn("shared", probe.ledger_blocks(ledger, "0.773", "2.01", "C4"))
        self.assertIn("shared", probe.ledger_blocks(ledger, "0.773", "2.01"))

    def test_the_preview_plan_is_read_back_exactly(self):
        plan = {"linkedin": {"actor": c2.LINKEDIN, "starts": 18, "depth": 15,
                             "ceiling_usd": LI},
                "indeed": {"actor": c2.INDEED, "starts": 72, "depth": 15,
                           "ceiling_usd": IN},
                "naukri": {"actor": "x/y", "starts": 2, "depth": 50, "ceiling_usd": None}}
        got = probe.preview_plan(paid_guard.preflight(plan, Decimal("11.33"),
                                                      Decimal("0.773")))
        self.assertEqual(got, {"linkedin": (18, 15, LI), "indeed": (72, 15, IN),
                               "naukri": (2, 50, None)})
        self.assertEqual(list(got), ["linkedin", "indeed", "naukri"])

    def test_the_preflight_client_cannot_start_anything(self):
        client = probe.ReadOnlyClient("not-a-token")
        self.assertEqual(sorted(vars(client.actor("a/b"))), ["get"])
        self.assertEqual(sorted(vars(client.user())), ["get", "limits"])

    def preflight(self, accts, **over):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, True)
        accounts = {SECRETS[i]: a for i, a in enumerate(accts)}
        provider = PoolClient({}, accounts=accounts)
        plan = {"linkedin": {"actor": c2.LINKEDIN, "starts": 18, "depth": 15,
                             "ceiling_usd": LI},
                "indeed": {"actor": c2.INDEED, "starts": 72, "depth": 15,
                           "ceiling_usd": IN}}
        text = paid_guard.preflight(plan, Decimal("11.33"), Decimal("0.773")) + \
            f"\n{paid_guard.BLOCKED} Missing: --allow-paid and {paid_guard.FLAG}=1.\n"
        child = SimpleNamespace(returncode=1, stdout=text, stderr="")
        args = probe_args(output=str(Path(out) / "pre.json"), **over)
        with self.fake_root(), \
                mock.patch.object(probe.subprocess, "run", return_value=child) as run, \
                mock.patch.object(scraper, "pool_tokens",
                                  return_value=[(slot(i), t) for i, t in
                                                enumerate(accounts)]), \
                mock.patch.object(probe, "ReadOnlyClient", provider), \
                mock.patch("dotenv.load_dotenv", lambda *a, **k: None), \
                contextlib.redirect_stdout(io.StringIO()):
            got = probe.preflight(args)
        child_call = run.call_args_list[0]          # then git, for the revision
        argv, env = child_call.args[0], child_call.kwargs["env"]
        return got, Path(args.output).read_text(), provider, argv, env

    def test_the_preflight_places_c5_on_three_fresh_accounts_and_spends_nothing(self):
        got, text, provider, argv, env = self.preflight(
            [Acct(f"u{i}", headroom=4.95) for i in range(3)])
        self.assertTrue(got["ready_for_c5_review"], got["checks"])
        self.assertEqual((got["logical_searches"], got["bounded_exposure_usd"]),
                         (90, "10.548"))
        self.assertEqual(got["pool"]["placed_units"], 90)
        self.assertEqual(provider.attempts("start"), [])
        self.assertEqual(provider.attempts("refused"), [])
        self.assertNotIn("--allow-paid", argv)
        self.assertNotIn(paid_guard.FLAG, env)
        self.assertFalse(got["single_account"]["holds_cap_plus_0_10"])
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def test_the_preflight_fails_when_the_pool_cannot_place_the_plan(self):
        got, *_ = self.preflight([Acct("u0", headroom=4.5609), Acct("u1", headroom=3.4078),
                                  Acct("u2", headroom=1.5353)])
        self.assertFalse(got["checks"]["pool_places_every_bounded_search"])
        self.assertFalse(got["ready_for_c5_review"])

    def test_the_preflight_fails_when_an_account_cannot_run_two_at_once(self):
        got, *_ = self.preflight([Acct(f"u{i}", headroom=4.95, mem_gb=6) for i in range(3)])
        self.assertTrue(got["checks"]["pool_places_every_bounded_search"])
        self.assertFalse(got["checks"]["placed_accounts_support_workers"])

    def test_the_preflight_refuses_the_key(self):
        with self.assertRaises(SystemExit):
            probe.preflight(probe_args(allow_paid=True, output="x"))

    def fake_root(self):
        """The probe writes its temporary profile under ROOT/profiles: here, a
        scratch root, never the repository's (test_app forbids that)."""
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "profiles").mkdir()
        return mock.patch.object(probe, "ROOT", root)

    def test_kept_output_is_moved_not_deleted(self):
        work, keep = tempfile.mkdtemp(), tempfile.mkdtemp()
        shutil.rmtree(keep)
        self.addCleanup(shutil.rmtree, keep, True)
        Path(work, "jobs_x.csv").write_text("kept")
        child = SimpleNamespace(returncode=1)
        with self.fake_root(), \
                mock.patch.object(probe.tempfile, "mkdtemp", return_value=work), \
                mock.patch.object(probe.subprocess, "run", return_value=child), \
                mock.patch.object(probe, "profile_source", return_value="x = 1\n"):
            with self.assertRaises(SystemExit):
                probe.probe(probe_args(keep_output=keep, output="unused.json"))
        self.assertEqual(Path(keep, "jobs_x.csv").read_text(), "kept")
        self.assertFalse(os.path.exists(work))


class Benchmark(unittest.TestCase):

    def test_the_allocator_benchmark_cannot_reach_a_real_client(self):
        source = (ROOT / "bench" / "search_v2_paid_accounts.py").read_text()
        for banned in ("apify_client", "_require_token", "engine_argv(", "APIFY_TOKEN",
                       "scrape_search(", ".start(run_input", "actor(", "scraper.main("):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
