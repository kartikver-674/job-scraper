# Location / scope UI correctness

Three reported problems on the Search Preferences screen, fixed together
because they are the same seam: the work scope and the location picker were
two controls answering one question without agreeing about it.

**Not deployed.** No new environment variable, no backend semantics changed.
`work_scope` filtering, paid search planning, the Indeed country mapping and
Search Engine V2 are all untouched.

| | |
|---|---|
| Baseline | `5224d12` |
| Diff | 4 source files, 2 test files, +531 −104 |
| Tests | **2,070 pass** — sweep 883, auto-apply 1,102, bench 43, deploy 42 |

---

## 1. Root cause of the checkbox lag

Reported as: click United States, the chip appears but the tick does not;
click United Kingdom, and *then* the United States tick appears.

The menu options were:

```html
<input type="checkbox" :checked="has('United States')"
       @click.prevent="toggle('United States')">
```

A checkbox toggles itself as part of its own activation, and
`preventDefault()` **cancels that activation, restoring the checkedness the
box had before the click**. So `@click.prevent` asks the browser to undo the
tick at the same time as the handler is changing the application state that
`:checked` is bound to — and the browser's restore can land after Alpine has
applied the binding.

The result on screen was a box showing the state from **before its own
click**, catching up only on the next interaction — because changing `picked`
re-runs `:checked` for *every* option, including one whose own restore had
already happened. That matches the report exactly: the chip updates, the tick
does not, and the previous tick appears on the following click.

**The precise ordering is not instrumented here**, and this document does not
claim one: no browser was driven and no timing was captured. What is
established is the mechanism — a cancelled activation that rewrites the same
property the binding writes — and that removing it removes the symptom's
cause.

**Nothing was wrong with the state.** `picked` was always correct, which is
why the chip, the count and the submitted value were always right. Only the
checkbox — the one thing the browser also writes to — was wrong.

### The fix

`@change`, not `@click.prevent`. `change` fires after the browser has
committed the tick, so there is nothing left to undo and the binding and the
box agree on the same interaction.

```html
<input type="checkbox" :value="name" :checked="has(name)"
       @change.stop="toggle(name)">
```

`.stop` keeps the native `change` off the form, where the live-cost handler
would have priced the estimate against a hidden input Alpine had not written
yet. `priced()` re-dispatches on the next tick instead, which is the same
guard the picker already had.

The one place that still needs a nudge is the all-locations row, because its
state is "nothing picked" and it cannot be unticked directly — you leave it
by choosing a place:

```html
<input type="checkbox" :checked="!picked.length" @change.stop="all($el)">
```

`all()` clears the array and forces the box back on.

---

## 2. One source of truth

`picked` — a single array of place names — now lives on the **form's** Alpine
scope, next to `scope`. Everything the user can see or submit is derived from
it, and nothing else stores a selection:

| | reads |
|---|---|
| checkbox tick | `:checked="has(name)"` |
| chips | `x-for="name in picked"` |
| count on the closed control | `x-text="picked.length + …"` |
| posted form value | `:value="picked.join(', ')"` |
| "Anywhere in India" / "Everywhere" tick | `:checked="!picked.length"` |
| which options are offered | `offered()` → filters `groups` by `scope` |

There is no `selected`, `checked`, `chips` or `formLocations` beside it —
`test_every_view_of_the_selection_reads_the_same_array` asserts both halves
of that: that each view reads `picked`, and that no second store exists.

It moved from the picker's own `x-data` to the form's because the option list
depends on `scope`, which the radios own. A child scope reaching up for a
parent's property works in Alpine, but it is a subtlety this screen does not
need, and one scope means one place to look.

**One press is one toggle.** The row is a `<label>`, which already forwards a
press to its own input, so a handler on the row as well would fire twice.
There is none — `test_one_press_is_one_toggle` asserts the menu carries no
`@click` at all.

---

## 3. Scope / location option matrix

Locations **narrow** the chosen answer and may never widen it.

| scope | option groups offered | all-row label | picker |
|---|---|---|---|
| **Onsite or hybrid, in India** | India — 7 cities: Delhi, Gurgaon, Chandigarh, Bengaluru, Hyderabad, Pune, Mumbai | **Anywhere in India** | shown |
| **Remote roles** | *none* | — | **hidden** |
| **Onsite or hybrid, anywhere in the world** | India **+** Countries (21 verified geographies) | **Everywhere** | shown |

