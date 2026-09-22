"""V2-B2 benchmark: does bounded Lever concurrency actually pay, and is it safe?

AUDIT-ONLY. No Apify, no credits, no production file written. Two arms, kept
apart on purpose because they answer different questions and only one of them
can answer parity:

  --capture   fetch each configured Lever board ONCE over public HTTP and save
              the raw payload plus its measured latency to a scratch directory.
  --replay    run serial / workers=N over that frozen capture, replaying each
              board's recorded latency. Deterministic, so output can be compared
              BYTE FOR BYTE. This is the only arm that may claim parity.
  --live      time serial / 2 / 3 / 4 against the real provider. Answers latency,
              errors and rate limiting. It may NOT claim parity: Lever inventory
              changes between arms, so a row difference here is not evidence of
              a concurrency bug.

    .venv/bin/python -m bench.search_v2_free_concurrency --capture --out /tmp/lever-cap
    .venv/bin/python -m bench.search_v2_free_concurrency --replay  --out /tmp/lever-cap \\
        --json docs/search-v2-evidence/free-concurrency-replay.json
    .venv/bin/python -m bench.search_v2_free_concurrency --live \\
        --json docs/search-v2-evidence/free-concurrency-live.json

Nothing here is imported by production code.
"""
import argparse
import hashlib
import json
import os
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

import telemetry                                    # noqa: E402
from sources import ats, concurrency                # noqa: E402
import sources as sources_pkg                       # noqa: E402


def engine():
    """The unchanged production predicates, without a profile or a résumé."""
    import scraper
    return scraper


def lever_boards():
    import config
    return dict(config.ATS_BOARDS["lever"])


def rss_mb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux kilobytes.
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 2)


# ---------------------------------------------------------------------------
def capture(out_dir):
    """One serial pass, saving each board's raw payload and latency."""
    from sources._http import get_json
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    spec = ats.ATS["lever"]
    manifest = {}
    for token, company in lever_boards().items():
        url = spec["url"].format(token=token)
        started = time.perf_counter()
        try:
            payload = get_json(url)
            elapsed = time.perf_counter() - started
            (out / f"{token}.json").write_text(json.dumps(payload))
            manifest[token] = {"company": company, "seconds": round(elapsed, 4),
                               "items": len(payload or []), "error": None}
        except Exception as exc:
            manifest[token] = {"company": company,
                               "seconds": round(time.perf_counter() - started, 4),
                               "items": 0, "error": f"{type(exc).__name__}: {exc}"}
        print(f"  {token:<24} {manifest[token]['seconds']:>7.3f}s "
              f"{manifest[token]['items']:>5} items "
              f"{manifest[token]['error'] or ''}")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\ncaptured {len(manifest)} boards -> {out}")
    return manifest


def _replay_patch(out_dir, manifest):
    """Make ats.get_json serve the capture, and sleep the recorded latency.

    Replaying the latency is what makes completion order differ from registry
    order, which is the condition the determinism claim has to survive.
    """
    out = Path(out_dir)
    spec_url = ats.ATS["lever"]["url"]
    by_url = {}
    for token, meta in manifest.items():
        path = out / f"{token}.json"
        if path.exists():
            by_url[spec_url.format(token=token)] = (
                json.loads(path.read_text()), meta["seconds"])

    real = ats.get_json

    def fake(url, **kw):
        if url not in by_url:
            raise RuntimeError(f"replay: no capture for {url}")
        payload, seconds = by_url[url]
        time.sleep(seconds)
        return payload

    return real, fake


def one_pass(boards, flag, workers, log=None):
    """One acquisition of the Lever registry, returning rows and wall time."""
    scraper = engine()
    os.environ[concurrency.FLAG] = flag
    if workers is not None:
        os.environ[concurrency.WORKERS_ENV] = str(workers)
    started = time.perf_counter()
    rows = sources_pkg.fetch_free(
        {"lever": boards}, {}, scraper.is_dev_title, scraper.location_allowed,
        scraper.is_home_location, log=log or (lambda *a: None))
    return rows, time.perf_counter() - started


def digest(rows):
    scraper = engine()
    final = scraper.finalize([dict(r) for r in rows])
    blob = json.dumps(final, sort_keys=False, ensure_ascii=False)
    return {
        "acquired_rows": len(rows),
        "source_sequence": [r["Source"] for r in rows],
        "final_rows": len(final),
        "final_sha256": hashlib.sha256(blob.encode()).hexdigest(),
        "final_apply_urls": [r["apply_url"] for r in final],
        "final_scores": [r["score"] for r in final],
    }


