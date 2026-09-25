"""The one-track compatibility firewall (Multi-Track Phase 0a).

These goldens freeze how Sweep behaves TODAY for one profile, under both
engine stamps, so the Multi-Track refactors that follow (TrackContext, the
unified plan, schema 2) can be checked against an objective baseline:

  render         make_profile.render() source, byte for byte
  plan           the dry-run plan, every unit's provider input, combo keys
  engine matrix  render environment -> profile stamp -> scoring branch
  scoring        score_job per row, score_and_filter stages, finalize,
                 LAST_STATS, memoised finalize
  mutation       what score_job adds to a row, and the _score_once memo
  dedupe         job_key precedence, survivor rule, lost provenance
  exports        engine CSV/JSON, and the CSV/JSON/XLSX/HTML downloads
  authorization  cost, spend cap, coverage and AccountPool.authorize

Each profile runs in a FRESH interpreter (one_track_golden_driver.py),
because config and scraper bind the profile at import. The v1 and v2
goldens are both today's behaviour. Nothing here says they should agree:
where they do agree, a test says so explicitly, and only for fields that
really are engine-independent.

POLICY: there is no update mode. An expected value changes only by editing
the committed JSON under fixtures/one_track_goldens/expected/, on purpose,
in the same change that explains why.

ORDER MATTERS. Python's dict == ignores key order, and several key orders
here are contracts: the dry-run "sites" order IS the plan order the
allocator's prefix walks; an output row's key order is the engine JSON's
column order. Those subtrees are compared with assert_same_order as well.

No network, no model, no provider. Inputs are synthetic (see each fixture's
_about).
"""
import ast
import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

import config
import scraper
from sweep import app as app_module
from sweep import logic
from sweep import plan as plan_mod

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "one_track_goldens")
EXPECTED = os.path.join(FIXTURES, "expected")
DRIVER = os.path.join(HERE, "one_track_golden_driver.py")
REPO = os.path.dirname(os.path.dirname(HERE))

# Render environments. "v1-absent" is the worker's real state (no engine
# variable at all); "v1" and "v2" set SWEEP_PROFILE_ENGINE_VERSION.
RENDER_ENVS = {"v1-absent": None, "v1": "v1", "v2": "v2"}
STAMPS = ("v1", "v2")

# Configure-screen forms, validated by the production validator, so these
# are exactly the prefs the app would hand make_profile.render().
FORMS = {
    "india": {"scope": "india"},
    "remote": {"scope": "remote"},
    "global_custom": {"scope": "global", "locations": "United Kingdom, Bengaluru",
                      "max_results": "25", "max_age_days": "7",
                      "min_comp_usd": "30000", "avoid": "php, salesforce"},
    "india_naukri": {"scope": "india", "sites_present": "1", "site_linkedin": "on",
                     "site_indeed": "on", "site_naukri": "on"},
    "free_only": {"scope": "india"},
    "scoring": {"scope": "india", "min_comp_usd": "20000", "avoid": "php"},
}

# (case, derived fixture, form). Case names are profile module names.
CASES = [
    ("golden_ada_india", "ada-plain", "india"),
    ("golden_ada_remote", "ada-plain", "remote"),
    ("golden_ada_global_custom", "ada-plain", "global_custom"),
    ("golden_ada_india_naukri", "ada-plain", "india_naukri"),
    ("golden_ada_free_only", "ada-plain", "free_only"),
    ("golden_chen_india", "chen-plain", "india"),
    ("golden_bhaskar_india", "bhaskar-plain", "india"),
    ("golden_scoring", "scoring", "scoring"),
]
SCORING_CASE = "golden_scoring"
# The same synthetic profile and rows under the other two Configure scopes,
# so the arrangement and geography filters are frozen for every scope. Only
# a compact projection is stored (see scope_projection).
SCOPE_CASES = [("golden_scoring_remote", "remote"), ("golden_scoring_global", "global_custom")]

# The plan fields a later phase must ADD only on its own path (Phase 3).
FUTURE_PLAN_FIELDS = {"request_key", "_request_key", "ledger", "_ledger", "tracks",
                      "_tracks", "plan_hash", "plan_version"}

# Plans whose every unit (search dict + built provider input) and combo key
# is stored in full. The larger India plans store their full dry-run site
# lists (so order stays readable), the first and last unit per site in full,
# and a SHA-256 over every unit and every combo key — still an exact check,
# at a fraction of the size.
FULL_DETAIL = {"golden_scoring", "golden_ada_remote", "golden_ada_global_custom",
               "golden_ada_free_only"}


def _digest(obj):
    import hashlib
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False).encode("utf-8")).hexdigest()


