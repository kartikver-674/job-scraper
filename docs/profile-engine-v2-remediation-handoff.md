# Profile Engine v2 — remediation batch handoff

**PROFILE ENGINE V2 IS STILL: experimental / not production-approved.**
**ROLE CONSTRUCTION IS: off.**
**PUBLIC BETA SHOULD REMAIN: v1.**

No job-quality improvement is claimed. Nothing was deployed or merged.

This batch is the minimum safe remediation the independent review identified. It
does not finish v2, and it does not address R3, R4 or R5.

---

## 1. State

| | |
|---|---|
| Branch | `feat/profile-engine-v2` |
| HEAD | `4ef38db6b9b6f95c26bbeafe115bcc62b179bf48` |
| Reviewed HEAD (batch base) | `1073e2a871212b2543988faf12a9b78a6a48e3c3` |
| `main` | `05c26f4` — unmoved, no rebase needed |
| Working tree | clean except three untracked docs (§10) |

### Commits

| Commit | Scope |
|---|---|
| `a4ce070` | R1 employment propagation, date association, promotion uncertainty |
| `96fa298` | Real loader schema, engine binding, default v1, numeric bounds |
| `c985320` | Evaluator isolation, restored adversarial fixture |
| `4ef38db` | Privacy: synthetic fixtures |

15 files, +1508 / −112.

---

## 2. Each issue: the failing test before, the passing test after

Every fix followed the same order — reproduce on the production path, watch it
fail, make the smallest correction, watch it pass, run the surrounding suites.

| Issue | Failing before | Passing after |
|---|---|---|
| **R1** `read()` recomputed from rejected rows (1 year → 27) | `test_read_does_not_recompute_from_the_rejected_rows` · `test_read_returns_only_the_validated_rows` · `test_the_answer_and_its_explanation_agree` | same, all pass |
| **R1b** rejected row reached search titles | `test_the_fabricated_title_is_not_a_search_candidate` | passes |
| **Date A** year borrowed from the education section (25 yrs) | `test_a_year_cannot_be_borrowed_from_the_education_section` | passes |
| **Date B** invented `Present` extended a finished job | `test_an_invented_present_cannot_extend_a_finished_job` | passes |
| **Date C** row naming nobody → 27 yrs | `test_a_row_with_no_employer_or_title_cannot_be_located` | passes |
| **Date D** end date in 2030 counted | `test_a_future_end_date_is_not_experience` | passes |
| **Date E** `2023-19` became January | `test_an_impossible_month_is_unreadable_not_january` | passes |
| **Date F** reversed interval | already safe | `test_a_reversed_range_is_still_rejected` |
| **Promotion** qualifier lost with a simplified title | `test_the_simplified_title_is_still_flagged` · `test_the_uncertainty_reaches_the_result_metadata` | pass |
| **R2a** real loader ignored the schema | `test_a_future_schema_is_refused_by_the_real_loader` | passes |
| **R2b** worker scored a v1 profile as v2 | `test_a_v1_profile_on_a_worker_that_says_v2_scores_v1` · `..._v2_profile_on_a_worker_that_says_v1_scores_v2` | pass |
| **Default** was v2 | `test_no_configuration_at_all_is_v1` | passes |
| **Rollback** defeated by a stale flag | `test_a_stale_step_flag_cannot_defeat_an_explicit_v1` | passes |
| **Bounds** `-10`→`+10`, `10**9`, `True`, `1.9` | `test_a_weight_outside_the_scale_is_refused` +5 | pass |
| **R6** evaluator inherited ambient engine | `test_conditions_are_correct_with_a_contaminating_environment` | passes |

Two reproductions are kept as runnable probes in the gitignored review directory:
`r1_probe.py` (27 years) and `r_dates_probe.py` (all six date cases).

---

## 3. What changed, and one correction I got wrong first

