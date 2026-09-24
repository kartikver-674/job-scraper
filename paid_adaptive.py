"""Search Engine V2-C4 — adaptive paid execution, evaluated and never enforced.

    SWEEP_PAID_ADAPTIVE_MODE = off (default) | shadow | enforce

Three things, kept apart:

  OBSERVATION  after each paid search is integrated — in PLAN order, on the
               thread that integrates it — what it added: counts, top-K
               movement, a provider-position summary. Read off the checkpoint
               the engine makes there anyway, so only what the sweep had then.
  DECISION     each candidate policy is handed the observations up to the one
               in hand and nothing later (fires() gets a prefix), and answers
               continue / would_stop / would_skip / would_use_depth.
  EVALUATION   once the result is final, every decision is priced against it:
               what it would have avoided AND what it would have lost.

ENFORCEMENT does not exist. No candidate has passed the evidence gate
(docs/search-engine-v2-c4-adaptive-paid-execution.md §11), so `enforce` runs
as `shadow`, and `shadow` runs as `off`: the same starts, inputs, depths,
reservations, requests, rows and bytes. This module reads the rows it is
handed and never adds a key to one; it returns one telemetry section, and it
needs SWEEP_SEARCH_V2_TELEMETRY on, since without a record there is nowhere
to write it.

Deterministic and offline: no model call, no network, and no clock in a
decision. bench/search_v2_paid_adaptive.py runs these same functions on
fixtures and captured traces.

Privacy: job keys (company + title) are held in memory to compare rankings
and never leave this module. The section holds unit ids, fingerprints,
counts and amounts.
"""
import os
import time
from collections import Counter
from decimal import Decimal

from telemetry import PAID_UNIT, PROVIDER_POSITION
from telemetry import position_bucket as bucket

MODE_ENV = "SWEEP_PAID_ADAPTIVE_MODE"
MODES = ("off", "shadow", "enforce")
POLICY_VERSION = "c4.1"
SCHEMA = "search-v2c4.1"
TOP_K = (10, 20, 50)
# Provider-position prefixes evaluated against the plan's own depth.
DEPTHS = (5, 10)
# Candidate ids that have passed the evidence gate. Empty: ENFORCE has
# nothing to enforce, which is why it runs as shadow.
PROMOTED = ()

# A SENSITIVITY GRID, not proposals: each family at points spanning what a
# reviewer would ask about, so a report shows how the loss moves with the
# setting. None of these numbers is an enforcement threshold (§11).
CANDIDATES = (
    ("marginal_streak", {"n": 2}),
    ("marginal_streak", {"n": 3}),
    ("marginal_streak", {"n": 5}),
    ("top_k_stable", {"k": 10, "m": 3}),
    ("top_k_stable", {"k": 20, "m": 3}),
    ("top_k_stable", {"k": 20, "m": 5}),
    ("provider_marginal_streak", {"n": 3}),
    ("provider_marginal_streak", {"n": 5}),
    ("remote_after_places", {"m": 1}),
    ("remote_after_places", {"m": 2}),
    ("depth_prefix", {"d": 5, "m": 3}),
    ("depth_prefix", {"d": 10, "m": 3}),
)


def mode(env=None):
    """The requested mode. Anything unrecognised is off, the flags' rule."""
    raw = (os.environ if env is None else env).get(MODE_ENV, "").strip().lower()
    return raw if raw in MODES else "off"


def candidate_id(family, params):
    return family + ":" + ",".join(f"{k}={v}" for k, v in params.items())


# ===========================================================================
# Ranking and counterfactuals — on projections, never on row content
# ===========================================================================
# A projection row is (origin, key, score, position): origin a paid unit id or
# None for a free source, key the job identity (an unkeyed row gets a key of
# its own, so it is never collapsed — dedupe's rule), position the provider's.
def rank(rows):
    """scraper.rank_rows on a projection: best score first, ties in arrival
    order (a stable sort, as rank_rows'), then the first row per key. Pinned
    equal to rank_rows by test_paid_adaptive."""
    out, seen = [], set()
    for origin, key, _score, position in sorted(rows, key=lambda t: t[2], reverse=True):
        if key not in seen:
            seen.add(key)
            out.append((origin, key, position))
    return out


