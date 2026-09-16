"""The market the repo ships with, for a machine that has none.

corpus_signal already solved this for skill WEIGHTS: no output/ means the
committed frequency table answers instead of every term abstaining. Title
derivation had no such fallback, and the asymmetry reached production as an
upload that could not succeed:

    Modal answered 200, fields_for returned no role_keywords,
    local_profile escalated, and the screen blamed the PDF.

The cause is not the file. `from_resume()` drops internships on purpose
(local_extract.countable, whose comment records why), so a résumé whose
every role is an internship has no own-title floor — and with no corpus
there is nothing else. Measured on the résumé that failed: 8 keywords with
a corpus, 0 without.

These hold the fallback and the rules around it. They are deliberately
model-free: the invariant is about the CORPUS, and a test that needed
qwen3 running would not be run.
"""

import gzip
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

import local_search  # noqa: E402

# The résumé class that failed in production, as local_extract read it.
# Every role is an internship or a study position, which is exactly what
# countable() filters out — so from_resume gives nothing and the corpus is
# the only thing left.
INTERNSHIP_ONLY = {
    "skills": ["python", "django", "reactjs", "react", "node.js",
               "javascript", "tensorflow", "pytorch", "aws", "docker",
               "kubernetes", "postgresql", "mysql", "mongodb", "flask",
               "firebase", "rest api", "ci/cd"],
    "employment": [
        {"title": "Full Stack Developer (Intern)",
         "company": "Appable Technologies Pvt.Ltd."},
        {"title": "Front End Developer (Intern)",
         "company": "Xploremy City Pvt.Ltd."},
        {"title": "Student Lead",
         "company": "Chandigarh University Technology Business Incubator"},
    ],
}

GOOD_ROW = ["full stack engineer", 40, ["react", "node.js"], "acme"]


def artifact(rows=None, **overrides):
    """A frozen file on disk, valid unless a test breaks it on purpose."""
    payload = {"schema_version": local_search.FROZEN_SCHEMA,
               "generated_at": "2026-09-17",
               "row_count": 0,
               "rows": rows if rows is not None else []}
    payload.update(overrides)
    handle, path = tempfile.mkstemp(suffix=".json.gz")
    os.close(handle)
    with gzip.GzipFile(path, "wb", mtime=0) as fh:
        fh.write(json.dumps(payload).encode())
    return path


def enough(n=local_search.MIN_CORPUS_ROWS):
    """n rows that differ, so vocabulary() is not one term repeated."""
    return [["engineer %d" % i, 10, ["react"], "co %d" % i] for i in range(n)]


class TestWhichCorpusAnswers(unittest.TestCase):
    """The four rules, and never a fifth: live, frozen, none, never both."""

    def test_1_a_healthy_live_corpus_beats_the_frozen_one(self):
        live = tempfile.mkdtemp()
        with open(os.path.join(live, "jobs.csv"), "w", encoding="utf-8") as fh:
            fh.write("title,score,matched_skills,company\n")
            for i in range(local_search.MIN_CORPUS_ROWS):
                fh.write(f"live engineer {i},30,react,co{i}\n")
        rows, source = local_search.market_rows(
            live, artifact(rows=enough()))
        self.assertEqual(source, "live")
        self.assertTrue(all("live engineer" in t for t, _s, _sk, _c in rows))

    def test_2_no_live_corpus_uses_the_frozen_one(self):
        rows, source = local_search.market_rows(
            tempfile.mkdtemp(), artifact(rows=enough()))
        self.assertEqual(source, "frozen")
        self.assertEqual(len(rows), local_search.MIN_CORPUS_ROWS)

    def test_3_a_THIN_live_corpus_uses_the_frozen_one(self):
        """37 rows is not a small market, it is noise. corpus_signal makes
        the same judgement about frequencies at the same number."""
        live = tempfile.mkdtemp()
        with open(os.path.join(live, "jobs.csv"), "w", encoding="utf-8") as fh:
            fh.write("title,score,matched_skills,company\n")
            for i in range(37):
                fh.write(f"thin engineer {i},30,react,co{i}\n")
        rows, source = local_search.market_rows(
            live, artifact(rows=enough()))
        self.assertEqual(source, "frozen")
        self.assertFalse(any("thin engineer" in t for t, _s, _sk, _c in rows),
                         "the thin live corpus leaked into the frozen one")

    def test_the_two_are_never_merged(self):
        """Blending would make a keyword's rank depend on which dataset it
        landed in, and idf() would divide by a total describing neither."""
        live = tempfile.mkdtemp()
        with open(os.path.join(live, "jobs.csv"), "w", encoding="utf-8") as fh:
            fh.write("title,score,matched_skills,company\n")
            for i in range(local_search.MIN_CORPUS_ROWS):
                fh.write(f"live engineer {i},30,react,co{i}\n")
        rows, _source = local_search.market_rows(live, artifact(rows=enough()))
        self.assertEqual(len(rows), local_search.MIN_CORPUS_ROWS,
                         "the totals were added together")

    def test_zero_rows_either_side_is_none(self):
        rows, source = local_search.market_rows(
            tempfile.mkdtemp(), os.path.join(tempfile.mkdtemp(), "nope.gz"))
        self.assertEqual((rows, source), ([], "none"))


