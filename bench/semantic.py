"""The two fields nothing else can produce, and how to check a model's answer.

Everything else in the profile now comes from somewhere deterministic.
Names and skills are extracted at 1.000, years is computed from dates,
skill weights come from the corpus, and role_keywords are retrieved from
the market or taken from the person's own titles. What is left is genuinely
semantic:

  title_exclude    DIFFERENT CAREERS that borrow this person's vocabulary
                   — "data engineer" for a web developer. It wins over
                   every other gate (scraper.is_dev_title checks it first),
                   so it is the most dangerous field in the profile: an
                   entry matching the person's own work deletes inventory
                   and nothing downstream reports it.
  domain halves    the two sides of a field whose intersection is worth a
                   bonus — frontend and backend for a web developer. Some
                   fields have no such split, and saying so is a valid
                   answer.

Neither is retrievable. The corpus knows that "data engineer" and "web
developer" are different strings; it does not know they are different
careers. That is the judgement a model is for, and it is a much smaller
ask than the forty keywords it was failing at.

CHECKING THE ANSWER WITHOUT A SECOND MODEL
------------------------------------------
title_exclude has a property that makes it deterministically testable. If
a term really does name a different career, the listings it matches
should rarely want this person's skills. If they want them as often as
the market at large does, it is not a different career — it is this
person's work, about to be deleted before anything scores it.

    exclusion precision = 1 - (relevance of the excluded rows
                               / this person's market baseline)

Above the baseline is a self-inflicted wound; far below it is a real
career boundary. No taxonomy, no second opinion, no key.

The halves are checked differently: a×b has to appear in real job titles,
because a bonus for naming both sides is worthless if no posting ever
names both.

    python -m bench.semantic --demo
    python -m bench.semantic qwen3:8b
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

from bench import derive_search as ds

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA = {
    "type": "object",
    "properties": {
        "title_exclude": {
            "type": "array", "maxItems": 16, "items": {"type": "string"}},
        "domain_half_a": {
            "type": "array", "maxItems": 8, "items": {"type": "string"}},
        "domain_half_b": {
            "type": "array", "maxItems": 8, "items": {"type": "string"}},
        "domain_bonus": {"type": "integer"},
    },
    "required": ["title_exclude", "domain_half_a", "domain_half_b",
                 "domain_bonus"],
}

# The model is given what the deterministic half already established —
# the field, the skills, the keywords — rather than the résumé, because
# the question is not "what does this page say" but "what neighbouring
# careers share these words". Handing it the résumé again invites it to
# re-extract instead of judge.
PROMPT = """A job search is being set up for someone working in {field}.

Their skills: {skills}
The job titles they are searching for: {keywords}

Answer two questions about this field.

1. title_exclude — job titles that CONTAIN one of the words above but are
   a DIFFERENT CAREER, which this person should never be shown. For a web
   developer, "data engineer" and "sales engineer" are different careers
   that borrow the word "engineer". Rules:
   - Never a seniority level. "senior developer" and "junior developer"
     are the same career and are handled elsewhere.
   - Never a specialism inside their own field. For a backend engineer,
     "platform engineer" is the same career.
   - Only titles that actually share vocabulary with their search. A
     title with no words in common needs no exclusion.
   - Lowercase fragments as they appear inside real job titles.
   - If nothing qualifies, answer with an empty list.

2. domain_half_a and domain_half_b — the two halves of this field, where a
   job mentioning BOTH is a better fit than one mentioning either. For a
   web developer that is frontend and backend. Rules:
   - Concrete technical words, never generic category words like
     "engineer", "developer", "software" or "technology".
   - Many fields have no such split. If this one does not, answer with
     two empty lists and domain_bonus 0.
   - domain_bonus is 0 when there is no split, otherwise 6.
"""


# Handing the model its own keyword list invites it to hand the list
# back: 80 of 96 proposed exclusions were verbatim one of the keywords in
# the prompt. The people who escaped it — gopal with two keywords, hana
# with three — are the ones whose list was too short to copy. So this
# variant withholds the list and gives only the field and the skills,
# which is the question actually being asked.
PROMPT_BLIND = """A job search is being set up for someone working in {field}.

Their skills: {skills}

Answer two questions about this field.

