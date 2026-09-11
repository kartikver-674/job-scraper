"""Target 2 at scale: does the Apify-only backlog hold public ATS boards?

MEASUREMENT ONLY. Nothing is written to ATS_BOARDS; promotion is a separate,
explicit step, exactly as every prior round in this line of work.

Tests the hypothesis the last probe raised but could not settle: employer
profile predicted resolution rate there (bucket2, already-known and global,
16.9%; bucket3, India-heavy long tail, 9.1%, against a prior 8.2% baseline).
Those two sets were constructed differently, so the divergence may be an
artifact. This probes ~8x more companies and segments by proxies that already
exist in output/ rather than by how a set was assembled.

Three things this run does that the 329-company round did not need:

  resumable      a JSONL checkpoint, one line per finished company, in the
                 shape of .done_combos. An interruption loses nothing and
                 re-probes nothing.
  block-aware    the five platforms are a handful of SHARED multi-tenant hosts
                 being asked about thousands of slugs from one IP. That looks
                 different to them than one request per company server.
                 harvest_ats.get_json is WRAPPED (not modified) to observe
                 status codes, and a platform that starts answering 429/403 is
                 DISABLED rather than slowed down and retried.
  latency-aware  a rolling median per platform; a sustained spike is reported.

    python -m bench.backlog_harvest --companies <json> --state <dir>
    python -m bench.backlog_harvest --demo
"""
import argparse
import collections
import json
import os
import statistics
import sys
import time
import urllib.error

import scraper
import harvest_ats
from sources import ats
from sources._http import get_json as _plain_get_json

BLOCK_CODES = (429, 403)
BLOCK_STREAK = 5          # consecutive block-shaped answers -> disable platform
LATENCY_WINDOW = 50
LATENCY_ALERT = 6.0       # seconds, rolling median
WORKABLE = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"

STATUS = collections.defaultdict(collections.Counter)
LATENCY = collections.defaultdict(collections.deque)
STREAK = collections.Counter()
DISABLED = set()
_CURRENT = {"platform": None}


def _observing_get_json(url, **kw):
    """Wraps harvest_ats's own get_json so probe() stays byte-for-byte unmodified
    while we can still see WHY a probe failed. probe() swallows every exception
    and returns None, which is right for resolution and useless for telling a
    404 apart from a 429."""
    plat = _CURRENT["platform"]
    start = time.monotonic()
    try:
        out = _plain_get_json(url, **kw)
        STATUS[plat]["ok"] += 1
        STREAK[plat] = 0
        return out
    except urllib.error.HTTPError as exc:
        STATUS[plat][exc.code] += 1
        STREAK[plat] = STREAK[plat] + 1 if exc.code in BLOCK_CODES else 0
        raise
    except Exception as exc:
        STATUS[plat][type(exc).__name__] += 1
        raise
    finally:
        d = LATENCY[plat]
        d.append(time.monotonic() - start)
        if len(d) > LATENCY_WINDOW:
            d.popleft()


harvest_ats.get_json = _observing_get_json


def platform_healthy(platform, log):
    if platform in DISABLED:
        return False
    if STREAK[platform] >= BLOCK_STREAK:
        DISABLED.add(platform)
        log(f"  ! {platform}: {BLOCK_STREAK} block-shaped answers in a row "
            f"({dict(STATUS[platform])}) — DISABLING. Not retrying, not slowing down.")
        return False
    d = LATENCY[platform]
    if len(d) == LATENCY_WINDOW and statistics.median(d) > LATENCY_ALERT:
        DISABLED.add(platform)
        log(f"  ! {platform}: rolling median latency "
            f"{statistics.median(d):.1f}s — DISABLING (looks like throttling).")
        return False
    return True


def probe_company(display, cache, delay, log):
    """First healthy platform that resolves. Returns (platform, token, (jobs, home))."""
    for token in harvest_ats.slugs(display):
        for platform in ats.ATS:
            if not platform_healthy(platform, log):
                continue
            key = f"{platform}:{token}"
            if key not in cache:
                _CURRENT["platform"] = platform
                cache[key] = (lambda h: [h[0], h[1]] if h else None)(
                    harvest_ats.probe(platform, token))
                time.sleep(delay)
            if cache[key]:
                return platform, token, cache[key]
    return None


def workable_signal(display, cache, delay):
    for token in harvest_ats.slugs(display):
        key = f"workable:{token}"
        if key not in cache:
            _CURRENT["platform"] = "workable"
            try:
                data = _observing_get_json(WORKABLE.format(token=token),
                                           timeout=15, retries=0)
                jobs = (data or {}).get("jobs")
                cache[key] = len(jobs) if isinstance(jobs, list) else None
            except Exception:
                cache[key] = None
            time.sleep(delay)
        if cache[key]:
            return token, cache[key]
    return None


