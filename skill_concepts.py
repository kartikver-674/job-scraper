"""One real concept, one scoring contribution.

THE PROBLEM THIS SOLVES
-----------------------
scraper.score_job walks SCORING["skill_weights"] and adds a point for every
TERM whose pattern matches. Terms are strings, not concepts, so a profile
carrying three spellings of one technology scored it three times:

    "Firebase FCM"  ->  firebase fcm (3) + fcm (5) + firebase (4)  = 12
    "TypeScript"    ->  typescript (2)                             =  2

Twelve points for a push-notification library against two for the language
the candidate writes every day. Nothing about the candidate changed between
those two numbers — only how many ways the extractor happened to spell one
of them.

The same split runs the other way. Terms are matched literally, so the
spelling the résumé happened to use is the only one that matches:

    résumé said "JavaScript (ES6+)"  ->  a job saying "JavaScript"  scored 0
    PDF split "LWC" into "L WC"      ->  a job saying "LWC"         scored 0

THE MODEL
---------
A Concept is one technology with one weight and many matchers:

    id        stable lowercase identity, e.g. "javascript"
    display   what a human reads, e.g. "JavaScript"
    aliases   every spelling that MATCHES it, e.g. js, es6, javascript (es6+)
    raw       the extracted strings that produced it, kept for provenance

Aliases are OR-matchers for one contribution. Related concepts stay
separate, because they are separate questions to ask of a job:

    react native  is not  react          a React job is not a mobile job
    socket.io     is not  websockets     a library is not the protocol
    firebase      is not  fcm            a platform is not one service
    salesforce    is not  apex           a platform is not its language
    azure devops  is not  azure          a CI tool is not a cloud

Keeping them separate would re-introduce double counting by the back door —
"React Native" contains "react" — so OVERLAP is settled by span
containment rather than by a table: a concept scores only if it has at
least one match that is not wholly inside another matched concept's span.
"React Native developer" scores React Native alone; "React and React
Native" scores both, because there the job really did ask for both.

WHAT THIS IS NOT
----------------
Not a taxonomy. The alias table is small, explicit and hand-checked, and
anything not in it passes straight through as its own concept — an unknown
tool keeps its name, its weight and its matcher. A résumé naming a library
released last week is scored exactly as it is written. That is deliberate:
the audit's §10 warning is that importing an ontology would gate real
modern skills out, and the failures measured there are all spelling, not
missing world knowledge.

Weights are NOT touched here. A concept takes the highest weight among the
spellings that produced it, so the strongest signal the profile already
carried is the one that survives. Deciding what a skill is WORTH is
evidence-aware importance, which is a later step.

    python skill_concepts.py        # self-check, offline
"""

import os
import re

# --------------------------------------------------------------------------
# The alias table
# --------------------------------------------------------------------------
#
# Read as {canonical id: (display, *aliases)}. Every entry is a case where
# two strings are THE SAME TECHNOLOGY — never "related", never "usually
# implies". The distinction is the whole design: merging is permanent and
# invisible, so the bar is sameness, not similarity.
#
# Lookup is by folded key (see _key), which removes spaces and punctuation.
# That is what makes the PDF-spacing failures disappear for free: "L WC",
# "lwc" and "L.W.C." all fold to "lwc", and the table needs one entry, not
# one per way a PDF extractor can break a word.

