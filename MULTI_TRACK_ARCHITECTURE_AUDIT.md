# Multi-Track Job Search — Architecture Audit of the Current Codebase

| | |
|---|---|
| Audit date | 2026-09-25 |
| Repository HEAD | `ff06a48` on `main` (level with `origin/main`) |
| Scope | Audit only. No production code, configuration, migration, push or deploy. |
| Method | Direct reading of the runtime code paths cited below, plus the existing offline test suites and module self-checks (§16). No provider was called. |

## How to read this document

- **FACT** is the default. Every factual statement cites `path::function` and
  line numbers (`path:L`) at HEAD `ff06a48`.
- **INFERENCE** marks a conclusion drawn from facts that the code does not
  itself state. Each one is labelled where it appears.
- **UNKNOWN** items are collected in §18 rather than guessed.

### Vocabulary: "profile" means three things in this codebase

The word is overloaded, and Multi-Track design will have to keep the
meanings apart:

1. **The derivation (`derived`)**: the dict the résumé engine returns and the
   web app keeps as `app.state["derived"]` (`sweep/app.py:1576`, `1846`).
2. **The rendered profile module**: Python source `profiles/<name>.py`, made by
   `auto-apply/make_profile.py::render` (`:1071-1338`). It combines the
   derivation, the user's search preferences, the spend cap and the
   partial-sweep authorization in **one** file. The engine reads only this.
3. **The profile name**: a string that is the filename, the Python module name
   (`config.py:1150`), the output directory (`config.py:1186-1188`), the key of
   every ledger under it, and the export filename (`sweep/app.py:2939-2940`).

---

## 1. Repository State

Commands run before any inspection (read-only, nothing modified):

```
$ git status
On branch main
Your branch is up to date with 'origin/main'.
nothing to commit, working tree clean

$ git branch --show-current
main

$ git log --oneline -10
ff06a48 feat(feedback): add in-app beta feedback form
314cb1e fix(public-confirm): a visitor sees the estimate, their credit and the searches covered, never the generated safety cap
f131a43 fix(public-byok): V2-D closeout — no configuration spends a visitor's keys without the account pool's authorization, so every paid rollback is fail-safe
b115bf4 docs(search-v2-d): Search V2's final decision record, the production configuration it recommends, and 28 of 28 mutations caught
c064825 fix(free-sources): greenhouse:postman is gone after nine 404s, the eight shadow boards stay in shadow, and native ids stay diagnostic
3a9445f feat(paid-polling): Indeed runs are polled every 2 s, and the Indeed plan keeps every location until a second observation says otherwise
e269e80 feat(public-byok): a visitor may fund one sweep from several of their own Apify accounts, and it runs whole or, only if they say so, as the part their credit safely covers
a87e3c9 docs(c5): one integrated Sweep ran all 90 paid searches on five accounts inside their ceilings, and most of them added nothing
8c430ae docs(paid-multi-account): two LinkedIn starts ran on two accounts, each ceiling held in both ledgers, and the provider confirms which account ran which
b6a974b feat(paid-multi-account): a developer-only clamp can lower each pooled account's usable capacity, never raise it

$ git remote -v
origin  https://github.com/kartikver-674/job-scraper.git (fetch)
origin  https://github.com/kartikver-674/job-scraper.git (push)
```

| Fact | Value | How established |
|---|---|---|
| Current branch | `main` | `git branch --show-current` |
| HEAD | `ff06a48bd023cad0b6213150a84b16e4da7442ae` | `git rev-parse HEAD` |
| Upstream | `origin/main` | `git rev-parse --abbrev-ref @{u}` |
| Ahead / behind upstream | 0 / 0 | `git rev-list --left-right --count HEAD...@{u}` |
| Tracked modified files | none | `git status --porcelain` |
| Untracked files | none | `git status --porcelain --untracked-files=all` returned nothing |
| Ignored paths present | 39 (e.g. `output/`, `graphify-out/`, `scratch_*.log`, `.venv`) | `git status --porcelain --ignored`; left untouched |
| `ff06a48` exists locally | yes (commit) | `git cat-file -t ff06a48` |
| `ff06a48` pushed | **yes** | `git ls-remote origin refs/heads/main` returns `ff06a48…`; `git reflog origin/main` shows `update by push` at `ff06a48`; `git branch -r --contains ff06a48` lists `origin/main` |
| Feedback feature in HEAD | yes | `sweep/feedback.py` (249 lines) registered at `sweep/app.py:3018-3019`; endpoint `"feedback"` in `sweep/public.py:90`; env keys `render.yaml:93-98` |

The working tree was **clean before the audit**. After the audit the only new
file is this one. The test runs (§16) left `git status --porcelain` unchanged
(verified before and after).

`graphify-out/` is dated 2026-09-18, a week of commits old. It was not used.

---

## 2. Current End-to-End Architecture

### 2.1 Two deployments, one Flask app

The same `sweep.app:create_app` (`sweep/app.py:236-3026`) runs as:

- **Local console**: a single operator. `app.state` is one process-wide dict
  (`sweep/app.py:247`). Profiles are written into `profiles/`, the engine runs
  as a local subprocess, and results land in `output/<profile>/`.
- **Public beta**, enabled by `SWEEP_PUBLIC_MODE=1` (`sweep/public.py:132-138`,
  `render.yaml:35-36`). `public.harden` (`sweep/public.py:437-653`) replaces
  `app.state` with a per-browser `SessionState` (`:242-267`, installed at
  `:497-499`). It stops profiles being written to disk (`keep_profile`,
  `:511-515`), allowlists endpoints (`PUBLIC_ENDPOINTS`, `:67-91`) and puts a
  beta code and a daily derivation budget on the door. Plans and sweeps are
  delegated to the Oracle worker through `sweep/worker_link.py::injections`
  (`:115-253`).

```
LOCAL    browser ─► Flask (app.state dict) ─► subprocess scraper.py --profile NAME
                                      └─ profiles/NAME.py ─► output/NAME/*

PUBLIC   browser ─► Render Flask (SessionState, signed cookie)
                        ├─► Modal/Oracle inference (résumé → derived)
                        └─► Oracle sweep worker (bearer + owner HMAC)
                               └─ renders profiles/beta_<run>.py ─► scraper.py child
                                  └─ /var/lib/sweep-worker/runs/<run>/output/*
```

Render runs one gunicorn worker with 8 threads (`render.yaml:28-32`), because
sessions and the budget live in process memory (`render.yaml:13-16`).

### 2.2 Stage-by-stage trace

Every stage lists its entry point, the data in and out, where state is kept,
and the single-résumé/single-profile assumption it makes.

**1. Résumé upload.** `POST /resume` → `sweep/app.py::resume` (`:1458-1512`).
- In: `request.files.get("resume")`, one file (`:1460`). The form has a single
  `<input type="file" name="resume" accept="application/pdf">` with no
  `multiple` (`sweep/templates/upload.html:86`).
- Local: saved to the fixed path `auto-apply/resume/resume.pdf` (`RESUME_DIR`,
  `:18`, `:1473-1474`), so each upload overwrites the last. Public: a
  `tempfile.mkstemp` file, deleted in the same request (`:1469-1471`,
  `:1489-1492`).
- A magic-byte PDF check runs in every mode (`sweep/public.py::looks_like_pdf`
  `:388-395`, called at `sweep/app.py:1480`).
- Out: `state["resume_path"]` (a local path, or the filename in public mode)
  and `state["resume_text"]` (`:1496-1498`).
- Assumption: a new upload **clears** `PREFERENCE_KEYS`, `derived`, `profile`,
  `profile_source`, `plan` and `raw_plan` (`:1509-1511`). One résumé per
  session.

**2. Résumé parsing.** `extract` defaults to `resume_parser.extract_text`
(`sweep/app.py:264-266`). That is pypdf text plus NFKC normalisation
(`auto-apply/resume_parser.py:18-39`), returning one string.

**3. LLM / profile extraction.** `GET /review` renders `deriving.html` without
calling a model (`sweep/app.py:1590-1602`). The page auto-submits
`POST /derive` (`:1624-1747`).
- `derived_for_state` (`:1546-1578`) sets `state["parse"]` under `_parse_lock`
  and calls `derive(state["resume_text"], _prefs(state))`.
- `derive` (`:271-302`) calls `make_profile.generate(client, cfg.MODELS,
  resume_text, prefs, engine=...)`. In public mode it is wrapped by
  `public.metered` (`sweep/app.py:327-329`; `sweep/public.py:344-364`).
- The engine comes from `SWEEP_PROFILE_ENGINE` (`make_profile.py:470-483`);
  Render sets `local` (`render.yaml:39-40`). `generate` (`:568-635`) calls
  `generate_local` (`:556-565`), which calls `local_profile.generate`
  (`auto-apply/local_profile.py:147-382`):
  1. `local_extract.read` (`local_extract.py:981-1007`) makes two
     schema-constrained model calls, FIELDS then EMPLOYMENT (`:201-208`,
     `:276-281`), through `inference.provider`. Render sets `remote`
     (`render.yaml:58-59`). `route` (`:939-978`) runs deterministic grounding
     checks, and `read` recomputes years from the validated employment rows
     (`:996-1006`).
  2. `local_search.Market(output_dir)` and `local_search.fields_for`
     (`local_search.py:1388-1460`) derive `role_keywords`, `title_hints` and
     their provenance from the corpus.
  3. Optional V3 guards run: canonical, unknown-family, orphan, hard-drop and
     role-evidence (`local_profile.py:228-314`). All are off by default (§5).
- `make_profile._finish` (`:535-553`) then applies `split_compounds`
  (`:486-532`), `widen_skills` (`skill_scan.widen`) and either
  `reweight_from_evidence` (v2, `:285-376`) or `reweight_from_corpus` (v1,
  `:379-431`). Render requests v2 (`render.yaml:56-57`).
- Out: one `derived` dict, cached in `state["derived"]` (`sweep/app.py:1576`).

**4. Profile storage.**
- The derivation lives in `state["derived"]`: process memory, per session in
  public mode.
- The rendered file is written by `app.write_profile`. Locally that is
  `default_write_profile` to `profiles/<name>.py` (`sweep/app.py:377-385`).
  Publicly it is `keep_profile`, which keeps the source in
  `state["profile_source"]` (`sweep/public.py:511-515`).

**5. Profile review page.** `GET /review` → `review` (`sweep/app.py:1580-1622`)
renders `review.html` with `derived`, the `commodity` terms (weight ≤
`COMMODITY_WEIGHT` = 2, `:1514`), `importance_badges`
(`sweep/logic.py:1327-1351`) and a suggested name
(`make_profile.profile_name_for`, `make_profile.py:156-178`).

**6. Profile update/edit.** `POST /review` → `review_post`
(`sweep/app.py:1765-1848`).
- Weights: `logic.reweighted` (`sweep/logic.py:868-918`) reads the parallel
  `term`/`weight` lists plus `drop`, `add_skills` and `add_weight`.
- Experience: `logic.with_experience` (`:964-985`).
- Then `ensure_scope()`, `make_profile.render(name, kept, _prefs(state))`,
  `app.write_profile`, `state["derived"] = kept` and `state["profile"] = name`
  (`sweep/app.py:1837-1847`).
- Not editable in any route: `role_keywords`, `title_hints`, `penalty_terms`
  and the domain halves. `review.html` only displays role keywords as chips
  (`review.html:57-59`, `228-230`).
- Locally, `POST /rescore` (`sweep/app.py:2944-3016`) edits weights from the
  results screen.

**7. Search preferences.**
- `GET/POST /configure` (`:2148-2210`) → `commit_prefs` (`:970-1008`) →
  `logic._configure_overrides` (`sweep/logic.py:1080-1213`).
- A scope expands through `_SCOPE` (`:174-215`), and the result is stored under
  `PREFERENCE_KEYS` (`sweep/app.py:79-81`).
- `POST /estimate` (`:2212-2278`) prices a candidate copy and restores state
  afterwards.
- The free/paid choice is made at `/key/free` and `/key` (`_apply_choice`,
  `:1882-1920`).
- `sync_profile` (`:944-968`) re-renders the profile from state before every
  pricing.
- `_prefs(state)` (`:3047-3081`) is the preference dict handed to
  `render`/`derive`.

**8. Search planning (common).**
- The plan always comes from the engine's dry run,
  `scraper.py --profile NAME --dry-run --json` (`scraper.py::main`
  `:3798-3829`). The web layer never re-derives a plan (`sweep/plan.py:1-6`).
- `costed(profile)` (`sweep/app.py:1053-1128`) calls `fetch_plan`: locally
  `sweep/plan.py::fetch` (`:17-22`, a subprocess), publicly
  `worker_link.fetch_plan` (`sweep/worker_link.py:124-146`) → `POST /v1/plans`
  (`deploy/sweep_worker.py:777-812`).
- The full plan goes to `state["raw_plan"]` (`:1077`). The priced remainder,
  after `sweep/runs.py::remaining_plan` (`:42-65`) and `sweep/plan.py::cost`
  (`:35-89`), goes to `state["plan"]` (`:1127`).

**9. Free search planning.** There is no per-query free plan.
- `run_free` (`scraper.py:3780-3782`) is true when any of `ATS_BOARDS`,
  enabled `FEEDS`, `OPTUM` or `ENTERPRISE` is configured.
- The dry-run JSON reports only a count, `"free_sources"` (`:3822`).
- The only query-bearing free input is the Himalayas feed. Its `queries` are
  the profile's `role_keywords` (`make_profile.py:1219`, `_fmt_feeds`
  `:884-903`), capped at `max_queries` 8 (`sources/feeds.py:176-189`).

**10. Paid planning.**
- `plan_for_site` (`scraper.py:1058-1077`) calls `build_search_plan`
  (`:1015-1037`): keywords × locations × companies for each enabled site in
  `resolve_sites` order (`:1043-1055`).
- The depth is `effective_search` (`:1201-1215`). The provider charge ceiling
  is `max_charge_usd` (`:1167-1182`).
- Render's public coverage uses `plan.units`, `plan.prefix` and
  `plan.coverage` (`sweep/plan.py:92-139`), called from `costed`
  (`sweep/app.py:1108-1126`).

