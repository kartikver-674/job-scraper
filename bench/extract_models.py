"""Does a bigger local model read more skills off the same page?

The three-way diagnosis left one blocker. Retrieval is fixed, the
scoring formula is fine — given the same vocabulary it matched Gemini —
and the coverage gap turned out to be EXTRACTION RECALL rather than
normalisation: local returns roughly 40% of the skills Gemini finds in
the same document, and mechanical alias expansion recovers at most a
third of that difference. cloudinary, firebase and flatlist are on
Kanav's page; apex, lwc and lightning are on Kavya's; salesforce itself
is on Lovish's, and his whole career is Salesforce.

So this changes ONE thing and measures it: the model doing the
extraction. Same résumé text, same schema, same prompt, same
temperature, same context sizing. Nothing downstream is touched and no
Gemini is called — its earlier output is not even loaded here.

Ground truth is the technologies named on each page, read off the
documents by hand and stored beside them, because neither model's
output can be the answer key for the other.

Scored on exact match after the same normalisation bench/score.py uses,
then a second time allowing bench/vocab.py's market-validated variants,
so "react" counting for "react.js" is visible as normalisation recovery
rather than hidden inside recall.

    python -m bench.extract_models --demo
    python -m bench.extract_models --dir <texts> qwen3:8b qwen3:14b
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

from bench import vocab as vocab_mod
from bench.score import norm

HERE = os.path.dirname(os.path.abspath(__file__))


def extracted(model, text, timeout=1200):
    """The current extraction step, unchanged, with the model swapped."""
    from bench.run import ask

    started = time.time()
    parsed, _elapsed = ask(model, text)
    return parsed, time.time() - started


def prf(got, want):
    got, want = {norm(g) for g in got if norm(g)}, {norm(w) for w in want if norm(w)}
    hit = got & want
    precision = len(hit) / len(got) if got else 0.0
    recall = len(hit) / len(want) if want else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)
    return precision, recall, f1, sorted(want - got), sorted(got - want)


def with_variants(got, vocab, rows):
    """The extraction plus every market-validated spelling of it."""
    expanded, _added = vocab_mod.expand_all(got, vocab, rows)
    return expanded


def run(models, directory, people, cache_path=None):
    cache_path = cache_path or os.path.join(HERE, "results",
                                            "extract-models.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    for model in models:
        for name in people:
            key = f"{model}::{name}"
            if key in cache:
                continue
            with open(os.path.join(directory, name + ".txt"),
                      encoding="utf-8") as fh:
                text = fh.read()
            try:
                parsed, seconds = extracted(model, text)
            except Exception as exc:            # noqa: BLE001 - logged
                cache[key] = {"error": f"{type(exc).__name__}: {exc}"}
            else:
                cache[key] = {"skills": parsed.get("skills") or [],
                              "titles": parsed.get("titles") or [],
                              "companies": parsed.get("companies") or [],
                              "seconds": round(seconds, 1)}
            got = cache[key]
            print(f"  {key:<28} {got.get('seconds', '-'):>6}s  "
                  f"{len(got.get('skills') or [])} skills", flush=True)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, indent=1)
    return cache


def report(models, directory, people, cache=None):
    from bench import orphan_skills as osk

    cache = cache if cache is not None else run(models, directory, people)
    with open(os.path.join(directory, "truth_skills.json"),
              encoding="utf-8") as fh:
        truth = json.load(fh)
    rows, _idx, vocab, _sen = osk.setup()

    print(f"\n{'=' * 80}\nextraction only: same résumé, same schema, "
          f"different model\n{'=' * 80}")
    print(f"  ground truth read off the documents by hand; no Gemini "
          f"output is used\n")
    print(f"  {'person':<20}{'model':<12}{'n':>4}{'P':>6}{'R':>6}{'F1':>6}"
          f"{'R+norm':>8}{'secs':>7}")
    totals = {}
    for name in people:
        want = truth[name]["tech"]
        for model in models:
            got = cache.get(f"{model}::{name}")
            if not got or got.get("error"):
                print(f"  {name:<20}{model:<12}  error")
                continue
            skills = got["skills"]
            p, r, f1, missing, _spurious = prf(skills, want)
            _p2, r2, _f2, missing2, _s2 = prf(
                with_variants(skills, vocab, rows), want)
            totals.setdefault(model, []).append((p, r, f1, r2, got["seconds"],
                                                 len(skills)))
            print(f"  {name:<20}{model:<12}{len(skills):>4}{p:>6.2f}{r:>6.2f}"
                  f"{f1:>6.2f}{r2:>8.2f}{got['seconds']:>7.0f}")
        print(f"    {'missed by ' + models[0]:<24}"
              f"{sorted(prf(cache[f'{models[0]}::{name}']['skills'], want)[3])[:7]}")
        if len(models) > 1 and not cache.get(f"{models[1]}::{name}", {}).get("error"):
            print(f"    {'missed by ' + models[1]:<24}"
                  f"{sorted(prf(cache[f'{models[1]}::{name}']['skills'], want)[3])[:7]}")

    print()
    for model, got in totals.items():
        n = len(got)
        print(f"  {model:<14} mean  precision {sum(g[0] for g in got)/n:.2f}"
              f"   recall {sum(g[1] for g in got)/n:.2f}"
              f"   F1 {sum(g[2] for g in got)/n:.2f}"
              f"   recall+norm {sum(g[3] for g in got)/n:.2f}"
              f"   {sum(g[5] for g in got)/n:.0f} skills   "
              f"{sum(g[4] for g in got)/n:.0f}s")
    return cache


def demo():
    p, r, f1, missing, spurious = prf(["React.js", "node"], ["react.js", "node"])
    assert (p, r, f1) == (1.0, 1.0, 1.0)
    p, r, f1, missing, spurious = prf(["react.js"], ["react.js", "firebase"])
    assert p == 1.0 and r == 0.5 and missing == ["firebase"]
    # Exact after normalisation, so case and spacing are not a miss and
    # a genuinely different term is.
    assert prf(["  REACT.JS "], ["react.js"])[1] == 1.0
    assert prf(["java"], ["javascript"])[1] == 0.0
    assert prf([], ["x"])[:3] == (0.0, 0.0, 0.0)
    assert prf(["x"], [])[:3] == (0.0, 0.0, 0.0)

    # Normalisation recovery is measured separately, never folded into
    # raw recall: "react" standing in for "react.js" has to be visible.
    rows = [("a", 0, frozenset({"react", "react.js"}), f"c{i}")
            for i in range(30)] + [("b", 0, frozenset(), f"d{i}")
                                   for i in range(60)]
    vocab = {}
    for _t, _s, skills, _c in rows:
        for s in skills:
            vocab[s] = vocab.get(s, 0) + 1
    assert prf(["react.js"], ["react"])[1] == 0.0
    assert prf(with_variants(["react.js"], vocab, rows), ["react"])[1] == 1.0
    print("extract_models demo ok")


def main():
    args = sys.argv[1:]
    if "--demo" in args:
        return demo()
    directory = args[args.index("--dir") + 1] if "--dir" in args else None
    if not directory:
        sys.exit(__doc__)
    models = [a for a in args if not a.startswith("--") and a != directory]
    report(models or ["qwen3:8b"], directory,
           ["kanav_reactnative", "lovish", "kavya"])


if __name__ == "__main__":
    main()
