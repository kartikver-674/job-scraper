# P0 Handoff: "Machine Learning" no longer reads as learning or coursework

| | |
|---|---|
| Checkpoint | Standalone P0 fix to `skill_evidence` classification on the ordinary single-résumé v2 path |
| Branch | `fix/skill-evidence-machine-learning`, a worktree created from `origin/main` = `d1c44bca2fbde6e49b5aa9e91fe5acdae02d1d0e` (verified with `git ls-remote` before starting); no upstream |
| Production files changed | **`skill_evidence.py` only**: the `_LEARNING` pattern (a narrowed `learning` + a course-taken cue) and its comments |
| Tests | `auto-apply/tests/test_evidence_semantics.py`: two classes added (21 tests); no existing test changed |
| Review | **APPROVED** (round 2), including the accepted widening "Completed a React course." → learning and the four accepted known limitations (§O) |
| Commit / push / deploy | committed locally as `fix(profile): distinguish learning domains from coursework` (these 3 files; parent `d1c44bc`); **not pushed, not deployed**. Release shape: this commit → the separate `_HEADINGS` P0 commit → combined regression → one production rollout |
| Heading bug | **separate**: a P0 commit of its own, immediately after this one; not touched here (§O.2) |
| Model / provider / network calls | **none**. Every derivation stubs the two model calls from committed captures; sockets are refused |
| Multi-Track | untouched: `feat/multi-track-search` @ `589a05b`, clean, 2 ahead / 0 behind |

## Review correction (round 2): an explicit course is still learning

**The regression the review found.** The first candidate turned `Completed a machine learning course.` from LEARNING into MENTIONED.

**Why.** On `d1c44bc` that sentence was LEARNING **only** because of the word "learning" inside the technology name. The first candidate rightly stopped reading that word, and nothing else in the sentence was a cue: **bare "course" is not a pedagogical cue anywhere** in `skill_evidence`.

**Existing pedagogical mechanisms (audit, `d1c44bc`):**

