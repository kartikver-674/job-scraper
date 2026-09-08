# Stitch prompt — job-scraper web UI

Paste everything between the rules into Stitch. The **Motion spec** section at the
end is deliberately outside the paste: Stitch outputs static screens, so GSAP work
happens after, against the screens it returns.

---

Design a web app called **Sweep**. It runs a paid job search on someone's behalf:
they upload a résumé, it derives what to search for, they configure how wide to
cast the net, and then it spends real money — their own scraping-API credit — to
buy job listings. A full sweep takes about 40 minutes and costs between $1 and
$10 depending on how they configure it.

The product is not a job board. It is a **metered instrument**, closer to an
electricity meter or a taxi meter than to a careers site. Every screen answers one
question: *what am I about to spend, and what did I actually get for it?* Users are
technically confident job seekers who bring their own API key and care about not
wasting credit.

## The one memorable element: the meter rail

A persistent **meter** runs across every screen — a horizontal rail pinned below
the header, full-bleed, about 8px tall, with the committed spend filling it from
the left against a hard cap marker.

This is the app's signature and the only place boldness is spent. It is not
decoration and never a generic progress bar:

- On the configuration screen it moves **live** as the user toggles options, before
  anything is committed. Turning on a second country visibly pushes the fill right.
- Above the rail, one large number: the current estimate, e.g. `$2.94`. Below it,
  small plain text: `56 searches · 8 keywords across 7 locations`.
- The cap marker is a thin vertical tick with the cap value beside it. Past it, the
  fill changes colour and the number does too.

Everything else on every screen stays quiet and disciplined so the rail carries the
personality.

## Colour: colour means money

The interface is monochrome. Saturated colour is reserved and each hue means
exactly one thing, so a user can read cost from colour alone:

- `#0F1A1E` — ground. Deep slate-teal, deliberately not a tinted black.
- `#18262B` — raised surfaces: panels, table header, input fields.
- `#E8EDEB` — primary text, soft chalk rather than pure white.
- `#7E9199` — secondary text, labels, disabled states, rules.
- `#F0A22E` — **paid spend only.** The meter fill, cost figures, anything billable.
- `#4FA8A0` — **free sources only.** The public job feeds that cost nothing.
- `#D8543F` — **over the cap only.** Nothing else in the product is ever red.

That paid-versus-free split is the real distinction in this product, so it must be
legible everywhere: a row sourced from a free feed carries a teal marker, a paid
row an amber one. Never use amber or teal for ordinary emphasis, hover, or focus —
neutral tones handle all of that.

## Typography

Two families, both from Google Fonts:

- **Bricolage Grotesque** — headlines and the large meter numerals. Use its width
  and weight range as an active part of the design; the estimate figure should be
  set large, tight, and heavy enough to read as an instrument readout.
- **Instrument Sans** — all UI text, form labels, table content. Use its tabular
  figures for every number in a table or a live counter so digits don't shift width
  as they change.

Set a clear scale. Body text stays under 80 characters per line. Do **not** use
tracked-out all-caps labels, do not put an eyebrow label above every heading, do
not join metadata with middle dots, and do not append arrows to button text.

## Screens

### 1. Upload résumé

The entry point. A single wide drop zone, centered, on the ground colour. The
headline states the trade plainly — something like "Point it at your résumé" — with
one line beneath explaining that the résumé decides what gets searched for, and
nothing is spent yet.

The meter rail is present but empty, with `$0.00` above it. Establishing it as
empty here is what makes it meaningful later.

Accepts a PDF by drag or file picker. Show the accepted format and size limit as
quiet text inside the zone, not as a tooltip. After a file lands, the zone
collapses into a single row: filename, page count, and a "Replace" action.

### 2. Review what it read

The app has parsed the résumé into a search configuration, and the user must be
able to correct it before spending. This screen is where a bad guess gets caught.

Two columns. Left: the plain-language reading — inferred field, years of
experience, and the job titles it will search for, as editable chips. Right: the
skill terms it will score against, each with a weight from 1 to 5.