def loss(full, cf, truth):
    """What a counterfactual result lacks that the real one had. Counted by
    job, never by row: a job two units returned is one job."""
    keys = [k for _, k, _ in full]
    kept = {k for _, k, _ in cf}
    cf_keys = [k for _, k, _ in cf]
    lost = [k for k in keys if k not in kept]          # rank order
    origins = {}
    for origin, key, _, _ in truth:
        origins.setdefault(key, set()).add(origin)
    out = {"final_lost": len(lost),
           # C1's marginal: one paid unit had it and no free source did.
           "final_marginal_lost": sum(1 for k in lost if len(origins[k]) == 1
                                      and None not in origins[k])}
    for top in TOP_K:
        out[f"top{top}_lost"] = len(set(keys[:top]) - set(cf_keys[:top]))
    out["best_lost_rank"] = keys.index(lost[0]) + 1 if lost else None
    return out


def counterfactual(truth, keep):
    """The result had only the rows keep(origin, position) admits been bought
    — every other rule (score, filters, ranking, dedupe) unchanged."""
    return rank([t for t in truth if keep(t[0], t[3])])


# ===========================================================================
# Observation — decision-time state, one per integrated paid search
# ===========================================================================
def observe(state, unit, raw, eligible, final, started, committed_usd, elapsed_ms,
            ok=True):
    """What `unit` added, read off the checkpoint made right after it.

      raw       [(key, position)] the unit's own rows, dataset order
      eligible  [(origin, key, score, position)] every eligible row at this
                checkpoint, arrival order
      final     [(origin, key, position)] the checkpoint's result, rank order
      state     carried from call to call: keys acquired, the previous top-K

    Only this checkpoint and what came before it: nothing here can see a unit
    not yet integrated or the free sources, which run after the paid phase.
    """
    uid = unit["unit_id"]
    origins = {}
    for origin, key, _, _ in eligible:
        origins.setdefault(key, set()).add(origin)
    owner = {k: o for o, k, _ in final}
    mine_final = [(k, p) for o, k, p in final if o == uid]
    marginal = [(k, p) for k, p in mine_final if origins.get(k) == {uid}]
    mine_eligible = [s for o, _, s, _ in eligible if o == uid]
    acquired = state.setdefault("acquired", set())
    repeat = sum(1 for k, _ in raw if k in acquired)
    acquired.update(k for k, _ in raw)
    top = {}
    for size in TOP_K:
        now = [k for _, k, _ in final[:size]]
        before = state.get(size, [])
        was = {k: i for i, k in enumerate(before)}
        entered = [k for k in now if k not in was]
        top[str(size)] = {
            "entered": len(entered),
            "displaced": len(set(was) - set(now)),
            "moved": sum(1 for i, k in enumerate(now) if k in was and was[k] != i),
            "entered_from_unit": sum(1 for k in entered if owner[k] == uid),
            "entered_marginal": sum(1 for k in entered
                                    if owner[k] == uid and origins.get(k) == {uid})}
        state[size] = now
    return {
        "unit_id": uid, "plan_index": unit["plan_index"], "provider": unit["provider"],
        "location_mode": unit.get("location_mode"), "keyword_fp": unit.get("keyword_fp"),
        "requested_depth": unit.get("requested_depth"),
        "status": "completed" if ok else "failed",
        "raw": len(raw),
        "eligible": len(mine_eligible),
        "eligible_positive": sum(1 for s in mine_eligible if s > 0),
        "final": len(mine_final),
        "final_marginal": len(marginal),
        "acquired_repeat_of_earlier_paid": repeat,
        "positions_known": sum(1 for _, p in raw if p is not None),
        "final_by_position": dict(Counter(bucket(p) for _, p in mine_final)),
        "marginal_by_position": dict(Counter(bucket(p) for _, p in marginal)),
        "top": top,
        "started": started,
        "committed_usd": str(committed_usd),
        "elapsed_ms": elapsed_ms,
    }


# ===========================================================================
# Decision — pure functions of a PREFIX of the observations
# ===========================================================================
def _streak(history, test):
    n = 0
    for o in reversed(history):
        if not test(o):
            break
        n += 1
    return n


