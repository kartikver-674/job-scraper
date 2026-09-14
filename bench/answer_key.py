"""Score BOTH backends against the answer key, not against each other.

WHY THE LOCAL BASELINE STOPPED BEING THE ORACLE
-----------------------------------------------
The equivalence gate asked "does Modal reproduce local-direct?". Twice now
local-direct has been the side that was wrong:

  Sarthak       local repeats the one DealerMatix role (8/8 runs); cold
                Modal emits it once (7/7), which is what the résumé says.
  dmitri-plain  local swaps company and title on both rows (4/4); cold
                Modal matches bench/people.py (5/5). The swap reaches
                role_keywords, SEARCH.role_keywords and the himalayas feed
                queries: local searches for company names.

Requiring Modal to match local would reward a known defect. So for the 52
synthetic documents, whose ground truth is the dict that generated them,
each backend is scored against that truth, and the backend-vs-backend
comparison is kept alongside rather than instead.

WHERE THE EXPECTED VALUES COME FROM
-----------------------------------
Source fields — employment rows, skills, titles, companies — come straight
from bench/people.py, scored with bench/score.py's existing rule (exact
after lowercase/punctuation folding; never fuzzy).

Derived fields — years, months, SEARCH/SETTINGS limits, role_keywords, feed
queries, title gates, SCORING — are NOT re-implemented here. The answer key
is fed to production in place of the two model calls, and the same
observe() the backends go through runs everything after them: grounding,
the router, the years arithmetic, local_search, skill widening, re-weighting
and render(). "Correct" for a derived field means "what production does
with a perfect extraction of this document".

The derived comparison uses the gate's own normalize(), including the one
approved rule (exact duplicate employment rows). No other rule is added.

    python -m bench.backends --answer-key --all --url ... --endpoint-state ...
"""

import copy
from unittest.mock import patch

import local_extract as le
from bench import score as scoring
from bench.people import PEOPLE, truth

CATEGORIES = ("both correct", "local correct / Modal wrong",
              "local wrong / Modal correct", "both wrong, same way",
              "both wrong, differently")

# (group, search/filter-driving?, why). Source groups are scored against
# bench/people.py directly.
SOURCE = (
    ("employment rows", True,
     "which roles exist decides the held titles and the months counted"),
    ("employment company", False,
     "not read downstream — grounding and display only"),
    ("employment title", True,
     "held titles become role_keywords and feed queries"),
    ("employment dates", True, "months_from counts them into years_experience"),
    ("relevant-role classification", True,
     "decides which rows count and which titles are held"),
    ("skills", True, "local_search, skill widening and SCORING weights"),
    ("titles (fields call)", False,
     "grounding only — held titles come from employment rows"),
    ("companies (fields call)", False, "grounding only"),
    ("name", False, "display"),
    ("education", False, "not consumed"),
    ("institutions", False, "not consumed"),
    ("projects", False, "not consumed"),
    ("certifications", False, "not consumed"),
)
SET_GROUPS = {"skills": "skills", "titles (fields call)": "titles",
              "companies (fields call)": "companies", "education": "education",
              "institutions": "institutions", "projects": "projects",
              "certifications": "certifications"}

# (group, driving?, observation paths, why). Derived groups are scored
# against production's own output for the answer key.
DERIVED = (
    ("years_experience", True, ("profile.years_experience",),
     "becomes SEARCH.experience_years and max_experience_years"),
    ("experience_months", False, ("profile.experience_months",),
     "display only — render() ignores it"),
    ("search experience limits", True,
     ("config.SEARCH.experience_years", "config.SETTINGS.max_experience_years"),
     "LinkedIn seniority band and the posting-floor drop"),
    ("role_keywords", True,
     ("profile.role_keywords", "config.SEARCH.role_keywords"),
     "the paid search queries, in budget order"),
    ("feed queries", True, ("config.FEEDS",), "free-feed queries"),
    ("title/search hints", True,
     ("profile.title_hints", "profile.title_exclude",
      "config.ATS_TITLE_HINTS", "config.ATS_TITLE_EXCLUDE"),
     "title gates applied to every listing"),
    ("skill-driven scoring", True, ("profile.skill_weights", "config.SCORING"),
     "how every listing is scored and dropped"),
    ("rendered config (all)", True, ("config",),
     "every assignment the scraper imports"),
    ("ranking provenance", False,
     ("profile.local_ranking", "profile.local_from_orphans"),
     "provenance — render() ignores it; role_keywords carries its effect"),
    ("escalation", True, ("@failure",),
     "an escalated profile is no profile at all"),
)

