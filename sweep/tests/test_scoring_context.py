"""Multi-Track Phase 1: scoring through an explicit, immutable context.

scraper.evaluate(row, ctx) is pure and reads every profile-set value from a
ScoringContext; score_job(row) is the one-profile wrapper over it; the memo is
keyed by context. These tests pin what makes more than one context possible
in one process — independence, engine choice without rebinding, rows left
untouched — and that the wrapper is still the old API. One-profile output is
pinned byte for byte by test_one_track_goldens.

Offline. Contexts are built from synthetic tables; the one fresh-interpreter
case loads a synthetic profile rendered by make_profile under each stamp.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import experience_guard
import scraper
import skill_concepts
from sweep.tests.test_one_track_goldens import REPO, _drive, _env, derived_for, prefs_for

SETTINGS = {"max_experience_years": 6, "candidate_experience_months": 40}
MERN = {"skill_weights": {"react": 5, "react.js": 5, "reactjs": 5, "node.js": 4, "mongodb": 3},
        "penalty_terms": {"salesforce": -8}, "frontend_terms": [], "backend_terms": [],
        "fullstack_title_terms": [], "fullstack_bonus": 0,
        "hard_drop_terms": ["intern", "junior"]}
DATA = dict(MERN, skill_weights={"python": 5, "sql": 4}, penalty_terms={"react": -6})


def row(**over):
    base = {"Source": "greenhouse:probe", "Title": "React Developer", "Company": "Probe Co",
            "Location": "Bengaluru, Karnataka", "Salary": "", "Experience": "",
            "Posted Date": "1 day ago", "Job URL": "https://example.test/jobs/probe",
            "Description": "React, React.js and ReactJS with Node.js, MongoDB, Python and SQL."}
    base.update(over)
    return base


def ctx(scoring=MERN, engine="v2", **settings):
    return scraper.scoring_context(scoring, dict(SETTINGS, **settings), engine)


class EngineIsolated(unittest.TestCase):
    """Pin the process-global engine for the test, and put it back."""

    def setUp(self):
        bound = skill_concepts._BOUND
        self.addCleanup(setattr, skill_concepts, "_BOUND", bound)
        skill_concepts.bind(None)
        env = mock.patch.dict(os.environ, {skill_concepts.VERSION_ENV: "v1"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(experience_guard.FLAG, None)


class ContextShape(EngineIsolated):

    def test_frozen(self):
        c = ctx()
        with self.assertRaises(AttributeError):
            c.engine = "v1"
        with self.assertRaises(ValueError):
            ctx(engine="mixed")

    def test_the_default_context_is_the_live_tables(self):
        """default_context() reads the module tables at call time, which is why
        it is built per call: callers swap them in place."""
        c = scraper.default_context()
        self.assertEqual([(t, w, p) for t, w, p in c.skill_terms],
                         [(t, w, p) for t, (w, p) in scraper.SKILL_PATTERNS.items()])
        self.assertEqual(list(c.concepts), scraper.SKILL_CONCEPTS)
        self.assertEqual(list(c.hard_drop), list(scraper.HARD_DROP_PATTERNS.values()))
        before = c.key
        saved = dict(scraper.HARD_DROP_PATTERNS)
        self.addCleanup(lambda: (scraper.HARD_DROP_PATTERNS.clear(),
                                 scraper.HARD_DROP_PATTERNS.update(saved)))
        scraper.HARD_DROP_PATTERNS["zorbmanager"] = scraper._compile("zorbmanager")
        self.assertNotEqual(scraper.default_context().key, before)

    def test_the_default_engine_follows_the_bound_stamp(self):
        self.assertEqual(scraper.default_context().engine, "v1")
        skill_concepts.bind("v2")
        self.assertEqual(scraper.default_context().engine, "v2")
        skill_concepts.bind("v1")
        with mock.patch.dict(os.environ, {skill_concepts.VERSION_ENV: "v2"}):
            self.assertEqual(scraper.default_context().engine, "v1")   # the stamp wins

    def test_the_builder_compiles_what_import_compiled(self):
        c = scraper.scoring_context(scraper.SCORING, scraper.SETTINGS, "v1")
        self.assertEqual(c.key, scraper.default_context().key)


class ContextIndependence(EngineIsolated):
    """The Multi-Track-enabling property: one untouched job, two contexts."""

    def test_two_contexts_score_one_job_differently(self):
        a, b = ctx(MERN), ctx(DATA)
        job = row()
        ea, eb = scraper.evaluate(job, a), scraper.evaluate(job, b)
        self.assertTrue(ea.eligible and eb.eligible)
        self.assertEqual((ea.score, ea.matched_skills), (12, "React, Node.js, MongoDB"))
        self.assertEqual((eb.score, eb.matched_skills), (3, "Python, sql"))   # 5 + 4 - 6

    def test_order_does_not_matter_and_nothing_leaks(self):
        a, b = ctx(MERN), ctx(DATA)
        fresh_a, fresh_b = scraper.evaluate(row(), a), scraper.evaluate(row(), b)
        ab, ba = row(), row()
        got_ab = (scraper.evaluate_once(ab, a), scraper.evaluate_once(ab, b))
        got_ba = (scraper.evaluate_once(ba, b), scraper.evaluate_once(ba, a))
        self.assertEqual(got_ab, (fresh_a, fresh_b))
        self.assertEqual(got_ba, (fresh_b, fresh_a))
        self.assertIs(scraper.evaluate_once(ab, a), got_ab[0])     # A again: A's memo
        self.assertEqual(set(ab[scraper.EVALS]), {a.key, b.key})

    def test_a_memo_entry_is_never_handed_to_another_context(self):
        a, b = ctx(MERN), ctx(DATA)
        job = row()
        scraper.evaluate_once(job, a)
        self.assertNotEqual(scraper.evaluate_once(job, b), job[scraper.EVALS][a.key])
        self.assertEqual(scraper.evaluate_once(job, b), scraper.evaluate(row(), b))

    def test_a_context_that_drops_does_not_drop_for_another(self):
        senior = ctx(MERN, max_experience_years=10)
        junior = ctx(MERN, max_experience_years=2)
        job = row(Description="React and Node.js. 5+ years of experience required.")
        self.assertTrue(scraper.evaluate_once(job, senior).eligible)
        self.assertEqual(scraper.evaluate_once(job, junior),
                         scraper.Evaluation(False, "excluded", years_required=5))
        self.assertTrue(scraper.evaluate_once(job, senior).eligible)

    def test_equal_contents_share_a_key_and_any_difference_splits_it(self):
        self.assertEqual(ctx(MERN).key, ctx(copy.deepcopy(MERN)).key)
        for different in (ctx(MERN, engine="v1"), ctx(DATA), ctx(MERN, max_experience_years=7),
                          ctx(dict(MERN, fullstack_bonus=4)),
                          ctx(dict(MERN, hard_drop_terms=["intern"]))):
            self.assertNotEqual(different.key, ctx(MERN).key)


class EngineIndependence(EngineIsolated):
    """v1 and v2 in one process, the global engine never consulted or moved."""

    def test_v1_and_v2_side_by_side_without_rebinding(self):
        v1, v2 = ctx(MERN, engine="v1"), ctx(MERN, engine="v2")
        job = row(Description="React, React.js and ReactJS with Node.js.")
        with mock.patch.object(skill_concepts, "bind",
                               side_effect=AssertionError("evaluate rebound the engine")):
            for pinned in ("v1", "v2"):          # the process says the opposite, too
                with mock.patch.dict(os.environ, {skill_concepts.VERSION_ENV: pinned}):
                    first, second, again = (scraper.evaluate(job, v1), scraper.evaluate(job, v2),
                                            scraper.evaluate(job, v1))
                    self.assertEqual((first.score, first.matched_skills),
                                     (19, "react, react.js, reactjs, node.js"))
                    self.assertEqual((second.score, second.matched_skills), (9, "React, Node.js"))
                    self.assertEqual(again, first)
                    self.assertEqual(skill_concepts.engine_version(), pinned)
        self.assertIsNone(skill_concepts._BOUND)


class PureEvaluation(EngineIsolated):

    def test_evaluate_and_job_facts_write_nothing(self):
        for job in (row(), row(Title="Junior React Developer"), row(Company="Jobgether")):
            before = copy.deepcopy(job)
            scraper.job_facts(job)
            scraper.evaluate(job, ctx())
            scraper.evaluate(job, scraper.default_context())
            self.assertEqual(job, before)
            self.assertEqual(list(job), list(before))

    def test_evaluate_once_writes_only_its_memo(self):
        job = row()
        before = copy.deepcopy(job)
        scraper.evaluate_once(job, ctx())
        self.assertEqual({k: v for k, v in job.items() if k != scraper.EVALS}, before)
        for legacy in ("score", "matched_skills", "is_fullstack", scraper.SCORED,
                       scraper.SCORED_BY, "remote_scope"):
            self.assertNotIn(legacy, job)

    def test_the_guard_is_returned_not_recorded(self):
        job = row(Description="React. We require 8+ years of experience.")
        mark = len(experience_guard.DROPPED)
        self.addCleanup(lambda: experience_guard.DROPPED.__delitem__(slice(mark, None)))
        with mock.patch.dict(os.environ, {experience_guard.FLAG: "1"}), \
                mock.patch.dict(scraper.SETTINGS, max_experience_years=10,
                                candidate_experience_months=40):
            got = scraper.evaluate(job, ctx(max_experience_years=10))
            self.assertEqual(got.exp_verdict["action"], "hard_drop")
            self.assertFalse(got.eligible)
            self.assertEqual(len(experience_guard.DROPPED), mark)
            self.assertIsNone(scraper.score_job(copy.deepcopy(job)))   # the wrapper records
            self.assertEqual(len(experience_guard.DROPPED), mark + 1)


class CompatibilityWrapper(EngineIsolated):
    """score_job(row): evaluate under the default context, then today's writes."""

    LEGACY_ORDER = ["score", "matched_skills", "is_fullstack", "years_required",
                    "remote_scope", "remote_regions", "visa", "eor", "timezones",
                    "tz_gap", "remote?", "hr_email", "hr_phone"]

    def test_a_kept_row_gets_todays_fields_in_todays_order(self):
        job = row(Description="React and Node.js. Mail careers@example.com.")
        before = copy.deepcopy(job)
        got = scraper.evaluate(job, scraper.default_context())
        self.assertIs(scraper.score_job(job), job)
        self.assertEqual(list(job)[len(before):], self.LEGACY_ORDER)
        self.assertEqual({k: job[k] for k in before}, before)
        self.assertEqual((job["score"], job["matched_skills"], job["is_fullstack"],
                          job["years_required"]),
                         (got.score, got.matched_skills, got.is_fullstack, got.years_required))
        self.assertEqual(job["hr_email"], "careers@example.com")

    def test_a_dropped_row_is_untouched(self):
        for job in (row(Company="Jobgether"), row(Title="Intern, React")):
            before = copy.deepcopy(job)
            self.assertIsNone(scraper.score_job(job))
            self.assertEqual(job, before)