1. title_exclude — job titles from OTHER careers that share vocabulary
   with this one and would show up in a search for it, but that this
   person should never be shown. For a web developer, "data engineer" and
   "sales engineer" are different careers that borrow the word
   "engineer". Rules:
   - Never a seniority level. "senior developer" and "junior developer"
     are the same career and are handled elsewhere.
   - Never a specialism inside their own field. For a backend engineer,
     "platform engineer" is the same career, not a different one.
   - Never a title this person could plausibly be hired for.
   - Lowercase fragments as they appear inside real job titles.
   - If nothing qualifies, answer with an empty list.

2. domain_half_a and domain_half_b — the two halves of this field, where a
   job mentioning BOTH is a better fit than one mentioning either. For a
   web developer that is frontend and backend. Rules:
   - Concrete technical words, never generic category words like
     "engineer", "developer", "software" or "technology".
   - Many fields have no such split. If this one does not, answer with
     two empty lists and domain_bonus 0.
   - domain_bonus is 0 when there is no split, otherwise 6.
"""


def ask_blind(model, field, skills, keywords, timeout=900):
    """The same question with the keyword list withheld."""
    return ask(model, field, skills, keywords, timeout, PROMPT_BLIND)


def ask(model, field, skills, keywords, timeout=900, template=None):
    import urllib.request
    from bench.run import ctx_for, KEEP_ALIVE, OLLAMA

    template = template or PROMPT
    prompt = template.format(field=field, skills=", ".join(sorted(skills)),
                             keywords=", ".join(keywords) or "(none derived)")
    body = json.dumps({
        "model": model, "prompt": prompt, "format": SCHEMA, "stream": False,
        "think": False, "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0.2,
                    "num_ctx": ctx_for(prompt, reply_tokens=900)},
    }).encode()
    request = urllib.request.Request(OLLAMA, body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(json.loads(response.read())["response"])


# An exclude term whose rows want this person's skills nearly as often as
# a random row does is not a different career. 0.6 leaves room for the
# real overlap between neighbouring fields — a data engineer's postings do
# mention python — while catching a term that would delete this person's
# own work.
SELF_HARM = 0.6

# Below this many listings, the corpus cannot say whether a term is a
# different career or a typo, so the term is kept and reported as
# unverified rather than dropped on no evidence.
UNVERIFIABLE = 10


def exclusion_precision(term, rows, own, baseline):
    """How much LESS this term's rows want this person than the market.

    1.0 is a clean career boundary: nothing it matches wants these
    skills. 0.0 means it is excluding rows exactly as relevant as
    average, which is to say this person's own work.
    """
    bought = ds.buys([term], rows, own)
    if bought["listings"] < UNVERIFIABLE:
        return None, bought["listings"]
    if not baseline:
        return None, bought["listings"]
    return 1.0 - (bought["relevance"] / baseline), bought["listings"]


# Role words that every job title in this market shares. Overlapping on
# one of these says nothing — "data engineer" and "backend engineer" are
# different careers that both end in "engineer" — so the own-career test
# below ignores them and looks only at the distinctive half of a title.
GENERIC_ROLE = {"engineer", "developer", "analyst", "specialist", "lead",
                "manager", "consultant", "architect", "programmer",
                "officer", "associate", "staff", "member", "technical",
                "senior", "junior", "principal", "i", "ii", "iii",
                "software", "technology", "tech", "it", "systems", "system"}


def _distinctive(text):
    import re
    return {w for w in re.findall(r"[a-z0-9+#.]+", str(text).lower())
            if w not in GENERIC_ROLE and len(w) > 1}


def own_career(term, resume_titles):
    """Does this term share a distinctive word with a role they have HELD?

    The corpus cannot answer this for anyone whose field it has not
    scraped, and those are exactly the people the guard matters for:
    gopal is a QA Automation Lead and the model proposed excluding "qa
    analyst", "qa engineer", "qa specialist" and "qa tester", none of
    which had enough rows to verify. His own résumé says "QA Automation"
    and "Test Engineer", and that source always exists.
    """
    mine = _distinctive(term)
    return any(mine & _distinctive(title) for title in resume_titles)


def check_exclusions(answer, rows, own, baseline, keywords, seniority,
                     resume_titles=()):
    """(kept, corrections) for title_exclude."""
    from bench.search_fields import _has

    kept, corrections = [], []
    targets = [k.strip().lower() for k in keywords if k.strip()]
    for term in answer.get("title_exclude") or ():
        low = term.strip().lower()
        if not low:
            continue
        # RULE 3, and the model was told: seniority is a different rung,
        # not a different career, and config already ranks it.
        if _has(low, seniority):
            corrections.append((term, "seniority, which config already ranks"))
            continue
        # The catastrophic one. This gate is checked before anything else,
        # so a term matching their own search deletes it silently.
        if any(low in t for t in targets):
            corrections.append(
                (term, "matches this person's own search keywords"))
            continue
        # Needs no corpus, so it still protects the uncovered fields.
        if own_career(low, resume_titles):
            corrections.append(
                (term, "shares a distinctive word with a role this person "
                       "has actually held"))
            continue
        precision, listings = exclusion_precision(term, rows, own, baseline)
        if precision is None:
            kept.append((term, None, listings))
            continue
        if precision < SELF_HARM:
            corrections.append(
                (term, f"the rows it removes want these skills "
                       f"{1 - precision:.0%} as often as the market does, so "
                       f"it is not a different career"))
            continue
        kept.append((term, precision, listings))
    return kept, corrections


def check_halves(answer, rows, seniority):
    """(ok, corrections) for the two halves and their bonus."""
    from bench.search_fields import _has

    a = [t.strip().lower() for t in answer.get("domain_half_a") or () if t.strip()]
    b = [t.strip().lower() for t in answer.get("domain_half_b") or () if t.strip()]
    bonus = answer.get("domain_bonus") or 0
    corrections = []

    # "engineer" in a half hands the bonus to any adjacent job for free,
    # which is the same leak RULE 2 warns about for domain_title_terms.
    generic = {"engineer", "developer", "software", "technology", "tech",
               "engineering", "development", "programming", "it", "computer"}
    for name, half in (("domain_half_a", a), ("domain_half_b", b)):
        bad = [t for t in half if t in generic or _has(t, seniority)]
        if bad:
            corrections.append((name, f"generic category words: {bad}"))
            half[:] = [t for t in half if t not in bad]

    overlap = set(a) & set(b)
    if overlap:
        corrections.append(("domain_half_b",
                            f"the same term on both sides: {sorted(overlap)}"))
        b[:] = [t for t in b if t not in overlap]

    # A bonus for naming both sides is worth nothing if no posting ever
    # names both. Measured, not assumed.
    pairs = 0
    if a and b:
        for title, _score, _sk, _co in rows:
            if any(x in title for x in a) and any(y in title for y in b):
                pairs += 1
    if a and b and pairs == 0:
        corrections.append(("domain_bonus",
                            "no listing in the corpus names both halves"))
        bonus = 0
    if bonus and not (a and b):
        corrections.append(("domain_bonus", "a half is empty, so it can "
                                            "never fire"))
        bonus = 0
    return {"domain_half_a": a, "domain_half_b": b, "domain_bonus": bonus,
            "pairs": pairs}, corrections


LAYOUTS_MEASURED = ("plain", "messy")


def inputs_for(person, rows, idx, total, seniority, want=12):
    """What the deterministic half already knows, as the model's context."""
    from bench.cold_start import from_resume, validated

    own = {s.strip().lower() for s in person["skills"]}
    keywords = validated(
        from_resume(person, seniority)
        + [k for k in ds.keywords_for(own, rows, idx, total, want,
                                      seniority=seniority)],
        rows, seniority)
    return person["headline"], own, keywords