def replay(out_dir, arms=(None, 1, 2, 3, 4)):
    manifest = json.loads((Path(out_dir) / "manifest.json").read_text())
    boards = {t: m["company"] for t, m in manifest.items()}
    real, fake = _replay_patch(out_dir, manifest)
    ats.get_json = fake
    results = {}
    try:
        for workers in arms:
            name = "serial" if workers is None else f"workers={workers}"
            flag = "0" if workers is None else "1"
            rows, seconds = one_pass(boards, flag, workers)
            results[name] = dict(digest(rows), seconds=round(seconds, 3),
                                 peak_rss_mb=rss_mb())
            print(f"  {name:<12} {seconds:>7.3f}s  "
                  f"{results[name]['acquired_rows']:>5} rows  "
                  f"final {results[name]['final_rows']}")
    finally:
        ats.get_json = real
        os.environ.pop(concurrency.FLAG, None)
        os.environ.pop(concurrency.WORKERS_ENV, None)

    base = results["serial"]
    parity = {name: {
        "source_sequence_identical": r["source_sequence"] == base["source_sequence"],
        "final_sha256_identical": r["final_sha256"] == base["final_sha256"],
        "final_apply_urls_identical": r["final_apply_urls"] == base["final_apply_urls"],
        "final_scores_identical": r["final_scores"] == base["final_scores"],
    } for name, r in results.items() if name != "serial"}
    return {"mode": "DETERMINISTIC REPLAY of a frozen capture; parity is valid here",
            "boards": len(boards), "arms": results, "parity_vs_serial": parity,
            "all_arms_identical_to_serial":
                all(all(v.values()) for v in parity.values())}


def live(arms=(None, 2, 3, 4)):
    """Latency only. Explicitly NOT a parity measurement."""
    boards = lever_boards()
    results = {}
    for workers in arms:
        name = "serial" if workers is None else f"workers={workers}"
        flag = "0" if workers is None else "1"
        os.environ[telemetry.FLAG] = "1"
        with_tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "v2b2-bench"
        with_tmp.mkdir(parents=True, exist_ok=True)
        telemetry.start("free", str(with_tmp))
        rows, seconds = one_pass(boards, flag, workers)
        record = telemetry.record()
        units = [dict(u) for u in record["units"]]
        telemetry.finish()
        os.environ.pop(telemetry.FLAG, None)
        durations = sorted(u["duration_ms"] for u in units)
        results[name] = {
            "wall_seconds": round(seconds, 3),
            "summed_board_seconds": round(sum(durations) / 1000, 3),
            "boards": len(units),
            "acquired_rows": len(rows),
            "failures": sum(1 for u in units if not u["ok"]),
            "failure_categories": sorted({u["failure_category"]
                                          for u in units if not u["ok"]}),
            "retries": sum(u["retries"] for u in units),
            "median_board_ms": durations[len(durations) // 2] if durations else None,
            "max_board_ms": durations[-1] if durations else None,
            "per_board_ms": {u["board"]: u["duration_ms"] for u in units},
            "raw_rows": sum(u["raw_count"] for u in units),
            "normalized_rows": sum(u["normalized_count"] for u in units),
            "gated_rows": sum(u["source_gate_count"] for u in units),
            "peak_rss_mb": rss_mb(),
        }
        print(f"  {name:<12} wall {seconds:>7.3f}s  "
              f"summed {results[name]['summed_board_seconds']:>7.3f}s  "
              f"rows {results[name]['acquired_rows']:>4}  "
              f"fail {results[name]['failures']}  "
              f"retry {results[name]['retries']}")
    os.environ.pop(concurrency.FLAG, None)
    os.environ.pop(concurrency.WORKERS_ENV, None)
    base = results["serial"]["wall_seconds"]
    for name, r in results.items():
        r["speedup_vs_serial"] = round(base / r["wall_seconds"], 2) \
            if r["wall_seconds"] else None
    return {"mode": "LIVE public Lever HTTP; latency/provider behaviour ONLY. "
                    "Inventory changes between arms, so row-count differences "
                    "here are NOT evidence about determinism — see the replay.",
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "apify_cost_usd": 0, "arms": results}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", default="/tmp/v2b2-lever-capture")
    ap.add_argument("--json")
    args = ap.parse_args()

    if not (args.capture or args.replay or args.live):
        boards = lever_boards()
        print(f"{len(boards)} Lever boards. Arms: serial, 2, 3, 4 workers.")
        print("Public HTTP only, 0 Apify credits. Pass --capture / --replay / --live.")
        return

    out = None
    if args.capture:
        print("capture (one serial pass over public Lever endpoints):")
        capture(args.out)
    if args.replay:
        print("\nreplay (frozen capture, deterministic):")
        out = replay(args.out)
        print(json.dumps({"all_arms_identical_to_serial":
                          out["all_arms_identical_to_serial"],
                          "parity_vs_serial": out["parity_vs_serial"]}, indent=2))
    if args.live:
        print("\nlive (public Lever endpoints):")
        live_out = live()
        out = {"replay": out, "live": live_out} if out else live_out
    if args.json and out is not None:
        Path(args.json).write_text(json.dumps(out, indent=2) + "\n")
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
