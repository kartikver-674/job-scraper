# Search Preferences Correctness V1

What the user selects is what Sweep does. Implementation of the ratified
decisions on `docs/search-preferences-forensic-audit.md` (`571f19c`).

**Not deployed.** Nothing is switched on by a flag either — these are
behaviour changes in the ordinary code path, and the section on deployment
below says what that means for a rollback.

| | |
|---|---|
| Baseline | `571f19c` (the audit) |
| Scope | Parts A–L of the Search Preferences Correctness V1 brief |
| Out of scope | Search Engine V2, free-path depth plumbing, Profile Engine V3, ranking, query generation, title gating, industry taxonomy, visa filtering |
| Production diff | 13 files, +1,061 −204, plus one new test file |
| Tests | **1,969 pass** — sweep 858, auto-apply 1,026, bench 43, deploy 42 (was 1,925) |

---

## 1. Exact bugs fixed

Numbered as in the audit's bug list.

| # | Bug | Fixed by |
|---|---|---|
| **B1** | Minimum salary silently erased on any return to Configure | the box renders its stored value |
| **B2** | "How recent" silently reset to 14 days | `selected` comes from state, not a hardcoded attribute |
| **B3** | Locations did nothing in Free Sweep, and the summary said they did | `config.LOCATION_MATCH` → `LOCATION_HINTS` in the rendered profile → `scraper.location_allowed` |
| **B5** | The depth box never showed the stored value | it renders `value`, keeps the placeholder only as the stepper's fallback |
| **B6** | "In India" and "Onsite worldwide" were the same sweep in Free mode | `SETTINGS["work_scope"]` and a post-fetch arrangement filter |
| **B7** | A picked city stripped LinkedIn's `f_WT=2` and bought onsite rows | the picker is dropped under remote, and the scope now *states* `remote_only` |
| **B8** | Indeed always searched the Indian site | `config.INDEED_COUNTRIES`, derived per search, fail-closed |
| **B9** | Avoid terms could be added but never removed | they live in `prefs["avoid"]`, and the posted list is authoritative |
| **B10** | Preferences persisted only as a side effect of the cost fetch | a real `POST /configure`; `/estimate` prices and commits nothing |
| **B11** | An untouched screen ran a different search from the one it showed | the default scope is materialised at `POST /review` |
| **B12** | Preferences leaked from one résumé to the next | `PREFERENCE_KEYS`, cleared together by `POST /resume` |
| **B13** | "using defaults" was static text | computed from state |
| **B14** | "industries" was not implemented | the heading says what the code does |
| **B16** | `POST /estimate` accepted duplicate locations | `_parse_chips` de-duplicates, order preserved |
| **B19** | No cap on avoid terms | `MAX_AVOID_TERMS = 20`, rejected not truncated |

Also fixed in passing, because the same change reached them: the Advanced
disclosure now opens when it holds something non-default; the summary shows
the work scope *and* the narrowing rather than one or the other; and
`_search_summary` no longer prints a depth in a mode that has none.

**Carried forward, deliberately** — see §9.

---

## 2. Product semantics chosen

The three answers are three different sweeps. Each owns six state keys, and
`_configure_overrides` replaces all six together so none can be left behind.

| answer | arrangement filter | place filter (free) | paid retrieval |
|---|---|---|---|
| **Remote roles** | `remote_scopes = ["worldwide", "remote"]` + the hires-home rescue, unchanged | none — "from anywhere" | LinkedIn `f_WT=2` in the home geography; Indeed home market |
| **Onsite or hybrid, in India** | onsite/hybrid only | every spelling of an Indian city `config` knows | six city geoIds; Indeed `IN` |
| **Onsite or hybrid, anywhere in the world** | onsite/hybrid only | none until the picker adds one | nine verified geoIds; Indeed per country |

**"Onsite or hybrid" is the complement of `enrich.REMOTE_SCOPES`, not the
pair `("onsite", "hybrid")`.** 39% of real free rows and 50% of paid rows
state no arrangement at all, and this engine's standing rule is that a blank
signal means "the posting didn't say", never "no". So india/global keep
onsite, hybrid **and unstated**, and drop only what the posting positively
calls remote — including geo-restricted remote, which is a remote job
wherever it is locked to.

**Locations narrow the answer above; they never replace it.** Empty means
everywhere that answer covers.

