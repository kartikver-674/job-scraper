# P0 Handoff: a line that begins like a learning heading is not one

| | |
|---|---|
| Checkpoint | Second standalone P0 profile fix: EDUCATION heading-row precedence (Option 1, sections only + an explicit compatibility boundary) |
| Branch | `fix/skill-evidence-machine-learning` (no upstream) |
| Starting commit | `2e8da15cd7254ba076598e59412e342fd87319a2` (the learning-domain fix) → parent `d1c44bca2fbde6e49b5aa9e91fe5acdae02d1d0e` = `origin/main` = remote `main` (verified with `git ls-remote`) |
| Production files changed | **`skill_evidence.py` only** |
| Tests | `auto-apply/tests/test_evidence_semantics.py`: +1 class (9 tests); the pinned wrapped-ML test's *mechanism* assertion updated, its behaviour assertion unchanged |
| `_LEARNING` | **byte-identical to `2e8da15`** (source block and compiled pattern) |
| Review | **APPROVED** (Option 1 as implemented; the recorded debt in §P and the wrapped `Machine⏎Learning` limitation accepted) |
| Commit / push / deploy | local second commit `fix(profile): anchor learning section headings`, parent `2e8da15` (these 3 files; the SHA is `git log -1` on this branch); **not pushed, not deployed**. Next: the combined two-commit release preflight (`d1c44bc` + `2e8da15` + this commit) |
| Model / provider calls | **none**. Every derivation stubs the two model calls from committed captures; sockets are refused |
| Multi-Track | untouched: `feat/multi-track-search` @ `589a05b`, clean (scratch `git archive` copies only) |

---

## A. Starting commit

- `2e8da15` on `fix/skill-evidence-machine-learning`, working tree clean.
- `origin/main` and remote `main` are both `d1c44bc`.
- The feature worktree is `589a05b`, with no tracked difference.

## B. The malformed regex

`skill_evidence._HEADINGS`, row 9 (EDUCATION):

```
(currently\s+)?(learning|studying)|in\s+progress|(professional|continuing)\s+(development|education)
```

It is the **only** row with a top-level `|`, checked by a scan of all 13 rows that skips groups and character classes.

## C. Precedence proof (from the compiled expressions at `2e8da15`)

| Matcher | Compiled | Effect |
|---|---|---|
| `_MATCHERS[9]` | `^\s*(currently\s+)?(learning\|studying)\|in\s+progress\|(professional\|continuing)\s+(development\|education)\s*:?\s*$` | `^` binds to branch A only, `\s*:?\s*$` to branch C only. `re.match` anchors B at the start. **A and B match any prefix** |
| `_SPACELESS[9]` | `^(currently)?(learning\|studying)\|inprogress\|(professional\|continuing)(development\|education)$` | the same, on the folded line |
| `_HEADING_LINE` | `^\s*(?:row1\|…\|row13)\s*:?\s*$` | correct, since it groups every row, but **defined and never used** |

**MEASURED:**

- `match("Learning React and TypeScript")` returns `'Learning'` (8 of 29 characters).
- `match("In progress building a Go service")` returns `'In progress'`.
- `match("Professional Development and more")` returns `None`: branch C is the anchored one.

The grouped form `^\s*(?:A|B|C)\s*:?\s*$` rejects both content lines and keeps every heading. **The missing group is the entire heading defect.**

## D. Pristine reproduction, and why grouping alone was unsafe

**On `2e8da15`, all of these open an EDUCATION section:**

- Learning React, TypeScript and Next.js
- Learning React and TypeScript
- Learning Kubernetes for CKAD
- Studying AWS for the exam
- Studying distributed systems independently
- In progress with Terraform training
- In progress building a Go service
- Learning, FastAPI
- Learning: Rust, Go
- **Learnings from scaling Kafka**
- Studying: AWS SAA

Everything after them, until the next heading, reads as coursework:

- the work bullet `- Deployed services with Docker.` after `In progress with Terraform training` drops from **CORE to BACKGROUND**;
- a summary sentence `Built APIs in Go and PostgreSQL.` after `Studying AWS for the exam` reads as coursework.

