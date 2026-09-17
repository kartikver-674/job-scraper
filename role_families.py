"""Which careers to search for, proposed by the résumé and checked by the market.

THE FAILURE THIS REPLACES
-------------------------
Search fields were built from whatever the corpus correlated with, and the
corpus is a record of previous searches. Three of the eight paid queries
derived for the audited résumé had nothing to do with the candidate:

    software engineer -python developer   anchored on GIT
    applied ai backend engineer           the fragment was "i backend"
    sde ii, amazon now                    five rows, one company

The first is the shape of the whole problem. Git appeared in 22 of 27 rows
at one employer, that correlation cleared the lift bar, and a version
control system every engineer on earth uses became the evidence for a
Python career the candidate does not have. Nothing in the résumé proposed
it; the market invented it.

The second is the same failure one step later. A fragment with real
support ("backend") was canonicalised into a concrete title carrying a
domain the candidate has no evidence for ("applied ai"), and the guards
that approved the fragment were never re-run on the title that replaced it.

THE ORDER THIS ENFORCES
-----------------------
    1. the RÉSUMÉ proposes career directions      (role families)
    2. the MARKET offers concrete titles for them (availability)
    3. the RÉSUMÉ checks the title it got back    (revalidation)

Step 3 is the one that was missing. Market data is allowed to tell us how
a job is advertised and whether anyone is hiring for it. It is not allowed
to tell us what the candidate does for a living.

WHAT MAY ANCHOR A CAREER
------------------------
A family needs evidence a person would recognise as their own work:

  - a title they actually held
  - a CORE or STRONG_SECONDARY concept (skill_evidence's tiers)
  - never a workflow tool, whatever its tier

The last rule is separate on purpose. Tiers already stop Git here — it is
a skills-list claim, so SUPPORTING — but a résumé whose work bullets talk
about managing CI/CD pipelines would make CI/CD core, and "how you work"
still would not be "what you are". Git, Jira, Agile and their neighbours
describe the process around any stack and belong to no career.

INTENT IS NOT EXPERIENCE
------------------------
Evidence proves what someone HAS done, never what they want next. This
candidate's Salesforce work is professional and undeniable, and that is
still not proof they want a Salesforce-only role. So families carry a
`primary` flag rather than a rank: the ones the person's own headline and
summary name are primary, the rest are offered as lanes. The user's
explicit preference beats both, always.

    python role_families.py        # self-check, offline
"""

import re

import skill_concepts

# --------------------------------------------------------------------------
# What cannot be a career
# --------------------------------------------------------------------------
#
# Process and tooling: how work gets done, on any stack, in any field. Every
# engineer uses version control and a ticket tracker, so neither says
# anything about which jobs to search for. Deliberately small, deliberately
# not "everything common" — TypeScript is in a third of listings and is a
# perfectly good career.
#
# Jenkins, Docker and Kubernetes are NOT here: "DevOps Engineer" and
# "Platform Engineer" are real careers those genuinely anchor.

WORKFLOW = frozenset({
    "git", "github", "gitlab", "bitbucket", "svn",
    "jira", "confluence", "trello", "asana", "notion", "slack",
    "agile", "scrum", "kanban", "waterfall",
    "postman", "swagger", "insomnia",
    "ci/cd", "azure devops",
})

# Words that describe a LEVEL or a SHAPE of job rather than a field. Present
# in almost every title and carrying no domain claim, so revalidation must
# not ask the résumé to prove them.
ROLE_WORDS = frozenset({
    "engineer", "engineering", "developer", "development", "software",
    "programmer", "consultant", "specialist", "analyst", "architect",
    "manager", "lead", "senior", "junior", "associate", "principal",
    "staff", "intern", "trainee", "graduate", "entry", "level",
    "sr", "jr", "i", "ii", "iii", "iv", "1", "2", "3",
    "full", "stack", "fullstack", "front", "frontend", "back", "backend",
    "end", "web", "application", "applications", "app", "systems", "system",
    "technical", "technology", "professional", "member", "of", "and", "the",
    "remote", "hybrid", "onsite", "contract", "time", "full-time",
})

