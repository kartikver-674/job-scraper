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

import functools
import hashlib
import re

import semantic_scope          # V3 Step 8. Scope only; off unless set.
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
    # "Tools & Technologies", "Tools and Platforms". `technolog\w*` above
    # matches the bare word; these are the prefixed spellings real résumés
    # use, and the one this module's own docstring names ("T ools &
    # Platforms:") was never actually recognised. The separator class is
    # written without a literal "&" requirement so the _SPACELESS variant
    # still matches — _FOLD strips punctuation before that comparison.
    (SKILLS, r"tools?\s*(?:[&+/]|and)?\s*(?:technolog\w*|platforms?)"),
    # "Expertise", "Areas of Expertise", "Skill Set" — three more labels
    # that opened no section at all, so everything under them was read as a
    # continuation of whatever came before.
    (SKILLS, r"(areas?\s*of\s*)?expertise"),
    (SKILLS, r"skills?\s*set"),
    # Tried BEFORE the WORK row below, whose second group contains
    # "career": "Career Objective" is a summary, not an employment section.
    (SUMMARY, r"career\s*objective"),
    (WORK, r"(professional|work|relevant|industry)?\s*"
           r"(experience|employment|history|career)"),
    # "Career History" and "Employment History". The row above matches
    # "history" alone and "career" alone but not the pair, because its
    # prefix group admits neither word — so a résumé using either spelling
    # opened NO work section, and nothing in the document could reach CORE.
    # Measured at two bands off every professional claim.
    (WORK, r"(career|employment|work)\s*history"),
    (PROJECT, r"(personal|selected|key|academic|side)?\s*(projects?|portfolio)"),
    # Tried before WORK's "relevant experience": "Relevant Coursework" is
    # the heading that leaked, and a résumé that puts it AFTER Experience
    # had its coursework tiered as professional work.
    (EDUCATION, r"(relevant|related|academic|additional|selected)?\s*"
                r"(coursework|course\s*work)"),
    (EDUCATION, r"(currently\s+)?(learning|studying)|in\s+progress|"
                r"(professional|continuing)\s+(development|education)"),
    (EDUCATION, r"(education|academics?|qualifications?)"),
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
    if _BULLET.match(text):
        return None
    if ":" in text and text.split(":", 1)[1].strip():
        # "- Technologies: Redis, MongoDB" is a stack line, not a heading.
        # Folding strips the colon and `technolog\w*` then swallows the
        # whole line, so this opened a SKILLS section and orphaned its own
        # content — the bullet's contents ended up in no section at all.
        # A real broken heading ("T ools & Platforms:") has nothing after
        # its colon.
        return None
    folded = _FOLD.sub("", text.lower())
    for kind, matcher in _SPACELESS:
        if folded and matcher.match(folded):
            return kind
    return None


# An entry line: an employer, a project, a degree. Projects write
# "PharmaDesk | React Native, ..."; jobs write the company and then the
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
# What a mention MEANS
# --------------------------------------------------------------------------
#
# Appearing in a work section is a fact about typography. The engine was
# reading it as a claim: any occurrence not flagged `planned` became
# professional experience, so "Evaluated but never used Kubernetes" in a
# job bullet tiered CORE. Six statuses replace that one boolean.
#
# Nothing here votes on keywords. Each marker family is looked for in a
# window scoped to what that family can grammatically govern, and the
# first family that matches decides:
#
#     NEGATED > PLANNED > LEARNING > USED > MENTIONED
#
# The windows differ because English scope differs, and the asymmetry is
# load-bearing. The audited résumé writes, on one line:
#
#     Shipped a wallet escrow ledger in multi-document MongoDB
#     transactions, Planned for a pluggable payment gateway.
#
# "Planned" opens a new scope after the comma; it does not reach back and
# unbuild the ledger. So a planned marker is looked for BEFORE the name
# anywhere in the sentence (a lead-in governs a list: "Planned: A, B, C")
# but AFTER it only to the next clause break. Negation is looked for
# across the whole sentence in both directions, because "Evaluated
# Kubernetes but did not adopt it" puts the denial after a conjunction.

