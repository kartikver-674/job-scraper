# Run-this prompt: tailor the scraper's `config.py` to my résumé

**How to use:** clone the repo, drop your résumé at `auto-apply/resume/resume.pdf`, then paste
**everything below the line** as your first message into a fresh Claude Code window opened at
the repo root. Claude will read your résumé and rewrite the candidate-specific parts of
`config.py` so the scraper fetches and ranks jobs that match *you*. It uses judgment per
résumé — it does **not** apply a fixed template — so it adapts even if your field is nothing
like the current setup (which is tuned for a React/Node full-stack dev).

No new dependencies, no scripts, no external API: Claude reads the PDF directly and edits the
file.

---------------------------------------------------------------------------------------------

You are tailoring this repo's job-scraper configuration to my résumé. The scraper
(`scraper.py`) reads `config.py` to decide **which** jobs to fetch and **how** to rank them.
Right now `config.py` is hand-tuned for a specific person.
Re-tune it for me, based on my résumé, using your judgment.

Do this:

1. **Read my résumé** at `auto-apply/resume/resume.pdf` (use the Read tool — it reads PDFs
   directly). If that file doesn't exist, stop and ask me where my résumé is.

2. **Read `config.py`** end-to-end so you understand its structure and comments. You will edit
   only the **candidate-specific** parts. Do **NOT** touch infrastructure that isn't about who
   I am: `SITES`, `GREENHOUSE_COMPANIES`, `LEVER_COMPANIES`, `LINKEDIN_GEO_IDS`,
   `NAUKRI_CITY_IDS`, `INDIA_LOCATION_HINTS`, and the cost-guard settings.

   **Then read `profiles/*.py` — every one of them.** This is the most expensive thing to
   miss. A profile overlay REPLACES the whole key it defines (the merge is one level deep), so
   a profile still holding the previous person's `role_keywords` will silently run *their*
   search at full price the moment anyone types `--profile global_remote`. `config.py` looking
   perfect tells you nothing about the profiles. Retune `SEARCH.role_keywords` in each, and
   re-do the cost arithmetic in the module docstring if the keyword count changed.

   **Two things that look like infrastructure but are candidate-specific — retune both:**
   - `ATS_TITLE_HINTS` — the ONLY gate on every free source (all ATS boards + all 5 feeds).
     Left on the previous person's vocabulary it discards 100% of the free layer and the run
     still prints a normal summary, because the boards are fetched and every row is then
     thrown away. Measured: with dev-only hints, a Salesforce résumé got 0 free rows; after
     retuning, 9 ranked rows including the two best matches of the whole sweep. Keep it
     BROADER than `role_keywords` — free rows cost nothing, so admit adjacent titles and let
     `SCORING` sink them.
   - `FEEDS` — wherever the API takes a filter, that filter encodes a FIELD. Verify which ones
     actually work rather than trusting the docstrings (all checked live 2026-07-28):
     `wwr` categories DO filter (`remote-sales-and-marketing-jobs` 154 items,
     `remote-customer-support-jobs` 60, `remote-product-jobs` 56,
     `remote-management-and-finance-jobs` 36; `remote-all-other-remote-jobs` returns 0 and is
     not a real category); `jobicy` `industry` DOES filter; `remotive`'s `category` is
     **ignored** by their API (every value returns the same newest ~36 across all categories);
     `remoteok` and `himalayas` offer no filter at all. So for a non-dev résumé you must
     retune `wwr` + `jobicy`; the other three are gated only by `ATS_TITLE_HINTS`.

3. **Infer my profile from the résumé** — my actual field/domain, seniority, years of
   experience, and the skills I genuinely have. Do not assume I'm a web/full-stack developer;
   read what the résumé actually says (I could be a data scientist, designer, DevOps engineer,
   PM, etc.).

4. **Ask me (in ONE batched question) for the things a résumé can't tell you** — never invent
   these:
   - preferred locations (and remote vs onsite),
   - expected salary / minimum CTC,
   - any roles, stacks, or domains I want to **avoid** (e.g. "no Salesforce/CRM"),
   - target seniority (which levels to include; which to exclude).
   Wait for my answers before editing.

