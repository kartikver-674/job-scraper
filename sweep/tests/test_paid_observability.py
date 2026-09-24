"""Search Engine V2-C1: the paid path, observed — and unchanged by being observed.

Every planned paid search gets an id; every executed one records where its time
went, which cost readings it saw (kept apart, none final), and what its rows
became: stale, filtered, deduplicated against what, eligible, positive, final.
None of it may change what the engine runs or writes.

NO REAL CREDENTIAL AND NO REAL CLIENT. scraper.main() runs in-process against
ScriptedClient, a stand-in for apify_client.ApifyClient that logs every request
the engine makes; _require_token is patched, so no .env is read; sockets are
denied for the module (which does not reach apify-client's Rust transport — the
stand-in is what makes a real call impossible here, as in V2-C0).
"""
import contextlib
import copy
import glob
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import scraper
import telemetry
from sweep.tests import test_search_v2_shadow_eval as b3

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "apify_api_C1FIXTURE0000000000000000"       # not a real token
LINKEDIN = "curious_coder/linkedin-jobs-scraper"
TODAY = datetime.now().strftime("%Y-%m-%d")
STALE = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
RICH = b3.RICH
_sleep = time.sleep                 # real, before any test patches time.sleep

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def deny(*a, **kw):
        raise AssertionError("a C1 test tried to open a socket")
    socket.socket.connect = deny
    socket.create_connection = deny


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


# ---------------------------------------------------------------------------
# The provider, scripted
# ---------------------------------------------------------------------------
def li(job_id, title, company, desc, location="Remote", posted=TODAY):
    """One LinkedIn dataset item, shaped as the actor returns it."""
    return {"title": title, "companyName": company, "location": location,
            "descriptionText": desc, "postedAt": posted,
            "link": f"https://www.linkedin.com/jobs/view/{job_id}"}


class Script:
    """What the provider does with the next actor start."""

    def __init__(self, items=(), status="SUCCEEDED", usage=0.03, events=None,
                 polls=1, run_time=30.0, start_error=None, delay=None):
        self.items, self.status, self.usage = list(items), status, usage
        self.events = events if events is not None else {
            "apify-default-dataset-item": len(self.items), "apify-actor-start": 1}
        self.polls, self.run_time, self.start_error = polls, run_time, start_error
        self.delay = delay or {}        # real seconds per request kind


class ScriptedClient:
    """apify_client.ApifyClient's stand-in. Each start takes the next Script;
    every request the engine makes is appended to `calls`."""

    def __init__(self, scripts, account=None):
        self.scripts, self.account = list(scripts), list(account or [])
        self.readable = account is not None
        self.calls, self.runs = [], {}

    def __call__(self, token=None, **kw):       # ApifyClient(token)
        assert token == TOKEN, "the engine was handed something else"
        return self

    def actor(self, actor_id):
        client = self

        class Actor:
            # Declares max_total_charge_usd, as apify-client >= 3.1.0 does:
            # scrape_search introspects for it and fails closed without it.
            def start(self, *, run_input=None, run_timeout=None,
                      max_total_charge_usd=None):
                return client._start(actor_id, run_input, max_total_charge_usd)
        return Actor()

    def _start(self, actor_id, run_input, max_total_charge_usd):
        self.calls.append(("start", actor_id, copy.deepcopy(run_input),
                           max_total_charge_usd))
        script = self.scripts.pop(0)
        _sleep(script.delay.get("start", 0))
        if script.start_error:
            raise script.start_error
        run_id = f"run_{len(self.runs) + 1}"
        self.runs[run_id] = [script, 0]
        return SimpleNamespace(
            id=run_id, build_id=f"build_{run_id}", started_at="2026-09-24T00:00:00Z",
            options=SimpleNamespace(max_total_charge_usd=(
                None if max_total_charge_usd is None else float(max_total_charge_usd))))

    def run(self, run_id):
        return SimpleNamespace(get=lambda: self._get(run_id),
                               abort=lambda: self.calls.append(("abort", run_id)))

    def _get(self, run_id):
        self.calls.append(("get", run_id))
        state = self.runs[run_id]
        script = state[0]
        state[1] += 1
        _sleep(script.delay.get("get", 0))
        done = state[1] >= script.polls
        return SimpleNamespace(
            id=run_id, status=script.status if done else "RUNNING",
            finished_at="2026-09-24T00:00:30Z" if done else None,
            default_dataset_id=f"ds_{run_id}", usage_total_usd=script.usage,
            charged_event_counts=dict(script.events), build_number="1.7.17",
            stats=SimpleNamespace(run_time_secs=script.run_time))

    def dataset(self, dataset_id):
        run_id = dataset_id[len("ds_"):]

        def items():
            self.calls.append(("dataset", dataset_id))
            script = self.runs[run_id][0]
            _sleep(script.delay.get("dataset", 0))
            yield from copy.deepcopy(script.items)
        return SimpleNamespace(iterate_items=items)

    def user(self):
        def limits():
            self.calls.append(("limits",))
            if not self.readable:
                raise RuntimeError("account unreadable")
            usage = self.account.pop(0)
            return SimpleNamespace(model_dump=lambda: {"current": {
                "monthly_usage_usd": usage}})
        return SimpleNamespace(limits=limits)


# ---------------------------------------------------------------------------
# One sweep through scraper.main()
# ---------------------------------------------------------------------------
FREE_REGISTRY = {"lever": {"betalever": "Beta"}}
FREE_BOARDS = {"lever:betalever": [b3.lever("fb1", "Backend Engineer", RICH)]}


class Swept(b3.Result):
    pass


