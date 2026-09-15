# Production inference on Modal

Sweep's remote inference service on a Modal T4, deployed 15 September 2026.
Oracle A1 (`docs/oracle-deployment.md`) stays up as the fallback. Sweep does
not know which one it is talking to: it sees `SWEEP_INFERENCE_URL` and
`SWEEP_INFERENCE_TOKEN`, nothing else.

## What runs

`deploy/modal_production.py` — app and Secret `sweep-inference-production`, on
the same serving wrapper (`deploy/modal_serving.py`) the 52-document answer-key
gate accepted. Its own app, token and state file; nothing shared with the
benchmark app but the model Volume.

| | |
| --- | --- |
| GPU | one T4; `max_containers=1`, `min_containers=0` |
| Idle scale-down | 60 s — **not tuned; revisit only with real usage data** |
| Runtime | Ollama 0.34.0, `qwen3:8b` Q4_K_M, digest `500a1f067a9f…`, Volume `sweep-ollama-models` |
| Cold start | GPU memory snapshot; the snapshot boot is synthetic-only — no résumé in any snapshot |
| Service | `inference_service.py` unchanged: `/healthz`, `POST /v1/generate`, bearer auth, one model slot, 30 s queue → 429 `model_busy` + `Retry-After` |
| Contract | identical to Oracle: 401, 400, 404, 413, 429, 504 `model_timeout`, … |

No Gemini anywhere in the service. With `SWEEP_PROFILE_ENGINE=local` a model
error is an error in Sweep too — no Gemini fallback there either.

## Rollout record, 15 September 2026

| Step | Result |
| --- | --- |
| Prime (deploy → snapshot) | ok — Ollama 0.34.0, digest `500a1f067a9f`, Q4_K_M, `size_vram` 5 274 117 078, ctx 2048 |
| Restored cold `/healthz` | **16.49 s** (restore + authenticated generation 26.84 s); new container, same digest, `size_vram` > 0 |
| Live contract | 11/11 — see below |
| Sarthak (`auto-apply/resume/resume.pdf`) vs local-direct | raw exact, semantic exact, accepted; local 37.85 s, Modal 25.25 s |
| Representative answer-key set (bhaskar / ada / hana, plain) | **Modal QUALIFIES** — 3/3 fully correct on every driving field, same as local; raw exact 3/3; 0 regressions |

Sarthak matched raw-exact because **both** backends returned the exact
duplicate employment row — production Modal reproduced the known model bug
the benchmark app never showed (`docs/local-engine-known-bugs.md` §1). Low
severity, no effect on search; the agreed semantic rule treats it as benign.

Contract, live over HTTPS: `/healthz` 200 · unknown route 404 · no token 401 ·
wrong token 401 · unknown field 400 · oversized prompt 413 · authenticated
generation 200 (8.96 s warm) · timeout 1 s → 504 `model_timeout`, which the
client maps to `ModelUnavailable` · an 8-request burst answered only 200/429,
three `model_busy` with `Retry-After: 30`.

Reports (personal values; git-ignored): `output/modal-production/`.

## Spend guard

**The guarantee is the Modal workspace budget: $25**, set in the dashboard
(Settings → Usage & Billing; there is no CLI). The $30 credit is never reached
even if the budget overshoots a little.

The code bounds the *rate*, not the total: one T4 at most, idle scale-down
60 s, token required. ≈ $0.66/h while a container is up, so $25 ≈ 38
container-hours. One cold use costs about 16 s restore + the work + 60 s idle.

**Know this:** the bearer token is checked *inside* the container. Any request
to the public URL — a scanner, a typo — wakes the GPU before it gets its 401.
The budget caps what that can cost; the URL is unguessable but not secret.

## A first request can take ~150 s — measured, and survivable

Modal keeps one GPU snapshot per worker type. A cold request that lands on a
worker type with no snapshot yet pays a full boot plus snapshot creation
(~150 s) instead of a ~16 s restore. It happened once on this rollout: the
first cold request after priming.

