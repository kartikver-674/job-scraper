"""The migration gate for Profile Engine v2: five conditions, one input.

    A   v1, production today
    B   A + canonical concepts                     (step 2)
    C   B + evidence-aware importance              (step 3)
    D   B + revised role-query validation          (step 4)
    E   C + D, the full v2 candidate

Everything else is pinned. Same résumés, same corpus, same clock, same
seniority lists, same preferences — and, critically, the same MODEL
OUTPUT: extraction is built once per person from bench/people.py's own
truth dicts and replayed into all five conditions. So a difference
between two columns is postprocessing and nothing else. No model is
called; the 52-document suite is where extraction itself is judged, and
it is left alone.

WHAT THIS CAN AND CANNOT SHOW
-----------------------------
It can show: whether the eight known failures are gone, whether concepts
survive canonicalisation, whether importance is invariant to provenance,
whether queries stay inside the professions each persona plausibly
belongs to, and what any of it costs in time.

It cannot show that anyone gets better JOBS. That needs independently
judged job-ranking data, which does not exist here, so no Precision@10,
no nDCG, and no claim of either.

    python -m bench.evaluate            # the whole gate
    python -m bench.evaluate --demo     # self-check
"""

import json
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for path in (REPO_ROOT, os.path.join(REPO_ROOT, "auto-apply")):
    if path not in sys.path:
        sys.path.insert(0, path)

import corpus_signal          # noqa: E402
import local_search           # noqa: E402
import make_profile           # noqa: E402
import skill_concepts         # noqa: E402
import skill_evidence         # noqa: E402
import skill_scan             # noqa: E402
from bench import eval_people  # noqa: E402
from bench import render       # noqa: E402
from bench.people import titles as person_titles  # noqa: E402

NOW = (2026, 9)


def _sha(path):
    """A short digest of a corpus file, so an archived run says which."""
    import hashlib
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()[:16]
    except OSError:
        return None


def _revision():
    """The source revision, when this is a checkout."""
    import subprocess
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=10)
        return done.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None

# The five conditions, as the two flags that produce them.
CONDITIONS = {
    "A": {"concepts": False, "evidence": False, "roles": False,
          "label": "v1 (production today)"},
    "B": {"concepts": True, "evidence": False, "roles": False,
          "label": "canonical concepts only"},
    "C": {"concepts": True, "evidence": True, "roles": False,
          "label": "concepts + evidence importance"},
    # Evidence is ON here but its WEIGHTS are discarded: role families are
    # anchored by tiers, so "roles without importance" cannot exist — it
    # falls straight back to B. Computing tiers for the gate while keeping
    # v1's weights is the only way to isolate the role change, and it is
    # not a shippable configuration, only a measurement.
    "D": {"concepts": True, "evidence": True, "roles": True,
          "keep_v1_weights": True,
          "label": "concepts + role validation (v1 weights)"},
    "E": {"concepts": True, "evidence": True, "roles": True,
          "label": "full v2 candidate"},
}


class Flags:
    """The COMPLETE environment for one condition, restored afterwards.

    The independent review found this class changing the three step flags
    and leaving SWEEP_PROFILE_ENGINE_VERSION alone. A developer with that
    variable exported ran every condition — including the one labelled
    "v1" — as v2, and the archived table said otherwise. A condition now
    states its whole configuration, including the variables it needs
    ABSENT, and the version is deliberately unpinned so the step flags are
    what compose each condition.
    """

    NAMES = (skill_concepts.VERSION_ENV, skill_concepts.FLAG,
             skill_concepts.EVIDENCE_FLAG, skill_concepts.ROLES_FLAG)

    def __init__(self, concepts=False, evidence=False, roles=False,
                 **_rest):
        self.want = {skill_concepts.FLAG: concepts,
                     skill_concepts.EVIDENCE_FLAG: evidence,
                     skill_concepts.ROLES_FLAG: roles}

    def __enter__(self):
        self.before = {n: os.environ.get(n) for n in self.NAMES}
        # Unpinned on purpose: with a version pinned, an explicit version
        # is the complete answer and the step flags below would do
        # nothing. Removing it is what makes B, C and D reachable at all.
        os.environ.pop(skill_concepts.VERSION_ENV, None)
        # A bound profile would outrank both. Nothing here loads a
        # profile, and releasing it costs nothing if something later does.
        skill_concepts.bind(None)
        for name, on in self.want.items():
            if on:
                os.environ[name] = "1"
            else:
                os.environ.pop(name, None)
        return self

    def __exit__(self, *_exc):
        for name, value in self.before.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
        return False

    def effective(self):
        """What is actually in force inside this block."""
        return skill_concepts.effective()


