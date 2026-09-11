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


# The neutral point of the 1-5 weight scale RULE 1 uses, and the floor
# centrality() builds up from.
NEUTRAL_WEIGHT = 3


def centrality(term, text, vocab, pairs=None):
    """(1-5, why) — how central this skill is to THIS person.

    A flat 3 for every term was measured to cost real ranking. The
    corpus already says how much a term narrows the MARKET, through
    separation(); what it cannot say is how much of this PERSON the term
    is, and that is what the blend's other half wants. Gemini supplies
    it as a judgement. The résumé supplies it as structure.

    Three independent kinds of evidence, strongest first:

      listed in a dedicated skills section — the author declaring it,
        which is the strongest claim available and counts double
      named more than once — returned to rather than mentioned
      used in experience or projects — done, not just known

    Summed onto a floor of 1, which reaches 5 exactly when all three
    hold. No constant here is fitted to any benchmark: the 1-5 range is
    RULE 1's own scale, the floor is its bottom, and the doubling is the
    ordering above and nothing more.

    reweight_from_corpus still runs afterwards and blends this against
    the market exactly as it always has. This only decides what it
    starts from.
    """
    skills_text, other_text = sections(text)
    low = str(text).lower()
    listed = bool(occurrences(term, skills_text, vocab))
    used = bool(occurrences(term, other_text, vocab))
    repeated = len(re.findall(re.escape(str(term).lower()), low)) > 1
    # The same inheritance gated() applies, for the same reason and by
    # the same derived pairs: someone who lists "Lightning Web
    # Components" under skills and writes "LWC" in a bullet has listed
    # it. Without this the abbreviation scores 2 while its own
    # expansion scores 5, which is one skill held at two values.
    if not listed:
        pairs = pairs if pairs is not None else abbreviations(vocab)
        for other in sorted(pairs.get(term, ())):
            if occurrences(other, skills_text, vocab):
                listed = True
                break

    score = 1 + (2 if listed else 0) + (1 if repeated else 0) + (1 if used else 0)
    why = ", ".join(
        [w for w in ("listed under skills" if listed else "",
                     "named more than once" if repeated else "",
                     "used in experience or projects" if used else "") if w])
    return max(1, min(5, score)), (why or "present in the résumé")

# How many scanned terms the user-facing prose names before it stops
# counting. Enough to show what KIND of thing was added without the
# sentence becoming the whole paragraph.
SHOWN_IN_NOTES = 3


