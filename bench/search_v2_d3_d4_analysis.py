"""V2-D3 / V2-D4: Indeed location redundancy and the paid polling interval,
analysed OFFLINE from the one integrated C5 run. Paid-unreachable: it reads the
kept run directory and committed evidence, imports no client and makes no
network call, and writes counts, clocks, amounts, ranks, unit ids, keyword
labels and fingerprints -- never a title, company, description, URL or query.

    .venv/bin/python -m bench.search_v2_d3_d4_analysis --run-dir output/c5-run \\
        --evidence-dir docs/search-v2-evidence --out-dir docs/search-v2-evidence

Writes d3-indeed-location-analysis.json and d4-polling-simulation.json.

D3. The engine held every paid row's job_key in memory only (paid_adaptive's
privacy rule), so the record has per-unit counts, one trace token per dataset
position, and the engine's own EXACT losses for three kinds of removal set:
each unit alone, each plan suffix (if_stopped_after), each Remote twin. A
location is none of those, so its loss is SET-IDENTIFIED here: every way of
giving each discarded eligible row (trace D) to the final job that beat it
that is consistent with the trace flags, the plan order, same_unit/other_paid
and the engine's exact losses is enumerated, and each counterfactual is
[min, max] over all of them. Where min == max it is exact.

D4. scrape_search sleeps 5 s, then sends one RunClient.get() (the SDK's 5 s
'short' timeout, doubled per re-send), until the run is terminal. Each unit's
loop start, poll count, total GET time and the provider's finishedAt are in
the record, so the 5 s schedule is rebuilt per unit, calibrated against the
measured poll counts and lags, and replayed at 1-4 s.
"""
import argparse
import glob
import itertools
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from bench.search_v2_c5_analysis import dist, ts

TOP_K = (10, 20, 50)
INTERVALS = (1, 2, 3, 4, 5)
BASELINE_S = 5
EST_RUN_USD = 0.09      # config.SITE_RATES["indeed"], the plan's own per-run estimate
SINK = "linkedin_won"   # a discarded Indeed row whose key a LinkedIn row won
EARLIER = ("c1-probe-a-india-two-shapes.json", "c1-probe-b-remote-react.json",
           "c1-cross-probe-summary.json", "c35-indeed-contract.json",
           "c35-indeed-provider-contract.json", "c35-indeed-concurrency-canary.json",
           "paid-plans.json", "replay-business_salesforce-india.json",
           "replay-react_native-india.json", "replay-software_fullstack-india.json",
           "replay-software_fullstack-remote.json")


# ===========================================================================
# Pure pieces (unit-tested)
# ===========================================================================
def overlap(a, b):
    """Set overlap: intersection, directional coverage, Jaccard."""
    a, b = set(a), set(b)
    inter, union = len(a & b), len(a | b)
    return {"intersection": inter,
            "a_to_b": round(inter / len(a), 3) if a else None,
            "b_to_a": round(inter / len(b), 3) if b else None,
            "jaccard": round(inter / union, 3) if union else None}


def removal_loss(jobs, removed):
    """What the result loses when every origin in `removed` did not run.

    jobs: [{"rank", "origins", "survivor", "marginal"}]; origins holds every
    unit (or "free") with an eligible row for that job, survivor the one that
    won. A job is lost only when ALL its origins are removed (dedupe's rule);
    one whose survivor went but another origin stayed is `substituted`: still
    in the result, at a rank the record cannot give (the other row's score).
    """
    removed = set(removed)
    lost = sorted(j["rank"] for j in jobs if j["origins"] <= removed)
    sub = sorted(j["rank"] for j in jobs
                 if j["survivor"] in removed and not j["origins"] <= removed)
    out = {"final_lost": len(lost),
           "final_marginal_lost": sum(1 for j in jobs if j["origins"] <= removed
                                      and j["marginal"]),
           "best_lost_rank": lost[0] if lost else None}
    for k in TOP_K:
        out[f"top{k}_lost"] = sum(1 for r in lost if r <= k)
        out[f"top{k}_at_risk"] = sum(1 for r in sub if r <= k)
    return out


def simulate_polls(finish, interval, g, g_last=None, alpha=0.5, deadline=360.0):
    """scrape_search's loop, on a clock that starts when the loop does: sleep
    `interval`, send one GET taking `g`, repeat. A GET sees the run terminal
    when the provider finished before the server read it, `alpha` of the way
    through the GET. Returns (polls, detected_at); the terminal GET takes
    `g_last` (a stalled request), every other one `g`."""
    g_last = g if g_last is None else g_last
    t, polls = 0.0, 0
    while t < deadline:
        t += interval
        polls += 1
        if t + alpha * g >= finish:
            return polls, t + g_last
        t += g
    return polls, None


def segment_wall(units, workers=2):
    """The C2 coordinator (paid_phase_c2), one thread: integrate the plan-order
    head the moment its result is in; else start the next search while a
    worker is free; else block for the next result, in finish order.
    units: plan-order [(exec_s, merge_s)]. Returns (wall_s, start_times)."""
    t, head, sent, running = 0.0, 0, 0, 0
    done_at, arrived, starts = {}, set(), []
    while True:
        if head < sent and head in arrived:
            t += units[head][1]
            head += 1
        elif sent < len(units) and running < workers:
            done_at[sent] = t + units[sent][0]
            starts.append(t)
            sent += 1
            running += 1
        elif head == sent:
            return t, starts
        else:
            i = min((j for j in done_at if j not in arrived), key=lambda j: done_at[j])
            t = max(t, done_at[i])
            arrived.add(i)
            running -= 1


def min_attempts(duration_s, timeout_s=5.0, backoff_s=0.5, cap_s=360.0):
    """The fewest attempts one SDK call needs to last `duration_s`: attempt i
    is cut at timeout*2^(i-1) (capped), and a failed attempt i sleeps at most
    2 * backoff * 2^(i-1) (apify-client's jittered exponential backoff)."""
    k, longest = 1, min(timeout_s, cap_s)
    while longest < duration_s:
        longest += 2 * backoff_s * 2 ** (k - 1) + min(timeout_s * 2 ** k, cap_s)
        k += 1
    return k


# ===========================================================================
# Loading
# ===========================================================================
def load(run_dir, evidence_dir):
    run, ev = Path(run_dir), Path(evidence_dir)
    tpath = sorted(glob.glob(str(run / "telemetry" / "sweep_*.json")))[0]
    jpath = sorted(glob.glob(str(run / "jobs_*.json")))[0]
    rec = json.loads(Path(tpath).read_text())
    rows = json.loads(Path(jpath).read_text())
    settle = json.loads((ev / "c5-provider-cost-settlement.json").read_text())
    names = [f"{run.name}/telemetry/{Path(tpath).name}", f"{run.name}/{Path(jpath).name}",
             "c5-provider-cost-settlement.json", "scraper.py", "telemetry.py",
             "paid_adaptive.py", "apify_client/_consts.py"]
    return rec, rows, settle, names


