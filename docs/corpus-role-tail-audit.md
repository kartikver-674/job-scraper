# Corpus role tail — audit

Traced and measured at `49e9227` against the **frozen** corpus (22,806 listings,
367-term vocabulary) under the live V3 matrix. **Audit only.** Fix 1 and Fix 2
are unchanged, `SWEEP_MARKET_COMPOUND_SPLIT` is still off, nothing is deployed,
and Profile Engine V3 is not redesigned.

15 personas: the 13 benchmark people in `bench/people.py` and the two Srishti
packagings. No model call — every stage below is deterministic given a skill
set, which is what makes a 15-persona sweep affordable and repeatable.

```
SWEEP_ROLE_EVIDENCE=1              SWEEP_CANONICAL_REVALIDATION=1
SWEEP_CANDIDATE_TITLE_GATE=1       SWEEP_CANONICAL_FALLBACK=discard
SWEEP_CANDIDATE_HARD_DROP=1        SWEEP_SEMANTIC_SCOPE=1
SWEEP_ORPHAN_ROLE_GUARD=0          SWEEP_SEMANTIC_SCOPE_NEGATION_VERBS=0
SWEEP_ROLE_ATTACHMENT_GUARD=1      SWEEP_FAMILY_CENTRALITY_GATE=1
SWEEP_PROFILE_ENGINE_VERSION=v2
```

**The answer first.** The tail is real, it is systemic rather than Srishti's,
and it is *older and larger* than the compound splitter. **54 of 73 admitted
corpus roles rest on fewer than ten actual postings** — the number
`MIN_LISTINGS = 10` is named for — with the flag **off**. Four rest on a single
posting.

One guard fixes the specific hole the brief asks about, costs no persona a
search, and is measured below. Fix 1 can be enabled with it.

---

## 1. Corpus role admission trace

| # | stage | input | threshold | fail-open | fail-closed |
|---|---|---|---|---|---|
| 1 | `identities()` | extracted skill strings | `LOOKUP` ∪ market (flag) | unknown compound stays whole | — |
| 2 | `clean_skills` | atoms | `CONCEPT` words ∧ ∉ vocab | rare real tool survives | — |
| 3 | `matching_rows` | own ∩ each listing's skills | `need=2` shared **and** `evidence ≥ 7.0` (Σ idf) | **`return out or weak`** — if nothing clears 7.0, every ≥2-skill row is returned anyway | 0 rows when fewer than 2 skills are in vocab |
| 4 | `fragments()` | matched titles | 2–3-word n-grams, seniority stripped | — | — |
| 5 | weighting | each matched row | `row_weight = max(1.0, evidence)` | — | — |
| 6 | **`MIN_LISTINGS`** | `here[frag]` — **summed weight** | `mine < 10` → drop | **one posting with evidence ≥ 10 passes** | — |
| 7 | `MIN_COMPANIES` | `idx[frag]["companies"]` — **corpus-wide**, not matched | `< 5` → drop | a fragment posted by 5 companies passes even if the candidate matched 1 | — |
| 8 | `MAX_SHARE` | corpus share | `> 0.25` → drop | — | wildcards removed |
| 9 | lift | `(mine/Σw) / share` | `≤ 1.0` → drop | — | — |
| 10 | `quality` | reachable share | multiplies the score; **never a floor** | a fragment with `quality 0.0` still ranks | — |
| 11 | overlap prune | ranked fragments | substring containment | — | — |
| 12 | `canonicalise` | fragment → real corpus title | — | — | — |
| 13 | `validated` | candidate queries | seniority/word-count contract | — | — |
| 14 | `canonical_guard.check` | **replacements only** | search-operator, contract | a non-replacement is never judged | rejects `sf -data cloud` |
| 15 | `role_evidence.filter_queries` | queries | **only `GATED` families** | `family_of → None` ⇒ **never rejected**; `thin` record ⇒ nothing rejected; no non-GATED anchor ⇒ nothing rejected | rejects `salesforce engineer` |
| 16 | `title_gate` own-hints | corpus hints | **only `CORE` families** survive | legacy floor when no evidence at all | drops hints of weak families |

