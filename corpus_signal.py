"""Skill importance measured from the jobs already scraped, not guessed.

make_profile's RULE 1 tells the model to "WEIGHT BY DISCRIMINATIVE POWER,
NOT CENTRALITY" — a term that also appears in jobs you do NOT want must
score low however central it is to your work. That is a claim about a job
market, and until now it was answered from a language model's prior.

output/ is the market. Every scraped listing carries a `matched_skills`
column naming which of the profile's terms that posting actually contained,
so document frequency over those rows measures discriminative power for the
exact searches this user runs.

Measuring is not the whole answer, and the POC that led here showed why:
rarity alone put `opencv` — a term the résumé mentions once, in 0.2% of
listings — at the top of a React Native developer's weights, which would
float every computer-vision job to the top of their shortlist. Rarity says
how much a term SEPARATES; only the résumé says whether it is the
candidate's. blend() combines the two.

    python -m corpus_signal            # what the corpus says about config
    python -m corpus_signal --demo     # self-check, no corpus needed
"""

import collections
import csv
import glob
import math
import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# A term seen across fewer listings than this has no measurable frequency —
# one lucky posting would move it two whole steps — so the corpus abstains
# rather than voting on noise.
MIN_LISTINGS = 200

# Frequency bands, log-spaced. The interesting range is the bottom decade:
# the difference between 0.4% and 4% decides a shortlist, while the one
# between 30% and 40% is two terms that both separate nothing.
BANDS = ((0.20, 1), (0.10, 2), (0.04, 3), (0.01, 4))


def frequencies(output_dir=None):
    """{term: (listings containing it, listings scored for it)}.

    The denominator is per TERM. `matched_skills` can only name terms the
    profile that ran the sweep was carrying, so a term missing from a
    profile was never looked for in those listings — counting it as absent
    would report every term as rarer than it is, in proportion to how few
    profiles used it.
    """
    output_dir = output_dir or os.path.join(REPO_ROOT, "output")
    per_sweep = collections.defaultdict(
        lambda: {"rows": 0, "hits": collections.Counter(), "vocab": set()})

    for path in glob.glob(os.path.join(output_dir, "**", "*.csv"),
                          recursive=True):
        sweep = per_sweep[os.path.basename(os.path.dirname(path))]
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                if "matched_skills" not in (reader.fieldnames or ()):
                    continue
                for row in reader:
                    sweep["rows"] += 1
                    terms = {t.strip().lower()
                             for t in (row.get("matched_skills") or "").split(",")
                             if t.strip()}
                    sweep["vocab"] |= terms
                    for term in terms:
                        sweep["hits"][term] += 1
        except (OSError, UnicodeDecodeError, csv.Error):
            # One unreadable file must not take the whole measurement down.
            continue

    out = {}
    for sweep in per_sweep.values():
        for term in sweep["vocab"]:
            hits, seen = out.get(term, (0, 0))
            out[term] = (hits + sweep["hits"][term], seen + sweep["rows"])
    return out


def vocabulary(output_dir=None, freqs=None):
    """{term: listings naming it} — the market's own skill vocabulary.

    Derived from frequencies() rather than read again: that function
    already walks output/ and counts every term in matched_skills, and
    a second reader would be a second thing to keep correct.
    """
    freqs = freqs if freqs is not None else frequencies(output_dir)
    return {term: hits for term, (hits, _seen) in freqs.items() if hits}


def separation(term, freqs):
    """How much this term narrows the market, 1-5, or None when unmeasured."""
    hits, seen = freqs.get(term, (0, 0))
    if seen < MIN_LISTINGS:
        return None
    share = hits / seen
    for floor, weight in BANDS:
        if share >= floor:
            return weight
    return 5


