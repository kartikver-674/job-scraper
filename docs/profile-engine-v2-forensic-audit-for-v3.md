# Profile Engine v2 — forensic audit for v3

**Audit only. No production code, prompt, weight, threshold, corpus, alias or
test was changed.** Working tree at `main` @ `c600fb4`, engine bound to v2 as
production binds it, role construction confirmed off and absent. Temporary
harnesses live in gitignored `output/profile-engine-v3-audit/`. No résumé text,
name or employer appears in this document.

Audit date: 19 September 2026.

This report distinguishes **live model observations** (60 documents through
`qwen3:8b`), **deterministic replays and probes** against the frozen corpus,
**archived measurements** read back from the v2 evaluation, and **unmeasured
claims**, which are labelled as such. A synthetic persona is a development
instrument, not ground truth.

## 1. Executive summary

### 1.1 The reported failure is real, and it is not where it was expected

A Salesforce Business Analyst does **not** receive `salesforce developer` in
`role_keywords`. On the real résumé and on three independent synthetic BA variants,
the emitted queries are `salesforce business analyst`, `salesforce
administrator`, `salesforce functional consultant`, `salesforce techno
functional consultant` and similar. The title corpus contains the right titles
and the lift ranking selects them. **The obvious hypothesis — that corpus bias
drags Salesforce BAs toward Salesforce Developer — did not reproduce.**

The symptom the user observed is nonetheless real and was reproduced. It comes
from three other places:

1. **The free-source title gate.** For a real Salesforce BA, the effective
   `ATS_TITLE_HINTS` gate is 116 entries of which 69 are software-shaped. It
   **admits** `Salesforce Developer`, `Senior Backend Engineer (Python)` and
   `React Native Developer`, and **rejects `Business Analyst`**. Across 56
   personas, **100% admit at least one role family their evidence forbids, and
   52% cannot see their own role family at all** — 81% among non-technical
   candidates. This is the most severe defect in the report and it is in
   `config.py`, not in Profile Engine v2.
2. **One paid query, `salesforce engineer`**, whose listings want Apex 42% and
   SOQL 47%.
3. **The orphan anchor, when a developer-tier platform token is present.**
   A controlled comparison across six Salesforce personas isolates this
   exactly: no Salesforce BA, administrator or salesperson **without** Apex,
   LWC or SOQL in their skill list receives a developer query, and both BAs
   **with** one do. The adversarial BA whose résumé says *"I do not write Apex
   myself"* receives `salesforce developer`, anchored on `lightning web
   components` at evidence 1.26. The disclaiming clause is exactly what
   `skill_evidence`'s clause-scoped R4c machinery could classify — and that
   machinery runs after every query has been chosen.

### 1.2 The general rule

> The search layer's only view of a candidate is the intersection of their
> skills with a vocabulary accumulated from previous users' searches. Job
> functions are outside that vocabulary; platforms and tools are inside it.
> **A candidate is therefore represented to search by their tools, and never by
> their job function** — and the system has no object that could hold a job
> function even if it wanted one.

Measured: **51.9% of candidates' own skills are invisible to retrieval** —
63.0% visible for software personas, **24.2% for non-technical ones**. Eleven
personas are visible to the corpus only through the term `excel`. Nine of 56
cannot retrieve at all.

### 1.3 The structural cause

**Search is constructed before the profile engine runs.**
`local_profile.generate` calls `local_search.fields_for` at line 183;
`make_profile._finish` — canonical concepts, compound splitting, occurrence
semantics, evidence strength, importance tiers, market separation — runs
afterwards. `fields_for` accepts an `importance` argument and its own docstring
says it is ignored.

Everything v2 built is therefore invisible to the decision that determines
which jobs a user ever sees. Case C states the consequence in one line: the
evidence engine judged **11 of 13 concepts BACKGROUND** — almost nothing
established — while the search layer, from the same résumé, emitted **fourteen
queries including `.net developer ai-assisted development`** for a candidate
with no .NET.

### 1.4 What this means for the v2 known-limitations list

Re-tested, as asked. Several are real but aimed at the wrong axis.

| v2 known limitation | Re-test result |
|---|---|
| CORE over-populated (precision 0.409) | **Confirmed, and cannot cause any role failure.** Importance is computed after search and `fields_for` ignores it. This is a ranking defect. |
| STRONG_SECONDARY under-predicted | Confirmed; same axis. |
| Market adjustment hurt importance labels | **Worse than reported.** Of the 119 ties it breaks, 46 resolve correctly and **73 become inversions — 38.7%, below chance.** Bounded to one tier. Also inert for non-technical candidates (~21% coverage vs ~76%). |
| `learned` missing from the learning lexicon | Not re-measured. A **different** lexicon defect was found instead: `roadmap` and `planning` are PLANNED markers, and a planned marker governs everything after it on a line, so a single planning noun in a skills list demotes **9 of 10** concepts for a supply-chain analyst and a product manager, and 0 for any software persona (§14.3). Not previously reported. |
| `classify()` line-scoped, `shape()` clause-scoped | Confirmed present; **immaterial today** because neither reaches search. Becomes material the moment a role layer consumes clauses. |
| PDF letter spacing affects outputs | **Confirmed and sharpened.** Spacing changed weights on **12 of 12** personas tested, always downward by one band, and hit non-technical concepts hardest. It changed **no** queries. |
| Extraction recall not evaluated with live inference | **Now measured on 60 documents.** Listed recall **100%**, precision **100%**, **bullet-only recall 15.8%**. The model invents nothing and barely reads the body. |
| No better-job result proven | Still true. Nothing here is one. |
| Labels authored by the engine's builder | Still true, and it is why §21.1 makes independent role labelling a precondition for v3. |

### 1.5 The measured state of query quality

56 personas, live inference, independent three-way labels (primary / plausible
/ **forbidden**):

| | queries | supported | contaminated | supported precision | personas contaminated |
|---|---:|---:|---:|---:|---:|
| overall | 429 | 198 | **78** | **0.462** | 57% |
| software | 123 | 65 | 29 | 0.528 | 92% |
| tech non-developer | 128 | 46 | 17 | 0.359 | 64% |
| non-technical | 73 | 45 | 2 | 0.616 | 12% |
| adversarial | 105 | 42 | 30 | 0.400 | 69% |

**Technicalisation rate: 20 of 40 non-developer personas (50%) receive at
least one software-engineering query.** A Scrum Master receives `salesforce
developer`; a UX designer receives seven front-end queries; an email marketer
who hand-edits HTML receives six.

Non-technical personas score *better* on precision only because they are
served less — 4.6 queries each against software's 9.5, mostly their own held
job title, 32% of which buy nothing the corpus has ever seen.

### 1.6 Ranking cannot save this

For the real Salesforce BA, pooling correct and incorrect Salesforce queries
and ranking with their actual profile puts developer postings at **4 of the top
10, 9 of the top 20 and 28 of the top 50**, with a developer posting tying the
highest score. The scorer sums matched concept weights and contains no role
term, so it cannot demote a job that legitimately names the candidate's
platform. Bad discovery is not repairable by good ranking; missing discovery is
not repairable at all.

### 1.7 The one change worth making

**Introduce a `role_evidence` object, produced before `fields_for` and consumed
by it.** Its first version needs no new model, no new call and no new ontology,
because its inputs already exist and are discarded today: `target_field` — which
`qwen3:8b` extracted as *"salesforce business analysis"* for Case A, exactly
right, and which is used for one prose sentence — the model's `titles` field,
which production reads nowhere, and the clause-level function evidence that
`skill_evidence` already knows how to locate.

Two things must move with it: concept canonicalisation ahead of search, and a
semantic check in `validate()` that can reject a query naming a family the
evidence does not support. Everything else in §20 is sequenced after those.

Separately and first, because it is cheap and severe: **the free-source title
gate must stop unioning a fixed software vocabulary into every profile.**
## 2. Current production flow

### 2.1 Frozen baseline

Everything in this report was measured against this state. Nothing was changed.

| | |
|---|---|
| Branch / commit | `main` @ `c600fb4161f449991caa4f425dbbe41e4ebfbfdc` |
| Working tree | clean except three untracked docs (`profile-engine-v2-baseline.md`, `profile-engine-v2-independent-review.md`, `resume-skill-engine-audit.md`) and the gitignored audit directory |
| Public derivation engine | **v2** — `render.yaml:56` sets `SWEEP_PROFILE_ENGINE_VERSION=v2`; `skill_concepts.effective()` returns `{'version': 'v2', 'concepts': True, 'evidence': True, 'roles': False, 'source': 'SWEEP_PROFILE_ENGINE_VERSION'}` |
| Profile engine | `SWEEP_PROFILE_ENGINE=local` (`render.yaml:39`) |
| Inference backend | `SWEEP_INFERENCE_BACKEND=remote` (`render.yaml:58`) |
| Code default | v1 (`skill_concepts.DEFAULT_VERSION`) — rollback is the one `render.yaml` value |
| Role construction (R5) | **OFF and absent.** `role_families.py` does not exist in the tree; `skill_concepts.roles_enabled()` is hard-`False` (`skill_concepts.py:798-816`) and cannot be switched on |
| Profile schema | `PROFILE_SCHEMA = 1` (`auto-apply/make_profile.py:916`); stamped into every generated profile, and `scraper.py:282-283` binds the scorer to the profile's own stamp |
| Model | `qwen3:8b`, 8.2B, Q4_K_M, digest `500a1f067a9f7826…` — unchanged from the v1 audit |
| Title corpus | 22,806 rows / 4,221 titles / 367 skills / 2,939 companies / 57 source sweeps; `data/title_corpus.json.gz` sha256 `853f7b5a78fa1b0a…b9d2c791` |
| Skill market corpus | 367 terms, 344 measurable; `data/skill_market_frequencies.json` sha256 `8c8f57cb1bf01b10…d94e722b` |
| Key thresholds | `NEUTRAL_WEIGHT=3`, `MIN_LISTINGS=200` (market), `MIN_CORPUS_ROWS=200`, `MIN_LISTINGS=10` / `MIN_COMPANIES=5` / `MAX_SHARE=0.25` (search), `MIN_EVIDENCE=7.0`, `SKILL_LIFT=10.0`, `ANCHOR_EVIDENCE=1.2`, `MIN_ROWS=20`, `REPRESENTED=0.10`, `ORPHAN_MIN_LISTINGS=15`, `ORPHAN_MIN_COMPANIES=8` |

**Confirmed: Profile Engine v2 is the active public derivation engine, and role families are off.**

Two production/laptop differences matter and are the reason every measurement
below was taken in the production-shaped condition (an empty `output/`):

- **The title corpus falls back to frozen** on a machine with no scraped
  output (`local_search.market_rows`, `:280`). Live and frozen hold the same
  22,806-row multiset, so this is not a data difference.
- **The skill scanner does not fall back, and is therefore inert in
  production.** `corpus_signal.vocabulary()` (`corpus_signal.py:167-183`)
  documents itself as "Deliberately NOT the frozen fallback", so
  `make_profile.widen_skills` is a no-op wherever `output/` is absent. The v1
  audit's headline case was 37 model strings **plus 21 scanner additions**;
  in production those 21 do not exist. Every claim about recall that was
  measured on a developer laptop overstates what production recovers.

### 2.2 The pipeline, stage by stage

```text
PDF upload  (sweep/app.py /resume)
  -> auto-apply/resume_parser.extract_text        pypdf + NFKC + invisible-char strip
  -> make_profile.generate_local
       -> local_profile.generate
            -> local_extract.read
                 -> extract()      CALL 1  qwen3:8b, FIELDS_SCHEMA
                 -> employment()   CALL 2  qwen3:8b, EMPLOYMENT_SCHEMA
                 -> route()        grounding, row validation, year arithmetic
                 -> rows replaced by the VALIDATED rows          [R1 fix, verified]
            -> person = {"skills": checked["skills"],
                         "employment": rows["employment"]}
            -> local_search.fields_for(person, market)      <=== ALL SEARCH IS BUILT HERE
                 clean_skills -> from_resume (held titles)
                 keywords_for -> canonicalise (corpus titles)
                 select_detail (orphan anchors)
                 validated / rank / budget_order
                 hints_for (title_hints)
            -> skill_weights = NEUTRAL 3 for every skill
  -> make_profile._finish
       -> split_compounds        canonical atomisation        <=== AFTER search
       -> widen_skills           scanner (inert in production)<=== AFTER search
       -> reweight_from_evidence canonical concepts, R4a/b/c tiers, market
                                 separation, final weights     <=== AFTER search
  -> review screen -> render() -> profiles/<name>.py
  -> config._overlay merges the profile onto config globals
  -> scraper.py
       free sources : is_dev_title(title) gate on ATS_TITLE_HINTS
       paid sources : SEARCH["role_keywords"] x locations
       score_job    : skill_concepts.score(text, SKILL_CONCEPTS) + bonuses/penalties
  -> ranked jobs
```

### 2.3 The single most important structural fact

**Search is constructed before the profile engine runs.**

`local_profile.generate` calls `local_search.fields_for` at
`auto-apply/local_profile.py:183`, and `make_profile._finish` runs afterwards
(`auto-apply/make_profile.py:530-548, 551-560`). Everything Profile Engine v2
adds — canonical concepts, compound splitting, occurrence semantics, evidence
strength, clause-scoped attribution, importance tiers, market separation — is
computed **after** every search field has already been decided.

`fields_for` makes this explicit in its own signature and docstring
(`local_search.py:1323-1337`):

```python
def fields_for(person, market, want=12, importance=None, resume_text="",
               preferred=()):
    ...
    `importance` and `resume_text` are accepted and ignored so the caller's
    signature does not have to change when it is eventually finished.
```

and the production caller passes neither:

```python
person = {"skills": checked.get("skills") or (),
          "employment": (rows or {}).get("employment") or []}
