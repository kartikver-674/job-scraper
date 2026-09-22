#!/usr/bin/env python3
"""Audit-only public free-source census; never imports profiles or paid clients.

Default: write exact registry without network. --live: sequential, one attempt,
15s socket timeout, 20MiB body cap. Disabled enterprise/Optum remain UNKNOWN.
Snapshots contain public job fields only and default to /tmp (not committed).
All feed configured pages are attempted; a failed feed loses its partial rows,
matching the production adapter boundary. HTTP evidence survives that failure.
"""
import argparse
import ast
import collections
import csv
import datetime as dt
import json
from pathlib import Path
import re
import resource
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sources import ats, feeds, enterprise, _http, FEED_FETCHERS


def literals():
    wanted = {"ATS_BOARDS", "FEEDS", "OPTUM", "ENTERPRISE", "ATS_TITLE_HINTS",
              "ATS_TITLE_EXCLUDE", "LOCATION_HINTS", "HOME_LOCATION_HINTS"}
    return {n.targets[0].id: ast.literal_eval(n.value)
            for n in ast.parse((ROOT / "config.py").read_text()).body
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in wanted}


def identity_functions():
    names = {"_norm_key", "_company_key", "_title_key", "_canonical_url", "job_key"}
    nodes = [n for n in ast.parse((ROOT / "scraper.py").read_text()).body
             if isinstance(n, ast.FunctionDef) and n.name in names
             or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name)
                and t.id == "_CORP_SUFFIX_RE" for t in n.targets)]
    namespace = {"re": re, "urllib": urllib}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "scraper.py:identity-only", "exec"), namespace)
    return namespace["job_key"]


def registry(cfg):
    rows = []
    for family, boards in cfg["ATS_BOARDS"].items():
        for token, company in boards.items():
            rows.append(dict(source_name=f"{family}:{token}", company=company,
                provider_family=family, board_identifier=token,
                endpoint=ats.ATS[family]["url"].format(token=token),
                endpoint_type="public JSON GET", country="UNKNOWN: no per-board metadata",
                active=True, configuration={}, pagination="single response; SmartRecruiters capped at 100" if family == "smartrecruiters" else "single response; no adapter pagination",
                source_file="config.py:196", source_health_metadata="none"))
    endpoints = {"remoteok": "https://remoteok.com/api", "wwr": "https://weworkremotely.com/categories/{category}.rss",
        "remotive": "https://remotive.com/api/remote-jobs?category=software-dev",
        "jobicy": "https://jobicy.com/api/v2/remote-jobs?count=50&industry=engineering",
        "himalayas": "https://himalayas.app/jobs/api?limit=20&offset={offset}"}
    for name, spec in cfg["FEEDS"].items():
        rows.append(dict(source_name=name, company="MULTIPLE", provider_family=name,
            board_identifier=name, endpoint=endpoints[name], endpoint_type="public RSS GET" if name == "wwr" else "public JSON GET",
            country="remote feed; job-specific restrictions", active=spec.get("enabled", False), configuration=spec,
            pagination="8 category feeds" if name == "wwr" else "10 x 20 browse rows; profile may use <=8 query calls" if name == "himalayas" else "single response",
            source_file="config.py:507", source_health_metadata="none"))
    for slug, spec in enterprise.EMPLOYERS.items():
        family = spec["platform"]
        endpoint = {"amazon": "https://www.amazon.jobs/en/search.json", "orc": f"https://{spec.get('host')}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
            "workday": f"https://{spec.get('host')}/wday/cxs/{spec.get('tenant')}/{spec.get('site')}/jobs", "successfactors": f"https://{spec.get('host')}/search/"}[family]
        rows.append(dict(source_name=slug, company=spec["company"], provider_family=family,
            board_identifier=spec.get("site", slug), endpoint=endpoint,
            endpoint_type="public JSON POST" if family == "workday" else "public HTML GET" if family == "successfactors" else "public JSON GET",
            country=spec.get("country", spec.get("location", "UNKNOWN: global listing")),
            active=cfg["ENTERPRISE"].get("enabled", False) and slug in cfg["ENTERPRISE"]["employers"],
            configuration=dict(cfg["ENTERPRISE"], employer_spec=spec),
            pagination="5 configured pages (Workday 25 x 20); optional per-job detail",
            source_file="sources/enterprise.py:65; config.py:496", source_health_metadata="historical comments only"))
    rows.append(dict(source_name="optum", company="Optum", provider_family="radancy_talentbrew",
        board_identifier="34088; brand optum", endpoint="https://careers.unitedhealthgroup.com/search-jobs/results",
        endpoint_type="public JSON containing HTML GET", country="global listing; location query ignored",
        active=cfg["OPTUM"].get("enabled", False), configuration=cfg["OPTUM"],
        pagination="<=70 x 100 listings plus detail per title/location survivor",
        source_file="config.py:456; sources/optum.py:64", source_health_metadata="historical comments only"))
    return rows