# Domains the market's SKILL vocabulary does not carry as single tokens, so
# the vocabulary check below cannot see them. Each one is a different
# career that borrows ordinary title words — the exact thing title_exclude
# exists for, and the exact thing that put "applied ai backend engineer"
# in front of a mobile developer.
DOMAIN_MARKERS = frozenset({
    "ai", "ml", "genai", "llm", "nlp", "cv", "data", "analytics",
    "devops", "sre", "reliability", "infrastructure", "platform",
    "security", "cyber", "infosec", "qa", "quality", "test", "testing",
    "embedded", "firmware", "hardware", "blockchain", "web3", "crypto",
    "game", "gaming", "unity", "unreal", "salesforce", "sap", "oracle",
    "mainframe", "erp", "network", "networking", "cloud", "mobile",
})

_WORD = re.compile(r"[a-z0-9+#.]+")


def words_of(text):
    """The comparable words in a title or a phrase.

    Dots are kept INSIDE a word, because "node.js" and "socket.io" are
    names, and stripped from the ends, because "Salesforce." at the end
    of a sentence is still Salesforce.
    """
    return [w for w in (raw.strip(".")
                        for raw in _WORD.findall(str(text or "").lower()))
            if w]


def can_anchor(concept_id, tier):
    """May this concept propose a career of its own?"""
    if concept_id in WORKFLOW:
        return False
    return tier in ("CORE", "STRONG_SECONDARY")


# --------------------------------------------------------------------------
# Families
# --------------------------------------------------------------------------

class Family:
    """One career direction the résumé supports, and what it buys.

    `titles` are concrete market titles that survived revalidation;
    `rejected` records the ones that did not and why, because a search
    that quietly dropped a career is worse than one that says so.
    """

    __slots__ = ("key", "display", "anchor", "tier", "source", "primary",
                 "titles", "rejected")

    def __init__(self, key, display, anchor, tier, source, primary):
        self.key = key
        self.display = display
        self.anchor = anchor
        self.tier = tier
        self.source = source
        self.primary = primary
        self.titles = []
        self.rejected = []

    def __repr__(self):
        return (f"Family({self.key!r}, {self.source}, "
                f"primary={self.primary}, titles={self.titles})")

    def as_dict(self):
        return {"key": self.key, "display": self.display,
                "anchor": self.anchor, "tier": self.tier,
                "source": self.source, "primary": self.primary,
                "titles": list(self.titles),
                "rejected": [{"title": t, "why": w} for t, w in self.rejected]}


def headline_of(text, lines=6):
    """The top of the résumé plus its summary — where intent is stated.

    Not a section parse: the first few lines are the name, the headline
    and the contact block on every résumé shape there is, and the summary
    is the person describing themselves rather than listing what they
    have touched. Used ONLY to mark a family primary, never to create or
    delete one.
    """
    import skill_evidence
    head = "\n".join(str(text or "").splitlines()[:lines])
    for kind, _entry, start, end in skill_evidence.sections(text):
        if kind == skill_evidence.SUMMARY:
            head += "\n" + str(text)[start:end]
    return head.lower()


def propose(held_titles, importance, headline="", preferred=()):
    """The career directions this résumé supports, before any market.

    `importance` is skill_evidence.assess_all's output. `preferred` is
    whatever the user typed, and it is authoritative: a preference
    becomes a primary family whether or not the résumé proposes it.
    """
    families, seen = [], set()

    def add(key, display, anchor, tier, source, primary):
        if key in seen:
            # A direction reached twice keeps the STRONGER claim on it.
            for existing in families:
                if existing.key == key:
                    existing.primary = existing.primary or primary
            return
        seen.add(key)
        families.append(Family(key, display, anchor, tier, source, primary))

    # 1. What the user asked for. First, and primary by definition.
    for want in preferred or ():
        text = str(want).strip().lower()
        if text:
            add(f"want:{text}", text, None, None,
                "the role you asked for", True)

    # 2. Titles they actually held. Always primary — this is the career
    #    they are in.
    for title in held_titles or ():
        text = str(title).strip().lower()
        if text:
            add(f"held:{text}", text, None, None,
                "a title from your own employment", True)

    # 3. Concepts with evidence behind them. Primary when the person's own
    #    headline or summary names them, a lane when it does not.
    head = set(words_of(headline))
    for row in importance or ():
        concept_id, tier = row.get("id"), row.get("tier")
        if not can_anchor(concept_id, tier):
            continue
        named = bool(set(words_of(concept_id)) & head)
        add(f"skill:{concept_id}", row.get("display") or concept_id,
            concept_id, tier,
            f"{'core' if tier == 'CORE' else 'project'} skill"
            + (" named in your summary" if named else ""),
            named)
    return families


