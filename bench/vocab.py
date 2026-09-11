"""Widen a skill list locally, without Gemini and without a lexicon.

The coverage experiment settled that thin vocabulary explains roughly
90% of the mean-score gap and 80% of the readable-rows gap: the same
listings, scored with 18 local terms against the 46 Gemini extracted,
moved a specialist's own roles from below the readable band to above it.
Borrowing Gemini's vocabulary is not a solution, it is the dependency.

This does it from two things already on hand: the term as the résumé
wrote it, and the market's own vocabulary in output/.

  1  MECHANICAL  surface variants of the term. "React.js" also gets
     written "reactjs", "react js" and "react"; "front-end" also gets
     "front end" and "frontend". These are transforms of the string, not
     knowledge about software — the same rules turn "Sales-Force" into
     "sales force" without anyone teaching them what Salesforce is.
  2  MARKET-VALIDATED  a variant survives only if the corpus already
     uses it as a skill. That is what stops the expansion inventing
     noise: it can only ever add a term employers are already asking
     for, and it adds nothing at all for a term nobody varies.

No model, no alias table, no profession anywhere in the file.

WHAT WOULD MAKE THIS WRONG, and is therefore measured: adding a variant
that is not the same skill. "java" and "javascript" are a mechanical hop
apart and are different things. So every proposed pair is scored on how
often the two co-occur in the same listing against how often chance
would put them there — two spellings of one skill appear together far
more than two different skills do — and the report shows the pairs it
accepted and rejected rather than only a count.

    python -m bench.vocab --demo
    python -m bench.vocab            # measure against the answer key
"""

import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

HERE = os.path.dirname(os.path.abspath(__file__))

# Suffixes that mark an implementation of a thing rather than a different
# thing: "react.js" and "react" are one skill. Stripping is proposed, not
# applied — the corpus still has to recognise the result.
_RUNTIME_SUFFIX = (".js", "js", ".net", "3", "2")


def mechanical(term):
    """Surface variants of one written skill. No knowledge, just string
    transforms, and the caller decides which survive."""
    base = str(term).strip().lower()
    if not base:
        return set()
    out = {base}
    # Separators are interchangeable in the wild: react.js / react js /
    # reactjs, front-end / front end / frontend.
    squeezed = re.sub(r"[\s._\-/]+", "", base)
    spaced = re.sub(r"[._\-/]+", " ", base).strip()
    out |= {squeezed, spaced, re.sub(r"\s+", "-", spaced)}
    # A parenthetical is usually a second name for the same thing:
    # "hibernate (jpa)" is hibernate, and it is also jpa.
    inside = re.findall(r"\(([^)]*)\)", base)
    outside = re.sub(r"\([^)]*\)", " ", base).strip()
    if inside:
        out.add(outside)
        out |= {i.strip() for i in inside if i.strip()}
    out.add(base.replace("&", "and"))
    # Runtime and version suffixes.
    for suffix in _RUNTIME_SUFFIX:
        for form in (base, squeezed, spaced):
            if form.endswith(suffix) and len(form) > len(suffix) + 2:
                out.add(form[: -len(suffix)].strip(" .-"))
    # Plurals, both directions, on the last word only.
    words = spaced.split()
    if words:
        last = words[-1]
        if last.endswith("s") and len(last) > 3:
            out.add(" ".join(words[:-1] + [last[:-1]]).strip())
        else:
            out.add(" ".join(words[:-1] + [last + "s"]).strip())
    return {v for v in out if v and len(v) > 1}


def cooccurrence(a, b, rows):
    """How much more often two terms share a listing than chance allows.

    Two spellings of one skill travel together; two different skills do
    not. This is the check that keeps "java" from dragging in
    "javascript".
    """
    total = len(rows)
    if not total:
        return 0.0
    seen_a = sum(1 for r in rows if a in r[2])
    seen_b = sum(1 for r in rows if b in r[2])
    both = sum(1 for r in rows if a in r[2] and b in r[2])
    if not seen_a or not seen_b:
        return 0.0
    expected = (seen_a / total) * (seen_b / total) * total
    return both / expected if expected else 0.0


