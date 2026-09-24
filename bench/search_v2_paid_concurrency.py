"""V2-C2 offline benchmark: the paid phase through the reservation scheduler at
1-4 workers, against the serial loop — the same plan, rows and provider timing.

OFFLINE. No client, no token, no network: each arm is a fresh child process
running scraper.main() against V2-C2's thread-safe scripted Apify stand-in
(sweep/tests/test_paid_concurrency.py, KeyedClient), with the credential step
patched and sockets denied — the harness the C2 tests use.

    .venv/bin/python -m bench.search_v2_paid_concurrency \\
        --output docs/search-v2-evidence/c2-paid-concurrency-benchmark.json

Plans
  c1        V2-C1's 90-search replay (bench/search_v2_paid_overhead.py): 9
            keywords x 5 places x LinkedIn and Indeed, 15 synthetic rows each.
            Until V2-C3.5 Indeed had no provider ceiling, so under C2 its 45 ran
            one at a time; the oldN arms below reproduce that.
  linkedin  the same plan's 45 LinkedIn searches alone: the part C2 may overlap.

Waits
  none      the provider answers at once: local work only, the denominator V2-C1
            §20 measured (~225 s of CPU, almost all of it checkpoint re-scoring).
  scaled    each run lasts one of V2-C1's measured runtimes (16, 22, 34 or 40 s,
            fixed per search by a seeded draw), divided by --scale, polled every
            5/--scale s; start 0.6 s and dataset 0.9 s, scaled alike. CPU is not
            scaled — so local work weighs --scale times more against the wait
            here than in a real sweep. Label every scaled figure accordingly.

V2-C3.5 (Indeed provider-bounded, so C2 overlaps it too) adds
  default   the repository default's shape: 9 keywords x {India, Remote} on
            LinkedIn (18) and x the eight default places on Indeed (72).
  oldN      an arm run as C2 was before C3.5: Indeed's charge model removed,
            so its searches run one at a time, unreserved. N is the workers.
  --indeed-runtimes S,S,..  Indeed's per-run seconds for the scaled waits
            (e.g. the C3.5 canary's); LinkedIn's distribution when not given,
            which is then synthetic for Indeed — say so.
  --public-cap  instead of sweeping: the public app's spend cap against every
            provider ceiling for the plans V2-C3 audited, and the starts C2
            admits under it. Arithmetic through the real functions; no sweep.
"""
import argparse
import hashlib
import json
import math
import random
import resource
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

RUNTIMES_S = (16, 22, 34, 40)          # V2-C1 §9/§19: 15.9-39.6 s measured
DEFAULT_LINKEDIN_PLACES = ("India", "Remote")      # config.SITES["linkedin"]
START_S, DATASET_S, POLL_S = 0.6, 0.9, 5.0
# A laptop's speed is not constant: the first run of this benchmark lost its
# second half to host contention (CPU time for identical work grew ~2.8x as
# other apps took the performance cores). Every arm therefore times a fixed
# scoring workload before and after its sweep, and an arm measured more than
# SLOW x slower than the reference is rerun, up to RETRIES times; every
# attempt stays in the evidence.
SLOW, RETRIES, CALIBRATION_ROWS = 1.25, 2, 300


def prefer_performance_cores():
    """macOS only: ask for user-interactive QoS, so a busy desktop cannot park
    the measurement on efficiency cores. Measured before adopting it: the same
    scoring workload read 3.6-14.9 ms/row at default QoS and 3.57-3.68 at
    user-interactive. It tunes the instrument, not the code measured, and does
    nothing elsewhere."""
    if sys.platform != "darwin":
        return False
    import ctypes
    try:
        return ctypes.CDLL("/usr/lib/libSystem.dylib").pthread_set_qos_class_self_np(
            0x21, 0) == 0                      # QOS_CLASS_USER_INTERACTIVE
    except (OSError, AttributeError):
        return False


def calibration(scraper, rows):
    """score_job's wall and CPU ms per row over fixed rows: the host, now."""
    import copy
    work = copy.deepcopy(rows)
    wall, cpu = time.perf_counter(), time.thread_time()
    for row in work:
        scraper.score_job(row)
    return {"wall_ms_per_row": round((time.perf_counter() - wall) * 1000 / len(work), 4),
            "cpu_ms_per_row": round((time.thread_time() - cpu) * 1000 / len(work), 4)}


