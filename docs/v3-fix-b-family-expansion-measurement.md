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

## 5. The one non-software regression, examined rather than assumed

`pm_technical` loses primary-probe visibility under every variant.

```
primary label      technical_pm    probes: Technical Program Manager,
                                           Technical Project Manager
held fragments     product manager, technical product manager,
                   software engineer, technical product
target_field       "software engineering"
strong families    product, project_delivery
```

Under BASELINE the probes are visible only because `project_delivery` is strong
and dumps `program manager` / `project manager` into the gate. Under any
corroboration rule that family is peripheral, and — checked explicitly — **not
one** of the ten `project_delivery` fragments appears anywhere in that
persona's held titles, corpus hints or final queries. Neither probe string
appears either.

So no close-variant rule could recover it: the résumé describes a Technical
**Product** Manager, and product management and programme management are
different professions. Whether this counts as a regression depends on whether
you trust the persona's label or its document. By the measurement's own
definition it is a loss, and it is reported as one; by the document it is a
correction. It is the single non-software cost of every variant, including the
recommended one.

The software safety set is otherwise clean under C, D and F: `swe_frontend`,
`swe_backend`, `swe_data_eng`, `swe_fullstack`, `swe_java`, `swe_devops`,
`swe_ml`, `adv_grad_cs` and `adv_swe_titled_sf_work` all keep primary
visibility. Only held-title-only (A/B) breaks `adv_grad_cs`.

## 6. Recommendation

**Variant F, the central/peripheral model.**

```
CORE       a family named by a grounded held title, or matched by the words of
           a corroborated target_field
           -> the family's full board vocabulary, exactly as today

PERIPHERAL any other family with strong work-mode evidence
           -> only the titles the candidate's own evidence already names
              (held-title fragments, corpus title hints, final queries)
```

Why this one:

- it is the **only** variant that suppresses all four residual bad titles while
  keeping all eight wanted ones
- **no software persona regresses**, including the graduate with no held title
  that held-title-only breaks
- gate hints fall 2220 → 1629 (27%) with **zero** starvation and **zero**
  fallbacks to the legacy floor, so nobody is left without a gate
- forbidden-family admissions fall 20 → 17 across the suite
- it encodes the distinction the brief asked for directly: performing an
  activity earns you the titles your own document names, being in a profession
  earns you the profession's vocabulary

What it does not fix, and should not be expected to:

- `pm_technical`, above
- the `it_administration` vocabulary, which mixes `salesforce administrator`
  with `infrastructure`. F routes around it; it does not repair it. If that
  vocabulary were split, variant D would become viable too, and the two would
  converge
- primary visibility 54/54 → 53/54. The single loss is `pm_technical`

## 7. Before implementing

1. **Decide the `pm_technical` question first.** If its label is right, F needs
   a fourth corroboration source and none of the three measured here supplies
   one. If its document is right, F is correct and the persona's label should
   be revisited. This is a labelling decision, not an engine decision, and it
   is the only thing standing between F and a clean result.
2. **Do not add a density threshold.** §4 shows it separates the extremes and
   fails on `data_analytics`, which is the family that actually matters here.
3. **Consider splitting `FAMILY_TITLES["it_administration"]`** as separate
   work. It is the proximate cause of the one bad title variant D re-admits.

No code changed. No threshold tuned. Fix C untouched: `roles`, `users`, skill
weights, market separation, ranking and `score_job` are as they were, and the
free-retrieval path is unchanged.
