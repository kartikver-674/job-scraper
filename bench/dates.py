"""Stop asking the model for years of experience; ask for dates and count.

The benchmark's clearest result is that both local models read a résumé well
and cannot do arithmetic over it. qwen3:8b scored 0.156 on years_experience
and gave chen four different answers for the same facts in four layouts —
4, 7, 8, 7 — which is not an extraction failure, because it got chen's
employers right every time. NuExtract returned null on 28 of 32, which for
a verbatim extractor is correct: the page never says "11 years".

So the field is computed here. The model is asked only for the date ranges
it demonstrably can find, and Python sums them.

    python -m bench.dates qwen3:8b
    python -m bench.dates --demo
"""

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

from bench.people import PEOPLE, truth
from bench.render import LAYOUTS
from bench.run import OLLAMA, KEEP_ALIVE, RESUMES, ctx_for

# Only what the model is good at: which rows exist and what they say.
SCHEMA = {
    "type": "object",
    "properties": {
        "employment": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "title": {"type": "string"},
                    # Free text on purpose. Résumés write dates every way
                    # there is and forcing a format here just moves the
                    # parsing into the model, which is the thing that failed.
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                },
                "required": ["company", "title", "start", "end"],
            },
        },
    },
    "required": ["employment"],
}

PROMPT = """List every EMPLOYMENT entry in this résumé.

- One entry per row of work history, including internships.
- Do NOT include education, certifications, publications or projects.
- Copy the dates exactly as written. If a role is current, end is "present".

Résumé:
{text}"""

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# Words that mark a row as not counting toward professional experience.
# bhaskar's answer key is 0 and he has a three-month internship, which is
# the case that makes this necessary rather than tidy.
NOT_PROFESSIONAL = ("intern", "internship", "trainee", "student", "volunteer")


def parse_month(text, today=(2026, 9)):
    """(year, month) from whatever a résumé wrote. None when unreadable."""
    if not text:
        return None
    low = str(text).strip().lower()
    if low in ("present", "current", "now", "ongoing", "till date", "to date"):
        return today
    year = month = None
    for token in low.replace("/", " ").replace("-", " ").replace(",", " ").split():
        if token[:3] in MONTHS and month is None:
            month = MONTHS[token[:3]]
        elif token.isdigit():
            value = int(token)
            if 1900 < value < 2100 and year is None:
                year = value
            elif 1 <= value <= 12 and month is None:
                month = value
    if year is None:
        return None
    return year, month or 1


def months_between(start, end):
    """Whole months from start to end, never negative."""
    if not start or not end:
        return 0
    return max(0, (end[0] - start[0]) * 12 + (end[1] - start[1]))


def years_from(employment, today=(2026, 9)):
    """Professional years: overlapping rows merged, non-professional dropped.

    Merged because a row can overlap another — hana worked a machine
    learning internship for six months while still employed as a structural
    engineer, and adding those spans counts that half-year twice.
    """
    spans = []
    for row in employment or ():
        title = (row.get("title") or "").lower()
        if any(word in title for word in NOT_PROFESSIONAL):
            continue
        start = parse_month(row.get("start"), today)
        end = parse_month(row.get("end"), today)
        if start and end and months_between(start, end) > 0:
            spans.append((start, end))
    if not spans:
        return 0

    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    # Completed years, not nearest: ada has five years and seven months of
    # continuous work and every résumé in the world calls that five. round()
    # made it six and disagreed with the answer key on the control case.
    return sum(months_between(s, e) for s, e in merged) // 12


def ask(model, text, timeout=600, url=OLLAMA):
    prompt = PROMPT.format(text=text)
    body = json.dumps({
        "model": model, "prompt": prompt, "format": SCHEMA, "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0, "num_ctx": ctx_for(prompt)},
    }).encode()
    request = urllib.request.Request(url, body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(json.loads(response.read())["response"])


def run(model, cache_path=None, asker=ask):
    cache_path = cache_path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "results",
        f"dates-{model.replace(':', '_').replace('/', '_')}.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    from resume_parser import extract_text

    for slug in PEOPLE:
        for layout in LAYOUTS:
            key = f"{slug}-{layout}"
            pdf = os.path.join(RESUMES, f"{slug}-{layout}.pdf")
            if key in cache or not os.path.exists(pdf):
                continue
            try:
                cache[key] = asker(model, extract_text(pdf))
            except (urllib.error.URLError, json.JSONDecodeError, OSError,
                    TimeoutError) as exc:
                cache[key] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"  {key:<18} "
                  f"{len(cache[key].get('employment') or [])} rows", flush=True)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, indent=1)
    return cache


