# Temporary Modal equivalence gate

Prepared 14 September 2026. Implementation is local; **equivalence has not been
proven and this endpoint has not been deployed by this task**. Oracle remains
the reference/backup. Normal Sweep configuration is unchanged.

The previously measured Sarthak results are M1 Pro 37.4 s, Oracle A1 186.9 s,
and T4 snapshot-restored end-to-end 27.97 s (13–14 s profile computation).
The older Oracle ~7-minute observation and CPU extrapolations are separate
historical observations, not the apples-to-apples instrumented comparison.
Token-count variation (53 vs 117 employment tokens) is why this gate exists.

## What runs

`bench.backends` on the Mac → HTTPS Modal WSGI endpoint → the unchanged
`inference_service.create_app()` → loopback Ollama → T4 qwen3:8b.

- Ollama **0.34.0**, qwen3:8b **Q4_K_M**, digest prefix **500a1f067a9f**.
  Startup validates version, quantization, and the installed model. It never
  pulls a model. The benchmark checks the *full* digest against the smoke proof.
- Existing `sweep-ollama-models` Volume mounted at `/root/.ollama`; no model
  download or Volume commit during requests.
- GPU snapshots, one container maximum, eight HTTP inputs, existing one-slot
  semaphore and 30-second slot wait. Idle scale-down is two seconds for this
  experiment. The app itself keeps its existing request-size limits, timeout
  clamp, bearer authentication, error envelopes and operational log.
- Only `GET /healthz` and `POST /v1/generate` are service routes. Diagnostics
  require the Modal SDK/account credentials and are not HTTP routes. No raw
  Ollama API is exposed. Gunicorn is not run: Modal hosts the WSGI callable.
  The unchanged Oracle Gunicorn configuration still has its 360-second timeout.
- Synthetic production-prompt warmup uses numeric `keep_alive=-1` before the
  snapshot. Environment and all subsequent requests use normal `30s`. A freshly
  restored model remains resident until the first real request updates its
  keep-alive; later idle residency follows the normal behavior.
- The snapshot is captured before any real resume arrives. Ollama output is
  discarded; the service's existing metadata logger is used. Prompts, responses,
  resumes and tokens are not written to the Volume or service log.

## Manual commands

Run from `/Users/kartikverma/ReactNative/job-scraper`, with local Ollama already
running and the pinned model installed. The existing `.venv` has Modal 1.5.5.
Do not use `modal run` for deployment: GPU snapshots require a deployed app.

Create a **separate benchmark token** once. This writes only an ignored local
secret file; it does not change the application `.env` or Oracle token:

```bash
cd /Users/kartikverma/ReactNative/job-scraper
umask 077
mkdir -p output/modal-equivalence
.venv/bin/python -c 'import secrets; print("SWEEP_INFERENCE_TOKEN=" + secrets.token_hex(32))' > output/modal-equivalence/token.env
.venv/bin/modal secret create sweep-inference-benchmark --from-dotenv output/modal-equivalence/token.env
export SWEEP_BENCHMARK_TOKEN="$(cut -d= -f2 output/modal-equivalence/token.env)"
```

Do not repeat the token-generation command after creating the Secret: that
would replace the local token without updating Modal. In later shells, use
only the `export` line. If the Secret already exists, inspect it in Modal and
resolve the existing token deliberately; these commands do not overwrite it.

Deploy the temporary endpoint:

```bash
.venv/bin/modal deploy deploy/modal_benchmark.py
```

Prime the snapshot with synthetic text via a private method:

```bash
.venv/bin/python deploy/run_modal_benchmark.py prime
```

Verify an actual restore, public HTTPS authentication and structured generation:

```bash
.venv/bin/python deploy/run_modal_benchmark.py smoke
```

