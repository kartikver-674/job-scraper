"""Read a résumé with the market's vocabulary as well as with a model.

A model reads a "Technical Skills" list almost perfectly and under-reads
everything else. Measured on real résumés, roughly a third of the
technologies on a page are named only in experience bullets, project
descriptions or section headings, and the extractor returns none of
them: "Salesforce" is absent from a Salesforce developer's skills
because it appears as a heading and a modifier rather than a list item,
while the market names it in 932 listings.

Thin skill coverage was measured to cause about 90% of the ranking gap
against the hosted model — the same listings, scored with a wider
vocabulary, moved a specialist's own roles from below the readable band
to above it. A larger local model did not fix it: 70% more parameters
bought one point of recall.

So the document is read a second way. The market's own vocabulary is the
query, the whole résumé is the text, and anything present is a
candidate. It finds headings and prose equally because it does not know
what a heading is.

WHAT KEEPS IT HONEST

Every hit must clear a structural gate, because a term appearing once in
passing is context rather than a claim — "field sales platform"
describes a product someone built, not a skill they have:

  listed under a skills section, or
  occurring more than once and matched exactly, or
  an abbreviation whose expansion is listed under skills

Abbreviation pairs are derived from the vocabulary, not listed: a
multi-word term whose initials are themselves a market term. That is how
"LWC" inherits the evidence of "Lightning Web Components" two lines
above it, without this file knowing what either of them is.

Nothing here knows a profession. The same rules apply to a nurse's
résumé and would find a nurse's vocabulary, if that is what output/ held.

Degrades to a no-op: an absent output/ or an empty vocabulary means no
candidates and the model's own extraction passes through untouched.

    python -m skill_scan --demo
"""

import re
import sys

# Below this length a term matches too much to trust: "go" and "c" are
# real skills and they are also inside thousands of ordinary words.
SHORT = 3

# Section headings, as résumés write them.
_HEADING = re.compile(
    r"^\s*(professional\s+summary|summary|about\s+me|core\s+skills|"
    r"technical\s+skills|skills|professional\s+experience|experience|"
    r"projects|education|certifications|core\s+competencies)\s*:?\s*$",
    re.I)

_SKILLS_HEADING = re.compile(r"skill|competenc|technolog", re.I)

# Suffixes marking an implementation of a thing rather than a different
# thing: "react.js" and "react" are one skill.
_RUNTIME_SUFFIX = (".js", "js", ".net", "3", "2")


def mechanical(term):
    """Surface variants of one written skill — string transforms only."""
    base = str(term).strip().lower()
    if not base:
        return set()
    out = {base}
    squeezed = re.sub(r"[\s._\-/]+", "", base)
    spaced = re.sub(r"[._\-/]+", " ", base).strip()
    out |= {squeezed, spaced, re.sub(r"\s+", "-", spaced)}
    inside = re.findall(r"\(([^)]*)\)", base)
    if inside:
        out.add(re.sub(r"\([^)]*\)", " ", base).strip())
        out |= {i.strip() for i in inside if i.strip()}
    out.add(base.replace("&", "and"))
    for suffix in _RUNTIME_SUFFIX:
        for form in (base, squeezed, spaced):
            if form.endswith(suffix) and len(form) > len(suffix) + 2:
                out.add(form[: -len(suffix)].strip(" .-"))
    words = spaced.split()
    if words:
        last = words[-1]
        if last.endswith("s") and len(last) > 3:
            out.add(" ".join(words[:-1] + [last[:-1]]).strip())
        else:
            out.add(" ".join(words[:-1] + [last + "s"]).strip())
    return {v for v in out if v and len(v) > 1}


def _covered(span, needle, low, vocab):
    """Is this occurrence only part of a longer skill the market knows?

    "React Native" contains "react", and a document saying only "React
    Native" is not claiming React; "mysql" is not claiming "sql".
    Longest market term at a position wins.
    """
    start, end = span
    for other in vocab:
        other = str(other).lower()
        if other == needle or needle not in other or len(other) <= len(needle):
            continue
        for match in re.finditer(re.escape(other), low):
            if match.start() <= start and match.end() >= end:
                return True
    return False