Two stages decide almost everything, and they point opposite ways.

* **Step 15 rejects only `GATED` families** — `software_engineering`,
  `data_engineering`, `ml_engineering`. Everything else, and everything it
  cannot classify, passes.
* **Step 16 admits only `CORE` families.** Everything else is dropped.

A query is therefore *kept unless proven wrong*, and a gate hint is *dropped
unless proven right*. That asymmetry is Phase 6.

---

## 2. `MIN_LISTINGS` semantics — proven

```python
MIN_LISTINGS = 10   # "A fragment seen fewer times than this is noise"

row_weight = max(1.0, float(shared))        # shared = Σ idf over the overlap
here[frag] += row_weight
...
if not entry or mine < min_listings: continue
```

`mine` is **summed evidence weight**, not a posting count. Real overlaps score
7–18, so **one posting can carry 17.9** and clear a threshold named "10
listings".

### Every admitted corpus role, by what it actually rests on

73 corpus-derived roles across 15 personas, flag ON:

| actual distinct postings | roles | share |
|---|---|---|
| **1** | **4** | 5% |
| 2 | 3 | 4% |
| 3–4 | 22 | 30% |
| 5–9 | 25 | 34% |
| **fewer than 10** | **54** | **74%** |
| 10 or more | 19 | 26% |

The single-posting cases:

| persona | role | postings | matched companies | weighted | passed because |
|---|---|---|---|---|---|
| lena | `back end developer` | **1** | 1 | 16.03 | 16.03 ≥ 10 |
| mateo | `java fullstack` | **1** | 3 | 11.04 | 11.04 ≥ 10 |
| mateo | `react js developer` | **1** | 1 | 11.04 | 11.04 ≥ 10 |
| srishti_ba | `quality analyst` | **1** | 1 | 17.94 | 17.94 ≥ 10 |

**`MIN_COMPANIES = 5` does not catch these**, because it reads
`idx[frag]["companies"]` — every company posting that fragment **anywhere in
the corpus** — not the companies among the candidate's matched rows.
`quality analyst` has 5 corpus companies and **1** matched company.

### Verdict

**Both.** The name is misleading *and* the threshold is too permissive.

* Misleading: a constant called `MIN_LISTINGS` compared against a float sum of
  logarithms will be read as a posting count by everyone including its author.
* Too permissive: at evidence 10–18 per row, the effective floor is **one
  posting**, which is two orders of magnitude below what the comment beside it
  describes ("a real role name is posted by MANY employers").

Not changed here. Section 10 says what the minimal change is.

---

## 3. Unknown-family inventory

Every final corpus-derived role with `role_evidence.family_of(title) is None`,
across all 15 personas, both flag states.

| title | postings | matched cos | corpus listings / cos | lift | canonical | in gate | flag | personas | classification |
|---|---|---|---|---|---|---|---|---|---|
| `software engineering professional` | 6 | 1 | 256 / 22 | 5.1 | accepted | **yes** | off+on | farida, jonas, (hana, lena when packaged) | **QUESTIONABLE** — a real phrase, but it names no profession; it is a seniority-neutral synonym for "engineer" and buys the catalogue |
| `revenue operations` | 6 | 3–5 | 19 / 10 | 16–19 | accepted | **yes** | off+on | srishti ×2 | **PLAUSIBLE** — a real profession, adjacent to Salesforce BA work, and the taxonomy simply has no entry for it. The candidate has no RevOps evidence. |
| `sf -data cloud` | 4 | 2 | 13 / 5 | 19–24 | **REJECTED** (`search_operator`) | no | off+on | srishti ×2 | **MALFORMED** — caught, correctly, by canonical revalidation |
| `ai-native software entwickler (m/w/d)` | 3 | 1 | 11 / 6 | **296** | accepted | no | off+on | hana | **WRONG** — a German-language posting title with a gender tag; not a search string in any market this candidate is in |
| `node js` | 2 | 1 | 35 / 12 | 5.9 | accepted | **yes** | off+on | lena | **WRONG** — a technology, not a role. Searching it returns every listing naming Node. |
| `quality analyst` | **1** | 1 | 8 / 5 | 11.1 | accepted | **yes** | **on only** | srishti_ba | **QUESTIONABLE** — a real profession the candidate has adjacent evidence for (UAT, testing, defect triage), on one posting |