USED = "USED"
PLANNED = "PLANNED"
NEGATED = "NEGATED_OR_REJECTED"
LEARNING = "LEARNING_OR_COURSEWORK"
MENTIONED = "MENTIONED"
UNCERTAIN = "UNCERTAIN"

STATUSES = (USED, PLANNED, NEGATED, LEARNING, MENTIONED, UNCERTAIN)

# Statuses that assert the candidate has the skill. The rest are the
# document saying something else.
CLAIMED = (USED, MENTIONED, UNCERTAIN)

_PLANNED = re.compile(
    r"\b(planned|planning|plan\s+to|upcoming|roadmap|will\s+(be|add|build|"
    r"introduce|support|migrate|adopt)|to\s+be\s+(added|built|implemented|"
    r"introduced)|future|proposed|considering|evaluating|exploring|"
    r"looking\s+into|intend(ing|ed|s)?\s+to|next\s+(phase|step|quarter)|"
    r"scheduled)\b", re.I)

# Every marker is multi-word and about ADOPTION. A bare "not" never
# negates: "Fixed an issue where Redis was not reconnecting" is Redis
# work, and a rule that keyed on the word would have deleted it. That is
# the whole false-negation control — there is no scoring to tune.
_NEGATED = re.compile(
    r"\b(never\s+(used|worked|shipped|touched)|"
    r"not?\s+(used|using|adopted|shipped|familiar|experience|exposure)|"
    r"no\s+(hands[\s-]?on|prior|direct|professional)\s+\w+|"
    r"did\s+not\s+(use|adopt|ship|pursue|proceed)|"
    r"without\s+(using|adopting)|"
    r"rejected|ruled\s+out|dropped\s+in\s+favou?r|decided\s+against|"
    r"discounted|abandoned\s+(the\s+)?(idea|plan|attempt)|"
    r"but\s+(chose|went\s+with|picked|selected|shipped\s+with|"
    r"settled\s+on|used)\b)", re.I)

_LEARNING = re.compile(
    r"\b(learning|studying|self[\s-]?taught|tutorial|bootcamp|"
    r"course\s+(on|in)|working\s+through|enrolled|coursework|"
    r"familiaris\w+|familiariz\w+|reading\s+about|getting\s+up\s+to\s+"
    r"speed|beginner|exploring\s+in\s+my\s+own\s+time)\b", re.I)

# Positive evidence that the candidate DID something with it, in two
# kinds. R4a asked one question — was this used at all — and a single
# lexicon answered it. R4b needs the two kinds apart, because "Implemented
# Redis caching" and "Cachely | Redis" are not the same evidence.
#
# _ACTION is a verb of work. Every entry is an explicit inflection, never
# a `\w*` stem, because the stems collide with ordinary nouns and names:
# `cach\w*` matched the project name "Cachely", `scal\w*` matched "Scala",
# `fix\w*` matched "Fixture", `own\w*` matched "ownership", `develop\w*`
# matched the job title "Developer". Each of those turned a stack line
# into a use claim.
_ACTION = re.compile(
    r"\b(built|build|building|developed|develop|developing|"
    r"implemented|implement|implementing|"
    r"designed|designing|shipped|shipping|deployed|deploying|"
    r"migrated|migrating|integrated|integrating|"
    r"wrote|written|writing|authored|author|created|creating|"
    r"maintained|maintaining|automated|automating|"
    r"optimi[sz]ed|optimi[sz]ing|refactored|refactoring|rewrote|rewritten|"
    r"scaled|scaling|tested|testing|debugged|debugging|fixed|fixing|"
    r"configured|configuring|administered|orchestrated|orchestrating|"
    r"instrumented|monitored|monitoring|streamed|streaming|"
    r"indexed|indexing|cached|caching|queried|querying|"
    r"added|adding|trained|training|cut|reduced|reducing|"
    r"improved|improving|launched|launching|delivered|delivering|"
    r"led|owned|ported|introduced|replaced|extended|upgraded|"
    r"tuned|tuning|profiled|benchmarked|secured|parallelis|parallelized|"
    r"leveraged|leveraging|used|using|utili[sz]ed|supported|supporting|"
    r"managed|managing)\b", re.I)

