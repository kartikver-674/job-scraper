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

A checkbox toggles itself *before* the click event is dispatched — the HTML
spec's **pre-click activation steps** — and `preventDefault()` runs the
**canceled activation steps**, which restore the checkedness to what it was
before the click. Those run *after* event dispatch finishes.

Alpine flushes reactive effects on a microtask, and a microtask checkpoint
happens when each listener returns. So the order was:

```
1. browser ticks the box                     checked = true
2. @click.prevent fires, toggle() runs       picked gains "United States"
3. microtask: Alpine's :checked effect       checked = true   (already was)
4. canceled activation steps                 checked = FALSE  ← undone here
```

Every box therefore displayed the state from **before its own click**. It
only caught up on the next interaction, because changing `picked` re-ran
`:checked` for *every* option — including the one whose own revert had
already happened. Hence "one interaction behind", and hence "sometimes":
step 3 and step 4 race, and which wins varies.

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
| **Onsite or hybrid, in India** | India (Delhi, Gurgaon, Chandigarh, Bengaluru, Hyderabad, Pune, Mumbai) | **Anywhere in India** | shown |
| **Remote roles** | *none* | — | **hidden** |
| **Onsite or hybrid, anywhere in the world** | India **+** Countries (21 verified geographies) | **Everywhere** | shown |

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
posting it is refused (below). Worth knowing before reading the HTML and
concluding otherwise.

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
