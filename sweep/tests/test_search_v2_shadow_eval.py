"""Search Engine V2-B3: the eight shadow boards are judged, never shipped.

What these pin, in the brief's order: shadow off adds nothing; shadow on
fetches exactly the eight; the user's rows, ranking, dedupe survivor, CSV and
JSON are byte-identical either way; production source counts exclude shadow;
one board, every board, the evaluator or the record write failing cannot fail a
sweep; the two overlap definitions; the candidate funnel equals a frozen
offline replay; top-N is measured against the real result; the record holds no
row text; the Lever executor never sees a shadow board; nothing paid is
reachable.

No network: sockets are denied for the whole module, and every HTTP response is
a fixture served through ats.get_json — the real adapter still maps it.
"""
import contextlib
import copy
import glob
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime, timedelta

import experience_guard
import scraper
import telemetry
from sources import ats, concurrency, shadow

_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def setUpModule():
    def deny(*a, **kw):
        raise AssertionError("a shadow test tried to open a socket")
    socket.socket.connect = deny
    socket.create_connection = deny


def tearDownModule():
    socket.socket.connect = _REAL_CONNECT
    socket.create_connection = _REAL_CREATE


TODAY = datetime.now().strftime("%Y-%m-%d")
STALE = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
NOW_MS = int(time.time() * 1000)
RICH = "react node postgres typescript python django"   # a full-stack score
EIGHT = [f"greenhouse:{t}" for t in shadow.BOARDS["greenhouse"]]


def gh(token, job_id, title, desc, location="Remote", posted=TODAY):
    """One Greenhouse posting, shaped as boards-api returns it."""
    return {"id": job_id, "internal_job_id": job_id + 100000, "title": title,
            "location": {"name": location},
            "absolute_url": f"https://job-boards.greenhouse.io/{token}/jobs/{job_id}",
            "updated_at": f"{posted}T09:00:00Z", "content": desc,
            "company_name": token, "offices": [], "requisition_id": f"R{job_id}"}


def lever(post_id, title, desc, location="Remote"):
    return {"id": post_id, "text": title,
            "categories": {"location": location, "commitment": "Full-time"},
            "hostedUrl": f"https://jobs.lever.co/x/{post_id}",
            "applyUrl": f"https://jobs.lever.co/x/{post_id}/apply",
            "createdAt": NOW_MS, "descriptionPlain": desc}


# The user's sweep. Small, but it has what a leak would disturb: a Fivetran
# posting that a shadow board publishes a strictly better copy of, and a
# Vercel posting a shadow board publishes under the same native id.
REGISTRY = {"lever": {"alpha": "Alpha", "fivetranlever": "Fivetran"},
            "greenhouse": {"gamma": "Gamma", "vercelmirror": "Vercel"}}
PRODUCTION = {
    "lever:alpha": [lever("a1", "Backend Engineer", "python django"),
                    lever("a2", "Frontend Engineer", "react typescript",
                          "Anywhere in the World")],
    "lever:fivetranlever": [lever("f1", "Backend Engineer", "python django")],
    "greenhouse:gamma": {"jobs": [
        gh("gamma", 1001, "Full Stack Developer", "react node postgres"),
        gh("gamma", 1002, "Software Engineer", "java spring")]},
    "greenhouse:vercelmirror": {"jobs": [
        gh("vercelmirror", 5001, "Frontend Engineer", "react typescript")]},
}
# One case per funnel exit, so a wrong filter shows up as a wrong count.
SHADOW = {
    "fivetran": [gh("fivetran", 7001, "Backend Engineer", RICH),   # beats lever twin
                 gh("fivetran", 7002, "Full Stack Engineer", RICH)],  # new, top
    "abnormalsecurity": [gh("abnormalsecurity", 7101, "Frontend Engineer",
                            "react typescript")],            # new, positive
    "apolloio": [gh("apolloio", 7201, "Software Engineer", "java spring")],  # < 0
    "brex": [gh("brex", 7301, "Staff Engineer", "react node")],   # hard drop
    "vercel": [gh("vercel", 5001, "Frontend Engineer",
                  "react typescript")],                      # same posting
    "jumio": [gh("jumio", 7401, "Backend Engineer", "python django",
                 posted=STALE)],                             # stale
    "catawiki": [gh("catawiki", 7501, "Account Executive", "sales quota")],
    "zetaglobal": [],                                        # empty board
}


def routes(production=None, shadow_boards=None):
    table = {}
    bodies = dict(production or PRODUCTION)
    for token, jobs in (SHADOW if shadow_boards is None else shadow_boards).items():
        bodies[f"greenhouse:{token}"] = {"jobs": jobs}
    for board, body in bodies.items():
        platform, token = board.split(":")
        table[ats.ATS[platform]["url"].format(token=token)] = (board, body)
    return table


@contextlib.contextmanager
def _env(**values):
    old = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _served(table, fail=(), calls=None, delay=None, status=503, done=None):
    """ats.get_json answered from `table`; boards in `fail` raise HTTP
    `status`. `delay` sleeps per board, `done` records completion order —
    what a concurrency test needs to make completion order visible."""
    real = ats.get_json

    def fake(url, *a, **kw):
        board, body = table[url]
        if calls is not None:
            calls.append(board)
        time.sleep((delay or {}).get(board, 0))
        if done is not None:
            done.append(board)
        if board in fail:
            raise urllib.error.HTTPError(url, status, "unavailable", None, None)
        return copy.deepcopy(body)
    ats.get_json = fake
    try:
        yield
    finally:
        ats.get_json = real


