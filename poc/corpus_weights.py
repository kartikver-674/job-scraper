"""POC: can the corpus answer the question the model is asked to guess?

make_profile's RULE 1 tells Gemini to "WEIGHT BY DISCRIMINATIVE POWER, NOT
CENTRALITY, ON A 1-5 SCALE" — a term that also appears in jobs you do NOT
want must score low, however central it is to your work.

That is a claim about a job market, not about a résumé, and the model
answers it from its prior. We have the market: output/ holds thousands of
real listings whose `matched_skills` column records which of the profile's
terms each posting actually contained. Document frequency over that IS
discriminative power, measured, for the exact searches this user runs.

This script asks two things:

  1. How well does Gemini's 1-5 weight agree with measured frequency?
  2. Which terms would move, and by how much, if the weight were measured
     instead of guessed?

Read-only. Nothing here writes to output/, profiles/ or .env, and nothing
calls a model.

    python -m poc.corpus_weights            # the report
    python -m poc.corpus_weights --demo     # self-check, no corpus needed
"""

import collections
import csv
import glob
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The weight the model may assign, and what this maps a measured frequency
# onto so the two are comparable at all.
SCALE = (1, 2, 3, 4, 5)


def corpus_frequencies(output_dir=None):
    """{term: (listings containing it, listings scored for it)} from the CSVs.

    The denominator is per TERM, not global: `matched_skills` can only name
    terms the profile that ran the sweep was carrying, so a term absent from
    a profile is not "0% in those listings" — it was never looked for. Each
    term is therefore counted only over the sweep directories whose
    vocabulary included it.
    """
    output_dir = output_dir or os.path.join(REPO, "output")
    # Per directory: how many listings, and which terms were in play.
    per_dir = collections.defaultdict(
        lambda: {"rows": 0, "hits": collections.Counter(), "vocab": set()})

    for path in glob.glob(os.path.join(output_dir, "**", "*.csv"),
                          recursive=True):
        bucket = per_dir[os.path.basename(os.path.dirname(path))]
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                if "matched_skills" not in (reader.fieldnames or ()):
                    continue
                for row in reader:
                    bucket["rows"] += 1
                    terms = {t.strip().lower()
                             for t in (row.get("matched_skills") or "").split(",")
                             if t.strip()}
                    bucket["vocab"] |= terms
                    for term in terms:
                        bucket["hits"][term] += 1
        except (OSError, UnicodeDecodeError, csv.Error):
            continue

    out = {}
    for bucket in per_dir.values():
        for term in bucket["vocab"]:
            hits, seen = out.get(term, (0, 0))
            out[term] = (hits + bucket["hits"][term], seen + bucket["rows"])
    return out


def measured_weight(share):
    """A 1-5 weight from a term's document frequency.

    The same shape RULE 1 describes, computed rather than guessed: a term in
    nearly every posting separates nothing and scores 1; a term in a few
    percent of them is what actually picks a shortlist out of a market.

    Cut on log-frequency, because the interesting range is the bottom decade
    — the difference between 0.4% and 4% matters far more than the one
    between 30% and 40%, and a linear cut puts almost everything in one bin.
    """
    # Log-spaced cuts, written as the frequencies themselves: the log is
    # the reasoning, but expressed as decades the band edges land on
    # ambiguous boundaries (10% is exactly 1.0) and read as arithmetic
    # rather than as the thresholds they are.
    if share >= 0.20:
        return 1
    if share >= 0.10:
        return 2
    if share >= 0.04:
        return 3
    if share >= 0.01:
        return 4
    return 5


def spearman(pairs):
    """Rank correlation, ties averaged. No numpy in this project."""
    if len(pairs) < 3:
        return None

    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            shared = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                out[order[k]] = shared
            i = j + 1
        return out

    xs, ys = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    n = len(pairs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs)
                    * sum((y - my) ** 2 for y in ys))
    return None if den == 0 else num / den


