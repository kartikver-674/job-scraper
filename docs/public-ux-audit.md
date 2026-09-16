# Sweep public beta — UX audit and redesign proposal

Written for: you, as the product owner deciding what to change before the next
beta cohort.

> **Status: implemented on `feat/public-ux-redesign`,** in five commits
> (`69fe679`, `2bc1549`, `e3d3ce0`, `8c037b4`, `f3bfbd5`). Where the build
> departed from this audit, the audit is annotated inline — search for
> **CORRECTED**. Two proposals were deliberately not shipped; see
> "What was not implemented" at the end.

## How this was audited

Not from the templates alone. `sweep/tests/test_worker_link.py` already knows
how to stand up a real public-mode Render app against a real Oracle worker on a
socket, so that harness was reused to serve the actual app on a port with
session state seeded per screen, and every screen was rendered in Chrome:

- desktop at 1440px
- mobile at **true 390px** via CDP `Emulation.setDeviceMetricsOverride`
  (plain headless Chrome clamps to 500px and silently crops — the first round
  of "mobile" shots were misleading for exactly this reason)
- a DOM probe measuring `documentElement.scrollWidth` vs `clientWidth` and
  listing every element extending past the viewport
- contrast ratios computed from the CSS custom properties
- public-mode route reachability checked by POSTing to each form action

