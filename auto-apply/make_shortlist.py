"""
Build a shareable job shortlist for a person, in one step.

Full per-person flow:

  1. Drop their résumé at  auto-apply/resume/resume.pdf
  2. Derive a profile from it:
     python auto-apply/make_profile.py --name <name> \
         --locations "Bengaluru,Remote" --exclude-levels "intern,fresher"
  3. python auto-apply/make_shortlist.py --scrape "Their Name"
  4. Publish auto-apply/shortlist.html for a shareable link.

Or drive the whole thing in a browser with `python -m sweep`.

Step 2 used to say the retune "needs Claude's judgment, so it isn't scripted"
and told you to paste RESUME_AUTOCONFIG_PROMPT.md into a Claude window.
make_profile.py replaced that, and it also validates every key it emits
against the live config — which is how two keys that prompt had invented
(SCORING.drop_terms, SETTINGS.min_ctc_lpa) were found to have never done
anything.

Without --scrape it just rebuilds the HTML from the latest existing CSV — free,
no Apify credits. Use --scrape only after step 2, to fetch jobs for THIS résumé.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apply_config as cfg
import linkedin_shortlist


def main(argv):
    scrape = "--scrape" in argv
    name = " ".join(a for a in argv if not a.startswith("--")).strip()
    if scrape:
        print("Running the scraper — this uses Apify credits (see config.py cost guards)…")
        subprocess.run([sys.executable, os.path.join(cfg.REPO_ROOT, "scraper.py")], check=True)
    path, n = linkedin_shortlist.generate(candidate=name)
    print("Wrote " + path + " (" + str(n) + " jobs).")
    print("Next: open it in a browser, or send the file to anyone — it's self-contained.")


if __name__ == "__main__":
    main(sys.argv[1:])
