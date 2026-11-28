#!/usr/bin/env bash
# Experiment 8 on the 500-task set: E7b (Exp 4 fixed cap 2,048 + order by the online gradient-boosted length model)
# plus the Exp 5 fixed starvation guard at T = 120 s, 240 s and 600 s, run one after another (approved 2026-11-25;
# T = 360 s was started, then stopped by the user at 655 requests and replaced by 600 s).
# Same workload as the Phase 1 FIFO N4 baseline. /opt/pytorch Python (scikit-learn). Finished runs (meta.json) are
# skipped; if the first E8 launcher (run_e8a_full500.sh, T = 120 s) is still running, waits for it first.
# Launch detached:
#   setsid nohup experiments/mem_admission/run_e8_full500.sh > results/mem_admission/run_full500_e8.log 2>&1 < /dev/null & disown
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
PY=/opt/pytorch/bin/python3
while pgrep -f "run_e8a_full500.sh|scheduler --name full500_e8" >/dev/null; do sleep 60; done
for t in 120 240 600; do
  name="full500_e8_cap2048_online_gbm_starve${t}"
  [ "$t" = 120 ] && name=full500_e8a_cap2048_online_gbm_starve120
  [ -f "results/mem_admission/$name/meta.json" ] && continue
  echo "$(date -u +%FT%TZ) start $name"
  $PY -W ignore -u -m experiments.mem_admission.scheduler --name "$name" --trace data/traces/agenttrace_full_500_seed2.jsonl \
    --n-tasks 500 --rate 0.2 --cap 2048 --reserve cap --order online_gbm --starve-s "$t"
done
$PY -W ignore -m experiments.mem_admission.compare_full500
