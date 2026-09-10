"""POC: how much of the profile schema needs a model at all?

make_profile asks Gemini for 13 required fields. They are not one kind of
thing, and the difference decides whether a local model has to be good or
merely adequate:

  EXTRACTION   what the résumé says          — text, findable
  JUDGMENT     what the job market is like   — not in the résumé at all

This fills every field it can WITHOUT a model, using two vocabularies the
repo already owns: config.ATS_TITLE_HINTS plus the real titles in output/,
and the skill terms observed across thousands of real listings. Then it
reports what is left over.

The point is the leftover, not the parser. If eleven fields fall out of
rules and a corpus, a local model only has to write two sentences of prose
— and a 4B model can do that. If the rules cover four, the local model has
to be genuinely good and the POC is a different, harder project.

Read-only: no model call, no writes outside stdout.

    python -m poc.local_parse                    # both résumés on disk
    python -m poc.local_parse path/to/cv.pdf
    python -m poc.local_parse --demo             # self-check, no files
"""

import os
import re
import sys

from poc import corpus_weights

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every field make_profile.RESPONSE_SCHEMA requires, and how this fills it.
#   "text"   — read out of the résumé
#   "corpus" — measured over real listings in output/
#   "prose"  — a sentence a human reads; no rule writes this
FIELD_SOURCE = {
    "candidate_name": "text",
    "years_experience": "text",
    "role_keywords": "text",
    "skill_weights": "text+corpus",
    "penalty_terms": "corpus",
    "domain_half_a": "text",
    "domain_half_b": "text",
    "domain_title_terms": "text",
    "title_hints": "text",
    "title_exclude": "corpus",
    "domain_bonus": "rule",
    "field_summary": "prose",
    "notes": "prose",
}

# Seniority and discipline words that belong in title_exclude rather than in
# a skill list. Kept short and explicit: this is a POC, and a long list here
# would be the taxonomy problem in disguise.
SENIORITY = ("intern", "internship", "principal", "staff", "director",
             "vp", "head of", "manager", "architect")
OFF_DISCIPLINE = ("sales", "marketing", "recruiter", "hr ", "accountant",
                  "civil", "mechanical", "teacher", "nurse")

_WORD = re.compile(r"[a-z0-9][a-z0-9+#.\- ]*")


def read_pdf(path):
    """Résumé text. pypdf is already a dependency — nothing new is added."""
    from pypdf import PdfReader
    return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)


def _has_term(text, term):
    """Whole-term match, the same shape scraper._compile uses.

    Substring matching is what makes a naive parser claim "r" and "go" from
    every résumé ever written.
    """
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])",
                     text) is not None


def skills_in(text, vocabulary):
    """Which of a known vocabulary this résumé actually names.

    A closed vocabulary rather than open-ended noun extraction: the terms
    that matter are the ones the scorer can match against a listing, and
    those are exactly the terms already seen in real listings.
    """
    low = text.lower()
    return sorted(t for t in vocabulary if _has_term(low, t))


