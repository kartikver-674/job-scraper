"""Measure the profile fields that spend money, and check them against reality.

The extraction benchmark answered a different question. name, skills, titles
and years are READ off a résumé and can be graded against an answer key.
The fields here are GENERATED — twenty to forty role keywords, two weighted
lists, four domain lists, two title gates — and there is no answer key for
them, because there is no single right set of search keywords for a person.

There is something better, though, and it is already on disk. output/ holds
15,514 real job titles with the score each row was given, so a keyword's
value is measurable: how many listings it draws, what they score, and how
many of them the pipeline throws away after paying for them.

WHY THESE FIELDS AND NOT THE OTHERS
-----------------------------------
Each one moves money or inventory:

  role_keywords       one paid search per keyword per location. Apify's
                      actors bill per RESULT, so a keyword that draws 4,890
                      rows averaging 3.6 costs real dollars for rows that
                      then get dropped. This is the field that spends.
  title_exclude       wins over every other gate (scraper.is_dev_title
                      checks it first), so an entry that matches the
                      person's own target roles deletes inventory silently.
                      Nothing downstream reports what it removed.
  title_hints         the gate on every FREE source. A missing fragment is
                      inventory nobody ever sees; an over-broad one floods
                      the free sources with adjacent-industry rows.
  domain_title_terms  flags a job as a match FROM THE TITLE ALONE, bypassing
                      every other piece of evidence (RULE 2). A bare job
                      function here promotes jobs on no evidence at all.
  penalty_terms       negated at render time. A term that matches the
                      person's own skills penalises their own profile.
  domain_half_a/b     an "both sides of my field" bonus. Generic category
                      words in a half hand the bonus to any adjacent job.

THE MEASURE, AND ITS ONE HONEST LIMITATION
------------------------------------------
Keywords are matched against the TITLES OF SCRAPED LISTINGS, not against
the search keywords that drew them. That distinction is the whole
methodology: output/ was scraped using keywords a previous model wrote, so
scoring a keyword by "did this keyword appear in the search log" would rate
the incumbent perfect and every alternative zero by construction. Listing
titles are independent of which query found them.

Two limitations remain, and both are reported rather than smoothed over.

The corpus is one person's market, so a keyword for a profession Sweep has
never searched is unmeasurable here, not bad. The report says "unmeasured".

The bigger one: the `score` column was computed with the CORPUS OWNER's
skill_weights. Precision — the share of drawn rows reaching score 20 — is
therefore only a fair measure for someone in that owner's field. ada is a
Django backend engineer and the corpus owner is not, so rows that are right
for her score low, and her 5.2% is partly this artifact rather than her
keywords. Precision is still worth reporting, because a keyword drawing
30,000 rows of which almost none score is a bad keyword under any scoring,
but it is not the number to hang a verdict on.

The verdict-grade numbers are the ones no scoring touches:

  hard-dropped share   of the rows a keyword buys, the share config
                       DELETES on the title alone. Pure waste, and
                       title_excluded and hard_drop_terms decide it
                       without reference to anyone's weights.
  wildcard count       a keyword matching a quarter of the market
  stability            agreement between two renderings of one résumé
  rule compliance      an empty title_exclude, a missing stem, an
                       internship keyword under exclude_levels

    python -m bench.search_fields --demo
    python -m bench.search_fields qwen3:8b        # generate, then measure
    python -m bench.search_fields --reference     # what the shipped profiles score
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto-apply"))

HERE = os.path.dirname(os.path.abspath(__file__))

# Seniority is already handled twice over in config.SCORING — hard_drop_terms
# removes those rows and soft_drop_terms down-ranks them — which is why RULE 3
# says title_exclude is for DIFFERENT CAREERS and never for seniority. A
# seniority word in a title gate is redundant at best and, in title_exclude,
# deletes rows the pipeline was going to rank rather than remove.
def _seniority():
    import config
    return (tuple(config.SCORING["hard_drop_terms"]),
            tuple(config.SCORING["soft_drop_terms"]))


# A keyword matching more than this share of the whole market is not a
# keyword, it is a wildcard: it buys the catalogue and lets scoring sort it
# out, which is exactly the spend the budget screen exists to prevent.
WILDCARD_SHARE = 0.25

# Below this many listings a keyword is not "bad", it is unmeasured — this
# corpus is one person's market and cannot speak for another profession.
UNMEASURED = 1

# What a keyword's rows cost. LinkedIn is the cheapest paid source and so
# the most conservative choice for a waste figure: quoting Naukri's $0.50
# per 50 would flatter the argument.
CHEAPEST_RATE, CHEAPEST_BASIS = 0.045, 25


def load_titles(output_dir=None):
    """[(title, score)] for every scraped listing. The market, as measured."""
    import corpus_signal
    return corpus_signal.title_yield(output_dir)


def yield_of(term, titles):
    """(listings drawn, mean score) for one keyword, substring-matched."""
    import corpus_signal
    return corpus_signal.keyword_yield(term, titles)


def droppable(title, hard):
    """Would the pipeline delete this row after paying for it?

    Word-boundary on purpose, matching config's own matcher: "intern" must
    not fire inside "internal" or "international".
    """
    import re
    low = title.lower()
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low)
               for w in hard)


def spend(listings):
    """Dollars for that many results at the cheapest paid rate."""
    return math.ceil(listings / CHEAPEST_BASIS) * CHEAPEST_RATE


def keyword_rows(terms, titles, hard):
    """Per keyword: what it draws, what that scores, and what is wasted."""
    rows = []
    for term in terms:
        listings, mean = yield_of(term, titles)
        matched = [(t, s) for t, s in titles if term.strip().lower() in t]
        wasted = [s for t, s in matched if droppable(t, hard)]
        rows.append({
            "term": term,
            "listings": listings,
            "mean_score": mean,
            "wasted_rows": len(wasted),
            "share": listings / len(titles) if titles else 0.0,
        })
    return rows


# config.py:534 already reasons in this band — "11 of the 15 reachable rows
# at score >= 20" — so the bar is the repo's own rather than one invented
# here. 15% of the corpus clears it, which is the baseline every
# title-alone promoter has to beat to be worth anything.
REACHABLE = 20


def baseline(titles):
    """Share of the whole market that reaches the readable band."""
    if not titles:
        return 0.0
    return sum(1 for _, s in titles if s >= REACHABLE) / len(titles)


def profile_of(term, titles, hard):
    """What one keyword actually buys: rows, reachable rows, wasted rows."""
    needle = term.strip().lower()
    if not needle:
        return {"term": term, "listings": 0, "reachable": 0, "wasted": 0,
                "share": 0.0, "precision": None}
    matched = [(t, s) for t, s in titles if needle in t]
    reachable = sum(1 for _, s in matched if s >= REACHABLE)
    wasted = sum(1 for t, _ in matched if droppable(t, hard))
    return {
        "term": term,
        "listings": len(matched),
        "reachable": reachable,
        "wasted": wasted,
        "share": len(matched) / len(titles) if titles else 0.0,
        "precision": reachable / len(matched) if matched else None,
    }


def _words(text):
    return set(str(text).lower().replace("-", " ").replace(".", " ").split())


def _has(term, words):
    """Does this term carry one of these words, as a word?"""
    return bool(_words(term) & set(words))


def check_role_keywords(profile, titles, hard, soft):
    """The field that spends. Corrections here are dollars, not tidiness."""
    terms = list(profile.get("role_keywords") or [])
    corrections, escalations = [], []
    wildcards, senior = [], []
    for term in terms:
        row = profile_of(term, titles, hard)
        # A keyword matching a quarter of the market is not a search, it is
        # the catalogue. "software engineer" draws 4,890 of 15,514 rows at a
        # mean of 3.6 and costs $8.82 at the cheapest paid rate.
        if row["share"] > WILDCARD_SHARE:
            wildcards.append(term)
        # A hard_drop word in a paid keyword buys rows config DELETES —
        # pure waste. A soft_drop word only costs them -4, so those rows
        # still reach the shortlist and dropping the keyword would lose
        # real inventory. config draws that line itself; so does this.
        if _has(term, hard):
            senior.append(term)
    if wildcards:
        corrections.append({
            "field": "role_keywords", "action": "dropped",
            "removed": wildcards,
            "why": f"each matches over {WILDCARD_SHARE:.0%} of the market, so "
                   f"the search buys the catalogue and pays per row"})
    if senior:
        corrections.append({
            "field": "role_keywords", "action": "dropped",
            "removed": senior,
            "why": "carries a config.SCORING hard_drop word, so every row "
                   "it matches is deleted after being paid for"})
    bad = len(set(wildcards) | set(senior))
    if terms and bad / len(terms) > 0.5:
        escalations.append(
            f"role_keywords: {bad} of {len(terms)} are wildcards or carry "
            f"seniority, which is not a list to correct item by item")
    return corrections, escalations


def check_penalties(profile):
    """A penalty on the person's own skill is a penalty on the person."""
    penalties = [e["term"] for e in profile.get("penalty_terms") or ()]
    own = {e["term"].strip().lower()
           for e in profile.get("skill_weights") or () if e.get("term")}
    clash = [p for p in penalties if p.strip().lower() in own]
    if not clash:
        return [], []
    return [{"field": "penalty_terms", "action": "dropped", "removed": clash,
             "why": "also listed as one of this person's own skills, and "
                    "penalty_terms is negated at render time"}], []


