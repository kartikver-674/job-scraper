"""
Résumé-aware job auto-applier — Phase 1 (email).

Default (dry-run): read the latest ranked CSV, draft tailored emails for jobs with
a recruiter email and score >= MIN_SCORE (skipping already-applied), write .md drafts
+ a review queue, and record them. Sends nothing.

--send: send ONLY review-queue rows you've marked `approved`, with a per-email y/N
confirmation, résumé attached, a delay between sends, and the log updated to `sent`.

Run from the repo root:
    python auto-apply/apply.py --dry-run --limit 2
    python auto-apply/apply.py --send --limit 2
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv

import apply_config as cfg
import answers as answers_mod
import emailer
import records
import resume_parser
import selection
import tailor


# --- dependency-injected core (tested) -------------------------------------

def run_dry_run(cfg, *, limit, min_score, tailor_fn, resume_text_fn, answers_fn):
    """Draft emails for qualifying jobs. Returns the number of drafts written."""
    csv_path = cfg.latest_input_csv()
    if not csv_path:
        print("No input CSV found in output/. Run the scraper first.")
        return 0
    rows = selection.load_jobs(csv_path)
    applied = selection.load_applied_keys(cfg.APPLICATIONS_LOG)
    candidates = selection.select_candidates(rows, min_score, applied, limit)
    if not candidates:
        print("No new emailable jobs at score >= %d (all applied or below threshold)."
              % min_score)
        return 0

    resume_text = resume_text_fn()
    answers = answers_fn()
    count = 0
    for job in candidates:
        key = selection.job_key(job)
        draft = tailor_fn(job)
        path = records.write_draft_md(cfg.DRAFTS_DIR, key, job["hr_email"],
                                      draft["subject"], draft["body"],
                                      draft["grounding_notes"])
        records.append_review_row(cfg.REVIEW_QUEUE, {
            "job_key": key, "to": job["hr_email"], "subject": draft["subject"],
            "draft_path": path, "status": "draft"})
        records.upsert_application(cfg.APPLICATIONS_LOG, key, job["company"],
                                   job["title"], "email", "drafted")
        print(f"  drafted: {job['company']} — {job['title']}  ->  {path}")
        count += 1
    print(f"\n{count} draft(s) written. Review them, set status to 'approved' in "
          f"{os.path.basename(cfg.REVIEW_QUEUE)}, then run with --send.")
    return count


def run_send(cfg, *, limit, delay, confirm_fn, send_fn):
    """Send approved+confirmed drafts. Returns the number sent."""
    rows = records.read_review_rows(cfg.REVIEW_QUEUE)
    approved = [r for r in rows if r.get("status") == "approved"]
    if not approved:
        print("Nothing approved. Set status to 'approved' in the review queue first.")
        return 0
    sent = 0
    for row in approved[:limit] if limit is not None else approved:
        if not confirm_fn(row):
            print(f"  skipped: {row['to']}")
            continue
        # Re-read the body from the draft file (source of truth the user reviewed).
        body = _read_body_from_draft(row.get("draft_path", ""))
        if not body.strip():
            print(f"  skipped (empty draft body): {row['to']}")
            continue
        send_fn(row["to"], row["subject"], body)
        records.set_review_status(cfg.REVIEW_QUEUE, row["job_key"], "sent")
        records.upsert_application(cfg.APPLICATIONS_LOG, row["job_key"], "", "",
                                   "email", "sent")
        print(f"  sent: {row['to']}")
        sent += 1
        if delay:
            time.sleep(delay)
    print(f"\n{sent} email(s) sent.")
    return sent


def _read_body_from_draft(draft_path):
    """Extract the body between the two '---' fences of a draft .md (best-effort)."""
    if not draft_path or not os.path.exists(draft_path):
        return ""
    with open(draft_path, encoding="utf-8") as f:
        text = f.read()
    parts = text.split("\n---\n")
    return parts[1].strip() if len(parts) >= 2 else ""


# --- real wiring (not unit-tested; exercised manually) ---------------------

def _real_tailor(client, model):
    def _fn(job):
        return tailor.draft_email(client, model, job,
                                  _real_tailor.resume_text, _real_tailor.answers)
    return _fn


def _interactive_confirm(row):
    body = _read_body_from_draft(row.get("draft_path"))
    preview = body[:300]
    truncated = " (truncated)" if len(body) > 300 else ""
    print(f"To: {row['to']}")
    print(f"Subject: {row['subject']}")
    print(f"Body preview{truncated}:\n{preview}\n")
    ans = input(f"Send to {row['to']} — subject '{row['subject']}'? [y/N] ").strip().lower()
    return ans == "y"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Résumé-aware job auto-applier (Phase 1).")
    parser.add_argument("--send", action="store_true",
                        help="Send approved drafts (default is dry-run, sends nothing).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Explicit dry-run (default behaviour).")
    parser.add_argument("--limit", type=int, default=cfg.PER_RUN_CAP)
    parser.add_argument("--delay", type=int, default=cfg.SEND_DELAY_SECONDS)
    parser.add_argument("--min-score", type=int, default=cfg.MIN_SCORE)
    args = parser.parse_args(argv)

    load_dotenv(os.path.join(cfg.REPO_ROOT, ".env"))

    if args.send:
        user = os.environ.get("SMTP_USER")
        password = os.environ.get("SMTP_APP_PASSWORD")
        if not user or not password:
            print("SMTP_USER / SMTP_APP_PASSWORD missing from .env.")
            return 1
        from_addr = cfg.ME.get("email") or user

        def send_fn(to, subject, body):
            msg = emailer.build_message(from_addr, to, subject, body,
                                        attachment_path=cfg.RESUME_PDF)
            emailer.send_message(msg, cfg.SMTP_HOST, cfg.SMTP_PORT, user, password)

        run_send(cfg, limit=args.limit, delay=args.delay,
                 confirm_fn=_interactive_confirm, send_fn=send_fn)
        return 0

    # Dry-run (default).
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY missing from .env.")
        return 1
    client = tailor.get_client(api_key)
    _real_tailor.resume_text = resume_parser.load_resume(cfg.RESUME_PDF, cfg.RESUME_TXT)
    _real_tailor.answers = answers_mod.load_answers(cfg.ANSWERS_FILE)
    run_dry_run(cfg, limit=args.limit, min_score=args.min_score,
                tailor_fn=_real_tailor(client, cfg.MODEL),
                resume_text_fn=lambda: _real_tailor.resume_text,
                answers_fn=lambda: _real_tailor.answers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