**Remote + a city: option A, with option B as the guard.** The picker is
hidden while "Remote roles" is selected (a place cannot narrow "anywhere"),
`_configure_overrides` drops any location posted under that scope, and
`_SCOPE["remote"]` states `linkedin_remote_only = True` rather than relying
on the literal `"Remote"` token surviving in the location list. Three layers,
because this one spent money.

**Missing location on a posting is KEPT.** Defined rather than incidental:
`location_allowed` and `in_home_country` both answer True for a blank, which
is the same rule as everything else here. Measured: 0 of 7,132 real rows in
`output/` have one.

**Visa sponsorship stays informational.** No filter was invented. The
worldwide answer says most of those roles need sponsorship and says that
Sweep does not filter on it.

---

## 3. Routes, templates and modules changed

```
config.py                 +122   LOCATION_MATCH, INDEED_COUNTRIES,
                                 SETTINGS["work_scope"]
scraper.py                +101   in_home_country, onsite_or_hybrid,
                                 the work-scope block in finalize(),
                                 _indeed_country + fail-closed build_input
auto-apply/make_profile.py +43   emits LOCATION_HINTS and work_scope;
                                 prefs["avoid"] becomes real penalties
sweep/logic.py            +180   _SCOPE gains work_scope/location_hints,
                                 remote_only True; _parse_chips de-dupes;
                                 MAX_AVOID_TERMS; the locations rule
sweep/app.py              +334   POST /configure, commit_prefs, sync_profile,
                                 ensure_scope, PREFERENCE_KEYS, _prefs,
                                 search_facts, /estimate, /run, /resume
sweep/public.py             +8   configure_post in PUBLIC_ENDPOINTS
templates/configure.html  +240   one real form; every control renders state
templates/_search_summary   +20  scope AND locations; optional depth row
templates/confirm.html       +3  passes the depth through
```

### The one structural change

The profile file is now a **derived artifact of session state**, re-rendered
from state at every point that reads it:

```
GET  /configure   sync_profile() -> costed()
GET  /confirm     sync_profile() -> costed()
POST /configure   validate -> render -> write -> COMMIT state -> 303 /confirm
POST /run         validate -> render -> write -> COMMIT state -> re-cost -> launch
POST /estimate    validate -> render -> write -> price -> state UNCHANGED
```

That is what lets `/estimate` price a candidate the user has not agreed to:
the next screen puts the committed one back. It is also what makes the
untouched screen honest, because `sync_profile` runs before the first price.

### Form submission

`configure.html` is now **one** `<form method="post">` wrapping both columns.
Before, it had no `method` and no `action`, "Review and start" was an `<a>`,
and the free path's Start button was a second form carrying no preference
fields — so the only thing that ever saved a setting was the live-cost
`fetch()` firing on a change event.

| mode | action | submit button |
|---|---|---|
| public + free | `POST /run` | "Start Free Sweep" |
| public + paid | `POST /configure` → 303 `/confirm` | "Save and review" |
| local console | `POST /configure` → 303 `/confirm` | "Review the sweep" / "Review spend plan" |

The free public path submits to `/run` because that path has no confirm
screen and its Start button *is* the form's submit. `POST /run` applies the
form through the same `commit_prefs` before it prices or launches anything; a
POST arriving from `/confirm` carries no recognised field and it is a no-op
there.

**JavaScript is not required for correctness.** With Alpine unavailable the
form still submits, every control still posts, and the server still
validates. What is lost is the live cost figure and the hiding of the
locations panel under Remote — and the server drops those locations anyway.

---

## 4. Free vs paid after the fix

| setting | FREE | PAID |
|---|---|---|
| paid sources | not rendered; forced off by the worker | full |
| **which jobs** | **full** — all three answers differ | **full** — plan *and* post-fetch |
| **locations** | **full** — post-fetch, text match on the posting's own location | full — geoId + Indeed market, and the same post-fetch filter for india |
| recency | full (post-fetch) | full (source-side on LinkedIn/Naukri, post-fetch everywhere) |
| **jobs per search** | **not rendered** | full |
| minimum salary | full | full |
| avoid terms | full | full |

Three settings changed column. `locations` and `which jobs` went from dead or
degenerate to working in free mode; `jobs per search` went from silently
inert to absent.

### Why depth is not in free mode

