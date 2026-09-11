# Can 129 Free Boards Let Us Spend Less on Apify?

**Measurement only. No config change, no Apify call, no Gemini call, no probing.**
Scope: `kartik_reachable` only — 8 keywords × 8 locations = 64 LinkedIn searches,
**$2.88 per sweep** at $0.045/search.

**Answer: no keyword is safe to drop — not one, not close. Depth reduction is
measurable and the answer is that it is expensive.**

---

## Track A — can a keyword be dropped entirely?

### The attribution gap, stated first

**Which keyword surfaced a given posting was never recorded.** Output rows carry
23 fields and none of them is a keyword, query, or combo id; `.done_combos`
records which searches ran but nothing linking a search to its rows. The only
artifact that could reconstruct it is the per-run Apify dataset, and reading
those is an Apify call, which this step excludes.

So the per-keyword table as specified cannot be built from what exists.

**It does not change the answer**, because the drop test is disqualified by a
single uncovered posting and the uncovered count is 241 of 243. For any keyword
to clear, its *entire* useful yield would have to fall inside the 2 covered
postings. Each keyword ran 8 searches at depth 25 — 200 result slots — and the
two covered postings are at BitGo and MongoDB. No keyword is that small.

### The three bars, measured on all 243

Every historical useful (≥20) LinkedIn posting for this profile, against
today's 129-entry `ATS_BOARDS`, boards re-fetched live rather than reused:

| bar | surviving |
|---|---:|
| historical useful postings | **243** (204 distinct companies) |
| 1. company has a live board today | **5** (2.1%) |
| 2. that board still carries the posting | **2** |
| 3. it still scores ≥20 under this profile | **2** |

The five that cleared bar 1, individually:

| company | posting | still on board | score today |
|---|---|---|---|
| BitGo | Backend Engineer E2 – Ecosystem | yes | **39** |
| MongoDB | Software Engineer 3, AI Builder Experience | yes | **28** |
| Codeyoung | Full Stack Engineer | **no** | — |
| Pentair | Associate Specialist, Cloud Platform | **no** | — |
| JumpCloud | Full Stack Software Engineer, Front-End | **no** | — |

Three of five show exactly why "has a board" is not "is covered": the employer
runs a public board and the specific opening is no longer on it.

### Gap list

**241 of 243.** Not enumerated by name here because the list is the near-entirety
of the profile's useful output — 204 companies of which 199 have no board at all.
The named list the brief asks for would be the report.

### Keywords clearing: none. Savings: $0.

For the record, so the arithmetic exists when something does clear: dropping one
keyword removes 8 searches, **$0.36 per sweep**, 12.5% of this profile's $2.88.

---

## Track B — can `max_results` be reduced?

### Feasibility: yes, and this was not expected

Rank **was** recorded, incidentally. LinkedIn's own job URL carries its position
within the result set, and the scraper stores that URL verbatim as `apply_url`:

```
…/jobs/view/ai-native-full-stack-developer-…?position=7&pageNum=0&refId=…
```

**All 1,123 LinkedIn rows for this profile carry `position=`. Every one is
`pageNum=0`, positions 1–25 — exactly `max_results=25`.** No instrumentation is
needed; the data has been there all along.

### Depth sensitivity

| depth | cost/search | useful kept | useful lost | ≥40 lost |
|---:|---:|---:|---:|---:|
| **25** (current) | $0.0450 | 243 | 0 | 0 |
| 20 | $0.0360 | 188 | **55 (23%)** | 10 (16%) |
| 18 | $0.0324 | 167 | 76 (31%) | 15 (24%) |
| 15 | $0.0270 | 143 | **100 (41%)** | 20 (32%) |
| 12 | $0.0216 | 120 | 123 (51%) | 24 (38%) |
| 10 | $0.0180 | 97 | 146 (60%) | 29 (46%) |

**A 20% cost cut (25→20) costs 23% of useful postings and 16% of the best ones.
The trade is worse than linear at every depth tested.**

And the losses are not the marginal rows. The highest-scoring postings this
profile has ever found sit deep in the results:

| position | score | posting |
|---:|---:|---|
| 21 | **64** | Senior Associate Consultant – FullStack, SAP BusinessObjects |
| 19 | **63** | Engineer 2, Styli |
| 23 | **62** | App Developer – AI/ML, Warner Bros. Discovery |
| 23 | **59** | Full Stack Developer (Node.js & React.js), Aadrika |
| 22 | **56** | Full Stack Engineer, Tarento Group |

LinkedIn's ordering is uncorrelated with our scoring, which is the whole reason
a résumé-matching layer exists. Truncating its results truncates arbitrarily.

### The one caveat, and its direction

Each posting has **one** recorded position — whichever instance survived dedupe —
and a posting returned by several searches keeps an arbitrary one, not the
lowest. A row recorded at 23 may also have sat at 4 in another search, where a
shallower sweep would still have found it.

So every loss figure above is an **upper bound**. The real loss is somewhere
between these numbers and zero. Given 562 distinct postings drawn from 64 × 25 =
1,600 result slots (~2.8 appearances each), the overlap is substantial and the
bound may be loose.

**Making it exact needs one field**: record, per row, the (keyword, location) and
the position it came back at — before `finalize` dedupes. That is one dict entry
in `normalize()` and one line in `scrape_search()`. It would also close Track A's
attribution gap permanently. Noted for later; not done here.

---

## Closing

**Is any keyword safe to drop today? No.** 241 of 243 useful postings have no
free-board alternative — no board, or a board that no longer carries the opening.
The free boards and the paid searches are looking at almost disjoint slices of
the market, which is the same finding every round since Phase 1, now measured
against the specific thing a drop decision depends on.

**Is the saving worth acting on? The question does not arise** — nothing clears.
And the ceiling is small: this profile's entire paid sweep is **$2.88**, and one
keyword is **$0.36**. Even a hypothetical fully-covered keyword would save 36
cents a sweep. **Paid-search reduction is not where the money is for this
profile, and the effort to establish safety exceeds the saving it could unlock.**

**Is depth reduction measurable? Yes — today, with no new instrumentation**, and
the measurement argues against it: 25→20 trades 20% of cost for up to 23% of
useful results and 16% of the best ones, with the top-scoring rows concentrated
at positions 19–25.

The honest summary of both tracks: **129 free boards have not made the paid path
cheaper, because they do not overlap it.** Their value is the 1,424 net-new
postings measured yesterday, not a reduction in what Apify is asked to do.

---

## Reproducing

Both tracks are re-derivable from `output/kartik_reachable/` plus a live fetch of
the 5 overlapping boards. No stored artifact beyond the repo is needed.