The existing Stitch project (`projects/15915018717797936571`, "Sweep Metered
Job Search WebApp", 34 screens) was inspected. Its theme is dark, `customColor`
`#f0a22e`, surface `#03161c` — identical to `sweep/static/sweep.css`. The design
system is intact and matches what ships. It is also named
**"Precision Metered Instrument"**, and registered as **DESKTOP**. That name is
the whole diagnosis in three words: the UI is a precision instrument for an
operator metering spend, and the public visitor is a job seeker on a phone.

---

# Part 1 — The audit

## 1. Current public user journey

```
/beta      code gate
/          upload a PDF                     step 1 "Upload"
/review    (renders → calls the model)      step 2 "Review"
/review    check + edit + name profile      step 2
/key       free or paid                     step 3 "Free or paid"
/configure six panels of settings           step 4 "Configure"
/confirm   cost table                       step 5 "Confirm"
/run  →    /running  poll every 4s          step 6 "Running"
/results   filters, exports, job tables     step 7 "Results"
```

Seven numbered steps, rendered as a seven-item tracker in the header of every
screen. The steps are the routes. That is the first thing to change: the routes
are a correct engineering decomposition and a poor mental model.

Two further facts that shape everything below:

- The worker runs `MAX_ACTIVE = 1`. **One sweep at a time, globally.** With more
  than one beta visitor, queueing is the normal case, not the edge case.
- Runs are cleaned after `TTL_SECONDS = 48h`.

## 2. Where a first-time user hesitates

| # | Moment | Why they stall |
|---|---|---|
| 1 | `/beta` | "Sweep reads your résumé and works out which job titles to search for and how to weight your skills." Two of those three clauses are about the internals. Nothing says *jobs*. |
| 2 | Upload hero | "Job search you can **meter**. It **prices** every search before it runs." A job seeker did not come to meter anything. |
| 3 | Upload aside | "What the model reads out" showing `not read yet ×3`. An empty table of three unfamiliar nouns. |
| 4 | Upload tile 3 | "**Runs on this machine** — Sweep serves this page from your own terminal… the plan and the results stay in this folder." Flatly untrue on `sweep-beta.onrender.com`. The visitor is reading a claim they can see is false, on the trust-critical screen. |
| 5 | Review | The biggest thing on screen is a 1–5 skill-weight table. They came to check that Sweep understood them; they are handed a scoring console. |
| 6 | Review | "Name this profile" — empty required field, no default visible, "Saved as `profiles/<name>.py`". They do not know what a profile is, and nothing is saved to disk in public mode anyway. |
| 7 | Review CTA | "Looks right" — your own instinct here is correct. |
| 8 | `/key` | Two cards, two identical-weight buttons, no recommendation. |
| 9 | `/key` | A bare Apify token field, visible immediately, with no explanation of what Apify is. |
| 10 | `/key` | "Verify key" — verify and then what? The CTA does not name the outcome. |
| 11 | Configure | Six equal panels, ~450 words of muted prose, no titles or keywords on the screen that calls itself "Configure the sweep". |
| 12 | Configure | `*` footnote system: cost-affecting fields marked with an asterisk explained in a paragraph 2,400px below the first mark. |
| 13 | Confirm | Answers "what will this cost" and never "what is Sweep about to search for". |
| 14 | Running (free) | The default public path shows one indeterminate bar and a panel that says *"there is no per-search progress to show."* For up to 40 minutes. |
| 15 | Running | Nothing says the sweep survives closing the tab — even though it does. |
| 16 | Running | Nothing says they are queued behind another run — even though the worker reports it. |
| 17 | Results | Jobs begin below a filter panel, an export bar and (free path) a 55-word panel about a feature they cannot use. |
| 18 | Results | Scores are bare integers: `61`, `43`, `14`. No scale, no legend, no unit. |
| 19 | Results | "Source" column reads `ashby`, `lever`, `greenhouse`, `remoteok`, `workable`. |

## 3. Screens with too many simultaneous decisions

- **`/configure`** — 9 independent controls across 6 panels, all equal weight:
  3 source checkboxes, a 3-way scope radio, a multi-select location picker, a
  recency select, a depth stepper, a pay floor, a skip-terms field. A user can
  run an excellent sweep by touching **none** of them.
- **`/review`** — per-skill weight steppers (4 rows × 3 controls), per-skill
  remove checkboxes, an add-skills field with its own weight stepper, two
  experience steppers, and a required profile name. ~17 controls.
- **`/key`** — two mutually exclusive choices presented at identical weight,
  with the harder one's form already expanded.
- **`/results`** — 4 filter controls + a submit + 4 export links + (paid) a
  re-rank `<details>` containing the whole weight editor again, all above the
  jobs.

## 4. Internal / developer terms visible to the public user

Verified by grep against `sweep/templates/`, filtered to what is actually
reachable in public mode:

| Term | Where | Note |
|---|---|---|
| "Runs on this machine", "your own terminal", "this folder" | `upload.html:163-166` | **False in public mode.** Not gated on `public_mode`. |
| "Saved as `profiles/<name>.py`" | `review.html:121` | **False** — `public.py`'s `keep_profile` deliberately never writes. |
| "Reads the shortlist already on **your disk**" | `results.html:252` | False — it is Render's memory. |
| "**The terminal running Sweep** will say more" | `running.html:152` | A public visitor has no terminal. |
| "The detail is in the terminal running Sweep", `python scraper.py --profile X --dry-run` | `plan_error.html:17-20` | Same. |
| "Excel export needs the **openpyxl** package. Run `pip install -r requirements.txt`" | `app.py:2024` | A visitor cannot pip install on Render. |
| "an **Apify actor** already returned", "no **actor runs**" | `results.html:263,281` | Scraping infrastructure. |
| `.env` | `results.html:325` | The operator's file. |
| lowercase site keys `linkedin` / `indeed` / `naukri` | `configure.html:93`, `confirm.html:59` | Every other screen writes them properly via `site_label()`. |
| `ashby`, `lever`, `greenhouse`, `remoteok`, `workable` | `results.html:68` | Raw source keys as a user-facing column. |
| "sweep", "profile", "plan", "combo", "depth", "rate basis", "per-run minimum", "score" | throughout | Engine vocabulary. |
| "1 searches" | `confirm.html:60` | Plural bug. |

### 4b. Three dead ends the new public paid path opens

Reproduced against the running app. A public visitor who pastes their own Apify
key has `free_only == False`, which un-gates template branches written for the
operator:

```
GET /results (public, paid)  → renders <form action="/rescore">
GET /running (public, paid)  → renders <form action="/second-key">
POST /rescore    → 404  "Not available in the public beta."  (plain text)
POST /second-key → 404  "Not available in the public beta."  (plain text)
```

`confirm.html`'s `plan.over_cap` branch carries a third `/second-key` form on
the same basis. These are not styled pages — they are `text/plain` 404 bodies
with no navigation. A paying visitor whose credit runs mid-sweep is offered
"Add this key and continue" and gets a blank plain-text refusal.

## 5. Ambiguous CTAs

| Current | Problem |
|---|---|
| "Looks right" | Names a judgement, not an action or a destination. |
| "Verify key" | Terminal-sounding. Does not say it continues the journey. |
| "Search the free sources" | "Sources" is our word. Competes visually with "Verify key". |
| "Review spend plan" | On the free path it becomes "Review the sweep" — vaguer still. |
| "Run the sweep — $1.02" | Actually good. Keep the pattern. |
| "Update the list" | For filters. Fine but occupies primary real estate. |
| "Read my résumé" | Good. Keep. |
| "See the results" / "See what it found" | Two labels for the same destination. |

## 6. Information hierarchy problems

1. **The 7-step tracker is the most persistent element in the app** and it
   describes the backend decomposition. `/key` ("Free or paid") and `/confirm`
   are not steps a user thinks in.
2. **`/review`: the wrong column is dominant.** "What it read" — the actual
   subject of the screen — is a narrow left column. The right two-thirds is the
   weight table plus two explanatory callouts.
3. **`/configure`: no panel is more important than any other.** The single most
   consequential control (Sources, which is the only lever that meaningfully
   moves cost and breadth) has the same visual weight as "Skip these".
4. **`/configure`: the cost sidebar is the point of the screen and is last on
   mobile.** Measured page height at 390px: **2848px**. The live estimate and
   the CTA sit at the bottom, ~2600px below the controls that change them.
5. **`/confirm`: two thirds of the desktop viewport is empty**, and the cost
   table occupies the prime column while the decision (the button) is a narrow
   sidebar.
6. **`/running` (paid): a search-combination grid is the largest element.**
   Cells labelled `BEN`, `GUR`, `RI` grouped under `LINKEDIN` / `INDEED`.
7. **`/results`: jobs start below three competing blocks.** On mobile the first
   job row is ~730px down.
8. **`.primary` and `.secondary` are nearly the same size** — 13px, padding
   `0.75rem 1.5rem` vs `0.75rem 1rem`. The only difference is fill vs outline.
   There is no "this is the one button" treatment anywhere in the system.

## 7. Controls that should move behind progressive disclosure

| Control | Currently | Should be |
|---|---|---|
| Apify token field | expanded on arrival at `/key` | behind "Use my own Apify key" |
| Skill weights table (1–5) | dominant panel on `/review` | inside "Adjust how jobs are scored" |
| Add skills the parse missed | always visible | same section |
| "Terms that push a listing down" | its own panel | same section |
| Penalty/commodity callouts | two stacked callouts | one line inside that section |
| Profile name field | required, always visible | pre-filled, inside advanced (or removed publicly — see §11) |
| Results per search (depth) | prime position with 3 paragraphs | "Advanced search settings" |
| Pay floor | own panel | Advanced |
| Skip these / down-rank terms | own panel | Advanced |
| How recent | own panel row | Advanced (default 14 days) |
| Per-site source checkboxes | first panel on `/configure` | keep, but as one "Where we search" choice, not three raw toggles |
| Search-combination grid | dominant on `/running` | "Show search detail" disclosure |
| Filters | panel above the jobs | compact toolbar / drawer |
| Exports | 4 buttons above the jobs | one "Export" menu, secondary |
| Re-rank editor | `<details>`, above the jobs | below the jobs, and **not rendered at all in public mode** |

## 8. Pages that could *feel* combined (backend routes unchanged)

- **`/review` + `/key`** → one "Here's what we understood — how should we
  search?" screen. Route `/review` keeps its POST; `/key` keeps its POST. The
  free/paid choice becomes the bottom section of the review page, so the
  default public journey is Upload → Review → Results.
- **`/configure` + `/confirm`** → one "Search preferences" screen whose sticky
  footer carries the live summary and the single start button. `/confirm`
  survives as the paid-only interstitial (money deserves its own screen); the
  free path skips it entirely.
- **`/running` + `/results`** → the same screen, progressively filling. The
  results table is already fed by the same `read_rows`. Running becomes
  "Results, still arriving".

That reduces the *felt* journey to four:

```
Résumé  →  Your profile  →  Searching  →  Jobs
```

## 9. Components to extract and reuse

| New shared partial | Replaces |
|---|---|
| `_journey.html` | the 7-step `<nav class="tracker">` in `base.html` |
| `_search_summary.html` — titles, locations, scope, sources as chips | ad-hoc fragments on review / confirm / running / results; **currently nowhere shows all four together** |
| `_match.html` — the score, rendered as a band | `results.html:69` `.score`, `running.html:274` `.feed-score`, `exports.as_html` |
| `_job_card.html` | the 9-column `<tr>` in `listings_table`, so mobile can render cards and desktop rows from one source |
| `_disclosure.html` — a labelled `<details>` with a consistent summary | five hand-rolled `<details>` / callout patterns |
| `_source_chip.html` — `site_label()` for *all* sites, not just paid | `results.html:68`, `running.html`, `configure.html:93` |
| `_state.html` — a one-shape empty/error panel (what happened / is my data safe / what to do) | `.empty`, `.error`, `.callout`, `.banner`, `plan_error.html` |

`site_label()` already exists in `sweep/logic.py:239` and only covers the three
paid boards. Extending its map to `greenhouse → Greenhouse`,
`lever → Lever`, `remoteok → RemoteOK`, `ashby → Ashby`, `workable → Workable`
is a two-line change that fixes the raw keys on three screens.

## 10. Components to visually demote

- The step tracker (to 4 compact stages).
- The skill-weight table (`_weights.html`) on `/review`.
- The cost line-item table on `/confirm` (the total and the button are the
  decision; the breakdown is supporting detail).
- The search-combination grid on `/running`.
- Filters and exports on `/results`.
- Every `muted` explanatory paragraph on `/configure` — currently ~450 words.

## 11. Components to remove (in public mode)

- `upload.html`'s "Runs on this machine" tile — **false**.
- `review.html`'s "Saved as `profiles/<name>.py`" helper, and arguably the
  profile-name field itself: in public mode the name only labels an export
  filename. Auto-name it from the résumé and drop the required field.
- `results.html`'s re-rank `<details>` when `public_mode` — it posts to a route
  that 404s.
- `running.html`'s and `confirm.html`'s `/second-key` forms when `public_mode`
  — same.
- The `openpyxl` / `pip install` error text in public mode.
- "Re-ranking needs a paid sweep" on the free results page — a 55-word
  explanation of a feature the reader cannot reach, above their jobs.
- The dead live-cost meter machinery on `/configure` in public mode:
  `base.html` renders the meter only `{% if not public_mode %}`, so
  `configure.html`'s `meter_numeral_bind` / `meter_fill_bind` / `sweepCount`
  blocks never paint. A public paid visitor gets **no live cost readout at
  all** except the sidebar. Either wire the sidebar as the live element or
  enable the meter publicly — but do not keep both dead and duplicated.

## 12. Loading / error / empty-state audit

The *messages* are mostly well written. The gaps are coverage and framing.

| State | Today | Verdict |
|---|---|---|
| Résumé parse failure | `deriving.html` + "The model call failed… an exhausted API quota, a key that no longer works, or a scanned PDF" | Message OK; **mentions the operator's terminal in the catch-all**, and lists three causes the user cannot distinguish |
| Not a PDF / too large | client-side pre-check + server 413 | **Good. Keep.** |
| Invalid Apify key | `check_token` error on `/key` | OK |
| Key with insufficient balance | `plan.over_cap` panel + ack checkbox | Thorough for the operator; for a visitor it is 5 headings and 3 paragraphs on a money screen. Also carries a dead `/second-key` form |
| **Worker busy / queued** | **nothing** | `queue_position` is returned by the worker (`sweep_worker.py:588`) and is **read nowhere in `sweep/`**. With `MAX_ACTIVE=1` this is the common case |
| Sweep interrupted | 4 named endings on `/running` | **Genuinely good. Keep.** |
| Oracle restart / Render redeploy | `worker_link.rehydrate` | Works, and is invisible — no reassurance is shown |
| Paid run needs key again | `WorkerError` → back to `/key` with a reason | Reasonable, but lands on the fork screen rather than a "resume your sweep" screen |
| Network timeout to worker | "the sweep worker did not answer" → 502 on `/key` | Leaks the word "worker" |
| **No jobs found** | "0 listings match" → "Nothing was found for this profile yet" + "Clear every filter" | **Wrong for a genuine zero-result sweep** — there are no filters to clear, and "for this profile" is jargon |
| Very few jobs found | nothing | No state. A 3-job sweep looks like a broken one |
| **Expired run / TTL cleaned (48h)** | `RunNotFound` → `WorkerError` handler → `key.html` with "The sweep service is not available just now: that sweep is not available." | **Bad.** Dumps the user on the free/paid fork with a message that suggests an outage, not an expiry |
| Export failure | openpyxl `ImportError` → 503 with pip instructions | Impossible advice publicly |
| Modal/inference unavailable | `InferenceError` → "The local model could not be reached" | "**local**" is wrong publicly — it is on Modal |

Nothing renders a bare "Something went wrong". Credit where due. But five states
have no screen at all, and four give instructions only an operator can follow.

## 13. Mobile

**The good news, measured, not assumed:** at a true 390px every screen reports
`scrollWidth == clientWidth == 390`. There is **no horizontal page overflow
anywhere**. The `.listings` table is properly contained
(`overflow:auto; contain:paint` — the comment at `sweep.css:370` records this
being fixed deliberately, and it holds). The tracker collapses to
"4 of 7 — ④ Configure — ⑤ Confirm". **This responsive work should be preserved,
not redone.**

The real mobile problems are length, order, and target size:

| Screen | Height at 390px | Problem |
|---|---|---|
| `/configure` | **2848px** | ~7 screenfuls. Cost + CTA are last. |
| `/results` | 2548px | First job ~730px down. |
| `/review` | 2150px | Weight table before the summary. |
| `/running` (free) | 900px | Almost nothing on it. |

Plus:

- **Touch targets below 44×44px**, all measured from the CSS:
  `.stepper button` 32×~34 (the main editing control on `/review`);
  `.remove > span` 28×28; `.pick-x` ~13×13 (removing a location chip);
  `.keyx` similar. `button.primary` ≈ 37px tall.
- **`button.primary { align-self: flex-start }`** — the primary action is never
  full-width, so on a 390px column it is a small button floating left.
- **`.listings { max-height: 70vh; overflow: auto }`** creates a nested scroll
  region inside a scrolling page. On touch this traps the scroll.
- **Base font 13px**, `.meta` 11px, `.label` 10px, `.listings td` 12px. The
  muted 11–12px paragraphs that carry most of `/configure` are hard work on a
  phone. Inputs inherit 13px, which triggers iOS Safari's zoom-on-focus.

## 14. Accessibility

**Already good, keep:** `:focus-visible` is defined on `a, button, input,
select, summary` plus `:focus-within` on `.choice` and `.drop`
(`sweep.css:467`). `aria-current="step"` is correct and the comment at
`base.html:83` records why it is template text rather than an interpolation.
Locked steps omit `href` rather than using `disabled`. `visually-hidden` labels
exist on every icon-only control. Steppers carry real `aria-label`s naming the
term. The `costs()` asterisk is `aria-hidden` with a spoken alternative. The
row-click on `/results` deliberately adds no tab stop because the `Apply` anchor
is the keyboard path.

**Contrast failures**, computed from the tokens:

| Token | On `--ground` | On `--surface` | WCAG |
|---|---|---|---|
| `--chalk #d2e6ee` | 14.35:1 | — | pass |
| `--warm #d7c3af` | 10.85:1 | — | pass |
| `--slate #9f8e7b` | 5.84:1 | 5.18:1 | pass |
| **`--dim #524435`** | **1.97:1** | **1.75:1** | **fail** |

`--dim` is used for **`input::placeholder`** (`sweep.css:293`) — so every
placeholder in the app (`No floor`, `Salesforce, CRM, .NET`,
`kubernetes, terraform, grpc`, the results search box) is effectively
invisible — and for `.step.locked` text and markers, which is how the tracker
renders steps not yet reached.

**Other issues:**

- `configure.html:164` — `<div class="picker-box" tabindex="0" role="button">`
  contains real `<button>` chips. Interactive descendants inside `role="button"`
  are invalid and confuse screen readers. The CSS comment acknowledges the
  `<button>`-can't-nest-`<button>` constraint but resolves it the wrong way.
- The location menu has no `role="listbox"`, no arrow-key navigation, no
  Escape-to-close, and no focus return. `@click.outside` closes it for a mouse
  only.
- `review.html:119` — the profile name `<input>` is required but has no
  `aria-describedby` linking it to its help text or its clash warning.
- Disabled submit buttons rely on `opacity: .5` plus `:disabled`; the
  "Saving the profile…" status is a `<span>`, not an `aria-live` region, so
  screen-reader users get no announcement that the form was accepted.
- `/running` updates counts and banners entirely without `aria-live`. A blind
  user polling a 40-minute sweep is told nothing.

## 15. Trust and friction

**Working well:** "Nothing is charged yet" on upload; "Used for your sweep, and
not saved… never written to a file, a cookie, or a log" on `/key`; the cost
shown before the button; the four honest sweep endings; "not known" rather than
a fabricated `$0.00`. The refusal to invent a number is a real asset — do not
lose it.

**Undermining trust:**

1. **"Runs on this machine… stays in this folder"** on the landing screen, on a
   hosted site. One visibly false claim discounts every true one beside it.
2. **The Apify key field is visible before any explanation of what Apify is.**
   Asking for a credential for an unnamed third party is the highest-friction
   moment in the product and it currently arrives with no setup.
3. **No mention that the sweep survives the tab closing** — a true, reassuring,
   and completely absent fact.
4. **No queue transparency** — a visitor waiting behind someone else's sweep
   sees an indeterminate bar, indistinguishable from a hang.
5. **Nothing marks where AI is used.** The model reads the résumé and derives
   titles, weights and penalty terms. The word "model" appears; the fact that
   these are *guesses to be checked* is only implied by "Correct bad guesses".
6. `deriving.html` auto-submits a POST on page load, which is right, but pairs
   with "Reading your résumé" and no cancel. On a Modal cold start that is
   ~150s of a bar with no exit.

## 16. What must not change

Per your constraint, and because the audit found them sound:

- The palette, type scale and Stitch design system in `sweep/static/sweep.css`.
- The containment work (`contain: paint`, `min-width: 0`, the `.listings`
  scroll box) — it is correct and load-bearing.
- `:focus-visible` coverage and the `visually-hidden` labelling discipline.
- The four named sweep endings on `/running`.
- "not known" instead of a fabricated zero, everywhere.
- The `_weights.html` single-macro rule (review and results cannot drift).
- The single-quoted `|tojson` convention in Alpine attributes.
- The no-CDN policy on the key screen.
- Client-side PDF type/size pre-check.
- The drag-and-drop upload panel.

---

# Part 2 — The proposed design

## 17. Public user mental model

> I gave Sweep my résumé.
> It told me what it understood.
> I chose how wide to search.
> It found jobs while I waited.
> Here are my matches.

Four nouns, not seven verbs.

## 18. Proposed information architecture

```
Résumé            →  Profile             →  Searching          →  Jobs
(/, /review GET)     (/review, /key)        (/configure*, /confirm*,   (/results)
                                             /run, /running)
```

The journey indicator shows **four** stages. Routes are unchanged. `step_states`
in `sweep/logic.py:162` already returns whatever list it is given, and
`app.config["STEPS"]` is already overridable — so this is a config change plus a
mapping from 7 slugs to 4 stages, not a rewrite.

```python
# sweep/logic.py — public journey
PUBLIC_STAGES = [
    ("upload",    "Résumé",   {"upload"}),
    ("review",    "Profile",  {"review", "key"}),
    ("configure", "Search",   {"configure", "confirm", "running"}),
    ("results",   "Jobs",     {"results"}),
]
```

## 19 & 20. Screen-by-screen — headings, CTAs, and the case for each

### `/beta` — the door

**Current problem.** "Sweep is in private beta" then "Sweep reads your résumé
and works out which job titles to search for and how to weight your skills."
The second half of that sentence is about scoring internals.

**User impact.** The first sentence a stranger reads is about the machine, not
about them.

**Proposed change.**

> **H1** Sweep is in private beta
> **Lede** Upload your résumé and Sweep searches job boards for roles that
> actually match it — then ranks them, so the best ones are at the top.
> **Field label** Your beta code
> **CTA** Enter

**Why better.** Leads with the outcome. Keeps the honest gating. One sentence,
no internals.

---

### `/` — Résumé

**Current problem.** Hero says "Job search you can meter… prices every search
before it runs." A "What the model reads out" panel shows three `not read yet`
rows. A third tile claims the app runs on the visitor's own machine.

**User impact.** The value proposition is the operator's (cost control), not the
visitor's (finding jobs). One tile is provably false, on the first screen.

**Proposed change.**

> **H1** Find jobs that match your résumé
> **Lede** Upload your résumé. Sweep reads it, works out what you should be
> searching for, and searches job boards for you.
> **Drop zone** Drop your résumé here, or choose a PDF *(unchanged)*
> **Reassurance** Free to try. Nothing is charged, and your résumé is not
> stored after this session.
> **CTA** Read my résumé *(unchanged — it is good)*

Three tiles become one honest strip:

> **Free sources, always** Company career pages and public job feeds are
> searched on every sweep, at no cost.
> **Bigger search, optional** LinkedIn, Indeed and Naukri can be added using
> your own Apify account.
> **Ranked against you** Every job is scored on how well it matches your skills
> and experience — best matches first.

Drop the `not read yet` panel on a first visit; show it only when
`read` is populated (a returning session), titled "Last time, Sweep read".

**Why better.** The screen now answers "what is this, and what do I get" before
"what does it cost". The false claim is gone. The empty panel no longer
introduces three unfamiliar nouns with no values.

---

### `/review` — Profile *(the highest-value change)*

**Current problem.** The screen's own subject — what Sweep understood — occupies
a narrow left column. Two thirds of the width is a 1–5 skill-weight table under
two explanatory callouts. The CTA is "Looks right". Locations and search scope,
which are part of "what Sweep understood about me", are not on this screen at
all — they appear two screens later.

**User impact.** The user cannot tell at a glance whether Sweep got them right,
which is the one job of this screen. They are asked to tune a scoring model
before they have confirmed the basics.

**Proposed change.** Summary first, editing behind disclosure.

> **H1** Here's what Sweep understood about you
> **Lede** Read from **ada-okonkwo-cv.pdf**. Check it over — this is what your
> job search will be based on.

Four summary cards, each with a quiet **Edit** affordance:

| Card | Content | Edit reveals |
|---|---|---|
| **You** | "Full-stack React Native developer · 2 years' experience" | the years/months steppers |
| **Roles to search for** | `React Native Developer` `Full Stack Engineer` chips | add/remove titles |
| **Your strongest skills** | top 5 by weight as chips, "+3 more" | the full weight editor |
| **Where you want to work** | scope + locations *(moved forward from `/configure`)* | scope radio + location picker |

Then one disclosure, closed by default:

> **Adjust how jobs are scored** *(optional)* — containing the 1–5 weight table,
> "add skills the parse missed", the low-signal notice, and the penalty terms.
> Summary line: "Sweep weights 4 skills. `javascript` and `git` are set aside as
> too common to be useful."

Then the free/paid choice as the final section of this same screen (see below),
so the journey is Résumé → Profile → Jobs.

> **Primary CTA** Continue to job search
> **Secondary** Start over with a different résumé *(unchanged, stays a link)*

Remove the profile-name field in public mode; auto-name from the résumé and use
it only for the export filename.

**Why better.** The mental model becomes "here's what we understood" instead of
"configure the scorer". Everything needed to verify is visible in about three
seconds; everything needed to tune is one click away. Moving locations here
means the user confirms *who they are and where they'll work* in one place,
which is how a person thinks about a job search.

**Stitch:** worth a prototype. Four summary cards with inline edit is the one
genuinely new layout in this redesign, and the existing Stitch project already
holds the tokens.

---

### `/key` — the free/paid choice

**Current problem.** Two cards at equal weight, two buttons in the same style,
and the Apify token field already expanded. Nothing says what Apify is.

**User impact.** No recommended path; the harder option looks equally endorsed;
the credential ask arrives cold.

**Proposed change.** One dominant choice, one disclosed alternative. As a
section of `/review` on desktop, or its own screen on mobile — the POST targets
(`/key/free`, `/key`) do not change.

> **H2** How wide should Sweep search?
>
> **▸ Free Sweep** *(recommended, visually dominant)*
> Searches company career pages and public job feeds. No account needed, and
> nothing to pay.
> **CTA (large, full-width on mobile)** Start Free Sweep
>
> Below, as a quiet secondary row:
> **Want LinkedIn, Indeed and Naukri too?** → *Use my own Apify key*

Expanding that reveals:

> **Full Sweep**
> Sweep can also search LinkedIn, Indeed and Naukri. Those boards are read
> through **Apify**, a service that runs the searches for you and bills per
> search — so this uses your own free Apify account, not ours. A typical sweep
> costs **under $1**, and Sweep shows you the exact figure before anything runs.
>
> **Your Apify API token** [field]
> **Your key is used for this sweep and never saved** — not to a file, a cookie,
> or a log. Starting another sweep later means pasting it again.
> **Where to find it** Sign in at console.apify.com → Settings → Integrations.
> Checking your balance is free.
> **CTA** Check my key and continue

**Why better.** The zero-friction path becomes the visually obvious default.
Apify is explained in two sentences, in terms of *what it does for you* and *who
pays* — no actors, no credits, no rates. The key field only appears once someone
has opted into needing it. The CTA names the outcome ("and continue") instead of
ending at "Verify".

**Exact microcopy for Apify** (use verbatim, these are the four facts that
matter and no more):

> Apify is a service that runs job-board searches. Sweep uses **your own** free
> Apify account for LinkedIn, Indeed and Naukri, so the searches are billed to
> you, never to us — and you see the cost before anything runs.

> Your Apify key is used for this Sweep and is never saved.

---

### `/configure` — Search preferences

**Current problem.** Six equal panels, nine controls, ~450 words of muted
explanation, an asterisk-footnote system spanning 2,400px, raw lowercase site
keys, and a cost sidebar that is the point of the screen and last on mobile.

**User impact.** A screen no one should *need* to visit reads as mandatory
configuration, and the one number that matters is hardest to see.

**Proposed change.** Reframe and collapse.

> **H1** Search preferences
> **Lede** These are already set up from your résumé. Change anything you like,
> or go straight to searching.

Visible (three things only):

1. **Where we'll search** — LinkedIn / Indeed / Naukri as named, properly-cased
   toggles with a price beside each; free sources shown as an always-on chip
   row. (Free path: a single sentence, no toggles.)
2. **Where you can work** — the scope radio, with the prose cut to one line:
   "Remote roles come from the free sources at no cost."
3. **Locations** — the picker, unchanged, minus the paragraph.

Behind one disclosure:

> **Advanced search settings** — how recent (14 days), results per search,
> minimum pay, skills or stacks to avoid. Summary line when untouched:
> "Using Sweep's defaults — last 14 days, 15 results per search."

Replace the `*` footnote system with a per-control inline hint on the three
controls that actually move cost: `Affects cost`.

Sticky footer (mobile) / sticky sidebar (desktop):

> Estimated cost **$1.02** · 11 searches
> **CTA** Review and start →

**Why better.** A user can run a good sweep by reading two lines and pressing
one button. The controls that exist for the operator's precision still exist,
one click away. The cost is always on screen next to the button that commits to
it — which is the whole reason this screen has a live estimate.

---

### `/confirm` — what Sweep is about to do

**Current problem.** The heading is "Confirm the spend" and the content is a
billing table. Nothing on the screen says *what will be searched*.

**User impact.** The last screen before a 40-minute commitment does not let the
user check the thing most likely to be wrong — the titles and locations.

**Proposed change.** Answer "what is about to happen", then the money.

> **H1** Ready to search
>
> **Searching for**
> React Native Developer · Full Stack Engineer
> **Where**
> Bengaluru · Gurgaon · Remote (India)
> **Sources**
> LinkedIn · Indeed · Naukri · 6 free job boards
> **Takes about** 40 minutes — you can close this tab and come back
>
> **Estimated cost $1.02**
> Billed to your own Apify account. Sweep stops at $1.28 whatever happens.
>
> **CTA (dominant)** Start Sweep — $1.02
> **Secondary** Change search preferences

Free path: identical structure, `Estimated cost` block replaced by a single
line — "Free — no account and no charge" — and the CTA becomes **Start Free
Sweep**. Skip `/confirm` altogether on the free path if the summary is already
in `/configure`'s sticky footer.

**Why better.** It becomes a confirmation of *intent*, which is what a
confirmation screen is for, with cost as one line of it rather than the subject.
The tab-closing reassurance appears before the wait, not during it.

---

### `/running` — Searching

**Current problem (free path — the default public journey).** One indeterminate
bar and a panel reading "there is no per-search progress to show". **Nothing
about queue position, and nothing about closing the tab.**

> **CORRECTED.** This audit originally said the "Listings arriving" feed was
> gated out by a template accident and "works" on the free path. It does not.
> `scraper.py` calls `fetch_free()` once and emits a single checkpoint after
> it, so a free sweep produces no rows until the end — which is exactly why
> `live_feed()` returns early on that path. A live feed there would sit empty
> for the whole run and then flash the lot, and a "0 jobs found so far"
> headline would be the bar's own dishonesty with a number on it. What
> shipped instead: the queue position, an evidenced stage line, the
> tab-closing reassurance, and the count and feed only once there is
> something to count.

**Current problem (paid path).** The dominant element is a grid of cells reading
`BEN GUR RI` under `LINKEDIN` / `INDEED` — a backend job matrix.

**User impact.** The free visitor cannot tell a working sweep from a hung one.
With `MAX_ACTIVE = 1` they may simply be queued, and are told nothing.

**Proposed change.** One screen for both paths, built from facts already in
`snapshot()`.

> **H1** Sweep is searching for you
> **Stage** (one of, derived from existing state)
> — You're in the queue — one other sweep is running. You're **next**.
> — Preparing your search
> — Searching job sources
> — Checking openings
> — Ranking your matches
> — Preparing your results
>
> **184 jobs found so far** ← the headline figure (`p.found`, already computed)
>
> **This usually takes about 40 minutes.** You don't need to keep this page
> open — Sweep carries on, and your results will be here when you come back.
>
> **Latest matches** *(the existing feed — enabled on the free path too)*
> **Show search detail ▸** *(the combination grid, paid only, collapsed)*
> **Stop the sweep** *(secondary, unchanged)*

**Why better.** The screen reports the one number a job seeker cares about
instead of the number an operator cares about. Queueing stops reading as a hang.
The tab-closing fact, which is already true, finally gets said. The grid stays
for anyone who wants it.

**Backend:** this is the only change that needs worker plumbing — see §24.

---

### `/results` — Jobs

**Current problem.** "40 listings kept". Above the first job: a four-control
filter panel, a four-button export bar, and (free) a 55-word panel about
re-ranking, which the reader cannot use. Scores are bare integers. Sources are
raw keys. First job is ~730px down on mobile.

**User impact.** The payoff screen opens with administration. The score — the
entire product claim — is unexplained.

**Proposed change.**

> **H1** 40 jobs matched your profile
> **Lede** Best matches first. 32 you can work in India, 8 that would need a
> visa.

Then, immediately, the jobs. Filters collapse into a compact toolbar
(`Sort: Best match ▾` · `Filter ▾` · `Export ▾`) that sits flush above the list
and never exceeds one row.

**Score presentation** — this is presentation only, no scoring logic changes:

| Raw | Shown as |
|---|---|
| ≥ 50 | **Excellent match** |
| 30–49 | **Strong match** |
| 15–29 | **Good match** |
| < 15 | **Possible match** |

> **CORRECTED — not shipped.** Those thresholds were eyeballed from one
> sample shortlist, and a band is a claim about a distribution. The screen
> ships the raw number, the column header `Match`, a legend in the lede and
> a title on every cell. `docs/match-bands.md` records the analysis that
> would earn the bands — including the real possibility that the answer is a
> percentile of each sweep rather than an absolute table, or no bands at all.

with the raw number kept as a small secondary figure and a one-line legend:
"Match strength is how well the job's requirements line up with the skills and
experience on your résumé." Do **not** render it as a percentage — it is not one,
and claiming otherwise would be the same class of dishonesty the codebase
already refuses elsewhere ("not known" over a fabricated `$0.00`).

Job rows become cards on mobile (title, company, location, match band, matched
skills, **Apply**) and stay a table on desktop. Make the whole card a real link
target on mobile rather than relying on the row-click script.

Remove `max-height: 70vh` from `.listings` so the page scrolls once.

**Why better.** Jobs dominate. The score becomes meaningful without changing a
line of scoring code. The apply action becomes reachable on a phone.

---

## 21. Microcopy for unfamiliar concepts — the full replacement set

| Instead of | Say |
|---|---|
| "Configure the sweep" | "Search preferences" |
| "Looks right" | "Continue to job search" |
| "Run the sweep" / "Run" | "Start Sweep" / "Start Free Sweep" |
| "Free or paid" (tracker) | "Search" |
| "How far should this sweep go?" | "How wide should Sweep search?" |
| "Search the free sources" | "Start Free Sweep" |
| "Verify key" | "Check my key and continue" |
| "Review spend plan" | "Review and start" |
| "Confirm the spend" | "Ready to search" |
| "40 listings kept" | "40 jobs matched your profile" |
| "Update the list" | "Apply filters" |
| "Score 43" | "Strong match · 43" |
| "source_site: greenhouse" | "Greenhouse" |
| "Results per search" | "How many jobs per search" *(Advanced)* |
| "Skip these / down-rank" | "Skills or industries to avoid" |
| "Pay floor" | "Minimum salary" |
| "Terms that push a listing down" | "Counted against a match" |
| "Skill weights, 1 to 5" | "How your skills are weighted" |
| "The terminal running Sweep will say more" | "Sweep couldn't finish this search. Everything it found is saved — open your results, or start a new sweep." |
| "The local model could not be reached" | "Sweep couldn't read your résumé just now. Nothing was charged. Try again in a minute." |
| "Nothing was found for this profile yet" | "This sweep didn't find any matching jobs. Try widening where you can work, or add more job titles." |
| "that sweep is not available" (expired) | "This sweep has expired. Sweeps are kept for 48 hours. Start a new one — your résumé is still loaded." |
| "Excel export needs the openpyxl package…" | "Excel export isn't available right now. CSV and JSON work — or open the web page version." |

## 22. What stays exactly as it is

`sweep/static/sweep.css` tokens, type ramp and the Stitch design system; the
containment/overflow work; `:focus-visible` coverage; `visually-hidden`
labelling; the drop-zone upload panel with its client-side pre-check; the four
named sweep endings; "not known" over a fabricated zero; the single `_weights.html`
macro; the single-quoted `|tojson` Alpine convention; no CDN on the key screen;
`deriving.html`'s plain-script auto-submit (it exists because Alpine was too
slow on that one screen — do not convert it).