def check_title_exclude(profile, hard, soft):
    """The gate that wins over the others, so the gate that can delete work.

    scraper.is_dev_title checks title_exclude FIRST, so an entry matching
    the person's own target roles removes inventory before anything scores
    it, and nothing downstream reports what went missing.
    """
    excludes = list(profile.get("title_exclude") or [])
    targets = [t.strip().lower() for t in
               (list(profile.get("role_keywords") or [])
                + list(profile.get("title_hints") or [])) if t.strip()]
    corrections, escalations = [], []
    # One direction only. The test is "would a job titled like one of this
    # person's target roles be caught by this exclude fragment", which is
    # `exclude in target`. Testing both directions flagged the shipped
    # profiles' "sdet" because the hint "sde" is a substring of it — and
    # SDET is a different career, which is exactly what this gate is for.
    self_block = [e for e in excludes
                  if any(e.strip().lower() in t for t in targets)]
    # RULE 3: title_exclude is for DIFFERENT CAREERS, never for seniority.
    senior = [e for e in excludes
              if _has(e, hard + soft) and e not in self_block]
    if self_block:
        corrections.append({
            "field": "title_exclude", "action": "dropped",
            "removed": self_block,
            "why": "matches this person's own role_keywords or title_hints, "
                   "and title_exclude is checked first so it would delete "
                   "their target roles silently"})
    if senior:
        corrections.append({
            "field": "title_exclude", "action": "dropped", "removed": senior,
            "why": "seniority, which config.SCORING already ranks — RULE 3 "
                   "reserves this gate for different careers"})
    return corrections, escalations