CONCEPTS = {
    # --- languages ---------------------------------------------------------
    "javascript": ("JavaScript", "js", "ecmascript", "es6", "es6+", "es2015",
                   "javascript es6", "vanilla javascript"),
    "typescript": ("TypeScript", "ts"),
    "python": ("Python", "python3"),
    "java": ("Java",),
    "c++": ("C++", "cpp"),
    "c#": ("C#", "csharp"),
    # NOT "go". An alias becomes a MATCHER for every profile that names the
    # concept, so a short word that is also ordinary English injects false
    # matches into profiles that never carried it: "go" fires twice in "we
    # go to market fast… you will go far", and "rn" fires on the nursing
    # corpus's "RN team". Measured before adding, not assumed. Anything
    # left out here still works as an unknown concept in its own right.
    "golang": ("Go", "golang", "go lang"),

    # --- runtimes and frameworks -------------------------------------------
    # "node" folds to the same key as "node.js", which is the point: a
    # profile carrying both spellings scored twice for one runtime.
    # ponytail: a bare "node" in prose is not always Node.js (graph nodes,
    # Kubernetes nodes). Treating it as the runtime is what stops the
    # double count, and is right in the overwhelming majority of software
    # listings — revisit with context if a real mis-hit shows up.
    "node.js": ("Node.js", "node", "nodejs", "node js"),
    "react": ("React", "react.js", "reactjs", "react js"),
    # NOT an alias of react. A React job is not a mobile job, and the
    # candidate whose whole career is React Native must not have it folded
    # into the far commoner web framework.
    # "rn" is deliberately absent: see the note on Go above — it is how
    # every nursing listing writes Registered Nurse.
    "react native": ("React Native", "reactnative", "react-native"),
    "express": ("Express", "express.js", "expressjs"),
    "next.js": ("Next.js", "nextjs", "next js"),
    "vue": ("Vue", "vue.js", "vuejs"),
    "angular": ("Angular", "angular.js", "angularjs"),
    "redux toolkit": ("Redux Toolkit", "rtk"),
    "redux": ("Redux",),
    "tailwind css": ("Tailwind CSS", "tailwind", "tailwindcss"),
    "react hook form": ("React Hook Form", "reacthookform"),

    # --- data ---------------------------------------------------------------
    "mongodb": ("MongoDB", "mongo"),
    "postgresql": ("PostgreSQL", "postgres", "psql", "post gres"),
    "mysql": ("MySQL", "my sql"),
    "redis": ("Redis",),
    "mongoose": ("Mongoose",),

    # --- web platform --------------------------------------------------------
    "html": ("HTML", "html5"),
    "css": ("CSS", "css3"),

    # --- Salesforce ----------------------------------------------------------
    # Three concepts, not one. Apex is a language and LWC is a UI framework;
    # a job wanting Apex is a different job from one wanting the CRM, and
    # folding them would make every Salesforce admin posting look like a
    # match for a Salesforce developer.
    "salesforce": ("Salesforce", "salesforce crm", "sfdc", "force.com"),
    "apex": ("Apex",),
    "lightning web components": ("Lightning Web Components", "lwc", "l wc"),
    "soql": ("SOQL",),
    "crm": ("CRM",),

    # --- APIs and auth --------------------------------------------------------
    "rest api": ("REST APIs", "rest", "rest apis", "restful", "restful api",
                 "restful apis", "rest api design", "rest apis design"),
    # Designing APIs is a skill in its own right and is asked for on its
    # own. Span containment stops it scoring twice inside "REST API design".
    "api design": ("API Design",),
    "graphql": ("GraphQL",),
    "oauth": ("OAuth", "oauth2", "oauth 2.0", "oauth2.0"),
    "jwt": ("JWT", "json web token", "json web tokens"),
    "websockets": ("WebSockets", "websocket", "web sockets"),
    # The library, not the protocol. Socket.IO runs over WebSockets and can
    # fall back to polling; a job asking for one is not asking for the other.
    "socket.io": ("Socket.IO", "socketio", "socket io"),

    # --- cloud and platforms ---------------------------------------------------
    "firebase": ("Firebase",),
    # A single Firebase service. Not an alias of Firebase: a job wanting
    # push notifications is not a job wanting the platform, and this exact
    # pair is what produced the 12-point FCM match.
    "firebase cloud messaging": ("Firebase Cloud Messaging", "fcm",
                                 "firebase fcm", "firebase messaging"),
    "aws": ("AWS", "amazon web services"),
    "gcp": ("GCP", "google cloud", "google cloud platform"),
    "azure": ("Azure", "microsoft azure"),
    # A CI/CD product that happens to carry the Azure name. Folding this
    # into Azure would turn "we use Azure DevOps for pipelines" into cloud
    # engineering experience the candidate never claimed.
    "azure devops": ("Azure DevOps", "azuredevops", "vsts"),
    "docker": ("Docker",),
    "kubernetes": ("Kubernetes", "k8s"),

    # --- practice --------------------------------------------------------------
    "ci/cd": ("CI/CD", "cicd", "ci cd", "continuous integration"),
    "agile": ("Agile",),
    "scrum": ("Scrum",),
    "jest": ("Jest",),
    "git": ("Git",),
    "github": ("GitHub",),
    "jira": ("Jira",),
    "postman": ("Postman",),
    "expo": ("Expo",),
    "vite": ("Vite",),
    "zod": ("Zod",),
    "cloudinary": ("Cloudinary",),
    "tensorflow": ("TensorFlow",),
    "opencv": ("OpenCV",),
}


