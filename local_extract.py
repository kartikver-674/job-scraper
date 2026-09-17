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

import os
import re
import time

import inference
# Re-exported, not re-implemented. Every one of these was a name in this
# module before the transport moved to inference.py, and bench/, Sweep and
# the test-suite all still read them from here — same objects, so an
# `except local_extract.ModelUnavailable` still catches what it always did.
from inference import (  # noqa: F401
    DEFAULT_HOST,
    DEFAULT_MODEL,
    HOST_ENV,
    KEEP_ALIVE,
    MODEL_ENV,
    InferenceError,
    ModelUnavailable,
    RemoteServiceError,
    ctx_for,
    host,
    keep_alive,
    model_name,
)

# The default deadline for one model call, in seconds. Was a literal on
# four signatures; named here because the service has to be told it too.
TIMEOUT = 600


def endpoint(path="/api/generate"):
    """A full URL for one of the Ollama server's paths.

    Only meaningful for the local-direct backend, and kept because bench/
    reads it. Sweep itself no longer builds model URLs: it asks
    inference.provider() for a backend and hands it a prompt.
    """
    return host() + path


# The generate endpoint as a module-level name, for bench/, which reads it
# as a constant. Resolved at import; the functions below resolve per call,
# so this is a convenience rather than the source of truth.
OLLAMA = endpoint()


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------
#
# There is none here any more. `_generate` picks a backend and hands it the
# prompt and the schema; whether that reaches Ollama over a loopback socket
# or an HTTP service three lines of config away is not this file's business.
# What IS this file's business — the prompts, the schema, the router, the
# arithmetic — is unchanged below, byte for byte.

def _generate(model, prompt, schema, timeout, url=None, backend=None):
    """One schema-constrained generation. Returns the parsed JSON.

    `url` overrides the backend's endpoint, which is how the tests and
    bench/ point a call at a stub instead of the machine's real Ollama.
    """
    return inference.provider(backend, **({"url": url} if url else {})
                              ).generate(model, prompt, schema, timeout)


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


def _object(parsed, what):
    """The answer, if it is the JSON object the schema asked for.

    A schema-constrained decoder usually returns the right shape, and
    "usually" is the problem: the callers below index straight into this,
    so a list or a bare string became an AttributeError several frames
    away from the model that caused it. One type check, one clear name.
    """
    if not isinstance(parsed, dict):
        raise InferenceError(
            f"the model's {what} answer is {type(parsed).__name__}, not the "
            f"JSON object the schema asked for")
    return parsed


def extract(model=None, text="", timeout=TIMEOUT, url=None,
            backend=None):
    """The fields, from one local call. Returns (parsed, seconds)."""
    prompt = FIELDS_PROMPT.format(text=text)
    started = time.time()
    parsed = _generate(model_name(model), prompt, FIELDS_SCHEMA, timeout,
                       url, backend)
    return _object(parsed, "fields"), time.time() - started


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


def employment(model=None, text="", timeout=TIMEOUT, url=None,
               backend=None):
    """The employment rows and the target field, from one local call."""
    prompt = EMPLOYMENT_PROMPT.format(text=text)
    return _object(_generate(model_name(model), prompt, EMPLOYMENT_SCHEMA,
                             timeout, url, backend), "employment")


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

# Words that say "this role has not ended". parse_month already knew them;
# naming them lets the grounding check below tell a legitimate open end
# from a date that should be findable in the document.
PRESENT_WORDS = ("present", "current", "now", "ongoing", "till date",
                 "to date")


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
    if low in PRESENT_WORDS:
        return now
    year = month = None
    impossible = False
    for token in low.replace("/", " ").replace("-", " ").replace(",", " ").split():
        if token[:3] in MONTHS and month is None:
            month = MONTHS[token[:3]]
        elif token.isdigit():
            value = int(token)
            if 1900 < value < 2100 and year is None:
                year = value
            elif 1 <= value <= 12 and month is None:
                month = value
            elif 12 < value < 1900 or value >= 2100:
                # A number that is neither a year nor a month. "2023-19"
                # used to drop the 19 and answer January 2023, which is a
                # date nobody wrote. Only fatal if no month was named any
                # other way, so "15 Jan 2021" still reads.
                impossible = True
    if year is None or (impossible and month is None):
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