def _completed(history, provider=None):
    return [o for o in history if o["status"] == "completed"
            and (provider is None or o["provider"] == provider)]


def _beyond(o, depth):
    """This unit's marginal rows past `depth` or at an unknown position."""
    return sum(n for b, n in o["marginal_by_position"].items()
               if b == "unknown" or int(b.split("-")[0]) > depth)


def fires(family, params, history):
    """(action, reason_code, scope, evidence) at the decision point after
    history[-1], or None to continue. `history` ends at the unit in hand."""
    last = history[-1]
    if last["status"] != "completed":
        return None
    p = last["provider"]
    if family == "marginal_streak":
        n = _streak(_completed(history), lambda o: o["final_marginal"] == 0)
        if n >= params["n"]:
            return "would_stop", "zero_marginal_streak", None, {"streak": n}
    elif family == "top_k_stable":
        key = str(params["k"])
        n = _streak(_completed(history), lambda o: o["top"][key]["entered"] == 0)
        if n >= params["m"]:
            return "would_stop", f"top{key}_unchanged_streak", None, {"streak": n}
    elif family == "provider_marginal_streak":
        n = _streak(_completed(history, p), lambda o: o["final_marginal"] == 0)
        if n >= params["n"]:
            return "would_skip", "provider_zero_marginal_streak", {"provider": p}, {
                "streak": n}
    elif family == "remote_after_places":
        redundant = [o for o in _completed(history, p)
                     if o["location_mode"] == "remote" and o["final_marginal"] == 0]
        if len(redundant) >= params["m"]:
            return "would_skip", "remote_twin_redundant_before", {
                "provider": p, "location_mode": "remote"}, {"redundant_remote": len(redundant)}
    elif family == "depth_prefix":
        d = params["d"]
        if last["requested_depth"] is None or d >= last["requested_depth"]:
            return None
        n = _streak(_completed(history, p),
                    lambda o: o["positions_known"] == o["raw"] and _beyond(o, d) == 0)
        if n >= params["m"]:
            return "would_use_depth", f"nothing_marginal_beyond_position_{d}", {
                "provider": p, "depth": d}, {"streak": n}
    return None


def simulate(family, params, observations):
    """Walk the sweep in plan order as an online policy would: at each point
    the policy sees observations[:i + 1] and nothing after. A stop ends it;
    a skip or a depth fires once per provider."""
    decisions, fired = [], set()
    for i, o in enumerate(observations):
        if "stop" in fired:
            break
        got = fires(family, params, observations[:i + 1])
        if got is None:
            continue
        action, reason, scope, evidence = got
        tag = "stop" if action == "would_stop" else (scope or {}).get("provider")
        if tag in fired:
            continue
        fired.add(tag)
        decisions.append({"decision_point": f"after {o['unit_id']}", "unit_id": o["unit_id"],
                          "plan_index": o["plan_index"], "provider": o["provider"],
                          "started_at_decision": o["started"], "action": action,
                          "reason_code": reason, "scope": scope,
                          "evidence_counts": evidence, "actual_behavior": "executed"})
    return decisions


# ===========================================================================
# Evaluation — the decisions priced against the final result
# ===========================================================================
def _affected(decision, plan):
    """Planned units a decision would have avoided or cut: later in the plan
    and inside its scope."""
    scope = decision["scope"] or {}
    return [u for u in plan if u["plan_index"] > decision["plan_index"]
            and all(u.get(k) == v for k, v in scope.items() if k != "depth")]


def _savings(avoided, executed, started_at=None):
    ex = [u for u in avoided if u["unit_id"] in executed]
    records = [executed[u["unit_id"]] for u in ex]
    ceilings = [Decimal(u["charge_ceiling_usd"]) for u in ex if u.get("charge_ceiling_usd")]
    return {
        "logical_avoided": len(ex),
        # Under concurrency the searches already sent at the decision point
        # were running: only the rest could have been avoided as scheduled.
        "avoidable_as_scheduled": (len(ex) if started_at is None else
                                   sum(1 for u in ex if u["plan_index"] >= started_at)),
        "physical_starts_avoided": sum(1 for r in records if r.get("actor_run_id")),
        "max_exposure_avoided_usd": str(sum(ceilings, Decimal(0))),
        "unbounded_avoided": len(ex) - len(ceilings),
        # Terminal-poll readings: provisional, never final (V2-C1).
        "provisional_cost_avoided_usd": round(sum(
            o["usd"] for r in records for o in r.get("cost_observations") or ()
            if o.get("source") == "run_record_at_completion"), 6),
        "unit_wall_ms_avoided": sum(r.get("duration_ms") or 0 for r in records),
    }


