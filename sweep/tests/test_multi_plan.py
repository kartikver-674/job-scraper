"""Multi-Track Phase 3: the schema-2 acquisition and planning path.

multi_plans() builds each track's units the way plan_for_site builds one
profile's, identifies each by the provider request it would send
(request_key), runs a request several tracks share once with every requester
in its provenance, and interleaves units fairly by track within each site —
the per-site cap cutting the merged list. scrape_search stamps PAID_TRACKS on
every paid row; fetch_free_multi fetches the free sources once through the
union of the tracks' own title gates and stamps FREE; plan_hash binds the plan
Confirm priced, and a stale one exits PLAN_CHANGED before any account is read;
main()'s checkpoints finalize through finalize_multi into MULTI_OUTPUT_COLUMNS.

Schema 1 never enters any of it: test_one_track_goldens pins its plans, and
SchemaOneDispatch here pins the dispatch.

NO NETWORK, NO REAL CLIENT, NO CREDENTIAL. Paid runs go through V2-C2's
KeyedClient and C4.5's PoolClient, free boards are served from fixtures, and
sockets are denied for the module. Tracks, jobs and résumés are synthetic.
"""
import argparse
import ast
import contextlib
import copy
import csv
import dataclasses
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import config
import scraper
import skill_concepts
import telemetry
from sources import feeds, shadow
from sweep import app as app_module
from sweep import plan as plan_mod
from sweep import runs
from sweep.tests import test_paid_concurrency as c2
from sweep.tests import test_paid_multi_account as c45
from sweep.tests import test_paid_observability as c1
from sweep.tests import test_search_v2_shadow_eval as b3
from sweep.tests.test_one_track_goldens import (REPO, _env, derived_for, pool_authorize,
                                                prefs_for)
from sweep.tests.test_track_loading import A as TA
from sweep.tests.test_track_loading import B as TB
from sweep.tests.test_track_loading import C as TC
from sweep.tests.test_track_loading import flags

sys.path.insert(0, os.path.join(REPO, "auto-apply"))
import make_profile  # noqa: E402

setUpModule, tearDownModule = c45.setUpModule, c45.tearDownModule

PAID, FREE, KEY, LEDGER = (scraper.PAID_TRACKS, scraper.FREE, scraper.REQUEST_KEY,
                           scraper.DONE_ID)
MERN, MOBILE, AI = "a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2", "c3c3c3c3c3c3c3c3"
DESC = "React, Node.js, MongoDB, React Native, Kotlin, Python and PyTorch."
TODAY = c1.TODAY


def track(tid, keywords, years=2, hints=("developer",), exclude=(), engine="v2",
          skills=None):
    """A TrackContext from a TRACKS entry of the checked shape."""
    return scraper.track_context({
        "id": tid, "engine": engine,
        "SEARCH": {"role_keywords": list(keywords), "experience_years": years},
        "SETTINGS": {"max_experience_years": years + 3,
                     "candidate_experience_months": years * 12},
        "SCORING": {"skill_weights": dict(skills or {"react": 5}), "penalty_terms": {},
                    "frontend_terms": [], "backend_terms": [],
                    "fullstack_title_terms": [], "fullstack_bonus": 0},
        "ATS_TITLE_HINTS": list(hints), "ATS_TITLE_EXCLUDE": list(exclude)},
        {"hard_drop_terms": ["intern"]})


# MERN and Mobile ask LinkedIn the same "react developer" question (2 years:
# f_E 3); AI asks it at 6 years (f_E 5). Indeed reads no experience, so there
# all three ask one question. MERN's gate excludes "mobile", which Mobile's
# admits.
TRACKS = (
    track(MERN, ["mern stack developer", "react developer", "node.js developer"], 2,
          ["mern", "react", "node", "full stack"], exclude=["mobile"],
          skills={"react": 5, "node.js": 4, "mongodb": 3}),
    track(MOBILE, ["react native developer", "react developer", "android developer"], 2,
          ["mobile", "android", "react native", "ios"],
          skills={"react native": 5, "kotlin": 4}),
    track(AI, ["machine learning engineer", "react developer", "ai engineer"], 6,
          ["machine learning", "ml engineer", "ai engineer"], exclude=["intern"],
          engine="v1", skills={"python": 5, "pytorch": 4}),
)
ORDER = [t.id for t in TRACKS]
LINKEDIN = [("mern stack developer", [MERN]), ("react native developer", [MOBILE]),
            ("machine learning engineer", [AI]), ("react developer", [MERN, MOBILE]),
            ("android developer", [MOBILE]), ("react developer", [AI]),
            ("node.js developer", [MERN]), ("ai engineer", [AI])]
INDEED = [("mern stack developer", [MERN]), ("react native developer", [MOBILE]),
          ("machine learning engineer", [AI]), ("react developer", [MERN, MOBILE, AI]),
          ("android developer", [MOBILE]), ("ai engineer", [AI]),
          ("node.js developer", [MERN])]

# Every Sweep filter off or neutral, so a row's fate is its tracks' alone.
FILTERS = {"max_age_days": 14, "min_comp_usd": None, "min_score": None,
           "drop_undated": False, "work_scope": None, "remote_scopes": [],
           "drop_no_visa": False, "require_eor": False, "drop_excluded": True,
           "experience_aggregate": "max", "home_utc_offset": 5.5,
           "max_searches_per_site": None, "max_spend_usd": None,
           "allow_partial_paid_sweep": None}


@contextlib.contextmanager
def sweep_env(sites=("linkedin", "indeed"), li=("India",), indeed=("Bengaluru",),
              naukri=("Delhi / NCR",), order=None, linkedin=None, **settings):
    """The Sweep layer a schema-2 profile loads: SITES, SEARCH and SETTINGS."""
    base = copy.deepcopy(scraper.SITES)
    places = {"linkedin": li, "indeed": indeed, "naukri": naukri}
    cfg = {k: dict(base[k], enabled=k in sites, locations=list(places[k]))
           for k in (order or base)}
    cfg["linkedin"].update(linkedin or {})
    with mock.patch.object(scraper, "SITES", cfg), \
            mock.patch.dict(scraper.SETTINGS, dict(FILTERS, **settings)), \
            mock.patch.dict(scraper.SEARCH, {"max_results": 15, "country": "IN"}):
        yield cfg


def args(**over):
    return argparse.Namespace(**dict({"keywords": None, "test": False, "limit": None,
                                      "site": None}, **over))


def unified(tracks=TRACKS, **over):
    a = args(**over)
    return scraper.multi_plans(scraper.resolve_sites(a), a, list(tracks))


def shape(units):
    return [(u["keywords"], u[PAID]) for u in units]


def public(unit):
    """A unit as schema 1 builds it: the private schema-2 keys removed."""
    return {k: v for k, v in unit.items() if not k.startswith("_")}


def dry_run(tracks=TRACKS, argv=(), **sweep):
    """main()'s --dry-run --json for a schema-2 Sweep, in process."""
    printed = io.StringIO()
    with sweep_env(**sweep), mock.patch.object(scraper, "TRACK_CONTEXTS", tuple(tracks)), \
            mock.patch.object(sys, "argv", ["scraper.py", "--dry-run", "--json", *argv]), \
            contextlib.redirect_stdout(printed):
        scraper.main()
    return json.loads(printed.getvalue())


# ---------------------------------------------------------------------------
# One offline sweep through scraper.main()
# ---------------------------------------------------------------------------
def item(site, job_id, title, company):
    if site == "linkedin":
        return c1.li(job_id, title, company, DESC, location="Bengaluru, Karnataka")
    return c2.indeed_item(job_id, title, company, DESC, location="Bengaluru, Karnataka")


def default_script(site, unit, index):
    """Every request returns a job of its own; every "react developer" request
    also returns the one job all three tracks reach: React Developer at Probe,
    which the free board carries too."""
    items = [item(site, f"u{index}", unit["keywords"].title(), f"Company {index}")]
    if unit["keywords"] == "react developer":
        items.append(item(site, f"shared{index}", "React Developer", "Probe"))
    return c1.Script(items)


