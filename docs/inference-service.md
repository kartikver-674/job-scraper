# Running the inference service

`inference_service.py` is the model service Sweep talks to instead of running an
8B model inside its own process:

```
Sweep  ->  inference_service (gunicorn)  ->  Ollama  ->  qwen3:8b
```

It is stateless, holds a request only for as long as it takes to answer it, and
is the only component in the repo that knows Ollama exists. Sweep reaches it
through `inference.py`'s provider boundary and never falls back to a local model
when it cannot be reached.

**Not deployed anywhere yet.** This documents how to run it, not where.
[inference-hosting.md](inference-hosting.md) is the evidence for *where* and what
it would cost; [oracle-deployment.md](oracle-deployment.md) is the step-by-step
runbook for the host that evidence picked.

---

## Minimum runtime requirements

| | |
| --- | --- |
| Python | 3.10+ (developed on 3.14) |
| Packages | `flask`, `gunicorn` — both in `requirements.txt` |
| Model runtime | Ollama, reachable over HTTP, with the model pulled |
| RAM | **~6 GB free for the model alone.** qwen3:8b is 5.2 GB of weights plus a KV cache sized per request (`inference.ctx_for`, capped at 8192 tokens). The service process itself is ~60 MB |
| Disk | 5.2 GB for the model. The service writes nothing |
| CPU/GPU | Whatever Ollama needs. The service is I/O-bound and idle while the model runs |
| Network | One inbound port (default 8811), one outbound to Ollama |

The service and Ollama may be on different machines — set `OLLAMA_HOST`. Nothing
about the service assumes they share a host.

---

## Environment variables

**Required.**

| Variable | Meaning |
| --- | --- |
| `SWEEP_INFERENCE_TOKEN` | Bearer token(s) the service accepts, comma-separated so one can be rotated without a restart window. **The service refuses to start without it** — an open model endpoint is not a dev convenience |

**Runtime — where the model is.** Unchanged from the direct-Ollama path, and
read by the *service*, not by Sweep.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama's base URL. A missing scheme is supplied |
| `OLLAMA_MODEL` | `qwen3:8b` | Model to ask when the request does not name one |
| `SWEEP_MODEL_KEEP_ALIVE` | `30s` | How long the runtime keeps the model resident. Ollama's own grammar (`30s`, `10m`, `-1` for forever). **Not yet tuned** — see Known issues |

**Service — deployment only.**

| Variable | Default | Meaning |
| --- | --- | --- |
| `SWEEP_INFERENCE_HOST` | `127.0.0.1` | Bind address. Binding beyond loopback is logged loudly at startup |
| `SWEEP_INFERENCE_PORT` | `8811` | Bind port |
| `SWEEP_INFERENCE_WORKERS` | `1` | Concurrent generations allowed. See Concurrency |
| `SWEEP_INFERENCE_QUEUE_WAIT` | `30` | Seconds a request waits for a model slot before `model_busy` |
| `SWEEP_INFERENCE_LOGLEVEL` | `info` | gunicorn log level |

A mistyped numeric setting is a **startup failure**, not a silent fallback to the
default — somebody set it because they meant something by it.

**Client side**, for whatever calls the service (`inference.py`):
`SWEEP_INFERENCE_BACKEND=remote`, `SWEEP_INFERENCE_URL`, `SWEEP_INFERENCE_TOKEN`.

---

## Starting it

```bash
pip install -r requirements.txt
ollama serve &                      # or a systemd unit, or another machine
ollama pull qwen3:8b

SWEEP_INFERENCE_TOKEN=$(openssl rand -hex 32) python -m inference_service
```

That **is** the production command. It validates the configuration, prints what
it is about to become, and then execs gunicorn with `gunicorn.conf.py`. There is
no separate development server — the thing you run in development is the thing
that ships. Extra arguments are passed through to gunicorn:

```bash
python -m inference_service --log-level debug
```

Equivalent, if you would rather invoke gunicorn yourself (this skips the startup
report and the pre-bind token check):

```bash
gunicorn -c gunicorn.conf.py "inference_service:create_app()"
```

Startup prints, before anything binds:

```
bind            127.0.0.1:8811
model slots     1 (queue wait 30s)
runtime         http://127.0.0.1:11434
model           qwen3:8b (keep_alive 30s)
max prompt      262144 bytes
max timeout     300s
```

### Stopping it

`SIGTERM` (or Ctrl-C). gunicorn stops accepting, lets the in-flight generation
finish, then exits. `graceful_timeout` is `MAX_TIMEOUT + 30` = 330s, so a
generation running at its full permitted deadline still completes. Verified:
SIGTERM sent 8s into a live extraction, the request returned `200` at 9.8s, then
the port closed.

---

## Health check

```bash
curl -s localhost:8811/healthz
{"status":"ok","runtime_reachable":true,"model":"qwen3:8b","model_slots":1}
```

`200` when Ollama answers, `503` with `"status":"degraded"` when it does not. No
token required — it is a liveness probe and says nothing a caller could not learn
by connecting. It takes no model slot, so it answers in milliseconds while a
90-second generation is in flight; that is what makes it usable as a probe.

A `degraded` service is **still running and still correct**. Ollama is a separate
process with its own restart, so the service warns at startup and returns
`model_unavailable` per request rather than refusing to boot — otherwise "Ollama
is slow to come up" becomes an outage that needs a human.

---

## Concurrency

Requests are accepted concurrently (8 gunicorn threads) and generations run
**one at a time**, behind a semaphore sized by `SWEEP_INFERENCE_WORKERS`.

This is the shape of the resource, not caution. A second concurrent generation
against one Ollama does not halve latency — the runtime loads a second copy of a
5 GB model, or evicts the first and reloads it, and both callers then finish
later than either would have alone.