BASE_ENV = {telemetry.FLAG: "1", shadow.FLAG: None, concurrency.FLAG: None,
            concurrency.WORKERS_ENV: None, concurrency.GREENHOUSE_FLAG: None,
            concurrency.GREENHOUSE_WORKERS_ENV: None, experience_guard.FLAG: None,
            scraper.READY_FLAG: None, "APIFY_TOKEN": None}


class Result:
    def __init__(self, out, calls, log):
        def one(pattern, mode="rb"):
            found = glob.glob(os.path.join(out, pattern))
            if not found:
                return None
            with open(found[0], mode) as fh:
                return fh.read()
        self.csv = one("jobs_*.csv")
        self.json = one("jobs_*.json")
        self.seen = one("seen.tsv")
        body = one(os.path.join("telemetry", "sweep_*.json"), "r")
        self.telemetry_text = body
        self.telemetry = json.loads(body) if body else None
        self.files = sorted(os.path.relpath(p, out) for p in glob.glob(
            os.path.join(out, "**", "*"), recursive=True) if os.path.isfile(p))
        self.calls = calls
        self.log = log


def sweep(env=None, fail=(), table=None, before=None, registry=None,
          delay=None, status=503, done=None):
    """scraper.main() end to end, offline: a free sweep over REGISTRY with
    every response a fixture. Returns what is left on disk."""
    out = tempfile.mkdtemp()
    calls = []
    saved = (sys.argv, scraper.ATS_BOARDS, scraper.FEEDS,
             scraper.SETTINGS["output_dir"])
    if before:
        before(out)
    try:
        sys.argv = ["scraper.py", "--site", "free", "--yes"]
        scraper.ATS_BOARDS = copy.deepcopy(registry or REGISTRY)
        scraper.FEEDS = {}
        scraper.SETTINGS["output_dir"] = out
        log = io.StringIO()
        with _env(**dict(BASE_ENV, **(env or {}))), \
                _served(table or routes(), fail, calls, delay, status, done), \
                contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            scraper.main()
        return Result(out, calls, log.getvalue())
    finally:
        (sys.argv, scraper.ATS_BOARDS, scraper.FEEDS,
         scraper.SETTINGS["output_dir"]) = saved
        shutil.rmtree(out, ignore_errors=True)


def production_view(record):
    """Every field of a telemetry record that describes the user's sweep, less
    the clocks and ids that differ between any two runs."""
    keep = ("path", "family", "board", "ok", "failure_category", "requests",
            "retries", "raw_count", "normalized_count", "source_gate_count",
            "shadow")
    return {"units": [{k: u[k] for k in keep} for u in record["units"]],
            **{k: record[k] for k in (
                "path", "sources_attempted", "sources_succeeded",
                "sources_failed", "raw_rows", "normalized_rows",
                "eligible_rows", "final_rows", "stages", "identity")}}


SHADOW_ON = {shadow.FLAG: "1"}


# ---------------------------------------------------------------------------
# 1. Shadow off
# ---------------------------------------------------------------------------
class ShadowOff(unittest.TestCase):

    def test_no_shadow_request_and_nothing_recorded(self):
        got = sweep()
        self.assertTrue(got.calls)
        self.assertFalse(set(got.calls) & set(EIGHT), got.calls)
        self.assertEqual(got.telemetry["shadow_units"], [])
        self.assertNotIn("shadow_evaluation", got.telemetry)
        self.assertNotIn("shadow", got.log)

    def test_no_artifact_beyond_what_production_writes(self):
        got = sweep()
        names = [f for f in got.files if not f.startswith("telemetry/")]
        self.assertEqual(sorted(n.split("_")[0] for n in names),
                         ["jobs", "jobs", "seen.tsv"], got.files)
        self.assertEqual(len([f for f in got.files
                              if f.startswith("telemetry/")]), 1)

    def test_serial_path_fetches_the_registry_in_order(self):
        got = sweep()
        self.assertEqual(got.calls, ["lever:alpha", "lever:fivetranlever",
                                     "greenhouse:gamma", "greenhouse:vercelmirror"])

    def test_lever_path_fetches_the_registry_and_nothing_else(self):
        seen = []
        real = concurrency._fetch_one

        def spy(platform, token, *a, **kw):
            seen.append(f"{platform}:{token}")
            return real(platform, token, *a, **kw)
        concurrency._fetch_one = spy
        try:
            sweep({concurrency.FLAG: "1"})
        finally:
            concurrency._fetch_one = real
        self.assertEqual(sorted(seen), ["lever:alpha", "lever:fivetranlever"])

    def test_run_is_inert_when_disabled(self):
        calls = []
        with _env(**{shadow.FLAG: None}), _served(routes(), calls=calls):
            self.assertIsNone(shadow.run(lambda t: True, lambda l: True,
                                         log=lambda *a: None))
        self.assertEqual(calls, [])

    def test_on_without_telemetry_fetches_nothing(self):
        """Nowhere to record the evidence means no requests for it."""
        got = sweep({telemetry.FLAG: None, **SHADOW_ON})
        self.assertFalse(set(got.calls) & set(EIGHT), got.calls)
        self.assertIsNone(got.telemetry)
        self.assertIn("shadow tranche skipped", got.log)
        self.assertEqual(got.csv, sweep({telemetry.FLAG: None}).csv)