# _ATTACH only ties a tool to something. "services in Python" says the
# work happened; it does not say what the work was. That is real use and
# weaker evidence than an action, and R4b keeps the difference.
_ATTACH = re.compile(r"\b(with|in|on|via|through|across|over|into|from)\b",
                     re.I)

# Verbs that open a predicate without being evidence of building
# anything. "documented Kafka migration options" is a second thing the
# person did; it ends the previous verb's reach, and grants nothing of its
# own. Overlap with _ACTION is harmless — both start a new predicate.
_OTHER_VERB = re.compile(
    r"\b(documented?|documenting|evaluated?|evaluating|researched?|"
    r"researching|analy[sz]ed?|analy[sz]ing|reviewed?|reviewing|"
    r"presented?|presenting|proposed?|proposing|recommended?|recommending|"
    r"investigated?|investigating|explored?|exploring|considered?|"
    r"considering|assessed?|assessing|compared?|comparing|studied|studying|"
    r"learned|learning|attended|attending|participated|collaborated|"
    r"collaborating|coordinated|coordinating|assisted|assisting|mentored|"
    r"mentoring|planned|planning|scheduled|drafted|drafting|compiled|"
    r"compiling|prepared|preparing|discussed|negotiated|demonstrated|"
    r"piloted|prototyped|shadowed|observed|reported|reporting|tracked|"
    r"tracking|rejected|abandoned|dropped)\b", re.I)

# What can end one predicate and begin the next. The same vocabulary R4a
# uses for clause scope, plus the dashes résumés use as separators —
# _CLAUSE_BREAK itself is left byte-identical so R4a's planned window
# cannot move.
_PREDICATE_BREAK = re.compile(
    r"[,:;–—]|\b(?:and|but|while|whereas|although|though|however)\b", re.I)

# An adverb or connector between the break and the new verb. "…, then
# implemented Kafka consumers" is still a new predicate.
_LEADIN = re.compile(
    r"[\s,]*(?:(?:and|but|then|later|also|next|subsequently|afterwards?|"
    r"finally|eventually|before|after|now|additionally|further)\s+)*", re.I)

# A full stop ends a sentence; the dot in "Node.js", "v1.2" or "99.9%"
# does not. R4a only ever searched these windows for planned and negated
# markers, which are words, so the difference never showed. R4c reads the
# window for the verb that governs an occurrence, and "Built APIs with
# Node.js, Express and PostgreSQL" was being cut in half by a filename.
_TERMINATOR = re.compile(r"\.(?=\s|$)|[;!?]|\n")
_CLAUSE_BREAK = re.compile(
    r"[,:;]|\b(?:and|but|while|whereas|although|though|however)\b", re.I)


@functools.lru_cache(maxsize=4)
def _soft_newlines(text):
    """Offsets of the newlines that are wraps rather than breaks.

    Computed once per document. This used to be decided per newline per
    occurrence, and deciding it calls _heading(), so a 46-concept résumé
    ran the heading matchers thirty thousand times.
    """
    soft, at = set(), text.find("\n")
    while at >= 0:
        opened = text.rfind("\n", 0, at) + 1
        closed = text.find("\n", at + 1)
        before = text[opened:at].rstrip()
        after = text[at + 1:len(text) if closed < 0 else closed]
        if (before and after.strip()
                and not _ENDS_SENTENCE.search(before)
                and not _BULLET.match(after)
                and _heading(after) is None):
            soft.add(at)
        at = closed
    return frozenset(soft)


def _wrapped(text, at):
    """Is the newline at this offset a soft wrap rather than a break?

    A PDF breaks a line wherever the column ran out, so "Plan to migrate
    to\n  Kubernetes" is one thought and the old rule — any \n ends the
    sentence — could not see the marker. A wrap is a line that did not
    finish its punctuation and is not followed by a bullet or a heading.
    A trailing comma is a wrap too: "Built services using Node.js,\n
    Express and MongoDB" is one bullet and one predicate.
    """
    return at in _soft_newlines(text)