**The 150 s web cap does not break the client.** Measured against production,
not assumed: a real request kept busy for **282 s** came back correctly.
Modal hands anything past 150 s to a result URL with a 303, `urllib` follows
it — converting the POST to a GET, with the `Authorization` header intact —
and both results and contract errors arrive whole (that 282 s request
returned its proper 504 `model_timeout`; a stub confirms 429 `model_busy`
keeps its class and `Retry-After` across the hand-off too). urllib allows 4
repeats of one URL, so ~12.5 minutes of hand-off before it gives up.

What can still reach the user is the ordinary transient underneath — a
dropped transport, a gateway 5xx, a model not there yet. `RemoteService`
retries those **once**, inside the caller's existing deadline; `model_busy`,
malformed output and 401/400/413 are never retried. See `_retryable` in
`inference.py`.

## What a failure looks like to the user

Every `InferenceError` — a 504 `model_timeout`, a transport failure, a
temporary 5xx, a 429 `model_busy` — renders the "Reading your résumé" screen
with **HTTP 502** and the message "The local model could not be reached:
…", plus a button that re-POSTs `/derive` on the same résumé. The résumé
stays in session state, so that button is a one-click manual retry. There is
no Gemini fallback on any of these paths: `SWEEP_PROFILE_ENGINE=local`
re-raises an escalation instead of spending an API call.

Two model calls make a profile (fields, then employment), so with the
one-shot retry a profile costs at most four requests.

## The deployed image vs. this checkout

The container ships `inference.py`, and the retry above changed it, so
`prime.sources` no longer matches the working tree. Nothing about production
behaviour differs — the service runs `LocalOllama` and the error classes,
neither of which changed — but the benchmark driver's source-hash guard will
refuse to run until production is redeployed (`modal deploy`, then prime and
smoke, per the commands below). Do that when convenient; it is not needed for
the cutover.

## Using it from Sweep

Only after every row above passes. Sweep never reads `.env`; these must be in
the process environment. The filled-in file is
`output/modal-production/sweep-modal.env` (mode 600, git-ignored):

```bash
SWEEP_PROFILE_ENGINE=local
SWEEP_INFERENCE_BACKEND=remote
SWEEP_INFERENCE_URL=https://kartikverma674--sweep-inference-production-productionend-f51cc6.modal.run
SWEEP_INFERENCE_TOKEN=<output/modal-production/token.env>

set -a; source output/modal-production/sweep-modal.env; set +a; python -m sweep
```

## Rollback — Oracle, one change

Same engine and backend; only the URL and token change back:

```bash
SWEEP_INFERENCE_URL=https://sweep-inference.duckdns.org
SWEEP_INFERENCE_TOKEN=<Oracle token: /etc/sweep-inference/env on the A1 host>
```

To take Modal down entirely: `.venv/bin/modal app stop sweep-inference-production`.

## Redeploy, re-prime, rotate

A redeploy makes new snapshots, so it is always followed by prime and smoke:

```bash
export SWEEP_PRODUCTION_TOKEN="$(sed -n 's/^SWEEP_INFERENCE_TOKEN=//p' output/modal-production/token.env)"
.venv/bin/modal deploy deploy/modal_production.py
.venv/bin/python deploy/run_modal_benchmark.py --target production prime
.venv/bin/python deploy/run_modal_benchmark.py --target production smoke     # repeat if it reports snapshot creation
.venv/bin/python deploy/run_modal_benchmark.py --target production contract
```

Rotate the token: write a new `output/modal-production/token.env`
(`SWEEP_INFERENCE_TOKEN=$(openssl rand -hex 32)`, mode 600), then
`.venv/bin/modal secret create sweep-inference-production --from-dotenv output/modal-production/token.env --force`,
then the four commands above, then update `sweep-modal.env`.