**11. Worker handoff.**
- Local: `POST /run` (`sweep/app.py:2303-2459`) stamps the cap and the
  partial flag into state (`:2410-2418`), re-renders and writes the profile
  (`:2424-2425`), then `start_sweep` = `sweep/runs.py::start` (`:135-147`)
  spawns `scraper.py --profile NAME --yes` with a copy of `os.environ`.
  `state["proc"]` holds that one child.
- Public: `worker_link.start_sweep` (`:149-160`) → `worker_client.create_run`
  (`sweep/worker_client.py:95-111`) → `POST /v1/runs` with
  `{"profile": derived, "prefs": _prefs(state), "free_only", "owner", "key_ids"}`.
  - The worker's `create_run` (`deploy/sweep_worker.py:814-866`) validates the
    body with `_checked` (`:173-204`) and renders `profiles/beta_<run_id>.py`
    in the checkout plus a copy at `<run_dir>/profile.py` (`:838-849`).
  - It writes `status.json` (`:851-856`) and queues the run
    (`Queue.submit` `:480-485`, `MAX_ACTIVE = 1` `:78`).
  - `default_spawn` (`:315-365`) starts the engine. BYOK tokens go on stdin;
    free runs log to `engine.log`.

**12. Provider execution.**
- Paid first (`scraper.py:3936-4129`): `scrape_search` (`:1319-1457`), either
  through the serial loop (`:4016-4105`) or through `paid_phase_c2`
  (`:2425-2753`). BYOK runs always use `paid_phase_c2` (`:3977-4003`).
- Free after (`:4132-4137`): `fetch_free` (`:287-294`) →
  `sources.fetch_free` (`sources/__init__.py:36-105`). That dispatches to
  `ats.fetch` (`sources/ats.py:174-203`), the feed adapters
  (`sources/feeds.py`), `optum.fetch`, `enterprise.fetch`, and
  `concurrency.fetch_boards` when a platform flag is on.

**13. Normalization.**
- Paid: `scraper.normalize` (`:175-198`) runs per dataset item inside
  `scrape_search` (`:1433-1441`). `search_query`/`search_rank` are stamped
  there, and `", Remote"` is appended to `Location` when the query was remote
  (`:1450-1456`).
- Free: each adapter builds the internal schema itself (`sources/ats.py::_row`
  `:153-172`, `sources/feeds.py::_json_rows` `:60-97`), then applies the
  caller's title and location predicates (`ats.py:197-198`; `feeds.py:94`).

**14. Filtering / eligibility.** `scraper.py::score_and_filter` (`:2863-2958`)
runs `score_job` (`:631-745`, with the hard filters inside it) and then the
preference filters. §9 details each rule.

**15. Deduplication.** `rank_rows` (`:2961-2970`) sorts by score, then calls
`dedupe` (`:820-832`) on `job_key` (`:786-817`).

**16. Ranking / scoring.** `score_job` computes the score, and
`rank_rows.sort` orders by it. The results screen re-sorts with
`logic.sort_rows` (`sweep/logic.py:520-524`).

**17. Run persistence.**
- Local: `_write_run_json` (`sweep/app.py:3029-3044`) writes
  `{profile, baseline_usd, planned}`, but **no code reads `run.json`** (grep of
  `sweep/*.py`, `scraper.py` and `deploy/*.py` finds only the writer and two
  comments). The same-day ledger is `.done_combos` (`scraper.py:3928-3933`,
  appended at `:4091-4093` and `:2578-2580`).
- Public: `status.json` via `RunStore` (`deploy/sweep_worker.py:256-313`).
  Paid runs also write `paid_account_ledger.json` (`scraper.py::AccountLedger`
  `:2038-2087`) and `paid_authorization.json` (`:2419-2422`).

**18. Results persistence.**
- `emit` (`scraper.py:3903-3914`) calls `finalize` and then `write_outputs`
  (`:3098-3105`). The write is atomic (`_replaced`, `:3076-3095`) to
  `<output_dir>/jobs_<YYYY-MM-DD_HHMM>.csv/json` (`:3869-3871`).
- It runs after every paid search, after the free phase, and once at the end
  (`:4151`). `seen.tsv` is appended at the end (`record_seen`, `:3059-3072`).

**19. Results loading.**
- Local `read_rows(profile)` (`sweep/app.py:649-686`) prefers
  `jobs_combined*.csv`, otherwise the newest `jobs_2*.csv`.
- Public `read_rows` → `worker_client.all_rows` (`sweep/worker_client.py:155-170`,
  cap 5000) → the worker's `rows_since` (`deploy/sweep_worker.py:654-670`,
  newest CSV, 500 rows per page).
- `rows_with_applied` (`sweep/app.py:691-711`) adds `_key` and `applied`.

**20. Results rendering.** `GET /results` → `_results_page`
(`sweep/app.py:2650-2713`) → `logic.shortlist` (`sweep/logic.py:816-840`) →
`bucket_rows` (`:1233-1252`) → `results.html::listings_table` (`:9-101`).

**21-23. CSV / JSON / XLSX export.** `GET /export.<fmt>` → `export`
(`sweep/app.py:2892-2942`), using the `EXPORTS` map (`:2879-2890`) and
`sweep/exports.py`. An HTML export also exists (§14).

**24. Restoration.**
- Local: none after a server restart. `app.state` is memory, `run.json` is
  never read, and `/results` redirects to upload without `state["profile"]`
  (`:2872-2873`).
- Public: `worker_link.rehydrate` (`:285-313`) runs from a `before_request`
  hook (`sweep/public.py:527-535`). It uses the cookie's `run_id`
  (`public.remember_run` `:180-188`) and restores `profile`, `free_only`,
  `raw_plan`, `plan`, `run_started_at` and `proc`.

---

## 3. Current Profile Schema

There is **no single canonical profile schema** in the code. Nine
profile-like structures exist:

| # | Structure | Defined at | Consumer |
|---|---|---|---|
| S1 | Gemini answer | `make_profile.RESPONSE_SCHEMA` (`make_profile.py:116-145`) | `_finish`, `render` |
| S2 | Local model answers | `local_extract.FIELDS_SCHEMA` (`:143-158`) and `EMPLOYMENT_SCHEMA` (`:216-249`) | `route`, `read`, `local_profile.generate` |
| S3 | `derived` (local engine) | return dict of `local_profile.generate` (`:335-382`), augmented by `_finish` | web state, `render`, worker payload |
| S4 | `state["derived"]` after review | `logic.reweighted` + `with_experience` output (`sweep/app.py:1800-1807`, `1846`) | every later render |
| S5 | Preferences | `sweep/app.py::_prefs` (`:3047-3081`) | `render`, `derive`, worker payload |
| S6 | Rendered profile module | `make_profile.render` (`:1071-1338`) | `config.py` import |
| S7 | Engine runtime config | `config` globals after `_overlay` (`config.py:1105-1136`, `1175-1188`) | `scraper.py` |
| S8 | Worker payload | `worker_client.create_run` / `plan` (`:95-111`, `:134-144`) | `deploy/sweep_worker.py::_checked` |
| S9 | Hand-written profiles | the files in `profiles/` | local CLI only |

### 3.1 Extraction prompts and schemas

- **Local engine (production, per `render.yaml:39-40`)**:
  - FIELDS call: prompt `FIELDS_PROMPT` (`local_extract.py:163-183`), schema
    `FIELDS_SCHEMA` with `name`, `years_experience`, `titles[]`, `skills[]`,
    `companies[]`, `education[]`, `institutions[]`, `projects[]` and
    `certifications[]`, all required.
  - EMPLOYMENT call: prompt `EMPLOYMENT_PROMPT` (`:251-273`), schema
    `{target_field, employment[{company, title, start, end, relevant}]}`.
  - The model name comes from `local_extract.model_name`; the default is
    `local_profile.DEFAULT_MODEL` (`local_profile.py:71`). `render.yaml`
    comments name qwen3:8b on Modal (`render.yaml:71-73`).
- **Gemini engine**: `SYSTEM_INSTRUCTION` (`make_profile.py:53-103`), the user
  prompt from `build_prompt` (`:181-199`, which includes locations, the avoid
  list and excluded levels), and `RESPONSE_SCHEMA` (`:116-145`). Models come
  from `apply_config.MODELS` (`auto-apply/apply_config.py:62`).

### 3.2 The `derived` dict (S3/S4), field by field

"Editable" means the web UI can change it. "Rendered to" names where
`render()` writes it.

| Field | Type | Origin (local engine) | Editable | Rendered to → consumed by |
|---|---|---|---|---|
| `candidate_name` | str | FIELDS `name`, grounded (`local_extract.py:584-586`) | no | not rendered. `profile_name_for` → profile name (`sweep/app.py:1611-1612`, `1749-1763`) → output dir, export filename |
| `field_summary` | str | `local_profile._summary` (`:98-108`) | no | docstring (`make_profile.py:1270`); review card (`review.html:43`) |
| `years_experience` | int | `months_from(...) // 12` (`local_extract.py:998-999`) | yes (`with_experience`) | `SEARCH["experience_years"]` (`make_profile.py:1295`) → LinkedIn `f_E` (`scraper.py:911-913`), Naukri `experience` (`:1006-1007`); `SETTINGS["max_experience_years"] = years+3` (`make_profile.py:1302`) → `score_job` floor gate (`scraper.py:656-658`) |
| `experience_months` | int | `months_from` (`local_extract.py:998`, `1004`) | yes | `SETTINGS["candidate_experience_months"]` (`make_profile.py:1306`) → `experience_guard` (off by default); review display |
| `role_keywords` | list[str] | `fields_for` + guards (`local_profile.py:221-314`) | **no** | `SEARCH["role_keywords"]` (`make_profile.py:1294`) → paid plan keywords; `FEEDS.himalayas.queries` (`:1219`); `title_gate.build` (flagged); `search_facts` (`sweep/app.py:1040`) |
| `skill_weights` | list[{term, weight 1-5}] | model `skills`, cleaned (`local_profile.py:199-200`), `NEUTRAL_WEIGHT` 3 (`:82`, `:343-344`), then `_finish` weighting | yes (weight/drop/add) | `SCORING["skill_weights"]` via `_weights` (`make_profile.py:1107`, `:711-738`) → `SKILL_PATTERNS` / `SKILL_CONCEPTS` (`scraper.py:311`, `:316`) |
| `penalty_terms` | list[{term, weight 1-12}] | the user's avoid list at `AVOID_SEVERITY` 6 (`local_profile.py:345-347`) | no (display only) | `SCORING["penalty_terms"]` negated, minus `exclude_levels` (`make_profile.py:1248-1251`), plus `prefs.avoid` at -12 (`:1261-1264`) |
| `domain_half_a` / `domain_half_b` | list[str] | `[]` (`local_profile.py:349-350`) | no | `SCORING["frontend_terms"]` / `["backend_terms"]` (`make_profile.py:1318-1319`) |
| `domain_title_terms` | list[str] | `[]` | no | `SCORING["fullstack_title_terms"]` (`:1321`) |
| `domain_bonus` | int | `0` | no | `SCORING["fullstack_bonus"]` (`:1322`) |
| `title_hints` | list[str] | `fields_for` | no | `ATS_TITLE_HINTS`: model hints ∪ `config.ATS_TITLE_HINTS` (`make_profile.py:861-865`), or `title_gate.build` when flagged (`:850-860`) → `is_dev_title` |
| `title_exclude` | list[str] | `[]` (`local_profile.py:354`) | no | `ATS_TITLE_EXCLUDE` (`make_profile.py:1337`) |
| `notes` | str | `local_profile._notes` (`:111-144`), plus `skill_scan` (`skill_scan.py:304`) | no | docstring (`make_profile.py:1276`); review (`review.html:94-96`) |
| `skills_added` | dict | `skill_scan.widen` (`skill_scan.py:301`) | no | comment block (`make_profile.py:1112-1119`) |
| `skills_split` | list | `split_compounds` (`make_profile.py:530-532`) | no | not rendered |
| `skill_importance` | list[dict] | `reweight_from_evidence` (v2, `:361-376`); holds occurrences as offsets and digests | no | review badges only (`sweep/logic.py:1327-1351`); not rendered |
| `role_signals`, `role_evidence`, `role_gate_rejected`, `hard_drop_restored`, `hard_drop_record`, `orphan_guard_record`, `canonical_guard_record`, `unknown_family_record`, `local_ranking`, `local_from_orphans`, `local_decision` | various | `local_profile.generate` (`:360-381`) | no | **not rendered** (`render` emits only `PROFILE_NAMES`, `make_profile.py:935-948`); transmitted to the worker inside the payload (§3.8) |

Two render facts matter for the current scoring shape:

- For local-engine profiles, rendering `frontend_terms: []`,
  `backend_terms: []`, `fullstack_title_terms: []` and `fullstack_bonus: 0`
  **replaces** config's defaults. `SCORING` is merged one level deep by
  `.update` (`config.py:1132`). `is_fullstack` can therefore never be true for
  those profiles (`scraper.py:696-701`).
- `SCORING["hard_drop_terms"]` is rendered from `prefs["exclude_levels"]`
  (`make_profile.py:1326`). No route ever sets `state["exclude_levels"]`: grep
  finds only the read at `sweep/app.py:3050`. So every Sweep-generated
  profile carries the constant list `["intern", "internship", "fresher",
  "trainee", "new grad", "junior", "jr"]` (`:3050-3052`), which **replaces**
  config's longer list (`config.py:893-916`, which includes "principal",
  "staff", "manager", "architect", "director", "head of", "vp" and "chief").

### 3.3 Preferences (S5) → rendered target