def compact_plan(case, plan):
    """What the plan golden stores for `case` (see FULL_DETAIL)."""
    if case in FULL_DETAIL:
        return plan
    return dict(plan,
                units={site: {"count": len(units), "first": units[0], "last": units[-1],
                              "sha256": _digest(units)}
                       for site, units in plan["units"].items()},
                combo_keys={"count": len(plan["combo_keys"]), "first": plan["combo_keys"][0],
                            "last": plan["combo_keys"][-1],
                            "sha256": _digest(plan["combo_keys"])})


def assert_same_order(test, got, want, path="$"):
    """Recursively: every dict's keys in the same order, every list aligned."""
    if isinstance(want, dict):
        test.assertIsInstance(got, dict, path)
        test.assertEqual(list(got), list(want), f"key order differs at {path}")
        for key in want:
            assert_same_order(test, got[key], want[key], f"{path}.{key}")
    elif isinstance(want, list):
        test.assertIsInstance(got, list, path)
        test.assertEqual(len(got), len(want), f"length differs at {path}")
        for i, (g, w) in enumerate(zip(got, want)):
            assert_same_order(test, g, w, f"{path}[{i}]")


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def _expected(name):
    with open(os.path.join(EXPECTED, name), encoding="utf-8") as fh:
        return json.load(fh)


def prefs_for(form_name):
    """_prefs(state) after the Configure form, as the app builds it."""
    state = dict(logic._configure_overrides(FORMS[form_name], logic.DEFAULT_SCOPE))
    if form_name == "free_only":
        # POST /key/free (sweep/app.py::_apply_choice): every paid site off.
        state["free_only"] = True
        state["sites_enabled"] = {site: False for site in logic.paid_sites()}
    return app_module._prefs(state)


def derived_for(fixture):
    if fixture == "scoring":
        return _load("scoring_profile.json")["derived"]
    return _load("derived_personas.json")["personas"][fixture]


