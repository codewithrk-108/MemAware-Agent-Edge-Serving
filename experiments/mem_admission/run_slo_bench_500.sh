#!/usr/bin/env bash
# SLO benchmark on the 500-task set (user, 2026-11-27): FIFO N4 vs E11b, 3 runs each (same seed 2, identical arrivals;
# the average removes run-to-run timing noise), all on the rebuilt binary, then solo latency of every task (3 reps,
# retain mode, task alone on the GPU) as the SLO base, then the analysis. Resumable: finished steps are skipped.
# Run 1 of E11b is the existing full500_e11b_cap2048_gbm_slotreuse_ownfirst (rebuilt binary, identical flags).
# Order interleaves the systems so slow drift affects both equally. Launch detached:
#   setsid nohup experiments/mem_admission/run_slo_bench_500.sh > results/mem_admission/run_slo_bench_500.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
R=results/mem_admission
TRACE=data/traces/agenttrace_full_500_seed2.jsonl
fifo() {   # Phase 1 FIFO N4 config (lab log "Commands"), 500 tasks
  [ -f "$R/full500_fifo_n4_rebuilt_r$1/tasks.csv" ] && return 0
  echo "=== FIFO N4 run $1 $(date -u)"
  $PY -W ignore -u - "$1" <<'PY'
import sys
from phase1.runner import RunConfig, execute
from harness.server import ServerConfig
execute(RunConfig(name=f"full500_fifo_n4_rebuilt_r{sys.argv[1]}", trace="data/traces/agenttrace_full_500_seed2.jsonl",
                  n_tasks=500, arrival_rate=0.2, concurrency=4, delay_scale=1.0, cache_prompt=True, deadline_s=21600,
                  request_timeout_s=1800, scheduler="request", require_kv_metrics=True, seed=2,
                  server=ServerConfig(ctx=32768, parallel=4, kv_unified=True, cache_ram=0, mmap=False)),
        group="mem_admission")
PY
}
e11b() {   # identical flags to run_e11b_full500.sh
  name=full500_e11b_cap2048_gbm_slotreuse_ownfirst_r$1
  [ -f "$R/$name/meta.json" ] && return 0
  echo "=== E11b run $1 $(date -u)"
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace $TRACE \
    --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --slot-reuse --slot-order own_first
}
fifo 1; e11b 2; fifo 2; e11b 3; fifo 3
echo "=== solo latencies $(date -u)"
$PY -W ignore -u -m experiments.solo_SLO.run_solo --name full500_v1 --trace $TRACE --reps 3 --modes retain
$PY -W ignore -m experiments.solo_SLO.analyze --name full500_v1 || true
echo "=== analysis $(date -u)"
$PY -W ignore -m experiments.mem_admission.slo_benchmark full500_v1 $R/slo_bench_500 \
  "FIFO N4=$R/full500_fifo_n4_rebuilt_r1,$R/full500_fifo_n4_rebuilt_r2,$R/full500_fifo_n4_rebuilt_r3" \
  "E11b=$R/full500_e11b_cap2048_gbm_slotreuse_ownfirst,$R/full500_e11b_cap2048_gbm_slotreuse_ownfirst_r2,$R/full500_e11b_cap2048_gbm_slotreuse_ownfirst_r3"
echo "=== done $(date -u)"