| `_prefs` key | Source | Rendered to (`make_profile.render`) | Engine consumer |
|---|---|---|---|
| `locations` | scope/picker (`logic._SCOPE`, `_configure_overrides`), default `["Remote"]` (`sweep/app.py:3049`) | `SEARCH["locations"]` (`:1296`) | `plan_for_site` (sites without their own `locations`) |
| `exclude_levels` | constant default (`sweep/app.py:3050-3052`) | `SCORING["hard_drop_terms"]`; also removed from penalties | `title_excluded` |
| `avoid` | Configure "words to rank lower" | penalties at -12 (`:1261-1264`) | `score_job` penalties |
| `min_comp_usd` | Configure | always written (`:1307`); None disables | `comp_ok` |
| `max_spend_usd` | set by `/run` only (`sweep/app.py:2410`) | `SETTINGS` when not None (`:1131-1133`) | spend guard / `PaidExposure` budget |
| `remote_scopes` | scope | `SETTINGS` when not None (`:1142-1143`) | `reachable` filter |
| `work_scope` | scope | `SETTINGS` when not None (`:1149-1150`) | arrangement / India filters |
| `location_hints` | scope/picker | top-level `LOCATION_HINTS` (`:1161-1164`) | `location_allowed` (free acquisition gate) |
| `max_age_days` | Configure | `SETTINGS` when not None (`:1140-1141`) | recency filter; LinkedIn `f_TPR`; Naukri freshness |
| `max_results` | Configure | `SEARCH["max_results"]` when not None (`:1121-1122`) | paid depth |
| `linkedin_locations`, `linkedin_remote_only` | scope/picker | `SITES["linkedin"]` whole-dict overlay (`:1175-1205`) | LinkedIn plan + `f_WT` |
| `sites_enabled` | Configure / free choice | `SITES[site]["enabled"]` (`:1211-1212`) | `resolve_sites` |
| `allow_partial_paid_sweep` | `/run` over-cap tick (`sweep/app.py:2416-2418`) | `SETTINGS` when not None (`:1137-1139`) | `paid_phase_c2(partial=...)` (`scraper.py:4002`) |

There is **no job-type preference** (full-time or contract, for example) in
`_prefs` or `render`. `SEARCH["salary_min"]` is always rendered as `None`
(`make_profile.py:1297`).

### 3.4 Rendered profile module (S6)

Top-level names allowed: `SITES`, `FEEDS`, `SEARCH`, `SETTINGS`, `SCORING`,
`ATS_TITLE_HINTS`, `ATS_TITLE_EXCLUDE`, `LOCATION_HINTS` and `PROFILE_SCHEMA`
(`PROFILE_NAMES`, `make_profile.py:935-948`).

Validation before anything is written:

- `validate_keys` (`:763-780`) rejects unknown section keys against the live
  config.
- `_weights` (`:711-738`) enforces the skill 1-5 and penalty 1-12 ranges.
- `_years` (`:794-801`) enforces 0-60 years.
- LinkedIn geographies must be in the verified table (`:1176-1205`).
- `_prose` strips docstring-escaping characters (`:918-930`).
- `check_module` (`:1014-1068`) proves the module is only literal
  assignments.
- The worker appends a `SETTINGS["output_dir"]` assignment and runs
  `assert_only_literals` (`deploy/sweep_worker.py:141-170`, `:233-249`).

Versioning:

- The file carries `PROFILE_SCHEMA = {"version": 1, "engine": <v1|v2>}`
  (`make_profile.py:1291`, `PROFILE_SCHEMA = 1` `:954`).
- `profile_schema` (`:962-994`) treats a missing stamp as legacy v1 and a
  future version as unreadable.
- `config.load_profile_module` (`config.py:1139-1172`) exits on an unreadable
  stamp.
- The `derived` dict and the `_prefs` dict carry **no** version.

### 3.5 Engine runtime representation (S7)

- `config._selected_profile` (`config.py:1093-1102`) reads `--profile` from
  `sys.argv`, else `$JOB_PROFILE`, **at import time** (`:1175`).
- `_overlay` (`:1105-1136`) mutates the module globals in place: dicts merge
  one level deep, lists are replaced.
- `PROFILE_ENGINE` comes from the stamp (`:1184`). `scraper.py` binds it
  process-wide with `skill_concepts.bind` (`scraper.py:326-327`;
  `skill_concepts.py:795-810`).
- `scraper.py` imports those globals by name (`:70-74`) and precompiles its
  scoring tables at import: `SKILL_PATTERNS`, `SKILL_CONCEPTS`,
  `PENALTY_PATTERNS`, `FRONTEND_PATTERNS`, `BACKEND_PATTERNS`,
  `FULLSTACK_TITLE_PATTERNS`, `HARD_DROP_PATTERNS`, `SOFT_DROP_PATTERNS` and
  `COMPANY_BLOCKLIST` (`:311-344`).

### 3.6 Session, persistence, worker and frontend representations

- **Session**: `state["derived"]`, `state["profile"]` (name) and
  `state["profile_source"]` (public, rendered source), plus the preference
  keys. Public mode keeps them in `SessionStore` memory (TTL 2 h, at most 500
  sessions; `sweep/public.py:112-115`, `:199-239`).
- **Persistence**: locally, `profiles/<name>.py` is overwritten by every
  review, estimate candidate, configure, run and rescore
  (`sweep/app.py:1839`, `2257`, `1005`, `2424-2425`, `3012`). On the worker,
  `profiles/beta_<run_id>.py` and `<run_dir>/profile.py` are kept until TTL
  cleanup (`deploy/sweep_worker.py:844-849`, `579-588`).
- **Worker**: the payload key `"profile"` holds the whole `derived` dict
  (S8, §3.8).
- **Snapshots/copies**: the worker's `<run_dir>/profile.py` is the only
  per-run snapshot. Locally, no per-run copy of the profile exists (§12).
- **Frontend**: server-rendered only. `review.html` reads `field_summary`,
  experience, `role_keywords`, `skill_weights`, `penalty_terms` and `notes`.
  `_weights.html` posts `term`/`weight`/`drop`/`add_skills`/`add_weight`
  (`_weights.html:44`, `66-68`, `81`, `102-109`).

### 3.7 Where the requested concepts live

| Concept | Where |
|---|---|
| Target job titles | `derived.role_keywords` → `SEARCH.role_keywords` |
| Skills | `derived.skill_weights` → `SCORING.skill_weights` |
| Years of experience | `derived.years_experience` / `experience_months` → `SEARCH.experience_years`, `SETTINGS.max_experience_years`, `SETTINGS.candidate_experience_months` |
| Location | preferences only (`locations`, `linkedin_locations`, `location_hints`); nothing from the résumé |
| Remote preference | preferences: `scope` → `remote_scopes` / `work_scope` / `linkedin_remote_only` |
| Job type | none |
| Exclusions | `avoid` (penalty), `exclude_levels` → `hard_drop_terms` (drop), `title_exclude` (free gate), `config.SCORING.company_blocklist` (config) |
| Keywords | `role_keywords` (paid queries, Himalayas queries) |
| Seniority | `hard_drop_terms`, config `soft_drop_terms` (`config.py:923`), `experience_years` → LinkedIn `f_E` |
| Salary | `min_comp_usd` (preference); `salary_min` always None |
| Eligibility | §9 |
| Ranking | §10 |

### 3.8 Worker payload (S8)

- `worker_client.create_run` sends `{"profile": <derived dict>, "prefs":
  <_prefs>, "free_only": bool, "owner": <HMAC>, ["key_ids": [...]],
  ["apify_token": ...]}` (`sweep/worker_client.py:104-110`).
- `_checked` allows only those six top-level fields, refuses anything else
  (`deploy/sweep_worker.py:177-180`), and requires `profile` and `prefs` to be
  objects (`:182-184`).
- The body limit is `MAX_BODY = 512 KiB` (`:90`, `:691`).

---

## 4. Single-Profile / Single-Résumé Assumptions

Classes: **UI** · **BE** (web backend) · **W** (worker) · **ENG** (shared
search engine) · **P** (persistence/serialization) · **X** (export) · **$**
(security/cost-sensitive).

| # | Location | Current behaviour | Why it is single-profile | Class |
|---|---|---|---|---|
| 1 | `sweep/templates/upload.html:86` | one `type="file" name="resume"`, no `multiple` | one file per upload | UI |
| 2 | `sweep/app.py:1460` | `request.files.get("resume")` | reads one file | BE |
| 3 | `sweep/app.py:1473-1474` | local PDF saved to the fixed `auto-apply/resume/resume.pdf` | the path is a scalar and is overwritten | BE, P |
| 4 | `sweep/app.py:1496-1498` | `state["resume_path"]`, `state["resume_text"]` | scalar keys | BE, P |
| 5 | `sweep/app.py:1509-1511` | a new upload pops `derived`, `profile`, preferences, plans | a second résumé erases the first | BE |
| 6 | `sweep/app.py:1523-1578` | one `state["parse"]` marker, one cached `state["derived"]` | one derivation per session | BE, $ |
| 7 | `sweep/public.py:270-342`, `render.yaml:74-77` | `DailyLimit` counts **one derivation** per slot: 3 per IP, 60 total per day | each extra résumé is another GPU-billed derivation | BE, $ |
| 8 | `sweep/app.py:1580-1622`, `review.html` | the review renders one `derived` | one profile on screen | UI |
| 9 | `sweep/app.py:1765-1848` | one name, one `render`, `state["profile"] = name` | the profile name is scalar | BE, P |
| 10 | `sweep/app.py` (40 uses of `state["profile"]`) | the name keys output dir, applied ledger, export filename, `read_rows`, `read_done` | name = directory = run identity | BE, P, X |
| 11 | `make_profile.render(name, data, prefs)` (`:1071`) | one derivation + one preference set → one module | the module format holds one `SCORING`/`SEARCH` | ENG |
| 12 | `config.py:1093-1102`, `1175-1188` | one profile is selected per **process**, at import | argv/env scalar; overlay mutates globals | ENG |
| 13 | `config.py:1105-1136` | `_overlay` mutates `SEARCH`/`SCORING`/`SETTINGS`/... in place | the globals hold one profile | ENG |
| 14 | `scraper.py:311-344` | scoring tables compiled at import | built from one `SCORING` | ENG |
| 15 | `scraper.py:326-327`, `skill_concepts.py:795-810` | `skill_concepts._BOUND` is process-global | one engine version per process | ENG |
| 16 | `config.py:1186-1188` | `SETTINGS["output_dir"] = output/<PROFILE>` | per-profile directory | ENG, P |
| 17 | `scraper.py:3928-3933`, `sweep/runs.py:23-32` | `.done_combos` lives in that directory; the key has no profile component | the ledger is scoped by directory | ENG, P, $ |
| 18 | `scraper.py:3045-3072` | `seen.tsv` in that directory | per-profile history | ENG, P |
| 19 | `sweep/app.py:2446`, `2746-2756` | `state["proc"]` holds one child; `_sweep_in_flight` | one sweep per session | BE |
| 20 | `sweep/plan.py:17-22` | `fetch(profile)` runs one dry run | one plan per profile | BE, ENG |
| 21 | `sweep/app.py:1077`, `1127` | one `raw_plan`, one `plan` in state | one priced plan | BE, $ |
| 22 | `sweep/app.py:111-130`, `2410` | `spend_cap_for(plan)` stamped into this profile's `SETTINGS.max_spend_usd` | the cap lives inside the scoring profile | BE, ENG, $ |
| 23 | `sweep/app.py:2416-2418` | `allow_partial_paid_sweep` stamped into the same profile | the authorization lives in the profile file | BE, $ |
| 24 | `sweep/app.py:2422-2423` | one `run_key_ids` list | one run funds from one key set | BE, $ |
| 25 | `sweep/worker_client.py:104` | payload key `"profile"` is one dict | single profile over the wire | W, P |
| 26 | `deploy/sweep_worker.py:177-184` | strict field allowlist; `profile` must be an object | an extra `tracks` field would get 400 | W |
| 27 | `deploy/sweep_worker.py:832-849` | one `profile_name = beta_<run_id>`, one rendered file, one output dir | one profile per run | W, P |
| 28 | `deploy/sweep_worker.py:78`, `368-590` | one child per run, `MAX_ACTIVE = 1` | runs serialize on the box | W |
| 29 | `deploy/sweep_worker.py:851-856` | `status.json` has scalar `profile_name` / `profile_path` | one profile per status | W, P |
| 30 | `sweep/public.py:180-192` | the cookie holds one `run_id` | one run per browser | BE, P |
| 31 | `sweep/worker_link.py:285-313` | `rehydrate` sets one profile name and `free_only = True` | restores one run | BE |
| 32 | `scraper.py:97-106` | `OUTPUT_COLUMNS`: one `score`, one `matched_skills`, one `is_fullstack` | result schema has one score | ENG, P |
| 33 | `scraper.py:2779-2839` | `to_output` maps one score | one score per row | ENG |
| 34 | `scraper.py:719-744` | `score_job` writes the score fields onto the row dict | one score slot per row object | ENG |
| 35 | `scraper.py:2853-2860` | `_score_once` stores the kept/dropped verdict on the row (`_scored`) | a second profile would receive the first's verdict | ENG |
| 36 | `scraper.py:2961-2970`, `820-832` | dedupe keeps the highest-scoring copy | the survivor depends on one profile's scores | ENG |
| 37 | `scraper.py:2843`, `2991-2996` | `LAST_STATS` module dict | one run's funnel | ENG |
| 38 | `scraper.py:206-239`; `sources/*` | free title/location gates read module globals and run inside the adapters | acquisition is filtered for one profile | ENG, $ |
| 39 | `make_profile.py:1219` | Himalayas queries = this profile's `role_keywords` | per-profile free queries | ENG |
| 40 | `sweep/app.py:573-686` | `read_done(profile, day)`, `read_live(profile, since)`, `read_rows(profile)`, `list_sweeps(profile)` | keyed by one profile | BE |
| 41 | `sweep/logic.py:1003-1060`, `sweep/app.py:688-711` | `applied.tsv` under `output/<profile>/` | per-profile applied state | BE, P |
| 42 | `results.html:15`, `70` | one Score/Match column | one score shown | UI |
| 43 | `sweep/exports.py:28-47` | one `Score` and one `Matched skills` column | one score exported | X |
| 44 | `sweep/app.py:2915-2922`, `2939-2940` | about sheet `("Profile", profile)`; filename `sweep-<profile>-<date>` | one profile per export | X |
| 45 | `sweep/app.py:1010-1051`, `_search_summary.html:13-21` | `search_facts` roles = `derived.role_keywords` | one list of roles | UI |
| 46 | `sweep/app.py:203-206`, `773-831` | `planned_keys(state)` / `snapshot` read one `raw_plan` | one progress grid | BE |
| 47 | `scraper.py:3881-3883`, `telemetry.py:137` | telemetry records one `profile` string | one per record | ENG |
| 48 | `sweep/logic.py:329` | `step_states` has a boolean `has_profile` | step logic for one profile | UI |
| 49 | `merge_jobs.py:23-24`, `rescore_from_apify.py:76-82` | operate on one `SETTINGS["output_dir"]` (`JOB_PROFILE`) | per-profile tools | ENG (local) |
| 50 | `sweep/feedback.py:127-133` | reads `free_only` / `allow_partial` / `byok_keys` | scalar context | BE (minor) |
| 51 | `sweep/public.py:576-598` | `/healthz` reports one `derivation_engine` for the process | per-process engine | BE (observability) |

