"""V2-B2 / V2-B4 benchmark: does bounded concurrency pay for one provider, and is
it safe?

AUDIT-ONLY. No Apify, no credits, no production file written, and no path to
paid execution at all: this drives `sources.fetch_free` for ONE ATS provider and
never the engine's entry point. Three arms, kept apart on purpose because they
answer different questions and only one of them can answer parity:

  --capture   fetch each configured board of the provider ONCE over public HTTP
              and save the raw payload (or the HTTP error) plus its measured
              latency to a scratch directory.
  --replay    run serial / workers=N over that frozen capture, replaying each
              board's recorded latency AND recorded error (a 404 replays as a
              404). Deterministic, so rows, final output and telemetry can be
              compared BYTE FOR BYTE. This is the only arm that may claim parity.
              `--collision` adds a labelled synthetic twin of a real posting
              right after the slowest contributing board, finishing first — a
              duplicate whose completion order is inverted, on real data.
  --live      time serial / N workers against the real provider, each arm in a
              FRESH child process so peak RSS is that arm's own. Answers latency,
              errors and rate limiting. It may NOT claim parity: inventory
              changes between arms.

    .venv/bin/python -m bench.search_v2_free_concurrency --provider greenhouse \\
        --capture --out /tmp/gh-cap
    .venv/bin/python -m bench.search_v2_free_concurrency --provider greenhouse \\
        --replay --collision --out /tmp/gh-cap \\
        --json docs/search-v2-evidence/greenhouse-concurrency-replay.json
    .venv/bin/python -m bench.search_v2_free_concurrency --provider greenhouse \\
        --live --json docs/search-v2-evidence/greenhouse-concurrency-live.json

`--provider lever` (the default) reproduces V2-B2's evidence.
Nothing here is imported by production code.
"""
import argparse
import hashlib
import json
import os
import resource
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

import telemetry                                    # noqa: E402
from sources import ats, concurrency                # noqa: E402
import sources as sources_pkg                       # noqa: E402

# Arms per provider. Lever keeps V2-B2's, so its evidence reproduces. For
# Greenhouse, 8 is only run live if 2, 4 and 6 were clean (see live()).
ARMS = {"lever": {"replay": (None, 1, 2, 3, 4), "live": (None, 2, 3, 4)},
        "greenhouse": {"replay": (None, 1, 2, 4, 6, 8), "live": (None, 2, 4, 6, 8)}}


def engine():
    """The unchanged production predicates, without a profile or a résumé."""
    import scraper
    return scraper


def boards_for(provider):
    import config
    return dict(config.ATS_BOARDS[provider])


def rss_mb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux kilobytes.
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 2)


def _items(provider, payload):
    spec = ats.ATS[provider]
    return (payload.get(spec["list"]) if spec["list"] else payload) or []


# ---------------------------------------------------------------------------
def capture(provider, out_dir):
    """One serial pass, saving each board's raw payload or error, and latency."""
    from sources._http import get_json
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    spec = ats.ATS[provider]
    manifest = {}
    for token, company in boards_for(provider).items():
        url = spec["url"].format(token=token)
        started = time.perf_counter()
        try:
            payload = get_json(url)
            elapsed = time.perf_counter() - started
            (out / f"{token}.json").write_text(json.dumps(payload))
            manifest[token] = {"company": company, "seconds": round(elapsed, 4),
                               "items": len(_items(provider, payload)),
                               "error": None, "status": 200}
        except Exception as exc:
            manifest[token] = {"company": company,
                               "seconds": round(time.perf_counter() - started, 4),
                               "items": 0, "error": f"{type(exc).__name__}: {exc}",
                               "status": getattr(exc, "code", None)}
        print(f"  {token:<24} {manifest[token]['seconds']:>7.3f}s "
              f"{manifest[token]['items']:>5} items "
              f"{manifest[token]['error'] or ''}")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\ncaptured {len(manifest)} boards -> {out}")
    return manifest


def _replay_patch(provider, out_dir, manifest, extra=None):
    """Make ats.get_json serve the capture: the recorded payload, or the
    recorded HTTP error, after the recorded latency. Replaying latency is what
    makes completion order differ from registry order, which is the condition
    the determinism claim has to survive. `extra` adds synthetic boards."""
    out = Path(out_dir)
    spec_url = ats.ATS[provider]["url"]
    by_url = {}
    for token, meta in manifest.items():
        path = out / f"{token}.json"
        body = json.loads(path.read_text()) if path.exists() else None
        by_url[spec_url.format(token=token)] = (token, body, meta)
    for token, (body, meta) in (extra or {}).items():
        by_url[spec_url.format(token=token)] = (token, body, meta)
    done, lock = [], threading.Lock()

    def fake(url, **kw):
        if url not in by_url:
            raise RuntimeError(f"replay: no capture for {url}")
        token, body, meta = by_url[url]
        time.sleep(meta["seconds"])
        with lock:
            done.append(token)
        if body is None:
            raise urllib.error.HTTPError(url, meta.get("status") or 599,
                                         meta.get("error") or "replayed error",
                                         None, None)
        return body

    return ats.get_json, fake, done


