"""Which engine reads a résumé, which engine scores it, and what a profile
is allowed to contain.

Three findings from the independent review, all about production paths
that helper tests could not see:

  R2a  make_profile.load_profile() rejects a profile from a future schema,
       and config.py — the loader production actually uses — imports and
       overlays the profile without ever calling it. The protection was
       real and unreachable.

  R2b  Render pins the derivation engine in render.yaml. The Oracle
       worker's scraper child reads its OWN environment, so a profile
       derived as v1 could be scored as v2 and nobody would know. Two
       machines agreeing by coincidence is not a contract.

  Part 4  The renderer shared by every engine still accepted any integer
       as a weight, turned -10 into +10, and took True as one year.

The fix is that a profile says which engine derived it, and that stamp —
not an environment variable on whichever machine happens to run — decides
how it is scored.
"""

import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_APPLY = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(AUTO_APPLY)
for path in (REPO_ROOT, AUTO_APPLY):
    if path not in sys.path:
        sys.path.insert(0, path)

import make_profile  # noqa: E402
import skill_concepts  # noqa: E402

FLAGS = (skill_concepts.VERSION_ENV, skill_concepts.FLAG,
         skill_concepts.EVIDENCE_FLAG, skill_concepts.ROLES_FLAG)


class Clean(unittest.TestCase):
    """Every test here starts from a known-empty configuration."""

    def setUp(self):
        self._before = {name: os.environ.get(name) for name in FLAGS}
        for name in FLAGS:
            os.environ.pop(name, None)
        skill_concepts.bind(None)

    def tearDown(self):
        skill_concepts.bind(None)
        for name, value in self._before.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value


class TestTheCodeDefaultIsV1(Clean):
    """Part 7. The absence of an environment variable must not silently
    enable an engine the evaluation called experimental."""

    def test_no_configuration_at_all_is_v1(self):
        self.assertEqual(skill_concepts.engine_version(), "v1")
        self.assertFalse(skill_concepts.enabled())
        self.assertFalse(skill_concepts.evidence_enabled())

    def test_role_families_are_off_under_every_version(self):
        """Part 8: held, and not reachable through the version switch."""
        for version in ("v1", "v2"):
            os.environ[skill_concepts.VERSION_ENV] = version
            self.assertFalse(skill_concepts.roles_enabled(), version)

    def test_v2_is_still_selectable_for_experiments(self):
        os.environ[skill_concepts.VERSION_ENV] = "v2"
        self.assertTrue(skill_concepts.enabled())
        self.assertTrue(skill_concepts.evidence_enabled())


class TestRollbackIsAbsolute(Clean):
    """Part 7. The review found that a documented rollback to v1 could be
    silently defeated by a stale SWEEP_SKILL_CONCEPTS left in someone's
    shell."""

    def test_a_stale_step_flag_cannot_defeat_an_explicit_v1(self):
        os.environ[skill_concepts.FLAG] = "1"
        os.environ[skill_concepts.EVIDENCE_FLAG] = "1"
        os.environ[skill_concepts.VERSION_ENV] = "v1"
        self.assertFalse(skill_concepts.enabled())
        self.assertFalse(skill_concepts.evidence_enabled())

    def test_step_flags_still_work_when_no_version_is_pinned(self):
        """Experiments compose; production pins. bench/evaluate.py relies
        on this, and it pops the version variable to get it."""
        os.environ[skill_concepts.FLAG] = "1"
        self.assertTrue(skill_concepts.enabled())
        self.assertFalse(skill_concepts.evidence_enabled())

    def test_the_effective_configuration_is_inspectable(self):
        os.environ[skill_concepts.FLAG] = "1"
        effective = skill_concepts.effective()
        # Neither engine: concepts without evidence is an experiment, and
        # saying "v2" would stamp a profile v2 and later bind evidence
        # that was never used to derive it.
        self.assertEqual(effective["version"], skill_concepts.MIXED)
        self.assertTrue(effective["concepts"])
        self.assertFalse(effective["evidence"])
        self.assertFalse(effective["roles"])
        self.assertEqual(effective["source"], "step flags")

    def test_both_step_flags_together_are_a_real_v2(self):
        os.environ[skill_concepts.FLAG] = "1"
        os.environ[skill_concepts.EVIDENCE_FLAG] = "1"
        self.assertEqual(skill_concepts.engine_version(), "v2")

    def test_a_mixed_composition_cannot_be_bound(self):
        """So an experiment's profile cannot be run as though it were one
        of the two supported engines."""
        with self.assertRaises(ValueError):
            skill_concepts.bind(skill_concepts.MIXED)

    def test_an_unknown_version_is_still_an_error(self):
        os.environ[skill_concepts.VERSION_ENV] = "v9"
        with self.assertRaises(ValueError):
            skill_concepts.engine_version()