**Grouping alone (measured on a scratch copy) fixed every section but regressed three skills layouts.** The false heading had also been a **hard sentence break**. Without it a skills block is one soft-wrapped sentence, so a learning cue there reaches the skills *above* it:

| Layout | `2e8da15` | grouping alone |
|---|---|---|
| `Python, SQL, Machine⏎Learning, FastAPI⏎Docker` | Python and SQL claims | **all five LEARNING** |
| `Python⏎Learning React and TypeScript⏎FastAPI⏎PostgreSQL` | Python claim | **Python LEARNING** |
| `Python, SQL⏎Learning: Rust, Go⏎Docker` | Python and SQL claims | **Python and SQL LEARNING** |

## E. Chosen approach: sections only, plus an explicit compatibility boundary (Option 1)

1. **Group every row before it is anchored**, in both matcher constructions. This changes the language of only a row with a top-level `|`, which is row 9 alone.
2. **Keep, on purpose, the sentence boundary the bug provided, without the section.**
   - Exactly the lines the malformed row took for headings become **pedagogical content lines**: ≤ 60 characters (the heading limit), beginning with the row's own lead words (`(currently )?(learning|studying)` or `in progress`), and not a real heading.
   - Each such line **is its own sentence**, with a hard break on **both** sides, as it was when it was a "heading". But it opens no section: the section it sits in continues.

   So one "Learning React" line cannot make the skills above **or** below it coursework, while its own content keeps whatever its own words say.
3. **`_LEARNING` untouched.** No soft-wrap NLP, no line-level skills scoping, no second production file.

## F. Implementation (`skill_evidence.py`)

| Change | Where |
|---|---|
| `_PEDAGOGICAL_LEAD = r"(currently\s+)?(learning\|studying)\|in\s+progress"`, and row 9 written as `_PEDAGOGICAL_LEAD + "\|(professional\|continuing)\s+(development\|education)"` | the row's **value is byte-identical** (`_HEADINGS` compares equal to `2e8da15`'s); one source for the lead words |
| `_MATCHERS`: `r"^\s*(?:" + pattern + r")\s*:?\s*$"` | was `r"^\s*" + pattern + …` |
| `_SPACELESS`: `r"^(?:" + folded pattern + r")$"` | was `r"^" + … + r"$"` |
| `_PEDAGOGICAL_LINE` + `_leads_pedagogy(line)`: ≤ 60 characters, `_PEDAGOGICAL_LEAD` matched as a prefix, `_heading() is None` | new |
| `_soft_newlines`: a newline is soft only if also `not _leads_pedagogy(before) and not _leads_pedagogy(after)` | +2 conditions |