`sources.fetch_free()` has no depth argument — the signature has nowhere to
put one — and every free adapter pulls a fixed amount: whole boards for
Greenhouse/Lever/Ashby/Breezy, `limit=100` for SmartRecruiters, 25×8 for WWR,
`count=50` for Jobicy, `limit=20` × ≤8 queries for Himalayas. Plumbing a
depth through 129 boards and 5 feeds is Search Engine V2 work and was
explicitly out of scope, so the control is not shown rather than shown and
ignored.

---

## 5. Persistence behaviour

| field | rendered from state | posted value authoritative | survives leave-and-return |
|---|---|---|---|
| which jobs (`scope`) | ✅ | ✅ | ✅ |
| locations | ✅ | ✅ | ✅ |
| paid sources | ✅ | ✅ | ✅ |
| how recent | ✅ **(was hardcoded 14)** | ✅ | ✅ **(was reset to 14)** |
| jobs per search | ✅ **(was a placeholder)** | ✅ | ✅ |
| minimum salary | ✅ **(was always empty)** | ✅ | ✅ **(was erased)** |
| avoid terms | ✅ **(was always empty)** | ✅ **(was append-only)** | ✅ |

Blank now means what the control means, and the two are different on purpose:

- an empty **depth** box is "use Sweep's default" — the key is dropped and
  `config.SEARCH["max_results"]` (15) applies;
- an empty **salary** box is "no floor" — a real choice;
- an empty **avoid** box is "no terms" — which is what makes one removable;
- an empty **picker** is "everywhere this answer covers" — it restores the
  scope's own three keys rather than leaving an earlier pick standing.

### Model penalties vs user penalties

They were merged; they are separated now, which is the whole of B9.

```
derived["penalty_terms"]   the model's, from the résumé — the user cannot clear these
state["avoid"]             the user's, from the box  — authoritative, at -12
```

`make_profile.render` folds the second over the first, so an explicit "I do
not want this" outranks whatever the model thought of the same technology.
`prefs["avoid"]` already existed in the renderer's contract (the CLI's
`--avoid`) and reached only the model prompt; it is now a real penalty, which
also means the CLI and the screen finally agree.

### Starting over

`POST /resume` clears `PREFERENCE_KEYS` — scope and its six expansions,
recency, depth, pay floor, avoid-list, source toggles and the free/paid
choice — together with the parse. It used to clear the avoid-list (which
lived inside `derived`) and keep the other six. Deliberately **not** cleared:
`cap_usd` and the credit readings (a verified key belongs to the person, not
the résumé) and anything in the signed cookie (session identity, the beta
gate, a run already in flight).

---

## 6. Location and work-scope truth table

Measured through the real routes, the real renderer and the real engine
(`sweep/tests/test_search_prefs.py`). The corpus is one row per case the
audit's Phase 3 table asked about.

| row | location text | arrangement | Remote | India | Worldwide | India+BLR | Global+UK |
|---|---|---|---|---|---|---|---|
| A | Anywhere in the World | worldwide | ✅ | — | — | — | — |
| B | United States, Remote | restricted | — | — | — | — | — |
| C | India, Remote | restricted (to home) | ✅ | — | — | — | — |
| D | Bengaluru, Karnataka | hybrid | — | ✅ | ✅ | ✅ | — |
| E | Bangalore, India | onsite | — | ✅ | ✅ | ✅ | — |
| F | Hyderabad, Telangana | onsite | — | ✅ | ✅ | — | — |
| G | London, United Kingdom | onsite | — | — | ✅ | — | ✅ |
| H | Berlin, Germany | onsite | — | — | ✅ | — | — |
| I | Gurugram | onsite | — | ✅ | ✅ | — | — |
| Z | *(blank)* | unstated | — | ✅ | ✅ | ✅ | ✅ |

Every spelling in that table is one a real free board actually produces —
they were read off the 2,083 free rows in `output/`, not invented.
`Hyderabad, Telangana`, `Bengaluru, Karnataka`, `Bangalore, India` and
`Gurugram` are 4 of the top 30 location strings, and matching only the word
"India" would have kept one of them.

### Work-mode × location, end to end

