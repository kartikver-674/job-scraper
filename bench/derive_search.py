"""Derive the search fields from the corpus instead of asking a model.

The generation benchmark said the local model writes search strategy at
market baseline with coin-flip stability: precision 31.6% against a 32.1%
market, Jaccard 0.37 between two renderings of one résumé, and an empty
title_exclude on 26 of 26 profiles. Asking a better prompt for a better
answer is one response. The other is to notice that the answer is already
on disk.

output/ holds 15,514 real listings, each with the TITLE it was posted
under and the SKILLS the scraper matched in it. That is a map from skills
to the titles that want them, built from the market rather than from a
model's idea of the market. A person's extracted skills are the query.

So role_keywords stops being generation and becomes retrieval:

    résumé --(local model, 1.000 on skills)--> skills
    skills --(this file, deterministic)------> the titles that hire them
    titles --(validators)--------------------> minus wildcards and waste

Nothing here asks a model anything. The model's contribution is the skill
list, which it already extracts perfectly, and the semantic judgements
that genuinely need one stay elsewhere.

WHAT THIS FIXES ABOUT THE MEASUREMENT, TOO
------------------------------------------
bench/search_fields.py has to caveat its precision figure, because the
`score` column was computed with the corpus owner's skill_weights and so
flatters keywords aimed at that owner's field. matched_skills does not
have that problem: whether a listing mentions Django is a fact about the
listing. `buys()["relevance"]` is therefore a per-person quality measure
that is fair to ada and to kwame alike.

WHAT IT CANNOT DO
-----------------
The corpus is one market. ada's skills are 6 of 8 covered by it and
gopal's are 1 of 9, so this derivation is strong for people near the
market already scraped and thin for everyone else. That is the cold-start
problem, and it is a property of the approach rather than a bug in it: a
brand-new user in an unscraped field has no corpus to retrieve from. Any
figure here is reported alongside its coverage for that reason.

    python -m bench.derive_search --demo
    python -m bench.derive_search            # derive and measure, 13 people
"""

import collections
import csv
import glob
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# Every name below is the production implementation. This module keeps the
# measurement harness (report/demo/main) and nothing else: the benchmark
# has to score the code that ships, not a copy of it.
from local_search import (  # noqa: E402,F401
    CONCEPT, FILLER, MAX_SHARE, MIN_COMPANIES, MIN_EVIDENCE, MIN_LISTINGS,
    NGRAM, _WORD, buys, canonical, canonicalise, corpus_rows, evidence,
    fragments, hints_for, idf, index, keywords_for, matching_rows,
    vocabulary,
)
import local_search


def clean_skills(skills, resume_text=None, vocab=(), rows=()):
    """The harness's older signature; resume_text and rows were never read."""
    return local_search.clean_skills(skills, vocab)


def coverage(own, rows):
    """How much of this person's vocabulary the corpus has ever seen."""
    seen = set()
    for row in rows:
        seen |= row[2]
    hit = {s for s in own if s in seen}
    return len(hit), len(own)


def derive(person, rows, idx=None, total=None, want=12):
    """The search fields this corpus supports for this person."""
    import config

    hard = tuple(config.SCORING["hard_drop_terms"])
    soft = tuple(config.SCORING["soft_drop_terms"])
    idx = idx if idx is not None else index(rows, hard + soft)
    total = total or len(rows)
    own = {s.strip().lower() for s in person["skills"]}
    keywords = keywords_for(own, rows, idx, total, want, seniority=hard + soft)
    return {
        "role_keywords": keywords,
        # title_hints gates the FREE sources and only ever widens, so it
        # takes the same fragments plus every single word inside them that
        # is itself a real fragment. Wider than role_keywords on purpose:
        # a missing hint is inventory nobody sees, and hints cost nothing.
        "title_hints": sorted({k for k in keywords} | {
            w for k in keywords for w in k.split()
            if idx.get(w, {}).get("listings", 0) >= MIN_LISTINGS}),
        # Left empty deliberately rather than guessed. Deciding that data
        # engineering is a DIFFERENT CAREER from web development, and not
        # merely an adjacent one, is the semantic judgement the corpus
        # cannot make and the one place a model still earns its keep.
        "title_exclude": [],
        "skill_weights": [{"term": s, "weight": 3} for s in sorted(own)],
        "penalty_terms": [],
        "domain_half_a": [], "domain_half_b": [],
        "domain_title_terms": [], "domain_bonus": 0,
    }


