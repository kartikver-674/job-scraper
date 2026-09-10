"""Run a local model over the 32 benchmark résumés and score it.

Results are cached per (model, document) so a re-run costs nothing and a
crash halfway does not throw away twenty minutes of inference. Delete
bench/results/<model>.json to force a re-run.

    python -m bench.run qwen3:8b
    python -m bench.run qwen3:8b --limit 4      # a quick look
    python -m bench.run --compare               # every model measured so far
    python -m bench.run --demo                  # self-check, no model needed
"""

import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

from bench import score as scoring
from bench.people import PEOPLE, truth
from bench.render import LAYOUTS

HERE = os.path.dirname(os.path.abspath(__file__))
RESUMES = os.path.join(HERE, "resumes")
RESULTS = os.path.join(HERE, "results")
OLLAMA = "http://127.0.0.1:11434/api/generate"

# The fields asked for. Sweep consumes four of these; the rest are here
# because a parser that cannot find an employer is not production-worthy
# whatever Sweep happens to read today.
SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "years_experience": {"type": "integer"},
        "titles": {"type": "array", "items": {"type": "string"}},
        "skills": {"type": "array", "items": {"type": "string"}},
        "companies": {"type": "array", "items": {"type": "string"}},
        "education": {"type": "array", "items": {"type": "string"}},
        "institutions": {"type": "array", "items": {"type": "string"}},
        "projects": {"type": "array", "items": {"type": "string"}},
        "certifications": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "years_experience", "titles", "skills", "companies",
                 "education", "institutions", "projects", "certifications"],
}

# Every instruction here exists because of a failure the benchmark caught.
# The employer line is the first one: qwen3 listed "University of Leeds" as
# a company on the control document.
PROMPT = """Extract structured data from this résumé.

Rules:
- companies: EMPLOYERS only — places that PAID this person to work. A
  university or school is an employer if they worked there and is not one
  if they only studied there.
- institutions: schools and universities only.
- education: the degree names only, not the institution.
- titles: job titles held, exactly as written. Keep seniority words.
- skills: technologies and tools, lowercase, as written on the page.
- projects: project names only.
- certifications: certification names only, not the issuer.
- years_experience: whole completed years being PAID TO DO THE KIND OF WORK
  THIS RÉSUMÉ IS TARGETING. Internships, traineeships and study do not
  count. Years spent in a different career the person has since left do not
  count. Roles held at the same time count once, not twice. Gaps between
  roles do not count. A date of birth is not a career start. If the person
  is a student with no professional role, answer 0.

Résumé:
{text}"""


# NuExtract does not take instructions — the template IS the instruction,
# and its field values are TYPE DESCRIPTORS rather than JSON Schema. Giving
# it the instruct prompt above would be testing it through an interface it
# was not built for, so each model gets its own.
#
# Worth stating plainly when reading the results: the instruct prompt has
# been tuned against this benchmark (the "never a university" rule exists
# because qwen3 called University of Leeds an employer), and NuExtract gets
# no equivalent steer because there is nowhere to put one.
NUEXTRACT_TEMPLATE = {
    "name": "verbatim-string",
    "years_experience": "number",
    "titles": ["verbatim-string"],
    "skills": ["verbatim-string"],
    "companies": ["verbatim-string"],
    "education": ["verbatim-string"],
    "institutions": ["verbatim-string"],
    "projects": ["verbatim-string"],
    "certifications": ["verbatim-string"],
}

OLLAMA_CHAT = "http://127.0.0.1:11434/api/chat"

# Unload as soon as a request finishes. Left resident, one 8B model plus a
# second one being measured is more than this class of machine has.
KEEP_ALIVE = "30s"


def ctx_for(prompt, reply_tokens=768, floor=2048, ceiling=8192):
    """A context window sized to the document, not guessed at.

    The KV cache scales with this number whether the tokens are used or
    not, and the first version of this asked for 16,384 on an 8B model —
    roughly 5GB of weights plus another 5GB of cache — against a longest
    prompt of 919 tokens. The machine ran out of memory and restarted. The
    comment justifying the 16k said a 2k window "silently truncates hana";
    hana's longest render is 919 tokens and fits 2k with 1,100 to spare.
    It was never measured.

    ~4 chars per token is rough, so `reply_tokens` of headroom covers both
    the estimate being wrong and the JSON coming back (esi's twelve
    projects are the longest answer). Rounded up to a power of two because
    runtimes allocate in blocks anyway.
    """
    need = len(prompt) // 4 + reply_tokens
    size = floor
    while size < need and size < ceiling:
        size *= 2
    return min(size, ceiling)


