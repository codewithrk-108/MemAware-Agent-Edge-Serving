#!/usr/bin/env bash
# Cloud-init bootstrap for the GPU box. Mirrors the repo's expected layout:
#   /opt/exp/phase-1-fastllamacpp   (harness)
#   /opt/exp/llama.cpp              (LLAMA_SERVER default: ../llama.cpp/build/bin/llama-server)
#   /opt/exp/models                 (MODEL default: ../models/Qwen3-0.6B-Q8_0.gguf)
set -euxo pipefail
exec > /var/log/mas-bootstrap.log 2>&1

BUCKET="__BUCKET__"
LLAMA_COMMIT="1242be6"        # same commit as the Mac microbenchmarks; change only on purpose
REPO="https://github.com/codewithrk-108/phase-1-fastllamacpp.git"
REPO_REF="main"               # pin to a commit SHA before the real sweep
CUDA_ARCH="89"                # L4 (g6) = 89, A10G (g5) = 86, T4 (g4dn) = 75

export DEBIAN_FRONTEND=noninteractive
apt-get update && apt-get install -y build-essential cmake git jq awscli
systemctl disable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer || true

# --- Stable GPU timings: persistence mode, clocks locked at max SM clock ---
nvidia-smi -pm 1
MAXCLK=$(nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader,nounits | head -1)
nvidia-smi -lgc "$MAXCLK","$MAXCLK" || echo "clock lock not supported; record and continue"

mkdir -p /opt/exp && cd /opt/exp

# --- llama.cpp with CUDA, pinned ---
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp && git checkout "$LLAMA_COMMIT"
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH" -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF
cmake --build build -j "$(nproc)" --target llama-server
cd /opt/exp

# --- Model ---
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
mkdir -p models
uvx --from huggingface_hub hf download Qwen/Qwen3-0.6B-GGUF Qwen3-0.6B-Q8_0.gguf --local-dir models
sha256sum models/Qwen3-0.6B-Q8_0.gguf | tee models/SHA256   # compare with the Mac copy once

# --- Harness + Linux/CUDA memory patch ---
git clone "$REPO" && cd phase-1-fastllamacpp && git checkout "$REPO_REF"
aws s3 cp "s3://$BUCKET/setup/linux-cuda-harness.patch" /tmp/ && git apply /tmp/linux-cuda-harness.patch
uv sync && uv pip install nvidia-ml-py

# --- Resume: pull any results already finished by an earlier (spot) box ---
aws s3 sync "s3://$BUCKET/results/" results/ --quiet || true

chown -R ubuntu:ubuntu /opt/exp
touch /opt/exp/BOOTSTRAP_DONE