def _depth_cut(units, depth, ceiling_at, truth):
    """Rows at a known provider position <= depth kept for `units`; an
    unknown position is counted as NOT kept, so the loss is an upper bound."""
    ids = {u["unit_id"] for u in units}

    def keep(origin, position):
        return origin not in ids or (position is not None and position <= depth)
    saved = Decimal(0)
    for u in units:
        if u.get("charge_ceiling_usd") and ceiling_at is not None:
            alt = ceiling_at(u["provider"], depth)
            if alt is not None:
                saved += Decimal(u["charge_ceiling_usd"]) - Decimal(alt)
    beyond = sum(1 for o, _, _, p in truth if o in ids and (p is None or p > depth))
    return keep, {"units_cut": len(units), "eligible_rows_not_bought": beyond,
                  "max_exposure_avoided_usd": str(saved)}


def evaluate(plan, observations, truth, executed, ceiling_at=None):
    """The whole counterfactual report.

      plan          every planned paid unit (telemetry's paid_units entries)
      observations  observe()'s records, plan order
      truth         [(origin, key, score, position)] the FINAL pass's
                    eligible rows, paid and free, arrival order
      executed      unit_id -> the unit's execution record (duration, run id,
                    cost readings), for units that ran
      ceiling_at    (provider, depth) -> the provider ceiling at that depth
    """
    full = rank(truth)
    ran = [o["unit_id"] for o in observations]
    by_id = {u["unit_id"]: u for u in plan}

    per_unit = []
    for i, uid in enumerate(ran):
        later = set(ran[i + 1:])
        per_unit.append({
            "unit_id": uid,
            "if_stopped_after": dict(
                loss(full, counterfactual(truth, lambda o, p: o not in later), truth),
                **_savings([by_id[u] for u in ran[i + 1:]], executed)),
            "if_skipped_alone": loss(full, counterfactual(
                truth, lambda o, p, uid=uid: o != uid), truth)})

    origins = {}
    for o, k, _, _ in truth:
        origins.setdefault(k, set()).add(o)
    top = {size: {k for _, k, _ in full[:size]} for size in TOP_K}
    depth = {}
    for provider in sorted({u["provider"] for u in plan}):
        units = [by_id[u] for u in ran if by_id[u]["provider"] == provider]
        ids = {u["unit_id"] for u in units}
        rows = [t for t in truth if t[0] in ids]
        if not rows or all(t[3] is None for t in rows):
            depth[provider] = {"status": "UNKNOWN", "reason": (
                "no provider position on these rows; dataset order is the actor's "
                "push order, not a rank")}
            continue
        requested = max(u.get("requested_depth") or 0 for u in units)
        finals = [(o, k, p) for o, k, p in full if o in ids]      # survivors only
        out = depth[provider] = {
            "status": "MEASURED", "requested_depth": requested,
            "unknown_position_rows": sum(1 for t in rows if t[3] is None),
            "note": "a prefix of the provider's own positions; that a run at the "
                    "lower depth returns exactly these rows is NOT proven"}
        for d in sorted({*DEPTHS, requested}):
            if d > requested:
                continue
            mine = [t for t in rows if t[3] is not None and t[3] <= d]
            won = [(o, k) for o, k, p in finals if p is not None and p <= d]
            entry = {"eligible": len(mine),
                     "eligible_positive": sum(1 for t in mine if t[2] > 0),
                     "final": len(won),
                     "final_marginal": sum(1 for o, k in won if origins[k] == {o}),
                     **{f"top{s}": sum(1 for _, k in won if k in top[s]) for s in TOP_K}}
            if d < requested:
                keep, saved = _depth_cut(units, d, ceiling_at, truth)
                entry.update(loss=loss(full, counterfactual(truth, keep), truth),
                             savings=saved)
            out[str(d)] = entry

    pairs = []
    for u in (by_id[x] for x in ran):
        if u.get("location_mode") != "remote":
            continue
        places = [by_id[x] for x in ran if by_id[x]["provider"] == u["provider"]
                  and by_id[x].get("keyword_fp") == u.get("keyword_fp")
                  and by_id[x].get("location_mode") == "place"
                  and by_id[x]["plan_index"] < u["plan_index"]]
        if not places:
            continue
        place_keys = {k for o, k, _, _ in truth if o in {p["unit_id"] for p in places}}
        remote_keys = {k for o, k, _, _ in truth if o == u["unit_id"]}
        pairs.append({"provider": u["provider"], "keyword_fp": u.get("keyword_fp"),
                      "place_units": [p["unit_id"] for p in places],
                      "remote_unit": u["unit_id"],
                      "remote_eligible_jobs": len(remote_keys),
                      "overlap_with_places": len(remote_keys & place_keys),
                      "if_remote_skipped": loss(full, counterfactual(
                          truth, lambda o, p, r=u["unit_id"]: o != r), truth)})

    candidates = []
    for family, params in CANDIDATES:
        decisions = simulate(family, params, observations)
        skipped, cut, depth_used = set(), [], {}
        for d in decisions:
            hit = [u for u in _affected(d, plan) if u["unit_id"] in executed]
            if d["action"] == "would_use_depth":
                cut.append((d["scope"]["depth"], hit))
                depth_used[d["provider"]] = d["scope"]["depth"]
            else:
                skipped |= {u["unit_id"] for u in hit}
            d["affects_units"] = len(hit)
            d["bounded_exposure_avoided_usd"] = str(sum(
                (Decimal(u["charge_ceiling_usd"]) for u in hit
                 if u.get("charge_ceiling_usd") and d["action"] != "would_use_depth"),
                Decimal(0)))
        keeps = [lambda o, p: o not in skipped]
        depth_savings = []
        for d, units in cut:
            keep, saved = _depth_cut(units, d, ceiling_at, truth)
            keeps.append(keep)
            depth_savings.append(saved)
        cf = counterfactual(truth, lambda o, p: all(k(o, p) for k in keeps))
        stop = next((d for d in decisions if d["action"] == "would_stop"), None)
        candidates.append({
            "candidate_id": candidate_id(family, params), "family": family,
            "params": params, "promoted": candidate_id(family, params) in PROMOTED,
            "decisions": decisions,
            "would_execute": len(ran) - len(skipped), "would_skip": len(skipped),
            "would_stop_after": stop["unit_id"] if stop else None,
            "would_use_depth": depth_used,
            "savings": dict(_savings([by_id[u] for u in skipped], executed,
                                     stop["started_at_decision"] if stop else None),
                            depth=depth_savings),
            "loss": loss(full, cf, truth)})

    return {"logical_planned": len(plan), "logical_executed": len(ran),
            "physical_starts": sum(1 for u in ran if (executed.get(u) or {}).get("actor_run_id")),
            "final_jobs": len(full), "observations": observations, "per_unit": per_unit,
            "depth": depth, "pairs": pairs, "candidates": candidates}


