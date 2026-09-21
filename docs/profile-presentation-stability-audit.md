# Profile presentation stability — audit of two résumés for one person

Traced and measured at `3e54a98` against the live corpus (22,806 listings,
367-term skill vocabulary). Nothing changed in Profile Engine V3, query
generation, title gating or the engine's behaviour. One test file was added.

The reported case: **Srishti Rawat, one career, two résumés — 1 searchable role
against 9, and 14 flat weights against 9 differentiated ones.**

Production configuration throughout, as `render.yaml` pins it:
`SWEEP_PROFILE_ENGINE=local`, `SWEEP_PROFILE_ENGINE_VERSION=v2`. **Every V3 step
flag is absent and therefore off** — `SWEEP_ROLE_EVIDENCE`,
`SWEEP_CANDIDATE_TITLE_GATE`, `SWEEP_FAMILY_CENTRALITY_GATE`,
`SWEEP_CANDIDATE_HARD_DROP`, `SWEEP_ORPHAN_ROLE_GUARD`,
`SWEEP_CANONICAL_REVALIDATION`, `SWEEP_ROLE_ATTACHMENT_GUARD`,
`SWEEP_SEMANTIC_SCOPE`. That matters for the verdict and is established by
trace, not assumed — see Part 5.

The answer, first: **the magnitude is not justified, the cause is not the
headline, and V3 Fix B is not involved.** Two deterministic defects, both
upstream of everything the brief suspected, produce the whole gap.

---

## Part 1 — Exact document diff

The two files are **not** the same content under different headings. The
bullet-level diff:

```
bullets                  functional 19    ba 14
identical bullets                         0
near-matched pairs                        8
functional-only blocks   SELECTED PROJECTS (5 clients), 3 extra work bullets
```