# ---------------------------------------------------------------------------
# 2. Shadow on: exactly the eight
# ---------------------------------------------------------------------------
class TheEightBoards(unittest.TestCase):

    def test_exactly_the_eight_in_order_and_no_pubmatic(self):
        got = sweep(SHADOW_ON)
        self.assertEqual([c for c in got.calls if c in EIGHT], EIGHT)
        self.assertEqual(set(shadow.BOARDS["greenhouse"]), {
            "fivetran", "abnormalsecurity", "apolloio", "brex", "vercel",
            "jumio", "catawiki", "zetaglobal"})
        self.assertNotIn("pubmatic", shadow.BOARDS["greenhouse"])
        self.assertFalse(any("pubmatic" in c for c in got.calls))
        self.assertEqual([u["board"] for u in got.telemetry["shadow_units"]], EIGHT)
        self.assertEqual(sorted(got.telemetry["shadow_evaluation"]["by_board"]),
                         sorted(EIGHT))

    def test_shadow_is_requested_only_after_the_results_are_written(self):
        """Every production request precedes every shadow request."""
        got = sweep(SHADOW_ON)
        first_shadow = min(i for i, c in enumerate(got.calls) if c in EIGHT)
        self.assertTrue(all(c not in EIGHT for c in got.calls[:first_shadow]))
        self.assertEqual(len(got.calls) - first_shadow, 8)
        self.assertLess(got.log.find("Wrote "), got.log.find("shadow tranche"))


# ---------------------------------------------------------------------------
# 3. Result isolation, end to end
# ---------------------------------------------------------------------------
class ResultIsolation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.off = sweep()
        cls.on = sweep(SHADOW_ON)

    def test_the_fixture_would_expose_a_merge(self):
        """Guard the guard: merged, these shadow rows change the result — a new
        row count AND a different dedupe survivor. Parity below means something
        only because of this."""
        with _env(**BASE_ENV), telemetry_record():
            prod = fetched(REGISTRY, PRODUCTION)
            extra = fetched({"greenhouse": shadow.BOARDS["greenhouse"]},
                            routes_only_shadow())
            alone = scraper.finalize(copy.deepcopy(prod))
            merged = scraper.finalize(copy.deepcopy(prod) + copy.deepcopy(extra))
        self.assertNotEqual(len(alone), len(merged))

        def survivor(rows):
            return next(r["apply_url"] for r in rows if r["company"] == "Fivetran"
                        and r["title"] == "Backend Engineer")
        self.assertIn("jobs.lever.co", survivor(alone))
        self.assertIn("greenhouse.io/fivetran", survivor(merged))

    def test_csv_and_json_byte_identical(self):
        self.assertIsNotNone(self.off.csv)
        self.assertEqual(self.on.csv, self.off.csv)
        self.assertEqual(self.on.json, self.off.json)
        self.assertEqual(self.on.seen, self.off.seen)

    def test_rows_ranking_survivor_and_count_identical(self):
        off, on = json.loads(self.off.json), json.loads(self.on.json)
        self.assertEqual(on, off)
        self.assertEqual([(r["score"], r["apply_url"]) for r in on],
                         [(r["score"], r["apply_url"]) for r in off])
        twin = [r for r in on if r["company"] == "Fivetran"]
        self.assertEqual(len(twin), 1)
        self.assertIn("jobs.lever.co", twin[0]["apply_url"])
        self.assertFalse(any(r["source_site"] in EIGHT for r in on))
        self.assertEqual(self.on.telemetry["final_rows"], len(off))

    def test_the_shadow_was_actually_evaluated(self):
        """Parity from a shadow that silently did nothing proves nothing."""
        ev = self.on.telemetry["shadow_evaluation"]
        self.assertEqual(ev["status"], "evaluated")
        self.assertGreater(ev["totals"]["combined_final"],
                           ev["totals"]["baseline_final"])


# ---------------------------------------------------------------------------
# 4. Production source counts
# ---------------------------------------------------------------------------
class SourceCounts(unittest.TestCase):

    def test_production_fields_identical(self):
        off, on = sweep(), sweep(SHADOW_ON)
        self.assertEqual(production_view(on.telemetry),
                         production_view(off.telemetry))
        self.assertEqual(on.telemetry["sources_attempted"], 4)
        self.assertEqual(on.telemetry["sources_succeeded"], 4)
        self.assertEqual(on.telemetry["sources_failed"], 0)

    def test_shadow_units_are_separate(self):
        record = sweep(SHADOW_ON).telemetry
        self.assertEqual(len(record["shadow_units"]), 8)
        self.assertTrue(all(u["shadow"] for u in record["shadow_units"]))
        self.assertFalse(any(u["shadow"] for u in record["units"]))
        self.assertFalse({u["board"] for u in record["units"]} & set(EIGHT))

    def test_banner_still_counts_the_registry(self):
        got = sweep(SHADOW_ON)
        self.assertIn("Free sources: 4 ATS boards", got.log)


