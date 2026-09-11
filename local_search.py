"""Search fields for one résumé, derived from the jobs already scraped.

The other half of what Gemini used to do alone. Given the skills and
employment a local model read off a résumé (local_extract), this turns
them into the fields that drive the sweep:

    fields_for(person, market)   role_keywords, title_hints, skills, ...

Nothing here calls a model. The corpus in output/ is the instrument: a
job title is a real role because employers post it, a keyword is worth
paying for because the listings it draws want this person's skills, and
a skill is worth its own search because the market attaches a title to
it. Where Gemini brought world knowledge, this brings measurement, and
the two disagree in ways the benchmark records rather than hides.

THE ORDER KEYWORDS COME OUT IN
------------------------------
Three tiers, because a budget cap discards the tail and the tail must be
the least valuable thing:

  1 held      titles from this person's own relevant employment
  2 anchored  a title recovered for a specialist skill nothing else
              searches for — the step that found lovish 5 Salesforce
              roles and kanav 2 React Native ones where corpus lift
              alone found none
  3 corpus    ranked by market lift

Before the tiers existed a generic high-lift keyword could outrank the
person's own job title, and a cap then bought the market's aggregate
opinion instead of the candidate.

WHAT IS MEASURED AND WHAT IS NOT
--------------------------------
Retrieval and coverage are solved: the tiers fixed retrieval, the
skill_scan vocabulary union fixed most of coverage. SCORING is not.
Deterministic ranking cannot tell that "queueable apex" outranks
"postman" when corpus frequency, résumé structure and text position all
say they are alike — that residual is semantic and is documented, not
papered over.

The measurement harnesses live in bench/ and import from here, so the
benchmark scores the code that ships.

    python -m local_search --demo
"""

import collections
import csv
import glob
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# The market: every listing already scraped
# --------------------------------------------------------------------------

# config.py:534 already reasons in this band — "11 of the 15 reachable rows
# at score >= 20" — so the bar is the repo's own rather than one invented
# here. It is a MEASURING bar, not the user-facing min_score.
REACHABLE = 20

# n-grams of this many words. role_keywords are SEARCH strings and the
# boards match them loosely, so "backend engineer" is the useful unit —
# a whole scraped title like "senior software engineer ii (backend)" is
# not something anyone would search for.
NGRAM = (2, 3)

# A fragment seen fewer times than this is noise rather than a role.
MIN_LISTINGS = 10

# ...and a real role name is posted by MANY employers. One company's
# internal jargon — "engineer a2", "developer unifi" — can be frequent
# and well-scored inside that company's postings and is still not
# something anyone would search for.
MIN_COMPANIES = 5

# Above this share of the market a fragment is a wildcard: it buys the
# catalogue and lets scoring sort it out, which is exactly the spend the
# budget screen exists to prevent.
MAX_SHARE = WILDCARD_SHARE = 0.25

# Below this many listings a keyword is not "bad", it is unmeasured —
# this corpus is one person's market and cannot speak for another
# profession.
UNMEASURED = 1

# What a keyword's rows cost. LinkedIn is the cheapest paid source and so
# the most conservative choice for a waste figure.
CHEAPEST_RATE, CHEAPEST_BASIS = 0.045, 25

# Words that are not a role by themselves and drag a fragment towards
# matching everything. Deliberately short: a stop list for TITLE
# fragments, not a taxonomy.
FILLER = {"and", "or", "the", "a", "an", "of", "for", "with", "in", "at",
          "to", "new", "remote", "hybrid", "onsite", "full", "time", "part",
          "job", "jobs", "role", "opening", "openings", "hiring", "urgent",
          "immediate", "walk", "walkin", "fresher", "years", "yrs", "exp"}

_WORD = re.compile(r"[a-z0-9+#.]+")


def corpus_rows(output_dir=None):
    """[(title, score, frozenset(matched_skills), company)] per listing.

    utf-8-sig because 41 of the 54 files carry a BOM, which renamed the
    first column and made 58.8% of the corpus read as score 0.
    """
    output_dir = output_dir or os.path.join(HERE, "output")
    rows = []
    for path in glob.glob(os.path.join(output_dir, "**", "*.csv"),
                          recursive=True):
        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                if not reader.fieldnames or "title" not in reader.fieldnames:
                    continue
                for row in reader:
                    title = (row.get("title") or "").strip().lower()
                    if not title:
                        continue
                    try:
                        score = int(float(row.get("score") or 0))
                    except (TypeError, ValueError):
                        score = 0
                    skills = frozenset(
                        s.strip().lower()
                        for s in (row.get("matched_skills") or "").split(",")
                        if s.strip())
                    rows.append((title, score, skills,
                                 (row.get("company") or "").strip().lower()))
        except (OSError, UnicodeDecodeError, csv.Error):
            continue
    return rows


def fragments(title, seniority=()):
    """Seniority-free n-gram fragments of one job title.

    Seniority words are stripped rather than the title skipped: RULE 3
    asks for the stem, and "senior backend engineer" is evidence that
    "backend engineer" is a role someone hires for.
    """
    words = [w for w in _WORD.findall(title.lower())
             if w not in FILLER and w not in seniority and not w.isdigit()]
    out = set()
    for size in NGRAM:
        for i in range(len(words) - size + 1):
            out.add(" ".join(words[i:i + size]))
    return out


def index(rows, seniority=()):
    """{fragment: {listings, reachable, companies, skills}} over the corpus."""
    idx = {}
    for title, score, skills, company in rows:
        for frag in fragments(title, seniority):
            entry = idx.setdefault(
                frag, {"listings": 0, "reachable": 0, "companies": set(),
                       "skills": collections.Counter()})
            entry["listings"] += 1
            if score >= REACHABLE:
                entry["reachable"] += 1
            if company:
                entry["companies"].add(company)
            entry["skills"].update(skills)
    return idx


def vocabulary(rows):
    """Every skill term the market has ever named, with its listing count."""
    seen = collections.Counter()
    for _t, _s, skills, _c in rows:
        seen.update(skills)
    return seen


def seniority_lists():
    """config's own two seniority lists. Seniority is already handled twice
    over in config.SCORING, which is why RULE 3 says title_exclude is for
    DIFFERENT CAREERS and never for seniority."""
    import config
    return (tuple(config.SCORING["hard_drop_terms"]),
            tuple(config.SCORING["soft_drop_terms"]))


class Market:
    """The corpus, indexed once. Building it twice is the slow part."""

    def __init__(self, output_dir=None, rows=None, seniority=None):
        if seniority is None:
            hard, soft = seniority_lists()
        else:
            hard, soft = seniority, ()
        self.hard, self.soft = hard, soft
        self.seniority = tuple(hard) + tuple(soft)
        self.rows = list(rows) if rows is not None else corpus_rows(output_dir)
        self.index = index(self.rows, self.seniority)
        self.vocab = vocabulary(self.rows)
        self.total = len(self.rows)
        self.titles = [(t, s) for t, s, _sk, _c in self.rows]

    def __len__(self):
        return self.total


# --------------------------------------------------------------------------
# Retrieval: which listings actually want this person
# --------------------------------------------------------------------------

