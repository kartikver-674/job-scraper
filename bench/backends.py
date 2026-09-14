"""Local-direct vs an HTTP backend through the actual finished Sweep pipeline.

Two model calls per backend/document; observations wrap production functions,
never replay them. Stops on the first semantic difference or failure. Reports
contain personal profile values: keep them locally under ignored output/.
"""

import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
for path in (REPO_ROOT, REPO_ROOT / "auto-apply"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import inference
import local_extract as le
import local_search
from bench.people import PEOPLE
from bench.render import LAYOUTS
from deploy.ollama_probe_lib import EXPECTED_DIGEST_PREFIX, MODEL, OLLAMA_VERSION

RESUMES = HERE / "resumes"
REPRESENTATIVE = ("bhaskar", "ada", "hana")
# Identical preferences on both sides. Rendering is offline; it fetches no jobs.
PREFS = {"locations": ["Remote"], "avoid": [], "exclude_levels": [],
         "min_comp_usd": 0, "max_spend_usd": 0}
MISSING = object()


def documents(people=None, limit=None, layouts=("plain",), paths=None):
    """Never silently skip a requested document or misspell a corpus selector."""
    if paths:
        out = [(Path(p).stem, str(Path(p).resolve())) for p in paths]
    else:
        slugs = list(people) if people is not None else list(PEOPLE)
        if set(slugs) - set(PEOPLE) or set(layouts) - set(LAYOUTS):
            raise ValueError("unknown person or layout")
        out = [(f"{s}-{layout}", str(RESUMES / f"{s}-{layout}.pdf"))
               for s in slugs for layout in layouts]
    if not out or any(not Path(path).is_file() for _, path in out):
        raise ValueError("one or more requested resume files are missing")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    return out[:limit] if limit else out


def read_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as reply:
        return json.load(reply)


def service_up(url=None):
    try:
        body = read_json((url or inference.service_url()).rstrip("/") + "/healthz")
        return body.get("status") == "ok" and body.get("runtime_reachable") is True, body
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, AttributeError):
        return False, {"status": "unreachable or unhealthy"}


def local_identity(model):
    """Fail instead of silently comparing a different build/quantization."""
    version = read_json(inference.host() + "/api/version")["version"]
    models = read_json(inference.host() + "/api/tags")["models"]
    info = next((m for m in models if m.get("name") == model), {})
    digest = info.get("digest", "")
    quant = info.get("details", {}).get("quantization_level")
    if (version != OLLAMA_VERSION or model != MODEL
            or not digest.startswith(EXPECTED_DIGEST_PREFIX) or quant != "Q4_K_M"):
        raise ValueError("local baseline must be Ollama 0.34.0 / qwen3:8b / "
                         "500a1f067a9f… / Q4_K_M; no automatic pull or upgrade")
    return {"ollama_version": version, "model": model, "digest": digest,
            "quantization": quant}


def rendered_config(profile, prefs):
    """Read every production-rendered assignment without exec or disk writes."""
    import make_profile
    module = ast.parse(make_profile.render("backend_benchmark", profile, prefs))
    result = {}
    for node in module.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # explanatory module docstring
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            raise ValueError("renderer emitted a nonliteral statement")
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            raise ValueError("renderer emitted a nonliteral assignment")
        result[target.id] = ast.literal_eval(node.value)
    return result


def observe(text, backend, market, frequencies, model=None, url=None, token=None,
            now=None, prefs=None):
    """Capture the read() actually consumed, finish it, then render in memory.

    Benchmark-only scoped patches freeze the clock and read-only market data;
    they do not substitute extraction, arithmetic or profile implementations.
    """
    import corpus_signal
    import local_profile
    import make_profile

    seen = {}
    actual_read = le.read
    now = now or le.today()
    prefs = copy.deepcopy(PREFS if prefs is None else prefs)

    def recording(*args, **kwargs):
        checked, employment, decision = actual_read(*args, **kwargs)
        seen.update(extracted=copy.deepcopy(checked),
                    employment=copy.deepcopy(employment),
                    decision=copy.deepcopy({k: v for k, v in decision.items()
                                            if k != "result"}))
        return checked, employment, decision

    env = {inference.BACKEND_ENV: backend,
           inference.KEEP_ALIVE_ENV: inference.DEFAULT_KEEP_ALIVE}
    if token is not None:
        env[inference.TOKEN_ENV] = token
    started = time.perf_counter()
    with patch.dict(os.environ, env), patch.object(le, "read", recording), \
            patch.object(le, "today", return_value=now), \
            patch.object(corpus_signal, "frequencies", return_value=frequencies):
        try:
            data = local_profile.generate(text, prefs, model=model, market=market,
                                          backend=backend, url=url, log=lambda *a: None)
            data = make_profile._finish(data, text, None, lambda *a: None)
            seen["profile"] = data
            seen["config"] = rendered_config(data, prefs)
        except local_profile.Escalated as exc:
            # Retain employment evidence when the real router refuses it.
            # An escalated baseline is not an accepted profile or a passing gate.
            seen["failure"] = {"category": "escalated", "reasons": list(exc.reasons)}
    return seen, time.perf_counter() - started