def check_title_hints(profile, titles, hard, soft):
    """The gate on the free sources. Widening only, so the risk is one-sided."""
    hints = list(profile.get("title_hints") or [])
    corrections = []
    # This gate only ever WIDENS, so a present senior variant costs
    # nothing — RULE 3 says "err towards including a title". The real
    # failure is a MISSING stem: "senior developer" without "developer"
    # gates out every non-senior row, and those rows are the reachable
    # ones. So the stem is added rather than the hint removed. Deleting
    # hints here would lose inventory, which is the bug this gate exists
    # to prevent.
    have = {h.strip().lower() for h in hints}
    add = []
    for hint in hints:
        stem = " ".join(w for w in hint.lower().split() if w not in soft)
        stem = stem.strip()
        if stem and stem != hint.strip().lower() and stem not in have:
            add.append(stem)
            have.add(stem)
    if add:
        corrections.append({
            "field": "title_hints", "action": "added", "added": add,
            "why": "the seniority-free stem was missing, and without it the "
                   "gate drops every non-senior row — RULE 3 asks for "
                   "'developer', not only 'senior developer'"})
    return corrections, []


def check_domain(profile, titles, hard):
    """domain_title_terms promote on the title alone, so they must earn it."""
    terms = list(profile.get("domain_title_terms") or [])
    corrections, escalations = [], []
    bar = baseline(titles)
    leaky = []
    for term in terms:
        row = profile_of(term, titles, hard)
        # Promoting a job on its title alone is only defensible if that
        # title beats the market. A term whose matches reach the readable
        # band LESS often than a random row is worse than no rule at all —
        # which is precisely RULE 2's "business analyst" leak.
        if row["listings"] > UNMEASURED and row["precision"] is not None \
                and row["precision"] < bar:
            leaky.append(term)
    if leaky:
        corrections.append({
            "field": "domain_title_terms", "action": "dropped",
            "removed": leaky,
            "why": f"the rows each one matches reach score {REACHABLE} less "
                   f"often than the market's own {bar:.0%}, so promoting on "
                   f"this title alone is worse than not promoting"})
    # RULE consistency: a bonus with nothing to combine is a dead setting.
    halves = (profile.get("domain_half_a") or [],
              profile.get("domain_half_b") or [])
    if profile.get("domain_bonus") and not (halves[0] and halves[1]):
        corrections.append({
            "field": "domain_bonus", "action": "computed",
            "from": profile.get("domain_bonus"), "to": 0,
            "why": "a both-halves bonus with an empty half can never fire"})
    return corrections, escalations


