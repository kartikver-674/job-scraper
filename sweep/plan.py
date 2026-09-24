"""Turn a planned sweep into a costed one.

The plan itself always comes from `scraper.py --dry-run --json` rather than
being re-derived here. Two planners would eventually disagree about what a
sweep costs, and the cost is the one thing this UI exists to be honest about.
"""

import json
import os
import subprocess
import sys
from decimal import Decimal

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fetch(profile, runner=None):
    """Return the raw plan dict for a profile. Costs nothing — no actors run."""
    argv = [sys.executable, "scraper.py", "--profile", profile, "--dry-run", "--json"]
    run = runner or (lambda a: subprocess.run(
        a, cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout)
    return json.loads(run(argv))


# A pay-per-event actor bills per result, so a per-search rate means nothing
# without the depth it was measured at — and config.SITE_RATES' three rates were
# measured at three different depths, which config.SITE_RATE_BASIS records
# beside them. Pricing all three from one basis over-charges naukri 2x
# unconditionally (its $0.50 is a per-run MINIMUM at its own fixed depth of 50,
# which the depth control cannot move and a floor does not halve) and
# under-states indeed by 40% at the default depth of 15.
DEFAULT_RATE_BASIS = 25


def cost(raw, rates, basis=None):
    """Add per-site subtotals and a total. A site with no rate is free.

    Each rate is scaled from the depth it was MEASURED at to the depth this
    plan will actually run at, because max_results multiplies real spend: it
    reaches the actors as maxItemsPerSearch / count / maxJobs. `rate` stays the
    EFFECTIVE per-search rate, so rate x searches == subtotal still holds.

    `basis` is per site (config.SITE_RATE_BASIS). A site whose basis equals the
    depth it always runs at is therefore unscaled, which is what naukri needs:
    a per-run minimum is not a per-result price.

    V2-C4 keeps a second figure beside the estimate, never mixed into it:
    AUTHORIZATION EXPOSURE, the provider-enforced ceiling every search carries
    (the engine's own `charge_ceiling_usd`, per site, from the same dry run).
    `bounded_exposure` is what those ceilings sum to — the most the bounded
    searches can be charged, not what they are expected to cost. A site with
    no ceiling is counted in `unbounded_searches` and priced only by its
    estimate, `unbounded_estimate`, which is not a maximum of anything. A plan
    from an engine that predates the field has no known ceiling at all.
    """
    basis = basis or {}
    depth = raw.get("max_results") or {}
    ceilings = raw.get("charge_ceiling_usd") or {}
    lines = []
    for site, searches in raw["sites"].items():
        site_basis = basis.get(site) or DEFAULT_RATE_BASIS
        # An absent depth means "priced at its own basis" — never 0, which
        # would reprice a paid site to free.
        results = depth.get(site) or site_basis
        rate = rates.get(site, 0.0) * results / site_basis
        ceiling = ceilings.get(site)
        lines.append({
            "site": site,
            "searches": len(searches),
            "results": results,
            "rate": rate,
            "subtotal": round(len(searches) * rate, 4),
            "free": rate == 0.0,
            # Per search, as a decimal string; None: no provider ceiling.
            "ceiling": ceiling,
        })
    bounded = [l for l in lines if l["ceiling"] is not None]
    unbounded = [l for l in lines if l["ceiling"] is None and not l["free"]]
    return {
        "profile": raw["profile"],
        "lines": lines,
        "total": round(sum(l["subtotal"] for l in lines), 4),
        "total_searches": sum(l["searches"] for l in lines),
        "free_sources": raw.get("free_sources", 0),
        "bounded_exposure": str(sum((Decimal(l["ceiling"]) * l["searches"]
                                     for l in bounded), Decimal(0))),
        "unbounded_estimate": round(sum(l["subtotal"] for l in unbounded), 4),
        "unbounded_searches": sum(l["searches"] for l in unbounded),
    }


def units(raw):
    """[(unit_id, site, ceiling)] for every paid search in `raw`, in the
    engine's plan order and under its ids (scraper.paid_unit_id): ceiling a
    Decimal, or None where the provider enforces none."""
    ceilings = raw.get("charge_ceiling_usd") or {}
    out = []
    for site, searches in raw["sites"].items():
        ceiling = ceilings.get(site)
        for _ in searches:
            out.append((f"paid_{len(out):03d}", site,
                        None if ceiling is None else Decimal(ceiling)))
    return out


def prefix(raw, n):
    """`raw` with only its first n paid searches, in plan order — what a
    partial sweep of n searches runs, for cost() to price exactly."""
    sites, left = {}, n
    for site, searches in raw["sites"].items():
        if left <= 0:
            break
        sites[site] = list(searches[:left])
        left -= len(sites[site])
    return dict(raw, sites=sites)


def coverage(raw, capacities, budget=None):
    """V2-D1: how much of this plan the connected accounts can safely hold,
    by the engine's own allocator — never by adding balances up, because two
    accounts with $0.10 each hold no $0.135 search. `capacities`: each
    account's real usable capacity (scraper.usable_capacity), a Decimal.

    Advisory: the engine re-reads every account just before the first start
    and decides again on what it finds. Returns the counts, the partial
    prefix's estimate, and the capacity figures a screen shows."""
    import scraper
    plan = units(raw)
    placed = scraper.place_units(
        [(u, c) for u, _s, c in plan if c is not None],
        [(f"account_{i:03d}", c) for i, c in enumerate(capacities)])
    k = scraper.placeable_prefix([(u, c) for u, _s, c in plan], placed, budget)
    return {"total_units": len(plan), "placeable_units": k,
            "full": k == len(plan),
            "accounts": len(capacities),
            "available_usd": float(sum(capacities, Decimal(0))),
            "bounded_exposure_usd": float(sum((c for _u, _s, c in plan
                                               if c is not None), Decimal(0))),
            "prefix": prefix(raw, k)}