def normalize(value, path=(), now=None):
    """Field-specific normalization. Unknown fields and query order stay exact.

    Keep duplicates, booleans vs numbers, missing vs null, and employment row
    order (held titles use it). No synonym or punctuation folding.
    """
    if isinstance(value, dict):
        return {k: normalize(v, path + (k,), now) for k, v in value.items()}
    if isinstance(value, list):
        items = [normalize(v, path + (i,), now) for i, v in enumerate(value)]
        if path in (("profile", "title_hints"), ("profile", "title_exclude")):
            # make_profile._title_gate consumes these as stripped lowercase sets.
            return sorted({str(v).strip().lower() for v in items})
        if path == ("decision", "corrections") or (len(path) == 2 and path[0] == "extracted" and path[1] in (
                "skills", "titles", "companies", "institutions", "education",
                "projects", "certifications")):
            return sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
        return items
    if (len(path) == 4 and path[:2] == ("employment", "employment")
            and path[-1] in ("start", "end") and isinstance(value, str)):
        parsed = le.parse_month(value, now)
        return {"parsed_month": list(parsed)} if parsed else value
    # Structured corrections, field summary, added skills and reasons remain
    # compared. This duplicated human-readable prose doesn't drive job behavior.
    if path == ("profile", "notes") and isinstance(value, str):
        return "<display notes; structured provenance compared separately>"
    return value


def differences(left, right, path=()):
    """Leaf paths for scalars; lists retain useful order/row evidence."""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(left.keys() | right.keys()):
            if key not in left or key not in right:
                yield path + (key,), left.get(key, MISSING), right.get(key, MISSING)
            else:
                yield from differences(left[key], right[key], path + (key,))
    elif type(left) is not type(right) or json.dumps(left, sort_keys=True) != json.dumps(right, sort_keys=True):
        yield path, left, right


def compare_observations(left, right, now=None):
    diffs = []
    for path, a, b in differences(left, right):
        same = (a is not MISSING and b is not MISSING and not list(
            differences(normalize(a, path, now), normalize(b, path, now))))
        diffs.append({"path": ".".join(map(str, path)),
                      "local": "<MISSING>" if a is MISSING else a,
                      "remote": "<MISSING>" if b is MISSING else b,
                      "classification": "benign formatting" if same else "behavior-changing",
                      "reason": ("display-only notes; structured evidence compared separately"
                                 if path == ("profile", "notes") else
                                 "field-specific normalization" if same else
                                 "semantic evidence or downstream value differs")})
    return {"exact_match": not diffs,
            "semantic_match": all(d["classification"] == "benign formatting" for d in diffs),
            "diffs": diffs}