def report(model, cache):
    exact = ambiguous = 0
    print(f"\n{'=' * 66}\n{model}: years COMPUTED from extracted dates"
          f"\n{'=' * 66}")
    per_person = {}
    for key, entry in sorted(cache.items()):
        slug = key.rsplit("-", 1)[0]
        per_person.setdefault(slug, []).append(years_from(entry.get("employment")))
    for slug, got in sorted(per_person.items()):
        want = truth(slug)["years_experience"]
        hits = sum(1 for g in got if g == want)
        exact += hits
        note = ""
        # hana's answer key takes her headline's reading (four years since
        # the career change). Summing her rows gives twelve, which is also
        # true. No arithmetic settles which one a job filter should use, so
        # this is the case that needs a model or a question to the user —
        # and it is exactly the case a confidence check should flag.
        if slug == "hana":
            note = "  <- ambiguous: 4 since the change, 12 in total"
            ambiguous += len(got)
        print(f"  {slug:<9} truth {want:>2}   computed {got}"
              f"   {hits}/{len(got)}{note}")
    total = sum(len(v) for v in per_person.values())
    print(f"\n  exact: {exact}/{total} = {exact / total:.0%}")
    print(f"  excluding the one case dates cannot settle: "
          f"{exact}/{total - ambiguous} = {exact / (total - ambiguous):.0%}")
    return exact / total


def demo():
    assert parse_month("Apr 2023") == (2023, 4)
    assert parse_month("04/2023") == (2023, 4)
    assert parse_month("2023") == (2023, 1)
    assert parse_month("Present", today=(2026, 9)) == (2026, 9)
    assert parse_month("") is None and parse_month("shortly") is None
    assert parse_month("September 2017") == (2017, 9)

    assert months_between((2020, 1), (2021, 1)) == 12
    assert months_between((2021, 1), (2020, 1)) == 0, "never negative"

    # ada: Jan 2021 - Mar 2023 then Apr 2023 - present(Sep 2026) = 67 months,
    # which is five years and seven months, which is five years.
    ada = [{"title": "Backend Engineer", "start": "Jan 2021", "end": "Mar 2023"},
           {"title": "Senior Backend Engineer", "start": "Apr 2023",
            "end": "Present"}]
    assert years_from(ada, today=(2026, 9)) == 5, years_from(ada, (2026, 9))

    # bhaskar: an internship only, so zero professional years.
    assert years_from([{"title": "Software Engineering Intern",
                        "start": "May 2025", "end": "Jul 2025"}]) == 0

    # Overlapping rows are merged, not added: hana's internship ran while
    # she was still a structural engineer.
    overlap = [{"title": "Structural Engineer", "start": "Apr 2014",
                "end": "Dec 2021"},
               {"title": "Engineer", "start": "Jul 2021", "end": "Dec 2021"}]
    assert years_from(overlap, today=(2026, 9)) == 7, years_from(overlap)

    assert years_from([]) == 0 and years_from(None) == 0
    # Unreadable dates are dropped rather than counted as zero-length.
    assert years_from([{"title": "Engineer", "start": "?", "end": "?"}]) == 0

    def fake(model, text):
        # Matched on the FULL name. Matching the first name picked chen for
        # gopal's résumé, because gopal lives in CHENnai — and gopal was
        # then scored against chen's eleven years.
        slug = next(s for s in PEOPLE if PEOPLE[s]["name"] in text)
        return {"employment": [
            {"company": j["company"], "title": j["title"],
             "start": j["start"], "end": j["end"] or "present"}
            for j in PEOPLE[slug]["employment"]]}

    import tempfile
    cache = run("fake:dates", os.path.join(tempfile.mkdtemp(), "d.json"), fake)
    assert len(cache) == 32
    # Given perfect date extraction the arithmetic must be right on every
    # person the dates can settle.
    for slug in PEOPLE:
        got = years_from(cache[f"{slug}-plain"]["employment"])
        want = truth(slug)["years_experience"]
        if slug != "hana":
            assert got == want, (slug, got, want)
    print("dates demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    for model in [a for a in sys.argv[1:] if not a.startswith("--")]:
        report(model, run(model))


if __name__ == "__main__":
    main()