---

## 5. Profile Dependency Matrix

The profile file carries both résumé-derived fields and preferences, so the
table says which kind a stage consumes. "(r)" means résumé-derived, "(p)"
means preference, and "cfg" means `config.py` defaults that Sweep-rendered
profiles do not override. Every guard module named below defaults **off**:
`experience_guard.py:45-54`, `title_gate.py:44-49`, `role_evidence.py:58-62`,
`canonical_guard.py:51-71`, `hard_drop.py:50-82`, `orphan_guard.py:44-60`,
`unknown_family_guard.py:55-68`, `semantic_scope.py:52-68`,
`attachment_guard.py:46-61`, `family_centrality.py:34-38`,
`sources/shadow.py:64-129`. `render.yaml` sets none of them.

| Stage | Class | Function(s) | Fields consumed |
|---|---|---|---|
| Search intent / query generation | **PROFILE-DEPENDENT** | `local_search.fields_for` (`:1388`); guards in `local_profile.generate` (`:228-314`) | (r) skills, employment rows; the corpus `Market` (shared) |
| Location generation | **PROFILE-DEPENDENT (p)** | `logic._SCOPE` (`:174-215`), `_configure_overrides` (`:1115-1159`) | (p) scope, picked locations |
| Provider query construction | **MIXED** | `build_search_plan` (`scraper.py:1015-1037`), `effective_search` (`:1201-1215`), `build_input` (`:951-1009`), `_build_linkedin_url` (`:860-916`), `_indeed_country` (`:919-948`) | (r) `keywords`, `experience_years` (LinkedIn `f_E`, Naukri `experience`); (p) `location`, `max_results`, `SETTINGS.max_age_days` (LinkedIn `f_TPR`, Naukri `freshness`), `SITES.linkedin.remote_only`/`locations`; cfg `remote_geo`, `SEARCH.country`, `INDEED_COUNTRIES`, `LINKEDIN_GEO_IDS` |
| Free-source execution | **MIXED** | `sources.fetch_free` (`sources/__init__.py:36-105`), `ats.fetch` (`sources/ats.py:174-203`), `feeds._json_rows` (`:60-97`), `feeds.himalayas` (`:191-225`) | request targets: cfg `ATS_BOARDS`/`FEEDS`/`OPTUM`/`ENTERPRISE` (not rendered by `make_profile`); (r) Himalayas `queries`; in-adapter gates (r) `ATS_TITLE_HINTS`/`EXCLUDE`, (p) `LOCATION_HINTS`; `hires_home` computed on the **unfiltered** board with cfg `HOME_LOCATION_HINTS` (`ats.py:192-195`) |
| Paid-source execution | **MIXED** | `scrape_search` (`:1319-1457`), `paid_phase_c2` (`:2425-2753`) | mechanics independent; inputs as above; (p) budget `SETTINGS.max_spend_usd`, `allow_partial_paid_sweep` |
| Pagination / depth | **MIXED** | paid: `max_results` → `limitPerSource`/`maxItemsPerSearch`/`maxJobs` (`:955-1008`); free: adapter constants (cfg `FEEDS.*.pages`, `OPTUM.max_pages`, `ENTERPRISE.max_pages`) | (p) `max_results`; Himalayas query count (r), capped at 8 |
| Raw parsing | **PROFILE-INDEPENDENT** | `normalize`, `normalize_naukri` (`:144-198`), `ats._row`, `feeds._json_rows` field maps | none |
| Normalization | **MIXED** | `scrape_search` post-processing (`:1430-1456`) | paid rows get `search_query = "<keywords> @ <location>"` from the query that fetched them; `", Remote"` appended when the query was remote (`remote_was_queried` `:1234-1249`, reads `SITES.linkedin.remote_only`, (p)). That changes `Location`, which `enrich.remote_scope` reads. cfg `description_max` |
| Experience extraction | **PROFILE-INDEPENDENT** | `_required_experience_floor` (`:434-481`) | job text; cfg `experience_aggregate` (not rendered). Its **use** is profile-dependent (next rows) |
| Eligibility filtering | **MIXED** | `score_job` hard filters, `score_and_filter` (`:2863-2958`) | §9 per rule |
| Title filtering | **PROFILE-DEPENDENT** | free: `is_dev_title` (`:225-239`); all rows: `title_excluded` (`:519-530`) | (r) `ATS_TITLE_HINTS`/`EXCLUDE`; `hard_drop_terms` from `exclude_levels`, a constant in Sweep; cfg `soft_drop_terms` (penalty only) |
| Location filtering | **PROFILE-DEPENDENT (p)** | `location_allowed` (`:206-222`, free only, at acquisition); `reachable` (`:353-374`); `onsite_or_hybrid` (`:269-278`); `in_home_country` (`:249-266`) | (p) `LOCATION_HINTS`, `remote_scopes`, `work_scope`; cfg `keep_restricted_if_hires_home`, `HOME_LOCATION_HINTS` |
| Experience filtering | **PROFILE-DEPENDENT (r)** | `score_job` (`:656-658`); `experience_guard` (`:666-673`, off) | (r) `max_experience_years`, `candidate_experience_months` |
| Skill matching | **PROFILE-DEPENDENT (r)** | `score_job` (`:685-693`); `skill_concepts.score` (`skill_concepts.py:673-706`) | (r) `skill_weights`; engine version bound from the profile stamp |
| Deduplication | **MIXED** | `rank_rows` → `dedupe` → `job_key` | the key is job-only (`req_number` / company+title / URL); **which copy survives** is decided by the profile score (`:2969-2970`); it runs after profile-dependent filtering |
| Scoring | **PROFILE-DEPENDENT** | `score_job` (`:631-745`) | (r) weights, penalties, domain halves; (p) avoid; cfg soft penalties, timezone penalty, `home_utc_offset` |
| Ranking | **PROFILE-DEPENDENT** | `rank_rows.sort` (`:2969`); UI `sort_rows` (`logic.py:467-524`) | score; UI sorts `recent`/`experience` fall back to score for ties |
| Final filtering | **MIXED** | `SETTINGS.min_score` (cfg None, `config.py:967`); UI `shortlist` (`logic.py:816-840`); `--only-new` (`scraper.py:3907-3912`, CLI) | query-string filters; seen history |
| Enrichment | **PROFILE-INDEPENDENT** | `enrich.enrich(row, SETTINGS["home_utc_offset"])` (`scraper.py:731`) | job text; cfg `home_utc_offset` (not rendered) |
| Results bucketing | **PROFILE-INDEPENDENT** | `linkedin_shortlist.bucket` (`auto-apply/linkedin_shortlist.py:148-165`) | job fields; hard-coded `_INDIA` |
| Persistence | **PROFILE-DEPENDENT** | `write_outputs` (`:3098-3105`), dirs from `config.py:1186-1188` | profile name → path; one score per row |
| Export | **MIXED** | `export` (`sweep/app.py:2892-2942`), `sweep/exports.py` | fixed columns; content carries the profile's score/`matched_skills`; the filename carries the profile name |
| Paid authorization | **MIXED** | `AccountPool.authorize` (`:2210-2254`), `plan.coverage` | plan **order** (derived from `role_keywords` order and preferences), ceilings (site + (p) depth), (p) budget; not query text (§8) |

---

## 6. Search Plan Representation

### 6.1 Planner entry point and in-engine unit

- Entry: `scraper.py::main` builds
  `plans = {site_key: plan_for_site(site_key, args) for site_key in enabled}`
  and drops empty lists (`:3771-3772`).
- Unit (a plain dict, `build_search_plan` `:1028-1036`): `keywords`,
  `location`, `company` (None unless `SITES[site]["companies"]`), `country`
  (`SEARCH.country`), `experience_years`, `salary_min`, `max_results`.
- No class, identifier or version is attached to the dict itself.

### 6.2 Ordering

1. **Site order** = `SITES` dict order: linkedin, indeed, naukri
   (`config.py:72-110`, via `resolve_sites` `:1055`). A profile's `SITES`
   overlay updates existing keys in place, so the order is kept
   (`config.py:1132`).
2. **Within a site**: keyword-major, then location, then company
   (`:1025-1027`). `sweep/runs.py:84-87` states the same.
3. **Keyword order** is the order of `role_keywords` that
   `local_search.fields_for` produces: `rank` (`:1219-1248`), then
   `budget_order` ("which of them the user's budget actually reaches",
   `:1430-1436`).
4. **Truncation** keeps a prefix: `--limit` or
   `SETTINGS.max_searches_per_site` (`:1074-1076`); `--test` keeps the first
   unit (`:1068-1071`).

### 6.3 Identifiers

| Identifier | Definition | Used for |
|---|---|---|
| `unit_id` | `paid_unit_id(n)` = `"paid_%03d"` over the **global** plan position across sites (`:1252-1253`; entries `:2472-2484`; Render mirror `plan.units` `sweep/plan.py:92-103`) | allocator, reservations, ledger, telemetry |
| `combo_key` | `"{YYYY-MM-DD}\|{site}\|{keywords}\|{location}\|{company or ''}"` (`sweep/runs.py::combo_key` `:23-32`; engine `:2481-2482`, `:4030-4031`) | `.done_combos`, progress grid, `remaining_plan` pricing |
| `query_fp` / `keyword_fp` | sha256 prefixes of `site\|keywords\|location\|company` / `site\|keywords\|company` (`paid_unit` `:1256-1274`) | telemetry only |

### 6.4 Serialization

- Dry-run JSON (`:3799-3828`):
  `{"profile", "sites": {site: [{"keywords","location","company"}]},
  "max_results": {site: int}, "charge_ceiling_usd": {site: str|None},
  "free_sources": int, "public_paid": "multi"|"single"|"off"}`.
- Depth and ceiling are **per site, taken from `plan[0]`**. `experience_years`,
  `country`, `salary_min` and per-unit `max_results` are **not** serialized.
- The costed plan (`plan.cost` `:79-89`) adds `lines[{site, searches, results,
  rate, subtotal, free, ceiling, label}]`, `total`, `total_searches`,
  `free_sources`, `bounded_exposure`, `unbounded_estimate` and
  `unbounded_searches`.
- `costed` adds `already_done`, `over_cap`, `shortfall`, `spend_cap`,
  `coverage{total_units, placeable_units, full, accounts, available_usd,
  bounded_exposure_usd, partial_estimate, single_account, funding_ids}` and
  `paid_unavailable` (`sweep/app.py:1088-1126`).

### 6.5 Worker payload representation

- **The plan is never sent to the worker.** The worker receives `derived` and
  `prefs` and re-plans by rendering the profile and running the engine: once
  for `/v1/plans` (`deploy/sweep_worker.py:786-806`) and again for the run
  (`:838-857`).
- No plan hash, plan id or plan version travels between Confirm and
  execution.
- **INFERENCE**: the plan that executes equals the one Confirm showed only
  because the same inputs and code produce the same plan deterministically.

### 6.6 Cost information

Per site: `rate` (from `config.SITE_RATES` × depth / `SITE_RATE_BASIS`,
`sweep/plan.py:60-76`) and the provider `ceiling`. Per plan: the estimate
`total`, `bounded_exposure` and `unbounded_estimate`. The engine recomputes the
ceiling per unit (`max_charge_usd`, `:2477-2483`).

### 6.7 Plan versioning

None on the plan JSON. The presence of `public_paid` works as a capability
check: if it is absent or not `multi`/`single`, paid is unavailable
(`sweep/app.py:1111-1116`).

### 6.8 Does search-plan deduplication exist today?

**At plan construction: no.** `build_search_plan` emits the full cross product
with no equality check (`:1024-1037`). What exists upstream:

- Locations: `_parse_chips` removes case-insensitive duplicates
  (`sweep/logic.py:271-276`).
- `role_keywords`: `local_search.validated` (`:986-993`) and `rank`
  (`:1227-1236`) remove duplicates by `strip().lower()`.

**At execution: a same-day idempotency filter, not a plan dedupe.**

- A unit whose `combo_key` is already in `done` is skipped: serial loop
  `:4032-4035`; C2 `:2642-2645`.
- In C2, a unit whose `combo_key` is still in flight waits for its twin
  (`:2638-2641`) and is then decided against `done`.
- What that means:
  - It happens after provider expansion (units are per site), keyed on exact
    strings (case-sensitive) and scoped to the date.
  - It is keyed **only** on site, keywords, location and company. It ignores
    depth, `experience_years` (`f_E`), `max_age_days` (`f_TPR`),
    `remote_only` (`f_WT`) and `country`.
  - Order is preserved.
  - A skipped unit contributes no rows. The rows carry the running unit's
    `search_query`.
- The pool's `authorize` excludes only combos already in the pre-run `done`
  set (`:2218-2219`). Two in-plan units with the same `combo_key` would both
  be counted and placed.
- Render's `remaining_plan` drops today's done combos from **pricing**
  (`sweep/runs.py:42-65`).

Two visually identical queries on **different sites** are never treated as
equivalent, because the site is part of the key.

---

## 7. Free vs Paid Planning and Execution