def check_isolation():
    """Every condition, as it will actually run. Raises if any disagrees.

    Called before the run rather than trusted: the whole value of an A/B
    table is that the labels are true.
    """
    wrong = []
    for name, wanted in CONDITIONS.items():
        with Flags(**wanted) as flags:
            got = flags.effective()
            for key in ("concepts", "evidence", "roles"):
                if bool(got[key]) != bool(wanted.get(key)):
                    wrong.append(f"{name}: {key} is {got[key]}, "
                                 f"condition asks {wanted.get(key)}")
    if wrong:
        raise RuntimeError(
            "evaluation conditions are contaminated by the environment:\n  "
            + "\n  ".join(wrong))
    return True


# --------------------------------------------------------------------------
# The fixed input
# --------------------------------------------------------------------------

def resume_text(slug, person, out_dir):
    """The person's résumé, through the real PDF path.

    Rendered and extracted exactly as production would: a PDF's own
    spacing damage ("L WC") is part of what is being measured, and
    hand-writing clean text would evaluate a document nobody uploads.
    """
    import resume_parser
    stem = os.path.join(out_dir, f"{slug}-plain")
    if not os.path.exists(stem + ".pdf"):
        saved = render.PEOPLE
        try:
            render.PEOPLE = dict(saved, **{slug: person})
            render.build(slugs=[slug], layouts=("plain",), out_dir=out_dir)
        finally:
            render.PEOPLE = saved
    return resume_parser.extract_text(stem + ".pdf")


def extraction(person):
    """The model's answer, held constant across every condition.

    Taken from the persona's own truth rather than from a model call: the
    experiment is about what happens AFTER extraction, and a model run
    per condition would put sampling noise in every column.
    """
    return {
        "skill_weights": [{"term": s, "weight": skill_scan.NEUTRAL_WEIGHT}
                          for s in sorted(person["skills"])],
        "role_keywords": [], "penalty_terms": [],
        "domain_half_a": [], "domain_half_b": [], "domain_title_terms": [],
        "domain_bonus": 0, "title_hints": [], "title_exclude": [],
        "field_summary": person.get("headline", ""), "notes": "",
        "years_experience": person["years_experience"],
        "candidate_name": person["name"],
    }


def person_for_search(person):
    """{skills, employment} as local_search.fields_for wants it."""
    rows = []
    for job in person["employment"]:
        rows.append({"company": job["company"], "title": job["title"],
                     "start": job.get("start") or "", "end": job.get("end") or "present",
                     "relevant": job.get("relevant", True)})
    return {"skills": sorted(person["skills"]), "employment": rows}


# --------------------------------------------------------------------------
# One condition, one person
# --------------------------------------------------------------------------