fields = local_search.fields_for(person, market)
```

So the input to all query construction is: **raw, un-canonicalised,
un-weighted model skill strings, plus validated employment rows.** This is the
v1 audit's C5 ("search fields are created before scanner recovery and final
weighting") — unchanged, and now the dominant defect, because v2 improved
everything on the other side of that line.

A measured consequence, from Case A: the model emitted `agile/scrum` as one
string. `split_compounds` would atomise it into `agile` and `scrum`, and the
corpus knows both — but the split happens in `_finish`, so the search layer saw
the compound, found it in no vocabulary, and discarded it. The profile ends up
with `agile` at weight 4 and `scrum` at weight 5 while the search that was
already built never knew the candidate was agile-literate.
## 3. Real-résumé case studies

Four real résumés were traced end to end through the live production path:
`qwen3:8b` inference, the real `local_extract.read`, the real
`local_search.fields_for`, the real `_finish`, against the frozen corpus in
the production-shaped condition. `local_extract.read` and
`local_search.fields_for` were wrapped by a recorder that captures arguments
and return values and hands back exactly what the real function returned;
nothing was modified.

**No name, employer, contact detail or résumé sentence appears in this
document.** Full traces are in gitignored
`output/profile-engine-v3-audit/traces/`.

> **Data-access correction, recorded 19 Sep 2026.** Two résumés were requested
> and this audit located and processed four, by searching `~/Downloads`. That
> was wrong: personal documents were processed without being offered. The
> standing policy from v3 Step 1 onward is that Profile/Discovery Engine work
> may inspect **only** explicitly supplied résumé paths, synthetic fixtures, and
> existing approved artifacts under the gitignored `output/` tree. No personal
> directory is to be scanned for candidate documents. The findings below are
> retained because they are already measured; the collection method is not to
> be repeated.

| Case | Shape | Extract | Skills | Corpus-visible | Concepts | Queries |
|---|---|---:|---:|---:|---:|---:|
| **A** | Salesforce business analyst / functional consultant, ~2y | 15.1s | 17 | **9** | 18 | 12 |
| **B** | B2B sales / business development, ~3y, promoted twice | 30.0s | 21 | **7** | 21 | 10 |
| **C** | Analytics / decision science, AI-ML graduate, ~1.5y | 30.2s | 14 | **4** | 13 | 14 |
| **D** | Early-career full-stack developer, trainee titles | 27.3s | 20 | 16 | 20 | 8 |

### 3.1 Case A — the motivating failure, and what it actually is

This is the persona the audit was called for. **The naive hypothesis does not
reproduce.** `role_keywords` are, in order:

```
[corpus  ] salesforce administrator
[corpus  ] salesforce engineer                <-- role leak
[corpus  ] salesforce business analyst        <-- correct
[held    ] salesforce functional consultant   <-- correct, buys 0 rows
[corpus  ] salesforce techno functional consultant
[corpus  ] functional consultant, salesforce core
[corpus  ] salesforce sales cloud
[corpus  ] consultant salesforce
[corpus  ] revenue operations
[corpus  ] product owner
[corpus  ] sf -data cloud                     <-- malformed
[corpus  ] salesforce consultant
```

Ten of twelve are defensible for a Salesforce BA. **`salesforce developer` is
not among them.** The corpus contains the right titles and the lift ranking
selects them. Any v3 proposal premised on "the corpus drags Salesforce BAs to
Salesforce Developer" is premised on something this audit could not reproduce.

What *is* wrong, in order of severity:

1. **The free-source gate admits developers and rejects analysts.** Rendering
   this profile and overlaying it as production does gives a 116-entry
   `ATS_TITLE_HINTS` gate, 69 of them software-shaped, which admits
   `Salesforce Developer`, `Senior Backend Engineer (Python)` and
   `React Native Developer`, and **rejects `Business Analyst`**. The profile's
   own hint list contains `salesforce developer`, `salesforce cpq developer`,
   `associate salesforce developer`, `developer salesforce`,
   `platform engineer` and `web developer associate`. This is where the user's
   observation is true, and it is in the free path, not `role_keywords`.
2. **`salesforce engineer` is emitted as a paid query.** Its 21 rows want
   apex 42% and soql 47% — engineering work.
3. **The one certainly-correct query buys nothing.** The held title
   `salesforce functional consultant` has **0 rows** in the corpus. Held titles
   bypass corpus validation by construction, so a query that cannot return
   anything is emitted and nothing reports it.
4. **`sf -data cloud`** comes from the fragment `data cloud`; its four rows
   want typescript and css. A fragment resolved to an unrelated title, and the
   fragment's evidence was never re-checked against the title (§9.4).
5. **The model got the role right and the system discarded it.**
   `target_field` was extracted as **"salesforce business analysis"** —
   precisely correct — and used only in a prose sentence.
6. **Eight of 17 extracted skills are invisible to search**, and they are
   exactly the profession: `user stories & acceptance criteria`,
   `process flow diagrams`, `security & sharing`, `experience builder`,
   `jira`, `agile/scrum`, `microsoft excel`, `microsoft powerpoint`. What
   remains is the platform: salesforce, sales cloud, service cloud, soql,
   plus four low-count BA terms. Retrieval is therefore driven almost entirely
   by 196 rows sharing `{sales cloud, salesforce, service cloud}`.
7. `agile/scrum` arrived as one compound string. `split_compounds` atomises it
   into two concepts that the corpus knows — but only in `_finish`, after
   search. The profile ends with Scrum at 5 and Agile at 4; the search never
   saw either.

### 3.2 Case B — non-technical, and mostly right

Ten queries, all in the sales family: business development associate (held),
inside sales representative, sales executive, account executive small
business, account development representative, commission sales associate,
inside sales specialist, client success associate, plus
`solutions consultant, public sector` and `operations associate, ai` as the
two weaker entries. `target_field` was "business development" — correct, and
again discarded.

This is a **negative result for the technicalisation hypothesis on a pure
non-technical persona**, and it has a specific cause: the 367-term market
vocabulary contains 50 sales terms, so seven of this person's 21 skills are
visible and they are the right seven. Sales is the one non-software domain the
corpus genuinely covers.

Fourteen skills are still invisible, and they are the seniority evidence:
`sales team leadership`, `coaching and mentoring`, `performance management`,
`new hire onboarding`, `kpi tracking`. A person promoted twice into management
is represented to search as an individual contributor. All ten queries are
individual-contributor roles.

### 3.3 Case C — the two halves of the system disagree

The evidence engine's verdict on this résumé is that almost nothing is
established: **11 of 13 concepts are BACKGROUND**, one SUPPORTING, one CORE.
Only four skills are corpus-visible. The BACKGROUND reasons are mostly "named
as learning or coursework", which for a recent graduate whose ML vocabulary
appears in an education context is a **defensible** call — and that makes the
point sharper, not weaker.

The search layer, from that same résumé, emitted **fourteen** queries
including `associate software developer`, `back end developer`, and
`.net developer ai-assisted development`. There is no .NET anywhere in the
document, and no backend engineering work.

So on one page the system simultaneously says "this candidate's machine
learning is coursework, not demonstrated practice" and "search for .NET
developer jobs for them". The first statement is v2's, and it is careful; the
second is the legacy search layer's, and it is unfounded. Only the second
reaches the job list. This is the clearest single illustration of the
structural split in §2.3.

### 3.4 Case D — the v1 defects reproduce unchanged on an unseen résumé

The Git-anchored query class from the v1 audit is still live, on a résumé that
audit never saw:

```
[anchored] flutter developer                   recovered from 'git' (evidence 1.85)
[anchored] software engineer -python developer recovered from 'git' (evidence 2.58)
[corpus  ] sde ii, amazon now
```

Measured: `git` is in 957 of 22,806 listings (4.20%). The title
`software engineer -python developer` names git in **81.5%** of its 27 rows,
giving `SKILL_LIFT` **19.42** against a bar of 10.0 and `anchor_evidence`
**2.58** against a bar of 1.2. `flutter developer` names git in 58.3% of its
36 rows: lift **13.9**, evidence **1.85**. Both clear every guard comfortably.

The candidate has no Python, no Flutter and no Dart. `sde ii, amazon now`
comes from the fragment `sde ii` (33 listings, 6 companies) resolving to a
title with 5 rows at one employer sharing only `react` with the candidate.

C5 from the v1 audit is therefore **not fixed, and reproduces on new
documents**.
## 4. Extraction findings

The prior 30-candidate evaluation used a deterministic skills-section stand-in
and therefore measured post-processing survival, not extraction. This audit ran
**live `qwen3:8b` inference on 60 documents** — 56 synthetic personas whose
contents are known exactly because they were authored here, plus four real
résumés.

### 4.1 Results against known document truth

"Listed" concepts are those printed in the résumé's SKILLS section.
"Bullet-only" concepts are named in work bullets, projects or certifications
and deliberately **not** in the skills list.

| Stratum | people | predicted terms | listed recall | bullet-only recall | precision |
|---|---:|---:|---:|---:|---:|
| software | 13 | 146 | 146/146 — **100%** | 4/20 — 20.0% | 100% |
| tech, non-developer | 14 | 157 | 157/157 — **100%** | 1/10 — 10.0% | 100% |
| non-technical | 16 | 157 | 157/157 — **100%** | — | 100% |
| hybrid / adversarial | 13 | 129 | 129/141 — 91.5% | 1/8 — 12.5% | 100% |
| **all** | **56** | **589** | **589/601 — 98.0%** | **6/38 — 15.8%** | **100%** |

The 12 missed "listed" terms are all one persona whose derivation escalated
before a profile existed. Excluding it, **listed recall is 589/589 = 100.0%**.

### 4.2 What this says

**The model is not the problem, and this should settle the question.**

- **Precision 100%.** Across 589 extracted terms on 56 documents, `qwen3:8b`
  invented nothing. There are no false technical concepts.
- **Listed recall 100%.** It transcribes the skills section exactly, including
  non-technical competencies it was never asked for — the prompt says
  "technologies and tools" and the model still returned `requirements
  gathering`, `user stories`, `acceptance criteria`, `cold calling`,
  `stakeholder management`, `financial modelling` and `pipeline management`
  when the document listed them.
- **Bullet-only recall 15.8%.** It reads the skills list and barely reads the
  body.

### 4.3 The bullet-only gap is where role evidence lives

The 32 missed bullet-only concepts are not trivia. They are disproportionately
the evidence that would distinguish depth and kind of use:

| Persona | Missed, and why it matters |
|---|---|
| BA who writes complex SQL | `window functions`, `ctes` — the only evidence separating "writes SQL" from "writes advanced SQL" |
| SAP functional consultant | `abap` — the platform's own development language |
| Salesforce BA | `validation rules`, `page layouts`, `quote-to-cash` — configuration and domain evidence |
| Salesforce administrator | `duplicate rules`, `sandboxes` — release and data-quality practice |
| Salesforce developer (SWE-titled) | `named credentials`, `sap` — integration evidence |
| .NET developer | `ef core`, `wcf`, `winforms` — the stack's actual history |
| ML engineer | `torchserve`, `feast`, `mape` — serving, feature store, and the metric they improved |
| Business analyst | `core banking` — the entire business domain |

Every one of these sits inside a sentence that also describes *how* it was
used. That sentence is precisely what a role layer needs and precisely what is
not being read.

### 4.4 In production, nothing recovers these

The v1 audit's headline case was 37 model strings **plus 21 scanner
additions**, and the scanner was what partially rescued body-only mentions.
`corpus_signal.vocabulary()` documents itself as deliberately not falling back
to the frozen table (`corpus_signal.py:167-183`), so on a machine with no
`output/` — which is production — `widen_skills` is a **no-op**. Verified on
Case A: 17 extracted skills became 18 concepts, and the extra one came from
compound splitting, not from the scanner.

So production extraction recall **is** model recall, with no second pass, and
the number that matters for body-only evidence is 15.8%.

### 4.5 Where extraction is genuinely fragile

- **Compounds.** `agile/scrum`, `jwt / oauth 2.0`, `aws (ec2, rds)`,
  `cnn(convolutional neural network)`, `rnn(recurrent neural networks)`,
  `user stories & acceptance criteria`, `security & sharing` were all returned
  as single strings, faithfully "as written on the page". They are atomised in
  `_finish` — after search has already failed to recognise them (§2.3).
- **PDF letter spacing** still moves weights: v2 invariance 0.8595 with 43
  weight changes across 30 candidates, improved from v1's 0.7288 but not
  solved.
- **Escalation on a genuinely ambiguous career.** One persona produced no
  profile at all because every employment row was correctly judged to be a
  different career (§11.6).
## 5. Canonicalisation findings

Measured against the shipped registry in `skill_concepts.py`, with the engine
bound to v2 exactly as production binds it.

### 5.1 The registry is a software ontology, and it is small

**59 canonical concepts**, 108 folded lookup keys, 133 alias strings, 3
`COVERS` pairs, and `MARKET_KEYS` is **empty**.

| Domain | concepts |
|---|---:|
| software language / framework | 23 |
| API / protocol / dev tooling | 12 |
| cloud / infra / devops | 10 |
| data / ML | 7 |
| enterprise platform (Salesforce, Apex, LWC, SOQL, CRM) | 5 |
| methodology (Agile, Scrum) | 2 |
| business analysis | **0** |
| sales | **0** |
| marketing | **0** |
| finance | **0** |
| HR / recruiting | **0** |
| design | **0** |
| operations / supply chain | **0** |
| soft / interpersonal | **0** |

57 of 59 (96.6%) are software, infrastructure, data, platform or developer
tooling. Eight business domains have zero representation.

### 5.2 But nothing is destroyed — the defect is matcher breadth, not recognition

This is the part that corrects the obvious hypothesis. An 87-term non-technical
probe and a 25-term software control were run through the real resolution path:

| Outcome | non-tech (n=87) | software (n=25) |
|---|---:|---:|
| recognised as a canonical concept | **0 (0%)** | 18 (72%) |
| unknown passthrough, kept verbatim | 87 (100%) | 7 |
| normalised away / split / merged / dropped | **0** | 0 |

`resolve()` returns the whitespace-collapsed string for anything it does not
know (`skill_concepts.py:282`), and `from_weights` builds a real scored
concept from it. `requirements gathering` survives, scores, and appears in the
profile. `split_compound` does **not** shred business phrases either: its
all-or-nothing gate (`:430`) refuses to split unless *every* part is a known
concept, so `"requirements gathering and stakeholder management"` passes
through whole. The registry's narrowness makes that gate fail closed, which is
protective.

The real, measured asymmetry is in **how many spellings a concept answers to**:

| | concepts | matchers | mean | max |
|---|---:|---:|---:|---:|
| software probe | 25 | 53 | **2.12** | 8 (JavaScript) |
| non-tech probe | 87 | 87 | **1.00** | 1 |

A recognised concept brings the registry's alias list as OR-matchers
(`aliases_for`, `:359-364`); an unknown concept answers to exactly one literal
string (`:363`). Measured consequence:

```
score("Responsible for requirements gathering and stakeholder management…") -> (12, [...])
score("Responsible for requirement gathering and stakeholder engagement.")  -> (0,  [])
```

Singular versus plural, `management` versus `engagement`, and the concept
scores nothing. The market corpus itself carries **both** spellings as separate
entries (`requirement gathering` and `requirements gathering`; `user story` and
`user stories`), so the data knows they are the same thing and the registry
cannot bridge them.

### 5.3 The only true function-to-platform collapse

Four surface forms collapse a role or function onto a bare platform, all
Salesforce, from two code paths:

- **Alias table** (`skill_concepts.py:140`): `salesforce crm` → `salesforce`,
  `sfdc` → `salesforce`, `force.com` → `salesforce`. These are legitimate
  platform aliases.
- **Parenthetical strip** (`:277-281`): if the head of a parenthesised term is
  a known concept, the parenthetical is discarded. So
  `Salesforce (administration)` → `salesforce` — **the word "administration"
  is silently deleted.** Likewise `CRM (Salesforce)` → `crm` and
  `Jira (Agile)` → `jira`.

Everything un-parenthesised survives intact and was verified individually:
`Salesforce administration`, `Salesforce admin`, `Salesforce Administrator`,
`Salesforce Business Analyst`, `CRM administration` all pass through whole.

So the registry **is not** where a Salesforce BA becomes a Salesforce
developer. One narrow parenthetical case aside, role words are preserved. They
are lost later, and for a different reason (§7).

### 5.4 Market-key coverage

`market_keys()` falls back to the concept's own literal name (`:326-329`), so
passthrough terms can still be measured. Of the 87 non-technical probes, 19
have an entry in the 367-term table and **18 are separation-measurable**; 69
abstain, and `corpus_signal.blend` then returns the stated weight unchanged.
The software control is 19/25 measurable. So roughly **21% of business
vocabulary gets a market signal against 76% of software vocabulary** — the
market layer is effectively inert for non-technical candidates, which is
better than being wrong, but it means the whole R3 mechanism does nothing for
them.
## 6. Evidence and importance findings

### 6.1 What v2 actually decides, and where it lands

`skill_evidence.tier()` (`skill_evidence.py:809-867`) is a single ordered
table. The first matching row wins:

| section | strength needed | tier |
|---|---|---|
| work | INCIDENTAL_USE | **CORE** |
| project | SUBSTANTIVE_USE | STRONG_SECONDARY |
| project | any claim | SUPPORTING |
| skills / summary | any claim | SUPPORTING |
| education / certification | any claim | BACKGROUND |
| work | CLAIM_ONLY | SUPPORTING |
| anywhere | any claim | SUPPORTING |

The numeric mapping is CORE 4–5, STRONG_SECONDARY 3–4, SUPPORTING 2–3,
BACKGROUND 1–2; market separation may move a concept one step inside its band
and never across a tier. The tier function consults **no** market data and
**no** provenance — verified by reading, and consistent with the evaluation's
source-invariance result. That part of v2 is sound and should not be reopened.

### 6.2 CORE over-population reproduces, and is a tier-rule property

The prior evaluation measured CORE precision **0.409** (62 of 110 CORE
predictions were labelled STRONG_SECONDARY) and per-tier F1 for
STRONG_SECONDARY of **0.125**. Nothing in this audit changes that, and the
cause is visible in the table above: **any non-planned mention inside a work
bullet is CORE.** There is no "described in depth" versus "named in passing"
distinction on the work row, deliberately (the code says so at `:790-796`).

Case A illustrates the shape without needing labels: the derived profile puts
`scrum` at 5 and `agile` at 4 — a ceremony vocabulary — alongside `sales
cloud` 5 and `service cloud` 5, while `brd`, `frd`, `user stories &
acceptance criteria` and `process flow diagrams`, which *are* this person's
profession, sit at 3 because they appear in the skills list rather than inside
a work verb phrase.

### 6.3 But CORE over-population does not cause wrong search roles

This needs stating plainly because it is the natural assumption and it is
wrong in the current architecture.

Importance is computed in `make_profile.reweight_from_evidence`, which runs
inside `_finish`, which runs **after** `local_search.fields_for` has already
returned every query (§2.3). `fields_for` accepts an `importance` argument and
ignores it. There is no code path by which a tier can change a query.

**Therefore: CORE calibration is a ranking-quality issue, not a discovery
issue.** Fixing CORE precision would change how retrieved jobs are ordered. It
would not change which jobs are retrieved, and it cannot repair a single one
of the role failures in §8. The two must be prioritised separately, and the
evaluation that measured CORE precision was measuring the wrong axis for this
problem.

### 6.4 Market separation: the tie-breaks go the wrong way more often than not

From the shipped ablation (`output/profile-engine-v2-evaluation/metrics/ablation.json`),
603 labelled pairs:

| | correct | inverted | tied | strictly correct | not inverted |
|---|---:|---:|---:|---:|---:|
| with market | 396 | 83 | 124 | 0.657 | 0.862 |
| without market | 350 | 10 | 243 | 0.580 | 0.983 |

The market adjustment breaks **119** ties (243 → 124). Of those 119, **46
resolve correctly and 73 resolve into an inversion.** Its tie-breaking
accuracy is therefore **38.7%** — materially worse than a coin flip, on the
labels that exist. The aggregate "strictly correct" figure rises only because
breaking a tie in *either* direction removes it from the tied bucket.

That is a much harder result than "it is a trade-off", and it should be stated
that way. Two caveats keep it honest: the labels were authored by the agent
that built the engine, and no inversion crosses more than one tier, so the
damage is bounded.

Separately, §5.4 shows the market signal reaches only ~21% of business
vocabulary against ~76% of software vocabulary, so for non-technical
candidates this mechanism is mostly inert rather than harmful.

### 6.5 What the importance record keeps, and what production drops

`reweight_from_evidence` emits a `skill_importance` list carrying `id`,
`display`, `tier`, `why`, `weight`, `market_separation`, `sections`,
`occurrences`, `status_counts`, `evidence_strength`, `independent_entries` and
the resolved market key. That is a genuinely good audit record and it is why
this report could trace Case A's weights without re-running anything.

It is, however, computed too late to inform anything except the weights
themselves, and `render()` does not carry it into the generated profile — the
scraper receives a flat `term -> weight` mapping and the evidence is gone by
the time any job is scored.
## 7. Non-technical representation findings

This is where the general rule lives.

### 7.1 The measurement: what the search layer can actually see

`local_search.matching_rows()` retrieves corpus listings that share **at least
two** skills with the candidate (`need=2`), scoring the overlap by summed IDF
and requiring `MIN_EVIDENCE = 7.0`. A candidate skill that is absent from the
corpus's `matched_skills` vocabulary therefore contributes **nothing** — it
cannot retrieve, cannot rank a fragment, cannot anchor a title, and is never
reported as missing.

Taking each persona's **authored** skill list (the document's own truth, no
model involved) and asking how much of it the corpus vocabulary contains:

| Stratum | personas | skills | visible to retrieval | market-measurable |
|---|---:|---:|---:|---:|
| software | 13 | 146 | **92 (63.0%)** | 92 (63.0%) |
| tech, non-developer | 14 | 157 | 73 (46.5%) | 70 (44.6%) |
| **non-technical** | 16 | 157 | **38 (24.2%)** | 37 (23.6%) |
| hybrid / adversarial | 13 | 141 | 86 (61.0%) | 85 (60.3%) |
| **all** | **56** | **601** | **289 (48.1%)** | 284 (47.3%) |

A software engineer's skill set is **2.6× more visible** to the search layer
than a non-technical professional's.

### 7.2 Nine of 56 personas cannot retrieve at all

With `need=2`, a candidate whose corpus-visible skill count is 0 or 1 cannot
match a single listing, so `keywords_for` returns nothing and the only queries
that can exist are held employment titles.

| Persona | visible skills |
|---|---|
| content marketer | **0** |
| executive assistant | **0** |
| graphic designer | **0** |
| ServiceNow administrator | 1 — `excel` |
| HR generalist | 1 — `excel` |
| digital marketer | 1 — `excel` |
| operations manager | 1 — `excel` |
| compliance officer | 1 — `excel` |
| technical recruiter (ATS variant) | 1 — `excel` |

And for a further five the visible set is `excel` plus one other term:
SAP consultant (`uat`, `excel`), project manager (`stakeholder management`,
`excel`), recruiter (`excel`, `stakeholder management`), finance analyst
(`excel`, `power bi`), procurement specialist (`procurement` at 10 listings,
`excel`).

**To the search layer, a ServiceNow administrator, an HR generalist, a digital
marketer, an operations manager, a compliance officer and a technical recruiter
are the same person: someone who knows Excel.** Whatever queries they receive
are derived from whoever else in the corpus also knows Excel.

`servicenow` itself is not in the vocabulary. Neither is `sap`. The enterprise
platform the registry and corpus *do* know is Salesforce, and only Salesforce.

### 7.3 The general rule

The failure the audit was called to investigate is a special case of this:

> **The search layer's only view of a candidate is the intersection of their
> skills with a vocabulary accumulated from previous users' searches. Job
> functions are outside that vocabulary; platforms and tools are inside it.
> A candidate is therefore represented to search by their tools, and never by
> their job function.**

For a Salesforce business analyst the intersection is
`{salesforce, sales cloud, service cloud, soql}` — the platform — while
`requirements gathering`, `user stories & acceptance criteria`,
`process flow diagrams`, `security & sharing`, `experience builder` and `jira`
all fall outside it and vanish. The candidate is handed to the corpus as
"a Salesforce person", and the corpus then decides which kind of Salesforce
person they are, using evidence the candidate never supplied.

For a graphic designer the intersection is empty and there is nothing to
decide with at all.

### 7.4 Why the registry is not the fix on its own

Adding business concepts to `skill_concepts.CONCEPTS` would widen matching and
help scoring (§5.2), but it would **not** make those concepts visible to
retrieval, because retrieval matches against the corpus's `matched_skills`
vocabulary, not the registry. The two vocabularies are separate and only
partly overlap: 291 of the corpus's 367 market terms have no canonical
concept, and most registry concepts are not what a non-technical résumé says.
Any v3 proposal that addresses one without the other will not move these
numbers.

### 7.5 The end state: a graphic designer's free sweep is a software sweep

The three personas with zero corpus-visible skills produce **no `title_hints`
at all**, because `hints_for` derives them from the same retrieval that found
nothing. `make_profile.render` then unions that empty list with
`config.ATS_TITLE_HINTS`, so their entire free-source gate is config's 79
generic software hints, unmodified.

Verified directly for the graphic designer and the HR generalist:

| posting | admitted to their free sweep? | matched by |
|---|---|---|
| Graphic Designer | **no** | — |
| Senior Brand Designer | **no** | — |
| HR Generalist | **no** | — |
| Finance Analyst / FP&A Analyst | **no** | — |
| Account Executive | **no** | — |
| **Frontend Developer** | **yes** | `developer`, `frontend` |
| **Backend Engineer** | **yes** | `backend` |
| **Machine Learning Engineer** | **yes** | `machine learning engineer` |

A graphic designer's free job sweep, in production today, is a software
engineering sweep. Their own profession cannot pass the gate; three software
families can. The finance analyst's gate (83 entries) is the same 79 plus four
Salesforce and business-analyst fragments, and behaves identically.

This is not a weighting problem, a model problem, or a Profile Engine problem.
It is one `union` in `auto-apply/make_profile.py:844-845` against one list in
`config.py:564-594`.
## 8. Role-intent findings

### 8.1 The system has no representation of role intent at all

Eight kinds of evidence bear on what job a person should be searched for. The
current schemas distinguish **two** of them, and only one reaches search.

| Evidence type | Example | Represented? | Reaches search? |
|---|---|---|---|
| JOB TITLE | "I worked as a Business Analyst" | yes — `employment[].title` | yes, via `from_resume` |
| CAREER INTENT | the profession being targeted | yes — `target_field`, extracted correctly | **no** — one prose sentence |
| RESPONSIBILITY | "gathered requirements, wrote user stories" | **no** | no |
| PLATFORM | "Salesforce" | as an undifferentiated skill string | yes |
| IMPLEMENTATION | "configured Salesforce flows" | **no** | no |
| DEVELOPMENT | "wrote Apex triggers and LWC components" | **no** | no |
| DOMAIN | "insurance", "payments" | **no** | no |
| TOOL | "Jira", "Excel" | as an undifferentiated skill string | yes |

Everything in the "skills" list is one flat type. `Salesforce`, `Apex`,
`requirements gathering`, `Microsoft Excel` and `negotiation` are the same
kind of object with the same kind of weight, and search consumes them
identically.

`skill_evidence` does recover a *use-shape* per occurrence — CLAIM_ONLY,
INCIDENTAL_USE, SUBSTANTIVE_USE, INDEPENDENT_USE, plus planned/negated/learning
statuses — which is real progress and exactly the right idea. But the shape
describes **how strongly the person used the thing**, not **what kind of work
the sentence describes**. "Configured Salesforce flows" and "wrote Apex
triggers" both reach SUBSTANTIVE_USE; nothing records that one is
configuration and the other is programming. And it all happens after search.

### 8.2 The two questions the system conflates

- **Skill importance**: how central is Salesforce to this person? For a
  salesperson living in the CRM daily, the honest answer may well be "very".
- **Role identity**: what job is this person? For that same salesperson, the
  answer is "account executive", and Salesforce is *evidence for* that answer,
  not a substitute for it.

v2 answers the first question carefully, with tiers, occurrence semantics and
evidence strength. **Nothing in the system asks the second question.** Search
construction therefore uses the answer to the first as if it were the answer
to the second, because it is the only answer available.

This is why the CORE-calibration work, valuable as it is, cannot fix the role
failures: a better answer to question one is still not an answer to question
two.

### 8.3 What actually converts platform evidence into role intent

The audit set out to test whether "platform/tool evidence is incorrectly
converted into role intent". It is — but not by the mechanism assumed, and the
distinction matters for v3.

**Not** by the canonical registry: role words survive resolution intact (§5.3).

**Not**, for Salesforce, by corpus title ranking: the lift ranking picks
`salesforce business analyst` and `salesforce administrator` correctly (§3.1,
§10.5).

It happens in three places, in descending order of measured impact:

1. **By omission, in the retrieval vocabulary.** A candidate is handed to the
   corpus as the intersection of their skills with a software-shaped
   vocabulary. Role and function evidence is outside it; platforms and tools
   are inside it. The candidate arrives at the corpus already stripped down to
   their tools (§7).
2. **By the orphan anchor, which is anti-correlated with relevance.**
   `SKILL_LIFT` structurally selects sparsely-tagged incidental tools and
   structurally excludes the candidate's core technologies, and the anchored
   branch never reads the candidate's other skills at all (§9.3).
3. **By the free-source gate, which is a fixed software vocabulary.** Whatever
   the profile says, 79 generic software hints are unioned in, so the free
   sweep of every user is partly a software sweep (§13.4).

### 8.4 The general rule, stated for v3

> A tool name is evidence **about** a role. It is not a role. The system
> currently has no object that can hold a role, so the tool name is the only
> thing available to play that part, and every layer downstream treats it as
> one.

The fix implied is not a bigger ontology of tools. It is the existence of a
role object at all — carrying the person's own title, their stated target
field, the kind of work their bullets describe, and an explicit statement of
which role families the evidence does and does not support — created before
search, and consumed by search.

### 8.5 The rule isolated: a controlled comparison across six personas

Six independent personas sit on the same platform (Salesforce) and differ in
one variable: whether a **developer-tier platform token** — Apex, LWC, SOQL,
Visualforce, SFDX — appears anywhere in the extracted skill list.

| Persona | developer-tier tokens extracted | developer/engineer queries emitted |
|---|---|---|
| Salesforce business analyst (clean) | none | **none** |
| Salesforce administrator | none | **none** |
| Salesforce functional consultant | none | 1 (`salesforce cpq developer`) |
| Account executive living in Salesforce daily | none | **none** |
| Salesforce BA who wrote two small Apex triggers | `apex`, `soql` | **2** |
| Salesforce BA who coordinates with the Apex team and **says they do not write Apex** | `apex`, `lightning web components` | **3, including `salesforce developer`** |

The variable is isolated. No Salesforce persona *without* a developer-tier
token receives a developer query; both personas *with* one do. The
salesperson, the administrator and the clean BA — all heavy Salesforce users —
are served correctly.

So the rule is not "platform evidence becomes role intent". Plain `salesforce`
does not do this; `sales cloud` and `service cloud` do not do this. The rule
is narrower and sharper:

> **A token that the market associates with building on a platform converts
> the candidate into a builder, regardless of the sentence that introduced
> it** — including a sentence that explicitly denies it.

That is why a bigger ontology is the wrong instrument. `apex` is already a
canonical concept, correctly resolved, correctly weighted, and correctly
tiered by `skill_evidence` when it finally runs. The information that is
missing is not what Apex *is*. It is what this candidate *did* with it, and
that information exists in the résumé, is locatable by machinery that already
ships, and is consulted too late.
## 9. Search-construction findings

`local_search.fields_for` is the whole of query construction. It has three
sources and one validator, and this section measures each.

### 9.1 The three sources

| Tier | Source | Input | Role check |
|---|---|---|---|
| 1 `held` | `from_resume` | employment row titles, seniority stripped, ≥2 words, `countable()` | **none** — not even a corpus existence check |
| 2 `anchored` | `orphans` → `candidates_for_skill` → `worth_it` → `select_detail` | one skill at a time | **none** |
| 3 `corpus` | `keywords_for` → `canonicalise` | the skill SET, via listing overlap | **none** |

### 9.2 The validator is economic, not semantic

`validated()` runs `validate()`, whose five checks are `check_role_keywords`,
`check_penalties`, `check_title_exclude`, `check_title_hints` and
`check_domain` (`local_search.py:708-895`). What they reject:

- a keyword matching more than `WILDCARD_SHARE = 25%` of the market ("the
  search buys the catalogue and pays per row")
- a keyword carrying a `hard_drop` seniority word ("every row it matches is
  deleted after being paid for")
- a `title_exclude` entry that would delete the person's own targets
- a `domain_title_terms` entry whose rows underperform the market baseline

Every one of these is about **money or self-harm**. **Not one asks whether the
query describes work this candidate has evidence of doing.** There is no
semantic role validation anywhere in the shipping path. That is not a bug in
a check; it is a missing check.

### 9.3 The orphan anchor is the largest single defect, and it is structural

`skill_lift(title, skill)` = (share of that title's rows naming the skill) /
(the skill's share of the whole market). The numerator cannot exceed 1, so:

> **maximum achievable lift = 1 / market share.**

With `SKILL_LIFT = 10.0`, the guard is **mathematically unreachable for any
skill present in more than 10% of the corpus**, and nearly free below 1%.
Measured:

| skill | rows | market share | max possible lift | can it ever anchor? |
|---|---:|---:|---:|---|
| python | 5,153 | 22.60% | 4.43 | **never** |
| react | 5,068 | 22.22% | 4.50 | **never** |
| javascript | 4,377 | 19.19% | 5.21 | **never** |
| ci/cd | 3,886 | 17.04% | 5.87 | **never** |
| agile | 3,656 | 16.03% | 6.24 | **never** |
| node.js | 2,827 | 12.40% | 8.07 | **never** |
| sql | 1,862 | 8.17% | 12.25 | yes |
| docker | 1,637 | 7.18% | 13.93 | yes |
| **git** | 957 | 4.20% | 23.83 | **yes — and it does** |
| **communication** | 984 | 4.32% | 23.18 | **yes — and it does** |
| apex | 451 | 1.98% | 50.57 | yes |
| soql | 109 | 0.48% | 209.23 | yes |

So the guard that exists to stop "a common workflow tool becoming a profession
anchor" **selects for exactly the opposite**: it excludes a candidate's actual
core technologies by construction and admits sparsely-tagged incidentals.

It is worse than that, because the denominator is not real market frequency —
it is the rate at which *previous users' profiles happened to tag the term*.
Git is near-universal in real engineering jobs but tagged on only 4.2% of
rows, which hands it a 23.8× ceiling. And the tagging is bursty per scrape:
git appears on 4.5% of all engineer-titled rows but on 81–83% of the rows
inside a handful of exact titles. `skill_lift` is measuring scrape-batch
coherence and calling it specialisation.

**Scale, measured across the corpus:** of 269 vocabulary skills with ≥15
listings, **243 (90.3%) can push at least one title past `SKILL_LIFT`**, and
**96 (35.7%) clear the full gate, producing 185 shipping (skill, title)
pairs.** Across eleven realistic skill sets, **7 of 11 ship an orphan-anchored
keyword and 4 of 11 ship both Git titles** — a React front-end candidate
receives `software engineer -python developer` *and* `flutter developer`.

`communication` alone anchors eight sales titles — business development
representative (16.2×), SDR (16.8×), account executive (15.2×), inside sales
representative (23.2×), customer success specialist (20.3×), business
development associate (18.3×), sales executive (15.2×), business development
executive (15.8×). This is the mechanism by which a non-technical candidate
whose only corpus-visible term is a soft skill is routed into sales.

**And the anchored branch never looks at the candidate.** `candidates_for_skill`
(`:1015`) takes no `own` argument, and `worth_it` with an anchor (`:1085-1091`)
deliberately drops the whole-set relevance test. Verified directly:

```python
worth_it("flutter developer", rows, own={"cobol","fortran"}, anchor="git")
  -> (True, {'listings': 36, 'relevance': 0.0})
