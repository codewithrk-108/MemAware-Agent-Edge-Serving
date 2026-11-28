#!/usr/bin/env bash
# Experiment 11b on the 500-task set (user, 2026-11-27): E7b (cap 2,048, memory-aware admission, online gradient-boosted
# length order) + slot reuse with own-first slot order (--slot-reuse --slot-order own_first; plain LRU memory guard).
# Same trace, arrivals and flags as the E7b 500-task run; rebuilt binary. Then compare_full500 and the reuse analysis
# against the old-binary E7b 500-task run. Launch detached:
#   setsid nohup experiments/mem_admission/run_e11b_full500.sh > results/mem_admission/run_full500_e11b.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
name=full500_e11b_cap2048_gbm_slotreuse_ownfirst
[ -f "results/mem_admission/$name/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_full_500_seed2.jsonl \
    --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --slot-reuse --slot-order own_first
$PY -W ignore -m experiments.mem_admission.compare_full500
$PY -W ignore -m experiments.mem_admission.analyze_e11 "$name" full500_e7b_cap2048_online_gbm e11b_full500_analysis E11b || true