def plan_units(rec):
    """Plan-order paid units joined to their execution and C2 schedule rows."""
    ex = {u["unit_id"]: u for u in rec["units"] if u.get("path") == "paid"}
    sc = {u["unit_id"]: u for u in rec["paid_execution"]["units"]}
    return [dict(p, ex=ex.get(p["unit_id"], {}), sched=sc.get(p["unit_id"], {}))
            for p in sorted(rec["paid_units"], key=lambda u: u["plan_index"])]


def final_positions(units, rows):
    """(unit_id, dataset position) -> final rank for every paid survivor. A
    row's search_query is scrape_search's own label and search_rank its
    dataset index; the label is matched in memory and never written."""
    by_label = defaultdict(list)
    for u in units:
        e = u["ex"]
        by_label[(u["provider"], f"{e.get('query') or '(all)'} @ {e.get('location') or ''}")
                 ].append(u["unit_id"])
    out = {}
    for rank, r in enumerate(rows, 1):
        ids = by_label.get((r.get("source_site"), r.get("search_query")))
        if ids:
            assert len(ids) == 1, "a label names two units of one provider"
            out[(ids[0], int(r["search_rank"]))] = rank
    return out


def engine_facts(root):
    """The poll loop and the SDK's retry constants, read as text (no import)."""
    src = (root / "scraper.py").read_text()
    body = src[src.index("def scrape_search("):]
    body = body[:body.index("\ndef ", 10)]
    loop = body[body.index("while time.monotonic() < deadline"):]
    consts = {}
    for p in sorted(root.glob(".venv/lib/python*/site-packages/apify_client/_consts.py")):
        for name, val in re.findall(r"^(DEFAULT_\w+) = (.+)$", p.read_text(), re.M):
            consts[name] = val.strip()
    return {"poll_interval_s": int(re.search(r"time\.sleep\((\d+)\)", loop).group(1)),
            "deadline_s": int(re.search(r"deadline = time\.monotonic\(\) \+ (\d+)", body)
                              .group(1)),
            "poll_call": "rc.get()" if "run = rc.get()" in loop else "UNKNOWN",
            "uses_wait_for_finish": ".wait_for_finish(" in body.replace(
                "AVOID .call()/.wait_for_finish()", ""),
            "sdk_constants": {k: consts.get(k) for k in (
                "DEFAULT_TIMEOUT_SHORT", "DEFAULT_MAX_RETRIES",
                "DEFAULT_MIN_DELAY_BETWEEN_RETRIES")}}


# ===========================================================================
# D3 — Indeed location redundancy
# ===========================================================================
def keyword_labels(units):
    labels = {}
    for u in units:
        labels.setdefault(u["keyword_fp"], f"k{len(labels) + 1:02d}")
    return labels


def indeed_jobs(indeed, pos_rank, per_unit, order):
    """The final jobs Indeed won and the eligible rows dedupe discarded.

    marginal: the engine's own per-unit count, resolved per row by the
    engine's if_skipped_alone best_lost_rank when a unit mixes both kinds.
    free_origin: from the engine's suffix losses -- a survivor first acquired
    at u ('n') can only have origins after u or free, so the jobs lost by
    removing units >= u but not units > u are exactly u's survivors with no
    free origin.
    """
    ids = [u["unit_id"] for u in order]
    stop = {p["unit_id"]: p["if_stopped_after"]["final_lost"] for p in per_unit}
    alone = {p["unit_id"]: p["if_skipped_alone"] for p in per_unit}
    jobs, drows = [], []
    for u in indeed:
        uid, f = u["unit_id"], u["funnel"]
        base = {"unit": uid, "location": u["ex"]["location"], "keyword": u["keyword_fp"],
                "mode": u["location_mode"]}
        toks = u["trace"].split()
        finals = [(i, t) for i, t in enumerate(toks, 1) if t[0] == "F"]
        ranks = [pos_rank[(uid, i)] for i, _ in finals]
        assert len(ranks) == f["final"], uid
        if f["final_marginal"] in (0, len(finals)):
            marg = [bool(f["final_marginal"])] * len(finals)
        else:
            assert f["final_marginal"] == 1, f"{uid}: which survivors are marginal is unknown"
            marg = [r == alone[uid]["best_lost_rank"] for r in ranks]
        prev = ids[ids.index(uid) - 1]
        paid_only = stop[prev] - stop[uid]
        nonmarg = [k for k, m in enumerate(marg) if not m]
        free_n = len(finals) - paid_only
        assert free_n in (0, len(nonmarg)), f"{uid}: which survivor has a free origin is unknown"
        for k, ((i, t), rank) in enumerate(zip(finals, ranks)):
            jobs.append(dict(base, id=f"rank_{rank:03d}", rank=rank, position=i,
                             marginal=marg[k], free_origin=bool(free_n) and k in nonmarg,
                             fflag=t[3] == "f", acquired=t[2]))
        for i, t in enumerate(toks, 1):
            if t[0] == "D":
                drows.append(dict(base, position=i, fflag=t[3] == "f", acquired=t[2]))
        lost = f["dedupe_lost"]
        assert lost["same_unit"] == lost["free"] == 0, f"{uid}: a D row not lost to other paid"
    return jobs, drows


def twin_clash(units_of_job, info):
    """paid_adaptive.pairs' rule: a Remote twin shares no eligible key with an
    earlier place search of its keyword (overlap_with_places == 0)."""
    for a in units_of_job:
        for b in units_of_job:
            if (info[a]["mode"] == "remote" and info[b]["mode"] == "place"
                    and info[a]["keyword"] == info[b]["keyword"]
                    and info[b]["plan_index"] < info[a]["plan_index"]):
                return True
    return False


def candidates(d, jobs, sink_flags, info):
    """The jobs a discarded row can belong to: one that is not marginal, with
    the same key_also_free flag (it is per key), not in the D row's own unit
    (same_unit == 0), and -- the survivor having been acquired 'n', new to
    the sweep -- from an earlier unit. Or a LinkedIn survivor with the flag."""
    out = [j["id"] for j in jobs
           if not j["marginal"] and j["fflag"] == d["fflag"] and j["unit"] != d["unit"]
           and (j["acquired"] != "n"
                or info[j["unit"]]["plan_index"] < info[d["unit"]]["plan_index"])
           and not twin_clash((j["unit"], d["unit"]), info)]
    return out + ([SINK] if d["fflag"] in sink_flags else [])


