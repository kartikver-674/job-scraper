"""One real résumé through the whole local pipeline, against Gemini's answer.

No sweep, no Apify, no spend, and nothing written outside the scratch
directory given on the command line. The rendered profile goes there, not
into profiles/.

The comparison is the point. profiles/sarthak_verma.py was generated from
auto-apply/resume/resume.pdf by Gemini through make_profile.py, in the
current schema, so the same document can go through the local pipeline
and the two answers can be put side by side field by field.

    python -m bench.dryrun                      # sarthak, the default pair
    python -m bench.dryrun --resume X --against profiles.Y
"""

import importlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

DEFAULT_RESUME = os.path.join(REPO_ROOT, "auto-apply", "resume", "resume.pdf")
DEFAULT_AGAINST = "profiles.sarthak_verma"


def local_profile(model, resume_text, prefs, log=print):
    """Every field, from local sources only. Returns (profile, timings)."""
    import config
    from bench import cold_start, dates as dating, derive_search as ds
    from bench import route, run as extract, search_fields, semantic

    timings, t0 = {}, time.time()

    def mark(name, since):
        timings[name] = time.time() - since
        log(f"    {name:<22} {timings[name]:>6.1f}s")
        return time.time()

    log("  running the local pipeline")
    mark_from = t0
    fields, _elapsed = extract.ask(model, resume_text)
    mark_from = mark("extract fields", mark_from)

    employment = dating.ask(model, resume_text)
    mark_from = mark("extract employment", mark_from)

    # Grounding and the years derivation, exactly as bench/route measures
    # them: nothing invented survives, and the number is computed.
    decision = route.route(fields, employment, resume_text)
    checked = decision["result"] or dict(fields)
    years = dating.years_from((employment or {}).get("employment") or [])
    checked["years_experience"] = years
    mark_from = mark("validate + derive", mark_from)

    hard = tuple(config.SCORING["hard_drop_terms"])
    soft = tuple(config.SCORING["soft_drop_terms"])
    seniority = hard + soft
    rows = ds.corpus_rows()
    idx = ds.index(rows, seniority)
    # Concept filler out before anything downstream sees it: these terms
    # feed both keyword retrieval and the weights that decide ranking.
    vocab = ds.vocabulary(rows)
    skills, filler = ds.clean_skills(checked.get("skills") or (), vocab=vocab)
    own = set(skills)
    if filler:
        log(f"    dropped {len(filler)} concept term(s): {filler}")
    person = {"skills": sorted(own),
              "employment": (employment or {}).get("employment") or []}
    # Canonicalised, because these are sent to LinkedIn and Indeed as
    # literal search strings and the ranked n-grams are not job titles.
    keywords = cold_start.validated(
        cold_start.from_resume(person, seniority)
        + ds.canonicalise(
            ds.keywords_for(own, rows, idx, len(rows), want=12,
                            seniority=seniority), rows, seniority),
        rows, seniority)
    mark_from = mark("derive keywords", mark_from)

    field = (employment or {}).get("target_field") or ""
    answer = semantic.ask_blind(model, field or "software engineering", own,
                                keywords)
    halves, _fixes = semantic.check_halves(answer, rows, seniority)
    mark_from = mark("semantic halves", mark_from)

    # RULE 3 wants twenty to forty. Taking only the keywords and their
    # component words gave thirteen, gating the free sources tighter than
    # config's own default.
    hints = ds.hints_for(own, rows, idx, len(rows), seniority)

    # skill_weights: the résumé supplies the terms, the corpus the numbers.
    import corpus_signal
    weights = {s: 3 for s in sorted(own)}
    _ = weights
    blended, _moved = corpus_signal.reweight(weights, corpus_signal.frequencies())
    mark_from = mark("weight against corpus", mark_from)

    profile = {
        "candidate_name": checked.get("name", ""),
        "field_summary": field,
        "years_experience": years,
        "role_keywords": keywords,
        "skill_weights": [{"term": t, "weight": w}
                          for t, w in sorted(blended.items())],
        "penalty_terms": [{"term": t, "weight": 6}
                          for t in prefs.get("avoid") or ()],
        "domain_half_a": halves["domain_half_a"],
        "domain_half_b": halves["domain_half_b"],
        "domain_title_terms": [k for k in keywords
                               if len(k.split()) >= 2][:12],
        # Measured at 46% wrong with four fifths unverifiable, and this
        # gate is checked before every other one. config.py's own base
        # ships zero entries; so does this.
        "title_exclude": [],
        "domain_bonus": halves["domain_bonus"],
        "title_hints": hints,
        "notes": "generated locally; title_exclude intentionally empty",
    }
    timings["total"] = time.time() - t0
    return profile, timings, decision


def gemini_profile(module_name):
    """The shipped Gemini answer, read back into the generated shape."""
    from bench.search_fields import from_config

    return from_config(importlib.import_module(module_name))


def _terms(value):
    """A comparable set from either shape a field can arrive in."""
    if isinstance(value, dict):
        return {str(k).strip().lower() for k in value}
    out = set()
    for item in value or ():
        if isinstance(item, dict):
            item = item.get("term", "")
        # str(None) is "None", which is truthy and would enter the set as
        # the literal term "none".
        if item is not None and str(item).strip():
            out.add(str(item).strip().lower())
    return out


