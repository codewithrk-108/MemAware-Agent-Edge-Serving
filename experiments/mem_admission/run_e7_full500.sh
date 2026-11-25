#!/usr/bin/env bash
# Experiment 7 on the 500-task set: Exp 4 fixed cap 2,048 + ordering by an output-length estimator learned online
# from this run's completed requests only. E7a: online class x step median. E7b: online gradient-boosted model.
# Same workload as the Phase 1 FIFO N4 baseline (500 tasks, Poisson mean 5 s, seed 2). Run one after the other with
# /opt/pytorch's Python (has scikit-learn; the repo venv does not). Finished runs (meta.json present) are skipped.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
for kind in online_median online_gbm; do
  case $kind in online_median) tag=e7a ;; online_gbm) tag=e7b ;; esac
  name="full500_${tag}_cap2048_${kind}"
  [ -f "results/mem_admission/$name/meta.json" ] && continue
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_full_500_seed2.jsonl \
    --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order "$kind"
done
$PY -W ignore -m experiments.mem_admission.compare_full500
