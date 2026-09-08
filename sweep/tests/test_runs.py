import os
import signal
import subprocess
import sys
import tempfile
import unittest

from sweep import runs

TODAY = "2026-09-08"

RAW = {
    "profile": "kanav",
    "sites": {
        "linkedin": [
            {"keywords": "React Native Developer", "location": "India", "company": ""},
            {"keywords": "React Native Developer", "location": "Remote", "company": ""},
        ],
        "indeed": [
            {"keywords": "Full Stack Engineer", "location": "Pune", "company": ""},
        ],
    },
    "free_sources": 0,
}


class TestComboKeys(unittest.TestCase):
    def test_key_format_matches_the_engine_exactly(self):
        keys = runs.combo_keys(RAW, TODAY)
        self.assertEqual(
            keys[0], "2026-09-08|linkedin|React Native Developer|India|")

    def test_one_key_per_planned_search_in_plan_order(self):
        keys = runs.combo_keys(RAW, TODAY)
        self.assertEqual(len(keys), 3)
        self.assertTrue(keys[2].startswith("2026-09-08|indeed|"))

    def test_company_lands_in_the_fifth_field(self):
        raw = {"profile": "x", "sites": {"linkedin": [
            {"keywords": "SDE", "location": "India", "company": "Stripe"}]},
            "free_sources": 0}
        self.assertEqual(
            runs.combo_keys(raw, TODAY)[0], "2026-09-08|linkedin|SDE|India|Stripe")


class TestDoneKeys(unittest.TestCase):
    def _ledger(self, lines):
        fh = tempfile.NamedTemporaryFile("w", suffix=".done", delete=False)
        fh.write("\n".join(lines) + "\n")
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_missing_file_is_no_progress_not_an_error(self):
        self.assertEqual(runs.done_keys("/nonexistent/.done_combos", TODAY), set())

    def test_only_todays_lines_count(self):
        # The engine reloads the ledger filtered to today (scraper.py:1702), so
        # yesterday's work does NOT count as done and WILL be re-billed.
        path = self._ledger([
            "2026-09-07|linkedin|React Native Developer|India|",
            "2026-09-08|linkedin|React Native Developer|Remote|",
        ])
        done = runs.done_keys(path, TODAY)
        self.assertEqual(done, {"2026-09-08|linkedin|React Native Developer|Remote|"})


class TestProgress(unittest.TestCase):
    def test_counts_and_outstanding_split(self):
        planned = runs.combo_keys(RAW, TODAY)
        done = {planned[0]}
        p = runs.progress(planned, done)
        self.assertEqual(p["planned"], 3)
        self.assertEqual(p["done"], 1)
        self.assertEqual(p["outstanding"], 2)

    def test_tiles_carry_site_and_state_in_plan_order(self):
        planned = runs.combo_keys(RAW, TODAY)
        p = runs.progress(planned, {planned[0]})
        self.assertEqual([t["state"] for t in p["tiles"]],
                         ["done", "pending", "pending"])
        self.assertEqual([t["site"] for t in p["tiles"]],
                         ["linkedin", "linkedin", "indeed"])
        self.assertEqual(p["tiles"][1]["label"],
                         "React Native Developer @ Remote")

    def test_a_finished_sweep_has_nothing_outstanding(self):
        planned = runs.combo_keys(RAW, TODAY)
        p = runs.progress(planned, set(planned))
        self.assertEqual(p["done"], 3)
        self.assertEqual(p["outstanding"], 0)

    def test_an_empty_plan_does_not_divide_by_zero(self):
        p = runs.progress([], set())
        self.assertEqual(p["planned"], 0)
        self.assertEqual(p["fraction"], 0.0)


class FakePopen:
    """Captures arguments to start() without spawning a real process."""
    def __init__(self, argv, cwd=None, env=None, stdout=None, stderr=None, text=False):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.stdout = stdout
        self.stderr = stderr
        self.text = text
        self.signals_sent = []

    def poll(self):
        return None

    def send_signal(self, sig):
        self.signals_sent.append(sig)


