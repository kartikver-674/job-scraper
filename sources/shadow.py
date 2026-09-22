"""Shadow source evaluation — measured, never shipped to a user.

    SWEEP_FREE_SOURCE_SHADOW=1

The Free expansion audit discovered 15,798 candidate board identifiers, probed
365 of them and found 187 that answer with compatible public JSON. It
recommended enabling almost none of them: across four frozen candidate
fixtures, all 187 together contributed six positive-score jobs, and every one
of those six came from the nine boards below. That is the whole case for a
shadow tranche rather than a registry expansion.

WHAT SHADOW MEANS HERE, precisely:

  * nothing is fetched unless the flag is on;
  * rows are fetched through the UNCHANGED greenhouse adapter, counted, and
    then dropped on the floor — `run()` returns None, so there is no row for a
    caller to accidentally merge;
  * config.ATS_BOARDS is untouched, so the sweep's own source count, banner and
    registry are exactly what they were;
  * every board is isolated, and the whole tranche is wrapped again by its
    caller, so a shadow board cannot fail a user's sweep;
  * counts land in the telemetry record's `shadow_units`, kept apart from the
    sweep's own sources.

Eligibility, positive-score and marginal-unique yield are deliberately NOT
computed here. Answering "would this board have put a job in front of the user"
means running the whole scoring/filter/dedupe pipeline a second time over a
different row set, in a user's sweep, for a diagnostic — which is both a real
cost and a real risk to the run that person is waiting for. That measurement
belongs offline against the frozen fixtures, and lives in
bench/search_v2_shadow.py. This module answers only the questions production
can answer cheaply and safely: does the board respond, how fast, how many rows,
and how many survive the acquisition gate.

PubMatic is in the audit's nine but deliberately NOT here: its sampled job page
returned HTTP 403 in the spot-check, so its rows may not be reachable at all.
It waits in a reachability-review queue instead of being measured as if it were
comparable to the rest.
"""
import os

import telemetry

from . import ats

FLAG = "SWEEP_FREE_SOURCE_SHADOW"

# The eight boards recommended for shadow validation by
# docs/free-source-expansion-audit.md §8. Every token's list endpoint already
# returned valid normalized jobs, and every board identity was confirmed
# against the provider's own company endpoint in
# docs/search-v2-evidence/first-tranche-spotchecks.json.
#
# This is NOT config.ATS_BOARDS and must not be merged into it. A board becomes
# a real source through a reviewed registry change, after shadow evidence — not
# by someone moving a line from this file into that one.
BOARDS = {
    "greenhouse": {
        "fivetran": "Fivetran",
        "abnormalsecurity": "Abnormal Security",
        "apolloio": "Apollo.io",
        "brex": "Brex",
        "vercel": "Vercel",
        "jumio": "Jumio",
        "catawiki": "Catawiki",
        "zetaglobal": "Zeta Global",
    },
}


def enabled():
    """Read per call, so turning the flag off lands on the next sweep."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def run(keep_title, keep_location, is_home=None, log=print):
    """Fetch and measure the shadow tranche. Returns None — always.

    The return type is the safety property: there is no row set here for a
    caller to extend a sweep with, however the call site is later edited.
    """
    if not enabled():
        return None
    for platform, boards in BOARDS.items():
        if platform not in ats.ATS:                 # a typo, not a live source
            log(f"  shadow {platform:<9} {'-':<22} ! no adapter")
            continue
        for token, company in boards.items():
            with telemetry.unit("shadow", platform, board=f"{platform}:{token}",
                                timeout_s=25, shadow=True):
                try:
                    got = ats.fetch(platform, token, company, keep_title,
                                    keep_location, is_home)
                    log(f"  shadow {platform:<9} {company:<22} "
                        f"{len(got):>4} gated (discarded)")
                except Exception as exc:
                    telemetry.failed(exc)
                    log(f"  shadow {platform:<9} {company:<22} ! {exc}")
    return None