def compare(local, gemini, rows, own, hard):
    """Field by field, with the market's opinion where it has one."""
    from bench import derive_search as ds

    fields = ["role_keywords", "title_hints", "title_exclude",
              "skill_weights", "penalty_terms", "domain_half_a",
              "domain_half_b", "domain_title_terms"]
    print(f"\n  {'field':<22}{'local':>7}{'gemini':>8}{'shared':>8}"
          f"{'only local':>12}{'only gemini':>13}")
    diffs = {}
    for name in fields:
        a, b = _terms(local.get(name)), _terms(gemini.get(name))
        shared = a & b
        diffs[name] = (sorted(a - b), sorted(b - a))
        print(f"  {name:<22}{len(a):>7}{len(b):>8}{len(shared):>8}"
              f"{len(a - b):>12}{len(b - a):>13}")

    print(f"\n  {'':<22}{'local':>10}{'gemini':>10}")
    for name, key in (("years_experience", "years_experience"),
                      ("domain_bonus", "domain_bonus")):
        print(f"  {name:<22}{str(local.get(key)):>10}{str(gemini.get(key)):>10}")

    # The metric that decides whether these are usable at all, and the
    # one this file did not have on its first run. role_keywords are sent
    # to LinkedIn and Indeed as LITERAL SEARCH STRINGS. Substring-matching
    # them against stored titles — which is what the row below does —
    # rewards an n-gram like "developer react native" that no board would
    # ever match, and flattered the local pipeline badly.
    import collections
    whole = collections.Counter(t for t, _s, _sk, _c in rows)
    print(f"\n  are these usable as queries? (exact job titles in the corpus)")
    for label, keys in (("local", local.get("role_keywords")),
                        ("gemini", gemini.get("role_keywords"))):
        keys = list(keys or [])
        real = [k for k in keys if whole.get(str(k).strip().lower(), 0)]
        share = f"{len(real)}/{len(keys)}" if keys else "-"
        print(f"    {label:<20}{share:>8}   "
              f"not a real title: "
              f"{[k for k in keys if k not in real][:5]}")

    # What each keyword set would actually buy, on the same corpus.
    print(f"\n  what the paid keywords would buy (same corpus, no spend):")
    print(f"  {'':<22}{'rows':>8}{'rel':>6}{'reach':>7}{'dropped':>9}"
          f"{'spend':>9}")
    for label, keys in (("local", local.get("role_keywords")),
                        ("gemini", gemini.get("role_keywords"))):
        got = ds.buys(keys or [], rows, own, hard)
        pct = lambda v: f"{v:.0%}" if v is not None else "-"
        print(f"  {label:<22}{got['listings']:>8}{pct(got['relevance']):>6}"
              f"{pct(got['reachable']):>7}{got['wasted']:>9}"
              f"{'$' + format(got['spend'], '.2f'):>9}")
    return diffs


def main():
    import config
    from bench import derive_search as ds

    args = sys.argv[1:]

    def opt(flag, default):
        return args[args.index(flag) + 1] if flag in args else default

    if "--demo" in args:
        return demo()
    resume = opt("--resume", DEFAULT_RESUME)
    against = opt("--against", DEFAULT_AGAINST)
    out_dir = opt("--out", os.environ.get("TMPDIR", "/tmp"))
    model = opt("--model", "qwen3:8b")

    from resume_parser import extract_text
    text = extract_text(resume)
    gemini = gemini_profile(against)
    prefs = {"locations": ["Delhi", "Bengaluru"], "avoid": [],
             "exclude_levels": ["intern", "fresher", "junior"]}

    print(f"\n{'=' * 74}\ndry run: {os.path.basename(resume)} "
          f"vs {against}\n{'=' * 74}")
    print(f"  {len(text)} characters of résumé; no sweep, no Apify, no spend")
    local, timings, decision = local_profile(model, text, prefs)
    print(f"    {'TOTAL':<22} {timings['total']:>6.1f}s")
    print(f"\n  grounding: {decision['decision']}"
          + (f" — {'; '.join(decision['reasons'])}" if decision["reasons"]
             else f", {len(decision['corrections'])} correction(s)"))

    rows = ds.corpus_rows()
    own = _terms(local.get("skill_weights"))
    hard = tuple(config.SCORING["hard_drop_terms"])
    diffs = compare(local, gemini, rows, own, hard)

    for name in ("role_keywords", "domain_half_a", "domain_half_b",
                 "domain_title_terms"):
        only_local, only_gemini = diffs[name]
        print(f"\n  {name}")
        print(f"    local only : {only_local[:10]}")
        print(f"    gemini only: {only_gemini[:10]}")

    os.makedirs(out_dir, exist_ok=True)
    written = os.path.join(out_dir, "local_profile.json")
    with open(written, "w", encoding="utf-8") as fh:
        json.dump({"profile": local, "timings": timings}, fh, indent=1)
    print(f"\n  wrote {written}")
    return local, gemini, timings


def demo():
    assert _terms([{"term": "React"}, {"term": " node "}]) == {"react", "node"}
    assert _terms({"React": 5, "Node": 3}) == {"react", "node"}
    assert _terms(["A", "", None]) == {"a"}
    assert _terms(None) == set()
    print("dryrun demo ok")


if __name__ == "__main__":
    main()
