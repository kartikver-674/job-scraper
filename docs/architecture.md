# Sweep, end to end

What happens between a stranger opening a URL and downloading a shortlist
of jobs scored against their résumé — and where every piece of that runs.

Two audiences use the same code. The **console** is one operator on their
own laptop, spending their own Apify credit (`python -m sweep`). The
**public beta** is anyone with a beta code, on Render, spending nothing or
their own key. The difference is one environment variable,
`SWEEP_PUBLIC_MODE`, and what it swaps underneath the same screens.

---

# Part 1 — High-level design

## Three machines, because no one of them can do all of it

```
                    ┌──────────────────────────────────────────┐
   browser ────────►│ RENDER  sweep-beta (free tier)           │
                    │ Flask + gunicorn, 1 worker, 8 threads    │
                    │ owns: the session, the screens, the gate │
                    └───────┬───────────────────────┬──────────┘
                            │ server-side, bearer   │ server-side, bearer
                            ▼                       ▼
        ┌───────────────────────────┐   ┌──────────────────────────────────┐
        │ MODAL  T4 GPU             │   │ ORACLE A1  2 OCPU / 12 GB        │
        │ qwen3:8b via Ollama       │   │ sweep-worker :8812  (this repo)  │
        │ reads résumés, ~20s cold  │   │  └─ spawns scraper.py children   │
        │ scale-to-zero             │   │ inference fallback  :8811        │
        └───────────────────────────┘   │ Caddy terminates TLS for both    │
                                        └──────────────────────────────────┘
```

| Machine | Why it exists | What would break without it |
| --- | --- | --- |
| **Render** (free) | Public HTTPS, sessions, the UI | Nothing to give a beta user |
| **Modal** (T4, scale-to-zero) | Reading a résumé needs a GPU for ~20 s. Paying for an idle GPU is absurd; a snapshot restore makes cold start cheap | On CPU (Oracle) the same extraction takes ~7 minutes |
| **Oracle A1** (always on) | A sweep runs for minutes and must outlive the browser. Render Free spins down after 15 idle minutes and kills its children | Every sweep dies when the visitor closes the tab |

The résumé model and the sweep never meet: Modal reads the CV, Oracle
searches the market, and Render is the only thing that talks to both.

## The four boundaries the design is built around

1. **Money.** A public run is funded by whoever asked for it or it does not
   happen. The operator's Apify key is unreachable from a public screen,
   stripped from every public child's environment, and never a fallback.
2. **Credentials.** A visitor's key exists for one Render request, then in
   the worker's memory until their run starts, then in one child process's
   environment. Never a cookie, file, log, status record, command line or
   export — and never persisted for recovery.
3. **Isolation.** One process serves every visitor. `app.state` becomes a
   per-session view; run ownership is an HMAC of the signed cookie under
   `SECRET_KEY`, so a run id alone authorises nothing.
4. **Persistence.** Render's disk is ephemeral and its memory dies on
   redeploy. Anything that must survive lives on Oracle (the run) or in the
   signed cookie (which run is yours).

## What the visitor sees

```
/beta → / → /review → /key → /configure → /confirm → /running → /results
 gate  upload  check    free    sources     price     progress   shortlist
              profile  or key   & depth    & cap                 & exports
```

Those are the console's own screens, unchanged. Public mode swaps what
they reach for, not what they look like.

---

# Part 2 — Low-level design, step by step

## 0. The door — `sweep/public.py`

`SWEEP_PUBLIC_MODE=1` makes `create_app()` call `public.harden(app)`, which:

- requires `SECRET_KEY` and `SWEEP_BETA_CODE` or refuses to boot;
- wraps the app in `ProxyFix(x_for=SWEEP_TRUSTED_PROXIES, x_proto=1, x_host=0)` —
  Cloudflare then Render's LB both write `X-Forwarded-For`, so the visitor is
  the **second entry from the right**; `x_host=0` because `request.host` is
  what the CSRF check compares an `Origin` against;
- replaces `app.state` with `SessionState`, a per-cookie view of an
  in-memory store (2 h TTL, 500 sessions max);
- refuses any endpoint not in `PUBLIC_ENDPOINTS` with a 404 — the allowlist
  fails closed, so a route added later is invisible until someone lists it.

**Identity.** `session_id()` mints a random id into the signed cookie.
`owner_for_session()` = `HMAC-SHA256(SECRET_KEY, "sweep-run-owner|" + sid)`.
Oracle only ever sees that HMAC, and a different `SECRET_KEY` derives a
different one — a stolen cookie is not a run.

## 1. Résumé → text — `POST /resume`

The PDF is saved to a **temp file**, parsed, and deleted in the same
request (public mode); the console writes it to `auto-apply/resume/`.
`looks_like_pdf()` checks the magic bytes in both modes. Only the text and
the original filename go into the session.