def calibration_rows(scraper, overhead, c1):
    return [scraper.normalize(item, "linkedin")
            for s in overhead.scripts(c1) for item in s.items][:CALIBRATION_ROWS]


def timed(module, name, box):
    """Wrap module.name to add its wall and coordinator-thread CPU to `box`."""
    real = getattr(module, name)

    def wrapper(*a, **kw):
        wall, cpu = time.perf_counter(), time.thread_time()
        try:
            return real(*a, **kw)
        finally:
            box["calls"] += 1
            box["cpu_s"] += time.thread_time() - cpu
            box["wall_s"] += time.perf_counter() - wall
    return wrapper


def modelled_wait_s(scripts, n_linkedin, workers, indeed_bounded):
    """The pure wait schedule: each provider's searches in plan order, greedy
    over its width — 1 when serial or unbounded — LinkedIn's segment first,
    as paid_phase_c2 runs them. No CPU, no account reads."""
    def span(durations, width):
        free = [0.0] * width
        for d in durations:
            free[free.index(min(free))] += d
        return max(free, default=0.0)
    d = [s.delay.get("start", 0) + s.polls * s.delay.get("get", 0)
         + s.delay.get("dataset", 0) for s in scripts]
    w = workers or 1
    return round(span(d[:n_linkedin], w)
                 + span(d[n_linkedin:], w if indeed_bounded else 1), 3)


def arm(mode, plan, wait, scale, indeed_runtimes=RUNTIMES_S):
    """One sweep in this process; prints its measurements as JSON. mode
    "calibrate" only times the host; "oldN" is C2 at N workers with Indeed
    unbounded, as before V2-C3.5."""
    import socket

    def deny(*a, **kw):
        raise RuntimeError("offline benchmark: network forbidden")
    socket.socket.connect = deny
    socket.create_connection = deny
    qos = prefer_performance_cores()
    from unittest import mock

    import scraper
    from bench import search_v2_paid_overhead as overhead
    from sweep.tests import test_paid_concurrency as c2
    from sweep.tests import test_paid_observability as c1

    probe_rows = calibration_rows(scraper, overhead, c1)
    before = calibration(scraper, probe_rows)
    if mode == "calibrate":
        print(json.dumps({"arm": mode, "calibration_before": before,
                          "user_interactive_qos": qos}))
        return
    scripts, sites, places = overhead.scripts(c1), ("linkedin", "indeed"), None
    n_linkedin = len(scripts) // 2              # c1: 45 LinkedIn, then 45 Indeed
    if plan == "linkedin":
        scripts, sites = scripts[:n_linkedin], ("linkedin",)
    elif plan == "default":
        import config
        places = {"linkedin": list(DEFAULT_LINKEDIN_PLACES),
                  "indeed": list(config.SEARCH["locations"])}
        n_linkedin = len(overhead.KEYWORDS) * len(DEFAULT_LINKEDIN_PLACES)
    if wait == "scaled":
        draw = random.Random(7)
        for i, script in enumerate(scripts):
            runtime = draw.choice(RUNTIMES_S if i < n_linkedin else indeed_runtimes)
            script.polls = math.ceil(runtime / POLL_S)
            script.run_time = float(runtime)
            script.delay = {"start": START_S / scale, "get": POLL_S / scale,
                            "dataset": DATASET_S / scale}
    finalize = {"calls": 0, "cpu_s": 0.0, "wall_s": 0.0}
    write = {"calls": 0, "cpu_s": 0.0, "wall_s": 0.0}
    account = [round(i * 0.03, 5) for i in range(len(scripts) + 1)]
    old = mode.startswith("old")
    workers = c2.SERIAL if mode == "serial" else int(mode.removeprefix("old"))
    patches = [mock.patch.object(scraper, "finalize", timed(scraper, "finalize", finalize)),
               mock.patch.object(scraper, "write_outputs",
                                 timed(scraper, "write_outputs", write))]
    if old:
        patches.append(mock.patch.object(scraper, "ACTOR_CHARGE_MODEL", {
            k: v for k, v in scraper.ACTOR_CHARGE_MODEL.items() if k != "indeed"}))
    wall, cpu = time.perf_counter(), time.process_time()
    got = c2.c2_sweep(
        scripts, workers=workers, keywords=overhead.KEYWORDS, locations=overhead.PLACES,
        site_locations=places, sites=sites, account=account, patches=patches)
    wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
    after = calibration(scraper, probe_rows)
    record = got.telemetry or {}
    ex = record.get("paid_execution") or {}
    kinds = [c[0] for c in got.client.calls]
    print(json.dumps({
        "arm": mode, "plan": plan, "wait": wait, "scale": scale if wait == "scaled" else None,
        "wall_s": round(wall, 3), "cpu_s": round(cpu, 3),
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        // (1024 if sys.platform == "darwin" else 1),
        "paid_phase_s": round((record.get("paid_summary") or {})
                              .get("paid_phase_ms", 0) / 1000, 3),
        "starts": kinds.count("start"), "polls": kinds.count("get"),
        "account_reads": kinds.count("limits"),
        "final_rows": len(json.loads(got.json)) if got.json else None,
        "outputs_sha256": hashlib.sha256((got.csv or b"") + (got.json or b"")
                                         + (got.seen or b"")).hexdigest(),
        "done_ledger_sha256": hashlib.sha256("\n".join(got.done).encode()).hexdigest(),
        "finalize": {k: round(v, 3) for k, v in finalize.items()},
        "write_outputs": {k: round(v, 3) for k, v in write.items()},
        "peak_in_flight": {"all": got.client.peak["*"],
                           "linkedin": got.client.peak[c2.LINKEDIN],
                           "indeed": got.client.peak[c2.INDEED]},
        "peak_buffered": ex.get("peak_buffered"),
        "buffered_wait_ms": ex.get("buffered_wait_ms"),
        "committed_usd": (ex.get("exposure") or {}).get("committed_usd"),
        "pending_peak_usd": (ex.get("exposure") or {}).get("pending_peak_usd"),
        "checkpoint": ex.get("checkpoint"),
        "segments": [{k: s[k] for k in ("provider", "bounded", "workers", "wall_ms")}
                     for s in ex.get("segments") or []],
        "modelled_wait_s": (modelled_wait_s(scripts, n_linkedin, workers, not old)
                            if wait == "scaled" else None),
        "record_bytes": len(got.telemetry_text or ""),
        "calibration_before": before, "calibration_after": after,
        "user_interactive_qos": qos,
    }))