# --------------------------------------------------------------------------
# Which concept a piece of text belongs to
# --------------------------------------------------------------------------
#
# "React Native" contains the word "react", and a React Native job is not
# a React job. Suppressing the shorter concept was already the intent, but
# it was done by comparing spans among the concepts THIS CANDIDATE
# happened to have: a React-only profile scored on "react native
# developer", and adding React Native to the profile changed React's
# answer. Whether a concept may claim a span became a fact about the
# extractor rather than about the text.
#
# So the relation is declared here, and it is consulted whether or not the
# longer concept was extracted. Deliberately NOT a universal "suppress any
# shorter string inside a longer one": "Java" inside "JavaScript" is a
# different kind of accident, and legitimate names contain other names
# without being parent and child. Each pair below is a judgement someone
# can disagree with in review.
#
# Read as {longer concept: (concepts its text must not feed, ...)}.
COVERS = {
    "react native": ("react",),
    "firebase cloud messaging": ("firebase",),
    "azure devops": ("azure",),
}


def covered_by(canonical):
    """Concepts whose occurrences must not be read as this one's."""
    return tuple(longer for longer, shorter in COVERS.items()
                 if canonical in shorter)


def _key(term):
    """Folded lookup key: lowercase, letters and digits only.

    Punctuation and spacing are exactly the noise that split one concept
    into several — "node.js"/"node js", "L WC"/"LWC", "socket.io"/"socket
    io". Folding them away means the table holds a concept once rather
    than once per way a PDF can break it.

    + and # survive, because they are the whole name in "c++" and "c#".
    """
    return re.sub(r"[^a-z0-9+#]+", "", str(term or "").lower())


def _build():
    """{folded alias: canonical id} and {canonical id: display}."""
    lookup, display = {}, {}
    for canonical, entry in CONCEPTS.items():
        display[canonical] = entry[0]
        for alias in (canonical,) + tuple(entry[1:]):
            folded = _key(alias)
            if folded and folded not in lookup:
                lookup[folded] = canonical
    return lookup, display


LOOKUP, DISPLAY = _build()

# Trailing noise a résumé hangs off a real name: "JavaScript (ES6+)",
# "Redis (caching)", "Python 3". Stripped only when what remains is a
# concept this table knows, so an unknown product keeps its whole name.
_PARENTHETICAL = re.compile(r"\s*\(([^)]*)\)\s*$")


def resolve(term):
    """The canonical id for one extracted string.

    Unknown passthrough is the important half: a tool no table has heard
    of resolves to ITSELF, keeps its spelling and is scored normally.
    Gating on a dictionary would delete real skills, which is the failure
    mode the audit warns about in §10.
    """
    raw = str(term or "").strip().lower()
    if not raw:
        return ""
    folded = _key(raw)
    if folded in LOOKUP:
        return LOOKUP[folded]
    # "javascript (es6+)" -> "javascript". Only when the head is known: an
    # unknown "Acme Runtime (fast)" stays whole rather than being guessed at.
    stripped = _PARENTHETICAL.sub("", raw).strip()
    if stripped != raw:
        head = _key(stripped)
        if head in LOOKUP:
            return LOOKUP[head]
    return re.sub(r"\s+", " ", raw)


def display_name(canonical):
    """What a human reads for this concept."""
    return DISPLAY.get(canonical, canonical)


# --------------------------------------------------------------------------
# What the MARKET is asked about a concept
# --------------------------------------------------------------------------
#
# The market signal has to be a property of the CONCEPT, not of the
# spellings one candidate's extractor happened to emit. It was
# max(separation) over the concept's id AND its observed raw strings, so
# the same person with the same résumé measured JavaScript at band 1 with
# one spelling, 2 with two, and 4 with six — because "es6" is rare and
# "javascript" is not. Rarity of an alias is not rarity of the concept.
#
# The policy: ask the corpus about the concept's DECLARED keys, in the
# order the table declares them, and take the FIRST one the corpus can
# actually measure. Not the rarest, not the strongest, not whichever
# arrived first from the model.
#
# The declared keys are the canonical id followed by the table's own
# aliases. That order is fixed in this file and identical for every
# candidate. A concept whose id is not the name the corpus counts can
# name its own preferred key here instead.
MARKET_KEYS = {
    # (none needed yet — every canonical id above is either measured by
    # the shipped table or falls through to an alias that is. Entries go
    # here when a corpus counts a concept under a name that is not its
    # canonical id.)
}


def market_keys(canonical):
    """The terms to ask the corpus about, in a fixed, candidate-independent
    order."""
    declared = MARKET_KEYS.get(canonical)
    if declared:
        return tuple(declared)
    entry = CONCEPTS.get(canonical)
    if not entry:
        # Unknown concept: it answers to its own name and nothing else.
        # Guessing a neighbour's frequency for it would be worse than
        # abstaining.
        return (canonical,)
    seen, keys = set(), []
    for key in (canonical,) + tuple(entry[1:]):
        low = str(key).strip().lower()
        if low and low not in seen:
            seen.add(low)
            keys.append(low)
    return tuple(keys)