@functools.lru_cache(maxsize=2048)
def _sentence(text, start, end, bounds):
    """(open, close) of the sentence around an occurrence, inside bounds.

    Cached: classify(), shape() and _digest() all ask for the same
    sentence around the same occurrence, so it is computed once and
    answered three times.
    """
    lo, hi = bounds
    opened = lo
    for match in _TERMINATOR.finditer(text, lo, start):
        if match.group() == "\n" and _wrapped(text, match.start()):
            continue
        opened = match.end()
    closed = hi
    for match in _TERMINATOR.finditer(text, end, hi):
        if match.group() == "\n" and _wrapped(text, match.start()):
            continue
        closed = match.start()
        break
    return opened, closed


def _unit(text, start, bounds):
    """The LOGICAL line the occurrence sits on — a bullet, a stack line, a
    heading — clipped to its section so it can never reach the next
    employer.

    Logical, because a PDF breaks a line wherever the column ran out.
    "Built services using Node.js,\n  Express and MongoDB" is one bullet,
    and stopping at the newline hid the verb from everything on the
    continuation. Same soft-wrap test R4a already uses for sentences.
    """
    lo, hi = bounds
    opened, at = lo, text.rfind("\n", lo, start)
    while at >= lo:
        if not _wrapped(text, at):
            opened = at + 1
            break
        at = text.rfind("\n", lo, at)
    closed, at = hi, text.find("\n", start, hi)
    while 0 <= at < hi:
        if not _wrapped(text, at):
            closed = at
            break
        at = text.find("\n", at + 1, hi)
    return opened, closed


def _opens_predicate(text, at, limit):
    """Does a new predicate start here? A verb, after any connector."""
    lead = _LEADIN.match(text, at, limit)
    at = lead.end() if lead else at
    return bool(_ACTION.match(text, at, limit)
                or _OTHER_VERB.match(text, at, limit))


def _governed(text, start, end, bounds):
    """The predicate segment that grammatically governs this occurrence.

    A clause break ends the governing verb's reach only when what follows
    it starts a NEW predicate. That is the whole policy, and it is what
    separates the two cases that look identical to a comma-splitter:

        Built APIs with Node.js, Express and PostgreSQL.
            "Express" is not a verb, so `Built` governs all three.

        Implemented Redis caching, documented Kafka migration options.
            "documented" is, so `Implemented` stops at the comma.

    A coordinated list with no new predicate in it is therefore governed
    by the one verb in front of it, however many commas it contains.
    """
    opened, closed = _sentence(text, start, end, bounds)
    lo = opened
    for match in _PREDICATE_BREAK.finditer(text, opened, start):
        if _opens_predicate(text, match.end(), start):
            lo = match.end()
    hi = closed
    for match in _PREDICATE_BREAK.finditer(text, end, closed):
        if _opens_predicate(text, match.end(), closed):
            hi = match.start()
            break
    return lo, hi


def _bounds(offset, spans, length):
    """The section span an occurrence lives in — the hard edge no window
    may cross, so Employer A's bullet can never read Employer B's."""
    for _kind, _entry, start, end in spans or ():
        if start <= offset < end:
            return start, end
    return 0, length


def _digest(text, start, end, bounds):
    """A digest of the sentence the status was read from.

    Provenance that survives into production without carrying a line of
    anybody's résumé: given the document, an auditor can recompute this
    and confirm which sentence the classifier judged.
    """
    opened, closed = _sentence(text, start, end, bounds)
    return hashlib.sha256(
        text[opened:closed].strip().encode("utf-8")).hexdigest()[:12]


def is_planned(text, start, end=None):
    """Kept for callers that only ever asked the one old question."""
    return classify(text, start, start if end is None else end) == PLANNED


