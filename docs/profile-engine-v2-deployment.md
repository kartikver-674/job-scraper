# Profile Engine v2 — deployment and rollback plan

**Status: prepared, not executed. Nothing is deployed. The public beta is pinned
to v1.** Awaiting approval.

Branch `feat/profile-engine-v2`, head `3edaf4e`, 10 commits, 34 files,
+6346/−68.

---

## 0. What "v2" means here, and what it does not

**v2 = step 2 (canonical concepts) + step 3 (evidence-aware importance).**

It does **not** include step 4 (role-query construction). The evaluation measured
step 4 removing the job search entirely for 2 of 16 personas — a graduate with no
employment and a QA specialist the corpus under-covers — and recommended holding
it. It stays behind `SWEEP_ROLE_FAMILIES`, off, and **is not reachable through the
version switch**; `engine_version()` returning `v2` does not enable it, and a test
asserts that for both versions.

This reading comes from the evaluation's single recommendation ("ship steps 1–3,
hold step 4"). If the approval intended step 4 as well, say so — it is one line in
`skill_concepts.roles_enabled()`, and the 2-persona failure would need fixing
first.

Step 1 (correctness and safety fixes) is **not optional and not flagged**. It is in
both versions: fabricated employment rows, the docstring escape, truncated model
answers and row-order instability are corrected unconditionally.

---

## 1. Final commit set

| Commit | What |
|---|---|
| `33b9680` | row-order invariance in title selection |
| `9d82dbc` | review screen stops recommending common skills for deletion |
| `790ea21` | employment rows checked against the document |
| `3c57ddd` | generated profiles hardened against model prose |
| `f4428cb` | three model-answer boundaries that failed unsafely |
| `e726565` | canonical skill concepts (**v2, step 2**) |
| `625a85a` | evidence-aware importance (**v2, step 3**) |
| `133718f` | role families (**held, flag off**) |
| `429bb7c` | evaluation harness, 16 personas, the report |
| `3edaf4e` | version switch, profile schema stamp, Render pin |

---

## 2. Benchmark comparison

**52-document regression suite — identical under both versions.** Not "close":
identical.

| | Documents | Years exact | Macro F1 | Router |
|---|---:|---:|---:|---|
| v1 | 52 | 52/52 | 0.97628 | 38 corrected / 14 accept / 0 escalate |
| v2 | 52 | 52/52 | 0.97628 | 38 corrected / 14 accept / 0 escalate |

The migration does not touch extraction, dates, layouts or the router. No expected
answer was rewritten.

**16 held-out personas** (`python -m bench.evaluate`), all strata, 0 failed parses,
0 errors in every condition:

| | Concept recall | Queries | Wrong-career | p50 |
|---|---:|---:|---:|---:|
| v1 | 1.000 | 116 | 4 | 387 ms |
| **v2 (shipping)** | **1.000** | **116** | **4** | **390 ms** |
| step 4, held | 1.000 | 41 | 0 | 728 ms |

**v2 as shipping does not change a single query.** Role construction is step 4,
which is held. The risk surface of this migration is **skill weights only**.

What v2 does change, measured across the same 16 personas: core-versus-list-only
importance inversions **19.2% → 0%** (66 of 344 pairs to 0).

**Supplied development résumé:**

| | Terms | Weight distribution | Time |
|---|---:|---|---:|
| v1 | 58 | 2:12 3:33 4:11 5:2 | 409 ms |
| v2 | 57 | 2:6 3:26 4:17 5:8 | 421 ms |

v1 reproduces the audited baseline profile **byte for byte** — that is the rollback
proof, not an assertion.

No job-quality claim is made. There is no independently judged ranking data, so no
Precision@10 or nDCG. See `docs/profile-engine-v2-evaluation.md` §1.

---

## 3. Latency

| Path | v1 | v2 | Δ |
|---|---:|---:|---:|
| Dev résumé, full `_finish` | 409 ms | 421 ms | **+12 ms (1.03×)** |
| 16 personas, p50 | 387 ms | 390 ms | +3 ms |
| 16 personas, p95 | 552 ms | 559 ms | +7 ms |
| Render cold start, frozen data | 1.5 ms | 27.9 ms | +26 ms |