def market_signal_for(canonical, freqs, corpus_signal):
    """What the market says about this concept, and how it was asked.

    Returns the observation rather than a bare number, so an audit can ask
    later WHY a concept was weighted as it was — which key answered, on
    what denominator, and which keys were considered and could not be
    measured. Abstaining (separation None) is deliberate and preferred
    over borrowing an alias's rarity.
    """
    considered = market_keys(canonical)
    for key in considered:
        separation = corpus_signal.separation(key, freqs)
        if separation is not None:
            hits, seen = (freqs or {}).get(key, (0, 0))
            return {"id": canonical, "key": key, "separation": separation,
                    "hits": hits, "seen": seen, "considered": considered}
    return {"id": canonical, "key": None, "separation": None,
            "hits": None, "seen": None, "considered": considered}


def aliases_for(canonical):
    """Every spelling that should MATCH this concept."""
    entry = CONCEPTS.get(canonical)
    if not entry:
        return (canonical,)
    return (canonical,) + tuple(entry[1:])


# --------------------------------------------------------------------------
# Atomic extraction: one string, one concept
# --------------------------------------------------------------------------
#
# The extractor is asked for skills "as written", so it returns what the
# page says — "JWT / OAuth 2.0", "React Hook Form + Zod", "Agile/Scrum".
# Each became ONE literal matcher, which matches a job saying exactly that
# and nothing else, so both halves were invisible.
#
# Splitting is deliberately timid. A product name containing punctuation is
# far more common than a compound, so the whole string is checked FIRST and
# a known concept is never split: ci/cd, node.js, socket.io and c++ all
# survive intact.

_SEPARATORS = re.compile(r"\s*[/+&,]\s*|\s+and\s+")

# Splitting on "." would destroy more names than it fixes (node.js,
# socket.io, next.js, express.js, asp.net), so it is not a separator at all.
MIN_PART = 2


def split_compound(raw):
    """One extracted string as the atomic concepts it actually names.

    Returns a list, the original string when there is nothing safe to do.
    """
    term = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    if not term:
        return []
    # A name this table knows is never a compound, however much punctuation
    # it carries. This is what protects "ci/cd" and "c++".
    if _key(term) in LOOKUP:
        return [term]

    # "Socket.IO (WebSockets)" names two things; "AcmeTool (Enterprise)"
    # names one, and the parenthetical is part of the product's name. Only
    # split when BOTH halves are concepts this table knows — otherwise the
    # whole string survives, parentheses and all. Dropping the
    # parenthetical was losing half an unknown product's identity.
    match = _PARENTHETICAL.search(term)
    if match:
        head, inner = _PARENTHETICAL.sub("", term).strip(), match.group(1).strip()
        if head and inner and _key(inner) in LOOKUP and _key(head) in LOOKUP:
            return [head, inner]
        return [term]

    parts = [p.strip() for p in _SEPARATORS.split(term) if p and p.strip()]
    if len(parts) < 2:
        return [term]
    # Any fragment too short to be a name means the separator was part of
    # one: "c++" splits to ["c", "", ""], "node.js" is already protected
    # above. Abort rather than invent two skills out of one.
    if any(len(p) < MIN_PART for p in parts):
        return [term]
    # POSITIVE EVIDENCE, not the presence of a separator. "JWT / OAuth
    # 2.0" is two concepts because both halves are concepts; "Foo/Bar" and
    # "Research & Development" are one unknown string each, and splitting
    # them invented two skills nobody claimed.
    #
    # A mixed compound — one known half, one unknown — is kept whole on
    # purpose. Splitting it would mint a concept out of the unknown half,
    # and keeping it whole loses nothing the scanner cannot recover on its
    # own from the résumé text.
    if not all(_key(p) in LOOKUP for p in parts):
        return [term]
    return parts


def atomize(terms):
    """Every extracted string, split where it is safe to split."""
    out = []
    for term in terms or ():
        for part in split_compound(term):
            if part and part not in out:
                out.append(part)
    return out


def identities(terms):
    """Raw extracted strings as the atomic canonical concepts they name.

    V3 STEP 2, and deliberately the ONLY place that answers "what concepts
    is this person claiming". Search used to match on whatever spellings
    extraction happened to emit while the profile resolved the same strings
    to canonical ids one stage later, so the two layers disagreed about
    identity — `agile/scrum` was one unknown string to the corpus and two
    known concepts to the profile. This is the single transformation both
    now mean.

    It is exactly `atomize` then `resolve`, in that order, because the
    order matters: resolving first would hand `split_compound` a string
    the LOOKUP table has already rewritten, and its whole protection is
    that it refuses to split anything the table recognises.

    Pure. No résumé text, no evidence, no market, no weights, no roles —
    those all stay where they are. Order-preserving, and deduplicated on
    the CANONICAL id, so `javascript (es6+)` contributes JavaScript once
    rather than twice.
    """
    out, seen = [], set()
    for part in atomize(terms):
        concept = resolve(part)
        if concept and concept not in seen:
            seen.add(concept)
            out.append(concept)
    return out


