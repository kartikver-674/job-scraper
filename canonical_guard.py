"""What was valid as a fragment is not automatically valid as its replacement.

`local_search.canonical()` swaps a ranked n-gram for a real corpus title, because
the n-grams are not titles: "native developer" becomes "react native developer",
which 86 listings are actually called. That is the right transformation and it
stays.

The defect is the boundary. On the corpus path the replacement is revalidated,
because `canonicalise` runs before `validated`. On the ORPHAN path it is not:
`candidates_for_skill` calls `canonical()` and the result reaches
`role_keywords` through `worth_it` and `select_detail` without ever meeting
`validate`. So:

    VALID(fragment)  does not imply  VALID(canonical(fragment))

    canonical_guard.revalidate(queries, trace, ...) -> (kept, record)

**Corpus membership is provenance, not validity.** The title that exposed this
occurs in 27 corpus listings and passes every existing check: its share is
0.1%, it carries no drop term, and `validated()` keeps it. It is still not a
query anybody should send.

Most of the rules below are existing final-query rules re-applied to the final
string. Exactly one is new, and it is new because the trace proved no existing
validator can express it:

    a leading hyphen on a token is a NEGATION OPERATOR

in every major job-board search syntax. "software engineer -python developer"
does not ask for a slightly odd job; it asks for software engineers that are
NOT python, and then a loose word. That is a property of the string as a SEARCH
QUERY, which is exactly the boundary this module guards, and no rule about
corpus rows or economics can see it.

Deliberately not rules. `.net developer`, `c++ developer`, `front-end
developer`, `ui developer (angular)`, `account executive, small business` and
`sr. business systems analyst` are all odd-looking and all fine: punctuation
inside a word, a parenthetical, a comma qualifier and an abbreviation are how
boards write titles. A tidiness rule would have deleted them.

Step 3, Step 4, Step 5 and Step 6 are untouched. Step 5's candidate-aware
exemption is read through its own function rather than reimplemented, so a
grounded Engineering Manager does not lose their title to a rule this module
re-applies.
"""
import os
import re

import hard_drop             # FROZEN at Step 5. Read, never modified.

FLAG = "SWEEP_CANONICAL_REVALIDATION"

# What to do when a replacement fails. Ablated on the development set; see the
# handoff.
#
#   "discard"   drop the query. The fragment behind it was a ranked n-gram that
#               was never valid as a standalone query, so keeping it would be
#               shipping the thing canonical() exists to replace.
#   "fragment"  fall back to the original fragment.
FALLBACK = os.environ.get("SWEEP_CANONICAL_FALLBACK", "discard").strip().lower()

# The same ceiling local_search.validate applies to role_keywords.
WILDCARD_SHARE = 0.25

# THE ONE NEW RULE. A token beginning with -, + or ~, or a bare boolean word,
# is search syntax rather than part of a job title.
_OPERATOR = re.compile(r"(?:^|\s)[-+~](?=\S)|(?:^|\s)(?:OR|AND|NOT)(?:\s|$)")