def paid_history():
    """{company_key: {job_key: output_row}} for every LinkedIn/Indeed/Naukri row
    the repo has ever written. This is the comparison set: 'does the board carry
    a posting we have actually seen for this employer'."""
    PAID = ("linkedin", "indeed", "naukri")
    out = collections.defaultdict(dict)
    for f in __import__("glob").glob("output/**/jobs_*.json", recursive=True):
        try:
            data = json.load(open(f))
        except Exception:
            continue
        for r in data:
            if (r.get("source_site") or "").split(":")[0] not in PAID:
                continue
            ck = scraper._company_key(r.get("company"))
            k = scraper.job_key(r)
            if ck and k and (k not in out[ck]
                             or (r.get("score") or 0) > (out[ck][k].get("score") or 0)):
                out[ck][k] = r
    return out


def n_skills(v):
    return len([s for s in (v or "").split(",") if s.strip()])


def demo():
    from bench.ats_harvest_probe import is_staffing
    assert is_staffing("bcforward india", "BCforward India") == "named"
    assert is_staffing("x", "Talent Aspire") == "pattern_strong"
    assert is_staffing("x", "Tata Consultancy Services") == "pattern_weak"
    STREAK.clear(); DISABLED.clear()
    logged = []
    STREAK["greenhouse"] = BLOCK_STREAK
    assert not platform_healthy("greenhouse", logged.append)
    assert "greenhouse" in DISABLED and "DISABLING" in logged[0]
    assert not platform_healthy("greenhouse", logged.append)   # stays disabled
    STREAK.clear(); DISABLED.clear()
    assert platform_healthy("lever", logged.append)
    print("bench.backlog_harvest: all self-checks pass")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies")
    ap.add_argument("--state")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.demo:
        demo()
        return
    from bench.ats_harvest_probe import is_staffing

    os.makedirs(args.state, exist_ok=True)
    ck_path = os.path.join(args.state, "checkpoint.jsonl")
    cache_path = os.path.join(args.state, "probe_cache.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

    done = set()
    if os.path.exists(ck_path):
        for line in open(ck_path):
            try:
                done.add(json.loads(line)["company_key"])
            except Exception:
                pass

    companies = json.load(open(args.companies))
    todo = [c for c in companies if c not in done]   # file order IS the sampling plan
    if args.limit:
        todo = todo[:args.limit]
    hist = paid_history()

    def log(msg):
        print(msg, flush=True)

    log(f"companies to probe {len(todo)}  (already checkpointed {len(done)})")
    log(f"platforms          {', '.join(ats.ATS)}   delay {args.delay}s\n")

    t0 = time.monotonic()
    with open(ck_path, "a") as ck:
        for i, c in enumerate(todo, 1):
            display = companies[c]
            rec = {"company_key": c, "display": display,
                   "staffing": is_staffing(c, display),
                   "seen_postings": len(hist.get(c, {}))}
            hit = probe_company(display, cache, args.delay, log)
            if hit:
                platform, token, (jobs, home) = hit
                rec.update(resolved=True, platform=platform, token=token,
                           board_jobs=jobs, board_india=home)
                try:
                    _CURRENT["platform"] = platform
                    rows = ats.fetch(platform, token, display,
                                     lambda t: True, lambda l: True)
                    by_key = {}
                    for r in rows:
                        k = scraper.job_key(r)
                        if k:
                            by_key[k] = r
                    matches = []
                    for k, paid_row in hist.get(c, {}).items():
                        if k in by_key:
                            full = scraper.score_job(dict(by_key[k]))
                            matches.append({
                                "title": paid_row.get("title", ""),
                                "paid_source": paid_row.get("source_site", ""),
                                "paid_score": paid_row.get("score", 0),
                                "paid_skills": n_skills(paid_row.get("matched_skills")),
                                "ats_score": full["score"] if full else None,
                                "ats_skills": (n_skills(full.get("matched_skills"))
                                               if full else None),
                                "ats_desc": len(by_key[k].get("Description") or ""),
                            })
                    rec["matches"] = matches
                    time.sleep(args.delay)
                except Exception as exc:
                    rec["board_error"] = f"{type(exc).__name__}: {exc}"
                log(f"  [{i}/{len(todo)}] {display[:32]:<32} {platform}:{token} "
                    f"{jobs} jobs, {len(rec.get('matches', []))}/{rec['seen_postings']} known"
                    + ("  [staffing]" if rec["staffing"] else ""))
            else:
                rec["resolved"] = False
                if not any(p not in DISABLED for p in ats.ATS):
                    log("\n  ! every platform disabled — stopping.")
                    ck.write(json.dumps(rec) + "\n")
                    break
                sig = workable_signal(display, cache, args.delay)
                if sig:
                    rec["workable_signal"] = {"token": sig[0], "jobs": sig[1]}
            ck.write(json.dumps(rec) + "\n")
            ck.flush()
            if i % 50 == 0:
                json.dump(cache, open(cache_path, "w"))
                rate = i / (time.monotonic() - t0)
                log(f"  ... {i}/{len(todo)}  {rate*60:.0f} companies/min  "
                    f"eta {(len(todo)-i)/rate/60:.0f} min  "
                    f"disabled={sorted(DISABLED) or 'none'}")
    json.dump(cache, open(cache_path, "w"))
    log(f"\nstatus by platform: "
        + json.dumps({k: dict(v) for k, v in STATUS.items()}))
    log(f"disabled: {sorted(DISABLED) or 'none'}")


if __name__ == "__main__":
    main()
