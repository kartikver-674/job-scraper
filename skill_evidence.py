"""Where a skill actually appears, and what that says about the candidate.

TWO FACTS, NOT ONE
------------------
A skill carries two independent properties and the engine has been
collapsing them into a single number:

    candidate importance   how central this is to what the person does
    market discrimination  how much naming it narrows the job market

They are not the same question and they do not have the same answer.
TypeScript is the language this candidate writes every working day AND
it appears in a third of all listings. Firebase Cloud Messaging is one
library used once in one side project AND it appears in under 1%. The
old path multiplied the two together, so the market's answer decided
both: TypeScript landed on 2, FCM on 5, and the review screen offered to
delete TypeScript.

This module answers only the FIRST question, from the résumé alone. The
market keeps answering the second, separately, exactly as it does today.

HOW IMPORTANCE IS DECIDED
-------------------------
Not by asking a model. The audit measured that: a free evidence call
timed out at 600s, a larger budget produced undecodable JSON at 233s,
and the bounded eight-candidate call that DID complete labelled the
candidate's project-only Node and Mongo as professional work — the exact
error the tiers exist to avoid. So the model keeps the job it is good at
(naming skills) and Python does the rest:

    1. split the résumé into sections, by heading
    2. find every occurrence of every alias, deterministically
    3. tier the concept from WHERE those occurrences are

Step 2 matters. Nothing here trusts a quote the model produced — every
span is found by searching the document, so the entity-to-span link is
true by construction rather than by assertion. The audit's compact probe
supplied a quote for FCM that was not in the résumé at all, and a quote
for Node that proved neither Node nor employment.

THE TIERS
---------
Semantic, and deliberately not a 0-100 score: a résumé cannot prove
calibrated proficiency, and inventing a number that looks like it can is
the failure this replaces.

    CORE              used in paid professional work
    STRONG_SECONDARY  built with, substantially, in projects
    SUPPORTING        claimed, or mentioned once in passing
    BACKGROUND        coursework, certification, or not yet done

BACKGROUND rather than the "generic / low signal" a fourth tier usually
gets called: generic is a fact about the MARKET, and this axis is
deliberately not the market. Git and Tailwind are both list-only claims
here and both land on SUPPORTING; that they differ enormously in how
much they narrow a search is the other axis's business, and it still
says so.

ORIGIN IS NOT EVIDENCE
----------------------
The same rules run for every concept whatever produced it. A term Qwen
named and a term the scanner recovered are tiered identically when the
résumé says the same thing about them, which is audit defect C1: the
same Apex evidence scored 3 when reported and 5 when recovered.

    python skill_evidence.py        # self-check, offline
"""

import re

import skill_concepts

# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------
#
# The smallest thing that works on real résumés: headings are their own
# line, and everything under one belongs to it. No document model, no
# nesting, no per-résumé configuration. An unrecognised heading does not
# start a section, so its content stays with whatever came before — a
# skill under a heading nobody has seen keeps the context of the section
# it is written in rather than vanishing.

WORK = "work"
PROJECT = "project"
SKILLS = "skills"
EDUCATION = "education"
CERTIFICATION = "certification"
SUMMARY = "summary"
OTHER = "other"

# Matched against a whole line, so a bullet mentioning "projects" does not
# open a section. Ordered: the first pattern that matches wins, and
# "technical skills" must be tried before the bare "experience" in
# "skills & experience".
_HEADINGS = (
    (SKILLS, r"(technical|core|key|professional)?\s*(skills|competenc\w*|"
             r"technolog\w*|tech\s+stack|toolkit)"),
    (WORK, r"(professional|work|relevant|industry)?\s*"
           r"(experience|employment|history|career)"),
    (PROJECT, r"(personal|selected|key|academic|side)?\s*(projects?|portfolio)"),
    (EDUCATION, r"(education|academics?|qualifications?|coursework)"),
    (CERTIFICATION, r"(certifications?|licen[cs]es?|courses?|training)"),
    (SUMMARY, r"(professional\s+summary|summary|about\s*(me)?|profile|"
              r"objective)"),
)

_HEADING_LINE = re.compile(
    r"^\s*(?:" + "|".join(p for _k, p in _HEADINGS) + r")\s*:?\s*$", re.I)

_MATCHERS = tuple((kind, re.compile(r"^\s*" + pattern + r"\s*:?\s*$", re.I))
                  for kind, pattern in _HEADINGS)