def run_one(slug, person, text, market, condition, out_dir):
    """Weights, queries and timings for one cell of the matrix."""
    result = {"slug": slug, "condition": condition, "errors": []}
    started = time.perf_counter()
    with Flags(**CONDITIONS[condition]) as flags:
        # What was ACTUALLY in force, recorded per cell. An archived
        # comparison is only auditable if it says what ran.
        result["effective"] = flags.effective()
        try:
            data = make_profile.split_compounds(extraction(person),
                                                log=lambda *a: None)
            data = skill_scan.widen(data, text, vocab=market.vocab,
                                    log=lambda *a: None)
            importance = None
            if skill_concepts.evidence_enabled():
                weights = {e["term"].strip().lower(): e["weight"]
                           for e in data["skill_weights"]}
                concepts = skill_concepts.from_weights(weights)
                freqs, _source = corpus_signal.market_signal(out_dir)
                seps = {}
                for concept in concepts:
                    measured = [corpus_signal.separation(t, freqs)
                                for t in (concept.id,) + concept.raw]
                    measured = [m for m in measured if m is not None]
                    seps[concept.id] = max(measured) if measured else None
                importance = skill_evidence.assess_all(concepts, text, seps)
                if CONDITIONS[condition].get("keep_v1_weights"):
                    # D only: tiers exist so role families can be gated,
                    # and the weights stay exactly v1's, so any difference
                    # in this column is the ROLE change alone.
                    blended, _moved = corpus_signal.reweight(weights, freqs)
                    data = dict(data, skill_weights=[
                        {"term": e["term"],
                         "weight": blended.get(e["term"].strip().lower(),
                                               e["weight"])}
                        for e in data["skill_weights"]])
                else:
                    data = dict(data, skill_weights=[
                        {"term": e["term"],
                         "weight": next(
                             (r["weight"] for r in importance
                              if r["id"] == skill_concepts.resolve(e["term"])),
                             e["weight"])}
                        for e in data["skill_weights"]])
                data["skill_importance"] = importance
            else:
                freqs, _source = corpus_signal.market_signal(out_dir)
                blended, _moved = corpus_signal.reweight(
                    {e["term"].strip().lower(): e["weight"]
                     for e in data["skill_weights"]}, freqs)
                data = dict(data, skill_weights=[
                    {"term": e["term"],
                     "weight": blended.get(e["term"].strip().lower(),
                                           e["weight"])}
                    for e in data["skill_weights"]])

            fields = local_search.fields_for(
                person_for_search(person), market, importance=importance,
                resume_text=text)
            result["weights"] = {e["term"]: e["weight"]
                                 for e in data["skill_weights"]}
            result["queries"] = list(fields.get("role_keywords") or [])
            result["families"] = fields.get("role_families") or []
            result["importance"] = importance or []
        except Exception as exc:                      # noqa: BLE001
            # Recorded, never swallowed. A condition that crashes on a
            # persona is a result about that condition.
            result["errors"].append(f"{type(exc).__name__}: {exc}")
            result["weights"], result["queries"] = {}, []
            result["families"], result["importance"] = [], []
    result["ms"] = (time.perf_counter() - started) * 1000
    return result


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def concept_recall(result, person):
    """Did every skill the résumé states survive to the profile?

    Judged against people.py's own list, which predates v2. Compared as
    CONCEPTS, so "node"/"node.js" counts once on both sides — the point
    of canonicalisation is that those were ever two things.
    """
    want = {skill_concepts.resolve(s) for s in person["skills"]}
    got = {skill_concepts.resolve(t) for t in result["weights"]}
    return {"want": len(want), "found": len(want & got),
            "missing": sorted(want - got),
            "recall": len(want & got) / len(want) if want else None}


def unknown_recall(result, person):
    """The same, restricted to tools no alias table has heard of."""
    want = {skill_concepts.resolve(s) for s in person["skills"]
            if skill_concepts._key(s) not in skill_concepts.LOOKUP}
    got = {skill_concepts.resolve(t) for t in result["weights"]}
    return {"want": len(want), "found": len(want & got),
            "missing": sorted(want - got),
            "recall": len(want & got) / len(want) if want else None}


def role_verdicts(queries, labels):
    """Each query against the AUTHORED role labels.

    A query is WRONG when it names a profession the persona's label calls
    a miss; CREDIBLE when it contains a stem the label allows; UNJUDGED
    otherwise, and unjudged is reported rather than counted as a pass.
    """
    out = []
    for query in queries:
        low = query.lower()
        wrong = [w for w in labels["wrong"] if w in low]
        credible = [c for c in labels["credible"] if c in low]
        verdict = "wrong" if wrong else ("credible" if credible else "unjudged")
        out.append({"query": query, "verdict": verdict,
                    "matched": wrong or credible})
    return out


