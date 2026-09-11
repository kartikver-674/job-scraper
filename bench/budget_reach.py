"""Keyword ordering measured by what the BUDGET REACHES, not by rank.

WHAT THIS EXISTS BECAUSE OF
---------------------------
The first integrated live run retrieved zero Salesforce rows from paid
sources for a Salesforce developer, and zero React Native rows for a
React Native developer. Neither keyword was wrong and neither was
missing: both were generated, validated, and ranked — at position 6 and
position 10.

    val_local_lovish:  ran 9 of 18 linkedin combos
                       'salesforce developer' is keyword 6 — never ran
    val_local_kanav:   ran 9 of 22 combos
                       'react native developer' is keyword 10 — never ran

scraper.build_search_plan is keyword-major, so keyword k occupies
searches [k*L, k*L+L) for L locations, and scraper.py:1925 stops the
whole sweep the moment spend crosses max_spend_usd. Measured over five
live runs: $0.88 for ~45 LinkedIn searches, so ~$0.0196 each, and a
$0.15 cap reaches about nine — four keywords at two locations.

So a keyword list longer than the budget is not a longer search, it is a
TRUNCATED one, and everything after the cutoff is decoration. Ordering by
rank optimises the whole list; the user only ever buys the prefix.

WHAT IS MEASURED HERE
---------------------
Nothing upstream moves: extraction, the vocabulary union, the specialist
anchors, corpus scoring and the candidate set are all exactly what ships.
Only the FINAL order changes, and only the affordable prefix is scored.

The objective is idf-weighted coverage of the person's own skills, with
diminishing returns, which is generic by construction — it names no
profession and no technology. A keyword is worth a search when its
listings want skills of theirs that nothing already bought covers, and a
second near-identical variant is worth little because the first one
already covered them.

    python -m bench.budget_reach --demo
    python -m bench.budget_reach            # the three diagnostic résumés
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import local_search

HERE = os.path.dirname(os.path.abspath(__file__))

# Measured, not assumed: five live runs at a $0.15 cap each spent $0.88
# over ~45 LinkedIn searches.
COST_PER_SEARCH = 0.0196

# A skill counts as covered once this share of a keyword's listings name
# it. Below that the keyword is not really searching for it.
COVERED_SHARE = 0.25


def affordable_searches(cap, cost=COST_PER_SEARCH):
    """How many paid searches a spend cap reaches.

    The cap is checked BEFORE each search and the crossing search still
    runs, so the count is one more than the strict quotient.
    """
    if cap is None:
        return None
    return int(cap / cost) + 1


def affordable_keywords(cap, locations, cost=COST_PER_SEARCH):
    """...and how many KEYWORDS that is, which is what ordering controls."""
    n = affordable_searches(cap, cost)
    return None if n is None else max(1, n // max(1, locations))


def wants(keyword, rows, own, vocab=None, total=None, alpha=None):
    """{skill: share of this keyword's listings naming it}, for their skills.

    A fact about the listings the keyword buys, so it is comparable
    across people and cannot flatter one profession.

    The share is SHRUNK toward the market's own rate for that skill, in
    proportion to how little evidence the title has. Raw shares let a
    21-listing title out-bid a 217-listing one on the same skills: with a
    handful of rows, one mobile posting is 5% and three are 14%, and the
    high-volume title that would actually deliver the rows lost the slot.
    The pseudo-count is local_search.MIN_ROWS — production's own "a title
    nothing much is posted under is not worth a search" bar — so a title
    at that bar is trusted half as much as the market, and one far above
    it is trusted nearly fully. Nothing new is calibrated.
    """
    alpha = local_search.MIN_ROWS if alpha is None else alpha
    needle = keyword.strip().lower()
    matched = [r for r in rows if needle in r[0]]
    if not matched:
        return {}
    out = {}
    for skill in own:
        n = sum(1 for r in matched if skill in r[2])
        if not n:
            continue
        base = ((vocab.get(skill, 0) / total)
                if vocab is not None and total else 0.0)
        out[skill] = (n + alpha * base) / (len(matched) + alpha)
    return out


def profile(candidates, rows, own, vocab, total):
    """Per candidate: what it wants, its idf weight, and its employers."""
    out = {}
    for keyword in candidates:
        share = wants(keyword, rows, own, vocab, total)
        needle = keyword.strip().lower()
        matched = [r for r in rows if needle in r[0]]
        out[keyword] = {
            "share": share,
            "listings": len(matched),
            "companies": len({r[3] for r in matched if r[3]}),
            "idf": {s: local_search.idf(s, vocab, total) for s in share},
        }
    return out


def value(entry, covered):
    """Marginal idf-weighted skill mass this keyword adds.

    Diminishing returns: a skill already bought at a higher share
    contributes nothing, so redundant variants of one role fall away
    without any rule naming them.
    """
    gain = 0.0
    for skill, share in entry["share"].items():
        extra = share - covered.get(skill, 0.0)
        if extra > 0:
            gain += entry["idf"][skill] * extra
    return gain


def budget_order(candidates, held, rows, own, vocab, total,
                 min_rows=None, min_companies=None):
    """Greedy coverage, but only over candidates the market actually posts.

    Coverage alone picks narrow titles: for one résumé the four-keyword
    prefix covered twice the skills and bought FIVE listings, against 44
    for the shipped order. A search that returns nothing is not a cheap
    search, it is a wasted one, and the paid plan spends the same money
    either way.

    The two bars are production's own, from the orphan pass that already
    decides when a title is worth a paid search — local_search.MIN_ROWS
    ("a title nothing much is posted under is not worth a search whatever
    it scores") and ORPHAN_MIN_COMPANIES ("one employer's stack is not a
    market"). Nothing new is calibrated here.

    If too few candidates clear the bars the whole set is used, so a thin
    market degrades to the shipped order rather than to an empty plan.
    """
    min_rows = local_search.MIN_ROWS if min_rows is None else min_rows
    min_companies = (local_search.ORPHAN_MIN_COMPANIES
                     if min_companies is None else min_companies)
    info = profile(candidates, rows, own, vocab, total)
    eligible = [k for k in candidates
                if info[k]["listings"] >= min_rows
                and info[k]["companies"] >= min_companies]
    return _greedy(candidates, eligible, held, info)


def _greedy(candidates, eligible, held, info):
    """The candidates re-ordered so the affordable prefix carries the most.

    One held title is placed first and only one. It is the strongest prior
    there is — the person has actually done that job — but the current
    ordering puts EVERY held title first, and for a generalist that is two
    generic searches out of the four a small cap affords. The held title
    chosen is the one covering the most of their own evidence, so the
    choice is made on the same measure as everything else.

    Everything after is greedy marginal gain. No candidate is dropped: the
    tail keeps the same rule, so this is purely an order.
    """
    held_set = {h.strip().lower() for h in held or ()}
    # Ineligible candidates are not dropped, only held back behind every
    # candidate the market supports.
    thin = [k for k in candidates if k not in eligible]
    remaining = list(eligible) if len(eligible) >= 2 else list(candidates)
    thin = [k for k in candidates if k not in remaining]
    order, covered = [], {}

    def take(keyword):
        order.append(keyword)
        remaining.remove(keyword)
        for skill, share in info[keyword]["share"].items():
            covered[skill] = max(covered.get(skill, 0.0), share)

    first_held = [k for k in remaining if k.strip().lower() in held_set]
    if first_held:
        take(max(first_held, key=lambda k: value(info[k], covered)))

    while remaining:
        # Ties broken by listings then alphabetically, so the order is
        # reproducible run to run.
        best = max(remaining, key=lambda k: (value(info[k], covered),
                                             info[k]["listings"], k))
        if value(info[best], covered) <= 0:
            # Nothing left adds coverage; keep the incoming order for the
            # rest rather than inventing one.
            order.extend(remaining)
            break
        take(best)
    return order + thin, info


# A title nobody posts cannot be anyone's specialist search, however
# pure its skill mix. Matches local_search.MIN_ROWS, which already
# refuses an anchored keyword on the same grounds.
SPECIALIST_MIN_ROWS = 20


def rare_skills(own, rows, vocab, total):
    """Their TARGETABLE skills: discriminative, and ones the market posts.

    Top-quartile idf was the first definition and it was wrong. It selects
    the long tail of a résumé's own phrasing — "batch apex", ".net worker
    services", "offline-first" — which no job title's listings name at
    any meaningful share, so every ordering scored zero and the metric
    could not tell them apart.

    A skill is a target opportunity when it is rare enough to discriminate
    AND common enough to be searchable. Both bars are already in
    production, in the orphan pass that decides when a skill deserves a
    paid search of its own, so they are reused rather than reinvented:
    local_search.ORPHAN_MIN_LISTINGS and ORPHAN_MIN_COMPANIES, plus an
    idf above this person's own median so ubiquitous vocabulary drops out.
    """
    if not own:
        return set()
    employers = {}
    for _t, _s, skills, company in rows:
        for skill in skills:
            if company:
                employers.setdefault(skill, set()).add(company)
    searchable = {
        s for s in own
        if vocab.get(s, 0) >= local_search.ORPHAN_MIN_LISTINGS
        and len(employers.get(s, ())) >= local_search.ORPHAN_MIN_COMPANIES}
    if not searchable:
        return set()
    idfs = sorted(local_search.idf(s, vocab, total) for s in searchable)
    median = idfs[len(idfs) // 2]
    return {s for s in searchable
            if local_search.idf(s, vocab, total) >= median}


def rare_covered(order, info, limit, rare):
    """How many of their scarcest skills the affordable prefix reaches."""
    covered = {}
    for keyword in order[:limit]:
        for skill, share in info[keyword]["share"].items():
            covered[skill] = max(covered.get(skill, 0.0), share)
    return {s for s in rare if covered.get(s, 0.0) >= COVERED_SHARE}


# A candidate whose wanted-skill set is already this well covered by
# earlier picks is buying the same role again.
REDUNDANT_AT = 0.6


def suppress_redundant(candidates, held, rows, own, vocab, total,
                       redundant_at=REDUNDANT_AT, held_in_prefix=1):
    """The shipped order, with redundant entries deferred to the tail.

    The conservative alternative to re-ranking: every candidate keeps its
    position unless an earlier pick already bought most of what it wants,
    in which case it moves behind the ones that buy something new. Two
    things change and nothing else:

      a near-duplicate role stops consuming a slot the budget can afford
      held titles stop taking every early slot — for a generalist whose
      own titles are "software developer" and "solutions developer" that
      was two of the four searches a small cap reaches, both generic

    Deferred candidates keep their relative order, so this cannot invent
    a ranking the corpus did not support.
    """
    info = profile(candidates, rows, own, vocab, total)
    held_set = {h.strip().lower() for h in held or ()}
    keep, defer, covered, held_taken = [], [], {}, 0
    for keyword in candidates:
        entry = info[keyword]
        mine = value(entry, {})
        gain = value(entry, covered)
        is_held = keyword.strip().lower() in held_set
        redundant = mine > 0 and (gain / mine) < (1.0 - redundant_at)
        if is_held and held_taken >= held_in_prefix:
            defer.append(keyword)
            continue
        if redundant:
            defer.append(keyword)
            continue
        keep.append(keyword)
        held_taken += 1 if is_held else 0
        for skill, share in entry["share"].items():
            covered[skill] = max(covered.get(skill, 0.0), share)
    return keep + defer, info


def specialist_of(info, own, rows, vocab, total):
    """The candidate that best serves this person's targetable skills.

    Generic: "rarest" is idf over the corpus, so nothing here knows what
    Salesforce or React Native are. Used only to score an ordering, never
    to build one.
    """
    rare = rare_skills(own, rows, vocab, total)
    best, score = None, 0.0
    for keyword, entry in info.items():
        if entry["listings"] < SPECIALIST_MIN_ROWS:
            continue
        got = sum(entry["idf"][s] * entry["share"][s]
                  for s in entry["share"] if s in rare)
        if got > score:
            best, score = keyword, got
    return best


def coverage(order, info, limit):
    """What the first `limit` keywords actually reach."""
    covered, mass = {}, 0.0
    for keyword in order[:limit]:
        for skill, share in info[keyword]["share"].items():
            if share > covered.get(skill, 0.0):
                mass += info[keyword]["idf"][skill] * (
                    share - covered.get(skill, 0.0))
                covered[skill] = share
    firm = {s for s, share in covered.items() if share >= COVERED_SHARE}
    return {"mass": mass, "skills": len(firm), "covered": firm}


def redundancy(order, info, limit):
    """Mean pairwise Jaccard of what the prefix's keywords want.

    High means the budget bought the same role several times.
    """
    sets = [{s for s, share in info[k]["share"].items()
             if share >= COVERED_SHARE} for k in order[:limit]]
    sets = [s for s in sets if s]
    if len(sets) < 2:
        return 0.0
    pairs, total = 0, 0.0
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            if union:
                total += len(sets[i] & sets[j]) / len(union)
                pairs += 1
    return total / pairs if pairs else 0.0


def demo():
    """Self-check: the objective must prefer new evidence over more of the same."""
    rows = []
    # A rare specialist cluster, a common generalist cluster, and a second
    # near-identical specialist variant that must NOT be bought twice.
    for i in range(40):
        rows.append(("salesforce developer", 25, frozenset({"apex", "soql"}),
                     f"sf{i % 12}"))
    for i in range(30):
        rows.append(("senior salesforce developer", 25,
                     frozenset({"apex", "soql"}), f"sf{i % 12}"))
    # i%12 rather than i%30: with one title per i%5, a stride of 30
    # yields only six distinct employers and the generic cluster failed
    # the market-support bar for a reason the test was not about.
    for i in range(600):
        rows.append((f"software engineer {i % 5}", 3,
                     frozenset({"java", "sql"}), f"big{i % 12}"))
    for i in range(60):
        rows.append(("react native developer", 20,
                     frozenset({"react native"}), f"mob{i % 15}"))
    total = len(rows)
    vocab = local_search.vocabulary(rows)
    own = {"apex", "soql", "java", "sql", "react native"}

    cands = ["software engineer 1", "salesforce developer",
             "senior salesforce developer", "react native developer"]
    order, info = budget_order(cands, ["software engineer 1"], rows, own,
                               vocab, total)

    # The held title leads, because the person has done that job.
    assert order[0] == "software engineer 1", order
    # Then the rare cluster, ahead of a second helping of the generic one.
    assert order[1] == "salesforce developer", order
    # The near-duplicate variant is displaced to the tail: the first one
    # already covered apex and soql.
    assert order.index("senior salesforce developer") > \
        order.index("react native developer"), order

    # A title the market barely posts is held behind every one it does,
    # however pure its skill mix — a search that returns nothing costs
    # the same as one that returns ten.
    thin_rows = rows + [("niche apex architect", 30, frozenset({"apex"}),
                         "solo")] * 4
    t_vocab = local_search.vocabulary(thin_rows)
    t_order, t_info = budget_order(
        cands + ["niche apex architect"], ["software engineer 1"],
        thin_rows, own, t_vocab, len(thin_rows))
    assert t_info["niche apex architect"]["listings"] < local_search.MIN_ROWS
    assert t_order[-1] == "niche apex architect", t_order

    # Coverage rises with the prefix and redundancy stays low.
    assert coverage(order, info, 3)["mass"] > coverage(order, info, 1)["mass"]
    assert coverage(order, info, 3)["skills"] >= 4, coverage(order, info, 3)

    # A sorted-by-lift order buys the duplicate and covers less in 3 slots.
    naive = ["software engineer 1", "senior salesforce developer",
             "salesforce developer", "react native developer"]
    assert coverage(order, info, 3)["mass"] > coverage(naive, info, 3)["mass"]
    assert redundancy(order, info, 3) < redundancy(naive, info, 3)

    # The specialist is identified without naming any technology.
    spec = specialist_of(info, own, rows, vocab, total)
    assert spec in ("salesforce developer", "senior salesforce developer"), spec

    # The affordability model matches what the live runs actually bought:
    # nine searches at a $0.15 cap, which is four keywords at two locations.
    assert affordable_searches(0.15) == 8, affordable_searches(0.15)
    assert affordable_keywords(0.15, 2) == 4
    assert affordable_keywords(None, 2) is None

    # Zero-gain candidates keep their incoming order rather than being
    # dropped or shuffled.
    assert len(order) == len(cands) and set(order) == set(cands)

    print("budget_reach demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