# Two overlapping skills is not two pieces of evidence. "sql + agile"
# matches most software listings and says nothing about anyone; "apex +
# soql" says a great deal. The sum of inverse document frequency over the
# overlap measures how much the match NARROWS the market, the same tf-idf
# shape corpus_signal.separation() already uses on weights.
#
# Calibrated against the pairs that actually went wrong: sql+agile scores
# 4.26 and put a business analyst into frontend-engineer searches,
# react+node.js scores 3.11, while react native+redux toolkit scores 8.54
# and apex+soql 12.11. The bar sits between them.
MIN_EVIDENCE = 7.0


def idf(skill, vocab, total):
    """How much knowing a listing wants this skill narrows the market."""
    return math.log(total / (1 + vocab.get(skill, 0)))


def evidence(overlap, vocab, total):
    """Summed idf of the skills a listing and a person share."""
    return sum(idf(skill, vocab, total) for skill in overlap)


def matching_rows(own, rows, need=2, vocab=None, total=None,
                  min_evidence=MIN_EVIDENCE):
    """The listings that actually want this person, and how much.

    Retrieval before aggregation. The first version scored every fragment
    in the corpus by how often its listings mentioned one of the person's
    skills, and produced the same junk for everyone — "apps stack",
    "engineer a2" — because a single ubiquitous skill like python is
    enough to make any globally well-scoring fragment look relevant.
    Selecting the LISTINGS first is what makes a Django backend engineer
    and a Go platform engineer come out different.
    """
    vocab = vocab if vocab is not None else vocabulary(rows)
    total = total or len(rows)
    out, weak = [], []
    for title, score, skills, _company in rows:
        shared = skills & own
        if len(shared) < need:
            continue
        strength = evidence(shared, vocab, total)
        row = (title, score, strength)
        if strength >= min_evidence:
            out.append(row)
        else:
            weak.append(row)
    # Someone whose every skill is a common one would otherwise retrieve
    # nothing at all. Thin evidence is still evidence when it is all
    # there is.
    return out or weak


def keywords_for(own, rows, idx, total, want=12, need=2,
                 min_listings=MIN_LISTINGS, seniority=(), vocab=None):
    """Title fragments distinctive to the listings that want these skills.

    Ranked by LIFT — how much more common a fragment is among this
    person's matching listings than in the market at large — rather than
    by raw frequency. Without it the ranking returns "software engineer"
    for everybody, which is both true and useless.
    """
    vocab = vocab if vocab is not None else vocabulary(rows)
    matched = matching_rows(own, rows, need, vocab, total)
    if not matched:
        return []
    # Each matched listing counts for how much EVIDENCE it carries, not
    # one apiece. A listing wanting apex and soql (12.11) is stronger
    # evidence about a Salesforce developer than one wanting java and
    # docker (6.75), and counting rows equally let the bulk of a
    # generalist skill list outvote the speciality that defines someone.
    here = collections.Counter()
    reach = collections.Counter()
    weight_total = 0.0
    for title, score, shared in matched:
        row_weight = max(1.0, float(shared))
        weight_total += row_weight
        for frag in fragments(title, seniority):
            here[frag] += row_weight
            if score >= REACHABLE:
                reach[frag] += row_weight

    scored = []
    for frag, mine in here.items():
        entry = idx.get(frag)
        if not entry or mine < min_listings:
            continue
        if len(entry["companies"]) < MIN_COMPANIES:
            continue
        share = entry["listings"] / total if total else 0.0
        if share > MAX_SHARE:
            continue
        lift = (mine / weight_total) / share if share and weight_total else 0.0
        if lift <= 1.0:
            continue
        # Reachability decides between two equally distinctive fragments:
        # being characteristic of this person's market is worth nothing if
        # the rows it draws are not worth reading.
        quality = reach[frag] / mine
        scored.append((lift * quality, mine, frag))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2]))

    # Overlapping fragments buy the same rows twice. "software engineer"
    # and "software engineer backend" are one search's worth of value at
    # two searches' cost, so the better-ranked one wins and the other goes.
    taken = []
    for _, _, frag in scored:
        if any(frag in got or got in frag for got in taken):
            continue
        taken.append(frag)
        if len(taken) >= want:
            break
    return taken


# --------------------------------------------------------------------------
# Real job titles, not n-gram artifacts
# --------------------------------------------------------------------------

def canonical(fragment, titles, seniority=(), max_words=4):
    """The most common COMPLETE job title containing this fragment.

    role_keywords are sent to LinkedIn and Indeed as literal search
    strings, and the ranked n-grams are not titles: "developer react
    native", "js developer", "stack ai". Only 4 of 13 derived for a real
    résumé were things anyone posts. Ranking finds the right concept and
    this puts a real title back on it — "native developer" becomes "react
    native developer", which 86 listings are actually called.
    """
    needle = fragment.strip().lower()
    if not needle:
        return None
    plain, senior = (None, 0), (None, 0)
    for title, count in titles.items():
        if needle not in title or not (2 <= len(title.split()) <= max_words):
            continue
        words = title.split()
        bucket = "senior" if any(w in seniority for w in words) else "plain"
        if bucket == "plain" and count > plain[1]:
            plain = (title, count)
        elif bucket == "senior" and count > senior[1]:
            senior = (title, count)
    # A title with no seniority word in it is preferred outright. Stripping
    # one produces a string that may be no title at all: "senior software
    # engineer onsite" became "software engineer onsite", which nobody
    # posts, and it went straight out as a search query.
    if plain[0]:
        return plain[0]
    if not senior[0]:
        return None
    stripped = " ".join(w for w in senior[0].split() if w not in seniority)
    return stripped if stripped and stripped in titles else None


def canonicalise(frags, rows, seniority=()):
    """Ranked fragments as real, deduplicated job titles."""
    titles = collections.Counter(t for t, _s, _sk, _c in rows)
    out = []
    for fragment in frags:
        title = canonical(fragment, titles, seniority)
        if title and title not in out:
            out.append(title)
    return out


def hints_for(own, rows, idx, total, seniority=(), floor=20, ceiling=40):
    """title_hints: the gate on every free source, deliberately wide.

    RULE 3 asks for twenty to forty entries and says a missing fragment is
    inventory nobody ever sees. The first version took only the twelve
    ranked keywords and their component words and produced thirteen, so
    the free sources were gated more tightly than config's own default.

    This gate only ever WIDENS — scraper.is_dev_title drops a posting
    whose title contains none of these — so the cost of one more entry is
    nothing and the cost of one missing entry is a job never seen.
    """
    out = []
    titles = collections.Counter(t for t, _s, _k, _c in rows)

    def add(value):
        value = value.strip().lower()
        if value and value not in out:
            out.append(value)

    ranked = keywords_for(own, rows, idx, total, want=ceiling,
                          seniority=seniority)
    for fragment in ranked:
        add(fragment)
        title = canonical(fragment, titles, seniority)
        if title:
            add(title)
        if len(out) >= ceiling:
            break
    # Single words, but only ones the market posts often enough to be a
    # real fragment of a title rather than a stray token.
    for fragment in ranked:
        for word in fragment.split():
            if idx.get(word, {}).get("listings", 0) >= MIN_LISTINGS * 5:
                add(word)
    return out[:ceiling]


