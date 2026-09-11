"""Does Adzuna's redirect_url reach a full job description? MEASUREMENT ONLY.

The one open question from docs/superpowers/specs/2026-09-11-adzuna-phase1-results.md.
Adzuna caps every description at 500 characters, which cost a median 16 score
points on the postings we could see from both sides. If redirect_url reaches the
real JD, that cap is an implementation detail; if it does not, Adzuna is a thin
employer-discovery signal and nothing more.

Deliberately NARROW. Two sets only:

  shared    the postings present on BOTH LinkedIn and Adzuna. The truncation
            hypothesis in isolation -- we already know the opening is real and
            we already have a full-JD score for it to be compared against.
  bucket3   a spread across employers never seen via ANY source. Tests whether
            the fetch also works where the discovery value actually is.

The 400 Software Engineer / AI Engineer rows are NOT probed. They scored zero
because Adzuna's full-text search mistargeted the query, and a full JD fixes
nothing about a job that was never the job we asked for.

This probe does not push through a block. A 403, a CAPTCHA, or a login wall IS
the answer, and it is recorded as such rather than worked around.

    python -m bench.adzuna_jd_probe --cache <dir> --out <json>     # offline set-up + fetches
    python -m bench.adzuna_jd_probe --demo                          # no network
"""
import argparse
import collections
import glob
import html
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import config
import scraper
from sources._http import strip_html

UA = "Mozilla/5.0 (compatible; job-scraper)"
TIMEOUT = 20
RETRIES = 2                # matches sources/_http.py
PER_DOMAIN_DELAY = 3.0     # seconds between hits on the SAME host
MAX_FETCHES = 40
BLOCK_STREAK = 3           # consecutive 403/429 -> stop entirely
USEFUL = 20

# An employer's own ATS is the employer's own door: these are the platforms
# sources/ats.py and sources/enterprise.py already fetch directly and freely.
ATS_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "breezy.hr",
    "smartrecruiters.com", "myworkdayjobs.com", "myworkdaysite.com", "wd1.myworkdaycdn.com",
    "oraclecloud.com", "taleo.net", "icims.com", "jobvite.com", "bamboohr.com",
    "recruitee.com", "successfactors.com", "avature.net", "eightfold.ai",
    "phenompeople.com", "peoplefluent.com", "brassring.com", "silkroad.com",
)

# Other job boards / aggregators. Landing here reopens exactly the third-party
# terms question that put LinkedIn behind Apify, so it is named, not glossed.
AGGREGATOR_HOSTS = (
    "adzuna.com", "adzuna.in", "indeed.com", "naukri.com", "linkedin.com",
    "glassdoor.com", "ziprecruiter.com", "monster.com", "monsterindia.com",
    "shine.com", "timesjobs.com", "foundit.in", "instahyre.com", "cutshort.io",
    "hirist.tech", "hirist.com", "iimjobs.com", "apna.co", "jobsdb.com",
    "simplyhired.com", "careerbuilder.com", "dice.com", "jooble.org",
    "talent.com", "jobrapido.com", "neuvoo", "trabajo", "whatjobs.com",
    "jobg8.com", "careerjet", "expertini", "jobleads", "learn4good",
    "recruit.net", "jobsora", "joblift", "clickajobs", "jobted", "jobisjob",
    "bebee.com", "wisdomjobs", "freshersworld", "placementindia",
)


def host_of(url):
    return (urllib.parse.urlsplit(url).netloc or "").lower().removeprefix("www.")


def classify_host(hostname):
    """employer_direct | aggregator | unknown_direct.

    unknown_direct is kept SEPARATE from employer_direct rather than assumed
    into it: a host we do not recognise is usually the employer's own careers
    site, but 'usually' is not a category.
    """
    for h in AGGREGATOR_HOSTS:
        if h in hostname:
            return "aggregator"
    for h in ATS_HOSTS:
        if h in hostname:
            return "employer_ats"
    return "unknown_direct"


# --------------------------------------------------------------------------
# Page reading
# --------------------------------------------------------------------------
_JSONLD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                     re.S | re.I)
_BLOCK = re.compile(r"(are you a robot|captcha|cf-browser-verification|"
                    r"enable javascript and cookies|access denied|"
                    r"unusual traffic|verify you are human)", re.I)
_LOGIN = re.compile(r"(sign in to continue|log in to view|please sign in|"
                    r"create an account to view|login required)", re.I)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def jd_from_jsonld(page):
    """The JD out of a schema.org JobPosting, or None.

    Strongly preferred over the whole page: whole-page text drags in nav and
    'related jobs' rails, which would credit a posting with skills that appear
    only in a sidebar. That would manufacture exactly the lift this probe is
    supposed to be testing for.
    """
    for blob in _JSONLD.findall(page):
        try:
            data = json.loads(html.unescape(blob.strip()))
        except Exception:
            continue
        for node in _walk(data):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if "JobPosting" in types and node.get("description"):
                return strip_html(node["description"])
    return None


