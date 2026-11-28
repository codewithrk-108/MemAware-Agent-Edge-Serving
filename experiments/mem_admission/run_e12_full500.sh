#!/usr/bin/env bash
# Experiment 12 on the 500-task set (user, 2026-11-27): E11b (E7b + slot reuse, own-first slot order, LRU memory guard)
# with at most 4 requests running at once (--max-running 4, continuations count). Everything else as E11b's 500-task run.
# Then compare_full500 and the reuse analysis against E11b. Launch detached:
#   setsid nohup experiments/mem_admission/run_e12_full500.sh > results/mem_admission/run_full500_e12.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
name=full500_e12_cap2048_gbm_slotreuse_ownfirst_run4
[ -f "results/mem_admission/$name/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_full_500_seed2.jsonl \
    --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --slot-reuse --slot-order own_first \
    --max-running 4
$PY -W ignore -m experiments.mem_admission.compare_full500
$PY -W ignore -m experiments.mem_admission.analyze_e11 "$name" full500_e11b_cap2048_gbm_slotreuse_ownfirst \
  e12_full500_analysis E12 "E11b (no running cap)" || true