# A heading a PDF has broken — "T echnical Skills", "F rontend:", "T ools
# & Platforms:" — is still a heading, and the audited résumé has three of
# them. The same pattern with every space requirement stripped, matched
# against the line with every non-alphanumeric stripped, recognises them
# without a second table to keep in step.
_SPACELESS = tuple(
    (kind, re.compile(r"^" + pattern.replace(r"\s*", "").replace(r"\s+", "")
                      + r"$", re.I))
    for kind, pattern in _HEADINGS)

_FOLD = re.compile(r"[^a-z0-9]+")


def _heading(line):
    """The section this line opens, or None if it opens none."""
    text = str(line).strip()
    if not text or len(text) > 60:
        return None
    for kind, matcher in _MATCHERS:
        if matcher.match(text):
            return kind
    folded = _FOLD.sub("", text.lower())
    for kind, matcher in _SPACELESS:
        if folded and matcher.match(folded):
            return kind
    return None


# An entry line: an employer, a project, a degree. Projects write
# "MediCart | React Native, ..."; jobs write the company and then the
# title. Only used as a LABEL, so a miss costs provenance, never a tier.
_ENTRY_SPLIT = re.compile(r"\s*[|–—]\s*")


def sections(text):
    """[(kind, entry, start, end)] over the text, in order.

    Offsets are into the string passed in, so a span found later can be
    placed without re-scanning. Text before any heading is the header
    block, which is where a summary usually lives when it has no heading
    of its own.
    """
    lines, spans, at = str(text or "").splitlines(keepends=True), [], 0
    kind, entry, start = OTHER, "", 0
    bullet, previous = False, ""
    for line in lines:
        found = _heading(line)
        if found:
            if at > start:
                spans.append((kind, entry, start, at))
            kind, entry, start = found, "", at + len(line)
            bullet = False
        elif _BULLET.match(line):
            bullet = True
        elif (kind in (WORK, PROJECT, EDUCATION)
                and _is_entry(line, bullet, previous)):
            # Only once per block. An entry's header is often two lines —
            # the employer and then the title and dates, the degree under
            # the university — and taking the second would label the work
            # "used at Software Engineer January 2025 - Present" instead
            # of naming the company. A bullet is what ends a header.
            if bullet or not entry:
                if at > start:
                    spans.append((kind, entry, start, at))
                entry = _ENTRY_SPLIT.split(line.strip())[0].strip()
                start, bullet = at, False
        if line.strip():
            previous = line.rstrip()
        at += len(line)
    if at > start:
        spans.append((kind, entry, start, at))
    return spans


# A bullet is content; a line that is not a bullet and carries a name is
# the entry it belongs to. An entry label is only ever shown to a human —
# never consulted to decide a tier — so a miss costs provenance, not
# correctness.
_BULLET = re.compile(r"^\s*[-–—*•]")

# A PDF wraps a long bullet onto the next line, and that continuation is
# not a new entry. Two tells, and both are needed: "validation and error
# handling that cut..." gives itself away by starting lowercase, while
# "Google Maps proxy, with JWT auth..." does not — there the tell is that
# the line before it ended mid-sentence.
_ENDS_SENTENCE = re.compile(r"[.;:!?]\s*$")


def _is_entry(line, in_bullet, previous):
    stripped = str(line).strip()
    if not stripped or len(stripped) >= 120:
        return False
    if not re.match(r"[A-Z0-9]", stripped):
        # A real entry — an employer, a project, a degree — starts with a
        # capital. A wrapped clause does not.
        return False
    if in_bullet and not _ENDS_SENTENCE.search(previous):
        return False
    return bool(re.search(r"[A-Za-z]", stripped))


def section_at(offset, spans):
    """(kind, entry) for a position in the text."""
    for kind, entry, start, end in spans:
        if start <= offset < end:
            return kind, entry
    return OTHER, ""


# --------------------------------------------------------------------------
# Planned work is not experience
# --------------------------------------------------------------------------
#
# "Planned for a pluggable payment gateway" is the résumé saying the
# opposite of "I built this". One deterministic clause check, applied to
# the sentence the span sits in.

_PLANNED = re.compile(
    r"\b(planned|planning|plan\s+to|upcoming|roadmap|will\s+(be|add|build|"
    r"support)|to\s+be\s+(added|built|implemented)|future|proposed|"
    r"intend(ed|s)?\s+to|next\s+(phase|step))\b", re.I)

_SENTENCE = re.compile(r"[.;\n]")