def blend(centrality, sep):
    """One weight from what the résumé says and what the market says.

    The geometric mean, rounded, which is the same shape as tf-idf: two
    independent multiplicative factors averaged in log space. It has the
    three properties the POC's failures asked for —

      strong AND rare      -> stays high     (maven  4x5 -> 4, 5x5 -> 5)
      strong but commodity -> falls          (react  4x1 -> 2)
      rare but incidental  -> stays low      (opencv 1x5 -> 2)

    — where taking the market's word alone gets the third one badly wrong,
    and taking the résumé's word alone gets the second one wrong, which is
    what RULE 1 was written about. Top marks need both, which is the point:
    5 is reserved for a term the candidate is strongest in AND the market
    rarely asks for.

    round() is Python's banker's rounding, which is safe here only because
    sqrt(c*s) is never exactly x.5 for integer c*s — (n+0.5)**2 is never a
    whole number.

    An unmeasured term keeps its stated weight: the corpus abstaining is
    not a vote for the middle.

    So does a weight outside 1-5. RULE 1 defines that scale in prose but
    RESPONSE_SCHEMA bounds `weight` only to "integer", and the renderer
    passes whatever the model sends straight through — a weight of 10 is
    not on this scale, so the corpus has nothing comparable to say about
    it. Squashing it to 2 would be re-bounding the scale, which is a
    different change from measuring importance.
    """
    if sep is None or not (1 <= centrality <= 5):
        return centrality
    return max(1, min(5, round(math.sqrt(int(centrality) * sep))))


def reweight(weights, freqs):
    """`weights` with each term blended against the corpus.

    Returns (blended, moved) where `moved` is [(term, before, after, share)]
    for the terms that changed, so a caller can say what it did rather than
    silently rewriting the numbers that decide a ranking.
    """
    blended, moved = {}, []
    for term, stated in weights.items():
        sep = separation(term, freqs)
        after = blend(stated, sep)
        blended[term] = after
        if after != stated:
            hits, seen = freqs.get(term, (0, 0))
            moved.append((term, stated, after, hits / seen if seen else None))
    moved.sort(key=lambda m: (m[2] - m[1], m[0]))
    return blended, moved


def title_yield(output_dir=None):
    """{keyword-free} per title: how many listings it drew and how they scored.

    role_keywords are what a paid sweep BUYS — one search per keyword per
    location — so their value is measurable in a way skill weights are not:
    a keyword that returned 4,890 listings averaging 3.6 cost far more than
    one that returned 329 averaging 14.2 and was worth less.

    Returns [{title, listings, mean_score}] over whole scraped titles. The
    caller matches its own keywords against these; this stays a measurement
    rather than deciding what counts as a match.
    """
    output_dir = output_dir or os.path.join(REPO_ROOT, "output")
    rows = []
    for path in glob.glob(os.path.join(output_dir, "**", "*.csv"),
                          recursive=True):
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                if not reader.fieldnames or "title" not in reader.fieldnames:
                    continue
                for row in reader:
                    title = (row.get("title") or "").strip().lower()
                    if not title:
                        continue
                    try:
                        score = int(float(row.get("score") or 0))
                    except (TypeError, ValueError):
                        score = 0
                    rows.append((title, score))
        except (OSError, UnicodeDecodeError, csv.Error):
            continue
    return rows


def keyword_yield(keyword, titles):
    """(listings drawn, mean score) for one search keyword.

    Substring, not whole-word: a keyword is a SEARCH string and the boards
    match it as one, so "mobile" legitimately draws "Mobile Engineer II".
    """
    needle = keyword.strip().lower()
    hit = [score for title, score in titles if needle and needle in title]
    if not hit:
        return 0, None
    return len(hit), sum(hit) / len(hit)


def spearman(pairs):
    """Rank correlation, ties averaged. Reports agreement without numpy."""
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


