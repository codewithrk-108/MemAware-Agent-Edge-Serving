#!/usr/bin/env bash
# Experiment 12b on the 500-task set (user, 2026-11-27): E12 with the server at -np 4 (--slots 4), as FIFO N4, so at
# most 4 caches exist in the KV pool; still --max-running 4. Everything else as E11b/E12. Then compare_full500 and the
# analysis against E12 (-np 32).
# Launch detached:
#   setsid nohup experiments/mem_admission/run_e12b_full500.sh > results/mem_admission/run_full500_e12b.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
name=full500_e12b_cap2048_gbm_slotreuse_ownfirst_np4
[ -f "results/mem_admission/$name/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_full_500_seed2.jsonl \
    --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --slot-reuse --slot-order own_first \
    --max-running 4 --slots 4
$PY -W ignore -m experiments.mem_admission.compare_full500
$PY -W ignore -m experiments.mem_admission.analyze_e11 "$name" full500_e12_cap2048_gbm_slotreuse_ownfirst_run4 \
  e12b_full500_analysis E12b "E12 (-np 32)" || true
