"""V2-C0: developer tooling cannot spend Apify credit without two deliberate keys.

DEVELOPER TOOLING ONLY. Production (the worker, the local console, a user's
own `python scraper.py`) never imports this file, and scraper.py does not know
it exists. See docs/search-engine-v2-c0-paid-dev-safety.md.

A developer tool that runs the engine runs it as

    python -m bench.paid_guard [--allow-paid] [--max-usd X] [--exposed-usd Y] \\
        -- <scraper.py arguments>

which is what engine_argv() builds. That child loads the profile once, asks
the engine's own `--dry-run --json` what this exact invocation would run, and
only if the plan holds a paid search does it print a preflight and require
BOTH keys:

    --allow-paid                 on its own command line
    SWEEP_ALLOW_PAID_BENCH=1     in its environment

and, when --max-usd is given, a provider-enforced worst case that fits under
it. A token, a .env, a paid profile or the default SITES is not permission:
being able to authenticate is not being authorised to spend. The plan checked
is the plan that runs (one process, one config load), and the engine's
credential step is wrapped, so a paid branch the preflight did not authorise
cannot obtain a token.
"""
import argparse
import contextlib
import io
import json
import os
import sys
from decimal import Decimal

FLAG = "SWEEP_ALLOW_PAID_BENCH"
BLOCKED = "Paid benchmark execution blocked."
HOW = (f"To intentionally permit paid developer execution, pass --allow-paid "
       f"AND set {FLAG}=1. Both are required; an APIFY_TOKEN, a .env or a "
       f"paid profile is not permission.")


class PaidBenchBlocked(SystemExit):
    """Exits the tool. A SystemExit so the engine's per-search
    `except Exception` can never swallow it."""


def env_allows(env=None):
    """The repository's flag convention (telemetry, shadow, concurrency)."""
    env = os.environ if env is None else env
    return env.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def require_paid_bench_permission(cli_allow_paid, env=None):
    """Return only when both keys are present. Never reads a token."""
    missing = [key for key, present in (
        ("--allow-paid", cli_allow_paid is True),
        (f"{FLAG}=1", env_allows(env))) if not present]
    if missing:
        raise PaidBenchBlocked(f"{BLOCKED} Missing: {' and '.join(missing)}.\n{HOW}")


def engine_argv(scraper_args, allow_paid=False, max_usd=None, exposed_usd=None,
                python=None):
    """How a developer tool launches the engine. `allow_paid` forwards the
    tool's own --allow-paid; the environment key is inherited, never
    synthesised, so a tool that drops either key produces a child that
    refuses."""
    return [python or sys.executable, "-u", "-m", "bench.paid_guard",
            *(["--allow-paid"] if allow_paid is True else []),
            *(["--max-usd", str(max_usd)] if max_usd is not None else []),
            *(["--exposed-usd", str(exposed_usd)] if exposed_usd is not None else []),
            "--", *scraper_args]


def paid_plan(scraper, engine):
    """{site: {actor, starts, depth, ceiling_usd}} for every site with at
    least one planned search, from the engine's own --dry-run --json on the
    already-loaded config. ceiling_usd None means no provider-side ceiling."""
    out, argv = io.StringIO(), sys.argv
    sys.argv = ["scraper.py", *engine, "--dry-run", "--json"]
    try:
        with contextlib.redirect_stdout(out):
            scraper.main()
    finally:
        sys.argv = argv
    plan = json.loads(out.getvalue().strip().splitlines()[-1])
    return {site: {"actor": scraper.SITES[site]["actor"],
                   "starts": len(searches),
                   "depth": plan["max_results"][site],
                   "ceiling_usd": scraper.max_charge_usd(
                       site, plan["max_results"][site])}
            for site, searches in plan["sites"].items() if searches}


def worst_case(plan):
    """Starts x provider ceiling, summed; None if any site is unbounded."""
    if any(p["ceiling_usd"] is None for p in plan.values()):
        return None
    return sum((p["starts"] * p["ceiling_usd"] for p in plan.values()), Decimal(0))


def preflight(plan, max_usd=None, exposed_usd=Decimal(0)):
    total = worst_case(plan)
    lines = ["", "PAID PREFLIGHT — nothing has been started"]
    for site, p in plan.items():
        cap = ("NONE — not provider-bounded" if p["ceiling_usd"] is None
               else f"${p['ceiling_usd']} per start, provider-enforced")
        lines.append(f"  {site:<9} {p['actor']:<38} {p['starts']:>3} start(s)  "
                     f"depth {p['depth']:<3} ceiling {cap}")
    lines.append(f"  worst case this run: "
                 + ("UNBOUNDED" if total is None else f"${total}"))
    if max_usd is not None:
        after = "UNBOUNDED" if total is None else f"${exposed_usd + total}"
        lines.append(f"  exposure before: ${exposed_usd}   after: {after}   "
                     f"limit (--max-usd): ${max_usd}")
    return "\n".join(lines) + "\n"


def authorize(plan, cli_allow_paid, max_usd=None, exposed_usd=Decimal(0), env=None):
    """The gate: both keys, then the exposure limit when one was given."""
    require_paid_bench_permission(cli_allow_paid, env)
    if max_usd is None:
        return
    total = worst_case(plan)
    if total is None:
        raise PaidBenchBlocked(
            f"{BLOCKED} A planned site has no provider-side charge ceiling, so "
            f"its worst case cannot be held under --max-usd ${max_usd}.")
    if exposed_usd + total > max_usd:
        raise PaidBenchBlocked(
            f"{BLOCKED} Worst case ${total} on top of ${exposed_usd} already "
            f"exposed exceeds --max-usd ${max_usd}.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m bench.paid_guard")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-usd", type=Decimal)
    ap.add_argument("--exposed-usd", type=Decimal, default=Decimal(0))
    ap.add_argument("engine", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    engine = args.engine[1:] if args.engine[:1] == ["--"] else args.engine

    saved = sys.argv
    sys.argv = ["scraper.py", *engine]      # config reads --profile at import
    try:
        import scraper
        authorized = False
        if not {"--dry-run", "--demo"} & set(engine):   # both return before any client
            plan = paid_plan(scraper, engine)
            if plan:
                print(preflight(plan, args.max_usd, args.exposed_usd), flush=True)
                authorize(plan, args.allow_paid, args.max_usd, args.exposed_usd)
                authorized = True

        # Both credential steps: the single account's, and V2-C4.5's pool.
        real = {name: getattr(scraper, name)
                for name in ("_require_token", "_require_token_pool")}

        def credential_step(step):
            def guarded():
                # The engine calls this only when its plan is paid, before any
                # client exists. Unauthorised here means the run went paid
                # although the plan checked above did not.
                if not authorized:
                    raise PaidBenchBlocked(
                        f"{BLOCKED} The engine reached its credential step without "
                        f"an authorised paid plan.\n{HOW}")
                return step()
            return guarded
        for name, step in real.items():
            setattr(scraper, name, credential_step(step))
        try:
            scraper.main()
        finally:
            for name, step in real.items():
                setattr(scraper, name, step)
    finally:
        sys.argv = saved


if __name__ == "__main__":
    main()
