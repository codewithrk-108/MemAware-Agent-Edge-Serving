#!/usr/bin/env bash
# Start with nohup on an EBS-backed instance whose shutdown behavior is set to Stop.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1
mkdir -p results/phase1
status_file="results/phase1/agenttrace_autostop_status.txt"

finish() {
    result=$?
    trap - EXIT
    printf 'finished_utc=%s\nsweep_exit_code=%s\nshutdown_requested=true\n' \
        "$(date -u +%FT%TZ)" "$result" > "$status_file"
    sync
    echo "Sweep exited with status $result; requesting instance shutdown."
    if ! sudo -n shutdown -h now; then
        echo "Shutdown request failed; check instance state and stop it manually."
        printf 'shutdown_failed=true\n' >> "$status_file"
        sync
        return 1
    fi
}
trap finish EXIT

export MODEL="${MODEL:-/home/ec2-user/Qwen3-0.6B-Q8_0.gguf}"
export LLAMA_SERVER="${LLAMA_SERVER:-/home/ec2-user/bin/llama-server}"
echo "Starting AgentTrace sweep at $(date -u +%FT%TZ)."
.venv/bin/python -u -m phase1.sweep \
    --groups baseline_agenttrace concurrency_agenttrace idle_agenttrace retention_agenttrace \
             scheduler_agenttrace arrival_agenttrace budget_agenttrace \
    --deduplicate "$@"
