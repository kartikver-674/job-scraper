"""V2-D3/D4 offline analysis: the pure pieces on tiny fixtures, the committed
evidence's privacy, and that the tool cannot reach the network."""
import ast
import glob
import json
import re
import unittest
from pathlib import Path

from bench.search_v2_d3_d4_analysis import (
    SINK, assignments, min_attempts, overlap, removal_loss, segment_wall,
    simulate_polls)

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = [ROOT / "docs/search-v2-evidence/d3-indeed-location-analysis.json",
            ROOT / "docs/search-v2-evidence/d4-polling-simulation.json"]
RUN = ROOT / "output/c5-run"
NETWORK = {"requests", "urllib", "urllib3", "http", "socket", "httpx", "aiohttp",
           "apify_client", "impit"}


class PureTests(unittest.TestCase):
    def test_overlap(self):
        self.assertEqual(overlap({1, 2, 3}, {2, 3, 4, 5}),
                         {"intersection": 2, "a_to_b": 0.667, "b_to_a": 0.5, "jaccard": 0.4})
        self.assertEqual(overlap(set(), {1})["a_to_b"], None)

    def test_removal_loss(self):
        jobs = [{"rank": 1, "origins": frozenset({"A"}), "survivor": "A", "marginal": True},
                {"rank": 5, "origins": frozenset({"A", "B"}), "survivor": "A",
                 "marginal": False},
                {"rank": 12, "origins": frozenset({"B", "free"}), "survivor": "B",
                 "marginal": False}]
        one = removal_loss(jobs, {"A"})
        self.assertEqual((one["final_lost"], one["final_marginal_lost"], one["best_lost_rank"]),
                         (1, 1, 1))
        self.assertEqual((one["top10_lost"], one["top10_at_risk"]), (1, 1))  # rank 5 moved
        both = removal_loss(jobs, {"A", "B"})
        self.assertEqual((both["final_lost"], both["top10_lost"], both["top20_at_risk"]),
                         (2, 2, 1))                              # a free source keeps 12

    def test_poll_simulation(self):
        polls, at = simulate_polls(7.2, 5, 0.3)              # 5.0 running, 10.3 terminal
        self.assertEqual(polls, 2)
        self.assertAlmostEqual(at - 7.2, 3.4)
        self.assertEqual(simulate_polls(7.2, 2, 0.3), (4, 9.2))
        self.assertEqual(simulate_polls(4.0, 5, 0.3, g_last=18.6), (1, 23.6))  # a stall

    def test_segment_wall(self):
        wall, starts = segment_wall([(10, 1), (2, 1), (2, 1)])
        self.assertEqual((wall, starts), (13, [0, 0, 2]))   # the head holds integration

    def test_min_attempts(self):
        self.assertEqual([min_attempts(s) for s in (4.9, 5.5, 16.0, 18.592)], [1, 2, 2, 3])

    def test_assignments_respect_twins_and_coverage(self):
        info = {"u1": {"mode": "place", "keyword": "a", "plan_index": 1},
                "u2": {"mode": "remote", "keyword": "a", "plan_index": 2},
                "u3": {"mode": "remote", "keyword": "b", "plan_index": 3}}
        jobs = [dict(id="J", unit="u1", marginal=False, free_origin=False, fflag=False,
                     acquired="n"),
                dict(id="K", unit="u2", marginal=True, free_origin=False, fflag=False,
                     acquired="n")]
        drows = [dict(unit="u2", fflag=False), dict(unit="u3", fflag=False)]
        got = list(assignments(jobs, drows, {False}, info))
        # u2 is J's own keyword's Remote twin (no shared key); J needs an origin
        self.assertEqual(got, [({"J": {"u3"}}, {"u2": 1, "u3": 0})])
        self.assertEqual(SINK, "linkedin_won")


class EvidenceTests(unittest.TestCase):
    def test_evidence_is_private(self):
        texts = [p.read_text() for p in EVIDENCE]
        for t in texts:
            self.assertNotIn("http", t.lower())
            self.assertNotIn("@", t)
            self.assertIsNone(re.search(r"account_\d", t))
        tel = sorted(glob.glob(str(RUN / "telemetry" / "sweep_*.json")))
        jobs = sorted(glob.glob(str(RUN / "jobs_*.json")))
        if not (tel and jobs):
            self.skipTest("output/c5-run is not present")
        needles = {u["query"] for u in json.loads(Path(tel[0]).read_text())["units"]
                   if len(u.get("query") or "") >= 4}
        needles |= {r[k] for r in json.loads(Path(jobs[0]).read_text())
                    for k in ("title", "company") if len(r.get(k) or "") >= 4}
        for t in texts:
            self.assertEqual([n for n in needles if n in t], [])

    def test_no_network_import(self):
        for rel in ("bench/search_v2_d3_d4_analysis.py", "bench/search_v2_c5_analysis.py"):
            tree = ast.parse((ROOT / rel).read_text())
            names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                     for a in n.names} | {n.module for n in ast.walk(tree)
                                          if isinstance(n, ast.ImportFrom) and n.module}
            self.assertEqual({m.split(".")[0] for m in names} & NETWORK, set(), rel)


if __name__ == "__main__":
    unittest.main()