# --------------------------------------------------------------------------
# Concepts, from a flat profile
# --------------------------------------------------------------------------

class Concept:
    """One technology: one weight, one contribution, many matchers."""

    __slots__ = ("id", "display", "weight", "aliases", "raw", "patterns")

    def __init__(self, canonical, weight, raw, extra_aliases=()):
        self.id = canonical
        self.display = display_name(canonical)
        self.weight = weight
        self.raw = tuple(raw)
        # The table's spellings UNION the ones this profile happened to
        # carry. The union is what fixes the other half of the bug: a
        # résumé that said "JavaScript (ES6+)" now also matches a job that
        # says plain "JavaScript", because the concept brought its own
        # aliases with it.
        # Deduped by the LITERAL string, not the folded key. Folding is for
        # lookup — deciding that "l wc" and "lwc" name one concept — but
        # they are different MATCHERS, and dropping either loses the text
        # that only spells it that way. The audited résumé says "L WC";
        # keeping only "lwc" found nothing.
        seen, aliases = set(), []
        for alias in tuple(aliases_for(canonical)) + tuple(extra_aliases):
            literal = re.sub(r"\s+", " ", str(alias).strip().lower())
            if literal and literal not in seen:
                seen.add(literal)
                aliases.append(literal)
        self.aliases = tuple(aliases)
        self.patterns = tuple(compile_alias(a) for a in self.aliases)

    def __repr__(self):
        return f"Concept({self.id!r}, weight={self.weight})"

    def spans(self, text):
        """Every (start, end) this concept matches in the text."""
        found = []
        for pattern in self.patterns:
            found.extend(m.span() for m in pattern.finditer(text))
        return found


def compile_alias(term):
    """scraper._compile's matcher, kept identical on purpose.

    Alphanumeric lookarounds rather than \\b, so ".net", "node.js",
    "socket.io", "c#" match cleanly and "lead" does not fire inside
    "leadership". The scorer below must agree with the one it replaces
    about what a match IS; only about how many times it counts.
    """
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower())
                      + r"(?![a-z0-9])")


def from_weights(weights):
    """[Concept] from a profile's flat {term: weight} dict.

    Terms that name one concept collapse into one entry. The weight is the
    HIGHEST among them: this step changes how many times a concept counts,
    never what it is worth, so the strongest signal the profile already
    carried is the one that survives.
    """
    grouped = {}
    for term, weight in (weights or {}).items():
        canonical = resolve(term)
        if not canonical:
            continue
        entry = grouped.setdefault(canonical, {"weight": weight, "raw": []})
        entry["weight"] = max(entry["weight"], weight)
        if term not in entry["raw"]:
            entry["raw"].append(term)
    return [Concept(canonical, e["weight"], e["raw"], extra_aliases=e["raw"])
            for canonical, e in grouped.items()]


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def _inside(span, others):
    """Is this span wholly contained in one of the others, and smaller?"""
    start, end = span
    return any(o_start <= start and end <= o_end
               and (o_end - o_start) > (end - start)
               for o_start, o_end in others)


def covering_spans(canonical, text):
    """Spans in this text that belong to a concept covering `canonical`.

    Built from the registry, so the answer does not depend on which
    concepts were extracted for this candidate — which is the whole point.
    """
    spans = []
    for longer in covered_by(canonical):
        for alias in aliases_for(longer):
            spans.extend(m.span() for m in compile_alias(alias).finditer(text))
    return spans


def own_spans(concept, text):
    """This concept's occurrences, minus the ones that belong to a concept
    that covers it."""
    covering = covering_spans(concept.id, text)
    return [span for span in concept.spans(text)
            if not _inside(span, covering)]


def score(text, concepts):
    """(points, [display names]) — each concept counted at most once.

    THE OVERLAP POLICY, which is the part worth arguing about:

    A concept contributes only if it has at least one match that is not
    wholly inside a LONGER match of some other concept. So

        "React Native developer"        React Native only
        "React and React Native"        both — the job asked for both
        "Firebase FCM"                  FCM only
        "Firebase and FCM"              both
        "REST API design"               REST APIs only
        "Salesforce CRM"                Salesforce only (crm is inside it)

    This replaces an accident with a rule. Before, every one of those
    scored every overlapping term, so the longer and more specific the
    phrase in the job ad, the more points it silently collected.
    """
    text = (text or "").lower()
    # own_spans, not spans: a concept the registry says is covered here —
    # "react" inside "react native" — never had a claim on this text,
    # whether or not React Native is in this candidate's set.
    hits = [(c, own_spans(c, text)) for c in concepts]
    hits = [(c, s) for c, s in hits if s]
    every = [span for _c, spans in hits for span in spans]

    points, matched = 0, []
    for concept, spans in hits:
        if all(_inside(span, every) for span in spans):
            continue
        points += concept.weight
        matched.append(concept.display)
    return points, matched


