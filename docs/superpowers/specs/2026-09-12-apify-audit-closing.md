# Apify Independence — Closing Position

Closes the audit opened on 2026-09-11. Seven rounds of measurement, one
conclusion, two decisions left open for a human.

**Verdict: LinkedIn stays, by decision rather than by default. The free-source
work was worth doing, but as inventory expansion — it has not made the paid path
cheaper, because the two barely overlap. The one real saving found is inside the
paid plan itself.**

---

## 1. What moved

| the audit's headline | then | now |
|---|---:|---:|
| ≥20 postings that are Apify-**only** | **86.8%** | **82.6%** |
| ≥40 postings that are Apify-**only** | **91.7%** | **86.9%** |
| companies seen only via Apify | **89.0%** | **86.5%** |

82 useful postings and 75 companies gained a free alternative. Boards went from
24 to **129**.

The percentages understate the work, because the corpus they measure is
historical. The boards carry **1,424 dev-titled deduped postings that never
appeared in any sweep**, from 26 employers never seen in any form. That is the
real yield, and the attribution arithmetic cannot see it.

## 2. What did not move: spend

This profile's entire paid sweep is **$2.88** — 64 searches at $0.045.

**No keyword is droppable.** Of `kartik_reachable`'s 243 historical useful
LinkedIn postings, 5 are at a company with a board, and **2** survive the board
still carrying the opening at a still-useful score. Gap: **241 of 243**.

**Depth reduction is measurable and bad.** 25→20 buys a 20% cost cut for up to
23% of useful postings and 16% of the ≥40s. The best rows sit deep — scores 64,
63 and 62 at positions 21, 19 and 23 — because LinkedIn's ordering is
uncorrelated with our scoring, which is why a scoring layer exists at all.

## 3. What *did* move: redundancy inside the plan

With per-combo attribution recovered from the Apify datasets (Part 0/1 below),
the sweep turns out to search the same postings repeatedly.

Of 240 distinct useful postings in one sweep, **156 (65%) were surfaced by more
than one combo**; one appeared in **17**. No keyword is contained in another —
the closest pair, `Backend Engineer Node.js` and `Node.js Developer`, share 79 of
97 and 103.

**27 combos individually contribute nothing unique. Dropping all 27 together
loses 8 postings** — the set-cover trap, because a posting covered only by two
"individually redundant" combos is orphaned when both go.

**The greedy safe set is 22 combos, verified to lose zero useful postings:
$0.99 per sweep, 34% of paid spend.** $2.88 → $1.89.

*Measured on one sweep.* Redundancy structure may differ next time; a combo
redundant on 2026-09-06 could be the sole source of something tomorrow. This is
a candidate list to validate against a second sweep, not a config change.

## 4. Indeed — untestable here, for a structural reason

`kartik_reachable` disables Indeed (`"indeed": {"enabled": False}` — "no geoId
advantage here"). **Zero Indeed rows, zero `.done_combos` entries.** There is
nothing to run the drop test against within this profile's scope, and the brief
forbids extrapolating to others.

The depth half is also closed: Indeed's `apply_url` carries **only `jk=`**, the
job id. No position, no page. Unlike LinkedIn there is no accidental rank signal,
so retrospective depth analysis for Indeed is impossible — and now unnecessary,
since Part 4 records rank for every paid source going forward.

## 5. Closing the audit's own phases

**Phase 2 — the provider seam: do not build it.** Its purpose was to let an
aggregator slot in beside Apify. Phase 1 killed that premise, and nothing since
has produced a second search provider worth a seam. `sources.fetch_free()`
already is the board registry, and the one paid provider needs no abstraction to
sit beside it. Building it would be architecture in search of a consumer.

**Phase 3 — an aggregator as a first-class provider: does not survive contact
with the data.** Adzuna recovered 4.5% of LinkedIn's useful inventory and scored
0% of it, capped at a 500-character description that two independent probes
confirmed is worth +11 to +14 points and is unreachable at sweep volume through
either door. Adzuna's surviving role is a company-name generator for
`harvest_ats.py`, which needs a list export, not a provider.

**Phase 5 — LinkedIn: keep it, as a decision.** It supplies 84% of useful
postings and 90% of the best ones, and seven rounds have failed to find any
source that overlaps it. It is also the one source whose terms prohibit
automated access, which is precisely what we pay Apify to carry. Keeping it is
now backed by measurement rather than inertia.

---

## OPEN DECISIONS FOR A HUMAN

Neither is resolved here, and neither should be decided from this document alone.

**(a) Operationalise Adzuna as a scheduled company-name generator?**
It produced 239 employers never seen through any source, from 56 free API calls,
with 2.7% overlap against free inventory. Feeding those names to
`harvest_ats.py` is what produced most of the 96 boards added since. The open
question is whether to run it on a schedule or by hand, and how often. Nothing
about it is built.

**(b) Finish the remaining ~2,026-company backlog?**
~4–5 hours of probing, ~12.9% resolution, so perhaps 260 more boards. **Now
correctly understood as inventory expansion with no spend-reduction upside** —
§2 establishes that boards do not let us drop keywords or reduce depth. Order by
geography if it goes ahead: international resolves at 15.1% against India-only
10.7%, and large-and-India-only collapses to 1.8%, the cheapest stratum to skip.

---

## Appendix — this round's five parts

**Part 0 — datasets retrievable: YES.** All 218 in-window SUCCEEDED runs still
have readable datasets. Read-only; no actor started. (One false alarm of mine:
`client.dataset(id).get()` returns a typed object in apify-client 3.x, and
calling `.get()` on it raises `AttributeError`, which first read as "expired".
The repo documents that trap twice and I walked into it anyway.)

**Part 1 — redundancy: 22 combos safely droppable, $0.99/sweep.** See §3.

**Part 2 — Indeed: not testable in this profile.** See §4.

**Part 3 — naukri: kept, with the reason recorded.** Deleting the entry was
tried and reverted. `make_profile.validate_keys` rejects a `SITES` key that
`config` does not define, so every generated profile breaks, and eight sweep
tests assert naukri's presence in the UI and that its per-run floor is not
scaled by the depth control. `enabled=False` already means it cannot be planned
or run. The cascade costs more than the dead line it removes, so the change made
was documentation: the re-measurement (still zero rows across 22,745) and why
removal was rejected. **Stated plainly: the brief asked for removal and I did
not remove it.**

**Part 4 — instrumentation: `search_query` and `search_rank`.** Stamped in
`scrape_search` as the dataset is read, before `finalize()` dedupes. Appended to
`OUTPUT_COLUMNS`, never inserted. Verified byte-identical on 4,600 existing
column values across 200 real rows. Closes both gaps permanently, going forward.

Known limitation, recorded beside the field: dedupe keeps one row per posting and
the survivor's rank is whichever search sorted first, not the minimum across
searches. Depth analysis on it is an upper bound. Fixing that means changing
dedupe to keep the lowest rank when collapsing — a behaviour change, deliberately
not made here.
