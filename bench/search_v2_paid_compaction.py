"""V2-C3 offline plan audit: what the current paid plans hold, and what physical
compaction could and could not take out of them.

OFFLINE. No client, no token, no network. It calls the CURRENT planner and
adapters (plan_for_site, effective_search, build_input, max_charge_usd) —
never the engine's main(), never a client.

    .venv/bin/python -m bench.search_v2_paid_compaction \\
        --output docs/search-v2-evidence/c3-paid-plan-audit.json

For the repository defaults, the four synthetic cohorts of paid-plans.json,
the public app's three work scopes and the generic tracked profiles: logical
searches by provider, exact execution duplicates, keyword x location x company
structure, depth, bounded vs unbounded, structural and semantic overlap
CANDIDATES (never removed), the physical starts batching WOULD have made
(hypothetical: batching was prototyped and not merged, see
docs/search-engine-v2-c3-paid-plan-compaction.md §10), and the public spend
cap's admission. Each plan is audited under its own config. Profiles derived
from real people's résumés are not read.
"""
import argparse
import contextlib
import copy
import json
import os
import socket
import subprocess
import sys
import urllib.parse
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
EVIDENCE = ROOT / "docs" / "search-v2-evidence"

# Generic, tracked profiles with a paid plan. The cmp*_<name>, kartik_* and
# sarthak_* profiles are derived from real people's résumés and are not read.
PROFILES = ("bigtech_paid", "bigtech_capgemini", "global_all", "global_remote",
            "india_remote")
SIZES = (1, 2, 3, 4)


def deny_network():
    def deny(*a, **kw):
        raise RuntimeError("offline: network forbidden")
    socket.socket.connect = deny
    socket.create_connection = deny


# ---------------------------------------------------------------------------
# Plans, from the current planner
# ---------------------------------------------------------------------------
def current_plan(scraper):
    """(site, search) in the order the paid phase visits them, with each
    search's billed depth, ceiling and exact actor input."""
    args = SimpleNamespace(site=None, test=False, keywords=None, limit=None)
    units = []
    for site in scraper.resolve_sites(args):
        for search in scraper.plan_for_site(site, args):
            eff = scraper.effective_search(site, search)
            units.append({
                "index": len(units), "site": site, "actor": scraper.SITES[site]["actor"],
                "keywords": search["keywords"], "location": search["location"],
                "company": search.get("company") or "",
                "combo": f"{site}|{search['keywords']}|{search['location']}|"
                         f"{search.get('company') or ''}",
                "depth": eff["max_results"],
                "ceiling": scraper.max_charge_usd(site, eff["max_results"]),
                "input": scraper.build_input(site, eff)})
    return units


@contextlib.contextmanager
def lowered(scraper, config, case):
    """A synthetic or public-scope plan's config: SEARCH and SITES as
    make_profile.render lowers the same preferences (keywords, locations,
    LinkedIn's locations and remote_only, sites enabled). None: as loaded."""
    search, sites = copy.deepcopy(config.SEARCH), copy.deepcopy(config.SITES)
    if case is not None:
        search.update(role_keywords=case["keywords"], locations=case["locations"])
        sites["linkedin"].update(locations=case["linkedin_locations"],
                                 remote_only=case.get("linkedin_remote_only", False))
        for site, on in case.get("sites_enabled", {}).items():
            sites[site]["enabled"] = on
    saved = scraper.SEARCH, scraper.SITES
    scraper.SEARCH, scraper.SITES = search, sites
    try:
        yield
    finally:
        scraper.SEARCH, scraper.SITES = saved


def cases():
    fixtures = json.loads((EVIDENCE / "paid-plans.json").read_text())[
        "synthetic_profile_fixtures"]
    out = [("repository_defaults", None)]
    for f in fixtures:
        out.append((f["case"], {"keywords": f["derived"]["role_keywords"],
                                "locations": f["prefs"]["locations"],
                                "linkedin_locations": f["prefs"]["linkedin_locations"],
                                "sites_enabled": f["prefs"]["sites_enabled"]}))
    from sweep.logic import _SCOPE
    roles = fixtures[0]["derived"]["role_keywords"]
    for scope, keys in _SCOPE.items():
        out.append((f"public_scope_{scope}__software_fullstack", {
            "keywords": roles, "locations": keys["locations"],
            "linkedin_locations": keys["linkedin_locations"],
            "linkedin_remote_only": keys["linkedin_remote_only"]}))
    return out


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------
def hypothetical_starts(units, size):
    """The starts the C3 prototype's formation would make of one site's plan
    at `size` searches per start, nothing done and no budget: contiguous runs
    of provider-bounded LinkedIn searches whose inputs match but for the URL,
    distinct URLs and combos, cut at `size`. Arithmetic for the report — the
    prototype is not merged."""
    if size == 1 or units[0]["site"] != "linkedin" or any(
            u["ceiling"] is None for u in units):
        return [[u["index"]] for u in units]
    batches = []
    for u in units:
        shape = {k: v for k, v in u["input"].items() if k != "urls"}
        last = batches[-1] if batches else None
        if (last and len(last["members"]) < size and last["shape"] == shape
                and u["input"]["urls"][0] not in last["urls"]
                and u["combo"] not in last["combos"]):
            last["members"].append(u["index"])
            last["urls"].add(u["input"]["urls"][0])
            last["combos"].add(u["combo"])
        else:
            batches.append({"members": [u["index"]], "shape": shape,
                            "urls": {u["input"]["urls"][0]}, "combos": {u["combo"]}})
    return [b["members"] for b in batches]