# --------------------------------------------------------------------------
# What gets recorded
# --------------------------------------------------------------------------

def canonical_matched(recorded):
    """An existing row's matched_skills, as canonical display names.

    The corpus on disk was written term by term, so one listing can carry
    "firebase fcm, fcm, firebase" — the same duplication, frozen. This
    translates a stored row forward WITHOUT rewriting the file: reading is
    left alone in this step because collapsing terms changes the per-term
    denominators corpus_signal measures, and that would move weights.
    """
    if isinstance(recorded, str):
        terms = [t.strip() for t in recorded.split(",")]
    else:
        terms = [str(t).strip() for t in recorded or ()]
    out = []
    for term in terms:
        if not term:
            continue
        name = display_name(resolve(term))
        if name not in out:
            out.append(name)
    return out


# --------------------------------------------------------------------------
# The seam
# --------------------------------------------------------------------------

FLAG = "SWEEP_SKILL_CONCEPTS"

# Step 3's seam, and deliberately a separate switch. The two change
# different things and are worth comparing apart: CONCEPTS changes how
# many times a match COUNTS, EVIDENCE changes what a skill is WORTH.
EVIDENCE_FLAG = "SWEEP_SKILL_EVIDENCE"


ROLES_FLAG = "SWEEP_ROLE_FAMILIES"

# --------------------------------------------------------------------------
# The production switch
# --------------------------------------------------------------------------
#
# One name decides which engine reads a résumé, so a rollback is one
# environment variable rather than three:
#
#     v1   the engine that has always run. Every alias scored on its own,
#          importance from the corpus alone.
#     v2   canonical concepts (step 2) and evidence-aware importance
#          (step 3), which the evaluation measured and recommended.
#
# v2 DOES NOT INCLUDE ROLE FAMILIES. The evaluation measured step 4
# removing the job search entirely for 2 of 16 personas — a graduate with
# no employment and a QA specialist the corpus under-covers — and
# recommended holding it. It stays behind SWEEP_ROLE_FAMILIES, off, and
# is not reachable through this switch. docs/profile-engine-v2-evaluation.md
# carries the numbers.
#
# THE CODE DEFAULT IS v1 — see DEFAULT_VERSION below. An earlier revision
# of this comment said the default was v2, which was true of the
# experimental branch and was the exact defect the independent review
# found. It is corrected here rather than left to mislead whoever reads
# this file during a rollback. Render requests v2 explicitly in
# render.yaml; absence of the variable still means v1.
VERSION_ENV = "SWEEP_PROFILE_ENGINE_VERSION"
VERSIONS = ("v1", "v2")

# v1. The independent review found the branch defaulting to v2, which
# meant any process without the variable — a worker, a CLI run, a
# background rerank — silently ran an engine the evaluation called
# experimental. Absence now means the engine that has always run.
DEFAULT_VERSION = "v1"

# The engine a LOADED PROFILE was derived with, when one is loaded.
#
# Two machines agreeing by coincidence is not a contract. Render pins the
# derivation engine; the Oracle worker's scraper child reads its own
# environment, so a profile derived as v1 could be scored as v2 and
# nothing would say so. A profile now carries the engine that wrote it,
# config.py reads that stamp, and it beats whatever the scoring machine
# happens to have exported.
_BOUND = None


def bind(version):
    """Pin the engine to the one a loaded profile was derived with.

    None releases it. Anything that is not a known version raises rather
    than being ignored: this value arrives from a file on disk, and
    profile content must never reach configuration unchecked.
    """
    global _BOUND
    if version is None:
        _BOUND = None
        return None
    if not isinstance(version, str):
        raise ValueError(
            f"engine version must be a string, got {type(version).__name__}")
    _BOUND = engine_version(version)
    return _BOUND