def public_cap():
    """V2-C3.5: the public app's spend cap (sweep.app.spend_cap_for — 1.25 x
    the estimate, floored at $0.50) against every provider ceiling, for the
    plans V2-C3 audited, and the starts C2 admits under it in plan order. A
    bounded start must fit its full ceiling (PaidExposure.reserve); an
    unbounded one is admitted while the view is under the cap and then adds its
    ESTIMATED cost — optimistic, since a real reading lags and admits more. The
    serial loop stops once the estimated spend reaches the cap. Arithmetic
    through the real functions: no sweep, no client, no network."""
    from collections import Counter
    from decimal import Decimal

    import config
    import scraper
    from bench import search_v2_paid_compaction as c3
    from sweep.app import SPEND_CAP_HEADROOM, spend_cap_for
    from sweep.plan import cost
    c3.deny_network()
    rows = []
    for name, case in c3.cases():
        with c3.lowered(scraper, config, case):
            units = c3.current_plan(scraper)
        raw = {"profile": name, "sites": {}, "max_results": {}}
        for u in units:
            raw["sites"].setdefault(u["site"], []).append(u)
            raw["max_results"][u["site"]] = u["depth"]
        costed = cost(raw, config.SITE_RATES, config.SITE_RATE_BASIS)
        rate = {line["site"]: line["rate"] for line in costed["lines"]}
        cap = Decimal(str(spend_cap_for(costed["total"])))

        def admitted(indeed_bounded):
            exposure, n = scraper.PaidExposure(cap), Counter()
            for u in units:
                if u["ceiling"] is not None and (indeed_bounded or u["site"] != "indeed"):
                    if not exposure.reserve(u["index"], u["ceiling"]):
                        break
                    exposure.commit(u["index"])
                else:
                    if not exposure.admit(u["index"]):
                        break
                    exposure.observe(0, unbounded_delta=rate[u["site"]])
                n[u["site"]] += 1
            return dict(n)
        spent, serial = 0.0, Counter()
        for u in units:
            if spent >= float(cap):
                break
            spent += rate[u["site"]]
            serial[u["site"]] += 1
        ceilings = {s: us[0]["ceiling"] for s, us in raw["sites"].items()}
        every = sum((u["ceiling"] for u in units if u["ceiling"] is not None), Decimal(0))
        rows.append({
            "plan": name, "estimate_usd": costed["total"], "cap_usd": str(cap),
            "searches": {s: len(us) for s, us in raw["sites"].items()},
            "estimate_per_search_usd": {s: round(r, 5) for s, r in rate.items()},
            "cap_allowance_per_search_usd": {s: round(r * SPEND_CAP_HEADROOM, 5)
                                             for s, r in rate.items()},
            "ceiling_per_search_usd": {s: None if c is None else str(c)
                                       for s, c in ceilings.items()},
            "all_ceilings_usd": str(every),
            "unbounded_searches": sum(1 for u in units if u["ceiling"] is None),
            "cap_holds_every_ceiling": every <= cap,
            "admitted_c2_indeed_unbounded_as_before": admitted(False),
            "admitted_c2_indeed_bounded_c35": admitted(True),
            "admitted_serial_flag_off": dict(serial)})
    return {"status": "DERIVED OFFLINE through the real spend_cap_for, plan.cost, "
                      "max_charge_usd and PaidExposure; arithmetic, not a sweep",
            "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "code_revision": revision(), "plans": rows}


def revision():
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    return head + ("-dirty" if dirty else "")


def child(args):
    done = subprocess.run([sys.executable, "-m", "bench.search_v2_paid_concurrency", *args],
                          cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(done.stdout.strip().splitlines()[-1])


def slow(run, best):
    """Measured on a slowed host: the fixed workload, before or after the
    sweep, ran more than SLOW x the best CPU ms/row seen in the whole run."""
    return any(run[k]["cpu_ms_per_row"] > SLOW * best
               for k in ("calibration_before", "calibration_after"))


def row_equivalents(run):
    """CPU seconds in units of one score_job call on this host at that moment
    — a figure that survives a host that is fast in one arm and slow in the
    next, because the unit is measured beside it."""
    per_row = (run["calibration_before"]["cpu_ms_per_row"]
               + run["calibration_after"]["cpu_ms_per_row"]) / 2
    return {"process": round(run["cpu_s"] * 1000 / per_row),
            "finalize": round(run["finalize"]["cpu_s"] * 1000 / per_row)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm")
    ap.add_argument("--plan", default="c1")
    ap.add_argument("--wait", default="none")
    ap.add_argument("--scale", type=float, default=10.0)
    ap.add_argument("--plans", default="c1,linkedin")
    ap.add_argument("--waits", default="none,scaled")
    ap.add_argument("--arms", default="serial,1,2,3,4")
    ap.add_argument("--repeats-none", type=int, default=2)
    ap.add_argument("--repeats-scaled", type=int, default=1)
    ap.add_argument("--repeats-scaled-linkedin", type=int, default=2)
    ap.add_argument("--indeed-runtimes",
                    help="V2-C3.5: Indeed's per-run seconds, comma-separated")
    ap.add_argument("--indeed-runtimes-source", default="synthetic: LinkedIn's")
    ap.add_argument("--public-cap", action="store_true")
    ap.add_argument("--output")
    args = ap.parse_args()
    indeed_runtimes = (tuple(float(s) for s in args.indeed_runtimes.split(","))
                       if args.indeed_runtimes else RUNTIMES_S)
    if args.arm:
        return arm(args.arm, args.plan, args.wait, args.scale, indeed_runtimes)
    if args.public_cap:
        out = public_cap()
        if args.output:
            Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
        print(json.dumps(out["plans"], indent=1))
        return

    arms = args.arms.split(",")
    seen = [child(["--arm", "calibrate"])["calibration_before"] for _ in range(3)]

    def run_arm(a, plan, wait):
        run = child(["--arm", a, "--plan", plan, "--wait", wait,
                     "--scale", str(args.scale),
                     *(["--indeed-runtimes", args.indeed_runtimes]
                       if args.indeed_runtimes else [])])
        seen.extend([run["calibration_before"], run["calibration_after"]])
        print(f"{plan:<8} {wait:<6} {a:<6} {run['wall_s']:>8.2f}s wall "
              f"{run['cpu_s']:>8.2f}s cpu  host {run['calibration_before']['cpu_ms_per_row']}"
              f"/{run['calibration_after']['cpu_ms_per_row']} ms/row", flush=True)
        return run

    groups = []
    for plan in args.plans.split(","):
        for wait in args.waits.split(","):
            repeats = (args.repeats_none if wait == "none" else
                       args.repeats_scaled_linkedin if plan == "linkedin" else
                       args.repeats_scaled)
            runs = {a: [] for a in arms}
            for i in range(repeats):
                for a in (arms if i % 2 == 0 else list(reversed(arms))):
                    runs[a].append(run_arm(a, plan, wait))
            groups.append({"plan": plan, "wait": wait, "repeats": repeats,
                           "scale": args.scale if wait == "scaled" else None,
                           "all_runs": runs,
                           "rejected_for_host_speed": {a: [] for a in arms}})

    # Judged afterwards against the best host speed seen at ANY point, so a
    # run that began on a slow host cannot set a slow reference. A slow arm is
    # measured again, up to RETRIES times; every attempt is kept.
    best = min(c["cpu_ms_per_row"] for c in seen)
    for g in groups:
        for a, runs in g["all_runs"].items():
            for i, run in enumerate(runs):
                for _ in range(RETRIES):
                    if not slow(run, best):
                        break
                    g["rejected_for_host_speed"][a].append(run)
                    run = run_arm(a, g["plan"], g["wait"])
                runs[i] = dict(run, host_slow=slow(run, best),
                               cpu_row_equivalents=row_equivalents(run))
        for rejected in g["rejected_for_host_speed"].values():
            for run in rejected:
                run.update(host_slow=True, cpu_row_equivalents=row_equivalents(run))

    for g in groups:
        runs = g["all_runs"]

        def clean(a):
            return [r for r in runs[a] if not r["host_slow"]] or runs[a]

        def med(a, key, sub=None):
            return statistics.median(r[key][sub] if sub else r[key] for r in clean(a))
        every = [r for rs in list(runs.values())
                 + list(g["rejected_for_host_speed"].values()) for r in rs]
        base = med("serial", "wall_s") if "serial" in runs else None
        g.update({
            "outputs_identical_across_every_run":
                len({r["outputs_sha256"] for r in every}) == 1,
            "done_ledgers_identical": len({r["done_ledger_sha256"] for r in every}) == 1,
            "arms_still_on_a_slow_host": {a: sum(r["host_slow"] for r in runs[a])
                                          for a in arms},
            "median": {a: {
                "wall_s": med(a, "wall_s"), "cpu_s": med(a, "cpu_s"),
                "cpu_row_equivalents": med(a, "cpu_row_equivalents", "process"),
                "finalize_row_equivalents": med(a, "cpu_row_equivalents", "finalize"),
                "paid_phase_s": med(a, "paid_phase_s"),
                "peak_rss_kb": med(a, "peak_rss_kb"),
                "speedup_vs_serial": round(base / med(a, "wall_s"), 3) if base else None,
                # V2-C3.5: an arm against the same worker count with Indeed
                # unbounded, as C2 ran it before.
                "speedup_vs_old_same_workers": (
                    round(med(f"old{a}", "wall_s") / med(a, "wall_s"), 3)
                    if f"old{a}" in runs else None)}
                for a in arms},
        })
    out = {
        "status": "MEASURED OFFLINE; scripted provider (KeyedClient), no network",
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "code_revision": revision(),
        "host_best_calibration_cpu_ms_per_row": best,
        "host_slow_threshold": f"a before/after calibration above {SLOW} x the best "
                               f"seen; such an arm is measured again up to "
                               f"{RETRIES} times and every attempt kept",
        "runtimes_s": RUNTIMES_S, "indeed_runtimes_s": indeed_runtimes,
        "indeed_runtimes_source": (args.indeed_runtimes_source if args.indeed_runtimes
                                   else "synthetic: LinkedIn's distribution"),
        "start_s": START_S, "dataset_s": DATASET_S,
        "poll_s": POLL_S, "groups": groups,
        "limits": [
            "Synthetic rows (V2-C1's generator); scoring cost grows with real "
            "descriptions, which are longer.",
            "Scaled waits divide provider time by --scale but not CPU, so local "
            "work is --scale times heavier against the wait than in a real sweep.",
            "Indeed runtimes: " + (args.indeed_runtimes_source if args.indeed_runtimes
                                   else "LinkedIn's distribution by assumption "
                                        "(synthetic)") + ".",
            "One laptop; Oracle's cores and quota differ. A first run on "
            "2026-09-24 was discarded when its later arms slowed ~2.8x per row "
            "under host contention; the calibration exists because of it.",
        ],
    }
    if args.output:
        Path(args.output).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps([{k: g[k] for k in ("plan", "wait",
                                         "outputs_identical_across_every_run",
                                         "median")} for g in groups], indent=2))


if __name__ == "__main__":
    main()