# Backend-vs-backend diff path -> scored group, first prefix match wins.
# None means the path is not in the answer key; the reason says why that
# is safe (it never reaches the rendered configuration).
PATH_GROUPS = (
    ("employment.employment", "@employment", None),
    ("employment.target_field", None,
     "not in the answer key; only colours display text (field_summary, notes)"),
    ("extracted.skills", "skills", None),
    ("extracted.titles", "titles (fields call)", None),
    ("extracted.companies", "companies (fields call)", None),
    ("extracted.name", "name", None),
    ("extracted.education", "education", None),
    ("extracted.institutions", "institutions", None),
    ("extracted.projects", "projects", None),
    ("extracted.certifications", "certifications", None),
    ("extracted.years_experience", "years_experience", None),
    ("extracted.experience_months", "experience_months", None),
    ("decision.decision", "@decision", None),
    ("decision.", None,
     "router provenance — records the model's own stated years, which "
     "production never uses"),
    ("profile.years_experience", "years_experience", None),
    ("profile.experience_months", "experience_months", None),
    ("profile.role_keywords", "role_keywords", None),
    ("profile.title_hints", "title/search hints", None),
    ("profile.title_exclude", "title/search hints", None),
    ("profile.skill_weights", "skill-driven scoring", None),
    ("profile.local_ranking", "ranking provenance", None),
    ("profile.local_from_orphans", "ranking provenance", None),
    ("profile.candidate_name", "name", None),
    ("profile.", None,
     "display/provenance text; anything render() turns into configuration "
     "is scored under config"),
    ("config.SEARCH.role_keywords", "role_keywords", None),
    ("config.SEARCH.experience_years", "search experience limits", None),
    ("config.SETTINGS.max_experience_years", "search experience limits", None),
    ("config.FEEDS", "feed queries", None),
    ("config.ATS_TITLE", "title/search hints", None),
    ("config.SCORING", "skill-driven scoring", None),
    ("config", "rendered config (all)", None),
    ("failure", "escalation", None),
)
EMPLOYMENT_GROUPS = ("employment rows", "employment company", "employment title",
                     "employment dates", "relevant-role classification")


def person_of(slug):
    """'dmitri-plain' -> 'dmitri'; only synthetic documents have a key."""
    person = slug.rsplit("-", 1)[0]
    if person not in PEOPLE:
        raise ValueError(f"{slug!r} is not a synthetic benchmark document")
    return person


def key_answers(person):
    """The answer key, expressed in the two extraction schemas.

    What a perfect extractor following FIELDS_PROMPT and EMPLOYMENT_PROMPT
    would return. Two contract points and nothing else: skills are
    lowercase because FIELDS_PROMPT asks for them lowercase, and
    target_field is empty because the key does not state one — it only
    colours display text, which is not scored.
    """
    t = truth(person)
    fields = {"name": t["name"], "years_experience": t["years_experience"],
              "titles": list(t["titles"]),
              "skills": [s.lower() for s in t["skills"]],
              "companies": list(t["companies"]),
              "education": list(t["education"]),
              "institutions": list(t["institutions"]),
              "projects": list(t["projects"]),
              "certifications": list(t["certifications"])}
    employment = {"target_field": "",
                  "employment": copy.deepcopy(t["employment_rows"])}
    return fields, employment