# --------------------------------------------------------------------------
# Revalidation: does the title we got back still describe this person?
# --------------------------------------------------------------------------

# A title nothing much is posted under is not worth a paid search, and one
# posted by a single employer is that employer's internal vocabulary rather
# than a market. Both numbers are local_search's own, reused so the two
# passes cannot disagree about what "enough" means.
MIN_ROWS = 20
MIN_COMPANIES = 5

# Share of a title's listings that must name at least one of the
# candidate's concepts. Low on purpose: a specialist's own title routinely
# scores badly against their whole skill list, which is what the audit's
# ANCHOR_EVIDENCE note is about. This only has to exclude titles with no
# relationship at all — "sde ii, amazon now" shared nothing.
MIN_OVERLAP = 0.10

# And the share that must want TWO OR MORE of them, for a title the
# MARKET proposed rather than one the person held. One shared tool is a
# coincidence; two is a market. See revalidate() for the measurements.
MIN_PAIR_SHARE = 0.50

# local_search's own wildcard bar, reused rather than re-picked so the two
# passes cannot disagree about what "too broad" means.
MAX_SHARE = 0.25


def unsupported_domains(title, own_concepts, resume_text, vocab=()):
    """Words in this title that claim a domain the candidate cannot back.

    Two sources, because neither sees everything. The market's own skill
    vocabulary knows "python", "android" and "salesforce" are fields;
    it does not carry "ai", "ml" or "data" as single tokens, and those are
    precisely the words that turned a backend fragment into "applied ai
    backend engineer" for a mobile developer.

    Ordinary title furniture is never questioned: nobody has to prove
    "engineer".
    """
    low = str(resume_text or "").lower()
    own = set(own_concepts or ())
    bad = []
    for word in words_of(title):
        if word in ROLE_WORDS or len(word) < 2:
            continue
        claims = word in DOMAIN_MARKERS or word in (vocab or ())
        if not claims:
            continue
        if skill_concepts.resolve(word) in own or word in own:
            continue
        # The résumé may name it without the extractor having reported it.
        if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", low):
            continue
        bad.append(word)
    return bad