## 23. Where Stitch earns its keep

Use it for three things and nothing else:

1. **The `/review` summary cards** — the one genuinely new layout. Worth two or
   three variants before committing.
2. **The mobile job card** for `/results` — the row→card transformation needs a
   visual answer for which of the nine columns survive.
3. **The searching screen** — the stage indicator + headline count + latest
   matches, at both widths.

Do **not** use Stitch for: the journey indicator (it is a config change plus
CSS), any copy decision, the progressive-disclosure structure, or `/confirm`
(it is a summary list — markup, not design). The existing project is registered
`DESKTOP`; add a mobile device type before prototyping the job card.

## 24. Backend changes genuinely needed

Only two, both small, neither touching scraping, scoring, credentials, session
isolation, Modal, or free/paid semantics:

1. **Surface queue position.** `deploy/sweep_worker.py:588` already returns
   `queue_position` in the run status. `sweep/worker_link.py` drops it on the
   floor. Carry it through `rehydrate`/`_status` into `snapshot()` so
   `/running` can say "one other sweep is running — you're next". Read-only,
   no worker change at all.

2. **A found-count and a stage on the free path.** `snapshot()` already
   computes `p["found"]` and `p["latest"]` via `live_feed()`. The free branch
   returns early *before* the parts of the screen that would use them are
   rendered, because the template gates the whole right-hand column on
   `free_only`. Fix in the template, not the backend. A coarse stage
   (`preparing` / `searching` / `ranking`) would need the worker to report a
   phase — **defer this**; derive the stage from what already exists
   (`elapsed`, `found`, `done/planned`, `free_running`) until it proves
   insufficient.