def years_experience(text):
    """Years, from an explicit claim or from the earliest year present.

    Two readings, and the explicit one wins: "5+ years" is the candidate's
    own summary, while the earliest date is an inference that a degree year
    or a certification date can throw off.
    """
    stated = re.search(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", text, re.I)
    if stated:
        return int(stated.group(1))
    years = [int(y) for y in re.findall(r"\b(20[0-2]\d)\b", text)]
    if not years:
        return None
    import datetime
    return max(0, datetime.date.today().year - min(years))


def candidate_name(text):
    """The first line that reads like a name.

    Deliberately conservative. A wrong name is worse than none: it becomes
    the profile filename, and Sweep's review screen prefills it.
    """
    for line in (ln.strip() for ln in text.splitlines()):
        if not line or "@" in line or any(c.isdigit() for c in line):
            continue
        words = line.split()
        if 2 <= len(words) <= 4 and all(w[:1].isupper() for w in words if w):
            return line
    return ""


def role_keywords(text, titles, limit=6):
    """Titles to search for, taken from titles that REALLY EXIST in the market.

    Not invented: every candidate here was scraped off a live posting, so a
    title this returns is one that returns results. A title the model
    invents can search for nothing at all.
    """
    low = text.lower()
    hits = [t for t in titles if _has_term(low, t)]
    # Longest first: "senior react native developer" beats "developer".
    hits.sort(key=lambda t: (-len(t), t))
    kept = []
    for title in hits:
        if not any(title in k for k in kept):
            kept.append(title)
        if len(kept) >= limit:
            break
    return kept


def derive(text, vocabulary, titles, freqs):
    """Every field this can fill without a model."""
    skills = skills_in(text, vocabulary)
    weights = {}
    for term in skills:
        hits, seen = freqs.get(term, (0, 0))
        # Unmeasured terms sit mid-scale rather than at either extreme: the
        # corpus has no opinion, so neither does this.
        weights[term] = (corpus_weights.measured_weight(hits / seen)
                         if seen >= 200 else 3)

    low = text.lower()
    # Terms common in this market that the résumé never mentions: the
    # candidate is not competing for those jobs.
    absent = [t for t, (hits, seen) in freqs.items()
              if seen >= 200 and hits / seen >= 0.05 and not _has_term(low, t)]
    absent.sort(key=lambda t: -freqs[t][0] / freqs[t][1])

    return {
        "candidate_name": candidate_name(text),
        "years_experience": years_experience(text),
        "role_keywords": role_keywords(text, titles),
        "skill_weights": weights,
        "penalty_terms": absent[:12],
        "title_exclude": [w for w in SENIORITY + OFF_DISCIPLINE
                          if not _has_term(low, w.strip())][:12],
        "domain_bonus": 5,
        "field_summary": None,   # prose
        "notes": None,           # prose
    }


def corpus_vocabulary(freqs, min_listings=200):
    """Skill terms real listings actually used, as the closed vocabulary."""
    return sorted(t for t, (_hits, seen) in freqs.items() if seen >= min_listings)


def corpus_titles(output_dir=None):
    """Distinct job titles seen in output/, lowercased and trimmed."""
    import csv
    import glob
    output_dir = output_dir or os.path.join(REPO, "output")
    titles = set()
    for path in glob.glob(os.path.join(output_dir, "**", "*.csv"),
                          recursive=True):
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    t = (row.get("title") or "").strip().lower()
                    # Long titles are one posting's marketing, not a search
                    # term. Short ones ("engineer") match everything.
                    if 8 <= len(t) <= 45 and "(" not in t:
                        titles.add(t)
        except (OSError, UnicodeDecodeError, csv.Error):
            continue
    return sorted(titles)


def coverage(filled):
    """How many required fields came out, and what is left for a model."""
    got, missing = [], []
    for field in FIELD_SOURCE:
        value = filled.get(field, "__absent__")
        if value in ("__absent__", None, "", [], {}):
            missing.append(field)
        else:
            got.append(field)
    return got, missing


def demo():
    text = ("Asha Menon\nasha@example.com  +91 90000 00000\n"
            "Senior React Native Developer with 6 years building mobile apps.\n"
            "Skills: React Native, TypeScript, Node.js, GraphQL, Redis\n")
    vocab = ["react native", "typescript", "node.js", "graphql", "redis",
             "salesforce"]
    freqs = {"react native": (120, 10000), "typescript": (3200, 10000),
             "node.js": (2100, 10000), "graphql": (40, 10000),
             "redis": (900, 10000), "salesforce": (700, 10000)}

    assert candidate_name(text) == "Asha Menon"
    assert candidate_name("no name here 123") == "", "a wrong name is worse than none"
    assert years_experience(text) == 6
    assert years_experience("worked 2019 to 2021") is not None
    assert years_experience("nothing dated here") is None

    assert _has_term("i use go daily", "go")
    assert not _has_term("golang is good", "go"), "substrings are not terms"
    assert skills_in(text, vocab) == ["graphql", "node.js", "react native",
                                      "redis", "typescript"]

    out = derive(text, vocab, ["senior react native developer"], freqs)
    # Measured, not guessed: typescript is in a third of postings, graphql
    # in 0.4% — so the rare one carries the signal.
    assert out["skill_weights"]["typescript"] == 1
    assert out["skill_weights"]["graphql"] == 5
    assert out["role_keywords"] == ["senior react native developer"]
    # A common market term the résumé never mentions.
    assert "salesforce" in out["penalty_terms"]
    assert out["candidate_name"] == "Asha Menon"

    got, missing = coverage(out)
    assert set(missing) >= {"field_summary", "notes"}, "prose needs a model"
    assert "skill_weights" in got and "role_keywords" in got
    print("local_parse demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not paths:
        d = os.path.join(REPO, "auto-apply", "resume")
        paths = [os.path.join(d, f) for f in sorted(os.listdir(d))
                 if f.lower().endswith(".pdf")]

    freqs = corpus_weights.corpus_frequencies()
    vocab = corpus_vocabulary(freqs)
    titles = corpus_titles()
    print(f"vocabulary: {len(vocab)} skill terms, {len(titles):,} real titles"
          f" — both measured from output/, nothing hand-written\n")

    for path in paths:
        text = read_pdf(path)
        out = derive(text, vocab, titles, freqs)
        got, missing = coverage(out)
        print("=" * 72)
        print(f"{os.path.basename(path)}   ({len(text.split())} words)")
        print("=" * 72)
        print(f"  name          {out['candidate_name'] or '(none found)'}")
        print(f"  years         {out['years_experience']}")
        print(f"  titles        {', '.join(out['role_keywords']) or '(none)'}")
        top = sorted(out["skill_weights"].items(), key=lambda kv: (-kv[1], kv[0]))
        print(f"  skills        {len(out['skill_weights'])} found; "
              f"top by measured signal:")
        for term, w in top[:8]:
            hits, seen = freqs.get(term, (0, 0))
            share = f"{hits / seen:5.1%}" if seen else "   n/a"
            print(f"                  {w}  {term:<22} {share} of listings")
        print(f"  penalties     {', '.join(out['penalty_terms'][:6])}")
        print(f"\n  FIELDS FILLED WITHOUT A MODEL   {len(got)}/{len(FIELD_SOURCE)}")
        print(f"  still needs one                 {', '.join(missing)}\n")


if __name__ == "__main__":
    main()
