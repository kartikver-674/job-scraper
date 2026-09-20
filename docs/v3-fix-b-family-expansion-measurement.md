# Fix B — should a strong family hand over its whole profession vocabulary?

Read-only measurement and design recommendation. **Nothing is implemented.**
Each candidate rule is simulated by substituting `title_gate._families` for one
pass and restoring it; `title_gate.py` is never written to.

Baseline is Fix A (`376c4e6`) with the intended V3 stack plus
`SWEEP_ROLE_ATTACHMENT_GUARD=1`. 54 personas plus the regression candidate.

---

## 1. Current behaviour, confirmed in code

`title_gate._families()`:

```python
admitted = sorted(f for f, strength in supports.items()
                  if strength == "strong" or f in by_title)
for family in admitted:
    for fragment in FAMILY_TITLES.get(family, ()):
        ...
```

Strong support, or a held title naming the family, hands over the family's
**entire** board vocabulary. Confirmed absent: any family cap, any centrality
concept, any corroboration requirement beyond `strong`, any distinction between
a profession and an activity, any proportional expansion.

Two things the trace added that the brief did not assume:

- **`_target()` expands a whole family too.** A corroborated `target_field`
  contributes `FAMILY_TITLES[family]` on the same all-or-nothing basis.
- **`admitted` also gates the candidate's own corpus hints.** Narrowing
  families therefore has a second-order effect: fewer own-hints survive. Every
  number below includes it.

## 2. The distinction being measured

> "Evidence that I have performed this kind of activity"
> versus
> "Evidence that this is a profession I should search for."

For the regression candidate the four surviving bad titles come from
`hr_recruiting`, `it_administration`, `support` and `data_analytics` — families
whose evidence is real. Onboarding dealers is people work. Configuring users
and roles is administration. Post-go-live support is support. Reports and
dashboards are analysis. None of it makes him a recruiter, a sysadmin, a
support engineer or a data analyst.

## 3. Results

| variant | gate hints | primary vis. | forbidden | starv | fallbk | personas changed | H gate | H bad | H wanted | SW reg | non-SW reg |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **BASELINE** (Fix A) | 2220 | 54/54 | 20 | 0 | 0 | — | 109 | 4/8 | 8/8 | 0 | 0 |
| **A** held-title only | 1524 | 52/54 | 16 | 0 | 0 | 36 | 35 | **0/8** | 6/8 | 1 | 1 |
| **B** target-field only | 1524 | 52/54 | 16 | 0 | 0 | 36 | 35 | **0/8** | 6/8 | 1 | 1 |
| **C** held **or** target | 1575 | 53/54 | 17 | 0 | 0 | 36 | 41 | **0/8** | 7/8 | **0** | 1 |
| **D** supported-query | 1806 | 53/54 | 17 | 0 | 0 | 31 | 72 | 1/8 | **8/8** | **0** | 1 |
| **F** central/peripheral | **1629** | **53/54** | **17** | **0** | **0** | 36 | **43** | **0/8** | **8/8** | **0** | 1 |

Per-title, for the regression candidate:

```
                                          BASE  HELD   TGT  H|T  QUERY  CENTRAL
Director of Recruiting, Engineering & IT  ADMIT   no    no   no    no      no
Staff Infrastructure Security Engineer    ADMIT   no    no   no  ADMIT     no
Technical Support Engineer 2              ADMIT   no    no   no    no      no
Data Analyst                              ADMIT   no    no   no    no      no
--- wanted ---
Business / Sr / Salesforce Business Analyst ADMIT ADMIT ADMIT ADMIT ADMIT ADMIT
Salesforce / CRM Functional Consultant    ADMIT ADMIT ADMIT ADMIT ADMIT   ADMIT
Business Systems Analyst                  ADMIT   no    no  ADMIT ADMIT   ADMIT
Salesforce Administrator                  ADMIT   no    no   no  ADMIT   ADMIT
```

### What each measurement actually showed

**A — held-title corroboration.** Suppresses all four bad titles and costs two
wanted ones. It is the bluntest rule and the only one that regresses a software
persona: `adv_grad_cs` has **no held title at all** (a new graduate), so
held-title-only collapses its gate from 78 to 10 and loses its primary
visibility. A rule that requires a held title punishes people who do not have
one yet.

