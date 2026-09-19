"""A rare skill can suggest a job title. It cannot confer the profession.

The orphan pass exists because a specialist skill nobody is searching for is
inventory the candidate never sees. It finds such a skill, asks the corpus which
job titles are posted alongside it, and adds the best-anchored one as a query.
That is useful and stays.

What it must not do is manufacture an occupation. Traced on the development
matrix, `git` anchors `flutter developer` for a technical programme manager, and
a platform token anchors `salesforce developer` for a business analyst who
coordinates with developers. The chain is circular if left alone:

    skill X  ->  corpus association  ->  role Y  ->  "Y is supported, X proposed it"

    orphan_guard.filter_queries(queries, data, orphans) -> (kept, record)

The break in the circle is that support must come from somewhere the orphan
skill did not: a grounded held title, or Step 3's work-mode evidence, neither of
which reads the corpus association that proposed the title.

Four conditions must ALL hold before anything is rejected, and each one is a
fail-open door:

  1. the query came from the orphan path, not from the résumé or the ranked
     corpus keywords
  2. the frozen classifier recognises its role family — UNKNOWN is not
     UNRELATED, and a profession the sixteen families cannot place is kept
  3. the candidate has meaningful role evidence at all — a thin record judges
     nobody
  4. that family has no independent support

Only a ROLE TITLE is refused. The orphan skill itself keeps every other path it
had; see `skill_survives` for what that does and does not currently reach.

Step 3's role record, Step 4's title gate and Step 5's restorations are all read
or untouched. SKILL_LIFT, the rarity calculation and the corpus statistics are
not consulted here: the question is whether a lifted signal may name a
profession, not how large the lift should be.
"""
import os

import role_evidence          # FROZEN at Step 3. Read, never modified.

FLAG = "SWEEP_ORPHAN_ROLE_GUARD"

# Which evidence counts as independent support for a family.
#
#   "any"     the family appears in the frozen role record at all, or a held
#             title names it. The least aggressive rule that can work.
#   "strong"  the family needs STRONG work-mode evidence, or a held title.
#             Catches a weak mention that the orphan path then amplifies.
#
# Ablated on the development set: the two rules reject exactly the same seven
# titles there, so "any" ships. Preferring the stricter variant when it buys
# nothing measurable would be choosing severity for its own sake.
MODE = os.environ.get("SWEEP_ORPHAN_GUARD_MODE", "any").strip().lower()