class KeyedMemo(EngineIsolated):
    """_score_once: finalize(memo=True)'s one-profile memo, now per context."""

    def counting(self):
        calls, real = [], scraper.score_job

        def count(r):
            calls.append(1)
            return real(r)
        patch = mock.patch.object(scraper, "score_job", count)
        patch.start()
        self.addCleanup(patch.stop)
        return calls

    def test_a_stale_false_verdict_no_longer_suppresses_scoring(self):
        job = row()
        job[scraper.SCORED] = False                  # left by no context of this run
        self.assertIs(scraper._score_once(job), job)
        self.assertIs(job[scraper.SCORED], True)
        self.assertEqual(job[scraper.SCORED_BY], scraper.default_context().key)

    def test_a_verdict_is_reused_only_under_its_own_context(self):
        calls = self.counting()
        job = row()
        scraper._score_once(job)
        scraper._score_once(job)
        self.assertEqual(len(calls), 1)               # the memo still works
        job[scraper.SCORED], job[scraper.SCORED_BY] = False, "another context's key"
        self.assertIs(scraper._score_once(job), job)
        self.assertEqual(len(calls), 2)

    def test_repeated_passes_are_deterministic(self):
        rows = [row(**{"Job URL": f"https://example.test/{i}", "Title": t})
                for i, t in enumerate(("React Developer", "Junior React Developer",
                                       "Senior React Developer"))]
        verdicts = [scraper.score_job(copy.deepcopy(r)) is not None for r in rows]
        plain = scraper.finalize(copy.deepcopy(rows))
        held = copy.deepcopy(rows)
        self.assertEqual(scraper.finalize(held, memo=True), plain)
        self.assertEqual(scraper.finalize(held, memo=True), plain)
        self.assertEqual([r[scraper.SCORED] for r in held], verdicts)
        self.assertIn(False, verdicts)