The driver waits for **zero containers**, then makes HTTPS `/healthz` the first
request. Private diagnostics must show the same saved boot ID in a **different
instance**, the expected digest/version/quantization, `size_vram > 0`, `30s`,
and exact hashes for `inference.py`, `inference_service.py`, `local_extract.py`.
It tests missing/wrong tokens, unknown request fields and a synthetic generation
through the existing RemoteService. No resume primes or smoke-tests the snapshot.

If smoke fails, stop. A new boot ID can mean Modal needs another snapshot for
another worker type; prime and smoke again only after checking its Containers
tab. Reusing a warm container is explicitly rejected. Smoke failure clears a
previous saved success. Source changes require redeploying, priming and smoking.

After smoke succeeds, obtain the benchmark URL without changing Sweep's URL:

```bash
export MODAL_BENCHMARK_URL="$(.venv/bin/python deploy/run_modal_benchmark.py url)"
```

First run **the exact Sarthak input** (expected parsed length: 3634 characters):

```bash
.venv/bin/python -m bench.backends --url "$MODAL_BENCHMARK_URL" --endpoint-state output/modal-equivalence/endpoint.json --resume auto-apply/resume/resume.pdf --json output/modal-equivalence/sarthak.json
```

Only after that passes, run short/medium/long representatives from the existing
corpus: `bhaskar-plain`, `ada-plain`, `hana-plain`:

```bash
.venv/bin/python -m bench.backends --url "$MODAL_BENCHMARK_URL" --endpoint-state output/modal-equivalence/endpoint.json --representative --json output/modal-equivalence/representative.json
```

Only after those pass, run **all 52 PDFs** (13 people × four layouts):

```bash
.venv/bin/python -m bench.backends --url "$MODAL_BENCHMARK_URL" --endpoint-state output/modal-equivalence/endpoint.json --all --json output/modal-equivalence/full-52.json
```

An optional Oracle reference is a separate comparison using the same input:

```bash
.venv/bin/python -m bench.backends --url https://sweep-inference.duckdns.org --label oracle --token-env ORACLE_BENCHMARK_TOKEN --resume auto-apply/resume/resume.pdf --json output/modal-equivalence/oracle-reference.json
```

Set `ORACLE_BENCHMARK_TOKEN` to the existing token beforehand. This command does
not change Oracle; its longer latency is not a reason to run it for every gate.
Generic HTTP comparisons with `--label remote` or `--label oracle` can omit
`--endpoint-state`. The default `--label modal` **requires it** and refuses to
run without restored-smoke proof, as supplied by the commands above.

## What a pass means

The benchmark calls `local_profile.generate` for each backend, observes its
actual `local_extract.read` return, then runs production `make_profile._finish`
(skill widening and weight recalculation) and `make_profile.render`. It reads
literal assignments from the rendered source with `ast.literal_eval`; it neither
executes generated Python nor saves/imports a live profile. There are exactly
two model calls per backend/document.

Both sides share the same parsed resume, preferences, month used for `present`,
local market rows and skill frequencies. These local market inputs are read once
per run. No jobs are fetched. Document and market hashes make separate reports
comparable; use the same output corpus between stages. `--output-dir` can select
an existing frozen local corpus.

Compared structures include all checked extracted fields, complete employment
rows/target field, router decision/corrections/reasons, the finished profile and
**every rendered configuration assignment**. This includes `SEARCH`, `SETTINGS`,
`SCORING`, `FEEDS`, title gates and any site overrides, as well as exact
`years_experience`, `experience_months`, `SEARCH.experience_years` and
`SETTINGS.max_experience_years`. The benchmark does not reimplement the `years+3`
calculation. It reads the renderer's result.

The exact count means equality of these parsed structures, not raw JSON strings
or generated tokens. The semantic count permits only these defined differences:

- Reordering the extracted skills/titles/company/education/etc. observation
  lists, with duplicates preserved. Employment row order and query order stay
  strict because held titles and search budget order can depend on them.
- Date spellings resolving to the same month with production `parse_month`.
  Different unreadable strings remain differences; no date is guessed.
- Ordering/casing/outer whitespace in profile title hints/excludes, consumed
  by the production renderer as lowercase sets. Final rendered gates must match.
