# Where to host the inference service

Research, September 2026. **Nothing is deployed.** This is the evidence for one
decision: where `inference_service.py` should run when it stops running on a
laptop, at a ceiling of ~10 users/day and a budget of ₹0.

Prices and limits move. Every number here is dated and sourced; re-check before
acting on it, and note that one of the headline options (Oracle) halved its free
allowance in June 2026 **without announcing it**.

---

## 1. The workload, measured

Not estimated. These are `prompt_eval_count` / `eval_count` from real Ollama
responses for three benchmark résumés through the production prompts, on
qwen3:8b:

| Résumé | Calls | Prompt tokens | Output tokens | Wall time (local, warm) |
| --- | --- | --- | --- | --- |
| bhaskar (fresher, short) | 2 | 888 | 201 | 13.5 s |
| ada (control, one page) | 2 | 916 | 258 | 18.9 s |
| hana (three pages, career change) | 2 | 2 042 | 473 | 29.6 s |

Local generation measured **20.9–21.8 tok/s** on Apple Silicon. The longest
single call is hana's employment call: 1 049 prompt + 230 output tokens.

**Per month at the 10-users/day ceiling** (300 profiles):

| | |
| --- | --- |
| Model calls | 600 |
| Prompt tokens | ~384 000 |
| Output tokens | ~93 000 |
| Total tokens | **~477 000** |
| Concurrent generations | **1** (the service serialises; see the deployment doc) |
| Peak RAM for the model | 5.2 GB weights + ~1 GB KV cache at our 8 192 ceiling |

This is a *tiny* workload. ~16 000 tokens a day. The entire month of inference is
less than one long chat session. **That is the single most important fact in this
document**: nothing here is bounded by throughput. Everything is bounded by
whether a free host will hold 5.2 GB of weights at all, and how slowly it
multiplies them.

---

## 2. Genuinely viable at ₹0

### Oracle Cloud Infrastructure — Always Free ARM (Ampere A1) ⭐ recommended