The weights are the important part. Show them as a sorted list, heaviest first,
each row carrying the term, its weight as a short horizontal bar, and a remove
control. Include a short explanation in the interface's own voice: terms that would
also appear in jobs the user does *not* want are weighted low on purpose, so a
generic term sitting high is worth removing.

Seed it with real-looking content: `react native 5`, `node.js 5`, `mongodb 5`,
`express 4`, `typescript 4`, `redis 4`, `socket.io 4`, `javascript 2`, `rest api 1`,
`git 1`. Flag the last two visually as low-value so the screen teaches what pruning
means.

Primary action: "Looks right". Secondary: "Start over with a different résumé".

### 3. Connect your scraping key

The app spends the user's own credit, so it needs their API key. Be direct about
that rather than dressing it up.

One password-style field, monospaced content, with a "Verify key" button that
resolves to a small confirmed state showing the account's remaining credit — a real
number, e.g. `$8.41 available`. That figure then becomes the meter's cap on every
later screen, which the copy should say.

Beneath, quiet reassurance that answers the obvious question: the key is used only
to run searches, and where it is stored. Include a link-styled "How do I get a
key?" that opens a short 3-step panel rather than navigating away.

### 4. Configure the sweep

The densest screen and the one where the meter earns its place. Every control here
moves the estimate above the rail immediately.

Left column, a stack of grouped controls. Right column, sticky, a live breakdown
that itemises the estimate — per source, showing search count and subtotal, with
free sources listed at `$0.00` in teal.

Controls, grouped under plain headings:

**Where you can work** — the most consequential choice, so put it first. Four
options, chosen as cards rather than a dropdown because each needs a sentence:
India only (onsite and hybrid); genuine remote from anywhere; global onsite
(warn plainly that most of these need visa sponsorship); everything.

**How senior** — a two-handle range on years of experience, plus a set of removable
chips for title words to exclude: `intern`, `fresher`, `trainee`, `new grad`,
`junior`.

**Pay floor** — a single number field, with a clearly-worded switch for whether to
keep listings that don't state pay at all. Most don't, so the copy should say so.

**Freshness** — how many days old a listing may be. Show the trade-off in text: a
wider window buys more listings at the same price.

**Skip** — free-text chips for stacks or domains to down-rank, e.g. `Salesforce`,
`CRM`, `.NET`.

**Depth** — results per search, and which sources to include. Each source row shows
its measured rate so the choice is informed: LinkedIn `$0.045 per search`, Indeed
`$0.09 per run`, Naukri `$0.50 per run minimum`, and the free feeds — RemoteOK, We
Work Remotely, Remotive, Jobicy, Himalayas — grouped as one teal row reading
`free, no key needed`.

As the estimate crosses $5, the meter fill shifts toward the cap tick and a quiet
inline note appears. Do not block the user or open a modal here; the next screen
handles it.

### 5. Confirm the spend

A deliberate stop before money moves. Narrow, centered, calm — the visual opposite
of the previous screen.

The estimate set very large in Bricolage Grotesque, amber. Below it, the itemised
plan as a plain list: source, search count, subtotal. Below that, the honest
caveats in the interface's voice — the estimate is derived from measured per-search
rates and the real figure lands within about 10%; a sweep takes roughly 40 minutes;
and re-ranking results later is free, so weights can be changed afterward without
paying again.

Primary action names the amount: "Run the sweep — $2.94".

**The over-cap variant of this screen** is a required second design. When the
estimate exceeds the key's available credit, the meter overshoots its cap tick and
turns red, and the confirm button is replaced by a second-key panel: one additional
key field, with copy explaining that the sweep will run on the first key until its
credit is exhausted and continue on the second, and that nothing is billed twice
because completed searches are recorded as they finish. Offer a clearly-worded
secondary path: "Narrow the search instead", returning to the previous screen.

### 6. Sweep running

The user waits about 40 minutes, so this screen must stay honest and readable and
must never show a fake spinner. It should reward a glance at 3 minutes and at 30.