def validate(profile, titles, hard=None, soft=None):
    """One decision for one generated profile, with its reasons."""
    if hard is None or soft is None:
        hard, soft = _seniority()
    if not profile:
        return {"decision": "escalate", "result": None, "corrections": [],
                "reasons": ["the local model returned nothing usable"]}
    corrections, reasons = [], []
    for check in (lambda p: check_role_keywords(p, titles, hard, soft),
                  lambda p: check_penalties(p),
                  lambda p: check_title_exclude(p, hard, soft),
                  lambda p: check_title_hints(p, titles, hard, soft),
                  lambda p: check_domain(p, titles, hard)):
        fixes, stops = check(profile)
        corrections.extend(fixes)
        reasons.extend(stops)
    if reasons:
        return {"decision": "escalate", "result": None,
                "corrections": [], "reasons": reasons}

    result = dict(profile)
    for fix in corrections:
        field = fix["field"]
        if fix["action"] == "dropped":
            gone = {str(v).strip().lower() for v in fix["removed"]}
            values = result.get(field) or []
            if values and isinstance(values[0], dict):
                result[field] = [e for e in values
                                 if str(e.get("term", "")).strip().lower()
                                 not in gone]
            else:
                result[field] = [v for v in values
                                 if str(v).strip().lower() not in gone]
        elif fix["action"] == "added":
            result[field] = list(result.get(field) or []) + list(fix["added"])
        elif fix["action"] == "computed":
            result[field] = fix["to"]
    return {"decision": "corrected" if corrections else "accept",
            "result": result, "corrections": corrections, "reasons": []}


def search_quality(profile, titles, hard=None, soft=None):
    """What this profile's paid keywords would buy, in rows and dollars."""
    if hard is None:
        hard, _ = _seniority()
    rows = [profile_of(t, titles, hard)
            for t in profile.get("role_keywords") or ()]
    measured = [r for r in rows if r["listings"] > UNMEASURED]
    listings = sum(r["listings"] for r in measured)
    return {
        "keywords": len(rows),
        "unmeasured": len(rows) - len(measured),
        "listings": listings,
        "reachable": sum(r["reachable"] for r in measured),
        "wasted": sum(r["wasted"] for r in measured),
        "spend": spend(listings),
        "precision": (sum(r["reachable"] for r in measured) / listings
                      if listings else None),
        "wildcards": sum(1 for r in rows if r["share"] > WILDCARD_SHARE),
    }


# ---------------------------------------------------------------- generating

# The production schema with the arrays bounded. Unbounded, a local model
# does not stop: chen's profile ran over thirty minutes and filled the
# context window, where the same call with these caps takes 44 seconds.
# Gemini honours the prose rule ("twenty to forty entries") and qwen3 does
# not, so the bound has to be in the grammar.
CAPS = {"role_keywords": 40, "skill_weights": 44, "penalty_terms": 12,
        "domain_half_a": 12, "domain_half_b": 12, "domain_title_terms": 12,
        "title_hints": 40, "title_exclude": 20}

LAYOUTS_MEASURED = ("plain", "messy")


def capped_schema():
    """RESPONSE_SCHEMA with each array bounded.

    Each property is deep-copied ON ITS OWN rather than copying the schema
    once. skill_weights and penalty_terms are the SAME _WEIGHTED_LIST
    object in RESPONSE_SCHEMA, and deepcopy keeps shared references shared,
    so one pass over CAPS capped skill_weights at 44 and then re-capped it
    at 12 through the alias — starving the field RULE 1 says to aim for 40+
    of, where thin coverage scores real matches at zero.
    """
    import copy
    import make_profile as mp
    schema = dict(mp.RESPONSE_SCHEMA)
    schema["properties"] = {name: copy.deepcopy(spec) for name, spec
                            in mp.RESPONSE_SCHEMA["properties"].items()}
    for field, cap in CAPS.items():
        schema["properties"][field]["maxItems"] = cap
    return schema


def prefs_for(person):
    """Preferences a résumé cannot state, filled in plausibly per person.

    exclude_levels is the standard three because that is what config
    already drops; the locations are the person's own, so the measurement
    is not asking a Japanese ML engineer to search Bengaluru.
    """
    return {"locations": [person["location"].split(",")[0].strip(), "Remote"],
            "avoid": [],
            "exclude_levels": ["intern", "fresher", "junior"]}