**No new inference calls.** Evidence assessment is deterministic Python over text
already in memory: ~9 ms for 46 concepts. Token usage unchanged. Timeout rate 0.

---

## 4. Profile schema compatibility

Generated profiles now carry one extra top-level name:

```python
PROFILE_SCHEMA = {"version": 1, "engine": "v2"}
```

| Case | Behaviour | Verified |
|---|---|---|
| Profile written before this field | Readable. **Absence is the version** — reported as schema 0, engine v1 | all 44 on-disk profiles load |
| Profile from this build | Readable, engine recorded | test |
| Profile from a **future** schema | **Refused** with "written by a newer build than this one… regenerate" | test |
| Malformed stamp | Refused, names the type it got | test |

**Nothing on disk needs migrating.** `config._overlay` reads only `OVERLAYABLE`
names, so the stamp cannot change how any profile behaves — asserted by test, not
assumed.

Version 1 covers both engines: v1 and v2 emit the same keys with the same types.
The number moves when the *meaning* of a field changes.

---

## 5. Render changes

**One line, already committed** (`render.yaml`):

```yaml
- key: SWEEP_PROFILE_ENGINE_VERSION
  value: v1          # ← the migration is changing this to v2
```

Everything else on Render is unchanged: `SWEEP_PROFILE_ENGINE=local`,
`SWEEP_INFERENCE_BACKEND=remote`, all secrets, all limits.

Cold start with frozen title + skill data is verified for both versions: title
corpus `frozen` (22,806 rows), market signal `frozen` (367 terms), and a profile
derived in both. `sweep/tests/test_public.py` now asserts that render.yaml carries
this pin, so the deployed configuration and the tests of it cannot drift.

---

## 6. Modal changes — **redeploy required**

Modal ships three files (`deploy/modal_serving.py: SOURCE_FILES`), and two of them
changed on this branch:

| File | Changed | Does Modal execute the change? |
|---|---|---|
| `inference.py` | **+10** | **Yes** — the truncation guard is in `LocalOllama.generate`, which the warmup path calls |
| `local_extract.py` | **+190/−11** | **No** — Modal uses only `FIELDS_PROMPT` and `FIELDS_SCHEMA`, both byte-identical; the grounding runs client-side |
| `inference_service.py` | no | — |

The service also reports a SHA256 of all three files in its status payload, so a
stale container is visibly stale.

**The change is low-risk** (a `done_reason == "length"` guard that turns a silently
truncated answer into an error) but it is on a path Modal runs, so the redeploy is
required rather than optional:

```sh
modal deploy deploy/modal_production.py
```

**Modal has no engine-version concept.** It returns model JSON; v1/v2 is decided
entirely by the caller.

---

## 7. Oracle changes — **none required**

`deploy/sweep_worker.py` renders profiles, so the format matters. It was **verified
empirically**, not by reading:

- `render_profile()` accepts v1- and v2-stamped profiles unchanged.
- `assert_only_literals()` allows any `Name` bound to a literal, so
  `PROFILE_SCHEMA` passes by construction.
- 42 deploy tests pass.

The worker receives **already-derived** profile data from Render and only renders
it, so its own engine version affects the `engine` stamp and nothing else. No
worker code change, no worker redeploy, no worker environment change.

If you want the stamp to be accurate rather than default, set
`SWEEP_PROFILE_ENGINE_VERSION` on the worker to match Render. Cosmetic.

---

## 8. Deployment sequence

Nothing below runs until approved.

**Step 0 — merge.** `feat/profile-engine-v2` → `main`. This changes nothing in
production: Render is pinned to v1 and Modal is not yet redeployed.

**Step 1 — Modal.** `modal deploy deploy/modal_production.py`. Confirm the status
payload reports the new `inference.py` SHA256. Production is still v1; this only
picks up step 1's truncation guard.