def demo():
    # -- bands -------------------------------------------------------------
    freqs = {"react": (3340, 10000), "docker": (1570, 10000),
             "express": (540, 10000), "postgres": (130, 10000),
             "maven": (50, 10000), "thin": (1, 10)}
    assert separation("react", freqs) == 1, "a third of postings separates nothing"
    assert separation("docker", freqs) == 2
    assert separation("express", freqs) == 3
    assert separation("postgres", freqs) == 4
    assert separation("maven", freqs) == 5
    assert separation("thin", freqs) is None, "too few listings to measure"
    assert separation("never seen", freqs) is None

    # -- the three cases the POC's failures asked for -----------------------
    assert blend(5, 5) == 5, "strongest and rarest is the only 5"
    assert blend(4, 5) == 4, "strong and rare stays high"
    assert blend(4, 1) == 2, "strong but in every posting separates nothing"
    assert blend(1, 5) == 2, "rare but incidental must not top the list"
    assert blend(5, 3) == 4
    # The ordering that matters: a term you are strong in and the market
    # rarely asks for must outrank one you barely mention.
    assert blend(5, 4) > blend(1, 5)
    # No exact .5 to round, so banker's rounding cannot surprise us.
    for c in range(1, 6):
        for s_ in range(1, 6):
            assert (math.sqrt(c * s_) % 1) != 0.5
    # Abstention is not a vote for the middle.
    assert blend(5, None) == 5 and blend(1, None) == 1
    # Off the scale RULE 1 defines, so off this function's remit. The
    # renderer has always passed these through; re-bounding the scale is a
    # different change from measuring importance.
    assert blend(10, 1) == 10 and blend(0, 5) == 0 and blend(-3, 5) == -3
    # Never outside the scale the scorer understands.
    for c in range(1, 6):
        for s in list(range(1, 6)) + [None]:
            assert 1 <= blend(c, s) <= 5

    # -- reweight reports what it changed ----------------------------------
    blended, moved = reweight({"react": 5, "maven": 4, "thin": 2}, freqs)
    assert blended == {"react": 2, "maven": 4, "thin": 2}
    assert [m[0] for m in moved] == ["react"], "only the term that moved"
    assert moved[0][1:3] == (5, 2)

    assert abs(spearman([(1, 1), (2, 2), (3, 3)]) - 1.0) < 1e-9
    assert spearman([(2, 2), (2, 2), (2, 2)]) is None

    # -- title yield -------------------------------------------------------
    titles = [("senior react native developer", 40), ("react native engineer", 20),
              ("software engineer", 0), ("software engineer ii", 4)]
    n, mean = keyword_yield("react native", titles)
    assert (n, mean) == (2, 30.0)
    broad, broad_mean = keyword_yield("software engineer", titles)
    assert (broad, broad_mean) == (2, 2.0), "a broad keyword draws weaker matches"
    assert keyword_yield("cobol", titles) == (0, None), "no listings, no opinion"
    # Substring on purpose: a keyword is what the board is asked for.
    assert keyword_yield("mobile", [("mobile engineer ii", 9)]) == (1, 9.0)
    # -- the BOM -----------------------------------------------------------
    # 41 of the 54 files in output/ are written with a byte-order mark, so
    # the first column's NAME carries it and row.get("score") returns None.
    # Read as plain utf-8, 58.8% of the corpus came back as score 0 and the
    # market baseline read 15% instead of 32% — every precision figure
    # measured against this corpus divides by that.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "bom.csv"), "w",
                  encoding="utf-8-sig", newline="") as fh:
            fh.write("score,title,matched_skills\n44,React Developer,react\n")
        rows = title_yield(tmp)
        assert rows == [("react developer", 44)], rows
        assert frequencies(tmp)["react"][0] == 1

    # vocabulary() is frequencies() with the denominator dropped, so
    # a term counted zero times is not in the market's vocabulary.
    assert vocabulary(freqs={"react": (30, 100), "cobol": (0, 100)}) == {
        "react": 30}
    assert vocabulary(freqs={}) == {}

    print("corpus_signal demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    sys.path.insert(0, REPO_ROOT)
    import config

    freqs = frequencies()
    stated = config.SCORING["skill_weights"]
    blended, moved = reweight(stated, freqs)

    measured = [(w, separation(t, freqs)) for t, w in stated.items()
                if separation(t, freqs) is not None]
    rho = spearman(measured)
    print(f"corpus: {len(freqs)} terms measured across output/")
    print(f"profile: {len(stated)} terms, {len(measured)} of them measurable")
    print(f"agreement with the market before blending: "
          + (f"{rho:+.2f}" if rho is not None else "n/a"))
    print(f"terms the blend moves: {len(moved)}")
    after = spearman([(blended[t], separation(t, freqs)) for t in stated
                      if separation(t, freqs) is not None])
    print(f"agreement after blending:                  "
          + (f"{after:+.2f}" if after is not None else "n/a") + "\n")
    for term, before, after_w, share in moved:
        arrow = "down" if after_w < before else "up  "
        pct = f"{share:6.1%}" if share is not None else "   n/a"
        print(f"  {arrow} {before} -> {after_w}  {term:<24} {pct} of listings")

    titles = title_yield()
    print(f"\nrole_keywords, by what they actually drew ({len(titles):,} listings):")
    for kw in config.SEARCH["role_keywords"]:
        n, mean = keyword_yield(kw, titles)
        if n:
            print(f"  {kw:<34} {n:>5} listings   mean score {mean:>5.1f}")
        else:
            print(f"  {kw:<34} {'—':>5} never drew a listing here")


if __name__ == "__main__":
    main()