| answer | locations | `SEARCH.locations` | `work_scope` | `LOCATION_HINTS` | LinkedIn | Indeed |
|---|---|---|---|---|---|---|
| Remote | *(hidden)* | `["Remote"]` | `remote` | *(none)* | `geoId=India&f_WT=2` | `IN` |
| Remote | forged `Bengaluru` | `["Remote"]` | `remote` | *(none)* | `geoId=India&f_WT=2` | `IN` |
| India | empty | 6 cities | `india` | 14 Indian fragments | 6 city geoIds | `IN` |
| India | Bengaluru | `["Bengaluru"]` | `india` | bengaluru/bangalore/blr | `geoId=Bengaluru` | `IN` |
| India | Mumbai + Bengaluru | both | `india` | both sets | 2 geoIds | `IN` |
| Worldwide | empty | 9 countries | `global` | *(none)* | 9 geoIds | per country |
| Worldwide | United Kingdom | `["United Kingdom"]` | `global` | UK fragments | `geoId=UK` | **`GB`** |
| Worldwide | UK + Germany | both | `global` | both sets | 2 geoIds | `GB`, `DE` |

---

## 7. Paid actor input tests

The two defects that spent money are pinned by asserting on the URL and the
input dict the actor would actually receive
(`TestPaidActorInputs`, `sweep/tests/test_search_prefs.py`).

```
test_remote_keeps_the_linkedin_remote_filter              f_WT=2 in every URL
test_a_city_cannot_strip_the_linkedin_remote_filter       f_WT=2 survives a picked city
test_a_forged_remote_plus_city_still_cannot_buy_onsite    f_WT=2 survives SITES tampering
test_indeed_searches_the_market_the_location_is_in        UK->GB, DE->DE, US->US
test_indeed_searches_india_for_an_india_sweep             IN
test_indeed_refuses_a_location_it_cannot_map_to_a_market  ValueError, before any spend
test_duplicate_locations_do_not_duplicate_paid_searches   Delhi x3 -> one search per keyword
```

The Indeed refusal matters because of *where* it lands: `main()` puts every
planned search through `build_input` in a preflight loop before a single
actor starts, and exits with the list of problems. An unmappable geography
therefore costs nothing — the same fail-closed discipline `LINKEDIN_GEO_IDS`
already had.

```python
>>> scraper.build_input("indeed", {"location": "Atlantis", ...})
ValueError: indeed: no country code for 'Atlantis'. Indeed searches one
country's site, so running this would bill for the wrong market. Add it to
config.INDEED_COUNTRIES.
```

---

## 8. Before / after

### On the real corpus in `output/` — 2,083 free rows, 5,049 paid

| answer | free before | free after | paid before | paid after |
|---|---:|---:|---:|---:|
| Remote roles | 593 | **593** | 694 | **694** |
| Onsite/hybrid in India | 2,083 | **506** | 5,049 | **2,033** |
| Onsite/hybrid worldwide | 2,083 | **1,170** | 5,049 | **3,659** |

The remote answer is untouched — it was the one that already worked. The
other two stop returning everything, which is what their labels always
claimed and what made them indistinguishable from each other.

### Free-path location narrowing — was no effect at all

| picked | free rows matched |
|---|---:|
| Bengaluru | 218 / 2,083 |
| Hyderabad | 151 / 2,083 |
| Mumbai | 36 / 2,083 |
| India | 633 / 2,083 |
| United Kingdom | 126 / 2,083 |
| Germany | 54 / 2,083 |
| United States | 497 / 2,083 |

### Regression tests, by audit finding