def assignments(jobs, drows, sink_flags, info):
    """Every consistent way to give the discarded rows to jobs: yields
    {job_id: set of D-row units}, {unit: rows given to LinkedIn}."""
    by_unit = defaultdict(list)
    for d in drows:
        by_unit[d["unit"]].append(d)
    units = sorted(by_unit, key=lambda u: info[u]["plan_index"])
    options = []
    for u in units:
        opts = set()
        for pick in itertools.product(*(candidates(d, jobs, sink_flags, info)
                                        for d in by_unit[u])):
            mine = [p for p in pick if p != SINK]
            if len(mine) == len(set(mine)):        # two rows of one unit: two keys
                opts.add((frozenset(mine), len(pick) - len(mine)))
        options.append(sorted(opts, key=lambda o: (sorted(o[0]), o[1])))
    need = {j["id"] for j in jobs if not j["marginal"] and not j["free_origin"]}
    survivor = {j["id"]: j["unit"] for j in jobs}
    for combo in itertools.product(*options):
        extra, sinks = defaultdict(set), {}
        for u, (picked, n_sink) in zip(units, combo):
            sinks[u] = n_sink
            for jid in picked:
                extra[jid].add(u)
        if need <= set(extra) and not any(
                twin_clash(extra[j] | {survivor[j]}, info) for j in extra):
            yield extra, sinks


def origin_sets(jobs, extra, loc_of=None):
    """Each job's origins (units, or locations with loc_of), 'free' included."""
    out = []
    for j in jobs:
        o = {j["unit"]} | extra.get(j["id"], set())
        if loc_of:
            o = {loc_of[x] for x in o}
        out.append({"rank": j["rank"], "marginal": j["marginal"],
                    "survivor": loc_of[j["unit"]] if loc_of else j["unit"],
                    "origins": frozenset(o | ({"free"} if j["free_origin"] else set()))})
    return out


def span(values):
    v = [x for x in values if x is not None]
    return [min(v), max(v)] if v else [None, None]


def trace_identity(by_kw_loc, locs):
    """Per location pair: keywords whose reach+score token sequences match
    position for position, and the agreement rate against chance."""
    out = {}
    for a, b in itertools.combinations(locs, 2):
        same, agree, total, chance = 0, 0, 0, 0.0
        for kw, got in by_kw_loc.items():
            x, y = [t[:2] for t in got[a]], [t[:2] for t in got[b]]
            same += x == y
            n = min(len(x), len(y))
            agree += sum(p == q for p, q in zip(x, y))
            total += n
            cx, cy = Counter(x), Counter(y)
            chance += n * sum(cx[t] / len(x) * cy[t] / len(y) for t in cx)
        out[(a, b)] = {"keywords_identical": same, "keywords": len(by_kw_loc),
                       "positional_agreement": round(agree / total, 3),
                       "chance_agreement": round(chance / total, 3)}
    return out