def overlap_candidates(config, units):
    """Categories 2 and 5 — CANDIDATES, never removed. Structural: the same
    keyword and company searched in two places of one market (by the market
    config.INDEED_COUNTRIES assigns each place, INFERRED as overlapping), or a
    LinkedIn place searched with and without f_WT=2 (the remote search is the
    place search narrowed by a keyword the AI search appends). Semantic only:
    two keywords that differ by developer/engineer, or whose words nest."""
    structural, remote_within, semantic = [], [], []
    for a in units:
        for b in units:
            if b["index"] <= a["index"] or a["site"] != b["site"]:
                continue
            same_q = (a["keywords"], a["company"]) == (b["keywords"], b["company"])
            if a["site"] == "linkedin" and same_q:
                qa = urllib.parse.parse_qs(urllib.parse.urlparse(a["input"]["urls"][0]).query)
                qb = urllib.parse.parse_qs(urllib.parse.urlparse(b["input"]["urls"][0]).query)
                if qa.get("geoId") == qb.get("geoId") and ("f_WT" in qa) != ("f_WT" in qb):
                    remote_within.append([a["index"], b["index"]])
                    continue
            ma = config.INDEED_COUNTRIES.get(a["location"])
            mb = config.INDEED_COUNTRIES.get(b["location"])
            if same_q and a["location"] != b["location"] and ma and ma == mb:
                structural.append([a["index"], b["index"]])
            wa = set(a["keywords"].lower().split()) - {"developer", "engineer"}
            wb = set(b["keywords"].lower().split()) - {"developer", "engineer"}
            if (a["location"], a["company"]) == (b["location"], b["company"]) and \
                    a["keywords"] != b["keywords"] and (wa == wb or wa < wb or wb < wa):
                semantic.append([a["index"], b["index"]])
    return {"same_keyword_two_places_one_market_pairs": len(structural),
            "linkedin_place_with_and_without_remote_pairs": len(remote_within),
            "semantic_only_keyword_pairs": len(semantic),
            "examples": {"structural": structural[:3], "remote_within_place": remote_within[:3],
                         "semantic_only": semantic[:3]}}