Nice-to-have, not required: distinguish `RunNotFound` caused by TTL expiry from
"not yours", so the expired-run copy can be accurate. The worker deliberately
conflates them for security reasons — leave it, and word the message to cover
both ("this sweep is no longer available — sweeps are kept for 48 hours").

## 25. Risks

| Risk | Mitigation |
|---|---|
| **Local console regression.** Public and local share every template; `free_only` and `public_mode` branches are already load-bearing. | Gate every public-only change on `public_mode`, never on `free_only`. `test_public_sweep.py` already asserts both directions — extend it rather than adding new tests. |
| **Progressive disclosure hides something the operator needs daily.** | Disclosures default open in local mode, closed in public mode. One template flag. |
| **Match bands mislead.** A "Good match" that isn't is worse than an unexplained 27. | Keep the raw number beside the band. Sample real sweeps to set thresholds before shipping — the bands above are a starting point, not measured. |
| **Merging `/review` and `/key` breaks `step_states`.** | `AFTER_REVIEW_ENDPOINT` already exists as the seam. Change the mapping, not the guards — `step_states` reads the same facts the guards do, and the docstring at `logic.py:162` explains why that invariant matters. |
| **Removing the profile-name field.** | Public only. Local mode still writes `profiles/<name>.py` and must keep the field and the clash check. |
| **A large copy pass introduces a claim that isn't true.** This codebase's strongest quality is that it does not overclaim. | Every new sentence must name a fact in the code. The three false claims found here ("runs on this machine", "saved as profiles/*.py", "on your disk") got in the same way. |
| **Sticky mobile footer covers content.** | `padding-block-end` on the scroll container equal to the footer height. |
| **Scope creep into a visual redesign.** | Phase 1 changes no colours, no fonts, and no spacing tokens. |

