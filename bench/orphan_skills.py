"""A prominent skill nobody searched for: find it, and give it a title.

The live sweep found the hole. Corpus retrieval selects listings that want
at least TWO of a person's skills and reads the common titles off them,
which means a real skill sitting outside the person's main market cluster
contributes nothing. Sarthak has Salesforce Mobile SDK experience;
"salesforce developer" appears in 4 of his 4,529 matched listings, so it
never ranked, and a two-search test of that one keyword returned four
genuine Salesforce Developer roles scoring 19, 14, 14 and 14 — better
yield per search than his whole local run averaged.

The fix is not a Salesforce case. It is that a skill can be:

  PRESENT     extracted from the résumé, so the person claims it
  SEARCHABLE  named by enough listings at enough employers to be a market
  ORPHANED    named by none of the keywords already selected

and the third condition is the one nothing was checking. An orphaned
skill gets one keyword of its own, taken from the titles the market
attaches to THAT skill rather than to the person's cluster.

WHERE THE MODEL COMES IN, AND ONLY THERE
----------------------------------------
Orphan detection is arithmetic and so is the candidate title. What
neither can decide is whether a skill is part of what someone does for a
living or incidental tooling they happen to list. git, postman and
docker are all present, searchable and orphaned for most people, and
nobody is hired as a "git developer". That judgement is semantic, it is
one filtered list rather than forty invented keywords, and it is checked
afterwards by the same validators as everything else.

    python -m bench.orphan_skills --demo
    python -m bench.orphan_skills qwen3:8b
"""

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

from bench import derive_search as ds

HERE = os.path.dirname(os.path.abspath(__file__))

# A skill is REPRESENTED when one of the selected keywords already draws
# listings that name it this often. Below that the keyword set is not
# really searching for it, whatever the skill list says.
REPRESENTED = 0.10

# A skill needs at least this many listings, at this many employers, to
# be worth a paid search of its own. The same shape as the fragment
# guards in derive_search, for the same reason: one employer's stack is
# not a market.
MIN_LISTINGS = 15
MIN_COMPANIES = 8

# A title only earns a search because of THIS skill if its listings name
# the skill far more than the market does. Without this the mechanism
# offered "backend engineer" for spring boot and "full stack engineer"
# for aws, the model took both, and one person's keyword set went from
# 408 rows at 94% relevance to 3,095 at 67% — seven times the cost for
# worse results, while missing the one title the feature exists for.
#
# 10x keeps salesforce developer (14.1x), java developer (21.4x), android
# developer (28.1x) and ai/ml engineer (17.6x); it rejects backend
# engineer (5.4x), full stack engineer (5.0x) and account executive
# (5.8x). The closest false positive is "business development
# representative" for salesforce at 8.4x, so the margin is real but not
# generous.
SKILL_LIFT = 10.0


def skill_lift(title, skill, rows, vocab, total):
    """How much more this title's listings name this skill than the market."""
    matched = [r for r in rows if title in r[0]]
    base = vocab.get(skill, 0) / total if total else 0.0
    if not matched or not base:
        return None
    return (sum(1 for r in matched if skill in r[2]) / len(matched)) / base


def represented(skill, keywords, rows):
    """Share of the rows these keywords buy that name this skill."""
    needles = [k.strip().lower() for k in keywords if k.strip()]
    if not needles:
        return 0.0
    seen = [r for r in rows if any(n in r[0] for n in needles)]
    if not seen:
        return 0.0
    return sum(1 for r in seen if skill in r[2]) / len(seen)


def orphans(own, keywords, rows, vocab=None):
    """Skills that are present and searchable and nobody is searching for.

    Returns [(skill, listings, employers, share)] worst-covered first, so
    the most neglected skill is the first candidate for a keyword.
    """
    vocab = vocab if vocab is not None else ds.vocabulary(rows)
    employers = collections.defaultdict(set)
    for _t, _s, skills, company in rows:
        for skill in skills:
            if company:
                employers[skill].add(company)
    out = []
    for skill in sorted(own):
        listings = vocab.get(skill, 0)
        if listings < MIN_LISTINGS or len(employers[skill]) < MIN_COMPANIES:
            continue
        share = represented(skill, keywords, rows)
        if share >= REPRESENTED:
            continue
        out.append((skill, listings, len(employers[skill]), share))
    out.sort(key=lambda row: (row[3], -row[1]))
    return out