def paid_sweep(scripts, *, on=True, keywords=("Alpha Engineer",),
               locations=("Bengaluru",), sites=("linkedin",), account=None,
               budget=None, free=False, done=(), env=None, patches=()):
    """A paid (or paid + free) sweep, offline. Returns what is left on disk,
    the telemetry record, and every request the engine made."""
    out = tempfile.mkdtemp()
    client = ScriptedClient(scripts, account)
    saved = (sys.argv, scraper.SITES, scraper.ATS_BOARDS, scraper.FEEDS,
             dict(scraper.SETTINGS))
    try:
        scraper.SITES = {k: dict(v, enabled=k in sites, locations=list(locations))
                         for k, v in copy.deepcopy(saved[1]).items()}
        scraper.ATS_BOARDS = copy.deepcopy(FREE_REGISTRY) if free else {}
        scraper.FEEDS = {}
        scraper.SETTINGS.update(output_dir=out, max_spend_usd=budget)
        if done:
            with open(os.path.join(out, ".done_combos"), "w") as fh:
                fh.writelines(f"{TODAY}|{d}|\n" for d in done)
        sys.argv = (["scraper.py", "--keywords", ",".join(keywords), "--yes"]
                    + ([] if free else ["--no-free"]))
        log = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(b3._env(**dict(
                b3.BASE_ENV, **{telemetry.FLAG: "1" if on else None}, **(env or {}))))
            stack.enter_context(b3._served(b3.routes(FREE_BOARDS, {}) if free else {}))
            stack.enter_context(mock.patch.object(scraper, "_require_token",
                                                  return_value=TOKEN))
            stack.enter_context(mock.patch("apify_client.ApifyClient", client))
            stack.enter_context(mock.patch.object(scraper.time, "sleep",
                                                  lambda *_: None))
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(log))
            stack.enter_context(contextlib.redirect_stderr(log))
            try:
                scraper.main()
            except SystemExit as exc:
                log.write(f"\nSystemExit: {exc}")
        got = b3.Result(out, [], log.getvalue())
        got.client = client
        return got
    finally:
        (sys.argv, scraper.SITES, scraper.ATS_BOARDS, scraper.FEEDS) = saved[:4]
        scraper.SETTINGS.clear()
        scraper.SETTINGS.update(saved[4])
        shutil.rmtree(out, ignore_errors=True)


def units(got):
    """paid_units joined to their units[] execution record by unit_id — the
    join any reader of the record makes."""
    executed = {u["unit_id"]: u for u in got.telemetry["units"] if u.get("unit_id")}
    return {u["unit_id"]: dict(u, execution=executed.get(u["unit_id"]))
            for u in got.telemetry["paid_units"]}


def csv_rows(got):
    import csv
    return list(csv.DictReader(io.StringIO(got.csv.decode("utf-8-sig"))))


# ---------------------------------------------------------------------------
# The attribution fixture: two paid units with overlapping jobs, and free
# ---------------------------------------------------------------------------
# Unit A (paid_000, "Alpha Engineer"), by dataset position:
#   1 final, positive; the same job as B1 (tie, A arrived first)
#   2 eligible; a free Lever row holds the same job_key and scores higher
#   3 stale
#   4 not remote under the default remote-worldwide scopes: unreachable
#   5 "Staff": hard-filtered, never scored
#   6 eligible, final, score not positive
#   7 a repeat of A1 inside the same dataset (another native id, same job_key)
#   8 eligible, then beaten by B4, a higher-scored copy
# Unit B (paid_001, "Beta Engineer"):
#   1 A1 again — same native id AND same job_key: lost to A1 on arrival
#   2 new, final, only here
#   3 A1's native id RETITLED: current job_key says a different job, so it is
#   4 A8's job with a richer description: it wins
A_ITEMS = [
    li(101, "Full Stack Engineer", "Acme", RICH),
    li(102, "Backend Engineer", "Beta", "python django"),
    li(103, "Frontend Engineer", "Gamma", RICH, posted=STALE),
    li(104, "Software Engineer", "Delta", RICH, location="Bengaluru, Karnataka, India"),
    li(105, "Staff Engineer", "Epsilon", RICH),
    li(106, "Software Engineer", "Zeta", "java spring"),
    li(107, "Full Stack Engineer", "Acme", RICH),
    li(108, "Frontend Developer", "Iota", "react"),
]
B_ITEMS = [
    li(101, "Full Stack Engineer", "Acme", RICH),
    li(202, "Platform Engineer", "Theta", RICH),
    li(101, "Senior Full Stack Engineer", "Acme", RICH),
    li(204, "Frontend Developer", "Iota", RICH),
]
A_TRACE = "F+n. D+nf S+n. R+n. H.n. F-n. D+u. D+n."
B_TRACE = "D+p. F+n. F+n. F+p."


def attribution(on=True, **kw):
    return paid_sweep([Script(A_ITEMS, usage=0.01605), Script(B_ITEMS, usage=0.00805)],
                      on=on, keywords=("Alpha Engineer", "Beta Engineer"),
                      free=True, account=[1.0, 1.012, 1.02], **kw)