---

# Part 3 — Implementation plan

Four phases. Each is independently shippable and independently revertable.
Phase 1 alone fixes most of the beta feedback.

## Phase 0 — Truth and dead ends (half a day)

No layout change. These are defects, not design.

| # | Change | File |
|---|---|---|
| 0.1 | Delete the "Runs on this machine" tile in public mode | `upload.html:162-167` |
| 0.2 | Drop "Saved as `profiles/<name>.py`" in public mode | `review.html:121` |
| 0.3 | Drop "on your disk" from the export note in public mode | `results.html:252` |
| 0.4 | Gate the re-rank `<details>` on `not public_mode` | `results.html:271` |
| 0.5 | Gate both `/second-key` forms on `not public_mode` | `running.html:324`, `confirm.html:104` |
| 0.6 | Public-safe text for the derive catch-all, `plan_error.html`, the `halted` banner, and the openpyxl 503 | `app.py`, `plan_error.html`, `running.html:152` |
| 0.7 | Extend `SITE_LABELS` to the free sources | `logic.py:237` |
| 0.8 | Use `site_label()` in configure, confirm and the results source column | 3 templates |
| 0.9 | Fix "1 searches" | `confirm.html:60` |
| 0.10 | Lift `input::placeholder` off `--dim`; lift `.step.locked` to `--slate` | `sweep.css:293,157` |