def run(model, cache_path=None, asker=ask, layouts=LAYOUTS_MEASURED):
    import config
    from bench.people import PEOPLE

    cache_path = cache_path or os.path.join(
        HERE, "results",
        f"semantic-{model.replace(':', '_').replace('/', '_')}.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)

    rows = ds.corpus_rows()
    seniority = (tuple(config.SCORING["hard_drop_terms"])
                 + tuple(config.SCORING["soft_drop_terms"]))
    idx = ds.index(rows, seniority)
    for slug, person in PEOPLE.items():
        field, own, keywords = inputs_for(person, rows, idx, len(rows),
                                          seniority)
        for layout in layouts:
            key = f"{slug}-{layout}"
            if key in cache:
                continue
            try:
                cache[key] = asker(model, field, own, keywords)
            except Exception as exc:            # noqa: BLE001 - logged
                cache[key] = {"error": f"{type(exc).__name__}: {exc}"}
            got = cache[key]
            print(f"  {key:<16} {len(got.get('title_exclude') or [])} excludes,"
                  f" halves {len(got.get('domain_half_a') or [])}/"
                  f"{len(got.get('domain_half_b') or [])},"
                  f" bonus {got.get('domain_bonus')}", flush=True)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, indent=1)
    return cache


def report(model, cache=None):
    import config
    from bench.cold_start import from_resume
    from bench.people import PEOPLE

    cache = cache if cache is not None else run(model)
    rows = ds.corpus_rows()
    total = len(rows)
    seniority = (tuple(config.SCORING["hard_drop_terms"])
                 + tuple(config.SCORING["soft_drop_terms"]))
    idx = ds.index(rows, seniority)

    print(f"\n{'=' * 76}\n{model}: title_exclude and the domain halves"
          f"\n{'=' * 76}")
    print(f"  {'person':<9}{'proposed':>9}{'kept':>6}{'dropped':>9}"
          f"{'unverif':>9}{'halves':>8}{'pairs':>7}")
    kept_all, dropped_all, unverified = [], [], []
    pairs_zero = 0
    per_person = {}
    for slug, person in PEOPLE.items():
        field, own, keywords = inputs_for(person, rows, idx, total, seniority)
        base = sum(1 for r in rows if r[2] & own) / total
        for layout in LAYOUTS_MEASURED:
            answer = cache.get(f"{slug}-{layout}")
            if not answer or answer.get("error"):
                continue
            kept, corrections = check_exclusions(
                answer, rows, own, base, keywords, seniority,
                from_resume(person, seniority))
            halves, half_fixes = check_halves(answer, rows, seniority)
            per_person.setdefault(slug, []).append(
                ({t for t, _p, _n in kept}, halves))
            kept_all.extend((slug, t, p) for t, p, _n in kept)
            dropped_all.extend((slug, t, why) for t, why in corrections)
            unverified.extend((slug, t) for t, p, _n in kept if p is None)
            if layout == "plain":
                if halves["domain_half_a"] and halves["domain_half_b"] \
                        and halves["pairs"] == 0:
                    pairs_zero += 1
                print(f"  {slug:<9}"
                      f"{len(answer.get('title_exclude') or []):>9}"
                      f"{len(kept):>6}{len(corrections):>9}"
                      f"{sum(1 for _t, p, _n in kept if p is None):>9}"
                      f"{len(halves['domain_half_a'])}/"
                      f"{len(halves['domain_half_b']):<6}"
                      f"{halves['pairs']:>7}")

    proposed = len(kept_all) + len(dropped_all)
    print(f"\n  {proposed} exclusions proposed across {len(per_person)} "
          f"people x {len(LAYOUTS_MEASURED)} renderings")
    print(f"    {len(kept_all):>4} kept "
          f"({len(unverified)} of them unverifiable — too few rows to judge)")
    print(f"    {len(dropped_all):>4} dropped by a validator")
    if proposed:
        print(f"    {len(dropped_all) / proposed:.0%} of what the model "
              f"proposed was wrong")

    if dropped_all:
        reasons = {}
        for _slug, _term, why in dropped_all:
            # Group by KIND, not by wording. Each self-harm reason
            # carries its own percentage, so keying on the text printed
            # forty near-identical one-line entries.
            key = ("removes rows this person actually wants"
                   if "as often as the market" in why
                   else why.split(",")[0].split(" so ")[0][:52])
            reasons[key] = reasons.get(key, 0) + 1
        print("\n  why they were dropped:")
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>4}x  {why}")
        print("\n  the dangerous ones — excluding this person's own work:")
        shown = 0
        for slug, term, why in dropped_all:
            if ("own search" in why or "not a different career" in why
                    or "actually held" in why):
                print(f"    {slug:<9} {term!r}")
                shown += 1
            if shown >= 8:
                break

    verified = [(s, t, p) for s, t, p in kept_all if p is not None]
    if verified:
        mean = sum(p for _s, _t, p in verified) / len(verified)
        print(f"\n  exclusion precision of what survived: {mean:.2f} mean "
              f"over {len(verified)} verifiable terms")
        print("    (1.0 = removes nothing this person wants; "
              "0.0 = removes their own work)")

    # Stability, the measure a field with no answer key still has.
    jac = []
    for slug, runs in per_person.items():
        if len(runs) < 2:
            continue
        x, y = runs[0][0], runs[1][0]
        if x or y:
            jac.append(len(x & y) / len(x | y))
    if jac:
        print(f"\n  agreement between two renderings: Jaccard "
              f"{sum(jac) / len(jac):.2f} over {len(jac)} people")
    if pairs_zero:
        print(f"  {pairs_zero} people were given halves no listing "
              f"in the corpus names together")