**R1.** `route()` now returns the rows it vouched for, and `read()` replaces the
model's answer with them. `local_profile.generate()` consumes `read()`'s rows, so
it was fixed by the same change. The correction text may still name what it
dropped — that is transparency — and the test asserts on the *search fields*, not
the whole profile, so the explanation is allowed to say so.

**Date association — the first attempt was wrong and the benchmark caught it.**
I first required a row's dates to sit within a character window of its own
employer. That failed: in a compact résumé the education section is a few hundred
characters below the last job. I then tried a **line** window, which failed the
other way — a `tables` layout wraps every cell onto its own line, so a genuine
row's dates sit six lines from its employer, and a window tight enough to stop
the borrow **escalated 4 of the 52 benchmark documents**.

What separates the two cases is not distance but whether a **section heading**
stands between them. With that rule: **0 of 52 escalate** and the benchmark is
unchanged. The line cap (14) is a backstop for one enormous section, not the rule.

**Promotion uncertainty.** `ambiguous_span()` reads the document beside the row
now, on a tighter budget than the dates get (1 line), because a title's qualifier
lives on the title's line. A wider budget let one job's qualifier mark the next
job uncertain in a headingless document. **No promotion date is invented** and the
counted span is unchanged — it is still the conservative floor. What is new is
that the floor is declared in the corrections.

**Engine binding.** A profile carries the engine that derived it;
`config.load_profile_module()` reads that stamp and `scraper` binds it, so the
scoring machine's environment cannot disagree with the deriving machine.

**Precedence** is now explicit: bound profile → pinned version → step flags →
`v1`. The middle rule is what makes rollback absolute; the last is what lets the
evaluator compose conditions.

**`mixed`.** Concepts-without-evidence now reports as `mixed`, not `v2`. Calling
it v2 would stamp a profile v2 and later bind evidence that was never used to
derive it — the exact silent disagreement this contract exists to stop. A mixed
profile cannot be bound or loaded.

---

## 4. Configuration

**Code default: `v1`.** `skill_concepts.DEFAULT_VERSION = "v1"`.
**Public beta: `v1`**, pinned in `render.yaml` *and* now protected by the code
default — the pin is a statement of intent rather than the only thing standing
between the beta and unproven behaviour.

| Precedence | Source | Beats |
|---|---|---|
| 1 | the loaded profile's stamp | everything |
| 2 | `SWEEP_PROFILE_ENGINE_VERSION` | step flags |
| 3 | `SWEEP_SKILL_CONCEPTS` / `SWEEP_SKILL_EVIDENCE` | the default |
| 4 | `v1` | — |

`skill_concepts.effective()` reports version, the three booleans, and which of the
four decided.

**Worker:** needs no matching variable. A v1 profile on a worker exporting v2
scores v1; a v2 profile on a worker exporting v1 scores v2; a worker with nothing
exported honours the profile. Verified in **separate interpreters**, because the
binding happens at import.

**Loader:** `config.load_profile_module()` refuses a future schema with the
reason, refuses a malformed stamp, and loads every profile on disk — a profile
written before the stamp existed reports as v1, which is what it is.

**Role families: off** under every version, and a bound profile can never switch
them on.

---

## 5. Tests — from HEAD `4ef38db`

| Suite | Ran | Failed | Skipped | Notes |
|---|---:|---:|---:|---|
| auto-apply | **654** | 0 | 2 | 1 expected failure (§7) |
| Sweep | **770** | 0 | 1 | |
| bench | **43** | 0 | 0 | |
| deploy / worker | **42** | 0 | 0 | |
| **Total** | **1509** | **0** | **3** | |

Engine-focused subset (inside auto-apply): `test_employment_grounding` 53,
`test_engine_contract` 44 (new), `test_skill_concepts` 78, `test_skill_evidence`
52, `test_role_families` 51, `test_generated_profile_safety` 34.

Module self-checks: `local_extract`, `local_search`, `corpus_signal`,
`skill_scan`, `skill_concepts`, `skill_evidence`, `role_families` — all pass.

Logs in gitignored `output/profile-engine-v2-remediation/`.