def score_of(text, weights):
    """What a job description scores against this profile, both ways."""
    if skill_concepts.enabled():
        return skill_concepts.score(text.lower(),
                                    skill_concepts.from_weights(weights))[0]
    return sum(w for t, w in weights.items()
               if skill_concepts.compile_alias(t).search(text.lower()))


# --------------------------------------------------------------------------
# The eight invariants that gate the migration
# --------------------------------------------------------------------------

def invariants(market, out_dir):
    """Each known failure, re-run as a check. (name, passed, detail)."""
    checks = []

    def add(name, passed, detail):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    # 1 — aliases increase score by duplication
    with Flags(concepts=True):
        one = {"node.js": 3}
        many = {"node.js": 3, "node": 3, "nodejs": 3, "node js": 3}
        text = "node.js and node experience required"
        add("aliases cannot inflate a score",
            score_of(text, one) == score_of(text, many) == 3,
            f"one spelling {score_of(text, one)}, four {score_of(text, many)}")

        fcm = {"firebase fcm": 3, "fcm": 5, "firebase": 4}
        add("Firebase FCM scores once",
            score_of("we use firebase fcm", fcm) == 5,
            f"{score_of('we use firebase fcm', fcm)} (was 12 in v1)")

        # 2 — formatting breaks matching
        js = score_of("strong javascript required", {"javascript (es6+)": 3})
        lwc = score_of("lwc and apex", {"l wc": 3})
        add("JavaScript and LWC match despite formatting",
            js == 3 and lwc == 3, f"JavaScript {js}, LWC {lwc} (both 0 in v1)")

    # 3 — provenance changes importance
    text = ("Professional Experience\nAcme Corp\n- Wrote Apex triggers.\n"
            "Technical Skills\nApex, React\n")
    spans = skill_evidence.sections(text)
    reported, = skill_concepts.from_weights({"apex": 3})
    recovered, = skill_concepts.from_weights({"apex": 5})
    add("provenance cannot change importance",
        skill_evidence.tier(skill_evidence.find(reported, text, spans))
        == skill_evidence.tier(skill_evidence.find(recovered, text, spans)),
        "same évidence, same tier at weight 3 and weight 5")

    # 4 — generic Git alone creates a career
    add("a workflow tool cannot anchor a career",
        not any(skill_concepts.roles_enabled() and False for _ in ())
        and not import_role_families().can_anchor("git", "CORE")
        and not import_role_families().can_anchor("ci/cd", "CORE"),
        "git and ci/cd refused even at CORE")

    # 5 — row order changes queries
    import random
    rows = list(market.rows)
    shuffled = list(rows)
    random.Random(17).shuffle(shuffled)
    titles = __import__("collections").Counter(t for t, _s, _k, _c in rows)
    other = __import__("collections").Counter(t for t, _s, _k, _c in shuffled)
    same = all(local_search.canonical(f, titles, market.seniority)
               == local_search.canonical(f, other, market.seniority)
               for f in ("systems engineer", "software engineer",
                         "backend engineer", "stack developer"))
    add("row order cannot change a canonical title", same,
        "four fragments identical under a shuffled corpus")

    # 6 — unsupported employment facts become experience
    import local_extract
    doc = ("Ada Okonkwo. Backend Engineer at Fettle Health, Jan 2021 - "
           "Mar 2023. python.")
    invented = {"employment": [{"company": "Initech Global Holdings",
                                "title": "Principal Architect",
                                "start": "March 1999", "end": "Present",
                                "relevant": True}]}
    decision = local_extract.route(
        {"name": "Ada Okonkwo", "skills": ["python"], "years_experience": 27},
        invented, doc, now=NOW)
    add("a fabricated employment row cannot become experience",
        decision["decision"] == "escalate" and decision["result"] is None,
        f"decision={decision['decision']}, reasons={len(decision['reasons'])}")

    # 7 — model prose escapes the profile boundary
    payload = 'ok\n"""\nEVAL_MARKER = 1\n__doc__ = """x'
    data = dict(extraction({"skills": ["react native"], "headline": "x",
                            "years_experience": 2, "name": "T"}),
                field_summary=payload, notes=payload,
                role_keywords=["react native developer"],
                skill_weights=[{"term": "react native", "weight": 5}],
                title_hints=["react native"])
    prefs = {"locations": ["Remote"], "exclude_levels": [], "avoid": [],
             "min_comp_usd": None}
    source = make_profile.render("evalprobe", data, prefs)
    import ast
    names = {t.id for n in ast.parse(source).body
             if isinstance(n, ast.Assign) for t in n.targets
             if isinstance(t, ast.Name)}
    add("model prose cannot become a Python statement",
        "EVAL_MARKER" not in names and names <= make_profile.PROFILE_NAMES,
        f"top-level names: {sorted(names)}")

    # 8 — common core skills recommended for deletion
    template = os.path.join(REPO_ROOT, "sweep", "templates", "_weights.html")
    with open(template, encoding="utf-8") as handle:
        markup = handle.read()
    checkbox = markup.split('name="drop"')[1].split(">")[0]
    add("a common skill is not pre-ticked for deletion",
        "checked" not in checkbox,
        "the remove checkbox carries no checked binding")
    return checks