def _scoped(text, start, end, section, bounds, record=None):
    """V3 STEP 8. The same decision order and the same cue definitions as
    classify() below; only the WINDOWS are narrower (audit defect V3-C16).

    Each window is intersected with _governed() -- the clause segmentation
    shape() has always used for evidence depth, where a break ends a verb's
    reach only when what follows starts a new predicate -- and a cue in a
    different comma item from the concept no longer governs it. Intersected,
    never replaced: bounding PLANNED by _governed alone would WIDEN it, since
    its forward bound is already a bare clause break.
    """
    sentence = _sentence(text, start, end, bounds)
    governed = _governed(text, start, end, bounds)
    opened, closed = sentence
    ahead = _CLAUSE_BREAK.search(text, end, closed)

    def fires(rx, family, window):
        lo, hi = semantic_scope.narrow(window, governed)
        match, why = semantic_scope.first_governing(text, rx, lo, hi, start, end)
        if match is not None and record is not None:
            record.setdefault("decisions", []).append(
                {"span": [start, end], "cue": match.group(0),
                 "cue_family": family, "cue_position": list(match.span()),
                 "scope": text[lo:hi], "reason": why})
        return match

    if fires(semantic_scope.negated(_NEGATED), "NEGATED", sentence):
        return NEGATED
    if fires(_PLANNED, "PLANNED", (opened, ahead.start() if ahead else closed)):
        return PLANNED
    if section in (EDUCATION, CERTIFICATION):
        return LEARNING
    if fires(_LEARNING, "LEARNING", sentence):
        return LEARNING
    line_lo, line_hi = _unit(text, start, bounds)
    if _used(text, line_lo, start, end, line_hi):
        return USED
    return MENTIONED


def classify(text, start, end, section=OTHER, bounds=None, record=None):
    """The status of one occurrence. Deterministic, local, no model."""
    text = str(text or "")
    bounds = (0, len(text)) if bounds is None else bounds
    if semantic_scope.enabled():
        return _scoped(text, start, end, section, bounds, record)
    opened, closed = _sentence(text, start, end, bounds)

    # Negation reads the whole sentence: the denial can sit on either
    # side of the name, and past a conjunction.
    if _NEGATED.search(text, opened, start) or _NEGATED.search(text, end, closed):
        return NEGATED

    # Planned reads backwards to the sentence, forwards only to the next
    # clause break — see the module note above for why.
    ahead = _CLAUSE_BREAK.search(text, end, closed)
    if _PLANNED.search(text, opened, start) \
            or _PLANNED.search(text, end, ahead.start() if ahead else closed):
        return PLANNED

    if section in (EDUCATION, CERTIFICATION):
        return LEARNING
    if _LEARNING.search(text, opened, closed):
        return LEARNING

    # Use is read from the whole line, not the clause: a bullet's verb is
    # often several commas away from the tool it governs ("Built and
    # shipped the billing service, with Redis for idempotency keys").
    line_lo, line_hi = _unit(text, start, bounds)
    if _used(text, line_lo, start, end, line_hi):
        return USED
    return MENTIONED


def _used(text, line_lo, start, end, line_hi):
    """Either kind of positive evidence, anywhere on the line."""
    for pattern in (_ACTION, _ATTACH):
        if pattern.search(text, line_lo, start) \
                or pattern.search(text, end, line_hi):
            return True
    return False


# --------------------------------------------------------------------------
# How much the document actually shows
# --------------------------------------------------------------------------
#
# R4a settled what a mention MEANS. This settles how much a USED mention
# SHOWS, and it is a third axis — not importance, and emphatically not
# proficiency. A résumé cannot establish how good somebody is at anything;
# it can only be read for how much of their work it describes.
#
# The engine was using lexical repetition as the proxy, so this
#
#     Cachely | Redis / redis
#
# outranked this
#
#     - Implemented Redis-backed session caching with fail-open degradation.
#
# because the first produced two spans and the second produced one.
# Repetition is not depth. Naming a thing twice says nothing the first
# naming did not; describing what was built with it says a great deal.
#
#     NO_EVIDENCE      nothing the candidate claims
#     CLAIM_ONLY       named in a list, a stack, a summary, an entry header
#     INCIDENTAL_USE   tied to work, but nothing said about what was done
#     SUBSTANTIVE_USE  an action or responsibility statement names it
#     INDEPENDENT_USE  substantive, in two or more separately named entries
#
# There is deliberately NO "used repeatedly" rung. Adding one would put
# repetition back on the ladder under a better name.

