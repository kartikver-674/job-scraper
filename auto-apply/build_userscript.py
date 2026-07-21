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
    (["years", "experience"], "years_experience"),
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
