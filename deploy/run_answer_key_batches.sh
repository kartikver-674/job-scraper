#!/bin/bash
# The 52-document answer-key benchmark, in four resumable batches.
#
#   caffeinate -i bash deploy/run_answer_key_batches.sh
#
# Prerequisites, in order: the Modal spend limit is set (docs/modal-equivalence.md),
# then  modal deploy deploy/modal_benchmark.py
#       python deploy/run_modal_benchmark.py prime
#       python deploy/run_modal_benchmark.py smoke
#
# Why batches: on 14 September 2026 the Mac reset itself (undervoltage
# lockout) after 34 of 52 documents, and the single end-of-run report went
# with it. Each batch now saves its own report; re-running this script
# skips batches already saved in this month's run directory, so it resumes.
# A new month gets a new directory, because the merge refuses to combine
# batches taken under different clocks.
#
# Whatever happens — success, failure, Ctrl-C — the public benchmark
# endpoint is stopped on exit, so nothing is left deployed to spend.
set -u
cd "$(dirname "$0")/.."
RUN_DIR=${RUN_DIR:-output/modal-equivalence/answer-key-$(date +%Y-%m)}
mkdir -p "$RUN_DIR"
STATUS=$RUN_DIR/status.log

teardown() {
  .venv/bin/modal app stop sweep-inference-benchmark --yes >/dev/null 2>&1
  echo "benchmark endpoint stopped at $(date +%H:%M:%S)" >> "$STATUS"
}
trap teardown EXIT

export SWEEP_BENCHMARK_TOKEN="$(cut -d= -f2 output/modal-equivalence/token.env)"
if ! URL="$(.venv/bin/python deploy/run_modal_benchmark.py url)"; then
  echo "no passed smoke test: deploy, prime and smoke first" | tee -a "$STATUS"
  exit 1
fi
date "+run started %Y-%m-%d %H:%M:%S in $RUN_DIR" >> "$STATUS"

run_batch() {
  n=$1; people=$2
  if [ -f "$RUN_DIR/batch-$n.json" ]; then
    echo "batch $n already saved — skipped" >> "$STATUS"; return
  fi
  for attempt in 1 2; do
    date "+batch $n ($people) attempt $attempt %H:%M:%S" >> "$STATUS"
    .venv/bin/python -u -m bench.backends --answer-key --people "$people" --layouts all \
      --url "$URL" --endpoint-state output/modal-equivalence/endpoint.json \
      --json "$RUN_DIR/batch-$n.json" > "$RUN_DIR/batch-$n.log" 2>&1
    echo "batch $n attempt $attempt exit=$? $(date +%H:%M:%S)" >> "$STATUS"
    # A cold-restore preflight failure is transient; one retry, no more.
    grep -q "Preflight failed" "$RUN_DIR/batch-$n.log" || break
  done
}

run_batch 1 ada,bhaskar,chen
run_batch 2 dmitri,esi,farida
run_batch 3 gopal,hana,iris
run_batch 4 jonas,kwame,lena,mateo
.venv/bin/python -m bench.merge_answer_key "$RUN_DIR/answer-key-52.json" \
  "$RUN_DIR"/batch-1.json "$RUN_DIR"/batch-2.json "$RUN_DIR"/batch-3.json "$RUN_DIR"/batch-4.json \
  > "$RUN_DIR/merged.log" 2>&1
echo "merge exit=$? $(date +%H:%M:%S)" >> "$STATUS"
cat "$STATUS"