## 2. Text → profile — `POST /derive`

This is the expensive step, metered by `public.metered()`: one complete
derivation is **one** beta usage (default 3/IP/day, 60/day total), reserved
under a lock before the model is asked and released if it fails.

```
make_profile.generate(engine="local")
└─ local_profile.generate()
   └─ local_extract.read()
      ├─ extract()     → FIELDS_SCHEMA    ┐ two schema-constrained calls,
      └─ employment()  → EMPLOYMENT_SCHEMA┘ think:false, temperature 0
      └─ route(fields, rows, text) → accept | corrected | escalate
   ├─ months_from(rows) → years_experience   (deterministic, merges ranges)
   └─ local_search      → role_keywords, title hints from the market corpus
└─ _finish()
   ├─ widen_skills()        skills named only in prose (skill_scan)
   └─ reweight_from_corpus() blends each weight against market frequency
```

Both model calls cross `inference.provider()`, which is the whole point of
that boundary: `SWEEP_INFERENCE_BACKEND=remote` sends them to Modal over
HTTPS with a bearer token; `local-direct` would send them to a local
Ollama. Identical prompts, schemas and arithmetic either way.

**On Modal:** the container restores from a GPU memory snapshot (~16 s;
~150 s if Modal has to build a snapshot for a new machine type), Ollama
answers with `qwen3:8b` Q4_K_M, then scales to zero after 60 s idle.
`RemoteService` retries **once** for a transient absence, inside the
caller's existing deadline; `model_busy` (429), malformed output and
401/400/413 are never retried.

**Weights.** The local engine stamps every skill `NEUTRAL_WEIGHT = 3`, then
`corpus_signal` blends it with how much that term narrows the market:
`blend(centrality, separation) = round(sqrt(c × s))`, clamped 1–5. Document
frequency comes from `output/` when the machine has sweeps of its own, and
otherwise from the shipped table `data/skill_market_frequencies.json` (367
terms from 22,806 listings). Live or frozen — never merged.

## 3. Review → a profile — `POST /review`

The visitor edits weights, drops terms, sets experience. `make_profile.render()`
turns the reviewed dict into profile source; every value goes through
`repr()`, so a skill term of `'); import os` is a quoted string. In public
mode the source is kept in the session, not written to `profiles/`, and the
visitor is sent to `/key`.

## 4. Free or paid — `GET/POST /key`

**Free** (`/key/free`): records the choice and switches every billed board
off (`sites_enabled = {site: False}`).

**Paid** (`POST /key`, public branch): the visitor's token is validated with
Apify's read-only `user().limits()` call, forwarded to the worker
(`POST /v1/tokens`), and forgotten by Render in that same request. What
stays in the session is the **credit figure**, not the credential.
`refresh_credits()` and `read_env_tokens()` do nothing in public mode — that
file is the operator's.

The worker holds it in memory against the owner HMAC for 45 minutes, and
releases it the moment that visitor's run starts.

## 5. Configure → a plan — `GET /configure`, `POST /estimate`

Which sources bill: `config.SITES ∩ config.SITE_RATES` = **linkedin**
($0.045/search at 25 results), **indeed** ($0.09 at 15), **naukri** ($0.50 at
50, a per-run minimum). Everything else — 129 ATS boards and 5 remote feeds —
is free and always on.

The plan itself comes from the engine's own dry run, on Oracle
(`POST /v1/plans` → `scraper.py --profile … --dry-run --json`), because the
number a visitor approves before spending their money should not be
arithmetic repeated in the web app. `plan.cost()` scales each rate from the
depth it was measured at to the depth this sweep will run at.

## 6. Confirm → a cap — `GET /confirm`

Shows per-site subtotals and the total. `SETTINGS["max_spend_usd"]` is
stamped into the profile before the child starts — the engine re-reads the
account after every search and stops there. A plan costing more than the
key's credit needs an explicit tick.

## 7. Run — `POST /run` → `deploy/sweep_worker.py`

Render calls `POST /v1/runs` with the reviewed profile, the prefs and the
owner HMAC. The worker:

1. mints `run_id = token_hex(16)` (hex, because it is also a Python module
   name and a path segment);
2. renders the profile with the same `make_profile.render()`, then
   **parses it and refuses anything that is not a literal assignment** —
   that is what makes "structured data cannot become code" a property
   rather than a hope;
3. writes it to `<checkout>/profiles/beta_<run_id>.py` (the engine selects a
   profile by module name) and to the run directory;
4. takes the held Apify token for a paid run — or refuses (409) rather than
   borrowing anyone's;
5. spawns `scraper.py --profile beta_<run_id> --yes` with the token **in the
   child's environment only**, every `APIFY_TOKEN*` of the operator's
   stripped first, and nothing on the command line where `/proc` would
   publish it.

One run at a time, FIFO behind it, all state in
`/var/lib/sweep-worker/runs/<run_id>/` with atomic `status.json`.

