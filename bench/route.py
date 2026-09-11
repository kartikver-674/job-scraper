"""Decide, per résumé, whether the local parse is good enough to keep.

Three outcomes, and every escalation carries the reason it escalated:

  accept     every check passed; use the local result as it stands
  corrected  a check failed and a deterministic fix existed; fixed locally
  escalate   a check failed with no local fix; this one needs Gemini

The checks are deterministic on purpose. A schema-constrained model returns
well-formed JSON whether it is right or wrong, so it has no confidence to
report — "97%" is not a number any of these models emit. What IS available
is evidence:

  grounding   every name, employer, skill and title the model returned
              should appear in the document it read. One that does not was
              invented, and an invented value can be dropped.
  arithmetic  years of experience is computed from the extracted dates
              and the definition in bench/dates.py, so the model's own
              answer is checkable against a figure that does not depend
              on it. The model contributes one judgement the arithmetic
              cannot make — whether a role belongs to the career the
              résumé is targeting — and even that is checked: flags that
              wipe out the entire history escalate rather than returning
              zero years.

Escalation is reserved for evidence of a PROBLEM, not suspicion of
difficulty: dates that cannot be read, or a model confabulating most of an
answer rather than one item of it. Guessing that a document is hard would
send work to Gemini on a hunch, which is the dependency this is meant to
remove.

    python -m bench.route qwen3:8b     # the tally, with reasons
    python -m bench.route --demo
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

import local_extract
from local_extract import (  # noqa: F401  -- re-exported for the harness
    CONFABULATION_SHARE,
    GROUNDED_FIELDS,
    SHORT,
    _key,
    check_grounding,
    supported,
)
from bench import dates as dating
from bench.people import PEOPLE, truth
from bench.render import LAYOUTS
from bench.run import RESUMES


def check_years(parsed, employment, text):
    return local_extract.check_years(parsed, employment, dating.TODAY)


def route(parsed, employment, text):
    return local_extract.route(parsed, employment, text, dating.TODAY)


def load(model):
    here = os.path.dirname(os.path.abspath(__file__))
    from bench.run import cache_name

    with open(os.path.join(here, "results", cache_name(model)),
              encoding="utf-8") as fh:
        fields = json.load(fh)
    dates_path = os.path.join(
        here, "results",
        "dates-" + model.replace(":", "_").replace("/", "_") + ".json")
    employment = {}
    if os.path.exists(dates_path):
        with open(dates_path, encoding="utf-8") as fh:
            employment = json.load(fh)
    return fields, employment


def report(model):
    from resume_parser import extract_text
    from bench.score import score_one, aggregate, SWEEP_FIELDS

    fields, employment = load(model)
    tally = {"accept": [], "corrected": [], "escalate": []}
    scored_before, scored_after, all_fixes = [], [], []

    for slug in PEOPLE:
        for layout in LAYOUTS:
            key = f"{slug}-{layout}"
            if key not in fields:
                continue
            text = extract_text(os.path.join(RESUMES, f"{slug}-{layout}.pdf"))
            decision = route(fields[key].get("parsed"),
                             employment.get(key), text)
            tally[decision["decision"]].append((key, decision))
            all_fixes.extend(decision["corrections"])
            if fields[key].get("parsed"):
                scored_before.append(score_one(fields[key]["parsed"],
                                               truth(slug)))
            if decision["result"]:
                scored_after.append(score_one(decision["result"], truth(slug)))

    total = sum(len(v) for v in tally.values())
    print(f"\n{'=' * 70}\n{model}: routing {total} résumés\n{'=' * 70}")
    print(f"  {len(tally['accept']):>3} accepted locally as-is")
    print(f"  {len(tally['corrected']):>3} corrected locally by a validator")
    print(f"  {len(tally['escalate']):>3} escalated to Gemini")
    handled = len(tally["accept"]) + len(tally["corrected"])
    print(f"\n  handled without Gemini: {handled}/{total}"
          f" = {handled / total:.0%}")
    print(f"  fallback rate:          {len(tally['escalate']) / total:.0%}")

    if all_fixes:
        counts = {}
        for fix in all_fixes:
            counts[(fix["field"], fix["action"])] = \
                counts.get((fix["field"], fix["action"]), 0) + 1
        print("\n  what the validators corrected:")
        for (field, action), n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>3}x  {field} {action}")

    if tally["escalate"]:
        print("\n  WHY each one went to Gemini:")
        for key, decision in tally["escalate"]:
            print(f"    {key}")
            for reason in decision["reasons"]:
                print(f"        {reason}")

    # years_experience is the field the definition was written for, and the
    # one the model cannot do alone. Reported per person because a single
    # average hides the thing worth seeing: whether the four renderings of
    # one person agree with each other.
    kept = {}
    for bucket in ("accept", "corrected"):
        for key, decision in tally[bucket]:
            kept.setdefault(key.rsplit("-", 1)[0], []).append(
                decision["result"].get("years_experience"))
    if kept:
        print("\n  years_experience of what was kept locally:")
        wrong = []
        for slug, got in sorted(kept.items()):
            want = truth(slug)["years_experience"]
            hits = sum(1 for g in got if g == want)
            flag = "" if hits == len(got) else "   <- WRONG, and kept"
            if hits != len(got):
                wrong.append(slug)
            print(f"    {slug:<9} truth {want:>2}   got {str(got):<16}"
                  f" {hits}/{len(got)}{flag}")
        # The number that matters more than the fallback rate. A parse that
        # is wrong AND passes every check costs more than one that escalates,
        # because nothing downstream will ever question it.
        total_kept = sum(len(v) for v in kept.values())
        bad = sum(1 for slug, got in kept.items()
                  for g in got if g != truth(slug)["years_experience"])
        print(f"\n    kept and correct:  {total_kept - bad}/{total_kept}")
        if wrong:
            print(f"    kept and WRONG:    {bad}/{total_kept}"
                  f"  ({', '.join(sorted(wrong))}) — these are worse than an"
                  f" escalation, because nothing downstream questions them")

    if scored_before and scored_after:
        before = aggregate(scored_before, SWEEP_FIELDS)["macro_f1"]
        after = aggregate(scored_after, SWEEP_FIELDS)["macro_f1"]
        print(f"\n  accuracy on Sweep's fields, of what was kept locally:")
        print(f"    raw model output        {before:.3f}")
        print(f"    after routing           {after:.3f}")
    return tally


def demo():
    text = ("Ada Okonkwo\nSenior Backend Engineer at Fettle Health, "
            "Apr 2023 - Present. Backend Engineer at Northwind Logistics, "
            "Jan 2021 - Mar 2023. Skills: python, django.")
    employment = {"employment": [
        {"company": "Fettle Health", "title": "Senior Backend Engineer",
         "start": "Apr 2023", "end": "Present"},
        {"company": "Northwind Logistics", "title": "Backend Engineer",
         "start": "Jan 2021", "end": "Mar 2023"}]}
    good = {"name": "Ada Okonkwo", "years_experience": 5,
            "titles": ["Backend Engineer"], "skills": ["python", "django"],
            "companies": ["Fettle Health", "Northwind Logistics"],
            "institutions": []}

    out = route(good, employment, text)
    assert out["decision"] == "accept", out
    assert out["result"]["years_experience"] == 5

    # The model's arithmetic is replaced by the arithmetic, and the fix says
    # so rather than changing the number quietly.
    wrong = dict(good, years_experience=3)
    out = route(wrong, employment, text)
    assert out["decision"] == "corrected"
    assert out["result"]["years_experience"] == 5
    fix = out["corrections"][0]
    assert (fix["from"], fix["to"]) == (3, 5) and "summed" in fix["why"]

    # One invented employer is dropped, not escalated.
    out = route(dict(good, companies=["Fettle Health", "Northwind Logistics",
                                      "University of Leeds"]),
                employment, text)
    assert out["decision"] == "corrected"
    assert "University of Leeds" not in out["result"]["companies"]
    assert len(out["result"]["companies"]) == 2

    # Most of them invented is not one bad row; it is a parse to distrust.
    out = route(dict(good, companies=["Nowhere Ltd", "Alsonot Inc",
                                      "Fettle Health"]), employment, text)
    assert out["decision"] == "escalate"
    assert "2 of 3 not found" in out["reasons"][0]

    # A name that is not on the page is never a droppable field.
    out = route(dict(good, name="Someone Else"), employment, text)
    assert out["decision"] == "escalate" and "name" in out["reasons"][0]

    # Unreadable dates: nothing to check the years against.
    murky = {"employment": [{"company": "X", "title": "Engineer",
                             "start": "?", "end": "?"}]}
    out = route(good, murky, text)
    assert out["decision"] == "escalate"
    assert "none of the 1 countable employment date ranges" in \
        out["reasons"][0], out["reasons"]

    # The career change, which is the case the definition exists for. The
    # correction has to SAY it excluded a previous field, because "12 became
    # 3" with no reason is exactly the silent answer that got hana wrong.
    changed = {"employment": [
        {"company": "Volta Insight", "title": "Data Engineer",
         "start": "Aug 2023", "end": "present", "relevant": True},
        {"company": "Achimota Senior High School",
         "title": "Mathematics Teacher", "start": "Sep 2014",
         "end": "Jul 2023", "relevant": False}]}
    out = route(dict(good, years_experience=11), changed, text)
    assert out["decision"] == "corrected"
    assert out["result"]["years_experience"] == 3, out["result"]
    assert "career change was detected" in out["corrections"][0]["why"]
    assert "8 years in a previous field" in out["corrections"][0]["why"], \
        out["corrections"][0]["why"]

    # One career throughout means no career-change note, so the log stays
    # readable: the note appears only where the answer is not the obvious one.
    out = route(dict(good, years_experience=3), employment, text)
    assert "career change" not in out["corrections"][0]["why"]

    # Flags that wipe out the whole history are not an answer of zero.
    out = route(good, {"employment": [dict(r, relevant=False)
                                      for r in changed["employment"]]}, text)
    assert out["decision"] == "escalate"
    assert "no relevant experience" in out["reasons"][0], out["reasons"]

    # An internship-only history is a fresher, and zero is the right answer
    # rather than a reason to pay for a second opinion.
    intern = {"employment": [{"company": "Zenith Softworks", "start": "May 2025",
                              "title": "Software Engineering Intern",
                              "end": "Jul 2025", "relevant": True}]}
    out = route(dict(good, years_experience=1), intern, text)
    assert out["decision"] == "corrected"
    assert out["result"]["years_experience"] == 0
    assert "internships" in out["corrections"][0]["why"]
    assert route(dict(good, years_experience=0), intern, text
                 )["decision"] == "accept"

    # A fresher: no rows, and the model agreed there were none.
    assert route(dict(good, years_experience=0, companies=[], titles=[]),
                 {"employment": []}, text)["decision"] == "accept"
    # A number with nothing behind it has nothing to check it against.
    out = route(dict(good, years_experience=8, companies=[], titles=[]),
                {"employment": []}, text)
    assert out["decision"] == "escalate" and "no employment rows" in out["reasons"][0]

    # Nothing at all from the model.
    assert route(None, employment, text)["decision"] == "escalate"

    # Punctuation and spacing are not differences.
    assert supported("Node.js", "we use node js here")
    assert supported("node.js", "Node.js and Express")
    assert supported("C++", "c ++ and rust")
    assert supported("react native", "React\nNative developer"), "line wrap"
    assert not supported("Kotlin", "we use node js here")
    assert not supported("", "anything")
    # A short name is looked for as a word, or the folded document makes it
    # match anything: "go" is inside "google" and "c" inside "docker".
    assert supported("go", "we write go and rust")
    assert not supported("go", "we use google cloud"), "go is not in google"
    assert not supported("c", "docker and terraform"), "c is not in docker"
    assert supported("c", "we write c and rust")
    print("route demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    for model in [a for a in sys.argv[1:] if not a.startswith("--")]:
        report(model)


if __name__ == "__main__":
    main()
