"""Can a brand-new user get a useful search strategy with no corpus?

bench/derive_search.py retrieves role_keywords from output/, which works
where the market has been scraped and returns nothing where it has not:
dmitri and gopal got no keywords at all. That is honest and useless to
someone signing up today, and "provide a Gemini key" is the answer we are
trying to delete.

Three sources are available offline, in increasing cost:

  1. THE RÉSUMÉ ITSELF. The person's own job titles are role keywords —
     "QA Automation Lead", "SDET", "Test Engineer" are what gopal should
     be searching, and they are on his page. This needs no corpus and no
     model beyond the extraction that already scores 0.905 on titles.
  2. THE SEED CORPUS. Whatever listings Sweep has already scraped, shipped
     with the tool. A new user in a covered field gets warm-start quality
     on day one.
  3. THE LOCAL MODEL, for adjacent roles a résumé does not name.

The measurement below asks which of these are actually needed, because
the cheapest one may be enough and the expensive ones have to earn it.

HOW COLD START IS SIMULATED HONESTLY
------------------------------------
Two ways, and they answer different questions.

  holdout   every listing mentioning ANY of this person's skills is
            removed from the corpus before deriving. That is a person
            whose profession the market has genuinely never seen, and it
            is the real test — measuring gopal, who is already
            uncovered, only tells us about gopal.
  subsample  derive from 5%, 10%, 25%, 50% of the corpus, to find how
            much seed data is actually needed before quality saturates.

Evaluation always uses the FULL corpus, including for holdout. The corpus
is the measuring instrument; withholding it from the derivation is the
experiment, and withholding it from the scoring would just be measuring
less.

    python -m bench.cold_start --demo
    python -m bench.cold_start
"""

import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

from bench import derive_search as ds

from local_search import from_resume, validated  # noqa: F401


def holdout(rows, own):
    """The corpus as it would look if this person's field had never been
    scraped: every listing mentioning any of their skills, removed."""
    return [r for r in rows if not (r[2] & own)]


def subsample(rows, fraction, seed=0):
    """A smaller corpus, sampled without replacement."""
    if fraction >= 1.0:
        return list(rows)
    picked = random.Random(seed).sample(rows, int(len(rows) * fraction))
    return picked


def strategies(person, rows, idx, total, seniority, want=12):
    """The candidate strategies for one person, cheapest first."""
    own = {s.strip().lower() for s in person["skills"]}
    resume = from_resume(person, seniority)
    warm = ds.keywords_for(own, rows, idx, total, want, seniority=seniority)

    # A field-holdout column was here and it measured nothing. Removing
    # every listing that mentions any of the person's skills guarantees
    # skill-retrieval returns zero, because the retrieval step looks for
    # exactly those skills — it restated the premise. gopal and dmitri
    # already show the real behaviour: no coverage, no keywords.
    union = list(resume)
    for k in warm:
        if not any(k in got or got in k for got in union):
            union.append(k)
    return {"résumé only": resume, "corpus only": warm,
            "résumé + corpus": union}


def report(want=12):
    import config
    from bench.people import PEOPLE
    from bench.search_fields import REACHABLE

    rows = ds.corpus_rows()
    hard = tuple(config.SCORING["hard_drop_terms"])
    soft = tuple(config.SCORING["soft_drop_terms"])
    seniority = hard + soft
    idx = ds.index(rows, seniority)
    total = len(rows)
    base_reach = sum(1 for r in rows if r[1] >= REACHABLE) / total

    order = ["résumé only", "corpus only", "résumé + corpus"]
    sums = {name: [] for name in order}
    empty = {name: [] for name in order}

    print(f"\n{'=' * 76}\ncold start: a new user whose field the corpus has "
          f"never seen\n{'=' * 76}")
    print(f"  {total} listings, {base_reach:.0%} reach score {REACHABLE}. "
          f"Evaluation always uses the FULL\n  corpus; only the derivation "
          f"is starved.\n")
    print(f"  {'person':<9}{'':<2}{'strategy':<26}{'kw':>4}{'rows':>7}"
          f"{'rel':>6}{'lift':>6}{'reach':>7}")
    for slug, person in PEOPLE.items():
        own = {s.strip().lower() for s in person["skills"]}
        mine = sum(1 for r in rows if r[2] & own) / total
        got = strategies(person, rows, idx, total, seniority, want)
        first = True
        for name in order:
            keywords = validated(got[name], rows, seniority)
            bought = ds.buys(keywords, rows, own, hard)
            if not keywords:
                empty[name].append(slug)
            if bought["listings"]:
                sums[name].append((bought, mine))
            pct = lambda v: f"{v:.0%}" if v is not None else "-"
            lift = (f"{bought['relevance'] / mine:.1f}x"
                    if bought["relevance"] is not None and mine else "-")
            print(f"  {slug if first else '':<9}{'':<2}{name:<26}"
                  f"{len(keywords):>4}{bought['listings']:>7}"
                  f"{pct(bought['relevance']):>6}{lift:>6}"
                  f"{pct(bought['reachable']):>7}")
            first = False
        print()

    print(f"  {'strategy':<26}{'people':>8}{'no kw':>7}{'rows':>8}"
          f"{'rel':>6}{'lift':>6}{'reach':>7}{'drop':>7}")
    for name in order:
        got = sums[name]
        if not got:
            print(f"  {name:<26}{0:>8}{len(empty[name]):>7}")
            continue
        listings = sum(g["listings"] for g, _ in got)
        rel = sum(g["relevance"] * g["listings"] for g, _ in got) / listings
        reach = sum(g["reachable"] * g["listings"] for g, _ in got) / listings
        lift = sum((g["relevance"] / m) * g["listings"]
                   for g, m in got if m) / listings
        waste = sum(g["wasted"] for g, _ in got)
        print(f"  {name:<26}{len(got):>8}{len(empty[name]):>7}{listings:>8}"
              f"{rel:>5.0%}{lift:>5.1f}x{reach:>7.0%}"
              f"{waste / listings:>6.1%}")
    print(f"  {'the market':<26}{'':>8}{'':>7}{total:>8}{'':>5}{1.0:>5.1f}x"
          f"{base_reach:>7.0%}")