def enabled():
    """Read per call, so a rollback lands on the next request."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def supported_families(role, mode=None):
    """Families the candidate has evidence for that the orphan path did not
    supply. Held titles and work modes; never a corpus association."""
    role = role or {}
    mode = (mode or MODE)
    supports = role.get("supports") or {}
    by_title = set(role.get("title_families") or {})
    if mode == "any":
        return set(supports) | by_title
    return {f for f, strength in supports.items() if strength == "strong"} | by_title


def transition_families(role):
    """A family Step 3 already accepted as a corroborated transition."""
    role = role or {}
    target = (role.get("stated_target") or {}).get("family")
    if not target:
        return set()
    needs = set(role_evidence.FAMILIES.get(target, {}).get("needs", ()))
    if needs & set(role.get("transition_modes") or ()):
        return {target}
    return set()


def filter_queries(queries, data, orphan_titles, anchors=None, mode=None):
    """(kept, record). Only orphan-derived role titles can be removed."""
    queries = list(queries or [])
    data = data or {}
    role = data.get("role_evidence")
    orphans = {str(t).strip().lower() for t in (orphan_titles or ())}
    anchors = {str(t).strip().lower(): skill
               for t, skill in (anchors or ())}

    record = {"mode": mode or MODE, "decisions": [], "rejected": [],
              "fail_open": None}
    if not orphans:
        record["fail_open"] = "no orphan-derived title in this query set"
        return queries, record
    if not role:
        record["fail_open"] = "no role record; nothing to judge against"
        return queries, record
    if role.get("thin"):
        record["fail_open"] = ("role evidence is thin; a record that judges "
                               "nobody judges no orphan either")
        return queries, record

    independent = supported_families(role, mode) | transition_families(role)
    if not independent:
        record["fail_open"] = ("no independently supported family; rejecting "
                               "would be absence of evidence, not evidence")
        return queries, record

    kept = []
    for query in queries:
        low = str(query).strip().lower()
        if low not in orphans:
            kept.append(query)
            continue
        family = role_evidence.family_of(query)
        row = {"query": query, "orphan_term": anchors.get(low),
               "proposed_family": family,
               "candidate_support": sorted(independent),
               "accepted": True, "reason": ""}
        if family is None:
            row["reason"] = ("the frozen classifier does not recognise this "
                             "occupation; unknown is not unrelated")
        elif family in independent:
            row["reason"] = f"{family} is independently supported"
        else:
            row["accepted"] = False
            row["reason"] = (
                f"{family} has no support outside the orphan skill itself; "
                f"the candidate's independent evidence is "
                f"{', '.join(sorted(independent))}")
            record["rejected"].append(query)
        record["decisions"].append(row)
        if row["accepted"]:
            kept.append(query)

    if not kept:
        record["fail_open"] = ("every query was orphan-derived and unsupported; "
                               "nothing rejected rather than starve discovery")
        record["rejected"] = []
        return queries, record
    return kept, record


def skill_survives(_skill):
    """Does refusing the ROLE TITLE still leave the skill usable?

    Honest answer for the current architecture: partly. The orphan skill keeps
    every path it already had — it is in `skills`, it is weighted by the scorer,
    and it still reaches `title_hints` through `hints_for` — so refusing the
    title does not delete the skill. What the candidate loses is the one
    corpus-derived SEARCH STRING that skill would have bought.

    There is no separate "search this skill as a concept" channel to fall back
    on: role_keywords are job-title queries by construction. Building one is a
    change to query generation and is out of scope here, so this returns what
    is true rather than pretending otherwise.
    """
    return {"kept_in_skills": True, "kept_in_scoring": True,
            "kept_in_title_hints": True, "kept_as_a_search_query": False}


def explain(record):
    """§18. Every decision, in the résumé's own terms, with no score."""
    out = []
    if record.get("fail_open"):
        out.append(f"  FAIL OPEN: {record['fail_open']}")
    for row in record["decisions"]:
        out.append(
            f"  orphan term        : {row['orphan_term'] or 'unrecorded'}\n"
            f"  proposed title     : {row['query']}\n"
            f"  proposed family    : {row['proposed_family']}\n"
            f"  candidate support  : {', '.join(row['candidate_support']) or 'none'}\n"
            f"  decision           : "
            f"{'accept' if row['accepted'] else 'reject orphan-derived title'}\n"
            f"  reason             : {row['reason']}")
    return "\n".join(out)


def demo():
    def person(supports=None, titles=None, thin=False, transition=()):
        return {"role_evidence": {
            "supports": dict(supports or {}),
            "title_families": dict(titles or {}),
            "transition_modes": list(transition),
            "stated_target": {"family": None},
            "thin": thin}}

    # A delivery manager whose git anchored a mobile title.
    kept, rec = filter_queries(
        ["project manager", "flutter developer"],
        person(supports={"project_delivery": "strong"},
               titles={"project_delivery": ["Programme Manager"]}),
        ["flutter developer"], anchors=[("flutter developer", "git")])
    assert kept == ["project manager"], kept
    assert rec["rejected"] == ["flutter developer"]

    # A mobile engineer whose firebase anchored the same title.
    kept, _r = filter_queries(
        ["mobile developer", "flutter developer"],
        person(supports={"software_engineering": "strong"}),
        ["flutter developer"])
    assert kept == ["mobile developer", "flutter developer"], kept

    # An occupation the sixteen families cannot place is never refused.
    kept, _r = filter_queries(
        ["veterinary nurse", "zzz qqq"],
        person(supports={"support": "strong"}), ["zzz qqq"])
    assert "zzz qqq" in kept

    # Thin evidence judges nobody, and the last query is never taken away.
    kept, rec = filter_queries(["flutter developer"],
                               person(supports={"design": "strong"}, thin=True),
                               ["flutter developer"])
    assert kept == ["flutter developer"] and rec["fail_open"]
    kept, rec = filter_queries(["flutter developer"],
                               person(supports={"design": "strong"}),
                               ["flutter developer"])
    assert kept == ["flutter developer"], "starvation guard did not fire"
    print("orphan_guard demo ok")


if __name__ == "__main__":
    demo()
