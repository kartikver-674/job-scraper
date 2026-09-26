"""
Résumé PDF -> profiles/<name>.py, in one Gemini call.

The scripted replacement for RESUME_AUTOCONFIG_PROMPT.md: same judgment, no
human in a chat window. The model returns JSON only and NEVER writes Python —
this file renders the profile from a template, so a malformed answer is a
KeyError here rather than a syntax error in the config the scraper imports.

    python auto-apply/make_profile.py --name kanav \
        --locations "Bengaluru,Remote" --min-comp-usd 20000 \
        --avoid "Salesforce,CRM" --exclude-levels "intern,fresher,junior"

Preferences a résumé cannot state (locations, pay floor, avoid-list, seniority
band) come from those flags, never from the model — a missing flag is an error,
not a guess. Everything else is inferred from the résumé text.

Verify the result at zero cost before spending:

    python scraper.py --profile <name> --dry-run
"""

import argparse
import ast
import json
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apply_config as cfg
import resume_parser

if cfg.REPO_ROOT not in sys.path:
    sys.path.insert(0, cfg.REPO_ROOT)

import local_extract
import skill_concepts
import title_gate

# `tailor` and `google.genai` are imported inside the two functions that
# actually reach Gemini (_generate_one and main), NOT here. At module
# level they made google-genai a hard install dependency of this file —
# and this file is unavoidable for the local engine, because render() and
# the engine seam live in it. So `engine=local` could not run in an
# environment without the Gemini SDK, which is the whole point of it.

# The two rules that make or break the output, both learned from live failures
# documented in RESUME_AUTOCONFIG_PROMPT.md. Keep them together: they are the
# entire reason this is a model call and not a keyword dump.
SYSTEM_INSTRUCTION = (
    "You configure a job scraper from a candidate's résumé. Infer their real "
    "field, seniority and skills from the résumé text alone — do not assume "
    "software or web development, and never output a skill the résumé does not "
    "state.\n\n"
    "RULE 1 — WEIGHT BY DISCRIMINATIVE POWER, NOT CENTRALITY, ON A 1-5 SCALE. "
    "These are different things and confusing them is the main way this config "
    "goes wrong. Ask of every term: 'would this word also appear in a job this "
    "person does NOT want?' If yes, weight it LOW however core it is to their "
    "career. Terms naming their specific platform/domain/niche take 5; "
    "transferable craft vocabulary they share with adjacent fields gets 1, "
    "even when it is genuinely their top skill. A real failure: for a "
    "Salesforce consultant, 'requirement gathering', 'BRD', 'UAT' and "
    "'stakeholder management' were weighted mid-scale because they were her "
    "strongest skills — an 'Oracle Fusion Functional Consultant' then ranked "
    "#1, because every business-analyst posting on earth contains those words "
    "while only the right ones contain 'Salesforce'. Use 1-5 and nothing "
    "higher: every existing profile is on that scale, and score thresholds are "
    "compared across profiles.\n\n"
    "COVER THE SPELLINGS. Terms are matched literally on word boundaries, so "
    "'node.js' does NOT match a posting that says 'Node'. Emit every common "
    "spelling and abbreviation of each skill as its own entry at the same "
    "weight ('node' AND 'node.js'; 'react.js' AND 'react'; 'postgres' AND "
    "'postgresql'). Aim for 40+ skill_weights entries — thin coverage silently "
    "scores real matches at zero.\n\n"
    "RULE 2 — domain_title_terms flags a job as a match from the TITLE ALONE, "
    "bypassing every other piece of evidence, so every entry MUST name the "
    "platform or domain and NEVER a bare job function. 'salesforce business "
    "analyst' is correct; 'business analyst' leaks to 'Business Analyst "
    "(Italian)' and 'Japanese Business Analyst'. Same rule for the two halves: "
    "keep generic category words out of them, or any adjacent-industry job "
    "satisfies a half for free.\n\n"
    "RULE 3 — title_hints is the gate on every FREE source. Company boards and "
    "job feeds return their whole catalogue and scraper.is_dev_title() drops "
    "any posting whose title contains none of these, before it is scored, so a "
    "missing fragment is inventory nobody ever sees. Emit lowercase fragments "
    "as they appear INSIDE real job titles for this person's field, matched as "
    "substrings: 'salesforce', 'crm consultant', 'apex' for a Salesforce "
    "consultant; 'data engineer', 'analytics engineer' for a data engineer. "
    "Include the seniority-free stem ('developer', not 'senior developer'), "
    "every stack they could be hired for, and the adjacent titles their "
    "experience genuinely qualifies them for. Twenty to forty entries. They are "
    "added to a generic software list, so this can only widen the search — "
    "err towards including a title. title_exclude wins over both and is for "
    "DIFFERENT CAREERS that borrow the same words ('data engineer' for a web "
    "developer), never for seniority.\n\n"
    "The two halves (domain_half_a / domain_half_b) encode a 'job mentions "
    "both sides of my field' bonus — frontend+backend for a web developer. If "
    "the candidate's field has no such natural split, return both halves empty "
    "and domain_bonus 0, and say so in notes."
)

# No `additionalProperties` in the Gemini Developer API, so the two term->weight
# maps travel as arrays of {term, weight} and are folded into dicts below.
_WEIGHTED_LIST = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"term": {"type": "string"}, "weight": {"type": "integer"}},
        "required": ["term", "weight"],
    },
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        # The person's own name, used to prefill the profile name on Sweep's
        # review screen. Empty string when the résumé does not state one.
        "candidate_name": {"type": "string"},
        "field_summary": {"type": "string"},
        "years_experience": {"type": "integer"},
        "role_keywords": {"type": "array", "items": {"type": "string"}},
        "skill_weights": _WEIGHTED_LIST,
        # Positive severity 1-12; negated at render time so the model can never
        # get the sign backwards and silently turn a penalty into a bonus.
        "penalty_terms": _WEIGHTED_LIST,
        "domain_half_a": {"type": "array", "items": {"type": "string"}},
        "domain_half_b": {"type": "array", "items": {"type": "string"}},
        "domain_title_terms": {"type": "array", "items": {"type": "string"}},
        # The free-source title gate (config.ATS_TITLE_HINTS). Substring
        # fragments, lowercase — see RULE 3.
        "title_hints": {"type": "array", "items": {"type": "string"}},
        "title_exclude": {"type": "array", "items": {"type": "string"}},
        "domain_bonus": {"type": "integer"},
        "notes": {"type": "string"},
    },
    "required": [
        "candidate_name",
        "field_summary", "years_experience", "role_keywords", "skill_weights",
        "penalty_terms", "domain_half_a", "domain_half_b", "domain_title_terms",
        "title_hints", "title_exclude", "domain_bonus", "notes",
    ],
}


# A profile name is both a filename and an import path (config.py does
# importlib.import_module(f"profiles.{PROFILE}")) and is validated by
# sweep.logic._NAME_RE — [A-Za-z_][A-Za-z0-9_-]*. Everything below has to land
# inside that, or this would prefill the very form that rejects it.
_SLUG_STRIP = re.compile(r"[^a-z0-9_-]+")
_SLUG_RUNS = re.compile(r"_{2,}")


def profile_name_for(data):
    """A default profile name from the résumé's own candidate_name.

    Returns "" when there is nothing usable, and the caller falls back to an
    empty field for the user to fill — which is what the review screen did
    for every résumé before this.
    """
    raw = (data.get("candidate_name") or "").strip().lower()
    # Accents fold to their base letter instead of being stripped: "María
    # Peña" would otherwise slug to mar_a_pe_a. Scripts that do not decompose
    # to ASCII at all (CJK, for one) still come back empty and get typed by
    # hand, which is honest — there is no transliteration here worth trusting
    # with someone's name.
    raw = "".join(c for c in unicodedata.normalize("NFKD", raw)
                  if not unicodedata.combining(c))
    slug = _SLUG_RUNS.sub("_", _SLUG_STRIP.sub("_", raw)).strip("_-")
    # The first character has to be a letter or underscore. A name that
    # transliterates to digits, or to nothing at all, gets typed by hand
    # rather than silently turned into an invalid module name.
    if not slug or not (slug[0].isalpha() or slug[0] == "_"):
        return ""
    # Truncated for a sane filename, and never left ending on a separator.
    return slug[:40].rstrip("_-")


def build_prompt(resume_text, prefs):
    """Assemble the user-content prompt from résumé text and the CLI preferences."""
    return (
        "=== RÉSUMÉ (the only source of truth about this person) ===\n"
        f"{resume_text}\n\n"
        "=== STATED PREFERENCES (already handled; use only as context) ===\n"
        f"Locations: {', '.join(prefs['locations'])}\n"
        f"Wants to avoid: {', '.join(prefs['avoid']) or '(nothing stated)'}\n"
        f"Seniority levels to exclude: {', '.join(prefs['exclude_levels']) or '(none)'}\n\n"
        "Produce the scraper configuration. role_keywords are the job titles "
        "this person should actually be searched for. Include the avoid-list in "
        "penalty_terms alongside any technology obviously off-domain for their "
        "field. title_hints are lowercase fragments that appear INSIDE job "
        "titles in their field, matched as substrings — they are the gate on "
        "every free company board, so a missing one is inventory nobody sees. "
        "candidate_name is the person's own name exactly as the résumé "
        "writes it, or an empty string if it does not state one — never a "
        "guess from an email address or a file name."
    )


