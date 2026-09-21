"""A job whose JD proves the candidate is years short of its floor.

The existing experience gate lives in `scraper._required_experience_floor` +
`SETTINGS["max_experience_years"]`, and it does two things this one does not:

  * it reads ONE number per posting (max() across every years figure) with no
    idea whether that figure is an overall minimum, a per-skill figure, a
    preference or the low end of a range;
  * `max_experience_years` is rendered as `years_experience + 3`, and the gate
    is a strict `>`, so a candidate the engine recorded as 3 years keeps every
    posting demanding 6. That is the reported defect: Hargun (3y1m) received
    6+ roles.

This module answers a narrower question — "does the JD itself CONFIRM the
candidate is materially below the minimum?" — and only acts when it does.

    assess(jd_text, candidate_months)  ->  {candidate_years, job_min_years,
                                            requirement_type, confidence,
                                            gap_years, action, evidence}

    action  "none" | "penalty" | "hard_drop"

Three things it will not do, each because the false positive is worse than the
false negative:

  * act on a TITLE. "Senior", "Staff", "Lead" and "Principal" are labels, and
    config.SCORING already down-ranks them. Nothing here reads the title.
  * act on a PREFERENCE. "6+ years preferred" is not a bar.
  * act on a figure whose scope it could not resolve. A years figure attached
    to a named technology ("6+ years Salesforce") is not an overall minimum,
    and treating it as one is precisely how "3+ years total, 6+ Salesforce"
    would delete a reachable job.

Unknown anything — no candidate months, no confirmed statement, unreadable JD —
returns action "none". Fail open, always.

Aggregation is MIN across confirmed overall statements, deliberately the
opposite of `SETTINGS["experience_aggregate"]="max"`. That setting feeds a
DISPLAYED number and a ranking order, where over-reading is cheap; this feeds a
deletion, where over-reading removes a job the person could have had.
"""
import os
import re

FLAG = "SWEEP_EXPERIENCE_MISMATCH_GUARD"

# Gap bands, in years. A gap under a year is noise — résumés round, JDs round,
# and the candidate figure is itself derived from month-granularity dates.
PENALTY_GAP = 1.0
DROP_GAP = 2.0