Six distinct titles, eight role instances, six of fifteen personas. **Five reach
production; `sf -data cloud` is already stopped by canonical revalidation.**

**Not every unknown family is wrong.** `revenue operations` is a genuine
profession the taxonomy has not learned, and `quality analyst` is a reasonable
adjacency for someone who owns UAT. The defect is not that they are
unclassifiable — it is that **being unclassifiable removes every check**, so a
1-posting artefact and a real profession are admitted on identical terms.

---

## 4. Fail-closed simulation

`family_of(title) is None` → reject, **held titles and validated résumé titles
always preserved**.

| | result |
|---|---|
| queries removed | **8** (6 distinct titles) |
| personas affected | **6 of 15** |
| personas left with no corpus role | 0 |
| **zero-query personas** | **0** |
| held titles lost | **0** |
| primary role coverage | unchanged — every persona keeps its own job title and every known-family corpus role |

Worst case is `srishti_functional`, 11 roles → 9.

**"Unknown family = reject" is viable.** It costs no one a search.

But it is the wrong *rule*, even though it is the right *outcome* here: it
punishes the taxonomy's gaps rather than the evidence. `revenue operations` is
removed for being unrecognised, not for being unsupported. If the taxonomy
later learns RevOps, a title admitted on one posting comes straight back.

---

## 5. Stronger-evidence simulations

Applied to **unknown-family corpus titles only**, all 15 personas, flag ON.

| policy | removed | personas hit | zero-query | what survives |
|---|---|---|---|---|
| current | 0 | 0 | 0 | everything |
| **A** ≥ 10 distinct postings | **8** | 6 | **0** | nothing |
| **B** ≥ 5 matched companies | 7 | 6 | 0 | `revenue operations` (srishti_ba, 5 cos) |
| **C** ≥ 5 postings ∧ ≥ 3 companies | 6 | 6 | 0 | `revenue operations` ×2 |
| **F** ≥ 3 matched companies | 6 | 6 | 0 | `revenue operations` ×2 |
| **D** lexical overlap with a held title | **8** | 6 | 0 | **nothing — 0 of 8 overlap** |
| **E/G** fail-closed | 8 | 6 | 0 | nothing |

Policy **D is useless here**: none of the eight shares a word with its
persona's held titles, so it collapses to fail-closed while being a weaker
rule. Dropped.

### The same policies applied to ALL corpus roles

Worth knowing, because the thinness is not confined to unknown families:

| policy | removed of 73 | personas left with no corpus role | zero-query personas |
|---|---|---|---|
| A ≥ 10 postings | **54** | 7 | 1 |
| B ≥ 5 matched companies | 63 | 10 | 1 |
| C ≥ 5 postings ∧ ≥ 3 companies | 53 | 8 | 0 |
| F ≥ 3 matched companies | 46 | 6 | 0 |

**A blanket evidence floor is not deployable.** It removes three quarters of
every persona's search and strands one persona with nothing. The tail is not a
rim that can be trimmed — it is most of the distribution, and much of it is
useful. That is why the recommendation in section 10 is scoped to unknown
families.

---

## 6. Paid query vs free title gate

Across all 15 personas, flag ON:

| | count |
|---|---|
| in **both** query list and gate | 55 |
| **query only** — paid for, not scored on free boards | **39** |
| **gate only** — scored for free, never searched | 423 |

### Why they differ, mechanically

* A query is rejected only when `family_of` returns a **GATED** family the
  candidate does not support (step 15). Unknown and ungated families pass.
* A corpus hint enters the gate only when its family is **CORE** — named by a
  held title, or a strong family corroborated by the stated target (step 16).

**Queries are kept unless proven wrong; gate hints are dropped unless proven
right.** Two tests of opposite polarity over the same evidence.

### Intentional vs accidental

**Gate-only (423) is intentional and correct.** The gate filters a whole free
board at zero marginal cost, so breadth is cheap; a paid query costs money per
result, so it must be narrow. These are different mechanics and should produce
different sets.

**Query-only (39) is the accidental direction, and it is systemic — not a
Salesforce quirk.** The candidate pays to find a title on a paid board and then
cannot see the same title on a free one:

| persona | query not in gate | postings |
|---|---|---|
| ada | `ai ml engineer` | 47 |
| farida, jonas | `full stack engineer` | 31 |
| srishti_ba | **`salesforce administrator`** | 32 |
| srishti_functional | **`salesforce administrator`** | 31 |
| srishti ×2 | **`salesforce business analyst`** | 19–22 |
| srishti ×2 | **`salesforce sales cloud`** | 5–8 |
| chen, ada | `data scientist` | 4–8 |
| … 30 more | | |

`salesforce administrator` is the clearest: the candidate is a **Salesforce
Certified Administrator**, it is their highest-lift query (32 postings, 21
companies), and the gate drops it because `it_administration` is only *weak*
support and so never enters Fix B's `considered` set.

**Can the same evidence make a title worth searching and unsafe to keep?**
For a genuinely different mechanism, yes — a gate hint applies to every posting
on a board, a query to one search. But not here. Nothing about the candidate's
evidence changed between step 15 and step 16; only which *test* was applied.
**This is accidental.** Out of scope for this audit by instruction, and it
should not stay that way.

---

## 7. Compound / multi-family titles

`family_of` is an **ordered, first-match-wins** scan of sixteen regexes, with
`software_engineering` **last**. Only three families are `GATED` —
`software_engineering`, `data_engineering`, `ml_engineering` — and only a GATED
family can ever be rejected.

Consequence: **a title that names development and matches any earlier family is
classified as the earlier one.** If that family is ungated, the development
test is never run.

Measured over the frozen corpus's distinct title fragments:

```
fragments naming development                            4051
classified as some family other than software_engineering 210  (5.2%)
  of those, routed to an UNGATED family                   101  — development test lost
```

| family that wins | fragments | gated? | examples |
|---|---|---|---|
| ml_engineering | 95 | **GATED** | `agentic ai developer`, `ai developer` |
| support | 30 | ungated | `application support engineer`, `associate support engineer` |
| data_analytics | 15 | ungated | `analytics developer`, `analytics insights engineer` |
| data_engineering | 14 | **GATED** | `ai data engineer` |
| design | 14 | ungated | `engineer ui ux`, `product design engineer` |
| marketing | 12 | ungated | `developer marketing cloud` |
| sales | 12 | ungated | `commercial sales engineer`, `customer success engineer` |
| **functional_consulting** | **9** | **ungated** | **`application developer consultant`, `consultant fullstack`** |
| it_administration | 7 | ungated | `developer admin`, `developer administrator` |
| finance | 2 | ungated | `engineer treasury` |

### The audited case, corrected

`salesforce techno functional consultant` does **not** match the
`software_engineering` regex at all — "consultant" is not a development word.
It matches `techno.?functional`, which is written **explicitly into
`functional_consulting`**, an ungated family.