def revalidate(title, rows, own_concepts, resume_text, vocab=(),
               family=None, seniority=()):
    """(ok, why) for one concrete title, judged against the candidate.

    Run on the FINAL string, never on the fragment that produced it. The
    audit's failure is entirely in the gap between those two: "backend"
    earned its place and "applied ai backend engineer" inherited it.
    """
    needle = str(title).strip().lower()
    if not needle:
        return False, "empty"

    # Seniority is stripped for the reason from_resume already gives:
    # "Senior Backend Engineer" as a search STRING finds only the senior
    # rows, and the reachable ones are the others. A candidate two years
    # in must not be sent shopping in the senior aisle.
    carried = [w for w in words_of(needle) if w in set(seniority or ())]
    if carried:
        return False, f"carries seniority ({', '.join(sorted(set(carried)))})"

    matched = [r for r in rows if needle in r[0]]
    if len(matched) < MIN_ROWS:
        return False, f"only {len(matched)} listings, needs {MIN_ROWS}"
    # The wildcard guard, local_search's own. "software engineer" matches
    # 6,869 of 22,806 stored rows — a third of the market — so as a paid
    # query it buys the catalogue and ranks it afterwards.
    if rows and len(matched) / len(rows) > MAX_SHARE:
        return False, (f"matches {len(matched) / len(rows):.0%} of the "
                       f"market — a wildcard, not a search")
    companies = {r[3] for r in matched if r[3]}
    if len(companies) < MIN_COMPANIES:
        return False, (f"posted by {len(companies)} employer(s) — one "
                       f"company's internal title, not a market")

    own = set(own_concepts or ())

    # Checked before the statistical guards because it is the one a person
    # can act on: "claims ai, which your résumé does not evidence" says
    # what is wrong, where "8% overlap" only says something is.
    missing = unsupported_domains(title, own, resume_text, vocab)
    if missing:
        return False, (f"claims {', '.join(sorted(missing))}, which your "
                       f"résumé does not evidence")

    naming = sum(1 for r in matched if {skill_concepts.resolve(s)
                                        for s in r[2]} & own)
    share = naming / len(matched)
    if share < MIN_OVERLAP:
        return False, (f"only {share:.0%} of its listings want any skill "
                       f"you have")

    # Generic tools alone are not a relationship. A title whose only
    # connection to the candidate is that both mention Git is the Python
    # failure wearing a different hat.
    real = own - WORKFLOW
    if real:
        substantive = sum(1 for r in matched
                          if {skill_concepts.resolve(s) for s in r[2]} & real)
        if not substantive:
            return False, "connected to you only through workflow tools"

    if family is not None and family.anchor:
        # Canonicalisation must not have walked away from the family that
        # proposed it. The anchor's own words need not appear — "react
        # native" legitimately yields "mobile developer" — but the title
        # has to still want the anchor.
        wants = sum(1 for r in matched
                    if family.anchor in {skill_concepts.resolve(s)
                                         for s in r[2]})
        if not wants:
            return False, (f"no listing under it asks for "
                           f"{family.display}, which proposed it")

        # ...and its market must be recognisably the candidate's, not
        # merely a market that happens to touch one of their tools.
        #
        # This is the guard the lift bar cannot supply, because a sales
        # job asking for a CRM really does ask for it. Measured on the
        # audited corpus, the share of a title's listings wanting TWO OR
        # MORE of this candidate's concepts separates the two markets a
        # shared platform has, with a wide margin and no tuning:
        #
        #     business development representative   27%
        #     sales development representative      21%
        #     mobile app developer                  76%
        #     salesforce developer                  78%
        #     node.js developer                     92%
        #     react native developer                93%
        #
        # Anchored titles only. A title the person actually HELD is theirs
        # whatever the market makes of it — "software engineer" is a
        # 6,869-row bucket and scores 46%.
        pairs = sum(1 for r in matched
                    if len({skill_concepts.resolve(s) for s in r[2]} & own) >= 2)
        if pairs / len(matched) < MIN_PAIR_SHARE:
            return False, (f"only {pairs / len(matched):.0%} of its listings "
                           f"want two or more of your skills — a different "
                           f"profession that uses {family.display}")
    return True, f"{len(matched)} listings at {len(companies)} employers"


# --------------------------------------------------------------------------
# Market: concrete titles for a family
# --------------------------------------------------------------------------

def titles_for(family, market, limit=6):
    """Concrete titles the market posts for this family, best first.

    Delegated to local_search.candidates_for_skill rather than reinvented,
    because that function already carries the guard this needs most. Raw
    frequency picks the WRONG title for a specialist: the corpus names
    salesforce in 932 listings and the commonest titles among them are
    "development representative" and "business development
    representative" — sales jobs that happen to require the CRM. Its
    SKILL_LIFT bar (a title's listings must want the skill ten times more
    than the market does) keeps "salesforce developer" at 14.1x and
    throws those away.

    Breadth is still the point: a React Native developer reaches "mobile
    developer" and "react native engineer", not only the exact string
    their résumé used. What keeps it grounded is revalidation, not a
    narrow proposal.
    """
    import local_search
    if family.anchor:
        names = set(skill_concepts.aliases_for(family.anchor)) | {family.anchor}
        named = [r for r in market.rows
                 if names & {str(s).lower() for s in r[2]}
                 or family.anchor in {skill_concepts.resolve(s) for s in r[2]}]
    else:
        named = [r for r in market.rows if family.display in r[0]]
    if not named:
        return []
    counted = {}
    for title, _score, _skills, _company in named:
        counted[title] = counted.get(title, 0) + 1
    # Step 1's ordering, so live and frozen corpora holding the same rows
    # in different orders give the same answer.
    ranked = sorted(counted.items(),
                    key=lambda kv: local_search.rank_title(kv[1], kv[0]))
    return [title for title, _n in ranked[:limit * 5]]


def _shape(title):
    """A title's comparable shape, for deduplication.

    "full stack developer", "full-stack developer" and "fullstack
    developer" are one search bought three times. Substring containment
    does not see it; folding the punctuation out does.
    """
    return re.sub(r"[^a-z0-9]", "", str(title).lower())


