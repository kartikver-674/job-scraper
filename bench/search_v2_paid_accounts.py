"""V2-C4.5: the account allocator, benchmarked. OFFLINE and paid-unreachable:
no client, no token, no engine run — scraper's pure allocator functions over
invented account capacities and the current default plan's ceilings.

    .venv/bin/python -m bench.search_v2_paid_accounts \\
        --output docs/search-v2-evidence/c45-allocator-benchmark.json

Compared, each placing a plan in plan order:
  most_headroom  greedy: the account with the most left that fits
  best_fit       greedy: the account with the least left that still fits
  exact          scraper.place_units — the longest plan prefix that CAN be
                 placed, proved by exhaustive table (_suffix_tables), filled
                 smallest account first
Each is judged on searches admitted (the plan stops at the first it cannot
place, as the cap does), accounts used, leftover and STRANDED headroom (an
account's leftover below the smallest ceiling still unplaced: money that sat
unused while work it could not hold went unrun).
"""
import argparse
import json
import random
import tempfile
import time
import tracemalloc
from collections import Counter
from decimal import Decimal
from pathlib import Path

import scraper

BUFFER = scraper.ACCOUNT_BUFFER_USD


def c5_plan():
    """[(unit_id, provider, ceiling)] for the repository's default paid plan,
    built the way plan_for_site builds it, priced by max_charge_usd."""
    units, n = [], 0
    for site, cfg in scraper.SITES.items():
        if not cfg.get("enabled"):
            continue
        plan = scraper.build_search_plan(
            cfg.get("keywords", scraper.SEARCH["role_keywords"]),
            cfg.get("locations", scraper.SEARCH["locations"]), cfg.get("companies"))
        for search in plan:
            depth = scraper.effective_search(site, search)["max_results"]
            units.append((scraper.paid_unit_id(n), site, scraper.max_charge_usd(site, depth)))
            n += 1
    return units


def greedy(units, accounts, pick):
    left = dict(accounts)
    out = {}
    for unit_id, ceiling in units:
        fits = [label for label, _ in accounts if left[label] >= ceiling]
        if not fits:
            break
        label = pick(fits, left)
        left[label] -= ceiling
        out[unit_id] = label
    return out


STRATEGIES = {
    "most_headroom": lambda u, a: greedy(u, a, lambda f, l: max(f, key=lambda k: l[k])),
    "best_fit": lambda u, a: greedy(u, a, lambda f, l: min(f, key=lambda k: l[k])),
    "exact": scraper.place_units,
}


def judge(units, accounts, placed):
    """Stranded: leftover below the smallest ceiling still UNPLACED — money
    that was there while work it could not hold went unrun. Zero once the
    whole plan is placed (what is left is then only unused)."""
    ceiling = dict(units)
    smallest = min((c for u, c in units if u not in placed), default=Decimal(0))
    load = Counter()
    for unit_id, label in placed.items():
        load[label] += ceiling[unit_id]
    leftover = {label: cap - load[label] for label, cap in accounts}
    return {"admitted": len(placed), "of": len(units),
            "admitted_usd": str(sum(load.values(), Decimal(0))),
            "accounts_used": len(load),
            "leftover_usd": str(sum(leftover.values(), Decimal(0))),
            "stranded_usd": str(sum((v for v in leftover.values() if v < smallest),
                                    Decimal(0)))}


def capacities(*headrooms):
    """Account capacities as the pool computes them: headroom less the buffer."""
    return [(f"account_{i:03d}", max(Decimal(0), Decimal(str(h)) - BUFFER))
            for i, h in enumerate(headrooms)]


def shapes(plan):
    li = [(u, c) for u, p, c in plan if p == "linkedin"][:1]
    ind = [(u, c) for u, p, c in plan if p == "indeed"][:1]
    full = [(u, c) for u, _, c in plan]
    exposure = sum((c for _, c in full), Decimal(0))

    def mixed(n_li, n_in):
        return [(f"m{i:03d}", li[0][1]) for i in range(n_li)] + \
               [(f"m{n_li + i:03d}", ind[0][1]) for i in range(n_in)]
    return {
        "A_one_large_account": (full, capacities(20)),
        "B_many_equal_5usd": (full, capacities(*[5] * 5)),
        "C_fragmented_c5_preflight": (full, capacities(4.5609, 3.4078, 1.5353, 0.51,
                                                       0.0214)),
        "D_fresh_near_5usd": (full, capacities(4.99, 4.95, 5, 4.90)),
        "E_aggregate_enough_no_account_fits": (ind, capacities(0.11, 0.11, 0.11)),
        "F_exact_fit_boundary": (full, [("account_000", exposure / 2),
                                        ("account_001", exposure / 2)]),
        "F_one_mill_short": (full, [("account_000", exposure / 2),
                                    ("account_001", exposure / 2 - Decimal("0.001"))]),
        "G_mixed_best_fit_strands": (mixed(3, 2), [("account_000", Decimal("0.181")),
                                                   ("account_001", Decimal("0.227"))]),
        "G_mixed_most_headroom_strands": (mixed(2, 1), [("account_000", Decimal("0.092")),
                                                        ("account_001", Decimal("0.135"))]),
        "H_c5_plan_three_fresh_accounts": (full, capacities(5, 5, 5)),
        "H_c5_plan_tight_two_accounts": (full, capacities(5.3, 5.3)),
    }


