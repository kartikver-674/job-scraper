# V3 Step 1 — role signals preserved and measured

**Nothing was deployed, nothing was committed, and no user's job results
change.** This batch preserves two role-shaped fields the model already
produces and throws away, measures how good they are, and proves the addition
is inert. It builds no role model, consumes nothing, and touches no query.

| | |
|---|---|
| Base SHA | `c600fb4161f449991caa4f425dbbe41e4ebfbfdc` |
| Final SHA | **`c600fb4161f449991caa4f425dbbe41e4ebfbfdc`** — no commit was made; the change is in the working tree |
| Engine | v2, unchanged. `render.yaml` untouched |
| Profile schema | **1, unchanged** |
| Role construction | off and absent, unchanged |

---

## 1. Audit hygiene, done first

**Current main SHA:** `c600fb4161f449991caa4f425dbbe41e4ebfbfdc`, working tree
clean apart from untracked docs.

**Defect-ID corrections in the audit — documentation only.** The definitions
in §16 were already correct (V3-C4 free-source gate, V3-C5 orphan anchor,
V3-C6 fragment→canonical revalidation gap). Two cross-references in §20 were
wrong and are fixed:

| Location | Was | Now |
|---|---|---|
| §20 Step 4 (free-source gate) | `V3-C6` | **`V3-C4`** |
| §20 Step 5 (orphan anchor + revalidation) | `V3-C4 and V3-C5` | **`V3-C5 and V3-C6`** |

Two further documentation-only errors of the same class were found while
reading and are also fixed, listed here so the change is reviewable rather
than silent:

| Location | Was | Now |
|---|---|---|
| §11.1 methodology | "Four independent Salesforce-BA variants" | "Three independent synthetic Salesforce-BA variants", with a pointer to §8.5 |
| §23 answer 1 | "four independent synthetic Salesforce-BA variants" | "three" |

There are three synthetic Salesforce-BA variants; §8.5 contrasts them with
three further Salesforce personas. **No measurement, table, count or conclusion
was altered.**

**Data-access policy, recorded.** Future Profile/Discovery Engine work may
inspect only:

- explicitly supplied résumé files,
- synthetic fixtures,
- existing approved audit artifacts under the gitignored `output/` tree.

No personal directory is to be scanned for candidate documents. The v2 audit
requested two résumés, searched `~/Downloads`, found four and processed all
four; that is recorded as a correction in the audit's own §3 and will not be
repeated. The policy is also written to the project memory so it survives this
session.

---

## 2. Where the fields come from, and where they died

Traced in the current code, not from a handoff.

| | `target_field` | `titles` |
|---|---|---|
| Produced by | **EMPLOYMENT call** — `local_extract.employment`, `EMPLOYMENT_SCHEMA` (`local_extract.py:216-248`) | **FIELDS call** — `local_extract.extract`, `FIELDS_SCHEMA` (`:143-162`) |
| Prompt asks for | "the BROAD profession this person is looking for work in now, in two or three words, taken from their most recent role" | "job titles held, exactly as written. Keep seniority words" |
| Schema validation | `type: string`, in `required`. Root object type-checked by `_object()`. No value validation | `array of string`, in `required`. Same root check |
| Grounding | **NONE.** It lives in the employment answer; `check_grounding` only validates the FIELDS answer, over `GROUNDED_FIELDS = ("companies","skills","titles","institutions")` (`:438`) | **YES.** `titles` is in `GROUNDED_FIELDS`, so every entry must appear in the document or it is dropped, and a mostly-confabulated list escalates |
| Survives `read()` | yes, inside `rows` | yes, inside `checked` |
| **Died at** | `auto-apply/local_profile.py:171` — read into `field`, used only by `_summary()` and `_notes()`, i.e. one sentence of docstring prose | **nowhere at all.** Exhaustive grep: outside `bench/` and tests, `checked["titles"]` is read by no production code |

They differ across the two calls, and the asymmetry matters: **one is grounded
and one is not.** A later consumer must not trust them equally, so the record
carries that fact explicitly rather than leaving it to be rediscovered.

---

## 3. What was added

`local_extract.role_signals(checked, rows)` — a new pure function, plus two
lines in `local_profile.generate` that call it and attach the result.

