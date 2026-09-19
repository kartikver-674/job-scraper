"""How far a semantic cue reaches. Not what the cue MEANS.

V3 Step 8, fixing audit defect V3-C16. `skill_evidence.classify` already tells
USED from PLANNED, LEARNING and NEGATED, and those definitions are untouched
here. What was wrong is scope: a cue attached to one noun phrase governed every
concept that merely shared punctuation with it.

The measured damage was not the sentence the defect is usually told with. It
was the skills line:

    Scrum, Agile, Kanban, Backlog Refinement, Sprint Planning, Retrospectives

"Sprint Planning" is a skill this person has. The word "Planning" inside it was
read as an intent cue governing the whole line, so Scrum, Kanban, Jira and the
rest came back PLANNED. PLANNED is not in `CLAIMED`, so those skills lost their
evidence entirely and with it their tier. Fifty-three of the fifty-six PLANNED
occurrences on the development set were this.

Three rules, in the order they are asked, and every one of them can only ever
NARROW a window:

  1. a cue never reaches past `_governed` -- the clause segmentation the
     evidence-depth layer has always used, where a break ends a verb's reach
     only if what follows STARTS A NEW PREDICATE. Reused, not reinvented.
  2. a cue separated from the concept by a comma must LEAD the clause. The
     "Planning" in "... Sprint Planning, Retrospectives" is one list item among
     siblings and governs nothing but itself.
  3. a cue that is immediately followed by a comma ENDS its own item rather
     than introducing a list, so "Demand Planning, Forecasting" names two
     skills and does not plan forecasting; and a lead-in crosses a comma only
     into a coordinate list, one closed by and/or.

Coordination is deliberately preserved. "Learning React, TypeScript and
Next.js" still marks all three LEARNING, and "did not use Java or Kotlin" still
negates both: a fix that stopped leakage by cutting every comma would score
better on leaks and be wrong.

Widening is out of mandate. Bounding PLANNED by `_governed` alone would WIDEN
it forward, because its existing forward bound is already a bare clause break;
on the development set that turned 34 MENTIONED occurrences into PLANNED. Every
window here is an intersection with what the layer already did.

Separately switchable, and separately measured, because it is cue DETECTION
rather than scope: `_NEGATED` recognises "did not use" but not "did not write",
so "Did not write Apex" was classified USED -- the verb inside the denial was
read as evidence of doing the thing denied. See NEGATION_VERBS.
"""
import functools
import os
import re

FLAG = "SWEEP_SEMANTIC_SCOPE"

# Sub-rule 4, switchable on its own. This is cue detection, not scope, and it
# is the one change here that alters WHICH strings negate. Step 8's mandate is
# scope, so review can refuse this without losing the rest:
#     SWEEP_SEMANTIC_SCOPE_NEGATION_VERBS=0
NEGATION_VERBS = re.compile(
    r"\bdid\s+not\s+(?:write|wrote|writing|build|built|building|develop|"
    r"developed|developing|code|coded|coding|configure|configured|implement|"
    r"implemented|touch|touched|work|worked)\b", re.I)

_ITEM_END = re.compile(r"\s*[,;]")
_COORD = re.compile(r"\b(?:and|or)\b", re.I)


def enabled():
    """Read per call, so a rollback lands on the next request."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def negation_verbs():
    """Sub-rule 4 is on by default WHEN Step 8 is on, and off on its own flag."""
    return os.environ.get(FLAG + "_NEGATION_VERBS", "1").strip().lower() \
        in ("1", "true", "yes", "on")


def narrow(current, governed):
    """A window may only ever shrink. Never widen a cue's reach."""
    lo, hi = current
    g_lo, g_hi = governed
    return max(lo, g_lo), min(hi, g_hi)


def governs(text, lo, hi, cue_span, start, end):
    """May the cue matched at `cue_span` govern the concept at [start, end)?

    Everything here turns on one question: are the cue and the concept in the
    SAME list item, or in different ones? Both lookaheads stop at `hi`, the
    cue family's own window -- a conjunction beyond it belongs to a different
    predicate and says nothing about this list.
    """
    cue_s, cue_e = cue_span
    between = text[cue_e:start] if cue_e <= start else text[end:cue_s]
    if "," not in between:
        return True, "same clause item"
    if "," in text[lo:cue_s]:
        return False, "cue sits in a sibling list item"
    if _ITEM_END.match(text, cue_e, hi):
        return False, "cue ends its own list item rather than introducing one"
    if not _COORD.search(text, min(cue_e, end), hi):
        return False, "list states no coordination (no and/or)"
    return True, "lead-in governing a coordinate list"


def first_governing(text, rx, lo, hi, start, end):
    """(match, reason) for the first cue in the window that reaches here."""
    for m in list(rx.finditer(text, lo, start)) + list(rx.finditer(text, end, hi)):
        ok, why = governs(text, lo, hi, m.span(), start, end)
        if ok:
            return m, why
    return None, ""


@functools.lru_cache(maxsize=4)
def _combined(pattern):
    return re.compile(f"(?:{pattern})|(?:{NEGATION_VERBS.pattern})", re.I)


def negated(base):
    """`base`, plus the denial verbs résumés use that it does not list."""
    return _combined(base.pattern) if negation_verbs() else base


def explain(record):
    """§16. Span, concept, cue, boundary, verdict. No score."""
    out = []
    for row in record.get("decisions") or ():
        out.append(
            f"  concept     : {row.get('concept', '(by span)')}\n"
            f"  span        : {row['span']}\n"
            f"  cue         : {row['cue']!r} ({row['cue_family']}) "
            f"at {row['cue_position']}\n"
            f"  scope       : {row['scope']!r}\n"
            f"  coordination: {row['reason']}\n"
            f"  verdict     : {row.get('old', '?')} -> "
            f"{row.get('new', '?')}")
    return "\n".join(out)


def demo():
    lead = "learning react, typescript and next.js"
    sib = "scrum, agile, kanban, backlog refinement, sprint planning, jira"
    first = "demand planning, forecasting, inventory management, excel"
    nocoord = "planned api migration, react, node, postgresql"

    # A lead-in governs a coordinate list.
    at = lead.index("next.js")
    m = re.search(r"learning", lead)
    assert governs(lead, 0, len(lead), m.span(), at, at + 7)[0]

    # A cue in a SIBLING item governs nothing but itself.
    at = sib.index("jira")
    m = re.search(r"planning", sib)
    ok, why = governs(sib, 0, len(sib), m.span(), at, at + 4)
    assert not ok and "sibling" in why, why

    # A cue that ends the FIRST item is an item, not a lead-in.
    at = first.index("excel")
    m = re.search(r"planning", first)
    ok, why = governs(first, 0, len(first), m.span(), at, at + 5)
    assert not ok and "ends its own" in why, why

    # A lead-in still needs a coordinate list to cross a comma.
    at = nocoord.index("postgresql")
    m = re.search(r"planned", nocoord)
    ok, why = governs(nocoord, 0, len(nocoord), m.span(), at, at + 10)
    assert not ok and "coordination" in why, why
    # ... and governs its own item without one.
    at = nocoord.index("api")
    assert governs(nocoord, 0, len(nocoord), m.span(), at, at + 3)[0]

    # A window only ever shrinks.
    assert narrow((0, 100), (10, 40)) == (10, 40)
    assert narrow((10, 40), (0, 100)) == (10, 40)
    print("semantic_scope demo ok")


if __name__ == "__main__":
    demo()
