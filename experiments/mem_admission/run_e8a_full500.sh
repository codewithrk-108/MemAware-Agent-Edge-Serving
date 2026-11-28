#!/usr/bin/env bash
# Experiment 8a on the 500-task set: E7b (Exp 4 fixed cap 2,048 + order by the online gradient-boosted length model)
# plus the Exp 5 fixed starvation guard, T = 120 s (approved 2026-11-25). Same workload as the Phase 1 FIFO N4
# baseline. Run with /opt/pytorch's Python (scikit-learn). Skipped if meta.json exists. Launch detached:
#   setsid nohup experiments/mem_admission/run_e8a_full500.sh > results/mem_admission/run_full500_e8a.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
name=full500_e8a_cap2048_online_gbm_starve120
[ -f "results/mem_admission/$name/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_full_500_seed2.jsonl \
    --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --starve-s 120
$PY -W ignore -m experiments.mem_admission.compare_full500
