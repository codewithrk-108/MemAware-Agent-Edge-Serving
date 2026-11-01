#!/usr/bin/env bash
# Run in a dedicated checkout/branch. Only approve shutdown after verifying EC2 Stop behavior.
set -eu
part="${1:?usage: run_split_sweep_and_stop.sh a|b}"
case "$part" in a|b) ;; *) echo 'Part must be a or b' >&2; exit 2 ;; esac
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
: "${MODEL:?Set MODEL to the fixed GGUF file}"
: "${LLAMA_SERVER:?Set LLAMA_SERVER to the validated instrumented binary}"
group="${OUTPUT_GROUP:-t4_load_v1_part_$part}"
sweep_group="${SWEEP_GROUP:-load_t4_part_$part}"
commit_message="${RESULT_COMMIT_MESSAGE:-Record T4 load sweep part $part results}"
out="results/$group"
mkdir -p "$out"
commit_log="$root/../${group}_commit.log"

finish() {
    sweep_code=$?
    trap - EXIT
    set +e
    printf 'finished_utc=%s\nsweep_exit_code=%s\n' "$(date -u +%FT%TZ)" "$sweep_code" > "$out/status.txt"
    if [ -n "${SWEEP_LOG:-}" ] && [ -f "$SWEEP_LOG" ]; then cp "$SWEEP_LOG" "$out/sweep.log"; fi
    sync
    git add -- "$out" && git commit -m "$commit_message" > "$commit_log" 2>&1
    commit_code=$?
    if [ "$commit_code" -ne 0 ]; then
        echo "Result commit failed; instance left running. See $commit_log" >&2
        exit 1
    fi
    git rev-parse HEAD > "$root/../${group}_result_commit.txt"
    if [ "${PUSH_RESULTS:-0}" = 1 ]; then
        branch="$(git branch --show-current)"
        case "$branch" in results/*) ;; *) echo 'Refusing to push results outside a results/* branch.' >&2; exit 1 ;; esac
        push_ok=0
        for attempt in 1 2 3; do
            if git push --set-upstream origin "HEAD:refs/heads/$branch" > "$root/../${group}_push.log" 2>&1; then
                push_ok=1
                break
            fi
            if [ "$attempt" -lt 3 ]; then sleep "${PUSH_RETRY_DELAY:-15}"; fi
        done
        if [ "$push_ok" -ne 1 ]; then
            echo 'Result push failed; committed results preserved and instance left running.' >&2
            exit 1
        fi
        if [ -n "${PHASE1_GITHUB_CREDENTIAL:-}" ]; then rm -f -- "$PHASE1_GITHUB_CREDENTIAL"; fi
    fi
    sync
    if [ -f "$root/.shutdown-approved" ]; then
        echo 'Results committed; requesting instance stop through verified shutdown behavior.'
        sudo -n shutdown -h now || { echo 'Shutdown failed; stop the instance manually.' >&2; exit 1; }
    else
        echo 'Results committed. Shutdown approval marker absent; instance left running.'
    fi
    exit "$sweep_code"
}
trap finish EXIT

{
    printf 'started_utc=%s\npart=%s\n' "$(date -u +%FT%TZ)" "$part"
    hostname
    git rev-parse HEAD
    sha256sum "$MODEL" "$LLAMA_SERVER" "${TRACE_FILE:-data/traces/agenttrace_50_seed2.jsonl}"
    sha256sum runtime/llama-b11780-kv-occupancy.patch
    for library in "$(dirname "$LLAMA_SERVER")"/*.so*; do
        if [ -f "$library" ]; then sha256sum "$library"; fi
    done
    .venv/bin/python --version
    nvidia-smi --query-gpu=name,uuid,driver_version,memory.total --format=csv
    "$LLAMA_SERVER" --version
} > "$out/provenance.txt" 2>&1
.venv/bin/python -u -m phase1.sweep --groups "$sweep_group" \
    --output-group "$group" --require-kv-metrics