def enabled():
    """Read per call, so a rollback lands on the next request."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def _share(term, titles):
    if not titles:
        return 0.0
    low = term.strip().lower()
    return sum(1 for title, _s in titles if low in title) / len(titles)


def check(title, titles=(), exempt=(), hard_terms=()):
    """(ok, rule, reason) for one final canonical string."""
    value = str(title or "").strip().lower()
    exempt = {str(t).strip().lower() for t in exempt}

    if not value:
        return False, "degenerate", "the replacement is empty"
    if len(value.split()) < 2:
        return (False, "degenerate",
                "one word is a fragment, not a role, and buys the catalogue")
    if _OPERATOR.search(title):
        return (False, "search_operator",
                "a leading hyphen is a negation operator on every major board, "
                "so this string does not ask for the job it appears to name")
    if titles and _share(value, titles) > WILDCARD_SHARE:
        return (False, "catalogue",
                f"matches {_share(value, titles):.0%} of the market; a search "
                f"that buys the catalogue is paid for per row")

    # The existing hard-drop rule, re-applied to the FINAL string because the
    # orphan path never met it. Role terms route through Step 5 so a grounded
    # title is not taken back by a rule Step 5 already decided.
    present = [t for t in hard_terms
               if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", value)]
    role_terms = set(hard_drop.dropped_role_terms(value))
    level_entry = [t for t in present if t not in role_terms]
    if level_entry:
        return (False, "level_or_entry",
                f"carries {', '.join(sorted(level_entry))}, which config drops "
                f"and which names a rank rather than a role")
    if role_terms and value not in exempt:
        return (False, "role_term_unexempted",
                f"carries {', '.join(sorted(role_terms))} and the candidate has "
                f"no grounded title for it; Step 5 grants no exemption here")
    return True, "accepted", "satisfies the final-query contract"


def revalidate(queries, trace=(), titles=(), exempt=(), hard_terms=(),
               fallback=None):
    """(kept, record). Only canonical REPLACEMENTS are judged."""
    queries = list(queries or [])
    fallback = (fallback or FALLBACK)
    # replacement -> the fragment it replaced. First writer wins, so the
    # earliest fragment that produced a title is the one reported.
    origin = {}
    for fragment, title in (trace or ()):
        if title and str(title).strip().lower() not in origin:
            origin[str(title).strip().lower()] = fragment
    record = {"fallback_policy": fallback, "decisions": [], "rejected": [],
              "recovered": []}

    kept = []
    for query in queries:
        low = str(query).strip().lower()
        if low not in origin:
            kept.append(query)          # not a replacement; not ours to judge
            continue
        ok, rule, reason = check(query, titles, exempt, hard_terms)
        row = {"query": query, "original_fragment": origin[low], "rule": rule,
               "accepted": ok, "reason": reason, "fallback_used": False}
        if ok:
            kept.append(query)
        else:
            record["rejected"].append(query)
            if fallback == "fragment":
                fragment = origin[low]
                good, _r, _why = check(fragment, titles, exempt, hard_terms)
                if good and fragment not in kept:
                    kept.append(fragment)
                    row["fallback_used"] = True
                    record["recovered"].append(fragment)
        record["decisions"].append(row)

    if not kept and queries:
        record["fail_open"] = ("every query was an invalid replacement; "
                               "nothing removed rather than starve discovery")
        record["rejected"], record["recovered"] = [], []
        return queries, record
    return kept, record


def explain(record):
    """§7. Original, replacement, rule, decision, reason. No score."""
    out = []
    if record.get("fail_open"):
        out.append(f"  FAIL OPEN: {record['fail_open']}")
    for row in record["decisions"]:
        out.append(
            f"  original           : {row['original_fragment']}\n"
            f"  canonical          : {row['query']}\n"
            f"  final-query rule   : {row['rule']}\n"
            f"  decision           : "
            f"{'accept' if row['accepted'] else 'reject replacement'}"
            + ("  (fell back to the fragment)" if row["fallback_used"] else "")
            + f"\n  reason             : {row['reason']}")
    return "\n".join(out)


def demo():
    titles = ([("react native developer", 30)] * 40
              + [("software engineer -python developer", 5)] * 27
              + [("engineering manager", 30)] * 20
              + [("principal engineer", 30)] * 15
              + [("backend engineer", 25)] * 60)
    hard = ("principal", "staff", "manager", "architect", "director", "junior")

    # A clean replacement survives.
    ok, _r, _w = check("react native developer", titles, (), hard)
    assert ok
    # The malformed one does not, and the rule names the harm.
    ok, rule, _w = check("software engineer -python developer", titles, (), hard)
    assert not ok and rule == "search_operator"
    # Punctuation inside a word is not an operator.
    for good in (".net developer", "c++ developer", "front-end developer",
                 "ui developer (angular)", "account executive, small business"):
        ok, _r, why = check(good, (), (), hard)
        assert ok, (good, why)
    # A level term is refused; a grounded role term is not.
    ok, rule, _w = check("principal engineer", titles, (), hard)
    assert not ok and rule == "level_or_entry"
    ok, rule, _w = check("engineering manager", titles, (), hard)
    assert not ok and rule == "role_term_unexempted"
    ok, _r, _w = check("engineering manager", titles, ("engineering manager",), hard)
    assert ok, "Step 5's exemption was not respected"
    # One word is a fragment, not a role.
    assert not check("developer", titles, (), hard)[0]

    trace = [("software engineer python", "software engineer -python developer"),
             ("native developer", "react native developer")]
    kept, rec = revalidate(
        ["react native developer", "software engineer -python developer",
         "a query nobody canonicalised"], trace, titles, (), hard)
    assert kept == ["react native developer", "a query nobody canonicalised"], kept
    assert rec["rejected"] == ["software engineer -python developer"]
    assert rec["decisions"][1]["original_fragment"] == "software engineer python"
    print("canonical_guard demo ok")


if __name__ == "__main__":
    demo()