| | |
| --- | --- |
| Hardware | 2 OCPU ARM64 / 12 GB RAM ([halved from 4/24 in June 2026](https://www.infoq.com/news/2026/07/oracle-cloud-free-tier-limits/), enforced 18 Aug 2026) |
| Storage | 200 GB block, always free — model is 5.2 GB |
| Network | 10 TB/month egress; 1 free flexible load balancer (10 Mbps) |
| Cost | **₹0/month, indefinitely.** Not a credit, not a trial |
| Runs our exact stack | **Yes** — Ollama + qwen3:8b + our gunicorn service, unmodified |

**Fits in RAM comfortably**: 5.2 GB weights + ~1 GB KV + ~1 GB OS ≈ 7.5 GB of
12 GB.

**The bottleneck is CPU token generation.** Ampere A1 measures
[5–8 tok/s for a 7B Q4_K_M at 4 OCPU](https://blog.easecloud.io/ai-cloud/launch-oracle-cloud-llms-in/),
and an independent benchmark put
[Qwen3-8B at 6.13 tok/s on A1](https://tiffena.me/blog/ai-infrastructure/benchmark-cpu-only-llm-inference-oracle-ampere-a1-llama.cpp-ollama-docker/).
At the *new* 2-OCPU allowance, scale that to ~3–4 tok/s. Applying that to our
measured token counts:

| Résumé | Prefill (~20 tok/s) | Generation (~3.5 tok/s) | **Per profile** | Local |
| --- | --- | --- | --- | --- |
| bhaskar | 44 s | 57 s | **~100 s** | 13.5 s |
| ada | 46 s | 74 s | **~120 s** | 18.9 s |
| hana | 102 s | 135 s | **~240 s** | 29.6 s |

**2–4 minutes per profile — roughly 8× slower than local.** For a
once-per-user résumé upload at 10 users/day, that is tolerable. It is not
tolerable for anything interactive.

Worth checking: the longest *single call* projects to ~120 s, comfortably inside
our 300 s `MAX_TIMEOUT` and the 360 s gunicorn worker timeout. **No configuration
change is needed** to run on this hardware.

**Two risks, both manageable:**

1. **Idle reclamation.** Oracle
   [may reclaim an Always Free instance](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm)
   if, over 7 days, CPU **and** network **and** memory utilisation are all below
   20%. At 10 profiles/day our CPU sits near 2%. The escape is the memory
   criterion — and we already have the knob for it. Setting
   `SWEEP_MODEL_KEEP_ALIVE=-1` pins the 5.2 GB model resident, which is 43% of
   12 GB, above the threshold. It also removes a 5.2 GB reload between our two
   calls, which on ARM disk is not free. *One setting buys both.*
2. **Oracle changing its mind.** They halved the allowance in June 2026 with no
   blog post and no email until instances started being terminated. Assume they
   may do it again. At 12 GB there is exactly one halving of headroom left before
   qwen3:8b stops fitting.

Also worth knowing: A1 capacity is frequently exhausted in popular regions and
"out of host capacity" on creation is common. Budget for retries or an
off-peak region.

**HTTPS**: not provided. Terminate with Caddy on the box (automatic Let's
Encrypt, ~6 lines of config) or the free flexible load balancer.

---

## 3. Viable at ₹0 — but on a credit allowance, not free infrastructure

Listed separately, as instructed. These are **vendor allowances that can be
withdrawn**, not free infrastructure.

### Modal — serverless GPU, scale to zero ⭐ recommended upgrade path

[$30/month in credits that renew monthly](https://modal.com/pricing) on the
Starter plan, with 100 containers / 10 GPU concurrency. T4 bills at
**$0.000164/second**.

Our monthly cost against that allowance:

| | Seconds/month | Cost | vs $30 allowance |
| --- | --- | --- | --- |
| Compute (300 profiles × ~11 s on a T4) | 3 300 | **$0.54** | 1.8% |
| Worst case, + a 75 s cold start on every one of 300 | 25 800 | **$4.23** | 14% |

**7×–55× headroom.** Even if the GPU estimate is off by 2×, it stays free.

**The bottleneck here is cold start, not compute.** Weights must reach GPU
memory: 60–90 s naively, dropping to
[2–5 s for 7B–13B models with Modal's GPU memory snapshots](https://www.gmicloud.ai/en/blog/modal-serverless-gpu-functions),
and Modal Volumes stop the 5.2 GB re-downloading each time. At 10 users/day
almost every request is a cold start, so this number *is* the latency.

Modal provides HTTPS web endpoints natively — no reverse proxy, no certificates,
no public IP to defend. The cost is **provider-specific deployment code**, which
this task explicitly defers.

### Cerebras / Groq / Cloudflare Workers AI — free, fast, *different model*

These are genuinely free, genuinely fast, HTTPS, zero-ops — and they **do not
serve qwen3:8b**.

| Provider | Free allowance | Our usage | Qwen available |
| --- | --- | --- | --- |
| [Cerebras](https://www.getaiperks.com/en/ai/cerebras-free-tier-guide) | 1M tokens/day, no card; 8 192-token context cap | ~16k/day = **1.6%** | Qwen3 **235B** |
| [Groq](https://tokenmix.ai/blog/groq-free-tier-limits-2026) | 14 400 req/day, 30 RPM, 6K TPM | 20 req/day = **0.14%** | Qwen3 **32B** |
| [Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/platform/pricing/) | 10 000 Neurons/day, shared across models | small | Qwen (various) |

Our prompts fit Cerebras' 8 192-token context cap (largest is 2 042). Quota is a
non-issue at any of them.

**Why this is not the recommendation despite being the cheapest and fastest
option on the page:** changing the model is not a hosting decision. Every prompt
rule in `local_extract.py` exists because the 52-document benchmark caught
qwen3:8b doing something wrong, and `years_experience` scores 52/52 *with this
model*. A different model needs the whole benchmark re-run before any of that
carries over. It may well be **better** — a 32B model plausibly extracts more
accurately — but that is a measurement project, not a deploy.

Our provider boundary makes it a small change when someone wants to measure it:
`inference.py` would gain an OpenAI-compatible provider alongside `LocalOllama`,
and Sweep would not notice.

---

## 4. Short-term trial only

| Option | What you get | Why it is not a home |
| --- | --- | --- |
| [Fly.io](https://fly.io/docs/about/discontinued-plans/) | $5 one-time credit, 30-day expiry; free allowances removed for new customers in 2024 | Expires. ~$7–10/month for a real setup afterwards |
| [RunPod serverless](https://www.runpod.io/articles/guides/serverless-gpu-pricing) | ~$0.58/hr for a 16 GB worker, sub-200 ms FlashBoot | No recurring free credit. ~$1.45/month at our scale — cheap, not ₹0 |
| AWS / GCP / Azure trials | 12 months or $200–$300 credits | Promotional. Cliff at expiry, and GPU quota is usually refused on trial accounts |
| [Hugging Face PRO](https://huggingface.co/pricing) | $9/month, 25 min/day H200 ZeroGPU, 10 Spaces | A subscription. Listed because it is the cheapest way to *unblock* HF Spaces at all |

---

## 5. Not viable for this workload

| Option | Verdict |
| --- | --- |
| **HF Spaces free CPU Basic** (2 vCPU / 16 GB) | **Blocked at the door.** [Gradio and Docker Spaces now require a paid plan to create](https://huggingface.co/docs/hub/en/spaces-overview) — PRO for personal accounts. Free accounts get only 2 Gradio Spaces on ZeroGPU. The RAM would have been ample; the plan gate is the blocker |
| **HF ZeroGPU** (free tier) | [~5 min GPU/day free](https://huggingface.co/docs/hub/en/spaces-zerogpu) — which our 150 s/day would actually *fit*. But it is a Gradio SDK with `@spaces.GPU`-decorated functions, not a host for a persistent HTTP server, so Ollama cannot run under it. Adopting it means replacing the runtime, reloading weights per invocation, and abandoning our API. Architecture mismatch, not a quota problem |
| **Render free** (512 MB / 0.1 CPU) | Cannot hold a 5.2 GB model — off by 10×. [15-minute spin-down, ~1 min cold start](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026). Viable **only** as a gateway; see §6 |
| **Koyeb free** (512 MB / 0.1 vCPU) | Same RAM problem, and the free Starter tier [closed to new users after the Mistral acquisition in early 2026](https://www.koyeb.com/blog/sustaining-free-compute-in-a-hostile-environment) |
| **Google Colab free** | [12 h maximum runtime, no guaranteed GPU, and terminations for serving a web UI from a managed runtime](https://research.google.com/colaboratory/faq.html). Hostile to being a server, by design and by policy |
| **Kaggle notebooks** | Same shape: session-bounded, notebook-oriented, no stable ingress |

---

## 6. Split architecture vs one host

The question was whether a tiny public API on a free tier plus a model on an
intermittent GPU beats putting everything on one host.

**One host wins, clearly, and the split is worth explicitly rejecting.**

A split (say Render free as the gateway, Modal as the model) costs:

- **Render's own cold start on top of the model's.** Free Render spins down after
  15 minutes idle and takes ~1 minute to come back. At 10 users/day *every*
  request pays that, before the model has been asked anything. It makes latency
  worse, not better.
- **A second network hop, a second secret, a second failure domain.** Our service
  is 60 MB and stateless. There is nothing for a gateway to do that the model
  host is not already doing.
- **Nothing gained.** The gateway's only real job would be to give a fixed public
  address to a model host that has none. Modal web endpoints and an Oracle VM
  both already have one.

The split earns its keep in exactly one case: **the model runs somewhere that
cannot accept inbound connections** — a machine behind NAT, a Colab session, a
laptop. Then the gateway is a necessity, not an optimisation, and the right shape
is the model host polling or holding a tunnel outbound. We are not in that case,
and choosing to be would be choosing the constraint.

**On CPU-vs-GPU**, the honest summary from §2: CPU inference is *acceptable* here
only because the workload is 10 one-off uploads a day and 2–4 minutes of waiting
is survivable for a résumé parse. It is not a general endorsement. At 100
users/day, or if anything interactive ever calls this, CPU stops working and the
answer becomes a GPU.

---

## 7. Recommended architecture

### Now: one Oracle Always Free ARM instance, everything on it

```
                    ┌──────────── the internet ────────────┐
                    │                                      │
   Sweep (laptop / wherever)                                │
   SWEEP_INFERENCE_BACKEND=remote                           │
   SWEEP_INFERENCE_URL=https://infer.example.com            │
   SWEEP_INFERENCE_TOKEN=<secret>                           │
                    │                                       │
                    │  HTTPS :443, bearer token             │
                    ▼                                       │
   ┌────────────────────────────────────────────────────────┴──────┐
   │  Oracle Always Free — VM.Standard.A1.Flex, 2 OCPU / 12 GB      │
   │                                                               │
   │   Caddy :443  ──── TLS termination, Let's Encrypt, auto-renew │
   │        │  proxy to 127.0.0.1:8811                             │
   │        ▼                                                      │
   │   gunicorn :8811  (127.0.0.1 only)                            │
   │     inference_service:create_app()                            │
   │     1 worker · 8 threads · 1 model slot · 360 s worker timeout │
   │        │  bearer token checked before any model work           │
   │        ▼                                                      │
   │   Ollama :11434  (127.0.0.1 only, never exposed)              │
   │        └── qwen3:8b  Q4_K_M, 5.2 GB, pinned resident          │
   └───────────────────────────────────────────────────────────────┘
```

**Services: three.** Caddy, our gunicorn service, Ollama. No database, no queue,
no Docker, no orchestration. Two systemd units (Ollama ships one; add one for the
service) and Caddy's own.

**Environment:**

```bash
# required
SWEEP_INFERENCE_TOKEN=<openssl rand -hex 32>

# the one deliberate tuning decision — see §2, risk 1
SWEEP_MODEL_KEEP_ALIVE=-1        # pin the model resident: kills the reload
                                 # between our two calls AND keeps memory
                                 # above Oracle's 20% reclamation floor

# defaults are already correct for this host; listed to be explicit
SWEEP_INFERENCE_HOST=127.0.0.1   # Caddy is the only thing that reaches us
SWEEP_INFERENCE_PORT=8811
SWEEP_INFERENCE_WORKERS=1        # 2 OCPU cannot serve two generations
SWEEP_INFERENCE_QUEUE_WAIT=30
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:8b
```

Starting it is unchanged from the laptop: `python -m inference_service`.

**Network and security boundaries:**

| Boundary | Rule |
| --- | --- |
| Internet → Caddy | :443 only. OCI security list + host firewall closed on everything else |
| Caddy → service | `127.0.0.1:8811`. `SWEEP_INFERENCE_HOST` stays loopback, so the service is unreachable from outside even if the firewall is wrong |
| Service → Ollama | `127.0.0.1:11434`. **Ollama is never exposed**, which is the point of the boundary — it has no authentication of its own |
| Auth | Bearer token, checked before any model work. A future per-user limiter keys on `principal()` |
| Secrets | One token. No database, nothing persisted, no résumé or prompt written to disk or log |

**Latency, from the measured numbers:**

| | |
| --- | --- |
| Warm (model resident, `KEEP_ALIVE=-1`) | **~100–240 s per profile** (2 calls) |
| Cold (first request after a restart) | + ~30–60 s to load 5.2 GB from disk |
| Cold, if `KEEP_ALIVE` were left at 30 s | + that reload **between the two calls of a single profile** — the reason for the setting |
| `/healthz` | milliseconds, always — it takes no model slot |
| HTTP hop overhead | 0.50 ms median (measured, 200 calls) — noise |

**Failure behaviour** (all already implemented and tested):

| Condition | Behaviour |
| --- | --- |
| Ollama down | `/healthz` → 503 `degraded`; requests → 503 `model_unavailable`. Service stays up. Sweep's `local-first` may spend one Gemini call |
| Second request during a generation | Queues up to 30 s, then **429 `model_busy`** + `Retry-After`. **Never** escalates to Gemini |
| Model slower than the deadline | 504 `model_timeout` |
| Bad token | 401 `unauthorized`, before any model work |
| Prompt > 256 KB | 413, refused before the model |
| Service unreachable from Sweep | `RemoteServiceError`. **No silent fallback to a local Ollama, ever** |
| Deploy / restart | SIGTERM; in-flight generation finishes (330 s graceful window, verified) |
| Oracle reclaims the instance | Sweep fails explicitly. Mitigated by `KEEP_ALIVE=-1`; monitor by watching `/healthz` |

### Later: Modal, behind the same boundary

When 10/day becomes 100/day, or the 2–4 minute wait stops being acceptable, the
upgrade is a GPU with scale-to-zero — projected **$0.54–$4.23/month against a $30
monthly allowance**, and ~11 s of compute per profile instead of 100–240 s.

What makes that cheap to do is that it is *already* only a config change on
Sweep's side. `SWEEP_INFERENCE_URL` points at a Modal web endpoint and nothing
else moves. On the service side, `RUNTIME` in `inference_service.py` is the one
line that names Ollama; swapping it for vLLM, llama.cpp or a hosted endpoint
changes that file and no other. **Sweep never learns which runtime answered.**

The order to try things, if the first choice disappoints:

1. Oracle ARM, CPU, ₹0 — *the recommendation*
2. Modal T4, scale-to-zero, ₹0 against credits — when latency matters
3. Cerebras/Groq with a **re-run benchmark** — when a bigger model is worth measuring
4. RunPod / a rented GPU at ~$1.50–$5/month — when "₹0" stops being the constraint