class ModelAnswerError(RuntimeError):
    """The call succeeded and the answer was unusable.

    Carries a SHORT reason composed here, from the response's own finish_reason
    enum — never str(exc) from the client library, which can carry the request
    URL. That rule is why the UI could only ever say "the reason is in the
    terminal"; this is what lets it say which failure it was instead.
    """


# gemini-3.6-flash reasons before it answers, and thinking tokens are charged
# against the SAME budget as the answer. With no ceiling set, the default
# applies — and a response that spends it thinking finishes with MAX_TOKENS and
# no text part at all, which reaches json.loads as None. Set explicitly and
# generously: this is a ceiling, not a reservation, so a larger one costs
# nothing on a response that does not need it. The JSON this schema asks for
# runs ~1.5k tokens.
MAX_OUTPUT_TOKENS = 16384


def _unusable(response):
    """Why an answer could not be read, or None if it can be.

    The vocabulary is the library's FinishReason enum, so nothing the model or
    a server wrote is echoed.
    """
    text = response.text
    if text:
        return None
    candidates = response.candidates or []
    reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    name = getattr(reason, "name", None) or str(reason or "no reason given")
    if name == "MAX_TOKENS":
        return ("the model ran out of output budget before it answered — "
                f"raise MAX_OUTPUT_TOKENS (currently {MAX_OUTPUT_TOKENS})")
    if name in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"):
        return f"the model refused to answer ({name.lower()})"
    return f"the model returned no answer ({name})"


class QuotaExhausted(ModelAnswerError):
    """Every model on the ladder is out of daily requests."""


# Free-tier RPD is counted per model and resets on a clock, not a rolling
# window: "Requests per day (RPD) quotas reset at midnight Pacific time"
# (ai.google.dev/gemini-api/docs/rate-limits, checked 2026-09-09). So the wait
# is answerable exactly, and "try again later" is a worse answer than the time.
_QUOTA_TZ = "America/Los_Angeles"