**Done when:** grep for `terminal|this machine|on disk|\.env|profiles/|pip install`
over the public-reachable templates returns nothing; POSTing to `/rescore` and
`/second-key` is unreachable from any rendered public page.

## Phase 1 — Navigation, hierarchy, copy, CTAs, disclosure (3–4 days)

The bulk of the perceived improvement.

| # | Change |
|---|---|
| 1.1 | Four-stage journey indicator; `PUBLIC_STAGES` mapping 7 slugs → 4 |
| 1.2 | `/review` restructured: summary cards first, weight editor behind "Adjust how jobs are scored", locations moved forward from `/configure` |
| 1.3 | `/review` CTA → **Continue to job search**; drop the public profile-name field |
| 1.4 | `/key`: Free Sweep dominant; Apify behind "Use my own Apify key"; new Apify microcopy; CTA → **Check my key and continue** |
| 1.5 | `/configure` → "Search preferences": three visible controls, everything else in "Advanced search settings"; prose cut from ~450 words to ~80; asterisk footnote replaced by inline `Affects cost` |
| 1.6 | `/confirm` → "Ready to search": searching-for / where / sources / duration, then cost, then one dominant CTA |
| 1.7 | `/results`: headline "40 jobs matched your profile"; filters and exports collapse into one toolbar above the list; re-rank moved below the jobs (local only) |
| 1.8 | Match bands on `/results` and in the running feed |
| 1.9 | Full copy pass against the §21 table |
| 1.10 | A `.primary` size/weight step so one button per screen is unmistakably the action |