NO_EVIDENCE = "NO_EVIDENCE"
CLAIM_ONLY = "CLAIM_ONLY"
INCIDENTAL_USE = "INCIDENTAL_USE"
SUBSTANTIVE_USE = "SUBSTANTIVE_USE"
INDEPENDENT_USE = "INDEPENDENT_USE"

STRENGTHS = (NO_EVIDENCE, CLAIM_ONLY, INCIDENTAL_USE, SUBSTANTIVE_USE,
             INDEPENDENT_USE)

# Sections where nothing is ever described as done. A skills list names
# things; it never says what was built, so no verb appearing on one of its
# lines ("Testing: Jest") can make it an action.
_CLAIM_SECTIONS = (SKILLS, SUMMARY, EDUCATION, CERTIFICATION, OTHER)

# A labelled stack line. "Tech: Redis, Node" is the project naming its
# ingredients, whatever words happen to be in the label.
_STACK_LINE = re.compile(
    r"\s*[-–—*•]?\s*(tech|technical|technologies|tool|tools|stack|"
    r"tech\s+stack|built\s+with|language|languages|framework|frameworks|"
    r"librar\w+|database|databases|platform|platforms|testing|"
    r"frontend|front[\s-]?end|backend|back[\s-]?end|devops|cloud|"
    r"skills?|other|infrastructure)\s*:", re.I)


def shape(text, start, end, section, bounds, header=False):
    """How much this one occurrence shows. CLAIM_ONLY unless it says more."""
    if section in _CLAIM_SECTIONS or header:
        return CLAIM_ONLY
    line_lo, line_hi = _unit(text, start, bounds)
    if _STACK_LINE.match(text, line_lo, line_hi):
        # Whether the line is a labelled stack is a fact about the LINE.
        return CLAIM_ONLY
    # Whether a verb governs this occurrence is a fact about its clause.
    lo, hi = _governed(text, start, end, bounds)
    if _ACTION.search(text, lo, start) or _ACTION.search(text, end, hi):
        return SUBSTANTIVE_USE
    if _ATTACH.search(text, lo, start) or _ATTACH.search(text, end, hi):
        return INCIDENTAL_USE
    return CLAIM_ONLY


def strength(evidence):
    """(strength, independent entries) for one concept.

    Independence is counted over separately NAMED entries, never over
    spans. Two bullets in one project are one entry; the same tool used in
    two projects is two. An entry the parser could not name does not count
    as independent — faking breadth out of an attribution failure is worse
    than reporting less.
    """
    claimed = [e for e in evidence if e.claimed]
    if not claimed:
        return NO_EVIDENCE, 0
    best = max((e.shape for e in claimed), key=STRENGTHS.index)
    entries = {(e.section, e.entry) for e in claimed
               if e.entry and e.shape in (INCIDENTAL_USE, SUBSTANTIVE_USE)}
    if best == SUBSTANTIVE_USE and len(entries) > 1:
        return INDEPENDENT_USE, len(entries)
    return best, len(entries)


def _at_least(name, floor):
    return STRENGTHS.index(name) >= STRENGTHS.index(floor)


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------

class Evidence:
    """One occurrence of one concept, and where it sits.

    Found by searching the document, never supplied by a model: the link
    from concept to span is true because this code made it.
    """

    __slots__ = ("section", "entry", "start", "end", "alias", "status",
                 "shape", "sha")

    def __init__(self, section, entry, start, end, alias, status=MENTIONED,
                 shape=CLAIM_ONLY, sha=""):
        self.section = section
        self.entry = entry
        self.start = start
        self.end = end
        self.alias = alias
        self.status = status
        self.shape = shape
        # A digest of the sentence this was read from, so an audit can
        # confirm the classifier saw what the résumé says without the
        # record carrying a line of anybody's résumé. Provenance, not a
        # quote.
        self.sha = sha

    @property
    def planned(self):
        """Kept: `planned` was this module's only semantic bit before R4a."""
        return self.status == PLANNED

    @property
    def claimed(self):
        """Does this occurrence assert the candidate has the skill?"""
        return self.status in CLAIMED

    def __repr__(self):
        return (f"Evidence({self.section}, {self.entry!r}, "
                f"{self.start}:{self.end}, {self.status}/{self.shape})")

    def as_dict(self):
        return {"section": self.section, "entry": self.entry,
                "span": [self.start, self.end], "alias": self.alias,
                "status": self.status, "shape": self.shape,
                "sha": self.sha,
                "planned": self.planned}