5. **Propose the edits** to `config.py`, then show me a clear before → after summary for each
   changed key before writing. Specifically re-derive:
   - `SEARCH.role_keywords` — the job titles I should actually be searched for.
   - `SEARCH.experience_years` and `SETTINGS.max_experience_years` — from my experience +
     target level.
   - `SEARCH.locations` and `SETTINGS.min_ctc_lpa` / `SEARCH.salary_min` — from my answers in
     step 4.
   - `SCORING.skill_weights` — **the skills that are actually in my résumé**, each weighted by
     **DISCRIMINATIVE POWER, not by how central the skill is to me.** These are different
     things, and confusing them is the single most common way this config goes wrong. Ask of
     every term: *"would this word appear in a job I DON'T want?"* If yes, weight it low
     however core it is to my career.

     Worked example (a real failure, not hypothetical): for a Salesforce functional
     consultant, "requirement gathering", "BRD", "FRD", "UAT", "gap analysis" and
     "stakeholder management" are genuinely her top skills — so they were weighted 3-4. But
     *every* business-analyst JD on earth contains those words, while only the right jobs
     contain "Salesforce". Result on live data: an **"Oracle Fusion Functional Consultant"
     ranked #1** and a generic **"Business Analyst" scored 26**, both beating the real
     **"Salesforce Business Analyst" at 25**. The fix was to let the platform/domain terms
     dominate (10 vs 8 vs 5) and demote the generic craft vocabulary to 1-2 — same skills,
     same résumé, correct ranking.

     So: **the terms that identify MY niche get the highest weights; the transferable craft
     vocabulary I share with adjacent fields gets supporting weight only.** Do not include
     skills I don't have.
   - `SCORING.frontend_terms`, `SCORING.backend_terms`, `SCORING.fullstack_terms`,
     `SCORING.fullstack_title_terms`, `SCORING.fullstack_bonus` — these encode a full-stack
     "has both frontend + backend" bonus. If I'm a full-stack/web dev, adapt them to my stack.
     **If my field is not web development, this bonus will distort ranking** — replace it with
     the right notion for my domain (e.g. group the two halves of MY field), or neutralize it,
     and tell me exactly what you did and why.

     **`fullstack_title_terms` is a trap: every term MUST name my platform/domain, never a
     bare job function.** It flags a job as a match from the TITLE ALONE, bypassing all other
     evidence. Bare function words leak badly — measured on live data, `"business analyst"`
     handed the bonus to *"Business Analyst (Italian)"* and *"Japanese Business Analyst"*, and
     `"functional consultant"` handed it to *"Oracle Fusion Functional Consultant"*. Use
     `"salesforce business analyst"`, not `"business analyst"`. Genuine matches still earn the
     bonus through the two-halves rule, so nothing real is lost. Same rule for the two halves
     themselves: keep generic category words (e.g. a bare `"crm"`) OUT of them, or any
     adjacent-industry job satisfies that half for free.
   - `SCORING.penalty_terms` — stacks/domains to down-rank: my avoid-list from step 4 plus
     obviously off-domain technologies for my field. Two calibration rules, both learned the
     expensive way:

     **Penalize the TITLE signal, not a passing mention.** A term that names the wrong JOB is
     a strong penalty; a term that merely appears as a nice-to-have inside the RIGHT job is
     not. Measured: `"apex": -6` (meant to exclude pure Salesforce-developer roles) instead
     cancelled the entire bonus on the two best matches in the sweep — a Salesforce
     Administrator and a Business Systems Analyst, both ADM-201, page layouts, validation
     rules — because each JD listed "Knowledge of Apex, Flow, SOQL" as a bonus qualification.
     The fix: `"salesforce developer": -8` (the honest pure-dev signal) and `"apex": -3`. Ask
     of every penalty: *"could this word appear in a job I DO want?"* If yes, go mild and put
     the weight on the title term instead.

     **Penalize the bare platform word, not just its versioned name.** Measured: a "Microsoft
     Dynamics CRM Functional Consultant" scored **+2** and sat mid-list because the penalty
     said `"dynamics 365"` and the posting said "Dynamics CRM". With `"dynamics": -6` it
     correctly dropped to -10. Competing platforms get renamed constantly; match the root.
   - `SCORING.drop_terms` — seniority words that should remove a job outright, based on the
     levels I said to exclude in step 4.