# CO-OCCURRENCE WAS TRIED AS THE LEGITIMACY TEST AND IS WRONG. The idea
# was that two spellings of one skill travel together; the data says
# they do the opposite. A writer picks "REST API" or "REST APIs" and
# uses it throughout, so the two spellings are close to mutually
# EXCLUSIVE within a listing: the pair scored 1.9x and was rejected,
# which is a false rejection of an obvious alias. Worse, a base term the
# corpus has never seen — "hibernate (jpa)" as written — scores 0.0x by
# construction, rejecting "hibernate" and "jpa" for being unheard of
# under a name nobody uses. Co-occurrence measures complementarity, not
# synonymy.
#
# What remains is the market itself. A variant is added only if
# employers already ask for it by that name, which cannot invent a skill
# and cannot add a spelling nobody uses. The transforms are narrow
# enough not to cross between skills — none of them turns "java" into
# "javascript" — and every pair is listed in the report so the claim is
# auditable rather than asserted.
MIN_COOCCURRENCE = 0.0

# A variant nobody posts is not worth adding whatever it looks like.
MIN_LISTINGS = 5


def expand(term, vocab, rows, min_cooccurrence=MIN_COOCCURRENCE):
    """(accepted, rejected) variants of one term, with their evidence."""
    base = str(term).strip().lower()
    accepted, rejected = [], []
    for variant in sorted(mechanical(term) - {base}):
        listings = vocab.get(variant, 0)
        if listings < MIN_LISTINGS:
            continue
        lift = cooccurrence(base, variant, rows)
        # Kept as reported evidence, not as a gate — see above.
        if listings >= MIN_LISTINGS and lift >= min_cooccurrence:
            accepted.append((variant, listings, lift))
        else:
            rejected.append((variant, listings, lift))
    return accepted, rejected


def expand_all(skills, vocab, rows, min_cooccurrence=MIN_COOCCURRENCE):
    """(expanded skill set, [(original, variant, listings, lift)])."""
    out = {str(s).strip().lower() for s in skills if str(s).strip()}
    added = []
    for term in sorted(out):
        accepted, _rejected = expand(term, vocab, rows, min_cooccurrence)
        for variant, listings, lift in accepted:
            if variant not in out:
                out.add(variant)
                added.append((term, variant, listings, lift))
    return out, added


def report():
    import json
    from bench import orphan_skills as osk
    from bench.people import PEOPLE

    rows, _idx, vocab, _sen = osk.setup()

    print(f"\n{'=' * 78}\nlocal vocabulary expansion: does it add real variants?"
          f"\n{'=' * 78}")
    print(f"  {len(vocab)} skill terms seen across {len(rows)} listings\n")

    # -- the dedicated term-normalisation subtest ----------------------
    print("  TERM NORMALISATION (ReactJS vs React.js), the case the coverage")
    print("  result made relevant:\n")
    for term in ("React.js", "Node.js", "REST APIs", "front-end",
                 "Hibernate (JPA)", "java"):
        accepted, rejected = expand(term, vocab, rows)
        got = ", ".join(f"{v} ({n}, {l:.1f}x)" for v, n, l in accepted) or "-"
        print(f"    {term:<18} + {got}")
        for v, n, l in rejected:
            print(f"    {'':<18} X {v} ({n} listings, only {l:.1f}x "
                  f"co-occurrence — not the same skill)")

    # -- against the answer key ---------------------------------------
    print(f"\n  AGAINST THE ANSWER KEY — the skills are known, so an added")
    print(f"  term is legitimate only if it is another spelling of one.\n")
    print(f"    {'person':<10}{'skills':>7}{'+added':>7}{'total':>7}   added")
    total_added = 0
    for slug, person in PEOPLE.items():
        own = {s.strip().lower() for s in person["skills"]}
        expanded, added = expand_all(own, vocab, rows)
        total_added += len(added)
        names = ", ".join(v for _t, v, _n, _l in added) or "-"
        print(f"    {slug:<10}{len(own):>7}{len(added):>7}{len(expanded):>7}"
              f"   {names[:52]}")
    print(f"\n    {total_added} variants added across {len(PEOPLE)} people")

    # -- against the real résumés -------------------------------------
    path = os.path.join(HERE, "results", "real-qwen3_8b.json")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        real = json.load(fh)
    print(f"\n  AGAINST THE REAL RÉSUMÉS, against Gemini's vocabulary size\n")
    print(f"    {'person':<20}{'local':>7}{'+added':>7}{'expanded':>9}"
          f"{'gemini':>8}{'closed':>8}")
    for name in sorted(real):
        own = {s.strip().lower() for s in real[name]["skills"]}
        expanded, added = expand_all(own, vocab, rows)
        gem_path = f"/tmp/gemini_fields_{name}.json"
        gem = "-"
        closed = "-"
        if os.path.exists(gem_path):
            with open(gem_path, encoding="utf-8") as fh:
                terms = {e["term"].strip().lower()
                         for e in json.load(fh)["skill_weights"]}
            gem = str(len(terms))
            gap = len(terms) - len(own)
            closed = (f"{len(added) / gap:.0%}" if gap > 0 else "n/a")
        print(f"    {name:<20}{len(own):>7}{len(added):>7}{len(expanded):>9}"
              f"{gem:>8}{closed:>8}")