def d3(rec, rows, settle, names, evidence_dir):
    units = plan_units(rec)
    info = {u["unit_id"]: {"mode": u["location_mode"], "keyword": u["keyword_fp"],
                           "plan_index": u["plan_index"]} for u in units}
    indeed = [u for u in units if u["provider"] == "indeed"]
    labels = keyword_labels(indeed)
    locs = list(dict.fromkeys(u["ex"]["location"] for u in indeed))
    loc_of = {u["unit_id"]: u["ex"]["location"] for u in indeed}
    pos_rank = final_positions(units, rows)
    ad = rec["paid_adaptive"]
    jobs, drows = indeed_jobs(indeed, pos_rank, ad["per_unit"], units)
    li_final = [t for u in units if u["provider"] == "linkedin"
                for t in u["trace"].split() if t[0] == "F"]
    sink_flags = {t[3] == "f" for t in li_final}
    settled = {r["unit_id"]: r["settled_usd"] for r in settle["runs"]}
    ceiling = {u["unit_id"]: float(u["charge_ceiling_usd"]) for u in units}

    # ---- every consistent assignment, collapsed to location space -----------
    n_assign, unit_sigs, loc_sigs = 0, [], Counter()
    first = None
    for extra, sinks in assignments(jobs, drows, sink_flags, info):
        n_assign += 1
        first = first or extra
        sig = tuple(frozenset(loc_of[x] for x in extra.get(j["id"], ())) for j in jobs)
        sink_loc = Counter()
        for u, n in sinks.items():
            sink_loc[loc_of[u]] += n
        loc_sigs[(sig, tuple(sorted(sink_loc.items())))] += 1
        if len(unit_sigs) < 2 or n_assign % 50000 == 0:
            unit_sigs.append(extra)
    unit_sigs.append(extra)
    scenarios = []
    for (sig, sink_loc), n in sorted(loc_sigs.items(), key=lambda kv: repr(kv[0])):
        extra = {j["id"]: set(s) for j, s in zip(jobs, sig)}
        scenarios.append((origin_sets(jobs, {}, None), extra, dict(sink_loc), n))

    def loc_jobs(extra):
        return [{"rank": j["rank"], "marginal": j["marginal"], "survivor": j["location"],
                 "origins": frozenset({j["location"]} | extra[j["id"]]
                                      | ({"free"} if j["free_origin"] else set()))}
                for j in jobs]

    def bounded(removed_locs):
        got = [removal_loss(loc_jobs(extra), removed_locs) for _, extra, _, _ in scenarios]
        out = {k: span(g[k] for g in got) for k in ("final_lost", "final_marginal_lost")}
        for k in TOP_K:
            out[f"top{k}_lost"] = [min(g[f"top{k}_lost"] for g in got),
                                   max(g[f"top{k}_lost"] + g[f"top{k}_at_risk"] for g in got)]
        out["best_lost_rank_possible"] = sorted({g["best_lost_rank"] for g in got},
                                                key=lambda r: (r is None, r))
        return out

    # ---- validation against the engine's exact, key-based losses -------------
    checks = {"suffix": [0, 0], "alone": [0, 0], "alone_topk_within_bounds": [0, 0]}
    ids = [u["unit_id"] for u in units]
    for extra in unit_sigs:
        ujobs = origin_sets(jobs, extra)
        for p in ad["per_unit"]:
            i = ids.index(p["unit_id"])
            if i < ids.index(indeed[0]["unit_id"]) - 1:
                continue
            got = removal_loss(ujobs, ids[i + 1:])
            want = p["if_stopped_after"]
            checks["suffix"][1] += 1
            checks["suffix"][0] += all(got[k] == want[k] for k in (
                "final_lost", "final_marginal_lost", "best_lost_rank",
                "top10_lost", "top20_lost", "top50_lost"))
            if p["unit_id"] in loc_of:
                got, want = removal_loss(ujobs, {p["unit_id"]}), p["if_skipped_alone"]
                checks["alone"][1] += 1
                checks["alone"][0] += all(got[k] == want[k] for k in (
                    "final_lost", "final_marginal_lost", "best_lost_rank"))
                checks["alone_topk_within_bounds"][1] += 1
                checks["alone_topk_within_bounds"][0] += all(
                    got[f"top{k}_lost"] <= want[f"top{k}_lost"]
                    <= got[f"top{k}_lost"] + got[f"top{k}_at_risk"] for k in TOP_K)
    engine_total = ad["per_unit"][ids.index(indeed[0]["unit_id"]) - 1]["if_stopped_after"]

    # ---- per location -------------------------------------------------------
    by_kw_loc = defaultdict(dict)
    for u in indeed:
        by_kw_loc[labels[u["keyword_fp"]]][u["ex"]["location"]] = u["trace"].split()
    ident = trace_identity(by_kw_loc, locs)
    table = {}
    for loc in locs:
        mine = [u for u in indeed if u["ex"]["location"] == loc]
        f = lambda k: sum(u["funnel"].get(k) or 0 for u in mine)
        a = lambda k: sum(u["funnel"]["acquired"][k] for u in mine)
        ranks = sorted(j["rank"] for j in jobs if j["location"] == loc)
        cost = round(sum(settled[u["unit_id"]] for u in mine), 6)
        loss = bounded({loc})
        top20 = sum(1 for r in ranks if r <= 20)
        per = lambda n: round(cost / n, 4) if n else None
        table[loc] = {
            "starts": len(mine), "requested_depth_each": mine[0]["requested_depth"],
            "requested_total": sum(u["requested_depth"] for u in mine),
            "returned": f("raw"), "stale": f("stale"),
            "repeat_in_unit": a("repeat_in_unit"),
            "repeat_of_earlier_paid": a("repeat_of_earlier_paid"),
            "duplicate_paid": a("repeat_in_unit") + a("repeat_of_earlier_paid"),
            "unique_paid_new": a("new"), "key_also_free": f("key_also_free"),
            "eligible": f("eligible"), "eligible_positive": f("eligible_positive"),
            "final": f("final"), "final_positive": f("final_positive"),
            "final_marginal_unit_sum": f("final_marginal"),
            "final_marginal_location": loss["final_lost"],
            **{f"top{k}": sum(1 for r in ranks if r <= k) for k in TOP_K},
            "best_final_rank": ranks[0] if ranks else None,
            "settled_usd": cost, "exposure_usd": round(sum(ceiling[u["unit_id"]]
                                                           for u in mine), 3),
            "cost_per_final_usd": per(f("final")),
            "cost_per_final_marginal_usd": {
                "unit_sum": per(f("final_marginal")),
                "location_at_min": per(loss["final_lost"][0]),
                "location_at_max": per(loss["final_lost"][1])},
            "cost_per_top20_usd": per(top20),
            "per_unit": [{"unit_id": u["unit_id"], "keyword": labels[u["keyword_fp"]],
                          "raw": u["funnel"]["raw"],
                          "new": u["funnel"]["acquired"]["new"],
                          "repeat_of_earlier_paid":
                              u["funnel"]["acquired"]["repeat_of_earlier_paid"],
                          "repeat_in_unit": u["funnel"]["acquired"]["repeat_in_unit"],
                          "eligible": u["funnel"]["eligible"], "final": u["funnel"]["final"],
                          "final_marginal": u["funnel"]["final_marginal"],
                          "settled_usd": round(settled[u["unit_id"]], 6)} for u in mine]}

    # ---- pair matrix ----------------------------------------------------------
    matrix = []
    for a, b in itertools.combinations(locs, 2):
        idn = ident[(a, b)]
        later = b if locs.index(b) > locs.index(a) else a
        all_repeat = table[later]["unique_paid_new"] == 0
        same_list = idn["keywords_identical"] == idn["keywords"] and all_repeat
        shared, pair_only, sink_extra, cov = [], [], [], []
        for _, extra, sink_loc, _ in scenarios:
            lj = loc_jobs(extra)
            ea = {j["rank"] for j in lj if a in j["origins"]}
            eb = {j["rank"] for j in lj if b in j["origins"]}
            shared.append(len(ea & eb))
            cov.append(overlap(ea, eb))
            sink_extra.append(min(sink_loc.get(a, 0), sink_loc.get(b, 0)))
            both = removal_loss(lj, {a, b})["final_lost"]
            pair_only.append(both - removal_loss(lj, {a})["final_lost"]
                             - removal_loss(lj, {b})["final_lost"])
        matrix.append({
            "a": a, "b": b,
            "raw": ({"status": "INFERRED", "same_result_list": True,
                     "a_to_b": 1.0, "b_to_a": 1.0, "jaccard": 1.0,
                     "basis": (f"reach+score tokens identical position for position on "
                               f"{idn['keywords_identical']}/{idn['keywords']} keywords, and "
                               f"every row of {later} was acquired as a repeat")}
                    if same_list else
                    {"status": "UNKNOWN", "intersection": None,
                     "basis": "per-row job keys were never persisted"}),
            "trace_identity": idn,
            "eligible_shared_jobs_indeed_won": span(shared),
            "eligible_shared_jobs_linkedin_won_possible": max(sink_extra),
            "eligible_a_to_b": span(c["a_to_b"] for c in cov),
            "eligible_b_to_a": span(c["b_to_a"] for c in cov),
            "eligible_jaccard": span(c["jaccard"] for c in cov),
            "final_shared_jobs": span(shared),
            "final_marginal_shared": 0,
            "lost_only_if_both_removed": span(pair_only)})

    # ---- counterfactuals --------------------------------------------------------
    places = [x for x in locs if x != "Remote"]
    sets = [("baseline", [])] + [(f"without {x}", [x]) for x in locs] + [
        ("without Gurgaon+Noida (keep Delhi, New Delhi)", ["Gurgaon", "Noida"]),
        ("NCR as Delhi only (drop New Delhi, Gurgaon, Noida)",
         ["New Delhi", "Gurgaon", "Noida"]),
        ("NCR as New Delhi only (drop Delhi, Gurgaon, Noida)", ["Delhi", "Gurgaon", "Noida"]),
        ("without the whole NCR cluster", ["Delhi", "New Delhi", "Gurgaon", "Noida"]),
        ("Remote twins only (drop every place)", places),
        ("without Indeed", locs)]
    cfs = []
    for name, drop in sets:
        mine = [u for u in indeed if u["ex"]["location"] in drop]
        cfs.append({"name": name, "removed_locations": drop, "starts_avoided": len(mine),
                    "exposure_avoided_usd": round(sum(ceiling[u["unit_id"]] for u in mine), 3),
                    "estimated_cost_avoided_usd": round(EST_RUN_USD * len(mine), 3),
                    "settled_cost_avoided_usd": round(sum(settled[u["unit_id"]]
                                                          for u in mine), 4),
                    **bounded(drop)})
    total = next(c for c in cfs if c["name"] == "without Indeed")
    assert total["final_lost"] == [engine_total["final_lost"]] * 2
    remote = next(c for c in cfs if c["name"] == "without Remote")
    pairs = [p for p in ad["pairs"] if p["provider"] == "indeed"]
    summed = {k: sum(p["if_remote_skipped"][k] for p in pairs)
              for k in ("final_lost", "top20_lost")}

    # ---- earlier evidence: does anything else see these locations returned? --
    ev = Path(evidence_dir)
    earlier = {}
    for name in EARLIER:
        p = ev / name
        if p.exists():
            text = p.read_text()
            earlier[name] = {x: text.count(x) for x in locs if x != "Remote" and x in text}

    gn = table["Gurgaon"], table["Noida"]
    return {
        "schema": "search-v2-d3.1",
        "status": "OFFLINE analysis of the one integrated C5 run; a decision input, not a "
                  "decision to prune. One synthetic profile, one date, one keyword set.",
        "generated_from": names,
        "evidence_labels": {
            "plan_shape": "MEASURED", "per_location_funnel_and_cost": "MEASURED",
            "top_k_and_best_rank_by_location": "MEASURED (final JSON rank x survivor unit)",
            "final_marginal_unit_sum": "MEASURED (engine per unit)",
            "final_marginal_location_and_counterfactuals":
                "INFERRED, set-identified: [min, max] over every assignment consistent "
                "with the record; exact where min == max",
            "engine_validation": "VERIFIED (reproduces the engine's key-based losses)",
            "raw_overlap_new_delhi_gurgaon_noida": "INFERRED (strong; see matrix basis)",
            "raw_overlap_other_pairs": "UNKNOWN (per-row keys never persisted)",
            "eligible_and_final_overlap": "INFERRED, set-identified",
            "remote_same_keyword_overlap": "MEASURED (engine pairs)",
            "earlier_evidence_corroboration": "VERIFIED absent (single-source)"},
        "method": {
            "identity": "The engine's own job_key semantics, read through its records: the "
                        "trace's acquired letter (n new / u repeat in unit / p repeat of an "
                        "earlier paid unit, in plan order) and f flag come from "
                        "telemetry._paid_acquired over job_key; F/D and marginal from "
                        "_paid_outcome over dedupe's result. Jobs are named by final rank "
                        "only.",
            "final_marginal": "unit: the engine's own (no other unit and no free source had "
                              "an eligible row with that key). location: the final jobs "
                              "every one of whose eligible origins is in that location "
                              "(removal_loss) -- the jobs that disappear if it had not run.",
            "set_identification": "Each discarded eligible row (trace D, all lost_to "
                                  "other_paid) belongs to a non-marginal final job with the "
                                  "same f flag, not in its own unit, won by an earlier unit "
                                  "(the survivors were all acquired 'n'), never a Remote "
                                  "twin with its own keyword's place search (pairs "
                                  "overlap_with_places == 0), at most one per job per unit; "
                                  "or a LinkedIn survivor with the same flag. Every "
                                  "non-marginal survivor without a free origin (read off "
                                  "the engine's suffix losses) gets at least one.",
            "top_k_bounds": "lower: lost jobs only. upper: plus jobs whose survivor was "
                            "removed but another origin stayed (its rank is the other "
                            "row's, which the record does not hold).",
            "assignments_enumerated": n_assign,
            "distinct_location_scenarios": len(scenarios),
            "keywords": {v: k for k, v in labels.items()}},
        "engine_validation": {"suffix_losses_matched": checks["suffix"],
                              "single_unit_losses_matched": checks["alone"],
                              "single_unit_topk_within_bounds":
                                  checks["alone_topk_within_bounds"],
                              "assignments_checked": len(unit_sigs),
                              "survivor_rows_mapped": len(jobs),
                              "discarded_rows": len(drows),
                              "without_indeed_engine": {k: engine_total[k] for k in (
                                  "final_lost", "final_marginal_lost", "top10_lost",
                                  "top20_lost", "top50_lost", "best_lost_rank")}},
        "plan": {"locations": locs, "keywords": len(labels), "starts": len(indeed),
                 "shape": f"{len(labels)} keywords x {len(locs)} location values "
                          f"({len(places)} India places + Remote)",
                 "requested_depth": indeed[0]["requested_depth"],
                 "ceiling_usd_each": ceiling[indeed[0]["unit_id"]],
                 "estimated_usd_each": EST_RUN_USD},
        "locations": table,
        "overlap_matrix": matrix,
        "counterfactuals": cfs,
        "remote_check": {
            "same_keyword_overlap_with_places": [p["overlap_with_places"] for p in pairs],
            "c5_claim": {"final_lost": 10, "best_lost_rank": 21, "top20_lost": 0,
                         "exposure_usd": 1.215},
            "sum_of_single_twin_skips": summed,
            "joint_removal": {k: remote[k] for k in (
                "final_lost", "top10_lost", "top20_lost", "top50_lost",
                "best_lost_rank_possible", "exposure_avoided_usd")},
            "verdict": "C5's 10 finals / best rank 21 / 0 top-20 is the SUM of the nine "
                       "single-twin skips; twins duplicate EACH OTHER across keywords, so "
                       "removing all nine loses more (joint_removal).",
            "remote_share_of_indeed_finals": sum(1 for j in jobs if j["location"] == "Remote")},
        "earlier_evidence": {"location_mentions": earlier,
                             "note": "c35 Indeed probes ran Bengaluru only; paid-plans.json "
                                     "is plan shape (offline, zero paid calls) with no "
                                     "returned rows; replay-*/c1-* hold no Indeed location "
                                     "results. The NCR redundancy is SINGLE-SOURCE (C5)."},
        "c5_fact_checks": [
            {"fact": "Indeed 72 = 8 India locations x 9 keywords plus Remote twins",
             "finding": f"72 = {len(labels)} keywords x {len(locs)} location values: "
                        f"{len(places)} India places + 1 Remote twin"},
            {"fact": "duplicate-of-another-paid-search 644",
             "finding": f"644 = {sum(t['repeat_in_unit'] for t in table.values())} repeats "
                        f"within one search + "
                        f"{sum(t['repeat_of_earlier_paid'] for t in table.values())} "
                        f"repeats of an earlier paid search"},
            {"fact": "removing all Remote twins loses 10 finals, best rank 21, 0 top-20",
             "finding": f"WRONG as a joint removal: final_lost {remote['final_lost']}, "
                        f"top-20 lost {remote['top20_lost']}, best lost rank one of "
                        f"{remote['best_lost_rank_possible']}; 10/21/0 is the sum of "
                        f"single-twin skips"}],
        "decision": {
            "outcome": "B",
            "recommendation": "Keep the Indeed default locations unchanged for now.",
            "reasons": [
                f"Gurgaon and Noida are the only candidates: 0 of "
                f"{gn[0]['returned'] + gn[1]['returned']} rows new to the sweep (MEASURED), "
                f"the same result list as New Delhi on 9/9 keywords (INFERRED), 0 eligible, "
                f"0 final, 0 top-k, and removing both loses exactly nothing here while "
                f"avoiding 18 starts, ${gn[0]['exposure_usd'] + gn[1]['exposure_usd']:.3f} "
                f"exposure and ${gn[0]['settled_usd'] + gn[1]['settled_usd']:.2f} settled.",
                "But the bar for outcome A needs redundancy across MULTIPLE evidence sources "
                "and this is one run, one date, one synthetic profile's keywords: no earlier "
                "probe ran Indeed at an NCR location. Single-source.",
                "New Delhi also had 0 eligible, but it is the list Gurgaon and Noida repeat: "
                "dropping all three (or Delhi) is a different, profile-dependent bet.",
                "Remote twins are the most valuable Indeed searches (14 of 20 finals, the #1 "
                "final job overall): keep them."],
            "single_source": ["NCR trio identity", "Gurgaon/Noida zero contribution",
                              "every location-level loss"],
            "to_reach_A": "A second run on a different keyword set (or date) that again "
                          "shows Gurgaon and Noida acquiring 0 new keys after New Delhi; "
                          "persisting a salted hash of each paid unit's job keys would "
                          "turn the UNKNOWN pair overlaps and these bounds into exact "
                          "figures."}}