| test | pins |
|---|---|
| `test_a_pay_floor_survives_leaving_the_screen_and_coming_back` | B1 |
| `test_a_freshness_window_survives_leaving_the_screen_and_coming_back` | B2 |
| `test_a_picked_city_narrows_a_free_sweep` · `test_two_picked_cities_match_either` · `test_a_picked_country_narrows_a_free_sweep` | B3 |
| `test_free_mode_does_not_offer_a_depth_control` | B4 |
| `test_the_depth_box_shows_the_depth_that_is_in_force` | B5 |
| `test_the_three_answers_filter_differently` + one per answer | B6 |
| `test_a_city_cannot_strip_the_linkedin_remote_filter` (+ forged variant) | B7 |
| `test_indeed_searches_the_market_the_location_is_in` (+ refusal) | B8 |
| `test_an_avoid_term_can_be_taken_back` · `test_clearing_the_box_clears_the_user_terms_but_not_the_models` | B9 |
| `test_the_submitted_form_wins_with_no_estimate_call_at_all` · `test_the_estimate_prices_a_candidate_and_commits_nothing` · `test_the_preferences_form_posts_where_the_page_says_it_does` (public, against the worker) | B10 |
| `test_an_untouched_screen_and_a_touched_one_plan_the_same_sweep` · `test_changing_a_setting_and_changing_it_back_is_a_no_op` | B11 |
| `test_a_new_resume_resets_every_preference` | B12 |
| `test_the_advanced_badge_reports_the_real_state` | B13 |
| `test_avoiding_a_term_lowers_the_rank_and_removes_nothing` | B14 |
| `test_duplicate_locations_do_not_duplicate_paid_searches` · `test_repeats_do_not_count_twice_against_the_limit` | B16 |
| `test_the_limit_is_stated_and_enforced` | B19 |
| `test_every_control_appears_exactly_once` · `test_the_preferences_form_can_be_submitted_without_javascript` | mobile/desktop, no-JS |
| `test_going_on_to_confirm_and_back_keeps_every_value` | back navigation |
| `test_the_summary_does_not_claim_a_location_under_remote` · `test_the_free_summary_shows_only_what_is_active` | the summary |

### Existing tests that changed, and why

Eleven encoded the old contract. Each was updated to assert the new one, not
relaxed:

- six asserted that `POST /estimate` commits to the session — they now post
  the form to `/configure`, which is where committing moved;
- `test_no_scope_submitted_emits_no_sites_block` asserted B11 itself (no
  scope → no `SITES` block). It is now
  `test_an_unsubmitted_scope_still_materialises_the_default` and asserts the
  opposite, including `remote_only: True`;
- two `/run` tests counted `write_profile` calls; `GET /confirm` now
  re-derives the profile first, so the baseline is two and the guard — that a
  409 adds none — is unchanged;
- two cost-marker tests named the old headings;
- three `make_profile` tests asserted that `prefs["avoid"]` produced no
  penalty. Four new ones now pin that it does, that it outranks the model's
  weight, that an empty list changes nothing, and that a hard-dropped term is
  still not double-counted.

---

## 9. Known remaining issues

**Carried forward from the audit, untouched here by instruction:**

- **B4 proper** — free-path depth. The control is hidden, not implemented.
- **B15** — `dedupe()` keys on company + title and excludes location, so a
  second city costs a second set of paid searches and cannot produce a second
  row for the same posting at the same employer.
- **B17** — Naukri still ignores the Locations picker (`make_profile` overlays
  `locations` onto LinkedIn only). Latent: Naukri is disabled by default.
- **B18** — Naukri's source-side freshness rounds 14 up to 15 days.
  `finalize()` re-applies the true window, so only the fetch is wider.
- **B20** — `_FormError`'s docstring says it never echoes input; the locations
  and avoid-term messages do. Rendered with Alpine `x-text`, so it is text,
  not markup.
- Search Engine V2, industry taxonomy, visa filtering, Fix C.

**New, introduced by this patch and worth stating plainly:**

- **India and worldwide now discard paid rows they paid to fetch.** LinkedIn
  is queried by geoId, which narrows *where* a search runs and not what
  arrangement comes back, so a paid India sweep retrieves remote rows and the
  new filter drops them. LinkedIn's `f_WT` takes `1` (onsite) and `3`
  (hybrid) and could constrain retrieval too — it is not done here because
  the comma-separated form is unverified against this actor and verifying it
  costs real money. It is a cost-efficiency change, not a correctness one.
- **Free-path country matching is a text match, not a gazetteer.** Picking
  "United Kingdom" matches the country name, its abbreviations and four hub
  cities; a posting that says only "Reading" is missed. The India entries are
  complete because India is the home market. Marked with a `ponytail:` comment
  in `config.LOCATION_MATCH` naming the upgrade path. Paid retrieval is
  unaffected — it is constrained at the source.
- **`POST /estimate` still writes the profile file** (it must: the local dry
  run prices the file). It no longer touches the session, and every reader
  re-derives from state first, so the candidate cannot escape. Making pricing
  side-effect-free entirely needs a plan-from-state path in the local dry run.