class FakeExitedProcess:
    """Fake process that already exited."""
    def __init__(self):
        self.signals_sent = []

    def poll(self):
        return 0

    def send_signal(self, sig):
        self.signals_sent.append(sig)


class TestStart(unittest.TestCase):
    def test_start_builds_correct_argv(self):
        fake_popen = FakePopen
        proc = runs.start("myprofile", popen=fake_popen)
        self.assertEqual(proc.argv[0], sys.executable)
        self.assertEqual(proc.argv[1], "scraper.py")
        self.assertEqual(proc.argv[2], "--profile")
        self.assertEqual(proc.argv[3], "myprofile")
        self.assertEqual(proc.argv[4], "--yes")

    def test_start_sets_cwd_to_repo_root(self):
        fake_popen = FakePopen
        proc = runs.start("myprofile", popen=fake_popen)
        self.assertTrue(proc.cwd.endswith("job-scraper"))

    def test_start_silences_stdout_and_inherits_stderr(self):
        fake_popen = FakePopen
        proc = runs.start("myprofile", popen=fake_popen)
        self.assertEqual(proc.stdout, subprocess.DEVNULL)
        # Not PIPE: nothing reads it, and a full buffer would block the child
        # while poll() still reported it as running.
        self.assertIsNone(proc.stderr)
        self.assertTrue(proc.text)


class TestStartRescore(unittest.TestCase):
    """start_rescore is the only function here that spawns a real process
    against a real profile's real output directory, and it had no direct
    coverage at all — argv, cwd, the profile hand-off and the streams were
    each asserted nowhere."""

    def test_start_rescore_builds_correct_argv(self):
        proc = runs.start_rescore("myprofile", 6, popen=FakePopen)
        self.assertEqual(proc.argv[0], sys.executable)
        self.assertEqual(proc.argv[1], "rescore_from_apify.py")
        self.assertEqual(proc.argv[2], "--hours")
        self.assertEqual(proc.argv[3], "6")
        # No --profile flag: config.py resolves it from JOB_PROFILE instead.
        self.assertNotIn("--profile", proc.argv)

    def test_start_rescore_passes_the_profile_through_the_environment(self):
        proc = runs.start_rescore("myprofile", 6, env={"PATH": "/usr/bin"},
                                   popen=FakePopen)
        self.assertEqual(proc.env["JOB_PROFILE"], "myprofile")
        self.assertEqual(proc.env["PATH"], "/usr/bin")

    def test_start_rescore_does_not_mutate_the_environment_it_was_given(self):
        given = {"PATH": "/usr/bin"}
        runs.start_rescore("myprofile", 6, env=given, popen=FakePopen)
        self.assertNotIn("JOB_PROFILE", given)

    def test_start_rescore_sets_cwd_to_repo_root(self):
        proc = runs.start_rescore("myprofile", 6, popen=FakePopen)
        self.assertTrue(proc.cwd.endswith("job-scraper"))

    def test_start_rescore_silences_stdout_and_inherits_stderr(self):
        proc = runs.start_rescore("myprofile", 6, popen=FakePopen)
        self.assertEqual(proc.stdout, subprocess.DEVNULL)
        # Neither PIPE (wedges on a full buffer) nor DEVNULL (discards the
        # "Nothing to re-score" message that explains a no-op re-rank).
        self.assertIsNone(proc.stderr)
        self.assertTrue(proc.text)


class TestStop(unittest.TestCase):
    def test_stop_sends_sigint_when_process_running(self):
        proc = FakePopen([], cwd="/tmp", env={})
        runs.stop(proc)
        self.assertIn(signal.SIGINT, proc.signals_sent)

    def test_stop_does_not_signal_already_exited_process(self):
        proc = FakeExitedProcess()
        runs.stop(proc)
        self.assertEqual(len(proc.signals_sent), 0)

    def test_stop_returns_exit_code(self):
        proc = FakeExitedProcess()
        result = runs.stop(proc)
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
