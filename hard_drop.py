"""A globally undesirable title term is not undesirable for everyone.

`config.SCORING["hard_drop_terms"]` contains manager, architect, director,
head of, vp and chief, and `soft_drop_terms` adds lead. Those words are stripped
from every title everywhere, and a title reduced below two words is discarded,
so an Engineering Manager's own job title produces no search at all:

    from_resume({"title": "Engineering Manager"})   -> []
    from_resume({"title": "Solutions Architect"})   -> []
    from_resume({"title": "Technical Lead"})        -> []

The words are doing a real job — an individual contributor should not be sent
Director postings — but they are applied without reference to the person. This
restores a dropped query when, and only when, the candidate has grounded
evidence for that exact role.

    hard_drop.restore(profile_data, market)  ->  (queries, record)

Two sources of exemption, narrowest first, and nothing else grants one.

**A grounded held title.** The title the person actually held is restored
verbatim. Not a family, not a related title, not a promotion: an Engineering
Manager gets Engineering Manager back, and a Software Engineer gets nothing,
because family-level support would be exactly the upward inflation this must
not create.

**A stated target plus corroborating work evidence.** `target_field` is
ungrounded and can never bypass a drop on its own. Paired with STRONG evidence
for the work mode the role implies — management work for a manager, development
work for an architect — it is a career transition the system already has
evidence for, and refusing it would make that transition unsearchable.

Restored queries still face the economic guard the validator applies: a term
matching a quarter of the market is the catalogue, not a search, and stays
dropped however well grounded it is.

They do NOT have to appear in the scraped corpus. An earlier revision required
that and refused "project manager" and "digital marketing manager" because the
corpus held no such rows — which it does not, because the corpus is the history
of searches that were themselves software-shaped. Requiring corpus presence
makes the bias self-sealing. `from_resume` is documented as the zero-dependency
floor that works with no corpus at all, and a person's own job title is held to
the same standard here.

Nothing here modifies Step 3's role record or Step 4's title gate. Both are read.
"""
import os
import re

FLAG = "SWEEP_CANDIDATE_HARD_DROP"

# The same ceiling local_search.validate applies. A keyword drawing more than
# this share of the corpus buys the catalogue and is paid for per row.
WILDCARD_SHARE = 0.25

# The configured drop list is three different things wearing one name, and only
# the first is a role somebody can hold.
#
#   ROLE   an occupation. Exemptible, because a person can be one.
#   LEVEL  a rank within an occupation. Never exemptible here: restoring
#          "principal" would be a seniority claim, not a role claim.
#   ENTRY  the too-junior block. A different concern entirely, and nobody needs
#          their intern postings restored.
ROLE_TERMS = {
    "manager": "management",
    "architect": "development",
    "director": "management",
    "head of": "management",
    "vp": "management",
    "chief": "management",
    "lead": "management",
}
LEVEL_TERMS = frozenset({"principal", "staff", "senior", "sr"})
ENTRY_TERMS = frozenset({
    "intern", "internship", "trainee", "fresher", "apprentice", "co-op",
    "new grad", "graduate", "junior", "jr"})

_WORD = re.compile(r"[a-z0-9+#.]+")