| Word | In `_LEARNING` (prose) | As a section heading | Elsewhere |
|---|---|---|---|
| course | **only `course on` / `course in`** | "Course(s)" → CERTIFICATION | — |
| coursework | yes | "Coursework", "Relevant / Academic / Related Coursework" → EDUCATION | — |
| training | **no** | "Training" → CERTIFICATION | `_ACTION` verb (`trained`, `training`) = evidence of **use** |
| studying | yes | "Studying", "Currently studying" → EDUCATION | also an `_OTHER_VERB` (ends a verb's reach) |
| learning | yes (bare; now the verb only) | "Learning", "Currently learning" → EDUCATION | — |
| certification | no | "Certification(s)", "Licence(s)" → CERTIFICATION | — |
| specialization / specialisation | **no** | **no** | only the bench credential "Deep Learning Specialisation", listed under **Certifications** (2 personas) |
| bootcamp | yes | — | — |
| tutorial | yes | — | — |
| self-study | **no** (only `self[\s-]?taught`) | — | — |
| enrolled, working through, reading about, familiarise, getting up to speed, beginner, exploring in my own time | yes | — | — |

**The fix, round 2: a course the candidate TOOK is the cue**, added to `_LEARNING` in `skill_evidence.py`:

- **Taken:** "course(s)" after a verb of taking it (completed, took, finished, attended, passed, audited, or taking/take…) in the same sentence, within 60 characters and no sentence end between.
- **Dated:** "course(s)" followed by a year (`course, 2025`, `course (2025)`).
- **Either way it must end its noun phrase:** followed by punctuation, a preposition (on, in, at, from, by, with, through, via, about, for) or the date. So "the course catalogue service" and "the course migration", which an e-learning engineer *builds*, are never read as study.

The field name stays a plain claim; the independent course language decides.

**Decisions:**

- **`Deep Learning Specialization, 2024.` → A + B.** It is **A, educational under an existing structural cue** when it sits under a Certifications/Courses/Training/Education heading. That is where the bench's own "Deep Learning Specialisation" is, and it is LEARNING there. Free-standing in a summary it is **B, ambiguous**, and stays an ordinary claim: "specialization" also means a focus area ("Specialization: distributed systems"). No `specialization` cue was added. A credential-name rule would be a **C**, separate future certification rule.
- **Clause-initial `Learning X` nouns** ("Learning Management Systems", "Learning & Development", "Learning to Rank") are a genuine ambiguity. They have the verb's shape ("Learning React") and still fire, **exactly as on `d1c44bc`** (pinned as KNOWN AMBIGUITY). The occurrence-overlap alternative that would resolve some of them is assessed in §D, and deferred.
- **The heading bug is separate**, and so is the wrapped `Machine⏎Learning` case it causes. Both are unchanged and pinned (§O.1–2).

---

## A. Bug reproduction on `d1c44bc`

Deterministic text; `skill_evidence.sections`, `_sentence` and `classify` per occurrence, on a pristine `git archive d1c44bc`:

| Case (section) | Text | Every occurrence on `d1c44bc` | The "sentence" classify searched |
|---|---|---|---|
| flat lines (skills) | `Python⏎Machine Learning⏎FastAPI⏎PostgreSQL` | **all LEARNING_OR_COURSEWORK** | the whole section: `Python⏎Machine Learning⏎FastAPI⏎PostgreSQL` |
| comma list (skills) | `Python, Machine Learning, FastAPI, PostgreSQL, Docker` | **all LEARNING** | the whole line |
| labelled group, inline (skills) | `Machine Learning: PyTorch, scikit-learn, MLflow⏎Languages: Python, SQL` | **all LEARNING**, including Python and SQL on the next line | both lines |
| labelled group, label on its own line | `Machine Learning:⏎PyTorch, scikit-learn, MLflow` | MENTIONED (correct, by luck: the trailing colon ends the sentence) | from the line after the label |
| Deep Learning / Reinforcement Learning lists | `Python, Deep Learning, TensorFlow, Docker` … | **all LEARNING** | the whole line |
| **work bullet** | `- Built machine learning models using Python.` | **machine learning AND python: LEARNING** | the bullet |
| controls | `- Currently learning Rust …`, `- Learning Kubernetes`, `In 2024 I was learning React, …`, `Studying AWS …`, `Coursework in Machine Learning …`, a `RELEVANT COURSEWORK` section | LEARNING (correct) | — |

**Effect on tier and weight** (same text through `make_profile.generate_local` → `_finish` → `render()`, model answers stubbed):

- A skills-only claim that should be SUPPORTING lands on **BACKGROUND**, one weight lower (e.g. Python 2 → 1, FastAPI 3 → 2).
- The work bullet costs **CORE**: machine learning is weight 1 instead of 4, and Python 2 instead of 4.

**It is worse than DQ2b saw.** It is not limited to skills sections: any sentence containing "Machine/Deep/Reinforcement/Transfer Learning" (a work bullet, a summary, "Machine learning engineer shipping models with PyTorch.") marks **every** skill in that sentence as coursework. The only thing that bounds it is the sentence, and a skills section has no sentence end.

## B. Root cause

FACT, `skill_evidence.py` at `d1c44bc`:

1. `_LEARNING` (line 327) begins `\b(learning|studying|…`: a **bare `learning`**.
2. `classify()` fires `LEARNING` if `_LEARNING` matches **anywhere** in the occurrence's sentence, `(opened, closed)` from `_sentence()`. It checks after NEGATED and PLANNED, and before USED.
3. `_sentence()` ends a sentence at `.`, `;`, `!`, `?` or a **hard** newline. `_soft_newlines()` treats a newline as a soft wrap when the line before doesn't end in punctuation and the next line isn't a bullet or heading. **Skills lines never end in punctuation**, so a skills section is **one sentence**.
4. "Machine Learning" contains the word "learning". The concept's own span therefore supplies the match that condemns every concept in the section, itself included.

V3 Step 8's scoped path (`_scoped`, behind `SWEEP_SEMANTIC_SCOPE`, off in production) reads the same regex. Its comma rule protects comma lists, but not line lists or work sentences.

## C. Existing `_LEARNING` behaviour and what the bare word was for

Every existing test and fixture that exercises learning wording (repository search at `d1c44bc`):

| Where | Case | Mechanism |
|---|---|---|
| `test_evidence_semantics.test_16` | `- Currently learning Rust in my own time.` | `_LEARNING` "learning", **after an adverb** |
| `test_evidence_semantics.test_17` | `- Working through a course on Scala.` | `working through`, `course on` |
| `test_18` / `test_19` | Relevant / Academic Coursework heading | **section** (EDUCATION), not the regex |
| `test_20` | Currently Learning heading | **section** (EDUCATION) |
| `test_semantic_scope` (§ a–c, g, coordination) | `Learning React and TypeScript`, `Learning React, built Node APIs`, `Learning React, TypeScript and Next.js`, `In 2024 I was learning React, …`, `- Learning React⏎- Built Node APIs`, `Learning Python. Built Python services.`, lower- and upper-case variants | "learning" **clause-initial**, or **after an auxiliary** ("was") |
| `semantic_scope.demo` | `learning react, typescript and next.js` | clause-initial |
| `test_role_evidence` | bootcamp coursework | `bootcamp`, `coursework` |

**Conclusion:** the bare `learning` exists for the **verb/gerund**, "(am/was/currently) learning X", or a clause opening with "Learning X". A section *called* "Learning" or "Currently Learning" is recognised by `_HEADINGS`, not by `_LEARNING`. Nothing anywhere relied on "learning" as the head of a noun.

## D. Options considered

| Option | What | Verdict |
|---|---|---|
| **A**: remove bare `learning`, add explicit phrases ("currently learning", "am learning", …) | Every field noun is safe | **Rejected alone.** It loses the clause-initial verb ("Learning React and TypeScript", "- Learning Kubernetes"), which the tests pin, unless "clause-initial" is expressed anyway. That is option B |
| **B**: context-aware bare `learning` | "learning" counts only when nothing modifies it: clause-initial, or after a function word | **Chosen**: one regex, no phrase list of technologies, only ever narrows |
| **C**: section-level protection (a skills-section token can't reclassify the section) | Scope, like `semantic_scope` | **Rejected for this P0.** It changes scope for every cue (planned, negated, learning), is the larger behavioural change the brief warns about, and still wouldn't fix the work-bullet case, which is one sentence. `semantic_scope` already owns scope, behind its own flag |
| Phrase exception (`if phrase == "machine learning"`) | — | **Rejected.** "Deep", "Reinforcement", "Transfer", "Federated", "Supervised", "e-" … is an open list |
| **D**: occurrence-overlap semantics: a cue may not come from characters inside a skill/domain occurrence (review §8) | Restore bare `learning`, but ignore a match that lies inside a concept occurrence | **Assessed, not adopted.** (1) **Insufficient alone.** It protects a concept only from its OWN span. `Python, Machine Learning, FastAPI` still condemns Python and FastAPI unless every OTHER concept's spans are also excluded, and then only if the model happened to extract "machine learning" as a skill. The grammar rule protects neighbours whether or not it did. (2) **Wider plumbing.** `classify()` runs inside `find()` for one concept at a time. The other concepts' spans exist only later, in `assess_all()`. Passing them in changes `find`/`classify` signatures, and to keep the flag-gated scoped path in step, `semantic_scope.first_governing` too, which is a second production file. (3) **Where it WOULD add value:** clause-initial `Learning X` nouns the model extracted ("Learning Management Systems"). That is a pre-existing ambiguity, not this P0, so it is recorded as a follow-up (§O.5). The regex approach has the smaller blast radius: one file, one pattern, no signature change |

## E. Chosen fix

`skill_evidence.py`: the bare `learning` alternative of `_LEARNING` becomes `_LEARNING_VERB`. "learning" fires only when:

1. **nothing modifies it on the same line**: the character before it is not a letter or digit followed by a space, tab or hyphen, and not a letter or digit followed by two spaces. It opens a line or follows punctuation ("- Learning", "Built X. Learning Y", "(learning", "Summary⏎Learning React"); **or**
2. **it follows a function word** from a closed English class (`_LEARNING_LEADS`), separated by one space, newline or hyphen:
   - auxiliaries: am, is, are, was, were, be, been, being, I'm, we're;
   - adverbs of time: currently, actively, still, now, also, always, constantly, continuously, presently, recently;
   - phase and affect verbs: start…, began, begun, begin…, continue…, keep…, enjoy…, love…;
   - prepositions and conjunctions: about, in, on, of, for, by, and, while, then;
   - `self` (self-learning).

A `(?=learning)` guard is placed first, so no other position evaluates the lookbehinds (§L).

**Round 2 adds `_COURSE_TAKEN`** (see *Review correction* above) as one more alternative of the same `_LEARNING` pattern.

**Properties:**

- **The `learning` rule only narrows.** Every "learning" that fires now also fired before. The course cue is the **one deliberate widening**: explicit course language is now pedagogical whatever the subject ("Completed a React course." was MENTIONED on `d1c44bc` and is LEARNING now). The parity run shows it touches none of the 52 + 32 existing documents.
- **Every other cue is untouched:** studying, self-taught, tutorial, bootcamp, course on/in, working through, enrolled, coursework, familiarise, reading about, getting up to speed, beginner, exploring in my own time.
- **Nothing else moves:** the section rules (EDUCATION/CERTIFICATION → LEARNING), the sentence and soft-wrap logic, NEGATED/PLANNED/USED, the tiers, weights, bands and concepts.
- **The match is still exactly the word `learning`.** So `semantic_scope`'s cue text and spans are unchanged for every existing pedagogical case (its tests pass).
- **A line break never counts as a modifier join.** That is deliberate: "Summary⏎Learning React, TypeScript and Next.js" opens its section with the verb and must stay a cue. The cost is §O.1.

## F. Positive technology tests (new, `LearningAsAFieldIsNotLearning`, 11 tests)

| Test | Pins |
|---|---|
| each field in a comma skills list | Machine, Deep and Reinforcement Learning, and their neighbours: MENTIONED |
| one field on separate lines | the field plus Python, FastAPI, PostgreSQL, Docker: MENTIONED, tier SUPPORTING, ×3 fields |
| one field in a comma list | the neighbours: MENTIONED, SUPPORTING |
| labelled skills group | `Machine Learning: …` inline, label on its own line, and `Deep Learning / Reinforcement Learning: …`: PyTorch, scikit-learn, MLflow, Python, SQL all MENTIONED |
| building machine learning models is work | `- Built machine learning models using Python.`: both USED, Python **CORE** |
| hyphenated / double-spaced / tab | `Machine  Learning`, `Machine⇥Learning`, `machine-learning`, `e-learning`: neighbours MENTIONED |
| **any modified learning is a field** | 14 more: Transfer, Federated, Representation, Statistical, Active, Self-Supervised, Online, Contrastive, Few-Shot, Q-, Meta-, Imitation, Continual, Unsupervised Learning. Each, and its neighbours, is a claim, and none matches `_LEARNING` on its own. Not a three-item list: the rule is grammar |
| **substantive use of a field** | `- Built a federated learning system in Python.` · `- Implemented federated learning with PyTorch.` · `- Applied transfer learning with PyTorch.` · `- Trained reinforcement learning agents with Ray.`: every concept USED |
| the pattern itself | no match on Machine/deep/Reinforcement/Transfer Learning or machine-learning; a match on 7 verb forms |
| semantic scope agrees | the same results with `SWEEP_SEMANTIC_SCOPE=1`, and "Learning React, TypeScript and Next.js" still LEARNING there |
| **known limit: a field split by a line wrap** | pinned **KNOWN LIMITATION, unchanged by this fix** (§O.1) |

Test-first: the first 12 new tests were run against the unfixed code. 9 failed: all 8 technology tests, plus the mixed "cue governs its own sentence" test, whose Python sits in an ML work bullet. Every pure-preservation test passed. The two later additions (the known limit, and a section that opens with the verb) came from the design review in §E.

## G. Genuine learning and coursework preservation (new, `LearningAsPedagogyIsKept`, 10 tests)

| Case | Result |
|---|---|
| `- Currently learning Rust in my own time.` · `- Learning Kubernetes` · `- I am learning Go on weekends.` · `- Self-learning Rust.` · `- Built Python services. Learning Rust in my own time.` | LEARNING |
| `- Currently learning Machine Learning with PyTorch.` | machine learning **and** PyTorch: LEARNING |
| `In 2024 I was learning React, TypeScript and Next.js` · `Studying AWS for the associate exam.` · `Interested in learning Rust and Go.` | LEARNING |
| **`Coursework in Machine Learning and Python.`** | **LEARNING**, from `coursework`; the technology phrase does not override it |
| a section that opens with the verb (`Summary` / `SUMMARY` / `Profile` + `Learning React, …`) | LEARNING |
| `Relevant Coursework` and `Currently Learning` sections holding `Machine Learning, Python` | LEARNING (section rule) |
| one learning bullet beside one ML work bullet | Python USED, Rust LEARNING: the cue governs only its own sentence |
| **pedagogical context wins, for any field** (Transfer, Federated, Reinforcement, Machine Learning × `Currently learning X.` / `Completed a course in X.` / `Studying x.`) | LEARNING, 12 cases: the context decides, never the field string |
| **a completed course (the round-2 regression)**: `Completed a machine learning course.` · `Took a machine learning course.` · `Completed a course in machine learning.` · `Completed an online machine learning course on Coursera.` · `Finished a deep learning course in 2024.` · `Machine learning course, 2025.` · `Machine learning course (2025).` · the same as a work bullet · `Completed a React course.` | LEARNING |
| **course as a product** (control): `- Built the course catalogue service in Go.` · `- Completed the course-catalog migration to Kubernetes.` · `- Completed the course migration to PostgreSQL.` · `- Designed course recommendations with Python.` | **not** LEARNING |
| **specialization**: `Deep Learning Specialization, Coursera, 2024` under **Certifications** · the same free-standing in a summary | LEARNING (section) · MENTIONED (no cue: decision B) |
| **known ambiguity** (pinned, unchanged from `d1c44bc`): `Learning Management Systems, Moodle, SCORM` | Moodle LEARNING: the noun has the verb's shape (§O.5) |

All 20 of the existing adversarial cases (`test_01`–`test_24`) and every `test_semantic_scope` case pass unchanged.

## H. Section blast-radius result

One "Machine Learning" inside an ordinary SKILLS section no longer touches its neighbours:

| Layout | Python / FastAPI / PostgreSQL / Docker on `d1c44bc` | after the fix |
|---|---|---|
| flat, one per line | LEARNING, BACKGROUND | **MENTIONED, SUPPORTING** |
| comma-separated | LEARNING, BACKGROUND | **MENTIONED, SUPPORTING** |
| group label inline (`Machine Learning: PyTorch, …`, next line `Languages: Python, SQL`) | LEARNING across **both** lines | **MENTIONED** |
| group label on its own line | MENTIONED | MENTIONED (unchanged) |

## I. Existing 52-document parity

**Method.** Each tree runs in fresh interpreters: the pristine `git archive d1c44bc` and the fix worktree.

- Environment: `env -i` + `SWEEP_PROFILE_ENGINE=local`, `SWEEP_PROFILE_ENGINE_VERSION=v2`; sockets refused; empty output directory (frozen corpora).
- Inputs: the 52 bench résumés, with the committed `bench/results/qwen3_8b.json` + `dates-qwen3_8b.json` answering the two model calls (counted).
- Path: `generate_local` → `_finish` → `_title_gate` → `render()` with the app's India preferences.

| Check | Result |
|---|---|
| Derived dict (every key) | **52/52 identical** |
| Rendered profile bytes | **52/52 identical** |
| Merged title gate / role keywords | 52/52 identical |
| Model calls | 52 extract + 52 employment stubs per tree (2 per résumé); **0 real** |

The assumption held. hana's headline, "Machine Learning Engineer (formerly civil engineering)", contains the phrase, but no concept occurs in that header sentence, so nothing moved.

## J. Targeted before/after effects

**Crafted synthetic texts** (12; model answers stubbed; the full derivation):

| Text | Before (`d1c44bc`) | After | Rendered `SCORING` weight changes |
|---|---|---|---|
| `SKILLS: Python, Machine Learning, FastAPI, PostgreSQL, Docker` | Python, ML, Docker BACKGROUND (1) | SUPPORTING (2) | python 1→2, machine learning 1→2, docker 1→2 |
| skills one per line incl. Machine Learning | 4 concepts BACKGROUND | SUPPORTING | fastapi 2→3, ML/postgresql/python 1→2 |
| `Machine Learning: PyTorch, scikit-learn, MLflow` | BACKGROUND | SUPPORTING | pytorch, scikit-learn, mlflow 2→3 |
| `Python, Deep Learning, TensorFlow, Docker` | BACKGROUND | SUPPORTING | deep learning 2→3, docker 1→2, python 1→2 |
| `Reinforcement Learning, PyTorch, Python, Ray` | BACKGROUND | SUPPORTING | reinforcement learning 2→3, python 1→2 |
| `- Built machine learning models using Python.` | ML BACKGROUND, Python SUPPORTING | **both CORE** | machine learning **1→4**, python **2→4** |
| `- Built a federated learning system in Python.` | FL BACKGROUND, Python SUPPORTING | **both CORE** | federated learning **2→5**, python **2→4** |
| controls: currently learning Rust · coursework in ML (summary) · RELEVANT COURSEWORK section · studying AWS · **`Completed a machine learning course.`** (summary) | — | **identical (derived + rendered)** | none |

**The DQ2b fixture suite** (32 documents, its committed captures, read-only from the feature checkout):

- **24/32 identical.** Every app, platform and quality document.
- **The 8 data-family documents change, and only there.** Each change is a skills-list claim moving **BACKGROUND → SUPPORTING (+1)**: 7–13 concepts per document, and every one was LEARNING-only before and is no longer.
- Role keywords and title gates are **identical on all 32**: search does not move.

Every rendered `SCORING.skill_weights` change equals the derived change exactly. No concept, keyword, hint, band or tier constant moved.

**Edge phrases, first candidate vs round 2** (MEASURED with `classify` on each tree):

| Phrase (section) | `d1c44bc` | Round 2 |
|---|---|---|
| `Completed a machine learning course.` (summary; also as a work bullet) | LEARNING | **LEARNING** (course cue; the first candidate gave MENTIONED) |
| `Took a machine learning course.` · `Completed a course in machine learning.` · `Machine learning course, 2025.` · `Completed a Machine Learning course on Coursera.` | LEARNING | LEARNING |
| `Completed a React course.` | MENTIONED | **LEARNING**: the one deliberate widening, explicit course language |
| `Deep Learning Specialization, 2024.` (summary) | LEARNING | MENTIONED (decision B) |
| `Deep Learning Specialization, Coursera, 2024` (Certifications) | LEARNING | LEARNING (section) |
| `Python, Transfer Learning, Docker` · `…, Federated Learning, …` · `…, Self-Supervised Learning, …` (python) | LEARNING | **MENTIONED** |
| `Currently learning Transfer Learning.` · `Completed a course in Transfer Learning.` · `Studying federated learning.` | LEARNING | LEARNING |
| `- Built a federated learning system in Python.` | LEARNING | **USED** |
| `- Built the course catalogue service in Go.` · `- Completed the course migration to PostgreSQL.` | USED · MENTIONED | USED · MENTIONED (unchanged: "course" is the product) |
| `Learning Management Systems, Moodle` · `Moodle, Learning Management Systems, SCORM` | LEARNING | LEARNING (known ambiguity, unchanged) |
| `E-Learning, Moodle, SCORM` | LEARNING | MENTIONED |
| `Keen on learning Rust.` | LEARNING | LEARNING |
| `- Led the machine learning platform team using Kubernetes.` | LEARNING | **USED** |

## K. Full regression

Re-run after round 2 (the fix column is round 2's). The same suites ran on two worktrees in the same environment (`env -i PATH HOME LANG PYTHONDONTWRITEBYTECODE`, no `.env`, no `output/`): a **detached baseline at `d1c44bc`**, and the fix. Failures are compared **by name**. No log holds a token-shaped string.

| Suite | Baseline `d1c44bc` | Fix | Same failures? |
|---|---|---|---|
| Focused evidence (`test_evidence_semantics`, `test_semantic_scope`, `test_skill_evidence`, `test_evidence_depth`, `test_presentation_stability`, `test_role_evidence`) | 231 OK | **252 OK** (+21 new) | — |
| Phase 0b `sweep.tests.test_engine_propagation` | 39 OK | **39 OK** | — |
| Full Sweep `discover -s sweep/tests` | 1,701 run, 1 failure, 6 skipped | **1,701 run, 1 failure, 6 skipped** | **identical**: `test_public_paid…test_local_key_post_still_writes_env_and_os_environ` (needs a repo `.env`, absent in any worktree) |
| auto-apply `discover -s tests` | 1,102 run, 2 errors | **1,123 run** (+21), 2 errors | **identical**: the known `test_inference` `/healthz` 503 pair |
| Worker/deploy `deploy.test_sweep_worker` + `test_modal_benchmark` | 42 OK | **42 OK** | — |
| Bench `test_backends` + `test_answer_key` | 43 run, 1 failure | **43 run, 1 failure** | **identical**: `ProductionCapture…render_real_profile` (needs the gitignored `output/` corpus) |
| `python skill_evidence.py` (module self-check) | ok | **ok** | — |
| **Phase 0a goldens** | not on `main` (Phase 0a lives on `feat/multi-track-search`) | **35/35 OK** on the port rehearsal (§N) | no golden update |

**Environment notes.** The three failures and the 3 extra skips (6 vs 3 in the main checkout) are this worktree environment, not the fix: they reproduce identically on the pristine baseline. `main` has 1,701 Sweep tests; the feature branch has 1,860. There were no golden updates and no fixture rewrites.

## L. Performance

`skill_evidence.assess_all` over 84 synthetic texts (52 bench + 32 DQ2b) with their captured skill lists, caches cleared each round, median of 7, three alternating fresh-interpreter rounds per tree:

| Measure | `d1c44bc` | Fix | Δ |
|---|---:|---:|---:|
| `assess_all`, 84 documents | 177.5–178.9 ms | 193.9–195.1 ms (one 229 ms outlier) | **+9%** (≈ 0.2 ms per document) |
| `_LEARNING.search`, 4,200 whole-document searches | 159.6–165.1 ms | 299.4–311.8 ms | 1.9× (the course alternative adds a verb set tried at each position) |
| Warm derivation, 52 documents (the parity run) | 22.6 s | 22.1 s | noise; ≈ 420 ms per document, so +0.2 ms is ≈ 0.05% |

**How the cost was brought down.** The first version put the lookbehind alternation *before* the literal, so every text position evaluated ~60 lookbehinds. That was 10.5× slower on the regex and +52% on `assess_all`. Adding a `(?=learning)` guard first brought it to +6%. The guard changes no match: the fix's derived and rendered output was re-run and is identical on all 94 documents. Round 2's course alternative costs about 3 more points; left as is (negligible, and not optimised prematurely).

## M. Production file scope

| File | Change |
|---|---|
| `skill_evidence.py` | `_LEARNING`'s bare `learning` → `_LEARNING_VERB` (+ `_LEARNING_LEADS`), plus round 2's `_COURSE_TAKEN` (+ `_COURSE_END`), and the comments explaining them. **+48 / −1**, nothing else |
| `auto-apply/tests/test_evidence_semantics.py` | +2 test classes (21 tests) and two helpers, **+247 / −0**; no existing test changed |
| `P0_MACHINE_LEARNING_EVIDENCE_FIX_HANDOFF.md` | this document |

No other production file was needed: not `semantic_scope.py` (its tests pass unchanged), `make_profile`, `local_profile`, `scraper`, search, scoring, the worker or engine propagation.

## N. Future feature-branch port procedure

`skill_evidence.py` and `auto-apply/tests/test_evidence_semantics.py` are **byte-identical** at `d1c44bc` and at `589a05b` (`git diff d1c44bc 589a05b --quiet` on both), so the port is a straight copy. Rehearsed in a scratch `git archive 589a05b` with the two files copied in; the feature worktree was not touched:

| Suite on the rehearsal tree | Result |
|---|---|
| Phase 0a goldens (`sweep.tests.test_one_track_goldens`) | **35/35 OK**: no golden moves (none of their inputs holds the phrase) |
| DQ2a `test_track_intent` | **44/44 OK** |
| `test_evidence_semantics` | **63/63 OK** |
| DQ2b `bench.test_targeted` | **26/27**: only `test_known_defect_a_machine_learning_label_unclaims_the_skills_section` fails, exactly as designed (data-dataeng-plain's skills now MENTIONED) |

**Procedure, after this fix is approved and lands on `main`:**

1. Merge or cherry-pick the fix commit onto `feat/multi-track-search`. No conflict is expected: the files are identical.
2. In the **same** commit, flip DQ2b's known-defect test to assert the correct behaviour: every data-family skills-section occurrence is claimed, and none is `LEARNING_OR_COURSEWORK`. Update the handoff's §M.4 note.
3. **Replay DQ2b against the corrected code** (`python -m bench.targeted_eval`, no model call). Record the moved numbers; expected: data-family weights only, keywords and plans unchanged.
4. Re-run Phase 0a–3, 0b, DQ2a and DQ2b, the full Sweep and auto-apply.
5. Only then may DQ2c be opened (DQ2b review, Decision A).

## O. Risks and open questions

1. **Known limitation: a field wrapped across two PDF lines ("Machine⏎Learning, FastAPI").** Unchanged by this fix and pinned by a test. It isn't `_LEARNING` at all: a line that *starts* with "Learning" is taken as a heading (next item).
2. **A separate, pre-existing heading defect, found during this audit, NOT fixed: a separate P0 commit immediately after this one, by review decision.** `_HEADINGS`' EDUCATION row `(currently\s+)?(learning|studying)|in\s+progress|(professional|continuing)\s+(development|education)` has an **ungrouped top-level `|`**. `_MATCHERS` and `_SPACELESS` wrap it as `^…$`, so the first branch is anchored at the start only.
   - Effect: **any line of ≤ 60 characters that begins with "Learning", "Studying" or "Currently learning", or with "In progress", opens an EDUCATION section**, and everything after it is coursework until the next heading.
   - Examples: "Learning React, TypeScript and Next.js", "Studying AWS for the exam", "In progress: AWS SA", "Learning, FastAPI".
   - `_HEADING_LINE` is correct (it groups every row).
   - None of the 84 synthetic documents has such a line, except hana-tables' bare "Learning" line from a table-wrapped headline, which is a whole-line match anyway.
   - Fixing it (one pair of parentheses) changes section boundaries, the collateral the brief warns against. **It needs its own decision:** a follow-up P0/P1 with its own tests and parity.
3. **Resolved in round 2: the explicit course.** "Completed a machine learning course." is LEARNING again, through the course cue. **Still a claim, by decision:** a free-standing "Deep Learning Specialization" (B). It is LEARNING under a Certifications-type heading (A); a credential-name rule would be separate future work (C). **Not covered by the course cue** (MENTIONED; LEARNING only under a Courses/Training/Certifications heading):
   - "course" followed by "and …";
   - a course named without a verb or date ("Machine learning course");
   - "self-study", which is not a cue.
4. **The function-word class is closed and English.** A pedagogical "learning" after a word not in it ("desperately learning Go") is missed. The only cost is MENTIONED instead of LEARNING; nothing is falsely unclaimed.
5. **Clause-initial `Learning X` nouns: a known ambiguity, unchanged, pinned.** "Learning Management Systems", "Learning & Development", "Learning to Rank" and "Learning Analytics" have the verb's shape ("Learning React") and still fire at a clause start, *including* as a later comma item ("Moodle, Learning Management Systems"). They then take their sentence with them, exactly as on `d1c44bc`.
   - No regex can tell "Learning Kubernetes" from "Learning Management Systems" without a vocabulary.
   - What can is the concept spans the document names: in `assess_all`, "learning management system" is an extracted concept whose span covers the cue.
   - That is option D's plumbing (§D). It is recorded as a follow-up with its own review, not built here.
6. **Scope is unchanged.** A *genuine* cue such as "Learning: Rust, Go" on a skills line still governs its whole soft-wrapped sentence, as before. That is `semantic_scope`'s job.
7. **Real résumés** were not re-measured (none supplied; none read). The effect on the user's real data is INFERENCE: any résumé listing "Machine Learning", "Deep Learning" and the like in skills, a summary or a work bullet had those sentences' skills tiered BACKGROUND and should now tier correctly.

---

Stop for review: nothing committed, pushed or deployed.