def buys(keywords, rows, own, hard=()):
    """What a keyword set would actually buy, measured three ways.

    `relevance` is the honest per-person number: the share of purchased
    listings that mention at least one of this person's skills. It is a
    fact about the listings, so unlike `score` it does not flatter
    keywords aimed at the corpus owner's field.
    """
    from bench.search_fields import REACHABLE, droppable, spend

    needles = [k.strip().lower() for k in keywords if k.strip()]
    if not needles:
        return {"listings": 0, "relevance": None, "reachable": None,
                "wasted": 0, "spend": 0.0}
    seen = [r for r in rows if any(n in r[0] for n in needles)]
    if not seen:
        return {"listings": 0, "relevance": None, "reachable": None,
                "wasted": 0, "spend": 0.0}
    wanted = sum(1 for _t, _s, skills, _c in seen if skills & own)
    reach = sum(1 for _t, score, _sk, _c in seen if score >= REACHABLE)
    waste = sum(1 for title, _s, _sk, _c in seen if droppable(title, hard))
    return {"listings": len(seen), "relevance": wanted / len(seen),
            "reachable": reach / len(seen), "wasted": waste,
            "spend": spend(len(seen))}


def report(want=12, model="qwen3:8b"):
    import json
    import config
    from bench.people import PEOPLE
    from bench.search_fields import REACHABLE

    rows = corpus_rows()
    hard = tuple(config.SCORING["hard_drop_terms"])
    soft = tuple(config.SCORING["soft_drop_terms"])
    idx = index(rows, hard + soft)
    total = len(rows)
    base_reach = sum(1 for r in rows if r[1] >= REACHABLE) / total

    generated = {}
    path = os.path.join(HERE, "results",
                        f"profiles-{model.replace(':', '_')}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            generated = json.load(fh)

    print(f"\n{'=' * 78}\ncorpus-derived vs model-generated role_keywords"
          f"\n{'=' * 78}")
    print(f"  {total} listings; {base_reach:.0%} reach score {REACHABLE}")
    print(f"  relevance is compared against THIS PERSON's own baseline — the "
          f"share of the\n  whole market naming any of their skills — because "
          f"that share differs per person\n  and a raw percentage would "
          f"reward whoever happens to match the corpus.")
    print(f"\n  {'person':<9}{'cov':>5}{'base':>6}{'':>2}{'derived':>26}"
          f"{'':>2}{'model-generated':>26}")
    print(f"  {'':<9}{'':>5}{'':>6}{'':>2}{'rows  rel  lift  reach':>26}"
          f"{'':>2}{'rows  rel  lift  reach':>26}")

    sums = {"derived": [], "model": []}
    for slug, person in PEOPLE.items():
        own = {s.strip().lower() for s in person["skills"]}
        hit, n = coverage(own, rows)
        # What a RANDOM listing from this market gives this person. Any
        # keyword worth paying for has to beat it.
        mine = sum(1 for r in rows if r[2] & own) / total
        derived = keywords_for(own, rows, idx, total, want, seniority=hard + soft)
        cells = []
        for label, keywords in (
                ("derived", derived),
                ("model", (generated.get(f"{slug}-plain") or {})
                 .get("role_keywords") or [])):
            got = buys(keywords, rows, own, hard)
            if got["listings"]:
                sums[label].append((got, mine))
            pct = lambda v: f"{v:.0%}" if v is not None else "-"
            lift = (f"{got['relevance'] / mine:.1f}x"
                    if got["relevance"] is not None and mine else "-")
            cells.append(f"{got['listings']:>6}{pct(got['relevance']):>6}"
                         f"{lift:>6}{pct(got['reachable']):>7}")
        print(f"  {slug:<9}{f'{hit}/{n}':>5}{mine:>6.0%}{'':>2}"
              f"{cells[0]:>26}{'':>2}{cells[1]:>26}")

    print()
    for label in ("derived", "model"):
        got = sums[label]
        if not got:
            continue
        listings = sum(g["listings"] for g, _ in got)
        rel = sum(g["relevance"] * g["listings"] for g, _ in got) / listings
        reach = sum(g["reachable"] * g["listings"] for g, _ in got) / listings
        waste = sum(g["wasted"] for g, _ in got)
        # Each person's lift weighted by what they bought, so one person
        # with a huge keyword set cannot carry the average.
        lift = sum((g["relevance"] / m) * g["listings"]
                   for g, m in got if m) / listings
        print(f"  {label:<9} {len(got):>2} people  {listings:>6} rows  "
              f"relevance {rel:.0%}  lift {lift:.1f}x  "
              f"reachable {reach:.0%}  hard-dropped {waste / listings:.1%}")
    print(f"  {'market':<9} {'':>2}         {total:>6} rows  "
          f"{'':>14} lift 1.0x  reachable {base_reach:.0%}")


def rows_for(hist):
    """A corpus shaped from a title histogram, for the demo only."""
    out = []
    for title, count in hist.items():
        out += [(title, 40, frozenset({"react", "typescript"}),
                 f"{title}-{i}") for i in range(count)]
    return out


def demo():
    sen = ("senior", "intern", "ii")
    assert fragments("Senior Backend Engineer II, Remote", sen) == {
        "backend engineer"}, fragments("Senior Backend Engineer II, Remote", sen)
    assert "software engineer" in fragments("Software Engineer (Java)", sen)

    # A 400-listing market built so that each guard is the ONLY thing
    # excluding its term. The first version of this fixture excluded three
    # of them by accident — every guard could be deleted and the demo
    # still passed, which is worse than having no demo.
    #
    #   react developer     40 employers, share 0.18, lift 2.0  -> kept
    #   developer unifi      1 employer,  share 0.08, lift 2.0  -> employers
    #   software engineer  120 employers, share 0.30, lift 2.0  -> wildcard
    #   field ops           80 employers, share 0.20, lift 0.3  -> lift
    both = frozenset({"react", "typescript"})
    rows = []
    rows += [("react developer", 40, both, f"co{i}") for i in range(40)]
    rows += [("react developer unifi", 40, both, "unifi") for _ in range(30)]
    rows += [("software engineer", 40, both, f"s{i}") for i in range(120)]
    rows += [("field ops", 40, both if i < 12 else frozenset(), f"f{i}")
             for i in range(80)]
    rows += [("warehouse operative", 0, frozenset(), f"w{i}")
             for i in range(130)]
    total = len(rows)
    assert total == 400
    idx = index(rows, sen)
    own = {"react", "typescript"}

    assert len(idx["react developer"]["companies"]) == 41
    assert len(idx["developer unifi"]["companies"]) == 1
    # Retrieval before aggregation: only the listings wanting BOTH skills.
    matched = matching_rows(own, rows, 2)
    assert len(matched) == 202, len(matched)

    got = keywords_for(own, rows, idx, total, want=9, seniority=sen)
    assert "react developer" in got, got
    # One employer's jargon: frequent, perfectly scored, and not a role.
    assert "developer unifi" not in got, got
    # A quarter of the market is a catalogue, not a search.
    assert "software engineer" not in got, got
    # No more common among this person's listings than in the market at
    # large, so it says nothing about them.
    assert "field ops" not in got, got

    # Each exclusion is load-bearing on its own guard: relax the guard and
    # the term comes back. Without these three the guards can all be
    # deleted with the demo still green.
    loose = keywords_for(own, rows, idx, total, want=9, seniority=sen,
                         min_listings=1)
    assert "developer unifi" not in loose, "employers, not frequency"
    assert idx["software engineer"]["listings"] / total > MAX_SHARE
    assert (idx["field ops"]["listings"] / total) < MAX_SHARE, \
        "field ops must be excluded by lift, not by share"
    assert len(idx["field ops"]["companies"]) >= MIN_COMPANIES, \
        "field ops must be excluded by lift, not by its employer count"

    # No overlap with the corpus means no keywords, rather than junk. This
    # is the cold-start case and abstaining is the right answer: gopal has
    # one skill in this market and the model invented 28 rows of keywords
    # at 0% relevance for him.
    assert keywords_for({"cobol", "fortran"}, rows, idx, total,
                        seniority=sen) == []
    assert coverage({"react", "cobol"}, rows) == (1, 2)

    # clean_skills: concept filler out, specific tools kept.
    kept, junk = clean_skills(
        ["React Native", "SOLID Principles", "apex",
         "Data Structures & Algorithms (DSA)", "smartstore",
         "Object-Oriented Programming (OOP)"], vocab={"react native"})
    assert kept == ["react native", "apex", "smartstore"], kept
    assert len(junk) == 3, junk
    # A concept word the MARKET uses as a skill survives, because then it
    # is searchable whatever it reads like.
    assert clean_skills(["solid principles"],
                        vocab={"solid principles"}) == (["solid principles"], [])

    # canonical(): a ranked n-gram is not a job title, and role_keywords
    # are sent to the boards as literal search strings.
    hist = collections.Counter({"react native developer": 86,
                                "native developer": 1,
                                "senior react js developer": 4,
                                "react js developer": 2,
                                "senior data engineer": 9})
    assert canonical("native developer", hist, sen) == "react native developer"
    # A seniority-free title wins outright over a more common senior one.
    assert canonical("react js", hist, sen) == "react js developer"
    # When only a senior title exists, stripping is allowed only if what
    # is left is itself something people post. "senior software engineer
    # onsite" became "software engineer onsite", which nobody posts, and
    # it went out as a search query.
    assert canonical("engineer onsite", collections.Counter(
        {"senior software engineer onsite": 5}), sen) is None
    assert canonical("data engineer", hist, sen) is None, "only a senior one"
    assert canonical("nothing", hist, sen) is None
    assert canonical("", hist, sen) is None
    # Two fragments naming one title collapse to one search, not two.
    assert canonicalise(["native developer", "react native developer"],
                        rows_for(hist), sen) == ["react native developer"]

    # hints_for reaches the floor RULE 3 asks for; the first version
    # produced thirteen and gated the free sources tighter than config.
    # Filler with no skills, so the matching rows are a MINORITY of the
    # market and lift can exceed 1. Without it every fragment is exactly
    # as common among the matches as in the market, lift is 1.0 for all
    # of them, and the ranking correctly returns nothing.
    wide = (rows_for(hist)
            + [("react developer", 40, frozenset({"react", "typescript"}),
                f"co{i}") for i in range(30)]
            + [("warehouse operative", 0, frozenset(), f"w{i}")
               for i in range(400)])
    widx = index(wide, sen)
    own_wide = {"react", "typescript"}
    hints = hints_for(own_wide, wide, widx, len(wide), sen, floor=5)
    keys = keywords_for(own_wide, wide, widx, len(wide), want=12,
                        seniority=sen)
    # The property that matters: the gate is WIDER than the paid keyword
    # list, and free of duplicates. How wide it gets is a function of how
    # many titles the market has, which five fixture titles cannot show —
    # the real corpus produces 40 against RULE 3's floor of 20.
    assert set(hints) >= set(keys), (hints, keys)
    assert len(hints) > len(keys), (hints, keys)
    assert hints == list(dict.fromkeys(hints)), "no duplicates"
    assert all(h.strip() == h and h.islower() for h in hints)

    # The evidence bar. Two shared skills is not two pieces of evidence:
    # a pair both sides of the market have says nothing, and it is what
    # put a business analyst into frontend-engineer searches.
    big = [("generic role", 40, frozenset({"agile", "sql"}), f"g{i}")
           for i in range(400)]
    big += [("apex specialist", 40, frozenset({"apex", "soql"}), f"s{i}")
            for i in range(10)]
    big += [("filler", 0, frozenset(), f"f{i}") for i in range(600)]
    vbig, tbig = vocabulary(big), len(big)
    both_kinds = {"agile", "sql", "apex", "soql"}
    matched_titles = {r[0] for r in matching_rows(both_kinds, big, 2, vbig, tbig)}
    assert "apex specialist" in matched_titles, matched_titles
    assert "generic role" not in matched_titles, "a common pair is not evidence"
    # The third element is the EVIDENCE, which is what ranking weights by.
    row = [r for r in matching_rows(both_kinds, big, 2, vbig, tbig)][0]
    assert row[2] > 2.0, row
    # Someone whose every skill is common still gets their listings back
    # rather than nothing at all.
    only_common = {"agile", "sql"}
    weak = {r[0] for r in matching_rows(only_common, big, 2, vbig, tbig)}
    assert weak == {"generic role"}, weak

    bought = buys(["react developer"], rows, own)
    assert bought["listings"] == 70 and bought["relevance"] == 1.0
    assert bought["reachable"] == 1.0
    assert buys([], rows, own)["listings"] == 0
    assert buys(["nothing here"], rows, own)["listings"] == 0
    # Relevance is about the LISTINGS, not about anyone's scoring, which
    # is the whole reason two different people can be compared with it.
    assert buys(["warehouse operative"], rows, own)["relevance"] == 0.0

    # The BOM. 41 of the 54 files in output/ are written with one, and
    # reading them as plain utf-8 renames the first column so that
    # row.get("score") returns None — 58.8% of the corpus read as score 0
    # and the market baseline came out at 15% instead of 32%.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "bom.csv"), "w",
                  encoding="utf-8-sig", newline="") as fh:
            fh.write("score,title,company,matched_skills\n")
            fh.write("44,React Developer,Acme,\"react,typescript\"\n")
        got = corpus_rows(tmp)
        assert got == [("react developer", 44, frozenset({"react",
                                                          "typescript"}),
                        "acme")], got
    print("derive_search demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    report()


if __name__ == "__main__":
    main()