The meter rail is now the live spend, filling for real, with the amount spent above
it and the estimate as a ghosted target ahead of the fill.

Centre: a **searchgrid** — one small tile per planned search, laid out as a dense
grid of 56, each labelled with its keyword and location on hover. Tiles are
outlined while queued, filled when complete, and carry the amber or teal source
colour. The grid filling in unevenly is the progress indicator, and it is truthful
in a way a bar is not.

Around it, four figures set large with plain labels beneath: searches completed,
listings found, listings kept after filtering, spent so far. To one side, a running
log of the most recent completions, newest first, each line reading like
`Bengaluru · React Native Developer — 18 found, 6 kept`.

Include an honest, prominent time estimate that updates from actual pace rather
than counting down from a fixed number, and a "Stop the sweep" action that states
what stopping preserves: everything already fetched is kept.

Design an **interrupted** variant of this screen too. When a key runs out of credit
mid-sweep the remaining searches fail silently, which is the product's real failure
mode, so this state must be unmissable: the grid shows the incomplete tiles in red
outline, the copy says exactly how many searches did not run, and the primary
action offers to resume with another key — noting that completed searches will not
be re-run or re-billed.

### 7. Results

The payoff. A dense, calm, readable table — this screen is for reading, not for
delight, so keep it quiet and let the data carry it.

Group rows into three sections, in this order, each with a count and a one-line
explanation of what it means:

- **You can work here now** — onsite or hybrid in the user's own country.
- **Genuinely remote** — reachable from where they are.
- **Needs sponsorship** — collapsed by default, since most users can't use these.

Columns: score, title, company, location, pay, experience asked for, matched skill
terms as small chips, and an apply action. Carry the source marker as an amber or
teal dot at the row start. Score is the sort key and should be visually weighted
accordingly — set it in tabular figures, large enough to scan down the column.

Above the table: a filter row with a score threshold slider, a source filter, and a
search box. State clearly that filtering is free and reversible, and that re-ranking
never costs anything.

Also design the **empty result** for a sweep that returned nothing usable: say
which filter removed the most rows and offer to loosen that specific one, rather
than showing a generic empty illustration.

## Quality floor

Responsive to mobile, with the results table scrolling horizontally inside its own
container rather than the page. Visible keyboard focus on every control. Contrast
that holds at these colours. Respect reduced-motion preferences.

---

## Motion spec — keep this, don't paste it

For implementation after Stitch returns the screens. GSAP throughout. One
orchestrated moment per screen; nothing scattered.

**The meter is the animated protagonist.** Almost all motion in the product belongs
to it, which is what keeps the rest of the app from feeling busy.

- **Page load, once per session** — the rail draws from left to zero-width over
  ~600ms on a soft ease, and the cap tick drops in after it. Never replay on
  navigation.
- **Estimate changes (screen 4)** — the fill tweens to its new width in ~350ms
  while the figure above runs a GSAP counter to the new value. Tabular figures mean
  no layout shift. `overwrite: 'auto'` on the tween so fast toggling doesn't queue.
- **Crossing the cap (screen 5)** — the single dramatic moment in the product. The
  fill overshoots the cap tick by a few percent, the rail shakes once, hard and
  short, and it settles back to rest against the tick in red. Roughly 500ms total.
  Everything else on the page holds still. Once per state entry, not on a loop.
- **Searchgrid (screen 6)** — each tile fills on its own completion event with a
  ~200ms scale-and-colour transition, stagger `0.01` when a batch lands together.
  The grid animates because work genuinely finished, so this reads as data rather
  than decoration.
- **Section reveal (screen 7)** — the three result groups stagger in once, ~80ms
  apart. Rows do not animate individually and cards do not lift on hover.

Everything else answers a click: panels expanding, chips being removed, the key
field resolving to its confirmed state. Skip entrance animations on sections and
transitions on every card — those are the generic default.

Wrap the lot in `gsap.matchMedia()` with a `(prefers-reduced-motion: reduce)`
branch that sets end states directly and leaves the counters as plain text.
