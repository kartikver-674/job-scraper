"""Offline paid-engine audit. Never starts actors or reads credentials/profiles.

Run: .venv/bin/python bench/search_v2_paid_audit.py
Writes synthetic actor plans and redacted historical aggregate evidence only.
"""
import copy
import json
import os
from pathlib import Path
import re
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "docs/search-v2-evidence"


def deny_network(*args, **kwargs):
    raise RuntimeError("This audit is offline; network access is forbidden")


def main():
    # Process-only isolation: do not inherit named profiles or credentials.
    os.environ.pop("JOB_PROFILE", None)
    for name in list(os.environ):
        if name.startswith("APIFY_TOKEN"):
            del os.environ[name]
    sys.argv = [sys.argv[0]]
    sys.path.insert(0, str(ROOT))
    with patch.object(socket.socket, "connect", deny_network), patch.object(
            socket, "create_connection", deny_network):
        import config
        import scraper
        from sweep.plan import cost

        args = SimpleNamespace(site=None, test=False, keywords=None, limit=None)
        sys.path.insert(0, str(ROOT / "auto-apply"))
        import make_profile
        cases = [
            ("repository_defaults", None, None, False),
            ("software_fullstack", ["Software Engineer", "Full Stack Developer"],
             ["Bengaluru", "Remote"], False),
            ("react_native", ["React Native Developer", "React Native Engineer"],
             ["Bengaluru", "Remote"], False),
            ("business_salesforce", ["Business Analyst", "Salesforce Functional Consultant",
              "Salesforce Administrator"], ["Delhi", "Remote"], True),
            ("multiple_roles_locations", ["Software Engineer", "Full Stack Developer",
              "React Native Developer", "Business Analyst"],
             ["Bengaluru", "Hyderabad", "Pune", "Remote"], True),
        ]
        plans, fixtures = [], []
        for name, roles, locations, naukri in cases:
            search, sites = copy.deepcopy(config.SEARCH), copy.deepcopy(config.SITES)
            if roles is not None:
                search.update(role_keywords=roles, locations=locations)
                sites["linkedin"]["locations"] = locations
                sites["naukri"]["enabled"] = naukri
                skills = ({"salesforce": 5, "sales cloud": 5, "uat": 1}
                          if name == "business_salesforce" else
                          {"react native": 5, "typescript": 5, "javascript": 3}
                          if name == "react_native" else
                          {"react": 5, "node.js": 5, "typescript": 5})
                data = {"years_experience": 2, "experience_months": 24,
                        "role_keywords": roles, "skill_weights": [
                            {"term": term, "weight": weight} for term, weight in skills.items()],
                        "penalty_terms": [], "domain_half_a": [], "domain_half_b": [],
                        "domain_title_terms": [], "domain_bonus": 0,
                        "field_summary": "Synthetic audit fixture", "notes": "No real candidate data",
                        "title_hints": roles, "title_exclude": []}
                prefs = {"locations": locations, "linkedin_locations": locations,
                         "max_results": 15, "max_age_days": 14,
                         "min_comp_usd": None, "exclude_levels": [], "avoid": [],
                         "sites_enabled": {"linkedin": True, "indeed": True, "naukri": naukri}}
                make_profile.render("audit_" + name, data, prefs)
                fixtures.append({"case": name, "derived": data, "prefs": prefs,
                                 "caveat": "Synthetic candidate inputs, not extraction/generation output; "
                                 "scoring engine and title gates stay frozen"})
            with patch.object(scraper, "SEARCH", search), patch.object(scraper, "SITES", sites):
                raw = {"profile": name, "sites": {}, "max_results": {}}
                units = []
                for site in scraper.resolve_sites(args):
                    searches = scraper.plan_for_site(site, args)
                    raw["sites"][site] = searches
                    for original in searches:
                        effective = scraper.effective_search(site, original)
                        raw["max_results"][site] = effective["max_results"]
                        units.append({"source": site, "query": original["keywords"],
                            "location": original["location"], "country": original["country"],
                            "remote_query": scraper.remote_was_queried(site, original),
                            "max_age_days": scraper.SETTINGS["max_age_days"],
                            "max_results_intended": effective["max_results"],
                            "actor": sites[site]["actor"], "actor_runs": 1,
                            "input": scraper.build_input(site, effective)})
                plans.append({"case": name, "basis": "synthetic lowered SEARCH/SITES inputs; "
                    "production planner/adapters; no profile generation or scoring changes",
                    "cost_estimate": cost(raw, config.SITE_RATES, config.SITE_RATE_BASIS),
                    "intended_result_capacity": sum(u["max_results_intended"] for u in units),
                    "units": units})
        assert [len(p["units"]) for p in plans] == [90, 8, 8, 18, 40]

        # Deliberately distinct URLs/locations: prove the actual identity rule.
        synthetic = [
            {"title": "Software Engineer", "company": "Example Labs",
             "location": "Bengaluru", "apply_url": "https://example.invalid/jobs/1"},
            {"title": "Engineer Software", "company": "Example",
             "location": "Hyderabad", "apply_url": "https://example.invalid/jobs/2"},
        ]
        identity = {"case": "synthetic distinct location and URL", "input_rows": 2,
                    "output_rows": len(scraper.dedupe(synthetic)),
                    "keys": [scraper.job_key(r) for r in synthetic]}
        assert identity["output_rows"] == 1
        missing_identity = [
            {"apply_url": "https://example.invalid/viewjob?jk=1"},
            {"apply_url": "https://example.invalid/viewjob?jk=2"}]
        identity["query_id_url_fallback_rows_kept"] = len(scraper.dedupe(missing_identity))
        assert identity["query_id_url_fallback_rows_kept"] == 1
        identity["same_req_id_different_employers_rows_kept"] = len(scraper.dedupe([
            {"title": "Engineer", "company": "Example A", "req_number": "123"},
            {"title": "Analyst", "company": "Example B", "req_number": "123"}]))
        assert identity["same_req_id_different_employers_rows_kept"] == 1

        # Numeric aggregates only: never export names, queries, JDs, or tokens.
        historical = []
        for path in sorted(ROOT.glob("scratch*.log")):
            content = path.read_text(errors="replace")
            totals = re.findall(r"Total pulled:\s*(\d+)", content)
            finals = re.findall(r"After scoring/filter\+dedupe:\s*(\d+)", content)
            costs = re.findall(r"Total Apify spend this run: \$(\d+\.\d+)", content)
            if not totals and not costs:
                continue
            historical.append({"artifact": f"historical_log_{len(historical) + 1:02d}",
                "run_total_pulled": list(map(int, totals)),
                "run_final_rows": list(map(int, finals)),
                "run_reported_spend_usd": list(map(float, costs)),
                "actor_success_log_lines": len(re.findall(r"\[\d+/\d+\].*\d+ jobs", content)),
                "actor_failure_log_lines": len(re.findall(r"\[\d+/\d+\].* ! ", content)),
                "timing_available": False,
                "caveat": "historical console accounting, not invoice; logs may concatenate runs "
                          "or include free sources; not current-profile quality evidence"})
        DEST.mkdir(parents=True, exist_ok=True)
        (DEST / "paid-plans.json").write_text(json.dumps({"status": "MEASURED OFFLINE",
            "paid_calls": 0, "synthetic_profile_fixtures": fixtures, "cases": plans}, indent=2) + "\n")
        (DEST / "paid-offline-evidence.json").write_text(json.dumps({
            "status": "MEASURED OFFLINE", "paid_calls": 0, "dedupe_probes": identity,
            "historical_redacted_aggregates": historical}, indent=2) + "\n")
        print(json.dumps({p["case"]: {"runs": len(p["units"]),
            "capacity": p["intended_result_capacity"],
            "estimated_usd": p["cost_estimate"]["total"]} for p in plans}, indent=2))
        print("Offline assertions passed; paid calls: 0.")


if __name__ == "__main__":
    main()