| Dimension | Free | Paid |
|---|---|---|
| Plan type | no unit list; a count `free_sources` (`:3822`) | list of unit dicts per site (§6.1) |
| Planner | config registries `ATS_BOARDS`, `FEEDS`, `OPTUM`, `ENTERPRISE` (`:3776-3782`) | `plan_for_site` / `build_search_plan` |
| Expansion | one fetch per board / feed / query | site × keyword × location × company |
| Query source | whole boards; Himalayas `q=` from `role_keywords` | `role_keywords` × preference locations |
| Identifiers | `Source = "platform:token"` (`ats.py:154`); telemetry board `platform:token` | `unit_id`, `combo_key` |
| Order in sweep | after paid (`:4132-4137`) | first (`:3936-4129`) |
| Execution | `fetch_free` in one pass (serial, or `concurrency.fetch_boards` per platform flag) | `scrape_search` via serial loop or C2 scheduler |
| Acquisition gate | **yes**: `keep_title` / `keep_location` inside the adapter | **no** `is_dev_title` / `location_allowed`; shaped by the provider query instead |
| Resume ledger | none (not in `.done_combos`) | `.done_combos` |
| Persistence | rows only in the finalized CSV/JSON | also `paid_account_ledger.json`, `paid_authorization.json` |
| Cost | `SITE_RATES` has no entry → free (`sweep/plan.py:65`, `:73`) | `SITE_RATES` × depth; provider ceiling |
| Account metadata | none | pool, `byok_keys`, `run_key_ids` |
| Progress UI | no tiles; the live feed is disabled for `free_only` (`sweep/app.py:735-736`) | tiles from `combo_key` |
| Public enforcement | `free_prefs` switches every paid site off and a token is refused (`deploy/sweep_worker.py:195-196`, `215-230`) | pool authorization always (`scraper.py:3977-3979`) |
| Failure interplay | runs after a partial paid phase | `PaidPlanRefused` exits 3 **before** the free phase (`:4004-4014`) |

**Dimensions that make two plan units execution-equivalent or not, as the
code builds them.**

- A paid provider request is fully determined by `build_input(site_key,
  effective_search(site_key, unit))`. That reads: the site, and therefore the
  actor (`SITES[site]["actor"]`); `keywords`; `location`; `company`
  (LinkedIn `f_C`); `max_results` after `results_per_run` and actor minimums;
  `experience_years` (LinkedIn `f_E`, Naukri `experience`); global
  `SETTINGS.max_age_days` (`f_TPR`, Naukri `freshness`); global
  `SITES.linkedin.remote_only` and `remote_geo` (`f_WT`, geo for "Remote");
  and the Indeed `country` derived from the location, or `SEARCH.country` for
  Remote.
- The charge ceiling follows from site and depth (`max_charge_usd`).
- The engine's own identity (`combo_key`) covers only site, keywords,
  location and company.
- **A free fetch** is determined by the board/feed URL, plus `q` for
  Himalayas. The rows kept from the same fetch differ per profile, because the
  gates run inside the adapter.

---

## 8. Paid Allocator and Partial-Plan Semantics

### 8.1 Account / key handling

- **Local**:
  - Keys live in `.env` as `APIFY_TOKEN` and `APIFY_TOKEN_<n>`.
    `read_env_tokens` reconciles `os.environ` with the file
    (`sweep/app.py:447-492`). `POST /key` writes `APIFY_TOKEN`
    (`:2036-2037`); `/second-key` adds a slot (`:2509-2556`).
  - The engine uses `_require_token`, one account with the most headroom
    (`scraper.py:3262-3308`), or `pool_tokens` when the multi-account and
    concurrency flags are set (`:1670-1675`, `:3977-3979`).
- **Public (BYOK)**:
  - `POST /key` → `_public_key` (`sweep/app.py:1962-2006`). `read_account`
    makes two free reads (`:351-370`, arithmetic `scraper._account_reading`
    `:1797-1818`). An HMAC digest of the account id refuses duplicates
    (`:1944-1951`, `:1983-1985`). At most 20 keys are allowed
    (`MAX_PUBLIC_KEYS` `:107`).
  - `worker_client.hold_token` sends the key to the worker, which holds it in
    memory for 45 minutes (`Queue.hold_token`,
    `deploy/sweep_worker.py:428-444`, `HOLD_SECONDS` `:74`).
  - The session keeps `{id, account digest, headroom_usd, capacity_usd}`
    (`:1996-2001`). `/key/forget` releases one key (`:2048-2070`).
  - At start, the worker's `held_tokens(owner, key_ids)` requires every named
    key to still be held (`:446-457`). The tokens are popped
    (`Queue._start` `:503`), piped on stdin (`default_spawn` `:353-364`) and
    released after creation (`:859-862`).
  - The engine reads them once with `byok_tokens` (`scraper.py:1713-1730`).

### 8.2 Kill switch and modes

- `SWEEP_PUBLIC_PAID`: any value other than empty or a clear yes means off
  (`scraper.py:1698-1700`; worker `:115-117`).
  - Off at the worker: `create_run` refuses with 503 (`:818-819`).
  - Off at the engine: exit 3 before any account read (`:3963-3971`).
  - The dry run then reports `public_paid: "off"`, which makes Render set
    `paid_unavailable` (`sweep/app.py:1115-1116`) and `/run` refuse with 503
    (`:2340-2347`).
- `public_paid_mode`: `"multi"` if `SWEEP_PAID_MULTI_ACCOUNT` is on, else
  `"single"` (`:1703-1710`). Render mirrors the account choice
  (`_funding_keys`, `sweep/app.py:1130-1138`); the engine applies it in
  `AccountPool.open(single=...)` (`:2153-2182`).

### 8.3 Estimate, cap, capacity

- Estimate: `sweep/plan.py::cost`.
- Cap: `spend_cap_for` (`sweep/app.py:111-130`) =
  max(\$0.50, 1.25 × estimate), and at least `bounded_exposure` +
  1.25 × `unbounded_estimate`, rounded up to the cent. It is stamped into the
  profile by `/run`.
- Account capacity: `_account_reading` headroom = min(monthly limit, plan
  credits) − used, floored to \$0.001; `usable_capacity` = headroom − \$0.01
  (`:1791-1794`). Memory and run slots are read too (`:1811-1818`).

### 8.4 Exact authorization (engine, fresh readings)

1. `AccountPool.open` (`:2153-2182`) reads every key again, collapses slots
   that belong to the same account, and applies `single`.
2. `AccountPool.plan` (`:2184-2204`) runs `project_assignment` →
   `place_units` over bounded units not yet done, **in plan order**.
3. `place_units` (`:1892-1946`) is exact bin-packing over whole-mill
   ceilings. It returns an assignment for the **longest prefix** of the plan
   that can be placed whole, found by binary search over prefix length
   (`:1920-1923`). `feasible(n)` depends only on how many units of each
   ceiling size appear in the first *n* units and on the account capacities
   (`:1912-1918`). Within one size, units take accounts in fill order, in plan
   order (`:1941-1945`).
4. `AccountPool.authorize` (`:2210-2254`):
   - `can_run` = placed units whose account has a run slot and enough memory
     (`:2220-2223`).
   - `k = placeable_prefix([(unit_id, ceiling) in plan order], can_run,
     budget)` (`:2224`).
   - `full = k == len(todo)`. The outcome is `full`, or `partial` (only when
     the user chose it and k > 0), or `refused` (`:2225-2226`).
   - `runnable = todo[:k]`, `skipped = the rest` (`:2227-2228`), with a
     `stop_reason` of unbounded, insufficient_capacity or budget
     (`:2232-2235`).
5. `placeable_prefix` (`:1949-1963`) walks units in order and stops at the
   first one with no ceiling, or not in `placed`, or whose ceiling would push
   the held total past `budget`.
6. `paid_phase_c2` writes the record (`:2490`). On `refused` it raises
   `PaidPlanRefused` (`:2498-2515`). On `partial` it sets `limit =
   pool.runnable` (`:2516-2517`).
7. At send time, the first unit not in `limit` stops the phase
   (`:2646-2648`, reported at `:2702-2712`). Each start also needs the
   sweep-level `PaidExposure.reserve` (`:2655-2658`, class `:1498-1604`) and
   an account-level hold (`pool.hold` `:2271-2287`).

Render's **advisory** version: `plan.coverage` (`sweep/plan.py:118-139`) uses
the session's stored capacities and `budget = spend_cap`. `plan.prefix`
(`:106-115`) builds the partial plan by taking the first *k* units across
sites in order.

Serial non-pool path (local only): it stops when `spent >= budget`, measured
from account deltas, before the next unit (`scraper.py:4036-4039`).

### 8.5 Worker payload after authorization

There is none. The worker only reports back. `status.paid_authorization`
carries the allowlisted `AUTH_FIELDS` (`deploy/sweep_worker.py:101-105`) read
from the engine's `paid_authorization.json` (`:637-651`). Render reads it
through `read_authorization` (`sweep/worker_link.py:227-238`) for
`sweep_state` (`sweep/logic.py:541-574`).

### 8.6 Required answers

- **Does partial execution depend on plan order?** **Yes.**
  - `placeable_prefix` iterates in plan order and stops at the first unit it
    cannot run (`scraper.py:1958-1962`).
  - `place_units` is defined over the longest *prefix* (`:1895-1896`,
    `:1920-1923`), and `authorize` runs `todo[:k]` (`:2227`).
  - The runtime stops at the first unit outside `limit` (`:2646-2648`), and
    Render prices `plan.prefix(raw, k)` (`sweep/plan.py:106-115`).
  - Tests assert "the first units in plan order"
    (`sweep/tests/test_search_v2_d.py:202-210`, `:823-825`).
- **What determines the prefix?** The first plan-order index *k* (among units
  not already done today) where one of these holds: the unit has no provider
  ceiling (`ceiling is None`: Naukri, `ACTOR_CHARGE_MODEL` has no entry,
  `:1154-1157`); its prefix is not placeable on the accounts, or its account
  lacks a run slot or memory; or the cumulative held ceilings would exceed
  `SETTINGS.max_spend_usd`.
  - Consequence: an enabled Naukri unit ends the prefix at its position, so a
    BYOK plan that includes Naukri can never be `full`. Naukri is disabled by
    default (`config.py:107`).
- **Does the allocator care about query contents?** **No.**
  - `place_units` receives only `(unit_id, ceiling)` and `(label,
    capacity)` (`:1892-1910`), and `placeable_prefix` only `(unit_id,
    ceiling)`.
  - The ceiling is a function of site and depth (`max_charge_usd`), so the
    **site** and **depth** matter; keywords and location do not.
  - Memory per unit is per site (`AccountPool.memory[site]`, `:2190-2193`).
- **Can changing plan order alter which searches execute during a partial
  sweep?** **Yes.** The runnable set is a positional prefix, so a different
  order puts different units in the first *k*. Because feasibility depends on
  the multiset of ceiling sizes in the prefix, reordering units with
  different ceilings can change *k* itself.
  - **INFERENCE** from `max_charge_usd`'s arithmetic at depth 15: a LinkedIn
    ceiling is \$0.046 and an Indeed ceiling is \$0.135. So moving Indeed
    units ahead of LinkedIn units changes which prefix fits a given capacity.

---

## 9. Eligibility Architecture

Order of application in a paid+free sweep:

1. Provider query (paid).
2. Adapter gate (free).
3. `normalize`.
4. `score_job` hard filters.
5. `min_score`.
6. Recency.
7. Compensation.
8. Reachability.
9. Visa and EOR.
10. Arrangement.
11. India geography.
12. `rank_rows` sort + `dedupe`.
13. `to_output`.

This whole chain re-runs on the cumulative `raw_rows` at every `emit`
checkpoint (`scraper.py:3903-3914`).