def ask(model, resume_text, person, timeout=1500):
    import json as _json
    import urllib.request
    import make_profile as mp
    from bench.run import ctx_for, KEEP_ALIVE, OLLAMA

    prompt = mp.SYSTEM_INSTRUCTION + "\n\n" + mp.build_prompt(
        resume_text, prefs_for(person))
    # reply_tokens is 3000 rather than the extraction default of 768: this
    # answer is thirteen fields and two weighted lists, and sizing it for
    # an extraction reply left ~790 tokens of room, which is what made the
    # first attempt look like a model that could not do the job.
    body = _json.dumps({
        "model": model, "prompt": prompt, "format": capped_schema(),
        "stream": False, "think": False, "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0.2,
                    "num_ctx": ctx_for(prompt, reply_tokens=3000)},
    }).encode()
    request = urllib.request.Request(OLLAMA, body,
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return _json.loads(_json.loads(response.read())["response"])


def run(model, cache_path=None, asker=ask, layouts=LAYOUTS_MEASURED):
    from bench.people import PEOPLE
    from bench.run import RESUMES
    from resume_parser import extract_text

    cache_path = cache_path or os.path.join(
        HERE, "results",
        f"profiles-{model.replace(':', '_').replace('/', '_')}.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    for slug, person in PEOPLE.items():
        for layout in layouts:
            key = f"{slug}-{layout}"
            pdf = os.path.join(RESUMES, f"{slug}-{layout}.pdf")
            if key in cache or not os.path.exists(pdf):
                continue
            try:
                cache[key] = asker(model, extract_text(pdf), person)
            except Exception as exc:            # noqa: BLE001 - logged, not raised
                cache[key] = {"error": f"{type(exc).__name__}: {exc}"}
            got = cache[key]
            print(f"  {key:<16} "
                  f"{len(got.get('role_keywords') or [])} keywords, "
                  f"{len(got.get('skill_weights') or [])} weights, "
                  f"{len(got.get('title_exclude') or [])} excludes",
                  flush=True)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, indent=1)
    return cache


# ---------------------------------------------------------------- reporting

def coverage_gaps(profile):
    """Where a generated profile falls short of the rules' own numbers."""
    gaps = []
    weights = len(profile.get("skill_weights") or ())
    if weights < 40:
        # RULE 1: "Aim for 40+ skill_weights entries — thin coverage
        # silently scores real matches at zero."
        gaps.append(f"skill_weights {weights} < 40")
    hints = len(profile.get("title_hints") or ())
    if hints < 20:
        # RULE 3: twenty to forty entries, and a missing fragment is
        # inventory nobody ever sees.
        gaps.append(f"title_hints {hints} < 20")
    if not profile.get("role_keywords"):
        gaps.append("role_keywords empty — nothing to search")
    return gaps


def report(model, cache=None, titles=None):
    from bench.people import PEOPLE

    cache = cache if cache is not None else run(model)
    titles = titles if titles is not None else load_titles()
    hard, soft = _seniority()
    bar = baseline(titles)

    tally = {"accept": [], "corrected": [], "escalate": []}
    fixes, before, after, gaps = [], [], [], {}
    for key, profile in sorted(cache.items()):
        if profile.get("error"):
            tally["escalate"].append((key, {"reasons": [profile["error"]]}))
            continue
        decision = validate(profile, titles, hard, soft)
        tally[decision["decision"]].append((key, decision))
        fixes.extend(decision["corrections"])
        before.append(search_quality(profile, titles, hard))
        if decision["result"]:
            after.append(search_quality(decision["result"], titles, hard))
        found = coverage_gaps(profile)
        if found:
            gaps[key] = found

    total = sum(len(v) for v in tally.values())
    print(f"\n{'=' * 72}\n{model}: the search-driving fields, {total} profiles"
          f"\n{'=' * 72}")
    print(f"  market baseline: {bar:.1%} of {len(titles)} scraped listings "
          f"reach score {REACHABLE}")
    print(f"\n  {len(tally['accept']):>3} accepted as generated")
    print(f"  {len(tally['corrected']):>3} corrected by a validator")
    print(f"  {len(tally['escalate']):>3} escalated")

    if fixes:
        counts = {}
        for fix in fixes:
            counts[(fix["field"], fix["action"])] = counts.get(
                (fix["field"], fix["action"]), 0) + 1
            for value in fix.get("removed", ()):
                counts.setdefault("_terms", []).append((fix["field"], value))
        terms = counts.pop("_terms", [])
        print("\n  what the validators removed:")
        for (field, action), n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>3}x  {field} {action}")
        seen = []
        for field, value in terms:
            if (field, str(value).lower()) not in seen:
                seen.append((field, str(value).lower()))
        print("\n  the terms themselves (distinct):")
        for field, value in seen[:18]:
            print(f"    {field:<20} {value}")
        if len(seen) > 18:
            print(f"    ... and {len(seen) - 18} more")

    def totals(rows):
        listings = sum(r["listings"] for r in rows)
        return {
            "keywords": sum(r["keywords"] for r in rows),
            "unmeasured": sum(r["unmeasured"] for r in rows),
            "listings": listings,
            "reachable": sum(r["reachable"] for r in rows),
            "wasted": sum(r["wasted"] for r in rows),
            "spend": sum(r["spend"] for r in rows),
            "wildcards": sum(r["wildcards"] for r in rows),
            "precision": (sum(r["reachable"] for r in rows) / listings
                          if listings else None),
        }

    if before:
        b, a = totals(before), totals(after) if after else None
        print(f"\n  what the paid keywords would buy, across all {total} "
              f"profiles:")
        head = f"    {'':<26}{'as generated':>14}{'after checks':>14}"
        print(head)

        def row(label, key, fmt=lambda v: f"{v}"):
            av = fmt(a[key]) if a and a[key] is not None else "-"
            bv = fmt(b[key]) if b[key] is not None else "-"
            print(f"    {label:<26}{bv:>14}{av:>14}")

        row("keywords", "keywords")
        row("of those unmeasurable here", "unmeasured")
        row("wildcard keywords", "wildcards")
        row("listings bought", "listings")
        row("of those reachable", "reachable")
        row("of those hard-dropped", "wasted")
        # The confound-free one: no scoring is involved, only whether
        # config deletes the row on its title.
        wb = b["wasted"] / b["listings"] if b["listings"] else None
        wa = (a["wasted"] / a["listings"]
              if a and a["listings"] else None)
        print(f"    {'hard-dropped share':<26}"
              f"{(f'{wb:.1%}' if wb is not None else '-'):>14}"
              f"{(f'{wa:.1%}' if wa is not None else '-'):>14}")
        row("precision (see caveat)", "precision", lambda v: f"{v:.1%}")
        row("spend at the cheapest rate", "spend", lambda v: f"${v:,.2f}")

    if gaps:
        print(f"\n  coverage against the rules' own numbers "
              f"({len(gaps)} of {total} profiles short):")
        for key, found in sorted(gaps.items())[:8]:
            print(f"    {key:<16} {'; '.join(found)}")
        if len(gaps) > 8:
            print(f"    ... and {len(gaps) - 8} more")

    if tally["escalate"]:
        print("\n  WHY each one escalated:")
        for key, decision in tally["escalate"]:
            print(f"    {key}")
            for reason in decision["reasons"]:
                print(f"        {reason}")

    # Stability across two renderings of identical facts. A generated field
    # has no answer key, so agreement with itself is the only correctness
    # signal available for it.
    pairs = 0
    overlaps = []
    for slug in PEOPLE:
        a_doc, b_doc = cache.get(f"{slug}-plain"), cache.get(f"{slug}-messy")
        if not a_doc or not b_doc or a_doc.get("error") or b_doc.get("error"):
            continue
        pairs += 1
        for field in ("role_keywords", "title_hints", "title_exclude"):
            x = {str(v).lower() for v in a_doc.get(field) or ()}
            y = {str(v).lower() for v in b_doc.get(field) or ()}
            if x or y:
                overlaps.append((field, len(x & y) / len(x | y)))
    if overlaps:
        print(f"\n  agreement between two renderings of the same person "
              f"({pairs} pairs):")
        for field in ("role_keywords", "title_hints", "title_exclude"):
            got = [j for f, j in overlaps if f == field]
            if got:
                print(f"    {field:<20} Jaccard {sum(got) / len(got):.2f}")
    return tally