# Words that mark a résumé line as a CONCEPT rather than a searchable
# tool. "SOLID principles" and "Data Structures & Algorithms (DSA)" are
# real things to know and nobody posts a job titled after them, so they
# add nothing to a search and dilute the weights that decide ranking.
CONCEPT = {"principles", "principle", "patterns", "pattern", "algorithms",
           "algorithm", "structures", "programming", "concepts", "concept",
           "methodologies", "methodology", "paradigms", "fundamentals",
           "practices", "oop", "oops", "dsa", "solid"}


def clean_skills(skills, vocab=()):
    """(kept, dropped) — the searchable half of an extracted skill list.

    A term is dropped only when it reads as a concept AND the market does
    not use it as a skill, so a rare-but-real tool the corpus has never
    seen survives.
    """
    vocab = vocab or ()
    kept, dropped = [], []
    for skill in skills:
        term = str(skill).strip().lower()
        if not term:
            continue
        words = set(re.findall(r"[a-z0-9+#.]+", term))
        if words & CONCEPT and term not in vocab:
            dropped.append(term)
        else:
            kept.append(term)
    return kept, dropped


# A résumé title is one role. Splitting on these gives the searchable
# stems inside it — "Full-Stack Developer / React Native" is two.
_SPLIT = re.compile(r"\s*[/|,()]\s*|\s+-\s+")


def from_resume(person, seniority=()):
    """Role keywords taken from the person's own job titles.

    The zero-dependency floor: no corpus, no model, no key. Seniority is
    stripped for the same reason RULE 3 asks for the stem — "Senior
    Backend Engineer" as a search string finds only the senior rows, and
    the reachable ones are the others.
    """
    import local_extract

    out = []
    # The same two clauses the years derivation uses. Without them hana
    # searches for "structural engineer" and kwame for "mathematics
    # teacher" — the careers they left — and bhaskar searches for the
    # internship he did rather than the job he wants.
    for job in local_extract.countable(person.get("employment") or ()):
        for piece in _SPLIT.split(job.get("title") or ""):
            words = [w for w in re.findall(r"[a-z0-9+#.]+", piece.lower())
                     if w not in seniority]
            stem = " ".join(words).strip()
            # One word is a fragment, not a role: "engineer" alone buys
            # the catalogue, which is what the wildcard guard exists for.
            if len(words) >= 2 and stem not in out:
                out.append(stem)
    return out


# --------------------------------------------------------------------------
# What a keyword buys, and the validators that decide whether to buy it
# --------------------------------------------------------------------------

def droppable(title, hard):
    """Would the pipeline delete this row after paying for it?

    Word-boundary on purpose, matching config's own matcher: "intern" must
    not fire inside "internal" or "international".
    """
    low = title.lower()
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low)
               for w in hard)


def spend(listings):
    """Dollars for that many results at the cheapest paid rate."""
    return math.ceil(listings / CHEAPEST_BASIS) * CHEAPEST_RATE


def baseline(titles):
    """Share of the whole market that reaches the readable band."""
    if not titles:
        return 0.0
    return sum(1 for _, s in titles if s >= REACHABLE) / len(titles)


def profile_of(term, titles, hard):
    """What one keyword actually buys: rows, reachable rows, wasted rows."""
    needle = term.strip().lower()
    if not needle:
        return {"term": term, "listings": 0, "reachable": 0, "wasted": 0,
                "share": 0.0, "precision": None}
    matched = [(t, s) for t, s in titles if needle in t]
    reachable = sum(1 for _, s in matched if s >= REACHABLE)
    wasted = sum(1 for t, _ in matched if droppable(t, hard))
    return {
        "term": term,
        "listings": len(matched),
        "reachable": reachable,
        "wasted": wasted,
        "share": len(matched) / len(titles) if titles else 0.0,
        "precision": reachable / len(matched) if matched else None,
    }


def buys(keywords, rows, own, hard=()):
    """What a keyword set would actually buy, measured three ways.

    `relevance` is the honest per-person number: the share of purchased
    listings that mention at least one of this person's skills. It is a
    fact about the listings, so unlike `score` it does not flatter
    keywords aimed at the corpus owner's field.
    """
    needles = [k.strip().lower() for k in keywords if k.strip()]
    if not needles:
        return {"listings": 0, "relevance": None, "reachable": None,
                "wasted": 0, "spend": 0.0}
    seen = [r for r in rows if any(n in r[0] for n in needles)]
    if not seen:
        return {"listings": 0, "relevance": None, "reachable": None,
                "wasted": 0, "spend": 0.0}
    wanted = sum(1 for _t, _s, skills, _c in seen if skills & own)
    reach = sum(1 for _t, score, _sk, _c in seen if score >= REACHABLE)
    waste = sum(1 for title, _s, _sk, _c in seen if droppable(title, hard))
    return {"listings": len(seen), "relevance": wanted / len(seen),
            "reachable": reach / len(seen), "wasted": waste,
            "spend": spend(len(seen))}


def _words(text):
    return set(str(text).lower().replace("-", " ").replace(".", " ").split())


def _has(term, words):
    """Does this term carry one of these words, as a word?"""
    return bool(_words(term) & set(words))


def check_role_keywords(profile, titles, hard, soft):
    """The field that spends. Corrections here are dollars, not tidiness."""
    terms = list(profile.get("role_keywords") or [])
    corrections, escalations = [], []
    wildcards, senior = [], []
    for term in terms:
        row = profile_of(term, titles, hard)
        # A keyword matching a quarter of the market is not a search, it is
        # the catalogue. "software engineer" draws 4,890 of 15,514 rows at
        # a mean of 3.6 and costs $8.82 at the cheapest paid rate.
        if row["share"] > WILDCARD_SHARE:
            wildcards.append(term)
        # A hard_drop word in a paid keyword buys rows config DELETES —
        # pure waste. A soft_drop word only costs them -4, so those rows
        # still reach the shortlist and dropping the keyword would lose
        # real inventory. config draws that line itself; so does this.
        if _has(term, hard):
            senior.append(term)
    if wildcards:
        corrections.append({
            "field": "role_keywords", "action": "dropped",
            "removed": wildcards,
            "why": f"each matches over {WILDCARD_SHARE:.0%} of the market, so "
                   f"the search buys the catalogue and pays per row"})
    if senior:
        corrections.append({
            "field": "role_keywords", "action": "dropped",
            "removed": senior,
            "why": "carries a config.SCORING hard_drop word, so every row "
                   "it matches is deleted after being paid for"})
    bad = len(set(wildcards) | set(senior))
    if terms and bad / len(terms) > 0.5:
        escalations.append(
            f"role_keywords: {bad} of {len(terms)} are wildcards or carry "
            f"seniority, which is not a list to correct item by item")
    return corrections, escalations


def check_penalties(profile):
    """A penalty on the person's own skill is a penalty on the person."""
    penalties = [e["term"] for e in profile.get("penalty_terms") or ()]
    own = {e["term"].strip().lower()
           for e in profile.get("skill_weights") or () if e.get("term")}
    clash = [p for p in penalties if p.strip().lower() in own]
    if not clash:
        return [], []
    return [{"field": "penalty_terms", "action": "dropped", "removed": clash,
             "why": "also listed as one of this person's own skills, and "
                    "penalty_terms is negated at render time"}], []


