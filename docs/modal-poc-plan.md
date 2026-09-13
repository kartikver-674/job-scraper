# Modal GPU: the POC plan

**Nothing is deployed.** This is the design for the next experiment, written
before it runs so the result cannot be argued into whatever we hoped for.

Researched 13 September 2026. Prices and limits move; every figure is sourced.

---

## 1. The one question this experiment answers

> Does the **same** qwen3:8b, running the **same** prompts and schema through the
> **same** `/v1/generate` contract, on a GPU instead of 2 ARM cores, bring a
> profile back under a minute?

Nothing else changes. Not the model, not the quantisation, not the runtime, not
the prompt format, not the downstream pipeline. **Isolate the hardware.**

That discipline is what makes the result interpretable, and it is why the
tempting options are deliberately excluded from *this* experiment:

| Tempting | Why not now |
| --- | --- |
| vLLM instead of Ollama | Different sampler, different structured-output implementation. A faster number would not tell us whether the GPU or the runtime did it |
| Qwen3-32B on a free API | A different model. `years_experience` scores 52/52 with *this* one, and every prompt rule exists because the benchmark caught *this* one erring |
| A different quantisation | Q4_K_M is what was benchmarked. Changing it changes output quality, silently |

If Ollama turns out not to work on Modal, §7 is the fallback — but explain why
first, don't substitute quietly.

### The comparison the POC must produce

The same real résumé — the one that took ~7 minutes — through all three, with
`bench/remote_profile.py` recording per call:

| | Model | Host | Per-profile |
| --- | --- | --- | --- |
| 1 | qwen3:8b Q4_K_M | M1 Pro, local | 14.3 s measured (`ada`); the real résumé not yet run |
| 2 | qwen3:8b Q4_K_M | Oracle A1, 2 OCPU | **~7 min measured** |
| 3 | qwen3:8b Q4_K_M | Modal T4 | **unknown — this is the experiment** |

> **The earlier "~11 s/profile on a T4" figure is withdrawn.** It was arithmetic
> on an assumed token rate, exactly the method that produced the 100–240 s Oracle
> estimate that reality tripled. Treat it as a hypothesis with no evidence behind
> it. The only honest number we have for a GPU is *none*.

---

## 2. What Modal is, in the terms that matter here

