"""Search Engine V2-A non-regression: telemetry and the shadow tranche are inert.

Two flags were added. Both default OFF, and the only claim worth testing is that
flipping them changes nothing a user sees. These run with the rest of the suite
(`python -m unittest discover -s sweep/tests -t .`) so the claim is re-checked on
every change, not just on the day it was written.

No network: every test here either runs the offline pipeline or stubs the fetch.
"""
import os
import tempfile
import unittest
from datetime import datetime

import scraper
import telemetry
from sources import ats, feeds, shadow


def rows():
    """Remote, fresh, several rows tied on score, two sharing a dedupe key.

    Ties matter: the sort is stable, so a tie is broken by ARRIVAL ORDER. A
    sample without ties cannot detect an observer that reorders a list.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    def row(title, company, source, url, desc, location="Remote"):
        return {"Title": title, "Company": company, "Location": location,
                "Description": desc, "Posted Date": today, "Source": source,
                "Job URL": url, "Salary": "", "Experience": ""}

    return [
        row("Backend Engineer", "Beta Labs", "lever:beta", "https://x/2",
            "python django"),
        row("Engineer, Backend", "Beta", "remoteok", "https://x/3",
            "python django", "Worldwide"),
        row("Full Stack Developer", "Gamma", "greenhouse:gamma", "https://x/4",
            "react node postgres"),
        row("Software Engineer", "Delta", "ashby:delta", "https://x/5",
            "react node postgres"),
        row("Frontend Engineer", "Epsilon", "jobicy", "https://x/6",
            "react typescript", "Anywhere in the World"),
        row("React Native Developer", "Zeta", "himalayas", "https://x/7",
            "react native typescript"),
    ]


class FlagDefaults(unittest.TestCase):
    """Both flags are off unless a deployment says otherwise."""

    def test_telemetry_flag_name_and_default(self):
        self.assertEqual(telemetry.FLAG, "SWEEP_SEARCH_V2_TELEMETRY")
        with _unset(telemetry.FLAG):
            self.assertFalse(telemetry.enabled())

    def test_shadow_flag_name_and_default(self):
        self.assertEqual(shadow.FLAG, "SWEEP_FREE_SOURCE_SHADOW")
        with _unset(shadow.FLAG):
            self.assertFalse(shadow.enabled())

    def test_no_deployment_file_switches_either_flag_on(self):
        """A flag that ships on is not a flag that defaults off.

        Looks for an ASSIGNMENT, not a mention: sweep_worker.py names the
        telemetry flag in a comment explaining why it exports SWEEP_RUN_ID, and
        a test that cannot tell prose from configuration is a test that has to
        be edited every time someone writes a sentence.
        """
        import re
        for path in ("render.yaml", "deploy/sweep_worker.py", "gunicorn.conf.py"):
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            for flag in (telemetry.FLAG, shadow.FLAG):
                setters = re.findall(
                    r"""(?:\benv\[["']%(f)s["']\]\s*=|"""          # env["FLAG"] =
                    r"""\bos\.environ\[["']%(f)s["']\]\s*=|"""    # os.environ[...] =
                    r"""^\s*%(f)s\s*=|"""                          # FLAG= (shell/env file)
                    r"""\bkey:\s*%(f)s\b|"""                      # render.yaml key:
                    r"""\bsetdefault\(["']%(f)s["'])"""            # setdefault(...)
                    % {"f": re.escape(flag)}, body, re.M)
                self.assertEqual(setters, [], f"{path} sets {flag}")

    def test_profile_engine_version_untouched(self):
        """V2-A must not move the profile engine. Named explicitly because it is
        the one switch a search change must never reach for."""
        import skill_concepts
        self.assertEqual(skill_concepts.VERSION_ENV,
                         "SWEEP_PROFILE_ENGINE_VERSION")
        self.assertEqual(skill_concepts.DEFAULT_VERSION, "v1")

    def test_experience_mismatch_guard_still_off(self):
        import experience_guard
        with _unset(experience_guard.FLAG):
            self.assertFalse(experience_guard.enabled())


class TelemetryIsPassive(unittest.TestCase):
    """Flag on and flag off must produce the same list, in the same order."""

    def test_finalize_identical_with_flag_on(self):
        sample = rows()
        with _unset(telemetry.FLAG):
            off = scraper.finalize([dict(r) for r in sample])
            off_stats = dict(scraper.LAST_STATS)
        # A sample that collapses to one row would pass whatever telemetry did.
        self.assertGreaterEqual(len(off), 4, off)
        self.assertLess(len({r["score"] for r in off}), len(off),
                        "no score ties, so reordering would be undetectable")

        with tempfile.TemporaryDirectory() as tmp, _set(telemetry.FLAG, "1"):
            telemetry.start("free", tmp)
            on = scraper.finalize([dict(r) for r in sample])
            record = telemetry.record()
            stages = record["stages"]
            telemetry.finish()

        self.assertEqual(on, off, "telemetry changed the result set")
        self.assertEqual(dict(scraper.LAST_STATS), off_stats,
                         "telemetry changed the reported filter stats")
        # Counts must agree with the rows, or the record is decorative.
        self.assertEqual(stages["final_after_dedupe"]["total"], len(on))
        self.assertEqual(stages["observed_normalized"]["total"], len(sample))

    def test_identity_order_preserved(self):
        """Not just the same set — the same sequence."""
        sample = rows()
        with _unset(telemetry.FLAG):
            off = scraper.finalize([dict(r) for r in sample])
        with tempfile.TemporaryDirectory() as tmp, _set(telemetry.FLAG, "1"):
            telemetry.start("free", tmp)
            on = scraper.finalize([dict(r) for r in sample])
            telemetry.finish()
        self.assertEqual([r["apply_url"] for r in on],
                         [r["apply_url"] for r in off])
        self.assertEqual([r["score"] for r in on], [r["score"] for r in off])

    def test_native_diagnostic_cannot_reach_output(self):
        """The whole safety case for capturing native IDs at all."""
        out = scraper.to_output({"_native": {"native_id": "1"}})
        self.assertNotIn("_native", out)
        self.assertEqual(set(out), set(scraper.OUTPUT_COLUMNS))

    def test_native_only_captured_under_the_flag(self):
        item = {"title": "Engineer", "absolute_url": "https://x/1", "id": 7,
                "location": {"name": "Remote"}, "updated_at": "2026-09-01",
                "content": "react"}
        spec = ats.ATS["greenhouse"]
        with _unset(telemetry.FLAG):
            self.assertNotIn("_native",
                             ats._row(item, "greenhouse", "acme", "Acme", spec))
        with tempfile.TemporaryDirectory() as tmp, _set(telemetry.FLAG, "1"):
            telemetry.start("free", tmp)
            row = ats._row(item, "greenhouse", "acme", "Acme", spec)
            telemetry.finish()
        self.assertEqual(row["_native"]["native_id"], "7")

    def test_record_holds_no_description_or_credential(self):
        """Privacy is a property of the record, so assert it on a real record."""
        sample = rows()
        for row in sample:
            row["Description"] += " SENSITIVE-JD-TEXT"
        with tempfile.TemporaryDirectory() as tmp, _set(telemetry.FLAG, "1"):
            telemetry.start("free", tmp)
            scraper.finalize([dict(r) for r in sample])
            with telemetry.unit("paid", "linkedin", query="Engineer"):
                telemetry.paid_run(actor_id="a/b", token="SECRET-TOKEN")
            telemetry.identity(sample, scraper.job_key)
            path = telemetry.finish()
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        self.assertNotIn("SENSITIVE-JD-TEXT", body)
        self.assertNotIn("SECRET-TOKEN", body)
        self.assertNotIn("APIFY", body)


class ShadowIsIsolated(unittest.TestCase):

    def test_eight_expected_boards_and_no_pubmatic(self):
        tokens = set(shadow.BOARDS["greenhouse"])
        self.assertEqual(tokens, {"fivetran", "abnormalsecurity", "apolloio",
                                  "brex", "vercel", "jumio", "catawiki",
                                  "zetaglobal"})
        # The audit's ninth board had a 403 on its sampled job page.
        self.assertNotIn("pubmatic", tokens)

    def test_shadow_boards_are_not_in_the_production_registry(self):
        """The registry is what a user's sweep fetches. These must not be in it."""
        import config
        live = config.ATS_BOARDS.get("greenhouse", {})
        for token in shadow.BOARDS["greenhouse"]:
            self.assertNotIn(token, live, f"{token} leaked into ATS_BOARDS")

    def test_registry_size_unchanged(self):
        """134 active records: 129 ATS boards + 5 enabled feeds."""
        import config
        boards = sum(len(b) for b in config.ATS_BOARDS.values())
        enabled_feeds = sum(1 for c in config.FEEDS.values() if c.get("enabled"))
        self.assertEqual(boards, 129)
        self.assertEqual(enabled_feeds, 5)
        self.assertFalse(config.OPTUM.get("enabled"))
        self.assertFalse(config.ENTERPRISE.get("enabled"))

    def test_run_is_a_noop_when_disabled(self):
        calls = []
        real = ats.fetch
        ats.fetch = lambda *a, **kw: calls.append(a) or []
        try:
            with _unset(shadow.FLAG):
                self.assertIsNone(shadow.run(lambda t: True, lambda l: True))
        finally:
            ats.fetch = real
        self.assertEqual(calls, [], "shadow fetched with the flag off")

    def test_run_returns_none_even_when_boards_yield_rows(self):
        """The return type IS the safety property: there is no row set to merge.

        Under a telemetry record: since V2-B3, shadow with nowhere to record
        fetches nothing, so without one this would pass without ever fetching.
        """
        calls = []
        real = ats.fetch
        ats.fetch = lambda *a, **kw: calls.append(a) or [
            {"Title": "Engineer", "Source": "x"}]
        try:
            with tempfile.TemporaryDirectory() as tmp, \
                    _set(telemetry.FLAG, "1"), _set(shadow.FLAG, "1"):
                telemetry.start("free", tmp)
                self.assertIsNone(
                    shadow.run(lambda t: True, lambda l: True, log=lambda *a: None))
                telemetry.finish()
        finally:
            ats.fetch = real
        self.assertEqual(len(calls), 8, "the boards were never fetched")

    def test_a_failing_shadow_board_cannot_fail_the_sweep(self):
        calls = []
        real = ats.fetch

        def boom(*a, **kw):
            calls.append(a)
            raise RuntimeError("shadow board exploded")
        ats.fetch = boom
        try:
            with tempfile.TemporaryDirectory() as tmp, \
                    _set(telemetry.FLAG, "1"), _set(shadow.FLAG, "1"):
                telemetry.start("free", tmp)
                self.assertIsNone(
                    shadow.run(lambda t: True, lambda l: True, log=lambda *a: None))
                telemetry.finish()
        finally:
            ats.fetch = real
        self.assertEqual(len(calls), 8, "a failure stopped the other boards")

    def test_shadow_units_do_not_inflate_sweep_source_counts(self):
        real = ats.fetch
        ats.fetch = lambda *a, **kw: [{"Title": "Engineer", "Source": "x"}]
        try:
            with tempfile.TemporaryDirectory() as tmp, \
                    _set(telemetry.FLAG, "1"), _set(shadow.FLAG, "1"):
                telemetry.start("free", tmp)
                shadow.run(lambda t: True, lambda l: True, log=lambda *a: None)
                record = telemetry.record()
                attempted = record["sources_attempted"]
                shadow_units = len(record["shadow_units"])
                telemetry.finish()
        finally:
            ats.fetch = real
        self.assertEqual(attempted, 0, "shadow counted as a sweep source")
        self.assertEqual(shadow_units, 8)


class WwrFeedRoutes(unittest.TestCase):
    """The concrete WWR defect the audit measured, in both its halves."""

    RSS = ("""<?xml version="1.0"?><rss><channel>"""
           """<item><title>Acme: Senior Backend Engineer</title>"""
           """<region>Anywhere in the World</region>"""
           """<pubDate>Mon, 21 Sep 2026 10:00:00 +0000</pubDate>"""
           """<guid>wwr-1</guid>"""
           """<link>https://weworkremotely.com/remote-jobs/acme-backend</link>"""
           """<description>Python</description></item>"""
           """</channel></rss>""")

    def setUp(self):
        import xml.etree.ElementTree as ET
        self.calls = []
        self.real = feeds.get_xml

        def fake(url, **kw):
            self.calls.append(url)
            if url == feeds.WWR_WHOLE_BOARD:
                raise OSError("simulated redirect failure")
            return ET.fromstring(self.RSS)
        feeds.get_xml = fake
        self.addCleanup(lambda: setattr(feeds, "get_xml", self.real))

    def test_whole_board_is_not_requested_as_a_category(self):
        self.assertNotIn("/categories/", feeds.WWR_WHOLE_BOARD)
        self.assertEqual(feeds._wwr_url("remote-jobs"), feeds.WWR_WHOLE_BOARD)
        self.assertEqual(
            feeds._wwr_url("remote-programming-jobs"),
            "https://weworkremotely.com/categories/remote-programming-jobs.rss")

    def test_every_configured_category_resolves(self):
        import config
        for category in config.FEEDS["wwr"]["categories"]:
            url = feeds._wwr_url(category)
            self.assertTrue(url.startswith("https://weworkremotely.com/"), url)
            self.assertTrue(url.endswith(".rss"), url)

    def test_one_failing_feed_does_not_erase_the_successful_ones(self):
        got = feeds.wwr({"categories": ["remote-programming-jobs",
                                        "remote-design-jobs",
                                        "remote-jobs"]},
                        lambda t: True, lambda l: True)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(got), 2, "a failed feed discarded earlier rows")

    def test_total_failure_still_reports_as_a_failure(self):
        with self.assertRaises(RuntimeError) as caught:
            feeds.wwr({"categories": ["remote-jobs"]},
                      lambda t: True, lambda l: True)
        self.assertIn("every configured feed failed", str(caught.exception))

    def test_filtered_to_zero_is_not_a_failure(self):
        """All routes answered; the predicates kept nothing. Empty, not raising."""
        self.assertEqual(
            feeds.wwr({"categories": ["remote-design-jobs"]},
                      lambda t: False, lambda l: True), [])

    def test_successful_normalization_unchanged(self):
        row = feeds.wwr({"categories": ["remote-programming-jobs"]},
                        lambda t: True, lambda l: True)[0]
        self.assertEqual(row["Company"], "Acme")
        self.assertEqual(row["Title"], "Senior Backend Engineer")
        self.assertEqual(row["Location"], "Anywhere in the World")
        self.assertEqual(row["Posted Date"], "2026-09-21")
        self.assertEqual(row["Source"], "wwr")
        self.assertEqual(row["Description"], "Python")
        self.assertNotIn("_native", row)        # flag is off


class NativeIdentityMaps(unittest.TestCase):

    def test_every_ats_platform_declares_native_fields(self):
        self.assertEqual(set(ats.NATIVE), set(ats.ATS))
        for platform, fields in ats.NATIVE.items():
            self.assertIn("native_id", fields, platform)

    def test_native_map_cannot_shadow_a_scored_field(self):
        for name, fields in list(ats.NATIVE.items()) + list(feeds.NATIVE.items()):
            self.assertFalse(set(fields) & set(ats.BLANK), name)

    def test_json_feeds_declare_native_fields(self):
        """wwr is handled inline (RSS elements, not dicts), so it is exempt."""
        from sources import FEED_FETCHERS
        self.assertEqual(set(feeds.NATIVE), set(FEED_FETCHERS) - {"wwr"})


class _ctx:
    def __init__(self, name, value):
        self.name, self.value = name, value

    def __enter__(self):
        self.was = os.environ.get(self.name)
        if self.value is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.value

    def __exit__(self, *exc):
        if self.was is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.was
        return False


def _set(name, value):
    return _ctx(name, value)


def _unset(name):
    return _ctx(name, None)


if __name__ == "__main__":
    unittest.main()