def candidates_for_skill(skill, rows, idx, total, seniority=(), want=8,
                         min_share=0.02, vocab=None):
    """The job titles this market attaches to this skill, most common first.

    A LIST, not a pick, because frequency alone chooses wrong. The corpus
    names "salesforce" in 932 listings and the commonest titles among
    them are "development representative" (23%), "business development
    representative" (12%) and "sales development representative" (11%) —
    sales jobs that require Salesforce CRM. "salesforce developer" is
    5.9% of them, and it is the one a mobile engineer with Salesforce SDK
    experience should be searching. matched_skills cannot tell a CRM user
    from a Salesforce developer; that is the semantic step.

    Every candidate is a real title from the corpus, guarded the same way
    every other keyword is, so whatever selects among them cannot invent
    one.
    """
    named = [r for r in rows if skill in r[2]]
    if not named:
        return []
    vocab = vocab if vocab is not None else ds.vocabulary(rows)
    here = collections.Counter()
    for title, _score, _skills, _company in named:
        for fragment in ds.fragments(title, seniority):
            here[fragment] += 1
    titles = collections.Counter(t for t, _s, _k, _c in rows)
    out = []
    for fragment, count in here.most_common(80):
        entry = idx.get(fragment)
        if not entry or count / len(named) < min_share:
            continue
        if len(entry["companies"]) < MIN_COMPANIES:
            continue
        if entry["listings"] / total > ds.MAX_SHARE:
            continue
        title = ds.canonical(fragment, titles, seniority)
        if not title or title in out:
            continue
        if any(title in got or got in title for got in out):
            continue
        # The guard that makes this a skill-anchored keyword rather than
        # a second helping of generic ones.
        lift = skill_lift(title, skill, rows, vocab, total)
        if lift is None or lift < SKILL_LIFT:
            continue
        out.append(title)
        if len(out) >= want:
            break
    return out


SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array", "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string"},
                    "titles": {"type": "array", "maxItems": 3,
                               "items": {"type": "string"}},
                },
                "required": ["skill", "titles"],
            },
        },
    },
    "required": ["picks"],
}

PROMPT = """Someone works in {field}. Their skills include the ones below.

For each skill, real job titles from the market are listed. Choose the
titles that are jobs THIS PERSON could be hired for — the same kind of
work as {field}, using that skill.

Rules:
- Choose ONLY from the titles listed for that skill. Do not write new ones.
- A title using the skill in a different profession does not count. Sales
  roles require Salesforce and are not software engineering; analyst roles
  require SQL and are not software engineering.
- Most skills will have nothing worth choosing. A tool everyone uses —
  git, docker, sql — is not a job title. Answer with an empty list then.
- At most one or two titles per skill, and only clear cases.

{blocks}"""