def demo():
    sen = ("senior", "lead", "junior", "intern")
    # A market where react work is common and data work is a different
    # career that happens to share the word "engineer".
    rows = []
    rows += [("frontend developer", 40, frozenset({"react"}), f"a{i}")
             for i in range(60)]
    rows += [("react developer", 40, frozenset({"react"}), f"b{i}")
             for i in range(40)]
    rows += [("data engineer", 30, frozenset({"spark"}), f"c{i}")
             for i in range(50)]
    rows += [("warehouse operative", 0, frozenset(), f"d{i}")
             for i in range(50)]
    own = {"react"}
    total = len(rows)
    base = sum(1 for r in rows if r[2] & own) / total
    assert abs(base - 0.5) < 1e-9, base

    # A real career boundary: nothing it removes wants react.
    p, n = exclusion_precision("data engineer", rows, own, base)
    assert n == 50 and p == 1.0, (p, n)
    # This person's own work: every row it removes wants react, so it is
    # twice as relevant as the market and the precision goes negative.
    p, _n = exclusion_precision("frontend developer", rows, own, base)
    assert p == -1.0, p
    # Too few rows to judge is not the same as a bad term.
    assert exclusion_precision("sales engineer", rows, own, base)[0] is None

    # The own-career guard, which needs no corpus and so still works for
    # the people the corpus cannot verify.
    held = ["qa automation", "test engineer"]
    assert own_career("qa analyst", held), "shares 'qa'"
    assert own_career("test analyst", held), "shares 'test'"
    assert own_career("automation engineer", held), "shares 'automation'"
    # Sharing only a generic role word is not sharing a career: every
    # title in this market ends in engineer or developer.
    assert not own_career("data engineer", held)
    assert not own_career("devops engineer", held)
    # And its limit, worth stating: a synonym the résumé does not use
    # gets through. gopal writes "QA" and never "quality".
    assert not own_career("quality engineer", held)
    assert not own_career("anything", [])

    kept, dropped = check_exclusions(
        {"title_exclude": ["qa analyst", "data engineer"]},
        rows, own, base, [], sen, held)
    assert [t for t, _p, _n in kept] == ["data engineer"], kept
    assert "actually held" in dict(dropped)["qa analyst"]

    answer = {"title_exclude": ["data engineer", "frontend developer",
                                "senior developer", "react developer",
                                "unheard of role"],
              "domain_half_a": [], "domain_half_b": [], "domain_bonus": 0}
    kept, dropped = check_exclusions(answer, rows, own, base,
                                     ["react developer"], sen)
    names = [t for t, _p, _n in kept]
    whys = {t: why for t, why in dropped}
    assert names == ["data engineer", "unheard of role"], names
    # Its own search keyword, caught before the corpus is even consulted.
    assert "own search keywords" in whys["react developer"]
    assert "seniority" in whys["senior developer"]
    assert "not a different career" in whys["frontend developer"]
    # Unverifiable terms are kept, and marked so the report can say so.
    assert [p for t, p, _n in kept if t == "unheard of role"] == [None]

    # Halves: generic words leak the bonus to any adjacent job.
    halves, fixes = check_halves(
        {"domain_half_a": ["frontend", "engineer"],
         "domain_half_b": ["backend", "frontend"], "domain_bonus": 6}, rows, sen)
    assert halves["domain_half_a"] == ["frontend"], halves
    assert halves["domain_half_b"] == ["backend"], halves
    assert any("generic" in why for _f, why in fixes)
    assert any("both sides" in why for _f, why in fixes)

    # A bonus nothing can earn is switched off. No title here names a
    # frontend word and a backend word together.
    assert halves["pairs"] == 0 and halves["domain_bonus"] == 0
    assert any("names both halves" in why for _f, why in fixes)

    # A half that is empty cannot fire either.
    halves, fixes = check_halves({"domain_half_a": ["frontend"],
                                  "domain_half_b": [], "domain_bonus": 6},
                                 rows, sen)
    assert halves["domain_bonus"] == 0
    assert any("half is empty" in why for _f, why in fixes)

    # A real split, in a market that has postings naming both.
    pair_rows = rows + [("frontend backend developer", 40,
                         frozenset({"react"}), f"e{i}") for i in range(5)]
    halves, _fixes = check_halves({"domain_half_a": ["frontend"],
                                   "domain_half_b": ["backend"],
                                   "domain_bonus": 6}, pair_rows, sen)
    assert halves["pairs"] == 5 and halves["domain_bonus"] == 6

    # Saying "this field has no split" is a valid answer, not a failure.
    halves, fixes = check_halves({"domain_half_a": [], "domain_half_b": [],
                                  "domain_bonus": 0}, rows, sen)
    assert halves["domain_bonus"] == 0 and not fixes
    print("semantic demo ok")


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
