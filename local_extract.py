"""Read a résumé with a local model, then check and derive with Python.

The half of profile generation that Gemini used to do alone. Two calls to
a model running on the user's own machine, then deterministic code that
decides whether to keep the answer:

    extract(model, text)        name, skills, titles, companies, ...
    employment(model, text)     one row per job, plus the target field
    route(fields, rows, text)   accept / corrected / escalate, with reasons
    years_from(rows)            the number, computed — never asked for

WHY THE MODEL IS NOT ASKED FOR years_experience
-----------------------------------------------
It cannot do the arithmetic. Asked directly, qwen3:8b was off by three or
more years on 12 of the 52 benchmark documents and gave one person four
different answers for four renderings of the same facts — 4, 7, 8, 7 —
while getting her employers right every time. Asked instead for the rows
and one boolean per row, with Python doing the counting, the same model
and the same documents score 52/52 exactly.

That matters more than a benchmark line, because two production consumers
read the number and both of them DROP rows when it is wrong:

  SEARCH["experience_years"]        LinkedIn's f_E seniority band
                                    (scraper._linkedin_experience_code)
  SETTINGS["max_experience_years"]  = years + 3, and any posting whose
                                    stated floor exceeds it is dropped
                                    (scraper.py:576, merge_jobs.py:74)

An under-read deletes reachable roles before scoring ever runs.

THE DEFINITION
--------------
years_experience is the number of whole completed years the person has
been paid to do THE KIND OF WORK THIS RÉSUMÉ IS TARGETING. Not total time
in the workforce, and not time in one job title. Both consumers above
compare it against what a POSTING DEMANDS, and "5+ years of machine
learning" means five years of machine learning — so for someone who spent
eight years designing bridges and four building models, the answer is
four.

Six clauses, each with a benchmark case that fails without it:

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

Clause 3 is the only one needing anything from the model beyond dates: one
boolean per row. Everything else is arithmetic.

The measurement harnesses that produced these numbers live in bench/ and
import from here, so there is one implementation and the benchmark scores
the code that ships.

    python -m local_extract --demo
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

# Where the local model server is and which model to ask. Both come from
# the environment, using Ollama's own variable name, because "install
# Ollama" is what replaces "supply an API key" — and a user running it on
# another port, in Docker, or on a second machine has to be able to say so.
#
# Resolved PER CALL, never bound as a default argument. Freezing these at
# import time is what made them unreachable from a test: a test that
# patched the constant made a real model call and asserted the wrong
# failure, which is the sort of thing a hardcoded default hides.
HOST_ENV = "OLLAMA_HOST"
MODEL_ENV = "OLLAMA_MODEL"
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:8b"

# Unload as soon as a request finishes. Left resident, an 8B model is more
# than a laptop has to spare while a sweep is also running.
KEEP_ALIVE = "30s"


def host():
    """Base URL of the local model server, from OLLAMA_HOST.

    Ollama's own variable is routinely written without a scheme
    ("127.0.0.1:11434", "ollama:11434" inside compose), so a missing one
    is supplied rather than producing a urllib error nobody can read.
    """
    value = (os.environ.get(HOST_ENV) or "").strip() or DEFAULT_HOST
    if "://" not in value:
        value = "http://" + value
    return value.rstrip("/")


def endpoint(path="/api/generate"):
    """A full URL for one of the server's paths."""
    return host() + path


def model_name(model=None):
    """The model to ask: an explicit one, else OLLAMA_MODEL, else the
    default. Passed through so a caller can always override."""
    return (model or os.environ.get(MODEL_ENV) or "").strip() or DEFAULT_MODEL


# The generate endpoint as a module-level name, for bench/, which reads it
# as a constant. Resolved at import; the functions below resolve per call,
# so this is a convenience rather than the source of truth.
OLLAMA = endpoint()


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

def ctx_for(prompt, reply_tokens=768, floor=2048, ceiling=8192):
    """A context window sized to the document, not guessed at.

    The KV cache scales with this number whether the tokens are used or
    not, and the first version asked for 16,384 on an 8B model — roughly
    5GB of weights plus another 5GB of cache — against a longest prompt of
    919 tokens. The machine ran out of memory and restarted.

    ~4 chars per token is rough, so `reply_tokens` of headroom covers both
    the estimate being wrong and the JSON coming back. Rounded up to a
    power of two because runtimes allocate in blocks anyway.
    """
    need = len(prompt) // 4 + reply_tokens
    size = floor
    while size < need and size < ceiling:
        size *= 2
    return min(size, ceiling)