def one_pass(provider, boards, flag, workers, log=None):
    """One acquisition of one provider's registry, returning rows and wall time."""
    scraper = engine()
    flag_env, workers_env = concurrency.ENV[provider]
    os.environ[flag_env] = flag
    if workers is not None:
        os.environ[workers_env] = str(workers)
    started = time.perf_counter()
    rows = sources_pkg.fetch_free(
        {provider: boards}, {}, scraper.is_dev_title, scraper.location_allowed,
        scraper.is_home_location, log=log or (lambda *a: None))
    return rows, time.perf_counter() - started


def recorded_pass(provider, boards, flag, workers):
    """one_pass under an open telemetry record: rows, wall, and the units."""
    os.environ[telemetry.FLAG] = "1"
    with tempfile.TemporaryDirectory() as tmp:
        telemetry.start("free", tmp)
        rows, seconds = one_pass(provider, boards, flag, workers)
        units = [dict(u) for u in telemetry.record()["units"]]
        telemetry.finish()
    os.environ.pop(telemetry.FLAG, None)
    return rows, seconds, units


UNIT_FIELDS = ("board", "ok", "failure_category", "requests", "retries",
               "raw_count", "normalized_count", "source_gate_count")


def digest(rows, units):
    scraper = engine()
    final = scraper.finalize([dict(r) for r in rows])
    blob = json.dumps(final, sort_keys=False, ensure_ascii=False)
    return {
        "acquired_rows": len(rows),
        "source_sequence": [r["Source"] for r in rows],
        "acquired_sha256": hashlib.sha256(json.dumps(
            [{k: v for k, v in r.items() if k != "_native"} for r in rows],
            ensure_ascii=False).encode()).hexdigest(),
        "final_rows": len(final),
        "final_sha256": hashlib.sha256(blob.encode()).hexdigest(),
        "final_apply_urls": [r["apply_url"] for r in final],
        "final_scopes": {r["apply_url"]: r["remote_scope"] for r in final},
        "final_scores": [r["score"] for r in final],
        "telemetry_units": [{k: u[k] for k in UNIT_FIELDS} for u in units],
        "summed_board_seconds": round(sum(u["duration_ms"] for u in units) / 1000, 3),
        "failures": sorted(u["board"] for u in units if not u["ok"]),
    }


def _arm_name(workers):
    return "serial" if workers is None else f"workers={workers}"


def _run_arms(provider, boards, arms, out_dir, manifest, extra=None):
    real, fake, done = _replay_patch(provider, out_dir, manifest, extra)
    ats.get_json = fake
    results = {}
    try:
        for workers in arms:
            done.clear()
            flag = "0" if workers is None else "1"
            rows, seconds, units = recorded_pass(provider, boards, flag, workers)
            results[_arm_name(workers)] = dict(
                digest(rows, units), seconds=round(seconds, 3),
                completion_order=list(done))
            r = results[_arm_name(workers)]
            print(f"  {_arm_name(workers):<12} {seconds:>7.3f}s  "
                  f"{r['acquired_rows']:>5} rows  final {r['final_rows']}  "
                  f"failures {r['failures']}")
    finally:
        ats.get_json = real
        for name in concurrency.ENV[provider]:
            os.environ.pop(name, None)
    return results


def _parity(results):
    base = results["serial"]
    keys = ("source_sequence", "acquired_sha256", "final_sha256", "final_scopes",
            "final_apply_urls", "final_scores", "telemetry_units", "failures")
    parity = {name: {f"{k}_identical": r[k] == base[k] for k in keys}
              for name, r in results.items() if name != "serial"}
    return parity, all(all(v.values()) for v in parity.values())