def check_title_exclude(profile, hard, soft):
    """The gate that wins over the others, so the gate that can delete work.

    scraper.is_dev_title checks title_exclude FIRST, so an entry matching
    the person's own target roles removes inventory before anything scores
    it, and nothing downstream reports what went missing.
    """
    excludes = list(profile.get("title_exclude") or [])
    targets = [t.strip().lower() for t in
               (list(profile.get("role_keywords") or [])
                + list(profile.get("title_hints") or [])) if t.strip()]
    corrections, escalations = [], []
    # One direction only. The test is "would a job titled like one of this
    # person's target roles be caught by this exclude fragment", which is
    # `exclude in target`. Testing both directions flagged the shipped
    # profiles' "sdet" because the hint "sde" is a substring of it — and
    # SDET is a different career, which is exactly what this gate is for.
    self_block = [e for e in excludes
                  if any(e.strip().lower() in t for t in targets)]
    # RULE 3: title_exclude is for DIFFERENT CAREERS, never for seniority.
    senior = [e for e in excludes
              if _has(e, hard + soft) and e not in self_block]
    if self_block:
        corrections.append({
            "field": "title_exclude", "action": "dropped",
            "removed": self_block,
            "why": "matches this person's own role_keywords or title_hints, "
                   "and title_exclude is checked first so it would delete "
                   "their target roles silently"})
    if senior:
        corrections.append({
            "field": "title_exclude", "action": "dropped", "removed": senior,
            "why": "seniority, which config.SCORING already ranks — RULE 3 "
                   "reserves this gate for different careers"})
    return corrections, escalations


def check_title_hints(profile, titles, hard, soft):
    """The gate on the free sources. Widening only, so the risk is one-sided."""
    hints = list(profile.get("title_hints") or [])
    corrections = []
    # This gate only ever WIDENS, so a present senior variant costs
    # nothing — RULE 3 says "err towards including a title". The real
    # failure is a MISSING stem: "senior developer" without "developer"
    # gates out every non-senior row, and those rows are the reachable
    # ones. So the stem is added rather than the hint removed.
    have = {h.strip().lower() for h in hints}
    add = []
    for hint in hints:
        stem = " ".join(w for w in hint.lower().split() if w not in soft)
        stem = stem.strip()
        if stem and stem != hint.strip().lower() and stem not in have:
            add.append(stem)
            have.add(stem)
    if add:
        corrections.append({
            "field": "title_hints", "action": "added", "added": add,
            "why": "the seniority-free stem was missing, and without it the "
                   "gate drops every non-senior row — RULE 3 asks for "
                   "'developer', not only 'senior developer'"})
    return corrections, []


def check_domain(profile, titles, hard):
    """domain_title_terms promote on the title alone, so they must earn it."""
    terms = list(profile.get("domain_title_terms") or [])
    corrections, escalations = [], []
    bar = baseline(titles)
    leaky = []
    for term in terms:
        row = profile_of(term, titles, hard)
        # Promoting a job on its title alone is only defensible if that
        # title beats the market. A term whose matches reach the readable
        # band LESS often than a random row is worse than no rule at all —
        # which is precisely RULE 2's "business analyst" leak.
        if row["listings"] > UNMEASURED and row["precision"] is not None \
                and row["precision"] < bar:
            leaky.append(term)
    if leaky:
        corrections.append({
            "field": "domain_title_terms", "action": "dropped",
            "removed": leaky,
            "why": f"the rows each one matches reach score {REACHABLE} less "
                   f"often than the market's own {bar:.0%}, so promoting on "
                   f"this title alone is worse than not promoting"})
    # A bonus with nothing to combine is a dead setting.
    halves = (profile.get("domain_half_a") or [],
              profile.get("domain_half_b") or [])
    if profile.get("domain_bonus") and not (halves[0] and halves[1]):
        corrections.append({
            "field": "domain_bonus", "action": "computed",
            "from": profile.get("domain_bonus"), "to": 0,
            "why": "a both-halves bonus with an empty half can never fire"})
    return corrections, escalations


def validate(profile, titles, hard=None, soft=None):
    """One decision for one generated profile, with its reasons."""
    if hard is None or soft is None:
        hard, soft = seniority_lists()
    if not profile:
        return {"decision": "escalate", "result": None, "corrections": [],
                "reasons": ["the local model returned nothing usable"]}
    corrections, reasons = [], []
    for check in (lambda p: check_role_keywords(p, titles, hard, soft),
                  lambda p: check_penalties(p),
                  lambda p: check_title_exclude(p, hard, soft),
                  lambda p: check_title_hints(p, titles, hard, soft),
                  lambda p: check_domain(p, titles, hard)):
        fixes, stops = check(profile)
        corrections.extend(fixes)
        reasons.extend(stops)
    if reasons:
        return {"decision": "escalate", "result": None,
                "corrections": [], "reasons": reasons}

    result = dict(profile)
    for fix in corrections:
        field = fix["field"]
        if fix["action"] == "dropped":
            gone = {str(v).strip().lower() for v in fix["removed"]}
            values = result.get(field) or []
            if values and isinstance(values[0], dict):
                result[field] = [e for e in values
                                 if str(e.get("term", "")).strip().lower()
                                 not in gone]
            else:
                result[field] = [v for v in values
                                 if str(v).strip().lower() not in gone]
        elif fix["action"] == "added":
            result[field] = list(result.get(field) or []) + list(fix["added"])
        elif fix["action"] == "computed":
            result[field] = fix["to"]
    return {"decision": "corrected" if corrections else "accept",
            "result": result, "corrections": corrections, "reasons": []}


def validated(keywords, rows, seniority):
    """The keywords a validator would actually let through.

    Measuring unvalidated strategies flattered the wrong one: lena's own
    title is "Software Engineer", which buys 4,890 rows — a third of the
    market — and dragged résumé-only below the market on reachability
    while looking like a volume win.
    """
    profile = {"role_keywords": list(keywords), "title_hints": [],
               "title_exclude": [], "skill_weights": [], "penalty_terms": [],
               "domain_half_a": [], "domain_half_b": [],
               "domain_title_terms": [], "domain_bonus": 0}
    titles = [(t, s) for t, s, _sk, _c in rows]
    out = validate(profile, titles)
    if out["decision"] == "escalate":
        return []
    # Deduplicated, because the two sources overlap. A résumé saying
    # "Senior Business Development Associate" yields the stem "business
    # development associate", and corpus canonicalisation independently
    # yields the same title — one search's worth of rows bought twice.
    seen, unique = set(), []
    for keyword in out["result"]["role_keywords"]:
        key = keyword.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(keyword)
    return unique


# --------------------------------------------------------------------------
# The orphan pass: a specialist skill nothing else is searching for
# --------------------------------------------------------------------------

# A skill is REPRESENTED when one of the selected keywords already draws
# listings that name it this often. Below that the keyword set is not
# really searching for it, whatever the skill list says.
REPRESENTED = 0.10

# A skill needs at least this many listings, at this many employers, to be
# worth a paid search of its own. One employer's stack is not a market.
ORPHAN_MIN_LISTINGS = 15
ORPHAN_MIN_COMPANIES = 8