So this is not an ordering accident. It is a **taxonomy decision**: the
engine's position is that a techno-functional consultant is a consultant. That
is defensible — the title names a consulting job on a platform — but it means
the development half of the word is a claim the engine has decided never to
test, rather than one it fails to notice.

**Can a title legitimately carry more than one family?** Yes, and 210 corpus
fragments do. The current model cannot express it: `family_of` returns one
answer, and the gate asks one question of it. Widening that is a taxonomy
change and is explicitly out of scope.

---

## 8. Product Owner control

Three separate questions, answered separately.

**A. Does the résumé support product-shaped work?**
**Yes.** `role_evidence` reports `product: strong` for *both* Srishti
packagings — from requirement gathering, user stories with acceptance criteria,
backlog-shaped work. Not a stretch.

**B. Is "product owner" a reasonable profession transition?**
**Yes, and the market says so directly.** See C.

**C. Is 3-posting corpus evidence sufficient to propose the title?**
**No — and it is worse than three postings.** The actual rows behind
`product owner` for `srishti_ba`:

```
salesforce business analyst / product owner (contract-to-hire)   demandpdx
salesforce business analyst / product owner (contract-to-hire)   demandpdx
salesforce business analyst / product owner (contract-to-hire)   demandpdx
```

**One job, one company, listed three times.** `matched_companies = 1`.

The finding cuts both ways and should be read carefully:

* The *title* is well chosen — the single posting behind it is literally
  "Salesforce Business Analyst / Product Owner", the candidate's own profession
  with Product Owner as an alternate name. Fix B classing `product` peripheral
  and re-admitting the fragment is correct behaviour.
* The *mechanism* that proposed it is broken. A duplicate advert from one
  employer became a paid search query.

`srishti_functional` reaches the same title on 5 postings across 3 companies,
so the title is not an artefact of one packaging. **Only question C is a
corpus-tail problem, and it is `MIN_LISTINGS`, not Fix B, not `product`.**

---

## 9. Flag off vs flag on — new roles

The 13 benchmark personas carry **atomic** skills, so the splitter cannot move
them: flag on and off are identical for all 13. To make them flag-sensitive,
each persona's own skills were re-packaged as `&`-joined adjacent pairs — the
same person, a phrase-shaped skills line, which is exactly the real-world
variation Srishti's two résumés represent.

Role counts in this table are taken **before** `role_evidence.filter_queries`
and canonical revalidation, so they are one or two higher than section 3's —
the point here is what retrieval proposes, which is what the flag changes.

| persona (packaged) | matching rows | roles | new when ON |
|---|---|---|---|
| **lena** | 0 → **47** | **0 → 8** | 8 |
| **hana** | 0 → **103** | **2 → 11** | 9 |
| **mateo** | 4 → 53 | 2 → 10 | 8 |
| **srishti_functional** | 0 → 330 | 1 → 11 | 10 |
| srishti_ba | 308 → 398 | 11 → 11 | 1 (`quality analyst`), 1 lost (`sf -data cloud`) |
| chen | 281 → 355 | 10 → 10 | 0 |
| bhaskar | 250 → 293 | 5 → **4** | 0, **1 lost** (`software development engineer ii`) |
| ada, dmitri, esi, farida, gopal, iris, jonas, kwame | unchanged | unchanged | 0 |

**The flag rescues personas, not just widens them.** `lena` packaged has
**zero** corpus roles with the flag off and eight with it on. `hana` goes 2 → 11.
That is the defect Fix 1 exists to fix, reproduced on people who are not
Srishti.

### Every new role, with what it rests on