def _twin(provider, out_dir, manifest, results):
    """A synthetic twin of one real posting: the same item, a different apply
    URL, on a board with the same company label, inserted right after the
    slowest board that contributes a final row — and answering in 10 ms, so it
    finishes first. Returns (boards with the twin, extra, board, twin, url)."""
    spec = ats.ATS[provider]
    url_field = spec["map"]["Job URL"]
    # A posting kept as remote/worldwide, so its eligibility cannot hinge on
    # the board-level hires_home signal — which a one-posting twin board could
    # compute differently. The collision is then decided by order alone.
    final_urls = {u for u, scope in results["serial"]["final_scopes"].items()
                  if scope in ("remote", "worldwide")}
    contributing = []
    for token, meta in manifest.items():
        path = Path(out_dir) / f"{token}.json"
        if not path.exists():
            continue
        for item in _items(provider, json.loads(path.read_text())):
            if item.get(url_field) in final_urls:
                contributing.append((meta["seconds"], token, item))
                break
    seconds, board, item = max(contributing, key=lambda c: c[0])
    twin = f"{board}-synthetic-twin"
    twin_item = dict(item, **{url_field: item[url_field] + "#synthetic-twin"})
    body = {spec["list"]: [twin_item]} if spec["list"] else [twin_item]
    boards = {}
    for token, meta in manifest.items():
        boards[token] = meta["company"]
        if token == board:
            boards[twin] = meta["company"]
    return (boards, {twin: (body, {"seconds": 0.01, "company": manifest[board]["company"]})},
            board, twin, item[url_field])


def replay(provider, out_dir, arms, collision=False):
    # Offline by construction: every answer comes from the capture.
    def deny(*a, **kw):
        raise RuntimeError("replay is offline")
    socket.socket.connect = deny
    socket.create_connection = deny

    manifest = json.loads((Path(out_dir) / "manifest.json").read_text())
    boards = {t: m["company"] for t, m in manifest.items()}
    print(f"replay: {len(boards)} {provider} boards, frozen capture")
    results = _run_arms(provider, boards, arms, out_dir, manifest)
    parity, identical = _parity(results)
    out = {"mode": "DETERMINISTIC REPLAY of a frozen capture; parity is valid here",
           "provider": provider, "boards": len(boards),
           "captured_failures": {t: m["error"] for t, m in manifest.items()
                                 if m["error"]},
           "slowest_captured_boards": sorted(
               ((m["seconds"], t) for t, m in manifest.items()), reverse=True)[:5],
           "summed_captured_seconds": round(sum(m["seconds"] for m in manifest.values()), 3),
           "arms": results, "parity_vs_serial": parity,
           "all_arms_identical_to_serial": identical,
           "process_peak_rss_mb": rss_mb(),
           "rss_note": "one process with the whole capture loaded; replay RSS "
                       "is not a per-arm memory measurement — see --live"}

    if collision:
        cboards, extra, board, twin, url = _twin(provider, out_dir, manifest, results)
        print(f"\ncollision arm: synthetic twin of {board} inserted after it")
        cres = _run_arms(provider, cboards, arms, out_dir, manifest, extra)
        cparity, cidentical = _parity(cres)
        survivors = {}
        for name, r in cres.items():
            survivors[name] = {
                "earlier_board_row_survives": url in r["final_apply_urls"],
                "twin_row_survives": url + "#synthetic-twin" in r["final_apply_urls"],
                "twin_finished_first": (r["completion_order"].index(twin)
                                        < r["completion_order"].index(board)),
            }
        out["collision"] = {
            "synthetic": True,
            "what": "the twin is NOT a production board; it copies one real "
                    "posting of the board it follows, with a different apply "
                    "URL, and answers in 10 ms",
            "earlier_board": board, "twin_board": twin,
            "arms": {n: {k: r[k] for k in ("seconds", "final_rows", "final_sha256",
                                           "failures")} for n, r in cres.items()},
            "survivor": survivors,
            "parity_vs_serial": cparity,
            "all_arms_identical_to_serial": cidentical,
            # The twin really was one posting to dedupe: the result has no
            # extra row with it than without it.
            "twin_deduped_against_the_earlier_board":
                cres["serial"]["final_rows"] == results["serial"]["final_rows"],
            "earlier_board_wins_in_every_arm": all(
                s["earlier_board_row_survives"] and not s["twin_row_survives"]
                for s in survivors.values()),
        }
    return out