- **The prefix is matched as the row used to match**, with no word boundary, so "Learnings from scaling Kafka" stands alone too. A boundary can only isolate a line; it can never make anything learning.
- **`_soft_newlines` is cached per document**, so `_leads_pedagogy` runs once per line.
- **Every other user of the sentence and unit logic** (`_sentence`, `_unit`, `_governed`, `semantic_scope`'s windows) follows automatically, because they all read `_wrapped`. `semantic_scope.py` is unchanged.

## G. Valid headings preserved

All still open **EDUCATION**:

- Learning · Learning: · LEARNING
- Currently Learning · Currently Learning: · currently learning · C urrently Learning (PDF-broken, via `_SPACELESS`)
- Studying · Studying: · STUDYING · Currently Studying
- In Progress · In Progress: · In Progress : · "  In Progress  "
- Professional Development · Continuing Education

The separate rows are unchanged:

- Relevant Coursework, RELEVANT COURSEWORK and Education → EDUCATION;
- Courses, Training and Certifications → CERTIFICATION.

**Only row 9 changed** (MEASURED). I compared old and new `_MATCHERS` and `_SPACELESS`, row by row, over 59 representative heading and content lines for every family. Exactly 5 lines changed, all in row 9, all from heading to content:

- Learning React and TypeScript
- Studying AWS for the exam
- In progress building a Go service
- Learnings from scaling Kafka
- Learning: Rust, Go

Lines that only resemble other rows ("Experience with Kafka and Redis", "Coursework in ML", "Professional Development in AWS", "Education and training", "Training new engineers on Kafka") behave exactly as before.

## H. False-positive content cases (before → after)

| Line | Heading? | Section it opens |
|---|---|---|
| Learning React, TypeScript and Next.js · Learning React and TypeScript · Learning Kubernetes for CKAD | yes → **no** | EDUCATION → **none** |
| Studying AWS for the exam · Studying distributed systems independently · Studying: AWS SAA | yes → **no** | EDUCATION → **none** |
| In progress with Terraform training · In progress building a Go service | yes → **no** | EDUCATION → **none** |
| Learnings from scaling Kafka | yes → **no** | EDUCATION → **none** |
| Learning: Rust, Go · Learning, FastAPI | yes → **no** | EDUCATION → **none** |

Tested in a skills section, `Skills⏎Python⏎<line>⏎PostgreSQL`, for every line: the sections are OTHER + SKILLS only, and Python and PostgreSQL stay SKILLS/MENTIONED.

## I. Backward and forward contamination

Occurrence section and status, MEASURED on the working fix (and pinned):

| Case | `2e8da15` | Fix |
|---|---|---|
| `SKILLS⏎Python⏎Learning React and TypeScript⏎FastAPI⏎PostgreSQL` | Python SKILLS/claim; React, TS in no section/LEARNING; FastAPI, PostgreSQL **EDUCATION/LEARNING** | **all SKILLS**: Python, FastAPI, PostgreSQL **claims** (SUPPORTING); React, TypeScript **LEARNING** (BACKGROUND) |
| `SKILLS⏎Python, SQL⏎Learning: Rust, Go⏎Docker` | Python, SQL claims; Rust, Go LEARNING; Docker EDUCATION/LEARNING | **all SKILLS**: Python, SQL, Docker **claims**; Rust, Go **LEARNING** |
| `SKILLS⏎Python⏎Studying AWS for the exam⏎PostgreSQL` | PostgreSQL EDUCATION/LEARNING | **all SKILLS**: Python, PostgreSQL claims; AWS LEARNING |
| `SKILLS⏎Python⏎Learnings from scaling Kafka⏎PostgreSQL` | PostgreSQL EDUCATION/LEARNING | all SKILLS: Python, PostgreSQL claims; Kafka USED ("scaling", its own line) |
| work: `- Built APIs in Python.⏎In progress building a Go service⏎- Deployed services with Docker.` (and `… Terraform training`) | Docker EDUCATION/LEARNING, **BACKGROUND** | **Docker WORK/USED, CORE**; Python WORK/USED |
| summary: `Studying AWS for the exam⏎Built APIs in Go and PostgreSQL.` | Go, PostgreSQL EDUCATION/LEARNING | **SUMMARY/USED**; AWS SUMMARY/LEARNING |
| real `Currently Learning` / `STUDYING` / `In Progress:` heading after `Python` | Rust, Kubernetes EDUCATION/LEARNING | **unchanged**: EDUCATION/LEARNING; Python SKILLS/claim |

**Backward contamination: eliminated. Forward contamination: eliminated.**

A long pedagogical sentence (> 60 characters) that a PDF wrapped keeps its continuation, exactly as before: `Learning React, TypeScript and Next.js by building two side projects⏎with Tailwind CSS in my own time.` makes Tailwind CSS LEARNING (pinned).

## J. Wrapped `Machine⏎Learning`: still a known limitation

`SKILLS⏎Python, SQL, Machine⏎Learning, FastAPI⏎Docker`:

| | `2e8da15` | Fix |
|---|---|---|
| sections | SKILLS, then EDUCATION after the wrap | **SKILLS throughout** |
| Python, SQL | claims | claims |
| FastAPI | LEARNING (in the false heading line) | **LEARNING** (known limit) |
| Docker | EDUCATION/LEARNING | **claim** (improved) |

**Remaining mechanism.** "Learning, FastAPI" is now a pedagogical content line (its own sentence), and `_LEARNING` never treats a newline as a modifier join. So a line-initial "Learning" reads as the verb, and FastAPI on that same line reads as learning.

- Fixing it needs a soft-wrap-aware `_LEARNING` rule (Option 2), which this checkpoint froze.
- `test_known_limit_a_field_split_by_a_line_wrap` keeps its **behaviour** assertion unchanged: Python a claim, FastAPI LEARNING.
- Its **mechanism** assertion flipped from `_heading("Learning, FastAPI") == EDUCATION` to `is None`, because that assertion was the heading bug itself.
- Its docstring now names the remaining mechanism. It is not weakened.

## K. `_LEARNING` byte-identity

MEASURED against `git show 2e8da15:skill_evidence.py`:

- the whole `_LEARNING` source block is **byte-identical** (3,346 bytes: from the "Learning is pedagogy as a VERB" comment through `_LEARNING = re.compile(...)`);
- the compiled `_LEARNING.pattern` and its flags are identical.

All 2e8da15 learning-domain tests (Machine/Deep/Reinforcement/14 more X-Learning fields, substantive use, the course cue, genuine learning, Coursework in Machine Learning, the Specialization and LMS pins) pass unchanged.

## L. 52-document parity

Same harness as the previous P0: fresh interpreters, `env -i` + v2, frozen corpora, two stubbed model calls per résumé, sockets refused.

| Set | vs `2e8da15` | vs `d1c44bc` |
|---|---|---|
| **52 bench documents**: derived dict | **52/52 identical** | **52/52 identical** |
| 52 bench documents: rendered profile bytes | **52/52** | **52/52** |
| 52 bench documents: merged gate / role keywords | 52/52 / 52/52 | 52/52 / 52/52 |
| 32 DQ2b documents (release code) | 32/32 derived and rendered | 24/32 (the 8 data documents: `2e8da15`'s intended change) |
| 12 crafted texts | 12/12 rendered, **11/12 derived** | — |

The one crafted derived difference is `ctl-studying` ("SUMMARY⏎Studying AWS for the associate exam."). Its AWS occurrence's **section label** moves from `other` to `summary`. On `2e8da15` the occurrence sat *inside* the false heading line, which belonged to no section. Status (LEARNING), tier (BACKGROUND), weight, keywords and rendered bytes are identical.

## M. DQ2b capture replay (no model call)

Scratch `git archive` trees of the feature branch; the feature worktree is untouched. Each tree ran its own `bench.targeted` replay and `bench.targeted_eval`.

| Transition | Documents whose derivation changed | Role keywords / title gate / search plans | TrackIntent |
|---|---|---|---|
| `589a05b` → `+2e8da15` | **the 8 data-family documents** (the known ML defect flips) | identical / identical / identical | the 8 change: skills sections are now claimed (recorded occurrences 0 → 19–25), and data-ml's intent concepts go 4 → 8 (plain) / 4 → 7 (twocol) |
| `+2e8da15` → `+heading fix` | **0 of 32** | identical | identical |

**Every `targeted_eval` section is identical** under the heading fix (plans, convergence, M3, jobs, floor contract, intent, skills experiment, fail-open): no DQ2b document hits the malformed row.

M3 numbers that moved under `2e8da15`, for the port:

- data-plain: ties 1 → 0;
- data-twocol: intended-track wins 6 → 4, ties 3 → 4;
- data weight mass: dataeng 57 → 67, ml 81 → 92 (plain) / 68 → 75 (twocol), analytics 53 → 66.

## N. Full regression

The fix worktree and a scratch feature tree (`589a05b` + `2e8da15` + this fix) were each compared **by test name** with the detached `d1c44bc` baseline, in the same environment (`env -i PATH HOME LANG PYTHONDONTWRITEBYTECODE`, no `.env`, no `output/`). No log holds a token-shaped string.

| Suite | Baseline `d1c44bc` | This fix | Same failures? |
|---|---|---|---|
| Focused evidence (`test_evidence_semantics`, `test_semantic_scope`, `test_skill_evidence`, `test_evidence_depth`, `test_presentation_stability`, `test_role_evidence`) | 231 OK | **261 OK** (231 + 21 from `2e8da15` + 9 new) | — |
| Phase 0b `sweep.tests.test_engine_propagation` | 39 OK | **39 OK** | — |
| Full Sweep | 1,701 run, 1 failure, 6 skipped | **1,701 run, 1 failure, 6 skipped** | **identical**: `test_local_key_post_still_writes_env_and_os_environ` (needs a repo `.env`) |
| auto-apply | 1,102 run, 2 errors | **1,132 run** (+30), 2 errors | **identical**: the known `test_inference` `/healthz` 503 pair |
| Worker/deploy | 42 OK | **42 OK** | — |
| Bench (`test_backends`, `test_answer_key`) | 43 run, 1 failure | **43 run, 1 failure** | **identical**: `ProductionCapture…render_real_profile` (needs `output/`) |
| `python skill_evidence.py` | ok | **ok** | — |
| Feature scratch: Phase 0a goldens | — | **35/35 OK** | no golden update |
| Feature scratch: DQ2a `test_track_intent` | — | **44/44 OK** | — |
| Feature scratch: `test_evidence_semantics` | — | **72/72 OK** | — |
| Feature scratch: DQ2b `bench.test_targeted` | — | **26/27**: only `test_known_defect_a_machine_learning_label_unclaims_the_skills_section` fails, the designed `2e8da15` flip | — |

There were no fixture or golden rewrites.

## O. Performance

84 synthetic texts (52 bench + 32 DQ2b) with their captured skill lists; caches cleared each round; median of 7; three alternating fresh-interpreter rounds per tree.

| Measure | `2e8da15` | Fix | Δ absolute | Δ |
|---|---:|---:|---:|---:|
| `sections()` + `_soft_newlines()`, 84 documents | 21.2–21.6 ms | 21.8–22.3 ms | ≈ +0.6 ms total (≈ **8 µs per document**) | +3–5% |
| `assess_all`, 84 documents | 186.4–187.6 ms | 187.2–191.5 ms | ≈ +1–4 ms total (≈ **0.01–0.05 ms per document**) | +0.5–2% |
| `_LEARNING.search`, 4,200 searches | 296.8–299.7 ms | 297.3–297.9 ms | none | identical pattern |

The added work is `_leads_pedagogy` on the two lines around each newline, inside the per-document cached `_soft_newlines`. A warm derivation is ≈ 420 ms per document, so the cost is negligible.

## O2. Production file scope

| File | Change |
|---|---|
| `skill_evidence.py` | row-9 lead constant (value-identical row), grouped `_MATCHERS` / `_SPACELESS`, `_leads_pedagogy` + two `_soft_newlines` conditions, comments. +41 / −5 |
| `auto-apply/tests/test_evidence_semantics.py` | +`TheLearningHeadingRowIsAnchored` (9 tests), 2 helpers; wrapped-ML test mechanism assertion + docstring. +132 / −8 |
| `P0_HEADING_DETECTION_FIX_HANDOFF.md` | this document |

No other production file: `semantic_scope.py`, `make_profile`, `local_profile`, `scraper`, search, scoring, the worker and engine propagation are all untouched. The compatibility boundary is expressed entirely by `skill_evidence._soft_newlines`, which every sentence and unit window already reads.

## P. Remaining sentence-scope / soft-wrap debt (not fixed here)

1. **Wrapped `Machine⏎Learning`** (§J): needs a soft-wrap-aware `_LEARNING` join (Option 2).
2. **A genuine learning cue in the middle of a skills line**, such as `Python, SQL, currently learning Rust`, still governs its whole soft-wrapped skills sentence, as before. Only *line-initial* pedagogical lines are isolated. That is general sentence scope (Option 3 / `semantic_scope`).
3. **Long (> 60 character) pedagogical lines** are not isolated. That keeps the wrap of real prose (§I), but a long "Learning …" line inside a skills list still joins its neighbours, as before.
4. **"In progress …" lines have no prose cue.** `In progress with Terraform training` reads Terraform by its ordinary evidence (USED via "with"). `in progress` stays a heading-only word; `_LEARNING` is frozen.
5. **A pedagogical line in a work section can become an entry label.** Because it now stands alone, a capitalised non-bullet line after a finished bullet is read as an entry. For example, `In progress with Terraform training` labels the next bullet's "used at …". This affects provenance text only, never a section, status or tier.
6. **A letter-broken `L earning …` content line** is neither a heading nor a boundary (the spaceless matcher alone ever took it for one).
7. **`Learning Management System`**-style clause-initial nouns: still a known ambiguity (previous P0, §O.5).
8. **`_HEADING_LINE`** is correct but unused. Left in place: removing dead code is out of this checkpoint's scope.

---

Stop for review: nothing committed, pushed or deployed.