**Done when:** every public screen passes the five-second test — where am I,
what has Sweep done, what do I do, what will the button do, what's next — and
each has exactly one dominant action.

## Phase 2 — Components and responsive (2–3 days)

| # | Change |
|---|---|
| 2.1 | Extract `_journey`, `_search_summary`, `_match`, `_job_card`, `_source_chip`, `_state` |
| 2.2 | `/results` renders cards below 48rem, table above, from one partial |
| 2.3 | Remove `.listings { max-height: 70vh }`; one scroll per page |
| 2.4 | Sticky cost+CTA footer on `/configure` and `/confirm` at mobile widths |
| 2.5 | Touch targets to 44×44: `.stepper button`, `.remove`, `.pick-x`, `.keyx` |
| 2.6 | `button.primary` full-width below 48rem (drop `align-self: flex-start` there) |
| 2.7 | Inputs to 16px on mobile to stop iOS zoom-on-focus |
| 2.8 | Rebuild the location picker as a proper `role="listbox"` with arrow keys, Escape and focus return — or replace it with a plain `<select multiple>` styled to match, which is smaller and correct by default |

**Done when:** at 390px `/configure` is under ~1600px tall, `/results` shows a
job above the fold, and every interactive target measures ≥44px.

## Phase 3 — Searching screen, states, accessibility (2–3 days)

