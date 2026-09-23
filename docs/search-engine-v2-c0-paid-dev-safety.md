# V2-C0 — developer tooling cannot spend without two keys

Date: 2026-09-23. Baseline: `e48b820` (V2-B5). Production runs V2-B5 with
`SWEEP_SEARCH_V2_TELEMETRY=1`, Lever 1/4, Greenhouse 1/4,
`SWEEP_FREE_SOURCE_SHADOW=1` and `SWEEP_RESULTS_READY_EARLY=1`.
Scope: **safety of paid execution reached from developer, benchmark and audit
tooling.** Not paid-search optimisation, not a paid-engine change, not deployed.
No environment or deployment file changed. **`scraper.py`, the worker, the
console and every production module are byte-identical to `e48b820`.**

Evidence classes as in V2-A: **MEASURED** (a reproducible experiment or a dated
probe), **VERIFIED** (directly observed code or configuration), **INFERRED** (a
model with stated assumptions), **UNKNOWN** (evidence unavailable, which is not
the same as zero).

Nothing else moved: the registry (134 records, Postman included), Lever and
Greenhouse concurrency, B3's shadow evaluation, B5's readiness marker and
atomic writes, ranking, scoring, dedupe, `job_key`, Search Preferences,
recency, geography, arrangement, salary, and every paid path: B1's
`limitPerSource`/`count`/`maxTotalChargeUsd`, Indeed, Naukri, the budget guard.
`SWEEP_EXPERIENCE_MISMATCH_GUARD` remains off.

## 0. Headline findings

1. **VERIFIED — the incident had one gate, and it was authentication.** A
   temporary profile inherited the default paid `SITES`; `_require_token()`
   called `load_dotenv()`, which restored the token the shell had unset; with a
   token present, nothing else in the path asks whether anyone meant to spend
   (§1–§2).
2. **VERIFIED — the guard cannot live in `scraper.py`.** The same
   `python scraper.py --profile X --yes` is what the worker, the local console
   and a user at the terminal run. Gating it would change production. It lives
   where developer tooling meets the engine: every developer tool now launches
   the engine only through `bench/paid_guard.py` (§5).
3. **The rule.** A paid plan runs from developer tooling only with
   **`--allow-paid` AND `SWEEP_ALLOW_PAID_BENCH=1`**. Evaluated on the engine's
   own `--dry-run --json` of the exact invocation, **in the process that then
   runs it**, before any client, token or actor. A free plan needs neither key
   (§5–§8).
4. **MEASURED — the incident is reproduced and refused**, in-process and in real
   child processes: the same invocation spends (against a fake client) through
   `scraper.py`, and stops through the guard, with or without a token in the
   shell or restored from `.env` (§13).
5. **VERIFIED — Python socket denial does not stop apify-client 3.x.** It sends
   through `impit`, a Rust HTTP stack. What keeps the repository's tests from
   spending is their stub clients; C0's own tests never let a real credential
   into the process at all (§17).
6. **MEASURED — 14 targeted mutations, all caught**, each file restored
   byte-identical (§14).
7. **MEASURED — one authorised LinkedIn run confirmed B1's contract live**
   through the guarded path: Apify recorded `maxTotalChargeUsd = 0.046`, the
   actor received `limitPerSource = count = 15` and returned exactly 15 rows,
   and charged **$0.03005** (15 results × $0.002 + one $0.00005 start). That is
   the whole paid spend of this task: 1 actor start, $0.046 worst case, $0.50
   limit (§15–§16).

## 1. The B3 verification incident

From [V2-B3 §18](search-engine-v2-b3-shadow-production-evaluation.md): to check
the worker-shaped path, a temporary `profiles/b3e2e_*.py` overrode only
`ATS_BOARDS`, `FEEDS` and `SETTINGS`, and `scraper.py --profile … --yes` ran
with `APIFY_TOKEN` unset in the shell.

| | |
|---|---|
| Completed paid searches | **23**: 18 LinkedIn, 5 Indeed; possibly one more Indeed in flight when stopped |
| Estimated charge | ~$1.0–1.1 at published prices; upper bound ~$1.37 |
| Actual charge | UNKNOWN (process killed before its spend line; account not queried) |
| Shipped B3 code | could not reach paid execution; this was a verification-tool failure |

B3 then hardened its own harness (`--site free`, paid sites disabled, dry-run
pre-check, paid-marker kill). That protected one harness.

## 2. Root cause — and why unsetting APIFY_TOKEN did nothing

**VERIFIED** in `scraper.py`:

```python
enabled = resolve_sites(args)                 # SITES toggles: a profile that omits
plans = {s: plan_for_site(s, args) ...}       #   SITES inherits linkedin + indeed
...
if (not args.yes and ... and sys.stdin.isatty()):   # --yes, or no tty: no prompt
    input(...)
...
if plans:
    client = ApifyClient(_require_token())    # load_dotenv() inside
```