FREE_POSTS = {"lever:probe": [
    b3.lever("f1", "React Developer", DESC, "Bengaluru"),        # MERN's gate
    b3.lever("f2", "Android Developer", DESC, "Bengaluru"),      # Mobile's
    b3.lever("f3", "Sales Manager", DESC, "Bengaluru"),          # nobody's
    b3.lever("f4", "Machine Learning Intern", DESC, "Bengaluru"),  # AI's hint, AI's exclude
    b3.lever("f5", "Mobile Engineer", DESC, "Bengaluru"),        # MERN excludes, Mobile admits
]}


def unit_of(url):
    """The plan index a scripted row came from (default_script's job ids)."""
    return int(re.search(r"(?:u|shared)(\d+)$", url).group(1))


class Swept:
    def __init__(self, out, log, client, exit_code, error, checkpoints, last, plans,
                 stats, himalayas):
        self.log, self.client, self.exit_code, self.error = log, client, exit_code, error
        self.checkpoints, self.last, self.plans, self.stats = checkpoints, last, plans, stats
        self.himalayas = himalayas
        found = sorted(glob.glob(os.path.join(out, "jobs_*.csv")), key=os.path.getmtime)
        self.csv = Path(found[-1]).read_text(encoding="utf-8-sig") if found else None
        self.header = next(csv.reader(io.StringIO(self.csv))) if self.csv else None
        self.rows = list(csv.DictReader(io.StringIO(self.csv))) if self.csv else []
        done = os.path.join(out, ".done_combos")
        self.done = Path(done).read_text().splitlines() if os.path.exists(done) else []
        auth = os.path.join(out, scraper.AUTH_RECORD)
        self.auth = json.loads(Path(auth).read_text()) if os.path.exists(auth) else None
        records = glob.glob(os.path.join(out, "telemetry", "sweep_*.json"))
        self.telemetry = json.loads(Path(records[0]).read_text()) if records else None
        self.files = sorted(os.listdir(out))
        self.starts = [c for c in client.calls if c[0] == "start"]

    def units(self):
        return [u for units in self.plans.values() for u in units]

    def row(self, company, title):
        return next(r for r in self.rows if r["company"] == company and r["title"] == title)


def _forbidden(*_a, **_k):
    raise AssertionError("a Multi-Track Sweep reached one-profile scoring")


def _no_dotenv(*_a, **_k):
    raise AssertionError("a BYOK run read .env")


def multi_sweep(tracks=TRACKS, script=default_script, *, free=None, workers=c2.SERIAL,
                accounts=None, byok=False, partial=False, env=None, patches=(), argv=(),
                telemetry_on=False, search=None, out=None, **sweep):
    """A sweep of `tracks` (schema 2; () runs the schema-1 path over `search`)
    through scraper.main(), offline. The provider answers each planned request
    with script(site, unit, plan index). `accounts` (c45.Acct) put the paid
    phase through the account pool — the visitor's keys on stdin when `byok`."""
    keep = out is not None
    out = out or tempfile.mkdtemp(prefix="multi-plan-")
    checkpoints, last, stats, himalayas = [], [], {}, []
    real_finalize = scraper.finalize_multi

    def recording(rows, tracks_):
        checkpoints.append([(r.get("Job URL"), list(r[PAID]) if PAID in r else None,
                             r.get(FREE)) for r in rows])
        last[:] = rows
        return real_finalize(rows, tracks_)

    def serve_himalayas(url, **_kw):
        himalayas.append(url)
        return {"jobs": []}

    exit_code = error = None
    try:
        with sweep_env(**sweep) as cfg, contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(scraper, "TRACK_CONTEXTS", tuple(tracks)))
            stack.enter_context(mock.patch.dict(scraper.SEARCH, search or {}))
            stack.enter_context(mock.patch.dict(scraper.SETTINGS, output_dir=out,
                                                allow_partial_paid_sweep=partial or None))
            for name, value in (("ATS_BOARDS", {"lever": {"probe": "Probe"}} if free else {}),
                                ("FEEDS", {}), ("OPTUM", {}), ("ENTERPRISE", {}),
                                ("LOCATION_HINTS", [])):
                stack.enter_context(mock.patch.object(scraper, name, value))
            stack.enter_context(mock.patch.object(
                sys, "argv", ["scraper.py", "--yes", *argv] + ([] if free else ["--no-free"])))
            a = scraper.parse_args()
            sites = scraper.resolve_sites(a)
            plans = (scraper.multi_plans(sites, a, list(tracks)) if tracks
                     else {s: scraper.plan_for_site(s, a) for s in sites})
            plans = {s: units for s, units in plans.items() if units}
            by_input = {}
            for index, (site, unit) in enumerate(
                    (s, u) for s, units in plans.items() for u in units):
                run_input = scraper.build_input(site, scraper.effective_search(site, unit))
                by_input[c2.input_key(cfg[site]["actor"], run_input)] = (
                    index, script(site, unit, index))
            secrets = c45.SECRETS[:len(accounts or ())]
            client = (c45.PoolClient(by_input, accounts=dict(zip(secrets, accounts)))
                      if accounts else c2.KeyedClient(by_input))
            environment = dict(b3.BASE_ENV, **{telemetry.FLAG: "1" if telemetry_on else None,
                                               scraper.PLAN_HASH_ENV: None,
                                               scraper.PUBLIC_PAID_FLAG: None,
                                               scraper.BYOK_ENV: None,
                                               scraper.PAID_MULTI_ACCOUNT_FLAG: None})
            if workers is not c2.SERIAL:
                environment.update({scraper.PAID_CONCURRENCY_FLAG: "1",
                                    scraper.PAID_WORKERS_ENV: str(workers)})
            if accounts:
                environment[scraper.PAID_MULTI_ACCOUNT_FLAG] = "1"
            if byok:
                environment[scraper.BYOK_ENV] = "stdin"
            environment.update(env or {})
            stack.enter_context(b3._env(**environment))
            if byok:
                stdin = io.StringIO(json.dumps({"apify_tokens": list(secrets)}))
                stack.enter_context(mock.patch.object(scraper, "_byok", None))
                stack.enter_context(mock.patch.object(scraper.sys, "stdin", stdin))
                stack.enter_context(mock.patch("dotenv.load_dotenv", _no_dotenv))
            elif accounts:
                stack.enter_context(mock.patch.object(
                    scraper, "_require_token_pool",
                    lambda: [(c45.slot(i), s) for i, s in enumerate(secrets)]))
            stack.enter_context(mock.patch.object(
                scraper, "_require_token",
                c45.forbid_single if accounts else (lambda: c1.TOKEN)))
            stack.enter_context(mock.patch("apify_client.ApifyClient", client))
            stack.enter_context(mock.patch.object(scraper.time, "sleep", lambda *_: None))
            stack.enter_context(mock.patch.object(feeds, "get_json", serve_himalayas))
            if tracks:
                stack.enter_context(mock.patch.object(scraper, "finalize_multi", recording))
                stack.enter_context(mock.patch.object(scraper, "score_job", _forbidden))
            if free:
                stack.enter_context(b3._served(b3.routes(free, {})))
            for p in patches:
                stack.enter_context(p)
            log = io.StringIO()
            stack.enter_context(contextlib.redirect_stdout(log))
            stack.enter_context(contextlib.redirect_stderr(log))
            try:
                scraper.main()
            except SystemExit as exc:
                exit_code = exc.code
            except Exception as exc:           # e.g. a checkpoint refusing its rows
                error = exc
            finally:
                c2._join_workers()
                stats.update(scraper.LAST_STATS)
        return Swept(out, log.getvalue(), client, exit_code, error, checkpoints, list(last),
                     plans, stats, himalayas)
    finally:
        if not keep:
            shutil.rmtree(out, ignore_errors=True)


