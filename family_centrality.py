"""Performing an activity is not the same as being in a profession.

V3 Fix B. `title_gate._families` hands a family's ENTIRE board vocabulary to
any family with strong work-mode evidence. On a Business Analyst's résumé that
meant onboarding dealers bought the whole recruiting vocabulary, configuring
users and roles bought `infrastructure`, and reports and dashboards bought
`data analyst` -- families whose evidence is perfectly real and whose
professions the person is not in.

    CORE       named by a grounded held title, or matched by a corroborated
               target_field
               -> the family's full FAMILY_TITLES vocabulary, as today

    PERIPHERAL strong work-mode evidence and nothing more
               -> only the titles the candidate's own evidence ALREADY NAMES:
                  held-title fragments, corpus title hints, final queries

Provenance is one-directional and that is the whole safety property. The
evidence text is built from candidate sources only -- never from FAMILY_TITLES
-- and a peripheral family may contribute a fragment only if that exact
fragment already appears in it. A family vocabulary can therefore never
manufacture the evidence that would readmit the family.

So `salesforce administrator` survives for a Salesforce BA, because it is one
of their own queries; `infrastructure` does not, because nothing in their
document says it, even though both currently live in `it_administration`.

Off unless SWEEP_FAMILY_CENTRALITY_GATE is set. It is deliberately NOT part of
SWEEP_CANDIDATE_TITLE_GATE: Fix B refines that gate and has to stay
independently reversible.
"""
import os

FLAG = "SWEEP_FAMILY_CENTRALITY_GATE"


def enabled():
    """Read per call, so a rollback lands on the next request."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def target_families(target_field, family_titles, normalize):
    """Families the stated target names, by WORDS.

    Deliberately not `role_evidence.family_of`: `target_field` holds a
    description -- "business analysis", "software engineering" -- and
    `family_of` is built for job titles, so it returns None for almost every
    real value. Measured in the Fix B report; this is that method, unchanged.
    Containment is tested in both directions, because the target may be
    narrower ("salesforce administration") or broader ("analysis") than the
    fragment.
    """
    stated = normalize(target_field or "")
    if not stated:
        return set()
    return {family for family, fragments in family_titles.items()
            if any(f in stated or stated in f for f in fragments)}


def evidence_text(held, own_hints, queries, normalize):
    """Everything the CANDIDATE's own document and search already name.

    FAMILY_TITLES is never a source here. That is what keeps the expansion
    from feeding itself.
    """
    parts = ([normalize(h) for h in (own_hints or ())]
             + [normalize(q) for q in (queries or ())]
             + list(held or ()))
    return " | ".join(p for p in parts if p).lower()


def families(role, signals, held, own_hints, queries, family_titles, normalize):
    """(fragments, core_families, record) -- Fix B's replacement for _families.

    `core_families` is what the caller uses to filter the candidate's own
    corpus hints, exactly as the strong-family set did before. A peripheral
    family does not widen that filter; it only re-admits the specific titles
    the evidence already carries.
    """
    role = role or {}
    supports = role.get("supports") or {}
    by_title = set(role.get("title_families") or {})
    strong = {f for f, strength in supports.items() if strength == "strong"}
    stated_family = (role.get("stated_target") or {}).get("family")
    by_target = target_families((signals or {}).get("target_field"),
                                family_titles, normalize)

    def is_core(family):
        if family in by_title:
            return True
        return family in strong and (family == stated_family
                                     or family in by_target)

    considered = sorted(strong | by_title)
    core = [f for f in considered if is_core(f)]
    peripheral = [f for f in considered if f not in core]

    out = []
    for family in core:
        for fragment in family_titles.get(family, ()):
            if fragment not in out:
                out.append(fragment)

    named = evidence_text(held, own_hints, queries, normalize)
    recovered = []
    for family in peripheral:
        for fragment in family_titles.get(family, ()):
            if fragment in named and fragment not in out:
                out.append(fragment)
                recovered.append(fragment)

    record = {"core": core, "peripheral": peripheral,
              "core_by_held_title": sorted(f for f in core if f in by_title),
              "core_by_target": sorted(f for f in core if f not in by_title),
              "peripheral_titles_kept": recovered}
    return out, core, record


def demo():
    # Fragments as the real FAMILY_TITLES writes them, including the
    # description-shaped "business analysis" the target path needs.
    FT = {"business_analysis": ("business analyst", "business systems analyst",
                                "business analysis"),
          "it_administration": ("salesforce administrator", "infrastructure",
                                "systems engineer"),
          "support": ("technical support", "support engineer"),
          "product": ("product manager", "product owner")}
    norm = lambda s: " ".join(str(s or "").lower().split())

    role = {"supports": {"business_analysis": "strong",
                         "it_administration": "strong",
                         "support": "strong"},
            "title_families": {}, "stated_target": {"family": None}}
    sig = {"target_field": "business analysis"}
    frags, core, rec = families(role, sig, [], ["salesforce administrator"],
                                ["business analyst"], FT, norm)

    # business_analysis is CORE by target words -> full vocabulary
    assert "business_analysis" in core
    assert "business systems analyst" in frags
    # it_administration is PERIPHERAL -> only the title the evidence names
    assert "it_administration" not in core
    assert "salesforce administrator" in frags
    assert "infrastructure" not in frags, frags
    assert "systems engineer" not in frags
    # support is PERIPHERAL with nothing named -> contributes nothing
    assert "technical support" not in frags and "support engineer" not in frags
    # a held title makes a family core even with no target
    role2 = dict(role, title_families={"product": ["product manager"]})
    _f2, core2, _r2 = families(role2, {}, ["product manager"], [], [], FT, norm)
    assert "product" in core2
    print("family_centrality demo ok")


if __name__ == "__main__":
    demo()
