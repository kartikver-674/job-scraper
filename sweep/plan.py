"""Turn a planned sweep into a costed one.

The plan itself always comes from `scraper.py --dry-run --json` rather than
being re-derived here. Two planners would eventually disagree about what a
sweep costs, and the cost is the one thing this UI exists to be honest about.
"""

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fetch(profile, runner=None):
    """Return the raw plan dict for a profile. Costs nothing — no actors run."""
    argv = [sys.executable, "scraper.py", "--profile", profile, "--dry-run", "--json"]
    run = runner or (lambda a: subprocess.run(
        a, cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout)
    return json.loads(run(argv))


def cost(raw, rates):
    """Add per-site subtotals and a total. A site with no rate is free."""
    lines = []
    for site, searches in raw["sites"].items():
        rate = rates.get(site, 0.0)
        lines.append({
            "site": site,
            "searches": len(searches),
            "rate": rate,
            "subtotal": round(len(searches) * rate, 4),
            "free": rate == 0.0,
        })
    return {
        "profile": raw["profile"],
        "lines": lines,
        "total": round(sum(l["subtotal"] for l in lines), 4),
        "total_searches": sum(l["searches"] for l in lines),
        "free_sources": raw.get("free_sources", 0),
    }