def compare(model_weights, freqs, min_listings=200):
    """Model weight against measured weight, for terms the corpus can speak to.

    A term seen in only a handful of listings has no measurable frequency,
    so it is reported as unmeasurable rather than scored against noise.
    """
    rows, unmeasurable = [], []
    for term, weight in sorted(model_weights.items()):
        hits, seen = freqs.get(term, (0, 0))
        if seen < min_listings:
            unmeasurable.append(term)
            continue
        share = hits / seen
        rows.append({"term": term, "model": weight, "share": share,
                     "measured": measured_weight(share),
                     "hits": hits, "seen": seen})
    return rows, unmeasurable


def report(rows, unmeasurable, total_terms):
    agree = [r for r in rows if r["model"] == r["measured"]]
    gap = sorted(rows, key=lambda r: r["measured"] - r["model"])
    rho = spearman([(r["model"], r["measured"]) for r in rows])

    print(f"\n{'=' * 72}\nRULE 1: guessed weight vs measured discriminative power\n{'=' * 72}")
    print(f"  terms in the profile        {total_terms}")
    print(f"  measurable in the corpus    {len(rows)}"
          f"   (the rest appear in too few listings to measure)")
    print(f"  exact agreement             {len(agree)}/{len(rows)}"
          f" = {len(agree) / len(rows):.0%}" if rows else "  no measurable terms")
    within = [r for r in rows if abs(r["model"] - r["measured"]) <= 1]
    print(f"  within one step             {len(within)}/{len(rows)}"
          f" = {len(within) / len(rows):.0%}")
    print(f"  rank correlation (spearman) {rho:+.2f}" if rho is not None
          else "  rank correlation            n/a")

    print(f"\n  Model scored HIGH, market says commodity"
          f" — these inflate every listing:")
    for r in gap[:8]:
        if r["measured"] - r["model"] >= 0:
            break
        print(f"    {r['term']:<24} model {r['model']}  measured"
              f" {r['measured']}   in {r['share']:6.1%} of {r['seen']:,} listings")

    print(f"\n  Model scored LOW, market says rare"
          f" — these are the terms that actually separate:")
    for r in reversed(gap[-8:]):
        if r["measured"] - r["model"] <= 0:
            break
        print(f"    {r['term']:<24} model {r['model']}  measured"
              f" {r['measured']}   in {r['share']:6.1%} of {r['seen']:,} listings")

    if unmeasurable:
        print(f"\n  Not measurable here ({len(unmeasurable)}): "
              + ", ".join(unmeasurable[:12])
              + (" ..." if len(unmeasurable) > 12 else ""))
    return {"measurable": len(rows), "exact": len(agree),
            "within_one": len(within), "rho": rho}


def demo():
    """Self-check on synthetic frequencies — no corpus, no files."""
    assert measured_weight(0.32) == 1, "a term in a third of postings is noise"
    assert measured_weight(0.15) == 2
    assert measured_weight(0.06) == 3
    assert measured_weight(0.02) == 4
    assert measured_weight(0.004) == 5, "a term in 0.4% of postings separates"
    assert measured_weight(0.0) == 5

    # Perfectly ordered pairs correlate at 1, reversed at -1.
    assert abs(spearman([(1, 1), (2, 2), (3, 3), (4, 4)]) - 1.0) < 1e-9
    assert abs(spearman([(1, 4), (2, 3), (3, 2), (4, 1)]) + 1.0) < 1e-9
    assert spearman([(1, 1), (1, 1), (1, 1)]) is None, "no variance, no rho"

    # A term is counted only over sweeps whose vocabulary carried it.
    freqs = {"react": (30, 100), "graphql": (1, 100)}
    rows, missing = compare({"react": 5, "graphql": 1}, freqs, min_listings=50)
    by = {r["term"]: r for r in rows}
    assert by["react"]["measured"] == 1 and by["react"]["model"] == 5
    assert by["graphql"]["measured"] == 4 and by["graphql"]["model"] == 1
    assert missing == []

    rows, missing = compare({"rare": 3}, {"rare": (1, 10)}, min_listings=200)
    assert rows == [] and missing == ["rare"], "too few listings to measure"
    print("corpus_weights demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    sys.path.insert(0, REPO)
    import config

    weights = config.SCORING["skill_weights"]
    freqs = corpus_frequencies()
    rows, unmeasurable = compare(weights, freqs)
    if not rows:
        print("No measurable terms — is output/ populated?")
        return
    report(rows, unmeasurable, len(weights))


if __name__ == "__main__":
    main()