def stress(plan, n=500, seed=45):
    """Random account pools against the real plan: how often each greedy
    admits less than the exact allocator (which, being exact, never admits
    less than any strategy)."""
    rng = random.Random(seed)
    full = [(u, c) for u, _, c in plan]
    worse = Counter()
    stranded = {k: Decimal(0) for k in STRATEGIES}
    for _ in range(n):
        accounts = capacities(*[round(rng.uniform(0, 5), 4)
                                for _ in range(rng.randint(2, 6))])
        got = {k: f(full, accounts) for k, f in STRATEGIES.items()}
        for k, placed in got.items():
            assert len(placed) <= len(got["exact"]), (k, accounts)
            worse[k] += len(placed) < len(got["exact"])
            stranded[k] += Decimal(judge(full, accounts, placed)["stranded_usd"])
    return {"instances": n, "seed": seed,
            "admitted_fewer_than_exact": {k: worse[k] for k in STRATEGIES},
            "mean_stranded_usd": {k: str((v / n).quantize(Decimal("0.0001")))
                                  for k, v in stranded.items()}}


def overhead(plan):
    """Planning CPU and memory for the plan across 5/10/20 accounts, and the
    per-start cost of the coordinator's pool calls, durable ledger included."""
    full = [(u, c) for u, _, c in plan]
    out = {}
    for n in (5, 10, 20):
        accounts = capacities(*[5] * n)
        reps = 5
        cpu = time.process_time()
        for _ in range(reps):
            scraper.place_units(full, accounts)
        plan_ms = (time.process_time() - cpu) * 1000 / reps
        tracemalloc.start()
        scraper.place_units(full, accounts)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        out[f"{n}_accounts"] = {"planning_cpu_ms": round(plan_ms, 2),
                                "planning_peak_kib": round(peak / 1024, 1)}
    with tempfile.TemporaryDirectory() as d:
        reading = {"plan": "FREE", "headroom_usd": Decimal(5), "used_usd": 0.0,
                   "memory_mb": 16384, "run_slots": 5}
        accounts = [scraper.PoolAccount(f"account_{i:03d}", [f"S{i}"], f"t{i}", reading,
                                        lambda token: None) for i in range(5)]
        pool = scraper.AccountPool(accounts, [], scraper.AccountLedger(
            str(Path(d) / scraper.POOL_RECORD)))
        pool.assignment, pool.report = scraper.project_assignment(plan, accounts)
        exposure = scraper.PaidExposure(Decimal("10.55"))
        entries = [scraper.PaidEntry(i, i + 1, len(plan), u, p, "", {}, "", u, c)
                   for i, (u, p, c) in enumerate(plan)]
        started = time.perf_counter()
        for e in entries:
            assert pool.verdict(e) == "go"
            assert exposure.reserve(e.unit_id, e.ceiling)
            holds = pool.hold(e, exposure)
            holds.commit(e.unit_id)
            holds.started("run")
            holds.ended("SUCCEEDED")
            holds.release(e.unit_id)
        per = (time.perf_counter() - started) * 1000 / len(entries)
    out["per_start_pool_ms_with_ledger_fsync"] = round(per, 3)
    out["ledger_writes_per_start"] = 4
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    plan = c5_plan()
    results = {}
    for name, (units, accounts) in shapes(plan).items():
        results[name] = {"accounts_usd": [str(c) for _, c in accounts],
                         **{k: judge(units, accounts, f(units, accounts))
                            for k, f in STRATEGIES.items()}}
    out = {"status": "MEASURED offline: pure allocator functions, invented capacities, "
                     "the current default plan's ceilings; no client, no token",
           "plan": {"units": len(plan), "by_provider": dict(Counter(p for _, p, _ in plan)),
                    "ceilings_usd": {p: str(c) for _, p, c in plan},
                    "bounded_exposure_usd": str(sum((c for *_, c in plan), Decimal(0)))},
           "buffer_per_account_usd": str(BUFFER),
           "shapes": results, "stress": stress(plan), "overhead": overhead(plan)}
    Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    for name, r in results.items():
        print(f"{name:<38} " + "  ".join(f"{k}={r[k]['admitted']}/{r[k]['of']}"
                                         for k in STRATEGIES))
    print(json.dumps(out["stress"], indent=1))
    print(json.dumps(out["overhead"], indent=1))


if __name__ == "__main__":
    main()