| # | difference | Functional version | BA version | semantic or presentational | affects profile? | magnitude |
|---|---|---|---|---|---|---|
| 1 | **A** candidate headline | `FUNCTIONAL CONSULTANT` | `SALESFORCE BUSINESS ANALYST` | presentational | **no** (measured) | none — Part 7 |
| 2 | **B** summary opener | "Functional Consultant with 1+ year … business-process and CRM implementations" | "Salesforce Certified Administrator and Business Analyst with 1+ year … on Salesforce Sales Cloud and Service Cloud" | **semantic** — names the products | yes | 1 band on 3 concepts |
| 3 | **B** summary tail | adds change management, business operations, production support, stakeholder coordination | omits all four | **semantic** | yes | 4 concepts exist only in F |
| 4 | **C** skills heading | `SKILLS` | `CORE COMPETENCIES` | **presentational** | **yes, catastrophically** | roleJ 1.0 → 0.091 — Part 6 |
| 5 | **C** tools heading | `TOOLS & TECHNOLOGIES` | `TECHNICAL SKILLS` | **presentational** | yes | 1 band × 4 concepts |
| 6 | **D** skills list wording | 14 `&`-joined business phrases (`Sales Cloud & Service Cloud`, `Salesforce Administration & Configuration`) | 10 phrases **plus** a products block (`Salesforce: Sales Cloud, Service Cloud, …`) | **semantic in form, identical in content** | **yes — this is the root cause** | 1 role vs 11 |
| 7 | **E** employment title | `Functional Consultant` | `Salesforce Functional Consultant` | **semantic** (the person's own title, as written) | yes | exactly 1 role — Part 8 |
| 8 | **F** the decisive bullet | "configured **CRM solutions** to match them" | "configured **Salesforce Sales Cloud and Service Cloud** solutions to match them" | **semantic** | **yes** | `salesforce` 1→4, `sales cloud` 2→5, `service cloud` 2→5 |
| 9 | **F** bullets present only in F | tracked change requests; mapped workflows → SOPs; coordinated UAT | folded into other bullets | **semantic** | yes | `change requests` CORE/5 in F only |
| 10 | **G** projects | 5 named clients (Greaves, Parle Agro, L'Oréal, Boat, JK Papers) | **absent** | **semantic** | yes | `procurement` STRONG_SECONDARY/4 in F only |
| 11 | **H** tools wording | `Salesforce (Sales Cloud, Service Cloud, Lightning Experience, Experience Builder, Flow Builder, Lightning App Builder, Reports & Dashboards)` | `Salesforce: Sales Cloud, Service Cloud, Experience Builder, Flow Builder, Reports & Dashboards` | presentational (F is a superset) | no, once the heading is read | — |
| 12 | **I** date placement | title and dates on one line | dates on their own line | presentational | no | none |

**"SKILLS" vs "CORE COMPETENCIES" is row 4, and it is the single most damaging
difference in the table** — not because the classifier misreads it (it does
not; both map to `skills`) but because of what it does to the model's *choice
of which section to read*. Part 6 isolates it.

Rows 2, 3, 8, 9, 10 are genuine evidence differences. **Row 8 alone accounts
for the entire `salesforce` weight gap.** The Functional résumé never writes
"Salesforce" in a work bullet; it writes "CRM".

---

## Part 2 — Production parser diff, layer by layer

Both documents through `make_profile.generate_local`, identical environment,
identical corpus. Temperature is 0 and a byte-identical re-run reproduced every
field exactly (determinism control, Part 10), so every difference below is
causal rather than sampling noise.

| layer | Functional | BA | diverged? |
|---|---|---|---|
| `local_extract.read` → router | `accept` | `accept` | no |
| `experience_months` | **20** | **20** | **no** |
| `years_experience` | **1** | **1** | **no** |
| `checked["titles"]` | `['FUNCTIONAL CONSULTANT']` | `['Salesforce Business Analyst']` | yes (the headline) |
| employment titles | `['Functional Consultant']` | `['Salesforce Functional Consultant']` | yes |
| `rows["target_field"]` | `functional consulting` | `salesforce business analysis` | yes |
| **`checked["skills"]`** | **14 `&`-compounds** | **11 atomic product names** | **YES — first material divergence** |
| `skill_concepts.identities()` | 14 in, **14 out, none split** | 11 in, 11 out | yes |
| `clean_skills` filler | none | none | no |
| **terms in corpus vocab** | **1 of 14** (`reports & dashboards`) | **6 of 11** | **YES** |
| **`matching_rows(need=2)`** | **0 listings** | **308 listings** | **YES — the collapse** |
| `keywords_for` | **`[]`** | 12 fragments | yes |
| `from_resume` (held titles) | `['functional consultant']` | `['salesforce functional consultant']` | yes |
| `validated` | `['functional consultant']` | 11 roles | yes |
| `hints_for` | **0 hints** | **38 hints** | yes |
| `skill_scan.widen` | +22 recovered | +19 recovered | no (both work) |
| `skill_evidence.assess_all` | 36 concepts | 30 concepts | see Part 4 |

### The first layer where they materially diverge

**`local_extract.read`'s EMPLOYMENT/FIELDS call — the model's choice of which
skills block to read.** Everything after that is a consequence.

The Functional résumé has one skills block, written as business phrases. The BA
résumé has two, and the model read the *products* block. That choice is not
stable (Part 6), and the layer immediately downstream has no tolerance for the
wrong answer.

### The layer that turns a soft difference into a hard one

`skill_concepts.split_compound` ([skill_concepts.py:388](skill_concepts.py#L388)).
`&` **is** a separator, but the split is gated on every half being present in
`LOOKUP`, the hand-curated concept table. Measured against that table and
against the market:

| compound | halves | in `LOOKUP`? | in corpus vocab? | split? |
|---|---|---|---|---|
| `sales cloud & service cloud` | sales cloud / service cloud | no / no | **yes / yes** | **no** |
| `reports & dashboards` | reports / dashboards | no / no | **yes / yes** | **no** |
| `agile & sdlc` | agile / sdlc | **yes** / no | yes / yes | **no** |
| `gap analysis & process improvement` | gap analysis / process improvement | no / no | yes / no | no (correct) |
| `salesforce administration & configuration` | salesforce administration / configuration | no / no | no / no | no (correct) |

`LOOKUP` is developer-tool-shaped: it contains `salesforce` and `soql`, and not
`sales cloud`, `service cloud`, `flow builder`, `reports`, `dashboards`. The
corpus — measured from 22,806 real postings — contains all of them.

**Two registries answer "is this a real skill", and search only trusts the one
that was never consulted.**

### The counterfactual, measured

Applying `split_compound`'s own rule with the market vocabulary admitted as a
second registry, changing nothing else:

```
Functional skills, as shipped      1 of 14 in vocab → 0 matching rows  → 1 role
Functional skills, atomised        6 of 17 in vocab → 330 matching rows → 11 roles

  ['functional consultant', 'salesforce techno functional consultant',
   'salesforce business analyst', 'functional consultant, salesforce core',
   'consultant salesforce', 'salesforce administrator',
   'salesforce sales cloud', 'product owner', 'revenue operations',
   'salesforce consultant', 'sf -data cloud']
```

That is the BA profile, from the Functional document, with the headline,
target_field, employment title and every V3 flag untouched. **The 1-vs-9 gap is
a compound-atomisation defect and essentially nothing else.**

---

## Part 3 — Provenance of every role

### BA version — 11 roles

`salesforce functional consultant` is held. The other ten come from
`keywords_for`, retrieved by the 308 listings sharing ≥2 of the candidate's six
in-vocabulary skills, ranked by lift × reachability, then canonicalised to a
real corpus title.

| role | source evidence | source section | source text | family | core/peripheral | why admitted |
|---|---|---|---|---|---|---|
| `salesforce functional consultant` | held job title | PROFESSIONAL EXPERIENCE | "Salesforce Functional Consultant \| Dealermatix" | consulting | **core** | `from_resume` — the person's own title, seniority stripped, ≥2 words |
| `salesforce administrator` | 39 listings | corpus | wanting salesforce(39), service cloud(25), sales cloud(25), soql(7) | admin | **core** | highest lift; certification corroborates |
| `salesforce business analyst` | 20 listings | corpus | salesforce(20), service cloud(17), sales cloud(15) | analysis | **core** | lift + the headline's own words |
| `salesforce sales cloud` | 14 listings | corpus | salesforce(14), sales cloud(12) | platform | **core** | product named in a work bullet |
| `salesforce engineer` | 11 listings | corpus | salesforce(11), **soql(8)** | **development** | **peripheral** | retrieved by SOQL — a skills-list claim, no construction verb anywhere |
| `consultant salesforce` | 9 listings | corpus | service cloud(9), salesforce(9) | consulting | core | word-order variant of the held title |
| `functional consultant, salesforce core` | 6 listings | corpus | salesforce(6), all five skills | consulting | core | canonicalised from `functional consultant` |
| `revenue operations` | **6 listings** | corpus | salesforce(6), sales cloud(6) | **sales ops** | **peripheral** | co-occurs with Sales Cloud; **no revenue-ops evidence in either document** |
| `sf -data cloud` | 6 listings | corpus | service cloud(6), salesforce(6) | platform | peripheral | Data Cloud appears nowhere on the résumé; also a malformed string |
| `salesforce techno functional consultant` | **5 listings** | corpus | salesforce(5), **soql(5)**, flow builder(3) | **hybrid dev** | **peripheral** | "techno" asserts development the document does not show |
| `product owner` | **3 listings** | corpus | service cloud(3), salesforce(3) | **product** | **peripheral** | **thinnest in the set** |

`MIN_LISTINGS = 10` ([local_search.py:74](local_search.py#L74)) is denominated
in *evidence weight*, not rows — each matched listing counts
`max(1.0, evidence)`, which is 7–12 here. So `product owner` clears a floor
named "10 listings" on **3 real postings**. Worth knowing before trusting the
tail of any corpus list.

### Functional version — 1 role

| role | source | why the others are missing |
|---|---|---|
| `functional consultant` | held job title | `keywords_for` returned `[]` |

**For every role the Functional version lacks: the evidence was PRESENT and
went UNREAD.** Not downweighted, not gated — unread. The Functional résumé
names Sales Cloud, Service Cloud, Flow Builder, Salesforce, SOQL, Lightning
Experience, Experience Builder and Lightning App Builder, a strict superset of
the BA version's products. `matching_rows` saw one term, because the other
thirteen were inside `&`-compounds nothing atomised.

It is **not** because the headline or target field changed. Part 7 proves that
directly.

---

## Part 4 — Shared-concept weight diff

Weights come from `skill_evidence.assess_all`: a tier from **where in the
document the concept appears**, then the market may move it one step inside the
tier's band ([skill_evidence.py:957](skill_evidence.py#L957)).

```
CORE 5 (band 4-5) · STRONG_SECONDARY 4 (3-4) · SUPPORTING 3 (2-3) · BACKGROUND 2 (1-2)
```

| concept | in F? | in BA? | provenance (F) | provenance (BA) | tier F | tier BA | w F | w BA | why different |
|---|---|---|---|---|---|---|---|---|---|
| **salesforce** | yes | yes | skills-line compound + certification + swallowed tools block | **work bullet** | BACKGROUND | **CORE** | **1** | **4** | **row 8: "CRM solutions" vs "Salesforce … solutions"** — evidence, plus one band from the `TOOLS & TECHNOLOGIES` heading |
| **service cloud** | yes | yes | swallowed tools block | **work bullet** | BACKGROUND | **CORE** | **2** | **5** | same |
| **sales cloud** | yes | yes | swallowed tools block | **work bullet** | BACKGROUND | **CORE** | **2** | **5** | same |
| **flow builder** | yes | yes | swallowed tools block | skills block | BACKGROUND | SUPPORTING | **2** | **3** | **heading alone** — see below |
| requirement gathering | yes | yes | **project bullet** (Greaves) | skills + summary | STRONG_SECONDARY | SUPPORTING | **4** | 3 | F has projects; BA does not. Correct. |
| reports / dashboards | yes | yes | work bullet | work bullet | CORE | CORE | 4 | 4 | **identical** ✓ |
| uat | yes | yes | work bullet | work bullet | CORE | CORE | 5 | 5 | **identical** ✓ |
| gap analysis | yes | yes | work bullet | work bullet | CORE | CORE | 5 | 5 | **identical** ✓ |
| go-live | yes | yes | work bullet | work bullet | CORE | CORE | 5 | 5 | **identical** ✓ |
| brd / frd | yes | yes | summary + skills | summary + skills | SUPPORTING | SUPPORTING | 3 | 3 | **identical** ✓ |
| agile / sdlc | yes | yes | compound only | atomic in skills | BACKGROUND | SUPPORTING | 1–2 | 2–3 | atomisation, as above |
| user story | yes | yes | compound | work bullet | BACKGROUND | SUPPORTING | 2 | 3 | evidence + atomisation |
| soql | **no** | yes | in the swallowed block, never extracted | skills block | — | SUPPORTING | — | 3 | extraction, not weighting |
| change requests | yes | **no** | **work bullet** | absent | CORE | — | **5** | — | genuine: F-only bullet |
| crm | yes | **no** | **work bullet** | absent | CORE | — | **4** | — | genuine: F says CRM where BA says Salesforce |
| procurement | yes | **no** | **project bullets** | absent | STRONG_SECONDARY | — | **4** | — | genuine: F-only projects |

### Weight changes caused by a heading alone

`TOOLS & TECHNOLOGIES` is not in `skill_evidence._HEADINGS`
([skill_evidence.py:99](skill_evidence.py#L99)), so it opens no section and its
contents are read as a continuation of `CERTIFICATIONS`. Renaming that one line
to `TECHNICAL SKILLS` and changing **nothing else** in the Functional résumé:

```
concept        as shipped              heading recognised
flow builder   BACKGROUND  2      ->   SUPPORTING  3
sales cloud    BACKGROUND  2      ->   SUPPORTING  3
salesforce     BACKGROUND  1      ->   SUPPORTING  2
service cloud  BACKGROUND  2      ->   SUPPORTING  3

concepts moved by the heading alone: 4 of 36
```

**Called out explicitly, as the brief asks: `flow builder` differs between the
two résumés for no reason except the heading.** The other three would still
differ, by two bands rather than three, because of the CRM/Salesforce bullet.

### The UI's flat weights

The Functional profile is **not** uniformly weighted — it carries CORE/5 on
`change requests`, `gap analysis`, `go-live`, `uat` and CORE/4 on `crm`,
`reports`, `dashboards`. What the review screen showed at weight 3 is the
fourteen **model-reported** skills, all of which are compounds no tier can lift
above SUPPORTING because a compound is never found in a work bullet — the
bullets say "reports and dashboards", not "reports & dashboards". The
differentiated weights went to the twenty-two skills `skill_scan` recovered,
which sort below them on the screen.

So: the flatness the screenshot shows is real, it is the same compound defect
seen from the UI, and the engine's own answer underneath is not flat.

---

## Part 5 — Title / target-field dependence

The suspected architecture is

```
headline → target_field → role families → allowed roles → skill weighting
```

**That chain does not exist in the shipped engine.** Traced, every consumer of
`target_field` in the repo:

| consumer | file | shipped? |
|---|---|---|
| the EMPLOYMENT prompt's `relevant` boolean, per row | [local_extract.py:262](local_extract.py#L262) | **yes** |
| `_summary` / `_notes` — the docstring the review screen prints | [local_profile.py:176](auto-apply/local_profile.py#L176) | **yes, display only** |
| `role_signals`, recorded and marked `grounded: False` | [local_extract.py:494](local_extract.py#L494) | carried, **unread** |
| `title_gate._target` — widens the ATS gate when corroborated | [title_gate.py:242](title_gate.py#L242) | **no** — `SWEEP_CANDIDATE_TITLE_GATE` off |
| `family_centrality.target_families` — **V3 Fix B** | [family_centrality.py:42](family_centrality.py#L42) | **no** — `SWEEP_FAMILY_CENTRALITY_GATE` off |
| `hard_drop.restore` corroboration | [hard_drop.py:160](hard_drop.py#L160) | **no** — `SWEEP_CANDIDATE_HARD_DROP` off |
| `role_evidence.build`'s stated target | [role_evidence.py:510](role_evidence.py#L510) | **no** — `SWEEP_ROLE_EVIDENCE` off |

`local_search.fields_for` — which produces `role_keywords`, `title_hints` and
the ranking — **never receives `target_field` at all**. It is handed
`{"skills", "employment"}` and nothing else, deliberately
([local_profile.py:203](auto-apply/local_profile.py#L203): *"fields_for() must
not see role intent"*).

**So in production today, `target_field` influences exactly two things: which
employment rows count toward `years_experience`, and a sentence on the review
screen.** Both résumés yielded 20 months and 1 year, so it influenced nothing
measurable in this pair.

**V3 Fix B is not responsible. It is not running.** Neither is the title gate,
the role-evidence gate, hard-drop restoration, the orphan guard, canonical
revalidation, the attachment guard or semantic scope. This was established by
reading `render.yaml` and each module's `FLAG`, and confirmed by Part 7's
measurement.

The terms the brief asks about — *title-alone evidence*, *title promoters*,
*target-field corroboration*, *held-title-derived family*, *core family*,
*peripheral family* — all exist, all in flag-gated V3 modules, all inert.

---

## Part 6 — Section-heading invariance

One canonical body (the BA résumé). Only the named label changes; every byte
below the heading is identical. Full production pipeline, 13 runs.

### 6a. The classifier's own table

| label | opens | | label | opens |
|---|---|---|---|---|
| `SKILLS` | `skills` ✓ | | `PROFESSIONAL EXPERIENCE` | `work` ✓ |
| `CORE COMPETENCIES` | `skills` ✓ | | `WORK EXPERIENCE` | `work` ✓ |
| `TECHNICAL SKILLS` | `skills` ✓ | | `EMPLOYMENT` | `work` ✓ |
| `KEY SKILLS` | `skills` ✓ | | `WORK HISTORY` | `work` ✓ |
| **`EXPERTISE`** | **None** ✗ | | **`CAREER HISTORY`** | **None** ✗ |
| **`TOOLS & TECHNOLOGIES`** | **None** ✗ | | **`EMPLOYMENT HISTORY`** | **None** ✗ |
| **`AREAS OF EXPERTISE`** | **None** ✗ | | `PROFESSIONAL SUMMARY` / `SUMMARY` / `PROFILE` / `ABOUT` / `OBJECTIVE` | `summary` ✓ |
| **`SKILL SET`** | **None** ✗ | | **`CAREER OBJECTIVE`** | **None** ✗ |

`CAREER HISTORY` fails because WORK's pattern is
`(professional|work|relevant|industry)?\s*(experience|employment|history|career)`
— `history` alone matches and `career` alone matches, but `career history` has
no prefix the group admits.

### 6b. End-to-end, per label family

| variant | role J | hint J | skill J | mean \|Δw\| | max Δw | roles |
|---|---|---|---|---|---|---|
| **summary: PROFESSIONAL SUMMARY → SUMMARY** | 1.000 | 1.000 | 1.000 | 0.00 | 0 | 11→11 |
| **summary: → PROFILE** | 1.000 | 1.000 | 1.000 | 0.00 | 0 | 11→11 |
| **summary: → ABOUT** | 1.000 | 1.000 | 1.000 | 0.00 | 0 | 11→11 |
| **work: PROFESSIONAL EXPERIENCE → WORK EXPERIENCE** | 1.000 | 1.000 | 0.882 | 0.00 | 0 | 11→11 |
| **work: → EMPLOYMENT** | 1.000 | 1.000 | 0.882 | 0.00 | 0 | 11→11 |
| **work: → CAREER HISTORY** | 1.000 | 1.000 | 0.882 | **0.53** | **2** | 11→11 |
| **skills: CORE COMPETENCIES → EXPERTISE** | 1.000 | 1.000 | 0.900 | 0.00 | 0 | 11→11 |
| **skills: → SKILLS** | **0.091** | **0.000** | 0.641 | 0.24 | 1 | **11→1** |
| **skills: → TECHNICAL SKILLS** | **0.091** | **0.000** | 0.595 | 0.24 | 1 | **11→1** |
| **tools: TECHNICAL SKILLS → TOOLS & TECHNOLOGIES** | 1.000 | 1.000 | 0.933 | **0.25** | **1** | 11→11 |

**Summary labels are perfectly invariant. Three others are not.**

#### `TOOLS & TECHNOLOGIES` — one band off everything below it

The same defect Part 4 measured on the Functional résumé, isolated on a
byte-identical BA body. Renaming `TECHNICAL SKILLS` to `TOOLS & TECHNOLOGIES`
demotes every concept in that block from SUPPORTING to BACKGROUND:

```
soql 3→2   experience builder 3→2   reports & dashboards 3→2
microsoft excel 3→2   microsoft powerpoint 3→2   english 3→2   hindi 3→2
```

Roles and hints are untouched.

#### `CAREER HISTORY` — two bands off every professional claim

No work section opens, so no concept can satisfy the `work + INCIDENTAL_USE →
CORE` rule. Every one of the eight CORE concepts falls to SUPPORTING:

```
uat 5→3   sales cloud 5→3   service cloud 5→3   gap analysis 5→3
go-live 5→3   salesforce 4→2   reports 4→2   dashboards 4→2
```

Roles and title hints are untouched — the damage is confined to weighting —
but a candidate who writes `CAREER HISTORY` has their entire employment history
read as a list of claims.

#### `SKILLS` — an 11-role profile becomes a 1-role profile

`role_jaccard 0.091`, `title-hint jaccard 0.000`, on a byte-identical body.

The classifier is not at fault: `SKILLS`, `CORE COMPETENCIES` and `TECHNICAL
SKILLS` all map to `skills` correctly. What changes is **which of the
document's two skill blocks the model reads**:

```
CORE COMPETENCIES + TECHNICAL SKILLS  → model reads TECHNICAL SKILLS (products)   11 roles
EXPERTISE         + TECHNICAL SKILLS  → model reads TECHNICAL SKILLS (products)   11 roles
SKILLS            + TECHNICAL SKILLS  → model reads SKILLS           (phrases)     1 role
TECHNICAL SKILLS  + TECHNICAL SKILLS  → model reads the first, plus label-prefixed
                                        junk: 'salesforce: sales cloud, service
                                        cloud, experience builder, flow builder,
                                        reports & dashboards' as ONE skill         1 role
```

Two labels that both mean "skills" send the model to different blocks. The
extraction preference is soft; the collapse is not, because the compound defect
of Part 2 gives the downstream path zero tolerance for the phrase-shaped answer.

**The `SKILLS` / `CORE COMPETENCIES` difference the brief singled out is
therefore real and is worth 10 of the 11 roles — but the mechanism is model
section-selection amplified by compound atomisation, not the classifier.**

---

## Part 7 — Headline sensitivity

Same body. Only the line under the name changes.

| headline | target_field | roles | role J | hint J | mean \|Δw\| |
|---|---|---|---|---|---|
| `SALESFORCE BUSINESS ANALYST` (baseline) | `salesforce business analysis` | 11 | — | — | — |
| `BUSINESS ANALYST` | `business analysis` | 11 | **1.000** | **1.000** | **0.00** |
| `FUNCTIONAL CONSULTANT` | `functional consulting` | 11 | **1.000** | **1.000** | **0.00** |
| `SALESFORCE FUNCTIONAL CONSULTANT` | `salesforce consulting` | 11 | **1.000** | **1.000** | **0.00** |

**The headline changes `target_field` and nothing else.** Identical roles,
identical hints, identical weights, identical extracted skills, to the string.

Some change was expected here and would have been legitimate. **There is
none.** The declared identity is currently inert.

---

## Part 8 — Employment-title sensitivity

Same body. Only the title on the employment line changes.

| employment title | roles | role J | the one role that differs | weights moved |
|---|---|---|---|---|
| `Salesforce Functional Consultant` (baseline) | 11 | — | `salesforce functional consultant` | — |
| `Functional Consultant` | 11 | 0.833 | `functional consultant` | **none** |
| `Business Analyst` | 11 | 0.833 | `business analyst` | **none** |

Exactly one role changes — the held title itself, passed through `from_resume`
— and the ten corpus-derived roles are unmoved because they come from skills.
**Proportionate, and the best-behaved layer in the engine.**

---

## Part 9 — Evidence-only diagnostic baseline

Headline removed, employment title removed, the summary's identity clause
removed. Bullets and skills untouched. Diagnostic only.

| body | what the model inferred from the work evidence | outcome |
|---|---|---|
| BA | **"Salesforce Implementation Specialist"** | **Escalated** |
| Functional | **"CRM Implementation Specialist"** | **Escalated** |

```
Escalated: employment: 1 of 1 rows are not supported by the document
  (title 'Salesforce Implementation Specialist' is not in the document);
  years_experience: model said 1 with no employment rows extracted to support it
```

Two findings.

1. **The evidence alone names a coherent and reasonable profession** — an
   implementation specialist, which is what both documents describe. The model
   can read the career without being told what it is.
2. **The engine cannot use that answer.** `check_grounding` requires an
   extracted title to appear verbatim, so an inferred one is always rejected
   and the profile fails closed. There is no evidence-only path; a declared
   title is load-bearing for whether a profile exists at all. Fail-closed is
   the right default — but it means the anchor the product principle asks for
   is not available to the engine today.

3. And the diagnostic settles the semantic question: stripped of every label,
   the two bodies still name **different** professions — Salesforce vs CRM —
   because the bullets themselves differ (Part 1 row 8). **The pair is not
   semantically equivalent, and no invariance test should demand that it be.**

---

## Part 10 — Stability metrics

```
role_jaccard   |A ∩ B| / |A ∪ B| over final role_keywords
query_jaccard  the same over title_hints
skill_jaccard  the same over skill_weights keys
weight_delta   mean |w_A − w_B| over concepts BOTH profiles carry, and the max
family_delta   not measurable in production — role_evidence is flag-off
gate_delta     not measurable in production — title_gate is flag-off
```

**Determinism control.** The BA fixture run twice, separately, through the full
pipeline: role keywords, extracted skills and every weight identical. At
temperature 0 the engine is reproducible, so every delta in this audit is
causal.

| comparison | kind | role J | query J | skill J | mean \|Δw\| | max Δw | verdict |
|---|---|---|---|---|---|---|---|
| summary label ×3 | pure presentation | **1.000** | **1.000** | **1.000** | 0.00 | 0 | **stable** ✓ |
| work label → EMPLOYMENT / WORK EXPERIENCE | pure presentation | 1.000 | 1.000 | 0.882 | 0.00 | 0 | **stable** ✓ |
| work label → CAREER HISTORY | pure presentation | 1.000 | 1.000 | 0.882 | **0.53** | **2** | **unstable** ✗ |
| tools label → TOOLS & TECHNOLOGIES | pure presentation | 1.000 | 1.000 | 0.933 | **0.25** | 1 | **unstable**, but inside the threshold |
| skills label → EXPERTISE | pure presentation | 1.000 | 1.000 | 0.900 | 0.00 | 0 | stable ✓ |
| **skills label → SKILLS** | **pure presentation** | **0.091** | **0.000** | 0.641 | 0.24 | 1 | **severe** ✗✗ |
| headline ×3 | declared identity | 1.000 | 1.000 | 0.935–1.0 | 0.00 | 0 | **inert** — under-sensitive |
| employment title ×2 | semantic | 0.833 | 1.000 | 1.000 | 0.00 | 0 | **proportionate** ✓ |
| **real pair, F vs BA** | mixed | **0.000** | **0.000** | **0.347** | **0.88** | **3** | see Part 13 |

Suggested thresholds, from the measured distribution rather than from taste:
pure-presentation variants should hold `role_jaccard ≥ 0.9` and
`max |Δw| ≤ 1`. **Three of the ten pure-presentation variants fail that today**
— `SKILLS`, `TECHNICAL SKILLS` (roles) and `CAREER HISTORY` (weights).

`TOOLS & TECHNOLOGIES` passes the numeric threshold and is still a defect: it
demotes seven concepts by one band each. A one-band threshold cannot catch a
one-band error, so Fix 2 should be verified by the heading table directly
(which the shipped test file does) rather than by this metric.

---

## Part 11 — Earliest causal divergence

Two independent defects. The second amplifies the first from a preference into
a collapse.

### Root cause 1 (amplifier, and the one that matters) — `skill_concepts.split_compound`

**Layer: skill evidence / concept identity. Not the parser, not target-field
inference, not family centrality, not the query generator, not the title gate.**

An `&`-joined skill is atomised only when every half is in `LOOKUP`. The corpus
vocabulary — 367 terms measured from 22,806 real listings — is never consulted,
so `sales cloud & service cloud` reaches `matching_rows` as one string that
matches nothing, while both halves individually match hundreds. Thirteen of the
Functional résumé's fourteen skills die this way, `matching_rows(need=2)`
returns **0**, and `role_keywords` falls back to the held job title alone.

Everything the brief suspected is downstream of this and is exonerated by it.

### Root cause 2 (trigger) — model section-selection

**Layer: parser (`local_extract.read`).** When a document has two skill-shaped
blocks, which one the model reads depends on the labels. `CORE COMPETENCIES` +
`TECHNICAL SKILLS` sends it to the products; `SKILLS` + `TECHNICAL SKILLS`
sends it to the phrases. Soft on its own — with root cause 1 fixed, the phrase
answer still yields 11 roles — but it is what selects which input the amplifier
sees.

### Root cause 3 (independent, weights only) — `skill_evidence._HEADINGS`

**Layer: section classifier.** `TOOLS & TECHNOLOGIES` and `CAREER HISTORY` open
no section. The first costs one band on four concepts in the real pair; the
second costs two bands on every professional claim. Independent of 1 and 2, and
affects weights only — never roles.

### Not the cause

| suspected | verdict |
|---|---|
| target-field inference | **exonerated** — reaches nothing but the summary line and row relevance (Part 5) |
| held-title weighting | **exonerated** — exactly one role, proportionate (Part 8) |
| role evidence / family centrality / V3 Fix B | **not running** (Part 5) |
| title gate | **not running** |
| query generator | **correct** — given real skills it produced the right 11 roles from the Functional document too |
| skill weighting arithmetic | **correct** — tiers and bands did exactly what the evidence supported |

---

## Part 12 — The product principle

> Declared identity should guide the search, but work evidence should anchor it.

The engine fails this in **both** directions at once.

| the principle says | the engine does |
|---|---|
| declared identity should **guide** | **it guides nothing.** Three different headlines produced byte-identical roles, hints and weights (Part 7). |
| work evidence should **anchor** | **it anchors nothing when the skills line is prose-shaped.** Sales Cloud, Service Cloud, Flow Builder and Salesforce are all in the Functional document and all invisible to search (Part 2). |
| strongly demonstrated skills should not disappear because the headline changed | **true today, for the wrong reason** — the headline has no power. They disappear because of a `&`. |
| unsupported adjacent professions should not appear from a broad domain term | **violated.** `revenue operations` (6 listings), `product owner` (3), `salesforce engineer` and `salesforce techno functional consultant` are admitted on corpus co-occurrence with no supporting evidence in either document. This is exactly what `role_evidence` was built to veto, and it is flag-off. |

Evidence anchoring is currently a property of how the candidate punctuated
their skills line.

---

## Part 13 — Verdict on the real pair

**1. Which differences are justified?**

- `salesforce`, `sales cloud`, `service cloud` at CORE in BA and not in F —
  **justified, two of the three bands.** The BA résumé says "configured
  Salesforce Sales Cloud and Service Cloud solutions"; the Functional résumé
  says "configured CRM solutions". Different claims.
- `change requests` 5, `crm` 4, `procurement` 4 in F only — **justified.** F has
  bullets and five named client projects that BA does not.
- `requirement gathering` 4 in F vs 3 in BA — **justified.** F evidences it in a
  project; BA only lists it.
- `functional consultant` vs `salesforce functional consultant` as the held role
  — **justified.** That is what each document says the job was called.

**2. Which differences are disproportionate?**

- **1 role vs 11.** Not justified by any evidence difference. The Functional
  résumé names a superset of the BA résumé's products; `keywords_for` returned
  `[]` because thirteen skills were `&`-compounds. **Root cause 1.**
- **0 title hints vs 38.** Same cause. The Functional profile has no ATS gate at
  all.
- **`flow builder` 2 vs 3.** One band, entirely from `TOOLS & TECHNOLOGIES`
  opening no section. **Root cause 3.**
- **One band of the salesforce / sales cloud / service cloud gap** (the third of
  the three) — same heading defect.

**3. Which roles in the BA profile are strongly supported by BOTH documents?**

`salesforce functional consultant`, `functional consultant, salesforce core`,
`consultant salesforce` (the held title and its variants), `salesforce business
analyst`, `salesforce administrator` (certification in both), `salesforce sales
cloud`. Six of eleven. The Functional résumé's own atomised counterfactual
produced ten of these eleven.

**4. Which exist mainly because of packaging?**

None, in the sense the brief means — **no role in the BA profile is there
because of the headline.** But four are there on thin corpus co-occurrence with
no résumé support in either document:

- `revenue operations` — 6 listings, no revenue-ops evidence anywhere
- `product owner` — **3 listings**, no product-ownership evidence anywhere
- `salesforce engineer` — retrieved by SOQL, a skills-list claim; no
  construction verb governs a software artefact anywhere in either document
- `salesforce techno functional consultant` — same; "techno" asserts
  development the résumé does not show
- (`sf -data cloud` is additionally a malformed query string)

These are the corpus over-reaching, not the packaging. `role_evidence` would
veto the two development-shaped ones; it is off.

**5. Which strong skills should have similar weights in both?**

`salesforce`, `sales cloud`, `service cloud`, `flow builder`, `soql`,
`experience builder`. All six are equally documented — the Functional résumé
lists strictly more of them. `reports`, `dashboards`, `uat`, `gap analysis`,
`go-live` and `brd`/`frd` **already do** match exactly, which is the engine
working. `flow builder` should match and does not, by one band, for no reason
but a heading. `salesforce` / `sales cloud` / `service cloud` should be closer
than 3 bands: one band is the heading, two are real.

**6. Is 1 role vs 9 defensible?**

**No.** The same document, with `&`-compounds atomised against the market
vocabulary and nothing else changed, yields eleven roles — essentially the BA
set. A person's job search collapsing to a single query because they wrote
"Sales Cloud & Service Cloud" instead of "Sales Cloud, Service Cloud" is not a
defensible product behaviour.

**7. Is 14 uniformly-weighted skills vs 9 differentiated ones defensible?**

**Partly, and the flatness is a display artefact.** The Functional profile
underneath is not flat — `change requests` 5, `gap analysis` 5, `go-live` 5,
`uat` 5, `crm` 4, `reports` 4, `dashboards` 4, `procurement` 4. What the review
screen showed at weight 3 is the fourteen *compound* strings, which can never
exceed SUPPORTING because no work bullet contains the compound. The
differentiated weights are on the twenty-two scanner-recovered concepts, which
sort below them.

So the visible flatness is the same compound defect seen from the UI. Fixing
atomisation fixes the screen too.

---

## Part 14 — Proposed fixes

Not implemented. Ordered by how much each buys.

### Fix 1 — admit the corpus vocabulary as a second registry in `split_compound`

**Layer: `skill_concepts.split_compound`. One condition.** Split when every half
is in `LOOKUP` **or** in the market vocabulary. The positive-evidence rule is
preserved — an unknown half still blocks the split, so "Research & Development"
stays whole — but a term 22,806 real postings use as a skill counts as
evidence that it is one.

Measured: Functional goes 1 role → 11, 0 title hints → 38. Nothing else in this
audit moves.

Two things to resolve before implementing, neither settled by this audit:
- `split_compound` is currently **pure** (no corpus, no I/O). Passing a
  vocabulary changes that signature and `identities()`'s with it.
- It runs in two places at different times: `identities()` before search and
  `split_compounds()` in `_finish` after. They must not disagree — that
  disagreement is audit defect V3-C1, already fixed once.

### Fix 2 — teach `_HEADINGS` the four labels real résumés use

**Layer: `skill_evidence._HEADINGS`.** Add `tools`-prefixed technology blocks,
`expertise` / `areas of expertise` / `skill set` → `skills`; `career history` /
`employment history` → `work`; `career objective` → `summary`.

`CAREER HISTORY` is the urgent one: it costs two bands on every professional
claim in the document. `TOOLS & TECHNOLOGIES` costs one band on four concepts
in this pair.

Risk is low and one-directional — these labels currently open nothing, so
recognising them can only move concepts from a weaker section to their real
one. The existing `AREAS OF` / `SKILL SET` cases should be added in the same
edit or they rot back in.

### Fix 3 — decide whether the declared identity should do anything

Not a bug; a product decision the audit surfaces. `target_field` is ungrounded
and correctly distrusted, and the three consumers that would let it guide the
search — `title_gate._target`, `family_centrality.target_families`,
`hard_drop.restore` — are all written, all tested and all flag-off. The engine
is under-sensitive to declared identity, not over-sensitive. Whether to turn
any of them on is the V3 sequence's question, not this audit's.

### Fix 4 — the corpus tail (separate concern)

`product owner` on 3 listings, `revenue operations` on 6, `sf -data cloud` as a
malformed string. `MIN_LISTINGS = 10` is denominated in evidence weight, not
rows, so the floor is softer than its name. Worth a separate look; out of scope
here.

### Explicitly NOT proposed

No change to `target_field`, role evidence, family centrality, skill weights,
queries or the title gate. The divergence is upstream of all six.

---

## Part 15 — Regression-test plan

### Shipped with this audit

[auto-apply/tests/test_presentation_stability.py](auto-apply/tests/test_presentation_stability.py)
— 13 tests, no model required, all passing. It locks the two deterministic
defects **as they ship**, each naming the behaviour that should replace it, so
fixing the engine fails the test loudly rather than passing a stale
expectation.

- `split_compound` refuses `sales cloud & service cloud` and `agile & sdlc`
- `identities()` leaves all fourteen Functional skills compound
- the BA list needs no splitting — the contrast that proves the pair differs
  only in form
- the heading table's recognised synonyms, and the seven it misses
- `TOOLS & TECHNOLOGIES` reads as certification text; renaming it to `TECHNICAL
  SKILLS` moves the concept one band
- `CAREER HISTORY` costs `sales cloud` two bands against `PROFESSIONAL
  EXPERIENCE`

### To add when Fix 1 lands

1. **The counterfactual as an assertion.** Functional's fourteen skills, through
   `identities()`, must yield ≥6 corpus-vocabulary terms and
   `matching_rows(need=2) > 0` against a frozen corpus
   (`local_search.frozen_market` already exists for this).
2. **Both call sites agree.** `identities(x)` and `split_compounds` must produce
   the same atoms for the same input — the V3-C1 property, asserted rather than
   commented.
3. **No invented concepts.** `Research & Development`, `Foo & Bar`, `C++`,
   `CI/CD`, `Node.js` still survive whole.

### To add when Fix 2 lands

4. Each newly recognised label opens its section, and the four Functional
   concepts move BACKGROUND → SUPPORTING.
5. A `CAREER HISTORY` body reaches CORE.

### The end-to-end sweep

The 24-run variant sweep behind Parts 6–9 needs a live model and ~13 minutes,
so it is not a unit test. It belongs in `bench/` as an invariance harness with
the thresholds Part 10 proposes: pure-presentation variants hold
`role_jaccard ≥ 0.9` and `max |Δw| ≤ 1`. Three of the ten pure-presentation
variants fail that today; all three should pass after Fixes 1 and 2. The
`TOOLS & TECHNOLOGIES` case passes the metric and is covered by the unit tests
instead — see Part 10.

Determinism is not an obstacle — temperature is 0 and the byte-identical
re-run reproduced every field exactly.

### One caveat on reproduction

The measurements here give the BA résumé **11** roles where the reported
screenshot shows **9**; the extra two are `salesforce engineer` and
`sf -data cloud`. The corpus has grown since that run (22,806 rows now). Every
comparison in this audit is between runs against the same corpus, so the
conclusions are unaffected, but a frozen corpus is what an invariance harness
should use.

---

# Production V3 matrix replay

Everything above Part 15 was measured with the V3 step flags **absent**, on the
authority of `render.yaml`. That was wrong: `render.yaml` is not the live
runtime. This section replays the decisive measurements under the matrix
actually in force, on the same corpus (22,806 listings), at `bf7d97e`. No
engine code was changed.

```
SWEEP_PROFILE_ENGINE_VERSION          v2        (production, unchanged)
SWEEP_ROLE_EVIDENCE                   1
SWEEP_CANDIDATE_TITLE_GATE            1
SWEEP_CANDIDATE_HARD_DROP             1
SWEEP_ORPHAN_ROLE_GUARD               0
SWEEP_CANONICAL_REVALIDATION          1
SWEEP_CANONICAL_FALLBACK              discard
SWEEP_SEMANTIC_SCOPE                  1
SWEEP_SEMANTIC_SCOPE_NEGATION_VERBS   0
SWEEP_ROLE_ATTACHMENT_GUARD           1
SWEEP_FAMILY_CENTRALITY_GATE          1
```

Confirmed live in every run via each module's own `enabled()`.
`SWEEP_CANONICAL_FALLBACK` is read at **import time**
([canonical_guard.py:60](canonical_guard.py#L60)), so the harness sets the
whole matrix before the first import; a late assignment would be silently
ignored.

**The replay validates the matrix against the report.** Under V3 the BA résumé
yields **9 roles** — exactly the nine in the reported screenshot. The V3-off
measurement gave 11. The earlier runs were not reproducing production.

Determinism control under the matrix: the BA body run twice gave identical
roles, identical title gate and identical weights.

---

## R1 — A / B / C under the live matrix

**B** applies the proposed market-aware compound rule by monkeypatching
`skill_concepts.split_compound` **inside the harness only**. Production code is
untouched; `LOOKUP`, the parenthetical rule and `MIN_PART` still run first, and
an unknown half still blocks the split.

| | **A** Functional, current | **B** Functional, atomised | **C** BA, current |
|---|---|---|---|
| target_field | `functional consulting` | `functional consulting` | `salesforce business analysis` |
| headline title | `FUNCTIONAL CONSULTANT` | `FUNCTIONAL CONSULTANT` | `Salesforce Business Analyst` |
| employment title | `Functional Consultant` | `Functional Consultant` | `Salesforce Functional Consultant` |
| extracted skills | 14 compounds | 14 compounds | 11 atomic |
| concepts after `identities()` | 14, **none split** | **17, five split** | 11 |
| `role_evidence` supports | data_analytics **strong**, business_analysis **strong**, functional_consulting **strong**, project_delivery **strong**, product **strong**, it_administration weak, hr_recruiting weak | identical | data_analytics strong, business_analysis strong, functional_consulting strong, product strong, it_administration weak, project_delivery weak |
| `title_families` | `functional_consulting` | `functional_consulting` | `functional_consulting`, **`business_analysis`** |
| platforms / thin | `['salesforce']` / False | same | same |
| **CORE families** | `functional_consulting` | `functional_consulting` | **`business_analysis`, `functional_consulting`** |
| **PERIPHERAL** | business_analysis, data_analytics, product, project_delivery | same | data_analytics, product |
| peripheral titles recovered | **none** | business analyst, systems analyst, product owner | product owner |
| role gate rejected | none | none | **`salesforce engineer`** |
| hard-drop restored | none | none | none |
| canonical rejected | none | **`sf -data cloud`** | **`sf -data cloud`** |
| **final roles** | **1** | **10** | **9** |
| title hints | **0** | 40 | 38 |
| **title gate** | **10** | 32 | 37 |

### A → B: the compound fix, measured under V3

```
A  1 role   0 hints   10 gate entries
B  10 roles 40 hints  32 gate entries
```

B's roles: `salesforce administrator`, `salesforce business analyst`,
`functional consultant`, `salesforce techno functional consultant`,
`functional consultant, salesforce core`, `consultant salesforce`,
`salesforce sales cloud`, `product owner`, `revenue operations`,
`salesforce consultant`.

**The V3 guards do not close the gap, and they were never going to.** Every one
of them is reject-only — `role_evidence.filter_queries`,
`canonical_guard.revalidate` and `orphan_guard.filter_queries` can veto a query,
never propose one, and `hard_drop.restore` only restores what the seniority
lists deleted. A candidate whose skills never reached `matching_rows` has
nothing for any guard to act on. **Root cause 1 survives the replay unchanged
and is, if anything, better isolated: it is upstream of every layer that was
previously unmeasured.**

### A is worse under V3 than it was without it

With the V3 flags off, the Functional candidate's ATS gate was the legacy union
with `config.ATS_TITLE_HINTS` — a broad software floor. Under the live matrix
the gate is **candidate-specific and 10 entries long**:

```
application consultant · crm consultant · erp consultant · functional consultant
functional consulting · implementation consultant · solution consultant
solutions consultant · technical consultant · techno-functional
```

`global_floor_used: False` — the fallback did not fire, because one held title
is "candidate-specific evidence". Correct by Step 4's rule, and the result is a
Salesforce-certified administrator whose free-board scan contains the word
`salesforce` **zero times**. That is a consequence of root cause 1 reaching a
layer the first audit could not see.

---

## R2 — Headline invariance under V3

Same body, only the line under the name changes. All four runs under the live
matrix.

| headline | target_field | CORE families | final roles | role J | **gate size** | **gate J** |
|---|---|---|---|---|---|---|
| `SALESFORCE BUSINESS ANALYST` | `salesforce business analysis` | business_analysis, functional_consulting | 9 | — | 37 | — |
| `BUSINESS ANALYST` | `business analysis` | business_analysis, functional_consulting | 9 | **1.000** | 35 | **0.946** |
| `FUNCTIONAL CONSULTANT` | `functional consulting` | **functional_consulting only** | 9 | **1.000** | 30 | **0.763** |
| `SALESFORCE FUNCTIONAL CONSULTANT` | `salesforce consulting` | **functional_consulting only** | 9 | **1.000** | 30 | **0.763** |

### The earlier conclusion was half right, and the half that was wrong matters

**"The headline is inert" does NOT survive.** It survives for the paid-search
path and fails for the free-board gate.

* **Final roles: still perfectly invariant.** `role_jaccard 1.000` across all
  four headlines, as measured with the flags off. Queries come from skills and
  the held employment title, and no V3 layer changes that.
* **Core families and the title gate are NOT invariant.** Declaring
  `FUNCTIONAL CONSULTANT` instead of `SALESFORCE BUSINESS ANALYST` on an
  unchanged body removes eight gate entries — `business analysis`,
  `business systems analyst`, `process analyst`, `requirements analyst`,
  `systems analyst`, `salesforce business analyst`, `salesforce business`,
  `salesforce business analysis` — because `business_analysis` falls from CORE
  to PERIPHERAL and Fix B then re-admits only the one fragment the candidate's
  own queries already name (`business analyst`).

### Two distinct routes to CORE, and the headline drives both

`role_evidence.build` folds the **headline** into `title_families`
([role_evidence.py:512](role_evidence.py#L512): `by_title = _title_families(held + headline)`),
and `family_centrality.is_core` admits a strong family that is named by a held
title **or** corroborated by the stated target
([family_centrality.py:88](family_centrality.py#L88)). The replay separates them:

| headline | how `business_analysis` reached CORE |
|---|---|
| `SALESFORCE BUSINESS ANALYST` | **by title** — the headline is a title family |
| `BUSINESS ANALYST` | **by target** — `core_by_target: ['business_analysis']`; the headline changed `target_field`, and `target_families` matched it to the family vocabulary |
| `FUNCTIONAL CONSULTANT` | **neither** — demoted to peripheral |
| `SALESFORCE FUNCTIONAL CONSULTANT` | **neither** — demoted to peripheral |

**`target_field` is therefore load-bearing under the live matrix**, and the
Part 5 claim that it "reaches nothing but the summary line and per-row
relevance" is **retracted**. It is ungrounded and can only ever widen a gate,
which is the design — but it is not inert.

### Is this proportionate?

Yes. A gate Jaccard of 0.763 for a genuinely different declared profession is
the behaviour the product principle asks for, and it is the *only* place the
engine currently honours the declaration. The engine is still under-sensitive
to the headline on the search path (`role_jaccard 1.000`), not over-sensitive
anywhere.

---

## R3 — Thin role review under V3

| role | source evidence | family | role-evidence verdict | central / peripheral | canonical validation | **final** |
|---|---|---|---|---|---|---|
| **`salesforce engineer`** | 11 listings, retrieved by **SOQL** (8) — a skills-list claim | `software_engineering` | **REJECTED** — *"needs development; platform evidence exists (salesforce) but no substantive development ownership"*; `missing_work_modes: ['development']`, 0 refuted, 0 delegated | — (family not supported) | accepted | **DROPPED** ✓ |
| **`sf -data cloud`** | 6 listings, from fragment `data cloud` | `None` | not reached | — | **REJECTED** — *"a leading hyphen is a negation operator on every major board, so this string does not ask for the job it appears to name"* | **DROPPED** ✓ |
| **`salesforce techno functional consultant`** | **5 listings**; salesforce(5), **soql(5)**, flow builder(3) | `functional_consulting` | **KEPT** — the family is CORE by held title | core | accepted, from fragment `techno functional` | **KEPT** ✗ |
| **`revenue operations`** | **6 listings**; salesforce(6), sales cloud(6) | **`None`** | **KEPT** — an unrecognised family is a fail-open path | n/a | accepted | **KEPT** ✗ |
| **`product owner`** | **3 listings**; service cloud(3), salesforce(3), sales cloud(3) | `product` | **KEPT** — `product` has **strong** work-mode support | **peripheral**, and the one fragment Fix B re-admitted | accepted | **KEPT** ✗ |

**V3 removes two of the five, and the two it removes are the right two.** The
role-evidence gate did exactly what it was built for on `salesforce engineer`:
SOQL on a skills line is not development ownership, and the rejection record
quotes the reasoning rather than a score.

The three that survive are each a *different* fail-open path, not one weakness:

* `salesforce techno functional consultant` — "techno" asserts development, but
  `family_of` resolves the whole string to `functional_consulting`, which the
  candidate genuinely holds. The gate never sees a development claim to test.
  **A family-resolution blind spot, not a gate failure.**
* `revenue operations` — `family_of` returns `None`, and an unrecognised family
  is deliberately never rejected
  ([role_evidence.py:619](role_evidence.py#L619)). Working as designed; the
  design has no answer for a title outside `FAMILIES`.
* `product owner` — `product` is **strong** on work-mode evidence (requirement
  gathering, user stories, acceptance criteria, backlog-shaped work), so Fix B
  correctly classes it peripheral and correctly re-admits the one fragment the
  candidate's own queries already name. **This is Fix B working**, and the
  residual question is whether 3 listings should have produced the query at
  all — a corpus-tail concern (Part 14, Fix 4), not a V3 one.

`MIN_LISTINGS = 10` being denominated in evidence weight rather than rows
([local_search.py:74](local_search.py#L74)) remains the reason a 3-posting
fragment clears the floor. Unchanged by V3.

### One inconsistency the replay exposed

`title_gate`'s `own_hints_dropped` for **C** discards
**`salesforce administrator`** and **`salesforce sales cloud`** — both of which
are **final search roles** in the same profile. `salesforce administrator` is
the candidate's highest-lift query (39 listings) and they hold the
certification; it is dropped because `it_administration` is only *weak* support
and so never enters Fix B's `considered` set (`strong | by_title`).

So the paid-query path searches for `salesforce administrator` while the
free-board gate refuses to score it. The two paths disagree about the same
candidate. Out of scope here; worth its own look.

---

## R4 — Heading defect under the live matrix

Re-measured like-for-like — each run's own concept set and its own market
separations — with `SWEEP_SEMANTIC_SCOPE=1` and
`SWEEP_SEMANTIC_SCOPE_NEGATION_VERBS=0`.

**`TOOLS & TECHNOLOGIES` → `TECHNICAL SKILLS`** (Functional résumé, 36 concepts)

| concept | shipped | heading recognised |
|---|---|---|
| flow builder | BACKGROUND 2 | SUPPORTING 3 |
| sales cloud | BACKGROUND 2 | SUPPORTING 3 |
| salesforce | BACKGROUND 1 | SUPPORTING 2 |
| service cloud | BACKGROUND 2 | SUPPORTING 3 |

`moved: 4 of 36, total |Δw| 4` — **identical to the V3-off measurement.**

**`TECHNICAL SKILLS` → `TOOLS & TECHNOLOGIES`** (BA résumé, 30 concepts)
demotes seven: `soql`, `experience builder`, `reports & dashboards`,
`microsoft excel`, `microsoft powerpoint`, `english`, `hindi` — each
SUPPORTING 3 → BACKGROUND 2. `total |Δw| 7`. Identical to V3-off.

**`PROFESSIONAL EXPERIENCE` → `CAREER HISTORY`** (BA résumé, 30 concepts)

```
dashboards  CORE 4 -> SUPPORTING 2      sales cloud    CORE 5 -> SUPPORTING 3
gap analysis CORE 5 -> SUPPORTING 3     salesforce     CORE 4 -> SUPPORTING 2
go-live     CORE 5 -> SUPPORTING 3      service cloud  CORE 5 -> SUPPORTING 3
reports     CORE 4 -> SUPPORTING 2      uat            CORE 5 -> SUPPORTING 3

moved: 8 of 30   total |Δw|: 16
```

Identical to V3-off. **Root cause 3 is fully independent of the V3 matrix**, as
the first audit predicted, and it is the more expensive of the two headings:
two bands off every professional claim in the document.

### One thing the replay added

`assess_all` resolves overlapping concepts, so an unsplit compound **shadows**
its own atoms: with `salesforce administration & configuration` in the concept
list, occurrences of `salesforce` inside it are attributed to the longer
concept. Measured on the Functional résumé, `salesforce` is BACKGROUND 1 with
the compounds present and SUPPORTING 3 with only atomic concepts.

**The compound defect therefore costs twice** — once by starving search, and
again by shadowing the atomic concept's evidence in tiering. The first audit
attributed all of that gap to the heading and to the CRM/Salesforce bullet;
part of it is root cause 1 a second time.

---

## R5 — What changed and what survived

### Retracted

| earlier claim | replay finding |
|---|---|
| "Every V3 step flag is absent and therefore off" | **Wrong.** `render.yaml` is not the live runtime. Nine of ten flags are on. |
| Part 5: "`target_field` influences exactly two things: which employment rows count toward `years_experience`, and a sentence on the review screen" | **Retracted.** Under the live matrix it also reaches `family_centrality.target_families` → CORE families → title gate. Measured: the `BUSINESS ANALYST` headline keeps `business_analysis` CORE **by target**, not by title. |
| Part 7 / Part 10: "the headline is inert", `role_jaccard 1.000`, `max |Δw| 0` | **Half retracted.** Final roles and weights stay perfectly invariant. The **title gate does not**: gate Jaccard 1.000 → 0.946 → 0.763 across the four headlines, and CORE families go 2 → 1. |
| Part 5 / Part 11: "V3 Fix B is not responsible; it is not running" | **The premise was wrong; the conclusion holds.** Fix B *is* running. It is still not responsible for the 1-vs-9 gap — A and B differ only in compound atomisation and have identical family records. |
| Part 3 / Part 13: the BA profile has 11 roles including `salesforce engineer` and `sf -data cloud` | **Corrected to 9.** V3 drops both. The reported screenshot was right and the first audit's corpus-only run was not production. |
| Part 13 Q4: four roles admitted "on corpus co-occurrence with no supporting evidence" | **Narrowed to three.** `salesforce engineer` is rejected by the role gate. `product owner` is not unsupported — `product` has strong work-mode evidence and Fix B re-admits it deliberately. |

### Survived unchanged

| claim | replay evidence |
|---|---|
| **Root cause 1** — `split_compound` gating on `LOOKUP` alone starves `matching_rows` | A = 1 role, B = 10 roles, identical family records, under the full matrix. Strengthened: every V3 guard is reject-only, so none of them can reach a query that was never proposed. |
| **Root cause 3** — `TOOLS & TECHNOLOGIES` and `CAREER HISTORY` open no section | Byte-identical deltas under both configurations. Independent of V3. |
| The 1-vs-9 gap is not caused by the headline | Four headlines, one body, `role_jaccard 1.000`. |
| The employment title is proportionate | Held title only; unchanged. |
| Skill-weighting arithmetic is correct | Tiers and bands behaved exactly as the evidence supported in every run. |
| Determinism | Byte-identical input, two runs under the matrix: identical roles, gate and weights. |

### Strengthened

* **Root cause 1 is more expensive than first measured.** Beyond search
  starvation it shadows atomic concepts in `assess_all` (R4), and under the
  live matrix it collapses the ATS title gate to 10 consultant-shaped entries
  containing no Salesforce term at all (R1).
* **The `SKILLS` vs `CORE COMPETENCIES` finding is unaffected.** It acts on
  model section-selection, upstream of every flag in the matrix.

### Unchanged recommendation

Fix 1 and Fix 2 stand as written in Part 14, for the same reasons and with
larger measured benefit. Fix 3 needs rewording — the declared identity is
**not** inert; it drives the title gate and nothing else — but that is a
description change, not a different action.

**Two new items, both out of scope here:**

* the title gate drops `salesforce administrator` and `salesforce sales cloud`
  while the query list keeps them (R3);
* `family_of` returns `None` for `revenue operations` and resolves
  `salesforce techno functional consultant` to `functional_consulting`, so
  neither reaches the development test (R3).

### Replay reproduction

```
scratchpad/v3probe.py <fixture> <name> [--atomise]
```

Sets the full matrix before the first import, runs `generate_local`, then calls
`make_profile._title_gate` and `family_centrality.families` directly to capture
the gate and the core/peripheral split, which `generate_local` does not return.
`--atomise` monkeypatches `skill_concepts.split_compound` **in the harness
only**. Seven runs: A, B, C and the four headline variants. Nothing in the
repository was modified by the replay.