Two different sets are in play under the India answer, and they are not the
same size — see §10, which is the review that found it:

| | contents |
|---|---|
| the picker's 7 offers (`_INDIA_CITY_NAMES`) | + Chandigarh |
| the paid **retrieval plan** (`_SCOPE["india"]["locations"]`) | 6 cities, no Chandigarh |
| the **universe** — what counts as India post-fetch (`HOME_LOCATION_HINTS`) | every Indian spelling config knows |

A city is offered as a narrowing of the **universe**, not of the retrieval
plan. That is the same shape "Onsite or hybrid, anywhere in the world" has
had all along: its plan is nine countries, none of them India, and picking
Bengaluru under it is still a narrowing of "anywhere".

One table, `sweep.logic.location_groups()`, drives the menu **and** the
server's validation, so the two cannot offer different things:

```python
[{"heading": "India",     "scopes": ["india", "global"], "names": [...7 cities]},
 {"heading": "Countries", "scopes": ["global"],          "names": [...21 countries]}]
```

`location_groups(scope)` filters it; `allowed_locations(scope)` flattens that
into what the validator will accept. `location_groups(None)` is the full
superset — the one the unknown-name check uses, so a typo still earns the
money-protecting 400 rather than being quietly narrowed away.

### "Anywhere in India" / "Everywhere" is the empty list

Not a stored value, not a flag. It is `picked == []`, which makes it mutually
exclusive with every individual pick **by construction** — there is no second
piece of state that could disagree. Picking a city makes `!picked.length`
false and the row unticks itself; ticking the row clears the array and every
city unticks. Nothing downstream ever sees the word.

### A note on what is in the page source

The picker renders its own menu (Alpine `x-for`), because the options have to
change when the scope radio changes and a round trip would lose every other
unsaved edit on the screen. So the **full** group table is in the page as
data, and the picker filters it.

