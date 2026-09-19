# V3 independent evaluation holdout — protocol

**Status: PROTOCOL ONLY. INDEPENDENT HOLDOUT LABELS: NOT YET COLLECTED.**

Prepared during V3 Step 1, before any role-based query behaviour exists, which
is the only time it can honestly be prepared. Nothing in this document is a
result, and no label in it was written by the engine or by the agent building
the engine.

Required by §21.1 of [profile-engine-v2-forensic-audit-for-v3.md](profile-engine-v2-forensic-audit-for-v3.md).

---

## 1. Why this exists and why it must be separate

The v2 evaluation's labels were authored by the agent that built the engine.
That was disclosed and bounded, but it cannot be repeated for **role** labels,
because role labels are exactly where an engine-aligned labeller's frame leaks:
the person who knows the engine emits `salesforce developer` for a BA is the
person least able to write an innocent `MUST_NOT_GENERATE` list.

The 56-persona matrix from the audit stays **development data**. Its labels are
independent of the engine but were written by the same agent that wrote the
audit, and its documents are synthetic. It may be used freely to develop and
debug. It may not be used to decide whether v3 ships.

## 2. Candidate sourcing

- Candidates are **explicitly supplied** résumé files, one supply event, paths
  named by the repository owner. No directory is scanned. This is the v3
  data-access policy and it is not negotiable: during the v2 audit two résumés
  were requested and four were found and processed by searching `~/Downloads`.
- Target **24 candidates minimum**, which §21.1 calls a development milestone
  and not a statistical-power claim. More is better; fewer is reportable.
- Stratified, with a floor per stratum so the holdout cannot quietly become a
  software benchmark:

| Stratum | floor |
|---|---:|
| software engineering (any specialism) | 5 |
| technical non-developer (platform admin/BA/consultant, PM, delivery, support) | 6 |
| non-technical (sales, marketing, finance, HR, ops, design, legal) | 8 |
| hybrid / career transition / ambiguous | 5 |

The non-technical floor is the largest on purpose. That is the population the
audit measured as worst served, and a holdout that under-samples it will
certify a v3 that has not fixed anything.

- Résumé text lives only in gitignored `output/profile-engine-v3-holdout/`.
  Nothing in `docs/` may carry a name, employer, contact detail or excerpt.

## 3. The labelling instrument

One row per candidate, three role-family fields, written **from the résumé
alone**.

| Field | Meaning |
|---|---|
| `primary` | the one role family this person is, on this evidence. Exactly one. `UNCERTAIN` is a valid and expected answer for career-transition cases. |
| `plausible` | other families the evidence genuinely supports, that a good recruiter would also put them forward for. May be empty. |
| `must_not_generate` | families this evidence does **not** support, which would be a wrong search. **This is the load-bearing field.** |
| `why_must_not` | one sentence per entry. A list without reasons cannot be reviewed. |
| `notes` | anything the labeller wants recorded, including doubt. |

Rules given to the labeller, verbatim in `LABELLING-INSTRUCTIONS.md`:

1. Read the résumé. Do not run anything. Do not look at any system output.
2. `must_not_generate` is not "every family that is not primary". It is the
   families a reader would actively object to. A frontend developer's
   `must_not_generate` should contain `data_engineer`, not `fullstack`.
3. A tool is not a role. Someone who names Salesforce is not a Salesforce
   developer; someone who names SQL is not a data engineer. If the evidence for
   a family is only that a tool appears, that family belongs in
   `must_not_generate`.
4. If you cannot decide the primary family, write `UNCERTAIN` and say why.
   Forcing an answer is worse than recording the doubt.
5. Do not revise a label after seeing any engine output. Ever.

## 4. Independence and double review

- The labeller must not be the agent or person implementing v3.
- **At least 25%** of candidates get a **second, blind** labeller.
- Disagreements are **reported, not reconciled away**. The v3 report carries
  the raw disagreement rate per field, and `must_not_generate` disagreements
  are listed individually.
- If only one labeller is available, that is recorded as a limitation and the
  holdout is marked single-labelled. It is not silently upgraded.

## 5. Locking

Before the first v3 role output is generated against these candidates:

1. `labels.json` is finalised.
2. `python output/profile-engine-v3-holdout/lock_labels.py --lock` writes
   `labels.sha256` and a UTC timestamp.
3. The hash is quoted in every subsequent v3 report.
4. Any later edit invalidates the lock, and the validator refuses to run the
   evaluation until a **new** lock is taken and the change is explained in the
   report. There is no silent relabelling.

## 6. What the holdout measures

The discovery metrics from §21.2, on locked labels:

| Metric | Definition |
|---|---|
| Supported-query precision | queries whose family is `primary` or in `plausible`, over all queries |
| Severe contamination rate | candidates emitting ≥1 `must_not_generate` family |
| Primary-role coverage | candidates with ≥1 query in `primary` |
| Zero-retrieval rate | candidates for whom search returns nothing |
| Free-gate fit | candidates whose gate admits `primary` and excludes `must_not_generate` |
| Query-set invariance | identical queries under layout, ordering, alias and spacing perturbation |
| Held-title yield | emitted held titles that buy zero corpus rows |

Ranking metrics come second and need independently judged job relevance, which
is a separate collection exercise and is **not** covered by this protocol.

## 7. Release rule

**Coverage, not precision, is the gate.** A precision gain bought with any
increase in zero-retrieval rate is a failure, because that is exactly how R5
removed the job search for 2 of 16 personas and looked better while doing it.

## 8. Current status

| | |
|---|---|
| Protocol | **written** |
| Instrument and templates | **written** — `output/profile-engine-v3-holdout/` |
| Validator and locking script | **written** |
| Candidates supplied | **none** |
| Labels collected | **none** |
| Second labeller | **not arranged** |
| Lock taken | **no** |

**INDEPENDENT HOLDOUT LABELS: NOT YET COLLECTED.** No v3 step beyond Step 2
should be evaluated as complete until this section says otherwise.