def read_page(page):
    """(jd_text, how, verdict). how = jsonld | wholepage | none."""
    text = strip_html(page)
    if _BLOCK.search(page):
        return "", "none", "blocked_page"
    jd = jd_from_jsonld(page)
    if jd and len(jd) > 300:
        return jd, "jsonld", "job_posting"
    if _LOGIN.search(page) and len(text) < 3000:
        return "", "none", "login_wall"
    if len(text) < 800:
        return "", "none", "thin_or_dead"
    return text, "wholepage", "page_text_only"


# --------------------------------------------------------------------------
def fetch(url, last_hit):
    """One bounded GET. Returns (status, final_url, body, note).

    Same discipline as sources/_http.py: short timeout, at most two retries,
    NEVER a retry on 4xx -- a block answered once is answered.
    """
    hostname = host_of(url)
    wait = PER_DOMAIN_DELAY - (time.monotonic() - last_hit.get(hostname, 0))
    if wait > 0:
        time.sleep(wait)
    err = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(400_000).decode("utf-8", "replace")
                last_hit[host_of(resp.geturl())] = time.monotonic()
                return resp.status, resp.geturl(), body, ""
        except urllib.error.HTTPError as exc:
            last_hit[hostname] = time.monotonic()
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if 400 <= exc.code < 500:
                return exc.code, exc.url or url, "", (
                    f"retry-after={retry_after}" if retry_after else "")
            err = exc
        except Exception as exc:
            last_hit[hostname] = time.monotonic()
            err = exc
        if attempt < RETRIES:
            time.sleep(2 ** attempt)
    return None, url, "", f"{type(err).__name__}: {err}"


# --------------------------------------------------------------------------
def rows_from_cache(cache_dir):
    """Rebuild the scored Adzuna rows from the replay's response cache.

    The replay's result JSON dropped Job URL, and re-running to recover it would
    cost quota for data already on disk.
    """
    out = {}
    for f in sorted(glob.glob(os.path.join(cache_dir, "*.json"))):
        for raw in json.load(open(f)):
            scored = scraper.score_job(dict(raw))
            if scored is None:
                continue
            k = scraper.job_key(scored)
            if k and (k not in out or scored["score"] > out[k]["score"]):
                out[k] = scored
    return out


def linkedin_keys(profile):
    keys = {}
    for f in glob.glob(f"output/{profile}/jobs_*.json"):
        try:
            data = json.load(open(f))
        except Exception:
            continue
        for r in data:
            if not (r.get("source_site") or "").startswith("linkedin"):
                continue
            k = scraper.job_key(r)
            if k and (k not in keys or (r.get("score") or 0) > keys[k]):
                keys[k] = r.get("score") or 0
    return keys


def repo_companies(paid):
    PAID = ("linkedin", "indeed", "naukri")
    seen = set()
    for f in glob.glob("output/**/jobs_*.json", recursive=True):
        try:
            data = json.load(open(f))
        except Exception:
            continue
        for r in data:
            fam = (r.get("source_site") or "").split(":")[0]
            if not fam:
                continue
            if (fam in PAID) == paid:
                seen.add(scraper._company_key(r.get("company")))
    seen.discard("")
    return seen


def n_skills(value):
    return len([s for s in (value or "").split(",") if s.strip()])


def summarize(pairs, label):
    """pairs: [(snippet_score, full_score, snippet_skills, full_skills)]"""
    if not pairs:
        print(f"  {label}: no successful fetches")
        return {}
    ds = [f - s for s, f, _, _ in pairs]
    sk = [fk - sk_ for _, _, sk_, fk in pairs]
    was = sum(1 for s, _, _, _ in pairs if s >= USEFUL)
    now = sum(1 for _, f, _, _ in pairs if f >= USEFUL)
    out = {"n": len(pairs), "median_score_gain": statistics.median(ds),
           "median_skill_gain": statistics.median(sk),
           "useful_before": was, "useful_after": now,
           "median_score_before": statistics.median([s for s, _, _, _ in pairs]),
           "median_score_after": statistics.median([f for _, f, _, _ in pairs]),
           "median_skills_before": statistics.median([s for _, _, s, _ in pairs]),
           "median_skills_after": statistics.median([f for _, _, _, f in pairs])}
    print(f"  {label}: n={out['n']}  "
          f"score {out['median_score_before']}->{out['median_score_after']} "
          f"(median gain {out['median_score_gain']:+})  "
          f"skills {out['median_skills_before']}->{out['median_skills_after']}  "
          f"useful {was}->{now}")
    return out


