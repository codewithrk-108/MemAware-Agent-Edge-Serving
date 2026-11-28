#!/usr/bin/env bash
# Experiment 10 on the 500-task set: split KV pool. Partition P (alpha = 0.5) runs E7b (online gradient-boosted length
# model order, backfill); partition F (the other half) admits by ascending ready time (as FIFO N4), strict, at most 2 in
# flight. A request needing more than a partition (history + cap) fails with its task. Cap 2,048, reservation
# history + cap, continuations first in their own partition. /opt/pytorch Python.
# Launch detached:
#   setsid nohup experiments/mem_admission/run_e10_full500.sh > results/mem_admission/run_full500_e10.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
name=full500_e10_cap2048_gbm_split_a050_fifo2
[ -f "results/mem_admission/$name/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_full_500_seed2.jsonl \
    --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --alpha 0.5 --fifo-conc 2
$PY -W ignore -m experiments.mem_admission.compare_full500