| Rule | Function | Inputs | Reads profile? | Job alone enough? | When | Permanent removal? | Class |
|---|---|---|---|---|---|---|---|
| Provider-side filters | `_build_linkedin_url` `f_E`/`f_TPR`/`f_WT`/geo (`:860-916`); Naukri `experience`/`freshness` (`:986-1008`); Indeed `country` | the unit, `SETTINGS` | yes: (r) `experience_years`, (p) `max_age_days`, `remote_only`, location | n/a | before acquisition | rows never fetched | **PROFILE-DEPENDENT** |
| Free title gate | `is_dev_title` (`:225-239`), applied in `ats.fetch` (`sources/ats.py:197-198`), `feeds._json_rows` (`:94`), optum/enterprise `keep_title` | `Title`; `ATS_TITLE_HINTS`/`EXCLUDE` | yes (r) | no | inside the adapter, before `fetch_free` returns | yes: the row is never created | **PROFILE-DEPENDENT** |
| Free location gate | `location_allowed` (`:206-222`) | `Location`; `LOCATION_HINTS` | yes (p) | no | inside the adapter | yes | **PROFILE-DEPENDENT (p)** |
| Repost-farm blocklist | `blocked_company` (`:347-350`), first check in `score_job` (`:647-648`) | `Company`; cfg `company_blocklist` | not rendered by Sweep | yes | after normalization, before dedupe | yes (this run) | **PROFILE-INDEPENDENT** (for Sweep profiles) |
| Title hard drop | `title_excluded` (`:519-530`) in `score_job` (`:655`) | `Title`; `hard_drop_terms` | yes: from `exclude_levels`, a constant in Sweep | no | before dedupe | yes while `drop_excluded` is True (`config.py:944`) | **MIXED** (profile-carried, constant value) |
| Experience floor | `_required_experience_floor` vs `max_experience_years` (`:656-658`) | JD text; (r) years+3 | yes (r) | no | before dedupe | yes | **PROFILE-DEPENDENT** |
| Experience guard | `experience_guard.assess/record` (`:666-673`) | JD text; (r) `candidate_experience_months` | yes (r) | no | before dedupe | yes on `hard_drop` | **PROFILE-DEPENDENT**, off by default |
| Minimum score | `SETTINGS.min_score` (`:2884-2885`) | score | cfg None; not rendered | no | after scoring | would be; inactive | **PROFILE-INDEPENDENT** (inactive) |
| Recency | `is_recent` (`:533-537`, `:2893-2897`) | `Posted Date`; (p) `max_age_days`; cfg `drop_undated` | yes (p) | no | after scoring | yes | **PROFILE-DEPENDENT (p)** |
| Compensation | `comp_ok` (`:611-613`, `:2903-2906`) | `Salary`; (p) `min_comp_usd` | yes (p) | no | after scoring | yes | **PROFILE-DEPENDENT (p)** |
| Remote reachability | `reachable` (`:353-374`, `:2911-2915`) | `remote_scope`, `hires_home`, `remote_regions`; (p) `remote_scopes`; cfg `keep_restricted_if_hires_home`, `HOME_LOCATION_HINTS` | yes (p) | no | after scoring | yes | **PROFILE-DEPENDENT (p)** |
| Visa | `drop_no_visa` (`:2917-2920`) | `visa` | cfg False | yes | after scoring | inactive | **PROFILE-INDEPENDENT** (inactive) |
| EOR | `require_eor` (`:2922-2925`) | `eor` | cfg False | yes | after scoring | inactive | **PROFILE-INDEPENDENT** (inactive) |
| Work arrangement | `onsite_or_hybrid` (`:269-278`, `:2941-2946`) | `remote_scope`; (p) `work_scope` | yes (p) | no | after scoring | yes | **PROFILE-DEPENDENT (p)** |
| India geography | `in_home_country` (`:249-266`, `:2947-2951`) | `Location`; cfg `HOME_LOCATION_HINTS` | yes (p): `work_scope == "india"` | no | after scoring | yes | **PROFILE-DEPENDENT (p)** |
| Soft seniority | `SOFT_DROP_PATTERNS` (`:678`, `:709-710`) | `Title`; cfg | no | yes | in scoring | no (penalty) | **PROFILE-INDEPENDENT** |
| Dedupe | `dedupe` (`:820-832`) | `job_key` | the survivor depends on the score | the key does, the survivor does not | after all filters | losers discarded | **MIXED** |
| `--only-new` | `emit` (`:3907-3912`) | `seen.tsv` | per output dir | no | after finalize | yes (CLI only) | **MIXED** (history) |
| Merge re-application | `merge_jobs.applyable` (`merge_jobs.py:27-78`) | stored columns; the **current** profile's config | yes | no | at merge (local) | yes | **PROFILE-DEPENDENT** |
| Results filters | `logic.shortlist` (`:816-840`) | query string | no | n/a | at view/export | no (view only) | **PROFILE-INDEPENDENT** |

Sponsorship exists only as the `visa` signal (off). Job age is the recency
rule. Source quality is handled by the config blocklist and the ATS board
registry; the shadow boards are off (`sources/shadow.py:64-129`).

**Could a job that fails Profile A be discarded before it is ever evaluated
against Profile B?** Today a run has exactly one profile, so B does not exist.
Mechanically, given the code:

1. Free rows failing A's title or location gate are dropped inside the
   adapter and never become rows (`sources/ats.py:197-198`).
2. Paid rows are shaped by A's keywords and A's provider filters (`f_E`,
   `f_TPR`, `f_WT`), so postings outside A's query are never fetched.
3. Rows failing A's hard filters or preference filters are absent from every
   `finalize` output. `raw_rows` exists only in process memory for the run
   (`scraper.py:3916`).
   - Output rows omit `Description`: `to_output` has no such key
     (`:2779-2839`), and `merge_jobs.py:30-32` says so. Saved rows therefore
     cannot be re-scored from disk.
   - Paid raw datasets remain at Apify. `rescore_from_apify.py` can re-read
     them (`:59-82`), locally, for every account's runs in a time window, not
     tied to a run.
   - Free raw rows are not persisted anywhere.
4. Dedupe runs after A's filtering and keeps A's highest-scoring copy.

---

## 10. Ranking / Scoring Architecture

**Entry**: `finalize` (`:2973-2997`) → `score_and_filter` (`:2863-2958`) →
`score_job` (`:631-745`) per row; `rank_rows` (`:2961-2970`).

**Fields read from the job**:

- `Title`, `Description`, `Experience` (concatenated and lowercased,
  `:637-641`).
- `Company` (blocklist).
- Enrichment of `Location`, `Title` and `Description` → `remote_scope`,
  `visa`, `eor`, `timezones`, `tz_gap`, `remote_regions`
  (`enrich.enrich`, `enrich.py:295-320`).
- `hires_home` (reachability).
- `Salary` and `Posted Date` (filters).

**Fields read from the profile (as config globals)**:

- `SCORING`: `skill_weights`, `penalty_terms`, `frontend_terms`,
  `backend_terms`, `fullstack_title_terms`, `fullstack_bonus`,
  `hard_drop_terms`.
- Config-only `SCORING`: `soft_drop_terms`, `soft_penalty` (-4),
  `drop_penalty` (-15), `experience_gap_penalty` (-8),
  `timezone_gap_penalty` (-1.5), `company_blocklist`.
- `SETTINGS`: `max_experience_years`, `candidate_experience_months`,
  `drop_excluded`, `experience_aggregate`, `home_utc_offset`.
- The engine version via `skill_concepts.enabled()` (`skill_concepts.py:873-881`).

**Formula as coded**:

```
score  = Σ skill weight          v1: every matching term (`:688-693`)
                                 v2: every concept once, under the overlap policy
                                     (`skill_concepts.score`)
       + fullstack_bonus         if frontend∧backend or a title term (`:696-701`)
       + Σ penalty               negative (`:704-706`)
       + soft_penalty            if a soft seniority term appears (`:709-710`)
       + experience_gap_penalty  if the guard says "penalty" (`:714-715`)
       + drop_penalty            if excluded and drop_excluded is False (`:716-717`)
score  = round(score + timezone_gap_penalty × (tz_gap − TZ_FREE_HOURS))
         when the gap is exceeded (`:737-740`)
```

- Intermediates: `matched`, `is_fullstack`, `years_required` (the parsed
  floor), `exp_verdict`.
- Deterministic: no LLM and no network in scoring. `enrich` is regex-based.
- Caching: `_score_once` memoises the kept/dropped verdict on the row object
  when `memo=True`, which is used only under `SWEEP_PAID_CONCURRENCY` without
  the experience guard (`:3900-3901`, `:2853-2860`).
- Side effects: `experience_guard.record` appends to the module list
  `DROPPED` when the guard is on (see `shadow_engine`'s isolation,
  `:3009-3017`); `LAST_STATS`; telemetry counters.

**Required answers.**

1. **Can the current scorer be called independently for Profile A, B and C?**
   Not within one process as written. `score_job` reads module-level tables
   compiled at import from the one profile `config` overlaid at import
   (`scraper.py:311-344`, `config.py:1175-1188`). The engine version is a
   process-global bind (`:326-327`). No function re-overlays or recompiles.
   **INFERENCE**: the working way to score against another profile today is a
   separate process with a different `JOB_PROFILE`, which is how the worker
   (`default_spawn` `:334`) and `rescore_from_apify` operate.
2. **Does it mutate the job?** Yes. It sets `score`, `matched_skills`,
   `is_fullstack`, `years_required`, `remote?`, `hr_email`, `hr_phone`
   (`:719-744`) and the `enrich` fields; under memo it also sets `_scored`.
   Rows it drops return `None` before any of these assignments
   (`:647-648`, `:675-676`).
3. **Does it mutate the profile?** No. It only reads the globals.
4. **Does it rely on global or session state?** Yes: config module globals,
   `scraper` module tables, `skill_concepts._BOUND`,
   `experience_guard.DROPPED`, `LAST_STATS` and telemetry `_run`. It does not
   read web session state.
5. **Does it attach one scalar score directly onto the job?** Yes,
   `row["score"]` (`:719`, `:740`), carried to `OUTPUT_COLUMNS[0]`.
6. **Does ranking itself drop results?** Sorting does not. `rank_rows` then
   calls `dedupe`, which drops duplicates (`:2970`).
7. **Are eligibility and scoring coupled?** Yes. The hard filters (blocklist,
   title drop, experience floor, experience guard) live inside `score_job`
   and return `None` (`:643-676`). The penalty variants live in the same
   function.
8. **Are there thresholds after scoring that would matter per track?**
   - `SETTINGS.min_score` exists (cfg None, not rendered, `:2884-2885`).
   - The UI "Minimum score" filter (`sweep/logic.py:824`).
   - Dedupe survivor selection by score.
   - `COMMODITY_WEIGHT` (`sweep/app.py:1514`) is a review-screen display
     threshold on skill weights, not on jobs.

---

## 11. Normalization and Job Deduplication

**Raw → normalized.**

- Paid: `scrape_search` iterates the dataset and calls `normalize(item,
  site_key)` (`:1433-1441`). `FIELD_KEYS` maps many source keys to `Title`,
  `Company`, `Location`, `Salary`, `Experience`, `Posted Date`, `Job URL` and
  `Description` (`:80-94`). Naukri has its own mapper (`:144-172`). HTML is
  stripped and truncated to `description_max` (`:197-198`, `:137-141`).
- Free adapters produce the same keys with `Source = "platform:token"`, plus
  `hires_home` (ATS `BLANK`, `sources/ats.py:135-137`) and, for some sources,
  `req_number`, `grade` and `verified_live`.
- Paid rows also get `search_query`, `search_rank` and, when telemetry is on,
  a provider position.
- `score_job` adds the scoring and enrichment fields. `to_output` projects to
  `OUTPUT_COLUMNS` (`:97-106`).

**Normalized internal schema**: `Source`, `Title`, `Company`, `Location`,
`Salary`, `Experience`, `Posted Date`, `Job URL`, `Description`, plus the
optional `hires_home`, `req_number`, `grade`, `verified_live`, `search_query`,
`search_rank`, `_native` (telemetry) and `_scored` (memo).

**Where dedupe occurs.**

- `rank_rows` at every `emit` (`:2961-2970`).
- `merge_jobs.merge_rows` (`merge_jobs.py:92-97`) at merge.
- Identity-only uses of `_seen_key`: `seen.tsv` (`:3040-3072`), the live feed
  (`sweep/app.py:751`) and `applied.tsv` (`:709-710`).

**Key**: `job_key` (`:786-817`), in precedence order:

1. `("req", req_number.lower())`.
2. `("ct", company_key, title_key)`. `_company_key` strips corporate suffixes
   (`:756-768`); `_title_key` sorts the title tokens (`:771-773`). Location is
   deliberately excluded (`:789-795`).
3. `("url", canonical host+path)` (`:776-783`).
4. Otherwise no key, and the row is always kept (`:826-831`).

**Merge behaviour**: none. The first occurrence after a **stable** descending
sort by score wins (`:2969-2970`). Other copies are dropped whole, with no
field merge. Their `source_site`, `search_query`, `apply_url` and
`date_posted` are lost. Ties keep arrival order: paid rows are integrated in
plan order (`paid_phase_c2` integrates in plan order, `:2439-2443`), then free
rows follow (`:4135`).

**Profile influence**: the key, no. Which copies exist, yes (gates and
queries). Which copy survives, yes (score).

**Score before dedupe**: yes. **Eligibility before dedupe**: yes, all of §9's
post-acquisition filters (`score_and_filter` then `rank_rows`).

**One physical job found by two searches**: rows with the same
`req_number`, or the same normalized company and token-sorted title, become
one row, whatever their locations or sources. Postings differing in company
string after suffix stripping, or in title tokens, stay separate. The code
itself documents one family that survives as duplicates: a publisher that puts
the city in the title (`config.py:313-317`, `875-882`).

---

## 12. Persistence / Run / Session / Serialization

**Storage technology**: filesystem files (CSV, JSON, TSV, `.py`), process
memory, and a signed Flask cookie. No database anywhere in the code read.

| Item | Local console | Public beta |
|---|---|---|
| Session state | `app.state` dict in process memory (`sweep/app.py:247`) | `SessionStore` rooms per cookie `sid`, TTL 2 h, max 500 (`sweep/public.py:199-239`) |
| Cookie | Flask default | signed: `sid`, `beta_ok`, `run_id`; HttpOnly, Secure, SameSite=Strict (`sweep/public.py:148-152`, `187`, `465-473`, `567-568`) |
| Résumé | `auto-apply/resume/resume.pdf` + `state["resume_text"]` | `state["resume_text"]` only (temp PDF deleted) |
| Profile | `profiles/<name>.py` (overwritten) | `state["profile_source"]`; worker `profiles/beta_<run>.py` + `<run_dir>/profile.py` |
| Plan | `state["raw_plan"]`, `state["plan"]` | same (session) |
| Run record | `output/<name>/run.json` (write-only); `state["proc"]` Popen | worker `<runs>/<run_id>/status.json` (`RunStore`); `state["proc"]` = `RemoteRun` |
| Progress | `output/<name>/.done_combos` | worker run `output/.done_combos`, served in status `done` (`deploy/sweep_worker.py:597-609`, `722-724`) |
| Final jobs | `output/<name>/jobs_<stamp>.csv/.json` (cumulative rewrite) | worker run `output/jobs_<stamp>.csv/.json` |
| Raw / normalized jobs | memory only (`raw_rows`) | memory only |
| Applied state | `output/<name>/applied.tsv` | not available (`OPERATOR_ONLY`, `sweep/public.py:106-107`) |
| History | `seen.tsv`, older `jobs_*.json`, `jobs_combined.*` | none across runs |
| Paid ledgers | `paid_account_ledger.json`, `paid_authorization.json` when pooled | same, in the run dir |
| Errors | stderr / terminal | `status.error` text composed by the worker (`:412-423`, `:496-499`, `:534-535`) |
| Telemetry (flag) | `<output_dir>/telemetry/*.json` (`telemetry.py:970-974`) | same, if set on the worker (UNKNOWN, §18) |

**Required answers.**

- **Does a result contain one scalar score?** Yes (`OUTPUT_COLUMNS` `score`).
- **Is profile data copied into each run?** Locally, no. The run reads
  `profiles/<name>.py` at start, and later edits overwrite that file. The
  output rows carry only `matched_skills`. `run.json` has only the name. On
  the worker, yes: the rendered source is copied to `<run_dir>/profile.py`
  and `profiles/beta_<run>.py` (`deploy/sweep_worker.py:844-849`) until TTL
  cleanup (`:568-590`).