**B — target-field corroboration.** Identical numbers to A, and that is itself
the finding: `role_evidence.family_of("business analysis")` returns **None**.
`target_field` holds a *description* ("business analysis", "software
engineering"), not a *title*, and the family classifier is built for titles. So
the `_target()` family path contributes nothing for this candidate and, on this
set, almost nothing for anyone. Matching the target's **words** against family
vocabulary does work — that is what C and F use — but the existing
`family_of`-based path is largely inert.

**C — held-title OR target.** 0 of 8 bad, no software regression, and it
recovers `Business Systems Analyst`. It still loses `Salesforce Administrator`,
because `it_administration` is not corroborated either way.

**D — supported-query corroboration.** Keeps all 8 wanted titles but lets
`Staff Infrastructure Security Engineer` back in. The mechanism is exact and
worth stating: the query `salesforce administrator` classifies to
`it_administration`, that family then hands over its whole vocabulary, and the
vocabulary contains `infrastructure`. **This is a vocabulary problem, not a
corroboration problem** — `FAMILY_TITLES["it_administration"]` lumps
`salesforce administrator` together with `infrastructure`, `systems engineer`
and `network administrator`.

One methodological note: an earlier pass of this measurement classified queries
with the **audit** classifier (`classify_query.family_of`), whose taxonomy names
differ from `role_evidence`'s — `business_analyst` versus `business_analysis`.
Nothing matched, and four variants came out numerically identical. Corrected to
`role_evidence.family_of`; the table above is post-correction.

**F — central/peripheral.** Core families (held title or target) give their
whole vocabulary; peripheral strong families give **only the titles the
candidate's own evidence already names**. This is the only variant that gets
0 of 8 bad *and* 8 of 8 wanted. `Salesforce Administrator` survives because it
is literally one of his queries and corpus hints; `infrastructure` does not,
because nothing in his document says it.

## 4. Measurement E — do central and incidental families separate?

Regression candidate, `target_field = "business analysis"`, one held-title
family (`functional_consulting`):

| family | strength | hits | entries | held | target | query |
|---|---|---:|---:|---|---|---|
| business_analysis | strong | 38 | 7 | – | **yes** | **yes** |
| product | strong | 33 | 6 | – | – | **yes** |
| data_analytics | strong | 21 | 6 | – | – | – |
| functional_consulting | strong | 18 | 7 | **yes** | – | **yes** |
| project_delivery | strong | 12 | 4 | – | – | – |
| it_administration | strong | 5 | 3 | – | – | **yes** |
| hr_recruiting | strong | 4 | 2 | – | – | – |
| support | strong | 2 | 1 | – | – | – |
| sales | strong | 2 | 1 | – | – | **yes** |

**Density separates the extremes and not the middle.** The incidental families
cluster low (2–5 hits, 1–3 entries) and the identity families cluster high
(18–38 hits, 6–7 entries), so a threshold somewhere around 10 hits would work
*for this candidate*. But `data_analytics` sits at 21 hits and 6 entries — as
dense as the identity families — and it is the family that produces the
`Data Analyst` admission. Density alone would keep it.

**Corroboration separates them where density does not.** `data_analytics` has
no held title, no target-field match and no query; `business_analysis` has two
of the three. That is why the recommendation below is corroboration-based and
why no density threshold is proposed.

## 5. The one non-software regression — resolved as a benchmark defect

`pm_technical` lost primary-probe visibility under every variant. Audited
before deciding anything.

### What the fixture actually describes

```
headline      Technical Product Manager
summary       "Technical product manager with 7 years owning platform and
               API products."
employment    Technical Product Manager, Corvus Platform, Jan 2021 - Present
              Software Engineer, Corvus Platform, Jun 2016 - Dec 2020
skills        API Design, Technical Specifications, Roadmap, SQL, Python,
              REST APIs, OpenAPI, SLOs, Jira, Confluence, Stakeholder Mgmt
primary       technical_pm          <-- the only artefact that disagrees
plausible     product_manager, backend, solutions_consultant
```

Nothing in the document mentions programme or project management. No "program
manager", no "project manager", no delivery or ceremony framing.

### What `technical_pm` means in this framework

Not "technical PM" as an umbrella. The audit taxonomy is explicit, and three
independent artefacts agree:

```
classify_query.family_of("technical product manager")  ->  product_manager
classify_query.family_of("technical program manager")  ->  technical_pm
classify_query.family_of("technical project manager")  ->  technical_pm
PROBES["technical_pm"]        = Technical Program Manager, Technical Project Manager
PROBES["product_manager"]     = Product Manager, Senior Product Owner
pm_product  (headline "Product Manager")  lists technical_pm as PLAUSIBLE
```

`product_manager` and `technical_pm` are **separate families** in the
`delivery` group, and a Product Manager persona treats Technical Program
Manager as adjacent-but-different. So the probes are **correct for their
label**; the user's suggested fix of repointing `PROBES["technical_pm"]` at
Product Manager titles would have corrupted a taxonomy entry that eight other
personas reference.

### What the framework says a label means

`defs.py`, first paragraph:

> Every `primary`, `plausible` and `forbidden` field below was written from the
> persona's own described evidence, BEFORE running the engine on it, and none
> of them was revised afterwards to match output.

`primary` is therefore the profession the document describes — not a target.
Every sibling follows it: `pm_product` headline "Product Manager" → primary
`product_manager`; `proj_manager` → `project_manager`; `scrum_master` →
`scrum_master`. **`pm_technical` is the only persona whose primary contradicts
its own headline**, and the document-grounded family was sitting in `plausible`.

### Decision, and the hazard in making it

Evaluation metadata corrected. `primary` and `plausible` are **swapped** so the
document-grounded family is primary, mirroring `pm_product` exactly:

```
-    primary="technical_pm",
-    plausible=["product_manager", "backend", "solutions_consultant"],
+    primary="product_manager",
+    plausible=["technical_pm", "backend", "solutions_consultant"],
```

No persona text was changed; the summary, employment and skills hash
identically before and after. No engine code was changed.

**The hazard is real and should be weighed by review, not by me.** The defs
header exists precisely to stop labels being revised to match output, and this
revision was *occasioned* by variant F failing on it. Three things argue it is
nonetheless sound:

1. every piece of evidence is **independent of variant F** — the headline, the
   frozen classifier, the probe table and the sibling personas. The defect was
   findable without running any Fix B variant
2. the correction **does not flatter the baseline**: `pm_technical` was already
   54/54-visible under BASELINE before the relabel, and still is after, because
   the old rule dumped both vocabularies in regardless
3. the change is a **swap**, not a deletion — the information content of the
   label set is preserved and `technical_pm` remains plausible for this person

Two caveats you should have:

- **`defs.py` is gitignored and untracked.** The correction therefore cannot be
  committed separately as asked; it exists only in the local workspace. The
  exact diff is recorded above so it can be reapplied or reverted, and a backup
  sits at `output/v3-fix-b/defs.py.pre-pm-correction`. It also means the
  header's "never revised afterwards" claim is not verifiable from history for
  any label, including this one.
- If review disagrees and restores `technical_pm`, variant F's honest number is
  **53/54**, and the remaining loss is a persona whose document names no
  project-delivery title anywhere.

## 6. Variant F re-run, rule unchanged

Only the benchmark changed. `variant()` was not edited between runs
(`sha256[:12] = dab2d7b99e2e`); the subject cache was cleared and the same rule
re-applied.

| | before correction | after correction |
|---|---:|---:|
| **primary visibility** | 53/54 | **54/54** |
| forbidden-family admissions | 17 | 17 (baseline 20) |
| starvation | 0 | 0 |
| legacy-floor fallbacks | 0 | 0 |
| total gate hints | 1629 | 1629 (baseline 2220) |
| regression candidate gate | 43 | 43 (Fix A baseline 109) |
| candidate bad titles | 0/8 | **0/8** |
| candidate wanted titles | 8/8 | **8/8** |
| **software regressions** | 0 | **0** |
| **non-software regressions** | 1 | **0** |

Full table on the corrected benchmark:

```
variant        hints  prim vis  forbid  starv  fallbk  chg  H gate  H bad  H want  SW  NSW
BASELINE        2220     54/54      20      0       0    0     109    4/8     8/8   0    0
HELD            1524     53/54      16      0       0   36      35    0/8     6/8   1    0
TARGET          1524     53/54      16      0       0   36      35    0/8     6/8   1    0
HELD_OR_TGT     1575     54/54      17      0       0   36      41    0/8     7/8   0    0
QUERY           1806     54/54      17      0       0   31      72    1/8     8/8   0    0
CENTRAL         1629     54/54      17      0       0   36      43    0/8     8/8   0    0
```

`HELD`/`TARGET` still break `adv_grad_cs`, the graduate with no held title.
`QUERY` still re-admits `Staff Infrastructure Security Engineer` through the
`it_administration` vocabulary. **CENTRAL is the only variant with no
regression of any kind.**

## 7. The target_field finding, carried forward unchanged

`role_evidence.family_of(target_field)` is largely inert: `target_field` holds
descriptions — `"business analysis"`, `"software engineering"` — and
`family_of` is built for titles, so it returns `None`. The `_target()` family
path contributes almost nothing on this set.

Variant F does **not** depend on repairing that. It uses the **target-word**
corroboration measured here: the normalised `target_field` string is matched
against `FAMILY_TITLES` fragments directly, in either containment direction. An
implementation must preserve exactly that method. **A `family_of` taxonomy
refactor must not be mixed into this patch.**

## 8. Recommendation: GO

**Implement variant F, the central/peripheral model.**

```
CORE       a family named by a grounded held title, or matched by the words of
           a corroborated target_field
           -> the family's full board vocabulary, exactly as today

PERIPHERAL any other family with strong work-mode evidence
           -> only the titles the candidate's own evidence already names
              (held-title fragments, corpus title hints, final queries)
```

On the corrected benchmark it is the only variant that is strictly better than
the Fix A baseline on every measured axis except gate breadth, which is the
point:

- primary visibility **54/54**, unchanged from baseline
- forbidden-family admissions **20 → 17**
- gate hints **2220 → 1629**, with **zero** starvation and **zero** fallbacks
- the regression candidate: gate **109 → 43**, bad titles **4/8 → 0/8**,
  wanted titles **8/8 retained**
- **no software regression, no non-software regression**

Conditions on the GO:

1. **Review must ratify the `pm_technical` relabel**, or accept 53/54. It is
   evaluation metadata, it is untracked, and it was changed after the variant
   failed on it — all three facts are above so the decision is yours.
2. Implement behind its own flag, default off, with the usual byte-identical
   off path, as Steps 3–8 and Fix A all did.
3. Do not fold in a `family_of` taxonomy refactor or an
   `it_administration` vocabulary split. Both are real and both are separate.

Still not implemented. No engine code changed, no threshold tuned, Fix A
untouched, Fix C untouched, free retrieval unchanged.
