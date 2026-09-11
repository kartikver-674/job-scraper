"""Same résumés, same corpus, two backends. Are the profiles the same?

The regression this exists to prevent: moving the model call out of
Sweep's process quietly changing what Sweep searches for. The claim being
tested is narrow and total — `local-direct` and `remote` differ in WHERE
the model runs and in nothing else, so for the same document they must
produce the same profile, field for field.

They should be IDENTICAL rather than merely close. Both backends send the
same prompt, the same JSON Schema and the same num_ctx to the same Ollama
at temperature 0, and everything after the model — grounding, the router,
the arithmetic, local_search's ranking — is the same code called in the
same order. Anything less than equality is a finding, not a tolerance.

The corpus is built ONCE and handed to both runs, so a sweep finishing
mid-benchmark cannot show up as a backend difference.

    python -m inference_service                    # in another shell
    python -m bench.backends                       # 4 people, both backends
    python -m bench.backends --limit 8
    python -m bench.backends --people ada,hana,kwame
    python -m bench.backends --demo                # self-check, no model
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

import inference
import local_search
from bench.people import PEOPLE

RESUMES = os.path.join(HERE, "resumes")

# The layout to measure. One per person rather than all four: the question
# here is backend equivalence, not layout robustness, and bench/run.py
# already answers the second one.
LAYOUT = "plain"

# What downstream actually reads. make_profile.RESPONSE_SCHEMA's required
# keys are the contract render() writes into a profile module, and the
# scraper imports that module — a difference in any of these is a
# difference in which jobs get scraped and how they score.
def downstream_fields():
    import make_profile
    return tuple(make_profile.RESPONSE_SCHEMA["required"])


# Provenance the local engine adds. Not read by render(), but derived
# deterministically from the model's answer, so a difference here is an
# early warning that the model answers differ even when the profile does
# not.
PROVENANCE = ("local_decision", "local_ranking", "local_from_orphans",
              "experience_months")


def documents(people=None, limit=None):
    """(slug, path) for the benchmark documents to run."""
    slugs = people or list(PEOPLE)
    out = [(s, os.path.join(RESUMES, f"{s}-{LAYOUT}.pdf")) for s in slugs]
    out = [d for d in out if os.path.exists(d[1])]
    return out[:limit] if limit else out


def service_up(url=None):
    """Is the inference service answering? Returns (ok, detail)."""
    url = (url or inference.service_url()) + "/healthz"
    try:
        with urllib.request.urlopen(url, timeout=5) as reply:
            return True, json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        with exc:
            try:
                return False, json.loads(exc.read())
            except ValueError:
                return False, {"status": f"HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, {"status": f"unreachable ({type(exc).__name__})"}


def profile_for(text, backend, market, model=None):
    """One profile through one backend. Returns (profile, seconds)."""
    import local_profile

    started = time.time()
    got = local_profile.generate(text, {"avoid": []}, model=model,
                                 market=market, backend=backend,
                                 log=lambda *a: None)
    return got, time.time() - started


def compare(left, right, fields):
    """Field names that differ, with both values."""
    out = []
    for field in fields:
        a, b = left.get(field), right.get(field)
        if a != b:
            out.append((field, a, b))
    return out


def run(people=None, limit=None, model=None, log=print):
    """Every document through both backends. Returns the rows."""
    from resume_parser import extract_text

    fields = downstream_fields()
    docs = documents(people, limit)
    if not docs:
        raise SystemExit("no benchmark documents found in bench/resumes")

    ok, health = service_up()
    if not ok:
        raise SystemExit(
            f"the inference service is not answering ({health.get('status')})"
            f"\n  start it with:  SWEEP_INFERENCE_TOKEN=... "
            f"python -m inference_service")
    log(f"service: {health.get('status')} "
        f"(runtime_reachable={health.get('runtime_reachable')}, "
        f"model={health.get('model')})")

    # Built once, handed to both. A sweep landing mid-run would otherwise
    # look exactly like a backend difference.
    market = local_search.Market()
    log(f"corpus: {len(market)} scored listing(s), read once and shared\n")

    rows = []
    for slug, path in docs:
        text = extract_text(path)
        row = {"slug": slug, "chars": len(text)}
        try:
            direct, row["direct_s"] = profile_for(text, "local-direct",
                                                  market, model)
            remote, row["remote_s"] = profile_for(text, "remote", market,
                                                  model)
        except Exception as exc:                       # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            log(f"{slug:10} ERROR  {row['error']}")
            continue
        row["diffs"] = compare(direct, remote, fields)
        row["provenance_diffs"] = compare(direct, remote, PROVENANCE)
        row["years"] = direct["years_experience"]
        row["keywords"] = len(direct["role_keywords"])
        verdict = "SAME" if not row["diffs"] else f"{len(row['diffs'])} DIFF"
        log(f"{slug:10} {row['direct_s']:6.1f}s direct  "
            f"{row['remote_s']:6.1f}s remote   {verdict:8} "
            f"years={row['years']} keywords={row['keywords']}")
        for field, a, b in row["diffs"] + row["provenance_diffs"]:
            log(f"           {field}:\n             direct {a!r}\n"
                f"             remote {b!r}")
        rows.append(row)
    return rows


def summarise(rows, log=print):
    """The headline. Returns True when every profile matched."""
    done = [r for r in rows if "error" not in r]
    failed = [r for r in rows if "error" in r]
    differing = [r for r in done if r["diffs"] or r["provenance_diffs"]]

    log("\n" + "=" * 66)
    log(f"{len(done)}/{len(rows)} document(s) completed on both backends")
    if done:
        direct = [r["direct_s"] for r in done]
        remote = [r["remote_s"] for r in done]
        log(f"  local-direct  median {statistics.median(direct):6.1f}s   "
            f"min {min(direct):5.1f}s  max {max(direct):5.1f}s")
        log(f"  remote        median {statistics.median(remote):6.1f}s   "
            f"min {min(remote):5.1f}s  max {max(remote):5.1f}s")
        overhead = statistics.median(remote) - statistics.median(direct)
        log(f"  HTTP hop      {overhead:+.2f}s median "
            f"({overhead / statistics.median(direct) * 100:+.1f}%), "
            f"for 2 model calls per document")
    for row in failed:
        log(f"  FAILED {row['slug']}: {row['error']}")
    if differing:
        log(f"\n  {len(differing)} document(s) DIFFERED between backends:")
        for row in differing:
            log(f"    {row['slug']}: "
                f"{', '.join(f for f, _a, _b in row['diffs'] + row['provenance_diffs'])}")
    else:
        log("\n  every field identical on both backends")
    log("=" * 66)
    return bool(done) and not differing and not failed


def demo():
    """Self-check: the comparison logic, with no model and no service."""
    fields = ("candidate_name", "years_experience", "role_keywords")
    same = {"candidate_name": "Ada", "years_experience": 5,
            "role_keywords": ["backend engineer"]}
    assert compare(same, dict(same), fields) == []

    # Order matters in a keyword list: it is the search order.
    reordered = dict(same, role_keywords=["engineer backend"])
    assert [f for f, _a, _b in compare(same, reordered, fields)] == [
        "role_keywords"]

    # A field missing on one side is a difference, not a skip.
    assert compare(same, {"candidate_name": "Ada"}, fields)

    # Fields outside the list are not compared — provenance is reported
    # separately so it cannot mask or fake a downstream difference.
    assert compare(same, dict(same, local_decision="corrected"), fields) == []

    rows = [{"slug": "ada", "direct_s": 10.0, "remote_s": 11.0, "diffs": [],
             "provenance_diffs": []},
            {"slug": "hana", "direct_s": 20.0, "remote_s": 21.0, "diffs": [],
             "provenance_diffs": []}]
    said = []
    assert summarise(rows, said.append) is True
    assert "every field identical" in " ".join(said)

    said = []
    rows[1]["diffs"] = [("years_experience", 5, 6)]
    assert summarise(rows, said.append) is False
    assert "DIFFERED" in " ".join(said)

    # A document that failed on either backend is never a pass.
    said = []
    assert summarise([{"slug": "x", "error": "boom"}], said.append) is False

    assert documents(limit=2) and len(documents(limit=2)) == 2
    assert all(s in PEOPLE for s, _p in documents())

    print("bench.backends demo ok")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=4,
                        help="how many documents (default 4)")
    parser.add_argument("--people", help="comma-separated slugs")
    parser.add_argument("--model", help="override OLLAMA_MODEL")
    parser.add_argument("--json", help="write the rows here")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.demo:
        return demo()

    people = [p.strip() for p in args.people.split(",")] if args.people else None
    rows = run(people, None if args.people else args.limit, args.model)
    ok = summarise(rows)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, default=str)
        print(f"wrote {args.json}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