```python
role_signals = {
    "target_field": "salesforce business analysis",   # EMPLOYMENT call, ungrounded
    "titles": ["Salesforce Functional Consultant"],   # FIELDS call, grounded
    "employment_titles": ["Salesforce Functional Consultant"],  # validated rows, raw
    "grounded": {"titles": True,
                 "target_field": False,
                 "employment_titles": True},
}
```

**The three sources are kept apart on purpose.** Merging them here would
destroy the one measurement the next step depends on: where they disagree
(§7). `employment_titles` are the validated rows' titles kept raw — no
seniority stripping, no two-word rule — so a comparison can still see what the
résumé said.

**Sanitisation**, because this is model prose travelling into a dict a renderer
and a review screen read: non-strings are dropped rather than coerced, control
characters are removed, whitespace collapses, entries are capped at
`SIGNAL_CHARS = 120` and lists at `SIGNAL_ITEMS = 20`, and lists deduplicate
case-insensitively while preserving order.

**Carried through:** `local_extract.read` → `local_profile.generate` → the
returned profile → `make_profile._finish` (`split_compounds` → `widen_skills`
→ `reweight_from_evidence`, all of which rebuild the dict and were verified to
preserve additive keys) → the profile the review screen and `render()` receive.

### Files changed

| File | Change |
|---|---|
| `local_extract.py` | **+92 / −0.** `role_signals()`, `_signal()`, `_signal_list()`, `SIGNAL_CHARS`, `SIGNAL_ITEMS`, `_CONTROL` |
| `auto-apply/local_profile.py` | **+11 / −0.** One call, one key, two comments |
| `auto-apply/tests/test_role_signals.py` | new, 21 tests |
| `docs/profile-engine-v3-holdout-protocol.md` | new |
| `docs/profile-engine-v2-forensic-audit-for-v3.md` | documentation-only corrections (§1) |

Total production diff: **103 insertions, 0 deletions, 2 files.** Purely
additive.

---

## 4. Schema decision: NO BUMP, and the record does not enter the rendered file

**Decided from the loader contract, not guessed.**

The contract has two halves that point in opposite directions:

1. `make_profile.py:914-918` states the rule: *"Bumped when the MEANING of a
   profile's fields changes… A future step that adds evidence to the file
   itself is what makes this 2."*
2. `config.load_profile_module` (`config.py:976-1004`) is the **real** loader —
   it calls `make_profile.profile_schema()` and **`sys.exit`s** when
   `version > PROFILE_SCHEMA`. It runs at import time on every scraper run,
   including on the Oracle worker, which holds its own checkout.

So emitting `role_signals` as a new top-level name in `profiles/<name>.py`
would be "adding evidence to the file", would require adding it to
`PROFILE_NAMES` for the AST allowlist, and by rule (1) would make the schema 2
— which by fact (2) would **hard-exit any worker not yet updated**, and would
change the `profile_schema` field `/healthz` publishes and a public test
asserts.

That is a mandatory deployment ordering, created by a batch whose entire
purpose is to change nothing.

**Therefore:** the record is carried in the profile **dict** and is *not*
rendered into `profiles/<name>.py`. `render()` emits only `PROFILE_NAMES`, so
this needs no code to enforce and is covered by a test. No schema bump is
required, the contract stays intact for the step that genuinely needs it, and
the bump can then be paired with the worker deployment it implies.

Precedent: `skill_importance` is exactly this — a rich derived record carried
in the dict and deliberately never rendered.

**This is a deviation from the literal instruction** ("→ generated profile"),
and it is flagged rather than buried. If the record is wanted inside
`profiles/<name>.py`, that is one line in `PROFILE_NAMES`, one line in the
render template, a `PROFILE_SCHEMA` bump to 2, and a worker-first deployment —
and it should be its own reviewed change.

Existing profiles are unaffected: legacy (no stamp), schema-1/v1 and
schema-1/v2 all remain readable, and a future schema is still refused. Engine
binding is untouched.

---

## 5. Zero-behaviour-change proof

`output/profile-engine-v3-step1/zero_change_proof.py`.

**Method.** The BEFORE state is the audit's own captured output, produced by
the pre-change code. Each candidate's **cached extraction** is replayed through
the **current** code with `local_extract.read` stubbed to return the recorded
`(checked, rows, decision)`, so the model cannot introduce noise and the only
variable is the diff. Everything downstream is the real production path.
Comparison is by SHA-256 of a canonical dump.