def is_planned(text, start):
    """Does a planned-work marker govern the mention at this offset?

    The marker has to come BEFORE the mention, inside the same sentence.
    "Planned for a pluggable payment gateway" makes the gateway planned;
    it does not retroactively unbuild the things listed before it. The
    audited résumé writes

        ... a wallet escrow ledger in multi-document MongoDB
        transactions , Planned for a pluggable payment gateway.

    on one line, so a rule that looked at the whole sentence would have
    turned real MongoDB work into future work.
    """
    opened = max((m.end() for m in _SENTENCE.finditer(text, 0, start)),
                 default=0)
    return bool(_PLANNED.search(text, opened, start))


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------

class Evidence:
    """One occurrence of one concept, and where it sits.

    Found by searching the document, never supplied by a model: the link
    from concept to span is true because this code made it.
    """

    __slots__ = ("section", "entry", "start", "end", "alias", "planned")

    def __init__(self, section, entry, start, end, alias, planned=False):
        self.section = section
        self.entry = entry
        self.start = start
        self.end = end
        self.alias = alias
        self.planned = planned

    def __repr__(self):
        return (f"Evidence({self.section}, {self.entry!r}, "
                f"{self.start}:{self.end}, planned={self.planned})")

    def as_dict(self):
        return {"section": self.section, "entry": self.entry,
                "span": [self.start, self.end], "alias": self.alias,
                "planned": self.planned}


def find(concept, text, spans=None):
    """Every validated occurrence of this concept in the document."""
    text = str(text or "")
    low = text.lower()
    spans = sections(text) if spans is None else spans
    found = []
    for alias in concept.aliases:
        for match in skill_concepts.compile_alias(alias).finditer(low):
            section, entry = section_at(match.start(), spans)
            found.append(Evidence(
                section, entry, match.start(), match.end(), alias,
                planned=is_planned(low, match.start())))
    # Two aliases of one concept can match the same words ("node" inside
    # "node.js"). One occurrence is one piece of evidence.
    found.sort(key=lambda e: (e.start, -(e.end - e.start)))
    kept, covered = [], []
    for item in found:
        if any(s <= item.start and item.end <= e for s, e in covered):
            continue
        kept.append(item)
        covered.append((item.start, item.end))
    return kept


# --------------------------------------------------------------------------
# Tiers
# --------------------------------------------------------------------------

CORE = "CORE"
STRONG_SECONDARY = "STRONG_SECONDARY"
SUPPORTING = "SUPPORTING"
BACKGROUND = "BACKGROUND"

ORDER = (BACKGROUND, SUPPORTING, STRONG_SECONDARY, CORE)

# A project has to be more than a stack line to count as building
# something. The stack line names it; a bullet describing what was done
# with it is the evidence. Two occurrences inside one project entry is
# the smallest honest bar and it is what separates RentKaro's Socket.IO
# (named in the stack AND described in a bullet) from Cloudinary (named
# in the stack and never mentioned again).
PROJECT_DEPTH = 2


def tier(evidence):
    """(tier, why) for one concept, from where it appears.

    The order is evidence strength, and nothing else is consulted — not
    the market, not which extractor found it, not how rare it is.
    """
    real = [e for e in evidence if not e.planned]
    if not real:
        if evidence:
            return BACKGROUND, "only named as planned or future work"
        return BACKGROUND, "not found in the document"

    where = {}
    for item in real:
        where.setdefault(item.section, []).append(item)

    if where.get(WORK):
        entries = {e.entry for e in where[WORK] if e.entry}
        detail = f"used at {', '.join(sorted(entries))}" if entries \
            else "used in professional experience"
        return CORE, detail

    if where.get(PROJECT):
        by_entry = {}
        for item in where[PROJECT]:
            by_entry.setdefault(item.entry, []).append(item)
        deepest = max(len(v) for v in by_entry.values())
        if deepest >= PROJECT_DEPTH or len(by_entry) > 1:
            names = ", ".join(sorted(n for n in by_entry if n)) or "a project"
            return STRONG_SECONDARY, f"built with in {names}"
        return SUPPORTING, "named in a project's stack"

    if where.get(SKILLS):
        if where.get(SUMMARY):
            # Claimed in the list AND in the person's own description of
            # themselves, but nowhere they say they used it.
            return SUPPORTING, "listed under skills and named in the summary"
        return SUPPORTING, "listed under skills only"

    if where.get(SUMMARY):
        return SUPPORTING, "named in the summary only"

    if where.get(EDUCATION) or where.get(CERTIFICATION):
        kind = "coursework" if where.get(EDUCATION) else "a certification"
        return BACKGROUND, f"named only in {kind}"

    return SUPPORTING, "present in the document"


def rank(tier_name):
    """Where a tier sits, so two can be compared."""
    return ORDER.index(tier_name) if tier_name in ORDER else 0


