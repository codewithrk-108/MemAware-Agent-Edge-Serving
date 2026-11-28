#!/usr/bin/env bash
# Experiment 11c smoke (user approved, 2026-11-26): E11b (slot reuse, own-first slot order) + memory guard that erases
# finished tasks' leftovers first (LRU among them), then LRU leftovers of live tasks (--guard-order finished_first).
# Same 50-task set, arrivals and rebuilt binary as E11/E11b; control = smoke_e7b_cap2048_gbm_rebuilt (existing).
#   setsid nohup experiments/mem_admission/run_e11c_smoke.sh > results/mem_admission/run_smoke_e11c.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
name=smoke_e11c_cap2048_gbm_slotreuse_ownfirst_guardfin
[ -f "results/mem_admission/$name/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_50_seed2.jsonl \
    --n-tasks 50 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --slot-reuse --slot-order own_first \
    --guard-order finished_first
$PY -W ignore -m experiments.mem_admission.analyze_e11 "$name" smoke_e7b_cap2048_gbm_rebuilt e11c_smoke_analysis E11c
