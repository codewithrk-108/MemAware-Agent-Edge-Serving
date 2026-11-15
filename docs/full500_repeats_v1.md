# Replicates of the seven full 500-task T4 runs (R1–R7)

Purpose: measure run-to-run variability of Table 3 by rerunning R1–R7 **unchanged**: same
`full500_t4_part_a/b` configurations in `phase1/sweep.py`, same trace
(`agenttrace_full_500_seed2.jsonl`), same seed 2, so task order and arrival times are identical.
The driver checks the planned task order and arrival offsets against the original
`planned_runs.json` before every run.

| | |
|---|---|
| Hardware | 2 × g4dn.xlarge (Tesla T4), same AL2023 Deep Learning Base AMI family as R1–R7 |
| Runtime | llama.cpp b11780 + `runtime/llama-b11780-kv-occupancy.patch`, CUDA arch 75, built at the same path as R1–R7; the binary's SHA-256 is compared with the R1–R7 binary in each host's provenance file |
| Model | Qwen3-0.6B Q8_0, SHA-256 checked before any run |
| Driver | `phase1/full500_repeats.py`, started by `aws/user-data-t4-repeats.sh` |
| Original data | replicate 1 = `results/t4_full500_v1_part_a` (R1–R3), `results/t4_full500_v1_part_b` (R4–R7) |

## Schedule

The two hosts alternate halves, so each configuration is measured on both instances:

| Replicate | Host a | Host b |
|---|---|---|
| 2 | part a (R1, R2, R3) | part b (R4, R6, R7, R5) |
| 3 | part b | part a |
| 4 | part a | part b |
| … | … | … |

Part a takes about 5.7 h and part b about 5.3 h. A run starts only if its expected duration
(original span × 1.25 + 10 min) ends before the deadline (Mon 16 Nov 2026, 10:00 PST); shorter
runs may still fill the remaining time. Afterwards each instance stops (EBS kept). A safety timer
stops it at 13:00 PST regardless.

## Output

```
results/t4_full500_repeats_v1/
  host_a_provenance.txt, host_b_provenance.txt   runtime/model hashes, GPU, driver, AMI
  host_a_runs.csv, host_b_runs.csv               one row per run: start, end, status, headline numbers
  host_a_driver.txt, host_b_driver.txt           driver output
  rep02_part_a/<original run name>/              same per-run files as results/README.md
  rep02_part_b/...
```

Each host pushes its own branch, `results/t4-full500-repeats-v1-host-a` and `-host-b`, after
every run. The folders don't overlap, so both branches merge cleanly. `server.log` and
`memory.csv` are not committed (per-run file rule); they stay on the instance disks and in S3.

GitHub access comes from a token stored as a private object in an S3 bucket in the course
account (`s3://<bucket>/github-token`, SSE-S3; Parameter Store and Secrets Manager are blocked by
the account's service control policy). The instance role `phase1-t4-runner` may read only that
object and write under `raw/`. The token is written to a private file used only by
`phase1/repo_credential_helper.py` and deleted when the driver exits. After every run the full run
folders, including `server.log` and `memory.csv`, are copied to `s3://<bucket>/raw/host-<a|b>/`.
Revoke the token and delete the object after the runs.