# ===========================================================================
# D4 — polling interval
# ===========================================================================
def poll_units(units):
    """Per unit, on the engine clock (epoch s): the loop start (terminal-poll
    stamp minus wait_ms), the provider's finish, measured polls, GET latency.
    A unit whose SDK re-sent requests has its excess on the terminal GET."""
    base = defaultdict(list)
    for u in units:
        e, sdk = u["ex"], u["sched"].get("sdk") or {}
        if sdk.get("requests") == sdk.get("calls"):
            base[u["provider"]].append(e["poll_get_ms"] / 1000 / e["poll_count"])
    typical = {p: statistics.median(v) for p, v in base.items()}
    out = []
    for u in units:
        e, s = u["ex"], u["sched"]
        det = ts(next(o["observed_at"] for o in e["cost_observations"]
                      if o["source"] == "run_record_at_completion")).timestamp()
        n, total = e["poll_count"], e["poll_get_ms"] / 1000
        resend = s["sdk"]["requests"] - s["sdk"]["calls"]
        g = typical[u["provider"]] if resend else total / n
        out.append({"unit_id": u["unit_id"], "provider": u["provider"],
                    "plan_index": u["plan_index"], "loop": det - e["wait_ms"] / 1000,
                    "det": det, "finish": ts(e["actor_finished_at"]).timestamp(),
                    "provider_started": ts(e["actor_started_at"]).timestamp(),
                    "committed": ts(s["committed_at"]).timestamp(),
                    "unit_started": ts(e["started_at"]).timestamp(),
                    "dataset_start": ts(e["dataset_retrieved_at"]).timestamp()
                    - e["dataset_ms"] / 1000,
                    "polls": n, "get_total": total, "g": g,
                    "g_last": total - (n - 1) * g, "resend": resend,
                    "start_ms": e["start_ms"], "exec_s": s["execution_ms"] / 1000,
                    "merge_s": s["merge_ms"] / 1000, "sdk": s["sdk"],
                    "run_time_s": e.get("actor_run_time_s"),
                    "unit_wall_s": e["duration_ms"] / 1000})
    return out, typical


