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
MODEL = "gemini-2.5-flash"
DRY_RUN_DEFAULT = True

# --- Applicant contact block (used in the email signature) -----------------
# Fill from résumé during implementation; email is also the SMTP sender.
ME = {
    "name": "Kartik Verma",
    "email": "kartikverma674@gmail.com",
    "phone": "",
    "linkedin": "",
    "github": "",
}

# --- SMTP (Gmail) ----------------------------------------------------------
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# --- Channels --------------------------------------------------------------
CHANNELS = {"email": True}  # Phase-2 channels intentionally absent


def latest_input_csv():
    """Return INPUT_CSV if set, else the newest output/jobs_combined_*.csv (or None)."""
    if INPUT_CSV:
        return INPUT_CSV
    matches = glob.glob(os.path.join(OUTPUT_DIR, "jobs_combined_*.csv"))
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)
