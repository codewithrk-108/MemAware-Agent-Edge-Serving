#!/bin/bash
# Runs the microbenchmarks in order. Plug in the charger, close other GPU apps. Takes about 30-40 minutes.
# Pass options through, e.g. ./micro/run_all.sh --platform linux-cuda
set -euo pipefail
cd "$(dirname "$0")/.."
PLATFORM=macos
args=("$@")
for i in "${!args[@]}"; do
    if [[ "${args[$i]}" == "--platform" ]]; then PLATFORM="${args[$((i + 1))]}"; fi
    if [[ "${args[$i]}" == --platform=* ]]; then PLATFORM="${args[$i]#--platform=}"; fi
done
OUT="results/$PLATFORM/micro"
mkdir -p "$OUT"
for m in m1_memory m2_prefill m3_concurrency m4_prefix_reuse m5_retention m6_capacity; do
    echo "=== $m $(date +%H:%M:%S)"
    uv run python -m micro.$m "$@" 2>&1 | tee "$OUT/$m.stdout"
done
uv run python -m analysis.plot_micro --platform "$PLATFORM"
