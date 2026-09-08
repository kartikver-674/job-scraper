"""Read a sweep's real progress, and start and stop the engine.

Progress comes from output/<profile>/.done_combos, which scraper.py appends to
as each search finishes (scraper.py:1743-1744). That file is what the engine
already trusts to resume a capped sweep without re-billing, which makes it the
honest record — stdout is a formatting detail that would break in silence.
"""

import os
import signal
import subprocess
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def today():
    return datetime.now().strftime("%Y-%m-%d")


def combo_keys(raw_plan, day):
    """Every ledger key this plan intends to write, in plan order.

    Must match scraper.py:1743-1744 byte for byte:
        {date}|{site}|{keywords}|{location}|{company or ''}
    """
    keys = []
    for site_key, searches in raw_plan["sites"].items():
        for s in searches:
            keys.append(f"{day}|{site_key}|{s['keywords']}|"
                        f"{s['location']}|{s.get('company') or ''}")
    return keys


def done_keys(done_path, day):
    """Keys already finished TODAY. Yesterday's lines are not progress.

    scraper.py:1716 loads the ledger filtered to today, so a sweep that spans
    midnight re-runs and re-bills everything. Mirror that here rather than
    reporting progress the engine will not honour.
    """
    if not os.path.exists(done_path):
        return set()
    with open(done_path) as fh:
        return {ln.strip() for ln in fh if ln.startswith(day)}


def progress(planned, done):
    """Per-tile state plus counts. Never divides by zero on an empty plan."""
    tiles = []
    for key in planned:
        # ponytail: no escaping of | in keywords; if keywords contain |, label is garbled.
        # Matches scraper.py:1743-1744's own non-escaping, so staying aligned is worth it.
        _, site, keywords, location, _company = key.split("|", 4)
        tiles.append({
            "site": site,
            "label": f"{keywords or '(all)'} @ {location}",
            "state": "done" if key in done else "pending",
        })
    finished = sum(1 for t in tiles if t["state"] == "done")
    return {
        "planned": len(planned),
        "done": finished,
        "outstanding": len(planned) - finished,
        "fraction": (finished / len(planned)) if planned else 0.0,
        "tiles": tiles,
    }


def done_path_for(output_dir):
    return os.path.join(output_dir, ".done_combos")


def start(profile, env=None, popen=subprocess.Popen):
    """Launch a sweep. --yes because the UI already took the confirmation."""
    return popen([sys.executable, "scraper.py", "--profile", profile, "--yes"],
                 cwd=REPO_ROOT, env=env or os.environ.copy(),
                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def start_rescore(profile, hours, env=None, popen=subprocess.Popen):
    """Re-score already-paid Apify datasets against this profile's weights.

    Free: reading a dataset costs no actor events. JOB_PROFILE is how
    config.py picks the profile when there is no --profile in argv
    (config.py:632-640), which is also what redirects the output directory.
    """
    child = dict(env or os.environ, JOB_PROFILE=profile)
    # stderr is DEVNULL, not PIPE: nobody reads a re-score's stderr, and an
    # undrained pipe blocks the child once it fills — which poll() would then
    # report as permanently in flight, wedging the button for good.
    return popen([sys.executable, "rescore_from_apify.py", "--hours", str(hours)],
                 cwd=REPO_ROOT, env=child,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)


def stop(proc):
    """Ask the engine to stop. Everything already fetched is already on disk:
    scraper.py emits after every search, so SIGINT never loses rows."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    return proc.poll()
