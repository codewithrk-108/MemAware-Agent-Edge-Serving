# Seven full AgentTrace runs on two T4 instances

Every run uses `data/traces/agenttrace_full_500_seed2.jsonl`: 500 distinct original
problems, 1068 planned model requests, 568 saved tool waits, and 1,591,939 planned
output tokens. Dataset SHA-256:
`9131e06eb8915b466e91a608d7f135634373d59dd9ccb84578385065ab30573b`.

| Instance | Order | Config | N | Arrivals | Unified KV cells |
|---|---:|---:|---:|---|---:|
| A | 1 | 1, conservative | 1 | Poisson mean 10s | 32768 |
| A | 2 | 2, primary candidate | 4 | Poisson mean 5s | 32768 |
| A | 3 | 3, aggressive | 16 | Poisson mean 5s | 32768 |
| B | 1 | 4, primary candidate | 4 | Poisson mean 2s | 32768 |
| B | 2 | 6, primary candidate | 4 | Burst | 32768 |
| B | 3 | 7, aggressive | 16 | Poisson mean 2s | 65536 |
| B | 4 | 5, aggressive | 16 | Poisson mean 2s | 32768 |

Each instance runs its configurations sequentially. The split targets similar
total durations (roughly six hours each); estimates are uncertain, particularly
because failures change the amount of executed work. Simultaneous completion is
not guaranteed. Config 7 uses 64K to avoid duplicating config 5.

Common controls: request FIFO, Qwen3-0.6B Q8_0, f16 KV, identical instrumented
b11780 runtime, prompt reuse enabled, host prompt cache off, no mmap, seed 2,
and saved tool delays at scale 1. Arrival times are task arrivals, not independent
arrivals for every model step. All policies must later reuse these same controls.
Task deadlines are 21600 seconds and request timeouts are 1800 seconds to avoid
inheriting short smoke/CLI defaults. They are ceilings, not expected durations.

The groups are `full500_t4_part_a` and `full500_t4_part_b`; outputs are
`results/t4_full500_v1_part_a` and `results/t4_full500_v1_part_b`. Each run records
request/task results, queue timelines, admission diagnostics, process/GPU memory,
and sampled physical KV occupancy. Successful-task latency must be interpreted
alongside failures. Physical occupancy includes idle retained contents.

Use clean checkouts on branches `results/t4-full500-v1-part-a` and
`results/t4-full500-v1-part-b`. Configure a repository-matched Git credential
helper, with credentials stored privately outside the checkout. Test a real push
before starting inference. No credential contents belong in Git, command-line
arguments, logs, model metadata, or provenance files.

Set launcher environment variables `SWEEP_GROUP`, `OUTPUT_GROUP`, `TRACE_FILE`,
`PUSH_RESULTS=1`, `PHASE1_GITHUB_CREDENTIAL`, `MODEL`, `LLAMA_SERVER`,
`RESULT_COMMIT_MESSAGE`, and `SWEEP_LOG`, then start
`phase1/run_split_sweep_and_stop.sh a` or `b` under `nohup`. The existing
`.shutdown-approved` marker is used only for instances whose EC2 shutdown
behavior was confirmed Stop.

After its sweep, each instance commits its result directory and pushes its own
results branch directly to GitHub. The launcher retries a failed push, retains
committed results and keeps the instance running if upload still fails. After a
successful push it deletes the temporary credential file and requests shutdown.
This workflow needs neither an open SSH session nor a running local Codex turn.