def ask(model, field, blocks, timeout=900):
    import json as _json
    import urllib.request
    from bench.run import ctx_for, KEEP_ALIVE, OLLAMA

    listed = "\n\n".join(
        f"{skill}:\n" + "\n".join(f"  - {t}" for t in titles)
        for skill, titles in blocks)
    prompt = PROMPT.format(field=field or "software engineering",
                           blocks=listed)
    body = _json.dumps({
        "model": model, "prompt": prompt, "format": SCHEMA, "stream": False,
        "think": False, "keep_alive": KEEP_ALIVE,
        # Zero, not 0.2. This is a SELECTION from a fixed list, not
        # generation, and at 0.2 it was a coin flip on the pick that
        # matters: a Salesforce Apex developer was offered "salesforce
        # developer" for both apex and soql, and one run took it while
        # the next took "mobile developer" and "back end developer"
        # instead. Same inputs, same field, different answer.
        "options": {"temperature": 0,
                    "num_ctx": ctx_for(prompt, reply_tokens=700)},
    }).encode()
    request = urllib.request.Request(OLLAMA, body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return _json.loads(_json.loads(response.read())["response"])


# An added keyword has to be mostly about this person. Below this share
# of its rows naming one of their skills it is a broadening of the search
# rather than a recovery of a missed one.
#
# RELEVANCE, not reachability, and the distinction matters more here than
# anywhere else in this benchmark. Judged on reachability, "salesforce
# developer" scores 9% against a 32% market and reads as noise — and the
# live sweep returned four genuine Salesforce Developer roles from it at
# scores 19, 14, 14 and 14. The corpus `score` column was computed with
# the CORPUS OWNER's skill_weights, so it marks down exactly the
# off-cluster skills this feature exists to recover. Relevance is a fact
# about the listings and does not have that bias: salesforce developer
# scores 91% on it, the highest of any candidate.
MIN_RELEVANCE = 0.75


# A title can be right for someone even when most of its listings want
# none of their other skills — that is what being a SPECIALIST means.
# Lovish writes enterprise Apex and "salesforce developer" scores 34%
# against his whole skill set, because those listings do not ask for his
# React or Node; the same title scores 91% for Sarthak, for whom
# Salesforce is secondary and React is what the rest of the listing
# wants. Judged on the whole set, the bar keeps the generalist's
# secondary skill and throws away the specialist's primary one.
#
# So the anchor gets its own test: how much this title's market wants
# the SKILL THAT PRODUCED IT, weighted by how much that skill narrows
# the market. Raw anchor share cannot do it alone — apex scores 0.25 and
# java 0.21 — but apex is four times rarer, and the product separates
# them: apex 1.46, salesforce 2.39, java 0.84, sql 0.68.
ANCHOR_EVIDENCE = 1.2

# And a title nothing much is posted under is not worth a search
# whatever it scores: "sf data cloud consultant" matched two listings.
MIN_ROWS = 20


def anchor_evidence(title, anchor, rows, vocab, total):
    """How strongly this title's market wants the skill that produced it."""
    named = [r for r in rows if title in r[0]]
    if not named or not anchor:
        return 0.0
    share = sum(1 for r in named if anchor in r[2]) / len(named)
    return share * ds.idf(anchor, vocab, total)


def worth_it(title, rows, own, anchor=None, vocab=None, total=None):
    """Is this addition about this person, or just a wider net?"""
    got = ds.buys([title], rows, own)
    if got["listings"] < MIN_ROWS or got["relevance"] is None:
        return False, got
    if anchor is None:
        # No anchor to reason about: fall back to the whole-set bar.
        return got["relevance"] >= MIN_RELEVANCE, got
    # One gate, not two. The whole-set relevance test used to be an
    # alternative route in, and it let "associate ai/ml engineer" through
    # on a git anchor — generic skill, 0.87 evidence, no business being a
    # paid search. Anchor evidence already says what the other test was
    # reaching for, and says it for specialists too.
    vocab = vocab if vocab is not None else ds.vocabulary(rows)
    total = total or len(rows)
    strength = anchor_evidence(title, anchor, rows, vocab, total)
    return strength >= ANCHOR_EVIDENCE, got


def select(own, base, rows, idx, total, seniority, vocab=None, cap=2):
    """The orphan keywords to add, chosen without asking a model.

    The model was here and is not any more. It was asked to pick the
    software title out of a skill's candidates — "salesforce developer"
    rather than "sales operations analyst" — and on the one résumé the
    feature exists for it returned an empty list at temperature 0, having
    picked correctly three runs earlier on a slightly different candidate
    set. Removing it entirely and letting worth_it decide everything went
    the other way: ten additions for one person.

    Ranking by anchor evidence and capping does both jobs. It is
    deterministic, it needs no inference, and the cap bounds the spend
    whatever the corpus throws up.
    """
    return [title for title, _skill, _evidence in
            select_detail(own, base, rows, idx, total, seniority, vocab, cap)]


def select_detail(own, base, rows, idx, total, seniority, vocab=None, cap=2):
    """select(), but saying which skill produced each keyword and how
    strongly — the ordering downstream has to be explainable."""
    vocab = vocab if vocab is not None else ds.vocabulary(rows)
    scored = []
    for skill, titles in blocks_for(own, base, rows, idx, total, seniority,
                                    vocab=vocab):
        for title in titles:
            if any(title in b or b in title for b in base):
                continue
            ok, _got = worth_it(title, rows, own, skill, vocab, total)
            if not ok:
                continue
            scored.append((anchor_evidence(title, skill, rows, vocab, total),
                           skill, title))
    scored.sort(key=lambda row: (-row[0], row[2]))
    keep = []
    for evidence_, skill, title in scored:
        # Checked BEFORE appending: the other order appends one and then
        # notices, so a cap of zero still added a keyword.
        if len(keep) >= cap:
            break
        if any(title in got or got in title for got, _s, _e in keep):
            continue
        keep.append((title, skill, evidence_))
    return keep


def accept(answer, blocks, rows, seniority):
    """(kept, refused) — the picks that survive, and why the others did not.

    The model can only choose from what it was given, so the first check
    is that it did. Everything after that is the same validation every
    other keyword gets.
    """
    from bench.cold_start import validated

    offered = {skill: {t.lower() for t in titles} for skill, titles in blocks}
    kept, refused = [], []
    for pick in (answer or {}).get("picks") or ():
        skill = str(pick.get("skill", "")).strip().lower()
        for title in pick.get("titles") or ():
            low = str(title).strip().lower()
            if not low:
                continue
            if skill not in offered:
                refused.append((skill, low, "a skill that was not asked about"))
                continue
            if low not in offered[skill]:
                # The one failure the design forecloses: it cannot invent
                # a title, because a title it invents is not on the list.
                refused.append((skill, low, "not one of the titles offered"))
                continue
            if not validated([low], rows, seniority):
                refused.append((skill, low, "a wildcard or carries a "
                                            "hard-dropped word"))
                continue
            kept.append((skill, low))
    return kept, refused


def setup():
    """The corpus and the seniority lists, built once."""
    import config

    rows = ds.corpus_rows()
    seniority = (tuple(config.SCORING["hard_drop_terms"])
                 + tuple(config.SCORING["soft_drop_terms"]))
    return rows, ds.index(rows, seniority), ds.vocabulary(rows), seniority


def blocks_for(own, keywords, rows, idx, total, seniority, cap=8,
               vocab=None):
    """[(skill, [candidate titles])] for this person's orphaned skills."""
    out = []
    for skill, _n, _emp, _share in orphans(own, keywords, rows):
        titles = candidates_for_skill(skill, rows, idx, total, seniority,
                                      vocab=vocab)
        if titles:
            out.append((skill, titles))
        if len(out) >= cap:
            break
    return out


def run(model, cache_path=None, asker=ask):
    """One call per person. Skills come from the answer key, so this
    measures the orphan mechanism rather than extraction noise."""
    import json
    from bench.cold_start import from_resume, validated
    from bench.people import PEOPLE

    cache_path = cache_path or os.path.join(
        HERE, "results",
        f"orphans-{model.replace(':', '_').replace('/', '_')}.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)

    rows, idx, _vocab, seniority = setup()
    total = len(rows)
    for slug, person in PEOPLE.items():
        if slug in cache:
            continue
        own = {s.strip().lower() for s in person["skills"]}
        base = validated(
            from_resume(person, seniority)
            + ds.canonicalise(
                ds.keywords_for(own, rows, idx, total, want=12,
                                seniority=seniority), rows, seniority),
            rows, seniority)
        blocks = blocks_for(own, base, rows, idx, total, seniority)
        if not blocks:
            cache[slug] = {"base": base, "blocks": [], "answer": {"picks": []}}
        else:
            try:
                answer = asker(model, person["headline"], blocks)
            except Exception as exc:            # noqa: BLE001 - logged
                answer = {"error": f"{type(exc).__name__}: {exc}"}
            cache[slug] = {"base": base, "blocks": blocks, "answer": answer}
        got = cache[slug]
        print(f"  {slug:<9} {len(got['base'])} base keywords, "
              f"{len(got['blocks'])} orphan skill(s), "
              f"{len(got['answer'].get('picks') or [])} pick(s)", flush=True)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=1)
    return cache


def report(model, cache=None):
    from bench.people import PEOPLE
    from bench.search_fields import REACHABLE

    cache = cache if cache is not None else run(model)
    rows, _idx, _vocab, seniority = setup()
    total = len(rows)
    base_reach = sum(1 for r in rows if r[1] >= REACHABLE) / total

    print(f"\n{'=' * 78}\n{model}: does an orphaned skill earn its own keyword?"
          f"\n{'=' * 78}")
    print(f"  {total} listings, {base_reach:.0%} reach score {REACHABLE}\n")
    print(f"  {'person':<9}{'kw':>4}{'+new':>5}{'':>2}"
          f"{'rows':>7}{'+rows':>7}{'':>2}{'reach':>6}{'reach*':>7}"
          f"{'':>2}{'spend':>7}{'+spend':>8}   added")
    totals = {"base": [], "plus": []}
    added_all, refused_all, thin_all = [], [], []
    for slug, person in PEOPLE.items():
        got = cache.get(slug)
        if not got:
            continue
        own = {s.strip().lower() for s in person["skills"]}
        blocks = [(s, list(t)) for s, t in got["blocks"]]
        kept, refused = accept(got["answer"], blocks, rows, seniority)
        refused_all.extend((slug, *r) for r in refused)
        base = list(got["base"])
        # Deduplicated against the base list AND against each other: two
        # different orphaned skills can point at one title, and "java
        # developer" was being added twice, once for java and once for
        # spring boot. Two searches, one search's worth of rows.
        extra, thin = [], []
        for _s, title in kept:
            if any(title in got_ or got_ in title
                   for got_ in base + extra):
                continue
            ok, _got = worth_it(title, rows, own, _s)
            (extra if ok else thin).append(title)
        thin_all.extend((slug, t) for t in dict.fromkeys(thin))
        seen_extra = set()
        for skill, title in kept:
            if title in extra and title not in seen_extra:
                seen_extra.add(title)
                added_all.append((slug, skill, title))
        plus = base + extra

        b = ds.buys(base, rows, own)
        p = ds.buys(plus, rows, own)
        totals["base"].append(b)
        totals["plus"].append(p)
        pct = lambda v: f"{v:.0%}" if v is not None else "-"
        print(f"  {slug:<9}{len(base):>4}{len(extra):>5}{'':>2}"
              f"{b['listings']:>7}{p['listings'] - b['listings']:>+7}{'':>2}"
              f"{pct(b['reachable']):>6}{pct(p['reachable']):>7}{'':>2}"
              f"{'$' + format(b['spend'], '.2f'):>7}"
              f"{'$' + format(p['spend'] - b['spend'], '.2f'):>8}   "
              f"{', '.join(extra) or '-'}")

    def agg(rows_):
        listings = sum(r["listings"] for r in rows_)
        return (listings,
                sum(r["reachable"] * r["listings"] for r in rows_) / listings
                if listings else None,
                sum(r["relevance"] * r["listings"] for r in rows_) / listings
                if listings else None,
                sum(r["spend"] for r in rows_),
                sum(r["wasted"] for r in rows_))
    bl, br, brel, bs, bw = agg(totals["base"])
    pl, pr, prel, ps, pw = agg(totals["plus"])
    print(f"\n  {'':<22}{'base':>10}{'with orphans':>15}{'change':>10}")
    for label, x, y, fmt in (("rows bought", bl, pl, "{:,.0f}"),
                             ("reachable", br, pr, "{:.1%}"),
                             ("relevance", brel, prel, "{:.1%}"),
                             ("hard-dropped", bw, pw, "{:,.0f}"),
                             ("spend", bs, ps, "${:.2f}")):
        delta = ((f"{(y - x):+,.0f}" if "," in fmt else
                  (f"{(y - x):+.1%}" if "%" in fmt else f"${y - x:+.2f}"))
                 if x is not None and y is not None else "-")
        print(f"  {label:<22}{fmt.format(x):>10}{fmt.format(y):>15}"
              f"{delta:>10}")

    print(f"\n  {len(added_all)} keyword(s) added across "
          f"{len({a[0] for a in added_all})} people:")
    for slug, skill, title in added_all:
        print(f"    {slug:<9} {skill:<16} -> {title}")
    if thin_all:
        print(f"\n  {len(thin_all)} pick(s) dropped as too broad "
              f"(under {MIN_RELEVANCE:.0%} relevance):")
        for slug, title in thin_all:
            print(f"    {slug:<9} {title}")
    if refused_all:
        print(f"\n  {len(refused_all)} pick(s) refused by a validator:")
        seen = set()
        for slug, skill, title, why in refused_all:
            if (skill, title, why) in seen:
                continue
            seen.add((skill, title, why))
            print(f"    {slug:<9} {title!r:<34} {why}")


def demo():
    sen = ("senior", "intern", "ii")
    # A market with two clusters and one off-cluster skill. react work is
    # the person's cluster; salesforce is the orphan, and its listings are
    # mostly SALES roles that merely require the CRM — which is why
    # frequency alone picks the wrong title.
    rows = []
    rows += [("react developer", 40, frozenset({"react", "typescript"}),
              f"r{i}") for i in range(60)]
    rows += [("business development representative", 30,
              frozenset({"salesforce"}), f"b{i}") for i in range(50)]
    rows += [("salesforce developer", 35,
              frozenset({"salesforce"} | ({"apex"} if i < 10 else set())),
              f"s{i}") for i in range(20)]
    # Enough unrelated market that salesforce is a MINORITY skill; lift is
    # measured against its base rate, so a small market makes every title
    # look unremarkable and the guard reject everything.
    # A big generic cluster that MENTIONS salesforce occasionally. Without
    # the lift guard "software engineer" qualifies as a salesforce title —
    # it is frequent, multi-employer and under the wildcard share — and
    # that is exactly the failure that took one real profile from 408 rows
    # at 94% relevance to 3,095 at 67%.
    rows += [("software engineer", 20,
              frozenset({"salesforce"} if i < 10 else set()), f"g{i}")
             for i in range(300)]
    rows += [("warehouse operative", 0, frozenset(), f"w{i}")
             for i in range(1000)]
    total = len(rows)
    idx = ds.index(rows, sen)
    vocab = ds.vocabulary(rows)
    own = {"react", "typescript", "salesforce", "apex"}
    base = ["react developer"]

    # react is represented by the base keyword; salesforce is not.
    assert represented("react", base, rows) == 1.0
    assert represented("salesforce", base, rows) == 0.0
    assert represented("react", [], rows) == 0.0

    got = {skill for skill, *_rest in orphans(own, base, rows, vocab)}
    assert got == {"salesforce"}, got
    # A skill too thin in the market is not worth a paid search even when
    # it is orphaned.
    # apex IS one of their skills and IS orphaned, and is still not worth
    # a paid search: ten listings is not a market.
    assert "apex" in own and represented("apex", base, rows) == 0.0
    assert vocab["apex"] < MIN_LISTINGS
    assert "apex" not in got, "ten listings is under MIN_LISTINGS"

    cands = candidates_for_skill("salesforce", rows, idx, total, sen,
                                 vocab=vocab)
    # Both survive the guards, so the choice between them is semantic —
    # which is the whole reason a model is asked.
    assert "salesforce developer" in cands, cands
    assert "business development representative" in cands, cands
    # The generic cluster title is NOT offered, and only the lift guard
    # stops it: it clears the frequency, employer and wildcard checks.
    assert "software engineer" not in cands, cands
    assert idx["software engineer"]["listings"] / total < ds.MAX_SHARE
    assert len(idx["software engineer"]["companies"]) >= MIN_COMPANIES

    # The lift guard is what stopped generic titles being offered. Here
    # the react cluster's own title has no salesforce lift at all.
    assert skill_lift("react developer", "salesforce", rows, vocab,
                      total) == 0.0
    assert skill_lift("salesforce developer", "salesforce", rows, vocab,
                      total) > SKILL_LIFT
    assert skill_lift("nothing here", "salesforce", rows, vocab,
                      total) is None

    blocks = [("salesforce", cands)]
    # The model can only choose from what it was given.
    kept, refused = accept({"picks": [{"skill": "salesforce",
                                       "titles": ["salesforce developer"]}]},
                           blocks, rows, sen)
    assert kept == [("salesforce", "salesforce developer")], kept
    assert refused == []

    kept, refused = accept({"picks": [{"skill": "salesforce",
                                       "titles": ["salesforce architect"]}]},
                           blocks, rows, sen)
    assert kept == [] and "not one of the titles offered" in refused[0][2]
    kept, refused = accept({"picks": [{"skill": "cobol",
                                       "titles": ["cobol developer"]}]},
                           blocks, rows, sen)
    assert kept == [] and "not asked about" in refused[0][2]
    assert accept({}, blocks, rows, sen) == ([], [])
    assert accept({"picks": []}, blocks, rows, sen) == ([], [])

    # worth_it uses RELEVANCE, not reachability. Judged on reachability
    # "salesforce developer" reads as noise — its rows score low under the
    # corpus owner's weights, which is the bias this feature has to
    # survive — while relevance is a fact about the listings.
    ok, got = worth_it("salesforce developer", rows, own)
    assert ok and got["relevance"] == 1.0
    bad, got = worth_it("warehouse operative", rows, own)
    assert not bad and got["relevance"] == 0.0
    assert worth_it("nothing here", rows, own)[0] is False

    # Anchored: a title whose market wants the DISCRIMINATIVE skill that
    # produced it survives even when it wants little else this person
    # has. That is what being a specialist looks like, and the whole-set
    # bar threw it away.
    vocab = ds.vocabulary(rows)
    strong = anchor_evidence("salesforce developer", "salesforce", rows,
                             vocab, total)
    assert strong >= ANCHOR_EVIDENCE, strong
    # A generic anchor cannot carry a title, however common the title is.
    weak = anchor_evidence("business development representative", "react",
                           rows, vocab, total)
    assert weak < ANCHOR_EVIDENCE, weak
    # Too few listings is not a market whatever the evidence says.
    assert worth_it("salesforce developer", rows, own, "salesforce",
                    vocab, total)[0]
    assert MIN_ROWS > 2

    # The anchored gate, on a market where a generic anchor cannot
    # carry a title. "software engineer" is wanted by 100% of this
    # person's skills and is still not a keyword they should buy,
    # because the skill that produced it — react, in 660 of 1380
    # listings — narrows nothing. Only this gate rejects it.
    wide = ([("react developer", 40, frozenset({"react", "typescript"}),
              f"r{i}") for i in range(60)]
            + [("salesforce developer", 35,
                frozenset({"salesforce"} | ({"apex"} if i < 10 else set())),
                f"s{i}") for i in range(20)]
            + [("software engineer", 20, frozenset({"react", "typescript"}),
                f"g{i}") for i in range(300)]
            + [("web developer", 20, frozenset({"react", "typescript"}),
                f"h{i}") for i in range(300)]
            + [("warehouse operative", 0, frozenset(), f"w{i}")
               for i in range(700)])
    wv, wt = ds.vocabulary(wide), len(wide)
    wown = {"react", "typescript", "salesforce", "apex"}
    strong_ok, strong_got = worth_it("salesforce developer", wide, wown,
                                     "apex", wv, wt)
    weak_ok, weak_got = worth_it("software engineer", wide, wown,
                                 "react", wv, wt)
    assert strong_ok and weak_got["relevance"] == 1.0
    assert not weak_ok, "a generic anchor cannot carry a title"
    # Both score 100% on the whole set, so the old bar kept both.
    assert strong_got["relevance"] == weak_got["relevance"] == 1.0

    # select(): deterministic, ranked, capped. No model.
    base = ["react developer"]
    picked = select(own, base, rows, idx, total, sen, vocab, cap=2)
    assert "salesforce developer" in picked, picked
    assert len(picked) <= 2
    assert select(own, base, rows, idx, total, sen, vocab, cap=0) == []
    # Same inputs, same answer, every time — the property the model
    # could not provide.
    assert picked == select(own, base, rows, idx, total, sen, vocab, cap=2)
    print("orphan_skills demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    models = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not models:
        sys.exit(__doc__)
    for model in models:
        report(model)


if __name__ == "__main__":
    main()
