"""
Configuration for the résumé-aware job auto-applier (Phase 1 — email).

Everything that decides WHICH jobs are considered and HOW drafts/sends behave
lives here. Secrets never live here — they come from .env (GEMINI_API_KEY,
SMTP_USER, SMTP_APP_PASSWORD). Paths resolve from the repo root via __file__, so
commands work from any working directory.
"""

import glob
import os

# --- Paths -----------------------------------------------------------------
AUTO_APPLY_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(AUTO_APPLY_DIR)
OUTPUT_DIR = os.path.join(REPO_ROOT, "output")

# Explicit input CSV, or None to auto-pick the newest jobs_combined_*.csv.
INPUT_CSV = None

RESUME_PDF = os.path.join(AUTO_APPLY_DIR, "resume", "resume.pdf")
RESUME_TXT = os.path.join(AUTO_APPLY_DIR, "resume", "resume.txt")
DRAFTS_DIR = os.path.join(AUTO_APPLY_DIR, "drafts")
REVIEW_QUEUE = os.path.join(AUTO_APPLY_DIR, "review_queue.csv")
APPLICATIONS_LOG = os.path.join(AUTO_APPLY_DIR, "applications.csv")
ANSWERS_FILE = os.path.join(AUTO_APPLY_DIR, "answers.yaml")

# --- Selection / pacing ----------------------------------------------------
MIN_SCORE = 10            # drop jobs scoring below this (recruiter/Python noise sinks out)
PER_RUN_CAP = 5           # default cap; --limit overrides
SEND_DELAY_SECONDS = 20   # delay between sends on --send

# --- Tailoring -------------------------------------------------------------
# Read by tailor.draft_email (one call per job) and make_profile.py (one call
# per résumé). Retired models 404 with "no longer available", which is silent
# until you spend: gemini-2.5-flash went first, then gemini-2.0-flash, which is
# what this was pinned to until 2026-09-08.
#
# gemini-3.6-flash is the replacement the API itself names in that 404. Still a
# pin, not the gemini-flash-latest alias, because the alias hits the same
# transient 503s and the pin is the version we have actually measured — flash
# reproduced the discriminative-weighting fix on the Salesforce résumé that
# RESUME_AUTOCONFIG_PROMPT.md documents, so a bigger model buys nothing here.
# The 503s are handled by retry (make_profile.generate), not by model choice.
# gemini-3.1-pro-preview is NOT usable: 429 RESOURCE_EXHAUSTED, no pro quota.
MODEL = "gemini-3.6-flash"

# The ladder make_profile.generate() walks when a model's DAILY quota is spent.
# RPD is counted per model, so the next one down has its own budget — measured
# on the free tier, 2026-09-09, when 3.6 sat at 19/20 requests for the day and
# a résumé that had parsed the day before stopped parsing:
#
#     gemini-3.6-flash        20 RPD    the pin above; best measured answers
#     gemini-3.8-flash        20 RPD    same family, separate budget
#     gemini-3.1-flash-lite  500 RPD    25x the headroom, weaker answers
#
# Order is deliberate: quality first, and flash-lite last because it is the one
# that will still answer at the end of a heavy day. A model that 404s (retired,
# or an id typo) is skipped like an exhausted one rather than stopping the
# ladder — only the first entry is one this repo has measured, so verify the
# other two against aistudio.google.com/app/apikey before trusting their names.
MODELS = (MODEL, "gemini-3.8-flash", "gemini-3.1-flash-lite")
DRY_RUN_DEFAULT = True

# --- Applicant contact block (used in the email signature) -----------------
# Fill from résumé during implementation; email is also the SMTP sender.
ME = {
    "name": "Kartik Verma",
    "email": "kartikverma674@gmail.com",
    "phone": "9518069412",
    "linkedin": "",
    "github": "",
}

# --- SMTP (Gmail) ----------------------------------------------------------
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# --- Phase 2: LinkedIn Easy Apply autofill userscript ----------------------
# Generated .user.js embeds the Groq key + résumé text -> gitignored.
USERSCRIPT_TEMPLATE = os.path.join(AUTO_APPLY_DIR, "userscript_template.js")
USERSCRIPT_OUT = os.path.join(AUTO_APPLY_DIR, "linkedin-easyapply.user.js")
SHORTLIST_OUT = os.path.join(AUTO_APPLY_DIR, "shortlist.html")
# LLM fallback for novel free-text questions (Gemini key has zero quota; Groq
# free tier verified working 2026-07-21). Key comes from .env GROQ_API_KEY.
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# --- Channels --------------------------------------------------------------
CHANNELS = {"email": True}  # Phase-2 channels intentionally absent


def latest_input_csv():
    """Return INPUT_CSV if set, else the newest output/jobs_combined_*.csv (or None)."""
    if INPUT_CSV:
        return INPUT_CSV
    matches = glob.glob(os.path.join(OUTPUT_DIR, "jobs_combined*.csv"))
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)