# ===========================================================================
# 1, 13, 24 — telemetry changes nothing the engine does or writes
# ===========================================================================
class BehaviourEquivalence(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.off, cls.on = attribution(on=False), attribution(on=True)

    def test_the_same_requests_in_the_same_order(self):
        self.assertEqual(self.off.client.calls, self.on.client.calls)
        kinds = [c[0] for c in self.on.client.calls]
        self.assertEqual(kinds.count("start"), 2)
        self.assertEqual(kinds.count("limits"), 3)       # baseline + one per unit

    def test_the_same_actor_input_and_ceiling(self):
        starts = [c for c in self.on.client.calls if c[0] == "start"]
        self.assertEqual(starts, [c for c in self.off.client.calls if c[0] == "start"])
        for _, actor, run_input, ceiling in starts:
            self.assertEqual(actor, LINKEDIN)
            self.assertEqual(ceiling, Decimal("0.046"))
            self.assertEqual(set(run_input),
                             {"urls", "limitPerSource", "count", "scrapeCompany"})

    def test_csv_json_and_seen_ledger_byte_identical(self):
        self.assertEqual(self.off.csv, self.on.csv)
        self.assertEqual(self.off.json, self.on.json)
        self.assertEqual(self.off.seen, self.on.seen)
        self.assertGreater(len(csv_rows(self.on)), 4)

    def test_the_provenance_key_reaches_no_output(self):
        for body in (self.on.csv, self.on.json, self.on.seen):
            self.assertNotIn(telemetry.PAID_UNIT.encode(), body)
            self.assertNotIn(b"paid_00", body)
        stamped = {"Title": "x", telemetry.PAID_UNIT: ("paid_000", 1)}
        self.assertEqual(set(scraper.to_output(stamped)), set(scraper.OUTPUT_COLUMNS))

    def test_nothing_is_stamped_with_telemetry_off(self):
        rows = []
        real = scraper.finalize

        def spy(raw):
            rows[:] = raw
            return real(raw)
        paid_sweep([Script(A_ITEMS[:2])], on=False,
                   patches=[mock.patch.object(scraper, "finalize", spy)])
        self.assertTrue(rows)
        self.assertFalse(any(telemetry.PAID_UNIT in r for r in rows))
        self.assertIsNone(self.off.telemetry)

    def test_the_same_budget_decision(self):
        """The cap trips at the same unit, on the same reading, either way."""
        def run(on):
            return paid_sweep([Script(A_ITEMS[:1]), Script(B_ITEMS[1:2]),
                               Script(), Script()], on=on,
                              keywords=("K1", "K2", "K3", "K4"), budget=0.02,
                              account=[0.0, 0.014, 0.05])
        off, on = run(False), run(True)
        self.assertEqual(off.client.calls, on.client.calls)
        self.assertEqual([c[0] for c in on.client.calls].count("start"), 2)
        self.assertEqual(off.csv, on.csv)
        self.assertIn("spend cap $0.02 reached", on.log)
        self.assertEqual([u["status"] for u in on.telemetry["paid_units"]],
                         ["completed", "completed", "skipped_budget", "skipped_budget"])


# ===========================================================================
# 2 — identity
# ===========================================================================
class UnitIdentity(unittest.TestCase):

    def test_every_planned_unit_has_an_id_in_plan_order(self):
        got = paid_sweep([Script(), Script(), Script()], sites=("linkedin", "indeed"),
                         keywords=("K1", "K2"), locations=("Bengaluru",),
                         done=("linkedin|K1|Bengaluru",))
        plan = got.telemetry["paid_units"]
        self.assertEqual([u["unit_id"] for u in plan],
                         ["paid_000", "paid_001", "paid_002", "paid_003"])
        self.assertEqual([u["plan_index"] for u in plan], [0, 1, 2, 3])
        self.assertEqual([u["provider"] for u in plan],
                         ["linkedin", "linkedin", "indeed", "indeed"])
        self.assertEqual(plan[0]["status"], "skipped_done")    # no run, still an id
        self.assertIsNone(units(got)["paid_000"]["execution"])
        self.assertIsNone(plan[0]["funnel"])
        executed = [u["unit_id"] for u in got.telemetry["units"] if u["path"] == "paid"]
        self.assertEqual(executed, ["paid_001", "paid_002", "paid_003"])

    def test_ids_and_fingerprints_are_deterministic(self):
        def plan():
            got = paid_sweep([Script(), Script()], keywords=("K1", "K2"))
            return [(u["unit_id"], u["query_fp"]) for u in got.telemetry["paid_units"]]
        first, second = plan(), plan()
        self.assertEqual(first, second)
        self.assertEqual(len({fp for _, fp in first}), 2)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{12}", fp) for _, fp in first))

    def test_a_unit_that_fails_before_any_run_keeps_its_id(self):
        got = paid_sweep([Script(start_error=RuntimeError("HTTP 402"))])
        u = units(got)["paid_000"]
        self.assertEqual(u["status"], "failed")
        self.assertNotIn("actor_run_id", u["execution"])
        self.assertEqual(got.telemetry["paid_summary"]["actor_starts"], 0)


# ===========================================================================
# 3–10 — one unit's clock and funnel
# ===========================================================================
class OneUnit(unittest.TestCase):

    def test_a_successful_unit_records_where_its_time_went(self):
        delay = {"start": 0.03, "get": 0.01, "dataset": 0.02}
        got = paid_sweep([Script(A_ITEMS[:2], polls=3, delay=delay)])
        ex = units(got)["paid_000"]["execution"]
        self.assertEqual(ex["poll_count"], 3)
        self.assertGreaterEqual(ex["start_ms"], 25)
        self.assertGreaterEqual(ex["poll_get_ms"], 25)
        self.assertGreaterEqual(ex["wait_ms"], ex["poll_get_ms"])
        self.assertGreaterEqual(ex["dataset_ms"], 15)
        self.assertIsInstance(ex["checkpoint_ms"], int)
        self.assertGreaterEqual(ex["duration_ms"] + 2,      # each figure rounded
                                ex["start_ms"] + ex["wait_ms"] + ex["dataset_ms"])
        self.assertEqual(ex["actor_run_time_s"], 30.0)
        self.assertEqual(ex["actor_build_number"], "1.7.17")
        self.assertEqual(ex["provider_ceiling_usd"], 0.046)
        self.assertEqual(ex["max_total_charge_usd"], "0.046")
        self.assertEqual(ex["charged_events"], {"apify-default-dataset-item": 2,
                                                "apify-actor-start": 1})
        summary = got.telemetry["paid_summary"]
        self.assertIsInstance(summary["paid_phase_ms"], int)
        self.assertGreaterEqual(summary["paid_phase_ms"], ex["duration_ms"])

    def test_a_failed_run_invents_no_rows_and_no_cost(self):
        got = paid_sweep([Script(A_ITEMS[:3], status="FAILED", usage=0.004)],
                         account=[1.0])
        u = units(got)["paid_000"]
        ex = u["execution"]
        self.assertEqual(u["status"], "failed")
        self.assertFalse(ex["ok"])
        self.assertEqual(ex["failure_type"], "RuntimeError")
        self.assertEqual(ex["actor_status"], "FAILED")
        self.assertEqual(ex["raw_count"], 0)
        self.assertIsNone(u["funnel"])
        self.assertIsNone(u["trace"])
        # What the terminal Run said is kept; the account was not read on the
        # failure path, so there is no account reading at all — not a zero.
        self.assertEqual([(o["source"], o["usd"], o["final"])
                          for o in ex["cost_observations"]],
                         [("run_record_at_completion", 0.004, False)])
        self.assertNotIn(("dataset", "ds_run_1"), got.client.calls)
        self.assertIn("No jobs scraped", got.log)

    def test_zero_results_are_zeros(self):
        got = paid_sweep([Script([]), Script(A_ITEMS[:1])], keywords=("K1", "K2"))
        u = units(got)["paid_000"]
        self.assertEqual(u["status"], "completed")
        f = u["funnel"]
        self.assertEqual((f["raw"], f["normalized"], f["eligible"], f["final"]), (0, 0, 0, 0))
        self.assertEqual(f["stale"], 0)
        self.assertEqual(u["trace"], "")

    def test_the_funnel_of_every_exit(self):
        got = attribution()
        a = units(got)["paid_000"]["funnel"]
        self.assertEqual(a["requested"], 15)
        self.assertEqual((a["raw"], a["normalized"]), (8, 8))                # 6
        self.assertEqual(a["stages"], {
            "observed_normalized": 8, "post_hard_filter_and_score": 7,
            "post_recency": 6, "post_salary_reachability_visa_eor": 5,
            "post_location_eligible": 5, "final_after_dedupe": 2})
        self.assertEqual(a["stale"], 1)                                      # 7
        self.assertEqual(a["eligible"], 5)                                   # 9
        self.assertEqual(a["eligible_positive"], 4)                          # 10
        self.assertEqual((a["final"], a["final_positive"]), (2, 1))
        self.assertEqual(units(got)["paid_000"]["trace"], A_TRACE)
        self.assertEqual(units(got)["paid_001"]["trace"], B_TRACE)

    def test_the_funnel_agrees_with_the_filter_counts_the_engine_prints(self):
        got = attribution()
        a, b = (units(got)[k]["funnel"] for k in ("paid_000", "paid_001"))
        stale_total = int(re.search(r"(\d+) stale", got.log).group(1))
        self.assertEqual(a["stale"] + b["stale"], stale_total)   # free row is fresh


# ===========================================================================
# 8, 11, 12 — dedupe and final contribution follow the engine's own survivors
# ===========================================================================
class Attribution(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.got = attribution()
        cls.a = units(cls.got)["paid_000"]
        cls.b = units(cls.got)["paid_001"]

    def test_final_positions_are_exactly_the_rows_the_user_got(self):
        """F in a trace is a row in the CSV, at that search_rank, and nothing
        else is — the attribution is read off the real result."""
        rows = csv_rows(self.got)
        for u, label in ((self.a, "Alpha Engineer @ Bengaluru"),
                         (self.b, "Beta Engineer @ Bengaluru")):
            finals = {i + 1 for i, t in enumerate(u["trace"].split()) if t[0] == "F"}
            self.assertEqual(finals, {int(r["search_rank"]) for r in rows
                                      if r["search_query"] == label})

    def test_one_final_job_is_counted_once(self):
        rows = csv_rows(self.got)
        paid_rows = [r for r in rows if r["source_site"] == "linkedin"]
        self.assertEqual(self.a["funnel"]["final"] + self.b["funnel"]["final"],
                         len(paid_rows))
        summary = self.got.telemetry["paid_summary"]["by_provider"]["linkedin"]
        self.assertEqual(summary["final"], len(paid_rows))
        self.assertEqual(len(rows) - len(paid_rows), 1)          # the free row

    def test_who_beat_each_eligible_row(self):
        self.assertEqual(self.a["funnel"]["dedupe_lost"],
                         {"same_unit": 1, "other_paid": 1, "free": 1})
        self.assertEqual(self.b["funnel"]["dedupe_lost"],
                         {"same_unit": 0, "other_paid": 1, "free": 0})

    def test_a_tie_goes_to_the_earlier_unit_as_dedupe_decides(self):
        rows = csv_rows(self.got)
        acme = [r for r in rows if r["title"] == "Full Stack Engineer"]
        self.assertEqual(len(acme), 1)
        self.assertEqual(acme[0]["search_query"], "Alpha Engineer @ Bengaluru")
        self.assertEqual(self.a["trace"].split()[0][0], "F")
        self.assertEqual(self.b["trace"].split()[0][0], "D")

    def test_a_higher_score_wins_across_units_whatever_the_order(self):
        rows = csv_rows(self.got)
        iota = [r for r in rows if r["company"] == "Iota"]
        self.assertEqual([r["search_query"] for r in iota], ["Beta Engineer @ Bengaluru"])

    def test_the_free_twin_wins_where_it_scores_higher(self):
        rows = csv_rows(self.got)
        beta = [r for r in rows if r["company"] == "Beta"]
        self.assertEqual([r["source_site"] for r in beta], ["lever:betalever"])

    def test_marginal_means_no_one_else_had_it(self):
        # A: A1 is also B1's, A6 is only A's. B: B2 and B3 only B's; B4 is A8's.
        self.assertEqual(self.a["funnel"]["final_marginal"], 1)
        self.assertEqual(self.b["funnel"]["final_marginal"], 2)

    def test_repeat_work_is_visible_before_any_filter(self):
        self.assertEqual(self.a["funnel"]["acquired"], {
            "new": 7, "repeat_in_unit": 1, "repeat_of_earlier_paid": 0, "unkeyed": 0})
        self.assertEqual(self.b["funnel"]["acquired"], {
            "new": 2, "repeat_in_unit": 0, "repeat_of_earlier_paid": 2, "unkeyed": 0})
        self.assertEqual(self.a["funnel"]["key_also_free"], 1)

    def test_a_retitled_native_id_is_a_new_job_as_job_key_says(self):
        rows = csv_rows(self.got)
        self.assertEqual(sorted(r["title"] for r in rows if r["company"] == "Acme"),
                         ["Full Stack Engineer", "Senior Full Stack Engineer"])
        self.assertEqual(self.b["trace"].split()[2], "F+n.")

    def test_counts_equal_the_trace(self):
        for u in (self.a, self.b):
            tokens = u["trace"].split()
            f = u["funnel"]
            self.assertEqual(len(tokens), f["normalized"])
            self.assertEqual(sum(t[0] == "F" for t in tokens), f["final"])
            self.assertEqual(sum(t[0] in "DF" for t in tokens), f["eligible"])
            self.assertEqual(sum(t[0] in "DF" and t[1] == "+" for t in tokens),
                             f["eligible_positive"])
            self.assertEqual(sum(t[0] == "S" for t in tokens), f["stale"])


# ===========================================================================
# 14 — privacy
# ===========================================================================
class Privacy(unittest.TestCase):

    def test_no_row_text_url_or_token_in_the_record(self):
        items = [li("MARKERURL1", "Full Stack Engineer", "MARKERCO", RICH + " MARKERJD"),
                 li("MARKERURL2", "MARKERTITLE Engineer", "Acme", RICH)]
        got = paid_sweep([Script(items)])
        body = got.telemetry_text
        for marker in ("MARKERURL", "MARKERCO", "MARKERJD", "MARKERTITLE", TOKEN,
                       "linkedin.com/jobs"):
            self.assertNotIn(marker, body)

    def test_the_new_sections_hold_no_query_text(self):
        got = paid_sweep([Script(A_ITEMS[:2])], keywords=("QUERYMARKER Engineer",),
                         locations=("Bengaluru",))
        paid = json.dumps({k: got.telemetry[k] for k in ("paid_units", "paid_summary")})
        self.assertNotIn("QUERYMARKER", paid)
        self.assertNotIn("Bengaluru", paid)
        # V2-A's unit record already carried the keyword; C1 did not widen it.
        self.assertIn("QUERYMARKER", json.dumps(got.telemetry["units"]))

    def test_every_new_key_is_known(self):
        got = attribution()
        u = got.telemetry["paid_units"][0]
        self.assertEqual(set(u), {"unit_id", "plan_index", "provider", "actor",
                                  "requested_depth", "charge_ceiling_usd", "query_fp",
                                  "location_mode", "company_filter", "status",
                                  "funnel", "trace"})
        v2a = {"path", "family", "board", "query", "location", "country",
               "requested_limit", "timeout_s", "started_at", "finished_at",
               "duration_ms", "ok", "failure_category", "failure", "requests",
               "retries", "raw_count", "normalized_count", "source_gate_count",
               "shadow"}
        record = got.telemetry["units"][0]
        self.assertLessEqual(set(record), v2a | set(telemetry.PAID_RUN_FIELDS)
                             | {"cost_observations"})
        self.assertTrue(re.fullmatch(r"([HSRAGDF][+\-.][nupk][f.] ?)*", u["trace"]))

    def test_the_probe_reads_the_same_join(self):
        got = attribution()
        view = {u["unit_id"]: u for u in probe.paid_view(got.telemetry)}
        joined = units(got)
        for unit_id, u in view.items():
            self.assertEqual(u["trace"], joined[unit_id]["trace"])
            self.assertEqual(u["execution"]["actor_run_id"],
                             joined[unit_id]["execution"]["actor_run_id"])
            self.assertNotIn("query", u["execution"])


# ===========================================================================
# 15–17 — cost readings: separate, provisional, never guessed
# ===========================================================================
class CostObservations(unittest.TestCase):

    def test_run_record_and_account_delta_stay_apart(self):
        """C0's own numbers: the terminal poll said $0.02805, the account had
        moved $0.01405."""
        got = paid_sweep([Script(A_ITEMS[:2], usage=0.02805)], account=[5.0, 5.01405])
        ex = units(got)["paid_000"]["execution"]
        obs = {o["source"]: o for o in ex["cost_observations"]}
        self.assertEqual(obs["run_record_at_completion"]["usd"], 0.02805)
        self.assertEqual(obs["account_usage_delta"]["usd"], 0.01405)
        self.assertEqual(len(ex["cost_observations"]), 2)
        for o in obs.values():
            self.assertTrue(o["observed_at"].endswith("+00:00"))
        # V2-A's two fields keep their meaning.
        unit = got.telemetry["units"][0]
        self.assertEqual(unit["reported_cost_usd"], 0.02805)
        self.assertEqual(unit["billed_delta_usd"], 0.01405)
        self.assertEqual(ex["budget_basis"], "account_delta")
        self.assertEqual(ex["budget_view_usd"], 0.01405)

    def test_nothing_production_records_is_final(self):
        got = attribution()
        observed = [o for u in units(got).values()
                    for o in (u["execution"] or {}).get("cost_observations", [])]
        self.assertTrue(observed)
        self.assertFalse(any(o["final"] for o in observed))
        self.assertEqual({s for s, final in telemetry.COST_SOURCES.items() if final},
                         {"run_record_settled"})
        self.assertNotIn("run_record_settled", {o["source"] for o in observed})
        for c in got.telemetry["paid_summary"]["cost_observed"].values():
            self.assertFalse(c["final"])

    def test_finality_is_not_the_callers_to_choose(self):
        with tempfile.TemporaryDirectory() as tmp, b3._env(**{telemetry.FLAG: "1"}):
            telemetry.start("paid", tmp)
            with telemetry.unit("paid", "linkedin") as rec:
                telemetry.cost_observation("account_usage_delta", 0.01)
                telemetry.cost_observation("made_up_source", 0.02)
                telemetry.cost_observation("run_record_at_completion", None)
                telemetry.cost_observation("run_record_at_completion", "n/a")
            telemetry.finish()
        self.assertEqual([(o["source"], o["final"]) for o in rec["cost_observations"]],
                         [("account_usage_delta", False)])

    def test_a_missing_reading_is_absent_not_zero(self):
        got = paid_sweep([Script(A_ITEMS[:2], usage=None)], account=None)
        ex = units(got)["paid_000"]["execution"]
        self.assertNotIn("cost_observations", ex)
        self.assertEqual(ex["budget_basis"], "run_record_sum")
        self.assertIsNone(ex["account_read_ms"])
        self.assertEqual(got.telemetry["paid_summary"]["cost_observed"], {})


# ===========================================================================
# 18–19 — planned vs executed, per sweep and per provider
# ===========================================================================
class Summary(unittest.TestCase):

    def test_planned_attempted_completed_failed_skipped(self):
        got = paid_sweep(
            [Script(status="FAILED", usage=0.004), Script(A_ITEMS[:1], usage=0.03)],
            keywords=("K1", "K2", "K3", "K4"), done=("linkedin|K1|Bengaluru",),
            budget=0.02, account=[0.0, 0.03])
        self.assertEqual([u["status"] for u in got.telemetry["paid_units"]],
                         ["skipped_done", "failed", "completed", "skipped_budget"])
        s = got.telemetry["paid_summary"]
        self.assertEqual((s["planned"], s["attempted"], s["completed"], s["failed"],
                          s["skipped_done"], s["skipped_budget"], s["unvisited"]),
                         (4, 2, 1, 1, 1, 1, 0))
        self.assertEqual(s["actor_starts"], 2)
        self.assertEqual(s["planned_bounded_exposure_usd"], "0.184")
        self.assertEqual(s["attempted_bounded_exposure_usd"], "0.092")

    def test_providers_aggregate_their_own_units(self):
        got = paid_sweep([Script(A_ITEMS[:2]), Script(A_ITEMS[5:6]),
                          Script(B_ITEMS[1:2]), Script()],
                         sites=("linkedin", "indeed"), keywords=("K1",),
                         locations=("Bengaluru", "Hyderabad"))
        s = got.telemetry["paid_summary"]
        executed = {u["unit_id"]: u for u in got.telemetry["units"] if u["path"] == "paid"}
        plan = got.telemetry["paid_units"]
        for provider in ("linkedin", "indeed"):
            mine = [u for u in plan if u["provider"] == provider]
            p = s["by_provider"][provider]
            self.assertEqual(p["planned"], len(mine))
            self.assertEqual(p["unit_wall_ms"], sum(
                executed[u["unit_id"]]["duration_ms"] for u in mine
                if u["unit_id"] in executed))
            self.assertEqual(p["final"], sum(u["funnel"]["final"] for u in mine
                                             if u["funnel"]))
        self.assertEqual(s["by_provider"]["linkedin"]["planned"], 2)
        self.assertEqual(s["planned_unbounded_units"], 2)             # indeed
        self.assertEqual(s["planned_bounded_exposure_usd"], "0.092")
        indeed = [u for u in plan if u["provider"] == "indeed"][0]
        self.assertIsNone(indeed["charge_ceiling_usd"])


# ===========================================================================
# 20–22, 25 — B1, free sweeps, schema
# ===========================================================================
class Compatibility(unittest.TestCase):

    def test_the_b1_contract_with_telemetry_on(self):
        got = paid_sweep([Script(A_ITEMS[:1])])
        (_, actor, run_input, ceiling), = [c for c in got.client.calls if c[0] == "start"]
        expected = scraper.build_input("linkedin", scraper.effective_search(
            "linkedin", {"keywords": "Alpha Engineer", "location": "Bengaluru",
                         "company": None, "experience_years": scraper.SEARCH["experience_years"],
                         "max_results": scraper.SEARCH["max_results"]}))
        self.assertEqual(run_input, expected)
        self.assertEqual((run_input["limitPerSource"], run_input["count"]), (15, 15))
        self.assertFalse(run_input["scrapeCompany"])
        self.assertEqual(ceiling, Decimal("0.046"))
        self.assertEqual(units(got)["paid_000"]["charge_ceiling_usd"], "0.046")

    def test_a_free_sweep_has_no_paid_sections_and_the_same_output(self):
        on, off = b3.sweep(), b3.sweep({telemetry.FLAG: None})
        self.assertEqual(on.csv, off.csv)
        for key in ("paid_units", "paid_summary"):
            self.assertNotIn(key, on.telemetry)
        self.assertNotIn("paid_phase_start", on.telemetry["milestones"])

    def test_the_record_schema_and_v2a_fields(self):
        got = attribution()
        rec = got.telemetry
        self.assertEqual(rec["schema"], "search-v2a.1")
        self.assertEqual(rec["paid_summary"]["schema"], "search-v2c1.1")
        unit = rec["units"][0]
        for key in ("path", "family", "board", "query", "location", "requested_limit",
                    "duration_ms", "ok", "raw_count", "actor_run_id", "poll_count",
                    "reported_cost_usd", "billed_delta_usd", "max_total_charge_usd"):
            self.assertIn(key, unit)
        self.assertEqual(set(rec["stages"]["final_after_dedupe"]), {"total", "by_source"})
        self.assertFalse(any(k.startswith("_") for k in rec))

    def test_an_older_record_still_reads(self):
        """A pre-C1 record has no paid sections; the probe's reader says so
        rather than failing."""
        from bench import search_v2_paid_probe as probe
        self.assertEqual(probe.paid_view({"schema": "search-v2a.1", "units": []}), [])

    def test_a_hook_that_breaks_cannot_fail_the_sweep(self):
        def boom(*a):
            raise KeyError("boom")
        broken = mock.patch.object(telemetry, "_paid_outcome", boom)
        off, on = attribution(on=False), attribution(patches=[broken])
        self.assertEqual(off.csv, on.csv)
        self.assertTrue(any("paid telemetry skipped in boom" in n
                            for n in on.telemetry["notes"]))


# ===========================================================================
# 21 and the ledger — the probe stays behind C0; the ledger is not a key
# ===========================================================================
from bench import paid_guard, search_v2_paid_probe as probe      # noqa: E402

LEDGER_KEYS = {"stage", "at", "provider", "actor", "purpose", "actor_starts",
               "requested_depth", "ceiling_usd_per_start", "intended_max_usd",
               "actual_final_usd", "evidence", "status", "cumulative_intended_usd",
               "cumulative_known_actual_usd"}
# V2-C3's one entry is a start that carried two searches, so it also says how
# many logical searches that was and the batch size; actor_starts is physical.
LEDGER_BATCH_KEYS = {"logical_units", "batch_size"}


def probe_args(**over):
    return SimpleNamespace(**{**dict(
        site="linkedin", searches=1, keywords="Backend Developer", allow_paid=False,
        max_usd="0.10", exposed_usd="0.046", ledger=str(probe.LEDGER)), **over})


class ProbeStaysBehindTheGuard(unittest.TestCase):

    def test_both_keys_and_both_limits_come_from_the_command_line(self):
        argv = probe.guard_argv(probe_args(), "p")
        self.assertEqual(argv[:4], [sys.executable, "-u", "-m", "bench.paid_guard"])
        self.assertNotIn("--allow-paid", argv)
        self.assertIn("--allow-paid", probe.guard_argv(probe_args(allow_paid=True), "p"))
        self.assertNotIn("--allow-paid",
                         probe.guard_argv(probe_args(allow_paid="yes"), "p"))
        self.assertEqual(argv[argv.index("--exposed-usd") + 1], "0.046")
        self.assertEqual(argv[argv.index("--max-usd") + 1], "0.10")
        self.assertEqual(argv[argv.index("--") + 1:],
                         ["--profile", "p", "--site", "linkedin", "--limit", "1",
                          "--keywords", "Backend Developer", "--yes"])

    def test_the_ledger_cannot_change_what_the_guard_is_given(self):
        """A ledger claiming no exposure and endless headroom changes nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(json.dumps({"ceiling_usd": "999", "entries": [
                {"cumulative_intended_usd": "0", "cumulative_known_actual_usd": 0}]}))
            args = probe_args(ledger=str(path))
            with mock.patch.object(probe, "LEDGER", path):
                argv = probe.guard_argv(args, "p")
        self.assertNotIn("--allow-paid", argv)
        self.assertEqual(argv[argv.index("--exposed-usd") + 1], "0.046")
        self.assertEqual(argv[argv.index("--max-usd") + 1], "0.10")
        self.assertFalse({"LEDGER", "load_ledger", "ledger_blocks", "ledger"}
                         & set(probe.guard_argv.__code__.co_names))

    def test_the_ledger_can_only_refuse(self):
        ledger = probe.load_ledger()
        committed = ledger["entries"][-1]["cumulative_intended_usd"]
        self.assertIsNone(probe.ledger_blocks(ledger, committed, "0.10"))
        self.assertIn("below", probe.ledger_blocks(ledger, "0", "0.10"))
        self.assertIn("exceeds", probe.ledger_blocks(ledger, committed, "2.01"))
        self.assertIsNone(probe.ledger_blocks(None, "0", "5"))

    def test_a_refused_probe_writes_nothing_and_starts_nothing(self):
        with mock.patch.object(probe.subprocess, "run") as run, \
                mock.patch.object(probe, "profile_source") as source:
            with self.assertRaises(SystemExit) as refused:
                probe.probe(probe_args(exposed_usd="0"))
        self.assertIn("research ledger", str(refused.exception))
        run.assert_not_called()
        source.assert_not_called()

    def test_nothing_that_decides_reads_the_ledger(self):
        for module in (paid_guard, scraper, telemetry):
            text = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("paid-research-ledger", text)
            self.assertNotIn("LEDGER", text)


class ResearchLedger(unittest.TestCase):

    def test_totals_add_up_under_the_ceiling_and_hold_no_secret(self):
        text = probe.LEDGER.read_text()
        ledger = json.loads(text)
        intended = actual = Decimal(0)
        for e in ledger["entries"]:
            self.assertIn(set(e), (LEDGER_KEYS, LEDGER_KEYS | LEDGER_BATCH_KEYS))
            intended += Decimal(e["intended_max_usd"])
            self.assertEqual(Decimal(e["cumulative_intended_usd"]), intended)
            if e["actual_final_usd"] is not None:
                actual += Decimal(str(e["actual_final_usd"]))
                self.assertLessEqual(Decimal(str(e["actual_final_usd"])),
                                     Decimal(e["intended_max_usd"]))
            self.assertAlmostEqual(e["cumulative_known_actual_usd"], float(actual), 6)
            self.assertTrue((probe.LEDGER.parent / e["evidence"]).exists(), e["evidence"])
        self.assertLessEqual(intended, Decimal(ledger["ceiling_usd"]))
        self.assertEqual(ledger["entries"][0]["stage"], "C0")
        self.assertNotRegex(text, r"apify_api_\w+")
        for word in ("title", "company", "description", "url"):
            self.assertNotIn(f'"{word}"', text)

    def test_append_keeps_running_totals(self):
        ledger = {"ceiling_usd": "2.00", "entries": []}
        ledger = probe.ledger_append(ledger, {"intended_max_usd": "0.046",
                                              "actual_final_usd": 0.03005})
        ledger = probe.ledger_append(ledger, {"intended_max_usd": "0.092",
                                              "actual_final_usd": None})
        last = ledger["entries"][-1]
        self.assertEqual(last["cumulative_intended_usd"], "0.138")
        self.assertEqual(last["cumulative_known_actual_usd"], 0.03005)


class ProbeReadings(unittest.TestCase):
    """The probe's own logic, offline: when a charge counts as settled."""

    def test_settled_means_unchanged_across_two_reads(self):
        self.assertIsNone(probe.settle([0.028]))
        self.assertIsNone(probe.settle([0.028, 0.030]))
        self.assertEqual(probe.settle([0.028, 0.030, 0.030]), 1)
        self.assertEqual(probe.settle([0.03, 0.03, 0.03]), 0)
        self.assertIsNone(probe.settle([None, 0.03]))

    def test_observation_stops_once_run_and_account_agree(self):
        usage, account = iter([0.02805, 0.03005, 0.03005, 0.03005]), \
            iter([5.01405, 5.02005, 5.03005, 5.03005])

        class Account:
            def run(self, rid):
                return SimpleNamespace(get=lambda: SimpleNamespace(
                    status="SUCCEEDED", usage_total_usd=next(usage),
                    charged_event_counts={"apify-default-dataset-item": 15}))

            def user(self):
                return SimpleNamespace(limits=lambda: SimpleNamespace(
                    model_dump=lambda: {"current": {"monthly_usage_usd": next(account)}}))
        owned = {"run_1": ("APIFY_TOKEN", Account())}
        runs, acct, _ = probe.observe(["run_1"], owned, {"APIFY_TOKEN": {"usd": 5.0}},
                                      window_s=480, sleep=lambda s: None)
        self.assertEqual([r["usage_total_usd"] for r in runs["run_1"]],
                         [0.02805, 0.03005, 0.03005, 0.03005])
        self.assertEqual([r["delta_usd"] for r in acct["APIFY_TOKEN"]],
                         [0.01405, 0.02005, 0.03005, 0.03005])

    def test_an_account_covers_its_runs_it_need_not_equal_them(self):
        """Probe A's own readings: the delta passed $0.0601 and kept creeping."""
        deltas = [0.058111, 0.060111, 0.060123, 0.060123, 0.060135, 0.060139]
        self.assertEqual(probe.covered(deltas, 0.0601), 1)
        self.assertIsNone(probe.covered([0.058, 0.059], 0.0601))
        self.assertIsNone(probe.covered([0.0601, None], 0.0601))
        self.assertEqual(probe.covered([0.061, 0.0601], 0.0601), 0)
        view = probe.account_view("A", [{"delta_usd": d, "observed_at": str(i)}
                                        for i, d in enumerate(deltas)], 0.0601)
        self.assertEqual(view["covered_at"], "1")
        self.assertEqual(view["residual_usd_at_end"], 0.000039)

    def test_the_window_is_bounded(self):
        class Moving:
            n = 0

            def run(self, rid):
                Moving.n += 1
                return SimpleNamespace(get=lambda: SimpleNamespace(
                    status="SUCCEEDED", usage_total_usd=Moving.n / 1000,
                    charged_event_counts={}))

            def user(self):
                raise RuntimeError("unreadable")
        runs, _, _ = probe.observe(["r"], {"r": ("A", Moving())}, {}, window_s=60,
                                   sleep=lambda s: None)
        self.assertEqual(len(runs["r"]), len([s for s in probe.SCHEDULE if s <= 60]))

    def test_summary_bands_and_ratios(self):
        unit = {"unit_id": "paid_000", "location_mode": "place",
                "execution": {"actor_run_time_s": 39.6, "duration_ms": 47100},
                "funnel": {"raw": 15, "requested": 15, "stale": 1, "eligible": 6,
                           "eligible_positive": 3, "final": 5, "final_marginal": 5,
                           "acquired": {"new": 14, "repeat_in_unit": 1,
                                        "repeat_of_earlier_paid": 0, "unkeyed": 0}},
                "trace": " ".join(["F+n."] * 5 + ["D+u.", "S+n."] + ["R-n."] * 8)}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ev.json")
            Path(path).write_text(json.dumps({"shape": {}, "units": [unit],
                                              "cost_convergence": [{
                                                  "unit_id": "paid_000",
                                                  "settled": {"usd": 0.03005}}]}))
            out = probe.summarize([path])
        self.assertEqual(out["yield_by_position"]["1-5"]["final"], 5)
        self.assertEqual(out["yield_by_position"]["6-10"]["eligible"], 1)
        self.assertEqual(out["yield_by_position"]["6-10"]["stale"], 1)
        self.assertEqual(out["yield_by_position"]["11-15"]["eligible"], 0)
        u = out["units"][0]
        self.assertEqual(u["usd_per_final"], round(0.03005 / 5, 4))
        self.assertEqual(u["duplicate_rate"], round(1 / 15, 4))
        self.assertEqual(out["sample"]["units"], 1)


if __name__ == "__main__":
    unittest.main()
