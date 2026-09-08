# Sweep — a local web UI for the job scraper

Design doc. 2026-09-08.

## Problem

Running a sweep for a new person currently takes a person who knows this repo.
`auto-apply/make_profile.py` removed the need for a human to hand-tune
`config.py`, but everything around it is still CLI: place a PDF at a fixed path,
pass four flags, read `--dry-run` prose to decide whether the spend is
acceptable, watch a 40-minute run scroll past, then open a generated HTML file.

The goal is a UI that a job seeker who is not us can drive end to end, without
making the tool less careful about money than it is today.

## Decisions

**Local-first, not hosted.** `sweep` starts a server on `127.0.0.1`, opens the
browser, and exits when the window closes. The résumé and the API keys never
leave the user's machine, there is no queue or worker infrastructure for
40-minute jobs, there is no auth, and it costs nothing to operate. It also
matches the bring-your-own-token model: the user is already supplying the credit,
so there is no reason for us to be in the middle. What we publish is a landing
page and an install command.

**Server-rendered HTML with Alpine.js.** Stitch produced HTML/Tailwind screens,
so they become Jinja templates directly rather than being re-authored as
components. No JSON API layer is needed for its own sake. Alpine drives the live
meter, SSE streams sweep progress, GSAP loads as a plain script.

**Core flow first; variants are states.** Seven screens. Over-cap, interrupted
and backup-key are conditional blocks inside confirm and running, because each is
the same page with a different value in the same file.

## Architecture

Flask is the one new dependency. Stdlib `http.server` would mean writing routing
and template plumbing by hand; FastAPI would add starlette, pydantic and uvicorn
to a single-user localhost tool that needs none of them. Flask brings Jinja2,
which is what makes the Stitch HTML reusable, and SSE is a generator response.

The engine is unchanged. A sweep is `scraper.py --profile <name> --yes` as a
subprocess. The web layer starts it, watches it, and reads its output.

No database. This repo already keeps state in files — `output/seen.tsv`,
`output/<profile>/.done_combos`, `output/<profile>/jobs_*.json`. One `run.json`
per sweep sits alongside them. Adding SQLite would introduce a new concept and
a second source of truth for something the filesystem already records.

New modules:

    sweep/__main__.py     entry point: pick a port, start Flask, open browser
    sweep/app.py          routes
    sweep/runs.py         start/stop a sweep, watch .done_combos, read results
    sweep/templates/      Jinja templates ported from the Stitch v3 screens
    sweep/static/         one stylesheet, Alpine, GSAP

Reused unchanged: `auto-apply/resume_parser.py`, `auto-apply/make_profile.py`,
`scraper.py`, `rescore_from_apify.py`, `auto-apply/linkedin_shortlist.py`,
`verify_geoids.py`, and `profiles/` as the profile store.

### Progress comes from `.done_combos`

`scraper.py` appends to `output/<profile>/.done_combos` as each keyword×location
combination finishes. Its own comments call it the only honest record of what ran,
and it is what makes a resumed sweep exact. The UI counts its lines against the
planned combo count.

This is deliberately not stdout parsing. Log lines are a formatting detail that
would break silently; `.done_combos` is the file the engine already trusts. It
also gives the interrupted state for free: planned minus done is exactly how many
searches did not run.

### Spend comes from the account, never from the actor

`scraper.py:account_usage_usd()` reads month-to-date spend from
`client.user().limits()`. Its docstring records why this matters: on a measured
84-run sweep the actor self-reported $0.53 while the account actually moved
$1.61. A meter built on actor self-reports would understate spend by roughly 3x.

The running screen's spend figure and the key screen's available-credit figure
both come from this function. Available credit is the account's monthly limit
minus `monthly_usage_usd`.

### The UI never enforces the budget

`SETTINGS["max_spend_usd"]` in the profile remains the only real cap, enforced
inside `scraper.py` before each search launches. Everything the UI shows is
advisory. A bug in the estimate must not be able to cause an overspend.

## Screens

### 1. Upload — `GET /` , `POST /resume`

Saves the PDF and extracts text via `resume_parser.load_resume()`, which already
caches on mtime. Meter reads `$0.00`; no key and no configuration exist yet.

### 2. Review — `GET /review` , `POST /review`

Calls `make_profile.generate()` and `make_profile.render()` as functions. Shows
the inferred field, the titles to search, and the skill weights as bars.

This screen exists because the model gets weights wrong in a specific, repeatable
way: on the first real run it emitted `render: 3` (a hosting provider, but a word
in most frontend postings) and `git: 1`. Approving writes `profiles/<name>.py`.

Terms whose weight is low are flagged, with the reason stated plainly: a term
that also appears in jobs you don't want carries no signal however core it is to
you.

### 3. Key — `GET /key` , `POST /key`

