#!/usr/bin/env bash
# Experiment 11 smoke (user, 2026-11-26): E7b (cap 2,048, memory-aware admission, online gradient-boosted length order)
# with slot reuse (--slot-reuse): finished steps keep their cache in the slot as a leftover instead of being erased;
# the scheduler picks the slot with the longest shared prompt prefix (never a running or waiting-continuation slot);
# leftovers are erased least recently used first only when the physical pool would otherwise overflow. 50-task set.
#   setsid nohup experiments/mem_admission/run_e11_smoke.sh > results/mem_admission/run_smoke_e11.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
name=smoke_e11_cap2048_gbm_slotreuse
[ -f "results/mem_admission/$name/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_50_seed2.jsonl \
    --n-tasks 50 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --slot-reuse
# Control on the same (rebuilt) binary: plain E7b on the 50-task set, slots erased after every step.
base=smoke_e7b_cap2048_gbm_rebuilt
[ -f "results/mem_admission/$base/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$base" --trace data/traces/agenttrace_50_seed2.jsonl \
    --n-tasks 50 --rate 0.2 --cap 2048 --reserve cap --order online_gbm