def find(concept, text, spans=None):
    """Every validated occurrence of this concept in the document."""
    text = str(text or "")
    low = text.lower()
    spans = sections(text) if spans is None else spans
    # Words that belong to a concept the registry says covers this one.
    # "React Native" in a work bullet is React Native's evidence and never
    # React's — and that has to be true whether or not React Native was
    # extracted for this candidate, or the tier a concept receives depends
    # on the extractor's luck with a DIFFERENT skill. Same rule the scorer
    # uses; this is not a change to tier policy.
    covering = skill_concepts.covering_spans(concept.id, low)
    found = []
    for alias in concept.aliases:
        for match in skill_concepts.compile_alias(alias).finditer(low):
            if skill_concepts._inside(match.span(), covering):
                continue
            section, entry = section_at(match.start(), spans)
            bounds = _bounds(match.start(), spans, len(text))
            status = classify(low, match.start(), match.end(), section,
                              bounds)
            # The entry's own header line names the stack; it never
            # describes work. `bounds[0]` is where that line starts.
            header = bool(entry) and _unit(low, match.start(),
                                           bounds)[0] == bounds[0]
            found.append(Evidence(
                section, entry, match.start(), match.end(), alias,
                status=status,
                shape=shape(low, match.start(), match.end(), section, bounds,
                            header),
                sha=_digest(low, match.start(), match.end(), bounds)))
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

# The whole tier decision, in one table. Read top to bottom; the first
# row that matches wins. `strength` is the best shape among the concept's
# claimed occurrences IN THAT SECTION — never a count of them.
#
#     section    strength needed        tier
#     ---------  ---------------------  ----------------
#     work       INCIDENTAL_USE         CORE
#     project    SUBSTANTIVE_USE        STRONG_SECONDARY
#     project    (any claim)            SUPPORTING
#     skills     (any claim)            SUPPORTING
#     summary    (any claim)            SUPPORTING
#     education  (any claim)            BACKGROUND
#     work       CLAIM_ONLY             SUPPORTING
#     anywhere   (any claim)            SUPPORTING
#
# Two deliberate asymmetries:
#
# Work keeps INCIDENTAL_USE as its bar, not SUBSTANTIVE_USE. "Node.js
# microservices on AWS" is a real professional claim written the way
# résumés are actually written, and raising the bar would demote ordinary
# work prose across every document to buy a distinction this batch has no
# labels to justify.
#
# A project needs SUBSTANTIVE_USE, and needs it ONCE. The old rule wanted
# two occurrences in one entry, which is why one detailed implementation
# bullet lost to a stack line that spelled the tool twice.
#
# Breadth across entries raises the recorded STRENGTH to INDEPENDENT_USE
# and deliberately does not raise the tier: a project is a project however
# many of them there are, and promoting to CORE would make unpaid work
# indistinguishable from employment.


def tier(evidence):
    """(tier, why) for one concept, from where it appears.

    The order is evidence strength, and nothing else is consulted — not
    the market, not which extractor found it, not how rare it is.
    """
    real = [e for e in evidence if e.claimed]
    if not real:
        return BACKGROUND, _refused(evidence)

    where = {}
    for item in real:
        where.setdefault(item.section, []).append(item)

    def best(section):
        """The strongest shape this concept reaches inside one section."""
        items = where.get(section)
        if not items:
            return NO_EVIDENCE
        return max((e.shape for e in items), key=STRENGTHS.index)

    def names(section, floor):
        return sorted({e.entry for e in where.get(section, ())
                       if e.entry and _at_least(e.shape, floor)})

    if _at_least(best(WORK), INCIDENTAL_USE):
        entries = names(WORK, INCIDENTAL_USE)
        detail = f"used at {', '.join(entries)}" if entries \
            else "used in professional experience"
        return CORE, detail

    if where.get(PROJECT):
        if _at_least(best(PROJECT), SUBSTANTIVE_USE):
            entries = names(PROJECT, SUBSTANTIVE_USE)
            where_ = ", ".join(entries) or "a project"
            return STRONG_SECONDARY, f"built with in {where_}"
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

    if where.get(WORK):
        entries = {e.entry for e in where[WORK] if e.entry}
        where_ = f" at {', '.join(sorted(entries))}" if entries else ""
        return SUPPORTING, ("named in professional experience" + where_
                            + ", not described as used")

    return SUPPORTING, "present in the document"