def months_from(rows, now=None, ignore_relevance=False):
    """The definition at the top of this file, computed — in months.

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
    return sum(months_between(s, e) for s, e in merged)


def years_from(rows, now=None, ignore_relevance=False):
    """Completed years, not nearest: ada has five years and seven months of
    continuous work and every résumé in the world calls that five. round()
    made it six and disagreed with the answer key on the control case."""
    return months_from(rows, now, ignore_relevance) // 12


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


# A title that is BOTH a traineeship and a promotion describes a span whose
# countable part is not stated anywhere. "Software Engineer (promoted from
# Software Engineer Trainee)" is twenty months of which an unknown prefix
# does not count.
PROMOTION_MARKERS = ("promoted", "promotion")


# A date belongs to a row if it can be reached from that row's own name
# WITHOUT crossing a section heading.
#
# Distance alone cannot do it, in either direction. A compact résumé puts
# the education section a few lines under the last job, so any window wide
# enough for a real entry also borrows a degree's years. And a table
# layout wraps every cell onto its own line —
#
#     Zenith / Softworks / Kochi, / India / May 2025 – Jul / 2025
#
# — so the dates of a genuine row can sit six lines from its employer with
# nothing wrong at all. What separates the two cases is not how far apart
# they are but whether a HEADING stands between them.
#
# The line cap is a backstop for one enormous section, not the main rule.
# check_years already treats a refused row as a floor rather than a zero,
# so being too strict costs a conservative number and being too loose
# costs 27 invented years.
NEARBY_LINES = 14

# The budget for a qualifier that belongs to the title itself, rather than
# to the entry's block.
QUALIFIER_LINES = 1

# Headings, as résumés write them. Deliberately the same shape as
# skill_scan's: a short line that is nothing but a section name.
_SECTION_LINE = re.compile(
    r"^\s*(professional\s+summary|summary|about\s+me|profile|objective|"
    r"core\s+skills|technical\s+skills|skills|competenc\w*|technolog\w*|"
    r"professional\s+experience|work\s+experience|experience|employment|"
    r"projects?|portfolio|education|academics?|qualifications?|coursework|"
    r"certifications?|licen[cs]es?|courses?|training|publications?|awards?|"
    r"interests?|references?|languages?)\s*:?\s*$", re.I)

# Below this a company or title is too short to locate reliably, so the
# row is judged unlocatable rather than matched to the wrong place.
LOCATABLE = 3


def _folded(text):
    """(folded text, index back into the original).

    Folding is how "Harbourline S ystems" is found at all; the index
    is how the match is then given a position on the real page.
    """
    keep, index = [], []
    for at, char in enumerate(str(text or "").lower()):
        if char.isalnum() or char in "+#":
            keep.append(char)
            index.append(at)
    return "".join(keep), index


def _occurrences(needle, folded, index):
    """Original-text offsets where this value appears."""
    key = _key(needle)
    if len(key) < LOCATABLE:
        return []
    out, at = [], folded.find(key)
    while at >= 0:
        out.append(index[at])
        at = folded.find(key, at + 1)
    return out


def anchors(row, text):
    """Where this row's own employer or title sits in the document.

    Empty means the row names nobody findable — which is not a licence to
    validate its dates against the whole page.
    """
    folded, index = _folded(text)
    spots = []
    for field in ("company", "title"):
        spots.extend(_occurrences(row.get(field), folded, index))
    return sorted(set(spots))


def _lines(text):
    """(offset -> line number, {line numbers that are section headings})."""
    raw = str(text or "")
    breaks = [at for at, char in enumerate(raw) if char == "\n"]
    headings = {n for n, line in enumerate(raw.splitlines())
                if len(line.strip()) <= 40 and _SECTION_LINE.match(line)}

    def line(offset):
        lo, hi = 0, len(breaks)
        while lo < hi:
            mid = (lo + hi) // 2
            if breaks[mid] < offset:
                lo = mid + 1
            else:
                hi = mid
        return lo
    return line, headings


def _near(value, text, spots, folded=None, index=None, placed=None,
          within=None):
    """Is this value in the same block of the document as one of those
    anchors — no heading between them, and not absurdly far?

    `within` tightens the line budget for callers that need adjacency
    rather than block membership. A date can be six lines from its
    employer in a table layout; a title's own qualifier cannot.
    """
    if folded is None:
        folded, index = _folded(text)
    line, headings = placed or _lines(text)
    budget = NEARBY_LINES if within is None else within
    for at in _occurrences(value, folded, index):
        here = line(at)
        for spot in spots:
            there = line(spot)
            lo, hi = (here, there) if here < there else (there, here)
            if hi - lo > budget:
                continue
            if any(lo < n <= hi for n in headings):
                continue
            return True
    return False


def row_problems(row, text, now=None):
    """Why this employment row cannot be counted, if it cannot.

    check_grounding validates the FIELDS call. Employment rows come from a
    second call and were never checked against the document at all, so the
    date arithmetic — which is exact, and tested 52/52 — ran over whatever
    the model said. An offline row reading "Initech Global Holdings /
    Principal Architect / March 1999 - Present", with no part of it in the
    résumé, was accepted as 27 years of experience.
    """
    bad = []
    for field in ("company", "title"):
        value = str(row.get(field) or "").strip()
        if value and not supported(value, text):
            bad.append(f"{field} {value!r} is not in the document")

    # Where this row's own entry sits. Everything below is checked against
    # THOSE positions rather than against the whole page: a year is only
    # this job's year if it is written next to this job.
    spots = anchors(row, text)
    folded, index = _folded(text)
    placed = _lines(text)
    if not spots:
        bad.append("names no employer or title that can be found in the "
                   "document, so its dates cannot be checked against it")

    start, end = parse_month(row.get("start"), now), parse_month(row.get("end"), now)
    for field, when in (("start", start), ("end", end)):
        raw = str(row.get(field) or "").strip().lower()
        if raw in PRESENT_WORDS:
            # An open end is a word, not a date — but the document has to
            # agree that the job is open. Replacing a real "December 2024"
            # with "Present" added two years of experience nobody claimed.
            if spots and not any(
                    _near(word, text, spots, folded, index, placed)
                    for word in PRESENT_WORDS):
                bad.append(f"{field} says {row.get(field)!r}, and nothing "
                           f"beside this role says it is still current")
            continue
        if not when:
            # Unreadable is already check_years' business — it counts
            # those as a floor rather than dropping the row.
            continue
        # The YEAR, not the whole date: a résumé writes "Jan 2025" and the
        # model returns "January 2025", which no string comparison
        # survives. What changed is WHERE it has to appear.
        if spots and not _near(str(when[0]), text, spots, folded, index,
                               placed):
            bad.append(f"{field} year {when[0]} is not written beside this "
                       f"role in the document")

    if start and end and end < start:
        bad.append(f"ends {row.get('end')!r} before it starts "
                   f"{row.get('start')!r}")
    horizon = now or today()
    if start and start > horizon:
        bad.append(f"starts {row.get('start')!r} in the future")
    if end and str(row.get("end") or "").strip().lower() not in PRESENT_WORDS \
            and end > horizon:
        bad.append(f"ends {row.get('end')!r} in the future")
    return bad


def ambiguous_span(row, text=""):
    """Is this row's countable span unknowable from the document?

    Read the DOCUMENT, not only the model's title. The audited résumé says

        Software Engineer (promoted from Software Engineer Trainee)

    and the model returned "Software Engineer" — true, perfectly grounded,
    and missing the one word that made the interval uncertain. Reading the
    title alone meant a simplification silently converted an unresolved
    traineeship into fully known professional experience.

    The qualifier has to belong to THIS row, so it is looked for beside
    the row's own anchors, exactly as its dates are. No promotion date is
    invented: the span is still counted as a floor. What is added is that
    the floor is declared.
    """
    title = str(row.get("title") or "").lower()
    if (any(word in title for word in NOT_PROFESSIONAL)
            and any(mark in title for mark in PROMOTION_MARKERS)):
        return True
    if not text:
        return False
    spots = anchors(row, text)
    if not spots:
        return False
    folded, index = _folded(text)
    placed = _lines(text)
    # Adjacent, not merely in the same block: the qualifier lives in the
    # title itself. A wider budget let one job's "(promoted from Trainee)"
    # mark the NEXT job uncertain in a document with no headings between
    # them.
    return (any(_near(word, text, spots, folded, index, placed, QUALIFIER_LINES)
                for word in NOT_PROFESSIONAL)
            and any(_near(mark, text, spots, folded, index, placed,
                          QUALIFIER_LINES)
                    for mark in PROMOTION_MARKERS))


def check_employment(rows, text, now=None):
    """(corrections, escalations, rows_that_may_be_counted).

    Same shape and the same share threshold as check_grounding, because it
    is the same judgement: one bad row is a row to drop, most of them bad
    is a parse that cannot be trusted row by row.
    """
    # Defensive at the shared seam rather than at each caller: every path to
    # the date arithmetic comes through here, and a row that is a string
    # rather than an object used to reach row.get() and raise AttributeError
    # several frames down. A malformed answer is a parse to escalate, not a
    # crash.
    if not isinstance(rows, dict):
        return [], [f"employment: the model's answer is "
                    f"{type(rows).__name__}, not an object"], []
    rows = list(rows.get("employment") or [])
    if not rows:
        return [], [], []
    if not all(isinstance(row, dict) for row in rows):
        return [], [f"employment: {sum(1 for r in rows if not isinstance(r, dict))} "
                    f"of {len(rows)} rows are not objects"], []

    problems = [(row, row_problems(row, text, now)) for row in rows]
    bad = [(row, why) for row, why in problems if why]
    if not bad:
        corrections = []
    elif len(bad) / len(rows) > CONFABULATION_SHARE:
        detail = "; ".join(why[0] for _row, why in bad[:3])
        return [], [f"employment: {len(bad)} of {len(rows)} rows are not "
                    f"supported by the document ({detail})"], []
    else:
        corrections = [{
            "field": "employment", "action": "excluded",
            "removed": [row for row, _why in bad],
            "why": "not supported by the document: "
                   + "; ".join("; ".join(why) for _row, why in bad)}]

    kept = [row for row, why in problems if not why]
    # Not a drop, and deliberately not a guess. The row is real and the
    # promotion is real; only the DATE the traineeship ended is missing.
    # is_professional already excludes it, so the count is a floor — say
    # so, rather than letting zero read as a measurement.
    unclear = [row for row in kept if ambiguous_span(row, text)]
    if unclear:
        corrections.append({
            "field": "employment", "action": "flagged",
            "removed": unclear,
            "why": f"{len(unclear)} row(s) record a promotion out of a "
                   f"traineeship without saying when, so the professional "
                   f"span is unknown and counts as zero — a floor, not a "
                   f"measurement. Needs review."})
    return corrections, [], kept


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
    # BEFORE check_years, and it hands back the rows rather than only a
    # verdict: the arithmetic is exact, so the only way to keep it honest
    # is to stop unsupported rows reaching it.
    row_fixes, row_stops, kept = check_employment(rows, text, now)
    year_fixes, year_stops = check_years(parsed, {"employment": kept}, now)
    reasons = grounding_stops + row_stops + year_stops
    if reasons:
        return {"decision": "escalate", "result": None,
                "corrections": [], "reasons": reasons}

    result = dict(parsed)
    corrections = grounding_fixes + row_fixes + year_fixes
    for fix in corrections:
        if fix["action"] == "dropped":
            removed = {_key(v) for v in fix["removed"]}
            result[fix["field"]] = [v for v in result[fix["field"]]
                                    if _key(v) not in removed]
        elif fix["action"] == "computed":
            result[fix["field"]] = fix["to"]
    # The rows travel WITH the verdict. Returning only a decision left the
    # caller holding the model's original list, and read() recomputed from
    # it — so a row this function had just rejected came back as 27 years
    # of experience while the correction beside it still said the row was
    # excluded. One validated list, one truth.
    return {"decision": "corrected" if corrections else "accept",
            "result": result, "corrections": corrections, "reasons": [],
            "employment": kept}


def read(model=None, text="", timeout=TIMEOUT, url=None, now=None,
         backend=None):
    """Both calls and the verdict, for one résumé.

    Returns (checked_fields, employment_answer, decision). `checked_fields`
    is None when the decision is to escalate — the caller falls back.
    """
    fields, _seconds = extract(model, text, timeout, url, backend)
    rows = employment(model, text, timeout, url, backend)
    decision = route(fields, rows, text, now)
    checked = decision["result"]
    if checked is not None:
        # The VALIDATED rows, and they replace the answer the model gave —
        # every consumer downstream reads `rows`, so leaving the originals
        # here is what let a rejected row derive a search title as well as
        # an experience total.
        rows = dict(rows or {}, employment=list(decision["employment"]))
        months = months_from(rows["employment"], now)
        checked["years_experience"] = months // 12
        # The remainder, for display only. Both consumers of the number
        # compare it against a posting's stated floor, so they keep the
        # whole years; a review screen reading "1 year" for 1y10m is what
        # made someone distrust the whole parse.
        checked["experience_months"] = months
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
    assert months_from(ada, now) == 67, months_from(ada, now)  # 5y7m

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
    # The dates and the second title are here because check_employment reads
    # this text too: the route cases below reuse `ada` and `hana`, and rows
    # whose employer, title or year is nowhere in the document no longer
    # reach the arithmetic.
    text = ("Ada Okonkwo. Backend Engineer at Fettle Health, Jan 2021 - Mar "
            "2023. Senior Backend Engineer, Apr 2023 - Present. Structural "
            "Engineer, Jan 2014 - Dec 2021. python, django.")
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
    # The title is one the document really carries: this case is about
    # unreadable DATES, and a row that also fails grounding would escalate
    # for the other reason and prove nothing about check_years.
    blind = route({"name": "Ada Okonkwo", "years_experience": 3},
                  {"employment": [{"title": "Backend Engineer", "start": "?",
                                   "end": "?", "relevant": True}]}, text)
    assert blind["decision"] == "escalate" and "could be read" in blind["reasons"][0]

    # An employment row is checked against the document too. Exact
    # arithmetic over an employer nobody wrote down is still exact.
    invented = [{"company": "Initech Global Holdings",
                 "title": "Principal Architect", "start": "March 1999",
                 "end": "Present", "relevant": True}]
    fake = route({"name": "Ada Okonkwo", "years_experience": 27},
                 {"employment": invented}, text)
    assert fake["decision"] == "escalate", fake
    assert "not supported by the document" in fake["reasons"][0], fake
    # One bad row among good ones is a correction, not an escalation —
    # the same share rule check_grounding uses.
    mixed = route({"name": "Ada Okonkwo", "years_experience": 27},
                  {"employment": ada + invented}, text)
    assert mixed["decision"] == "corrected", mixed
    assert mixed["result"]["years_experience"] == 5, mixed
    # A range that ends before it begins counted zero and said nothing.
    backwards = {"title": "Backend Engineer", "start": "Mar 2023",
                 "end": "Jan 2021", "relevant": True}
    assert any("before it starts" in why
               for why in row_problems(backwards, text, now))
    # A promotion out of a traineeship with no date is a floor, not a
    # measurement, and says so rather than answering zero quietly.
    assert ambiguous_span({"title": "Engineer (promoted from Trainee)"})
    assert not ambiguous_span({"title": "Engineer (promoted)"})

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