class TestABadArtifactIsNoArtifact(unittest.TestCase):
    """Never raises. A file truncated, hand-edited or left out of a build
    degrades to the behaviour that existed before it did."""

    def _none(self, path):
        self.assertEqual(local_search.frozen_rows(path), [])
        self.assertEqual(
            local_search.market_rows(tempfile.mkdtemp(), path)[1], "none")

    def test_4_missing(self):
        self._none(os.path.join(tempfile.mkdtemp(), "not-here.json.gz"))

    def test_5_truncated(self):
        good = artifact(rows=enough())
        cut = good + ".cut"
        with open(good, "rb") as src, open(cut, "wb") as dst:
            dst.write(src.read()[:120])
        self._none(cut)

    def test_6_not_json(self):
        handle, path = tempfile.mkstemp(suffix=".json.gz")
        os.close(handle)
        with gzip.GzipFile(path, "wb", mtime=0) as fh:
            fh.write(b"this is not json at all")
        self._none(path)

    def test_6b_not_even_gzip(self):
        handle, path = tempfile.mkstemp(suffix=".json.gz")
        os.close(handle)
        with open(path, "wb") as fh:
            fh.write(b'{"schema_version": 1, "rows": []}')
        self._none(path)

    def test_7_a_schema_this_reader_does_not_understand(self):
        """Refused rather than read hopefully: a loader that accepted a
        format it did not understand is how a corpus becomes WRONG rather
        than absent, and nothing downstream could tell."""
        self._none(artifact(rows=enough(),
                            schema_version=local_search.FROZEN_SCHEMA + 1))
        self._none(artifact(rows=enough(), schema_version=None))

    def test_8_one_malformed_row_rejects_the_whole_artifact(self):
        """Deliberately the OPPOSITE of frozen_frequencies' per-entry rule.
        A frequency table is a bag of independent terms, so one bad line
        costs one term. This is a market, and idf() divides by len(rows) —
        quietly dropping rows would not give a smaller corpus, it would
        give a differently-weighted one."""
        for broken in ([GOOD_ROW, ["title only"]],
                       [GOOD_ROW, ["t", "not an int", ["react"], "co"]],
                       [GOOD_ROW, ["t", 1, "react", "co"]],
                       [GOOD_ROW, ["t", 1, [7], "co"]],
                       [GOOD_ROW, ["", 1, ["react"], "co"]],
                       [GOOD_ROW, ["t", True, ["react"], "co"]],
                       [GOOD_ROW, None]):
            with self.subTest(row=str(broken[1])[:40]):
                self._none(artifact(rows=enough() + broken))

    def test_rows_missing_entirely(self):
        handle, path = tempfile.mkstemp(suffix=".json.gz")
        os.close(handle)
        with gzip.GzipFile(path, "wb", mtime=0) as fh:
            fh.write(json.dumps({"schema_version": 1}).encode())
        self._none(path)


class TestTheShippedArtifact(unittest.TestCase):
    """The file actually committed to this repo."""

    def setUp(self):
        self.rows = local_search.frozen_rows()
        if not self.rows:
            self.skipTest("data/title_corpus.json.gz is not built")

    def test_12_the_path_does_not_depend_on_the_working_directory(self):
        """The beta runs under gunicorn from whatever cwd Render chose. A
        relative path would read as "no corpus" there and as the real one
        on a laptop — the production/local split this exists to close."""
        was = os.getcwd()
        try:
            os.chdir(tempfile.mkdtemp())
            self.assertEqual(len(local_search.frozen_rows()), len(self.rows))
        finally:
            os.chdir(was)

    def test_it_is_usable(self):
        self.assertTrue(local_search.usable(self.rows))
        self.assertGreaterEqual(len(self.rows), local_search.MIN_CORPUS_ROWS)

    def test_every_row_is_the_shape_market_expects(self):
        for title, score, skills, company in self.rows[:500]:
            self.assertIsInstance(title, str)
            self.assertIsInstance(score, int)
            self.assertIsInstance(skills, frozenset)
            self.assertIsInstance(company, str)

    def test_it_carries_only_job_listing_data(self):
        """Checked here as well as before committing: this file goes into a
        public repo, and the check that matters is the one that runs."""
        import re
        email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
        secret = re.compile(r"(apify_api_|sk-|Bearer |ghp_|AKIA|BEGIN )", re.I)
        pathy = re.compile(r"(/Users/|/home/|/var/folders/|\.pdf\b|/opt/)")
        for title, _score, skills, company in self.rows:
            for text in (title, company, *skills):
                self.assertIsNone(email.search(text), text)
                self.assertIsNone(secret.search(text), text)
                self.assertIsNone(pathy.search(text), text)


