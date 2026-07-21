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
     YOUR judgment of how central it is to the roles I'm targeting (weight by relevance to my
     target job type, not by a fixed tier table). Higher = more important to my target jobs.
     Do not include skills I don't have.
   - `SCORING.frontend_terms`, `SCORING.backend_terms`, `SCORING.fullstack_terms`,
     `SCORING.fullstack_title_terms`, `SCORING.fullstack_bonus` — these encode a full-stack
     "has both frontend + backend" bonus. If I'm a full-stack/web dev, adapt them to my stack.
     **If my field is not web development, this bonus will distort ranking** — replace it with
     the right notion for my domain (e.g. group the two halves of MY field), or neutralize it,
     and tell me exactly what you did and why.
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

Constraints: never fabricate skills or experience — everything in `skill_weights`/keywords
must be grounded in my résumé; preferences come only from my step-4 answers. Don't add any
dependency. Keep all the infrastructure config (sites, ATS companies, geo/city id maps) exactly
as-is.
