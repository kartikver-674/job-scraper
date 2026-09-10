"""Read a sweep's real progress, and start and stop the engine.

Progress comes from output/<profile>/.done_combos, which scraper.py appends to
as each search finishes (scraper.py:1743-1744). That file is what the engine
already trusts to resume a capped sweep without re-billing, which makes it the
honest record — stdout is a formatting detail that would break in silence.
"""

import os
import re
import signal
import subprocess
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def today():
    return datetime.now().strftime("%Y-%m-%d")


def combo_key(day, site_key, search):
    """One ledger key. Must match scraper.py:1743-1744 byte for byte:
        {date}|{site}|{keywords}|{location}|{company or ''}

    One function, because two places now build these — the progress grid and
    the price of what is left. A second copy that drifted would silently
    price searches the engine is about to skip.
    """
    return (f"{day}|{site_key}|{search['keywords']}|"
            f"{search['location']}|{search.get('company') or ''}")


def combo_keys(raw_plan, day):
    """Every ledger key this plan intends to write, in plan order."""
    return [combo_key(day, site_key, s)
            for site_key, searches in raw_plan["sites"].items()
            for s in searches]


def remaining_plan(raw_plan, done, day):
    """`raw_plan` with today's finished combos dropped, and how many went.

    scraper.py prints `--dry-run --json` at line 1815, BEFORE it loads
    .done_combos at 1888 — so the plan the UI prices is always the whole
    sweep, including searches the engine is about to skip. Pricing that
    after an interruption quotes the full sweep to resume a fraction of it,
    and the over-cap gate then refuses a resume the remainder could easily
    afford.

    The same applies to a second sweep of the SAME day, which the engine
    skips wholesale — scraper.py's own comment records that shipping as a
    run which "skipped every combo, scraped nothing, and still printed a
    normal-looking summary". Priced from here it reads as $0.00 before the
    button is pressed.
    """
    sites, dropped = {}, 0
    for site_key, searches in raw_plan["sites"].items():
        kept = [s for s in searches
                if combo_key(day, site_key, s) not in done]
        dropped += len(searches) - len(kept)
        if kept:
            sites[site_key] = kept
    return dict(raw_plan, sites=sites), dropped


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


def short_location(location, limit=3):
    """A 2-4 character stand-in for a location name, for the progress grid.

    The cells are 44px wide, so the full name never fit — but the plan is
    keyword-major (scraper.build_search_plan loops locations INSIDE keywords),
    which means the location is what changes from one cell to the next and so
    the one worth showing. The full "keyword @ location" stays on the hover.

    Initials for a multi-word name ("United Arab Emirates" -> UAE), the first
    few letters otherwise ("Bengaluru" -> BEN). Ambiguity is possible in
    principle and absent in practice across config's verified names — and the
    tooltip resolves it either way.
    """
    words = re.findall(r"[A-Za-z0-9]+", location or "")
    if not words:
        return "?"
    if len(words) > 1:
        return "".join(w[0] for w in words)[:4].upper()
    return words[0][:limit].upper()


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
            # Shown INSIDE the cell, so the grid says what each search is
            # for without a hover. Computed here rather than in the template:
            # one place, and testable without a browser.
            "short": short_location(location),
            "state": "done" if key in done else "pending",
        })
    finished = sum(1 for t in tiles if t["state"] == "done")
    return {
        "planned": len(planned),
        # Distinct sites in plan order. The grid groups by these so the site
        # is stated once per row instead of squeezed into every cell.
        "sites": list(dict.fromkeys(t["site"] for t in tiles)),
        "done": finished,
        "outstanding": len(planned) - finished,
        "fraction": (finished / len(planned)) if planned else 0.0,
        "tiles": tiles,
    }


def done_path_for(output_dir):
    return os.path.join(output_dir, ".done_combos")


def start(profile, env=None, popen=subprocess.Popen):
    """Launch a sweep. --yes because the UI already took the confirmation.

    stderr is inherited, not piped: nothing in this app ever reads a child's
    stderr — progress comes from .done_combos and liveness from poll() — and
    an undrained pipe blocks the child once its ~64KB buffer fills, which
    poll() then reports as permanently running. Inheriting costs nothing and
    puts the engine's own errors in the terminal the server is already
    printing to.
    """
    return popen([sys.executable, "scraper.py", "--profile", profile, "--yes"],
                 cwd=REPO_ROOT, env=env or os.environ.copy(),
                 stdout=subprocess.DEVNULL, stderr=None, text=True)


def start_rescore(profile, hours, env=None, popen=subprocess.Popen):
    """Re-score already-paid Apify datasets against this profile's weights.

    Free: reading a dataset costs no actor events. JOB_PROFILE is how
    config.py picks the profile when there is no --profile in argv
    (config.py:632-640), which is also what redirects the output directory.
    """
    child = dict(env or os.environ, JOB_PROFILE=profile)
    # stderr inherited for the reason in start(), and DEVNULL would be worse
    # than either: rescore_from_apify.py's two expected outcomes are
    # sys.exit("No APIFY_TOKEN* found...") and sys.exit("Nothing to re-score.
    # Widen --hours/--limit..."), both on stderr, and the UI shows only
    # liveness — so discarding them deletes the explanation for a re-score
    # that appears to do nothing.
    return popen([sys.executable, "rescore_from_apify.py", "--hours", str(hours)],
                 cwd=REPO_ROOT, env=child,
                 stdout=subprocess.DEVNULL, stderr=None, text=True)


def start_merge(profile, env=None, popen=subprocess.Popen):
    """Fold this profile's earlier sweeps into one deduped shortlist.

    Free and entirely local: merge_jobs.py reads the jobs_*.json files already
    on disk and writes jobs_combined.*. JOB_PROFILE is how config.py picks the
    profile with no --profile in argv, which is also what redirects the output
    directory — the same mechanism start_rescore uses.

    stderr inherited for the reason in start(): merge_jobs.py's expected
    failure is sys.exit("No jobs_*.json files to merge under ..."), and the UI
    shows only liveness, so discarding it deletes the explanation for a merge
    that appears to do nothing.
    """
    child = dict(env or os.environ, JOB_PROFILE=profile)
    return popen([sys.executable, "merge_jobs.py"], cwd=REPO_ROOT, env=child,
                 stdout=subprocess.DEVNULL, stderr=None, text=True)


def stop(proc):
    """Ask the engine to stop. Everything already fetched is already on disk:
    scraper.py emits after every search, so SIGINT never loses rows."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    return proc.poll()
