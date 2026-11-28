#!/usr/bin/env bash
# Experiment 10c on the 500-task set (user, 2026-11-26): E10b (split pool alpha = 0.5, P = E7b, F = memory-aware FCFS, strict,
# no F concurrency limit, oversized requests fail with their task) plus a per-partition deadlock breaker
# (--split-breaker partition): if nothing runs in a partition and none of its waiting continuations fits, discard
# that partition's continuation holding the fewest cells. 500-task set; then compare_full500. Launch detached:
#   setsid nohup experiments/mem_admission/run_e10c_full500.sh > results/mem_admission/run_full500_e10c.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
name=full500_e10c_cap2048_gbm_split_a050_fcfs_mem_pbreak
[ -f "results/mem_admission/$name/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_full_500_seed2.jsonl \
    --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --alpha 0.5 --fifo-conc 32 \
    --split-breaker partition
$PY -W ignore -m experiments.mem_admission.compare_full500
