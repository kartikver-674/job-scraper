# Scope: a frozen title corpus for the public beta

Status: **proposed, not built.** Numbers below are measured, not estimated.

## The failure this fixes

A real beta upload failed and told the visitor to re-export their PDF. The
PDF was fine — 3806 characters of clean text. Reproduced end to end:

```
Modal call          200, the model answered
local_search.fields_for()   role_keywords: []
local_profile raises Escalated
app.py catch-all → "export your résumé as a text-based PDF"
```

`7c8979d` replaced that message with an honest one. This document is about
making the parse actually succeed.

## Why it happened, precisely

Three facts meet:

1. **Render ships no `output/`.** `docs/public-beta.md` says so, and it is
   the same reason `data/skill_market_frequencies.json` exists — skill
   weights already have a frozen fallback, and title derivation does not.

2. **`from_resume()` deliberately drops internships.** It filters through
   `local_extract.countable()`, whose own comment records why: *"without
   them ... bhaskar searches for the internship he did rather than the job
   he wants."* That is a measured guard, not an oversight.

3. **So a résumé whose every role is an internship has no own-title floor,**
   and with no corpus there is nothing else. Measured on the résumé that
   failed — roles `Full Stack Developer (Intern)`, `Front End Developer
   (Intern)`, `Student Lead`:

   | corpus | `from_resume` | `role_keywords` | outcome |
   | --- | --- | --- | --- |
   | 22,806 rows (a laptop) | `[]` | 8 | parses |
   | 0 rows (Render) | `[]` | **0** | **Escalated** |

   Note `from_resume` gives nothing in BOTH cases. Every one of the eight
   keywords came from the corpus — six ranked by market lift, two anchored
   off `firebase` and `tensorflow`. The corpus is not an enhancement for
   this class of résumé; it is the only mechanism that serves it.

This is not one unlucky file. It is every graduate, every career-changer,
and anyone whose last title does not name the job they want.

## The proposal

Mirror `corpus_signal.market_signal()` exactly — the pattern is already in
the repo, already tested, and already reported by `/healthz`.

```python
# local_search.py
def market_rows(output_dir=None, frozen=None):
    """(rows, source) where source is "live", "frozen" or "none".

    A CHOICE, not a merge — corpus_signal's own reasoning, for the same
    reason: blending would make a keyword's rank depend on which dataset
    it happened to land in.
    """
```

`Market.__init__` already accepts `rows=`, so the seam exists. Four pieces:

| # | Piece | Notes |
| --- | --- | --- |
| 1 | `data/title_corpus.json.gz` | the committed table |
| 2 | `local_search.frozen_rows()` + `market_rows()` | degrades to `[]` on a missing/corrupt file, as `frozen_frequencies` does |
| 3 | `python -m local_search --freeze` | mirrors `python -m corpus_signal --freeze`, same provenance block |
| 4 | `/healthz` reports `title_corpus_source` | beside the existing `market_signal_source` |

## Measured cost

Full corpus, no trimming:

| | |
| --- | --- |
| rows | 22,806 (4,221 distinct titles, 367 skills, 2,939 companies) |
| file on disk | **386 KB** gzipped (2.4 MB raw) |
| gunzip + JSON parse | 0.07 s |
| rebuild tuples | 0.05 s |
| `Market` index build | 0.64 s |
| **total cold** | **0.76 s** |
| peak Python heap | **38 MB** (Render Free is 512 MB) |

`Market` is built once per parse, and a parse already waits 20–150 s on the
model. 0.76 s is noise.

## The one real decision: dedupe or not

The corpus holds 57 sweeps, so the same posting appears many times.

| variant | rows | file | Lovish's keywords |
| --- | --- | --- | --- |
| as-is | 22,806 | 386 KB gz | **8** |
| dedupe (title, score, skills, company) | 9,512 | 218 KB gz | 7 |
| dedupe (title, skills, company) | 8,348 | 162 KB gz | — |

Deduping halves the file and **changes the answers**: 7 keywords instead of
8, and different ones — `artificial intelligence engineer` and `sde iii`
appear, `full stack web developer` and `node js` drop out. `idf()` divides
by `len(rows)` and counts skill occurrences, so collapsing repeats is a
different market, not a smaller copy of the same one.

**Recommendation: ship it as-is.** 386 KB is affordable, and a frozen
corpus that answers differently from the live one would make a bug
reproducible on a laptop and not on Render, or the reverse. `company` in
particular cannot be dropped at all — it feeds the "posted by many
employers" guard that stops one firm's internal title polluting results.

Deduping is defensible on the merits (each real posting counted once, which
is arguably more correct) but it is a change to search behaviour, so it
should be a deliberate decision with its own before/after, not a side
effect of adding a fallback.

## What is in the file

Checked before proposing to commit it: public job-listing data only.
Titles, integer scores, matched skill terms, company names. No email
addresses, no numeric identifiers, no résumé text, no personal data. The
367 skills are generic technology terms (`.net`, `agile`, `ai agent`); the
top companies are GitLab, Stripe, Databricks, Cloudflare, Elastic.

One honest caveat: the corpus is a snapshot of **the maintainer's own
sweeps**, so it leans India-and-React-Native. A visitor from a different
field gets keywords ranked against a market that is not quite theirs. That
is still far better than `Escalated`, and it is the same limitation the
frozen skill table already carries.

## Risks

| Risk | Mitigation |
| --- | --- |
| Frozen and live corpora rank differently | Ship as-is rather than deduped, so the two are the same dataset |
| The table goes stale as the market moves | Provenance block records the date and row count; re-freeze is one command |
| 386 KB in git history | One file, regenerated rarely. Git handles it; the alternative is a failing product |
| A visitor's field is absent from the corpus | They get fewer keywords, not zero — `select_detail` anchors off skills, which is where 2 of Lovish's 8 came from |
| `MIN_LISTINGS`-style thinness gate | `corpus_signal` uses 200; the same floor should decide "usable" here rather than `len(rows) > 0` |

## What this does NOT fix

A résumé with no usable skills either — no corpus can anchor off nothing.
That case still escalates, and now says so honestly.

## Estimate

Half a day. Four small pieces, one of them a maintenance command, and the
pattern is already written once in `corpus_signal.py`. The tests that
matter are: frozen file missing, truncated, and nonsense all degrade to
`none`; live beats frozen when both exist; and the résumé above produces
keywords with the frozen table and none without it.