def _refused(evidence):
    """Why a concept with occurrences still has no claim behind it."""
    if not evidence:
        return "not found in the document"
    seen = {e.status for e in evidence}
    if NEGATED in seen:
        return "named as evaluated or rejected, not as used"
    if LEARNING in seen:
        return "named as learning or coursework"
    return "only named as planned or future work"


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
        if not mine and not by:
            # find() now drops occurrences that belong to a concept the
            # REGISTRY says covers this one, before they are ever seen
            # here — so "React" in a React Native document arrives with no
            # evidence at all. Saying "not found in the document" would be
            # untrue and would read like a bug, so ask the registry why.
            # NOT named `longer`: that is the span list this loop is
            # still using for every other concept.
            for covering in skill_concepts.covered_by(concept.id):
                if skill_concepts.covering_spans(concept.id, text.lower()):
                    by.add(display_of(covering))
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


def occurrence(evidence):
    """One occurrence as an inspectable record: where, and what it meant.

    Offsets and a digest, never the words. The production importance
    record keeps these so a wrong tier can be argued with — before R4a it
    kept nothing, and every tier was unfalsifiable once the run ended.

    No `entry`: the label is a raw line lifted off the page — an employer
    name with its city still attached, and whatever spacing the PDF
    extractor produced — `why` already names the employers once, and
    repeating it per occurrence would put résumé text in every row of a
    record whose whole point is to avoid carrying any.
    """
    return {"status": evidence.status, "shape": evidence.shape,
            "section": evidence.section,
            "span": [evidence.start, evidence.end], "sha": evidence.sha}


def status_counts(evidence):
    """How many occurrences of each status, in a fixed order."""
    counts = {}
    for item in evidence:
        counts[item.status] = counts.get(item.status, 0) + 1
    return {name: counts[name] for name in STATUSES if name in counts}


def display_of(canonical):
    """A covering concept's human name, for the explanation."""
    return skill_concepts.display_name(canonical)


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
    depth, entries = strength(evidence)
    return {
        "id": concept.id,
        "display": concept.display,
        "tier": name,
        "why": why,
        "weight": weight(name, separation),
        "market_separation": separation,
        "evidence": [e.as_dict() for e in evidence],
        "occurrences": [occurrence(e) for e in evidence],
        "status_counts": status_counts(evidence),
        "evidence_strength": depth,
        "independent_entries": entries,
        "sections": sorted({e.section for e in evidence if e.claimed}),
        "planned_only": bool(evidence) and all(e.planned for e in evidence),
    }


def assess(concept, text, spans=None, separation=None):
    """Everything this module knows about one concept, as plain data."""
    evidence = find(concept, text, spans)
    name, why = tier(evidence)
    depth, entries = strength(evidence)
    return {
        "id": concept.id,
        "display": concept.display,
        "tier": name,
        "why": why,
        "weight": weight(name, separation),
        "market_separation": separation,
        "evidence": [e.as_dict() for e in evidence],
        "occurrences": [occurrence(e) for e in evidence],
        "status_counts": status_counts(evidence),
        "evidence_strength": depth,
        "independent_entries": entries,
        "sections": sorted({e.section for e in evidence if e.claimed}),
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
        "Harbourline Systems Pvt Ltd\n"
        "Software Engineer January 2025 - Present\n"
        "- Build REST integrations between a React Native app and Salesforce.\n"
        "- Author Apex unit tests at 85% coverage.\n"
        "Projects\n"
        "LendCircle | React Native, Node.js, Redis, Socket.IO, Cloudinary\n"
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
    assert tier_of("harbourline") in (CORE, SUPPORTING)

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