| | |
| --- | --- |
| Isolation | containers under **gVisor (`runsc`)**, with **`nvproxy`** for GPU access — not bare Linux |
| Container boot | ["about one second"](https://modal.com/docs/guide/cold-start), before your own init |
| Scale to zero | default idle `scaledown_window` **60 s**, configurable 2 s – 20 min |
| Web endpoints | **hard 150 s HTTP request timeout** ([docs](https://modal.com/docs/guide/webhook-timeouts)) |
| Volumes | persistent, up to **2.5 GB/s**, 1 TiB/month free then $0.09/GiB-month |
| Secrets | first-class `modal.Secret`, injected as env vars |

### 2.1 The 150-second wall — the constraint that shapes the design

Modal enforces a **150 s maximum on web-endpoint HTTP requests**, across
`fastapi_endpoint`, `asgi_app`, `wsgi_app` *and* `web_server`. Past that it
returns a **303 redirect** to the same URL for the client to re-poll.

Our contract allows `MAX_TIMEOUT=300` and Sweep's client waits `timeout + 15`.
So a Modal call slower than 150 s does **not** produce a clean `model_timeout`:
it produces a 303, and `urllib` follows a 303 by **converting POST to GET** —
which would arrive at `/v1/generate` as a GET and come back `not_found`. A
timeout would surface as a confusing routing error.

**On a GPU this should never fire** (calls ought to be seconds). But it must be
verified, not assumed, and it is the single most likely way this POC produces a
baffling result. Two mitigations, in order of preference:

1. Confirm empirically that every call is well under 150 s. If so, nothing to do.
2. If any call approaches it, the Modal deployment sends a client `timeout`
   below 140 s so *our* deadline fires first and the caller gets a
   `model_timeout` category. **No code change** — `timeout` is already a
   per-request field.

Do not raise Modal's limit by polling/redirect handling in this POC. That is a
contract change, and the contract is the thing we are holding fixed.

### 2.2 Does Ollama actually work under gVisor + nvproxy?

**This is the first thing to test, before any benchmarking.** Modal's GPU access
goes through `nvproxy` rather than the host driver directly. Most CUDA workloads
work; Ollama bundles its own CUDA runtime and probes the driver at startup, which
is exactly the kind of thing a syscall-emulating sandbox can trip.

The go/no-go check is one line, run inside a Modal GPU container:

```
ollama run qwen3:8b "hi"   # then: does `ollama ps` show size_vram > 0 ?
```

`size_vram > 0` is the whole test. **If Ollama loads the model into system RAM
instead of VRAM it will be as slow as Oracle or slower**, and the experiment is
meaningless. On Oracle today `size_vram = 0` — that is the CPU-only signature,
and seeing it again on a GPU means the GPU is not being used.

Fail this, and §7 applies.

---

## 3. GPU choice

| GPU | VRAM | $/s | $/hr | Verdict |
| --- | --- | --- | --- | --- |
| **T4** | 16 GB | $0.000164 | $0.59 | **Start here.** Cheapest; 16 GB is ample |
| L4 | 24 GB | $0.000222 | $0.80 | Newer, +35% cost. Worth one comparison run if T4 disappoints |
| A10G / L40S / A100 | 24–80 GB | $0.000306+ | $1.10+ | Overkill for an 8B; VRAM is not our constraint |

**VRAM fit** — the model is 5.2 GB of Q4_K_M weights, and our measurement showed
the resident footprint at `num_ctx=2048` is ~5.3 GB, rising to ~5.9 GB at 4096
and ~7.2 GB projected at our 8192 ceiling. Against T4's 16 GB that is **less than
half**, with room for the KV cache of the longest résumé we would ever send.
VRAM does not drive this decision; price does.

T4 is Turing (no bf16, older kernels). If it underperforms surprisingly, L4 is
the one comparison worth running — but only after T4 has a number.

---

## 4. The deployment shape

```
Sweep ──HTTPS──► Modal web endpoint ──► our app ──► Ollama ──► qwen3:8b (GPU)
                 (Modal's TLS)          (unchanged) (in-container)
```

**Sweep must not learn that Modal exists.** It keeps exactly:

```bash
SWEEP_INFERENCE_BACKEND=remote
SWEEP_INFERENCE_URL=https://<workspace>--sweep-inference.modal.run
SWEEP_INFERENCE_TOKEN=...
```

No new environment variable, no conditional, no provider flag. That is the point
of the boundary, and it is already built.

### What gets written

A single new file, outside the application, e.g. `deploy/modal_app.py`:

- a Modal Image that installs Ollama and our three files
- a Volume mounted at `/root/.ollama` so the 5.2 GB model is pulled **once**
- a container hook that starts `ollama serve` and waits for it to answer
- `@modal.wsgi_app()` returning `inference_service.create_app()` — our real app,
  unmodified
- `modal.Secret` supplying `SWEEP_INFERENCE_TOKEN`

**No change to `inference.py`, `inference_service.py` or `gunicorn.conf.py`.**
If the POC needs one, that is a finding worth reporting, not a patch to slip in.

Note `gunicorn.conf.py` is unused on Modal — `wsgi_app` runs the WSGI callable
under Modal's own server. The 360 s worker timeout and the one-worker pinning are
gunicorn settings and simply do not apply; §5 covers what replaces them.

### Model caching — the cold-start question

The 5.2 GB model **must not re-download per cold start**. A Modal Volume mounted
at `/root/.ollama` holds it: pulled once by a one-off function, then read at up
to 2.5 GB/s, so loading should be seconds rather than a download.

Sequence to verify, in order:

1. `modal run` a one-shot `ollama pull qwen3:8b` that writes to the Volume and
   commits it.
2. Cold-start the endpoint. Confirm **no download** in the logs.
3. Measure: container boot → `ollama serve` ready → model in VRAM → first token.

If the Volume approach fights Ollama's blob layout, the alternative is baking the
model into the **image** (larger image, but Modal caches image layers). Try the
Volume first; it keeps the image small and the model updatable.

---

## 5. Concurrency, and what our one-slot design means here

**Keep the single model slot.** `SWEEP_INFERENCE_WORKERS=1` stays.

The reasoning that produced it — a second concurrent generation makes one Ollama
load a second copy of the model or evict the first — is a property of *one Ollama
with one GPU*, not of Oracle. It holds on a T4.

What changes is that Modal can run **several containers**, each with its own GPU
and its own Ollama. So the scaling axis becomes containers, not slots — and the
Starter plan allows 100 containers / 10 GPU concurrency. For 10 profiles a day
none of that matters, and `max_containers=1` is the honest setting for a POC that
is trying to measure one thing.

`model_busy` (429 + `Retry-After`, never a Gemini call) is produced by our app, so
it behaves identically. With `max_containers=1` it becomes reachable exactly as
on Oracle — worth one deliberate test.

---

## 6. Cost, at 10 profiles/day

**The dominant term is not inference. It is how long a container stays alive
after a request.**

Modal bills GPU time while the container exists, and `scaledown_window` governs
that. With 10 uploads a day arriving separately:

| `scaledown_window` | Container-seconds/month | T4 cost | vs $30 |
| --- | --- | --- | --- |
| 60 s (default) | 10 × (≈20 s work + 60 s idle) × 30 ≈ 24 000 | **≈ $3.94** | 13% |
| 5 min | 10 × (20 + 300) × 30 = 96 000 | ≈ $15.74 | 52% |
| **8 min** | ≈ 150 000 | ≈ **$24.60** | **82% — the break-even** |
| 20 min (max) | 10 × (20 + 1200) × 30 = 366 000 | ≈ $60.02 | **200% — over budget** |

$30 ÷ $0.000164/s ≈ **182 900 GPU-seconds/month**, about 50 hours, or ~10 minutes
of container life per upload at 10/day.

**So: keep `scaledown_window` short and accept cold starts.** Tuning it up to
avoid cold starts is what turns this from free into a bill. Storage is free
(5.2 GB against 1 TiB included).

The 20 s work figure is a placeholder standing in for an unmeasured quantity —
the whole point of the POC. Even at 60 s of GPU work per profile, the 60 s window
row is ≈ $5.90/month. The conclusion is robust to being wrong by 3×, which is
precisely the margin the Oracle estimate lacked.

### Credit versus infrastructure

**Modal's $30/month is a recurring credit allowance, not free infrastructure.**
It resets monthly and does not roll over ([pricing](https://modal.com/pricing)).
It is a vendor's commercial decision and can be changed or withdrawn; Oracle's
Always Free is a different category of promise — and Oracle halved *its*
allowance in June 2026 without announcing it, so neither is a guarantee.

Plan on that basis: **Modal free at this scale is a real and current fact, and
not something to build a business on.** If the credit disappears, this workload
costs ~$4–6/month, which is a survivable answer rather than a cliff.

---

## 7. If Ollama will not run on Modal

Only if §2.2 fails — the model does not reach VRAM, or Ollama will not start
under gVisor. Then, in order, and **explained before adopted**:

1. **Ollama in a plain container with `modal.web_server`** rather than a WSGI
   app, in case the failure is process-model related rather than CUDA.
2. **llama.cpp's own server** — the same GGUF, the same Q4_K_M weights, the same
   grammar-constrained JSON. Closest possible substitute; Ollama is a wrapper
   over llama.cpp, so the model behaviour should carry over. Our
   `inference_service.RUNTIME` is the one line that names a runtime, so this is a
   provider swap by design.
3. **vLLM** — last resort, and a *different experiment*. It wants unquantised or
   AWQ/GPTQ weights and implements structured output differently, so adopting it
   means re-running the 52-document benchmark before any result counts.

---

## 8. Success criteria

The POC succeeds if **all** of these hold:

- [ ] `ollama ps` inside the container shows **`size_vram > 0`** — the GPU is real
- [ ] The 5.2 GB model does **not** re-download on cold start
- [ ] Every call completes **well under 150 s**
- [ ] `/healthz` and `/v1/generate` behave exactly as on Oracle: 401 without a
      token, 429 + `Retry-After` when busy, the same error categories
- [ ] Sweep reaches it with **only** `SWEEP_INFERENCE_URL` changed
- [ ] `bench/backends.py` shows the generated profile is **field-identical** to
      the local-direct baseline
- [ ] `bench/remote_profile.py` gives per-call token counts and rates for the
      three-way comparison
- [ ] Measured cost for a month of simulated load stays **under $10** against the
      $30 allowance

It **fails**, and we say so, if the GPU is not used, if the profile is not
identical, or if a contract behaviour differs.

---

## 9. Measured: T4, real résumé, 13 September 2026

The POC ran. **Ollama works on a Modal T4** — `size_vram 5,274,117,078`, the
whole model in VRAM, Volume cache working, no redownload. §2.2's go/no-go passed.

Sarthak résumé (3634 chars), production prompts and schema, `le.read()`:

| | cold | warm (same prompt) |
| --- | --- | --- |
| fields | 101.26 s | — |
| employment | 4.33 s | — |
| **profile** | **105.60 s** | 13.42 s |

Warm is prefix-cache-assisted and does not predict a second user.

### Where the 105.60 s went

| | |
| --- | --- |
| reading 5.23 GB off the Volume | **0.98 s** (5.34 GB/s) |
| weights → VRAM (reload, warm cache) | 4.46 s |
| `ollama serve` ready | 7.14 s |
| real prefill, 1100 tok @ 1145 tok/s | 0.96 s |
| real generation, 359 tok @ 37.2 tok/s | 9.65 s |
| employment call, all of it | 4.33 s |
| **one-time init inside `load_duration`** | **52.43 s** |
| **one-time init billed to `prompt_eval`** | **33.73 s** |
| | **= 86.16 s, or 81.6% of the run** |

**The tell**: the first call prefills 1100 tokens at 31.7 tok/s, the second
prefills 1156 at **1143.6 tok/s** — 36×, same GPU, same container, neither
cached, no shared prefix. The first `prompt_eval` is not measuring prefill at
all; it is CUDA kernel/JIT warmup billed to the wrong counter.

**Disk is not the bottleneck and neither is the VRAM transfer.** Strip the
one-time init and the profile is **~15 s** — better than the M1 Pro's 37.4 s and
12× better than Oracle's 186.9 s.

### The three-way table so far

| Host | Cold profile | Steady state (model resident, prompt uncached) |
| --- | --- | --- |
| M1 Pro | 37.8 s | 21.5 s |
| Oracle A1, 2 OCPU | 186.9 s | — |
| **Modal T4** | **105.60 s** | **~15 s** |

Modal wins decisively on steady state and loses on cold start. Everything now
hinges on whether the 86 s is payable once instead of per container.

### The 150-second web-endpoint cap

The cap is **per HTTP request**, and Sweep makes two, so the binding constraint
is the worst single call — cold `fields` at 101.26 s, a **+48.7 s margin (32% of
the cap)**.

Résumé length barely moves it, because the 86 s is fixed:

| résumé | ctx | cold `fields` | margin |
| --- | --- | --- | --- |
| 3 634 (measured) | 2048 | 96.9 s | +53.1 s |
| 6 000 | 2048 | 101.2 s | +48.8 s |
| 12 000 | 4096 | 110.2 s | +39.8 s |
| 30 000 | 8192 | 124.0 s | +26.0 s |

So there **is** margin, but it is hostage to a fixed 86 s, and the failure mode
past the cap is ugly: Modal 303-redirects, `urllib` turns a followed 303 into a
GET, and `/v1/generate` answers `not_found` — a routing error, not a timeout.

**Do not deploy the public endpoint while cold start is 86 s.** Remove the init
and cold `fields` is ~15 s with +135 s of margin, and the question disappears.

### Next: does a GPU snapshot skip the init?

`deploy/modal_snapshot_probe.py`. Modal's GPU memory snapshots are exactly aimed
at "skipping past work like imports and JIT compilation", which is what the 86 s
is — but every published success is an in-process PyTorch workload, and Ollama is
not one. Four things could sink it, and the probe makes each fail separately:

1. **The CUDA context lives in a child process.** `ollama serve` (Go) spawns
   `llama-server` (C++), which holds the GPU. Modal's write-up says it enumerates
   "all active CUDA sessions and their associated PIDs", which is promising but
   untested here.
2. **Ollama is a server with a listening socket.** Restoring one is the part of
   checkpoint/restore most likely not to survive.
3. **It is not PyTorch.**
   [modal-client#4132](https://github.com/modal-labs/modal-client/issues/4132)
   reports restore segfaulting on ~60% of attempts with CTranslate2 — a C++ CUDA
   library, the closest published analogue to llama.cpp.
4. **Driver support on T4.** GPU snapshots need the CUDA checkpoint/restore API
   on driver branch 570/575; the documented example uses an A10.

If it fails, the next step is **not** to change the runtime. It is a straight
L4-vs-T4 comparison — same Ollama, same model, same prompts — to see whether
newer silicon shortens the 86 s.