# ===========================================================================
class RequestIdentity(unittest.TestCase):
    """request_key: one search if and only if the built request is the same."""

    UNIT = {"keywords": "react developer", "location": "India", "company": None,
            "country": "IN", "experience_years": 2, "salary_min": None, "max_results": 15}

    def key(self, site="linkedin", **over):
        return scraper.request_key(site, dict(self.UNIT, **over))

    def test_an_identical_request_collapses_and_keeps_every_requester(self):
        with sweep_env():
            plans = unified()
        self.assertEqual(shape(plans["linkedin"]), LINKEDIN)     # f_E splits AI's
        self.assertEqual(shape(plans["indeed"]), INDEED)         # Indeed reads no f_E

    def test_every_dimension_a_provider_reads_splits_the_key(self):
        with sweep_env(li=("India", "Remote")) as cfg:
            base = self.key()
            self.assertRegex(base, r"^[0-9a-f]{64}$")
            self.assertEqual(self.key(), base)                           # deterministic
            split = {"f_E": self.key(experience_years=6),
                     "geo": self.key(location="United Kingdom"),
                     "remote location": self.key(location="Remote"),
                     "depth": self.key(max_results=25),
                     "keyword case (no folding)": self.key(keywords="React Developer"),
                     "company": self.key(company="Microsoft"),
                     "indeed country": self.key("indeed", location="United Kingdom")}
            with mock.patch.dict(scraper.SETTINGS, max_age_days=7):
                split["recency"] = self.key()
            with mock.patch.dict(cfg["linkedin"], remote_only=True):
                split["remote_only"] = self.key()
            with mock.patch.dict(cfg["linkedin"], actor="someone/else-linkedin"):
                split["actor"] = self.key()
            with mock.patch.dict(scraper.ACTOR_CHARGE_MODEL["linkedin"],
                                 overshoot=Decimal("2")):
                split["ceiling"] = self.key()
            indeed = self.key("indeed", location="Bengaluru")
        for what, key in split.items():
            self.assertNotEqual(key, indeed if what == "indeed country" else base, what)
        # "Remote" and "India" + remote_only build one URL (f_WT=2 in India):
        # one request, so one key (design §E). Every other pair differs.
        self.assertEqual(split.pop("remote_only"), split["remote location"])
        self.assertEqual(len(set(split.values())), len(split))

    def test_a_dimension_no_adapter_reads_collapses(self):
        with sweep_env():
            self.assertEqual(self.key(salary_min=90000), self.key())
            self.assertEqual(self.key(experience_years=3), self.key(experience_years=5))
            indeed = self.key("indeed", location="Bengaluru")
            self.assertEqual(self.key("indeed", location="Bengaluru", experience_years=9), indeed)
            self.assertEqual(self.key("indeed", location="Bengaluru", company="IBM"), indeed)

    def test_it_hashes_exactly_what_scrape_search_sends(self):
        sent = {}

        class Stop(Exception):
            pass

        class Actor:
            def start(self, *, run_input=None, run_timeout=None, max_total_charge_usd=None):
                sent.update(input=run_input, timeout=run_timeout,
                            ceiling=max_total_charge_usd)
                raise Stop
        with sweep_env():
            unit = unified()["linkedin"][3]
            actor = scraper.SITES["linkedin"]["actor"]
            with self.assertRaises(Stop):
                scraper.scrape_search(SimpleNamespace(actor=lambda _id: Actor()), "linkedin",
                                      actor, unit)
        self.assertEqual(sent["timeout"], timedelta(seconds=scraper.RUN_TIMEOUT_S))
        self.assertEqual(unit[KEY], scraper._sha256_json(
            {"actor": actor, "input": sent["input"], "ceiling": str(sent["ceiling"]),
             "timeout_s": scraper.RUN_TIMEOUT_S}))


# ===========================================================================
class Ordering(unittest.TestCase):
    """Site-major, then one unit per track in turn, the cap on the merged list."""

    def plan_of(self, *keyword_lists, **over):
        tracks = [track(str(i + 1) * 16, kws) for i, kws in enumerate(keyword_lists)]
        with sweep_env(sites=("linkedin",)):
            return [(u["keywords"], [t[0] for t in u[PAID]])
                    for u in unified(tracks, **over)["linkedin"]]

    def test_one_unit_from_each_track_in_turn(self):
        self.assertEqual(self.plan_of(["a1", "a2", "a3"], ["b1", "b2"], ["c1"]),
                         [("a1", ["1"]), ("b1", ["2"]), ("c1", ["3"]), ("a2", ["1"]),
                          ("b2", ["2"]), ("a3", ["1"])])

    def test_a_shared_request_takes_the_earliest_slot_and_costs_no_turn(self):
        self.assertEqual(self.plan_of(["shared", "a1", "a2"], ["b1", "shared", "b2"]),
                         [("shared", ["1", "2"]), ("b1", ["2"]), ("a1", ["1"]),
                          ("b2", ["2"]), ("a2", ["1"])])
        # Reached first by the second track: that slot, both requesters, track order.
        self.assertEqual(self.plan_of(["a1", "shared"], ["shared", "b1"]),
                         [("a1", ["1"]), ("shared", ["1", "2"]), ("b1", ["2"])])

    def test_each_track_keeps_its_own_order(self):
        with sweep_env(li=("India", "Bengaluru"), indeed=("Bengaluru", "Pune")):
            plans = unified()
            for site, units in plans.items():
                pos = {u[KEY]: i for i, u in enumerate(units)}
                for t in TRACKS:
                    own = [scraper.request_key(site, s)
                           for s in scraper.track_plan(site, args(), t)]
                    self.assertLessEqual(set(own), set(pos))             # nothing lost
                    for i, key in enumerate(own):
                        for later in own[i + 1:]:
                            if pos[later] < pos[key]:   # only a shared one moves up
                                self.assertGreater(len(units[pos[later]][PAID]), 1)
                    alone = [k for k in own if units[pos[k]][PAID] == [t.id]]
                    self.assertEqual(sorted(alone, key=pos.get), alone)

    def test_the_cap_cuts_the_merged_plan_never_one_track(self):
        with sweep_env():
            full = unified()
            by_flag = unified(limit=4)
            with mock.patch.dict(scraper.SETTINGS, max_searches_per_site=4):
                by_setting = unified()
        for capped in (by_flag, by_setting):
            self.assertEqual(capped, {site: units[:4] for site, units in full.items()})
        # A requester whose own turn at a request falls past the cap still owns it.
        self.assertEqual(self.plan_of(["a1", "a2", "a3", "shared"], ["shared"], limit=2),
                         [("a1", ["1"]), ("shared", ["1", "2"])])

    def test_site_order_is_unchanged(self):
        with sweep_env():
            self.assertEqual(list(unified()), ["linkedin", "indeed"])
        with sweep_env(order=("indeed", "linkedin", "naukri")):
            self.assertEqual(list(unified()), ["indeed", "linkedin"])

    def test_one_track_is_plan_for_site(self):
        one = scraper.TrackContext(
            id="d4" * 8, role_keywords=tuple(scraper.SEARCH["role_keywords"]),
            experience_years=scraper.SEARCH["experience_years"], title_hints=(),
            title_exclude=(), scoring=TRACKS[0].scoring)
        cases = [({}, {}),
                 ({"li": ("India", "Bengaluru"), "indeed": ("Remote", "Pune")}, {"limit": 3}),
                 ({}, {"test": True}),
                 ({}, {"keywords": "alpha engineer, beta engineer"}),
                 ({"sites": ("linkedin", "indeed", "naukri"),
                   "naukri": ("Delhi / NCR", "Remote")}, {}),
                 ({"linkedin": {"companies": ["Microsoft", "IBM"], "remote_only": True}}, {})]
        for env, over in cases:
            with self.subTest(env=env, over=over), sweep_env(**env):
                a = args(**over)
                legacy = {s: scraper.plan_for_site(s, a) for s in scraper.resolve_sites(a)}
                multi = scraper.multi_plans(scraper.resolve_sites(a), a, [one])
                self.assertEqual({s: [public(u) for u in us] for s, us in multi.items()},
                                 legacy)
                self.assertTrue(all(u[PAID] == [one.id] for us in multi.values() for u in us))

    def test_a_hand_written_repeat_runs_once(self):
        """Design §E: with remote_only, LinkedIn's "India" and "Remote" are one
        request (f_WT=2 in India). A hand-written schema-1 profile runs it
        twice; a schema-2 track runs it once. Sweep-generated location lists
        repeat no geography (FreshInterpreters' parity covers every scope)."""
        with sweep_env(sites=("linkedin",), li=("India", "Remote"),
                       linkedin={"remote_only": True}), \
                mock.patch.dict(scraper.SEARCH, role_keywords=list(TRACKS[0].role_keywords),
                                experience_years=2):
            legacy = scraper.plan_for_site("linkedin", args())
            multi = unified([TRACKS[0]])["linkedin"]
        self.assertEqual((len(legacy), len(multi)), (6, 3))
        self.assertEqual([public(u) for u in multi], legacy[::2])
        self.assertEqual([u[PAID] for u in multi], [[MERN]] * 3)    # asked twice, listed once


