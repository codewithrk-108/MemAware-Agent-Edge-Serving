#!/usr/bin/env bash
# Experiment 5: Exp 4 fixed cap 2,048 (reservation = history + cap) plus a starvation guard on per-request queue wait.
# E5a: T = 60 s, E5b: T = 120 s. 50-task workload (Poisson mean 5 s, seed 2), one run after the other.
# Finished runs (meta.json present) are skipped.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MODEL:?set MODEL}" "${LLAMA_SERVER:?set LLAMA_SERVER}"
for t in 60 120; do
  name="smoke_e5_cap2048_starve${t}"
  [ -f "results/mem_admission/$name/meta.json" ] && continue
  .venv/bin/python -u -m experiments.mem_admission.scheduler --name "$name" --cap 2048 --reserve cap --starve-s "$t"
done
.venv/bin/python -m experiments.mem_admission.compare