def quota_reset(now=None):
    """(when it resets in local time, how long that is) as strings."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    pacific = ZoneInfo(_QUOTA_TZ)
    now = now or datetime.now(pacific)
    now = now.astimezone(pacific)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    left = midnight - now
    hours, minutes = divmod(int(left.total_seconds()) // 60, 60)
    # Rendered in the reader's OWN timezone: the quota is Pacific, the person
    # waiting for it is not.
    local = midnight.astimezone()
    return (local.strftime("%H:%M %Z").strip(),
            f"{hours}h {minutes:02d}m" if hours else f"{minutes}m")


# A model is out of daily requests, or is not a model any more. Both mean "try
# the next one" rather than "give up" — and neither is retryable on the same
# model, unlike the 503s below.
_SPENT = ("resource_exhausted", "429", "quota")
_GONE = ("not_found", "404", "no longer available")
_BUSY = ("503", "unavailable", "overloaded")


def _is(exc, needles):
    low = str(exc).lower()
    return any(n in low for n in needles)


def reweight_from_evidence(data, resume_text, output_dir=None, log=print):
    """Weights from where each skill APPEARS, with the market kept apart.

    The replacement for reweight_from_corpus when SWEEP_SKILL_EVIDENCE is
    set. What changes is not the arithmetic but the contract: the old path
    multiplied a centrality proxy by market rarity and produced one
    number, so the market decided both questions. Measured on the audited
    résumé, that put Firebase Cloud Messaging on 5 and TypeScript on 2 —
    a library used once in one side project above the language the
    candidate writes every working day.

    Here the two stay separate. skill_evidence reads the document and
    assigns a TIER; the corpus still measures separation exactly as it
    does today; and the tier's band decides how far the market may move
    the result. Rarity can order two skills the résumé ranks alike. It
    cannot carry a skills-list claim above professional work.

    The same rules run for every concept whatever named it — audit defect
    C1, where the identical Apex evidence scored 3 when Qwen reported it
    and 5 when the scanner recovered it. Origin is recorded, never
    consulted.
    """
    if cfg.REPO_ROOT not in sys.path:
        sys.path.insert(0, cfg.REPO_ROOT)
    import corpus_signal
    import skill_evidence

    entries = [e for e in data.get("skill_weights") or ()
               if str(e.get("term", "")).strip()]
    if not entries:
        return data
    weights = {e["term"].strip().lower(): e["weight"] for e in entries}
    concepts = skill_concepts.from_weights(weights)
    freqs, source = corpus_signal.market_signal(output_dir)

    # The market answers about the CONCEPT, not about whichever spellings
    # this candidate's extractor happened to emit.
    #
    # This was max(separation) over the concept's id AND its observed raw
    # strings, and "es6" is rare where "javascript" is not — so the same
    # person with the same résumé and the same corpus measured JavaScript
    # at band 1 with one spelling and band 4 with six. Rarity of an alias
    # is not rarity of the concept.
    #
    # skill_concepts.market_signal_for asks the concept's DECLARED keys in
    # a fixed order and takes the first the corpus can measure. The full
    # observation is kept so an audit can ask which key answered.
    observed = {c.id: skill_concepts.market_signal_for(
        c.id, freqs, corpus_signal) for c in concepts}
    separations = {cid: row["separation"] for cid, row in observed.items()}

    assessed = skill_evidence.assess_all(concepts, resume_text, separations)
    by_id = {row["id"]: row for row in assessed}

    # Back onto the flat term->weight interface every consumer speaks.
    # Each raw spelling carries its concept's weight, so matching keeps
    # every alias while the NUMBER is now the concept's one answer.
    out = []
    for entry in entries:
        row = by_id.get(skill_concepts.resolve(entry["term"]))
        out.append({"term": entry["term"],
                    "weight": row["weight"] if row else entry["weight"]})

    where = ("measured in output/" if source == "live"
             else f"from {corpus_signal.FROZEN_NAME}")
    log(f"  weighted {len(assessed)} concept(s) by résumé evidence, with "
        f"market separation {where} kept separate:")
    order = {name: i for i, name in enumerate(reversed(skill_evidence.ORDER))}
    for row in sorted(assessed, key=lambda r: (order[r["tier"]],
                                               -r["weight"], r["display"]))[:12]:
        rarity = ("unmeasured" if row["market_separation"] is None
                  else f"market {row['market_separation']}/5")
        log(f"    {row['weight']}  {row['display']:<26} "
            f"{row['tier']:<17} {rarity:<11} {row['why']}")
    if len(assessed) > 12:
        log(f"    ... and {len(assessed) - 12} more")
    return dict(data, skill_weights=out,
                skill_importance=[
                    # `occurrences` and `status_counts` are R4a: what each
                    # mention MEANT, kept as offsets and sentence digests so
                    # a tier can be argued with after the run. No résumé
                    # text is copied into the profile.
                    dict({k: row[k] for k in ("id", "display", "tier", "why",
                                              "weight", "market_separation",
                                              "sections", "occurrences",
                                              "status_counts",
                                              "evidence_strength",
                                              "independent_entries")},
                         # Which corpus term answered for this concept, and
                         # on what denominator. Internal provenance, not UI.
                         market=observed.get(row["id"]))
                    for row in assessed])


def reweight_from_corpus(data, output_dir=None, log=print):
    """Re-score the model's skill weights against the jobs already scraped.

    RULE 1 asks the model how much each term NARROWS the market. That is a
    fact about the market, not about the résumé, and output/ holds the
    market: corpus_signal measures each term's document frequency across
    every listing already fetched and blends it with what the model said —
    the model supplies which terms are the candidate's and how central, the
    corpus supplies how much each one separates.

    Here rather than in render(), because render() is also where the USER's
    reviewed weights arrive from Sweep's review screen. Re-scoring those
    would silently discard the one explicit override the UI offers. This
    runs first, so what the user reviews is already corrected and their
    edits still win.

    Degrades on its own: an absent or empty output/ leaves every term
    unmeasured, and blend() treats abstention as "keep what you were told".
    """
    # The same guarded insert _load_config() uses. generate() does not call
    # it, so nothing has put the repo root on the path by the time this runs.
    if cfg.REPO_ROOT not in sys.path:
        sys.path.insert(0, cfg.REPO_ROOT)
    import corpus_signal

    weights = {e["term"].strip().lower(): e["weight"]
               for e in data.get("skill_weights") or () if e["term"].strip()}
    if not weights:
        return data
    freqs, source = corpus_signal.market_signal(output_dir)
    blended, moved = corpus_signal.reweight(weights, freqs)
    if not moved:
        return data

    data = dict(data, skill_weights=[
        {"term": e["term"], "weight": blended.get(e["term"].strip().lower(),
                                                  e["weight"])}
        for e in data["skill_weights"]])
    # Said out loud, not applied in silence: these are the numbers that
    # decide which jobs reach the top of a shortlist.
    # Which market answered is part of what was done to these numbers: a
    # weight from the shipped table is a weight from somebody else's
    # sweeps, and a reader deserves to know that without reading the code.
    where = ("measured in output/" if source == "live"
             else f"from {corpus_signal.FROZEN_NAME}")
    log(f"  re-scored {len(moved)} weight(s) against {len(freqs)} terms"
        f" {where}:")
    for term, before, after, share in moved[:10]:
        pct = f"{share:.1%}" if share is not None else "n/a"
        log(f"    {before} -> {after}  {term:<24} in {pct} of listings")
    if len(moved) > 10:
        log(f"    ... and {len(moved) - 10} more")
    return data


def widen_skills(data, resume_text, output_dir=None, log=print):
    """Add skills the résumé names and the market recognises.

    The model reads a skills section well and under-reads prose: about a
    third of the technologies on a real résumé are named only in
    experience bullets or headings, and thin coverage was measured to
    cause most of the ranking gap — a specialist's own roles scoring
    below the readable band. skill_scan gates every hit structurally, so
    a term appearing once in passing is not treated as a claim, and
    carries its reason into notes.

    Here rather than in render(), and before reweight_from_corpus, for
    the same reason that function is here: render() is where the USER's
    reviewed weights arrive from Sweep's review screen, and both of
    these must be settled before that so the user's edits still win.

    Degrades to a no-op: an absent or empty output/ yields no
    vocabulary, so nothing is added.
    """
    if cfg.REPO_ROOT not in sys.path:
        sys.path.insert(0, cfg.REPO_ROOT)
    import skill_scan

    return skill_scan.widen(data, resume_text, output_dir, log)


# Which engine reads the résumé. Read from the environment so both entry
# points — the CLI and Sweep's injected derive() — pick it up without
# either of them growing a flag, and defaulted to the engine that has
# always run here so turning this on is a deliberate act.
#
#   gemini       one Gemini call (the default; unchanged behaviour)
#   local-first  the local pipeline, with Gemini as the fallback when a
#                deterministic check escalates
#   local        the local pipeline only — no API key needed, and an
#                escalation is an error rather than a fallback
ENGINE_ENV = "SWEEP_PROFILE_ENGINE"
ENGINES = ("gemini", "local-first", "local")


def engine_name(engine=None):
    """The engine to use, validated. An unknown name is an error, not a
    silent fall back to Gemini — that would spend a call the user asked
    not to spend."""
    name = (engine or os.environ.get(ENGINE_ENV) or "gemini").strip().lower()
    if name not in ENGINES:
        raise ValueError(
            f"{ENGINE_ENV}={name!r} is not an engine — expected one of "
            f"{', '.join(ENGINES)}")
    return name


def split_compounds(data, log=print):
    """Compound extracted strings as the atomic skills they name.

    The prompts ask for skills "as written", so the page's own punctuation
    comes back with them: "JWT / OAuth 2.0", "React Hook Form + Zod",
    "Agile/Scrum". Each became ONE literal matcher, which fires only on a
    job that writes the compound exactly the same way — so both halves
    were invisible to scoring.

    Off unless SWEEP_SKILL_CONCEPTS is set. skill_concepts.split_compound
    refuses to touch anything it recognises as a single name (ci/cd,
    node.js, socket.io, c++), and the weight rides along to each half:
    deciding what a skill is WORTH is a later step.
    """
    if not skill_concepts.enabled():
        return data
    weights, added = data.get("skill_weights") or [], []
    # Resolved once for the whole list, and by skill_concepts rather than
    # here: identities() ran before search with the same registry, and the
    # two stages must not be able to disagree about what an atom is.
    known = skill_concepts.market_terms()
    out, at = [], {}
    for entry in weights:
        parts = skill_concepts.split_compound(entry["term"], known)
        for part in parts:
            if part in at:
                # A half can collide with a term already in the list —
                # "jwt / oauth 2.0" (3) splits onto the scanner's own
                # "jwt" (4). Keep the HIGHER, exactly as from_weights
                # does: splitting must not cost a term the weight it
                # already had.
                existing = out[at[part]]
                existing["weight"] = max(existing["weight"], entry["weight"])
                continue
            at[part] = len(out)
            out.append({"term": part, "weight": entry["weight"]})
        if len(parts) > 1:
            added.append((entry["term"], parts))
    if added:
        log(f"  split {len(added)} compound skill(s) into atomic concepts:")
        for raw, parts in added:
            log(f"    {raw:28s} -> {', '.join(parts)}")
    # The raw strings are kept beside the weights, not thrown away: this is
    # the provenance for anyone asking why a term is in the profile.
    return dict(data, skill_weights=out,
                skills_split=[{"raw": raw, "into": parts}
                              for raw, parts in added])


def _finish(data, resume_text, output_dir, log):
    """The steps every engine's answer goes through.

    Split BEFORE widening, so the scanner and the market both see atomic
    terms — a compound reaching the corpus lookup is a term the market has
    never heard of, and abstains on. Widen before re-scoring, so a scanned
    term is weighted against the market exactly like a reported one — and
    all of them before render(), because render() is where the USER's
    reviewed weights arrive from Sweep's review screen and those must win.
    """
    widened = widen_skills(split_compounds(data, log), resume_text,
                           output_dir, log)
    if skill_concepts.evidence_enabled():
        # AFTER widening on purpose. A scanner-recovered term must reach
        # the tiers on exactly the same footing as a model-reported one —
        # that asymmetry is the defect (C1), and running importance
        # before recovery would rebuild it one step earlier.
        return reweight_from_evidence(widened, resume_text, output_dir, log)
    return reweight_from_corpus(widened, output_dir, log)


def generate_local(resume_text, prefs, log=print, output_dir=None, model=None):
    """The local engine, finished exactly like the Gemini one."""
    if cfg.REPO_ROOT not in sys.path:
        sys.path.insert(0, cfg.REPO_ROOT)
    import local_profile

    return _finish(
        local_profile.generate(resume_text, prefs, model=model,
                               output_dir=output_dir, log=log),
        resume_text, output_dir, log)


def generate(client, models, resume_text, prefs, attempts=5, sleep=time.sleep,
             log=print, output_dir=None, engine=None):
    """One profile from one résumé, by whichever engine is selected.

    With the default engine this is one structured Gemini call down a
    ladder of models. `models` is a model id or a sequence of them, tried
    in order. A model whose DAILY quota is spent (429) or that no longer
    exists (404) is skipped — RPD is counted per model, so the next one
    has its own budget. The transient 503s are retried on the SAME model
    first, since they are not about which model was asked.

    Raises QuotaExhausted, naming when the quota comes back, only when
    every model on the ladder is spent.
    """
    engine = engine_name(engine)
    if engine != "gemini":
        import local_profile
        try:
            return generate_local(resume_text, prefs, log, output_dir)
        except (local_profile.Escalated,
                local_extract.ModelUnavailable) as exc:
            if engine == "local":
                raise
            # local-first: a deterministic check found EVIDENCE of a
            # problem, or there is no local model to ask. Both are worth
            # one Gemini call; neither is worth failing the upload.
            log(f"  local engine escalated ({exc}) — asking Gemini")
            if client is None:
                raise

    if isinstance(models, str):
        models = (models,)
    models = tuple(models)
    spent, last = [], None
    for index, model in enumerate(models):
        try:
            return _finish(
                _generate_one(client, model, resume_text, prefs,
                              attempts, sleep, log),
                resume_text, output_dir, log)
        except Exception as exc:
            last = exc
            if _is(exc, _SPENT):
                spent.append(model)
                why = "daily quota spent"
            elif _is(exc, _GONE):
                why = "not a model any more"
            elif _is(exc, _BUSY):
                # Its own retries are already spent by here. The endpoint
                # being busy is not about which model was asked, so another
                # one is worth a try — but it is not an exhausted quota, and
                # the error at the end must not claim it is.
                why = "still overloaded after retrying"
            else:
                # An auth failure, a bad schema, an unusable answer: every
                # model on the ladder fails it identically, so walking them
                # spends calls to reach the same place.
                raise
            if index + 1 < len(models):
                log(f"  {model}: {why} — falling back to {models[index + 1]}")
    if not spent:
        # Nothing was exhausted; whatever actually stopped the last model is
        # the truth, and the CLI's own 503 branch still reads it.
        raise last
    at, left = quota_reset()
    raise QuotaExhausted(
        f"every model is out of requests for today ({', '.join(spent)}). "
        f"The free tier resets at midnight Pacific — {at} your time, about {left} from now")


def _generate_one(client, model, resume_text, prefs, attempts, sleep, log):
    """One structured Gemini call, retried on the transient 503s this API throws."""
    # Imported here rather than at module level: see the note at the top.
    from google.genai import types

    prompt = build_prompt(resume_text, prefs)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.2,
    )
    for attempt in range(attempts):
        try:
            response = client.models.generate_content(
                model=model, contents=prompt, config=config)
            # response.text is None when the response carries no text part —
            # a refusal, or a budget spent on thinking. json.loads(None) then
            # raises a TypeError that reads as "the résumé is unreadable".
            unusable = _unusable(response)
            if unusable:
                raise ModelAnswerError(unusable)
            try:
                return json.loads(response.text)
            except json.JSONDecodeError:
                # Truncation lands here instead: a partial answer is still text.
                raise ModelAnswerError(
                    "the model's answer was cut off before it was valid JSON — "
                    f"raise MAX_OUTPUT_TOKENS (currently {MAX_OUTPUT_TOKENS})"
                ) from None
        except Exception as exc:
            # 503 UNAVAILABLE ("high demand") is common on this endpoint — it
            # hit 2 of 6 calls while this was written, then exhausted a 3-try
            # budget on the first real run, which is why the budget is 5 with a
            # widening back-off. Anything else is a real error: fail loudly
            # rather than burning the budget on a retired model's 404.
            if attempt == attempts - 1 or "503" not in str(exc):
                raise
            log(f"  503 from {model}, retrying in {5 * (attempt + 1)}s "
                f"({attempt + 1}/{attempts - 1})")
            sleep(5 * (attempt + 1))


# The scale every consumer shares. RULE 1 states it to the model ("use 1-5
# and nothing higher: every existing profile is on that scale, and score
# thresholds are compared across profiles"), and until now only the model
# was asked to obey it: -10 became +10 through abs(), and 10**9 was
# accepted as a skill weight.
WEIGHT_RANGE = (1, 5)

# Penalties are a different, documented scale — RESPONSE_SCHEMA calls them
# "positive severity 1-12", negated at render so the model cannot get the
# sign backwards.
PENALTY_RANGE = (1, 12)


def _whole(value, label):
    """An integer, or a refusal. bool is not an integer here.

    True passed int() as 1 and became a year of experience; 1.9 truncated
    to 1 without saying so. A model answer that is the wrong TYPE is a
    malformed answer, not a number to round.
    """
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ValueError(f"{label} must be a whole number, got {value!r}")


def _weights(entries, sign=1):
    """Fold [{term, weight}] into {term: weight}, lowercased, sign applied.

    Bounded here because this is the boundary every engine shares — the
    local path, the Gemini path, the review screen and hand-written
    answers all render through it. Out of range is refused rather than
    clamped: a weight of 99 is not a strong opinion, it is a malformed
    answer, and silently turning it into 5 would hide that.
    """
    low, high = PENALTY_RANGE if sign < 0 else WEIGHT_RANGE
    kind = "penalty" if sign < 0 else "skill"
    out = {}
    for entry in entries:
        term = str(entry["term"]).strip().lower()
        if not term:
            continue
        weight = _whole(entry["weight"], f"{kind} weight for {term!r}")
        # abs() for penalties only, where the schema documents it as the
        # guard against a reversed sign. For skills it turned the most
        # negative answer into the strongest positive signal.
        if sign < 0:
            weight = abs(weight)
        if not low <= weight <= high:
            raise ValueError(
                f"{kind} weight for {term!r} must be between {low} and "
                f"{high}, got {weight}")
        out[term] = sign * weight
    return out


def _load_config():
    """Import config.py with sys.argv neutralised — it reads --profile from
    argv at import time, so importing it under a test runner's own argv (or
    while JOB_PROFILE is set for an unrelated run) would pick the wrong
    profile or exit outright. Shared by validate_keys() and render(), which
    both need the live config as the single source of truth."""
    argv, job_profile = sys.argv, os.environ.pop("JOB_PROFILE", None)
    sys.argv = [argv[0]]
    try:
        # Inserted once, not per call: this runs on every /estimate and every
        # /run, and repeating it grew sys.path by an entry each time — the
        # third instance of a defect fixed twice elsewhere.
        if cfg.REPO_ROOT not in sys.path:
            sys.path.insert(0, cfg.REPO_ROOT)
        import config
        return config
    finally:
        sys.argv = argv
        if job_profile is not None:
            os.environ["JOB_PROFILE"] = job_profile


def validate_keys(rendered_keys):
    """Reject any config key that config.py does not actually define. Returns
    the live config module so callers that already paid for this import (i.e.
    render()) don't have to import it a second time.

    Profile merge is a plain dict.update(), so a misspelled key is accepted in
    silence and does nothing — which is how RESUME_AUTOCONFIG_PROMPT.md came to
    name `SCORING.drop_terms` and `SETTINGS.min_ctc_lpa`, neither of which has
    ever existed. Checked against the live config so it cannot drift again.
    """
    config = _load_config()
    known = {"SEARCH": config.SEARCH, "SETTINGS": config.SETTINGS,
             "SCORING": config.SCORING, "SITES": config.SITES}
    unknown = [f"{section}.{key}" for section, keys in rendered_keys.items()
               for key in keys if key not in known[section]]
    if unknown:
        raise KeyError(f"not real config keys: {', '.join(unknown)}")
    return config


# The same window sweep.logic.with_experience already enforces on the review
# form, for the reason its comment gives: this number becomes
# SETTINGS["max_experience_years"] (years + 3) and SEARCH["experience_years"],
# and both are compared against what a posting DEMANDS. Unbounded above, it
# stops filtering anything; negative, max_experience_years goes negative too
# and every posting is dropped — a search that silently returns nothing.
# The form gate only covers the field the user posted; the model's own figure
# reaches here ungated.
MAX_CAREER_YEARS = 60


def _years(value):
    """years_experience, or a refusal. Never a profile that cannot work."""
    years = _whole(value, "years_experience")
    if not 0 <= years <= MAX_CAREER_YEARS:
        raise ValueError(
            f"years_experience must be between 0 and {MAX_CAREER_YEARS}, "
            f"got {years}")
    return years


def _fmt(value, indent=8):
    """Render a dict or list as readable, line-wrapped Python source."""
    pad = " " * indent
    if isinstance(value, dict):
        body = "".join(f"{pad}{k!r}: {v!r},\n" for k, v in value.items())
    else:
        body = "".join(f"{pad}{v!r},\n" for v in value)
    open_, close = ("{", "}") if isinstance(value, dict) else ("[", "]")
    return f"{open_}\n{body}{' ' * (indent - 4)}{close}" if value else f"{open_}{close}"


def _fmt_sites(overlay):
    """Render a SITES overlay as profile source.

    Each site's WHOLE dict is written out, because config._overlay merges one
    level deep only (SITES.update(override)) — so a partial entry like
    {"enabled": False} replaces the real one and takes "actor" and naukri's
    "results_per_run" with it. Harmless while the site is off, a broken or
    silently re-priced run the moment someone flips it back on by hand.
    """
    out = "SITES = {\n"
    for site in sorted(overlay):
        out += f'    "{site}": {{\n'
        for key in sorted(overlay[site]):
            value = overlay[site][key]
            rendered = (_fmt(value, indent=12) if isinstance(value, list)
                        else repr(value))
            out += f'        "{key}": {rendered},\n'
        out += "    },\n"
    return out + "}\n\n"


def _title_gate(data, config):
    """(hints, excludes) for the free-source title gate.

    UNIONED with config.ATS_TITLE_HINTS, never replacing it: the gate decides
    what a company board is even scored on, and a thin or eccentric model
    answer must not be able to make a profile see LESS than the generic
    software floor. Measured on five live greenhouse boards (2,567 open jobs,
    2026-09-09), the floor alone admits 33% of them; the old 21-entry floor
    admitted 16%.

    Excludes are NOT unioned with anything — they delete, so only what the
    model asked for is honoured.
    """
    excludes = {str(t).strip().lower() for t in data.get("title_exclude") or []}
    if title_gate.enabled():
        # V3 STEP 4. The global floor is NOT unioned in. The gate is built from
        # this candidate's own grounded titles and supported role families, so
        # it answers "could this title belong to this person" rather than "is
        # this a title somebody in software might want" (audit V3-C4). The
        # legacy floor survives as the last fallback, for a candidate about whom
        # nothing at all is known, and that fallback is recorded rather than
        # silent.
        hints, record = title_gate.build(data, config.ATS_TITLE_HINTS)
        kept = sorted(h for h in hints if h and h not in excludes)
        return kept, sorted(excludes), record
    hints = {str(t).strip().lower() for t in data.get("title_hints") or []}
    hints |= set(config.ATS_TITLE_HINTS)
    # A term on both lists would delete itself: exclude wins in is_dev_title.
    return (sorted(h for h in hints if h and h not in excludes),
            sorted(excludes), None)


def _gate_note(record):
    """One comment line above the rendered gate, so a reader can see where it
    came from without running anything. Empty under the legacy union."""
    if not record:
        return ""
    if record.get("global_floor_used"):
        return ("# V3 Step 4: no candidate-specific title evidence existed; "
                "fell back to\n# the legacy global floor.\n")
    families = ", ".join(record.get("supported_families") or ()) or "none"
    line = (f"# V3 Step 4: candidate-specific gate. Supported families: "
            f"{families}.\n")
    if record.get("fallback"):
        line += f"# FALLBACK: {record['fallback']}.\n"
    return line


def _fmt_feeds(queries):
    """A FEEDS overlay carrying this résumé's himalayas search terms.

    The WHOLE himalayas entry is written for the reason _fmt_sites spells out:
    config._overlay merges one level deep (FEEDS.update(override)), so a
    partial {"himalayas": {"queries": [...]}} replaces the real entry and takes
    "enabled" and "pages" with it — leaving the feed silently off.

    Only this one feed is written. The others are absent from the overlay, so
    dict.update leaves config.py's own entries in place.
    """
    if not queries:
        return ""
    return ("FEEDS = {\n"
            '    "himalayas": {\n'
            '        "enabled": True,\n'
            '        "pages": 10,\n'
            f'        "queries": {_fmt(queries, indent=12)},\n'
            "    },\n"
            "}\n\n")


# Exactly two characters carry syntactic power inside a """...""" block: a
# run of quotes can close it, and a backslash escapes whatever follows —
# including the closing quotes. field_summary and notes are free model prose
# interpolated straight into the module docstring, and an f-string escapes
# nothing, so prose containing
#
#     """
#     WHATEVER = 1
#
# closed the docstring and made the next line a top-level statement in a
# file config.py imports. Every other value in a profile goes through
# repr() already; only the prose did not.
_QUOTE_RUN = re.compile(r'"{2,}')