def replay(p, interval, delta, alpha):
    """(polls, detection epoch) for one unit at `interval`."""
    polls, at = simulate_polls(p["finish"] + delta - p["loop"], interval, p["g"],
                               p["g_last"], alpha)
    return polls, None if at is None else p["loop"] + at


def calibrate(pts):
    """The clock offset (engine - provider) and server read point that make
    the rebuilt 5 s schedule reproduce the measured poll counts best."""
    best = None
    for alpha in (0.0, 0.25, 0.5, 0.75, 1.0):
        for step in range(-200, 201):
            delta = step / 100
            sims = [replay(p, BASELINE_S, delta, alpha) for p in pts]
            miss = sum(s[0] != p["polls"] for s, p in zip(sims, pts))
            err = sum(abs(s[1] - p["det"]) for s, p in zip(sims, pts))
            key = (miss, round(err, 6), abs(delta), abs(alpha - 0.5))
            if best is None or key < best[0]:
                best = (key, delta, alpha)
    return best[1], best[2]


def d4(rec, names, facts):
    units = plan_units(rec)
    pts, typical = poll_units(units)
    delta, alpha = calibrate(pts)
    lo = max(p["committed"] - p["provider_started"] for p in pts)
    hi = min(p["loop"] - p["provider_started"] for p in pts)
    lag5 = {p["unit_id"]: p["det"] - p["finish"] for p in pts}
    sim = {(p["unit_id"], i): replay(p, i, delta, alpha) for p in pts for i in INTERVALS}
    calib = {}
    segs = {g["provider"]: g for g in rec["paid_execution"]["segments"]}
    providers = [g["provider"] for g in rec["paid_execution"]["segments"]]
    for prov in providers:
        mine = [p for p in pts if p["provider"] == prov]
        s5 = [sim[(p["unit_id"], BASELINE_S)] for p in mine]
        wall, starts = segment_wall([(p["exec_s"], p["merge_s"]) for p in mine])
        t0 = mine[0]["unit_started"]
        calib[prov] = {
            "poll_count_mismatches": sum(s[0] != p["polls"] for s, p in zip(s5, mine)),
            "units": len(mine),
            "lag_abs_error_s": dist([abs(s[1] - p["det"]) for s, p in zip(s5, mine)]),
            "measured_lag_s": dist([lag5[p["unit_id"]] for p in mine]),
            "simulated_5s_lag_s": dist([s[1] - p["finish"] for s, p in zip(s5, mine)]),
            "segment_wall_measured_s": round(segs[prov]["wall_ms"] / 1000, 3),
            "segment_wall_simulated_s": round(wall, 3),
            "segment_start_error_s": dist([abs(s - (p["unit_started"] - t0))
                                           for s, p in zip(starts, mine)])}
    table, per_plan = {}, {}
    for prov in providers:
        mine = [p for p in pts if p["provider"] == prov]
        base_wall = segment_wall([(p["exec_s"], p["merge_s"]) for p in mine])[0]
        rows = {}
        for i in INTERVALS:
            got = [sim[(p["unit_id"], i)] for p in mine]
            lags = [at - p["finish"] for (_, at), p in zip(got, mine)]
            extra = [n - sim[(p["unit_id"], BASELINE_S)][0] for (n, _), p in zip(got, mine)]
            shift = [sim[(p["unit_id"], BASELINE_S)][1] - at for (_, at), p in zip(got, mine)]
            wall = segment_wall([(p["exec_s"] - s, p["merge_s"])
                                 for p, s in zip(mine, shift)])[0]
            lag = dist(lags)
            rows[str(i)] = {
                "lag_s": dict(lag, mean=round(statistics.mean(lags), 3)),
                "polls_per_run": round(statistics.mean(n for n, _ in got), 3),
                "extra_polls_per_run_vs_5s": round(statistics.mean(extra), 3),
                "extra_polls_provider_total": sum(extra),
                "segment_wall_s_inferred": round(wall, 1),
                "segment_wall_change_s_inferred": round(wall - base_wall, 1),
                "peak_poll_rate_per_s_2_workers": round(2 / (i + typical[prov]), 2)}
        table[prov] = rows
    for i in INTERVALS:
        per_plan[str(i)] = sum(table[p][str(i)]["extra_polls_provider_total"]
                               for p in providers)

    # ---- the Indeed outlier ------------------------------------------------------
    ind = [p for p in pts if p["provider"] == "indeed"]
    worst = max(ind, key=lambda p: lag5[p["unit_id"]])
    stalls = []
    for p in pts:
        if p["resend"]:
            first_poll = p["loop"] + BASELINE_S
            stalls.append({"unit_id": p["unit_id"], "provider": p["provider"],
                           "sdk_resends": p["resend"], "polls": p["polls"],
                           "poll_get_s": round(p["get_total"], 3),
                           "start_s": round(p["start_ms"] / 1000, 3),
                           "lag_s": round(lag5[p["unit_id"]], 3),
                           "provider_finish_after_loop_start_s":
                               round(p["finish"] - p["loop"], 3),
                           "first_poll_issued_after_finish_s":
                               round(first_poll - p["finish"], 3),
                           "min_attempts_for_one_get": min_attempts(p["get_total"])})
    ex = {u["unit_id"]: u["ex"] for u in units}
    sc = {u["unit_id"]: u["sched"] for u in units}
    w_lo, w_hi = worst["det"] - worst["get_total"], worst["det"]
    concurrent = []
    for u in units:
        e = u["ex"]
        acct_at = next((ts(o["observed_at"]).timestamp() for o in e.get("cost_observations")
                        or () if o["source"] == "account_usage_delta"), None)
        if acct_at and e.get("account_read_ms"):
            a_lo = acct_at - e["account_read_ms"] / 1000
            common = min(w_hi, acct_at) - max(w_lo, a_lo)
            if common > 0 and u["unit_id"] != worst["unit_id"]:
                concurrent.append({
                    "unit_id": u["unit_id"], "call": "account limits read (coordinator "
                    "thread, the account's own read_client)",
                    "duration_s": round(e["account_read_ms"] / 1000, 3),
                    "overlap_with_outlier_poll_s": round(common, 3),
                    "ended_apart_s": round(abs(acct_at - w_hi), 3),
                    "same_account": sc[u["unit_id"]].get("account")
                    == sc[worst["unit_id"]].get("account")})
    typical_calls = [e["account_read_ms"] / 1000 for e in ex.values()
                     if e.get("account_read_ms")]
    outlier = {
        "unit_id": worst["unit_id"], "lag_s": round(lag5[worst["unit_id"]], 3),
        "classification": "VERIFIED mechanism, UNKNOWN underlying cause",
        "verified": [
            f"one poll (poll_count {worst['polls']}); the GET itself took "
            f"{worst['get_total']:.3f} s of the {lag5[worst['unit_id']]:.3f} s lag; the "
            f"run had finished {worst['loop'] + BASELINE_S - worst['finish']:.3f} s before "
            f"that GET was sent (loop start + 5 s)",
            f"SDK: calls {sc[worst['unit_id']]['sdk']['calls']}, requests "
            f"{sc[worst['unit_id']]['sdk']['requests']} -> {worst['resend']} re-sends, "
            f"{sc[worst['unit_id']]['sdk']['rate_limit_errors']} rate-limited (429)",
            f"with the SDK's timeouts (5 s, doubled per attempt) and backoff (<= 1 s, "
            f"<= 2 s) one GET lasting {worst['get_total']:.3f} s needs >= "
            f"{min_attempts(worst['get_total'])} attempts, so both re-sends were inside "
            f"this GET (start {worst['start_ms']} ms and dataset read normal)",
            f"not C2 buffering: buffered_wait_ms "
            f"{sc[worst['unit_id']]['buffered_wait_ms']}; not the provider: runtime "
            f"{worst['run_time_s']} s, provider status SUCCEEDED",
            "a different request on a different client and thread stalled over the same "
            "window and ended within a second of it (concurrent_stalls)"],
        "inferred": [
            "both failed attempts ran to (or near) their 5 s and 10 s timeouts: a fast "
            "5xx could not fill 18.6 s",
            "the stall was upstream of both clients (network path or provider API), since "
            "they share no client or connection pool, only the account token"],
        "unknown": ["what stalled the API or network for ~18 s",
                    "whether a shorter interval would have dodged it (the stall's start is "
                    "not recorded; the simulation keeps the stall on the terminal GET)"],
        "concurrent_stalls": concurrent,
        "typical_account_read_s": dist(typical_calls),
        "units_with_sdk_resends": stalls,
        "note": "impit's stub calls `timeout` a per-request timeout; the >= 3 attempt "
                "bound treats it as a total bound (INFERRED from the stub, the binary is "
                "compiled)."}
    return {
        "schema": "search-v2-d4.1",
        "status": "OFFLINE simulation from the C5 run's clocks; no paid call. Every "
                  "interval other than 5 s is SIMULATED/INFERRED, never measured.",
        "generated_from": names,
        "evidence_labels": {
            "engine_polling": "VERIFIED (scraper.py, apify_client/_consts.py read as text)",
            "measured_lags_and_polls": "MEASURED",
            "clock_offset_and_calibration": "INFERRED (fitted, then checked against the "
                                            "independent start-window bound)",
            "lags_at_1_to_4_s": "INFERRED (simulated)",
            "segment_wall_changes": "INFERRED (scheduler model calibrated to the measured "
                                    "5 s segments)",
            "rate_limits": "MEASURED at 5 s only; provider limits UNKNOWN offline",
            "polls_cost": "VERIFIED (settled == priced events; no event is a poll)",
            "outlier": "VERIFIED mechanism / INFERRED detail / UNKNOWN cause"},
        "engine_polling": dict(facts, first_poll="interval after the loop starts, which is "
                               "the start acknowledgment plus client.run() and one "
                               "telemetry write (ms)",
                               sdk_semantics="RunClient.get() is a plain GET "
                               "(timeout tier 'short'), NOT the long-polling "
                               "waitForFinish; re-sent on transport errors, 5xx and 429 "
                               "with jittered exponential backoff, timeout doubling"),
        "model": {
            "poll_k_sent_at": "loop_start + k*interval + (k-1)*g",
            "detects_when": "sent_at + alpha*g >= provider finish (engine clock)",
            "g": "the unit's measured mean GET latency; a unit with SDK re-sends gets the "
                 "provider's typical GET for every poll and its measured excess on the "
                 "terminal one, at every interval (conservative)",
            "typical_get_s": {p: round(v, 3) for p, v in typical.items()},
            "clock_offset_s_engine_minus_provider": delta, "alpha": alpha,
            "clock_offset_feasible_from_start_windows_s": [round(lo, 3), round(hi, 3)],
            "unit_wall_change": "exec shortens by the detection gain; integration (account "
                                "read, checkpoint) unchanged",
            "calibration": calib},
        "simulation": table,
        "extra_poll_requests_per_90_search_plan": per_plan,
        "rate_limit_evidence": {
            prov: {"sdk_calls": sum(p["sdk"]["calls"] for p in pts if p["provider"] == prov),
                   "sdk_requests": sum(p["sdk"]["requests"] for p in pts
                                       if p["provider"] == prov),
                   "sdk_resends": sum(p["resend"] for p in pts if p["provider"] == prov),
                   "rate_limited_429": sum(p["sdk"]["rate_limit_errors"] for p in pts
                                   if p["provider"] == prov),
                   "poll_gets_measured": sum(p["polls"] for p in pts if p["provider"] == prov)}
            for prov in providers} | {
            "note": "0 rate-limited (429) responses at 5 s. The provider's published API rate limit was not "
                    "read (offline): UNKNOWN. At 1 s, two workers poll at most ~1.5 GET/s."},
        "outlier": outlier,
        "c5_fact_checks": {
            prov: {"poll_detection_delay_s": calib[prov]["measured_lag_s"],
                   "provider_runtime_s": dist([p["run_time_s"] for p in pts
                                               if p["provider"] == prov]),
                   "unit_wall_s": dist([p["unit_wall_s"] for p in pts
                                        if p["provider"] == prov]),
                   "terminal_poll_stamp_vs_dataset_read_s": round(max(
                       abs(p["det"] - p["dataset_start"]) for p in pts
                       if p["provider"] == prov), 3)}
            for prov in providers},
        "decision": decide(table, pts, providers)}


