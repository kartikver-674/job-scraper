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
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apply_config as cfg
import resume_parser
import tailor

from google.genai import types

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
        "domain_bonus": {"type": "integer"},
        "notes": {"type": "string"},
    },
    "required": [
        "field_summary", "years_experience", "role_keywords", "skill_weights",
        "penalty_terms", "domain_half_a", "domain_half_b", "domain_title_terms",
        "domain_bonus", "notes",
    ],
}


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
        "field."
    )


def generate(client, model, resume_text, prefs, attempts=5, sleep=time.sleep):
    """One structured Gemini call, retried on the transient 503s this API throws."""
    prompt = build_prompt(resume_text, prefs)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        temperature=0.2,
    )
    for attempt in range(attempts):
        try:
            response = client.models.generate_content(
                model=model, contents=prompt, config=config)
            return json.loads(response.text)
        except Exception as exc:
            # 503 UNAVAILABLE ("high demand") is common on this endpoint — it
            # hit 2 of 6 calls while this was written, then exhausted a 3-try
            # budget on the first real run, which is why the budget is 5 with a
            # widening back-off. Anything else is a real error: fail loudly
            # rather than burning the budget on a retired model's 404.
            if attempt == attempts - 1 or "503" not in str(exc):
                raise
            print(f"  503 from {model}, retrying in {5 * (attempt + 1)}s "
                  f"({attempt + 1}/{attempts - 1})")
            sleep(5 * (attempt + 1))


def _weights(entries, sign=1):
    """Fold [{term, weight}] into {term: weight}, lowercased, sign applied."""
    return {e["term"].strip().lower(): sign * abs(int(e["weight"])) for e in entries
            if e["term"].strip()}


def validate_keys(rendered_keys):
    """Reject any config key that config.py does not actually define.

    Profile merge is a plain dict.update(), so a misspelled key is accepted in
    silence and does nothing — which is how RESUME_AUTOCONFIG_PROMPT.md came to
    name `SCORING.drop_terms` and `SETTINGS.min_ctc_lpa`, neither of which has
    ever existed. Checked against the live config so it cannot drift again.
    """
    argv, job_profile = sys.argv, os.environ.pop("JOB_PROFILE", None)
    sys.argv = [argv[0]]          # config.py reads --profile from argv at import
    try:
        sys.path.insert(0, cfg.REPO_ROOT)
        import config
    finally:
        sys.argv = argv
        if job_profile is not None:
            os.environ["JOB_PROFILE"] = job_profile

    known = {"SEARCH": config.SEARCH, "SETTINGS": config.SETTINGS,
             "SCORING": config.SCORING}
    unknown = [f"{section}.{key}" for section, keys in rendered_keys.items()
               for key in keys if key not in known[section]]
    if unknown:
        raise KeyError(f"not real config keys: {', '.join(unknown)}")


def _fmt(value, indent=8):
    """Render a dict or list as readable, line-wrapped Python source."""
    pad = " " * indent
    if isinstance(value, dict):
        body = "".join(f"{pad}{k!r}: {v!r},\n" for k, v in value.items())
    else:
        body = "".join(f"{pad}{v!r},\n" for v in value)
    open_, close = ("{", "}") if isinstance(value, dict) else ("[", "]")
    return f"{open_}\n{body}{' ' * (indent - 4)}{close}" if value else f"{open_}{close}"


def render(name, data, prefs):
    """Render profiles/<name>.py source from the model's JSON and the preferences."""
    sections = {
        "SEARCH": ["role_keywords", "experience_years", "locations", "salary_min"],
        "SETTINGS": ["max_experience_years", "min_comp_usd"],
        "SCORING": ["skill_weights", "penalty_terms", "frontend_terms",
                    "backend_terms", "fullstack_title_terms", "fullstack_bonus",
                    "hard_drop_terms"],
    }
    validate_keys(sections)

    years = int(data["years_experience"])
    skills = _weights(data["skill_weights"])

    # The model reliably copies the excluded seniority words into penalty_terms
    # as well, even when told they are already handled. With drop_excluded True
    # (the default) hard_drop_terms deletes those rows outright, so the penalty
    # is dead weight — and if that flag is ever flipped off they would be
    # penalized twice, once by drop_penalty and once here. Strip them.
    excluded = set(prefs["exclude_levels"])
    penalties = {term: weight
                 for term, weight in _weights(data["penalty_terms"], sign=-1).items()
                 if term not in excluded}
    return f'''"""{name} — generated from a résumé by auto-apply/make_profile.py.

{data["field_summary"]}

    python scraper.py --profile {name} --dry-run   # cost check, free
    python scraper.py --profile {name} --yes

HOW THE MODEL READ THIS RÉSUMÉ
{data["notes"]}

Skill weights are by DISCRIMINATIVE POWER, not centrality: a term that would
also appear in an unwanted job is weighted low however core it is to this
person. Locations, pay floor, avoid-list and excluded seniority came from the
command line, not from the résumé. Anything absent here inherits from config.py.

COSTS MONEY BY DEFAULT. This file sets no SITES and no max_spend_usd, so it
inherits config.py's — LinkedIn + Indeed + Naukri all enabled. Run --dry-run
first and read the run count; Naukri alone is ~$0.50/run minimum. To narrow it,
copy the SITES block from profiles/kartik_reachable.py. LinkedIn searches the
geoIds in SITES, NOT the locations above, so city-level LinkedIn needs that
block too.

Re-scoring is free — after editing weights run `python rescore_from_apify.py`
rather than paying to scrape again.
"""

SEARCH = {{
    "role_keywords": {_fmt(data["role_keywords"])},
    "experience_years": {years},
    "locations": {_fmt(prefs["locations"])},
    "salary_min": None,
}}

SETTINGS = {{
    # Title bands are a label; this reads the years a posting actually demands.
    "max_experience_years": {years + 3},
    "min_comp_usd": {prefs["min_comp_usd"]!r},
}}

SCORING = {{
    "skill_weights": {_fmt(skills)},

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
'''


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
    args = parser.parse_args(argv)

    out = os.path.join(cfg.REPO_ROOT, "profiles", f"{args.name}.py")
    if os.path.exists(out) and not args.force:
        sys.exit(f"{out} exists. Re-run with --force to overwrite (git has the old one).")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY missing from .env.")

    prefs = {
        "locations": _csv(args.locations),
        "exclude_levels": [w.lower() for w in _csv(args.exclude_levels)],
        "avoid": _csv(args.avoid),
        "min_comp_usd": args.min_comp_usd,
    }

    resume_text = resume_parser.load_resume(args.resume, cfg.RESUME_TXT)
    print(f"Résumé: {len(resume_text)} chars from {args.resume}")

    try:
        data = generate(tailor.get_client(api_key), cfg.MODEL, resume_text, prefs)
    except Exception as exc:
        if "503" in str(exc):
            sys.exit(f"{cfg.MODEL} is overloaded (503) and did not recover. "
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
    print(f"\nRead it, then verify at zero cost:\n"
          f"  python scraper.py --profile {args.name} --dry-run")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(cfg.REPO_ROOT, ".env"))
    main()