def _prose(text):
    """Model prose, safe to interpolate into a triple-quoted docstring.

    Not a blocklist of patterns — the removal of the only two symbols that
    can act. str.replace('\"\"\"', '\"') would NOT do: replace scans left to
    right without rescanning, so nine quotes become three and the escape is
    back. Collapsing every run of two or more leaves no two adjacent, so no
    three can exist however they arrived.
    """
    return _QUOTE_RUN.sub('"', str(text or "")).replace("\\", " ")


# Every top-level name render() is allowed to define. A GENERATED profile is
# DATA: config.py imports profiles/<name>.py, so anything else in there runs.
PROFILE_NAMES = frozenset({
    "SITES", "FEEDS", "SEARCH", "SETTINGS", "SCORING",
    "ATS_TITLE_HINTS", "ATS_TITLE_EXCLUDE",
    # The free sources' location filter. They have no location parameter to
    # query, so scraper.location_allowed matches this vocabulary against each
    # posting's own location string — which is the only way the Locations
    # picker can narrow a Free Sweep at all.
    "LOCATION_HINTS",
    # Which engine wrote this file. Inert to config._overlay, which only
    # reads OVERLAYABLE names, so it changes nothing about how a profile
    # behaves — it is there so a reader, a rollback and a bug report can
    # all tell v1 output from v2 output without guessing.
    "PROFILE_SCHEMA",
    # Schema 2 only: one entry per résumé in a Multi-Track Sweep. Never in
    # config.OVERLAYABLE — no track is ever overlaid onto the globals.
    "TRACKS",
})

