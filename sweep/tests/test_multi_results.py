"""Multi-Track Phase 2B: the result side, over already-acquired copies.

finalize_multi(rows, tracks) evaluates every copy once per relevant track,
keeps it if any track is eligible, runs the Sweep's filters once, clusters
physical duplicates, picks each track's own best copy, the best track and the
representative, and returns one row per job carrying every eligible track's
evaluation. Relevance comes only from explicit provenance: PAID_TRACKS on a
paid copy, FREE on a free one (each track's title gate decides).

Nothing here runs a search. main() still refuses a schema-2 Sweep, and every
row's provenance is attributed by hand, standing in for what Phase 3 stamps.
Offline, synthetic rows and tracks.
"""
import copy
import csv
import io
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
from sweep.tests.test_one_track_goldens import REPO, _env, _load, derived_for, prefs_for

sys.path.insert(0, os.path.join(REPO, "auto-apply"))
import make_profile  # noqa: E402

MERN, MOBILE, AI = "a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2", "c3c3c3c3c3c3c3c3"


def track(tid, engine, years, skills, hints, exclude=(), penalties=None, halves=([], []),
          titles=(), bonus=0):
    return {"id": tid, "engine": engine,
            "SEARCH": {"role_keywords": [hints[0] + " developer"], "experience_years": years},
            "SETTINGS": {"max_experience_years": years + 3,
                         "candidate_experience_months": years * 12 + 4},
            "SCORING": {"skill_weights": skills, "penalty_terms": penalties or {},
                        "frontend_terms": list(halves[0]), "backend_terms": list(halves[1]),
                        "fullstack_title_terms": list(titles), "fullstack_bonus": bonus},
            "ATS_TITLE_HINTS": list(hints), "ATS_TITLE_EXCLUDE": list(exclude)}


TRACK_DATA = make_profile.check_tracks([
    track(MERN, "v2", 2, {"react": 5, "node.js": 4, "mongodb": 3, "typescript": 3},
          ["react", "node", "mern", "full stack"], halves=(["react"], ["node.js"]),
          titles=["mern"], bonus=4),
    track(MOBILE, "v2", 3, {"react native": 5, "kotlin": 4, "swift": 4, "typescript": 2},
          ["mobile", "android", "ios", "react native"]),
    track(AI, "v1", 0, {"python": 5, "pytorch": 4, "llm": 4},
          ["machine learning", "ml engineer", "ai engineer", "data scientist"],
          exclude=["intern"], penalties={"php": -6}),
])
SWEEP_SCORING = {"hard_drop_terms": ["intern", "internship"]}
FILTERS = {"max_age_days": 14, "min_comp_usd": 20000, "min_score": None,
           "drop_undated": False, "work_scope": "india", "remote_scopes": [],
           "drop_no_visa": False, "require_eor": False,
           "keep_restricted_if_hires_home": True, "drop_excluded": True,
           "experience_aggregate": "max", "home_utc_offset": 5.5}


def contexts(order=(MERN, MOBILE, AI)):
    by_id = {t["id"]: t for t in TRACK_DATA}
    return [scraper.track_context(by_id[tid], SWEEP_SCORING) for tid in order]


def job(name, title, description, paid=None, free=False, **over):
    row = {"Source": "linkedin" if paid else "greenhouse:probe", "Title": title,
           "Company": f"{name} Co", "Location": "Bengaluru, Karnataka", "Salary": "",
           "Experience": "", "Posted Date": "1 day ago",
           "Job URL": f"https://example.test/multi/{name}", "hires_home": "yes",
           "Description": description}
    if paid:
        row[scraper.PAID_TRACKS] = list(paid)
    if free:
        row[scraper.FREE] = True
    row.update(over)
    return row


# §18 of the Phase 2B brief, one row per case.
ROWS = [
    job("mern-only", "MERN Stack Developer", "React, Node.js and MongoDB.", paid=[MERN]),
    job("ai-only", "Machine Learning Engineer", "Python, PyTorch and LLM work.", paid=[AI]),
    job("shared", "Full Stack AI Engineer", "React, Node.js, Python and LLM.", paid=[AI, MERN]),
    job("free-mobile", "Android Developer", "Kotlin and React Native.", free=True),
    job("free-mern-mobile", "React Native Full Stack Developer",
        "React Native, React, Node.js and Kotlin.", free=True),
    job("a-too-senior", "React Developer",
        "React and Node.js. 8+ years of experience required.", paid=[MERN]),
    job("b-only", "React Native Developer",
        "React Native and Kotlin. 6+ years of experience required.", paid=[MERN, MOBILE]),
    job("a-wins", "React Developer", "React, Node.js, MongoDB and TypeScript.",
        paid=[MERN, MOBILE]),
    job("b-wins", "Mobile Developer", "React Native, Kotlin and Swift.", paid=[MERN, MOBILE]),
]


