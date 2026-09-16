# Deciding what a match score means

Status: **not started.** The UX pass deliberately shipped the raw score.

## Why this is open

The public results screen shows a bare integer — `61`, `43`, `14` — and a
visitor has no way to tell whether 43 is good. The obvious fix is a band:

> Excellent match · Strong match · Good match · Possible match

The UX audit proposed thresholds of ≥50 / 30–49 / 15–29 / <15. **Those were
invented, not measured.** They came from eyeballing one sample shortlist. A
band is a claim about a distribution, and a "Strong match" that turns out to
be a weak one is worse than an unexplained 27 — it is the same class of
overclaim as printing `$0.00` for a spend nobody read, which this codebase
already refuses to do everywhere else.

So the screen ships the number plus one sentence of explanation, and this is
the work that would earn the bands.

## What shipped instead

- Column header `Match` rather than `Score`
- A legend in the lede: *"Match score is how much of your résumé — skills and
  experience — a job actually asks for, so a higher score means more of what
  you have."*
- `title="Match score N — how much of your résumé this job asks for"` on every
  cell, and on the running screen's feed

## The analysis

Everything needed is already on disk. `output/*/jobs_*.csv` holds every sweep
ever run, with `score`, `matched_skills`, `title`, `company` and
`source_site` per row.

1. **Get the distribution.** Per profile, not pooled: scores are not
   comparable across résumés, because the weights differ. Plot the histogram
   and the deciles of `score` for each of the profiles with a real sweep
   behind them.

2. **Decide whether bands are per-profile or absolute.** This is the real
   question. If the useful cut points move with the profile, a fixed table of
   thresholds is wrong for everyone but the profile it was fitted to, and the
   band has to be a percentile of *this sweep* ("top 10% of your matches")
   rather than an absolute score. Percentile bands are self-calibrating and
   need no ground truth, which makes them the cheaper answer if the
   distributions really do differ.

3. **Get some ground truth, if bands are to be absolute.** `bench/answer_key.py`
   and `bench/score.py` already exist for scoring quality work — see what they
   hold before building anything. Otherwise: take ~150 rows spanning the score
   range from two or three real profiles and label each *would I apply to
   this?* Three labels, not five.

4. **Fit the cut points to that**, and report how often a band is wrong in
   each direction. A band that calls a bad job "Strong" is much worse than one
   that calls a good job "Possible": the first wastes an application, the
   second costs a scroll.

5. **Check the top band is not empty.** A sweep where nothing clears
   "Excellent" makes the whole scale look broken. If the top band is rarely
   occupied, there should be three bands, not four.

## When to do it

After enough beta sweeps exist to have distributions from people who are not
the operator. Every profile in `output/` today is a friendly tester, and their
résumés cluster.

## If the answer is "no bands"

That is a real outcome and it is fine. The number plus the legend is honest,
and "top 10% of your matches" is available as a middle option that needs no
ground truth at all. Do not ship a four-word judgement the data cannot support.