def demo():
    # Mechanical transforms: separators, parentheticals, runtime
    # suffixes, plurals. No knowledge of what any of these things are.
    assert "react" in mechanical("React.js")
    assert "reactjs" in mechanical("React.js")
    assert "react js" in mechanical("React.js")
    assert {"front end", "frontend"} <= mechanical("front-end")
    assert {"hibernate", "jpa"} <= mechanical("Hibernate (JPA)")
    assert "rest api" in mechanical("REST APIs")
    assert "apis" in mechanical("api")
    assert "css" in mechanical("css3")
    # The transform that would be dangerous is not generated: nothing
    # here turns one skill into a different one.
    assert "javascript" not in mechanical("java")
    assert "java" not in mechanical("javascript")
    assert mechanical("") == set() and mechanical("  ") == set()

    # Market validation is the gate. A variant nobody posts is not added
    # however well-formed it looks.
    rows = ([("a", 0, frozenset({"react.js", "react"}), f"c{i}")
             for i in range(30)]
            + [("b", 0, frozenset({"react"}), f"d{i}") for i in range(30)]
            + [("c", 0, frozenset(), f"e{i}") for i in range(100)])
    vocab = {}
    for _t, _s, skills, _c in rows:
        for skill in skills:
            vocab[skill] = vocab.get(skill, 0) + 1
    accepted, _rejected = expand("React.js", vocab, rows)
    assert [v for v, _n, _l in accepted] == ["react"], accepted
    # "reactjs" is a perfectly good variant and nobody in this market
    # uses it, so it is not added.
    assert "reactjs" not in {v for v, _n, _l in accepted}
    assert expand("cobol", vocab, rows) == ([], [])

    expanded, added = expand_all({"react.js"}, vocab, rows)
    assert expanded == {"react.js", "react"}
    assert added and added[0][0] == "react.js" and added[0][1] == "react"
    # Idempotent: expanding an expanded set adds nothing new.
    again, added2 = expand_all(expanded, vocab, rows)
    assert again == expanded and added2 == []

    # cooccurrence is reported, not used as a gate. It is kept because
    # the number is informative and because the reason it cannot gate is
    # worth being able to re-derive: two spellings of one skill are
    # often mutually exclusive in a document.
    assert cooccurrence("react", "react.js", rows) > 0
    assert cooccurrence("react", "cobol", rows) == 0.0
    assert cooccurrence("a", "b", []) == 0.0
    print("vocab demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    report()


if __name__ == "__main__":
    main()