def demo():
    assert host_of("https://www.Adzuna.in/land/ad/1") == "adzuna.in"
    assert classify_host("adzuna.in") == "aggregator"
    assert classify_host("boards.greenhouse.io") == "employer_ats"
    assert classify_host("careers.acme.com") == "unknown_direct"
    assert classify_host("in.indeed.com") == "aggregator"
    page = ('<script type="application/ld+json">'
            + json.dumps({"@type": "JobPosting",
                          "description": "<p>" + "React and Node.js. " * 40 + "</p>"})
            + "</script><nav>Python jobs</nav>")
    jd, how, verdict = read_page(page)
    assert how == "jsonld" and verdict == "job_posting", (how, verdict)
    assert "Python" not in jd, "nav text must not leak into the JD"
    assert read_page("<html>please sign in to continue</html>")[2] == "login_wall"
    assert read_page("<html>Are you a robot?</html>")[2] == "blocked_page"
    assert read_page("<html>tiny</html>")[2] == "thin_or_dead"
    assert read_page("<html>" + "word " * 400 + "</html>")[1] == "wholepage"
    print("bench.adzuna_jd_probe: all self-checks pass")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", help="the replay's response cache")
    ap.add_argument("--profile", default="kartik_reachable")
    ap.add_argument("--bucket3", type=int, default=18)
    ap.add_argument("--out")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.demo:
        demo()
        return
    if config.PROFILE != args.profile:
        sys.exit(f"Run with JOB_PROFILE={args.profile}.")

    adz = rows_from_cache(args.cache)
    li = linkedin_keys(args.profile)
    free_co, paid_co = repo_companies(False), repo_companies(True)

    shared = [(k, adz[k]) for k in adz if k in li]
    # Bucket 3, spread across employers: one row per company, best first, so a
    # single prolific employer cannot stand in for the whole discovery set.
    by_company = {}
    for k, r in adz.items():
        ck = scraper._company_key(r["Company"])
        if not ck or ck in free_co or ck in paid_co:
            continue
        if ck not in by_company or r["score"] > by_company[ck][1]["score"]:
            by_company[ck] = (k, r)
    b3 = sorted(by_company.values(), key=lambda kr: -kr[1]["score"])[:args.bucket3]

    print(f"shared postings      {len(shared)}")
    print(f"bucket-3 sample      {len(b3)} rows across {len(b3)} distinct companies")
    print(f"total fetches        {len(shared) + len(b3)} (cap {MAX_FETCHES})\n")
    if len(shared) + len(b3) > MAX_FETCHES:
        sys.exit("refusing: over the fetch cap")

    last_hit, records, streak = {}, [], 0
    for label, rows in (("shared", shared), ("bucket3", b3)):
        for k, r in rows:
            url = r["Job URL"]
            status, final, body, note = fetch(url, last_hit)
            fhost = host_of(final)
            rec = {"set": label, "title": r["Title"], "company": r["Company"],
                   "start_host": host_of(url), "final_host": fhost,
                   "status": status, "category": classify_host(fhost),
                   "note": note, "snippet_score": r["score"],
                   "snippet_skills": n_skills(r.get("matched_skills"))}
            if status in (403, 429) or (body and _BLOCK.search(body)):
                streak += 1
            else:
                streak = 0
            if body:
                jd, how, verdict = read_page(body)
                rec.update(verdict=verdict, extraction=how, jd_chars=len(jd))
                if jd:
                    full = scraper.score_job({**r, "Description": jd})
                    if full is not None:
                        rec["full_score"] = full["score"]
                        rec["full_skills"] = n_skills(full.get("matched_skills"))
            else:
                rec.update(verdict=("http_%s" % status if status else "network"),
                           extraction="none", jd_chars=0)
            records.append(rec)
            print(f"  {label:<8} {str(status or '-'):>4} {rec['category']:<15} "
                  f"{fhost[:30]:<30} {rec.get('verdict',''):<16} "
                  f"jd={rec.get('jd_chars',0):>5} "
                  f"{r['score']:>3}->{rec.get('full_score','-')}")
            if streak >= BLOCK_STREAK:
                print(f"\n  ! {BLOCK_STREAK} blocks in a row — stopping. "
                      f"That is the probe's answer, not an obstacle.")
                break
        if streak >= BLOCK_STREAK:
            break

    print("\n" + "=" * 72)
    for label in ("shared", "bucket3"):
        sub = [r for r in records if r["set"] == label]
        ok = [r for r in sub if "full_score" in r]
        print(f"\n{label}: {len(sub)} fetched, {len(ok)} yielded a JD "
              f"({100*len(ok)/max(1,len(sub)):.0f}%)")
        print("  domains: " + json.dumps(dict(collections.Counter(
            r["category"] for r in sub))))
        print("  verdicts: " + json.dumps(dict(collections.Counter(
            r["verdict"] for r in sub))))
        if ok:
            lens = sorted(r["jd_chars"] for r in ok)
            print(f"  jd chars: min {lens[0]} median {statistics.median(lens)} max {lens[-1]}")
        summarize([(r["snippet_score"], r["full_score"], r["snippet_skills"],
                    r["full_skills"]) for r in ok], f"  {label} lift")

    hosts = collections.Counter(r["final_host"] for r in records)
    print("\nfinal hosts seen more than once:")
    for h, n in hosts.most_common():
        if n > 1:
            print(f"  {n:>3}  {h:<44} {classify_host(h)}")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"records": records,
                       "hosts": dict(hosts)}, fh, indent=2)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