PROBE = """
import json, sys
name, repo = sys.argv[1], sys.argv[2]
sys.argv = ["scraper.py", "--profile", name]
sys.path[:0] = [".", repo, repo + "/auto-apply"]
import config, scraper
c = scraper.default_context()
print(json.dumps({
    "stamp": config.PROFILE_ENGINE, "engine": c.engine,
    "builder_agrees": scraper.scoring_context(config.SCORING, config.SETTINGS, c.engine).key == c.key,
    "weights": {t: w for t, w, _p in c.skill_terms} == config.SCORING["skill_weights"],
    "penalties": {t: w for t, w, _p in c.penalties} == config.SCORING["penalty_terms"],
    "hard_drop": [p.pattern for p in c.hard_drop]
                 == [scraper._compile(t).pattern for t in config.SCORING["hard_drop_terms"]],
    "experience": [c.max_experience_years, c.candidate_experience_months]
                  == [config.SETTINGS["max_experience_years"],
                      config.SETTINGS["candidate_experience_months"]]}))
"""


class ProfileContext(unittest.TestCase):
    """A real stamped profile, in a fresh interpreter, yields its own context."""

    def test_each_stamp_yields_its_engine_and_its_tables(self):
        work = tempfile.mkdtemp(prefix="scoring-context-")
        self.addCleanup(shutil.rmtree, work, True)
        spec = {"name": "phase1_probe", "derived": derived_for("scoring"),
                "prefs": prefs_for("scoring")}
        for stamp in ("v1", "v2"):
            home = os.path.join(work, stamp)
            os.makedirs(os.path.join(home, "profiles"))
            open(os.path.join(home, "profiles", "__init__.py"), "w").close()
            source = _drive("render", {"cases": [spec]}, home, stamp)["sources"]["phase1_probe"]
            with open(os.path.join(home, "profiles", "phase1_probe.py"), "w",
                      encoding="utf-8") as fh:
                fh.write(source)
            got = subprocess.run([sys.executable, "-c", PROBE, "phase1_probe", REPO],
                                 capture_output=True, text=True, cwd=home, env=_env(home),
                                 timeout=120)
            self.assertEqual(got.returncode, 0, got.stderr[-2000:])
            with self.subTest(stamp=stamp):
                self.assertEqual(json.loads(got.stdout),
                                 {"stamp": stamp, "engine": stamp, "builder_agrees": True,
                                  "weights": True, "penalties": True, "hard_drop": True,
                                  "experience": True})


if __name__ == "__main__":
    unittest.main()