def engine_version(value=None):
    """Which engine reads and scores this résumé: "v1" or "v2".

    Precedence, strongest first:

      1. the engine a loaded profile was derived with     bind()
      2. an explicitly pinned version                     VERSION_ENV
      3. individual step flags, for experiments           FLAG, EVIDENCE_FLAG
      4. the default                                      v1

    2 above 3 is the rollback property: the review found that a stale
    SWEEP_SKILL_CONCEPTS in somebody's shell could silently defeat a
    documented rollback to v1. An explicit version is now the complete
    answer, and composing steps is what an unpinned environment is for —
    which is how bench/evaluate.py isolates its conditions.
    """
    if value is None and _BOUND is not None:
        return _BOUND
    name = value if value is not None else os.environ.get(VERSION_ENV)
    if not name:
        return None if value is not None else _from_flags()
    name = str(name).strip().lower()
    if name not in VERSIONS:
        raise ValueError(
            f"{VERSION_ENV}={name!r} is not an engine version — expected one "
            f"of {', '.join(VERSIONS)}")
    return name


# A composition that is neither engine. Reported, never bound, and
# refused by the profile loader — an experiment's output must not be
# runnable as though it were one of the two supported engines.
MIXED = "mixed"


def _from_flags():
    """The version implied when nothing is pinned.

    v2 means BOTH steps. Concepts without evidence is a real and useful
    experiment, and calling it v2 would stamp a profile v2 and later bind
    evidence that was never used to derive it — precisely the silent
    disagreement this contract exists to stop.
    """
    concepts, evidence = _on(FLAG), _on(EVIDENCE_FLAG)
    if concepts and evidence:
        return "v2"
    if not concepts and not evidence:
        return DEFAULT_VERSION
    return MIXED


def _pinned():
    """Is a version explicitly chosen, rather than implied?"""
    return _BOUND is not None or bool(os.environ.get(VERSION_ENV, "").strip())