# Bumped when the MEANING of a profile's fields changes, not when the
# engine changes: v1 and v2 both emit the same keys with the same types,
# so both are schema 1. A future step that adds evidence to the file
# itself is what makes this 2.
PROFILE_SCHEMA = 1

# Profiles written before this field existed. Readable, and known to be
# v1 output — absence IS the version, which is why nothing needs
# migrating on disk.
LEGACY_SCHEMA = 0

# Schema 2, a Multi-Track Sweep, written only by render_sweep(): the Sweep's
# own sections once, plus TRACKS — one to three résumés, each carrying its
# own search intent, experience, scoring tables, free-source title gate and
# the engine that derived it. It is the newest schema this build reads.
# render() still writes schema 1 byte for byte, which is why PROFILE_SCHEMA
# stays 1; a build older than this one refuses schema 2 as "newer".
SWEEP_SCHEMA = 2
SWEEP_ENGINE = "multi"      # a schema-2 stamp's engine; each track names its own
MAX_TRACKS = 3
TRACK_ID = re.compile(r"[0-9a-f]{8,32}")   # opaque (secrets.token_hex); never a name
# Exactly what one track carries. Every key is required and nothing else is
# allowed: a track holds one résumé's values, never a Sweep's.
TRACK_SECTIONS = {
    "SEARCH": ("role_keywords", "experience_years"),
    "SETTINGS": ("max_experience_years", "candidate_experience_months"),
    "SCORING": ("skill_weights", "penalty_terms", "frontend_terms", "backend_terms",
                "fullstack_title_terms", "fullstack_bonus"),
}
TRACK_KEYS = ("id", "engine", "SEARCH", "SETTINGS", "SCORING",
              "ATS_TITLE_HINTS", "ATS_TITLE_EXCLUDE")


def profile_schema(module):
    """Which engine wrote a profile, and whether this build can read it.

    Returns {"version", "engine", "readable", "why"}. Never raises on an
    old file: a profile written before PROFILE_SCHEMA existed is v1
    output and perfectly readable, and ABSENCE is how we know that —
    which is why nothing on disk needs migrating.

    A profile from a FUTURE schema is the case that must fail loudly. Its
    fields may mean something this build does not implement, and quietly
    running it would spend real money on a search nobody configured.
    """
    stamp = getattr(module, "PROFILE_SCHEMA", None)
    if stamp is None:
        return {"version": LEGACY_SCHEMA, "engine": "v1", "readable": True,
                "why": "written before profiles carried a schema — v1 output"}
    if isinstance(stamp, int):          # a bare int is a tolerated shorthand
        stamp = {"version": stamp, "engine": "unknown"}
    if not isinstance(stamp, dict) or not isinstance(stamp.get("version"), int):
        return {"version": None, "engine": "unknown", "readable": False,
                "why": (f"PROFILE_SCHEMA is {type(stamp).__name__}, not a "
                        f"version — this file was not written by "
                        f"make_profile.render()")}
    version = stamp["version"]
    if version > SWEEP_SCHEMA:
        return {"version": version, "engine": stamp.get("engine", "unknown"),
                "readable": False,
                "why": (f"profile schema {version} was written by a newer "
                        f"build than this one (schema {SWEEP_SCHEMA}). "
                        f"Upgrade, or regenerate the profile with this "
                        f"build — do not run it as-is.")}
    if (version == SWEEP_SCHEMA) != (stamp.get("engine") == SWEEP_ENGINE):
        return {"version": version, "engine": stamp.get("engine", "unknown"),
                "readable": False,
                "why": (f"a schema {SWEEP_SCHEMA} profile's engine is "
                        f"{SWEEP_ENGINE!r} (each track names its own), and "
                        f"only schema {SWEEP_SCHEMA} may say {SWEEP_ENGINE!r} "
                        f"— this stamp says version {version}, engine "
                        f"{stamp.get('engine')!r}")}
    return {"version": version, "engine": stamp.get("engine", "unknown"),
            "readable": True, "why": f"schema {version}"}


def check_tracks(tracks):
    """A schema-2 TRACKS value, or a ValueError naming what is wrong with it.

    The one gate for track data, on the way out (render_sweep) and on the way
    in (config.load_profile_module). Shapes and types only; the values were
    range-checked when render_sweep built them from a derivation.
    """
    def fail(why):
        raise ValueError(f"TRACKS: {why}")

    def strings(value, label):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            fail(f"{label} must be a list of strings")

    def whole(value, label, allow_none=False):
        if value is None and allow_none:
            return
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            fail(f"{label} must be a whole number >= 0, got {value!r}")

    if not isinstance(tracks, (list, tuple)) or not 1 <= len(tracks) <= MAX_TRACKS:
        fail(f"a Sweep has 1 to {MAX_TRACKS} tracks")
    seen = set()
    for n, track in enumerate(tracks, 1):
        where = f"track {n}"
        if not isinstance(track, dict) or set(track) != set(TRACK_KEYS):
            got = sorted(track) if isinstance(track, dict) else type(track).__name__
            fail(f"{where} must have exactly {list(TRACK_KEYS)}, got {got}")
        tid = track["id"]
        if not isinstance(tid, str) or not TRACK_ID.fullmatch(tid):
            fail(f"{where} id must be 8-32 lowercase hex characters, got {tid!r}")
        if tid in seen:
            fail(f"{where} repeats id {tid!r}")
        seen.add(tid)
        if track["engine"] not in skill_concepts.VERSIONS:
            fail(f"{where} engine must be one of {skill_concepts.VERSIONS}, "
                 f"got {track['engine']!r}")
        for section, keys in TRACK_SECTIONS.items():
            if not isinstance(track[section], dict) or set(track[section]) != set(keys):
                fail(f"{where} {section} must have exactly {list(keys)}")
        search, settings, scoring = track["SEARCH"], track["SETTINGS"], track["SCORING"]
        strings(search["role_keywords"], f"{where} role_keywords")
        whole(search["experience_years"], f"{where} experience_years")
        whole(settings["max_experience_years"], f"{where} max_experience_years")
        whole(settings["candidate_experience_months"],
              f"{where} candidate_experience_months", allow_none=True)
        for key in ("skill_weights", "penalty_terms"):
            table = scoring[key]
            if not isinstance(table, dict) or not all(
                    isinstance(t, str) and isinstance(w, int) and not isinstance(w, bool)
                    for t, w in table.items()):
                fail(f"{where} {key} must map strings to whole numbers")
        for key in ("frontend_terms", "backend_terms", "fullstack_title_terms"):
            strings(scoring[key], f"{where} {key}")
        if isinstance(scoring["fullstack_bonus"], bool) or not isinstance(
                scoring["fullstack_bonus"], int):
            fail(f"{where} fullstack_bonus must be a whole number")
        strings(track["ATS_TITLE_HINTS"], f"{where} ATS_TITLE_HINTS")
        strings(track["ATS_TITLE_EXCLUDE"], f"{where} ATS_TITLE_EXCLUDE")
    return tracks