| persona | new role only when ON | family | postings | matched cos | canonical | classification |
|---|---|---|---|---|---|---|
| hana | `ai ml engineer` | ml_engineering | 54 | 1 | ok | **PLAUSIBLE** — she is an ML engineer |
| hana | `data scientist` | data_analytics | 8 | 2 | ok | PLAUSIBLE |
| hana | `associate ai/ml engineer` | ml_engineering | 6 | 1 | ok | PLAUSIBLE |
| hana | `professional services consultant` | functional_consulting | 6 | 1 | ok | **QUESTIONABLE** |
| hana | `software engineering professional` | **None** | 6 | 1 | ok | **QUESTIONABLE** |
| hana | `full stack engineer` | software_engineering | 8 | 1 | ok | PLAUSIBLE |
| hana | `artificial intelligence engineer` | software_engineering | 4 | 1 | ok | PLAUSIBLE |
| hana | `technical consultant-ai integration` | functional_consulting | 4 | 1 | ok | **QUESTIONABLE** |
| hana | `software engineer, agentic ai` | software_engineering | **2** | 1 | ok | **QUESTIONABLE** |
| lena | `backend engineer` | software_engineering | 27 | 2 | ok | **CLEARLY APPROPRIATE** |
| lena | `machine learning engineer` | ml_engineering | 5 | 1 | ok | PLAUSIBLE |
| lena | `associate software engineer` | software_engineering | 3 | 1 | ok | PLAUSIBLE |
| lena | `software engineering professional` | **None** | 3 | 2 | ok | **QUESTIONABLE** |
| lena | `java developer` | software_engineering | **2** | 1 | ok | PLAUSIBLE |
| lena | `backend developer` | software_engineering | **2** | 1 | ok | CLEARLY APPROPRIATE |
| lena | `full stack engineer` | software_engineering | 4 | 1 | ok | PLAUSIBLE |
| lena | `back end developer` | software_engineering | **1** | 1 | ok | CLEARLY APPROPRIATE (1 posting) |
| mateo | `gen ai engineer`, `full stack ai engineer` | ml_engineering | 4 | 1–2 | ok | **QUESTIONABLE** |
| mateo | `ai ml engineer`, `artificial intelligence engineer` | ml_engineering | 4–26 | 1 | ok | **QUESTIONABLE** — he is a backend engineer |
| mateo | `software engineer ii`, `full stack engineer` | software_engineering | 5–31 | 1–27 | ok | PLAUSIBLE |
| mateo | `software engineering professional` | **None** | 6 | 1 | ok | **QUESTIONABLE** |
| srishti_ba | `quality analyst` | **None** | **1** | 1 | ok | **QUESTIONABLE** |
| srishti_functional | 10 Salesforce/consulting roles | mixed | 2–31 | 1–17 | `sf -data cloud` rejected | mostly **CLEARLY APPROPRIATE** |

**Nothing the flag adds is WRONG.** The worst entries are QUESTIONABLE — thin,
or a plausible adjacency the candidate has partial evidence for. Every one of
them is the same thinness the flag-off tail already has; more matching rows
simply surfaces more of it.

One small regression: **`bhaskar` loses `software development engineer ii`**
with the flag on. Re-ranking, not rejection — the extra rows changed the lift
order and it fell out of the top 12. He keeps 4 roles including his own.

---

## 10. Recommended minimal fix

**One guard. Unknown-family corpus titles must clear a real evidence floor.**

```
family_of(title) is None
  AND title is corpus-derived (not a held or validated résumé title)
  AND (distinct matched postings < 10 OR distinct matched companies < 3)
      → reject
```

Measured across all 15 personas, flag ON:

| | |
|---|---|
| roles removed | **8** (all six unknown-family titles) |
| personas affected | 6 of 15 |
| **zero-query personas** | **0** |
| held titles lost | **0** |
| worst persona | `srishti_functional` 11 → 9 |
| what goes | `software engineering professional`, `revenue operations`, `sf -data cloud`, `ai-native software entwickler (m/w/d)`, `node js`, `quality analyst` |

On this corpus the guard removes exactly what a bare fail-closed removes, because
every unknown-family title happens to be thin. **The rule is still the better
one**, and the difference matters the first time it does not coincide: it
rejects for *thin evidence*, not for *taxonomy ignorance*. A genuine profession
the taxonomy has not learned — RevOps with 40 postings across 12 employers —
survives. A duplicate advert does not.

