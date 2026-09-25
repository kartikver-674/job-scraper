# Multi-Track Job Search — V1 Architecture Design

| | |
|---|---|
| Date | 2026-09-25 |
| Based on | `MULTI_TRACK_ARCHITECTURE_AUDIT.md` (HEAD `ff06a48`) plus the fact checks in §A |
| Status | **Design for review. Nothing here is implemented.** Amended 2026-09-25 with production evidence for D-1 and a track-label rehydration fix (§A.3, §A.4, §N.1) |
| Scope | Multi-Track V1: 1–3 résumés per candidate, each one a career track, searched in one shared Sweep |

Audit references are written `[Audit §n]`. Code references are `path:line` at
`ff06a48`. Each major decision uses the requested structure: **Current fact /
Problem / Decision / Why / Compatibility / Risk.**

---

## A. Two Narrow Fact Checks (plus one finding they surfaced)

### A.1 How one profile's `role_keywords` order is produced (audit U14, now resolved)

`role_keywords` is decided **once, at derivation time on Render**, and it is
never re-ordered afterwards:

- `local_profile.generate` calls `local_search.fields_for(person, market)`
  (`auto-apply/local_profile.py:221`).
- The V3 guards only filter the list, preserving order; all of them are off by
  default [Audit §5]. `hard_drop.restore` would append at the end
  (`:304-305`).
- `make_profile.render` writes the list verbatim to `SEARCH["role_keywords"]`
  (`make_profile.py:1294`).
- The worker renders from that same list (`deploy/sweep_worker.py:838-840`).
- `build_search_plan` walks keywords in list order (`scraper.py:1025`).

Inside `fields_for` (`local_search.py:1388-1463`), the order comes from these
steps:

1. **Held titles**. `from_resume` (`:665-689`) walks the *countable*
   employment rows in résumé order (`local_extract.countable`). It splits each
   title on `/ | , ( )` and `" - "` (`_SPLIT`, `:662`), strips seniority
   words, keeps stems of at least two words, and dedupes in first-seen order.
2. **Corpus fragments**. `keywords_for` (`:422-513`) retrieves the market rows
   that share at least two of the person's skills with idf evidence ≥ 7.0
   (`matching_rows`, `:384-419`). It weights each fragment by evidence and
   drops fragments that:
   - have weight below 10,
   - appear at fewer than 5 employers,
   - cover more than 25 % of the market,
   - or have lift ≤ 1.
   It sorts the survivors by `(-lift×reachability, -weight, fragment)`
   (`:501`), suppresses overlapping fragments, and keeps the top 12.
   `canonicalise` (`:580-588`) maps each fragment to its most common complete
   title using `rank_title = (-count, word count, title)` (`:520-538`),
   keeping first-seen order.
3. **Validation**. `validated(held + corpus)` (`:959-985`) drops wildcard
   terms (share above 25 %) and terms containing a hard-drop word. It returns
   `[]` if more than half the terms are bad, and otherwise dedupes by
   `strip().lower()` in order. The result is split back into `held` and
   `corpus`, both in validated order (`:1421-1424`).
4. **Specialist recovery**. `select_detail` (`:1171-1203`) takes orphan skills
   (`orphans`, `:1053-1075`, sorted by `(share, -listings)`) and their
   candidate titles (`candidates_for_skill`, `:1078-1126`). It keeps titles
   passing `worth_it` (anchor evidence ≥ 1.2, at least 20 listings), sorts by
   `(-anchor_evidence, title)`, caps at **2**, and suppresses overlaps.
5. **Tiering**. `rank(held, anchored, corpus)` (`:1219-1248`) produces tier 1
   (held), then tier 2 (anchored), then tier 3 (corpus). Every keyword is
   **lower-cased**, and a keyword offered by two tiers keeps its first tier.
6. **Budget reorder**. `budget_order` (`:1329-1381`) reorders but selects
   nothing:
   - A keyword is eligible when it buys at least 20 listings from at least 8
     employers. If fewer than two keywords qualify, every keyword is
     eligible.
   - Ineligible keywords (`thin`) go to the end, in tier order.
   - Exactly one held title leads: the eligible held title maximising
     `(marginal evidence, listings, keyword)`.
   - After that the order is greedy: each step picks the maximum of
     `(_marginal(share, idf, covered), listings, keyword)`. Once the best
     marginal gain is ≤ 0, the rest are appended in tier order.
   - **Ties break by listing count, then by the lexicographically greatest
     keyword**, because this is `max(...)`, not `min`. The docstring's
     "alphabetically" means this.