**Compared:** `role_keywords`, `ranking`, `title_hints`, `skills`,
`filler_dropped`, `from_orphans`; the SEARCH/SCORING-shaped profile fields; the
**effective free-source gate** (profile hints ∪ `config.ATS_TITLE_HINTS`);
`skill_weights`; every `skill_importance` row field by field (tier, weight,
`market_separation`, sections, `status_counts`, `evidence_strength`,
`independent_entries`, why, **resolved market key**); derived concept ids; and
**scorer output** on a fixed synthetic job text.

| Cohort | Scope | Result |
|---|---|---|
| 56 personas | full: search **and** weights, tiers, market keys, scorer | **54 identical**, 2 skipped |
| the 2 skipped | both escalate before a profile exists | **byte-identical escalation message**, verified separately |
| 4 real résumés | search fields + free gate only | **4 identical** |

Persona text was regenerated deterministically from `defs.py`/`render.py` and
its sha256 checked against the sha the audit recorded, before it was used.

The real résumés' source PDFs were **not re-read** — the new data-access policy
— so only the text-independent half could be replayed for them. The
text-dependent half (weights and tiers) is proven on the 54 personas, and is in
any case unreachable by this diff: `_finish` receives one extra key and passes
it through.

**The only key delta on all 54 profiles is `role_signals`.** No query changed.

**SEARCH OUTPUT CHANGED: no.**

---

## 6. `target_field` quality

56 personas, live `qwen3:8b`, against the independently authored labels.
`target_field` is a **profession phrase**, not a job title, so the audit's title
classifier is the wrong instrument; every distinct value was hand-classified
and the full table is printed by `measure_signals.py`.

Rule applied: **DOMAIN** means the phrase names a discipline so broad that it
does not distinguish the person's role from families their own labels mark
forbidden.

| Stratum | n | PRIMARY | PLAUSIBLE | DOMAIN | INCORRECT | EMPTY |
|---|---:|---:|---:|---:|---:|---:|
| software | 13 | 6 | 0 | **7** | 0 | 0 |
| tech non-developer | 14 | 10 | 0 | 4 | 0 | 0 |
| **non-technical** | 16 | **16** | 0 | **0** | 0 | 0 |
| hybrid / adversarial | 13 | 10 | 1 | 2 | 0 | 0 |
| **all** | **56** | **42 (75.0%)** | 1 | **13 (23.2%)** | **0** | **0** |

- **Names the primary role: 75.0%. Primary or plausible: 76.8%.**
- **Incorrect: zero. Empty: zero.** On 56 documents the model never named a
  wrong profession and never declined to answer.
- **12 of the 13 DOMAIN failures are the single string `"software
  engineering"`.** The thirteenth is `"service operations"` for a ServiceNow
  administrator.

The shape of the failure is the finding:

> `target_field` is **100% primary-naming for non-technical candidates** and
> degrades only for technical ones, where the model answers with the industry
> instead of the role. That is the exact inverse of every other signal in the
> system, which serves software candidates best.

The audit's known counterexample reproduces and is now quantified: Scrum Master
→ `"software engineering"`. So do `swe_qa`, `swe_devops`, `swe_backend`,
`swe_java`, `swe_dotnet`, `swe_reactnative`, `pm_technical`,
`sol_consultant`, `adv_dev_to_pm`, `adv_grad_cs` and `adv_swe_titled_sf_work`.
**23.2% of the time the field names an industry, not a role.** It is evidence,
not ground truth, and must never be used alone.

---

## 7. `titles[]` quality, and the agreement matrix

| Measure | Result |
|---|---|
| personas with a non-empty `titles[]` | 55/56 (the one empty escalated) |
| contains the **primary** role family | 49/55 — **89.1%** |
| contains a **forbidden** family | **0/55 — 0.0%** |
| **equals `employment[].title` exactly** | **55/55 — 100%** |
| **carries anything `employment[].title` does not** | **0/55 — 0.0%** |

**`titles[]` is fully redundant with employment titles on this matrix, and it
adds no unsupported role.** It is safe and, as extracted today, it is not an
independent signal.

The audit hypothesised it might carry a résumé **headline** the employment rows
lack. Checked on the four real résumés, where headlines genuinely differ:

- Case A's headline is *Salesforce Business Analyst*; its employment title is
  *Salesforce Functional Consultant*. `titles[]` returned **only the employment
  title**. The headline was lost by **extraction**, not by plumbing.
- Case D's `titles[]` is a **subset** of its employment titles — it can also
  drop one.