# ---------------------------------------------------------------------------
def live_arm(provider, workers):
    """One live arm, run in its own process: latency, errors, rows, RSS."""
    boards = boards_for(provider)
    rss_before = rss_mb()
    flag = "0" if workers is None else "1"
    rows, seconds, units = recorded_pass(provider, boards, flag, workers)
    durations = sorted(u["duration_ms"] for u in units)
    p95 = durations[max(0, -(-len(durations) * 95 // 100) - 1)] if durations else None
    return {
        "wall_seconds": round(seconds, 3),
        "summed_board_seconds": round(sum(durations) / 1000, 3),
        "boards": len(units),
        "acquired_rows": len(rows),
        "failures": sorted(u["board"] for u in units if not u["ok"]),
        "failure_categories": dict(Counter(u["failure_category"]
                                           for u in units if not u["ok"])),
        "http_429": sum(1 for u in units if u["failure_category"] == "http_429"),
        "retries": sum(u["retries"] for u in units),
        "median_board_ms": durations[len(durations) // 2] if durations else None,
        "p95_board_ms": p95,
        "max_board_ms": durations[-1] if durations else None,
        "per_board_ms": {u["board"]: u["duration_ms"] for u in units},
        "raw_rows": sum(u["raw_count"] for u in units),
        "normalized_rows": sum(u["normalized_count"] for u in units),
        "gated_rows": sum(u["source_gate_count"] for u in units),
        "peak_rss_mb_after_imports": rss_before,
        "peak_rss_mb": rss_mb(),
    }


def live(provider, arms):
    """Latency only, explicitly NOT a parity measurement. Each arm is a fresh
    child process running live_arm, so memory is not a running maximum."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("APIFY_TOKEN", "SWEEP_"))}
    results = {}
    for workers in arms:
        name = _arm_name(workers)
        if workers == 8 and provider == "greenhouse" and not _clean(results):
            results[name] = {"skipped": "an earlier arm was not clean (new "
                                        "failure, retry or 429)"}
            print(f"  {name:<12} skipped: earlier arm not clean")
            continue
        child = subprocess.run(
            [sys.executable, "-m", "bench.search_v2_free_concurrency",
             "--provider", provider, "--live-arm", str(workers or 0)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
        if child.returncode != 0:
            raise SystemExit(f"{name} failed:\n{child.stderr[-2000:]}")
        results[name] = r = json.loads(child.stdout.strip().splitlines()[-1])
        print(f"  {name:<12} wall {r['wall_seconds']:>7.3f}s  "
              f"summed {r['summed_board_seconds']:>7.3f}s  "
              f"median {r['median_board_ms']} ms  p95 {r['p95_board_ms']} ms  "
              f"fail {r['failures']}  retry {r['retries']}  "
              f"rss {r['peak_rss_mb']} MB")
    base = results["serial"]["wall_seconds"]
    for r in results.values():
        if "wall_seconds" in r:
            r["speedup_vs_serial"] = round(base / r["wall_seconds"], 2)
            r["seconds_saved_vs_serial"] = round(base - r["wall_seconds"], 3)
    return {"mode": f"LIVE public {provider} HTTP; latency/provider behaviour "
                    f"ONLY. Inventory changes between arms, so row-count "
                    f"differences here are NOT evidence about determinism — "
                    f"see the replay.",
            "provider": provider,
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "apify_cost_usd": 0, "arms": results}


def _clean(results):
    """Every concurrent arm so far failed only where serial did, retried
    nothing, and saw no 429."""
    base = set(results.get("serial", {}).get("failures", []))
    return all(set(r.get("failures", [])) <= base and not r.get("retries")
               and not r.get("http_429") for r in results.values()
               if "wall_seconds" in r)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=sorted(concurrency.ENV), default="lever")
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--collision", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--live-arm", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json")
    args = ap.parse_args()
    for key in [k for k in os.environ if k.startswith("APIFY_TOKEN")]:
        del os.environ[key]
    out_dir = args.out or f"/tmp/v2-{args.provider}-capture"

    if args.live_arm is not None:
        print(json.dumps(live_arm(args.provider, args.live_arm or None)))
        return
    if not (args.capture or args.replay or args.live):
        print(f"{len(boards_for(args.provider))} {args.provider} boards. "
              f"Public HTTP only, 0 Apify credits. "
              f"Pass --capture / --replay / --live.")
        return

    out = {}
    if args.capture:
        print(f"capture (one serial pass over public {args.provider} endpoints):")
        capture(args.provider, out_dir)
    if args.live:
        print(f"\nlive (public {args.provider} endpoints, one process per arm):")
        out["live"] = live(args.provider, ARMS[args.provider]["live"])
    if args.replay:
        print("\nreplay (frozen capture, deterministic):")
        out["replay"] = replay(args.provider, out_dir,
                               ARMS[args.provider]["replay"], args.collision)
        print(json.dumps({"all_arms_identical_to_serial":
                          out["replay"]["all_arms_identical_to_serial"],
                          "collision": {k: out["replay"]["collision"][k] for k in (
                              "earlier_board_wins_in_every_arm",
                              "all_arms_identical_to_serial")}
                          if args.collision else None}, indent=2))
    if args.json and out:
        body = out["live"] if list(out) == ["live"] else (
            out["replay"] if list(out) == ["replay"] else out)
        Path(args.json).write_text(json.dumps(body, indent=2) + "\n")
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
