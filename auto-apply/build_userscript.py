"""
Generate the LinkedIn Easy Apply autofill userscript.

Bakes a résumé-grounded answer bank, free-text templates, the résumé text, and
Groq config into a Tampermonkey userscript by injecting JSON into
userscript_template.js. The output file contains the Groq API key and résumé
text, so it is gitignored. Regenerate after editing answers.yaml.

Run:  python auto-apply/build_userscript.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apply_config as cfg
from answers import load_answers
from resume_parser import load_resume

# Seed keyword patterns -> the answers.yaml key that answers them. All lowercase
# keywords must appear in a question label to match. Values come only from
# answers.yaml -- nothing is invented; unmatched questions are flagged.
BANK_PATTERNS = [
    (["notice"], "notice_period"),
    (["expected", "ctc"], "expected_ctc"),
    (["salary"], "expected_ctc"),
    (["current", "location"], "current_location"),
    (["relocat"], "willing_to_relocate"),
    (["authoriz"], "work_authorization"),
    (["work", "permit"], "work_authorization"),
    (["years", "react"], "years_experience"),
    (["years", "node"], "years_experience"),
    (["years", "javascript"], "years_experience"),
    (["years", "typescript"], "years_experience"),
    (["years", "mongo"], "years_experience"),
    (["total", "experience"], "years_experience"),
    (["overall", "experience"], "years_experience"),
]

# Free-text templates: keywords -> template with {company}/{title} slots filled
# from the page at runtime. Grounded, user-editable prose.
FREE_TEXT = [
    {
        "keywords": ["why"],
        "template": (
            "I'm excited about the {title} role at {company} - it fits my ~2 years "
            "of full-stack experience with React, React Native, TypeScript, Node.js, "
            "Express and MongoDB."
        ),
    },
]


def build_bank(answers):
    """Seed patterns + answers.yaml values -> [{keywords, answer}]. Skip any
    pattern whose value is missing/blank (never emit a blank/fabricated answer)."""
    bank = []
    for keywords, key in BANK_PATTERNS:
        value = (answers.get(key) or "").strip()
        if value:
            bank.append({"keywords": keywords, "answer": value})
    return bank


def render_userscript(template, bank, free_text, resume_text,
                      groq_key, groq_model, groq_endpoint, me):
    """Replace each /*__TOKEN__*/ with a json.dumps'd value (valid JS literals)."""
    replacements = {
        "/*__BANK__*/": json.dumps(bank),
        "/*__FREE_TEXT__*/": json.dumps(free_text),
        "/*__RESUME__*/": json.dumps(resume_text),
        "/*__GROQ_KEY__*/": json.dumps(groq_key),
        "/*__GROQ_MODEL__*/": json.dumps(groq_model),
        "/*__GROQ_ENDPOINT__*/": json.dumps(groq_endpoint),
        "/*__ME__*/": json.dumps(me),
    }
    out = template
    for token, value in replacements.items():
        out = out.replace(token, value)
    return out


def _read_env_key(name):
    """Read a KEY=value from the repo-root .env (secrets are not in apply_config)."""
    env_path = os.path.join(cfg.REPO_ROOT, ".env")
    if not os.path.exists(env_path):
        return ""
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return ""


def generate():
    answers = load_answers(cfg.ANSWERS_FILE)
    resume_text = load_resume(cfg.RESUME_PDF, cfg.RESUME_TXT)
    groq_key = os.environ.get("GROQ_API_KEY") or _read_env_key("GROQ_API_KEY")
    if not groq_key:
        print("WARNING: GROQ_API_KEY not set — LLM fallback (tier 3) disabled.")
    with open(cfg.USERSCRIPT_TEMPLATE, "r", encoding="utf-8") as f:
        template = f.read()
    out = render_userscript(
        template, build_bank(answers), FREE_TEXT, resume_text,
        groq_key, cfg.GROQ_MODEL, cfg.GROQ_ENDPOINT, cfg.ME,
    )
    with open(cfg.USERSCRIPT_OUT, "w", encoding="utf-8") as f:
        f.write(out)
    return cfg.USERSCRIPT_OUT


if __name__ == "__main__":
    print("Wrote " + generate())