- Order of correction records, with every record and its values preserved.
- Differences in duplicated display `notes`, reported as benign with an explicit
  display-only reason. The structured correction status/count/outcomes,
  field summary, added skills and their evidence still have to match.

No synonym mapping, numeric tolerance, title rewriting, skill alias expansion,
employment-row deletion or fuzzy match is used. Unknown/new fields stay strict.
Every diff prints document, field path, both values, classification and reason.
Reports include the observed structures for inspection and are saved with mode
0600. They contain personal information; keep `output/modal-equivalence` private.
No report contains bearer tokens or raw prompts/resume text.

The first semantic difference, refusal or transport failure stops the run and
returns exit code 1. An identical escalation on both sides is still not a pass.
The summary reports completed/requested, exact and semantic counts; unrun
documents are never counted as passes. Do not proceed to the next stage after a
failure and do not tune prompts/model/schema to remove a difference.

## Remaining platform caveats

GPU snapshots are still an alpha feature. Modal may capture multiple snapshots
for different worker types and recapture them after runtime/security changes.
Code/configuration changes invalidate snapshots; Volume changes do not. A
successful prime/restore does not guarantee every later boot will restore.
See [Modal memory snapshots](https://modal.com/docs/guide/memory-snapshots).

The 27.97 s figure was a private-function measurement. It is evidence for the
hardware choice, not a measurement of this new HTTP endpoint. Actual endpoint
readiness and correctness remain to be measured by these commands.

Modal Web Functions return a **303 to a result URL after 150 s**, rather than an
application `model_timeout`. Its result URL supports polling; the older docs'
claim that this must become `/v1/generate` → `not_found` was too strong. The
existing provider uses urllib's redirect behavior. No polling or timeout changes
are introduced here. The smoke readiness check refuses redirects and the
benchmark must report failures explicitly. Snapshot restore provides measured
headroom for Sarthak, not a guarantee for every document, recapture or overload.
See [Modal request timeouts](https://modal.com/docs/guide/webhook-timeouts).

The service retains its 300 s timeout clamp and error taxonomy; platform startup
failures/timeouts can occur before the WSGI app can return its structured errors.
Likewise, requests beyond Modal's eight in-flight input slots wait in Modal's
own infrastructure before the app's 30-second semaphore wait begins. This POC
does not claim a globally bounded queue or production availability guarantee.

Ollama/model absence after startup still produces the existing 503 response;
model timeouts 504, malformed model JSON 502, internal errors 500, auth 401,
bad requests 400, oversized requests 413, capacity 429 with `Retry-After`.
The provider does not fall back. This benchmark never enters the Gemini engine.

Local verification:

```bash
.venv/bin/python -m unittest bench.test_backends deploy.test_modal_benchmark -q
(cd auto-apply && ../.venv/bin/python -m unittest discover -s tests -q)
.venv/bin/python -m unittest discover -s sweep/tests -q
```

After the experiment, stop only the temporary benchmark app to remove its
public endpoint. The model Volume, historical probes and Oracle remain intact:

```bash
.venv/bin/modal app stop sweep-inference-benchmark
```

---

## Status, 14 September 2026 — paused; Modal is down

### What ran

| Stage | Result |
| --- | --- |
| Sarthak gate | raw **FAIL** (local repeats the one DealerMatix role), semantic **PASS** after the approved exact-duplicate rule; every search-driving field equal |
| Representative (bhaskar / ada / hana) | 3/3 raw and semantic |
| First full gate (local as oracle) | stopped at `dmitri-plain`: local swaps company/title (4/4), Modal matches the key (5/5) |
| Answer-key scoring, batches 1–3 | **36 of 52 scored** |
| Batch 4 (jonas, kwame, lena, mateo — 16 documents) | **not run** — paused for the credit decision below |

### Interim answer-key result, 36 documents

| | |
| --- | --- |
| Local fully correct on every search/filter-driving field | 35 |
| Modal fully correct | 36 |
| Both fully correct | 35 |
| Local-only correct | 0 |
| Modal-only correct | 1 (`dmitri-plain`) |
| Both incorrect | 0 |
| Raw backend exact / semantic matches | 28 / 28 |
| Behaviour-changing per the backend comparator | 8 — 7 on non-driving fields |
| **Driving regressions (local right, Modal wrong)** | **0** |
| Driving-field correct totals | local 464, Modal 468 |

Reported individually, as it is a Modal error the aggregate would hide:
`iris-twocol` — the **fields-call titles** are wrong on Modal and right on
local. That list only feeds grounding (held titles come from the employment
rows, correct on both), so it is not a search regression. Both-wrong-same-way
fields (projects ×11, companies ×3) come from PDF layouts gluing words together.

Acceptance: three of four criteria pass; the fourth fails only because 16
documents have not run. **Modal is not yet accepted.**

Reports (local, ignored): `output/modal-equivalence/ak-batch-{1,2,3}.json`,
`output/modal-equivalence/answer-key-36-interim.json`.

### Why it paused, and the credit correction

A Modal email reported 85% of a **$1** credit used. Earlier documents here said
Modal's Starter plan carries $30/month; on this account the $30 was unlocked
only by **adding a payment method**. With a card on file, the rule is now:
Modal must never cost more than the $30 credit, even by mistake. Modal stays
down until the next billing period.

- Every Modal app is **stopped** (`sweep-inference-benchmark`,
  `sweep-ollama-snapshot-probe`); 0 containers.
- Kept, because they cost nothing: the `sweep-ollama-models` Volume (5.2 GB,
  inside the free 1 TiB) and the `sweep-inference-benchmark` Secret.

### The spend guard — set it before anything is deployed again

Neither setting is available from the CLI. In Modal: **Settings → Usage &
Billing** (`/settings/usage`), as a Workspace Owner or Manager:

1. **Workspace budget — $25.** The budget is Modal's "hard outer cap" on total
   usage before credits. Set below $30 because Modal does not document how
   quickly a limit is enforced or whether usage can overshoot it.
2. **Spend limit — $0** if the page accepts it (out-of-pocket charges after
   credits; the docs do not say whether $0 is allowed). If it does not, use the
   smallest value it accepts; the $25 budget still bounds total usage.

The budget is the guarantee, not the code. The code only bounds the rate: the
benchmark endpoint runs at most one T4 container (`max_containers=1`, idle
scale-down 2 s, bearer token required) — about $0.59/h of GPU, roughly $0.66/h
with CPU and memory, so $25 would take ~38 continuous hours. The batch runner
stops the endpoint on exit, whatever the exit.

### Resuming next billing period

Re-run all 52 in the new month. September's batches cannot be merged with
October's — the merge refuses different clock months, because "present" dates
resolve against the clock.

```bash
# 0. budget + spend limit set in the Modal dashboard (above)
ollama serve &                                 # local baseline; qwen3:8b 500a1f067a9f
export SWEEP_BENCHMARK_TOKEN="$(cut -d= -f2 output/modal-equivalence/token.env)"
.venv/bin/modal deploy deploy/modal_benchmark.py
.venv/bin/python deploy/run_modal_benchmark.py prime
.venv/bin/python deploy/run_modal_benchmark.py smoke
caffeinate -i bash deploy/run_answer_key_batches.sh   # ~50 min, ~$0.5; stops the endpoint on exit
# verdict: output/modal-equivalence/answer-key-YYYY-MM/merged.log
```

If the machine resets mid-run, run the last command again: batches already
saved in this month's directory are skipped.

### The Mac

The 14 September reset was an **undervoltage lockout** (`vdd_under`,
`vdd_hi_uvlo`), the third with that signature (twice on 10 September), on AC
through Apple's 96 W adapter with a healthy battery. That points at the
machine, not the workload or the charger. Sustained local inference can trigger
it; run Apple Diagnostics.