# ===========================================================================
# Offline: the live observation, replayed on a synthetic or captured sweep
# ===========================================================================
def replay(plan, unit_rows, free=(), executed=None, ceiling_at=None):
    """The live path's observations and evaluation for a sweep given as rows.

      unit_rows  unit_id -> [(key, score, eligible, position)], dataset order
      free       [(key, score)] eligible free rows, fetched after the paid phase

    The checkpoint after each unit is rank() over every eligible row so far —
    what finalize's pass there produces — so observe() sees exactly what it
    sees live."""
    state, eligible, observations = {}, [], []
    committed = Decimal(0)
    for i, u in enumerate(plan):
        rows = unit_rows.get(u["unit_id"])
        if rows is None:
            continue
        committed += Decimal(u.get("charge_ceiling_usd") or 0)
        eligible = eligible + [(u["unit_id"], k, s, p) for k, s, e, p in rows if e]
        observations.append(observe(state, u, [(k, p) for k, _, _, p in rows], eligible,
                                    rank(eligible), started=i + 1,
                                    committed_usd=committed, elapsed_ms=0))
    truth = eligible + [(None, k, s, None) for k, s in free]
    if executed is None:
        executed = {u["unit_id"]: {"actor_run_id": f"run_{u['unit_id']}"}
                    for u in plan if u["unit_id"] in unit_rows}
    return evaluate(plan, observations, truth, executed, ceiling_at)


