"""Did the construct verb actually act on this artefact?

V3 Fix A. `role_evidence._governed_hits` records development evidence when a
CONSTRUCT verb and an engineering ARTEFACT appear anywhere in the same governed
clause. It never asks whether the artefact is what the verb acted *on*, so

    "Authored FSDs for the Supplier Warranty Recovery module"

is read as `authored` + `module` and becomes development evidence. What was
authored is a specification document; the module is the complement of "for".
On a real résumé this promoted a Business Analyst to `software_engineering` and
`ml_engineering`, and through Step 4 put twenty-eight engineering titles into
their free-source title gate.

THE RULE, and it is one sentence:

    a verb governs an artefact only when the words between them form a single
    noun phrase -- determiners and modifiers -- with no preposition and no
    clause break.

A preposition between the two means the artefact sits inside a prepositional
phrase, which is a different slot from the verb's object. A comma, colon or
semicolon means a different clause entirely. Everything else is kept.

Deliberately NOT rules, because each would cost a true positive:

    "and" / "or"     "built reports and dashboards" -- a coordinated object is
                     still the object
    distance         "implemented a highly available payment service" is fine
                     however many adjectives intervene
    the preposition
    AFTER the
    artefact         "built an API for dealer onboarding" -- what follows the
                     object says nothing about attachment

Both directions are tried before a hit is rejected: `built the service` and
`the service was rebuilt` are both attachment. A hit is dropped only when
neither the nearest verb before nor the nearest verb after attaches.

No model, no parser, no new dependency. Off unless SWEEP_ROLE_ATTACHMENT_GUARD
is set; with it absent `role_evidence` runs its original code path.
"""
import os
import re

FLAG = "SWEEP_ROLE_ATTACHMENT_GUARD"

# Prepositions that open a new phrase. "to" is included: it introduces both a
# prepositional phrase ("migrated to AWS") and an infinitive ("to give business
# teams visibility"), and in either case what follows is not the verb's object.
_PREPOSITION = re.compile(
    r"\b(?:for|of|with|within|in|into|on|onto|to|across|from|by|via|through|"
    r"over|under|between|among|against|about|around|per|alongside|besides|"
    r"beyond|during|toward|towards|upon)\b", re.I)

# A clause break. The artefact is in a different clause from the verb.
_BREAK = re.compile(r"[,:;()\[\]–—]|\.(?=\s|$)")


def enabled():
    """Read per call, so a rollback lands on the next request."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def attaches(text, verb_end, artefact_start):
    """Do the words between a verb and an artefact form one noun phrase?"""
    between = text[verb_end:artefact_start]
    if _PREPOSITION.search(between):
        return False, "a preposition puts the artefact in a different phrase"
    if _BREAK.search(between):
        return False, "a clause break separates them"
    return True, "the artefact is the verb's object"


def governs(text, lo, hi, artefact_span, verb_rx):
    """(ok, reason) -- does any construct verb in this clause act on the artefact?

    The NEAREST verb on each side is the one that could govern; a verb further
    away is separated from the artefact by the nearer one's own clause.
    """
    a_start, a_end = artefact_span
    before = [m for m in verb_rx.finditer(text, lo, a_start)]
    if before:
        ok, why = attaches(text, before[-1].end(), a_start)
        if ok:
            return True, f"'{before[-1].group(0)}' -> {why}"
    after = verb_rx.search(text, a_end, hi)
    if after:
        ok, why = attaches(text, a_end, after.start())
        if ok:
            return True, f"'{after.group(0)}' -> {why}"
    if not before and not after:
        # No construct verb at all: not this guard's decision to make.
        return True, "no construct verb in the clause"
    nearest = before[-1].group(0) if before else after.group(0)
    return False, f"'{nearest}' does not act on this artefact"


def demo():
    verb = re.compile(r"\b(?:built|build|authored|author|developed|develop|"
                      r"implemented|implement|engineered|engineer|wrote)\b", re.I)

    def check(s, artefact):
        low = s.lower()
        at = low.index(artefact)
        return governs(low, 0, len(low), (at, at + len(artefact)), verb)

    # False attachment -- the artefact is not what was made.
    assert not check("authored fsds for the supplier warranty recovery module",
                     "module")[0]
    assert not check("authored fsds across multiple dms modules", "modules")[0]
    assert not check("built custom reports and dashboards to give business "
                     "teams visibility on sales and service performance",
                     "service")[0]
    assert not check("java engineer with 8 years on spring boot microservices",
                     "microservices")[0]

    # True attachment -- the artefact IS what was made.
    assert check("built an api for dealer onboarding", "api")[0]
    assert check("developed the customer portal platform", "platform")[0]
    assert check("implemented microservices for payment processing",
                 "microservices")[0]
    assert check("engineered highly available payment services", "services")[0]
    assert check("built reports and dashboards", "dashboards")[0]
    assert check("wrote the integration", "integration")[0]
    # Verb after the artefact.
    assert check("the payment service was rebuilt", "service")[1]
    print("attachment_guard demo ok")


if __name__ == "__main__":
    demo()
