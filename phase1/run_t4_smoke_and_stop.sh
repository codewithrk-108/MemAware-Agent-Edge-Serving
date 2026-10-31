#!/usr/bin/env bash
# Run on an EBS-backed instance whose shutdown behavior is set to Stop.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1
mkdir -p results/phase1
status_file="results/phase1/t4_smoke_sweep_status.txt"

finish() {
    result=$?
    trap - EXIT
    printf 'finished_utc=%s\nsweep_exit_code=%s\nshutdown_requested=true\n' \
        "$(date -u +%FT%TZ)" "$result" > "$status_file"
    sync
    echo "T4 smoke exited with status $result; shutting down in 60 seconds."
    sleep 60
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
echo "Starting T4 smoke sweep at $(date -u +%FT%TZ)."
.venv/bin/python -u -m phase1.sweep --groups smoke_t4 "$@"
