#!/usr/bin/env bash
# Experiment 10b on the 500-task set (user, 2026-11-26): split KV pool, alpha = 0.5. Partition P runs E7b (online
# gradient-boosted length model order, backfill). Partition F is memory-aware FCFS: ascending ready time (as FIFO N4),
# admits while history + cap (2,048) fits in F, strict (waits if the oldest does not fit), no concurrency limit
# (--fifo-conc 32 = all slots). Everything else as E10: continuations first in their own partition, a request needing
# more than a partition fails with its task. /opt/pytorch Python. Launch detached:
#   setsid nohup experiments/mem_admission/run_e10b_full500.sh > results/mem_admission/run_full500_e10b.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
name=full500_e10b_cap2048_gbm_split_a050_fcfs_mem
[ -f "results/mem_admission/$name/meta.json" ] || \
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_full_500_seed2.jsonl \
    --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --alpha 0.5 --fifo-conc 32
$PY -W ignore -m experiments.mem_admission.compare_full500