def import_role_families():
    import role_families
    return role_families


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def evaluate(out_dir=None, resumes=None):
    """Every condition over every persona, with the metrics."""
    # Before anything is measured: a table whose labels are not true is
    # worse than no table.
    check_isolation()
    out_dir = out_dir or os.environ.get("SWEEP_EVAL_OUT") or None
    resumes = resumes or os.path.join(HERE, "resumes")
    people = eval_people.all_people()
    market = local_search.Market()
    texts = {slug: resume_text(slug, person, resumes)
             for slug, person in people.items()}

    cells, failures = {}, []
    for condition in CONDITIONS:
        for slug, person in people.items():
            cell = run_one(slug, person, texts[slug], market, condition,
                           out_dir)
            cells[(condition, slug)] = cell
            for error in cell["errors"]:
                failures.append({"condition": condition, "slug": slug,
                                 "error": error})

    report = {"conditions": {}, "failures": failures,
              "people": len(people),
              "corpus": {"rows": market.total,
                         "source": local_search.market_rows(out_dir)[1],
                         "frozen_titles_sha256": _sha(
                             local_search.frozen_path()),
                         "frozen_skills_sha256": _sha(
                             os.path.join(REPO_ROOT, "data",
                                          "skill_market_frequencies.json"))},
              "revision": _revision(),
              "isolation_checked": True,
              "text_chars": {slug: len(t) for slug, t in texts.items()}}

    for condition in CONDITIONS:
        rows = [cells[(condition, slug)] for slug in people]
        recalls, unknowns, verdicts, latencies = [], [], [], []
        per_person = {}
        for slug, person in people.items():
            cell = cells[(condition, slug)]
            rec = concept_recall(cell, person)
            unk = unknown_recall(cell, person)
            verd = role_verdicts(cell["queries"], eval_people.ROLES[slug])
            recalls.append(rec)
            unknowns.append(unk)
            verdicts.extend(verd)
            latencies.append(cell["ms"])
            per_person[slug] = {
                "stratum": eval_people.ROLES[slug]["stratum"],
                "concepts": len(cell["weights"]),
                "recall": rec["recall"], "missing": rec["missing"],
                "queries": cell["queries"],
                "verdicts": verd, "ms": round(cell["ms"], 1),
                "errors": cell["errors"],
            }
        wrong = [v for v in verdicts if v["verdict"] == "wrong"]
        credible = [v for v in verdicts if v["verdict"] == "credible"]
        unjudged = [v for v in verdicts if v["verdict"] == "unjudged"]
        found = sum(r["found"] for r in recalls)
        want = sum(r["want"] for r in recalls)
        ufound = sum(u["found"] for u in unknowns)
        uwant = sum(u["want"] for u in unknowns)
        report["conditions"][condition] = {
            "label": CONDITIONS[condition]["label"],
            "effective": cells[(condition, next(iter(people)))]["effective"],
            "concept_recall": found / want if want else None,
            "concepts_missing": want - found,
            "unknown_recall": ufound / uwant if uwant else None,
            "queries": len(verdicts),
            "wrong_career_queries": len(wrong),
            "wrong_rate": len(wrong) / len(verdicts) if verdicts else None,
            "credible": len(credible), "unjudged": len(unjudged),
            "people_with_no_queries": sum(
                1 for slug in people if not cells[(condition, slug)]["queries"]),
            "errors": sum(len(cells[(condition, s)]["errors"]) for s in people),
            "p50_ms": round(statistics.median(latencies), 1),
            "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1),
            "worst": sorted(
                ({"slug": v["query"], "matched": v["matched"]} for v in wrong),
                key=lambda r: r["slug"])[:8],
            "per_person": per_person,
        }

    report["invariants"] = invariants(market, out_dir)
    report["stability"] = stability(people, texts, market, out_dir)
    return report