# ---------------------------------------------------------------------------
# 5–7. Failure semantics
# ---------------------------------------------------------------------------
class FailureSemantics(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.off = sweep()

    def test_one_board_failing_costs_that_board_only(self):
        got = sweep(SHADOW_ON, fail={"greenhouse:fivetran"})
        self.assertEqual(got.csv, self.off.csv)
        units = {u["board"]: u for u in got.telemetry["shadow_units"]}
        self.assertFalse(units["greenhouse:fivetran"]["ok"])
        self.assertEqual(units["greenhouse:fivetran"]["failure_category"], "http_503")
        self.assertEqual(sum(u["ok"] for u in units.values()), 7)
        board = got.telemetry["shadow_evaluation"]["by_board"]
        self.assertFalse(board["greenhouse:fivetran"]["fetched"])
        # Unavailable, not zero.
        self.assertIsNone(board["greenhouse:fivetran"]["gated"])
        self.assertIsNone(board["greenhouse:fivetran"]["final_new"])
        self.assertEqual(sum(b["fetched"] for b in board.values()), 7)
        self.assertEqual(board["greenhouse:abnormalsecurity"]["final_new"], 1)

    def test_every_board_failing_still_completes_the_sweep(self):
        got = sweep(SHADOW_ON, fail=set(EIGHT))
        self.assertEqual(got.csv, self.off.csv)
        self.assertEqual(got.json, self.off.json)
        self.assertFalse(any(u["ok"] for u in got.telemetry["shadow_units"]))
        ev = got.telemetry["shadow_evaluation"]
        self.assertEqual(ev["rows_evaluated"], 0)
        self.assertEqual(ev["totals"]["combined_final"],
                         ev["totals"]["baseline_final"])

    def test_an_evaluator_failure_is_recorded_not_raised(self):
        real = shadow._usefulness
        shadow._usefulness = lambda *a: 1 / 0
        try:
            got = sweep(SHADOW_ON)
        finally:
            shadow._usefulness = real
        self.assertEqual(got.csv, self.off.csv)
        ev = got.telemetry["shadow_evaluation"]
        self.assertEqual(ev["status"], "failed")
        self.assertEqual(ev["error_type"], "ZeroDivisionError")
        self.assertIn("results above are unaffected", got.log)

    def test_the_shadow_call_itself_raising_cannot_fail_the_sweep(self):
        real = shadow.run

        def boom(*a, **kw):
            raise RuntimeError("shadow exploded")
        shadow.run = boom
        try:
            got = sweep(SHADOW_ON)
        finally:
            shadow.run = real
        self.assertEqual(got.csv, self.off.csv)
        self.assertIsNotNone(got.telemetry, "the record was not written")
        self.assertIn("shadow tranche skipped: shadow exploded", got.log)

    def test_a_record_that_cannot_be_written_cannot_fail_the_sweep(self):
        def block(out):         # a FILE where the telemetry directory would go
            open(os.path.join(out, "telemetry"), "w").close()
        got = sweep(SHADOW_ON, before=block)
        self.assertEqual(got.csv, self.off.csv)
        self.assertEqual(got.json, self.off.json)
        self.assertIn("telemetry: could not write", got.log)

    def test_over_budget_keeps_acquisition_and_skips_the_funnel(self):
        real = shadow.MAX_EVAL_ROWS
        shadow.MAX_EVAL_ROWS = 1
        try:
            got = sweep(SHADOW_ON)
        finally:
            shadow.MAX_EVAL_ROWS = real
        self.assertEqual(got.csv, self.off.csv)
        ev = got.telemetry["shadow_evaluation"]
        self.assertEqual(ev["status"], "acquisition_only_over_budget")
        self.assertIsNone(ev["totals"])
        fivetran = ev["by_board"]["greenhouse:fivetran"]
        self.assertEqual(fivetran["gated"], 2)
        self.assertIsNone(fivetran["eligible"])


# ---------------------------------------------------------------------------
# Unit-level helpers: the real adapter over fixtures, no main()
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def telemetry_record():
    tmp = tempfile.mkdtemp()
    with _env(**{telemetry.FLAG: "1"}):
        telemetry.start("free", tmp)
        try:
            yield telemetry.record()
        finally:
            telemetry.finish()
            shutil.rmtree(tmp, ignore_errors=True)


def routes_only_shadow(boards=None):
    return {f"greenhouse:{t}": {"jobs": jobs}
            for t, jobs in (SHADOW if boards is None else boards).items()}


def fetched(registry, bodies):
    """Rows exactly as the sweep would hold them: the real adapter over the
    fixture bodies, under the sweep's own predicates, then fetch_free's
    preparation."""
    table = {}
    for board, body in bodies.items():
        platform, token = board.split(":")
        table[ats.ATS[platform]["url"].format(token=token)] = (board, body)
    rows = []
    with _served(table):
        for platform, boards in registry.items():
            for token, company in boards.items():
                rows.extend(ats.fetch(platform, token, company,
                                      scraper.is_dev_title,
                                      scraper.location_allowed,
                                      scraper.is_home_location))
    return [scraper._truncate_desc(r) for r in rows]


def held_for(boards_bodies):
    """[(board, rows)] as shadow.run would hold them."""
    out = []
    for token, company in shadow.BOARDS["greenhouse"].items():
        if token in boards_bodies:
            out.append((f"greenhouse:{token}", fetched(
                {"greenhouse": {token: company}},
                {f"greenhouse:{token}": {"jobs": boards_bodies[token]}})))
    return out


def evaluated(production_registry, production_bodies, shadow_bodies):
    """(section, final_rows, production_rows) for one evaluation, the way
    main() runs it: finalize first, then shadow against its output."""
    with _env(**BASE_ENV), telemetry_record() as record:
        prod = fetched(production_registry, production_bodies)
        final = scraper.finalize(prod)
        held = held_for(shadow_bodies)
        shadow.evaluate(held, scraper.shadow_engine(final, prod))
        return record["shadow_evaluation"], final, prod


# ---------------------------------------------------------------------------
# 8. Overlap
# ---------------------------------------------------------------------------
OVERLAP_REGISTRY = {"lever": {"abnormallever": "Abnormal Security"},
                    "greenhouse": {"apolloalias": "Apollo.io",
                                   "brexalias": "Brex",
                                   "vercelmirror": "Vercel"}}
OVERLAP_PRODUCTION = {
    # Lever id "9001" is the same STRING as a Greenhouse id below, and must not
    # count as the same posting: native ids only compare inside one provider.
    "lever:abnormallever": [lever("9001", "Backend Engineer", "python django")],
    "greenhouse:apolloalias": {"jobs": [gh("apolloalias", 2001, "Frontend Engineer",
                                           "react typescript")]},
    "greenhouse:brexalias": {"jobs": [gh("brexalias", 4001, "Backend Engineer II",
                                         "python django")]},
    "greenhouse:vercelmirror": {"jobs": [gh("vercelmirror", 5001, "Frontend Engineer",
                                            "react typescript")]},
}
OVERLAP_SHADOW = {
    "abnormalsecurity": [gh("abnormalsecurity", 9001, "Backend Engineer",
                            "python django")],   # key = lever row: not comparable
    "apolloio": [gh("apolloio", 3001, "Frontend Engineer",
                    "react typescript")],        # same key, different native id
    "brex": [gh("brex", 4001, "Backend Engineer", "python django")],  # same id, new key
    "vercel": [gh("vercel", 5001, "Frontend Engineer", "react typescript")],  # both
    "fivetran": [gh("fivetran", 7002, "Full Stack Engineer", RICH)],   # neither
}


class Overlap(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.section, cls.final, cls.prod = evaluated(
            OVERLAP_REGISTRY, OVERLAP_PRODUCTION, OVERLAP_SHADOW)
        cls.board = cls.section["by_board"]

    def counts(self, token):
        b = self.board[f"greenhouse:{token}"]
        return {k: b[k] for k in shadow.OVERLAP}

    def test_key_match_against_another_provider_is_not_comparable(self):
        self.assertEqual(self.counts("abnormalsecurity"), {
            "key_overlap": 1, "key_and_native_overlap": 0,
            "key_overlap_native_distinct": 0,
            "key_overlap_native_not_comparable": 1,
            "native_overlap": 0, "native_overlap_key_distinct": 0})

    def test_key_says_duplicate_native_says_distinct(self):
        self.assertEqual(self.counts("apolloio"), {
            "key_overlap": 1, "key_and_native_overlap": 0,
            "key_overlap_native_distinct": 1,
            "key_overlap_native_not_comparable": 0,
            "native_overlap": 0, "native_overlap_key_distinct": 0})

    def test_native_says_duplicate_key_says_distinct(self):
        self.assertEqual(self.counts("brex"), {
            "key_overlap": 0, "key_and_native_overlap": 0,
            "key_overlap_native_distinct": 0,
            "key_overlap_native_not_comparable": 0,
            "native_overlap": 1, "native_overlap_key_distinct": 1})

    def test_both_agree(self):
        self.assertEqual(self.counts("vercel"), {
            "key_overlap": 1, "key_and_native_overlap": 1,
            "key_overlap_native_distinct": 0,
            "key_overlap_native_not_comparable": 0,
            "native_overlap": 1, "native_overlap_key_distinct": 0})

    def test_neither(self):
        self.assertEqual(sum(self.counts("fivetran").values()), 0)

    def test_native_identity_fields(self):
        b = self.board["greenhouse:fivetran"]
        self.assertEqual((b["native_id"], b["native_id_distinct"],
                          b["requisition"], b["url"], b["url_carries_native_id"],
                          b["url_ats_hosted"]), (1, 1, 1, 1, 1, 1))
        self.assertEqual(self.section["production_rows_with_native_id"], 4)

    def test_overlap_changes_no_production_dedupe(self):
        """The same production rows, finalized with no shadow at all."""
        with _env(**BASE_ENV), telemetry_record():
            alone = scraper.finalize(fetched(OVERLAP_REGISTRY, OVERLAP_PRODUCTION))
        self.assertEqual(self.final, alone)

    def test_a_board_that_failed_is_unavailable_not_zero(self):
        for token in ("jumio", "catawiki", "zetaglobal"):
            b = self.board[f"greenhouse:{token}"]
            self.assertFalse(b["fetched"])
            self.assertTrue(all(b[k] is None for k in shadow.OVERLAP))


# ---------------------------------------------------------------------------
# 9. Candidate funnel == frozen offline replay
# ---------------------------------------------------------------------------
class CandidateFunnel(unittest.TestCase):
    """The offline method V2-A used (bench/search_v2_shadow.py) is
    finalize(production + shadow). The live evaluator must agree with it on
    every board, every stage, every count."""

    @classmethod
    def setUpClass(cls):
        with _env(**BASE_ENV), telemetry_record() as record:
            prod = fetched(REGISTRY, PRODUCTION)
            extra = [r for _, rows in held_for(SHADOW) for r in rows]
            base = scraper.finalize(copy.deepcopy(prod))
            cls.truth = scraper.finalize(copy.deepcopy(prod) + copy.deepcopy(extra))
            cls.truth_stages = copy.deepcopy(record["stages"])
            cls.base_keys = {scraper.job_key(r) for r in base}
        cls.section, cls.final, _ = evaluated(REGISTRY, PRODUCTION, SHADOW)

    def test_final_survivors_positive_and_top_n_match(self):
        for board in EIGHT:
            mine = [r for r in self.truth if r["source_site"] == board]
            new = [r for r in mine if scraper.job_key(r) not in self.base_keys]
            top = [r for r in self.truth[:shadow.TOP_N] if r in new]
            got = self.section["by_board"][board]
            self.assertEqual(
                (got["final_new"], got["final_replaces"],
                 got["final_new_positive"], got["top_n_new"]),
                (len(new), len(mine) - len(new),
                 sum(r["score"] > 0 for r in new), len(top)), board)
        totals = self.section["totals"]
        self.assertEqual(totals["combined_final"], len(self.truth))
        self.assertEqual(totals["combined_positive"],
                         sum(r["score"] > 0 for r in self.truth))

    def test_every_stage_matches_the_production_chain(self):
        for board in EIGHT:
            got = self.section["by_board"][board]["funnel"]
            for stage, counts in self.truth_stages.items():
                if stage == "final_after_dedupe":
                    continue
                self.assertEqual(got[stage], counts["by_source"].get(board, 0),
                                 f"{board} {stage}")
            self.assertEqual(self.section["by_board"][board]["eligible"],
                             got["post_location_eligible"])

    def test_the_fixture_exercises_every_exit(self):
        b = self.section["by_board"]
        self.assertEqual(b["greenhouse:catawiki"]["gated"], 0)          # gate
        self.assertEqual(b["greenhouse:brex"]["funnel"]
                         ["post_hard_filter_and_score"], 0)          # hard drop
        self.assertEqual(b["greenhouse:jumio"]["funnel"]["post_recency"], 0)
        self.assertEqual(b["greenhouse:jumio"]["fresh"], 0)
        self.assertEqual(b["greenhouse:apolloio"]["eligible"], 1)
        self.assertEqual(b["greenhouse:apolloio"]["eligible_positive"], 0)
        self.assertEqual(b["greenhouse:fivetran"]["final_replaces"], 1)
        self.assertEqual(b["greenhouse:vercel"]["eligible"], 1)
        self.assertEqual(b["greenhouse:vercel"]["final_new"]
                         + b["greenhouse:vercel"]["final_replaces"], 0)
        self.assertIsNone(b["greenhouse:zetaglobal"]["best_new_rank"])
        self.assertEqual(b["greenhouse:zetaglobal"]["gated"], 0)

    def test_production_stages_and_stats_untouched(self):
        with _env(**BASE_ENV), telemetry_record() as record:
            prod = fetched(REGISTRY, PRODUCTION)
            final = scraper.finalize(prod)
            before = copy.deepcopy({k: record[k] for k in (
                "stages", "normalized_rows", "eligible_rows", "final_rows",
                "milestones")})
            stats = dict(scraper.LAST_STATS)
            shadow.evaluate(held_for(SHADOW), scraper.shadow_engine(final, prod))
            after = {k: record[k] for k in before}
        self.assertEqual(after, before)
        self.assertEqual(dict(scraper.LAST_STATS), stats)

    def test_evaluator_mutates_no_production_row(self):
        with _env(**BASE_ENV), telemetry_record():
            prod = fetched(REGISTRY, PRODUCTION)
            final = scraper.finalize(prod)
            snapshot = copy.deepcopy((final, prod))
            ids = ([id(r) for r in final], [id(r) for r in prod])
            shadow.evaluate(held_for(SHADOW), scraper.shadow_engine(final, prod))
        self.assertEqual((final, prod), snapshot)
        self.assertEqual(([id(r) for r in final], [id(r) for r in prod]), ids)

    def test_experience_guard_record_is_restored(self):
        drop = {"fivetran": [gh("fivetran", 7009, "Backend Engineer",
                               "We require 8+ years of experience. python")]}
        was = scraper.SETTINGS.get("candidate_experience_months")
        scraper.SETTINGS["candidate_experience_months"] = 37
        try:
            with _env(**dict(BASE_ENV, **{experience_guard.FLAG: "1"})), \
                    telemetry_record():
                rows = held_for(drop)[0][1]
                mark = len(experience_guard.DROPPED)
                # Guard the guard: the production chain DOES record this row.
                scraper.score_and_filter(copy.deepcopy(rows), lambda *a: None)
                self.assertGreater(len(experience_guard.DROPPED), mark)
                del experience_guard.DROPPED[mark:]
                shadow.evaluate(held_for(drop), scraper.shadow_engine([], []))
                self.assertEqual(len(experience_guard.DROPPED), mark)
        finally:
            scraper.SETTINGS["candidate_experience_months"] = was


# ---------------------------------------------------------------------------
# 10. Top-N
# ---------------------------------------------------------------------------
class TopN(unittest.TestCase):
    """Against a 25-row result scored 34..10, with the scores preset so the
    ranks are exact. The real rank_rows, job_key and to_output do the work."""

    @classmethod
    def setUpClass(cls):
        def row(i, score, source="lever:x", company=None, title=None):
            return {"Title": title or f"Role {i}", "Company": company or f"Co{i}",
                    "Source": source, "Job URL": f"https://x/{source}/{i}",
                    "Location": "Remote", "Description": "", "preset": score,
                    "score": score}
        production = [row(i, 34 - i) for i in range(25)]
        final = scraper.rank_rows([scraper.to_output(r) for r in production])
        held = [
            ("greenhouse:fivetran", [row(100, 24, "greenhouse:fivetran")]),
            # Ties the 20th production row: the tie goes to production.
            ("greenhouse:abnormalsecurity",
             [row(101, 15, "greenhouse:abnormalsecurity")]),
            # The same posting as production's 12-point row, scored 33.
            ("greenhouse:apolloio", [row(102, 33, "greenhouse:apolloio",
                                         company="Co22", title="Role 22")]),
            # The same posting as production's best row, at the same score.
            ("greenhouse:vercel", [row(103, 34, "greenhouse:vercel",
                                       company="Co0", title="Role 0")]),
        ]

        def preset(rows, stage):
            for r in rows:
                r["score"] = r["preset"]
            stage("post_location_eligible", rows)
            return rows, {}
        engine = scraper.shadow_engine(final, production)._replace(
            score_and_filter=preset)
        cls.final = final
        with _env(**BASE_ENV), telemetry_record() as record:
            shadow.evaluate(held, engine)
            cls.section = record["shadow_evaluation"]
        cls.board = cls.section["by_board"]

    def test_baseline_top_n_is_the_real_result(self):
        self.assertEqual([r["score"] for r in self.final[:20]], list(range(34, 14, -1)))
        self.assertEqual(self.section["totals"]["baseline_top_n"], 20)

    def test_a_new_row_enters_at_its_real_rank(self):
        b = self.board["greenhouse:fivetran"]
        self.assertEqual((b["final_new"], b["top_n_new"], b["best_new_rank"]),
                         (1, 1, 13))

    def test_a_tie_at_the_cut_goes_to_production(self):
        b = self.board["greenhouse:abnormalsecurity"]
        self.assertEqual((b["final_new"], b["top_n_new"], b["best_new_rank"]),
                         (1, 0, 23))

    def test_a_better_twin_replaces_and_is_not_counted_as_new(self):
        b = self.board["greenhouse:apolloio"]
        self.assertEqual((b["final_new"], b["final_replaces"], b["top_n_replaces"],
                          b["top_n_new"]), (0, 1, 1, 0))

    def test_an_equal_twin_loses_to_production(self):
        b = self.board["greenhouse:vercel"]
        self.assertEqual((b["final_new"], b["final_replaces"]), (0, 0))

    def test_totals(self):
        t = self.section["totals"]
        self.assertEqual(t["baseline_final"], 25)
        self.assertEqual(t["combined_final"], 27)       # two new, one swap
        self.assertEqual(t["top_n_new"], 1)
        self.assertEqual(t["baseline_top_n_retained"], 18)

    def test_the_result_list_was_not_reordered(self):
        self.assertEqual([r["score"] for r in self.final], list(range(34, 9, -1)))


# ---------------------------------------------------------------------------
# 11. Privacy
# ---------------------------------------------------------------------------
MARKERS = ("QQTITLEMARK", "QQDESCMARK", "QQURLMARK", "QQCOMPANYMARK",
           "QQSECRETTOKEN")


class Privacy(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        marked = {}
        for token, jobs in SHADOW.items():
            marked[token] = [dict(j, title=f"{j['title']} QQTITLEMARK",
                                  content=f"{j['content']} QQDESCMARK",
                                  absolute_url=f"{j['absolute_url']}?QQURLMARK",
                                  company_name="QQCOMPANYMARK") for j in jobs]
        cls.got = sweep(dict(SHADOW_ON, APIFY_TOKEN="QQSECRETTOKEN"),
                        table=routes(shadow_boards=marked))

    def test_no_row_text_or_credential_reaches_the_record(self):
        body = self.got.telemetry_text
        self.assertIn("shadow_evaluation", body)
        for marker in MARKERS:
            self.assertNotIn(marker, body)
        # Skills, titles and descriptions of the fixtures, never written.
        for text in ("django", "typescript", "Full Stack Engineer", "matched"):
            self.assertNotIn(text, body)

    def test_every_value_is_bounded_and_every_key_known(self):
        ev = self.got.telemetry["shadow_evaluation"]
        top = {"schema", "status", "arrival", "top_n", "scope", "production_rows",
               "production_rows_with_native_id", "rows_evaluated", "totals",
               "cost", "by_board"}
        self.assertEqual(set(ev), top)
        self.assertEqual(set(ev["by_board"]), set(EIGHT))
        board_keys = ({"fetched"} | set(shadow.ACQUISITION) | set(shadow.OVERLAP)
                      | set(shadow.USEFUL))
        stages = {"observed_normalized", "post_hard_filter_and_score",
                  "post_recency", "post_salary_reachability_visa_eor",
                  "post_arrangement", "post_location_eligible"}
        for fields in ev["by_board"].values():
            self.assertEqual(set(fields), board_keys)
            self.assertLessEqual(set(fields["funnel"] or {}), stages)

        def walk(value):
            if isinstance(value, dict):
                for v in value.values():
                    yield from walk(v)
            elif isinstance(value, list):
                for v in value:
                    yield from walk(v)
            else:
                yield value
        strings = {v for v in walk(ev) if isinstance(v, str)}
        self.assertLessEqual(strings, {shadow.SCHEMA, "evaluated",
                                       "after_production", "worldwide", "remote"})
        self.assertTrue(all(isinstance(v, (int, float, bool, str, type(None)))
                            for v in walk(ev)))

    def test_nothing_is_written_outside_the_run_directory_record(self):
        off = sweep()
        self.assertEqual(
            [f.split("_")[0] for f in self.got.files],
            [f.split("_")[0] for f in off.files])


# ---------------------------------------------------------------------------
# 12. Lever concurrency independence
# ---------------------------------------------------------------------------
class LeverIndependence(unittest.TestCase):

    def test_shadow_boards_never_enter_the_lever_executor(self):
        seen, platforms = [], []
        real_one, real_boards = concurrency._fetch_one, concurrency.fetch_boards

        def spy_one(platform, token, *a, **kw):
            seen.append(f"{platform}:{token}")
            return real_one(platform, token, *a, **kw)

        def spy_boards(platform, *a, **kw):
            platforms.append(platform)
            return real_boards(platform, *a, **kw)
        concurrency._fetch_one = spy_one
        concurrency.fetch_boards = spy_boards
        try:
            on = sweep({concurrency.FLAG: "1", concurrency.WORKERS_ENV: "4",
                        **SHADOW_ON})
        finally:
            concurrency._fetch_one, concurrency.fetch_boards = real_one, real_boards
        self.assertEqual(sorted(seen), ["lever:alpha", "lever:fivetranlever"])
        self.assertEqual(platforms, ["lever"])
        self.assertEqual(len(on.telemetry["shadow_units"]), 8)
        self.assertEqual(on.csv, sweep({concurrency.FLAG: "1",
                                        concurrency.WORKERS_ENV: "4"}).csv)

    def test_worker_count_is_the_lever_setting_alone(self):
        with _env(**{concurrency.WORKERS_ENV: "3", shadow.FLAG: None}):
            off = concurrency.workers()
        with _env(**{concurrency.WORKERS_ENV: "3", shadow.FLAG: "1"}):
            on = concurrency.workers()
        self.assertEqual((off, on), (3, 3))

    def test_shadow_fetches_through_the_adapter_not_the_executor(self):
        with open(shadow.__file__, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("ats.fetch(", body)
        self.assertNotIn("concurrency", body.split('"""', 2)[2])


# ---------------------------------------------------------------------------
# The offline aggregator reads what production writes
# ---------------------------------------------------------------------------
class Aggregator(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from bench import search_v2_shadow_production as agg
        cls.agg = agg
        cls.records = [sweep(SHADOW_ON).telemetry,
                       sweep(SHADOW_ON, fail={"greenhouse:fivetran"}).telemetry,
                       sweep().telemetry]           # shadow off: ignored
        cls.summary = agg.summarise(
            [r for r in cls.records if r.get("shadow_units")])

    def test_schema_matches_the_evaluator(self):
        self.assertEqual(self.agg.SCHEMA, shadow.SCHEMA)

    def test_health_per_board(self):
        fivetran = self.summary["boards"]["greenhouse:fivetran"]
        self.assertEqual((fivetran["runs"], fivetran["ok"], fivetran["success_rate"]),
                         (2, 1, 0.5))
        self.assertEqual(fivetran["failure_categories"], {"http_503": 1})
        self.assertEqual(fivetran["evaluated_runs"], 1)
        self.assertEqual(self.summary["sweeps"]["runs"], 2)
        self.assertEqual(self.summary["sweeps"]["evaluated"], 2)

    def test_usefulness_and_breadth_per_board(self):
        abnormal = self.summary["boards"]["greenhouse:abnormalsecurity"]
        self.assertEqual(abnormal["final_new"]["sum"], 2)
        self.assertEqual(abnormal["runs_with_new_positive"], 2)
        self.assertEqual(abnormal["top_n_new"]["sum"], 2)
        self.assertEqual(self.summary["boards"]["greenhouse:catawiki"]["gated"]["median"], 0)
        self.assertEqual(self.summary["boards"]["greenhouse:vercel"]["native_overlap"], 2)
        (cohort, per), = self.summary["cohorts"].items()
        self.assertIn("remote=worldwide+remote", cohort)
        self.assertEqual(per["greenhouse:abnormalsecurity"]["runs"], 2)

    def test_the_report_holds_no_row_text(self):
        text = self.agg.markdown(self.summary)
        self.assertIn("greenhouse:fivetran", text)
        self.assertIn("http_503×1", text)
        for word in ("Engineer", "django", "jobs.lever.co"):
            self.assertNotIn(word, text)

    def test_load_reads_a_directory_once_per_sweep(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i, record in enumerate(self.records):
                with open(os.path.join(tmp, f"sweep_{i}.json"), "w") as fh:
                    json.dump(record, fh)
            with open(os.path.join(tmp, "sweep_dup.json"), "w") as fh:
                json.dump(self.records[0], fh)
            self.assertEqual(len(self.agg.load([tmp])), 2)


# ---------------------------------------------------------------------------
# 13. Paid isolation
# ---------------------------------------------------------------------------
class PaidIsolation(unittest.TestCase):

    def test_no_paid_path_is_reachable_from_shadow(self):
        with open(shadow.__file__, encoding="utf-8") as fh:
            code = fh.read().split('"""', 2)[2].lower()
        for name in ("apify", "scrape_search", "actor", "build_input"):
            self.assertNotIn(name, code)
        engine = scraper.shadow_engine([], [])
        self.assertNotIn(scraper.scrape_search, list(engine))

    def test_a_shadow_sweep_calls_nothing_paid(self):
        real = scraper.scrape_search

        def refuse(*a, **kw):
            raise AssertionError("a paid actor was reached")
        scraper.scrape_search = refuse
        try:
            got = sweep(SHADOW_ON)
        finally:
            scraper.scrape_search = real
        self.assertEqual(got.telemetry["shadow_evaluation"]["status"], "evaluated")
        self.assertFalse(any(u["path"] == "paid" for u in got.telemetry["units"]))


if __name__ == "__main__":
    unittest.main()