6. **On my confirmation, edit `config.py` in place.** Preserve its section layout and its
   heavily-commented style (update the comments so they describe me, not the previous person).
   The repo is under git, so I can diff/revert — but still show me the summary first and don't
   write until I approve.

7. **When done, verify in this order — the first two stages cost nothing, so never skip
   straight to spending:**
   1. `python scraper.py --dry-run` — prints the plan and per-site inputs at zero cost. Check
      the searches actually name my field. Repeat it per profile
      (`--profile global_remote --dry-run`), since a profile replaces the keywords.
   2. `python scraper.py --profile remote_intl --site free --yes` — **a REAL, FULLY-RANKED run
      for $0.** Free sources only, no Apify actor touched. This is the single highest-value
      check in this whole document: it validates `ATS_TITLE_HINTS`, the `FEEDS` retune, the
      skill weights, the penalties and the bonus against genuine live postings before any
      money moves. Both live bugs listed in step 5 were caught here, at zero cost.
   3. Only then a small paid run (see the cost-guard notes in `config.py`).

8. **Tune the weights against REAL results, not just your reasoning.** Every ranking bug
   documented in step 5 was invisible on inspection and obvious the moment live jobs came back.
   So after the free run in step 7.2, read the actual top 10 and ask *"is anything here a job
   they'd never want?"* — then fix the weights and re-check. Three things make this cheap:
   - **Open the posting before you judge a row by its title.** A title that reads like noise
     can be the best match in the sweep. Measured: a Twilio *"Systems Ops Administrator"* tied
     for #1 and looked like generic IT-ops leakage — the actual JD was a Salesforce
     Administrator role (Sales Cloud, ADM-201 certification, page layouts, validation rules,
     approval processes), i.e. exactly the target. Had I "fixed" the weights to demote it, I
     would have deleted a real match. Fetch the JD (the ATS boards have a public JSON API) and
     check the CONTEXT of the matched terms before touching a weight in either direction.
   - **Re-scoring is FREE — never re-scrape to apply new weights.** `python
     rescore_from_apify.py` pulls the raw items back from the Apify runs you already paid for
     (Apify retains each run's dataset server-side; dataset reads cost no actor events) and
     re-runs them through the current `config.py`, writing `output/jobs_combined.*`. Scoring
     changes are safe to apply retroactively **as long as you didn't change a FILTER** —
     `min_score`, `max_age_days`, `min_ctc_lpa`, `max_experience_years`, `drop_terms` decide
     which jobs exist at all; the weights only decide sort order.
   - Write a throwaway assertion script that scores a handful of hand-written fake jobs (one
     ideal match, one adjacent-industry near-miss, one wrong-seniority, one off-domain) and
     asserts the ordering. It catches this class of bug in seconds with no spend.

**Two operational gotchas worth knowing before you spend:**
   - **A clean exit does NOT mean a complete sweep.** Each search is wrapped in its own
     `try/except`, so when an Apify account hits `Monthly usage hard limit exceeded` every
     remaining search fails silently, the script still prints a normal-looking summary and
     exits 0. `output/.done_combos` is the only honest record of what was actually scraped —
     check `wc -l` against the planned run count.
   - **A second Apify key resumes a capped sweep with no code change and no double-billing.**
     Put `APIFY_TOKEN_2` in `.env` and run
     `APIFY_TOKEN="$(grep -E '^APIFY_TOKEN_2=' .env | cut -d= -f2-)" python scraper.py --site indeed --yes`
     — env vars beat `.env` (python-dotenv doesn't override), and the `.done_combos` ledger
     makes the resume exact. Note datasets belong to the account that ran them, so a sweep
     split across two keys needs `rescore_from_apify.py` to merge both (it already scans every
     `APIFY_TOKEN*` it finds).

Constraints: never fabricate skills or experience — everything in `skill_weights`/keywords
must be grounded in my résumé; preferences come only from my step-4 answers. Don't add any
dependency. Keep all the infrastructure config (sites, ATS companies, geo/city id maps) exactly
as-is.