def stability(people, texts, market, out_dir):
    """Do the same rows in a different order give the same queries?"""
    import random
    shuffled = list(market.rows)
    random.Random(29).shuffle(shuffled)
    other = local_search.Market(rows=shuffled, seniority=market.seniority)
    same, differ = 0, []
    for condition in ("A", "E"):
        for slug, person in people.items():
            first = run_one(slug, person, texts[slug], market, condition,
                            out_dir)["queries"]
            again = run_one(slug, person, texts[slug], other, condition,
                            out_dir)["queries"]
            if first == again:
                same += 1
            else:
                differ.append({"condition": condition, "slug": slug,
                               "before": first, "after": again})
    return {"checked": len(people) * 2, "identical": same,
            "differences": differ}


def demo():
    """Self-check on two people and two conditions. Offline, no model."""
    people = eval_people.all_people()
    market = local_search.Market()
    assert market.total, "the evaluation needs a corpus"
    person = people["priya"]
    text = resume_text("priya", person,
                       os.environ.get("SWEEP_EVAL_RESUMES")
                       or os.path.join(HERE, "resumes"))
    assert "React Native" in text or "react native" in text.lower(), text[:200]
    a = run_one("priya", person, text, market, "A", None)
    e = run_one("priya", person, text, market, "E", None)
    assert not a["errors"] and not e["errors"], (a["errors"], e["errors"])
    assert a["weights"] and e["weights"]
    # The mobile specialist must not be sent to a different profession.
    labels = eval_people.ROLES["priya"]
    for verdict in role_verdicts(e["queries"], labels):
        assert verdict["verdict"] != "wrong", verdict
    checks = invariants(market, None)
    assert len(checks) >= 8, len(checks)
    assert all(c["passed"] for c in checks), [c for c in checks if not c["passed"]]
    print(f"evaluate demo ok — {len(people)} people, {len(CONDITIONS)} "
          f"conditions, {len(checks)} invariants")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--demo" in argv:
        demo()
        return 0
    report = evaluate()
    where = os.environ.get("SWEEP_EVAL_REPORT")
    if where:
        with open(where, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1)
    print(json.dumps({k: v for k, v in report.items()
                      if k != "conditions"}, indent=1)[:2000])
    for name, row in report["conditions"].items():
        print(f"\n{name}  {row['label']}")
        print(f"   concept recall {row['concept_recall']:.3f}  "
              f"missing {row['concepts_missing']}  "
              f"queries {row['queries']}  wrong {row['wrong_career_queries']}"
              f"  unjudged {row['unjudged']}  "
              f"p50 {row['p50_ms']}ms  errors {row['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
