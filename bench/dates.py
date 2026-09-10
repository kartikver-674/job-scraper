"""What years_experience means for Sweep, and the arithmetic that derives it.

THE DEFINITION
--------------
years_experience is the number of whole completed years the person has been
paid to do THE KIND OF WORK THIS RÉSUMÉ IS TARGETING.

It is not total time in the workforce, and it is not time in one specific
job title. It is relevant experience, and that is not a matter of taste —
it is what Sweep's two consumers of the number actually do with it:

  SEARCH["experience_years"]        becomes LinkedIn's f_E seniority band
                                   (scraper.py:_linkedin_experience_code)
  SETTINGS["max_experience_years"]  = years + 3, and drops any posting whose
                                   stated floor exceeds it (scraper.py:576,
                                   merge_jobs.py:74)

Both compare the number against what a POSTING DEMANDS. A posting asking
for "5+ years of machine learning" means five years of machine learning.
So for someone who spent eight years designing bridges and four building
models, the number that answers Sweep's question is four. Answer twelve and
Sweep filters for director-level ML roles she will not get, and keeps
postings demanding fifteen years.

Six clauses, each with a case in the corpus that fails without it:

  1. Completed years, floored — never rounded.        ada: 5y7m is 5, not 6
  2. Paid professional work only. Internships,
     traineeships and study do not count.             bhaskar: 0.  lena: 1
  3. In the targeted line of work — a PROFESSION, not
     a specialism. Site reliability, platform and
     infrastructure engineering are one field; civil
     engineering and machine learning are two.        hana: 4.  kwame: 3
                                                      chen: 11, not 6
  4. Calendar time, so concurrent roles count
     once rather than being added.                    mateo: 4.  iris: 5
  5. Time worked, so gaps are not counted — the
     answer is not last date minus first date.        jonas: 6, not 8
  6. Part-time is counted, not prorated. Sweep is
     asking how senior a role fits, and two years
     of part-time work is two years of standing.      mateo's 16h/week row

Clause 3 is the only one that needs anything from the model beyond dates:
one boolean per employment row. Everything else is arithmetic, and the
benchmark's clearest result is that the models cannot do arithmetic.

WHY THE MODEL IS NOT ASKED FOR THE NUMBER
-----------------------------------------
qwen3:8b scored 0.156 on years_experience and gave one person four
different answers for four renderings of the same facts — 4, 7, 8, 7 —
while getting her employers right every time. NuExtract returned null on
28 of 32, which for a verbatim extractor is correct: the page never says
"11 years". So the model is asked only for what it demonstrably can find
(which rows exist, what they say, and whether each is in the same line of
work), and Python counts.

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
        # First on purpose. JSON is generated in property order, so the
        # model writes down what this résumé is targeting before it judges
        # a single row against it. Asked the other way round — a bare
        # per-row boolean — it kept kwame's nine years of teaching, because
        # nothing on his page announces the change the way hana's headline
        # does, and there was no written anchor to compare the row to.
        "target_field": {"type": "string"},
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
                    # Clause 3, and the only judgement asked of the model.
                    # A boolean is the right shape for it: the model is
                    # good at deciding whether two roles are the same kind
                    # of work and bad at turning that into a total.
                    "relevant": {"type": "boolean"},
                },
                "required": ["company", "title", "start", "end", "relevant"],
            },
        },
    },
    "required": ["target_field", "employment"],
}

PROMPT = """List every EMPLOYMENT entry in this résumé.

- One entry per row of work history, including internships.
- Do NOT include education, certifications, publications or projects.
- Copy the dates exactly as written. If a role is current, end is "present".
- target_field: the BROAD profession this person is looking for work in
  now, in two or three words, taken from their most recent role. For
  example "software engineering", "data engineering", "school teaching".
  Name the profession, not the specialism: someone whose last role was
  Staff Platform Engineer is in "software engineering", not "platform
  engineering".
- relevant: true if the role is work in target_field, false if it is not.
  Answer this for each row by comparing the row against target_field, not
  by asking whether the résumé looks like a career change — a résumé does
  not have to announce one. Adjacent specialisms within one profession are
  the SAME field: site reliability, infrastructure, platform and backend
  engineering are all software engineering, and a promotion, a sideways
  move, a part-time or a contract role are all relevant. A different field
  means a different profession — teaching, nursing, civil engineering —
  and it is not relevant even if it is the longest role on the page.

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


def is_professional(row):
    """Clause 2: paid work, not an internship, traineeship or placement."""
    title = (row.get("title") or "").lower()
    return not any(word in title for word in NOT_PROFESSIONAL)


def is_relevant(row):
    """Clause 3. Absent means relevant — most résumés are one career, and a
    model that omits the field should not have its subject's history
    erased."""
    return row.get("relevant") is not False


def countable(rows, ignore_relevance=False):
    """The rows the definition actually counts."""
    return [r for r in rows or ()
            if is_professional(r) and (ignore_relevance or is_relevant(r))]


def readable(row, today=(2026, 9)):
    """Are both ends of this row's date range parseable?"""
    return bool(parse_month(row.get("start"), today)
                and parse_month(row.get("end"), today))


