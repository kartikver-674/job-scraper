"""Which job titles could reasonably belong to THIS candidate.

The free-source gate decides what a company board is even scored on. Today it
asks "is this a title somebody in software might want?", because
`make_profile._title_gate` unions the candidate's own hints with a 79-entry
generic software floor. Measured on the development personas, that means 52% of
people cannot see their own profession and 100% can see somebody else's.

This builds the same kind of artefact — a list of lowercase fragments that
`scraper.is_dev_title` substring-matches — from the candidate's own evidence
instead of from a global floor.

    title_gate.build(profile_data, base_hints=config.ATS_TITLE_HINTS)
        -> (hints, record)

Two complementary paths, because neither alone covers everybody.

**Role-family evidence.** When Step 3's role record says a candidate is
supported in a family, that family's ordinary board vocabulary is admitted. This
is what stops a graphic designer's gate admitting backend engineering.

**Candidate title evidence.** Grounded employment titles and grounded extracted
titles become fragments directly. This is the path that carries teachers,
nurses, machinists and everyone else the sixteen coarse families do not
represent — and it works whether or not Step 3's flag is on, because the role
signals it reads were made unconditional in Step 1.

`target_field` is ungrounded and can only ever WIDEN, and only when something
grounded corroborates it. It can never narrow the gate and never overrides a
held title.

Nothing here decides what to search FOR. It decides what is allowed to be seen.
The gate only ever widens visibility, so the cost of one fragment too many is a
job posting nobody was going to score well anyway, and the cost of one fragment
too few is a job never seen at all. That asymmetry is why the fallbacks below
are generous rather than strict.
"""
import os
import re

import family_centrality      # V3 Fix B. Off unless set.
import role_evidence          # FROZEN at Step 3. Read, never modified.

FLAG = "SWEEP_CANDIDATE_TITLE_GATE"