Inputs are the extracted skills, the validated employment rows, the market
(the frozen corpus on Render, which has no `output/` [Audit §2.2 #3]) and the
seniority lists from the web process's **default** `config.SCORING`
(`local_search.py:199-209`, `:333-339`). The function is deterministic for
those inputs.

**Consequence for V1:** a track's keyword order is *data*, fixed on the track
when it is derived. A one-track Sweep keeps today's order automatically,
provided the unified planner (§F) takes each track's list as given and never
re-sorts it.

**Note on the V3 guards (amended 2026-09-25).** "Off by default" is true of
the code. It is **not** evidence about the live hosts:
`docs/profile-presentation-stability-audit.md:778-797` records a production
matrix with most V3 step flags **on**. This was not re-verified here, because
the Part 1 allowlist covered only the engine-version variables.

The conclusion above still holds. The guards run at derivation on Render and
only filter the list (preserving order) or append to it (`hard_drop.restore`).
Either way the order is fixed on the track before planning.

### A.2 Representative `derived` payload size (audit U6, partly resolved)

**Method (no model or provider call).** The committed benchmark captures hold
the local model's real answers for the **synthetic** personas:

- `bench/results/qwen3_8b.json`: FIELDS answers.
- `bench/results/dates-qwen3_8b.json`: EMPLOYMENT answers.
- The matching committed PDFs in `bench/resumes/`.

A scratch script (outside the repo) replaced the two model calls with those
captures and ran the **current production engine** (`make_profile.generate_local`
→ `_finish`) with:

- `SWEEP_PROFILE_ENGINE_VERSION=v2`, as in `render.yaml:56-57`;
- an empty output directory, as on Render, so the frozen corpora answer;
- sockets monkey-patched to raise;
- a no-op log.

It measured `len(json.dumps(derived).encode("utf-8"))`. It did not use
`real-qwen3_8b.json` (real people) or anything under `output/`.

| Measure | Result |
|---|---|
| Documents replayed | 52 (13 synthetic personas × 4 layouts); 0 escalations |
| `derived` size | min 6,299 B · median 7,944 B · max 9,273 B |
| Largest contributors (max doc) | `skill_importance` 5,807 B, `local_ranking` 698 B, `title_hints` 680 B, `notes` 415 B |
| Rendered profile source | 5,705–7,375 B |
| Fixture character | 6–10 skills per profile; résumé text 510–3,035 B; 2–11 role keywords |
| 1 / 2 / 3 tracks + plausible envelope | ≈ 10.0 KiB / 19.1 KiB / 28.2 KiB of the 512 KiB limit |

**Extrapolation (INFERENCE).** The fixtures are thin. The audited development
résumé in `docs/profile-engine-v2-production-candidate.md` §8 had 57 terms and
46 concepts. The heaviest fixture used about 580 B of `skill_importance` per
skill, so a 60-skill résumé is roughly 40 KB and three of them roughly 120 KB.

**Conclusion.** The 512 KiB limit does not need to shape the architecture.
V1 still sends the worker a **render-input projection** instead of the whole
`derived` dict (§M), which removes `skill_importance` and the provenance
records entirely. A size test guards the envelope. Real-résumé sizes remain
unmeasured (§V).

### A.3 D-1 — which engine the public worker stamps (**RESOLVED 2026-09-25**)

**What was unknown.** The first version of this document established from
code that:
- `make_profile.render` stamps `engine = skill_concepts.engine_version()` from
  the environment of the **rendering** process (`make_profile.py:1097`,
  `:1291`);
- public run profiles are rendered on the **Oracle worker**
  (`deploy/sweep_worker.py:233-236`, `:838-840`).

The worker's real environment (audit U1) was unknown, so D-1 was a blocker.

**Engine-version contract (code; the deployed code is the same, see below).**

| Precedence | Source | Result |
|---|---|---|
| 1 | `_BOUND`, set only by `skill_concepts.bind`, which is called only by `scraper.py:326-327` when a loaded profile carries a stamp | the bound version |
| 2 | `SWEEP_PROFILE_ENGINE_VERSION`, when non-empty | its value, lower-cased; anything but `v1`/`v2` raises `ValueError` (`skill_concepts.py:813-842`) |
| 3 | `SWEEP_SKILL_CONCEPTS` and `SWEEP_SKILL_EVIDENCE`, each "on" if `1`/`true`/`yes`/`on` | both on → `v2`; neither → the default; only one → `"mixed"`, which `bind()` refuses (`:848-861`; `auto-apply/tests/test_engine_contract.py:122`) |
| 4 | `DEFAULT_VERSION` | `"v1"` (`skill_concepts.py:782`) |

So the only variables that decide the stamp are `SWEEP_PROFILE_ENGINE_VERSION`,
`SWEEP_SKILL_CONCEPTS` and `SWEEP_SKILL_EVIDENCE`. Two similar-looking
variables are unrelated:
- `SWEEP_PROFILE_ENGINE` (`make_profile.py:470`) chooses gemini or local
  *derivation*.
- `SWEEP_ROLE_FAMILIES` is reported by `effective()`, but `roles_enabled()` is
  hard-false.

**Production evidence (read-only SSH to the worker, 2026-09-25).** Only the
names below were queried, and no other value was printed.

| Check | Result |
|---|---|
| `/opt/sweep-worker/app` checkout | branch `main`, HEAD **`f131a43`**, no tracked changes, no untracked paths (0 `profiles/beta_*`) |
| `f131a43` vs this design's HEAD `ff06a48` | the two later commits (`314cb1e`, `ff06a48`) change **no** engine, render or worker file: only docs, `render.yaml` feedback keys, `sweep/app.py`, `sweep/public.py`, feedback, templates, CSS and tests |
| `/etc/sweep-worker/env` (`sudo grep` for the three names only) | **no match** |
| `sweep-worker.service` and its drop-ins (`systemctl cat`, filtered to the three names) | **no reference**; the only `EnvironmentFile` is `/etc/sweep-worker/env` |
| Running gunicorn master and worker (environ read, 39 entries each, filtered to the three names) | **absent** in both |
| Deployed code | `bind()` is called only in `scraper.py:327`; `deploy/sweep_worker.py` does not import `scraper`; `DEFAULT_VERSION = "v1"`; the worker renders with `make_profile.render(name, profile, prefs)` (deployed `sweep_worker.py:236`) |

**Conclusion (FACT).**
- In the worker process `_BOUND` is `None` and none of the three variables is
  set, so `engine_version()` returns **`v1`**.
- Every public run profile is therefore stamped `{"version": 1, "engine":
  'v1'}`, the engine child binds v1 (`scraper.py:326-327`), and **current
  public sweeps score with v1**, the per-term branch (`scraper.py:688-693`).

**Render side.**
- Repository declaration: `render.yaml:56-57` sets `v2`.
- Confirmed live: a single public `GET https://sweep-beta.onrender.com/healthz`
  on 2026-09-25 returned `"derivation_engine": "v2", "engine_source":
  "SWEEP_PROFILE_ENGINE_VERSION"`. That endpoint reports its own process's
  engine (`sweep/public.py:576-598`), and it carries no secret and no user data.
- So a public résumé is **derived with v2** on Render and **scored with v1** on
  the worker. The profile a visitor downloads from `/profile.py` is rendered on
  Render and stamped `v2`; the run's copy on the worker is stamped `v1`.

**What the mismatch changes (FACT from code).** The engine version affects
only the positive-skill sum and the matched list (`scraper.py:685-693`).
Eligibility, filters and dedupe do not read it.

`reweight_from_evidence` gives every raw spelling of a concept that concept's
weight (`make_profile.py:342-346`). Under v1, each spelling that matches adds
the weight again; v2 counts each concept once (`skill_concepts.score`,
`skill_concepts.py:673-706`).

**INFERENCE:** a job description naming one concept several ways ("react",
"react.js", "reactjs") scores higher in production than the evaluated v2
behaviour intends, and `matched_skills` shows raw terms instead of concept
display names.

**Related, deliberately not queried.**
- `render()` also reads `SWEEP_CANDIDATE_TITLE_GATE` in the rendering process
  (`make_profile.py:850`). That flag changes the free-source title gate, and
  `title_gate.build` reads `role_signals` / `role_evidence` from `derived`
  (`title_gate.py:277-292`). Same class as D-1; see §V R-9.
- It also means the §M render projection must carry those two fields
  (corrected there).

### A.4 D-1 decision (Scenario B: the worker effectively uses v1)

**B1. Keep worker-env stamping for one-track (schema 1); use explicit engines
only in schema 2.**
- Upside: no change to public one-track scoring.
- Downside: the **same résumé would score v1 on its own and v2 once a second
  résumé is added**. The first track's scores and matched skills would change
  just because another track exists.
- A track's view in a multi-track Sweep would not match a one-track Sweep of
  that résumé.
- The rule the release depends on — a profile carries the engine that
  derived it — would stay broken for one-track.
- **Rejected.**

**B2. Fix explicit engine propagation for one-track first, as its own
production change, then build Multi-Track on it.**
- Code boundary:
  1. `make_profile.render(name, data, prefs, engine=None)`. `None` keeps
     today's environment behaviour. Otherwise the value is validated through
     `skill_concepts.engine_version(engine)`, and only `v1`/`v2` are accepted
     (`mixed` is refused).
  2. The worker's `_checked` accepts an optional top-level `engine` on
     `/v1/plans` and `/v1/runs`, and `render_profile` passes it through.
  3. Render records `state["derived_engine"] = skill_concepts.engine_version()`
     when a derivation completes (`derived_for_state`, `sweep/app.py:1576`).
     It is cleared together with `derived`, and `worker_client.plan` /
     `create_run` send it.
  4. The dry-run JSON adds `"profile_engine": config.PROFILE_ENGINE`, so
     Render can verify that the worker honoured the engine, and refuse a paid
     run on a mismatch.
  Local mode needs nothing: it renders in the same process that derived.
- **Rollout.**
  1. Deploy the worker first. It accepts the field, and a missing field means
     v1 exactly as today, so there is no behaviour change yet.
  2. Run the offline comparison (below) and get explicit sign-off.
  3. Deploy Render sending `engine`. From that moment **public scoring changes
     from v1 to v2**.
  4. Verify through `/v1/plans` `profile_engine` for a new session.
- **Offline comparison** (no provider or model call):
  - Take the 52 replayed synthetic derivations from §A.2 and a fixed committed
    job-row set with descriptions (chosen in Phase 0b; if none fits, a small,
    clearly labelled synthetic JD set).
  - Score every pair under a v1 stamp and a v2 stamp.
  - Assert that kept/dropped is identical.
  - Report the score-delta distribution, per-profile top-25 overlap and rank
    correlation, and the `matched_skills` text change.
- **Oracle deploy: required** (the worker change). Render deploy: required.
- **Rollback:**
  - Redeploy the previous Render, which sends no `engine`; the worker then
    renders from its environment (v1).
  - Or revert the worker.
  - No persisted state is involved: run profiles live per run, for 48 h.

**B3. Set `SWEEP_PROFILE_ENGINE_VERSION=v2` in the worker environment.**
- Same immediate effect as B2, with no code change.
- **Rejected.** It keeps derivation and scoring engines agreeing by
  **coincidence across two hosts**, the failure `scraper.py:318-325` was
  written to prevent. A Render rollback to v1 would then leave the worker
  stamping v2 onto v1 derivations. It also contradicts the release rule that
  the worker carries no engine variable.
- (A heuristic, such as inferring v2 from `skill_importance` in the payload,
  was also considered and rejected as fragile.)

**Decision: B2.** It **changes current public scoring from v1 to v2**, and
that is stated here so the change is approved explicitly, not discovered.
- It ships as **Phase 0b**, separately from Multi-Track, measured and signed
  off.
- Phases 1–3 are engine-agnostic: they must preserve behaviour under either
  stamp, and goldens cover both, so they may start after Phase 0a.
- **No multi-track run may happen before Phase 0b is live and verified.**

---

## B. Architecture Options Considered

### Option A — run the existing engine once per track, merge afterwards

- **Benefits**: almost no engine change. Each track is scored by today's code
  exactly.
- **Drawbacks**:
  - **Paid duplication.** The same request runs once per track: each run has
    its own output dir, so `.done_combos` cannot see across tracks
    [Audit §6.8].
  - **Free duplication.** Every ATS board and feed is fetched once per track.
  - **Runtime.** With `MAX_ACTIVE = 1` (`deploy/sweep_worker.py:78`), three
    runs queue behind each other.
  - **Cross-track dedupe** would have to run on output rows that have no
    `Description` and one score each [Audit §11].
- **Search V2 safety**:
  - Authorization would happen three times against the same accounts, each
    time on fresh readings, with no joint placement. A partial authorization
    for track A could consume capacity that B and C were priced against.
  - **BYOK breaks.** The worker spends held keys on the first run and releases
    them (`deploy/sweep_worker.py:859-862`), so runs two and three would find
    no keys.
- **Verdict**: **rejected.** It defeats the product goal (no duplicate paid
  work) and conflicts with the one-run BYOK key lifetime.

### Option B — make the whole engine context-driven

Replace every read of `config.SEARCH`, `SITES`, `SCORING` and `SETTINGS` with
an explicit context passed everywhere.

- **Benefits**: conceptually cleanest; no process globals.
- **Drawbacks**: the blast radius is the whole engine.
  - Globals are imported by name into `scraper.py` (`:70-74`) and read by the
    free predicates, `build_input`, `enrich`'s caller, `merge_jobs.py`,
    `rescore_from_apify.py` and 2,849 tests.
  - Most of those reads are **Sweep-level** and need no per-track value.
- **Search V2 safety**: it touches the paid scheduler's inputs everywhere, so
  the regression surface is large.
- **Verdict**: **rejected.** The audit shows track dependence is concentrated
  in three places: search intents, the free title gate, and
  eligibility/scoring. Refactoring everything else buys nothing.

### Option C — one Sweep context in globals, explicit per-track contexts only where tracks differ

- **Benefits**:
  - One engine process per Sweep, as today.
  - One unified plan through the unchanged paid scheduler, one free fetch, one
    output dir.
  - The **Sweep-level** configuration stays exactly where it is (config
    globals, loaded once per process), because there is exactly one Sweep per
    process.
  - Only track-dependent consumers change: plan construction, the free
    acquisition predicate, and eligibility/scoring. They read explicit,
    immutable `TrackContext` objects.
- **Drawbacks**: a real, focused refactor of `score_job` / `finalize` and
  plan construction. It needs a new rendered-profile schema for more than one
  track.
- **Search V2 safety**: the scheduler's input shape is unchanged: a
  `{site: [search dicts]}` whose dicts already carry per-unit
  `experience_years` (`scraper.py:1028-1036`). Placement, ceilings, the kill
  switch and fresh re-authorization are untouched.
- **Worker impact**: one child and one output dir per run, as today. The
  payload grows a `tracks` form.
- **Verdict**: **recommended** (§C).

### Option D — shared acquisition, then one scoring subprocess per track

Acquisition writes normalized rows (with descriptions) to a run file, and N
child processes of today's engine, each loading one track via `JOB_PROFILE`,
score them.

- **Benefits**: scoring code is reused untouched, and one-track scoring
  identity is exact.
- **Drawbacks**:
  - It needs a new raw-row persistence with full descriptions.
  - The engine re-finalizes after **every paid search** (`scraper.py:4088`,
    `:2571`), so N child spawns per checkpoint, or a new long-lived IPC layer.
  - The results-ready and early-publish paths would span processes.
  - Merging N output files reintroduces the dedupe/provenance problem Option A
    has.
- **Verdict**: **rejected.** More moving parts than the focused extraction
  Option C needs.

---

## C. Recommended Architecture

**Option C: "one Sweep, many tracks".** Split what today's rendered profile
mixes [Audit §3.2-3.4] into two layers.

1. **Sweep layer.** Preferences, sources, depth, spend cap, partial
   authorization and output dir, loaded into process globals exactly as today.
   It is identical for every track by construction.
2. **Track layer.** Search intents (keywords, experience band), the free title
   gate, and the scoring/eligibility configuration, held as explicit immutable
   `TrackContext`s.

The pipeline becomes:

```
tracks[1..3] ──► unified paid plan (canonical request identity, per-site round-robin,
                 provenance)  ──► existing paid scheduler + AccountPool (unchanged)
             └─► union free-acquisition predicate ──► one fetch_free
                                   │
             all rows (paid + free) ▼
   job facts once per copy (enrich, blocklist, experience floor, contacts)
   Sweep-level filters once per copy
   per track: relevance → eligibility → score          (pure; nothing written onto the row)
   cluster copies by job_key → per-track best copy → best track → rank
   write CSV/JSON: today's columns + (multi-track only) best_track, track_evals, found_by
```

The plan is bound between Confirm and run by a **plan hash** that the engine
itself computes and checks before any account is read (§G).

**Compatibility keystone.** A one-track Sweep renders today's **schema-1**
profile byte for byte. The engine treats a schema-1 profile as one implicit
track built from the same globals. One-track plans, scores, filters, dedupe
and outputs are therefore today's, which golden tests will enforce. Only
multi-track Sweeps (2–3 tracks) use the new **schema-2** profile and the new
columns.

**Prerequisite (amended 2026-09-25).** Every track's engine is carried as data
from derivation to scoring: Phase 0b (B2, §A.4) for one-track, and the track
payload for multi-track. Today the public worker stamps v1 from its own
environment (§A.3). The architecture itself is unchanged by D-1.

---

## D. Core Data Model

- **Current fact.** One rendered profile mixes résumé data, search
  preferences, the generated cap and the partial authorization. The profile
  name is at once the module, the directory, the ledger scope and the export
  name [Audit §3, §19 #10, #15].
- **Problem.** Several résumés cannot share one such artifact, and a name
  cannot identify a track.
- **Decision.** Four explicit layers — candidate/session, track, Sweep, run
  authorization — plus the unified plan and track-aware job results, as
  below.
- **Why.** Each field goes to the layer whose lifetime and sharing it
  actually has, and the Sweep layer keeps today's representation.
- **Compatibility.** For one track every layer collapses back to today's
  single profile.
- **Risk.** The "ambiguous" rows below are product decisions that V1 fixes
  conservatively.

### D.1 Where each current field belongs

| Current field (source) | V1 layer | Why |
|---|---|---|
| `role_keywords`, `title_hints`, `title_exclude`, `skill_weights`, `penalty_terms` (model), domain halves/bonus, `field_summary`, `notes` (`derived`) | **track** | derived from one résumé |
| `years_experience`, `experience_months` (`derived`, editable) | **track** | each résumé is derived and reviewed separately. **Ambiguous for product**: a candidate has one career, but two résumés can disagree. V1 keeps today's per-derivation semantics |
| engine version (v1/v2) | **track** (new explicit field) | §A.3 |
| `scope`, `locations`, `linkedin_locations`, `linkedin_remote_only`, `location_hints`, `remote_scopes`, `work_scope` (`_prefs`) | **Sweep** | they shape the shared provider requests (geo, `f_WT`) and the `", Remote"` location stamp (`scraper.py:1450-1456`), so they must be constant for a request to be shareable. **Ambiguous for product** (for example "remote for AI, onsite for MERN"); deferred |
| `max_age_days` | **Sweep** | LinkedIn `f_TPR` and Naukri freshness are request dimensions (`scraper.py:914-915`, `:995`) |
| `max_results` (depth) | **Sweep** | a per-site constant keeps every ceiling within a site equal, which §H relies on |
| `min_comp_usd` | **Sweep** | a job-global filter. **Ambiguous** (per-track pay floors); deferred |
| `avoid` | **Sweep** | "rank lower" from the Configure screen; added into **every** track's penalties at -12 (`make_profile.py:1261-1264`). **Ambiguous**; deferred |
| `exclude_levels` → `hard_drop_terms` | **Sweep** | a constant today (`sweep/app.py:3050-3052`) |
| `sites_enabled`, `free_only` | **Sweep** | one source choice per Sweep |
| `max_spend_usd` (generated cap) | **run authorization** | stamped at `/run` (`sweep/app.py:2410`) |
| `allow_partial_paid_sweep` | **run authorization** | `/run` over-cap tick (`:2416-2418`) |
| `byok_keys`, `run_key_ids`, `cap_usd`, `credit_total_usd` | **candidate session / run authorization** | never in track data |
| profile **name** | split three ways: **Sweep name** (module, output dir, ledgers, export filename); **track id** (identity); **track display name** (UI) | [Audit vocabulary §] |

### D.2 Session structures (Render, per session room)

```python
state["tracks"] = [            # 0..3, ordered = the user's display order
  {
    "id": "a3f9c2e1b7d40c55",  # secrets.token_hex(8); never derived from a name
    "name": "React Native Developer",  # display label: prefilled with the track's top role title
                                       # (first role_keywords entry, title-cased, deduped with " (2)"),
                                       # editable, 1-40 printable chars; never the filename (§N.1)
    "resume_name": "cv-mobile.pdf",    # basename only, as today
    "resume_text": "...",              # as today, per track
    "text_sha256": "…",                # for the same-résumé cache (§O)
    "parse": {...} | None,             # today's parse marker, now per track
    "derived": {...} | None,           # the COMPLETE derived dict, as today
    "engine": "v2",                    # skill_concepts.engine_version() in the DERIVING process,
                                       # recorded when the derivation completes (§A.4, B2)
    "reviewed": False,                 # set by POST /review for this track
    "selected": True,                  # included in the next Sweep
  },
]
state["profile"]        # unchanged meaning, now explicitly the SWEEP name
state[PREFERENCE_KEYS]  # unchanged (Sweep layer)
state["plan"], state["raw_plan"]  # unchanged keys; now the unified plan (§F)
state["byok_keys"], state["run_key_ids"], ...  # unchanged
```

- **Keep the complete `derived` per track in the session?** Yes. The review
  screen needs `skill_importance`, notes and experience, and editing re-renders
  from it, exactly as today.
- **Send the complete `derived` to the worker?** No. Only the projection
  `render()` reads (§M).
- **Track ids** are random, fixed at upload, and never a name. The id is what
  the engine, the plan, the result columns and the export join on.
  Display names never leave Render (§S).

### D.3 Rendered profile, schema 2 (only when 2–3 tracks are selected)

One module per Sweep, as today, so there is still one `profiles/<name>.py`,
one config selection, one output dir and one child. The Sweep-level sections
come from `prefs` exactly as `render()` builds them today. Track data moves
into one new literal:

```python
PROFILE_SCHEMA = {"version": 2, "engine": "multi"}
SITES = {...}; FEEDS = {...}; LOCATION_HINTS = [...]     # Sweep layer, as today
SEARCH = {"locations": [...], "salary_min": None, ["max_results": n]}   # no role_keywords
SETTINGS = {"min_comp_usd": ..., "max_age_days": ..., "remote_scopes": ..., "work_scope": ...,
            "max_spend_usd": ..., "allow_partial_paid_sweep": ...}
SCORING = {"hard_drop_terms": [...]}                      # Sweep-level only
TRACKS = [
  {"id": "a3f9c2e1b7d40c55", "engine": "v2",
   "SEARCH":   {"role_keywords": [...], "experience_years": 3},
   "SETTINGS": {"max_experience_years": 6, "candidate_experience_months": 40},
   "SCORING":  {"skill_weights": {...}, "penalty_terms": {...}, "frontend_terms": [...],
                "backend_terms": [...], "fullstack_title_terms": [...], "fullstack_bonus": 0},
   "ATS_TITLE_HINTS": [...], "ATS_TITLE_EXCLUDE": [...]},
  ...
]
```

- `render()` is split into `_sweep_sections(prefs, config)` and
  `_track_sections(data, prefs, config)`, both reusing today's validators:
  `_weights`, `_years`, geo checks and `check_module`.
- `render()` (schema 1) is unchanged in output. The new `render_sweep(name,
  tracks, prefs)` builds schema 2.
- `PROFILE_NAMES` gains `TRACKS`. `PROFILE_SCHEMA` 2 is a **meaning** change,
  so today's build refuses it loudly (`make_profile.py:986-992`). That is the
  desired fail-closed behaviour on a stale worker.

### D.4 Engine runtime: `TrackContext` (immutable)

```python
TrackContext(
  id, engine,                                        # engine decides v1 (per term) vs v2 (per concept)
  role_keywords, experience_years,                   # search intents
  title_hints, title_exclude,                        # free acquisition gate for this track
  skill_patterns | skill_concepts, penalty_patterns, # compiled once, exactly as scraper.py:311-333
  frontend, backend, fullstack_title, fullstack_bonus,
  hard_drop_patterns,                                # Sweep-level value, evaluated per track (identical)
  max_experience_years, candidate_experience_months,
)
```

- Built by one function, `track_context(overlay)`, over a **snapshot of
  `config`'s defaults** taken before any overlay. A schema-1 profile produces
  one context from the overlaid globals, which is the same data.
- Config-only scoring constants (`soft_drop_terms`, `soft_penalty`,
  `drop_penalty`, `experience_gap_penalty`, `timezone_gap_penalty`,
  `company_blocklist`, `drop_excluded`, `experience_aggregate`,
  `home_utc_offset`) stay **global**. `render()` never overrides them
  [Audit §3.2].

### D.5 Unified plan unit

Today's plan unit is a dict in `plans[site]` (`scraper.py:1028-1036`). V1
adds private keys only; `build_input` ignores unknown keys:

```python
{"keywords", "location", "company", "country", "experience_years", "salary_min", "max_results",  # today
 "_tracks": ["a3f9…", "7c01…"],      # every track that requested this exact provider request
 "_request_key": "sha256…",           # execution identity (§E)
 "_ledger": "site|keywords|location|company|<request_key[:16]>"}   # done identity (§E), date prepended at runtime
```

The dry-run JSON adds per entry `"tracks"` and `"ledger"`, and per plan
`"plan_version": 2` and `"plan_hash"`. All additions; Render's `plan.cost`,
`units`, `prefix` and `coverage` keep reading `sites` unchanged.

### D.6 Job result

In memory, per physical job (a *cluster* of copies sharing `job_key`):
`copies`, `evals[track_id] = TrackEvaluation(eligible, reason, score,
matched, is_fullstack, copy_index)`, `best_track`, `representative` (the best
track's chosen copy) and `found_by` (track ids whose paid units fetched any
copy, plus `"free"`).

Persisted: today's columns from the representative copy. For **multi-track
Sweeps only**, three appended columns (§Q):

- `best_track`
- `track_evals` (compact JSON `{id: {"s": score, "m": matched_skills}}`,
  eligible tracks only)
- `found_by`

No description is persisted (§Q).

---

## E. Search Execution Identity

**Current fact.**

- The provider request is exactly `build_input(site,
  effective_search(site, unit))`, sent with `run_timeout = 5 min` and
  `max_total_charge_usd = max_charge_usd(site, depth)` (`scraper.py:1334-1372`).
- That depends on:
  - the actor;
  - keywords, location and company;
  - `experience_years` (LinkedIn `f_E` bands at `:848-857`; Naukri
    `experience`);
  - `SETTINGS.max_age_days` (`f_TPR`, Naukri freshness);
  - `SITES.linkedin.remote_only` and `remote_geo` (`f_WT`);
  - the Indeed country derived from the location;
  - the effective depth.
- `combo_key` covers only site, keywords, location and company
  [Audit §6.3, §19 #3].

**Problem.** Two tracks can produce units with the same `combo_key` and
different provider requests. For example, the same keyword and city with
years 2 and 3 give `f_E` codes 3 and 4. Today the done ledger would skip the
second as "done", and a key-based dedupe would merge searches that are not
equivalent.

**Decision: three identities, each with one job.**

1. **Execution identity, `request_key`.** SHA-256 of canonical JSON
   (`sort_keys=True`, `separators=(",",":")`, `ensure_ascii=False`) of
   `{"actor": SITES[site]["actor"], "input": build_input(site,
   effective_search(site, unit)), "ceiling": str(max_charge_usd(...)) | None,
   "timeout_s": 300}`, computed **by the engine** at plan time.
   - Two units execute once **if and only if** their `request_key`s are
     equal, which means the provider would receive byte-identical start
     requests.
   - Strings are not normalized beyond what `build_input` already produces.
     Local-engine keywords are already lower-cased by `rank()`
     (`local_search.py:1230`). V1 applies no case-folding, alias merging (for
     example Gurgaon/Gurugram) or ignoring of depth or `f_E` differences.
     Any relaxation needs provider evidence first (U7/U8).
   - Because identity is derived from the function that builds the request,
     a dimension added to `build_input` later (a salary filter, say) is
     covered automatically. `salary_min`, which no adapter reads today, does
     not split units.
2. **Done / progress identity, `ledger`.**
   `{site}|{keywords}|{location}|{company}|{request_key[:16]}`, with the date
   prepended at runtime.
   - Multi-track only. A schema-1 Sweep keeps today's `combo_key` byte for
     byte, because within one track every unit with the same `combo_key`
     already has the same request (all other dimensions are constant within
     a track).
   - The engine emits it per unit in the dry run. Render uses the emitted
     value when present and falls back to `sweep/runs.py::combo_key`
     otherwise.
   - `runs.progress` splits on `"|"` at most four times (`sweep/runs.py:108`),
     so the suffix lands in the ignored company slot and the labels still
     render.
3. **Display identity.** `"<keywords> @ <location>"`, as today, plus the
   requesting tracks' names on Render. Two non-equivalent units can share a
   label; the track badge tells them apart.

**Provenance merge.** Every track's intents are expanded first. Units are
then grouped by `request_key`; the group's `_tracks` is the union of
requesters, in track order. Ordering (§F) never loses provenance, because the
merge happens before ordering.

**Why.** It is the only rule that is conservative by construction and needs
no hand-maintained list of dimensions per provider.

**Compatibility.**
- Within one track, Sweep-generated plans contain no duplicate requests. The
  three scopes' location lists have no repeated geography
  (`sweep/logic.py:174-215`), and the picker drops the Gurugram alias
  (`:129-131`). A test will assert that for every scope and picker
  combination.
- Hand-written local profiles that do repeat a request (for example
  LinkedIn `["India","Remote"]` with `remote_only`) would now run it once.
  That is strictly less spend for identical data.

**Risk.**
- Computing `build_input` during the dry run moves a bad-geography
  `ValueError` from run preflight to `/v1/plans`. That is earlier and safer,
  but new.
- `build_input` prints to **stdout** for an unmapped Naukri city
  (`scraper.py:1005`), which would corrupt `--dry-run --json`. Key
  computation must capture stdout, or that print must move to stderr.

### E.1 Review (2026-09-25): request identity — **APPROVED**

- **No credential.**
  - The hashed object is the actor id, the built `run_input`, the ceiling and
    the timeout.
  - `run_input` for LinkedIn is a public search URL plus depth fields; for
    Indeed and Naukri, search fields (`scraper.py:951-1009`).
  - The token lives only in the `ApifyClient` the input is sent through
    (`:1353`, `:1371`), never in the input.
- **No account state.**
  - Ceilings come from the fixed `ACTOR_CHARGE_MODEL` at the *free-tier*
    prices (`scraper.py:1118-1158`), not from the account's plan.
  - Pool assignment, memory, run slots and headroom are decided after
    identity and never enter it. That is correct, because one execution on
    any account satisfies every requester.
- **Every request-changing dimension is covered.**
  - `scrape_search` sends exactly `run_input`, `run_timeout = 5 min` and, when
    bounded, `max_total_charge_usd` (`:1334-1372`).
  - Nothing else reaches the provider: no memory option, build or proxy is
    passed.
  - Depth (after `results_per_run` and actor minimums), `f_E`, `f_TPR`,
    `f_WT`, geo, company and the Indeed country are all inside `run_input`.
  - Client-side behaviour (poll interval, the 360 s deadline) does not change
    what the provider executes.
- **Post-processing is a function of the identity.** `scrape_search` also
  stamps `", Remote"` when `remote_was_queried` (`:1234-1249`, `:1450-1456`).
  For LinkedIn that holds exactly when the URL carries `f_WT=2`; for Indeed and
  Naukri, exactly when the input location or `workMode` is remote. So two
  units with equal keys get equal stamps. `search_query` is display only.
- **Deterministic serialization.**
  - Inputs are JSON-native: strings, ints, bools and one-element lists.
  - The ceiling is `str(Decimal)`, and the LinkedIn URL's parameter order is
    fixed by code (`:893-916`).
  - `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)`,
    UTF-8 encoded.
  - Unicode forms are not normalized. Different forms get different keys,
    which can miss a dedupe but never merges two requests wrongly.
- **One consequence worth stating.** A non-LinkedIn unit that differs only by
  `company` produces an identical request, because no other adapter reads
  company, and so it collapses. That is correct; today it would run twice.

---

## F. Unified Plan Algorithm

**Current fact.**

- The plan is site-major: `SITES` order is linkedin, indeed, naukri
  (`config.py:72-110`). Within a site it is keyword-major, then location
  (`scraper.py:1015-1037`).
- The paid scheduler runs site by site (`scraper.py:2616`). Render's
  `plan.prefix` walks `raw["sites"]` in order (`sweep/plan.py:106-115`).

**Decision.** In the engine, after config load (Sweep globals) and context
construction:

```
for site in resolve_sites(args):                       # today's order; Sweep-level enablement
    seqs = [track_units(t, site) for t in tracks]      # each = build_search_plan(t.role_keywords,
                                                       #   SITES[site].locations or SEARCH.locations,
                                                       #   SITES[site].companies) with experience_years=t.experience_years
    for every unit: unit["_request_key"] = request_key(site, unit)
    requesters = {key: [track ids in track order that produced key]}
    merged, planned = [], set()
    cursors = [0] * len(tracks)
    while any cursor not exhausted:
        for i, t in enumerate(tracks):                 # round-robin, one unit per track per turn
            skip units in seqs[i] whose key ∈ planned  # already placed by an earlier track
            if a unit remains: take it; planned.add(key); merged.append(unit | {"_tracks": requesters[key]})
    apply --limit / max_searches_per_site as a prefix of merged (today's cap semantics, per site)
    plans[site] = merged
```

- **Deterministic.** Track order is the user's display order; everything else
  is list order.
- **One track** gives today's `plan_for_site` output exactly.
- **Shared units** are placed at the earliest slot any requesting track would
  give them.
- **Where it runs**: in the **engine**, the only code that can compute
  `request_key` (it needs `build_input` and the Sweep globals). Render never
  plans. That keeps the audit's rule "the plan comes from the engine's dry
  run" (`sweep/plan.py:1-6`).
- **Pricing and authorization** both consume this one `plans` dict:
  - Render prices `raw["sites"]` from the dry run.
  - The engine's `paid_phase_c2` builds `PaidEntry` from the same `plans`
    (`scraper.py:2472-2484`), using `_ledger` in place of the inline
    `combo_key` (`:2481-2482`, `:4030-4031`). That is the only scheduler
    change.

**Why.**
- The scheduler already accepts per-unit `experience_years` and ignores extra
  keys, so the unified plan fits its input shape with no change to
  reservation, placement or integration order.
- Keeping each track's own order inside the round-robin preserves
  `budget_order`'s premise that the affordable prefix carries the strongest
  keywords (`local_search.py:1266-1284`).

**Compatibility.**
- One-track plan JSON is identical, apart from additive fields that
  schema-1/legacy consumers ignore.
- Progress tiles come from the same `sites` lists.

**Risk.** Multi-track plans are larger: up to three times one track's units
before dedupe. Cost, runtime (`MAX_ACTIVE = 1` queueing) and partial sweeps
all become more likely (§H, §V).

---

## G. Confirm → Execution Contract

**Current fact.** The worker re-plans from `derived` + `prefs` at `/v1/runs`
(`deploy/sweep_worker.py:838-857`). No plan id or hash links what Confirm
priced to what executes. Render's coverage is advisory, and the engine
re-authorizes on fresh account readings (`scraper.py:2153-2254`)
[Audit §19 #5].

**Problem.** With a unified, deduplicated and interleaved plan, drift between
the plan Confirm priced and the plan that runs would mean paying for a
different search set. Causes include a worker deploy between the two steps,
changed config tables, or a changed engine version.

**Decision: deterministic regeneration with engine-side hash verification.**

1. The engine computes `plan_hash = sha256(canonical JSON {"v": 2, "tracks":
   [[id, engine], …], "units": [[site, actor, request_key, ceiling,
   tracks], …] in order})` over the **full** paid plan, before done-filtering.
   `--dry-run --json` returns it.
2. Render stores it with the costed plan (`state["plan"]["plan_hash"]`) and
   sends it on `POST /v1/runs` (§M). In local mode, `POST /run` passes it the
   same way.
3. The worker passes it to the child as `SWEEP_EXPECTED_PLAN_HASH`, next to
   `JOB_PROFILE` and `SWEEP_RUN_ID` (`deploy/sweep_worker.py:334-339`). It is
   not a secret.
4. The engine rebuilds the plan, recomputes the hash and, on mismatch,
   **exits with a new code 4 (`PLAN_CHANGED`) before `AccountPool.open`**.
   No account is read and nothing is charged. The worker maps exit 4 to a
   status error. Render shows "Your search changed since you confirmed it —
   review the new price" and returns the visitor to Confirm.
5. The fresh capacity check (`AccountPool.open` → `authorize`) is
   **unchanged** and still runs after the hash check. The hash binds *what*
   will run; the pool still decides *how much* can run, on the accounts'
   state at that moment.

**Why.**
- No new worker state: no stored plan, no plan TTL, no plan endpoint.
- The request body stays small.
- The check lives in the same function that executes the plan, so it cannot
  drift from it.

**Compatibility.**
- It applies to one-track paid runs too (same code path), where it can only
  refuse a run whose plan changed. A strict safety gain with no scoring
  change.
- Free-only runs price nothing and carry no hash.

**Risk.**
- A worker deploy between Confirm and Run makes every in-flight Confirm stale.
  That is the correct, fail-closed outcome, but visible to users.
- The hash must include every input that changes spend, and nothing that does
  not: capacity and the done ledger stay out.

### G.1 Review (2026-09-25): plan hash — **APPROVED, with two clarifications**

| Bound by the hash | How |
|---|---|
| Selected tracks and their order | the `tracks` list of ids |
| Track engine versions | `[id, engine]`. The engine does not change spend, but it binds the evaluation contract and catches a worker that ignored the engine |
| Ordered units, per-site order | the `units` list order, which is site-major |
| Provider/site, actor | per unit |
| Request identity | `request_key`, which covers every request dimension (§E.1) |
| Charge ceilings | per unit (also inside `request_key`; kept explicit for audit) |
| Provenance | the per-unit `tracks`, because Confirm's per-track coverage statement (§H) is computed from it |
| `--limit` / `max_searches_per_site` | the hash is taken **after** the cap, over the units that will run |

**Outside the hash, still correct:**
- **Account balances and capacity, and the exact account assignment.** They
  must be read fresh at execution. `AccountPool.open` → `authorize` stays the
  only capacity authority. Binding them would either refuse a run on every
  balance change or tempt skipping the fresh read.
- **Current done state.** The hash covers the full plan. Done-filtering is
  the same deterministic step on both sides (Render's `remaining_plan` for
  price; the engine's skip at run time), and a worker run starts in a fresh
  directory.
- **Spend cap and partial approval.** These are authorization values stamped
  by `/run` from the Confirm plan (`sweep/app.py:2410-2418`), and they are
  enforced fresh.

**Clarifications.**
1. Nothing name- or path-dependent may enter the hash. `/v1/plans` dry-runs a
   `plan_<hex>` profile while the run uses `beta_<run>`
   (`deploy/sweep_worker.py:789`, `:833`), and each has its own `output_dir`.
   So `config.PROFILE`, the output dir and the module name are excluded.
   `plan_estimate`'s use of `config.PROFILE` (`scraper.py:1973`) never reaches
   the hash.
2. The dry run and the run must be invoked with the same plan-shaping CLI
   flags. Worker runs pass none (`:348`, `:797-799`), and local `/run` passes
   none (`sweep/runs.py:145`).

---

## H. Partial-Sweep Ordering Strategy

**Current fact.**
- Partial execution is the plan-order prefix `todo[:k]` [Audit §8.6].
- `feasible(n)` depends on the *counts of each ceiling size* in the prefix
  (`scraper.py:1912-1918`).
- The scheduler is site-major.

**New fact (from the code, and it matters).** Depth is Sweep-level and per
site (`effective_search`, `scraper.py:1201-1215`), and the ceiling depends
only on site and depth (`max_charge_usd`, `:1167-1182`). So **every unit
within one site has the same ceiling.** Reordering units *within* a site
therefore cannot change the prefix length *k*: the multiset of ceilings in
any prefix is the same. It only changes *which* units are in the prefix.
Cross-site order, and with it *k*, stays exactly as today.

**Strategies evaluated.**

| Strategy | Keeps one-track order | Fair across tracks | Keeps per-track priority | Verdict |
|---|---|---|---|---|
| Concatenate tracks (A then B then C) | yes | **no**: A can take the whole prefix | yes | reject |
| Shared units first, then the rest | yes | partly | **no**: a track's top *unique* keyword waits behind every shared one | reject |
| Weighted round-robin | yes | configurable | yes | reject: no product basis for weights in V1 |
| Keyword-group round-robin (a track's whole keyword × locations block per turn) | yes | coarse (6 units per turn on the India scope) | yes | viable |
| **Unit-level round-robin with first-occurrence dedupe** (§F) | **yes** | **yes**: each track gains one unit per turn | **yes**: each track's own units stay keyword-major and prefix-closed | **recommended** |

**Decision.** Unit-level round-robin within each site, in track display
order; a shared unit takes the earliest slot any requester gives it. Nothing
changes about sites: LinkedIn units (all tracks, interleaved) still come
before Indeed units, as today.

**Confirm must say.** For a partial plan, per selected track: *"covers x of
y of this résumé's searches"*. Here x counts the covered units whose
`_tracks` include the track, so shared units count for every requester.
Also: *"n searches are shared by more than one résumé and run once"*.

It must **not** promise equal coverage, jobs, or coverage of any site beyond
the prefix. With LinkedIn first, a small credit often covers no Indeed search
for any track, the same as one-track today.

**Compatibility.** For one track this is the identity ordering, so partial
behaviour is today's.

**Risk.** Round-robin fairness is by *search count*, not expected value. A
track with a thin corpus still takes a turn. That is acceptable and honest
for V1.

---

## I. Free Search Strategy

**Current fact.**
- Free adapters apply `keep_title` / `keep_location` inside the adapter and
  discard rejects (`sources/ats.py:197-198`, `sources/feeds.py:94`).
- The predicates are passed in as parameters (`sources/__init__.py:36-37`),
  and `hires_home` is computed on the *unfiltered* board (`ats.py:192-195`).
- The request targets are config registries, not profile data; the one
  exception is the Himalayas `queries` (`make_profile.py:1219`), capped at 8
  (`sources/feeds.py:186`).

**Problem.** Running free acquisition once per track triples the requests.
Running it once with one track's gate drops rows the other tracks want.

**Decision.**

| Constraint | Where it stays |
|---|---|
| Board / feed / enterprise request targets (`ATS_BOARDS`, `FEEDS`, `OPTUM`, `ENTERPRISE`) | Sweep-level, fetched **once** (unchanged) |
| Location gate (`location_allowed`, `LOCATION_HINTS`) | acquisition, Sweep-level (unchanged) |
| Title gate | acquisition uses the **union** predicate `keep(t) = any(track admits t)`, where *admits* is today's `is_dev_title` with that track's own hints **and its own excludes** (never a union of excludes, which would delete other tracks' roles). **Per-track** re-evaluation afterwards, as a relevance test (§J) |
| Himalayas queries | one list built by round-robin across tracks' `role_keywords` with exact-string dedupe, capped at today's **8 in total** |
| `hires_home` | unchanged (board-level, job-global) |

**Why.** Acquisition breadth is bounded by construction.
- Each track's hints are already its résumé hints ∪ config's generic floor
  (`make_profile.py:861-865`), so the union adds at most the tracks'
  résumé-specific hints: 20–40 each (`local_search.py:591-626`).
- Nothing becomes a firehose: excludes are applied per track, and location
  stays shared.

**Compatibility.** One track: the union predicate is that track's predicate,
the Himalayas list is its first 8, and the per-track relevance re-check is a
no-op (every free row already passed it). Identical.

**Risk.**
- Keeping the Himalayas total at 8 gives each of three tracks about 3 queries
  instead of 8. That is chosen to avoid new 429 exposure
  (`sources/feeds.py:211-212`), and it is a real coverage trade-off (§V).
- Shadow boards stay single-context and are disabled for multi-track Sweeps.
  They are off anyway (`sources/shadow.py:64-129`).

---

## J. Eligibility Model

**Current fact.** [Audit §9] lists 20 rules; hard filters live inside
`score_job` (`scraper.py:643-676`).

**Problem.** Today every rule is applied for one profile, and a failing row is
removed for good. With several tracks, a rule that is really about one track
must not remove a job another track would keep.

**Why this split.** It follows what each rule actually reads [Audit §9 table].
Only rules that read résumé-derived fields become per-track; everything that
reads the job or the Sweep preferences stays single-pass.

**Decision: classification and order.**

| Class | Rules | Evaluated | Failing it means |
|---|---|---|---|
| Provider / acquisition | paid query dimensions; free location gate; free union title gate | before rows exist | never fetched / never created (as today) |
| **Job-global** (per copy) | repost blocklist (`blocked_company`); experience floor *parse* (`_required_experience_floor`); enrichment | once per copy | blocklist → copy discarded for every track |
| **Sweep-preference global** (per copy) | recency, compensation, remote reachability, visa/EOR (off), work arrangement, India geography | once per copy | copy discarded for every track |
| **Track-specific** | relevance; title hard drop (`hard_drop_terms`, identical across tracks in V1 but evaluated per context); experience floor comparison (`max_experience_years`); experience guard (`candidate_experience_months`, off); per-track `min_score` (config None) | per copy, per track | **that track** is ineligible for that copy |

**Relevance (new, and needed for correctness).** Track *t* is relevant to a
copy if the copy was fetched by a paid unit whose `_tracks` include *t*, or if
*t*'s own free title gate admits the copy's title. That is the test a
one-track Sweep of *t* applies implicitly: its paid rows come from its own
queries and are never title-gated, and its free rows pass its gate.

**Order of operations per copy.**

1. job facts;
2. per-track evaluation (relevance → hard filters → score);
3. keep the copy only if at least one track is eligible;
4. then the Sweep-preference filters.

For one track this is exactly today's order: `score_job` hard filters, then
`score_and_filter`'s preference filters (`scraper.py:2883-2951`). Stage
counts, `LAST_STATS` and telemetry therefore stay identical. The filters are
conjunctive, so for multi-track the order changes counts only, never the
surviving set.

**Discard vs mark.**
- A copy is **discarded** when it fails a job-global or Sweep-preference rule,
  or when no track is eligible.
- A track is **marked ineligible** otherwise. The job stays, eligible for the
  other tracks.
- If Track A rejects and Track B accepts, the job is kept with
  `eligible = {B}`, scored only for B, and shown in All and in B's view,
  never in A's.

**Compatibility.** One track: eligible for the one track ⇔ today's `score_job`
kept it. Paid rows stay relevant by provenance, and free rows passed the gate.

**Risk.** Relevance through the title gate applies to *other tracks'* paid
rows. Every track's gate includes the generic software floor
(`config.py:661-691`), so most developer titles are relevant to every
developer track. That is intended ("evaluate against every relevant track"),
but those views get longer.

---

## K. Scoring Refactor

**Current fact.** `score_job`:
- reads module tables compiled at import (`scraper.py:311-344`);
- reads the process-global engine bind (`:326-327`);
- writes eight fields onto the row (`:719-744`);
- under C2, memoises keep/drop on the row object (`SCORED`, `:2853-2860`);
- appends guard verdicts to the module list `experience_guard.DROPPED`
  [Audit §10].

**Problem.** A second track would overwrite the first's score. The memo would
hand B the verdict computed for A, and the engine bind cannot hold two
versions.

**Decision.** Split `score_job` into two pure pieces and one compatibility
wrapper:

1. `job_facts(row) -> JobFacts`, **once per copy**, stored as `row["_facts"]`.
   It holds:
   - the lower-cased text (`:637-641`);
   - `blocked` (`:647`);
   - the experience floor `years_required` (`:656`, config-only
     `experience_aggregate`);
   - contacts (`:741-744`);
   - enrichment: `enrich.enrich(row, SETTINGS["home_utc_offset"])` still
     writes `remote_scope`, `visa`, `eor`, `timezones`, `tz_gap` and
     `remote_regions`. These are job-global, identical for every track and
     read by the Sweep filters;
   - `remote?`;
   - the timezone over-gap in hours.
2. `evaluate(facts, row, ctx) -> TrackEvaluation(eligible, reason, score,
   matched, is_fullstack, exp_verdict)`. It is **pure**: it reads the
   `TrackContext`, never writes the row, and never appends to `DROPPED`.
   - The body is today's lines `:655-740`, with every global read replaced by
     `ctx.*` and `skill_concepts.enabled()` replaced by
     `ctx.engine == "v2"`. `skill_concepts.score` and `from_weights` are
     already pure (`skill_concepts.py:619-706`).
   - Memo: `row["_evals"][ctx.id]`, keyed **by track**, replacing the
     unkeyed `SCORED` flag. It has the same validity argument as today:
     `evaluate` reads only acquired fields and job facts.
3. `score_job(row)` stays as a wrapper for its existing callers
   (`rescore_from_apify`, the shadow engine, tests): `job_facts` +
   `evaluate(..., DEFAULT_CONTEXT)` + write today's fields. The default
   context is built from the overlaid globals.

**What stays process-global**:
- Sweep config;
- the config-only penalties and constants (§D.4);
- the `skill_concepts` registry (static data);
- `LAST_STATS` (job-level, plus per-track eligible counts);
- telemetry (job-level funnel; track **ids** only);
- `experience_guard.DROPPED`, now appended by the *caller* with the track id
  (the guard is off by default).
`skill_concepts.bind` still runs for the default context, so legacy callers
behave as today.

**Why.** It changes the one function whose global reads are track-dependent,
and leaves the rest of the engine's config reads alone (Option B's blast
radius avoided).

**Compatibility.** Golden tests assert that, for every existing
scoring-related test fixture and the replayed synthetic profiles,
`evaluate(DEFAULT_CONTEXT)` returns exactly today's
`score`/`matched_skills`/`is_fullstack`/kept-dropped. Existing suites stay
green unmodified.

**Risk.** This is the **biggest regression surface in V1**. The order of
enrichment against the early `return None` changes. That is invisible in the
output, because dropped rows are never written, but it must be proven by
parity tests, including telemetry stage counts.

---

## L. Job Deduplication / Provenance

**Current fact.** `rank_rows` sorts all eligible rows by score, stably, and
keeps the first row per `job_key`. The survivor is the highest-scoring copy,
ties go to earliest arrival, and losers' `source_site`, `apply_url` and
`search_query` are dropped (`scraper.py:2961-2970`, `:820-832`).

**Problem.** Picking one survivor by Track A's score before Track B is
evaluated decides B's copy for it.

**Decision: dedupe after per-track evaluation, per track, then combine.**

1. Group the surviving copies (§J) into clusters by `job_key`. Copies with no
   key are singleton clusters, as today (`:826-831`).
2. For each track *t*: `chosen_t` = the eligible copy with the highest
   `score_t`, ties to earliest arrival. This is exactly today's survivor rule,
   applied per track. Arrival order is unchanged: paid in plan order, then
   free (`:2439-2443`, `:4135`).
3. `best_track` = argmax of `score_t` over eligible tracks, ties to track
   display order. The **representative** = `chosen_best_track`; its fields
   become the row's columns.
4. Cluster order = `(-best score, arrival index of the representative)`,
   which equals today's final order for one track.
5. Provenance kept per cluster:
   - `found_by` = union of `_tracks` of the units that fetched any copy, plus
     `"free"` when a free source did;
   - per-track `score`/`matched` in `track_evals`.
   Losing copies' URLs and sources are dropped as today (V1 keeps no
   copy-level list, §W).

**Why.** It is today's survivor rule, applied once per track instead of once
globally, so no track's choice is made by another track's score.

**Different descriptions or URLs between duplicates.** Each track is scored
on its own best copy. So a copy whose description states "8+ years" can be
ineligible for a track while another copy of the same job stays eligible. The
displayed apply URL and source are the representative's.

**Compatibility.** One track: clusters with per-track max are the same as
sort-then-first-seen. Same rows, same order, same survivor.

**Risk.** In the per-track views, the row shows the representative's URL and
location while the score is that track's (possibly from another copy). It is
the same physical job, so this is acceptable for V1 and documented (§P).

---

## M. Worker / API Changes

**Current fact.**
- `_checked` allows exactly `profile`, `prefs`, `free_only`, `apify_token`,
  `owner` and `key_ids`, and refuses anything else
  (`deploy/sweep_worker.py:177-180`).
- Each run gets one rendered profile, one output dir and one child.
- Bearer auth, owner HMAC, BYOK hold/release and the kill switch are
  documented in [Audit §8, §15].

**Problem.** The worker must receive several tracks and bind the run to the
priced plan, without weakening auth or moving credentials.

**Why this shape.**
- One more accepted form and one hash field are the smallest change to a
  strict allowlist.
- There is no new plan storage.
- An old worker refuses the new form, which is the fail-closed behaviour
  wanted during rollout.

**Decision (smallest extension; everything else unchanged).**

| Endpoint | Change |
|---|---|
| `POST /v1/plans` | Accepts **either** `profile` (legacy, unchanged) **or** `tracks`, never both. `tracks` is a list of 1–3 `{"id": hex ≤ 32, "engine": "v1"\|"v2", "fields": <render projection>}`. The response is the dry-run JSON, now always carrying `plan_version`, `plan_hash`, and per-entry `tracks`/`ledger` |
| `POST /v1/runs` | The same `profile` \| `tracks` choice, plus `plan_hash` (**required** when `free_only` is false and `tracks` is present; optional on the legacy form until Render sends it). The worker renders with `render()` for 1 track and `render_sweep()` for 2–3, passes `SWEEP_EXPECTED_PLAN_HASH`, and records `track_ids` (random ids only) in `status.json` for rehydration |
| `GET /v1/runs/<id>` | Adds `track_ids`. Exit code 4 → `error: "plan_changed"`. Everything else unchanged |

- **Render projection (corrected 2026-09-25).** Exactly the keys `render()`
  reads:
  - `years_experience`, `experience_months`, `role_keywords`,
    `skill_weights`, `penalty_terms`;
  - `domain_half_a`, `domain_half_b`, `domain_title_terms`, `domain_bonus`;
  - `title_hints`, `title_exclude`, `field_summary`, `notes`,
    `skills_added`;
  - **plus `role_signals` and `role_evidence`**, which `title_gate.build`
    reads at render when `SWEEP_CANDIDATE_TITLE_GATE` is on in the rendering
    process (`make_profile.py:850`; `title_gate.py:277-292`). The first version
    of this design omitted them.

  The projection is one constant beside `render()`, with a test proving
  `render(projection) == render(full derived)` with the title-gate flag both
  on and off. It still excludes `candidate_name`, `skill_importance`,
  `local_ranking` and the guard records.
- **Engine field.**
  - Legacy `profile` form: optional top-level `engine` (Phase 0b, §A.4).
  - `tracks` form: `engine` is **required** per track.
  - The dry-run JSON reports `profile_engine`, or per-track engines, so Render
    can verify them.
- **Track display labels are not part of the worker contract.** Status
  carries `track_ids` (random hex) only. Labels stay in the browser's signed
  cookie and in Render memory (§N.1).
- **Explicit engine.** The worker renders each track with the `engine` it was
  sent (`render(..., engine=)`), never its own environment (§A.3, D-1).
- **Versioning.**
  - The request form (`tracks`) and the response's `plan_version` are the
    version signals.
  - An old worker refuses `tracks` and `plan_hash` with 400 (strict
    allowlist). Render treats that as "multi-résumé search unavailable" and
    **fails closed**: it never silently searches only track 0.
  - Deployment order: **worker first** (it accepts both forms), then Render.
- **Body size.** About 10 KiB per track measured, about 30 KiB for three
  (§A.2), far below `MAX_BODY`. A test asserts three maximal tracks fit.
- **Auth.** Unchanged: bearer on every endpoint, owner HMAC per run with 404
  for strangers.
- **Credentials.**
  - BYOK tokens stay where they are today: the worker's memory hold
    (`Queue._held`), popped at start, and on the child's stdin.
  - `tracks`, plans, hashes, status and results never contain a token or
    account id. `key_ids` is unchanged.
  - Kill switch, `AccountPool` and exact capacity rules: untouched.

**Compatibility.** A Render that still sends `profile` works against the new
worker exactly as today.

**Risk.** Three code paths briefly coexist: legacy profile, one track, and
multi-track. Tests pin each one.

---

## N. Session / Backward Compatibility

**Current fact.**
- Public session state is process memory and is lost on every Render restart
  or deploy. Only the signed cookie (`sid`, `beta_ok`, `run_id`) survives
  (`sweep/public.py:141-192`, `:199-239`).
- Local `app.state` is also process memory [Audit §12].

**Problem.** About 24 route reads assume one `derived` and one `resume_text`
at the top of state. A mechanical `tracks[0]` swap would hide the places
where multi-track semantics differ.

**Why this approach.** In-memory state cannot survive a deploy, so the
migration surface is the code, not stored data. Routing every access through
one helper module makes each changed route reviewable on its own.

**Compatibility.** With the flag off, or with one track, the screens and the
rendered artifact are today's.

**Decision.**
- A new pure helper module, `sweep/tracks.py`, owns every read and write of
  `state["tracks"]`: add, remove, select, rename, find by id, selected list,
  and validation. Routes go through it rather than indexing lists. The
  helpers validate shape on read; malformed track state resets that
  session's tracks and redirects to upload.
- **No dual-format session migration is needed.** A deploy empties
  in-memory state, so no old-format session can meet new code. What does
  cross a deploy is the cookie `run_id`, and old worker runs (48 h TTL),
  handled below.
- **One-résumé users.** Upload creates `tracks = [t]`. The screens show no
  track chrome while exactly one track exists, and derive → review → key →
  configure → confirm → run is today's flow. The rendered profile is
  schema 1, byte for byte. The only visible addition is an "Add another
  résumé (up to 3)" link, which is behind a flag, `SWEEP_MULTI_TRACK`,
  default off until Phase 7.
- `state["profile"]` keeps its meaning (Sweep name), and so do
  `state["plan"]`, `state["raw_plan"]` and every preference key. The
  top-level `state["resume_text"]` / `state["derived"]` / `state["parse"]`
  move into the track (routes are changed deliberately, route by route,
  through the helpers; no `tracks[0]` aliasing).
- **Rehydrate** (amended 2026-09-25): see §N.1. Labels survive a Render
  restart through the signed cookie. The existing `free_only = True`
  rehydration quirk (`sweep/worker_link.py:307`) stays out of scope (§V
  R-7).
- **Old result files.** CSVs without `best_track` are single-track: no tabs,
  today's rendering. `DictReader` tolerates both shapes.
- **Local operator mode.** Same routes, and the multi-track profile is
  written to `profiles/<sweep>.py` (schema 2). For multi-track Sweeps in V1:
  - `rescore` and `merge` are **disabled**: the re-rank editor edits one
    weight list, and a merge cannot re-score.
  - `applied` / `seen` stay keyed by the physical job (§R).
- **Session expiry.** The 2 h TTL drops every track. The worker run outlives
  it, as today.

**Risk.** The largest backward-compatibility risk sits here: routes that read
top-level `resume_text` / `derived` today (upload, review, derive, key,
configure, estimate, run, rescore; about 24 `derived` reads). Each one must
move to the helpers without changing one-track behaviour.

### N.1 Track-label persistence across a Render restart (amended 2026-09-25)

**Current fact (code and tests only; no production status file was read).**

- **Signed cookie.**
  - It is Flask 3.1.3's `SecureCookieSessionInterface`: itsdangerous, salt
    `cookie-session`, HMAC-SHA1, a JSON payload in base64 (optionally zlib).
    It is **signed, not encrypted**, so whoever holds the cookie can read it.
  - Keys today: `sid`, `beta_ok`, `run_id` (`sweep/public.py:148-152`, `:187`,
    `:567-568`).
  - Flags: HttpOnly, Secure, SameSite=Strict (`:465-473`).
    `PERMANENT_SESSION_LIFETIME = 2 h` (`:112`, `:472`) with
    `session.permanent` set at the beta gate (`:568`). Flask's
    `SESSION_REFRESH_EACH_REQUEST` defaults to `True` and the repo does not
    override it, so expiry **slides**: 2 h after the last request.
  - Browsers cap one cookie at about 4 KB (Flask warns above 4,093 bytes).
    Today's cookie is a small fraction of that.
- **Consequence (FACT).** After about 2 h of inactivity the browser loses
  `run_id`, and with it access to the run, even though the worker keeps the
  run for 48 h (`deploy/sweep_worker.py:69`). This is existing behaviour.
- **Worker `status.json`.**
  - Written at creation (`deploy/sweep_worker.py:851-856`) and updated on
    state changes.
  - Returned only by `GET /v1/runs/<id>` and `POST /v1/runs/<id>/stop`, both
    behind the bearer token (`:699-704`, `:731-735`) **and** the owner HMAC,
    with 404 for strangers (`:706-714`). `owner` is stripped from responses
    (`:716-725`).
  - Its contents are never logged. The worker's log lines are only "key held",
    "run created" and "run deleted" (`:764`, `:865`, `:901`), and gunicorn's
    access log records request lines, not bodies.
  - Deleted at TTL by the janitor (`:568-590`).
- **Render reconstruction.** `rehydrate` (`sweep/worker_link.py:285-313`),
  from the `before_request` hook (`sweep/public.py:527-535`), restores
  `profile`, `free_only = True`, a free plan, `run_started_at` and `proc` from
  the status. Nothing about tracks exists today.

**Options.**

| | R1: labels in worker `status.json` | **R2: labels in the signed cookie** | R3: slot numbers only ("Résumé 1..3") | R4: labels derived by the worker from data it already holds |
|---|---|---|---|---|
| Survives Render restart | yes | yes | yes (numbers only) | yes |
| Available exactly while the run is reachable | longer (48 h), but reachable only while the cookie holds `run_id` | **identical lifecycle to `run_id`**, same cookie | — | same as R1 |
| Protection | bearer + owner HMAC | signature (tamper-evident); HttpOnly, Secure, SameSite=Strict; readable by the cookie holder | — | bearer + owner |
| User text leaves Render | **yes**: onto the worker's disk for 48 h, a new host and a new retention period | **no**: stays in the user's own browser and Render memory | no | no, but it cannot reflect a user's rename |
| Contract change | the worker accepts and stores labels | none on the worker | none | the worker reads its rendered `TRACKS` into status |
| Meets "tell me which résumé fits" | yes | **yes** | **no**: "Résumé 2 is best" is not meaningful after a restart | partly: loses renames |
| Size | tiny | about 250 B for three labels | — | tiny |

**Decision: R2**, with meaningful default labels.
- The cookie already carries `run_id`. Storing the label mapping beside it:
  - gives labels exactly the lifecycle of the run's reachability;
  - survives a Render restart;
  - is tamper-evident;
  - adds no new host, no new retention period and no worker-contract change;
  - keeps user text off the worker.
- The only durable new data is one short label per track: no filename, no
  résumé text, no skills, no candidate name, no credential.

**Semantics.**
- **Label.** Each track's `name` (§D.2): prefilled at review with the track's
  top role title and editable. Validated on write *and* read: stripped, 1–40
  printable characters, no control characters.
- **Write.** Multi-track `POST /run` calls
  `public.remember_run(run_id, tracks=[[id, name], …])` in selected-track
  order. That writes `session["run_id"]` and `session["run_tracks"]`
  **together**. `forget_run` removes both. A one-track run writes no
  `run_tracks`, so its cookie is byte-identical to today's.
- **Rename before the run.** The name at `/run` is what the cookie stores.
- **Rename after the run started** (session still alive). The rename route
  updates the session track and, when that id is in `session["run_tracks"]`,
  the cookie label too. The results page shows the latest name.
- **Render restarts during a multi-track run.**
  - The cookie survives. `rehydrate` reads the status (owner-checked) and gets
    `track_ids` in plan order.
  - It builds `state["run_tracks"]` from them, using the cookie label for each
    id that appears in both and validates, and "Résumé *n*" (by position)
    otherwise.
  - The tracks' `derived` data is gone. As today, starting a *new* Sweep needs
    the résumés again.
- **`/results` after a restart.** Views and badges use `state["run_tracks"]`.
  "Best fit: *label*" stays meaningful. The recommended-résumé tooltip shows
  the label only, since the filename is not persisted.
- **Old one-track run** (no `track_ids` in status, no `run_tracks` in the
  cookie). Today's page: no views, no labels needed.
- **Malformed or missing metadata.** Degrade, never error:
  - A cookie `run_tracks` that is not a list of `[hex id, string]` pairs, an
    invalid label, or an id absent from the status: that entry is dropped and
    the fallback label used.
  - Malformed status `track_ids`: the run is shown as single-track (no views).
  - One category-only warning, never the values.
- **A cookie from a different run.** `remember_run` overwrites both keys
  together. If the ids still do not match the status, they are ignored.

**Why this is the minimum.** Nothing is persisted that R3 would not also
need, except the label itself. It rides an existing, signed,
owner-equivalent channel whose lifetime already equals the run's
reachability, and no worker change is needed.

**Compatibility.** One-track cookies and runs are unchanged. Old runs
rehydrate as today.

**Risk.**
- The cookie is decodable by whoever holds it. Labels are the user's own
  text, and the cookie is HttpOnly, Secure and SameSite=Strict.
- The 2 h sliding cookie lifetime limits every reachability path, labels
  included. That is existing behaviour (§V R-10).

---

## O. Derivation Quota / UX

**Current fact.**
- `DailyLimit` reserves one slot per complete derivation (two GPU calls),
  3 per IP and 60 in total per day. It gives the slot back on failure
  (`sweep/public.py:270-364`; `render.yaml:74-77`).
- The limits are environment variables.
- One derivation is one request under gunicorn's 600 s timeout, and a cold
  Modal start can take about 150 s per call (`render.yaml:17-21`, `:31`).

**Problem.** A three-résumé candidate spends the whole daily allowance on
setup, and any retry or failure then blocks them for the day.

**Why.** The derivation is the real GPU cost unit. Multiplying or discounting
it in code would misstate spend. The honest levers are:
- not paying twice for the same text;
- telling the user the cost before spending;
- tuning the existing environment numbers.

**Compatibility.** A one-résumé user sees today's quota behaviour, plus one
informational sentence.

**Decision.**
- **One résumé per request, one derivation per request**, exactly as today:
  upload track *n* → derive → review. There is no multi-file upload. Three
  cold-start derivations in one request could exceed the 600 s timeout.
- **Unit unchanged**: each derivation costs one slot. No silent
  multiplication, and no track-aware accounting.
- **Same-résumé cache.** Before deriving, look up `text_sha256` among the
  session's tracks and copy that track's `derived` (no model call, no slot).
- **UX statements.**
  - The upload screen states the cost before spending: "Each résumé uses one
    of your 3 résumé reads today (2 left)", from `DailyLimit.taken(ip)`.
  - A failed derivation says nothing was used, which is true because the
    slot is released.
  - Replacing a résumé is remove + add, and costs a new slot.
  - Returning to edit a derived track costs nothing.
- **Recommendation.** Keep the code as it is. **Raise `SWEEP_BETA_DAILY_PER_IP`
  to 6** when Multi-Track is enabled (Phase 7), so a three-résumé user can
  retry once. Keep `SWEEP_BETA_DAILY_TOTAL` = 60 as the GPU budget guard.
  That is a deployment-configuration change for later, not part of this
  design task.

**Risk.** The global 60 is only 20 three-résumé candidates a day. That is a
product and budget decision (§V).

---

## P. Results UX

**Current fact.** One Score column, three reachability sections, and GET
filters `min`/`source`/`q`/`sort` (`results.html:9-101`, `:224-262`)
[Audit §13].

**Problem.** A single score cannot say which résumé fits, and one list cannot
show one track's view.

**Why.** A query parameter reuses the existing filter mechanism. The badge
reuses existing cells.

**Compatibility.** A result with one track renders today's page.

**Decision.**

- **Views**: `All` (default), then one entry per selected track, as a
  `track=<id>` query parameter on `GET /results`. Filters and sort are
  unchanged and carried along, like `min`, `source`, `q` and `sort`
  (`sweep/app.py:2634-2648`).
- **Track *t* view** = jobs **eligible for *t*** (§J), ranked by *t*'s score.
  It is exactly what a one-track Sweep of *t* would list over the shared
  acquisition, not only jobs where *t* is best. The Score and Matched-skills
  cells show *t*'s values (`track_evals[t]`).
- **All view** = jobs eligible for at least one track, ranked by best score.
  - Score cell: best score, with a one-line badge "Best fit: *track name*".
  - When more than one track is eligible, an "also: *name* *score*" line.
  - Matched skills: the best track's.
- **Recommended résumé** = the best track's résumé filename, shown in the
  badge tooltip.
- **Sort / filter mapping**:
  - `score` sorts by the view's score.
  - `recent` and `experience` are unchanged, with ties broken by the view's
    score.
  - `min` applies to the view's score.
  - Buckets (India / remote / abroad) are unchanged; they are
    profile-independent [Audit §5].
- **No card redesign.**
  - The badge goes in the existing role or score cell
    (`results.html:70-71`).
  - The view switch is one small segmented control above the existing filter
    form (`:224`).
  - Shown only when the result has more than one track.
- **One track**: no switch and no badge. Today's page.

**Risk.** A per-track view built on eligibility alone includes weak matches
that other tracks fetched. Ranking and the 25-row section cap
(`sweep/logic.py:531`) keep them low. A "strong match" threshold is a later
tuning question (§V).

---

## Q. Persistence / Export

**Current fact.** Output rows are `OUTPUT_COLUMNS` (26 columns, one score, no
description). Exports use 16 `COLUMNS` (`sweep/exports.py:28-47`)
[Audit §14].

**Problem.** Track scores and the best résumé must survive to Render and to
the files, without breaking readers of today's columns.

**Why.** Appending columns, and only when there is more than one track, is
the one change that keeps every existing reader and every one-track byte the
same.

**Decision.**

- **Engine CSV/JSON, multi-track Sweeps only**: append
  - `best_track` (id),
  - `track_evals` (compact JSON, eligible tracks only),
  - `found_by` (`;`-joined ids, and `free`).
  `score` and `matched_skills` keep their meaning for the row: the best
  track's. One-track outputs are **byte-identical** to today's. The columns
  are appended, never inserted, the convention at `scraper.py:103-105`.
- **Description**: still not persisted. V1 scores every selected track during
  the run, so no later re-scoring needs it, and keeping it out preserves
  today's privacy and size profile, including the worker's 500-row pages to
  Render.
- **Exports, multi-track only**, appended after the existing 16 columns:
  - "Best résumé" (track display name, mapped on Render);
  - "Other matching résumés" (`Name (score); …`).
  In the per-track view the Score column is that track's score, as on
  screen. JSON gains a nested `"Résumés": [{"Name", "Score", "Matched
  skills", "Best"}]`. XLSX adds the same columns plus "Selected résumés" in
  the About sheet.
- **One-track exports**: unchanged, including column letters A–P
  (`exports.py:385-391`).
- `merge_jobs.csv_columns` must carry the appended columns when they are
  present (`merge_jobs.py:99-110`).

**Compatibility.** Existing consumers read by header (`DictReader`) or by
index into the first 26 or 16 columns, and both keep working.

**Risk.** `track_evals` is JSON inside a CSV cell. That is fine for Render,
which parses it, and the human exports never show it raw.

---

## R. History / Ledger Strategy

**Current fact.** `.done_combos`, `seen.tsv` and `applied.tsv` live under the
profile-named output dir. The done key omits several request dimensions
[Audit §4 #17-18, #41; §6.8].

**Problem.**
- Keying by profile name would split one Sweep's history across tracks.
- Keeping today's done key would let non-equivalent multi-track units collide.

**Why.** An execution fact (did this exact request run today) and a user fact
(have I seen or applied to this job) have different identities. Each keeps
its own.

**Compatibility.** One-track Sweeps write today's ledger lines, byte for byte.

**Risk.** Two ledger formats exist, each confined to its own Sweep directory.

**Decision.**

| Concern | Identity | Where |
|---|---|---|
| Execution done / progress | `ledger` = today's `combo_key` for one track; plus `\|request_key[:16]` for multi-track (§E) | `<output_dir>/.done_combos` (a fresh run dir on the worker; the Sweep dir locally) |
| Job seen (`--only-new`, `seen.tsv`) | `job_key` (physical job; profile-independent) | the Sweep's output dir, unchanged |
| Applied (`applied.tsv`, local only) | `job_key` | the Sweep's output dir, unchanged. It is a user/job fact, not a track fact |
| Track identity | random track id | session and plan only; never a directory or ledger key |

- Execution identity and job-state identity stay separate.
- Nothing is keyed by track name.
- Old local runs are untouched: new multi-track Sweeps use a new Sweep name
  and directory.
- Outcome analytics are out of scope (§W).

---

## S. Security / Privacy

**Current fact.**
- Résumé text is held per session.
- The full `derived` dict goes to the worker.
- Derivation logs print profile values to stdout, and escalation warnings can
  quote model values.
- Credentials are confined to the worker's memory and the child's stdin
  [Audit §15].

**Problem.** Up to three derivations and three profiles per candidate
multiply every one of those exposures.

**Why.** The two log changes and the projection are the minimum that stops
the multiplication. Credential handling needs no change because tracks never
touch it.

**Compatibility.** Nothing a visitor sees changes.

**Risk.**
- The count-only logger reduces what the operator can debug from Render
  logs.
- The escalation category still says which check failed.

**Decision.**

- **No new copies of résumé text.** It stays per track in the session,
  exactly as today. It is never sent to the worker, because the render
  projection excludes it.
- **Less profile data leaves Render.** The projection drops `skill_importance`
  (offsets and digests), role evidence, `candidate_name` and every provenance
  record. That is a reduction compared with today's full-`derived` payload.
- **Track display names** are user text. They stay on Render and in the
  browser's signed session cookie (§N.1): not sent to the worker, not written
  into the rendered profile or `status.json`, not logged, not in telemetry,
  and HTML-escaped on render (Jinja autoescape). Engine files carry only
  random ids. The cookie is signed, not encrypted, so its holder can decode
  it. The label is the only new durable datum, and defaults to a role title,
  never the résumé filename.
- **Render projection (corrected).** It now includes `role_signals` and
  `role_evidence`, which today's full-`derived` payload already sends, because
  the render-time title gate reads them (§M).
- **Logging exposure multiplies by up to three**, so two small changes become
  necessary in Phase 5:
  1. The web `derive()` passes a count-only logger to `make_profile.generate`
     instead of the default `print`. Today it prints skill names, tiers and
     rejected queries per derivation (`make_profile.py:350-360`;
     `local_profile.py:241-333`).
  2. `derive escalated` logs reason **categories**, not reason strings, which
     can quote model values (`sweep/app.py:1663`;
     `local_extract.py:575-586`).
  A broader logging cleanup stays out of scope.
- **Credentials**: no change to where they may exist (§M). Plans, hashes,
  `track_evals`, `found_by`, `status.json` and exports never carry a token,
  account id or key id.
- **Auth**: bearer, owner HMAC, `PUBLIC_ENDPOINTS` allowlist and the
  same-origin check are unchanged. New GET query parameters (`track=`) are
  validated against the session's track ids and never interpolated into a
  path.

---

## T. Implementation Phases

Each phase is independently reviewable, ends at a **STOP**, and keeps
one-track behaviour identical unless the phase says otherwise.

**Gates (amended 2026-09-25).**
- Phase 1 may begin after 0a.
- Phases 4–7, and **any multi-track run**, require 0b live and verified.
  Otherwise one-track (v1 on the worker) and multi-track (explicit v2) would
  score the same résumé differently.

| # | Objective | Files | Invariants | Tests | Oracle deploy? | Rollback boundary |
|---|---|---|---|---|---|---|
| **0a** | Freeze goldens | tests only | none changed | golden snapshots **under both v1 and v2 stamps**: one-track plan JSON over fixture profiles; `finalize` output over fixed row sets; exports; replayed synthetic `derived` → render | no | revert the test commit |
| **0b** | **D-1 fix (B2, §A.4)**: explicit engine from derivation to render, as a **standalone production change** | `auto-apply/make_profile.py` (`render(..., engine=)`), `deploy/sweep_worker.py` (`engine` field), `sweep/worker_client.py`, `sweep/app.py` (`derived_engine`), `scraper.py` (dry-run `profile_engine`) | no field → today's behaviour; kept/dropped identical under v1 and v2 | the §U "Phase 0b" block; offline v1 vs v2 comparison report; explicit sign-off before Render sends `engine` | **yes, worker first**, then Render | Render stops sending `engine` → worker env (v1); or revert the worker |
| **1** | Extract `TrackContext`, `job_facts`, `evaluate`, per-track memo; `score_job` becomes a wrapper | `scraper.py` | goldens byte-identical; all 2,849 existing tests unchanged | parity (outputs, stage counts, `LAST_STATS`); memo cannot leak between contexts; `evaluate` is pure (row unchanged) | eventually (no behaviour change) | revert one commit |
| **2** | Multi-context `finalize`: relevance, per-track eligibility, clusters, best track, appended columns when there are more than one context; schema-2 loading in `config` (`TRACKS`) | `scraper.py`, `config.py`, `make_profile.py` (`render_sweep`, `PROFILE_NAMES`, schema 2) | schema-1 path unchanged; schema 2 refused by an old build | A/B eligibility; B-only job; per-track isolation; cluster provenance; one-context parity | eventually | schema-2 files unused by anything live |
| **3** | Unified plan: `request_key`, round-robin, provenance, ledger keys, dry-run additive fields, `plan_hash`, exit 4; union free predicate, Himalayas union | `scraper.py`, `sweep/runs.py` (read `ledger`), `sweep/plan.py` (per-track coverage) | one-track plan and ledger identical; scheduler, placement, ceilings, kill switch unchanged | collapse / no-collapse (`f_E`, depth, geo, remote); round-robin; partial prefix per track; hash mismatch exits before any account read (fake pool asserts zero reads); no real calls | yes (engine) | no caller sends tracks yet |
| **4** | Worker contract: `tracks`, `plan_hash`, projection, explicit engine, `track_ids`, exit-4 mapping | `deploy/sweep_worker.py`, `sweep/worker_client.py` | legacy `profile` form unchanged; auth and BYOK unchanged | strict schema (both forms, never both); 1–3 tracks; id format; size limit; stale hash; ownership; restart recovery | **yes (worker first)** | Render keeps sending `profile` |
| **5** | Web: `sweep/tracks.py`, per-track upload/derive/review/rename/select, unified pricing with per-track coverage, run with hash, same-résumé cache, derivation log reduction; flag `SWEEP_MULTI_TRACK` (off) | `sweep/app.py`, `sweep/tracks.py`, `sweep/worker_link.py`, `sweep/public.py` (endpoints unchanged), templates `upload`, `review`, `confirm`, `configure` | flag off → today's screens; one track → schema 1 | route parity with the flag off; 2–3-track flows; quota statements; stale plan → re-confirm | no | flag off |
| **6** | Results and exports: views, badges, appended export columns, rehydrate with `track_ids` | `sweep/app.py`, `sweep/logic.py`, `sweep/exports.py`, `results.html` | one-track results and exports identical | per-track view membership; sort mapping; export columns; old single-track files | no | flag off |
| **7** | Enable in beta | environment only (`SWEEP_MULTI_TRACK=1`; quota decision) | — | manual free-only smoke through the paid guard; no paid call | no | flag off |

---

## U. Test Plan

No normal test spends provider money. Paid paths use the existing fake Apify
clients, fake pools and `bench.paid_guard`.

**Compatibility (one track)**
- One résumé produces `role_keywords` identical to today (replayed synthetic
  fixtures).
- One track produces a byte-identical rendered profile (schema 1).
- The one-track plan (`sites` lists, `max_results`, `charge_ceiling_usd`) is
  identical, and so are `combo_key`s and ledger lines.
- Identical `finalize` output, stage counts and `LAST_STATS` over golden row
  sets.
- Identical cost, spend cap, coverage and authorization outcome
  (full, partial, refused) through the fake pool.
- CSV, JSON, XLSX and HTML exports identical.
- With the flag off, every existing route test passes unmodified.

**Multi-track**
- 2 and 3 tracks.
- Same keyword, same Sweep settings and same `f_E` band collapse into one
  unit with `_tracks = [A, B]`.
- A different `f_E` band, depth, geo, remote flag or `max_age_days` does
  **not** collapse.
- Indeed collapses across differing years, because it has no experience
  field (a request-identity property).
- Provenance survives ordering and the `--limit` cap.
- A job eligible for B and not A is kept, in B's view only.
- Per-track score isolation: B's score never appears on A's evaluation, and
  the memo is keyed per track.
- `evaluate` leaves the row unchanged.
- A cluster keeps the per-track best copy and `found_by`.
- Best-track tie-break by display order.
- Recommended résumé.
- Per-track views, the All view and sort mapping.
- Export appended columns.

**Paid safety**
- Full, partial and refused authorization over unified plans.
- Within-site reorder leaves *k* unchanged (the property from §H), and the
  cross-site order is unchanged.
- Round-robin fairness counts per track under partial credit.
- Insufficient capacity.
- Duplicate account (existing tests, re-run on unified plans).
- Ceilings per provider.
- Kill switch off → no run, exit 3.
- Plan-hash mismatch → exit 4 **before** any account read (the fake pool
  records zero `users/me` calls).
- No token, account id or key id in any plan, hash input, `track_evals` or
  status.

**Worker**
- `_checked` accepts `profile` xor `tracks` and rejects both-or-neither,
  more than 3 tracks, duplicate ids, a malformed id, an unknown engine,
  unknown fields and an oversize body.
- Old worker + new Render fails closed with a message.
- Stale hash handling.
- Rehydrate old (no `track_ids`) and new runs.
- Ownership: 404 for strangers.
- Restart recovery unchanged.

**Phase 0b — engine propagation (D-1)**
- `render(..., engine=None)` is byte-identical to today's `render()` under
  every environment.
- `render(..., engine="v2")` stamps v2 in a process with no engine variables,
  the worker's real state (§A.3); `engine="v1"` stamps v1 even with
  `SWEEP_PROFILE_ENGINE_VERSION=v2` set.
- `engine="mixed"` or any unknown value is refused at the worker boundary
  (400, type-only message) and by `render`.
- The worker `_checked` accepts optional `engine` on both endpoints, and an
  old payload without it renders exactly as today.
- The dry-run `profile_engine` equals the stamp the run will bind, and Render
  refuses a paid run on a mismatch.
- The derived engine is recorded at derivation time. A Render environment
  change after derivation does not change an existing derivation's engine.
- **Kept/dropped identical** between v1 and v2 over the comparison fixtures;
  only `score`/`matched_skills` differ.

**Rehydration and track labels (§N.1)**
- A multi-track run survives Render state loss (simulated by a fresh
  `SessionStore` with the same cookie). `rehydrate` restores the view list in
  status `track_ids` order.
- The labels restored after the restart equal the labels at `/run`, and the
  "Best fit" badge and recommended-résumé text use them.
- A track renamed **after** `/run`, while the session is alive, shows the new
  label before and after a restart. One renamed **before** `/run` shows the
  name at `/run`.
- An old one-track run (status without `track_ids`, cookie without
  `run_tracks`) rehydrates exactly as today (byte-identical page).
- Malformed `run_tracks` (not a list, a bad pair, a non-hex id, a label over
  40 characters, a control character, an id absent from the status) drops
  only the bad entries and falls back to "Résumé *n*", with no exception and
  a category-only log line.
- The cookie payload after `/run` contains only `sid`, `beta_ok`, `run_id` and
  `run_tracks` of `[hex id, label]`: no filename, résumé text, skills,
  candidate name, token, key id or account id. Total cookie size stays under
  1 KB with three 40-character labels.
- `status.json` and the worker's create/plan request bodies contain no labels.
- An unauthorized owner, i.e. another browser's session holding a copied
  `run_id` but not the victim's cookie, gets 404 from the worker, and no
  labels are rendered for it.
- A cookie whose `run_tracks` belong to a previous run (ids not in the current
  status) is ignored.

**Privacy**
- Track names absent from worker payloads, rendered files and logs.
- The render projection excludes `skill_importance`, `candidate_name` and the
  provenance records.
- The derivation logger emits no skill or title strings.
- The telemetry record carries track ids only.

---

## V. Risks and Remaining Open Questions

| # | Item | Why it matters | Needed to close |
|---|---|---|---|
| **D-1 (fact resolved; decision taken; change pending approval)** | Previously unknown: which engine the worker stamps. Established 2026-09-25 from the deployed checkout (`f131a43`), the env file, the unit and the running processes: **v1**, while Render derives with v2 (§A.3) | Public sweeps score v1 today. Multi-track needs explicit engines | Decision B2 (§A.4). **Needs explicit approval of the v1→v2 public scoring change**, after the Phase 0b offline comparison. Not a blocker for Phases 0a–3; a blocker for Phase 4+ and for any multi-track run |
| D-2 | Sweep-level vs per-track location, remote and pay preferences | V1 makes them Sweep-level so requests can be shared (§D.1) | Product decision; per-track versions would cut sharing and need per-track request dimensions |
| D-3 | Quota numbers (3 per IP, 60 total) | A three-résumé user uses the whole daily allowance | Product/budget decision (§O recommends 6 per IP) |
| R-1 | Real-résumé `derived` size | Only synthetic fixtures were measured (§A.2) | Measure on approved real artifacts; the projection makes this non-blocking |
| R-2 | Multi-track plans are larger | More cost, longer runs behind `MAX_ACTIVE = 1`, partial far more likely | Measure plan sizes and cost for representative three-track candidates offline (dry-run only) |
| R-3 | Himalayas total cap of 8 | Fewer queries per track | Evidence on the 429 threshold before raising |
| R-4 | Per-track view includes weak matches | Longer lists | Later threshold tuning |
| R-5 | Scoring refactor regressions | Highest code risk | Phase 1 goldens and parity |
| R-6 | Worker CPU: up to 3× evaluation per checkpoint | The paid phase re-finalizes after every search (`scraper.py:2571`) | Per-track memo makes each (row, track) evaluate once; measure in Phase 2 |
| R-7 | Existing: `rehydrate` sets `free_only = True` for any run (`sweep/worker_link.py:307`) | Wrong display after a Render restart for paid runs | Separate fix; not part of Multi-Track |
| R-8 | Deploy ordering | An old worker rejects the new fields | Worker first; Render fails closed |
| R-9 | Other render-time flags read on the worker, e.g. `SWEEP_CANDIDATE_TITLE_GATE` (`make_profile.py:850`) | Same class as D-1: the free-source title gate for a public run is decided by the **worker's** environment; docs report V3 flags on in production (`docs/profile-presentation-stability-audit.md:778-797`) | Establish with the same allowlisted method (not done here, because the allowlist was engine variables only). The corrected projection already carries the inputs the gate reads |
| R-10 | Existing: the session cookie expires 2 h after the last request, while the worker keeps runs 48 h (§N.1) | Runs become unreachable after 2 h idle, labels included | Product decision; out of Multi-Track scope |

---

## W. Explicit Non-Goals (V1)

- More than 3 tracks, or managing résumés across sessions or accounts.
- Résumé rewriting, tailoring, application automation, outcome learning, or
  analytics.
- Per-track locations, remote scope, pay floors, avoid lists or sources
  (all Sweep-level in V1).
- Re-scoring a finished Sweep against a track added later. Persisting job
  descriptions.
- Any change to `AccountPool`, placement, ceilings, bounded exposure,
  concurrency, polling, the kill switch, or the fresh-reading authorization.
- Changing cross-site plan order (LinkedIn before Indeed).
- Looser request equivalence (case-folding, alias geographies) without
  provider evidence.
- R5 role families; the V3 guards; any change to derivation logic or keyword
  ordering.
- A database, persistent sessions, or a billing/quota system redesign.
- Local-mode re-rank and merge for multi-track Sweeps.
- A broad logging cleanup beyond the two derivation log changes in §S.
- A results-card redesign.