## 8. The engine — `scraper.py`

```
for each paid site, for each keyword × location:
    run the Apify actor → normalise → score_job() → emit()  (checkpoint)
    append the combo key to .done_combos                    (the ledger)
then the free sources: 129 ATS boards + 5 feeds → score_job() → emit()
```

`score_job()` hard-filters first (repost farms, unreachable titles, an
experience floor above yours), then adds each matched skill's weight,
subtracts penalties, adds a full-stack bonus for frontend+backend overlap,
and records `matched_skills`. Output is CSV + JSON, rewritten at every
checkpoint so an interrupted sweep still leaves a correct file.

Measured: the free half is **171 s wall, 6 s CPU, 262 MB peak** — it waits on
the network, which is why it fits beside the inference service.

## 9. Watching it — `GET /running`, `GET /progress`

The browser polls Render every 4 s; Render asks Oracle. (The console uses an
event stream instead; a stream would hold one of eight gunicorn threads for
the length of a sweep.) One poll asks three questions and fetches the
status once per request:

| Question | Answered by |
| --- | --- |
| Which searches are done? | the run's `.done_combos`, handed over with the status |
| What has arrived? | the newest `jobs_*.csv` in the run directory |
| Is it still alive? | `RemoteRun.poll()` → the worker's run state |

`finished = (child stopped) and outstanding == 0`. While the child is alive
and every planned search is done, `free_running` is true — the engine is
working through the free sources, which have no tiles, and the screen says
so rather than looking stuck.

## 10. Results and exports — `GET /results`, `GET /export.<fmt>`

Rows come back from the worker and go through the same `sweep.logic`
functions the console uses: `shortlist()` (score floor, source, search,
sort), `bucket_rows()` (reachable in India / fully remote / needs a visa),
`SECTIONS`. Exports run through the **same** buckets, so a file labelled 128
listings contains those 128.

## 11. Lifecycle and cleanup

| Event | What happens |
| --- | --- |
| Tab closed | Oracle keeps sweeping; the cookie brings the visitor back |
| Render redeploys | Same — `worker_link.rehydrate()` restores what the screens need from the worker |
| Worker restarts mid-run | That run is `interrupted` with a reason. Queued **free** runs re-queue; queued **paid** runs are interrupted, because the key was deliberately never written down |
| 48 hours | The janitor deletes the run directory and its rendered profile — never one that is queued or running |
| 2 hours idle | The Render session is forgotten; the visitor uploads again |

---

# Part 3 — Where everything lives

| Thing | Where | Survives a restart? |
| --- | --- | --- |
| Résumé PDF | temp file, deleted in the request | no, by design |
| Résumé text, parse, profile source | Render memory (`SessionStore`) | no |
| Which run is yours | the signed cookie (`run_id`) | **yes** |
| Run ownership proof | derived per request (HMAC) | **yes** |
| Visitor's Apify key | Render request → worker memory → child env | **no, by design** |
| Operator's Apify key | `.env` on their own machine | n/a — unreachable in public mode |
| The run, its profile, its results | `/var/lib/sweep-worker/runs/<id>/` | **yes**, for 48 h |
| Market frequency table | `data/skill_market_frequencies.json` | committed |

# Part 4 — Invariants worth not breaking

1. `SWEEP_PUBLIC_MODE` off must leave the console exactly as it was —
   shared state, its own `.env`, its subprocess, its event stream.
2. Nothing public may reach `/key/remove`, `/second-key`, `/rescore`,
   `/merge`, `/applied` or `/events`.
3. A paid public run without the visitor's own key is refused, never
   borrowed.
4. The rendered profile is literal assignments only.
5. One sweep at a time per worker; the queue is FIFO and disk-backed.
6. Prompts, schemas, the blend arithmetic, the bands, `MIN_LISTINGS` and the
   years logic are not changed casually — they are what the 52-document
   answer-key gate validated.

# Part 5 — What it costs

| | |
| --- | --- |
| Reading one résumé | two GPU calls, ~20–35 s on a restored Modal container |
| Modal ceiling | $25 workspace budget; one T4, scale-to-zero, ~$0.66/h while up |
| A free sweep | nothing — 129 ATS boards and 5 feeds over plain HTTPS |
| A paid sweep | the visitor's own Apify credit. Measured: 6 LinkedIn searches at depth 10 = **$0.10** |
| Render | free tier |
| Oracle | Always Free A1 |

## Related documents

- `docs/modal-production.md` — the inference deployment, its budget and rollback
- `docs/oracle-deployment.md` — the inference fallback on the A1
- `docs/oracle-sweep-worker.md` — installing and operating the sweep worker
- `docs/public-beta.md` — the Render service, its variables and its limits
- `docs/local-engine-known-bugs.md` — two recorded model bugs, deliberately unfixed