# ===========================================================================
# Live: the engine's hooks
# ===========================================================================
_s = None       # this sweep's state, or None whenever the mode is off


def start(plan, budget=None, job_key=None):
    """Open for one sweep's paid phase and return the REQUESTED mode. `plan`
    is telemetry's paid_units list, None when telemetry is off — and then,
    as when the mode is off, nothing is held and every hook below is a
    single `is None` test."""
    global _s
    _s = None
    requested = mode()
    if requested != "off" and plan:
        _s = {"requested": requested, "plan": {u["unit_id"]: u for u in plan},
              "order": [u["unit_id"] for u in plan], "budget": budget,
              "job_key": job_key, "keys": {}, "last": ((), ()), "state": {},
              "obs": [], "held": Decimal(0), "error": None, "t0": time.monotonic()}
    return requested


def active():
    return _s is not None


def capture(eligible, final):
    """From finalize(): the lists its pass just built. Held, not copied —
    integrated() reads the latest, and the last one is the final pass."""
    if _s is not None:
        _s["last"] = (eligible, final)


def _key(row):
    cache = _s["keys"]
    key = cache.get(id(row))
    if key is None:
        # Row objects live in raw_rows for the whole sweep, so id() is stable.
        key = cache[id(row)] = _s["job_key"](row) or ("unkeyed", id(row))
    return key


def _proj(row):
    where = row.get(PAID_UNIT)
    return (where[0] if where else None, _key(row), row.get("score", 0),
            row.get(PROVIDER_POSITION))


def integrated(unit_id, rows, ok, started, committed_usd=None):
    """After paid unit `unit_id` is integrated — completed (its rows are in
    the checkpoint just made) or failed (they are not). `started`: planned
    searches sent so far. `committed_usd`: C2's committed exposure; the
    serial loop has none, so it is the ceilings of the searches run so far.

    Called inside the paid loop, so it cannot fail a search: an error ends
    the observation for this sweep and is reported in the section instead."""
    if _s is None or _s["error"]:
        return
    try:
        unit = _s["plan"][unit_id]
        _s["held"] += Decimal(unit.get("charge_ceiling_usd") or 0)
        eligible, final = _s["last"]
        _s["obs"].append(observe(
            _s["state"], unit,
            [(_key(r), r.get(PROVIDER_POSITION)) for r in (rows or ())],
            [_proj(r) for r in eligible],
            [(o, k, p) for o, k, _, p in map(_proj, final)],
            started, _s["held"] if committed_usd is None else committed_usd,
            round((time.monotonic() - _s["t0"]) * 1000), ok))
    except Exception as exc:
        _s["error"] = f"observation stopped at {unit_id}: {type(exc).__name__}"


def finish(record, ceiling_at=None):
    """The section, from the last finalize pass (the final result) and the
    telemetry record's execution facts; None when off. Clears the state, and
    never raises: the result is already written when this runs."""
    global _s
    if _s is None:
        return None
    s, header = _s, {"schema": SCHEMA, "policy_version": POLICY_VERSION,
                     "requested_mode": _s["requested"], "mode": "shadow",
                     "enforce_available": bool(PROMOTED), "promoted": list(PROMOTED),
                     "budget_usd": None if _s["budget"] is None else str(_s["budget"]),
                     "actual_behavior": "full plan executed; no decision was applied"}
    try:
        if s["error"]:
            return dict(header, error=s["error"])
        truth = [_proj(r) for r in s["last"][0]]
        executed = {u["unit_id"]: u for u in (record or {}).get("units") or ()
                    if u.get("path") == "paid" and u.get("unit_id")}
        return dict(header, **evaluate([s["plan"][u] for u in s["order"]], s["obs"],
                                       truth, executed, ceiling_at))
    except Exception as exc:
        return dict(header, error=f"evaluation skipped: {type(exc).__name__}")
    finally:
        _s = None