def fill(families, market, own_concepts, resume_text, per_family=2):
    """Each family's concrete queries, revalidated against the candidate."""
    import local_search

    # A title belongs to ONE family, and to the right one. First-come
    # gave "react native developer" to the OAuth family simply because
    # OAuth was assessed earlier — the query was fine and the reason was
    # nonsense. So every (family, title) pair is scored by how much that
    # title's market actually wants that anchor, and the strongest claim
    # takes it: 99% of "react native developer" listings name React
    # Native, a handful name OAuth.
    claims = []
    for family in families:
        for rank_, title in enumerate(titles_for(family, market)):
            matched = [r for r in market.rows if title.lower() in r[0]]
            if not matched:
                continue
            if family.anchor:
                want = sum(1 for r in matched
                           if family.anchor in {skill_concepts.resolve(s)
                                                for s in r[2]})
                strength = want / len(matched)
            else:
                strength = 1.0          # the person's own held title
            claims.append((not family.primary, -strength, rank_, title,
                           family.key, family))
    claims.sort(key=lambda c: c[:5])

    taken = set()
    for _lane, _strength, _rank, title, _key, family in claims:
        if len(family.titles) >= per_family:
            continue
        key = title.strip().lower()
        shape = _shape(key)
        if shape in taken or any(key in got or got in key for got in taken):
            continue
        ok, why = revalidate(title, market.rows, own_concepts,
                             resume_text, market.vocab, family,
                             market.seniority)
        if ok:
            family.titles.append(title)
            taken.add(shape)
        elif len(family.rejected) < 4:
            family.rejected.append((title, why))
    # A held title is the person's own and does not need the market's
    # permission to be searched — but it does need to buy something.
    for family in families:
        if family.titles or family.anchor:
            continue
        rows = [r for r in market.rows if family.display in r[0]]
        # Their own title still may not be a wildcard: "software engineer"
        # is a third of the market, and paying for a third of the market
        # is not a search.
        if not (local_search.MIN_ROWS <= len(rows)
                <= MAX_SHARE * len(market.rows)):
            family.rejected.append(
                (family.display,
                 f"{len(rows)} listings — "
                 + ("too few to buy" if len(rows) < local_search.MIN_ROWS
                    else f"{len(rows) / len(market.rows):.0%} of the market, "
                         f"a wildcard")))
            continue
        family.titles.append(family.display)
    return families


def supported(families):
    """The families this market can actually be searched for.

    A proposal with nothing behind it is not a career lane, it is a
    skill. Dropped from the offer rather than carried as an empty row —
    the rejections stay on the family for anyone asking why.
    """
    return [f for f in families if f.titles]


def queries(families, cap=8):
    """The ordered search strings: primary families first, one each,
    then second choices, then the lanes.

    Round-robin rather than family-by-family, so a budget that only
    reaches four queries still reaches four DIRECTIONS rather than two
    directions twice.
    """
    out, reasons = [], {}
    for wanted_primary in (True, False):
        for depth in range(4):
            for family in families:
                if family.primary is not wanted_primary:
                    continue
                if depth >= len(family.titles) or len(out) >= cap:
                    continue
                title = family.titles[depth]
                if title in out:
                    continue
                out.append(title)
                reasons[title] = (family, depth)
    return out[:cap], reasons


def build(held_titles, importance, market, resume_text, preferred=(), cap=8):
    """Everything this module decides, as plain data."""
    own = {row["id"] for row in importance or ()}
    families = propose(held_titles, importance,
                       headline_of(resume_text), preferred)
    fill(families, market, own, resume_text)
    chosen, reasons = queries(families, cap)
    return {
        "role_keywords": chosen,
        "families": [f.as_dict() for f in families],
        "why": [{"query": title,
                 "family": reasons[title][0].display,
                 "source": reasons[title][0].source,
                 "primary": reasons[title][0].primary}
                for title in chosen],
    }