def key_observation(text, person, market, frequencies, now, prefs=None):
    """What production does with a PERFECT extraction of this document.

    The two model calls are replaced by the answer key; everything after
    them is the production code, through the same observe() the backends
    use. Nothing here re-derives a Sweep value by hand.
    """
    from bench import backends

    fields, employment = key_answers(person)
    with patch.object(le, "extract",
                      lambda *a, **k: (copy.deepcopy(fields), 0.0)), \
            patch.object(le, "employment",
                         lambda *a, **k: copy.deepcopy(employment)):
        observed, _seconds = backends.observe(text, "local-direct", market,
                                              frequencies, now=now, prefs=prefs)
    return observed


def _get(obs, path):
    from bench import backends

    cur = obs
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return backends.MISSING
        cur = cur[part]
    return cur


def _same_path(a_obs, b_obs, path, now):
    """Equal under the gate's own normalize() — no rule of our own."""
    from bench import backends

    if path == "@failure":
        return ("failure" in a_obs) == ("failure" in b_obs)
    a, b = _get(a_obs, path), _get(b_obs, path)
    if a is backends.MISSING or b is backends.MISSING:
        return a is b
    parts = tuple(path.split("."))
    return not list(backends.differences(backends.normalize(a, parts, now),
                                         backends.normalize(b, parts, now)))


def _view(obs, now):
    """What source scoring reads from one observation."""
    from bench import backends

    rows = (obs.get("employment") or {}).get("employment")
    extracted = obs.get("extracted")
    return {
        # The gate's normalization: dates as parsed months, and the one
        # approved rule — exact duplicate rows removed, first kept.
        "rows": (backends.normalize(rows, backends.EMPLOYMENT_ROWS, now)
                 if isinstance(rows, list) else None),
        "sets": ({g: extracted.get(f) for g, f in SET_GROUPS.items()}
                 if isinstance(extracted, dict) else None),
        "name": extracted.get("name") if isinstance(extracted, dict) else None,
    }


def _truth_view(person, now):
    from bench import backends

    t = truth(person)
    fields, employment = key_answers(person)
    return {"rows": backends.normalize(employment["employment"],
                                       backends.EMPLOYMENT_ROWS, now),
            "sets": {g: t[f] for g, f in SET_GROUPS.items()},
            "name": t["name"]}


def _source_verdicts(view, ref):
    """{group: bool} for one view against a reference view."""
    out = {}
    rows, want = view["rows"], ref["rows"]
    if rows is None or want is None:
        for group in EMPLOYMENT_GROUPS:
            out[group] = rows is want
    else:
        same_len = len(rows) == len(want)
        pairs = list(zip(rows, want))

        def every(check):
            return same_len and all(check(a, b) for a, b in pairs)

        out["employment rows"] = same_len
        out["employment company"] = every(lambda a, b: scoring.norm(
            a.get("company", "")) == scoring.norm(b.get("company", "")))
        out["employment title"] = every(lambda a, b: scoring.norm(
            a.get("title", "")) == scoring.norm(b.get("title", "")))
        out["employment dates"] = every(
            lambda a, b: (a.get("start"), a.get("end")) == (b.get("start"), b.get("end")))
        # Production's own reading: an absent flag means relevant.
        out["relevant-role classification"] = every(
            lambda a, b: le.is_relevant(a) == le.is_relevant(b))
    for group in SET_GROUPS:
        got = view["sets"][group] if view["sets"] else None
        exp = ref["sets"][group] if ref["sets"] else None
        out[group] = (got is exp if got is None or exp is None
                      else scoring.prf(got, exp)[2] == 1.0)
    out["name"] = (view["name"] is not None and ref["name"] is not None
                   and scoring.norm(view["name"]) == scoring.norm(ref["name"]))
    return out


def category(local_ok, modal_ok, same):
    if local_ok and modal_ok:
        return "both correct"
    if local_ok:
        return "local correct / Modal wrong"
    if modal_ok:
        return "local wrong / Modal correct"
    return "both wrong, same way" if same else "both wrong, differently"