# ===========================================================================
class PaidProvenance(unittest.TestCase):
    """Every paid row integrated in a schema-2 run carries its request's
    requesters — on every path main() can integrate paid rows through."""

    PATHS = {"serial": {}, "c2": {"workers": 2},
             "pool": {"workers": 2, "accounts": [c45.Acct("u1"), c45.Acct("u2")]},
             "byok": {"byok": True, "workers": 2,
                      "accounts": [c45.Acct("u1"), c45.Acct("u2")]},
             "byok serial": {"byok": True, "workers": c2.SERIAL, "accounts": [c45.Acct("u1")]}}

    def test_every_paid_row_carries_its_units_requesters_on_every_path(self):
        for name, how in self.PATHS.items():
            with self.subTest(path=name):
                got = multi_sweep(**how)
                self.assertIsNone(got.error)
                self.assertIsNone(got.exit_code, got.log[-2000:])
                units = got.units()
                self.assertEqual(len(got.starts), len(units))           # each request once
                self.assertEqual(len({json.dumps(c[2], sort_keys=True) for c in got.starts}),
                                 len(units))
                final = got.checkpoints[-1]
                self.assertEqual(len(final), 18)                  # 15 units + 3 shared rows
                for url, tracks, free in final:
                    self.assertEqual(tracks, units[unit_of(url)][PAID], url)
                    self.assertIsNone(free, url)
                # Repeated checkpoints never change a row's provenance.
                for earlier in got.checkpoints:
                    self.assertEqual(earlier, final[:len(earlier)])

    def test_each_row_holds_its_own_list(self):
        calls = []
        real = scraper.scrape_search

        def spy(client, site, actor, search, *a, **kw):
            rows, cost = real(client, site, actor, search, *a, **kw)
            calls.append((search, rows))
            return rows, cost
        multi_sweep(workers=2, patches=[mock.patch.object(scraper, "scrape_search", spy)])
        self.assertEqual(len(calls), 15)
        for search, rows in calls:
            for row in rows:
                self.assertEqual(row[PAID], search[PAID])
                self.assertIsNot(row[PAID], search[PAID])
            self.assertEqual(len({id(r[PAID]) for r in rows}), len(rows))

    def test_a_row_that_lost_its_stamp_never_reaches_a_written_checkpoint(self):
        real = scraper.scrape_search

        def unstamped(client, site, actor, search, *a, **kw):
            rows, cost = real(client, site, actor, search, *a, **kw)
            if search[PAID] == [MOBILE]:
                for row in rows:
                    row.pop(PAID)
            return rows, cost
        got = multi_sweep(patches=[mock.patch.object(scraper, "scrape_search", unstamped)])
        self.assertIsInstance(got.error, ValueError)
        self.assertIn("needs exactly one of _tracks", str(got.error))
        # Plan unit 0 (MERN's) checkpointed; unit 1 (Mobile's) broke every pass after.
        self.assertEqual(got.done, [f"{TODAY}|{got.units()[0][LEDGER]}"])
        self.assertEqual([unit_of(r["apply_url"]) for r in got.rows], [0])


# ===========================================================================
class FreeAcquisition(unittest.TestCase):
    """One fetch through the union of the tracks' own gates; every row FREE."""

    def fetch(self, posts, tracks=TRACKS, hints=(), feed_cfg=None):
        urls = []
        with mock.patch.object(scraper, "ATS_BOARDS", {"lever": {"probe": "Probe"}}), \
                mock.patch.object(scraper, "FEEDS", feed_cfg or {}), \
                mock.patch.object(scraper, "OPTUM", {}), \
                mock.patch.object(scraper, "ENTERPRISE", {}), \
                mock.patch.object(scraper, "LOCATION_HINTS", list(hints)), \
                mock.patch.object(feeds, "get_json",
                                  lambda url, **kw: (urls.append(url), {"jobs": []})[1]), \
                b3._env(**dict(b3.BASE_ENV, **{telemetry.FLAG: None})), \
                b3._served(b3.routes({"lever:probe": posts}, {})), \
                contextlib.redirect_stdout(io.StringIO()):
            rows = (scraper.fetch_free_multi(list(tracks)) if tracks is not None
                    else scraper.fetch_free())
        return rows, urls

    def test_kept_when_any_tracks_own_gate_admits_it(self):
        rows, _ = self.fetch(FREE_POSTS["lever:probe"])
        self.assertEqual([r["Title"] for r in rows],
                         ["React Developer", "Android Developer", "Mobile Engineer"])
        for row in rows:
            self.assertIs(row[FREE], True)
            self.assertNotIn(PAID, row)
        # Without Mobile, MERN's exclude decides "Mobile Engineer" alone.
        alone, _ = self.fetch(FREE_POSTS["lever:probe"], tracks=TRACKS[:1])
        self.assertEqual([r["Title"] for r in alone], ["React Developer"])

    def test_the_location_gate_stays_the_sweeps(self):
        posts = [b3.lever("g1", "React Developer", DESC, "Bengaluru"),
                 b3.lever("g2", "React Developer II", DESC, "Berlin, Germany")]
        rows, _ = self.fetch(posts, hints=["bengaluru"])
        self.assertEqual([r["Title"] for r in rows], ["React Developer"])

    def test_one_track_fetches_exactly_what_its_schema_1_profile_does(self):
        titles = ["Senior Software Engineer", "React Developer", "Data Engineer",
                  "Sales Manager", "QA Automation Engineer", "Full Stack Developer (MERN)",
                  "Machine Learning Engineer", "Android Developer", "Engineering Manager",
                  "DevOps Engineer", "Frontend Developer - ReactJS", "HR Business Partner",
                  "Java FSD", "AI/ML Engineer", "Customer Success Lead"]
        posts = [b3.lever(f"t{i}", t, DESC, "Bengaluru") for i, t in enumerate(titles)]
        kws = [f"role {i}" for i in range(11)]
        one = scraper.TrackContext(id="d4" * 8, role_keywords=tuple(kws), experience_years=2,
                                   title_hints=tuple(scraper.ATS_TITLE_HINTS),
                                   title_exclude=tuple(scraper.ATS_TITLE_EXCLUDE),
                                   scoring=TRACKS[0].scoring)
        rendered = ast.literal_eval(make_profile._fmt_feeds(kws).split("=", 1)[1])
        legacy, legacy_urls = self.fetch(posts, tracks=None, feed_cfg=rendered)
        multi, multi_urls = self.fetch(posts, tracks=[one],
                                       feed_cfg=copy.deepcopy(config.FEEDS))
        self.assertGreater(len(legacy), 3)
        self.assertLess(len(legacy), len(titles))
        self.assertEqual([{k: v for k, v in r.items() if k != FREE} for r in multi], legacy)
        himalayas = [u for u in multi_urls if "himalayas" in u]
        self.assertEqual(himalayas, [u for u in legacy_urls if "himalayas" in u])
        self.assertEqual(len(himalayas), 8)

    def test_every_free_path_is_stamped(self):
        made = [{"Source": s, "Title": "React Developer", "Description": "x" * 99999}
                for s in ("lever:x", "greenhouse:y", "remoteok", "himalayas", "optum",
                          "enterprise:z")]
        with mock.patch.object(scraper.sources, "fetch_free", return_value=made):
            rows = scraper.fetch_free_multi(list(TRACKS))
        self.assertEqual(len(rows), len(made))
        for row in rows:
            self.assertIs(row[FREE], True)
            self.assertNotIn(PAID, row)
            self.assertLessEqual(len(row["Description"]),
                                 scraper.SETTINGS["description_max"] + 1)

    def test_schema_1_fetch_free_is_todays(self):
        with mock.patch.object(scraper.sources, "fetch_free", return_value=[
                {"Source": "lever:x", "Title": "React Developer", "Description": "d"}]) as got:
            rows = scraper.fetch_free()
        passed = got.call_args.args
        self.assertIs(passed[0], scraper.ATS_BOARDS)
        self.assertIs(passed[1], scraper.FEEDS)
        self.assertIs(passed[2], scraper.is_dev_title)
        self.assertIs(passed[3], scraper.location_allowed)
        self.assertNotIn(FREE, rows[0])