def demo():
    """Self-check. `python role_families.py` — offline, no model."""
    import local_search

    # A market big enough for the real guards. candidates_for_skill needs
    # eight employers and a 10x skill lift before it will offer a title,
    # so a toy corpus would test nothing.
    rows, noise = [], 900
    def cluster(title, n, skills, companies, score=28):
        for i in range(n):
            rows.append((title, score, frozenset(skills), f"{title[:4]}{i % companies}"))

    cluster("react native developer", 200, {"react native", "typescript", "redux"}, 20)
    cluster("mobile developer", 120, {"react native", "typescript"}, 14)
    cluster("salesforce developer", 160, {"salesforce", "apex", "soql"}, 18)
    cluster("full stack developer", 160, {"node.js", "react", "mongodb"}, 16)
    # The Python trap: a real market whose listings name git, and nothing
    # else the candidate has.
    cluster("python developer", 140, {"python", "django", "git"}, 15)
    # The one-company artifact.
    cluster("sde ii, acme now", 40, {"java", "node.js"}, 1)
    # The domain-drift trap: enough overlap to pass every statistical
    # guard, and a different career. Only the domain check can reject it.
    cluster("applied ai backend engineer", 140, {"python", "tensorflow",
                                                 "node.js", "react"}, 16)
    # The shared-platform trap: genuinely wants the CRM, wants nothing
    # else of theirs, and is not an engineering job at all.
    cluster("business development representative", 140, {"salesforce", "crm"}, 15)
    for i in range(noise):
        rows.append((f"software engineer {i % 11}", 5,
                     frozenset({"java", "sql", "agile"}), f"big{i % 30}"))

    market = local_search.Market(rows=rows, seniority=("senior", "staff"))

    resume = ("Kartik Verma\nFull-Stack Software Engineer\n"
              "Summary\nReact Native and Node.js, plus Salesforce.\n")
    importance = [
        {"id": "react native", "display": "React Native", "tier": "CORE"},
        {"id": "salesforce", "display": "Salesforce", "tier": "CORE"},
        {"id": "apex", "display": "Apex", "tier": "CORE"},
        {"id": "node.js", "display": "Node.js", "tier": "STRONG_SECONDARY"},
        {"id": "git", "display": "Git", "tier": "CORE"},
        {"id": "ci/cd", "display": "CI/CD", "tier": "CORE"},
        {"id": "typescript", "display": "TypeScript", "tier": "SUPPORTING"},
    ]

    # Workflow tools cannot anchor a career, even at CORE.
    assert not can_anchor("git", "CORE")
    assert not can_anchor("ci/cd", "CORE")
    assert not can_anchor("typescript", "SUPPORTING")
    assert can_anchor("react native", "CORE")
    assert can_anchor("node.js", "STRONG_SECONDARY")

    built = build(["software engineer"], importance, market, resume)
    keys = {f["key"] for f in built["families"]}
    assert "skill:git" not in keys, keys
    assert "skill:ci/cd" not in keys, keys
    assert "skill:react native" in keys and "skill:salesforce" in keys

    got = built["role_keywords"]
    assert "react native developer" in got, got
    assert "python developer" not in got, got
    assert "sde ii, acme now" not in got, got
    assert "applied ai backend engineer" not in got, got

    # Salesforce is evidenced and offered — as a lane, not the identity.
    salesforce = next(f for f in built["families"]
                      if f["key"] == "skill:salesforce")
    assert "salesforce developer" in salesforce["titles"]
    assert salesforce["primary"] is True  # the summary names it
    native = next(f for f in built["families"]
                  if f["key"] == "skill:react native")
    assert native["primary"] is True

    # The two guards, directly.
    ok, why = revalidate("sde ii, acme now", rows, {"java"}, resume,
                         market.vocab)
    assert not ok and "employer" in why, why
    ok, why = revalidate("applied ai backend engineer", rows,
                         {"node.js", "react native"}, resume, market.vocab)
    assert not ok and "ai" in why, why
    ok, why = revalidate("react native developer", rows,
                         {"react native", "typescript"}, resume, market.vocab)
    assert ok, why

    # Intent: an explicit preference is authoritative and primary.
    wanted = build(["software engineer"], importance, market, resume,
                   preferred=["full stack developer"])
    assert "full stack developer" in wanted["role_keywords"]
    first = next(w for w in wanted["why"]
                 if w["query"] == "full stack developer")
    assert first["primary"] and "asked for" in first["source"]

    # Row order cannot change the answer.
    import random
    shuffled = list(rows)
    random.Random(3).shuffle(shuffled)
    other = build(["software engineer"], importance,
                  local_search.Market(rows=shuffled,
                                      seniority=("senior", "staff")), resume)
    assert other["role_keywords"] == built["role_keywords"], (
        other["role_keywords"], built["role_keywords"])

    print("role_families demo ok")


if __name__ == "__main__":
    demo()