def occurrences(term, text, vocab):
    """"boundary", "glued", or None — how this term appears, if at all.

    The glued pass exists because real résumé PDFs lose their spaces:
    one encodes "andsoql query", and a strict boundary finds apex and
    misses soql.
    """
    low = str(text).lower()
    needle = str(term).strip().lower()
    if len(needle) < SHORT:
        return None
    spans = [m.span() for m in re.finditer(
        r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9+#.])", low)]
    if spans and not all(_covered(s, needle, low, vocab) for s in spans):
        return "boundary"
    for match in re.finditer(re.escape(needle), low):
        if match.end() < len(low) and (low[match.end()].isalnum()
                                       or low[match.end()] in ".+#"):
            continue
        if _covered(match.span(), needle, low, vocab):
            continue
        return "glued"
    return None


def scan(text, vocab):
    """{market term: how it was found} for every one present."""
    found = {}
    for term in vocab:
        how = occurrences(term, text, vocab)
        if how:
            found[term] = how
            continue
        for variant in sorted(mechanical(term) - {term}):
            if len(variant) < SHORT:
                continue
            how = occurrences(variant, text, vocab)
            if how:
                found[term] = f"{how}:{variant}"
                break
    return found


def sections(text):
    """(skills-section text, everything else)."""
    skills, other, in_skills = [], [], False
    for line in str(text).splitlines():
        if _HEADING.match(line):
            in_skills = bool(_SKILLS_HEADING.search(line))
            continue
        (skills if in_skills else other).append(line)
    return "\n".join(skills), "\n".join(other)


def abbreviations(vocab):
    """{term: {partner}} — a multi-word term whose initials are also a term."""
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
    """{term: why it was kept} — the hits that clear the structural gate."""
    found = scan(text, vocab)
    skills_text, _other = sections(text)
    low = str(text).lower()
    pairs = pairs if pairs is not None else abbreviations(vocab)
    kept = {}
    for term, how in found.items():
        in_skills = bool(occurrences(term, skills_text, vocab))
        partner = ""
        if not in_skills:
            for other in sorted(pairs.get(term, ())):
                if occurrences(other, skills_text, vocab):
                    in_skills, partner = True, other
                    break
        if in_skills:
            kept[term] = (f"its expansion {partner!r} is listed under skills"
                          if partner else "listed under skills")
        elif how == "boundary" and len(re.findall(re.escape(term), low)) > 1:
            kept[term] = "named more than once"
    return kept


# The neutral point of the 1-5 weight scale RULE 1 uses. A scanned term
# has no model opinion attached, and reweight_from_corpus runs after
# this and moves it against the market like any other.
NEUTRAL_WEIGHT = 3


def widen(data, resume_text, output_dir=None, log=print, vocab=None):
    """`data` with market terms found in the résumé added to skill_weights.

    A no-op when the vocabulary is empty, which is what an absent or
    unscraped output/ produces — the same degradation
    reweight_from_corpus already relies on.
    """
    if vocab is None:
        import corpus_signal
        vocab = corpus_signal.vocabulary(output_dir)
    if not vocab or not resume_text:
        return data
    weights = list(data.get("skill_weights") or ())
    have = {str(e.get("term", "")).strip().lower() for e in weights}
    added = [(term, why) for term, why in sorted(gated(resume_text, vocab).items())
             if term not in have]
    if not added:
        return data

    data = dict(data)
    data["skill_weights"] = weights + [
        {"term": term, "weight": NEUTRAL_WEIGHT} for term, _why in added]
    # Said out loud and carried into the profile, because these are
    # skills the model did not report and a person reviewing the profile
    # should be able to see where each came from.
    note = "; ".join(f"{term} ({why})" for term, why in added)
    data["notes"] = ((data.get("notes") or "").rstrip()
                     + f" Added from the market vocabulary found in the "
                       f"résumé: {note}.").strip()
    log(f"  added {len(added)} skill(s) the model did not report, "
        f"found in the résumé and named by the market:")
    for term, why in added[:10]:
        log(f"    {term:<28} {why}")
    if len(added) > 10:
        log(f"    ... and {len(added) - 10} more")
    return data


def demo():
    vocab = {"react native": 800, "soql": 28, "mysql": 40, "sql": 1000,
             "apex": 45, "field sales": 4, "go": 50, "react.js": 300,
             "lwc": 35, "lightning web components": 33}
    text = ("Technical Skills\n"
            "Mobile: React Native, Apex\n"
            "Experience\n"
            "- Built a field sales platform, andsoql query tuning.\n"
            "- Used MySQL throughout.\n")

    found = scan(text, vocab)
    assert found["react native"] == "boundary"
    assert found["soql"].startswith("glued"), found["soql"]
    assert found["mysql"] == "boundary"
    # Longest match wins: mysql is not sql, React Native is not react.
    assert "sql" not in found and "react.js" not in found
    # Short terms match too much to trust.
    assert occurrences("go", "we go fast", vocab) is None
    assert occurrences("apex", "we use apex", vocab) == "boundary"

    kept = gated(text, vocab)
    assert "react native" in kept and "apex" in kept
    assert "field sales" in found and "field sales" not in kept, kept
    assert all(kept.values()), "every kept term carries a reason"

    # Repetition outside a skills section is a claim; once is context.
    twice = ("Technical Skills\nMobile: React Native\n"
             "Experience\n- Tuned soql heavily.\n- More soql work.\n")
    assert "soql" in gated(twice, vocab)
    once = "Technical Skills\nMobile: React Native\nExperience\n- Tuned soql.\n"
    assert "soql" not in gated(once, vocab)

    # Abbreviations are derived, and inherit their expansion's evidence.
    pairs = abbreviations(vocab)
    assert pairs["lwc"] == {"lightning web components"}
    assert abbreviations({"react native": 1}) == {}
    doc = ("Technical Skills\nFrontend: Lightning Web Components\n"
           "Experience\n- Built with Apex, LWC, SOQL.\n")
    assert "lwc" not in gated(doc, vocab, pairs={})
    assert "expansion" in gated(doc, vocab, pairs=pairs)["lwc"]

    skills_text, other = sections(text)
    assert "React Native" in skills_text and "field sales" not in skills_text
    assert "field sales" in other

    # widen(): adds, never replaces, and says why in the notes.
    data = {"skill_weights": [{"term": "apex", "weight": 5}], "notes": "x"}
    out = widen(data, text, vocab=vocab)
    terms = {e["term"] for e in out["skill_weights"]}
    assert "react native" in terms and "apex" in terms
    assert "field sales" not in terms
    # The model's own weight is untouched.
    assert [e for e in out["skill_weights"] if e["term"] == "apex"] == [
        {"term": "apex", "weight": 5}]
    assert "react native" in out["notes"] and out["notes"].startswith("x")
    assert data["skill_weights"] == [{"term": "apex", "weight": 5}], \
        "the caller's dict is not mutated"

    # Graceful degradation, three ways, all returning the input unchanged.
    assert widen(data, text, vocab={}) is data
    assert widen(data, "", vocab=vocab) is data
    assert widen(data, "nothing relevant here", vocab=vocab) is data
    print("skill_scan demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        sys.exit(__doc__)