# ===========================================================================
class Himalayas(unittest.TestCase):

    def test_round_robin_exact_dedupe_and_one_total_cap(self):
        a = track("1" * 16, ["A1", "A2", "A3", "A4"])
        b = track("2" * 16, ["B1", "A1", "B2", "B3"])
        c = track("3" * 16, ["C1", " ", "C2", "C3"])
        queries = scraper.himalayas_queries([a, b, c])
        self.assertEqual(queries, ["A1", "B1", "C1", "A2", "B2", "C2", "A3", "B3", "C3", "A4"])
        urls = feeds._himalayas_urls({"queries": queries})
        self.assertEqual([u.rsplit("q=", 1)[1] for u in urls],
                         ["A1", "B1", "C1", "A2", "B2", "C2", "A3", "B3"])      # 8 in total

    def test_one_track_is_its_first_eight(self):
        kws = [f"role {i}" for i in range(11)]
        rendered = ast.literal_eval(make_profile._fmt_feeds(kws).split("=", 1)[1])["himalayas"]
        got = scraper.himalayas_queries([track("1" * 16, kws)])
        self.assertEqual(feeds._himalayas_urls({"queries": got}),
                         feeds._himalayas_urls(rendered))

    def test_the_sweep_hands_the_union_to_the_feed_and_nothing_without_keywords(self):
        seen = {}

        def capture(boards, feed_cfg, *a, **kw):
            seen["feeds"] = copy.deepcopy(feed_cfg)
            return []
        base = {"himalayas": {"enabled": True, "pages": 10, "queries": []},
                "remoteok": {"enabled": True}}
        with mock.patch.object(scraper, "FEEDS", base), \
                mock.patch.object(scraper.sources, "fetch_free", capture):
            scraper.fetch_free_multi(list(TRACKS))
            self.assertEqual(seen["feeds"]["himalayas"],
                             {"enabled": True, "pages": 10,
                              "queries": scraper.himalayas_queries(TRACKS)})
            self.assertEqual(seen["feeds"]["remoteok"], {"enabled": True})
            scraper.fetch_free_multi([track("1" * 16, [])])
            self.assertEqual(seen["feeds"], base)
        self.assertEqual(scraper.himalayas_queries(TRACKS)[:3],
                         ["mern stack developer", "react native developer",
                          "machine learning engineer"])


# ===========================================================================
class Ledger(unittest.TestCase):
    """Schema 2's done identity: one request, one line; one display, two
    requests, two lines. Schema 1 keeps today's combo key."""

    def test_one_request_one_line_and_one_display_two_requests_two_lines(self):
        with sweep_env():
            plans = unified()
        react = [u for u in plans["linkedin"] if u["keywords"] == "react developer"]
        self.assertEqual([u[PAID] for u in react], [[MERN, MOBILE], [AI]])
        self.assertEqual({u[LEDGER].rsplit("|", 1)[0] for u in react},
                         {"linkedin|react developer|India|"})
        self.assertEqual([u[LEDGER].rsplit("|", 1)[1] for u in react],
                         [u[KEY][:16] for u in react])
        lines = [u[LEDGER] for units in plans.values() for u in units]
        self.assertEqual(len(set(lines)), len(lines))

    def test_the_run_writes_those_lines_and_a_rerun_skips_every_one(self):
        out = tempfile.mkdtemp(prefix="multi-plan-ledger-")
        self.addCleanup(shutil.rmtree, out, True)
        first = multi_sweep(out=out)
        self.assertEqual(first.done, [f"{TODAY}|{u[LEDGER]}" for u in first.units()])
        again = multi_sweep(out=out)
        self.assertEqual(again.starts, [])
        self.assertEqual(again.done, first.done)

    def test_schema_1_keeps_todays_combo_key(self):
        for company in (None, "IBM"):
            unit = {"keywords": "react developer", "location": "India", "company": company}
            line = scraper.done_key("2026-01-01", "linkedin", unit)
            self.assertEqual(line, f"2026-01-01|linkedin|react developer|India|{company or ''}")
            self.assertEqual(line, runs.combo_key("2026-01-01", "linkedin", unit))

    def test_render_side_progress_and_pricing_follow_the_ledger(self):
        raw = dry_run()
        day = "2026-01-01"
        keys = runs.combo_keys(raw, day)
        self.assertEqual(keys, [f"{day}|{e['ledger']}" for units in raw["sites"].values()
                                for e in units])
        progress = runs.progress(keys, {keys[3]})
        self.assertEqual(progress["tiles"][3],
                         {"site": "linkedin", "label": "react developer @ India",
                          "short": "IND", "state": "done"})
        self.assertEqual(progress["tiles"][5]["label"], "react developer @ India")
        self.assertEqual(progress["tiles"][5]["state"], "pending")   # AI's, a different request
        priced, dropped = runs.remaining_plan(raw, {keys[3]}, day)
        self.assertEqual(dropped, 1)
        self.assertNotIn(raw["sites"]["linkedin"][3], priced["sites"]["linkedin"])


# ===========================================================================
class PlanHash(unittest.TestCase):

    def test_it_binds_what_runs(self):
        with sweep_env():
            plans = unified()
            base = scraper.plan_hash(plans, TRACKS)
            self.assertRegex(base, r"^[0-9a-f]{64}$")
            self.assertEqual(scraper.plan_hash(unified(), TRACKS), base)
            swapped = (TRACKS[1], TRACKS[0], TRACKS[2])
            v1 = dataclasses.replace(TRACKS[0], scoring=dataclasses.replace(
                TRACKS[0].scoring, engine="v1"))
            edited = copy.deepcopy(plans)
            edited["linkedin"][3][PAID] = [MERN]
            changed = {"track order": scraper.plan_hash(unified(swapped), swapped),
                       "track list alone": scraper.plan_hash(plans, swapped),
                       "engine": scraper.plan_hash(plans, (v1,) + TRACKS[1:]),
                       "cap": scraper.plan_hash(unified(limit=5), TRACKS),
                       "provenance": scraper.plan_hash(edited, TRACKS)}
            with mock.patch.dict(scraper.SETTINGS, max_age_days=7):
                changed["request dimension"] = scraper.plan_hash(unified(), TRACKS)
            with mock.patch.object(config, "PROFILE", "another_name"), \
                    mock.patch.dict(scraper.SETTINGS, output_dir="/nowhere/else",
                                    max_spend_usd=0.5, allow_partial_paid_sweep=True):
                self.assertEqual(scraper.plan_hash(unified(), TRACKS), base)
        for what, digest in changed.items():
            self.assertNotEqual(digest, base, what)

    def test_the_dry_run_carries_it_and_only_additions(self):
        raw = dry_run()
        with sweep_env():
            self.assertEqual(raw["plan_hash"], scraper.plan_hash(unified(), TRACKS))
        self.assertEqual(raw["plan_version"], 2)
        self.assertEqual(list(raw), ["profile", "sites", "max_results", "charge_ceiling_usd",
                                     "free_sources", "public_paid", "plan_version",
                                     "plan_hash"])
        for units in raw["sites"].values():
            for entry in units:
                self.assertEqual(list(entry),
                                 ["keywords", "location", "company", "tracks", "ledger"])
        self.assertEqual([[e["keywords"], e["tracks"]] for e in raw["sites"]["indeed"]],
                         [list(x) for x in INDEED])

    def test_a_changed_plan_exits_4_before_any_account_is_read(self):
        got = multi_sweep(byok=True, workers=2, accounts=[c45.Acct("u1")],
                          env={scraper.PLAN_HASH_ENV: "0" * 64})
        self.assertEqual(got.exit_code, scraper.PLAN_CHANGED_EXIT)
        self.assertEqual(got.client.calls, [])
        self.assertEqual(got.client.tokens, [])
        self.assertEqual(got.files, [])
        self.assertIn("PLAN_CHANGED", got.log)

    def test_the_priced_hash_runs(self):
        priced = dry_run()["plan_hash"]
        got = multi_sweep(byok=True, workers=2, accounts=[c45.Acct("u1")],
                          env={scraper.PLAN_HASH_ENV: priced})
        self.assertIsNone(got.exit_code, got.log[-2000:])
        self.assertEqual(len(got.starts), 15)

    def test_schema_1_never_reads_it(self):
        got = c2.c2_sweep(c45.li_units(2), keywords=c2.KW[:2],
                          env={scraper.PLAN_HASH_ENV: "0" * 64})
        self.assertEqual(len(got.client.kinds("start")), 2)
        self.assertNotIn("PLAN_CHANGED", got.log)