def check_sweep(module):
    """A loaded schema-2 module's TRACKS, checked, with the Sweep-level
    sections checked to carry nothing a track owns. ValueError otherwise."""
    for section, keys in TRACK_SECTIONS.items():
        stray = sorted(set(getattr(module, section, None) or {}) & set(keys))
        if stray:
            raise ValueError(f"schema {SWEEP_SCHEMA}: {section} carries track "
                             f"keys {stray}; they belong in TRACKS")
    for name in ("ATS_TITLE_HINTS", "ATS_TITLE_EXCLUDE"):
        if hasattr(module, name):
            raise ValueError(f"schema {SWEEP_SCHEMA}: {name} is per track; "
                             f"it belongs in TRACKS")
    return check_tracks(getattr(module, "TRACKS", None))


def check_loaded(module, stamp):
    """What a loader checks after the stamp reads: a schema-2 file's Sweep and
    tracks, and that no other schema carries TRACKS — a TRACKS nobody reads
    would be a résumé silently left out of the search."""
    if stamp["version"] == SWEEP_SCHEMA:
        check_sweep(module)
    elif hasattr(module, "TRACKS"):
        raise ValueError(f"TRACKS is only valid in a schema {SWEEP_SCHEMA} "
                         f"profile; this one is schema {stamp['version']}")


def load_profile(name):
    """Import profiles/<name>.py, or fail with a compatibility message.

    The one place that turns "this file is from the future" into a
    sentence a person can act on, rather than an AttributeError four
    frames into a paid run.
    """
    import importlib
    if cfg.REPO_ROOT not in sys.path:
        sys.path.insert(0, cfg.REPO_ROOT)
    module = importlib.import_module(f"profiles.{name}")
    stamp = profile_schema(module)
    if not stamp["readable"]:
        raise ValueError(f"profiles/{name}.py: {stamp['why']}")
    try:
        check_loaded(module, stamp)
    except ValueError as exc:
        raise ValueError(f"profiles/{name}.py: {exc}") from exc
    return module, stamp


def check_module(source):
    """Source RENDERED BY THIS FILE, parsed and checked before it is written.

    Scope, and it matters: this judges what render() just built out of model
    output, never a profile a person wrote. The nine hand-written ones in
    profiles/ import helpers and define their own locals (ATS_BOARDS,
    _COUNTRIES, OPTUM) — deliberate, reviewed, in git, and none of this
    applies to them. Only the path from a résumé to an imported file needs
    a gate, because only that path has a stranger's prose in it.

    Belt and braces to _prose's razor: _prose makes the docstring
    inescapable, and this refuses the file if anything got through anyway.
    Returns the source, so it can wrap a return and no caller can forget it.

    ast.parse and ast.literal_eval both READ. Nothing here executes the
    module, which is the point — the check has to happen while the payload
    is still text.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(
            f"generated profile is not valid Python: {exc}") from exc

    allowed = ", ".join(sorted(PROFILE_NAMES))
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            continue  # the module docstring, and only in that position
        if not isinstance(node, ast.Assign):
            raise ValueError(
                f"generated profile line {node.lineno}: a top-level "
                f"{type(node).__name__} is not allowed — a profile is the "
                f"docstring and {allowed}")
        for target in node.targets:
            if not isinstance(target, ast.Name):
                raise ValueError(
                    f"generated profile line {node.lineno}: only plain "
                    f"top-level names may be assigned")
            if target.id not in PROFILE_NAMES:
                raise ValueError(
                    f"generated profile line {node.lineno}: assigns "
                    f"{target.id!r}, which is not one of {allowed}")
        # A profile holds data, never an expression to evaluate later.
        # literal_eval raises on anything with a call, a name or an
        # operator in it.
        try:
            ast.literal_eval(node.value)
        except (ValueError, SyntaxError, TypeError, MemoryError,
                RecursionError) as exc:
            raise ValueError(
                f"generated profile line {node.lineno}: "
                f"{getattr(node.targets[0], 'id', '?')} is not a literal "
                f"({exc})") from exc
    return source


def _months(data):
    """experience_months, or None."""
    # experience_months is what local_extract computed and what the review
    # screen collects; `years` is that same figure floored. Absent, this stays
    # None and experience_guard does not run at all — years * 12 would put a
    # 2y11m candidate at 2.0, and the guard's drop threshold is a 2.0-year gap,
    # so the fallback would hard-drop jobs on eleven months of rounding.
    months = data.get("experience_months")
    if not isinstance(months, int) or isinstance(months, bool) or months < 0:
        months = None
    return months


def _sweep_blocks(prefs, config):
    """The Sweep-level source fragments render() interpolates:
    (extra SEARCH lines, extra SETTINGS lines, LOCATION_HINTS, SITES). They
    come from the preferences alone — never from a résumé — so a Multi-Track
    Sweep writes them once, exactly as a one-résumé profile does."""
    extra_search = (f'    "max_results": {int(prefs["max_results"])!r},\n'
                     if prefs.get("max_results") is not None else "")
    extra_settings = ""
    # The only real spend guard in the system: scraper.py:1748 re-reads the
    # account after every search and stops launching once this is crossed.
    # config.py defaults it to None and that check is `if budget is not None`,
    # so a profile that omits this key has NO cap — which is why Sweep always
    # passes one. Omitted here still means "inherit from config.py", the same
    # rule as every other optional key: never emit None, which would write the
    # no-cap value in and read as a deliberate choice.
    if prefs.get("max_spend_usd") is not None:
        extra_settings += (
            f'    "max_spend_usd": {float(prefs["max_spend_usd"])!r},\n')
    # V2-D1: the user's own "run with my available credit anyway", from
    # Confirm. Written only when they gave an answer, so an unset one inherits
    # config's False: a full sweep that can never silently become partial.
    if prefs.get("allow_partial_paid_sweep") is not None:
        extra_settings += (f'    "allow_partial_paid_sweep": '
                           f'{bool(prefs["allow_partial_paid_sweep"])!r},\n')
    if prefs.get("max_age_days") is not None:
        extra_settings += f'    "max_age_days": {int(prefs["max_age_days"])!r},\n'
    if prefs.get("remote_scopes") is not None:
        extra_settings += f'    "remote_scopes": {_fmt(prefs["remote_scopes"])},\n'
    # Which of the three "which jobs should Sweep include" answers this is.
    # "remote" is already fully expressed by remote_scopes above and adds no
    # filter of its own; "india" and "global" are the onsite/hybrid halves,
    # and without them those two answers were the same sweep (see
    # scraper.finalize and config.SETTINGS["work_scope"]).
    if prefs.get("work_scope") is not None:
        extra_settings += f'    "work_scope": {str(prefs["work_scope"])!r},\n'

    # The free sources' location filter. Unset means "inherit config's empty
    # list", i.e. every location — the same "not set means inherit" rule as
    # every other optional key here, and the reason an unpicked location box
    # does not silently narrow a sweep. Written as its OWN top-level name
    # because config._overlay replaces list settings wholesale.
    #
    # Not derived from prefs["locations"]: that list is the paid SEARCH PLAN
    # (for "global" it is nine countries the person never named), and reusing
    # it here would turn a retrieval plan into a filter.
    hints = [str(h).strip().lower()
             for h in (prefs.get("location_hints") or []) if str(h).strip()]
    extra_hints = (f"LOCATION_HINTS = {_fmt(sorted(set(hints)), indent=4)}\n\n"
                   if hints else "")

    # SITES[site].get("locations", SEARCH["locations"]) means LinkedIn — the
    # most expensive site — keeps searching whatever config.py's SITES.linkedin
    # already says (India + Remote) no matter what SEARCH.locations above is
    # set to, unless this profile overrides it too (see scraper.plan_for_site).
    # A wrong or invented LinkedIn location doesn't error, it silently returns
    # US results and bills in full (see config.LINKEDIN_GEO_IDS), so every
    # location here is checked against that VERIFIED table — never passed
    # through on trust, and never trusted just because it came from this
    # app's own code instead of a form.
    overlay = {}
    if prefs.get("linkedin_locations") is not None:
        locations = list(prefs["linkedin_locations"])
        # A bare "Remote" isn't itself a LINKEDIN_GEO_IDS entry — it is a
        # special token _build_linkedin_url resolves via remote_geo (f_WT=2
        # filters workplace type WITHIN that one geography). So it is
        # verified differently: remote_geo itself has to be a real,
        # verified geoId, not "Remote" against the geography table.
        non_remote = [loc for loc in locations if loc.lower() != "remote"]
        unverified = [loc for loc in non_remote if loc not in config.LINKEDIN_GEO_IDS]
        if unverified:
            raise KeyError(
                f"not a verified LinkedIn geography: {', '.join(unverified)} "
                f"— add to config.LINKEDIN_GEO_IDS and confirm with "
                f"`python verify_geoids.py` before using it here.")
        # The whole SITES["linkedin"] dict is REPLACED, not deep-merged (see
        # config._overlay), so "enabled"/"actor"/"remote_geo" have to be
        # carried forward explicitly or the profile would silently switch
        # LinkedIn off (or leave a bare "Remote" location with no region).
        linkedin_site = dict(config.SITES["linkedin"])
        if len(non_remote) < len(locations):
            remote_geo = linkedin_site.get("remote_geo")
            if not remote_geo or remote_geo not in config.LINKEDIN_GEO_IDS:
                raise KeyError(
                    f"config.SITES['linkedin']['remote_geo'] is "
                    f"{remote_geo!r}, not a verified LinkedIn geography — "
                    f"a bare 'Remote' location needs one to mean anything "
                    f"(see scraper._build_linkedin_url).")
        linkedin_site["locations"] = locations
        linkedin_site["remote_only"] = bool(prefs.get("linkedin_remote_only"))
        overlay["linkedin"] = linkedin_site

    # Per-site on/off from Configure's Sources panel. Applied after the
    # LinkedIn block so switching LinkedIn off still keeps the locations it
    # would search if switched back on, rather than the two settings
    # clobbering each other depending on which ran last.
    for site, on in (prefs.get("sites_enabled") or {}).items():
        overlay.setdefault(site, dict(config.SITES[site]))["enabled"] = bool(on)

    extra_sites = _fmt_sites(overlay) if overlay else ""
    return extra_search, extra_settings, extra_hints, extra_sites


def _penalties(data, prefs):
    """penalty_terms for one résumé: the model's, less the excluded levels,
    plus the person's own avoid-list."""
    # The model reliably copies the excluded seniority words into penalty_terms
    # as well, even when told they are already handled. With drop_excluded True
    # (the default) hard_drop_terms deletes those rows outright, so the penalty
    # is dead weight — and if that flag is ever flipped off they would be
    # penalized twice, once by drop_penalty and once here. Strip them.
    excluded = set(prefs["exclude_levels"])
    penalties = {term: weight
                 for term, weight in _weights(data["penalty_terms"], sign=-1).items()
                 if term not in excluded}
    # The person's OWN avoid-list, kept separate from the model's all the way
    # to here so it stays removable: it lives in prefs, not in `data`, so
    # clearing the box on the Search-preferences screen clears it from the
    # next render. Folding it into data["penalty_terms"] instead made it
    # permanent — a term could be added and never taken back.
    #
    # Applied last and at the top of PENALTY_RANGE, so an explicit "I do not
    # want this" outranks whatever the model happened to think of the same
    # technology.
    for term in prefs.get("avoid") or []:
        term = str(term).strip().lower()
        if term and term not in excluded:
            penalties[term] = -PENALTY_RANGE[1]
    return penalties