def enabled():
    """Read per call, so a rollback lands on the next request."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------
# Reading the JD
# --------------------------------------------------------------------------
# One years figure, with the upper bound of a range captured separately rather
# than discarded — "3-6 years" is a minimum of 3, and reading it as 6 is the
# single easiest way to delete a reachable posting. Separators are the three
# dashes and the word "to", the same set scraper.YEARS_PATTERN learned the hard
# way from Accenture's templates.
_YEARS = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:(?:[-–—]|to)\s*(\d{1,2})\s*\+?\s*)?"
    r"(years?|yrs?)(?:\(s\))?", re.I)

# The figure is counting experience at all.
_EXP_CUE = re.compile(r"experience|hands[- ]on", re.I)
# A requirement cue immediately before the figure is itself enough: "at least 6
# years" in a JD is not counting anything but a career.
_REQ_CUE = re.compile(
    r"(?:minimum|min\.?|at least|no less than|must have|must possess|"
    r"mandatory|require[sd]?|required|requirement)\W*(?:of\s*)?$", re.I)
_REQ_AFTER = re.compile(r"^\W*(?:\w+\s+){0,3}(?:require[sd]?|mandatory|"
                        r"is a must|must)\b", re.I)
_PREF = re.compile(
    r"prefer(?:red|ably)?|ideally|ideal candidate|nice[- ]to[- ]have|"
    r"nice to have|a plus|bonus|desirable|desired|advantage|would be great|"
    r"we'?d love|good to have", re.I)
# "up to 5 years" is a ceiling, not a floor. Never a reason to drop.
_MAX_CUE = re.compile(r"(?:up to|no more than|less than|under|at most|"
                      r"maximum(?: of)?|max\.?)\s*$", re.I)
# Not a career: schooling, the company's age, somebody's age.
_EDU = re.compile(r"education|schooling|degree|diploma|bachelor|master|"
                  r"graduation|b\.?\s?tech|b\.?\s?e\b|m\.?\s?tech|"
                  r"post[- ]graduat", re.I)
_NOT_EXP = re.compile(r"^\s*(?:ago|old\b|of age|in business)", re.I)
_COMPANY_AGE = re.compile(
    r"(?:in business|been (?:around|operating|serving)|founded|established|"
    r"celebrating|for over)\s*(?:for\s*)?(?:over\s*)?$", re.I)
# Somebody else's years. "a team with 30 years of combined experience" is the
# employer's brochure, not a bar the applicant has to clear.
_NOT_YOURS = re.compile(r"combined|collectively|between us|our team|we have|"
                        r"the team has|across the team", re.I)

# Words that keep a figure GENERIC. A phrase built only out of these — plus the
# word "experience" and the connectives below — is an OVERALL minimum.
# Anything else names a specialism, and a specialism's years are not a career's.
_GENERIC = {
    "total", "overall", "combined", "cumulative", "professional", "relevant",
    "work", "working", "industry", "corporate", "commercial", "prior",
    "demonstrable", "proven", "progressive", "post-qualification", "hands-on",
    "hands", "on", "it", "software", "technology", "tech", "engineering",
    "development", "developing", "product", "business", "analysis", "analyst",
    "consulting", "functional", "technical", "full-time", "fulltime",
    "career", "employment", "job", "roles", "role", "similar", "related",
    "equivalent", "comparable", "applicable", "e",
    # "experience" does NOT end the scan: "experience in Salesforce" is the
    # phrasing that narrows a total down to one product, and stopping here
    # would read every one of them as an overall career minimum.
    "experience", "exp",
}
# Carry the scan forward without deciding anything.
_CONNECTIVE = {"of", "in", "with", "as", "on", "at", "for", "the", "a", "an",
               "any", "or", "to", "within", "across", "least", "minimum",
               "min", "plus"}
# The sentence has stopped talking about WHAT the years are in.
_STOP = {"required", "require", "requires", "requirement",
         "mandatory", "must", "is", "are", "have", "having", "needed", "need",
         "preferred", "desired", "essential", "expected", "and", "including",
         "include", "ideally", "you", "we", "candidates", "candidate"}
_WORD = re.compile(r"[a-z][a-z.\-]*", re.I)
_CLAUSE_END = re.compile(r"[.,;:()\[\]\n|/]|\bbut\b")


def _scope(after):
    """"overall" or "skill_specific" for the text following a years figure.

    Walks the words after the figure until the clause ends. Generic words and
    the word "experience" keep it overall; the first word that names something
    in particular ("react", "salesforce", "sap") makes it skill-specific.

    Empty is overall: "at least 6 years." narrows nothing, so nothing narrows
    it. Unknown is skill-specific, which is the fail-open side — a
    skill-specific figure can never drop a job.
    """
    clause = after
    end = _CLAUSE_END.search(after)
    if end:
        clause = after[:end.start()]
    for m in _WORD.finditer(clause.lower()):
        word = m.group(0).strip(".")
        if word in _STOP:
            break
        if word in _CONNECTIVE or word in _GENERIC:
            continue
        return "skill_specific"
    return "overall"


def statements(text):
    """Every years figure in `text`, read and classified.

    One dict per figure: min_years, max_years, requirement_type, scope,
    confidence, evidence. Nothing is aggregated here — `assess` decides which
    of them may act, and the whole list is what makes a decision auditable.
    """
    text = text or ""
    out = []
    for m in _YEARS.finditer(text):
        before = text[max(0, m.start() - 90):m.start()]
        after = text[m.end():m.end() + 90]
        low = int(m.group(1))
        high = int(m.group(2)) if m.group(2) else None
        evidence = text[max(0, m.start() - 40):m.end() + 50].strip()
        row = {"min_years": low, "max_years": high, "evidence": evidence,
               "scope": "unknown", "confidence": "low"}

        if _NOT_EXP.match(after) or _COMPANY_AGE.search(before):
            out.append(dict(row, requirement_type="not_experience"))
            continue
        edu, exp = _EDU.search(after), _EXP_CUE.search(after)
        # Whichever word comes first decides what is being counted: Accenture
        # writes "minimum 3 Year(s) Of Experience Is Required. Educational
        # Qualification: 15 Years Full Time Education" and both land in the
        # same window.
        if edu and (not exp or edu.start() < exp.start()):
            out.append(dict(row, requirement_type="not_experience"))
            continue
        if not (exp or _EXP_CUE.search(before) or _REQ_CUE.search(before)):
            out.append(dict(row, requirement_type="not_experience"))
            continue
        if _NOT_YOURS.search(before) or _NOT_YOURS.search(after[:40]):
            out.append(dict(row, requirement_type="ambiguous"))
            continue
        if _MAX_CUE.search(before):
            out.append(dict(row, requirement_type="maximum"))
            continue

        scope = _scope(after)
        if _PREF.search(before[-60:]) or _PREF.search(after[:60]):
            out.append(dict(row, requirement_type="preferred", scope=scope))
            continue

        # A bar is CONFIRMED when the JD says so — an explicit requirement cue,
        # the "+" that means "and up", or a range. A bare "6 years of
        # experience" is left at low confidence and can never drop a job.
        explicit = bool(_REQ_CUE.search(before) or _REQ_AFTER.match(after))
        plus = "+" in text[m.start():m.end()]
        kind = "range" if high is not None else "required"
        confidence = ("high" if (scope == "overall"
                                 and (explicit or plus or high is not None))
                      else "low")
        out.append(dict(row, requirement_type=kind, scope=scope,
                        confidence=confidence))
    return out


def required_minimum(text):
    """(years, evidence, requirement_type) for the CONFIRMED overall minimum.

    (None, "", "") when the JD does not confirm one. MIN across confirmed
    statements, not max: this number deletes jobs, so where a posting states
    two overall bars the smaller one is the one that has to be cleared.
    """
    ok = [s for s in statements(text)
          if s["scope"] == "overall" and s["confidence"] == "high"
          and s["requirement_type"] in ("required", "range")]
    if not ok:
        return None, "", ""
    best = min(ok, key=lambda s: s["min_years"])
    return best["min_years"], best["evidence"], best["requirement_type"]


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------
def assess(text, candidate_months):
    """What to do about this posting's experience bar, and why.

    `candidate_months` is the month-granularity total local_extract computes
    (`experience_months`). Whole years are NOT accepted as a substitute: they
    are floored, so a candidate with 2y11m reads as 2 and every 4-year posting
    becomes a 2.0-year gap — a hard drop built on 11 months of rounding. No
    months, no guard.
    """
    verdict = {"candidate_years": None, "job_min_years": None,
               "requirement_type": "", "confidence": "none",
               "gap_years": None, "action": "none", "evidence": ""}
    if not isinstance(candidate_months, (int, float)) or candidate_months < 0:
        verdict["evidence"] = "candidate experience unknown"
        return verdict
    verdict["candidate_years"] = round(candidate_months / 12.0, 2)

    job_min, evidence, kind = required_minimum(text)
    if job_min is None:
        verdict["evidence"] = "no confirmed overall minimum in the posting"
        return verdict

    gap = round(job_min - verdict["candidate_years"], 2)
    verdict.update(job_min_years=job_min, requirement_type=kind,
                   confidence="high", gap_years=gap, evidence=evidence,
                   action=("hard_drop" if gap >= DROP_GAP
                           else "penalty" if gap >= PENALTY_GAP else "none"))
    return verdict


# Debug telemetry. Hard drops are invisible by construction — the row is gone —
# so the reason has to be kept somewhere. Not shown to end users.
DROPPED = []


def record(verdict, title=""):
    """Remember a non-"none" verdict, for the run summary and for a bug report."""
    if verdict["action"] != "none":
        DROPPED.append(dict(verdict, title=title))
    return verdict


def summary():
    """One line per acted-on posting, plus counts. Empty when nothing acted."""
    if not DROPPED:
        return ""
    drops = [d for d in DROPPED if d["action"] == "hard_drop"]
    pens = [d for d in DROPPED if d["action"] == "penalty"]
    lines = [f"experience guard: {len(drops)} dropped, {len(pens)} penalised"]
    for d in drops:
        lines.append(f"  drop  {d['title'][:48]:<48} needs {d['job_min_years']}+"
                     f" vs {d['candidate_years']} (gap {d['gap_years']})"
                     f"  [{d['requirement_type']}] {d['evidence'][:70]!r}")
    return "\n".join(lines)


def demo():
    """Self-check: the classifier, the bands, and every fail-open path."""
    HARGUN = 37            # 3 years 1 month, the reported regression
    def act(jd, months=HARGUN):
        return assess(jd, months)["action"]

    # The reported case, and the band either side of it.
    assert act("We require 6+ years of experience.") == "hard_drop"
    assert act("We require 8+ years of experience.") == "hard_drop"
    assert act("We require 5+ years of experience.") == "penalty"
    assert act("We require 4+ years of experience.") == "none"

    # A preference is not a bar. A range is read at its floor.
    assert act("6+ years of experience preferred.") == "none"
    assert act("Ideally 6-8 years of experience.") == "none"
    assert act("3-6 years of experience.") == "none"
    assert act("Up to 6 years of experience.") == "none"

    # The multiple-years regression: the overall figure, never the per-skill one.
    both = "5+ years total development experience and 1+ years React."
    assert required_minimum(both)[0] == 5, statements(both)
    flipped = "3+ years of total experience, 6+ years of Salesforce experience."
    assert required_minimum(flipped)[0] == 3, statements(flipped)
    assert act(flipped) == "none"
    overall = "Experience: 6+ years overall, 3+ years Salesforce."
    assert required_minimum(overall)[0] == 6, statements(overall)

    # Not experience at all.
    for jd in ("A 4-year bachelor's degree is required.",
               "You will manage a team of 8.",
               "Salary $100k. Founded 6 years ago.",
               "A team with 30 years of combined experience."):
        assert required_minimum(jd)[0] is None, (jd, statements(jd))

    # Ambiguity fails open: a bare figure with no "+", no cue and no range.
    assert act("The role involves 6 years of experience.") == "none"

    # Both unknowns fail open.
    assert assess("6+ years of experience required.", None)["action"] == "none"
    assert act("A great place to work.") == "none"

    # A senior candidate clears the same bar.
    assert assess("6+ years of experience required.", 72)["action"] == "none"

    v = assess("Minimum 6 years of experience required.", HARGUN)
    assert v["job_min_years"] == 6 and v["gap_years"] == 2.92, v
    assert v["confidence"] == "high" and "6 years" in v["evidence"], v
    print("experience_guard demo ok")


if __name__ == "__main__":
    demo()