- **`GET /configure` and `GET /confirm` write the profile on every visit.**
  Idempotent and cheap (a dict assignment in public mode), and it is what
  makes state authoritative. Not optimised.
- **"Remote" is still accepted by `allowed_locations()` though the picker no
  longer offers it** — a page loaded before the change can still post it, and
  a 400 on a control the user cannot see is a worse answer. It is dropped as
  a non-place rather than treated as one.
- **The locations panel is hidden by Alpine.** With JavaScript off it is
  visible under "Remote roles"; anything picked there is dropped server-side,
  so the outcome is right and the affordance is briefly misleading.

---

## 10. Deployment requirements

**Not deployed. Not deploy-ready without a canary — see below.**

### No new environment variables

There is no `SWEEP_*` flag for this work. It is not a guarded experiment like
the V3 fixes; it is a correctness patch in the ordinary path. The existing
flags are unchanged and explicitly not to be touched:
`SWEEP_PROFILE_ENGINE_VERSION`, `SWEEP_ORPHAN_ROLE_GUARD=0`,
`SWEEP_SEMANTIC_SCOPE_NEGATION_VERBS=0`, `SWEEP_CANONICAL_FALLBACK=discard`,
`SWEEP_ROLE_ATTACHMENT_GUARD`, `SWEEP_FAMILY_CENTRALITY_GATE`.

### Both hosts, together

| host | why |
|---|---|
| **Render** (`sweep-beta`) | the screen, the routes, the validator |
| **Oracle** (`sweep-worker`) | `make_profile.render` and `scraper.py` run *there* — `work_scope`, `LOCATION_HINTS` and the Indeed country all take effect in the worker's checkout |

`deploy/sweep_worker.py` renders the profile itself, from the `prefs` Render
sends. A Render-only deploy therefore **fails silently, not loudly** — and
that is worse. Measured, by running the pre-patch renderer against the new
prefs:

```
OLD worker accepted the new prefs WITHOUT ERROR.
  work_scope emitted     : False
  LOCATION_HINTS emitted : False
  avoid penalty emitted  : False
```

`render()` reads the keys it knows and never iterates `prefs`, so an old
worker drops `work_scope`, `location_hints` and the avoid-list on the floor
and runs the OLD semantics: the screen offers three answers, two of them come
back identical, and the location picker does nothing again. Exactly the V3
Fix B trap, with no flag to leave off this time.

**Deploy Oracle first, or both at once. Never Render alone.** The reverse
order is safe: a new worker handed old prefs simply omits the optional keys,
which is the "unset means inherit" rule every other key here follows.

Verification on each host, after pulling:

```
python -c "import sys; sys.path[:0]=['.','auto-apply']; import config, scraper, make_profile; \
print('work_scope' in config.SETTINGS, len(config.LOCATION_MATCH), len(config.INDEED_COUNTRIES), \
scraper.build_input('indeed', {'keywords':'k','location':'United Kingdom','max_results':15})['country'])"
```

Required, verified locally: `True 33 33 GB`. If either host differs, stop.

### Canary

1. **Free sweep, "Onsite or hybrid, in India" + Bengaluru**, through the real
   beta path: upload → derive → review → free → preferences → Start. Confirm
   the results carry Bengaluru-area rows and no remote-only or non-Indian
   ones, and that the count is materially lower than before — this filter is
   *supposed* to narrow, and 506 of 2,083 on the historical corpus is the
   scale to expect.
2. **Free sweep, "Remote roles"**, same résumé. Must be unchanged from
   today's behaviour — it is the answer this patch did not touch, and it is
   the regression control.
3. **Back-navigation**: set a 30-day window and an 80,000 floor, go to the
   next screen, come back. Both must still be shown and still be in force.
4. **Paid smoke, if a key is connected**: one cheap run with
   "Onsite or hybrid, anywhere in the world" + United Kingdom, and confirm
   from the run log that Indeed was asked for `GB`. This is the only change
   whose failure mode is money, and no offline test can prove the actor
   accepts the code.

### Rollback

`git revert` of this commit, on both hosts. There is no flag and no partial
rollback: the state keys, the renderer and the engine filter are one change.
Sessions in flight hold `work_scope` in memory only, so a revert plus a
restart leaves nothing to migrate — and profiles on the worker are per-run
and regenerated.

Git rollback point: **`571f19c`** (the audit, last commit before this work).