def render(name, data, prefs):
    """Render profiles/<name>.py source from the model's JSON and the preferences.

    max_spend_usd / max_results / max_age_days / remote_scopes /
    linkedin_locations are
    optional overrides (from Sweep's Configure screen, sweep/app.py) —
    omitted from `prefs` (None), they are left out of the rendered section
    entirely so config.py's own default silently applies, per the
    one-level-deep profile merge in config.py's PROFILES section. A profile
    must never widen the sweep by accident, so "not set" has to mean
    "inherit", not "reset to some default picked here".
    """
    sections = {
        "SEARCH": ["role_keywords", "experience_years", "locations", "salary_min",
                   "max_results"],
        "SETTINGS": ["max_experience_years", "candidate_experience_months",
                     "min_comp_usd", "max_age_days",
                     "remote_scopes", "max_spend_usd", "work_scope",
                     "allow_partial_paid_sweep"],
        "SCORING": ["skill_weights", "penalty_terms", "frontend_terms",
                    "backend_terms", "fullstack_title_terms", "fullstack_bonus",
                    "hard_drop_terms"],
        "SITES": ["linkedin", "indeed", "naukri"],
    }
    config = validate_keys(sections)

    engine = skill_concepts.engine_version()
    years = _years(data["years_experience"])
    months = _months(data)
    skills = _weights(data["skill_weights"])
    # The scanned terms and why each was kept, as a comment beside the
    # weights they became. notes carries the count; this carries the
    # reasons, where someone reviewing the profile can check them
    # without them crowding the docstring.
    added_block = ""
    if data.get("skills_added"):
        lines = "\n".join(f"    #   {term:<28} {why}"
                           for term, why in sorted(data["skills_added"].items()))
        added_block = (
            "    # Found in the résumé and named by the market, added to what\n"
            "    # the model reported. Each line says why it counted as a claim.\n"
            f"{lines}\n")

    extra_search, extra_settings, extra_hints, extra_sites = _sweep_blocks(prefs, config)
    hints, excludes, gate_record = _title_gate(data, config)
    gate_note = _gate_note(gate_record)
    # The résumé's own role keywords are what himalayas is searched for; its
    # search endpoint takes one free-text query per request.
    extra_feeds = _fmt_feeds([k for k in data["role_keywords"] if str(k).strip()])

    spend_note = (
        f'Spending stops at ${float(prefs["max_spend_usd"]):.2f}: '
        f'max_spend_usd below is re-checked against the account after every '
        f'search, and the sweep stops there even with searches left.'
        if prefs.get("max_spend_usd") is not None else
        "COSTS MONEY BY DEFAULT. This file sets no max_spend_usd."
    )

    sites_note = (
        "This file sets no SITES, so it inherits config.py's — LinkedIn and "
        "Indeed on, Naukri off (it has never returned a row and costs ~$0.50 "
        "per run minimum). Run --dry-run first and read the run count. To "
        "narrow it, copy the SITES block from profiles/kartik_reachable.py. "
        "LinkedIn searches the geoIds in SITES, NOT the locations above, so "
        "city-level LinkedIn needs that block too."
        if not extra_sites else
        "This file DOES set SITES (below) — Configure chose it. LinkedIn "
        "searches only the geoIds listed there, not SEARCH.locations above; "
        "any site absent from that block follows config.py's default. Run "
        "--dry-run first and read the cost."
    )

    penalties = _penalties(data, prefs)
    # check_module wraps the return so no caller can forget it: the CLI and
    # Sweep's POST /review both come through here, and the file this builds
    # is imported by config.py.
    return check_module(f'''"""{name} — generated from a résumé by auto-apply/make_profile.py.

{_prose(data["field_summary"])}

    python scraper.py --profile {name} --dry-run   # cost check, free
    python scraper.py --profile {name} --yes

HOW THE MODEL READ THIS RÉSUMÉ
{_prose(data["notes"])}

Skill weights are by DISCRIMINATIVE POWER, not centrality: a term that would
also appear in an unwanted job is weighted low however core it is to this
person. Locations, pay floor, avoid-list and excluded seniority came from the
command line, not from the résumé. Anything absent here inherits from config.py.

{spend_note} {sites_note}

Re-scoring is free — after editing weights run `python rescore_from_apify.py`
rather than paying to scrape again.
"""

# Which engine wrote this file, for a rollback and a bug report. Inert:
# config._overlay only reads the names it knows.
PROFILE_SCHEMA = {{"version": {PROFILE_SCHEMA}, "engine": {engine!r}}}

{extra_hints}{extra_sites}{extra_feeds}SEARCH = {{
    "role_keywords": {_fmt(data["role_keywords"])},
    "experience_years": {years},
    "locations": {_fmt(prefs["locations"])},
    "salary_min": None,
{extra_search}}}

SETTINGS = {{
    # Title bands are a label; this reads the years a posting actually demands.
    "max_experience_years": {years + 3},
    # The same figure at month granularity, which is what experience_guard
    # needs — years alone are floored, and a gap threshold built on a floored
    # year drops jobs on eleven months of rounding.
    "candidate_experience_months": {months},
    "min_comp_usd": {prefs["min_comp_usd"]!r},
{extra_settings}}}

SCORING = {{
{added_block}    "skill_weights": {_fmt(skills)},

    # The avoid-list plus technologies off-domain for this field.
    "penalty_terms": {_fmt(penalties)},

    # Two halves of this field; a job naming both earns fullstack_bonus.
    # Empty halves with a 0 bonus mean the field has no such natural split.
    "frontend_terms": {_fmt([t.lower() for t in data["domain_half_a"]])},
    "backend_terms": {_fmt([t.lower() for t in data["domain_half_b"]])},
    # Each term MUST name the platform/domain — these match on TITLE ALONE.
    "fullstack_title_terms": {_fmt([t.lower() for t in data["domain_title_terms"]])},
    "fullstack_bonus": {int(data["domain_bonus"])},

    # From --exclude-levels. soft_drop_terms is left to config.py on purpose:
    # "senior" routinely means 3-4 years, so it down-ranks instead of dropping.
    "hard_drop_terms": {_fmt(prefs["exclude_levels"])},
}}

# The gate on every FREE source: a company board or feed returns its whole
# catalogue and scraper.is_dev_title() drops any title matching none of these
# BEFORE scoring, so a fragment missing here is inventory nobody sees. This is
# the résumé's own vocabulary UNIONED with config.py's generic software floor,
# so it can only ever widen the search.
{gate_note}ATS_TITLE_HINTS = {_fmt(hints, indent=4)}

# Checked first, so it wins: different CAREERS that borrow the same words.
ATS_TITLE_EXCLUDE = {_fmt(excludes, indent=4)}
''')