def decide(table, pts, providers):
    """Per provider, from the simulation: what each interval buys per extra GET."""
    row = lambda p, i: table[p][str(i)]
    gets = {p: sum(x["polls"] for x in pts if x["provider"] == p) for p in providers}
    calls = {p: sum(x["sdk"]["calls"] for x in pts if x["provider"] == p) for p in providers}
    eff = {p: {str(i): (round(-row(p, i)["segment_wall_change_s_inferred"]
                              / row(p, i)["extra_polls_provider_total"], 3)
                        if row(p, i)["extra_polls_provider_total"] else None)
               for i in INTERVALS if i != BASELINE_S} for p in providers}
    li2, li3, li4 = row("linkedin", 2), row("linkedin", 3), row("linkedin", 4)
    in1, in2, in3, in4, in5 = (row("indeed", i) for i in INTERVALS)
    fin = dist([x["finish"] - x["loop"] for x in pts if x["provider"] == "indeed"])
    li_run = dist([x["run_time_s"] for x in pts if x["provider"] == "linkedin"])
    return {
        "linkedin": "keep 5 s",
        "indeed": "change to 2 s, confirmed on the next live run (not 3 s, not 1 s)",
        "labels": "the gains are INFERRED (simulated on C5's clocks); nothing below 5 s "
                  "was measured",
        "segment_s_saved_per_extra_get": eff,
        "poll_gets_measured_at_5s": gets, "sdk_calls_measured_at_5s": calls,
        "indeed_finish_after_loop_start_s": fin,
        "reasons": [
            f"LinkedIn (hypothesis holds, weakly): the best alternative, 2 s, saves "
            f"{-li2['segment_wall_change_s_inferred']} s of a "
            f"{table['linkedin']['5']['segment_wall_s_inferred']} s segment for "
            f"{li2['extra_polls_provider_total']} extra GETs (+"
            f"{round(100 * li2['extra_polls_provider_total'] / gets['linkedin'])}% of its "
            f"poll GETs, +{round(100 * li2['extra_polls_provider_total'] / calls['linkedin'])}"
            f"% of its SDK calls); 3 s saves {-li3['segment_wall_change_s_inferred']} s and 4 s is "
            f"worse ({li4['segment_wall_change_s_inferred']:+} s). Runs of {li_run['min']:.0f}-{li_run['max']:.0f} s put the "
            f"finish anywhere in the cycle, so lag falls only ~linearly with the interval.",
            f"Indeed at 2 s: median lag {in5['lag_s']['median']} -> "
            f"{in2['lag_s']['median']} s, p95 {in5['lag_s']['p95']} -> "
            f"{in2['lag_s']['p95']} s, segment {in2['segment_wall_change_s_inferred']:+} s "
            f"of {in5['segment_wall_s_inferred']} s, for {in2['extra_polls_provider_total']} "
            f"extra GETs per plan (+{round(100 * in2['extra_polls_provider_total'] / gets['indeed'])}"
            f"% of its poll GETs, +{round(100 * in2['extra_polls_provider_total'] / calls['indeed'])}"
            f"% of its SDK calls), peak {in2['peak_poll_rate_per_s_2_workers']} GET/s "
            f"on 2 workers, $0 (polls are not charged events).",
            f"Indeed at 3 s is WORSE than 5 s ({in3['segment_wall_change_s_inferred']:+} s, "
            f"median lag {in3['lag_s']['median']} s) and 4 s is neutral "
            f"({in4['segment_wall_change_s_inferred']:+} s): Indeed runs finish "
            f"{fin['min']}-{fin['p95']} s (p95) after the loop starts, so a 3 s poll lands "
            f"just before most finishes and pays a second cycle. 5 s works today by "
            f"coincidence with that runtime; 2 s's lag (~U(0, 2) + GET) does not depend on it.",
            f"Not 1 s: {in1['segment_wall_change_s_inferred']:+} s for "
            f"{in1['extra_polls_provider_total']} extra GETs -- more gain, but each extra "
            f"GET buys less ({eff['indeed']['1']} vs {eff['indeed']['2']} s) and it "
            f"triples the poll traffic for a run that lasts ~4 s.",
            "No interval removes the Indeed tail: the p95/max are request stalls inside "
            "one GET (SDK timeouts 5 s then 10 s); the simulation keeps them at every "
            "interval."],
        "verify_live": "on the next paid run: Indeed detection lag median/p95, poll GETs "
                       "per run, SDK re-sends and 429s at 2 s",
        "separate_lever": "the Indeed tail is set by the poll GET's timeout escalation, not "
                          "the interval -- a separate, measured change if ever wanted."}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default="output/c5-run")
    ap.add_argument("--evidence-dir", default="docs/search-v2-evidence")
    ap.add_argument("--out-dir", default="docs/search-v2-evidence")
    args = ap.parse_args()
    root = Path(__file__).resolve().parent.parent
    rec, rows, settle, names = load(args.run_dir, args.evidence_dir)
    facts = engine_facts(root)
    body3 = d3(rec, rows, settle, names, args.evidence_dir)
    body4 = d4(rec, names, facts)
    out = Path(args.out_dir)
    for name, body in (("d3-indeed-location-analysis.json", body3),
                       ("d4-polling-simulation.json", body4)):
        (out / name).write_text(json.dumps(body, indent=2, default=str) + "\n")
    print(json.dumps({"d3_validation": body3["engine_validation"],
                      "d4_calibration": {p: {k: c[k] for k in (
                          "poll_count_mismatches", "units")}
                          for p, c in body4["model"]["calibration"].items()}}, indent=1))


if __name__ == "__main__":
    main()
