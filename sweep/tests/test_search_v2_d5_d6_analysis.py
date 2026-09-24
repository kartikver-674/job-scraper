"""bench/search_v2_d5_d6_analysis.py: its pure pieces on synthetic fixtures, the
privacy of the three evidence files it wrote, and that it cannot reach a network.

    .venv/bin/python -m unittest sweep.tests.test_search_v2_d5_d6_analysis -v
"""
import ast
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from bench import search_v2_d5_d6_analysis as d

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVIDENCE = os.path.join(REPO, "docs", "search-v2-evidence")
OUTPUTS = ("d5-free-path-analysis.json", "d5-shadow-boards.json", "d6-identity-audit.json")
C5_FINAL = os.path.join(REPO, "output", "c5-run", "jobs_2026-09-24_2141.json")
UUID_A = "0f1e2d3c-4b5a-4968-8776-655443322110"


def key(row):
    """Stub job_key: company + title, like the engine's 'ct' branch."""
    return ("ct", row.get("company", ""), row.get("title", ""))


def row(source, url, title="t", company="c"):
    return {"source_site": source, "apply_url": url, "title": title, "company": company}


class Scheduling(unittest.TestCase):
    def test_fifo_wall_matches_a_registry_order_pool(self):
        self.assertEqual(d.fifo_wall_ms([3, 1, 1, 1], 1), 6)          # serial = sum
        self.assertEqual(d.fifo_wall_ms([3, 1, 1, 1], 2), 3)
        self.assertEqual(d.fifo_wall_ms([4, 1, 1, 1, 1], 4), 4)        # floor = slowest
        self.assertEqual(d.fifo_wall_ms([1, 1, 1, 9], 4), 9)          # late straggler
        self.assertEqual(d.fifo_wall_ms([], 4), 0)

    def test_busy_counts_gaps_once_and_overlaps_once(self):
        t = datetime(2026, 9, 24, tzinfo=timezone.utc)
        s = lambda a, b: (t + timedelta(seconds=a), t + timedelta(seconds=b))
        self.assertEqual(d.busy_s([s(0, 2), s(5, 6)]), 3.0)         # a feed around wwr
        self.assertEqual(d.busy_s([s(0, 4), s(1, 2), s(3, 6)]), 6.0)

    def test_family_splits_wwr_from_other_feeds(self):
        self.assertEqual(d.family("wwr"), "wwr")
        self.assertEqual(d.family("remoteok"), "other_feeds")
        self.assertEqual(d.family("greenhouse:acme"), "greenhouse")


class NativeIdentity(unittest.TestCase):
    def test_ids_parse_per_namespace_and_malformed_is_flagged(self):
        u = d.url_native_id
        self.assertEqual(u("greenhouse:acme", "https://acme.example.test/c?gh_jid=1234567"),
                         ("greenhouse", "1234567", True))
        self.assertEqual(u("greenhouse:acme", "https://b.example.test/acme/jobs/7654321"),
                         ("greenhouse", "7654321", True))
        self.assertEqual(u("lever:acme", f"https://l.example.test/acme/{UUID_A}"),
                         ("lever", UUID_A, True))
        self.assertEqual(u("lever:acme", "https://l.example.test/acme/not-a-uuid"),
                         ("lever", "not-a-uuid", False))
        self.assertEqual(u("indeed", "https://i.example.test/viewjob?jk=0123456789abcdef"),
                         ("indeed", "0123456789abcdef", True))
        self.assertEqual(u("greenhouse:acme", ""), ("greenhouse", "", False))
        self.assertIsNone(u("remoteok", "https://r.example.test/x-123"))

    def test_drift_merges_only_inside_one_namespace(self):
        gh = "https://b.example.test/acme/jobs/1234567"
        runs = [("r1", [row("greenhouse:acme", gh, title="old"),
                        row("linkedin", "https://li.example.test/jobs/view/1234567"),
                        row("greenhouse:acme", "https://b.example.test/acme/jobs/")]),
                ("r2", [row("greenhouse:acme", gh, title="new")])]
        got = d.identity_across_runs(runs, key)
        self.assertEqual(got["native_ids"], 2)                      # gh and li stay apart
        self.assertEqual(got["raw_id_in_several_namespaces"], 1)
        self.assertEqual(got["ids_in_2plus_runs"], 1)
        self.assertEqual(got["same_id_different_job_key"], 1)
        self.assertEqual(got["drift_kind"], {"title": 1})
        self.assertEqual(got["coverage"]["greenhouse:missing_or_malformed"], 1)
        self.assertEqual(got["same_id_different_job_key_within_one_run"], 0)

    def test_drift_kind(self):
        self.assertEqual(d.drift_kind({("ct", "a", "x"), ("ct", "a", "y")}), "title")
        self.assertEqual(d.drift_kind({("ct", "a", "x"), ("ct", "b", "x")}), "company")
        self.assertEqual(d.drift_kind({("ct", "a", "x"), ("ct", "b", "y")}), "company_and_title")
        self.assertEqual(d.drift_kind({("ct", "a", "x"), None}), "key_kind")

    def test_seen_ledger_recompute(self):
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as fh:
            fh.write("2026-09-01\tct|c|t\tt\tc\n"         # still means what it meant
                     "2026-09-01\tct|c|old\tnew\tc\n"     # would not
                     "2026-09-01\treq|123\tt\tc\n"
                     "a title continued on a torn line\n")
        try:
            got = d.seen_ledgers([fh.name], lambda r: "|".join(key(r)))
        finally:
            os.unlink(fh.name)
        self.assertEqual(got["ct_recomputes_identically"], 1)
        self.assertEqual(got["ct_recomputes_differently"], 1)
        self.assertEqual(got["req_not_recomputable"], 1)
        self.assertEqual(got["malformed_lines"], 1)