def _shown(value, limit=140):
    text = repr(value)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def score_document(local, modal, key, person, now):
    """Every scored group for one document, with its category."""
    groups = {}
    local_v, modal_v, truth_v = _view(local, now), _view(modal, now), _truth_view(person, now)
    local_s = _source_verdicts(local_v, truth_v)
    modal_s = _source_verdicts(modal_v, truth_v)
    same_s = _source_verdicts(local_v, modal_v)
    for group, driving, why in SOURCE:
        entry = {"kind": "source", "driving": driving, "why": why,
                 "local_ok": local_s[group], "modal_ok": modal_s[group],
                 "same": same_s[group]}
        entry["category"] = category(entry["local_ok"], entry["modal_ok"], entry["same"])
        if entry["category"] != "both correct":
            if group in EMPLOYMENT_GROUPS:
                attr = {"employment rows": None, "employment company": "company",
                        "employment title": "title", "employment dates": ("start", "end"),
                        "relevant-role classification": "relevant"}[group]

                def pick(rows):
                    if rows is None:
                        return None
                    if attr is None:
                        return len(rows)
                    if isinstance(attr, tuple):
                        return [tuple(r.get(x) for x in attr) for r in rows]
                    return [r.get(attr) for r in rows]
                entry["values"] = {"key": pick(truth_v["rows"]), "local": pick(local_v["rows"]),
                                   "modal": pick(modal_v["rows"])}
            elif group in SET_GROUPS:
                exp = truth_v["sets"][group]
                entry["values"] = {
                    side: ({"missing": scoring.prf(v["sets"][group], exp)[3],
                            "spurious": scoring.prf(v["sets"][group], exp)[4]}
                           if v["sets"] else None)
                    for side, v in (("local", local_v), ("modal", modal_v))}
            else:
                entry["values"] = {"key": truth_v["name"], "local": local_v["name"],
                                   "modal": modal_v["name"]}
        groups[group] = entry

    key_escalated = "failure" in key
    for group, driving, paths, why in DERIVED:
        entry = {"kind": "derived", "driving": driving, "why": why}
        if key_escalated and group != "escalation":
            # Production refuses a PERFECT extraction of this document, so
            # there is no expected profile to score against.
            entry.update(local_ok=None, modal_ok=None, same=None,
                         category="unscorable: the answer key itself escalates")
        else:
            entry["local_ok"] = all(_same_path(local, key, p, now) for p in paths)
            entry["modal_ok"] = all(_same_path(modal, key, p, now) for p in paths)
            entry["same"] = all(_same_path(local, modal, p, now) for p in paths)
            entry["category"] = category(entry["local_ok"], entry["modal_ok"], entry["same"])
            if entry["category"] != "both correct" and paths[0] != "@failure":
                entry["values"] = {side: _shown(_get(obs, paths[0]))
                                   for side, obs in (("key", key), ("local", local),
                                                     ("modal", modal))}
        groups[group] = entry

    scorable = {g: e for g, e in groups.items() if e["local_ok"] is not None}
    driving = {g: e for g, e in scorable.items() if e["driving"]}
    return {
        "groups": groups,
        "key_escalated": key_escalated,
        "local_driving_correct": all(e["local_ok"] for e in driving.values()),
        "modal_driving_correct": all(e["modal_ok"] for e in driving.values()),
        "local_all_correct": all(e["local_ok"] for e in scorable.values()),
        "modal_all_correct": all(e["modal_ok"] for e in scorable.values()),
        "regressions": [g for g, e in driving.items()
                        if e["category"] == "local correct / Modal wrong"],
    }


def _group_for(path, row):
    for prefix, group, reason in PATH_GROUPS:
        if path == prefix or path.startswith(prefix):
            return group, reason
    return None, "UNKNOWN path — not mapped to the answer key"


def classify_disagreements(row):
    """Every backend-vs-backend diff, resolved against the answer key."""
    groups = row["scoring"]["groups"]
    out = []
    for diff in row.get("diffs", []):
        path = diff["path"]
        group, reason = _group_for(path, row)
        entry = {"path": path, "backend_classification": diff["classification"],
                 "duplicate_only": diff.get("normalization") is not None}
        if group == "@employment":
            resolved = [(g, groups[g]["category"]) for g in EMPLOYMENT_GROUPS
                        if not groups[g]["same"]]
            entry.update(scored=True, groups=resolved or [
                ("employment rows", groups["employment rows"]["category"])])
        elif group == "@decision":
            values = (diff.get("local"), diff.get("remote"))
            if "escalate" in values:
                entry.update(scored=True, groups=[("escalation",
                                                   groups["escalation"]["category"])])
            else:
                entry.update(scored=False, reason=(
                    "accept vs corrected reflects the model's own stated years, "
                    "which production ignores; the computed years are scored"))
        elif group:
            entry.update(scored=True, groups=[(group, groups[group]["category"])])
        else:
            entry.update(scored=False, reason=reason)
        out.append(entry)
    return out


