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
   `NAUKRI_CITY_IDS`, `INDIA_LOCATION_HINTS`, `ATS_TITLE_HINTS`, and the cost-guard settings.

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
     obviously off-domain technologies for my field.
   - `SCORING.drop_terms` — seniority words that should remove a job outright, based on the
     levels I said to exclude in step 4.

6. **On my confirmation, edit `config.py` in place.** Preserve its section layout and its
   heavily-commented style (update the comments so they describe me, not the previous person).
   The repo is under git, so I can diff/revert — but still show me the summary first and don't
   write until I approve.

7. **When done, tell me how to verify:** run `python scraper.py --dry-run` to print the plan
   and per-site inputs at zero cost, then a small real run (see the cost-guard notes in
   `config.py`).

8. **Tune the weights against REAL results, not just your reasoning.** The two ranking bugs
   documented in step 5 were both invisible on inspection and obvious the moment live jobs came
   back. So after the first small run, read the actual top 10 and ask *"is anything here a job
   they'd never want?"* — then fix the weights and re-check. Two things make this cheap:
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