def spend_cap_admission(units, costed):
    """How many LinkedIn searches the public app's cap lets start under C2's
    full-ceiling reservation, and what the prototype's batching would have
    admitted with budget-aware shrinking and — to show why it existed —
    without it. (Since V2-C4 the cap holds every provider ceiling.)"""
    from sweep.app import spend_cap_for
    cap = Decimal(str(spend_cap_for(costed)))
    estimate = costed["total"]
    li = [u for u in units if u["site"] == "linkedin"]
    if not li or li[0]["ceiling"] is None:
        return None
    c = li[0]["ceiling"]
    rows = {"cap_usd": str(cap), "estimate_usd": estimate, "linkedin_searches": len(li),
            "linkedin_ceiling_usd": str(c),
            "linkedin_admitted_c2": min(len(li), int(cap // c))}
    for size in SIZES[1:]:
        held, shrink, whole = Decimal(0), 0, 0
        for batch in hypothetical_starts(li, size):
            fits = int((cap - held) // c)
            if fits >= len(batch):
                held += len(batch) * c
                shrink += len(batch)
                whole += len(batch)
                continue
            shrink += max(fits, 0)
            break
        rows[f"linkedin_admitted_c3_prototype_size{size}"] = shrink
        rows[f"linkedin_admitted_if_batches_were_all_or_nothing_size{size}"] = whole
    return rows


def audit_plan(config, name, units):
    from sweep.plan import cost
    raw = {"profile": name, "sites": {}, "max_results": {}, "charge_ceiling_usd": {}}
    for u in units:
        raw["sites"].setdefault(u["site"], []).append(u)
        raw["max_results"][u["site"]] = u["depth"]
        raw["charge_ceiling_usd"][u["site"]] = (None if u["ceiling"] is None
                                                else str(u["ceiling"]))
    costed = cost(raw, config.SITE_RATES, config.SITE_RATE_BASIS)
    estimate = costed["total"]
    out = {"plan": name, "logical_searches": len(units), "current_physical_starts": len(units),
           "by_provider": {}, "estimate_usd": estimate}
    for site, us in raw["sites"].items():
        inputs = Counter(json.dumps(u["input"], sort_keys=True) for u in us)
        combos = Counter(u["combo"] for u in us)
        ceilings = {u["ceiling"] for u in us}
        out["by_provider"][site] = {
            "actor": us[0]["actor"], "logical_searches": len(us),
            "keywords": len({u["keywords"] for u in us}),
            "locations": len({u["location"] for u in us}),
            "companies": len({u["company"] for u in us}),
            "company_filtered": sum(1 for u in us if u["company"]),
            "depth": dict(Counter(u["depth"] for u in us)),
            "bounded": None not in ceilings,
            "ceiling_usd_each": sorted(str(c) for c in ceilings if c is not None),
            "exact_unique_inputs": len(inputs),
            "exact_execution_duplicates": [
                [u["index"] for u in us if json.dumps(u["input"], sort_keys=True) == k]
                for k, n in inputs.items() if n > 1],
            "repeated_combos": [[u["index"] for u in us if u["combo"] == k]
                                for k, n in combos.items() if n > 1],
            "hypothetical_physical_starts_by_batch_size": {
                size: len(hypothetical_starts(us, size)) for size in SIZES},
            "overlap_candidates": overlap_candidates(config, us),
        }
    out["public_spend_cap"] = spend_cap_admission(units, costed)
    return out


def plan_of_profile(name):
    """Child: a tracked profile's plan, audited under that profile's own
    config (JOB_PROFILE selects it at import)."""
    deny_network()
    import config
    import scraper
    print(json.dumps(audit_plan(config, f"profile_{name}", current_plan(scraper)),
                     default=str))


def observed_overlap():
    """Categories 3 and 4 from the live evidence: acquired repeats of an
    earlier unit and final jobs lost to another unit, per unit, n = 1 each."""
    rows = []
    for name in ("c1-probe-a-india-two-shapes.json", "c1-probe-b-remote-react.json",
                 "c2-live-concurrency-canary.json", "c3-live-batch-canary.json"):
        for u in json.loads((EVIDENCE / name).read_text()).get("units", []):
            f = u.get("funnel") or {}
            if f:
                rows.append({"evidence": name, "unit_id": u["unit_id"],
                             "location_mode": u.get("location_mode"), "raw": f["raw"],
                             "repeat_of_earlier_paid": (f.get("acquired") or {})
                             .get("repeat_of_earlier_paid"),
                             "final": f["final"], "final_marginal": f["final_marginal"],
                             "lost_to_other_paid": f["dedupe_lost"]["other_paid"],
                             "sample": "one run of one sweep"
                             + ("; batched — the actor hid the overlap (docs §7)"
                                if name.startswith("c3-") else "")})
    return rows


def revision():
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return head + ("-dirty" if dirty else "")


def audit(output):
    deny_network()
    import config
    import scraper
    plans = []
    for name, case in cases():
        with lowered(scraper, config, case):
            plans.append(audit_plan(config, name, current_plan(scraper)))
    for name in PROFILES:
        child = subprocess.run(
            [sys.executable, "-m", "bench.search_v2_paid_compaction", "--plan-of", name],
            cwd=ROOT, env=dict(os.environ, JOB_PROFILE=name), capture_output=True,
            text=True, check=True)
        plans.append(json.loads(child.stdout.strip().splitlines()[-1]))
    out = {"status": "MEASURED OFFLINE: the current planner and adapters; no client, "
                     "no network",
           "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "code_revision": revision(),
           "categories": {
               "1_exact_execution_duplicate": "identical provider input — the only "
                                              "category C3 may remove",
               "2_structural_overlap": "different inputs that may return overlapping "
                                       "jobs — candidates only",
               "3_observed_result_overlap": "C1 telemetry repeat_of_earlier_paid",
               "4_final_result_overlap": "a final job one unit lost to another",
               "5_semantic_similarity_only": "queries that sound related, no evidence"},
           "plans": plans, "observed_overlap": observed_overlap()}
    Path(output).write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps({p["plan"]: {s: [v["logical_searches"],
                                      v["hypothetical_physical_starts_by_batch_size"],
                                      len(v["exact_execution_duplicates"])]
                                  for s, v in p["by_provider"].items()} for p in plans},
                     indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan-of")
    ap.add_argument("--output", default=str(EVIDENCE / "c3-paid-plan-audit.json"))
    args = ap.parse_args()
    if args.plan_of:
        return plan_of_profile(args.plan_of)
    os.environ.pop("JOB_PROFILE", None)
    audit(args.output)


if __name__ == "__main__":
    main()
