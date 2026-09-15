"""Merge per-batch --answer-key reports into the one 52-document verdict.

    python -m bench.merge_answer_key OUT.json BATCH.json [BATCH.json ...]

The corpus runs in batches only so that a machine reset loses one batch,
not the corpus — the Mac reset itself mid-run on 14 September 2026
(undervoltage lockout) after 34 of 52 documents, and the report, which was
written only at the end, went with it.

The merge refuses to combine batches taken under different conditions:
the same clock month (it decides what "present" means), the same market
corpus, the same endpoint and the same pinned model. So a batch from one
month cannot be glued to a batch from the next. Nothing is re-scored here:
rows are carried exactly as the benchmark wrote them, then the gate's and
the answer key's own summaries run over all of them.
"""

import json
import os
import sys

from bench import answer_key as ak
from bench import backends as bb


def conditions(report):
    return (tuple(report["now"]), report["market_sha256"], report["url"],
            report["baseline"]["digest"])


def merge(reports):
    """One report from several batches, or ValueError."""
    if len({conditions(r) for r in reports}) != 1:
        raise ValueError("batches were not taken under identical conditions "
                         "(clock month, market corpus, endpoint, model)")
    order = [slug for slug, _ in bb.documents(layouts=bb.LAYOUTS)]
    rows = sorted((row for r in reports for row in r["rows"]),
                  key=lambda row: order.index(row["resume"]))
    slugs = [row["resume"] for row in rows]
    if len(set(slugs)) != len(slugs):
        raise ValueError("a document appears in more than one batch")
    return {**reports[0], "requested": len(order), "rows": rows,
            "not_run": [s for s in order if s not in slugs]}


def table(combined, log=print):
    """Every document on one line: local, Modal, and how they relate."""
    log("=" * 78 + "\nPER-DOCUMENT: search/filter-driving correctness vs the answer key\n"
        + "=" * 78)
    log(f"  {'document':16} {'local':7} {'Modal':7} {'backends':22} groups not both-correct")
    for row in combined["rows"]:
        if "scoring" not in row:
            log(f"  {row['resume']:16} ERROR {row.get('error')}")
            continue
        s = row["scoring"]
        relation = ("identical" if row["exact_match"] else
                    "downstream-equivalent" if row["semantic_match"] else
                    "behaviour-changing")
        odd = [f"{g}={e['category']}{'*' if e['driving'] else ''}"
               for g, e in s["groups"].items() if e["category"] != "both correct"]
        log(f"  {row['resume']:16} {'OK' if s['local_driving_correct'] else 'WRONG':7} "
            f"{'OK' if s['modal_driving_correct'] else 'WRONG':7} {relation:22} "
            f"{'; '.join(odd) if odd else '-'}")
    log("  (* = search/filter-driving)")
    if combined["not_run"]:
        log(f"\n  NOT RUN: {combined['not_run']}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        raise SystemExit(__doc__)
    out, paths = argv[0], argv[1:]
    try:
        combined = merge([json.load(open(p)) for p in paths])
    except ValueError as exc:
        raise SystemExit(f"STOP: {exc}")
    combined["batches"] = paths
    table(combined)
    bb.summarise(combined)
    ok = ak.summarise(combined)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(combined, fh, indent=2, ensure_ascii=False)
    os.chmod(out, 0o600)
    print(f"\nmerged report: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