def _env(home, engine=None):
    """A minimal environment: no .env, no SWEEP_* flag, no credential."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home,
           "LANG": "en_US.UTF-8", "PYTHONIOENCODING": "utf-8",
           "PYTHONDONTWRITEBYTECODE": "1"}
    if engine:
        env["SWEEP_PROFILE_ENGINE_VERSION"] = engine
    return env


def _drive(mode, spec, cwd, engine=None):
    got = subprocess.run([sys.executable, DRIVER, mode], input=json.dumps(spec),
                         capture_output=True, text=True, cwd=cwd,
                         env=_env(cwd, engine), timeout=300)
    if got.returncode != 0:
        raise AssertionError(f"golden driver {mode} failed:\n{got.stderr[-4000:]}")
    return json.loads(got.stdout)


def collect(workdir):
    """Everything the goldens compare, computed fresh from today's code."""
    specs = [{"name": name, "derived": derived_for(fixture), "prefs": prefs_for(form)}
             for name, fixture, form in CASES + [(n, "scoring", f) for n, f in SCOPE_CASES]]
    rows = _load("scoring_rows.json")["rows"]
    out = {"render": {}, "run": {}}
    for label, engine in RENDER_ENVS.items():
        home = os.path.join(workdir, f"render-{label}")
        os.makedirs(home)
        out["render"][label] = _drive("render", {"cases": specs}, home, engine)
    for stamp in STAMPS:
        cwd = os.path.join(workdir, f"run-{stamp}")
        os.makedirs(os.path.join(cwd, "profiles"))
        open(os.path.join(cwd, "profiles", "__init__.py"), "w").close()
        out["run"][stamp] = {}
        for name, _fixture, _form in CASES:
            with open(os.path.join(cwd, "profiles", f"{name}.py"), "w",
                      encoding="utf-8") as fh:
                fh.write(out["render"][stamp]["sources"][name])
            spec = {"name": name, "rows": rows if name == SCORING_CASE else None}
            # No engine variable at run time: the scoring branch must come
            # from the stamp in the file, as on the worker.
            out["run"][stamp][name] = _drive("run", spec, cwd)
        for name, _form in SCOPE_CASES:
            with open(os.path.join(cwd, "profiles", f"{name}.py"), "w",
                      encoding="utf-8") as fh:
                fh.write(out["render"][stamp]["sources"][name])
            out.setdefault("scope", {}).setdefault(stamp, {})[name] = scope_projection(
                _drive("run", {"name": name, "rows": rows}, cwd)["scoring"])
    # The run-time environment against the file's stamp, and a profile with
    # no stamp at all (hand-written, or rendered before stamping existed).
    unstamped = os.path.join(workdir, "run-unstamped")
    os.makedirs(os.path.join(unstamped, "profiles"))
    open(os.path.join(unstamped, "profiles", "__init__.py"), "w").close()
    with open(os.path.join(unstamped, "profiles", f"{SCORING_CASE}.py"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(line for line in
                           out["render"]["v2"]["sources"][SCORING_CASE].split("\n")
                           if not line.startswith("PROFILE_SCHEMA = ")))
    out["run_env"] = {}
    for label, source, engine in RUN_ENV_CASES:
        cwd = unstamped if source is None else os.path.join(workdir, f"run-{source}")
        out["run_env"][label] = _drive("run", {"name": SCORING_CASE, "rows": None},
                                       cwd, engine)["matrix"]
    return out


# (label, file stamp or None for no stamp, run-time SWEEP_PROFILE_ENGINE_VERSION)
RUN_ENV_CASES = [("v1-file+v2-env", "v1", "v2"), ("v2-file+v1-env", "v2", "v1"),
                 ("unstamped", None, None), ("unstamped+v2-env", None, "v2")]


def scope_projection(scoring):
    """Which rows each filter stage keeps, the stats, and the final order."""
    return {"stages": scoring["score_and_filter"]["stages"],
            "stats": scoring["score_and_filter"]["stats"],
            "eligible_urls": scoring["score_and_filter"]["eligible_urls"],
            "final": [[r["apply_url"], r["score"], r["remote_scope"]]
                      for r in scoring["finalize"]["rows"]],
            "last_stats": scoring["finalize"]["last_stats"],
            "sections": scoring["exports"]["sections"]}


def site_entries(raw):
    """PaidEntry lists for a dry-run plan, built as paid_phase_c2 builds them
    (scraper.py:2472-2484), so AccountPool sees the production shape."""
    out, n, day = {}, 0, "2026-01-01"
    for site, searches in raw["sites"].items():
        entries = out[site] = []
        for i, entry in enumerate(searches, 1):
            search = dict(entry, max_results=raw["max_results"][site])
            depth = scraper.effective_search(site, search)["max_results"]
            entries.append(scraper.PaidEntry(
                n, i, len(searches), scraper.paid_unit_id(n), site, scraper.SITES[site]["actor"],
                search, f"{search['keywords']} @ {search['location']}",
                f"{day}|{site}|{search['keywords']}|{search['location']}|"
                f"{search.get('company') or ''}",
                scraper.max_charge_usd(site, depth)))
            n += 1
    return out


def pool_authorize(raw, capacities, budget, partial, workdir):
    """AccountPool.plan + authorize over fixed, provider-free readings."""
    accounts = [scraper.PoolAccount(
        f"account_{i:03d}", [f"key_{i + 1}"], "golden-placeholder-not-a-credential",
        {"plan": "FREE", "headroom_usd": Decimal(c) + scraper.ACCOUNT_BUFFER_USD,
         "used_usd": 0.0, "memory_mb": 8192, "run_slots": 25},
        lambda _token: object()) for i, c in enumerate(capacities)]
    ledger = scraper.AccountLedger(os.path.join(workdir, scraper.POOL_RECORD))
    pool = scraper.AccountPool(accounts, [], ledger)
    entries = site_entries(raw)
    with contextlib.redirect_stdout(io.StringIO()):
        pool.plan(entries, set())
    auth = pool.authorize(entries, set(), budget, partial)
    order = [e.unit_id for es in entries.values() for e in es]
    return {"authorization": auth,
            "runnable_is_plan_prefix": [u for u in order if u in pool.runnable]
            == order[:len(pool.runnable)],
            "runnable_sites": _counts([u for es in entries.values() for u in es
                                       if u.unit_id in pool.runnable])}


def _counts(entries):
    """[[site, n], ...] in plan order — a list, so the order survives JSON."""
    out = []
    for e in entries:
        if out and out[-1][0] == e.site_key:
            out[-1][1] += 1
        else:
            out.append([e.site_key, 1])
    return out


AUTH_CASES = [
    # (label, plan case, capacities, budget, partial)
    ("full", "golden_ada_india", ["50"], None, False),
    ("partial_not_chosen", "golden_ada_india", ["1.00"], None, False),
    ("partial_chosen", "golden_ada_india", ["1.00"], None, True),
    ("two_accounts_partial", "golden_ada_india", ["0.50", "0.50"], None, True),
    ("nothing_placeable", "golden_ada_india", ["0.04"], None, True),
    ("budget_bound", "golden_ada_india", ["50"], "3.00", True),
    ("unbounded_naukri", "golden_ada_india_naukri", ["50"], None, True),
]


def authorization_results(plans, workdir):
    out = {"cost": {}, "cases": {}}
    for case in ("golden_ada_india", "golden_ada_india_naukri"):
        costed = plan_mod.cost(plans[case]["dry_run"], config.SITE_RATES,
                               config.SITE_RATE_BASIS)
        out["cost"][case] = {"cost": costed, "spend_cap": app_module.spend_cap_for(costed)}
    for label, case, caps, budget, partial in AUTH_CASES:
        raw = plans[case]["dry_run"]
        budget = None if budget is None else Decimal(budget)
        cov = plan_mod.coverage(raw, [Decimal(c) for c in caps], budget)
        prefix = cov.pop("prefix")
        engine = pool_authorize(raw, caps, budget, partial, workdir)
        out["cases"][label] = {
            "coverage": dict(cov, prefix_sites=[[s, len(v)] for s, v in prefix["sites"].items()]),
            "engine": engine}
    return json.loads(json.dumps(out, default=str))


# ---------------------------------------------------------------------------
_WORKDIR = None
_RESULTS = None


def setUpModule():
    global _WORKDIR, _RESULTS
    _WORKDIR = tempfile.mkdtemp(prefix="one-track-goldens-")
    _RESULTS = collect(_WORKDIR)


def tearDownModule():
    if _WORKDIR:
        shutil.rmtree(_WORKDIR, ignore_errors=True)


def _source(label, case):
    return _RESULTS["render"][label]["sources"][case]


def _run(stamp, case):
    return _RESULTS["run"][stamp][case]


class Golden(unittest.TestCase):
    maxDiff = None


# ===========================================================================
# Render
# ===========================================================================
class RenderGoldens(Golden):
    """make_profile.render() schema-1 output, byte for byte."""

    STAMP_LINE = 'PROFILE_SCHEMA = {{"version": 1, "engine": \'{}\'}}'

    def test_v2_source_is_the_golden_byte_for_byte(self):
        expected = _expected("render.json")
        self.assertEqual(sorted(expected), sorted(c for c, _f, _p in CASES))
        for case, _fixture, _form in CASES:
            with self.subTest(case=case):
                self.assertEqual(_source("v2", case).split("\n"), expected[case])

    def test_v1_differs_from_v2_only_in_the_engine_stamp(self):
        for case, _fixture, _form in CASES:
            with self.subTest(case=case):
                v1, v2 = _source("v1", case).split("\n"), _source("v2", case).split("\n")
                self.assertEqual(len(v1), len(v2))
                diff = [i for i, (a, b) in enumerate(zip(v1, v2)) if a != b]
                self.assertEqual(len(diff), 1, "only the stamp line may differ")
                self.assertEqual(v1[diff[0]], self.STAMP_LINE.format("v1"))
                self.assertEqual(v2[diff[0]], self.STAMP_LINE.format("v2"))

    def test_no_engine_variable_renders_exactly_like_explicit_v1(self):
        """The worker's real environment (design §A.3) is 'variable absent'."""
        for case, _fixture, _form in CASES:
            with self.subTest(case=case):
                self.assertEqual(_source("v1-absent", case), _source("v1", case))
        self.assertEqual(_RESULTS["render"]["v1-absent"]["engine_version"], "v1")
        self.assertEqual(_RESULTS["render"]["v1"]["engine_version"], "v1")
        self.assertEqual(_RESULTS["render"]["v2"]["engine_version"], "v2")

    def test_top_level_names_each_case_emits(self):
        """The names the engine overlays (config.OVERLAYABLE) plus the stamp."""
        base = {"PROFILE_SCHEMA", "SEARCH", "SETTINGS", "SCORING", "ATS_TITLE_HINTS",
                "ATS_TITLE_EXCLUDE", "FEEDS", "SITES"}
        expected = {
            "golden_ada_india": base | {"LOCATION_HINTS"},
            "golden_ada_remote": base,
            "golden_ada_global_custom": base | {"LOCATION_HINTS"},
            "golden_ada_india_naukri": base | {"LOCATION_HINTS"},
            "golden_ada_free_only": base | {"LOCATION_HINTS"},
            "golden_chen_india": base | {"LOCATION_HINTS"},
            "golden_bhaskar_india": base | {"LOCATION_HINTS"},
            "golden_scoring": base | {"LOCATION_HINTS"},
        }
        for case, _fixture, _form in CASES:
            with self.subTest(case=case):
                tree = ast.parse(_source("v2", case))
                names = {t.id for node in tree.body if isinstance(node, ast.Assign)
                         for t in node.targets}
                self.assertEqual(names, expected[case])

    def test_rendered_role_keywords_are_the_derivations_in_order(self):
        for case, fixture, _form in CASES:
            with self.subTest(case=case):
                module = {}
                exec(compile(_source("v2", case), case, "exec"), module)
                self.assertEqual(module["SEARCH"]["role_keywords"],
                                 derived_for(fixture)["role_keywords"])


# ===========================================================================
# Plan
# ===========================================================================
class PlanGoldens(Golden):
    """The dry-run plan exactly as the engine prints it, plus what it omits."""

    def test_plan_is_the_golden_under_both_stamps(self):
        expected = _expected("plans.json")
        for stamp in STAMPS:
            for case, _fixture, _form in CASES:
                with self.subTest(stamp=stamp, case=case):
                    got = compact_plan(case, _run(stamp, case)["plan"])
                    self.assertEqual(got, expected[case])
                    assert_same_order(self, got, expected[case])

    def test_the_engine_stamp_does_not_change_any_plan(self):
        for case, _fixture, _form in CASES:
            with self.subTest(case=case):
                self.assertEqual(_run("v1", case)["plan"], _run("v2", case)["plan"])

    def test_site_keyword_and_location_order(self):
        """Site-major in config.SITES order; keyword-major, location inner."""
        sites = {"golden_ada_india": ["linkedin", "indeed"],
                 "golden_ada_remote": ["linkedin", "indeed"],
                 "golden_ada_global_custom": ["linkedin", "indeed"],
                 "golden_ada_india_naukri": ["linkedin", "indeed", "naukri"],
                 "golden_ada_free_only": [],
                 "golden_chen_india": ["linkedin", "indeed"],
                 "golden_bhaskar_india": ["linkedin", "indeed"],
                 "golden_scoring": ["linkedin", "indeed"]}
        for case, fixture, form in CASES:
            plan = _run("v2", case)["plan"]
            with self.subTest(case=case):
                self.assertEqual(list(plan["dry_run"]["sites"]), sites[case])
                keywords = derived_for(fixture)["role_keywords"]
                prefs = prefs_for(form)
                for site, entries in plan["dry_run"]["sites"].items():
                    locations = (config.SITES["naukri"]["locations"] if site == "naukri"
                                 else prefs["linkedin_locations"] if site == "linkedin"
                                 else prefs["locations"])
                    self.assertEqual([(e["keywords"], e["location"]) for e in entries],
                                     [(k, loc) for k in keywords for loc in locations])
                    self.assertTrue(all(e["company"] == "" for e in entries))
                self.assertEqual(plan["search_count"],
                                 sum(len(v) for v in plan["dry_run"]["sites"].values()))
                self.assertEqual(len(plan["combo_keys"]), plan["search_count"])

    def test_dry_run_shape_carries_no_future_multi_track_field(self):
        for case, _fixture, _form in CASES:
            plan = _run("v2", case)["plan"]
            with self.subTest(case=case):
                self.assertEqual(set(plan["dry_run"]),
                                 {"profile", "sites", "max_results", "charge_ceiling_usd",
                                  "free_sources", "public_paid"})
                for entries in plan["dry_run"]["sites"].values():
                    for entry in entries:
                        self.assertEqual(set(entry), {"keywords", "location", "company"})
                blob = json.dumps(plan)
                for field in FUTURE_PLAN_FIELDS:
                    self.assertNotIn(f'"{field}"', blob)

    def test_combo_keys_are_date_site_keyword_location_company(self):
        plan = _run("v2", "golden_ada_india")["plan"]
        first = plan["dry_run"]["sites"]["linkedin"][0]
        self.assertEqual(plan["combo_keys"][0],
                         f"2026-01-01|linkedin|{first['keywords']}|{first['location']}|")

    def test_experience_band_reaches_the_linkedin_input(self):
        """years 5 -> f_E=4, 11 -> 5, 0 -> 2 (scraper._linkedin_experience_code)."""
        for case, band in (("golden_ada_india", "4"), ("golden_chen_india", "5"),
                           ("golden_bhaskar_india", "2")):
            with self.subTest(case=case):
                url = _run("v2", case)["plan"]["units"]["linkedin"][0]["input"]["urls"][0]
                self.assertIn(f"f_E={band}", url)

    def test_free_only_plans_nothing_paid(self):
        plan = _run("v2", "golden_ada_free_only")["plan"]
        self.assertEqual(plan["dry_run"]["sites"], {})
        self.assertEqual(plan["search_count"], 0)
        self.assertGreater(plan["dry_run"]["free_sources"], 0)


# ===========================================================================
# Engine-stamp matrix
# ===========================================================================
class EngineStampMatrix(Golden):
    """Local engine contract: render environment -> stamp -> scoring branch.
    NOT the deployment state (Render v2 / worker v1), which Phase 0b fixes."""

    def test_matrix(self):
        expected = {
            "v1": {"profile_engine": "v1", "profile_schema": {"version": 1, "engine": "v1"},
                   "effective_version": "v1", "effective_source": "the profile it was derived with",
                   "concept_scoring": False, "evidence": False},
            "v2": {"profile_engine": "v2", "profile_schema": {"version": 1, "engine": "v2"},
                   "effective_version": "v2", "effective_source": "the profile it was derived with",
                   "concept_scoring": True, "evidence": True},
        }
        for stamp in STAMPS:
            for case, _fixture, _form in CASES:
                with self.subTest(stamp=stamp, case=case):
                    self.assertEqual(_run(stamp, case)["matrix"], expected[stamp])

    def test_run_time_environment(self):
        """The file's engine wins over the run-time variable. A file with no
        stamp is v1 output by definition (make_profile.profile_schema), so
        it binds v1 too: the variable never decides a loaded profile."""
        got = {label: (m["profile_engine"], m["effective_version"], m["concept_scoring"])
               for label, m in _RESULTS["run_env"].items()}
        self.assertEqual(got, {"v1-file+v2-env": ("v1", "v1", False),
                               "v2-file+v1-env": ("v2", "v2", True),
                               "unstamped": ("v1", "v1", False),
                               "unstamped+v2-env": ("v1", "v1", False)})
        self.assertIsNone(_RESULTS["run_env"]["unstamped"]["profile_schema"])


# ===========================================================================
# Scoring / finalize
# ===========================================================================
KEPT = {  # by fixture URL suffix, from scoring_rows.json _case notes
    "01": True, "02": True, "03": True, "04": True, "05": True, "06": True,
    "07": False, "08": True, "09": False, "10": True, "11": True, "12": True,
    "13": True, "14": True, "15": True, "16": True, "17": False, "18": True,
}
# score_job alone keeps 12-15; the Sweep-preference filters then drop them.
PREFERENCE_DROPPED = {"12", "13", "14", "15"}


def _url(n):
    return f"https://example.test/jobs/{n}"


class ScoringGoldens(Golden):

    def _scoring(self, stamp):
        return _run(stamp, SCORING_CASE)["scoring"]

    def test_scoring_is_the_golden(self):
        for stamp in STAMPS:
            with self.subTest(stamp=stamp):
                expected = _expected(f"scoring_{stamp}.json")
                self.assertEqual(self._scoring(stamp), expected)
                assert_same_order(self, self._scoring(stamp), expected)

    def test_score_job_keep_drop_is_the_same_under_both_stamps(self):
        for stamp in STAMPS:
            with self.subTest(stamp=stamp):
                got = {r["url"][-2:]: r["kept"] for r in self._scoring(stamp)["score_job"]}
                self.assertEqual(got, KEPT)

    def test_final_rows_and_dedupe_survivor(self):
        for stamp in STAMPS:
            final = self._scoring(stamp)["finalize"]["rows"]
            urls = [r["apply_url"] for r in final]
            with self.subTest(stamp=stamp):
                expected = {_url(n) for n, kept in KEPT.items()
                            if kept and n not in PREFERENCE_DROPPED and n != "10"}
                self.assertEqual(set(urls), expected)
                # Two copies of one job: the higher-scoring free copy survives
                # and the paid copy's provenance goes with the loser.
                beta = [r for r in final if r["company"].startswith("Beta")]
                self.assertEqual([r["apply_url"] for r in beta], [_url("11")])
                self.assertEqual(beta[0]["search_query"], "")
                self.assertEqual(beta[0]["source_site"], "greenhouse:betasystems")
                scores = [r["score"] for r in final]
                self.assertEqual(scores, sorted(scores, reverse=True))

    def test_fields_the_engine_stamp_does_not_touch(self):
        """Only the positive-skill sum and matched list read the stamp."""
        v1 = {r["url"]: r for r in self._scoring("v1")["score_job"]}
        v2 = {r["url"]: r for r in self._scoring("v2")["score_job"]}
        for url, row in v1.items():
            with self.subTest(url=url):
                self.assertEqual(row["kept"], v2[url]["kept"])
                self.assertEqual(row["added_keys"], v2[url]["added_keys"])
                for key in ("is_fullstack", "years_required", "remote_scope", "remote?",
                            "tz_gap", "hr_email", "visa", "eor", "remote_regions"):
                    self.assertEqual(row["added"].get(key), v2[url]["added"].get(key), key)

    def test_stage_counts_and_filter_stats_match_under_both_stamps(self):
        a, b = self._scoring("v1"), self._scoring("v2")
        self.assertEqual(a["score_and_filter"]["stages"], b["score_and_filter"]["stages"])
        self.assertEqual(a["score_and_filter"]["stats"], b["score_and_filter"]["stats"])
        self.assertEqual(a["finalize"]["last_stats"], b["finalize"]["last_stats"])

    def test_memoised_finalize_is_identical(self):
        for stamp in STAMPS:
            with self.subTest(stamp=stamp):
                got = self._scoring(stamp)["finalize"]
                self.assertTrue(got["memo_output_identical"])
                self.assertEqual(got["memo_flags"],
                                 [KEPT[f"{i:02d}"] for i in range(1, 19)])


class ScopeGoldens(Golden):
    """The preference filters under the remote and global scopes."""

    def test_scopes_are_the_golden(self):
        expected = _expected("scoring_scopes.json")
        self.assertEqual(_RESULTS["scope"], expected)
        assert_same_order(self, _RESULTS["scope"], expected)

    def test_filter_decisions_do_not_depend_on_the_stamp(self):
        for name, _form in SCOPE_CASES:
            a, b = _RESULTS["scope"]["v1"][name], _RESULTS["scope"]["v2"][name]
            with self.subTest(case=name):
                for key in ("stages", "stats", "eligible_urls", "last_stats"):
                    self.assertEqual(a[key], b[key], key)


class ScoreJobMutationContract(Golden):
    """What Phase 1's compatibility wrapper must reproduce."""

    KEPT_ROW_ADDS = ["eor", "hr_email", "hr_phone", "is_fullstack", "matched_skills",
                     "remote?", "remote_regions", "remote_scope", "score", "timezones",
                     "tz_gap", "visa", "years_required"]

    def test_mutation_contract(self):
        for stamp in STAMPS:
            scoring = _run(stamp, SCORING_CASE)["scoring"]
            m = scoring["mutation"]
            with self.subTest(stamp=stamp):
                self.assertTrue(m["repeat_score_job_identical"])
                self.assertTrue(m["dropped_returns_none"])
                self.assertTrue(m["dropped_row_unchanged"])
                self.assertTrue(m["score_once_keeps"])
                self.assertIs(m["score_once_flag"], True)
                self.assertEqual(m["score_once_flag_key"], "_scored")
                # The hazard design §K removes: a False verdict left on a row
                # is returned without re-scoring, even for a row score_job keeps.
                self.assertTrue(m["score_once_honours_a_stale_false_flag"])
                self.assertTrue(m["stale_flag_row_would_be_kept_by_score_job"])
                rows = {r["url"]: r for r in scoring["score_job"]}
                self.assertEqual(rows[_url("01")]["added_keys"], self.KEPT_ROW_ADDS)
                self.assertEqual(rows[_url("01")]["changed_keys"], [])
                for n in ("07", "09", "17"):
                    self.assertEqual(rows[_url(n)]["added_keys"], [], n)
                    self.assertEqual(rows[_url(n)]["changed_keys"], [], n)


# ===========================================================================
# Dedupe (pure functions; no profile involved)
# ===========================================================================
def _row(**kw):
    base = {"Title": "", "Company": "", "Job URL": "", "Source": "", "score": 0}
    base.update(kw)
    return base


class DedupeContract(Golden):

    def test_requisition_number_outranks_company_and_title(self):
        a = _row(Title="Engineer A", Company="Optum", req_number="R-100")
        b = _row(Title="Engineer B", Company="Other", req_number="r-100")
        self.assertEqual(scraper.job_key(a), ("req", "r-100"))
        self.assertEqual(scraper.job_key(a), scraper.job_key(b))

    def test_company_suffixes_and_title_word_order_are_ignored(self):
        a = _row(Title="Node.js Developer", Company="Beta Systems Pvt Ltd")
        b = _row(Title="Developer, Node.js", Company="Beta Systems")
        self.assertEqual(scraper.job_key(a), ("ct", "beta", "developer js node"))
        self.assertEqual(scraper.job_key(a), scraper.job_key(b))

    def test_location_is_not_part_of_identity(self):
        a = _row(Title="React Developer", Company="Acme", Location="Remote")
        b = _row(Title="React Developer", Company="Acme", Location="Bengaluru")
        self.assertEqual(scraper.job_key(a), scraper.job_key(b))

    def test_url_fallback_drops_tracking_and_www(self):
        a = _row(Title="React Developer", **{"Job URL": "https://www.Example.test/j/9?utm=x"})
        b = _row(Title="React Developer", **{"Job URL": "https://example.test/j/9/"})
        self.assertEqual(scraper.job_key(a), ("url", "example.test/j/9"))
        self.assertEqual(scraper.job_key(a), scraper.job_key(b))

    def test_a_row_with_no_identity_is_always_kept(self):
        rows = [_row(Title="X"), _row(Title="X")]
        self.assertIsNone(scraper.job_key(rows[0]))
        self.assertEqual(len(scraper.dedupe(rows)), 2)

    def test_highest_score_wins_and_ties_keep_arrival_order(self):
        low = _row(Title="Dev", Company="Acme", score=5, Source="linkedin", n=1)
        high = _row(Title="Dev", Company="Acme", score=9, Source="greenhouse:acme", n=2)
        tie_a = _row(Title="Ops", Company="Acme", score=4, Source="linkedin", n=3)
        tie_b = _row(Title="Ops", Company="Acme", score=4, Source="remoteok", n=4)
        got = scraper.rank_rows([low, high, tie_a, tie_b])
        self.assertEqual([r["n"] for r in got], [2, 3])
        # The loser's source is gone: dedupe keeps one dict, merges nothing.
        self.assertEqual({r["Source"] for r in got}, {"greenhouse:acme", "linkedin"})


# ===========================================================================
# Exports
# ===========================================================================
class ExportGoldens(Golden):

    def test_exports_are_the_golden(self):
        for stamp in STAMPS:
            scoring = _run(stamp, SCORING_CASE)["scoring"]
            expected = _expected(f"scoring_{stamp}.json")
            with self.subTest(stamp=stamp):
                self.assertEqual(scoring["engine_outputs"], expected["engine_outputs"])
                self.assertEqual(scoring["exports"], expected["exports"])

    def test_shapes_every_consumer_reads(self):
        from sweep import exports
        for stamp in STAMPS:
            s = _run(stamp, SCORING_CASE)["scoring"]
            with self.subTest(stamp=stamp):
                self.assertTrue(s["engine_outputs"]["csv_has_bom"])
                self.assertTrue(s["engine_outputs"]["json_equals_finalize"])
                self.assertEqual(s["engine_outputs"]["csv_lines"][0],
                                 ",".join(scraper.OUTPUT_COLUMNS))
                self.assertEqual(len(scraper.OUTPUT_COLUMNS), 26)
                self.assertTrue(s["exports"]["csv_has_bom"])
                self.assertEqual(s["exports"]["csv_lines"][0], ",".join(exports.HEADERS))
                self.assertEqual(len(exports.HEADERS), 16)
                self.assertTrue(all(list(r) == exports.HEADERS for r in s["exports"]["json"]))
                xlsx = s["exports"]["xlsx"]
                self.assertEqual(xlsx["sheets"], ["Listings", "About this export"])
                self.assertEqual(xlsx["Listings"]["cells"][0], exports.HEADERS)
                self.assertEqual(xlsx["Listings"]["freeze_panes"], "A2")
                self.assertEqual(xlsx["Listings"]["auto_filter"],
                                 f"A1:P{len(s['exports']['json']) + 1}")


# ===========================================================================
# Paid planning / authorization (offline; fixed readings, no provider)
# ===========================================================================
class PaidAuthorizationGoldens(Golden):

    @classmethod
    def setUpClass(cls):
        cls.got = authorization_results(_expected("plans.json"), _WORKDIR)

    def test_authorization_is_the_golden(self):
        self.assertEqual(self.got, _expected("authorization.json"))
        assert_same_order(self, self.got, _expected("authorization.json"))

    def test_the_plan_prefix_is_linkedin_first(self):
        """Plan order is config.SITES order, so a partial run spends on
        LinkedIn before Indeed. Coverage and the engine agree on it."""
        for label in ("partial_chosen", "two_accounts_partial", "budget_bound"):
            case = self.got["cases"][label]
            with self.subTest(label=label):
                self.assertEqual(case["coverage"]["prefix_sites"][0][0], "linkedin")
                self.assertEqual(case["engine"]["runnable_sites"][0][0], "linkedin")

    def test_render_coverage_and_engine_authorization_agree(self):
        for label, _case, _caps, _budget, _partial in AUTH_CASES:
            case = self.got["cases"][label]
            with self.subTest(label=label):
                self.assertEqual(case["coverage"]["placeable_units"],
                                 case["engine"]["authorization"]["placeable_paid_units"])
                self.assertTrue(case["engine"]["runnable_is_plan_prefix"])

    def test_outcomes(self):
        outcome = {label: c["engine"]["authorization"]["outcome"]
                   for label, c in self.got["cases"].items()}
        self.assertEqual(outcome, {"full": "full", "partial_not_chosen": "refused",
                                   "partial_chosen": "partial",
                                   "two_accounts_partial": "partial",
                                   "nothing_placeable": "refused",
                                   "budget_bound": "partial",
                                   "unbounded_naukri": "partial"})
        stop = {label: c["engine"]["authorization"]["stop_reason"]
                for label, c in self.got["cases"].items()}
        self.assertEqual(stop["budget_bound"], "budget")
        self.assertEqual(stop["unbounded_naukri"], "unbounded")
        self.assertEqual(stop["partial_chosen"], "insufficient_capacity")
        self.assertIsNone(stop["full"])


if __name__ == "__main__":
    unittest.main()
