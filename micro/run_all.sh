#!/bin/bash
# Runs the microbenchmarks in order. Plug in the charger, close other GPU apps. Takes about 30-40 minutes.
set -euo pipefail
cd "$(dirname "$0")/.."
for m in m1_memory m2_prefill m3_concurrency m4_prefix_reuse m5_retention m6_capacity; do
    echo "=== $m $(date +%H:%M:%S)"
    uv run python -m micro.$m "$@" 2>&1 | tee "results/micro/$m.stdout"
done
uv run python -m analysis.plot_micro
