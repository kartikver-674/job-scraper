"""Drive the DEPLOYED snapshot probe. Not `modal run` — and that matters.

Modal refuses memory snapshots on ephemeral apps:

    Memory snapshots are disabled for ephemeral apps.
    Deploy your app with `modal deploy` to enable memory snapshots.

`modal run` creates an ephemeral app, so running the experiment that way
silently disables the very thing it is measuring: the snapshot arm becomes
a second control, and the comparison reports "no difference" for a reason
that has nothing to do with Ollama. That is a worse outcome than an error,
because the number looks real.

So the snapshot experiment has two steps:

    modal deploy deploy/modal_snapshot_probe.py
    python deploy/run_snapshot_probe.py

This script parses the résumé locally with the production parser, then
calls the deployed classes. The snapshot arm is invoked TWICE: Modal's
first call creates the snapshot, the second restores from it, and only the
second is the measurement.
"""

import argparse
import json
import os
import sys

import modal

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
APP_NAME = "sweep-ollama-snapshot-probe"

sys.path.insert(0, HERE)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "auto-apply"))

from modal_snapshot_probe import _verdict          # one copy of the verdict


def arm(name):
    """A handle on a class in the DEPLOYED app."""
    try:
        return modal.Cls.from_name(APP_NAME, name)()
    except Exception as exc:
        raise SystemExit(
            f"could not reach {name} in the deployed app {APP_NAME!r}: {exc}\n"
            f"  deploy it first:  modal deploy deploy/modal_snapshot_probe.py"
        ) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", default=os.path.join(
        REPO_ROOT, "auto-apply", "resume", "resume.pdf"))
    parser.add_argument("--arm", default="both",
                        choices=("both", "control", "snapshot"))
    parser.add_argument("--json", help="write the raw results here")
    args = parser.parse_args()

    from resume_parser import extract_text

    text = extract_text(args.resume)
    if not text.strip():
        raise SystemExit(f"no text extracted from {args.resume}")
    print(f"{os.path.basename(args.resume)}: {len(text)} chars, parsed "
          f"locally by the production parser\n")

    results = {}
    if args.arm in ("both", "control"):
        print("--- CONTROL ARM (snapshots off) ---", flush=True)
        results["control"] = arm("ControlArm").run.remote(text)

    if args.arm in ("both", "snapshot"):
        print("\n--- SNAPSHOT ARM (alpha GPU memory snapshot) ---")
        print("First invocation CREATES the snapshot and will not be fast.",
              flush=True)
        snapshot = arm("SnapshotArm")
        snapshot.run.remote(text)
        print("\nSecond invocation — this one restores, and is the "
              "measurement.", flush=True)
        results["snapshot"] = snapshot.run.remote(text)

    if "control" in results and "snapshot" in results:
        _verdict(results["control"], results["snapshot"])
    print("\nJSON:")
    print(json.dumps(results, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