def curve(fractions=(0.02, 0.05, 0.1, 0.25, 0.5, 1.0), want=12, seed=0):
    """How much seed corpus is needed before quality stops improving.

    The product question behind this is how large a corpus has to ship
    with Sweep for a new user to get a warm start. Derivation runs on the
    sample; evaluation always runs on the full corpus.
    """
    import config
    from bench.people import PEOPLE
    from bench.search_fields import REACHABLE

    rows = ds.corpus_rows()
    hard = tuple(config.SCORING["hard_drop_terms"])
    seniority = hard + tuple(config.SCORING["soft_drop_terms"])
    total_full = len(rows)
    base = sum(1 for r in rows if r[1] >= REACHABLE) / total_full

    print(f"\n{'=' * 76}\nhow much seed corpus a new user needs\n{'=' * 76}")
    print(f"  derivation runs on the sample, scoring on all {total_full} "
          f"listings ({base:.0%} reachable)\n")
    print(f"  {'sample':>8}{'listings':>10}{'people served':>15}"
          f"{'rows':>8}{'rel':>6}{'reach':>7}")
    for fraction in fractions:
        sample = subsample(rows, fraction, seed)
        idx = ds.index(sample, seniority)
        served, got = 0, []
        for person in PEOPLE.values():
            own = {s.strip().lower() for s in person["skills"]}
            keywords = validated(
                ds.keywords_for(own, sample, idx, len(sample), want,
                                seniority=seniority), rows, seniority)
            if not keywords:
                continue
            bought = ds.buys(keywords, rows, own, hard)
            if bought["listings"]:
                served += 1
                got.append(bought)
        if not got:
            print(f"  {fraction:>7.0%}{len(sample):>10}{served:>15}")
            continue
        listings = sum(g["listings"] for g in got)
        rel = sum(g["relevance"] * g["listings"] for g in got) / listings
        reach = sum(g["reachable"] * g["listings"] for g in got) / listings
        print(f"  {fraction:>7.0%}{len(sample):>10}{served:>15}"
              f"{listings:>8}{rel:>6.0%}{reach:>7.0%}")


def demo():
    sen = ("senior", "lead", "intern", "ii")
    person = {"skills": ["react"], "employment": [
        {"title": "Senior Backend Engineer", "relevant": True},
        {"title": "Full-Stack Developer / React Native", "relevant": True},
        {"title": "Structural Engineer", "relevant": False},
        {"title": "Software Engineering Intern", "relevant": True},
        {"title": "Engineer", "relevant": True},
    ]}
    got = from_resume(person, sen)
    assert "backend engineer" in got, got
    # A compound title is two searchable roles, not one string.
    assert "full stack developer" in got and "react native" in got, got
    # The career they left is not what they are searching for, and the
    # internship is not a role they held — the same two clauses the years
    # derivation applies, for the same reason.
    assert "structural engineer" not in got, got
    assert not any("intern" in g for g in got), got
    # One word is a fragment, not a role: "engineer" alone buys the market.
    assert "engineer" not in got, got
    assert got == list(dict.fromkeys(got)), "no duplicates"

    rows = [("react developer", 40, frozenset({"react"}), "a"),
            ("warehouse operative", 0, frozenset(), "b")]
    kept = holdout(rows, {"react"})
    assert [r[0] for r in kept] == ["warehouse operative"]
    assert holdout(rows, set()) == rows

    assert len(subsample(rows * 50, 0.5)) == 50
    assert subsample(rows, 1.0) == rows
    # Deterministic, so a curve is reproducible rather than noise.
    assert subsample(rows * 50, 0.2, seed=7) == subsample(rows * 50, 0.2, seed=7)

    # A résumé title can itself be a wildcard: lena's is "Software
    # Engineer", which buys a third of the market. The validator is what
    # stops the cheapest strategy from also being the most expensive.
    market = [("software engineer", 40, frozenset({"react"}), f"c{i}")
              for i in range(40)]
    market += [("react developer", 40, frozenset({"react"}), f"d{i}")
               for i in range(10)]
    assert validated(["software engineer"], market, sen) == []
    assert validated(["react developer"], market, sen) == ["react developer"]
    # The two sources overlap and the union must not buy one search twice.
    assert validated(["react developer", "React Developer",
                      "react developer"], market, sen) == ["react developer"]
    print("cold_start demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    report()
    curve()


if __name__ == "__main__":
    main()