So the headline evidence the role layer wants is not currently being extracted
at all. That is a finding for a later step, not something plumbing can fix.

### Signal agreement matrix (no conflict resolved here)

| Outcome | n | share |
|---|---:|---:|
| all three agree | 41 | 73.2% |
| two agree, one does not | 13 | 23.2% |
| **conflict (at most one agrees)** | **0** | **0.0%** |
| only one source present | 2 | 3.6% |
| no usable role signal | **0** | **0.0%** |

**Every candidate has at least one usable role signal, and no candidate has a
three-way conflict.** Of the 13 disagreements, **12 are the same shape**:
`target_field` says DOMAIN while `titles` and `employment_titles` are right.

The thirteenth is the interesting one and the best argument for carrying
`target_field` at all:

> **`adv_career_switcher`** — five years in sales, retraining into software.
> `target_field` = `"software development"`, correctly naming the **new**
> career. `titles` and `employment_titles` both name the **old** one.
> It is the only source that gets the career switcher right, and it is the
> case the current pipeline fails hardest: that persona receives **no profile
> at all**.

---

## 8. V2 components re-run, unchanged

| Component | Suite | Result |
|---|---|---|
| employment-row validation | `tests.test_employment_grounding` | |
| canonical alias invariance | `tests.test_canonical_hardening`, `tests.test_skill_concepts` | |
| occurrence semantics | `tests.test_evidence_semantics` | |
| evidence strength | `tests.test_evidence_depth` | |
| clause scope | `tests.test_clause_scope` | |
| generated-profile safety | `tests.test_generated_profile_safety` | |
| engine-version binding | `tests.test_engine_contract` | |
| (the nine together) | | **386 tests, OK** |
| row-order determinism | `sweep.tests.test_title_corpus` | **34 tests, OK** |
| deterministic date arithmetic | `bench.dates --demo` | **52 documents, 52/52 years exact** |
| answer key | `bench.test_answer_key` | **19 tests, OK** |
| module self-checks | `local_extract`, `local_search`, `skill_concepts`, `skill_evidence`, `corpus_signal`, `local_profile`, `config` | all ok |

**V2 SKILL ENGINE: unchanged.**

---

## 9. Full test results

Baseline taken in the **same working tree and environment**, by stashing the
two modified files and parking the new test file, then restoring both. A clean
`git worktree` was tried first and rejected as a baseline: it lacks the
gitignored `output/` corpus, which several Sweep and bench tests read, and it
reported spurious failures for that reason alone.

| Suite | Before (HEAD, same env) | After | Delta |
|---|---:|---:|---|
| auto-apply | 728 OK | **749 OK** | +21 — exactly the new test file |
| Sweep | 816 OK | **816 OK** | 0 |
| bench | 43 OK | **43 OK** | 0 |
| deploy / worker | 42 OK | **42 OK** | 0 |
| **total** | **1,629** | **1,650** | **+21, 0 failures either side** |

### One flaky error, investigated and not mine

`deploy.test_sweep_worker.TestTheApifyToken.test_a_held_key_is_written_nowhere_and_logged_nowhere`
errored **once**, on the first deploy run, immediately after the 268-second
Sweep suite. It did not recur in **3** further runs on this tree, **25** runs
at HEAD in a clean worktree, or the same-environment HEAD baseline above, and
the suite passes in isolation. One occurrence in roughly 30 runs.

The mechanism is in code this batch does not touch. `sweep_worker._wait`
(`deploy/sweep_worker.py:428-446`) guards `store.read` against the run
directory disappearing — its comment says so explicitly — but the
`store.update` immediately after is **not** guarded, so `os.replace` at
`:250` raises `FileNotFoundError` inside a worker thread when the test's
`tempfile.mkdtemp` directory is removed while `_wait` is still finishing.

That is a pre-existing latent race in the worker's own error handling, worth
its own small fix, and unrelated to role signals. It is recorded here rather
than left as an unexplained red line.

---

## 10. Holdout protocol

[docs/profile-engine-v3-holdout-protocol.md](profile-engine-v3-holdout-protocol.md),
plus, under gitignored `output/profile-engine-v3-holdout/`:
`LABELLING-INSTRUCTIONS.md`, `labels.template.json`, an empty `labels.json`,
and `lock_labels.py`.