# --------------------------------------------------------------------------
# The compatibility mapping
# --------------------------------------------------------------------------
#
# Everything downstream — config.SCORING, scraper.score_job, the review
# screen's stepper, every profile on disk — speaks integers 1 to 5. So the
# tier is mapped into that interface rather than replacing it.
#
# THE NUMBER IS NOT PROFICIENCY. It is a ranking contribution: how much
# this concept should move a job's score. The résumé cannot establish how
# good someone is at anything and this does not claim to.
#
# Each tier owns a BAND, and the market moves the weight only inside it:
#
#     CORE              5   band 4-5
#     STRONG_SECONDARY  4   band 3-4
#     SUPPORTING        3   band 2-3
#     BACKGROUND        2   band 1-2
#
# Bands overlap by one with their neighbour and with nothing else. That is
# the bounded rule: the market can separate two concepts the évidence
# ranks equally, and can nudge one past its immediate neighbour, but it
# can never carry SUPPORTING above CORE. FCM measured at under 1% of
# listings is SUPPORTING and rare: 3 + 1 = 4, clamped to 3. TypeScript at
# 32% is CORE and common: 5 - 1 = 4. Four beats three, which is the
# relationship the old multiply inverted.

BANDS = {
    CORE: (5, 4, 5),
    STRONG_SECONDARY: (4, 3, 4),
    SUPPORTING: (3, 2, 3),
    BACKGROUND: (2, 1, 2),
}

# How far the market may move a weight inside its band. One step: enough
# to order two concepts the résumé ranks alike, never enough to reorder
# what it ranks differently.
MARKET_STEP = 1


def weight(tier_name, separation=None):
    """The 1-5 ranking contribution for a tier, refined by the market.

    `separation` is corpus_signal.separation()'s 1-5 (1 = named by most
    listings, 5 = named by under 1%), or None when the corpus abstains —
    in which case the tier's own base is used unchanged.
    """
    base, low, high = BANDS.get(tier_name, BANDS[SUPPORTING])
    if separation is None:
        return base
    if separation <= 2:          # common: says little about which job
        adjusted = base - MARKET_STEP
    elif separation >= 4:        # rare: says a lot about which job
        adjusted = base + MARKET_STEP
    else:
        adjusted = base
    return max(low, min(high, adjusted))


def assess_all(concepts, text, separations=None):
    """Assess every concept together, so overlaps settle the same way
    scoring settles them.

    Alone, "react" finds itself inside every "React Native" on the page
    and inherits the mobile work as if it were web work — the accident
    skill_concepts.score already refuses to pay twice for. The rule is
    the same one: an occurrence wholly inside a LONGER concept's
    occurrence is that concept's, not this one's.

    The audited résumé is the case. React Native is the professional
    work; React appears on its own in the summary, in one project and in
    the skills list. Tiered separately they are both CORE; tiered
    together React Native is CORE and React is strong secondary, which
    is what the page actually says.
    """
    spans = sections(text)
    separations = separations or {}
    found = {c.id: find(c, text, spans) for c in concepts}
    longer = [(e.start, e.end, c.display)
              for c in concepts for e in found[c.id]]
    out = []
    for concept in concepts:
        mine, by = [], set()
        for item in found[concept.id]:
            owner = _shadowed_by(item, longer, concept.display)
            if owner:
                by.add(owner)
            else:
                mine.append(item)
        assessed = _assessed(concept, mine, separations.get(concept.id))
        if not mine and by:
            # Found on the page every time, and every time as part of a
            # longer name. "Firebase" appears only inside "Firebase FCM";
            # the page never claims the platform on its own. Say that,
            # rather than "not found" — which is not true and reads like
            # a bug.
            assessed["why"] = ("only ever named inside "
                               + ", ".join(sorted(by)))
            assessed["shadowed_by"] = sorted(by)
        out.append(assessed)
    return out


def _shadowed_by(evidence, spans, mine):
    """The concept whose longer occurrence swallows this one, if any."""
    width = evidence.end - evidence.start
    for start, end, display in spans:
        if display == mine:
            continue
        if start <= evidence.start and evidence.end <= end \
                and (end - start) > width:
            return display
    return None


def _assessed(concept, evidence, separation):
    name, why = tier(evidence)
    return {
        "id": concept.id,
        "display": concept.display,
        "tier": name,
        "why": why,
        "weight": weight(name, separation),
        "market_separation": separation,
        "evidence": [e.as_dict() for e in evidence],
        "sections": sorted({e.section for e in evidence if not e.planned}),
        "planned_only": bool(evidence) and all(e.planned for e in evidence),
    }