Under "onsite or hybrid, in India" no country is rendered as an option row —
`test_india_offers_no_foreign_country` asserts that — but "United States" does
appear once in the page source, inside that JSON. It is not selectable, and
posting it anyway is **dropped** by the server, not refused with an error
(§5 has the reasoning, and §4's measured table shows the result). A name the
geoId table does not know at all is the one that still earns a hard 400.
Worth knowing before reading the HTML and concluding otherwise.

---

## 4. Scope-transition behaviour

Client-side the moment the radio changes (`$watch('scope', () => narrow())`),
and server-side on every submit. The server is the authority; the client is
there so the screen never shows a chip that is about to be discarded.

| from | to | selections | result |
|---|---|---|---|
| Worldwide + US + UK | India | both foreign | **dropped** → back to all of India |
| Worldwide + Bengaluru + US | India | mixed | **Bengaluru kept**, US dropped |
| India + Bengaluru | Worldwide | Indian city | **kept** — still valid there |
| any + anything | Remote | anything | **cleared** |
| India + Bengaluru | India | — | unchanged |

Measured end to end through `POST /configure`:

```
India + United States (forged)     -> locations = the six India cities
India + Bengaluru, United States   -> locations = ['Bengaluru']
Remote + Bengaluru (forged)        -> locations = ['Remote']
Global + United States             -> locations = ['United States']
Global + Bengaluru                 -> locations = ['Bengaluru']
India + Atlantis (unknown name)    -> HTTP 400, nothing applied
```

---

## 5. Server-side validation

`_configure_overrides` does three things to a posted location list, in order:

1. **Parse and de-duplicate** (unchanged) — `Delhi, Delhi` is one Delhi.
2. **Refuse an unverified name** (unchanged) — a name outside
   `config.LINKEDIN_GEO_IDS` is a 400 with the geoId message, because that is
   the one that bills for the wrong country.
3. **Drop anything this scope does not offer** (new) —
   `[p for p in picked if p != REMOTE and p in allowed_locations(scope)]`.

**Dropped, not refused, and deliberately.** A mismatch here is a stale page, a
forged post, or a no-JS visitor whose hidden field still carries the last
render's picks — and that last one would be stranded by a 400, because
without JavaScript the picker cannot be edited at all (the checkboxes carry no
`name`; only the hidden field posts). What survives is shown back on the very
next screen, so nothing disappears silently from the user's point of view.

An unverified name keeps its hard refusal because the two cases are different:
narrowing is for a name the table *knows* and this scope does not offer;
a name nobody verified is still the money bug it always was.
`test_an_unverified_name_is_still_refused_not_narrowed` pins the distinction.

`_picked_locations()` filters the same way on the way out, so a pick left in
state by an older build cannot come back as a chip.

---

## 6. India is the default

`sweep.logic.DEFAULT_SCOPE = "india"`, and the radio is first on the screen.
One name, read by the radio order, by `ensure_scope()`, by `current_scope()`
and by the form validator, so they cannot drift.

**It is a default, not an override.** `ensure_scope()` writes it only when no
scope has been committed:

```python
if not app.state.get("scope"):
    app.state["scope"] = DEFAULT_SCOPE
    app.state.update(_SCOPE[DEFAULT_SCOPE])
```

So a stored Remote or Worldwide choice survives every reload, every visit to
/confirm and back, and every unrelated preference change —
`test_a_stored_remote_choice_is_not_overwritten`,
`test_a_stored_worldwide_choice_is_not_overwritten` and
`test_changing_an_unrelated_preference_leaves_the_scope_alone`.

No stored values changed and no migration is needed: the identifiers are
still `india` / `remote` / `global`, and a session already holding one keeps
it.

---

## 7. Tests

`sweep/tests/test_search_prefs.py` — 63 tests, 24 of them new here.

| # | case from the brief | test |
|---|---|---|
| 1 | fresh session defaults to India | `test_a_fresh_session_starts_on_india` · `test_india_is_the_first_answer_on_the_screen` · `test_the_default_is_what_the_first_profile_is_written_with` |
| 2 | stored Remote not overwritten | `test_a_stored_remote_choice_is_not_overwritten` |
| 3 | stored Worldwide not overwritten | `test_a_stored_worldwide_choice_is_not_overwritten` |
| 4 | India exposes Indian cities only | `test_india_offers_indian_cities_only` |
| 5 | India exposes no foreign countries | `test_india_offers_no_foreign_country` |
| 6 | Worldwide exposes the countries | `test_worldwide_offers_the_countries_and_the_cities` |
| 7 | Remote hides the picker | `test_remote_offers_nothing_and_hides_the_picker` |
| 8 | Worldwide → India removes foreign | `test_worldwide_to_india_drops_the_foreign_picks` · `…keeps_an_indian_city` |
| 9 | India → Remote clears locations | `test_any_scope_to_remote_clears_the_locations` |
| 10 | India → Worldwide keeps the city | `test_india_to_worldwide_keeps_the_indian_city` |
| 11/12 | all-row exclusive with individual picks | `test_the_empty_picker_means_the_whole_scope` · `test_the_all_row_is_named_for_the_scope` |
| 13 | forged India + US sanitised | `test_a_forged_india_plus_united_states_is_sanitised` |
| 14 | forged Remote + Bengaluru dropped | `test_a_forged_remote_plus_bengaluru_drops_bengaluru` |
| 15 | checkbox updates on the same interaction | `test_no_option_cancels_its_own_click` |
| 16 | one state behind every view | `test_every_view_of_the_selection_reads_the_same_array` |
| 17 | no double toggle from the row | `test_one_press_is_one_toggle` |
| 18 | back navigation restores the selection | `test_back_navigation_restores_the_exact_selection` |

**15 and 17 are structural, and that is a real limit.** The failure is what a
browser does with an Alpine binding, and there is no browser in this suite.
They assert the *property that made it possible* — a submit-time undo racing a
reactive write, and a second handler on the row — rather than the symptom.
A rendered check appearing late cannot be caught here; a `@click.prevent`
coming back can.

### Mutation-checked

Every new guard was verified to fail on its own bug before being kept — the
same discipline that caught a vacuous test in the previous patch:

```
reintroduce @click.prevent on the options   -> TestOneSourceOfTruthForLocations FAILED (2)
offer Countries under scope "india"         -> TestLocationsNarrowTheScope      FAILED (2)
drop the server-side scope narrowing        -> TestScopeTransitions             FAILED (4)
set DEFAULT_SCOPE back to "remote"          -> TestIndiaIsTheDefault            FAILED (2)
restore all four                            -> OK
```

### Existing tests updated

Five, all encoding the previous defaults rather than a contract that still
holds:

* `test_an_unsubmitted_scope_still_materialises_the_default` — asserted
  `"remote"`; now reads `DEFAULT_SCOPE` and checks the India cities and
  `work_scope` reach the profile.
* `test_only_verified_locations_are_offered` — scraped option rows out of the
  HTML. The menu is Alpine-rendered now, so it asserts on the table that
  drives both the menu and the validator, and that the page hands that table
  over verbatim.
* `test_picking_locations_narrows_both_lists` and
  `test_the_current_pick_comes_back_into_the_control` — used `Delhi, Germany`
  under `india`, which is now exactly the combination that gets narrowed.
  Moved to `global`, where it is a legitimate pick.
* `test_an_untouched_screen_and_a_touched_one_plan_the_same_sweep` — the test
  helper's `form()` defaulted to `scope="remote"` while the app now defaults
  to India, so "touched with no change" was changing the scope. The helper
  reads `DEFAULT_SCOPE` now.

One fixture changed rather than a test: `TestAvoidTerms` builds
worldwide-remote rows, so it posts `scope="remote"` explicitly — under the
India default the arrangement filter would drop both rows before any penalty
could be read off their order.

### Suites

```
sweep          883  OK      (+24)
auto-apply    1102  OK
bench           43  OK
deploy          42  OK
                     total 2,070
scraper --demo, python -m sources — green
```

---

## 8. Manual check — not done

No browser was driven. The checkbox fix is the one change in this patch whose
correctness is a browser behaviour, and the structural tests cannot confirm
the tick now appears on the same click — only that the mechanism that
prevented it is gone.

Worth walking through before this goes anywhere:

**Desktop, India (the default)**
- India is first and already selected; the picker reads "Anywhere in India"
- only Indian cities in the menu, no group heading (there is only one group)
- click Bengaluru → tick and chip appear **on that click**
- click Hyderabad → both ticks stay, count reads "2 locations"
- untick Bengaluru → chip goes, "Anywhere in India" does not re-tick until
  the last city is gone

**Worldwide**
- switch to Worldwide → "India" and "Countries" headings both appear
- Bengaluru stays selected
- add United Kingdom and Canada → each ticks immediately, count reads 3
- switch back to India → the two foreign chips vanish, Bengaluru remains

**Remote**
- the whole Locations panel disappears
- switch back → no stale chips

**Mobile** — the picker CSS is top-level, untouched by any media query
(verified: brace depth 0 at `.picker-group`), and the only new rule is
`display: contents` on the group wrapper, which exists so the wrapper does
not become a flex item and break the menu's column gap.

---

## 9. What did not change

* `work_scope` post-fetch filtering, `LOCATION_HINTS`, the Indeed country
  mapping, paid search planning, `_SCOPE`'s own contents, Search Engine V2.
* Stored values: `india` / `remote` / `global` are the same identifiers.
* The no-JS contract: the form still submits, the server still validates, and
  the picker is still inert without Alpine — as it always was, since its
  checkboxes carry no `name` and only the hidden field posts.
* `REMOTE` is still accepted by `allowed_locations()` though never offered,
  so a page loaded before it left the menu does not 400 on a control its
  reader cannot see.

---

## 10. Review of `84b0d27` — the Chandigarh gap

A pre-deploy review asked whether "7 cities offered" and "the six India
cities" in §4's measured output were the same set. **They are not, and it was
not a documentation typo.** One real defect, fixed here.

### The questions, answered from the code

1. **What does `_SCOPE["india"]` represent?** Three different things, and the
   distinction is the whole of this finding:
   * `locations` / `linkedin_locations` = `["Delhi", "Gurgaon", "Bengaluru",
     "Hyderabad", "Pune", "Mumbai"]` — **six**. This is the paid *retrieval
     plan*: which geoIds LinkedIn and Indeed are asked for.
   * `location_hints` = `LOCATION_MATCH["India"]`, which is
     `HOME_LOCATION_HINTS` — the free sources' place filter.
   * `work_scope = "india"` — makes `finalize()` apply
     `scraper.in_home_country()` to **every** row, paid and free.

   The *universe* "Anywhere in India" means is the last two. The six cities
   are a cost decision about where to spend, not the boundary of the answer.

2. **What does `location_groups("india")` offer?** **Seven**: the six above
   **plus Chandigarh** (`_INDIA_CITY_NAMES`, a separate presentation list).

3. **With `picked == []`, what reaches the pipeline?** `SEARCH.locations` and
   `SITES.linkedin.locations` = the six cities; `LOCATION_HINTS` = the India
   vocabulary; `SETTINGS.work_scope = "india"`. Indeed's country is `IN` for
   every one of them.

4. **Is every offered city inside the broad universe?** It was **not**.

5. **Chandigarh specifically:** it was in neither the retrieval plan nor
   `HOME_LOCATION_HINTS`. Being outside the *plan* is fine — picking a city
   redirects retrieval, exactly as under the worldwide answer. Being outside
   `HOME_LOCATION_HINTS` is the bug: `work_scope="india"` filters on
   `in_home_country()`, so **narrowing to Chandigarh filtered out
   Chandigarh's own rows.**

6. **Verdict: a correctness issue, not a doc typo.** Introduced by
   `work_scope` in `c9c7ef1` — before that no post-fetch India test existed
   and the city worked.

### How much it cost, measured

Not all of it: most Chandigarh rows spell out "India" and survived on that
word alone. The ones that do not are what was lost.

```
                  matched by the city filter   dropped by the India filter
chandigarh                  27                            4
mohali                      15                            2
thane                        8                            0
panchkula / blr / secunderabad / bombay   0                0
                                                total     6   of 7,132 rows
```

Six rows — `'Chandigarh'`, `'Chandigarh, Chandigarh'`, `'Mohali, Punjab'`.
Small, silent, and precisely the rows a user asking for Chandigarh wanted.

The same check found four more fragments with the same shape:
`blr`, `secunderabad`, `bombay`, `thane` — offered by a city, not recognised
as India. None had cost anything yet, because no row happened to use them
without also saying "India".

### The fix

Two data changes in `config.py`. `_SCOPE` is still untouched, so the paid
plan and the cost of "Anywhere in India" are unchanged.

```python
HOME_LOCATION_HINTS += ["chandigarh", "mohali", "panchkula",
                        "secunderabad", "bombay", "thane"]
LOCATION_MATCH["Bengaluru"] -= ["blr"]
```

`blr` went the other way: it was added unverified in the earlier patch,
matches nothing in 7,132 real rows, and a three-letter airport code is not a
spelling worth widening a *country* test for.

Because `LOCATION_MATCH["India"]` **is** `HOME_LOCATION_HINTS`, one edit
fixes both the free-path hints for "Anywhere in India" and the post-fetch
India test.

**Blast radius, measured:** rows counted as India go 3,303 → 3,309 of 7,132
— exactly the six, and the only newly recognised strings are `'Chandigarh'`,
`'Chandigarh, Chandigarh'` and `'Mohali, Punjab'`. `HOME_LOCATION_HINTS` also
feeds the `hires_home` board signal, which becomes correspondingly more
correct: an employer posting in Chandigarh does hire in India.

### The regression test

`test_every_offered_india_city_is_inside_india` — for every name the picker
offers under the India scope, every fragment in `LOCATION_MATCH` for it must
satisfy `in_home_country()`. Structural, so it fails for the **next** city
added without its spellings, not only for the one that was wrong.

`test_narrowing_to_chandigarh_keeps_chandigarh_rows` drives the same thing
end to end through `POST /configure` and `finalize()`, on the spelling that
carries no "India" of its own — and asserts the broad answer covers those
rows too, since a narrowing may not reach rows its own scope would reject.

Both were checked against the pre-fix config:

```
revert HOME_LOCATION_HINTS  -> in_home_country('chandigarh') is False — narrowing
                               to it would filter its own rows out
                            -> kept ['Full'] != ['Bare', 'Full', 'Mohali']
restore                     -> OK
```

### Documentation corrected in the same pass

* §3 said posting "United States" under India is "refused". It is
  **dropped/sanitised**; only an *unverified* name is a hard 400. §5 and the
  tests were already right; §3 was not.
* §1 asserted a specific microtask-versus-cancelled-activation ordering and
  called it a race. No browser was instrumented, so that claim is withdrawn.
  The documented cause is now the mechanism alone: `@click.prevent` cancels
  the checkbox's native activation and can restore the browser's checkedness
  after the application state has changed.
* §3's matrix now states all three India sets side by side, so "7 offered"
  and "6 in the plan" cannot read as a contradiction again.

---

## 11. The picker froze the page — and what finally caught it

Reported after `5df73c1`: "when I open the location accordion it completely
freezes the webpage, I can't click anything else and just scroll."

### Root cause

`priced()` ended in `this.$dispatch("change")`.

**Alpine binds `$dispatch` to the element whose EXPRESSION is running**, not
to the `x-data` root. `priced()` is reached from a checkbox's own
`@change.stop="toggle(name)"` — so `$dispatch` fired a `change` **at that
same checkbox**, which re-entered that same handler, which called `priced()`
again.

`.stop` is no defence: it stops *propagation*, not the listener on the
*target*. Measured in Chrome — **one click produced 3,001 `change` events**
and the main thread never came back. Scrolling still worked because it is
compositor-driven, which is exactly what was reported.

It fires at the hidden `locations` field now, which listens for nothing, so
the event only travels up to the form that re-prices. The depth stepper has
always dispatched this way, from its own input, for the same reason.

```html
<input type="hidden" name="locations" x-ref="locationsField" :value="…">
```
```js
priced() {
  this.$nextTick(() => this.$refs.locationsField.dispatchEvent(
    new Event("change", { bubbles: true })));
}
```

### A second bug, made while fixing the first

The first fix carried the word `checkbox's` in its explanatory comment.
`x-data` is **single-quoted**, so that apostrophe closed the attribute and
truncated every method after it. The page threw no error; the menu simply
rendered its one all-locations row and nothing else. The file's own header
has warned about this class since it was written, and the repo already had
`alpine_scope()` — a helper that reads `x-data` through a real HTML parser —
because the same trap had been hit before.

### How it was found

Neither bug produces a Python failure, and §7 admitted as much: "15 and 17
are structural, and that is a real limit."

So a real browser was driven — Chrome via `puppeteer-core`, loading the
actual rendered page with the actual Alpine build. The freeze reproduced on
the first click inside the open menu; a counter on
`EventTarget.prototype.dispatchEvent` turned it into a number and a stack.

### `tools/ui_smoke.py`

That harness is now a committed tool rather than a throwaway. It renders
`/configure` for each scope through the real Flask client, drives Chrome, and
asserts on the things only a browser can answer:

* the menu renders the expected number of rows (a truncated `x-data`
  renders one);
* each option ticks **on its own click**;
* the tick, the chips, the count and the posted value all agree;
* **exactly one `change` event per click** — the storm, as a number;
* the all-locations row clears every pick and stays ticked;
* no page errors.

It is deliberately **not** in any suite and adds no repo dependency: it needs
Chrome and `puppeteer-core`, and says how to get them if they are missing.

```
python tools/ui_smoke.py                 # both scopes
SWEEP_NODE_MODULES=/tmp/sweep-ui python tools/ui_smoke.py --scope india
```

Both bugs were re-introduced to confirm the tool fails on them:

```
$dispatch restored      -> FAIL MAIN THREAD BLOCKED: reading after Delhi
apostrophe restored     -> FAIL menu rendered 1 rows, expected 8
                              (a truncated x-data renders only the all-locations row)
restored                -> ui smoke ok
```

### Python regressions added

Cheap guards for the same two mistakes, so an ordinary test run catches them:

* `test_nothing_dispatches_an_event_at_something_listening_for_it` — no
  `$dispatch(` in the scope, the dispatch goes to `$refs.locationsField`, and
  that field carries no `@change` of its own.
* `test_the_picker_scope_survives_an_html_parser` — reads `x-data` through
  `HTMLParser`, asserts all eight methods survived, that the attribute holds
  no apostrophe, and that its braces balance.

Both mutation-checked.

### Measured after the fix, in Chrome

```
global: 29 rows   india: 8 rows
  Delhi / Gurgaon / Chandigarh: tick, chips, count and value agree; 1 change event
  all-locations row: clears every pick and stays ticked
  page errors: none
```

This also settles, by observation rather than by structure, the three things
§7 could only assert indirectly: the checkbox updates on the same
interaction (15), every view derives from one array (16), and one press is
one toggle (17).

### Four tests updated

`test_every_view_of_the_selection_reads_the_same_array`,
`test_the_estimate_is_still_asked_after_the_field_is_written` (both files)
and `test_it_posts_one_field_not_a_repeated_one` matched the hidden input's
old attribute order or the old `$dispatch` call. Each was re-pointed at the
new markup with its intent unchanged.

Suites after: **sweep 887, auto-apply 1,102, bench 43, deploy 42 — 2,074.**