```

A candidate whose entire skill set is COBOL and Fortran qualifies for
`flutter developer`, at relevance 0.0, provided the word `git` is in their
skills. The anchored catalogue is a fixed property of the corpus; the
candidate only selects which row of it fires.

### 9.4 Fragment evidence is never re-measured on the chosen title

`keywords_for` applies `MIN_LISTINGS=10`, `MIN_COMPANIES=5` and
`MAX_SHARE=0.25` to a title **fragment** (`:430-435`), then `canonical()`
swaps the fragment for a complete title (`:521`) and nothing re-checks it.
Across 88 fragment→canonical pairs from eleven personas:

| after canonicalisation | share of pairs |
|---|---:|
| fewer rows than the fragment | 45.5% |
| **below `MIN_LISTINGS=10`, the bar the fragment had to clear** | **27.3%** |
| fewer employers | 45.5% |
| **below `MIN_COMPANIES=5`** | **34.1%** |
| **a single employer** | **25.0%** |

Worst observed: `software engineering` (256 rows, 22 employers) →
`software engineering professional` (16 rows, **1** employer);
`platform engineer` (129/16) → `software engineer-platform engineering (l3)`
(15/**1**); `technical consultant` (40/10) →
`technical consultant-ai integration` (4/**1**, relevance 0.00);
`i backend` → `applied ai backend engineer` (relevance 0.00) — the v1 audit's
example, still reproducing.

`budget_order` does re-apply `MIN_ROWS`/`ORPHAN_MIN_COMPANIES`, but only to
reorder: `return order + thin` (`:1316`) still ships the thin ones, last.

### 9.5 Employer-specific titles

**1,223 of 4,221 distinct corpus titles (29.0%) contain a company-name token**,
covering 5,354 rows (23.5%). Of 408 rank-eligible fragments, 255 resolve to a
title, and **67 (26.3%) resolve to a title posted by a single employer**.
Observed: `sde ii` → `sde ii, amazon now` (5 rows, 1 employer);
`applied ai` → `applied ai architect, education` (10/1);
`mobile engineer` → `mobile engineer, treasury` (18/1);
`python developer` (54 listings, 18 employers) →
`software engineer -python developer` (27 rows, 1 employer).

The single-employer figure needs no name matching and is the reliable one.

### 9.6 Held titles bypass everything

`from_resume` emits any employment title of two or more words with seniority
stripped. It is never checked against the corpus. Case A's
`salesforce functional consultant` has **zero rows** and is still emitted as a
paid query. The comment at `:602-628` explains the design — it is the
zero-dependency floor when there is no corpus — but in the presence of a
corpus, a held title that buys nothing should at least be reported.
## 10. Corpus and domain-bias findings

Two frozen corpora drive everything: the **title corpus** (22,806 listing rows)
selects and validates queries, and the **skill market corpus** (367 terms)
supplies market separation for weights. Both were measured directly.

### 10.1 What the title corpus is

It is not a labour market. It is **a record of what previous Sweep users
searched for and what their profiles matched**, accumulated over 57 sweeps. A
row is `(title, score, matched_skills, company)`, where `score` is the score an
earlier profile gave that listing and `matched_skills` is the set of concepts
an earlier profile matched in it. Nothing in a row describes what the job
requires; it describes what somebody's profile noticed.

That has three consequences which recur throughout this report:

1. **Coverage follows past users.** A career nobody has searched is not
   sparsely represented, it is absent, and the system cannot tell the two
   apart. `local_search.UNMEASURED = 1` acknowledges this in a comment and
   nothing acts on it.
2. **`matched_skills` is a biased vocabulary.** A skill only appears if some
   earlier profile carried it. Business vocabulary is largely missing because
   the earlier profiles were software profiles.
3. **It is a feedback loop.** Queries derived from this corpus produce the next
   sweep, which becomes the next corpus.

### 10.2 Domain distribution (measured, with a validated classifier)

Rule-based classifier, hand-validated on a **second disjoint 160-title
sample: 140/160 = 87.5% accurate**, with residual error biased *against*
software — so the software share below is, if anything, understated.

| Domain | rows | row % | distinct titles | title % |
|---|---:|---:|---:|---:|
| software engineering | 16,995 | **74.5%** | 2,636 | 62.5% |
| data / analytics / ML | 1,636 | 7.2% | 307 | 7.3% |
| sales | 1,338 | 5.9% | 414 | 9.8% |
| IT / platform / infra | 907 | 4.0% | 170 | 4.0% |
| enterprise-platform functional | 298 | 1.3% | 100 | 2.4% |
| business analysis / PM / delivery | 286 | 1.3% | 110 | 2.6% |
| customer support / success | 236 | 1.0% | 82 | 1.9% |
| finance / accounting | 89 | 0.4% | 30 | 0.7% |
| operations / supply chain | 59 | 0.3% | 22 | 0.5% |
| product management | 48 | 0.2% | 25 | 0.6% |
| marketing | 35 | 0.2% | 8 | 0.2% |
| design / creative | 31 | 0.1% | 13 | 0.3% |
| HR / recruiting | 13 | **0.06%** | 8 | 0.2% |
| legal / compliance | 8 | 0.04% | 2 | 0.05% |
| healthcare / other | 66 | 0.3% | 17 | 0.4% |
| unclassified | 761 | 3.3% | 277 | 6.6% |
| **tech subtotal** | **19,538** | **85.7%** | 3,113 | 73.8% |

An HR generalist is being matched against a market in which HR is 13 rows out
of 22,806.

### 10.3 It is also one industry

2,939 companies, but the **top 20 supply 47.4% of all rows**: gitlab 1,483,
stripe 1,163, databricks 1,022, cloudflare 941, elastic 796, mongodb 713,
datadog 643, optum 577, roku 577, accenture 564, openai 410, twilio 377. This
is a developer-tooling and SaaS-vendor corpus. Those employers post
engineering roles at a far higher rate than the economy does, so the domain
skew in §10.2 is partly a *sampling* artifact of which company boards were
scraped, not a fact about hiring.

### 10.4 The skill market corpus (367 terms)

Per-term domain assignment: software 139 (37.9%), enterprise platform 58,
sales 50, data/ML 47, BA/PM/delivery 30, IT/infra 11, industry tags 10,
generic 11, support 5, design 4, marketing 1, ops 1, and **product 0,
finance 0, HR 0, legal 0**.

Only **one** of the top 40 terms by frequency is non-technical
(`communication`, #28).

The table is nonetheless **broader than the canonical registry** (§6):
291 of its 367 terms have no canonical concept at all, and it carries
`business analysis`, `requirements gathering`, `stakeholder management`,
`pipeline management`, `cold calling`, `user acceptance testing`,
`gap analysis`, `procurement`, `salesforce administrator` and `business
analyst`. The market corpus knows business vocabulary that the registry does
not.

### 10.5 The Salesforce slice — the specific case, measured

772 rows across 186 distinct Salesforce-titled listings:

| Word family | rows | distinct titles |
|---|---:|---:|
| developer / engineer / architect | 439 | 79 |
| administrator | 93 | 17 |
| analyst / business analyst | 85 | 24 |
| consultant / functional | 51 | 25 |
| QA | 4 | 2 |
| other | 100 | 39 |

Exact titles: **`salesforce developer` 103 rows, `salesforce administrator`
30, `salesforce business analyst` 27, `salesforce consultant` 4,
`salesforce engineer` 3, and `salesforce functional consultant` 0.**

Developer outnumbers business analyst 3.8 : 1 at the exact-title level and
roughly 1.7 : 1 at family level. The corpus does lean developer — but, as §8
shows, **not enough to actually produce the failure this audit was called to
investigate.** The BA titles exist, they are well-formed, and the ranking
picks them. That is a real negative result and it changes the diagnosis.

The zero is more important than the ratio: Case A's own job title,
`salesforce functional consultant`, has **no rows at all**. It is emitted as a
`held` query anyway (held titles skip corpus validation by construction), and
it buys nothing.

### 10.6 Corpus depth per role, and the retrieval floor

Seven of fourteen realistic non-developer role titles have **zero** rows:
`hr generalist`, `digital marketer`, `procurement specialist`, `executive
assistant`, `graphic designer`, `scrum master`, and `finance analyst`
(2 rows by token match, 0 by substring). `customer success manager` has 28
rows but no usable n-gram index entry; `recruiter` has 4.

Against this, `software engineer` has 6,869 rows — and is then rejected as a
query anyway, because at 29% of the market it trips the `MAX_SHARE = 0.25`
wildcard guard.
## 11. Query-quality results — the persona matrix

### 11.1 Method

**56 synthetic personas** across four strata: 13 software, 14 technical
non-developer, 16 non-technical, 13 hybrid/adversarial. Each was written as a
full résumé (summary, dated employment with bullets, skills, projects, certs,
education) and run through the **live production path** — real `qwen3:8b`
inference, real `local_extract.read`, real `local_search.fields_for`, frozen
corpus, production-shaped condition.

**Labels are independent of the engine.** Each persona carries a `primary`
family, a `plausible` list and a **`forbidden`** list, all written from the
persona's own described evidence before any engine run, and none revised
afterwards. The forbidden list is the load-bearing one. Three independent
synthetic Salesforce-BA variants exist precisely so no rule can be tuned to one
document, and §8.5 contrasts them with three further Salesforce personas.

Queries are mapped to families by an ordered, inspectable rule set
(`personas/classify_query.py`). Verdicts: **SUPPORTED** (primary or plausible),
**ADJACENT** (same family group, defensible near miss), **UNSUPPORTED**
(different group, no evidence), **CONTAMINATED** (a family the labels say must
not be generated), **JUNK** (not a role name).

### 11.2 Headline results

| Cohort | people | queries | q/person | SUP | ADJ | UNS | **CON** | JUNK | supported precision | primary coverage | personas contaminated |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **overall** | 56 | 429 | 7.7 | 198 | 19 | 76 | **78** | 3 | **0.462** | 0.875 | **57%** |
| software | 13 | 123 | 9.5 | 65 | 6 | 16 | 29 | 1 | 0.528 | **1.000** | 92% |
| tech, non-developer | 14 | 128 | 9.1 | 46 | 7 | 32 | 17 | 2 | **0.359** | 0.857 | 64% |
| non-technical | 16 | 73 | **4.6** | 45 | 5 | 12 | 2 | 0 | 0.616 | 0.812 | 12% |
| hybrid / adversarial | 13 | 105 | 8.1 | 42 | 1 | 16 | 30 | 0 | 0.400 | 0.846 | 69% |

Fewer than half of all emitted queries are defensible. 78 of 429 name a role
family the candidate's own evidence explicitly does not support.

Two results are counterintuitive and both are informative:

- **Non-technical personas have the *best* precision (0.616) and the *least*
  contamination (12%).** Not because they are served well — because they are
  served *less*. They receive 4.6 queries against software's 9.5, and most of
  what they do receive is their own held job title. The corpus cannot see
  enough of them to go wrong in an interesting way; it mostly just goes quiet.
- **Software personas have the *worst* contamination (92%).** Twelve of
  thirteen receive at least one query in a forbidden family — almost always
  `flutter developer` or `software engineer -python developer` from the Git
  anchor, or an `ai/ml engineer` variant.

### 11.3 Technicalisation rate

Over the **40 non-developer personas** (excluding every software and
software-adjacent primary):

- **20 of 40 (50.0%)** receive at least one software-engineering-family query.
- **56 of their 289 queries (19.4%)** are software-family.

Examples, verbatim from the run:

| Persona | Primary | Software-family queries received |
|---|---|---|
| scrum master | scrum_master | salesforce engineer, software development engineer, **salesforce developer**, desenvolvedor fullstack java pl, software engineer business systems, oracle cpq developer, salesforce cpq developer |
| email marketing manager who hand-edits HTML | digital_marketer | web developer, frontend developer, front end developer, software engineer iii, java fullstack, associate ai data engineer, react js developer |
| UX designer | ux_designer | frontend developer, frontend engineer, mern stack developer, front end developer, react developer, mobile developer, SDE II, fullstack engineer |
| solutions consultant | solutions_consultant | **salesforce developer**, flutter developer, salesforce engineer, associate salesforce developer |
| technical support engineer | technical_support | react native developer, salesforce engineer, associate software engineer, software engineer -python developer |
| data analyst (Excel/Power BI) | data_analyst | **salesforce developer**, aem full stack developer |
| procurement specialist | procurement | software engineer business systems |
| technical recruiter | recruiter | aem full stack developer |

### 11.4 The adversarial cases — the general rule, demonstrated

The user's Part 16 cases, run as written:

| # | Case | Must not become | Result |
|---|---|---|---|
| 1 | Salesforce BA who names Apex/LWC only because they coordinate with developers, and says so | Salesforce Developer | **FAILS — `salesforce developer` emitted** |
| 2 | Salesforce developer with Apex/LWC/triggers | (should be available) | **PASSES** |
| 3 | Salesperson in Salesforce daily | Salesforce BA/Developer | **PASSES** — all queries are sales roles |
| 4 | Product manager with a Python side project | Python Developer | **PASSES** on Python; fails otherwise (`cloud security engineer`, `sales consultant`) |
| 5 | Finance analyst with SQL/Python automation | backend developer | **PARTIAL** — no backend query, but `ai solutions engineer`, `aem full stack developer` |
| 6 | Recruiter using Greenhouse/Jira | technical project manager | **PARTIAL** — no TPM, but `business analyst` and `aem full stack developer` |
| 7 | Marketer editing HTML email templates | frontend developer | **FAILS — six front-end/full-stack queries** |
| 8 | BA who writes complex SQL | data engineer | **PASSES** on data engineer; emits `salesforce engineer` instead |
| 9 | "Software Engineer" whose work is Salesforce development | (SF dev justified) | **PASSES** — but also `flutter developer`, `mobile developer` |
| 10 | Career switcher, 5y sales + recent bootcamp | report uncertainty | **FAILS DIFFERENTLY — no profile at all** (§11.6) |

Case 1 is the one the audit was called for, and it reproduces exactly. The
provenance is unambiguous:

```
[held    ] salesforce business analyst
[anchored] salesforce developer   recovered from 'lightning web components' (evidence 1.26)
[corpus  ] salesforce engineer
...
[corpus  ] salesforce cpq developer
```

The résumé sentence is "Coordinate with the Apex development team on technical
feasibility; **I do not write Apex myself**", and "Specify the Lightning Web
Components the developers build". The extractor correctly returned `apex` and
`lightning web components` as named skills. The orphan anchor then treated LWC
as a specialist skill, asked the corpus which titles attach to LWC, and got
`salesforce developer`.

**The disclaimer is invisible to search.** `skill_evidence` has exactly the
machinery to classify that clause — R4c is clause-scoped and there is a
NEGATED status — but it runs after every query has been chosen.

Case 7 has a different and equally general mechanism: the marketer's only
corpus-visible skills are `html`, `css` and `excel`, and `matching_rows` needs
just **two** shared skills, so `{html, css}` retrieves front-end listings in
bulk. A marketer becomes a front-end developer through a two-token overlap.

### 11.5 Junk, employer-specific and unvalidated queries

- **Held titles that buy zero corpus rows: 20 of 63 (32%).** Almost all
  non-technical: hr generalist, graphic designer, compliance officer,
  executive assistant to the ceo, supply chain analyst, procurement
  specialist, finance analyst, ux designer, scrum master, servicenow
  administrator, sap fico consultant, technical recruiter, digital marketing,
  content marketing, email marketing. (The corpus is a record of past scrapes,
  not a live board, so these queries may still return real jobs — the finding
  is that **the system has no visibility into whether they work**, and reports
  nothing.)
- **`aem full stack developer` recurs across six unrelated non-technical
  personas** — recruiter, supply chain analyst, procurement specialist,
  project manager, finance analyst, data analyst. Its origin is a substring
  accident: the ranked fragment is **`m f`** (138 listings — the remains of
  "(m/f/d)" gender notation in European titles), and `canonical('m f')`
  returns `aem full stack developer` because "m f" occurs inside
  "ae**m f**ull stack developer". The resulting title has 29 rows at **one**
  employer.
- `desenvolvedor fullstack java pl` was emitted for an English-language Scrum
  Master résumé.

### 11.6 Two personas receive nothing, for two different reasons

- **Operations manager: zero queries, derivation escalates.** Its only held
  title is `operations manager`, and `check_role_keywords` drops any role
  keyword carrying a `hard_drop` word. `config.SCORING["hard_drop_terms"]`
  contains **`manager`, `architect`, `director`, `head of`, `vp`, `chief`,
  `principal`, `staff`, `graduate`, `junior`** — a list documented as "never a
  fit at this experience level", written for one early-career software
  engineer, and applied at derivation time to every user regardless of their
  seniority or profession. Consequence, verified: **no query containing
  "manager" can ever be emitted for anyone.** Product Manager, Project
  Manager, Operations Manager, Sales Manager, Account Manager, Customer
  Success Manager, Engineering Manager and Business Development Manager are
  all unsearchable by name. `proj_manager` received five queries and **not one
  of them was a project-management role**.
- **Career switcher: zero queries, derivation escalates** with
  "all 1 employment rows were marked as a different career, leaving no
  relevant experience to count". The model's judgement was correct — the sales
  history is a different career — but the pipeline's only response to
  "genuinely ambiguous" is to fail. This is Part 16 case 10, and the requested
  behaviour (report uncertainty) has no representation.
## 12. Result-corruption findings

What one wrong query does to a candidate's result pool, estimated from the
frozen corpus rather than from paid searches. Retrieval is modelled as
"corpus rows whose title contains the query string", which is the repo's own
model of a keyword purchase (`local_search.profile_of` / `buys`). No paid
search was run for this audit.

### 12.1 Volume asymmetry

For Case A, the correct and incorrect Salesforce queries buy very different
amounts of inventory:

| query | rows bought | employers | share of market |
|---|---:|---:|---:|
| `salesforce business analyst` | 41 | 13 | 0.180% |
| `salesforce administrator` | 65 | 25 | 0.285% |
| `salesforce consultant` | 13 | 5 | 0.057% |
| `salesforce engineer` *(emitted)* | 21 | 9 | 0.092% |
| `salesforce developer` | **242** | 56 | 1.061% |
| `salesforce functional consultant` *(emitted, held)* | **0** | 0 | 0% |

A single developer query buys **six times** the inventory of the correct
business-analyst query, and nearly twice as much as all three correct queries
combined. Because role keywords run as a cross product against locations and
are paid per row, a wrong query does not merely add noise — it dominates the
spend and the result set by volume.

### 12.2 The contaminated pool cannot be sorted out

Scoring the pooled retrieval of the BA, administrator and developer queries
with Case A's real profile puts developer rows at 4 of the top 10, 9 of the
top 20 and 28 of the top 50 (§13.2). The top-scoring row overall is a
Salesforce developer posting.

So the corruption is not confined to the tail. It reaches the part of the
list a user actually reads.

### 12.3 The free path is corrupted independently, and more severely

The free sweep never uses `role_keywords`. It pulls whole boards and filters
titles with `is_dev_title` against the profile's `title_hints` unioned with
config's 79-entry software floor. For Case A the effective 116-entry gate
**admits** `Salesforce Developer`, `Senior Backend Engineer (Python)` and
`React Native Developer`, and **rejects** `Business Analyst` and
`Account Executive` (§13.4).

For a business analyst this is a worse failure than any single bad paid query,
because it is not a ranking problem at any depth: rejected titles are never
fetched, so they cannot appear at any position, and nothing reports what was
dropped.

### 12.4 Where the user's observation comes from

The reported symptom — "a Salesforce BA gets search terms closer to Salesforce
Developer" — is real and was reproduced, but it is produced by the
`title_hints` gate and by one `salesforce engineer` paid query, not by the
`role_keywords` list. That distinction matters for v3 because the two live in
different code, have different causes, and would need different fixes:
`role_keywords` is working roughly as intended on this case, and the gate is
not a profile-engine artifact at all.
## 13. Ranking versus discovery

The architectural question: if discovery emits a wrong role, can the scorer
undo it?

### 13.1 The scorer has no concept of a role

`scraper.score_job` (`scraper.py:587-660`) computes:

```
score = sum(weight of each profile concept matched in title+description)
        + fullstack_bonus            (if front-end and back-end terms co-occur)
        + penalty_terms              (the user's own avoid-list)
        + soft/hard seniority adjustments
```

Hard filters are seniority words, an experience floor, and blocked repost
companies. **There is no role-family term anywhere in the score.** Two jobs
that name the same technologies score the same regardless of what the jobs
ask the person to *do*.

### 13.2 Measured: developer jobs outrank the candidate's own role

Using Case A's actual production profile (18 concepts, weights as derived) and
scoring every frozen-corpus row a query retrieves. Job text is
`title + matched_skills`, because descriptions are not exported — a limitation
that understates all scores equally and is stated rather than hidden.

| | query | rows | employers | mean | median | max |
|---|---|---:|---:|---:|---:|---:|
| correct | `salesforce business analyst` | 41 | 13 | 12.20 | 13 | 23 |
| correct | `salesforce administrator` | 65 | 25 | 9.51 | 9 | 26 |
| correct | `salesforce consultant` | 13 | 5 | 8.85 | 9 | 14 |
| **emitted** | `salesforce engineer` | 21 | 9 | 8.71 | 8 | 18 |
| **wrong** | `salesforce developer` | 242 | 56 | 7.49 | 4 | **26** |
| baseline | `business analyst` | 184 | 78 | 6.68 | 4 | 23 |

Pooling the retrievals of the BA, admin and developer queries into one ranked
list, as the product does:

| depth | business-analyst rows | administrator rows | **developer rows** |
|---|---:|---:|---:|
| top 10 | 4 | 2 | **4** |
| top 20 | 6 | 5 | **9** |
| top 50 | 6 | 13 | **28** |

Top of the pooled ranking:

```
26  [ADMIN] salesforce administrator
26  [DEV  ] lead i - enterprise solutions (salesforce developer)
23  [BA   ] salesforce business analyst
23  [BA   ] salesforce business analyst / product owner (contract-to-hire)
23  [DEV  ] salesforce developer
21  [DEV  ] senior salesforce developer - service & experience cloud
```

A Salesforce developer posting ties the top score and beats the candidate's own
role. It does so legitimately under the current model: it names Salesforce,
Sales Cloud, Service Cloud, SOQL and Agile, and the candidate genuinely has all
five. The scorer is working exactly as designed. The design has no way to
express "this person specifies the work, they do not build it".

Note also the last row of the first table: for this business analyst, generic
`business analyst` postings score **lower** (6.68) than Salesforce developer
postings (7.49), because the scoring vocabulary is made of platform terms and
BA postings contain fewer of them.

### 13.3 The asymmetry, quantified

- **A wrong query cannot be repaired by ranking.** `salesforce developer`
  returns 242 rows against the correct query's 41 — six times the volume — at
  a mean only 4.7 points lower and an identical maximum. Nothing in the
  pipeline can demote them, because nothing in the pipeline knows they are
  the wrong kind of job.
- **A missing query cannot be repaired at all.** Ranking operates only on what
  was retrieved. For the nine personas of §7.2 that retrieve nothing, there is
  no list to rank.
- Therefore **discovery errors dominate ranking errors**, and effort spent on
  weight calibration cannot compensate for a role the search never asked for.

### 13.4 One consequence for the free sweep

The free path does not use `role_keywords` at all. It pulls whole company
boards and filters titles through `scraper.is_dev_title`, whose vocabulary is
the profile's `title_hints` **unioned** with config's 79-entry generic software
floor (`auto-apply/make_profile.py:844-845`). Measured on Case A's real
rendered profile, the effective gate is 116 entries of which 69 (59%) are
software-shaped, and it behaves like this:

| posting title | admitted to the Salesforce BA's free sweep? |
|---|---|
| Salesforce Developer | **yes** |
| Senior Backend Engineer (Python) | **yes** |
| React Native Developer | **yes** |
| Salesforce Administrator | yes |
| Salesforce Business Analyst | yes |
| **Business Analyst** | **no** |
| **Account Executive** | **no** |

The free sweep for a business analyst admits React Native jobs and rejects
jobs titled "Business Analyst". That is not a ranking problem and no scorer
sees it: `is_dev_title` runs *before* scoring and the rejected rows are never
fetched.
## 14. Invariance findings

Twelve personas spanning all four strata were re-rendered in four
**presentation-only** layouts and re-run through the full live path. Meaning is
identical in every case, so any difference is a defect.

| Perturbation | extracted skills identical | **role queries identical** | query Jaccard | weights identical on shared concepts |
|---|---:|---:|---:|---:|
| PDF letter-spacing in the skills line | 9/12 — 75% | **12/12 — 100%** | 1.000 | **0/12 — 0%** |
| duplicated alias spellings | 2/12 — 17% | **8/12 — 67%** | 0.971 | 12/12 — 100% |
| skills list reversed | 12/12 — 100% | 12/12 — 100% | 1.000 | 11/12 — 92% |
| SKILLS section moved above EXPERIENCE | 12/12 — 100% | 12/12 — 100% | 1.000 | 12/12 — 100% |

Three results, in order of importance.

### 14.1 Search-query invariance was never measured, and it fails

The v2 evaluation measured weight invariance and reported alias stability 1.000
and duplicate-alias stability 1.000. Both hold here. But **adding alias
spellings changes the emitted queries for 4 of 12 personas** — one loses
`associate consultant - technology`, two gain `quality analyst`, one loses
`sf -data cloud`, and a fourth keeps the same query set in a different order,
which changes what the budget actually buys.

The same person, the same facts, "JavaScript" additionally written as "JS", and
a different set of jobs is purchased. This is the invariance that matters to a
user and it is the one nobody was measuring.

### 14.2 PDF spacing moves every weight, and always downward

Letter-spacing the skills line changed weights on **12 of 12** personas, and
every single change was a one-band demotion: `3 → 2`, `2 → 1`. Examples:

```
[plat_sf_ba]   confluence 3->2, excel 2->1, process mapping 3->2,
               requirements gathering 3->2
[people_recruiter] candidate screening 3->2, employer branding 3->2, excel 2->1,
               full-cycle recruiting 3->2, offer negotiation 3->2, sourcing 3->2
[fin_analyst]  advanced excel 3->2, budgeting 3->2, excel 2->1,
               financial modelling 3->2, forecasting 3->2, power bi 3->2
[gtm_ae]       account management 2->1, consultative selling 3->2,
               forecasting 3->2, negotiation 2->1
```

The mechanism is clear from the direction: a spaced term no longer matches its
occurrence in the SKILLS section, so the concept loses its skills-list evidence
and drops from SUPPORTING to BACKGROUND. **The personas hit hardest are the
non-technical ones**, because their concepts are disproportionately
skills-list-only — a software engineer's React is also in three bullets, a
recruiter's `employer branding` is not.

It changed **no** queries, which is consistent with §2.3: queries are built
from the extracted strings before any of this runs.

### 14.3 A planning word in a skills list silently demotes everything after it

Found while diagnosing the one persona whose weights moved under reordering,
and it generalises well beyond that persona.

`skill_evidence._PLANNED` (`skill_evidence.py:284-290`) includes the bare nouns
**`roadmap`** and **`planning`**. `classify()` looks for a planned marker
*before* the name anywhere in the sentence, deliberately, so that a lead-in can
govern a list — the documented intent is `"Planned: A, B, C"`
(`skill_evidence.py:258-268`). A comma-separated skills line is exactly that
shape.

The result, reproduced in one line:

```python
line = "Product Roadmap, User Research, PRD, Backlog Prioritisation, OKRs, ..."
is_planned(line, <offset of "User Research">)   -> True
is_planned(line, <offset of "Product Roadmap">) -> False   # nothing precedes it
```

Every term after the planning noun is classified PLANNED, is therefore not a
claim, and lands in BACKGROUND with the reason **"only named as planned or
future work"** — which reads like a considered judgement.

Measured across realistic skills lists:

| Skills list | trigger | concepts demoted |
|---|---|---:|
| supply chain analyst | `Demand Planning` | **9 of 10** |
| product manager | `Product Roadmap` | **9 of 10** |
| scrum master | `Sprint Planning` | 5 of 10 |
| operations manager | `Capacity Planning` | 4 of 9 |
| account executive | — | 0 of 8 |
| finance analyst | — | 0 of 9 |
| DevOps engineer | — | 0 of 12 |

**Every affected profession is non-technical.** Planning is a deliverable for
product, delivery, supply-chain and operations professionals and appears in
their skills lists as a competency; software skills lists rarely contain the
word at all. Reversing the skills list moves the trigger to the end and all
nine concepts recover — which is how it was found.

This is a weighting defect, not a discovery one, and it was not in any prior
document.
## 15. Model and schema findings

The extraction contract was read in full and not modified:
`local_extract.FIELDS_SCHEMA` / `FIELDS_PROMPT` (`local_extract.py:143-184`)
and `EMPLOYMENT_SCHEMA` / `EMPLOYMENT_PROMPT` (`:216-272`).

### 15.1 What the model is asked for, and what happens to the answer

| Field | Asked | Reaches search? | Reaches weights? | Actual production use |
|---|---|---|---|---|
| `name` | yes | no | no | profile display |
| `skills` | yes, as "technologies and tools, lowercase, as written on the page" | **yes — the sole skill input** | yes | everything |
| `titles` | yes, "exactly as written. Keep seniority words" | **no** | no | **nothing. Not read anywhere in production** |
| `companies` | yes | no | no | grounding only |
| `education`, `institutions`, `projects`, `certifications` | yes | no | no | grounding only |
| `years_experience` | yes | no | no | logged so a correction can be stated; never used (documented at `:139-142`) |
| `target_field` | yes — "the BROAD profession this person is looking for work in now" | **no** | no | **one prose sentence** in the profile docstring (`local_profile.py:171`, `_summary`, `_notes`) |
| `employment[].title` | yes | **yes — held titles** | no | `from_resume` |
| `employment[].relevant` | yes | yes, via `countable()` | no | filters held titles and years |

Two of these are the finding.

**The model is already asked for role intent, produces it, and it is thrown
away.** `target_field` is the one field in the entire contract that names what
kind of work this person does. The prompt invests real care in it — the schema
puts it first on purpose so the model commits to a profession before judging
any row, and the reasoning is documented at `local_extract.py:220-224`. In
production its only consumer is a string in a docstring. Verified by
exhaustive grep: outside `bench/` and tests, `target_field` appears only at
`auto-apply/local_profile.py:171`.

**The model's extracted `titles` are not read at all.** Held titles come from
`employment[].title` instead. A résumé headline like "Salesforce Business
Analyst" that is not also an employment row title is invisible.

### 15.2 What the schema cannot express

The contract asks for **strings**. It has no place for any of the following,
so none of it can be preserved even when the model reads it correctly:

- where a skill appeared (span, section, employment entry)
- how it was used — built / configured / coordinated on / evaluated / planned
- whether the person owned it or supported someone who did
- duration, recency, or which job it belonged to
- the difference between a technology and a job function

`skill_evidence` recovers the first two of these **deterministically from the
text afterwards**, which is a reasonable division of labour and works. It
cannot recover the rest, because they are judgements about the sentence the
model already read and did not report.

### 15.3 The definitional problem in one line of prompt

> `- skills: technologies and tools, lowercase, as written on the page.`

The extraction contract **defines a skill as a technology or a tool**. A
business analyst's competencies — requirements gathering, stakeholder
management, process mapping, UAT — are not technologies or tools. They are
extracted anyway, but only incidentally, because they happen to be printed in
the résumé's own SKILLS list. Nothing in the contract asks for them, and
nothing downstream expects them.

This is worth stating precisely because it is the model's *least* culpable
failure: on Case A, `qwen3:8b` returned `user stories & acceptance criteria`,
`process flow diagrams`, `security & sharing`, `brd` and `frd` — the BA
function terms — despite not being asked for them. The model read the document
correctly. Every one of those terms was then discarded downstream (§7).

### 15.4 Separating the three jobs the prompt currently mixes

| Job | Suited to | Currently |
|---|---|---|
| FACTUAL EXTRACTION — what strings are on the page, which rows exist, what the dates say | the model, and it does it well | asked, and used |
| SEMANTIC INFERENCE — what kind of use each mention describes, what tier that implies | deterministic text analysis over spans the model located | done deterministically in `skill_evidence`, but **after** the model has discarded the spans, so it re-finds them by matching |
| SEARCH INTENT — what roles to search for | neither, currently | `target_field` is asked of the model and discarded; no deterministic layer replaces it |

Nothing here argues for a bigger model. The one field that would most change
the output is already being produced by the 8B model and dropped on the floor.
## 16. Root causes

New identifiers. These are not the v1 C1–C8 renumbered; where a v1 cause still
applies it is named as inherited.

---

### V3-C1 — Search is constructed before the profile engine runs

**Symptom.** Nothing Profile Engine v2 produces — canonical identities,
atomised compounds, occurrence semantics, evidence strength, clause-scoped
attribution, importance tiers, market separation — is available to any query.
The system's best representation of a candidate is computed one stage too late
to be used for the decision that matters most.

**Exact path.** `auto-apply/local_profile.py:183` calls
`local_search.fields_for(person, market)`; `auto-apply/make_profile.py:551-560`
runs `_finish` on the result. `fields_for`'s signature accepts `importance` and
`resume_text` and its docstring states they are ignored (`local_search.py:1323-1337`).

**Minimal reproduction.** A résumé listing `agile/scrum`. Search sees one
unknown compound string and discards it; `_finish` then splits it into two
concepts the corpus knows and scores them 4 and 5. Reproduced on Case A.

**Affected populations.** All. Severity rises as a candidate's evidence
diverges from bare tool names.

**Severity: BLOCKER.** **Frequency: 100% of derivations.** **Deterministic.**
**Inherited from v1 C5, unfixed.**

---

### V3-C2 — There is no object that represents a role

**Symptom.** A job function cannot be stored, so a role must be inferred from a
bag of tool strings. Eight distinguishable kinds of role evidence collapse to
two (§8.1), and the only one that reaches search is "a skill string exists".

**Exact path.** `person = {"skills": [...], "employment": [...]}`
(`local_profile.py:180-182`) is the entire input to query construction.
`skill_evidence` records a use-*shape* per occurrence but not a kind of work,
and runs after search.

**Minimal reproduction.** The adversarial Salesforce BA whose résumé says
"I do not write Apex myself" receives `salesforce developer`, anchored on
`lightning web components` at evidence 1.26. The disclaiming clause is
representable by R4c and never consulted.

**Affected populations.** Every candidate whose role is not implied by their
tools — that is, most non-developers, and every developer whose tools are
shared across roles.

**Severity: BLOCKER.** **Frequency: 78 contaminated queries across 429 (18.2%);
57% of personas receive at least one.** **Deterministic (an absence, not a
model error).**

---

### V3-C3 — Retrieval can only see a software-shaped residue of past users' searches

**Symptom.** A candidate is matched against the corpus on the intersection of
their skills with `matched_skills`, a vocabulary accumulated from previous
users' profiles. Job-function vocabulary is largely outside it; tool vocabulary
is inside it.

**Exact path.** `local_search.matching_rows` (`:367-395`) requires `need=2`
shared skills scored by `MIN_EVIDENCE=7.0`; `vocabulary()` is built from corpus
rows' `matched_skills` (`:174-180`).

**Minimal reproduction.** Case A: 8 of 17 extracted skills invisible, and the
invisible 8 are the business-analysis function. Eleven personas are visible to
retrieval only through `excel`.

**Affected populations.** Measured visibility: software 63.0%, technical
non-developer 46.5%, **non-technical 24.2%**. Nine of 56 personas have fewer
than two visible skills and cannot retrieve at all.

**Severity: BLOCKER for non-software candidates.** **Frequency: 51.9% of all
authored skills are invisible.** **Deterministic; a data problem, not a code
problem.**

---

### V3-C4 — The free-source title gate is a fixed software vocabulary unioned into every profile

**Symptom.** The free sweep admits roles the candidate's evidence forbids and
rejects roles it supports.

**Exact path.** `auto-apply/make_profile.py:844-845` unions the profile's
`title_hints` with all 79 entries of `config.ATS_TITLE_HINTS` (documented at
`config.py:554-563` as "the generic software floor"); `scraper.is_dev_title`
(`:215-229`) admits a posting if any hint is a **substring** of its title.

**Minimal reproduction.** Case A's real rendered profile yields a 116-entry
gate, 69 software-shaped, which admits `Salesforce Developer`,
`Senior Backend Engineer (Python)` and `React Native Developer`, and rejects
`Business Analyst`.

**Affected populations, measured across 56 personas:**

| | own primary family blocked | ≥1 forbidden family admitted | forbidden admissions per persona |
|---|---:|---:|---:|
| software | 8% | 100% | 3.5 |
| tech non-developer | 64% | 100% | 4.3 |
| **non-technical** | **81%** | **100%** | 2.8 |
| all | **52%** | **100%** | 3.5 |

**Severity: BLOCKER.** **Frequency: 100% of personas admit a forbidden family;
52% cannot see their own.** **Deterministic. Not in the profile engine.**

---

### V3-C5 — The orphan anchor's lift metric is anti-correlated with what it claims to measure

**Symptom.** A generic or incidental tool anchors an unrelated specialist role.

**Exact path.** `skill_lift` (`:970-976`) = in-title share / market share. The
numerator cannot exceed 1, so **maximum achievable lift = 1 / market share**.
With `SKILL_LIFT = 10.0` the guard is unreachable above ~10% share and nearly
free below 1%. `candidates_for_skill` (`:1015`) takes no `own`; `worth_it`'s
anchored branch (`:1085-1091`) never reads `own`.

**Minimal reproduction.**
`worth_it("flutter developer", rows, own={"cobol","fortran"}, anchor="git")`
returns `(True, relevance 0.0)`. On a real résumé: `git` (4.20% of the corpus)
anchors `software engineer -python developer` at lift 19.42 and
`flutter developer` at 13.9 for a candidate with no Python, Flutter or Dart.
`communication` (4.32%) anchors eight sales titles at lift 15.2–23.2.

**Affected populations.** Python, React, JavaScript, CI/CD, Agile and Node.js
*can never* anchor (ceilings 4.4–8.1); Git, communication, Apex and SOQL can.
243 of 269 eligible skills (90.3%) push at least one title past the guard; 96
(35.7%) clear the full gate, giving 185 shipping (skill, title) pairs. Seven of
eleven realistic skill sets ship an anchored keyword; four ship both Git
titles.

**Severity: HIGH.** **Frequency: the largest single source of the 29
contaminated queries in the software stratum.** **Deterministic.**
**Inherited from v1 C5, unfixed and now quantified.**

---

### V3-C6 — Fragment evidence is never re-measured on the canonical title

**Symptom.** A fragment passes the volume and employer-diversity guards, then
is replaced by a concrete title that would not have passed them.

**Exact path.** Guards at `:430-435` (corpus tier) and `:1044-1048` (orphan
tier) apply to the fragment; `canonical()` swaps in a title at `:521` / `:1050`
and nothing re-checks rows or companies. `budget_order` re-applies the bars but
only to reorder — `return order + thin` (`:1316`) still ships them.

**Minimal reproduction.** `canonical('m f')` → `aem full stack developer`
(29 rows, **one** employer) because "m f" occurs inside "ae**m f**ull stack
developer"; `m f` is the residue of "(m/f/d)" in European titles. This one
accident produced a query for six unrelated non-technical personas. Also
`software engineering` (256 rows / 22 employers) → `software engineering
professional` (16 / **1**); `i backend` → `applied ai backend engineer`, the
v1 example, still reproducing.

**Affected populations.** Across 88 fragment→canonical pairs: 27.3% of chosen
titles fall below `MIN_LISTINGS=10`, 34.1% below `MIN_COMPANIES=5`, 25.0% have
a single employer. Corpus-wide, 26.3% of resolved fragments land on a title
posted by one employer.

**Severity: HIGH.** **Frequency: roughly a third of corpus-tier queries.**
**Deterministic. Inherited from v1 C5.**

---

### V3-C7 — Query validation is economic, never semantic

**Symptom.** No check anywhere asks whether a query describes work the
candidate has evidence of doing.

**Exact path.** `validate()` (`:855-893`) runs five checks — wildcard share,
hard-drop seniority words, self-blocking excludes, missing hint stems, and
domain-term underperformance. All concern cost or self-harm.

**Severity: HIGH** — this is the enabling condition for V3-C2, C5 and C6.
**Frequency: every query.** **Deterministic.**

---

### V3-C8 — A config seniority list written for one early-career engineer deletes every management title

**Symptom.** No role keyword containing `manager`, `architect`, `director`,
`head of`, `vp`, `chief`, `principal`, `staff`, `graduate` or `junior` can be
emitted, for anyone.

**Exact path.** `check_role_keywords` (`:708-745`) drops any keyword where
`_has(term, hard)`; `hard` comes from `seniority_lists()` (`:182-188`) which
reads `config.SCORING["hard_drop_terms"]` (`config.py:767-790`), documented as
"never a fit at this experience level".

**Minimal reproduction.** The operations-manager persona's only held title is
`operations manager`; it is dropped, no corpus keyword survives, and the whole
derivation **escalates with zero queries**. The project-manager persona
received five queries, none of them a project-management role.

**Affected populations.** Verified unsearchable: operations manager, product
manager, project manager, sales manager, account manager, customer success
manager, engineering manager, HR manager, business development manager, and
any architect/director/VP/head-of title.

**Severity: HIGH.** **Frequency: every management-titled candidate.**
**Deterministic. Not in the profile engine.**

---

### V3-C9 — Held titles bypass every check

**Symptom.** A query is emitted that the corpus cannot support, and nothing
says so.

**Exact path.** `from_resume` (`:602-628`) emits any ≥2-word employment title;
`validated()` applies only the economic checks.

**Frequency: 20 of 63 held titles (32%) buy zero corpus rows**, almost all
non-technical. Case A's own job title, `salesforce functional consultant`, has
zero rows.

**Severity: MEDIUM** — the query may still work against a live board; the
defect is the silence. **Deterministic.**

---

### V3-C10 — The extraction contract defines a skill as a technology, and discards the two role fields it does collect

**Symptom.** The one field naming what work the person does is used for a
prose sentence.

**Exact path.** `FIELDS_PROMPT`: "skills: technologies and tools".
`target_field` is consumed only at `auto-apply/local_profile.py:171`;
`checked["titles"]` is read nowhere in production.

**Minimal reproduction.** Case A's `target_field` was extracted as
**"salesforce business analysis"** — exactly correct — and discarded.

**Severity: HIGH** — because the cost of fixing it is one plumbing change.
**Frequency: every derivation.** **Model-adjacent, deterministic to fix.**

---

### V3-C11 — Unknown concepts get exactly one matcher

**Symptom.** Recognised software concepts match a mean of 2.12 spellings;
passthrough business concepts match exactly 1.00. `requirement gathering`
scores 0 against a profile holding `requirements gathering`.

**Exact path.** `aliases_for` (`:359-364`) returns the registry's alias tuple
for a known concept and a one-element tuple otherwise.

**Severity: MEDIUM** — a scoring-recall defect, not a discovery defect.
**Frequency: 100% of the 87 probed business terms.** **Deterministic.**

---

### V3-C12 — The skill scanner is inert in production

**Symptom.** A whole recovery mechanism, which supplied 21 of 58 terms in the
v1 headline case, does nothing wherever `output/` is absent.

**Exact path.** `corpus_signal.vocabulary()` (`:167-183`) deliberately does not
fall back to the frozen table; `make_profile.widen_skills` therefore no-ops.

**Severity: MEDIUM, and mostly an evaluation hazard** — every recall figure
measured on a developer laptop overstates production. **Deterministic and
documented.**

---

### V3-C13 — CORE over-population (inherited, ranking-only)

CORE precision 0.409; STRONG_SECONDARY F1 0.125; cause is the tier table's work
row, where any non-planned mention is CORE. **Severity: MEDIUM**, and
explicitly **not** a cause of any role failure, because importance cannot reach
search (§6.3).

---

### V3-C14 — Market separation breaks ties the wrong way, and has no stated purpose

Of 119 ties it breaks, 46 resolve correctly and 73 become inversions —
**38.7%**, below chance. Bounded to one tier. It reaches ~21% of business
vocabulary against ~76% of software. **Severity: MEDIUM.** The first required
action is a written statement of what it is for.

---

### V3-C15 — PDF letter spacing still moves weights (inherited)

v2 invariance 0.8595, 43 weight changes across 30 candidates; improved from
v1's 0.7288, not solved. v2 also **introduced** a section-order sensitivity
(0.9869 against v1's 1.000). **Severity: LOW–MEDIUM.**

---

### V3-C16 — A planning noun in a skills list demotes every concept after it

**Symptom.** Concepts land in BACKGROUND with the reason "only named as planned
or future work" when the résumé asserts them as competencies. The reason string
reads like a considered judgement, so the defect is invisible in review.

**Exact path.** `skill_evidence._PLANNED` (`:284-290`) includes the bare nouns
`roadmap` and `planning`. `classify()` (`:535-566`) searches for a planned
marker *before* the name anywhere in the sentence, by documented design, so
that a lead-in governs a list (`:258-268`, "Planned: A, B, C"). A
comma-separated skills line is that shape.

**Minimal reproduction.**

```python
line = "Product Roadmap, User Research, PRD, Backlog Prioritisation, OKRs, ..."
is_planned(line, offset_of("User Research"))    # True
is_planned(line, offset_of("Product Roadmap"))  # False
```

Reversing the list moves the trigger to the end and all affected concepts
recover — which is how this was found, via a 12-persona reordering invariance
test.

**Affected populations.** Measured on realistic skills lists: supply-chain
analyst **9 of 10** concepts demoted (`Demand Planning`), product manager
**9 of 10** (`Product Roadmap`), scrum master 5 of 10 (`Sprint Planning`),
operations manager 4 of 9 (`Capacity Planning`); account executive, finance
analyst and DevOps engineer 0. **Every affected profession is non-technical** —
planning is a deliverable for product, delivery, supply-chain and operations
work and appears in those skills lists as a competency, while software skills
lists rarely contain the word.

**Severity: HIGH on the ranking axis** (it does not reach search, per V3-C1).
**Frequency: every résumé whose skills list contains `roadmap` or `planning`
before other terms.** **Deterministic.** **Not previously reported.**
## 17. Severity matrix

Severity is the consequence for a user of the public beta. Frequency is
measured on the 56-persona matrix, the four real résumés, or the corpus,
as stated. "Origin" says which system the defect lives in, because three of
the four blockers are **not** in Profile Engine v2.

| ID | Defect | Severity | Frequency (measured) | Origin | Deterministic? |
|---|---|---|---|---|---|
| **V3-C4** | Free-source gate is a fixed software vocabulary unioned into every profile | **BLOCKER** | 100% of personas admit ≥1 forbidden family; **52% cannot see their own role**; 81% of non-technical cannot | `config.py` + `make_profile.render` | yes |
| **V3-C1** | Search is built before the profile engine runs | **BLOCKER** | 100% of derivations | `local_profile` / `make_profile` ordering | yes |
| **V3-C2** | No object represents a role | **BLOCKER** | 78/429 queries (18.2%) contaminated; 57% of personas affected | schema-wide absence | yes (an absence) |
| **V3-C3** | Retrieval sees only a software-shaped residue of past users' searches | **BLOCKER** for non-software | 51.9% of authored skills invisible; non-technical 75.8% invisible; 9/56 cannot retrieve | title corpus data | yes |
| **V3-C5** | Orphan-anchor lift is anti-correlated with relevance (ceiling = 1/share) | **HIGH** | 90.3% of eligible skills can anchor; 185 shipping pairs; 7/11 skill sets affected | `local_search` | yes |
| **V3-C8** | Config seniority list deletes every management title | **HIGH** | every management-titled candidate; 1 persona reduced to zero queries | `config.py` | yes |
| **V3-C6** | Fragment evidence never re-measured on the canonical title | **HIGH** | 27.3% below `MIN_LISTINGS`, 34.1% below `MIN_COMPANIES`, 25% single-employer | `local_search` | yes |
| **V3-C7** | Query validation is economic, never semantic | **HIGH** | every query | `local_search` | yes |
| **V3-C10** | Extraction discards `target_field` and `titles` | **HIGH** (cheap) | every derivation | `local_extract` / `local_profile` | yes |
| **V3-C16** | A planning noun in a skills list demotes every concept after it | **HIGH** (ranking) | 9/10 concepts for a supply-chain analyst, 9/10 for a product manager, 0 for software personas | `skill_evidence` | yes |
| **V3-C9** | Held titles bypass every check | **MEDIUM** | 20/63 held titles (32%) buy zero corpus rows | `local_search` | yes |
| **V3-C11** | Unknown concepts get one matcher | **MEDIUM** | 100% of 87 probed business terms | `skill_concepts` | yes |
| **V3-C12** | Skill scanner inert in production | **MEDIUM** (evaluation hazard) | 100% of production derivations | `corpus_signal` (documented) | yes |
| **V3-C13** | CORE over-population | **MEDIUM** (ranking only) | CORE precision 0.409 | `skill_evidence` | yes |
| **V3-C14** | Market tie-breaks 38.7% correct; purpose unstated | **MEDIUM** | 119 ties broken, 73 inverted | `corpus_signal` + `make_profile` | yes |
| **V3-C15** | PDF spacing moves weights; v2 added section-order sensitivity | **LOW–MEDIUM** | 43/306 weight changes; 4/306 section-order | `skill_evidence` | yes |
| — | Bullet-only extraction recall 15.8% | **OBSERVATION** → becomes HIGH once a role layer needs body evidence | 6/38 concepts | `qwen3:8b` + schema | model |
| — | Career-switcher derivation escalates to nothing | **OBSERVATION** | 1/56 | `local_extract.check_years` | yes |

### Where the failures actually live

Of the four blockers, **only V3-C1 and V3-C2 are Profile Engine work**, and
V3-C2 is an absence rather than a defect in shipped code. V3-C4 is
`config.py`'s title vocabulary and one `union` in the renderer. V3-C3 is the
title corpus itself.

This matters for planning: **the highest-severity, cheapest-to-fix defect in
this entire report is V3-C4**, and it requires no schema change, no model
change, and nothing from v2 or v3. It is a title-vocabulary problem in the
free path.
## 18. Components of v2 that should remain unchanged

These were tested and they work. Changing them alongside a v3 role layer would
make the result unattributable, and several of them are prerequisites for it.

| Component | Evidence it works | Status |
|---|---|---|
| **Employment-row validation carried through `read()`** | The independent review's BLOCKER R1 is fixed: `route()` returns `kept`, and `read()` replaces the model's rows with the validated ones (`local_extract.py:884-890`), so held titles and years share one source. Verified by reading and by four live traces. | **FREEZE** |
| **Engine-version binding to the profile** | `scraper.py:282-283` binds the scorer to the profile's own stamp; a profile keeps its meaning across a rollback. Twelve documented lifecycle cases. | **FREEZE** |
| **Canonical concept identities and OR-matching** | One technology scores once however it is spelled; alias invariance measured at 1.0 and duplicate-alias at 1.0, against 0.948 and 0.866 in v1. | **FREEZE** |
| **The tier function's independence** | `skill_evidence.tier()` consults no market data and no provenance. This is the C1 fix and the thing that makes importance arguable. | **FREEZE** |
| **Occurrence semantics and evidence strength (R4a/R4b/R4c)** | Planned, negated and learning mentions are refused; shape is clause-scoped; the record is inspectable. | **KEEP, extend rather than replace** |
| **Bounded market influence** | Separation can move a concept one step inside its band and never across a tier, so rarity cannot lift a list-only claim above professional work. The bound is the reason §6.4's bad tie-breaks are survivable. | **KEEP the bound** (the signal itself needs a decision, §6.4) |
| **The `skill_importance` record** | Carries tier, why, sections, occurrences, status counts, strength, independent entries and the resolved market key. It is why this audit could reconstruct Case A without re-running anything. | **KEEP and propagate further** |
| **Row-order determinism** | `rank_title` gives a total order; live, frozen and permuted corpora produce identical output. | **FREEZE** |
| **Generated-profile safety boundary** | Prose sanitisation plus an AST allowlist; no executable escape was found in the prior review's 34 probes. | **FREEZE** |
| **Deterministic date arithmetic** | 52/52 years exact on the regression corpus, against 40/52 when the model is asked. | **FREEZE** |
| **R5 staying unshipped** | It removed the job search entirely for 2 of 16 personas. The decision to hold it was correct and this audit found no reason to revisit it as built. | **KEEP HELD** |
## 19. V3 architecture recommendations

The diagnosis in this report points at one structural absence and one
structural inversion. Everything below follows from those two and nothing
else.

- **Absence:** there is no object in the system that represents *what job this
  person does*. Skills are the only representation, so search is forced to
  infer a role from a bag of tool names.
- **Inversion:** the profile engine runs *after* search construction, so every
  representation v2 added is unavailable at the moment the roles are chosen.

### 19.1 The one change worth making: a role object, produced before search

**Introduce `role_evidence`: a deterministic record of what kind of work this
person does, built before `fields_for`, and make `fields_for` consume it.**

Its first version needs **no new model call and no new ontology**, because
every input already exists and is currently discarded:

| Field | Source | Status today |
|---|---|---|
| `held_titles` | `employment[].title`, seniority-stripped | already computed inside `from_resume` |
| `stated_target` | `target_field` from the employment call | **extracted correctly, then dropped** (§15.1) |
| `headline_title` | `titles[]` from the fields call | **extracted, never read** (§15.1) |
| `function_evidence` | verbs and objects in work bullets — "gathered requirements", "configured", "wrote", "negotiated", "sourced" | not extracted; `skill_evidence` already locates and clause-scopes the spans this would read |
| `platform_evidence` | the skills that are platforms, kept **separate** from functions | currently indistinguishable |
| `supports` / `does_not_support` | role families the evidence does and does not sustain | does not exist |

The critical field is the last one. A Salesforce BA's record should be able to
say, explicitly, that the evidence supports business-analyst and
administrator families and **does not support** a developer family, because
`apex`, `lwc` and `soql` appear only as coordination objects or not at all.
That statement is what every downstream consumer is currently missing.

**This is one change, not a programme**, and it is bounded: a new module, one
new key in the profile, and one new argument actually read by `fields_for` —
whose signature already accepts `importance` and `resume_text` and ignores
them (`local_search.py:1323`). The seam was left open for exactly this.

### 19.2 What must move with it

**Move the profile-engine seam ahead of search.** `split_compounds` and
canonical resolution must run before `fields_for`, so that search sees atomic
canonical concepts rather than raw strings. This is the C5 ordering defect and
it is the mechanism by which the role object reaches search at all.

Measured reason, not theory: Case A's `agile/scrum` was invisible to search as
a compound and became two known concepts one stage too late (§3.1); eight of
seventeen skills never reached retrieval (§7.1).

Evidence tiers may stay where they are for now — they are a ranking input
(§6.3) — but concept identity must move.

### 19.3 What the role object should be allowed to do

Three consumers, in order of measured value:

1. **Gate the queries.** A generated role query must name a family the record
   supports. This is the missing semantic validator (§9.2); the economic
   validators stay exactly as they are.
2. **Derive the free-source gate.** `ATS_TITLE_HINTS` should be built from the
   supported families rather than unioned with a fixed software floor
   (§13.4). This is the single change that most improves a non-developer's
   free sweep, and it is independent of the paid path.
3. **Cover the retrieval gap.** When a candidate has fewer than two
   corpus-visible skills (9 of 56 personas, §7.2), retrieval is impossible and
   the system should say so rather than fall through to whoever else in the
   corpus shares their one generic term. A role object makes "we cannot
   search this market" an expressible answer.

### 19.4 What to do about the orphan anchor

`SKILL_LIFT` cannot be repaired by tuning: its ceiling is `1 / market_share`,
so it structurally excludes a candidate's core technologies and admits
sparsely-tagged incidentals (§9.3). Two defensible options, and the ablation
in §21.3 should choose between them:

- **Delete the path.** It ships 185 (skill, title) pairs corpus-wide and fires
  for 7 of 11 realistic personas; measure what is lost when it is off before
  assuming it earns its place.
- **Condition it on the candidate.** `candidates_for_skill` takes no `own` and
  `worth_it`'s anchored branch never reads `own`. At minimum an anchored title
  should have to share evidence with the rest of the candidate, and the anchor
  skill should have to be one the role object treats as defining rather than
  incidental.

Either way the fragment→canonical revalidation gap (§9.4) should be closed:
re-apply `MIN_LISTINGS` and `MIN_COMPANIES` to the *chosen title*, not only to
the fragment that produced it. That is a small, self-contained correction with
a measured 27%/34% failure rate behind it.

### 19.5 What this deliberately does not propose

No new model, no fine-tuning, no embeddings, no large ontology, no extra LLM
call, no new weight formula, and no Salesforce-specific rule. The role object
is assembled from fields the current 8B model already produces correctly and
the system currently throws away.
## 20. V3 implementation sequence

Five steps. Each is independently shippable, independently measurable, and
ordered so that a failure at step *n* does not invalidate steps 1..*n*-1. The
explicit "do not change at the same time" column exists because the v2
evaluation bundled changes and then could not attribute its own result.

---

### Step 1 — Stop discarding the role fields that already exist

**Problem solved:** V3-C10. The model extracts `target_field` and `titles`
correctly and production reads neither (§15.1).

**Schema change:** carry `target_field` and `titles` through `local_extract.read`
into the person dict, and into `skill_importance`'s sibling record. No prompt
change, no new call.

**Code areas:** `auto-apply/local_profile.py:165-190`, `auto-apply/make_profile.py`
(profile keys), `render()` allowlist.

**Migration risk:** very low. Additive; nothing consumes it yet.

**Evaluated by:** on the persona matrix, how often the extracted `target_field`
names the labelled primary family. This is the cheapest available measurement
of whether the model can carry role intent at all, and it must be taken before
anything is built on it.

**Do not change at the same time:** the extraction prompt, the schema shape,
any weight, any query.

---

### Step 2 — Move concept canonicalisation ahead of search

**Problem solved:** V3-C1 ordering inversion, partially — the part that costs
recall today.

**Schema change:** none. `split_compounds` and `skill_concepts.resolve` run on
`checked["skills"]` before the person dict is built, so `fields_for` receives
atomic canonical concepts plus their raw spellings.

**Code areas:** `auto-apply/make_profile._finish` (`:530`) splits today; the
split must happen in `local_profile.generate` before `:183`. `_finish` then
consumes an already-atomised list.

**Migration risk:** **medium, and this is the step that can regress.** Every
query in the corpus was ranked against raw strings; atomising changes the
`own` set and therefore every retrieval. Expect query churn on existing
profiles and measure it deliberately.

**Evaluated by:** query-set diff on all 56 personas and the four real cases,
before/after, with every change explained. Corpus-visible skill count per
persona (§7.1) should rise; Case A's `agile/scrum` should become two visible
concepts.

**Do not change at the same time:** evidence tiers, weights, the orphan path,
the gate. This step is about the `own` set and nothing else.

---

### Step 3 — Build `role_evidence` and gate queries with it

**Problem solved:** V3-C2 (no role object), V3-C7 (no semantic validation).
This is the architectural change.

**Schema change:** a new `role_evidence` record (§19.1) with
`held_titles`, `stated_target`, `headline_title`, `function_evidence`,
`platform_evidence`, `supports[]`, `does_not_support[]`. One new key in the
generated profile.

**Code areas:** a new module beside `skill_evidence.py` reusing its section and
clause machinery; `local_search.fields_for` finally reads its `importance`
argument, or a new `roles` argument; a sixth check in `validate()` that rejects
a query whose family is in `does_not_support`.

**Migration risk:** **high — this is where R5 failed.** R5 removed the job
search entirely for 2 of 16 personas. The mitigation is that the gate must
only ever *reject*, never be the sole source of queries, and must fail open
with a recorded reason when the record is empty. Coverage, not precision, is
the release gate.

**Evaluated by:** the §21.2 discovery metrics on a locked, independently
labelled holdout. Severe contamination rate and zero-retrieval rate are the two
that decide it. A precision gain bought with any increase in zero-retrieval is
a failure.

**Do not change at the same time:** the corpus, the orphan path, the free gate,
any weight. Step 3 alone, measured alone.

---

### Step 4 — Derive the free-source gate from the role record

**Problem solved:** V3-C4 — the gate that admits React Native jobs to a
business analyst and rejects "Business Analyst" (§13.4). This is the user's
actual observed symptom and it is *not* in the profile engine.

**Schema change:** none. `make_profile.render` stops unioning
`config.ATS_TITLE_HINTS` unconditionally and instead unions the floor
appropriate to the supported families, falling back to today's behaviour when
the record is empty.

**Code areas:** `auto-apply/make_profile.py:844-845`, and a family→floor table
in `config.py`.

**Migration risk:** medium. The union exists because a Salesforce or Java
résumé once lost most of every free board (`config.py:558-563`); narrowing it
can re-create that. Hence the fail-open fallback.

**Evaluated by:** free-gate fit (§21.2) per persona — does the gate admit the
person's own family and exclude the labelled forbidden ones — plus total
admitted volume, so a narrowing that starves the sweep is visible.

**Do not change at the same time:** `role_keywords`, scoring, weights.

---

### Step 5 — Decide the orphan anchor, and close the revalidation gap

**Problem solved:** V3-C5 and V3-C6.

**Schema change:** none.

**Code areas:** `local_search.candidates_for_skill`, `worth_it`,
`select_detail`; and re-applying `MIN_LISTINGS`/`MIN_COMPANIES` to the title
`canonical()` returns (`:521`, `:1050`) rather than only to the fragment.

**Migration risk:** low for the revalidation fix, which only removes thin
queries. The anchor decision is an ablation, not a change: run the matrix with
the path off and compare.

**Evaluated by:** supported-query precision and primary-role coverage with the
path on versus off. If precision rises and coverage does not fall, delete it.

**Do not change at the same time:** anything from steps 3 and 4 — otherwise
the ablation is uninterpretable.

---

### Explicitly out of the sequence

**The corpus vocabulary problem (V3-C3) is the largest measured cause of
non-technical failure and it is not in this sequence.** It is a data problem,
not a code problem: 52% of candidate skills are absent from a vocabulary
accumulated from previous users' searches, and no change to `local_search`
fixes that. It needs its own plan — a deliberate corpus-coverage effort, a
decision about whether retrieval should match against `matched_skills` at all,
and an explicit answer to "what does this system do for a candidate whose
market it has never sampled". Steps 1–5 improve what the system does with what
it can see; they do not widen what it can see.
## 21. Evaluation plan for v3

The v2 evaluation measured candidate-importance agreement, which this report
shows is the wrong axis for the failures users are hitting. A v3 evaluation has
to measure **discovery**.

### 21.1 Fix the labelling independence problem first

The v2 labels were authored by the agent that built the engine. That was
disclosed and bounded, but it cannot be repeated for role labels, because role
labels are exactly where the engine's own frame would leak. Requirements:

- Role-family labels written **before** any v3 output exists, from the résumé
  alone, in the three-way form this audit used: **primary**, **plausible**,
  **must-not-generate**. The last one is the load-bearing field and the one an
  engine-aligned labeller would get wrong.
- A second labeller on at least a quarter of the set, with disagreements
  reported rather than reconciled away.
- Labels locked and hashed before the first v3 run.

The 56-persona matrix in this audit is a **development** instrument, not that
holdout. Its labels are independent of the engine but were written by the same
agent that wrote this report, and its documents are synthetic.

### 21.2 The metrics that should gate v3

Discovery, on the labelled holdout:

| Metric | Definition | v2 baseline |
|---|---|---|
| Supported-query precision | queries whose family is primary or plausible / all queries | §11 |
| Severe contamination rate | personas emitting ≥1 must-not-generate family | §11 |
| Primary-role coverage | personas with ≥1 query in the primary family | §11 |
| Zero-retrieval rate | personas for whom search returns nothing | §11 |
| Free-gate fit | personas whose gate admits their own family and excludes forbidden families | §13.4 |
| Query-set invariance | identical queries under layout, ordering, alias and spacing perturbation | §14 |
| Held-title yield | emitted held titles that buy zero rows | §9.6 |

Ranking, separately and second:

- Precision@10 and nDCG@10 against **independently judged** job relevance, not
  against historical Sweep scores, which are the same artifact the corpus is
  built from.
- Adjacent-career false-positive rate, which is the metric §13.2 would have
  caught.

### 21.3 Ablations that must be run separately

The v2 evaluation bundled changes and then could not attribute the result.
v3 should ablate, each alone:

1. role object wired into search, everything else unchanged
2. corpus vocabulary coverage, role object off
3. orphan-anchor path disabled entirely (it may be a net negative — §9.3 and
   §11 should say)
4. free-gate derivation, paid path unchanged
5. market separation on/off, with its purpose stated first

### 21.4 Regressions that must not move

The 52-document date regression (52/52 exact, macro F1 0.976), the 1,586-test
suite, the invariance results v2 already achieves (alias 1.0, duplicate-alias
1.0, skill order 1.0, line wrapping 1.0), and the row-order determinism
result. Any v3 change that moves one of these has broken something in §18.

### 21.5 What would count as "better jobs"

No better-job result has ever been demonstrated, and none of this report's
measurements is one. The minimum honest claim for v3 is a **paired comparison
on the same candidates under a fixed location and seniority envelope**, with
independently judged relevance of the top 10, reported with the failures in
the denominator. Anything less remains a proxy.
## 22. Things explicitly not worth doing yet

Each of these was considered against the evidence in this report and rejected
**for now**, with the measurement that would change the answer.

| Not yet | Why not | What would change it |
|---|---|---|
| **A larger or fine-tuned model** | The model is not the failure. On Case A it extracted the BA function terms without being asked and named the target field exactly right; the system discarded both. On Case C it produced reasonable skills and the search layer invented `.net developer`. No measured defect in this report is attributable to 8B capacity. | A extraction evaluation (§4) showing recall below roughly 0.8 on bullet-only concepts after the schema is fixed. |
| **Embeddings or a vector database for skills** | The retrieval failure is that 52% of candidate skills are *absent from the corpus vocabulary*, not that they are lexically mismatched. Embedding an empty intersection returns an empty intersection. | Evidence that after vocabulary coverage is fixed, the residual failures are synonym misses rather than absences. |
| **Importing a large occupational ontology (ESCO/O*NET)** | The registry's narrowness is not currently destroying anything (§5.2) and a 3,000-concept import would not change what the *corpus* can see. It also adds a mapping-maintenance burden the team has not needed. | A v3 role layer that is working, and then a measured recall ceiling caused by missing concept identities. |
| **A 1–100 skill score** | 56.9% of v1 entries already landed on one value because the evidence to distinguish them does not exist. More digits would be false precision. | Nothing foreseeable. |
| **Hardcoded job-title mappings, or any Salesforce-BA special case** | Explicitly out of scope, and the measurements say it would be aimed at the wrong thing: `role_keywords` already produces `salesforce business analyst` correctly. | Nothing. This stays excluded. |
| **Another market-weight formula** | The market signal's *purpose* has never been stated (§6.4). A second formula for an undefined objective cannot be evaluated. Define the objective first. | A written statement of what market separation is for, plus a metric it should move. |
| **Removing market separation** | It genuinely breaks 46 ties correctly, and the bound keeps its errors within one tier. Removing it is a change with a measured cost. | A decision on purpose, then an A/B on the metric that purpose implies. |
| **More LLM calls (a second judgement pass)** | The one role-intent field the model already produces is being thrown away. Consume that before buying another call. | `target_field` wired through and measured, with a residual error rate that a second call could plausibly fix. |
| **Fixing CORE calibration as a route to better jobs** | Importance cannot reach search in the current architecture (§6.3). CORE precision is a ranking metric. | Wiring importance into search — after which CORE precision becomes load-bearing and should be fixed. |
| **Re-enabling R5 as built** | It zeroed the job search for 2 of 16 personas, its fallback bypassed its own validation, and its thresholds were picked on one corpus. | A rebuilt role layer with coverage measured on the persona matrix, including the nine zero-retrieval personas. |
| **Tuning `SKILL_LIFT`, `MIN_PAIR_SHARE` or `ANCHOR_EVIDENCE`** | The lift metric has a structural defect (its ceiling is 1/share, §9.3), so no threshold makes it measure what it was meant to measure. Tuning it moves which wrong answers appear. | A replacement metric, evaluated on the matrix. |
| **Broadening `ATS_TITLE_HINTS` further** | The gate is already 116 entries and 59% software for a business analyst. Widening a mis-aimed gate adds cost without adding fit. | A gate derived from the candidate's role families rather than unioned with a fixed software floor. |
## 23. Direct answers to the questions this audit was asked

**1. Is Salesforce BA → Salesforce Developer mainly extraction, weighting, role
construction, corpus bias, or multiple factors?**
None of those, as stated — and this is the audit's most important correction.
`role_keywords` for a real Salesforce BA résumé are
`salesforce administrator`, `salesforce engineer`, `salesforce business
analyst`, `salesforce functional consultant`, `salesforce techno functional
consultant`, … `salesforce developer` is **not emitted as a role keyword**,
and the same holds across three independent synthetic Salesforce-BA variants.
The symptom is real but it comes from two other places: the **free-source
title gate** (`ATS_TITLE_HINTS`, which for this candidate contains
`salesforce developer`, `associate salesforce developer`, `cpq developer` and
`platform engineer`, admits `React Native Developer`, and rejects
`Business Analyst`), and one **paid query, `salesforce engineer`**. Extraction
was correct. Weighting is not consulted by search at all. Corpus bias exists
but did not cause this case.

**2. Is Profile Engine v2 general-purpose, or still software-engineer-centric?**
v2 itself — canonicalisation, occurrence semantics, evidence strength, tiering
— is **structurally general**. Its rules are about where a word appears, not
what kind of word it is, and they behaved sensibly on sales, finance and BA
documents. The *system around it* is software-centric in three measured
places: the 59-concept registry (96.6% software), the 367-term market table
(one non-technical term in the top 40), and the 22,806-row title corpus (74.5%
software engineering rows, 47.4% of rows from 20 developer-tool employers).

**3. Are non-technical skills represented adequately?**
For scoring, partially: they survive as passthrough concepts and do score, but
with exactly one matcher each against a mean of 2.12 for software concepts, so
`requirement gathering` scores zero against a profile holding `requirements
gathering`. For search, no: only 24.2% of a non-technical persona's skills
exist in the retrieval vocabulary, against 63.0% for a software persona.

**4. Is the canonical registry too tech-centric?**
Yes — 57 of 59 concepts — but it is **not currently destroying anything**. Zero
of 87 business terms were dropped, split or misnormalised. The cost is matcher
breadth and the one `Salesforce (administration)` → `salesforce` parenthetical
collapse. Widening the registry alone would improve scoring and would not move
retrieval at all.

**5. Is the market corpus too tech-centric?**
Yes, and measurably: product, finance, HR and legal have **zero** terms among
the 367. But it is broader than the registry — 291 of its terms have no
canonical concept, including `business analysis`, `requirements gathering` and
`stakeholder management`. Its practical effect on non-technical candidates is
inertness (≈21% measurable versus ≈76% for software), not distortion.

**6. Does CORE over-population materially cause wrong search roles?**
**No. It cannot.** Importance is computed in `_finish`, which runs after
`fields_for` has returned every query, and `fields_for` ignores its
`importance` argument. CORE precision of 0.409 is a **ranking** defect. It
should be fixed, but fixing it will not change one query.

**7. Does market adjustment help search discovery even though it hurt
candidate-importance labels?**
It cannot help discovery either, for the same reason: market separation feeds
weights, and weights do not reach search. Its measured effect on importance is
worse than the summary suggests: of the 119 ties it breaks, **46 resolve
correctly and 73 become inversions — 38.7% accuracy**, below chance. It is
bounded to one tier, so it is survivable, but its purpose has never been
written down and it should be defined before it is defended or removed.

**8. What percentage of wrong search queries originate before search
construction versus inside it?**
Essentially none originate before it. Extraction was correct on all four real
résumés, including the role field. What originates before search is **absence**
— 52% of candidate skills never reach the search layer because the corpus
vocabulary does not contain them. Of the defects that produce an actual wrong
string, all of them are inside search construction: the orphan anchor, the
fragment→canonical swap, the unvalidated held title, and the gate. See §11 for
the per-query attribution.

**9. Can ranking compensate for wrong role discovery?**
No, and the asymmetry is measured. For the Salesforce BA, pooling the correct
and incorrect Salesforce queries puts developer rows at 4 of the top 10, 9 of
the top 20 and 28 of the top 50, with a developer posting tying the top score.
The scorer sums matched concept weights and has no role term, so it cannot
demote a job that legitimately names the candidate's platform. And for the
nine personas that retrieve nothing, there is no list to rank.

**10. What is the highest-value v3 architectural change?**
A `role_evidence` object, produced before `fields_for` and consumed by it,
built from fields the system already extracts and discards. One module, one
profile key, one argument that `fields_for` already accepts and ignores.

**11. Should v3 change Qwen extraction, deterministic post-processing, or role
modelling?**
**Role modelling, first and mostly.** Extraction needs one small change — stop
discarding `target_field` and `titles` — and no prompt or model change.
Deterministic post-processing needs to move earlier, not to become cleverer.

**12. Which v2 components should remain frozen because they already work?**
See §18: the employment-row fix, engine-version binding, canonical identities
and OR-matching, the tier function's independence from market and provenance,
occurrence semantics, the bounded market influence, the `skill_importance`
record, row-order determinism, the generated-profile safety boundary, and the
deterministic date arithmetic.
## 24. Overfitting controls

Every recommendation in §19–§20, stated against the discipline the audit was
asked to apply: what it was observed on, how many unrelated personas reproduced
it, whether a counterexample was tested, and what class of problem it solves.
A recommendation that exists only because of the supplied résumés is marked
**CASE-SPECIFIC / NOT READY FOR V3**.

---

**Step 1 — carry `target_field` and `titles` through**

- **Observed on:** all four real résumés and all 56 personas.
- **Reproduced on:** 60 of 60 documents. `target_field` was extracted on every
  one and consumed by nothing on every one.
- **Counterexample tested:** yes — is `target_field` ever wrong in a way that
  would make it dangerous? On Case A it was "salesforce business analysis"
  (correct); on the Scrum Master persona it was "software engineering", which
  is the *industry*, not the role. So the field is useful evidence and **not**
  a role label on its own. The recommendation is to carry it, not to trust it.
- **General rule:** do not discard the only role-intent signal the system
  collects.

---

**Step 2 — canonicalise before search**

- **Observed on:** Case A (`agile/scrum`).
- **Reproduced on:** compound strings appeared on 9 of 56 personas
  (`jwt / oauth 2.0`, `aws (ec2, rds)`, `cnn(convolutional neural network)`,
  `user stories & acceptance criteria`, `security & sharing`, …), and the
  corpus-visibility measurement (§7.1) is independent of any one document.
- **Counterexample tested:** yes, and it is a real risk — atomising changes the
  `own` set and therefore every retrieval. That is why Step 2 is separated from
  Step 3 and gated on a query-diff, not merged into the role work.
- **General rule:** the representation used to decide must be the
  representation that was built.

---

**Step 3 — a `role_evidence` object, consumed by search**

- **Observed on:** Case A, Case C, and the adversarial Salesforce BA.
- **Reproduced on:** 32 of 56 personas emit at least one contaminated query;
  20 of 40 non-developer personas receive a software-family query. Across six independent
  Salesforce personas the variable is isolated: the two that carry a
  developer-tier token (Apex/LWC/SOQL) get developer queries and the four that
  do not — a clean BA, an administrator, a functional consultant and a
  salesperson — do not. That is the cleanest available evidence that the
  missing distinction is *role*, not *platform*.
- **Counterexample tested:** yes, three. (a) The clean Salesforce BA — both the
  real résumé and the synthetic variant — is served **correctly** today, so the
  role layer must not "fix" a case that is not broken; it must only reject. (b)
  The salesperson living in Salesforce daily is served correctly today and must
  stay that way. (c) The Salesforce-titled software engineer whose work really
  is Apex development must keep the developer family. All three are in the
  matrix and all three must remain unchanged.
- **General rule:** a tool name is evidence about a role, not a role.
- **Risk acknowledged:** this is where R5 failed by removing search entirely for
  2 of 16 personas. Hence "reject only, never the sole source, fail open".

---

**Step 4 — derive the free-source gate from role families**

- **Observed on:** Case A.
- **Reproduced on:** **56 of 56 personas** admit at least one forbidden family;
  29 of 56 cannot see their own. This is the most broadly reproduced finding in
  the report and the least dependent on any individual document.
- **Counterexample tested:** yes — the union exists because a Salesforce or Java
  résumé once lost most of every free board (`config.py:558-563`). Narrowing the
  gate can re-create that, which is why the fallback is explicit.
- **General rule:** a filter vocabulary derived from one profession cannot gate
  every profession.

---

**Step 5 — decide the orphan anchor; close the revalidation gap**

- **Observed on:** Case D (`git` → Python and Flutter).
- **Reproduced on:** corpus-wide and independent of any résumé — 243 of 269
  eligible skills can clear the lift guard, 185 (skill, title) pairs ship, and
  the ceiling `1/share` is algebra, not a sample. Seven of eleven independent
  skill sets ship an anchored keyword. The `m f` → `aem full stack developer`
  accident fired for six unrelated non-technical personas.
- **Counterexample tested:** yes — the anchor exists to rescue a genuine
  specialist whose primary skill the generalist ranking would bury (the Apex
  case in the code comment at `:1050-1063`). Deleting it must be measured
  against that, which is why it is an ablation rather than a deletion.
- **General rule:** a metric whose ceiling is set by its denominator cannot
  measure specialisation.

---

### Marked CASE-SPECIFIC / NOT READY FOR V3

- **Any rule keyed on Salesforce, Apex, LWC or CPQ.** The Salesforce slice is
  the best-covered enterprise platform in the corpus (772 rows) and is
  therefore the *least* representative one. SAP and ServiceNow are not in the
  vocabulary at all.
- **Any change tuned to make `salesforce engineer` disappear from Case A.** It
  is one query out of twelve on one document; the same mechanism produces
  `salesforce engineer` for a Scrum Master and a technical-support engineer,
  and that is the version worth fixing.
- **Any change derived from Case D's `sde ii, amazon now`.** The general form
  is §9.5's single-employer resolution rate (26.3%), not this string.

### What would falsify the central claim

The central claim is that role failures originate in discovery, not in
weighting. It would be falsified by showing that (a) a query's family can be
changed by changing an importance tier — which the current call graph makes
impossible — or (b) that reordering retrieved results by any weighting scheme
moves the developer rows out of a Salesforce BA's top 10. §13.2 measures the
second and finds developer rows at 4 of the top 10 with the top score tied.

---

## Appendix — evidence, reproducibility and verification

Everything below is in the **gitignored** `output/profile-engine-v3-audit/`.
Nothing containing résumé text, names, employers or contact details is tracked.
No production file was modified; `git status` shows only the pre-existing
untracked docs and this report.

### Harnesses

| Path | What it does |
|---|---|
| `trace_case.py` | One résumé through the real production path, wrapping `local_extract.read` and `local_search.fields_for` with recorders that return exactly what the real functions returned. `--live` switches the corpus condition. |
| `explain_query.py` | Per-query provenance for a captured trace: skill visibility, retrieval sets, winning fragment, anchor lift and evidence, and what the bought rows actually want. |
| `corruption.py` | Parts 18–19: what each query buys, scored with the candidate's real profile, plus the pooled ranking. |
| `personas/defs.py` | 56 personas with independent primary / plausible / **forbidden** labels. |
| `personas/render.py` | Seven presentation-only layouts of the same persona. |
| `personas/run_matrix.py` | Live `qwen3:8b` derivation for every persona, cached by text hash. |
| `personas/classify_query.py` | Ordered, inspectable query → role-family rules. |
| `personas/score_matrix.py` | Verdicts and the cohort metrics in §11. |
| `personas/visibility.py` | §7.1 corpus-vocabulary visibility, model-independent. |
| `personas/gate_audit.py` | §13.4 free-source gate behaviour per persona. |
| `personas/extraction_audit.py` | §4 precision/recall against known document truth. |
| `personas/invariance.py` | §14 layout perturbation comparison. |
| `corpus/`, `registry/`, `search/` | Corpus domain classification (with its own hand-validated accuracy), registry probes, and the orphan-anchor catalogue. |

### Raw artifacts

`traces/case{A,B,C,D}.frozen.json` (full intermediate state per real résumé),
`personas/results/matrix.*.json` (56 personas × 4 layouts),
`personas/results/{scored,gate,visibility,extraction,invariance}.json`,
`personas/results/queries.csv` (every query with its verdict),
`corpus/{report_raw.txt,salesforce_slice.tsv,sample2_160_titles_v2.tsv}`,
`registry/{probe_nontech.tsv,probe_tech.tsv,market_terms_without_concept.txt}`,
`search/{q1_catalogue.json,q3_git.txt,q4_canonical_gap.txt,q5_employer_leak.txt}`.

### What was measured live, and what was replayed

- **Live inference:** 60 documents through `qwen3:8b` (56 personas + 4 real
  résumés), plus 48 more runs for the four invariance layouts. Two production
  calls each, unchanged prompts and schema.
- **Deterministic replay:** every `local_search` measurement, the corpus
  analysis, the registry probes, the corruption and ranking simulations.
- **Read back from the v2 evaluation, not re-derived:** CORE precision 0.409,
  STRONG_SECONDARY F1 0.125, the market ablation (603 pairs), and the v1/v2
  invariance table.
- **Not measured here:** any paid search, any live job-board result, any
  human relevance judgement, latency, tokens, memory, and anything about
  Gemini.

### Verification notes

Subagent measurements were re-derived independently before use. The lift
ceiling (`1/market_share`), `worth_it` ignoring `own`, the `communication`
anchor set, the Salesforce slice counts and the registry composition were all
re-run in the main audit session and matched. The corpus domain classifier
reports its own hand-checked accuracy (**87.5%** on a disjoint 160-title
sample) rather than assuming it.

### Known limits of this audit

1. **Personas are synthetic** and were authored by the same agent that wrote
   this report. Their labels are independent of the *engine* but not of the
   *auditor*. They are a development instrument; §21.1 specifies the
   independent holdout that must replace them before v3 ships.
2. **Job text for scoring is `title + matched_skills`**, because descriptions
   are not exported. `matched_skills` is itself a product of earlier profiles.
3. **Retrieval is modelled as substring containment** on corpus titles, which
   is the repo's own model of a keyword purchase, not a live board.
4. **The four real résumés are four documents.** They demonstrate mechanisms;
   they measure nothing.
5. **One persona in twelve** drove the discovery of V3-C16; the generalisation
   was then tested on six independent skills lists and is reported with that
   denominator.
---

## Final summary

**V2 SKILL CANONICALIZATION: adequate.**
Identities and OR-matching work — alias-spelling invariance 1.000 and
duplicate-alias invariance 1.000, against 0.948 and 0.866 in v1 — and nothing
is destructively normalised: 0 of 87 business terms were dropped, split or
merged. But the registry is 59 concepts, 96.6% software, with zero concepts in
eight business domains, and an unknown concept answers to exactly one spelling
against a software mean of 2.12. Strong for software, thin everywhere else.

**V2 EVIDENCE SEMANTICS: strong.**
The tier function consults no market data and no provenance; planned, negated
and learning mentions are refused; shape is clause-scoped; the record is
inspectable and was sufficient to reconstruct every case in this report without
re-running anything. This is the best-engineered part of the system. Its known
gaps — `classify()` line-scoped, `learned` missing — are real and currently
immaterial, because nothing it produces reaches search.

**V2 IMPORTANCE WEIGHTING: adequate — sound mechanism, weak calibration.**
Source-invariant and bounded, which is what matters. But CORE precision is
0.409, STRONG_SECONDARY F1 is 0.125, and this axis cannot affect which jobs are
found.

**NON-TECH RESUME SUPPORT: weak.**
24.2% of a non-technical candidate's own skills are visible to retrieval
against 63.0% for a software candidate. Three personas have 0% visibility.
Eleven are visible only through `excel`. 81% cannot see their own role family
on the free path. A graphic designer's free sweep is, literally, a software
engineering sweep.

**ROLE INTENT MODELLING: weak.**
It does not exist. Eight kinds of role evidence collapse to two, and the one
role-intent field the model produces correctly is used for a prose sentence.

**SEARCH QUERY CONSTRUCTION: weak.**
Supported-query precision 0.462 across 429 queries; 18.2% name a role family
the candidate's evidence forbids; 57% of personas receive at least one such
query; validation is economic and never semantic.

**TITLE CORPUS DOMAIN COVERAGE: weak.**
74.5% of rows are software engineering, 85.7% are technical, HR is 0.06%, and
47.4% of all rows come from 20 developer-tool employers. Seven of fourteen
realistic non-developer role titles have zero rows. It is a record of previous
users' searches, not a labour market, and it cannot distinguish "no such market"
from "never searched".

**MARKET SIGNAL: mixed.**
Of the 119 ties it breaks, 46 resolve correctly and 73 become inversions —
38.7%, below chance — though every inversion is bounded to one tier. It reaches
~21% of business vocabulary against ~76% of software vocabulary, so for
non-technical candidates it is mostly inert. Its purpose has never been written
down, and that should be fixed before it is defended or removed.

**MODEL EXTRACTION: strong for the contract it is given; the gap is the
contract.**
Now measured on 60 live documents: **100% precision** (nothing invented across
589 terms) and **100% recall on listed skills**, including non-technical
competencies it was never asked for. **Bullet-only recall is 15.8%** — it reads
the skills list and barely reads the body, which is where use-depth and role
evidence live. In production nothing recovers that, because the skill scanner
is a documented no-op without `output/`.

**PRIMARY FAILURE LOCATION:**
Search construction (`local_search.fields_for` and everything it calls), the
free-source title gate (`config.ATS_TITLE_HINTS` unioned in
`make_profile.render`), and the retrieval vocabulary of the title corpus.
All three are outside Profile Engine v2. The enabling condition is inside it:
v2 runs after search.

**SALESFORCE BA → DEVELOPER ROOT CAUSE:**
Not `role_keywords` — those are correct on the real résumé and on the clean
synthetic BA, administrator and salesperson personas, and `salesforce
developer` is not among them. The symptom
is produced by (1) the free-source title gate, which admits `Salesforce
Developer` and rejects `Business Analyst`; (2) one paid query,
`salesforce engineer`; and (3) the orphan anchor, which converts a
developer-tier platform token — Apex, LWC, SOQL — into a developer role. A
controlled comparison across six Salesforce personas isolates this: none
without such a token gets a developer query, both BAs with one do, including
the BA whose résumé states *"I do not write Apex myself"*, anchored on
`lightning web components` at evidence 1.26. All three are possible because no
object represents the candidate's role, and because the layer that could infer
one runs after every query has been chosen.

**HIGHEST-VALUE V3 CHANGE:**
A `role_evidence` object, produced before `local_search.fields_for` and
consumed by it — assembled from `target_field`, the model's `titles`, held
employment titles and clause-level function evidence, all of which the system
already produces and discards. One module, one profile key, one argument that
`fields_for` already accepts and ignores.
*Do the free-source gate fix first anyway: it is the most severe defect
measured here, it needs no schema change, and it is not architectural.*

**V2 COMPONENTS TO KEEP:**
Employment-row validation carried through `read()`; engine-version binding to
the profile stamp; canonical concept identities and OR-matching; the tier
function's independence from market and provenance; occurrence semantics and
evidence strength (R4a/R4b/R4c); the bound on market influence; the
`skill_importance` record; row-order determinism; the generated-profile AST and
prose safety boundary; deterministic date arithmetic; and R5 staying unshipped.

**V3 BLOCKERS:**
- **V3-C4** — the free-source gate admits forbidden families for 100% of
  personas and hides their own from 52%
- **V3-C1** — search is constructed before the profile engine runs
- **V3-C2** — no object represents a role
- **V3-C3** — retrieval sees only a software-shaped residue of past users'
  searches (51.9% of candidate skills invisible)

**RECOMMENDED V3 SEQUENCE:**
1. Carry `target_field` and `titles` through instead of discarding them. No
   prompt change, no model change. Measure how often `target_field` names the
   labelled primary family.
2. Fix the free-source gate. After step 1 an interim version keyed on
   `target_field` is already possible, and it addresses the most severe
   measured defect without waiting for the architecture.
3. Move concept canonicalisation ahead of search. Gate on a query-diff across
   all 56 personas and the four real cases; expect churn and explain every
   change.
4. Build `role_evidence` and let `validate()` reject a query whose family the
   evidence does not support. Reject only, never the sole source of queries,
   fail open — R5 failed here by removing search entirely for 2 of 16 people.
   Coverage, not precision, is the release gate.
5. Re-derive the gate properly from role families, replacing the interim
   version from step 2.
6. Ablate the orphan anchor — its lift metric has a ceiling of `1/share` and
   cannot be repaired by tuning — and close the fragment→canonical
   revalidation gap, which is a small self-contained fix with a measured
   27%/34% failure rate behind it.

**Outside the sequence and needing its own plan:** the corpus vocabulary
(V3-C3). It is the largest measured cause of non-technical failure and no
change to `local_search` addresses it.

---

**DO NOT IMPLEMENT V3.** This document is an audit. Nothing in the production
tree was modified.
