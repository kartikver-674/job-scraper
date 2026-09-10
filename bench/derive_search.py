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
listing. `relevance()` below is therefore a per-person quality measure
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
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# n-grams of this many words. role_keywords are SEARCH strings and the
# boards match them loosely, so "backend engineer" is the useful unit —
# a whole scraped title like "senior software engineer ii (backend)" is
# not something anyone would search for.
NGRAM = (2, 3)

# A fragment seen fewer times than this is noise rather than a role.
MIN_LISTINGS = 10

# ...and a real role name is posted by MANY employers. One company's
# internal jargon — "engineer a2", "developer unifi", "engineer billings"
# — can be frequent and well-scored inside that company's postings and is
# still not something anyone would search for. Distinct-employer support
# separates the two without needing a taxonomy of job titles.
MIN_COMPANIES = 5

# Above this share of the market a fragment is a wildcard, matching
# bench/search_fields.WILDCARD_SHARE.
MAX_SHARE = 0.25

# Words that are not a role by themselves and drag a fragment towards
# matching everything. Kept deliberately short: this is a stop list for
# TITLE fragments, not a taxonomy.
FILLER = {"and", "or", "the", "a", "an", "of", "for", "with", "in", "at",
          "to", "new", "remote", "hybrid", "onsite", "full", "time", "part",
          "job", "jobs", "role", "opening", "openings", "hiring", "urgent",
          "immediate", "walk", "walkin", "fresher", "years", "yrs", "exp"}

_WORD = re.compile(r"[a-z0-9+#.]+")


def corpus_rows(output_dir=None):
    """[(title, score, frozenset(matched_skills), company)] per listing.

    utf-8-sig because 41 of the 54 files carry a BOM, which renamed the
    first column and made 58.8% of the corpus read as score 0.
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
                    skills = frozenset(
                        s.strip().lower()
                        for s in (row.get("matched_skills") or "").split(",")
                        if s.strip())
                    rows.append((title, score, skills,
                                 (row.get("company") or "").strip().lower()))
        except (OSError, UnicodeDecodeError, csv.Error):
            continue
    return rows


def fragments(title, seniority=()):
    """Seniority-free n-gram fragments of one job title.

    Seniority words are stripped rather than the title skipped: RULE 3
    asks for the stem, and "senior backend engineer" is evidence that
    "backend engineer" is a role someone hires for.
    """
    words = [w for w in _WORD.findall(title.lower())
             if w not in FILLER and w not in seniority and not w.isdigit()]
    out = set()
    for size in NGRAM:
        for i in range(len(words) - size + 1):
            out.add(" ".join(words[i:i + size]))
    return out


def index(rows, seniority=()):
    """{fragment: {listings, reachable, skills: Counter}} over the corpus."""
    from bench.search_fields import REACHABLE

    idx = {}
    for title, score, skills, company in rows:
        for frag in fragments(title, seniority):
            entry = idx.setdefault(
                frag, {"listings": 0, "reachable": 0, "companies": set(),
                       "skills": collections.Counter()})
            entry["listings"] += 1
            if score >= REACHABLE:
                entry["reachable"] += 1
            if company:
                entry["companies"].add(company)
            entry["skills"].update(skills)
    return idx


def relevance(entry, own):
    """Share of this fragment's listings that want one of these skills.

    A fact about the listings, not about anyone's weights — which is why
    this is the per-person quality measure and `score` is not.
    """
    if not entry["listings"] or not own:
        return 0.0
    return sum(n for skill, n in entry["skills"].items()
               if skill in own) / entry["listings"]


def matching_rows(own, rows, need=2):
    """The listings that actually want this person, and how much.

    Retrieval before aggregation. The first version of this scored every
    fragment in the corpus by how often its listings mentioned one of the
    person's skills, and produced the same junk for everyone — "apps
    stack", "engineer a2" — because a single ubiquitous skill like python
    is enough to make any globally well-scoring fragment look relevant.
    Selecting the LISTINGS first is what makes a Django backend engineer
    and a Go platform engineer come out different.
    """
    out = []
    for title, score, skills, _company in rows:
        overlap = len(skills & own)
        if overlap >= need:
            out.append((title, score, overlap))
    return out


def keywords_for(own, rows, idx, total, want=12, need=2,
                 min_listings=MIN_LISTINGS, seniority=()):
    """Title fragments distinctive to the listings that want these skills.

    Ranked by LIFT — how much more common a fragment is among this
    person's matching listings than in the market at large — rather than
    by raw frequency. Without it the ranking returns "software engineer"
    for everybody, which is both true and useless: it is the wildcard the
    validators exist to remove.
    """
    from bench.search_fields import REACHABLE

    matched = matching_rows(own, rows, need)
    if not matched:
        return []
    here = collections.Counter()
    reach = collections.Counter()
    for title, score, _ in matched:
        for frag in fragments(title, seniority):
            here[frag] += 1
            if score >= REACHABLE:
                reach[frag] += 1

    scored = []
    for frag, mine in here.items():
        entry = idx.get(frag)
        if not entry or mine < min_listings:
            continue
        if len(entry["companies"]) < MIN_COMPANIES:
            continue
        share = entry["listings"] / total if total else 0.0
        if share > MAX_SHARE:
            continue
        lift = (mine / len(matched)) / share if share else 0.0
        if lift <= 1.0:
            continue
        # Reachability decides between two equally distinctive fragments:
        # being characteristic of this person's market is worth nothing if
        # the rows it draws are not worth reading.
        quality = reach[frag] / mine
        scored.append((lift * quality, mine, frag))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2]))

    # Overlapping fragments buy the same rows twice. "software engineer"
    # and "software engineer backend" are one search's worth of value at
    # two searches' cost, so the better-ranked one wins and the other goes.
    taken = []
    for _, _, frag in scored:
        if any(frag in got or got in frag for got in taken):
            continue
        taken.append(frag)
        if len(taken) >= want:
            break
    return taken


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

    bought = buys(["react developer"], rows, own)
    assert bought["listings"] == 70 and bought["relevance"] == 1.0
    assert bought["reachable"] == 1.0
    assert buys([], rows, own)["listings"] == 0
    assert buys(["nothing here"], rows, own)["listings"] == 0
    # Relevance is about the LISTINGS, not about anyone's scoring, which
    # is the whole reason two different people can be compared with it.
    assert buys(["warehouse operative"], rows, own)["relevance"] == 0.0
    assert relevance({"listings": 0, "skills": collections.Counter()},
                     own) == 0.0

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