# ---------------------------------------------------------------- reference

# make_profile.render() writes the generated fields into config under other
# names (lines 709-715), so the shipped profiles can be read back through
# the same validators. The comparison this enables is not per-person —
# these are real people with real résumés this benchmark does not have —
# but the rule-compliance checks are absolute, and the market metrics
# describe a distribution either way.
RENDERED_AS = {
    "domain_half_a": "frontend_terms",
    "domain_half_b": "backend_terms",
    "domain_title_terms": "fullstack_title_terms",
    "domain_bonus": "fullstack_bonus",
}


def from_config(module=None):
    """A shipped profile read back into the generated shape.

    `module` is a profiles.* module whose overlays sit on top of config's
    base; None measures the base alone. Weighted maps come back as dicts
    there and as [{term, weight}] here, so they are converted.
    """
    import config
    scoring = dict(config.SCORING)
    hints, excludes = list(config.ATS_TITLE_HINTS), list(config.ATS_TITLE_EXCLUDE)
    keywords = list(config.SEARCH.get("role_keywords") or [])
    if module is not None:
        scoring.update(getattr(module, "SCORING", {}) or {})
        hints = list(getattr(module, "ATS_TITLE_HINTS", hints) or hints)
        excludes = list(getattr(module, "ATS_TITLE_EXCLUDE", excludes)
                        or excludes)
        keywords = list((getattr(module, "SEARCH", {}) or {}).get(
            "role_keywords") or keywords)

    def weighted(name):
        got = scoring.get(name) or {}
        if isinstance(got, dict):
            return [{"term": t, "weight": w} for t, w in got.items()]
        return list(got)

    return {
        # make_profile.render() writes years into SEARCH, not SCORING, so
        # reading it back needs its own line. Without it the dry run
        # printed "gemini None" against a locally derived 1 and looked
        # like a disagreement where the two actually agree.
        "years_experience": (config.SEARCH.get("experience_years")
                             if module is None else
                             (getattr(module, "SEARCH", {}) or {}).get(
                                 "experience_years",
                                 config.SEARCH.get("experience_years"))),
        "role_keywords": keywords,
        "title_hints": hints,
        "title_exclude": excludes,
        "skill_weights": weighted("skill_weights"),
        "penalty_terms": weighted("penalty_terms"),
        "domain_half_a": list(scoring.get(RENDERED_AS["domain_half_a"]) or []),
        "domain_half_b": list(scoring.get(RENDERED_AS["domain_half_b"]) or []),
        "domain_title_terms": list(
            scoring.get(RENDERED_AS["domain_title_terms"]) or []),
        "domain_bonus": scoring.get(RENDERED_AS["domain_bonus"]) or 0,
    }