### 52-document regression

| | Documents | Years exact | Macro F1 | Router |
|---|---:|---:|---:|---|
| v1 | 52 | 52/52 | 0.97628 | 38 / 14 / 0 |
| v2 | 52 | 52/52 | 0.97628 | 38 / 14 / 0 |

Unchanged, both versions, after the date rule.

### The 13 required proofs

1 `test_read_does_not_recompute_from_the_rejected_rows` · 2
`test_the_fabricated_title_is_not_a_search_candidate` · 3
`test_a_year_cannot_be_borrowed_from_the_education_section` · 4
`test_an_invented_present_cannot_extend_a_finished_job` · 5
`test_an_impossible_month_is_unreadable_not_january`,
`test_a_future_end_date_is_not_experience` · 6
`test_a_future_schema_is_refused_by_the_real_loader` · 7
`test_a_v1_profile_on_a_worker_that_says_v2_scores_v1` · 8
`test_a_v2_profile_on_a_worker_that_says_v1_scores_v2` · 9
`test_no_configuration_at_all_is_v1` · 10
`test_a_stale_step_flag_cannot_defeat_an_explicit_v1` · 11
`test_conditions_are_correct_with_a_contaminating_environment` · 12
`test_role_families_are_off_under_every_version` · 13 the 156 tests and 3 demos in
the sanitised files.

---

## 6. Evaluator configuration matrix

Verified with `SWEEP_PROFILE_ENGINE_VERSION=v2` and `SWEEP_SKILL_CONCEPTS=1`
exported — the contamination the review found:

| Condition | concepts | evidence | roles | version | Queries | Wrong | Zero-query |
|---|---|---|---|---|---:|---:|---:|
| A | False | False | False | `v1` | 116 | 4 | 0 |
| B | True | False | False | `mixed` | 116 | 4 | 0 |
| C | True | True | False | `v2` | 116 | 4 | 0 |
| D | True | True | True | `v2` | 41 | 0 | 2 |
| E | True | True | True | `v2` | 41 | 0 | 2 |

`check_isolation()` runs before any measurement and raises if a condition's
effective configuration disagrees with its label. Every record now carries the
effective flags per cell, the source revision, and digests of both frozen
corpora.

**Numbers unchanged from the reviewed run.** That is the expected result: this
batch fixed the harness, not the metrics, and the previously archived table had
been produced under an explicit outer `v1` pin.

---

## 7. Known failure, recorded deliberately

`test_KNOWN_FAILURE_a_sales_title_is_still_admitted` — `expectedFailure`.

Both adversarial sales fixtures are now present. The measured one (~27% of
listings naming anything else an engineer has) is rejected by the pair-share
guard. The **coherent** one — every sales listing genuinely wanting Salesforce
*and* CRM, which is what `SKILL_LIFT` used to reject — is **re-admitted**, and an
engineer is offered a business-development query.

It was not reshaped to pass. Role thresholds are out of scope for this batch. If
it ever starts passing, role construction has changed and the test should become
a plain assertion.

---

## 8. Role output: unchanged

| | |
|---|---|
| v1 keywords == v2 keywords | **True** |
| v1 keywords == audited baseline | **True** |

Role construction was not touched. The audited résumé still receives the Git/Python,
AI-backend and employer-specific SDE queries the review named — **C5 remains
unresolved in production**, exactly as before this batch.

---

## 9. Still unresolved — explicitly out of scope

| | Status |
|---|---|
| **R3** alias-dependent market weighting (`max` over `concept.id` + raw spellings) | **NOT FIXED.** Adding aliases can still change the measured separation and therefore the final weight. |
| **R4** evidence attribution — planned/negated/coursework reaching CORE, project-depth by repetition, shadowing dependent on the candidate set | **NOT FIXED.** |
| **R5** role construction unwired in production; `fill()`'s held-title fallback bypasses revalidation | **NOT FIXED.** Role families remain off. |
| Unknown compound splitting, parent/child semantics, recency, responsibility, career transition | not touched |
| `MIN_PAIR_SHARE`, `SKILL_LIFT`, `DOMAIN_MARKERS` | not tuned |
| Independent labels, ranking evaluation | not attempted |
| Bhaskar / Gopal zero-query personas | not rescued |

