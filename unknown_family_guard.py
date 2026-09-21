"""A title nobody can classify has to earn its place some other way.

`role_evidence.family_of` answers with one of sixteen families, or None. None
means FAIL OPEN: a query the taxonomy cannot place is never rejected, which is
the right default — the taxonomy is incomplete, and Revenue Operations is a
real profession it has simply never learned.

The defect is what that costs. Being unclassifiable removes EVERY
profession-family check at once, so a one-advert artefact and a genuine
profession arrive on identical terms:

    node js                                 2 postings, 1 employer
    quality analyst                         1 posting,  1 employer
    ai-native software entwickler (m/w/d)   3 postings, 1 employer
    revenue operations                      6 postings, 4 employers

Measured across 15 personas in docs/corpus-role-tail-audit.md.

    filter_queries(queries, held, admission, trace) -> kept, record

So this guard asks the ONE question the taxonomy's silence left unasked: how
much does this actually rest on?

    family is None  AND  corpus-derived  AND  (postings < 10 OR employers < 3)
        -> reject

It rejects for THIN EVIDENCE, never for taxonomy ignorance. That distinction is
the whole design: a profession the taxonomy has not learned, posted forty times
by twelve employers, survives. A duplicate advert does not. On today's corpus
the two rules happen to remove the same six titles; the difference is what
happens the first time they do not coincide.

WHAT IT NEVER TOUCHES
---------------------
* A KNOWN family. 54 of 73 admitted corpus roles rest on fewer than ten
  postings, and most are useful — `backend developer`, `java developer`,
  `associate software engineer`. A blanket evidence floor removes three
  quarters of every search and strands a persona with nothing. This guard is
  for unknown family AND thin evidence, never either alone.
* A HELD or grounded résumé title. Someone whose job is "Revenue Operations
  Manager" must be able to search for it, and the taxonomy's gaps must never
  be what stops them.
* Anything canonical_guard already rejected. `sf -data cloud` never reaches
  here, and the record says which layer took it.

Off unless SWEEP_UNKNOWN_FAMILY_GUARD is set, like every other V3 step. With
the flag absent nothing below runs and the query list is the one v2 produced.

    python unknown_family_guard.py --demo
"""
import os

import role_evidence

FLAG = "SWEEP_UNKNOWN_FAMILY_GUARD"

# The two floors. Postings are matched ROWS, not deduplicated jobs — nothing
# in this pipeline dedupes by job identity, and `product owner` reached a
# candidate on one advert listed three times. The employer floor is what
# actually stops that, which is why both are required rather than either.
MIN_POSTINGS = 10
MIN_COMPANIES = 3

REASON = "unknown_family_thin_evidence"