class WrittenEvidence(unittest.TestCase):
    def setUp(self):
        self.files = {n: os.path.join(EVIDENCE, n) for n in OUTPUTS}
        missing = [n for n, p in self.files.items() if not os.path.exists(p)]
        if missing:
            self.skipTest(f"not generated yet: {missing}")
        self.text = {}
        for n, p in self.files.items():
            with open(p, encoding="utf-8") as fh:
                self.text[n] = fh.read()

    def test_no_url_email_or_row_text(self):
        for name, text in self.text.items():
            body = json.loads(text)
            # Evidence file NAMES are required provenance; one contains "http".
            stripped = text
            for f in body.get("generated_from") or []:
                stripped = stripped.replace(str(f), "")
            self.assertNotIn("http", stripped, name)
            self.assertIsNone(re.search(r"(?i)https?:|www\.", text), name)
            self.assertNotIn("@", text, name)

    def test_no_c5_title_or_company(self):
        if not os.path.exists(C5_FINAL):
            self.skipTest("output/c5-run absent")
        with open(C5_FINAL, encoding="utf-8") as fh:
            rows = json.load(fh)
        values = {r.get(k) for r in rows for k in ("title", "company")} - {None, ""}
        for name, text in self.text.items():
            leaked = [v for v in values if re.search(
                r"(?<![A-Za-z0-9])" + re.escape(v) + r"(?![A-Za-z0-9])", text)]
            self.assertEqual(leaked, [], name)

    def test_every_file_has_schema_provenance_and_decision(self):
        for name, text in self.text.items():
            body = json.loads(text)
            for field in ("schema", "generated_from", "evidence_labels", "decision"):
                self.assertIn(field, body, name)
        shadow = json.loads(self.text["d5-shadow-boards.json"])
        self.assertEqual(len(shadow["boards"]), 8)
        self.assertLessEqual(shadow["decision"]["natural_observations"], 1)
        self.assertEqual(shadow["decision"]["promote"], [])   # one natural sweep: never enough


class Offline(unittest.TestCase):
    BANNED = {"socket", "requests", "httpx", "aiohttp", "urllib3", "urllib.request",
              "http", "http.client", "apify_client", "sources"}

    def test_no_network_import(self):
        with open(d.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        self.assertFalse({n for n in names
                          if n in self.BANNED or n.split(".")[0] in self.BANNED
                          and n != "urllib.parse"}, names)
        top = {a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names}
        self.assertNotIn("scraper", top)       # the engine only lazily, for job_key


if __name__ == "__main__":
    unittest.main()