A request that arrives while the slot is taken **waits** up to
`SWEEP_INFERENCE_QUEUE_WAIT` seconds, then is refused with `model_busy` and a
`Retry-After`. Nothing queues invisibly and nothing spawns an extra model load.

`gunicorn.conf.py` pins `workers = 1` deliberately: the semaphore is a
`threading.Semaphore` and therefore per-process, so two worker processes would
mean two concurrent generations — the exact thing it exists to prevent. Scale by
raising `SWEEP_INFERENCE_WORKERS` only if the runtime behind it can actually hold
that many models resident.

---

## Expected failure modes

Every failure returns the same shape, and `category` is the contract — the status
code is a hint:

```json
{"request_id": "a1b2c3d4e5f6", "error": {"category": "...", "message": "..."}}
```

| Category | Status | Cause | What to do |
| --- | --- | --- | --- |
| `unauthorized` | 401 | Missing, malformed or unknown bearer token | Check `SWEEP_INFERENCE_TOKEN` on both sides |
| `bad_request` | 400 | Unknown field, missing field, wrong type, empty prompt or schema | The client is off-contract; the message names the field |
| `payload_too_large` | 413 | Prompt over 256 KB, or body over 320 KB | Not a résumé. Refused before it reaches the model |
| `model_busy` | **429** | No model slot within `SWEEP_INFERENCE_QUEUE_WAIT` | Capacity, not failure. Retry after `Retry-After`, or raise the wait |
| `model_unavailable` | 503 | Ollama not running, or the model not pulled | `ollama serve`; `ollama pull qwen3:8b`. `/healthz` will also be degraded |
| `model_timeout` | 504 | The model did not answer within the request's deadline | Usually a cold load on a busy machine. See keep-alive |
| `bad_model_output` | 502 | The runtime answered with something that is not the JSON it was asked for | A model or runtime problem, not a client one |
| `internal_error` | 500 | A bug | The request id is in the log with a traceback |
| `not_found` | 404/405 | Wrong path or method | Only `POST /v1/generate` and `GET /healthz` exist |

On the client side `inference.py` splits these three ways, because they route
differently:

| Exception | From | Sweep's `local-first` engine |
| --- | --- | --- |
| `ModelUnavailable` | `model_unavailable`, `model_timeout`, `bad_model_output` | *There is no model to ask.* Worth one Gemini call |
| `ModelBusy` | `model_busy` | **Never escalates.** Retryable; carries `retry_after` |
| `RemoteServiceError` | everything else, **including any response that is not this shape** | Nothing catches it. Explicit failure |

`ModelBusy` is deliberately none of the other two. A full queue is not an absent
model, so it must not open the paid path — and it would open it *under load*,
which is the worst possible moment to start spending per request. It is not a
misconfiguration either, so it is the one failure here a caller may sensibly
retry. That is also why the status is `429` and not `503`: `503` tells a caller
the service is unavailable and gives it a reason to go somewhere else, which is
exactly the wrong conclusion.

Sweep does not retry automatically today — it raises. Adding a bounded backoff is
a sensible next step when there is more than one caller.

Sweep never falls back from the remote service to a local Ollama. Failures are
explicit.

### Not failures

- **`degraded` health with the service up.** Ollama is down; the service is fine.
- **A slow first request.** The model is being loaded. See below.

---

## Logging

One line per request, to stdout:

```
request_id=65440b5bcb86 path=/v1/generate status=503 model=nope:1b \
  prompt_bytes=41 waited_ms=0 duration_ms=1 outcome=model_unavailable
```

Six fields and no seventh: request id, the path and status they describe, model,
duration and the outcome category. **Nothing derived from the request body.**

`prompt_bytes` and `waited_ms` used to be here. A byte count reconstructs
nothing, which is precisely the argument every field makes on the way in — a
service that handles other people's résumés earns trust by having nothing to
explain, not by having a good explanation. Duration plus the outcome category is
enough to debug with; if it ever stops being enough, the thing to add is a
metric, not a log field.

`duration_ms` is the whole request, so for a queued one it includes the wait. A
saturated service shows up as `outcome=model_busy` lines, not as a number to
subtract. Never the prompt, never the résumé, never the model's answer, never the
token. An unhandled exception logs its type and its **frames** but not its
message — `json`, `urllib` and runtime adapters all routinely put the thing they
choked on into the message, and for this service that thing is a résumé.

`X-Request-Id` is echoed back on every response, and honoured if the caller sends
one.

Nothing is persisted. No database, no cache, no spool file, no audit trail.

---

## Known issues and things deliberately not done

- **`SWEEP_MODEL_KEEP_ALIVE` is configurable and still defaults to `30s`.**
  It has now been measured — see
  [docs/inference-hosting.md §8](inference-hosting.md#8-the-keep-alive-measurement).
  The short version: `30s` is *correct* for a single profile, because the two
  model calls are back to back and nothing expires between them. Raising it
  matters only between one user's profile and the next, and on Oracle for the
  idle-reclamation floor. The default is deliberately unchanged.
- **One shared token, no per-user auth, no rate limiting.** `principal()` returns
  the token as the identity today and a user id later; a limiter keyed on its
  return value drops in at the marked line in `generate()` without the contract
  moving.
- **No TLS.** Loopback only today. The prompt is a résumé in clear text, so
  anything beyond loopback needs TLS terminated in front of this.
- **No automatic retry on `model_busy`.** The client raises `ModelBusy` with the
  service's `Retry-After` attached and leaves the decision to the caller. A
  bounded backoff is worth adding when there is more than one caller.
- **No draining flag on `/healthz`.** During shutdown it keeps reporting `ok`
  until the port closes. That matters when a load balancer is watching, and there
  isn't one.
- **Single instance.** No Docker, no orchestration, no autoscaling, by design.