1. **Inheritance.** Profiles merge one level deep (`config._overlay`); a name a
   profile omits is inherited. `SITES` omitted = LinkedIn and Indeed enabled.
2. **No prompt.** `--yes` (or a non-tty stdin, i.e. any subprocess) skips the
   only confirmation.
3. **Dotenv.** `_require_token()` calls `load_dotenv()`, whose `find_dotenv()`
   walks up from the directory of the file that called it — `scraper.py`, so
   the repository root — whatever the caller's cwd (only an interactive session
   starts from the cwd instead). Unsetting the variable in the shell only makes
   `load_dotenv()` put it back: `override=False` skips variables that are
   *present*, not ones that were removed. (The same rule is why a helper script
   run from outside the repository finds no `.env` at all — observed during
   this stage's post-run checks.)
4. **Nothing else asks.** Once a token exists the paid loop starts. The budget
   guard bounds a sweep's size, not whether it was intended.

The one gate on the path was "can we authenticate". That is a different
question from "did someone mean to spend".

## 3. Authentication vs authorisation to spend

| | Authentication | Authorisation to spend (C0) |
|---|---|---|
| Question | can this process talk to Apify as someone? | did a developer deliberately decide this tool may spend? |
| Evidence | `APIFY_TOKEN*`, `.env`, a valid account, credit | `--allow-paid` **and** `SWEEP_ALLOW_PAID_BENCH=1` |
| Who decides | whoever configured the machine, once | the person running this invocation, now |
| Default | usually present on a developer machine | absent everywhere; never in `.env`, Render or Oracle |

C0 changes only the right-hand column. The token may stay in `.env`; dotenv
behaviour is unchanged; production token loading is unchanged. The guard
**never reads a token** — pinned by a test that records every key the decision
looks up and finds only `SWEEP_ALLOW_PAID_BENCH`.

## 4. Reachability audit

Every tracked Python file was classified by what it can reach. The table is
**test-backed**: `Reachability.test_every_file_that_can_reach_paid_code_is_classified`
scans every tracked non-test file for an engine launch (`"scraper.py"`,
`scraper.main(`, `engine_argv(`), the credential step (`_require_token(`), a
client (`ApifyClient(`), an actor start (`.start(run_input`, `.call(run_input`),
or a production launcher (`default_spawn`, `sweep.runs`, `runs.start`), and
fails on any match missing from the table — or any table entry that no longer
matches.

| Entry point | Class | Paid plan? | Token? | load_dotenv? | Client / actor? | Acknowledgement before C0 | Guard |
|---|---|---|---|---|---|---|---|
| `scraper.py` (CLI: user, worker, console) | **production** | yes | env or `.env` | yes, in `_require_token` | yes | user intent (`--yes`/prompt) | **none, by design** |
| `deploy/sweep_worker.py` `default_spawn` | **production** | the visitor's | BYOK in env; operator keys stripped | in the child | in the child | visitor's paid request | none |
| `sweep/runs.py` `start` (console) | **production** | the user's | env / `.env` | in the child | in the child | UI confirmation | none |
| `sweep/plan.py`, worker `/v1/plans` | production | dry run only | stripped (worker) | no | **no** — returns before any client | — | none needed |
| `sweep/app.py` | production | no | reads keys | `.env` read for the key picker | client for **account reads only** | — | none needed |
| `rescore_from_apify.py` | production (console) | no | yes | yes | reads finished runs' datasets; **starts nothing** | — | none needed |
| `auto-apply/make_shortlist.py --scrape` | production (user CLI) | the user's config | via the child | via the child | via the child | explicit `--scrape`, prints "uses Apify credits", engine prompt on a tty | none: a user's own sweep |
| `bench/search_v2_shadow_b3.py --sweep` | developer benchmark | could inherit | stripped, **then restored by the child's dotenv** | in the child | in the child | its own dry-run check (B3) | **guarded** (§12) |
| `bench/search_v2_paid_probe.py` (new) | developer benchmark | yes, by design | via the child | via the child | via the child | — | **guarded** |
| `bench/paid_guard.py` (new) | the guard | — | — | only after both keys | only after both keys | — | — |
| `bench/search_v2_results_ready.py` | developer benchmark | no: B5 harness, `--site free`, fixtures | no | no | stubbed | — | unreachable |
| `bench/search_v2_free_audit.py` | developer audit | no | no | no | no — parses `scraper.py`'s text | — | unreachable |
| `config.py`, `profiles/global_all.py` | — | — | — | — | — | — | unreachable (a self-test string, a comment) |
| every other `bench/`, `tools/`, `poc/` file | developer | no | several strip `APIFY_TOKEN*` | `adzuna_replay` (for Adzuna keys) | **no**: no engine entry, no client | — | unreachable |
| `sweep/tests/*`, `deploy/test_*` | test | some | stubbed / fixture | stubbed (`_require_token` patched) or fixture (C0) | fake clients | — | unreachable by stubs (§17) |

Every developer path that could reach paid code did so through one door: a
child `scraper.py`. **Can a subprocess lose caller intent?** Before C0, yes —
B3's harness stripped the token from its child's environment, and the child
restored it from `.env`. The parent's intent ("no paid") was not something the
child could see. After C0 the child *is* the check (§9).

## 5. Design

**One small module, `bench/paid_guard.py`, used by every developer tool that
runs the engine.** Production never imports it; nothing in `bench/` is imported
by production (pinned).

```
developer tool ──► engine_argv(args, allow_paid=<its own --allow-paid>)
                     = [python, -u, -m, bench.paid_guard, (--allow-paid), (--max-usd X), --, *args]
                           │  environment inherited: SWEEP_ALLOW_PAID_BENCH only if the developer set it
                           ▼
child: import scraper   ← config loads the profile ONCE
       plan = engine's own `--dry-run --json` of these args, in this process
       plan has a paid search?
         no  → run scraper.main(); the credential step stays locked
         yes → print PAID PREFLIGHT
               require --allow-paid AND SWEEP_ALLOW_PAID_BENCH=1   ─ else exit, nothing started
               with --max-usd: worst case must be provider-bounded and fit ─ else exit
               unlock the credential step; run scraper.main()
```

`require_paid_bench_permission(cli_allow_paid, env=None)` is the two-key rule
itself; `authorize()` adds the optional exposure limit; `main()` is the guarded
engine.

**The CLI key must be literally `True`**, and `engine_argv` forwards
`--allow-paid` only for `allow_paid is True`. **The environment key** follows the
repository's flag convention (telemetry, shadow, concurrency, readiness):
`1/true/yes/on`, case- and space-insensitive. `0`, `false`, `no`, `off`, empty,
`2`, `enabled`, `y`, `1.0` and anything else are denied.

**Rejected:** a check inside `scraper.main()` (changes production: the worker,
console and user CLI all run it); a production-side marker such as "launched by
the worker" (a production change, and a hand-typed dev run would lack it too);
removing or shadowing `.env` for tools (authentication is not the problem, and
the brief forbids it); a parent-only check (the parent's preflight and the
child's execution would be two config loads, and the gap between them is where
a plan can change).

## 6. The exact guard boundary

Inside the guarded child, in order:

1. `import scraper` — config and the profile load, once.
2. `paid_plan()` — `scraper.main()` with `--dry-run --json` appended, stdout
   captured. The engine's dry-run branch returns before telemetry, outputs,
   `.done_combos`, the credential step and any client.
3. Plan paid → **PAID PREFLIGHT printed**, then `authorize()`. A refusal is a
   `SystemExit` subclass, so the engine's per-search `except Exception` cannot
   swallow it.
4. `scraper._require_token` is wrapped. The engine calls it only when its plan
   is paid, **before** `ApifyClient(...)`, `account_usage_usd`, `actor.start` or
   any dataset read. Locked unless step 3 authorised; restored afterwards.
5. `scraper.main()`.

So an unauthorised developer run stops **before** `load_dotenv()` inside
`_require_token`, before `_token_headroom` (the limits read with several
tokens), before the client exists, before the first actor starts. Pinned by
`assertNothingPaidBegan`: no client built, no start, the credential step never
entered, no token restored, and the token absent from all output.

`--dry-run` and `--demo` skip steps 2–3: both return before any client, and the
lock in step 4 still holds.

## 7. Dotenv

Unchanged in production: `_require_token()` still calls `load_dotenv()` and a
`.env` still supplies tokens. The rail holds because the decision precedes it
and never looks at a token:

- **Token absent, `.env` present** (the incident) — blocked at step 3; step 4
  never runs, so `load_dotenv()` never runs (`test_the_same_invocation_through_the_guard_is_blocked`).
- **Token already restored** by something earlier (a tool that calls
  `load_dotenv()` at import, as `rescore_from_apify` and `adzuna_replay` do) —
  still blocked (`test_a_token_already_restored_by_load_dotenv_is_still_not_permission`,
  for no keys, env-only and CLI-only).
- **Control** — the same state through `scraper.main()` (production) restores
  the fixture token from the fixture `.env` and starts both actors against a fake
  client: the incident reproduced harmlessly
  (`test_control_production_restores_the_token_and_spends`, and in a real child
  process, `test_control_the_incident_command_spends_in_the_sandbox`).

## 8. Plan awareness and preflight

The guard asks the engine what the invocation would run; it does not guess
from flags or from which tool is calling. `--site free` needs no key even with
paid `SITES` inherited; a profile that inherits paid sites needs both keys even
if its author meant it to be free.

The preflight, printed before any refusal so a blocked run still shows what it
would have cost:

```
PAID PREFLIGHT — nothing has been started
  linkedin  curious_coder/linkedin-jobs-scraper      1 start(s)  depth 15  ceiling $0.046 per start, provider-enforced
  worst case this run: $0.046
  exposure before: $0   after: $0.046   limit (--max-usd): $0.05
```

Actor ids come from the **loaded** config (several profiles replace a site's
dict wholesale), depth from the dry run's billed depth (`effective_search`),
the ceiling from `scraper.max_charge_usd` — B1's own function, not a copy. A
site with no provider ceiling (Indeed, Naukri) prints
`NONE — not provider-bounded` and makes the worst case `UNBOUNDED`; under
`--max-usd` such a plan is refused, so **no provider without a hard cap can run
through a budgeted tool**. `--exposed-usd` carries the task's running worst-case
total, so the limit is on cumulative intended exposure, decided before the
call, never on actual spend after it.

## 9. Subprocess behaviour

- **The CLI key crosses as the literal flag.** A tool forwards its own
  `--allow-paid`; `engine_argv` refuses anything but `True`.
- **The environment key crosses only by inheritance.** No tool synthesises it.
  A tool that builds a clean environment drops it — B3's harness removes every
  `SWEEP_*` — and its child refuses. Losing a key is a refusal, never an
  escalation.
- **The child re-derives the plan.** Nothing about the parent's view of the plan
  is trusted; the child's own dry run on its own loaded config is what is
  checked, and what runs.
- **No internal acknowledgement variable exists**, so there is nothing a shell
  could set to stand in for `--allow-paid`.

Pinned with real child processes in `Subprocess` (§13).

## 10. Production isolation

| Claim | Pinned by |
|---|---|
| No production module mentions `paid_guard`, `SWEEP_ALLOW_PAID_BENCH`, `allow-paid`/`allow_paid`, or imports `bench` (`scraper.py`, `config.py`, `telemetry.py`, `deploy/sweep_worker.py`, `rescore_from_apify.py`, `auto-apply/make_shortlist.py`, `sweep/*.py`, `sources/*.py`) | `test_production_code_does_not_know_the_guard` |
| A production paid run with no developer key starts its actors | `test_a_production_paid_run_needs_no_developer_key` |
| Production never consults the guard, whatever `SWEEP_ALLOW_PAID_BENCH` says (`None`, `0`, `1`) | `test_production_never_consults_the_guard` (guard functions patched to raise) |
| The worker still launches `scraper.py --profile X --yes` with the visitor's token (BYOK) and no developer key; the console still launches `scraper.py` | `test_the_worker_and_the_console_launch_the_engine_directly` |
| `git diff e48b820 -- scraper.py config.py telemetry.py deploy/ sweep/*.py sources/` is empty | VERIFIED |

User BYOK, the paid plan, `--yes`/prompt, B1 and the budget guard are what they
were, because the code that implements them is.

## 11. B1 interaction

C0 adds nothing to what an actor is given. With both keys, the guarded run's
starts — actor, `run_input` and `max_total_charge_usd` for LinkedIn and Indeed —
**equal production's, element for element**
(`test_the_guard_changes_nothing_the_actor_is_given`): LinkedIn
`limitPerSource = 15`, `count = 15`, `scrapeCompany = False`, ceiling
`Decimal("0.046")`; Indeed without a ceiling. B1's `Decimal` handling,
`charge_ceiling_supported()` fail-closed check and `requirements.txt` pin are
untouched, and B1's 32 `test_paid_contract` tests pass unchanged. The
preflight's ceiling is computed by B1's `max_charge_usd`, and the test asserts
the current code still gives $0.046 at depth 15.

## 12. Tools

**Guarded**

- **`bench/search_v2_shadow_b3.py --sweep`** — each of its four children now runs
  through `engine_argv(... "--site", "free" ...)`. Its separate dry-run pre-check
  (one arm, a separate process) is replaced by the guard's per-arm dry run in
  the process that runs it. It never passes `--allow-paid` and strips `SWEEP_*`,
  so a paid plan can never be authorised there. `--site free`, the disabled
  paid sites and the `PAID_MARKERS` kill stay. A nonzero child exit now stops
  the harness with the log tail, rather than failing later on a missing CSV.
- **`bench/search_v2_paid_probe.py`** (new) — the one paid-capable developer
  tool: N searches of one provider-bounded site through the unchanged engine
  (§18). It writes a temporary profile enabling only `--site` (so no default
  paid site rides along), runs the guarded child with telemetry on, then reads
  the run back from Apify with free GETs — the input the actor received, the
  ceiling Apify recorded, the events it charged, the dataset size. It writes
  counts and contract fields; no row content, no token; the temporary profile
  and outputs are deleted.

**Proven paid-unreachable (no flag added)** — every other `bench/`, `tools/`
and `poc/` file: none launches the engine, builds a client or starts an actor
(the scan in §4). Several already strip `APIFY_TOKEN*` and deny sockets. The
free concurrency benchmark drives `sources.fetch_free` only (B4 `PaidIsolation`),
the results-ready benchmark drives B5's fixture harness with `--site free`, the
paid audit builds actor inputs offline without a client. Adding `--allow-paid`
to any of them would be a flag that authorises nothing.

## 13. Tests

`sweep/tests/test_paid_dev_guard.py` — **45 tests**, ~4 s.

**No real credential and no real client.** The repository's `.env` is never
opened (`dotenv.main.find_dotenv` answers a fixture `.env` holding a fixture
token), every `APIFY_TOKEN*` is removed from the environment, and
`apify_client.ApifyClient` is a recorder. A mutation that deleted the guard
reaches a fake client holding a fake token. Child processes run under a
bootstrap (`SANDBOX`) that denies sockets, replaces the client and the dotenv
lookup, and **proves itself before any test child is started** (the class is
skipped otherwise).

| Brief | Group / test | Pins |
|---|---|---|
| — | `TwoKeyRule` (9) | neither, env only, CLI only, both; the conventional values; CLI key literally `True`; a token is not permission; the decision reads only `SWEEP_ALLOW_PAID_BENCH`; the message names both keys and says both are required |
| — | `Preflight` (5) | ceiling is B1's $0.046; worst case; preflight text; limit including exposure so far; keys before limit |
| 1, 9 | `test_free_only_needs_no_key`, `test_explicit_free_needs_no_key_even_with_paid_sites_inherited` | free sweep through the guard writes its CSV; no preflight; nothing paid began |
| 2, 8 | `test_inherited_paid_sites_with_no_key_are_blocked`, `test_the_preflight_shows_the_inherited_plan` | the incident profile: blocked, nothing written, the inherited 18 + 72 plan shown |
| 3, 4 | `test_the_environment_key_alone_is_blocked`, `test_the_cli_key_alone_is_blocked`, `test_a_garbage_environment_value_is_blocked` | one key is never enough |
| 5 | `test_both_keys_run_the_plan_under_the_b1_ceiling` | client built with the fixture token; LinkedIn under $0.046 with depth 15/15; preflight before the first actor line |
| 6 | `test_a_token_in_the_shell_is_not_permission` | `APIFY_TOKEN` and `APIFY_TOKEN_2` in the shell: blocked |
| 7 | `DotenvIncident` (3) | control spends; guarded blocks; a token restored beforehand is still not permission |
| 10 | `ProductionIsolation` (5) | §10 |
| 11 | `test_the_guard_changes_nothing_the_actor_is_given` | §11 |
| 12 | `assertNothingPaidBegan` in every refusal | no client, no start, no credential step, no restored token |
| 13 | `Subprocess` (6) | real children: control spends in the sandbox; no key, env only, CLI only (also what a `SWEEP_*`-stripping tool gets) refused; both keys survive the boundary with the $0.046 ceiling and 15/15 input |
| 14 | `test_the_token_never_reaches_output` + every refusal | the fixture token absent from stdout, stderr and messages |
| race | `test_a_plan_that_turns_paid_after_the_check_is_blocked` | preflight saw no paid search, the engine then planned one: the credential step refuses, with and without the keys |
| — | `GuardedEngine` limit tests | within runs; over, or over with exposure so far, refused; an unbounded site refused under a limit |
| — | `Reachability` (3) | §4's table; guarded tools launch only through `engine_argv` (AST: no `scraper.main`, `scrape_search`, `_require_token` call and no `"scraper.py"` argument); `engine_argv`'s exact shape |

## 14. Mutation checks — all fourteen caught

Each applied alone to the source; four suites run (`test_paid_dev_guard`,
`test_paid_contract`, `test_search_v2_shadow_eval`, `test_results_ready_early`);
then reverted, checked with `cmp` against the saved copy, and the file's
SHA-256 compared with its value before the mutation. **All 14 restored
byte-identical.** The brief's A–H are A–H; E2 and I–M are extra. Every mutation
ran against fakes only: a test process that loses its guard still holds a
fixture token and a recording client (§13), so a surviving mutation could not
have spent. 7 m 55 s for the set.

| # | Mutation | Result | Among the failures |
|---|---|---|---|
| A | CLI half of the check removed | **6 fail, 2 errors** | `TwoKeyRule.test_the_environment_key_alone_is_blocked`, `GuardedEngine.test_the_environment_key_alone_is_blocked`, `Subprocess.test_the_environment_key_alone_is_blocked` |
| B | ENV half removed | **7 fail, 3 errors** | `TwoKeyRule.test_the_cli_key_alone_is_blocked`, `GuardedEngine.test_the_cli_key_alone_is_blocked`, `Subprocess.test_the_cli_key_alone_is_blocked` |
| C | `APIFY_TOKEN` presence treated as permission | **2 fail, 1 error** | `TwoKeyRule.test_a_token_is_not_permission`, `GuardedEngine.test_a_token_in_the_shell_is_not_permission`, `DotenvIncident.test_a_token_already_restored_by_load_dotenv_is_still_not_permission` |
| D | the check moved after the paid boundary (after `scraper.main()`) | **12 fail** | `test_inherited_paid_sites_with_no_key_are_blocked`, `test_a_token_in_the_shell_is_not_permission`, `Subprocess.test_the_cli_key_alone_is_blocked` |
| E | inherited paid sites not guarded (only an explicit `--site` counts) | **9 fail** | `test_inherited_paid_sites_with_no_key_are_blocked`, `test_the_preflight_shows_the_inherited_plan`, `Subprocess.test_the_environment_key_alone_is_blocked` |
| E2 | the credential-step layer removed | **1 fail** | `test_a_plan_that_turns_paid_after_the_check_is_blocked` |
| F | the developer guard applied to the production paid path in `scraper.py` | **9 fail, 2 errors** | `test_production_code_does_not_know_the_guard`, `test_a_production_paid_run_needs_no_developer_key`, B5's `test_a_paid_sweep_searches_and_writes_the_same_and_is_ready_after` |
| G | a `load_dotenv`-restored token bypasses authorisation | **19 fail** | `test_a_token_already_restored_by_load_dotenv_is_still_not_permission`, `test_a_token_in_the_shell_is_not_permission`, `Subprocess.test_both_keys_survive_the_boundary` |
| H | a truthy garbage value accepted | **2 fail** | `test_only_the_conventional_true_values_count`, `test_a_garbage_environment_value_is_blocked` |
| I | a merely truthy `allow_paid` forwarded to the child | **1 fail** | `test_the_guard_launches_the_engine_as_a_guarded_child` |
| J | the engine's credential step not restored | **1 fail** | `test_the_credential_step_is_restored_afterwards` |
| K | B3's harness launches `scraper.py` directly again | **1 fail** | `test_every_guarded_tool_launches_through_the_guard_only` |
| L | the exposure limit ignored | **3 fail** | `test_within_the_limit_runs_and_over_it_does_not`, `test_the_limit_holds_the_worst_case_including_what_was_spent`, `test_a_site_without_a_provider_ceiling_cannot_run_under_a_limit` |
| M | a site without a provider ceiling counted as free | **5 fail** | `test_worst_case`, `test_a_site_without_a_provider_ceiling_cannot_run_under_a_limit`, `test_the_preflight_shows_the_inherited_plan` |

**E, read carefully.** With the preflight blind to inherited sites, the
incident run was *still stopped*, by the credential-step layer; what the tests
caught is the missing preflight and the wrong refusal. That is the two layers
doing their jobs. E2 removes the second layer alone, and only the race test
sees it, because on every other path the first layer already refused.


## 15. Live paid verification — policy, preflight, accounting

**Policy (from the brief, applied as written):** at most **$0.50 of intended
exposure for the whole task**, decided on worst case *before* each call; a
live call only for what fixtures cannot establish; both keys on the command
line; every B1 protection in place; stop on the first anomaly; stop once the
question is answered. **Nothing that a fixture can prove was paid for**: the
refusals, the dotenv path, the inheritance path and the free path are all
offline (§13).

**Preconditions met before the call:** the guard implemented; the full sweep
suite (1,187), deploy (42) and the C0 + B1 tests green; 14/14 mutations caught.

**Preview first, at zero cost.** The same command without either key ran the
guarded child, printed the preflight and stopped before any client:

```
PAID PREFLIGHT — nothing has been started
  linkedin  curious_coder/linkedin-jobs-scraper      1 start(s)  depth 15  ceiling $0.046 per start, provider-enforced
  worst case this run: $0.046
  exposure before: $0   after: $0.046   limit (--max-usd): $0.05

Paid benchmark execution blocked. Missing: --allow-paid and SWEEP_ALLOW_PAID_BENCH=1.
```

The input was checked offline from current code beforehand:
`{"urls": ["https://www.linkedin.com/jobs/search/?keywords=Software+Engineer&geoId=105214831&f_E=3&f_TPR=r1209600"], "limitPerSource": 15, "count": 15, "scrapeCompany": false}`,
ceiling `max_charge_usd("linkedin", 15) = $0.046`. The per-invocation limit was
set to **$0.05**, tighter than the task's $0.50, so the guard itself would have
refused a second start.

**Accounting.**

| Call | Provider | Purpose | Starts | Depth | `maxTotalChargeUsd` | Intended max | Cumulative intended | Actual | Cumulative actual |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Apify, LinkedIn | C1 contract check through the guarded path | 1 | 15 | $0.046 | $0.046 | **$0.046** | **$0.03005** | **$0.03005** |

No other paid call was made. The preview, the post-run reads (run record,
input record, dataset metadata), the engine's own account-limit reads and the
run listing below are free GETs.

## 16. C1 — the LinkedIn validation (MEASURED, one run)

`SWEEP_ALLOW_PAID_BENCH=1 .venv/bin/python -m bench.search_v2_paid_probe --allow-paid --max-usd 0.05 --exposed-usd 0 --site linkedin --keywords "Software Engineer" --location Bengaluru --output docs/search-v2-evidence/c1-linkedin-contract.json`
at 2026-09-23 17:52:54 UTC.
[Evidence](search-v2-evidence/c1-linkedin-contract.json).

| | Sweep's side (telemetry) | Apify's side (run record) |
|---|---|---|
| Run | `QthNSmbc1XE5pB7o9`, build id `kmTE1iuf8N2sZAj8t` | status `SUCCEEDED`, build `1.7.17` |
| Input | `limitPerSource` 15, `count` 15, `scrapeCompany` false (from `build_input`) | **the same**, plus the actor's schema defaults: `autoConvertToAiSearch: true`, `datePosted: "anyTime"`, `companyIds: []`, `under10Applicants: false`, `splitByLocation: false` |
| Charge ceiling | started with `max_total_charge_usd = 0.046` | **`options.maxTotalChargeUsd = 0.046`** — recorded by the provider |
| Rows | requested 15, raw 15 | **dataset items 15** |
| Charged events | — | `apify-default-dataset-item` × 15, `apify-actor-start` × 1 |
| Charge | self-report at poll time $0.02805; account delta at that moment $0.01405 | **`usageTotalUsd` $0.03005** = 15 × $0.002 + $0.00005 |
| Time | unit 47.1 s (start + 8 polls + dataset read); tool wall 53.5 s | run time 39.6 s |
| After the engine's rules | 15 normalized; 0 stale (>14 d); **1 final row** under the default config's scope rules | — |

**Run listing** (free GET per configured account, runs started since the probe
began): `APIFY_TOKEN` — exactly **one**, this run; `APIFY_TOKEN_2` — none. **No
unexpected provider activity.** The token was absent (checked by value, printed
as a boolean) from the probe's full output, the run listing and the evidence
file.

**What it establishes:**

1. **The ceiling is real at the provider (MEASURED).** apify-client 3.1.0
   carried `max_total_charge_usd` into the run options, the API accepted it and
   the run carries it. B1 had verified the mechanism from the SDK source only.
2. **This build honours the depth (MEASURED, one run).** 15 rows for 15
   requested — B1 UNKNOWN #1 answered for build 1.7.17. Which field it read,
   `limitPerSource` or `count`, stays UNKNOWN (both were 15), and is harmless.
3. **The charge is exactly B1's model.** $0.03005 is B1's predicted "honest
   charge (free tier)" to the cent, so this account pays the $0.002 FREE-tier
   result price (B1 UNKNOWN #4, answered for `APIFY_TOKEN`'s account). The run
   used 65% of its ceiling; the 1.5× overshoot allowance was not needed.
4. **Recency held (MEASURED, one run).** The engine dropped 0 of 15 rows as
   older than 14 days although the actor's own `datePosted` default is
   `"anyTime"`: consistent with the URL's `f_TPR` doing the filtering, as B1 §5
   read from the actor's documentation.
5. **B1's `autoConvertToAiSearch` decision, observed.** Unsent, the actor runs
   it `true` — the mitigation mode B1 chose to leave on.

**A finding for the next paid stage, not changed here.** Right after the run,
both of the engine's spend figures read *low*: the run's self-report at poll
time was $0.02805 (one result short) and the account delta $0.01405 (usage
aggregation lag), against a final $0.03005. The sweep budget guard reads the
account delta between runs, so in a fast sweep it can see less than has been
charged. One observation; the paid engine is out of C0's scope.

**Stopped there**, as the brief requires: the contract question is answered, so
no second call was made for sample size.


## 17. Unresolved risks

1. **A hand-typed `python scraper.py` is not gated.** It is the product's CLI
   and the worker's engine, so it must keep spending on a token alone. The
   incident's exact command, typed by hand, would still spend. C0 closes the
   *tooling* path — every tracked tool that runs the engine goes through the
   guard, and the scan fails when one does not — not the human one. An
   untracked one-off script or a shell one-liner is outside any test. Mitigation
   is procedural: run the probe without keys first (it prints the preflight and
   stops), and check `--dry-run --json` before any real sweep.
2. **Python socket denial does not cover apify-client 3.x (VERIFIED).**
   `apify-client 3.1.0` depends on `impit`, a Rust HTTP client; patching
   `socket.socket.connect` does not reach it. Existing test modules that say
   "sockets are denied, so a test that reached Apify would fail rather than
   spend" are protected in fact by their stub clients and patched
   `_require_token`, not by the socket patch. Not changed here (out of scope);
   C0's tests do not rely on it.
3. **Indirect reach is scanned by name.** A tool that reached the engine through
   a helper whose name is not in the pattern (a new launcher, `runpy`,
   `os.system`) would not be flagged until it is named. The pattern covers every
   launcher that exists today.
4. **The exposure limit is per invocation, supplied by the developer.**
   `--exposed-usd` is a manual running total, not a ledger. Deliberate: it keeps
   the rule "decide on worst case before the call" without persisting anything.
5. **Indeed and Naukri have no provider-side ceiling** (B1 §4). Under a
   `--max-usd` they cannot run through the probe; without one the guard allows
   them with both keys and prints `UNBOUNDED`. A future paid benchmark of either
   needs a charge model first — a reviewed change.
6. **`rescore_from_apify.py` reads finished runs' datasets.** It starts no actor;
   whether Apify bills storage reads for it is UNKNOWN and, at dataset sizes of
   tens of items, negligible (INFERRED).
7. **The worker's paid child still calls `load_dotenv()`.** It is given the
   visitor's key as `APIFY_TOKEN`, which dotenv will not override, but a `.env`
   on the worker holding `APIFY_TOKEN_2` would add a second account to
   `_require_token`'s choice. Whether the Oracle checkout has a `.env` is
   UNKNOWN; it is a git clone and `.env` is gitignored, so INFERRED absent.
   Production code, out of C0's scope; noted because the audit passed it.
8. **Other paid providers** (Modal GPU probes in `deploy/`, Gemini, Groq,
   Adzuna) are outside this Apify-scoped stage.

## 18. Exact syntax for an intentional paid benchmark

Look first — no keys, nothing can start, the preflight prints and the child
exits:

```sh
.venv/bin/python -m bench.search_v2_paid_probe \
    --max-usd 0.50 --exposed-usd 0 \
    --site linkedin --keywords "Software Engineer" --location Bengaluru \
    --output /tmp/probe.json
```

Then, deliberately, both keys:

```sh
SWEEP_ALLOW_PAID_BENCH=1 .venv/bin/python -m bench.search_v2_paid_probe \
    --allow-paid --max-usd 0.50 --exposed-usd <worst case already committed> \
    --site linkedin --keywords "Software Engineer" --location Bengaluru \
    --output docs/search-v2-evidence/<name>.json
```

A new tool that must run the engine calls
`paid_guard.engine_argv(<scraper args>, allow_paid=args.allow_paid, max_usd=...)`
and `subprocess.run(argv, cwd=ROOT, env=<inherited>)`, and gets a `REACH` entry
of `"guarded"`. It never builds a `"scraper.py"` argv itself; the scan fails if
it does.

## 19. Rollback and removal

Nothing to roll back in production: no flag, no environment change, no
production code. Removing C0 means deleting `bench/paid_guard.py`,
`bench/search_v2_paid_probe.py` and the test, and restoring B3's harness — which
would reopen exactly the incident's path. `SWEEP_ALLOW_PAID_BENCH` must never be
added to `.env`, `render.yaml`, `/etc/sweep-worker/env` or any deployment file;
its natural state is absent.

## 20. Verification

- `python -m unittest discover -s sweep/tests -t .` — **1,187 tests, OK** (1,142
  before, +45), 240 s. An earlier run caught a real defect in the new tests:
  their sandbox wrote its fixture profile into `profiles/`, which `test_app`'s
  write guard forbids; the profile now lives in a temp dir appended to the
  `profiles` package path inside the sandbox, and the repository is never
  written.
- `python -m unittest deploy.test_sweep_worker deploy.test_modal_benchmark` —
  **42 tests, OK**.
- `python scraper.py --demo`, `python telemetry.py`, `python -m sources`,
  `python -m sources.concurrency` — pass.
- `auto-apply` suite — 1,102 tests, 2 errors in `test_inference` healthz (a
  local inference service answering HTTP 503); **the same two errors reproduce
  on a clean worktree of `e48b820`**, as they did for B3 and B5 — environmental.
- Mutation checks — 14/14 caught; every mutated file restored byte-identical
  (`cmp` and SHA-256).
- **Free-only tool, live (MEASURED 2026-09-23 17:29 UTC):**
  `bench/search_v2_shadow_b3.py --sweep`, now through the guard: four real free
  sweeps over four public boards, every arm exit 0, CSV and JSON byte-identical
  across arms, 38 final rows, shadow `evaluated` where on, **zero Apify**.
  [Evidence](search-v2-evidence/c0-b3-free-sweep-through-guard.json).
- `git diff e48b820 -- scraper.py config.py telemetry.py deploy/ sweep/*.py
  sources/ rescore_from_apify.py auto-apply/ requirements.txt render.yaml
  gunicorn.conf.py` — **empty**.
- **Paid:** one call, §15–§16. No other Apify run on either configured account.