def widen(data, resume_text, output_dir=None, log=print, vocab=None):
    """`data` with market terms found in the résumé added to skill_weights.

    A no-op when the vocabulary is empty, which is what an absent or
    unscraped output/ produces — the same degradation
    reweight_from_corpus already relies on.
    """
    if vocab is None:
        import corpus_signal
        vocab = corpus_signal.vocabulary(output_dir)
    # No early return for an empty vocabulary or an empty résumé. It
    # read well and it was redundant: with nothing to search for, or
    # nothing to search, gated() finds nothing and the `not added`
    # branch below returns the caller's dict unchanged by the same
    # path. Two mechanisms for one behaviour, and only one of them
    # could fail a test. The degradation is unchanged and still
    # asserted three ways in demo() and in the production suite.
    weights = list(data.get("skill_weights") or ())
    have = {str(e.get("term", "")).strip().lower() for e in weights}
    added = [(term, why) for term, why in sorted(gated(resume_text, vocab).items())
             if term not in have]
    if not added:
        return data

    data = dict(data)
    data["skill_weights"] = weights + [
        {"term": term, "weight": centrality(term, resume_text, vocab)[0]}
        for term, _why in added]
    # The reasons are kept, and kept OUT of the prose. notes is read by a
    # person on Sweep's review screen and printed into the profile's
    # docstring under "HOW THE MODEL READ THIS RÉSUMÉ"; a résumé like
    # Kavya's yields thirty scanned terms, and thirty parenthetical
    # explanations there would bury the one or two sentences that
    # actually describe the parse. So the prose gets a count and a few
    # examples, deterministically ordered, and every reason survives in
    # skills_added for the profile to render and for anyone debugging.
    data["skills_added"] = dict(added)
    shown = ", ".join(term for term, _why in added[:SHOWN_IN_NOTES])
    more = len(added) - SHOWN_IN_NOTES
    data["notes"] = ((data.get("notes") or "").rstrip()
                     + f" Also found {len(added)} skill(s) written in the "
                       f"résumé and named by the market — {shown}"
                     + (f" and {more} more" if more > 0 else "")
                     + ".").strip()
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

    # centrality(): structural evidence, strongest first, on a floor of 1.
    doc = ("Technical Skills\nMobile: React Native, Apex\n"
           "Experience\n- Shipped React Native to production.\n"
           "- More React Native work.\n")
    listed_used_repeated, why = centrality("react native", doc, vocab)
    assert listed_used_repeated == 5, (listed_used_repeated, why)
    assert "listed under skills" in why and "more than once" in why
    # Listed and nothing else is mid-scale, not top.
    listed_only, why = centrality("apex", doc, vocab)
    assert listed_only == 3, (listed_only, why)
    assert why == "listed under skills"
    # Prose only is the floor plus its one signal.
    prose, why = centrality("soql", "Experience\n- Tuned soql once.\n", vocab)
    assert prose == 2, (prose, why)
    # Never outside RULE 1's scale.
    assert 1 <= centrality("cobol", doc, vocab)[0] <= 5
    # An abbreviation inherits its expansion's listing, as the gate does.
    abbr = ("Technical Skills\nFrontend: Lightning Web Components\n"
            "Experience\n- Built with LWC.\n")
    assert centrality("lwc", abbr, vocab, pairs={})[0] == 1
    assert centrality("lwc", abbr, vocab,
                      pairs=abbreviations(vocab))[0] == 3

    # KNOWN DEFECT, recorded rather than fixed because the scan is
    # frozen. occurrences() rejects a term followed by ".", which is
    # right for "react" inside "react.js" and wrong for a term at the
    # end of a sentence: "Built with LWC." does not match, so the use
    # in prose is invisible and lwc above scores 3 rather than 4. The
    # fix is to reject "." only when a letter follows it, and it
    # belongs with the scan, not here.
    assert occurrences("lwc", "Built with LWC.", vocab) is None
    assert occurrences("lwc", "Built with LWC today", vocab) == "boundary"

    # widen(): adds, never replaces, and says why in the notes.
    data = {"skill_weights": [{"term": "apex", "weight": 5}], "notes": "x"}
    out = widen(data, text, vocab=vocab)
    # A scanned term enters at its centrality, not at a flat 3.
    scanned = {e["term"]: e["weight"] for e in out["skill_weights"]}
    assert scanned["react native"] == centrality("react native", text,
                                                 vocab)[0]
    terms = {e["term"] for e in out["skill_weights"]}
    assert "react native" in terms and "apex" in terms
    assert "field sales" not in terms
    # The model's own weight is untouched.
    assert [e for e in out["skill_weights"] if e["term"] == "apex"] == [
        {"term": "apex", "weight": 5}]
    # The prose stays short and the reasons survive elsewhere.
    assert out["notes"].startswith("x")
    assert "react native" in out["notes"]
    assert "listed under skills" not in out["notes"], "reasons are not prose"
    assert out["skills_added"]["react native"] == "listed under skills"

    # Many additions do not become many sentences.
    wide_vocab = dict(vocab, **{f"skill {i}": 40 for i in range(12)})
    wide_text = ("Technical Skills\n"
                 + ", ".join(f"skill {i}" for i in range(12)) + "\n")
    many = widen({"skill_weights": []}, wide_text, vocab=wide_vocab,
                 log=lambda *a: None)
    assert len(many["skills_added"]) >= 12, many["skills_added"]
    assert many["notes"].count(",") <= SHOWN_IN_NOTES, many["notes"]
    assert "and 9 more" in many["notes"], many["notes"]
    assert len(many["notes"]) < 200, len(many["notes"])
    # Deterministic: the same input names the same examples every time.
    assert many["notes"] == widen({"skill_weights": []}, wide_text,
                                  vocab=wide_vocab,
                                  log=lambda *a: None)["notes"]
    assert data["skill_weights"] == [{"term": "apex", "weight": 5}], \
        "the caller's dict is not mutated"

    # Graceful degradation, three ways, all returning the CALLER'S OWN
    # dict — an absent output/ yields an empty vocabulary, and a profile
    # must pass through untouched rather than empty.
    assert widen(data, text, vocab={}) is data
    assert widen(data, "", vocab=vocab) is data
    assert widen(data, "nothing relevant here", vocab=vocab) is data
    print("skill_scan demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        sys.exit(__doc__)