REFERENCE = ("bigtech", "optum", "kartik_reachable", "global_all",
             "global_remote", "india_remote")


def reference(titles=None):
    """The same validators over the profiles actually shipped and swept with."""
    import importlib
    titles = titles if titles is not None else load_titles()
    cache = {"config (base)": from_config()}
    for name in REFERENCE:
        try:
            cache[name] = from_config(importlib.import_module(
                "profiles." + name))
        except ImportError:
            continue
    return report("the shipped profiles", cache, titles)


def demo():
    hard, soft = ("intern", "junior", "principal"), ("senior", "sr", "lead")
    market = ([("software engineer", 0)] * 90
              + [("software engineer ii", 40)] * 10
              + [("salesforce consultant", 45)] * 5
              + [("business analyst", 0)] * 20
              + [("senior data engineer", 30)] * 5)
    assert abs(baseline(market) - 20 / 130) < 1e-9, baseline(market)

    row = profile_of("software engineer", market, hard)
    assert row["listings"] == 100 and row["reachable"] == 10
    assert abs(row["precision"] - 0.10) < 1e-9
    assert row["share"] > WILDCARD_SHARE, "it is most of this market"
    # Word-boundary, matching config's own matcher.
    assert droppable("Software Engineer Intern", hard)
    assert not droppable("International Software Engineer", hard), \
        "intern must not fire inside international"
    assert not droppable("Software Engineer II", hard)

    assert spend(0) == 0.0
    assert abs(spend(25) - 0.045) < 1e-9
    assert abs(spend(26) - 0.09) < 1e-9, "billed per block, so 26 rows is two"

    # A wildcard keyword and a seniority keyword both cost money for rows
    # config then removes, so both are dropped and each says which.
    # Two of four, so half — the escalation needs MORE than half, and the
    # first version of this fixture had two of three and escalated, which
    # is the rule working rather than a bug.
    profile = {"role_keywords": ["software engineer", "principal consultant",
                                 "salesforce consultant", "crm consultant"],
               "title_hints": ["consultant"], "title_exclude": [],
               "skill_weights": [{"term": "apex", "weight": 5}],
               "penalty_terms": [], "domain_half_a": [], "domain_half_b": [],
               "domain_title_terms": [], "domain_bonus": 0}
    out = validate(profile, market, hard, soft)
    assert out["decision"] == "corrected", out
    assert out["result"]["role_keywords"] == ["salesforce consultant",
                                              "crm consultant"], \
        out["result"]["role_keywords"]
    whys = " ".join(f["why"] for f in out["corrections"])
    assert "buys the catalogue" in whys and "hard_drop word" in whys

    # A soft_drop word is NOT a reason to drop a paid keyword: those rows
    # only lose four points and still reach a shortlist, so removing the
    # keyword would throw away inventory that config deliberately keeps.
    out = validate(dict(profile, role_keywords=["senior consultant",
                                                "salesforce consultant"]),
                   market, hard, soft)
    assert "senior consultant" in out["result"]["role_keywords"]

    # title_hints only ever widen, so a senior variant costs nothing and
    # the failure is a MISSING stem. The stem is added; nothing is removed.
    out = validate(dict(profile, role_keywords=["salesforce consultant"],
                        title_hints=["senior developer", "sdet"]),
                   market, hard, soft)
    assert out["result"]["title_hints"] == ["senior developer", "sdet",
                                            "developer"], \
        out["result"]["title_hints"]
    # And it is not added twice when it is already there.
    out = validate(dict(profile, role_keywords=["salesforce consultant"],
                        title_hints=["senior developer", "developer"]),
                   market, hard, soft)
    assert out["result"]["title_hints"] == ["senior developer", "developer"]

    # A penalty on the person's own skill penalises the person.
    out = validate(dict(profile, role_keywords=["salesforce consultant"],
                        penalty_terms=[{"term": "apex", "weight": 6},
                                       {"term": "crm", "weight": 4}]),
                   market, hard, soft)
    assert [e["term"] for e in out["result"]["penalty_terms"]] == ["crm"]

    # title_exclude is checked first, so an entry matching the person's own
    # target roles deletes their work silently. That one is dropped; a real
    # different-career entry is kept.
    out = validate(dict(profile, role_keywords=["salesforce consultant"],
                        title_exclude=["salesforce consultant",
                                       "senior", "data engineer"]),
                   market, hard, soft)
    assert out["result"]["title_exclude"] == ["data engineer"], \
        out["result"]["title_exclude"]

    # One direction only. "sdet" is a different career and belongs in this
    # gate; the hint "sde" being a substring of it is not a reason to
    # remove it, and testing both directions did exactly that to the
    # shipped profiles.
    out = validate(dict(profile, role_keywords=["salesforce consultant"],
                        title_hints=["sde"], title_exclude=["sdet"]),
                   market, hard, soft)
    assert out["result"]["title_exclude"] == ["sdet"]

    # RULE 2's own example: a bare job function reaches the readable band
    # LESS often than a random row, so promoting on it is worse than
    # nothing. The platform-qualified version beats the market and stays.
    out = validate(dict(profile, role_keywords=["salesforce consultant"],
                        domain_title_terms=["business analyst",
                                            "salesforce consultant"]),
                   market, hard, soft)
    assert out["result"]["domain_title_terms"] == ["salesforce consultant"], \
        out["result"]["domain_title_terms"]

    # A both-halves bonus with an empty half can never fire.
    out = validate(dict(profile, role_keywords=["salesforce consultant"],
                        domain_bonus=8, domain_half_a=["frontend"]),
                   market, hard, soft)
    assert out["result"]["domain_bonus"] == 0

    # Most of the list being wildcards is not a list to fix item by item.
    out = validate(dict(profile, role_keywords=["software engineer",
                                                "principal consultant"]),
                   market, hard, soft)
    assert out["decision"] == "escalate" and "2 of 2" in out["reasons"][0], out

    assert validate(None, market, hard, soft)["decision"] == "escalate"
    assert validate({}, market, hard, soft)["decision"] == "escalate"

    # Unmeasured is not bad: a profession this corpus has never seen has no
    # rows here, and saying "zero value" would be a claim the data cannot
    # support.
    q = search_quality({"role_keywords": ["nurse practitioner"]}, market, hard)
    assert q["unmeasured"] == 1 and q["listings"] == 0

    assert coverage_gaps({"skill_weights": [], "title_hints": [],
                          "role_keywords": []})
    assert not coverage_gaps({"skill_weights": [{}] * 40,
                              "title_hints": ["x"] * 20,
                              "role_keywords": ["y"]})
    print("search_fields demo ok")


def main():
    if "--demo" in sys.argv:
        return demo()
    if "--reference" in sys.argv:
        return reference()
    models = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not models:
        sys.exit(__doc__)
    for model in models:
        report(model)


if __name__ == "__main__":
    main()