# ===========================================================================
def placeable(plans, capacity, workdir):
    raw = {"sites": {s: [dict(u) for u in units] for s, units in plans.items()},
           "max_results": {s: 15 for s in plans}}
    return pool_authorize(raw, [capacity], None, True, workdir)["authorization"][
        "placeable_paid_units"]


def coverage(units, k):
    return {t: (sum(t in u[PAID] for u in units[:k]), sum(t in u[PAID] for u in units))
            for t in ORDER}


class Authorization(unittest.TestCase):
    """The unified plan through the existing pool, unchanged. LinkedIn
    ceilings are $0.046 each (8 units), Indeed's $0.135 (7 units)."""

    def test_full(self):
        got = multi_sweep(byok=True, workers=2, accounts=[c45.Acct("u1")])
        self.assertEqual(got.auth["outcome"], "full")
        self.assertEqual(got.auth["total_planned_paid_units"], 15)
        self.assertEqual(len(got.starts), 15)

    def test_refused_when_partial_was_not_chosen(self):
        got = multi_sweep(byok=True, workers=2, accounts=[c45.Acct("u1", capacity="0.30")])
        self.assertEqual(got.exit_code, scraper.PAID_REFUSED_EXIT)
        self.assertEqual(got.auth["outcome"], "refused")
        self.assertEqual(got.starts, [])
        self.assertIsNone(got.csv)

    def test_partial_runs_the_prefix_and_shared_units_count_for_every_requester(self):
        got = multi_sweep(byok=True, workers=2, partial=True,
                          accounts=[c45.Acct("u1", capacity="0.30")])
        self.assertEqual((got.auth["outcome"], got.auth["placeable_paid_units"]),
                         ("partial", 6))
        units = got.units()
        started = sorted(json.dumps(c[2], sort_keys=True) for c in got.starts)
        with sweep_env():
            prefix = sorted(json.dumps(scraper.build_input(
                s, scraper.effective_search(s, u)), sort_keys=True)
                for s, us in got.plans.items() for u in us[:6] if s == "linkedin")
        self.assertEqual(started, prefix)
        # "react developer" for MERN and Mobile sits in the prefix once and
        # counts for both; nothing promises the tracks equal coverage.
        self.assertEqual(coverage(units, 6), {MERN: (2, 6), MOBILE: (3, 6), AI: (2, 6)})

    def test_within_site_order_changes_which_units_fit_never_how_many(self):
        workdir = tempfile.mkdtemp(prefix="multi-plan-auth-")
        self.addCleanup(shutil.rmtree, workdir, True)
        with sweep_env():
            plans = unified()
            concatenated = {s: sorted(us, key=lambda u: ORDER.index(u[PAID][0]))
                            for s, us in plans.items()}
            for capacity in ("0.10", "0.30", "0.50", "0.70", "1.00", "5"):
                with self.subTest(capacity=capacity):
                    self.assertEqual(placeable(plans, capacity, workdir),
                                     placeable(concatenated, capacity, workdir))
            k = placeable(plans, "0.30", workdir)
            flat = [u for us in plans.values() for u in us]
            cat = [u for us in concatenated.values() for u in us]
            self.assertEqual(min(c for c, _ in coverage(flat, k).values()), 2)
            self.assertEqual(min(c for c, _ in coverage(cat, k).values()), 1)
            # LinkedIn still comes first: $0.50 covers every LinkedIn unit and
            # no Indeed one, for every track alike.
            self.assertEqual(placeable(plans, "0.50", workdir), len(plans["linkedin"]))

    def test_the_kill_switch_reads_no_account(self):
        got = multi_sweep(byok=True, workers=2, accounts=[c45.Acct("u1")],
                          env={scraper.PUBLIC_PAID_FLAG: "0"})
        self.assertEqual(got.exit_code, scraper.PAID_REFUSED_EXIT)
        self.assertEqual(got.client.calls, [])
        self.assertEqual(got.client.tokens, [])


# ===========================================================================
class EndToEnd(unittest.TestCase):
    """Three tracks, offline: unified plan -> paid rows stamped -> free rows
    stamped -> checkpoints -> finalize_multi -> MULTI_OUTPUT_COLUMNS."""

    @classmethod
    def setUpClass(cls):
        cls.got = multi_sweep(free=FREE_POSTS, telemetry_on=True)

    def test_every_request_ran_once_in_plan_order(self):
        got = self.got
        self.assertIsNone(got.error)
        self.assertIsNone(got.exit_code, got.log[-2000:])
        with sweep_env():
            planned = [scraper.build_input(s, scraper.effective_search(s, u))
                       for s, us in got.plans.items() for u in us]
        self.assertEqual([c[2] for c in got.starts], planned)
        self.assertEqual(shape(got.plans["linkedin"]), LINKEDIN)
        self.assertEqual(shape(got.plans["indeed"]), INDEED)

    def test_checkpoints_stamps_and_columns(self):
        got = self.got
        self.assertEqual(len(got.checkpoints), 15 + 2)          # per search, free, final
        final = got.checkpoints[-1]
        paid = [c for c in final if c[1] is not None]
        free = [c for c in final if c[2] is not None]
        self.assertEqual((len(paid), len(free), len(final)), (18, 3, 21))
        self.assertTrue(all(c[2] is True and c[1] is None for c in free))
        self.assertEqual(got.header, scraper.MULTI_OUTPUT_COLUMNS)
        self.assertEqual(len(got.rows), 18)
        self.assertEqual(got.done, [f"{TODAY}|{u[LEDGER]}" for u in got.units()])

    def test_found_by_is_acquisition_and_track_evals_is_fit(self):
        got = self.got
        shared = got.row("Probe", "React Developer")
        self.assertEqual(shared["found_by"], f"{MERN};{MOBILE};{AI};free")
        self.assertEqual(list(json.loads(shared["track_evals"])), ORDER)
        self.assertEqual(shared["best_track"], MERN)
        android = got.row("Probe", "Android Developer")
        self.assertEqual((android["found_by"], list(json.loads(android["track_evals"]))),
                         ("free", [MOBILE]))
        mobile = got.row("Probe", "Mobile Engineer")                 # MERN excludes it
        self.assertEqual(list(json.loads(mobile["track_evals"])), [MOBILE])
        units = got.units()
        for row in got.rows:
            if row["company"].startswith("Company "):
                tracks = units[int(row["company"].split()[1])][PAID]
                self.assertEqual(row["found_by"], ";".join(tracks))
                self.assertLessEqual(set(json.loads(row["track_evals"])), set(tracks))
                self.assertIn(row["best_track"], tracks)

    def test_himalayas_asked_once_for_every_track(self):
        self.assertEqual([u.rsplit("q=", 1)[1] for u in self.got.himalayas],
                         [q.replace(" ", "%20") for q in scraper.himalayas_queries(TRACKS)[:8]])

    def test_telemetry_keeps_its_job_level_funnel_and_no_adaptive_section(self):
        record = self.got.telemetry
        self.assertIsNotNone(record)
        self.assertNotIn("paid_adaptive", record)
        self.assertEqual(len(record["paid_units"]), 15)
        text = json.dumps(record)
        for tid in ORDER:
            self.assertNotIn(tid, text)

    def test_no_credential_reaches_any_output(self):
        for blob in (self.got.csv, self.got.log, json.dumps(self.got.telemetry)):
            self.assertNotIn(c1.TOKEN, blob)

    def test_the_shadow_tranche_stays_off(self):
        """Its boards are judged with one profile's gate and scoring (design §I)."""
        called = []
        got = multi_sweep(free=FREE_POSTS, env={shadow.FLAG: "1"},
                          patches=[mock.patch.object(scraper.shadow, "run",
                                                     lambda *a, **k: called.append(a))])
        self.assertIsNone(got.error)
        self.assertEqual(called, [])
        self.assertNotIn("shadow tranche", got.log)
        self.assertEqual(len(got.rows), 18)