def urls(rows):
    return [r["apply_url"].rsplit("/", 1)[1] for r in rows]


class Isolated(unittest.TestCase):

    def setUp(self):
        bound = skill_concepts._BOUND
        self.addCleanup(setattr, skill_concepts, "_BOUND", bound)
        skill_concepts.bind(None)
        for patch in (mock.patch.dict(scraper.SETTINGS, FILTERS),
                      mock.patch.dict(os.environ, {skill_concepts.VERSION_ENV: "v1"})):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop(experience_guard.FLAG, None)

    def run_multi(self, rows=None, order=(MERN, MOBILE, AI)):
        return scraper.finalize_multi(copy.deepcopy(ROWS if rows is None else rows),
                                      contexts(order))

    def evals(self, out, name):
        row = next(r for r in out if r["apply_url"].endswith("/" + name))
        return row, json.loads(row["track_evals"])


# ===========================================================================
class Relevance(Isolated):

    def test_paid_copies_go_only_to_the_tracks_that_asked(self):
        tracks = contexts()
        got = {r["Job URL"].rsplit("/", 1)[1]: [t.id for t in scraper.relevant_tracks(r, tracks)]
               for r in ROWS}
        self.assertEqual(got["mern-only"], [MERN])
        self.assertEqual(got["ai-only"], [AI])
        self.assertEqual(got["shared"], [MERN, AI])            # track order, not request order

    def test_free_copies_go_to_each_track_whose_gate_admits_them(self):
        tracks = contexts()
        gate = lambda title: [t.id for t in scraper.relevant_tracks(  # noqa: E731
            job("x", title, "", free=True), tracks)]
        self.assertEqual(gate("Android Developer"), [MOBILE])
        self.assertEqual(gate("React Native Full Stack Developer"), [MERN, MOBILE])
        self.assertEqual(gate("ML Engineer Intern"), [])      # AI's own exclude wins
        self.assertEqual(gate("Accountant"), [])

    def test_repeated_ids_count_once(self):
        self.assertEqual([t.id for t in scraper.relevant_tracks(
            job("x", "React Developer", "", paid=[MERN, MERN]), contexts())], [MERN])

    def test_missing_or_malformed_provenance_is_refused_never_guessed(self):
        bad = {"no provenance": job("x", "React Developer", "React."),
               "both": job("x", "React Developer", "React.", paid=[MERN], free=True),
               "empty tracks": dict(job("x", "React Developer", "React."),
                                    **{scraper.PAID_TRACKS: []}),
               "tracks as a string": dict(job("x", "React Developer", "React."),
                                          **{scraper.PAID_TRACKS: MERN}),
               "unknown track": job("x", "React Developer", "React.", paid=["ffffffffffffffff"]),
               "free not True": dict(job("x", "React Developer", "React."),
                                     **{scraper.FREE: "yes"})}
        for label, row in bad.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    scraper.relevant_tracks(row, contexts())
                with self.assertRaises(ValueError):                    # the whole pass
                    self.run_multi(ROWS + [row])


