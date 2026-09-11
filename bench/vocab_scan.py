"""Read the résumé with the market's vocabulary, not only with a model.

Two experiments have narrowed the extraction problem to one shape. A
larger model bought one point of recall and nothing downstream, so size
is not the lever. And every term both models miss sits in the same
place: prose bullets and section headings. They read a "Technical
Skills" list almost perfectly and under-read everything else — which is
why "Salesforce" is absent from a Salesforce developer's skills, as a
heading and a modifier rather than a list item, while the market names
it 932 times.

So this reads the document a second way. The market's own 348-term
vocabulary is the query, the whole normalised résumé is the corpus, and
anything present is a candidate. It finds headings and prose equally
because it does not know what a heading is.

  MODEL       what the extractor returns today
  VOCABULARY  every market term occurring in the document
  UNION       both, which is the arm this exists to measure

An earlier attempt at this was abandoned for pulling "field sales" and
"change requests" out of achievement prose. Two things have changed
since and neither existed then: retrieval is idf-weighted, so a common
term cannot steer a search on its own, and keywords are ranked by held
title and specialist anchor before corpus lift. Whether that is enough
is the question, and false positives are classified by term rather than
counted, so the answer is inspectable.

Nothing replaces the model here. The union is built alongside, measured,
and integrated nowhere.

    python -m bench.vocab_scan --demo
    python -m bench.vocab_scan            # 13 answer-key people
    python -m bench.vocab_scan --dir <texts>   # and the real résumés
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

from bench import vocab as vocab_mod

HERE = os.path.dirname(os.path.abspath(__file__))

# A term shorter than this matches too much to be worth trusting: "go"
# and "r" and "c" are real skills and they are also inside thousands of
# ordinary words. The two-tier rule in bench/route.py made the same call.
SHORT = 3


def occurrences(term, text, vocab):
    """Is this market term actually in this document?

    Word-boundary first, because that is right for text that survived
    extraction intact. Then a glue-tolerant pass, because real résumé
    PDFs lose their spaces: one of these encodes "andsoql query" and
    "integratingsalesforce apex,smartstore", and a strict boundary finds
    apex and misses soql. The glued pass guards the right edge and
    checks the whole run against the vocabulary, so "mysql" does not
    yield "sql" — mysql is itself a skill — while "andsoql" does yield
    soql.
    """
    low = text.lower()
    needle = str(term).strip().lower()
    if len(needle) < SHORT:
        return None
    spans = [m.span() for m in re.finditer(
        r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9+#.])", low)]
    if spans and not all(_covered(span, needle, low, vocab) for span in spans):
        return "boundary"
    for match in re.finditer(re.escape(needle), low):
        if match.end() < len(low) and (low[match.end()].isalnum()
                                       or low[match.end()] in ".+#"):
            continue
        start = match.start()
        while start > 0 and low[start - 1].isalnum():
            start -= 1
        # Longest match wins here as it does on the boundary path:
        # "sql" inside "mysql" and "react" inside "React Native" are
        # both rejected by it, so the run-against-vocabulary check that
        # used to sit here was a second way of saying the same thing.
        if _covered(match.span(), needle, low, vocab):
            continue
        return "glued"
    return None


def _covered(span, needle, low, vocab):
    """Is this occurrence only part of a longer skill the market knows?

    "React Native" contains "react", and a document that says only
    "React Native" is not thereby claiming React. The same rule already
    stops "mysql" yielding "sql" in the glued pass; applying it to plain
    matches too means the longest market term at a position wins, which
    is the general form of it rather than a special case.
    """
    start, end = span
    for other in vocab:
        other = other.lower()
        if other == needle or needle not in other or len(other) <= len(needle):
            continue
        for match in re.finditer(re.escape(other), low):
            if match.start() <= start and match.end() >= end:
                return True
    return False


def scan(text, vocab):
    """{term: how it was found} for every market term in the document.

    Aliases go both ways: a document writing "React.js" is found by the
    market term "react", and a document writing "reactjs" is found too,
    because each market term is searched for under its own mechanical
    variants as well as its own name.
    """
    found = {}
    for term in vocab:
        how = occurrences(term, text, vocab)
        if how:
            found[term] = how
            continue
        for variant in sorted(vocab_mod.mechanical(term) - {term}):
            if len(variant) < SHORT:
                continue
            how = occurrences(variant, text, vocab)
            if how:
                found[term] = f"{how}:{variant}"
                break
    return found


def arms(model_skills, text, vocab):
    """The three sets under comparison."""
    model = {str(s).strip().lower() for s in model_skills if str(s).strip()}
    found = scan(text, vocab)
    return {"model": model, "vocabulary": set(found),
            "union": model | set(found)}, found


def score(got, want):
    from bench.score import norm

    got = {norm(g) for g in got if norm(g)}
    want = {norm(w) for w in want if norm(w)}
    hit = got & want
    p = len(hit) / len(got) if got else 0.0
    r = len(hit) / len(want) if want else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f1, sorted(want - got), sorted(got - want)


def report(directory=None):
    import collections
    from bench.people import PEOPLE
    from bench.run import RESUMES
    from bench import orphan_skills as osk
    from resume_parser import extract_text

    rows, _idx, vocab, _sen = osk.setup()
    with open(os.path.join(HERE, "results", "qwen3_8b.json"),
              encoding="utf-8") as fh:
        fields = json.load(fh)

    order = ("model", "vocabulary", "union")
    totals = {a: [] for a in order}
    false_positives = collections.Counter()
    recovered = collections.Counter()

    print(f"\n{'=' * 78}\nmarket-vocabulary scan against the answer key"
          f"\n{'=' * 78}")
    print(f"  {len(vocab)} market terms, 13 people whose skills are known\n")
    print(f"  {'person':<10}{'':<3}{'model P/R/F1':>22}"
          f"{'vocabulary P/R/F1':>22}{'union P/R/F1':>22}")
    for slug, person in PEOPLE.items():
        want = person["skills"]
        parsed = (fields.get(f"{slug}-plain") or {}).get("parsed") or {}
        text = extract_text(os.path.join(RESUMES, f"{slug}-plain.pdf"))
        sets, found = arms(parsed.get("skills") or [], text, vocab)
        cells = []
        for arm in order:
            p, r, f1, missing, spurious = score(sets[arm], want)
            totals[arm].append((p, r, f1, len(sets[arm])))
            cells.append(f"{p:.2f}/{r:.2f}/{f1:.2f}")
            if arm == "vocabulary":
                for term in spurious:
                    false_positives[term] += 1
            if arm == "model":
                for term in missing:
                    if term in {t.lower() for t in sets["vocabulary"]}:
                        recovered[term] += 1
        print(f"  {slug:<10}{'':<3}{cells[0]:>22}{cells[1]:>22}{cells[2]:>22}")

    print(f"\n  {'arm':<14}{'precision':>11}{'recall':>9}{'F1':>7}{'terms':>8}")
    for arm in order:
        got = totals[arm]
        n = len(got)
        print(f"  {arm:<14}{sum(g[0] for g in got)/n:>11.2f}"
              f"{sum(g[1] for g in got)/n:>9.2f}{sum(g[2] for g in got)/n:>7.2f}"
              f"{sum(g[3] for g in got)/n:>8.1f}")

    if recovered:
        print(f"\n  recovered by the scan, missed by the model "
              f"({sum(recovered.values())} instances):")
        for term, n in recovered.most_common(12):
            print(f"    {n:>2}x  {term}")
    print(f"\n  FALSE POSITIVES of the vocabulary arm, by term "
          f"({sum(false_positives.values())} instances, "
          f"{len(false_positives)} distinct):")
    for term, n in false_positives.most_common(18):
        print(f"    {n:>2}x  {term}")

    if not directory:
        return
    # -- the three real résumés, hand-verified ground truth ------------
    with open(os.path.join(directory, "truth_skills.json"),
              encoding="utf-8") as fh:
        truth = json.load(fh)
    with open(os.path.join(HERE, "results", "extract-models.json"),
              encoding="utf-8") as fh:
        models = json.load(fh)
    print(f"\n{'=' * 78}\nthe three real résumés\n{'=' * 78}")
    print(f"  {'person':<20}{'arm':<12}{'P':>6}{'R':>6}{'F1':>6}{'terms':>7}")
    for name in ("kanav_reactnative", "lovish", "kavya"):
        want = truth[name]["tech"]
        with open(os.path.join(directory, name + ".txt"),
                  encoding="utf-8") as fh:
            text = fh.read()
        got = models.get(f"qwen3:8b::{name}", {}).get("skills") or []
        sets, _found = arms(got, text, vocab)
        for arm in order:
            p, r, f1, missing, _sp = score(sets[arm], want)
            print(f"  {name if arm == 'model' else '':<20}{arm:<12}"
                  f"{p:>6.2f}{r:>6.2f}{f1:>6.2f}{len(sets[arm]):>7}")
        _p, _r, _f, still, _s = score(sets["union"], want)
        print(f"    still missing after union: {still[:8]}")


def sections_of(text):
    """(skills-section text, everything-else text).

    Reuses the heading detection bench/variants.py already has, so the
    two files agree on what a section is.
    """
    from bench.variants import sections

    skills, other = [], []
    for head, body in sections(text):
        target = (skills if head and re.search(r"skill|competenc|technolog",
                                               head, re.I) else other)
        target.extend(body)
    return "\n".join(skills), "\n".join(other)


def abbreviations(vocab):
    """{term: {its abbreviation or its expansion}} from the vocabulary.

    Derived, not listed. A multi-word market term whose initials are
    THEMSELVES a market term is an abbreviation pair: "lightning web
    components" gives "lwc", both are terms employers use, so the two
    are the same skill written two ways. Nothing here knows what either
    of them is, and the same rule pairs any acronym the market happens
    to use alongside its expansion.
    """
    pairs = {}
    for term in vocab:
        words = [w for w in str(term).split() if w]
        if len(words) < 2:
            continue
        acronym = "".join(w[0] for w in words)
        if len(acronym) < 2 or acronym == term or acronym not in vocab:
            continue
        pairs.setdefault(acronym, set()).add(term)
        pairs.setdefault(term, set()).add(acronym)
    return pairs


def gated(text, vocab, pairs=None):
    """Vocabulary hits that clear a deterministic quality gate.

    Two structural conditions, no tuned numbers:

      CLAIMED   the term is inside a skills section, where the author is
                listing what they know, OR it occurs more than once in
                the document. A term someone lists, or returns to, is a
                claim. A term appearing once in passing is context —
                "field sales platform" describes the product Kanav built
                and is not a skill of his, and it appears exactly once
                outside any skills section.
      EXACT     the match is on the term itself at a word boundary, not
                on a variant and not through the glue-tolerant pass.
                Those two are how the scan reaches damaged PDFs, and
                they are the weaker evidence, so they need the claim.

    Returns {term: why}, so a rejected term can be argued with.
    """
    found = scan(text, vocab)
    skills_text, _other = sections_of(text)
    low = text.lower()
    pairs = pairs if pairs is not None else abbreviations(vocab)
    kept = {}
    for term, how in found.items():
        in_skills = bool(occurrences(term, skills_text, vocab))
        # An abbreviation inherits the structural evidence of its
        # expansion. Lovish writes "Lightning Web Components" in his
        # skills list and "LWC" once in a bullet; the abbreviation was
        # being discarded as a one-off prose mention while the very same
        # skill sat two lines above, spelled out.
        partner = ""
        if not in_skills:
            for other in sorted(pairs.get(term, ())):
                if occurrences(other, skills_text, vocab):
                    in_skills, partner = True, other
                    break
        repeated = len(re.findall(re.escape(term.lower()), low)) > 1
        exact = how == "boundary"
        if in_skills:
            kept[term] = (f"its expansion {partner!r} is listed under skills"
                          if partner else "listed under skills")
        elif repeated and exact:
            kept[term] = "occurs more than once, exact match"
        elif exact and how == "boundary" and term in skills_text.lower():
            kept[term] = "in the skills section"
    return kept, found


def demo():
    vocab = {"react native": 800, "soql": 28, "mysql": 40, "sql": 1000,
             "apex": 45, "field sales": 4, "go": 50, "react.js": 300}
    text = ("Technical Skills\n"
            "Mobile: React Native, Apex\n"
            "Experience\n"
            "- Built a field sales platform, andsoql query tuning.\n"
            "- Used MySQL throughout.\n")

    found = scan(text, vocab)
    assert found["react native"] == "boundary"
    assert found["apex"] == "boundary"
    # The glue-tolerant pass: "andsoql" yields soql, and "mysql" does
    # NOT yield sql, because mysql is itself a known skill.
    assert found["soql"].startswith("glued"), found["soql"]
    assert found["mysql"] == "boundary"
    assert "sql" not in found, found.get("sql")
    # Short terms match too much to trust. The guard has to be tested on
    # text where a short term genuinely WOULD match, or it is untested:
    # "go" does not match "going" for boundary reasons anyway.
    assert occurrences("go", "we go fast", vocab) is None, "short guard"
    assert occurrences("apex", "we use apex", vocab) == "boundary"
    assert "go" not in found
    assert occurrences("go", "we are going somewhere", vocab) is None

    # Aliases both ways: the market term is "react.js" and the document
    # says "React Native" — different skills, so no false hit — while a
    # document writing the variant is still found.
    assert "react.js" not in found
    assert "react.js" in scan("I use ReactJS daily", vocab)

    kept, allhits = gated(text, vocab)
    # Listed under skills: kept. Mentioned once in a bullet describing
    # the product: not a claim about the person.
    assert "react native" in kept and "apex" in kept
    assert "field sales" in allhits and "field sales" not in kept, kept
    # The repetition clause: a term outside any skills section, named
    # more than once, is a claim rather than passing context.
    twice = ("Technical Skills\nMobile: React Native\n"
             "Experience\n- Tuned soql heavily.\n- More soql work.\n")
    kept2, all2 = gated(twice, vocab)
    assert "soql" in all2 and "soql" in kept2, (kept2, all2)
    once = ("Technical Skills\nMobile: React Native\n"
            "Experience\n- Tuned soql once.\n")
    kept3, all3 = gated(once, vocab)
    assert "soql" in all3 and "soql" not in kept3, (kept3, all3)
    assert all(v for v in kept.values()), "every kept term carries a reason"
    # The gate can only remove.
    assert set(kept) <= set(allhits)

    sk, other = sections_of(text)
    assert "React Native" in sk and "field sales" not in sk
    assert "field sales" in other

    # Abbreviation pairs are DERIVED from the vocabulary: a multi-word
    # term whose initials are themselves a market term.
    abbr_vocab = dict(vocab, **{"lwc": 35, "lightning web components": 33,
                                "field sales platform": 9})
    pairs = abbreviations(abbr_vocab)
    assert pairs["lwc"] == {"lightning web components"}, pairs.get("lwc")
    assert "lightning web components" in pairs
    # A multi-word term whose initials are NOT a market term makes no
    # pair, which is what keeps this from inventing relations.
    assert "field sales platform" not in pairs, pairs.get("field sales platform")
    assert abbreviations({"react native": 1}) == {}

    # The case this rule exists for: the expansion is listed under
    # skills, the abbreviation appears once in a bullet. Without the
    # rule the abbreviation is discarded as passing context.
    doc = ("Technical Skills\n"
           "Frontend: Lightning Web Components, Aura\n"
           "Experience\n- Built with Apex, LWC, SOQL for reporting.\n")
    without, _all1 = gated(doc, abbr_vocab, pairs={})
    with_, _all2 = gated(doc, abbr_vocab, pairs=pairs)
    assert "lwc" not in without, without
    assert "lwc" in with_, with_
    assert "expansion" in with_["lwc"]
    # And it does not open a door: a one-off product noun with no
    # expansion listed anywhere is still rejected.
    assert "field sales" not in gated(text, abbr_vocab, pairs=pairs)[0]

    p, r, f1, missing, spurious = score({"a", "b"}, ["a", "c"])
    assert p == 0.5 and r == 0.5 and missing == ["c"] and spurious == ["b"]
    sets, _f = arms(["Model Only"], text, vocab)
    assert sets["model"] == {"model only"}
    assert sets["union"] == sets["model"] | sets["vocabulary"]
    print("vocab_scan demo ok")


def main():
    args = sys.argv[1:]
    if "--demo" in args:
        return demo()
    directory = args[args.index("--dir") + 1] if "--dir" in args else None
    report(directory)


if __name__ == "__main__":
    main()