class TestTheProfileBindsItsOwnEngine(Clean):
    """R2b. A profile derived as v1 must not be scored as v2 because the
    machine that scores it has a different environment."""

    def test_a_bound_v1_profile_beats_a_v2_environment(self):
        os.environ[skill_concepts.VERSION_ENV] = "v2"
        skill_concepts.bind("v1")
        self.assertEqual(skill_concepts.engine_version(), "v1")
        self.assertFalse(skill_concepts.enabled())

    def test_a_bound_v2_profile_beats_a_v1_environment(self):
        os.environ[skill_concepts.VERSION_ENV] = "v1"
        skill_concepts.bind("v2")
        self.assertEqual(skill_concepts.engine_version(), "v2")
        self.assertTrue(skill_concepts.enabled())

    def test_a_bound_engine_beats_stale_step_flags_too(self):
        os.environ[skill_concepts.FLAG] = "1"
        skill_concepts.bind("v1")
        self.assertFalse(skill_concepts.enabled())

    def test_binding_none_releases_it(self):
        skill_concepts.bind("v2")
        self.assertEqual(skill_concepts.engine_version(), "v2")
        skill_concepts.bind(None)
        self.assertEqual(skill_concepts.engine_version(), "v1")

    def test_a_bound_engine_is_reported(self):
        skill_concepts.bind("v2")
        self.assertEqual(skill_concepts.effective()["source"],
                         "the profile it was derived with")

    def test_an_unknown_bound_value_is_refused_at_the_binding(self):
        with self.assertRaises(ValueError):
            skill_concepts.bind("v9")

    def test_a_profile_cannot_bind_something_that_is_not_a_version(self):
        """Arbitrary profile content must not reach configuration."""
        for junk in ("; rm -rf /", "v2 v1", 2, ["v2"], {"v": 2}):
            with self.assertRaises(ValueError):
                skill_concepts.bind(junk)


class TestTheRealLoaderEnforcesTheSchema(Clean):
    """R2a. The check has to live where production loads profiles."""

    def module(self, name, **attrs):
        made = types.ModuleType(f"profiles.{name}")
        made.SEARCH = {"role_keywords": ["sentinel"], "experience_years": 1,
                       "locations": ["Remote"], "salary_min": None}
        for key, value in attrs.items():
            setattr(made, key, value)
        sys.modules[f"profiles.{name}"] = made
        self.addCleanup(sys.modules.pop, f"profiles.{name}", None)
        return made

    def load(self, name):
        import config
        return config.load_profile_module(name)

    def test_a_future_schema_is_refused_by_the_real_loader(self):
        self.module("futureprobe",
                    PROFILE_SCHEMA={"version": make_profile.PROFILE_SCHEMA + 1,
                                    "engine": "v9"})
        with self.assertRaises(SystemExit) as caught:
            self.load("futureprobe")
        self.assertIn("newer build", str(caught.exception))

    def test_a_current_profile_loads_and_reports_its_engine(self):
        self.module("currentprobe",
                    PROFILE_SCHEMA={"version": make_profile.PROFILE_SCHEMA,
                                    "engine": "v2"})
        module, stamp = self.load("currentprobe")
        self.assertEqual(stamp["engine"], "v2")
        self.assertTrue(hasattr(module, "SEARCH"))

    def test_a_profile_written_before_the_stamp_still_loads_as_v1(self):
        self.module("legacyprobe")
        _module, stamp = self.load("legacyprobe")
        self.assertTrue(stamp["readable"])
        self.assertEqual(stamp["engine"], "v1")

    def test_a_malformed_stamp_is_refused(self):
        self.module("junkprobe", PROFILE_SCHEMA="not a version")
        with self.assertRaises(SystemExit):
            self.load("junkprobe")

    def test_every_profile_on_disk_still_loads(self):
        import glob
        for path in sorted(glob.glob(os.path.join(REPO_ROOT, "profiles",
                                                  "*.py"))):
            name = os.path.basename(path)[:-3]
            if name.startswith("_"):
                continue
            self.load(name)