def ask_nuextract(model, text, timeout=600, url=OLLAMA_CHAT):
    """NuExtract's own interface: a template role, then the document."""
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "template",
             "content": json.dumps(NUEXTRACT_TEMPLATE, indent=4)},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "think": False,
        "keep_alive": KEEP_ALIVE,
        # 0.2 is what the model card asks for without thinking enabled.
        "options": {"temperature": 0.2, "num_ctx": ctx_for(text)},
    }).encode()
    started = time.time()
    request = urllib.request.Request(url, body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    return json.loads(payload["message"]["content"]), time.time() - started


def asker_for(model):
    """The interface this model was built for."""
    return ask_nuextract if "nuextract" in model.lower() else ask


def ask(model, text, timeout=600, url=OLLAMA):
    """One schema-constrained generation. Returns (parsed, seconds)."""
    prompt = PROMPT.format(text=text)
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "format": SCHEMA,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0, "num_ctx": ctx_for(prompt)},
    }).encode()
    started = time.time()
    request = urllib.request.Request(url, body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    return json.loads(payload["response"]), time.time() - started


def cache_name(model):
    """A model id as a filename. Registry ids carry a namespace —
    "numind/nuextract3:Q4_K_M" — and replacing only the colon left a path
    pointing into a directory that does not exist, which threw away a
    finished run at the moment it tried to save it.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", model) + ".json"


def documents(limit=None):
    """(slug, layout, pdf path) for every benchmark document."""
    out = [(slug, layout, os.path.join(RESUMES, f"{slug}-{layout}.pdf"))
           for slug in PEOPLE for layout in LAYOUTS]
    out = [d for d in out if os.path.exists(d[2])]
    return out[:limit] if limit else out


def run(model, limit=None, results_dir=None, asker=None):
    """Every document through `model`, cached. Returns the results dict."""
    results_dir = results_dir or RESULTS
    asker = asker or asker_for(model)
    os.makedirs(results_dir, exist_ok=True)
    path = os.path.join(results_dir, cache_name(model))
    cache = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cache = json.load(fh)

    from resume_parser import extract_text

    for slug, layout, pdf in documents(limit):
        key = f"{slug}-{layout}"
        if key in cache:
            continue
        try:
            parsed, took = asker(model, extract_text(pdf))
            cache[key] = {"parsed": parsed, "seconds": took}
        except (urllib.error.URLError, json.JSONDecodeError, OSError,
                TimeoutError) as exc:
            # A refusal or a timeout is a RESULT, not a reason to lose the
            # other thirty-one.
            cache[key] = {"parsed": None, "seconds": None,
                          "error": f"{type(exc).__name__}: {exc}"}
        print(f"  {key:<18} "
              + (f"{cache[key]['seconds']:.1f}s" if cache[key].get("seconds")
                 else cache[key].get("error", "?")[:60]), flush=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=1)
    return cache


def report(model, cache):
    """Per-field, per-layout and per-person accuracy for one model."""
    scored, by_layout, by_person, times, failures = [], {}, {}, [], 0
    for key, entry in sorted(cache.items()):
        slug, layout = key.rsplit("-", 1)
        if not entry.get("parsed"):
            failures += 1
            continue
        result = scoring.score_one(entry["parsed"], truth(slug))
        scored.append(result)
        by_layout.setdefault(layout, []).append(result)
        by_person.setdefault(slug, []).append(result)
        if entry.get("seconds"):
            times.append(entry["seconds"])

    if not scored:
        print(f"\n{model}: nothing scored ({failures} failures)")
        return None

    full = scoring.aggregate(scored)
    sweep = scoring.aggregate(scored, scoring.SWEEP_FIELDS)
    print(f"\n{'=' * 74}\n{model}   {len(scored)} documents"
          + (f", {failures} failed" if failures else "")
          + (f"   median {statistics.median(times):.1f}s" if times else "")
          + f"\n{'=' * 74}")
    print(f"  macro F1, Sweep's own fields   {sweep['macro_f1']:.3f}")
    print(f"  macro F1, every field          {full['macro_f1']:.3f}\n")
    print("  by field:")
    for field, value in sorted(full["per_field"].items(),
                               key=lambda kv: kv[1] or 0):
        mark = "  <- Sweep uses this" if field in scoring.SWEEP_FIELDS else ""
        print(f"    {field:<16} {value:.3f}{mark}")

    print("\n  by layout:")
    for layout in LAYOUTS:
        rows = by_layout.get(layout)
        if rows:
            print(f"    {layout:<16} {scoring.aggregate(rows)['macro_f1']:.3f}"
                  f"   ({len(rows)} docs)")

    print("\n  by résumé (the structural difficulty each one tests):")
    for slug, rows in sorted(by_person.items(),
                             key=lambda kv: scoring.aggregate(kv[1])["macro_f1"]):
        print(f"    {slug:<9} {scoring.aggregate(rows)['macro_f1']:.3f}"
              f"   {PEOPLE[slug]['difficulty']}")

    worst = sorted(scored, key=lambda r: r["years_experience"]["error"] or 0)
    bad = [r for r in worst if (r["years_experience"]["error"] or 0) >= 3]
    if bad:
        print(f"\n  years_experience off by 3+: {len(bad)} of {len(scored)}"
              f"   worst {max(r['years_experience']['error'] for r in bad)}")
    return {"model": model, "sweep": sweep, "full": full,
            "docs": len(scored), "failures": failures,
            "median_seconds": statistics.median(times) if times else None}


def compare(results_dir=None):
    """Every model measured so far, side by side."""
    results_dir = results_dir or RESULTS
    rows = []
    for name in sorted(os.listdir(results_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(results_dir, name), encoding="utf-8") as fh:
            cache = json.load(fh)
        summary = report(name[:-5], cache)
        if summary:
            rows.append(summary)
    if len(rows) > 1:
        print(f"\n{'=' * 74}\nside by side\n{'=' * 74}")
        print(f"  {'model':<22} {'Sweep F1':>9} {'all F1':>8} {'docs':>5}"
              f" {'median':>8}")
        for r in sorted(rows, key=lambda r: -r["sweep"]["macro_f1"]):
            secs = f"{r['median_seconds']:.1f}s" if r["median_seconds"] else "—"
            print(f"  {r['model']:<22} {r['sweep']['macro_f1']:>9.3f}"
                  f" {r['full']['macro_f1']:>8.3f} {r['docs']:>5} {secs:>8}")
    return rows


def demo():
    # The window is sized, not guessed. This is the assertion that would
    # have stopped a 16k request for a 919-token document.
    assert ctx_for("x" * 3679) == 2048, "the longest benchmark document"
    assert ctx_for("") == 2048, "never below the floor"
    assert ctx_for("x" * 40000) == 8192, "never above the ceiling"
    assert ctx_for("x" * 6000) == 4096
    # Monotonic: a longer document never gets a smaller window.
    sizes = [ctx_for("x" * n) for n in range(0, 40000, 1000)]
    assert sizes == sorted(sizes)

    # A registry id with a namespace must not become a path.
    assert cache_name("numind/nuextract3:Q4_K_M") == \
        "numind_nuextract3_Q4_K_M.json"
    assert cache_name("qwen3:8b") == "qwen3_8b.json"
    assert "/" not in cache_name("a/b/c:d")

    docs = documents()
    assert len(docs) == len(PEOPLE) * len(LAYOUTS), len(docs)
    assert all(os.path.exists(p) for _, _, p in docs)

    # A fake model, so the harness is testable without inference.
    def perfect(model, text):
        # Full name, not first: "Chen" is a substring of "Chennai", so a
        # first-name match hands gopal's document chen's answer key. This
        # demo only escaped it by stopping at four documents.
        slug = next(s for s in PEOPLE if PEOPLE[s]["name"] in text)
        return dict(truth(slug)), 0.1

    import tempfile
    tmp = tempfile.mkdtemp()
    # Every document, not a sample: the first-name collision above only
    # bites once gopal is in the set, and a four-document demo never got
    # there.
    cache = run("fake:perfect", results_dir=tmp, asker=perfect)
    assert len(cache) == len(PEOPLE) * len(LAYOUTS)
    summary = report("fake:perfect", cache)
    assert summary["sweep"]["macro_f1"] == 1.0, "a perfect parser scores 1.0"
    assert summary["full"]["macro_f1"] == 1.0

    # Cached: a second run asks nothing.
    def explode(model, text):
        raise AssertionError("should not be called — the cache is warm")
    assert len(run("fake:perfect", results_dir=tmp,
                   asker=explode)) == len(PEOPLE) * len(LAYOUTS)

    # A model that fails on one document must not lose the others.
    def flaky(model, text):
        if "Bhaskar Nair" in text:
            raise TimeoutError("too slow")
        return perfect(model, text)
    cache = run("fake:flaky", limit=8, results_dir=tmp, asker=flaky)
    assert any(v.get("error") for v in cache.values())
    assert report("fake:flaky", cache)["failures"] >= 1
    print("run demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    if "--compare" in sys.argv:
        return compare()
    models = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not models:
        sys.exit(__doc__)
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    for model in models:
        print(f"\nrunning {model} over {len(documents(limit))} documents")
        report(model, run(model, limit))


if __name__ == "__main__":
    main()
