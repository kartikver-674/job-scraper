"""The local pipeline over real résumés, measured without spending anything.

The synthetic corpus did its job and has run out of things to say: 13
people, curated single-cluster skill lists, and one mechanism firing for
one of them. Real résumés are messier in the ways that matter — three
professions this tool was not built for, seniority from one year to four,
niche stacks, and one person with three different résumés for the same
job history.

Nothing here calls Gemini and nothing here spends Apify credits. The
corpus is the measuring instrument, exactly as in bench/derive_search.py,
which means the numbers are a proxy for a live sweep and not a substitute
for one. What it can settle for free:

  coverage    how much of each person's vocabulary the market has seen
  queryable   are the keywords real job titles, or n-gram artifacts
  relevance   do the rows they buy want this person's skills
  spend       what a sweep would cost, at the cheapest paid rate
  consistency three résumés of one person should not produce three
              different search strategies

Résumé text is read from a directory of .txt files given on the command
line, which stays outside the repository: these are real people's names,
addresses and phone numbers, and none of it belongs in git.

    python -m bench.real_resumes --demo
    python -m bench.real_resumes --dir /path/to/texts
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

from bench import derive_search as ds

HERE = os.path.dirname(os.path.abspath(__file__))


def profile_for(model, text, log=print):
    """The whole local pipeline for one résumé. Returns (profile, seconds)."""
    from bench import cold_start, dates as dating, orphan_skills as osk
    from bench import route, run as extract, semantic

    started = time.time()
    rows, idx, vocab, seniority = osk.setup()
    total = len(rows)

    fields, _elapsed = extract.ask(model, text)
    employment = dating.ask(model, text)
    decision = route.route(fields, employment, text)
    checked = decision["result"] or dict(fields)
    years = dating.years_from((employment or {}).get("employment") or [])

    skills, filler = ds.clean_skills(checked.get("skills") or (), vocab=vocab)
    own = set(skills)
    person = {"skills": sorted(own),
              "employment": (employment or {}).get("employment") or []}

    base = cold_start.validated(
        cold_start.from_resume(person, seniority)
        + ds.canonicalise(
            ds.keywords_for(own, rows, idx, total, want=12,
                            seniority=seniority), rows, seniority),
        rows, seniority)

    # The orphan pass: a skill the market knows and these keywords do not
    # search for gets one of its own, chosen from corpus candidates.
    blocks = osk.blocks_for(own, base, rows, idx, total, seniority,
                            vocab=vocab)
    extra = []
    if blocks:
        try:
            answer = osk.ask(model, (employment or {}).get("target_field")
                             or "software engineering", blocks)
        except Exception as exc:                # noqa: BLE001 - logged
            answer = {"error": f"{type(exc).__name__}: {exc}"}
        kept, _refused = osk.accept(answer, blocks, rows, seniority)
        for _skill, title in kept:
            if any(title in got or got in title for got in base + extra):
                continue
            ok, _got = osk.worth_it(title, rows, own)
            if ok:
                extra.append(title)

    field = (employment or {}).get("target_field") or ""
    return {
        "name": checked.get("name", ""),
        "field": field,
        "years": years,
        "skills": sorted(own),
        "filler_dropped": filler,
        "keywords": base + extra,
        "from_orphans": extra,
        "hints": ds.hints_for(own, rows, idx, total, seniority),
        "grounding": decision["decision"],
    }, time.time() - started


def run(model, directory, cache_path=None):
    cache_path = cache_path or os.path.join(
        HERE, "results", f"real-{model.replace(':', '_')}.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    names = sorted(f[:-4] for f in os.listdir(directory) if f.endswith(".txt"))
    for name in names:
        if name in cache:
            continue
        with open(os.path.join(directory, name + ".txt"),
                  encoding="utf-8") as fh:
            text = fh.read()
        got, seconds = profile_for(model, text)
        got["seconds"] = round(seconds, 1)
        cache[name] = got
        print(f"  {name:<20} {seconds:>5.0f}s  {len(got['skills'])} skills, "
              f"{len(got['keywords'])} keywords "
              f"(+{len(got['from_orphans'])} orphan)", flush=True)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=1)
    return cache


def report(model, directory, cache=None):
    import collections
    from bench import orphan_skills as osk
    from bench.search_fields import REACHABLE

    cache = cache if cache is not None else run(model, directory)
    rows, _idx, vocab, _sen = osk.setup()
    total = len(rows)
    whole = collections.Counter(t for t, _s, _k, _c in rows)
    base_reach = sum(1 for r in rows if r[1] >= REACHABLE) / total

    print(f"\n{'=' * 84}\n{model}: real résumés, corpus-measured, no spend"
          f"\n{'=' * 84}")
    print(f"  {total} listings, {base_reach:.0%} reach score {REACHABLE}\n")
    print(f"  {'person':<20}{'yrs':>4}{'skl':>4}{'cov':>7}{'kw':>4}{'+orp':>5}"
          f"{'real':>6}{'rows':>7}{'rel':>6}{'lift':>6}{'spend':>8}{'s':>5}")
    for name, got in sorted(cache.items()):
        own = set(got["skills"])
        hit, n = ds.coverage(own, rows)
        keys = got["keywords"]
        real = sum(1 for k in keys if whole.get(k.strip().lower(), 0))
        bought = ds.buys(keys, rows, own)
        mine = sum(1 for r in rows if r[2] & own) / total
        lift = (bought["relevance"] / mine
                if bought["relevance"] is not None and mine else None)
        pct = lambda v: f"{v:.0%}" if v is not None else "-"
        print(f"  {name:<20}{got['years']:>4}{len(own):>4}"
              f"{f'{hit}/{n}':>7}{len(keys):>4}{len(got['from_orphans']):>5}"
              f"{f'{real}/{len(keys)}':>6}{bought['listings']:>7}"
              f"{pct(bought['relevance']):>6}"
              f"{(f'{lift:.1f}x' if lift else '-'):>6}"
              f"{'$' + format(bought['spend'], '.2f'):>8}"
              f"{got['seconds']:>5.0f}")

    # Three résumés, one job history. A search strategy that changes with
    # the wording of the CV is not reading the career.
    variants = collections.defaultdict(list)
    for name, got in cache.items():
        variants[name.split("_")[0]].append((name, got))
    for stem, group in sorted(variants.items()):
        if len(group) < 2:
            continue
        print(f"\n  {stem}: {len(group)} résumés of one person")
        sets = {}
        for name, got in sorted(group):
            sets[name] = {k.lower() for k in got["keywords"]}
            print(f"    {name:<20} years {got['years']}  "
                  f"{len(got['skills'])} skills  {sorted(sets[name])}")
        names = sorted(sets)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                union = sets[a] | sets[b]
                jac = len(sets[a] & sets[b]) / len(union) if union else 1.0
                print(f"    {a} vs {b}: Jaccard {jac:.2f}")

    print("\n  what each pipeline stage produced, per person:")
    for name, got in sorted(cache.items()):
        print(f"    {name}")
        print(f"      field    {got['field']!r}  grounding {got['grounding']}")
        print(f"      keywords {got['keywords']}")
        if got["from_orphans"]:
            print(f"      orphans  {got['from_orphans']}")
        if got["filler_dropped"]:
            print(f"      filler   {got['filler_dropped'][:4]}")


def demo():
    # profile_for and run need a model; only the reporting arithmetic is
    # checkable offline, and it is the part that could silently mislead.
    import collections
    rows = [("react developer", 40, frozenset({"react"}), "a")] * 20
    whole = collections.Counter(t for t, _s, _k, _c in rows)
    assert whole["react developer"] == 20
    got = ds.buys(["react developer"], rows, {"react"})
    assert got["listings"] == 20 and got["relevance"] == 1.0
    # A keyword that is not a posted title is the failure this file exists
    # to catch, so the check has to be exact-title and not substring.
    assert whole.get("developer", 0) == 0
    assert ds.buys(["developer"], rows, {"react"})["listings"] == 20, \
        "buys is substring; the queryable count must not be"
    print("real_resumes demo ok")


def main():
    args = sys.argv[1:]
    if "--demo" in args:
        return demo()
    directory = (args[args.index("--dir") + 1] if "--dir" in args else None)
    if not directory:
        sys.exit(__doc__)
    models = [a for a in args if not a.startswith("--")
              and a != directory]
    report(models[0] if models else "qwen3:8b", directory)


if __name__ == "__main__":
    main()
