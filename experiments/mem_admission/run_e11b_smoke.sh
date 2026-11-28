#!/usr/bin/env bash
# Experiment 11b smoke (user approved, 2026-11-26): E11 slot reuse with --slot-order own_first:
# own leftover > finished task's leftover with a shared prefix > empty slot > LRU leftover of a live task.
# Same 50-task set, arrivals and rebuilt binary as the E11 smoke; control = smoke_e7b_cap2048_gbm_rebuilt (existing).
#   setsid nohup experiments/mem_admission/run_e11b_smoke.sh > results/mem_admission/run_smoke_e11b.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
name=smoke_e11b_cap2048_gbm_slotreuse_ownfirst
[ -f "results/mem_admission/$name/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_50_seed2.jsonl \
    --n-tasks 50 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --slot-reuse --slot-order own_first
$PY -W ignore -m experiments.mem_admission.analyze_e11 "$name" smoke_e7b_cap2048_gbm_rebuilt e11b_smoke_analysis E11b