def enabled():
    """Read per call, so a rollback lands on the next request."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


# Ordinary board vocabulary per frozen role family. Fragments, not titles: they
# are substring-matched, so "data analyst" admits "Senior Data Analyst" and
# "Data Analyst II" without either being listed.
#
# This is not an occupation ontology and must not become one. It exists only to
# turn a family Step 3 already decided into the words boards use for it. A
# profession that is not one of the sixteen is carried by held titles instead.
FAMILY_TITLES = {
    "software_engineering": (
        "software engineer", "software developer", "software development",
        "developer", "programmer", "sde", "backend", "back end", "frontend",
        "front end", "full stack", "fullstack", "web developer",
        "mobile developer", "android", "ios engineer", "devops",
        "site reliability", "platform engineer", "engineering manager",
    ),
    "data_engineering": (
        "data engineer", "data engineering", "etl", "analytics engineer",
        "data platform", "data warehouse", "big data",
    ),
    "ml_engineering": (
        "machine learning", "ml engineer", "ai engineer",
        "artificial intelligence", "deep learning", "nlp engineer",
        "computer vision", "mlops",
    ),
    "data_analytics": (
        # No bare "analytics": it is inside "analytics engineer", which is a
        # data-engineering title, not an analyst one.
        "data analyst", "business intelligence", "bi analyst",
        "reporting analyst", "insights analyst", "data scientist",
        "analytics manager", "analytics lead",
    ),
    "business_analysis": (
        "business analyst", "business systems analyst", "systems analyst",
        "requirements analyst", "process analyst", "business analysis",
    ),
    "functional_consulting": (
        "functional consultant", "implementation consultant",
        "solution consultant", "solutions consultant", "erp consultant",
        "crm consultant", "techno-functional", "application consultant",
        "technical consultant",
    ),
    "it_administration": (
        "system administrator", "systems administrator", "sysadmin",
        "network administrator", "database administrator", "it administrator",
        "it support", "infrastructure", "systems engineer", "administrator",
    ),
    "project_delivery": (
        "project manager", "programme manager", "program manager",
        "delivery manager", "scrum master", "agile coach", "pmo",
        "project coordinator", "release manager", "project lead",
    ),
    "product": (
        "product manager", "product owner", "product management",
        "product analyst",
    ),
    "management": (
        "operations manager", "general manager", "business operations",
        "operations lead", "branch manager", "store manager",
        "department manager", "head of operations",
    ),
    # NOTE: no bare "sales". `scraper.is_dev_title` matches substrings, and
    # "sales" is inside "salesforce", so a bare entry admitted Salesforce
    # Developer to every salesperson. Every fragment below is a whole role.
    "sales": (
        "account executive", "account manager", "sales manager",
        "sales representative", "sales executive", "sales associate",
        "sales development", "business development", "customer success",
        "client success", "inside sales", "partnerships", "renewals",
    ),
    "marketing": (
        "marketing", "seo", "content strategist", "copywriter", "brand",
        "social media", "campaign manager", "communications", "public relations",
    ),
    "finance": (
        "financial analyst", "finance analyst", "accountant", "accounting",
        "fp&a", "controller", "bookkeeper", "auditor", "accounts payable",
        "accounts receivable", "treasury",
    ),
    "hr_recruiting": (
        "recruiter", "recruiting", "talent acquisition", "human resources",
        "hr generalist", "hr business partner", "people operations", "sourcer",
        "payroll", "benefits",
    ),
    "support": (
        "technical support", "customer support", "customer service",
        "help desk", "helpdesk", "service desk", "support specialist",
        "support engineer", "support analyst",
    ),
    "design": (
        "designer", "ux", "ui/ux", "user experience", "product design",
        "graphic design", "visual design", "interaction design",
        "design", "creative",
    ),
}

# Level words that carry no occupational meaning. Removed from a held title
# before it becomes a fragment, so "Senior Graphic Designer" also admits
# "Graphic Designer" and "Junior Graphic Designer".
_LEVEL = re.compile(
    r"\b(senior|snr|sr|junior|jnr|jr|lead|principal|staff|chief|head of|"
    r"entry[- ]?level|mid[- ]?level|trainee|intern|apprentice|associate|"
    r"i{1,3}|iv|v|[0-9]+)\b")
_PUNCT = re.compile(r"[^a-z0-9&/+ -]")
# Words too generic to be a gate fragment on their own: they would admit most
# of a job board. A held title reduced to one of these contributes nothing.
_TOO_BROAD = frozenset((
    "manager", "engineer", "analyst", "specialist", "coordinator", "associate",
    "assistant", "consultant", "director", "executive", "officer", "lead",
    "administrator", "advisor", "generalist", "professional", "technician",
    "representative", "agent", "operator", "clerk", "supervisor", "intern",
    "staff", "worker", "member", "partner", "owner", "head",
))

MIN_FRAGMENT = 4
# A fragment that begins with a function word is a sentence tail, not a role.
_STOPWORD_START = re.compile(r"^(the|a|an|to|of|for|and|at|in|on|with)\b")


def normalize(text):
    out = str(text or "").lower().strip()
    out = re.sub(r"[-_]+", " ", out)          # "Front-End" and "Front End" are one
    out = _PUNCT.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def fragments(title):
    """Gate fragments for one held job title. Deterministic and small.

    The whole title, minus level words, plus its last two words when the title
    is longer than that — so "Salesforce Business Analyst" also admits a
    posting worded plainly as "Business Analyst".
    """
    whole = normalize(title)
    if not whole:
        return []
    stripped = re.sub(r"\s+", " ", _LEVEL.sub(" ", whole)).strip()
    out = []
    for value in (stripped, whole):
        if (value and len(value) >= MIN_FRAGMENT and value not in _TOO_BROAD
                and value not in out):
            out.append(value)
    # Both ends, because a job title's meaning can sit at either. "Salesforce
    # Business Analyst" needs its tail; "Executive Assistant to the CEO" needs
    # its head, and taking only the tail left that person matching "the ceo"
    # and invisible to every Executive Assistant posting on the board.
    words = stripped.split()
    if len(words) > 2:
        for pair in (" ".join(words[:2]), " ".join(words[-2:])):
            if (len(pair) >= MIN_FRAGMENT and pair not in _TOO_BROAD
                    and pair not in out and not _STOPWORD_START.match(pair)):
                out.append(pair)
    return out


def _held(signals):
    """Grounded titles: what the résumé says this person has actually been."""
    out = []
    for group in ("employment_titles", "titles"):
        for title in (signals.get(group) or ()):
            for fragment in fragments(title):
                if fragment not in out:
                    out.append(fragment)
    return out


def _families(role):
    """Board vocabulary for families the candidate is genuinely in.

    STRONG work-mode evidence, or a grounded held title that names the family.
    Weak support is deliberately not enough: a single passing mention is the
    very thing that used to manufacture a developer, and admitting a whole
    family's board vocabulary on it would rebuild that here. This reads
    role_evidence and changes nothing about how it is built.
    """
    role = role or {}
    supports = role.get("supports") or {}
    by_title = set(role.get("title_families") or {})
    admitted = sorted(f for f, strength in supports.items()
                      if strength == "strong" or f in by_title)
    out = []
    for family in admitted:
        for fragment in FAMILY_TITLES.get(family, ()):
            if fragment not in out:
                out.append(fragment)
    return out, admitted


def _target(signals, role, grounded):
    """`target_field`, admitted only when something grounded agrees with it.

    Ungrounded on its own — the model may write anything here — so it may widen
    a gate and may never narrow one. A stated target with no corroboration
    contributes nothing at all.
    """
    stated = normalize(signals.get("target_field"))
    if not stated:
        return [], None
    family = (role or {}).get("stated_target", {}).get("family")
    supported = set((role or {}).get("supports") or {})
    # Corroborated either by the role record supporting that family, or by the
    # words of the target already appearing in a grounded held title.
    by_family = bool(family and family in supported)
    by_title = any(word in " ".join(grounded)
                   for word in stated.split() if len(word) > 3)
    if not (by_family or by_title):
        return [], "uncorroborated, contributed nothing"
    out = [stated] if len(stated) >= MIN_FRAGMENT and stated not in _TOO_BROAD else []
    if by_family:
        for fragment in FAMILY_TITLES.get(family, ()):
            if fragment not in out:
                out.append(fragment)
    return out, ("corroborated by the role record" if by_family
                 else "corroborated by a held title")


def build(data, base_hints=()):
    """(hints, record). The record explains every hint's provenance.

    `data` is the profile dict local_profile.generate produced: it carries
    `title_hints`, `role_signals` and, when Step 3 is on, `role_evidence`.
    `base_hints` is the legacy global floor, used ONLY by the last fallback.
    """
    data = data or {}
    signals = data.get("role_signals") or {}
    role = data.get("role_evidence")
    own_raw = [normalize(h) for h in (data.get("title_hints") or [])]
    own_raw = [h for h in own_raw if h and h not in _TOO_BROAD]

    held = _held(signals)
    # V3 FIX B. Strong work-mode evidence means the candidate DID this kind of
    # work; it does not mean the profession is theirs to search broadly. With
    # the centrality gate on, only a family named by a held title or by the
    # stated target hands over its whole vocabulary, and every other strong
    # family contributes just the titles the candidate's own evidence already
    # names. `_families` below is the untouched Fix-A path.
    centrality = None
    if family_centrality.enabled():
        family_hints, supported, centrality = family_centrality.families(
            role, signals, held, own_raw, data.get("role_keywords") or (),
            FAMILY_TITLES, normalize)
    else:
        family_hints, supported = _families(role)
    # The candidate's own hints are derived from the CORPUS, and the corpus is
    # 63% software, so they carry titles from families this person has no
    # evidence for — "salesforce developer" for a business analyst is exactly
    # the contamination this step exists to stop, arriving by a side door. A
    # hint naming a family the candidate is not admitted in is dropped; one the
    # frozen classifier cannot place is kept, because unknown is not unrelated.
    admitted = set(supported)
    own, own_dropped = [], []
    for hint in own_raw:
        # A bare platform name is not a role. "salesforce" as a gate fragment
        # admits every Salesforce job on the board, developer included, which
        # is the platform-token defect arriving one layer down. The platform
        # vocabulary is Step 3's own, read and not modified.
        if not role_evidence._PLATFORM_RX.sub(" ", hint).strip():
            own_dropped.append(hint)
            continue
        family = role_evidence.family_of(hint)
        if family and family not in admitted:
            own_dropped.append(hint)
        else:
            own.append(hint)
    target_hints, target_note = _target(signals, role, held)

    hints, provenance = [], {}
    for source, values in (("held_titles", held),
                           ("role_families", family_hints),
                           ("candidate_title_hints", own),
                           ("stated_target", target_hints)):
        for value in values:
            if value and value not in hints:
                hints.append(value)
                provenance[value] = source

    record = {
        "candidate_specific": True,
        "fallback": None,
        "supported_families": supported,
        "held_title_fragments": held,
        "target_field_use": target_note,
        "provenance": provenance,
        "own_hints_dropped": own_dropped,
        "global_floor_used": False,
    }
    if centrality is not None:
        record["family_centrality"] = centrality

    # Usable means PRESENT, not plentiful. A teacher whose evidence is three
    # fragments has a small correct gate; counting fragments and calling three
    # "thin" sent exactly that person to the software floor, which is the
    # defect this step exists to remove.
    if held or family_hints:
        record["gate_size"] = len(hints)
        return sorted(hints), record

    if own:
        record["fallback"] = ("no grounded title or family evidence; the "
                              "candidate's own corpus-derived hints alone")
        record["gate_size"] = len(own)
        return sorted(set(own)), record

    # Nothing candidate-specific at all. The legacy floor is no worse than
    # guessing and is what shipped before, but it is never reached silently.
    record["fallback"] = ("no candidate-specific title evidence of any kind; "
                          "fell back to the legacy global floor")
    record["global_floor_used"] = True
    record["candidate_specific"] = False
    out = sorted({normalize(h) for h in base_hints if normalize(h)})
    record["gate_size"] = len(out)
    return out, record


def admits(hints, title):
    """The same substring test scraper.is_dev_title applies."""
    low = str(title or "").lower()
    return any(h in low for h in hints if h)


def explain(title, hints, record, family_of=None):
    """Why one posting would be admitted or rejected. §14, no score anywhere."""
    low = str(title or "").lower()
    matched = sorted(h for h in hints if h and h in low)
    family = family_of(title) if family_of else None
    decision = "admit" if matched else "reject"
    if matched:
        sources = sorted({record["provenance"].get(h, "fallback")
                          for h in matched})
        reason = f"matched {matched[:4]} from {', '.join(sources)}"
    elif record.get("global_floor_used"):
        reason = ("no candidate evidence existed, so the legacy global floor "
                  "was used and this title is outside it")
    else:
        reason = ("candidate-specific role and title evidence does not support "
                  "this title; the global ATS floor is not part of the V3 "
                  "candidate gate")
    return {
        "job_title": title,
        "detected_family": family,
        "candidate_supported_families": record["supported_families"],
        "candidate_title_hints_matched": matched,
        "decision": decision,
        "reason": reason,
        "fallback_used": bool(record.get("fallback")),
    }


def demo():
    """Self-check. The shapes the audit measured, none of them special-cased."""
    designer = {
        "title_hints": ["graphic design", "brand", "adobe"],
        "role_signals": {"employment_titles": ["Senior Graphic Designer"],
                         "titles": ["Graphic Designer"], "target_field": ""},
        "role_evidence": {"supports": {"design": "strong"},
                          "stated_target": {"family": None}},
    }
    hints, record = build(designer, base_hints=["developer", "software engineer"])
    assert admits(hints, "Graphic Designer"), hints
    assert admits(hints, "Senior Brand Designer"), hints
    assert not admits(hints, "Frontend Developer"), hints
    assert not admits(hints, "Backend Engineer"), hints
    assert record["fallback"] is None and not record["global_floor_used"]

    dev = {
        "title_hints": ["apex", "salesforce"],
        "role_signals": {"employment_titles": ["Salesforce Developer"],
                         "titles": ["Salesforce Developer"], "target_field": ""},
        "role_evidence": {"supports": {"software_engineering": "strong"},
                          "stated_target": {"family": None}},
    }
    hints2, _r2 = build(dev, base_hints=["developer"])
    assert admits(hints2, "Salesforce Developer")
    assert admits(hints2, "Backend Engineer")

    # A profession the sixteen families do not represent, carried by its title.
    teacher = {
        "title_hints": [],
        "role_signals": {"employment_titles": ["High School Teacher"],
                         "titles": ["Teacher"], "target_field": ""},
        "role_evidence": {"supports": {}, "stated_target": {"family": None}},
    }
    hints3, rec3 = build(teacher, base_hints=["developer", "software engineer"])
    assert admits(hints3, "High School Teacher"), hints3
    assert not admits(hints3, "Backend Developer"), hints3
    assert rec3["global_floor_used"] is False

    # Nothing at all: the last fallback, recorded rather than silent.
    empty, rec4 = build({"title_hints": [], "role_signals": {}},
                        base_hints=["developer"])
    assert empty == ["developer"] and rec4["global_floor_used"]
    assert rec4["fallback"]
    print("title_gate demo ok")


if __name__ == "__main__":
    demo()
