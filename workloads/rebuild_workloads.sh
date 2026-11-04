#!/usr/bin/env bash
# Rebuild the Phase 1 AgentTrace workloads from the committed source file and check that the
# results are byte-identical to the committed traces in data/traces/.
#
#   data/source/agenttrace_readable.jsonl
#     -> build_agenttrace --split all --max-context 16384   (agenttrace_all.jsonl, 1,169 tasks)
#     -> derive_agenttrace scale                           (agenttrace_all_tool_delay_150x_max10s.jsonl)
#     -> derive_agenttrace sample                          (agenttrace_50_seed2.jsonl, smoke tests)
#     -> build_full_agenttrace                             (agenttrace_full_500_seed2.jsonl, full runs)
#
# Usage: ./workloads/rebuild_workloads.sh   (from any directory; needs uv and network for the tokenizer)
set -euo pipefail
cd "$(dirname "$0")/.."

TOK=data/source/Qwen3-0.6B-tokenizer.json
TOK_URL=https://huggingface.co/Qwen/Qwen3-0.6B/resolve/main/tokenizer.json
TOK_SHA=aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4

sha() { uv run python -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$1"; }

if [[ ! -f "$TOK" ]]; then
  echo "downloading tokenizer to $TOK"
  curl -fsSL -o "$TOK.part" "$TOK_URL" && mv "$TOK.part" "$TOK"
fi
[[ "$(sha "$TOK")" == "$TOK_SHA" ]] || { echo "tokenizer SHA-256 mismatch: expected $TOK_SHA" >&2; exit 1; }

OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT

uv run python -m workloads.build_agenttrace --source data/source/agenttrace_readable.jsonl \
  --system-prompt workloads/prompts/agenttrace_smolagents_1_24_0.txt --tokenizer-json "$TOK" \
  --out "$OUT/agenttrace_all.jsonl" --split all --max-context 16384 > /dev/null
uv run python -m workloads.derive_agenttrace scale \
  --source "$OUT/agenttrace_all.jsonl" --out "$OUT/agenttrace_all_tool_delay_150x_max10s.jsonl" > /dev/null
uv run python -m workloads.derive_agenttrace sample \
  --source "$OUT/agenttrace_all_tool_delay_150x_max10s.jsonl" --out "$OUT/agenttrace_50_seed2.jsonl" > /dev/null
uv run python -m workloads.build_full_agenttrace \
  --source "$OUT/agenttrace_all_tool_delay_150x_max10s.jsonl" --out "$OUT/agenttrace_full_500_seed2.jsonl" > /dev/null

status=0
for f in agenttrace_all_tool_delay_150x_max10s.jsonl agenttrace_50_seed2.jsonl agenttrace_full_500_seed2.jsonl; do
  if cmp -s "$OUT/$f" "data/traces/$f"; then echo "identical  data/traces/$f"; else echo "DIFFERENT  data/traces/$f"; status=1; fi
done
exit $status
