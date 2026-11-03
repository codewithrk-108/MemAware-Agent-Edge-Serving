# AWS scripts

Scripts used to run the harness on AWS GPU instances. They take all account-specific values
(bucket, subnet, security group, instance profile) from environment variables; nothing here is
tied to one account.

| File | Purpose |
|---|---|
| `launch.sh` | Launch one GPU instance (on-demand or spot) from the AWS Deep Learning Base AMI, with SSM access and no inbound ports. `BUCKET=… SUBNET=… SG=… ./launch.sh [on-demand\|spot] [instance-type]` |
| `user-data.sh` | Cloud-init bootstrap: builds llama.cpp at the pinned commit with CUDA, downloads Qwen3-0.6B Q8_0, clones this repo, applies `linux-cuda-harness.patch`, and pulls any results already synced to S3. |
| `run_sweep.sh` | Runs `python -m phase1.sweep` on the instance, syncing `results/` to S3 every minute so a spot interruption loses at most the run in progress. `--shutdown` stops the box afterwards. |
| `linux-cuda-harness.patch` | Linux/CUDA additions to the macOS-first harness: process VRAM from NVML in the memory footprint, CUDA device lines in the startup-log parser, GPU name/driver/clock in `meta.json`. |

These scripts set up the fractional-L4 (`g6f`) instance used in writeup S1. Runs on both GPUs were
started with the auto-stopping launchers in `phase1/` (`run_t4_smoke_and_stop.sh`,
`run_agenttrace_and_stop.sh`, `run_split_sweep_and_stop.sh`); the two-instance T4 procedure is in
`docs/t4_split_sweeps.md` and `docs/full500_runs_v1.md`.

Before the first launch, request the "Running On-Demand G and VT instances" quota (and the Spot
equivalent) for at least 4 vCPUs; new accounts start at 0. Tag instances and use `--shutdown` or an
AWS Budgets alert so no box is left running.
