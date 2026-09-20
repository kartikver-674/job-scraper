# Search Preferences — forensic audit

Audit only. Nothing was fixed, no default changed, no behaviour altered, no
deploy. The only file added by this work is this document.

| | |
|---|---|
| HEAD at audit | `2b5befd`, working tree otherwise clean |
| Screen | `GET /configure` → `sweep/templates/configure.html` |
| Persistence endpoint | `POST /estimate` → [app.py:1789](sweep/app.py#L1789) |
| Validator | `_configure_overrides` → [logic.py:936](sweep/logic.py#L936) |
| Renderer | `make_profile.render` → [make_profile.py:1062](auto-apply/make_profile.py#L1062) |
| Engine | [scraper.py](scraper.py) — `finalize()` at [1175](scraper.py#L1175) |
| Method | Flask test client against the real routes; the rendered profile re-loaded through `config`'s own overlay; `finalize()` run over controlled rows; 7,132 real rows in `output/` measured for coverage. No network, no Apify spend. |

---

## Executive table

| SETTING | STATUS | FREE | PAID | DEFAULT CORRECT? | WORDING ACCURATE? |
|---|---|---|---|---|---|
| Where Sweep searches (`site_*`) | **WORKING** | not rendered | full | yes | yes |
| Where you can work (`scope`) | **PARTIAL / MISLEADING** | only 1 of 3 options does anything | full | yes (`remote`) | **no** |
| Locations (`locations`) | **DEAD in Free / PARTIAL in Paid** | **ignored entirely** | LinkedIn + Indeed only | yes (empty) | **no** |
| How recent (`max_age_days`) | **BROKEN (persistence)** | full, post-fetch | full, + source-side | 14 is real | yes |
| Jobs per search (`max_results`) | **DEAD in Free / MISLEADING** | **no meaning at all** | full | **15 is real**, but never displayed back | **no** |
| Minimum salary (`min_comp_usd`) | **BROKEN (persistence)** | full, post-fetch | full, post-fetch | no floor — correct | yes, but weak in practice |
| Skills/industries to avoid (`skip_terms`) | **PARTIAL / MISLEADING** | full | full | none — correct | **no** ("industries") |

Headline answers to the three questions that prompted this:

1. **Jobs per search = 15 is REAL, not 1.** `config.SEARCH["max_results"] = 15`
   and an empty box means "inherit it". The box is a *placeholder*, so it also
   shows `15` when the stored value is `25` — the display is cosmetic, the
   value is not. In **Free Sweep the control has no meaning whatsoever**.
2. **Location filtering is genuinely broken — in the free path it is not
   wired at all.** Free sources are filtered by `config.LOCATION_HINTS`, which
   is `[]` and which no generated profile ever sets. Nothing the picker posts
   reaches them.
3. **"Where you can work" is worse than confusing.** In Free Sweep two of its
   three options produce a byte-identical job set.

---

## 1. Configuration call flow

```
configure.html  (one <form>, NO method and NO action)
   │  every control's change event
   ▼
Alpine @change → fetch POST /estimate
   JSON.stringify(Object.fromEntries(new FormData($el)))
   │
   ▼
POST /estimate                      app.py:1789
   ├─ _configure_overrides(form)    logic.py:936     ← validate, map to state keys
   ├─ free_only() → drop sites_enabled
   ├─ skip_terms → append to state["derived"]["penalty_terms"] @ weight 12
   ├─ make_profile.render(name, derived, _prefs(new_state))   ← the ONLY writer
   ├─ app.write_profile(name, source)
   └─ app.state ← new_state          (applied only after render() succeeded)
   │
   ▼
"Review and start" is an <a href="/confirm">, NOT a submit.
   │
   ▼
POST /run                           app.py:1878
   └─ re-renders the profile from the SAME state, then:
        local  : subprocess scraper.py --profile <name>
        public : worker_client.create_run(derived, _prefs(state))
                    │
                    ▼  Oracle
                 sweep_worker._checked → free_prefs (paid sites forced off)
                 → make_profile.render → assert_only_literals
                 → subprocess scraper.py --profile <name>
   │
   ▼
scraper.py
   ├─ plan_for_site   ← SEARCH.locations, SITES[x].locations, SEARCH.max_results
   ├─ build_input     ← per-actor mapping (see §12)
   ├─ fetch_free      ← ATS boards + feeds; takes NO depth and NO locations
   └─ finalize()      ← max_age_days, min_comp_usd, remote_scopes, score, dedupe
```

**The single most important structural fact:** the preferences `<form>` has no
`method` and no `action`. It cannot be submitted. Preferences are persisted
**only** as a side effect of the live-cost `fetch()`. Verified on the rendered
page:

```
form tag: <form class="stack" @change="busy = true; err = null; fetch('/estimate', …
```

---

## 2. Complete setting inventory

Every `name=` on the rendered page (counted on a real render — there is exactly
one of each, so mobile and desktop cannot disagree):

```
Counter({'scope': 3 (one radio group), 'locations': 1, 'max_age_days': 1,
         'max_results': 1, 'min_comp_usd': 1, 'skip_terms': 1})
plus, in PAID mode only: 'sites_present': 1, 'site_<name>': one per paid site
```

| SETTING | UI LABEL (public) | REQUEST KEY | UI DEFAULT | BACKEND DEFAULT | WORKER DEFAULT | FREE | PAID | POST-FETCH | RANKING | ACTUAL EFFECT |
|---|---|---|---|---|---|---|---|---|---|---|
| Paid sources | Where Sweep searches | `site_<name>` + `sites_present` | from state | `config.SITES[x].enabled` | forced **all off** by `free_prefs()` | n/a (not rendered) | ✅ | – | – | `SITES[x]["enabled"]` |
| Work scope | Where you can work | `scope` | `remote` (from `current_scope()`) | none → `_prefs` gives `locations=["Remote"]`, `remote_scopes` inherits `["worldwide","remote"]` | same | ⚠️ only `remote_scopes` | ✅ | ✅ `reachable()` | – | expands to 4 state keys |
| Locations | Locations | `locations` | from state ✅ | scope's own list | same | ❌ **ignored** | ⚠️ LinkedIn + Indeed only | ❌ | – | `SEARCH.locations`, `SITES.linkedin.locations` |
| Freshness | How recent | `max_age_days` | **hardcoded 14** ❌ | `config.SETTINGS = 14` | same | ✅ | ✅ + `f_TPR` / `freshness` | ✅ `is_recent()` | – | drops older rows |
| Depth | Jobs per search | `max_results` | placeholder `15`, **no value** | `config.SEARCH = 15` | same | ❌ **inert** | ✅ | – | – | actor row count |
| Pay floor | Minimum salary | `min_comp_usd` | placeholder, **no value** | `None` (explicitly written, overriding config's `6000`) | same | ✅ | ✅ | ✅ `comp_ok()` | – | drops stated pay below floor |
| Avoid | Skills or industries to avoid | `skip_terms` | placeholder, **no value** | none | same | ✅ | ✅ | – | ✅ −12 each | appended to `SCORING.penalty_terms` |

**Nothing else on the screen is a setting.** The cost meter, the source
summary, the asterisk footnote and the "Start Free Sweep" form carry no
preference fields.

---

## 3. Default-value audit

Measured, not read off the screen. State was seeded with
`max_age_days=30, max_results=25, min_comp_usd=80000, scope=india,
locations=["Bengaluru"]`, then `GET /configure` was rendered:

```
[how recent]     <select name="max_age_days"> <option value="7">7 days</option>
                   <option value="14" selected>14 days</option>          ← HARDCODED
                   <option value="30">30 days</option> </select>
[jobs per search]<input type="number" name="max_results" placeholder="15" …>   ← no value
[min salary]     <input type="number" name="min_comp_usd" placeholder="No floor" …> ← no value
[skip terms]     <input type="text"   name="skip_terms"  placeholder="Salesforce, CRM, .NET" …> ← no value
[picker]         picked: ["Bengaluru"]                                    ← correct
[scope]          value="india" checked                                    ← correct
```

Four of seven controls render a **fixed** state that ignores what is stored.
Two of those four then post that fixed state back as an instruction (§16).

| Field | PLACEHOLDER | HTML `value` | `selected` | Server default | Session default | Worker default | Source default |
|---|---|---|---|---|---|---|---|
| `scope` | – | `remote/india/global` | from state ✅ | `current_scope()` → `"remote"` | unset | n/a | – |
| `locations` | "All locations" | from state ✅ | – | scope's list | unset | same | LinkedIn: config `["India","Remote"]`; Naukri: config `["Delhi / NCR","Remote"]`; free: none |
| `max_age_days` | – | – | **`14`, hardcoded** | `config.SETTINGS` = 14 | unset → inherit | same | LinkedIn `f_TPR`; Naukri `freshness`; Indeed **none** |
| `max_results` | **`15`** | **none** | – | `config.SEARCH` = 15 | unset → inherit | same | LinkedIn floor 10; Naukri fixed 50; Indeed honours it; free sources: **fixed per feed** |
| `min_comp_usd` | "No floor" | **none** | – | `None` — written explicitly, so config's `6000` never applies | unset | same | none |
| `skip_terms` | "Salesforce, CRM, .NET" | **none** | – | none | none | same | none |

### Jobs per search — the five scenarios, measured

| # | Scenario | Browser posts | `_configure_overrides` | state | profile file | LinkedIn | Indeed | Naukri | Free |
|---|---|---|---|---|---|---|---|---|---|
| A | never opened Advanced | `max_results: ""` | key absent (`if form.get(...)`) | `None` | key omitted | **15** | **15** | 50 | – |
| B | opened, changed nothing | `max_results: ""` | key absent | `None` | key omitted | **15** | **15** | 50 | – |
| C | set to 10 | `"10"` | `10` | `10` | `"max_results": 10` | **10** | 10 | 50 | – |
| D | set to 25 | `"25"` | `25` | `25` | `"max_results": 25` | **25** | 25 | 50 | – |
| E | `0` / `-5` / `500` / `abc` | as typed | `_FormError` | **unchanged** | **not rewritten** | – | – | – | – |
| E′ | `1` (accepted) | `"1"` | `1` | `1` | `"max_results": 1` | **10** (actor floor) | **1** | 50 | – |

Raw evidence, `POST /estimate` then the profile re-loaded through `config`:

```
### A/B blank      HTTP 200   SEARCH.max_results 15   state max_results None
### C set to 10    HTTP 200   SEARCH.max_results 10
### D set to 25    HTTP 200   SEARCH.max_results 25
### E 0            HTTP 400   "Results per search must be between 1 and 200."
### E -5           HTTP 400   same
### E 500          HTTP 400   same
### E abc          HTTP 400   "Results per search must be a whole number."
```

**Verdict: 15 is a real submitted-by-omission default, not a fake.** The bug
is the opposite of the one suspected — the box never shows the *stored* value,
so a saved 25 is displayed as "15".

---

## 4. Where you can work

`scope` is not one setting. It expands, in [`_SCOPE`](sweep/logic.py#L100), into
four state keys at once:

| option | stored `scope` | `remote_scopes` | `locations` / `linkedin_locations` | `linkedin_remote_only` |
|---|---|---|---|---|
| Remote, from anywhere | `"remote"` | `["worldwide","remote"]` | `["Remote"]` | `False` |
| In India — onsite or hybrid | `"india"` | `[]` | Delhi, Gurgaon, Bengaluru, Hyderabad, Pune, Mumbai | `False` |
| Onsite, anywhere in the world | `"global"` | `[]` | US, UK, CA, IE, DE, NL, AU, SG, AE | `False` |

The only thing that filters **retrieved rows** is `remote_scopes`, through
`finalize()` → `reachable()`. `locations` only builds the paid search plan.

**Therefore in Free Sweep, `india` and `global` are the same setting.** Both
set `remote_scopes = []`, which disables the filter, and neither's location
list reaches a free source.

### Concrete examples — measured through `enrich.remote_scope` + `finalize()`

| Job | `remote_scope` | Remote, from anywhere | In India | Onsite worldwide |
|---|---|---|---|---|
| Remote — United States only | `restricted` | **dropped** (unless the employer's board also posts in India — then kept) | kept | kept |
| Remote — anywhere in the world | `worldwide` | **kept** | kept | kept |
| Remote — India | `restricted` | **kept** (locked *to* home) | kept | kept |
| Remote — EMEA | `restricted` | **dropped** | kept | kept |
| Bangalore, India — Hybrid | `hybrid` | **dropped** | kept | kept |
| Bangalore, India — Onsite | `onsite` | **dropped** | kept | kept |
| London, UK — Onsite | `onsite` | **dropped** | kept | kept |
| No location given | `""` | **dropped** | kept | kept |
| Bengaluru + Berlin (multi) | `""` | **dropped** | kept | kept |

```
scope=remote  remote_scopes=['worldwide','remote']  kept 2/9
scope=india   remote_scopes=[]                      kept 9/9
scope=global  remote_scopes=[]                      kept 9/9   ← identical to india
```

Answers to the specific questions:

- **Geo-locked remote**: `restricted`. Kept under "Remote, from anywhere" only
  when `keep_restricted_if_hires_home` (default on) and either the employer's
  board also posts a home-country job, or the lock is *to* India.
- **Hybrid**: dropped by "Remote, from anywhere"; kept by the other two.
- **No location**: `remote_scope = ""` → dropped by "Remote, from anywhere".
  This is a large class — 39% of real free rows.
- **Multi-location**: no special handling; scored as whatever
  `enrich.remote_scope` reads out of the combined text.
- **Does India mean India only?** For *retrieval* on paid boards, yes — six
  city geoIds. For *filtering*, no: nothing filters, so free-source rows from
  anywhere on earth are kept.
- **Does "anywhere in the world" allow every country?** No. Nine verified
  LinkedIn geographies for paid retrieval, and no filter at all afterwards.
- **Sponsorship**: never considered here. `SETTINGS["drop_no_visa"]` is
  `False` and no control on this screen touches it. The results screen labels
  a bucket "needs a visa"; the preferences screen does not filter on it.
- **Free vs paid**: not equal. Paid gets a different *search plan* per option;
  free gets only the `remote_scopes` difference, which is zero between two of
  the three.

### Real-corpus impact of "Remote, from anywhere"

Over 7,132 distinct rows in `output/`:

```
free rows 2083   worldwide 6.8%  remote 3.8%  restricted 33.2%  hybrid 15.9%  onsite 1.2%  blank 39.0%
paid rows 5049   worldwide 2.1%  remote 2.0%  restricted 23.5%  hybrid 15.1%  onsite 6.9%  blank 50.4%

reachable() keeps  free 593/2083 = 28.5%
                   paid 694/5049 = 13.7%
```

It is by far the most aggressive control on the screen, and the copy beside it
talks only about LinkedIn pricing.

---

## 5. Location filter

```
picker (Alpine, dedupes client-side)
  → hidden input  name="locations"  value="Delhi, Bengaluru"
  → _parse_chips  (comma split; letters/digits/space/. + # / - only)
  → validated against allowed_locations() = config.LINKEDIN_GEO_IDS keys
  → state["locations"] AND state["linkedin_locations"]
  → SEARCH["locations"]  and  SITES["linkedin"]["locations"]
  → plan_for_site: SITES[site].get("locations", SEARCH["locations"])
```

Classification, per the audit model:

- **A. constrains retrieval** — LinkedIn (geoId) and Indeed (`location`
  string) **only**.
- **B. filters after retrieval** — **never.** `finalize()` does not read
  `SEARCH["locations"]` at all.
- **D. only some free sources** — **no free source.** `sources.fetch_free()`
  takes `keep_location = scraper.location_allowed`, which reads
  `config.LOCATION_HINTS`. That is `[]`, and `make_profile.render()` has no
  `LOCATION_HINTS` in its `sections` map, so a generated profile can never set
  it.
- **E. ignored** — yes, for the entire Free Sweep.
- **F. conflicts with "Where you can work"** — yes, see §6.

Measured:

```
scraper.LOCATION_HINTS = []
  location_allowed('Bengaluru, India')      = True
  location_allowed('London, United Kingdom')= True
  location_allowed('')                      = True
```

### Test matrix

| input | result |
|---|---|
| none selected | scope's own list survives (`if picked:` guard) ✅ |
| one city | replaces the scope list entirely |
| multiple cities | replaces; one paid search per city per keyword |
| region / country | same, if it is a `LINKEDIN_GEO_IDS` key |
| alternate spelling `Bangalore` | **HTTP 400**, rejected (correct — an unverified name costs money) |
| alias `Gurugram` | **HTTP 400** — deliberately withheld so it cannot be paid for twice under `Gurgaon` |
| `Remote` | accepted; special-cased onto `SITES.linkedin.remote_geo` (India) |
| multi-location posting | not a picker concern; see §4 |
| location missing on a posting | kept — free sources' `location_allowed` returns True for `""` |
| India city while "Remote anywhere" selected | accepted, **and it silently removes LinkedIn's remote filter** — §6 |
| foreign city while "India onsite/hybrid" selected | `London` rejected (no geoId); `United Kingdom` **accepted with no warning** |
| duplicate `Delhi, Delhi, Delhi` via the endpoint | accepted → `['Delhi','Delhi','Delhi']` → **3× the paid searches** |
| 200-char junk | HTTP 400 |

Precedence answer: **Locations REPLACE the scope's list. They never add to it.**

```
scope=india locations='Bengaluru'  → SEARCH.locations ['Bengaluru']   (not the six cities)
scope=india locations='Mumbai, Bengaluru' → ['Mumbai','Bengaluru']
```

---

## 6. Work-mode × location interaction

Measured end-to-end, showing the actual LinkedIn URL the actor is handed:

| WORK MODE | LOCATIONS | intended by the UI | actual `SEARCH.locations` | actual `remote_scopes` | LinkedIn URL | narrows as expected? |
|---|---|---|---|---|---|---|
| Remote anywhere | empty | worldwide remote | `["Remote"]` | `["worldwide","remote"]` | `geoId=India&f_WT=2` | yes (free feeds carry the worldwide half) |
| Remote anywhere | Bengaluru | remote roles based in Bengaluru | `["Bengaluru"]` | `["worldwide","remote"]` | `geoId=Bengaluru` — **no `f_WT=2`** | **NO — buys onsite Bengaluru rows, then `reachable()` discards them** |
| India onsite/hybrid | empty | 6 Indian cities | 6 cities | `[]` | 6 city geoIds, no `f_WT` | yes |
| India onsite/hybrid | Bengaluru | narrow to Bengaluru | `["Bengaluru"]` | `[]` | `geoId=Bengaluru` | yes |
| India onsite/hybrid | Mumbai + Bengaluru | two cities | both | `[]` | two geoIds | yes |
| Worldwide onsite | empty | 9 countries | 9 countries | `[]` | 9 geoIds | yes |
| Worldwide onsite | United Kingdom | UK only | `["United Kingdom"]` | `[]` | `geoId=UK` | LinkedIn yes; **Indeed no** — see below |
| Worldwide onsite | UK + Germany | two countries | both | `[]` | two geoIds | LinkedIn yes; Indeed no |
| Worldwide onsite | **Bengaluru** | should be impossible | `["Bengaluru"]` | `[]` | `geoId=Bengaluru` | **accepted, "worldwide onsite" becomes India-only** |

The two cost-bearing defects:

```
--- scope=remote + picked Bengaluru
  linkedin  …/jobs/search/?keywords=React+Developer&geoId=105214831&f_E=3&f_TPR=r1209600
                                                     ^^^ no f_WT=2
--- scope=global, no city
  indeed    {"position":"React Developer","location":"United States","country":"IN", …}
  indeed    {"position":"React Developer","location":"United Kingdom","country":"IN", …}
                                                                       ^^^^^^^^^^^^
```

`SEARCH["country"]` is `"IN"` in `config.py` and is **not** in
`make_profile.render`'s `sections["SEARCH"]` list, so no profile can ever
change it. Indeed is always queried against the Indian site.

Naukri is a third case: it carries its own `locations` in `config.SITES`, and
`make_profile` only ever overlays LinkedIn's, so **the picker never moves
Naukri** — it stays on `Delhi / NCR` + `Remote` whatever the user chose.
(Naukri is off by default, so this is latent.)

---

## 7. Recency

- **Default**: 14 days. Real (`config.SETTINGS["max_age_days"] = 14`) but the
  `<option … selected>` is hardcoded rather than read from state.
- **Unit**: whole days. Accepted range at the server: **1–365**. The `<select>`
  offers only 7 / 14 / 30.
- **Computed from**: the posting's own `Posted Date`, parsed by
  `scraper._parse_date`. Not the scrape time.
- **Missing dates**: **kept** — `is_recent` returns `not SETTINGS["drop_undated"]`
  and `drop_undated` is `False`. The UI never mentions this.
- **Relative dates**: handled — `"6 days ago"`, `"30+ days ago"`,
  `"3 weeks ago"`, `"today"`/`"just posted"`. Unrecognised prose
  (`"Posted recently"`) parses to `None` and is therefore **kept**.
- **Timezone**: naive `datetime.now()` minus a naive parsed date, both local.
  A ±1-day slop at the boundary, which does not matter at a 7/14/30-day grain.

### Boundary, measured at a 14-day window

```
max_age_days=None  kept 14/14
max_age_days=30    kept 13/14   (drops 45d)
max_age_days=14    kept  9/14   ['0d','13d','14d','1d','7d','garbage','nodate','relative6','today']
max_age_days=7     kept  7/14
max_age_days=1     kept  5/14
max_age_days=14 + drop_undated=True  →  kept 7
```

**14 days is inclusive**: a 14-day-old posting is kept, a 15-day-old one is
dropped. `(now - posted).days <= max_age_days`.

### Source-side vs post-fetch

| source | source-side constraint | post-fetch `is_recent`? |
|---|---|---|
| LinkedIn | ✅ `f_TPR=r<days×86400>` (`r1209600` for 14) | ✅ |
| Indeed | ❌ none | ✅ (the only filter) |
| Naukri | ⚠️ `freshness` enum, **14 → "15"** | ✅ (trims the extra day) |
| every ATS board | ❌ whole board fetched | ✅ |
| every feed | ❌ whole feed fetched | ✅ |

So "14 days" does not mean 14 days *at* every source — but it does mean 14
days in the output, because `finalize()` re-applies it to everything.

Date coverage on real data is excellent: **100% of both free and paid rows
carry a parseable `date_posted`**, so this filter genuinely bites.

---

## 8. Jobs per search

**What it means**: results per `(keyword × location [× company])` combination,
per paid site. Not per sweep, not per source, not per board, not post-dedupe.
`build_search_plan` makes one search dict per combo and stamps
`SEARCH["max_results"]` on each; `effective_search` is the single authority on
billable depth and applies the per-site overrides.

| question | answer |
|---|---|
| jobs per QUERY? | **yes** — one query is one title in one location |
| jobs per SOURCE? | no |
| jobs per COMPANY BOARD? | **no — company boards ignore it entirely** |
| jobs per Apify actor? | per *search*, and each actor run is one search |
| total for the sweep? | no |
| max rows requested? | yes |
| max rows retained? | no — everything fetched is scored |
| max after dedupe? | no |

### Free Sweep

`fetch_free()` takes no depth argument. `sources.fetch_free(ats_boards,
feed_cfg, keep_title, keep_location, …)` — the signature has nowhere to put
one. Every free source pulls a fixed amount:

| free source | how much it pulls | movable by this control? |
|---|---|---|
| Greenhouse / Lever / Ashby / Breezy | the **whole board** | ❌ |
| SmartRecruiters | `?limit=100`, hardcoded in the URL | ❌ |
| RemoteOK | the whole feed | ❌ |
| We Work Remotely | 25 newest × 8 categories | ❌ |
| Remotive | ~40, whole category | ❌ |
| Jobicy | `count` from `config.FEEDS` = 50 | ❌ |
| Himalayas | `limit=20` × up to 8 role-keyword queries | ❌ |

**"Jobs per search" has no interpretation at all in Free Sweep.** It is
rendered, validated, stored, written into the profile and shipped to the
Oracle worker, where nothing reads it. The screen even says *"Results per
search is how many jobs each single search brings back"* — the free path runs
no searches in that sense; it enumerates 129 configured boards and 5 feeds.

### Paid Sweep, measured at each layer

| value | form | POST /estimate | state | profile | LinkedIn actor | Indeed actor | Naukri actor |
|---|---|---|---|---|---|---|---|
| 1 | `"1"` | ok | 1 | `"max_results": 1` | **10** (`ACTOR_MIN_RESULTS`) | 1 | 50 |
| 5 | `"5"` | ok | 5 | 5 | **10** | 5 | 50 |
| 15 (blank) | `""` | key dropped | `None` | omitted | 15 | 15 | 50 |
| 25 | `"25"` | ok | 25 | 25 | 25 | 25 | 50 |

```
--- depth=1
  linkedin  depth=10   {"urls":[…], "count": 10, …}      ← floored
  indeed    depth=1    {… "maxItemsPerSearch": 1 …}
  naukri    depth=50   {… "maxJobs": 50 …}               ← per-run minimum charge
```

So **1 → 15 changes nothing on Naukri, and changes 10 → 15 on LinkedIn**
(not 1 → 15). Both are deliberate and documented; neither is stated on the
screen, which says only that Naukri "does not change".

---

## 9. Minimum salary

The UI claim — *"Most jobs do not state a salary, and those are always kept.
This only drops jobs that state one below your figure."* — is **true**.

`comp_ok(text, floor)` → `True if comp_max_usd(text) is None else top >= floor`.
`comp_max_usd` returns `None` for empty, undisclosed wording, unparseable text,
and — critically — **any figure with no identifiable currency**, so it fails
open rather than guessing.

Runs **after** scoring and **before** ranking, inside `finalize()`, on every
row from every source, free and paid alike.

### Parse table, measured

| input | `comp_max_usd` (USD) | survives an 80,000 floor? |
|---|---|---|
| `''` | `None` | ✅ kept |
| `Competitive` | `None` | ✅ |
| `Not disclosed` | `None` | ✅ |
| `USD 50000-50000 per year` | 50,000 | ❌ dropped |
| `USD 100000-100000 per year` | 100,000 | ✅ |
| `USD 60000-90000 per year` | **90,000** | ✅ |
| `USD 100000-150000 per year` | **150,000** | ✅ |
| `USD 45 per hour` | 93,600 (×2080) | ✅ |
| `USD 8000 per month` | 96,000 (×12) | ✅ |
| `18-24 LPA` | 27,360 (INR ×0.0114) | ❌ |
| `Rs. 3000000 per year` | 34,200 | ❌ |
| `EUR 70000-95000 per year` | 102,600 (×1.08) | ✅ |
| `GBP 65000 per year` | 82,550 (×1.27) | ✅ |
| `50000-80000 per month` (no currency) | `None` | ✅ kept |
| `$120,000` | 120,000 | ✅ |
| `banana` | `None` | ✅ kept |

- **Currencies are converted**, from a hardcoded snapshot
  (`USD_PER`, 10 currencies). The UI's "Minimum yearly pay, USD" is therefore
  honest.
- **Which end of a range is compared**: the **maximum** — literally
  `max()` of every number found in the string. Generous by design; it means
  `60k–90k` survives an 80k floor.
- **Hourly / monthly** are annualised (2080 h, 12 mo).
- **Unknown salary is retained.** Confirmed.
- **Before ranking**, after scoring. Free and paid both.

### Practical effectiveness — real corpus

| bucket | rows | rows with a parseable salary |
|---|---|---|
| paid | 5,049 | 331 (**6.6%**) |
| free | 2,083 | 107 (**5.1%**) |

| source | rows | usable salary |
|---|---|---|
| linkedin | 4,792 | 6.2% |
| greenhouse | 1,140 | **0.0%** |
| indeed | 257 | 12.5% |
| himalayas | 246 | 23.2% |
| optum | 245 | 0.0% |
| jobicy | 94 | **50.0%** |
| ashby / lever / wwr / remoteok / amazon / accenture | 39–153 each | 0.0% |
| remotive | 5 | 60.0% |

The setting is wired, correct, and touches roughly **one row in sixteen**. A
user who sets a floor and sees the count barely move is seeing correct
behaviour; the copy does not prepare them for how rare stated pay is. Measured
end to end: an 80,000 floor kept 13 of 16 fixture rows; a 200,000 floor kept 5
of 16 — and the three it dropped at 80k were the only three that stated a low
figure.

---

## 10. Skills or industries to avoid

```
skip_terms  →  _parse_chips (letters, digits, space, . + # / - only)
            →  appended to state["derived"]["penalty_terms"] at weight 12
            →  make_profile._weights(sign=-1)  →  SCORING["penalty_terms"][term] = -12
            →  scraper._compile(term)  →  (?<![a-z0-9])<escaped>(?![a-z0-9])
            →  score_job: searched over  title + "\n" + description + "\n" + Experience
```

| question | answer |
|---|---|
| raw text penalty terms? | **yes** |
| canonical concepts / aliases? | **no.** `skill_concepts` applies to positive weights only; penalties are a plain per-term regex loop |
| title exclusions? | no |
| description penalties? | yes |
| hard drops? | **no** |
| ranking-only? | **yes** |
| how large? | **exactly −12** per matching term, the top of `PENALTY_RANGE` |
| title or description? | **both**, plus the `Experience` field |
| Free and Paid? | **both** — it is in `SCORING`, applied in `score_job` for every row |

**"Rank these lower. Nothing is removed outright." — VERIFIED TRUE.**
`SETTINGS["min_score"]` is `None` by default and `make_profile.render` has no
`min_score` in its `sections` map, so no generated profile can set one.
Nothing downstream drops a row for a low score.

Measured (config's own penalty list as the baseline, one term swapped in at a
time):

```
base                       React Dev 21 | Banking React Dev  5 | .NET React Dev  5 | React (Salesforce) -13
avoid=['banking']   4/4    React Dev 21 | .NET React Dev      5 | Banking React Dev -7 | React (Salesforce) -13
avoid=['crm']       4/4    React Dev 21 | Banking React Dev   5 | .NET React Dev   5 | React (Salesforce) -19
avoid=['.net']      4/4    React Dev 21 | Banking React Dev   5 | .NET React Dev  -1 | React (Salesforce) -13
avoid=[sf,crm,.net] 4/4    React Dev 21 | Banking React Dev   5 | .NET React Dev  -1 | React (Salesforce) -19
```

Row count never changes. Only the order does.

### "Industries" is not true

`banking`, `healthcare`, `automotive` become ordinary regex terms matched
anywhere in the title or description. There is no industry taxonomy, no NAICS
mapping, no company-sector lookup. Measured: `banking` was accepted and
applied as `-12`, same as `.net`.

Consequences a user cannot predict:

- A fintech job whose description says *"we serve banking clients"* is
  penalised identically to a core banking role.
- `Salesforce` is already in `config.SCORING["penalty_terms"]` at `-12` —
  but a *generated* profile **replaces** `penalty_terms` wholesale
  (`config._overlay` does `SCORING.update`), so what the user types is what
  applies, plus the model's own list.
- There is no way to remove a term once added (§16).

Validation is sound: `c++ <script>` → HTTP 400, rejected rather than
sanitised.

---

## 11. Free vs paid semantics

| | FREE | PAID / APIFY |
|---|---|---|
| work mode | **PARTIAL** — only `remote_scopes` applies; `india` and `global` are **identical** | **FULL** — changes the search plan *and* the post-fetch filter |
| locations | **IGNORED** | **PARTIAL** — LinkedIn + Indeed only; never Naukri; never a post-fetch filter |
| recency | **FULL** (post-fetch only) | **FULL** (source-side on LinkedIn/Naukri + post-fetch everywhere) |
| jobs per search | **IGNORED** | **FULL** (with a LinkedIn floor of 10 and a Naukri fixed 50) |
| min salary | **FULL** | **FULL** — same code path, same `finalize()` |
| avoid terms | **FULL** | **FULL** |
| paid source toggles | not rendered; forced all-off by the worker | **FULL** |

Three of six settings behave differently, and two are dead, in the mode that
is the product's default front door.

---

## 12. Source-by-source support

| source | work mode | locations | recency | depth | salary | avoid |
|---|---|---|---|---|---|---|
| **LinkedIn** (paid) | direct `f_WT=2` when the location is `Remote` | direct `geoId` | direct `f_TPR` | direct `count`, **floored at 10** | emulated post-fetch | emulated (ranking) |
| **Indeed** (paid) | unsupported | `location` string — but `country` is **hardwired `IN`** | **unsupported** → emulated | direct `maxItemsPerSearch` | emulated | emulated |
| **Naukri** (paid, off) | `workMode:["remote"]` when the location is `Remote` | **ignores the picker** — own config list, `cities` by id | `freshness` enum, **14 → 15** | **unsupported** — fixed `maxJobs: 50` | emulated | emulated |
| **Greenhouse / Lever / Ashby / Breezy / SmartRecruiters** | unsupported | **unsupported** (`LOCATION_HINTS` is empty) | unsupported → emulated | unsupported — whole board | emulated (boards publish **0%** salary) | emulated |
| **RemoteOK / WWR / Remotive / Jobicy** | inherently remote-only boards | unsupported | unsupported → emulated | unsupported — fixed counts | emulated | emulated |
| **Himalayas** | inherently remote | unsupported | unsupported → emulated | fixed `limit=20` × ≤8 queries (queries come from `role_keywords`, **not** from this screen) | emulated | emulated |
| **Optum / Enterprise** (disabled) | unsupported | unsupported | unsupported → emulated | own `max_pages` | emulated | emulated |

Raw actor inputs, captured from `build_input(effective_search(...))`:

```
linkedin  {"urls":["…?keywords=React+Developer&geoId=102713980&f_WT=2&f_E=3&f_TPR=r1209600"],"count":15,"scrapeCompany":false}
indeed    {"position":"React Developer","location":"Remote","country":"IN","maxItemsPerSearch":15,…}
naukri    {"keyword":"React Developer","maxJobs":50,"fetchDetails":true,"sortBy":"relevance","freshness":"15","workMode":["remote"],"experience":"2"}
```

---

## 13. A/B effectiveness results

Live sweeps were **not** run (they cost money and hit third-party boards).
These are two kinds of real measurement instead: the engine's own
`finalize()` executed over controlled rows, and coverage statistics over
7,132 distinct rows already in `output/`.

| change | expected | actual |
|---|---|---|
| `max_age_days` 14 → 7 | narrower | 9/14 → 7/14 on the fixture; 100% of real rows carry a parseable date, so it bites on the whole corpus |
| `max_age_days` 14 → 30 | wider | 9/14 → 13/14 |
| `min_comp_usd` none → 80,000 | narrower **only among rows with stated pay** | 16 → 13 on the fixture; on the real corpus only **5.1% free / 6.6% paid** of rows are even eligible to be dropped |
| `min_comp_usd` none → 200,000 | much narrower among the same 6% | 16 → 5 |
| scope `india` → `global` | different | **identical** in Free Sweep (9/9 both) |
| scope `india` → `remote` | much narrower | 9/9 → 2/9 on the fixture; **28.5% free / 13.7% paid** on the real corpus |
| avoid = Salesforce / CRM / .NET | same set, changed order | **4/4 kept, order changed** — exactly as advertised, −12 per term |
| locations = Bengaluru, Free Sweep | narrower | **no change** — free sources never see it |
| locations = Bengaluru, Paid Sweep | narrower retrieval | LinkedIn `geoId=105214831`; **and `f_WT=2` disappears if the scope was "remote"** |
| `max_results` 1 → 15, Free Sweep | deeper | **no change at any layer** |
| `max_results` 1 → 15, Paid Sweep | deeper | LinkedIn 10 → 15; Indeed 1 → 15; Naukri 50 → 50 |

One measurement worth stating on its own: **`dedupe()` keys on company + title
and deliberately excludes location** ([scraper.py:722](scraper.py#L722)). Adding
a second city therefore doubles the paid searches and the bill, but cannot
produce a second row for the same posting at the same employer. Only a
published requisition number separates them.

---

## 14. Dead / cosmetic settings

| control | classification | detail |
|---|---|---|
| **Jobs per search**, Free Sweep | **forwarded but ignored** | validated, stored, written into the profile, shipped to Oracle, read by nothing |
| **Locations**, Free Sweep | **forwarded but ignored** | free sources filter on `config.LOCATION_HINTS`, which is `[]` and unreachable from a profile |
| **Where you can work** → `india` vs `global`, Free Sweep | **indistinguishable** | both set `remote_scopes = []`; their location lists go nowhere |
| **How recent** `<option selected>` | **overwritten by a hardcoded value** | the template pins `14`, whatever state says |
| **Minimum salary** field value | **displayed but not submitted** back | no `value` attribute; the box is always empty on load |
| **Skip terms** field value | **displayed but not submitted** back | same; and there is no removal path at all |
| **"using defaults"** badge on the disclosure | **static text** | says "using defaults" even when three of the four are non-default |
| Naukri + Locations | **unsupported by the selected source** | `make_profile` only overlays LinkedIn's locations |
| `SEARCH["country"]` | **not exposed, not overridable** | hardwired `IN`; no profile can change it |

---

## 15. UI wording mismatches

| text | behaviour | verdict |
|---|---|---|
| **"Where you can work"** | It is not eligibility, not visa, not willingness. It sets (a) which geographies the paid boards are queried in, and (b) whether a `worldwide`/`remote` scope filter runs at all. In Free Sweep only (b) exists, and two of the three options set it identically. | **MISLEADING** |
| "Remote, from anywhere" | Drops hybrid, onsite, region-locked and *undated-location* rows — 71.5% of real free rows. Keeps geo-locked rows when the employer also posts in India. | understates how aggressive it is |
| "In India — onsite or hybrid" | Does not restrict to India in Free Sweep, and does not exclude remote. | **MISLEADING** |
| "Onsite, anywhere in the world" · "Most of these need visa sponsorship." | Nine countries on the paid boards; in Free Sweep, identical to the India option. Sponsorship is never filtered on. | **MISLEADING** |
| "Leave this empty to search everywhere the choice above covers, or pick cities to narrow it." | True for LinkedIn/Indeed. False for every free source, and false for Naukri. | **MISLEADING in Free** |
| "Jobs per search … how many jobs each single search brings back. Leave it empty for Sweep's own default." | Correct for paid. In Free Sweep there is no "single search" and the number does nothing. | **MISLEADING in Free** |
| the `15` in the depth box | A placeholder. It also reads `15` when 25 is stored. | **MISLEADING** |
| "A wider window finds more jobs at the same price." | True. | accurate |
| "Most jobs do not state a salary, and those are always kept. This only drops jobs that state one below your figure." | True. | **accurate** — but omits that only ~6% of rows state pay at all |
| "Rank these lower. Nothing is removed outright." | True. | **accurate** |
| "Skills or **industries** to avoid" | Plain text matching. No industry model. | **MISLEADING** |
| "What Sweep will search → Where: `Bengaluru`" (`_search_summary.html`) | In a Free Sweep the picked locations are displayed as what will be searched, and they are ignored. | **MISLEADING in Free** |
| "Advanced search settings — using defaults" | Static. | **MISLEADING** |

Wording is not proposed here — that was the instruction — but the semantics
are now pinned, so any rewrite has these facts to work from:

- the scope control mixes **two** independent ideas (*where to look* for paid
  retrieval, and *what kind of work arrangement to keep* post-fetch);
- the locations control is a **paid-retrieval** control, not a filter;
- "Jobs per search" is a **paid-retrieval depth** control with no free meaning.

---

## 16. Persistence / back navigation

Measured. The user sets everything, leaves, comes back, and touches any one
control:

```
1. POST {"scope":"india","locations":"Bengaluru","max_age_days":"30",
         "max_results":"25","min_comp_usd":"80000","skip_terms":"Salesforce"}
   → SEARCH.max_results 25   max_age_days 30   min_comp_usd 80000   penalty +salesforce:-12

2. reload /configure — the page renders 14 selected, both number boxes empty,
   skip box empty — then the user changes ANY control, so the whole form reposts:
   POST {"scope":"india","locations":"Bengaluru","max_age_days":"14",
         "max_results":"","min_comp_usd":"","skip_terms":""}
   → SEARCH.max_results 25   max_age_days 14   min_comp_usd None   penalty +salesforce:-12
                                        ^^                 ^^^^
                                   silently reset    silently erased
```

| field | survives a reload? | survives reload + one change? | shown correctly after reload? |
|---|---|---|---|
| `scope` | ✅ | ✅ | ✅ |
| `locations` | ✅ | ✅ | ✅ |
| paid source toggles | ✅ | ✅ | ✅ |
| `max_age_days` | ✅ in state | ❌ **reset to 14** | ❌ always shows 14 |
| `min_comp_usd` | ✅ in state | ❌ **erased to None** | ❌ always empty |
| `max_results` | ✅ | ✅ (blank is "no opinion") | ❌ always shows the `15` placeholder |
| `skip_terms` | ✅ | ✅ (blank is "no opinion") | ❌ always empty, **and cannot be removed** |

```
### skip=Salesforce, CRM, .NET   → {'salesforce':-12,'crm':-12,'.net':-12}
### then blank again             → {'salesforce':-12,'crm':-12,'.net':-12}   unchanged
### then banking, healthcare     → the previous three PLUS banking, healthcare
```

There is no state in the browser beyond the DOM — no `localStorage`, no
`sessionStorage`. Server session state is authoritative and is a per-browser
room in `public.SessionStore` (2-hour TTL, 500-room cap) in public mode, or a
plain process dict locally.

### Starting over with another résumé

`POST /resume` clears exactly three keys:

```python
for stale in ("derived", "profile", "profile_source"):
    app.state.pop(stale, None)
```

So `scope`, `locations`, `linkedin_locations`, `remote_scopes`,
`linkedin_remote_only`, `max_age_days`, `max_results`, `min_comp_usd`,
`sites_enabled` and `free_only` **all carry over to the next candidate**,
while `skip_terms` (which live inside `derived`) do not. Half the preferences
leak and half reset, with nothing on screen saying which.

### The bigger persistence risk

Because the form has no `action` and the "Review and start" control is an
`<a href>`, **the only thing that saves a preference is the live-estimate
`fetch()`**. Consequences:

- With JavaScript unavailable or broken, every control on the screen is inert
  — the page still renders, the boxes still accept input, and nothing is ever
  saved.
- Typing into the salary or skip box and then clicking "Review and start"
  fires `change` on blur and then navigates; the in-flight `POST /estimate`
  can be aborted by the navigation, so the last edit may be lost silently.
- In public **Free** mode the cost panel is not even rendered, yet the same
  `fetch` runs on every change and makes a round trip to the Oracle worker to
  price a plan that is always zero.

---

## 17. Mobile / desktop consistency

**No defect found.** Verified structurally rather than visually:

- The rendered page contains exactly one input per setting
  (`Counter({'scope': 3 radios, 'locations': 1, 'max_age_days': 1,
  'max_results': 1, 'min_comp_usd': 1, 'skip_terms': 1})`). There is no
  duplicated mobile markup that could post a second value.
- Every `display:none` inside a media query in `sweep.css` targets the step
  tracker, the weights table or the listings table — never a Configure
  control.
- The Advanced section is a `<details>`, which keeps its fields in the form
  whether open or closed; that is why a collapsed section still posts its
  (empty) defaults.

Mobile and desktop submit the same payload.

---

## 18. Validation / edge cases

All server-side, in `_configure_overrides`. Nothing relies on the HTML
attributes.

| input | result | leaves state untouched? |
|---|---|---|
| `max_results` = `0`, `-5`, `500` | 400 "must be between 1 and 200" | ✅ |
| `max_results` = `abc` | 400 "must be a whole number" | ✅ |
| `max_results` = `""` | accepted as "no opinion" | ✅ |
| `max_age_days` = `0`, `400` | 400 "must be between 1 and 365" | ✅ |
| `max_age_days` = `abc` | 400 | ✅ |
| `min_comp_usd` = `-1`, `100000001` | 400 "must be between 0 and 100000000" | ✅ |
| `min_comp_usd` = `abc` | 400 | ✅ |
| `min_comp_usd` = `""` | accepted as **no floor** (not "no opinion") | – |
| `scope` = `mars` | 400 "Choose where you can work." | ✅ |
| `locations` = `Bangalore` / `Gurugram` / 200 chars of junk | 400, checked against `LINKEDIN_GEO_IDS` | ✅ |
| `locations` = `Delhi, Delhi, Delhi` | **accepted** → 3 identical paid searches | – |
| `skip_terms` = `c++ <script>` | 400, rejected not sanitised | ✅ |
| `skip_terms` = `" , , "` | accepted, yields nothing | – |
| `skip_terms` = 300 terms | **accepted** → 300 compiled regexes run over every row | – |
| empty JSON body `{}` | 200, and **still rewrites the profile** | – |
| partial body `{"max_age_days":"30"}` | 200, patches only that key | – |
| forged `sites_present` in free mode | dropped; `sites_enabled` unchanged | ✅ |
| `sites_present` with no boxes in paid mode | **all paid sites switched off** — correct, that is what the marker is for | – |

Good properties worth recording:
- **Fail-atomic.** `_configure_overrides` raises on the first bad field with
  nothing applied, and `render()` runs against a *copy* of state; state is
  only replaced after the render succeeds. Verified on every 400 above.
- **Fail-closed on money.** An unverified LinkedIn geography is refused at
  three layers (`allowed_locations`, `make_profile.render`, `_build_linkedin_url`).
- **Public mode** carries `SESSION_COOKIE_SAMESITE=Strict` plus an explicit
  `Origin` check on every non-GET, so `POST /estimate` is not cross-site
  callable there.

Minor: `_FormError`'s docstring says it "never echoes the raw input back", but
the locations and skip-term messages both interpolate the offending value
(`f"{unknown[0]!r} is not a location…"`). The response is JSON rendered into
the page with Alpine `x-text`, so it is not an injection — only a broken
promise.

The local (non-public) console sets no SameSite and has no Origin check. It is
a localhost operator tool with no authentication to defeat, so this is noted
rather than raised.

---

## Bug list

### B1 — Minimum salary is silently erased on any return to Configure
| | |
|---|---|
| Severity | **High** |
| Root cause | `<input name="min_comp_usd">` renders no `value`; blank is mapped to `None` (a real "no floor"), not to "absent" — [logic.py:1003](sweep/logic.py#L1003) |
| Free / Paid | **Both** |
| User impact | A pay floor set before a back-navigation is removed without a word, and the user is shown an empty box that looks like it never took |
| Correct layer to fix | Template — render `value="{{ min_comp_usd }}"` from state |

### B2 — "How recent" silently resets to 14 days on any return to Configure
| | |
|---|---|
| Severity | **High** |
| Root cause | `<option value="14" selected>` is hardcoded in [configure.html](sweep/templates/configure.html); a `<select>` always posts, so the displayed lie is written back |
| Free / Paid | **Both** |
| User impact | A 30-day window silently becomes 14 the next time any control is touched — the user loses half their results and is never told |
| Correct layer to fix | Template — `{{ 'selected' if max_age_days == 14 }}`, the same pattern two other selects in this app already use |

### B3 — Locations do nothing in Free Sweep, and the summary says they do
| | |
|---|---|
| Severity | **High** |
| Root cause | Free sources filter on `config.LOCATION_HINTS` (`[]`), and `make_profile.render`'s `sections` map has no `LOCATION_HINTS`, so a generated profile can never set it — [scraper.py:196](scraper.py#L196), [make_profile.py:1074](auto-apply/make_profile.py#L1074) |
| Free / Paid | **Free** (dead); Paid is partial — LinkedIn + Indeed only, never Naukri, never post-fetch |
| User impact | A Free Sweep user picks Bengaluru, the confirm screen says "Where: Bengaluru", and the sweep returns jobs from everywhere |
| Correct layer to fix | Engine + renderer (emit `LOCATION_HINTS` from the picked locations) **or** product decision to not offer the control in Free mode — not the template |

### B4 — "Jobs per search" is inert in Free Sweep
| | |
|---|---|
| Severity | **High** |
| Root cause | `sources.fetch_free()` takes no depth argument; every free adapter uses a fixed count — [sources/\_\_init\_\_.py:34](sources/__init__.py#L34) |
| Free / Paid | **Free** |
| User impact | A control is offered, explained, validated and stored, and changes nothing. Raising it to 200 does not fetch one extra job |
| Correct layer to fix | UI (hide or relabel in free mode) — plumbing a depth into 129 whole-board fetches is a Search Engine V2 decision, not a bug fix |

### B5 — The depth box never shows the stored value
| | |
|---|---|
| Severity | Medium |
| Root cause | `placeholder="15"` with no `value`; the placeholder is also load-bearing for the `+`/`−` stepper, which reads `box.value \|\| box.placeholder` |
| Free / Paid | Both |
| User impact | 15 is displayed while 25 is in force. This is the origin of the "is 15 real?" suspicion — the number is real, the display is not |
| Correct layer to fix | Template, carefully: the stepper's placeholder fallback has to keep working |

### B6 — "In India" and "Onsite worldwide" are the same sweep in Free mode
| | |
|---|---|
| Severity | **High** |
| Root cause | Both set `remote_scopes = []`; their `locations` lists only ever reach the paid plan — [logic.py:100](sweep/logic.py#L100) |
| Free / Paid | **Free** |
| User impact | Two of three options in the most prominent control on the screen are indistinguishable. Measured: 9/9 rows kept by both |
| Correct layer to fix | Product + engine — the scope control needs a free-path meaning (a location filter and/or a `remote_scopes` value that differs) |

### B7 — Picking a city under "Remote, from anywhere" silently drops LinkedIn's remote filter
| | |
|---|---|
| Severity | **High — spends money** |
| Root cause | `_SCOPE["remote"]` sets `linkedin_remote_only = False` and relies on the literal token `"Remote"` in the location list to trigger `f_WT=2` in `_build_linkedin_url`. The picker replaces that list — [make_profile.py:1130](auto-apply/make_profile.py#L1130), [scraper.py:796](scraper.py#L796) |
| Free / Paid | **Paid** |
| User impact | LinkedIn is billed for onsite Bengaluru rows which `reachable()` then discards, because `remote_scopes` is still `["worldwide","remote"]`. Paid for, fetched, thrown away |
| Correct layer to fix | `_configure_overrides` — set `linkedin_remote_only = True` when the scope is `remote` and the picker has narrowed it |

### B8 — Indeed is always searched against the Indian site
| | |
|---|---|
| Severity | **High — spends money** |
| Root cause | `SEARCH["country"] = "IN"` in config, and `country` is absent from `make_profile.render`'s `sections["SEARCH"]`, so no profile can override it |
| Free / Paid | **Paid** |
| User impact | "Onsite, anywhere in the world" + United Kingdom sends `{"location":"United Kingdom","country":"IN"}` to the Indeed actor — a paid search of the wrong market |
| Correct layer to fix | Renderer + scope table — derive `country` from the scope/locations, or refuse the combination the way LinkedIn geoIds are refused |

### B9 — Skip terms can be added but never removed
| | |
|---|---|
| Severity | Medium |
| Root cause | `if form.get("skip_terms")` treats blank as "no opinion", and the appended terms live in `derived["penalty_terms"]` with no removal path — [app.py:1817](sweep/app.py#L1817) |
| Free / Paid | Both |
| User impact | A mistyped or regretted term penalises every sweep for the rest of the session, invisibly (the box renders empty) |
| Correct layer to fix | Template + route — render the current terms and treat the posted list as authoritative |

### B10 — Preferences persist only as a side effect of the cost-estimate fetch
| | |
|---|---|
| Severity | **High** |
| Root cause | The `<form>` has no `method`/`action`; "Review and start" is an `<a href>`; the only writer is the Alpine `@change` → `POST /estimate` |
| Free / Paid | Both |
| User impact | No JavaScript → every control is silently inert. A last edit followed immediately by clicking through can be lost to the aborted fetch. In public Free mode it also makes a pointless Oracle round trip on every keystroke-blur |
| Correct layer to fix | Route + template — give the form a real `POST /configure` and keep `/estimate` for pricing only |

### B11 — An untouched Configure screen runs a different search from the one it shows
| | |
|---|---|
| Severity | Medium |
| Root cause | `_prefs` leaves `linkedin_locations = None`, so `make_profile` writes no `SITES` block and LinkedIn inherits config's `["India","Remote"]`, while the radio shows "Remote, from anywhere" (`current_scope()` default) |
| Free / Paid | **Paid** |
| User impact | Measured: 4 planned LinkedIn searches become 2 the instant any control is touched. The user watches the price halve for no reason they caused, and before that they were being quoted an India-onsite sweep labelled "remote" |
| Correct layer to fix | `_prefs` / `/review` — materialise the default scope into state when the profile is first written |

### B12 — Search preferences leak from one résumé to the next
| | |
|---|---|
| Severity | Medium |
| Root cause | `POST /resume` clears only `derived`, `profile`, `profile_source` — [app.py:1268](sweep/app.py#L1268) |
| Free / Paid | Both |
| User impact | The second candidate inherits the first's scope, cities, salary floor, depth and source toggles — but not their skip terms, which live in `derived`. Inconsistent, and nothing on screen says so |
| Correct layer to fix | `/resume` — clear the preference keys too, or deliberately keep them all and say so |

### B13 — "Advanced search settings — using defaults" is static text
| | |
|---|---|
| Severity | Low |
| Root cause | Hardcoded `<span class="meta">using defaults</span>` |
| Free / Paid | Both |
| User impact | The one affordance that would tell a user something is hidden behind the disclosure always says nothing is |
| Correct layer to fix | Template |

### B14 — "industries" is not implemented
| | |
|---|---|
| Severity | Medium |
| Root cause | Skip terms become plain alphanumeric-boundary regexes over title + description + experience. No taxonomy, no aliases, no canonical concepts |
| Free / Paid | Both |
| User impact | `banking` penalises any posting that mentions a banking client; `healthcare` penalises a health-tech product role. Users cannot predict the blast radius |
| Correct layer to fix | Wording (P2) — or a real sector model, which is a Search Engine V2 scope |

### B15 — Extra cities cannot add rows for the same posting
| | |
|---|---|
| Severity | Low (correct behaviour, misleading copy) |
| Root cause | `job_key` deliberately excludes location — [scraper.py:722](scraper.py#L722) |
| Free / Paid | Paid |
| User impact | The copy says each city is a separate paid search and the price moves with the count; it does not say a second city can cost twice as much and return the same rows for employers posting the same title in both |
| Correct layer to fix | Wording |

### B16 — `POST /estimate` accepts duplicate locations
| | |
|---|---|
| Severity | Low |
| Root cause | `_parse_chips` does not de-duplicate; the Alpine picker does, so the UI cannot produce it |
| Free / Paid | Paid |
| User impact | `Delhi, Delhi, Delhi` plans and bills three identical searches |
| Correct layer to fix | `_parse_chips` / `_configure_overrides` |

### B17 — Naukri ignores the Locations picker
| | |
|---|---|
| Severity | Low (latent — Naukri is disabled by default) |
| Root cause | `make_profile.render` overlays `locations` onto `SITES["linkedin"]` only |
| Free / Paid | Paid |
| User impact | If Naukri is ever enabled, it searches `Delhi / NCR` + `Remote` whatever the user picked |
| Correct layer to fix | Renderer |

### B18 — Naukri's source-side freshness rounds 14 up to 15 days
| | |
|---|---|
| Severity | Informational |
| Root cause | `_naukri_freshness` maps to the actor's enum `1/3/7/15/30` |
| Free / Paid | Paid |
| User impact | None in the output — `finalize()` re-applies the true window. It fetches one extra day of rows and pays for them |
| Correct layer to fix | None required; document |

### B19 — No cap on the number of skip terms
| | |
|---|---|
| Severity | Low |
| Root cause | `_parse_chips` validates each term and does not bound the count |
| Free / Paid | Both |
| User impact | 300 terms accepted → 300 compiled regexes scanned over every row's full description. Self-inflicted, session-scoped, but a real cost |
| Correct layer to fix | `_parse_chips` |

### B20 — Validation messages echo the raw input, contrary to `_FormError`'s docstring
| | |
|---|---|
| Severity | Informational |
| Root cause | Locations and skip-term messages interpolate the offending value |
| Free / Paid | Both |
| User impact | None — the JSON is rendered with Alpine `x-text`, so it is text, not markup. The docstring is simply wrong |
| Correct layer to fix | Docstring, or the messages |

---

## Proposed fixes — not implemented

### P0 — the setting does not work, or lies to the user

1. **B1** Render `min_comp_usd`'s stored value. One attribute. Until then, any
   user who navigates back loses their pay floor.
2. **B2** Drive the `max_age_days` `<option selected>` from state. One
   expression, and the pattern already exists elsewhere in this app.
3. **B10** Give the preferences form a real `POST /configure` and demote
   `/estimate` to pricing. This is the structural fix that makes B1 and B2
   impossible to reintroduce, and it is what makes the screen work without
   JavaScript.
4. **B3 / B4 / B6** Decide, per setting, between *wire it into the free path*
   and *do not show it in free mode*. Three controls are currently dead or
   degenerate in the mode most users will be in. This is a product decision
   and should not be made by whoever writes the patch.

### P1 — works incorrectly or inconsistently

5. **B7** Set `linkedin_remote_only = True` when the scope is `remote` and the
   picker has narrowed the locations. Currently the combination buys rows the
   pipeline is guaranteed to discard.
6. **B8** Make Indeed's `country` follow the scope, or refuse scope/location
   combinations Indeed cannot serve — the same fail-closed discipline LinkedIn
   geoIds already get.
7. **B9** Render the current skip terms and make the posted list authoritative,
   so a term can be removed.
8. **B11** Materialise the default scope into state when the profile is first
   written, so the screen and the plan agree before anyone touches anything.
9. **B12** Clear the preference keys on `POST /resume`, or keep them all
   deliberately and say so on screen. Not half each.
10. **B16 / B17** De-duplicate posted locations; overlay locations onto every
    site that accepts them, not only LinkedIn.

### P2 — wording and comprehension

11. **B14** Stop saying "industries" unless a sector model exists. Say what it
    does: match these words in the title or description and rank those jobs
    lower.
12. **Where you can work** — split the two ideas it currently conflates, or
    rename it to whichever one it will actually mean. Do not rename it until
    P0 item 4 is decided; the right words depend on that answer.
13. **Jobs per search** — if it stays visible in free mode, it needs different
    words there. If it is hidden, none are needed.
14. **B13** Make the disclosure badge report whether anything inside it is
    non-default.
15. **B15** Say that extra cities cost extra and may return the same postings.
16. **Minimum salary** — add that only about one job in sixteen states pay at
    all, so the count will barely move. The current copy is true and still
    sets the wrong expectation.
17. **Recency** — say that undated postings are kept.
18. **`_search_summary`** — do not show picked locations as "Where" in a mode
    that ignores them.

### P3 — enhancement

19. **B19** Cap the skip-term count.
20. **B18 / B20** Document Naukri's 15-day rounding; fix the `_FormError`
    docstring.
21. Offer more of the accepted recency range — the server takes 1–365 and the
    control offers three values.
22. Consider surfacing `drop_no_visa`, since "Onsite, anywhere in the world"
    already tells the user most of those roles need sponsorship and then does
    nothing about it.

---

## Appendix — how to re-run the measurements

Nothing here needs network access, an Apify key, or the Oracle worker.

```
# form → state → profile → effective config
python - <<'PY'
import sys, tempfile, types, copy
sys.path[:0] = [".", "auto-apply"]
from sweep import app as app_module
...  # POST /estimate against a test client, then exec() the written profile
PY

# post-fetch filters over controlled rows
python -c "import scraper; scraper.SETTINGS.update(max_age_days=7); print(len(scraper.finalize(rows)))"

# actor inputs
python -c "import scraper; print(scraper.build_input('linkedin', scraper.effective_search('linkedin', s)))"

# corpus coverage
python -c "import glob, json; ..."   # over output/*/jobs_*.json
```

The full probe scripts used for this audit were run from the session
scratchpad and are not committed — every number they produced is reproduced
inline above, with the code path it came from.