Two narrower limits inside what *was* fixed:

- **Date locality is heading-based.** A résumé with no section headings at all
  gives the line cap (14) as the only boundary. The audited and benchmark
  documents all have headings.
- **Promotion uncertainty is flagged, not resolved.** The span is still the
  conservative floor; no allocation between trainee and professional is attempted.

---

## 10. Privacy scan

| Check | Result |
|---|---|
| Identifying résumé excerpts in files this branch added | **6 files found, all replaced** with structurally equivalent synthetic data |
| Test behaviour after replacement | identical — 156 tests + 3 module demos pass |
| Credentials / tokens / private keys in the diff | none |
| Personal email or phone in added lines | none (the `example.com` addresses are the synthetic benchmark people) |
| Personal local paths | none |
| Audit evidence | stays in gitignored `output/` |

**Left alone, and flagged:** `bench/test_backends.py` and the
`profiles/kartik_reachable.py` filename carry the same name and **pre-date this
branch on `main`**. Sanitising them is a separate change.

### The three untracked documents — your decision, not taken

| File | Contains | Safe to commit? |
|---|---|---|
| `docs/profile-engine-v2-baseline.md` | no identifiers, no secrets; corpus/PDF hashes only | **Yes** |
| `docs/profile-engine-v2-independent-review.md` | no identifiers, no secrets | **Yes** |
| `docs/resume-skill-engine-audit.md` | **37 hits** — the résumé filename, two project names, personal paths | **No, not as-is.** Needs the same synthetic replacement, or stays untracked |

Not committed, per the instruction.

---

## 11. Deployment

Nothing changed about what a deployment needs, except that it is now safer:

- **Render** — deploy the branch; `render.yaml` still pins `v1`, and the code
  default is `v1` too.
- **Oracle worker** — no change and no matching variable needed; the profile
  binds its own engine.
- **Modal** — still requires a redeploy, for the same reason as before
  (`inference.py` is one of its three shipped files and changed earlier in the
  branch). `local_extract.py` changed substantially in this batch and is also
  shipped, but Modal uses only its `FIELDS_PROMPT` and `FIELDS_SCHEMA`, both
  **unchanged**.

---

## 12. What a reviewer should check first

1. `local_extract.read()` / `route()` — that validated rows really are the only
   rows, including through `local_profile.generate()`.
2. `local_extract._near()` and `_lines()` — the heading-boundary rule, and
   whether a heading-less résumé is adequately handled by the line cap alone.
3. `skill_concepts.engine_version()` — the four-level precedence, and `mixed`.
4. `config.load_profile_module()` — that nothing else imports a profile.
5. `scraper.py`'s `bind()` call — that binding at import is the right moment.
6. `bench/evaluate.Flags` / `check_isolation()` — that a condition's declared
   configuration is its real one.
7. `test_role_families.TestTheCoherentSharedPlatformMarket` — the known failure.
8. `make_profile._weights()` — the 1–5 / 1–12 split, and the fixture decision
   below.

**One compatibility decision, recorded rather than assumed:** the `PAYLOAD`
fixture's weights `10` and `8` came from an older Gemini prompt using a 1–10
scale. The instruction this repo sends today says *"use 1-5 and nothing higher:
every existing profile is on that scale, and score thresholds are compared across
profiles."* Those values were historical permissiveness, not supported behaviour.
The fixture moves to `5` and `4`; the orderings it asserts are unchanged.

**Three contracts deliberately reversed**, each marked in the test that asserts
them: a row naming nobody is no longer "fine"; the default is no longer v2; a step
flag no longer outranks an explicit version.

---

Returned for another independent review. Not merged, not deployed.
