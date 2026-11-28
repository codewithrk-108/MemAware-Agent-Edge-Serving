#!/usr/bin/env bash
# Experiment 13, the "beat FIFO N4" algorithm (user, 2026-11-27). Detached:
#   setsid nohup experiments/mem_admission/run_e13.sh > results/mem_admission/run_e13.log 2>&1 < /dev/null & disown
# 1. FIFO N4 50-task smoke on the rebuilt binary (reference; Phase 1 config, 50-task trace).
# 2. E13 50-task smokes for K = 0, 0.25, 0.5 s per predicted token (-np 4, at most 4 running, memory-aware admission,
#    deadline = task arrival + K * min(predicted step tokens, 2,048), own-first slot reuse).
# 3. Pick K automatically: most metrics better than the FIFO N4 smoke (completed, p50, p95, p99, mean, tasks/min);
#    ties to the lowest p95. Then E13 with that K on the 500-task set, and the 500-task comparison.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
R=results/mem_admission
E13="--cap 2048 --reserve cap --order online_gbm --slot-reuse --slot-order own_first --slots 4 --max-running 4 --rate 0.2"
if [ ! -f "$R/smoke_fifo_n4_rebuilt/tasks.csv" ]; then
$PY -W ignore -u - <<'PY'
from phase1.runner import RunConfig, execute
from harness.server import ServerConfig
execute(RunConfig(name="smoke_fifo_n4_rebuilt", trace="data/traces/agenttrace_50_seed2.jsonl", n_tasks=50,
                  arrival_rate=0.2, concurrency=4, delay_scale=1.0, cache_prompt=True, deadline_s=21600,
                  request_timeout_s=1800, scheduler="request", require_kv_metrics=True, seed=2,
                  server=ServerConfig(ctx=32768, parallel=4, kv_unified=True, cache_ram=0, mmap=False)),
        group="mem_admission")
PY
fi
for K in 0 0.25 0.5; do
  name=smoke_e13_beatfifo_k${K}
  [ -f "$R/$name/meta.json" ] || $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" \
    --trace data/traces/agenttrace_50_seed2.jsonl --n-tasks 50 $E13 --deadline-k "$K"
done
$PY -W ignore -m experiments.mem_admission.compare_e13 $R/e13_smoke_compare.md \
  "FIFO N4 (rebuilt)=$R/smoke_fifo_n4_rebuilt" "E11b smoke=$R/smoke_e11b_cap2048_gbm_slotreuse_ownfirst" \
  "E13 K=0=$R/smoke_e13_beatfifo_k0" "E13 K=0.25=$R/smoke_e13_beatfifo_k0.25" "E13 K=0.5=$R/smoke_e13_beatfifo_k0.5"
BEST=$($PY -W ignore - <<'PY'
from pathlib import Path
from experiments.mem_admission.compare_e13 import metrics, beats
R = Path("results/mem_admission"); ref = metrics(R / "smoke_fifo_n4_rebuilt")
c = {k: metrics(R / f"smoke_e13_beatfifo_k{k}") for k in ("0", "0.25", "0.5")}
print(max(c, key=lambda k: (beats(c[k], ref), -c[k]["p95 s"])))
PY
)
echo "chosen K = $BEST"
name=full500_e13_beatfifo_k${BEST}
[ -f "$R/$name/meta.json" ] || $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" \
  --trace data/traces/agenttrace_full_500_seed2.jsonl --n-tasks 500 $E13 --deadline-k "$BEST"
$PY -W ignore -m experiments.mem_admission.compare_e13 $R/e13_full500_compare.md \
  "FIFO N4=results/t4_full500_v1_part_a/t4_full500_v1_c2_n4_poisson5s_ctx32k_seed2" \
  "E11b=$R/full500_e11b_cap2048_gbm_slotreuse_ownfirst" "E12=$R/full500_e12_cap2048_gbm_slotreuse_ownfirst_run4" \
  "E13 K=$BEST=$R/$name"