# ===========================================================================
class Evaluation(Isolated):

    def test_relevance_eligibility_and_score_are_separate(self):
        out = self.run_multi()
        row, got = self.evals(out, "b-only")
        self.assertEqual(list(got), [MOBILE])     # relevant to MERN, not eligible for it
        self.assertNotIn("a-too-senior", urls(out))        # relevant only to MERN, dropped
        _row, ai = self.evals(out, "ai-only")
        self.assertEqual(list(ai), [AI])                   # MERN would score it; not asked

    def test_every_eligible_track_is_kept_and_winners_are_right(self):
        out = self.run_multi()
        a_row, a = self.evals(out, "a-wins")
        b_row, b = self.evals(out, "b-wins")
        self.assertEqual(set(a), {MERN, MOBILE})
        self.assertEqual(set(b), {MERN, MOBILE})
        self.assertGreater(a[MERN]["s"], a[MOBILE]["s"])
        self.assertEqual(a_row["best_track"], MERN)
        self.assertGreater(b[MOBILE]["s"], b[MERN]["s"])
        self.assertEqual(b_row["best_track"], MOBILE)
        for row, got in ((a_row, a), (b_row, b)):
            best = got[row["best_track"]]
            self.assertEqual((row["score"], row["matched_skills"]), (best["s"], best["m"]))

    def test_scores_are_each_tracks_own_evaluation(self):
        out = self.run_multi()
        by_id = {t.id: t for t in contexts()}
        for name in ("shared", "a-wins", "b-wins", "free-mern-mobile"):
            source = next(r for r in ROWS if r["Job URL"].endswith("/" + name))
            _row, got = self.evals(out, name)
            for tid, e in got.items():
                direct = scraper.evaluate(copy.deepcopy(source), by_id[tid].scoring)
                with self.subTest(name=name, track=tid):
                    self.assertEqual((e["s"], e["m"]), (direct.score, direct.matched_skills))

    def test_job_facts_once_per_copy_across_tracks_and_passes(self):
        rows = copy.deepcopy(ROWS)
        real, calls = scraper.job_facts, []
        with mock.patch.object(scraper, "job_facts", lambda r: calls.append(1) or real(r)):
            first = scraper.finalize_multi(rows, contexts())
            second = scraper.finalize_multi(rows, contexts())
        self.assertEqual(first, second)
        self.assertEqual(len(calls), len(rows))          # 9 copies, 3 tracks, 2 passes

    def test_track_order_and_the_process_engine_do_not_change_evaluations(self):
        with mock.patch.object(skill_concepts, "bind", side_effect=AssertionError("rebound")):
            forward = self.run_multi()
            backward = self.run_multi(order=(AI, MOBILE, MERN))
            with mock.patch.dict(os.environ, {skill_concepts.VERSION_ENV: "v2"}):
                other_engine = self.run_multi()
        norm = lambda out: {r["apply_url"]: json.loads(r["track_evals"]) for r in out}  # noqa
        self.assertEqual(norm(forward), norm(backward))
        self.assertEqual(forward, other_engine)

    def test_nothing_is_written_that_is_one_tracks(self):
        rows = copy.deepcopy(ROWS)
        scraper.finalize_multi(rows, contexts())
        for row in rows:
            for key in ("score", "matched_skills", "is_fullstack"):
                self.assertNotIn(key, row)

    def test_min_score_is_a_per_track_test(self):
        with mock.patch.dict(scraper.SETTINGS, min_score=5):
            out = self.run_multi()
        _row, got = self.evals(out, "a-wins")
        self.assertEqual(list(got), [MERN])                  # Mobile's 2 is under the floor

    def test_guard_verdicts_carry_their_track_and_are_recorded_once(self):
        rows = [job("guard", "React Native Developer",
                    "React Native and React. We require 8+ years of experience.",
                    paid=[MERN, MOBILE])]
        mark = len(experience_guard.DROPPED)
        self.addCleanup(lambda: experience_guard.DROPPED.__delitem__(slice(mark, None)))
        with mock.patch.dict(os.environ, {experience_guard.FLAG: "1"}), \
                mock.patch.dict(scraper.SETTINGS, drop_excluded=False):
            scraper.finalize_multi(rows, contexts())
            scraper.finalize_multi(rows, contexts())
        new = experience_guard.DROPPED[mark:]
        self.assertEqual(sorted(d["track"] for d in new), sorted([MERN, MOBILE]))
        self.assertTrue(all(d["action"] != "none" for d in new))


# ===========================================================================
class SweepFilters(Isolated):

    def test_the_sweep_filters_run_once_per_copy(self):
        extra = [job("stale", "React Developer", "React.", paid=[MERN, AI],
                     **{"Posted Date": "2020-01-02"}),
                 job("low-pay", "React Developer", "React.", paid=[MERN], Salary="INR 3 LPA"),
                 job("remote", "React Developer", "Fully remote. React.", paid=[MERN],
                     Location="Worldwide, Remote"),
                 job("abroad", "React Developer", "Onsite. React.", paid=[MERN],
                     Location="Berlin, Germany", hires_home="no")]
        out = self.run_multi(ROWS + extra)
        for name in ("stale", "low-pay", "remote", "abroad"):
            self.assertNotIn(name, urls(out))
        self.assertEqual({k: scraper.LAST_STATS[k] for k in
                          ("stale", "low_salary", "wrong_arrangement", "off_geography")},
                         {"stale": 1, "low_salary": 1, "wrong_arrangement": 1,
                          "off_geography": 1})

    def test_remote_reachability(self):
        rows = [job("worldwide", "React Developer", "Fully remote. React.", paid=[MERN],
                    Location="Worldwide, Remote"),
                job("onsite", "React Developer", "Onsite. React.", paid=[MERN])]
        with mock.patch.dict(scraper.SETTINGS, work_scope=None,
                             remote_scopes=["worldwide", "remote"]):
            out = self.run_multi(rows)
        self.assertEqual(urls(out), ["worldwide"])
        self.assertEqual(scraper.LAST_STATS["unreachable"], 1)