| # | Change |
|---|---|
| 3.1 | Carry `queue_position` through `worker_link` into `snapshot()` |
| 3.2 | Rebuild `/running`: stage line, headline found-count, "you can close this tab", latest-matches feed **on the free path too**, grid behind a disclosure |
| 3.3 | Real states for: queued, no jobs found, very few jobs found, expired run (48h), worker unreachable, key no longer held — each answering what happened / is my data safe / do I need to do anything / what next |
| 3.4 | `aria-live="polite"` on the running counts and on form-submitting status text |
| 3.5 | `aria-describedby` wiring on inputs with help text |
| 3.6 | Full keyboard pass on every public screen |
| 3.7 | Re-measure contrast; no public token below 4.5:1 for text |

**Done when:** every row in the §12 table has a designed screen, and a keyboard
+ screen-reader run of the whole journey completes without a dead end.

## Phase 4 — Polish (1 day)

Motion on stage transitions, the empty/loading shimmer for the job list, the
"very few results" nudge copy, and a final read-through of every public string.

---

## Suggested order of approval

Phase 0 is defect-fixing and needs no design sign-off — it can ship now.
Phases 1–4 need your approval on:

1. the four-stage journey and the 7→4 mapping;
2. the `/review` summary-card structure (Stitch prototype first, if you want);
3. the match-band thresholds, which should be set from real sweep data rather
   than the placeholders above;
4. whether the public profile-name field goes away entirely.

---

# What was not implemented, and why

| Proposed | Outcome |
|---|---|
| Match bands (Excellent / Strong / Good / Possible) | **Not shipped.** Thresholds were not measured. Raw score plus a legend shipped instead; `docs/match-bands.md` holds the analysis task. |
| Merging `/review` and `/key` into one screen | **Not shipped, by decision.** Keeping them apart finishes one mental task ("did Sweep understand me?") before starting another ("how should it search?"). |
| Moving locations/scope into the Profile summary | **Not shipped, by decision.** Where you want to work describes *this search*, not the candidate — it stays in Search preferences. |
| Extracting `_journey`, `_match`, `_job_card`, `_state` partials | **Partly.** `_search_summary.html` was extracted and `site_label()` centralised. The job card is CSS over the existing row rather than a second template, which is less markup and cannot drift. The journey is a config table (`PUBLIC_STAGES`), not a partial. |
| A coarse worker-reported phase (`preparing`/`searching`/`ranking`) | **Not shipped.** The stage line is derived from facts already in `snapshot()`; asking the worker for a phase is a backend change that has not proved necessary. |

## Known remainder

- The weight-number inputs inside the skills table measure 26px wide on a
  phone (44px tall, with 44×44 steppers either side). They sit inside a
  horizontally-scrolling table, behind a disclosure, on the public path.
- `auto_profile_name` falls back to `"sweep"` when the résumé's name does not
  transliterate, so an export is then `sweep-sweep-<date>.csv`.