# ===========================================================================
class OneTrackRunParity(unittest.TestCase):
    """A one-track schema-2 Sweep, run whole through main(), makes the same
    requests, costs, authorization and results as the schema-1 profile it
    would replace. Internal proof only: one résumé still ships as schema 1."""

    def setUp(self):
        bound = skill_concepts._BOUND
        self.addCleanup(setattr, skill_concepts, "_BOUND", bound)
        skill_concepts.bind(None)
        patch = mock.patch.dict(os.environ, {skill_concepts.VERSION_ENV: "v2"})
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_same_requests_costs_authorization_and_jobs(self):
        kws = ["react developer", "node.js developer", "android developer"]
        search = {"role_keywords": kws, "experience_years": 2}
        one = scraper.TrackContext(id="d4" * 8, role_keywords=tuple(kws), experience_years=2,
                                   title_hints=tuple(scraper.ATS_TITLE_HINTS),
                                   title_exclude=tuple(scraper.ATS_TITLE_EXCLUDE),
                                   scoring=scraper.default_context())
        how = dict(search=search, free=FREE_POSTS, byok=True, workers=2, partial=True,
                   accounts=[c45.Acct("u1", capacity="0.30")])
        legacy = multi_sweep(tracks=(), **how)
        multi = multi_sweep(tracks=(one,), **how)
        for got in (legacy, multi):
            self.assertIsNone(got.error)
            self.assertIsNone(got.exit_code, got.log[-2000:])
        self.assertEqual(legacy.auth["outcome"], "partial")
        self.assertEqual(multi.auth, legacy.auth)
        self.assertEqual(
            sorted(json.dumps(c[1:], sort_keys=True, default=str) for c in multi.starts),
            sorted(json.dumps(c[1:], sort_keys=True, default=str) for c in legacy.starts))
        self.assertEqual(legacy.header, scraper.OUTPUT_COLUMNS)
        self.assertEqual(multi.header, scraper.MULTI_OUTPUT_COLUMNS)
        self.assertGreater(len(legacy.rows), 3)
        self.assertEqual([{k: r[k] for k in scraper.OUTPUT_COLUMNS} for r in multi.rows],
                         legacy.rows)
        self.assertEqual(multi.stats, legacy.stats)
        self.assertEqual([line.rsplit("|", 1)[0] for line in multi.done], legacy.done)
        self.assertEqual([public(u) for us in multi.plans.values() for u in us],
                         [u for us in legacy.plans.values() for u in us])


