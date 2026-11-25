#!/usr/bin/env bash
# Run the plain FIFO N4 baseline and experiments 1 and 2 on the same workload, one after another.
#   smoke: 50-task trace;  full: 500-task trace (the Phase 1 R2 workload)
# Arrivals: Poisson, mean 5 s (rate 0.2/s), seed 2 -- identical task order and arrival times in all three runs.
# Finished runs (meta.json present) are skipped, so the script can be rerun after an interruption.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
size="${1:?usage: run_experiments.sh smoke|full}"
case "$size" in
  smoke) trace=data/traces/agenttrace_50_seed2.jsonl; n=50 ;;
  full)  trace=data/traces/agenttrace_full_500_seed2.jsonl; n=500 ;;
  *) echo "smoke or full" >&2; exit 2 ;;
esac
group=kv_policy
py=.venv/bin/python

done_run() { [ -f "results/$group/$1/meta.json" ]; }

name="${size}_baseline_fifo_n4"
done_run "$name" || $py -u - <<PY
from phase1.runner import RunConfig, execute
from harness.server import ServerConfig
execute(RunConfig(name="$name", trace="$trace", n_tasks=$n, arrival_rate=0.2, concurrency=4, delay_scale=1.0,
                  cache_prompt=True, deadline_s=21600, request_timeout_s=1800, scheduler="request",
                  require_kv_metrics=True, seed=2,
                  server=ServerConfig(ctx=32768, parallel=4, kv_unified=True, cache_ram=0, mmap=False)),
        group="$group")
PY

name="${size}_exp1_policy_back"
done_run "$name" || $py -u -m experiments.kv_policy.runner --name "$name" --requeue back \
  --trace "$trace" --n-tasks "$n" --rate 0.2 --group "$group"

name="${size}_exp2_policy_front"
done_run "$name" || $py -u -m experiments.kv_policy.runner --name "$name" --requeue front \
  --trace "$trace" --n-tasks "$n" --rate 0.2 --group "$group"

$py -m experiments.kv_policy.compare --size "$size"