# A title only earns a search because of THIS skill if its listings name
# the skill far more than the market does. Without this the mechanism
# offered "backend engineer" for spring boot and "full stack engineer" for
# aws, and one person's keyword set went from 408 rows at 94% relevance to
# 3,095 at 67% — seven times the cost for worse results, while missing the
# one title the feature exists for.
#
# 10x keeps salesforce developer (14.1x), java developer (21.4x), android
# developer (28.1x) and ai/ml engineer (17.6x); it rejects backend engineer
# (5.4x), full stack engineer (5.0x) and account executive (5.8x).
SKILL_LIFT = 10.0

# A title can be right for someone even when most of its listings want none
# of their other skills — that is what being a SPECIALIST means. Lovish
# writes enterprise Apex and "salesforce developer" scores 34% against his
# whole skill set, because those listings do not ask for his React or Node.
# Judged on the whole set, the bar keeps the generalist's secondary skill
# and throws away the specialist's primary one.
#
# So the anchor gets its own test: how much this title's market wants the
# SKILL THAT PRODUCED IT, weighted by how much that skill narrows the
# market. Raw anchor share cannot do it alone — apex scores 0.25 and java
# 0.21 — but apex is four times rarer, and the product separates them:
# apex 1.46, salesforce 2.39, java 0.84, sql 0.68.
ANCHOR_EVIDENCE = 1.2

# And a title nothing much is posted under is not worth a search whatever
# it scores: "sf data cloud consultant" matched two listings.
MIN_ROWS = 20


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

    Returns [(skill, listings, employers, share)] worst-covered first.
    """
    vocab = vocab if vocab is not None else vocabulary(rows)
    employers = collections.defaultdict(set)
    for _t, _s, skills, company in rows:
        for skill in skills:
            if company:
                employers[skill].add(company)
    out = []
    for skill in sorted(own):
        listings = vocab.get(skill, 0)
        if listings < ORPHAN_MIN_LISTINGS \
                or len(employers[skill]) < ORPHAN_MIN_COMPANIES:
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
    names "salesforce" in 932 listings and the commonest titles among them
    are "development representative" (23%) and "business development
    representative" (12%) — sales jobs that require Salesforce CRM.
    "salesforce developer" is 5.9% of them, and it is the one a mobile
    engineer with Salesforce SDK experience should be searching.

    Every candidate is a real title from the corpus, guarded the same way
    every other keyword is, so whatever selects among them cannot invent one.
    """
    named = [r for r in rows if skill in r[2]]
    if not named:
        return []
    vocab = vocab if vocab is not None else vocabulary(rows)
    here = collections.Counter()
    for title, _score, _skills, _company in named:
        for fragment in fragments(title, seniority):
            here[fragment] += 1
    titles = collections.Counter(t for t, _s, _k, _c in rows)
    out = []
    for fragment, count in here.most_common(80):
        entry = idx.get(fragment)
        if not entry or count / len(named) < min_share:
            continue
        if len(entry["companies"]) < ORPHAN_MIN_COMPANIES:
            continue
        if entry["listings"] / total > MAX_SHARE:
            continue
        title = canonical(fragment, titles, seniority)
        if not title or title in out:
            continue
        if any(title in got or got in title for got in out):
            continue
        # The guard that makes this a skill-anchored keyword rather than a
        # second helping of generic ones.
        lift = skill_lift(title, skill, rows, vocab, total)
        if lift is None or lift < SKILL_LIFT:
            continue
        out.append(title)
        if len(out) >= want:
            break
    return out


def anchor_evidence(title, anchor, rows, vocab, total):
    """How strongly this title's market wants the skill that produced it."""
    named = [r for r in rows if title in r[0]]
    if not named or not anchor:
        return 0.0
    share = sum(1 for r in named if anchor in r[2]) / len(named)
    return share * idf(anchor, vocab, total)


def worth_it(title, rows, own, anchor=None, vocab=None, total=None):
    """Is this addition about this person, or just a wider net?"""
    got = buys([title], rows, own)
    if got["listings"] < MIN_ROWS or got["relevance"] is None:
        return False, got
    if anchor is None:
        # No anchor to reason about: fall back to the whole-set bar.
        return got["relevance"] >= 0.75, got
    # One gate, not two. The whole-set relevance test used to be an
    # alternative route in, and it let "associate ai/ml engineer" through
    # on a git anchor — generic skill, 0.87 evidence, no business being a
    # paid search. Anchor evidence already says what the other test was
    # reaching for, and says it for specialists too.
    vocab = vocab if vocab is not None else vocabulary(rows)
    total = total or len(rows)
    strength = anchor_evidence(title, anchor, rows, vocab, total)
    return strength >= ANCHOR_EVIDENCE, got


def blocks_for(own, keywords, rows, idx, total, seniority, cap=8, vocab=None):
    """[(skill, [candidate titles])] for this person's orphaned skills."""
    out = []
    for skill, _n, _emp, _share in orphans(own, keywords, rows, vocab):
        titles = candidates_for_skill(skill, rows, idx, total, seniority,
                                      vocab=vocab)
        if titles:
            out.append((skill, titles))
        if len(out) >= cap:
            break
    return out


def select_detail(own, base, rows, idx, total, seniority, vocab=None, cap=2):
    """The orphan keywords to add, with the skill that produced each.

    No model. One was here: asked to pick the software title out of a
    skill's candidates — "salesforce developer" rather than "sales
    operations analyst" — it returned an empty list at temperature 0 on
    the one résumé the feature exists for. Ranking by anchor evidence and
    capping does both jobs, deterministically.
    """
    vocab = vocab if vocab is not None else vocabulary(rows)
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


def select(own, base, rows, idx, total, seniority, vocab=None, cap=2):
    """Just the orphan keywords, without the provenance."""
    return [title for title, _skill, _evidence in
            select_detail(own, base, rows, idx, total, seniority, vocab, cap)]


# --------------------------------------------------------------------------
# The order they are searched in
# --------------------------------------------------------------------------

TIERS = {1: "held", 2: "anchored", 3: "corpus"}


def rank(held, anchored, corpus):
    """[(keyword, tier, why)] in the order they should be searched.

    `held` are titles from relevant employment, `anchored` are
    (keyword, skill, evidence) from the specialist recovery, `corpus` are
    the corpus-ranked keywords. Ties inside a tier keep the order they
    arrived in, which is already each source's own ranking.
    """
    out, seen = [], set()

    def add(keyword, tier, why):
        key = str(keyword).strip().lower()
        if not key or key in seen:
            return
        # A keyword reachable two ways is bought once, at its BEST tier —
        # the first tier that offered it, since tiers are added in order.
        seen.add(key)
        out.append((key, tier, why))

    for title in held or ():
        add(title, 1, "held: a title from this person's own employment")
    for item in anchored or ():
        keyword, skill, strength = (item if isinstance(item, (tuple, list))
                                    else (item, "", 0.0))
        add(keyword, 2,
            f"anchored: recovered from '{skill}' (evidence {strength:.2f})"
            if skill else "anchored: recovered from a discriminative skill")
    for keyword in corpus or ():
        add(keyword, 3, "corpus: ranked by market lift")
    return out


def ranked_keywords(ranked):
    """Just the strings, in order."""
    return [keyword for keyword, _tier, _why in ranked]