# ===========================================================================
class DryRunStdout(unittest.TestCase):
    """Computing request keys calls build_input during the dry run; its
    unmapped-Naukri-city note must not land inside --json."""

    def test_the_note_goes_to_stderr_and_the_json_parses(self):
        out, err = io.StringIO(), io.StringIO()
        with sweep_env(sites=("naukri",), naukri=("Atlantis",)), \
                mock.patch.object(scraper, "TRACK_CONTEXTS", TRACKS), \
                mock.patch.object(sys, "argv", ["scraper.py", "--dry-run", "--json"]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            scraper.main()
        self.assertEqual(json.loads(out.getvalue())["plan_version"], 2)
        self.assertIn("no city ID for 'Atlantis'", err.getvalue())

    def test_schema_1_prints_it_where_it_always_has(self):
        out = io.StringIO()
        with sweep_env(sites=("naukri",)), contextlib.redirect_stdout(out):
            scraper.build_input("naukri", {"keywords": "x", "location": "Atlantis",
                                           "max_results": 50, "experience_years": 2})
        self.assertIn("no city ID for 'Atlantis'", out.getvalue())

    def test_an_unmapped_place_refuses_at_plan_time(self):
        with sweep_env(sites=("linkedin",), li=("Atlantis",)), \
                mock.patch.object(scraper, "TRACK_CONTEXTS", TRACKS), \
                mock.patch.object(sys, "argv", ["scraper.py", "--dry-run", "--json"]), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                self.assertRaises(SystemExit) as refused:
            scraper.main()
        self.assertIn("no geoId for 'Atlantis'", str(refused.exception))
        self.assertEqual(out.getvalue(), "")


# ===========================================================================
class SchemaOneDispatch(unittest.TestCase):
    """TRACK_CONTEXTS == (): today's planner, free predicate, finalize and
    columns, and no schema-2 function is ever called."""

    def test_a_schema_1_sweep_never_enters_the_multi_track_path(self):
        def forbid(*_a, **_k):
            raise AssertionError("schema 1 entered the Multi-Track path")
        paid, free = [], []
        real_scrape, real_free = scraper.scrape_search, scraper.fetch_free

        def scrape_spy(*a, **kw):
            rows, cost = real_scrape(*a, **kw)
            paid.extend(rows)
            return rows, cost

        def free_spy():
            rows = real_free()
            free.extend(rows)
            return rows
        real_write = scraper.write_outputs

        def three_arguments(out_rows, csv_path, json_path):   # today's signature, as
            return real_write(out_rows, csv_path, json_path)   # every existing stand-in has it
        patches = [mock.patch.object(scraper, name, forbid)
                   for name in ("multi_plans", "track_plan", "request_key", "plan_hash",
                                "fetch_free_multi", "himalayas_queries", "finalize_multi")]
        patches += [mock.patch.object(scraper, "scrape_search", scrape_spy),
                    mock.patch.object(scraper, "fetch_free", free_spy),
                    mock.patch.object(scraper, "write_outputs", three_arguments)]
        got = c2.c2_sweep(c45.li_units(2), keywords=c2.KW[:2], free=True, patches=patches)
        self.assertEqual(next(csv.reader(io.StringIO(got.csv.decode("utf-8-sig")))),
                         scraper.OUTPUT_COLUMNS)
        self.assertTrue(paid and free)
        self.assertFalse([r for r in paid if PAID in r])
        self.assertFalse([r for r in free if FREE in r])
        self.assertEqual(got.done, [f"{TODAY}|linkedin|{kw}|Bengaluru|" for kw in c2.KW[:2]])

    def test_write_outputs_says_its_columns_explicitly(self):
        out = tempfile.mkdtemp(prefix="multi-plan-write-")
        self.addCleanup(shutil.rmtree, out, True)
        row = scraper.to_output({"Title": "React Developer", "Company": "Probe"})
        multi = dict(row, best_track=MERN, track_evals="{}", found_by="free")
        with mock.patch.dict(scraper.SETTINGS, output_dir=out):
            for rows, columns, want in (([row], None, scraper.OUTPUT_COLUMNS),
                                        ([multi], scraper.MULTI_OUTPUT_COLUMNS,
                                         scraper.MULTI_OUTPUT_COLUMNS)):
                path = os.path.join(out, "jobs.csv")
                scraper.write_outputs(rows, path, os.path.join(out, "jobs.json"), columns)
                with open(path, encoding="utf-8-sig") as fh:
                    self.assertEqual(next(csv.reader(fh)), want)


# ===========================================================================
FRESH = r'''
import contextlib, io, json, socket, sys
name, repo = sys.argv[1:3]
def no_network(*a, **k):
    raise RuntimeError("probe opened a connection")
socket.socket.connect = no_network
socket.create_connection = no_network
sys.argv = ["scraper.py", "--profile", name, "--dry-run", "--json"]
sys.path[:0] = [".", repo, repo + "/auto-apply"]
import scraper
printed = io.StringIO()
with contextlib.redirect_stdout(printed):
    scraper.main()
args = scraper.parse_args()
sites = scraper.resolve_sites(args)
plans = (scraper.multi_plans(sites, args, scraper.TRACK_CONTEXTS) if scraper.TRACK_CONTEXTS
         else {s: scraper.plan_for_site(s, args) for s in sites})
units = {s: [{"search": {k: v for k, v in u.items() if not k.startswith("_")},
              "input": scraper.build_input(s, scraper.effective_search(s, u))} for u in p]
         for s, p in plans.items() if p}
print(json.dumps({"dry": json.loads(printed.getvalue()), "units": units}))
'''

STALE = r'''
import socket, sys
name, repo = sys.argv[1:3]
def no_network(*a, **k):
    raise RuntimeError("probe opened a connection")
socket.socket.connect = no_network
socket.create_connection = no_network
sys.argv = ["scraper.py", "--profile", name, "--yes", "--no-free"]
sys.path[:0] = [".", repo, repo + "/auto-apply"]
import apify_client
class Refuse:
    def __init__(self, *a, **k):
        raise RuntimeError("an Apify client was built")
apify_client.ApifyClient = Refuse
import scraper
scraper.main()
'''


class FreshInterpreters(unittest.TestCase):
    """Real schema-1 and schema-2 files through the real import path, one
    fresh interpreter each (config binds a profile at import)."""

    def setUp(self):
        self.homes = []

    def tearDown(self):
        for home in self.homes:
            shutil.rmtree(home, ignore_errors=True)

    def home(self):
        home = tempfile.mkdtemp(prefix="multi-plan-fresh-")
        os.makedirs(os.path.join(home, "profiles"))
        open(os.path.join(home, "profiles", "__init__.py"), "w").close()
        self.homes.append(home)
        return home

    def write(self, home, name, source):
        with open(os.path.join(home, "profiles", f"{name}.py"), "w", encoding="utf-8") as fh:
            fh.write(source)

    def probe(self, home, name):
        got = subprocess.run([sys.executable, "-c", FRESH, name, REPO], capture_output=True,
                             text=True, cwd=home, env=_env(home), timeout=120)
        self.assertEqual(got.returncode, 0, got.stderr[-3000:])
        return json.loads(got.stdout)

    def test_a_one_track_sweep_plans_prices_and_authorizes_as_its_schema_1_profile(self):
        home = self.home()
        cases = [("ada-plain", "india"), ("ada-plain", "remote"),
                 ("ada-plain", "global_custom"), ("ada-plain", "india_naukri"),
                 ("chen-plain", "india"), ("bhaskar-plain", "india")]
        for persona, form in cases:
            derived, prefs = derived_for(persona), prefs_for(form)
            one_name = f"one_{persona.split('-')[0]}_{form}"
            sweep_name = f"sweep_{persona.split('-')[0]}_{form}"
            with flags(**{skill_concepts.VERSION_ENV: "v2"}):
                self.write(home, one_name, make_profile.render(one_name, derived, prefs))
                self.write(home, sweep_name, make_profile.render_sweep(sweep_name, [
                    {"id": "d4" * 8, "engine": "v2", "derived": derived}], prefs))
            legacy, multi = self.probe(home, one_name), self.probe(home, sweep_name)
            with self.subTest(persona=persona, form=form):
                one_dry, sweep_dry = legacy["dry"], multi["dry"]
                self.assertEqual(sweep_dry.pop("plan_version"), 2)
                self.assertRegex(sweep_dry.pop("plan_hash"), r"^[0-9a-f]{64}$")
                for units in sweep_dry["sites"].values():
                    for entry in units:
                        self.assertEqual(entry.pop("tracks"), ["d4" * 8])
                        entry.pop("ledger")
                self.assertEqual({k: v for k, v in sweep_dry.items() if k != "profile"},
                                 {k: v for k, v in one_dry.items() if k != "profile"})
                self.assertEqual(multi["units"], legacy["units"])     # inputs, order
                costs = [plan_mod.cost(d, config.SITE_RATES, config.SITE_RATE_BASIS)
                         for d in (one_dry, sweep_dry)]
                self.assertEqual(*[{k: v for k, v in c.items() if k != "profile"}
                                   for c in costs])
                self.assertEqual(*[app_module.spend_cap_for(c) for c in costs])
                for caps, partial in ((["50"], False), (["1.00"], True), (["0.04"], True)):
                    authorized = [pool_authorize(d, caps, None, partial, home)
                                  for d in (one_dry, sweep_dry)]
                    self.assertEqual(*authorized)

    def test_the_hash_ignores_names_and_paths_and_binds_tracks_and_engines(self):
        prefs = prefs_for("india")
        first, second = self.home(), self.home()
        self.write(first, "alpha", make_profile.render_sweep("alpha", [TA, TB, TC], prefs))
        self.write(second, "beta", make_profile.render_sweep("beta", [TA, TB, TC], prefs))
        self.write(second, "gamma", make_profile.render_sweep("gamma", [TB, TA, TC], prefs))
        self.write(second, "delta", make_profile.render_sweep(
            "delta", [dict(TA, engine="v1"), TB, TC], prefs))
        got = {name: self.probe(home, name) for home, name in
               ((first, "alpha"), (second, "beta"), (second, "gamma"), (second, "delta"))}
        digest = {name: g["dry"]["plan_hash"] for name, g in got.items()}
        self.assertEqual(digest["alpha"], digest["beta"])       # other name, dir, hash seed
        self.assertNotEqual(digest["gamma"], digest["alpha"])   # track order
        self.assertNotEqual(digest["delta"], digest["alpha"])   # an engine alone
        self.assertEqual(got["delta"]["units"], got["alpha"]["units"])

    def test_a_stale_hash_exits_4_in_a_real_process_before_any_account(self):
        home = self.home()
        self.write(home, "sweep", make_profile.render_sweep("sweep", [TA, TB, TC],
                                                            prefs_for("india")))
        priced = self.probe(home, "sweep")["dry"]["plan_hash"]
        keys = json.dumps({"apify_tokens": ["SYNTHETIC_KEY_NOT_A_CREDENTIAL"]})
        runs_ = {}
        for label, expected in (("stale", "0" * 64), ("priced", priced)):
            env = dict(_env(home), **{scraper.BYOK_ENV: "stdin",
                                      scraper.PLAN_HASH_ENV: expected})
            runs_[label] = subprocess.run([sys.executable, "-c", STALE, "sweep", REPO],
                                          input=keys, capture_output=True, text=True,
                                          cwd=home, env=env, timeout=120)
        stale, ok = runs_["stale"], runs_["priced"]
        self.assertEqual(stale.returncode, scraper.PLAN_CHANGED_EXIT, stale.stderr[-2000:])
        self.assertIn("PLAN_CHANGED", stale.stderr)
        self.assertNotIn("Apify client was built", stale.stderr)
        # The priced hash passes the check and reaches the account read, where
        # this probe's client refuses — so the pool has nothing and refuses too.
        self.assertEqual(ok.returncode, scraper.PAID_REFUSED_EXIT, ok.stderr[-2000:])
        self.assertNotIn("PLAN_CHANGED", ok.stderr)


if __name__ == "__main__":
    unittest.main()
