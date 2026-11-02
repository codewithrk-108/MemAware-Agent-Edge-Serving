#!/usr/bin/env bash
# Run the repo's own Phase 1 sweep on the GPU box, syncing results/ to S3 every minute so a spot
# interruption loses at most the run in progress (phase1.sweep skips runs that already have meta.json).
# Usage (on the box, as ubuntu):  BUCKET=... ./run_sweep.sh [--groups concurrency_syn ...] [--shutdown]
set -euo pipefail
BUCKET="${BUCKET:?set BUCKET}"
cd /opt/exp/phase-1-fastllamacpp

SHUTDOWN=0; ARGS=()
for a in "$@"; do [[ "$a" == "--shutdown" ]] && SHUTDOWN=1 || ARGS+=("$a"); done

( while sleep 60; do aws s3 sync results/ "s3://$BUCKET/results/" --quiet; done ) &
SYNC=$!

nvidia-smi --query-gpu=name,driver_version,clocks.sm,clocks.max.sm,memory.total --format=csv > results/gpu_env.csv
uv run python -m phase1.sweep "${ARGS[@]}" 2>&1 | tee -a results/sweep_aws.log

kill $SYNC || true
aws s3 sync results/ "s3://$BUCKET/results/" --quiet
[[ $SHUTDOWN == 1 ]] && sudo shutdown -h now