class TestTheResumeThatFailedInProduction(unittest.TestCase):
    """The regression. Semantic, not a snapshot: the contract is that a
    graduate gets SOMETHING to search for, not that they get these eight
    strings in this order."""

    def _keywords(self, market):
        return local_search.fields_for(INTERNSHIP_ONLY, market)["role_keywords"]

    def test_10_the_fixture_is_meaningful_because_it_fails_without_a_corpus(self):
        """If this ever passes, the regression below is proving nothing."""
        empty = local_search.Market(rows=[])
        self.assertEqual(local_search.from_resume(
            INTERNSHIP_ONLY, empty.seniority), [],
            "countable() stopped filtering internships — the premise is gone")
        self.assertEqual(self._keywords(empty), [])

    def test_9_with_the_frozen_corpus_it_produces_keywords(self):
        rows = local_search.frozen_rows()
        if not rows:
            self.skipTest("data/title_corpus.json.gz is not built")
        keywords = self._keywords(local_search.Market(rows=rows))
        self.assertTrue(keywords, "the résumé still derives nothing")
        # Something from the family this person actually works in, rather
        # than eight exact strings whose order is not a stable contract.
        family = ("full stack", "frontend", "front end", "web developer",
                  "react", "node", "software", "developer", "engineer")
        self.assertTrue(
            any(any(word in k for word in family) for k in keywords),
            f"nothing recognisable to this candidate: {keywords}")

    def test_a_render_shaped_app_reaches_the_frozen_corpus(self):
        """End to end through the seam Market actually uses: an empty
        output_dir is what Render has."""
        if not local_search.frozen_rows():
            self.skipTest("data/title_corpus.json.gz is not built")
        market = local_search.Market(output_dir=tempfile.mkdtemp())
        self.assertEqual(market.source, "frozen")
        self.assertTrue(self._keywords(market))


class TestNothingElseMoved(unittest.TestCase):

    def test_13_an_explicit_rows_argument_still_wins(self):
        """Several tests build Market(rows=[]) to mean "an empty market"
        and must keep meaning it — the fallback is for "read the corpus for
        me", never for rows a caller handed in."""
        self.assertEqual(local_search.Market(rows=[]).rows, [])
        self.assertEqual(local_search.Market(rows=[]).source, "given")
        one = [("full stack engineer", 40, frozenset({"react"}), "acme")]
        self.assertEqual(local_search.Market(rows=one).rows, one)

    def test_13b_a_live_corpus_is_still_read_exactly_as_before(self):
        live = local_search.corpus_rows(os.path.join(REPO_ROOT, "output"))
        if not local_search.usable(live):
            self.skipTest("no live corpus on this machine")
        market = local_search.Market(
            output_dir=os.path.join(REPO_ROOT, "output"))
        self.assertEqual(market.source, "live")
        self.assertEqual(len(market.rows), len(live))

    def test_the_orphan_pass_floor_is_untouched(self):
        """MIN_ROWS is 20 and belongs to the orphan pass. The corpus gate
        is 200 and is a different question — naming both MIN_ROWS made the
        later binding win and silently moved this one."""
        self.assertEqual(local_search.MIN_ROWS, 20)
        self.assertEqual(local_search.SHARE_PRIOR, 20)
        self.assertEqual(local_search.MIN_CORPUS_ROWS, 200)

    def test_the_frozen_market_is_built_once(self):
        if not local_search.frozen_rows():
            self.skipTest("data/title_corpus.json.gz is not built")
        self.assertIs(local_search.frozen_market(),
                      local_search.frozen_market())


class TestTheHealthCheckSaysWhichCorpusAnswered(unittest.TestCase):

    def _healthz(self, output_dir):
        from unittest import mock

        from sweep import app as app_module
        from sweep import public
        env = {public.PUBLIC_ENV: "1", public.SECRET_ENV: "s",
               public.CODE_ENV: "c"}
        with mock.patch.dict(os.environ, env, clear=False):
            app = app_module.create_app(derive=lambda t, p: {},
                                        extract=lambda p: "x",
                                        output_dir=output_dir)
        return app.test_client().get("/healthz").get_json()

    def test_11_it_reports_frozen_when_there_is_no_live_corpus(self):
        if not local_search.frozen_rows():
            self.skipTest("data/title_corpus.json.gz is not built")
        body = self._healthz(tempfile.mkdtemp())
        self.assertEqual(body["title_corpus_source"], "frozen")
        self.assertEqual(body["title_corpus_rows"],
                         len(local_search.frozen_rows()))

    def test_11b_the_existing_diagnostic_is_untouched(self):
        body = self._healthz(tempfile.mkdtemp())
        self.assertIn("market_signal_source", body)
        self.assertEqual(body["status"], "ok")

    def test_the_count_is_there_to_catch_a_shipped_empty_corpus(self):
        """"frozen" alone cannot tell a corpus that shipped from one that
        did not."""
        body = self._healthz(tempfile.mkdtemp())
        self.assertIsInstance(body["title_corpus_rows"], int)


if __name__ == "__main__":
    unittest.main()