def enabled():
    """Read per call, so a rollback lands on the next request."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "").lower().strip())


def dropped_role_terms(text):
    """Which ROLE-carrying drop terms this title contains."""
    low = normalize(text)
    words = set(_WORD.findall(low))
    return sorted(term for term in ROLE_TERMS
                  if (term in words if " " not in term else term in low))


def _share(term, titles):
    """Fraction of the corpus a keyword would match. The catalogue guard."""
    if not titles:
        return 0.0
    low = normalize(term)
    return sum(1 for title, _s in titles if low in title) / len(titles)


def _strong(role, mode):
    modes = (role or {}).get("work_modes") or {}
    return (modes.get(mode) or {}).get("strength") == "strong"


def _held_titles(signals):
    out = []
    for group in ("employment_titles", "titles"):
        for title in (signals.get(group) or ()):
            value = normalize(title)
            if value and value not in out:
                out.append(value)
    return out


def restore(data, market=None):
    """(queries to add back, record). Never removes anything."""
    data = data or {}
    signals = data.get("role_signals") or {}
    role = data.get("role_evidence")
    have = {normalize(q) for q in (data.get("role_keywords") or ())}
    titles = list(getattr(market, "titles", None) or ())

    decisions, restored = [], []

    def consider(candidate, source, corroboration):
        value = normalize(candidate)
        terms = dropped_role_terms(value)
        row = {"query": value, "matched_terms": terms, "source": source,
               "evidence": corroboration, "granted": False, "reason": ""}
        if not terms:
            row["reason"] = "carries no role-carrying drop term; nothing to exempt"
        elif value in have:
            row["reason"] = "already present; the drop did not remove it"
        elif len(value.split()) < 2:
            row["reason"] = ("one word is a fragment, not a role, and buys the "
                             "catalogue")
        elif titles and _share(value, titles) > WILDCARD_SHARE:
            row["reason"] = (f"matches {_share(value, titles):.0%} of the corpus; "
                             f"a search that buys the catalogue stays dropped")
        else:
            row["granted"] = True
            row["reason"] = f"grounded by {corroboration}"
            restored.append(value)
        decisions.append(row)

    # 1. A title the person actually held. The narrowest possible evidence,
    #    and the only one that needs no corroboration.
    for title in _held_titles(signals):
        if dropped_role_terms(title):
            consider(title, "held_title", "a grounded held title")

    # 2. A stated target, which is ungrounded, plus STRONG evidence for the work
    #    the role implies. Never the target alone.
    target = normalize(signals.get("target_field"))
    for term in dropped_role_terms(target):
        mode = ROLE_TERMS[term]
        if _strong(role, mode):
            consider(target, "stated_target",
                     f"a stated target corroborated by strong {mode} evidence")
        else:
            decisions.append({
                "query": target, "matched_terms": [term],
                "source": "stated_target", "evidence": f"no strong {mode} evidence",
                "granted": False,
                "reason": ("target_field is ungrounded and cannot bypass a hard "
                           "drop without corroborating work evidence")})

    return restored, {"restored": restored, "decisions": decisions,
                      "role_terms": sorted(ROLE_TERMS),
                      "level_terms_never_exempted": sorted(LEVEL_TERMS),
                      "entry_terms_never_exempted": sorted(ENTRY_TERMS)}


def explain(record):
    """Human-readable, one line per decision. §11, and no score anywhere."""
    out = []
    for row in record["decisions"]:
        out.append(
            f"  {row['query']!r}\n"
            f"    matched drop term(s): {', '.join(row['matched_terms']) or '-'}\n"
            f"    candidate evidence  : {row['evidence']}\n"
            f"    decision            : {'keep' if row['granted'] else 'drop'}\n"
            f"    reason              : {row['reason']}")
    return "\n".join(out)


class _Titles:
    """Minimal stand-in for a Market in the self-check below."""

    def __init__(self, titles):
        self.titles = titles


def demo():
    corpus = ([("engineering manager", 5)] * 30 + [("solutions architect", 5)] * 20
              + [("software engineer", 5)] * 40 + [("program manager", 5)] * 20
              + [("technical lead", 5)] * 15 + [("director of engineering", 5)] * 10)
    market = _Titles(corpus)

    def person(held=(), target="", modes=None, queries=()):
        return {"role_keywords": list(queries),
                "role_signals": {"employment_titles": list(held), "titles": [],
                                 "target_field": target},
                "role_evidence": {"work_modes": {m: {"strength": s}
                                                 for m, s in (modes or {}).items()}}}

    got, _r = restore(person(held=["Engineering Manager"]), market)
    assert got == ["engineering manager"], got
    got, _r = restore(person(held=["Software Engineer"]), market)
    assert got == [], got
    got, _r = restore(person(held=["Solutions Architect"]), market)
    assert got == ["solutions architect"], got
    got, _r = restore(person(held=["Project Coordinator"]), market)
    assert got == [], got
    # target alone is never enough; target plus strong management work is.
    got, _r = restore(person(target="engineering manager"), market)
    assert got == [], got
    got, _r = restore(person(target="engineering manager",
                             modes={"management": "strong"}), market)
    assert got == ["engineering manager"], got
    # a term matching the catalogue stays dropped however well grounded
    flood = _Titles([("engineering manager", 5)] * 90 + [("x", 5)] * 10)
    got, _r = restore(person(held=["Engineering Manager"]), flood)
    assert got == [], got
    # a title the corpus has never seen is still restored: the corpus is the
    # history of software-shaped searches, not the market.
    got, _r = restore(person(held=["Digital Marketing Manager"]), market)
    assert got == ["digital marketing manager"], got
    print("hard_drop demo ok")


if __name__ == "__main__":
    demo()
