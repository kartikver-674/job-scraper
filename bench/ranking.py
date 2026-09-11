"""Which keyword goes first, and why — the only thing this file decides.

The live comparison found the local pipeline had the right keywords and
ran the wrong ones. Lovish is a Salesforce developer and "salesforce
developer" was ninth of nine; Kanav is a React Native engineer and
"react native developer" was eleventh of twelve. A four-combo budget cap
discarded both, so a sweep that could have found five Salesforce roles
found none. Gemini leads with the person's identity; this ranks by
corpus lift, which puts "sde ii, amazon now" above a career.

So the ordering is explicit, and it is a policy about EVIDENCE rather
than about any profession:

  1  HELD      a title this person actually held, from employment the
               years derivation counts as relevant. The strongest claim
               anyone can make about what they are, and it is on the
               page rather than inferred.
  2  ANCHORED  a keyword recovered from a discriminative skill whose
               market wants it — the orphan mechanism's output. Second
               because it is inferred from evidence rather than stated,
               and the anchor already had to clear the lift and
               anchor-evidence bars to exist.
  3  CORPUS    everything the corpus ranking produced. Good keywords,
               but ranked by how a market behaves in aggregate rather
               than by what this person is.

Nothing is added, removed or re-validated here. Every keyword arriving
has already passed the wildcard, seniority, hard-drop, employer-count
and relevance guards; this only decides the order they go out in, and
deduplicates across tiers so a title held AND recovered is bought once.

    python -m bench.ranking --demo
"""

import sys

from local_search import TIERS, explain, rank  # noqa: F401
from local_search import ranked_keywords as keywords  # noqa: F401


def demo():
    held = ["solutions developer", "software developer"]
    anchored = [("react native developer", "react native", 2.88),
                ("solutions developer", "x", 9.9)]
    corpus = ["mern stack developer", "software developer", "node js"]

    got = rank(held, anchored, corpus)
    assert keywords(got) == ["solutions developer", "software developer",
                             "react native developer",
                             "mern stack developer", "node js"], keywords(got)
    # Held titles come first, in the order given.
    assert [t for _k, t, _w in got][:2] == [1, 1]
    # The specialist recovery outranks the corpus ranking, which is the
    # whole point: a budget cap must not reach it last.
    assert got[2][1] == 2 and "react native" in got[2][2]
    # Bought once, at the best tier it was offered at.
    assert sum(1 for k, *_ in got if k == "solutions developer") == 1
    assert sum(1 for k, *_ in got if k == "software developer") == 1
    assert [k for k, t, _w in got if t == 1] == ["solutions developer",
                                                 "software developer"]

    # Nothing is invented and nothing is dropped.
    assert set(keywords(got)) == {k.lower() for k in
                                  held + [a[0] for a in anchored] + corpus}
    assert rank([], [], []) == []
    assert keywords(rank([], [], ["only"])) == ["only"]
    # Blank and duplicate input cannot produce a blank or duplicate slot.
    assert keywords(rank(["", "  ", "a"], [], ["A"])) == ["a"]
    # A bare string in the anchored list still ranks, without a reason
    # it cannot know.
    assert keywords(rank([], ["plain"], [])) == ["plain"]

    text = explain(got, limit=2)
    assert "would run" in text and text.count("would run") == 2
    assert "[held" in text and "[anchored" in text and "[corpus" in text
    print("ranking demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        sys.exit(__doc__)
