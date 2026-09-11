"""One question: is the rarity signal being suppressed by its denominator?

corpus_signal.separation() divides a term's hits by `seen` — the rows of
every sweep whose PROFILE CARRIED THAT TERM. The docstring explains why:
matched_skills can only name terms the running profile was looking for,
so dividing by the whole corpus would report every term as rarer than it
is, in proportion to how few profiles used it.

That is a real bias and this is its mirror image. A specialist term
carried by one narrow profile is divided by that one narrow sweep, so it
looks common in exactly the market where it is most distinctive:
"queueable apex" is scored only in Salesforce sweeps, where nearly every
listing wants it, and comes out separating nothing. Meanwhile "git" is
carried by most profiles and divided by most of the corpus.

So the alternative denominator is the whole market: hits over every row
scraped, regardless of which profile was looking. Its bias is the one
the original avoided — terms no profile carried look rarer than they
are — and the point of measuring both is to see which bias costs more.

NOTHING ELSE MOVES. The bands are corpus_signal's own, unchanged and
unfitted; the floor is its MIN_LISTINGS; blend(), reweight(),
centrality, the gate, retrieval, keyword ranking and the score formula
are all untouched. The only difference between the two columns below is
what the share is divided by.

    python -m bench.separation_alt --demo
    python -m bench.separation_alt
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

import corpus_signal

HERE = os.path.dirname(os.path.abspath(__file__))


def broad(freqs):
    """freqs re-expressed against the whole market.

    Same hits, one denominator: the largest `seen` any term has, which
    is the corpus as the most widely-carried term saw it. Using the
    maximum rather than a separate row count keeps this comparable with
    the numbers frequencies() already produces and needs no second pass
    over output/.
    """
    if not freqs:
        return {}
    universe = max(seen for _hits, seen in freqs.values())
    return {term: (hits, universe) for term, (hits, _seen) in freqs.items()}


def separation_broad(term, freqs):
    """corpus_signal.separation(), with the market as the denominator."""
    return corpus_signal.separation(term, broad(freqs))


def compare(terms, freqs):
    """[(term, hits, narrow_seen, narrow_sep, broad_sep)] for inspection."""
    wide = broad(freqs)
    out = []
    for term in terms:
        hits, seen = freqs.get(term, (0, 0))
        out.append((term, hits, seen,
                    corpus_signal.separation(term, freqs),
                    corpus_signal.separation(term, wide)))
    return out


def demo():
    # Two terms, same hits, different narrow denominators — which is
    # exactly the asymmetry under test. A specialist carried by one
    # narrow profile is divided by that profile's rows; a commodity
    # carried by everyone is divided by the whole corpus.
    freqs = {"specialist": (100, 400),      # 25% narrow, 0.71% broad
             "commodity": (100, 14000),     # 0.71% under both
             "ubiquitous": (7000, 14000),   # 50% under both
             "thin": (5, 50)}               # under MIN_LISTINGS narrowly
    wide = broad(freqs)
    assert {seen for _h, seen in wide.values()} == {14000}, wide
    assert {t: h for t, (h, _s) in wide.items()} == {
        t: h for t, (h, _s) in freqs.items()}, "hits must not change"

    import corpus_signal as cs
    # The specialist looks common narrowly and rare broadly. That is the
    # whole hypothesis, stated as a test.
    assert cs.separation("specialist", freqs) == 1
    assert cs.separation("specialist", wide) == 5
    # The ubiquitous term separates nothing under either denominator.
    assert cs.separation("ubiquitous", freqs) == 1
    assert cs.separation("ubiquitous", wide) == 1
    # AND THE COST, which is the finding: a commodity term nobody
    # bothers to write down is rare in TEXT and the broad denominator
    # cannot tell that from being specialist. Both score the same here
    # while meaning opposite things.
    assert cs.separation("commodity", wide) == cs.separation("specialist", wide)
    # The floor still applies; a term under MIN_LISTINGS is unmeasured.
    assert cs.separation("thin", freqs) is None
    assert cs.separation("thin", wide) is not None, \
        "the broad denominator lifts thin terms over the floor"

    assert broad({}) == {}
    rows = compare(["specialist", "ubiquitous"], freqs)
    assert [r[0] for r in rows] == ["specialist", "ubiquitous"]
    assert rows[0][3] == 1 and rows[0][4] == 5
    print("separation_alt demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    freqs = corpus_signal.frequencies()
    print(f"  {'term':<28}{'hits':>6}{'narrow':>8}{'broad':>7}")
    for row in compare(sorted(freqs), freqs):
        print(f"  {row[0]:<28}{row[1]:>6}{str(row[3]):>8}{str(row[4]):>7}")


if __name__ == "__main__":
    main()