def years_from(employment, today=(2026, 9), ignore_relevance=False):
    """The definition at the top of this file, computed.

    Overlaps are merged rather than added (clause 4) — mateo held two real
    jobs at once and hana interned in ML for six months while still
    employed as a structural engineer, and summing counts that time twice.
    Only merged spans are added, so gaps between them are excluded for
    free (clause 5).

    `ignore_relevance` computes the total-career figure instead, which is
    not the definition but is what makes a career change detectable: the
    two numbers differ only when there is one.
    """
    spans = []
    for row in countable(employment, ignore_relevance):
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
        # bench/run.py has always sent this and this file never did, which
        # made every document here reason at length before extracting a
        # date — three minutes a document against ten seconds there, for
        # the same 52 documents. Copying a row out of a table is not a
        # reasoning task.
        "think": False,
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
    exact = 0
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
        # A career change is the case worth marking, because it is where
        # the arithmetic depends on the model's one judgement rather than
        # only on its dates.
        note = ""
        if any(j.get("relevant") is False
               for j in PEOPLE[slug]["employment"]):
            total_years = years_from(truth(slug)["employment_rows"],
                                     ignore_relevance=True)
            note = (f"  <- career change; {total_years} without clause 3"
                    + ("" if hits == len(got) else ", and not caught"))
        print(f"  {slug:<9} truth {want:>2}   computed {got}"
              f"   {hits}/{len(got)}{note}")
    total = sum(len(v) for v in per_person.values())
    print(f"\n  exact: {exact}/{total} = {exact / total:.0%}")
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

    # THE check: every answer key in the corpus is what this arithmetic
    # produces from that person's own rows. Before the definition was
    # written down these two could disagree and nothing noticed — which is
    # how hana came to be "correctly" reported as 12 with full confidence.
    for slug, person in PEOPLE.items():
        rows = truth(slug)["employment_rows"]
        got = years_from(rows, today=(2026, 9))
        assert got == person["years_experience"], (
            slug, got, person["years_experience"])

    # And each clause earns its keep: remove it and a specific person breaks.
    assert years_from(truth("mateo")["employment_rows"]) == 4
    assert sum(months_between(parse_month(r["start"]), parse_month(r["end"]))
               for r in truth("mateo")["employment_rows"]) // 12 == 6, \
        "clause 4: adding concurrent roles gives mateo two extra years"
    jonas = truth("jonas")["employment_rows"]
    assert years_from(jonas) == 6
    assert months_between(parse_month(jonas[-1]["start"]),
                          parse_month(jonas[0]["end"])) // 12 == 8, \
        "clause 5: first-to-last gives jonas two years he did not work"
    assert years_from(truth("kwame")["employment_rows"]) == 3
    assert years_from(truth("kwame")["employment_rows"],
                      ignore_relevance=True) == 11, \
        "clause 3: kwame's teaching years are the tempting wrong answer"
    assert years_from(truth("lena")["employment_rows"]) == 1
    assert years_from([dict(r, title="Engineer")
                       for r in truth("lena")["employment_rows"]]) == 2, \
        "clause 2: lena's internship is the difference between 1 and 2"
    assert years_from(truth("iris")["employment_rows"]) == 5

    assert is_professional({"title": "Backend Engineer"})
    assert not is_professional({"title": "Software Engineering Intern"})
    assert is_relevant({"title": "x"}) and is_relevant({"relevant": True})
    assert not is_relevant({"relevant": False})
    assert readable({"start": "Jan 2020", "end": "present"})
    assert not readable({"start": "sometime", "end": "present"})
    # bhaskar has a row and nothing countable in it, which is not the same
    # as having no history: the difference decides escalate versus zero.
    assert countable(truth("bhaskar")["employment_rows"]) == []
    assert len(countable(truth("kwame")["employment_rows"])) == 1
    assert len(countable(truth("kwame")["employment_rows"],
                         ignore_relevance=True)) == 2

    def fake(model, text):
        # Matched on the FULL name. Matching the first name picked chen for
        # gopal's résumé, because gopal lives in CHENnai — and gopal was
        # then scored against chen's eleven years.
        slug = next(s for s in PEOPLE if PEOPLE[s]["name"] in text)
        return {"employment": [
            {"company": j["company"], "title": j["title"],
             "start": j["start"], "end": j["end"] or "present",
             "relevant": j.get("relevant", True)}
            for j in PEOPLE[slug]["employment"]],
            "target_field": PEOPLE[slug]["headline"]}

    import tempfile
    cache = run("fake:dates", os.path.join(tempfile.mkdtemp(), "d.json"), fake)
    assert len(cache) == 52
    # Given perfect extraction the arithmetic must now be right on EVERY
    # person, hana included. She used to be the exception because the
    # question was undefined, not because the arithmetic could not reach her.
    for slug in PEOPLE:
        got = years_from(cache[f"{slug}-plain"]["employment"])
        want = truth(slug)["years_experience"]
        assert got == want, (slug, got, want)
    print("dates demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    for model in [a for a in sys.argv[1:] if not a.startswith("--")]:
        report(model, run(model))


if __name__ == "__main__":
    main()
