# Free-search regression: a Business Analyst receiving engineering jobs

Forensic report. No fix implemented, no threshold changed, no production touched.

Traced at `ccfa857`, engine code at `31b746f` (Steps 3–8 present, all flags off
by default).

---

## The headline finding, before anything else

**This is not a V3 regression.** V3 has never been deployed — the release
readiness checkpoint (`1f14f89`) recorded both hosts as unconfigured, and
nothing has been applied since. The screenshots were produced by the **legacy
V2 path**.

Run on the same résumé, same corpus, same preferences:

| | V2 (live today) | V3 intended stack |
|---|---:|---:|
| title-gate size | 115 hints | **147 hints** |
| legacy 79-entry software floor present | **yes** | no |
| observed bad titles admitted | **5 of 8** | **8 of 8** |

V2 admits the engineering jobs through the **legacy 79-entry software floor**.
V3 removes that floor but replaces it with something wider for this candidate,
and additionally admits `Director of Recruiting`, `Technical Support Engineer`
and `Data Analyst`, which V2 rejected.

So the correct statement is: **a pre-existing V2 defect, which V3 in its
current form would make worse for this candidate rather than fix.** Both need
addressing; neither is caused by the other.

## Candidate role summary

Grounded, from the résumé's own structure:

```
target_field       business analysis
held titles        Senior Functional Consultant / Business Analyst
                   Functional Consultant / Business Analyst
                   Co-Founder, Sport Leader
title_families     functional_consulting  (the only family any held title names)
```

## Final V3 search queries

All 14, verbatim:

```
business analyst                          salesforce techno functional consultant
salesforce business analyst               product owner
salesforce administrator                  functional consultant, salesforce core
functional consultant                     consultant salesforce
supplier warranty recovery                salesforce sales cloud
excel utility                             salesforce consultant
revenue operations                        application development analyst
```

| classification | count | queries |
|---|---:|---|
| SUPPORTED | 10 | business analyst, salesforce business analyst, salesforce administrator, functional consultant, salesforce techno functional consultant, functional consultant salesforce core, consultant salesforce, salesforce consultant, salesforce sales cloud, product owner |
| NEUTRAL / BROAD | 3 | supplier warranty recovery, excel utility, revenue operations |
| UNSUPPORTED | 1 | application development analyst |
| **FORBIDDEN PROFESSION** | **0** | — |

Step 3 rejected 0, Step 5 restored 0, Step 7 rejected 0, Step 6 did not run.

> **Profile/query generation is not the source of these engineering results.**
> V3 never generated `software engineer`, `backend engineer`, `AI engineer`,
> `infrastructure engineer`, `Salesforce developer` or `data engineer`.

The two project-name queries (`supplier warranty recovery`, `excel utility`)
are noise from employment-title parsing, and `application development analyst`
is a corpus title. None of them retrieved the bad jobs — see next section.

## Free search call flow

```
Free Sweep
  → sources.fetch_free(ats_boards, feeds, keep_title, keep_location)
      for platform, boards in ats_boards:          <-- CONFIGURED COMPANY LIST
        for token, company in boards:
          ats.fetch(platform, token, company, keep_title, keep_location)
              → GET the company's WHOLE board
              → keep_title(title)  ==  scraper.is_dev_title
              → keep_location(loc)
  → score_job(row)
  → results
```

**The free path never uses the search queries.** It enumerates whole company
boards (GitLab, JumpCloud, New Relic, Twilio, Databricks, Precision AQ) and
filters what comes back by title substring. There is no per-query request, no
relevance retrieval, and no role-fit step.

That answers Phase 3 for every bad result at once:

| result | source | which query retrieved it | mechanism |
|---|---|---|---|
| Staff Software Engineer, Device Management | Lever / JumpCloud | **none** | whole-board enumeration |
| AI Engineer | Greenhouse / GitLab | **none** | whole-board enumeration |
| Staff Backend Engineer - Database Change Mgmt | Greenhouse / GitLab | **none** | whole-board enumeration |
| Staff Infrastructure Security Engineer | Greenhouse / GitLab | **none** | whole-board enumeration |
| Director of Recruiting, Engineering & IT | Greenhouse / GitLab | **none** | whole-board enumeration |
| Senior Salesforce Developer, Service Cloud | Greenhouse / New Relic | **none** | whole-board enumeration |

Every one passed exactly two filters: `is_dev_title` and location. Nothing
else stood between the company board and the results page.

## Title-gate trace

`scraper.is_dev_title` is **substring**, not word-boundary, and
`ATS_TITLE_EXCLUDE` rendered **empty** for this candidate, so it has no veto.

V3 gate composition — 147 hints, no cap:

| source | count |
|---|---:|
| grounded employment-title hints | e.g. `senior functional consultant / business analyst`, `sport leader`, `swr supplier warranty recovery` |
| corpus-derived (candidate's own `title_hints`) | e.g. `salesforce techno functional consultant`, `sr business systems analyst` |
| **role-family-derived** | **~120, the dominant source** |
| fallback (global floor) | 0 — never fired |

Admission of every observed bad title:

| title | V2 | V3 | hint that admitted it |
|---|---|---|---|
| Staff Software Engineer, Device Management | ADMIT | ADMIT | `software engineer` |
| AI Engineer | ADMIT | ADMIT | `ai engineer` |
| Staff Backend Engineer | ADMIT | ADMIT | `backend` |
| Staff Infrastructure Security Engineer | ADMIT | ADMIT | V2 `security engineer` / V3 `infrastructure` |
| Senior Salesforce Developer, Service Cloud | ADMIT | ADMIT | `salesforce developer`, `developer` |
| Director of Recruiting, Engineering & IT | no | **ADMIT** | `recruiting` |
| Technical Support Engineer 2 | no | **ADMIT** | `technical support`, `support engineer` |
| Data Analyst | no | **ADMIT** | `data analyst` |

The three V3-only admissions come from families V3 added.

### Why V3's gate is wider than the floor it replaced

`title_gate._families()` admits every family whose support is `strong` and
adds that family's **entire** board vocabulary. There is no cap on family
count and no requirement that the family be central to the candidate.

Step 3 marked **11 of 16 families strong** for this résumé:

```
business_analysis strong   functional_consulting strong   product strong
data_analytics    strong   hr_recruiting        strong   project_delivery strong
it_administration strong   ml_engineering       strong   sales strong
software_engineering strong   support strong            marketing weak
```

All 20 `software_engineering` titles, all 8 `ml_engineering`, all 10
`hr_recruiting`, all 10 `it_administration`, all 8 `data_analytics`, all 9
`support` and all 13 `sales` titles entered the gate.

### The single mode that granted both gated engineering families

`software_engineering` and `ml_engineering` are GATED — they require **strong**
work-mode evidence. Both were granted by one mode, `development`, strength
`strong` on three asserted hits:

```
USED  work  Cummins SWR   "Authored FSDs for the Supplier Warranty Recovery (SWR)
                           module, defining custom screens ..."
USED  work  Brillare      "authored FSDs across multiple DMS modules"
USED  work  Brillare      "Built custom reports and dashboards ..."
```

The first two are the defect. `role_evidence._governed_hits` fires when a
CONSTRUCT verb (`authored`) and an ARTEFACT (`module`, `screens`) appear in the
same governed clause. It does not check that the artefact is the verb's
**object**. He authored a *Functional Specification Document*; the module is
the object of "for". The third is Salesforce report/dashboard configuration,
not software development.

Three hits, two named entries → `strong` → both gated families admitted → 28
engineering titles in the gate.

## Ranking score breakdown

`score_job` computes: skill-concept matches + fullstack bonus + penalty terms +
seniority down-ranks. **There is no title-fit or profession-fit term anywhere.**

The candidate's rendered profile then switches off every negative signal:

```
SCORING["hard_drop_terms"]   = []     (default has 18: staff, director, manager, ...)
SCORING["penalty_terms"]     = {}
SCORING["fullstack_bonus"]   = 0
ATS_TITLE_EXCLUDE            = []
```

`config._overlay` merges `SCORING` one level deep, so these empty values
**replace** the defaults. The consequence is that for this candidate the score
is a pure sum of matched skill weights with nothing subtracting.

Reconstructed with the candidate's own 70 concepts:

```
AI Engineer                          10   users 5 + roles 5
Staff Backend Engineer               10   users 5 + roles 5
Staff Infrastructure Security Eng.   10   users 5 + roles 5
Staff Software Engineer (Device Mgmt) 12  users 5 + roles 5 + Agile 2
Technical Support Engineer 2          5   roles 5
Director of Recruiting, Eng & IT     17   users 5 + roles 5 + dashboards 4 + stakeholder mgmt 3
Senior Salesforce Developer          45   users, roles, profiles, permission sets, lightning,
                                          Jira, reports, dashboards, Salesforce, service cloud, ...
```

The observed 10 / 10 / 10 / 10 / 5 match the reconstruction exactly. **The
score of 10 on those jobs is literally `users` + `roles`.**

`Staff` and `Director` contributed no penalty because `hard_drop_terms` is
empty — with the default list they would have been dropped outright or taken
−15.

## Generic token findings

| concept | weight | tier | market sep | aliases |
|---|---:|---|---|---|
| `roles` | 5 | CORE | unmeasured | `roles` |
| `users` | 5 | CORE | unmeasured | `users` |
| `profiles` | 5 | CORE | 4 | `profiles` |
| `permission sets` | 5 | CORE | 4 | `permission sets` |
| `reports` | 4 | CORE | 2 | `reports` |
| `dashboards` | 4 | CORE | 2 | `dashboards` |

`roles` and `users` are single common English words, weighted 5 (CORE, the top
tier), with **no market separation measured**. They match the description of
essentially any software job. They are the entire score on four of the six bad
results, and they are the chips the UI displayed.

The tier is not wrong by the rules as written: both are genuinely used in named
work entries ("Configured users, roles, profiles, and permission sets aligned
to the client organisational hierarchy"). The evidence layer is behaving
correctly. What is missing is any notion that a concept can be well-evidenced
and still carry almost no discriminating power.

## Concept-fragmentation findings

The skills line

```
Users, Roles, Profiles & Permission Sets
```

— one Salesforce administration capability — became **four independent scoring
concepts**, each weighted 5, each matching independently. Nothing records that
they came from one compound, and nothing requires the Salesforce context to be
present for them to score.

`Sales Cloud` and `Service Cloud` survive as compounds. `Data Lake` did not
become a data-engineering signal. **`React Native` did not confer React Native
identity** — the phrase "Worked with the React Native team" produced no React
Native concept and no mobile family, which is the delegation rule working as
designed.

## Free vs paid differences

| | free | paid (Apify) |
|---|---|---|
| retrieval | whole-board enumeration of configured companies | per-query search |
| queries used | **no** | yes |
| title filter | `is_dev_title` (gate substring) | same gate applies |
| role-fit validation | none | none |
| ranking | `score_job` | `score_job`, identical |

The bug is **not** free-only in its scoring or filtering — both paths share
`is_dev_title` and `score_job`. What is free-specific is that free search has
no query-relevance step at all, so the title gate is doing the entire job of
deciding what profession the user sees. In paid search a bad gate is a second
line of defence behind a query; in free search it is the only line.

## Root cause attribution

| problem | contribution | evidence |
|---|---|---|
| Bad query generation | **no** | all 14 queries BA/Salesforce-functional; 0 forbidden |
| Free-source broad retrieval | **yes — primary** | `fetch_free` enumerates whole boards; queries unused |
| Title gate leakage | **yes — primary** | V2 legacy floor admits 5/8; V3 family expansion admits 8/8 |
| Missing result role-fit | **yes** | no candidate-aware check exists on retrieved titles |
| Generic skill inflation | **yes** | `users`+`roles` = the observed 10 exactly |
| Concept fragmentation | **yes** | one compound → 4 independent weight-5 concepts |
| Seniority mismatch | **yes** | profile renders `hard_drop_terms = []`; Staff/Director unpenalised |
| Ranking weakness | **yes** | `score_job` has no profession-fit term of any kind |

Ranked by how much each one has to change for the symptom to disappear:
**(1)** nothing validates profession after retrieval, **(2)** the gate is the
only filter and it is too wide, **(3)** two generic tokens supply the entire
score.

## Regression harness results

Gate verdict and title-only score, candidate's own concepts:

```
SUPPORTED / EXPECTED
  Business Analyst                    ADMIT  business analyst             2
  Salesforce Business Analyst         ADMIT  salesforce business analyst  6
  Salesforce Functional Consultant    ADMIT  functional consultant        7
  DMS Functional Consultant           ADMIT  functional consultant        8
  Business Systems Analyst            ADMIT  business systems analyst     0
RELATED BUT CONDITIONAL
  Product Analyst                     ADMIT  product analyst              0
  Systems Analyst                     ADMIT  systems analyst              0
  Salesforce Administrator            ADMIT  salesforce administrator     4
UNSUPPORTED / WRONG PROFESSION
  Software Engineer                   ADMIT  software engineer            0
  Staff Software Engineer             ADMIT  software engineer            0
  Backend Engineer                    ADMIT  backend                      0
  Infrastructure Security Engineer    ADMIT  infrastructure               0
  AI Engineer                         ADMIT  ai engineer                  0
  Salesforce Developer                ADMIT  salesforce developer         4
  Director of Recruiting              ADMIT  recruiting                   0
  Data Engineer                       no     -                            0
```

Eight of nine wrong-profession titles are admitted, and the title-only score
does not separate them from the right ones: `Business Systems Analyst`,
`Product Analyst` and `Systems Analyst` all score **0**, the same as
`Software Engineer`. On a real posting the difference is made up entirely by
description overlap, which is where `users` and `roles` win.

## Do not overfit: the same mechanism across the 56 personas

| | |
|---|---|
| non-software personas granted software/ml engineering **strong** | **1 of 39** (`tech_support`) |
| median supported families, non-software | 5 |
| median gate size, non-software | 34 hints |
| personas whose gate exceeds the 79-entry floor | **1 of 54** (`swe_frontend`, 87) |

**Hargun is an extreme outlier: 12 supported families and a 147-hint gate.**
No development persona is close. That is why the cumulative review measured
Step 4 as the batch's largest win (primary visibility 48% → 100%, candidates
admitting a forbidden family 54 → 21) and never surfaced this.

The reason is résumé length and density. The synthetic personas are short and
single-domain; this résumé is 7,800 characters describing onboarding, sales
cloud, administration, analysis, delivery, consulting, configuration, support
and documentation. Each activates a work mode, each mode reaching `strong`
admits a whole family vocabulary, and nothing caps the total.

**Any fix must be tested against the software personas**, where a wide
engineering gate is correct. Narrowing families globally would damage
`swe_frontend`, `swe_backend`, `swe_data_eng` and `adv_grad_cs`, whose gates
are legitimately 67–87 hints.

## Fix options

Not implemented. Listed at the layer the evidence points to, with the risk.

**A — Step 3 `development` attachment (query generation layer).**
Require the ARTEFACT to be the CONSTRUCT verb's actual object, so "authored
FSDs **for** the module" stops counting as development. Fixes the false
`software_engineering` / `ml_engineering` at source, which removes 28 titles
from this gate. *Benefit:* corrects the record every later layer reads.
*Risk:* medium — touches frozen Step 3, needs the full 56-persona revalidation.
*Scope:* all modes.

**B — cap or rank family expansion in Step 4 (title gate).**
Admit family vocabularies for at most the *n* best-evidenced families, or
require a held title / target-field corroboration before a family contributes
its full board vocabulary. *Benefit:* bounds the gate for dense résumés
without touching Step 3. *Risk:* low-medium; must not shrink the legitimate
67–87-hint software gates. *Scope:* all modes.

**C — word-boundary matching in `is_dev_title`.**
`infrastructure`, `backend`, `developer`, `recruiting` are matched as bare
substrings. *Benefit:* small and surgical. *Risk:* the docstring records why
substring was chosen (ReactJS, AI/ML, Devops); changing it needs its own
measurement. *Scope:* all modes. **Does not fix this case** — every admission
here is already a whole-word match.

**D — candidate-aware role-fit validation on retrieved titles.**
The missing layer. Today `role_evidence` is used only to veto *queries*; no
equivalent runs on job titles that arrive from a company board. *Benefit:*
addresses the structural gap rather than one symptom, and is the only option
that protects free search where there is no query at all. *Risk:* highest —
new layer, needs its own step, flag and holdout. *Scope:* could be free-only
first.

**E — generic-concept weighting.**
`roles` and `users` are CORE with no measured market separation. A rule that
caps the ranking contribution of a concept the corpus cannot separate would
remove the entire score from four of the six bad results. *Benefit:* large and
mechanical. *Risk:* must not demote genuinely rare-but-unmeasured skills;
`unmeasured` currently means "corpus has no signal", not "generic".
*Scope:* all modes. **Explicitly not a hardcoded stopword list.**

**F — profession-fit term in `score_job`.**
There is none today. *Risk:* highest blast radius; changes every score for
every user. Not recommended as the first move.

**G — restore negative signal for non-software profiles.**
`hard_drop_terms = []` and `ATS_TITLE_EXCLUDE = []` are rendered for this
candidate, so `Staff` and `Director` carry no penalty. Worth confirming
whether emptying these is intended for non-software candidates or is itself a
rendering defect. *Risk:* low. *Scope:* all modes.

## Recommended next experiment

Two measurements, before choosing any fix:

1. **Quantify the `development` false positive.** Run the Step 3 attachment
   check (option A) as a *measurement only* across all 54 personas plus this
   résumé: how many `development` hits have an artefact that is not the verb's
   object, and which families change. If it removes both engineering families
   here and changes nothing on the software personas, A is the cheapest correct
   fix and B becomes optional.

2. **Quantify generic-concept contribution.** For every persona, compute what
   share of each job's score comes from concepts with no measured market
   separation. If the share is large and concentrated in wrong-profession
   matches, E is justified on evidence rather than intuition.

Both are read-only. Neither should be started until this report is reviewed,
and the V2-versus-V3 correction at the top is the part that most needs a
decision: **the live beta has this defect today, with V3 switched off.**