The protocol fixes candidate sourcing (explicitly supplied files only),
stratum floors with the **largest floor on non-technical** candidates, the
three-field instrument (`primary` / `plausible` / **`must_not_generate`** with
a written reason per entry), labeller independence, ≥25% blind double review
with disagreements reported rather than reconciled, and hash-locking before the
first v3 role output.

`lock_labels.py` refuses to lock an empty or malformed set, enforces the
stratum floors, requires a reason for every `must_not_generate` entry, rejects
a family that is both supported and forbidden, and reports loudly if the file
changed after a lock. Verified against a throwaway malformed set; the throwaway
was deleted.

**INDEPENDENT HOLDOUT LABELS: NOT YET COLLECTED.** No candidates supplied, no
labels written, no second labeller arranged, no lock taken. Nothing was
fabricated to fill the gap.

---

## 11. Known failures and limits

1. **`target_field` is wrong-shaped 23.2% of the time**, always by naming an
   industry rather than a role, and almost always the string
   `"software engineering"`. Any consumer must treat it as evidence, not truth.
2. **`titles[]` adds nothing** over `employment[].title` on 55/55 personas and
   on all four real résumés, and it loses the résumé headline even when the
   headline differs from the employment title. The headline is not extracted.
3. **`target_field` is ungrounded.** Unlike `titles`, nothing checks it against
   the document. The record says so; no consumer enforces it yet.
4. **The measurement set is synthetic and self-labelled.** The 56 personas are
   development data whose labels were written by the agent that wrote the
   audit. §10 exists precisely because these numbers cannot certify v3.
5. **Two personas still receive no profile** — the operations manager
   (V3-C8, every management title is dropped) and the career switcher. Step 1
   changes neither, and the career switcher is the one candidate whose
   `target_field` is uniquely correct.
6. **The real résumés' weight half was not re-proven**, because the source PDFs
   were deliberately not re-read. Proven on 54 personas instead.
7. **Nothing was committed.** The change sits in the working tree.

---

## Final summary

**TARGET_FIELD:** **useful signal** — 75.0% names the primary role, 76.8%
primary-or-plausible, **0% incorrect**, 0% empty, and **100% correct on
non-technical candidates**, the population the system serves worst. Degrades
to a broad industry name 23.2% of the time, almost entirely on technical
candidates, so it is evidence and never ground truth.

**TITLES:** **weak signal** — grounded, safe, 89.1% contains the primary
family, 0% adds a forbidden family, but **100% redundant** with
`employment[].title` and it loses the résumé headline even when they differ.
Worth carrying because it costs nothing; not worth relying on until headline
extraction exists.

**EMPLOYMENT TITLES:** **useful signal** — validated against the document,
already the basis of held queries, and correct for the primary family wherever
`titles[]` is. Its weakness is structural rather than extractive: it names the
job the person *had*, which is wrong for a career changer, and it is the source
V3-C8 then deletes whenever the title contains "manager".

**ROLE SIGNAL AGREEMENT:** all three agree **41/56 (73.2%)**; two agree
**13/56 (23.2%)**; three-way conflict **0/56 (0.0%)**; only one source present
**2/56**; no usable signal **0/56**. 12 of the 13 disagreements are
`target_field` naming an industry while the other two are right. The 13th is
the career switcher, where `target_field` alone is correct.

**SEARCH OUTPUT CHANGED:** **no.** 54/54 personas byte-identical across search
fields, free gate, weights, tiers, market keys and scorer; 2/2 escalations
identical; 4/4 real cases identical on search fields.

**V2 SKILL ENGINE:** **unchanged.** 386 frozen-component tests, 34 row-order
tests, 52/52 date regression, all module self-checks.

**INDEPENDENT V3 HOLDOUT:** **protocol only.** Labels not collected, no lock
taken, nothing fabricated.

**READY FOR INTERIM FREE-GATE DESIGN:** **yes** — with one constraint that the
measurement here imposes. `target_field` is reliable enough to widen a gate
(0% incorrect) but not to narrow one (23.2% names an industry). An interim
gate keyed on it must therefore **only add** families it names and must not
remove config's floor, or the 13 DOMAIN cases become 13 starved sweeps.

**READY FOR ROLE_EVIDENCE:** **no — next step must be separately reviewed.**

---

Stopping here. The free gate was not touched, canonicalisation was not moved,
`role_evidence` was not built, orphan anchors were not touched, nothing was
deployed and nothing was committed.