def _fmt_tracks(tracks):
    """TRACKS as readable, deterministic Python source."""
    out = "[\n"
    for track in tracks:
        out += (f'    {{\n        "id": {track["id"]!r},\n'
                f'        "engine": {track["engine"]!r},\n')
        for section in TRACK_SECTIONS:
            out += f'        "{section}": {{\n'
            for key, value in track[section].items():
                shown = (_fmt(value, indent=16) if isinstance(value, (dict, list))
                         else repr(value))
                out += f'            "{key}": {shown},\n'
            out += "        },\n"
        for key in ("ATS_TITLE_HINTS", "ATS_TITLE_EXCLUDE"):
            out += f'        "{key}": {_fmt(track[key], indent=12)},\n'
        out += "    },\n"
    return out + "]"


def render_sweep(name, tracks, prefs):
    """Render a schema-2 Multi-Track Sweep: the Sweep's own sections once, then
    TRACKS, one entry per résumé.

    `tracks` is [{"id", "engine", "derived"}] in the user's order. Each track
    is built from its OWN derivation with the helpers render() uses, so its
    values are what render() writes for that résumé under these preferences —
    never taken from another track, and its engine is the one it names, never
    the process's. Ids are opaque; nothing here names a person or a file.
    """
    if not isinstance(tracks, (list, tuple)) or not 1 <= len(tracks) <= MAX_TRACKS:
        raise ValueError(f"a Sweep has 1 to {MAX_TRACKS} tracks")
    config = validate_keys({
        "SEARCH": ["locations", "salary_min", "max_results", *TRACK_SECTIONS["SEARCH"]],
        "SETTINGS": ["min_comp_usd", "max_age_days", "remote_scopes", "max_spend_usd",
                     "work_scope", "allow_partial_paid_sweep",
                     *TRACK_SECTIONS["SETTINGS"]],
        "SCORING": ["hard_drop_terms", *TRACK_SECTIONS["SCORING"]],
        "SITES": ["linkedin", "indeed", "naukri"]})
    extra_search, extra_settings, extra_hints, extra_sites = _sweep_blocks(prefs, config)
    built = []
    for track in tracks:
        if not isinstance(track, dict) or set(track) != {"id", "engine", "derived"}:
            raise ValueError("each track is {'id', 'engine', 'derived'}")
        data = track["derived"]
        years = _years(data["years_experience"])
        hints, excludes, _record = _title_gate(data, config)
        built.append({
            "id": track["id"], "engine": track["engine"],
            "SEARCH": {"role_keywords": list(data["role_keywords"]),
                       "experience_years": years},
            "SETTINGS": {"max_experience_years": years + 3,
                         "candidate_experience_months": _months(data)},
            "SCORING": {
                "skill_weights": _weights(data["skill_weights"]),
                "penalty_terms": _penalties(data, prefs),
                "frontend_terms": [t.lower() for t in data["domain_half_a"]],
                "backend_terms": [t.lower() for t in data["domain_half_b"]],
                "fullstack_title_terms": [t.lower() for t in data["domain_title_terms"]],
                "fullstack_bonus": int(data["domain_bonus"])},
            "ATS_TITLE_HINTS": hints, "ATS_TITLE_EXCLUDE": excludes})
    check_tracks(built)
    return check_module(f'''"""{_prose(name)} — a Multi-Track Sweep generated by auto-apply/make_profile.py.

{len(built)} résumé tracks, searched as one Sweep. The Sweep's own choices —
locations, recency, depth, pay floor, sources, spend cap — are written once
below; each TRACKS entry is one résumé's search intent, experience, scoring
tables and free-source title gate, with the engine that derived it.
"""

PROFILE_SCHEMA = {{"version": {SWEEP_SCHEMA}, "engine": {SWEEP_ENGINE!r}}}

{extra_hints}{extra_sites}SEARCH = {{
    "locations": {_fmt(prefs["locations"])},
    "salary_min": None,
{extra_search}}}

SETTINGS = {{
    "min_comp_usd": {prefs["min_comp_usd"]!r},
{extra_settings}}}

SCORING = {{
    "hard_drop_terms": {_fmt(prefs["exclude_levels"])},
}}

TRACKS = {_fmt_tracks(built)}
''')


def _csv(value):
    return [part.strip() for part in value.split(",") if part.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--name", required=True, help="profile name -> profiles/<name>.py")
    parser.add_argument("--resume", default=cfg.RESUME_PDF, help="résumé PDF")
    parser.add_argument("--locations", required=True, help="comma-separated, e.g. 'Bengaluru,Remote'")
    parser.add_argument("--exclude-levels", required=True,
                        help="comma-separated seniority words to drop, e.g. 'intern,fresher,junior'")
    parser.add_argument("--min-comp-usd", type=int, default=None,
                        help="annual pay floor in USD; omit to keep undisclosed postings")
    parser.add_argument("--avoid", default="", help="comma-separated roles/stacks to down-rank")
    parser.add_argument("--force", action="store_true", help="overwrite an existing profile")
    parser.add_argument("--engine", choices=ENGINES, default=None,
                        help=f"who reads the résumé (default: ${ENGINE_ENV} "
                             f"or gemini). 'local' needs no API key.")
    args = parser.parse_args(argv)

    out = os.path.join(cfg.REPO_ROOT, "profiles", f"{args.name}.py")
    if os.path.exists(out) and not args.force:
        sys.exit(f"{out} exists. Re-run with --force to overwrite (git has the old one).")

    try:
        engine = engine_name(args.engine)
    except ValueError as exc:
        sys.exit(str(exc))
    api_key = os.environ.get("GEMINI_API_KEY")
    # Only the engines that can actually call Gemini need the key. This is
    # the whole point of the local engine, so demanding it anyway would
    # defeat it.
    if not api_key and engine != "local":
        sys.exit("GEMINI_API_KEY missing from .env."
                 + ("" if engine == "gemini" else
                    " Use --engine local to run without one."))

    prefs = {
        "locations": _csv(args.locations),
        "exclude_levels": [w.lower() for w in _csv(args.exclude_levels)],
        "avoid": _csv(args.avoid),
        "min_comp_usd": args.min_comp_usd,
    }

    # The cache is ONE shared file and load_resume only compares its mtime
    # against whichever PDF it was handed, so a cache newer than the named
    # file is returned for it — "594 chars from ada-plain.pdf" silently
    # reading 3,634 chars of somebody else. That is the same defect that
    # once had one person measured twice under two names. The cache is
    # only ever right for the résumé it was made from.
    resume_text = (resume_parser.load_resume(args.resume, cfg.RESUME_TXT)
                   if os.path.abspath(args.resume) == os.path.abspath(cfg.RESUME_PDF)
                   else resume_parser.extract_text(args.resume))
    print(f"Résumé: {len(resume_text)} chars from {args.resume}")

    print(f"Engine: {engine}")
    client = None
    # A key AND an engine that can use one. A leftover GEMINI_API_KEY in
    # .env is common, and building a client for it would import the SDK
    # and crash a local-only install that never needed either.
    if api_key and engine != "local":
        import tailor
        client = tailor.get_client(api_key)
    try:
        data = generate(client, cfg.MODELS, resume_text, prefs, engine=engine)
    except QuotaExhausted as exc:
        # Nothing was written, and running it again today reaches the same
        # place — so the exit says when it will not.
        sys.exit(f"Nothing was written: {exc}.")
    except Exception as exc:
        if "503" in str(exc):
            sys.exit(f"{cfg.MODELS[0]} is overloaded (503) and did not recover. "
                     "Nothing was written — just run this again.")
        raise
    source = render(args.name, data, prefs)

    with open(out, "w", encoding="utf-8") as f:
        f.write(source)

    print(f"\n{data['field_summary']}\n")
    print(f"Wrote {out}")
    print(f"  role_keywords : {len(data['role_keywords'])}")
    print(f"  skill_weights : {len(data['skill_weights'])}")
    print(f"  penalty_terms : {len(data['penalty_terms'])}")
    # Said out loud, not applied in silence: these are the numbers that
    # decide which jobs reach the top of a shortlist.

    print(f"\nRead it, then verify at zero cost:\n"
          f"  python scraper.py --profile {args.name} --dry-run")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(cfg.REPO_ROOT, ".env"))
    main()