def _on(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def enabled():
    """Is concept scoring on?

    Read per call, so a rollback takes effect on the next request rather
    than the next deploy.
    """
    if _pinned():
        return engine_version() == "v2"
    return _on(FLAG) or DEFAULT_VERSION == "v2"


def effective():
    """The complete configuration in force, for logs and evaluation records.

    Archived comparisons are only auditable if they say what was actually
    running, which the review found the evaluator did not.
    """
    if _BOUND is not None:
        source = "the profile it was derived with"
    elif os.environ.get(VERSION_ENV, "").strip():
        source = VERSION_ENV
    elif _on(FLAG) or _on(EVIDENCE_FLAG) or _on(ROLES_FLAG):
        source = "step flags"
    else:
        source = "default"
    return {"version": engine_version(), "concepts": enabled(),
            "evidence": evidence_enabled(), "roles": roles_enabled(),
            "source": source}


def evidence_enabled():
    """Is evidence-aware importance on? Off unless asked.

    Implies concept grouping: tiers are assigned per CONCEPT, so asking
    for evidence without canonicalisation would tier "node" and "node.js"
    as two careers. One switch turning on the thing it depends on beats a
    second switch someone can forget.
    """
    if _pinned():
        return engine_version() == "v2"
    return _on(EVIDENCE_FLAG) or DEFAULT_VERSION == "v2"


def roles_enabled():
    """Is evidence-grounded role construction on? Off unless asked.

    Deliberately NOT part of v2, and not reachable through the version
    switch: the evaluation measured it removing the job search entirely
    for 2 of 16 personas, and the independent review agreed it should stay
    held. Only the explicit step flag turns it on, and a bound profile can
    never turn it on at all — the reason for holding it is a coverage
    regression, not a configuration question.

    IN THIS RELEASE IT IS ALWAYS FALSE. role_families.py is not shipped
    here, so SWEEP_ROLE_FAMILIES has nothing to switch on. Returning the
    flag's value would make /healthz and effective() report roles as ON
    while no role code exists — the worst of both, a flag that lies. The
    name and the flag stay so the contract test keeps asserting the
    answer, and so re-introducing the implementation is one line.
    """
    return False


def demo():
    """Self-check. `python skill_concepts.py` — offline, no model."""
    # --- aliases resolve to one concept ------------------------------------
    for spelling in ("JavaScript", "js", "JS", "JavaScript (ES6+)", "ES6+",
                     "ecmascript"):
        assert resolve(spelling) == "javascript", spelling
    for spelling in ("TypeScript", "ts", "TS"):
        assert resolve(spelling) == "typescript", spelling
    for spelling in ("Node.js", "node", "NodeJS", "node js"):
        assert resolve(spelling) == "node.js", spelling
    for spelling in ("PostgreSQL", "postgres", "Postgres"):
        assert resolve(spelling) == "postgresql", spelling
    # The PDF-spacing failure, which the folded key fixes without a rule
    # per spelling.
    for spelling in ("LWC", "lwc", "L WC", "l wc", "Lightning Web Components"):
        assert resolve(spelling) == "lightning web components", spelling

    # --- related is not the same -------------------------------------------
    assert resolve("react") != resolve("react native")
    assert resolve("socket.io") != resolve("websockets")
    assert resolve("firebase") != resolve("fcm")
    assert resolve("salesforce") != resolve("apex")
    assert resolve("salesforce") != resolve("lwc")
    assert resolve("azure devops") != resolve("azure")

    # --- unknown passthrough -------------------------------------------------
    assert resolve("Bun") == "bun"
    assert resolve("some brand new runtime") == "some brand new runtime"
    assert display_name(resolve("Bun")) == "bun"

    # --- compounds split, product names do not -------------------------------
    assert split_compound("JWT / OAuth 2.0") == ["jwt", "oauth 2.0"]
    assert split_compound("React Hook Form + Zod") == ["react hook form", "zod"]
    assert split_compound("Agile/Scrum") == ["agile", "scrum"]
    # CONTRACT CHANGE: neither half is a concept this table knows, so the
    # string survives whole rather than minting two skills. The scanner
    # can still recover "authentication" on its own from the market
    # vocabulary if the document supports it.
    assert split_compound("Authentication & Security") == [
        "authentication & security"]
    # Positive evidence, not the presence of a separator.
    assert split_compound("Foo/Bar") == ["foo/bar"]
    assert split_compound("AcmeTool (Enterprise)") == ["acmetool (enterprise)"]
    assert split_compound("Socket.IO (WebSockets)") == ["socket.io", "websockets"]
    assert split_compound("CI/CD") == ["ci/cd"], split_compound("CI/CD")
    assert split_compound("c++") == ["c++"], split_compound("c++")
    assert split_compound("node.js") == ["node.js"]
    # Kept whole; resolve() still maps it to Redis through the
    # parenthetical fallback, so the identity is not lost.
    assert split_compound("Redis (caching)") == ["redis (caching)"]
    assert resolve("Redis (caching)") == "redis"

    # --- one concept, one contribution ---------------------------------------
    concepts = from_weights({"firebase fcm": 3, "fcm": 5, "firebase": 4,
                             "typescript": 2})
    points, matched = score("We use Firebase FCM for push.", concepts)
    assert points == 5, (points, matched)
    assert matched == ["Firebase Cloud Messaging"], matched
    # ...and the platform still scores when the job really names it too.
    points, matched = score("Firebase and FCM both.", concepts)
    assert points == 9 and sorted(matched) == ["Firebase",
                                               "Firebase Cloud Messaging"]

    # --- adding a spelling cannot add points ---------------------------------
    one = from_weights({"node.js": 2})
    many = from_weights({"node.js": 2, "node": 2, "nodejs": 2, "node js": 2})
    text = "Node.js and Node experience required."
    assert score(text, one)[0] == score(text, many)[0] == 2

    # --- the spelling the résumé used is not the only one that matches -------
    js = from_weights({"javascript (es6+)": 3})
    assert score("Strong JavaScript required.", js)[0] == 3
    lwc = from_weights({"l wc": 3})
    assert score("LWC and Apex.", lwc)[0] == 3

    # --- parent/child follows the rule, not the regex ------------------------
    rn = from_weights({"react": 2, "react native": 3})
    assert score("React Native developer", rn) == (3, ["React Native"])
    assert score("React developer", rn) == (2, ["React"])
    both = score("React and React Native", rn)
    assert both[0] == 5 and sorted(both[1]) == ["React", "React Native"]

    # --- distinct concepts both count when both are named --------------------
    sio = from_weights({"socket.io": 5, "websockets": 3})
    assert score("Socket.IO over WebSockets", sio)[0] == 8
    assert score("Socket.IO only", sio)[0] == 5

    # --- REST's five entries become one --------------------------------------
    rest = from_weights({"rest": 2, "rest api": 4, "rest apis": 3,
                         "rest api design": 3, "api design": 4})
    points, matched = score("REST API design experience", rest)
    assert points == 4 and matched == ["REST APIs"], (points, matched)

    # --- recording ------------------------------------------------------------
    assert canonical_matched("firebase fcm, fcm, firebase") == [
        "Firebase Cloud Messaging", "Firebase"]
    assert canonical_matched(["js", "javascript"]) == ["JavaScript"]

    # --- determinism ----------------------------------------------------------
    weights = {"js": 1, "javascript": 2, "javascript (es6+)": 3, "es6": 1}
    first = [(c.id, c.weight, c.aliases) for c in from_weights(weights)]
    for _ in range(5):
        assert [(c.id, c.weight, c.aliases)
                for c in from_weights(dict(weights))] == first

    print("skill_concepts demo ok")


if __name__ == "__main__":
    demo()