class TestNumericAndShapeBoundaries(Clean):
    """Part 4. The renderer is shared by every engine, so its bounds are
    the ones that matter."""

    def test_a_weight_outside_the_scale_is_refused(self):
        for bad in (0, 99, 10 ** 9, -10):
            with self.assertRaises(ValueError, msg=bad):
                make_profile._weights([{"term": "x", "weight": bad}])

    def test_a_negative_weight_does_not_become_a_strong_positive(self):
        """abs() turned -10 into the strongest possible signal."""
        with self.assertRaises(ValueError):
            make_profile._weights([{"term": "x", "weight": -10}])

    def test_the_supported_scale_still_works(self):
        for good in (1, 2, 3, 4, 5):
            self.assertEqual(
                make_profile._weights([{"term": "x", "weight": good}]),
                {"x": good})

    def test_a_penalty_keeps_its_wider_documented_range(self):
        """penalty_terms are severity 1-12 by schema, negated at render."""
        self.assertEqual(
            make_profile._weights([{"term": "x", "weight": 12}], sign=-1),
            {"x": -12})
        with self.assertRaises(ValueError):
            make_profile._weights([{"term": "x", "weight": 13}], sign=-1)

    def test_a_non_integer_weight_is_refused(self):
        for bad in (True, 2.5, "3", None, [3]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                make_profile._weights([{"term": "x", "weight": bad}])

    def test_a_bool_is_not_a_year(self):
        with self.assertRaises(ValueError):
            make_profile._years(True)

    def test_a_float_year_is_refused_rather_than_truncated(self):
        with self.assertRaises(ValueError):
            make_profile._years(1.9)

    def test_a_whole_float_is_accepted(self):
        self.assertEqual(make_profile._years(4.0), 4)

    def test_the_ordinary_range_still_works(self):
        for years in (0, 1, 4, 40, 60):
            self.assertEqual(make_profile._years(years), years)

    def test_render_refuses_a_malformed_answer_before_writing_a_profile(self):
        data = {"field_summary": "s", "notes": "n", "years_experience": 2,
                "role_keywords": ["react native developer"],
                "skill_weights": [{"term": "react native", "weight": 99}],
                "penalty_terms": [], "domain_half_a": [], "domain_half_b": [],
                "domain_title_terms": [], "domain_bonus": 0,
                "title_hints": ["react native"], "title_exclude": []}
        prefs = {"locations": ["Remote"], "exclude_levels": [],
                 "min_comp_usd": None, "avoid": []}
        with self.assertRaises(ValueError):
            make_profile.render("boundsprobe", data, prefs)


class TestTheScorerHonoursTheProfile(Clean):
    """R2b end to end, through the module scraper actually scores with.

    Each case runs in its own interpreter: config and scraper bind at
    import, which is the moment under test, and re-importing them in
    process would not reproduce it.
    """

    SCRIPT = """
import sys, types, os, json
sys.path[:0] = [%(root)r, %(auto)r]
prof = types.ModuleType("profiles.probe")
%(stamp)s
prof.SCORING = {"skill_weights": {"react native": 5, "react": 3}}
sys.modules["profiles.probe"] = prof
os.environ["JOB_PROFILE"] = "probe"
%(env)s
import config, skill_concepts, scraper
# Names the compound ONLY. "react native and react" would score both
# under v2 as well, correctly, and prove nothing about which engine ran.
row = {"Title": "React Native Developer", "Company": "Synthetic",
       "Description": "we build react native apps"}
print(json.dumps({"derived": config.PROFILE_ENGINE,
                  "effective": skill_concepts.effective(),
                  "score": scraper.score_job(dict(row))["score"]}))
"""

    def probe(self, stamp, env):
        # Not named run(): that is TestCase.run.
        import json
        import subprocess
        script = self.SCRIPT % {
            "root": REPO_ROOT, "auto": AUTO_APPLY, "stamp": stamp,
            "env": env}
        clean = {k: v for k, v in os.environ.items() if k not in FLAGS}
        done = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True, env=clean,
                              cwd=REPO_ROOT)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    V1 = 'prof.PROFILE_SCHEMA = {"version": 1, "engine": "v1"}'
    V2 = 'prof.PROFILE_SCHEMA = {"version": 1, "engine": "v2"}'
    LEGACY = "pass"

    def test_a_v1_profile_on_a_worker_that_says_v2_scores_v1(self):
        got = self.probe(self.V1,
                       'os.environ["SWEEP_PROFILE_ENGINE_VERSION"] = "v2"')
        self.assertEqual(got["derived"], "v1")
        self.assertEqual(got["effective"]["version"], "v1")
        # v1 scores the substring "react" inside "react native" too: 5 + 3.
        self.assertEqual(got["score"], 8)

    def test_a_v2_profile_on_a_worker_that_says_v1_scores_v2(self):
        got = self.probe(self.V2,
                       'os.environ["SWEEP_PROFILE_ENGINE_VERSION"] = "v1"')
        self.assertEqual(got["derived"], "v2")
        self.assertEqual(got["effective"]["version"], "v2")
        # v2 counts the concept once: react is inside react native.
        self.assertEqual(got["score"], 5)

    def test_a_worker_with_no_engine_variable_at_all_still_honours_it(self):
        """The commonest mismatch: an updated worker nobody configured."""
        got = self.probe(self.V2, "pass")
        self.assertEqual(got["effective"]["version"], "v2")
        self.assertEqual(got["score"], 5)

    def test_a_stale_step_flag_on_the_worker_cannot_override_the_profile(self):
        got = self.probe(self.V1, 'os.environ["SWEEP_SKILL_CONCEPTS"] = "1"')
        self.assertEqual(got["effective"]["version"], "v1")
        self.assertEqual(got["score"], 8)

    def test_a_profile_written_before_the_stamp_scores_v1(self):
        got = self.probe(self.LEGACY,
                       'os.environ["SWEEP_PROFILE_ENGINE_VERSION"] = "v2"')
        self.assertEqual(got["derived"], "v1")
        self.assertEqual(got["score"], 8)

    def test_role_families_cannot_be_switched_on_by_a_bound_profile(self):
        got = self.probe(self.V2, 'os.environ["SWEEP_ROLE_FAMILIES"] = "1"')
        self.assertFalse(got["effective"]["roles"])

    def test_with_no_profile_the_environment_still_answers(self):
        """CLI and background runs with no profile keep the old contract."""
        import json
        import subprocess
        script = ("import sys, os, json\n"
                  f"sys.path[:0] = [{REPO_ROOT!r}, {AUTO_APPLY!r}]\n"
                  'os.environ["SWEEP_PROFILE_ENGINE_VERSION"] = "v2"\n'
                  "import skill_concepts\n"
                  "print(json.dumps(skill_concepts.effective()))\n")
        clean = {k: v for k, v in os.environ.items() if k not in FLAGS}
        done = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True, env=clean,
                              cwd=REPO_ROOT)
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        got = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(got["version"], "v2")
        self.assertEqual(got["source"], "SWEEP_PROFILE_ENGINE_VERSION")


# R6's evaluator-isolation tests are NOT in this release. They exercise
# bench/evaluate.py, which is an evaluation harness and is deliberately not
# shipped here — see docs/profile-engine-v2-production-candidate.md. The
# contract they protect (a bound profile wins over the environment) is
# still asserted above, from production code paths.

if __name__ == "__main__":
    unittest.main()