def explain(ranked, limit=None):
    """The ordering, said out loud, so a bad first keyword is visible."""
    lines = []
    for position, (keyword, tier, why) in enumerate(ranked, start=1):
        mark = "  <- would run" if limit and position <= limit else ""
        lines.append(f"    {position:>2}. [{TIERS[tier]:<8}] {keyword:<38}"
                     f" {why}{mark}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Budget reach: the user only ever buys the PREFIX
# --------------------------------------------------------------------------
#
# scraper.build_search_plan is keyword-major, so keyword k occupies
# searches [k*L, k*L+L) for L locations, and scraper.py stops the whole
# sweep the moment spend crosses max_spend_usd. Measured over five live
# runs: $0.88 for ~45 LinkedIn searches, and a $0.15 cap reached nine —
# four keywords at two locations.
#
# So a keyword list longer than the budget is not a longer search, it is a
# TRUNCATED one. The first integrated live run generated the right
# specialist keyword for two people and placed it at position 6 and
# position 10; neither ever ran, and both bought zero specialist rows from
# the sources they paid for. Ordering by rank optimises the whole list;
# the user only ever buys the prefix.
#
# This reorders what rank() produced. It selects nothing, drops nothing,
# and changes no score — every candidate and every tier is what it was.

# How much a title's own evidence is trusted against the market's. The
# pseudo-count is MIN_ROWS, the same "a title nothing much is posted under
# is not worth a search" bar the orphan pass already applies, so a title
# at that bar is trusted half as much as the market and one far above it
# nearly fully. Without this a 21-listing title out-bid a 217-listing one
# on the same skills, because with a handful of rows three mobile
# postings read as 14%.
SHARE_PRIOR = MIN_ROWS


def wanted_skills(keyword, rows, own, vocab, total, alpha=SHARE_PRIOR):
    """{skill: shrunk share of this keyword's listings naming it}.

    A fact about the listings the keyword buys, so it is comparable
    across people and cannot flatter one profession.
    """
    needle = keyword.strip().lower()
    matched = [r for r in rows if needle in r[0]]
    if not matched:
        return {}, 0, 0
    out = {}
    for skill in own:
        n = sum(1 for r in matched if skill in r[2])
        if n:
            base = vocab.get(skill, 0) / total if total else 0.0
            out[skill] = (n + alpha * base) / (len(matched) + alpha)
    return out, len(matched), len({r[3] for r in matched if r[3]})


def _marginal(share, idfs, covered):
    """Evidence this keyword adds that nothing already bought covers.

    Diminishing returns, so a second near-identical variant of one role
    is worth little and stops consuming a slot the budget can afford.
    """
    gain = 0.0
    for skill, value in share.items():
        extra = value - covered.get(skill, 0.0)
        if extra > 0:
            gain += idfs[skill] * extra
    return gain


def budget_order(keywords, held, rows, own, vocab, total):
    """Reorder so the affordable prefix carries the most evidence.

    Greedy marginal coverage, over candidates the market actually posts.
    Coverage alone picks narrow titles — for one résumé it covered twice
    the skills and bought FIVE listings against 44 — so eligibility uses
    the orphan pass's own bars (MIN_ROWS, ORPHAN_MIN_COMPANIES) and a thin
    candidate waits behind every candidate the market supports.

    One held title leads when the market supports it. It is the strongest
    prior there is, but putting EVERY held title first spent two of a
    small cap's four searches on "software developer" and "solutions
    developer" for a mobile specialist.

    Deterministic: ties break by listing count then alphabetically.
    """
    info = {}
    for keyword in keywords:
        share, listings, companies = wanted_skills(keyword, rows, own,
                                                   vocab, total)
        info[keyword] = {
            "share": share, "listings": listings, "companies": companies,
            "idf": {s: idf(s, vocab, total) for s in share},
        }
    eligible = [k for k in keywords
                if info[k]["listings"] >= MIN_ROWS
                and info[k]["companies"] >= ORPHAN_MIN_COMPANIES]
    remaining = list(eligible) if len(eligible) >= 2 else list(keywords)
    thin = [k for k in keywords if k not in remaining]
    held_set = {h.strip().lower() for h in held or ()}
    order, covered = [], {}

    def take(keyword):
        order.append(keyword)
        remaining.remove(keyword)
        for skill, value in info[keyword]["share"].items():
            covered[skill] = max(covered.get(skill, 0.0), value)

    leading = [k for k in remaining if k.strip().lower() in held_set]
    if leading:
        take(max(leading, key=lambda k: (
            _marginal(info[k]["share"], info[k]["idf"], covered),
            info[k]["listings"], k)))

    while remaining:
        best = max(remaining, key=lambda k: (
            _marginal(info[k]["share"], info[k]["idf"], covered),
            info[k]["listings"], k))
        if _marginal(info[best]["share"], info[best]["idf"], covered) <= 0:
            order.extend(remaining)
            break
        take(best)
    return order + thin


# --------------------------------------------------------------------------
# The whole derivation, for one person
# --------------------------------------------------------------------------

def fields_for(person, market, want=12):
    """Every corpus-derived search field for one person.

    `person` is {"skills": [...], "employment": [rows]} — whatever
    local_extract produced and route() approved. `market` is a Market.

    Returns a dict carrying the fields AND the provenance: which skills
    were dropped as concepts, and why each keyword is in the order it is.
    """
    rows, idx = market.rows, market.index
    total, vocab, seniority = market.total, market.vocab, market.seniority

    skills, filler = clean_skills(person.get("skills") or (), vocab)
    own = set(skills)

    held_raw = from_resume(person, seniority)
    corpus_raw = canonicalise(
        keywords_for(own, rows, idx, total, want=want, seniority=seniority),
        rows, seniority)
    # Validated as ONE list, so every guard sees the same input it was
    # measured on. The split below is for ordering only.
    base = validated(held_raw + corpus_raw, rows, seniority)
    held_keys = {h.strip().lower() for h in held_raw}
    held = [k for k in base if k.strip().lower() in held_keys]
    corpus = [k for k in base if k.strip().lower() not in held_keys]

    anchored = select_detail(own, base, rows, idx, total, seniority, vocab)
    order = rank(held, anchored, corpus)

    # Last step, and only an order: the tiers, the reasons and the
    # candidate set are exactly what rank() produced. What changes is
    # which of them the user's budget actually reaches.
    reasons = {k: (t, w) for k, t, w in order}
    reordered = budget_order([k for k, _t, _w in order], held, rows, own,
                             vocab, total)
    order = [(k, reasons[k][0], reasons[k][1]) for k in reordered]

    return {
        "skills": sorted(own),
        "filler_dropped": filler,
        "role_keywords": ranked_keywords(order),
        "ranking": [[k, t, w] for k, t, w in order],
        "from_orphans": [title for title, _s, _e in anchored],
        "title_hints": hints_for(own, rows, idx, total, seniority),
    }


def demo():
    """Self-check on a small synthetic market, with every guard mutated."""
    # An 805-row market: a Salesforce cluster, a React cluster, and noise.
    # The Salesforce cluster is deliberately SMALL — apex has to be rare
    # for its 10x skill-lift to be reachable at all, which is the point
    # of that guard. At 120 rows it was 20% of the market and capped at
    # 5x, so the orphan assertion below would have failed for the wrong
    # reason.
    rows = []
    for i in range(60):
        rows.append(("salesforce developer", 25 if i % 2 else 5,
                     frozenset({"apex", "soql", "lwc"}), f"sfco{i % 20}"))
    for i in range(60):
        rows.append(("business development representative", 2,
                     frozenset({"salesforce", "crm"}), f"salesco{i % 12}"))
    # Half the React cluster omits redux, so redux is 50%-represented by
    # the React keyword rather than 100%. A skill represented at exactly
    # 1.0 cannot test the REPRESENTED guard, because the comparison is
    # `>=` and raising the bar to 1.0 still excludes it.
    for i in range(120):
        rows.append(("react native developer", 30 if i % 2 else 4,
                     frozenset({"react native", "redux", "javascript"}),
                     f"mobco{i % 20}"))
    for i in range(120):
        rows.append(("react native developer", 30 if i % 2 else 4,
                     frozenset({"react native", "javascript"}),
                     f"mobco{i % 20}"))
    # DECOY 1, for MIN_ROWS: a rare skill with a high-lift title at ten
    # employers, under twenty listings. Every orphan gate passes and only
    # the row count refuses it.
    for i in range(15):
        rows.append(("integration consultant", 20,
                     frozenset({"mulesoft", "esb"}), f"intco{i % 10}"))
    # DECOY 2, for ORPHAN_MIN_COMPANIES: one employer's internal stack.
    # Frequent enough to look like a market, posted by nobody else.
    for i in range(30):
        rows.append(("workflow automation engineer", 22,
                     frozenset({"workflow builder"}), "onlyco"))
    # Noise is the bulk of it on purpose: at 400 rows the Salesforce
    # cluster was 30% of the market and the WILDCARD guard removed it,
    # so the retrieval assertion below would have passed or failed for
    # the wrong reason.
    for i in range(400):
        rows.append((f"software engineer {i % 7}", 3,
                     frozenset({"java", "sql", "agile"}), f"bigco{i % 25}"))

    market = Market(rows=rows, seniority=("senior", "staff", "lead"))
    assert len(market) == 805
    assert market.vocab["apex"] == 60

    # Retrieval picks the right cluster, not the biggest one.
    sf = {"apex", "soql", "lwc"}
    keys = keywords_for(sf, market.rows, market.index, market.total,
                        seniority=market.seniority)
    assert any("salesforce" in k for k in keys), keys
    assert not any("react" in k for k in keys), keys

    # idf separates a narrowing overlap from a ubiquitous one.
    assert evidence({"apex", "soql"}, market.vocab, market.total) > \
        evidence({"java", "sql"}, market.vocab, market.total)
    # ...and the weak overlap is what MIN_EVIDENCE is set between.
    assert evidence({"java", "sql"}, market.vocab, market.total) < MIN_EVIDENCE
    # MIN_EVIDENCE partitions, it does not filter: `out or weak` means a
    # person whose every skill is common still retrieves their rows. An
    # unreachable bar therefore changes nothing, which is deliberate and
    # is the one guard here with no failing direction.
    everything = matching_rows(sf, market.rows, 2, market.vocab,
                               market.total, min_evidence=10 ** 9)
    assert len(everything) == len(matching_rows(
        sf, market.rows, 2, market.vocab, market.total)), "fallback lost"
    # And when BOTH tiers have rows the weak ones are DISCARDED, not
    # appended — `out or weak`, never `out + weak`. For someone holding
    # both a rare stack and a common one, the rare rows are the retrieval
    # set and the common rows are not diluted into it.
    both = {"apex", "soql", "lwc", "react native", "redux", "javascript"}
    strong = matching_rows(both, market.rows, 2, market.vocab, market.total)
    assert len(strong) == 60, len(strong)
    assert all("salesforce" in title for title, _s, _e in strong)
    # ...which is only meaningful because the weak tier is genuinely
    # non-empty here: 240 React rows are being set aside, not missing.
    assert len(matching_rows(both, market.rows, 2, market.vocab,
                             market.total, min_evidence=0.0)) == 300

    # min_listings is a real filter, and is passed rather than patched for
    # the same reason: it is a default argument.
    assert keywords_for(sf, market.rows, market.index, market.total,
                        seniority=market.seniority,
                        min_listings=10 ** 6) == []

    # canonical() turns a fragment into a title people actually post.
    titles = collections.Counter(t for t, _s, _k, _c in market.rows)
    assert canonical("native developer", titles,
                     market.seniority) == "react native developer"

    # clean_skills drops a concept the market never names, keeps a real tool.
    kept, dropped = clean_skills(
        ["solid principles", "dsa", "apex", "react native"], market.vocab)
    assert dropped == ["solid principles", "dsa"], dropped
    assert kept == ["apex", "react native"], kept
    # ...but a "concept" word the market DOES use as a skill survives.
    kept2, dropped2 = clean_skills(["programming"], {"programming": 40})
    assert kept2 == ["programming"] and not dropped2

    # from_resume takes held titles and strips seniority, skipping the
    # internship and the abandoned career.
    person = {"skills": sorted(sf), "employment": [
        {"title": "Senior Salesforce Developer", "start": "Jan 2022",
         "end": "present", "relevant": True},
        {"title": "Mathematics Teacher", "start": "Jan 2014",
         "end": "Dec 2021", "relevant": False},
        {"title": "Software Intern", "start": "Jan 2021", "end": "Mar 2021",
         "relevant": True}]}
    held = from_resume(person, market.seniority)
    assert held == ["salesforce developer"], held

    # --- the validators, each mutated so it is the sole cause -------------
    plain = {"role_keywords": [], "title_hints": [], "title_exclude": [],
             "skill_weights": [], "penalty_terms": [], "domain_half_a": [],
             "domain_half_b": [], "domain_title_terms": [], "domain_bonus": 0}
    hard, soft = ("intern", "fresher"), ("senior", "staff", "lead")

    # A wildcard keyword is dropped; a narrow one is not.
    wide = [("software engineer x", 3)] * 300 + [("salesforce developer", 25)] * 20
    fixes, _ = check_role_keywords(
        dict(plain, role_keywords=["software engineer", "salesforce developer"]),
        wide, hard, soft)
    assert fixes and fixes[0]["removed"] == ["software engineer"], fixes

    # A keyword carrying a hard_drop word buys rows config deletes.
    fixes, _ = check_role_keywords(
        dict(plain, role_keywords=["intern developer"]), wide, hard, soft)
    assert any("hard_drop" in f["why"] for f in fixes), fixes

    # A penalty on the person's own skill is dropped.
    fixes, _ = check_penalties(dict(
        plain, penalty_terms=[{"term": "apex", "weight": 6}],
        skill_weights=[{"term": "apex", "weight": 5}]))
    assert fixes[0]["removed"] == ["apex"], fixes

    # title_exclude that would delete the person's own target roles.
    fixes, _ = check_title_exclude(dict(
        plain, role_keywords=["salesforce developer"],
        title_exclude=["salesforce"]), hard, soft)
    assert fixes[0]["removed"] == ["salesforce"], fixes
    # ...and one that is a genuinely different career survives.
    fixes, _ = check_title_exclude(dict(
        plain, role_keywords=["salesforce developer"],
        title_exclude=["data engineer"]), hard, soft)
    assert not fixes, fixes

    # A missing seniority-free stem is ADDED, never the hint removed.
    fixes, _ = check_title_hints(
        dict(plain, title_hints=["senior developer"]), wide, hard, soft)
    assert fixes[0]["action"] == "added" and fixes[0]["added"] == ["developer"]

    # A domain title term that reaches the band less often than the market.
    fixes, _ = check_domain(
        dict(plain, domain_title_terms=["software engineer"]),
        market.titles, hard)
    assert fixes and "software engineer" in fixes[0]["removed"], fixes
    # A bonus with an empty half can never fire.
    fixes, _ = check_domain(dict(plain, domain_bonus=6, domain_half_a=["x"]),
                            market.titles, hard)
    assert any(f["field"] == "domain_bonus" and f["to"] == 0 for f in fixes)

    # validate() applies every correction and reports the decision.
    out = validate(dict(plain, role_keywords=["software engineer",
                                              "salesforce developer"]),
                   wide, hard, soft)
    assert out["decision"] == "corrected"
    assert out["result"]["role_keywords"] == ["salesforce developer"]

    # --- the orphan pass --------------------------------------------------
    # A mobile engineer who also knows apex: apex is orphaned by the React
    # keywords, and the anchored title comes back.
    mixed = {"react native", "redux", "javascript", "apex", "soql", "lwc",
             "mulesoft", "esb", "workflow builder"}
    base = ["react native developer"]
    got = orphans(mixed, base, market.rows, market.vocab)
    assert any(s == "apex" for s, _n, _e, _sh in got), got
    picked = select_detail(mixed, base, market.rows, market.index,
                           market.total, market.seniority, market.vocab)
    assert picked and picked[0][0] == "salesforce developer", picked
    assert picked[0][1] in mixed

    # SKILL_LIFT is the sole reason a generic title is refused: apex's
    # market lift for "salesforce developer" clears 10x.
    lift = skill_lift("salesforce developer", "apex", market.rows,
                      market.vocab, market.total)
    assert lift is not None and lift >= SKILL_LIFT, lift
    # ...while a title whose listings are no more apex-y than the market
    # would not. "software engineer" names apex never.
    assert skill_lift("software engineer", "apex", market.rows,
                      market.vocab, market.total) == 0.0

    # DECOY 1: a rare skill whose title has only 15 rows. Every other gate
    # passes; MIN_ROWS is the sole reason it is refused.
    assert market.vocab["mulesoft"] == 15
    assert skill_lift("integration consultant", "mulesoft", market.rows,
                      market.vocab, market.total) >= SKILL_LIFT
    assert anchor_evidence("integration consultant", "mulesoft", market.rows,
                           market.vocab, market.total) >= ANCHOR_EVIDENCE
    assert "integration consultant" in candidates_for_skill(
        "mulesoft", market.rows, market.index, market.total, market.seniority,
        vocab=market.vocab)
    assert not worth_it("integration consultant", market.rows, mixed,
                        "mulesoft", market.vocab, market.total)[0]
    assert "integration consultant" not in [t for t, _s, _e in picked], picked

    # DECOY 2: one employer's internal stack. ORPHAN_MIN_COMPANIES is the
    # sole reason it never becomes a keyword — it is frequent, its title
    # has a huge skill lift, and it clears the row count.
    assert market.vocab["workflow builder"] == 30
    assert not any(s == "workflow builder"
                   for s, _n, _e, _sh in orphans(mixed, base, market.rows,
                                                 market.vocab)), "decoy 2"
    assert "workflow automation engineer" not in [t for t, _s, _e in picked]

    # A skill the base keywords ALREADY buy rows for is not an orphan.
    # redux is named by half the rows "react native developer" draws, and
    # REPRESENTED is the only gate that excludes it.
    assert represented("redux", base, market.rows) == 0.5
    assert market.vocab["redux"] == 120 >= ORPHAN_MIN_LISTINGS
    assert not any(s == "redux" for s, _n, _e, _sh in
                   orphans(mixed, base, market.rows, market.vocab)), "redux"

    # The cap is checked before appending, so cap=0 adds nothing.
    assert select(mixed, base, market.rows, market.index, market.total,
                  market.seniority, market.vocab, cap=0) == []

    # --- ranking ----------------------------------------------------------
    order = rank(["salesforce developer"],
                 [("react native developer", "react native", 2.9)],
                 ["business development representative", "salesforce developer"])
    assert [k for k, _t, _w in order] == [
        "salesforce developer", "react native developer",
        "business development representative"], order
    # A keyword reachable two ways is bought once, at its best tier.
    assert order[0][1] == 1
    assert "held" in order[0][2] and "anchored" in order[1][2]

    # --- the whole derivation ---------------------------------------------
    fields = fields_for(person, market)
    assert fields["role_keywords"][0] == "salesforce developer", fields
    assert fields["skills"] == ["apex", "lwc", "soql"]
    assert fields["title_hints"], "the free-source gate must not be empty"
    # Held titles rank ahead of anything the corpus offered.
    assert fields["ranking"][0][1] == 1

    # --- budget reach ------------------------------------------------------
    # The failure this exists for: the right specialist keyword generated
    # and ranked beyond what the budget buys. rank() puts every held title
    # first, so a generalist's two generic titles take the prefix.
    held2 = ["software developer", "solutions developer"]
    cands = ["software developer", "solutions developer",
             "react native developer", "salesforce developer"]
    market2 = Market(rows=rows + [
        ("software developer", 3, frozenset({"javascript"}), f"gen{i}")
        for i in range(200)] + [
        ("solutions developer", 3, frozenset({"javascript"}), f"sol{i % 9}")
        for i in range(40)], seniority=("senior",))
    mixed2 = {"react native", "redux", "javascript", "apex", "soql", "lwc"}
    got = budget_order(cands, held2, market2.rows, mixed2,
                       market2.vocab, market2.total)
    # One held title leads; the second no longer takes a slot ahead of
    # the searches that buy evidence nothing else does.
    assert got[0] in held2, got
    assert sum(1 for k in got[:3] if k in held2) == 1, got
    assert "salesforce developer" in got[:3], got
    assert set(got) == set(cands), "reordering must not drop a candidate"

    # A title the market barely posts waits behind every one it does,
    # however pure its skill mix — a search returning nothing costs the
    # same as one returning ten.
    thin_market = Market(rows=market2.rows + [
        ("niche apex architect", 30, frozenset({"apex"}), "solo")] * 4,
        seniority=("senior",))
    got = budget_order(cands + ["niche apex architect"], held2,
                       thin_market.rows, mixed2, thin_market.vocab,
                       thin_market.total)
    assert got[-1] == "niche apex architect", got

    # Shares are shrunk toward the market, so a tiny title cannot out-bid
    # a large one on the same skills by small-sample noise.
    big, _l, _c = wanted_skills("react native developer", market.rows,
                                mixed, market.vocab, market.total)
    assert big["react native"] < 1.0, "an unshrunk share would be 1.0"

    # fields_for still returns every keyword, and the held title leads
    # when the market supports it.
    fields2 = fields_for(person, market)
    assert set(fields2["role_keywords"]) == set(fields["role_keywords"])

    # buys() reports what a keyword set costs and how relevant it is.
    got = buys(["salesforce developer"], market.rows, sf)
    assert got["listings"] == 60 and got["relevance"] == 1.0
    assert got["spend"] > 0

    print("local_search demo ok")


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        demo()
    else:
        print(__doc__)
