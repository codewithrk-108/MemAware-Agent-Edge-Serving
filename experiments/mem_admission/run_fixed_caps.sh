#!/usr/bin/env bash
# Experiment 4, corrected: reservation = prompt (history) + cap, no use of the true output length for admission.
# Caps 1,024 / 2,048 / 4,096 / 8,192 on the 50-task workload (Poisson mean 5 s, seed 2), one after another.
# Finished runs (meta.json present) are skipped. The earlier runs (smoke_exp4_cap*, reserve min_true) are kept.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
for cap in 1024 2048 4096 8192; do
  name="smoke_exp4fc_cap${cap}"
  [ -f "results/mem_admission/$name/meta.json" ] && continue
  .venv/bin/python -u -m experiments.mem_admission.scheduler --name "$name" --cap "$cap" --reserve cap
done
.venv/bin/python -m experiments.mem_admission.compare
