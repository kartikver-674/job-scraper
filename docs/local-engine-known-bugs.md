# Local engine — known bugs, recorded and deliberately not fixed

Found by the Modal equivalence work (September 2026), recorded here so they
are not lost, and **kept out of the production Modal deployment**. Fixing
either one means changing the extraction prompts or schema, which changes the
exact inference behaviour the 52-document answer-key gate just validated —
so a fix is its own piece of work, followed by a full re-run of that gate.

Modal is correct on case 2. **Case 1 is not local-only:** the production
Modal endpoint reproduced it on its first Sarthak run (below).

Affected: anyone running the local engine against a local Ollama
(`SWEEP_INFERENCE_BACKEND=local-direct`, the default). **Untested:** whether
the Oracle A1 CPU node — the production fallback — reproduces either bug. It
runs the same qwen3:8b, but on different hardware, and it was not scored on
the corpus.

---

## 1. An exact duplicate employment row

**Seen on:** the Sarthak résumé (3 634 characters), which lists one role —
DealerMatix Technologies, Software Engineer, Jan 2025 – Present.

| Backend | Rows returned | Runs |
| --- | --- | --- |
| local-direct, M1, model warm | 2 (identical) | 5/5 |
| local-direct, M1, model reloaded each time | 2 (identical) | 3/3 |
| Modal T4 benchmark app, cold before every profile | 1 | 7/7 |
| **Modal T4 production app, 15 Sep 2026 rollout** | **2 (identical)** | **1/1** |

So it is the model, not the local path: same model digest, same prompts, and
the output flips between runs on the same hardware class. Why the benchmark
app never showed it and production did on its first try is unknown — one run
is not a rate. That production run matched local exactly (raw and semantic),
because both backends returned the duplicate.

**Impact: none measured on search behaviour.** Held titles are de-duplicated
twice in `local_search` (the `stem not in out` guard, then `add()` in
`rank()`), and `months_from` merges overlapping ranges, so years, keywords,
title gates, scoring and the rendered configuration were identical. The only
observable effect is on unreadable-date or escalation paths, where a repeated
row changes a count inside the router's explanation text ("1 of 2 rows…" →
"2 of 3 rows…") — never a decision.

**Severity: low.** The equivalence gate treats it as a raw mismatch and a
semantic match (`bench/backends.py`, exact-duplicate employment rows only).

## 2. Company and title swapped — search-driving

**Seen on:** `dmitri-plain` in the synthetic corpus. The answer key is
Kestrel Systems / Verification Engineer and Aragats Robotics / Systems
Engineer.

| Backend | company | title | Runs |
| --- | --- | --- | --- |
| Answer key | Kestrel Systems | Verification Engineer | — |
| Modal T4, cold | Kestrel Systems | Verification Engineer | 5/5 |
| **local-direct, M1** | **Verification Engineer** | **Kestrel Systems** | **4/4** |

**Impact: behaviour-changing.** Held titles come from the employment rows, so
local's `role_keywords`, `SEARCH.role_keywords`, the himalayas feed queries and
the local ranking become company names — `kestrel systems`,
`aragats robotics` — instead of `systems engineer`, `verification engineer`.
Local searches job boards for the employers, not the jobs. Dates, years and
experience limits are unaffected.

**It is detectable.** Local's own *fields* call returns the titles and
companies correctly; only its *employment* call swaps them. A row whose title
is one of the document's own companies, and whose company is one of its own
titles, is internally inconsistent.

**Scope in the corpus:** 1 of 52 documents; the other three dmitri layouts
were correct.

**Severity: high where it occurs.**

## 3. Local centrality is a constant, so weights can only reach 2/3/4

**Not fixed, deliberately. Recorded here as the follow-up to the frozen
market table** (`data/skill_market_frequencies.json`), which fixed a
different half of the same screen.

A skill's final weight is `blend(centrality, separation)` — the rounded
geometric mean of how central the skill is to the candidate and how much it
narrows the market. On the **Gemini** path the model supplies centrality
1-5. On the **local** path nothing does: `local_profile.generate` stamps
every skill with `NEUTRAL_WEIGHT = 3`, and `skill_scan` adds its own finds at
the same 3.

Three consequences, measured on the Sarthak résumé (42 skills):

| | |
| --- | --- |
| Reachable weights | **only 2, 3 and 4** — `blend(3, sep)` cannot produce 1 or 5 for any `sep` |
| "5 is reserved for a term the candidate is strongest in AND the market rarely asks for" (`blend`'s own docstring) | unreachable locally |
| What decides the ranking | rarity alone, since centrality is constant |

That last one is the real defect. **Bazel scores 4 and React Native scores
3** on this résumé: Bazel is named once, in a comma-list under "Build
Tools"; React Native is the job title, three bullets and two projects. Same
for Axios and Tailwind CSS at 4. This is precisely the "rare but incidental"
failure `blend` was written to prevent — it only prevents it when centrality
varies.

**Why it is not fixed here.** Any real centrality signal (for example: named
in the experience bullets vs. skills-list only vs. one passing mention —
`skill_scan` already records that evidence structurally) changes the weights
on **both** backends, which changes shortlist order for every existing
profile. That needs its own branch, a re-run of the 52-document answer-key
gate, and a before/after ranking comparison on a real sweep — not a
beta-week change.

## What a fix would involve — not started

Either a prompt/schema change to the employment call, or a deterministic
consistency guard after it (swap back, or escalate, when a row's title and
company match the document's own companies and titles the other way round).
Both change production logic, so both require:

1. the change on its own branch, separate from any deployment work;
2. the 52-document answer-key gate re-run on **both** backends
   (`deploy/run_answer_key_batches.sh`);
3. no driving regression on either backend before it merges.

Reproduce the local side (no Modal, no cost):

```bash
.venv/bin/python -c "
import sys; sys.path[:0] = ['.', 'auto-apply']
import local_extract as le
from resume_parser import extract_text
rows = le.employment(None, extract_text('bench/resumes/dmitri-plain.pdf'), backend='local-direct')
print([(r['company'], r['title']) for r in rows['employment']])"
```