- **Can old runs be reopened?**
  - Locally, `/results` shows the newest file for `state["profile"]`, and
    `list_sweeps` offers older `jobs_*.json` for merge
    (`sweep/app.py:619-643`). There is no run picker, and a restart loses
    `state["profile"]`.
  - Publicly, one run per cookie for 48 h (`TTL_SECONDS`,
    `deploy/sweep_worker.py:69`); `remember_run` overwrites the previous id.
- **Is there schema/version validation?**
  - Profile module: yes (`PROFILE_SCHEMA`).
  - Telemetry: `SCHEMA = "search-v2a.1"` (`telemetry.py:58`).
  - Account ledger: `"schema": "search-v2c45.1"` (`scraper.py:2048`).
  - Title corpus: `FROZEN_SCHEMA = 1` (`local_search.py:230`).
  - No version on: `status.json`, `run.json`, job CSV/JSON (read with
    `csv.DictReader`, no column check), dry-run plan JSON, worker API
    bodies, session state.
- **After a server restart**:
  - Local: state is lost. A running child keeps running but is no longer
    tracked; the code documents that a new `/run` could start a second one
    (`sweep/app.py:2758-2762`).
  - Public (Render): sessions and résumé text are lost. `rehydrate` restores
    the run with `free_only = True` whatever the run was
    (`sweep/worker_link.py:305-312`).
- **After a worker restart**: `Queue.recover` (`deploy/sweep_worker.py:395-424`)
  marks running runs interrupted, re-queues queued free runs, and marks
  queued paid runs interrupted, since tokens are never persisted. Held keys
  are lost.
- **Where results live**: filesystem (local disk, or worker disk served over
  HTTP) plus process memory. Nothing client-side except the "opened listings"
  set in `localStorage` (`results.html:450-470`).

---

## 13. Results UI

- **Row creation**: server-side Jinja. `results.html::listings_table`
  (`:9-101`) renders one `<tr>` per row dict. The columns are applied tick
  (local only), Source, Score/Match, Role + company, Location, Pay,
  Experience, up to six matched-skill tags, and Apply + posted age. Rows with
  an `http(s)` apply URL are clickable (`:27-35`, `438-496`).
- **Score display**: the raw integer, headed "Score" locally and "Match"
  publicly (`:15`, `:70`).
- **Tabs / sections**: three buckets, `india`, `remote` and `abroad`
  (`sweep/logic.py:1219-1230`); `abroad` is collapsed (`results.html:408-435`).
- **Filters / sorting**: GET form with `min`, `source` (platform prefix), `q`
  (title/company substring) and `sort` (`score`, `recent`, `experience`)
  (`results.html:224-262`; `sweep/app.py:2634-2648`).
- **Pagination**: 25 per section (`SECTION_CAP`, `sweep/logic.py:531`), with
  a "Show all" link (`full=1`) (`results.html:124-142`).
- **Counts**: `total` / `all_total` in the heading (`:151-156`) and a count
  per section (`:116-119`).
- **Metadata available to the template**: `profile` name, `derived` (local
  re-rank editor, `:312-370`), `sources`, `merged`, `earlier`/`earlier_dates`,
  `spend`, and `public_mode`/`free_only` (`sweep/app.py:2678-2713`).
- **Does the frontend receive the whole job object?** No. It receives HTML
  built from the row dict, which has the output columns plus `_key` and
  `applied`. JSON reaches the browser only through the running screen's
  `/progress` or SSE payload (`sweep/app.py:2585-2619`), whose `latest` items
  are `{key, title, company, site, score}` (`:755-759`).
- **State restoration**: every GET re-reads the file or worker rows. Alpine
  keeps only per-row tick state; `localStorage` keeps opened hrefs.

**Smallest current points where track metadata could later be shown** (no
redesign implied):

- The per-row cells in `listings_table` (`results.html:36-96`), for example
  beside the Score cell (`:70`) or the matched-skills cell (`:82-85`).
- The section loop (`results.html:408-435`) and the filter form (`:237-262`).
- The export column table `sweep/exports.py::COLUMNS` (`:28-47`) and the
  about pairs (`sweep/app.py:2915-2922`).
- The live-feed item dict (`sweep/app.py:755-759`) and `running.html`.
- The review cards (`review.html:40-92`) and `_search_summary.html:13-21`.

---

## 14. Export

Supported: **CSV, JSON, XLSX and HTML**, through `GET /export.<fmt>`. The
`EXPORTS` map (`sweep/app.py:2879-2888`) serves an unknown extension as 404
(`:2900-2901`). No other format exists in this code.

| | CSV | JSON | XLSX | HTML |
|---|---|---|---|---|
| Builder | `exports.as_csv` (`:122-132`) | `exports.as_json` (`:135-146`) | `exports.as_xlsx` (`:268-329`) | `exports.as_html` (`:232-265`) |
| Columns | `COLUMNS` (16): Applied, Reachable, Score, Role, Company, Location, Remote, Pay, Experience, Matched skills, Source, Posted, Visa, Recruiter email, Recruiter phone, Apply URL (`:28-47`) | objects keyed by those headers | "Listings" sheet with the same columns; "About this export" sheet | cards grouped by `_bucket`, plus an about list |
| Score | `as_number` int (`:118`) | int | int cell | text |
| Formula defuse | yes (`defuse` `:68-77`) | no, by design (`:138-141`) | yes | HTML-escaped; only `http(s)` hrefs |
| About metadata | none | none | Profile, Exported, Listings, Minimum score, Source, Search text, Sorted by (`sweep/app.py:2915-2922`) | same |

- **Row set and order**: `shortlist(rows_with_applied(profile), min, source,
  q, sort)`, then `rows_for_export(bucket_rows(rows), SECTIONS)`
  (`sweep/app.py:2906-2910`; `exports.py:93-110`). Bucket order is india,
  remote, abroad, with the chosen sort inside each bucket.
- **Filename**: `sweep-<profile stripped to [A-Za-z0-9_-]>-<YYYY-MM-DD>.<fmt>`
  (`sweep/app.py:2939-2940`).
- **Fixed column assumptions**: the XLSX filter range and the hyperlink
  column come from `COLUMNS` (`exports.py:307-316`). The exports self-check
  asserts fixed column letters A-P (`:385-391`). Tests assert CSV headers and
  cells (§16).
- **Compatibility**: the JSON keys are human headers; there is no schema
  version.
- The engine's own CSV (`OUTPUT_COLUMNS`, 26 columns) is a separate artifact.
  Public visitors only get the four export formats.

---

## 15. Security / Privacy Constraints

| Area | Evidence | Relevance for later design |
|---|---|---|
| Résumé text at rest | Local: fixed PDF path, overwritten (`sweep/app.py:1473-1474`). Public: temp file deleted in the same request (`:1489-1492`); text kept in session memory (`:1498`) for up to 2 h (`sweep/public.py:112`) | every extra résumé is another full text held in session memory |
| Résumé text to models | the full text is interpolated into the prompts (`local_extract.py:204`, `279`; `make_profile.py:181-186`) | each derivation sends the whole résumé |
| Inference logs | category only, never the prompt or answer (`inference.py:484-488`; `inference_service.py:370-380`, `418-424`) | — |
| Derivation logs to stdout | `make_profile.generate` defaults to `log=print` (`:569`), and the web `derive` passes no `log` (`sweep/app.py:301-302`). What gets printed: top-12 skills with tier and reason (`make_profile.py:350-360`), split compounds (`:525-527`), re-weighted terms (`:424-430`), guard-rejected queries (`local_profile.py:241-244`, `264-267`, `283-286`, `302-303`, `311-313`) and the keyword ranking (`:333`) | derived profile values reach the web process's stdout. Where Render keeps it is UNKNOWN (§18) |
| Escalation log line | `app.logger.warning("derive escalated: %s", ...)` (`sweep/app.py:1663`). Reasons can quote up to three unsupported model values and an unfound candidate name (`local_extract.py:575-586`) | model-extracted strings can reach server logs |
| Worker logs | only `"a caller's key is held for one run"` and `"run=%s created free_only=%s"` (`deploy/sweep_worker.py:764`, `865`). Free runs keep `engine.log` (stdout+stderr) in the run dir (`:340-347`); paid runs send engine output to DEVNULL (`:342-344`) | a free `engine.log` contains the printed plan (keywords × locations) and the top 10 jobs |
| Rendered profile on the worker disk | `profiles/beta_<run>.py` + `<run_dir>/profile.py` for 48 h (`:844-849`, `568-590`). Contents: skills, role keywords, `field_summary`, and `notes`, which may name values the router dropped (`local_profile.py:127-130`). `candidate_name` is not rendered | copies multiply per rendered profile |
| Worker payload contents | the full `derived` dict including provenance records, plus `prefs`; no credential (`sweep/worker_client.py:104-110`); the payload cap is 512 KiB (`deploy/sweep_worker.py:90`) | payload size grows with each profile |
| Provider keys, public | never written to `.env` or `os.environ` or session; only an id, an account digest and figures are kept (`sweep/app.py:1962-2006`); never echoed (`:1978-1981`, `2020-2026`); held only in worker memory (`:381-389`); passed on stdin, with `APIFY_TOKEN*` and developer vars scrubbed from the child env (`:326-333`); the engine reads stdin once and its refusal text is fixed (`scraper.py:1713-1730`) | — |
| Provider keys in errors | `worker_client._call` composes its own messages (`:82-92`); `discover_accounts` records the exception **type** only (`scraper.py:1840-1841`); the ledger and telemetry exclude tokens (`:2044`; `telemetry.py:24-28`) | — |
| Token in a URL (developer path) | `_token_headroom` puts the token in the query string of an Apify limits call; exceptions are swallowed (`scraper.py:3208-3220`); used by the local single-account `_require_token` (`:3297`) | not on the public path |
| Operator `.env` isolation | public mode never reads `.env` (`sweep/app.py:470-471`, `523-524`, `1299-1301`) | — |
| Worker auth | bearer on every endpoint (`deploy/sweep_worker.py:699-704`, `731-735`); owner HMAC per run with 404 for strangers (`:706-714`); owner = HMAC(`SECRET_KEY`, sid) (`sweep/public.py:155-177`) | any per-track resource must stay behind the same owner check |
| Public surface | endpoint allowlist, same-origin check on writes, beta code (`sweep/public.py:537-557`); operator-only routes (`:106-107`) | new routes are invisible until added to `PUBLIC_ENDPOINTS` |
| Cost gates | per-derivation daily budget (`sweep/public.py:270-364`); paid pool authorization (§8) | more derivations draw on the same GPU budget |
| Exports | contain scraped recruiter emails and phones (`exports.py:44-45`); the filename carries the profile name, which in public mode derives from `candidate_name` (`sweep/app.py:1749-1763`, `2939-2940`) | — |
| Telemetry (off by default) | records query keyword/location strings and the profile name, clipped to 200 characters (`telemetry.py:22-28`, `137`, `245-265`) | role keywords are résumé-derived |
| Tracked personal data | `auto-apply/apply_config.py:67-73` holds a hard-coded personal contact block (values not reproduced here); `profiles/` contains named hand-written profiles | not used by the Sweep routes read in this audit |

---

## 16. Existing Test Coverage

### 16.1 Runs performed in this audit (offline; no provider)

| Command | Result |
|---|---|
| `.venv/bin/python -m unittest discover -s sweep/tests -t .` | **Ran 1662, OK** (291 s) |
| `.venv/bin/python -m unittest deploy.test_sweep_worker deploy.test_modal_benchmark` | **Ran 42, OK** |
| `cd auto-apply && ../.venv/bin/python -m unittest discover -s tests -t .` | **Ran 1102, FAILED (errors=2)**: `test_inference.TestAuthenticationFailure.test_healthz_needs_no_token` and `test_inference.TestConcurrentRequests.test_healthz_answers_while_a_generation_holds_the_slot`, both `HTTPError 503` from the in-test inference `/healthz`. The same two tests are recorded as environmental on the pre-D baseline in `docs/search-engine-v2-d-finalization.md` §7.3. Not investigated further (audit-only) |
| `.venv/bin/python -m unittest bench.test_backends bench.test_answer_key` | **Ran 43, OK** |
| `scraper.py --demo`; `config.py`; `python -m sweep.exports`; `python -m sources` | all print their OK lines |
| `git status --porcelain` before vs after | identical (no change) |

Test output was written to scratch files and only the summary lines were
read, so no environment values were printed.

### 16.2 Inventory by concern

