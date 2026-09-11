"""A profile from a résumé with no API key: local model, then Python.

The Gemini-free half of auto-apply/make_profile.py. It returns the SAME
dict `_generate_one` returns, so everything downstream — widen_skills,
reweight_from_corpus, render(), Sweep's review screen — is unchanged and
does not know which engine produced it.

    generate(resume_text, prefs)    -> the profile dict
    Escalated                       -> raised when the local parse fails
                                       a check and Gemini should be asked

WHAT IS LOCAL AND WHAT IS NOT
-----------------------------
Two calls to a model on the user's own machine read the résumé
(local_extract). Everything after that is arithmetic and measurement
against the jobs already in output/ (local_search). Nothing leaves the
machine.

  candidate_name      local model, then checked against the document
  years_experience    COMPUTED from the extracted dates — 52/52 on the
                      benchmark, against 40/52 when the model is asked
  skills              local model, concept filler removed, then widened
                      by skill_scan in make_profile.generate()
  role_keywords       held titles, then specialist-anchored recoveries,
                      then corpus lift — all validated
  title_hints         corpus fragments, widened to RULE 3's floor
  skill_weights       neutral here on purpose: reweight_from_corpus runs
                      immediately after in generate() and is the thing
                      that sets these numbers
  penalty_terms       the user's own avoid-list, nothing inferred

FIELDS THIS ENGINE LEAVES EMPTY, DELIBERATELY
---------------------------------------------
domain_half_a / domain_half_b / domain_title_terms / domain_bonus and
title_exclude all ship empty, which is what the live-validated local
profiles (profiles/cmp4_narrow_*.py) ran with. The halves were tried
through a local semantic call and the title_exclude answers measured 46%
wrong with four fifths unverifiable — and that gate is checked FIRST, so
a wrong entry deletes a target role before anything scores it.
config.py's own base ships zero entries too. An empty half with a zero
bonus is a dead setting, not a missing one.

Gemini does fill these, which is a real difference and is logged rather
than hidden.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apply_config as cfg

if cfg.REPO_ROOT not in sys.path:
    sys.path.insert(0, cfg.REPO_ROOT)

import local_extract
import local_search

# The model this was measured on. Bigger models were tried for extraction
# and did not do better; see bench/extract_models.py. Resolved per call
# from OLLAMA_MODEL by local_extract.model_name, so this name is the
# documented default rather than the value used.
DEFAULT_MODEL = local_extract.DEFAULT_MODEL

# Mid-scale on the 1-12 severity the schema uses, negated at render time.
# The avoid-list is the user's own instruction, so it does not need to
# out-shout a skill weight to be honoured — and render() strips any entry
# that is already in --exclude-levels, which hard_drop_terms deletes anyway.
AVOID_SEVERITY = 6

# Neutral, because reweight_from_corpus runs next and is what decides
# these numbers. Matching skill_scan's own constant keeps a reported
# skill and a scanned one on the same footing before that runs.
NEUTRAL_WEIGHT = 3


class Escalated(RuntimeError):
    """The local parse failed a deterministic check with no local fix.

    Carries the reasons, which name the evidence rather than a suspicion:
    dates that cannot be read, a model confabulating most of an answer, or
    an employment history flagged entirely as someone else's career.
    """

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


def _summary(field, years, skills):
    """The one-line field summary render() puts in the docstring.

    Gemini writes prose here. This states the facts it was derived from,
    which is more useful to someone checking the profile and cannot
    misdescribe a career it has not understood.
    """
    what = field.strip() or "software engineering"
    how_long = ("no completed professional years yet" if not years
                else f"{years} year{'s' if years != 1 else ''} of relevant experience")
    return f"{what} — {how_long}, {len(skills)} skills read from the résumé."


def _notes(decision, fields, years, field):
    """How this profile was arrived at, for the docstring render() writes.

    Every correction the router made is named. skill_scan appends its own
    reasons to this string later, in make_profile.widen_skills.
    """
    lines = [f"Generated locally — no API key, no data left this machine. "
             f"Target field read as {field.strip() or 'software engineering'!r}; "
             f"years_experience COMPUTED from the extracted employment dates, "
             f"not asked for."]
    if decision.get("corrections"):
        for fix in decision["corrections"]:
            if fix["action"] == "computed":
                lines.append(
                    f"{fix['field']}: the model said {fix['from']}, the dates "
                    f"say {fix['to']} — {fix['why']}.")
            else:
                lines.append(
                    f"{fix['field']}: dropped {', '.join(map(str, fix['removed']))} "
                    f"— {fix['why']}.")
    if fields["filler_dropped"]:
        lines.append(
            f"Dropped {len(fields['filler_dropped'])} résumé term(s) that name "
            f"a concept rather than a searchable tool: "
            f"{', '.join(fields['filler_dropped'])}.")
    if fields["from_orphans"]:
        lines.append(
            f"Added {len(fields['from_orphans'])} keyword(s) for a specialist "
            f"skill nothing else was searching for: "
            f"{', '.join(fields['from_orphans'])}.")
    lines.append(
        "The two domain halves, the title-alone promoters and title_exclude "
        "are empty on purpose — see auto-apply/local_profile.py.")
    return " ".join(lines)


def generate(resume_text, prefs, model=None, output_dir=None, log=print,
             market=None, url=None):
    """One profile, from the résumé and the corpus. Raises Escalated.

    `prefs` is make_profile's dict: locations, exclude_levels, avoid,
    min_comp_usd and the optional overrides. Only `avoid` is read here —
    the rest are the user's, and render() applies them.
    """
    # Both resolved here so the log names what was actually asked, and
    # so OLLAMA_HOST/OLLAMA_MODEL are read at call time rather than frozen
    # at import.
    model = local_extract.model_name(model)
    url = url or local_extract.endpoint()

    log(f"  reading the résumé with {model} at {url.rsplit('/api/', 1)[0]}")
    checked, rows, decision = local_extract.read(model, resume_text, url=url)
    if checked is None:
        raise Escalated(decision["reasons"])
    log(f"    grounding: {decision['decision']}"
        + (f", {len(decision['corrections'])} correction(s)"
           if decision["corrections"] else ""))

    years = checked["years_experience"]
    field = (rows or {}).get("target_field") or ""

    log("  deriving the search fields from the jobs already scraped")
    market = market if market is not None else local_search.Market(output_dir)
    if not len(market):
        # No corpus is a cold start, not an error: from_resume() still
        # gives the person's own job titles, which is the zero-dependency
        # floor this was designed to have.
        log("    output/ holds no scored listings — falling back to the "
            "résumé's own job titles only")
    person = {"skills": checked.get("skills") or (),
              "employment": (rows or {}).get("employment") or []}
    fields = local_search.fields_for(person, market)

    if not fields["role_keywords"]:
        # Nothing to search for is not a profile. This is the one local
        # failure that is about the corpus rather than the résumé, and it
        # is worth saying which.
        raise Escalated([
            "role_keywords: no keyword survived validation — the résumé's "
            f"{len(person['skills'])} skill(s) and "
            f"{len(person['employment'])} employment row(s) produced nothing "
            f"the {len(market)}-listing corpus supports"])

    log(f"    {len(fields['role_keywords'])} keyword(s), "
        f"{len(fields['title_hints'])} title hint(s), "
        f"{len(fields['skills'])} skill(s)")
    log(local_search.explain(fields["ranking"]))

    return {
        "candidate_name": checked.get("name") or "",
        "field_summary": _summary(field, years, fields["skills"]),
        "years_experience": years,
        "role_keywords": fields["role_keywords"],
        "skill_weights": [{"term": s, "weight": NEUTRAL_WEIGHT}
                          for s in fields["skills"]],
        "penalty_terms": [{"term": t.strip().lower(),
                           "weight": AVOID_SEVERITY}
                          for t in (prefs.get("avoid") or ()) if t.strip()],
        # See the module docstring: empty is the validated setting.
        "domain_half_a": [],
        "domain_half_b": [],
        "domain_title_terms": [],
        "domain_bonus": 0,
        "title_hints": fields["title_hints"],
        "title_exclude": [],
        "notes": _notes(decision, fields, years, field),
        # Provenance, for the caller's log and for the tests. render()
        # ignores keys it does not name.
        "local_ranking": fields["ranking"],
        "local_from_orphans": fields["from_orphans"],
        "local_decision": decision["decision"],
    }


def demo():
    """Self-check with the model and the corpus both faked."""
    text = ("Lovish Kumar\n"
            "Salesforce Developer at Acme Cloud, Jan 2023 - Present\n"
            "Skills: apex, soql, lwc, javascript, SOLID principles\n")

    rows = []
    for i in range(60):
        rows.append(("salesforce developer", 25 if i % 2 else 5,
                     frozenset({"apex", "soql", "lwc"}), f"sfco{i % 20}"))
    for i in range(400):
        rows.append((f"software engineer {i % 7}", 3,
                     frozenset({"java", "sql"}), f"bigco{i % 25}"))
    market = local_search.Market(rows=rows,
                                 seniority=("senior", "staff", "lead"))

    answers = {
        "fields": {"name": "Lovish Kumar", "years_experience": 9,
                   "titles": ["Salesforce Developer"],
                   "skills": ["apex", "soql", "lwc", "javascript",
                              "solid principles"],
                   "companies": ["Acme Cloud"], "education": [],
                   "institutions": [], "projects": [], "certifications": []},
        "employment": {"target_field": "software engineering", "employment": [
            {"company": "Acme Cloud", "title": "Salesforce Developer",
             "start": "Jan 2023", "end": "Present", "relevant": True}]},
    }
    real_extract, real_employment = local_extract.extract, local_extract.employment
    local_extract.extract = lambda m, t, *a, **k: (answers["fields"], 0.1)
    local_extract.employment = lambda m, t, *a, **k: answers["employment"]
    quiet = lambda *a, **k: None
    try:
        got = generate(text, {"avoid": ["CRM", ""]}, market=market, log=quiet)

        # The number is COMPUTED, not the 9 the model said.
        assert got["years_experience"] == local_extract.years_from(
            answers["employment"]["employment"]), got["years_experience"]
        assert got["years_experience"] != 9
        # ...and the correction is stated in the notes, with both figures.
        assert "the model said 9" in got["notes"], got["notes"]

        # The concept term is gone, the real ones are not.
        assert "solid principles" not in [w["term"] for w in got["skill_weights"]]
        assert {"apex", "soql", "lwc"} <= {w["term"] for w in got["skill_weights"]}
        assert "concept" in got["notes"]

        # The person's own held title leads the keywords.
        assert got["role_keywords"][0] == "salesforce developer", got
        assert got["local_ranking"][0][1] == 1

        # Weights are neutral: reweight_from_corpus sets them next.
        assert {w["weight"] for w in got["skill_weights"]} == {NEUTRAL_WEIGHT}

        # The avoid-list becomes a penalty; the empty string does not.
        assert got["penalty_terms"] == [{"term": "crm", "weight": AVOID_SEVERITY}]

        # The deliberately-empty fields are empty, and the bonus with them.
        for field in ("domain_half_a", "domain_half_b", "domain_title_terms",
                      "title_exclude"):
            assert got[field] == [], field
        assert got["domain_bonus"] == 0

        assert got["candidate_name"] == "Lovish Kumar"
        assert got["title_hints"], "the free-source gate must not be empty"

        # Every key render() reads is present, so a malformed profile is a
        # failure here rather than a KeyError in the config the scraper
        # imports.
        import make_profile
        for key in make_profile.RESPONSE_SCHEMA["required"]:
            assert key in got, key

        # A confabulating parse escalates rather than being rendered.
        local_extract.extract = lambda m, t, *a, **k: (
            dict(answers["fields"], skills=["cobol", "fortran", "rpg"]), 0.1)
        try:
            generate(text, {"avoid": []}, market=market, log=quiet)
            raise AssertionError("a confabulated skill list must escalate")
        except Escalated as exc:
            assert "not found in the document" in str(exc), exc

        # An empty corpus is a cold start, not a failure: the person's own
        # job title is the zero-dependency floor this was designed to keep.
        local_extract.extract = lambda m, t, *a, **k: (answers["fields"], 0.1)
        empty = local_search.Market(rows=[], seniority=("senior",))
        cold = generate(text, {"avoid": []}, market=empty, log=quiet)
        assert cold["role_keywords"] == ["salesforce developer"], cold

        # But a one-word title leaves even that floor with nothing — a
        # bare "Engineer" as a search string buys the catalogue — and with
        # no corpus to fall back on there is no profile to render.
        bare = ("Ann Lee\n"
                "Engineer at Acme Cloud, Jan 2023 - Present\n"
                "Skills: apex\n")
        local_extract.extract = lambda m, t, *a, **k: (
            {"name": "Ann Lee", "years_experience": 3, "titles": ["Engineer"],
             "skills": ["apex"], "companies": ["Acme Cloud"], "education": [],
             "institutions": [], "projects": [], "certifications": []}, 0.1)
        local_extract.employment = lambda m, t, *a, **k: {
            "target_field": "software engineering",
            "employment": [{"company": "Acme Cloud", "title": "Engineer",
                            "start": "Jan 2023", "end": "Present",
                            "relevant": True}]}
        try:
            generate(bare, {"avoid": []}, market=empty, log=quiet)
            raise AssertionError("no keyword at all must escalate")
        except Escalated as exc:
            assert "no keyword survived" in str(exc), exc
    finally:
        local_extract.extract, local_extract.employment = (real_extract,
                                                           real_employment)
    print("local_profile demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        print(__doc__)