def enabled():
    """Read per call, so a rollback lands on the next request."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def _stats_for(query, fragment, admission):
    """The admission record for this query, or None when there is none.

    Keyed by the FRAGMENT keywords_for scored, because canonicalise may have
    replaced it with a real corpus title by the time it reaches here.
    """
    for key in (fragment, query):
        if key and key in admission:
            return admission[key]
        if key and str(key).lower() in admission:
            return admission[str(key).lower()]
    return None


def assess(query, fragment, admission):
    """(keep, why) for one query. Never raises, never consults the market."""
    family = role_evidence.family_of(query)
    if family is not None:
        return True, {"family": family, "rule": "known_family"}
    stats = _stats_for(query, fragment, admission)
    if stats is None:
        # No admission record means this did not come from the corpus path
        # this guard measures. Unmeasured is not evidence of thinness.
        return True, {"family": None, "rule": "no_admission_record"}
    postings = int(stats.get("postings") or 0)
    companies = int(stats.get("companies") or 0)
    row = {"family": None, "postings": postings, "companies": companies,
           "weight": stats.get("weight"),
           "min_postings": MIN_POSTINGS, "min_companies": MIN_COMPANIES}
    if postings >= MIN_POSTINGS and companies >= MIN_COMPANIES:
        return True, dict(row, rule="unknown_family_well_supported")
    short = ("postings" if postings < MIN_POSTINGS else "companies")
    return False, dict(row, rule=REASON, reason=REASON, short_of=short)


def filter_queries(queries, held=(), admission=None, trace=()):
    """(kept, record). Reject-only, and it never removes the last query.

    `held` is the person's own job titles — exempt whatever the taxonomy
    thinks. `admission` is local_search.fields_for's per-fragment record.
    `trace` is its canonical fragment->title pairs, so a replaced title can
    be measured on the fragment that earned it.
    """
    queries = list(queries or [])
    record = {"flag": FLAG, "min_postings": MIN_POSTINGS,
              "min_companies": MIN_COMPANIES, "decisions": [], "rejected": []}
    if not queries or not admission:
        return queries, record
    exempt = {str(h).strip().lower() for h in (held or ()) if str(h).strip()}
    back = {str(title).strip().lower(): frag
            for frag, title in (trace or ()) if title}

    kept = []
    for query in queries:
        low = str(query).strip().lower()
        if low in exempt:
            record["decisions"].append(
                {"query": query, "kept": True, "rule": "held_title"})
            kept.append(query)
            continue
        ok, why = assess(query, back.get(low), admission)
        record["decisions"].append(dict({"query": query, "kept": ok}, **why))
        if ok:
            kept.append(query)
        else:
            record["rejected"].append(query)

    # The last surviving query is never taken away. Same fail-open the role
    # gate has, and for the same reason: a candidate with nothing to search
    # for is a worse outcome than one thin query.
    if not kept and queries:
        record["fail_open"] = ("every query was thin; kept them rather than "
                               "leave the candidate with nothing to search")
        return queries, dict(record, rejected=[])
    return kept, record


def explain(record):
    """The rejections in the résumé's own numbers, one line each."""
    if not record or not record.get("rejected"):
        return ""
    lines = ["    unknown-family guard:"]
    for row in record["decisions"]:
        if row.get("kept"):
            continue
        lines.append(
            f"      {row['query']!r}: no family, and {row['postings']} matched "
            f"posting(s) from {row['companies']} employer(s) — needs "
            f"{MIN_POSTINGS} and {MIN_COMPANIES}")
    return "\n".join(lines)


def demo():
    """Self-check. No corpus, no model — the rule is arithmetic."""
    adm = {
        "node js": {"postings": 2, "companies": 1, "weight": 14.7},
        "revenue operations": {"postings": 6, "companies": 4, "weight": 55.1},
        "revops big": {"postings": 40, "companies": 12, "weight": 400.0},
        "twenty one co": {"postings": 20, "companies": 2, "weight": 200.0},
        "nine ten co": {"postings": 9, "companies": 10, "weight": 90.0},
        "exactly": {"postings": 10, "companies": 3, "weight": 100.0},
        "salesforce administrator": {"postings": 1, "companies": 1, "weight": 11.0},
    }
    os.environ[FLAG] = "1"
    assert enabled()

    kept, rec = filter_queries(
        ["node js", "revenue operations", "revops big", "twenty one co",
         "nine ten co", "exactly", "salesforce administrator"],
        held=(), admission=adm)
    assert "node js" not in kept, kept
    assert "revenue operations" not in kept, kept          # 6 postings
    assert "revops big" in kept, kept                      # 40 / 12
    assert "twenty one co" not in kept, kept               # 2 employers
    assert "nine ten co" not in kept, kept                 # 9 postings
    assert "exactly" in kept, kept                         # on the floor
    # known family, one posting: untouched
    assert "salesforce administrator" in kept, kept
    assert rec["rejected"] == ["node js", "revenue operations",
                               "twenty one co", "nine ten co"], rec["rejected"]

    # a held title is exempt however thin and however unclassifiable
    kept, _ = filter_queries(["node js"], held=["node js"], admission=adm)
    assert kept == ["node js"]

    # a canonical replacement is measured on the fragment that earned it
    kept, _ = filter_queries(["Revenue Operations Lead"], held=(),
                             admission=adm,
                             trace=[("revops big", "Revenue Operations Lead")])
    assert kept == ["Revenue Operations Lead"], kept

    # never the last query
    kept, rec = filter_queries(["node js"], held=(), admission=adm)
    assert kept == ["node js"] and rec.get("fail_open")

    del os.environ[FLAG]
    assert not enabled()
    print("unknown_family_guard: all checks passed")


if __name__ == "__main__":
    demo()