| Concern | Test files (count of `def test_`) | Notes |
|---|---|---|
| Upload | `sweep/tests/test_app.py` (537), `test_public.py` (53: `TestNoDisk`) | PDF check, temp-file deletion |
| Extraction | `auto-apply/tests/test_inference.py` (102), `test_make_profile.py` (159), `test_employment_grounding.py` (53), `test_skill_evidence.py` (52), `test_skill_concepts.py` (78), `test_evidence_*`, `test_clause_scope.py`, `test_corpus_fallback.py`, `test_canonical_*`, `test_presentation_stability.py`; `bench/test_backends.py`, `bench/test_answer_key.py` | fixtures: `auto-apply/tests/fixtures/` (e.g. `budget_reach_corpus.json.gz`), `bench/resumes/` |
| Profile review / edit | `test_app.py`, `test_parse_activity.py` (26), `test_public_copy.py` (31) | |
| Profile safety / versioning | `test_generated_profile_safety.py` (34), `test_engine_contract.py` (38) | schema stamp, loader, v1/v2 binding |
| Search config / prefs | `test_search_prefs.py` (67) | runs the engine's filters under preferences |
| Planner / plan cost | `test_plan.py` (18), `test_paid_plan_audit.py` (3), `test_runs.py` (35) | |
| Paid execution / contract | `test_paid_contract.py` (32), `test_indeed_bounded.py` (58), `test_paid_concurrency.py` (81), `test_paid_observability.py` (51), `test_paid_dev_guard.py` (45), `test_paid_adaptive.py` (69) | fake Apify clients |
| Allocator / partial | `test_paid_multi_account.py` (86: `place_units`), `test_search_v2_d.py` (68: `FullOrPartial`, `coverage`, prefix in plan order) | `placeable_prefix` is exercised through `authorize`/`coverage`, not called directly by name |
| Worker | `deploy/test_sweep_worker.py` (34), `sweep/tests/test_worker_link.py` (5), `test_public_sweep.py` (23), `test_public_paid.py` (29), `test_active_run.py` (29) | |
| Free sources | `test_free_concurrency.py` (47), `test_free_concurrency_greenhouse.py` (37), `test_search_v2_shadow_eval.py` (55) | |
| Eligibility | `test_search_prefs.py`; `auto-apply/tests/test_experience_guard.py` (26), `test_title_gate.py` (35), `test_hard_drop.py` (38) | |
| Ranking / scoring | `test_skill_concepts.py`, `test_engine_contract.py`, `test_paid_concurrency.py` (memo parity) | |
| Normalization / dedupe | `scraper.py::demo` (job identity); `test_free_concurrency.py`; `test_paid_concurrency.py` ("the first planned twin wins", `:507-550`); `test_selection.py` | |
| Results / early readiness | `test_results_ready_early.py` (58), `test_app.py` | |
| Export | `test_app.py`, `test_public_sweep.py` (`export.html`), `test_results_ready_early.py`; `sweep/exports.py::demo` | |
| Session / serialization | `test_public.py` (`TestSessionIsolation`), `test_worker_link.py` (`TestOwnershipSurvivesARenderRestart`) | |
| Security / privacy | `test_public.py`, `test_public_paid.py` (`TestWhereTheKeyIsAllowedToExist`), `deploy/test_sweep_worker.py` (`TestBearerAuth`, `TestOwnership`, `TestTheApifyToken`, `TestNoArbitraryPython`), `test_feedback.py` (40) | |
| Telemetry | `test_search_v2_telemetry.py` (26) | |

`sweep/tests/test_app.py` installs an audit hook that refuses writes to the
real `.env`, `output/` and `profiles/` (`:30-70`).

### 16.3 Gaps relevant to Multi-Track (facts about absence)

- No test runs two derivations in one session, two profiles in one engine
  process, or two profiles in one worker run. No such code path exists.
- No test covers plan-unit equivalence across profiles, or two units sharing
  a `combo_key` while differing in `f_E`, depth or `max_age_days`.
- No test exercises re-scoring stored output rows, which carry no
  `Description`.
- No test covers export schemas with more than one score.
- No test asserts the post-restart `rehydrate` behaviour for a **paid** run
  (`free_only = True`). `TestOwnershipSurvivesARenderRestart` covers
  ownership (`test_worker_link.py`); the paid case was not searched further.

---

## 17. Multi-Track Touch-Point Inventory

This is an inventory, not permission to change anything. The classification
follows from the dependencies documented above.

| File / module | Classification | Why |
|---|---|---|
| `sweep/app.py` | **definitely affected** | single `resume_*`/`derived`/`profile`/`plan` state; `derive`, `review_post`, `costed`, `run`, `_results_page`, `export`, `_prefs` (§4 #2-10, 19-24, 40, 44-46) |
| `sweep/templates/upload.html`, `review.html`, `_weights.html`, `results.html`, `_search_summary.html` | **definitely affected** | one file input; one profile reviewed; one score column (§4 #1, 8, 42, 45) |
| `auto-apply/make_profile.py` | **definitely affected** | `render` emits one `SCORING`/`SEARCH` and folds preferences, cap and partial authorization into the same file (§3.3-3.4) |
| `config.py` | **definitely affected** | one profile per process, selected at import, overlaid onto globals (§3.5) |
| `scraper.py` | **definitely affected** | module-level scoring tables, in-place row scoring, one score in `OUTPUT_COLUMNS`, dedupe by score, free gates at acquisition, plan/`combo_key`/allocator semantics (§5-11) |
| `sweep/worker_link.py`, `sweep/worker_client.py`, `deploy/sweep_worker.py` | **definitely affected** | payload `profile` is one dict; strict field allowlist; one rendered profile and output dir per run; `rehydrate` (§3.8, §4 #25-31) |
| `sweep/public.py` | **definitely affected** | per-derivation budget; one `run_id` per cookie; session memory bounds (§4 #7, 30) |
| `sweep/exports.py`, `sweep/logic.py` | **definitely affected** | fixed `COLUMNS` with one Score; `shortlist`, `reweighted`, applied ledger per profile (§13-14) |
| `sweep/runs.py`, `sweep/plan.py` | **probably affected** | `combo_key` identity, progress, `units`/`prefix`/`coverage` follow one plan's order (§6, §8) |
| `auto-apply/local_profile.py`, `local_extract.py`, `local_search.py` | **probably affected** | derivation per résumé; `fields_for` per person; keyword order drives plan order (§2.2 #3, §6.2) |
| `skill_concepts.py` | **probably affected** | process-global `_BOUND` engine version (§10) |
| `sources/__init__.py`, `sources/ats.py`, `sources/feeds.py`, `sources/concurrency.py`, `sources/optum.py`, `sources/enterprise.py` | **probably affected** | title/location predicates are applied inside the adapters (§9) |
| `telemetry.py`, `paid_adaptive.py`, `sources/shadow.py` | **possibly affected** | record one profile / one funnel; flag-gated |
| `merge_jobs.py`, `rescore_from_apify.py` | **possibly affected** | per-`JOB_PROFILE` output dirs; re-apply one profile's rules |
| `sweep/templates/running.html`, `configure.html`, `confirm.html`, `key.html`, `deriving.html`, `base.html` | **possibly affected** | one plan/progress/parse strip |
| `sweep/feedback.py` | **possibly affected** | reads scalar session context |
| `render.yaml` | **possibly affected** | `SWEEP_BETA_DAILY_*` quota values; engine pins |
| Guard modules (`experience_guard`, `title_gate`, `role_evidence`, `canonical_guard`, `hard_drop`, `orphan_guard`, `unknown_family_guard`, `semantic_scope`, `attachment_guard`, `family_centrality`) | **possibly affected** | per-person inputs; all off by default |
| `enrich.py`, `auto-apply/linkedin_shortlist.py` (`bucket`) | **likely unaffected** | profile-independent (§5) |
| `corpus_signal.py`, `skill_evidence.py`, `skill_scan.py` | **likely unaffected** in structure | per-call functions of (terms, text, corpus); called once per derivation |
| `inference.py`, `inference_service.py`, `deploy/modal_*.py`, `gunicorn.conf.py` | **likely unaffected** | per-call model transport; no profile state |
| `bench/*`, `poc/*`, `verify_geoids.py`, `harvest_ats.py`, `auto-apply/{apply,emailer,tailor,build_userscript,...}.py` | **likely unaffected** | not on the Sweep request path |

---

## 18. Facts That Were NOT Established

| # | Unknown | Why it could not be established | What would establish it |
|---|---|---|---|
| U1 | The actual environment of the Oracle worker (`SWEEP_PAID_CONCURRENCY`, `SWEEP_PAID_MULTI_ACCOUNT`, `SWEEP_PUBLIC_PAID`, `SWEEP_RESULTS_READY_EARLY`, `SWEEP_SEARCH_V2_TELEMETRY`, free-concurrency flags, V3 guard flags) | it lives in `/etc/sweep-worker/env` on the host (`docs/oracle-sweep-worker.md:77-83`, `:113`), not in the repo; the V2-D doc's recommended values (`docs/search-engine-v2-d-finalization.md:640-646`) are a recommendation, not proof | read the host file with secrets redacted |
| U2 | Whether Render's live env equals `render.yaml` | several keys are `sync: false` and dashboard-set (`render.yaml:64-98`) | Render dashboard / API |
| U3 | The deployed commit on Render, the worker checkout and Modal | not observable from the repo | deployment inspection |
| U4 | Where Render and the worker retain stdout/stderr (derivation `print` logs, `app.logger`) and for how long | platform behaviour, not code | Render log settings; `journalctl` retention on Oracle |
| U5 | Whether telemetry is enabled in production, and so whether query strings are recorded there | depends on U1 | U1 |
| U6 | The serialized size of real `derived` dicts relative to the 512 KiB worker body cap | depends on real résumés | measure `len(json.dumps(derived))` on representative résumés |
| U7 | How much provider results change with `f_E` / `f_TPR` / `f_WT` for otherwise identical queries | provider behaviour | controlled, budgeted provider experiment (not run) |
| U8 | Whether two identical queries run the same day return identical result sets | provider behaviour | same |
| U9 | How many free-source rows one profile's title gate rejects that another profile's gate would keep | data question | offline replay of stored board payloads through two gates (none stored today; free raw rows are not persisted) |
| U10 | Per-derivation latency and GPU cost in production | measured figures exist in docs, not re-verified here | inference service metrics |
| U11 | Memory headroom of the single Render worker for more session data | `SessionStore` bounds count (500) but not bytes | memory profiling on Render |
| U12 | Whether any hand-written `profiles/*.py` overrides `ATS_BOARDS`, `home_utc_offset`, `soft_drop_terms`, `min_score`, etc. | not read file by file | read each `profiles/*.py` (local only; public profiles are generated) |
| U13 | Whether `rehydrate`'s `free_only = True` is visible to a paid visitor after a Render restart (e.g. meter/spend display) | only the assignment was read (`sweep/worker_link.py:307`), not every screen's behaviour in that state | a test or manual run of that path |
| U14 | The exact `local_search.budget_order` and `select_detail` logic behind keyword order | read at signature and caller level only (`:1329`, `:1171`, `:1430-1436`) | a full read of `local_search.py:1033-1386` |
| U15 | The internal structure of `skill_evidence` tiering and `skill_scan` gating | out of scope for search/plan/eligibility; only their outputs were traced | a full read of both modules |
| U16 | The cause of the two auto-apply test errors in this environment | audit-only; only the exception type was read | run the two tests in isolation with a local inference stub |
| U17 | Whether `graphify-out` reflects HEAD | dated 2026-09-18; not used | regenerate |

---

## 19. High-Risk Areas for Future Design

These are factual constraints and risks the current code reveals. None is a
design proposal.

1. **The engine is one-profile-per-process by construction.** The profile is
   chosen from argv/env at import (`config.py:1175`) and overlaid into
   module globals (`:1105-1136`). The scoring tables are compiled at import
   (`scraper.py:311-344`), and the engine version is bound process-wide
   (`:326-327`). No API re-targets a running process.
2. **Free-source acquisition is profile-filtered and destructive.** The title
   and location gates run inside the adapters, and rejected rows never exist
   (`sources/ats.py:197-198`; `sources/feeds.py:94`). Free raw rows are not
   persisted.
3. **Paid queries embed provider filters that are not part of the engine's
   execution identity.** `f_E` (from `experience_years`), `f_TPR`
   (`max_age_days`), `f_WT` (`remote_only`) and depth shape what LinkedIn and
   Naukri return (`scraper.py:909-915`, `986-1008`), but `combo_key` covers
   only site, keywords, location and company (`sweep/runs.py:31-32`). The done
   ledger treats units equal by `combo_key` as the same search and skips the
   later one (`scraper.py:4032-4035`, `2642-2645`).
4. **Partial paid execution is positional.** The runnable set is a plan-order
   prefix, and its length depends on the ceiling multiset of that prefix
   (§8.6). Any change to unit order changes what runs under "run with my
   available credit".
5. **What Confirm shows is not the object that executes.** The worker
   re-plans from `derived` + `prefs` (`deploy/sweep_worker.py:838-857`). No
   plan hash or version is compared, and Render's coverage is advisory; the
   engine re-authorizes on fresh account readings (`scraper.py:2153-2254`).
6. **Scoring mutates rows in place, and under C2 memoises the verdict on the
   row object** (`scraper.py:719-744`, `2853-2860`). One row dict holds one
   score, and a memoised row is never re-scored.
7. **Eligibility and scoring are coupled.** Hard filters live inside
   `score_job` and return `None` (`:643-676`). Several preference filters
   follow in `score_and_filter` before dedupe.
8. **Dedupe discards the losing copies' provenance, and the survivor is
   chosen by score** (`:2969-2970`, `820-832`). Which source, `apply_url` and
   `search_query` a user sees depends on the profile that scored it.
9. **Stored results cannot be re-scored.** Output rows omit `Description`
   (`:2779-2839`). Only paid raw data survives, at the provider.
10. **The rendered profile is one artifact holding résumé fields, search
    preferences, the generated spend cap and the partial-sweep
    authorization** (`make_profile.py:1123-1150`, `sweep/app.py:2410-2425`).
    Each rendering re-derives all of them together.
11. **The worker contract is strict and singular.** Unknown fields are
    refused (`deploy/sweep_worker.py:177-180`). Each run gets one rendered
    profile, one output dir and one child; `MAX_ACTIVE = 1`; and each cookie
    holds one `run_id` (`sweep/public.py:187`).
12. **The public cost gate counts derivations.** The limit is 3 per IP and 60
    in total per day (`sweep/public.py:119-120`, `render.yaml:74-77`), and
    each derivation is two GPU calls (`sweep/public.py:274-279`).
13. **Session state is unversioned process memory in one gunicorn worker,
    bounded by count only** (`sweep/public.py:112-115`, `render.yaml:13-16`).
14. **Profile-derived values reach process stdout and warning logs during
    derivation** (§15 rows "Derivation logs" and "Escalation log line").
15. **Output directories and ledgers (`.done_combos`, `seen.tsv`,
    `applied.tsv`) are keyed by profile name**, and the profile name is also
    the module name and export filename (`config.py:1186-1188`;
    `sweep/logic.py:1015-1016`; `sweep/app.py:2939-2940`).
16. **Engines differ in structure, not only in weights.** Local-engine
    profiles render empty domain halves (`local_profile.py:349-352`), so the
    full-stack bonus is disabled for them. Generated profiles replace
    config's `hard_drop_terms` with the short `exclude_levels` list (§3.2).