**Step 2 — Render smoke on v1.** Deploy `main` with the pin still at `v1`. Run §10.
This proves the merge is safe *before* any engine change. **If this fails, stop —
the problem is step 1, not v2.**

**Step 3 — flip to v2.** Change `SWEEP_PROFILE_ENGINE_VERSION` to `v2` in the
Render dashboard. No redeploy needed — the switch is read per request, so it takes
effect on the next derivation.

**Step 4 — Render smoke on v2.** Run §10 again, comparing against the v1 run.

**Step 5 — watch.** Leave it for one full beta day before considering step 4
(role families) or any default change.

---

## 9. Rollback

**Primary — 30 seconds, no deploy:**

```sh
# Render dashboard → Environment → SWEEP_PROFILE_ENGINE_VERSION = v1
```

Read per call, so the next derivation is v1. Profiles already written stay valid
and keep their `engine: "v2"` stamp, which is how you find them later.

**Local / worker equivalent:**

```sh
export SWEEP_PROFILE_ENGINE_VERSION=v1
```

**Full code rollback, if step 1 itself is implicated:**

```sh
git revert --no-commit 3edaf4e..429bb7c && git commit   # engine only, keeps step 1
# or, to leave the branch entirely:
git checkout main && git reset --hard 05c26f4           # pre-migration
modal deploy deploy/modal_production.py                  # only if Modal was redeployed
```

Rolling back the *code* is only needed if step 1's correctness fixes are the
problem. For anything about weights, the environment variable is the rollback.

---

## 10. Production smoke test plan

Run after step 2 (on v1) and again after step 4 (on v2), and compare.

| # | Check | Pass |
|---|---|---|
| 1 | `GET /healthz` | 200; `market_signal_source` and `title_corpus_source` present; `title_corpus_rows` = 22,806 |
| 2 | Upload a known-good résumé PDF | 200, reaches the review screen |
| 3 | Review screen renders | skills listed; **no checkbox pre-ticked**; on v2, importance badges ("Used at work") appear beside "Common" |
| 4 | Role keywords present | ≥1 query; **v1 and v2 must produce the same list** — role construction is held, so any difference is a bug, not a feature |
| 5 | Download `profile.py` | contains `PROFILE_SCHEMA` with `"engine": "v2"` after the flip |
| 6 | Weights are not a flat wall of 3s | ≥3 distinct weights |
| 7 | Derivation latency | within ~1.1× of the v1 run (expected +12 ms) |
| 8 | A second, different résumé | same checks; no crash, no empty skill list |
| 9 | Beta rate limits | unchanged: 3/IP/day, 60/day |
| 10 | Worker handoff | a sweep starts and the worker accepts the rendered profile |
| 11 | Modal status | reports the new `inference.py` SHA256 |
| 12 | Rollback drill | flip to `v1`, derive again, confirm v1 weights return **without a redeploy** |

**Check 4 is the most important.** v2 as shipping changes weights only. If the
query list moves between v1 and v2 in production, something is enabling role
families and the flip should be reverted immediately.

**Stop conditions:** any 5xx on derive; an empty skill list on a résumé that worked
on v1; queries differing between versions; latency above 2× v1.

---

## 11. Residual risks

- **The evaluation's labels were authored by the engine's author.** No independent
  labelling happened. The invariants and the 52-doc suite are objective; the
  wrong-career counts are not.
- **Alias inflation is not exercised by the bench fixtures** — they list one
  spelling per skill. Step 2's benefit rests on the audited résumé and unit tests.
- **No job-quality evidence exists.** v2 is justified as "fixes defects, costs
  ~12 ms, breaks no invariant", never as "finds better jobs".
- **v2 changes weights, and weights order shortlists.** The 19.2%→0% inversion fix
  is a change in what ranks first. It is the intended change, and it is still a
  change a beta user will see.
- **Cold start is 18× slower in relative terms** (1.5 ms → 27.9 ms) and irrelevant
  in absolute ones.