Verifies the token with `apify-client` and reads the credit remaining, which
becomes the meter's cap for every later screen. Written to `.env` as
`APIFY_TOKEN`; the app keeps no copy of its own. A second key is stored as
`APIFY_TOKEN_2`. Note the ceiling: `rescore_from_apify.py:49` reads a fixed tuple
of `APIFY_TOKEN`, `APIFY_TOKEN_2`, `APIFY_TOKEN_3`, so the UI can offer at most
three keys before that list needs extending.

### 4. Configure — `GET /configure` , `POST /estimate`

Form fields map onto the config keys `make_profile.validate_keys()` already
checks against the live config, so a renamed key fails loudly instead of being
silently ignored — the failure mode that let `SCORING.drop_terms` and
`SETTINGS.min_ctc_lpa` sit in our own documentation while doing nothing.

Controls: where you can work, seniority band and excluded title words, pay floor,
freshness, terms to skip, results per search, and which sources.

`POST /estimate` returns the combo count and cost. **This requires one change to
`scraper.py`: a `--json` flag that makes the existing `--dry-run` emit its plan
as JSON instead of prose.** Re-deriving the planner in the web layer would create
two implementations that could disagree about what a sweep costs, which is the
one thing this UI exists to be trustworthy about.

Per-source rates shown from measurement: LinkedIn $0.045/search, Indeed
$0.09/run, Naukri $0.50/run minimum, free feeds $0.00.

### 5. Confirm — `GET /confirm` , `POST /run`

Itemised plan, the total, and a primary action naming the amount. States the
honest caveats: about 40 minutes, and re-ranking afterwards is free.

Over-cap is a conditional block on this page. When the estimate exceeds available
credit it replaces the confirm button with a second-key field plus quantified
alternatives ("drop Naukri, −$1.40"; "shrink the window 30d→14d, −$1.92"), and
explains that the sweep runs on the first key until its credit is exhausted and
continues on the second, with completed searches neither re-run nor re-billed —
which is true because of `.done_combos`.

### 6. Running — `GET /running` , `GET /events`

SSE stream reading `.done_combos` and account usage. One tile per planned search,
filling as each completes, carrying its source colour. Four figures: searches
done, listings found, listings kept, spent so far. Time remaining derived from
observed pace, never a fixed countdown.

Interrupted is the same page: when the process exits with combos still
outstanding, incomplete tiles show as not-run with an exact count and an offer to
resume with another key. This matters because a capped sweep currently fails
silently — every remaining search errors, the script still prints a normal summary
and exits 0.

`Stop the sweep` terminates the subprocess and states what is preserved:
everything already fetched is kept.

### 7. Results — `GET /results`

`linkedin_shortlist.py` already renders this, including the three reachability
buckets. Work is porting it into the shared template and adding the filter row
(score threshold, source, text search). Re-ranking calls
`rescore_from_apify.py`, which reads datasets already paid for and costs nothing.

## Errors

Every failure names what happened and what to do:

- No résumé text extracted (image-only PDF) — say so, ask for a text PDF.
- Gemini 503 — `make_profile.generate()` already retries five times with widening
  back-off; if it still fails, say the model is busy and nothing was written.
- Invalid or expired Apify token — reject at the key screen, before configuration.
- Sweep exits non-zero — show the interrupted state with the real combo counts.
- Zero results kept — name the filter that removed the most rows and offer to
  loosen that one specifically.

## Testing

`sweep/tests/`, unittest, matching `auto-apply/tests/`:

- `runs.py` progress maths: a fixture `.done_combos` plus a planned count gives
  the expected done/outstanding split, including the interrupted case.
- Estimate arithmetic: rate × count equals each subtotal and subtotals equal the
  total — the error that shipped in the mockups.
- Route smoke tests with the Flask test client: each screen renders, and each
  POST rejects missing required input rather than guessing.
- No test performs a paid run or a live model call; the sweep subprocess and the
  Gemini call are both injected.

## Out of scope

Hosting, accounts, and multi-user. Mobile layout. The auto-apply email flow and
the LinkedIn userscript, which stay CLI. Document-frequency weight calibration
(IDF over the 13,747 scraped rows), which is a separate piece of work and is
better done once this UI makes it visible which weights are wrong.

## Also in this work

`auto-apply/make_shortlist.py`'s docstring still says step 2 "needs Claude's
judgment, so it isn't scripted" and tells the reader to paste
`RESUME_AUTOCONFIG_PROMPT.md` into a Claude window. `make_profile.py` replaced
that. The docstring should describe the scripted flow.

## Open questions

None blocking. Two to confirm during implementation:

- Whether the Apify monthly limit is exposed alongside `monthly_usage_usd` in
  `limits()`; if not, available credit is entered by the user rather than read.
- Whether a sweep and a re-score can safely run against the same output
  directory at once, or whether the UI must serialise them.