# ===========================================================================
class Clusters(Isolated):

    def dup(self, n, description, **over):
        return job(f"dup-{n}", "Frontend Engineer", description, paid=[MERN, MOBILE],
                   req_number="R-77", **over)

    def test_each_track_keeps_its_own_copy_and_the_best_track_represents(self):
        rows = [self.dup(1, "React, Node.js and MongoDB."),
                self.dup(2, "React Native, Kotlin, Swift and TypeScript.")]
        tracks = contexts()
        acquired = copy.deepcopy(rows)
        copies, _stats = scraper.score_and_filter_multi(acquired, tracks)
        [cluster] = scraper.rank_clusters(copies, tracks, acquired)
        self.assertEqual(cluster.chosen[MERN][0], 0)           # MERN prefers copy 1
        self.assertEqual(cluster.chosen[MOBILE][0], 1)         # Mobile prefers copy 2
        out = scraper.finalize_multi(copy.deepcopy(rows), tracks)
        self.assertEqual(len(out), 1)                          # one physical job
        row, got = out[0], json.loads(out[0]["track_evals"])
        best = row["best_track"]
        self.assertEqual(row["apply_url"], rows[cluster.chosen[best][0]]["Job URL"])
        # The losing track keeps its own copy's score, not the representative's.
        for tid, index in ((MERN, 0), (MOBILE, 1)):
            direct = scraper.evaluate(copy.deepcopy(rows[index]),
                                      next(t for t in tracks if t.id == tid).scoring)
            self.assertEqual(got[tid]["s"], direct.score)

    def test_company_and_title_duplicates_tie_to_arrival(self):
        rows = [job("ct-1", "React Developer", "React and Node.js.", free=True,
                    Company="Acme Labs"),
                job("ct-2", "Developer, React", "React and Node.js.", free=True,
                    Company="Acme Labs Pvt Ltd")]
        out = self.run_multi(rows)
        self.assertEqual(urls(out), ["ct-1"])

    def test_a_best_track_tie_goes_to_track_order(self):
        tie = [job("tie", "Software Engineer", "Excel.", paid=[MERN, MOBILE])]
        self.assertEqual(self.run_multi(tie)[0]["best_track"], MERN)
        self.assertEqual(self.run_multi(tie, order=(MOBILE, MERN, AI))[0]["best_track"],
                         MOBILE)

    def test_clusters_rank_by_best_score_then_arrival(self):
        out = self.run_multi()
        scores = [r["score"] for r in out]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(out), len(set(r["apply_url"] for r in out)))

    def test_found_by_is_every_acquisition_in_track_order_then_free(self):
        rows = [job("fb-1", "React Developer", "React.", paid=[AI, MERN], req_number="R-9"),
                job("fb-2", "React Developer", "React.", free=True, req_number="R-9")]
        out = self.run_multi(rows)
        self.assertEqual(out[0]["found_by"], f"{MERN};{AI};free")