class HTTP:
    def __init__(self, timeout):
        self.calls, self.source, self.timeout = [], "", timeout

    def get(self, url, **ignored):
        start = time.perf_counter()
        item = dict(source=self.source, url=url, method="GET", started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    response_bytes=0, status=None, error=None)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _http.UA})
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                item["status"] = response.status
                item["response_date"] = response.headers.get("Date")
                data = response.read(20 * 1024 * 1024 + 1)
                item["response_bytes"] = len(data)
                if len(data) > 20 * 1024 * 1024:
                    raise ValueError("audit response body exceeds 20MiB")
                return data
        except Exception as exc:
            item["status"] = getattr(exc, "code", item["status"])
            item["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            item["duration_ms"] = round((time.perf_counter() - start) * 1000, 3)
            self.calls.append(item)


ROLE_PATTERNS = {
    "software engineering": r"software|developer|engineer", "full stack": r"full[ -]?stack|mern",
    "frontend / React": r"front[ -]?end|react(?! native)", "React Native / mobile": r"react native|mobile|android|ios\b",
    "backend": r"back[ -]?end", "Java": r"\bjava\b", "Python": r"\bpython\b",
    "QA / automation": r"\bqa\b|quality assurance|test.*automat|sdet", "DevOps / cloud": r"devops|cloud|site reliability|\bsre\b",
    "data / ML": r"\bdata\b|machine learning|\bml\b|\bai\b", "business analyst": r"business analyst",
    "Salesforce functional": r"salesforce.*(functional|consultant)|functional.*salesforce",
    "Salesforce administrator": r"salesforce.*admin", "product": r"\bproduct\b",
    "operations": r"operation", "early-career roles": r"junior|\bintern\b|graduate|entry[ -]level|associate|\bnew grad\b"}
CITY_PATTERNS = {"Bengaluru": "bengaluru|bangalore", "Gurgaon / Gurugram": "gurgaon|gurugram",
    "Hyderabad": "hyderabad", "Pune": "pune", "Mumbai": "mumbai|bombay", "Delhi / NCR": r"delhi|\bncr\b",
    "Noida": "noida", "Chandigarh / Mohali": "chandigarh|mohali", "Chennai": "chennai|madras", "India-wide mention": r"\bindia\b"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--snapshot", default="/tmp/search-v2-current-free-jobs.json")
    parser.add_argument("--output", default="docs/search-v2-evidence")
    args = parser.parse_args()
    cfg, key = literals(), identity_functions()
    records, all_jobs, http = registry(cfg), [], HTTP(args.timeout)
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    _http.get_bytes = http.get
    global_seen, global_url_seen = set(), set()
    today = dt.datetime.now(dt.timezone.utc).date()
    for record in records:
        name, family = record["source_name"], record["provider_family"]
        record.update(fetch_succeeds=None, raw_job_count=None, normalized_job_count=None, fresh_job_count=None,
            unique_job_count=None, eligible_job_count=None, candidate_relevant_job_count=None, duplicate_rate=None,
            fetch_time_ms=None, error_failure_type="not measured: disabled in default config" if not record["active"] else "not measured: offline inventory",
            eligible_measurement="UNKNOWN: no candidate profile; synthetic proxy reported separately")
        if not args.live or not record["active"]:
            continue
        http.source = name
        before, began = len(http.calls), time.perf_counter()
        try:
            if family in ats.ATS:
                data = json.loads(http.get(record["endpoint"]).decode("utf-8", "replace"))
                spec = ats.ATS[family]
                items = _http.dig(data, spec["list"]) if spec["list"] else data
                if not isinstance(items, list):
                    raise ValueError("schema: job list is absent or not a list")
                record["raw_job_count"] = len(items)
                record["provider_total"] = data.get("totalFound") if isinstance(data, dict) else None
                rows = [ats._row(i, family, record["board_identifier"], record["company"], spec) for i in items]
                hires_home = "yes" if any(any(h in r["Location"].lower() for h in cfg["HOME_LOCATION_HINTS"]) for r in rows) else "no"
                for row in rows:
                    row["hires_home"] = hires_home
            else:
                rows = FEED_FETCHERS[name](cfg["FEEDS"][name], lambda _: True, lambda _: True)
                record["raw_job_count"] = len(rows)  # parsed job entries; excludes feed metadata
            finished_fetch = time.perf_counter()
            record.update(fetch_succeeds=True, normalized_job_count=len(rows), error_failure_type=None,
                          fetch_time_ms=round((finished_fetch - began) * 1000, 3))
            valid_dates, fresh, future = [], 0, 0
            for row in rows:
                try:
                    age = (today - dt.date.fromisoformat(row["Posted Date"][:10])).days
                    valid_dates.append(age)
                    fresh += 0 <= age <= 21
                    future += age < 0
                except (ValueError, TypeError):
                    pass
            keys = [key(r) for r in rows]
            unique = set(k for k in keys if k)
            record.update(unique_job_count=len(unique) + keys.count(None),
                marginal_unique_job_count=len(unique - global_seen) + keys.count(None),
                fresh_job_count=fresh, stale_job_count=sum(a > 21 for a in valid_dates), future_date_count=future,
                unknown_date_count=len(rows) - len(valid_dates),
                duplicate_rate=round(1 - (len(unique) + keys.count(None)) / len(rows), 6) if rows else 0)
            global_seen.update(unique)
            urls = set(r["Job URL"].split("?")[0].rstrip("/").lower() for r in rows if r["Job URL"])
            record["canonical_url_unique_count"] = len(urls)
            record["marginal_canonical_url_unique_count"] = len(urls - global_url_seen)
            global_url_seen.update(urls)
            def india(row):
                return bool(re.search(r"\bindia\b|bengaluru|bangalore|gurgaon|gurugram|hyderabad|pune|mumbai|delhi|noida|chandigarh|mohali|chennai", row["Location"], re.I))
            title_rows = [r for r in rows if any(h in r["Title"].lower() for h in cfg["ATS_TITLE_HINTS"]) and not any(h in r["Title"].lower() for h in cfg["ATS_TITLE_EXCLUDE"])]
            proxy = [r for r in title_rows if india(r)]
            record.update(default_title_gate_count=len(title_rows), explicit_india_location_count=sum(india(r) for r in rows),
                synthetic_india_software_proxy_count=len({key(r) for r in proxy}),
                remote_location_mention_count=sum("remote" in r["Location"].lower() for r in rows),
                missing_title_count=sum(not r["Title"] for r in rows), missing_location_count=sum(not r["Location"] for r in rows),
                missing_description_count=sum(not r["Description"] for r in rows),
                salary_present_count=sum(bool(r["Salary"]) for r in rows),
                experience_present_count=sum(bool(r["Experience"]) for r in rows))
            for row in rows:
                row["audit_source"] = name
            all_jobs.extend(rows)
            record["local_analysis_ms"] = round((time.perf_counter() - finished_fetch) * 1000, 3)
        except Exception as exc:
            record.update(fetch_succeeds=False, fetch_time_ms=round((time.perf_counter() - began) * 1000, 3),
                          error_failure_type=f"{type(exc).__name__}: {exc}")
        calls = http.calls[before:]
        record.update(http_calls=len(calls), response_bytes=sum(c["response_bytes"] for c in calls),
                      http_time_ms=round(sum(c["duration_ms"] for c in calls), 3))
        print(name, record["fetch_succeeds"], record["raw_job_count"], record["error_failure_type"], flush=True)
        (out / "current-free-inventory.json").write_text(json.dumps(records, indent=2))
        (out / "current-free-http.json").write_text(json.dumps(http.calls, indent=2))
        Path(args.snapshot).write_text(json.dumps(all_jobs))
    summary = dict(started_at=stamp, finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        audit_wall_seconds=round(time.perf_counter() - start, 3), socket_timeout_seconds=args.timeout,
        retries=0, live=args.live, configured_records=len(records), active_records=sum(r["active"] for r in records),
        ats_records=sum(len(b) for b in cfg["ATS_BOARDS"].values()),
        family_counts=dict(collections.Counter(r["provider_family"] for r in records)),
        successful_sources=sum(r["fetch_succeeds"] is True for r in records),
        failed_sources=sum(r["fetch_succeeds"] is False for r in records),
        unknown_sources=sum(r["fetch_succeeds"] is None for r in records),
        zero_job_sources=[r["source_name"] for r in records if r["raw_job_count"] == 0],
        raw_normalized_rows=len(all_jobs), production_key_unique=len(global_seen), canonical_url_unique=len(global_url_seen),
        http_calls=len(http.calls), response_bytes=sum(c["response_bytes"] for c in http.calls),
        http_time_seconds=round(sum(c["duration_ms"] for c in http.calls) / 1000, 3),
        peak_rss_platform_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        snapshot=args.snapshot,
        role_title_proxy_counts={label: sum(bool(re.search(pat, r["Title"], re.I)) for r in all_jobs) for label, pat in ROLE_PATTERNS.items()},
        city_location_proxy_counts={label: sum(bool(re.search(pat, r["Location"], re.I)) for r in all_jobs) for label, pat in CITY_PATTERNS.items()})
    useful = sorted((r.get("synthetic_india_software_proxy_count", 0) for r in records), reverse=True)
    summary["synthetic_india_software_proxy_sum"] = sum(useful)
    summary["synthetic_proxy_concentration"] = {str(n): round(sum(useful[:n]) / sum(useful), 4) if sum(useful) else None for n in (10, 25, 50)}
    (out / "current-free-inventory.json").write_text(json.dumps(records, indent=2))
    (out / "current-free-http.json").write_text(json.dumps(http.calls, indent=2))
    (out / "current-free-summary.json").write_text(json.dumps(summary, indent=2))
    fields = list(dict.fromkeys(k for r in records for k in r))
    with (out / "current-free-inventory.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: json.dumps(v) if isinstance(v, (dict, list)) else "UNKNOWN" if v is None else v for k, v in r.items()} for r in records)
    if args.live:
        Path(args.snapshot).write_text(json.dumps(all_jobs))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