def assess(concept, text, spans=None, separation=None):
    """Everything this module knows about one concept, as plain data."""
    evidence = find(concept, text, spans)
    name, why = tier(evidence)
    return {
        "id": concept.id,
        "display": concept.display,
        "tier": name,
        "why": why,
        "weight": weight(name, separation),
        "market_separation": separation,
        "evidence": [e.as_dict() for e in evidence],
        "sections": sorted({e.section for e in evidence if not e.planned}),
        "planned_only": bool(evidence) and all(e.planned for e in evidence),
    }


def demo():
    """Self-check. `python skill_evidence.py` — offline, no model."""
    text = (
        "Summary\n"
        "Full-stack engineer building React Native apps with TypeScript.\n"
        "Technical Skills\n"
        "Languages: TypeScript, Apex, C++\n"
        "Frontend: React Native, Tailwind CSS, L WC\n"
        "Backend: Node.js, Mongoose\n"
        "Professional Experience\n"
        "Dealermatix Technologies Pvt Ltd\n"
        "Software Engineer January 2025 - Present\n"
        "- Build REST integrations between a React Native app and Salesforce.\n"
        "- Author Apex unit tests at 85% coverage.\n"
        "Projects\n"
        "RentKaro | React Native, Node.js, Redis, Socket.IO, Cloudinary\n"
        "- Developed a marketplace on a Node.js backend with Redis.\n"
        "- Added Socket.IO chat, and Planned for a pluggable payment gateway.\n"
        "Vintage Photo Restoration | Python, TensorFlow\n"
        "- Trained a GAN on 5,000 image pairs.\n"
        "Education\n"
        "B.E. Computer Science - Artificial Intelligence\n"
    )
    spans = sections(text)
    kinds = {kind for kind, _e, _s, _x in spans}
    assert {SUMMARY, SKILLS, WORK, PROJECT, EDUCATION} <= kinds, kinds

    def tier_of(term, weights=None):
        concept, = skill_concepts.from_weights(weights or {term: 3})
        return tier(find(concept, text, spans))[0]

    # Work evidence is CORE, whatever the market thinks of it.
    assert tier_of("react native") == CORE
    assert tier_of("apex") == CORE
    # ...and TypeScript, named in the summary and the skills list, is
    # CORE here only if work names it. It does not, so it is not.
    assert tier_of("typescript") == SUPPORTING, tier_of("typescript")

    # A project tool described in a bullet is STRONG_SECONDARY; one that
    # only appears in the stack line is SUPPORTING.
    assert tier_of("node.js") == STRONG_SECONDARY, tier_of("node.js")
    assert tier_of("socket.io") == STRONG_SECONDARY
    assert tier_of("cloudinary") == SUPPORTING, tier_of("cloudinary")

    # A skills-list-only claim is SUPPORTING, not promoted by rarity.
    assert tier_of("tailwind css") == SUPPORTING
    assert tier_of("mongoose") == SUPPORTING

    # The PDF-broken spelling still finds its evidence, through the
    # concept's aliases.
    assert tier_of("l wc") == SUPPORTING

    # Planned work never becomes experience.
    concept, = skill_concepts.from_weights({"payment gateway": 3})
    found = find(concept, text, spans)
    assert found and all(e.planned for e in found), found
    assert tier(found)[0] == BACKGROUND

    # An unrelated project survives, at low influence.
    assert tier_of("tensorflow") == SUPPORTING
    assert tier_of("python") == SUPPORTING

    # Unknown technology survives with its own evidence.
    assert tier_of("dealermatix") in (CORE, SUPPORTING)

    # --- the mapping ---------------------------------------------------------
    # CORE and common still outranks SUPPORTING and rare. This is the
    # relationship the old multiply inverted.
    assert weight(CORE, 1) == 4
    assert weight(SUPPORTING, 5) == 3
    assert weight(CORE, 1) > weight(SUPPORTING, 5)
    # Rarity refines inside the band and never leaves it.
    for sep in (None, 1, 2, 3, 4, 5):
        for name, (_base, low, high) in BANDS.items():
            assert low <= weight(name, sep) <= high, (name, sep)
    # An abstaining market changes nothing.
    assert weight(CORE) == 5 and weight(BACKGROUND) == 2

    # --- origin is not evidence -----------------------------------------------
    # The same concept, tiered from the same text, whoever named it.
    reported, = skill_concepts.from_weights({"apex": 3})
    recovered, = skill_concepts.from_weights({"apex": 5})
    assert tier(find(reported, text, spans)) == tier(find(recovered, text, spans))

    print("skill_evidence demo ok")


if __name__ == "__main__":
    demo()