# ===========================================================================
class AcquisitionProvenance(Isolated):
    """found_by is which searches surfaced a job — every acquired copy of it,
    whatever became of that copy — never which tracks it suits."""

    def same_job(self, name, title, description, **kw):
        return job(name, title, description, req_number="R-500", **kw)

    def test_a_discarded_paid_copy_still_counts_and_stays_out_of_track_evals(self):
        rows = [self.same_job("p-mobile", "Mobile Developer", "React Native, Kotlin and Swift.",
                              paid=[MOBILE]),
                self.same_job("f-android", "Android Developer", "Kotlin.", free=True),
                # MERN's own copy: too senior for MERN, so dropped.
                self.same_job("p-mern", "React Developer",
                              "React. 8+ years of experience required.", paid=[MERN])]
        [row] = self.run_multi(rows)
        self.assertEqual(row["best_track"], MOBILE)
        self.assertEqual(list(json.loads(row["track_evals"])), [MOBILE])
        self.assertEqual(row["found_by"], f"{MERN};{MOBILE};free")   # MERN surfaced it

    def test_a_filtered_free_copy_still_counts(self):
        rows = [self.same_job("f-stale", "React Developer", "React.", free=True,
                              **{"Posted Date": "2020-01-02"}),
                self.same_job("p-mern", "React Developer", "React and Node.js.", paid=[MERN])]
        [row] = self.run_multi(rows)
        self.assertEqual(row["apply_url"], "https://example.test/multi/p-mern")
        self.assertEqual(row["found_by"], f"{MERN};free")

    def test_a_job_whose_every_copy_fails_leaves_no_result(self):
        doomed = [self.same_job("d-1", "React Developer", "React.", paid=[MERN, AI],
                                **{"Posted Date": "2020-01-02"}),
                  self.same_job("d-2", "React Developer",
                                "React. 8+ years of experience required.", paid=[MERN]),
                  job("keyless", "Accountant", "Ledgers.", free=True, Company="",
                      **{"Job URL": ""})]
        out = self.run_multi(ROWS + doomed)
        self.assertEqual(len(out), len(self.run_multi()))              # no ghost rows
        self.assertFalse({"d-1", "d-2", "keyless"} & set(urls(out)))

    def test_order_is_track_order_then_free_whatever_the_arrival(self):
        import itertools
        base = [self.same_job("p-mobile", "Mobile Developer", "React Native and Kotlin.",
                              paid=[MOBILE]),
                self.same_job("f-android", "Android Developer", "Kotlin.", free=True),
                self.same_job("p-ai-mern", "React Developer", "React.", paid=[AI, MERN])]
        seen = {self.run_multi(list(p))[0]["found_by"] for p in itertools.permutations(base)}
        self.assertEqual(seen, {f"{MERN};{MOBILE};{AI};free"})


# ===========================================================================
class ResultRows(Isolated):

    def test_columns_values_and_nothing_private(self):
        out = self.run_multi()
        for row in out:
            self.assertEqual(list(row), scraper.MULTI_OUTPUT_COLUMNS)
            got = json.loads(row["track_evals"])
            self.assertIn(row["best_track"], got)
            self.assertTrue(all(set(v) == {"s", "m"} for v in got.values()))
            self.assertTrue(set(row["found_by"].split(";")) <= {MERN, MOBILE, AI, "free"})
        # The exact column list above already keeps every private key out;
        # this keeps the description text out of every value.
        blob = json.dumps(out)
        for text in ("Kotlin and React Native.", "8+ years of experience required"):
            self.assertNotIn(text, blob)

    def test_track_evals_are_deterministic_json(self):
        a, b = self.run_multi(), self.run_multi()
        self.assertEqual([r["track_evals"] for r in a], [r["track_evals"] for r in b])
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=scraper.MULTI_OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(a)
        back = list(csv.DictReader(io.StringIO(buffer.getvalue())))
        self.assertEqual([json.loads(r["track_evals"]) for r in back],
                         [json.loads(r["track_evals"]) for r in a])

    def test_track_stats(self):
        self.run_multi()
        stats = scraper.LAST_TRACK_STATS
        self.assertEqual(stats["copies"], len(ROWS))
        self.assertEqual(stats["eligible_copies"], len(ROWS) - 1)   # a-too-senior
        self.assertEqual(set(stats["eligible_by_track"]), {MERN, MOBILE, AI})
        self.assertEqual(stats["clusters"], scraper.LAST_STATS["kept"])
        self.assertEqual(stats["eligible_by_track"][AI], 2)          # ai-only, shared


# ===========================================================================
PARITY = """
import copy, json, socket, sys
mode, name, repo, rows_path = sys.argv[1:5]
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network"))
sys.argv = ["scraper.py", "--profile", name]
sys.path[:0] = [".", repo, repo + "/auto-apply"]
import config, scraper
rows = json.load(open(rows_path))
stages = []
collect = lambda s, got: stages.append([s, len(got)])
if mode == "one":
    rows = [r for r in rows if r.get("_tracks") or scraper.is_dev_title(r.get("Title"))]
    for r in rows:
        r.pop("_tracks", None); r.pop("_free", None)
    scraper.score_and_filter(copy.deepcopy(rows), collect)
    out = scraper.finalize(rows)
    blocked = None
else:
    scraper.score_and_filter_multi(copy.deepcopy(rows), scraper.TRACK_CONTEXTS, collect)
    out = scraper.finalize_multi(rows, scraper.TRACK_CONTEXTS)
    try:
        sys.argv = ["scraper.py", "--profile", name, "--dry-run", "--json"]
        scraper.main(); blocked = "ran"
    except SystemExit as exc:
        blocked = str(exc)
print(json.dumps({"out": out, "stats": dict(scraper.LAST_STATS), "stages": stages,
                  "blocked": blocked}))
"""