class ModelUnavailable(RuntimeError):
    """Ollama is not running, or the model is not pulled.

    Carries a short, actionable reason and never the exception text from
    urllib, which can include the request URL.
    """


def _generate(model, prompt, schema, timeout, url=None):
    """One schema-constrained generation. Returns the parsed JSON.

    `think: False` is not optional. Without it the model reasons at length
    before extracting, which measured 37.4s per document against 9.4s with
    it — and scored WORSE, because copying a date out of a table is not a
    reasoning task.
    """
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "format": schema,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "think": False,
        "options": {"temperature": 0, "num_ctx": ctx_for(prompt)},
    }).encode()
    url = url or endpoint()
    request = urllib.request.Request(url, body,
                                     {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ModelUnavailable(
                f"the local model {model!r} is not installed — "
                f"run `ollama pull {model}`") from None
        raise ModelUnavailable(
            f"the local model server answered {exc.code}") from None
    except urllib.error.URLError:
        raise ModelUnavailable(
            "no local model server is reachable at "
            f"{url.rsplit('/api/', 1)[0]} — is Ollama running?") from None
    try:
        return json.loads(payload["response"])
    except (KeyError, json.JSONDecodeError):
        raise ModelUnavailable(
            f"{model} returned an answer that was not the JSON it was "
            f"asked for") from None


# --------------------------------------------------------------------------
# Call 1: the fields
# --------------------------------------------------------------------------

# Sweep consumes four of these; the rest are here because a parser that
# cannot find an employer is not production-worthy whatever Sweep happens
# to read today. years_experience is asked for ONLY so route() has the
# model's own figure to log a correction against — it is never used.
FIELDS_SCHEMA = {
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
FIELDS_PROMPT = """Extract structured data from this résumé.

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


def extract(model=None, text="", timeout=600, url=None):
    """The fields, from one local call. Returns (parsed, seconds)."""
    prompt = FIELDS_PROMPT.format(text=text)
    started = time.time()
    return _generate(model_name(model), prompt, FIELDS_SCHEMA, timeout,
                     url), time.time() - started


# --------------------------------------------------------------------------
# Call 2: the employment rows
# --------------------------------------------------------------------------

# Only what the model is good at: which rows exist and what they say.
EMPLOYMENT_SCHEMA = {
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

EMPLOYMENT_PROMPT = """List every EMPLOYMENT entry in this résumé.

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


def employment(model=None, text="", timeout=600, url=None):
    """The employment rows and the target field, from one local call."""
    prompt = EMPLOYMENT_PROMPT.format(text=text)
    return _generate(model_name(model), prompt, EMPLOYMENT_SCHEMA, timeout,
                     url)


# --------------------------------------------------------------------------
# The arithmetic
# --------------------------------------------------------------------------

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# Words that mark a row as not counting toward professional experience.
# bhaskar's answer key is 0 and he has a three-month internship, which is
# the case that makes this necessary rather than tidy.
NOT_PROFESSIONAL = ("intern", "internship", "trainee", "student", "volunteer")


def today():
    """(year, month) now. A default argument would freeze the clock at
    import time, which is how "present" silently stopped advancing."""
    now = time.localtime()
    return now.tm_year, now.tm_mon


def parse_month(text, now=None):
    """(year, month) from whatever a résumé wrote. None when unreadable."""
    now = now or today()
    if not text:
        return None
    low = str(text).strip().lower()
    if low in ("present", "current", "now", "ongoing", "till date", "to date"):
        return now
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


def readable(row, now=None):
    """Are both ends of this row's date range parseable?"""
    return bool(parse_month(row.get("start"), now)
                and parse_month(row.get("end"), now))


def years_from(rows, now=None, ignore_relevance=False):
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
    for row in countable(rows, ignore_relevance):
        start = parse_month(row.get("start"), now)
        end = parse_month(row.get("end"), now)
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


# --------------------------------------------------------------------------
# The router: is this parse good enough to keep?
# --------------------------------------------------------------------------
#
# The checks are deterministic on purpose. A schema-constrained model
# returns well-formed JSON whether it is right or wrong, so it has no
# confidence to report — "97%" is not a number any of these models emit.
# What IS available is evidence: whether each value appears in the
# document, and whether the number agrees with the arithmetic.
#
# Escalation is reserved for evidence of a PROBLEM, not suspicion of
# difficulty. Guessing that a document is hard would send work to Gemini on
# a hunch, which is the dependency this is meant to remove.

# A model confabulating ONE employer is a bad row to drop. Confabulating
# most of them is a parse that cannot be trusted field by field, because
# whatever went wrong was not about that one value.
CONFABULATION_SHARE = 0.5

# Fields whose every entry should be findable in the document. Not
# education or certifications: a degree is routinely written "B.Tech
# Computer Science" and returned as "Bachelor of Technology in Computer
# Science", which is right and ungrounded, so checking it would drop
# correct answers.
GROUNDED_FIELDS = ("companies", "skills", "titles", "institutions")

# Everything but letters, digits and the two characters that carry meaning
# in a language name is removed — including the dot, so "Node.js" matches a
# document that writes "node js". Keeping the dot was the first version and
# it matched neither.
_PUNCT = re.compile(r"[^a-z0-9+#]+")

# Below this length, containment in the folded text is worthless: "go"
# folded is "go", and so is the middle of "google".
SHORT = 3


def _key(text):
    """Comparable form: lowercase, everything but [a-z0-9+#] removed."""
    return _PUNCT.sub("", str(text).lower())


def supported(value, text):
    """Is this value actually in the document?

    Two tiers, because folding the whole document into one string makes
    short needles match anything: "go" would be found inside "google" and
    "c" inside every word containing one. A short value is looked for as a
    WORD in the original text instead; a longer one can use the folded form
    and so survives line wraps and odd spacing.
    """
    needle = _key(value)
    if not needle:
        return False
    if len(needle) < SHORT:
        return re.search(r"(?<![a-z0-9])" + re.escape(str(value).lower().strip())
                         + r"(?![a-z0-9])", str(text).lower()) is not None
    return needle in _key(text)


def check_grounding(parsed, text):
    """(corrections, escalations) for the fields that must be in the text."""
    corrections, escalations = [], []
    for field in GROUNDED_FIELDS:
        values = parsed.get(field) or []
        if not values:
            continue
        unsupported = [v for v in values if not supported(v, text)]
        if not unsupported:
            continue
        if len(unsupported) / len(values) > CONFABULATION_SHARE:
            escalations.append(
                f"{field}: {len(unsupported)} of {len(values)} not found in "
                f"the document ({', '.join(map(str, unsupported[:3]))})")
        else:
            corrections.append({
                "field": field, "action": "dropped",
                "removed": unsupported,
                "why": "not present in the document",
            })
    name = parsed.get("name")
    if name and not supported(name, text):
        escalations.append(f"name: {name!r} is not in the document")
    return corrections, escalations


def check_years(parsed, rows, now=None):
    """years_experience against the definition at the top of this file.

    The model's own answer is never used. It is checked, not consulted: the
    number is derived from the rows, and the model's figure only decides
    whether a correction gets logged as one.
    """
    rows = (rows or {}).get("employment") or []
    stated = parsed.get("years_experience")
    if not rows:
        # No employment rows at all is a real answer for a fresher, but
        # only if the model also said zero. A number with nothing behind it
        # has nothing to check it against.
        if stated in (0, None):
            return [], []
        return [], [f"years_experience: model said {stated} with no "
                    f"employment rows extracted to support it"]

    counted = countable(rows)
    if not counted:
        # Nothing countable is the right answer for a fresher whose only
        # row is an internship. It is NOT a right answer when the rows were
        # all marked as another career: then the flags say the person has
        # left the field entirely, which is either wrong or a profile no
        # job search can be built from.
        if any(not is_relevant(r) for r in rows):
            return [], [
                f"years_experience: all {len(rows)} employment rows were "
                f"marked as a different career, leaving no relevant "
                f"experience to count"]
        if stated in (0, None):
            return [], []
        return [{"field": "years_experience", "action": "computed",
                 "from": stated, "to": 0,
                 "why": f"all {len(rows)} rows are internships or "
                        f"traineeships, which do not count"}], []

    unreadable = [r for r in counted if not readable(r, now)]
    if len(unreadable) == len(counted):
        return [], [f"years_experience: none of the {len(counted)} "
                    f"countable employment date ranges could be read"]

    computed = years_from(rows, now)
    total = years_from(rows, now, ignore_relevance=True)
    why = "summed from the extracted date ranges"
    if unreadable:
        # Some rows counted, some did not, so the total is a floor rather
        # than a figure. Worth saying, not worth a model call.
        why += (f"; {len(unreadable)} of {len(counted)} rows had unreadable "
                f"dates and were skipped")
    if total != computed:
        # The career-change case, and the one worth reading the log for:
        # this is where the answer stops being the obvious one. hana and
        # kwame both look like twelve-year veterans until clause 3 applies.
        why += (f"; a career change was detected, so {total - computed} "
                f"years in a previous field were excluded")
    if unreadable or stated != computed:
        return [{"field": "years_experience", "action": "computed",
                 "from": stated, "to": computed, "why": why}], []
    return [], []


def route(parsed, rows, text, now=None):
    """One decision for one résumé, with its reasons.

    Three outcomes:
      accept     every check passed; use the local result as it stands
      corrected  a check failed and a deterministic fix existed
      escalate   a check failed with no local fix; this one needs Gemini
    """
    if not parsed:
        return {"decision": "escalate", "result": None, "corrections": [],
                "reasons": ["the local model returned nothing usable"]}

    grounding_fixes, grounding_stops = check_grounding(parsed, text)
    year_fixes, year_stops = check_years(parsed, rows, now)
    reasons = grounding_stops + year_stops
    if reasons:
        return {"decision": "escalate", "result": None,
                "corrections": [], "reasons": reasons}

    result = dict(parsed)
    corrections = grounding_fixes + year_fixes
    for fix in corrections:
        if fix["action"] == "dropped":
            removed = {_key(v) for v in fix["removed"]}
            result[fix["field"]] = [v for v in result[fix["field"]]
                                    if _key(v) not in removed]
        elif fix["action"] == "computed":
            result[fix["field"]] = fix["to"]
    return {"decision": "corrected" if corrections else "accept",
            "result": result, "corrections": corrections, "reasons": []}


def read(model=None, text="", timeout=600, url=None, now=None):
    """Both calls and the verdict, for one résumé.

    Returns (checked_fields, employment_answer, decision). `checked_fields`
    is None when the decision is to escalate — the caller falls back.
    """
    fields, _seconds = extract(model, text, timeout, url)
    rows = employment(model, text, timeout, url)
    decision = route(fields, rows, text, now)
    checked = decision["result"]
    if checked is not None:
        checked["years_experience"] = years_from(
            (rows or {}).get("employment") or [], now)
    return checked, rows, decision


def demo():
    """Self-check: the six clauses, and the router's three outcomes."""
    now = (2026, 9)

    # Clause 1: floored, never rounded. ada is 5y7m.
    ada = [{"title": "Backend Engineer", "start": "Jan 2021", "end": "Mar 2023",
            "relevant": True},
           {"title": "Senior Backend Engineer", "start": "Apr 2023",
            "end": "Present", "relevant": True}]
    assert years_from(ada, now) == 5, years_from(ada, now)

    # Clause 2: an internship alone is zero.
    assert years_from([{"title": "Software Intern", "start": "Jun 2025",
                        "end": "Sep 2025", "relevant": True}], now) == 0

    # Clause 3: the prior career is excluded, and only relevance excludes it.
    hana = [{"title": "Structural Engineer", "start": "Jan 2014",
             "end": "Dec 2021", "relevant": False},
            {"title": "ML Engineer", "start": "Jan 2022", "end": "Present",
             "relevant": True}]
    assert years_from(hana, now) == 4, years_from(hana, now)
    assert years_from(hana, now, ignore_relevance=True) == 12

    # Clause 4: concurrent roles are merged, not added.
    mateo = [{"title": "Engineer", "start": "Jan 2022", "end": "Jan 2026",
              "relevant": True},
             {"title": "Consultant", "start": "Jan 2023", "end": "Jan 2025",
              "relevant": True}]
    assert years_from(mateo, now) == 4, years_from(mateo, now)

    # Clause 5: a gap is not counted. Two 3y spans either side of 2 years off.
    jonas = [{"title": "Dev", "start": "Jan 2015", "end": "Jan 2018",
              "relevant": True},
             {"title": "Dev", "start": "Jan 2020", "end": "Jan 2023",
              "relevant": True}]
    assert years_from(jonas, now) == 6, years_from(jonas, now)
    # ... and the naive answer would have been eight.
    assert months_between((2015, 1), (2023, 1)) // 12 == 8

    # Clause 6: part-time counts in full — nothing here prorates, which is
    # the point. A row carrying an hours note is still its calendar span.
    part = [{"title": "Engineer (16h/week)", "start": "Jan 2024",
             "end": "Jan 2026", "relevant": True}]
    assert years_from(part, now) == 2

    # "present" tracks the real clock rather than an import-time constant.
    live = [{"title": "Dev", "start": f"Jan {today()[0] - 3}", "end": "present",
             "relevant": True}]
    assert years_from(live) == 3, years_from(live)

    # Unreadable dates contribute nothing rather than raising.
    assert years_from([{"title": "Dev", "start": "sometime", "end": "later",
                        "relevant": True}], now) == 0
    assert parse_month("") is None and parse_month("2019/06", now) == (2019, 6)
    assert parse_month("present", now) == now

    # Grounding: a value not in the document is dropped, most of them escalate.
    text = "Ada Okonkwo. Backend Engineer at Fettle Health. python, django."
    fixes, stops = check_grounding(
        {"name": "Ada Okonkwo", "skills": ["python", "django", "cobol"]}, text)
    assert not stops and fixes[0]["removed"] == ["cobol"], (fixes, stops)
    _fixes, stops = check_grounding(
        {"skills": ["cobol", "fortran", "python"]}, text)
    assert stops and "2 of 3" in stops[0], stops
    # Short values are matched as words, so "go" is not found inside "google".
    assert supported("go", "we use go daily") and not supported("go", "google")
    # And punctuation spacing is not a difference worth escalating over.
    assert supported("Node.js", "worked with node js")

    # The three routes.
    rows = {"target_field": "software engineering", "employment": ada}
    ok = route({"name": "Ada Okonkwo", "years_experience": 5,
                "skills": ["python"]}, rows, text)
    assert ok["decision"] == "accept", ok

    fixed = route({"name": "Ada Okonkwo", "years_experience": 9,
                   "skills": ["python"]}, rows, text)
    assert fixed["decision"] == "corrected"
    assert fixed["result"]["years_experience"] == 5, fixed
    assert "summed from" in fixed["corrections"][0]["why"]

    # All rows flagged as another career: no history to build a search from.
    gone = route({"name": "Ada Okonkwo", "years_experience": 3},
                 {"employment": [dict(hana[0])]}, text)
    assert gone["decision"] == "escalate" and "different career" in gone["reasons"][0]

    # Unreadable dates everywhere escalate rather than answering zero.
    blind = route({"name": "Ada Okonkwo", "years_experience": 3},
                  {"employment": [{"title": "Dev", "start": "?", "end": "?",
                                   "relevant": True}]}, text)
    assert blind["decision"] == "escalate" and "could be read" in blind["reasons"][0]

    # A stated number with no rows behind it has nothing to check it against.
    empty = route({"name": "Ada Okonkwo", "years_experience": 7},
                  {"employment": []}, text)
    assert empty["decision"] == "escalate", empty
    # ... but a fresher saying zero is a real answer, not a failure.
    assert route({"name": "Ada Okonkwo", "years_experience": 0},
                 {"employment": []}, text)["decision"] == "accept"

    # ctx_for sizes to the document and never exceeds the ceiling.
    assert ctx_for("x" * 100) == 2048
    assert ctx_for("x" * 40000) == 8192

    print("local_extract demo ok")


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        demo()
    else:
        print(__doc__)