def describe(row, log=print):
    """One document's verdict, and every group that is not both-correct."""
    s = row["scoring"]
    relation = ("identical" if row["exact_match"] else
                "downstream-equivalent disagreement" if row["semantic_match"] else
                "behaviour-changing disagreement")
    log(f"  answer key: local {'CORRECT' if s['local_driving_correct'] else 'wrong'} / "
        f"Modal {'CORRECT' if s['modal_driving_correct'] else 'wrong'} on every "
        f"search/filter-driving field; backends: {relation}"
        + ("; the answer key itself escalates" if s["key_escalated"] else ""))
    for group, e in s["groups"].items():
        if e["category"] == "both correct":
            continue
        tag = "DRIVING" if e["driving"] else "non-driving"
        log(f"    {group:30} {tag:11} {e['category']}")
        if e.get("values"):
            log(f"        {e['values']}")
    for g in s["regressions"]:
        log(f"    !! REGRESSION: {g} — local matches the answer key and Modal does not")


def summarise(report, log=print):
    """Totals, per-field correctness, regressions and the acceptance call."""
    rows = [r for r in report["rows"] if "scoring" in r]
    errors = [(r["resume"], r.get("error")) for r in report["rows"] if "scoring" not in r]

    def count(pred):
        return sum(1 for r in rows if pred(r))

    L = lambda r: r["scoring"]["local_driving_correct"]
    M = lambda r: r["scoring"]["modal_driving_correct"]
    La = lambda r: r["scoring"]["local_all_correct"]
    Ma = lambda r: r["scoring"]["modal_all_correct"]
    duplicate_only = [r["resume"] for r in rows
                      if r["semantic_match"] and not r["exact_match"] and r["diffs"]
                      and all(d.get("normalization") for d in r["diffs"])]
    totals = {
        "documents": report["requested"], "scored": len(rows),
        "errors": errors,
        "local_fully_correct": count(L), "modal_fully_correct": count(M),
        "both_fully_correct": count(lambda r: L(r) and M(r)),
        "local_only_correct": count(lambda r: L(r) and not M(r)),
        "modal_only_correct": count(lambda r: M(r) and not L(r)),
        "both_incorrect": count(lambda r: not L(r) and not M(r)),
        "raw_backend_exact_matches": count(lambda r: r["exact_match"]),
        "semantic_backend_matches": count(lambda r: r["semantic_match"]),
        "behaviour_changing_disagreements": [r["resume"] for r in rows
                                             if not r["semantic_match"]],
        "duplicate_only_benign_disagreements": duplicate_only,
        "all_fields": {"local": count(La), "modal": count(Ma),
                       "both": count(lambda r: La(r) and Ma(r))},
    }

    per_field = {}
    for group, driving, *_ in SOURCE + DERIVED:
        cats = {c: 0 for c in CATEGORIES}
        cats["unscorable"] = 0
        local_ok = modal_ok = 0
        for r in rows:
            e = r["scoring"]["groups"][group]
            if e["local_ok"] is None:
                cats["unscorable"] += 1
                continue
            cats[e["category"]] += 1
            local_ok += e["local_ok"]
            modal_ok += e["modal_ok"]
        per_field[group] = {"driving": driving, "local_correct": local_ok,
                            "modal_correct": modal_ok, **cats}

    regressions = [{"resume": r["resume"], "group": g,
                    "values": r["scoring"]["groups"][g].get("values")}
                   for r in rows for g in r["scoring"]["regressions"]]
    unscored = [{"resume": r["resume"], "path": d["path"], "reason": d["reason"]}
                for r in rows for d in r["disagreements"] if not d["scored"]]
    unknown = [u for u in unscored if u["reason"].startswith("UNKNOWN")]
    local_total = sum(v["local_correct"] for v in per_field.values() if v["driving"])
    modal_total = sum(v["modal_correct"] for v in per_field.values() if v["driving"])
    criteria = {
        "modal at least as correct as local on driving fields":
            modal_total >= local_total,
        "no regression where local is correct and Modal wrong":
            not regressions,
        "every backend disagreement classifiable by the answer key":
            not unknown,
        "every requested document scored": len(rows) == report["requested"],
    }
    decision = {"qualifies": all(criteria.values()), "criteria": criteria,
                "driving_correct_totals": {"local": local_total, "modal": modal_total}}
    report["answer_key_summary"] = {"totals": totals, "per_field": per_field,
                                    "regressions": regressions,
                                    "unscored_disagreements": unscored,
                                    "key_escalations": [r["resume"] for r in rows
                                                        if r["scoring"]["key_escalated"]],
                                    "decision": decision}

    t = totals
    log("\n" + "=" * 78)
    log("ANSWER-KEY SCORING — local-direct and cold Modal, each against bench/people.py")
    log("=" * 78)
    log(f"  documents requested            {t['documents']}   scored {t['scored']}"
        f"{'   errors ' + str(t['errors']) if t['errors'] else ''}")
    log("  -- fully correct on every search/filter-driving field --")
    for label, key in (("local fully correct", "local_fully_correct"),
                       ("Modal fully correct", "modal_fully_correct"),
                       ("both fully correct", "both_fully_correct"),
                       ("local-only correct", "local_only_correct"),
                       ("Modal-only correct", "modal_only_correct"),
                       ("both incorrect", "both_incorrect")):
        log(f"  {label:30} {t[key]}")
    log(f"  (stricter, every scored field: local {t['all_fields']['local']}, "
        f"Modal {t['all_fields']['modal']}, both {t['all_fields']['both']})")
    log("  -- backend vs backend --")
    log(f"  raw exact matches              {t['raw_backend_exact_matches']}")
    log(f"  semantic matches               {t['semantic_backend_matches']}")
    log(f"  behaviour-changing             {len(t['behaviour_changing_disagreements'])} "
        f"{t['behaviour_changing_disagreements']}")
    log(f"  duplicate-only benign          {len(t['duplicate_only_benign_disagreements'])} "
        f"{t['duplicate_only_benign_disagreements']}")
    log("\n  per-field correctness (docs)       drv  local modal  both  L-only M-only same-wrong diff-wrong unscorable")
    for group, v in per_field.items():
        log(f"  {group:34} {'yes' if v['driving'] else 'no':>3} {v['local_correct']:>6} "
            f"{v['modal_correct']:>5} {v['both correct']:>5} "
            f"{v['local correct / Modal wrong']:>7} {v['local wrong / Modal correct']:>6} "
            f"{v['both wrong, same way']:>10} {v['both wrong, differently']:>10} "
            f"{v['unscorable']:>10}")
    if report["answer_key_summary"]["key_escalations"]:
        log(f"\n  answer key escalates in production (derived fields unscorable): "
            f"{report['answer_key_summary']['key_escalations']}")
    log(f"\n  REGRESSIONS (local correct, Modal wrong, driving): {len(regressions)}")
    for g in regressions:
        log(f"    {g['resume']} | {g['group']} | {g['values']}")
    if unscored:
        log(f"\n  backend disagreements not in the answer key: {len(unscored)}")
        for u in unscored:
            log(f"    {u['resume']} | {u['path']} | {u['reason']}")
    log("\n  ACCEPTANCE (synthetic corpus)")
    for label, ok in criteria.items():
        log(f"    [{'PASS' if ok else 'FAIL'}] {label}")
    log(f"    driving-field correct totals: local {local_total}, Modal {modal_total}")
    log(f"  => Modal {'QUALIFIES' if decision['qualifies'] else 'DOES NOT QUALIFY'}")
    return decision["qualifies"]