def run(people=None, limit=None, model=None, log=print, *, layouts=("plain",),
        paths=None, url=None, token=None, label="remote", output_dir=None,
        endpoint_state=None):
    import corpus_signal
    from resume_parser import extract_text

    if label.casefold() == "modal" and not endpoint_state:
        raise ValueError("Modal acceptance requires --endpoint-state from a restored smoke test")
    docs = documents(people, limit, layouts, paths)
    model = inference.model_name(model)
    identity = local_identity(model)
    url = (url or inference.service_url()).rstrip("/")
    if endpoint_state:
        from deploy.run_modal_benchmark import check_identity
        proof = json.loads(Path(endpoint_state).read_text())
        if (not proof.get("smoke_passed") or proof.get("url") != url
                or proof["restored"]["digest"] != identity["digest"]):
            raise ValueError("smoke proof URL/digest does not match this benchmark")
        check_identity(proof["restored"])
    ok, health = service_up(url)
    if not ok or health.get("model") != model or health.get("model_slots") != 1:
        raise ValueError("remote health/model/one-slot check failed; prime and smoke-test first")
    market = local_search.Market(output_dir)
    frequencies = corpus_signal.frequencies(output_dir)
    now = le.today()
    log(f"{len(docs)} document(s); {len(market)} local listings; clock={now}; remote={label}")
    report = {"requested": len(docs), "remote_label": label, "url": url,
              "baseline": identity, "now": now, "market_rows": len(market), "rows": []}
    report["endpoint_proof"] = proof if endpoint_state else None
    report["market_sha256"] = hashlib.sha256(json.dumps(
        {"rows": [(t, s, sorted(sk), c) for t, s, sk, c in market.rows],
         "frequencies": frequencies, "seniority": market.seniority},
        sort_keys=True).encode()).hexdigest()
    for slug, path in docs:
        row = {"resume": slug}
        try:
            text = extract_text(path)
            if not text.strip():
                raise ValueError("resume contains no extracted text")
            row["chars"] = len(text)
            row["text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
            direct, row["local_s"] = observe(text, "local-direct", market, frequencies,
                                              model, now=now)
            remote, row["remote_s"] = observe(text, "remote", market, frequencies,
                                               model, url, token, now)
            row.update(compare_observations(direct, remote, now))
            row["local"], row["remote"] = direct, remote
            row["accepted"] = "failure" not in direct and "failure" not in remote
            log(f"{slug}: exact={row['exact_match']} semantic={row['semantic_match']} "
                f"accepted={row['accepted']} local={row['local_s']:.2f}s {label}={row['remote_s']:.2f}s")
            for d in row["diffs"]:
                log(f"  {slug} | {d['path']} | {d['classification']} ({d['reason']})\n"
                    f"    local: {d['local']!r}\n    {label}: {d['remote']!r}")
            if not row["accepted"]:
                log(f"  extraction refused: local={direct.get('failure')} {label}={remote.get('failure')}")
        except Exception as exc:
            row["error"] = type(exc).__name__  # messages can quote request content
            log(f"{slug}: ERROR {row['error']} (no retry or fallback)")
        report["rows"].append(row)
        if row.get("error") or not row.get("semantic_match") or not row.get("accepted"):
            log("STOP: gate failed; remaining documents were not run.")
            break
    return report


def summarise(report, log=print):
    rows = report["rows"]
    exact = sum(r.get("exact_match", False) and r.get("accepted", False) for r in rows)
    semantic = sum(r.get("semantic_match", False) and r.get("accepted", False) for r in rows)
    log(f"Completed {len(rows)}/{report['requested']}; exact {exact}; semantic {semantic}.")
    return semantic == report["requested"]


def demo():
    import unittest
    suite = unittest.defaultTestLoader.loadTestsFromName("bench.test_backends")
    return unittest.TextTestRunner().run(suite).wasSuccessful()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    select = parser.add_mutually_exclusive_group()
    select.add_argument("--resume", action="append", help="explicit PDF/text path (repeatable)")
    select.add_argument("--people", help="comma-separated existing corpus slugs")
    select.add_argument("--representative", action="store_true", help="bhaskar / ada / hana, plain")
    select.add_argument("--all", action="store_true", help="all 13 people × 4 layouts = 52")
    parser.add_argument("--layouts", default="plain", help="comma-separated layouts, or all")
    parser.add_argument("--limit", type=int, help="default 4 only when no selection is given")
    parser.add_argument("--url", help="temporary remote base URL")
    parser.add_argument("--token", help="bearer token; prefer --token-env to avoid shell history")
    parser.add_argument("--token-env", default="SWEEP_BENCHMARK_TOKEN")
    parser.add_argument("--label", default="modal", help="report label, e.g. modal or oracle")
    parser.add_argument("--endpoint-state", help="required for --label modal: restored proof, URL, digest and sources")
    parser.add_argument("--output-dir", help="existing local scored-job corpus; never fetches jobs")
    parser.add_argument("--json", help="local report path, preferably under ignored output/")
    parser.add_argument("--demo", action="store_true", help="offline self-checks only")
    args = parser.parse_args()
    if args.demo:
        return 0 if demo() else 1
    if args.label.casefold() == "modal" and not args.endpoint_state:
        parser.error("Modal acceptance requires --endpoint-state from a restored smoke test")
    token = args.token or os.environ.get(args.token_env) or os.environ.get(inference.TOKEN_ENV)
    if not token:
        parser.error("bearer token required via --token or --token-env")
    url = args.url or inference.service_url()
    parsed_url = urllib.parse.urlsplit(url)
    if (parsed_url.scheme not in ("http", "https") or not parsed_url.hostname
            or parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment):
        parser.error("URL must be an HTTP(S) base URL without credentials, query or fragment")
    layouts = LAYOUTS if args.all or args.layouts == "all" else tuple(args.layouts.split(","))
    people = REPRESENTATIVE if args.representative else args.people.split(",") if args.people else None
    limit = args.limit
    if not any((args.resume, args.people, args.representative, args.all)) and limit is None:
        limit = 4
    if args.all and limit is not None:
        parser.error("--all cannot be limited: the full gate must request 52 documents")
    try:
        report = run(people, limit, layouts=layouts, paths=args.resume,
                     url=url, token=token, label=args.label, output_dir=args.output_dir,
                     endpoint_state=args.endpoint_state)
    except Exception as exc:
        print(f"Preflight failed: {type(exc).__name__}; check local pinned model and remote health.")
        return 1
    if args.json:
        destination = Path(args.json)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        destination.chmod(0o600)
        print(f"Private local report: {destination}")
    return 0 if summarise(report) else 1


if __name__ == "__main__":
    sys.exit(main())