**Distinct postings and distinct matched companies, counted as integers.** Both
are already computed inside `keywords_for`; neither requires new data.

### Explicitly not recommended

* **Do not change `MIN_LISTINGS` globally.** A ≥10-posting floor on all roles
  removes **54 of 73** and strands a persona with no query (section 5). The
  thinness is most of the distribution and much of it is useful.
* **Do not rename `MIN_LISTINGS` without the guard.** The name is wrong, but
  renaming it fixes nothing and the next reader will be equally surprised.
  Rename it *with* this change, so the two land together.
* **Do not fail closed on unknown families.** Same outcome today, worse rule.
* **Do not touch the taxonomy, `family_of`, Fix B, or the paid/free
  asymmetry.** Sections 6 and 7 are separate audits, as instructed.

---

## 11. Fix 1 release recommendation

### **YES WITH SMALL GUARD.**

`SWEEP_MARKET_COMPOUND_SPLIT` should be enabled **after** the section-10 guard
lands, not before, and not never.

**Why not "YES" as-is.** The flag adds roles resting on one and two postings —
`quality analyst` (1), `software engineer, agentic ai` (2), `java developer`
(2), `back end developer` (1). None is *wrong*, but paying for a search built
on a single duplicate advert is not a behaviour to switch on deliberately once
it has been measured.

**Why not "NO".** The flag is not the cause of anything on this page. Every
defect here is present with it off: 54 of 73 roles under ten postings, six
unknown-family titles, `product owner` on one duplicated advert. What the flag
changes is how much legitimate evidence reaches retrieval — and for packaged
personas that is the difference between a working search and no search at all:

```
lena  packaged   0 corpus roles OFF   →   8 ON
hana  packaged   2 corpus roles OFF   →  11 ON
srishti_functional  1 role OFF        →  11 ON
```

Holding Fix 1 to avoid the tail would leave those candidates with nothing in
order to avoid admitting a thin title to candidates who already have plenty.

**Why the guard is enough.** It removes every unknown-family entry the flag
introduces (`quality analyst`) and every one it did not, costs no persona a
search, and leaves the flag's real benefit — `salesforce administrator`,
`salesforce business analyst`, `backend engineer` — untouched.

### Conditions

* **G1.** Implement the section-10 guard. Measured: −8 roles, 0 zero-query
  personas.
* **G2.** Rename `MIN_LISTINGS` to what it measures in the same change, with
  the count-vs-weight distinction stated where it is read.
* **G3.** Then enable `SWEEP_MARKET_COMPOUND_SPLIT`, and run the canary from
  the fixes document (C1) with the guard already live.

### Residual risk accepted by this recommendation

* Roles on 1–9 postings **with a known family** are untouched — 54 of 73.
  Deliberate: no measured policy removes them without stranding personas.
* `product owner` on one duplicated advert survives, because `product` is a
  known family. Correct title, bad mechanism; it needs the `MIN_LISTINGS`
  question answered properly, which is a bigger change than this release.
* `salesforce administrator` remains a paid query the free gate will not score
  (section 6).
* `salesforce techno functional consultant` remains untested for its
  development half (section 7).

None of the four is made worse by enabling Fix 1, and all four exist today.

---

## Reproduction

```
scratchpad/tail.py     [1]        admission trace, all 15 personas, flag off/on
scratchpad/policy.py              phases 3-6, policy simulation
scratchpad/phase789.py            phases 7-8
scratchpad/phase79b.py            phase 7 ordering, phase 9 packaged-persona delta
```

Frozen corpus throughout, live V3 matrix set before the first import, no model
call, no engine code modified. Benchmark personas come from `bench/people.py`;
the two Srishti skill lists are the ones the local model actually returned,
recorded in the presentation-stability audit.
