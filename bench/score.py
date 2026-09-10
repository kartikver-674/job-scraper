"""Score one parser's output against the answer key.

Exact match after normalisation, not fuzzy match. Fuzzy scoring flatters a
parser: "Backend Engineer" scored as a hit for "Senior Backend Engineer"
hides exactly the seniority confusion that would send someone the wrong
jobs. Where a near-miss is interesting it is reported separately rather
than folded into the score.

Two aggregates, because the plan asked a wider question than Sweep does:

  sweep   the fields RESPONSE_SCHEMA actually consumes today
  full    everything a résumé parser should get right

A model that cannot find an employer's name is not production-worthy even
though Sweep never asks for one.
"""

import re
import sys

# Set-valued fields, scored with precision/recall/F1.
SET_FIELDS = ("titles", "skills", "companies", "education", "institutions",
              "projects", "certifications")
# Single-valued fields, scored exact.
SCALAR_FIELDS = ("name", "years_experience")

# What Sweep's RESPONSE_SCHEMA actually consumes. The rest is measured
# because the question is whether the model parses résumés, not whether it
# fills this one form.
SWEEP_FIELDS = ("name", "years_experience", "titles", "skills")

_PUNCT = re.compile(r"[^a-z0-9+#. ]+")
_SPACE = re.compile(r"\s+")


def norm(value):
    """Compare-ready text: lowercase, punctuation folded, spaces collapsed.

    Keeps + # . because they are part of real skill names — c++, c#,
    node.js, tla+ — and dropping them merges terms that are not the same.
    """
    text = _PUNCT.sub(" ", str(value).strip().lower())
    return _SPACE.sub(" ", text).strip()


def prf(predicted, actual):
    """(precision, recall, f1, missing, spurious) for two collections."""
    got = {norm(x) for x in predicted or () if norm(x)}
    want = {norm(x) for x in actual or () if norm(x)}
    if not want and not got:
        return 1.0, 1.0, 1.0, [], []
    hit = got & want
    precision = len(hit) / len(got) if got else 0.0
    recall = len(hit) / len(want) if want else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)
    return precision, recall, f1, sorted(want - got), sorted(got - want)


def score_one(predicted, truth):
    """Per-field result for one document."""
    out = {}
    for field in SET_FIELDS:
        p, r, f1, missing, spurious = prf(predicted.get(field), truth.get(field))
        out[field] = {"precision": p, "recall": r, "f1": f1,
                      "missing": missing, "spurious": spurious}

    got_name, want_name = norm(predicted.get("name", "")), norm(truth["name"])
    out["name"] = {"f1": 1.0 if got_name == want_name else 0.0,
                   "got": predicted.get("name", ""), "want": truth["name"]}

    want_years = truth["years_experience"]
    raw = predicted.get("years_experience")
    try:
        got_years = int(raw)
    except (TypeError, ValueError):
        got_years = None
    out["years_experience"] = {
        "f1": 1.0 if got_years == want_years else 0.0,
        # The size of the miss matters: one year out is a rounding
        # disagreement, twenty-three is a date-of-birth read as a career.
        "error": None if got_years is None else abs(got_years - want_years),
        "got": got_years, "want": want_years}
    return out


def aggregate(results, fields=None):
    """Macro-average F1 over `fields` across every scored document.

    Macro, not micro: eight résumés with wildly different skill counts
    would otherwise let one long one decide the number.
    """
    fields = fields or (SET_FIELDS + SCALAR_FIELDS)
    per_field = {}
    for field in fields:
        scores = [r[field]["f1"] for r in results if field in r]
        per_field[field] = sum(scores) / len(scores) if scores else None
    have = [v for v in per_field.values() if v is not None]
    return {"per_field": per_field,
            "macro_f1": sum(have) / len(have) if have else 0.0}


def demo():
    assert norm("  Senior  Backend Engineer ") == "senior backend engineer"
    assert norm("Node.js") == "node.js", "a dot is part of the name"
    assert norm("C++") == "c++" and norm("C#") == "c#"
    assert norm("TLA+") == "tla+"
    assert norm("Université Hassan II") == "universit hassan ii"

    # Exact, not fuzzy: the seniority difference is the thing that would
    # send someone the wrong jobs.
    p, r, f1, missing, spurious = prf(["Backend Engineer"],
                                      ["Senior Backend Engineer"])
    assert f1 == 0.0 and missing == ["senior backend engineer"]
    assert spurious == ["backend engineer"]

    p, r, f1, _, _ = prf(["a", "b"], ["a", "b"])
    assert (p, r, f1) == (1.0, 1.0, 1.0)
    p, r, f1, missing, spurious = prf(["a", "x"], ["a", "b"])
    assert p == 0.5 and r == 0.5 and missing == ["b"] and spurious == ["x"]
    # Both empty is agreement, not failure: a fresher has no certifications
    # and a parser that returns none is right.
    assert prf([], [])[2] == 1.0
    assert prf(["a"], [])[2] == 0.0, "inventing one is not agreement"
    # Duplicates collapse — chen's promotion is two rows at one employer.
    assert prf(["Meridian Pay", "Meridian Pay"], ["Meridian Pay"])[2] == 1.0

    truth = {"name": "Ada Okonkwo", "years_experience": 5,
             "titles": ["Backend Engineer"], "skills": ["python"],
             "companies": ["Fettle Health"], "education": [],
             "institutions": [], "projects": [], "certifications": []}
    good = score_one({"name": "ada okonkwo", "years_experience": 5,
                      "titles": ["Backend Engineer"], "skills": ["Python"],
                      "companies": ["Fettle Health"]}, truth)
    assert good["name"]["f1"] == 1.0, "case is not a difference"
    assert good["years_experience"]["f1"] == 1.0
    assert good["skills"]["f1"] == 1.0

    bad = score_one({"name": "", "years_experience": 23, "skills": []}, truth)
    assert bad["name"]["f1"] == 0.0
    assert bad["years_experience"]["error"] == 18, "the size of the miss"
    # A non-integer answer is a miss, not a crash.
    assert score_one({"years_experience": "five"}, truth
                     )["years_experience"]["error"] is None

    agg = aggregate([good], SWEEP_FIELDS)
    assert agg["macro_f1"] == 1.0
    assert set(agg["per_field"]) == set(SWEEP_FIELDS)
    mixed = aggregate([good, bad], ("name",))
    assert mixed["macro_f1"] == 0.5, "macro-averaged across documents"
    print("score demo ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