class OneTrackParity(unittest.TestCase):
    """A schema-2 Sweep of one track decides exactly as the schema-1 profile of
    the same résumé: same jobs, scores, survivors, order, stats and funnel."""

    def test_schema_1_and_a_one_track_sweep_agree(self):
        home = tempfile.mkdtemp(prefix="multi-parity-")
        self.addCleanup(shutil.rmtree, home, True)
        os.makedirs(os.path.join(home, "profiles"))
        open(os.path.join(home, "profiles", "__init__.py"), "w").close()
        tid = "d4d4d4d4d4d4d4d4"
        rows = []
        for n, r in enumerate(_load("scoring_rows.json")["rows"]):
            r = {k: v for k, v in r.items() if k != "_case"}
            r.update({scraper.PAID_TRACKS: [tid]} if r["Source"] == "linkedin"
                     else {scraper.FREE: True})
            rows.append(r)
        # A second copy of a paid job, found by the free half too: dedupe across
        # the paid/free line must match schema 1's.
        rows.append(dict(rows[0], **{"Job URL": "https://example.test/jobs/01-free-copy",
                                     "Source": "lever:acmelabs"}))
        rows_path = os.path.join(home, "rows.json")
        with open(rows_path, "w") as fh:
            json.dump(rows, fh)
        derived, prefs = derived_for("scoring"), prefs_for("scoring")
        for engine in ("v1", "v2"):
            saved = os.environ.get(skill_concepts.VERSION_ENV)
            os.environ[skill_concepts.VERSION_ENV] = engine
            try:
                one = make_profile.render(f"one_{engine}", derived, prefs)
                sweep = make_profile.render_sweep(f"sweep_{engine}", [
                    {"id": tid, "engine": engine, "derived": derived}], prefs)
            finally:
                os.environ.pop(skill_concepts.VERSION_ENV, None)
                if saved is not None:
                    os.environ[skill_concepts.VERSION_ENV] = saved
            for name, source in ((f"one_{engine}", one), (f"sweep_{engine}", sweep)):
                with open(os.path.join(home, "profiles", f"{name}.py"), "w") as fh:
                    fh.write(source)
            got = {}
            for mode, name in (("one", f"one_{engine}"), ("multi", f"sweep_{engine}")):
                run = subprocess.run([sys.executable, "-c", PARITY, mode, name, REPO, rows_path],
                                     capture_output=True, text=True, cwd=home, env=_env(home),
                                     timeout=120)
                self.assertEqual(run.returncode, 0, run.stderr[-3000:])
                got[mode] = json.loads(run.stdout)
            with self.subTest(engine=engine):
                one_out, multi_out = got["one"]["out"], got["multi"]["out"]
                self.assertGreater(len(one_out), 5)
                self.assertEqual([{k: r[k] for k in scraper.OUTPUT_COLUMNS} for r in multi_out],
                                 one_out)
                self.assertEqual(got["multi"]["stats"], got["one"]["stats"])
                self.assertEqual(got["multi"]["stages"], got["one"]["stages"])
                self.assertTrue(all(r["best_track"] == tid for r in multi_out))
                self.assertTrue(all(list(json.loads(r["track_evals"])) == [tid]
                                    for r in multi_out))
                self.assertIn("free", {p for r in multi_out for p in r["found_by"].split(";")})
                # main() still refuses a schema-2 Sweep, before any planning.
                self.assertIn("cannot run it yet", got["multi"]["blocked"])


class SchemaOneUntouched(unittest.TestCase):

    def test_finalize_is_the_one_profile_path(self):
        self.assertEqual(scraper.TRACK_CONTEXTS, ())
        out = scraper.finalize([dict(job("plain", "React Developer", "React."),
                                     **{"Posted Date": scraper.datetime.now().strftime("%Y-%m-%d")})])
        for row in out:
            self.assertEqual(list(row), scraper.OUTPUT_COLUMNS)


if __name__ == "__main__":
    unittest.main()
